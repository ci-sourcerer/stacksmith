from pathlib import Path

import pytest
from pydantic import ValidationError
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.loader import (
    _merge_config_locations,
    load_runfile,
    load_runfiles,
    load_stacks,
)
from stacksmith.merging import AddressAwareMerger
from stacksmith.models import InlineReference, MergePolicy, MergeRule
from stacksmith.variables import resolve_inputs


def test_address_rule_overrides_only_matching_stack_node(tmp_path: Path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "name: example\n"
        "components:\n"
        "  api:\n"
        "    type: service\n"
        "    tags: [base]\n"
        "    properties:\n"
        "      environment:\n"
        "        KEEP: original\n"
        "        REPLACE: original\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "name: example\n"
        "components:\n"
        "  api:\n"
        "    tags: [overlay]\n"
        "    properties:\n"
        "      environment:\n"
        "        REPLACE: overlay\n",
        encoding="utf-8",
    )

    stack = load_stacks(
        [base, overlay],
        merge_mode=MergePolicy(
            rules=[
                MergeRule(
                    select="scope == 'stack' && address == '/components/api/properties/environment'",
                    mode="override",
                )
            ]
        ),
    )

    assert stack.components["api"].properties["environment"] == {"REPLACE": "overlay"}
    assert stack.components["api"].tags == {"base", "overlay"}


def test_last_matching_rule_wins():
    assert AddressAwareMerger(
        MergePolicy(
            rules=[
                MergeRule(
                    select="scope == 'vars' && address == '/items'",
                    mode="override",
                ),
                MergeRule(select="address == '/items'", mode="deep"),
            ]
        ),
        "vars",
    ).merge({"items": ["base"]}, {"items": ["overlay"]}) == {
        "items": ["base", "overlay"]
    }


def test_json_pointer_address_escapes_mapping_keys():
    assert AddressAwareMerger(
        MergePolicy(
            rules=[MergeRule(select="address == '/a~1b/~0key'", mode="override")]
        ),
        "vars",
    ).merge(
        {"a/b": {"~key": {"old": True}}},
        {"a/b": {"~key": {"new": True}}},
    ) == {
        "a/b": {"~key": {"new": True}}
    }


def test_merge_rule_selector_must_return_boolean():
    with pytest.raises(
        StacksmithConfigError,
        match="must evaluate to a boolean value",
    ):
        AddressAwareMerger(
            MergePolicy(rules=[MergeRule(select="address", mode="override")]),
            "vars",
        ).merge({"setting": "base"}, {"setting": "overlay"})


def test_merge_rule_rejects_invalid_jmespath():
    with pytest.raises(ValidationError, match="Invalid merge rule selector"):
        MergeRule(select="[", mode="override")


def test_variable_rule_overrides_selected_value_only():
    result = resolve_inputs(
        vars_file=[],
        input_layers=[
            (
                "vars",
                InlineReference(
                    source="inline",
                    data={
                        "replace": {"old": True},
                        "preserve": {"old": True},
                    },
                ),
            ),
            (
                "vars",
                InlineReference(
                    source="inline",
                    data={
                        "replace": {"new": True},
                        "preserve": {"new": True},
                    },
                ),
            ),
        ],
        merge_mode=MergePolicy(
            rules=[MergeRule(select="address == '/replace'", mode="override")]
        ),
    )

    assert result["replace"] == {"new": True}
    assert result["preserve"] == {"old": True, "new": True}


def test_replaced_config_subtree_prunes_stale_source_locations():
    assert _merge_config_locations(
        {
            ("module_mappings", "api", "properties", "old", "validation"): "base:1-2",
            ("var_validations", "region"): "base:3-4",
        },
        {("module_mappings", "api", "properties", "new", "validation"): "overlay:1-2"},
        [("module_mappings", "api", "properties")],
    ) == {
        ("module_mappings", "api", "properties", "new", "validation"): "overlay:1-2",
        ("var_validations", "region"): "base:3-4",
    }


def test_runfile_rules_do_not_control_their_bootstrap_merge(tmp_path: Path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "stacks:\n"
        "  - source: local\n"
        "    data:\n"
        "      path: ./base-stack.yaml\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "merge_rules:\n"
        "  - select: scope == 'runfile' && address == '/stacks'\n"
        "    mode: override\n"
        "stacks:\n"
        "  - source: local\n"
        "    data:\n"
        "      path: ./overlay-stack.yaml\n",
        encoding="utf-8",
    )

    runfile = load_runfiles([base, overlay])

    assert [Path(reference.data.path).name for reference in runfile.stacks] == [
        "base-stack.yaml",
        "overlay-stack.yaml",
    ]


def test_external_policy_can_control_runfile_merge(tmp_path: Path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "stacks:\n"
        "  - source: local\n"
        "    data:\n"
        "      path: ./base-stack.yaml\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "stacks:\n"
        "  - source: local\n"
        "    data:\n"
        "      path: ./overlay-stack.yaml\n",
        encoding="utf-8",
    )

    runfile = load_runfiles(
        [base, overlay],
        merge_mode=MergePolicy(
            rules=[
                MergeRule(
                    select="scope == 'runfile' && address == '/stacks'",
                    mode="override",
                )
            ]
        ),
    )

    assert [Path(reference.data.path).name for reference in runfile.stacks] == [
        "overlay-stack.yaml"
    ]


def test_gitops_example_overrides_environment_values_files():
    runfile = load_runfile(
        Path(__file__).parents[1] / "examples/gitops-repo/common/stacksmith.yaml"
    )

    assert load_stacks(
        [Path(reference.data.path) for reference in runfile.stacks],
        merge_mode=MergePolicy(
            default=runfile.merge_mode or "deep",
            rules=runfile.merge_rules,
        ),
        template_context={
            "inputs": {
                "environment": "dev",
                "deployment_name": "example-dev",
                "deployment_tags": "dev",
            }
        },
    ).components["frontend_release"].properties["values_files"] == [
        "examples/gitops-repo/manifests/environments/dev/frontend-values.yaml"
    ]
