import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .apply_summary import apply_summary_session
from .exceptions import StacksmithError
from .models import ToolConfig
from .runner import run_terragrunt


class ExecutionPlanContext(BaseModel):
    """Portable configuration and identity of a saved execution plan.

    Attributes:
        version: Context schema version.
        created_at: Plan creation time in Unix seconds.
        plan_sha256: Digest of the reviewed binary.
        binding: Digest identifying the CI invocation.
        stack_name: Planned stack name.
        components: Planned component names.
        config: Managed configuration used during planning.
        files: Original generated configuration and provider lockfile contents.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    version: Literal[1] = 1
    created_at: float = Field(default_factory=time.time)
    plan_sha256: str
    binding: str = ""
    stack_name: str
    components: list[str]
    config: dict
    files: dict[str, str]

    @field_validator("files")
    @classmethod
    def _validate_files(cls, files: dict[str, str]) -> dict[str, str]:
        if not {"stacksmith.tf.json", "terragrunt.hcl.json"} <= files.keys():
            raise ValueError("Saved plan is missing generated configuration")
        if any(
            Path(name).name != name
            or name.startswith("..")
            or not name.endswith((".tf", ".tf.json", ".hcl", ".hcl.json"))
            for name in files
        ):
            raise ValueError("Saved plan contains an invalid configuration filename")
        return files


def execution_plan_context_path(plan: Path) -> Path:
    """Return the context sidecar path for a saved execution plan.

    Args:
        plan: Binary execution plan path.

    Returns:
        The context sidecar path.

    Raises:
        None.
    """
    return plan.with_suffix(".context.json")


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600),
        "wb",
    ) as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)


def save_execution_plan_context(
    plan: Path,
    build_dir: Path,
    config: ToolConfig,
    stack_name: str,
    components: list[str],
) -> None:
    """Save the generated configuration used to produce a binary plan.

    Args:
        plan: Successfully generated binary execution plan.
        build_dir: Directory containing its generated configuration.
        config: Tool and remote authentication configuration used during planning.
        stack_name: Planned stack name.
        components: Planned component names.

    Returns:
        None.

    Raises:
        OSError: If the plan or generated files cannot be read or written.
        ValueError: If the generated configuration is incomplete.
    """
    plan.chmod(0o600)
    _write_private(
        execution_plan_context_path(plan),
        ExecutionPlanContext(
            plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),
            stack_name=stack_name,
            components=components,
            config=config.model_dump(mode="json"),
            files={
                path.name: path.read_text(encoding="utf-8")
                for path in build_dir.iterdir()
                if path.is_file()
                and path.name.endswith((".tf", ".tf.json", ".hcl", ".hcl.json"))
            },
        )
        .model_dump_json()
        .encode(),
    )


def _load_context(plan: Path, binding: str) -> ExecutionPlanContext:
    try:
        context = ExecutionPlanContext.model_validate_json(
            execution_plan_context_path(plan).read_bytes()
        )
        if context.binding != binding:
            raise ValueError("Saved plan belongs to another CI invocation")
        if hashlib.sha256(plan.read_bytes()).hexdigest() != context.plan_sha256:
            raise ValueError("Saved plan checksum does not match")
        if time.time() - context.created_at > 86400:
            raise ValueError("Saved plan has expired; run a new preview")
        return context
    except (OSError, ValueError) as error:
        raise StacksmithError(f"Cannot use reviewed plan: {error}") from error


def bind_execution_plan(plan: Path, binding: str) -> None:
    """Bind a newly produced plan to a CI invocation.

    Args:
        plan: Newly produced binary plan with its context sidecar.
        binding: Digest identifying the manifest, environment, revision, and run.

    Returns:
        None.

    Raises:
        StacksmithError: If the new plan is missing, corrupt, or expired.
        OSError: If the context cannot be written.
    """
    context = _load_context(plan, "").model_copy(update={"binding": binding})
    _write_private(
        execution_plan_context_path(plan),
        context.model_dump_json().encode(),
    )
    _write_private(
        plan.with_suffix(".receipt.json"),
        json.dumps(
            {
                "schema_version": 1,
                "plan_sha256": context.plan_sha256,
                "binding": context.binding,
                "created_at": context.created_at,
            }
        ).encode(),
    )


def apply_execution_plan(
    plan: Path,
    binding: str,
    report: Path,
    *,
    destroy: bool = False,
    no_cas: bool = False,
) -> int:
    """Apply a reviewed binary plan with its original generated configuration.

    Args:
        plan: Reviewed binary plan with its context sidecar.
        binding: Expected CI invocation digest.
        report: Applied-change report destination.
        destroy: Whether the binary contains an infrastructure destroy plan.
        no_cas: Whether to disable Terragrunt CAS.

    Returns:
        Process-style apply exit code.

    Raises:
        StacksmithError: If the reviewed plan is missing, corrupt, expired, or mismatched.
        OSError: If generated files cannot be restored.
    """
    context = _load_context(plan, binding)
    for path in plan.parent.iterdir():
        if (
            path.is_file()
            and path.name.endswith((".tf", ".tf.json", ".hcl", ".hcl.json"))
            and path.name not in context.files
        ):
            path.unlink()
    for name, content in context.files.items():
        _write_private(plan.parent / name, content.encode())
    config = ToolConfig.model_validate(context.config)
    with apply_summary_session(
        True,
        report,
        {context.stack_name: context.components},
        "destroy" if destroy else "apply",
        "table",
        {"reviewed_plan_sha256": context.plan_sha256},
    ) as summary:
        summary.exit_code = run_terragrunt(
            ["apply", str(plan.resolve())],
            plan.parent,
            config=config,
            stack_name=context.stack_name,
            cache_dir=plan.parent / ".cache",
            auth_config=config.remote_auth or None,
            no_cas=no_cas,
        )
        return summary.exit_code


def _cipher() -> Fernet:
    try:
        return Fernet(os.environ.get("STACKSMITH_PLAN_ENCRYPTION_KEY", "").encode())
    except ValueError as error:
        raise StacksmithError(
            "STACKSMITH_PLAN_ENCRYPTION_KEY must be a Fernet key"
        ) from error


def transfer_execution_plan(plan: Path, binding: str, *, decrypt: bool) -> None:
    """Encrypt or restore a binary plan and its context for CI artifact transfer.

    Args:
        plan: Binary plan destination or source path.
        binding: Expected CI invocation digest.
        decrypt: Whether to restore an encrypted bundle instead of creating one.

    Returns:
        None.

    Raises:
        StacksmithError: If the key, bundle, or reviewed plan is invalid.
        OSError: If bundle files cannot be read or written.
    """
    bundle = plan.with_suffix(".enc")
    if not decrypt:
        context = _load_context(plan, binding)
        _write_private(
            bundle,
            _cipher().encrypt(
                json.dumps(
                    {
                        "context": context.model_dump(mode="json"),
                        "plan": base64.b64encode(plan.read_bytes()).decode(),
                    }
                ).encode()
            ),
        )
        return
    try:
        payload = json.loads(_cipher().decrypt(bundle.read_bytes(), ttl=86400))
        context = ExecutionPlanContext.model_validate(payload["context"])
        binary = base64.b64decode(payload["plan"], validate=True)
        if (
            context.binding != binding
            or hashlib.sha256(binary).hexdigest() != context.plan_sha256
        ):
            raise ValueError("Encrypted plan does not match this invocation")
    except (InvalidToken, ValueError, KeyError, TypeError) as error:
        raise StacksmithError(
            "Encrypted reviewed plan is invalid or expired"
        ) from error
    _write_private(plan, binary)
    _write_private(
        execution_plan_context_path(plan), context.model_dump_json().encode()
    )
