# Shared GitOps example modules

This directory contains shared Stacksmith module implementations for app
deployment types that a platform team can expose through a managed config.

These modules are intentionally simplified examples:

- `helm_app` models a Helm-style app deployment path using `helm_release`.
- `k8s_app` models a Kubernetes manifest delivery path using `kubernetes_manifest`.
- `command_runner` models an approved command execution path.
- `jenkins_build` models a Jenkins build trigger for deployments managed by Jenkins.

They demonstrate how Stacksmith component types can be mapped to
platform-approved modules and how a GitOps repo can include both infrastructure
and app deployment patterns.

These modules are referenced by the canonical GitOps example at
`examples/gitops-repo` through `examples/shared-config-repo/stacksmith-config.yaml`.

## Usage

Use these module sources in `stacksmith-config.yaml` module mappings and then
refer to them from stack components by `type`.

Example module mapping:

```yaml
module_mappings:
  helm_app:
    source:
      source: local
      data:
        path: examples/modules/helm_app
```

Example component:

```yaml
components:
  frontend:
    type: helm_app
    properties:
      name: frontend-release
      chart: ingress-nginx
      repository: https://kubernetes.github.io/ingress-nginx
      version: "4.11.3"
      namespace: web
      values_files:
        - frontend-values.dev.yaml
```

Example Kubernetes manifest component:

```yaml
components:
  app_config:
    type: k8s_app
    properties:
      namespace: default
      manifest_files:
        - app-config.dev.yaml
```

Example approved command component:

```yaml
components:
  deploy-app:
    type: command_runner
    properties:
      command_name: deploy
      vars:
        APP_ENV: prod
        RELEASE_TAG: "2026-06-02"
```

## Jenkins build module

`jenkins_build` sends a `POST` request to Jenkins using HTTP basic
authentication. It uses `buildWithParameters` for parameterized jobs and
`build` for non-parameterized jobs. It uses `terraform_data` so a build is
triggered on the first apply and only again when the Jenkins URL, job name,
parameters, `job_has_parameters`, or `rebuild_token` change. Authentication
values and parameters are marked sensitive; credential rotation alone does not
retrigger a build.

If `parameters` is empty, set `job_has_parameters: false` explicitly. Leaving
`job_has_parameters` unset with an empty `parameters` map is treated as
ambiguous and fails fast.

The machine applying this module needs Python 3. The module supports folder
jobs through slash-separated job names and URL-encodes each folder segment.

```yaml
components:
  deploy-app:
    type: jenkins_build
    properties:
      jenkins_url: https://jenkins.example.com
      job_name: deployments/my-app
      job_has_parameters: true
      parameters:
        environment: production
        image_tag: "2026.07.13"
      rebuild_token: "2026-07-13T12:00:00Z"
      username: "{{ inputs.jenkins_username }}"
      api_token: "{{ inputs.jenkins_api_token }}"
```

To deliberately re-run an unchanged job, change `rebuild_token` to any new
value. This is useful for a GitOps commit that requests a deployment retry;
leave it unset or unchanged for normal idempotent applies.
