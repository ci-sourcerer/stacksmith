# Repository layouts

Repository ownership and deployment layout are separate choices. Application teams can own stacks while platform teams own managed configuration and modules in another repository.

| Layout | Identifier | Choose it when |
| --- | --- | --- |
| Single deployment | `single` | One deployment lives at the root with `stacksmith.yaml` or `stack.yaml`. |
| Environment files | `env-files` | Shared inputs live in `common/stacksmith.yaml` and small overrides in `environments/dev.yaml`. |
| Environment directories | `folders` | Each environment needs its own runfile and assets under `environments/dev/`. |

Use `auto` to detect the layout. Mixing environment files and environment runfile directories requires an explicit mode or reorganizing the root. Shared runfiles do not count as a competing single deployment. Single deployments use `default` as their target identity.

Start with the local examples in `examples/quickstart`, then compare `examples/environment-files` and `examples/environment-directories`. They use the same workload. Shared changes affect all environments; environment-specific changes select their target. Unknown changed paths conservatively select every environment.

A monorepo can contain several ordinary deployment roots; this does not require another layout. Platform currently configures one root per repository. Local examples use local state; configure durable remote state before CI execution.
