from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..enums import MergeMode
from .configuration import FileReference, MergeRule, VariableReference


class StackMeta(BaseModel):
    """Metadata identifying a stack."""

    name: str


class ComponentDefinition(BaseModel):
    """Definition of a single component within a stack."""

    type: str
    tags: set[str] = Field(default_factory=set)
    properties: dict[str, Any] = Field(default_factory=dict)


class OperationInvocation(BaseModel):
    """A stack's request to run an operation approved in managed config."""

    use: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    rerun_token: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class StackDefinition(BaseModel):
    """Complete parsed stack definition from a YAML or JSON file."""

    name: str
    tags: set[str] = Field(default_factory=set)
    depends_on: list[str] = Field(default_factory=list)
    mock_outputs: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, ComponentDefinition] = Field(default_factory=dict)
    operations: dict[str, OperationInvocation] = Field(default_factory=dict)
    source_path: Path | None = Field(default=None, exclude=True)


class RunFile(BaseModel):
    """Stacksmith invocation manifest describing input layers."""

    merge_mode: MergeMode | None = None
    merge_rules: list[MergeRule] = Field(default_factory=list)
    stacks: list[FileReference] = Field(default_factory=list)
    configs: list[FileReference] = Field(default_factory=list)
    vars: list[VariableReference] = Field(default_factory=list)
