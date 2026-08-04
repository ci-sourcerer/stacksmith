import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .exceptions import StacksmithConfigError
from .formatters import compact_json
from .introspection import discover_module_outputs, discover_module_variables
from .models import (
    FileReference,
    ModuleMapping,
    ModuleOutputSpec,
    ModulePropertySpec,
    RemoteAuthConfig,
    ToolConfig,
    TransformSpec,
    ValidationSpec,
    render_file_reference,
    render_module_source_identity,
)
from .module_mapping import auto_exposed_output_names, resolve_module_mapping
from .remote import is_remote_url
from .vendor import get_vendor_dir

LOGGER = logging.getLogger(__name__)


@dataclass
class InputInfo:
    """Metadata for a single module input."""

    name: str
    module_variable: str
    description: str | None = None
    mapped_to: str | None = None
    auto_inject_inputs: bool = False
    validation: str | None = None
    validation_description: str | None = None
    validation_source: str | None = None
    transform: str | None = None
    transform_description: str | None = None
    transform_source: str | None = None
    note: str | None = None


@dataclass
class OutputInfo:
    """Metadata for a public component output."""

    name: str
    module_output: str
    description: str | None = None
    transform: str | None = None
    transform_description: str | None = None
    transform_source: str | None = None
    auto_exposed: bool = False
    note: str | None = None


@dataclass
class ComponentTypeInfo:
    """Inspection result for a single component type."""

    component_type: str
    display_name: str
    module_source: str
    module_version: str
    auto_inject_inputs: bool
    auto_expose_outputs: bool = False
    tags: list[str] = field(default_factory=list)
    inputs: list[InputInfo] = field(default_factory=list)
    outputs: list[OutputInfo] = field(default_factory=list)


@dataclass
class PlanPolicyInfo:
    """Inspection result for a single plan-level policy validation."""

    name: str
    description: str
    location: str
    rule_source: str | None = None
    enabled: bool = True


def _format_script_location(
    script: FileReference | str, config: ToolConfig | None
) -> str:
    rendered = render_file_reference(script)
    if is_remote_url(script):
        return rendered
    if config is None or config.source_path is None:
        return rendered
    try:
        return str(Path(rendered).relative_to(config.source_path.parent))
    except Exception:  # noqa: BLE001
        LOGGER.debug(
            "Could not relativize script path {script} to config source {config}",
            script=script,
            config=config.source_path,
        )
        return rendered


def _describe_location(
    spec: ValidationSpec | TransformSpec | None,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    location_path: tuple[str, ...],
    inline_field: str = "inline",
) -> str | None:
    if spec is None:
        return None
    if getattr(spec, inline_field) is not None:
        if config_locations is not None:
            return config_locations.get(location_path, "inline")
        return "inline"
    script = getattr(spec, "script", None)
    if script is not None:
        return _format_script_location(script, config)
    return None


def _describe_spec_location(
    spec: ValidationSpec | None,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    location_path: tuple[str, ...],
) -> str | None:
    return _describe_location(
        spec,
        config,
        config_locations,
        location_path,
        inline_field="inline",
    )


def _describe_transform_location(
    spec: TransformSpec | None,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    location_path: tuple[str, ...],
) -> str | None:
    return _describe_location(
        spec,
        config,
        config_locations,
        location_path,
        inline_field="jinja",
    )


def _describe_script_reference(
    spec: ValidationSpec | TransformSpec | None,
) -> str | None:
    if spec is None:
        return None
    if spec.script is None:
        return None
    return render_file_reference(spec.script)


def _resolve_var_validation_location(
    var_name: str,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    location_path: tuple[str, ...],
) -> str | None:
    if config is None or config_locations is None:
        return None
    var_validation = config.var_validations.get(var_name)
    if var_validation is None:
        return None
    return _describe_spec_location(
        var_validation,
        config,
        config_locations,
        location_path,
    )


def _resolve_var_validation(
    var_name: str, config: ToolConfig | None
) -> ValidationSpec | None:
    if config is None:
        return None
    return config.var_validations.get(var_name)


def _build_property_input_info(
    property_name: str,
    property_spec: ModulePropertySpec,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    mapping_location: tuple[str, ...],
) -> InputInfo:
    validation_spec = property_spec.validation
    if validation_spec is None:
        validation_spec = _resolve_var_validation(property_name, config)
        validation_location = _resolve_var_validation_location(
            property_name,
            config,
            config_locations,
            ("var_validations", property_name),
        )
    else:
        validation_location = _describe_spec_location(
            validation_spec,
            config,
            config_locations,
            (*mapping_location, "properties", property_name, "validation"),
        )

    return _build_input_info(
        property_name,
        property_spec,
        description=property_spec.description,
        validation_location=validation_location,
        validation_description=(
            validation_spec.description if validation_spec is not None else None
        ),
        validation_source=_describe_script_reference(validation_spec),
        transform_location=_describe_transform_location(
            property_spec.transform,
            config,
            config_locations,
            (*mapping_location, "properties", property_name, "transform"),
        ),
        transform_description=(
            property_spec.transform.description
            if property_spec.transform is not None
            else None
        ),
        transform_source=_describe_script_reference(property_spec.transform),
        is_auto_inject_inputsed=False,
    )


def _build_output_info(
    name: str,
    specification: ModuleOutputSpec,
    component_type: str,
    config: ToolConfig | None,
    config_locations: dict[tuple[str, ...], str] | None,
    mapping_location: tuple[str, ...] | None,
) -> OutputInfo:
    return OutputInfo(
        name=name,
        module_output=specification.mapped_from or name,
        description=specification.description,
        transform=_describe_transform_location(
            specification.transform,
            config,
            config_locations,
            (
                *(mapping_location or ("module_mappings", component_type)),
                "outputs",
                name,
                "transform",
            ),
        ),
        transform_description=(
            specification.transform.description
            if specification.transform is not None
            else None
        ),
        transform_source=_describe_script_reference(specification.transform),
    )


def inspect_component_type(
    component_type: str,
    mapping: ModuleMapping,
    config: ToolConfig | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
    config_locations: dict[tuple[str, ...], str] | None = None,
    mapping_location: tuple[str, ...] | None = None,
) -> ComponentTypeInfo:
    """Inspect a single configured component type.

    Discovers the module's declared interface and merges it with managed input
    and output specifications.

    Args:
        component_type: The abstract component type name (e.g. `aws_s3_bucket`).
        mapping: The module mapping from the tool config.
        cache_dir: Cache directory for fetching remote modules.
        auth_config: Optional host-keyed auth configuration.
        vendor_dir: Vendored module root directory.
        mapping_location: Optional config location path for mapping metadata.

    Returns:
        An `ComponentTypeInfo` containing input metadata for the module.

    Raises:
        StacksmithConfigError: If the module cannot be introspected.
    """
    mapping_source, mapping_version = render_module_source_identity(
        mapping.source,
        options={
            "base_path": (
                config.source_path.parent
                if config is not None and config.source_path is not None
                else None
            )
        },
    )
    try:
        discovered_vars = discover_module_variables(
            mapping_source,
            mapping_version,
            cache_dir=cache_dir,
            auth_config=auth_config,
            vendor_dir=vendor_dir or get_vendor_dir(),
        )
        discovered_outputs = (
            discover_module_outputs(
                mapping_source,
                mapping_version,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir or get_vendor_dir(),
            )
            if mapping.auto_expose_outputs
            else set()
        )
    except (OSError, RuntimeError, StacksmithConfigError) as exc:
        raise StacksmithConfigError(
            f"Could not introspect module for {component_type}: {exc}"
        ) from exc

    inputs = []

    # 1. Inputs explicitly configured in property specs
    seen_vars = set()
    for prop_name, prop_spec in mapping.properties.items():
        module_var = prop_spec.mapped_to or prop_name
        seen_vars.add(module_var)
        seen_vars.add(prop_name)
        inputs.append(
            _build_property_input_info(
                prop_name,
                prop_spec,
                config,
                config_locations,
                mapping_location or ("modules", component_type),
            )
        )

    # 2. Module variables not covered by property specs
    for var_name in sorted(discovered_vars - seen_vars):
        validation_spec = _resolve_var_validation(var_name, config)
        validation_location = _resolve_var_validation_location(
            var_name,
            config,
            config_locations,
            ("var_validations", var_name),
        )
        note = None if validation_location else "discovered via introspection"
        inputs.append(
            InputInfo(
                name=var_name,
                module_variable=var_name,
                auto_inject_inputs=mapping.auto_inject_inputs,
                validation=validation_location,
                validation_description=(
                    validation_spec.description if validation_spec is not None else None
                ),
                note=note,
            )
        )

    outputs = [
        _build_output_info(
            name,
            specification,
            component_type,
            config,
            config_locations,
            mapping_location,
        )
        for name, specification in sorted(mapping.outputs.items())
    ]
    outputs.extend(
        OutputInfo(
            name=name,
            module_output=name,
            auto_exposed=True,
            note="discovered via introspection",
        )
        for name in sorted(auto_exposed_output_names(mapping, discovered_outputs))
    )

    return ComponentTypeInfo(
        component_type=component_type,
        display_name=mapping.description or component_type,
        module_source=mapping_source,
        module_version=mapping_version,
        auto_inject_inputs=mapping.auto_inject_inputs,
        auto_expose_outputs=mapping.auto_expose_outputs,
        tags=sorted(mapping.tags),
        inputs=inputs,
        outputs=outputs,
    )


def _build_input_info(
    var_name: str,
    property_spec: ModulePropertySpec | None,
    description: str | None = None,
    validation_location: str | None = None,
    validation_description: str | None = None,
    validation_source: str | None = None,
    transform_location: str | None = None,
    transform_description: str | None = None,
    transform_source: str | None = None,
    is_auto_inject_inputsed: bool = False,
) -> InputInfo:
    mapped_to = property_spec.mapped_to if property_spec else None
    return InputInfo(
        name=var_name,
        module_variable=mapped_to or var_name,
        description=description,
        mapped_to=mapped_to,
        auto_inject_inputs=is_auto_inject_inputsed
        or (property_spec.auto_inject_inputs is not False if property_spec else False),
        validation=validation_location,
        validation_description=validation_description,
        validation_source=validation_source,
        transform=transform_location,
        transform_description=transform_description,
        transform_source=transform_source,
    )


def inspect_all(
    config: ToolConfig,
    component_types: list[str] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
    config_locations: dict[tuple[str, ...], str] | None = None,
) -> list[ComponentTypeInfo]:
    """Inspect one or more configured resource types.

    Args:
        config: Loaded tool configuration.
        component_types: Specific resource type(s) to inspect. Inspects all when `None`.
        cache_dir: Cache directory for fetching remote modules.
        auth_config: Optional host-keyed auth configuration.
        vendor_dir: Vendored module root directory.

    Returns:
        List of `ComponentTypeInfo` results, one per component type.
    """
    targets = (
        component_types if component_types else sorted(config.module_mappings.keys())
    )

    results = []
    for t in targets:
        results.append(
            inspect_component_type(
                t,
                resolve_module_mapping(config, t),
                config,
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir,
                config_locations=config_locations,
                mapping_location=(
                    ("module_mappings", t)
                    if t in config.module_mappings
                    else ("default_module_mapping",)
                ),
            )
        )
    return results


def inspect_plan_validations(
    config: ToolConfig, config_locations: dict[tuple[str, ...], str] | None = None
) -> list[PlanPolicyInfo]:
    """Inspect plan-level validations and return policy metadata.

    Args:
        config: Loaded tool configuration.
        config_locations: Optional mapping of config location paths to human-readable descriptions.

    Returns:
        List of `PlanPolicyInfo` results, one per plan-level validation.
    """
    policies: list[PlanPolicyInfo] = []
    for name, plan_validation in sorted(config.plan_validations.items()):
        location = (
            _describe_spec_location(
                plan_validation.rule,
                config,
                config_locations,
                ("plan_validations", name, "rule"),
            )
            or ""
        )
        policies.append(
            PlanPolicyInfo(
                name=name,
                description=plan_validation.description or "",
                location=location,
                rule_source=plan_validation.rule.script,
                enabled=plan_validation.enabled,
            )
        )
    return policies


def format_json(results: list[ComponentTypeInfo], details: bool = True) -> str:
    """Serialize inspection results to JSON.

    Args:
        results: Inspection results.
        details: Include validation/transform metadata in output.

    Returns:
        JSON string.
    """
    output = {}
    for info in results:
        inputs_list = []
        for inp in info.inputs:
            entry = {
                "name": inp.name,
                "module_variable": inp.module_variable,
            }
            if inp.description:
                entry["description"] = inp.description
            if inp.mapped_to:
                entry["mapped_to"] = inp.mapped_to
            entry["auto_inject_inputs"] = inp.auto_inject_inputs
            if details:
                if inp.validation:
                    entry["validation"] = inp.validation
                if inp.validation_description:
                    entry["validation_description"] = inp.validation_description
                if inp.transform:
                    entry["transform"] = inp.transform
                if inp.transform_description:
                    entry["transform_description"] = inp.transform_description
            inputs_list.append(entry)
        resource_entry = {
            "module_source": info.module_source,
            "module_version": info.module_version,
            "display_name": info.display_name,
            "auto_inject_inputs": info.auto_inject_inputs,
            "auto_expose_outputs": info.auto_expose_outputs,
        }
        if info.tags:
            resource_entry["tags"] = info.tags
        resource_entry["inputs"] = inputs_list
        if info.outputs:
            resource_entry["outputs"] = [
                {
                    "name": output.name,
                    **(
                        {"description": output.description}
                        if output.description
                        else {}
                    ),
                    "auto_exposed": output.auto_exposed,
                    **({"module_output": output.module_output} if details else {}),
                    **(
                        {"transform": output.transform}
                        if details and output.transform
                        else {}
                    ),
                    **(
                        {"transform_description": output.transform_description}
                        if details and output.transform_description
                        else {}
                    ),
                    **({"note": output.note} if details and output.note else {}),
                }
                for output in info.outputs
            ]
        output[info.component_type] = resource_entry
    return compact_json(output, sort_keys=True)


def format_yaml(results: list[ComponentTypeInfo], details: bool = True) -> str:
    """Serialize inspection results to YAML.

    Args:
        results: Inspection results.
        details: Include validation/transform metadata in output.

    Returns:
        YAML string.
    """
    data = json.loads(format_json(results, details=details))
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def format_table(
    results: list[ComponentTypeInfo],
    details: bool = True,
    basic: bool = False,
    plan_validations: list[PlanPolicyInfo] | None = None,
) -> None:
    """Print inspection results as a rich table to stderr.

    Args:
        results: Inspection results.
        details: Include validation/transform columns.
    """
    from rich.console import Console
    from rich.table import Table

    console = Console(stderr=True)

    for info in results:
        console.print()
        title = info.display_name
        if info.display_name != info.component_type:
            title = f"{info.display_name} [dim]({info.component_type})[/dim]"
        console.print(
            f"[bold]{title}[/bold]  "
            f"[dim]{info.module_source} @ {info.module_version}[/dim]"
        )
        if info.tags:
            console.print("  [magenta]Tags:[/magenta] " + ", ".join(info.tags))
        if info.auto_inject_inputs:
            console.print("  [green]auto_inject_inputs: enabled[/green]")
        if info.auto_expose_outputs:
            console.print("  [green]auto_expose_outputs: enabled[/green]")

        table = Table(
            show_header=True,
            show_lines=True,
            header_style="bold cyan",
            padding=(0, 1),
        )
        if basic:
            table.add_column("Input")
            table.add_column("Validation")
            table.add_column("Transform")
        else:
            table.add_column("Input")
            table.add_column("Description")
            table.add_column("Mapped To")
            table.add_column("Auto-Inject Inputs")
            if details:
                table.add_column("Validation")
                table.add_column("Transform")

        for inp in info.inputs:
            if basic:
                row = [
                    inp.name,
                    _format_rule_metadata(
                        inp.validation_description,
                        inp.validation,
                    ),
                    _format_rule_metadata(
                        inp.transform_description,
                        inp.transform,
                    ),
                ]
            else:
                row = [
                    inp.name,
                    inp.description or "",
                    inp.mapped_to or "",
                    "yes" if inp.auto_inject_inputs else "",
                ]
                if details:
                    row.append(
                        _format_rule_metadata(
                            inp.validation_description,
                            inp.validation,
                        )
                    )
                    row.append(
                        _format_rule_metadata(
                            inp.transform_description,
                            inp.transform,
                        )
                    )
            table.add_row(*row)

        console.print(table)

        if info.outputs:
            output_table = Table(
                show_header=True,
                show_lines=True,
                header_style="bold green",
                padding=(0, 1),
            )
            output_table.add_column("Output")
            output_table.add_column("Description")
            if not basic:
                output_table.add_column("Mapped From")
                output_table.add_column("Auto-Expose Outputs")
            if details or basic:
                output_table.add_column("Transform")
            for output in info.outputs:
                row = [output.name, output.description or ""]
                if not basic:
                    row.append(output.module_output)
                    row.append("yes" if output.auto_exposed else "no")
                if details or basic:
                    row.append(
                        _format_rule_metadata(
                            output.transform_description,
                            output.transform,
                        )
                    )
                output_table.add_row(*row)
            console.print(output_table)

    if plan_validations and not basic:
        console.print()
        console.print("[bold]Plan Policies[/bold]")
        policy_table = Table(
            show_header=True,
            show_lines=True,
            header_style="bold cyan",
            padding=(0, 1),
        )
        policy_table.add_column("Policy")
        policy_table.add_column("Description")
        policy_table.add_column("Location")

        for policy in plan_validations:
            policy_table.add_row(
                policy.name,
                policy.description,
                policy.location,
            )
        console.print(policy_table)


def _format_rule_metadata(description: str | None, location: str | None) -> str:
    if description and location:
        return f"{description}\n[dim]{location}[/dim]"
    return description or location or ""
