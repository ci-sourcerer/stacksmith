"""GitOps environment selection helpers shared by CLI and CI workflow code.

This module intentionally uses only Python standard library imports so it can be
loaded directly from source in GitHub Actions without importing the full
`stacksmith` package.
"""

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
SUPPORTED_DISCOVERY_MODES = {
    "folders",
    "flat-files",
    "env-files",
}
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
        return [
            {
                "environment": environment,
                "runfile": self.common_runfile,
            }
            for environment in self.selected_environments
        ]


def normalize_gitops_root(value: str) -> str:
    """Normalize a GitOps root input into a relative prefix.

    Args:
        value: Raw input value.

    Returns:
        Empty string for current directory roots or a normalized relative prefix.
    """
    value = value.strip() or "."
    if value.startswith("./"):
        value = value[2:]
    if value in {"", "."}:
        return ""
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return value.strip("/")


def normalize_discovery_mode(value: str) -> str:
    """Normalize discovery mode aliases used by GitOps workflows.

    Args:
        value: Raw mode input.

    Returns:
        Canonical discovery mode.
    """
    mode = value.strip().lower() or "folders"
    return "env-files" if mode in {"env", "env-files"} else mode


def validate_discovery_mode(value: str) -> str:
    """Return a validated canonical discovery mode.

    Args:
        value: Raw mode input.

    Returns:
        Canonical discovery mode.

    Raises:
        ValueError: If the mode is unsupported.
    """
    mode = normalize_discovery_mode(value)
    if mode not in SUPPORTED_DISCOVERY_MODES:
        supported = ", ".join(sorted(SUPPORTED_DISCOVERY_MODES))
        raise ValueError(
            f"Unsupported discovery mode '{value}'. Supported values: {supported}."
        )
    return mode


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
    return next(
        (
            str((root / candidate).as_posix())
            for candidate in COMMON_CANDIDATES
            if (root / candidate).exists()
        ),
        str((root / "common/stacksmith.yaml").as_posix()),
    )


def discover_environments(mode: str, root: Path) -> tuple[list[str], str]:
    """Discover environments and common runfile for a mode/root pair.

    Args:
        mode: Canonical discovery mode.
        root: Filesystem root for discovery.

    Returns:
        Tuple of discovered environment names and common runfile path.
    """
    all_envs = []
    env_root = root / "environments"

    if mode == "flat-files":
        common_file = common_run_file_for(root)
        for path in (
            sorted(root.glob("stacksmith.*.yaml"))
            + sorted(root.glob("stacksmith.*.yml"))
            + sorted(root.glob("stacksmith.*.json"))
        ):
            name = path.stem.replace("stacksmith.", "", 1)
            if name in {"yaml", "yml", "json", "common", "shared", "base", "defaults"}:
                continue
            all_envs.append(name)
        return all_envs, common_file

    if mode == "env-files":
        common_file = common_run_file_for(root)
        if env_root.exists():
            for path in (
                sorted(env_root.glob("*.yaml"))
                + sorted(env_root.glob("*.yml"))
                + sorted(env_root.glob("*.json"))
            ):
                if path.is_file() and path.stem not in {
                    "stacksmith",
                    "common",
                    "shared",
                    "base",
                    "defaults",
                }:
                    all_envs.append(path.stem)
        return all_envs, common_file

    if env_root.exists():
        for env_dir in sorted(env_root.iterdir()):
            if env_dir.is_dir():
                all_envs.append(env_dir.name)

    return all_envs, "common/stacksmith.yaml"


def parse_manual_environments(value: str) -> set[str]:
    """Parse comma-separated environment names.

    Args:
        value: Raw comma-separated list.

    Returns:
        Parsed set of environment names.
    """
    return {item.strip() for item in value.split(",") if item.strip()}


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
        return (
            explicit_event_name,
            (os.getenv("CALLER_BASE_REF") or "").strip(),
            (os.getenv("CALLER_EVENT_BEFORE") or "").strip(),
            (os.getenv("CALLER_SHA") or "").strip(),
        )

    if os.getenv("JENKINS_URL"):
        if os.getenv("CHANGE_ID"):
            return (
                "pull_request",
                (os.getenv("CHANGE_TARGET") or "").strip(),
                "",
                (os.getenv("GIT_COMMIT") or "").strip(),
            )
        return (
            "push",
            "",
            "",
            (os.getenv("GIT_COMMIT") or "").strip(),
        )

    return (
        (os.getenv("GITHUB_EVENT_NAME") or "").strip(),
        (os.getenv("GITHUB_BASE_REF") or "").strip(),
        (os.getenv("GITHUB_EVENT_BEFORE") or "").strip(),
        (os.getenv("GITHUB_SHA") or "").strip(),
    )


def _validated_manual_environments(
    manual_targets: set[str], all_envs: list[str]
) -> set[str]:
    discovered = set(all_envs)
    unknown = sorted(env for env in manual_targets if env not in discovered)
    if not unknown:
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
    mode: str,
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
    run_all = any(path.startswith(common_prefixes) for path in changed_paths) or any(
        path.startswith(".github/workflows/") for path in changed_paths
    )
    if run_all:
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

        if mode == "flat-files":
            if not candidate.startswith("stacksmith.") or not candidate.endswith(
                (".yaml", ".yml", ".json")
            ):
                continue
            env_name = candidate.removeprefix("stacksmith.").rsplit(".", 1)[0]
            if env_name not in _NON_ENVIRONMENT_NAMES:
                selected.add(env_name)
            continue

        if mode == "env-files":
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

    return selected


def select_target_environments(
    *,
    all_envs: list[str],
    mode: str,
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
    normalized_root = normalize_gitops_root(gitops_root)
    mode = validate_discovery_mode(discovery_mode)
    root = project_root(normalized_root)
    all_envs, common_runfile = discover_environments(mode, root)

    manual = manual_environments.strip()
    if not manual and not all_envs:
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
    selected, used_changed_paths = select_target_environments(
        all_envs=all_envs,
        mode=mode,
        gitops_root=normalized_root,
        manual_environments=manual,
        event_name=resolved_event_name,
        changed_paths=resolved_changed_paths,
    )
    return GitOpsSelectionResult(
        gitops_root=normalized_root,
        discovery_mode=mode,
        all_environments=all_envs,
        selected_environments=selected,
        common_runfile=common_runfile,
        changed_paths=used_changed_paths,
    )


def resolve_ci_context() -> tuple[str, str, str, str]:
    explicit_event_name = (os.getenv("CALLER_EVENT_NAME") or "").strip()
    if explicit_event_name:
        return (
            explicit_event_name,
            (os.getenv("CALLER_BASE_REF") or "").strip(),
            (os.getenv("CALLER_EVENT_BEFORE") or "").strip(),
            (os.getenv("CALLER_SHA") or "").strip(),
        )

    if os.getenv("JENKINS_URL"):
        if os.getenv("CHANGE_ID"):
            return (
                "pull_request",
                (os.getenv("CHANGE_TARGET") or "").strip(),
                "",
                (os.getenv("GIT_COMMIT") or "").strip(),
            )
        return (
            "push",
            "",
            "",
            (os.getenv("GIT_COMMIT") or "").strip(),
        )

    return (
        (os.getenv("GITHUB_EVENT_NAME") or "").strip(),
        (os.getenv("GITHUB_BASE_REF") or "").strip(),
        (os.getenv("GITHUB_EVENT_BEFORE") or "").strip(),
        (os.getenv("GITHUB_SHA") or "").strip(),
    )
