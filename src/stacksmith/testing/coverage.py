from ..models import StacksmithTestManifest, ToolConfig


def find_untested_policies(
    config: ToolConfig, manifest: StacksmithTestManifest
) -> list[str]:
    """List named variable and plan policies without manifest cases.

    Args:
        config: Effective managed configuration, including disabled policies.
        manifest: Effective merged test manifest.

    Returns:
        Sorted policy addresses with no declared cases. This inventory does not
        measure line coverage or account for pytest selection filters.
    """
    return sorted(
        f"{category}.{name}"
        for category in ("var_validations", "plan_validations")
        for name in getattr(config, category)
        if not getattr(manifest, category).get(name)
    )
