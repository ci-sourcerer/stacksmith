# Jenkins GitOps Pipeline for Stacksmith

This folder contains the opinionated Jenkins GitOps multibranch pipeline for Stacksmith.

## Usage

Configure your Jenkins job as a Multibranch Pipeline and set the pipeline script path to `jenkins/Jenkinsfile`.

## Required Jenkins configuration

The pipeline supports three execution modes, controlled by environment variables.

1. **Inside a Kubernetes pod**: Set `STACKSMITH_USE_K8S` to a truthy value. The pipeline creates a pod using the Jenkins Kubernetes plugin and executes inside its `stacksmith` container.
2. **Directly on a labeled agent**: Set `STACKSMITH_NODE_LABEL` to the Jenkins agent label to run on.
3. **Inside a Docker container**: Omit both settings. The pipeline will run on any available agent and execute inside a Docker container.

- `STACKSMITH_USE_K8S`: Optional boolean. When enabled, Kubernetes mode takes precedence over the labeled-agent and Docker modes.
- `STACKSMITH_NODE_LABEL`: Optional Jenkins agent label. If provided, the pipeline runs directly on the agent.
- `STACKSMITH_IMAGE`: Optional full container image used in Kubernetes and Docker modes.
- `STACKSMITH_IMAGE_VERSION`: Optional Stacksmith image tag used when `STACKSMITH_IMAGE` is unset. Defaults to `latest`.
- `STACKSMITH_DISCOVERY_MODE`: `folders`, `flat-files`, or `env-files`. Defaults to `auto` for the GitOps example layout.
- `STACKSMITH_GIT_TOKEN`: Optional. Git token used by Stacksmith and Terragrunt CAS for HTTPS Git sources.
- `STACKSMITH_GIT_SSH_KEY`: Optional. SSH private key path used by Stacksmith and Terragrunt CAS for SSH Git sources.
- `STACKSMITH_HTTP_TOKEN`: Optional. Bearer token for Stacksmith HTTP(S) remote references.
- `STACKSMITH_HTTP_USERNAME` / `STACKSMITH_HTTP_PASSWORD`: Optional basic auth credentials for Stacksmith HTTP(S) remote references.
- `TG_AUTH_PROVIDER_CMD`: Optional Terragrunt auth provider command.
- `TG_IAM_ASSUME_ROLE`: Optional IAM role ARN passed through to Terragrunt.

The following overrides can technically be set, but it is unnecessary in most cases. If omitted (probably what you want), Stacksmith derives equivalent CI context from Jenkins-native environment variables such as `JENKINS_URL`, `CHANGE_ID`, `CHANGE_TARGET`, and `GIT_COMMIT`.

- `CALLER_EVENT_NAME` — explicit event name override used for selection
- `CALLER_BASE_REF` — explicit base ref override for PR diffs
- `CALLER_EVENT_BEFORE` — explicit previous commit SHA override for push diffs
- `CALLER_SHA` — explicit current commit SHA override

## Behavior

- The pipeline is a scripted pipeline that dynamically generates parallel stages for each selected environment.
- It selects environments using the same `stacksmith info environments` CLI command as the GitHub Actions workflow, so discovery semantics track the installed Stacksmith version.
- Jenkins-specific context is normalized by the same installed Stacksmith code path used by GitHub Actions, so pull request and branch builds do not need a separate Jenkins-only selection code path.
- Each environment run writes generated files, plan JSON output, and cache data to `.stacksmith-ci/<environment>` to avoid collisions during parallel execution.
- If no environments are selected, the job prints a summary and exits successfully.
- For `apply` and native `operation` modes, the pipeline will pause for manual approval before proceeding.
- For `plan` operations, generated plan files and validation reports are archived as build artifacts.
- The pipeline attempts to run all selected environments in parallel, even if one fails, and reports a summary of failures at the end.

## Parameters and environment variables

The Jenkins job accepts the following parameters:

- `COMMAND`: Stacksmith command to run: `plan`, `apply`, or `operation`. Defaults to `plan`.
- `OPERATION_NAME`: Stack-local native operation name. Required when `COMMAND` is `operation`.
- `ENVIRONMENTS`: Optional comma-separated list of environments to target manually.
- `WORKDIR`: The working directory for `stacksmith` commands. Defaults to `.`.
- `FAIL_ON_CHANGES`: If `true`, the `plan` operation will fail if it contains any resource changes. Defaults to `false`.
- `STRICT_VALIDATION_WARNINGS`: If `true`, validation warnings will be treated as failures. Defaults to `false`.

The following values are read from Jenkins folder properties (or the job environment) rather than job parameters:

- `STACKSMITH_NO_CAS`: If `true`, the pipeline passes `--no-cas` to Stacksmith runtime commands.
- `STACKSMITH_FORCE_RERUN`: If `true`, force replacement of the native operation runner.
- `STACKSMITH_ENV_FILE`: Env file passed to Stacksmith. Defaults to `/dev/null` to prevent implicit `.env` loading in CI.
- `STACKSMITH_VALIDATION_REPORT_FORMAT`: Validation report format for plans. Defaults to `json`.
- `STACKSMITH_UPLOAD_ARTIFACTS`: Whether plan reports and plan JSON are archived. Defaults to `true`.
- `STACKSMITH_ARGS_JSON`: Ordered JSON array of additional Stacksmith CLI arguments. This is the preferred escape hatch for CLI options that are not first-class pipeline parameters. It cannot override the managed config.
- `STACKSMITH_CREDENTIALS_JSON`: Optional JSON object describing the credentials to bind. Each entry should include a `credentialId` value and an auth type such as `git_token`, `git_ssh_key`, `http_token`, or `http_basic`.
- `TG_AUTH_PROVIDER_CMD`: Optional Terragrunt auth provider command read from the Jenkins environment/folder properties.
- `TG_IAM_ASSUME_ROLE`: Optional IAM role ARN passed through to Terragrunt from the Jenkins environment/folder properties.

Example:

```json
{
  "git_token": { "credentialId": "my-git-token" },
  "git_ssh_key": { "credentialId": "my-git-ssh" },
  "http_token": { "credentialId": "my-http-token" },
  "http_basic": { "credentialId": "my-http-basic" }
}
```

For example, additional ordered CLI options can be configured without shell-quoting loss:

```json
["--vars", "vars/common.yaml", "--tag", "service"]
```

Discovery mode is configured via the environment variable `STACKSMITH_DISCOVERY_MODE` rather than a job parameter.
