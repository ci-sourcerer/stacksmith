from pathlib import Path

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
        },
        {
            "environment": "prod",
            "runfile": f"{tmp_path.as_posix()}/common/stacksmith.yaml",
            "environment_runfile": f"{tmp_path.as_posix()}/environments/prod.yaml",
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
