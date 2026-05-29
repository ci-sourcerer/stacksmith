# Stacksmith

## Overview

Stacksmith is a CLI tool that lets teams define infrastructure stacks in a simple YAML (or JSON) format and deploy them via [OpenTofu](https://opentofu.org) and [Terragrunt](https://terragrunt.gruntwork.io). It bridges the gap between a developer writing a plain component list and the OpenTofu ecosystem by abstracting module wiring, backend configuration, variable resolution, policy checks, and monorepo orchestration.

## Concepts

### Stack

A stack is the unit of infrastructure authored by application or service teams. A stack file contains metadata (`stack`, `tags`, `depends_on`, `mock_outputs`) and a set of `components`. It is the "calling code" that references abstract [component](#components) types declared in the managed config and provides properties for those components.

### Managed config

The managed config (`stacksmith-config.yaml`) is the shared contract controlled by platform teams. It defines backend settings, OpenTofu version, providers, module mappings, and centralized validation/transform rules.

### Components

Components are the entries under `components` in a stack file. Each component declares:

- `type`: an abstract type mapped by managed config to a OpenTofu module
- `tags`: optional targeting tags
- `properties`: module input values authored by stack owners

### Tags and targeting

Stacksmith supports both stack-level and component-level targeting.

- Stack tags come from the stack `tags` field and can be filtered in `run-all` with `--include-tag` and `--exclude-tag`.
- Component tags come from component `tags` plus optional managed-config module tags.
- Target expressions use `--tag-expr` and are evaluated with context keys including `tags`, `tag`, `stack_tags`, `resource_name`, and `resource_type`.

### Inputs

Input resolution order from lowest to highest priority:

1. Vars files from `STACKSMITH_VARS`, when used
2. Environment variables prefixed with `STACKSMITH_VAR_`
3. Explicit `--vars` and `--var key=value` entries, deep-merged in the order they appear on the command line

### Validation and transforms

Stacksmith supports Python-based validation and transform hooks.

- Validations use either `inline` Python or `script`.
- Transforms use `inline`, `script`, or `jinja` depending on context.
- Relative script paths resolve from the declaring file.

### Plan validations

Managed config can define `plan_validations` that run after `plan` and `run-all plan` against OpenTofu plan JSON output.

Plan validation rules can return `pass`, `warn`, or `fail` outcomes.

- Legacy boolean behavior is still supported, where truthy values pass and falsey values fail.
- Warnings are non-blocking by default.
- Use `--strict-validation-warnings` to treat warning outcomes as failures.
- Use `--fail-on-changes` on `plan` or `run-all plan` to return a non-zero exit code whenever the rendered plan contains any resource changes. This is useful for automated drift detection or CI checks where only a non-empty plan should fail.

### Remote resources

Config files, vars files, validation scripts, and transform scripts can be resolved from HTTP(S) and git URLs. Operational details are documented in [Remote resources](#remote-resources).

## How to use Stacksmith

1. Developers write a `stack.yaml` that lists abstract component types and their properties.
2. Stacksmith reads an org-managed config that maps component types to real OpenTofu modules and declares the shared backend and providers.
3. Stacksmith generates a `.tf.json` file (module calls + provider requirements) and a `terragrunt.hcl.json` file (backend, inputs, dependency wiring) into a build directory.
4. Terragrunt is invoked to run `init`, `plan`, `apply`, or `destroy` against that build directory, using OpenTofu as its executor.
5. For drift-aware workflows, pass `--fail-on-changes` to `plan` or `run-all plan` so the command exits non-zero when there are any planned updates, creates, or destroys.

## Configuration

This section shows managed config authoring details. Conceptual definitions for config ownership and responsibilities are documented in [Concepts](#concepts).

```yaml
# stacksmith-config.yaml: maintained by the platform team

backend:
  type: s3
  bucket: my-org-terraform-state
  region: us-east-1

tofu:
  version: "1.11.6"

providers:
  aws:
    source: "hashicorp/aws"
    version: "= 5.91.0"
    instances:
      default:
        config:
          data:
            region: us-east-1
      secondary:
        alias: secondary
        config:
          data:
            region: us-west-2
            assume_role:
              role_arn: arn:aws:iam::123456789012:role/stacksmith-secondary

modules:
  aws_s3_bucket:
    source: "https://github.com/my-org/terraform-aws-s3.git"
    version: "3.2.1"
    providers:
      aws: aws.secondary
    properties:
      acl:
        mapped_to: bucket_acl
  aws_ec2_instance:
    source: "https://github.com/my-org/terraform-aws-ec2.git"
    version: "5.0.0"
```

Provider definitions are grouped by provider family and can expose multiple named instances through `instances`. A `default` instance is optional; if omitted, Stacksmith emits an empty provider block for the unaliased provider. Non-default instances must define an explicit `alias`. Module mappings can optionally define a `providers` map that routes module provider names to an instance reference in `<provider>.<instance>` format. If a module mapping omits `providers`, Stacksmith uses the unaliased provider.

Each provider instance `config` must use exactly one top-level source key:

- `data`: Literal YAML mapping used directly as provider arguments.
- `inline`: Inline Python defining `config(**context)` that returns a dictionary of provider arguments.
- `script`: Path or URL to a Python script defining `config(**context)` that returns a dictionary of provider arguments.

Stacksmith can also introspect remote module sources to discover which OpenTofu `variable` inputs the module actually exposes. When `auto_inject: true` is enabled for a module mapping, stacksmith uses that discovery data to inject same-name resolved inputs automatically, without requiring empty `{}` property declarations for every module input. This means that only module variables that actually exist are auto-injected, unmapped stack inputs that might be organizational like `environment` are not leaked into a module that does not declare them, and explicit `mapped_to` mappings and property overrides still work as before.

A few things to note about the config are as follows.

- **Provider versions should probably be exact pins where possible, not ranges.** Fuzzy constraints like `~> 5.0` leave room for provider updates to silently change behaviour across deployments. The config is the right place to make upgrades deliberate and reviewed.
- **Only approved component types can be used by stacks.** If a component type appears in a stack but not in the config's `modules` catalogue, stacksmith rejects it at generation time.

## Writing a stack

A stack definition describes a logical unit of infrastructure. Developers write it, and managed config resolves implementation details.

```yaml
# stack.yaml

stack:
  name: my-app

tags:
  - apps
  - storage

components:
  app-bucket:
    type: aws_s3_bucket
    properties:
      acl: private
      bucket: "{{ inputs.bucket_name }}"
  app-server:
    type: aws_ec2_instance
    properties:
      ami: ami-0abcdef1234567890
      instance_type: t3.small
```

The S3 state key is derived automatically from the stack file's path relative to the repo root. For example `networking/vpc/stack.yaml` produces key `networking/vpc/terraform.tfstate`. For standalone stacks (single-stack commands without a `--root`), the key is simply `<name>/terraform.tfstate`.

Concept-level details for tags, input resolution, validations, plan validations, and transforms are documented in [Concepts](#concepts). This section intentionally focuses on stack authoring shape and examples.

## Remote resources

Stacksmith can pull scripts, config files, and vars files from remote locations. Anywhere a local file path is accepted for validation scripts, transform scripts, vars files, or config files, a remote URL can be used instead.

### Supported URL formats

HTTP(S) URLs fetch a single file directly.

```text
https://raw.githubusercontent.com/my-org/shared-config/main/validators/bucket.py
```

Git URLs use the `git+` prefix with a double-slash to separate the repo from the path within it, and an optional `@ref` suffix.

```text
git+https://github.com/my-org/shared-config.git//validators/bucket.py@v1.0.0
git+ssh://git@github.com/my-org/shared-config.git//validators/bucket.py@main
```

### Usage examples

In a stack's variable validation or a config's managed validation/transform, use a URL instead of a local path.

```yaml
# stacksmith-config.yaml – remote managed input validation script
var_validations:
  bucket_name:
    script: "https://raw.githubusercontent.com/my-org/shared/main/validators/bucket.py"
```

```yaml
# stacksmith-config.yaml – remote transform script from a git repo
modules:
  aws_s3_bucket:
    source: "https://github.com/my-org/terraform-aws-s3.git"
    version: "3.2.1"
    properties:
      acl:
        mapped_to: bucket_acl
        transform:
          script: "git+https://github.com/my-org/shared.git//transforms/acl.py@v2.0.0"
```

Config files and vars files also support remote URLs via `--config` and `--vars`.

```shell
stacksmith plan \
  --config https://example.com/org-config.yaml \
  --vars git+https://github.com/org/defaults.git//env/base.yaml@v1.2.0 \
  --vars git+https://github.com/org/service-defaults.git//bucket-writer/dev.yaml@v3.4.1
```

### Caching

Fetched resources are cached under a `.cache/` directory inside the build output directory (or `.stacksmith/.cache/` when no build directory is set). Cache entries are keyed by a SHA-256 hash of the URL. Use `--no-cache` to force a re-fetch of all remote resources.

### Environment variable defaults

`STACKSMITH_CONFIG` and `STACKSMITH_VARS` can provide default config and vars references when the corresponding CLI flags are omitted.

`STACKSMITH_STACK` can provide a default stack file path when no positional stack argument is given.

Use colon-delimited lists.

If an item contains colons, such as a remote URL, wrap that item in quotes.

```shell
export STACKSMITH_VARS='"git+https://github.com/org/platform-defaults.git//env/base.yaml@v1.2.0":"git+https://github.com/org/service-defaults.git//bucket-writer/dev.yaml@v3.4.1"'
```

### Authentication

Authentication is resolved by checking the `remote_auth` config section first, then falling back to environment variables.

#### Config-based auth

Add a `remote_auth` section to `stacksmith-config.yaml`, keyed by hostname.

```yaml
remote_auth:
  github.com:
    type: token
    token_env: GITHUB_TOKEN
  gitlab.internal.com:
    type: basic
    username_env: GITLAB_USER
    password_env: GITLAB_PASS
  git.private.com:
    type: ssh
    ssh_key_path: /home/ci/.ssh/deploy_key
```

Supported auth types are `token` (HTTP Bearer or git token), `basic` (HTTP Basic), and `ssh` (Git SSH key).

#### Environment variable fallbacks

When no matching `remote_auth` entry exists, stacksmith checks the following environment variables.

| Variable | Purpose |
| - | - |
| `STACKSMITH_HTTP_TOKEN` | Bearer token for HTTP(S) requests |
| `STACKSMITH_HTTP_USERNAME` / `STACKSMITH_HTTP_PASSWORD` | Basic auth for HTTP(S) |
| `STACKSMITH_GIT_TOKEN` | Token auth for git clone (HTTPS) |
| `STACKSMITH_GIT_SSH_KEY` | Path to SSH private key for git clone |
| `STACKSMITH_SSL_VERIFY` | Set to `false` to disable TLS verification |

> **Note:** Remote config files are fetched _before_ the config is loaded, so `remote_auth` entries are not available for config-level URLs. Use environment variables for authentication when fetching remote configs.

## CLI reference

Single-stack commands default to `stack.yaml` in the current directory (with fallback to `stack.yml` then `stack.json`). `--config` is repeatable; config files are deep-merged in order and later files override earlier ones. If `--config` is omitted, defaults come from `STACKSMITH_CONFIG` (supports one or more paths separated by your OS path separator), otherwise `./stacksmith-config.yaml`.

Paths passed to `--env-file`, `--build-dir`, `--root`, and the positional stack file argument support `~` expansion.

```shell
stacksmith validate [<stack_file>] [--config <config> ...] [--validation-report-format <json|csv>]
stacksmith generate [<stack_file>] [--config <config> ...] [--build-dir <dir>]
stacksmith init     [<stack_file>] [--config <config> ...]
stacksmith plan     [<stack_file>] [--config <config> ...] [--validation-report-format <json|csv>]
stacksmith apply    [<stack_file>] [--config <config> ...] [--auto-approve]
stacksmith destroy  [<stack_file>] [--config <config> ...] [--auto-approve]
stacksmith info inspect [--config <config> ...]
stacksmith info diagnose [<stack_file>] [--config <config> ...]
```

Common flags available on single-stack commands:

| Flag | Description |
| - | - |
| `--config` | Path or URL to `stacksmith-config.yaml` (repeatable). Files are deep-merged in order, with later files overriding earlier files. Supports `http(s)://` and `git+` URLs. Default: `STACKSMITH_CONFIG` using a single value or colon-delimited list. Quote items containing colons. |
| `--vars` | Repeatable path or URL to a vars YAML/JSON file. Explicit `--vars` entries deep-merge with `--var` in the order they are provided on the command line; dicts merge recursively and lists append. Supports `http(s)://` and `git+` URLs. Default: `STACKSMITH_VARS` using a single value or colon-delimited list. Quote items containing colons. |
| `--var key=val` | Input override, repeatable. Deep-merges in CLI order alongside `--vars`. |
| `--build-dir` | Output directory (default: `.stacksmith/` next to the stack file) |
| `--env-file` | Load environment variables from a dotenv-style file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. When omitted, Stacksmith will automatically load `.env` from the current working directory if present. |
| `--no-cache` | Force re-fetch of all remote resources, ignoring the local cache |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via `STACKSMITH_ONLY_USE_LOCAL_MODULES=1`. |
| `--no-local-modules` | Disable local module rewriting even if `STACKSMITH_ONLY_USE_LOCAL_MODULES` is set. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This affects `plan` and `run-all plan`. |
| `--validation-report-format` | Report format for `validate`, `plan`, and `run-all plan`. Choices: `json` (default) and `csv`. |
| `--debug` | Enable debug logging and developer diagnostics, including per-rule validation checks and generated JSON file paths. |

Run-all targeting and plan flags:

| Flag | Description |
| - | - |
| `--tag` | Repeatable simple tag selector. A component must include all specified tags to match. |
| `--tag-expr` | Single JMESPath expression used to select target modules. Expression output must be a strict boolean for each component. |
| `--include-tag` | Repeatable stack filter for `run-all`. Includes stacks that contain at least one of the provided tags. |
| `--exclude-tag` | Repeatable stack filter for `run-all`. Excludes stacks that contain any of the provided tags. |
| `--save-plan-json` | On `plan` and `run-all plan`, persist rendered plan JSON to the given file or directory. Single-stack `plan` accepts either a file path or directory. `run-all plan` writes one `<stack>.json` file per stack into the given directory. |
| `--validation-report-format` | On `run-all plan`, output the validation report as `json` (default) or `csv`. |

`plan` already serves as the dry-run mode for targeted execution, so a separate target dry-run flag is not required.

Expression context includes `tags` (effective tag list), `tag` (boolean map by tag name), `resource_name`, `resource_type`, `stack_name`, and `stack_tags`.

Examples:

```shell
stacksmith plan --tag prod --tag shared
stacksmith plan --tag-expr "contains(tags, 'prod') && (contains(tags, 'shared') || contains(tags, 'critical'))"
stacksmith plan --debug --save-plan-json ./plan.json
stacksmith run-all apply --tag prod --tag-expr "tag.experimental == `false`"
stacksmith run-all plan --debug --save-plan-json ./plans
stacksmith run-all plan --tag-expr "tag.prod && tag.experimental == `false`"
stacksmith run-all plan --include-tag prod --exclude-tag experimental
stacksmith run-all plan --tag-expr "contains(stack_tags, 'prod') && tag.web"
```

If your expression evaluates to a non-boolean value for any component, stacksmith fails fast with an error and no Terragrunt command is run.

Targeted execution is additive. It does not replace normal multi-stack orchestration, and it may fail when omitted targets are required by selected components.

### Validation report output

`validate`, `plan`, and `run-all plan` emit one machine-readable report block to stdout.

Use `--validation-report-format` to select output shape.

- `json` (default) preserves the existing structured payload.
- `csv` emits one row per validation result with summary fields repeated per row.

Human-oriented logs, Terragrunt/OpenTofu progress output, and diagnostics are written to stderr so stdout can be piped directly into tools like `jq`.

```json
{
  "command": "plan",
  "status": "warn",
  "exit_code": 0,
  "strict_validation_warnings": false,
  "summary": {
    "pass": 2,
    "warn": 1,
    "fail": 0
  },
  "results": [
    {
      "name": "require_imdsv2",
      "status": "warn",
      "message": "IMDSv2 check returned warning",
      "stack_name": "web"
    }
  ]
}
```

### CSV schema

When using `--validation-report-format csv`, Stacksmith emits two row types.

- One `report` row with overall command status and summary counts.
- One `result` row per validation result.

Result rows leave report-level columns empty to reduce duplication. Columns and meanings:

| Column | Description |
| - | - |
| `row_type` | Either `report` or `result`. |
| `command` | The CLI command that produced the report (e.g. `plan`, `validate`). |
| `report_status` | Overall report status: `pass`, `warn`, or `fail`. |
| `exit_code` | Numeric exit code emitted by the CLI process. |
| `strict_validation_warnings` | `true` if `--strict-validation-warnings` was used, else `false`. |
| `stack_count` | Number of stacks included in a multi-stack run (typically populated on `report` rows). |
| `summary_pass` | Count of passing validation results. |
| `summary_warn` | Count of warnings. |
| `summary_fail` | Count of failures. |
| `stack_name` | Stack name associated with the row (report stack for single-stack commands, result stack for `result` rows). |
| `result_name` | Validation rule name (or `validate` for var/validate commands). Populated on `result` rows. |
| `result_status` | Result status for this rule: `pass`, `warn`, or `fail`. Populated on `result` rows. |
| `result_message` | Short human-readable summary for the result. Populated on `result` rows. |
| `result_detail_json` | JSON-encoded detail payload for the result, including the long plan/value text when present. Populated on `result` rows. |

Exit behavior is as follows.

> Note: The CSV output format is subject to change; prefer `json` for stable machine-readable output.

- Exit code is `1` when at least one validation result is `fail`.
- Exit code is `1` for warnings only when `--strict-validation-warnings` is set.

This direct pipeline works without extra filtering.

```shell
stacksmith plan stack.yaml --config ./stacksmith-config.yaml | jq '.status'
stacksmith plan stack.yaml --config ./stacksmith-config.yaml --validation-report-format csv > validation-report.csv
```

## Info commands

Use `info inspect` to review configured modules, mappings, and metadata.

`info inspect --format json` and `info inspect --format yaml` write machine-readable output to stdout.

`info inspect --format table` writes human-readable output to stderr.

```shell
stacksmith info inspect --config ./stacksmith-config.yaml
```

Use `info diagnose` to inspect cache and module-resolution diagnostics for a stack.

`info diagnose` writes diagnostics to stderr.

```shell
stacksmith info diagnose stack.yaml --config ./stacksmith-config.yaml
```

## Monorepo orchestration

In a monorepo, stacksmith recursively discovers all `stack.yaml`/`stack.yml`/`stack.json` files from a root directory and builds a dependency graph from `depends_on` declarations.

### Inter-stack dependencies

When a stack declares `depends_on`, all OpenTofu outputs from the dependency are automatically passed as inputs. Stack authors never write output or input declarations; the wiring is inferred. If you need to reference a created item's attribute in another program, it is recommended you do so by using the API or CLI of the target system (e.g. AWS CLI) rather than OpenTofu outputs, as this creates a more explicit and decoupled contract between stacks.

For plan and apply stages, Terragrunt `mock_outputs` are used so that dependent stacks can be planned before dependencies have been applied. Define expected output shapes in the stack that _produces_ them:

```yaml
# networking/vpc/stack.yaml
stack:
  name: vpc

mock_outputs:
  vpc_id: "mock-vpc-id"
  subnet_ids:
    - "mock-subnet-1"
    - "mock-subnet-2"

components:
  main-vpc:
    type: aws_vpc
    properties:
      cidr_block: "10.0.0.0/16"
```

```yaml
# compute/web/stack.yaml
stack:
  name: web

depends_on:
  - vpc

components:
  web-server:
    type: aws_ec2_instance
    properties:
      instance_type: t3.medium
```

### Monorepo commands

```bash
stacksmith run-all <action> [--root <dir>] [--config <config> ...] [--clean] [--auto-approve]
```

If `STACKSMITH_ROOT` is set, it is used as the default root path. If not, root defaults to the current working directory.

`<action>` is one of `init`, `plan`, `apply`, `destroy`. Stacks are generated in topological dependency order and then Terragrunt is executed per generated stack directory in that order. For `destroy`, execution order is reversed so dependents are destroyed before dependencies.

When `action` is `plan`, you can also pass `--destroy` to run `terragrunt plan -destroy` for every stack.

When `action` is `plan`, you can also pass `--save-plan-json <dir>` to keep the rendered plan JSON for each discovered stack.

Use `--clean` on `run-all` to remove the existing build directory before regeneration.

## Docker

A Docker image is provided that bundles OpenTofu and Terragrunt so no local installation is required. It is also especially useful for CI environments.

As this project is reliant on [Common Python Tasks](https://github.com/ci-sourcerer/common-python-tasks), you can build the image with a simple command: `poe build-image`. You can pass `--build-args TOFU_PROVIDER_SPEC="hashicorp/aws=6.41.0:hashicorp/random=3.8.1"`, for example, to pre-install some OpenTofu providers into the image. This can drastically speed up Stacksmith runs for your users. By default, the image includes no providers, so OpenTofu will download them on demand during execution.

### Pre-installing modules

Similarly to providers, you can pre-install OpenTofu modules into the image using the `TOFU_MODULE_SPEC` build arg. This is a colon-separated list of `source=version` pairs that match the sources and versions in your managed config.

```shell
poe build-image --build-args TOFU_MODULE_SPEC="https://github.com/org/terraform-aws-s3.git=3.2.1:https://github.com/org/terraform-aws-ec2.git=5.0.0"
```

When modules are vendored in the image, Stacksmith automatically rewrites module sources in the generated `main.tf.json` to point to the local vendored copies instead of remote URLs. This eliminates network fetches during `tofu init` and ensures immutable, reproducible builds.

Each module is stored under a deterministic directory name derived from `sha256("<source>|<version>")[:16]`, and a `vendor-manifest.json` is written alongside the directories for reverse lookup.

### Controlling local module rewriting

Local module rewriting (requiring local vendored modules) is controlled by the `STACKSMITH_ONLY_USE_LOCAL_MODULES` environment variable and the `--use-local-modules` / `--no-local-modules` CLI flags.

| Control | Effect |
| - | - |
| `STACKSMITH_ONLY_USE_LOCAL_MODULES=1` | Enable local module rewriting |
| `--use-local-modules` | Enable explicitly from the CLI |
| `--no-local-modules` | Disable even when the env var is set |
| `STACKSMITH_VENDOR_DIR=<path>` | Override the local vendored module root directory |

If a vendored module directory is missing at generation time, Stacksmith fails fast with a clear error rather than silently falling back to remote fetching.

### Extracting the module and provider specs from config

The following recipes use `yq` to extract module and provider specs from a managed config file and pass them directly to `poe build-image`. `TOFU_PROVIDER_SPEC` and `TOFU_MODULE_SPEC` are parsed as colon-separated (`:`) lists of `source=version` items in `Dockerfile.deps`. Provider version ranges that include commas, such as `>= 6.39, < 7.0`, are supported.

```shell
poe build-image --build-args "TOFU_MODULE_SPEC=$(yq -r '.modules | to_entries | map("\(.value.source)=\(.value.version)") | join(":")' <path to stacksmith-config.yaml>)"
```

```shell
poe build-image --build-args "TOFU_PROVIDER_SPEC=$(yq -r '.providers | to_entries | map("\(.value.source)=\(.value.version)") | join(":")' <path to stacksmith-config.yaml>)"
```

## Tips

- Using a monorepo and concerned about who can edit what? Use GitHub's [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) file to restrict write access to certain stack files while allowing broader read access. Similarly, the managed config can be locked down to a small team of platform engineers, while the validation policies themselves can be tightly controlled by a security team.
- Doing a lot of `get` calls on dictionaries in your validation scripts? Try using `jmespath` instead to query complex nested structures with ease. For example, `jmespath.search("components.*.properties.bucket", stack)` would return a list of all bucket properties across all components in the stack.
- Want to take existing resources into consideration for validation rules? Import `boto3` and use it to query AWS directly from your validation scripts. Just be mindful of latency implications.
