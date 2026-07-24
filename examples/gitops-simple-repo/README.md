# Simple GitOps Example

This credential-free example creates two Terraform `null_resource` instances. It uses the same hybrid `env-files` discovery layout as the canonical GitOps example.

The common runfile loads the credential-free config from the shared config repo, the stack, and `vars/vars.common.yaml`. The discovered `dev` and `prod` environment files add their matching environment-specific vars.

## Discover the environments

Run discovery from the repository root.

```bash
stacksmith info environments \
  --gitops-root examples/gitops-simple-repo \
  --discovery-mode auto
```

## Plan or apply

Choose either `dev` or `prod`.

```bash
ENVIRONMENT=dev
stacksmith plan \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/${ENVIRONMENT}.yaml
```

```bash
ENVIRONMENT=dev
stacksmith apply \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/${ENVIRONMENT}.yaml
```

The local backend writes state under `.stacksmith-state`. OpenTofu downloads the HashiCorp null provider, but no cloud credentials are required.
