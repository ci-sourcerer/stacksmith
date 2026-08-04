# Simple GitOps Example

This credential-free example creates two Terraform `terraform_data` instances and a harmless local echo operation. It uses the same hybrid `env-files` discovery layout as the canonical GitOps example.

The common runfile loads the credential-free config from the shared config repo, the stack, and `vars/vars.common.yaml`. The discovered `dev` and `prod` environment files add their matching environment-specific vars.

The stack also declares `announce_reconciliation`, which selects the platform-approved `echo_reconciliation` operation. Its explicit `after_apply` trigger makes an apply reconcile the operation in a separate operation-only state after the Terraform resources converge. The operation receives the declared environment, message, project, and a sample secret token, then masks those values in streamed output so the example demonstrates redaction in practice. The token is sourced from the `STACKSMITH_EXAMPLE_SECRET` environment variable through a Jinja template, which shows the new environment-variable templating hook in action. For `dev`, it runs `Stacksmith simple GitOps reconciliation completed: environment=dev message=Hello from development project=stacksmith token=***`. This simulates a real-world GitOps workflow where an application-level operation is triggered after a successful apply without giving its Terraform root authority to change infrastructure.

## Discover the environments

Run discovery from the repository root.

```bash
stacksmith ci environments \
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

The local backend writes state under `.stacksmith-state`. OpenTofu downloads the HashiCorp terraform builtin provider, but no cloud credentials are required.

## CI backend policy

This credential-free example is intended for local Stacksmith commands. `stacksmith ci prepare`, including the GitHub Actions and Jenkins entrypoints, rejects its local backend. Configure a remote backend before using this repository layout in CI so state is durable and shared between plan and apply jobs.
