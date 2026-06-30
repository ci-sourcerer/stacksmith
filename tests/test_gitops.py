import importlib.util
import io
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from stacksmith.gitops import (
    discover_environments,
    evaluate_environment_selection,
    normalize_discovery_mode,
    select_changed_environments,
    validate_discovery_mode,
)


def _create_env_files_layout(tmp_path: Path) -> None:
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev.yaml").write_text("stacks: []\n")
    (tmp_path / "environments" / "prod.yaml").write_text("stacks: []\n")


def test_normalize_discovery_mode_env_alias():
    assert normalize_discovery_mode("env") == "env-files"


def test_validate_discovery_mode_rejects_unsupported_value():
    with pytest.raises(ValueError, match="Unsupported discovery mode"):
        validate_discovery_mode("broken")


def test_evaluate_environment_selection_auto_detects_env_files_layout(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    selection = evaluate_environment_selection(
        gitops_root=str(tmp_path),
        discovery_mode="auto",
        event_name="workflow_dispatch",
        changed_paths=[],
    )

    assert selection.selected_environments == ["dev", "prod"]


def test_discover_environments_flat_files_filters_common_variants(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "stacksmith.dev.yaml").write_text("stacks: []\n")
    (tmp_path / "stacksmith.prod.yaml").write_text("stacks: []\n")
    (tmp_path / "stacksmith.common.yaml").write_text("stacks: []\n")

    environments, common_runfile = discover_environments("flat-files", tmp_path)

    assert environments == ["dev", "prod"]
    assert common_runfile == f"{tmp_path.as_posix()}/common/stacksmith.yaml"


def test_select_changed_environments_fan_out_for_common_changes():
    selected = select_changed_environments(
        ["examples/gitops-repo/common/stacksmith.yaml"],
        "env-files",
        "examples/gitops-repo/",
        (
            "examples/gitops-repo/common/",
            "examples/gitops-repo/manifests/common/",
        ),
        ["dev", "prod"],
    )

    assert selected == {"dev", "prod"}


def test_select_changed_environments_targets_env_file():
    selected = select_changed_environments(
        ["examples/gitops-repo/environments/dev.yaml"],
        "env-files",
        "examples/gitops-repo/",
        (
            "examples/gitops-repo/common/",
            "examples/gitops-repo/manifests/common/",
        ),
        ["dev", "prod"],
    )

    assert selected == {"dev"}


def test_select_changed_environments_targets_flat_file_env():
    selected = select_changed_environments(
        ["examples/gitops-repo/stacksmith.prod.yaml"],
        "flat-files",
        "examples/gitops-repo/",
        (
            "examples/gitops-repo/common/",
            "examples/gitops-repo/manifests/common/",
        ),
        ["dev", "prod"],
    )

    assert selected == {"prod"}


def test_select_changed_environments_targets_environment_manifest_path():
    selected = select_changed_environments(
        ["examples/gitops-repo/manifests/environments/prod/app-config.yaml"],
        "env-files",
        "examples/gitops-repo/",
        (
            "examples/gitops-repo/common/",
            "examples/gitops-repo/manifests/common/",
        ),
        ["dev", "prod"],
    )

    assert selected == {"prod"}


def test_evaluate_environment_selection_with_manual_targets(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    selection = evaluate_environment_selection(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        manual_environments="dev,prod",
        event_name="workflow_dispatch",
        changed_paths=[],
    )

    assert selection.selected_environments == ["dev", "prod"]
    assert selection.matrix == [
        {
            "environment": "dev",
            "runfile": f"{tmp_path.as_posix()}/common/stacksmith.yaml",
            "environment_runfile": f"{tmp_path.as_posix()}/environments/dev.yaml",
            "stacksmith_args": "--var environment=dev",
        },
        {
            "environment": "prod",
            "runfile": f"{tmp_path.as_posix()}/common/stacksmith.yaml",
            "environment_runfile": f"{tmp_path.as_posix()}/environments/prod.yaml",
            "stacksmith_args": "--var environment=prod",
        },
    ]


def test_evaluate_environment_selection_manual_unknown_environment_fails(
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)

    with pytest.raises(ValueError, match="Unknown manual environment"):
        evaluate_environment_selection(
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            manual_environments="dev,staging",
            event_name="workflow_dispatch",
            changed_paths=[],
        )


def test_evaluate_environment_selection_push_no_matches_is_no_op(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    selection = evaluate_environment_selection(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="push",
        changed_paths=["docs/readme.md"],
    )

    assert selection.selected_environments == []
    assert selection.matrix == []


def test_evaluate_environment_selection_requires_discovered_or_manual(tmp_path: Path):
    with pytest.raises(ValueError, match="No environments were discovered"):
        evaluate_environment_selection(
            gitops_root=str(tmp_path),
            discovery_mode="folders",
            event_name="workflow_dispatch",
        )


def test_select_gitops_environments_writes_stdout_without_github_output(monkeypatch):
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "select_gitops_environments.py"
    )
    spec = importlib.util.spec_from_file_location(
        "select_gitops_environments", script_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stream)

    module._write_outputs(
        [
            {"environment": "dev", "runfile": "common/stacksmith.yaml"},
        ]
    )

    assert "matrix=[{" in stream.getvalue()
    assert "count=1" in stream.getvalue()


def test_evaluate_environment_selection_uses_jenkins_ci_context(
    monkeypatch, tmp_path: Path
):
    _create_env_files_layout(tmp_path)

    monkeypatch.delenv("CALLER_EVENT_NAME", raising=False)
    monkeypatch.delenv("CALLER_BASE_REF", raising=False)
    monkeypatch.delenv("CALLER_EVENT_BEFORE", raising=False)
    monkeypatch.delenv("CALLER_SHA", raising=False)
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.example")
    monkeypatch.setenv("CHANGE_ID", "42")
    monkeypatch.setenv("CHANGE_TARGET", "main")
    monkeypatch.setenv("GIT_COMMIT", "abc123")

    selection = evaluate_environment_selection(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="",
        changed_paths=[],
    )

    assert selection.selected_environments == ["dev", "prod"]
    assert selection.changed_paths == []


def test_changed_paths_for_event_git_error_resilience(monkeypatch):
    import subprocess

    from stacksmith.gitops import changed_paths_for_event

    def mock_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "git")

    def mock_check_output(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "git")

    monkeypatch.setattr(subprocess, "run", mock_run)
    monkeypatch.setattr(subprocess, "check_output", mock_check_output)

    # Verify we gracefully return [] on failures instead of raising exceptions
    assert changed_paths_for_event("push", before="sha1", after="sha256") == []
    assert changed_paths_for_event("pull_request", base_ref="main") == []


def test_select_gitops_environments_uses_gitops_root_env_alias(monkeypatch):
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "select_gitops_environments.py"
    )
    spec = importlib.util.spec_from_file_location(
        "select_gitops_environments", script_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    captured = {}

    class DummyGitOps:
        def evaluate_environment_selection(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                matrix=[{"environment": "dev", "runfile": "common/stacksmith.yaml"}]
            )

    monkeypatch.setenv("STACKSMITH_GITOPS_ROOT", "examples/gitops-repo")
    monkeypatch.delenv("INPUT_GITOPS_ROOT", raising=False)
    monkeypatch.setattr(module, "_load_module", lambda *_args, **_kwargs: DummyGitOps())
    monkeypatch.setattr(module, "_write_outputs", lambda matrix: None)

    assert module._main() == 0
    assert captured["gitops_root"] == str(
        (Path(__file__).resolve().parents[1] / "examples" / "gitops-repo").resolve()
    )


def test_select_gitops_environments_resolves_relative_gitops_root_from_repo_root(
    monkeypatch,
):
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "select_gitops_environments.py"
    )
    spec = importlib.util.spec_from_file_location(
        "select_gitops_environments", script_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    monkeypatch.setenv("INPUT_GITOPS_ROOT", "examples/gitops-repo")
    monkeypatch.delenv("STACKSMITH_GITOPS_ROOT", raising=False)

    expected = str((script_path.parent.parent / "examples" / "gitops-repo").resolve())
    assert module._resolve_gitops_root() == expected


def test_gitops_logging_uses_debug_level_when_enabled(monkeypatch, tmp_path: Path):
    _create_env_files_layout(tmp_path)

    monkeypatch.setenv("STACKSMITH_CI_DEBUG", "1")

    import stacksmith.gitops as gitops

    logger = logging.getLogger(gitops.LOGGER_NAME)
    logger.setLevel(logging.NOTSET)
    logger.handlers.clear()

    with pytest.raises(ValueError):
        evaluate_environment_selection(
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            manual_environments="dev,missing",
            event_name="workflow_dispatch",
            changed_paths=[],
        )

    assert logger.isEnabledFor(logging.DEBUG)


def test_evaluate_environment_selection_uses_jenkins_ci_context(
    monkeypatch, tmp_path: Path
):
    _create_env_files_layout(tmp_path)

    monkeypatch.delenv("CALLER_EVENT_NAME", raising=False)
    monkeypatch.delenv("CALLER_BASE_REF", raising=False)
    monkeypatch.delenv("CALLER_EVENT_BEFORE", raising=False)
    monkeypatch.delenv("CALLER_SHA", raising=False)
    monkeypatch.setenv("JENKINS_URL", "https://jenkins.example")
    monkeypatch.setenv("CHANGE_ID", "42")
    monkeypatch.setenv("CHANGE_TARGET", "main")
    monkeypatch.setenv("GIT_COMMIT", "abc123")

    selection = evaluate_environment_selection(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="",
        changed_paths=[],
    )

    assert selection.selected_environments == ["dev", "prod"]
