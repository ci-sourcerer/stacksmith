from pathlib import Path
from typing import Any

from jinja2 import TemplateError

from .component_references import (
    bind_component_references,
    validate_component_reference_template,
)
from .exceptions import StacksmithTransformError
from .models import (
    RemoteAuthConfig,
    StackDefinition,
    StackOutputDefinition,
    ToolConfig,
)
from .templating import create_sandboxed_jinja_environment
from .transforms import render_jinja_transform
from .utils import get_current_git_repository


def _stack_output_context(
    output_name: str,
    stack: StackDefinition,
) -> dict[str, Any]:
    context = {
        "output": {"name": output_name},
        "stack": {
            "name": stack.name,
            "tags": sorted(stack.tags),
        },
    }
    if repository := get_current_git_repository(
        stack.source_path.parent if stack.source_path is not None else None
    ):
        context["env"] = {"git_repository": repository}
    return context


def apply_stack_output_transform(
    output_name: str,
    specification: StackOutputDefinition,
    value: Any,
    stack: StackDefinition,
) -> Any:
    """Apply a stack output's Jinja transform to a value.

    Args:
        output_name: Public root output name.
        specification: Stack output definition containing the transform.
        value: Exported value before the stack-level transform.
        stack: Stack containing the output definition.

    Returns:
        Transformed value, or the original value when no transform is declared.

    Raises:
        StacksmithTransformError: If the Jinja transform cannot be rendered.
    """
    if specification.transform is None:
        return value
    try:
        validate_component_reference_template(
            create_sandboxed_jinja_environment(),
            specification.transform.jinja,
        )
        return render_jinja_transform(
            specification.transform.jinja,
            value,
            _stack_output_context(output_name, stack),
            "output",
        )
    except TemplateError as exc:
        description_suffix = (
            f" ({specification.transform.description})"
            if specification.transform.description
            else ""
        )
        raise StacksmithTransformError(
            f"Stack '{stack.name}' output '{output_name}' transform"
            f"{description_suffix} {exc}"
        ) from exc


def build_stack_output_blocks(
    stack: StackDefinition,
    config: ToolConfig,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Build root OpenTofu output blocks for a stack.

    Args:
        stack: Stack containing public output definitions.
        config: Managed configuration declaring referenced component outputs.
        cache_dir: Optional cache directory for component output transform scripts.
        auth_config: Optional remote authentication configuration.
        vendor_dir: Optional vendored module root used for output introspection.

    Returns:
        Root output blocks keyed by public stack output name.
    """
    blocks = {}
    for output_name, specification in stack.outputs.items():
        blocks[output_name] = {
            "value": apply_stack_output_transform(
                output_name,
                specification,
                bind_component_references(
                    specification.value,
                    stack,
                    config,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                    vendor_dir=vendor_dir,
                ),
                stack,
            ),
            **(
                {"description": specification.description}
                if specification.description
                else {}
            ),
            **({"sensitive": True} if specification.sensitive else {}),
        }
    return blocks


def build_stack_mock_outputs(stack: StackDefinition) -> dict[str, Any]:
    """Build transformed Terragrunt mock outputs declared by a stack.

    Args:
        stack: Producing stack containing public output definitions.

    Returns:
        Mock values keyed by output name. Outputs without an explicit `mock`
        field are omitted.
    """
    return {
        output_name: apply_stack_output_transform(
            output_name,
            specification,
            specification.mock,
            stack,
        )
        for output_name, specification in stack.outputs.items()
        if "mock" in specification.model_fields_set
    }
