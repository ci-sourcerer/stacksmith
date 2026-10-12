#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from packaging.version import InvalidVersion, Version


def _replace(text: str, pattern: str, replacement: str, path: Path) -> str:
    updated, count = re.subn(pattern, replacement, text)
    if not count:
        raise ValueError(f"No CI version pin found in {path}")
    return updated


def _pin_updates(root: Path, version: str) -> dict[Path, str]:
    updates = {}
    for path in (
        root / ".github/workflows/stacksmith-gitops-opinionated-reusable.yml",
        root / ".github/workflows/stacksmith-gitops-reusable.yml",
        root / "jenkins/vars/stacksmith.groovy",
        *sorted((root / "examples/github-actions").glob("stacksmith-*.yml")),
        root / "docs/integrations/ci.md",
    ):
        original = path.read_text(encoding="utf-8")
        if path.name == "stacksmith-gitops-opinionated-reusable.yml":
            updated = _replace(
                original,
                r"(STACKSMITH_IMAGE_VERSION \|\| ')[^']+(')",
                rf"\g<1>{version}\2",
                path,
            )
        elif path.name == "stacksmith-gitops-reusable.yml":
            updated = _replace(
                original,
                r"(      image_version:\n(?:        .*\n)*?        default: )[^\n]+",
                rf"\g<1>{version}",
                path,
            )
        elif path.suffix == ".groovy":
            updated = _replace(
                original,
                r"(STACKSMITH_IMAGE_VERSION \?: ')[^']+(')",
                rf"\g<1>{version}\2",
                path,
            )
        elif path.suffix == ".md":
            updated = _replace(
                original,
                r"@Library\('stacksmith(?:@v[^']+)?'\)",
                f"@Library('stacksmith@v{version}')",
                path,
            )
            updated = re.sub(
                r"(`STACKSMITH_IMAGE_VERSION`[^\n]*?Defaults to `)[^`]+(`)",
                rf"\g<1>{version}\2",
                updated,
            )
            updated = re.sub(
                r"(`STACKSMITH_IMAGE_VERSION` \(default `)[^`]+(`)",
                rf"\g<1>{version}\2",
                updated,
            )
        else:
            updated = _replace(
                original,
                r"(uses: ci-sourcerer/stacksmith/\.github/workflows/[^@\s]+@)[^\s]+",
                rf"\g<1>v{version}",
                path,
            )
        if updated != original:
            updates[path] = updated
    return updates


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    """Update Stacksmith CI pins to an explicit or pre-release-hook version.

    Args:
        None. Options are read from command-line arguments and release-hook variables.

    Returns:
        None.

    Raises:
        OSError: If CI files cannot be read or written.
        ValueError: If a template lacks its expected version pin.
        subprocess.CalledProcessError: If an optional local Git commit fails.
    """
    parser = argparse.ArgumentParser(
        description="Pin CI workflows and images to a Stacksmith release."
    )
    parser.add_argument("version", nargs="?", default=os.getenv("RELEASE_VERSION"))
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Create a local chore(release) commit; never push or tag.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the pins do not match, without writing files.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.version:
        parser.error("Provide a release version or set RELEASE_VERSION")
    try:
        version = Version(args.version)
    except InvalidVersion:
        parser.error("Provide a valid release version")
    if version.is_devrelease or version.is_postrelease or version.local:
        parser.error(
            "CI pins require a release version, without dev, post, or local suffixes"
        )
    if os.getenv("RELEASE_TAG", f"v{version}") != f"v{version}":
        parser.error("RELEASE_TAG must match RELEASE_VERSION")
    updates = _pin_updates(args.root, str(version))
    dry_run = args.dry_run or os.getenv("RELEASE_SCRIPT_DRY_RUN") == "1"
    if args.check or dry_run:
        print(
            f"{'Would update' if dry_run else 'Mismatched'} {len(updates)} CI pin files for v{version}"
        )
        if args.check and updates:
            sys.exit(1)
        return
    if args.commit and _git(args.root, "status", "--porcelain"):
        parser.error("Start with a clean working tree before committing release pins")
    for path, content in updates.items():
        path.write_text(content, encoding="utf-8")
    if args.commit and updates:
        _git(
            args.root,
            "add",
            "--",
            *(str(path.relative_to(args.root)) for path in updates),
        )
        _git(
            args.root,
            "commit",
            "-m",
            f"chore(release): pin CI workflows and images to {version}",
        )
    print(f"Pinned {len(updates)} CI files to v{version}; image tag {version}")


if __name__ == "__main__":
    main()
