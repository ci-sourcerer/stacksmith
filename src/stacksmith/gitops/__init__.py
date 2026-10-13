from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from ..authoring import (
    DocumentChange,
    set_operation_inputs,
    update_component_properties,
    update_operation_rerun_token,
    validate_stack_document,
)
from ..repository import (
    CommitPushResult,
    RepositoryStatus,
    commit_and_push,
    repository_status,
)

__all__ = [
    "CommitPushResult",
    "DocumentChange",
    "OperationRerunResult",
    "RepositoryStatus",
    "commit_and_push",
    "repository_status",
    "request_operation_rerun",
    "set_operation_inputs",
    "update_component_properties",
    "update_operation_rerun_token",
    "validate_stack_document",
]


@dataclass(frozen=True)
class OperationRerunResult:
    """Result of requesting a declarative operation rerun.

    Attributes:
        rerun_token: Token recorded on the operation invocation.
        change: Result of updating the stack document.
        publication: Commit/publication result, or `None` when no commit was needed.
    """

    rerun_token: str
    change: DocumentChange
    publication: CommitPushResult | None


def request_operation_rerun(
    stack_path: Path | str,
    operation: str,
    repo_path: Path | str = ".",
    rerun_token: str | None = None,
    message: str | None = None,
    remote: str = "origin",
    branch: str | None = None,
    push: bool = True,
) -> OperationRerunResult:
    """Record, commit, and optionally publish a declarative operation rerun.

    Args:
        repo_path: Repository root or a path within the repository.
        stack_path: Stack document containing the operation invocation.
        operation: Stack-local operation invocation name.
        rerun_token: Token to record, or a generated UUID when omitted.
        message: Commit message. If `None`, use a conventional operation-rerun message.
        remote: Remote name used when `push` is true.
        branch: Destination branch. If `None`, use the checked-out branch.
        push: Whether to push after committing.

    Returns:
        Token, document change, and optional publication result.
    """
    token = rerun_token or str(uuid4())
    change = update_operation_rerun_token(stack_path, operation, token)
    if not change.changed:
        return OperationRerunResult(token, change, None)
    publication = commit_and_push(
        repo_path=repo_path,
        paths=[change.path],
        message=message or f"chore(operations): rerun {operation}",
        remote=remote,
        branch=branch,
        push=push,
    )
    return OperationRerunResult(token, change, publication)
