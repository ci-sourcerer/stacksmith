# GitOps Example

This example GitOps repository uses the hybrid `env-files` discovery style.
It keeps the shared runfile under `common/stacksmith.yaml`, keeps each environment manifest in `environments/<env>.yaml`, and keeps the shared stack layers under `manifests/common/`.

The reusable workflow also supports the other two discovery styles:

- `folders` for `environments/<env>/` directories
- `flat-files` for root-level `stacksmith.<env>.yaml` files

Example layout for the canonical hybrid sample:

```text
examples/gitops-repo/
  common/
    stacksmith.yaml
  environments/
    dev.yaml
    prod.yaml
  manifests/
    common/
      platform.stack.yaml
      service.stack.yaml
    environments/
      dev/
        app-config.yaml
        frontend-values.yaml
      prod/
        app-config.yaml
        frontend-values.yaml
  vars/
    vars.dev.yaml
    vars.prod.yaml
```

The other two discovery styles look like this:

```text
folders:
  environments/
    dev/
      stacksmith.yaml
    prod/
      stacksmith.yaml

flat-files:
  stacksmith.dev.yaml
  stacksmith.prod.yaml
```

This example is intentionally local-path based for easy testing. In a real GitOps workflow, point the `source: local` references at remote Git or HTTP sources instead.

## Local testing with Stacksmith

The reusable workflow fans out one job per environment, using the shared runfile in `common/stacksmith.yaml` and the environment file in `environments/<env>.yaml`. You can reproduce that locally with the same inputs the CI job would pass.

Plan the `dev` environment from this repository root:

```bash
ENVIRONMENT=dev
stacksmith plan \
  --runfile examples/gitops-repo/common/stacksmith.yaml \
  --runfile examples/gitops-repo/environments/${ENVIRONMENT}.yaml \
  --vars examples/gitops-repo/vars/vars.${ENVIRONMENT}.yaml
```

Apply the `dev` environment from this repository root:

```bash
ENVIRONMENT=dev
stacksmith apply \
  --runfile examples/gitops-repo/common/stacksmith.yaml \
  --runfile examples/gitops-repo/environments/${ENVIRONMENT}.yaml \
  --vars examples/gitops-repo/vars/vars.${ENVIRONMENT}.yaml
```
