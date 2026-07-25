# Shared configuration example

This directory is a platform-owned Stacksmith managed configuration. It defines reusable validation policies, property transforms, provider configuration, and module mappings for the example stacks.

## Testing policies and transforms

The `tests/` directory uses pytest and Stacksmith's auto-loaded pytest plugin. Run the complete example suite from the repository root.

```shell
pytest examples/shared-config-repo/tests
```

The `stacksmith_test_runner` fixture discovers the nearest `stacksmith-config.yaml`, so the tests use this configuration and resolve its relative scripts exactly as Stacksmith does at runtime.

When running from another directory or testing a different managed configuration, provide the path explicitly.

```shell
pytest examples/shared-config-repo/tests \
  --stacksmith-config examples/shared-config-repo/stacksmith-config.yaml
```

If pytest plugin auto-loading is disabled, add `-p stacksmith.pytest_plugin` to either command.

The examples are organized by the Stacksmith behavior under test.

- `test_plan_policies.py` tests post-plan validation outcomes, including warnings.
- `test_variable_policies.py` tests resolved-input validation rules.
- `test_property_transforms.py` tests configured component-property transforms.
