import pytest

from stacksmith.associations import resolve_associations
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import generate_tf_json
from stacksmith.loading import load_config, load_stack
from stacksmith.models import ComponentDefinition, StackDefinition, ToolConfig


def _config(associations: dict) -> ToolConfig:
    return ToolConfig.model_validate(
        {
            "backend": {"data": {"type": "local", "path": ".state"}},
            "module_mappings": {
                "security_group": {
                    "source": {
                        "source": "local",
                        "data": {"path": "/tmp/security-group"},
                    },
                    "tags": ["managed"],
                    "outputs": {"id": {}},
                },
                "instance": {
                    "source": {
                        "source": "local",
                        "data": {"path": "/tmp/instance"},
                    },
                    "outputs": {"id": {}},
                },
                "role": {
                    "source": {
                        "source": "local",
                        "data": {"path": "/tmp/role"},
                    },
                    "outputs": {"id": {}},
                },
            },
            "associations": associations,
        }
    )


def _security_group_rule(
    *,
    cardinality: str = "one_or_more",
    merge: str = "append",
) -> dict:
    return {
        "producers": {
            "select": "component_type == 'security_group' && tag.managed",
            "cardinality": cardinality,
        },
        "consumers": {"select": "component_type == 'instance'"},
        "bindings": [
            {
                "output": "id",
                "property": "security_group_ids",
                "merge": merge,
            }
        ],
    }


def _stack(*, disabled_associations: set[str] | None = None) -> StackDefinition:
    return StackDefinition(
        name="association-test",
        disabled_associations=disabled_associations or set(),
        components={
            "standard": ComponentDefinition(type="security_group"),
            "application": ComponentDefinition(
                type="instance",
                properties={"security_group_ids": ["sg-explicit"]},
            ),
        },
    )


def test_loaders_accept_managed_associations_and_stack_opt_outs(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        """backend:
  data:
    type: local
    path: .state
module_mappings:
  security_group:
    source:
      source: local
      data:
        path: modules/security-group
    outputs:
      id: {}
  instance:
    source:
      source: local
      data:
        path: modules/instance
associations:
  standard-security-groups:
    producers:
      select: component_type == 'security_group'
      cardinality: one_or_more
    consumers:
      select: component_type == 'instance'
    bindings:
      - output: id
        property: security_group_ids
        merge: append
""",
        encoding="utf-8",
    )
    stack_path = tmp_path / "stack.yaml"
    stack_path.write_text(
        """name: association-test
disabled_associations:
  - standard-security-groups
components:
  application:
    type: instance
    disabled_associations:
      - standard-security-groups
""",
        encoding="utf-8",
    )

    assert "standard-security-groups" in load_config(config_path).associations
    stack = load_stack(stack_path)
    assert stack.disabled_associations == {"standard-security-groups"}
    assert stack.components["application"].disabled_associations == {
        "standard-security-groups"
    }


def test_append_association_uses_effective_mapping_tags_and_native_reference():
    config = _config({"standard-security-groups": _security_group_rule()})
    stack = _stack()

    resolution = resolve_associations(stack, config)

    assert resolution.stack.components["application"].properties[
        "security_group_ids"
    ] == [
        "sg-explicit",
        '{{ components["standard"]["id"] }}',
    ]
    assert resolution.stack is not stack
    assert stack.components["application"].properties == {
        "security_group_ids": ["sg-explicit"]
    }
    assert resolution.applications[0].producers == ("standard",)

    generated = generate_tf_json(resolution.stack, config, {})
    assert generated["module"]["application"]["security_group_ids"] == [
        "sg-explicit",
        "${module.standard.id}",
    ]


def test_set_if_absent_injects_scalar_for_singular_cardinality():
    stack = _stack()
    stack.components["application"].properties = {}

    resolution = resolve_associations(
        stack,
        _config(
            {
                "standard-security-group": _security_group_rule(
                    cardinality="exactly_one",
                    merge="set_if_absent",
                )
            }
        ),
    )

    assert (
        resolution.stack.components["application"].properties["security_group_ids"]
        == '{{ components["standard"]["id"] }}'
    )


def test_set_if_absent_preserves_an_explicit_property():
    resolution = resolve_associations(
        _stack(),
        _config(
            {
                "standard-security-group": _security_group_rule(
                    cardinality="exactly_one",
                    merge="set_if_absent",
                )
            }
        ),
    )

    assert resolution.stack.components["application"].properties == {
        "security_group_ids": ["sg-explicit"]
    }
    assert resolution.applications == ()


@pytest.mark.parametrize("scope", ["stack", "component"])
def test_association_can_be_disabled(scope: str):
    stack = _stack(
        disabled_associations=(
            {"standard-security-groups"} if scope == "stack" else None
        )
    )
    if scope == "component":
        stack.components["application"].disabled_associations.add(
            "standard-security-groups"
        )

    resolution = resolve_associations(
        stack,
        _config({"standard-security-groups": _security_group_rule()}),
    )

    assert resolution.stack.components["application"].properties == {
        "security_group_ids": ["sg-explicit"]
    }
    assert resolution.applications == ()


def test_unknown_disabled_association_is_rejected():
    with pytest.raises(StacksmithConfigError, match="unknown managed associations"):
        resolve_associations(
            _stack(disabled_associations={"misspelled"}),
            _config({"standard-security-groups": _security_group_rule()}),
        )


def test_required_producer_cardinality_is_enforced_when_consumers_exist():
    stack = _stack()
    del stack.components["standard"]

    with pytest.raises(StacksmithConfigError, match="matched 0"):
        resolve_associations(
            stack,
            _config({"standard-security-groups": _security_group_rule()}),
        )


def test_binding_must_reference_a_managed_public_output():
    config = _config({"standard-security-groups": _security_group_rule()})
    config.module_mappings["security_group"].outputs = {}

    with pytest.raises(StacksmithConfigError, match="does not expose"):
        resolve_associations(_stack(), config)


def test_invalid_selector_is_rejected_in_managed_config():
    rule = _security_group_rule()
    rule["producers"]["select"] = "contains(tags"

    with pytest.raises(ValueError, match="Invalid association selector"):
        _config({"standard-security-groups": rule})


def test_append_rejects_non_list_explicit_property():
    stack = _stack()
    stack.components["application"].properties["security_group_ids"] = "sg-explicit"

    with pytest.raises(StacksmithConfigError, match="is not a list"):
        resolve_associations(
            stack,
            _config({"standard-security-groups": _security_group_rule()}),
        )


def test_association_cycles_are_rejected():
    config = _config(
        {
            "role-to-instance": {
                "producers": {"select": "component_type == 'role'"},
                "consumers": {"select": "component_type == 'instance'"},
                "bindings": [{"output": "id", "property": "role_id"}],
            },
            "instance-to-role": {
                "producers": {"select": "component_type == 'instance'"},
                "consumers": {"select": "component_type == 'role'"},
                "bindings": [{"output": "id", "property": "instance_id"}],
            },
        }
    )
    stack = StackDefinition(
        name="cycle-test",
        components={
            "application": ComponentDefinition(type="instance"),
            "application_role": ComponentDefinition(type="role"),
        },
    )

    with pytest.raises(StacksmithConfigError, match="circular component dependencies"):
        resolve_associations(stack, config)


def test_multiple_set_if_absent_rules_cannot_set_the_same_property():
    config = _config(
        {
            "first": _security_group_rule(
                cardinality="exactly_one",
                merge="set_if_absent",
            ),
            "second": _security_group_rule(
                cardinality="exactly_one",
                merge="set_if_absent",
            ),
        }
    )
    stack = _stack()
    stack.components["application"].properties = {}

    with pytest.raises(StacksmithConfigError, match="both set"):
        resolve_associations(stack, config)
