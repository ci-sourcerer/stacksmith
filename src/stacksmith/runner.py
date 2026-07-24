import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from .enums import TerragruntAction
from .models import RemoteAuthConfig, ToolConfig
from .remote import apply_terragrunt_auth_env
from .tooling import ResolvedToolchain, resolve_toolchain
from .utils import env_truthy
from .validation import (
    PlanValidationExitCode,
    PlanValidationResult,
    evaluate_plan_validations_with_results,
    process_plan_validation_results,
)


def _has_enabled_plan_validations(config: ToolConfig) -> bool:
    return any(rule.enabled for rule in config.plan_validations.values())


def _parse_terragrunt_action(
    value: str | TerragruntAction | None,
) -> TerragruntAction | None:
    if value is None:
        return None
    if isinstance(value, TerragruntAction):
        return value

    try:
        return TerragruntAction(value)
    except ValueError:
        return None


def _should_run_plan_validation_flow(
    args: list[str],
    config: ToolConfig | None,
    save_plan_json: Path | None = None,
    fail_on_changes: bool = False,
) -> bool:
    return bool(
        args
        and _parse_terragrunt_action(args[0]) == TerragruntAction.PLAN
        and "-destroy" not in args
        and (
            save_plan_json is not None
            or (config is not None and _has_enabled_plan_validations(config))
            or fail_on_changes
        )
    )


def _build_env(auth_config: RemoteAuthConfig | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["TG_TF_PATH"] = _RESOLVED_TOOLCHAIN.tofu
    apply_terragrunt_auth_env(env, auth_config)
    LOGGER.debug(
        "Terragrunt environment TG_TF_PATH={tg_tf_path}", tg_tf_path=env["TG_TF_PATH"]
    )
    return env


_TOOL_VERSION_CHECKED = False
_RESOLVED_TOOLCHAIN = ResolvedToolchain(
    tofu=os.environ.get("TG_TF_PATH", "tofu"),
    terragrunt=os.environ.get("STACKSMITH_TERRAGRUNT_PATH", "terragrunt"),
)


def _should_skip_tool_version_check() -> bool:
    return env_truthy("SKIP_TOOL_VERSION_CHECK", prefix="STACKSMITH_")


def _check_required_tool_versions(
    config: ToolConfig | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> None:
    global _TOOL_VERSION_CHECKED
    global _RESOLVED_TOOLCHAIN

    if _TOOL_VERSION_CHECKED:
        return
    if _should_skip_tool_version_check():
        LOGGER.debug(
            "Skipping external tool version checks because stacksmith env var is set"
        )
        _RESOLVED_TOOLCHAIN = ResolvedToolchain(
            tofu=os.environ.get("TG_TF_PATH", "tofu"),
            terragrunt=os.environ.get("STACKSMITH_TERRAGRUNT_PATH", "terragrunt"),
        )
        _TOOL_VERSION_CHECKED = True
        return

    _RESOLVED_TOOLCHAIN = resolve_toolchain(
        config.tools if config is not None and hasattr(config, "tools") else None,
        cache_dir,
        auth_config,
        subprocess_module=subprocess,
    )
    _TOOL_VERSION_CHECKED = True


def _resolve_plan_json_output_path(
    base_path: Path,
    stack_name: str,
    multiple: bool = False,
) -> Path:
    if multiple or base_path.suffix.lower() != ".json":
        return base_path / f"{stack_name}.json"
    return base_path


def _has_plan_changes(plan_data: dict[str, Any]) -> bool:
    changes = plan_data.get("resource_changes", [])
    if not isinstance(changes, list):
        return False

    for change in changes:
        actions = (change.get("change") or {}).get("actions")
        if not isinstance(actions, list):
            return True
        if any(isinstance(action, str) and action != "no-op" for action in actions):
            return True

    return False


def _run_terragrunt(
    cmd: list[str],
    working_dir: Path,
    capture_output: bool = False,
    auth_config: RemoteAuthConfig | None = None,
) -> subprocess.CompletedProcess[str] | int:
    kwargs = {
        "cwd": working_dir,
        "env": _build_env(auth_config),
    }

    if capture_output:
        kwargs.update({"capture_output": True, "text": True})
        return subprocess.run(cmd, **kwargs)

    return subprocess.run(
        cmd,
        stdout=sys.stderr,
        stderr=sys.stderr,
        **kwargs,
    )


def _run_terragrunt_streaming(
    cmd: list[str],
    working_dir: Path,
    auth_config: RemoteAuthConfig | None = None,
) -> int:
    return int(_run_terragrunt(cmd, working_dir, auth_config=auth_config).returncode)


def _run_terragrunt_capture_text(
    cmd: list[str],
    working_dir: Path,
    auth_config: RemoteAuthConfig | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_terragrunt(
        cmd,
        working_dir,
        capture_output=True,
        auth_config=auth_config,
    )


def check_plan_validations(
    config: ToolConfig,
    plan_data: dict[str, Any],
    stack_name: str,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> list[PlanValidationResult]:
    """Evaluate reserved post-plan validations without wiring them into CLI flow.

    Args:
        config: Loaded tool configuration.
        plan_data: Parsed OpenTofu JSON plan output.
        stack_name: Human-readable stack identifier.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.

    Returns:
        Structured plan validation outcomes for each enabled rule.
    """
    return evaluate_plan_validations_with_results(
        config.plan_validations,
        plan_data,
        base_path=config.source_path.parent if config.source_path is not None else None,
        context={"stack_name": stack_name},
        cache_dir=cache_dir,
        auth_config=auth_config,
    )


def run_terragrunt(
    args: list[str],
    working_dir: Path,
    auto_approve: bool = False,
    config: ToolConfig | None = None,
    stack_name: str | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    save_plan_json: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    plan_validation_results: list[PlanValidationResult] | None = None,
    no_cas: bool = False,
) -> int:
    """Run a single-stack Terragrunt command.

    Args:
        args: Terragrunt subcommand and arguments (e.g. ["plan"]).
        working_dir: Directory containing terragrunt.hcl.
        auto_approve: If `True`, append `--auto-approve` for apply/destroy.
        config: Optional loaded tool config used for plan validations.
        stack_name: Optional stack identifier used in validation context.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        save_plan_json: Optional file path used to persist rendered plan JSON.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
        plan_validation_results: Optional list to collect per-rule plan
            validation outcomes for external reporting.

    Returns:
        Process exit code.
    """
    _check_required_tool_versions(
        config=config,
        cache_dir=cache_dir,
        auth_config=auth_config,
    )
    cmd = [_RESOLVED_TOOLCHAIN.terragrunt]
    if no_cas:
        cmd.append("--no-cas")
    cmd.extend(args)
    first_action = _parse_terragrunt_action(args[0] if args else None)

    if (
        args
        and auto_approve
        and first_action in {TerragruntAction.APPLY, TerragruntAction.DESTROY}
    ):
        cmd.append("--auto-approve")

    LOGGER.info("Running: {command}", command=" ".join(cmd))
    LOGGER.debug("Working dir: {working_dir}", working_dir=working_dir)

    if _should_run_plan_validation_flow(
        args,
        config,
        save_plan_json=save_plan_json,
        fail_on_changes=fail_on_changes,
    ):
        enabled_rules = (
            [name for name, rule in config.plan_validations.items() if rule.enabled]
            if config is not None
            else []
        )
        LOGGER.debug("Enabled plan validations: {rules}", rules=enabled_rules)
        return _run_plan_validations(
            cmd,
            args,
            working_dir,
            config,
            stack_name=stack_name or working_dir.name,
            cache_dir=cache_dir,
            auth_config=auth_config,
            save_plan_json=save_plan_json,
            strict_validation_warnings=strict_validation_warnings,
            fail_on_changes=fail_on_changes,
            plan_validation_results=plan_validation_results,
        )

    return _run_terragrunt_streaming(
        cmd,
        working_dir,
        auth_config=auth_config,
    )


def _run_plan_validations(
    plan_cmd: list[str],
    args: list[str],
    working_dir: Path,
    config: ToolConfig | None,
    stack_name: str,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    save_plan_json: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    plan_validation_results: list[PlanValidationResult] | None = None,
) -> int | PlanValidationExitCode:
    plan_cmd_prefix = plan_cmd[: len(plan_cmd) - len(args)]
    with tempfile.NamedTemporaryFile(
        dir=working_dir,
        prefix="stacksmith-",
        suffix=".plan",
        delete=False,
    ) as tmp_plan:
        plan_path = Path(tmp_plan.name)

    try:
        plan_with_out_cmd = [
            *plan_cmd_prefix,
            TerragruntAction.PLAN.value,
            *args[1:],
            "-out",
            str(plan_path),
        ]
        LOGGER.info(
            "Running plan for JSON validation: {command}",
            command=" ".join(plan_with_out_cmd),
        )
        LOGGER.debug("Temporary plan output path: {plan_path}", plan_path=plan_path)
        plan_result_code = _run_terragrunt_streaming(
            plan_with_out_cmd,
            working_dir,
            auth_config=auth_config,
        )
        if plan_result_code != 0:
            return plan_result_code

        show_cmd = [*plan_cmd_prefix, "show", "-json", str(plan_path)]
        LOGGER.debug("Rendering plan JSON: {command}", command=" ".join(show_cmd))
        show_result = _run_terragrunt_capture_text(
            show_cmd,
            working_dir,
            auth_config=auth_config,
        )
        LOGGER.debug(
            "Terragrunt show return code: {return_code}",
            return_code=show_result.returncode,
        )
        LOGGER.debug(
            "Terragrunt show stderr: {stderr}", stderr=show_result.stderr.strip()
        )
        if show_result.returncode != 0:
            if show_result.stderr:
                LOGGER.error(show_result.stderr.strip())
            return show_result.returncode

        if save_plan_json is not None:
            save_path = _resolve_plan_json_output_path(save_plan_json, stack_name)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(show_result.stdout, encoding="utf-8")
            LOGGER.info("Saved plan JSON to {path}", path=save_path)

        try:
            plan_data = json.loads(show_result.stdout)
            LOGGER.debug("Parsed plan")
        except json.JSONDecodeError as exc:
            LOGGER.error("Failed to parse plan JSON output: {exc}", exc=exc)
            return 1

        if config is None or not _has_enabled_plan_validations(config):
            if fail_on_changes and _has_plan_changes(plan_data):
                LOGGER.info("Failing plan because resource changes were detected")
                return PlanValidationExitCode.FAIL
            return PlanValidationExitCode.PASS

        outcomes = check_plan_validations(
            config,
            plan_data,
            stack_name=stack_name,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )
        if plan_validation_results is not None:
            plan_validation_results.extend(outcomes)

        validation_result = process_plan_validation_results(
            outcomes,
            strict_validation_warnings=strict_validation_warnings,
        )
        if validation_result != PlanValidationExitCode.PASS:
            return validation_result

        if fail_on_changes and _has_plan_changes(plan_data):
            LOGGER.info("Failing plan because resource changes were detected")
            return PlanValidationExitCode.FAIL

        return PlanValidationExitCode.PASS
    finally:
        plan_path.unlink(missing_ok=True)


def run_terragrunt_all_ordered(
    action: str | TerragruntAction | list[str],
    stack_build_dirs: dict[str, Path],
    auto_approve: bool = False,
    config: ToolConfig | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    stack_args_by_name: dict[str, list[str]] | None = None,
    save_plan_json: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    plan_validation_results: list[PlanValidationResult] | None = None,
    no_cas: bool = False,
) -> int:
    """Run Terragrunt across generated stack directories in dependency order.

    Args:
        action: Terragrunt action or Terragrunt arg list.
        stack_build_dirs: Ordered mapping of stack name to generated build directory.
            The expected order is dependency-first.
        auto_approve: If `True`, append `--auto-approve` for apply/destroy.
        config: Optional loaded tool config used for plan validations.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        stack_args_by_name: Optional map of stack name to Terragrunt args.
            When provided, these args are used per stack instead of the shared `action`.
        save_plan_json: Optional directory used to persist rendered plan JSON for
            each planned stack.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
        plan_validation_results: Optional list to collect per-rule plan
            validation outcomes for external reporting.

    Returns:
        Process exit code (first non-zero code short-circuits the run).
    """
    action_name = action[0] if isinstance(action, list) else action
    action_enum = _parse_terragrunt_action(action_name)

    stack_items = list(stack_build_dirs.items())
    if action_enum == TerragruntAction.DESTROY:
        stack_items = list(reversed(stack_items))

    for stack_name, stack_dir in stack_items:
        terragrunt_args = (
            stack_args_by_name.get(stack_name)
            if stack_args_by_name is not None
            else (
                list(action)
                if isinstance(action, list)
                else [action.value if isinstance(action, TerragruntAction) else action]
            )
        )
        if terragrunt_args is None:
            continue

        LOGGER.info("Stack: {stack_name}", stack_name=stack_name)
        stack_save_plan_json = None
        if (
            save_plan_json is not None
            and terragrunt_args
            and terragrunt_args[0] == TerragruntAction.PLAN.value
        ):
            stack_save_plan_json = _resolve_plan_json_output_path(
                save_plan_json,
                stack_name,
                multiple=True,
            )
        exit_code = run_terragrunt(
            terragrunt_args,
            stack_dir,
            auto_approve=auto_approve,
            config=config,
            stack_name=stack_name,
            cache_dir=cache_dir,
            auth_config=auth_config,
            save_plan_json=stack_save_plan_json,
            strict_validation_warnings=strict_validation_warnings,
            fail_on_changes=fail_on_changes,
            plan_validation_results=plan_validation_results,
            no_cas=no_cas,
        )
        if exit_code != 0:
            return exit_code

    return 0
