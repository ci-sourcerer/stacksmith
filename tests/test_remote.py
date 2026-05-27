from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from stacksmith.models import RemoteAuthEntry
from stacksmith.remote import (
    GitRef,
    _cache_key,
    _fetch_http,
    _resolve_auth_headers,
    _resolve_git_env,
    is_remote_url,
    parse_git_url,
    read_reference_content,
    resolve_if_remote,
    resolve_remote,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/file.yaml",
        "https://example.com/file.yaml",
        "git+https://github.com/org/repo.git//path/file.py@main",
        "git+ssh://git@github.com/org/repo.git//path/file.py",
    ],
)
def test_is_remote_url_true(url: str):
    assert is_remote_url(url) is True


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


def test_parse_git_url_https_with_ref():
    result = parse_git_url(
        "git+https://github.com/org/repo.git//scripts/validate.py@v1.2.3"
    )
    assert result == GitRef(
        repo_url="https://github.com/org/repo.git",
        path="scripts/validate.py",
        ref="v1.2.3",
    )


def test_parse_git_url_ssh_with_ref():
    result = parse_git_url("git+ssh://git@github.com/org/repo.git//lib/check.py@main")
    assert result == GitRef(
        repo_url="ssh://git@github.com/org/repo.git",
        path="lib/check.py",
        ref="main",
    )


def test_parse_git_url_no_ref():
    result = parse_git_url("git+https://github.com/org/repo.git//some/path.yaml")
    assert result == GitRef(
        repo_url="https://github.com/org/repo.git",
        path="some/path.yaml",
        ref=None,
    )


def test_parse_git_url_invalid():
    with pytest.raises(ValueError, match="Invalid git URL format"):
        parse_git_url("https://github.com/org/repo.git")


def test_cache_key_deterministic():
    assert _cache_key("hello") == _cache_key("hello")
    assert _cache_key("a") != _cache_key("b")
    assert len(_cache_key("anything")) == 16


def test_resolve_auth_headers_from_config():
    entry = RemoteAuthEntry(type="token", token_env="MY_TOKEN")
    auth_config = {"github.com": entry}

    with patch.dict("os.environ", {"MY_TOKEN": "tok123"}, clear=False):
        headers = _resolve_auth_headers("github.com", auth_config)

    assert headers == {"Authorization": "Bearer tok123"}


def test_resolve_auth_headers_from_env_token(monkeypatch):
    monkeypatch.setenv("STACKSMITH_HTTP_TOKEN", "env-token")
    headers = _resolve_auth_headers("example.com", None)
    assert headers == {"Authorization": "Bearer env-token"}


def test_resolve_auth_headers_from_env_basic(monkeypatch):
    monkeypatch.setenv("STACKSMITH_HTTP_USERNAME", "user")
    monkeypatch.setenv("STACKSMITH_HTTP_PASSWORD", "pass")
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    headers = _resolve_auth_headers("example.com", None)
    assert "Authorization" in headers
    assert headers["Authorization"].startswith("Basic ")


def test_resolve_auth_headers_no_auth(monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_PASSWORD", raising=False)
    headers = _resolve_auth_headers("example.com", None)
    assert headers == {}


def test_resolve_git_env_ssh_key_from_config(monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    entry = RemoteAuthEntry(type="ssh", ssh_key_path="/home/user/.ssh/deploy_key")
    auth_config = {"github.com": entry}

    env = _resolve_git_env("github.com", auth_config)

    assert "GIT_SSH_COMMAND" in env
    assert "/home/user/.ssh/deploy_key" in env["GIT_SSH_COMMAND"]


def test_resolve_git_env_token_from_config(monkeypatch):
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)
    monkeypatch.delenv("STACKSMITH_GIT_TOKEN", raising=False)
    monkeypatch.setenv("DEPLOY_TOKEN", "tok-xyz")
    entry = RemoteAuthEntry(type="token", token_env="DEPLOY_TOKEN")
    auth_config = {"github.com": entry}

    env = _resolve_git_env("github.com", auth_config)

    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert "tok-xyz" in env.get("GIT_CONFIG_KEY_0", "")


def test_resolve_git_env_fallback_env_token(monkeypatch):
    monkeypatch.setenv("STACKSMITH_GIT_TOKEN", "fallback-tok")
    monkeypatch.delenv("STACKSMITH_GIT_SSH_KEY", raising=False)

    env = _resolve_git_env("example.com", None)

    assert env.get("GIT_CONFIG_COUNT") == "1"
    assert "fallback-tok" in env.get("GIT_CONFIG_KEY_0", "")


def test_fetch_http_downloads_and_caches(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.content = b"file-contents"
    mock_resp.raise_for_status = MagicMock()

    with patch("stacksmith.remote.requests.get", return_value=mock_resp) as mock_get:
        result = _fetch_http("https://example.com/scripts/validate.py", tmp_path, None)
        assert result.exists()
        assert result.read_bytes() == b"file-contents"
        mock_get.assert_called_once()

        # Second call should use cache
        result2 = _fetch_http("https://example.com/scripts/validate.py", tmp_path, None)
        assert result2 == result
        mock_get.assert_called_once()  # no second call


def test_fetch_http_raises_on_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("STACKSMITH_HTTP_TOKEN", raising=False)
    monkeypatch.delenv("STACKSMITH_HTTP_USERNAME", raising=False)

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("404 Not Found")

    with patch("stacksmith.remote.requests.get", return_value=mock_resp):
        with pytest.raises(Exception, match="404 Not Found"):
            _fetch_http("https://example.com/missing.yaml", tmp_path, None)


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

    with (
        patch("stacksmith.utils.shutil.which", return_value="/usr/bin/git"),
        patch("stacksmith.utils.subprocess.run") as mock_run,
    ):
        mock_run.return_value = SimpleNamespace(returncode=0, stderr="")

        # Pre-create the expected target file so _fetch_git doesn't raise
        ref = parse_git_url(
            "git+https://github.com/org/repo.git//scripts/validate.py@main"
        )
        clone_dir = (
            tmp_path / "git" / f"{_cache_key(ref.repo_url)}-{_cache_key('main')}"
        )
        clone_dir.mkdir(parents=True)
        (clone_dir / "scripts").mkdir()
        (clone_dir / "scripts" / "validate.py").write_text("print('ok')")

        result = resolve_remote(
            "git+https://github.com/org/repo.git//scripts/validate.py@main",
            tmp_path,
        )
        assert result.name == "validate.py"


def test_resolve_remote_invalid_scheme():
    with pytest.raises(ValueError, match="Not a remote URL"):
        resolve_remote("/local/path.yaml", Path("/cache"))


def test_resolve_if_remote_local_path():
    result = resolve_if_remote("/tmp/config.yaml", Path("/cache"))
    assert result == Path("/tmp/config.yaml")


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
