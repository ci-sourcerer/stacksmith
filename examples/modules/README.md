# Shared GitOps example modules

This directory contains shared Stacksmith module implementations for app deployment types that a platform team can expose through a managed config.

These modules are intentionally simplified examples:

- `helm_app` models a Helm-style app deployment path using `helm_release`.
- `k8s_app` models a Kubernetes manifest delivery path using `kubernetes_manifest`.

They demonstrate how Stacksmith component types can be mapped to platform-approved modules. Note that `helm_app` and `kubernetes_app` are shown here as modules, but they could also be implemented as [native operations](../../README.md#native-operations) instead—it is purely a platform team preference. Modules are declarative and benefit from OpenTofu state management and dependency tracking. Operations are imperative actions that are useful for commands or workflows that don't map cleanly to infrastructure code. For app deployments like these, modules are typically the better choice, because they have first-class OpenTofu provider support, but the architecture is flexible.

These modules are referenced by the canonical GitOps example at
`examples/gitops-repo` through `examples/shared-config-repo/stacksmith-config.yaml`.

## Usage

Use these module sources in `stacksmith-config.yaml` module mappings and then refer to them from stack components by `type`.

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
