# Advanced authoring

Layer inputs, templates, remote resources, validations, transforms, and native operations for more sophisticated stacks.

## Inputs

Input resolution order from lowest to highest priority.

1. Vars files from `STACKSMITH_VARS`, when used without `--runfile`
2. Environment variables prefixed with `STACKSMITH_VAR_`
3. `stacksmith.yaml` `vars` sources, when a runfile is used
4. Explicit `--vars` and `--var key=value` entries, deep-merged in the order they appear on the command line

Runfile inline `vars` sources support Jinja in two stages. Stage 1 runs while the runfile itself is loaded and provides `runfile` metadata fields. Stage 2 runs during input resolution and provides `inputs` and `stack`.

The `runfile` metadata fields available during stage 1 are `runfile.path`, `runfile.dir`, `runfile.name`, and `runfile.stem`.

When Stacksmith renders a stack from a Git working tree with an `origin` remote, `git_repository` contains that remote URL. Stack rendering, input resolution, property transforms, and default module source templates resolve it from the stack file's directory, not Stacksmith's launch directory. Runfile stage 1 resolves it from the runfile directory. For direct input resolution without a stack, Stacksmith uses its current working directory. The variable is undefined when the relevant directory is not in a Git working tree or `origin` is not configured. For example, a stack can use `iac_repository: "{{ git_repository }}"` in an AWS resource tag.

When `--runfile` is used, Stacksmith applies runfile `vars` sources before CLI-provided variable layers and does not apply `STACKSMITH_VARS` defaults.

## Templating matrix

Stacksmith supports Jinja in specific surfaces rather than as a global feature.

| Surface | Render timing | Context | Notes |
| - | - | - | - |
| Stack source (`stack.yaml`, `stack.yml`, `stack.json`) | Before YAML or JSON parse and schema validation | `inputs`, `stack`, `components`, reserved stack-transform `output`, `git_repository` when available | Full-file render for structural generation. Component references and stack output transform values are preserved and bound during OpenTofu generation. |
| Resolved input values | After vars, env vars, runfile vars, and CLI vars are merged | `inputs`, `stack`, `git_repository` when available | Value-level render across merged inputs. |
| Runfile stage 1 (`stacksmith.yaml`) | During runfile load before schema validation | `runfile.path`, `runfile.dir`, `runfile.name`, `runfile.stem`, `git_repository` when available | Primarily for structured references and inline vars source data. |
| Runfile stage 2 (runfile inline vars after merge) | During input resolution | `inputs`, `stack`, `git_repository` when available | Lets runfile-provided values compose with final merged inputs and stack metadata. |
| `default_module_mapping.source` | During module mapping resolution when no explicit mapping exists | `component.type`, `component.name`, `env.git_repository` when available | Strict sandboxed render with post-render source validation. |
| Module property `default` values | During module input generation when the component omits the property | `property.name`, `property.kind`, `property.output_name`, `component.name`, `component.type`, `inputs`, `stack`, `components`, `env.git_repository` when available | Applies to explicit and default module mappings. Recursively renders configured defaults before transforms and validations; deferred component output references are preserved and bound afterward. |
| `properties.*.transform.jinja` | During input transform execution | `property.value` plus transform context (`property.name`, `property.kind`, `property.output_name`, `component.name`, `component.type`, `inputs`, `stack`, `env.git_repository` when available) | Adapts a resolved stack property into a module input. |
| `module_mappings.*.outputs.*.transform.jinja` | During component output binding | `output.value`, `output.name`, `output.module_output`, `component.name`, `component.type`, `stack`, `env.git_repository` when available | Adapts the unresolved module output reference into the public component output. |
| Stack `outputs.*.transform.jinja` | During root output generation | `output.value`, `output.name`, `stack`, `env.git_repository` when available | Safely adapts the exported value and its mock after component output binding. |

Other managed-config fields are intentionally non-templated.

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
    username_env: GITHUB_USERNAME
  gitlab.internal.com:
    type: basic
    username_env: GITLAB_USER
    password_env: GITLAB_PASS
  git.private.com:
    type: ssh
    ssh_key_path: /home/ci/.ssh/deploy_key
```

Supported auth types are `token` (HTTP Bearer or git token), `basic` (HTTP Basic), and `ssh` (Git SSH key).

When Stacksmith executes Terragrunt runtime commands, Stacksmith forwards HTTPS token auth through a temporary Git credential helper so CAS-backed and OpenTofu-initiated Git fetches can reuse your configured credentials. The helper reads tokens from the subprocess environment, contains no credential values itself, and is deleted when the subprocess exits. Token auth preserves a username supplied by Git configuration or the source URL, uses `username_env` when configured, and otherwise defaults the username to `git`.

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

## Validation and transforms

Stacksmith supports Python validation hooks and Python or Jinja transform hooks.

- Validations use either `inline` Python or `script`.
- Transforms use `inline`, `script`, or `jinja` depending on context.
- Relative script paths resolve from the declaring file.
- Validation and transform specifications accept an optional `description`, including entries in `var_validations`, managed module properties and outputs, and stack output transforms.

Machine-facing mapping keys remain stable identifiers, while `description` carries prose for people and inspection output. Optional descriptions are also supported on managed configurations, provider families and instances, module mappings and properties, operation definitions and inputs, merge rules, runfiles and their stack/config/variable references, stacks and their components, outputs, operation invocations, and test manifests.

## Plan validations

The [managed configuration](managed-configuration.md) can define `plan_validations` that run after `plan` and `run-all plan` against OpenTofu plan JSON output.

Plan validation rules can return `pass`, `warn`, or `fail` outcomes.

- Truthy values pass and falsey values fail.
- Warnings are non-blocking by default; use `--strict-validation-warnings` to treat warning outcomes as failures.
- Use `--fail-on-changes` on `plan` or `run-all plan` to return a non-zero exit code whenever the rendered plan contains *any* resource changes. This is useful for automated drift detection or CI checks where only a non-empty plan should fail.

## Native operations

Operations are config-owned imperative actions. Stacksmith compiles them into a separate runner-only Terraform root backed by `<stack-path>/operations/terraform.tfstate`. The infrastructure root never contains operation resources, and the operation root never contains infrastructure resources or providers.

Every approved public component output is exposed from the infrastructure root through the nested, sensitive `_stacksmith_operation_bridge` output, even before an operation references it. The bridge can therefore appear as one sensitive value under `Changes to Outputs` in an infrastructure plan, but it is not an operation resource and does not execute anything. It must live in the infrastructure state because that is where the component values are produced. Operations select individual values from the bridge through read-only remote state access. Predeclaring the bridge values means adding or changing an operation does not require an infrastructure apply merely to establish its dependency contract.

The managed config fixes the runner details, including the local command argument vector or Jenkins job and credentials. A stack can only select an approved operation and supply declared inputs. Operation inputs support the same Jinja templates and deferred public component outputs as component properties, so an operation can consume an output such as `{{ components.app.release_name }}`. Operations use the `manual` trigger by default; set `trigger: after_apply` in managed config to run them after a successful apply.

Local operation environments can automatically expose every declared operation input. Names are uppercased by default; overrides and exclusions are nested under `inputs` so they cannot collide with environment policy fields:

```yaml
environment:
  mode: auto
  inputs:
    overrides:
      KUBE_CONTEXT: kubeconfig_context
    exclude:
      - secret_value
```

OpenTofu suppresses all `local-exec` output when its command or environment contains a sensitive value. To support operation-level output control, Stacksmith declassifies the runner specification only at the local process boundary while keeping it sensitive in plans, then discards the child process's standard output and standard error by default. Set `stream_output: true` on a managed local operation to inherit those streams through OpenTofu.

Use `output_masking` to define literal redaction rules for streamed local operation output. `output_masking.literals` masks fixed literal strings, and `output_masking.inputs` masks resolved values for selected operation inputs. When streaming is enabled, every secret input must be listed in `output_masking.inputs`.

```yaml
# stacksmith-config.yaml
module_mappings:
  application:
    source:
      source: registry
      data:
        address: example/application
        version: "1.0.0"
    outputs:
      release_name:
        description: Deployed application release name.

operations:
  deploy:
    description: Deploy an approved application release.
    runner: local
    trigger: after_apply
    stream_output: true
    command: [./bin/deploy]
    output_masking:
      literals:
        - DO-NOT-LEAK
      inputs:
        - release_name
    environment:
      APP_ENV: environment
      RELEASE_NAME: release_name
    inputs:
      environment:
        description: Deployment environment.
        required: true
      release_name:
        description: Immutable application release identifier.
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
      release_name: "{{ components.app.release_name }}"
```

Dry-run a manual operation by its stack-local name before executing it.

```shell
stacksmith operation plan deploy_app --stack stack.yaml --config stacksmith-config.yaml
```

Omit operation names to dry-run every operation declared by the stack. The same omission for `operation run` executes all declared operations.

```shell
stacksmith operation plan --stack stack.yaml --config stacksmith-config.yaml
```

Infrastructure plans use `--after-apply` to restrict the operation preview to operations configured with the `after_apply` trigger. The Jenkins and GitHub Actions GitOps pipelines set this automatically for `plan` and `apply` commands.

```shell
stacksmith operation plan --after-apply --stack stack.yaml --config stacksmith-config.yaml
```

Run the operation after reviewing the plan.

```shell
stacksmith operation run deploy_app --stack stack.yaml --config stacksmith-config.yaml
```

Select multiple operations with a comma-delimited list. Stacksmith includes transitive `depends_on` operations automatically and generates a root containing only that batch. Independent operations run concurrently, while dependency edges preserve ordering. Set `STACKSMITH_MAX_PARALLEL_OPERATIONS` to cap concurrency; it defaults to `10` and is intentionally not a command-line or CI parameter.

```shell
STACKSMITH_MAX_PARALLEL_OPERATIONS=3 \
stacksmith operation run publish_image,deploy_app,smoke_test \
  --stack stack.yaml \
  --config stacksmith-config.yaml
```

For a one-time definite dispatch without changing the stack definition, add `--force-rerun` or set `STACKSMITH_FORCE_RERUN=1`. This marks each explicitly selected operation resource for replacement in the operation plan.

```shell
stacksmith operation run deploy_app --force-rerun --stack stack.yaml --config stacksmith-config.yaml
```

Alternatively, change `rerun_token` in the stack definition when the rerun request should remain declarative and reviewable. `operation plan` creates a saved operation-only OpenTofu plan without invoking provisioners. `operation run` validates that the plan contains no managed changes outside `module.stacksmith_operation_*` and applies that exact saved plan. Operations with the `after_apply` trigger run in stack dependency order during `stacksmith apply` and `stacksmith run-all apply`; Stacksmith replans them after infrastructure succeeds so component outputs are current. Stack-local `depends_on` can order multiple operations within a stack. Jenkins runners poll the queued build through completion, so a dependent operation starts only after its Jenkins prerequisite succeeds. Managed Jenkins definitions can set `poll_interval_seconds` and `timeout_seconds`, which default to 5 and 3600.

Preview removal of the complete isolated operation state without running operation provisioners, then destroy it after review.

```shell
stacksmith operation plan --destroy --stack stack.yaml --config stacksmith-config.yaml
stacksmith operation destroy --stack stack.yaml --config stacksmith-config.yaml
```

`operation destroy` generates and validates a fresh destroy plan, prompts for confirmation, and applies that exact saved plan. Add `--auto-approve` only in an already approved automation context. Operation names, `--after-apply`, and `--force-rerun` are rejected in destroy mode because cleanup removes the complete isolated state rather than dispatching selected operations.

### Declarative application deployments through Jenkins

An approved Jenkins operation can deploy application code while the GitOps repository records exactly what should be deployed. Keep the Jenkins URL, job, credential variable names, and allowed parameter mapping in managed configuration. Set `trigger: after_apply` explicitly because operations remain manual by default.

```yaml
# stacksmith-config.yaml
operations:
  deploy_application:
    runner: jenkins
    trigger: after_apply
    url: https://jenkins.example.com
    job_name: deployments/application
    username_env: STACKSMITH_JENKINS_USERNAME
    api_token_env: STACKSMITH_JENKINS_API_TOKEN
    parameters:
      ENVIRONMENT: environment
      GIT_COMMIT: commit_id
    inputs:
      environment:
        description: Deployment environment.
        required: true
      commit_id:
        description: Immutable application Git commit to deploy.
        required: true
```

The stack selects that approved operation and binds its parameters to declarative inputs.

```yaml
# application.stack.yaml
operations:
  deploy_application:
    use: deploy_application
    with:
      environment: "{{ inputs.environment }}"
      commit_id: "{{ inputs.application_commit }}"
```

Each environment layer pins the desired application revision.

```yaml
# environments/prod.yaml or its referenced vars file
vars:
  - source: inline
    data:
      environment: prod
      application_commit: "8c9f20bd0cbf2c70f7f728f4e92bf6ad239a45b1"
```

On a merge to the default branch, the normal apply workflow reconciles the operation in its isolated state after infrastructure succeeds. Changing `application_commit`, the approved Jenkins definition, a component output, or another bound parameter changes the operation specification, so OpenTofu replaces the operation resource and Stacksmith starts the Jenkins build. An unchanged specification is a no-op. The runner passes `GIT_COMMIT` to Jenkins, waits for the queued build to finish, and fails the operation phase if Jenkins does not report success. Jenkins credentials stay in the CI environment and never enter the application manifest.

## Testing policies and transforms

Stacksmith tests are declared in `tests.yaml` manifests. `stacksmith test` compiles those manifests into an ephemeral pytest module and runs it with Stacksmith's managed-config fixture wiring, cache behavior, and layered merge behavior.

When `--config` points at one or more managed config layers, Stacksmith discovers `tests.yaml` beside each selected config and merges them in order. You can also pass explicit manifest paths after Stacksmith options.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-base-config.yaml \
  --config examples/shared-config-repo/stacksmith-config.yaml
```

```shell
stacksmith test \
  --config platform/base-config.yaml \
  --config platform/prod-config.yaml \
  platform/tests.yaml \
  -- -k imdsv2
```

Use `--dump-tests` when you want to inspect the generated pytest code.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-base-config.yaml \
  --config examples/shared-config-repo/stacksmith-config.yaml \
  --dump-tests /tmp/stacksmith-generated-tests.py
```

Manifest test cases cover variable policies, plan policies, and component properties. Plan policy cases can include optional context (for example stack metadata), and manifests can define optional setup/teardown fixtures using either inline Python or script references. Fixture execution mode can be set to `per-suite` (default) or `per-test-case`.

Generated test names remain unique when case or policy names normalize to the same Python identifier. Policy exceptions, missing scripts, and invalid return values fail tests even when a case expects `fail`. Use `message_contains` on variable and plan cases to check a literal substring in the returned diagnostic; outcome assertion failures also display that diagnostic.

Fixture scripts support local, HTTP, and Git references. Local paths resolve relative to their declaring manifest, and remote scripts use the managed configuration's authentication and the test command's cache.

The test command reports named variable and plan policies without cases in the merged manifest, including disabled policies. This is an informational inventory of declared cases, not line coverage or a count of tests selected by pytest filters.

```yaml
description: Policy and transform tests for the production platform configuration.

fixtures:
  mode: per-test-case
  setup:
    inline: |
      fixture_state["ready"] = True

var_validations:
  aws_region:
    - value: us-east-1
      expect: pass

plan_validations:
  ec2_t3_micro_warning:
    - resources:
        - type: aws_instance
          after:
            instance_type: t3.micro
      context:
        stack_name: production
      expect: warn

component_properties:
  aws_s3_bucket:
    bucket_name:
      - value: My_Bucket
        inputs:
          environment: prod
        expect:
          output_name: bucket
          value: prod-my-bucket
```

Each `resources` item requires `type`; Stacksmith supplies `address: <type>.this` and `change.actions: [create]` by default. Set `address` or `actions` explicitly for address-sensitive policies, multiple resources of the same type, deletes, replacements, or other non-default plan behavior. Use `plan` instead of `resources` when a test requires exact OpenTofu plan JSON.

Property cases also accept `component_name` (default `test-component`), `stack`, and `git_repository`. These values use the same context structure as production transforms and validations, alongside `inputs`.

For a property with a configured validation, use `expect: fail` to require an explicit rejection. An unexpected successful result, a transform error, or a broken validation fails the test. Property `message_contains` is available only with `expect: fail`.

```yaml
component_properties:
  storage:
    access:
      - name: Rejects public access
        value: public
        component_name: audit-logs
        stack:
          name: production
        git_repository: https://example.com/platform.git
        expect: fail
        message_contains: Private access is required
```

## Local path resolution

- Local paths in `stacksmith.yaml` runfile `stacks`, `configs`, and local `vars` sources resolve relative to the runfile that declares them.
- Local script paths and local module source paths in `stacksmith-config.yaml` resolve relative to the config file that declares them.
