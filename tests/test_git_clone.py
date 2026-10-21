import os
import subprocess

from stacksmith.utils import clone_git_repo, resolve_git_env


def test_pinned_commit_checkout_and_environment(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, shell=False)
    (source / "config.yaml").write_text("version: 1\n")
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, shell=False)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "initial",
        ],
        check=True,
        shell=False,
    )
    revision = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    ).stdout.strip()
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "current-installation-token")
    environment = resolve_git_env("github.com", None)
    assert "current-installation-token" in environment["GIT_CONFIG_KEY_0"]
    result = clone_git_repo(str(source), tmp_path / "checkout", revision, environment)
    assert result.returncode == 0
    assert (tmp_path / "checkout/config.yaml").read_text() == "version: 1\n"
    assert (
        subprocess.run(
            ["git", "-C", str(tmp_path / "checkout"), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        ).stdout.strip()
        == revision
    )


def test_missing_commit_returns_failed_result(tmp_path):
    source = tmp_path / "source"
    subprocess.run(["git", "init", str(source)], check=True, shell=False)
    assert (
        clone_git_repo(
            str(source), tmp_path / "checkout", "f" * 40, os.environ.copy()
        ).returncode
        != 0
    )
