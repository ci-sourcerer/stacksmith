import json
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from ..constants import GENERATED_TF_JSON
from ..exceptions import StacksmithConfigError
from ..formatters import render_module_source_for
from ..introspection import (
    discover_module_outputs,
    discover_module_variables,
)
from ..models import (
    LocalModuleSourceReference,
    ModuleMapping,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
    render_module_source_identity,
)
from ..module_mapping import auto_exposed_output_names, resolve_module_mapping
from ..stack_outputs import build_stack_output_blocks
from ..utils import (
    derive_operation_state_key,
    derive_stack_state_key,
    get_current_git_repository,
)
from ..vendor import get_vendor_dir, resolve_module_source
from .operations import (
    build_operation_module_spec,
    resolve_operation_batch,
)
from .properties import PropertyRenderer
from .providers import (
    build_provider_blocks,
    build_required_providers,
    render_provider_reference,
)

_OPERATION_RUNNER_ASSETS = ("main.tf", "local.py", "jenkins.py")
_MODULE_REFERENCE_PATTERN = re.compile(
    r"\$\{module\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}"
)
_OPERATION_BRIDGE_OUTPUT_PATTERN = re.compile(
    r"data\.terraform_remote_state\.infrastructure\.outputs\.([A-Za-z_][A-Za-z0-9_]*)"
)


def operation_module_name(name: str) -> str:
    """Generate a valid Terraform module name for an operation.

    Args:
        name: The name of the operation.

    Returns:
        A valid Terraform module name for the operation.
    """
    return f"stacksmith_operation_{re.sub(r'[^A-Za-z0-9_]', '_', name)}"


def _write_operation_runner_assets(output_dir: Path, tf_json: dict[str, Any]) -> None:
    runner_names = {
        module["spec"]["runner"]
        for module in tf_json["module"].values()
        if module.get("source") == "./.stacksmith-operation-runner"
    }
    required_assets = {
        "main.tf",
        *(f"{runner_name}.py" for runner_name in runner_names),
    }
    operation_runner_dir = output_dir / ".stacksmith-operation-runner"

    for asset_name in _OPERATION_RUNNER_ASSETS:
        if asset_name not in required_assets:
            (operation_runner_dir / asset_name).unlink(missing_ok=True)

    if not runner_names:
        if operation_runner_dir.exists() and not any(operation_runner_dir.iterdir()):
            operation_runner_dir.rmdir()
        return

    operation_runner_dir.mkdir(exist_ok=True)
    runner_assets = files("stacksmith.assets").joinpath("operation_runner")
    for asset_name in required_assets:
        (operation_runner_dir / asset_name).write_text(
            runner_assets.joinpath(asset_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _generate_operation_blocks(
    stack: StackDefinition,
    config: ToolConfig,
    operation_names: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
) -> dict[str, Any]:
    modules = {}
    selected_names = (
        list(stack.operations) if operation_names is None else list(operation_names)
    )
    if not selected_names:
        return modules

    for name in resolve_operation_batch(stack, selected_names):
        invocation = stack.operations[name]
        dependencies = [
            f"module.{operation_module_name(dependency)}"
            for dependency in invocation.depends_on
        ]
        spec = _rewrite_operation_component_references(
            build_operation_module_spec(
                stack,
                config,
                name,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir,
            )
        )
        modules[operation_module_name(name)] = {
            "source": "./.stacksmith-operation-runner",
            "runner": spec["runner"],
            "spec": spec,
            **({"depends_on": dependencies} if dependencies else {}),
        }
    return modules


def _operation_bridge_output_name(component_name: str, output_name: str) -> str:
    return f"stacksmith_operation_bridge_{component_name}_{output_name}"


def _operation_bridge_output_reference(component_name: str, output_name: str) -> str:
    return (
        "${data.terraform_remote_state.infrastructure.outputs."
        f"{_operation_bridge_output_name(component_name, output_name)}}}"
    )


def _rewrite_operation_component_references(value: Any) -> Any:
    if isinstance(value, str):
        return _MODULE_REFERENCE_PATTERN.sub(
            lambda match: _operation_bridge_output_reference(
                match.group(1),
                match.group(2),
            ),
            value,
        )
    if isinstance(value, dict):
        return {
            key: _rewrite_operation_component_references(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_operation_component_references(item) for item in value]
    return value


def _component_bridge_outputs(
    stack: StackDefinition,
    config: ToolConfig,
    cache_dir: Path | None,
    auth_config: RemoteAuthConfig | None,
    vendor_dir: Path | None,
) -> dict[str, Any]:
    references: set[tuple[str, str]] = set()
    repository_path = (
        stack.source_path.parent if stack.source_path is not None else None
    )
    for component_name, component in stack.components.items():
        mapping = resolve_module_mapping(
            config,
            component.type,
            component_name,
            repository_path=repository_path,
        )
        references.update(
            (component_name, output.mapped_from or output_name)
            for output_name, output in mapping.outputs.items()
        )
        if mapping.auto_expose_outputs:
            references.update(
                (component_name, output_name)
                for output_name in auto_exposed_output_names(
                    mapping,
                    discover_module_outputs(
                        *render_module_source_identity(
                            mapping.source,
                            options={
                                "base_path": (
                                    config.source_path.parent
                                    if config.source_path is not None
                                    else None
                                )
                            },
                        ),
                        cache_dir=cache_dir,
                        auth_config=auth_config,
                        vendor_dir=vendor_dir or get_vendor_dir(),
                    ),
                )
            )
    return {
        _operation_bridge_output_name(component_name, output_name): {
            "value": f"${{module.{component_name}.{output_name}}}",
            "sensitive": True,
        }
        for component_name, output_name in sorted(references)
    }


def _backend_config_from_child_directory(
    config: ToolConfig,
    state_key: str,
) -> dict[str, Any]:
    backend_config = config.backend.config_with_state_key(state_key)
    path = backend_config.get("path")
    if (
        config.backend.type == "local"
        and isinstance(path, str)
        and not Path(path).is_absolute()
    ):
        backend_config["path"] = str(Path("..") / path)
    return backend_config


def _generate_operations_terraform_block(
    config: ToolConfig,
    stack: StackDefinition,
    root: Path | None,
) -> dict[str, Any]:
    block: dict[str, Any] = {}
    if config.tools and config.tools.tofu:
        block["required_version"] = f"= {config.tools.tofu.version}"
    if config.backend:
        block["backend"] = {
            config.backend.type: _backend_config_from_child_directory(
                config,
                derive_operation_state_key(stack.name, stack.source_path, root),
            )
        }
    return block


def _generate_operations_infrastructure_state_data(
    config: ToolConfig,
    stack: StackDefinition,
    root: Path | None,
) -> dict[str, Any]:
    return {
        "terraform_remote_state": {
            "infrastructure": {
                "backend": config.backend.type,
                "config": _backend_config_from_child_directory(
                    config,
                    derive_stack_state_key(stack.name, stack.source_path, root),
                ),
            }
        }
    }


def _stack_context(stack: StackDefinition) -> dict[str, Any]:
    return {
        "name": stack.name,
        "tags": sorted(stack.tags),
    }


def _generate_terraform_block(
    config: ToolConfig,
    stack: StackDefinition,
    root: Path | None = None,
    provider_source_formatter_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_key = derive_stack_state_key(stack.name, stack.source_path, root)
    block = {
        "required_providers": build_required_providers(
            config,
            formatter_options=provider_source_formatter_options,
        ),
    }
    if config.tools and config.tools.tofu:
        block["required_version"] = f"= {config.tools.tofu.version}"
    if config.backend:
        block["backend"] = {
            config.backend.type: config.backend.config_with_state_key(state_key)
        }
    return block


def _required_module_input_sources(
    config: ToolConfig, mapping: ModuleMapping
) -> dict[str, set[str]]:
    input_sources: dict[str, set[str]] = {}
    for input_set_name in [
        *config.required_module_input_sets,
        *mapping.required_input_sets,
    ]:
        for input_name in config.module_input_sets[input_set_name].inputs:
            input_sources.setdefault(input_name, set()).add(input_set_name)
    return input_sources


def _missing_required_module_input_error(
    input_name: str,
    input_set_names: set[str],
    component_name: str,
) -> StacksmithConfigError:
    return StacksmithConfigError(
        "Required module input set(s) "
        f"{', '.join(sorted(input_set_names))} require input '{input_name}' "
        f"for component '{component_name}', but no resolved input value was provided"
    )


def _generate_module_blocks(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    use_local_modules: bool = False,
    vendor_dir: Path | None = None,
    module_source_formatter_options: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    modules = {}

    path_bases: list[Path] = []
    if stack.source_path is not None:
        path_bases.append(stack.source_path.parent)
    if config.source_path is not None:
        path_bases.append(config.source_path.parent)
    path_bases.append(Path.cwd())

    vendor_dir = vendor_dir or get_vendor_dir()
    repository_path = (
        stack.source_path.parent if stack.source_path is not None else None
    )
    git_repository = get_current_git_repository(repository_path)

    for component_name, component in stack.components.items():
        mapping = resolve_module_mapping(
            config,
            component.type,
            component_name,
            repository_path=repository_path,
        )

        mapping_source, mapping_version = render_module_source_identity(
            mapping.source,
            options={
                "base_path": (
                    config.source_path.parent
                    if config.source_path is not None
                    else None
                )
            },
        )
        LOGGER.debug(
            "Generating module block for component '{component_name}' of type '{component_type}' using module {source}@{version}",
            component_name=component_name,
            component_type=component.type,
            source=mapping_source,
            version=mapping_version,
        )
        if mapping.auto_inject_inputs:
            LOGGER.debug(
                "Module mapping for component '{component_name}' has auto_inject_inputs enabled",
            )

        property_renderer = PropertyRenderer(
            config=config,
            stack_definition=stack,
            resolved_inputs=resolved_inputs,
            stack=_stack_context(stack),
            component_name=component_name,
            component_type=component.type,
            base_paths=path_bases,
            git_repository=git_repository,
            cache_dir=cache_dir,
            auth_config=auth_config,
            vendor_dir=vendor_dir,
        )

        module_block = {}
        source_options = dict(module_source_formatter_options or {})
        if config.source_path is not None:
            source_options.setdefault("base_path", config.source_path.parent)

        if isinstance(mapping.source, LocalModuleSourceReference):
            module_block.update(
                render_module_source_for(
                    "terraform",
                    mapping.source,
                    options=source_options,
                )
            )
        elif use_local_modules:
            module_block["source"] = resolve_module_source(
                mapping_source,
                mapping_version,
                vendor_dir=vendor_dir,
            )
        else:
            module_block.update(
                render_module_source_for(
                    "terraform",
                    mapping.source,
                    options=source_options,
                )
            )

        if mapping.providers:
            module_block["providers"] = {
                module_provider_name: render_provider_reference(
                    config,
                    provider_reference,
                )
                for module_provider_name, provider_reference in mapping.providers.items()
            }
        for prop_name, prop_value in component.properties.items():
            property_spec = mapping.properties.get(prop_name)
            output_name, module_block[output_name] = property_renderer.render(
                prop_name,
                prop_value,
                property_spec,
            )

        required_input_sources = _required_module_input_sources(config, mapping)
        if mapping.auto_inject_inputs:
            discovered_vars = discover_module_variables(
                mapping_source,
                mapping_version,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir if use_local_modules else None,
            )
            LOGGER.debug(
                "Module '{component_type}' declares variables: {vars}",
                component_type=component.type,
                vars=sorted(discovered_vars),
            )

        required_injected_keys = []
        reserved_output_names = set(module_block)
        for input_name, input_set_names in required_input_sources.items():
            if input_name in component.properties:
                continue
            if input_name not in resolved_inputs:
                raise _missing_required_module_input_error(
                    input_name,
                    input_set_names,
                    component_name,
                )

            property_spec = mapping.properties.get(input_name)
            output_name = property_renderer.output_name(input_name, property_spec)
            if output_name in reserved_output_names:
                continue
            output_name, module_block[output_name] = property_renderer.render(
                input_name,
                resolved_inputs[input_name],
                property_spec,
            )
            required_injected_keys.append(input_name)
            reserved_output_names.add(output_name)

        if required_injected_keys:
            LOGGER.debug(
                "Injected required input set values into component '{component_name}': {keys}",
                component_name=component_name,
                keys=sorted(required_injected_keys),
            )

        injected_keys = []
        if mapping.auto_inject_inputs:
            for input_name, input_value in resolved_inputs.items():
                if input_name in component.properties:
                    continue

                property_spec = mapping.properties.get(input_name)
                if (
                    property_spec is not None
                    and property_spec.auto_inject_inputs is False
                ):
                    continue

                output_name = property_renderer.output_name(input_name, property_spec)

                if (
                    output_name not in discovered_vars
                    and input_name not in discovered_vars
                ):
                    continue

                if output_name in reserved_output_names:
                    continue

                output_name, module_block[output_name] = property_renderer.render(
                    input_name,
                    input_value,
                    property_spec,
                )
                injected_keys.append(input_name)
                reserved_output_names.add(output_name)

        if injected_keys:
            LOGGER.debug(
                "Auto-injected inputs into component '{component_name}': {keys}",
                component_name=component_name,
                keys=sorted(injected_keys),
            )

        for prop_name, prop_spec in mapping.properties.items():
            output_name = property_renderer.output_name(prop_name, prop_spec)
            if output_name in module_block or prop_spec.default is None:
                continue

            output_name, module_block[output_name] = property_renderer.render(
                prop_name,
                prop_spec.default,
                prop_spec,
                kind="module_property_default",
            )

        modules[component_name] = module_block

    return modules


def generate_tf_json(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    use_local_modules: bool = False,
    vendor_dir: Path | None = None,
    root: Path | None = None,
    formatter_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate the complete `.tf.json` structure for a stack.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_inputs: Resolved input values.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        vendor_dir: Root directory containing vendored modules.
        formatter_options: Optional formatter option mappings keyed by
            `module_source` and `provider_source`.

    Returns:
        Dict representing the entire `.tf.json` file content.
    """
    module_source_options = None
    provider_source_options = None
    if formatter_options is not None:
        module_source_options = formatter_options.get("module_source")
        provider_source_options = formatter_options.get("provider_source")

    modules = _generate_module_blocks(
        stack,
        config,
        resolved_inputs,
        cache_dir=cache_dir,
        auth_config=auth_config,
        use_local_modules=use_local_modules,
        vendor_dir=vendor_dir,
        module_source_formatter_options=module_source_options,
    )
    doc = {
        "terraform": _generate_terraform_block(
            config,
            stack,
            root,
            provider_source_formatter_options=provider_source_options,
        ),
        "module": modules,
    }
    output_blocks = build_stack_output_blocks(
        stack,
        config,
        cache_dir=cache_dir,
        auth_config=auth_config,
        vendor_dir=vendor_dir,
    )
    bridge_outputs = _component_bridge_outputs(
        stack,
        config,
        cache_dir,
        auth_config,
        vendor_dir,
    )
    if collisions := sorted(set(output_blocks) & set(bridge_outputs)):
        raise StacksmithConfigError(
            "Stack output names reserved for operation bridges: "
            f"{', '.join(collisions)}"
        )
    output_blocks.update(bridge_outputs)
    if output_blocks:
        doc["output"] = output_blocks

    providers = build_provider_blocks(
        config,
        context={"stack_name": stack.name, "inputs": resolved_inputs},
        base_path=(
            config.source_path.parent if config.source_path is not None else None
        ),
        cache_dir=cache_dir,
        auth_config=auth_config,
    )
    if providers:
        doc["provider"] = providers
    return doc


def _has_bridge_output_reference(value: Any) -> bool:
    """Walk *value* to check for operation bridge output references.

    This avoids serialising the entire structure to JSON just to run a regex.
    """
    if isinstance(value, str):
        return _OPERATION_BRIDGE_OUTPUT_PATTERN.search(value) is not None
    if isinstance(value, dict):
        return any(
            _has_bridge_output_reference(v) for v in value.values()
        )
    if isinstance(value, list):
        return any(_has_bridge_output_reference(item) for item in value)
    return False


def generate_operations_tf_json(
    stack: StackDefinition,
    config: ToolConfig,
    operation_names: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Generate an isolated Terraform document for stack operations.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        operation_names: Explicit operation names, or `None` for all operations.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        vendor_dir: Root directory containing vendored modules.
        root: Optional monorepo root used for state key derivation.

    Returns:
        Terraform JSON containing only operation runner modules and read-only
        infrastructure state access.
    """
    modules = _generate_operation_blocks(
        stack,
        config,
        operation_names,
        cache_dir=cache_dir,
        auth_config=auth_config,
        vendor_dir=vendor_dir,
    )
    doc: dict[str, Any] = {
        "terraform": _generate_operations_terraform_block(config, stack, root),
        "module": modules,
    }
    if _has_bridge_output_reference(modules):
        doc["data"] = _generate_operations_infrastructure_state_data(
            config,
            stack,
            root,
        )
    return doc


def write_tf_json(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    output_dir: Path,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    use_local_modules: bool = False,
    vendor_dir: Path | None = None,
    root: Path | None = None,
    formatter_options: Mapping[str, Mapping[str, Any]] | None = None,
) -> Path:
    """Generate and write `stacksmith.tf.json` to the output directory.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_inputs: Resolved input values.
        output_dir: Directory to write `stacksmith.tf.json` into.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        vendor_dir: Root directory containing vendored modules.
        formatter_options: Optional formatter option mappings keyed by
            `module_source` and `provider_source`.

    Returns:
        Path to the written `stacksmith.tf.json` file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tf_json = generate_tf_json(
        stack,
        config,
        resolved_inputs,
        cache_dir=cache_dir,
        auth_config=auth_config,
        use_local_modules=use_local_modules,
        vendor_dir=vendor_dir,
        root=root,
        formatter_options=formatter_options,
    )
    _write_operation_runner_assets(output_dir, tf_json)
    output_path = output_dir / GENERATED_TF_JSON
    output_path.write_text(json.dumps(tf_json, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote generated JSON: {path}", path=output_path)
    return output_path


def write_operations_tf_json(
    stack: StackDefinition,
    config: ToolConfig,
    output_dir: Path,
    operation_names: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
    root: Path | None = None,
) -> Path:
    """Generate and write an isolated operation Terraform document.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        output_dir: Directory to write `stacksmith.tf.json` into.
        operation_names: Explicit operation names, or `None` for all operations.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        vendor_dir: Root directory containing vendored modules.
        root: Optional monorepo root used for state key derivation.

    Returns:
        Path to the written `stacksmith.tf.json` file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tf_json = generate_operations_tf_json(
        stack,
        config,
        operation_names,
        cache_dir,
        auth_config,
        vendor_dir,
        root,
    )
    _write_operation_runner_assets(output_dir, tf_json)
    output_path = output_dir / GENERATED_TF_JSON
    output_path.write_text(json.dumps(tf_json, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote generated operation JSON: {path}", path=output_path)
    return output_path
