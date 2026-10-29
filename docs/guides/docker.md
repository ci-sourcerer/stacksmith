# Docker

Build a Stacksmith image with tools, modules, and providers prepared for repeatable local or CI execution.

A Docker image is provided that bundles OpenTofu and Terragrunt so no local installation is required. It is also especially useful for CI environments.

As this project is reliant on [Common Python Tasks](https://github.com/ci-sourcerer/common-python-tasks), you can build the image with a simple command: `poe build-image`. You can pass `--build-args TOFU_PROVIDER_SPEC="hashicorp/aws=6.41.0:hashicorp/random=3.8.1"`, for example, to pre-install some OpenTofu providers into the image. This can drastically speed up Stacksmith runs for your users, which is especially helpful in CI environments. By default, the image includes no providers, so OpenTofu will download them on demand during execution.

> ⚠️ **WARNING:** `TOFU_PROVIDER_SPEC` is a shared provider cache keyed by provider version, not by OpenTofu version. If you build or run images with multiple OpenTofu versions, pre-cached providers may not be compatible with an older runtime unless you explicitly pin and pre-cache every provider version needed by those tool versions.

## Pre-installing modules

Similarly to providers, you can pre-install OpenTofu modules into the image using the `TOFU_MODULE_SPEC` build arg. This is a colon-separated list of `source=version-or-ref` pairs that match the sources and exact versions or Git refs in your managed config.

```shell
poe build-image --build-args TOFU_MODULE_SPEC="https://github.com/org/terraform-aws-s3.git=v3.2.1:https://github.com/org/terraform-aws-ec2.git=v5.0.0"
```

When modules are vendored in the image, Stacksmith automatically rewrites module sources in the generated `stacksmith.tf.json` to point to the local vendored copies instead of remote URLs. This eliminates network fetches during `tofu init` and ensures immutable, reproducible builds.

Each module is stored under a deterministic directory name derived from `sha256("<source>|<version>")[:16]`, and a `vendor-manifest.json` is written alongside the directories for reverse lookup.

## Controlling local module rewriting

Local module rewriting (requiring local vendored modules) is controlled by the `STACKSMITH_ONLY_USE_LOCAL_MODULES` environment variable and the `--use-local-modules` / `--no-local-modules` CLI flags.

| Control | Effect |
| - | - |
| `STACKSMITH_ONLY_USE_LOCAL_MODULES=1` | Enable local module rewriting |
| `--use-local-modules` | Enable explicitly from the CLI |
| `--no-local-modules` | Disable even when the env var is set |
| `STACKSMITH_VENDOR_DIR=<path>` | Override the local vendored module root directory |

If a vendored module directory is missing at generation time, Stacksmith fails fast with a clear error rather than silently falling back to remote fetching.

## Extracting the module and provider specs from config

The following recipe uses `yq` to extract module and provider specs from a managed config file and pass them directly to `poe build-image`. `TOFU_PROVIDER_SPEC` uses colon-separated (`:`) `source=version` items, while `TOFU_MODULE_SPEC` uses `source=version-or-ref` items. Provider version ranges that include commas, such as `>= 6.39, < 7.0`, are supported. Local module mappings are excluded because they are already filesystem paths rather than dependencies that OpenTofu can pre-fetch.

This extraction only includes explicit module mappings. A templated default mapping represents an open-ended set of sources and cannot be pre-vendored without knowing the stack components that will use it. When local-module-only execution is required, add explicit mappings for every component type that must be included in the image.

```shell
stacksmithConfigPath=<path to stacksmith-config.yaml>
poe build-image \
  --build-args \
    "TOFU_MODULE_SPEC=$(yq -r '
      .module_mappings
      | to_entries
      | map(
          (
            select(.value.source.source == "git")
            | .value.source.data.repo
              + ((.value.source.data.path | select(. != null) | "//" + .) // "")
              + "=" + .value.source.data.ref
          ),
          (
            select(.value.source.source == "registry")
            | .value.source.data.address + "=" + .value.source.data.version
          )
        )
      | join(":")
    ' "$stacksmithConfigPath")" \
    "TOFU_PROVIDER_SPEC=$(yq -r '
      .provider_mappings
      | to_entries
      | map("\(.value.source.data.address)=\(.value.source.data.version)")
      | join(":")
    ' "$stacksmithConfigPath")"
```
