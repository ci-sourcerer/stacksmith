from io import StringIO
from pathlib import Path

import pytest
from jsonschema import ValidationError
from loguru import logger as LOGGER
from stacksmith.loader import load_config, load_runfile, load_stack, load_stacks


def _s3_config_yaml(
    *,
    backend_bucket: str = "test-state-bucket",
    backend_region: str = "us-east-1",
    provider_version: str = "~> 5.0",
    provider_region: str = "us-east-1",
    include_instances: bool = True,
    module_body: str = "",
    extra_body: str = "",
) -> str:
    provider_instances = (
        "    instances:\n"
        "      default:\n"
        "        config:\n"
        "          data:\n"
        f"            region: {provider_region}\n"
        if include_instances
        else ""
    )
    return (
        "backend:\n"
        "  type: s3\n"
        f"  bucket: {backend_bucket}\n"
        f"  region: {backend_region}\n"
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
        f"        version: '{provider_version}'\n"
        f"{provider_instances}"
        "module_mappings:\n"
        "  aws_s3_bucket:\n"
        "    source:\n"
        "      source: git\n"
        "      data:\n"
        "        repo: https://github.com/org/terraform-aws-s3.git\n"
        "        ref: '1.0.0'\n"
        f"{module_body}"
        f"{extra_body}"
    )


def _local_config_yaml(
    *,
    module_body: str = "",
    extra_body: str = "",
) -> str:
    return (
        "backend:\n"
        "  type: local\n"
        "  path: .state\n"
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
        "          inline: |\n"
        "            def config(**context):\n"
        "                return {'region': 'us-east-1'}\n"
        "module_mappings:\n"
        "  aws_s3_bucket:\n"
        "    source:\n"
        "      source: git\n"
        "      data:\n"
        "        repo: https://github.com/org/terraform-aws-s3.git\n"
        "        ref: '1.0.0'\n"
        f"{module_body}"
        f"{extra_body}"
    )


def _provider_override_yaml(
    *,
    version: str,
    module_body: str = "",
    extra_body: str = "",
) -> str:
    return (
        "provider_mappings:\n"
        "  aws:\n"
        "    source:\n"
        "      source: registry\n"
        "      data:\n"
        "        address: hashicorp/aws\n"
        f"        version: '{version}'\n"
        f"{module_body}"
        f"{extra_body}"
    )


class TestLoadStack:
    def test_load_yaml(self, sample_stack_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        assert stack.name == "my-stack"
        assert "my-bucket" in stack.components
        assert stack.components["my-bucket"].type == "aws_s3_bucket"

    def test_load_json(self, sample_stack_json: Path):
        stack = load_stack(sample_stack_json)
        assert stack.name == "my-stack"

    def test_tags_loaded(self, sample_stack_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        assert isinstance(stack.tags, set)
        assert "networking" in stack.tags

    def test_resource_tags_loaded(self, tmp_path: Path):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: tagged-stack\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    tags:\n"
            "      - prod\n"
            "      - data\n"
        )

        stack = load_stack(stack_file)

        assert stack.components["bucket"].tags == {"prod", "data"}

    def test_yaml_and_json_produce_same_model(
        self, sample_stack_yaml: Path, sample_stack_json: Path
    ):
        yaml_stack = load_stack(sample_stack_yaml)
        json_stack = load_stack(sample_stack_json)
        assert yaml_stack.name == json_stack.name
        assert yaml_stack.components.keys() == json_stack.components.keys()
        assert yaml_stack.mock_outputs == json_stack.mock_outputs

    def test_source_path_is_set(self, sample_stack_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        assert stack.source_path == sample_stack_yaml.resolve()

    def test_mock_outputs_loaded(self, sample_stack_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        assert stack.mock_outputs["vpc_id"] == "mock-vpc-123"
        assert len(stack.mock_outputs["subnet_ids"]) == 2

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_stack(tmp_path / "nonexistent.yaml")

    def test_unsupported_extension(self, tmp_path: Path):
        bad_file = tmp_path / "stack.txt"
        bad_file.write_text("stack:\n  name: test\n  group: test\n")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            load_stack(bad_file)

    def test_invalid_schema_missing_components(self, tmp_path: Path):
        bad_file = tmp_path / "stack.yaml"
        bad_file.write_text("stack:\n  name: test\n  group: test\n")
        with pytest.raises(ValidationError):
            load_stack(bad_file)

    def test_load_stacks_merges_components_and_properties(self, tmp_path: Path):
        base_file = tmp_path / "base-stack.yaml"
        override_file = tmp_path / "override-stack.yaml"
        base_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - shared\n"
            "depends_on:\n"
            "  - network\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    tags:\n"
            "      - base\n"
            "    properties:\n"
            "      versioning_enabled: false\n",
            encoding="utf-8",
        )
        override_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - app\n"
            "depends_on:\n"
            "  - data\n"
            "components:\n"
            "  bucket:\n"
            "    properties:\n"
            "      versioning_enabled: true\n"
            "      acl: private\n"
            "  queue:\n"
            "    type: aws_sqs_queue\n",
            encoding="utf-8",
        )

        stack = load_stacks([base_file, override_file])

        assert stack.name == "merged-stack"
        assert stack.tags == {"shared", "app"}
        assert stack.depends_on == ["network", "data"]
        assert stack.components["bucket"].tags == {"base"}
        assert stack.components["bucket"].properties == {
            "versioning_enabled": True,
            "acl": "private",
        }
        assert stack.components["queue"].type == "aws_sqs_queue"
        assert stack.source_path == override_file.resolve()

    def test_load_stacks_deduplicates_tags_and_depends_on(self, tmp_path: Path):
        base_file = tmp_path / "base-stack.yaml"
        override_file = tmp_path / "override-stack.yaml"
        base_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - compute\n"
            "  - shared\n"
            "depends_on:\n"
            "  - network\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    tags:\n"
            "      - base\n"
            "      - base\n"
            "    properties:\n"
            "      versioning_enabled: false\n",
            encoding="utf-8",
        )
        override_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - shared\n"
            "  - app\n"
            "  - compute\n"
            "depends_on:\n"
            "  - data\n"
            "  - network\n"
            "components:\n"
            "  bucket:\n"
            "    tags:\n"
            "      - base\n"
            "      - base\n"
            "  queue:\n"
            "    type: aws_sqs_queue\n",
            encoding="utf-8",
        )

        stack = load_stacks([base_file, override_file])

        assert stack.tags == {"compute", "shared", "app"}
        assert stack.depends_on == ["network", "data"]
        assert stack.components["bucket"].tags == {"base"}
        assert stack.components["queue"].type == "aws_sqs_queue"

    def test_load_stacks_requires_at_least_one_path(self):
        with pytest.raises(ValueError, match="At least one stack file path"):
            load_stacks([])

    def test_load_stacks_override_mode_replaces_previous_layer(self, tmp_path: Path):
        base_file = tmp_path / "base-stack.yaml"
        override_file = tmp_path / "override-stack.yaml"
        base_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - shared\n"
            "depends_on:\n"
            "  - network\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    tags:\n"
            "      - base\n"
            "    properties:\n"
            "      versioning_enabled: false\n",
            encoding="utf-8",
        )
        override_file.write_text(
            "name: merged-stack\n"
            "tags:\n"
            "  - app\n"
            "components:\n"
            "  queue:\n"
            "    type: aws_sqs_queue\n",
            encoding="utf-8",
        )

        stack = load_stacks([base_file, override_file], merge_mode="override")

        assert stack.tags == {"app"}
        assert stack.depends_on == []
        assert set(stack.components) == {"queue"}
        assert stack.source_path == override_file.resolve()


class TestLoadRunFile:
    def test_load_runfile(self, tmp_path: Path):
        run_file = tmp_path / "stacksmith.yaml"
        run_file.write_text(
            "merge_mode: override\n"
            "stacks:\n"
            "  - source: http\n"
            "    data:\n"
            "      url: https://example.com/base-stack.yaml\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./stack.yaml\n"
            "configs:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./stacksmith-config.yaml\n"
            "vars:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./vars.dev.yaml\n"
            "var:\n"
            "  replicas: 2\n"
            "  features:\n"
            "    enabled: true\n",
            encoding="utf-8",
        )

        loaded = load_runfile(run_file)

        assert loaded.merge_mode == "override"
        assert loaded.stacks[0].source == "http"
        assert loaded.stacks[0].data.url == "https://example.com/base-stack.yaml"
        assert loaded.stacks[1].source == "local"
        assert loaded.stacks[1].data.path == "./stack.yaml"
        assert loaded.configs[0].source == "local"
        assert loaded.configs[0].data.path == "./stacksmith-config.yaml"
        assert loaded.vars[0].source == "local"
        assert loaded.vars[0].data.path == "./vars.dev.yaml"
        assert loaded.var == {"replicas": 2, "features": {"enabled": True}}

    def test_load_runfile_rejects_unknown_keys(self, tmp_path: Path):
        run_file = tmp_path / "stacksmith.yaml"
        run_file.write_text(
            "stacks:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./stack.yaml\n"
            "unexpected: true\n",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_runfile(run_file)

    def test_load_runfile_normalizes_legacy_string_references(self, tmp_path: Path):
        run_file = tmp_path / "stacksmith.yaml"
        run_file.write_text(
            "stacks:\n"
            "  - ./stack.yaml\n"
            "  - https://example.com/base-stack.yaml\n"
            "configs:\n"
            "  - git+https://github.com/org/platform.git//stacksmith-config.yaml@v1.2.3\n"
            "vars:\n"
            "  - ./vars.dev.yaml\n",
            encoding="utf-8",
        )

        loaded = load_runfile(run_file)

        assert loaded.stacks[0].source == "local"
        assert loaded.stacks[0].data.path == "./stack.yaml"
        assert loaded.stacks[1].source == "http"
        assert loaded.stacks[1].data.url == "https://example.com/base-stack.yaml"
        assert loaded.configs[0].source == "git"
        assert loaded.configs[0].data.repo == "https://github.com/org/platform.git"
        assert loaded.configs[0].data.path == "stacksmith-config.yaml"
        assert loaded.configs[0].data.ref == "v1.2.3"
        assert loaded.vars[0].source == "local"
        assert loaded.vars[0].data.path == "./vars.dev.yaml"

    def test_load_runfile_logs_warning_for_legacy_string_references(
        self, tmp_path: Path
    ):
        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="WARNING")

        try:
            run_file = tmp_path / "stacksmith.yaml"
            run_file.write_text(
                "stacks:\n"
                "  - ./stack.yaml\n"
                "  - https://example.com/base-stack.yaml\n"
                "configs:\n"
                "  - git+https://github.com/org/platform.git//stacksmith-config.yaml@v1.2.3\n"
                "vars:\n"
                "  - ./vars.dev.yaml\n",
                encoding="utf-8",
            )

            loaded = load_runfile(run_file)

            assert loaded.stacks[0].source == "local"
            assert loaded.stacks[1].source == "http"
            assert loaded.configs[0].source == "git"
            assert loaded.vars[0].source == "local"

            log_text = buffer.getvalue()
            assert "uses legacy local path string './stack.yaml'" in log_text
            assert (
                "uses legacy HTTP URL string 'https://example.com/base-stack.yaml'"
                in log_text
            )
            assert (
                "uses legacy git+ URL string 'git+https://github.com/org/platform.git//stacksmith-config.yaml@v1.2.3'"
                in log_text
            )
        finally:
            LOGGER.remove(sink_id)


class TestLoadConfig:
    def test_load_config(self, sample_config_yaml: Path):
        config = load_config(sample_config_yaml)
        assert config.backend.type == "s3"
        assert config.backend.config["bucket"] == "test-state-bucket"
        assert config.backend.config["region"] == "us-east-1"
        assert config.tools.tofu.version == "1.8.0"
        assert "aws" in config.provider_mappings
        assert "default" in config.provider_mappings["aws"].instances
        assert (
            config.provider_mappings["aws"].instances["default"].config.data["region"]
            == "us-east-1"
        )
        assert "aws_s3_bucket" in config.module_mappings

    def test_load_config_accepts_provider_config_spec(self, tmp_path: Path):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            _local_config_yaml(),
            encoding="utf-8",
        )

        config = load_config(config_file)

        assert (
            config.provider_mappings["aws"].instances["default"].config.inline
            is not None
        )

    def test_load_config_normalizes_legacy_reference_shapes(self, tmp_path: Path):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            "backend:\n"
            "  type: local\n"
            "  path: .state\n"
            "tools:\n"
            "  tofu:\n"
            "    version: '1.8.0'\n"
            "  terragrunt:\n"
            "    version: '1.0.6'\n"
            "provider_mappings:\n"
            "  aws:\n"
            "    source: hashicorp/aws\n"
            "    version: '~> 6.0'\n"
            "    instances:\n"
            "      default:\n"
            "        config:\n"
            "          script: scripts/providers/aws.py\n"
            "module_mappings:\n"
            "  aws_s3_bucket:\n"
            "    source: https://github.com/org/terraform-aws-s3.git//modules/bucket\n"
            "    version: '1.2.3'\n"
            "    properties:\n"
            "      acl:\n"
            "        transform:\n"
            "          script: scripts/transforms/acl.py\n"
            "var_validations:\n"
            "  bucket_name:\n"
            "    script: scripts/validations/bucket_name.py\n"
            "plan_validations:\n"
            "  no_destroy:\n"
            "    rule:\n"
            "      script: scripts/validations/no_destroy.py\n",
            encoding="utf-8",
        )

        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="WARNING")

        try:
            config = load_config(config_file)

            assert config.provider_mappings["aws"].source.source == "registry"
            assert (
                config.provider_mappings["aws"].source.data.address == "hashicorp/aws"
            )
            assert config.provider_mappings["aws"].source.data.version == "~> 6.0"

            module_source = config.module_mappings["aws_s3_bucket"].source
            assert module_source.source == "git"
            assert (
                module_source.data.repo == "https://github.com/org/terraform-aws-s3.git"
            )
            assert module_source.data.path == "modules/bucket"
            assert module_source.data.ref == "1.2.3"

            provider_script = (
                config.provider_mappings["aws"].instances["default"].config.script
            )
            assert provider_script is not None
            assert provider_script.source == "local"
            assert Path(provider_script.data.path).is_absolute()

            transform_script = (
                config.module_mappings["aws_s3_bucket"]
                .properties["acl"]
                .transform.script
            )
            assert transform_script is not None
            assert transform_script.source == "local"
            assert Path(transform_script.data.path).is_absolute()

            var_script = config.var_validations["bucket_name"].script
            assert var_script is not None
            assert var_script.source == "local"
            assert Path(var_script.data.path).is_absolute()

            plan_script = config.plan_validations["no_destroy"].rule.script
            assert plan_script is not None
            assert plan_script.source == "local"
            assert Path(plan_script.data.path).is_absolute()

            log_text = buffer.getvalue()
            assert "provider_mappings.aws uses legacy source/version shape" in log_text
            assert (
                "module_mappings.aws_s3_bucket uses legacy source/version git shape"
                in log_text
            )
            assert (
                "var_validations.bucket_name.script uses legacy local path string 'scripts/validations/bucket_name.py'"
                in log_text
            )
            assert (
                "plan_validations.no_destroy.rule.script uses legacy local path string 'scripts/validations/no_destroy.py'"
                in log_text
            )
        finally:
            LOGGER.remove(sink_id)

    def test_load_local_backend_config(self, sample_config_local_yaml: Path):
        config = load_config(sample_config_local_yaml)
        assert config.backend.type == "local"
        assert config.backend.config["path"] == "/tmp/stacksmith-state"

    def test_module_property_mapping(self, sample_config_yaml: Path):
        config = load_config(sample_config_yaml)
        assert (
            config.module_mappings["aws_s3_bucket"].properties["acl"].mapped_to
            == "bucket_acl"
        )

    def test_module_tags_loaded(self, tmp_path: Path):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            _s3_config_yaml(
                module_body=("    tags:\n" "      - prod\n" "      - shared\n")
            ),
            encoding="utf-8",
        )

        config = load_config(config_file)

        assert config.module_mappings["aws_s3_bucket"].tags == {"prod", "shared"}

    def test_source_path_is_set(self, sample_config_yaml: Path):
        config = load_config(sample_config_yaml)
        assert config.source_path == sample_config_yaml.resolve()

    def test_load_config_override_mode_replaces_previous_layer(self, tmp_path: Path):
        base_file = tmp_path / "base-stacksmith-config.yaml"
        override_file = tmp_path / "override-stacksmith-config.yaml"
        base_file.write_text(
            _s3_config_yaml(
                backend_bucket="base-bucket",
                module_body=(
                    "    properties:\n" "      acl:\n" "        mapped_to: bucket_acl\n"
                ),
            ),
            encoding="utf-8",
        )
        override_file.write_text(
            _local_config_yaml(),
            encoding="utf-8",
        )

        config = load_config([base_file, override_file], merge_mode="override")

        assert config.backend.type == "local"
        assert config.backend.config == {"path": ".state"}
        assert config.module_mappings["aws_s3_bucket"].properties == {}
        assert config.source_path == override_file.resolve()

    def test_invalid_validation_spec_is_rejected(self, tmp_path: Path):
        bad_file = tmp_path / "stack.yaml"
        bad_file.write_text(
            "stack:\n"
            "  name: bad-stack\n"
            "vars:\n"
            "  name:\n"
            "    type: str\n"
            "    validation:\n"
            "      inline: value.startswith('ok-')\n"
            "      script: validators/name.py\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_stack(bad_file)

    def test_invalid_transform_spec_is_rejected(self, tmp_path: Path):
        bad_file = tmp_path / "stacksmith-config.yaml"
        bad_file.write_text(
            _s3_config_yaml(
                backend_bucket="test-bucket",
                module_body=(
                    "    properties:\n"
                    "      acl:\n"
                    "        transform:\n"
                    "          inline: 'def transform(value, **context): return value.upper()'\n"
                    "          script:\n"
                    "            source: local\n"
                    "            data:\n"
                    "              path: transforms/acl.py\n"
                ),
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_config(bad_file)

    def test_old_provider_shape_is_rejected(self, tmp_path: Path):
        bad_file = tmp_path / "stacksmith-config.yaml"
        bad_file.write_text(
            _s3_config_yaml(
                backend_bucket="test-bucket",
                include_instances=False,
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_config(bad_file)

    def test_load_config_deep_merges_multiple_files(self, tmp_path: Path):
        base = tmp_path / "base.yaml"
        override = tmp_path / "override.yaml"
        base.write_text(
            _s3_config_yaml(
                backend_bucket="base-bucket",
                module_body=(
                    "    properties:\n" "      acl:\n" "        mapped_to: bucket_acl\n"
                ),
                extra_body=(
                    "plan_validations:\n"
                    "  no_destroy:\n"
                    "    rule:\n"
                    "      inline: \"'delete' not in [c['action'] for c in value['changes']]\"\n"
                ),
            ),
            encoding="utf-8",
        )
        override.write_text(
            _provider_override_yaml(
                version="= 5.91.0",
                module_body=(
                    "module_mappings:\n"
                    "  aws_s3_bucket:\n"
                    "    properties:\n"
                    "      acl:\n"
                    "        transform:\n"
                    "          inline: 'def transform(value, **context): return value.upper()'\n"
                ),
                extra_body=(
                    "plan_validations:\n" "  no_destroy:\n" "    enabled: false\n"
                ),
            ),
            encoding="utf-8",
        )

        config = load_config([base, override])

        assert config.provider_mappings["aws"].source.data.version == "= 5.91.0"
        assert config.module_mappings["aws_s3_bucket"].source.data.ref == "1.0.0"
        assert (
            config.module_mappings["aws_s3_bucket"].properties["acl"].mapped_to
            == "bucket_acl"
        )
        assert (
            config.module_mappings["aws_s3_bucket"].properties["acl"].transform
            is not None
        )
        assert config.plan_validations["no_destroy"].enabled is False

    def test_load_config_appends_lists_from_multiple_files(self, tmp_path: Path):
        base = tmp_path / "base.yaml"
        override = tmp_path / "override.yaml"
        base.write_text(
            _s3_config_yaml(
                backend_bucket="base-bucket",
                module_body=("    tags:\n" "      - base\n" "      - shared\n"),
            ),
            encoding="utf-8",
        )
        override.write_text(
            _provider_override_yaml(
                version="= 5.91.0",
                module_body=(
                    "    instances:\n"
                    "      default:\n"
                    "        config:\n"
                    "          data:\n"
                    "            region: us-west-2\n"
                    "module_mappings:\n"
                    "  aws_s3_bucket:\n"
                    "    tags:\n"
                    "      - env:prod\n"
                    "      - feature-x\n"
                ),
            ),
            encoding="utf-8",
        )

        config = load_config([base, override])

        assert config.provider_mappings["aws"].source.data.version == "= 5.91.0"
        assert config.module_mappings["aws_s3_bucket"].source.data.ref == "1.0.0"
        assert set(config.module_mappings["aws_s3_bucket"].tags) == {
            "base",
            "shared",
            "env:prod",
            "feature-x",
        }

    def test_plan_validation_defaults_to_enabled(self, tmp_path: Path):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_file.write_text(
            _s3_config_yaml(
                extra_body=(
                    "plan_validations:\n"
                    "  no_destroy:\n"
                    "    rule:\n"
                    '      inline: "True"\n'
                )
            ),
            encoding="utf-8",
        )

        config = load_config(config_file)

        assert config.plan_validations["no_destroy"].enabled is True
