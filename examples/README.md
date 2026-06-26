# Examples

This folder contains a realistic multi-repo-style example for Stacksmith.

The example provisions a small EC2 writer workload and S3 buckets, then wires
IAM and bucket policy so the EC2 role can write objects securely.

## Folder layout

The example is intentionally split into repository-style directories.

```text
examples/
  github-actions/
    stacksmith-plan.yml
    stacksmith-apply.yml
  modules/
    README.md
    command_runner/
    helm_app/
    kubernetes_app/
  gitops-repo/
    README.md
    common/
      stacksmith.yaml
    environments/
      dev.yaml
      prod.yaml
    manifests/
      common/
        platform.stack.yaml
        service.stack.yaml
        .stacksmith/
          stacksmith.tf.json
          terragrunt.hcl.json
      environments/
        dev/
          app-config.yaml
          frontend-values.yaml
        prod/
          app-config.yaml
          frontend-values.yaml
    vars/
      vars.dev.yaml
      vars.prod.yaml
  stack-repo/
    bucket-and-ec2/
      stack.yaml
      vars.dev.yaml
      .stacksmith/
        stacksmith.tf.json
        terragrunt.hcl.json
    .stacksmith/
      bucket-and-ec2/
  shared-config-repo/
    stacksmith-config.yaml
    scripts/
      providers/
        aws_identity.py
        configure_aws_secondary_provider.py
      transforms/
      validations/
```

The `gitops-repo` example is the canonical GitOps sample. It uses the hybrid `env-files` discovery mode with `environments/<env>.yaml` files and a shared `common/stacksmith.yaml` runfile.

The same workflow also supports the other two discovery styles:

- `folders` for `environments/<env>/` directories
- `flat-files` for root-level `stacksmith.<env>.yaml` files

All GitOps examples use the shared module implementations under
`examples/modules` via module mappings in `examples/shared-config-repo/stacksmith-config.yaml`.

## GitOps discovery styles

```text
folders:
  environments/
    dev/
      stacksmith.yaml
    prod/
      stacksmith.yaml

flat-files:
  stacksmith.dev.yaml
  stacksmith.prod.yaml

env-files:
  common/
    stacksmith.yaml
  environments/
    dev.yaml
    prod.yaml
```

## Example GitHub Actions wrappers

This example also includes GitHub Actions wrapper workflow templates under `examples/github-actions`.
These wrappers are references that call reusable workflows from this repository using `uses`.

- `examples/github-actions/stacksmith-plan.yml` triggers on pull requests to `main`, pushes to `main`, and manual dispatch.
- `examples/github-actions/stacksmith-apply.yml` triggers on pushes to `main` and manual dispatch.
- Both templates call `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@main`.

You can call that reusable workflow directly from your own workflow and keep your trigger policy in your repository.

These files do not run in this repository because they are intentionally stored outside `.github/workflows`.

When adapting them, update path filters and defaults for your repository structure.

Use path filters that match your discovery layout.

- For `folders`, include `**/stacksmith.yaml` and `**/manifests/environments/**`.
- For `flat-files`, include `**/stacksmith.*.yaml`, `**/stacksmith.*.yml`, and `**/stacksmith.*.json`.
- For `env-files`, include `**/environments/*.yaml`, `**/environments/*.yml`, and `**/environments/*.json`.
- For shared changes that should fan out to all environments, include `**/common/**` and `**/manifests/common/**`.
- Include stack file edits when your env/runfiles reference stack manifests directly, for example `**/*.stack.yaml` and `**/*.stack.yml`.

The stack repo contains stack definitions and environment vars.

## Jenkins pipeline submodule

A reusable Jenkins pipeline is available in `jenkins/Jenkinsfile`.
It is designed to be consumed by vendoring this repository as a git submodule and exposes the same GitOps selection behavior as the GitHub Actions wrappers.

For consuming repositories, set `STACKSMITH_AGENT_LABEL` and use `withEnv` in your Pipeline step to pass the required GitOps variables.

Use `jenkins/Jenkinsfile` as the Jenkins GitOps entrypoint. Shared execution behavior lives in `jenkins/stacksmith-helpers.groovy`.

The shared config repo contains module mappings, transforms, and validations.

## Example stack

The stack file is [`stack-repo/bucket-and-ec2/stack.yaml`](stack-repo/bucket-and-ec2/stack.yaml).

The vars file is [`stack-repo/bucket-and-ec2/vars.dev.yaml`](stack-repo/bucket-and-ec2/vars.dev.yaml).

The stack has the following tags.

- `compute`
- `storage`
- `example`
- `prod`

The `app` component is tagged with `web`, so you can combine stack-level and component-level targeting with expressions like `contains(stack_tags, 'prod') && tag.web`.

## Shared config repo

The managed config file is [`shared-config-repo/stacksmith-config.yaml`](shared-config-repo/stacksmith-config.yaml).

The transform script directory is [`shared-config-repo/scripts/transforms`](shared-config-repo/scripts/transforms).

The validation script directory is [`shared-config-repo/scripts/validations`](shared-config-repo/scripts/validations).

The provider script directory is [`shared-config-repo/scripts/providers`](shared-config-repo/scripts/providers).

### Environment variables

The example scripts support these optional environment variables.

- `STACKSMITH_SSE`
- `STACKSMITH_FETCH_AWS_ACCOUNT_ID`
- `STACKSMITH_EXAMPLE_AWS_ACCOUNT`
- `AWS_PROFILE`
- `AWS_DEFAULT_PROFILE`

Useful CLI environment variables for this example.

- `STACKSMITH_CONFIG`
- `STACKSMITH_ROOT`
- `STACKSMITH_DEBUG`
- `STACKSMITH_ONLY_USE_LOCAL_MODULES`

Implementation details are in
[`shared-config-repo/scripts/transforms/transform_s3_write_policy.py`](shared-config-repo/scripts/transforms/transform_s3_write_policy.py).

## Prerequisites

Before running `plan`, `apply`, or `destroy`, make sure the following are set up.

- AWS credentials are available for your shell session.
- The secondary provider only skips `assume_role` when STS caller identity resolves to a root ARN. If identity lookup fails or is inconclusive, the example keeps `assume_role` enabled.
- Set `STACKSMITH_VAR_AWS_PROFILE`, `AWS_PROFILE`, or `AWS_DEFAULT_PROFILE` if you want deterministic profile selection for the root identity check.
- The secondary provider config lives in [`shared-config-repo/scripts/providers/configure_aws_secondary_provider.py`](shared-config-repo/scripts/providers/configure_aws_secondary_provider.py) and reuses [`shared-config-repo/scripts/providers/aws_identity.py`](shared-config-repo/scripts/providers/aws_identity.py) for root detection.
- Vars in [`stack-repo/bucket-and-ec2/vars.dev.yaml`](stack-repo/bucket-and-ec2/vars.dev.yaml) are adapted for your account, especially `subnet_id`.
- Account and region assumptions in [`shared-config-repo/stacksmith-config.yaml`](shared-config-repo/stacksmith-config.yaml) match your target environment.

## Common commands

Run these commands from the repository root.

```bash
STACK_FILE="examples/stack-repo/bucket-and-ec2/stack.yaml"
VARS_FILE="examples/stack-repo/bucket-and-ec2/vars.dev.yaml"
CONFIG_FILE="examples/shared-config-repo/stacksmith-config.yaml"
SHARED_VARS_FILE=<path-to-shared-vars-file>
```

Validate one stack.

```bash
stacksmith validate "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Generate OpenTofu and Terragrunt JSON.

```bash
stacksmith generate "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Run a plan with plan validations enabled.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

This example includes a warning policy for `t3.micro` EC2 plans, so plan output can contain a warning outcome while still exiting successfully by default.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    | jq '.summary'
```

Treat warnings as failures in strict mode.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    --strict-validation-warnings
```

Read the machine-readable report block with `jq`.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    | jq '.'
```

Write the machine-readable report as CSV for spreadsheet-friendly review.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    --validation-report-format csv \
    > plan-validation-report.csv
```

### CSV schema

The CSV output contains a `report` row and one `result` row per validation result. Result rows keep report-level columns empty to reduce duplication.

| Column | Description |
| - | - |
| `row_type` | Either `report` or `result`. |
| `command` | The CLI command that produced the report (e.g. `plan`, `validate`). |
| `report_status` | Overall report status: `pass`, `warn`, or `fail`. |
| `exit_code` | Numeric exit code emitted by the CLI process. |
| `strict_validation_warnings` | `true` if `--strict-validation-warnings` was used, else `false`. |
| `stack_count` | Number of stacks in a multi-stack run (usually set on `report` rows). |
| `summary_pass` | Count of passing validation results. |
| `summary_warn` | Count of warnings. |
| `summary_fail` | Count of failures. |
| `stack_name` | Stack name for the row (single-stack summary for `report`, per-rule stack for `result`). |
| `result_name` | Validation rule name (or `validate` for var/validate commands). Populated on `result` rows. |
| `result_status` | Result status for this rule: `pass`, `warn`, or `fail`. Populated on `result` rows. |
| `result_message` | Short human-readable summary for the result. Populated on `result` rows. |
| `result_detail_json` | JSON-encoded detail payload for the result, including the long plan/value text when present. Populated on `result` rows. |

Layer shared defaults from another repo ahead of stack-local values.

> Note: The CSV output format is subject to change; prefer `json` for stable machine-readable output. Consumers should treat CSV as an unstable contract when automating integrations.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$SHARED_VARS_FILE" \
    --vars "$VARS_FILE"
```

Run `run-all` using the stack repo root.

```bash
stacksmith run-all plan \
    --root examples/stack-repo \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Run targeted plan for web-tagged components only when the stack has the `prod`
tag.

```bash
stacksmith run-all plan \
    --root examples/stack-repo \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    --tag-expr "contains(stack_tags, 'prod') && tag.web"
```

## Additional commands and options

Initialize providers and backend for one stack.

```bash
stacksmith init "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Apply one stack.

```bash
stacksmith apply "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Destroy one stack.

```bash
stacksmith destroy "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE"
```

Inspect configured module mappings.

```bash
stacksmith info inspect \
    --config "$CONFIG_FILE"
```

Show cache and module diagnostics for one stack.

```bash
stacksmith info diagnose "$STACK_FILE" \
    --config "$CONFIG_FILE"
```

High-value flags for this example workflow.

- `--env-file path/to/.env`
- `--var key=value`
- `--build-dir path/to/build-dir`
- `--destroy` (for `stacksmith plan` and `stacksmith run-all plan`)
- `--strict-validation-warnings`
- `--use-local-modules`

## Generated output

For single-stack operations, generated files are written under `examples/stack-repo/bucket-and-ec2/.stacksmith`.

When using `run-all` commands, generated files are organized as `examples/stack-repo/.stacksmith/<stack-name>/`.

You can override the default build directory location with `--build-dir`.

Notable generated files include `stacksmith.tf.json` and `terragrunt.hcl.json`.

## Security posture demonstrated

This example includes plan validations for these controls.

- IMDSv2 required on EC2
- Warning when EC2 plans include `t3.micro`
- S3 public access settings checked in generated plans
- Bucket write permissions scoped to role principal and object paths

The stack inputs also configure the following controls on S3 components.

- S3 ownership controls
- Insecure transport deny policy attachment

It also generates restrictive SSH ingress rules from explicit CIDR input.
