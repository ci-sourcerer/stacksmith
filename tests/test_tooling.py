import threading
import time

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


def test_resolve_single_tool_serializes_shared_cache_install(monkeypatch, tmp_path):
    active_installs = 0
    maximum_active_installs = 0
    download_count = 0
    state_lock = threading.Lock()

    def fake_download(tool_name, tool_cfg, cache_root, auth_config):
        nonlocal active_installs, download_count, maximum_active_installs
        with state_lock:
            active_installs += 1
            download_count += 1
            maximum_active_installs = max(maximum_active_installs, active_installs)
        time.sleep(0.05)
        binary_path = cache_root / tool_name / tool_cfg.version / "bin" / tool_name
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("binary", encoding="utf-8")
        with state_lock:
            active_installs -= 1
        return binary_path

    monkeypatch.setattr(
        tooling, "_find_local_tool_binary", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(tooling, "_is_usable_tool_binary", lambda *args, **kwargs: True)
    monkeypatch.setattr(tooling, "_download_and_install_tool", fake_download)

    results: list[str] = []

    def resolve_tool() -> None:
        results.append(
            tooling._resolve_single_tool(
                "terragrunt",
                ToolBinaryConfig(version="1.5.0"),
                tmp_path / "cache",
                auth_config=None,
            )
        )

    threads = [threading.Thread(target=resolve_tool) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert download_count == 1
    assert maximum_active_installs == 1
