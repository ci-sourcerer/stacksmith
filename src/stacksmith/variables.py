import json
import os
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml

from .enums import MergeMode
from .exceptions import StacksmithConfigError, StacksmithValidationError
from .input_parsing import coerce_input_value, parse_var_assignment
from .loading.validation import validate_fragment
from .merging import AddressAwareMerger
from .models import (
    FileReference,
    InlineReference,
    MergeConfig,
    RemoteAuthConfig,
    ValidationSpec,
    VariableReference,
)
from .remote import resolve_if_remote
from .templating import (
    create_sandboxed_jinja_environment,
    render_jinja_template_values,
)
from .utils import get_current_git_repository, stacksmith_env_list
from .validations import InputValidationOutcome, validate_value

_JINJA_ENV = create_sandboxed_jinja_environment()

_ENV_PREFIX = "STACKSMITH_VAR_"
InputLayer: TypeAlias = tuple[Literal["vars", "var"], str | VariableReference]


def _merge_resolved_value(
    resolved: dict[str, Any], name: str, incoming: Any, merger: AddressAwareMerger
) -> None:
    if name in resolved:
        resolved[name] = merger.value_strategy(
            [name],
            deepcopy(resolved[name]),
            deepcopy(incoming),
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
            loaded = yaml.safe_load(text) or {}
        case ".json":
            loaded = json.loads(text)
        case _:
            raise StacksmithConfigError(f"Unsupported vars file extension: {suffix}")
    validate_fragment(
        loaded,
        "vars.schema.json",
        "variables",
        path.resolve(),
    )
    return loaded


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


def _apply_vars_source(
    resolved: dict[str, Any],
    source: str | Path | VariableReference,
    merger: AddressAwareMerger,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> None:
    for name, value in _load_vars_source(
        source,
        cache_dir=cache_dir,
        auth_config=auth_config,
    ).items():
        _merge_resolved_value(resolved, name, value, merger)


def _load_vars_source(
    source: str | Path | VariableReference,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> dict[str, Any]:
    if isinstance(source, InlineReference):
        return source.data
    return _load_vars_file(source, cache_dir=cache_dir, auth_config=auth_config)


def _apply_cli_var_item(
    resolved: dict[str, Any], raw_item: str, merger: AddressAwareMerger
) -> None:
    name, raw_value = parse_var_assignment(raw_item)
    _merge_resolved_value(
        resolved,
        name,
        coerce_input_value(raw_value),
        merger,
    )


def _with_git_repository_context(context: dict[str, Any]) -> dict[str, Any]:
    repository = get_current_git_repository()
    if repository is None or "env" in context:
        return context
    return {**context, "env": {"git_repository": repository}}


def resolve_inputs(
    vars_file: (
        str | Path | FileReference | Sequence[str | Path | FileReference] | None
    ) = None,
    input_layers: Sequence[InputLayer] | None = None,
    config_validations: dict[str, ValidationSpec] | None = None,
    config_validation_base_path: Path | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
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
    merger = AddressAwareMerger(merge_mode, "vars")

    # Layer 1: vars file(s)
    for vars_path in _iter_vars_files(vars_file):
        _apply_vars_source(
            resolved,
            vars_path,
            merger,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )

    # Layer 2: environment variables
    for env_key, env_val in os.environ.items():
        if env_val is None or not env_key.startswith(_ENV_PREFIX):
            continue

        name = env_key.removeprefix(_ENV_PREFIX).lower()
        coerced = coerce_input_value(env_val)
        _merge_resolved_value(resolved, name, coerced, merger)

    # Layer 3: Explicit ordered CLI inputs.
    for kind, value in input_layers or []:
        match kind:
            case "vars":
                _apply_vars_source(
                    resolved,
                    value,
                    merger,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                )
            case "var":
                _apply_cli_var_item(resolved, value, merger)
            case _:
                raise StacksmithConfigError(f"Unsupported input layer kind: {kind}")

    context = _with_git_repository_context(context or {})
    rendered_inputs = deepcopy(resolved)
    render_context = {"inputs": rendered_inputs, **context}
    resolved = render_jinja_template_values(
        rendered_inputs,
        render_context,
        jinja_env=_JINJA_ENV,
    )

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
