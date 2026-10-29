import json
from typing import Any

from .templating import create_sandboxed_jinja_environment

_JINJA_ENV = create_sandboxed_jinja_environment()


def render_jinja_transform(
    template: str,
    value: Any,
    context: dict[str, Any],
    value_namespace: str,
) -> Any:
    """Render a Jinja transform with its value in a named context namespace.

    Args:
        template: Jinja transform template.
        value: Value exposed to the transform.
        context: Additional transform context.
        value_namespace: Context namespace that receives the `value` field.

    Returns:
        JSON-decoded rendered content when valid JSON, otherwise the rendered
        string.
    """
    value_context = context.get(value_namespace, {}).copy()
    value_context["value"] = value
    rendered = _JINJA_ENV.from_string(template).render(
        {**context, value_namespace: value_context}
    )
    try:
        return json.loads(rendered)
    except json.JSONDecodeError:
        return rendered
