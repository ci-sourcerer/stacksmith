import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2.sandbox import SandboxedEnvironment
from loguru import logger as LOGGER

from .formatters import render_module_source_for
from .generation.providers import (
    _build_provider_blocks,
    _build_required_providers,
    _render_provider_reference,
)
from .introspection import discover_module_variables
from .models import (
    ModulePropertySpec,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
    render_module_source_identity,
)
from .utils import derive_stack_state_key
from .validation import InputValidationOutcome, apply_transform, validate_value
from .vendor import get_vendor_dir, resolve_module_source

_JINJA_ENV = SandboxedEnvironment()


def _render_property_value(value: Any, context: dict[str, Any]) -> Any:
    match value:
        case str() if "{{" in value:
            return _JINJA_ENV.from_string(value).render(context)
        case dict():
            return {k: _render_property_value(v, context) for k, v in value.items()}
        case list():
            return [_render_property_value(item, context) for item in value]
        case _:
            return value


def _render_transform_jinja(template: str, value: Any, context: dict[str, Any]) -> Any:
    rendered = _JINJA_ENV.from_string(template).render({"value": value, **context})
    try:
        return json.loads(rendered)
    except json.JSONDecodeError:
        return rendered


def _generate_terraform_block(
    config: ToolConfig,
    stack: StackDefinition,
    root: Path | None = None,
    *,
    provider_source_formatter_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_key = derive_stack_state_key(stack.name, stack.source_path, root)
    return {
        "required_version": f"= {config.tofu.version}",
        "backend": {
            config.backend.type: config.backend.config_with_state_key(state_key)
        },
        "required_providers": _build_required_providers(
            config,
            formatter_options=provider_source_formatter_options,
        ),
    }


def _apply_property_spec(
    value: Any,
    property_spec: ModulePropertySpec | None,
    property_context: dict[str, Any],
    config: ToolConfig,
    *,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> Any:
    rendered = value
    if property_spec is None:
        return rendered

    transform_spec = property_spec.transform
    if transform_spec is not None:
        try:
            if transform_spec.jinja is not None:
                rendered = _render_transform_jinja(
                    transform_spec.jinja,
                    rendered,
                    property_context,
                )
            else:
                rendered = apply_transform(
                    transform_spec,
                    rendered,
                    base_path=(
                        config.source_path.parent
                        if config.source_path is not None
                        else None
                    ),
                    context=property_context,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                )
        except Exception as exc:
            raise ValueError(
                f"Resource '{property_context['resource_name']}' property '{property_context['name']}' transform error: {exc}"
            ) from exc

    validation_code = property_spec.validation
    if validation_code is not None:
        outcome, error_msg = validate_value(
            validation_code,
            rendered,
            base_path=(
                config.source_path.parent if config.source_path is not None else None
            ),
            context=property_context,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )
        if outcome != InputValidationOutcome.PASS:
            raise ValueError(
                f"Resource '{property_context['resource_name']}' property '{property_context['name']}': {error_msg}"
            )

    return rendered


def _build_property_context(
    *,
    name: str,
    kind: str,
    resource_name: str,
    resource_type: str,
    output_name: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "resource_name": resource_name,
        "resource_type": resource_type,
        "output_name": output_name,
        "inputs": inputs,
    }


def _generate_module_blocks(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    use_local_modules: bool = False,
    vendor_dir: Path | None = None,
    *,
    module_source_formatter_options: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    context = {"inputs": resolved_inputs}
    modules: dict[str, dict[str, Any]] = {}

    vendor_dir = vendor_dir or get_vendor_dir()

    for resource_name, resource in stack.components.items():
        mapping = config.module_mappings.get(resource.type)
        if mapping is None:
            raise ValueError(
                f"Component '{resource_name}' has type '{resource.type}' "
                f"which is not defined in the tool configuration module mappings. "
                f"Available types: {', '.join(config.module_mappings.keys())}"
            )

        mapping_source, mapping_version = render_module_source_identity(mapping.source)
        LOGGER.info(
            "Generating module block for component '{resource_name}' of type '{resource_type}' using module {source}@{version}",
            resource_name=resource_name,
            resource_type=resource.type,
            source=mapping_source,
            version=mapping_version,
        )
        if mapping.auto_inject:
            LOGGER.debug(
                "Module mapping for component '{resource_name}' has auto_inject enabled",
            )

        module_block: dict[str, Any] = {}
        if use_local_modules:
            try:
                module_block["source"] = resolve_module_source(
                    mapping_source,
                    mapping_version,
                    vendor_dir=vendor_dir,
                )
            except FileNotFoundError:
                LOGGER.debug(
                    "Vendored module not found for %s@%s; falling back to remote source",
                    mapping_source,
                    mapping_version,
                )
                module_block.update(
                    render_module_source_for(
                        "terraform",
                        mapping.source,
                        options=module_source_formatter_options,
                    )
                )
        else:
            module_block.update(
                render_module_source_for(
                    "terraform",
                    mapping.source,
                    options=module_source_formatter_options,
                )
            )

        if mapping.providers:
            module_block["providers"] = {
                module_provider_name: _render_provider_reference(
                    config,
                    provider_reference,
                )
                for module_provider_name, provider_reference in mapping.providers.items()
            }

        for prop_name, prop_value in resource.properties.items():
            rendered = _render_property_value(prop_value, context)
            property_spec = mapping.properties.get(prop_name)
            output_name = (
                property_spec.mapped_to
                if property_spec and property_spec.mapped_to
                else prop_name
            )
            if output_name != prop_name:
                LOGGER.debug(
                    "Component '{resource_name}' property '{prop_name}' is mapped to module input '{output_name}'",
                    resource_name=resource_name,
                    prop_name=prop_name,
                    output_name=output_name,
                )
            property_context = _build_property_context(
                name=prop_name,
                kind="resource_property",
                resource_name=resource_name,
                resource_type=resource.type,
                output_name=output_name,
                inputs=resolved_inputs,
            )

            rendered = _apply_property_spec(
                rendered,
                property_spec,
                property_context,
                config,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
            module_block[output_name] = rendered

        injected_keys = []
        if mapping.auto_inject:
            discovered_vars = discover_module_variables(
                mapping_source,
                mapping_version,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir if use_local_modules else None,
            )
            LOGGER.debug(
                "Module '{resource_type}' declares variables: {vars}",
                resource_type=resource.type,
                vars=sorted(discovered_vars),
            )

            reserved_output_names = set(module_block)
            for input_name, input_value in resolved_inputs.items():
                if input_name in resource.properties:
                    continue

                property_spec = mapping.properties.get(input_name)
                if property_spec is not None and property_spec.auto_inject is False:
                    continue

                output_name = (
                    property_spec.mapped_to
                    if property_spec and property_spec.mapped_to
                    else input_name
                )

                if (
                    output_name not in discovered_vars
                    and input_name not in discovered_vars
                ):
                    continue

                if output_name in reserved_output_names:
                    continue

                rendered = input_value
                property_context = _build_property_context(
                    name=input_name,
                    kind="resource_property",
                    resource_name=resource_name,
                    resource_type=resource.type,
                    output_name=output_name,
                    inputs=resolved_inputs,
                )

                rendered = _apply_property_spec(
                    rendered,
                    property_spec,
                    property_context,
                    config,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                )
                module_block[output_name] = rendered
                injected_keys.append(input_name)
                reserved_output_names.add(output_name)

        if injected_keys:
            LOGGER.debug(
                "Auto-injected inputs into component '{resource_name}': {keys}",
                resource_name=resource_name,
                keys=sorted(injected_keys),
            )

        for prop_name, prop_spec in mapping.properties.items():
            output_name = prop_spec.mapped_to if prop_spec.mapped_to else prop_name
            if output_name in module_block or prop_spec.default is None:
                continue

            property_context = _build_property_context(
                name=prop_name,
                kind="module_property_default",
                resource_name=resource_name,
                resource_type=resource.type,
                output_name=output_name,
                inputs=resolved_inputs,
            )
            module_block[output_name] = _apply_property_spec(
                prop_spec.default,
                prop_spec,
                property_context,
                config,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )

        modules[resource_name] = module_block

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
    """Generate the complete .tf.json structure for a stack.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_inputs: Resolved input values.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        use_local_modules: When True, rewrite module sources to local vendored paths.
        vendor_dir: Root directory containing vendored modules.
        formatter_options: Optional formatter option mappings keyed by
            `module_source` and `provider_source`.

    Returns:
        Dict representing the entire .tf.json file content.
    """
    module_source_options = None
    provider_source_options = None
    if formatter_options is not None:
        module_source_options = formatter_options.get("module_source")
        provider_source_options = formatter_options.get("provider_source")

    return {
        "terraform": _generate_terraform_block(
            config,
            stack,
            root,
            provider_source_formatter_options=provider_source_options,
        ),
        "provider": _build_provider_blocks(
            config,
            context={"stack_name": stack.name, "inputs": resolved_inputs},
            base_path=(
                config.source_path.parent if config.source_path is not None else None
            ),
            cache_dir=cache_dir,
            auth_config=auth_config,
        ),
        "module": _generate_module_blocks(
            stack,
            config,
            resolved_inputs,
            cache_dir=cache_dir,
            auth_config=auth_config,
            use_local_modules=use_local_modules,
            vendor_dir=vendor_dir,
            module_source_formatter_options=module_source_options,
        ),
    }


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
    """Generate and write main.tf.json to the output directory.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_inputs: Resolved input values.
        output_dir: Directory to write main.tf.json into.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        use_local_modules: When True, rewrite module sources to local vendored paths.
        vendor_dir: Root directory containing vendored modules.
        formatter_options: Optional formatter option mappings keyed by
            `module_source` and `provider_source`.

    Returns:
        Path to the written main.tf.json file.
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
    output_path = output_dir / "main.tf.json"
    output_path.write_text(json.dumps(tf_json, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote generated OpenTofu JSON: {path}", path=output_path)
    return output_path
