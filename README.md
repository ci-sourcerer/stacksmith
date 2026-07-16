# Stacksmith

**HEADS UP:** This project is absolutely a work in progress, there is no warranty, I have no idea what I am doing, etc. The current state is a proof-of-concept and its git history may be wiped at any moment. Use at your own risk/feel free to ask what's going on. Also, the license is no joke. This project is open source and how I contribute to it is going to stay that way.

**Definitely untested things:** CI stuff.

## Overview

Stacksmith is a CLI tool that lets teams define infrastructure stacks in a simple YAML (or JSON) format and deploy them via [OpenTofu](https://opentofu.org) and [Terragrunt](https://terragrunt.gruntwork.io). It bridges the gap between a developer writing a plain resource list and the OpenTofu ecosystem by abstracting module wiring, backend configuration, variable resolution, policy checks, and monorepo orchestration.

## Concepts

### Stack

A stack is the unit of infrastructure authored by application or service teams. A stack file contains metadata (`stack`, `tags`, `depends_on`, `mock_outputs`) and a set of `components`. It is the "calling code" that references abstract [component](#components) types declared in the managed config and provides properties for those components.

### Managed config

The managed config (`stacksmith-config.yaml`) is the shared contract controlled by platform teams. It defines backend settings, OpenTofu version, providers, module mappings, and centralized validation/transform rules.

### Components

Components are the entries under `components` in a stack file. Each component declares the following.

- `type`: an abstract type mapped by the [managed config](#managed-config) to a OpenTofu module
- `tags`: optional [targeting tags](#tags-and-targeting)
- `properties`: module input values authored by stack owners

### Tags and targeting

Stacksmith supports both stack-level and component-level targeting.

- Stack tags come from the stack `tags` field and can be filtered in `run-all` with `--include-tag` and `--exclude-tag`.
- Component tags come from component `tags` plus optional managed-config module tags.
- Target expressions use `--tag-expr` and are evaluated with context keys including `tags`, `tag`, `stack_tags`, `component_name`, and `component_type`.

### Inputs

Input resolution order from lowest to highest priority.

1. Vars files from `STACKSMITH_VARS`, when used without `--runfile`
2. Environment variables prefixed with `STACKSMITH_VAR_`
3. `stacksmith.yaml` `vars` and `var` entries, when a runfile is used
4. Explicit `--vars` and `--var key=value` entries, deep-merged in the order they appear on the command line

Runfile `var` entries may use Jinja templates and can reference the resolved `inputs` map as well as stack metadata such as `stack.name` and `stack.tags`. Runfiles can also reference their own resolved filepath using `runfile.path`, which is useful when the runfile location carries environment-specific context.

When `--runfile` is used, Stacksmith treats runfile `vars` as the vars-file source for that invocation and does not apply `STACKSMITH_VARS` defaults or CLI `--vars` entries.

### Validation and transforms

Stacksmith supports Python-based validation and transform hooks.

- Validations use either `inline` Python or `script`.
- Transforms use `inline`, `script`, or `jinja` depending on context.
- Relative script paths resolve from the declaring file.

### Local path resolution

- Local paths in `stacksmith.yaml` runfile `stacks`, `configs`, and `vars` resolve relative to the runfile that declares them.
- Local script paths and local module source paths in `stacksmith-config.yaml` resolve relative to the config file that declares them.

### Plan validations

The [managed config](#managed-config) can define `plan_validations` that run after `plan` and `run-all plan` against OpenTofu plan JSON output.

Plan validation rules can return `pass`, `warn`, or `fail` outcomes.

- Truthy values pass and falsey values fail.
- Warnings are non-blocking by default; use `--strict-validation-warnings` to treat warning outcomes as failures.
- Use `--fail-on-changes` on `plan` or `run-all plan` to return a non-zero exit code whenever the rendered plan contains *any* resource changes. This is useful for automated drift detection or CI checks where only a non-empty plan should fail.

## Configuration

This section shows managed config authoring details. Conceptual definitions for config ownership and responsibilities are documented in [Concepts](#concepts).

```yaml
# stacksmith-config.yaml: maintained by the platform team

backend:
  type: s3
  bucket: my-org-state
  region: us-east-1

tools:
  tofu:
    version: "1.11.6"
  terragrunt:
    version: "1.0.6"

provider_mappings:
  aws:
    source:
      source: registry
      data:
        address: hashicorp/aws
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

module_mappings:
  aws_s3_bucket:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-s3.git
        ref: "3.2.1"
    providers:
      aws: aws.secondary
    properties:
      acl:
        mapped_to: bucket_acl
  aws_ec2_instance:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-ec2.git
        ref: "5.0.0"
```

Provider definitions are grouped by provider family and can expose multiple named instances through `instances`. A `default` instance is optional; if omitted, Stacksmith emits an empty provider block for the unaliased provider. Non-default instances must define an explicit `alias`. Module mappings can optionally define a `providers` map that routes module provider names to an instance reference in `<provider>.<instance>` format. If a module mapping omits `providers`, Stacksmith uses the unaliased provider.

Each provider instance `config` must use exactly one top-level source key to define provider arguments. Supported sources are the following.

- `data`: Literal YAML mapping used directly as provider arguments.
- `inline`: Inline Python defining `config(**context)` that returns a dictionary of provider arguments.
- `script`: Path or URL to a Python script defining `config(**context)` that returns a dictionary of provider arguments.

Stacksmith can also introspect remote module sources to discover which OpenTofu `variable` inputs the module actually exposes. When `auto_inject: true` is enabled for a module mapping, stacksmith uses that discovery data to inject same-name resolved inputs automatically, without requiring empty `{}` property declarations for every module input. This means that only module variables that actually exist are auto-injected, unmapped stack inputs that might be organizational like `environment` are not leaked into a module that does not declare them, and explicit `mapped_to` mappings and property overrides still work as before.

A few things to note about the config are as follows.

- **Provider versions should probably be exact pins where possible, not ranges.** Fuzzy constraints like `~> 5.0` leave room for provider updates to silently change behaviour across deployments. The config is the right place to make upgrades deliberate and reviewed.
- **Only approved component types can be used by stacks.** If a component type appears in a stack but not in the config's `modules` catalogue, stacksmith rejects it at generation time.

## Writing a stack

A stack definition describes a logical unit of infrastructure. Developers write it, and the [managed config](#managed-config) resolves implementation details.

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

Stacksmith property templates can also access stack metadata via `stack.name` and `stack.tags`.
For example, you can compute values from the stack name like `{{ stack.name }}-{{ inputs.bucket_name }}`.

Components in the same stack can consume each other's OpenTofu module outputs with native Terraform references. The component name becomes the module name, so `${module.app_server.private_ip}` passes the `private_ip` output from `app_server` to another component property. OpenTofu infers the dependency from the reference.

### Generating components with Jinja

Stacksmith renders the complete stack source with the resolved `inputs` map before it parses and validates YAML or JSON. This lets a stack template generate any number of explicit components while keeping each generated component independently named, tagged, targeted, and referenced.

```yaml
components:
{% for worker_name, worker in inputs.workers.items() %}
  "{{ worker_name }}":
    type: aws_ec2_instance
    properties:
      ami: {{ worker.ami | tojson }}
      instance_type: {{ worker.instance_type | tojson }}
      tags:
        worker: {{ worker_name | tojson }}
{% endfor %}
```

The same rendering pass handles ordinary values, so existing property expressions such as `bucket: "{{ inputs.bucket_name }}"` remain supported. Use the Jinja `tojson` filter for an unquoted dynamic YAML value when it might contain characters that need escaping.

### State backend

The S3 state key is derived automatically from the stack file's path relative to the repo root. For example `networking/vpc/stack.yaml` produces key `networking/vpc/terraform.tfstate`. For standalone stacks (single-stack commands without a `--root`), the key is simply `<name>/terraform.tfstate`.

Concept-level details for tags, input resolution, validations, plan validations, and transforms are documented in [Concepts](#concepts). This section intentionally focuses on stack authoring shape and examples.

## Remote resources

Stacksmith can pull scripts, config files, vars files, stack files, and runfiles from remote locations. Anywhere a local file path is accepted for validation scripts, transform scripts, vars files, stack files, config files, or `stacksmith.yaml`, a remote URL can be used instead.

Runfiles and config script references use a structured `source` + `data` object.

Supported sources are:

- `local` with `data.path`
- `git` with `data.repo`, `data.path`, optional `data.ref`
- `http` with `data.url`
- `registry` with `data.address`, `data.version`

Stacksmith treats this as the canonical representation and renders tool-specific syntax server-side before invoking downstream tools.

### Canonical vs rendered target syntax

| Canonical reference | OpenTofu rendered value | CLI flag rendered value |
| - | - | - |
| `source: local`, `data.path: ./vars.dev.yaml` | `./vars.dev.yaml` | `./vars.dev.yaml` |
| `source: http`, `data.url: https://example.com/base.yaml` | `https://example.com/base.yaml` | `https://example.com/base.yaml` |
| `source: git`, `data.repo: https://github.com/org/shared.git`, `data.path: vars/base.yaml`, `data.ref: v1.2.3` | `git::https://github.com/org/shared.git//vars/base.yaml?ref=v1.2.3` | `git+https://github.com/org/shared.git//vars/base.yaml@v1.2.3` |
| `source: registry`, `data.address: hashicorp/aws`, `data.version: ~> 6.0` | `{ source = "hashicorp/aws", version = "~> 6.0" }` (provider/module fields) | Not used for file-style CLI flags |

### Usage examples

In config validations/transforms, use a structured script reference.

```yaml
# stacksmith-config.yaml – remote managed input validation script
var_validations:
  bucket_name:
    script:
      source: http
      data:
        url: https://raw.githubusercontent.com/my-org/shared/main/validators/bucket.py
```

```yaml
# stacksmith-config.yaml – remote transform script from a git repo
module_mappings:
  aws_s3_bucket:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-s3.git
        ref: 3.2.1
    properties:
      acl:
        mapped_to: bucket_acl
        transform:
          script:
            source: git
            data:
              repo: https://github.com/my-org/shared.git
              path: transforms/acl.py
              ref: v2.0.0
```

Config files, vars files, stack files, and runfiles also support remote URLs via CLI flags (`--config`, `--vars`, `--stack`, `--runfile`) where URL strings are passed directly.

```shell
stacksmith plan \
  --config https://example.com/org-config.yaml \
  --vars git+https://github.com/org/defaults.git//env/base.yaml@v1.2.0 \
  --vars git+https://github.com/org/service-defaults.git//bucket-writer/dev.yaml@v3.4.1
```

```shell
stacksmith validate \
  --runfile git+https://github.com/org/platform-live.git//services/payments/stacksmith.yaml@main
```

### Caching

Stacksmith and Terragrunt now use two cache layers.

- Stacksmith cache stores Stacksmith-resolved remote references (for example config files, vars files, stack files, runfiles, and Python scripts referenced by validations/transforms) under `.cache/` inside the build output directory, or `.stacksmith/.cache/` when no build directory is set.
- Terragrunt CAS caches Terragrunt source fetching (modules/catalog/stack sources) and is enabled by default in Terragrunt `>= 1.1.0`.

Use `--no-cache` to force Stacksmith to re-fetch its own remote references. On runtime commands (`init`, `plan`, `apply`, `destroy`, and `run-all`), `--no-cache` also disables Terragrunt CAS for that invocation.

Use `--no-cas` when you only want to disable Terragrunt CAS without clearing the Stacksmith cache.

### Environment variable defaults

`STACKSMITH_CONFIG` and `STACKSMITH_VARS` can provide default config and vars references when the corresponding CLI flags are omitted.

`STACKSMITH_STACK` can provide a default stack file path when no positional stack argument is given.

`STACKSMITH_RUN_FILE` can provide a default runfile reference when `--runfile` is omitted. If it is not set, Stacksmith auto-loads `./stacksmith.yaml` when present.

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

When Stacksmith executes Terragrunt runtime commands, Stacksmith also forwards Git auth into the Terragrunt subprocess environment so CAS-backed Git fetches can reuse your configured credentials.

#### Environment variable fallbacks

When no matching `remote_auth` entry exists, stacksmith checks the following environment variables.

| Variable | Purpose |
| - | - |
| `STACKSMITH_HTTP_TOKEN` | Bearer token for HTTP(S) requests |
| `STACKSMITH_HTTP_USERNAME` / `STACKSMITH_HTTP_PASSWORD` | Basic auth for HTTP(S) |
| `STACKSMITH_GIT_TOKEN` | Token auth for git clone (HTTPS) |
| `STACKSMITH_GIT_SSH_KEY` | Path to SSH private key for git clone |
| `STACKSMITH_SSL_VERIFY` | Set to `false` to disable TLS verification |

> ℹ️ **Note:** Remote config files are fetched *before* the config is loaded, so `remote_auth` entries are not available for config-level URLs. Use environment variables for authentication when fetching remote configs.

## Runfile

A runfile, usually `stacksmith.yaml`, is a reproducible invocation file for Stacksmith itself. It solves the GitOps problem of recording exactly which stack layers, shared configs, vars files, and inline variables were used for a deployment-oriented command instead of relying on an ephemeral shell history entry.

This is useful when platform teams publish a shared repo of base stack layers and managed defaults while application teams add service-specific overlays on top.

In the following example, the runfile references two stack layers (one from a git repo and one local) and two vars layers (one from a git repo and one local) in a deterministic order. It also defines an inline `var` layer that sets some default values for the stack. There is no `configs` section in this example, as the runfile author chose to rely on the environment variable `STACKSMITH_CONFIG` for config layering (coming from, for example, a GitHub Actions repository variable).

```yaml
stacks:
  - source: git
    data:
      repo: https://github.com/org/platform-stacks.git
      path: base/payments/stack.yaml
      ref: v1.4.0
  - source: local
    data:
      path: ./stack.yaml

vars:
  - source: git
    data:
      repo: https://github.com/org/platform-config.git
      path: vars/common.yaml
      ref: v3.2.1
  - source: local
    data:
      path: ./vars.dev.yaml

var:
  replicas: 2
  feature_flags:
    canary: true
```

Layering rules are deterministic.

- `stacks` are applied first in order for single-stack commands.
- `configs` are applied first, and later CLI `--config` flags append after them.
- `vars` and `var` act as a base layer ahead of CLI `--vars` and `--var` entries.
- `var` values can use any YAML type, including objects, arrays, booleans, and numbers.
- `merge_mode` controls how layering is applied. `deep` is the default. `override` makes each later layer replace the previous value wholesale.

Regarding "deep merge":

- Dicts merge recursively.
- Lists append in order.
- Later scalar values replace earlier ones.
- Set-like model fields such as tags deduplicate when parsed into the final model.

For `run-all`, `stacks` can also be used as an explicit target list instead of directory discovery.

If `--runfile` is omitted, Stacksmith checks `STACKSMITH_RUN_FILE` and then auto-detects `./stacksmith.yaml` when present.

`--merge-mode` on the CLI always takes precedence over the runfile `merge_mode` value.

## GitHub Actions GitOps workflow

This repository hosts reusable workflows and opinionated wrapper templates.

- `.github/workflows/stacksmith-gitops-reusable.yml` executes one environment in `plan`, `apply`, or native `operation` mode.
- `.github/workflows/stacksmith-gitops-opinionated-reusable.yml` discovers environments and fans out to the single-environment reusable workflow.
- `examples/github-actions/stacksmith-plan.yml`, `examples/github-actions/stacksmith-apply.yml`, and `examples/github-actions/stacksmith-operation.yml` are trigger wrappers that call the opinionated reusable workflow using `uses`.
- `jenkins/Jenkinsfile` is the opinionated Jenkins GitOps wrapper and is best used as a Multibranch Pipeline.

The wrappers under `examples/` do not execute in this repository because they are outside `.github/workflows`.
In your own repository, you can either:

- call `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>` from your workflow, or
- use the example wrappers as reference for trigger configuration.

The opinionated reusable workflow discovers target environments and then calls `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-reusable.yml@<version>` for each selected environment.

The CI selector is named `command` in GitHub Actions and `COMMAND` in Jenkins. Callers using the former `operation` or `OPERATION` selector must update to the new name.

- `workflow_dispatch` can run all environments, or a comma-delimited subset with `environments`.
- Native operation mode requires `operation_name`, the stack-local name passed to `stacksmith operation run` in each selected environment. Set `STACKSMITH_FORCE_RERUN=1` in the CI environment to force replacement of the operation runner resource when its execution identity has not changed.
- `discovery_mode` selects how environments are discovered. Use `folders` for `environments/<env>/` directories, `flat-files` for root-level `stacksmith.<env>.yaml|yml|json` files, or `env-files` for the hybrid `environments/<env>.yaml` layout. The aliases `env` and `env-files` both map to the hybrid env-file discovery path.
- `STACKSMITH_GITOPS_ROOT` defaults to `.` and can be overridden per run with `gitops_root`.
- Changes under `<gitops_root>/common` and `<gitops_root>/manifests/common` fan out to all environments.
- Changes under `<gitops_root>/environments/<env>` and `<gitops_root>/manifests/environments/<env>` target only that environment.
- For push and pull request events, when changed files do not map to any discovered environment, the workflow selection result is empty and the no-op job runs.
- Manual `environments` entries must map to discovered environments or selection fails fast.

The wrappers pass reusable workflow inputs from repository variables when available.

- `STACKSMITH_GITOPS_ROOT` (default `.`)
- `STACKSMITH_DISCOVERY_MODE` (default `auto`; set to `flat-files` for root-level env files, or `env-files` for the hybrid `environments/<env>.yaml` layout)
- `STACKSMITH_WORKDIR` (default `.`)
- `STACKSMITH_ENV_FILE` (default `/dev/null`)
- `STACKSMITH_IMAGE_VERSION` (default `latest`)
- `STACKSMITH_VALIDATION_REPORT_FORMAT` (default `json`, plan template)
- `STACKSMITH_UPLOAD_ARTIFACTS` (default `true`, plan template)
- `STACKSMITH_FAIL_ON_CHANGES` (default `false`, plan template)
- `STACKSMITH_STRICT_VALIDATION_WARNINGS` (default `false`, plan template)
- `STACKSMITH_NO_CAS` (default `false`)
- `STACKSMITH_ARGS_JSON` (default `[]`; ordered JSON array of additional CLI arguments; the workflow rejects managed `--config` and `-c` overrides)
- `STACKSMITH_CONFIG_REF` (required for the workflow entrypoints; points to the platform-managed Stacksmith config)
- `NO_VALIDATE_BRANCH_AND_OPERATION` (default `false`; bypasses the default-branch/PR operation guard)
- `TG_AUTH_PROVIDER_CMD` (default empty)
- `TG_IAM_ASSUME_ROLE` (default empty)

Credential values are inherited into the reusable workflows with standard GitHub Actions `secrets: inherit`. The supported secret names are `STACKSMITH_GIT_TOKEN`, `STACKSMITH_GIT_SSH_KEY`, `STACKSMITH_HTTP_TOKEN`, `STACKSMITH_HTTP_USERNAME`, and `STACKSMITH_HTTP_PASSWORD`. Jenkins provides the same runtime variables through native Jenkins credential bindings configured by `STACKSMITH_CREDENTIALS_JSON`.

Both CI implementations reserve the command selector, operation name, runfiles, build directory, plan output, validation format, and apply approval flags because those values are part of the GitOps contract. Every other Stacksmith CLI option can be supplied, in order and without shell-quoting loss, through `STACKSMITH_ARGS_JSON`. For example:

```json
["--vars", "vars/common.yaml", "--var", "replicas=3", "--tag", "service", "--debug"]
```

The GitHub workflows expose this as their `stacksmith_args_json` input. JSON arrays are used so repeated options, argument order, and values containing whitespace are preserved exactly. The workflow also requires a platform-managed config reference via `config_ref` or `STACKSMITH_CONFIG_REF`, injects it as `--config <ref>` for every Stacksmith invocation, and rejects attempts to override the config through `stacksmith_args_json`.

The opinionated reusable workflow intentionally does not expose free-form extra CLI args for `plan` or `apply`. Execution behavior is defined by repository-controlled workflow configuration and variables.

If you vendored the Jenkins wrapper or workflow assets into a consumer repository, put the vendored files under a platform-owned path or submodule and protect them with a CODEOWNERS file in that consumer repository so ordinary contributors cannot change the enforcement entrypoints. For example:

```text
.github/workflows/stacksmith-gitops-reusable.yml @platform-team
.github/workflows/stacksmith-gitops-opinionated-reusable.yml @platform-team
jenkins/Jenkinsfile @platform-team
```

The same pattern applies to any copied GitHub Actions workflow files that should remain platform-controlled.

### Consumer quickstart

Call the opinionated reusable workflow from your repository using `uses:`. Keep triggers and approval policies local and delegate discovery + per-environment execution to the reusable workflow here.

Plan on PR/push/manual (minimal example):

```yaml
name: stacksmith-plan

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  workflow_dispatch: {}

jobs:
  run-plan:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: plan
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      environments: ${{ github.event.inputs.environments || '' }}
      discovery_mode: ${{ vars.STACKSMITH_DISCOVERY_MODE || 'auto' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

Apply on push/manual (minimal example):

```yaml
name: stacksmith-apply

on:
  push:
    branches: [main]
  workflow_dispatch: {}

jobs:
  run-apply:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: apply
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      environments: ${{ github.event.inputs.environments || '' }}
      discovery_mode: ${{ vars.STACKSMITH_DISCOVERY_MODE || 'auto' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

Run a native operation manually with this minimal example.

```yaml
name: stacksmith-operation

on:
  workflow_dispatch:
    inputs:
      operation_name:
        description: Stack-local native operation name.
        required: true
        type: string

jobs:
  run-operation:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: operation
      operation_name: ${{ inputs.operation_name }}
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

> ℹ️ **Tip:** Pin the `uses:` reference to a release tag for stable downstream usage.

The reusable workflow also supports the `folders` and `flat-files` discovery modes for repositories that prefer those layouts.

This example now also shows app deployment and native operation patterns alongside infrastructure stacks. The shared config can expose approved Terraform component types such as `helm_app` and `k8s_app`, plus approved operations for local commands and Jenkins builds.

## Native operations

Operations are config-owned imperative actions. Stacksmith compiles them into private first-party Terraform modules, so their execution identity and locking use the same configured OpenTofu backend as the stack.

The managed config fixes the runner details, including the local command argument vector or Jenkins job and credentials. A stack can only select an approved operation and supply declared inputs. Operation inputs support the same Jinja templates and native Terraform references as component properties, so an operation can consume an output such as `${module.app.release_name}`. Operations use the `manual` trigger by default; set `trigger: after_apply` in managed config to run them after a successful apply.

```yaml
# stacksmith-config.yaml
operations:
  deploy:
    runner: local
    trigger: after_apply
    command: [./bin/deploy]
    environment:
      APP_ENV: environment
      RELEASE_NAME: release_name
    inputs:
      environment:
        required: true
      release_name:
        required: true
```

```yaml
# stack.yaml
components:
  app:
    type: application

operations:
  deploy_app:
    use: deploy
    with:
      environment: "{{ inputs.environment }}"
      release_name: "${module.app.release_name}"
```

Run a manual operation by its stack-local name.

```shell
stacksmith operation run deploy_app --stack stack.yaml --config stacksmith-config.yaml
```

For a one-time definite dispatch without changing the stack definition, add `--force-rerun` or set `STACKSMITH_FORCE_RERUN=1`. This passes `-replace=module.<operation_module>.terraform_data.operation` to the underlying apply while retaining the operation module target.

```shell
stacksmith operation run deploy_app --force-rerun --stack stack.yaml --config stacksmith-config.yaml
```

Alternatively, change `rerun_token` in the stack definition when the rerun request should remain declarative and reviewable. `operation run` performs a targeted OpenTofu apply of that operation's private module. Operations with the `after_apply` trigger run in stack dependency order during `stacksmith apply` and `stacksmith run-all apply`; stack-local `depends_on` can order multiple operations within a stack.

The Jenkins and GitHub Actions GitOps entrypoints also support native operation mode. Provide the stack-local operation name through Jenkins `OPERATION_NAME` with `COMMAND=operation`, or through the reusable workflow's `operation_name` input with `command: operation`. Set `STACKSMITH_FORCE_RERUN=1` in Jenkins folder properties or GitHub repository variables for a definite dispatch. Native operations use the same environment discovery, runfile layering, credentials, branch protections, and deployment approvals as infrastructure applies.

In this pattern, the shared runfile references the platform and service stack layers first, then environment-specific vars and overlays are layered on top.

```yaml
merge_mode: deep
configs:
  - examples/gitops-repo/common/stacksmith.yaml
vars:
  - examples/gitops-repo/vars/vars.dev.yaml
```

For production use, add GitHub Environment protections and secrets per environment. The reusable workflow maps `apply` jobs to the matching GitHub Environment name so approvals and scoped credentials can gate deployment.

The opinionated workflow resolves `STACKSMITH_ENV_FILE` from repository variables and falls back to `/dev/null` so CI runs are deterministic and do not implicitly load repository `.env` values.

> ⚠️ **Warning:** Stacksmith's GitOps workflows execute a fresh generation and execution of `terragrunt apply --auto-approve` directly against the latest tip of the target branch, rather than applying a pre-saved static plan binary (e.g., `tfplan` / `tofuplan`) generated during the plan phase. This creates a risk of plan vs. apply drift under the following scenarios:
>
> - **Concurrent Merges:** If another PR is merged after your PR's plan runs but before it is applied, the apply run will execute with the latest configurations of the target branch, which may differ from the approved plan.
> - **External State Changes:** If resources are modified out-of-band in the cloud provider, the apply step will reflect those updates.
> - **Dynamic Configurations:** If you reference dynamic data sources or remote modules with moving targets (e.g., untagged Git references or floating version constraints), the resolved files might differ between plan and apply execution.
>
> To mitigate this risk:
>
> 1. Enforce linear history or require branches to be up-to-date before merging in your repository settings (via GitHub Branch Protection)
> 2. Ensure all remote resources, configurations, and provider mappings use **immutable version pins** (exact commits or tags) rather than moving refs (like `main` or `latest`).

## CLI reference

<!-- BEGIN GENERATED CLI REFERENCE -->
Single-stack commands default to `stack.yaml` in the current directory, with fallback to `stack.yml` then `stack.json`, when neither `--stack`, `STACKSMITH_STACK`, nor `stacksmith.yaml` supplies stack refs.

### `stacksmith`

```text
stacksmith [-h] [--version]
                  {validate,generate,run-all,init,plan,apply,destroy,operation,info,ci} ...
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
| `run-all` | Discover all stacks and run terragrunt run-all |
| `init` | Generate + terragrunt init |
| `plan` | Generate + terragrunt plan |
| `apply` | Generate + terragrunt apply |
| `destroy` | Generate + terragrunt destroy |
| `operation` | Run native operations approved by managed configuration |
| `info` | Show stacksmith inspection and diagnostics commands |
| `ci` | CI-focused validation and diagnostics commands |

### `stacksmith validate`

```text
stacksmith validate [-h] [--stack STACK] [--runfile RUNFILE]
                           [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS]
                           [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache]
                           [--no-cas] [--strict-validation-warnings]
                           [--use-local-modules | --no-local-modules]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
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
stacksmith generate [-h] [--stack STACK] [--runfile RUNFILE]
                           [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS]
                           [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache]
                           [--no-cas] [--strict-validation-warnings]
                           [--use-local-modules | --no-local-modules]
                           [--debug | -q]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith run-all`

```text
stacksmith run-all [-h] [--root ROOT] [--stack STACK]
                          [--runfile RUNFILE] [-c CONFIG]
                          [--env-file ENV_FILE] [--vars VARS_FILE]
                          [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache]
                          [--no-cas] [--strict-validation-warnings]
                          [--use-local-modules | --no-local-modules]
                          [--debug | -q] [--validation-report-format {json}]
                          [--destroy] [--save-plan-json SAVE_PLAN_JSON]
                          [--fail-on-changes] [--tag TAG]
                          [--tag-expr TAG_EXPR] [--include-tag INCLUDE_TAG]
                          [--exclude-tag EXCLUDE_TAG] [--clean]
                          [--auto-approve]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save rendered plan JSON to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--tag` | Select components by tag. Repeat to require multiple tags. Supported for run-all plan/apply/destroy. |
| `--tag-expr` | JMESPath expression used to select resource targets. Supported for run-all plan/apply/destroy. |
| `--include-tag` | Include stacks that have this tag. Repeatable. |
| `--exclude-tag` | Exclude stacks that have this tag. Repeatable. |
| `--clean` | Remove existing build output directory before generation |
| `--auto-approve` | Skip interactive approval for apply/destroy |

### `stacksmith init`

```text
stacksmith init [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                       [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                       [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas]
                       [--strict-validation-warnings] [--use-local-modules |
                       --no-local-modules] [--debug | -q]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith plan`

```text
stacksmith plan [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                       [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                       [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas]
                       [--strict-validation-warnings] [--use-local-modules |
                       --no-local-modules] [--debug | -q] [--destroy]
                       [--save-plan-json SAVE_PLAN_JSON] [--fail-on-changes]
                       [--tag TAG] [--tag-expr TAG_EXPR]
                       [--validation-report-format {json}]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save rendered plan JSON to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |

### `stacksmith apply`

```text
stacksmith apply [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                        [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                        [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                        [--log LOG] [--no-cache] [--no-cas]
                        [--strict-validation-warnings] [--use-local-modules |
                        --no-local-modules] [--debug | -q] [--tag TAG]
                        [--tag-expr TAG_EXPR] [--auto-approve]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
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

### `stacksmith destroy`

```text
stacksmith destroy [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                          [--env-file ENV_FILE] [--vars VARS_FILE]
                          [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache]
                          [--no-cas] [--strict-validation-warnings]
                          [--use-local-modules | --no-local-modules]
                          [--debug | -q] [--tag TAG] [--tag-expr TAG_EXPR]
                          [--auto-approve]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
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

### `stacksmith operation run`

```text
stacksmith operation run [-h] [--force-rerun] [--stack STACK]
                                [--runfile RUNFILE] [-c CONFIG]
                                [--env-file ENV_FILE] [--vars VARS_FILE]
                                [--var VARS] [--merge-mode {deep,override}]
                                [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas]
                                [--strict-validation-warnings]
                                [--use-local-modules | --no-local-modules]
                                [--debug | -q]
                                operation_name [stack_file]
```

| Argument | Description |
| - | - |
| `operation_name` | Stack-local operation name |
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info inspect`

```text
stacksmith info inspect [-h] [--format {table,json}] [--basic]
                               [--runfile RUNFILE] [-c CONFIG]
                               [--env-file ENV_FILE] [--vars VARS_FILE]
                               [--var VARS] [--merge-mode {deep,override}]
                               [--build-dir BUILD_DIR] [--log LOG]
                               [--no-cache] [--no-cas]
                               [--strict-validation-warnings]
                               [--use-local-modules | --no-local-modules]
                               [--debug | -q]
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
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info diagnose`

```text
stacksmith info diagnose [-h] [--stack STACK] [--format {table,json}]
                                [--runfile RUNFILE] [-c CONFIG]
                                [--env-file ENV_FILE] [--vars VARS_FILE]
                                [--var VARS] [--merge-mode {deep,override}]
                                [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas]
                                [--strict-validation-warnings]
                                [--use-local-modules | --no-local-modules]
                                [--debug | -q]
                                [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--format` | Output format for diagnostics. Choices: `table`, `json`. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generator, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.remote, stacksmith.runner, stacksmith.terragrunt, stacksmith.utils, stacksmith.validation, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info environments`

```text
stacksmith info environments [-h] [--gitops-root GITOPS_ROOT]
                                    [--discovery-mode {folders,flat-files,env-files,env,auto}]
                                    [--environments ENVIRONMENTS]
                                    [--event-name EVENT_NAME]
                                    [--changed-path CHANGED_PATH]
                                    [--base-ref BASE_REF] [--before BEFORE]
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
                              [--environments ENVIRONMENTS]
                              [--workflow-runfile WORKFLOW_RUNFILE]
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

<!-- END GENERATED CLI REFERENCE -->

### Targeted execution

`plan` already serves as the dry-run mode for targeted execution, so a separate target dry-run flag is not required.

Expression context includes `tags` (effective tag list), `tag` (boolean map by tag name), `component_name`, `component_type`, `stack_name`, and `stack_tags`.

Only dot-style tag access is supported for tag expressions, for example `tag.prod`. Bracket-style references such as `tag['prod']` are not accepted.

Examples are as follows.

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

Use `--validation-report-format json` to explicitly select the currently supported output format. The flag is retained so more formats can be added later.

Human-oriented logs, OpenTofu progress output, and diagnostics are written to stderr so stdout can be piped directly into tools like `jq`.

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

Exit behavior is as follows.

- Exit code is `1` when at least one validation result is `fail`.
- Exit code is `1` for warnings only when `--strict-validation-warnings` is set.

This direct pipeline works without extra filtering.

```shell
stacksmith plan stack.yaml --config ./stacksmith-config.yaml | jq '.status'

stacksmith plan stack.yaml --config ./stacksmith-config.yaml --validation-report-format json > validation-report.json
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

Use `info environments` to preview GitOps environment discovery and selection logic used by the opinionated reusable workflow.

`info environments --format json` and `info environments --format yaml` write machine-readable output to stdout.

`info environments --format table` writes human-readable output to stderr.

```shell
stacksmith info environments \
  --gitops-root examples/gitops-repo \
  --discovery-mode env-files \
  --event-name push \
  --changed-path examples/gitops-repo/environments/dev.yaml
```

Use `ci validate` to run CI-oriented preflight checks with a stable check-result contract.

The first release focuses on static checks such as discovery mode validity, runfile path resolution, env-file path, and validation report format. The output structure is designed to support additional CI checks later without changing the command shape.

```shell
stacksmith ci validate \
  --gitops-root examples/gitops-repo \
  --discovery-mode env-files \
  --workflow-runfile examples/gitops-repo/common/stacksmith.yaml \
  --workflow-env-file /dev/null \
  --workflow-validation-report-format json
```

## Monorepo orchestration

In a monorepo, stacksmith recursively discovers all `stack.yaml`/`stack.yml`/`stack.json` files from a root directory and builds a dependency graph from `depends_on` declarations.

### Inter-stack dependencies

When a stack declares `depends_on`, all OpenTofu outputs from the dependency are automatically passed as inputs. Stack authors never write output or input declarations; the wiring is inferred. If you need to reference a created item's attribute in another program, it is recommended you do so by using the API or CLI of the target system (e.g. AWS CLI) rather than OpenTofu outputs, as this creates a more explicit and decoupled contract between stacks.

For plan and apply stages, Terragrunt `mock_outputs` are used so that dependent stacks can be planned before dependencies have been applied. Define expected output shapes in the stack that *produces* them:

```yaml
# networking/vpc/stack.yaml
stack:
  name: vpc

mock_outputs:
  vpc_id: mock-vpc-id
  subnet_ids:
    - mock-subnet-1
    - mock-subnet-2

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

> ⚠️ **WARNING:** `TOFU_PROVIDER_SPEC` is a shared provider cache keyed by provider version, not by OpenTofu version. If you build or run images with multiple OpenTofu versions, pre-cached providers may not be compatible with an older runtime unless you explicitly pin and pre-cache every provider version needed by those tool versions.

### Pre-installing modules

Similarly to providers, you can pre-install OpenTofu modules into the image using the `TOFU_MODULE_SPEC` build arg. This is a colon-separated list of `source=version-or-ref` pairs that match the sources and exact versions or Git refs in your managed config.

```shell
poe build-image --build-args TOFU_MODULE_SPEC="https://github.com/org/terraform-aws-s3.git=v3.2.1:https://github.com/org/terraform-aws-ec2.git=v5.0.0"
```

When modules are vendored in the image, Stacksmith automatically rewrites module sources in the generated `stacksmith.tf.json` to point to the local vendored copies instead of remote URLs. This eliminates network fetches during `tofu init` and ensures immutable, reproducible builds.

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

The following recipe uses `yq` to extract module and provider specs from a managed config file and pass them directly to `poe build-image`. `TOFU_PROVIDER_SPEC` uses colon-separated (`:`) `source=version` items, while `TOFU_MODULE_SPEC` uses `source=version-or-ref` items. Provider version ranges that include commas, such as `>= 6.39, < 7.0`, are supported. Local module mappings are excluded because they are already filesystem paths rather than dependencies that OpenTofu can pre-fetch.

```shell
stacksmithConfigPath=<path to stacksmith-config.yaml>
poe build-image \
  --build-args \
    "TOFU_MODULE_SPEC=$(yq -r '
      .module_mappings
      | to_entries
      | map(
          (
            select(.value.source.source == "git")
            | .value.source.data.repo
              + ((.value.source.data.path | select(. != null) | "//" + .) // "")
              + "=" + .value.source.data.ref
          ),
          (
            select(.value.source.source == "registry")
            | .value.source.data.address + "=" + .value.source.data.version
          )
        )
      | join(":")
    ' "$stacksmithConfigPath")" \
    "TOFU_PROVIDER_SPEC=$(yq -r '
      .provider_mappings
      | to_entries
      | map("\(.value.source.data.address)=\(.value.source.data.version)")
      | join(":")
    ' "$stacksmithConfigPath")"
```

## Tips

- Using a monorepo and concerned about who can edit what? Use GitHub's [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) file to restrict write access to certain stack files while allowing broader read access. Similarly, the managed config can be locked down to a small team of platform engineers, while the validation policies themselves can be tightly controlled by a security team.
- Doing a lot of `get` calls on dictionaries in your validation scripts? Try using `jmespath` instead to query complex nested structures with ease. For example, `jmespath.search("components.*.properties.bucket", stack)` would return a list of all bucket properties across all components in the stack.
- Want to take existing resources into consideration for validation rules? Import `boto3` and use it to query AWS directly from your validation scripts. Just be mindful of latency implications.

## Roadmap

The roadmap is ordered roughly by expected impact. Reproducibility and deployment safety come first, followed by operability and developer-experience improvements.

### Reproducible source locking and offline execution

Add a `stacksmith lock` command that resolves every remote input into an immutable identity and records the result in a lockfile. The lockfile should include Git commit SHAs, HTTP content hashes, module and provider versions, tool versions and checksums, and hashes for remotely loaded validation, transform, and provider configuration scripts.

Runtime commands should support `--locked` to reject inputs that disagree with the lockfile and `--offline` to prohibit network access and require every locked artifact to be available locally. This would make a runfile reproducible across machines even when its authored references use mutable branches, tags, or HTTP URLs. The lock data should be deterministic so that teams can review and commit it alongside their runfiles.

### Reviewed plan bundles and exact-plan application

Allow `plan` and `run-all plan` to save an applyable plan bundle rather than only rendered plan JSON. A bundle should contain the binary OpenTofu plan, its human- and machine-readable JSON, the generated Stacksmith files, target selection, relevant tool versions, and digests of every stack, config, variable layer, and remote source used to create it.

Add an apply mode that accepts only such a bundle and verifies its digests before applying the saved binary plan. This would let CI planning, policy evaluation, human approval, and deployment happen in separate jobs without silently recalculating a different plan between review and apply. Bundles containing sensitive plan data must be clearly marked and stored with appropriately restricted permissions.

### Resolution provenance and effective configuration inspection

Add an `info explain` command that shows how a final input or component property was produced. Its output should identify each contributing vars file, environment variable, runfile value, and command-line override in precedence order, along with deep-merge decisions, templates, transforms, managed defaults, property renames, and automatic injection.

The command should support table and JSON output, direct queries such as `inputs.region` or `components.api.properties.instance_type`, and redaction of sensitive values. A related effective-configuration view could render the fully merged stack, managed config, and resolved inputs without running OpenTofu, making configuration reviews and CI diagnostics substantially easier.

### Secret-aware inputs and operation parameters

Complete the existing `secret` operation input metadata and extend the concept to ordinary Stacksmith inputs. Secret declarations should support environment-backed and file-backed values initially, with a pluggable interface for external secret managers later. Diagnostics, provenance output, validation errors, and normal logs must redact these values.

Where the OpenTofu and Terragrunt execution models permit it, secrets should be passed through the process environment or temporary permission-restricted files instead of being serialized into generated configuration. Stacksmith should warn when a workflow necessarily places a secret in a plan or state file, and secret changes should still be able to affect operation execution identity without exposing the original value.

### Dependency graph and execution previews

Expose the existing monorepo dependency graph through an `info graph` command with table, JSON, DOT, and Mermaid output. The view should include stack paths, dependencies, state keys, selected components, mock-output usage, build directories, and the computed plan/apply or destroy order.

Add a `--dry-run` option to `run-all` that performs discovery, filtering, validation, targeting, and command construction without invoking Terragrunt. This would let users verify broad tag expressions and dependency changes before starting a long or destructive operation.

### Dependency-aware parallel `run-all`

Add `--jobs N` to execute independent stacks concurrently while continuing to respect dependency order. The scheduler should release a stack only after all of its required predecessors have succeeded, reverse the graph correctly for destruction, and keep serial execution as the default.

Parallel mode should provide grouped or prefixed logs, deterministic result summaries, and explicit fail-fast and continue-on-error policies. Plan JSON and validation results must remain isolated per stack so parallel workers cannot overwrite one another's artifacts.

### Trusted execution controls for Python hooks

Add a trust policy for Python validation, transform, and provider configuration hooks, especially remotely fetched scripts. The policy should support allowed hosts, required content hashes or lockfile entries, and a CI mode that rejects unpinned executable code. An optional isolated subprocess runner could add timeouts, a restricted environment, captured output, and resource limits while preserving an explicitly enabled in-process mode for compatibility.

This work should share source verification with the lockfile rather than inventing a separate integrity mechanism. Documentation should make clear that managed Python hooks are executable code and define which repository owners are expected to approve them.

### Additional validation report formats

Add YAML and CSV output for validation reports while retaining JSON as the stable machine-oriented default. YAML should preserve the complete nested report structure, while CSV should use one row per validation outcome with consistent columns for stack, rule, status, message, and origin.

### Typer-based CLI

Consider migrating the CLI from `argparse` to `typer` after the command and option model has stabilized. The migration should preserve current environment-variable behavior, generated CLI documentation, reusable Python API boundaries, exit codes, and stdout-versus-stderr guarantees. Its main goals would be clearer command composition, shell completion, and more maintainable help text rather than changing runtime semantics.
