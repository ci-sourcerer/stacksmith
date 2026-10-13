import subprocess
from functools import partial
from pathlib import Path

import pytest

from stacksmith.exceptions import StacksmithConfigError
from stacksmith.gitops import request_operation_rerun
from stacksmith.repository import (
    _GitRepository,
    commit_and_push,
    repository_status,
    retry_push,
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    (root / "stack.yaml").write_text(
        "name: example\noperations:\n  deploy:\n    use: deployment\n"
    )
    (root / "notes.txt").write_text("original\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "chore(stack): initialize stack")
    return root


def test_paths_are_repository_relative_and_unrelated_staging_survives(
    repo, tmp_path, monkeypatch
):
    head = repository_status(repo).head
    (repo / "notes.txt").write_text("staged notes\n")
    _git(repo, "add", "notes.txt")
    (repo / "[selected].txt").write_text("selected\n")
    (repo / "s.txt").write_text("unrelated\n")
    monkeypatch.chdir(tmp_path)
    result = commit_and_push(
        ["[selected].txt"],
        "feat(stack): publish selected file",
        expected_head=head,
        repo_path=repo,
    )
    assert not result.pushed
    assert (
        _git(repo, "show", "--format=", "--name-only", result.commit_sha)
        == "[selected].txt"
    )
    assert _git(repo, "diff", "--cached", "--name-only") == "notes.txt"
    assert (repo / "s.txt").read_text() == "unrelated\n"


def test_stale_base_prevents_rerun_file_and_index_mutations(repo):
    original = (repo / "stack.yaml").read_text()
    before = _git(repo, "status", "--porcelain")
    with pytest.raises(StacksmithConfigError, match="HEAD changed"):
        request_operation_rerun(
            "stack.yaml", "deploy", repo_path=repo, expected_head="0" * 40
        )
    assert (repo / "stack.yaml").read_text() == original
    assert _git(repo, "status", "--porcelain") == before


@pytest.mark.parametrize("destination", ["bad branch", "../bad"])
def test_invalid_destination_prevents_document_mutation(repo, destination):
    original = (repo / "stack.yaml").read_text()
    with pytest.raises(StacksmithConfigError):
        request_operation_rerun(
            "stack.yaml",
            "deploy",
            repo_path=repo,
            expected_head=repository_status(repo).head,
            destination_branch=destination,
        )
    assert (repo / "stack.yaml").read_text() == original


def test_detached_head_is_rejected_before_staging(repo):
    head = repository_status(repo).head
    _git(repo, "checkout", "--detach", head)
    (repo / "new.txt").write_text("new\n")
    with pytest.raises(StacksmithConfigError, match="local branch"):
        commit_and_push(
            ["new.txt"], "feat(stack): add file", expected_head=head, repo_path=repo
        )
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    assert _git(repo, "rev-parse", "HEAD") == head


def test_local_commit_failure_preserves_real_index(repo, monkeypatch):
    head = repository_status(repo).head
    (repo / "notes.txt").write_text("staged\n")
    _git(repo, "add", "notes.txt")
    (repo / "new.txt").write_text("new\n")
    before = _git(repo, "diff", "--cached")
    monkeypatch.setattr(
        _GitRepository,
        "_run",
        staticmethod(partial(_fail_commit_tree, _GitRepository._run)),
    )
    with pytest.raises(StacksmithConfigError, match="commit failed"):
        commit_and_push(
            ["new.txt"], "feat(stack): add file", expected_head=head, repo_path=repo
        )
    assert _git(repo, "diff", "--cached") == before
    assert _git(repo, "rev-parse", "HEAD") == head


def _fail_commit_tree(original, root, *args, **kwargs):
    if args[0] == "commit-tree":
        raise StacksmithConfigError("commit failed")
    return original(root, *args, **kwargs)


def _advance_before_ref_update(original, root, *args, **kwargs):
    if args[0] == "update-ref":
        _git(root, "commit", "--allow-empty", "-m", "chore(stack): concurrent advance")
    return original(root, *args, **kwargs)


def test_concurrent_head_advance_rejects_local_ref_update(repo, monkeypatch):
    head = repository_status(repo).head
    (repo / "new.txt").write_text("new\n")
    monkeypatch.setattr(
        _GitRepository,
        "_run",
        staticmethod(partial(_advance_before_ref_update, _GitRepository._run)),
    )
    with pytest.raises(StacksmithConfigError):
        commit_and_push(
            ["new.txt"], "feat(stack): add file", expected_head=head, repo_path=repo
        )
    assert _git(repo, "log", "-1", "--format=%s") == "chore(stack): concurrent advance"
    assert _git(repo, "diff", "--cached", "--name-only") == ""


def test_tracked_deletion_can_be_committed(repo):
    head = repository_status(repo).head
    (repo / "notes.txt").unlink()
    result = commit_and_push(
        ["notes.txt"], "chore(stack): remove notes", expected_head=head, repo_path=repo
    )
    assert (
        _git(repo, "show", "--format=", "--name-status", result.commit_sha)
        == "D\tnotes.txt"
    )
    assert not repository_status(repo).is_dirty


def test_no_op_publication_preserves_head_and_unrelated_staging(repo):
    head = repository_status(repo).head
    (repo / "notes.txt").write_text("staged\n")
    _git(repo, "add", "notes.txt")
    with pytest.raises(StacksmithConfigError, match="do not contain changes"):
        commit_and_push(
            ["stack.yaml"],
            "chore(stack): no change",
            expected_head=head,
            repo_path=repo,
        )
    assert repository_status(repo).head == head
    assert _git(repo, "diff", "--cached", "--name-only") == "notes.txt"


def test_missing_remote_prevents_rerun_document_mutation(repo):
    original = (repo / "stack.yaml").read_text()
    with pytest.raises(StacksmithConfigError, match="configured remote"):
        request_operation_rerun(
            "stack.yaml",
            "deploy",
            expected_head=repository_status(repo).head,
            repo_path=repo,
            push=True,
        )
    assert (repo / "stack.yaml").read_text() == original


def test_index_lock_reports_created_commit_for_recovery(repo):
    (repo / "new.txt").write_text("new\n")
    lock = repo / ".git" / "index.lock"
    lock.touch()
    result = commit_and_push(
        ["new.txt"],
        "feat(stack): publish",
        expected_head=repository_status(repo).head,
        repo_path=repo,
    )
    assert result.index_error
    assert repository_status(repo).head == result.commit_sha
    lock.unlink()
    _git(repo, "reset", "--quiet", result.commit_sha, "--", *result.paths)
    assert not repository_status(repo).is_dirty


@pytest.mark.parametrize("path", [".", "../outside.txt", ".git/config"])
def test_invalid_publication_paths_leave_head_unchanged(repo, path):
    head = repository_status(repo).head
    with pytest.raises(StacksmithConfigError):
        commit_and_push(
            [path], "feat(stack): publish", expected_head=head, repo_path=repo
        )
    assert repository_status(repo).head == head


def test_failed_push_can_retry_exact_commit_after_local_head_advances(repo, tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    _git(repo, "remote", "add", "origin", str(remote))
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (repo / "new.txt").write_text("new\n")
    result = commit_and_push(
        ["new.txt"],
        "feat(stack): publish",
        expected_head=repository_status(repo).head,
        repo_path=repo,
        destination_branch="proposal",
        push=True,
    )
    assert result.branch == "main"
    assert result.destination_branch == "proposal"
    assert not result.pushed and result.push_error
    assert repository_status(repo).head == result.commit_sha
    _git(repo, "commit", "--allow-empty", "-m", "chore(stack): advance local branch")
    hook.unlink()
    recovered = retry_push(result, repo)
    assert recovered.pushed and recovered.push_error is None
    assert _git(remote, "rev-parse", "refs/heads/proposal") == result.commit_sha
    assert _git(repo, "rev-parse", "HEAD") != result.commit_sha
