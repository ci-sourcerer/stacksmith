import json
import os
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml
from deepmerge import Merger
from jinja2.sandbox import SandboxedEnvironment

from .enums import MergeMode
from .exceptions import StacksmithConfigError, StacksmithValidationError
from .models import (
    FileReference,
    RemoteAuthConfig,
    ValidationSpec,
)
from .remote import resolve_if_remote
from .utils import stacksmith_env_list
from .validation import InputValidationOutcome, validate_value

_JINJA_ENV = SandboxedEnvironment()

_ENV_PREFIX = "STACKSMITH_VAR_"
InputLayer: TypeAlias = tuple[Literal["vars", "var"], str | FileReference]
_VAR_MERGER = Merger(
    [(dict, ["merge"]), (list, ["append"]), (set, ["union"])],
    ["override"],
    ["override"],
)


def _coerce_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _merge_var_values(existing: Any, incoming: Any, merge_mode: str | MergeMode) -> Any:
    if MergeMode(merge_mode) == MergeMode.OVERRIDE:
        return deepcopy(incoming)

    match (existing, incoming):
        case (dict(), dict()) | (list(), list()):
            return _VAR_MERGER.merge(deepcopy(existing), incoming)
        case _:
            return deepcopy(incoming)


def _merge_resolved_value(
    resolved: dict[str, Any],
    name: str,
    incoming: Any,
    merge_mode: str | MergeMode,
) -> None:
    if name in resolved:
        resolved[name] = _merge_var_values(
            resolved[name],
            incoming,
            merge_mode=merge_mode,
        )
    else:
        resolved[name] = deepcopy(incoming)


def _load_vars_file(
    path_or_url: str | Path | FileReference,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> dict[str, Any]:
    path = resolve_if_remote(
        path_or_url,
        cache_dir,
        auth_config,
        missing_cache_error_factory=lambda reference: StacksmithConfigError(
            "Cannot fetch remote vars file without a cache directory: " f"{reference}"
        ),
    )
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    match suffix:
        case ".yaml" | ".yml":
            return yaml.safe_load(text) or {}
        case ".json":
            return json.loads(text)
        case _:
            raise StacksmithConfigError(f"Unsupported vars file extension: {suffix}")


def _iter_vars_files(
    vars_file: str | Path | FileReference | Sequence[str | Path | FileReference] | None,
) -> list[str | Path | FileReference]:
    match vars_file:
        case None:
            return stacksmith_env_list("VARS") or []
        case str() | Path():
            return [vars_file]
        case _ if hasattr(vars_file, "source"):
            return [vars_file]
        case _:
            return list(vars_file)


def _parse_var_item(raw_item: str) -> tuple[str, str]:
    if "=" not in raw_item:
        raise StacksmithConfigError(
            f"Invalid --var format: {raw_item}. Expected key=value."
        )

    key, raw_value = raw_item.split("=", 1)
    key = key.strip()
    if not key:
        raise StacksmithConfigError(
            f"Invalid --var format: {raw_item}. Expected key=value."
        )
    return key, raw_value.strip()


def _apply_vars_source(
    resolved: dict[str, Any],
    vars_path: str | Path | FileReference,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> None:
    for name, value in _load_vars_file(
        vars_path,
        cache_dir=cache_dir,
        auth_config=auth_config,
    ).items():
        _merge_resolved_value(resolved, name, value, merge_mode=merge_mode)


def _apply_cli_var_item(
    resolved: dict[str, Any],
    raw_item: str,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> None:
    name, raw_value = _parse_var_item(raw_item)
    _merge_resolved_value(
        resolved,
        name,
        _coerce_value(raw_value),
        merge_mode=merge_mode,
    )


def _render_input_values(value: Any, context: dict[str, Any]) -> Any:
    match value:
        case str() if "{{" in value:
            return _JINJA_ENV.from_string(value).render(context)
        case dict():
            for key, nested in value.items():
                value[key] = _render_input_values(nested, context)
            return value
        case list():
            for index, item in enumerate(value):
                value[index] = _render_input_values(item, context)
            return value
        case _:
            return value


def resolve_inputs(
    vars_file: (
        str | Path | FileReference | Sequence[str | Path | FileReference] | None
    ) = None,
    input_layers: Sequence[InputLayer] | None = None,
    config_validations: dict[str, ValidationSpec] | None = None,
    config_validation_base_path: Path | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    merge_mode: str | MergeMode = MergeMode.DEEP,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve input values from all sources and validate them.

    Resolution order (lowest to highest priority):
        1. Vars file(s) passed in `vars_file`, or `STACKSMITH_VARS` defaults.
        2. Environment variables (`STACKSMITH_VAR_<NAME>`)
        3. Explicit ordered CLI input layers from `input_layers`, when provided.

    After resolution, config-level `config_validations` Python rules are applied.

    Args:
        vars_file: Optional path (or remote URL) to a vars YAML/JSON file.
        input_layers: Optional ordered sequence of `(kind, value)` CLI inputs.
            When provided, these layers are deep-merged in the order supplied.
        config_validations: Optional per-variable validation rules from the tool
            config. Keyed by variable name.
        config_validation_base_path: Base directory for config-defined validation
            scripts.
        cache_dir: Cache directory for fetching remote resources.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        merge_mode: Merge strategy for layered vars files and inline values.

    Returns:
        Dict of resolved input name to value.

    Raises:
        StacksmithConfigError: If any input source is invalid or if validation rules
            are misconfigured.
        StacksmithValidationError: If an input fails config-level validation.
    """
    resolved = {}

    # Layer 1: vars file(s)
    for vars_path in _iter_vars_files(vars_file):
        _apply_vars_source(
            resolved,
            vars_path,
            cache_dir=cache_dir,
            auth_config=auth_config,
            merge_mode=merge_mode,
        )

    # Layer 2: environment variables
    for env_key, env_val in os.environ.items():
        if env_val is None or not env_key.startswith(_ENV_PREFIX):
            continue

        name = env_key.removeprefix(_ENV_PREFIX).lower()
        coerced = _coerce_value(env_val)
        _merge_resolved_value(resolved, name, coerced, merge_mode=merge_mode)

    # Layer 3: Explicit ordered CLI inputs.
    for kind, value in input_layers or []:
        match kind:
            case "vars":
                _apply_vars_source(
                    resolved,
                    value,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                    merge_mode=merge_mode,
                )
            case "var":
                _apply_cli_var_item(resolved, value, merge_mode=merge_mode)
            case _:
                raise StacksmithConfigError(f"Unsupported input layer kind: {kind}")

    if context is None:
        context = {}
    rendered_inputs = deepcopy(resolved)
    render_context = {"inputs": rendered_inputs, **context}
    _render_input_values(rendered_inputs, render_context)
    resolved = rendered_inputs

    # Config-level validations run after input resolution.
    if config_validations:
        for name, spec in config_validations.items():
            if name in resolved:
                outcome, error_msg = validate_value(
                    spec,
                    resolved[name],
                    base_path=config_validation_base_path,
                    context={"name": name, "kind": "config_variable"},
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                )
                if outcome != InputValidationOutcome.PASS:
                    raise StacksmithValidationError(
                        f"Input '{name}' failed config validation: {error_msg}"
                    )

    return resolved
