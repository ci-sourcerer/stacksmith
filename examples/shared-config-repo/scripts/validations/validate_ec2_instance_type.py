"""Allow only approved EC2 family and size combinations for this example."""

import logging
import re
from typing import Any

LOGGER = logging.getLogger("validations")

_ALLOWED_INSTANCE_TYPE = re.compile(
    r"^(?:t3|t3a|t4g|m6i|m7i|m7g|c6i|c7i|c7g)\.(?:nano|micro|small|medium|large|xlarge|2xlarge)$"
)


def validate(value: Any, **context: Any) -> str:
    is_valid = isinstance(value, str) and bool(_ALLOWED_INSTANCE_TYPE.fullmatch(value))
    LOGGER.debug("Validating instance_type=%r; is_valid=%s", value, is_valid)
    if not is_valid:
        LOGGER.info("Rejected non-approved EC2 instance type %r", value)
        return "fail"
    return "pass"
