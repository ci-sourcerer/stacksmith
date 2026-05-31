# GitOps Example

This example GitOps repository demonstrates how stack definitions, configs,
and variables can be assembled to describe environment stacks. For the demo
everything is kept in a single checkout using structured local references so
you can run the examples locally.

In real GitOps workflows these pieces are often stored in separate repositories,
for example a shared-config repo, a manifests repo, or one repo per environment.
When integrating with a GitOps controller, point the references in the
`stacksmith.yaml` files at remote repositories instead of local paths.

Example remote target (replace with your controller/tooling-supported syntax)

<https://github.com/ci-sourcerer/stacksmith.git//examples/shared-config-repo/stacksmith-config.yaml>

Notes

- Local references in this example are for convenience and testing.
- Remote URL syntax varies by tool; the double-slash above indicates a repo URL followed by an in-repo path.

Change the `stacksmith.yaml` entries to `source: git` or `source: http`
references to simulate pulling manifests and configs from separate repositories.
