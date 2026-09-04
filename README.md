# Stacksmith

**HEADS UP:** This project is absolutely a work in progress, there is no warranty, I have no idea what I am doing, etc. The current state is a proof-of-concept and its git history may be wiped at any moment. Use at your own risk/feel free to ask what's going on. Also, the license is no joke. This project is open source and how I contribute to it is going to stay that way.

**Definitely untested things:** CI stuff, especially destroy-related items.

## Overview

Stacksmith is a CLI tool that lets teams define infrastructure stacks in a simple YAML (or JSON) format and deploy them via [OpenTofu](https://opentofu.org) and [Terragrunt](https://terragrunt.gruntwork.io). It bridges the gap between a developer writing a plain resource list and the OpenTofu ecosystem by abstracting module wiring, backend configuration, variable resolution, policy checks, and monorepo orchestration.

In short: Stacksmith is a wrapper for Terragrunt, which itself is a wrapper for OpenTofu.

## Core concepts and examples

### Stack

A stack is the unit of infrastructure authored by application or service teams. A stack file contains metadata, tags, dependency edges, components, explicit root outputs, and operation invocations. It is the "calling code" that references abstract [component](#components) types declared in the managed config and provides properties for those components.

### Managed config

The managed config (`stacksmith-config.yaml`) is the shared contract controlled by platform teams. It defines backend settings, OpenTofu version, providers, explicit or convention-based module mappings, and centralized validation/transform rules.

### Components

Components are the entries under `components` in a stack file. Each component declares the following.

- `type`: an abstract type resolved by the [managed config](#managed-config) to a OpenTofu module
- `tags`: optional [targeting tags](#tags-and-targeting)
- `properties`: module input values authored by stack owners

### Writing a stack

A stack definition describes a logical unit of infrastructure. Developers write it, and the [managed config](#managed-config) resolves implementation details.

```yaml
# stack.yaml

name: my-app

tags:
  - apps
  - storage

components:
  app-bucket:
    type: aws_s3_bucket
    properties:
      acl: private
      bucket: "{{ inputs.bucket_name }}"
  app-server:
    type: aws_ec2_instance
    properties:
      ami: ami-0abcdef1234567890
      instance_type: t3.small
```

Stacksmith property templates can also access stack metadata via `stack.name` and `stack.tags`, plus `git_repository` when the current working directory is a Git repository with an `origin` remote.
For example, you can compute values from the stack name like `{{ stack.name }}-{{ inputs.bucket_name }}`.

Components in the same stack consume managed public outputs with ordinary Jinja syntax. The managed config maps each public output to an underlying OpenTofu module output and may transform that reference, so stack authors do not need to know the selected module's interface or repeat adapter logic. For example, `{{ components.app_server.private_ip }}` passes the public `private_ip` output from `app_server` to another component property. Stacksmith binds the deferred value to a native OpenTofu reference during generation, preserving dependency inference.

Component outputs may be interpolated into component properties, stack output values, and operation inputs. They cannot drive Jinja loops, conditionals, filters, calls, or calculations because their values are not known until OpenTofu evaluates the generated dependency graph. A Jinja loop may still iterate over known inputs and emit direct component output references from those known keys. Component names containing hyphens use bracket notation, such as `{{ components["app-server"].private_ip }}`.

#### Consuming collection-valued component outputs

A component output may be a complete list, map, or object. Stacksmith can pass that collection to another component as one native OpenTofu value without knowing its contents during generation. The receiving module can then use ordinary OpenTofu expressions or `for_each` to create resources or invoke child modules for the collection. This usually removes the need for a Stacksmith-level `for_each`.

For example, an application module can expose endpoints keyed by stable service names, while a DNS component receives the complete map.

```yaml
components:
  services:
    type: application_services

  dns:
    type: route53_records
    properties:
      zone_id: Z123456
      zone_name: example.com
      endpoints: "{{ components.services.endpoints }}"
```

The module selected for `route53_records` can iterate over that input itself.

```hcl
variable "endpoints" {
  description = "Service endpoints keyed by stable service name."
  type = map(object({
    ip = string
  }))
}

resource "aws_route53_record" "this" {
  for_each = var.endpoints

  zone_id = var.zone_id
  name    = "${each.key}.${var.zone_name}"
  type    = "A"
  ttl     = 300
  records = [each.value.ip]
}
```

The producing module should preserve stable, configuration-derived keys while allowing the values to remain unknown until apply.

```hcl
output "endpoints" {
  description = "Service endpoints keyed by configured service name."
  value = {
    for name, instance in aws_instance.this :
    name => {
      ip = instance.private_ip
    }
  }
}
```

OpenTofu can plan the downstream `for_each` because the service names identify the instances even though their IP addresses are not yet known. The same pattern works when the receiving module calls another module with `for_each` instead of declaring resources directly.

Neither a module, a transform, nor a hypothetical Stacksmith-level `for_each` can create same-plan instances when the collection's keys are themselves unknown until apply. For example, a map keyed by generated IP addresses cannot drive `for_each` during the plan that creates those addresses.

```hcl
output "instances_by_ip" {
  description = "Instance identifiers keyed by generated IP address."
  value = {
    for instance in values(aws_instance.this) :
    instance.private_ip => instance.id
  }
}
```

Stacksmith transforms run during configuration generation and receive a deferred expression such as `${module.services.endpoints}`, not the eventual map. They may wrap or reshape that expression symbolically, but they cannot inspect its apply-time entries or bypass OpenTofu's requirement that [`for_each` keys be known during planning](https://opentofu.org/docs/language/meta-arguments/for_each/). Model such relationships with stable configured keys and unknown values, pass the whole collection to a collection-aware module or resource, use a fixed set of known slots, or introduce a separate apply boundary when the identities are genuinely discovered at runtime.

A future Stacksmith-level `for_each` could improve authoring convenience, root module addresses, and instance-level targeting, but it would not unlock iteration over unknown output keys. Collection-aware modules are therefore the preferred way to consume repeated or structured component outputs. If you do not want to change a module to be collection-aware, you can create a thin wrapper module that accepts the collection and calls the original module with `for_each`.

#### Generating components with Jinja

Stacksmith renders the complete stack source with the resolved `inputs` map before it parses and validates YAML or JSON. This lets a stack template generate any number of explicit components while keeping each generated component independently named, tagged, targeted, and referenced.

```yaml
components:
{% for worker_name, worker in inputs.workers.items() %}
  "{{ worker_name }}":
    type: aws_ec2_instance
    properties:
      ami: {{ worker.ami | tojson }}
      instance_type: {{ worker.instance_type | tojson }}
      tags:
        worker: {{ worker_name | tojson }}
{% endfor %}
```

The same rendering pass handles ordinary values, so existing property expressions such as `bucket: "{{ inputs.bucket_name }}"` remain supported. Use the Jinja `tojson` filter for an unquoted dynamic YAML value when it might contain characters that need escaping.

#### State backend

The S3 state key is derived automatically from the stack file's path relative to the repo root. For example `networking/vpc/stack.yaml` produces key `networking/vpc/terraform.tfstate`. For standalone stacks (single-stack commands without a `--root`), the key is simply `<name>/terraform.tfstate`. Native operations use a separate `<stack-path>/operations/terraform.tfstate` key.

Backend policy is platform-owned and uses one of `data`, `inline`, or `script`. `data` contains a fixed backend mapping, while executable forms define `config(**context)` and return one. The resolver runs after a stack's inputs are resolved, so it can select state from values such as `inputs["environment"]` or inspect its trusted execution environment (for example, with AWS STS). Stack authors can influence ordinary input values but cannot supply resolver code.

```yaml
backend:
  script:
    source: local
    data:
      path: scripts/resolve_backend.py
```

```python
def config(**context):
    environment = context["inputs"]["environment"]
    return {
        "type": "s3",
        "bucket": f"platform-state-{environment}",
        "region": "us-east-1",
    }
```

### Configuration

This section shows managed config authoring details.

```yaml
# stacksmith-config.yaml: maintained by the platform team

backend:
  data:
    type: s3
    bucket: my-org-state
    region: us-east-1

tools:
  tofu:
    version: "1.11.6"
  terragrunt:
    version: "1.0.6"

provider_mappings:
  aws:
    source:
      source: registry
      data:
        address: hashicorp/aws
        version: "= 5.91.0"
    instances:
      default:
        config:
          data:
            region: us-east-1
      secondary:
        alias: secondary
        config:
          data:
            region: us-west-2
            assume_role:
              role_arn: arn:aws:iam::123456789012:role/stacksmith-secondary

module_mappings:
  aws_s3_bucket:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-s3.git
        ref: "3.2.1"
    auto_expose_outputs: true
    providers:
      aws: aws.secondary
    properties:
      acl:
        mapped_to: bucket_acl
    outputs:
      id:
        description: Stable bucket identifier.
        mapped_from: s3_bucket_id
      arn:
        description: Bucket ARN for IAM policies.
        mapped_from: s3_bucket_id
        transform:
          description: Adapt the bucket identifier into an ARN.
          jinja: "arn:aws:s3:::{{ output.value }}"
  aws_ec2_instance:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-ec2.git
        ref: "5.0.0"
```

Provider definitions are grouped by provider family and can expose multiple named instances through `instances`. A `default` instance is optional; if omitted, Stacksmith emits an empty provider block for the unaliased provider. Non-default instances must define an explicit `alias`. Module mappings can optionally define a `providers` map that routes module provider names to an instance reference in `<provider>.<instance>` format. If a module mapping omits `providers`, Stacksmith uses the unaliased provider. The optional `outputs` map defines the component's public output contract. Each key is the name stack authors use under `components.<instance>`, while `mapped_from` selects the underlying module output and defaults to the public name. An output `transform` adapts the unresolved native OpenTofu reference before it reaches consumers. Jinja output transforms receive `output.value`, `output.name`, and `output.module_output`, plus component and stack metadata. Python output transforms receive the reference as `value` and the same metadata through `context`.

Set `auto_expose_outputs: true` to introspect the underlying module and make its other declared outputs available under the same names, such as `{{ components.app.instance_id }}`. Explicit output mappings remain authoritative: they can rename or transform outputs, and an underlying output claimed by an explicit mapping is not also exposed under its implementation name. Automatic exposure only affects component references inside the current stack; it never creates root stack outputs or expands the inter-stack state contract.

Output transforms execute during Stacksmith generation, before OpenTofu knows the actual output value. They can wrap a reference in a string or produce a structured list or object for a direct component reference. A structured transformed output cannot be embedded inside a larger string.

Use `default_module_mapping` to resolve component types that do not have an explicit entry in `module_mappings`. The default supports `source`, `auto_inject_inputs`, `auto_expose_outputs`, `tags`, `providers`, `properties`, and `outputs`.

```yaml
default_module_mapping:
  source:
    source: git
    data:
      repo: https://github.com/my-org/{{ component.type | replace("-", "_") }}
      ref: latest
  auto_inject_inputs: true
  auto_expose_outputs: true

module_mappings:
  exceptional_component:
    source:
      source: git
      data:
        repo: https://github.com/my-org/special-module.git
        ref: v1.2.3
```

Explicit mappings always take precedence. When no explicit mapping exists, Stacksmith renders string fields within the default mapping's `source` using strict, sandboxed Jinja and then validates the result as an ordinary local, Git, or registry module source. Templates can reference `component.type`, which is the component's declared type, and `component.name`, which is the component instance key in the stack. For `stacksmith info modules-and-policies <component-type>`, no component instance exists, so `component.name` is set to the requested component type. An unfiltered `info modules-and-policies` lists only explicit mappings because a default mapping represents an open-ended set of possible types.

`module_mappings` may be empty or omitted when `default_module_mapping` is configured. A managed config must provide at least one explicit mapping or a default mapping.

Each provider instance `config` must use exactly one top-level source key to define provider arguments. Supported sources are the following.

- `data`: Literal YAML mapping used directly as provider arguments.
- `inline`: Inline Python defining `config(**context)` that returns a dictionary of provider arguments.
- `script`: Path or URL to a Python script defining `config(**context)` that returns a dictionary of provider arguments.

Stacksmith can also introspect remote module sources to discover which OpenTofu `variable` inputs the module actually exposes. When `auto_inject_inputs: true` is enabled for a module mapping, stacksmith uses that discovery data to inject same-name resolved inputs automatically, without requiring empty `{}` property declarations for every module input. This means that only module variables that actually exist are auto-injected, unmapped stack inputs that might be organizational like `environment` are not leaked into a module that does not declare them, and explicit `mapped_to` mappings and property overrides still work as before.

Managed configs can define reusable `module_input_sets` for inputs that every selected module must receive. This is useful for organizational context such as `environment`, `business_unit`, or ownership tags that should be passed consistently across modules. Add set names to top-level `required_module_input_sets` to apply them to every generated module, or to a module mapping's `required_input_sets` to apply them only to components of that type. Required input sets are stricter than auto-injection: Stacksmith fails before planning if a required input value is missing. Stacksmith passes those values to the module, but the module remains responsible for declaring and using its own Terraform variables.

```yaml
module_input_sets:
  organization:
    description: Required organizational metadata.
    inputs:
      environment:
        type: string
        description: Deployment environment.
      business_unit:
        type: string
        description: Owning business unit.

required_module_input_sets:
  - organization

module_mappings:
  app:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-app.git
        ref: v1.2.3
    required_input_sets:
      - organization
```

Output introspection follows the same source-resolution rules. With `auto_expose_outputs: true`, only identifier-style names declared by the module can be referenced automatically. Outputs with names that require special OpenTofu traversal syntax need an explicit managed alias. Stacksmith does not execute the module during discovery, and explicit output mappings and transforms continue to define the managed aliases and adapters.

A few things to note about the config are as follows.

- **Provider versions should probably be exact pins where possible, not ranges.** Fuzzy constraints like `~> 5.0` leave room for provider updates to silently change behaviour across deployments. The config is the right place to make upgrades deliberate and reviewed.
- **Component types must have a resolution path.** Stacksmith uses an explicit mapping first, then the default mapping when configured, and rejects the component at generation time when neither exists.

## Execution & Orchestration

### The Runfile

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

Runfile templating follows the two-stage model from [Templating matrix](#templating-matrix).

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

### Lockfiles

Stacksmith lockfiles record the resolved stack, managed configuration, runfile, and variable inputs used by a single-stack workflow. Commit `stacksmith.lock.yaml` alongside the runfile or stack so changes to remote or local inputs can be reviewed.

`stacksmith generate` and `stacksmith init` create a missing lockfile automatically and enforce it during the same command. They do not replace an existing mismatched lockfile; use `stacksmith lock` when you intend to update the recorded inputs.

```bash
stacksmith lock stack.yaml --config stacksmith-config.yaml
stacksmith lock stack.yaml --config stacksmith-config.yaml --check
stacksmith plan stack.yaml --config stacksmith-config.yaml --locked
```

Pass `--locked` to `generate`, `init`, `plan`, `apply`, or `destroy` to reject missing or mismatched lock data. Add `--offline` to resolve locked remote inputs only from the local cache.

Unlocked CLI and Python API runtime calls warn by default. Set `STACKSMITH_WARN_ON_UNLOCKED=0` to suppress that warning, or set `STACKSMITH_REQUIRE_LOCKFILE=1` to require CLI runtime commands to use lock enforcement.

### Monorepo orchestration

In a monorepo, stacksmith recursively discovers all `stack.yaml`/`stack.yml`/`stack.json` files from a root directory and builds a dependency graph from `depends_on` declarations.

#### Inter-stack dependencies

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

#### Monorepo commands

```bash
stacksmith run-all <action> [--root <dir>] [--config <config> ...] [--clean] [--auto-approve]
```

If `STACKSMITH_ROOT` is set, it is used as the default root path. If not, root defaults to the current working directory.

`<action>` is one of `init`, `plan`, `apply`, `destroy`. Stacks are generated in topological dependency order and then Terragrunt is executed per generated stack directory in that order. For `destroy`, execution order is reversed so dependents are destroyed before dependencies.

When `action` is `plan`, you can also pass `--destroy` to run `terragrunt plan -destroy` for every stack.

When `action` is `plan`, pass `--save-redacted-plan-json <dir>` to keep archive-safe plan JSON for each discovered stack. Use `--save-plan-json <dir>` only when a trusted local consumer requires the raw rendered plan because OpenTofu includes sensitive values in its machine-readable output.

Use `--clean` on `run-all` to remove the existing build directory before regeneration.

#### Dependency and execution previews

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

### Tags and targeting

Stacksmith supports both stack-level and component-level targeting.

- Stack tags come from the stack `tags` field and can be filtered in `run-all` with `--include-tag` and `--exclude-tag`.
- Component tags come from component `tags` plus optional managed-config module tags.
- Target expressions use `--tag-expr` and are evaluated with context keys including `tags`, `tag`, `stack_tags`, `component_name`, and `component_type`.

## Advanced Authoring

### Inputs

Input resolution order from lowest to highest priority.

1. Vars files from `STACKSMITH_VARS`, when used without `--runfile`
2. Environment variables prefixed with `STACKSMITH_VAR_`
3. `stacksmith.yaml` `vars` sources, when a runfile is used
4. Explicit `--vars` and `--var key=value` entries, deep-merged in the order they appear on the command line

Runfile inline `vars` sources support Jinja in two stages. Stage 1 runs while the runfile itself is loaded and provides `runfile` metadata fields. Stage 2 runs during input resolution and provides `inputs` and `stack`.

The `runfile` metadata fields available during stage 1 are `runfile.path`, `runfile.dir`, `runfile.name`, and `runfile.stem`.

When Stacksmith renders a stack from a Git working tree with an `origin` remote, `git_repository` contains that remote URL. Stack rendering, input resolution, property transforms, and default module source templates resolve it from the stack file's directory, not Stacksmith's launch directory. Runfile stage 1 resolves it from the runfile directory. For direct input resolution without a stack, Stacksmith uses its current working directory. The variable is undefined when the relevant directory is not in a Git working tree or `origin` is not configured. For example, a stack can use `iac_repository: "{{ git_repository }}"` in an AWS resource tag.

When `--runfile` is used, Stacksmith applies runfile `vars` sources before CLI-provided variable layers and does not apply `STACKSMITH_VARS` defaults.

### Templating matrix

Stacksmith supports Jinja in specific surfaces rather than as a global feature.

| Surface | Render timing | Context | Notes |
| - | - | - | - |
| Stack source (`stack.yaml`, `stack.yml`, `stack.json`) | Before YAML or JSON parse and schema validation | `inputs`, `stack`, `components`, reserved stack-transform `output`, `git_repository` when available | Full-file render for structural generation. Component references and stack output transform values are preserved and bound during OpenTofu generation. |
| Resolved input values | After vars, env vars, runfile vars, and CLI vars are merged | `inputs`, `stack`, `git_repository` when available | Value-level render across merged inputs. |
| Runfile stage 1 (`stacksmith.yaml`) | During runfile load before schema validation | `runfile.path`, `runfile.dir`, `runfile.name`, `runfile.stem`, `git_repository` when available | Primarily for structured references and inline vars source data. |
| Runfile stage 2 (runfile inline vars after merge) | During input resolution | `inputs`, `stack`, `git_repository` when available | Lets runfile-provided values compose with final merged inputs and stack metadata. |
| `default_module_mapping.source` | During module mapping resolution when no explicit mapping exists | `component.type`, `component.name`, `env.git_repository` when available | Strict sandboxed render with post-render source validation. |
| Module property `default` values | During module input generation when the component omits the property | `property.name`, `property.kind`, `property.output_name`, `component.name`, `component.type`, `inputs`, `stack`, `components`, `env.git_repository` when available | Applies to explicit and default module mappings. Recursively renders configured defaults before transforms and validations; deferred component output references are preserved and bound afterward. |
| `properties.*.transform.jinja` | During input transform execution | `property.value` plus transform context (`property.name`, `property.kind`, `property.output_name`, `component.name`, `component.type`, `inputs`, `stack`, `env.git_repository` when available) | Adapts a resolved stack property into a module input. |
| `module_mappings.*.outputs.*.transform.jinja` | During component output binding | `output.value`, `output.name`, `output.module_output`, `component.name`, `component.type`, `stack`, `env.git_repository` when available | Adapts the unresolved module output reference into the public component output. |
| Stack `outputs.*.transform.jinja` | During root output generation | `output.value`, `output.name`, `stack`, `env.git_repository` when available | Safely adapts the exported value and its mock after component output binding. |

Other managed-config fields are intentionally non-templated.

### Remote resources

Stacksmith can pull scripts, config files, vars files, stack files, and runfiles from remote locations. Anywhere a local file path is accepted for validation scripts, transform scripts, vars files, stack files, config files, or `stacksmith.yaml`, a remote URL can be used instead.

Runfiles and config script references use a structured `source` + `data` object.

Supported sources are:

- `local` with `data.path`
- `git` with `data.repo`, `data.path`, optional `data.ref`
- `http` with `data.url`
- `registry` with `data.address`, `data.version`

Stacksmith treats this as the canonical representation and renders tool-specific syntax server-side before invoking downstream tools.

#### Canonical vs rendered target syntax

| Canonical reference | OpenTofu rendered value | CLI flag rendered value |
| - | - | - |
| `source: local`, `data.path: ./vars.dev.yaml` | `./vars.dev.yaml` | `./vars.dev.yaml` |
| `source: http`, `data.url: https://example.com/base.yaml` | `https://example.com/base.yaml` | `https://example.com/base.yaml` |
| `source: git`, `data.repo: https://github.com/org/shared.git`, `data.path: vars/base.yaml`, `data.ref: v1.2.3` | `git::https://github.com/org/shared.git//vars/base.yaml?ref=v1.2.3` | `git+https://github.com/org/shared.git//vars/base.yaml@v1.2.3` |
| `source: registry`, `data.address: hashicorp/aws`, `data.version: ~> 6.0` | `{ source = "hashicorp/aws", version = "~> 6.0" }` (provider/module fields) | Not used for file-style CLI flags |

#### Usage examples

In config validations/transforms, use a structured script reference.

```yaml
# stacksmith-config.yaml – remote managed input validation script
var_validations:
  bucket_name:
    script:
      source: http
      data:
        url: https://raw.githubusercontent.com/my-org/shared/main/validators/bucket.py
```

```yaml
# stacksmith-config.yaml – remote transform script from a git repo
module_mappings:
  aws_s3_bucket:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-s3.git
        ref: 3.2.1
    properties:
      acl:
        mapped_to: bucket_acl
        transform:
          script:
            source: git
            data:
              repo: https://github.com/my-org/shared.git
              path: transforms/acl.py
              ref: v2.0.0
```

Config files, vars files, stack files, and runfiles also support remote URLs via CLI flags (`--config`, `--vars`, `--stack`, `--runfile`) where URL strings are passed directly.

```shell
stacksmith plan \
  --config https://example.com/org-config.yaml \
  --vars git+https://github.com/org/defaults.git//env/base.yaml@v1.2.0 \
  --vars git+https://github.com/org/service-defaults.git//bucket-writer/dev.yaml@v3.4.1
```

```shell
stacksmith validate \
  --runfile git+https://github.com/org/platform-live.git//services/payments/stacksmith.yaml@main
```

#### Caching

Stacksmith and Terragrunt now use two cache layers.

- Stacksmith cache stores Stacksmith-resolved remote references (for example config files, vars files, stack files, runfiles, and Python scripts referenced by validations/transforms) under `.cache/` inside the build output directory, or `.stacksmith/.cache/` when no build directory is set.
- Terragrunt CAS caches Terragrunt source fetching (modules/catalog/stack sources) and is enabled by default in Terragrunt `>= 1.1.0`.

Use `--no-cache` to force Stacksmith to re-fetch its own remote references. On runtime commands (`init`, `plan`, `apply`, `destroy`, and `run-all`), `--no-cache` also disables Terragrunt CAS for that invocation.

Use `--no-cas` when you only want to disable Terragrunt CAS without clearing the Stacksmith cache.

#### Environment variable defaults

`STACKSMITH_CONFIG` and `STACKSMITH_VARS` can provide default config and vars references when the corresponding CLI flags are omitted.

`STACKSMITH_STACK` can provide a default stack file path when no positional stack argument is given.

`STACKSMITH_RUN_FILE` can provide a default runfile reference when `--runfile` is omitted. If it is not set, Stacksmith auto-loads `./stacksmith.yaml` when present.

Use colon-delimited lists.

If an item contains colons, such as a remote URL, wrap that item in quotes.

```shell
export STACKSMITH_VARS='"git+https://github.com/org/platform-defaults.git//env/base.yaml@v1.2.0":"git+https://github.com/org/service-defaults.git//bucket-writer/dev.yaml@v3.4.1"'
```

#### Authentication

Authentication is resolved by checking the `remote_auth` config section first, then falling back to environment variables.

##### Config-based auth

Add a `remote_auth` section to `stacksmith-config.yaml`, keyed by hostname.

```yaml
remote_auth:
  github.com:
    type: token
    token_env: GITHUB_TOKEN
    username_env: GITHUB_USERNAME
  gitlab.internal.com:
    type: basic
    username_env: GITLAB_USER
    password_env: GITLAB_PASS
  git.private.com:
    type: ssh
    ssh_key_path: /home/ci/.ssh/deploy_key
```

Supported auth types are `token` (HTTP Bearer or git token), `basic` (HTTP Basic), and `ssh` (Git SSH key).

When Stacksmith executes Terragrunt runtime commands, Stacksmith forwards HTTPS token auth through a temporary Git credential helper so CAS-backed and OpenTofu-initiated Git fetches can reuse your configured credentials. The helper reads tokens from the subprocess environment, contains no credential values itself, and is deleted when the subprocess exits. Token auth preserves a username supplied by Git configuration or the source URL, uses `username_env` when configured, and otherwise defaults the username to `git`.

##### Environment variable fallbacks

When no matching `remote_auth` entry exists, stacksmith checks the following environment variables.

| Variable | Purpose |
| - | - |
| `STACKSMITH_HTTP_TOKEN` | Bearer token for HTTP(S) requests |
| `STACKSMITH_HTTP_USERNAME` / `STACKSMITH_HTTP_PASSWORD` | Basic auth for HTTP(S) |
| `STACKSMITH_GIT_TOKEN` | Token auth for git clone (HTTPS) |
| `STACKSMITH_GIT_SSH_KEY` | Path to SSH private key for git clone |
| `STACKSMITH_SSL_VERIFY` | Set to `false` to disable TLS verification |

> ℹ️ **Note:** Remote config files are fetched *before* the config is loaded, so `remote_auth` entries are not available for config-level URLs. Use environment variables for authentication when fetching remote configs.

### Validation and transforms

Stacksmith supports Python validation hooks and Python or Jinja transform hooks.

- Validations use either `inline` Python or `script`.
- Transforms use `inline`, `script`, or `jinja` depending on context.
- Relative script paths resolve from the declaring file.
- Validation and transform specifications accept an optional `description`, including entries in `var_validations`, managed module properties and outputs, and stack output transforms.

Machine-facing mapping keys remain stable identifiers, while `description` carries prose for people and inspection output. Optional descriptions are also supported on managed configurations, provider families and instances, module mappings and properties, operation definitions and inputs, merge rules, runfiles and their stack/config/variable references, stacks and their components, outputs, operation invocations, and test manifests.

### Plan validations

The [managed config](#managed-config) can define `plan_validations` that run after `plan` and `run-all plan` against OpenTofu plan JSON output.

Plan validation rules can return `pass`, `warn`, or `fail` outcomes.

- Truthy values pass and falsey values fail.
- Warnings are non-blocking by default; use `--strict-validation-warnings` to treat warning outcomes as failures.
- Use `--fail-on-changes` on `plan` or `run-all plan` to return a non-zero exit code whenever the rendered plan contains *any* resource changes. This is useful for automated drift detection or CI checks where only a non-empty plan should fail.

### Native operations

Operations are config-owned imperative actions. Stacksmith compiles them into a separate runner-only Terraform root backed by `<stack-path>/operations/terraform.tfstate`. The infrastructure root never contains operation resources, and the operation root never contains infrastructure resources or providers.

Every approved public component output is exposed from the infrastructure root through a stable sensitive output named `stacksmith_operation_bridge_<component>_<output>`, even before an operation references it. These names can therefore appear under `Changes to Outputs` in an infrastructure plan, but they are bridge values rather than operation resources and do not execute anything. They must live in the infrastructure state because that is where their component values are produced. When an operation references one of those outputs, Stacksmith declares a matching sensitive input in the operation root and Terragrunt supplies its value through a read-only dependency. Predeclaring the bridges means adding or changing an operation does not require an infrastructure apply merely to establish its dependency contract.

The managed config fixes the runner details, including the local command argument vector or Jenkins job and credentials. A stack can only select an approved operation and supply declared inputs. Operation inputs support the same Jinja templates and deferred public component outputs as component properties, so an operation can consume an output such as `{{ components.app.release_name }}`. Operations use the `manual` trigger by default; set `trigger: after_apply` in managed config to run them after a successful apply.

Local operation environments can automatically expose every declared operation input. Names are uppercased by default; overrides and exclusions are nested under `inputs` so they cannot collide with environment policy fields:

```yaml
environment:
  mode: auto
  inputs:
    overrides:
      KUBE_CONTEXT: kubeconfig_context
    exclude:
      - secret_value
```

OpenTofu suppresses all `local-exec` output when its command or environment contains a sensitive value. To support operation-level output control, Stacksmith declassifies the runner specification only at the local process boundary while keeping it sensitive in plans, then discards the child process's standard output and standard error by default. Set `stream_output: true` on a managed local operation to inherit those streams through OpenTofu.

Use `output_masking` to define literal redaction rules for streamed local operation output. `output_masking.literals` masks fixed literal strings, and `output_masking.inputs` masks resolved values for selected operation inputs. When streaming is enabled, every secret input must be listed in `output_masking.inputs`.

```yaml
# stacksmith-config.yaml
module_mappings:
  application:
    source:
      source: registry
      data:
        address: example/application
        version: "1.0.0"
    outputs:
      release_name:
        description: Deployed application release name.

operations:
  deploy:
    description: Deploy an approved application release.
    runner: local
    trigger: after_apply
    stream_output: true
    command: [./bin/deploy]
    output_masking:
      literals:
        - DO-NOT-LEAK
      inputs:
        - release_name
    environment:
      APP_ENV: environment
      RELEASE_NAME: release_name
    inputs:
      environment:
        description: Deployment environment.
        required: true
      release_name:
        description: Immutable application release identifier.
        required: true
```

```yaml
# stack.yaml
components:
  app:
    type: application

operations:
  deploy_app:
    use: deploy
    with:
      environment: "{{ inputs.environment }}"
      release_name: "{{ components.app.release_name }}"
```

Dry-run a manual operation by its stack-local name before executing it.

```shell
stacksmith operation plan deploy_app --stack stack.yaml --config stacksmith-config.yaml
```

Omit operation names to dry-run every operation declared by the stack. The same omission for `operation run` executes all declared operations.

```shell
stacksmith operation plan --stack stack.yaml --config stacksmith-config.yaml
```

Infrastructure plans use `--after-apply` to restrict the operation preview to operations configured with the `after_apply` trigger. The Jenkins and GitHub Actions GitOps pipelines set this automatically for `plan` and `apply` commands.

```shell
stacksmith operation plan --after-apply --stack stack.yaml --config stacksmith-config.yaml
```

Run the operation after reviewing the plan.

```shell
stacksmith operation run deploy_app --stack stack.yaml --config stacksmith-config.yaml
```

Select multiple operations with a comma-delimited list. Stacksmith includes transitive `depends_on` operations automatically and generates a root containing only that batch. Independent operations run concurrently, while dependency edges preserve ordering. Set `STACKSMITH_MAX_PARALLEL_OPERATIONS` to cap concurrency; it defaults to `10` and is intentionally not a command-line or CI parameter.

```shell
STACKSMITH_MAX_PARALLEL_OPERATIONS=3 \
stacksmith operation run publish_image,deploy_app,smoke_test \
  --stack stack.yaml \
  --config stacksmith-config.yaml
```

For a one-time definite dispatch without changing the stack definition, add `--force-rerun` or set `STACKSMITH_FORCE_RERUN=1`. This marks each explicitly selected operation resource for replacement in the operation plan.

```shell
stacksmith operation run deploy_app --force-rerun --stack stack.yaml --config stacksmith-config.yaml
```

Alternatively, change `rerun_token` in the stack definition when the rerun request should remain declarative and reviewable. `operation plan` creates a saved operation-only OpenTofu plan without invoking provisioners. `operation run` validates that the plan contains no managed changes outside `module.stacksmith_operation_*` and applies that exact saved plan. Operations with the `after_apply` trigger run in stack dependency order during `stacksmith apply` and `stacksmith run-all apply`; Stacksmith replans them after infrastructure succeeds so component outputs are current. Stack-local `depends_on` can order multiple operations within a stack. Jenkins runners poll the queued build through completion, so a dependent operation starts only after its Jenkins prerequisite succeeds. Managed Jenkins definitions can set `poll_interval_seconds` and `timeout_seconds`, which default to 5 and 3600.

Preview removal of the complete isolated operation state without running operation provisioners, then destroy it after review.

```shell
stacksmith operation plan --destroy --stack stack.yaml --config stacksmith-config.yaml
stacksmith operation destroy --stack stack.yaml --config stacksmith-config.yaml
```

`operation destroy` generates and validates a fresh destroy plan, prompts for confirmation, and applies that exact saved plan. Add `--auto-approve` only in an already approved automation context. Operation names, `--after-apply`, and `--force-rerun` are rejected in destroy mode because cleanup removes the complete isolated state rather than dispatching selected operations.

#### Declarative application deployments through Jenkins

An approved Jenkins operation can deploy application code while the GitOps repository records exactly what should be deployed. Keep the Jenkins URL, job, credential variable names, and allowed parameter mapping in managed configuration. Set `trigger: after_apply` explicitly because operations remain manual by default.

```yaml
# stacksmith-config.yaml
operations:
  deploy_application:
    runner: jenkins
    trigger: after_apply
    url: https://jenkins.example.com
    job_name: deployments/application
    username_env: STACKSMITH_JENKINS_USERNAME
    api_token_env: STACKSMITH_JENKINS_API_TOKEN
    parameters:
      ENVIRONMENT: environment
      GIT_COMMIT: commit_id
    inputs:
      environment:
        description: Deployment environment.
        required: true
      commit_id:
        description: Immutable application Git commit to deploy.
        required: true
```

The stack selects that approved operation and binds its parameters to declarative inputs.

```yaml
# application.stack.yaml
operations:
  deploy_application:
    use: deploy_application
    with:
      environment: "{{ inputs.environment }}"
      commit_id: "{{ inputs.application_commit }}"
```

Each environment layer pins the desired application revision.

```yaml
# environments/prod.yaml or its referenced vars file
vars:
  - source: inline
    data:
      environment: prod
      application_commit: "8c9f20bd0cbf2c70f7f728f4e92bf6ad239a45b1"
```

On a merge to the default branch, the normal apply workflow reconciles the operation in its isolated state after infrastructure succeeds. Changing `application_commit`, the approved Jenkins definition, a component output, or another bound parameter changes the operation specification, so OpenTofu replaces the operation resource and Stacksmith starts the Jenkins build. An unchanged specification is a no-op. The runner passes `GIT_COMMIT` to Jenkins, waits for the queued build to finish, and fails the operation phase if Jenkins does not report success. Jenkins credentials stay in the CI environment and never enter the application manifest.

### Testing policies and transforms

Stacksmith tests are declared in `tests.yaml` manifests. `stacksmith test` compiles those manifests into an ephemeral pytest module and runs it with Stacksmith's managed-config fixture wiring, cache behavior, and layered merge behavior.

When `--config` points at one or more managed config layers, Stacksmith discovers `tests.yaml` beside each selected config and merges them in order. You can also pass explicit manifest paths after Stacksmith options.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-base-config.yaml \
  --config examples/shared-config-repo/stacksmith-config.yaml
```

```shell
stacksmith test \
  --config platform/base-config.yaml \
  --config platform/prod-config.yaml \
  platform/tests.yaml \
  -- -k imdsv2
```

Use `--dump-tests` when you want to inspect the generated pytest code.

```shell
stacksmith test \
  --config examples/shared-config-repo/stacksmith-base-config.yaml \
  --config examples/shared-config-repo/stacksmith-config.yaml \
  --dump-tests /tmp/stacksmith-generated-tests.py
```

Manifest test cases cover variable policies, plan policies, and component properties. Plan policy cases can include optional context (for example stack metadata), and manifests can define optional setup/teardown fixtures using either inline Python or script references. Fixture execution mode can be set to `per-suite` (default) or `per-test-case`.

```yaml
description: Policy and transform tests for the production platform configuration.

fixtures:
  mode: per-test-case
  setup:
    inline: |
      fixture_state["ready"] = True

var_validations:
  aws_region:
    - value: us-east-1
      expect: pass

plan_validations:
  ec2_t3_micro_warning:
    - resources:
        - type: aws_instance
          after:
            instance_type: t3.micro
      context:
        stack_name: production
      expect: warn

component_properties:
  aws_s3_bucket:
    bucket_name:
      - value: My_Bucket
        inputs:
          environment: prod
        expect:
          output_name: bucket
          value: prod-my-bucket
```

Each `resources` item requires `type`; Stacksmith supplies `address: <type>.this` and `change.actions: [create]` by default. Set `address` or `actions` explicitly for address-sensitive policies, multiple resources of the same type, deletes, replacements, or other non-default plan behavior. Use `plan` instead of `resources` when a test requires exact OpenTofu plan JSON.

### Local path resolution

- Local paths in `stacksmith.yaml` runfile `stacks`, `configs`, and local `vars` sources resolve relative to the runfile that declares them.
- Local script paths and local module source paths in `stacksmith-config.yaml` resolve relative to the config file that declares them.

## Integrations & Ecosystem

### CI GitOps workflows

Stacksmith provides equivalent opinionated GitOps entrypoints for GitHub Actions and Jenkins. Both use the same provider-neutral CI manifest and the `stacksmith ci prepare` / `stacksmith ci execute` contract, so environment selection and per-environment execution stay consistent across providers.

- [`.github/workflows/stacksmith-gitops-reusable.yml`](./.github/workflows/stacksmith-gitops-reusable.yml) executes one lifecycle phase for one environment from a versioned CI manifest.
- [`.github/workflows/stacksmith-gitops-opinionated-reusable.yml`](./.github/workflows/stacksmith-gitops-opinionated-reusable.yml) discovers environments and fans out to the single-environment reusable workflow.
- [`examples/github-actions/stacksmith-plan.yml`](./examples/github-actions/stacksmith-plan.yml), [`examples/github-actions/stacksmith-apply.yml`](./examples/github-actions/stacksmith-apply.yml), [`examples/github-actions/stacksmith-destroy.yml`](./examples/github-actions/stacksmith-destroy.yml), [`examples/github-actions/stacksmith-plan-operation.yml`](./examples/github-actions/stacksmith-plan-operation.yml), and [`examples/github-actions/stacksmith-operation.yml`](./examples/github-actions/stacksmith-operation.yml) are trigger wrappers that call the opinionated reusable workflow using `uses`.
- [`jenkins/vars/stacksmith.groovy`](./jenkins/vars/stacksmith.groovy) provides the opinionated Jenkins GitOps pipeline as the `stacksmith()` entrypoint of a trusted shared library.

The GitHub templates under `examples/` do not execute in this repository because they are outside `.github/workflows`.

#### Shared behavior

The opinionated reusable workflow prepares one provider-neutral manifest, discovers target environments, and then calls `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-reusable.yml@<version>` for each selected environment. The GitHub wrappers do this through `stacksmith ci prepare-from-env` and `stacksmith ci execute-from-env`. The Jenkins shared-library entrypoint uses the same adapter commands, so both providers converge on the same manifest and execution contract implemented by `stacksmith ci prepare` and `stacksmith ci execute`. A CI manifest prepared with command `test` supports only the `test` phase, which invokes `stacksmith test` with the manifest's managed config, selected environment, runfiles, variables, env file, working directory, and additional arguments. Plan and apply requests execute both the infrastructure `plan` phase and a `plan-operation` phase for operations selected by `after_apply`; manual operations are excluded. Apply waits for both previews, applies infrastructure after provider approval, then replans and reconciles the isolated operation state so operation inputs see current outputs. A `plan-operation` request previews an explicitly selected batch, or all operations when names are omitted, without approval or execution. An `apply-operation` request performs the same preview, waits for provider approval, and then executes the batch.

A destroy request first previews infrastructure with `plan --destroy` and previews removal of the complete isolated operation state. After approval, Stacksmith destroys the operation state before infrastructure, preventing stale operation resources from surviving their stack. A failed preview or operation-state cleanup prevents infrastructure destruction. Destroy manifests reject operation-name selection and forced reruns, and the shared policy rejects destructive execution on pull requests or non-default branches. GitHub runs the destructive phases through the selected protected environment; Jenkins uses its explicit `Approve` input. The single-environment workflow is therefore an internal execution primitive; call the opinionated workflow unless you intentionally generate and supply a manifest yourself.

For deployment commands, `stacksmith ci prepare` resolves the effective layered configuration for every selected environment and rejects `backend.type: local`. These CI runs must use a remote backend so state is durable and shared across lifecycle jobs. A standalone `test` command skips backend validation because `stacksmith test` does not read or write infrastructure state.

The deployment CI selector is named `command` in GitHub Actions and `COMMAND` in stack-running Jenkins jobs. Callers using the former `operation` or `OPERATION` selector must update to the new name.

- GitHub Actions `workflow_dispatch` can run all environments, or a comma-delimited subset with `environments`.
- `plan-operation` previews native operations without approval or execution; `apply-operation` previews, requests approval, and executes them. Supply a comma-delimited `operation_names` input such as `publish_image,deploy_app,smoke_test`, or leave it empty to select all operations in each stack. Set the repository, organization, job, or folder variable `STACKSMITH_MAX_PARALLEL_OPERATIONS` to cap concurrency and `STACKSMITH_FORCE_RERUN=1` to force replacement of selected operation runner resources when their execution identities have not changed.
- `discovery_mode` selects how environments are discovered. Use `folders` for `environments/<env>/` directories, `flat-files` for root-level `stacksmith.<env>.yaml|yml|json` files, or `env-files` for the hybrid `environments/<env>.yaml` layout. The aliases `env` and `env-files` both map to the hybrid env-file discovery path.
- In GitHub Actions, `STACKSMITH_GITOPS_ROOT` defaults to `.` and can be overridden per run with `gitops_root`.
- Changes under `<gitops_root>/common` and `<gitops_root>/manifests/common` fan out to all environments.
- Changes under `<gitops_root>/environments/<env>` and `<gitops_root>/manifests/environments/<env>` target only that environment.
- For push events, any changed path that does not map to a discovered environment conservatively selects all environments. Pull requests retain targeted selection and produce a no-op when no changed path maps to an environment.
- Manual `environments` entries must map to discovered environments or selection fails fast.

Both implementations reserve the command selector, operation selection, operation concurrency, runfiles, build directory, plan output, validation format, and apply approval flags because those values are part of the GitOps contract. Every other Stacksmith CLI option can be supplied, in order and without shell-quoting loss, through `STACKSMITH_ARGS_JSON`. For example:

```json
["--vars", "vars/common.yaml", "--var", "replicas=3", "--tag", "service"]
```

Set the reusable workflow's `debug` input, the Jenkins `DEBUG` parameter, or `STACKSMITH_DEBUG=1` to enable debug logging. Debug CI executions also run `stacksmith info modules-and-policies` with the selected environment's managed config and layered runfiles before the requested command.

The `--log` category filter matches logger names exactly; parent names do not automatically include submodules. For example, use `--log stacksmith.ci.service=DEBUG` for logs emitted by `stacksmith.ci.service`, rather than `--log stacksmith.ci=DEBUG`.

Plan, apply, and destroy executions read Stacksmith's source-locking controls exclusively from repository-, organization-, job-, or folder-managed environment settings. Set `STACKSMITH_REQUIRE_LOCKFILE` to require resolved inputs to match a lockfile, optionally set `STACKSMITH_LOCKFILE` to choose a non-default path, and combine `STACKSMITH_OFFLINE` with locked mode to prohibit network resolution. Native operations do not currently accept Stacksmith lock-policy flags.

Plan artifacts produced by the managed GitHub Actions and Jenkins entrypoints are redacted in memory before Stacksmith writes them. Ordinary previews use `plan.json`; destroy previews use the distinct `destroy-plan.json` filename and `stacksmith-destroy-plan-<environment>-<sha>` GitHub artifact name. Both providers attempt to retain the redacted preview and validation report even when the plan phase fails. The archive profile replaces schema-marked sensitive values with `<sensitive>` and omits input variables, configuration expressions, check problem messages, import details, generated configuration, replacement paths, and unrecognized fields because those locations do not consistently carry sensitivity metadata. The resulting JSON is intended for review and diagnostics, not as a complete substitute for raw `tofu show -json` output.

Use `stacksmith ci redact-plan <plan.json> --output <redacted-plan.json>` to sanitize an existing raw plan, or pass `--in-place` to atomically replace it. Keep the input file protected until redaction finishes.

Protect each consumer repository's CI entrypoint with a CODEOWNERS file so ordinary contributors cannot replace or bypass it. GitHub Actions callers can pin the reusable workflow to a release tag. Jenkins consumers keep only a protected call to the centrally managed trusted library in their repository.

#### GitHub Actions

In your own repository, you can do either of the following.

- call `ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>` from your workflow, or
- use the example wrappers as reference for trigger configuration.

The wrappers pass reusable workflow inputs from repository variables when available.

- `STACKSMITH_GITOPS_ROOT` (default `.`)
- `STACKSMITH_DISCOVERY_MODE` (default `auto`; set to `flat-files` for root-level env files, or `env-files` for the hybrid `environments/<env>.yaml` layout)
- `STACKSMITH_WORKDIR` (default `.`)
- `STACKSMITH_ENV_FILE` (default `/dev/null`)
- `STACKSMITH_IMAGE_VERSION` (default `latest`)
- `STACKSMITH_VALIDATION_REPORT_FORMAT` (default `json`, plan template)
- `STACKSMITH_UPLOAD_ARTIFACTS` (default `true`, plan template)
- `STACKSMITH_FAIL_ON_CHANGES` (default `false`, plan template)
- `STACKSMITH_STRICT_VALIDATION_WARNINGS` (default `false`, plan template)
- `STACKSMITH_DEBUG` (default `false`; enables debug logging and the modules-and-policies diagnostic)
- `STACKSMITH_NO_CAS` (default `false`)
- `STACKSMITH_REQUIRE_LOCKFILE` (default `false`; passes `--locked` to plan, apply, and destroy)
- `STACKSMITH_OFFLINE` (default `false`; passes `--offline` to plan, apply, and destroy and requires locked mode)
- `STACKSMITH_LOCKFILE` (default empty; optional explicit lockfile path)
- `STACKSMITH_ARGS_JSON` (default `[]`; ordered JSON array of additional CLI arguments; the workflow rejects managed config and lock-policy overrides)
- `STACKSMITH_CONFIG_REF` (required for the workflow entrypoints; points to the platform-managed Stacksmith config)
- `NO_VALIDATE_BRANCH_AND_OPERATION` (default `false`; bypasses the default-branch/PR operation guard)
- `TG_AUTH_PROVIDER_CMD` (default empty)
- `TG_IAM_ASSUME_ROLE` (default empty)

Credential values are inherited into the reusable workflows with standard GitHub Actions `secrets: inherit`. The supported secret names are `STACKSMITH_GIT_TOKEN`, `STACKSMITH_GIT_SSH_KEY`, `STACKSMITH_HTTP_TOKEN`, `STACKSMITH_HTTP_USERNAME`, `STACKSMITH_HTTP_PASSWORD`, `STACKSMITH_JENKINS_USERNAME`, and `STACKSMITH_JENKINS_API_TOKEN`.

The GitHub workflows expose this as their `stacksmith_args_json` input. JSON arrays are used so repeated options, argument order, and values containing whitespace are preserved exactly. The workflow requires the platform-managed config reference in `STACKSMITH_CONFIG_REF`, injects it as `--config <ref>` for every Stacksmith invocation, and rejects attempts to override the managed config or lock policy through `stacksmith_args_json`.

The opinionated reusable workflow exposes `debug` as an optional input. `STACKSMITH_CONFIG_REF`, `STACKSMITH_REQUIRE_LOCKFILE`, `STACKSMITH_OFFLINE`, and `STACKSMITH_LOCKFILE` are intentionally unavailable as workflow inputs so callers cannot override organization or repository policy per run.

##### Consumer quickstart

Call the opinionated reusable workflow from your repository using `uses:`. Keep triggers and approval policies local and delegate discovery + per-environment execution to the reusable workflow here.

Plan on PR/manual (minimal example).

```yaml
name: stacksmith-plan

on:
  pull_request:
    branches: [main]
  workflow_dispatch: {}

jobs:
  run-plan:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: plan
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      environments: ${{ github.event.inputs.environments || '' }}
      discovery_mode: ${{ vars.STACKSMITH_DISCOVERY_MODE || 'auto' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

Apply on push/manual (minimal example).

```yaml
name: stacksmith-apply

on:
  push: {}
  workflow_dispatch: {}

jobs:
  run-apply:
    if: ${{ github.event_name == 'workflow_dispatch' || github.ref_name == github.event.repository.default_branch }}
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: apply
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      environments: ${{ github.event.inputs.environments || '' }}
      discovery_mode: ${{ vars.STACKSMITH_DISCOVERY_MODE || 'auto' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

The apply wrapper observes every push so repository-specific path conventions cannot prevent reconciliation from starting. The job gate runs automatic applies only for the repository's current default branch. Once started, Stacksmith's changed-path discovery narrows execution to affected environments; if any push path is unrecognized, it conservatively reconciles every environment.

Keep destruction manual and require the operator to name the target environments. The complete template is [`examples/github-actions/stacksmith-destroy.yml`](./examples/github-actions/stacksmith-destroy.yml).

```yaml
name: stacksmith-destroy

on:
  workflow_dispatch:
    inputs:
      environments:
        description: Comma-separated environment names to destroy.
        required: true
        type: string

jobs:
  destroy:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: destroy
      environments: ${{ inputs.environments }}
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

Dispatch the workflow from the repository's default branch. Configure GitHub environment protection for every destroyable environment so operation-state cleanup and infrastructure destruction receive the required reviewers.

Run a native operation manually with this minimal example.

```yaml
name: stacksmith-operation

on:
  workflow_dispatch:
    inputs:
      operation_names:
        description: Comma-delimited stack-local native operation names. Leave empty to run all.
        required: false
        type: string

jobs:
  run-operation:
    uses: ci-sourcerer/stacksmith/.github/workflows/stacksmith-gitops-opinionated-reusable.yml@<version>
    with:
      command: apply-operation
      operation_names: ${{ inputs.operation_names || '' }}
      gitops_root: ${{ vars.STACKSMITH_GITOPS_ROOT || '.' }}
      workdir: ${{ vars.STACKSMITH_WORKDIR || '.' }}
    secrets: inherit
```

> ℹ️ **Tip:** Pin the `uses:` reference to a release tag for stable downstream usage.

The reusable workflow also supports the `folders` and `flat-files` discovery modes for repositories that prefer those layouts.

#### Jenkins

Configure this repository as a trusted global Pipeline library named `stacksmith`, with `jenkins` as its library path and a release tag as its default version. The trusted [`stacksmith.groovy`](./jenkins/vars/stacksmith.groovy) global variable owns the complete pipeline implementation.

Each consuming repository needs only this `Jenkinsfile`.

```groovy
@Library('stacksmith') _

stacksmith()
```

Configure a Jenkins Multibranch Pipeline with that file as its pipeline script path. Protect it from unapproved changes with the repository's CODEOWNERS file, replacing the example team with the platform team that owns the pipeline.

```text
/Jenkinsfile @my-org/platform
```

The entrypoint supports two distinct pipeline modes. Set the job or folder environment variable `STACKSMITH_TEST_PIPELINE` to a truthy value for a test-only pipeline. That mode always prepares a test manifest and runs only `stacksmith test` in the `Test` stage; it tests the managed Stacksmith configuration rather than Stacksmith's own Python unit-test suite. When the variable is unset or false, the job is a stack-running pipeline with no test command. Plan jobs run in the `Plan` stage. Apply jobs preview infrastructure and `after_apply` operations, request approval, apply infrastructure, and then reconcile operations. Explicit operation batches run through `Plan operation(s)`, `Approve`, and `Run operation(s)`, so an invalid operation plan cannot reach approval or execution. Destroy jobs preview infrastructure and operation-state removal, request approval, run `Destroy operation state`, and reach `Destroy` only when cleanup succeeds. Both modes check out the branch, prepare their CI manifest once, run each selected environment in parallel, and map Jenkins-native context including `CHANGE_ID`, `CHANGE_TARGET`, `GIT_PREVIOUS_COMMIT`, `GIT_COMMIT`, and `BRANCH_NAME` to the shared adapter inputs automatically. Redacted infrastructure plan JSON and validation reports are archived when artifact uploads are enabled.

When the Folder Properties plugin provides `withFolderProperties`, `stacksmith()` loads those properties around the complete pipeline. Otherwise, it uses the job environment directly.

Choose one execution mode through Jenkins folder properties or the job environment.

- Set `STACKSMITH_USE_K8S` to a truthy value to run in a Kubernetes-plugin pod.
- Set `STACKSMITH_NODE_LABEL` to run directly on that labeled agent.
- Otherwise, the pipeline runs in a Docker container on any available agent, or on `STACKSMITH_DOCKER_NODE` when set.

Both Jenkins pipeline modes expose these parameters.

- `ENVIRONMENTS`: Optional comma-separated list of environment names.
- `WORKDIR`: Working directory for Stacksmith commands. Defaults to `.`.
- `DEBUG`: Enable debug logging and print configured modules and policies before execution. Defaults to `false`.

Stack-running pipelines additionally expose these parameters.

- `COMMAND`: `plan`, `apply`, `destroy`, `plan-operation`, or `apply-operation`. Defaults to `plan`.
- `OPERATION_NAMES`: Comma-delimited operation names for a dependency-aware batch. Leave empty to select all operations for `plan-operation` or `apply-operation`.
- `FAIL_ON_CHANGES`: Fail a plan containing resource changes. Defaults to `false`.
- `STRICT_VALIDATION_WARNINGS`: Treat plan validation warnings as failures. Defaults to `false`.

Configure these values as Jenkins folder properties or job environment variables when needed:

- `STACKSMITH_IMAGE`: Full image for Kubernetes and Docker modes. When unset, the image is `docker.io/cisourcerer/stacksmith:<STACKSMITH_IMAGE_VERSION>`.
- `STACKSMITH_IMAGE_VERSION`: Image tag when `STACKSMITH_IMAGE` is unset. Defaults to `latest`.
- `STACKSMITH_TEST_PIPELINE`: Set to a truthy value to make the job a test-only pipeline. The `COMMAND` parameter is not exposed in this mode and cannot override the test command.
- `STACKSMITH_GITOPS_ROOT`: GitOps root for discovery. Defaults to `WORKDIR` in Jenkins.
- `STACKSMITH_DISCOVERY_MODE`: `auto`, `folders`, `flat-files`, or `env-files`. Defaults to `auto`.
- `STACKSMITH_MAX_PARALLEL_OPERATIONS`: Maximum independent operations planned or run concurrently within each environment. Defaults to `10`.
- `STACKSMITH_ENV_FILE`: Env file passed to Stacksmith. Defaults to `/dev/null` to prevent implicit `.env` loading.
- `STACKSMITH_CONFIG_REF`: Required platform-managed Stacksmith config reference.
- `STACKSMITH_DEBUG`: Environment equivalent for the `DEBUG` parameter. A truthy value enables debug mode even when the build parameter is false.
- `STACKSMITH_REQUIRE_LOCKFILE`, `STACKSMITH_OFFLINE`, and `STACKSMITH_LOCKFILE`: Job- or folder-managed source-locking policy. These settings are intentionally not exposed as build parameters.
- `STACKSMITH_NO_CAS`, `STACKSMITH_FORCE_RERUN`, `STACKSMITH_VALIDATION_REPORT_FORMAT`, `STACKSMITH_UPLOAD_ARTIFACTS`, and `STACKSMITH_ARGS_JSON`: Shared execution settings with the same behavior described above. `STACKSMITH_ARGS_JSON` must be an ordered JSON array and cannot override the managed config or lock policy. In test-only mode, it can include explicit `tests.yaml` paths and pytest arguments after `--`.
- `NO_VALIDATE_BRANCH_AND_OPERATION`: Set to `true` to bypass the shared default-branch and pull-request operation guard.
- `STACKSMITH_DEFAULT_BRANCH` or `BRANCH_IS_PRIMARY`: Branch-policy context when Jenkins does not provide it.
- `TG_AUTH_PROVIDER_CMD` and `TG_IAM_ASSUME_ROLE`: Optional Terragrunt authentication settings.

Bind remote-source credentials through `STACKSMITH_CREDENTIALS_JSON`. Provide a JSON array of credential objects, each with a Jenkins credential ID, required type, and optional variable name overrides.

**Basic form** (uses automatic variable naming from credential ID):

```json
[
  {"credentialId": "my-git-token", "type": "string"},
  {"credentialId": "my-http-basic", "type": "usernamePassword"}
]
```

This generates environment variables: `STACKSMITH_MY_GIT_TOKEN` and `STACKSMITH_MY_HTTP_BASIC` (credentialId uppercased with dashes replaced by underscores).

**With explicit variable names** (full control):

```json
[
  {
    "credentialId": "my-git-ssh",
    "type": "sshUserPrivateKey",
    "keyFileVariable": "MY_SSH_KEY",
    "usernameVariable": "MY_SSH_USER"
  },
  {
    "credentialId": "my-http-basic",
    "type": "usernamePassword",
    "usernameVariable": "CUSTOM_HTTP_USER",
    "passwordVariable": "CUSTOM_HTTP_PASS"
  },
  {
    "credentialId": "my-token",
    "type": "string",
    "variable": "MY_CUSTOM_TOKEN"
  }
]
```

Each credential object supports the following.

- `credentialId` (required): Jenkins credential ID to bind
- `type` (required): Credential type. Supported types:
  - `string`, `secret_text`, `git_token`, `http_token`: Token/secret credentials
  - `usernamePassword`, `http_basic`: Username and password credentials
  - `sshUserPrivateKey`, `git_ssh_key`: SSH key credentials
- `variable` (optional): Environment variable name for token/string credentials (default: `STACKSMITH_<CREDENTIALID_UPPERCASE>` with dashes replaced by underscores)
- `usernameVariable`, `passwordVariable` (optional): Variable names for username/password credentials (defaults: `STACKSMITH_<CREDENTIALID_UPPERCASE>_USERNAME`, `STACKSMITH_<CREDENTIALID_UPPERCASE>_PASSWORD`)
- `keyFileVariable` (optional): Variable name for SSH key path (default: `STACKSMITH_<CREDENTIALID_UPPERCASE>_KEY`)

This example now also shows app deployment and native operation patterns alongside infrastructure stacks. The shared config can expose approved Terraform component types such as `helm_app` and `k8s_app`, plus approved operations for local commands and Jenkins builds.

### Python API

Stacksmith exposes a stable Python API for applications, automation, and CI systems that need the same behavior as the CLI without launching a subprocess. Import supported names directly from `stacksmith`; submodules and names that begin with an underscore are implementation details.

| Function | Purpose |
| - | - |
| `validate_stack` | Validate a stack and its resolved inputs, returning a structured report. |
| `generate_stack` | Generate the OpenTofu and Terragrunt files for a stack. |
| `lock_stack` | Create or verify a deterministic Stacksmith lockfile. |
| `run_stack_action` | Generate one stack and run a Terragrunt action. |
| `run_all_stacks` | Discover or select stacks and run an action in dependency order. |
| `prepare_ci_execution` | Build a provider-neutral CI execution manifest. |
| `redact_plan` | Create an archive-safe copy of a parsed OpenTofu plan. |
| `redact_plan_file` | Redact an OpenTofu plan JSON file into an archive-safe artifact. |

The workflow functions accept the same layered stack, managed-config, variable, targeting, cache, and validation options used by the corresponding CLI commands. Execution functions return process-style exit codes, while generation returns the generated directory.

Direct `generate_stack` and `run_stack_action` calls warn unless `locked=True` or `offline=True` enables lockfile enforcement. Set `STACKSMITH_WARN_ON_UNLOCKED=0` when an embedding application intentionally manages reproducibility another way.

```python
import sys
from pathlib import Path

from stacksmith import run_stack_action

exit_code = run_stack_action(
    "plan",
    "stack.yaml",
    config=["stacksmith-config.yaml"],
    save_redacted_plan_json=Path("artifacts/plan.json"),
)
sys.exit(exit_code)
```

The top-level package also exports Stacksmith's exception types, merge-policy models, `StacksmithTestRunner`, and the GitOps change helpers described below.

#### GitOps change helpers

Stacksmith offers small, importable helpers for automated GitOps changes. They validate edited stack documents before leaving a change on disk, and `commit_and_push` stages and commits only the paths supplied by the caller.

What they do is easily implemented with your own git commands, but these helpers are simply convenient for Python-based automation scripts.

```python
from stacksmith import request_operation_rerun

result = request_operation_rerun(
    repo_path=".",
    stack_path="stacks/app.yaml",
    operation="deploy_app",
    push=False,
)
print(result.rerun_token)
```

Use `update_operation_rerun_token`, `set_operation_inputs`, and `update_component_properties` when mutation and Git publication should be controlled separately. YAML edits retain content outside the modified value; comments within a replaced mapping may be reformatted or removed.

The Jenkins and GitHub Actions GitOps entrypoints also support native operation batches. Use `COMMAND=plan-operation` for a dry run or `COMMAND=apply-operation` for an approved execution in Jenkins; use the corresponding reusable workflow `command` value in GitHub Actions. Provide comma-delimited names through `OPERATION_NAMES` or `operation_names`, or leave the value empty to select all operations. Set `STACKSMITH_FORCE_RERUN=1` in Jenkins folder properties or GitHub repository variables for a definite dispatch. Native operations use the same environment discovery, runfile layering, credentials, branch protections, and deployment approvals as infrastructure applies.

In this pattern, the shared runfile references the platform and service stack layers first, then environment-specific vars and overlays are layered on top.

```yaml
merge_mode: deep
configs:
  - source: local
    data:
      path: examples/gitops-repo/common/stacksmith.yaml
vars:
  - source: local
    data:
      path: examples/gitops-repo/vars/vars.dev.yaml
```

For production use, add GitHub Environment protections and secrets per environment. The reusable workflow completes the unprotected plan phase first, then maps each apply phase to the matching GitHub Environment so approvals and scoped credentials gate deployment after the plan is available.

The opinionated workflow resolves `STACKSMITH_ENV_FILE` from repository variables and falls back to `/dev/null` so CI runs are deterministic and do not implicitly load repository `.env` values.

> ⚠️ **Warning:** After the preview plan and approval, Stacksmith's GitOps workflows execute a fresh generation and execution of `terragrunt apply --auto-approve` directly against the latest tip of the target branch, rather than applying a pre-saved static plan binary. If you wish to use exact plan binaries natively, ensure you orchestrate `stacksmith plan --out target.tfplan` across your stacks, and push them to storage prior to leveraging `stacksmith apply --plan target.tfplan`.
>
> - **Concurrent Merges:** If another PR is merged after your PR's plan runs but before it is applied, the apply run will execute with the latest configurations of the target branch, which may differ from the approved plan.
> - **External State Changes:** If resources are modified out-of-band in the cloud provider, the apply step will reflect those updates.
> - **Dynamic Configurations:** If you reference dynamic data sources or remote modules with moving targets (e.g., untagged Git references or floating version constraints), the resolved files might differ between plan and apply execution.
>
> To mitigate this risk, do the following.
>
> 1. Enforce linear history or require branches to be up-to-date before merging in your repository settings (via GitHub Branch Protection)
> 2. Ensure all remote resources, configurations, and provider mappings use **immutable version pins** (exact commits or tags) rather than moving refs (like `main` or `latest`).

### Docker

A Docker image is provided that bundles OpenTofu and Terragrunt so no local installation is required. It is also especially useful for CI environments.

As this project is reliant on [Common Python Tasks](https://github.com/ci-sourcerer/common-python-tasks), you can build the image with a simple command: `poe build-image`. You can pass `--build-args TOFU_PROVIDER_SPEC="hashicorp/aws=6.41.0:hashicorp/random=3.8.1"`, for example, to pre-install some OpenTofu providers into the image. This can drastically speed up Stacksmith runs for your users, which is especially helpful in CI environments. By default, the image includes no providers, so OpenTofu will download them on demand during execution.

> ⚠️ **WARNING:** `TOFU_PROVIDER_SPEC` is a shared provider cache keyed by provider version, not by OpenTofu version. If you build or run images with multiple OpenTofu versions, pre-cached providers may not be compatible with an older runtime unless you explicitly pin and pre-cache every provider version needed by those tool versions.

#### Pre-installing modules

Similarly to providers, you can pre-install OpenTofu modules into the image using the `TOFU_MODULE_SPEC` build arg. This is a colon-separated list of `source=version-or-ref` pairs that match the sources and exact versions or Git refs in your managed config.

```shell
poe build-image --build-args TOFU_MODULE_SPEC="https://github.com/org/terraform-aws-s3.git=v3.2.1:https://github.com/org/terraform-aws-ec2.git=v5.0.0"
```

When modules are vendored in the image, Stacksmith automatically rewrites module sources in the generated `stacksmith.tf.json` to point to the local vendored copies instead of remote URLs. This eliminates network fetches during `tofu init` and ensures immutable, reproducible builds.

Each module is stored under a deterministic directory name derived from `sha256("<source>|<version>")[:16]`, and a `vendor-manifest.json` is written alongside the directories for reverse lookup.

#### Controlling local module rewriting

Local module rewriting (requiring local vendored modules) is controlled by the `STACKSMITH_ONLY_USE_LOCAL_MODULES` environment variable and the `--use-local-modules` / `--no-local-modules` CLI flags.

| Control | Effect |
| - | - |
| `STACKSMITH_ONLY_USE_LOCAL_MODULES=1` | Enable local module rewriting |
| `--use-local-modules` | Enable explicitly from the CLI |
| `--no-local-modules` | Disable even when the env var is set |
| `STACKSMITH_VENDOR_DIR=<path>` | Override the local vendored module root directory |

If a vendored module directory is missing at generation time, Stacksmith fails fast with a clear error rather than silently falling back to remote fetching.

#### Extracting the module and provider specs from config

The following recipe uses `yq` to extract module and provider specs from a managed config file and pass them directly to `poe build-image`. `TOFU_PROVIDER_SPEC` uses colon-separated (`:`) `source=version` items, while `TOFU_MODULE_SPEC` uses `source=version-or-ref` items. Provider version ranges that include commas, such as `>= 6.39, < 7.0`, are supported. Local module mappings are excluded because they are already filesystem paths rather than dependencies that OpenTofu can pre-fetch.

This extraction only includes explicit module mappings. A templated default mapping represents an open-ended set of sources and cannot be pre-vendored without knowing the stack components that will use it. When local-module-only execution is required, add explicit mappings for every component type that must be included in the image.

```shell
stacksmithConfigPath=<path to stacksmith-config.yaml>
poe build-image \
  --build-args \
    "TOFU_MODULE_SPEC=$(yq -r '
      .module_mappings
      | to_entries
      | map(
          (
            select(.value.source.source == "git")
            | .value.source.data.repo
              + ((.value.source.data.path | select(. != null) | "//" + .) // "")
              + "=" + .value.source.data.ref
          ),
          (
            select(.value.source.source == "registry")
            | .value.source.data.address + "=" + .value.source.data.version
          )
        )
      | join(":")
    ' "$stacksmithConfigPath")" \
    "TOFU_PROVIDER_SPEC=$(yq -r '
      .provider_mappings
      | to_entries
      | map("\(.value.source.data.address)=\(.value.source.data.version)")
      | join(":")
    ' "$stacksmithConfigPath")"
```

## Reference

### CLI reference

<!-- BEGIN GENERATED CLI REFERENCE -->
Single-stack commands default to `stack.yaml` in the current directory, with fallback to `stack.yml` then `stack.json`, when neither `--stack`, `STACKSMITH_STACK`, nor `stacksmith.yaml` supplies stack refs.

### `stacksmith`

```text
stacksmith [-h] [--version]
                  {validate,generate,lock,test,run-all,init,plan,apply,destroy,operation,info,ci} ...
```

YAML/JSON-driven Terragrunt wrapper

| Argument | Description |
| - | - |
| `--version` | show program's version number and exit |

#### Commands

| Command | Description |
| - | - |
| `validate` | Validate stack schema and variables |
| `generate` | Generate .tf.json and terragrunt.hcl.json |
| `lock` | Resolve stack inputs and write a deterministic lockfile |
| `test` | Run declarative tests.yaml manifests for managed config layers |
| `run-all` | Discover all stacks and run terragrunt run-all |
| `init` | Generate + terragrunt init |
| `plan` | Generate + terragrunt plan |
| `apply` | Generate + terragrunt apply |
| `destroy` | Generate + terragrunt destroy |
| `operation` | Plan, run, or destroy native operations approved by managed configuration |
| `info` | Show stacksmith inspection and diagnostics commands |
| `ci` | Prepare, inspect, and execute CI workflows |

### `stacksmith validate`

```text
stacksmith validate [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                           [--strict-validation-warnings] [--use-local-modules | --no-local-modules]
                           [--debug | -q] [--validation-report-format {json}]
                           [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |

### `stacksmith generate`

```text
stacksmith generate [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                           [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                           [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                           [--strict-validation-warnings] [--use-local-modules | --no-local-modules]
                           [--debug | -q] [--locked] [--offline] [--lockfile LOCKFILE]
                           [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith lock`

```text
stacksmith lock [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--lockfile LOCKFILE]
                       [--check]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |
| `--check` | Verify that the existing lockfile matches current resolved inputs. |

### `stacksmith test`

```text
stacksmith test [-h] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                       [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                       [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                       --no-local-modules] [--debug | -q] [--dump-tests DUMP_TESTS]
                       [test_path ...]
```

| Argument | Description |
| - | - |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `test_path` | Optional tests.yaml paths or directories. Defaults to tests.yaml beside each selected config layer. |
| `--dump-tests` | Write generated pytest code to this path before execution. |

### `stacksmith run-all`

```text
stacksmith run-all [-h] [--root ROOT] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                          [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                          [--strict-validation-warnings] [--use-local-modules | --no-local-modules] [--debug |
                          -q] [--validation-report-format {json}] [--destroy]
                          [--save-plan-json SAVE_PLAN_JSON |
                          --save-redacted-plan-json SAVE_REDACTED_PLAN_JSON] [--out OUT] [--fail-on-changes]
                          [--plan PLAN] [--no-after-apply] [--tag TAG] [--tag-expr TAG_EXPR]
                          [--include-tag INCLUDE_TAG] [--exclude-tag EXCLUDE_TAG] [--clean] [--auto-approve]
                          [--dry-run] [--format {table,json}]
                          {init,plan,apply,destroy}
```

| Argument | Description |
| - | - |
| `action` | Terragrunt action to run across all stacks. Choices: `init`, `plan`, `apply`, `destroy`. |
| `--root` | Root directory to discover stacks in (default: current working directory) |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save raw rendered plan JSON to the given file or directory. The raw document can contain sensitive values. |
| `--save-redacted-plan-json` | Save archive-safe redacted plan JSON to the given file or directory. |
| `--out` | Save generated execution plan to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--plan` | Path or directory to a pre-generated execution plan to apply. |
| `--no-after-apply` | When applying infrastructure, do not automatically reconcile operations configured with trigger: after_apply. Use a separate operation run phase instead. |
| `--tag` | Select components by tag. Repeat to require multiple tags. Supported for run-all plan/apply/destroy. |
| `--tag-expr` | JMESPath expression used to select resource targets. Supported for run-all plan/apply/destroy. |
| `--include-tag` | Include stacks that have this tag. Repeatable. |
| `--exclude-tag` | Exclude stacks that have this tag. Repeatable. |
| `--clean` | Remove existing build output directory before generation |
| `--auto-approve` | Skip interactive approval for apply/destroy |
| `--dry-run` | Preview discovery, validation, targeting, and commands without writing generated files or invoking Terragrunt. |
| `--format` | Output format for dependency and execution preview data. Choices: `table`, `json`. |

### `stacksmith init`

```text
stacksmith init [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--locked] [--offline]
                       [--lockfile LOCKFILE]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith plan`

```text
stacksmith plan [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                       [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                       [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                       [--use-local-modules | --no-local-modules] [--debug | -q] [--destroy]
                       [--save-plan-json SAVE_PLAN_JSON | --save-redacted-plan-json SAVE_REDACTED_PLAN_JSON]
                       [--out OUT] [--fail-on-changes] [--tag TAG] [--tag-expr TAG_EXPR]
                       [--validation-report-format {json}] [--locked] [--offline] [--lockfile LOCKFILE]
                       [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--destroy` | Plan destroy operations instead of a create/update when action is plan. |
| `--save-plan-json` | Save raw rendered plan JSON to the given file or directory. The raw document can contain sensitive values. |
| `--save-redacted-plan-json` | Save archive-safe redacted plan JSON to the given file or directory. |
| `--out` | Save generated execution plan to the given file or directory. |
| `--fail-on-changes` | Return a non-zero exit code if the plan contains any resource changes. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--validation-report-format` | Format for machine-readable validation reports emitted by validate, plan, and run-all plan. Choices: `json`. |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith apply`

```text
stacksmith apply [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                        [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                        [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                        [--use-local-modules | --no-local-modules] [--debug | -q] [--plan PLAN]
                        [--no-after-apply] [--tag TAG] [--tag-expr TAG_EXPR] [--auto-approve] [--locked]
                        [--offline] [--lockfile LOCKFILE]
                        [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--plan` | Path or directory to a pre-generated execution plan to apply. |
| `--no-after-apply` | When applying infrastructure, do not automatically reconcile operations configured with trigger: after_apply. Use a separate operation run phase instead. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--auto-approve` | Skip interactive approval |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith destroy`

```text
stacksmith destroy [-h] [--stack STACK] [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE]
                          [--vars VARS_FILE] [--var VARS] [--merge-mode {deep,override}]
                          [--build-dir BUILD_DIR] [--log LOG] [--no-cache] [--no-cas]
                          [--strict-validation-warnings] [--use-local-modules | --no-local-modules] [--debug |
                          -q] [--tag TAG] [--tag-expr TAG_EXPR] [--auto-approve] [--locked] [--offline]
                          [--lockfile LOCKFILE]
                          [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--tag` | Select components by tag. Repeat to require multiple tags. |
| `--tag-expr` | JMESPath expression used to select resource targets. |
| `--auto-approve` | Skip interactive approval |
| `--locked` | Require inputs to match lockfile entries. |
| `--offline` | Require locked artifacts to be available locally without network access. |
| `--lockfile` | Path to stacksmith.lock.yaml. When omitted, Stacksmith resolves the default location beside the primary runfile or stack file. |

### `stacksmith operation plan`

```text
stacksmith operation plan [-h] [--after-apply] [--destroy] [--force-rerun] [--stack STACK]
                                 [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                                 [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR]
                                 [--log LOG] [--no-cache] [--no-cas] [--strict-validation-warnings]
                                 [--use-local-modules | --no-local-modules] [--debug | -q]
                                 [operation_names] [stack_file]
```

| Argument | Description |
| - | - |
| `operation_names` | Comma-delimited stack-local operation names. Omit to select all operations declared by the stack. |
| `--after-apply` | Select only operations configured with the after_apply trigger. |
| `--destroy` | Plan destruction of the complete isolated operation state. |
| `--force-rerun` | Force the operation runner resource to be replaced even when its execution identity has not changed. Can also be enabled with STACKSMITH_FORCE_RERUN=1. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith operation run`

```text
stacksmith operation run [-h] [--force-rerun] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                                [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                                --no-local-modules] [--debug | -q]
                                [operation_names] [stack_file]
```

| Argument | Description |
| - | - |
| `operation_names` | Comma-delimited stack-local operation names. Omit to select all operations declared by the stack. |
| `--force-rerun` | Force the operation runner resource to be replaced even when its execution identity has not changed. Can also be enabled with STACKSMITH_FORCE_RERUN=1. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith operation destroy`

```text
stacksmith operation destroy [-h] [--auto-approve] [--stack STACK] [--runfile RUNFILE] [-c CONFIG]
                                    [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                    [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                    [--no-cache] [--no-cas] [--strict-validation-warnings]
                                    [--use-local-modules | --no-local-modules] [--debug | -q]
                                    [stack_file]
```

| Argument | Description |
| - | - |
| `--auto-approve` | Apply the generated operation-state destruction plan without prompting. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info modules-and-policies`

```text
stacksmith info modules-and-policies [-h] [--format {table,json}] [--basic] [--runfile RUNFILE]
                                            [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                            [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                            [--no-cache] [--no-cas] [--strict-validation-warnings]
                                            [--use-local-modules | --no-local-modules] [--debug | -q]
                                            [component_type ...]
```

| Argument | Description |
| - | - |
| `component_type` | Component type(s) to inspect. Inspects all when omitted. |
| `--format` | Output format (default: table). Choices: `table`, `json`. |
| `--basic` | Show only input, validation, and transform columns in the module table. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info diagnose`

```text
stacksmith info diagnose [-h] [--stack STACK] [--format {table,json}] [--verbose] [--runfile RUNFILE]
                                [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE] [--var VARS]
                                [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                                [--no-cache] [--no-cas] [--strict-validation-warnings] [--use-local-modules |
                                --no-local-modules] [--debug | -q]
                                [stack_file]
```

| Argument | Description |
| - | - |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `stack_file` | Optional path to stack.yaml, stack.yml, or stack.json. When omitted, stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml. |
| `--format` | Output format for diagnostics. Choices: `table`, `json`. |
| `--verbose` | Show additional description metadata in table output. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--strict-validation-warnings` | Treat warning outcomes from plan validations as failures. This only affects plan and run-all plan commands. |
| `--use-local-modules` | Rewrite module sources to local vendored paths instead of remote URLs. Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. |
| `--no-local-modules` | Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |

### `stacksmith info graph`

```text
stacksmith info graph [-h] [--action {plan,apply,destroy}] [--root ROOT] [--stack STACK]
                             [--runfile RUNFILE] [-c CONFIG] [--env-file ENV_FILE] [--vars VARS_FILE]
                             [--var VARS] [--merge-mode {deep,override}] [--build-dir BUILD_DIR] [--log LOG]
                             [--no-cache] [--no-cas] [--debug | -q] [--tag TAG] [--tag-expr TAG_EXPR]
                             [--include-tag INCLUDE_TAG] [--exclude-tag EXCLUDE_TAG] [--destroy] [--verbose]
                             [--format {table,json,dot,mermaid}]
```

| Argument | Description |
| - | - |
| `--action` | Terragrunt action used to compute commands and execution order. Choices: `plan`, `apply`, `destroy`. |
| `--root` | Root directory used to discover stacks. |
| `--stack` | Path or URL to a stack definition file. Repeat to deep-merge multiple stack layers for single-stack commands, or to target explicit stacks for run-all. |
| `--runfile` | Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; later files override earlier scalar values, dicts merge recursively, and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, otherwise ./stacksmith.yaml is auto-detected when present. |
| `-c, --config` | Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. If omitted, STACKSMITH_CONFIG can provide one or more paths separated by ':'. |
| `--env-file` | Load environment variables from a .env file before resolving config and variables. Repeat to layer multiple env files; later files override earlier env-file values, while pre-existing environment variables are preserved. |
| `--vars` | Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; later files override earlier scalar values, dicts merge recursively, and lists append. Supports http(s):// and git+ URLs. |
| `--var` | Variable override in key=value format (repeatable) |
| `--merge-mode` | Merge strategy for layered stacks, configs, and vars. Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones. Choices: `deep`, `override`. |
| `--build-dir` | Build output directory (default: .stacksmith/ alongside stack file) |
| `--log` | Set per-category logging levels in the form 'category=LEVEL'. Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. CATEGORY is typically one of stacksmith.api, stacksmith.ci, stacksmith.cli.args, stacksmith.cli.main, stacksmith.generation, stacksmith.gitops, stacksmith.inspector, stacksmith.introspection, stacksmith.loading, stacksmith.remote, stacksmith.runner, stacksmith.testing, stacksmith.utils, stacksmith.validations, stacksmith.vendor, or any Python logger name (for example, urllib3). |
| `--no-cache` | Force re-fetch of remote Stacksmith resources, ignoring local cache. For runtime commands (plan/apply/destroy/init/run-all), this also disables Terragrunt CAS. |
| `--no-cas` | Disable Terragrunt CAS for this run. By default, CAS is enabled in Terragrunt >= 1.1.0. |
| `--debug` | Enable debug logging. Can also be enabled via STACKSMITH_DEBUG=1. |
| `-q, --quiet` | Suppress non-error stacksmith logs while still streaming Terragrunt output. |
| `--tag` | Select components by tag. Repeat to require multiple tags. Supported for graph plan/apply/destroy previews. |
| `--tag-expr` | JMESPath expression used to select resource targets. Supported for graph plan/apply/destroy previews. |
| `--include-tag` | Include stacks that have this tag. Repeatable. |
| `--exclude-tag` | Exclude stacks that have this tag. Repeatable. |
| `--destroy` | Preview a destroy plan when the selected action is plan. |
| `--verbose` | Show additional description metadata in table output. |
| `--format` | Output format for dependency and execution preview data. Choices: `table`, `json`, `dot`, `mermaid`. |

### `stacksmith ci environments`

```text
stacksmith ci environments [-h] [--gitops-root GITOPS_ROOT]
                                  [--discovery-mode {folders,flat-files,env-files,env,auto}]
                                  [--environments ENVIRONMENTS] [--event-name EVENT_NAME]
                                  [--changed-path CHANGED_PATH] [--base-ref BASE_REF] [--before BEFORE]
                                  [--after AFTER] [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--event-name` | Optional caller event name used for event-aware selection. |
| `--changed-path` | Changed repository path used for selection simulation. Repeatable. |
| `--base-ref` | Base branch name used for pull-request diff selection. |
| `--before` | Previous commit SHA used for push diff selection. |
| `--after` | Current commit SHA used for push diff selection. |
| `--format` | Output format for environment preview data. Choices: `table`, `json`. |

### `stacksmith ci validate`

```text
stacksmith ci validate [-h] [--gitops-root GITOPS_ROOT]
                              [--discovery-mode {folders,flat-files,env-files,env,auto}]
                              [--environments ENVIRONMENTS] [--workflow-runfile WORKFLOW_RUNFILE]
                              [--workflow-env-file WORKFLOW_ENV_FILE]
                              [--workflow-validation-report-format WORKFLOW_VALIDATION_REPORT_FORMAT]
                              [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--workflow-runfile` | Optional runfile path to validate for CI invocations. |
| `--workflow-env-file` | Env file path to validate for CI invocations. Use /dev/null to represent deterministic no-env-file mode. |
| `--workflow-validation-report-format` | Validation report format value to validate for CI plan runs. |
| `--format` | Output format for CI validation results. Choices: `table`, `json`. |

### `stacksmith ci prepare`

```text
stacksmith ci prepare [-h] [--gitops-root GITOPS_ROOT]
                             [--discovery-mode {folders,flat-files,env-files,env,auto}]
                             [--environments ENVIRONMENTS] [--event-name EVENT_NAME]
                             [--changed-path CHANGED_PATH] [--base-ref BASE_REF] [--before BEFORE]
                             [--after AFTER]
                             --command {test,plan,apply,destroy,plan-operation,apply-operation}
                             [--operation-names OPERATION_NAMES] --config-ref CONFIG_REF [--workdir WORKDIR]
                             [--env-file ENV_FILE] [--stacksmith-args-json STACKSMITH_ARGS_JSON] [--debug]
                             [--no-cas] [--locked] [--offline] [--lockfile LOCKFILE] [--force-rerun]
                             [--validation-report-format {json}] [--fail-on-changes]
                             [--strict-validation-warnings] [--ref-name REF_NAME]
                             [--default-branch DEFAULT_BRANCH] [--is-primary-branch {true,false}]
                             [--skip-branch-validation] [--format {table,json}]
```

| Argument | Description |
| - | - |
| `--gitops-root` | Relative path to the GitOps root folder. |
| `--discovery-mode` | Environment discovery mode. Use folders, flat-files, or env-files (env is an alias for env-files). Choices: `folders`, `flat-files`, `env-files`, `env`, `auto`. |
| `--environments` | Optional comma-separated environment names to target manually. |
| `--event-name` | Optional caller event name used for event-aware selection. |
| `--changed-path` | Changed repository path used for selection simulation. Repeatable. |
| `--base-ref` | Base branch name used for pull-request diff selection. |
| `--before` | Previous commit SHA used for push diff selection. |
| `--after` | Current commit SHA used for push diff selection. |
| `--command` | Stacksmith command to execute for each selected environment. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `apply-operation`. |
| `--operation-names` | Comma-delimited stack-local operation names. Empty selects all for plan-operation and apply-operation commands. |
| `--config-ref` | Platform-managed Stacksmith config reference. |
| `--workdir` | Working directory relative to the checked-out repository. |
| `--env-file` | Environment file path, or /dev/null to disable implicit loading. |
| `--stacksmith-args-json` | JSON array of additional Stacksmith command-line arguments. |
| `--debug` | Enable debug logging and print configured modules and policies before each execution. |
| `--no-cas` | Disable content-addressable caching for generated runtime commands. |
| `--locked` | Require runtime inputs to match the Stacksmith lockfile. |
| `--offline` | Resolve locked remote inputs without network access. |
| `--lockfile` | Optional explicit Stacksmith lockfile path. |
| `--force-rerun` | Force native operation execution even when its identity is unchanged. |
| `--validation-report-format` | Validation report format for plan executions. Choices: `json`. |
| `--fail-on-changes` | Fail plan executions when resource changes are detected. |
| `--strict-validation-warnings` | Treat plan validation warnings as failures. |
| `--ref-name` | Current branch name used for shared branch policy validation. |
| `--default-branch` | Repository default branch used for shared branch policy validation. |
| `--is-primary-branch` | Provider primary-branch indicator when no default branch is available. Choices: `true`, `false`. |
| `--skip-branch-validation` | Skip shared branch and pull-request policy validation. |
| `--format` | Output format for the CI execution manifest. Choices: `table`, `json`. |

### `stacksmith ci execute`

```text
stacksmith ci execute [-h] --manifest MANIFEST --environment ENVIRONMENT
                             [--phase {test,plan,apply,destroy,plan-operation,operation}]
                             [--validation-report-output VALIDATION_REPORT_OUTPUT]
```

| Argument | Description |
| - | - |
| `--manifest` | Path to a JSON manifest emitted by stacksmith ci prepare. |
| `--environment` | Environment row from the manifest to execute. |
| `--phase` | Lifecycle phase to execute. The phase must belong to the manifest command; destroy manifests support infrastructure and operation-state previews, operation-state cleanup, and infrastructure destruction. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `operation`. |
| `--validation-report-output` | Optional path for plan validation report output. When set, plan JSON report output is written to this file. |

### `stacksmith ci prepare-from-env`

```text
stacksmith ci prepare-from-env [-h] [--provider {generic,github-actions,jenkins}]
                                      [--manifest-file MANIFEST_FILE] [--github-output GITHUB_OUTPUT]
```

| Argument | Description |
| - | - |
| `--provider` | CI provider adapter mode. github-actions emits manifest, matrix, and count to GITHUB_OUTPUT. generic and jenkins emit manifest JSON to stdout. Choices: `generic`, `github-actions`, `jenkins`. |
| `--manifest-file` | Optional file path where the generated manifest JSON is written. |
| `--github-output` | Optional override path for GITHUB_OUTPUT when provider is github-actions. |

### `stacksmith ci execute-from-env`

```text
stacksmith ci execute-from-env [-h] [--provider {generic,github-actions,jenkins}]
                                      [--manifest-file MANIFEST_FILE] [--environment ENVIRONMENT]
                                      [--phase {test,plan,apply,destroy,plan-operation,operation}]
                                      [--validation-report-output VALIDATION_REPORT_OUTPUT]
```

| Argument | Description |
| - | - |
| `--provider` | CI provider adapter mode for execution defaults. Choices: `generic`, `github-actions`, `jenkins`. |
| `--manifest-file` | Optional manifest file path override. When omitted, CI_MANIFEST_FILE or STACKSMITH_CI_MANIFEST is used. |
| `--environment` | Optional environment name override. When omitted, STACKSMITH_ENVIRONMENT or ENVIRONMENT is used. |
| `--phase` | Optional lifecycle phase override. When omitted, STACKSMITH_CI_PHASE or the manifest command is used. The phase must belong to the manifest command; destroy manifests support previews, operation-state cleanup, and infrastructure destruction. Choices: `test`, `plan`, `apply`, `destroy`, `plan-operation`, `operation`. |
| `--validation-report-output` | Optional plan validation report output path override. When omitted, STACKSMITH_VALIDATION_REPORT_PATH or provider defaults are used. |

### `stacksmith ci redact-plan`

```text
stacksmith ci redact-plan [-h] (--output OUTPUT | --in-place) input
```

| Argument | Description |
| - | - |
| `input` | Path to raw OpenTofu plan JSON. |
| `--output` | Write redacted plan JSON to this path. |
| `--in-place` | Atomically replace the input file with its redacted form. |

<!-- END GENERATED CLI REFERENCE -->

#### Targeted execution

`plan` already serves as the dry-run mode for targeted execution, so a separate target dry-run flag is not required.

Expression context includes `tags` (effective tag list), `tag` (boolean map by tag name), `component_name`, `component_type`, `stack_name`, and `stack_tags`.

Only dot-style tag access is supported for tag expressions, for example `tag.prod`. Bracket-style references such as `tag['prod']` are not accepted.

Examples are as follows.

```shell
stacksmith plan --tag prod --tag shared

stacksmith plan --tag-expr "contains(tags, 'prod') && (contains(tags, 'shared') || contains(tags, 'critical'))"

stacksmith plan --debug --save-redacted-plan-json ./plan.json

stacksmith run-all apply --tag prod --tag-expr "tag.experimental == `false`"

stacksmith run-all plan --debug --save-redacted-plan-json ./plans

stacksmith run-all plan --tag-expr "tag.prod && tag.experimental == `false`"

stacksmith run-all plan --include-tag prod --exclude-tag experimental

stacksmith run-all plan --tag-expr "contains(stack_tags, 'prod') && tag.web"
```

If your expression evaluates to a non-boolean value for any component, stacksmith fails fast with an error and no Terragrunt command is run.

Targeted execution is additive. It does not replace normal multi-stack orchestration, and it may fail when omitted targets are required by selected components.

#### Validation report output

`validate`, `plan`, and `run-all plan` emit one machine-readable report block to stdout.

Use `--validation-report-format json` to explicitly select the currently supported output format. The flag is retained so more formats can be added later.

```json
{
  "command": "plan",
  "status": "warn",
  "exit_code": 0,
  "strict_validation_warnings": false,
  "summary": {
    "pass": 2,
    "warn": 1,
    "fail": 0
  },
  "results": [
    {
      "name": "require_imdsv2",
      "status": "warn",
      "message": "IMDSv2 check returned warning",
      "stack_name": "web"
    }
  ]
}
```

Exit behavior is as follows.

- Exit code is `1` when at least one validation result is `fail`.
- Exit code is `1` for warnings only when `--strict-validation-warnings` is set.

This direct pipeline works without extra filtering.

```shell
stacksmith plan stack.yaml --config ./stacksmith-config.yaml | jq '.status'

stacksmith plan stack.yaml --config ./stacksmith-config.yaml --validation-report-format json > validation-report.json
```

### Info commands

Use `info modules-and-policies` to review configured modules, mappings, metadata, and plan validations.

`info modules-and-policies --format json` writes machine-readable output to stdout.

`info modules-and-policies --format table` writes human-readable output to stderr.

```shell
stacksmith info modules-and-policies --config examples/shared-config-repo/stacksmith-base-config.yaml --config examples/shared-config-repo/stacksmith-config.yaml
```

Use `info diagnose` to inspect cache and module-resolution diagnostics for a stack.

`info diagnose` writes diagnostics to stderr.

```shell
stacksmith info diagnose examples/stack-simple-repo/stack.yaml --config examples/shared-config-repo/stacksmith-base-config.yaml --config examples/shared-config-repo/stacksmith-config.yaml
```

### CI commands

Use `ci environments` to preview GitOps environment discovery and selection logic used by the opinionated reusable workflow.

`ci environments --format json` writes machine-readable output to stdout.

`ci environments --format table` writes human-readable output to stderr.

```shell
stacksmith ci environments \
  --gitops-root examples/gitops-repo \
  --discovery-mode env-files \
  --event-name push \
  --changed-path examples/gitops-repo/environments/dev.yaml
```

Use `ci validate` to run CI-oriented preflight checks with a stable check-result contract.

The first release focuses on static checks such as discovery mode validity, runfile path resolution, env-file path, and validation report format. The output structure is designed to support additional CI checks later without changing the command shape.

```shell
stacksmith ci validate \
  --gitops-root examples/gitops-repo \
  --discovery-mode env-files \
  --workflow-runfile examples/gitops-repo/common/stacksmith.yaml \
  --workflow-env-file /dev/null \
  --workflow-validation-report-format json
```

### Tips

- Using a monorepo and concerned about who can edit what? Use GitHub's [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) file to restrict write access to certain stack files while allowing broader read access. Similarly, the managed config can be locked down to a small team of platform engineers, while the validation policies themselves can be tightly controlled by a security team.
- Doing a lot of `get` calls on dictionaries in your validation scripts? Try using `jmespath` instead to query complex nested structures with ease. For example, `jmespath.search("components.*.properties.bucket", stack)` would return a list of all bucket properties across all components in the stack.
- Want to take existing resources into consideration for validation rules? Import `boto3` and use it to query AWS directly from your validation scripts. Just be mindful of latency implications.

### Roadmap

The roadmap is ordered roughly by expected impact. Reproducibility and deployment safety come first, followed by operability and developer-experience improvements.

#### Resolution provenance and effective configuration inspection

Add an `info explain` command that shows how a final input or component property was produced. Its output should identify each contributing vars file, environment variable, runfile value, and command-line override in precedence order, along with deep-merge decisions, templates, transforms, managed defaults, property renames, and automatic injection.

The command should support table and JSON output, direct queries such as `inputs.region` or `components.api.properties.instance_type`, and redaction of sensitive values. A related effective-configuration view could render the fully merged stack, managed config, and resolved inputs without running OpenTofu, making configuration reviews and CI diagnostics substantially easier.

#### Secret-aware inputs and operation parameters

Complete the existing `secret` operation input metadata and extend the concept to ordinary Stacksmith inputs. Secret declarations should support environment-backed and file-backed values initially, with a pluggable interface for external secret managers later. Diagnostics, provenance output, validation errors, and normal logs must redact these values.

Where the OpenTofu and Terragrunt execution models permit it, secrets should be passed through the process environment or temporary permission-restricted files instead of being serialized into generated configuration. Stacksmith should warn when a workflow necessarily places a secret in a plan or state file, and secret changes should still be able to affect operation execution identity without exposing the original value.

#### Dependency-aware parallel `run-all`

Add `--jobs N` to execute independent stacks concurrently while continuing to respect dependency order. The scheduler should release a stack only after all of its required predecessors have succeeded, reverse the graph correctly for destruction, and keep serial execution as the default.

Parallel mode should provide grouped or prefixed logs, deterministic result summaries, and explicit fail-fast and continue-on-error policies. Plan JSON and validation results must remain isolated per stack so parallel workers cannot overwrite one another's artifacts.

#### Trusted execution controls for Python hooks

Add a trust policy for Python validation, transform, and provider configuration hooks, especially remotely fetched scripts. The policy should support allowed hosts, required content hashes or lockfile entries, and a CI mode that rejects unpinned executable code. An optional isolated subprocess runner could add timeouts, a restricted environment, captured output, and resource limits while preserving an explicitly enabled in-process mode for compatibility.

This work should share source verification with the lockfile rather than inventing a separate integrity mechanism. Documentation should make clear that managed Python hooks are executable code and define which repository owners are expected to approve them.

#### Additional validation report formats

Add CSV output for validation reports while retaining JSON as the stable machine-oriented default. It should use one row per validation outcome with consistent columns for stack, rule, status, message, and origin.

#### Typer-based CLI

Consider migrating the CLI from `argparse` to `typer` after the command and option model has stabilized.
