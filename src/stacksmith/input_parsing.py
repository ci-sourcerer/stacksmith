import json
from typing import Any

from .exceptions import StacksmithConfigError


def coerce_input_value(raw: str) -> Any:
    """Parse a JSON-compatible input value, falling back to its original string.

    Args:
        raw: Raw input value.

    Returns:
        Parsed JSON value, or `raw` when it is not valid JSON.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_var_assignment(value: str) -> tuple[str, str]:
    """Parse and validate a `key=value` command-line input assignment.

    Args:
        value: Raw assignment.

    Returns:
        Stripped key and value.

    Raises:
        StacksmithConfigError: If the assignment has no separator or an empty key.
    """
    if "=" not in value:
        raise StacksmithConfigError(
            f"Invalid --var format: {value}. Expected key=value."
        )

    key, raw_value = value.split("=", 1)
    if not key.strip():
        raise StacksmithConfigError(
            f"Invalid --var format: {value}. Expected key=value."
        )
    return key.strip(), raw_value.strip()
