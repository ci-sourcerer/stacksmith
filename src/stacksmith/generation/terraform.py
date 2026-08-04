import json
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from ..constants import GENERATED_TF_JSON
from ..formatters import render_module_source_for
from ..introspection import discover_module_variables
from ..models import (
    LocalModuleSourceReference,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
    render_module_source_identity,
)
from ..module_mapping import resolve_module_mapping
from ..stack_outputs import build_stack_output_blocks
from ..utils import derive_stack_state_key, get_current_git_repository
from ..vendor import get_vendor_dir, resolve_module_source
from .operations import build_operation_module_spec, resolve_operation_batch
from .properties import PropertyRenderer
from .providers import (
    build_provider_blocks,
    build_required_providers,
    render_provider_reference,
)

_OPERATION_RUNNER_ASSETS = ("main.tf", "local.py", "jenkins.py")


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
        [
            name
            for name, invocation in stack.operations.items()
            if (definition := config.operations.get(invocation.use)) is not None
            and definition.trigger == "after_apply"
        ]
        if operation_names is None
        else list(operation_names)
    )
    if not selected_names:
        return modules

    for name in resolve_operation_batch(stack, selected_names):
        invocation = stack.operations[name]
        dependencies = [
            f"module.{component_name}" for component_name in stack.components
        ]
        dependencies.extend(
            f"module.{operation_module_name(dependency)}"
            for dependency in invocation.depends_on
        )
        modules[operation_module_name(name)] = {
            "source": "./.stacksmith-operation-runner",
            "spec": build_operation_module_spec(
                stack,
                config,
                name,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir,
            ),
            **({"depends_on": dependencies} if dependencies else {}),
        }
    return modules


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
        LOGGER.info(
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
        for prop_name, prop_value in component.properties.items():
            property_spec = mapping.properties.get(prop_name)
            output_name, module_block[output_name] = property_renderer.render(
                prop_name,
                prop_value,
                property_spec,
            )

        injected_keys = []
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

            reserved_output_names = set(module_block)
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
    operation_names: Sequence[str] | None = None,
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
    modules.update(
        _generate_operation_blocks(
            stack,
            config,
            operation_names,
            cache_dir=cache_dir,
            auth_config=auth_config,
            vendor_dir=vendor_dir,
        )
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
    if output_blocks := build_stack_output_blocks(
        stack,
        config,
        cache_dir=cache_dir,
        auth_config=auth_config,
        vendor_dir=vendor_dir,
    ):
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
    operation_names: Sequence[str] | None = None,
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
        operation_names=operation_names,
    )
    _write_operation_runner_assets(output_dir, tf_json)
    output_path = output_dir / GENERATED_TF_JSON
    output_path.write_text(json.dumps(tf_json, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote generated JSON: {path}", path=output_path)
    return output_path
