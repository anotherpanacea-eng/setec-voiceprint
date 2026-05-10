#!/usr/bin/env python3
"""Regression tests for mage_to_manifest.py.

Same pyarrow-mock strategy as test_raid_to_manifest.py.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None


@pytest.fixture(autouse=True)
def _pyarrow_cleanup():
    """See test_raid_to_manifest.py — restores sys.modules after
    each test so the pyarrow mock doesn't leak and break
    downstream tests that import sklearn (which reads
    pyarrow.__version__)."""
    saved = {
        name: sys.modules.get(name)
        for name in ("pyarrow", "pyarrow.parquet", "mage_to_manifest")
    }
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _install_mock_pyarrow(rows_by_file: dict[str, list[dict]]) -> None:
    fake_pa = types.ModuleType("pyarrow")
    fake_pq = types.ModuleType("pyarrow.parquet")
    fake_pa.__version__ = "0.0.0-mock"
    fake_pq.__version__ = "0.0.0-mock"

    class _FakeBatch:
        def __init__(self, rows):
            self._rows = rows

        def to_pylist(self):
            return list(self._rows)

    class _FakeParquetFile:
        def __init__(self, path):
            self._path = Path(path)

        def iter_batches(self):
            yield _FakeBatch(
                rows_by_file.get(self._path.name, []),
            )

    fake_pq.ParquetFile = _FakeParquetFile
    fake_pa.parquet = fake_pq
    sys.modules["pyarrow"] = fake_pa
    sys.modules["pyarrow.parquet"] = fake_pq


def _import_mage_to_manifest():
    if "mage_to_manifest" in sys.modules:
        del sys.modules["mage_to_manifest"]
    import mage_to_manifest  # type: ignore
    return mage_to_manifest


def _write_fake_parquet(dirpath: Path, name: str) -> Path:
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00FAKE PARQUET\x00")
    return p


# ---------- Label mapping ----------


class TestAiStatusForLabel:
    def test_label_0_is_human(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._ai_status_for_label(0) == "human"

    def test_label_1_is_ai(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._ai_status_for_label(1) == "ai"

    def test_unknown_label_returns_unknown(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._ai_status_for_label("not-a-number") == "unknown"
        assert mt._ai_status_for_label(None) == "unknown"
        assert mt._ai_status_for_label(99) == "unknown"


# ---------- Split inference ----------


class TestSplitForParquet:
    def test_train_inferred(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_parquet(
            "train-00000.parquet"
        ) == "train"

    def test_validation_inferred(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_parquet(
            "validation-x.parquet"
        ) == "val"

    def test_val_inferred(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_parquet("val-x.parquet") == "val"

    def test_test_inferred(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_parquet("test-x.parquet") == "test"

    def test_unknown_split(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_parquet("random.parquet") == "unknown"


# ---------- End-to-end ----------


class TestConvertEndToEnd:
    def test_basic_conversion(self, tmp_path):
        rows = [
            {"text": "Human text here.", "label": 0, "source": "cnn_dailymail"},
            {"text": "Machine text here.", "label": 1, "source": "gpt-4-turbo"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        (source_dir / ".fetch_record.json").write_text(
            json.dumps({"repo_id": "yaful/MAGE", "revision": "abc"}),
            encoding="utf-8",
        )
        _install_mock_pyarrow({"train-x.parquet": rows})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in manifest_path.read_text(
                encoding="utf-8",
            ).strip().splitlines()
        ]
        assert len(entries) == 2
        statuses = sorted(e["ai_status"] for e in entries)
        assert statuses == ["ai", "human"]
        for e in entries:
            assert e["source"] == "mage"
            assert e["privacy"] == "public"
            assert e["language_status"] == "native"
            assert e["editing_status"] == "unedited"
            assert e["register"] == "mixed"
            text_file = manifest_path.parent / e["path"]
            assert text_file.is_file()

    def test_unknown_label_rows_skipped(self, tmp_path):
        rows = [
            {"text": "valid", "label": 0, "source": "a"},
            {"text": "weird", "label": "nan", "source": "b"},
            {"text": "valid", "label": 1, "source": "c"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({"train-x.parquet": rows})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in manifest_path.read_text(
                encoding="utf-8",
            ).strip().splitlines()
        ]
        assert len(entries) == 2

    def test_empty_text_skipped(self, tmp_path):
        rows = [
            {"text": "", "label": 0, "source": "a"},
            {"text": "real", "label": 0, "source": "b"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({"train-x.parquet": rows})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in (
                source_dir / "manifest.jsonl"
            ).read_text(encoding="utf-8").strip().splitlines()
        ]
        assert len(entries) == 1

    def test_split_recorded_in_notes(self, tmp_path):
        rows = [{"text": "x", "label": 0, "source": "s"}]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "validation-x.parquet")
        _install_mock_pyarrow({"validation-x.parquet": rows})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 0
        entry = json.loads(
            (source_dir / "manifest.jsonl").read_text(
                encoding="utf-8",
            ).strip().splitlines()[0]
        )
        assert entry["notes"]["split"] == "val"

    def test_missing_source_dir_returns_1(self, tmp_path):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        import argparse
        args = argparse.Namespace(
            source_dir=str(tmp_path / "nope"),
            manifest=str(tmp_path / "m.jsonl"),
            text_dir=str(tmp_path / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 1

    def test_privacy_guard_blocks_public_output(self, tmp_path):
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({
            "train-x.parquet": [
                {"text": "x", "label": 0, "source": "s"},
            ],
        })
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(tmp_path / "PUBLIC" / "manifest.jsonl"),
            text_dir=str(tmp_path / "PUBLIC" / "text"),
            limit=0, allow_public_output=False,
        )
        rc = mt.convert(args)
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
