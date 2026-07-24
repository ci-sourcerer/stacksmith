import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from jinja2 import ChainableUndefined, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import validate

from .enums import MergeMode
from .exceptions import StacksmithConfigError, StacksmithNotFoundError
from .merging import AddressAwareMerger
from .models import MergeConfig, RunFile, StackDefinition, ToolConfig
from .utils import (
    get_current_git_repository,
    normalize_path_input,
    render_jinja_template_values,
)

_STACK_SCHEMA: dict[str, Any] | None = None
_CONFIG_SCHEMA: dict[str, Any] | None = None
_RUNFILE_SCHEMA: dict[str, Any] | None = None


def _resolve_runfile_local_references(
    data: dict[str, Any],
    runfile_dir: Path,
) -> dict[str, Any]:
    result = deepcopy(data)
    for key in ("stacks", "configs", "vars"):
        items = result.get(key)
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("source") != "local":
                continue

            payload = item.get("data")
            if not isinstance(payload, dict):
                continue

            local_path_raw = payload.get("path")
            if not isinstance(local_path_raw, str) or not local_path_raw:
                continue

            local_path = Path(local_path_raw).expanduser()
            if not local_path.is_absolute():
                payload["path"] = str((runfile_dir / local_path).resolve())
    return result


def _merge_layer(
    merged: dict[str, Any],
    layer: dict[str, Any],
    merger: AddressAwareMerger,
) -> dict[str, Any]:
    return merger.merge(merged, deepcopy(layer))


def _load_json_schema(name: str) -> dict[str, Any]:
    return json.loads(
        files("stacksmith.schemas").joinpath(name).read_text(encoding="utf-8")
    )


def _get_stack_schema() -> dict[str, Any]:
    global _STACK_SCHEMA
    if _STACK_SCHEMA is None:
        _STACK_SCHEMA = _load_json_schema("stack.schema.json")
    return _STACK_SCHEMA


def _get_config_schema() -> dict[str, Any]:
    global _CONFIG_SCHEMA
    if _CONFIG_SCHEMA is None:
        _CONFIG_SCHEMA = _load_json_schema("config.schema.json")
    return _CONFIG_SCHEMA


_JINJA_ENV = SandboxedEnvironment()


def _runfile_template_context(runfile_path: Path) -> dict[str, Any]:
    resolved_path = runfile_path.resolve()
    context: dict[str, Any] = {
        "runfile": {
            "path": str(resolved_path),
            "dir": str(resolved_path.parent),
            "name": resolved_path.name,
            "stem": resolved_path.stem,
        }
    }
    if repository := get_current_git_repository(resolved_path.parent):
        context["git_repository"] = repository
    return context


def _render_runfile_stage_one_templates(
    runfile_data: dict[str, Any],
    runfile_path: Path,
) -> dict[str, Any]:
    return render_jinja_template_values(
        runfile_data,
        _runfile_template_context(runfile_path),
        jinja_env=_JINJA_ENV,
    )


def _get_runfile_schema() -> dict[str, Any]:
    global _RUNFILE_SCHEMA
    if _RUNFILE_SCHEMA is None:
        _RUNFILE_SCHEMA = _load_json_schema("runfile.schema.json")
    return _RUNFILE_SCHEMA


def _read_file_text(path: Path) -> tuple[str, str]:
    if not path.exists():
        raise StacksmithNotFoundError(f"File not found: {path}")

    return path.suffix.lower(), path.read_text(encoding="utf-8")


def _parse_object_file(path: Path, suffix: str, text: str) -> dict[str, Any]:
    match suffix:
        case ".yaml" | ".yml":
            loaded = yaml.safe_load(text)
            if loaded is None:
                return {}
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded
        case ".json":
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded
        case _:
            raise StacksmithConfigError(
                f"Unsupported file extension '{suffix}'. Use .yaml, .yml, or .json."
            )


def _render_stack_template(
    text: str,
    path: Path,
    context: Mapping[str, Any],
    strict: bool,
) -> str:
    undefined = StrictUndefined if strict else ChainableUndefined
    environment = SandboxedEnvironment(undefined=undefined)
    try:
        return environment.from_string(text).render(context)
    except TemplateError as exc:
        raise StacksmithConfigError(
            f"Could not render stack template '{path}': {exc}"
        ) from exc


def _load_file(
    path: Path,
    template_context: Mapping[str, Any] | None = None,
    strict_template_context: bool = False,
) -> dict[str, Any]:
    suffix, text = _read_file_text(path)
    if template_context is not None:
        text = _render_stack_template(
            text,
            path,
            template_context,
            strict=strict_template_context,
        )
    return _parse_object_file(path, suffix, text)


def _extract_yaml_locations(text: str, path: Path) -> dict[tuple[str, ...], str]:
    root = yaml.compose(text)
    locations: dict[tuple[str, ...], str] = {}

    def _format_range(node: yaml.nodes.Node) -> str:
        start = node.start_mark.line + 1
        end = node.end_mark.line + 1
        return f"{path.name}:{start}-{end}"

    def _walk(node: yaml.nodes.Node, current_path: tuple[str, ...]) -> None:
        if isinstance(node, yaml.nodes.MappingNode):
            for key_node, value_node in node.value:
                if not isinstance(key_node, yaml.nodes.ScalarNode):
                    continue
                key = key_node.value
                next_path = (*current_path, key)
                if key in {"validation", "transform"} and isinstance(
                    value_node, yaml.nodes.MappingNode
                ):
                    locations[next_path] = _format_range(value_node)
                if current_path == ("var_validations",) and isinstance(
                    value_node, yaml.nodes.MappingNode
                ):
                    locations[next_path] = _format_range(value_node)
                if (
                    len(current_path) == 2
                    and current_path[0] == "plan_validations"
                    and key == "rule"
                    and isinstance(value_node, yaml.nodes.MappingNode)
                ):
                    locations[next_path] = _format_range(value_node)
                _walk(value_node, next_path)
        elif isinstance(node, yaml.nodes.SequenceNode):
            for item in node.value:
                _walk(item, current_path)

    if root is not None:
        _walk(root, ())
    return locations


def _load_file_with_locations(
    path: Path,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    suffix, text = _read_file_text(path)
    loaded = _parse_object_file(path, suffix, text)
    if suffix in {".yaml", ".yml"}:
        return loaded, _extract_yaml_locations(text, path)
    return loaded, {}


def _merge_config_layers_with_locations(
    config_paths: list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    merged: dict[str, Any] = {}
    merged_locations: dict[tuple[str, ...], str] = {}
    merger = AddressAwareMerger(merge_mode, "config")
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        layer, locations = _load_file_with_locations(resolved_path)
        normalized_layer = _resolve_config_script_paths(
            layer,
            resolved_path.parent,
        )
        merger.replaced_paths.clear()
        merged = _merge_layer(merged, normalized_layer, merger)
        merged_locations = _merge_config_locations(
            merged_locations,
            locations,
            merger.replaced_paths,
        )
    return merged, merged_locations


def _merge_config_locations(
    merged: dict[tuple[str, ...], str],
    incoming: dict[tuple[str, ...], str],
    replaced_paths: Sequence[tuple[Any, ...]],
) -> dict[tuple[str, ...], str]:
    result = {
        path: location
        for path, location in merged.items()
        if not any(path[: len(replaced)] == replaced for replaced in replaced_paths)
    }
    result.update(incoming)
    return result


def _absolutize_module_source(source: Any, config_dir: Path) -> None:
    if not isinstance(source, dict) or source.get("source") != "local":
        return

    payload = source.get("data")
    if not isinstance(payload, dict):
        return

    module_path_raw = payload.get("path")
    if not isinstance(module_path_raw, str) or not module_path_raw:
        return

    module_path = Path(module_path_raw).expanduser()
    if not module_path.is_absolute():
        payload["path"] = str((config_dir / module_path).resolve())


def _absolutize_script(spec: dict[str, Any], config_dir: Path) -> None:
    script = spec.get("script")
    if not isinstance(script, dict) or script.get("source") != "local":
        return

    payload = script.get("data")
    if not isinstance(payload, dict):
        return

    script_path_raw = payload.get("path")
    if not isinstance(script_path_raw, str) or not script_path_raw:
        return

    script_path = Path(script_path_raw)
    if not script_path.is_absolute():
        payload["path"] = str((config_dir / script_path).resolve())


def _resolve_module_mapping_paths(module: Any, config_dir: Path) -> None:
    if not isinstance(module, dict):
        return

    _absolutize_module_source(module.get("source"), config_dir)

    properties = module.get("properties")
    if not isinstance(properties, dict):
        return
    for prop_spec in properties.values():
        if not isinstance(prop_spec, dict):
            continue
        transform = prop_spec.get("transform")
        if isinstance(transform, dict):
            _absolutize_script(transform, config_dir)
        validation = prop_spec.get("validation")
        if isinstance(validation, dict):
            _absolutize_script(validation, config_dir)


def _resolve_config_script_paths(
    data: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:
    result = deepcopy(data)

    var_validations = result.get("var_validations")
    if isinstance(var_validations, dict):
        for spec in var_validations.values():
            if isinstance(spec, dict):
                _absolutize_script(spec, config_dir)

    module_mappings = result.get("module_mappings")
    if isinstance(module_mappings, dict):
        for module in module_mappings.values():
            _resolve_module_mapping_paths(module, config_dir)

    _resolve_module_mapping_paths(result.get("default_module_mapping"), config_dir)

    provider_mappings = result.get("provider_mappings")
    if isinstance(provider_mappings, dict):
        for provider in provider_mappings.values():
            if not isinstance(provider, dict):
                continue
            instances = provider.get("instances")
            if not isinstance(instances, dict):
                continue
            for instance in instances.values():
                if not isinstance(instance, dict):
                    continue
                provider_config = instance.get("config")
                if isinstance(provider_config, dict):
                    _absolutize_script(provider_config, config_dir)

    plan_validations = result.get("plan_validations")
    if isinstance(plan_validations, dict):
        for plan_spec in plan_validations.values():
            if not isinstance(plan_spec, dict):
                continue
            rule = plan_spec.get("rule")
            if isinstance(rule, dict):
                _absolutize_script(rule, config_dir)

    return result


def _merge_config_layers(
    config_paths: list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "config")
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        normalized_layer = _resolve_config_script_paths(
            _load_file(resolved_path),
            resolved_path.parent,
        )
        merged = _merge_layer(merged, normalized_layer, merger)
    return merged


def _dedupe_unique_stack_fields(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            key: (
                _dedupe_unique_ordered_list(value)
                if key in {"tags", "depends_on"} and isinstance(value, list)
                else _dedupe_unique_stack_fields(value)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [_dedupe_unique_stack_fields(item) for item in data]
    return data


def _dedupe_unique_ordered_list(items: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_stack(data: dict[str, Any], stack_paths: list[Path]) -> StackDefinition:
    validate(instance=data, schema=_get_stack_schema())
    stack = StackDefinition.model_validate(data)
    stack.source_path = stack_paths[-1].resolve()
    return stack


def _with_git_repository_template_context(
    template_context: Mapping[str, Any] | None,
    stack_source_path: Path,
) -> Mapping[str, Any] | None:
    repository = get_current_git_repository(stack_source_path.parent)
    if (
        template_context is None
        or repository is None
        or "git_repository" in template_context
    ):
        return template_context
    return {**template_context, "git_repository": repository}


def _merge_stack_layers(
    stack_paths: list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
    template_context: Mapping[str, Any] | None = None,
    strict_template_context: bool = False,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "stack")
    template_context = _with_git_repository_template_context(
        template_context,
        stack_paths[-1].resolve(),
    )
    for stack_path in stack_paths:
        resolved_path = stack_path.resolve()
        layer = _load_file(
            resolved_path,
            template_context=template_context,
            strict_template_context=strict_template_context,
        )
        merged = _merge_layer(merged, layer, merger)
    return merged


def _build_config(data: dict[str, Any], config_paths: list[Path]) -> ToolConfig:
    validate(instance=data, schema=_get_config_schema())
    config = ToolConfig.model_validate(data)
    config.source_path = config_paths[-1].resolve()
    return config


def load_stack(
    path: Path,
    merge_mode: MergeConfig = MergeMode.DEEP,
    template_context: Mapping[str, Any] | None = None,
    strict_template_context: bool = True,
) -> StackDefinition:
    """Load and validate a stack definition file.

    Args:
        path: Path to a stack.yaml, stack.yml, or stack.json file.
        merge_mode: Merge strategy used for layered stack files.
        template_context: Optional values available while rendering the stack source.
        strict_template_context: Whether undefined template values raise an error.

    Returns:
        Validated StackDefinition model.

    Raises:
        jsonschema.ValidationError: If the file does not match the stack schema.
    """
    return load_stacks(
        path,
        merge_mode=merge_mode,
        template_context=template_context,
        strict_template_context=strict_template_context,
    )


def load_stacks(
    path: Path | list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
    template_context: Mapping[str, Any] | None = None,
    strict_template_context: bool = True,
) -> StackDefinition:
    """Load and deep-merge one or more stack definition files.

    Args:
        path: `Path` or list of `Path`s to stack YAML/JSON files.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.
        merge_mode: Merge strategy used for layered stack files.
        template_context: Optional values available while rendering each stack source.
        strict_template_context: Whether undefined template values raise an error.

    Returns:
        Validated merged stack model.

    Raises:
        jsonschema.ValidationError: If any file or the merged result does not
            match the stack schema.
    """
    stack_paths = normalize_path_input(
        path,
        empty_error="At least one stack file path must be provided",
    )
    data = _merge_stack_layers(
        stack_paths,
        merge_mode=merge_mode,
        template_context=template_context,
        strict_template_context=strict_template_context,
    )
    data = _dedupe_unique_stack_fields(data)
    return _build_stack(data, stack_paths)


def load_stack_metadata(
    path: Path | list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> StackDefinition:
    """Load stack metadata without requiring template inputs.

    This permissive render lets discovery determine a stack's name and tags before
    its input-dependent component template is rendered for generation.

    Args:
        path: One or more stack YAML/JSON files.
        merge_mode: Merge strategy used for layered stack files.

    Returns:
        Parsed stack definition used only to resolve template inputs.
    """
    stack_paths = normalize_path_input(
        path,
        empty_error="At least one stack file path must be provided",
    )
    data = _merge_stack_layers(
        stack_paths,
        merge_mode=merge_mode,
        template_context={"inputs": {}, "stack": {"name": "", "tags": []}},
        strict_template_context=False,
    )
    for field_name, default in {
        "tags": [],
        "depends_on": [],
        "mock_outputs": {},
        "components": {},
        "operations": {},
    }.items():
        if data.get(field_name) is None:
            data[field_name] = default
    data = _dedupe_unique_stack_fields(data)
    stack = StackDefinition.model_validate(data)
    stack.source_path = stack_paths[-1].resolve()
    return stack


def load_config(
    path: Path | list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> ToolConfig:
    """Load, deep-merge, and validate one or more tool configuration files.

    Args:
        path: `Path` or list of `Path`s to stacksmith-config YAML/JSON files.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.

    Returns:
        Validated ToolConfig model.

    Raises:
        jsonschema.ValidationError: If the file does not match the config schema.
    """
    config_paths = normalize_path_input(
        path,
        empty_error="At least one config file path must be provided",
    )
    data = _merge_config_layers(config_paths, merge_mode=merge_mode)
    return _build_config(data, config_paths)


def load_config_with_locations(
    path: Path | list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[ToolConfig, dict[tuple[str, ...], str]]:
    """Load config and collect source locations for inline validation specs.

    Args:
        path: `Path` or list of `Path`s to stacksmith-config YAML/JSON files. When a
            list is provided, files are deep-merged in order where later files override
            earlier scalar values, dicts merge recursively, and lists append.
    Returns:
        Tuple containing the validated ToolConfig model and a dictionary mapping
        tuple keys to source locations for inline validation specs.

    Raises:
        jsonschema.ValidationError: If the file does not match the config schema.
    """
    config_paths = normalize_path_input(
        path,
        empty_error="At least one config file path must be provided",
    )
    data, locations = _merge_config_layers_with_locations(
        config_paths,
        merge_mode=merge_mode,
    )
    return _build_config(data, config_paths), locations


def _merge_runfile_layers(
    runfile_paths: list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "runfile")
    for runfile_path in runfile_paths:
        resolved_path = runfile_path.resolve()
        loaded_layer = _load_file(resolved_path)
        rendered_layer = _render_runfile_stage_one_templates(
            loaded_layer,
            resolved_path,
        )
        layer = _resolve_runfile_local_references(rendered_layer, resolved_path.parent)
        merged = _merge_layer(merged, layer, merger)
    return merged


def load_runfile(path: Path) -> RunFile:
    """Load and validate a Stacksmith runfile.

    Args:
        path: Path to a `stacksmith.yaml`, `stacksmith.yml`, or JSON runfile.

    Returns:
        Validated runfile model.

    Raises:
        jsonschema.ValidationError: If the file does not match the runfile schema.
    """
    return load_runfiles(path)


def load_runfiles(
    path: Path | list[Path],
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> RunFile:
    """Load and deep-merge one or more Stacksmith runfiles.

    Args:
        path: `Path` or list of `Path`s to stacksmith YAML/JSON runfiles.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.

    Returns:
        Validated merged runfile model.

    Raises:
        jsonschema.ValidationError: If the file does not match the runfile schema.
    """
    runfile_paths = normalize_path_input(
        path,
        empty_error="At least one runfile path must be provided",
    )
    data = _merge_runfile_layers(runfile_paths, merge_mode=merge_mode)
    validate(instance=data, schema=_get_runfile_schema())
    return RunFile.model_validate(data)
