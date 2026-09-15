import json
import re
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

_VALIDATION_REPORT_PATH: ContextVar[Path | None] = ContextVar(
    "validation_report_path", default=None
)


def component_for_address(address: str, components: Sequence[str]) -> str | None:
    """Attribute a resource address to a known top-level component.

    Args:
        address: Full OpenTofu resource address.
        components: Known Stacksmith components.

    Returns:
        Component name, or `None` for root or unmapped resources.
    """
    match = re.match(r"^module\.([^.[\s]+)", address)
    return match[1] if match and match[1] in components else None


def write_change_report(payload: dict[str, Any], path: Path) -> None:
    """Atomically write a change report with restrictive file permissions.

    Args:
        payload: JSON-compatible report.
        path: Destination artifact path.

    Raises:
        OSError: If the report cannot be written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(payload, output, indent=2)
            output.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def current_validation_report_path() -> Path | None:
    """Return the validation artifact path for the active CI execution.

    Returns:
        Validation artifact path, or `None` when validation is emitted to stdout.
    """
    return _VALIDATION_REPORT_PATH.get()


@contextmanager
def validation_report_path_context(path: Path | None) -> Iterator[None]:
    """Expose a validation artifact path while a CI command runs.

    Args:
        path: Destination receiving the validation report.

    Yields:
        None.
    """
    token = _VALIDATION_REPORT_PATH.set(path)
    try:
        yield
    finally:
        _VALIDATION_REPORT_PATH.reset(token)
