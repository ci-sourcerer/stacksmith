# Jenkins GitOps Pipeline for Stacksmith

This folder contains the opinionated Jenkins GitOps multibranch pipeline for Stacksmith.

## Usage

Configure your Jenkins job as a Multibranch Pipeline and set the pipeline script path to `jenkins/Jenkinsfile`.

## Required Jenkins configuration

The pipeline supports two execution modes, controlled by environment variables:

1.  **Directly on a labeled agent**: Set `STACKSMITH_NODE_LABEL` to the Jenkins agent label to run on.
2.  **Inside a Docker container**: Omit `STACKSMITH_NODE_LABEL`. The pipeline will run on any available agent and execute inside a Docker container.

- `STACKSMITH_NODE_LABEL`: Optional Jenkins agent label. If provided, the pipeline runs directly on the agent.
- `STACKSMITH_IMAGE_VERSION`: Optional. Specifies the Docker image tag for `cisourcerer/stacksmith` when running in container mode. Defaults to `latest`.
- `STACKSMITH_DISCOVERY_MODE`: `folders`, `flat-files`, or `env-files`. Defaults to `folders`.

The following overrides can technically be set, but it is unnecessary in most cases. If omitted (probably what you want), [`src/stacksmith/gitops.py`](../src/stacksmith/gitops.py) derives equivalent CI context from Jenkins-native environment variables such as `JENKINS_URL`, `CHANGE_ID`, `CHANGE_TARGET`, and `GIT_COMMIT`.

- `CALLER_EVENT_NAME` — explicit event name override used for selection
- `CALLER_BASE_REF` — explicit base ref override for PR diffs
- `CALLER_EVENT_BEFORE` — explicit previous commit SHA override for push diffs
- `CALLER_SHA` — explicit current commit SHA override

## Behavior

- The pipeline is a scripted pipeline that dynamically generates parallel stages for each selected environment.
- It selects environments using the same [`scripts/select_gitops_environments.py`](../scripts/select_gitops_environments.py) helper as the GitHub Actions workflow.
- Jenkins-specific context is normalized in [`src/stacksmith/gitops.py`](../src/stacksmith/gitops.py), so pull request and branch builds do not need a separate Jenkins-only selection code path.
- If no environments are selected, the job prints a summary and exits successfully.
- For `apply` operations, the pipeline will pause for manual approval before proceeding.
- For `plan` operations, generated plan files and validation reports are archived as build artifacts.
- The pipeline attempts to run all selected environments in parallel, even if one fails, and reports a summary of failures at the end.

## Parameters

The Jenkins job accepts the following parameters:

- `OPERATION`: `plan` or `apply`. Defaults to `plan`.
- `ENVIRONMENTS`: Optional comma-separated list of environments to target manually.
- `WORKDIR`: The working directory for `stacksmith` commands. Defaults to `.`.
- `FAIL_ON_CHANGES`: If `true`, the `plan` operation will fail if it contains any resource changes. Defaults to `false`.
- `STRICT_VALIDATION_WARNINGS`: If `true`, validation warnings will be treated as failures. Defaults to `false`.

Discovery mode is configured via the environment variable `STACKSMITH_DISCOVERY_MODE` rather than a job parameter.
