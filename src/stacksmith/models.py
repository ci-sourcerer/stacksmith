import re
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import MergeMode

_GIT_REPO_PREFIXES = ("https://", "http://", "ssh://", "git://", "git@")


class LocalReferenceData(BaseModel):
    """Local file reference payload."""

    path: str

    @model_validator(mode="after")
    def _validate_path(self) -> "LocalReferenceData":
        if not self.path.strip():
            raise ValueError("Local reference path must be a non-empty string")
        return self


class GitReferenceData(BaseModel):
    """Git file reference payload."""

    repo: str
    path: str
    ref: str | None = None

    @model_validator(mode="after")
    def _validate_git_fields(self) -> "GitReferenceData":
        if not self.repo.strip():
            raise ValueError("Git reference repo must be a non-empty string")
        if not self.repo.startswith(_GIT_REPO_PREFIXES):
            raise ValueError(
                "Git reference repo must start with one of https://, http://, ssh://, git://, or git@"
            )
        if not self.path.strip():
            raise ValueError("Git reference path must be a non-empty string")
        return self


class HttpReferenceData(BaseModel):
    """HTTP file reference payload."""

    url: str

    @model_validator(mode="after")
    def _validate_url(self) -> "HttpReferenceData":
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("HTTP reference url must start with http:// or https://")
        return self


class LocalReference(BaseModel):
    """Structured local file reference."""

    source: Literal["local"]
    data: LocalReferenceData


class GitReference(BaseModel):
    """Structured git file reference."""

    source: Literal["git"]
    data: GitReferenceData


class HttpReference(BaseModel):
    """Structured HTTP file reference."""

    source: Literal["http"]
    data: HttpReferenceData


FileReference: TypeAlias = Annotated[
    LocalReference | GitReference | HttpReference,
    Field(discriminator="source"),
]


def render_file_reference(reference: FileReference | str | Path) -> str:
    """Render a file reference to a path/URL string for resolution routines."""
    if isinstance(reference, Path):
        return str(reference)
    if isinstance(reference, str):
        return reference

    match reference:
        case LocalReference(data=data):
            return data.path
        case HttpReference(data=data):
            return data.url
        case GitReference(data=data):
            suffix = f"@{data.ref}" if data.ref else ""
            return f"git+{data.repo}//{data.path}{suffix}"

    raise ValueError(f"Unsupported file reference: {reference!r}")


def is_file_reference_remote(reference: FileReference | str | Path) -> bool:
    """Return True for git/http file references."""
    if isinstance(reference, (Path, str)):
        normalized = str(reference)
        return normalized.startswith(
            ("http://", "https://", "git+https://", "git+ssh://")
        )
    return reference.source in {"git", "http"}


class RegistrySourceData(BaseModel):
    """Registry-backed module/provider source payload."""

    address: str
    version: str

    @model_validator(mode="after")
    def _validate_registry_fields(self) -> "RegistrySourceData":
        if not self.address.strip():
            raise ValueError("Registry source address must be a non-empty string")
        if any(char.isspace() for char in self.address):
            raise ValueError("Registry source address must not contain whitespace")
        if "/" not in self.address:
            raise ValueError(
                "Registry source address must use '<namespace>/<name>' format"
            )
        if not self.version.strip():
            raise ValueError("Registry source version must be a non-empty string")
        return self


class ModuleGitSourceData(BaseModel):
    """Git-backed module source payload."""

    repo: str
    ref: str
    path: str | None = None

    @model_validator(mode="after")
    def _validate_module_git_fields(self) -> "ModuleGitSourceData":
        if not self.repo.strip():
            raise ValueError("Git module source repo must be a non-empty string")
        if not self.repo.startswith(_GIT_REPO_PREFIXES):
            raise ValueError(
                "Git module source repo must start with one of https://, http://, ssh://, git://, or git@"
            )
        if not self.ref.strip():
            raise ValueError("Git module source ref must be a non-empty string")
        if self.path is not None and not self.path.strip():
            raise ValueError("Git module source path must be non-empty when provided")
        return self


class RegistrySourceReference(BaseModel):
    """Structured registry source reference."""

    source: Literal["registry"]
    data: RegistrySourceData


class ModuleGitSourceReference(BaseModel):
    """Structured git module source reference."""

    source: Literal["git"]
    data: ModuleGitSourceData


class LocalModuleSourceReference(BaseModel):
    """Structured local module source reference."""

    source: Literal["local"]
    data: LocalReferenceData


ModuleSourceReference: TypeAlias = Annotated[
    RegistrySourceReference | ModuleGitSourceReference | LocalModuleSourceReference,
    Field(discriminator="source"),
]


def render_module_source_identity(
    source: ModuleSourceReference,
    options: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Return canonical (source, version/ref) tuple for cache and vendoring keys."""
    match source:
        case RegistrySourceReference(data=data):
            return data.address, data.version
        case ModuleGitSourceReference(data=data):
            in_repo_path = f"//{data.path}" if data.path else ""
            return f"{data.repo}{in_repo_path}", data.ref
        case LocalModuleSourceReference(data=data):
            local_path = Path(data.path).expanduser()
            if options is not None and options.get("base_path") is not None:
                base_path = Path(options["base_path"])
                if not local_path.is_absolute():
                    local_path = (base_path / local_path).resolve()
            return str(local_path), "local"

    raise ValueError(f"Unsupported module source: {source!r}")


def render_module_source_fields(
    source: ModuleSourceReference,
    options: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render Terraform module source fields from structured source data."""
    match source:
        case RegistrySourceReference(data=data):
            return {"source": data.address, "version": data.version}
        case ModuleGitSourceReference(data=data):
            module_path = f"//{data.path}" if data.path else ""
            git_source = f"git::{data.repo}{module_path}"
            query_suffix = f"ref={data.ref}"
            return {
                "source": (
                    f"{git_source}&{query_suffix}"
                    if "?" in git_source
                    else f"{git_source}?{query_suffix}"
                )
            }
        case LocalModuleSourceReference(data=data):
            local_path = Path(data.path)
            if options is not None and options.get("base_path") is not None:
                base_path = Path(options["base_path"])
                if not local_path.is_absolute():
                    local_path = (base_path / local_path).resolve()
            return {"source": str(local_path)}

    raise ValueError(f"Unsupported module source: {source!r}")


class ProviderSourceReference(BaseModel):
    """Structured provider source reference."""

    source: Literal["registry"]
    data: RegistrySourceData


def render_provider_source_fields(
    source: ProviderSourceReference,
    options: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render provider source/version fields for required_providers blocks."""
    return {
        "source": source.data.address,
        "version": source.data.version,
    }


class ValidationSpec(BaseModel):
    """Reusable validation rule defined as inline code or a local script."""

    inline: str | None = None
    script: FileReference | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ValidationSpec":
        if (self.inline is None) == (self.script is None):
            raise ValueError(
                "Exactly one of 'inline' or 'script' must be set for a validation"
            )
        return self


class TransformSpec(BaseModel):
    """Reusable property transform rule defined as Python or Jinja."""

    inline: str | None = None
    script: FileReference | None = None
    jinja: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "TransformSpec":
        if (
            sum(source is not None for source in (self.inline, self.script, self.jinja))
            != 1
        ):
            raise ValueError(
                "Exactly one of 'inline', 'script', or 'jinja' must be set for a transform"
            )
        return self


class ModulePropertySpec(BaseModel):
    """Combined per-property module configuration."""

    mapped_to: str | None = None
    default: Any | None = None
    transform: TransformSpec | None = None
    validation: ValidationSpec | None = None
    auto_inject: bool | None = None


class StackMeta(BaseModel):
    """Metadata identifying a stack."""

    name: str


class ComponentDefinition(BaseModel):
    """Definition of a single component within a stack."""

    type: str
    tags: set[str] = Field(default_factory=set)
    properties: dict[str, Any] = Field(default_factory=dict)


class OperationInvocation(BaseModel):
    """A stack's request to run an operation approved in managed config."""

    use: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    rerun_token: str | None = None
    depends_on: list[str] = Field(default_factory=list)


class StackDefinition(BaseModel):
    """Complete parsed stack definition from a YAML or JSON file."""

    name: str
    tags: set[str] = Field(default_factory=set)
    depends_on: list[str] = Field(default_factory=list)
    mock_outputs: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, ComponentDefinition] = Field(default_factory=dict)
    operations: dict[str, OperationInvocation] = Field(default_factory=dict)
    source_path: Path | None = Field(default=None, exclude=True)


class RunFile(BaseModel):
    """Stacksmith invocation manifest describing input layers."""

    merge_mode: MergeMode | None = None
    stacks: list[FileReference] = Field(default_factory=list)
    configs: list[FileReference] = Field(default_factory=list)
    vars: list[FileReference] = Field(default_factory=list)
    var: dict[str, Any] = Field(default_factory=dict)


class BackendConfig(BaseModel):
    """Backend configuration with explicit backend type and freeform settings."""

    model_config = ConfigDict(extra="allow")

    type: str

    @model_validator(mode="after")
    def _type_is_set(self) -> "BackendConfig":
        if not self.type.strip():
            raise ValueError("Backend 'type' must be a non-empty string")
        return self

    @property
    def config(self) -> dict[str, Any]:
        """Return the active backend provider configuration."""
        return self.model_dump(exclude={"type"})

    def config_with_state_key(self, state_key: str) -> dict[str, Any]:
        """Return backend config augmented with a stack-specific state key."""
        config = dict(self.config)
        if self.type == "local":
            path = config.get("path")
            if isinstance(path, str) and not path.endswith(state_key):
                config["path"] = str(Path(path).joinpath(state_key))
        elif "key" in config:
            if not config["key"]:
                config["key"] = state_key
        elif self.type in {"s3", "azurerm", "gcs", "oss"}:
            config.setdefault("key", state_key)
        return config


class ToolDownloadSpec(BaseModel):
    """Tool download metadata for missing binaries."""

    url_template: str
    sha256: str | None = None
    sha256_url_template: str | None = None

    @model_validator(mode="after")
    def _validate_url_template(self) -> "ToolDownloadSpec":
        if not self.url_template.strip():
            raise ValueError("Tool download url_template must be a non-empty string")
        return self


class ToolBinaryConfig(BaseModel):
    """Tool binary resolution settings."""

    version: str
    download: ToolDownloadSpec | None = None

    @model_validator(mode="after")
    def _validate_version(self) -> "ToolBinaryConfig":
        if not self.version.strip():
            raise ValueError("Tool version must be a non-empty string")
        return self


class ToolsConfig(BaseModel):
    """Configured tool binary settings for OpenTofu and Terragrunt."""

    tofu: ToolBinaryConfig
    terragrunt: ToolBinaryConfig


class ProviderConfigSpec(BaseModel):
    """Reusable provider config rule defined as inline code, script, or data."""

    model_config = ConfigDict(extra="forbid")

    inline: str | None = None
    script: FileReference | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ProviderConfigSpec":
        if (
            sum(source is not None for source in (self.inline, self.script, self.data))
            != 1
        ):
            raise ValueError(
                "Exactly one of 'inline', 'script', or 'data' must be set for a provider config"
            )
        if self.data is not None and not self.data:
            raise ValueError("Provider config 'data' must not be empty")
        return self


_PROVIDER_INSTANCE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def parse_provider_instance_reference(reference: str) -> tuple[str, str]:
    """Parse a provider instance reference in `<provider>.<instance>` format."""
    normalized = reference.strip()
    if not _PROVIDER_INSTANCE_REFERENCE_RE.fullmatch(normalized):
        raise ValueError("Provider reference must use '<provider>.<instance>' format")
    provider_name, instance_name = normalized.split(".", 1)
    return provider_name, instance_name


class ProviderInstance(BaseModel):
    """Single provider instance configuration."""

    alias: str | None = None
    config: ProviderConfigSpec


class ProviderFamily(BaseModel):
    """Provider source/version with one or more named instances."""

    source: ProviderSourceReference
    instances: dict[str, ProviderInstance]

    @model_validator(mode="after")
    def _validate_instances(self) -> "ProviderFamily":
        if "default" in self.instances:
            if self.instances["default"].alias is not None:
                raise ValueError("Provider default instance must not define an alias")

        aliases = set()
        for instance_name, instance in self.instances.items():
            if instance_name == "default":
                continue
            if not instance.alias:
                raise ValueError(
                    f"Provider instance '{instance_name}' must define an alias"
                )
            if instance.alias in aliases:
                raise ValueError(
                    f"Provider alias '{instance.alias}' is duplicated in one family"
                )
            aliases.add(instance.alias)
        return self


class ModuleMapping(BaseModel):
    """Mapping from an abstract resource type to a concrete OpenTofu module."""

    description: str | None = None
    source: ModuleSourceReference
    auto_inject: bool = False
    tags: set[str] = Field(default_factory=set)
    properties: dict[str, "ModulePropertySpec"] = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)


class OperationInputSpec(BaseModel):
    """Input contract for a native Stacksmith operation."""

    required: bool = False
    secret: bool = False


class LocalOperationDefinition(BaseModel):
    """Config-owned local process operation."""

    runner: Literal["local"]
    description: str | None = None
    trigger: Literal["manual", "after_apply"] = "manual"
    command: list[str]
    working_directory: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, OperationInputSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_command(self) -> "LocalOperationDefinition":
        if not self.command or any(not argument.strip() for argument in self.command):
            raise ValueError("Operation command must contain non-empty arguments")
        unknown_inputs = sorted(set(self.environment.values()) - set(self.inputs))
        if unknown_inputs:
            raise ValueError(
                "Operation environment references undeclared inputs: "
                f"{', '.join(unknown_inputs)}"
            )
        return self


class JenkinsOperationDefinition(BaseModel):
    """Config-owned Jenkins build operation."""

    runner: Literal["jenkins"]
    description: str | None = None
    trigger: Literal["manual", "after_apply"] = "manual"
    url: str
    job_name: str
    username_env: str
    api_token_env: str
    parameters: dict[str, str] = Field(default_factory=dict)
    inputs: dict[str, OperationInputSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_fields(self) -> "JenkinsOperationDefinition":
        if not self.url.startswith(("http://", "https://")):
            raise ValueError(
                "Jenkins operation url must start with http:// or https://"
            )
        if not self.job_name.strip():
            raise ValueError("Jenkins operation job_name must be non-empty")
        unknown_inputs = sorted(set(self.parameters.values()) - set(self.inputs))
        if unknown_inputs:
            raise ValueError(
                "Jenkins operation parameters reference undeclared inputs: "
                f"{', '.join(unknown_inputs)}"
            )
        return self


OperationDefinition: TypeAlias = Annotated[
    LocalOperationDefinition | JenkinsOperationDefinition,
    Field(discriminator="runner"),
]


class PlanValidation(BaseModel):
    """Reserved future post-plan validation rule."""

    description: str = ""
    enabled: bool = True
    rule: ValidationSpec


class RemoteAuthEntry(BaseModel):
    """Auth credentials for a single remote host."""

    type: Literal["token", "basic", "ssh"]
    token_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    ssh_key_path: str | None = None

    def to_http_headers(self) -> dict[str, str]:
        """Build HTTP headers from this auth entry."""
        import os

        match self.type:
            case "token" if self.token_env:
                token = os.getenv(self.token_env, "")
                if token:
                    return {"Authorization": f"Bearer {token}"}
            case "basic" if self.username_env and self.password_env:
                import requests.auth

                username = os.getenv(self.username_env, "")
                password = os.getenv(self.password_env, "")
                if username and password:
                    return {
                        "Authorization": requests.auth._basic_auth_str(
                            username, password
                        )
                    }
        return {}


RemoteAuthConfig = dict[str, RemoteAuthEntry]


class ToolConfig(BaseModel):
    """Complete tool configuration loaded from .config.yaml."""

    backend: BackendConfig
    tools: ToolsConfig
    provider_mappings: dict[str, ProviderFamily]
    module_mappings: dict[str, ModuleMapping]
    operations: dict[str, OperationDefinition] = Field(default_factory=dict)
    var_validations: dict[str, ValidationSpec] = Field(default_factory=dict)
    plan_validations: dict[str, PlanValidation] = Field(default_factory=dict)
    remote_auth: dict[str, RemoteAuthEntry] = Field(default_factory=dict)
    source_path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _validate_module_provider_references(self) -> "ToolConfig":
        for module_name, module in self.module_mappings.items():
            for child_provider_name, provider_reference in module.providers.items():
                provider_name, instance_name = parse_provider_instance_reference(
                    provider_reference
                )
                provider_family = self.provider_mappings.get(provider_name)
                if provider_family is None:
                    raise ValueError(
                        f"Module '{module_name}' provider '{child_provider_name}' "
                        f"references unknown provider family '{provider_name}'"
                    )
                if instance_name not in provider_family.instances:
                    raise ValueError(
                        f"Module '{module_name}' provider '{child_provider_name}' "
                        f"references unknown provider instance '{provider_reference}'"
                    )
        return self
