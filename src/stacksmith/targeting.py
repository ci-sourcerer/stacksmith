from pathlib import Path
from typing import Any

import jmespath
from jmespath import exceptions as jmespath_exceptions

from .enums import TerragruntAction
from .exceptions import StacksmithConfigError
from .models import StackDefinition, ToolConfig
from .module_mapping import resolve_module_mapping


def compile_tag_expression(tag_expr: str):
    """Compile a component tag expression.

    Args:
        tag_expr: JMESPath tag expression.

    Returns:
        Compiled JMESPath expression.

    Raises:
        StacksmithConfigError: If the expression is invalid.
    """
    try:
        return jmespath.compile(tag_expr)
    except jmespath_exceptions.JMESPathError as exc:
        raise StacksmithConfigError(f"Invalid --tag-expr: {exc}") from exc


def _collect_tag_references(node: Any, references: set[str]) -> None:
    if not isinstance(node, dict):
        return

    children = node.get("children", []) or []
    if (
        node.get("type") == "subexpression"
        and len(children) == 2
        and children[0].get("type") == "field"
        and children[0].get("value") == "tag"
        and isinstance(children[1].get("value"), str)
    ):
        references.add(children[1]["value"])

    for child in children:
        _collect_tag_references(child, references)
    for value in node.values():
        if isinstance(value, dict):
            _collect_tag_references(value, references)
        elif isinstance(value, list):
            for item in value:
                _collect_tag_references(item, references)


def extract_tag_references(tag_expr: str) -> set[str]:
    """Extract dot-style tag names referenced by an expression.

    Args:
        tag_expr: JMESPath tag expression.

    Returns:
        Referenced tag names, or an empty set for an invalid expression.
    """
    try:
        parsed = jmespath.parser.Parser().parse(tag_expr).parsed
    except jmespath_exceptions.JMESPathError:
        return set()

    references: set[str] = set()
    _collect_tag_references(parsed, references)
    return references


def _build_component_tag_context(
    stack: StackDefinition,
    component_name: str,
    component_effective_tags: set[str],
    all_stack_tags: set[str],
) -> dict[str, Any]:
    return {
        "tags": sorted(component_effective_tags),
        "tag": {tag: tag in component_effective_tags for tag in all_stack_tags},
        "component_name": component_name,
        "component_type": stack.components[component_name].type,
        "stack_name": stack.name,
        "stack_tags": sorted(stack.tags),
    }


def _evaluate_tag_expression(
    expression: Any,
    context: dict[str, Any],
    component_name: str,
) -> bool:
    result = expression.search(context)
    if not isinstance(result, bool):
        raise StacksmithConfigError(
            "Tag expression must evaluate to a boolean value for every component. "
            f"Component '{component_name}' produced type "
            f"'{type(result).__name__}' with value {result!r}."
        )
    return result


def compute_stack_target_modules(
    stack: StackDefinition,
    config: ToolConfig,
    expression: Any = None,
    referenced_tags: set[str] | None = None,
    required_tags: set[str] | None = None,
) -> list[str]:
    """Compute selected Terraform module addresses for one stack.

    Args:
        stack: Stack whose components are candidates.
        config: Managed module mapping configuration.
        expression: Optional compiled tag expression.
        referenced_tags: Tags referenced by the expression.
        required_tags: Tags every selected component must have.

    Returns:
        Selected Terraform module addresses.
    """
    effective_tags_by_component: dict[str, set[str]] = {}
    all_stack_tags: set[str] = set()
    for component_name, component in stack.components.items():
        mapping = resolve_module_mapping(
            config,
            component.type,
            component_name,
            repository_path=(
                stack.source_path.parent if stack.source_path is not None else None
            ),
        )
        effective_tags_by_component[component_name] = {
            *component.tags,
            *mapping.tags,
        }
        all_stack_tags.update(effective_tags_by_component[component_name])

    all_stack_tags.update(referenced_tags or set())
    required_tags = required_tags or set()

    targets: list[str] = []
    for component_name in stack.components:
        effective_tags = effective_tags_by_component[component_name]
        if required_tags and not required_tags.issubset(effective_tags):
            continue
        if expression is None or _evaluate_tag_expression(
            expression,
            _build_component_tag_context(
                stack,
                component_name,
                effective_tags,
                all_stack_tags,
            ),
            component_name,
        ):
            targets.append(f"module.{component_name}")
    return targets


def resolve_tag_targets(
    stack: StackDefinition,
    config: ToolConfig,
    tags: list[str] | None,
    tag_expr: str | None,
) -> tuple[None | object, set[str] | None, list[str]]:
    """Resolve a tag selection into a compiled expression and module targets.

    Args:
        stack: Stack whose components are candidates.
        config: Managed module mapping configuration.
        tags: Required component tags.
        tag_expr: Optional JMESPath tag expression.

    Returns:
        Compiled expression, referenced tags, and module addresses.
    """
    expression = compile_tag_expression(tag_expr) if tag_expr else None
    referenced_tags = extract_tag_references(tag_expr) if tag_expr else None
    return (
        expression,
        referenced_tags,
        compute_stack_target_modules(
            stack,
            config,
            expression,
            referenced_tags=referenced_tags,
            required_tags=set(tags or []),
        ),
    )


def validate_action_options(
    action: str | TerragruntAction,
    tags: list[str] | None,
    tag_expr: str | None,
    save_plan_json: Path | None,
    tag_support_label: str,
    save_plan_label: str,
) -> TerragruntAction:
    """Validate action-specific targeting and plan-output options.

    Args:
        action: Requested Terragrunt action.
        tags: Required component tags.
        tag_expr: Optional component tag expression.
        save_plan_json: Optional plan JSON output value.
        tag_support_label: Command label used in targeting errors.
        save_plan_label: Command label used in plan-output errors.

    Returns:
        Normalized Terragrunt action.

    Raises:
        StacksmithConfigError: If an option is unsupported for the action.
    """
    action_enum = TerragruntAction(action)
    if (tags or tag_expr) and action_enum not in {
        TerragruntAction.PLAN,
        TerragruntAction.APPLY,
        TerragruntAction.DESTROY,
    }:
        raise StacksmithConfigError(
            f"--tag and --tag-expr are only supported for {tag_support_label}"
        )
    if save_plan_json is not None and action_enum != TerragruntAction.PLAN:
        raise StacksmithConfigError(
            f"--save-plan-json is only supported for {save_plan_label}"
        )
    return action_enum


def build_terragrunt_args(
    action: str | TerragruntAction,
    destroy: bool = False,
    targets: list[str] | None = None,
) -> list[str]:
    """Build Terragrunt action and target arguments.

    Args:
        action: Requested Terragrunt action.
        destroy: Whether a plan is a destroy plan.
        targets: Optional Terraform module addresses.

    Returns:
        Terragrunt arguments.
    """
    action_enum = TerragruntAction(action)
    terragrunt_args = [action_enum.value]
    if action_enum == TerragruntAction.PLAN and destroy:
        terragrunt_args.append("-destroy")
    for target in targets or []:
        terragrunt_args.extend(["-target", target])
    return terragrunt_args
