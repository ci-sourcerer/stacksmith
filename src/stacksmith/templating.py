from collections.abc import Mapping
from typing import Any

from jinja2 import ChainableUndefined, StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

_JINJA_MARKERS = ("{{", "{%", "{#")


def contains_jinja_template(value: str) -> bool:
    """Return whether a string contains a Jinja expression marker.

    Args:
        value: String to inspect.

    Returns:
        `True` when the string contains a Jinja expression marker.
    """
    return any(marker in value for marker in _JINJA_MARKERS)


def create_sandboxed_jinja_environment(
    strict: bool = True,
) -> ImmutableSandboxedEnvironment:
    """Create the shared immutable Jinja sandbox.

    Args:
        strict: Whether undefined values raise an error instead of chaining.

    Returns:
        Immutable sandbox with the selected undefined-value behavior.
    """
    return ImmutableSandboxedEnvironment(
        undefined=StrictUndefined if strict else ChainableUndefined
    )


def render_jinja_template_values(
    value: Any, context: Mapping[str, Any], jinja_env: Any
) -> Any:
    """Render Jinja templates recursively in dictionaries and lists.

    Dictionaries and lists are updated in place so templates may reference values
    rendered earlier in the same structure.

    Args:
        value: Value to render.
        context: Rendering context available to Jinja templates.
        jinja_env: Jinja environment used to render template strings.

    Returns:
        Rendered value with the same nested structure as the input.
    """
    if isinstance(value, str) and contains_jinja_template(value):
        return jinja_env.from_string(value).render(context)
    if isinstance(value, dict):
        for key, nested in value.items():
            value[key] = render_jinja_template_values(
                nested,
                context,
                jinja_env=jinja_env,
            )
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = render_jinja_template_values(
                item,
                context,
                jinja_env=jinja_env,
            )
        return value
    return value
