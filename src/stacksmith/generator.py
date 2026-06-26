import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jinja2.sandbox import SandboxedEnvironment
from loguru import logger as LOGGER

from .exceptions import (
    StacksmithConfigError,
    StacksmithTransformError,
    StacksmithValidationError,
)
from .formatters import render_module_source_for
from .generation.providers import (
    _build_provider_blocks,
    _build_required_providers,
    _render_provider_reference,
)
from .introspection import discover_module_variables
from .models import (
    LocalModuleSourceReference,
    ModulePropertySpec,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
    render_module_source_identity,
)
from .utils import derive_stack_state_key, render_jinja_template_values
from .validation import InputValidationOutcome, apply_transform, validate_value
from .vendor import get_vendor_dir, resolve_module_source

_JINJA_ENV = SandboxedEnvironment()


def _render_property_value(value: Any, context: dict[str, Any]) -> Any:
    return render_jinja_template_values(value, context, jinja_env=_JINJA_ENV)


def _looks_like_module_path_input(name: str) -> bool:
    return name.endswith("_files") or name in {"cwd"}


def _resolve_module_input_path(value: str, base_paths: list[Path]) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)

    for base_path in base_paths:
        candidate = (base_path / path).resolve()
        if candidate.exists():
            return str(candidate)

    return str((Path.cwd() / path).resolve())


def _normalize_module_input_value(
    name: str,
    value: Any,
    base_paths: list[Path],
) -> Any:
    if not _looks_like_module_path_input(name):
        return value

    if isinstance(value, str):
        return _resolve_module_input_path(value, base_paths)

    if isinstance(value, list):
        return [
            (
                _resolve_module_input_path(item, base_paths)
                if isinstance(item, str)
                else item
            )
            for item in value
        ]

    return value


def _render_transform_jinja(template: str, value: Any, context: dict[str, Any]) -> Any:
    rendered = _JINJA_ENV.from_string(template).render({"value": value, **context})
    try:
        return json.loads(rendered)
    except json.JSONDecodeError:
        return rendered


def _stack_context(stack: StackDefinition) -> dict[str, Any]:
    return {
        "name": stack.name,
        "tags": sorted(stack.tags),
    }


def _generate_terraform_block(
    config: ToolConfig,
    stack: StackDefinition,
    root: Path | None = None,
    *,
    provider_source_formatter_options: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state_key = derive_stack_state_key(stack.name, stack.source_path, root)
    return {
        "required_version": f"= {config.tools.tofu.version}",
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
            raise StacksmithTransformError(
                f"Component '{property_context['component_name']}' property '{property_context['name']}' transform {exc}"
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
            raise StacksmithValidationError(
                f"Component '{property_context['component_name']}' property '{property_context['name']}': {error_msg}"
            )

    return rendered


def _build_property_context(
    *,
    name: str,
    kind: str,
    component_name: str,
    component_type: str,
    output_name: str,
    inputs: dict[str, Any] | None = None,
    stack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "name": name,
        "kind": kind,
        "component_name": component_name,
        "component_type": component_type,
        "output_name": output_name,
    }
    if inputs is not None:
        context["inputs"] = inputs
    if stack is not None:
        context["stack"] = stack
    return context


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
    # Provide both `inputs` and a lightweight `stack` object to Jinja so
    # templates and transforms can access stack metadata like `stack.name`
    # and `stack.tags`.
    context = {
        "inputs": resolved_inputs,
        "stack": _stack_context(stack),
    }
    modules = {}

    path_bases: list[Path] = []
    if stack.source_path is not None:
        path_bases.append(stack.source_path.parent)
    if config.source_path is not None:
        path_bases.append(config.source_path.parent)
    path_bases.append(Path.cwd())

    vendor_dir = vendor_dir or get_vendor_dir()

    for component_name, component in stack.components.items():
        mapping = config.module_mappings.get(component.type)
        if mapping is None:
            raise StacksmithConfigError(
                f"Component '{component_name}' has type '{component.type}' "
                f"which is not defined in the tool configuration module mappings. "
                f"Available types: {', '.join(config.module_mappings.keys())}"
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
        if mapping.auto_inject:
            LOGGER.debug(
                "Module mapping for component '{component_name}' has auto_inject enabled",
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
                        options=source_options,
                    )
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
                module_provider_name: _render_provider_reference(
                    config,
                    provider_reference,
                )
                for module_provider_name, provider_reference in mapping.providers.items()
            }

        for prop_name, prop_value in component.properties.items():
            rendered = _render_property_value(prop_value, context)
            property_spec = mapping.properties.get(prop_name)
            output_name = (
                property_spec.mapped_to
                if property_spec and property_spec.mapped_to
                else prop_name
            )
            if output_name != prop_name:
                LOGGER.debug(
                    "Component '{component_name}' property '{prop_name}' is mapped to module input '{output_name}'",
                    component_name=component_name,
                    prop_name=prop_name,
                    output_name=output_name,
                )
            property_context = _build_property_context(
                name=prop_name,
                kind="component_property",
                component_name=component_name,
                component_type=component.type,
                output_name=output_name,
                inputs=resolved_inputs,
                stack=_stack_context(stack),
            )

            rendered = _apply_property_spec(
                rendered,
                property_spec,
                property_context,
                config,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
            rendered = _normalize_module_input_value(output_name, rendered, path_bases)
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
                "Module '{component_type}' declares variables: {vars}",
                component_type=component.type,
                vars=sorted(discovered_vars),
            )

            reserved_output_names = set(module_block)
            for input_name, input_value in resolved_inputs.items():
                if input_name in component.properties:
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
                    kind="component_property",
                    component_name=component_name,
                    component_type=component.type,
                    output_name=output_name,
                    inputs=resolved_inputs,
                    stack=_stack_context(stack),
                )

                rendered = _apply_property_spec(
                    rendered,
                    property_spec,
                    property_context,
                    config,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                )
                rendered = _normalize_module_input_value(
                    output_name, rendered, path_bases
                )
                module_block[output_name] = rendered
                injected_keys.append(input_name)
                reserved_output_names.add(output_name)

        if injected_keys:
            LOGGER.debug(
                "Auto-injected inputs into component '{component_name}': {keys}",
                component_name=component_name,
                keys=sorted(injected_keys),
            )

        for prop_name, prop_spec in mapping.properties.items():
            output_name = prop_spec.mapped_to if prop_spec.mapped_to else prop_name
            if output_name in module_block or prop_spec.default is None:
                continue

            property_context = _build_property_context(
                name=prop_name,
                kind="module_property_default",
                component_name=component_name,
                component_type=component.type,
                output_name=output_name,
                inputs=resolved_inputs,
                stack=_stack_context(stack),
            )
            module_block[output_name] = _apply_property_spec(
                prop_spec.default,
                prop_spec,
                property_context,
                config,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
            module_block[output_name] = _normalize_module_input_value(
                output_name,
                module_block[output_name],
                path_bases,
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
    output_path = output_dir / "stacksmith.tf.json"
    output_path.write_text(json.dumps(tf_json, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote generated JSON: {path}", path=output_path)
    return output_path
