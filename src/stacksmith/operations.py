"""Compilation helpers for Stacksmith-native operation modules."""

import hashlib
import json
from pathlib import Path
from typing import Any

from jinja2.sandbox import SandboxedEnvironment

from .exceptions import StacksmithConfigError
from .models import (
    LocalOperationDefinition,
    OperationDefinition,
    OperationInvocation,
    StackDefinition,
    ToolConfig,
)
from .utils import render_jinja_template_values

_JINJA_ENV = SandboxedEnvironment()


def _validate_invocation(
    definition: OperationDefinition,
    invocation: OperationInvocation,
) -> None:
    unknown = sorted(set(invocation.with_) - set(definition.inputs))
    if unknown:
        raise StacksmithConfigError(
            f"Operation has undeclared inputs: {', '.join(unknown)}"
        )
    missing = sorted(
        name
        for name, specification in definition.inputs.items()
        if specification.required and name not in invocation.with_
    )
    if missing:
        raise StacksmithConfigError(
            f"Operation is missing required inputs: {', '.join(missing)}"
        )


def _resolve_invocation_inputs(
    stack: StackDefinition,
    invocation: OperationInvocation,
    resolved_inputs: dict[str, Any],
) -> dict[str, Any]:
    return render_jinja_template_values(
        invocation.with_,
        {
            "inputs": resolved_inputs,
            "stack": {"name": stack.name, "tags": sorted(stack.tags)},
        },
        jinja_env=_JINJA_ENV,
    )


def _execution_identity(
    stack: StackDefinition,
    operation_name: str,
    definition: OperationDefinition,
    values: dict[str, Any],
    rerun_token: str | None,
) -> str:
    encoded = json.dumps(
        {
            "stack": stack.name,
            "operation": operation_name,
            "definition": definition.model_dump(mode="json"),
            "inputs": values,
            "rerun_token": rerun_token,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_operation_module_spec(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    operation_instance_name: str,
) -> dict[str, Any]:
    """Build a structured, approved runner specification for one operation module."""
    invocation = stack.operations.get(operation_instance_name)
    if invocation is None:
        raise StacksmithConfigError(
            f"Stack '{stack.name}' does not define operation '{operation_instance_name}'"
        )
    definition = config.operations.get(invocation.use)
    if definition is None:
        raise StacksmithConfigError(
            f"Operation '{invocation.use}' is not defined in the tool configuration"
        )
    _validate_invocation(definition, invocation)
    values = _resolve_invocation_inputs(stack, invocation, resolved_inputs)
    spec: dict[str, Any] = {
        "identity": _execution_identity(
            stack, invocation.use, definition, values, invocation.rerun_token
        ),
        "runner": definition.runner,
    }
    if isinstance(definition, LocalOperationDefinition):
        base = (
            config.source_path.parent if config.source_path is not None else Path.cwd()
        )
        spec.update(
            {
                "command": definition.command,
                "environment": {
                    name: str(values[input_name])
                    for name, input_name in definition.environment.items()
                },
                "working_directory": str(
                    (base / definition.working_directory).resolve()
                    if definition.working_directory
                    else base
                ),
            }
        )
    else:
        spec.update(
            {
                "url": definition.url,
                "job_name": definition.job_name,
                "username_env": definition.username_env,
                "api_token_env": definition.api_token_env,
                "parameters": {
                    name: str(values[input_name])
                    for name, input_name in definition.parameters.items()
                },
            }
        )
    return spec
