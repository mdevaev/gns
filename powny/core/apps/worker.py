import sys
import os
import signal
import multiprocessing
import errno
import time

from contextlog import get_logger

from .. import context

from . import init
from . import Application


# =====
_stop = None


def run(args=None, config=None):
    if config is None:
        config = init(__name__, "Powny Worker", args)
    app = _Worker(config)
    global _stop
    _stop = app.stop
    return abs(app.run())


# =====
class _Worker(Application):
    """
        This application performs the jobs. Each job runs in a separate process and
        has its own connection to the backend. It automatically saves the state of
        the job. Worker only ensures that the process must be stopped upon request.
    """

    def __init__(self, config):
        Application.__init__(self, "worker", config)
        self._manager = _JobsManager(self._config.core.rules_dir, self._app_config.wait_slowpokes)

    def process(self):
        logger = get_logger()
        sleep_mode = False
        with self.get_backend_object().connected() as backend:
            while not self._stop_event.is_set():
                gen_jobs = backend.jobs_process.get_jobs()
                while not self._stop_event.is_set():
                    self._manager.manage(backend)
                    self._dump_worker_state(backend)

                    if self._manager.get_current() >= self._app_config.max_jobs:
                        logger.debug("Have reached the maximum concurrent jobs %(maxjobs)d,"
                                     " sleeping %(delay)f seconds...",
                                     {"maxjobs": self._app_config.max_jobs, "delay": self._app_config.max_jobs_sleep})
                        time.sleep(self._app_config.max_jobs_sleep)

                    else:
                        try:
                            job = next(gen_jobs)
                        except StopIteration:
                            if not sleep_mode:
                                logger.debug("No jobs in queue, sleeping for %(delay)f seconds...",
                                             {"delay": self._app_config.empty_sleep})
                            sleep_mode = True
                            time.sleep(self._app_config.empty_sleep)
                            break
                        else:
                            sleep_mode = False
                            self._manager.run_job(job, backend, self.get_backend_object())
                            # FIXME: По неизвестной пока причине, очередь медленно рассасывается без небольшого слипа.
                            # Скорее всего, имеет место гонка между воркерами.
                            time.sleep(self._app_config.job_delay)

    def _dump_worker_state(self, backend):
        self.dump_app_state(backend, {
            "active": self._manager.get_current(),
            "processed": self._manager.get_finished(),
            "not_started": self._manager.get_not_started(),
        })


class _JobsManager:
    def __init__(self, rules_dir, wait_slowpokes):
        self._rules_dir = rules_dir
        self._wait_slowpokes = wait_slowpokes
        self._procs = {}
        self._finished = 0
        self._not_started = 0

    def get_finished(self):
        return self._finished

    def get_current(self):
        return len(self._procs)

    def get_not_started(self):
        return self._not_started

    def run_job(self, job, backend, backend_for_proc):
        logger = get_logger(job_id=job.job_id, method=job.method_name)
        logger.info("Starting the job process")
        associated = multiprocessing.Event()
        proc = multiprocessing.Process(
            target=_exec_job,
            kwargs={
                "job": job,
                "rules_dir": self._rules_dir,
                "backend": backend_for_proc,
                "associated": associated,
                "job_owner_id": backend.jobs_process.get_my_id(),
            },
        )
        self._procs[job.job_id] = (job.method_name, proc, associated, time.time())
        proc.start()

    def manage(self, backend):
        for (job_id, (method_name, proc, associated, start_time)) in self._procs.copy().items():
            logger = get_logger(job_id=job_id, method=method_name)
            if not proc.is_alive():
                logger.info("Finished job process %(pid)d with retcode %(retcode)d",
                            {"pid": proc.pid, "retcode": proc.exitcode})
                self._finish(job_id)

            elif backend.jobs_process.is_deleted_job(job_id):
                self._terminate(proc)
                self._finish(job_id)

            elif time.time() - start_time > self._wait_slowpokes and not associated.is_set():
                # Проверяем процессы, которые за долгое время не успели перехватить блокировку
                self._process_slowpoke(job_id, proc, associated, backend)

    def _finish(self, job_id):
        self._procs.pop(job_id)
        self._finished += 1

    def _terminate(self, proc):
        logger = get_logger()
        logger.info("Terminating job process %(pid)d...", {"pid": proc.pid})
        try:
            proc.terminate()
            proc.join()
        except Exception:
            logger.exception("Can't terminate process %(pid)d; ignored", {"pid": proc.pid})
            return
        logger.info("Terminated job process %(pid)d with retcode %(exitcode)d",
                    {"pid": proc.pid, "exitcode": proc.exitcode})

    def _process_slowpoke(self, job_id, proc, associated, backend):
        logger = get_logger()
        logger.warning("Detected slowpoke job process %(pid)d", {"pid": proc.pid})
        try:
            # Останавливаем процесс и смотрим, перехватил ли он блокировку между первой проверкой
            # и вызовом этой функции.
            if not self._send_signal(proc, signal.SIGSTOP):
                # Процесс перехватил лок и успел завершить исполнение.
                if backend.jobs_process.is_my_job(job_id):
                    # Так же, процесс мог умереть, ничего не захватив и блокировка осталась у нас.
                    # Освобождаем ее для других воркеров.
                    backend.jobs_process.release_job(job_id)
                self._finish(job_id)

            elif associated.is_set():
                # Процесс тормозил, но блокировка перехвачена. Отпускаем с миром.
                logger.info("OK, slowpoke job process %(pid)d is alive and lock associated",
                            {"pid": proc.pid})
                if not self._send_signal(proc, signal.SIGCONT):
                    # Умер во время проверки - и пес с ним, коллектор подберет.
                    self._finish(job_id)

            else:
                # Процесс до сих пор не перехватил блокировку, или не успел об этом сообщить.
                # Так или иначе, это тормоза. Отбираем лок, если надо, и убиваем процесс, пока он в стопе.
                logger.error("Can't associate lock in job process %(pid)d",
                             {"pid": proc.pid})
                if backend.jobs_process.is_my_job(job_id):
                    # Если блокировка до сих пор у нас, то просто снимаем ее. Если нет - то процесс или не успел
                    # сообщить об этом, либо задачей уже занялся другой коллектор или воркер. Тогда забирать
                    # ее нельзя.
                    backend.jobs_process.release_job(job_id)

                killed = self._send_signal(proc, signal.SIGKILL)
                proc.join()
                if killed:
                    logger.info("Killed slowpoke job process %(pid)d with retcode %(exitcode)d",
                                {"pid": proc.pid, "exitcode": proc.exitcode})
                else:
                    logger.info("Found dead slowpoke job process %(pid)d with retcode %(exitcode)d",
                                {"pid": proc.pid, "exitcode": proc.exitcode})
                self._procs.pop(job_id)
                self._not_started += 1
        except Exception:
            logger.exception("Can't process slowpoke job process %(pid)d...", {"pid": proc.pid})

    def _send_signal(self, proc, signum):
        try:
            os.kill(proc.pid, signum)
            return True
        except OSError as err:
            if err.errno == errno.ESRCH:
                return False
            raise


def _exec_job(job, rules_dir, backend, associated, job_owner_id):
    logger = get_logger(job_id=job.job_id, method=job.method_name)
    rules_path = os.path.join(rules_dir, job.head)
    with backend.connected():
        logger.debug("Associating job with PID %(pid)d", {"pid": os.getpid()})
        backend.jobs_process.associate_job(job.job_id, job_owner_id)
        associated.set()

        sys.path.insert(0, rules_path)
        thread = context.JobThread(
            backend=backend,
            job_id=job.job_id,
            state=job.state,
            extra={"head": job.head},
        )
        thread.start()
        thread.join()
