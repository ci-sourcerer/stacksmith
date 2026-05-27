import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from deepmerge import Merger
from jsonschema import validate

from .exceptions import StacksmithConfigError, StacksmithNotFoundError
from .models import StackDefinition, ToolConfig
from .remote import is_remote_url

_STACK_SCHEMA: dict[str, Any] | None = None
_CONFIG_SCHEMA: dict[str, Any] | None = None
_CONFIG_MERGER = Merger(
    [(dict, ["merge"]), (list, ["append"]), (set, ["union"])],
    ["override"],
    ["override"],
)


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


def _load_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise StacksmithNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

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
    if not path.exists():
        raise StacksmithNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    match suffix:
        case ".yaml" | ".yml":
            loaded = yaml.safe_load(text)
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded, _extract_yaml_locations(text, path)
        case ".json":
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded, {}
        case _:
            raise StacksmithConfigError(
                f"Unsupported file extension '{suffix}'. Use .yaml, .yml, or .json."
            )


def _merge_config_layers_with_locations(
    config_paths: list[Path],
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    merged: dict[str, Any] = {}
    merged_locations: dict[tuple[str, ...], str] = {}
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        layer, locations = _load_file_with_locations(resolved_path)
        normalized_layer = _resolve_config_script_paths(layer, resolved_path.parent)
        merged = _CONFIG_MERGER.merge(merged, normalized_layer)
        merged_locations = _CONFIG_MERGER.merge(merged_locations, locations)
    return merged, merged_locations


def _resolve_config_script_paths(
    data: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:

    def _absolutize_script(spec: dict[str, Any]) -> None:
        script = spec.get("script")
        if not isinstance(script, str) or not script:
            return
        if is_remote_url(script):
            return
        script_path = Path(script)
        if not script_path.is_absolute():
            spec["script"] = str((config_dir / script_path).resolve())

    result = deepcopy(data)

    var_validations = result.get("var_validations")
    if isinstance(var_validations, dict):
        for spec in var_validations.values():
            if isinstance(spec, dict):
                _absolutize_script(spec)

    module_mappings = result.get("module_mappings")
    if isinstance(module_mappings, dict):
        for module in module_mappings.values():
            if not isinstance(module, dict):
                continue
            properties = module.get("properties")
            if not isinstance(properties, dict):
                continue
            for prop_spec in properties.values():
                if not isinstance(prop_spec, dict):
                    continue
                transform = prop_spec.get("transform")
                if isinstance(transform, dict):
                    _absolutize_script(transform)
                validation = prop_spec.get("validation")
                if isinstance(validation, dict):
                    _absolutize_script(validation)

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
                    _absolutize_script(provider_config)

    plan_validations = result.get("plan_validations")
    if isinstance(plan_validations, dict):
        for plan_spec in plan_validations.values():
            if not isinstance(plan_spec, dict):
                continue
            rule = plan_spec.get("rule")
            if isinstance(rule, dict):
                _absolutize_script(rule)

    return result


def _merge_config_layers(config_paths: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        normalized_layer = _resolve_config_script_paths(
            _load_file(resolved_path), resolved_path.parent
        )
        merged = _CONFIG_MERGER.merge(merged, normalized_layer)
    return merged


def _normalize_config_paths(path: Path | list[Path]) -> list[Path]:
    config_paths = [path] if isinstance(path, Path) else list(path)
    if not config_paths:
        raise StacksmithConfigError("At least one config file path must be provided")
    return config_paths


def _build_config(data: dict[str, Any], config_paths: list[Path]) -> ToolConfig:
    validate(instance=data, schema=_get_config_schema())
    config = ToolConfig.model_validate(data)
    config.source_path = config_paths[-1].resolve()
    return config


def load_stack(path: Path) -> StackDefinition:
    """Load and validate a stack definition file.

    Args:
        path: Path to a stack.yaml, stack.yml, or stack.json file.

    Returns:
        Validated StackDefinition model.

    Raises:
        jsonschema.ValidationError: If the file does not match the stack schema.
    """
    data = _load_file(path)
    validate(instance=data, schema=_get_stack_schema())
    stack = StackDefinition.model_validate(data)
    stack.source_path = path.resolve()
    return stack


def load_config(path: Path | list[Path]) -> ToolConfig:
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
    config_paths = _normalize_config_paths(path)
    data = _merge_config_layers(config_paths)
    return _build_config(data, config_paths)


def load_config_with_locations(
    path: Path | list[Path],
) -> tuple[ToolConfig, dict[tuple[str, ...], str]]:
    """Load config and collect source locations for inline validation specs.

    Args:
        path: `Path` or list of `Path`s to stacksmith-config YAML/JSON files.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.
    Returns:
        Tuple containing the validated ToolConfig model and a dictionary mapping
        tuple keys to source locations for inline validation specs.

    Raises:
        jsonschema.ValidationError: If the file does not match the config schema.
    """
    config_paths = _normalize_config_paths(path)
    data, locations = _merge_config_layers_with_locations(config_paths)
    return _build_config(data, config_paths), locations
