# CLI usage notes

Apply targeting, machine-readable reports, inspection commands, and CI helpers alongside the generated command reference.

## Targeted execution

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

### Validation report output

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

## Info commands

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

## CI commands

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

## Tips

- Using a monorepo and concerned about who can edit what? Use GitHub's [CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) file to restrict write access to certain stack files while allowing broader read access. Similarly, the managed config can be locked down to a small team of platform engineers, while the validation policies themselves can be tightly controlled by a security team.
- Doing a lot of `get` calls on dictionaries in your validation scripts? Try using `jmespath` instead to query complex nested structures with ease. For example, `jmespath.search("components.*.properties.bucket", stack)` would return a list of all bucket properties across all components in the stack.
- Want to take existing resources into consideration for validation rules? Import `boto3` and use it to query AWS directly from your validation scripts. Just be mindful of latency implications.
