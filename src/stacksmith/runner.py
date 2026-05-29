import concurrent.futures
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from .enums import TerragruntAction
from .exceptions import StacksmithError
from .models import RemoteAuthConfig, ToolConfig
from .utils import env_truthy
from .validation import (
    PlanValidationExitCode,
    PlanValidationResult,
    evaluate_plan_validations_with_results,
    process_plan_validation_results,
)


def _has_enabled_plan_validations(config: ToolConfig) -> bool:
    return any(rule.enabled for rule in config.plan_validations.values())


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["TG_TF_PATH"] = env.get("TG_TF_PATH", "tofu")
    LOGGER.debug(
        "Terragrunt environment TG_TF_PATH={tg_tf_path}", tg_tf_path=env["TG_TF_PATH"]
    )
    return env


_TOOL_VERSION_CHECKED = False


def _should_skip_tool_version_check() -> bool:
    return env_truthy("SKIP_TOOL_VERSION_CHECK", prefix="STACKSMITH_")


def _parse_tool_version(output: str) -> tuple[int, int, int]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", output)
    if not match:
        raise StacksmithError(f"Could not parse tool version from output: {output!r}")

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3) or "0")
    return major, minor, patch


def _check_tool_version(tool_name: str, cmd: list[str]) -> None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except FileNotFoundError as exc:
        raise StacksmithError(
            f"Required tool '{tool_name}' was not found: {cmd[0]}"
        ) from exc

    output = (result.stdout or "") + "\n" + (result.stderr or "")
    output = output.strip()
    if result.returncode != 0:
        raise StacksmithError(
            f"Failed to query {tool_name} version using {cmd}: {output}"
        )

    version = _parse_tool_version(output)
    if not ((1, 0, 0) <= version < (2, 0, 0)):
        raise StacksmithError(
            f"Unsupported {tool_name} version {version[0]}.{version[1]}.{version[2]}; "
            "expected >=1.0.0 and <2.0.0"
        )

    LOGGER.debug(
        "Verified {tool_name} version {version}",
        tool_name=tool_name,
        version=".".join(str(part) for part in version),
    )


def _check_required_tool_versions() -> None:
    global _TOOL_VERSION_CHECKED
    if _TOOL_VERSION_CHECKED:
        return
    if _should_skip_tool_version_check():
        LOGGER.debug(
            "Skipping external tool version checks because stacksmith env var is set"
        )
        _TOOL_VERSION_CHECKED = True
        return

    tool_path = os.environ.get("TG_TF_PATH", "tofu")
    checks = {
        "terragrunt": ["terragrunt", "--version"],
        "tofu": [tool_path, "-version"],
    }

    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_check_tool_version, name, cmd): name
            for name, cmd in checks.items()
        }
        for future in concurrent.futures.as_completed(futures):
            tool_name = futures[future]
            try:
                future.result()
            except StacksmithError as exc:
                errors.append(str(exc))

    if errors:
        raise StacksmithError("; ".join(errors))

    _TOOL_VERSION_CHECKED = True


def _resolve_plan_json_output_path(
    base_path: Path,
    stack_name: str,
    *,
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


def _run_terragrunt_streaming(cmd: list[str], working_dir: Path) -> int:
    return subprocess.run(
        cmd,
        cwd=working_dir,
        env=_build_env(),
        stdout=sys.stderr,
        stderr=sys.stderr,
    ).returncode


def _run_terragrunt_capture_text(
    cmd: list[str],
    working_dir: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=working_dir,
        env=_build_env(),
        capture_output=True,
        text=True,
    )


def check_plan_validations(
    config: ToolConfig,
    plan_data: dict[str, Any],
    *,
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
    _check_required_tool_versions()
    cmd = ["terragrunt", *args]
    first_action = None
    if args:
        try:
            first_action = TerragruntAction(args[0])
        except ValueError:
            first_action = None

    if (
        args
        and auto_approve
        and first_action in {TerragruntAction.APPLY, TerragruntAction.DESTROY}
    ):
        cmd.append("--auto-approve")

    LOGGER.info("Running: {command}", command=" ".join(cmd))
    LOGGER.debug("Working dir: {working_dir}", working_dir=working_dir)

    if (
        args
        and first_action == TerragruntAction.PLAN
        and "-destroy" not in args
        and (
            save_plan_json is not None
            or (config is not None and _has_enabled_plan_validations(config))
            or fail_on_changes
        )
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
        )

    return _run_terragrunt_streaming(cmd, working_dir)


def _run_plan_validations(
    plan_cmd: list[str],
    args: list[str],
    working_dir: Path,
    config: ToolConfig | None,
    *,
    stack_name: str,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    save_plan_json: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    plan_validation_results: list[PlanValidationResult] | None = None,
) -> int | PlanValidationExitCode:
    with tempfile.NamedTemporaryFile(
        dir=working_dir,
        prefix="stacksmith-",
        suffix=".plan",
        delete=False,
    ) as tmp_plan:
        plan_path = Path(tmp_plan.name)

    try:
        plan_with_out_cmd = [
            plan_cmd[0],
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
        plan_result_code = _run_terragrunt_streaming(plan_with_out_cmd, working_dir)
        if plan_result_code != 0:
            return plan_result_code

        show_cmd = [plan_cmd[0], "show", "-json", str(plan_path)]
        LOGGER.debug("Rendering plan JSON: {command}", command=" ".join(show_cmd))
        show_result = _run_terragrunt_capture_text(show_cmd, working_dir)
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
) -> int:
    """Run Terragrunt across generated stack directories in dependency order.

    Args:
        action: Terragrunt action or Terragrunt arg list (e.g. ["plan", "-destroy"]).
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
    action_enum = None
    if isinstance(action_name, str):
        try:
            action_enum = TerragruntAction(action_name)
        except ValueError:
            action_enum = None
    elif isinstance(action_name, TerragruntAction):
        action_enum = action_name

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
        )
        if exit_code != 0:
            return exit_code

    return 0
