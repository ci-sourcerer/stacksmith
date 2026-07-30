from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2.sandbox import SandboxedEnvironment

from ..enums import MergeMode
from ..merging import AddressAwareMerger
from ..models import (
    MergeConfig,
    RunFile,
    StackDefinition,
    StacksmithTestManifest,
    ToolConfig,
)
from ..templating import render_jinja_template_values
from ..utils import get_current_git_repository, normalize_path_input
from .files import load_object_file, load_object_file_with_locations
from .references import (
    resolve_config_local_references,
    resolve_runfile_local_references,
    resolve_test_manifest_local_references,
)
from .validation import (
    build_validated_model,
    validate_effective_document,
    validate_fragment,
)


def _merge_layer(
    merged: dict[str, Any],
    layer: dict[str, Any],
    merger: AddressAwareMerger,
) -> dict[str, Any]:
    return merger.merge(merged, deepcopy(layer))


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
        context["env"] = {"git_repository": repository}
    return context


def _render_runfile_stage_one_templates(
    runfile_data: dict[str, Any], runfile_path: Path
) -> dict[str, Any]:
    return render_jinja_template_values(
        runfile_data,
        _runfile_template_context(runfile_path),
        jinja_env=_JINJA_ENV,
    )


def _merge_config_layers_with_locations(
    config_paths: list[Path], merge_mode: MergeConfig = MergeMode.DEEP
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    merged: dict[str, Any] = {}
    merged_locations: dict[tuple[str, ...], str] = {}
    merger = AddressAwareMerger(merge_mode, "config")
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        layer, locations = load_object_file_with_locations(resolved_path)
        validate_fragment(
            layer,
            "config.schema.json",
            "Stacksmith config",
            resolved_path,
        )
        normalized_layer = resolve_config_local_references(
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


def _merge_config_layers(
    config_paths: list[Path], merge_mode: MergeConfig = MergeMode.DEEP
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "config")
    for config_path in config_paths:
        resolved_path = config_path.resolve()
        layer = load_object_file(resolved_path)
        validate_fragment(
            layer,
            "config.schema.json",
            "Stacksmith config",
            resolved_path,
        )
        normalized_layer = resolve_config_local_references(
            layer,
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
    validate_effective_document(
        data,
        "stack.schema.json",
        "stack definition",
        stack_paths,
    )
    stack = build_validated_model(
        StackDefinition,
        data,
        "stack definition",
        stack_paths,
    )
    stack.source_path = stack_paths[-1].resolve()
    return stack


def _with_git_repository_template_context(
    template_context: Mapping[str, Any] | None, stack_source_path: Path
) -> Mapping[str, Any] | None:
    repository = get_current_git_repository(stack_source_path.parent)
    if template_context is None or repository is None or "env" in template_context:
        return template_context
    return {**template_context, "env": {"git_repository": repository}}


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
        layer = load_object_file(
            resolved_path,
            template_context=template_context,
            strict_template_context=strict_template_context,
        )
        if strict_template_context:
            validate_fragment(
                layer,
                "stack.schema.json",
                "stack definition",
                resolved_path,
            )
        merged = _merge_layer(merged, layer, merger)
    return merged


def _build_config(data: dict[str, Any], config_paths: list[Path]) -> ToolConfig:
    validate_effective_document(
        data,
        "config.schema.json",
        "Stacksmith config",
        config_paths,
    )
    config = build_validated_model(
        ToolConfig,
        data,
        "Stacksmith config",
        config_paths,
    )
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
    path: Path | list[Path], merge_mode: MergeConfig = MergeMode.DEEP
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
    stack = build_validated_model(
        StackDefinition,
        data,
        "stack definition",
        stack_paths,
    )
    stack.source_path = stack_paths[-1].resolve()
    return stack


def load_config(
    path: Path | list[Path], merge_mode: MergeConfig = MergeMode.DEEP
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
    path: Path | list[Path], merge_mode: MergeConfig = MergeMode.DEEP
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
    runfile_paths: list[Path], merge_mode: MergeConfig = MergeMode.DEEP
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "runfile")
    for runfile_path in runfile_paths:
        resolved_path = runfile_path.resolve()
        loaded_layer = load_object_file(resolved_path)
        rendered_layer = _render_runfile_stage_one_templates(
            loaded_layer,
            resolved_path,
        )
        validate_fragment(
            rendered_layer,
            "runfile.schema.json",
            "runfile",
            resolved_path,
        )
        layer = resolve_runfile_local_references(
            rendered_layer,
            resolved_path.parent,
        )
        merged = _merge_layer(merged, layer, merger)
    return merged


def _merge_test_manifest_layers(
    manifest_paths: list[Path], merge_mode: MergeConfig = MergeMode.DEEP
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    merger = AddressAwareMerger(merge_mode, "config")
    for manifest_path in manifest_paths:
        resolved_path = manifest_path.resolve()
        loaded_layer = load_object_file(resolved_path)
        validate_fragment(
            loaded_layer,
            "test_manifest.schema.json",
            "test manifest",
            resolved_path,
        )
        layer = resolve_test_manifest_local_references(
            loaded_layer,
            resolved_path.parent,
        )
        merged = _merge_layer(merged, layer, merger)
    return merged


def _build_test_manifest(
    data: dict[str, Any], manifest_paths: list[Path]
) -> StacksmithTestManifest:
    validate_effective_document(
        data,
        "test_manifest.schema.json",
        "test manifest",
        manifest_paths,
    )
    manifest = build_validated_model(
        StacksmithTestManifest,
        data,
        "test manifest",
        manifest_paths,
    )
    manifest.source_path = manifest_paths[-1].resolve()
    return manifest


def load_test_manifest(
    path: Path, merge_mode: MergeConfig = MergeMode.DEEP
) -> StacksmithTestManifest:
    """Load and validate one Stacksmith YAML test manifest.

    Args:
        path: Path to a tests.yaml, tests.yml, or tests.json file.
        merge_mode: Merge strategy used for layered test manifests.

    Returns:
        Validated test manifest model.

    Raises:
        jsonschema.ValidationError: If the file does not match the test schema.
    """
    return load_test_manifests(path, merge_mode=merge_mode)


def load_test_manifests(
    path: Path | list[Path], merge_mode: MergeConfig = MergeMode.DEEP
) -> StacksmithTestManifest:
    """Load and deep-merge one or more Stacksmith test manifests.

    Args:
        path: `Path` or list of `Path`s to tests YAML/JSON files.
            When a list is provided, files are deep-merged in order where later
            files override earlier scalar values, dicts merge recursively, and
            lists append.
        merge_mode: Merge strategy used for layered test manifests.

    Returns:
        Validated merged test manifest model.

    Raises:
        jsonschema.ValidationError: If the file does not match the test schema.
    """
    manifest_paths = normalize_path_input(
        path,
        empty_error="At least one test manifest path must be provided",
    )
    data = _merge_test_manifest_layers(manifest_paths, merge_mode=merge_mode)
    return _build_test_manifest(data, manifest_paths)


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
    path: Path | list[Path], merge_mode: MergeConfig = MergeMode.DEEP
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
    validate_effective_document(
        data,
        "runfile.schema.json",
        "runfile",
        runfile_paths,
    )
    return build_validated_model(
        RunFile,
        data,
        "runfile",
        runfile_paths,
    )
