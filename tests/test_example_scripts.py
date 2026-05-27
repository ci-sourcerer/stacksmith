import json
from pathlib import Path

from stacksmith.models import TransformSpec
from stacksmith.validation import apply_transform


def test_transform_s3_write_policy_uses_actual_bucket_arn_from_inputs() -> None:
    config_root = Path(__file__).resolve().parents[1]
    base_path = config_root / "examples" / "shared-config-repo"
    spec = TransformSpec(
        script=str(
            base_path / "scripts" / "transforms" / "transform_s3_write_policy.py"
        )
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
