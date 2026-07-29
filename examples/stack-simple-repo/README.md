# Simple Stack Example

This credential-free example creates two Terraform `terraform_data` instances. It uses the `terraform-data-config.yaml` to configure the framework rather than the AWS-based `stacksmith-config.yaml`.

The common runfile loads the credential-free config from the shared config repo, the stack, and `vars.dev.yaml`.

## Plan or apply

```bash
stacksmith plan \
  --runfile examples/stack-simple-repo/common/stacksmith.yaml \
  --runfile examples/stack-simple-repo/environments/dev.yaml
```

```bash
stacksmith apply \
  --runfile examples/stack-simple-repo/common/stacksmith.yaml \
  --runfile examples/stack-simple-repo/environments/dev.yaml
```

The local backend writes state under `.stacksmith-state`. OpenTofu downloads the HashiCorp terraform builtin provider, but no cloud credentials are required.
