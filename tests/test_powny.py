import pprint
import time

from powny.core import __version__
from powny.core.golem import convert_status

from .fixtures.application import powny_api
from .fixtures.application import as_dict
from .fixtures.application import from_dict


# ====
def test_api_v1_rules():
    with powny_api() as (test_client, _):
        with test_client() as api:
            result = as_dict(api.get("/v1/rules"))
            assert result[0] == 200
            assert result[1] == {
                "status":  "ok",
                "message": "Current HEAD",
                "result": {"head": None, "errors": None, "exposed": None},
            }

            # ---
            result = as_dict(api.post("/v1/rules", **from_dict({"head": "foobar"})))
            assert result[0] == 400
            assert result[1] == {
                "status":  "error",
                "message": "The argument \"foobar\" is not a valid hex string",
                "result":  {"head": "foobar"},
            }
            result = as_dict(api.get("/v1/rules"))
            assert result[0] == 200
            assert result[1] == {
                "status":  "ok",
                "message": "Current HEAD",
                "result": {"head": None, "errors": None, "exposed": None},
            }

            # ---
            result = as_dict(api.post("/v1/rules", **from_dict({"head": "0"})))
            assert result[0] == 200
            assert result[1] == {
                "status":  "ok",
                "message": "The HEAD has been updated",
                "result": {"head": "0"},
            }
            result = as_dict(api.get("/v1/rules"))
            assert result[0] == 503
            assert result[1] == {
                "status":  "error",
                "message": "AssertionError: Can't find module path: rules/0",
                "result":  {"head": "0", "errors": None, "exposed": None},
            }

            # ---
            result = as_dict(api.post("/v1/rules", **from_dict({"head": "0123456789abcdef"})))
            assert result[0] == 200
            assert result[1] == {
                "status":  "ok",
                "message": "The HEAD has been updated",
                "result": {"head": "0123456789abcdef"},
            }

            result = as_dict(api.get("/v1/rules"))
            assert result[0] == 200
            assert result[1]["status"] == "ok"
            assert result[1]["message"] == "Current HEAD"
            assert result[1]["result"]["head"] == "0123456789abcdef"
            assert isinstance(result[1]["result"]["errors"], dict)
            assert isinstance(result[1]["result"]["exposed"]["methods"], list)
            assert isinstance(result[1]["result"]["exposed"]["handlers"], list)


def test_api_v1_system_state():
    with powny_api(with_worker=True) as (test_client, config):
        with test_client() as api:
            result = as_dict(api.get("/v1/system/state"))
            assert result[0] == 200
            assert result[1]["status"] == "ok"
            assert result[1]["message"] == "The system statistics"
            assert result[1]["result"]["jobs"]["input"] == 0
            assert result[1]["result"]["jobs"]["all"] == 0

            _init_head(api)

            assert as_dict(api.post("/v1/jobs?method=rules.test.empty_method", **from_dict({})))[0]

            time.sleep(config.worker.empty_sleep + 3)

            result = as_dict(api.get("/v1/system/state"))
            assert result[0] == 200
            assert result[1]["result"]["jobs"]["input"] == 0
            assert result[1]["result"]["jobs"]["all"] == 1


def test_api_v1_system_info():
    with powny_api() as (test_client, config):
        with test_client() as api:
            result = as_dict(api.get("/v1/system/info"))
            assert result[0] == 200
            assert result[1]["status"] == "ok"
            assert result[1]["message"] == "The system information"
            assert result[1]["result"]["version"] == __version__
            assert result[1]["result"]["backend"]["name"] == config.core.backend


def test_api_v1_system_config():
    with powny_api() as (test_client, config):
        with test_client() as api:
            result = as_dict(api.get("/v1/system/config"))
            assert result[0] == 200
            assert result[1] == {
                "status":  "ok",
                "message": "The system configuration",
                "result":  config,
            }


def _test_api_v1_jobs_delete(url, kwargs):
    with powny_api() as (test_client, config):
        with test_client() as api:
            _init_head(api)

            result = as_dict(api.post(url, **from_dict(kwargs)))
            assert result[0] == 200
            (job_id, job_info) = tuple(result[1]["result"].items())[0]
            method_name = job_info["method"]

            result = as_dict(api.get("/v1/jobs/" + job_id))
            assert result[0] == 200
            assert result[1]["result"]["method"] == method_name
            assert result[1]["result"]["deleted"] is None

            result = as_dict(api.delete("/v1/jobs/" + job_id))
            time.sleep(config.collector.empty_sleep)

            result = as_dict(api.get("/v1/jobs/" + job_id))
            assert result[0] == 404


def test_api_v1_jobs_method_delete():
    _test_api_v1_jobs_delete("/v1/jobs?method=rules.test.empty_method", {})


def test_api_v1_jobs_method_execution(httpserver):
    httpserver.serve_content(content="test", code=200, headers=None)
    with powny_api(with_worker=True) as (test_client, config):
        with test_client() as api:
            _init_head(api)
            result = as_dict(api.post("/v1/jobs?method=rules.test.do_urlopen", **from_dict({
                "test": "urlopen_by_event",
                "url":  httpserver.url,
            })))
            assert result[0] == 200
            job_id = tuple(result[1]["result"])[0]
            _check_result(job_id, api, config.worker.empty_sleep + 300)


def test_compat_golem_execution(httpserver):
    httpserver.serve_content(content="test", code=200, headers=None)
    with powny_api(with_worker=True) as (test_client, config):
        with test_client() as api:
            _init_head(api)
            previous_status = None
            for status in ("ok", "warning", "critical", "ok"):
                result = as_dict(api.post("/api/compat/golem/submit", **from_dict({
                    "object": "foo",
                    "eventtype": "urlopen_by_event",
                    "status": status,
                    "info": httpserver.url,
                })))
                assert result[0] == 200
                for (job_id, job_info) in result[1]["result"].items():
                    if job_info["method"] == "rules.test.urlopen_by_event":
                        break
                else:
                    job_id = None
                assert job_id is not None
                (previous, current) = _check_result(job_id, api, config.worker.empty_sleep + 300)
                assert (previous_status and convert_status(previous_status)) == (previous and previous["status"])
                assert convert_status(status) == current["status"]
                previous_status = status


def _init_head(api):
    assert as_dict(api.post("/v1/rules", **from_dict({"head": "0123456789abcdef"})))[0] == 200


def _check_result(job_id, api, wait):
    for _ in range(wait):
        result = as_dict(api.get("/v1/jobs/" + job_id))
        assert result[0] == 200
        if result[1]["result"]["finished"] is not None:
            break
        time.sleep(1)
    assert result[0] == 200
    assert result[1]["result"]["exc"] is None, pprint.pformat(result)
    return result[1]["result"]["retval"]
