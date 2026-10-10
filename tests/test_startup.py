import subprocess
import sys

import pytest


@pytest.mark.parametrize("argument", ["--help", "--version", "generate --help"])
def test_cli_information_does_not_load_execution_dependencies(argument):
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"""
import sys
from stacksmith.cli.main import main

sys.argv = ["stacksmith", *{argument!r}.split()]
try:
    main()
except SystemExit as error:
    assert error.code == 0
else:
    raise AssertionError("CLI did not exit")

for module in ("stacksmith.api", "stacksmith.testing", "pydantic", "jsonschema", "hcl2"):
    assert module not in sys.modules, module
""",
        ],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )


def test_public_exports_resolve_to_their_definitions():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
import stacksmith

assert "stacksmith.api" not in sys.modules
assert "stacksmith.testing" not in sys.modules

from stacksmith import ExecutionPreview, StacksmithTestRunner, generate_stack
from stacksmith.api import generate_stack as implementation
from stacksmith.models import ExecutionPreview as preview_model
from stacksmith.testing import StacksmithTestRunner as test_runner

assert generate_stack is implementation
assert ExecutionPreview is preview_model
assert StacksmithTestRunner is test_runner
""",
        ],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
