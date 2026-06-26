import json
from pathlib import Path

import pytest
from stacksmith.vendor import (
    DEFAULT_VENDOR_DIR,
    MANIFEST_FILENAME,
    get_vendor_dir,
    load_vendor_manifest,
    resolve_module_source,
    vendor_key,
    vendor_path,
    write_vendor_manifest,
)


class TestVendorKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key_a = vendor_key("https://github.com/org/mod.git", "1.0.0")
        key_b = vendor_key("https://github.com/org/mod.git", "1.0.0")
        assert key_a == key_b

    def test_different_versions_produce_different_keys(self):
        key_a = vendor_key("https://github.com/org/mod.git", "1.0.0")
        key_b = vendor_key("https://github.com/org/mod.git", "2.0.0")
        assert key_a != key_b

    def test_different_sources_produce_different_keys(self):
        key_a = vendor_key("https://github.com/org/mod-a.git", "1.0.0")
        key_b = vendor_key("https://github.com/org/mod-b.git", "1.0.0")
        assert key_a != key_b

    def test_length_is_16_hex(self):
        key = vendor_key("https://github.com/org/mod.git", "1.0.0")
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)


class TestVendorPath:
    def test_returns_path_under_vendor_dir(self, tmp_path: Path):
        path = vendor_path("https://github.com/org/mod.git", "1.0.0", tmp_path)
        assert path.parent == tmp_path

    def test_uses_default_vendor_dir(self):
        path = vendor_path("https://github.com/org/mod.git", "1.0.0")
        assert path.parent == DEFAULT_VENDOR_DIR

    def test_get_vendor_dir_uses_environment_override(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.setenv("STACKSMITH_VENDOR_DIR", str(tmp_path))
        assert get_vendor_dir() == tmp_path


class TestResolveModuleSource:
    def test_returns_local_path_when_directory_exists(self, tmp_path: Path):
        source = "https://github.com/org/mod.git"
        version = "1.0.0"
        expected = vendor_path(source, version, tmp_path)
        expected.mkdir(parents=True)

        result = resolve_module_source(source, version, vendor_dir=tmp_path)
        assert result == str(expected)

    def test_raises_when_directory_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Vendored module not found"):
            resolve_module_source(
                "https://github.com/org/mod.git", "1.0.0", vendor_dir=tmp_path
            )


class TestManifest:
    def test_write_and_load_roundtrip(self, tmp_path: Path):
        modules = {
            "aws_s3_bucket": ("https://github.com/org/s3.git", "1.0.0"),
            "aws_ec2_instance": ("https://github.com/org/ec2.git", "2.0.0"),
        }
        manifest_path = write_vendor_manifest(modules, tmp_path)

        assert manifest_path == tmp_path / MANIFEST_FILENAME
        assert manifest_path.exists()

        loaded = load_vendor_manifest(tmp_path)
        assert len(loaded) == 2
        for module_type, (source, version) in modules.items():
            key = vendor_key(source, version)
            assert key in loaded
            assert loaded[key]["source"] == source
            assert loaded[key]["version"] == version
            assert loaded[key]["module_type"] == module_type

    def test_manifest_is_valid_json(self, tmp_path: Path):
        write_vendor_manifest(
            {"mod": ("https://github.com/org/m.git", "0.1.0")}, tmp_path
        )
        raw = (tmp_path / MANIFEST_FILENAME).read_text()
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_load_raises_when_missing(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_vendor_manifest(tmp_path)
