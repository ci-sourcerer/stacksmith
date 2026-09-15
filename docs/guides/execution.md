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

## Aggregate plan reports

`stacksmith plan` and `stacksmith run-all plan` print a final table of planned resource changes grouped by stack and component. They also write `plan-summary.json` into the build directory, including when console reporting is disabled. Destroy previews (`--destroy`) use the same report with `mode: "destroy"`. Apply execution uses the separate applied-change report below; operation planning does not produce this plan report.

```sh
stacksmith run-all plan --root . --save-plan-summary-json artifacts/plan-summary.json
stacksmith plan --stack stack.yaml --plan-summary detailed
stacksmith plan --stack stack.yaml --plan-summary none
```

The `table` format is the default. `detailed` also lists resource addresses, moves, imports, output changes, drift, and deferred changes. Human output goes to stderr; existing machine-readable validation output on stdout is preserved. Collection renders each saved plan once and shares it with validation and existing plan exports.

### JSON contract

Reports have `schema_version: 1`, command and planning mode, timestamps, selection metadata, an exit code, aggregate `totals`, and a `stacks` array. Each stack records its components and execution status, and includes a `plan` inventory when JSON collection succeeded. Stack plans contain their own totals, component totals, resource changes, output changes, drift, and deferred changes. Resource records retain original ordered `actions` as well as a normalized `action`.

- `create`, `update`, `replace`, and `destroy` count resource instances. A replacement counts once under `replace`, without incrementing `create` or `destroy`.
- `read`, `forget`, `import`, and `move` are reported separately. Imports and moves can overlap other actions; adding all counters together does not give a distinct resource count.
- `resources` counts changed managed resource instances, excluding data source reads. `outputs` counts changed root outputs.
- `drift` describes observed changes outside OpenTofu. `deferred` describes changes OpenTofu could not fully plan. Neither is added to planned mutation totals.
- Nested modules are attributed to their top-level Stacksmith component. Resources without a known component have `component: null` and appear under `(root/unmapped)`.
- `complete` means every selected stack was collected successfully and its plan had no deferred or unrecognized actions. It describes coverage of the selected scope, not policy approval or coverage of filtered-out infrastructure.
- Stack `status` is `completed`, `failed`, or `not_run`. `policy_status` and `exit_code` distinguish successful collection from policy rejection. `empty_selection` explicitly identifies runs with no selected stacks.

The summary contains identities and action metadata, without before/after values, input variables, import IDs, generated configuration, or diagnostic messages. Resource addresses and output names remain visible. Use the separate `--save-redacted-plan-json` artifact for attribute-level review.

The plan summary and plan validation report remain separate contracts, but share a `run_id`. The plan summary records `validation_status`, validation counts, and the validation report destination. The validation report records `plan_summary_path`, whether plan collection was complete, and basic create, update, replace, destroy, resource, and output totals. Direct CLI validation is emitted to stdout, so its plan-summary reference uses `destination: "stdout"` and a null path. Managed CI runs record the validation artifact path. The console ends with one footer containing the change outcome, validation outcome, and both destinations.

### Failure behavior

During `run-all plan`, validation failures and `--fail-on-changes` failures allow subsequent plans to finish before returning a nonzero status. Execution failures stop planning; the final report contains completed results and marks remaining stacks `not_run`. Interruptions during planning produce a partial report when Python cleanup can run. Failure to write the report is an error.

Each invocation builds its report in memory and atomically replaces the destination. It never reads old plan files to compute totals. Reports begin after stack preparation; failures before planning starts do not produce a new report. CI matrix jobs produce separate reports per environment; merging reports across jobs is outside this initial scope.

## Applied-change reports

`stacksmith apply`, `stacksmith destroy`, and their `run-all` equivalents print a report of confirmed changes and write `apply-summary.json` into the build directory. Existing approval prompts, live OpenTofu output, saved-plan execution, and fail-fast behavior are preserved.

```sh
stacksmith apply --stack stack.yaml --apply-summary detailed
stacksmith run-all apply --root . --save-apply-summary-json artifacts/applied.json
stacksmith destroy --stack stack.yaml --apply-summary none
```

The `table` format is the default; `detailed` adds resource addresses and individual operation outcomes. `none` hides console reporting while still writing JSON. The report covers infrastructure execution; separately executed after-apply operations retain their own results. Consequently, an infrastructure report can succeed even if a later after-apply operation fails.

### Event capture and compatibility

Stacksmith detects whether the resolved OpenTofu binary supports `-json-into`, available in OpenTofu 1.12 and newer. This captures structured events alongside the normal human-readable UI without changing approval behavior. Unsupported binaries still execute their original command, and the artifact explicitly reports `json_event_capture_unavailable` with `complete: false`.

Raw events can contain sensitive data. Stacksmith captures them in a private temporary directory, extracts only identity and action metadata, and removes the temporary files during normal cleanup. CI archives the resulting summary, which excludes resource IDs, attribute values, output values, and diagnostic messages. See OpenTofu's [apply options](https://opentofu.org/docs/cli/commands/apply/) and [JSON UI contract](https://opentofu.org/docs/internals/machine-readable-ui/) for the event source.

### Confirmed results and partial failures

The JSON has `schema_version: 1`, command, selection metadata, timestamps, infrastructure exit code, aggregate totals, and a per-stack inventory. Each resource includes its component, planned action, operation outcomes, and overall status. Operations record their confirmation source.

- Resource mutations are counted after completion hooks. A planned or started action does not increase completed totals.
- A replacement counts once after both create and destroy complete. A replacement whose deletion succeeds and creation fails records one confirmed destruction, a failed operation, and a partial resource outcome.
- Imports and state removals are confirmed after successful execution when the final apply counters match the known planned operations. Moves are confirmed from their planned addresses and successful execution. Their confirmation source distinguishes these results from resource completion hooks.
- Failed operations may have effects the provider could not confirm. Their attempted actions remain in the inventory, and confirmed totals represent only the known completed work.
- Remaining stacks are `not_run` after a failure. Partial, interrupted, malformed, unsupported, or inconsistent event streams produce `complete: false`.
- Final OpenTofu counters are retained as `reported_totals` and checked against the collected inventory. Missing completion events cannot be mistaken for a successful run with zero changes.
- Root output names are included as `output_names`. The event stream does not provide an applied before/after output diff, so `output_changes_available` is explicitly `false`; these names are not claimed as changed outputs.

`complete` describes resource-operation coverage for the selected infrastructure scope. `exit_code` records whether infrastructure execution succeeded. State-only operations that cannot be confirmed remain visible as `unconfirmed`, rather than being counted as applied.
