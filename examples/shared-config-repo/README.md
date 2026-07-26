# Shared configuration example

This directory is a platform-owned Stacksmith managed configuration. It defines reusable validation policies, property transforms, provider configuration, and module mappings for the example stacks.

## Testing policies and transforms

This example uses a declarative `tests.yaml` manifest. `stacksmith test` compiles it into an ephemeral pytest module and executes it with Stacksmith's pytest plugin.

```shell
stacksmith test --config examples/shared-config-repo/stacksmith-config.yaml
```

`stacksmith test` supports repeated `--config` options and uses the same ordered configuration merge behavior as other Stacksmith commands. Pass additional pytest arguments after `--`.

```shell
stacksmith test \
  --config platform/stacksmith-config.yaml \
  --config environments/prod/stacksmith-config.yaml \
  -- -k imdsv2
```

Pass one or more explicit manifest paths (or directories that contain a manifest) when the tests file is not beside the selected config layer.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-config.yaml \
  examples/shared-config-repo/tests.yaml
```

Use `--dump-tests` to keep the generated pytest module for debugging.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-config.yaml \
  --dump-tests /tmp/stacksmith-generated-tests.py
```

The manifest includes test cases for all supported behaviors.

- `variable_policies` tests resolved-input validation rules.
- `plan_policies` tests post-plan validation outcomes, including warnings.
- `component_properties` tests configured transforms and validations.
- `fixtures` supports optional setup and teardown hooks through `inline` or `script` definitions, with execution mode set by `fixtures.mode` (`per-suite` or `per-test-case`).
