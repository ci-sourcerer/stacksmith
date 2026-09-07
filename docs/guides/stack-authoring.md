# Stack authoring

Define application infrastructure with stack metadata, components, properties, outputs, and backend selection.

A stack is the unit of infrastructure authored by application or service teams. A stack file contains metadata, tags, dependency edges, components, explicit root outputs, and operation invocations. It is the "calling code" that references abstract [component](#components) types declared in the managed config and provides properties for those components.

## Managed config

The managed config (`stacksmith-config.yaml`) is the shared contract controlled by platform teams. It defines backend settings, OpenTofu version, providers, explicit or convention-based module mappings, and centralized validation/transform rules.

## Components

Components are the entries under `components` in a stack file. Each component declares the following.

- `type`: an abstract type resolved by the [managed config](#managed-config) to a OpenTofu module
- `tags`: optional [targeting tags](execution.md#tags-and-targeting)
- `properties`: module input values authored by stack owners

## Writing a stack

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

### Consuming collection-valued component outputs

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

### Generating components with Jinja

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

### State backend

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
