import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from stacksmith.models import GitReference, RemoteAuthEntry
from stacksmith.remote import (
    is_remote_url,
    read_reference_content,
    resolve_if_remote,
    resolve_references,
    resolve_remote,
    terragrunt_auth_env,
)
from stacksmith.utils import cache_key, resolve_git_env


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/file.yaml",
        "https://example.com/file.yaml",
    ],
)
def test_is_remote_url_true(url: str):
    assert is_remote_url(url) is True


def test_is_remote_url_true_for_structured_git_reference():
    assert (
        is_remote_url(
            GitReference(
                source="git",
                data={
                    "repo": "https://github.com/org/repo.git",
                    "path": "path/file.py",
                    "ref": "main",
                },
            )
        )
        is True
    )


@pytest.mark.parametrize(
    "ref",
    [
        "/tmp/file.yaml",
        "relative/path.yaml",
        "file.yaml",
        "ftp://example.com/file.yaml",
        "",
    ],
)
def test_is_remote_url_false(ref: str):
    assert is_remote_url(ref) is False


def test_is_remote_url_true_for_git_plus_string():
    assert (
        is_remote_url("git+https://github.com/org/repo.git//path/file.py@main") is True
    )


def test_cache_key_deterministic():
    assert cache_key("hello") == cache_key("hello")
    assert cache_key("a") != cache_key("b")
    assert len(cache_key("anything")) == 16


def test_resolve_auth_headers_from_config(tmp_path: Path):
    entry = RemoteAuthEntry(type="token", token_env="MY_TOKEN")
    auth_config = {"github.com": entry}
    response = MagicMock(content=b"data", status_code=200)

    with (
        patch.dict("os.environ", {"MY_TOKEN": "tok123"}, clear=False),
        patch("stacksmith.remote.requests.get", return_value=response) as mock_get,
    ):
        resolve_remote("https://github.com/config.yaml", tmp_path, auth_config)

    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer tok123"}


def test_resolve_auth_headers_from_env_token(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STACKSMITH_HTTP_TOKEN", "env-token")
    response = MagicMock(content=b"data", status_code=200)

    with patch("stacksmith.remote.requests.get", return_value=response) as mock_get:
        resolve_remote("https://example.com/config.yaml", tmp_path)

    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer env-token"}


def test_resolve_auth_headers_from_env_basic(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STACKSMITH_HTTP_USERNAME", "user")
    monkeypatch.setenv("STACKSMITH_HTTP_PASSWORD", "pass")
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    response = MagicMock(content=b"data", status_code=200)

    with patch("stacksmith.remote.requests.get", return_value=response) as mock_get:
        resolve_remote("https://example.com/config.yaml", tmp_path)

    headers = mock_get.call_args.kwargs["headers"]
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_resolve_auth_headers_no_auth(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_PASSWORD", raising=False)
    response = MagicMock(content=b"data", status_code=200)

    with patch("stacksmith.remote.requests.get", return_value=response) as mock_get:
        resolve_remote("https://example.com/config.yaml", tmp_path)

    assert mock_get.call_args.kwargs["headers"] == {}


def test_resolve_git_env_ssh_key_from_config(monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    entry = RemoteAuthEntry(type="ssh", ssh_key_path="/home/user/.ssh/deploy_key")
    auth_config = {"github.com": entry}

    env = resolve_git_env("github.com", auth_config)

    assert "GIT_SSH_COMMAND" in env
    assert "/home/user/.ssh/deploy_key" in env["GIT_SSH_COMMAND"]


def test_resolve_git_env_token_from_config(monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    monkeypatch.setenv("DEPLOY_TOKEN", "tok-xyz")
    entry = RemoteAuthEntry(type="token", token_env="DEPLOY_TOKEN")
    auth_config = {"github.com": entry}

    env = resolve_git_env("github.com", auth_config)

    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert "tok-xyz" in env.get("GIT_CONFIG_KEY_0", "")


def test_resolve_git_env_fallback_env_token(monkeypatch):
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "fallback-tok")
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)

    env = resolve_git_env("example.com", None)

    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert "fallback-tok" in env.get("GIT_CONFIG_KEY_0", "")


def test_terragrunt_auth_env_sets_host_git_credential_helper(monkeypatch):
    monkeypatch.setenv("DEPLOY_TOKEN", "tok-xyz")
    monkeypatch.setenv("DEPLOY_USERNAME", "ci-user")
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)

    with terragrunt_auth_env(
        {},
        {
            "github.com": RemoteAuthEntry(
                type="token",
                token_env="DEPLOY_TOKEN",
                username_env="DEPLOY_USERNAME",
            ),
        },
    ) as env:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            env=env,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
        helper_paths = [
            Path(env[f"GIT_CONFIG_VALUE_{index}"])
            for index in range(int(env["GIT_CONFIG_COUNT"]))
            if env[f"GIT_CONFIG_KEY_{index}"].endswith(".helper")
            and env[f"GIT_CONFIG_VALUE_{index}"]
        ]

        assert "username=ci-user" in result.stdout
        assert "password=tok-xyz" in result.stdout
        assert helper_paths
        assert all(path.exists() for path in helper_paths)
        assert all("tok-xyz" not in path.read_text() for path in helper_paths)
        assert all(
            "tok-xyz" not in value for key, value in env.items() if "GIT_CONFIG" in key
        )

    assert all(not path.exists() for path in helper_paths)


def test_terragrunt_auth_env_uses_fallback_token_and_git_username(monkeypatch):
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "fallback-tok")

    with terragrunt_auth_env({}, None) as env:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=example.com\n\n",
            env=env,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )

    assert "username=git" in result.stdout
    assert "password=fallback-tok" in result.stdout


def test_terragrunt_auth_env_preserves_url_username(monkeypatch):
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "fallback-tok")

    with terragrunt_auth_env({}, None) as env:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=example.com\nusername=url-user\n\n",
            env=env,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )

    assert "username=url-user" in result.stdout
    assert "password=fallback-tok" in result.stdout


def test_terragrunt_auth_env_prefers_host_token_over_fallback(monkeypatch):
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "fallback-tok")
    monkeypatch.setenv("DEPLOY_TOKEN", "host-tok")

    with terragrunt_auth_env(
        {},
        {
            "github.com": RemoteAuthEntry(type="token", token_env="DEPLOY_TOKEN"),
        },
    ) as env:
        github_result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            env=env,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )
        other_result = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=example.com\n\n",
            env=env,
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        )

    assert "password=host-tok" in github_result.stdout
    assert "password=fallback-tok" in other_result.stdout


def test_terragrunt_auth_env_sets_ssh_command(monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)

    with terragrunt_auth_env(
        {},
        {
            "github.com": RemoteAuthEntry(
                type="ssh", ssh_key_path="/home/user/.ssh/deploy_key"
            ),
        },
    ) as env:
        assert env["GIT_SSH_COMMAND"].startswith("ssh -i")
        assert "/home/user/.ssh/deploy_key" in env["GIT_SSH_COMMAND"]


def test_fetch_http_downloads_and_caches(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.content = b"file-contents"
    mock_resp.raise_for_status = MagicMock()

    with patch("stacksmith.remote.requests.get", return_value=mock_resp) as mock_get:
        result = resolve_remote("https://example.com/scripts/validate.py", tmp_path)
        assert result.exists()
        assert result.read_bytes() == b"file-contents"
        mock_get.assert_called_once()

        # Second call should use cache
        result2 = resolve_remote("https://example.com/scripts/validate.py", tmp_path)
        assert result2 == result
        mock_get.assert_called_once()  # no second call


def test_fetch_http_raises_on_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with (
        patch("stacksmith.remote.requests.get", return_value=mock_resp),
        pytest.raises(Exception, match="404 Not Found"),
    ):
        resolve_remote("https://example.com/missing.yaml", tmp_path)


def test_resolve_remote_http(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.content = b"data"
    mock_resp.raise_for_status = MagicMock()

    with patch("stacksmith.remote.requests.get", return_value=mock_resp):
        result = resolve_remote("https://example.com/config.yaml", tmp_path)
        assert result.name == "config.yaml"
        assert result.read_bytes() == b"data"


def test_read_reference_content_local_file(tmp_path):
    sample = tmp_path / "example.txt"
    sample.write_text("hello world", encoding="utf-8")

    content = read_reference_content(str(sample), tmp_path)

    assert content == "hello world"


def test_resolve_remote_git(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)

    git_reference = GitReference(
        source="git",
        data={
            "repo": "https://github.com/org/repo.git",
            "path": "scripts/validate.py",
            "ref": "main",
        },
    )

    with (
        patch("stacksmith.utils.shutil.which", return_value="/usr/bin/git"),
        patch("stacksmith.utils.subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")

        # Pre-create the expected target file so _fetch_git doesn't raise
        clone_dir = (
            tmp_path
            / "git"
            / f"{cache_key(git_reference.data.repo)}-{cache_key('main')}"
        )
        clone_dir.mkdir(parents=True)
        (clone_dir / "scripts").mkdir()
        (clone_dir / "scripts" / "validate.py").write_text("print('ok')")

        result = resolve_remote(git_reference, tmp_path)
        assert result.name == "validate.py"


def test_resolve_remote_git_plus_string(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)

    git_url = "git+https://github.com/org/repo.git//scripts/validate.py@main"

    with (
        patch("stacksmith.remote.shutil.which", return_value="/usr/bin/git"),
        patch("stacksmith.utils.subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")

        clone_dir = (
            tmp_path
            / "git"
            / f"{cache_key('https://github.com/org/repo.git')}-{cache_key('main')}"
        )
        clone_dir.mkdir(parents=True)
        (clone_dir / "scripts").mkdir()
        (clone_dir / "scripts" / "validate.py").write_text("print('ok')")

        result = resolve_remote(git_url, tmp_path)
        assert result.name == "validate.py"


def test_resolve_remote_invalid_scheme():
    with pytest.raises(ValueError, match="Not a remote URL"):
        resolve_remote("/local/path.yaml", Path("/cache"))


def test_resolve_if_remote_local_path():
    result = resolve_if_remote("/tmp/config.yaml", Path("/cache"))
    assert result == Path("/tmp/config.yaml")


def test_resolve_references_preserves_order_and_expands_user(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    result = resolve_references(["~/base.yaml", Path("override.yaml")])

    assert result == [tmp_path / "base.yaml", Path("override.yaml")]


def test_resolve_references_supports_custom_missing_cache_error():
    with pytest.raises(RuntimeError, match="config requires cache"):
        resolve_references(
            ["https://example.com/config.yaml"],
            missing_cache_error_factory=lambda _: RuntimeError("config requires cache"),
        )


def test_resolve_if_remote_http(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.content = b"remote-data"
    mock_resp.raise_for_status = MagicMock()

    with patch("stacksmith.remote.requests.get", return_value=mock_resp):
        result = resolve_if_remote("https://example.com/values.yaml", tmp_path, None)
        assert result.exists()
        assert result.read_bytes() == b"remote-data"
