# Execution and orchestration

Control single-stack and monorepo execution with runfiles, lockfiles, dependency ordering, previews, and targeting.

## The Runfile

A runfile, usually `stacksmith.yaml`, is a reproducible invocation file for Stacksmith itself. It solves the GitOps problem of recording exactly which stack layers, shared configs, and variable sources were used for a deployment-oriented command instead of relying on an ephemeral shell history entry.

This is useful when platform teams publish a shared repo of base stack layers and managed defaults while application teams add service-specific overlays on top.

In the following example, the runfile references two stack layers (one from a git repo and one local) and three variable sources in a deterministic order. The final source supplies inline default values for the stack. There is no `configs` section in this example, as the runfile author chose to rely on the environment variable `STACKSMITH_CONFIG` for config layering (coming from, for example, a GitHub Actions repository variable, or a Jenkins global environment variable).

```yaml
description: Payments deployment assembled from shared and service-owned layers.

stacks:
  - source: git
    description: Shared payments platform layer.
    data:
      repo: https://github.com/org/platform-stacks.git
      path: base/payments/stack.yaml
      ref: v1.4.0
  - source: local
    description: Service-owned payments layer.
    data:
      path: ./stack.yaml

vars:
  - source: git
    description: Shared platform defaults.
    data:
      repo: https://github.com/org/platform-config.git
      path: vars/common.yaml
      ref: v3.2.1
  - source: local
    description: Development environment values.
    data:
      path: ./vars.dev.yaml
  - source: inline
    description: Deployment-specific defaults.
    data:
      replicas: 2
      feature_flags:
        canary: true
```

Runfile templating follows the two-stage model from [Templating matrix](advanced-authoring.md#templating-matrix).

- Stage 1 happens while runfiles are loaded and can use `runfile.path`, `runfile.dir`, `runfile.name`, and `runfile.stem`.
- Stage 2 happens later when inline vars are merged into inputs and can use `inputs` and `stack`.

Example stage 1 usage in a runfile reference.

```yaml
vars:
  - source: local
    data:
      path: "{{ runfile.dir }}/vars.dev.yaml"
```

Example stage 2 usage in runfile inline vars.

```yaml
vars:
  - source: inline
    data:
      bucket_name: "{{ inputs.prefix }}-{{ stack.name }}"
```

Layering rules are deterministic.

- `stacks` are applied first in order for single-stack commands.
- `configs` are applied first, and later CLI `--config` flags append after them.
- `vars` sources act as a base layer ahead of CLI `--vars` and `--var` entries.
- Inline `vars` sources accept any YAML value type, including objects, arrays, booleans, and numbers.
- `merge_mode` controls how layering is applied. `deep` is the default. `override` makes each later layer replace the previous value wholesale.

Regarding "deep merge":

- Dicts merge recursively.
- Lists append in order.
- Later scalar values replace earlier ones.
- Set-like model fields such as tags deduplicate when parsed into the final model.

Stacksmith validates layered documents in two phases. Each config, stack, runfile, test manifest, and vars layer is first checked as a fragment, so required values may be supplied by a later layer. The fragment profile is derived from the same bundled schema by deferring completeness constraints, which keeps it synchronized with the effective contract. After merging, the effective document is checked against its complete schema and then against semantic model rules. A managed config used on its own must therefore define `backend` and either an explicit module mapping or a default module mapping, while a config overlay may omit those values when another selected layer supplies them. Validation errors list every contributing source in precedence order and identify the missing or invalid document path.

The repository's VS Code settings associate mergeable YAML and JSON documents with generated `*.layer.schema.json` schemas, so partial overlays retain key and type diagnostics without false missing-key errors. The strict `*.schema.json` schemas remain the effective runtime contracts. Run `poe schemas-layer` after changing a strict schema and use `poe schemas-layer-check` to detect generated-schema drift.

Address-aware `merge_rules` can change the strategy for individual nodes while leaving `merge_mode` as the fallback.

```yaml
merge_mode: deep
merge_rules:
  - description: Replace environment values supplied by later component layers.
    select: >-
      scope == 'stack' &&
      starts_with(address, '/components/') &&
      ends_with(address, '/properties/environment')
    mode: override
  - description: Replace feature flags as one environment-owned object.
    select: "scope == 'vars' && address == '/feature_flags'"
    mode: override
```

Each `select` value is a JMESPath predicate evaluated for every node that exists in both the accumulated and incoming layers. The predicate context contains the following fields.

| Field | Value |
| - | - |
| `scope` | One of `stack`, `config`, `runfile`, or `vars` |
| `address` | The node's JSON Pointer address, such as `/components/api/properties/environment` |
| `path` | The address as an array of path segments |

Selectors must return a boolean. When multiple rules match the same address, the last matching rule wins. An `override` rule replaces the complete value at its address, so rules for its descendants are not evaluated. JSON Pointer escaping uses `~1` for `/` and `~0` for `~` within mapping keys.

> **Runfile bootstrap constraint:** Rules declared by a runfile apply to stack, config, and variable layering after the runfile has loaded. They cannot control the merge of the runfile layers that declare them because the effective rules are not known until that merge completes. Runfile merging can only use an address-aware policy supplied externally through the Python API; otherwise it uses its existing merge mode.

For `run-all`, `stacks` can also be used as an explicit target list instead of directory discovery.

If `--runfile` is omitted, Stacksmith checks `STACKSMITH_RUN_FILE` and then auto-detects `./stacksmith.yaml` when present.

`--merge-mode` on the CLI always takes precedence over the runfile `merge_mode` value and disables its `merge_rules`, making the selected mode a force-all override for that invocation.

## Lockfiles

Stacksmith lockfiles record the resolved stack, managed configuration, runfile, and variable inputs used by a single-stack workflow. Commit `stacksmith.lock.yaml` alongside the runfile or stack so changes to remote or local inputs can be reviewed.

`stacksmith generate` and `stacksmith init` create a missing lockfile automatically and enforce it during the same command. They do not replace an existing mismatched lockfile; use `stacksmith lock` when you intend to update the recorded inputs.

```bash
stacksmith lock stack.yaml --config stacksmith-config.yaml
stacksmith lock stack.yaml --config stacksmith-config.yaml --check
stacksmith plan stack.yaml --config stacksmith-config.yaml --locked
```

Pass `--locked` to `generate`, `init`, `plan`, `apply`, or `destroy` to reject missing or mismatched lock data. Add `--offline` to resolve locked remote inputs only from the local cache.

Unlocked CLI and Python API runtime calls warn by default. Set `STACKSMITH_WARN_ON_UNLOCKED=0` to suppress that warning, or set `STACKSMITH_REQUIRE_LOCKFILE=1` to require CLI runtime commands to use lock enforcement.

## Monorepo orchestration

In a monorepo, stacksmith recursively discovers all `stack.yaml`/`stack.yml`/`stack.json` files from a root directory and builds a dependency graph from `depends_on` declarations.

### Inter-stack dependencies

When a stack declares `depends_on`, Stacksmith generates a Terragrunt dependency block so the producing stack is applied first. The producing stack declares an explicit `outputs` contract; Stacksmith compiles those declarations into root OpenTofu `output` blocks that Terragrunt can read from dependency state. Outputs are not inferred from every underlying component because that would expose implementation details and make the inter-stack contract unstable.

Each output requires a `value`, which may reference a managed public component output. Optional `description` and `sensitive` fields map to the corresponding OpenTofu output fields. A Jinja-only `transform` can adapt the bound value with `output.value` and `output.name`. These deferred values may only be interpolated directly, not used in filters, control flow, calls, or calculations. Stack-authored transforms intentionally do not support Python hooks.

An optional `mock` value lets dependent stacks plan or validate before the producer has been applied. The mock models the value before the stack-level transform, and Stacksmith applies the same transform to both the real and mock values.

```yaml
# networking/vpc/stack.yaml
name: vpc

components:
  network:
    type: aws_vpc
    properties:
      cidr_block: "10.0.0.0/16"

outputs:
  vpc_uri:
    description: Stable URI for the shared VPC.
    value: "{{ components.network.id }}"
    transform:
      description: Adapt the VPC identifier into a URI.
      jinja: "vpc://{{ output.value }}"
    mock: mock-vpc-id
  subnet_ids:
    description: Private subnet identifiers.
    value: "{{ components.network.private_subnet_ids }}"
    mock:
      - mock-subnet-1
      - mock-subnet-2
```

The consuming stack declares the dependency edge.

```yaml
# compute/web/stack.yaml
name: web

depends_on:
  - vpc

components:
  web-server:
    type: aws_ec2_instance
    properties:
      instance_type: t3.medium
```

### Monorepo commands

```bash
stacksmith run-all <action> [--root <dir>] [--config <config> ...] [--clean] [--auto-approve]
```

If `STACKSMITH_ROOT` is set, it is used as the default root path. If not, root defaults to the current working directory.

`<action>` is one of `init`, `plan`, `apply`, `destroy`. Stacks are generated in topological dependency order and then Terragrunt is executed per generated stack directory in that order. For `destroy`, execution order is reversed so dependents are destroyed before dependencies.

When `action` is `plan`, you can also pass `--destroy` to run `terragrunt plan -destroy` for every stack.

When `action` is `plan`, pass `--save-redacted-plan-json <dir>` to keep archive-safe plan JSON for each discovered stack. Use `--save-plan-json <dir>` only when a trusted local consumer requires the raw rendered plan because OpenTofu includes sensitive values in its machine-readable output.

Use `--clean` on `run-all` to remove the existing build directory before regeneration.

### Dependency and execution previews

Use `info graph` to inspect the discovered dependency graph and the execution that Stacksmith would construct without writing generated files or invoking Terragrunt.

```bash
stacksmith info graph \
  --root examples/gitops-simple-repo \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/dev.yaml
```

The default table view includes stack paths, dependency edges, state keys, selected components, mock-output usage, build directories, logical Terragrunt commands, and the computed order. Use `--action destroy` to preview the reversed destruction order. Stack filters (`--include-tag` and `--exclude-tag`) and component selectors (`--tag` and `--tag-expr`) use the same semantics as `run-all`.

Pass `--format json` for the versioned machine-readable preview contract. Graphviz DOT and Mermaid flowcharts are also available through `--format dot` and `--format mermaid`.

Use `run-all --dry-run` to perform the same discovery, input resolution, static validation, filtering, targeting, and command construction as an execution without cleaning or writing build output.

```bash
stacksmith run-all plan \
  --root examples/gitops-simple-repo \
  --runfile examples/gitops-simple-repo/common/stacksmith.yaml \
  --runfile examples/gitops-simple-repo/environments/dev.yaml \
  --tag-expr "component_name == 'first'" \
  --dry-run \
  --format json
```

Dry runs can still resolve remote inputs, update Stacksmith's resource cache, and execute configured provider, transform, and static validation hooks. Those hooks can have their own external side effects. Dry runs do not resolve or download the Terragrunt/OpenTofu toolchain, execute Terragrunt, create plan files, or run post-plan validation rules.

Options that require an actual plan or execution, including plan artifact output, exact plan input, change detection, and strict post-plan validation, cannot be combined with `--dry-run`.

## Tags and targeting

Stacksmith supports both stack-level and component-level targeting.

- Stack tags come from the stack `tags` field and can be filtered in `run-all` with `--include-tag` and `--exclude-tag`.
- Component tags come from component `tags` plus optional managed-config module tags.
- Target expressions use `--tag-expr` and are evaluated with context keys including `tags`, `tag`, `stack_tags`, `component_name`, and `component_type`.
