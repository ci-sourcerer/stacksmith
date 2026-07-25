"""Examples of testing managed Stacksmith variable policies with pytest."""

import pytest
from stacksmith.validations.outcomes import InputValidationOutcome


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("us-east-1", InputValidationOutcome.PASS),
        ("eu-west-1", InputValidationOutcome.FAIL),
    ],
)
def test_aws_region_policy(
    stacksmith_test_runner,
    value: str,
    expected: InputValidationOutcome,
) -> None:
    outcome, _ = stacksmith_test_runner.run_variable_policy("aws_region", value)

    assert outcome == expected
