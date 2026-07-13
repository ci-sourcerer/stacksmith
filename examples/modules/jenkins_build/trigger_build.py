import base64
import json
import os
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def build_url(jenkins_url: str, job_name: str, has_parameters: bool = True) -> str:
    """Return the build endpoint for a Jenkins job.

    Args:
        jenkins_url: Base URL of the Jenkins instance.
        job_name: Job name, with folders separated by `/`.
        has_parameters: Whether the Jenkins job is parameterized.

    Returns:
        The URL for Jenkins's `build` or `buildWithParameters` endpoint.
    """
    job_path = "/job/".join(
        quote(segment, safe="") for segment in job_name.strip("/").split("/")
    )
    endpoint = "buildWithParameters" if has_parameters else "build"
    return f"{jenkins_url.rstrip('/')}/job/{job_path}/{endpoint}"


def _parse_optional_bool(raw_value: str | None) -> bool | None:
    if raw_value is None or raw_value == "":
        return None
    if raw_value.lower() in {"1", "true", "yes"}:
        return True
    if raw_value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError(
        "JENKINS_JOB_HAS_PARAMETERS must be true/false, 1/0, yes/no, or empty"
    )


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
    """Submit a build request to Jenkins from Terraform inputs.

    Raises:
        ValueError: If job parameter mode and provided parameters are inconsistent.
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

    has_parameters = _parse_optional_bool(os.environ.get("JENKINS_JOB_HAS_PARAMETERS"))
    if has_parameters is None and not parameters:
        raise ValueError(
            "No parameters were provided. Set JENKINS_JOB_HAS_PARAMETERS=false for "
            "non-parameterized jobs, or provide at least one parameter."
        )
    if has_parameters is False and parameters:
        raise ValueError(
            "JENKINS_PARAMETERS_JSON must be empty when "
            "JENKINS_JOB_HAS_PARAMETERS is false"
        )

    use_parameterized_endpoint = has_parameters is not False

    request = Request(
        build_url(
            os.environ["JENKINS_URL"],
            os.environ["JENKINS_JOB_NAME"],
            has_parameters=use_parameterized_endpoint,
        ),
        data=urlencode(parameters).encode() if use_parameterized_endpoint else None,
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
