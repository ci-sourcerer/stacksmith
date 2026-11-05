# Operations

This local example adds an automatic reconciliation operation to the quickstart workload. Infrastructure requires no cloud credentials. The operation uses a deferred secret independently of infrastructure.

Set `STACKSMITH_EXAMPLE_SECRET` to a disposable test token and `STACKSMITH_SECRET_FINGERPRINT_KEY` to a stable, high-entropy value in your shell. Do not commit either value. Run from this directory.

```sh
stacksmith plan --runfile stacksmith.yaml
stacksmith apply --runfile stacksmith.yaml
stacksmith apply --runfile stacksmith.yaml
stacksmith operation destroy --runfile stacksmith.yaml
stacksmith destroy --runfile stacksmith.yaml
```

The first apply reconciles the operation, masking the token. The second unchanged apply should not rerun it. Rotating the token changes its fingerprint. Unsetting the token makes secret resolution fail before execution. Keep the fingerprint key stable between runs. This example uses local state and is not a CI deployment.
