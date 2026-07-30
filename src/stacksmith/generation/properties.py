from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from ..component_references import bind_component_references
from ..exceptions import StacksmithTransformError, StacksmithValidationError
from ..models import (
    ModulePropertySpec,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
)
from ..transforms import render_jinja_transform
from ..validations import InputValidationOutcome, apply_transform, validate_value


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
    if not (name.endswith("_files") or name == "cwd"):
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


def apply_property_spec(
    value: Any,
    property_spec: ModulePropertySpec | None,
    property_context: dict[str, Any],
    config: ToolConfig,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> Any:
    """Apply a configured property transform and validation.

    Args:
        value: Property value before processing.
        property_spec: Optional managed property specification.
        property_context: Context exposed to transforms and validations.
        config: Managed Stacksmith configuration.
        cache_dir: Optional cache directory for remote scripts.
        auth_config: Optional remote authentication configuration.

    Returns:
        Transformed and validated property value.

    Raises:
        StacksmithTransformError: If the property transform fails.
        StacksmithValidationError: If the property validation fails.
    """
    rendered = value
    if property_spec is None:
        return rendered

    if property_spec.transform is not None:
        try:
            if property_spec.transform.jinja is not None:
                rendered = render_jinja_transform(
                    property_spec.transform.jinja,
                    rendered,
                    property_context,
                    "property",
                )
            else:
                rendered = apply_transform(
                    property_spec.transform,
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
                f"Component '{property_context['component']['name']}' property "
                f"'{property_context['property']['name']}' transform {exc}"
            ) from exc

    if property_spec.validation is None:
        return rendered

    outcome, error_msg = validate_value(
        property_spec.validation,
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
            f"Component '{property_context['component']['name']}' property "
            f"'{property_context['property']['name']}': {error_msg}"
        )
    return rendered


def build_property_context(
    name: str,
    kind: str,
    component_name: str,
    component_type: str,
    output_name: str,
    inputs: dict[str, Any] | None = None,
    stack: dict[str, Any] | None = None,
    git_repository: str | None = None,
) -> dict[str, Any]:
    """Build the context exposed to property transforms and validations.

    Args:
        name: Source property name.
        kind: Property context kind.
        component_name: Component instance name.
        component_type: Managed component type.
        output_name: Mapped module input name.
        inputs: Optional resolved stack inputs.
        stack: Optional stack metadata.
        git_repository: Optional source repository URL.

    Returns:
        Property processing context.
    """
    context = {
        "property": {
            "name": name,
            "kind": kind,
            "output_name": output_name,
        },
        "component": {
            "name": component_name,
            "type": component_type,
        },
    }
    if inputs is not None:
        context["inputs"] = inputs
    if stack is not None:
        context["stack"] = stack
    if git_repository is not None:
        context["env"] = {"git_repository": git_repository}
    return context


@dataclass(frozen=True)
class PropertyRenderer:
    """Render module property values using shared component context.

    Attributes:
        config: Managed Stacksmith configuration.
        stack_definition: Final stack definition containing referenced components.
        resolved_inputs: Inputs available to property processing.
        stack: Stack metadata available to property processing.
        component_name: Component instance name.
        component_type: Managed component type.
        base_paths: Candidate paths for resolving file-like module inputs.
        git_repository: Optional source repository URL.
        cache_dir: Optional cache directory for remote scripts.
        auth_config: Optional remote authentication configuration.
        vendor_dir: Optional vendored module root used for output introspection.
    """

    config: ToolConfig
    stack_definition: StackDefinition
    resolved_inputs: dict[str, Any]
    stack: dict[str, Any]
    component_name: str
    component_type: str
    base_paths: list[Path]
    git_repository: str | None = None
    cache_dir: Path | None = None
    auth_config: RemoteAuthConfig | None = None
    vendor_dir: Path | None = None

    def output_name(
        self,
        name: str,
        property_spec: ModulePropertySpec | None,
    ) -> str:
        """Return the mapped module input name for a source property.

        Args:
            name: Source property name.
            property_spec: Optional managed property specification.

        Returns:
            Mapped module input name.
        """
        if property_spec is not None and property_spec.mapped_to:
            return property_spec.mapped_to
        return name

    def render(
        self,
        name: str,
        value: Any,
        property_spec: ModulePropertySpec | None,
        kind: str = "component_property",
    ) -> tuple[str, Any]:
        """Render one property into its mapped module input.

        Args:
            name: Source property name.
            value: Property value before processing.
            property_spec: Optional managed property specification.
            kind: Property context kind.

        Returns:
            Mapped output name and rendered value.
        """
        output_name = self.output_name(name, property_spec)
        if output_name != name:
            LOGGER.debug(
                "Component '{component_name}' property '{property_name}' is mapped "
                "to module input '{output_name}'",
                component_name=self.component_name,
                property_name=name,
                output_name=output_name,
            )

        return output_name, _normalize_module_input_value(
            output_name,
            apply_property_spec(
                bind_component_references(
                    value,
                    self.stack_definition,
                    self.config,
                    self.component_name,
                    self.cache_dir,
                    self.auth_config,
                    self.vendor_dir,
                ),
                property_spec,
                build_property_context(
                    name=name,
                    kind=kind,
                    component_name=self.component_name,
                    component_type=self.component_type,
                    output_name=output_name,
                    inputs=self.resolved_inputs,
                    stack=self.stack,
                    git_repository=self.git_repository,
                ),
                self.config,
                cache_dir=self.cache_dir,
                auth_config=self.auth_config,
            ),
            self.base_paths,
        )
