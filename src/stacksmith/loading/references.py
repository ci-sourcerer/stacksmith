from copy import deepcopy
from pathlib import Path
from typing import Any


def _absolutize_local_reference(reference: Any, base_dir: Path) -> None:
    if not isinstance(reference, dict) or reference.get("source") != "local":
        return

    payload = reference.get("data")
    if not isinstance(payload, dict):
        return

    path_value = payload.get("path")
    if not isinstance(path_value, str) or not path_value:
        return

    path = Path(path_value).expanduser()
    if not path.is_absolute():
        payload["path"] = str((base_dir / path).resolve())


def resolve_runfile_local_references(
    data: dict[str, Any],
    runfile_dir: Path,
) -> dict[str, Any]:
    """Resolve local document references relative to a runfile.

    Args:
        data: Parsed runfile data.
        runfile_dir: Directory containing the runfile.

    Returns:
        Copied runfile data with absolute local references.
    """
    result = deepcopy(data)
    for key in ("stacks", "configs", "vars"):
        items = result.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            _absolutize_local_reference(item, runfile_dir)
    return result


def _resolve_module_mapping_references(module: Any, config_dir: Path) -> None:
    if not isinstance(module, dict):
        return

    _absolutize_local_reference(module.get("source"), config_dir)
    properties = module.get("properties")
    if not isinstance(properties, dict):
        return
    for property_spec in properties.values():
        if not isinstance(property_spec, dict):
            continue
        if isinstance(transform := property_spec.get("transform"), dict):
            _absolutize_local_reference(transform.get("script"), config_dir)
        if isinstance(validation := property_spec.get("validation"), dict):
            _absolutize_local_reference(validation.get("script"), config_dir)


def resolve_config_local_references(
    data: dict[str, Any],
    config_dir: Path,
) -> dict[str, Any]:
    """Resolve local module and script references relative to a config.

    Args:
        data: Parsed managed configuration.
        config_dir: Directory containing the configuration.

    Returns:
        Copied configuration with absolute local references.
    """
    result = deepcopy(data)

    if isinstance(var_validations := result.get("var_validations"), dict):
        for spec in var_validations.values():
            if isinstance(spec, dict):
                _absolutize_local_reference(spec.get("script"), config_dir)

    if isinstance(module_mappings := result.get("module_mappings"), dict):
        for module in module_mappings.values():
            _resolve_module_mapping_references(module, config_dir)
    _resolve_module_mapping_references(result.get("default_module_mapping"), config_dir)

    if isinstance(provider_mappings := result.get("provider_mappings"), dict):
        for provider in provider_mappings.values():
            if not isinstance(provider, dict) or not isinstance(
                instances := provider.get("instances"),
                dict,
            ):
                continue
            for instance in instances.values():
                if not isinstance(instance, dict):
                    continue
                if isinstance(provider_config := instance.get("config"), dict):
                    _absolutize_local_reference(
                        provider_config.get("script"),
                        config_dir,
                    )

    if isinstance(plan_validations := result.get("plan_validations"), dict):
        for plan_spec in plan_validations.values():
            if not isinstance(plan_spec, dict):
                continue
            if isinstance(rule := plan_spec.get("rule"), dict):
                _absolutize_local_reference(rule.get("script"), config_dir)

    return result


def resolve_test_manifest_local_references(
    data: dict[str, Any],
    manifest_dir: Path,
) -> dict[str, Any]:
    """Resolve local fixture scripts relative to a test manifest.

    Args:
        data: Parsed test manifest.
        manifest_dir: Directory containing the manifest.

    Returns:
        Copied manifest with absolute local fixture references.
    """
    result = deepcopy(data)
    fixtures = result.get("fixtures")
    if not isinstance(fixtures, dict):
        return result

    for fixture_name in ("setup", "teardown"):
        if isinstance(fixture_spec := fixtures.get(fixture_name), dict):
            _absolutize_local_reference(fixture_spec.get("script"), manifest_dir)
    return result
