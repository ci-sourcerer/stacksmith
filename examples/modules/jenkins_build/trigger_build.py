import base64
import json
import os
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def build_url(jenkins_url: str, job_name: str) -> str:
    """Return the parameterized-build endpoint for a Jenkins job.

    Args:
        jenkins_url: Base URL of the Jenkins instance.
        job_name: Job name, with folders separated by `/`.

    Returns:
        The URL for Jenkins's `buildWithParameters` endpoint.
    """
    job_path = "/job/".join(
        quote(segment, safe="") for segment in job_name.strip("/").split("/")
    )
    return f"{jenkins_url.rstrip('/')}/job/{job_path}/buildWithParameters"


def headers(username: str, api_token: str) -> dict[str, str]:
    """Build HTTP headers for an authenticated Jenkins request.

    Args:
        username: Jenkins username.
        api_token: Jenkins API token or password.
    Returns:
        Headers for the Jenkins build request.
    """
    request_headers = {
        "Authorization": f"Basic {base64.b64encode(f'{username}:{api_token}'.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return request_headers


def main() -> None:
    """Submit a parameterized build request to Jenkins from Terraform inputs.

    Raises:
        json.JSONDecodeError: If `JENKINS_PARAMETERS_JSON` is not valid JSON.
        urllib.error.HTTPError: If Jenkins rejects the build request.
        urllib.error.URLError: If the Jenkins instance cannot be reached.
    """
    parameters = json.loads(os.environ["JENKINS_PARAMETERS_JSON"])
    if not isinstance(parameters, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in parameters.items()
    ):
        raise ValueError(
            "JENKINS_PARAMETERS_JSON must be a JSON object of string values"
        )

    request = Request(
        build_url(os.environ["JENKINS_URL"], os.environ["JENKINS_JOB_NAME"]),
        data=urlencode(parameters).encode(),
        headers=headers(
            os.environ["JENKINS_USERNAME"],
            os.environ["JENKINS_API_TOKEN"],
        ),
        method="POST",
    )
    with urlopen(request) as response:
        if response.status not in {200, 201, 302}:
            raise RuntimeError(f"Jenkins returned unexpected status {response.status}")


if __name__ == "__main__":
    main()
