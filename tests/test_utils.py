import os
import subprocess
from pathlib import Path

from stacksmith.utils import (
    env_truthy,
    env_vars,
    get_current_git_repository,
    load_env_file,
    load_env_files,
    stacksmith_env,
    stacksmith_env_list,
)


def test_get_current_git_repository_returns_origin_url_for_supplied_path(
    monkeypatch, tmp_path: Path
):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:example/iac.git"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    working_directory = tmp_path / "working-directory"
    working_directory.mkdir()
    monkeypatch.chdir(working_directory)

    assert get_current_git_repository(repository) == "git@github.com:example/iac.git"


def test_env_truthy_recognizes_true_values(monkeypatch):
    for value in ("1", "true", "yes", "on", "TRUE", "Yes"):
        monkeypatch.setenv("STACKSMITH_FOO", value)
        assert env_truthy("STACKSMITH_FOO") is True


def test_env_truthy_defaults_to_false_when_absent():
    if "STACKSMITH_FOO" in os.environ:
        del os.environ["STACKSMITH_FOO"]
    assert env_truthy("STACKSMITH_FOO") is False


def test_env_truthy_supports_default_true_when_absent():
    if "STACKSMITH_FOO" in os.environ:
        del os.environ["STACKSMITH_FOO"]
    assert env_truthy("STACKSMITH_FOO", default=True) is True


def test_stacksmith_env_reads_prefixed_value(monkeypatch):
    monkeypatch.setenv("STACKSMITH_CONFIG", "./stacksmith-config.yaml")
    assert stacksmith_env("CONFIG") == "./stacksmith-config.yaml"


def test_stacksmith_env_returns_default_when_missing():
    if "STACKSMITH_CONFIG" in os.environ:
        del os.environ["STACKSMITH_CONFIG"]
    assert stacksmith_env("CONFIG", default="default.yaml") == "default.yaml"


def test_env_truthy_with_prefix(monkeypatch):
    monkeypatch.setenv("STACKSMITH_DEBUG", "true")
    assert env_truthy("DEBUG", prefix="STACKSMITH_") is True


def test_load_env_file_preserves_existing_variables(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("STACKSMITH_CONFIG=/tmp/config.yaml\n")
    monkeypatch.setenv("STACKSMITH_CONFIG", "/existing/config.yaml")

    load_env_file(env_path)

    assert os.environ["STACKSMITH_CONFIG"] == "/existing/config.yaml"


def test_load_env_file_ignores_invalid_dotenv_lines(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "STACKSMITH_CONFIG=/tmp/config.yaml\nINVALID_LINE\nCI=true\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("STACKSMITH_CONFIG", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("INVALID_LINE", raising=False)

    load_env_file(env_path)

    assert os.environ["STACKSMITH_CONFIG"] == "/tmp/config.yaml"
    assert os.environ["CI"] == "true"
    assert "INVALID_LINE" not in os.environ


def test_load_env_files_layers_later_files(monkeypatch, tmp_path):
    monkeypatch.delenv("STACKSMITH_CONFIG", raising=False)
    monkeypatch.delenv("STACKSMITH_DEBUG", raising=False)
    base_env = tmp_path / "base.env"
    base_env.write_text(
        "STACKSMITH_CONFIG=/base/config.yaml\nSTACKSMITH_DEBUG=false\n",
        encoding="utf-8",
    )
    override_env = tmp_path / "override.env"
    override_env.write_text(
        "STACKSMITH_CONFIG=/override/config.yaml\nSTACKSMITH_DEBUG=true\n",
        encoding="utf-8",
    )

    load_env_files([base_env, override_env])

    assert os.environ["STACKSMITH_CONFIG"] == "/override/config.yaml"
    assert os.environ["STACKSMITH_DEBUG"] == "true"


def test_stacksmith_env_list_supports_colon_delimited_quoted_urls(monkeypatch):
    monkeypatch.setenv(
        "STACKSMITH_CONFIG",
        '"https://example.com/base.yaml":"git+https://github.com/org/config.git//override.yaml@v1"',
    )

    assert stacksmith_env_list("CONFIG") == [
        "https://example.com/base.yaml",
        "git+https://github.com/org/config.git//override.yaml@v1",
    ]


def test_env_vars_filters_prefix(monkeypatch):
    monkeypatch.setenv("STACKSMITH_A", "1")
    monkeypatch.setenv("STACKSMITH_B", "2")
    monkeypatch.setenv("OTHER", "3")

    envs = env_vars()
    assert envs["STACKSMITH_A"] == "1"
    assert envs["STACKSMITH_B"] == "2"
    assert "OTHER" not in envs
