from pathlib import Path

from .enums import TerragruntAction
from .models import (
    DependencyPreview,
    ExcludedStackPreview,
    ExecutionPreview,
    StackDefinition,
    StackExecutionPreview,
    ToolConfig,
)
from .runner import build_terragrunt_command
from .stack_outputs import build_stack_mock_outputs
from .targeting import build_terragrunt_args, resolve_tag_targets
from .utils import derive_stack_state_key


def _resolve_stack_selection(
    stack: StackDefinition,
    config: ToolConfig,
    tags: list[str] | None,
    tag_expr: str | None,
) -> tuple[list[str], list[str]]:
    if not tags and not tag_expr:
        return sorted(stack.components), []

    _, _, targets = resolve_tag_targets(
        stack,
        config,
        tags=tags,
        tag_expr=tag_expr,
    )
    return [target.removeprefix("module.") for target in targets], targets


def _dependency_previews(
    stack: StackDefinition,
    stacks: dict[str, StackDefinition],
    action: TerragruntAction,
) -> list[DependencyPreview]:
    previews = []
    for dependency in stack.depends_on:
        mock_outputs = build_stack_mock_outputs(stacks[dependency])
        previews.append(
            DependencyPreview(
                name=dependency,
                uses_mock_outputs=(
                    action == TerragruntAction.PLAN and bool(mock_outputs)
                ),
                mock_output_keys=sorted(mock_outputs),
            )
        )
    return previews


def build_execution_preview(
    action: str | TerragruntAction,
    root: Path,
    stacks: dict[str, StackDefinition],
    stack_build_dirs: dict[str, Path],
    config: ToolConfig,
    *,
    state_root: Path | None,
    excluded_stacks: list[ExcludedStackPreview] | None = None,
    tags: list[str] | None = None,
    tag_expr: str | None = None,
    destroy: bool = False,
    auto_approve: bool = False,
    no_cas: bool = False,
    clean: bool = False,
) -> ExecutionPreview:
    """Build a structured preview from prepared stacks.

    Args:
        action: Terragrunt action being previewed.
        root: Root used for stack discovery.
        stacks: Prepared stacks in dependency-first order.
        stack_build_dirs: Build directory for each prepared stack.
        config: Loaded Stacksmith configuration.
        state_root: Root used to derive monorepo state keys.
        excluded_stacks: Stacks removed by stack-level filtering.
        tags: Required component tags.
        tag_expr: Optional component-selection expression.
        destroy: Whether a plan should preview destruction.
        auto_approve: Whether apply or destroy would skip approval.
        no_cas: Whether Terragrunt CAS would be disabled.
        clean: Whether execution would clean existing build output.

    Returns:
        Structured dependency and execution preview.
    """
    action_enum = TerragruntAction(action)
    selections: dict[str, tuple[list[str], list[str]]] = {
        name: _resolve_stack_selection(stack, config, tags, tag_expr)
        for name, stack in stacks.items()
    }
    execution_order = [
        name for name in stacks if selections[name][0] or not (tags or tag_expr)
    ]
    if action_enum == TerragruntAction.DESTROY:
        execution_order.reverse()
    execution_positions = {
        name: position for position, name in enumerate(execution_order, start=1)
    }

    stack_previews = []
    for name, stack in stacks.items():
        if stack.source_path is None:
            raise RuntimeError(f"Stack '{name}' is missing a source path")
        selected_components, targets = selections[name]
        selected = name in execution_positions
        component_descriptions = {
            component_name: component.description
            for component_name, component in sorted(stack.components.items())
            if component.description
        }
        terragrunt_args = (
            build_terragrunt_args(
                action_enum,
                destroy,
                targets=targets or None,
            )
            if selected
            else []
        )
        stack_previews.append(
            StackExecutionPreview(
                name=name,
                description=stack.description,
                source_path=stack.source_path,
                dependencies=_dependency_previews(stack, stacks, action_enum),
                state_key=derive_stack_state_key(
                    stack.name,
                    stack.source_path,
                    state_root,
                ),
                components=sorted(stack.components),
                component_descriptions=component_descriptions,
                selected_components=selected_components,
                build_directory=stack_build_dirs[name],
                terragrunt_args=terragrunt_args,
                command=(
                    build_terragrunt_command(
                        "terragrunt",
                        terragrunt_args,
                        auto_approve=auto_approve,
                        no_cas=no_cas,
                    )
                    if selected
                    else []
                ),
                execution_position=execution_positions.get(name),
                selected=selected,
                skip_reason=(
                    None
                    if selected
                    else "No components matched the requested tag selectors."
                ),
            )
        )

    return ExecutionPreview(
        action=action_enum,
        root=root.resolve(),
        execution_order=execution_order,
        stacks=stack_previews,
        excluded_stacks=excluded_stacks or [],
        would_clean=clean,
    )
