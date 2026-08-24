import json
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from stacksmith.exceptions import (
    StacksmithConfigError,
    StacksmithTransformError,
    StacksmithValidationError,
)
from stacksmith.generation import generate_tf_json, write_tf_json
from stacksmith.loading import load_config, load_stack
from stacksmith.models import (
    DefaultModuleMapping,
    ModuleInputSet,
    ModuleInputSpec,
    ModuleMapping,
    ModulePropertySpec,
    ToolConfig,
    TransformSpec,
    ValidationSpec,
    render_module_source_fields,
    render_module_source_identity,
    render_provider_source_fields,
)
from stacksmith.vendor import vendor_path


def _install_fake_boto3(
    monkeypatch: pytest.MonkeyPatch,
    arn: str = "arn:aws:iam::123456789012:user/example",
    raise_error: bool = False,
) -> None:
    fake_boto3 = types.ModuleType("boto3")
    fake_botocore = types.ModuleType("botocore")
    fake_botocore_exceptions = types.ModuleType("botocore.exceptions")

    class BotoCoreError(Exception):
        pass

    class ClientError(Exception):
        pass

    class ProfileNotFound(Exception):
        pass

    class FakeStsClient:
        def get_caller_identity(self) -> dict[str, str]:
            if raise_error:
                raise BotoCoreError("mocked sts failure")
            return {"Arn": arn}

    class FakeSession:
        def __init__(self, profile_name: str | None = None) -> None:
            self.profile_name = profile_name

        def client(self, service_name: str) -> FakeStsClient:
            if service_name != "sts":
                raise ValueError(f"Unsupported service for test fake: {service_name}")
            return FakeStsClient()

    fake_botocore_exceptions.BotoCoreError = BotoCoreError
    fake_botocore_exceptions.ClientError = ClientError
    fake_botocore_exceptions.ProfileNotFound = ProfileNotFound
    fake_boto3.session = types.SimpleNamespace(Session=FakeSession)

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_botocore_exceptions)


def _disable_auto_inject_inputs(config) -> None:
    for mapping in config.module_mappings.values():
        mapping.auto_inject_inputs = False


def _load_shared_example_config():
    config_root = (
        Path(__file__).resolve().parents[1] / "examples" / "shared-config-repo"
    )
    return load_config(
        [
            config_root / "stacksmith-base-config.yaml",
            config_root / "stacksmith-config.yaml",
        ]
    )


class TestGenerateTfJson:
    def test_generate_tf_json_passes_formatter_options(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        _disable_auto_inject_inputs(config)

        module_options_calls: list[dict[str, object] | None] = []
        provider_options_calls: list[dict[str, object] | None] = []

        def _module_formatter(target, source, options=None):
            module_options_calls.append(options)
            return render_module_source_fields(source)

        def _provider_formatter(target, source, options=None):
            provider_options_calls.append(options)
            return render_provider_source_fields(source)

        with (
            patch(
                "stacksmith.generation.terraform.render_module_source_for",
                side_effect=_module_formatter,
            ),
            patch(
                "stacksmith.generation.providers.render_provider_source_for",
                side_effect=_provider_formatter,
            ),
        ):
            generate_tf_json(
                stack,
                config,
                {"bucket_name": "my-bucket-test"},
                formatter_options={
                    "module_source": {"dialect": "terraform"},
                    "provider_source": {"dialect": "terraform"},
                },
            )

        assert module_options_calls
        assert provider_options_calls
        assert all(call["dialect"] == "terraform" for call in module_options_calls)
        assert all("base_path" in call for call in module_options_calls)
        assert all(isinstance(call["base_path"], Path) for call in module_options_calls)
        assert all(call == {"dialect": "terraform"} for call in provider_options_calls)

    def test_terraform_block(self, sample_stack_yaml: Path, sample_config_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        tf = result["terraform"]
        assert tf["required_version"] == "= 1.8.0"
        assert tf["backend"] == {
            "s3": {
                "bucket": "test-state-bucket",
                "region": "us-east-1",
                "key": "my-stack/terraform.tfstate",
            }
        }
        assert tf["required_providers"]["aws"]["source"] == "hashicorp/aws"

    def test_terraform_block_local_backend(
        self, sample_stack_yaml: Path, sample_config_local_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_local_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["terraform"]["backend"] == {
            "local": {"path": "/tmp/stacksmith-state/my-stack/terraform.tfstate"}
        }

    def test_terraform_block_generic_backend(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            textwrap.dedent("""
                backend:
                    data:
                        type: azurerm
                        storage_account_name: testaccount
                        container_name: testcontainer
                        resource_group_name: testrg

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
                                        region: us-east-1

                module_mappings:
                    aws_s3_bucket:
                        source:
                            source: git
                            data:
                                repo: "https://github.com/org/terraform-aws-s3.git"
                                ref: "1.0.0"
                        properties:
                            acl:
                                mapped_to: bucket_acl
                    aws_ec2_instance:
                        source:
                            source: git
                            data:
                                repo: "https://github.com/org/terraform-aws-ec2.git"
                                ref: "2.0.0"
                """).strip()
            + "\n",
            encoding="utf-8",
        )
        config = load_config(config_path)
        stack = load_stack(sample_stack_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["terraform"]["backend"] == {
            "azurerm": {
                "storage_account_name": "testaccount",
                "container_name": "testcontainer",
                "resource_group_name": "testrg",
                "key": "my-stack/terraform.tfstate",
            }
        }

    def test_module_providers_routing_uses_selected_instance(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            textwrap.dedent("""
                                backend:
                                    data:
                                        type: local
                                        path: .state

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
                                            secondary:
                                                alias: "secondary"
                                                config:
                                                    data:
                                                        region: "us-west-2"

                                module_mappings:
                                    aws_s3_bucket:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-s3.git"
                                                ref: "1.0.0"
                                        providers:
                                            aws: aws.secondary
                                    aws_ec2_instance:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-ec2.git"
                                                ref: "2.0.0"
                                """).strip()
            + "\n",
            encoding="utf-8",
        )

        stack = load_stack(sample_stack_yaml)
        config = load_config(config_path)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["provider"]["aws"] == [
            {"region": "us-east-1"},
            {"region": "us-west-2", "alias": "secondary"},
        ]
        assert result["module"]["my-bucket"]["providers"]["aws"] == "aws.secondary"
        assert "providers" not in result["module"]["my-instance"]

    def test_provider_block_includes_empty_default_when_no_default_instance(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            textwrap.dedent("""
                                backend:
                                    data:
                                        type: local
                                        path: .state

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
                                            secondary:
                                                alias: "secondary"
                                                config:
                                                    data:
                                                        region: "us-west-2"

                                module_mappings:
                                    aws_s3_bucket:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-s3.git"
                                                ref: "1.0.0"
                                        providers:
                                            aws: aws.secondary
                                    aws_ec2_instance:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-ec2.git"
                                                ref: "2.0.0"
                                """).strip()
            + "\n",
            encoding="utf-8",
        )

        stack = load_stack(sample_stack_yaml)
        config = load_config(config_path)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["provider"]["aws"] == [
            {},
            {"region": "us-west-2", "alias": "secondary"},
        ]
        assert result["module"]["my-bucket"]["providers"]["aws"] == "aws.secondary"

    def test_provider_config_inline_generates_provider_block(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            textwrap.dedent("""
                                backend:
                                    data:
                                        type: local
                                        path: .state

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
                                                    inline: |
                                                        def config(**context):
                                                                return {
                                                                        "region": context["inputs"]["aws_region"],
                                                                        "assume_role": {
                                                                                "role_arn": context["inputs"]["role_arn"],
                                                                                "external_id": context["inputs"]["external_id"],
                                                                        },
                                                                }

                                module_mappings:
                                    aws_s3_bucket:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-s3.git"
                                                ref: "1.0.0"
                                    aws_ec2_instance:
                                        source:
                                            source: git
                                            data:
                                                repo: "https://github.com/org/terraform-aws-ec2.git"
                                                ref: "2.0.0"
                                """).strip()
            + "\n",
            encoding="utf-8",
        )

        stack = load_stack(sample_stack_yaml)
        config = load_config(config_path)
        result = generate_tf_json(
            stack,
            config,
            {
                "bucket_name": "my-bucket-test",
                "aws_region": "us-west-2",
                "role_arn": "arn:aws:iam::123456789012:role/example",
                "external_id": "external-123",
            },
        )

        assert result["provider"]["aws"][0]["region"] == "us-west-2"
        assert (
            result["provider"]["aws"][0]["assume_role"]["role_arn"]
            == "arn:aws:iam::123456789012:role/example"
        )
        assert (
            result["provider"]["aws"][0]["assume_role"]["external_id"] == "external-123"
        )

    def test_module_blocks(self, sample_stack_yaml: Path, sample_config_yaml: Path):
        stack = load_stack(
            sample_stack_yaml,
            template_context={
                "inputs": {"bucket_name": "my-bucket-gen"},
                "stack": {"name": "my-stack", "tags": ["networking", "storage"]},
            },
        )
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-gen"})

        modules = result["module"]
        assert "my-bucket" in modules
        assert "my-instance" in modules

        bucket_mod = modules["my-bucket"]
        assert (
            bucket_mod["source"]
            == "git::https://github.com/org/terraform-aws-s3.git?ref=1.0.0"
        )
        assert bucket_mod["bucket_acl"] == "private"
        assert bucket_mod["bucket"] == "my-bucket-gen"

    def test_property_name_mapping_applied(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        # "acl" should be renamed to "bucket_acl" per the per-property mapping spec
        bucket_mod = result["module"]["my-bucket"]
        assert "bucket_acl" in bucket_mod
        assert "acl" not in bucket_mod

    def test_unknown_component_type_raises(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\n"
            "components:\n  bad-resource:\n    type: aws_unknown_thing\n"
            "    properties:\n      foo: bar\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)

        with pytest.raises(StacksmithConfigError, match="aws_unknown_thing"):
            generate_tf_json(stack, config, {})

    def test_unknown_component_type_uses_rendered_default_mapping(
        self,
        sample_config_yaml: Path,
        tmp_path: Path,
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\n"
            "components:\n"
            "  checkout:\n"
            "    type: web-service\n"
            "    properties:\n"
            "      replicas: 3\n",
            encoding="utf-8",
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.default_module_mapping = DefaultModuleMapping.model_validate(
            {
                "source": {
                    "source": "git",
                    "data": {
                        "repo": (
                            "https://github.com/org/"
                            "{{ component.type | replace('-', '_') }}"
                            "-{{ component.name }}.git"
                        ),
                        "ref": "latest",
                    },
                }
            }
        )

        result = generate_tf_json(stack, config, {})

        assert result["module"]["checkout"] == {
            "source": (
                "git::https://github.com/org/web_service-checkout.git?ref=latest"
            ),
            "replicas": 3,
        }

    def test_generic_module_source_uses_literal_version(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        mapping_data = config.module_mappings["aws_s3_bucket"].model_dump()
        mapping_data["source"] = {
            "source": "registry",
            "data": {"address": "hashicorp/aws", "version": "5.12.0"},
        }
        config.module_mappings["aws_s3_bucket"] = ModuleMapping.model_validate(
            mapping_data
        )

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-gen"})

        bucket_mod = result["module"]["my-bucket"]
        assert bucket_mod["source"] == "hashicorp/aws"
        assert bucket_mod["version"] == "5.12.0"

    def test_jinja2_in_properties_rendered(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(
            sample_stack_yaml,
            template_context={
                "inputs": {"bucket_name": "my-bucket-jinja"},
                "stack": {"name": "my-stack", "tags": ["networking", "storage"]},
            },
        )
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-jinja"})

        assert result["module"]["my-bucket"]["bucket"] == "my-bucket-jinja"

    def test_component_property_preserves_other_component_output_reference(
        self, tmp_path: Path, sample_config_yaml: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: component-outputs\n"
            "components:\n"
            "  my-bucket:\n"
            "    type: aws_s3_bucket\n"
            "  my-instance:\n"
            "    type: aws_ec2_instance\n"
            "    properties:\n"
            "      bucket_id: '{{ components[\"my-bucket\"].bucket_id }}'\n",
            encoding="utf-8",
        )
        stack = load_stack(
            stack_file,
            template_context={
                "inputs": {},
                "stack": {"name": "component-outputs", "tags": []},
            },
        )
        config = load_config(sample_config_yaml)

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket"})

        assert result["module"]["my-instance"]["bucket_id"] == (
            "${module.my-bucket.s3_bucket_id}"
        )

    def test_jinja2_transform_has_stack_context(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        """Property transform Jinja templates should be able to access stack metadata."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)

        stack.components["my-bucket"].properties["bucket_name"] = "ignored"
        config.module_mappings["aws_s3_bucket"].properties["bucket_name"] = (
            ModulePropertySpec(
                mapped_to="bucket",
                transform=TransformSpec(
                    jinja="{{ stack.name }}-{{ stack.tags | join('-') }}"
                ),
            )
        )

        result = generate_tf_json(stack, config, {"bucket_name": "ignored"})

        assert result["module"]["my-bucket"]["bucket"] == "my-stack-networking-storage"

    def test_jinja2_transform_has_git_repository_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_stack_yaml: Path,
        sample_config_yaml: Path,
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        stack.components["my-bucket"].properties["bucket_name"] = "ignored"
        config.module_mappings["aws_s3_bucket"].properties["bucket_name"] = (
            ModulePropertySpec(
                mapped_to="bucket",
                transform=TransformSpec(jinja="{{ env.git_repository }}"),
            )
        )
        monkeypatch.setattr(
            "stacksmith.generation.terraform.get_current_git_repository",
            lambda path: "https://github.com/example/iac.git",
        )

        result = generate_tf_json(stack, config, {"bucket_name": "ignored"})

        assert result["module"]["my-bucket"]["bucket"] == (
            "https://github.com/example/iac.git"
        )

    def test_example_secondary_provider_skips_assume_role_for_root_identity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_stack_yaml: Path,
    ):
        _install_fake_boto3(monkeypatch, arn="arn:aws:iam::123456789012:root")

        config = _load_shared_example_config()
        _disable_auto_inject_inputs(config)
        stack = load_stack(sample_stack_yaml)

        result = generate_tf_json(
            stack,
            config,
            {
                "bucket_name": "my-bucket-test",
                "aws_profile": "root",
            },
        )

        secondary = next(
            provider
            for provider in result["provider"]["aws"]
            if provider.get("alias") == "secondary"
        )
        assert secondary["region"] == "us-west-2"
        assert "assume_role" not in secondary

    def test_example_secondary_provider_keeps_assume_role_for_non_root_identity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_stack_yaml: Path,
    ):
        _install_fake_boto3(monkeypatch, arn="arn:aws:iam::123456789012:user/example")

        config = _load_shared_example_config()
        _disable_auto_inject_inputs(config)
        stack = load_stack(sample_stack_yaml)

        result = generate_tf_json(
            stack,
            config,
            {
                "bucket_name": "my-bucket-test",
                "aws_profile": "dev",
            },
        )

        secondary = next(
            provider
            for provider in result["provider"]["aws"]
            if provider.get("alias") == "secondary"
        )
        assert secondary["region"] == "us-west-2"
        assert secondary["assume_role"]["role_arn"].endswith("role/my-stack-secondary")
        assert secondary["assume_role"]["external_id"] == "stacksmith-my-stack"

    def test_example_secondary_provider_keeps_assume_role_on_identity_lookup_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_stack_yaml: Path,
    ):
        _install_fake_boto3(monkeypatch, raise_error=True)

        config = _load_shared_example_config()
        _disable_auto_inject_inputs(config)
        stack = load_stack(sample_stack_yaml)

        result = generate_tf_json(
            stack,
            config,
            {
                "bucket_name": "my-bucket-test",
                "aws_profile": "broken",
            },
        )

        secondary = next(
            provider
            for provider in result["provider"]["aws"]
            if provider.get("alias") == "secondary"
        )
        assert secondary["region"] == "us-west-2"
        assert secondary["assume_role"]["session_name"] == "stacksmith-secondary"


class TestPropertyValidation:
    def test_property_validation_passes(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            validation=ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value == 'private' else 'fail'"
                )
            ),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "private"

    def test_property_default_applies_when_property_missing(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        stack.components["my-bucket"].properties.pop("acl", None)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            default="private",
        )

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["module"]["my-bucket"]["bucket_acl"] == "private"

    def test_property_default_runs_through_transform_and_validation(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        stack.components["my-bucket"].properties.pop("acl", None)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            default="private",
            transform=TransformSpec(
                inline="def transform(value, **context): return value.upper()"
            ),
            validation=ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value == 'PRIVATE' else 'fail'"
                )
            ),
        )

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_validation_fails_by_premapping_name(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            validation=ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value == 'public' else 'fail'"
                )
            ),
        )
        with pytest.raises(StacksmithValidationError, match="property 'acl'"):
            generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

    def test_property_validation_fails_by_postmapping_name(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        # Validation on a mapped property name should still work through the combined spec
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            validation=ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value == 'public' else 'fail'"
                )
            ),
        )
        with pytest.raises(StacksmithValidationError, match="property 'acl'"):
            generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

    def test_property_validation_raise_pattern(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\n"
            "components:\n  my-bucket:\n    type: aws_s3_bucket\n"
            "    properties:\n      acl: restricted\n      bucket: test-bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        code = """\
    def validate(value, **context):
        if value not in ('private', 'public-read'):
            raise ValueError(f"acl must be private or public-read, got {value!r}")
    """
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            validation=ValidationSpec(inline=code),
        )
        with pytest.raises(StacksmithValidationError, match="private or public-read"):
            generate_tf_json(stack, config, {})

    def test_property_validation_script_path_resolves_relative_to_config(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        validators_dir = tmp_path / "validators"
        validators_dir.mkdir()
        (validators_dir / "bucket_acl.py").write_text(
            "def validate(value, **context):\n"
            "    return 'pass' if value == 'private' else 'fail'\n",
            encoding="utf-8",
        )
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            "backend:\n"
            "  data:\n"
            "    type: s3\n"
            "    bucket: test-state-bucket\n"
            "    region: us-east-1\n"
            "tools:\n"
            "  tofu:\n"
            "    version: '1.8.0'\n"
            "  terragrunt:\n"
            "    version: '1.0.6'\n"
            "provider_mappings:\n"
            "  aws:\n"
            "    source:\n"
            "      source: registry\n"
            "      data:\n"
            "        address: hashicorp/aws\n"
            "        version: '~> 5.0'\n"
            "    instances:\n"
            "      default:\n"
            "        config:\n"
            "          data:\n"
            "            region: us-east-1\n"
            "module_mappings:\n"
            "  aws_s3_bucket:\n"
            "    source:\n"
            "      source: git\n"
            "      data:\n"
            "        repo: https://github.com/org/terraform-aws-s3.git\n"
            "        ref: '1.0.0'\n"
            "    properties:\n"
            "      acl:\n"
            "        mapped_to: bucket_acl\n"
            "        validation:\n"
            "          script:\n"
            "            source: local\n"
            "            data:\n"
            "              path: validators/bucket_acl.py\n"
            "  aws_ec2_instance:\n"
            "    source:\n"
            "      source: git\n"
            "      data:\n"
            "        repo: https://github.com/org/terraform-aws-ec2.git\n"
            "        ref: '2.0.0'\n",
            encoding="utf-8",
        )
        stack = load_stack(sample_stack_yaml)
        config = load_config(config_file)

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["module"]["my-bucket"]["bucket_acl"] == "private"


class TestPropertyTransform:
    def test_property_transform_jinja_success(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(jinja="{{ property.value | upper }}"),
        )

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_transform_jinja_syntax_error_raises(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(jinja="{{ property.value | }}"),
        )

        with pytest.raises(
            StacksmithTransformError,
            match="property 'acl' transform",
        ):
            generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

    def test_property_transform_inline_success(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(
                inline="""def transform(value, **context): return value.upper() if isinstance(value, str) else value"""
            ),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_transform_function_success(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        code = """\
def transform(value, **context):
    return [value] if isinstance(value, str) else value
"""
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(inline=code),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == ["private"]

    def test_property_transform_missing_function_raises(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        code = "x = 42"
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(inline=code),
        )
        with pytest.raises(
            StacksmithTransformError, match="must define a callable 'transform"
        ):
            generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

    def test_property_transform_by_premapping_name(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(
                inline="""def transform(value, **context): return value.upper()"""
            ),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_transform_by_postmapping_name(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(
                inline="""def transform(value, **context): return value.upper()"""
            ),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_transform_error_raises(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(
                inline="""def transform(value, **context): raise ValueError('transform intentionally failed')"""
            ),
        )
        with pytest.raises(
            StacksmithTransformError,
            match="property 'acl' transform",
        ):
            generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})

    def test_property_transform_script_path_resolves_relative_to_config(
        self, sample_stack_yaml: Path, tmp_path: Path
    ):
        transforms_dir = tmp_path / "transforms"
        transforms_dir.mkdir()
        (transforms_dir / "acl_transform.py").write_text(
            "def transform(value, **context):\n    return value.upper() if isinstance(value, str) else value\n",
            encoding="utf-8",
        )
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            "backend:\n"
            "  data:\n"
            "    type: s3\n"
            "    bucket: test-state-bucket\n"
            "    region: us-east-1\n"
            "tools:\n"
            "  tofu:\n"
            "    version: '1.8.0'\n"
            "  terragrunt:\n"
            "    version: '1.0.6'\n"
            "provider_mappings:\n"
            "  aws:\n"
            "    source:\n"
            "      source: registry\n"
            "      data:\n"
            "        address: hashicorp/aws\n"
            "        version: '~> 5.0'\n"
            "    instances:\n"
            "      default:\n"
            "        config:\n"
            "          data:\n"
            "            region: us-east-1\n"
            "module_mappings:\n"
            "  aws_s3_bucket:\n"
            "    source:\n"
            "      source: git\n"
            "      data:\n"
            "        repo: https://github.com/org/terraform-aws-s3.git\n"
            "        ref: '1.0.0'\n"
            "    properties:\n"
            "      acl:\n"
            "        mapped_to: bucket_acl\n"
            "        transform:\n"
            "          script:\n"
            "            source: local\n"
            "            data:\n"
            "              path: transforms/acl_transform.py\n"
            "  aws_ec2_instance:\n"
            "    source:\n"
            "      source: git\n"
            "      data:\n"
            "        repo: https://github.com/org/terraform-aws-ec2.git\n"
            "        ref: '2.0.0'\n",
            encoding="utf-8",
        )
        stack = load_stack(sample_stack_yaml)
        config = load_config(config_file)

        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"

    def test_property_transform_runs_before_validation(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].properties["acl"] = ModulePropertySpec(
            mapped_to="bucket_acl",
            transform=TransformSpec(
                inline="""def transform(value, **context): return value.upper()"""
            ),
            validation=ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value == 'PRIVATE' else 'fail'"
                )
            ),
        )
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket-test"})
        assert result["module"]["my-bucket"]["bucket_acl"] == "PRIVATE"


class TestWriteTfJson:
    def test_writes_file(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        output = write_tf_json(
            stack, config, {"bucket_name": "my-bucket-write"}, tmp_path
        )

        assert output.exists()
        assert output.name == "stacksmith.tf.json"
        data = json.loads(output.read_text())
        assert "terraform" in data
        assert "module" in data


class TestAutoInjectVars:
    """Auto-inject tests — mock discover_module_variables to control the allowlist."""

    _DISCOVER = "stacksmith.generation.terraform.discover_module_variables"

    def test_auto_inject_inputss_platform_declared_properties(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        """Always-on injection adds platform-declared properties from resolved inputs."""
        stack = load_stack(
            sample_stack_yaml,
            template_context={
                "inputs": {"bucket_name": "my-bucket"},
                "stack": {"name": "my-stack", "tags": ["networking", "storage"]},
            },
        )
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True
        config.module_mappings["aws_s3_bucket"].properties["bucket_name"] = (
            ModulePropertySpec(mapped_to="bucket")
        )
        with patch(
            self._DISCOVER,
            return_value={"bucket", "bucket_name", "ami", "instance_type"},
        ):
            result = generate_tf_json(
                stack,
                config,
                {"bucket_name": "my-bucket"},
            )
        assert result["module"]["my-bucket"]["bucket"] == "my-bucket"

    def test_auto_inject_inputss_discovered_same_name_inputs(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        """Inputs matching discovered module variables are injected without explicit declarations."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True

        with patch(
            self._DISCOVER,
            return_value={"bucket_name", "aws_region", "bucket_acl", "bucket"},
        ):
            result = generate_tf_json(
                stack,
                config,
                {
                    "bucket_name": "my-bucket",
                    "aws_region": "us-east-1",
                    "environment": "dev",
                },
            )

        assert result["module"]["my-bucket"]["bucket_name"] == "my-bucket"
        assert result["module"]["my-bucket"]["aws_region"] == "us-east-1"
        assert "environment" not in result["module"]["my-bucket"]

    def test_auto_inject_inputs_property_opt_out_blocks_injection(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True
        config.module_mappings["aws_s3_bucket"].properties["bucket_name"] = (
            ModulePropertySpec(auto_inject_inputs=False)
        )

        with patch(
            self._DISCOVER,
            return_value={"bucket_name", "aws_region", "bucket_acl", "bucket"},
        ):
            result = generate_tf_json(
                stack,
                config,
                {
                    "bucket_name": "my-bucket",
                    "aws_region": "us-east-1",
                },
            )

        assert "bucket_name" not in result["module"]["my-bucket"]
        assert result["module"]["my-bucket"]["aws_region"] == "us-east-1"

    def test_auto_inject_inputs_vars_explicit_takes_precedence(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        """Explicitly defined properties take precedence over auto-injected vars."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True
        stack.components["my-bucket"].properties["tags"] = "explicit-tags"

        with patch(
            self._DISCOVER, return_value={"tags", "bucket_name", "bucket_acl", "bucket"}
        ):
            result = generate_tf_json(
                stack,
                config,
                {"bucket_name": "my-bucket", "tags": {"env": "prod"}},
            )
        assert result["module"]["my-bucket"]["tags"] == "explicit-tags"

    def test_auto_inject_inputs_is_disabled_by_default(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "my-bucket"})

        assert "bucket_name" not in result["module"]["my-bucket"]

    def test_auto_inject_inputs_mapped_property_uses_mapped_name(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True
        config.module_mappings["aws_s3_bucket"].properties["bucket_name"] = (
            ModulePropertySpec(mapped_to="bucket")
        )

        with patch(self._DISCOVER, return_value={"bucket", "bucket_acl"}):
            result = generate_tf_json(stack, config, {"bucket_name": "my-bucket"})

        assert result["module"]["my-bucket"]["bucket"] == "my-bucket"
        assert "bucket_name" not in result["module"]["my-bucket"]

    def test_auto_inject_inputs_skips_inputs_not_in_discovered_vars(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        """Inputs that don't match any discovered module variable are not injected."""
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.module_mappings["aws_s3_bucket"].auto_inject_inputs = True

        with patch(self._DISCOVER, return_value={"bucket_acl"}):
            result = generate_tf_json(
                stack,
                config,
                {"environment": "dev", "bucket_name": "test"},
            )

        assert "environment" not in result["module"]["my-bucket"]
        assert "bucket_name" not in result["module"]["my-bucket"]

    def test_required_module_input_sets_inject_without_auto_inject_inputs(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.module_input_sets["organization"] = ModuleInputSet(
            inputs={
                "environment": ModuleInputSpec(type="string"),
                "business_unit": ModuleInputSpec(type="string"),
            }
        )
        config.required_module_input_sets = ["organization"]

        with patch(
            self._DISCOVER,
            return_value={"environment", "business_unit", "bucket_acl"},
        ):
            result = generate_tf_json(
                stack,
                config,
                {
                    "environment": "prod",
                    "business_unit": "platform",
                },
            )

        assert result["module"]["my-bucket"]["environment"] == "prod"
        assert result["module"]["my-bucket"]["business_unit"] == "platform"

    def test_required_module_input_sets_require_resolved_input(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.module_input_sets["organization"] = ModuleInputSet(
            inputs={
                "environment": ModuleInputSpec(type="string"),
                "business_unit": ModuleInputSpec(type="string"),
            }
        )
        config.required_module_input_sets = ["organization"]

        with (
            patch(
                self._DISCOVER,
                return_value={"environment", "business_unit", "bucket_acl"},
            ),
            pytest.raises(StacksmithConfigError, match="business_unit"),
        ):
            generate_tf_json(stack, config, {"environment": "prod"})

    def test_required_module_input_sets_generate_declared_module_variable(
        self, sample_config_yaml: Path, tmp_path: Path
    ):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  my-bucket:\n    type: aws_s3_bucket\n"
        )
        stack = load_stack(stack_file)
        config = load_config(sample_config_yaml)
        config.module_input_sets["organization"] = ModuleInputSet(
            inputs={"environment": ModuleInputSpec(type="string")}
        )
        config.required_module_input_sets = ["organization"]

        with patch(self._DISCOVER, return_value={"bucket_acl"}):
            result = generate_tf_json(stack, config, {"environment": "prod"})

        assert result["module"]["my-bucket"]["environment"] == "prod"

    def test_required_module_input_sets_write_generated_module_variables(
        self, tmp_path: Path
    ):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        (module_dir / "main.tf").write_text(
            'output "name" { value = var.environment }\n',
            encoding="utf-8",
        )
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  app:\n    type: app\n",
            encoding="utf-8",
        )
        stack = load_stack(stack_file)
        config = ToolConfig.model_validate(
            {
                "backend": {"data": {"type": "local", "path": ".state"}},
                "module_input_sets": {
                    "organization": {
                        "inputs": {
                            "environment": {
                                "type": "string",
                                "description": "Deployment environment.",
                            }
                        }
                    }
                },
                "module_mappings": {
                    "app": {
                        "source": {
                            "source": "local",
                            "data": {"path": str(module_dir)},
                        },
                        "required_input_sets": ["organization"],
                    }
                },
            }
        )

        output_path = write_tf_json(
            stack,
            config,
            {"environment": "prod"},
            tmp_path / "out",
        )
        generated = json.loads(output_path.read_text(encoding="utf-8"))
        generated_module_dir = Path(generated["module"]["app"]["source"])
        generated_variables = json.loads(
            (generated_module_dir / "stacksmith.generated_variables.tf.json").read_text(
                encoding="utf-8"
            )
        )

        assert generated_module_dir != module_dir
        assert (module_dir / "stacksmith.generated_variables.tf.json").exists() is False
        assert generated["module"]["app"]["environment"] == "prod"
        assert generated_variables["variable"]["environment"] == {
            "type": "string",
            "description": "Deployment environment.",
        }

    def test_required_module_input_sets_preserve_local_package_subdirs(
        self, tmp_path: Path
    ):
        package_dir = tmp_path / "modules"
        module_dir = package_dir / "app"
        child_module_dir = package_dir / "shared" / "child"
        module_dir.mkdir(parents=True)
        child_module_dir.mkdir(parents=True)
        (module_dir / "main.tf").write_text(
            textwrap.dedent("""
                module "child" {
                  source = "../shared/child"
                }

                output "name" {
                  value = var.environment
                }
                """).strip()
            + "\n",
            encoding="utf-8",
        )
        (child_module_dir / "main.tf").write_text(
            'output "id" { value = "child" }\n',
            encoding="utf-8",
        )
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: test-stack\ncomponents:\n  app:\n    type: app\n",
            encoding="utf-8",
        )
        stack = load_stack(stack_file)
        config = ToolConfig.model_validate(
            {
                "backend": {"data": {"type": "local", "path": ".state"}},
                "module_input_sets": {
                    "organization": {
                        "inputs": {
                            "environment": {
                                "type": "string",
                                "description": "Deployment environment.",
                            }
                        }
                    }
                },
                "module_mappings": {
                    "app": {
                        "source": {
                            "source": "local",
                            "data": {"path": f"{package_dir}//app"},
                        },
                        "required_input_sets": ["organization"],
                    }
                },
            }
        )

        output_path = write_tf_json(
            stack,
            config,
            {"environment": "prod"},
            tmp_path / "out",
        )
        generated = json.loads(output_path.read_text(encoding="utf-8"))
        generated_package_path, separator, generated_module_path = generated["module"][
            "app"
        ]["source"].partition("//")
        generated_package_dir = Path(generated_package_path)

        assert separator == "//"
        assert generated_module_path == "app"
        assert (generated_package_dir / "shared" / "child" / "main.tf").is_file()
        assert (
            generated_package_dir / "app" / "stacksmith.generated_variables.tf.json"
        ).is_file()


class TestLocalModuleVendoring:
    def test_vendor_rewrite_uses_local_path(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        """When use_local_modules=True and vendored dir exists, source is rewritten."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)

        # Create vendored directories for both modules
        for mod in config.module_mappings.values():
            source, version = render_module_source_identity(mod.source)
            vendor_path(source, version, tmp_path).mkdir(parents=True)

        result = generate_tf_json(
            stack,
            config,
            {"bucket_name": "test-bucket"},
            use_local_modules=True,
            vendor_dir=tmp_path,
        )

        bucket_mod = result["module"]["my-bucket"]
        expected = str(
            vendor_path(
                *render_module_source_identity(
                    config.module_mappings["aws_s3_bucket"].source
                ),
                tmp_path,
            )
        )
        assert bucket_mod["source"] == expected
        assert "version" not in bucket_mod

    def test_local_module_source_uses_direct_path_without_version(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        mapping_data = config.module_mappings["aws_s3_bucket"].model_dump()
        mapping_data["source"] = {
            "source": "local",
            "data": {"path": "../../examples/modules/helm_app"},
        }
        config.module_mappings["aws_s3_bucket"] = ModuleMapping.model_validate(
            mapping_data
        )
        vendor_path(
            *render_module_source_identity(
                config.module_mappings["aws_ec2_instance"].source
            ),
            tmp_path,
        ).mkdir(parents=True)

        result = generate_tf_json(
            stack,
            config,
            {"bucket_name": "test-bucket"},
            use_local_modules=True,
            vendor_dir=tmp_path,
        )

        bucket_mod = result["module"]["my-bucket"]
        assert bucket_mod["source"] == str(
            (
                Path(__file__).resolve().parent
                / "fixtures"
                / "../../examples/modules/helm_app"
            ).resolve()
        )
        assert "version" not in bucket_mod

    def test_local_module_version_property_is_mapped_to_chart_version(
        self, tmp_path: Path
    ):
        stack_path = tmp_path / "stack.yaml"
        stack_path.write_text(
            'name: test-stack\ncomponents:\n  frontend:\n    type: helm_app\n    properties:\n      chart: ingress-nginx\n      repository: https://kubernetes.github.io/ingress-nginx\n      version: "4.11.3"\n      namespace: default\n'
        )
        stack = load_stack(stack_path)

        config = load_config(
            [
                Path("examples/shared-config-repo/stacksmith-base-config.yaml"),
                Path("examples/shared-config-repo/stacksmith-config.yaml"),
            ]
        )
        config.module_mappings["helm_app"].properties = {
            "version": ModulePropertySpec(mapped_to="chart_version")
        }

        result = generate_tf_json(
            stack,
            config,
            {"bucket_name": "unused"},
        )

        module_block = result["module"]["frontend"]
        assert module_block["chart_version"] == "4.11.3"
        assert "version" not in module_block

    def test_module_path_inputs_are_normalized_to_absolute_paths(self, tmp_path: Path):
        stack_path = tmp_path / "stack.yaml"
        stack_path.write_text(
            (
                "name: test-stack\n"
                "components:\n"
                "  frontend_release:\n"
                "    type: helm_app\n"
                "    properties:\n"
                "      chart: ingress-nginx\n"
                "      repository: https://kubernetes.github.io/ingress-nginx\n"
                '      version: "4.11.3"\n'
                "      namespace: default\n"
                "      values_files:\n"
                "        - examples/gitops-repo/manifests/environments/dev/frontend-values.yaml\n"
                "  app_config:\n"
                "    type: k8s_app\n"
                "    properties:\n"
                "      namespace: default\n"
                "      manifest_files:\n"
                "        - examples/gitops-repo/manifests/environments/dev/app-config.yaml\n"
            ),
            encoding="utf-8",
        )

        stack = load_stack(stack_path)
        config = load_config(
            [
                Path("examples/shared-config-repo/stacksmith-base-config.yaml"),
                Path("examples/shared-config-repo/stacksmith-config.yaml"),
            ]
        )

        result = generate_tf_json(stack, config, {"id": 1234})

        frontend_release = result["module"]["frontend_release"]
        app_config = result["module"]["app_config"]

        assert Path(frontend_release["values_files"][0]).is_absolute()
        assert frontend_release["values_files"][0].endswith("frontend-values.yaml")
        assert Path(frontend_release["values_files"][0]).exists()

        assert Path(app_config["manifest_files"][0]).is_absolute()
        assert app_config["manifest_files"][0].endswith("app-config.yaml")
        assert Path(app_config["manifest_files"][0]).exists()

    def test_vendor_rewrite_disabled_uses_remote(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        """When use_local_modules=False (default), source is remote git URL."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        result = generate_tf_json(stack, config, {"bucket_name": "test-bucket"})

        bucket_mod = result["module"]["my-bucket"]
        assert bucket_mod["source"].startswith("git::")

    def test_vendor_rewrite_fails_when_directory_is_missing(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        """Local-only generation fails instead of fetching a missing module."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)

        with pytest.raises(FileNotFoundError, match="Vendored module not found"):
            generate_tf_json(
                stack,
                config,
                {"bucket_name": "test-bucket"},
                use_local_modules=True,
                vendor_dir=tmp_path,
            )

    def test_vendor_rewrite_deterministic(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        """Calling generate_tf_json twice with the same inputs gives the same result."""
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)

        for mod in config.module_mappings.values():
            source, version = render_module_source_identity(mod.source)
            vendor_path(source, version, tmp_path).mkdir(parents=True)

        result1 = generate_tf_json(
            stack,
            config,
            {"bucket_name": "test"},
            use_local_modules=True,
            vendor_dir=tmp_path,
        )
        result2 = generate_tf_json(
            stack,
            config,
            {"bucket_name": "test"},
            use_local_modules=True,
            vendor_dir=tmp_path,
        )
        assert result1 == result2
