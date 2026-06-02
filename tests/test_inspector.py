import json
import textwrap
from unittest.mock import patch

import pytest
from stacksmith.inspector import (
    InputInfo,
    PlanPolicyInfo,
    ResourceTypeInfo,
    format_json,
    format_table,
    format_yaml,
    inspect_all,
    inspect_resource_type,
)
from stacksmith.introspection import parse_module_variables
from stacksmith.loader import load_config_with_locations
from stacksmith.models import (
    ModuleMapping,
    ModulePropertySpec,
    TransformSpec,
    ValidationSpec,
)


@pytest.fixture
def _simple_mapping() -> ModuleMapping:
    return ModuleMapping(
        description="AWS S3 bucket",
        source={
            "source": "git",
            "data": {
                "repo": "https://github.com/org/terraform-aws-s3.git",
                "ref": "1.0.0",
            },
        },
        auto_inject=False,
        properties={
            "acl": ModulePropertySpec(mapped_to="bucket_acl"),
        },
    )


@pytest.fixture
def _auto_inject_mapping() -> ModuleMapping:
    return ModuleMapping(
        description="AWS EC2 instance",
        source={
            "source": "git",
            "data": {
                "repo": "https://github.com/org/terraform-aws-ec2.git",
                "ref": "2.0.0",
            },
        },
        auto_inject=True,
        properties={
            "tags": ModulePropertySpec(
                validation=ValidationSpec(inline="def validate(value): return True"),
                transform=TransformSpec(
                    script={
                        "source": "local",
                        "data": {"path": "scripts/transform_tags.py"},
                    }
                ),
            ),
        },
    )


def test_inspect_resource_type_basic(_simple_mapping):
    discovered = {"bucket_name", "bucket_acl", "tags"}
    with patch(
        "stacksmith.inspector.discover_module_variables", return_value=discovered
    ):
        result = inspect_resource_type("aws_s3_bucket", _simple_mapping)

    assert result.resource_type == "aws_s3_bucket"
    assert result.display_name == "AWS S3 bucket"
    assert result.module_source == "https://github.com/org/terraform-aws-s3.git"
    assert result.module_version == "1.0.0"
    assert result.auto_inject is False

    names = [i.name for i in result.inputs]
    assert "acl" in names
    assert "bucket_name" in names
    assert "tags" in names


def test_inspect_table_uses_module_description(capsys):
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[],
        )
    ]

    format_table(results, basic=True)
    output = capsys.readouterr().err

    assert "AWS S3 bucket" in output
    assert "aws_s3_bucket" in output


def test_inspect_resource_type_maps_property_spec(_simple_mapping):
    with patch(
        "stacksmith.inspector.discover_module_variables",
        return_value={"bucket_acl", "other"},
    ):
        result = inspect_resource_type("aws_s3_bucket", _simple_mapping)

    acl_input = next(i for i in result.inputs if i.name == "acl")
    assert acl_input.mapped_to == "bucket_acl"
    assert acl_input.module_variable == "bucket_acl"


def test_inspect_resource_type_auto_inject_flag(_auto_inject_mapping):
    discovered = {"instance_type", "tags", "ami"}
    with patch(
        "stacksmith.inspector.discover_module_variables", return_value=discovered
    ):
        result = inspect_resource_type("aws_ec2_instance", _auto_inject_mapping)

    assert result.auto_inject is True
    ami_input = next(i for i in result.inputs if i.name == "ami")
    assert ami_input.auto_inject is True
    assert ami_input.note == "discovered via introspection"


def test_inspect_resource_type_validation_transform_metadata(_auto_inject_mapping):
    with patch(
        "stacksmith.inspector.discover_module_variables", return_value={"tags", "ami"}
    ):
        result = inspect_resource_type(
            "aws_ec2_instance",
            _auto_inject_mapping,
            config_locations={
                (
                    "modules",
                    "aws_ec2_instance",
                    "properties",
                    "tags",
                    "validation",
                ): "config.yaml:10-14",
            },
        )

    tags_input = next(i for i in result.inputs if i.name == "tags")
    assert tags_input.validation == "config.yaml:10-14"
    assert tags_input.transform == "scripts/transform_tags.py"


def test_inspect_resource_type_transform_script_path_is_relative(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        textwrap.dedent("""
            backend:
                type: local
                path: /tmp/state

            tools:
                tofu:
                    version: "1.8.0"
                terragrunt:
                    version: "1.0.6"

            provider_mappings:
                aws:
                    source:
                        source: registry
                        data:
                            address: "hashicorp/aws"
                            version: "~> 5.0"
                    instances:
                        default:
                            config:
                                data:
                                    region: "us-east-1"

            module_mappings:
                aws_s3_bucket:
                    source:
                        source: git
                        data:
                            repo: https://github.com/org/terraform-aws-s3-bucket.git
                            ref: "1.0.0"
                    properties:
                        bucket_name:
                            transform:
                                script:
                                    source: local
                                    data:
                                        path: scripts/transform_bucket_name.py
            """).strip() + "\n",
        encoding="utf-8",
    )
    config, locations = load_config_with_locations([config_path])
    with patch(
        "stacksmith.inspector.discover_module_variables",
        return_value={"bucket_name"},
    ):
        results = inspect_all(
            config,
            cache_dir=None,
            auth_config=None,
            config_locations=locations,
        )

    bucket_name_input = next(
        inp for result in results for inp in result.inputs if inp.name == "bucket_name"
    )
    assert bucket_name_input.transform == "scripts/transform_bucket_name.py"


def test_inspect_resource_type_policy_metadata_renders(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        textwrap.dedent("""
            backend:
                type: local
                path: /tmp/state

            tools:
                tofu:
                    version: "1.8.0"
                terragrunt:
                    version: "1.0.6"

            provider_mappings:
                aws:
                    source:
                        source: registry
                        data:
                            address: "hashicorp/aws"
                            version: "~> 5.0"
                    instances:
                        default:
                            config:
                                data:
                                    region: "us-east-1"

            module_mappings:
                aws_s3_bucket:
                    source:
                        source: git
                        data:
                            repo: https://github.com/org/terraform-aws-s3-bucket.git
                            ref: "1.0.0"
                    properties:
                        acl:
                            validation:
                                script:
                                    source: local
                                    data:
                                        path: scripts/policy.py
            """).strip() + "\n",
        encoding="utf-8",
    )
    config, locations = load_config_with_locations([config_path])
    with patch(
        "stacksmith.inspector.discover_module_variables",
        return_value={"acl"},
    ):
        results = inspect_all(
            config,
            cache_dir=None,
            auth_config=None,
            config_locations=locations,
        )

    acl_input = next(
        inp for result in results for inp in result.inputs if inp.name == "acl"
    )
    assert acl_input.validation == "scripts/policy.py"


def test_load_config_with_locations_reports_validation_block(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        textwrap.dedent("""
            backend:
                type: local
                path: /tmp/state

            tools:
                tofu:
                    version: "1.8.0"
                terragrunt:
                    version: "1.0.6"

            provider_mappings:
                aws:
                    source:
                        source: registry
                        data:
                            address: "hashicorp/aws"
                            version: "~> 5.0"
                    instances:
                        default:
                            config:
                                data:
                                    region: "us-east-1"

            module_mappings:
                aws_s3_bucket:
                    source:
                        source: git
                        data:
                            repo: "https://github.com/example/s3.git"
                            ref: "1.0.0"
                    properties:
                        tags:
                            validation:
                                inline: |
                                    def validate(value):
                                        return True
            """).strip() + "\n",
        encoding="utf-8",
    )
    _, locations = load_config_with_locations([config_path])

    assert (
        "module_mappings",
        "aws_s3_bucket",
        "properties",
        "tags",
        "validation",
    ) in locations
    assert locations[
        ("module_mappings", "aws_s3_bucket", "properties", "tags", "validation")
    ].startswith("stacksmith-config.yaml:")


def test_load_config_with_locations_reports_var_validation_block(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        textwrap.dedent("""
            backend:
                type: local
                path: /tmp/state

            tools:
                tofu:
                    version: "1.8.0"
                terragrunt:
                    version: "1.0.6"

            provider_mappings:
                aws:
                    source:
                        source: registry
                        data:
                            address: "hashicorp/aws"
                            version: "~> 5.0"
                    instances:
                        default:
                            config:
                                data:
                                    region: "us-east-1"

            module_mappings:
                aws_s3_bucket:
                    source:
                        source: git
                        data:
                            repo: https://github.com/org/terraform-aws-s3.git
                            ref: "1.0.0"
                    auto_inject: true
                    properties:
                        bucket_name:
                            mapped_to: bucket

            var_validations:
                aws_region:
                    inline: |
                        def validate(value, **context):
                            return value == "us-east-1"
            """).strip() + "\n",
        encoding="utf-8",
    )
    _, locations = load_config_with_locations([config_path])

    assert ("var_validations", "aws_region") in locations
    assert locations[("var_validations", "aws_region")].startswith(
        "stacksmith-config.yaml:"
    )


def test_inspect_resource_type_uses_var_validation_script_location(tmp_path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        textwrap.dedent("""
            backend:
                type: local
                path: /tmp/state

            tools:
                tofu:
                    version: "1.8.0"
                terragrunt:
                    version: "1.0.6"

            provider_mappings:
                aws:
                    source:
                        source: registry
                        data:
                            address: "hashicorp/aws"
                            version: "~> 5.0"
                    instances:
                        default:
                            config:
                                data:
                                    region: "us-east-1"

            module_mappings:
                aws_s3_bucket:
                    source:
                        source: git
                        data:
                            repo: https://github.com/org/terraform-aws-s3.git
                            ref: "1.0.0"
                    auto_inject: true
                    properties:
                        bucket_name:
                            mapped_to: bucket

            var_validations:
                aws_region:
                    script:
                        source: local
                        data:
                            path: scripts/validate_aws_region.py
            """).strip() + "\n",
        encoding="utf-8",
    )
    config, locations = load_config_with_locations([config_path])
    with patch(
        "stacksmith.inspector.discover_module_variables",
        return_value={"aws_region", "bucket_name"},
    ):
        results = inspect_all(
            config,
            cache_dir=None,
            auth_config=None,
            config_locations=locations,
        )

    aws_region_input = None
    for result in results:
        for inp in result.inputs:
            if inp.name == "aws_region":
                aws_region_input = inp
                break
        if aws_region_input:
            break

    assert aws_region_input is not None
    assert aws_region_input.validation is not None
    assert aws_region_input.validation.endswith("scripts/validate_aws_region.py")


def test_inspect_resource_type_introspection_failure(_simple_mapping):
    with patch(
        "stacksmith.inspector.discover_module_variables",
        side_effect=RuntimeError("clone failed"),
    ):
        result = inspect_resource_type("aws_s3_bucket", _simple_mapping)

    assert result.resource_type == "aws_s3_bucket"
    names = [i.name for i in result.inputs]
    assert "acl" in names


def test_inspect_all_filters_by_resource_type(sample_config_yaml):
    from stacksmith.loader import load_config

    config = load_config([sample_config_yaml])
    with patch("stacksmith.inspector.discover_module_variables", return_value=set()):
        results = inspect_all(config, resource_types=["aws_s3_bucket"])

    assert len(results) == 1
    assert results[0].resource_type == "aws_s3_bucket"


def test_inspect_all_unknown_type_raises(sample_config_yaml):
    from stacksmith.loader import load_config

    config = load_config([sample_config_yaml])
    with pytest.raises(ValueError, match="not configured"):
        with patch(
            "stacksmith.inspector.discover_module_variables", return_value=set()
        ):
            inspect_all(config, resource_types=["nonexistent_type"])


def test_inspect_all_no_filter(sample_config_yaml):
    from stacksmith.loader import load_config

    config = load_config([sample_config_yaml])
    with patch("stacksmith.inspector.discover_module_variables", return_value=set()):
        results = inspect_all(config)

    assert len(results) == len(config.module_mappings)
    types = {r.resource_type for r in results}
    assert types == set(config.module_mappings.keys())


def test_format_json_basic():
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[
                InputInfo(
                    name="bucket_name",
                    module_variable="bucket_name",
                ),
            ],
        )
    ]
    parsed = json.loads(format_json(results))
    assert "aws_s3_bucket" in parsed
    assert parsed["aws_s3_bucket"]["module_source"] == "https://github.com/org/s3.git"
    assert len(parsed["aws_s3_bucket"]["inputs"]) == 1


def test_format_json_includes_tags():
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            tags=["storage", "prod"],
            inputs=[],
        )
    ]
    parsed = json.loads(format_json(results))
    assert parsed["aws_s3_bucket"]["tags"] == ["storage", "prod"]


def test_format_json_details_includes_metadata():
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[
                InputInfo(
                    name="acl",
                    module_variable="bucket_acl",
                    mapped_to="bucket_acl",
                    validation="inline",
                    transform="transform.py",
                ),
            ],
        )
    ]
    parsed = json.loads(format_json(results, details=True))
    inp = parsed["aws_s3_bucket"]["inputs"][0]
    assert inp["validation"] == "inline"
    assert inp["transform"] == "transform.py"
    assert "policy" not in inp


def test_format_json_no_details_omits_metadata():
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[
                InputInfo(
                    name="acl",
                    module_variable="bucket_acl",
                    validation="inline",
                    transform="transform.py",
                ),
            ],
        )
    ]
    parsed = json.loads(format_json(results, details=False))
    inp = parsed["aws_s3_bucket"]["inputs"][0]
    assert "validation" not in inp
    assert "transform" not in inp


def test_format_yaml_produces_valid_yaml():
    import yaml

    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[],
        )
    ]
    output = format_yaml(results)
    data = yaml.safe_load(output)
    assert "aws_s3_bucket" in data


def test_format_table_runs_without_error(capsys):
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=True,
            tags=["storage"],
            inputs=[
                InputInfo(
                    name="bucket_name", module_variable="bucket_name", auto_inject=True
                ),
            ],
        )
    ]
    format_table(results, details=True)


def test_format_table_includes_tags(capsys):
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            tags=["storage", "prod"],
            inputs=[],
        )
    ]
    format_table(results, details=True)
    output = capsys.readouterr().err
    assert "Tags:" in output
    assert "storage, prod" in output


def test_format_table_basic_mode_and_plan_policies(capsys):
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[
                InputInfo(
                    name="bucket_name",
                    module_variable="bucket_name",
                    validation="config.yaml:10-12",
                    transform="scripts/transform.py",
                ),
            ],
        )
    ]
    plan_policies = [
        PlanPolicyInfo(
            name="ec2_requires_imdsv2",
            description="Ensure all EC2 instances require IMDSv2 tokens.",
            location="scripts/validate_ec2_requires_imdsv2.py",
        )
    ]

    format_table(results, basic=True, plan_policies=plan_policies)
    output = capsys.readouterr().err

    assert "Input" in output
    assert "Validation" in output
    assert "Transform" in output
    assert "Mapped To" not in output
    assert "Auto-Inject" not in output
    assert "Plan Policies" not in output


def test_format_table_shows_plan_policies(capsys):
    results = [
        ResourceTypeInfo(
            resource_type="aws_s3_bucket",
            display_name="AWS S3 bucket",
            module_source="https://github.com/org/s3.git",
            module_version="1.0.0",
            auto_inject=False,
            inputs=[],
        )
    ]
    plan_policies = [
        PlanPolicyInfo(
            name="ec2_requires_imdsv2",
            description="Ensure all EC2 instances require IMDSv2 tokens.",
            location="scripts/validate_ec2_requires_imdsv2.py",
        )
    ]

    format_table(results, plan_policies=plan_policies)
    output = capsys.readouterr().err

    assert "Plan Policies" in output
    assert "ec2_requires_imdsv2" in output
    assert "IMDSv2" in output


# .tf.json introspection


def test_parse_module_variables_tf_json(tmp_path):
    tf_json = tmp_path / "variables.tf.json"
    tf_json.write_text(
        json.dumps(
            {
                "variable": {
                    "instance_type": {"default": "t3.micro"},
                    "ami": {},
                }
            }
        )
    )
    result = parse_module_variables(tmp_path)
    assert result == {"instance_type", "ami"}


def test_parse_module_variables_mixed_tf_and_json(tmp_path):
    hcl_content = 'variable "region" {\n  default = "us-east-1"\n}\n'
    (tmp_path / "vars.tf").write_text(hcl_content)
    tf_json = tmp_path / "extra.tf.json"
    tf_json.write_text(
        json.dumps(
            {
                "variable": {
                    "environment": {},
                }
            }
        )
    )
    result = parse_module_variables(tmp_path)
    assert "region" in result
    assert "environment" in result


def test_parse_module_variables_tf_json_list_format(tmp_path):
    tf_json = tmp_path / "variables.tf.json"
    tf_json.write_text(
        json.dumps(
            {
                "variable": [
                    {"name": {"type": "string"}},
                    {"count": {"default": 1}},
                ]
            }
        )
    )
    result = parse_module_variables(tmp_path)
    assert result == {"name", "count"}


def test_parse_module_variables_tf_json_malformed(tmp_path):
    tf_json = tmp_path / "bad.tf.json"
    tf_json.write_text("not valid json")
    result = parse_module_variables(tmp_path)
    assert result == set()
