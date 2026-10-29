# Stacksmith

Stacksmith is a YAML/JSON-driven orchestration layer for [OpenTofu](https://opentofu.org) and [Terragrunt](https://terragrunt.gruntwork.io). Application teams describe infrastructure with small stack files while platform teams centrally manage module mappings, providers, state, policy, and CI behavior.

!!! warning "Early project"

    Stacksmith is a proof of concept. Interfaces and repository history may change without notice, and CI workflows—especially destructive paths—need further production validation. Use it at your own risk.

## Choose where to begin

- **New to Stacksmith?** Follow [Getting started](getting-started.md) to understand the files and run the first validation and plan.
- **Evaluating the model?** Read [Core concepts](concepts.md) for the boundaries between application and platform concerns.
- **Writing infrastructure?** Use [Stack authoring](guides/stack-authoring.md) and [Advanced authoring](guides/advanced-authoring.md).
- **Operating multiple stacks?** See [Execution and orchestration](guides/execution.md).
- **Building platform controls?** See [Managed configuration](guides/managed-configuration.md) and [CI and GitOps](integrations/ci.md).
- **Looking up a command?** Open the generated [CLI reference](reference/cli.md).

## What Stacksmith manages

- Component-to-module resolution and provider routing.
- Layered configuration, variables, templates, and remote resources.
- Validation and transformation policies.
- OpenTofu and Terragrunt generation and execution.
- Dependency-aware monorepo orchestration.
- Lockfiles, offline execution, and targeted previews.
- Native operations and guarded CI lifecycle workflows.

In short, Stacksmith is a wrapper for Terragrunt, which is itself a wrapper for OpenTofu. Its purpose is to provide a smaller application-facing contract without removing the underlying infrastructure engine or its dependency model.

## Typical workflow

1. A platform team defines a managed configuration and approved component mappings.
2. An application team authors a stack using those component types.
3. Stacksmith resolves inputs, policies, modules, providers, and state configuration.
4. The application team validates and reviews an OpenTofu plan.
5. Stacksmith applies the reviewed infrastructure and any approved operations.
