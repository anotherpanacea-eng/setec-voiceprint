#!/usr/bin/env python3
"""Regression tests for fetch_mage.py.

Mirrors test_fetch_raid.py's strategy: mock huggingface_hub via
sys.modules injection. Verify file selection, license
verification, and CLI behavior.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None


@pytest.fixture(autouse=True)
def _module_cleanup():
    """Snapshot sys.modules entries the tests overwrite, and
    restore them after each test."""
    saved = {
        name: sys.modules.get(name)
        for name in ("huggingface_hub", "fetch_mage")
    }
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _install_mock_huggingface_hub(
    *,
    license_str: str = "mit",
    revision: str = "abcd1234" * 5,
    repo_files: list[str] | None = None,
) -> None:
    if repo_files is None:
        repo_files = [
            "data/train-00000-of-00001.parquet",
            "data/validation-00000-of-00001.parquet",
            "data/test-00000-of-00001.parquet",
            "README.md",
        ]

    fake_hub = types.ModuleType("huggingface_hub")

    class _FakeCardData(dict):
        pass

    class _FakeDatasetInfo:
        def __init__(self) -> None:
            self.card_data = _FakeCardData(license=license_str)
            self.tags = [f"license:{license_str}"]
            self.sha = revision

    class _FakeApi:
        def __init__(self, token=None):
            self.token = token

        def dataset_info(self, repo_id):
            return _FakeDatasetInfo()

        def list_repo_files(self, repo_id, repo_type="dataset"):
            return list(repo_files)

    def _fake_hf_hub_download(
        *, repo_id, filename, repo_type, local_dir, token,
    ):
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00MAGE FAKE\x00")
        return str(out)

    fake_hub.HfApi = _FakeApi
    fake_hub.hf_hub_download = _fake_hf_hub_download
    sys.modules["huggingface_hub"] = fake_hub


def _import_fetch_mage():
    if "fetch_mage" in sys.modules:
        del sys.modules["fetch_mage"]
    import fetch_mage  # type: ignore
    return fetch_mage


# ---------- File selection ----------


class TestSelectFiles:
    def test_train_split(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/train-x.parquet",
            "data/validation-x.parquet",
            "data/test-x.parquet",
        ], "train")
        assert files == ["data/train-x.parquet"]

    def test_validation_synonym_val(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/val-x.parquet",
            "data/validation-x.parquet",
            "data/train-x.parquet",
        ], "validation")
        # Both val and validation should match.
        assert "data/val-x.parquet" in files
        assert "data/validation-x.parquet" in files
        assert "data/train-x.parquet" not in files

    def test_all_returns_every_parquet(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/train.parquet",
            "data/val.parquet",
            "data/test.parquet",
            "README.md",
        ], "all")
        assert len(files) == 3
        assert all(f.endswith(".parquet") for f in files)

    def test_unknown_split_raises(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        with pytest.raises(ValueError, match="Unknown split"):
            fm._select_files([], "completely_unknown")


# ---------- License verification ----------


class TestVerifyLicense:
    def test_mit_accepted(self):
        _install_mock_huggingface_hub(license_str="mit")
        fm = _import_fetch_mage()
        ok, observed = fm._verify_license(token=None)
        assert ok is True
        assert "mit" in observed

    def test_wrong_license_rejected(self):
        _install_mock_huggingface_hub(license_str="apache-2.0")
        fm = _import_fetch_mage()
        ok, _ = fm._verify_license(token=None)
        assert ok is False


# ---------- CLI ----------


class TestCli:
    def test_cli_dry_run(self, tmp_path, monkeypatch, capsys):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        rc = fm.main(["--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out

    def test_cli_full_flow_writes_notice_and_record(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fm, "REPO_ROOT", tmp_path.parent)
        rc = fm.main([])  # default: all splits
        assert rc == 0
        notice = tmp_path / "NOTICE.md"
        record = tmp_path / ".fetch_record.json"
        assert notice.is_file()
        assert record.is_file()
        notice_text = notice.read_text(encoding="utf-8")
        assert "MAGE" in notice_text
        assert "MIT" in notice_text
        record_data = json.loads(record.read_text(encoding="utf-8"))
        assert record_data["repo_id"] == "yaful/MAGE"
        assert record_data["split"] == "all"

    def test_cli_license_mismatch_returns_2(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub(license_str="apache-2.0")
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        rc = fm.main(["--dry-run"])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
