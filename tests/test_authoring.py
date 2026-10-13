import json
from pathlib import Path

import pytest
import yaml

from stacksmith.authoring import StackEdit, author_stack, update_component_properties
from stacksmith.exceptions import StacksmithConfigError

SOURCE = """# Keep this comment.
name: example
components:
  app:
    type: application
    properties:
      image: "example:v1" # Image comment.
      settings: {first: 1, second: 2}
outputs:
  address:
    value: "{{ components.app.address }}"
operations:
  deploy:
    use: deployment
    with:
      environment: dev
"""


def test_proposal_preserves_authored_comments_styles_and_expressions():
    result = author_stack(
        SOURCE,
        [
            StackEdit(
                action="merge",
                path=("components", "app", "properties"),
                value={"image": "example:v2"},
            )
        ],
    )

    assert "# Keep this comment." in result.source
    assert 'image: "example:v2" # Image comment.' in result.source
    assert "settings: {first: 1, second: 2}" in result.source
    assert 'value: "{{ components.app.address }}"' in result.source
    assert result.changed
    assert '-      image: "example:v1"' in result.diff
    assert '+      image: "example:v2"' in result.diff


def test_no_op_returns_exact_source_including_crlf():
    source = SOURCE.replace("\n", "\r\n")
    assert (
        author_stack(
            source, [StackEdit(action="set", path=("name",), value="example")]
        ).source
        == source
    )
    assert not author_stack(source, []).changed


def test_ordered_edits_cover_component_creation_removal_and_operation_inputs():
    result = author_stack(
        SOURCE,
        [
            StackEdit(
                action="set", path=("components", "worker"), value={"type": "worker"}
            ),
            StackEdit(action="remove", path=("components", "app")),
            StackEdit(
                action="merge",
                path=("operations", "deploy", "with"),
                value={"version": "v2"},
            ),
            StackEdit(
                action="set",
                path=("operations", "deploy", "rerun_token"),
                value="release-42",
            ),
        ],
    )
    document = yaml.safe_load(result.source)
    assert document["components"] == {"worker": {"type": "worker"}}
    assert document["operations"]["deploy"]["with"] == {
        "environment": "dev",
        "version": "v2",
    }
    assert document["operations"]["deploy"]["rerun_token"] == "release-42"


def test_merge_is_shallow_and_preserves_comments_on_surviving_keys():
    result = author_stack(
        SOURCE,
        [
            StackEdit(
                action="merge",
                path=("components", "app", "properties"),
                value={"settings": {"first": 3}},
            )
        ],
    )
    assert "second" not in result.source
    assert 'image: "example:v1" # Image comment.' in result.source


def test_ordered_edits_do_not_mutate_the_callers_edit_values():
    edit = StackEdit(action="set", path=("metadata",), value={"owner": "team"})
    result = author_stack(
        SOURCE,
        [edit, StackEdit(action="set", path=("metadata", "owner"), value="other")],
    )
    assert edit.value == {"owner": "team"}
    assert "owner: other" in result.source


def test_file_no_op_preserves_crlf_bytes(tmp_path):
    path = tmp_path / "stack.yaml"
    path.write_bytes(SOURCE.replace("\n", "\r\n").encode())
    original = path.read_bytes()
    assert not update_component_properties(path, "app", {"image": "example:v1"}).changed
    assert path.read_bytes() == original


def test_json_edits_remain_json_and_leave_original_source_untouched():
    source = '{"name":"example","components":{"app":{"type":"app"}}}'
    result = author_stack(
        source,
        [
            StackEdit(
                action="set",
                path=("components", "app", "properties"),
                value={"count": 2},
            )
        ],
        "stack.json",
    )
    assert json.loads(result.source)["components"]["app"]["properties"] == {"count": 2}
    assert source == '{"name":"example","components":{"app":{"type":"app"}}}'


@pytest.mark.parametrize(
    "source",
    [
        "name: example\ncomponents: &components {}\n",
        "{% if inputs.enabled %}\nname: example\n{% endif %}",
        "name: example\nmetadata: {date: 2026-10-12}\n",
        "name: example\nmetadata: {number: .inf}\n",
    ],
)
def test_unsupported_source_is_rejected_without_execution(source):
    with pytest.raises(StacksmithConfigError):
        author_stack(source, [])


def test_invalid_proposal_never_changes_file(tmp_path: Path):
    path = tmp_path / "stack.yaml"
    path.write_text(SOURCE)
    with pytest.raises(StacksmithConfigError):
        update_component_properties(path, "app", {"date": float("inf")})
    assert path.read_text() == SOURCE


@pytest.mark.parametrize(
    "edit",
    [
        StackEdit(action="set", path=(), value="invalid"),
        StackEdit(action="set", path=("name", "child"), value="invalid"),
        StackEdit(action="merge", path=("name",), value={}),
        StackEdit(action="set", path=("unsupported",), value=True),
    ],
)
def test_invalid_paths_and_proposals_are_rejected(edit):
    with pytest.raises(StacksmithConfigError):
        author_stack(SOURCE, [edit])
