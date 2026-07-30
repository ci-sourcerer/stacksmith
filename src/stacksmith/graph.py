import html
import json

from .models import ExecutionPreview, StackExecutionPreview


def _node_label(stack: StackExecutionPreview) -> str:
    order = (
        str(stack.execution_position)
        if stack.execution_position is not None
        else "skipped"
    )
    components = ", ".join(stack.selected_components) or "none"
    return "\n".join(
        (
            f"{order}. {stack.name}",
            f"path: {stack.source_path}",
            f"state: {stack.state_key}",
            f"components: {components}",
            f"build: {stack.build_directory}",
        )
    )


def _edge_label(mock_output_keys: list[str], uses_mock_outputs: bool) -> str | None:
    if not uses_mock_outputs:
        return None
    return f"mock outputs: {', '.join(mock_output_keys)}"


def render_execution_preview_dot(preview: ExecutionPreview) -> str:
    """Render an execution preview as Graphviz DOT.

    Args:
        preview: Structured execution preview to render.

    Returns:
        Graphviz DOT document.
    """
    lines = ["digraph stacksmith {", "  rankdir=LR;"]
    for stack in preview.stacks:
        attributes = [f"label={json.dumps(_node_label(stack))}"]
        if not stack.selected:
            attributes.extend(('style="dashed"', 'color="gray"'))
        lines.append(f"  {json.dumps(stack.name)} [{', '.join(attributes)}];")

    for stack in preview.stacks:
        for dependency in stack.dependencies:
            attributes = []
            if edge_label := _edge_label(
                dependency.mock_output_keys,
                dependency.uses_mock_outputs,
            ):
                attributes.append(f"label={json.dumps(edge_label)}")
            suffix = f" [{', '.join(attributes)}]" if attributes else ""
            lines.append(
                f"  {json.dumps(dependency.name)} -> "
                f"{json.dumps(stack.name)}{suffix};"
            )
    lines.append("}")
    return "\n".join(lines)


def render_execution_preview_mermaid(preview: ExecutionPreview) -> str:
    """Render an execution preview as a Mermaid flowchart.

    Args:
        preview: Structured execution preview to render.

    Returns:
        Mermaid flowchart document.
    """
    node_ids = {
        stack.name: f"stack_{index}" for index, stack in enumerate(preview.stacks)
    }
    lines = ["flowchart LR"]
    for stack in preview.stacks:
        label = html.escape(_node_label(stack), quote=True).replace("\n", "<br/>")
        lines.append(f'  {node_ids[stack.name]}["{label}"]')
        if not stack.selected:
            lines.append(
                f"  style {node_ids[stack.name]} stroke-dasharray: 5 5,color:#777"
            )

    for stack in preview.stacks:
        for dependency in stack.dependencies:
            edge = f"  {node_ids[dependency.name]} -->"
            if edge_label := _edge_label(
                dependency.mock_output_keys,
                dependency.uses_mock_outputs,
            ):
                edge += f"|{html.escape(edge_label)}|"
            lines.append(f"{edge} {node_ids[stack.name]}")
    return "\n".join(lines)
