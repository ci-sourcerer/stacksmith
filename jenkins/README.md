# Jenkins GitOps Pipeline for Stacksmith

This folder contains the opinionated Jenkins GitOps pipeline for Stacksmith.

## Usage

Add this folder as a git submodule to your repository so Jenkins can checkout the pipeline code alongside your app repo.

```bash
git submodule add <stacksmith-repo-url> jenkins/stacksmith-jenkins
```

Then configure your Jenkins job as a Multibranch Pipeline and set the pipeline script path to:

```text
jenkins/stacksmith-jenkins/Jenkinsfile
```

## Required Jenkins configuration

The pipeline uses the following environment variables:

- `STACKSMITH_AGENT_LABEL` — Jenkins agent label to run on
- `STACKSMITH_GITOPS_ROOT` — path to the GitOps root
- `STACKSMITH_DISCOVERY_MODE` — `folders`, `flat-files`, or `env-files`
- `STACKSMITH_ENV_FILE` — path to env file, defaults to `/dev/null`
- `STACKSMITH_IMAGE_VERSION` — Stacksmith container image version

The following overrides are optional. If omitted, `src/stacksmith/gitops.py` derives equivalent CI context from Jenkins-native environment variables such as `JENKINS_URL`, `CHANGE_ID`, `CHANGE_TARGET`, and `GIT_COMMIT`.

- `CALLER_EVENT_NAME` — explicit event name override used for selection
- `CALLER_BASE_REF` — explicit base ref override for PR diffs
- `CALLER_EVENT_BEFORE` — explicit previous commit SHA override for push diffs
- `CALLER_SHA` — explicit current commit SHA override

## Behavior

- The pipeline selects environments using the same `scripts/select_gitops_environments.py` helper as GitHub Actions.
- Jenkins-specific context is normalized in `src/stacksmith/gitops.py`, so pull request and branch builds do not need a separate Jenkins-only selection code path.
- If no environments are selected, the job prints a summary and exits successfully.
- Selected environments run in parallel on the configured agent label.

## Jenkins pipeline model

- `jenkins/Jenkinsfile` is the only supported Jenkins entrypoint.
- It is best used as a Multibranch Pipeline when you want branch-aware selection and diff-based environment discovery.
- Shared execution behavior lives in `jenkins/stacksmith-helpers.groovy`.

## Example pipeline parameters

In Jenkins, you can pass these parameters to the job:

- `OPERATION`: `plan` or `apply`
- `GITOPS_ROOT`: `.`
- `DISCOVERY_MODE`: `folders`
- `ENVIRONMENTS`: optional comma-separated list of target environments

The following values are configured through environment variables rather than job parameters:

- `STACKSMITH_ENV_FILE`: optional `.env` path, defaults to `/dev/null`
- `STACKSMITH_IMAGE_VERSION`: defaults to `latest`

The opinionated Jenkins wrapper does not expose free-form extra CLI args for `plan` or `apply`; execution behavior is defined by the pipeline configuration.

If you want a local wrapper repo to consume this pipeline, the repo should checkout the submodule and use the `jenkins/stacksmith-jenkins/Jenkinsfile` path.
