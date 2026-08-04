import json
from typing import Any

from .exceptions import StacksmithConfigError


def parse_operation_names(value: str) -> list[str]:
    """Parse comma-delimited stack-local operation names.

    Args:
        value: Comma-delimited operation names.

    Returns:
        Stripped operation names in input order.

    Raises:
        StacksmithConfigError: If a name is empty or appears more than once.
    """
    operation_names = [operation_name.strip() for operation_name in value.split(",")]
    if any(not operation_name for operation_name in operation_names):
        raise StacksmithConfigError(
            "Operation names must be a comma-delimited list of non-empty names"
        )
    if len(set(operation_names)) != len(operation_names):
        raise StacksmithConfigError("Operation names must be unique")
    return operation_names


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
