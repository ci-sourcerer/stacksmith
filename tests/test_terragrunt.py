import json
from pathlib import Path

from stacksmith.loader import load_config, load_stack
from stacksmith.terragrunt import generate_terragrunt_json, write_terragrunt_json


class TestGenerateTerragruntJson:
    def test_basic_structure(self, sample_stack_yaml: Path, sample_config_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(stack, config, {"bucket_name": "my-bucket-test"})

        assert doc["terraform"]["source"] == "."
        assert doc["remote_state"]["backend"] == "s3"
        assert doc["terraform_binary"] == "tofu"

    def test_state_key(self, sample_stack_yaml: Path, sample_config_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(stack, config, {})

        assert doc["remote_state"]["config"]["key"] == "my-stack/terraform.tfstate"

    def test_state_key_with_root(self, monorepo_dir: Path, sample_config_yaml: Path):
        vpc_stack = load_stack(monorepo_dir / "networking" / "vpc" / "stack.yaml")
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(vpc_stack, config, {}, root=monorepo_dir)

        assert (
            doc["remote_state"]["config"]["key"] == "networking/vpc/terraform.tfstate"
        )

    def test_backend_config(self, sample_stack_yaml: Path, sample_config_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(stack, config, {})

        cfg = doc["remote_state"]["config"]
        assert cfg["bucket"] == "test-state-bucket"
        assert cfg["region"] == "us-east-1"

    def test_dependency_blocks(self, monorepo_dir: Path, sample_config_yaml: Path):
        vpc_stack = load_stack(monorepo_dir / "networking" / "vpc" / "stack.yaml")
        web_stack = load_stack(monorepo_dir / "compute" / "web" / "stack.yaml")
        config = load_config(sample_config_yaml)

        doc = generate_terragrunt_json(
            web_stack,
            config,
            {},
            dependency_stacks={"vpc": vpc_stack},
            dependency_build_dirs={"vpc": Path("/build/networking/vpc")},
        )

        assert "vpc" in doc["dependency"]
        dep = doc["dependency"]["vpc"]
        assert dep["config_path"] == "/build/networking/vpc"
        assert dep["mock_outputs"]["vpc_id"] == "mock-vpc-id"
        assert dep["mock_outputs_allowed_terraform_commands"] == ["plan", "validate"]

    def test_inputs_include_resolved_vars(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(
            stack, config, {"bucket_name": "my-bucket-input", "instance_count": 3}
        )

        assert doc["inputs"]["bucket_name"] == "my-bucket-input"
        assert doc["inputs"]["instance_count"] == 3

    def test_no_dependency_key_when_no_deps(
        self, sample_stack_yaml: Path, sample_config_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        doc = generate_terragrunt_json(stack, config, {})

        assert "dependency" not in doc


class TestLocalBackend:
    def test_basic_structure(
        self, sample_stack_yaml: Path, sample_config_local_yaml: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_local_yaml)
        doc = generate_terragrunt_json(stack, config, {})

        assert doc["remote_state"]["backend"] == "local"

    def test_state_path(self, sample_stack_yaml: Path, sample_config_local_yaml: Path):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_local_yaml)
        doc = generate_terragrunt_json(stack, config, {})

        assert (
            doc["remote_state"]["config"]["path"]
            == "/tmp/stacksmith-state/my-stack/terraform.tfstate"
        )

    def test_state_path_with_root(
        self, monorepo_dir: Path, sample_config_local_yaml: Path
    ):
        vpc_stack = load_stack(monorepo_dir / "networking" / "vpc" / "stack.yaml")
        config = load_config(sample_config_local_yaml)
        doc = generate_terragrunt_json(vpc_stack, config, {}, root=monorepo_dir)

        assert (
            doc["remote_state"]["config"]["path"]
            == "/tmp/stacksmith-state/networking/vpc/terraform.tfstate"
        )


class TestWriteTerragruntJson:
    def test_writes_file(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        output = write_terragrunt_json(stack, config, {}, tmp_path)

        assert output.exists()
        assert output.name == "terragrunt.hcl.json"

    def test_output_is_valid_json(
        self, sample_stack_yaml: Path, sample_config_yaml: Path, tmp_path: Path
    ):
        stack = load_stack(sample_stack_yaml)
        config = load_config(sample_config_yaml)
        output = write_terragrunt_json(stack, config, {"x": 1}, tmp_path)

        doc = json.loads(output.read_text())
        assert doc["terraform"]["source"] == "."
        assert doc["inputs"]["x"] == 1
