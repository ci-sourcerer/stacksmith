# Getting started

This guide runs Stacksmith from a source checkout and introduces the two files at the center of its model: an application-owned stack and a platform-owned managed configuration.

## Prepare the environment

Stacksmith currently requires the Python version declared in `pyproject.toml`. From the repository root, create the development environment and inspect the CLI.

```sh
uv sync --group dev
uv run stacksmith --help
```

## Create a stack

Create `stack.yaml` with one application-owned component. The component type is intentionally abstract; the managed configuration decides which module implements it.

```yaml
name: my-app

tags:
  - apps
  - storage

components:
  app-bucket:
    type: aws_s3_bucket
    properties:
      acl: private
      bucket: my-app-assets
```

## Provide managed configuration

Create `stacksmith-config.yaml` with platform-owned tool, backend, provider, and module choices. Replace the example module repository and versions with values appropriate for your environment.

```yaml
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

module_mappings:
  aws_s3_bucket:
    source:
      source: git
      data:
        repo: https://github.com/my-org/terraform-aws-s3.git
        ref: "3.2.1"
    properties:
      acl:
        mapped_to: bucket_acl
```

## Validate and preview

Validate the Stacksmith inputs before asking OpenTofu to produce a plan.

```sh
uv run stacksmith validate --stack stack.yaml --config stacksmith-config.yaml
uv run stacksmith plan --stack stack.yaml --config stacksmith-config.yaml
```

The plan command resolves the configured tools and remote resources, generates the OpenTofu and Terragrunt files, initializes the working directory when required, and streams the underlying plan output.

## Apply deliberately

After reviewing the plan, apply the stack with the same inputs.

```sh
uv run stacksmith apply --stack stack.yaml --config stacksmith-config.yaml
```

For team workflows, prefer a checked-in [runfile](guides/execution.md#the-runfile) so commands share the same stack layers, configuration, variables, and lock policy.

## Continue learning

- Learn the ownership boundaries in [Core concepts](concepts.md).
- Explore the complete stack schema and output model in [Stack authoring](guides/stack-authoring.md).
- Configure defaults, module discovery, providers, and outputs in [Managed configuration](guides/managed-configuration.md).
- Add layered inputs, remote resources, validations, transforms, or operations in [Advanced authoring](guides/advanced-authoring.md).
- Look up every flag in the generated [CLI reference](reference/cli.md).
