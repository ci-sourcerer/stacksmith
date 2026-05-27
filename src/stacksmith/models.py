import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidationSpec(BaseModel):
    """Reusable validation rule defined as inline code or a local script."""

    inline: str | None = None
    script: str | None = None

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
    script: str | None = None
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


class StackDefinition(BaseModel):
    """Complete parsed stack definition from a YAML or JSON file."""

    name: str
    tags: set[str] = Field(default_factory=set)
    depends_on: list[str] = Field(default_factory=list)
    mock_outputs: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, ComponentDefinition]
    source_path: Path | None = Field(default=None, exclude=True)


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


class TofuConfig(BaseModel):
    """OpenTofu version configuration."""

    version: str


class ProviderConfigSpec(BaseModel):
    """Reusable provider config rule defined as inline code, script, or data."""

    model_config = ConfigDict(extra="forbid")

    inline: str | None = None
    script: str | None = None
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

    source: str
    version: str
    instances: dict[str, ProviderInstance]

    @model_validator(mode="after")
    def _validate_instances(self) -> "ProviderFamily":
        if "default" in self.instances:
            if self.instances["default"].alias is not None:
                raise ValueError("Provider default instance must not define an alias")

        aliases: set[str] = set()
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
    """Mapping from an abstract resource type to a concrete Terraform module."""

    description: str | None = None
    source: str
    version: str
    auto_inject: bool = False
    tags: set[str] = Field(default_factory=set)
    properties: dict[str, "ModulePropertySpec"] = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_local_source(self) -> "ModuleMapping":
        _validate_module_source(self.source)
        return self


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


_LOCAL_SOURCE_PATTERNS = (
    "file://",
    "./",
    "../",
)


def _validate_module_source(source: str) -> None:
    """Reject module sources that reference local filesystem paths."""
    normalized = source.strip()
    if any(normalized.startswith(pat) for pat in _LOCAL_SOURCE_PATTERNS):
        raise ValueError(
            f"Module source must be a remote URL or registry address, "
            f"not a local path: {source!r}"
        )
    if normalized.startswith("/") and not normalized.startswith("//"):
        raise ValueError(
            f"Module source must be a remote URL or registry address, "
            f"not an absolute local path: {source!r}"
        )


RemoteAuthConfig = dict[str, RemoteAuthEntry]


class ToolConfig(BaseModel):
    """Complete tool configuration loaded from .config.yaml."""

    backend: BackendConfig
    tofu: TofuConfig
    provider_mappings: dict[str, ProviderFamily]
    module_mappings: dict[str, ModuleMapping]
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
