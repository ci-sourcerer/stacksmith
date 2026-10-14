# Roadmap

These are likely future directions rather than committed release promises.

The roadmap is ordered roughly by expected impact. Reproducibility and deployment safety come first, followed by operability and developer-experience improvements.

## Move native operations to OpenTofu actions

Native operations currently use a separate runner-only Terraform root and isolated state. When OpenTofu supports action blocks and lifecycle triggers, move or adapt the backend to use them, reducing the need to model imperative work as provisioner-backed resources. Terraform's [plugin framework actions](https://developer.hashicorp.com/terraform/plugin/framework/actions) provide the reference model. OpenTofu support is still an open, pending discussion in [issue #3309](https://github.com/opentofu/opentofu/issues/3309), so the timing and exact behavior depend on that project's implementation.

## Secret-aware inputs and operation parameters

Environment-backed and local-file-backed secret references are supported for ordinary inputs and operation parameters. Managed `secret` metadata requires these references, and local operations automatically mask their secret parameters. CI systems can inject the environment variables or files; external secret-manager integrations are outside the current scope.

Secret contents are supplied through the execution environment rather than serialized into generated configuration. Ordinary module inputs can still enter OpenTofu plans and state, and generation warns about that retention. Operation identity uses keyed fingerprints to detect secret changes. Future work may extend the initial direct-reference contract to safe transformations and validation hooks. See [secret inputs](guides/stack-authoring.md#secret-inputs) for the supported boundaries.

## Dependency-aware parallel `run-all`

Add `--jobs N` to execute independent stacks concurrently while continuing to respect dependency order. The scheduler should release a stack only after all of its required predecessors have succeeded, reverse the graph correctly for destruction, and keep serial execution as the default.

Parallel mode should provide grouped or prefixed logs, deterministic result summaries, and explicit fail-fast and continue-on-error policies. Plan JSON and validation results must remain isolated per stack so parallel workers cannot overwrite one another's artifacts.

## Trusted execution controls for Python hooks

Add a trust policy for Python validation, transform, and provider configuration hooks, especially remotely fetched scripts. The policy should support allowed hosts, required content hashes or lockfile entries, and a CI mode that rejects unpinned executable code. An optional isolated subprocess runner could add timeouts, a restricted environment, captured output, and resource limits while preserving an explicitly enabled in-process mode for compatibility.

This work should share source verification with the lockfile rather than inventing a separate integrity mechanism. Documentation should make clear that managed Python hooks are executable code and define which repository owners are expected to approve them.

## Typer-based CLI

Consider migrating the CLI from `argparse` to `typer` after the command and option model has stabilized.
