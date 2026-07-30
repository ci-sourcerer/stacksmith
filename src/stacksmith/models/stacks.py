import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..enums import MergeMode
from .configuration import FileReference, MergeRule, VariableReference


class StackMeta(BaseModel):
    """Metadata identifying a stack."""

    name: str
    description: str | None = None


class ComponentDefinition(BaseModel):
    """Definition of a single component within a stack."""

    type: str
    description: str | None = None
    tags: set[str] = Field(default_factory=set)
    properties: dict[str, Any] = Field(default_factory=dict)


class OperationInvocation(BaseModel):
    """A stack's request to run an operation approved in managed config."""

    use: str
    description: str | None = None
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    rerun_token: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class StackOutputTransformSpec(BaseModel):
    """Jinja transform applied to a stack output value."""

    description: str | None = None
    jinja: str = Field(min_length=1)


class StackOutputDefinition(BaseModel):
    """Public root output exported by a stack."""

    description: str | None = None
    value: Any
    transform: StackOutputTransformSpec | None = None
    sensitive: bool = False
    mock: Any | None = None


_STACK_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class StackDefinition(BaseModel):
    """Complete parsed stack definition from a YAML or JSON file."""

    name: str
    description: str | None = None
    tags: set[str] = Field(default_factory=set)
    depends_on: list[str] = Field(default_factory=list)
    components: dict[str, ComponentDefinition] = Field(default_factory=dict)
    outputs: dict[str, StackOutputDefinition] = Field(default_factory=dict)
    operations: dict[str, OperationInvocation] = Field(default_factory=dict)
    source_path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate_output_names(self) -> "StackDefinition":
        invalid_names = sorted(
            name for name in self.outputs if not _STACK_OUTPUT_NAME_RE.fullmatch(name)
        )
        if invalid_names:
            raise ValueError(
                "Stack output names must be identifiers containing only letters, "
                f"numbers, and underscores: {', '.join(invalid_names)}"
            )
        return self


class RunFile(BaseModel):
    """Stacksmith invocation manifest describing input layers."""

    description: str | None = None
    merge_mode: MergeMode | None = None
    merge_rules: list[MergeRule] = Field(default_factory=list)
    stacks: list[FileReference] = Field(default_factory=list)
    configs: list[FileReference] = Field(default_factory=list)
    vars: list[VariableReference] = Field(default_factory=list)
