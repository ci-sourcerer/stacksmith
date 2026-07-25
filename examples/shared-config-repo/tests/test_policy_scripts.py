"""Examples of testing managed Stacksmith policies with pytest."""

import pytest
from stacksmith.validations.outcomes import PlanValidationOutcome


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (
            {
                "resource_changes": [
                    {
                        "address": "aws_instance.web",
                        "type": "aws_instance",
                        "change": {
                            "after": {"metadata_options": {"http_tokens": "required"}}
                        },
                    }
                ]
            },
            PlanValidationOutcome.PASS,
        ),
        (
            {
                "resource_changes": [
                    {
                        "address": "aws_instance.web",
                        "type": "aws_instance",
                        "change": {
                            "after": {"metadata_options": {"http_tokens": "optional"}}
                        },
                    }
                ]
            },
            PlanValidationOutcome.FAIL,
        ),
    ],
)
def test_ec2_requires_imdsv2(
    stacksmith_test_runner,
    plan: dict[str, object],
    expected: PlanValidationOutcome,
) -> None:
    outcome, _ = stacksmith_test_runner.run_plan_policy("ec2_requires_imdsv2", plan)

    assert outcome == expected


def test_ec2_t3_micro_policy_warns_with_stack_context(stacksmith_test_runner) -> None:
    outcome, message = stacksmith_test_runner.run_plan_policy(
        "ec2_t3_micro_warning",
        {
            "resource_changes": [
                {
                    "address": "aws_instance.web",
                    "type": "aws_instance",
                    "change": {"after": {"instance_type": "t3.micro"}},
                }
            ]
        },
        context={"stack_name": "production"},
    )

    assert outcome == PlanValidationOutcome.WARN
    assert "production" in message


def test_bucket_name_property_runs_its_configured_transform(
    stacksmith_test_runner,
) -> None:
    result = stacksmith_test_runner.run_component_property(
        "aws_s3_bucket",
        "bucket_name",
        "My_Bucket",
        inputs={"environment": "prod"},
    )

    assert result.output_name == "bucket"
    assert result.value == "prod-my-bucket"
