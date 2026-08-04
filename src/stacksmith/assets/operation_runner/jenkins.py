import base64
import json
import os
import time
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


def _request_json(url, authorization):
    with urlopen(
        Request(
            f"{url.rstrip('/')}/api/json",
            headers={"Authorization": authorization},
        ),
        timeout=30,
    ) as response:
        return json.load(response)


def _wait_for_executable(queue_url, authorization, deadline, poll_interval):
    while time.monotonic() < deadline:
        queue_item = _request_json(queue_url, authorization)
        if queue_item.get("cancelled"):
            raise RuntimeError("Jenkins cancelled the queued operation")
        if executable := queue_item.get("executable"):
            return urljoin(queue_url, executable["url"])
        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for the Jenkins operation to start")


def _wait_for_result(build_url, authorization, deadline, poll_interval):
    while time.monotonic() < deadline:
        build = _request_json(build_url, authorization)
        if not build.get("building", False) and build.get("result") is not None:
            if build["result"] != "SUCCESS":
                raise RuntimeError(
                    f"Jenkins operation finished with result {build['result']}"
                )
            return
        time.sleep(poll_interval)
    raise TimeoutError("Timed out waiting for the Jenkins operation to finish")


def _run():
    spec = json.loads(os.environ["STACKSMITH_OPERATION_SPEC"])
    authorization = (
        "Basic "
        + base64.b64encode(
            f"{os.environ[spec['username_env']]}:{os.environ[spec['api_token_env']]}".encode()
        ).decode()
    )
    jobs = "/".join(
        f"job/{quote(part, safe='')}" for part in spec["job_name"].split("/") if part
    )
    request = Request(
        f"{spec['url'].rstrip('/')}/{jobs}/buildWithParameters",
        data="&".join(
            f"{quote(key)}={quote(value)}" for key, value in spec["parameters"].items()
        ).encode(),
        headers={"Authorization": authorization},
    )
    with urlopen(request, timeout=30) as response:
        if response.status not in {200, 201, 202}:
            raise RuntimeError(f"Jenkins returned HTTP {response.status}")
        if not (queue_url := response.headers.get("Location")):
            raise RuntimeError("Jenkins did not return a queue item URL")

    deadline = time.monotonic() + spec["timeout_seconds"]
    _wait_for_result(
        _wait_for_executable(
            queue_url,
            authorization,
            deadline,
            spec["poll_interval_seconds"],
        ),
        authorization,
        deadline,
        spec["poll_interval_seconds"],
    )


_run()
