import json
import subprocess
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import requests

from stacksmith import api, remote
from stacksmith.models import GitReference, HttpReference


def _private_http_response(documents, url, headers, **_kwargs):
    response = requests.Response()
    response.url = url
    if urlparse(url).hostname == "private.example.invalid":
        response.status_code = (
            200 if headers.get("Authorization") == "Bearer test-token" else 401
        )
    else:
        assert "Authorization" not in headers
        response.status_code = 200
    response._content = documents[Path(urlparse(url).path).name]
    return response


def _private_git_clone(documents, repository, destination, env, **_kwargs):
    if urlparse(repository).hostname == "private.example.invalid":
        if (
            env.get("GIT_CONFIG_KEY_0")
            != "url.https://x-access-token:test-token@private.example.invalid/.insteadOf"
        ):
            return subprocess.CompletedProcess(
                [], 128, stdout="", stderr="Authentication required"
            )
    else:
        assert "x-access-token:test-token" not in env.get("GIT_CONFIG_KEY_0", "")
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in documents.items():
        (destination / name).write_bytes(content)
    return subprocess.CompletedProcess([], 0, stdout="", stderr="")


@pytest.fixture(params=["http", "git"])
def private_stacks(request, tmp_path, monkeypatch):
    """Provide private stack sources that reject unauthenticated access.

    Args:
        request: Parametrized HTTP or Git transport selection.
        tmp_path: Isolated source and cache directory.
        monkeypatch: Fixture for transport and credential environment overrides.

    Returns:
        Local configuration, transport mocks, and build locations.
    """
    for name in (
        "HTTP_TOKEN",
        "HTTP_USERNAME",
        "HTTP_PASSWORD",
        "GIT_TOKEN",
        "GIT_SSH_KEY",
    ):
        monkeypatch.delenv(f"STACKSMITH_{name}", raising=False)
    monkeypatch.setenv("STACKSMITH_TEST_REMOTE_TOKEN", "test-token")
    (tmp_path / "module").mkdir()
    (tmp_path / "config.yaml").write_text(
        json.dumps(
            {
                "backend": {"data": {"type": "local"}},
                "default_module_mapping": {
                    "source": {"source": "local", "data": {"path": "module"}}
                },
                "remote_auth": {
                    "private.example.invalid": {
                        "type": "token",
                        "token_env": "STACKSMITH_TEST_REMOTE_TOKEN",
                    }
                },
            }
        )
    )
    documents = {
        "base.yaml": b"name: app\ncomponents:\n  item:\n    type: data\n    properties:\n      input: base\n",
        "overlay.yaml": b"components:\n  item:\n    properties:\n      input: overlay\n",
    }
    get = Mock(side_effect=partial(_private_http_response, documents))
    clone = Mock(side_effect=partial(_private_git_clone, documents))
    monkeypatch.setattr(remote.requests, "get", get)
    monkeypatch.setattr(remote, "clone_git_repo", clone)
    monkeypatch.setattr(remote.sys.stdin, "isatty", lambda: False)
    return SimpleNamespace(
        protocol=request.param,
        root=tmp_path,
        config=[str(tmp_path / "config.yaml")],
        build=tmp_path / "build",
        get=get,
        clone=clone,
    )


def _stack_reference(protocol, path, structured, host="private.example.invalid"):
    if protocol == "http":
        if structured:
            return HttpReference(source="http", data={"url": f"https://{host}/{path}"})
        return f"https://{host}/{path}"
    if structured:
        return GitReference(
            source="git",
            data={"repo": f"https://{host}/stacks.git", "path": path, "ref": "main"},
        )
    return f"git+https://{host}/stacks.git//{path}@main"


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("command", ["generate", "lock", "run-all", "diagnose"])
def test_private_remote_stacks_use_configured_credentials(
    private_stacks, monkeypatch, structured, command
):
    references = [
        _stack_reference(private_stacks.protocol, path, structured)
        for path in ("base.yaml", "overlay.yaml")
    ]
    match command:
        case "generate":
            output = api.generate_stack(
                references, config=private_stacks.config, build_dir=private_stacks.build
            )
            assert output.is_dir()
            assert (
                json.loads((output / "stacksmith.tf.json").read_text())["module"][
                    "item"
                ]["input"]
                == "overlay"
            )
        case "lock":
            report = api.lock_stack(
                references,
                config=private_stacks.config,
                build_dir=private_stacks.build,
                lockfile=private_stacks.root / "stacksmith.lock.yaml",
            )
            assert report["in_sync"]
            assert report["artifact_count"] == 3
        case "run-all":
            execute = Mock(return_value=0)
            monkeypatch.setattr(api, "run_terragrunt_all_ordered", execute)
            assert (
                api.run_all_stacks(
                    "init",
                    private_stacks.root,
                    stacks=references[:1],
                    config=private_stacks.config,
                    build_dir=private_stacks.build,
                )
                == 0
            )
            assert (
                json.loads(
                    (private_stacks.build / "app/stacksmith.tf.json").read_text()
                )["module"]["item"]["input"]
                == "base"
            )
            execute.assert_called_once()
        case "diagnose":
            assert (
                api.inspect_cache_diagnostics(
                    references,
                    config=private_stacks.config,
                    build_dir=private_stacks.build,
                )["stack_name"]
                == "app"
            )
    if private_stacks.protocol == "http":
        assert private_stacks.get.call_count == (1 if command == "run-all" else 2)
        private_stacks.clone.assert_not_called()
    else:
        private_stacks.clone.assert_called_once()
        private_stacks.get.assert_not_called()


def test_stack_credentials_are_not_sent_to_unconfigured_hosts(private_stacks):
    output = api.generate_stack(
        _stack_reference(
            private_stacks.protocol, "base.yaml", False, "public.example.invalid"
        ),
        config=private_stacks.config,
        build_dir=private_stacks.build,
    )
    assert output.is_dir()
    assert (
        json.loads((output / "stacksmith.tf.json").read_text())["module"]["item"][
            "input"
        ]
        == "base"
    )
