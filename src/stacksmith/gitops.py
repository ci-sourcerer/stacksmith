import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .enums import DiscoveryMode

COMMON_CANDIDATES = (
    "common/stacksmith.yaml",
    "common/stacksmith.common.yaml",
    "common/stacksmith.shared.yaml",
    "common/stacksmith.base.yaml",
    "common/stacksmith.defaults.yaml",
    "stacksmith.yaml",
    "stacksmith.common.yaml",
    "stacksmith.shared.yaml",
    "stacksmith.base.yaml",
    "stacksmith.defaults.yaml",
)
DISCOVERY_FILE_EXTENSIONS = ("yaml", "yml", "json")
logger = logging.getLogger(__name__)


_NON_ENVIRONMENT_NAMES = {
    "stacksmith",
    "yaml",
    "yml",
    "json",
    "common",
    "shared",
    "base",
    "defaults",
}


@dataclass(frozen=True)
class GitOpsSelectionResult:
    """Resolved environment-selection details for a GitOps invocation."""

    gitops_root: str
    discovery_mode: str
    all_environments: list[str]
    selected_environments: list[str]
    common_runfile: str
    changed_paths: list[str]

    @property
    def matrix(self) -> list[dict[str, str]]:
        """Return matrix rows compatible with GitHub Actions strategy.include."""
        rows = []
        for environment in self.selected_environments:
            runfile = self.common_runfile
            environment_runfile = ""
            if self.discovery_mode == "env-files":
                root = Path(self.gitops_root or ".")
                for ext in DISCOVERY_FILE_EXTENSIONS:
                    env_file = root / "environments" / f"{environment}.{ext}"
                    if env_file.exists():
                        environment_runfile = env_file.as_posix()
                        break
            rows.append(
                {
                    "environment": environment,
                    "runfile": runfile,
                    "environment_runfile": environment_runfile,
                }
            )
        return rows


def normalize_gitops_root(value: str) -> str:
    """Normalize a GitOps root input into a relative prefix.

    Args:
        value: Raw input value.

    Returns:
        Empty string for current directory roots or a normalized relative prefix.
    """
    logger.debug("Normalizing GitOps root input: %r", value)
    value = value.strip() or "."
    if value.startswith("./"):
        value = value[2:]
    if value in {"", "."}:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return value.strip("/")


def normalize_discovery_mode(value: str) -> DiscoveryMode:
    """Normalize discovery mode aliases used by GitOps workflows.

    Args:
        value: Raw mode input.

    Returns:
        Canonical discovery mode.
    """
    mode = value.strip().lower() or DiscoveryMode.FOLDERS.value
    if mode in {DiscoveryMode.AUTO.value, ""}:
        normalized = DiscoveryMode.AUTO
    else:
        normalized = (
            DiscoveryMode.ENV_FILES
            if mode in {"env", DiscoveryMode.ENV_FILES.value}
            else DiscoveryMode(mode)
        )
    logger.debug("Normalized discovery mode %r -> %r", value, normalized)
    return normalized


def validate_discovery_mode(value: str) -> DiscoveryMode:
    """Return a validated canonical discovery mode.

    Args:
        value: Raw mode input.

    Returns:
        Canonical discovery mode.

    Raises:
        ValueError: If the mode is unsupported.
    """
    try:
        return normalize_discovery_mode(value)
    except ValueError as exc:
        supported = ", ".join(mode.value for mode in DiscoveryMode)
        raise ValueError(
            f"Unsupported discovery mode '{value}'. Supported values: {supported}."
        ) from exc


def resolve_discovery_mode(mode: DiscoveryMode, root: Path) -> DiscoveryMode:
    """Resolve discovery mode, defaulting to env-files for hybrid GitOps layouts."""
    if mode != DiscoveryMode.AUTO:
        return mode

    env_root = root / "environments"
    if env_root.exists() and any(env_root.iterdir()):
        for path in env_root.iterdir():
            if path.is_file() and path.suffix in {
                f".{ext}" for ext in DISCOVERY_FILE_EXTENSIONS
            }:
                return DiscoveryMode.ENV_FILES
        return DiscoveryMode.FOLDERS
    return DiscoveryMode.FOLDERS


def project_root(gitops_root: str) -> Path:
    """Return filesystem path for a normalized GitOps root value.

    Args:
        gitops_root: Normalized GitOps root.

    Returns:
        A path for discovery operations.
    """
    return Path(gitops_root or ".")


def common_run_file_for(root: Path) -> str:
    """Return the common runfile path for a GitOps root.

    Args:
        root: Filesystem root for GitOps discovery.

    Returns:
        A relative POSIX-style runfile path.
    """
    candidate = next(
        (candidate for candidate in COMMON_CANDIDATES if (root / candidate).exists()),
        "common/stacksmith.yaml",
    )
    resolved = str((root / candidate).as_posix())
    logger.debug("Resolved common runfile for %s -> %s", root, resolved)
    return resolved


def discover_environments(mode: DiscoveryMode, root: Path) -> tuple[list[str], str]:
    """Discover environments and common runfile for a mode/root pair.

    Args:
        mode: Canonical discovery mode.
        root: Filesystem root for discovery.

    Returns:
        Tuple of discovered environment names and common runfile path.
    """
    logger.info("Discovering environments with mode=%s root=%s", mode, root)
    all_envs = []
    env_root = root / "environments"

    if mode == DiscoveryMode.FLAT_FILES:
        common_file = common_run_file_for(root)
        logger.debug("Scanning flat-files under %s", root)
        for path in (
            sorted(root.glob("stacksmith.*.yaml"))
            + sorted(root.glob("stacksmith.*.yml"))
            + sorted(root.glob("stacksmith.*.json"))
        ):
            name = path.stem.replace("stacksmith.", "", 1)
            if name in _NON_ENVIRONMENT_NAMES:
                continue
            all_envs.append(name)
        logger.debug("Discovered flat-file environments: %s", all_envs)
        return all_envs, common_file

    if mode == DiscoveryMode.ENV_FILES:
        common_file = common_run_file_for(root)
        logger.debug("Scanning env-files under %s", env_root)
        if env_root.exists():
            for path in (
                sorted(env_root.glob("*.yaml"))
                + sorted(env_root.glob("*.yml"))
                + sorted(env_root.glob("*.json"))
            ):
                if path.is_file() and path.stem not in _NON_ENVIRONMENT_NAMES:
                    all_envs.append(path.stem)
        logger.debug("Discovered env-file environments: %s", all_envs)
        return all_envs, common_file

    if env_root.exists():
        logger.debug("Scanning folder-based environments under %s", env_root)
        for env_dir in sorted(env_root.iterdir()):
            if env_dir.is_dir():
                all_envs.append(env_dir.name)

    logger.debug("Discovered folder environments: %s", all_envs)
    return all_envs, "common/stacksmith.yaml"


def parse_manual_environments(value: str) -> set[str]:
    """Parse comma-separated environment names.

    Args:
        value: Raw comma-separated list.

    Returns:
        Parsed set of environment names.
    """
    parsed = {item.strip() for item in value.split(",") if item.strip()}
    logger.debug("Parsed manual environments from %r -> %s", value, sorted(parsed))
    return parsed


def resolve_ci_context() -> tuple[str, str, str, str]:
    """Resolve a normalized CI context for GitOps selection logic.

    GitHub Actions and Jenkins expose overlapping but different environment
    variables. This helper normalizes them to the selection interface used
    elsewhere in the module: event_name, base_ref, before, and after.

    Returns:
        Tuple of normalized event name, base ref, before SHA, and after SHA.
    """
    explicit_event_name = (os.getenv("CALLER_EVENT_NAME") or "").strip()
    if explicit_event_name:
        logger.info("Using explicit CI context overrides for GitOps selection")
        return (
            explicit_event_name,
            (os.getenv("CALLER_BASE_REF") or "").strip(),
            (os.getenv("CALLER_EVENT_BEFORE") or "").strip(),
            (os.getenv("CALLER_SHA") or "").strip(),
        )

    if os.getenv("JENKINS_URL"):
        logger.debug("Detected Jenkins CI environment")
        if os.getenv("CHANGE_ID"):
            resolved = (
                "pull_request",
                (os.getenv("CHANGE_TARGET") or "").strip(),
                "",
                (os.getenv("GIT_COMMIT") or "").strip(),
            )
            logger.debug("Resolved Jenkins PR context: %s", resolved)
            return resolved
        resolved = (
            "push",
            "",
            "",
            (os.getenv("GIT_COMMIT") or "").strip(),
        )
        logger.debug("Resolved Jenkins push context: %s", resolved)
        return resolved

    resolved = (
        (os.getenv("GITHUB_EVENT_NAME") or "").strip(),
        (os.getenv("GITHUB_BASE_REF") or "").strip(),
        (os.getenv("GITHUB_EVENT_BEFORE") or "").strip(),
        (os.getenv("GITHUB_SHA") or "").strip(),
    )
    logger.debug("Resolved GitHub CI context: %s", resolved)
    return resolved


def _validated_manual_environments(
    manual_targets: set[str], all_envs: list[str]
) -> set[str]:
    discovered = set(all_envs)
    unknown = sorted(env for env in manual_targets if env not in discovered)
    if not unknown:
        logger.info("Manual environments validated: %s", sorted(manual_targets))
        return manual_targets

    discovered_envs = ", ".join(sorted(all_envs)) if all_envs else "<none>"
    raise ValueError(
        "Unknown manual environment(s): "
        f"{', '.join(unknown)}. Discovered environments: {discovered_envs}."
    )


def changed_paths_for_event(
    event_name: str,
    *,
    base_ref: str = "",
    before: str = "",
    after: str = "",
) -> list[str]:
    """Resolve changed file paths from git for pull request and push events.

    Args:
        event_name: GitHub event name.
        base_ref: Base branch name for pull requests.
        before: Previous commit SHA for push events.
        after: Current commit SHA for push events.

    Returns:
        Changed repository paths.
    """
    logger.debug(
        "Resolving changed paths for event=%s base_ref=%r before=%r after=%r",
        event_name,
        base_ref,
        before,
        after,
    )
    if event_name == "pull_request" and base_ref:
        try:
            subprocess.run(
                ["git", "fetch", "origin", base_ref, "--depth", "1"],
                check=True,
                text=True,
                capture_output=True,
            )
            return _extract_non_empty_lines(
                subprocess.check_output(
                    ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
                    text=True,
                )
            )
        except subprocess.CalledProcessError as exc:
            import sys

            print(f"WARNING: git diff failed for pull_request: {exc}", file=sys.stderr)
            return []

    if (
        event_name == "push"
        and before
        and before != "0000000000000000000000000000000000000000"
        and after
    ):
        try:
            return _extract_non_empty_lines(
                subprocess.check_output(
                    ["git", "diff", "--name-only", before, after],
                    text=True,
                )
            )
        except subprocess.CalledProcessError as exc:
            import sys

            print(f"WARNING: git diff failed for push event: {exc}", file=sys.stderr)
            return []

    return []


def _extract_non_empty_lines(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def select_changed_environments(
    changed_paths: list[str],
    mode: DiscoveryMode,
    prefix: str,
    common_prefixes: tuple[str, ...],
    all_envs: list[str],
) -> set[str]:
    """Map changed paths to affected environments.

    Args:
        changed_paths: Changed repository paths.
        mode: Canonical discovery mode.
        prefix: GitOps root prefix with trailing slash, when set.
        common_prefixes: Prefixes that imply all environments are affected.
        all_envs: Known environments.

    Returns:
        Selected environment names.
    """
    logger.debug("Selecting environments from changed paths: %s", changed_paths)
    run_all = any(path.startswith(common_prefixes) for path in changed_paths) or any(
        path.startswith(".github/workflows/") for path in changed_paths
    )
    if run_all:
        logger.info("Common change detected; selecting all environments")
        return set(all_envs)

    selected: set[str] = set()
    for path in changed_paths:
        candidate = path[len(prefix) :] if path.startswith(prefix) else path

        if candidate.startswith("manifests/environments/"):
            relative_path = Path(candidate[len("manifests/environments/") :])
            if (
                relative_path.parts
                and relative_path.parts[0] not in _NON_ENVIRONMENT_NAMES
            ):
                selected.add(relative_path.parts[0])
            continue

        if mode == DiscoveryMode.FLAT_FILES:
            if not candidate.startswith("stacksmith.") or not candidate.endswith(
                (".yaml", ".yml", ".json")
            ):
                continue
            env_name = candidate.removeprefix("stacksmith.").rsplit(".", 1)[0]
            if env_name not in _NON_ENVIRONMENT_NAMES:
                selected.add(env_name)
            continue

        if mode == DiscoveryMode.ENV_FILES:
            if candidate.startswith("environments/"):
                relative_path = Path(candidate[len("environments/") :])
                env_name = (
                    relative_path.stem
                    if len(relative_path.parts) == 1
                    else relative_path.parts[0]
                )
                if env_name not in _NON_ENVIRONMENT_NAMES:
                    selected.add(env_name)
            continue

        if not candidate.startswith("environments/"):
            continue
        relative_path = Path(candidate[len("environments/") :])
        if relative_path.parts:
            selected.add(relative_path.parts[0])

    logger.debug("Selected environments from changed paths: %s", sorted(selected))
    return selected


def select_target_environments(
    *,
    all_envs: list[str],
    mode: DiscoveryMode,
    gitops_root: str,
    manual_environments: str = "",
    event_name: str = "",
    changed_paths: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve selected target environments for an invocation.

    Args:
        all_envs: Discovered environments.
        mode: Canonical discovery mode.
        gitops_root: Normalized GitOps root.
        manual_environments: Optional comma-separated manual overrides.
        event_name: GitHub event name.
        changed_paths: Optional changed paths to use for selection logic.

    Returns:
        Tuple of selected environments and changed paths used by selection.
    """
    manual = manual_environments.strip()
    selected = (
        _validated_manual_environments(parse_manual_environments(manual), all_envs)
        if manual
        else set()
    )
    resolved_changed_paths = list(changed_paths or [])

    if not manual:
        if event_name == "workflow_dispatch":
            selected = set(all_envs)
        elif event_name in {"pull_request", "push"}:
            selected = (
                set(all_envs)
                if not resolved_changed_paths
                else select_changed_environments(
                    resolved_changed_paths,
                    mode,
                    f"{gitops_root}/" if gitops_root else "",
                    (
                        f"{gitops_root}/common/" if gitops_root else "common/",
                        (
                            f"{gitops_root}/manifests/common/"
                            if gitops_root
                            else "manifests/common/"
                        ),
                    ),
                    all_envs,
                )
            )
        else:
            selected = set(all_envs)

    if manual:
        return sorted(selected), resolved_changed_paths
    logger.info("Resolved selected environments: %s", sorted(selected))
    return sorted(env for env in selected if env in all_envs), resolved_changed_paths


def evaluate_environment_selection(
    *,
    gitops_root: str,
    discovery_mode: str,
    manual_environments: str = "",
    event_name: str = "",
    changed_paths: list[str] | None = None,
    base_ref: str = "",
    before: str = "",
    after: str = "",
) -> GitOpsSelectionResult:
    """Evaluate GitOps environment selection using workflow-compatible logic.

    Args:
        gitops_root: Raw GitOps root input.
        discovery_mode: Raw discovery mode input.
        manual_environments: Optional comma-separated manual target environments.
        event_name: GitHub event name.
        changed_paths: Optional changed paths to bypass git diff collection.
        base_ref: Base branch for pull request diffs.
        before: Previous SHA for push diffs.
        after: Current SHA for push diffs.

    Returns:
        Selection result containing discovered/selected environments and matrix data.

    Raises:
        ValueError: If discovery mode is invalid or no environments are discoverable
            without a manual override, or if manual environment targets include unknown
            names.
    """
    logger.info("Evaluating GitOps environment selection")
    normalized_root = normalize_gitops_root(gitops_root)
    mode = validate_discovery_mode(discovery_mode)
    root = project_root(normalized_root)
    resolved_mode = resolve_discovery_mode(mode, root)
    logger.debug("Validated discovery mode=%s root=%s", resolved_mode, root)
    all_envs, common_runfile = discover_environments(resolved_mode, root)

    manual = manual_environments.strip()
    if not manual and not all_envs:
        logger.error("No environments discovered for %s", root)
        raise ValueError(
            "No environments were discovered under 'environments/'. "
            "Also, no environments were manually specified via the "
            "'environments' input or INPUT_ENVIRONMENTS environment variable."
        )

    resolved_event_name, resolved_base_ref, resolved_before, resolved_after = (
        resolve_ci_context()
    )
    resolved_event_name = event_name or resolved_event_name
    resolved_base_ref = base_ref or resolved_base_ref
    resolved_before = before or resolved_before
    resolved_after = after or resolved_after

    resolved_changed_paths = (
        list(changed_paths)
        if changed_paths is not None
        else changed_paths_for_event(
            resolved_event_name,
            base_ref=resolved_base_ref,
            before=resolved_before,
            after=resolved_after,
        )
    )
    logger.debug(
        "Resolved event context: %s %s %s %s",
        resolved_event_name,
        resolved_base_ref,
        resolved_before,
        resolved_after,
    )
    selected, used_changed_paths = select_target_environments(
        all_envs=all_envs,
        mode=resolved_mode,
        gitops_root=normalized_root,
        manual_environments=manual,
        event_name=resolved_event_name,
        changed_paths=resolved_changed_paths,
    )
    return GitOpsSelectionResult(
        gitops_root=normalized_root,
        discovery_mode=mode.value,
        all_environments=all_envs,
        selected_environments=selected,
        common_runfile=common_runfile,
        changed_paths=used_changed_paths,
    )
