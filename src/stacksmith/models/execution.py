from pathlib import Path

from pydantic import BaseModel, Field

from ..enums import TerragruntAction


class DependencyPreview(BaseModel):
    """Describe one dependency edge in an execution preview.

    Attributes:
        name: Name of the dependency stack.
        uses_mock_outputs: Whether the selected action can consume mock outputs.
        mock_output_keys: Mock output names exposed by the dependency stack.
    """

    name: str
    uses_mock_outputs: bool = False
    mock_output_keys: list[str] = Field(default_factory=list)


class ExcludedStackPreview(BaseModel):
    """Describe a stack removed by stack-level filtering.

    Attributes:
        name: Name of the excluded stack.
        source_path: Resolved source definition path.
        reason: Human-readable exclusion reason.
    """

    name: str
    source_path: Path
    reason: str


class StackExecutionPreview(BaseModel):
    """Describe the computed execution details for one stack.

    Attributes:
        name: Stack name.
        source_path: Resolved stack definition path.
        dependencies: Dependency edges declared by the stack.
        state_key: Backend state key derived for the stack.
        components: All component names declared by the stack.
        selected_components: Components selected for the requested execution.
        build_directory: Directory that generated files would use.
        terragrunt_args: Terragrunt action arguments that would execute.
        command: Logical Terragrunt command that would execute.
        execution_position: One-based position in the computed execution order.
        selected: Whether the stack would execute.
        skip_reason: Explanation when the stack would not execute.
    """

    name: str
    source_path: Path
    dependencies: list[DependencyPreview] = Field(default_factory=list)
    state_key: str
    components: list[str] = Field(default_factory=list)
    selected_components: list[str] = Field(default_factory=list)
    build_directory: Path
    terragrunt_args: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    execution_position: int | None = None
    selected: bool = True
    skip_reason: str | None = None


class ExecutionPreview(BaseModel):
    """Represent a deterministic dependency and execution preview.

    Attributes:
        schema_version: Version of the machine-readable preview contract.
        action: Requested Terragrunt action.
        root: Root used for stack discovery.
        execution_order: Stack names in their computed execution order.
        stacks: Details for stacks retained after stack-level filtering.
        excluded_stacks: Stacks removed by stack-level filtering.
        would_clean: Whether execution would clean existing build output.
    """

    schema_version: int = 1
    action: TerragruntAction
    root: Path
    execution_order: list[str] = Field(default_factory=list)
    stacks: list[StackExecutionPreview] = Field(default_factory=list)
    excluded_stacks: list[ExcludedStackPreview] = Field(default_factory=list)
    would_clean: bool = False
