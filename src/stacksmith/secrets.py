import base64
import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jinja2 import TemplateRuntimeError, nodes

from .exceptions import StacksmithConfigError

_MARKER = "__STACKSMITH_SECRET_"
_PATTERN = re.compile(r"__STACKSMITH_SECRET_([A-Za-z0-9_-]+)__")
SECRET_SOURCES_LOCAL = "_stacksmith_secret_sources"
FINGERPRINT_KEY_ENV = "STACKSMITH_SECRET_FINGERPRINT_KEY"


def is_secret(value: Any) -> bool:
    """Identify a deferred secret reference.

    Args:
        value: Value to inspect.

    Returns:
        Whether the value contains a reserved secret declaration.
    """
    return isinstance(value, dict) and "$secret" in value


def contains_secret(value: Any) -> bool:
    """Find secret references in a nested value.

    Args:
        value: Value to inspect.

    Returns:
        Whether any nested value is a secret reference.
    """
    if is_secret(value):
        return True
    if isinstance(value, dict):
        return any(contains_secret(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_secret(item) for item in value)
    return isinstance(value, str) and _MARKER in value


def normalize_secrets(value: Any, base_path: Path | None = None) -> Any:
    """Validate references and anchor local file sources without reading secrets.

    Args:
        value: A declaration or nested document.
        base_path: Declaring document directory; `None` requires absolute paths.

    Returns:
        A copy with normalized secret references.

    Raises:
        StacksmithConfigError: If a declaration or interpolation is invalid.
    """
    if isinstance(value, str) and _MARKER in value:
        if not (match := _PATTERN.fullmatch(value)):
            raise StacksmithConfigError("Secrets must occupy an entire value")
        try:
            value = json.loads(
                base64.urlsafe_b64decode(match[1] + "=" * (-len(match[1]) % 4))
            )
        except ValueError, UnicodeDecodeError:
            raise StacksmithConfigError("Invalid deferred secret reference") from None
    if is_secret(value):
        return {"$secret": _normalize_source(value, base_path)}
    if isinstance(value, dict):
        if "$secret_env" in value:
            raise StacksmithConfigError(
                "$secret_env is reserved for generated runtime bindings"
            )
        if any(contains_secret(key) for key in value):
            raise StacksmithConfigError("Secrets cannot be used as mapping keys")
        return {key: normalize_secrets(item, base_path) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_secrets(item, base_path) for item in value]
    return value


def require_secret_sources(
    values: Mapping[str, Any], names: Iterable[str], kind: str = "input"
) -> None:
    """Enforce managed sensitivity without accepting inline secret literals.

    Args:
        values: Supplied input values.
        names: Names declared secret by managed configuration.
        kind: Diagnostic label for the input contract.

    Returns:
        None.

    Raises:
        StacksmithConfigError: If a declared secret has a non-reference value.
    """
    for name in names:
        if name in values and not is_secret(values[name]):
            raise StacksmithConfigError(
                f"Secret {kind} '{name}' requires an env or file $secret reference"
            )


def _normalize_source(value: dict[str, Any], base_path: Path | None) -> dict[str, Any]:
    source = value["$secret"]
    if (
        set(value) != {"$secret"}
        or not isinstance(source, dict)
        or set(source) - {"env", "file", "strip_trailing_newline"}
        or ("env" in source) == ("file" in source)
        or not isinstance(source.get("env", source.get("file")), str)
        or not source.get("env", source.get("file"))
        or not isinstance(source.get("strip_trailing_newline", False), bool)
    ):
        raise StacksmithConfigError("A $secret requires exactly one env or file source")
    source = dict(source)
    if "env" in source:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source["env"]):
            raise StacksmithConfigError("Invalid secret environment variable name")
        if "strip_trailing_newline" in source:
            raise StacksmithConfigError(
                "strip_trailing_newline is only supported for secret files"
            )
    else:
        if "${" in source["file"] or "%{" in source["file"] or "\x00" in source["file"]:
            raise StacksmithConfigError(
                "Secret file paths cannot contain template expressions or NUL characters"
            )
        if not Path(source["file"]).is_absolute():
            if base_path is None:
                raise StacksmithConfigError(
                    "This secret file source requires an absolute path"
                )
            source["file"] = str(base_path / source["file"])
        source["file"] = str(Path(source["file"]).absolute())
    return source


def read_secret(reference: dict[str, Any]) -> str:
    """Read a validated text secret without coercion or template evaluation.

    Args:
        reference: Secret source declaration.

    Returns:
        Secret text, including whitespace unless explicitly stripped.

    Raises:
        StacksmithConfigError: If the source is invalid, missing, or unreadable.
    """
    source = normalize_secrets(reference)["$secret"]
    if "env" in source:
        if source["env"] not in os.environ:
            raise StacksmithConfigError(
                "Required secret environment variable is not set"
            )
        return os.environ[source["env"]]
    try:
        with Path(source["file"]).open(encoding="utf-8", newline="") as stream:
            value = stream.read()
    except OSError, UnicodeError:
        raise StacksmithConfigError(
            "Secret file is unavailable or is not UTF-8 text"
        ) from None
    return value.rstrip("\r\n") if source.get("strip_trailing_newline") else value


def secret_fingerprint(value: str) -> str:
    """Fingerprint a secret using a stable execution-environment key.

    Args:
        value: Secret text.

    Returns:
        A keyed SHA-256 digest.

    Raises:
        StacksmithConfigError: If the fingerprint key is unavailable.
    """
    if not os.environ.get(FINGERPRINT_KEY_ENV):
        raise StacksmithConfigError(f"Secret operations require {FINGERPRINT_KEY_ENV}")
    return hmac.new(
        os.environ[FINGERPRINT_KEY_ENV].encode(), value.encode(), hashlib.sha256
    ).hexdigest()


class _DeferredSecret:
    def __init__(self, reference: dict[str, Any]) -> None:
        self._reference = reference

    def __str__(self) -> str:
        return (
            _MARKER
            + base64.urlsafe_b64encode(json.dumps(self._reference).encode())
            .decode()
            .rstrip("=")
            + "__"
        )

    def __repr__(self) -> str:
        return "[REDACTED]"

    def __bool__(self) -> bool:
        raise TemplateRuntimeError("Secrets cannot control template execution")

    def __iter__(self):
        raise TemplateRuntimeError("Secrets cannot be iterated")


def secret_template_context(value: Any) -> Any:
    """Replace secret references with opaque template values.

    Args:
        value: Rendering context or nested value.

    Returns:
        Context preserving deferred references during direct interpolation.
    """
    if is_secret(value):
        return _DeferredSecret(value)
    if isinstance(value, dict):
        return {key: secret_template_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [secret_template_context(item) for item in value]
    return value


def _expression_value(node: nodes.Node, context: Mapping[str, Any]) -> Any:
    if isinstance(node, nodes.Name):
        return context.get(node.name)
    if isinstance(node, (nodes.Getattr, nodes.Getitem)):
        parent = _expression_value(node.node, context)
        if isinstance(parent, dict):
            if isinstance(node, nodes.Getattr):
                return parent.get(node.attr)
            if isinstance(node.arg, nodes.Const):
                return parent.get(node.arg.value)
            if contains_secret(parent):
                raise TemplateRuntimeError(
                    "Dynamic access to inputs containing secrets is unsupported"
                )
        if contains_secret(parent):
            raise TemplateRuntimeError(
                "Secret contents cannot be accessed in templates"
            )
    return None


def validate_secret_template(
    node: nodes.Node, context: Mapping[str, Any], direct: bool = False
) -> None:
    """Restrict secret template expressions to direct value interpolation.

    Args:
        node: Parsed Jinja node.
        context: Unwrapped rendering context.
        direct: Whether this node is a direct output expression.

    Returns:
        None.

    Raises:
        TemplateRuntimeError: If a secret participates in template logic.
    """
    if isinstance(node, (nodes.Name, nodes.Getattr, nodes.Getitem)):
        if contains_secret(_expression_value(node, context)):
            if direct and is_secret(_expression_value(node, context)):
                return
            raise TemplateRuntimeError(
                "Secrets support only direct value interpolation"
            )
        if isinstance(node, (nodes.Getattr, nodes.Getitem)):
            return
    for child in node.iter_child_nodes():
        validate_secret_template(child, context, isinstance(node, nodes.Output))


def bind_secret_variables(value: Any, sources: dict[str, Any]) -> Any:
    """Replace secret references with sensitive root-variable expressions.

    Args:
        value: Generated configuration values.
        sources: Mutable map populated with runtime source bindings.

    Returns:
        Values referencing generated root variables.
    """
    if is_secret(value):
        name = (
            "_stacksmith_secret_"
            + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        )
        sources[name] = value
        return f"${{var.{name}}}"
    if isinstance(value, dict):
        return {
            key: bind_secret_variables(item, sources) for key, item in value.items()
        }
    if isinstance(value, list):
        return [bind_secret_variables(item, sources) for item in value]
    return value


def validate_secret_locations(value: Any, path: tuple[str, ...] = ()) -> None:
    """Reject secret references in stack structure and non-value fields.

    Args:
        value: Parsed stack data.
        path: Current location in the document.

    Returns:
        None.

    Raises:
        StacksmithConfigError: If a secret appears outside runtime value fields.
    """
    if is_secret(value):
        if not (
            len(path) >= 4
            and path[0] == "components"
            and path[2] == "properties"
            or len(path) == 4
            and path[0] == "operations"
            and path[2] == "with"
            or len(path) >= 3
            and path[0] == "outputs"
            and path[2] == "value"
        ):
            raise StacksmithConfigError(
                "Secrets are only supported in component properties, operation parameters, and output values"
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            validate_secret_locations(item, (*path, str(key)))
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_secret_locations(item, (*path, str(index)))


def secret_environment(document: dict[str, Any]) -> dict[str, str]:
    """Resolve generated runtime bindings into a subprocess environment overlay.

    Args:
        document: Generated OpenTofu JSON configuration.

    Returns:
        Environment variables containing runtime secrets.

    Raises:
        StacksmithConfigError: If sources are unavailable or operation secrets changed.
    """
    environment = {
        f"TF_VAR_{name}": read_secret(reference)
        for name, reference in document.get("locals", {})
        .get(SECRET_SOURCES_LOCAL, {})
        .items()
    }
    for module in document.get("module", {}).values():
        if module.get("source") != "./.stacksmith-operation-runner":
            continue
        for binding in module.get("spec", {}).get("secret_bindings", {}).values():
            value = read_secret(binding["source"])
            if not hmac.compare_digest(
                secret_fingerprint(value), binding["fingerprint"]
            ):
                raise StacksmithConfigError(
                    "Operation secret changed after generation; generate a new operation plan"
                )
            environment[binding["environment"]] = value
    return environment
