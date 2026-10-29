"""Allow only AWS regions from the example's approved allowlist."""

import logging
from typing import Any

LOGGER = logging.getLogger("validations")

APPROVED_REGIONS = {
    "us-east-1",
    "us-west-2",
}


def validate(value: Any, **context: Any) -> str:
    is_valid = isinstance(value, str) and value in APPROVED_REGIONS
    LOGGER.debug("Validating aws_region=%r; is_valid=%s", value, is_valid)
    if not is_valid:
        LOGGER.info("Rejected non-approved AWS region %r", value)
        return "fail"
    return "pass"
