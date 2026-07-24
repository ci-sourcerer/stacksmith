import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import yaml
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError as PydanticValidationError

from ..exceptions import StacksmithConfigError
from ..loader import load_stack


@dataclass(frozen=True)
class DocumentChange:
    """Result of a targeted stack-document update.

    Attributes:
        path: Absolute path to the updated document.
        changed: Whether the document contents changed.
    """

    path: Path
    changed: bool


@dataclass(frozen=True)
class RepositoryStatus:
    """A concise snapshot of a local Git repository.

    Attributes:
        root: Absolute repository root.
        branch: Current branch, or `None` for a detached HEAD.
        head: Current commit SHA.
        remote: The `origin` remote URL, when configured.
        changed_paths: Paths with uncommitted changes relative to the repository root.
    """

    root: Path
    branch: str | None
    head: str
    remote: str | None
    changed_paths: tuple[Path, ...]

    @property
    def is_dirty(self) -> bool:
        """Return whether the repository has uncommitted changes."""
        return bool(self.changed_paths)


@dataclass(frozen=True)
class CommitPushResult:
    """Result of committing and optionally pushing selected paths.

    Attributes:
        commit_sha: SHA of the created commit.
        branch: Branch containing the commit.
        remote: Remote used for publication, if pushed.
        pushed: Whether the commit was pushed.
    """

    commit_sha: str
    branch: str
    remote: str | None
    pushed: bool


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


@dataclass(frozen=True)
class _GitRepository:
    """Repository-scoped git operations for local automation helpers."""

    root: Path

    @classmethod
    def from_path(cls, repo_path: Path | str) -> Self:
        return cls(
            Path(
                cls._run(
                    Path(repo_path).expanduser().resolve(),
                    "rev-parse",
                    "--show-toplevel",
                ).strip()
            ).resolve()
        )

    @staticmethod
    def _run_process(
        repo_path: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=repo_path,
                check=check,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise StacksmithConfigError(
                "Git must be installed to use automation helpers."
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or "Git command failed."
            raise StacksmithConfigError(detail) from exc

    @classmethod
    def _run(cls, repo_path: Path, *args: str, strip_output: bool = True) -> str:
        output = cls._run_process(repo_path, *args).stdout
        return output.strip() if strip_output else output

    def current_branch(self) -> str | None:
        try:
            return self._run(self.root, "symbolic-ref", "--quiet", "--short", "HEAD")
        except StacksmithConfigError:
            return None

    def origin_url(self) -> str | None:
        try:
            return self._run(self.root, "remote", "get-url", "origin")
        except StacksmithConfigError:
            return None

    def head(self) -> str:
        return self._run(self.root, "rev-parse", "HEAD")

    def status(self) -> RepositoryStatus:
        status = self._run(
            self.root,
            "status",
            "--porcelain=v1",
            "-z",
            strip_output=False,
        )
        return RepositoryStatus(
            root=self.root,
            branch=self.current_branch(),
            head=self.head(),
            remote=self.origin_url(),
            changed_paths=tuple(
                Path(entry[3:])
                for entry in status.split("\0")
                if entry and len(entry) > 3
            ),
        )

    def commit_selected(
        self,
        paths: list[Path | str],
        message: str,
        remote: str = "origin",
        branch: str | None = None,
        push: bool = True,
    ) -> CommitPushResult:
        if not message.strip():
            raise StacksmithConfigError("Commit message must be a non-empty string.")

        relative_paths = self._relative_paths(paths)
        self._run(self.root, "add", "--", *relative_paths)
        if not self._has_staged_changes(relative_paths):
            raise StacksmithConfigError(
                "Selected paths do not contain changes to commit."
            )

        destination_branch = branch or self.current_branch()
        if destination_branch is None:
            raise StacksmithConfigError(
                "Specify branch when committing from a detached HEAD."
            )

        self._run(self.root, "commit", "--only", "-m", message, "--", *relative_paths)
        commit_sha = self.head()
        if push:
            self._run(
                self.root, "push", remote, f"HEAD:refs/heads/{destination_branch}"
            )
        return CommitPushResult(
            commit_sha=commit_sha,
            branch=destination_branch,
            remote=remote if push else None,
            pushed=push,
        )

    def _has_staged_changes(self, relative_paths: list[str]) -> bool:
        completed = self._run_process(
            self.root,
            "diff",
            "--cached",
            "--quiet",
            "--",
            *relative_paths,
            check=False,
        )
        if completed.returncode > 1:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "Git command failed."
            )
            raise StacksmithConfigError(detail)
        return completed.returncode == 1

    def _relative_paths(self, paths: list[Path | str]) -> list[str]:
        if not paths:
            raise StacksmithConfigError(
                "At least one path must be supplied for publication."
            )

        relative_paths = []
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            try:
                relative_paths.append(str(resolved.relative_to(self.root)))
            except ValueError as exc:
                raise StacksmithConfigError(
                    f"Path '{resolved}' is outside repository '{self.root}'."
                ) from exc
        return relative_paths


def _read_document(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(text)
    elif suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    else:
        raise StacksmithConfigError(
            f"Unsupported file extension '{suffix}'. Use .yaml, .yml, or .json."
        )
    if not isinstance(data, dict):
        raise StacksmithConfigError(f"File must contain a top-level object: {path}")
    return data, text


def _mapping_value(
    mapping: dict[str, Any], key: str, description: str
) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise StacksmithConfigError(f"{description} must be a mapping.")
    return value


def _yaml_mapping_node_at_path(
    text: str, keys: tuple[str, ...]
) -> yaml.nodes.MappingNode:
    root = yaml.compose(text)
    current: yaml.nodes.Node | None = root
    for key in keys:
        if not isinstance(current, yaml.nodes.MappingNode):
            raise StacksmithConfigError(
                f"Cannot update '{'.'.join(keys)}' because it is not a mapping."
            )
        current = next(
            (
                value_node
                for key_node, value_node in current.value
                if isinstance(key_node, yaml.nodes.ScalarNode) and key_node.value == key
            ),
            None,
        )
    if not isinstance(current, yaml.nodes.MappingNode):
        raise StacksmithConfigError(
            f"Cannot update '{'.'.join(keys)}' because it is not a mapping."
        )
    return current


def _yaml_mapping_value_node(
    mapping: yaml.nodes.MappingNode, key: str
) -> yaml.nodes.Node | None:
    return next(
        (
            value_node
            for key_node, value_node in mapping.value
            if isinstance(key_node, yaml.nodes.ScalarNode) and key_node.value == key
        ),
        None,
    )


def _dump_yaml_value(value: Any, flow_style: bool | None) -> str:
    rendered = yaml.safe_dump(
        value,
        allow_unicode=True,
        default_flow_style=flow_style,
        sort_keys=False,
    ).rstrip()
    return rendered.removesuffix("\n...")


def _replace_yaml_node(text: str, node: yaml.nodes.Node, value: Any) -> str:
    start = node.start_mark.index
    end = node.end_mark.index
    line_start = text.rfind("\n", 0, start) + 1
    indentation = " " * (start - line_start)
    flow_style = (
        node.flow_style if isinstance(node, yaml.nodes.CollectionNode) else None
    )
    replacement = _dump_yaml_value(value, flow_style).replace("\n", f"\n{indentation}")
    return f"{text[:start]}{replacement}{text[end:]}"


def _insert_yaml_mapping_value(
    text: str,
    mapping: yaml.nodes.MappingNode,
    key: str,
    value: Any,
) -> str:
    if mapping.flow_style:
        raise StacksmithConfigError(
            f"Cannot add '{key}' to a flow-style mapping. Convert it to block style first."
        )
    line_end = text.find("\n", mapping.end_mark.index)
    insertion_point = len(text) if line_end == -1 else line_end + 1
    indentation = " " * mapping.start_mark.column
    prefix = (
        "" if insertion_point == 0 or text[:insertion_point].endswith("\n") else "\n"
    )
    if isinstance(value, (dict, list)):
        child_indentation = f"{indentation}  "
        rendered = _dump_yaml_value(value, False).replace(
            "\n", f"\n{child_indentation}"
        )
        insertion = f"{indentation}{key}:\n{child_indentation}{rendered}\n"
    else:
        insertion = f"{indentation}{key}: {_dump_yaml_value(value, None)}\n"
    return f"{text[:insertion_point]}{prefix}{insertion}{text[insertion_point:]}"


def _write_stack_update(
    path: Path,
    original_text: str,
    updated_text: str,
) -> DocumentChange:
    if original_text == updated_text:
        return DocumentChange(path=path.resolve(), changed=False)
    path.write_text(updated_text, encoding="utf-8")
    try:
        validate_stack_document(path)
    except (
        JsonSchemaValidationError,
        PydanticValidationError,
        StacksmithConfigError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ):
        path.write_text(original_text, encoding="utf-8")
        raise
    return DocumentChange(path=path.resolve(), changed=True)


def _update_json_document(data: dict[str, Any], original_text: str) -> str:
    rendered = f"{json.dumps(data, indent=2, ensure_ascii=False)}\n"
    if rendered == original_text:
        return original_text
    return rendered


def validate_stack_document(path: Path | str) -> None:
    """Validate an editable stack document before publication.

    Args:
        path: Stack YAML, YML, or JSON document to validate.

    Raises:
        StacksmithConfigError: If the path is not a valid stack document.
        jsonschema.ValidationError: If the document does not match Stacksmith's schema.
    """
    load_stack(
        Path(path),
        template_context={"inputs": {}, "stack": {"name": "", "tags": []}},
        strict_template_context=False,
    )


def update_operation_rerun_token(
    stack_path: Path | str, operation: str, rerun_token: str
) -> DocumentChange:
    """Set an operation invocation's declarative rerun token.

    Args:
        stack_path: Path to a stack document.
        operation: Stack-local operation invocation name.
        rerun_token: Non-empty value that changes the operation execution identity.

    Returns:
        Details of the document update.

    Raises:
        StacksmithConfigError: If the stack or operation cannot be updated.
    """
    if not rerun_token.strip():
        raise StacksmithConfigError("rerun_token must be a non-empty string.")
    path = Path(stack_path).expanduser().resolve()
    data, text = _read_document(path)
    operations = _mapping_value(data, "operations", "operations")
    invocation = _mapping_value(
        operations, operation, f"Operation '{operation}' invocation"
    )
    if invocation.get("rerun_token") == rerun_token:
        return DocumentChange(path=path, changed=False)
    invocation["rerun_token"] = rerun_token
    if path.suffix.lower() == ".json":
        return _write_stack_update(path, text, _update_json_document(data, text))
    invocation_node = _yaml_mapping_node_at_path(text, ("operations", operation))
    rerun_node = _yaml_mapping_value_node(invocation_node, "rerun_token")
    updated_text = (
        _replace_yaml_node(text, rerun_node, rerun_token)
        if rerun_node is not None
        else _insert_yaml_mapping_value(
            text, invocation_node, "rerun_token", rerun_token
        )
    )
    return _write_stack_update(path, text, updated_text)


def set_operation_inputs(
    stack_path: Path | str,
    operation: str,
    values: dict[str, Any],
    replace: bool = False,
) -> DocumentChange:
    """Merge or replace values in an operation invocation's `with` mapping.

    Args:
        stack_path: Path to a stack document.
        operation: Stack-local operation invocation name.
        values: Input values to set.
        replace: Whether to replace all existing input values instead of merging.

    Returns:
        Details of the document update.

    Raises:
        StacksmithConfigError: If the stack or operation cannot be updated.
    """
    path = Path(stack_path).expanduser().resolve()
    data, text = _read_document(path)
    operations = _mapping_value(data, "operations", "operations")
    invocation = _mapping_value(
        operations, operation, f"Operation '{operation}' invocation"
    )
    existing = invocation.get("with", {})
    if not isinstance(existing, dict):
        raise StacksmithConfigError(
            f"Operation '{operation}' input values must be a mapping."
        )
    updated_values = dict(values) if replace else {**existing, **values}
    if existing == updated_values:
        return DocumentChange(path=path, changed=False)
    invocation["with"] = updated_values
    if path.suffix.lower() == ".json":
        return _write_stack_update(path, text, _update_json_document(data, text))
    invocation_node = _yaml_mapping_node_at_path(text, ("operations", operation))
    inputs_node = _yaml_mapping_value_node(invocation_node, "with")
    updated_text = (
        _replace_yaml_node(text, inputs_node, updated_values)
        if inputs_node is not None
        else _insert_yaml_mapping_value(text, invocation_node, "with", updated_values)
    )
    return _write_stack_update(path, text, updated_text)


def update_component_properties(
    stack_path: Path | str,
    component: str,
    properties: dict[str, Any],
    replace: bool = False,
) -> DocumentChange:
    """Merge or replace properties for a component in a stack document.

    Args:
        stack_path: Path to a stack document.
        component: Component name to update.
        properties: Property values to set.
        replace: Whether to replace all existing properties instead of merging.

    Returns:
        Details of the document update.

    Raises:
        StacksmithConfigError: If the stack or component cannot be updated.
    """
    path = Path(stack_path).expanduser().resolve()
    data, text = _read_document(path)
    components = _mapping_value(data, "components", "components")
    definition = _mapping_value(
        components, component, f"Component '{component}' definition"
    )
    existing = definition.get("properties", {})
    if not isinstance(existing, dict):
        raise StacksmithConfigError(
            f"Component '{component}' properties must be a mapping."
        )
    updated_properties = dict(properties) if replace else {**existing, **properties}
    if existing == updated_properties:
        return DocumentChange(path=path, changed=False)
    definition["properties"] = updated_properties
    if path.suffix.lower() == ".json":
        return _write_stack_update(path, text, _update_json_document(data, text))
    definition_node = _yaml_mapping_node_at_path(text, ("components", component))
    properties_node = _yaml_mapping_value_node(definition_node, "properties")
    updated_text = (
        _replace_yaml_node(text, properties_node, updated_properties)
        if properties_node is not None
        else _insert_yaml_mapping_value(
            text, definition_node, "properties", updated_properties
        )
    )
    return _write_stack_update(path, text, updated_text)


def repository_status(repo_path: Path | str = ".") -> RepositoryStatus:
    """Return branch, commit, remote, and changed paths for a Git repository.

    Args:
        repo_path: Repository root or a path within the repository.

    Returns:
        A concise repository status snapshot.
    """
    return _GitRepository.from_path(repo_path).status()


def commit_and_push(
    paths: list[Path | str],
    message: str,
    repo_path: Path | str = ".",
    remote: str = "origin",
    branch: str | None = None,
    push: bool = True,
) -> CommitPushResult:
    """Commit only selected paths and optionally push the resulting commit.

    Args:
        repo_path: Repository root or a path within the repository.
        paths: Existing repository-relative or absolute files to commit.
        message: Commit message.
        remote: Remote name used when `push` is true.
        branch: Destination branch. Defaults to the checked-out branch.
        push: Whether to push after committing.

    Returns:
        Information about the created commit and optional push.

    Raises:
        StacksmithConfigError: If no selected changes exist or publication cannot proceed.
    """
    return _GitRepository.from_path(repo_path).commit_selected(
        paths=paths,
        message=message,
        remote=remote,
        branch=branch,
        push=push,
    )


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
        message: Commit message. Defaults to a conventional operation-rerun message.
        remote: Remote name used when `push` is true.
        branch: Destination branch. Defaults to the checked-out branch.
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
