import base64
import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen

spec = json.loads(os.environ["STACKSMITH_OPERATION_SPEC"])
username = os.environ[spec["username_env"]]
token = os.environ[spec["api_token_env"]]
jobs = "/".join(
    f"job/{quote(part, safe='')}" for part in spec["job_name"].split("/") if part
)
body = "&".join(
    f"{quote(key)}={quote(value)}" for key, value in spec["parameters"].items()
).encode()
auth = base64.b64encode(f"{username}:{token}".encode()).decode()
request = Request(
    f"{spec['url'].rstrip('/')}/{jobs}/buildWithParameters",
    data=body,
    headers={"Authorization": f"Basic {auth}"},
)
with urlopen(request, timeout=30) as response:
    if response.status not in {200, 201, 202}:
        raise RuntimeError(f"Jenkins returned HTTP {response.status}")
