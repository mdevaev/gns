#!/usr/bin/env python


from raava import application
from raava import worker

from gns import service
from gns import zclient
from gns import core


##### Public methods #####
def main():
    config = service.init(description="GNS Worker")[0]
    core_opts = config[service.S_CORE]
    app_opts = config[service.S_WORKER]

    core.init_rules_environment(config)

    app = application.Application(
        thread_class  = worker.WorkerThread,
        zoo_connect   = lambda: zclient.connect(config),
        workers       = app_opts[service.O_WORKERS],
        die_after     = app_opts[service.O_DIE_AFTER],
        quit_wait     = app_opts[service.O_QUIT_WAIT],
        interval      = app_opts[service.O_RECHECK],
        queue_timeout = app_opts[service.O_QUEUE_TIMEOUT],
        rules_path    = core_opts[service.O_RULES_DIR],
    )
    app.run()


##### Main #####
if __name__ == "__main__":
    main()

