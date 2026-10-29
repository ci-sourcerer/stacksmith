from typing import Any

import jmespath
from jmespath import exceptions as jmespath_exceptions

from .exceptions import StacksmithConfigError
from .models import StackDefinition, ToolConfig
from .module_mapping import resolve_module_mapping


def compile_component_expression(expression: str, label: str) -> Any:
    """Compile a JMESPath component-selection expression.

    Args:
        expression: JMESPath expression to compile.
        label: User-facing expression label included in errors.

    Returns:
        Compiled JMESPath expression.

    Raises:
        StacksmithConfigError: If the expression is invalid.
    """
    try:
        return jmespath.compile(expression)
    except jmespath_exceptions.JMESPathError as exc:
        raise StacksmithConfigError(f"Invalid {label}: {exc}") from exc


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


def extract_component_tag_references(expression: str) -> set[str]:
    """Return dot-style tag names referenced by a selector.

    Args:
        expression: JMESPath component-selection expression.

    Returns:
        Referenced tag names, or an empty set for invalid syntax.
    """
    try:
        parsed = jmespath.parser.Parser().parse(expression).parsed
    except jmespath_exceptions.JMESPathError:
        return set()

    references: set[str] = set()
    _collect_tag_references(parsed, references)
    return references


def build_component_selection_contexts(
    stack: StackDefinition,
    config: ToolConfig,
    referenced_tags: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build selector contexts for every component in a stack.

    Args:
        stack: Stack whose components are candidates.
        config: Managed module mapping configuration.
        referenced_tags: Tags that must appear in the boolean tag map even when
            no component currently has them.

    Returns:
        Component names mapped to their selector contexts.
    """
    effective_tags = {}
    all_tags = set(referenced_tags or set())
    for component_name, component in stack.components.items():
        mapping = resolve_module_mapping(
            config,
            component.type,
            component_name,
            repository_path=(
                stack.source_path.parent if stack.source_path is not None else None
            ),
        )
        effective_tags[component_name] = {*component.tags, *mapping.tags}
        all_tags.update(effective_tags[component_name])

    return {
        component_name: {
            "tags": sorted(effective_tags[component_name]),
            "tag": {tag: tag in effective_tags[component_name] for tag in all_tags},
            "component_name": component_name,
            "component_type": component.type,
            "stack_name": stack.name,
            "stack_tags": sorted(stack.tags),
        }
        for component_name, component in stack.components.items()
    }


def select_component_names(
    expression: Any,
    contexts: dict[str, dict[str, Any]],
    *,
    required_tags: set[str] | None = None,
) -> list[str]:
    """Select component names using a compiled expression and required tags.

    Args:
        expression: Optional compiled JMESPath expression.
        contexts: Component selector contexts keyed by component name.
        required_tags: Tags every selected component must have.

    Returns:
        Selected component names in stack declaration order.

    Raises:
        StacksmithConfigError: If the expression does not return a boolean.
    """
    selected = []
    for component_name, context in contexts.items():
        if required_tags and not required_tags.issubset(context["tags"]):
            continue
        if expression is None:
            selected.append(component_name)
            continue
        try:
            result = expression.search(context)
        except jmespath_exceptions.JMESPathError as exc:
            raise StacksmithConfigError(
                f"Component selector failed for component '{component_name}': {exc}"
            ) from exc
        if not isinstance(result, bool):
            raise StacksmithConfigError(
                "Component selector must evaluate to a boolean value for every "
                f"component. Component '{component_name}' produced type "
                f"'{type(result).__name__}' with value {result!r}."
            )
        if result:
            selected.append(component_name)
    return selected
