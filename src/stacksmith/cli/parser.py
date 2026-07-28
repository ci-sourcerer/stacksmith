import argparse
from importlib.metadata import version as metadata_version
from pathlib import Path

from ..enums import TerragruntAction
from ..utils import env_truthy, stacksmith_env
from .args import (
    _add_apply_args,
    _add_common_args,
    _add_plan_output_args,
    _add_stack_arg,
    _add_target_selection_args,
    _add_validation_report_format_arg,
    _configure_ci_execute_from_env_parser,
    _configure_ci_execute_parser,
    _configure_ci_prepare_from_env_parser,
    _configure_ci_prepare_parser,
    _configure_ci_validate_parser,
    _configure_diagnose_parser,
    _configure_info_environments_parser,
    _configure_inspect_parser,
    _path_type,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the Stacksmith command-line parser.

    Returns:
        Configured root argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="stacksmith",
        description="YAML/JSON-driven Terragrunt wrapper",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{parser.prog} {metadata_version('stacksmith')}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate stack schema and variables",
    )
    _add_stack_arg(validate_parser)
    _add_common_args(validate_parser)
    _add_validation_report_format_arg(validate_parser)

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate .tf.json and terragrunt.hcl.json",
    )
    _add_stack_arg(generate_parser)
    _add_common_args(generate_parser)

    test_parser = subparsers.add_parser(
        "test",
        help="Run declarative tests.yaml manifests for managed config layers",
    )
    _add_common_args(test_parser)
    test_parser.add_argument(
        "test_path",
        nargs="*",
        type=_path_type,
        help=(
            "Optional tests.yaml paths or directories. Defaults to tests.yaml "
            "beside each selected config layer."
        ),
    )
    test_parser.add_argument(
        "--dump-tests",
        type=_path_type,
        default=None,
        help="Write generated pytest code to this path before execution.",
    )

    run_all_parser = subparsers.add_parser(
        "run-all",
        help="Discover all stacks and run terragrunt run-all",
    )
    run_all_parser.add_argument(
        "action",
        choices=[action.value for action in TerragruntAction],
        help="Terragrunt action to run across all stacks",
    )
    run_all_parser.add_argument(
        "--root",
        type=_path_type,
        default=Path(stacksmith_env("ROOT", str(Path.cwd()))),
        required=False,
        help="Root directory to discover stacks in (default: current working directory)",
    )
    _add_stack_arg(run_all_parser, include_positional=False)
    _add_common_args(run_all_parser)
    _add_validation_report_format_arg(run_all_parser)
    _add_plan_output_args(run_all_parser)
    _add_apply_args(run_all_parser)
    _add_target_selection_args(
        run_all_parser,
        tag_help=(
            "Select components by tag. Repeat to require multiple tags. "
            "Supported for run-all plan/apply/destroy."
        ),
        tag_expr_help=(
            "JMESPath expression used to select resource targets. "
            "Supported for run-all plan/apply/destroy."
        ),
    )
    run_all_parser.add_argument(
        "--include-tag",
        action="append",
        help="Include stacks that have this tag. Repeatable.",
    )
    run_all_parser.add_argument(
        "--exclude-tag",
        action="append",
        help="Exclude stacks that have this tag. Repeatable.",
    )
    run_all_parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing build output directory before generation",
    )
    run_all_parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip interactive approval for apply/destroy",
    )

    for action in TerragruntAction:
        action_parser = subparsers.add_parser(
            action.value,
            help=f"Generate + terragrunt {action.value}",
        )
        _add_stack_arg(action_parser)
        _add_common_args(action_parser)
        action_parser.set_defaults(
            auto_approve=False,
            destroy=False,
            tag=None,
            tag_expr=None,
        )
        match action:
            case TerragruntAction.PLAN:
                _add_plan_output_args(action_parser)
                _add_target_selection_args(action_parser)
                _add_validation_report_format_arg(action_parser)
            case TerragruntAction.APPLY:
                _add_apply_args(action_parser)
                _add_target_selection_args(
                    action_parser,
                    include_auto_approve=True,
                )
            case TerragruntAction.DESTROY:
                _add_target_selection_args(
                    action_parser,
                    include_auto_approve=True,
                )

    operation_parser = subparsers.add_parser(
        "operation",
        help="Run native operations approved by managed configuration",
    )
    operation_run_parser = operation_parser.add_subparsers(
        dest="operation_command",
        required=True,
    ).add_parser(
        "run",
        help="Run one approved operation declared by a stack",
    )
    operation_run_parser.add_argument(
        "operation_name",
        help="Stack-local operation name",
    )
    operation_run_parser.add_argument(
        "--force-rerun",
        action="store_true",
        default=env_truthy("FORCE_RERUN", prefix="STACKSMITH_"),
        help=(
            "Force the operation runner resource to be replaced even when its "
            "execution identity has not changed. Can also be enabled with "
            "STACKSMITH_FORCE_RERUN=1."
        ),
    )
    _add_stack_arg(operation_run_parser)
    _add_common_args(operation_run_parser)

    info_subparsers = subparsers.add_parser(
        "info",
        help="Show stacksmith inspection and diagnostics commands",
    ).add_subparsers(dest="info_command", required=True)
    _configure_inspect_parser(
        info_subparsers.add_parser(
            "inspect",
            help="Inspect configured modules: variables, mappings, and metadata",
        )
    )
    _configure_diagnose_parser(
        info_subparsers.add_parser(
            "diagnose",
            help="Show cache and module diagnostics",
        )
    )
    _configure_info_environments_parser(
        info_subparsers.add_parser(
            "environments",
            help="Preview GitOps environment discovery and selection",
        )
    )

    ci_subparsers = subparsers.add_parser(
        "ci",
        help="CI-focused validation and diagnostics commands",
    ).add_subparsers(dest="ci_command", required=True)
    _configure_ci_validate_parser(
        ci_subparsers.add_parser(
            "validate",
            help="Validate CI workflow inputs using Stacksmith semantics",
        )
    )
    _configure_ci_prepare_parser(
        ci_subparsers.add_parser(
            "prepare",
            help="Validate GitOps policy and emit a provider-neutral execution manifest",
        )
    )
    _configure_ci_execute_parser(
        ci_subparsers.add_parser(
            "execute",
            help="Execute one environment from a manifest emitted by ci prepare",
        )
    )
    _configure_ci_prepare_from_env_parser(
        ci_subparsers.add_parser(
            "prepare-from-env",
            help="Build a CI manifest from adapter environment variables",
        )
    )
    _configure_ci_execute_from_env_parser(
        ci_subparsers.add_parser(
            "execute-from-env",
            help="Execute CI manifest adapter inputs from environment variables",
        )
    )
    return parser
