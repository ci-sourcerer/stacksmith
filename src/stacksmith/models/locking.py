from pathlib import Path

from pydantic import BaseModel, Field


class LockArtifact(BaseModel):
    """One lockable artifact resolved during stack preparation."""

    kind: str
    reference: str
    resolved_path: str
    sha256: str | None = None
    git_commit: str | None = None


class LockContext(BaseModel):
    """Source context used to generate a lockfile."""

    stack_paths: list[str] = Field(default_factory=list)
    config_paths: list[str] = Field(default_factory=list)
    runfile_references: list[str] = Field(default_factory=list)


class StackLockFile(BaseModel):
    """Deterministic lockfile document for Stacksmith inputs and artifacts."""

    schema_version: int = 1
    stacksmith_version: str
    context: LockContext
    artifacts: list[LockArtifact] = Field(default_factory=list)


class LockPolicy(BaseModel):
    """Runtime lock policy flags shared by CLI and API entry points."""

    locked: bool = False
    offline: bool = False
    lockfile: Path | None = None
