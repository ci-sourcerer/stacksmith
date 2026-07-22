import subprocess
from pathlib import Path

import pytest
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.gitops.changes import (
    commit_and_push,
    repository_status,
    request_operation_rerun,
    set_operation_inputs,
    update_component_properties,
    update_operation_rerun_token,
)
from stacksmith.loader import load_stack


def _write_stack(path: Path) -> None:
    path.write_text(
        """# This comment must survive a targeted update.
name: example
components:
  app:
    type: application
    properties:
      image: example:v1
operations:
  deploy:
    use: deployment
    with:
      environment: dev
""",
        encoding="utf-8",
    )


def _run_git(repo_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo_path, check=True, capture_output=True)


def _initialize_repository(repo_path: Path) -> Path:
    repo_path.mkdir()
    _run_git(repo_path, "init")
    _run_git(repo_path, "config", "user.email", "stacksmith@example.com")
    _run_git(repo_path, "config", "user.name", "Stacksmith Test")
    stack_path = repo_path / "stack.yaml"
    _write_stack(stack_path)
    _run_git(repo_path, "add", "stack.yaml")
    _run_git(repo_path, "commit", "-m", "chore(stack): add stack")
    return stack_path


def test_update_operation_rerun_token_preserves_unrelated_yaml_content(tmp_path: Path):
    stack_path = tmp_path / "stack.yaml"
    _write_stack(stack_path)

    change = update_operation_rerun_token(stack_path, "deploy", "release-42")

    assert change.changed
    assert "# This comment must survive a targeted update." in stack_path.read_text()
    assert load_stack(stack_path).operations["deploy"].rerun_token == "release-42"


def test_set_operation_inputs_merges_values(tmp_path: Path):
    stack_path = tmp_path / "stack.yaml"
    _write_stack(stack_path)

    set_operation_inputs(stack_path, "deploy", {"version": "v2"})

    assert load_stack(stack_path).operations["deploy"].with_ == {
        "environment": "dev",
        "version": "v2",
    }


def test_update_component_properties_replaces_json_values(tmp_path: Path):
    stack_path = tmp_path / "stack.json"
    stack_path.write_text(
        '{"name":"example","components":{"app":{"type":"application","properties":{"image":"v1"}}}}',
        encoding="utf-8",
    )

    update_component_properties(stack_path, "app", {"image": "v2"}, replace=True)

    assert load_stack(stack_path).components["app"].properties == {"image": "v2"}


def test_update_operations_rejects_unknown_operation(tmp_path: Path):
    stack_path = tmp_path / "stack.yaml"
    _write_stack(stack_path)

    with pytest.raises(StacksmithConfigError, match="unknown"):
        update_operation_rerun_token(stack_path, "unknown", "release-42")


def test_repository_status_and_commit_only_selected_path(tmp_path: Path):
    stack_path = _initialize_repository(tmp_path / "repo")
    unrelated_path = stack_path.parent / "notes.txt"
    update_operation_rerun_token(stack_path, "deploy", "release-42")
    unrelated_path.write_text("keep me out of the commit\n", encoding="utf-8")

    status = repository_status(stack_path.parent)
    result = commit_and_push(
        repo_path=stack_path.parent,
        paths=[stack_path],
        message="chore(operations): rerun deploy",
        push=False,
    )

    assert status.is_dirty
    assert Path("stack.yaml") in status.changed_paths
    assert result.pushed is False
    assert (
        _git_output(stack_path.parent, "show", "--format=", "--name-only", "HEAD")
        == "stack.yaml"
    )
    assert _git_output(stack_path.parent, "status", "--short") == "?? notes.txt"


def test_request_operation_rerun_commits_without_pushing(tmp_path: Path):
    stack_path = _initialize_repository(tmp_path / "repo")

    result = request_operation_rerun(
        repo_path=stack_path.parent,
        stack_path=stack_path,
        operation="deploy",
        rerun_token="release-42",
        push=False,
    )

    assert result.rerun_token == "release-42"
    assert result.publication is not None
    assert result.publication.pushed is False
    assert _git_output(stack_path.parent, "status", "--short") == ""


def _git_output(repo_path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
