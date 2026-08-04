import json
import os
import subprocess
import sys

spec = json.loads(os.environ["STACKSMITH_OPERATION_SPEC"])
environment = os.environ.copy()
environment.update(spec["environment"])
result = subprocess.run(
    spec["command"],
    cwd=spec["working_directory"],
    env=environment,
    shell=False,
    check=False,
)
if result.returncode:
    sys.exit(result.returncode)
