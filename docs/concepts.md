# Core concepts

Stacksmith separates application intent from platform implementation. Stack authors select approved component types and provide values; platform owners decide how those types map to modules, providers, state, and policy.

## Stack

A stack is the unit of infrastructure owned by an application or service team. It contains metadata, tags, dependency edges, component instances, explicit root outputs, and native operation invocations. See [Stack authoring](guides/stack-authoring.md).

## Managed configuration

The managed configuration is the platform-owned contract. It defines backend settings, tool versions, providers, explicit or convention-based module mappings, and centralized validation and transformation rules. See [Managed configuration](guides/managed-configuration.md).

## Component

A component is an instance of an abstract type inside a stack. Stacksmith resolves that type to an approved OpenTofu module, maps the component properties to module inputs, and exposes the module outputs through a managed public contract.

## Input

Inputs are resolved values available to stack templates, component properties, policies, backend resolvers, and operations. They can come from defaults, environment variables, files, CLI overrides, scripts, or remote resources. See [Advanced authoring](guides/advanced-authoring.md#inputs).

## Runfile

A runfile is the repository-level execution contract. It can select stack layers, managed configuration, variable sources, merge behavior, lock policy, and other defaults shared by commands. See [Execution and orchestration](guides/execution.md#the-runfile).

## Lockfile

A lockfile records resolved local and remote inputs for reproducible or offline execution. It complements package and provider locks by covering the Stacksmith resources that participate before OpenTofu runs. See [Lockfiles](guides/execution.md#lockfiles).

## Policy

Managed validations and transforms enforce platform rules without putting trusted implementation code in application-owned stacks. Plan validations can also inspect OpenTofu plan JSON after planning. See [Validation and transforms](guides/advanced-authoring.md#validation-and-transforms).

## Native operation

A native operation is an approved action modeled separately from infrastructure resources while retaining plan, state, dependency, and lifecycle controls. Operations can run directly or after infrastructure apply. See [Native operations](guides/advanced-authoring.md#native-operations).

## Generated configuration

Stacksmith generates OpenTofu JSON configuration and Terragrunt configuration in a build directory. OpenTofu still owns resource graph evaluation, planning, and apply-time unknown values; Stacksmith does not replace those semantics.
