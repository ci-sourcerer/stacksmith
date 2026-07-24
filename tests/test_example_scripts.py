import json
from pathlib import Path

from stacksmith.loader import load_config
from stacksmith.models import TransformSpec
from stacksmith.module_mapping import resolve_module_mapping
from stacksmith.validation import apply_transform


def test_shared_config_demonstrates_convention_based_module_mapping() -> None:
    config = load_config(
        Path(__file__).resolve().parents[1]
        / "examples"
        / "shared-config-repo"
        / "stacksmith-config.yaml"
    )

    mapping = resolve_module_mapping(config, "custom-component", "example")

    assert mapping.source.data.repo == "https://github.com/my-org/custom_component"
    assert mapping.auto_inject is True


def test_transform_s3_write_policy_uses_actual_bucket_arn_from_inputs() -> None:
    config_root = Path(__file__).resolve().parents[1]
    base_path = config_root / "examples" / "shared-config-repo"
    spec = TransformSpec(
        script={
            "source": "local",
            "data": {
                "path": str(
                    base_path
                    / "scripts"
                    / "transforms"
                    / "transform_s3_write_policy.py"
                )
            },
        }
    )

    context = {
        "inputs": {
            "bucket_name": "My_Bucket",
            "environment": "prod",
        }
    }
    result = apply_transform(
        spec, "${module.app.iam_role_arn}", base_path=base_path, context=context
    )

    policy = json.loads(result)

    assert policy["Statement"][0]["Resource"] == "arn:aws:s3:::prod-my-bucket/*"
    assert policy["Statement"][0]["Principal"]["AWS"] == "${module.app.iam_role_arn}"
