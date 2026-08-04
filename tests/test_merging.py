from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import build_operation_module_spec
from stacksmith.loading import (
    load_config,
    load_config_with_locations,
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
    ) == {"a/b": {"~key": {"new": True}}}


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


def test_replaced_config_subtree_prunes_stale_source_locations(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "overlay.yaml"
    config = sample_config_yaml.read_text(encoding="utf-8") + (
        "\nvar_validations:\n"
        "  region:\n"
        "    inline: |\n"
        "      def validate(value):\n"
        "          return True\n"
    )
    config = config.replace(
        "      acl:\n        mapped_to: bucket_acl\n",
        "      old:\n"
        "        validation:\n"
        "          inline: |\n"
        "            def validate(value):\n"
        "                return True\n",
    )
    base.write_text(config, encoding="utf-8")

    overlay.write_text(
        "module_mappings:\n"
        "  aws_s3_bucket:\n"
        "    properties:\n"
        "      new:\n"
        "        validation:\n"
        "          inline: |\n"
        "            def validate(value):\n"
        "                return True\n",
        encoding="utf-8",
    )
    _, locations = load_config_with_locations(
        [base, overlay],
        merge_mode=MergePolicy(
            rules=[
                MergeRule(
                    select=(
                        "scope == 'config' && address == "
                        "'/module_mappings/aws_s3_bucket/properties'"
                    ),
                    mode="override",
                )
            ]
        ),
    )

    assert (
        "module_mappings",
        "aws_s3_bucket",
        "properties",
        "old",
        "validation",
    ) not in locations
    assert (
        "module_mappings",
        "aws_s3_bucket",
        "properties",
        "new",
        "validation",
    ) in locations
    assert ("var_validations", "region") in locations


def test_runfile_rules_do_not_control_their_bootstrap_merge(tmp_path: Path):
    base = tmp_path / "base.yaml"
    base.write_text(
        "stacks:\n  - source: local\n    data:\n      path: ./base-stack.yaml\n",
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
        "stacks:\n  - source: local\n    data:\n      path: ./base-stack.yaml\n",
        encoding="utf-8",
    )
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "stacks:\n  - source: local\n    data:\n      path: ./overlay-stack.yaml\n",
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
                "application_commit": ("4e6f8a20b1c3d5e7f90123456789abcdef012345"),
            }
        },
    ).components["frontend_release"].properties["values_files"] == [
        "examples/gitops-repo/manifests/environments/dev/frontend-values.yaml"
    ]


def test_gitops_example_wires_pinned_commit_to_jenkins_operation():
    examples_root = Path(__file__).parents[1] / "examples"
    runfile = load_runfile(examples_root / "gitops-repo/common/stacksmith.yaml")
    application_commit = yaml.safe_load(
        (examples_root / "gitops-repo/vars/vars.dev.yaml").read_text(encoding="utf-8")
    )["application_commit"]
    stack = load_stacks(
        [Path(reference.data.path) for reference in runfile.stacks],
        merge_mode=MergePolicy(
            default=runfile.merge_mode or "deep",
            rules=runfile.merge_rules,
        ),
        template_context={
            "inputs": {
                "environment": "dev",
                "deployment_name": "app-artifacts-dev",
                "application_commit": application_commit,
            }
        },
    )

    spec = build_operation_module_spec(
        stack,
        load_config([Path(reference.data.path) for reference in runfile.configs]),
        "deploy_app",
    )

    assert spec["runner"] == "jenkins"
    assert spec["username_env"] == "STACKSMITH_JENKINS_USERNAME"
    assert spec["api_token_env"] == "STACKSMITH_JENKINS_API_TOKEN"
    assert spec["parameters"] == {
        "ENVIRONMENT": "dev",
        "RELEASE_TAG": "app-artifacts-dev",
        "GIT_COMMIT": application_commit,
    }


def test_simple_gitops_example_wires_after_apply_echo_operation():
    examples_root = Path(__file__).parents[1] / "examples"
    runfile = load_runfile(examples_root / "gitops-simple-repo/common/stacksmith.yaml")
    stack = load_stacks(
        [Path(reference.data.path) for reference in runfile.stacks],
        template_context={
            "inputs": {
                "environment": "dev",
                "message": "Hello from development",
                "project": "stacksmith",
                "first_name": "first",
                "second_name": "second",
            }
        },
    )

    config = load_config([Path(reference.data.path) for reference in runfile.configs])
    spec = build_operation_module_spec(stack, config, "announce_reconciliation")

    assert config.operations["echo_reconciliation"].trigger == "after_apply"
    assert spec["runner"] == "local"
    assert spec["command"] == [
        "sh",
        "-c",
        'echo "Stacksmith simple GitOps reconciliation completed: environment=$STACKSMITH_OPERATION_ENVIRONMENT message=$STACKSMITH_OPERATION_MESSAGE project=$STACKSMITH_OPERATION_PROJECT"',
    ]
    assert spec["environment"] == {
        "STACKSMITH_OPERATION_ENVIRONMENT": "dev",
        "STACKSMITH_OPERATION_MESSAGE": "Hello from development",
        "STACKSMITH_OPERATION_PROJECT": "stacksmith",
    }
