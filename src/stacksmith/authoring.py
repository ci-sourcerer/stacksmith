import difflib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.tokens import AliasToken, AnchorToken, TagToken

from .exceptions import StacksmithConfigError
from .loading.validation import validate_effective_document


class StackEdit(BaseModel):
    """An explicit edit to a mapping path in an authored stack.

    Attributes:
        action: Set a value, shallow-merge a mapping, or remove a key.
        path: Non-empty mapping keys from the document root to the edited value.
        value: JSON-compatible value for set or merge; unused for remove.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["set", "merge", "remove"]
    path: tuple[str, ...]
    value: JsonValue = None


@dataclass(frozen=True)
class SourceChange:
    """A reviewable source proposal with no filesystem or repository effects.

    Attributes:
        source: Proposed authored source.
        diff: Unified diff against the original source.
        changed: Whether the source differs from the original.
    """

    source: str
    diff: str
    changed: bool


@dataclass(frozen=True)
class DocumentChange:
    """Result of writing a validated stack-document proposal.

    Attributes:
        path: Absolute path to the updated document.
        changed: Whether the document contents changed.
    """

    path: Path
    changed: bool


def _yaml_parser() -> YAML:
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def _reject_json_constant(value: str) -> None:
    raise StacksmithConfigError(f"Non-finite JSON value '{value}' is not editable.")


def _validate_shape(value: Any, depth: int = 0) -> None:
    if depth > 40:
        raise StacksmithConfigError("Deeply nested documents are not editable.")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise StacksmithConfigError("Editable mapping keys must be strings.")
            _validate_shape(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _validate_shape(child, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise StacksmithConfigError("Non-finite numbers are not editable.")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise StacksmithConfigError("Editable values must be JSON-compatible.")


def _load_source(source: str, path: str) -> dict[str, Any]:
    suffix = Path(path).suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise StacksmithConfigError("Use a .yaml, .yml, or .json document path.")
    if "{%" in source or "{#" in source:
        raise StacksmithConfigError("Whole-file Jinja templates are read-only.")
    try:
        if suffix == ".json":
            document = json.loads(source, parse_constant=_reject_json_constant)
        else:
            parser = _yaml_parser()
            if any(
                isinstance(token, AliasToken | AnchorToken | TagToken)
                for token in parser.scan(source)
            ):
                raise StacksmithConfigError(
                    "YAML aliases, anchors, and tags are read-only."
                )
            document = parser.load(source)
    except (json.JSONDecodeError, YAMLError, RecursionError) as exc:
        raise StacksmithConfigError("Invalid editable YAML/JSON source.") from exc
    _validate_shape(document)
    if not isinstance(document, dict):
        raise StacksmithConfigError("A stack document must be a mapping.")
    return document


def _update_mapping(original: dict[str, Any], desired: dict[str, Any]) -> None:
    for key in list(original):
        if key not in desired:
            del original[key]
    for key, value in desired.items():
        if isinstance(original.get(key), dict) and isinstance(value, dict):
            _update_mapping(original[key], value)
        elif key not in original or json.dumps(
            original[key], sort_keys=True
        ) != json.dumps(value, sort_keys=True):
            original[key] = value


def _apply_edit(document: dict[str, Any], edit: StackEdit) -> None:
    if not edit.path or any(not key for key in edit.path):
        raise StacksmithConfigError("Edit paths must contain non-empty mapping keys.")
    current = document
    for key in edit.path[:-1]:
        if key not in current:
            if edit.action == "remove":
                return
            current[key] = {}
        if not isinstance(current[key], dict):
            raise StacksmithConfigError(f"Edit path '{key}' is not a mapping.")
        current = current[key]
    key = edit.path[-1]
    if edit.action == "remove":
        current.pop(key, None)
        return
    value = deepcopy(edit.value)
    if edit.action == "merge":
        if not isinstance(value, dict) or not isinstance(current.get(key, {}), dict):
            raise StacksmithConfigError(
                "Merge edits require mapping values and targets."
            )
        value = {**current.get(key, {}), **value}
    if isinstance(value, dict) and isinstance(current.get(key), dict):
        _update_mapping(current[key], value)
    else:
        current[key] = value


def author_stack(
    source: str, edits: list[StackEdit], path: str = "stack.yaml"
) -> SourceChange:
    """Propose explicit edits without rendering templates or writing files.

    Unrelated authored fields and surviving YAML comments and scalar styles are
    preserved. Semantic no-ops return the exact original source. Validation is
    against the stack document schema only; platform policies, configuration,
    hooks, references, and infrastructure plans require separate inspection.

    Args:
        source: Original YAML or JSON source.
        edits: Ordered set, shallow-merge, and remove edits to mapping paths.
        path: Filename used to choose serialization and label the diff.

    Returns:
        Validated proposed source, unified diff, and change indicator.

    Raises:
        StacksmithConfigError: If source, edits, or the proposed document are invalid.
    """
    document = _load_source(source, path)
    original = json.dumps(document, allow_nan=False, sort_keys=True)
    for edit in edits:
        _apply_edit(document, edit)
    _validate_shape(document)
    validate_effective_document(
        document, "stack.schema.json", "authored stack", [Path(path)]
    )
    if json.dumps(document, allow_nan=False, sort_keys=True) == original:
        return SourceChange(source=source, diff="", changed=False)
    if Path(path).suffix.lower() == ".json":
        proposed = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    else:
        output = StringIO()
        _yaml_parser().dump(document, output)
        proposed = output.getvalue()
    return SourceChange(
        source=proposed,
        diff="".join(
            difflib.unified_diff(
                source.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        ),
        changed=proposed != source,
    )


def validate_stack_document(path: Path | str) -> None:
    """Validate authored document structure without rendering or platform hooks.

    Args:
        path: Stack YAML, YML, or JSON document.

    Returns:
        None.

    Raises:
        StacksmithConfigError: If the authored source is unsupported or invalid.
        OSError: If the document cannot be read.
    """
    author_stack(_read_stack_text(Path(path).expanduser()), [], str(path))


def _read_stack_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as document:
        return document.read()


def _write_edit(stack_path: Path | str, edit: StackEdit) -> DocumentChange:
    path = Path(stack_path).expanduser().resolve()
    source = _read_stack_text(path)
    document = _load_source(source, str(path))
    section, name = edit.path[:2]
    if not isinstance(document.get(section), dict) or not isinstance(
        document[section].get(name), dict
    ):
        raise StacksmithConfigError(f"Unknown {section} definition '{name}'.")
    change = author_stack(source, [edit], str(path))
    if change.changed:
        path.write_text(change.source, encoding="utf-8")
    return DocumentChange(path, change.changed)


def update_operation_rerun_token(
    stack_path: Path | str, operation: str, rerun_token: str
) -> DocumentChange:
    """Set an existing operation invocation's declarative rerun token.

    Args:
        stack_path: Stack document to update.
        operation: Existing stack-local operation name.
        rerun_token: Non-empty token identifying the requested execution.

    Returns:
        Details of the validated document update.

    Raises:
        StacksmithConfigError: If the operation, token, or document is invalid.
        OSError: If the document cannot be read or written.
    """
    if not rerun_token.strip():
        raise StacksmithConfigError("rerun_token must be a non-empty string.")
    return _write_edit(
        stack_path,
        StackEdit(
            action="set",
            path=("operations", operation, "rerun_token"),
            value=rerun_token,
        ),
    )


def set_operation_inputs(
    stack_path: Path | str,
    operation: str,
    values: dict[str, Any],
    replace: bool = False,
) -> DocumentChange:
    """Merge or replace inputs for an existing operation invocation.

    Args:
        stack_path: Stack document to update.
        operation: Existing stack-local operation name.
        values: Authored input values.
        replace: Whether to replace all inputs instead of shallow-merging.

    Returns:
        Details of the validated document update.

    Raises:
        StacksmithConfigError: If the operation or document is invalid.
        ValidationError: If values cannot be represented as JSON.
        OSError: If the document cannot be read or written.
    """
    return _write_edit(
        stack_path,
        StackEdit(
            action="set" if replace else "merge",
            path=("operations", operation, "with"),
            value=values,
        ),
    )


def update_component_properties(
    stack_path: Path | str,
    component: str,
    properties: dict[str, Any],
    replace: bool = False,
) -> DocumentChange:
    """Merge or replace properties for an existing component.

    Args:
        stack_path: Stack document to update.
        component: Existing component name.
        properties: Authored property values.
        replace: Whether to replace all properties instead of shallow-merging.

    Returns:
        Details of the validated document update.

    Raises:
        StacksmithConfigError: If the component or document is invalid.
        ValidationError: If properties cannot be represented as JSON.
        OSError: If the document cannot be read or written.
    """
    return _write_edit(
        stack_path,
        StackEdit(
            action="set" if replace else "merge",
            path=("components", component, "properties"),
            value=properties,
        ),
    )
