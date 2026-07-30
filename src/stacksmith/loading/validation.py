import re
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from jsonschema.exceptions import best_match
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ..exceptions import StacksmithConfigError
from .files import load_json_schema

_MISSING_PROPERTY_PATTERN = re.compile(r"^'([^']+)' is a required property$")
_LAYER_FLAGS = {
    "runfile": "--runfile",
    "stack definition": "--stack",
    "Stacksmith config": "--config",
}
_FRAGMENT_OMITTED_KEYWORDS = {
    "dependentRequired",
    "minItems",
    "minProperties",
    "required",
    "uniqueItems",
}

ModelT = TypeVar("ModelT", bound=BaseModel)


def _relax_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_relax_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    relaxed = {}
    for key, value in schema.items():
        if key in _FRAGMENT_OMITTED_KEYWORDS:
            continue
        if key in {"$defs", "definitions", "properties", "patternProperties"}:
            relaxed[key] = {
                name: _relax_schema(definition) for name, definition in value.items()
            }
            continue
        relaxed["anyOf" if key == "oneOf" else key] = _relax_schema(value)
    return relaxed


@cache
def load_fragment_schema(schema_name: str) -> dict[str, Any]:
    """Derive an editor-safe layer schema from an effective document schema.

    The derived schema preserves supported keys, value types, and scalar
    constraints while deferring completeness and collection constraints until
    after all selected layers have been merged.

    Args:
        schema_name: Bundled effective schema resource name.

    Returns:
        Schema for validating one partial merge layer.
    """
    schema = _relax_schema(load_json_schema(schema_name))
    if schema_id := schema.get("$id"):
        schema["$id"] = schema_id.replace(".schema.json", ".layer.schema.json")
    if title := schema.get("title"):
        schema["title"] = f"{title} Layer"
    schema["description"] = (
        f"Partial merge layer for {schema.get('description', schema_name).rstrip('.')}."
    )
    schema["x-stacksmith-schema-profile"] = "layer"
    return schema


def _path_label(parts: Sequence[Any]) -> str:
    path = ""
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return f"`{path.removeprefix('.')}`" if path else "document root"


def _most_relevant_error(
    error: JsonSchemaValidationError,
) -> JsonSchemaValidationError:
    if not error.context:
        return error
    return _most_relevant_error(best_match(error.context))


def _format_schema_error(error: JsonSchemaValidationError) -> tuple[str, bool]:
    error = _most_relevant_error(error)
    if error.validator == "required" and (
        match := _MISSING_PROPERTY_PATTERN.match(error.message)
    ):
        return (
            f"Missing required key `{match.group(1)}` at "
            f"{_path_label(error.absolute_path)}.",
            True,
        )
    return (
        f"Invalid value at {_path_label(error.absolute_path)}: {error.message}.",
        False,
    )


def _format_pydantic_error(error: Mapping[str, Any]) -> tuple[str, bool]:
    if error.get("type") == "missing":
        return (
            f"Missing required key "
            f"`{str(error.get('loc', ('value',))[-1])}` at "
            f"{_path_label(error.get('loc', ())[:-1])}.",
            True,
        )
    return (
        f"Invalid value at {_path_label(error.get('loc', ()))}: "
        f"{str(error.get('msg', 'validation failed')).removeprefix('Value error, ')}.",
        False,
    )


def _format_validation_failure(
    document_kind: str,
    sources: Sequence[Path],
    issues: Sequence[tuple[str, bool]],
    fragment: bool,
) -> str:
    unique_issues = list(dict.fromkeys(message for message, _ in issues))
    if fragment:
        return (
            f"{document_kind.capitalize()} layer '{sources[0]}' is invalid:\n"
            + "\n".join(f"  - {issue}" for issue in unique_issues)
        )

    lines = [
        f"Effective {document_kind} is "
        f"{'incomplete' if issues and all(missing for _, missing in issues) else 'invalid'} "
        f"after merging {len(sources)} "
        f"{'layer' if len(sources) == 1 else 'layers'}:",
        *(f"  - {issue}" for issue in unique_issues),
        "Sources, from lowest to highest precedence:",
        *(f"  {index}. {source}" for index, source in enumerate(sources, start=1)),
    ]
    if any(missing for _, missing in issues):
        if document_kind in _LAYER_FLAGS:
            lines.append(
                "If these files are overlays, include every required base layer "
                f"with another `{_LAYER_FLAGS[document_kind]}` argument."
            )
        else:
            lines.append(
                "If these files are overlays, include every required base layer."
            )
    return "\n".join(lines)


def validate_fragment(
    data: Any,
    schema_name: str,
    document_kind: str,
    source: Path,
) -> None:
    """Validate fields present in one mergeable document layer.

    Required-field and minimum-size constraints are deferred until all layers
    have been merged. Types, known keys, and constraints on provided scalar
    values are still checked.

    Args:
        data: Parsed document layer.
        schema_name: Bundled effective schema resource name.
        document_kind: Human-readable document kind used in errors.
        source: Path of the layer being validated.

    Raises:
        StacksmithConfigError: If a provided value violates the fragment schema.
    """
    if errors := list(
        Draft202012Validator(load_fragment_schema(schema_name)).iter_errors(data)
    ):
        raise StacksmithConfigError(
            _format_validation_failure(
                document_kind,
                [source],
                [_format_schema_error(error) for error in errors],
                fragment=True,
            )
        )


def validate_effective_document(
    data: Any,
    schema_name: str,
    document_kind: str,
    sources: Sequence[Path],
) -> None:
    """Validate a fully merged document against its strict schema.

    Args:
        data: Fully merged document.
        schema_name: Bundled effective schema resource name.
        document_kind: Human-readable document kind used in errors.
        sources: Ordered paths that contributed to the merged document.

    Raises:
        StacksmithConfigError: If the effective document violates its schema.
    """
    if errors := list(
        Draft202012Validator(load_json_schema(schema_name)).iter_errors(data)
    ):
        raise StacksmithConfigError(
            _format_validation_failure(
                document_kind,
                sources,
                [_format_schema_error(error) for error in errors],
                fragment=False,
            )
        )


def build_validated_model(
    model_type: type[ModelT],
    data: Mapping[str, Any],
    document_kind: str,
    sources: Sequence[Path],
) -> ModelT:
    """Build a Pydantic model with normalized semantic validation errors.

    Args:
        model_type: Pydantic model type to construct.
        data: Effective document data.
        document_kind: Human-readable document kind used in errors.
        sources: Ordered paths that contributed to the merged document.

    Returns:
        Validated model instance.

    Raises:
        StacksmithConfigError: If model or semantic validation fails.
    """
    try:
        return model_type.model_validate(data)
    except PydanticValidationError as exc:
        raise StacksmithConfigError(
            _format_validation_failure(
                document_kind,
                sources,
                [_format_pydantic_error(error) for error in exc.errors()],
                fragment=False,
            )
        ) from exc
