"""Tests for module introspection — HCL variable discovery."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from stacksmith.introspection import parse_module_variables, resolve_module_dir
from stacksmith.models import LocalModuleSourceReference, render_module_source_identity
from stacksmith.utils import cache_key


class TestParseModuleVariables:
    def test_discovers_variables_from_tf_files(self, tmp_path: Path):
        (tmp_path / "variables.tf").write_text(
            'variable "instance_type" {\n'
            "  type    = string\n"
            '  default = "t3.micro"\n'
            "}\n"
            "\n"
            'variable "subnet_id" {\n'
            "  type = string\n"
            "}\n",
            encoding="utf-8",
        )
        (tmp_path / "main.tf").write_text(
            'variable "name" {\n'
            "  type = string\n"
            "}\n"
            "\n"
            'resource "aws_instance" "this" {\n'
            "  ami = var.name\n"
            "}\n",
            encoding="utf-8",
        )

        result = parse_module_variables(tmp_path)

        assert result == {"instance_type", "subnet_id", "name"}

    def test_returns_empty_set_when_no_tf_files(self, tmp_path: Path):
        (tmp_path / "readme.md").write_text("# Module\n", encoding="utf-8")

        assert parse_module_variables(tmp_path) == set()

    def test_returns_empty_set_when_no_variables(self, tmp_path: Path):
        (tmp_path / "main.tf").write_text(
            'resource "null_resource" "this" {}\n',
            encoding="utf-8",
        )

        assert parse_module_variables(tmp_path) == set()

    def test_skips_malformed_tf_files(self, tmp_path: Path):
        (tmp_path / "good.tf").write_text(
            'variable "region" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        (tmp_path / "bad.tf").write_text(
            "this is not valid HCL at all {{{{",
            encoding="utf-8",
        )

        result = parse_module_variables(tmp_path)

        assert "region" in result

    def test_discovers_variables_across_multiple_files(self, tmp_path: Path):
        (tmp_path / "a.tf").write_text(
            'variable "alpha" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        (tmp_path / "b.tf").write_text(
            'variable "beta" {\n  type = number\n}\n',
            encoding="utf-8",
        )

        assert parse_module_variables(tmp_path) == {"alpha", "beta"}

    def test_ignores_nested_tf_files(self, tmp_path: Path):
        (tmp_path / "variables.tf").write_text(
            'variable "top" {\n  type = string\n}\n',
            encoding="utf-8",
        )
        nested = tmp_path / "modules" / "child"
        nested.mkdir(parents=True)
        (nested / "variables.tf").write_text(
            'variable "nested" {\n  type = string\n}\n',
            encoding="utf-8",
        )

        result = parse_module_variables(tmp_path)

        assert result == {"top"}


class TestResolveModuleDir:
    def test_resolves_local_module_dir_without_git_clone(self, tmp_path: Path):
        module_dir = tmp_path / "examples" / "modules" / "helm_app"
        module_dir.mkdir(parents=True)
        (module_dir / "variables.tf").write_text(
            'variable "name" {\n  type = string\n}\n',
            encoding="utf-8",
        )

        source, version = render_module_source_identity(
            LocalModuleSourceReference(
                source="local",
                data={"path": "examples/modules/helm_app"},
            ),
            options={"base_path": tmp_path},
        )

        with patch("stacksmith.introspection._clone_git_repo") as mock_clone:
            result = resolve_module_dir(source, version, cache_dir=tmp_path)

        assert result == module_dir.resolve()
        mock_clone.assert_not_called()

    def test_uses_repo_root_and_returns_module_subdir(self, tmp_path: Path):
        source = (
            "https://github.com/terraform-aws-modules/terraform-aws-iam.git"
            "//modules/iam-role"
        )
        version = "6.6.0"
        repo_url = "https://github.com/terraform-aws-modules/terraform-aws-iam.git"
        clone_dir = (
            tmp_path / "introspect" / f"{cache_key(repo_url)}-{cache_key(version)}"
        )
        module_dir = clone_dir / "modules" / "iam-role"

        def _run(*args, **kwargs):
            clone_dir.mkdir(parents=True, exist_ok=True)
            module_dir.mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("stacksmith.utils.shutil.which", return_value="/usr/bin/git"),
            patch("stacksmith.utils.subprocess.run", side_effect=_run) as mock_run,
        ):
            result = resolve_module_dir(source, version, cache_dir=tmp_path)

        assert result == module_dir
        assert mock_run.call_args.args[0] == [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "6.6.0",
            repo_url,
            str(clone_dir),
        ]

    def test_refreshes_incomplete_cached_clone(self, tmp_path: Path):
        source = (
            "https://github.com/terraform-aws-modules/terraform-aws-ec2-instance.git"
        )
        version = "v6.4.0"
        clone_dir = (
            tmp_path / "introspect" / f"{cache_key(source)}-{cache_key(version)}"
        )
        module_dir = clone_dir
        module_dir.mkdir(parents=True)

        call_count = {"count": 0}

        def _run(cmd, env=None, capture_output=None, text=None):
            call_count["count"] += 1
            clone_dir.mkdir(parents=True, exist_ok=True)
            (module_dir / "variables.tf").write_text(
                'variable "subnet_id" {\n  type = string\n}\n',
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=0, stderr="")

        with (
            patch("stacksmith.utils.shutil.which", return_value="/usr/bin/git"),
            patch("stacksmith.utils.subprocess.run", side_effect=_run),
        ):
            result = resolve_module_dir(source, version, cache_dir=tmp_path)

        assert result == module_dir
        assert call_count["count"] == 1
        assert (module_dir / "variables.tf").exists()
