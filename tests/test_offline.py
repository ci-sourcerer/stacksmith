import json
import subprocess
from functools import partial
from inspect import signature
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
import requests

from stacksmith import api, remote
from stacksmith.cli import main as cli_main
from stacksmith.cli.parser import build_parser
from stacksmith.exceptions import StacksmithConfigError


def _serve_input(documents, url, **_kwargs):
    response = requests.Response()
    response.status_code = 200
    response._content = documents[Path(urlparse(url).path).name]
    return response


def _clone_inputs(documents, _repository, destination, **_kwargs):
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in documents.items():
        (destination / name).write_bytes(content)
    return subprocess.CompletedProcess([], 0, stdout="", stderr="")


@pytest.fixture(params=["http", "git"])
def remote_inputs(request, monkeypatch, tmp_path):
    """Provide remote input documents with controllable HTTP and Git transports.

    Args:
        request: Parametrized HTTP or Git transport selection.
        monkeypatch: Fixture for transport overrides.
        tmp_path: Isolated source, cache, and lockfile directory.

    Returns:
        Input references, transport mocks, and build locations.
    """
    (tmp_path / "module").mkdir()
    references = {
        kind: f"https://example.invalid/{kind}.yaml"
        if request.param == "http"
        else f"git+https://example.invalid/inputs.git//{kind}.yaml@main"
        for kind in ("config", "stack", "vars", "runfile")
    }
    documents = {
        "config.yaml": json.dumps(
            {
                "backend": {"data": {"type": "local"}},
                "default_module_mapping": {
                    "source": {
                        "source": "local",
                        "data": {"path": str(tmp_path / "module")},
                    }
                },
            }
        ).encode(),
        "stack.yaml": b"name: app\ncomponents:\n  item:\n    type: data\n    properties:\n      input: '{{ inputs.message }}'\n",
        "vars.yaml": b"message: from cached vars\n",
        "runfile.yaml": b"description: offline inputs\n",
    }
    get = Mock(side_effect=partial(_serve_input, documents))
    clone = Mock(side_effect=partial(_clone_inputs, documents))
    monkeypatch.setattr(remote.requests, "get", get)
    monkeypatch.setattr(remote, "clone_git_repo", clone)
    return SimpleNamespace(
        references=references,
        build=tmp_path / "build",
        cache=tmp_path / "build/.cache",
        lockfile=tmp_path / "stacksmith.lock.yaml",
        get=get,
        clone=clone,
    )


def _lock_arguments(inputs):
    return {
        "stack_file": inputs.references["stack"],
        "config": [inputs.references["config"]],
        "vars_file": inputs.references["vars"],
        "runfiles": [inputs.references["runfile"]],
        "build_dir": inputs.build,
        "lockfile": inputs.lockfile,
    }


def _block_transports(inputs):
    for transport in (inputs.get, inputs.clone):
        transport.reset_mock()
        transport.side_effect = AssertionError("Offline mode attempted network access")


@pytest.mark.parametrize("missing", ["config", "stack", "vars", "runfile"])
def test_offline_lock_rejects_uncached_inputs_without_network(remote_inputs, missing):
    paths = {
        kind: remote.resolve_remote(reference, remote_inputs.cache)
        for kind, reference in remote_inputs.references.items()
    }
    paths[missing].unlink()
    _block_transports(remote_inputs)
    with pytest.raises(
        StacksmithConfigError, match="Offline mode requires cached artifacts"
    ):
        api.lock_stack(**_lock_arguments(remote_inputs), offline=True, check=True)
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()


def test_offline_generation_uses_cached_inputs_and_verifies_hashes(remote_inputs):
    api.lock_stack(**_lock_arguments(remote_inputs))
    _block_transports(remote_inputs)
    assert api.lock_stack(**_lock_arguments(remote_inputs), offline=True, check=True)[
        "in_sync"
    ]
    output = api.generate_stack(**_lock_arguments(remote_inputs), offline=True)
    assert (
        json.loads((output / "stacksmith.tf.json").read_text())["module"]["item"][
            "input"
        ]
        == "from cached vars"
    )
    remote.resolve_remote(
        remote_inputs.references["vars"], remote_inputs.cache
    ).write_text("message: tampered\n")
    with pytest.raises(StacksmithConfigError, match="Lockfile mismatch"):
        api.generate_stack(**_lock_arguments(remote_inputs), offline=True)
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()


@pytest.mark.parametrize(
    "command", [api.lock_stack, api.generate_stack, api.run_stack_action]
)
def test_offline_no_cache_rejection_preserves_cache(remote_inputs, command):
    cached = remote.resolve_remote(
        remote_inputs.references["config"], remote_inputs.cache
    )
    _block_transports(remote_inputs)
    with pytest.raises(
        StacksmithConfigError, match="cannot be combined with --no-cache"
    ):
        command(
            **_lock_arguments(remote_inputs),
            offline=True,
            no_cache=True,
            **({"action": "plan"} if command is api.run_stack_action else {}),
        )
    assert cached.is_file()
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()


def test_cli_offline_rejects_uncached_runfile_before_fetch(remote_inputs):
    _block_transports(remote_inputs)
    args = build_parser().parse_args(
        [
            "generate",
            "--runfile",
            remote_inputs.references["runfile"],
            "--offline",
            "--build-dir",
            str(remote_inputs.build),
        ]
    )
    with pytest.raises(
        StacksmithConfigError, match="Offline mode requires cached artifacts"
    ):
        cli_main._cmd_generate(args)
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()


@pytest.mark.parametrize("command", [api.generate_stack, api.run_stack_action])
def test_offline_runtime_rejects_missing_config_before_fetch(remote_inputs, command):
    _block_transports(remote_inputs)
    with pytest.raises(
        StacksmithConfigError, match="Offline mode requires cached artifacts"
    ):
        command(
            **_lock_arguments(remote_inputs),
            offline=True,
            **({"action": "init"} if command is api.run_stack_action else {}),
        )
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()


def test_positional_offline_policy_is_restored_after_failure(remote_inputs):
    arguments = signature(api.lock_stack).bind(
        **_lock_arguments(remote_inputs), offline=True, check=True
    )
    arguments.apply_defaults()
    with pytest.raises(
        StacksmithConfigError, match="Offline mode requires cached artifacts"
    ):
        api.lock_stack(*arguments.args, **arguments.kwargs)
    remote_inputs.get.assert_not_called()
    remote_inputs.clone.assert_not_called()
    assert api.lock_stack(**_lock_arguments(remote_inputs))["in_sync"]
    assert remote_inputs.get.called or remote_inputs.clone.called


@pytest.mark.parametrize("remote_inputs", ["http"], indirect=True)
def test_offline_lock_and_generation_accept_http_query_references(remote_inputs):
    remote_inputs.references = {
        kind: f"{reference}?ref=release%2Fv1"
        for kind, reference in remote_inputs.references.items()
    }
    api.lock_stack(**_lock_arguments(remote_inputs))
    _block_transports(remote_inputs)
    assert api.lock_stack(**_lock_arguments(remote_inputs), offline=True, check=True)[
        "in_sync"
    ]
    output = api.generate_stack(**_lock_arguments(remote_inputs), offline=True)
    assert (
        json.loads((output / "stacksmith.tf.json").read_text())["module"]["item"][
            "input"
        ]
        == "from cached vars"
    )
    remote_inputs.get.assert_not_called()
