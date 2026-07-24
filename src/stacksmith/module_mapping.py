from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import TypeAdapter, ValidationError

from .exceptions import StacksmithConfigError
from .models import ModuleMapping, ModuleSourceReference, ToolConfig
from .utils import get_current_git_repository, render_jinja_template_values

_JINJA_ENV = SandboxedEnvironment(undefined=StrictUndefined)
_MODULE_SOURCE_ADAPTER = TypeAdapter(ModuleSourceReference)


def _mapping_context(
    component_type: str,
    component_name: str | None,
    repository_path: Path | None = None,
) -> dict[str, str]:
    context = {
        "component_name": component_name or component_type,
        "component_type": component_type,
    }
    if repository := get_current_git_repository(repository_path):
        context["git_repository"] = repository
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
