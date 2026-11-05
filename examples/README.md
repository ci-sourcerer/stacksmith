# Examples

Start with [Quickstart](quickstart/README.md), then compare the same workload in [Environment files](environment-files/README.md) and [Environment directories](environment-directories/README.md).

| Tutorial | Purpose |
| --- | --- |
| Quickstart | One deployment, local state, no cloud credentials. |
| Environment files | Shared inputs with compact dev/prod overrides. |
| Environment directories | Environment-owned runfiles and assets. |
| Operations | Deferred secrets and automatic operation reconciliation. |
| Multi-application monorepo | Independent application deployment roots. |

These tutorials use the built-in `terraform_data` resource. Local-state examples are not ready for CI; configure durable remote state first. GitHub Actions wrapper templates live in `github-actions`.

The supported discovery identifiers are `single`, `env-files`, and `folders`, with `auto` for detection. Platform currently configures one deployment root per repository.

For real repository ownership boundaries, see [the managed platform example](https://github.com/ci-sourcerer/stacksmith-example-platform) and [the application example](https://github.com/ci-sourcerer/stacksmith-example-app).
