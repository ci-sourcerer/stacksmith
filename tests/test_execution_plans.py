import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from stacksmith import execution_plans
from stacksmith.ci.contracts import CiExecutionManifest, CiExecutionRow
from stacksmith.cli import main as cli_main
from stacksmith.exceptions import StacksmithError
from stacksmith.loading import load_config


def _save_plan(plan: Path) -> None:
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_bytes(b"reviewed binary with sensitive values")
    (plan.parent / "stacksmith.tf.json").write_text('{"resource": {}}')
    (plan.parent / "terragrunt.hcl.json").write_text('{"terraform": {"source": "."}}')
    (plan.parent / ".terraform.lock.hcl").write_text('provider "locked" {}')
    execution_plans.save_execution_plan_context(
        plan,
        plan.parent,
        load_config(Path(__file__).parent / "fixtures/sample_config_local.yaml"),
        "service",
        ["api"],
    )


@pytest.fixture
def saved_plan(tmp_path: Path) -> Path:
    plan = tmp_path / "reviewed.tfplan"
    _save_plan(plan)
    execution_plans.bind_execution_plan(plan, "invocation")
    return plan


@pytest.mark.parametrize("destroy", [False, True])
def test_apply_consumes_binary_and_restores_reviewed_configuration(
    saved_plan: Path, monkeypatch, destroy: bool
):
    (saved_plan.parent / "stacksmith.tf.json").write_text("changed after preview")
    runner = Mock(return_value=0)
    monkeypatch.setattr(execution_plans, "run_terragrunt", runner)

    assert (
        execution_plans.apply_execution_plan(
            saved_plan,
            "invocation",
            saved_plan.parent / "apply-summary.json",
            destroy=destroy,
        )
        == 0
    )

    assert runner.call_count == 1
    assert runner.call_args.args == (
        ["apply", str(saved_plan.resolve())],
        saved_plan.parent,
    )
    assert (saved_plan.parent / "stacksmith.tf.json").read_text() == '{"resource": {}}'
    assert (
        saved_plan.parent / ".terraform.lock.hcl"
    ).read_text() == 'provider "locked" {}'
    assert saved_plan.stat().st_mode & 0o777 == 0o600
    report = json.loads((saved_plan.parent / "apply-summary.json").read_text())
    assert report["command"] == ("destroy" if destroy else "apply")
    assert report["selection"]["reviewed_plan_sha256"]


@pytest.mark.parametrize("problem", ["missing", "modified", "wrong_run", "expired"])
def test_unusable_plan_fails_before_any_execution(
    saved_plan: Path, monkeypatch, problem: str
):
    runner = Mock(return_value=0)
    monkeypatch.setattr(execution_plans, "run_terragrunt", runner)
    if problem == "missing":
        saved_plan.unlink()
    elif problem == "modified":
        saved_plan.write_bytes(b"different plan")
    elif problem == "expired":
        monkeypatch.setattr(execution_plans.time, "time", lambda: 10**12)

    with pytest.raises(StacksmithError):
        execution_plans.apply_execution_plan(
            saved_plan,
            "other" if problem == "wrong_run" else "invocation",
            saved_plan.parent / "apply-summary.json",
        )
    runner.assert_not_called()


def test_encrypted_handoff_restores_exact_bytes(saved_plan: Path, monkeypatch):
    monkeypatch.setenv("STACKSMITH_PLAN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    original = saved_plan.read_bytes()
    execution_plans.transfer_execution_plan(saved_plan, "invocation", decrypt=False)
    assert b"sensitive values" not in saved_plan.with_suffix(".enc").read_bytes()
    saved_plan.unlink()
    execution_plans.execution_plan_context_path(saved_plan).unlink()

    execution_plans.transfer_execution_plan(saved_plan, "invocation", decrypt=True)

    assert saved_plan.read_bytes() == original
    assert saved_plan.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("problem", ["wrong_key", "wrong_run", "modified", "expired"])
def test_invalid_handoff_never_writes_plaintext(
    saved_plan: Path, monkeypatch, problem: str
):
    monkeypatch.setenv("STACKSMITH_PLAN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    execution_plans.transfer_execution_plan(saved_plan, "invocation", decrypt=False)
    saved_plan.unlink()
    if problem == "wrong_key":
        monkeypatch.setenv(
            "STACKSMITH_PLAN_ENCRYPTION_KEY", Fernet.generate_key().decode()
        )
    elif problem == "modified":
        saved_plan.with_suffix(".enc").write_bytes(b"tampered ciphertext")
    elif problem == "expired":
        monkeypatch.setattr(execution_plans.time, "time", lambda: 10**12)
    with pytest.raises(StacksmithError):
        execution_plans.transfer_execution_plan(
            saved_plan,
            "other" if problem == "wrong_run" else "invocation",
            decrypt=True,
        )
    assert not saved_plan.exists()


@pytest.mark.parametrize("name", ["../outside.tf", "/tmp/outside.tf", "unrelated.json"])
def test_plan_context_rejects_unsafe_snapshot_paths(saved_plan: Path, name: str):
    payload = json.loads(
        execution_plans.execution_plan_context_path(saved_plan).read_text()
    )
    payload["files"][name] = "untrusted"
    with pytest.raises(ValueError, match="invalid configuration filename"):
        execution_plans.ExecutionPlanContext.model_validate(payload)


@pytest.mark.parametrize("command", ["apply", "destroy"])
def test_ci_lifecycle_uses_only_its_reviewed_plan(
    tmp_path: Path, monkeypatch, command: str
):
    manifest = CiExecutionManifest(
        command=command,
        config_ref="managed/config.yaml",
        workdir=str(tmp_path),
        tags=["selected"],
        matrix=[CiExecutionRow(environment="dev", runfile="stacksmith.yaml")],
    )
    monkeypatch.setenv("GITHUB_SHA", "reviewed-commit")
    monkeypatch.setenv("STACKSMITH_CI_EXECUTION_ID", "run-1-attempt-1")

    def preview(args, phase):
        assert phase == "plan"
        assert args.tag == ["selected"]
        _save_plan(args.out)
        return 0

    preview_handler = Mock(side_effect=preview)
    monkeypatch.setattr(cli_main, "_cmd_terragrunt_action", preview_handler)
    runner = Mock(return_value=0)
    monkeypatch.setattr(execution_plans, "run_terragrunt", runner)

    assert cli_main._execute_ci_manifest(manifest, "dev", "plan") == 0
    assert cli_main._execute_ci_manifest(manifest, "dev", command) == 0
    assert preview_handler.call_count == 1
    assert runner.call_args.args[0] == [
        "apply",
        str(tmp_path / ".stacksmith-ci/dev/reviewed.tfplan"),
    ]
    assert not (tmp_path / ".stacksmith-ci/dev/reviewed.tfplan").exists()


def test_ci_apply_without_preview_never_replans(tmp_path: Path, monkeypatch):
    manifest = CiExecutionManifest(
        command="apply",
        config_ref="managed/config.yaml",
        workdir=str(tmp_path),
        matrix=[CiExecutionRow(environment="dev", runfile="stacksmith.yaml")],
    )
    fresh_handler = Mock(return_value=0)
    monkeypatch.setattr(cli_main, "_cmd_terragrunt_action", fresh_handler)
    with pytest.raises(StacksmithError, match="reviewed plan"):
        cli_main._execute_ci_manifest(manifest, "dev", "apply")
    fresh_handler.assert_not_called()
