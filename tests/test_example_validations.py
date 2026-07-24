import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

VALIDATIONS_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "shared-config-repo"
    / "scripts"
    / "validations"
)


def _load_validation(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VALIDATIONS_PATH / name)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("script_name", "expected_message"),
    [
        (
            "validate_ec2_requires_imdsv2.py",
            "no aws_instance resources found",
        ),
        (
            "validate_ec2_t3_micro_warning.py",
            "no aws_instance resources found",
        ),
        (
            "validate_s3_no_public_access.py",
            "no S3 public-access resources found",
        ),
        (
            "validate_s3_write_policy_scope.py",
            "no aws_s3_bucket_policy resources found",
        ),
    ],
)
def test_plan_validation_logs_when_resource_type_is_absent(
    caplog: pytest.LogCaptureFixture,
    script_name: str,
    expected_message: str,
) -> None:
    validation = _load_validation(script_name)

    with caplog.at_level("INFO", logger="validations"):
        result = validation.validate({"resource_changes": []})

    assert result == "pass"
    assert expected_message in caplog.text
