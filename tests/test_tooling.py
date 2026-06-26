from stacksmith import tooling
from stacksmith.models import ToolBinaryConfig


def test_resolve_single_tool_ignores_invalid_cached_binary(monkeypatch, tmp_path):
    cache_root = tmp_path / "cache"
    binary_dir = cache_root / "terragrunt" / "1.5.0" / "bin"
    binary_dir.mkdir(parents=True)
    binary_path = binary_dir / "terragrunt"
    binary_path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    binary_path.chmod(0o755)

    download_called = False

    def fake_download(*args, **kwargs):
        nonlocal download_called
        download_called = True
        return tmp_path / "downloaded" / "terragrunt"

    monkeypatch.setattr(
        tooling, "_find_local_tool_binary", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(tooling, "_download_and_install_tool", fake_download)
    monkeypatch.setattr(tooling, "_probe_version", lambda *args, **kwargs: None)

    resolved = tooling._resolve_single_tool(
        "terragrunt",
        ToolBinaryConfig(version=">=1.5.0,<2.0.0"),
        cache_root,
        auth_config=None,
    )

    assert download_called is True
    assert resolved == str(tmp_path / "downloaded" / "terragrunt")
