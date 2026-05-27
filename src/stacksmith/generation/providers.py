import textwrap
from pathlib import Path
from typing import Any

from ..exceptions import StacksmithConfigError, StacksmithNotFoundError
from ..models import (
    RemoteAuthConfig,
    ToolConfig,
    parse_provider_instance_reference,
)


def _load_provider_config_code(
    spec: Any,
    base_path: Path | None,
    *,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> tuple[str, str]:
    if getattr(spec, "inline", None) is not None:
        return spec.inline, "<inline-provider-config>"
    if getattr(spec, "data", None) is not None:
        raise StacksmithConfigError(
            "Provider config data does not require code loading"
        )
    if getattr(spec, "script", None) is not None:
        script_path = (
            base_path / spec.script
            if base_path and not Path(spec.script).is_absolute()
            else Path(spec.script)
        )
        if not script_path.is_absolute():
            script_path = script_path.resolve()
        if not script_path.exists():
            raise StacksmithNotFoundError(f"Script not found: {script_path}")
        return script_path.read_text(encoding="utf-8"), str(script_path)
    raise StacksmithConfigError(
        "Provider config spec must define exactly one of 'inline', 'script', or 'data'."
    )


def _evaluate_provider_config(
    provider_name: str,
    instance_name: str,
    config: Any,
    *,
    context: dict[str, Any],
    base_path: Path | None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> dict[str, Any]:
    if getattr(config, "data", None) is not None:
        return config.data

    code, origin = _load_provider_config_code(
        config,
        base_path,
        cache_dir=cache_dir,
        auth_config=auth_config,
    )
    code = textwrap.dedent(code)
    ns: dict[str, Any] = {}
    exec(compile(code, origin, "exec"), ns)  # noqa: S102
    config_fn = ns.get("config")
    if not callable(config_fn):
        raise StacksmithConfigError(
            "Provider config code must define a callable 'config(**context)'"
        )

    evaluated = config_fn(
        provider_name=provider_name,
        instance_name=instance_name,
        **context,
    )
    if not isinstance(evaluated, dict):
        raise StacksmithConfigError("Provider config function must return a mapping")
    return evaluated


def _build_required_providers(config: ToolConfig) -> dict[str, dict[str, str]]:
    return {
        provider_name: {
            "source": provider_family.source,
            "version": provider_family.version,
        }
        for provider_name, provider_family in config.provider_mappings.items()
    }


def _build_provider_blocks(
    config: ToolConfig,
    *,
    context: dict[str, Any],
    base_path: Path | None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> dict[str, list[dict[str, Any]]]:
    provider_blocks: dict[str, list[dict[str, Any]]] = {}
    for provider_name, provider_family in config.provider_mappings.items():
        instances: list[dict[str, Any]] = []
        if "default" not in provider_family.instances:
            instances.append({})
        for instance_name, instance in provider_family.instances.items():
            instance_block = _evaluate_provider_config(
                provider_name,
                instance_name,
                instance.config,
                context=context,
                base_path=base_path,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
            if instance_name != "default":
                if instance.alias is None:
                    raise StacksmithConfigError(
                        f"Provider instance '{provider_name}.{instance_name}' must define an alias"
                    )
                instance_block["alias"] = instance.alias
            instances.append(instance_block)
        provider_blocks[provider_name] = instances
    return provider_blocks


def _render_provider_reference(config: ToolConfig, provider_reference: str) -> str:
    provider_name, instance_name = parse_provider_instance_reference(provider_reference)
    instance = config.provider_mappings[provider_name].instances[instance_name]
    if instance_name == "default":
        return provider_name
    if instance.alias is None:
        raise StacksmithConfigError(
            f"Provider instance '{provider_reference}' is missing alias for module routing"
        )
    return f"{provider_name}.{instance.alias}"
