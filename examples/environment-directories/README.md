# Environment Directories

A local-state tutorial using the built-in `terraform_data` resource. No AWS credentials or operation secrets are required. Run commands from this directory.

```sh
stacksmith ci environments --gitops-root . --discovery-mode auto
stacksmith validate --build-dir .stacksmith/dev --runfile common/stacksmith.yaml --runfile environments/dev/stacksmith.yaml
stacksmith plan --build-dir .stacksmith/dev --runfile common/stacksmith.yaml --runfile environments/dev/stacksmith.yaml
stacksmith apply --build-dir .stacksmith/dev --runfile common/stacksmith.yaml --runfile environments/dev/stacksmith.yaml
stacksmith destroy --build-dir .stacksmith/dev --runfile common/stacksmith.yaml --runfile environments/dev/stacksmith.yaml
```

Repeat with `prod` in place of `dev`. Discovery lists both targets. Each deployment contains one resource with its environment and greeting as inputs. These examples use local state and require a remote backend for CI.
