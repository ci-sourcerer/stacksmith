# Stacksmith

Stacksmith is a YAML/JSON-driven orchestration layer for [OpenTofu](https://opentofu.org) and [Terragrunt](https://terragrunt.gruntwork.io). It lets application teams describe infrastructure as reusable components while platform teams centrally manage modules, providers, state, policy, and CI behavior.

> [!WARNING]
> Stacksmith is an early proof of concept. Interfaces and repository history may change without notice, and CI workflows—especially destructive paths—need further production validation. Use it at your own risk.

## What it does

- Maps small stack definitions to platform-approved OpenTofu modules.
- Resolves layered configuration, variables, templates, and remote resources.
- Runs validation and transformation policies before infrastructure changes.
- Orchestrates dependency-aware stacks across monorepos.
- Provides guarded GitHub Actions and Jenkins workflows.
- Supports native operations alongside infrastructure lifecycle commands.

## Quick start

Set up the development environment and inspect the CLI.

```sh
uv sync --group dev
uv run stacksmith --help
```

A typical workflow validates, previews, and applies a stack against a platform-managed configuration.

```sh
uv run stacksmith validate --stack stack.yaml --config stacksmith-config.yaml
uv run stacksmith plan --stack stack.yaml --config stacksmith-config.yaml
uv run stacksmith apply --stack stack.yaml --config stacksmith-config.yaml
```

See the [getting started guide](https://stacksmith.ci-sourcerer.com/getting-started/) for a working stack example and the required configuration.

## Documentation

The [Stacksmith documentation](https://stacksmith.ci-sourcerer.com/) is the canonical source for concepts, authoring guides, integrations, and reference material. Documentation source is maintained in [`docs/`](docs/), and the complete generated command reference is available in the [CLI reference](https://stacksmith.ci-sourcerer.com/reference/cli/).

For local documentation development, use `poe docs-serve`. Run `poe docs-build` to perform the same strict build used in CI.

## Development

Run the standard project checks before submitting changes. This project uses [`common-python-tasks`](https://github.com/ci-sourcerer/common-python-tasks).

```sh
poe format
poe lint
poe test
```

## License

Stacksmith is available under the terms in [`LICENSE`](LICENSE).
