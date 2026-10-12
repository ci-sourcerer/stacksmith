import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / "scripts/update_ci_pins.py"


@pytest.fixture
def _pin_checkout(tmp_path: Path) -> Path:
    for path in (
        _ROOT / ".github/workflows/stacksmith-gitops-opinionated-reusable.yml",
        _ROOT / ".github/workflows/stacksmith-gitops-reusable.yml",
        _ROOT / "jenkins/vars/stacksmith.groovy",
        *(_ROOT / "examples/github-actions").glob("stacksmith-*.yml"),
        _ROOT / "docs/integrations/ci.md",
    ):
        target = tmp_path / path.relative_to(_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return tmp_path


def _invoke(root: Path, *arguments: str, extra_env: dict[str, str] | None = None):
    environment = os.environ.copy()
    for name in ("RELEASE_VERSION", "RELEASE_TAG", "RELEASE_SCRIPT_DRY_RUN"):
        environment.pop(name, None)
    environment.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root), *arguments],
        env=environment,
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        shell=False,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.parametrize("version", ["1.2.3", "1.2.3rc1"])
def test_pin_task_coordinates_workflow_image_and_library_refs(
    _pin_checkout: Path, version: str
):
    assert _invoke(_pin_checkout, version).returncode == 0
    executor = yaml.load(
        (
            _pin_checkout / ".github/workflows/stacksmith-gitops-reusable.yml"
        ).read_text(),
        Loader=yaml.BaseLoader,
    )
    assert (
        executor["on"]["workflow_call"]["inputs"]["image_version"]["default"] == version
    )
    for path in (_pin_checkout / "examples/github-actions").glob("stacksmith-*.yml"):
        workflow = yaml.safe_load(path.read_text())
        assert all(
            job["uses"].endswith(f"@v{version}") for job in workflow["jobs"].values()
        )
    assert (
        f"STACKSMITH_IMAGE_VERSION ?: '{version}'"
        in (_pin_checkout / "jenkins/vars/stacksmith.groovy").read_text()
    )
    assert (
        f"@Library('stacksmith@v{version}')"
        in (_pin_checkout / "docs/integrations/ci.md").read_text()
    )
    assert _invoke(_pin_checkout, version, "--check").returncode == 0
    original = (_pin_checkout / "jenkins/vars/stacksmith.groovy").read_bytes()
    assert _invoke(_pin_checkout, "2.0.0", "--check").returncode == 1
    assert (_pin_checkout / "jenkins/vars/stacksmith.groovy").read_bytes() == original
    assert _invoke(_pin_checkout, "2.0.0").returncode == 0
    assert _invoke(_pin_checkout, "2.0.0", "--check").returncode == 0


def test_release_hook_dry_run_never_writes_or_commits(_pin_checkout: Path):
    originals = {
        path: path.read_bytes() for path in _pin_checkout.rglob("*") if path.is_file()
    }
    result = _invoke(
        _pin_checkout,
        "--commit",
        extra_env={
            "RELEASE_VERSION": "1.2.3",
            "RELEASE_TAG": "v1.2.3",
            "RELEASE_SCRIPT_DRY_RUN": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Would update" in result.stdout
    assert {
        path: path.read_bytes() for path in _pin_checkout.rglob("*") if path.is_file()
    } == originals


def test_pin_task_makes_only_a_local_conventional_commit(_pin_checkout: Path):
    _git(_pin_checkout, "init", "-q")
    _git(_pin_checkout, "config", "user.name", "Release Test")
    _git(_pin_checkout, "config", "user.email", "release@example.invalid")
    _git(_pin_checkout, "config", "commit.gpgsign", "false")
    _git(_pin_checkout, "add", ".")
    _git(_pin_checkout, "commit", "-qm", "chore(test): prepare CI templates")

    result = _invoke(
        _pin_checkout,
        "--commit",
        extra_env={
            "RELEASE_VERSION": "1.2.3",
            "RELEASE_TAG": "v1.2.3",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (
        _git(_pin_checkout, "log", "-1", "--format=%s")
        == "chore(release): pin CI workflows and images to 1.2.3"
    )
    assert _git(_pin_checkout, "status", "--porcelain") == ""
    assert _git(_pin_checkout, "tag") == ""
    assert _git(_pin_checkout, "remote") == ""
    (_pin_checkout / "unrelated.txt").write_text("keep this uncommitted")
    assert _invoke(_pin_checkout, "2.0.0", "--commit").returncode == 2
    assert _invoke(_pin_checkout, "1.2.3", "--check").returncode == 0
    assert _git(_pin_checkout, "status", "--porcelain") == "?? unrelated.txt"


@pytest.mark.parametrize(
    "version", ["main", "1.2.3.dev1", "1.2.3+local", "1.2.3.post1"]
)
def test_pin_task_rejects_nonrelease_versions(_pin_checkout: Path, version: str):
    assert _invoke(_pin_checkout, version).returncode == 2


def test_release_hook_rejects_mismatched_tag(_pin_checkout: Path):
    assert (
        _invoke(
            _pin_checkout,
            extra_env={
                "RELEASE_VERSION": "1.2.3",
                "RELEASE_TAG": "v2.0.0",
            },
        ).returncode
        == 2
    )
