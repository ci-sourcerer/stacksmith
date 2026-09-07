# Python API

Call Stacksmith from Python when an application needs orchestration without shelling out to the CLI.

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

## GitOps change helpers

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
