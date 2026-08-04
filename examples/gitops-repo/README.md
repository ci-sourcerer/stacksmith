# GitOps Example

This example GitOps repository uses the hybrid `env-files` discovery style. It keeps the shared runfile under `common/stacksmith.yaml`, each environment manifest in `environments/<env>.yaml`, and shared stack layers under `manifests/common/`.

The reusable workflow's other layouts are summarized in [GitOps discovery styles](../README.md#gitops-discovery-styles).

This example is intentionally local-path based for easy testing. In a real GitOps workflow, point the `source: local` references at remote Git or HTTP sources instead.

The shared runfile also demonstrates address-aware merging. The platform stack provides a common Helm `values_files` entry, while the service stack provides the environment-specific entry. Its `merge_rules` selector overrides that list at `/components/frontend_release/properties/values_files`, preventing the normal deep-merge behavior from appending both files.

## Declarative application deployment

The service stack declares `deploy_app` using the platform-approved `jenkins_deploy` operation from the shared configuration. Each environment vars file pins `application_commit` to an immutable Git SHA. The operation maps that value to the Jenkins job's `GIT_COMMIT` parameter and also supplies `ENVIRONMENT` and `RELEASE_TAG`.

The approved Jenkins definition explicitly uses `trigger: after_apply`; operations remain manual by default. During a normal default-branch apply, Stacksmith first applies infrastructure, then replans and reconciles a separate operation-only OpenTofu state. Updating `application_commit` therefore requests deployment of that exact revision, while applying the same manifest again is a no-op. Stacksmith waits for Jenkins to finish and fails the operation phase if the build is unsuccessful. The operation root can read only the sensitive infrastructure outputs explicitly bridged for its inputs, so it cannot update infrastructure.

The GitHub repository or environment must define `STACKSMITH_JENKINS_USERNAME` and `STACKSMITH_JENKINS_API_TOKEN` secrets. The reusable execution workflow passes them to the operation runner, while the GitOps manifests contain only the non-secret deployment parameters.

## Local testing with Stacksmith

The reusable workflow fans out one job per environment, using the shared runfile in `common/stacksmith.yaml` and the environment file in `environments/<env>.yaml`. You can reproduce that locally with the same inputs the CI job would pass.

Plan the `dev` environment from this repository root.

```bash
ENVIRONMENT=dev
stacksmith plan \
  --runfile examples/gitops-repo/common/stacksmith.yaml \
  --runfile examples/gitops-repo/environments/${ENVIRONMENT}.yaml \
  --vars examples/gitops-repo/vars/vars.${ENVIRONMENT}.yaml
```

Apply the `dev` environment from this repository root.

```bash
ENVIRONMENT=dev
stacksmith apply \
  --runfile examples/gitops-repo/common/stacksmith.yaml \
  --runfile examples/gitops-repo/environments/${ENVIRONMENT}.yaml \
  --vars examples/gitops-repo/vars/vars.${ENVIRONMENT}.yaml
```

Unlike planning, applying this example executes the configured Jenkins operation when its isolated state-backed specification changes. Replace the placeholder Jenkins URL and job in the shared config and export `STACKSMITH_JENKINS_USERNAME` and `STACKSMITH_JENKINS_API_TOKEN` before testing an apply locally.

## Local workflow testing with `act`

Use the included helper script to test the reusable GitHub Actions workflow locally.

This helper passes the managed shared config reference to CI and relies on the example runfile to layer both `examples/shared-config-repo/stacksmith-base-config.yaml` and `examples/shared-config-repo/stacksmith-config.yaml` through its own `configs:` list.

```sh
examples/scripts/run-act-workflow.sh gitops-repo plan dev
examples/scripts/run-act-workflow.sh gitops-repo apply dev
```

The script uses the same workflow inputs as the reusable job and requires AWS credentials to be available in your shell. The `gitops-repo apply` command also requires `STACKSMITH_JENKINS_USERNAME` and `STACKSMITH_JENKINS_API_TOKEN`; the helper passes them to `act` through a permission-restricted temporary secrets file.

> Note: `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` must be set in your shell environment before running this test. Set `AWS_SESSION_TOKEN` when using temporary credentials.
