import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _author(request: dict[str, object], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "stacksmith", "author"],
        input=json.dumps(request),
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "STACKSMITH_CONFIG": "/missing/config.yaml",
        },
    )


def test_authoring_cli_proposes_source_without_loading_env_or_writing_files(tmp_path):
    (tmp_path / ".env").write_text("STACKSMITH_CONFIG=/another/missing/config.yaml\n")
    source = "name: example\ncomponents:\n  app:\n    type: application\n"
    result = _author(
        {
            "version": 1,
            "source": source,
            "path": "stack.yaml",
            "edits": [
                {
                    "action": "set",
                    "path": ["components", "app", "properties"],
                    "value": {"image": "v2"},
                }
            ],
        },
        tmp_path,
    )
    response = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert response["version"] == 1
    assert response["validation"] == "document"
    assert response["changed"]
    assert "image: v2" in response["source"]
    assert "+++ b/stack.yaml" in response["diff"]
    assert not (tmp_path / "stack.yaml").exists()


@pytest.mark.parametrize(
    "payload, code",
    [
        (
            {"version": 2, "source": "name: example\n", "path": "stack.yaml"},
            "invalid_request",
        ),
        (
            {
                "version": 1,
                "source": "name: example\n",
                "path": "stack.yaml",
                "unexpected": True,
            },
            "invalid_request",
        ),
        (
            {
                "version": 1,
                "source": "name: example\n",
                "path": "stack.yaml",
                "edits": [{"action": "execute", "path": ["name"]}],
            },
            "invalid_request",
        ),
        (
            {"version": 1, "source": "{% invalid %}", "path": "stack.yaml"},
            "invalid_document",
        ),
        (
            {
                "version": 1,
                "source": "name: example\n",
                "path": "stack.yaml",
                "edits": [{"action": "set", "path": ["name"], "value": 1}],
            },
            "invalid_document",
        ),
    ],
)
def test_authoring_cli_reports_machine_readable_errors(payload, code, tmp_path):
    result = _author(payload, tmp_path)
    assert result.returncode == 6
    assert json.loads(result.stdout)["error"]["code"] == code


def test_authoring_cli_reads_request_file(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps({"version": 1, "source": '{"name":"example"}', "path": "stack.json"})
    )
    result = subprocess.run(
        [sys.executable, "-m", "stacksmith", "author", "--request", str(path)],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["source"] == '{"name":"example"}'
    assert not json.loads(result.stdout)["changed"]
