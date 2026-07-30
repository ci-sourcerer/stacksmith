import re
from pathlib import Path
from typing import Any

from jinja2 import TemplateError
from pydantic import TypeAdapter, ValidationError

from .exceptions import StacksmithConfigError
from .models import ModuleMapping, ModuleSourceReference, ToolConfig
from .templating import (
    create_sandboxed_jinja_environment,
    render_jinja_template_values,
)
from .utils import get_current_git_repository

_JINJA_ENV = create_sandboxed_jinja_environment()
_MODULE_SOURCE_ADAPTER = TypeAdapter(ModuleSourceReference)
_AUTO_EXPOSED_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def auto_exposed_output_names(
    mapping: ModuleMapping,
    discovered_outputs: set[str],
) -> set[str]:
    """Return discovered module outputs exposed by a mapping.

    Explicit public output mappings take precedence and claim their underlying
    module output names, preventing those implementation names from also being
    exposed automatically.

    Args:
        mapping: Resolved module mapping.
        discovered_outputs: Output names discovered from the underlying module.

    Returns:
        Same-name module outputs available through automatic exposure.
    """
    if not mapping.auto_expose_outputs:
        return set()
    exposed_outputs = {
        name
        for name in discovered_outputs
        if _AUTO_EXPOSED_OUTPUT_NAME_RE.fullmatch(name)
    }
    for public_name, specification in mapping.outputs.items():
        exposed_outputs.discard(public_name)
        exposed_outputs.discard(specification.mapped_from or public_name)
    return exposed_outputs


def _mapping_context(
    component_type: str, component_name: str | None, repository_path: Path | None = None
) -> dict[str, Any]:
    context = {
        "component": {
            "name": component_name or component_type,
            "type": component_type,
        }
    }
    if repository := get_current_git_repository(repository_path):
        context["env"] = {"git_repository": repository}
    return context


def _mapping_label(component_type: str, component_name: str | None) -> str:
    if component_name is None:
        return f"component type '{component_type}'"
    return f"component '{component_name}' of type '{component_type}'"


def _render_default_mapping(
    config: ToolConfig,
    component_type: str,
    component_name: str | None,
    repository_path: Path | None = None,
) -> ModuleMapping:
    mapping_data: dict[str, Any] = config.default_module_mapping.model_dump()
    try:
        mapping_data["source"] = _MODULE_SOURCE_ADAPTER.validate_python(
            render_jinja_template_values(
                mapping_data["source"],
                _mapping_context(component_type, component_name, repository_path),
                jinja_env=_JINJA_ENV,
            )
        )
        return ModuleMapping.model_validate(mapping_data)
    except (TemplateError, ValidationError) as exc:
        raise StacksmithConfigError(
            "Could not render the default module mapping for "
            f"{_mapping_label(component_type, component_name)}: {exc}"
        ) from exc


def resolve_module_mapping(
    config: ToolConfig,
    component_type: str,
    component_name: str | None = None,
    repository_path: Path | None = None,
) -> ModuleMapping:
    """Resolve an explicit or rendered default mapping for a component.

    Args:
        config: Loaded Stacksmith tool configuration.
        component_type: Abstract component type used for explicit mapping lookup.
        component_name: Optional component instance name exposed to source templates.
            When omitted, `component_type` is also used as `component_name`.
        repository_path: Stack directory used to resolve `git_repository` in a
            default module source template. Uses the current directory when omitted.

    Returns:
        The explicit mapping when configured, otherwise a rendered default mapping.

    Raises:
        StacksmithConfigError: If no mapping is available or the default template
            cannot be rendered into a valid module mapping.
    """
    if component_type in config.module_mappings:
        return config.module_mappings[component_type]
    if config.default_module_mapping is not None:
        return _render_default_mapping(
            config,
            component_type,
            component_name,
            repository_path,
        )
    raise StacksmithConfigError(
        f"{_mapping_label(component_type, component_name).capitalize()} is not "
        "configured in the tool configuration module mappings. Available types: "
        f"{', '.join(config.module_mappings)}"
    )
