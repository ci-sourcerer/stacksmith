# Roadmap

These are likely future directions rather than committed release promises.

The roadmap is ordered roughly by expected impact. Reproducibility and deployment safety come first, followed by operability and developer-experience improvements.

## Resolution provenance and effective configuration inspection

Add an `info explain` command that shows how a final input or component property was produced. Its output should identify each contributing vars file, environment variable, runfile value, and command-line override in precedence order, along with deep-merge decisions, templates, transforms, managed defaults, property renames, and automatic injection.

The command should support table and JSON output, direct queries such as `inputs.region` or `components.api.properties.instance_type`, and redaction of sensitive values. A related effective-configuration view could render the fully merged stack, managed config, and resolved inputs without running OpenTofu, making configuration reviews and CI diagnostics substantially easier.

## Secret-aware inputs and operation parameters

Complete the existing `secret` operation input metadata and extend the concept to ordinary Stacksmith inputs. Secret declarations should support environment-backed and file-backed values initially, with a pluggable interface for external secret managers later. Diagnostics, provenance output, validation errors, and normal logs must redact these values.

Where the OpenTofu and Terragrunt execution models permit it, secrets should be passed through the process environment or temporary permission-restricted files instead of being serialized into generated configuration. Stacksmith should warn when a workflow necessarily places a secret in a plan or state file, and secret changes should still be able to affect operation execution identity without exposing the original value.

## Dependency-aware parallel `run-all`

Add `--jobs N` to execute independent stacks concurrently while continuing to respect dependency order. The scheduler should release a stack only after all of its required predecessors have succeeded, reverse the graph correctly for destruction, and keep serial execution as the default.

Parallel mode should provide grouped or prefixed logs, deterministic result summaries, and explicit fail-fast and continue-on-error policies. Plan JSON and validation results must remain isolated per stack so parallel workers cannot overwrite one another's artifacts.

## Trusted execution controls for Python hooks

Add a trust policy for Python validation, transform, and provider configuration hooks, especially remotely fetched scripts. The policy should support allowed hosts, required content hashes or lockfile entries, and a CI mode that rejects unpinned executable code. An optional isolated subprocess runner could add timeouts, a restricted environment, captured output, and resource limits while preserving an explicitly enabled in-process mode for compatibility.

This work should share source verification with the lockfile rather than inventing a separate integrity mechanism. Documentation should make clear that managed Python hooks are executable code and define which repository owners are expected to approve them.

## Additional validation report formats

Add CSV output for validation reports while retaining JSON as the stable machine-oriented default. It should use one row per validation outcome with consistent columns for stack, rule, status, message, and origin.

## Typer-based CLI

Consider migrating the CLI from `argparse` to `typer` after the command and option model has stabilized.
