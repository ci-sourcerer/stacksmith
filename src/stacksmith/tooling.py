import hashlib
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger as LOGGER
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

from .exceptions import StacksmithError
from .models import RemoteAuthConfig, ToolBinaryConfig, ToolDownloadSpec, ToolsConfig
from .remote import resolve_remote
from .utils import stacksmith_env

ToolName = Literal["tofu", "terragrunt"]

_DEFAULT_DOWNLOAD_TEMPLATES: dict[ToolName, str] = {
    "tofu": "https://github.com/opentofu/opentofu/releases/download/v{version}/tofu_{version}_{os}_{arch}.zip",
    "terragrunt": "https://github.com/gruntwork-io/terragrunt/releases/download/v{version}/terragrunt_{os}_{arch}",
}

_VERSION_COMMANDS: dict[ToolName, tuple[str, ...]] = {
    "tofu": ("-version",),
    "terragrunt": ("--version",),
}

_ENV_OVERRIDES: dict[ToolName, str | None] = {
    "tofu": "TG_TF_PATH",
    "terragrunt": "STACKSMITH_TERRAGRUNT_PATH",
}

_DEFAULT_COMMANDS: dict[ToolName, str] = {
    "tofu": "tofu",
    "terragrunt": "terragrunt",
}


@dataclass(frozen=True)
class ResolvedToolchain:
    """Resolved executable paths for OpenTofu and Terragrunt."""

    tofu: str
    terragrunt: str


def resolve_toolchain(
    tools: ToolsConfig | None,
    cache_dir: Path | None,
    auth_config: RemoteAuthConfig | None,
    subprocess_module: object | None = None,
) -> ResolvedToolchain:
    """Resolve the toolchain executables for this process."""
    cache_root = _resolve_tool_cache_root(cache_dir)
    configured_tools = tools or ToolsConfig(
        tofu=ToolBinaryConfig(version=">=1.0.0,<2.0.0"),
        terragrunt=ToolBinaryConfig(version=">=1.0.0,<2.0.0"),
    )

    def _resolve(tool_name: ToolName, tool_cfg: ToolBinaryConfig) -> str:
        return _resolve_single_tool(
            tool_name,
            tool_cfg,
            cache_root,
            auth_config,
            subprocess_module=subprocess_module,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        tofu_future = executor.submit(_resolve, "tofu", configured_tools.tofu)
        terragrunt_future = executor.submit(
            _resolve, "terragrunt", configured_tools.terragrunt
        )
        return ResolvedToolchain(
            tofu=tofu_future.result(),
            terragrunt=terragrunt_future.result(),
        )


def _resolve_tool_cache_root(cache_dir: Path | None) -> Path:
    if configured := stacksmith_env("TOOL_CACHE_DIR"):
        return Path(configured).expanduser()
    if cache_dir is not None:
        return cache_dir / "tools"
    return Path.cwd() / ".stacksmith" / ".cache" / "tools"


def _resolve_single_tool(
    tool_name: ToolName,
    tool_cfg: ToolBinaryConfig,
    cache_root: Path,
    auth_config: RemoteAuthConfig | None,
    subprocess_module: object | None = None,
) -> str:
    if cached := _find_cached_tool_binary(tool_name, tool_cfg.version, cache_root):
        return str(cached)

    if local := _find_local_tool_binary(
        tool_name,
        tool_cfg.version,
        subprocess_module=subprocess_module,
    ):
        return local

    return str(
        _download_and_install_tool(
            tool_name,
            tool_cfg,
            cache_root,
            auth_config,
        )
    )


def _find_cached_tool_binary(
    tool_name: ToolName,
    constraint: str,
    cache_root: Path,
) -> Path | None:
    tool_root = cache_root / tool_name
    if not tool_root.is_dir():
        return None

    matches: list[tuple[Version, Path]] = []
    for candidate in tool_root.iterdir():
        if not candidate.is_dir():
            continue

        binary_path = candidate / "bin" / _binary_name(tool_name)
        if not binary_path.is_file():
            continue

        try:
            version = Version(candidate.name)
        except InvalidVersion:
            continue

        if _version_satisfies(version, constraint):
            matches.append((version, binary_path))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0], reverse=True)
    LOGGER.debug(
        "Using cached {tool_name} binary at {path}",
        tool_name=tool_name,
        path=matches[0][1],
    )
    return matches[0][1]


def _find_local_tool_binary(
    tool_name: ToolName,
    constraint: str,
    subprocess_module: object | None = None,
) -> str | None:
    for command in _candidate_commands(tool_name):
        if (version := _probe_version(command, tool_name, subprocess_module)) is None:
            continue
        if _version_satisfies(version, constraint):
            LOGGER.debug(
                "Using local {tool_name}={version} from {command}",
                tool_name=tool_name,
                version=version,
                command=command,
            )
            return _resolve_command_path(command)

    return None


def _candidate_commands(tool_name: ToolName) -> list[str]:
    commands: list[str] = []
    if (override_name := _ENV_OVERRIDES[tool_name]) and (
        override_cmd := os.environ.get(override_name)
    ):
        commands.append(override_cmd)
    commands.append(_DEFAULT_COMMANDS[tool_name])
    return commands


def _probe_version(
    command: str,
    tool_name: ToolName,
    subprocess_module: object | None = None,
) -> Version | None:
    runner_subprocess = subprocess_module or subprocess
    try:
        result = runner_subprocess.run(
            [command, *_VERSION_COMMANDS[tool_name]],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if not output:
        return None

    return _parse_version_from_output(tool_name, output)


def _parse_version_from_output(tool_name: ToolName, output: str) -> Version:
    match = re.search(r"\bv?(\d+\.\d+(?:\.\d+)?)\b", output)
    if not match:
        raise StacksmithError(
            f"Could not parse {tool_name} version from output: {output!r}"
        )
    normalized = match.group(1)
    if normalized.count(".") == 1:
        normalized = f"{normalized}.0"
    return Version(normalized)


def _normalize_constraint(constraint: str) -> SpecifierSet:
    normalized = constraint.strip()
    if not normalized:
        raise StacksmithError("Tool version constraint must be non-empty")

    if normalized.startswith("~>"):
        return SpecifierSet(_convert_pessimistic_constraint(normalized))

    if re.match(r"^v?\d+(?:\.\d+){0,2}$", normalized):
        version = normalized.lstrip("v")
        if version.count(".") == 1:
            version = f"{version}.0"
        return SpecifierSet(f"=={version}")

    if normalized.startswith("=") and not normalized.startswith("=="):
        normalized = f"={normalized}"

    return SpecifierSet(normalized)


def _convert_pessimistic_constraint(constraint: str) -> str:
    version_text = constraint[2:].strip().lstrip("v")
    parts = version_text.split(".")
    if len(parts) not in {1, 2, 3}:
        raise StacksmithError(
            f"Unsupported pessimistic version constraint: {constraint!r}"
        )

    numbers = [int(part) for part in parts]
    while len(numbers) < 3:
        numbers.append(0)

    lower_bound = f">={numbers[0]}.{numbers[1]}.{numbers[2]}"
    if len(parts) == 1:
        upper_bound = f"<{numbers[0] + 1}.0.0"
    elif len(parts) == 2:
        upper_bound = f"<{numbers[0] + 1}.0.0"
    else:
        upper_bound = f"<{numbers[0]}.{numbers[1] + 1}.0"

    return f"{lower_bound},{upper_bound}"


def _version_satisfies(version: Version, constraint: str) -> bool:
    return version in _normalize_constraint(constraint)


def _download_and_install_tool(
    tool_name: ToolName,
    tool_cfg: ToolBinaryConfig,
    cache_root: Path,
    auth_config: RemoteAuthConfig | None,
) -> Path:
    version = _exact_version_for_download(tool_cfg.version)
    install_path = cache_root / tool_name / version / "bin" / _binary_name(tool_name)
    if install_path.is_file():
        return install_path

    download_spec = tool_cfg.download or ToolDownloadSpec(
        url_template=_DEFAULT_DOWNLOAD_TEMPLATES[tool_name]
    )
    url = _render_download_url(download_spec.url_template, version, tool_name)

    LOGGER.debug("Downloading {tool_name} from {url}", tool_name=tool_name, url=url)
    artifact = resolve_remote(url, cache_root / "downloads", auth_config=auth_config)
    _verify_download_checksum(
        artifact, download_spec, version, tool_name, cache_root, auth_config
    )

    install_path.parent.mkdir(parents=True, exist_ok=True)
    _install_tool_binary(artifact, install_path, tool_name)
    LOGGER.debug(
        "Finished downloading {tool_name} to {path}",
        tool_name=tool_name,
        path=install_path,
    )
    return install_path


def _exact_version_for_download(constraint: str) -> str:
    normalized = constraint.strip().lstrip("v")
    if re.match(r"^\d+(?:\.\d+){0,2}$", normalized):
        return normalized
    if normalized.startswith("=="):
        return normalized[2:].strip().lstrip("v")
    if normalized.startswith("="):
        return normalized[1:].strip().lstrip("v")
    raise StacksmithError(
        "Automatic download requires an exact version pin like '1.12.1' or '==1.12.1'"
    )


def _render_download_url(url_template: str, version: str, tool_name: ToolName) -> str:
    os_name, arch = _platform_tokens()
    try:
        return url_template.format(
            version=version,
            os=os_name,
            arch=arch,
            tool=tool_name,
        )
    except KeyError as exc:
        raise StacksmithError(
            f"Unknown tool download URL placeholder in template: {exc}"
        ) from exc


def _platform_tokens() -> tuple[str, str]:
    raw_os = platform.system().lower()
    raw_arch = platform.machine().lower()

    os_name = {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "windows",
    }.get(raw_os)
    if os_name is None:
        raise StacksmithError(f"Unsupported OS for tool download: {raw_os}")

    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(raw_arch)
    if arch is None:
        raise StacksmithError(f"Unsupported architecture for tool download: {raw_arch}")

    return os_name, arch


def _verify_download_checksum(
    artifact: Path,
    download_spec: ToolDownloadSpec,
    version: str,
    tool_name: ToolName,
    cache_root: Path,
    auth_config: RemoteAuthConfig | None,
) -> None:
    expected_checksum = download_spec.sha256
    if expected_checksum is None and download_spec.sha256_url_template is not None:
        checksum_url = _render_download_url(
            download_spec.sha256_url_template,
            version,
            tool_name,
        )
        checksum_file = resolve_remote(
            checksum_url,
            cache_root / "downloads",
            auth_config=auth_config,
        )
        expected_checksum = _parse_checksum_text(
            checksum_file.read_text(encoding="utf-8")
        )

    if expected_checksum is None:
        return

    computed_checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if computed_checksum != expected_checksum.strip().lower():
        raise StacksmithError(
            f"Checksum mismatch for {artifact}: expected {expected_checksum}, got {computed_checksum}"
        )


def _parse_checksum_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise StacksmithError("Checksum file is empty")

    first_token = stripped.split()[0]
    if not re.fullmatch(r"[0-9a-fA-F]{64}", first_token):
        raise StacksmithError("Checksum file does not start with a SHA-256 hash")
    return first_token.lower()


def _install_tool_binary(
    artifact: Path, install_path: Path, tool_name: ToolName
) -> None:
    if zipfile.is_zipfile(artifact):
        _install_from_zip(artifact, install_path, tool_name)
        return

    if tarfile.is_tarfile(artifact):
        _install_from_tar(artifact, install_path, tool_name)
        return

    shutil.copy2(artifact, install_path)
    _make_executable(install_path)


def _install_from_zip(archive: Path, install_path: Path, tool_name: ToolName) -> None:
    with zipfile.ZipFile(archive) as zip_file:
        member_name = _matching_archive_member(zip_file.namelist(), tool_name)
        if member_name is None:
            raise StacksmithError(
                f"Could not find {tool_name} binary in zip archive: {archive}"
            )
        with zip_file.open(member_name) as source:
            install_path.write_bytes(source.read())
    _make_executable(install_path)


def _install_from_tar(archive: Path, install_path: Path, tool_name: ToolName) -> None:
    with tarfile.open(archive) as tar_file:
        members = [member for member in tar_file.getmembers() if member.isfile()]
        member = _matching_tar_member(members, tool_name)
        if member is None:
            raise StacksmithError(
                f"Could not find {tool_name} binary in tar archive: {archive}"
            )
        extracted = tar_file.extractfile(member)
        if extracted is None:
            raise StacksmithError(
                f"Could not extract {tool_name} binary from tar archive: {archive}"
            )
        install_path.write_bytes(extracted.read())
    _make_executable(install_path)


def _matching_archive_member(names: list[str], tool_name: ToolName) -> str | None:
    expected = _binary_name(tool_name)
    for name in names:
        if name.endswith(f"/{expected}") or name == expected:
            return name
    return None


def _matching_tar_member(
    members: list[tarfile.TarInfo],
    tool_name: ToolName,
) -> tarfile.TarInfo | None:
    expected = _binary_name(tool_name)
    for member in members:
        if member.name.endswith(f"/{expected}") or member.name == expected:
            return member
    return None


def _binary_name(tool_name: ToolName) -> str:
    if os.name == "nt":
        return f"{tool_name}.exe"
    return tool_name


def _make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _resolve_command_path(command: str) -> str:
    expanded = Path(command).expanduser()
    if expanded.exists():
        return str(expanded.resolve())

    resolved = shutil.which(command)
    if resolved is None:
        return command

    if Path(command).name == command:
        return command

    return resolved
