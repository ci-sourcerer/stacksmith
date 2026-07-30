import json
import re
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

import yaml
from jinja2 import ChainableUndefined, StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from ..exceptions import StacksmithConfigError, StacksmithNotFoundError

_UNDEFINED_ATTRIBUTE_PATTERN = re.compile(r"has no attribute '([^']+)'")
_UNDEFINED_VALUE_PATTERN = re.compile(r"'([^']+)' is undefined")


@cache
def load_json_schema(name: str) -> dict[str, Any]:
    """Load and cache a bundled JSON schema.

    Args:
        name: Schema resource name.

    Returns:
        Parsed JSON schema.
    """
    return json.loads(
        files("stacksmith.schemas").joinpath(name).read_text(encoding="utf-8")
    )


def _read_file_text(path: Path) -> tuple[str, str]:
    if not path.exists():
        raise StacksmithNotFoundError(f"File not found: {path}")
    return path.suffix.lower(), path.read_text(encoding="utf-8")


def _parse_object_file(path: Path, suffix: str, text: str) -> dict[str, Any]:
    match suffix:
        case ".yaml" | ".yml":
            loaded = yaml.safe_load(text)
            if loaded is None:
                return {}
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded
        case ".json":
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise StacksmithConfigError(
                    f"File must contain a top-level object: {path}"
                )
            return loaded
        case _:
            raise StacksmithConfigError(
                f"Unsupported file extension '{suffix}'. Use .yaml, .yml, or .json."
            )


def _undefined_value_name(exc: UndefinedError) -> str | None:
    for pattern in (_UNDEFINED_ATTRIBUTE_PATTERN, _UNDEFINED_VALUE_PATTERN):
        if match := pattern.search(str(exc)):
            return match.group(1)
    return None


def _references_input(text: str, name: str) -> bool:
    escaped_name = re.escape(name)
    return bool(
        re.search(rf"\binputs\s*\.\s*{escaped_name}\b", text)
        or re.search(
            rf"""\binputs\s*\[\s*["']{escaped_name}["']\s*\]""",
            text,
        )
    )


def _format_undefined_template_error(text: str, path: Path, exc: UndefinedError) -> str:
    if (name := _undefined_value_name(exc)) and _references_input(text, name):
        return (
            f"Missing required stack template input '{name}' "
            f"(`inputs.{name}`) while rendering '{path}'. Pass it with "
            f"`--var {name}=<value>` or `--vars <path>`."
        )
    if name:
        return (
            f"Missing required template value '{name}' while rendering stack "
            f"template '{path}': {exc}"
        )
    return f"Could not render stack template '{path}': {exc}"


def _render_template(
    text: str, path: Path, context: Mapping[str, Any], strict: bool
) -> str:
    try:
        return (
            SandboxedEnvironment(
                undefined=StrictUndefined if strict else ChainableUndefined
            )
            .from_string(text)
            .render(context)
        )
    except UndefinedError as exc:
        raise StacksmithConfigError(
            _format_undefined_template_error(text, path, exc)
        ) from exc
    except TemplateError as exc:
        raise StacksmithConfigError(
            f"Could not render stack template '{path}': {exc}"
        ) from exc


def load_object_file(
    path: Path,
    template_context: Mapping[str, Any] | None = None,
    strict_template_context: bool = False,
) -> dict[str, Any]:
    """Load a YAML or JSON object document with optional Jinja rendering.

    Args:
        path: Document path.
        template_context: Optional Jinja context.
        strict_template_context: Whether undefined Jinja values raise an error.

    Returns:
        Parsed top-level mapping.
    """
    suffix, text = _read_file_text(path)
    if template_context is not None:
        text = _render_template(
            text,
            path,
            template_context,
            strict=strict_template_context,
        )
    return _parse_object_file(path, suffix, text)


def _format_yaml_range(node: yaml.nodes.Node, path: Path) -> str:
    return f"{path.name}:{node.start_mark.line + 1}-{node.end_mark.line + 1}"


def _walk_yaml_locations(
    node: yaml.nodes.Node,
    current_path: tuple[str, ...],
    path: Path,
    locations: dict[tuple[str, ...], str],
) -> None:
    if isinstance(node, yaml.nodes.SequenceNode):
        for item in node.value:
            _walk_yaml_locations(item, current_path, path, locations)
        return
    if not isinstance(node, yaml.nodes.MappingNode):
        return

    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.nodes.ScalarNode):
            continue
        key = key_node.value
        next_path = (*current_path, key)
        if (
            (
                key in {"validation", "transform"}
                and isinstance(value_node, yaml.nodes.MappingNode)
            )
            or (
                current_path == ("var_validations",)
                and isinstance(value_node, yaml.nodes.MappingNode)
            )
            or (
                len(current_path) == 2
                and current_path[0] == "plan_validations"
                and key == "rule"
                and isinstance(value_node, yaml.nodes.MappingNode)
            )
        ):
            locations[next_path] = _format_yaml_range(value_node, path)
        _walk_yaml_locations(value_node, next_path, path, locations)


def _extract_yaml_locations(text: str, path: Path) -> dict[tuple[str, ...], str]:
    locations: dict[tuple[str, ...], str] = {}
    if root := yaml.compose(text):
        _walk_yaml_locations(root, (), path, locations)
    return locations


def load_object_file_with_locations(
    path: Path,
) -> tuple[dict[str, Any], dict[tuple[str, ...], str]]:
    """Load an object document and collect YAML policy source locations.

    Args:
        path: Document path.

    Returns:
        Parsed mapping and source locations keyed by document path.
    """
    suffix, text = _read_file_text(path)
    loaded = _parse_object_file(path, suffix, text)
    if suffix in {".yaml", ".yml"}:
        return loaded, _extract_yaml_locations(text, path)
    return loaded, {}
