# Managed configuration

Centralize module mappings, providers, versions, policies, and platform-owned defaults in `stacksmith-config.yaml`.

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

Provider definitions are grouped by provider family and can expose multiple named instances through `instances`. The entire `instances` map and its `default` entry are optional; when the default is omitted, Stacksmith emits an empty provider block for the unaliased provider. References such as `aws.default` resolve to this implicit default. Non-default instances must define an explicit `alias`. An instance's `config` is optional when the provider needs no explicit arguments. Module mappings can optionally define a `providers` map that routes module provider names to an instance reference in `<provider>.<instance>` format. If a module mapping omits `providers`, Stacksmith uses the unaliased provider. The optional `outputs` map defines the component's public output contract. Each key is the name stack authors use under `components.<instance>`, while `mapped_from` selects the underlying module output and defaults to the public name. An output `transform` adapts the unresolved native OpenTofu reference before it reaches consumers. Jinja output transforms receive `output.value`, `output.name`, and `output.module_output`, plus component and stack metadata. Python output transforms receive the reference as `value` and the same metadata through `context`.

Set `auto_expose_outputs: true` to introspect the underlying module and make its other declared outputs available under the same names, such as `{{ components.app.instance_id }}`. Explicit output mappings remain authoritative: they can rename or transform outputs, and an underlying output claimed by an explicit mapping is not also exposed under its implementation name. Automatic exposure only affects component references inside the current stack; it never creates root stack outputs or expands the inter-stack state contract.

Output transforms execute during Stacksmith generation, before OpenTofu knows the actual output value. They can wrap a reference in a string or produce a structured list or object for a direct component reference. A structured transformed output cannot be embedded inside a larger string.

## Managed associations

Managed associations wire components that already exist in the final merged stack. A rule selects producer components, reads a managed public output from each producer, and contributes the resulting references to a managed property on every selected consumer. Associations do not create components or root stack outputs, and the generated references retain native OpenTofu dependency semantics.

```yaml
associations:
  standard-instance-security-groups:
    description: Attach standard security groups to application instances.
    producers:
      select: >-
        component_type == 'aws_security_group' &&
        tag.standard
      cardinality: one_or_more
    consumers:
      select: component_type == 'aws_ec2_instance'
    bindings:
      - output: id
        property: security_group_ids
        merge: append

  application-secret-access:
    description: Supply application secret ARNs to application roles.
    producers:
      select: >-
        component_type == 'aws_secretsmanager_secret' &&
        tag.application
      cardinality: zero_or_more
    consumers:
      select: >-
        component_type == 'aws_iam_role' &&
        tag.application
    bindings:
      - output: arn
        property: readable_secret_arns
        merge: append
```

Selectors use the same JMESPath component context as tag targeting. They can inspect `component_name`, `component_type`, `tags`, `tag.<name>`, `stack_name`, and `stack_tags`. Component tags include both tags declared on the component and tags supplied by its managed module mapping.

Producer cardinality controls validation and value shape. `exactly_one` requires one producer, `zero_or_one` permits at most one, `one_or_more` requires at least one, and `zero_or_more` accepts any count. With `set_if_absent`, singular cardinalities contribute a scalar and plural cardinalities contribute a list. With `append`, producers always contribute a list.

The `set_if_absent` merge mode preserves an explicitly authored property. The `append` mode adds references to an existing list, removes exact duplicates, and rejects non-list existing values. Multiple `append` rules may contribute to the same property, while multiple rules cannot implicitly set the same absent property.

A stack can disable a rule everywhere, or exclude one component from either side of a rule.

```yaml
disabled_associations:
  - application-secret-access

components:
  legacy_instance:
    type: aws_ec2_instance
    disabled_associations:
      - standard-instance-security-groups
```

Disabled association names are validated against the managed configuration so a misspelled opt-out cannot silently pass. Association rules are same-stack only. Continue using explicit root outputs and stack dependencies for cross-stack contracts.

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

When present, a provider instance's `config` must use exactly one top-level source key to define provider arguments. Supported sources are the following.

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
