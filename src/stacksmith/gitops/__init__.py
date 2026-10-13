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
    retry_push,
    validate_publication,
)

__all__ = [
    "CommitPushResult",
    "DocumentChange",
    "OperationRerunResult",
    "RepositoryStatus",
    "commit_and_push",
    "repository_status",
    "request_operation_rerun",
    "retry_push",
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
    *,
    expected_head: str,
    repo_path: Path | str = ".",
    rerun_token: str | None = None,
    message: str | None = None,
    remote: str = "origin",
    destination_branch: str | None = None,
    push: bool = False,
) -> OperationRerunResult:
    """Record, commit, and optionally publish a declarative operation rerun.

    Args:
        repo_path: Repository root or a path within the repository.
        stack_path: Stack document containing the operation invocation.
        operation: Stack-local operation invocation name.
        rerun_token: Token to record, or a generated UUID when omitted.
        message: Commit message. If `None`, use a conventional operation-rerun message.
        remote: Remote name used when `push` is true.
        expected_head: Full SHA of the reviewed local base commit.
        destination_branch: Remote branch; defaults to the current local branch.
        push: Whether to push after committing.

    Returns:
        Token, document change, and optional publication result. Inspect publication
        errors before treating the operation request as published.

    Raises:
        StacksmithConfigError: If preflight, authoring, or local commit creation fails.
        OSError: If the document cannot be read or written.
    """
    token = rerun_token or str(uuid4())
    validate_publication(
        [stack_path],
        message or f"chore(operations): rerun {operation}",
        expected_head=expected_head,
        repo_path=repo_path,
        remote=remote,
        destination_branch=destination_branch,
        push=push,
    )
    change = update_operation_rerun_token(
        repository_status(repo_path).root / stack_path, operation, token
    )
    if not change.changed:
        return OperationRerunResult(token, change, None)
    publication = commit_and_push(
        repo_path=repo_path,
        paths=[change.path],
        message=message or f"chore(operations): rerun {operation}",
        remote=remote,
        expected_head=expected_head,
        destination_branch=destination_branch,
        push=push,
    )
    return OperationRerunResult(token, change, publication)
