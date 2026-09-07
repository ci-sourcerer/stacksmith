# CLI reference

<!-- BEGIN GENERATED CLI REFERENCE -->
Single-stack commands default to `stack.yaml` in the current directory, with fallback to `stack.yml` then `stack.json`, when neither `--stack`, `STACKSMITH_STACK`, nor `stacksmith.yaml` supplies stack refs.

### `stacksmith`

```text
stacksmith [-h] [--version]
                  {validate,generate,lock,test,run-all,init,plan,apply,destroy,operation,info,ci} ...
```

YAML/JSON-driven Terragrunt wrapper

| Argument | Description |
| - | - |
| `--version` | show program's version number and exit |

#### Commands

| Command | Description |
| - | - |
| `validate` | Validate stack schema and variables |
| `generate` | Generate .tf.json and terragrunt.hcl.json |
| `lock` | Resolve stack inputs and write a deterministic lockfile |
| `test` | Run declarative tests.yaml manifests for managed config layers |
| `run-all` | Discover all stacks and run terragrunt run-all |
| `init` | Generate + terragrunt init |
| `plan` | Generate + terragrunt plan |
| `apply` | Generate + terragrunt apply |
| `destroy` | Generate + terragrunt destroy |
| `operation` | Plan, run, or destroy native operations approved by managed configuration |
| `info` | Show stacksmith inspection and diagnostics commands |
| `ci` | Prepare, inspect, and execute CI workflows |

### `stacksmith validate`

```text
stacksmith validate [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                           [--strict-validation-warnings] [--use-local-modules | --no-local-modules]
                           [--debug | -q] [--validation-report-format {json}]
                           [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |

### `stacksmith generate`

```text
stacksmith generate [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                           [--strict-validation-warnings] [--use-local-modules | --no-local-modules]
                           [--debug | -q] [--locked] [--offline] [--lockfile LOCKFILE]
                           [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith lock`

```text
stacksmith lock [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--lockfile LOCKFILE]
                       [--check]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |
| `--check` | Verify that the existing lockfile matches current resolved inputs. |

### `stacksmith test`

```text
stacksmith test [-h] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                       [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                       [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                       --no-local-modules] [--debug | -q] [--dump-tests DUMP_TESTS]
                       [test_path ...]
```

| Argument | Description |
| - | - |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `test_path` | Optional tests.yaml paths or directories. Defaults to tests.yaml beside each selected config layer. |
| `--dump-tests` | Write generated pytest code to this path before execution. |

### `stacksmith run-all`

```text
stacksmith run-all [-h] [--root ROOT] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                          [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                          [--strict-validation-warnings] [--use-local-modules | --no-local-modules] [--debug |
                          -q] [--validation-report-format {json}] [--destroy]
                          [--save-plan-json SAVE_PLAN_JSON |
                          --save-redacted-plan-json SAVE_REDACTED_PLAN_JSON] [--out OUT] [--fail-on-changes]
                          [--plan PLAN] [--no-after-apply] [--tag TAG] [--tag-expr TAG_EXPR]
                          [--include-tag INCLUDE_TAG] [--exclude-tag EXCLUDE_TAG] [--clean] [--auto-approve]
                          [--dry-run] [--format {table,json}]
                          {init,plan,apply,destroy}
```

| Argument | Description |
| - | - |
| `action` | Terragrunt action to run across all stacks. Choices: `init`, `plan`, `apply`, `destroy`. |
| `--root` | Root directory to discover stacks in (default: current working directory) |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save raw rendered plan JSON to the given file or directory. The raw document can contain sensitive values. |
| `--save-redacted-plan-json` | Save archive-safe redacted plan JSON to the given file or directory. |
| `--out` | Save generated execution plan to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--plan` | Path or directory to a pre-generated execution plan to apply. |
| `--no-after-apply` | When applying infrastructure, do not automatically reconcile operations configured with trigger: after_apply. Use a separate operation run phase instead. |
| `--tag` | Select components by tag. Repeat to require multiple tags. Supported for run-all plan/apply/destroy. |
| `--tag-expr` | JMESPath expression used to select resource targets. Supported for run-all plan/apply/destroy. |
| `--include-tag` | Include stacks that have this tag. Repeatable. |
| `--exclude-tag` | Exclude stacks that have this tag. Repeatable. |
| `--clean` | Remove existing build output directory before generation |
| `--auto-approve` | Skip interactive approval for apply/destroy |
| `--dry-run` | Preview discovery, validation, targeting, and commands without writing generated files or invoking Terragrunt. |
| `--format` | Output format for dependency and execution preview data. Choices: `table`, `json`. |

### `stacksmith init`

```text
stacksmith init [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--locked] [--offline]
                       [--lockfile LOCKFILE]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith plan`

```text
stacksmith plan [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--destroy]
                       [--save-plan-json SAVE_PLAN_JSON | --save-redacted-plan-json SAVE_REDACTED_PLAN_JSON]
                       [--out OUT] [--fail-on-changes] [--tag TAG] [--tag-expr TAG_EXPR]
                       [--validation-report-format {json}] [--locked] [--offline] [--lockfile LOCKFILE]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save raw rendered plan JSON to the given file or directory. The raw document can contain sensitive values. |
| `--save-redacted-plan-json` | Save archive-safe redacted plan JSON to the given file or directory. |
| `--out` | Save generated execution plan to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith apply`

```text
stacksmith apply [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                        [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                        [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                        [--use-local-modules | --no-local-modules] [--debug | -q] [--plan PLAN]
                        [--no-after-apply] [--tag TAG] [--tag-expr TAG_EXPR] [--auto-approve] [--locked]
                        [--offline] [--lockfile LOCKFILE]
                        [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--plan` | Path or directory to a pre-generated execution plan to apply. |
| `--no-after-apply` | When applying infrastructure, do not automatically reconcile operations configured with trigger: after_apply. Use a separate operation run phase instead. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--auto-approve` | Skip interactive approval |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith destroy`

```text
stacksmith destroy [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                          [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                          [--strict-validation-warnings] [--use-local-modules | --no-local-modules] [--debug |
                          -q] [--tag TAG] [--tag-expr TAG_EXPR] [--auto-approve] [--locked] [--offline]
                          [--lockfile LOCKFILE]
                          [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--auto-approve` | Skip interactive approval |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith operation plan`

```text
stacksmith operation plan [-h] [--after-apply] [--destroy] [--force-rerun] [--stack STACK]
                                 [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                                 [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                                 [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                                 [--use-local-modules | --no-local-modules] [--debug | -q]
                                 [operation_names] [stack_file]
```

| Argument | Description |
| - | - |
| `operation_names` | Comma-delimited stack-local operation names. Omit to select all operations declared by the stack. |
| `--after-apply` | Select only operations configured with the after_apply trigger. |
| `--destroy` | Plan destruction of the complete isolated operation state. |
| `--force-rerun` | Force the operation runner resource to be replaced even when its execution identity has not changed. Can also be enabled with STACKSMITH_FORCE_RERUN=1. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith operation run`

```text
stacksmith operation run [-h] [--force-rerun] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                                [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                                --no-local-modules] [--debug | -q]
                                [operation_names] [stack_file]
```

| Argument | Description |
| - | - |
| `operation_names` | Comma-delimited stack-local operation names. Omit to select all operations declared by the stack. |
| `--force-rerun` | Force the operation runner resource to be replaced even when its execution identity has not changed. Can also be enabled with STACKSMITH_FORCE_RERUN=1. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith operation destroy`

```text
stacksmith operation destroy [-h] [--auto-approve] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                                    [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                    [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                    [--no-cache] [--no-cas] [--strict-validation-warnings]
                                    [--use-local-modules | --no-local-modules] [--debug | -q]
                                    [stack_file]
```

| Argument | Description |
| - | - |
| `--auto-approve` | Apply the generated operation-state destruction plan without prompting. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info modules-and-policies`

```text
stacksmith info modules-and-policies [-h] [--format {table,json}] [--basic] [--runfile RUNFILE]
                                            [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                            [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                            [--no-cache] [--no-cas] [--strict-validation-warnings]
                                            [--use-local-modules | --no-local-modules] [--debug | -q]
                                            [component_type ...]
```

| Argument | Description |
| - | - |
| `component_type` | Component type(s) to inspect. Inspects all when omitted. |
| `--format` | Output format (default: table). Choices: `table`, `json`. |
| `--basic` | Show only input, validation, and transform columns in the module table. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info diagnose`

```text
stacksmith info diagnose [-h] [--stack STACK] [--format {table,json}] [--verbose] [--runfile RUNFILE]
                                [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                                --no-local-modules] [--debug | -q]
                                [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--format` | Output format for diagnostics. Choices: `table`, `json`. |
| `--verbose` | Show additional description metadata in table output. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info graph`

```text
stacksmith info graph [-h] [--action {plan,apply,destroy}] [--root ROOT] [--stack STACK]
                             [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                             [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                             [--no-cache] [--no-cas] [--debug | -q] [--tag TAG] [--tag-expr TAG_EXPR]
                             [--include-tag INCLUDE_TAG] [--exclude-tag EXCLUDE_TAG] [--destroy] [--verbose]
                             [--format {table,json,dot,mermaid}]
```

| Argument | Description |
| - | - |
| `--action` | Terragrunt action used to compute commands and execution order. Choices: `plan`, `apply`, `destroy`. |
| `--root` | Root directory used to discover stacks. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--tag` | Select components by tag. Repeat to require multiple tags. Supported for graph plan/apply/destroy previews. |
| `--tag-expr` | JMESPath expression used to select resource targets. Supported for graph plan/apply/destroy previews. |
| `--include-tag` | Include stacks that have this tag. Repeatable. |
| `--exclude-tag` | Exclude stacks that have this tag. Repeatable. |
| `--destroy` | Preview a destroy plan when the selected action is plan. |
| `--verbose` | Show additional description metadata in table output. |
| `--format` | Output format for dependency and execution preview data. Choices: `table`, `json`, `dot`, `mermaid`. |

### `stacksmith ci environments`

```text
stacksmith ci environments [-h] [--gitops-root GITOPS_ROOT]
                                  [--discovery-mode {folders,flat-files,env-files,env,auto}]
                                  [--environments ENVIRONMENTS] [--event-name EVENT_NAME]
                                  [--changed-path CHANGED_PATH] [--base-ref BASE_REF] [--before BEFORE]
                                  [--after AFTER] [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--event-name` | Optional caller event name used for event-aware selection. |
| `--changed-path` | Changed repository path used for selection simulation. Repeatable. |
| `--base-ref` | Base branch name used for pull-request diff selection. |
| `--before` | Previous commit SHA used for push diff selection. |
| `--after` | Current commit SHA used for push diff selection. |
| `--format` | Output format for environment preview data. Choices: `table`, `json`. |

### `stacksmith ci validate`

```text
stacksmith ci validate [-h] [--gitops-root GITOPS_ROOT]
                              [--discovery-mode {folders,flat-files,env-files,env,auto}]
                              [--environments ENVIRONMENTS] [--workflow-runfile WORKFLOW_RUNFILE]
                              [--workflow-env-file WORKFLOW_ENV_FILE]
                              [--workflow-validation-report-format WORKFLOW_VALIDATION_REPORT_FORMAT]
                              [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--workflow-runfile` | Optional runfile path to validate for CI invocations. |
| `--workflow-env-file` | Env file path to validate for CI invocations. Use /dev/null to represent deterministic no-env-file mode. |
| `--workflow-validation-report-format` | Validation report format value to validate for CI plan runs. |
| `--format` | Output format for CI validation results. Choices: `table`, `json`. |

### `stacksmith ci prepare`

```text
stacksmith ci prepare [-h] [--gitops-root GITOPS_ROOT]
                             [--discovery-mode {folders,flat-files,env-files,env,auto}]
                             [--environments ENVIRONMENTS] [--event-name EVENT_NAME]
                             [--changed-path CHANGED_PATH] [--base-ref BASE_REF] [--before BEFORE]
                             [--after AFTER]
                             --command {test,plan,apply,destroy,plan-operation,apply-operation}
                             [--operation-names OPERATION_NAMES] --config-ref CONFIG_REF [--workdir WORKDIR]
                             [--env-file ENV_FILE] [--stacksmith-args-json STACKSMITH_ARGS_JSON] [--debug]
                             [--no-cas] [--locked] [--offline] [--lockfile LOCKFILE] [--force-rerun]
                             [--validation-report-format {json}] [--fail-on-changes]
                             [--strict-validation-warnings] [--ref-name REF_NAME]
                             [--default-branch DEFAULT_BRANCH] [--is-primary-branch {true,false}]
                             [--skip-branch-validation] [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--event-name` | Optional caller event name used for event-aware selection. |
| `--changed-path` | Changed repository path used for selection simulation. Repeatable. |
| `--base-ref` | Base branch name used for pull-request diff selection. |
| `--before` | Previous commit SHA used for push diff selection. |
| `--after` | Current commit SHA used for push diff selection. |
| `--command` | Stacksmith command to execute for each selected environment. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `apply-operation`. |
| `--operation-names` | Comma-delimited stack-local operation names. Empty selects all for plan-operation and apply-operation commands. |
| `--config-ref` | Platform-managed Stacksmith config reference. |
| `--workdir` | Working directory relative to the checked-out repository. |
| `--env-file` | Environment file path, or /dev/null to disable implicit loading. |
| `--stacksmith-args-json` | JSON array of additional Stacksmith command-line arguments. |
| `--debug` | Enable debug logging and print configured modules and policies before each execution. |
| `--no-cas` | Disable content-addressable caching for generated runtime commands. |
| `--locked` | Require runtime inputs to match the Stacksmith lockfile. |
| `--offline` | Resolve locked remote inputs without network access. |
| `--lockfile` | Optional explicit Stacksmith lockfile path. |
| `--force-rerun` | Force native operation execution even when its identity is unchanged. |
| `--validation-report-format` | Validation report format for plan executions. Choices: `json`. |
| `--fail-on-changes` | Fail plan executions when resource changes are detected. |
| `--strict-validation-warnings` | Treat plan validation warnings as failures. |
| `--ref-name` | Current branch name used for shared branch policy validation. |
| `--default-branch` | Repository default branch used for shared branch policy validation. |
| `--is-primary-branch` | Provider primary-branch indicator when no default branch is available. Choices: `true`, `false`. |
| `--skip-branch-validation` | Skip shared branch and pull-request policy validation. |
| `--format` | Output format for the CI execution manifest. Choices: `table`, `json`. |

### `stacksmith ci execute`

```text
stacksmith ci execute [-h] --manifest MANIFEST --environment ENVIRONMENT
                             [--phase {test,plan,apply,destroy,plan-operation,operation}]
                             [--validation-report-output VALIDATION_REPORT_OUTPUT]
```

| Argument | Description |
| - | - |
| `--manifest` | Path to a JSON manifest emitted by stacksmith ci prepare. |
| `--environment` | Environment row from the manifest to execute. |
| `--phase` | Lifecycle phase to execute. The phase must belong to the manifest command; destroy manifests support infrastructure and operation-state previews, operation-state cleanup, and infrastructure destruction. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `operation`. |
| `--validation-report-output` | Optional path for plan validation report output. When set, plan JSON report output is written to this file. |

### `stacksmith ci prepare-from-env`

```text
stacksmith ci prepare-from-env [-h] [--provider {generic,github-actions,jenkins}]
                                      [--manifest-file MANIFEST_FILE] [--github-output GITHUB_OUTPUT]
```

| Argument | Description |
| - | - |
| `--provider` | CI provider adapter mode. github-actions emits manifest, matrix, and count to GITHUB_OUTPUT. generic and jenkins emit manifest JSON to stdout. Choices: `generic`, `github-actions`, `jenkins`. |
| `--manifest-file` | Optional file path where the generated manifest JSON is written. |
| `--github-output` | Optional override path for GITHUB_OUTPUT when provider is github-actions. |

### `stacksmith ci execute-from-env`

```text
stacksmith ci execute-from-env [-h] [--provider {generic,github-actions,jenkins}]
                                      [--manifest-file MANIFEST_FILE] [--environment ENVIRONMENT]
                                      [--phase {test,plan,apply,destroy,plan-operation,operation}]
                                      [--validation-report-output VALIDATION_REPORT_OUTPUT]
```

| Argument | Description |
| - | - |
| `--provider` | CI provider adapter mode for execution defaults. Choices: `generic`, `github-actions`, `jenkins`. |
| `--manifest-file` | Optional manifest file path override. When omitted, CI_MANIFEST_FILE or STACKSMITH_CI_MANIFEST is used. |
| `--environment` | Optional environment name override. When omitted, STACKSMITH_ENVIRONMENT or ENVIRONMENT is used. |
| `--phase` | Optional lifecycle phase override. When omitted, STACKSMITH_CI_PHASE or the manifest command is used. The phase must belong to the manifest command; destroy manifests support previews, operation-state cleanup, and infrastructure destruction. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `operation`. |
| `--validation-report-output` | Optional plan validation report output path override. When omitted, STACKSMITH_VALIDATION_REPORT_PATH or provider defaults are used. |

### `stacksmith ci redact-plan`

```text
stacksmith ci redact-plan [-h] (--output OUTPUT | --in-place) input
```

| Argument | Description |
| - | - |
| `input` | Path to raw OpenTofu plan JSON. |
| `--output` | Write redacted plan JSON to this path. |
| `--in-place` | Atomically replace the input file with its redacted form. |

<!-- END GENERATED CLI REFERENCE -->
