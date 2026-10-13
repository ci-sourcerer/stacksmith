import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self

from .exceptions import StacksmithConfigError


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
        remote: Intended remote, or `None` when publication was not requested.
        pushed: Whether the commit was pushed.
        destination_branch: Remote branch, distinct from the local branch.
        push_error: Recoverable push failure after creating the local commit.
        index_error: Failure to synchronize selected index entries after committing.
        paths: Selected repository-relative files for index recovery.
    """

    commit_sha: str
    branch: str
    remote: str | None
    pushed: bool
    destination_branch: str | None = None
    push_error: str | None = None
    index_error: str | None = None
    paths: tuple[str, ...] = ()


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
        repo_path: Path, *args: str, check: bool = True, index: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=repo_path,
                check=check,
                capture_output=True,
                text=True,
                shell=False,
                env=_git_environment(index),
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
        result = self._run_process(
            self.root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if result.returncode == 1:
            return None
        if result.returncode:
            raise StacksmithConfigError(
                result.stderr.strip() or "Cannot resolve local branch."
            )
        return result.stdout.strip()

    def origin_url(self) -> str | None:
        if "origin" not in self._run(self.root, "remote").splitlines():
            return None
        return self._run(self.root, "remote", "get-url", "origin")

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
        entries = status.split("\0")
        changed_paths = []
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry:
                i += 1
                continue
            if len(entry) < 3:
                i += 1
                continue
            xy = entry[:2]
            path = entry[3:]
            changed_paths.append(Path(path))
            if "R" in xy or "C" in xy:
                i += 1  # Skip ORIGINAL_PATH
            i += 1

        return RepositoryStatus(
            root=self.root,
            branch=self.current_branch(),
            head=self.head(),
            remote=self.origin_url(),
            changed_paths=tuple(changed_paths),
        )

    def preflight(
        self,
        paths: list[Path | str],
        message: str,
        expected_head: str,
        remote: str,
        destination_branch: str | None,
        push: bool,
    ) -> tuple[list[str], str, str]:
        if not message.strip():
            raise StacksmithConfigError("Commit message must be a non-empty string.")
        if (
            not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", expected_head)
            or self.head() != expected_head
        ):
            raise StacksmithConfigError(
                "The repository HEAD changed. Review the change against the current commit."
            )
        branch = self.current_branch()
        if branch is None:
            raise StacksmithConfigError(
                "Check out a local branch before publishing changes."
            )
        destination = destination_branch or branch
        self._run(self.root, "check-ref-format", f"refs/heads/{destination}")
        if push and (
            not remote or remote not in self._run(self.root, "remote").splitlines()
        ):
            raise StacksmithConfigError(
                "Publication requires a configured remote name."
            )
        return self._relative_paths(paths), branch, destination

    def commit_selected(
        self,
        paths: list[Path | str],
        message: str,
        expected_head: str,
        remote: str,
        destination_branch: str | None,
        push: bool,
    ) -> CommitPushResult:
        relative_paths, branch, destination = self.preflight(
            paths, message, expected_head, remote, destination_branch, push
        )
        commit_sha = self._create_commit(relative_paths, message, branch, expected_head)
        result = CommitPushResult(
            commit_sha=commit_sha,
            branch=branch,
            remote=remote if push else None,
            destination_branch=destination if push else None,
            pushed=False,
            paths=tuple(relative_paths),
        )
        try:
            self._run(
                self.root,
                "reset",
                "--quiet",
                commit_sha,
                "--",
                *self._pathspecs(relative_paths),
            )
        except StacksmithConfigError as exc:
            result = replace(result, index_error=str(exc))
        return self.push_result(result) if push else result

    def _create_commit(
        self, paths: list[str], message: str, branch: str, expected_head: str
    ) -> str:
        # A private index isolates publication from the user's staged changes.
        with TemporaryDirectory(prefix="stacksmith-index-") as directory:
            index = Path(directory, "index")
            self._run_process(self.root, "read-tree", expected_head, index=index)
            self._run_process(
                self.root, "add", "--", *self._pathspecs(paths), index=index
            )
            tree = self._run_process(
                self.root, "write-tree", index=index
            ).stdout.strip()
            if tree == self._run(self.root, "rev-parse", f"{expected_head}^{{tree}}"):
                raise StacksmithConfigError(
                    "Selected paths do not contain changes to commit."
                )
            commit_sha = self._run(
                self.root, "commit-tree", tree, "-p", expected_head, "-m", message
            )
            if self.current_branch() != branch:
                raise StacksmithConfigError(
                    "The checked-out branch changed during publication."
                )
            self._run(
                self.root,
                "update-ref",
                "-m",
                message,
                f"refs/heads/{branch}",
                commit_sha,
                expected_head,
            )
            return commit_sha

    def push_result(self, result: CommitPushResult) -> CommitPushResult:
        if result.remote is None or result.destination_branch is None:
            raise StacksmithConfigError("This commit has no publication destination.")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", result.commit_sha):
            raise StacksmithConfigError("Publication requires a full commit SHA.")
        try:
            self._run(
                self.root, "check-ref-format", f"refs/heads/{result.destination_branch}"
            )
            if result.remote not in self._run(self.root, "remote").splitlines():
                raise StacksmithConfigError(
                    "Publication requires a configured remote name."
                )
            self._run(self.root, "cat-file", "-e", f"{result.commit_sha}^{{commit}}")
            self._run(
                self.root,
                "push",
                "--",
                result.remote,
                f"{result.commit_sha}:refs/heads/{result.destination_branch}",
            )
        except StacksmithConfigError as exc:
            return replace(result, pushed=False, push_error=str(exc))
        return replace(result, pushed=True, push_error=None)

    @staticmethod
    def _pathspecs(paths: list[str]) -> list[str]:
        return [f":(literal){path}" for path in paths]

    def _relative_paths(self, paths: list[Path | str]) -> list[str]:
        if not paths:
            raise StacksmithConfigError(
                "At least one file must be supplied for publication."
            )
        relative_paths = []
        for path in paths:
            resolved = (self.root / Path(path).expanduser()).resolve()
            try:
                relative = resolved.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise StacksmithConfigError(
                    f"Path '{resolved}' is outside repository '{self.root}'."
                ) from exc
            if (
                relative == "."
                or relative == ".git"
                or relative.startswith(".git/")
                or resolved.is_dir()
            ):
                raise StacksmithConfigError(
                    "Publication paths must be explicit repository files."
                )
            if not resolved.is_file():
                self._run(
                    self.root,
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    *self._pathspecs([relative]),
                )
            relative_paths.append(relative)
        return list(dict.fromkeys(relative_paths))


def repository_status(repo_path: Path | str = ".") -> RepositoryStatus:
    """Return branch, commit, remote, and changed paths for a Git repository.

    Args:
        repo_path: Repository root or a path within the repository.

    Returns:
        A concise repository status snapshot.

    Raises:
        StacksmithConfigError: If repository status cannot be read.
    """
    return _GitRepository.from_path(repo_path).status()


def commit_and_push(
    paths: list[Path | str],
    message: str,
    *,
    expected_head: str,
    repo_path: Path | str = ".",
    remote: str = "origin",
    destination_branch: str | None = None,
    push: bool = False,
) -> CommitPushResult:
    """Commit selected worktree files against a reviewed base and optionally push.

    A private index preserves unrelated staged changes. The local branch advances
    with a compare-and-swap against `expected_head`. Commits use Git plumbing;
    commit hooks and automatic commit signing are not invoked. Serialize worktree
    edits and branch switches while publishing. Remote updates never force-push.

    Args:
        paths: Explicit files relative to the repository root, or absolute paths.
        message: Non-empty commit message.
        expected_head: Full SHA of the reviewed local base commit.
        repo_path: Repository root or directory within the repository.
        remote: Configured remote name used when pushing.
        destination_branch: Remote branch; defaults to the current local branch.
        push: Whether to publish, explicitly opted into by the caller.

    Returns:
        Created commit and publication status. Push and post-commit index failures
        are recorded in `push_error` and `index_error` without losing the commit.

    Raises:
        StacksmithConfigError: If preflight or local commit creation fails.
        OSError: If a private index cannot be created.
    """
    return _GitRepository.from_path(repo_path).commit_selected(
        paths,
        message,
        expected_head,
        remote,
        destination_branch,
        push,
    )


def retry_push(
    result: CommitPushResult, repo_path: Path | str = "."
) -> CommitPushResult:
    """Retry publication of the recorded commit without creating another commit.

    Args:
        result: Previous result containing the exact commit and publication target.
        repo_path: Repository containing the recorded commit.

    Returns:
        Updated result retaining any index error and recording push success or failure.

    Raises:
        StacksmithConfigError: If the commit or publication target is invalid.
    """
    return _GitRepository.from_path(repo_path).push_result(result)


def validate_publication(
    paths: list[Path | str],
    message: str,
    *,
    expected_head: str,
    repo_path: Path | str = ".",
    remote: str = "origin",
    destination_branch: str | None = None,
    push: bool = False,
) -> None:
    """Check repository publication prerequisites without mutating files or refs.

    Args:
        paths: Explicit repository-root-relative or absolute files.
        message: Proposed commit message.
        expected_head: Full SHA of the reviewed local base.
        repo_path: Repository root or directory within it.
        remote: Configured remote name for publication.
        destination_branch: Remote branch, defaulting to the local branch.
        push: Whether a remote destination must be checked.

    Returns:
        None.

    Raises:
        StacksmithConfigError: If publication prerequisites do not hold.
    """
    _GitRepository.from_path(repo_path).preflight(
        paths, message, expected_head, remote, destination_branch, push
    )


def _git_environment(index: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("GIT_INDEX_FILE", None)
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    return environment
