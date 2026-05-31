import json
import re
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from deepmerge import Merger
from jsonschema import validate
from loguru import logger as LOGGER

from .enums import MergeMode
from .exceptions import StacksmithConfigError, StacksmithNotFoundError
from .models import RunFile, StackDefinition, ToolConfig

_STACK_SCHEMA: dict[str, Any] | None = None
_CONFIG_SCHEMA: dict[str, Any] | None = None
_RUNFILE_SCHEMA: dict[str, Any] | None = None
_STACK_MERGER = Merger(
    [(dict, ["merge"]), (list, ["append"]), (set, ["union"])],
    ["override"],
    ["override"],
)
_CONFIG_MERGER = Merger(
    [(dict, ["merge"]), (list, ["append"]), (set, ["union"])],
    ["override"],
    ["override"],
)

_LEGACY_GIT_URL_RE = re.compile(
    r"^git\+(?P<repo_url>https?://[^/]+/[^@]+?|ssh://[^/]+/[^@]+?)"
    r"//(?P<path>[^@]+)"
    r"(?:@(?P<ref>.+))?$"
)

_URL_PREFIXES = ("http://", "https://", "ssh://", "git://", "git@")
_MODULE_REGISTRY_RE = re.compile(r"^[^\s/]+/[^\s/]+(?:/[^\s/]+)?$")


def _warn_legacy(path: Path, message: str) -> None:
    LOGGER.warning(
        "Deprecated config syntax in {path}: {message}",
        path=path,
        message=message,
    )


def _parse_legacy_git_url(value: str) -> tuple[str, str, str | None]:
    match = _LEGACY_GIT_URL_RE.match(value)
    if match is None:
        raise StacksmithConfigError(
            "Invalid git+ reference format: "
            f"{value}. Expected git+<proto>://<host>/<repo>//path[@ref]"
        )
    return match.group("repo_url"), match.group("path"), match.group("ref")


def _coerce_legacy_file_reference(
    value: Any,
    *,
    path: Path,
    location: str,
) -> Any:
    if not isinstance(value, str):
        return value

    if value.startswith("git+"):
        repo, file_path, ref = _parse_legacy_git_url(value)
        payload: dict[str, Any] = {
            "source": "git",
            "data": {"repo": repo, "path": file_path},
        }
        if ref:
            payload["data"]["ref"] = ref
        _warn_legacy(path, f"{location} uses legacy git+ URL string '{value}'")
        return payload

    if value.startswith(("http://", "https://")):
        _warn_legacy(path, f"{location} uses legacy HTTP URL string '{value}'")
        return {"source": "http", "data": {"url": value}}

    _warn_legacy(path, f"{location} uses legacy local path string '{value}'")
    return {"source": "local", "data": {"path": value}}


def _parse_legacy_module_git_source(source: str) -> tuple[str, str | None]:
    normalized = source.removeprefix("git::")
    if ".git//" in normalized:
        repo_prefix, module_path = normalized.split(".git//", 1)
        return f"{repo_prefix}.git", module_path
    return normalized, None


def _coerce_legacy_module_source(
    module: dict[str, Any],
    *,
    path: Path,
    module_name: str,
) -> None:
    source = module.get("source")
    if not isinstance(source, str):
        return

    version = module.pop("version", None)
    if source.startswith("git+"):
        repo, module_path, ref = _parse_legacy_git_url(source)
        selected_ref = ref or version
        if not isinstance(selected_ref, str) or not selected_ref.strip():
            raise StacksmithConfigError(
                f"Module '{module_name}' git source requires a non-empty version/ref"
            )
        module["source"] = {
            "source": "git",
            "data": {
                "repo": repo,
                "path": module_path,
                "ref": selected_ref,
            },
        }
        _warn_legacy(
            path,
            f"module_mappings.{module_name}.source uses legacy git+ URL syntax",
        )
        return

    if _MODULE_REGISTRY_RE.fullmatch(source):
        if not isinstance(version, str) or not version.strip():
            raise StacksmithConfigError(
                f"Module '{module_name}' registry source requires a non-empty version"
            )
        module["source"] = {
            "source": "registry",
            "data": {"address": source, "version": version},
        }
        _warn_legacy(
            path,
            f"module_mappings.{module_name} uses legacy source/version registry shape",
        )
        return

    if source.startswith(_URL_PREFIXES) or source.startswith("git::"):
        if not isinstance(version, str) or not version.strip():
            raise StacksmithConfigError(
                f"Module '{module_name}' git source requires a non-empty version/ref"
            )
        repo, module_path = _parse_legacy_module_git_source(source)
        payload: dict[str, Any] = {"repo": repo, "ref": version}
        if module_path:
            payload["path"] = module_path
        module["source"] = {
            "source": "git",
            "data": payload,
        }
        _warn_legacy(
            path,
            f"module_mappings.{module_name} uses legacy source/version git shape",
        )
        return

    raise StacksmithConfigError(
        f"Unsupported legacy module source for '{module_name}': {source}"
    )


def _coerce_legacy_provider_source(
    provider: dict[str, Any],
    *,
    path: Path,
    provider_name: str,
) -> None:
    source = provider.get("source")
    if not isinstance(source, str):
        return

    version = provider.pop("version", None)
    if not isinstance(version, str) or not version.strip():
        raise StacksmithConfigError(
            f"Provider '{provider_name}' legacy source requires a non-empty version"
        )

    provider["source"] = {
        "source": "registry",
        "data": {"address": source, "version": version},
    }
    _warn_legacy(
        path,
        f"provider_mappings.{provider_name} uses legacy source/version shape",
    )


def _normalize_legacy_runfile_references(
    data: dict[str, Any], path: Path
) -> dict[str, Any]:
    result = deepcopy(data)
    for key in ("stacks", "configs", "vars"):
        items = result.get(key)
        if not isinstance(items, list):
            continue
        result[key] = [
            _coerce_legacy_file_reference(
                item,
                path=path,
                location=f"{key}[{index}]",
            )
            for index, item in enumerate(items)
        ]
    return result


def _normalize_legacy_config_references(
    data: dict[str, Any], path: Path
) -> dict[str, Any]:
    result = deepcopy(data)

    var_validations = result.get("var_validations")
    if isinstance(var_validations, dict):
        for name, spec in var_validations.items():
            if isinstance(spec, dict) and isinstance(spec.get("script"), str):
                spec["script"] = _coerce_legacy_file_reference(
                    spec["script"],
                    path=path,
                    location=f"var_validations.{name}.script",
                )

    module_mappings = result.get("module_mappings")
    if isinstance(module_mappings, dict):
        for module_name, module in module_mappings.items():
            if not isinstance(module, dict):
                continue
            _coerce_legacy_module_source(module, path=path, module_name=module_name)
            properties = module.get("properties")
            if not isinstance(properties, dict):
                continue
            for prop_name, prop_spec in properties.items():
                if not isinstance(prop_spec, dict):
                    continue
                transform = prop_spec.get("transform")
                if isinstance(transform, dict) and isinstance(
                    transform.get("script"), str
                ):
                    transform["script"] = _coerce_legacy_file_reference(
                        transform["script"],
                        path=path,
                        location=(
                            f"module_mappings.{module_name}.properties."
                            f"{prop_name}.transform.script"
                        ),
                    )
                validation = prop_spec.get("validation")
                if isinstance(validation, dict) and isinstance(
                    validation.get("script"), str
                ):
                    validation["script"] = _coerce_legacy_file_reference(
                        validation["script"],
                        path=path,
                        location=(
                            f"module_mappings.{module_name}.properties."
                            f"{prop_name}.validation.script"
                        ),
                    )

    provider_mappings = result.get("provider_mappings")
    if isinstance(provider_mappings, dict):
        for provider_name, provider in provider_mappings.items():
            if not isinstance(provider, dict):
                continue
            _coerce_legacy_provider_source(
                provider,
                path=path,
                provider_name=provider_name,
            )
            instances = provider.get("instances")
            if not isinstance(instances, dict):
                continue
            for instance_name, instance in instances.items():
                if not isinstance(instance, dict):
                    continue
                config = instance.get("config")
                if isinstance(config, dict) and isinstance(config.get("script"), str):
                    config["script"] = _coerce_legacy_file_reference(
                        config["script"],
                        path=path,
                        location=(
                            f"provider_mappings.{provider_name}.instances."
                            f"{instance_name}.config.script"
                        ),
                    )

    plan_validations = result.get("plan_validations")
    if isinstance(plan_validations, dict):
        for name, plan_spec in plan_validations.items():
            if not isinstance(plan_spec, dict):
                continue
            rule = plan_spec.get("rule")
            if isinstance(rule, dict) and isinstance(rule.get("script"), str):
                rule["script"] = _coerce_legacy_file_reference(
                    rule["script"],
                    path=path,
                    location=f"plan_validations.{name}.rule.script",
                )

    return result


def _resolve_merge_mode(merge_mode: str | MergeMode) -> MergeMode:
    return MergeMode(merge_mode)


def _merge_layer(
    merged: dict[str, Any],
    layer: dict[str, Any],
    *,
    merge_mode: str | MergeMode,
    merger: Merger,
) -> dict[str, Any]:
    if _resolve_merge_mode(merge_mode) == MergeMode.OVERRIDE:
        return deepcopy(layer)
    return merger.merge(merged, layer)


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


def _load_file(path: Path) -> dict[str, Any]:
    suffix, text = _read_file_text(path)
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
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    merged: dict[str, Any] = {}
    merged_locations: dict[tuple[str, ...], str] = {}
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        layer, locations = _load_file_with_locations(resolved_path)
        normalized_legacy = _normalize_legacy_config_references(layer, resolved_path)
        normalized_layer = _resolve_config_script_paths(
            normalized_legacy,
            resolved_path.parent,
        )
        merged = _merge_layer(
            merged,
            normalized_layer,
            merge_mode=merge_mode,
            merger=_CONFIG_MERGER,
        )
        merged_locations = _merge_layer(
            merged_locations,
            locations,
            merge_mode=merge_mode,
            merger=_CONFIG_MERGER,
        )
    return merged, merged_locations


def _resolve_config_script_paths(
    data: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:

    def _absolutize_script(spec: dict[str, Any]) -> None:
        script = spec.get("script")
        if not isinstance(script, dict):
            return
        if script.get("source") != "local":
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


def _merge_config_layers(
    config_paths: list[Path],
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        normalized_legacy = _normalize_legacy_config_references(
            _load_file(resolved_path),
            resolved_path,
        )
        normalized_layer = _resolve_config_script_paths(
            normalized_legacy,
            resolved_path.parent,
        )
        merged = _merge_layer(
            merged,
            normalized_layer,
            merge_mode=merge_mode,
            merger=_CONFIG_MERGER,
        )
    return merged


def _normalize_config_paths(path: Path | list[Path]) -> list[Path]:
    config_paths = [path] if isinstance(path, Path) else list(path)
    if not config_paths:
        raise StacksmithConfigError("At least one config file path must be provided")
    return config_paths


def _normalize_stack_paths(path: Path | list[Path]) -> list[Path]:
    stack_paths = [path] if isinstance(path, Path) else list(path)
    if not stack_paths:
        raise StacksmithConfigError("At least one stack file path must be provided")
    return stack_paths


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


def _merge_stack_layers(
    stack_paths: list[Path],
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for stack_path in stack_paths:
        resolved_path = stack_path.resolve()
        layer = _load_file(resolved_path)
        merged = _merge_layer(
            merged,
            layer,
            merge_mode=merge_mode,
            merger=_STACK_MERGER,
        )
    return merged


def _build_config(data: dict[str, Any], config_paths: list[Path]) -> ToolConfig:
    validate(instance=data, schema=_get_config_schema())
    config = ToolConfig.model_validate(data)
    config.source_path = config_paths[-1].resolve()
    return config


def load_stack(
    path: Path,
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> StackDefinition:
    """Load and validate a stack definition file.

    Args:
        path: Path to a stack.yaml, stack.yml, or stack.json file.

    Returns:
        Validated StackDefinition model.

    Raises:
        jsonschema.ValidationError: If the file does not match the stack schema.
    """
    return load_stacks(path, merge_mode=merge_mode)


def load_stacks(
    path: Path | list[Path],
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> StackDefinition:
    """Load and deep-merge one or more stack definition files.

    Args:
        path: `Path` or list of `Path`s to stack YAML/JSON files.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.

    Returns:
        Validated merged stack model.

    Raises:
        jsonschema.ValidationError: If any file or the merged result does not
            match the stack schema.
    """
    stack_paths = _normalize_stack_paths(path)
    data = _merge_stack_layers(stack_paths, merge_mode=merge_mode)
    data = _dedupe_unique_stack_fields(data)
    return _build_stack(data, stack_paths)


def load_config(
    path: Path | list[Path],
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    config_paths = _normalize_config_paths(path)
    data = _merge_config_layers(config_paths, merge_mode=merge_mode)
    return _build_config(data, config_paths)


def load_config_with_locations(
    path: Path | list[Path],
    *,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    data, locations = _merge_config_layers_with_locations(
        config_paths,
        merge_mode=merge_mode,
    )
    return _build_config(data, config_paths), locations


def load_runfile(path: Path) -> RunFile:
    """Load and validate a Stacksmith run file.

    Args:
        path: Path to a `stacksmith.yaml`, `stacksmith.yml`, or JSON run file.

    Returns:
        Validated run-file model.

    Raises:
        jsonschema.ValidationError: If the file does not match the run-file schema.
    """
    resolved_path = path.resolve()
    data = _normalize_legacy_runfile_references(
        _load_file(resolved_path), resolved_path
    )
    validate(instance=data, schema=_get_runfile_schema())
    return RunFile.model_validate(data)
