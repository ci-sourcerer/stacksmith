# Quickstart

A local-state tutorial using the built-in `terraform_data` resource. No AWS credentials or operation secrets are required. Run commands from this directory.

```sh
stacksmith validate --runfile stacksmith.yaml
stacksmith plan --runfile stacksmith.yaml
stacksmith apply --runfile stacksmith.yaml
stacksmith destroy --runfile stacksmith.yaml
```

Expect one resource on the first apply and no changes on a repeated plan. Destroy removes it from local state. Keep `.stacksmith-state` until cleanup completes. CI requires a durable remote backend.
