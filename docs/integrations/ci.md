# CI and GitOps

Use the shared GitHub Actions and Jenkins integrations to discover environments, prepare versioned manifests, and execute guarded lifecycle phases.

Stacksmith provides equivalent opinionated GitOps entrypoints for GitHub Actions and Jenkins. Both use the same provider-neutral CI manifest and the `stacksmith ci prepare` / `stacksmith ci execute` contract, so environment selection and per-environment execution stay consistent across providers.

- [`.github/workflows/stacksmith-gitops-reusable.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/.github/workflows/stacksmith-gitops-reusable.yml) executes one lifecycle phase for one environment from a versioned CI manifest.
- [`.github/workflows/stacksmith-gitops-opinionated-reusable.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/.github/workflows/stacksmith-gitops-opinionated-reusable.yml) discovers environments and fans out to the single-environment reusable workflow.
- [`examples/github-actions/stacksmith-plan.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-plan.yml), [`examples/github-actions/stacksmith-apply.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-apply.yml), [`examples/github-actions/stacksmith-destroy.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-destroy.yml), [`examples/github-actions/stacksmith-plan-operation.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-plan-operation.yml), and [`examples/github-actions/stacksmith-operation.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-operation.yml) are trigger wrappers that call the opinionated reusable workflow using `uses`.
- [`jenkins/vars/stacksmith.groovy`](https://github.com/ci-sourcerer/stacksmith/blob/main/jenkins/vars/stacksmith.groovy) provides the opinionated Jenkins GitOps pipeline as the `stacksmith()` entrypoint of a trusted shared library.

The GitHub templates under `examples/` do not execute in this repository because they are outside `.github/workflows`.

## Shared behavior

The opinionated reusable workflow prepares one provider-neutral manifest, discovers target environments, and then calls `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-reusable.yml@<version>` for each selected environment. The GitHub wrappers do this through `stacksmith ci prepare-from-env` and `stacksmith ci execute-from-env`. The Jenkins shared-library entrypoint uses the same adapter commands, so both providers converge on the same manifest and execution contract implemented by `stacksmith ci prepare` and `stacksmith ci execute`. A CI manifest prepared with command `test` supports only the `test` phase, which invokes `stacksmith test` with the manifest's managed config, selected environment, runfiles, variables, env file, working directory, and additional arguments. Plan and apply requests execute both the infrastructure `plan` phase and a `plan-operation` phase for operations selected by `after_apply`; manual operations are excluded. Apply waits for both previews, applies infrastructure after provider approval, then replans and reconciles the isolated operation state so operation inputs see current outputs. A `plan-operation` request previews an explicitly selected batch, or all operations when names are omitted, without approval or execution. An `apply-operation` request performs the same preview, waits for provider approval, and then executes the batch.

A destroy request first previews infrastructure with `plan --destroy` and previews removal of the complete isolated operation state. After approval, Stacksmith destroys the operation state before infrastructure, preventing stale operation resources from surviving their stack. A failed preview or operation-state cleanup prevents infrastructure destruction. Destroy manifests reject operation-name selection and forced reruns, and the shared policy rejects destructive execution on pull requests or non-default branches. GitHub runs the destructive phases through the selected protected environment; Jenkins uses its explicit `Approve` input. The single-environment workflow is therefore an internal execution primitive; call the opinionated workflow unless you intentionally generate and supply a manifest yourself.

For deployment commands, `stacksmith ci prepare` resolves the effective layered configuration for every selected environment and rejects `backend.type: local`. These CI runs must use a remote backend so state is durable and shared across lifecycle jobs. A standalone `test` command skips backend validation because `stacksmith test` does not read or write infrastructure state.

The deployment CI selector is named `command` in GitHub Actions and `COMMAND` in stack-running Jenkins jobs. Callers using the former `operation` or `OPERATION` selector must update to the new name.

- GitHub Actions `workflow_dispatch` can run all environments, or a comma-delimited subset with `environments`.
- `plan-operation` previews native operations without approval or execution; `apply-operation` previews, requests approval, and executes them. Supply a comma-delimited `operation_names` input such as `publish_image,deploy_app,smoke_test`, or leave it empty to select all operations in each stack. Set the repository, organization, job, or folder variable `STACKSMITH_MAX_PARALLEL_OPERATIONS` to cap concurrency and `STACKSMITH_FORCE_RERUN=1` to force replacement of selected operation runner resources when their execution identities have not changed.
- `discovery_mode` selects how environments are discovered. Use `folders` for `environments/<env>/` directories, `flat-files` for root-level `stacksmith.<env>.yaml|yml|json` files, or `env-files` for the hybrid `environments/<env>.yaml` layout. The aliases `env` and `env-files` both map to the hybrid env-file discovery path.
- In GitHub Actions, `STACKSMITH_GITOPS_ROOT` defaults to `.` and can be overridden per run with `gitops_root`.
- Changes under `<gitops_root>/common` and `<gitops_root>/manifests/common` fan out to all environments.
- Changes under `<gitops_root>/environments/<env>` and `<gitops_root>/manifests/environments/<env>` target only that environment.
- For push events, any changed path that does not map to a discovered environment conservatively selects all environments. Pull requests retain targeted selection and produce a no-op when no changed path maps to an environment.
- Manual `environments` entries must map to discovered environments or selection fails fast.

Both implementations reserve the command selector, operation selection, operation concurrency, runfiles, build directory, plan output, validation format, and apply approval flags because those values are part of the GitOps contract. Every other Stacksmith CLI option can be supplied, in order and without shell-quoting loss, through `STACKSMITH_ARGS_JSON`. For example:

```json
["--vars", "vars/common.yaml", "--var", "replicas=3", "--tag", "service"]
```

Set the reusable workflow's `debug` input, the Jenkins `DEBUG` parameter, or `STACKSMITH_DEBUG=1` to enable debug logging. Debug CI executions also run `stacksmith info modules-and-policies` with the selected environment's managed config and layered runfiles before the requested command.

The `--log` category filter matches logger names exactly; parent names do not automatically include submodules. For example, use `--log stacksmith.ci.service=DEBUG` for logs emitted by `stacksmith.ci.service`, rather than `--log stacksmith.ci=DEBUG`.

Plan, apply, and destroy executions read Stacksmith's source-locking controls exclusively from repository-, organization-, job-, or folder-managed environment settings. Set `STACKSMITH_REQUIRE_LOCKFILE` to require resolved inputs to match a lockfile, optionally set `STACKSMITH_LOCKFILE` to choose a non-default path, and combine `STACKSMITH_OFFLINE` with locked mode to prohibit network resolution. Native operations do not currently accept Stacksmith lock-policy flags.

Plan artifacts produced by the managed GitHub Actions and Jenkins entrypoints are redacted in memory before Stacksmith writes them. Ordinary previews use `plan.json`; destroy previews use the distinct `destroy-plan.json` filename and `stacksmith-destroy-plan-<environment>-<sha>` GitHub artifact name. Both providers attempt to retain the redacted preview and validation report even when the plan phase fails. The archive profile replaces schema-marked sensitive values with `<sensitive>` and omits input variables, configuration expressions, check problem messages, import details, generated configuration, replacement paths, and unrecognized fields because those locations do not consistently carry sensitivity metadata. The resulting JSON is intended for review and diagnostics, not as a complete substitute for raw `tofu show -json` output.

Use `stacksmith ci redact-plan <plan.json> --output <redacted-plan.json>` to sanitize an existing raw plan, or pass `--in-place` to atomically replace it. Keep the input file protected until redaction finishes.

Protect each consumer repository's CI entrypoint with a CODEOWNERS file so ordinary contributors cannot replace or bypass it. GitHub Actions callers can pin the reusable workflow to a release tag. Jenkins consumers keep only a protected call to the centrally managed trusted library in their repository.

## GitHub Actions

In your own repository, you can do either of the following.

- call `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>` from your workflow, or
- use the example wrappers as reference for trigger configuration.

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
- `STACKSMITH_DEBUG` (default `false`; enables debug logging and the modules-and-policies diagnostic)
- `STACKSMITH_NO_CAS` (default `false`)
- `STACKSMITH_REQUIRE_LOCKFILE` (default `false`; passes `--locked` to plan, apply, and destroy)
- `STACKSMITH_OFFLINE` (default `false`; passes `--offline` to plan, apply, and destroy and requires locked mode)
- `STACKSMITH_LOCKFILE` (default empty; optional explicit lockfile path)
- `STACKSMITH_ARGS_JSON` (default `[]`; ordered JSON array of additional CLI arguments; the workflow rejects managed config and lock-policy overrides)
- `STACKSMITH_CONFIG_REF` (required for the workflow entrypoints; points to the platform-managed Stacksmith config)
- `NO_VALIDATE_BRANCH_AND_OPERATION` (default `false`; bypasses the default-branch/PR operation guard)
- `TG_AUTH_PROVIDER_CMD` (default empty)
- `TG_IAM_ASSUME_ROLE` (default empty)

Credential values are inherited into the reusable workflows with standard GitHub Actions `secrets: inherit`. The supported secret names are `STACKSMITH_GIT_TOKEN`, `STACKSMITH_GIT_SSH_KEY`, `STACKSMITH_HTTP_TOKEN`, `STACKSMITH_HTTP_USERNAME`, `STACKSMITH_HTTP_PASSWORD`, `STACKSMITH_JENKINS_USERNAME`, and `STACKSMITH_JENKINS_API_TOKEN`.

The GitHub workflows expose this as their `stacksmith_args_json` input. JSON arrays are used so repeated options, argument order, and values containing whitespace are preserved exactly. The workflow requires the platform-managed config reference in `STACKSMITH_CONFIG_REF`, injects it as `--config <ref>` for every Stacksmith invocation, and rejects attempts to override the managed config or lock policy through `stacksmith_args_json`.

The opinionated reusable workflow exposes `debug` as an optional input. `STACKSMITH_CONFIG_REF`, `STACKSMITH_REQUIRE_LOCKFILE`, `STACKSMITH_OFFLINE`, and `STACKSMITH_LOCKFILE` are intentionally unavailable as workflow inputs so callers cannot override organization or repository policy per run.

### Consumer quickstart

Call the opinionated reusable workflow from your repository using `uses:`. Keep triggers and approval policies local and delegate discovery + per-environment execution to the reusable workflow here.

Plan on PR/manual (minimal example).

```yaml
name: stacksmith-plan

on:
  pull_request:
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

Apply on push/manual (minimal example).

```yaml
name: stacksmith-apply

on:
  push: {}
  workflow_dispatch: {}

jobs:
  run-apply:
    if: ${{ github.event_name == 'workflow_dispatch' || github.ref_name == github.event.repository.default_branch }}
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: apply
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      environments: ${{ github.event.inputs.environments || '' }}
      discovery_mode: ${{ vars.STACKSMITH_DISCOVERY_MODE || 'auto' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

The apply wrapper observes every push so repository-specific path conventions cannot prevent reconciliation from starting. The job gate runs automatic applies only for the repository's current default branch. Once started, Stacksmith's changed-path discovery narrows execution to affected environments; if any push path is unrecognized, it conservatively reconciles every environment.

Keep destruction manual and require the operator to name the target environments. The complete template is [`examples/github-actions/stacksmith-destroy.yml`](https://github.com/ci-sourcerer/stacksmith/blob/main/examples/github-actions/stacksmith-destroy.yml).

```yaml
name: stacksmith-destroy

on:
  workflow_dispatch:
    inputs:
      environments:
        description: Comma-separated environment names to destroy.
        required: true
        type: string

jobs:
  destroy:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: destroy
      environments: ${{ inputs.environments }}
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

Dispatch the workflow from the repository's default branch. Configure GitHub environment protection for every destroyable environment so operation-state cleanup and infrastructure destruction receive the required reviewers.

Run a native operation manually with this minimal example.

```yaml
name: stacksmith-operation

on:
  workflow_dispatch:
    inputs:
      operation_names:
        description: Comma-delimited stack-local native operation names. Leave empty to run all.
        required: false
        type: string

jobs:
  run-operation:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: apply-operation
      operation_names: ${{ inputs.operation_names || '' }}
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

> ℹ️ **Tip:** Pin the `uses:` reference to a release tag for stable downstream usage.

The reusable workflow also supports the `folders` and `flat-files` discovery modes for repositories that prefer those layouts.

## Jenkins

Configure this repository as a trusted global Pipeline library named `stacksmith`, with `jenkins` as its library path and a release tag as its default version. The trusted [`stacksmith.groovy`](https://github.com/ci-sourcerer/stacksmith/blob/main/jenkins/vars/stacksmith.groovy) global variable owns the complete pipeline implementation.

Each consuming repository needs only this `Jenkinsfile`.

```groovy
@Library('stacksmith') _

stacksmith()
```

Configure a Jenkins Multibranch Pipeline with that file as its pipeline script path. Protect it from unapproved changes with the repository's CODEOWNERS file, replacing the example team with the platform team that owns the pipeline.

```text
/Jenkinsfile @my-org/platform
```

The entrypoint supports two distinct pipeline modes. Set the job or folder environment variable `STACKSMITH_TEST_PIPELINE` to a truthy value for a test-only pipeline. That mode always prepares a test manifest and runs only `stacksmith test` in the `Test` stage; it tests the managed Stacksmith configuration rather than Stacksmith's own Python unit-test suite. When the variable is unset or false, the job is a stack-running pipeline with no test command. Plan jobs run in the `Plan` stage. Apply jobs preview infrastructure and `after_apply` operations, request approval, apply infrastructure, and then reconcile operations. Explicit operation batches run through `Plan operation(s)`, `Approve`, and `Run operation(s)`, so an invalid operation plan cannot reach approval or execution. Destroy jobs preview infrastructure and operation-state removal, request approval, run `Destroy operation state`, and reach `Destroy` only when cleanup succeeds. Both modes check out the branch, prepare their CI manifest once, run each selected environment in parallel, and map Jenkins-native context including `CHANGE_ID`, `CHANGE_TARGET`, `GIT_PREVIOUS_COMMIT`, `GIT_COMMIT`, and `BRANCH_NAME` to the shared adapter inputs automatically. Redacted infrastructure plan JSON and validation reports are archived when artifact uploads are enabled.

When the Folder Properties plugin provides `withFolderProperties`, `stacksmith()` loads those properties around the complete pipeline. Otherwise, it uses the job environment directly.

Choose one execution mode through Jenkins folder properties or the job environment.

- Set `STACKSMITH_USE_K8S` to a truthy value to run in a Kubernetes-plugin pod.
- Set `STACKSMITH_NODE_LABEL` to run directly on that labeled agent.
- Otherwise, the pipeline runs in a Docker container on any available agent, or on `STACKSMITH_DOCKER_NODE` when set.

Both Jenkins pipeline modes expose these parameters.

- `ENVIRONMENTS`: Optional comma-separated list of environment names.
- `WORKDIR`: Working directory for Stacksmith commands. Defaults to `.`.
- `DEBUG`: Enable debug logging and print configured modules and policies before execution. Defaults to `false`.

Stack-running pipelines additionally expose these parameters.

- `COMMAND`: `plan`, `apply`, `destroy`, `plan-operation`, or `apply-operation`. Defaults to `plan`.
- `OPERATION_NAMES`: Comma-delimited operation names for a dependency-aware batch. Leave empty to select all operations for `plan-operation` or `apply-operation`.
- `FAIL_ON_CHANGES`: Fail a plan containing resource changes. Defaults to `false`.
- `STRICT_VALIDATION_WARNINGS`: Treat plan validation warnings as failures. Defaults to `false`.

Configure these values as Jenkins folder properties or job environment variables when needed:

- `STACKSMITH_IMAGE`: Full image for Kubernetes and Docker modes. When unset, the image is `docker.io/cisourcerer/stacksmith:<STACKSMITH_IMAGE_VERSION>`.
- `STACKSMITH_IMAGE_VERSION`: Image tag when `STACKSMITH_IMAGE` is unset. Defaults to `latest`.
- `STACKSMITH_TEST_PIPELINE`: Set to a truthy value to make the job a test-only pipeline. The `COMMAND` parameter is not exposed in this mode and cannot override the test command.
- `STACKSMITH_GITOPS_ROOT`: GitOps root for discovery. Defaults to `WORKDIR` in Jenkins.
- `STACKSMITH_DISCOVERY_MODE`: `auto`, `folders`, `flat-files`, or `env-files`. Defaults to `auto`.
- `STACKSMITH_MAX_PARALLEL_OPERATIONS`: Maximum independent operations planned or run concurrently within each environment. Defaults to `10`.
- `STACKSMITH_ENV_FILE`: Env file passed to Stacksmith. Defaults to `/dev/null` to prevent implicit `.env` loading.
- `STACKSMITH_CONFIG_REF`: Required platform-managed Stacksmith config reference.
- `STACKSMITH_DEBUG`: Environment equivalent for the `DEBUG` parameter. A truthy value enables debug mode even when the build parameter is false.
- `STACKSMITH_REQUIRE_LOCKFILE`, `STACKSMITH_OFFLINE`, and `STACKSMITH_LOCKFILE`: Job- or folder-managed source-locking policy. These settings are intentionally not exposed as build parameters.
- `STACKSMITH_NO_CAS`, `STACKSMITH_FORCE_RERUN`, `STACKSMITH_VALIDATION_REPORT_FORMAT`, `STACKSMITH_UPLOAD_ARTIFACTS`, and `STACKSMITH_ARGS_JSON`: Shared execution settings with the same behavior described above. `STACKSMITH_ARGS_JSON` must be an ordered JSON array and cannot override the managed config or lock policy. In test-only mode, it can include explicit `tests.yaml` paths and pytest arguments after `--`.
- `NO_VALIDATE_BRANCH_AND_OPERATION`: Set to `true` to bypass the shared default-branch and pull-request operation guard.
- `STACKSMITH_DEFAULT_BRANCH` or `BRANCH_IS_PRIMARY`: Branch-policy context when Jenkins does not provide it.
- `TG_AUTH_PROVIDER_CMD` and `TG_IAM_ASSUME_ROLE`: Optional Terragrunt authentication settings.

Bind remote-source credentials through `STACKSMITH_CREDENTIALS_JSON`. Provide a JSON array of credential objects, each with a Jenkins credential ID, required type, and optional variable name overrides.

**Basic form** (uses automatic variable naming from credential ID):

```json
[
  {"credentialId": "my-git-token", "type": "string"},
  {"credentialId": "my-http-basic", "type": "usernamePassword"}
]
```

This generates environment variables: `STACKSMITH_MY_GIT_TOKEN` and `STACKSMITH_MY_HTTP_BASIC` (credentialId uppercased with dashes replaced by underscores).

**With explicit variable names** (full control):

```json
[
  {
    "credentialId": "my-git-ssh",
    "type": "sshUserPrivateKey",
    "keyFileVariable": "MY_SSH_KEY",
    "usernameVariable": "MY_SSH_USER"
  },
  {
    "credentialId": "my-http-basic",
    "type": "usernamePassword",
    "usernameVariable": "CUSTOM_HTTP_USER",
    "passwordVariable": "CUSTOM_HTTP_PASS"
  },
  {
    "credentialId": "my-token",
    "type": "string",
    "variable": "MY_CUSTOM_TOKEN"
  }
]
```

Each credential object supports the following.

- `credentialId` (required): Jenkins credential ID to bind
- `type` (required): Credential type. Supported types:
  - `string`, `secret_text`, `git_token`, `http_token`: Token/secret credentials
  - `usernamePassword`, `http_basic`: Username and password credentials
  - `sshUserPrivateKey`, `git_ssh_key`: SSH key credentials
- `variable` (optional): Environment variable name for token/string credentials (default: `STACKSMITH_<CREDENTIALID_UPPERCASE>` with dashes replaced by underscores)
- `usernameVariable`, `passwordVariable` (optional): Variable names for username/password credentials (defaults: `STACKSMITH_<CREDENTIALID_UPPERCASE>_USERNAME`, `STACKSMITH_<CREDENTIALID_UPPERCASE>_PASSWORD`)
- `keyFileVariable` (optional): Variable name for SSH key path (default: `STACKSMITH_<CREDENTIALID_UPPERCASE>_KEY`)

This example now also shows app deployment and native operation patterns alongside infrastructure stacks. The shared config can expose approved Terraform component types such as `helm_app` and `k8s_app`, plus approved operations for local commands and Jenkins builds.
