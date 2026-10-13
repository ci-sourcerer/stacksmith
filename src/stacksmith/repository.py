import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from loguru import logger as LOGGER

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
        remote: Remote used for publication, if pushed.
        pushed: Whether the commit was pushed.
    """

    commit_sha: str
    branch: str
    remote: str | None
    pushed: bool


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

        LOGGER.info(
            "Committing {count} selected path(s) on branch {branch}",
            count=len(relative_paths),
            branch=destination_branch,
        )
        self._run(self.root, "commit", "--only", "-m", message, "--", *relative_paths)
        commit_sha = self.head()
        if push:
            LOGGER.info(
                "Pushing commit {commit_sha} to remote {remote}",
                commit_sha=commit_sha,
                remote=remote,
            )
            self._run(
                self.root, "push", remote, f"HEAD:refs/heads/{destination_branch}"
            )
        LOGGER.info(
            "Created commit {commit_sha} on branch {branch}",
            commit_sha=commit_sha,
            branch=destination_branch,
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
        branch: Destination branch. If `None`, use the checked-out branch.
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
