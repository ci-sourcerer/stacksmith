"""Backend policy evaluation for managed Stacksmith configuration."""

import os
import textwrap
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .exceptions import StacksmithConfigError, StacksmithNotFoundError
from .models import BackendConfig, BackendSpec, StackDefinition, ToolConfig
from .remote import resolve_reference_path


def _load_backend_code(
    config: ToolConfig,
    cache_dir: Path | None,
) -> tuple[str, str]:
    backend_spec = config.backend
    if backend_spec.inline is not None:
        return backend_spec.inline, "<inline-backend>"
    if backend_spec.script is None:
        raise StacksmithConfigError("Backend data does not require code loading")

    script_path = resolve_reference_path(
        backend_spec.script,
        base_path=config.source_path.parent if config.source_path else Path.cwd(),
        cache_dir=cache_dir,
        auth_config=config.remote_auth or None,
        missing_cache_error_factory=lambda reference: StacksmithConfigError(
            f"Cannot fetch remote backend script without a cache directory: {reference}"
        ),
        not_found_error_factory=lambda path: StacksmithNotFoundError(
            f"Backend script not found: {path}"
        ),
    )
    return script_path.read_text(encoding="utf-8"), str(script_path)


def _backend_context(
    config: ToolConfig,
    stack: StackDefinition,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "inputs": MappingProxyType(dict(inputs)),
        "stack": MappingProxyType(
            {
                "name": stack.name,
                "tags": tuple(sorted(stack.tags)),
                "source_path": str(stack.source_path) if stack.source_path else None,
            }
        ),
        "config": MappingProxyType(
            {"source_path": str(config.source_path) if config.source_path else None}
        ),
        "environment": MappingProxyType(dict(os.environ)),
    }


def _evaluate_backend_code(
    code: str,
    origin: str,
    context: dict[str, Any],
) -> BackendConfig:
    namespace: dict[str, Any] = {}
    try:
        exec(compile(textwrap.dedent(code), origin, "exec"), namespace)  # noqa: S102
    except (SyntaxError, TypeError, ValueError) as exc:
        raise StacksmithConfigError(
            f"Could not load backend resolver '{origin}': {exc}"
        ) from exc

    config_function = namespace.get("config")
    if not callable(config_function):
        raise StacksmithConfigError(
            f"Backend resolver '{origin}' must define a callable 'config(**context)'"
        )
    try:
        result = config_function(**context)
    except Exception as exc:
        raise StacksmithConfigError(
            f"Backend resolver '{origin}' failed: {exc}"
        ) from exc
    try:
        return BackendConfig.model_validate(result)
    except (TypeError, ValueError) as exc:
        raise StacksmithConfigError(
            f"Backend resolver '{origin}' returned an invalid backend: {exc}"
        ) from exc


def resolve_backend(
    config: ToolConfig,
    stack: StackDefinition,
    inputs: dict[str, Any],
    cache_dir: Path | None = None,
) -> BackendConfig:
    """Resolve the backend selected for one stack.

    Args:
        config: Platform-managed Stacksmith configuration.
        stack: Stack receiving the resolved backend.
        inputs: Fully resolved stack input values.
        cache_dir: Cache directory for remote resolver scripts.

    Returns:
        Validated backend configuration.

    Raises:
        StacksmithConfigError: If executable resolver code cannot be loaded,
            executed, or validated.
    """
    if config.backend.data is not None:
        return config.backend.data
    code, origin = _load_backend_code(config, cache_dir)
    return _evaluate_backend_code(code, origin, _backend_context(config, stack, inputs))


def with_resolved_backend(
    config: ToolConfig,
    backend: BackendConfig,
) -> ToolConfig:
    """Return a configuration copy with an already-resolved backend policy."""
    return config.model_copy(update={"backend": BackendSpec(data=backend)})
