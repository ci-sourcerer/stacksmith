from pathlib import Path

import pytest
import yaml
from jsonschema import ValidationError
from stacksmith import loader
from stacksmith.loader import (
    load_config,
    load_runfile,
    load_runfiles,
    load_stack,
    load_stack_metadata,
    load_stacks,
)
from stacksmith.models import MergePolicy, MergeRule


def _s3_config_yaml(
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

    def test_stack_template_can_generate_components(self, tmp_path: Path):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: workers\n"
            "components:\n"
            "{% for name, worker in inputs.workers.items() %}\n"
            "  '{{ name }}':\n"
            "    type: aws_ec2_instance\n"
            "    properties:\n"
            "      ami: {{ worker.ami | tojson }}\n"
            "      instance_type: {{ worker.instance_type | tojson }}\n"
            "{% endfor %}\n",
            encoding="utf-8",
        )

        stack = load_stack(
            stack_file,
            template_context={
                "inputs": {
                    "workers": {
                        "blue": {
                            "ami": "ami-blue",
                            "instance_type": "t3.small",
                        },
                        "green": {
                            "ami": "ami-green",
                            "instance_type": "t3.medium",
                        },
                    }
                }
            },
        )

        assert set(stack.components) == {"blue", "green"}
        assert stack.components["blue"].properties == {
            "ami": "ami-blue",
            "instance_type": "t3.small",
        }

    def test_stack_template_preserves_stack_metadata_context(self, tmp_path: Path):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: payments\n"
            "tags:\n"
            "  - applications\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    properties:\n"
            "      name: '{{ stack.name }}-{{ inputs.bucket_name }}'\n",
            encoding="utf-8",
        )

        stack = load_stack(
            stack_file,
            template_context={
                "inputs": {"bucket_name": "assets"},
                "stack": {"name": "payments", "tags": ["applications"]},
            },
        )

        assert stack.components["bucket"].properties["name"] == "payments-assets"

    def test_stack_template_renders_git_repository(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            loader,
            "get_current_git_repository",
            lambda path: "https://github.com/example/iac.git",
        )
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: repository-stack\n"
            "components:\n"
            "  bucket:\n"
            "    type: aws_s3_bucket\n"
            "    properties:\n"
            "      repository: '{{ git_repository }}'\n",
            encoding="utf-8",
        )

        stack = load_stack(
            stack_file,
            template_context={"inputs": {}, "stack": {"name": "", "tags": []}},
        )

        assert stack.components["bucket"].properties["repository"] == (
            "https://github.com/example/iac.git"
        )

    def test_stack_metadata_loads_template_without_inputs(self, tmp_path: Path):
        stack_file = tmp_path / "stack.yaml"
        stack_file.write_text(
            "name: workers\n"
            "components:\n"
            "{% for worker in inputs.workers %}\n"
            "  '{{ worker }}':\n"
            "    type: aws_ec2_instance\n"
            "{% endfor %}\n",
            encoding="utf-8",
        )

        stack = load_stack_metadata(stack_file)

        assert stack.name == "workers"
        assert stack.components == {}

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
    def test_load_runfiles_merges_layers_in_order(self, tmp_path: Path):
        base_run_file = tmp_path / "base.yaml"
        base_run_file.write_text(
            "merge_mode: deep\n"
            "stacks:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./base-stack.yaml\n"
            "configs:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./base-config.yaml\n"
            "vars:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./base-vars.yaml\n"
            "  - source: inline\n"
            "    data:\n"
            "      region: us-east-1\n"
            "      tags:\n"
            "        owner: platform\n",
            encoding="utf-8",
        )
        override_run_file = tmp_path / "override.yaml"
        override_run_file.write_text(
            "merge_mode: override\n"
            "stacks:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./override-stack.yaml\n"
            "configs:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./override-config.yaml\n"
            "vars:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./override-vars.yaml\n"
            "  - source: inline\n"
            "    data:\n"
            "      region: eu-west-1\n"
            "      tags:\n"
            "        env: dev\n",
            encoding="utf-8",
        )

        loaded = load_runfiles([base_run_file, override_run_file])

        assert loaded.merge_mode == "override"
        assert [ref.data.path for ref in loaded.stacks] == [
            str((tmp_path / "base-stack.yaml").resolve()),
            str((tmp_path / "override-stack.yaml").resolve()),
        ]
        assert [ref.data.path for ref in loaded.configs] == [
            str((tmp_path / "base-config.yaml").resolve()),
            str((tmp_path / "override-config.yaml").resolve()),
        ]
        assert [ref.source for ref in loaded.vars] == [
            "local",
            "inline",
            "local",
            "inline",
        ]
        assert loaded.vars[0].data.path == str((tmp_path / "base-vars.yaml").resolve())
        assert loaded.vars[1].data == {
            "region": "us-east-1",
            "tags": {"owner": "platform"},
        }
        assert loaded.vars[2].data.path == str(
            (tmp_path / "override-vars.yaml").resolve()
        )
        assert loaded.vars[3].data == {"region": "eu-west-1", "tags": {"env": "dev"}}

    def test_load_runfile(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
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
            "  - source: inline\n"
            "    data:\n"
            "      replicas: 2\n"
            "      features:\n"
            "        enabled: true\n",
            encoding="utf-8",
        )

        loaded = load_runfile(runfile)

        assert loaded.merge_mode == "override"
        assert loaded.stacks[0].source == "http"
        assert loaded.stacks[0].data.url == "https://example.com/base-stack.yaml"
        assert loaded.stacks[1].source == "local"
        assert loaded.stacks[1].data.path == str((tmp_path / "stack.yaml").resolve())
        assert loaded.configs[0].source == "local"
        assert loaded.configs[0].data.path == str(
            (tmp_path / "stacksmith-config.yaml").resolve()
        )
        assert loaded.vars[0].source == "local"
        assert loaded.vars[0].data.path == str((tmp_path / "vars.dev.yaml").resolve())
        assert loaded.vars[1].source == "inline"
        assert loaded.vars[1].data == {"replicas": 2, "features": {"enabled": True}}

    def test_load_runfile_rejects_top_level_var(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text("var:\n  replicas: 2\n", encoding="utf-8")

        with pytest.raises(ValidationError):
            load_runfile(runfile)

    def test_load_runfile_rejects_string_refs(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            """
vars:
  - "{{ runfile.path | replace('stacksmith.yaml', 'vars.dev.yaml') }}"
""",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_runfile(runfile)

    def test_load_runfile_renders_runfile_path_in_structured_refs(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            """
vars:
  - source: local
    data:
      path: "{{ runfile.path | replace('stacksmith.yaml', 'vars.dev.yaml') }}"
""",
            encoding="utf-8",
        )

        loaded = load_runfile(runfile)

        assert loaded.vars[0].source == "local"
        assert loaded.vars[0].data.path == str(
            runfile.resolve().with_name("vars.dev.yaml")
        )

    def test_load_runfile_renders_runfile_dir_name_and_stem(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            """
vars:
  - source: local
    data:
      path: "{{ runfile.dir }}/vars.dev.yaml"
  - source: inline
    data:
      runfile_name: "{{ runfile.name }}"
      runfile_stem: "{{ runfile.stem }}"
      runfile_dir: "{{ runfile.dir }}"
""",
            encoding="utf-8",
        )

        loaded = load_runfile(runfile)

        assert loaded.vars[0].source == "local"
        assert loaded.vars[0].data.path == str(
            runfile.resolve().parent / "vars.dev.yaml"
        )
        assert loaded.vars[1].source == "inline"
        assert loaded.vars[1].data == {
            "runfile_name": "stacksmith.yaml",
            "runfile_stem": "stacksmith",
            "runfile_dir": str(runfile.resolve().parent),
        }

    def test_load_runfile_renders_git_repository(self, monkeypatch, tmp_path: Path):
        monkeypatch.setattr(
            loader,
            "get_current_git_repository",
            lambda path: "https://github.com/example/iac.git",
        )
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            "vars:\n"
            "  - source: inline\n"
            "    data:\n"
            "      repository: '{{ git_repository }}'\n",
            encoding="utf-8",
        )

        loaded = load_runfile(runfile)

        assert loaded.vars[0].data == {
            "repository": "https://github.com/example/iac.git"
        }

    def test_load_runfile_rejects_unknown_keys(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            "stacks:\n"
            "  - source: local\n"
            "    data:\n"
            "      path: ./stack.yaml\n"
            "unexpected: true\n",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_runfile(runfile)

    def test_load_runfile_rejects_string_references(self, tmp_path: Path):
        runfile = tmp_path / "stacksmith.yaml"
        runfile.write_text(
            "stacks:\n"
            "  - ./stack.yaml\n"
            "configs:\n"
            "  - git+https://github.com/org/platform.git//stacksmith-config.yaml@v1.2.3\n"
            "vars:\n"
            "  - ./vars.dev.yaml\n",
            encoding="utf-8",
        )

        with pytest.raises(ValidationError):
            load_runfile(runfile)


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

    def test_load_config_accepts_default_only_mapping(
        self,
        sample_config_yaml: Path,
        tmp_path: Path,
    ):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_data = yaml.safe_load(sample_config_yaml.read_text(encoding="utf-8"))
        config_data.pop("module_mappings")
        config_data["default_module_mapping"] = {
            "source": {
                "source": "git",
                "data": {
                    "repo": (
                        "https://github.com/org/"
                        "terraform-{{ component_type | replace('-', '_') }}.git"
                    ),
                    "ref": "latest",
                },
            }
        }
        config_file.write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )

        config = load_config(config_file)

        assert config.module_mappings == {}
        assert config.default_module_mapping is not None
        assert "{{ component_type" in config.default_module_mapping.source.data.repo

    def test_load_config_rejects_default_mapping_description(
        self,
        sample_config_yaml: Path,
        tmp_path: Path,
    ):
        config_file = tmp_path / "stacksmith-config.yaml"
        config_data = yaml.safe_load(sample_config_yaml.read_text(encoding="utf-8"))
        config_data["default_module_mapping"] = {
            "description": "Convention-based module",
            "source": {
                "source": "git",
                "data": {
                    "repo": "https://github.com/org/{{ component_type }}.git",
                    "ref": "latest",
                },
            },
        }
        config_file.write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )

        with pytest.raises(ValidationError, match="description"):
            load_config(config_file)

    def test_default_local_module_source_resolves_relative_to_config(
        self,
        sample_config_yaml: Path,
        tmp_path: Path,
    ):
        config_dir = tmp_path / "shared"
        config_dir.mkdir()
        config_file = config_dir / "stacksmith-config.yaml"
        config_data = yaml.safe_load(sample_config_yaml.read_text(encoding="utf-8"))
        config_data["module_mappings"] = {}
        config_data["default_module_mapping"] = {
            "source": {
                "source": "local",
                "data": {"path": "../modules/{{ component_type }}"},
            },
            "properties": {
                "name": {
                    "transform": {
                        "script": {
                            "source": "local",
                            "data": {"path": "../scripts/transform.py"},
                        }
                    }
                }
            },
        }
        config_file.write_text(
            yaml.safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )

        config = load_config(config_file)

        assert config.default_module_mapping is not None
        assert config.default_module_mapping.source.data.path == str(
            (config_dir / "../modules/{{ component_type }}").resolve()
        )
        assert config.default_module_mapping.properties[
            "name"
        ].transform.script.data.path == str(
            (config_dir / "../scripts/transform.py").resolve()
        )

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

    def test_local_module_source_path_resolves_relative_to_config(self, tmp_path: Path):
        config_dir = tmp_path / "shared"
        config_dir.mkdir(parents=True)
        config_file = config_dir / "stacksmith-config.yaml"
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
            "  helm_app:\n"
            "    source:\n"
            "      source: local\n"
            "      data:\n"
            "        path: ../modules/helm_app\n",
            encoding="utf-8",
        )

        config = load_config(config_file)

        module_source = config.module_mappings["helm_app"].source
        assert module_source.source == "local"
        assert module_source.data.path == str(
            (config_dir / "../modules/helm_app").resolve()
        )

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

    def test_load_config_applies_address_aware_override(self, tmp_path: Path):
        base = tmp_path / "base.yaml"
        base.write_text(
            _s3_config_yaml(
                module_body=("    tags:\n" "      - base\n" "      - shared\n"),
            ),
            encoding="utf-8",
        )
        override = tmp_path / "override.yaml"
        override.write_text(
            _provider_override_yaml(
                version="= 5.91.0",
                module_body=(
                    "module_mappings:\n"
                    "  aws_s3_bucket:\n"
                    "    tags:\n"
                    "      - override\n"
                ),
            ),
            encoding="utf-8",
        )

        config = load_config(
            [base, override],
            merge_mode=MergePolicy(
                rules=[
                    MergeRule(
                        select="address == '/module_mappings/aws_s3_bucket/tags'",
                        mode="override",
                    )
                ]
            ),
        )

        assert config.module_mappings["aws_s3_bucket"].tags == {"override"}

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
