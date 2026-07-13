# Simple GitOps Example

This example shows a minimal `env-files` GitOps layout that deploys a single IAM role with an inline policy granting EC2 describe permissions.

It uses a shared `common/stacksmith.yaml` runfile for the managed config and stack definition, plus an environment-specific overlay under `environments/dev.yaml`.

## Local reproduction

Plan the `dev` environment from repository root:

```bash
ENVIRONMENT=dev
stacksmith plan \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/${ENVIRONMENT}.yaml
```

Apply the `dev` environment from repository root:

```bash
ENVIRONMENT=dev
stacksmith apply \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/${ENVIRONMENT}.yaml
```
