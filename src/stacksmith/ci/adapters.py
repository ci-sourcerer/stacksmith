import json
import os
import tempfile
from pathlib import Path

from ..exceptions import StacksmithError
from ..utils import parse_bool
from .contracts import CiExecutionManifest
from .service import prepare_ci_execution


def optional_env_bool(name: str) -> bool | None:
    """Read an optional boolean environment variable.

    Args:
        name: Environment variable name.

    Returns:
        Parsed boolean, or `None` when the variable is unset or empty.
    """
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    return parse_bool(raw_value)


def prepare_ci_manifest_from_env() -> CiExecutionManifest:
    """Prepare a CI execution manifest from workflow environment variables.

    Returns:
        Validated provider-neutral CI execution manifest.
    """
    return prepare_ci_execution(
        command=os.getenv("INPUT_COMMAND", ""),
        operation_name=os.getenv("INPUT_OPERATION_NAME", ""),
        config_ref=os.getenv("INPUT_CONFIG_REF", ""),
        workdir=os.getenv("INPUT_WORKDIR", "."),
        env_file=os.getenv("INPUT_ENV_FILE", "/dev/null"),
        stacksmith_args_json=os.getenv("INPUT_STACKSMITH_ARGS_JSON", "[]"),
        no_cas=parse_bool(os.getenv("INPUT_NO_CAS")),
        force_rerun=parse_bool(os.getenv("INPUT_FORCE_RERUN")),
        validation_report_format=os.getenv("INPUT_VALIDATION_REPORT_FORMAT", "json"),
        fail_on_changes=parse_bool(os.getenv("INPUT_FAIL_ON_CHANGES")),
        strict_validation_warnings=parse_bool(
            os.getenv("INPUT_STRICT_VALIDATION_WARNINGS")
        ),
        gitops_root=os.getenv("INPUT_GITOPS_ROOT", "."),
        discovery_mode=os.getenv("INPUT_DISCOVERY_MODE", "auto"),
        environments=os.getenv("INPUT_ENVIRONMENTS", ""),
        event_name=os.getenv("CALLER_EVENT_NAME", ""),
        base_ref=os.getenv("CALLER_BASE_REF", ""),
        before=os.getenv("CALLER_EVENT_BEFORE", ""),
        after=os.getenv("CALLER_SHA", ""),
        ref_name=os.getenv("CALLER_REF_NAME", ""),
        default_branch=os.getenv("CALLER_DEFAULT_BRANCH", ""),
        is_primary_branch=optional_env_bool("CALLER_IS_PRIMARY_BRANCH"),
        skip_branch_validation=parse_bool(os.getenv("SKIP_BRANCH_VALIDATION")),
    )


def manifest_output_json(manifest: CiExecutionManifest, compact: bool = False) -> str:
    """Serialize a CI execution manifest.

    Args:
        manifest: Manifest to serialize.
        compact: Whether to omit insignificant whitespace.

    Returns:
        Manifest JSON text.
    """
    if compact:
        return json.dumps(manifest.model_dump(mode="json"), separators=(",", ":"))
    return manifest.model_dump_json(indent=2)


def write_github_output_manifest(
    manifest: CiExecutionManifest, github_output_path: Path
) -> None:
    """Append manifest outputs for a GitHub Actions workflow.

    Args:
        manifest: Manifest to emit.
        github_output_path: GitHub Actions output file.
    """
    matrix = [row.model_dump(mode="json") for row in manifest.matrix]
    with github_output_path.open("a", encoding="utf-8") as output_stream:
        output_stream.write(
            f"manifest={manifest_output_json(manifest, compact=True)}\n"
        )
        output_stream.write(f"matrix={json.dumps(matrix, separators=(',', ':'))}\n")
        output_stream.write(f"count={len(matrix)}\n")


def load_ci_execution_manifest(path: Path) -> CiExecutionManifest:
    """Load and validate a CI execution manifest.

    Args:
        path: Manifest JSON path.

    Returns:
        Validated CI execution manifest.

    Raises:
        StacksmithError: If the manifest cannot be read or validated.
    """
    try:
        return CiExecutionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StacksmithError(f"Invalid CI execution manifest '{path}': {exc}") from exc


def write_ssh_key_material(environment: str) -> Path | None:
    """Write workflow-provided SSH key material to a restricted temporary file.

    Args:
        environment: Environment name used in the temporary filename.

    Returns:
        Temporary key path, or `None` when no key material is configured.
    """
    key_material = os.getenv("STACKSMITH_GIT_SSH_KEY_MATERIAL", "")
    if not key_material.strip():
        return None

    file_descriptor, key_path = tempfile.mkstemp(
        prefix=f"stacksmith_git_ssh_key_{environment}_"
    )
    os.close(file_descriptor)
    path = Path(key_path)
    path.chmod(0o600)
    path.write_text(f"{key_material.rstrip()}\n", encoding="utf-8")
    os.environ["STACKSMITH_GIT_SSH_KEY"] = str(path)
    return path


def resolve_ci_execution_manifest_path(
    explicit_manifest_file: Path | None,
) -> tuple[Path, Path | None]:
    """Resolve a manifest path from CLI options or workflow environment values.

    Args:
        explicit_manifest_file: Optional explicit manifest path.

    Returns:
        Manifest path and an optional temporary path that the caller must remove.

    Raises:
        StacksmithError: If no manifest source is configured.
    """
    if explicit_manifest_file is not None:
        return explicit_manifest_file, None

    if env_manifest_file := os.getenv("CI_MANIFEST_FILE"):
        return Path(env_manifest_file), None

    manifest_json = os.getenv("STACKSMITH_CI_MANIFEST", "")
    if not manifest_json.strip():
        raise StacksmithError(
            "Provide --manifest-file, CI_MANIFEST_FILE, or STACKSMITH_CI_MANIFEST"
        )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".json",
    ) as temporary_manifest:
        temporary_manifest.write(manifest_json)

    return Path(temporary_manifest.name), Path(temporary_manifest.name)


def resolve_ci_environment(explicit_environment: str) -> str:
    """Resolve the environment selected for CI execution.

    Args:
        explicit_environment: Optional explicit environment name.

    Returns:
        Selected environment name.

    Raises:
        StacksmithError: If no environment is configured.
    """
    environment = (
        explicit_environment.strip()
        or os.getenv("STACKSMITH_ENVIRONMENT", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
    )
    if not environment:
        raise StacksmithError(
            "Provide --environment, STACKSMITH_ENVIRONMENT, or ENVIRONMENT"
        )
    return environment


def resolve_validation_report_output(
    explicit_output: Path | None, manifest: CiExecutionManifest, environment: str
) -> Path | None:
    """Resolve the validation report output path for a CI execution.

    Args:
        explicit_output: Optional explicit report path.
        manifest: CI execution manifest.
        environment: Selected environment name.

    Returns:
        Report output path, or `None` for non-plan executions without a path.
    """
    if explicit_output is not None:
        return explicit_output

    if env_output_path := (
        os.getenv("STACKSMITH_VALIDATION_REPORT_PATH", "").strip()
        or os.getenv("VALIDATION_REPORT_PATH", "").strip()
    ):
        return Path(env_output_path)

    if manifest.command != "plan":
        return None

    return (
        Path(manifest.workdir)
        / ".stacksmith-ci"
        / environment
        / f"validation-report.{manifest.validation_report_format}"
    )
