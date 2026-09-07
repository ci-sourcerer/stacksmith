# Contributing

Stacksmith is an early proof of concept, so changes may reshape existing interfaces as well as add behavior. Keep implementation, tests, generated references, and narrative documentation aligned in the same change.

## Prepare a development environment

From a source checkout, synchronize the development dependency group.

```sh
uv sync --group dev
```

## Run project checks

Format before linting, then run the complete test suite.

```sh
poe format
poe lint
poe test
```

New behavior should normally include tests and documentation. Keep functions focused, use specific exception handling, and follow the conventions already established in the surrounding module.

## Work on the documentation

Documentation source lives in `docs/`, navigation and presentation are configured in `zensical.toml`, and the generated site is written to `site/`.

Start the local Zensical preview server while editing pages.

```sh
poe docs-serve
```

Run a strict production build before submitting documentation changes.

```sh
poe docs-build
```

The pull request workflow performs the same strict build and uploads the rendered site as an artifact. It does not publish the site.

## Publish the documentation

The production workflow in `.github/workflows/docs-deploy.yml` builds and deploys the site to GitHub Pages after documentation changes merge into `main`. It can also be started manually. GitHub repository settings must select **GitHub Actions** as the Pages source.

The custom hostname is configured in GitHub under **Settings → Pages → Custom domain**, not by a file in the generated site. Set it to `stacksmith.ci-sourcerer.com`.

Because DNS for `ci-sourcerer.com` is managed by Cloudflare, add this record to the zone.

| Type | Name | Target | Proxy status |
| - | - | - | - |
| `CNAME` | `stacksmith` | `ci-sourcerer.github.io` | DNS only |

Point the CNAME at the account-level GitHub Pages hostname without the repository name. Add the custom domain in GitHub before creating the public DNS record, verify the parent domain in GitHub when possible, and avoid wildcard records. After GitHub provisions its certificate, enable **Enforce HTTPS** in the Pages settings.

## Update the CLI reference

The command reference is generated from Stacksmith's argparse parser. Do not manually edit the generated block in `docs/reference/cli.md`.

```sh
poe docs-cli-reference
poe docs-cli-reference-check
```

The check command reports a diff and exits unsuccessfully when parser changes have not been propagated to the documentation.

## License

Contributions are accepted under the repository's [license](https://github.com/ci-sourcerer/stacksmith/blob/main/LICENSE).
