from contextvars import ContextVar
from copy import deepcopy
from typing import Any


class ResolutionTrace:
    """Collect ordered resolution events for an inspection session."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.source = ""
        self.scope = ""

    def record(self, path: str, action: str, value: Any, **details: Any) -> None:
        """Append a snapshot of a resolution decision.

        Args:
            path: Dotted configuration address.
            action: Decision made by the resolver.
            value: Result of the decision.
            **details: Additional source and decision metadata.

        Returns:
            None.
        """
        self.events.append(
            deepcopy(
                dict(
                    path=path,
                    action=action,
                    source=details.pop("source", self.source),
                    value=value,
                    **details,
                )
            )
        )

    def layer(self, scope: str, source: str, value: Any) -> None:
        """Record an incoming document or input layer.

        Args:
            scope: Configuration namespace.
            source: Source file or override label.
            value: Incoming document.

        Returns:
            None.
        """
        self.scope = scope
        self.source = source
        self._walk(scope, value)

    def input_source(self, source: Any) -> str:
        """Identify a file reference or its contributing runfile.

        Args:
            source: Input reference being resolved.

        Returns:
            Source label without inline values.
        """
        if not hasattr(source, "data"):
            return str(source)
        from .models import render_file_reference

        label = (
            "inline values"
            if source.source == "inline"
            else render_file_reference(source)
        )
        for event in reversed(self.events):
            if (
                event["path"].startswith("runfile.vars.")
                and isinstance(event["value"], dict)
                and event["value"].get("data")
                == (
                    source.data.model_dump(mode="json")
                    if hasattr(source.data, "model_dump")
                    else source.data
                )
            ):
                return f"{label} via {event['source']} ({event['path']})"
        return label

    def _walk(self, path: str, value: Any) -> None:
        self.record(path, "source", value)
        if isinstance(value, dict):
            for key, item in value.items():
                self._walk(f"{path}.{key}", item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self._walk(f"{path}.{index}", item)


ACTIVE_TRACE: ContextVar[ResolutionTrace | None] = ContextVar(
    "resolution_trace", default=None
)


def redact_values(value: Any, show_values: bool = False) -> Any:
    """Redact leaf values unless explicitly requested by the caller.

    Args:
        value: Inspection data to sanitize.
        show_values: Whether leaf values may be displayed.

    Returns:
        A sanitized copy of the value.
    """
    if show_values:
        return deepcopy(value)
    if isinstance(value, dict):
        return {key: redact_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_values(item) for item in value]
    return "[REDACTED]"
