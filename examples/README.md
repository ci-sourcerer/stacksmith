# Examples

This folder contains a realistic multi-repo-style example for Stacksmith.

The example provisions a small EC2 writer workload and S3 buckets, then wires IAM and bucket policy so the EC2 role can write objects securely.

The `gitops-repo` example is the canonical GitOps sample. It uses the hybrid `env-files` discovery mode with `environments/<env>.yaml` files and a shared `common/stacksmith.yaml` runfile.

The `gitops-simple-repo` example uses the same discovery layout to plan two credential-free Terraform `null_resource` instances across `dev` and `prod`.

The same workflow also supports the other two discovery styles.

- `folders` for `environments/<env>/` directories
- `flat-files` for root-level `stacksmith.<env>.yaml` files

All GitOps examples use the shared module implementations under `examples/modules` via module mappings in `examples/shared-config-repo/stacksmith-config.yaml`.

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

This example includes GitHub Actions wrapper workflow templates under `examples/github-actions`. The wrappers call reusable workflows from this repository using `uses`.

- [`github-actions/stacksmith-plan.yml`](github-actions/stacksmith-plan.yml) triggers on pull requests to `main`, pushes to `main`, and manual dispatch.
- [`github-actions/stacksmith-apply.yml`](github-actions/stacksmith-apply.yml) triggers on pushes to `main` and manual dispatch.
- [`github-actions/stacksmith-operation.yml`](github-actions/stacksmith-operation.yml) manually runs a stack-local native operation in the selected environments.
All templates call `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@main`. You can call that workflow directly and keep the trigger policy in your repository. These example files do not run here because they are intentionally stored outside `.github/workflows`.

When adapting the templates, update their defaults and choose path filters that match your discovery layout.

- For `folders`, include `**/stacksmith.yaml` and `**/manifests/environments/**`.
- For `flat-files`, include `**/stacksmith.*.yaml`, `**/stacksmith.*.yml`, and `**/stacksmith.*.json`.
- For `env-files`, include `**/environments/*.yaml`, `**/environments/*.yml`, and `**/environments/*.json`.
- For shared changes that should fan out to all environments, include `**/common/**` and `**/manifests/common/**`.
- Include stack file edits when your env/runfiles reference stack manifests directly, for example `**/*.stack.yaml` and `**/*.stack.yml`.

## Jenkins pipeline submodule

A reusable Jenkins pipeline is available in `Jenkinsfile`. It is designed to be consumed by vendoring this repository as a git submodule and exposes the same GitOps selection behavior as the GitHub Actions wrappers.

For consuming repositories, set `STACKSMITH_AGENT_LABEL` and use `withEnv` in your Pipeline step to pass the required GitOps variables.

Use `Jenkinsfile` as the Jenkins GitOps entrypoint. It mirrors the GitHub Actions workflow's discovery, execution flags, approval, and artifact behavior using Jenkins-native features.

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

The config demonstrates `default_module_mapping` with the convention `https://github.com/my-org/{{ component_type | replace("-", "_") }}`. Its explicit mappings keep every bundled component runnable and take precedence over this placeholder fallback.

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

Implementation details are in [`shared-config-repo/scripts/transforms/transform_s3_write_policy.py`](shared-config-repo/scripts/transforms/transform_s3_write_policy.py).

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

Run a plan with plan validations enabled and read its validation summary.

```shell
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    | jq '.summary'
```

This example includes a warning policy for `t3.micro` EC2 plans, so plan output can contain a warning outcome while still exiting successfully by default.

Treat warnings as failures in strict mode.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    --strict-validation-warnings
```

Write the machine-readable JSON report to a file.

```bash
stacksmith plan "$STACK_FILE" \
    --config "$CONFIG_FILE" \
    --vars "$VARS_FILE" \
    --validation-report-format json \
    > plan-validation-report.json
```

Layer shared defaults from another repo ahead of stack-local values.

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

Run a targeted plan for web-tagged components only when the stack has the `prod` tag.

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
