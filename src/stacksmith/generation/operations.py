import hashlib
import json
from pathlib import Path
from typing import Any

from ..component_references import bind_component_references
from ..exceptions import StacksmithConfigError
from ..models import (
    LocalOperationDefinition,
    OperationDefinition,
    OperationInvocation,
    RemoteAuthConfig,
    StackDefinition,
    ToolConfig,
)


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
            "definition": _operation_definition_identity(definition),
            "inputs": values,
            "rerun_token": rerun_token,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _operation_definition_identity(definition: OperationDefinition) -> dict[str, Any]:
    payload = definition.model_dump(mode="json", exclude={"description"})
    for input_specification in payload.get("inputs", {}).values():
        input_specification.pop("description", None)
    return payload


def build_operation_module_spec(
    stack: StackDefinition,
    config: ToolConfig,
    operation_instance_name: str,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    vendor_dir: Path | None = None,
) -> dict[str, Any]:
    """Build an approved runner specification for one operation module.

    Args:
        stack: Stack containing the operation invocation.
        config: Managed configuration containing the approved operation.
        operation_instance_name: Name of the operation invocation in the stack.
        cache_dir: Optional cache directory for remote output transform scripts.
        auth_config: Optional remote authentication configuration.
        vendor_dir: Optional vendored module root used for output introspection.

    Returns:
        Structured operation-runner module specification.
    """
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
    values = bind_component_references(
        invocation.with_,
        stack,
        config,
        cache_dir=cache_dir,
        auth_config=auth_config,
        vendor_dir=vendor_dir,
    )
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
