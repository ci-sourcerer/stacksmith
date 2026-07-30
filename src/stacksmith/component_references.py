import base64
import binascii
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from jinja2 import TemplateError, TemplateRuntimeError, TemplateSyntaxError, nodes
from jinja2.visitor import NodeVisitor

from .exceptions import StacksmithConfigError, StacksmithTransformError
from .introspection import discover_module_outputs
from .models import ModuleOutputSpec, render_module_source_identity
from .templating import create_sandboxed_jinja_environment
from .transforms import render_jinja_transform
from .utils import get_current_git_repository
from .validations import apply_transform
from .vendor import get_vendor_dir

if TYPE_CHECKING:
    from pathlib import Path

    from jinja2.sandbox import SandboxedEnvironment

    from .models import (
        ModuleMapping,
        RemoteAuthConfig,
        StackDefinition,
        ToolConfig,
    )

_MARKER_PREFIX = "__STACKSMITH_COMPONENT_REFERENCE_"
_MARKER_PATTERN = re.compile(rf"{re.escape(_MARKER_PREFIX)}([A-Za-z0-9_-]+)__")
_RAW_MODULE_REFERENCE = "${module."
_COMPONENT_TEMPLATE_PATTERN = re.compile(r"{{\s*components\s*(?:\.|\[)")
_STACK_OUTPUT_TEMPLATE_PATTERN = re.compile(r"{{\s*output\s*(?:\.|\[)")


def _reference_error(message: str) -> TemplateRuntimeError:
    return TemplateRuntimeError(
        f"Component output references {message}. Use "
        "`{{ components.<component>.<output> }}` only as a rendered value."
    )


def _stack_output_transform_error(message: str) -> TemplateRuntimeError:
    return TemplateRuntimeError(
        f"Stack output transform references {message}. Use `output.value` or "
        "`output.name` only as a rendered value."
    )


def _validate_reference_name(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not value:
        raise _reference_error(f"require a non-empty string {kind} name")
    return value


def _encode_reference(component_name: str, output_name: str) -> str:
    return (
        _MARKER_PREFIX
        + base64.urlsafe_b64encode(
            json.dumps(
                [component_name, output_name],
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
        + "__"
    )


def _decode_reference(payload: str) -> tuple[str, str]:
    try:
        decoded = json.loads(
            base64.urlsafe_b64decode(payload + ("=" * (-len(payload) % 4))).decode(
                "utf-8"
            )
        )
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StacksmithConfigError(
            "Stack contains an invalid deferred component output reference"
        ) from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        raise StacksmithConfigError(
            "Stack contains an invalid deferred component output reference"
        )
    return decoded[0], decoded[1]


class _DeferredComponentOutput:
    def __init__(self, component_name: str, output_name: str) -> None:
        self._component_name = component_name
        self._output_name = output_name

    def __str__(self) -> str:
        return _encode_reference(self._component_name, self._output_name)

    def __bool__(self) -> bool:
        raise _reference_error("cannot be evaluated as booleans")

    def __iter__(self):
        raise _reference_error("cannot be iterated")


class _DeferredComponent:
    def __init__(self, component_name: str) -> None:
        self._component_name = component_name

    def __getattr__(self, output_name: str) -> _DeferredComponentOutput:
        if output_name.startswith("_"):
            raise AttributeError(output_name)
        return self[output_name]

    def __getitem__(self, output_name: Any) -> _DeferredComponentOutput:
        return _DeferredComponentOutput(
            self._component_name,
            _validate_reference_name(output_name, "output"),
        )

    def __str__(self) -> str:
        raise _reference_error("must select a public output")

    def __bool__(self) -> bool:
        raise _reference_error("cannot be evaluated as booleans")

    def __iter__(self):
        raise _reference_error("cannot be iterated")


class _DeferredComponents:
    def __getattr__(self, component_name: str) -> _DeferredComponent:
        if component_name.startswith("_"):
            raise AttributeError(component_name)
        return self[component_name]

    def __getitem__(self, component_name: Any) -> _DeferredComponent:
        return _DeferredComponent(_validate_reference_name(component_name, "component"))

    def __str__(self) -> str:
        raise _reference_error("must select a component and public output")

    def __bool__(self) -> bool:
        raise _reference_error("cannot be evaluated as booleans")

    def __iter__(self):
        raise _reference_error("cannot be iterated")


class _DeferredStackOutput:
    _ATTRIBUTES = frozenset({"name", "value"})

    def __getattr__(self, attribute_name: str) -> str:
        if attribute_name.startswith("_"):
            raise AttributeError(attribute_name)
        return self[attribute_name]

    def __getitem__(self, attribute_name: Any) -> str:
        if attribute_name not in self._ATTRIBUTES:
            raise _stack_output_transform_error(
                "only expose `output.name` and `output.value` in stack output "
                "transforms"
            )
        return f"{{{{ output.{attribute_name} }}}}"

    def __str__(self) -> str:
        raise _stack_output_transform_error(
            "must select `output.name` or `output.value`"
        )

    def __bool__(self) -> bool:
        raise _stack_output_transform_error("cannot be evaluated as booleans")

    def __iter__(self):
        raise _stack_output_transform_error("cannot be iterated")


def _contains_component_reference(node: nodes.Node) -> bool:
    if isinstance(node, nodes.Name) and node.name == "components":
        return True
    return any(
        _contains_component_reference(child) for child in node.iter_child_nodes()
    )


def _is_component_output_expression(node: nodes.Node) -> bool:
    traversals = 0
    current = node
    while isinstance(current, (nodes.Getattr, nodes.Getitem)):
        traversals += 1
        current = current.node
    return (
        traversals == 2
        and isinstance(current, nodes.Name)
        and current.name == "components"
    )


def _contains_stack_output_reference(node: nodes.Node) -> bool:
    if isinstance(node, nodes.Name) and node.name == "output":
        return True
    return any(
        _contains_stack_output_reference(child) for child in node.iter_child_nodes()
    )


def _is_stack_output_expression(node: nodes.Node) -> bool:
    if isinstance(node, nodes.Getattr):
        attribute_name = node.attr
    elif isinstance(node, nodes.Getitem) and isinstance(node.arg, nodes.Const):
        attribute_name = node.arg.value
    else:
        return False
    return (
        attribute_name in _DeferredStackOutput._ATTRIBUTES
        and isinstance(node.node, nodes.Name)
        and node.node.name == "output"
    )


class _ComponentReferenceTemplateValidator(NodeVisitor):
    def visit_Output(self, node: nodes.Output, *args: Any, **kwargs: Any) -> None:
        for expression in node.nodes:
            if _contains_component_reference(expression):
                if not _is_component_output_expression(expression):
                    raise TemplateSyntaxError(
                        "Component outputs may only be referenced directly as "
                        "`{{ components.<component>.<output> }}` values",
                        expression.lineno,
                    )
                continue
            if _contains_stack_output_reference(expression):
                if not _is_stack_output_expression(expression):
                    raise TemplateSyntaxError(
                        "Stack output transforms may only reference `output.name` "
                        "or `output.value` directly",
                        expression.lineno,
                    )
                continue
            self.visit(expression, *args, **kwargs)

    def visit_Name(self, node: nodes.Name, *args: Any, **kwargs: Any) -> None:
        if node.name == "components":
            raise TemplateSyntaxError(
                "Component outputs cannot be used in Jinja control flow, "
                "filters, calls, or calculations",
                node.lineno,
            )
        if node.name == "output":
            raise TemplateSyntaxError(
                "Stack output transform values cannot be used in Jinja control "
                "flow, filters, calls, or calculations",
                node.lineno,
            )


def validate_component_reference_template(
    environment: "SandboxedEnvironment", source: str
) -> None:
    """Validate deferred output references in a stack Jinja template.

    Args:
        environment: Sandboxed Jinja environment used to parse the template.
        source: Stack template source.

    Raises:
        TemplateSyntaxError: If component or stack output transform references
            are used outside direct value interpolation.
    """
    _ComponentReferenceTemplateValidator().visit(environment.parse(source))


def with_component_reference_context(
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Add deferred output reference namespaces to a Jinja context.

    Args:
        context: Existing stack template context, or `None` when rendering is
            disabled.

    Returns:
        A copied context containing the reserved `components` and `output`
        namespaces, or `None`.
    """
    if context is None:
        return None
    return {
        **context,
        "components": _DeferredComponents(),
        "output": _DeferredStackOutput(),
    }


def _is_allowed_reference_path(path: tuple[str, ...]) -> bool:
    return (
        (len(path) >= 4 and path[0] == "components" and path[2] == "properties")
        or (len(path) >= 4 and path[0] == "operations" and path[2] == "with")
        or (len(path) >= 3 and path[0] == "outputs" and path[2] == "value")
    )


def _is_stack_output_transform_path(path: tuple[str, ...]) -> bool:
    return (
        len(path) == 4 and path[0] == "outputs" and path[2:] == ("transform", "jinja")
    )


def validate_component_reference_locations(
    value: Any, path: tuple[str, ...] = ()
) -> None:
    """Ensure deferred references appear only in runtime value locations.

    Args:
        value: Parsed stack data to inspect.
        path: Current document path used during recursive validation.

    Raises:
        StacksmithConfigError: If a reference appears in document structure or a
            raw module reference bypasses the public component contract.
    """
    if isinstance(value, str):
        if _is_stack_output_transform_path(path):
            try:
                validate_component_reference_template(
                    create_sandboxed_jinja_environment(),
                    value,
                )
            except TemplateError as exc:
                raise StacksmithConfigError(
                    f"Invalid stack output Jinja transform: {exc}"
                ) from exc
        if _RAW_MODULE_REFERENCE in value:
            raise StacksmithConfigError(
                "Raw Terraform module references are not supported in stack "
                "definitions. Declare a managed component output and reference "
                "it with `{{ components.<component>.<output> }}`."
            )
        if _MARKER_PREFIX in value and not _is_allowed_reference_path(path):
            raise StacksmithConfigError(
                "Component outputs may only be referenced from component "
                "properties, stack outputs, or operation inputs"
            )
        if _COMPONENT_TEMPLATE_PATTERN.search(value) and not _is_allowed_reference_path(
            path
        ):
            raise StacksmithConfigError(
                "Component outputs may only be referenced from component "
                "properties, stack outputs, or operation inputs"
            )
        if _STACK_OUTPUT_TEMPLATE_PATTERN.search(
            value
        ) and not _is_stack_output_transform_path(path):
            raise StacksmithConfigError(
                "`output.name` and `output.value` may only be used in stack "
                "output Jinja transforms"
            )
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and _MARKER_PREFIX in key:
                raise StacksmithConfigError(
                    "Component outputs cannot be used as mapping keys"
                )
            if isinstance(key, str) and (
                _COMPONENT_TEMPLATE_PATTERN.search(key)
                or _STACK_OUTPUT_TEMPLATE_PATTERN.search(key)
            ):
                raise StacksmithConfigError(
                    "Deferred output references cannot be used as mapping keys"
                )
            validate_component_reference_locations(nested, (*path, str(key)))
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            validate_component_reference_locations(
                nested,
                (*path, str(index)),
            )


def _render_reference(
    component_name: str,
    output_name: str,
    stack: "StackDefinition",
    config: "ToolConfig",
    consumer_component_name: str | None,
    cache_dir: "Path | None",
    auth_config: "RemoteAuthConfig | None",
    vendor_dir: "Path | None",
) -> Any:
    from .module_mapping import resolve_module_mapping

    component = stack.components.get(component_name)
    if component is None:
        raise StacksmithConfigError(
            f"Component output reference uses unknown component '{component_name}'"
        )
    if component_name == consumer_component_name:
        raise StacksmithConfigError(
            f"Component '{component_name}' cannot reference its own output "
            f"'{output_name}'"
        )
    mapping = resolve_module_mapping(
        config,
        component.type,
        component_name,
        repository_path=(
            stack.source_path.parent if stack.source_path is not None else None
        ),
    )
    output, module_output = _resolve_component_output(
        output_name,
        mapping,
        component_name,
        component.type,
        config,
        cache_dir,
        auth_config,
        vendor_dir,
    )
    return _apply_output_transform(
        f"${{module.{component_name}.{module_output}}}",
        output,
        output_name,
        module_output,
        component_name,
        component.type,
        stack,
        config,
        cache_dir,
        auth_config,
    )


def _resolve_component_output(
    output_name: str,
    mapping: "ModuleMapping",
    component_name: str,
    component_type: str,
    config: "ToolConfig",
    cache_dir: "Path | None",
    auth_config: "RemoteAuthConfig | None",
    vendor_dir: "Path | None",
) -> tuple[ModuleOutputSpec, str]:
    if output := mapping.outputs.get(output_name):
        return output, output.mapped_from or output_name

    auto_exposed_outputs = _discover_auto_exposed_outputs(
        mapping,
        component_name,
        component_type,
        config,
        cache_dir,
        auth_config,
        vendor_dir,
    )
    if output_name in auto_exposed_outputs:
        return ModuleOutputSpec(), output_name

    raise StacksmithConfigError(
        f"Component '{component_name}' of type '{component_type}' does not "
        f"expose output '{output_name}'. Available outputs: "
        f"{', '.join(sorted(set(mapping.outputs) | auto_exposed_outputs)) or 'none'}"
    )


def _discover_auto_exposed_outputs(
    mapping: "ModuleMapping",
    component_name: str,
    component_type: str,
    config: "ToolConfig",
    cache_dir: "Path | None",
    auth_config: "RemoteAuthConfig | None",
    vendor_dir: "Path | None",
) -> set[str]:
    from .module_mapping import auto_exposed_output_names

    if not mapping.auto_expose_outputs:
        return set()

    try:
        return auto_exposed_output_names(
            mapping,
            discover_module_outputs(
                *render_module_source_identity(
                    mapping.source,
                    options={
                        "base_path": (
                            config.source_path.parent
                            if config.source_path is not None
                            else None
                        )
                    },
                ),
                cache_dir=cache_dir,
                auth_config=auth_config,
                vendor_dir=vendor_dir or get_vendor_dir(),
            ),
        )
    except (OSError, RuntimeError, StacksmithConfigError) as exc:
        raise StacksmithConfigError(
            "Could not discover auto-exposed outputs for component "
            f"'{component_name}' of type '{component_type}': {exc}"
        ) from exc


def _build_output_transform_context(
    output_name: str,
    module_output: str,
    component_name: str,
    component_type: str,
    stack: "StackDefinition",
) -> dict[str, Any]:
    context = {
        "output": {
            "name": output_name,
            "module_output": module_output,
        },
        "component": {
            "name": component_name,
            "type": component_type,
        },
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


def _apply_output_transform(
    value: Any,
    output: "ModuleOutputSpec",
    output_name: str,
    module_output: str,
    component_name: str,
    component_type: str,
    stack: "StackDefinition",
    config: "ToolConfig",
    cache_dir: "Path | None",
    auth_config: "RemoteAuthConfig | None",
) -> Any:
    if output.transform is None:
        return value
    context = _build_output_transform_context(
        output_name,
        module_output,
        component_name,
        component_type,
        stack,
    )
    try:
        if output.transform.jinja is not None:
            return render_jinja_transform(
                output.transform.jinja,
                value,
                context,
                "output",
            )
        return apply_transform(
            output.transform,
            value,
            base_path=(
                config.source_path.parent if config.source_path is not None else None
            ),
            context=context,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )
    except (StacksmithTransformError, TemplateError) as exc:
        raise StacksmithTransformError(
            f"Component '{component_name}' output '{output_name}' transform {exc}"
        ) from exc


def _defer_component_references(value: str) -> str:
    if "{{" not in value or "components" not in value:
        return value
    environment = create_sandboxed_jinja_environment()
    try:
        validate_component_reference_template(environment, value)
        return environment.from_string(value).render(
            {"components": _DeferredComponents()}
        )
    except TemplateError as exc:
        raise StacksmithConfigError(
            f"Could not render deferred component output reference: {exc}"
        ) from exc


def bind_component_references(
    value: Any,
    stack: "StackDefinition",
    config: "ToolConfig",
    consumer_component_name: str | None = None,
    cache_dir: "Path | None" = None,
    auth_config: "RemoteAuthConfig | None" = None,
    vendor_dir: "Path | None" = None,
) -> Any:
    """Bind deferred Jinja component outputs to native Terraform references.

    Args:
        value: Component property or operation input value.
        stack: Final merged stack containing referenced component instances.
        config: Managed configuration declaring public component outputs.
        consumer_component_name: Component receiving the value, used to reject
            self-references.
        cache_dir: Optional cache directory for remote output transform scripts.
        auth_config: Optional remote authentication configuration.
        vendor_dir: Optional vendored module root used for output introspection.

    Returns:
        Value with deferred references replaced recursively.

    Raises:
        StacksmithConfigError: If a raw module reference is used or a deferred
            component/output reference cannot be resolved.
        StacksmithTransformError: If an output transform fails or returns a
            structured value in a string interpolation.
    """
    if isinstance(value, str):
        if _RAW_MODULE_REFERENCE in value:
            raise StacksmithConfigError(
                "Raw Terraform module references are not supported. Use "
                "`{{ components.<component>.<output> }}`."
            )
        deferred = _defer_component_references(value)
        if match := _MARKER_PATTERN.fullmatch(deferred):
            return _render_reference(
                *_decode_reference(match.group(1)),
                stack,
                config,
                consumer_component_name,
                cache_dir,
                auth_config,
                vendor_dir,
            )

        def _replace_reference(match: re.Match[str]) -> str:
            rendered_reference = _render_reference(
                *_decode_reference(match.group(1)),
                stack,
                config,
                consumer_component_name,
                cache_dir,
                auth_config,
                vendor_dir,
            )
            if not isinstance(rendered_reference, str):
                raise StacksmithTransformError(
                    "Component output transforms must return a string when the "
                    "output is interpolated into another string"
                )
            return rendered_reference

        rendered = _MARKER_PATTERN.sub(
            _replace_reference,
            deferred,
        )
        if _MARKER_PREFIX in rendered:
            raise StacksmithConfigError(
                "Stack contains an invalid deferred component output reference"
            )
        return rendered
    if isinstance(value, dict):
        return {
            key: bind_component_references(
                nested,
                stack,
                config,
                consumer_component_name,
                cache_dir,
                auth_config,
                vendor_dir,
            )
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            bind_component_references(
                nested,
                stack,
                config,
                consumer_component_name,
                cache_dir,
                auth_config,
                vendor_dir,
            )
            for nested in value
        ]
    return value
