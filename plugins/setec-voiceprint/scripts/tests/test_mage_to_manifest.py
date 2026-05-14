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


def _write_real_csv(
    dirpath: Path, name: str, rows: list[dict],
) -> Path:
    """Drop a real CSV file with the supplied rows. HF ships
    MAGE as CSV at the repo root, so this is the on-disk shape
    the converter sees in production."""
    import csv as _csv
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8")
        return p
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = _csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return p


# ---------- Label mapping ----------


class TestAiStatusForLabel:
    def test_label_0_is_human(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._ai_status_for_label(0) == "pre_ai_human"

    def test_label_1_is_ai(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._ai_status_for_label(1) == "ai_generated"

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


class TestManifestValidatorRoundTrip:
    """End-to-end: the converter's output must pass
    `manifest_validator.validate_manifest` cleanly. v1.42.3
    maps converter output to the validator's allowed
    vocabularies and omits the `register` field rather than
    asserting a bogus value."""

    def test_converted_manifest_passes_validator(self, tmp_path):
        rows = [
            {"text": "Human prose.", "label": "0",
             "src": "cmv_human"},
            {"text": "Machine prose.", "label": "1",
             "src": "gpt-4-turbo"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
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
        assert mt.convert(args) == 0
        sys.path.insert(0, str(ROOT))
        import manifest_validator as mv  # type: ignore
        result = mv.validate_manifest(str(manifest_path))
        errors = [
            i for i in result.get("issues", [])
            if getattr(i, "level", None) == "error"
        ]
        assert not errors, (
            f"Validator returned errors on converted MAGE "
            f"manifest:\n"
            + "\n".join(str(e) for e in errors[:20])
        )

    def test_register_field_omitted(self, tmp_path):
        # MAGE entries should NOT carry a `register` field —
        # the source dataset varies per row and no single
        # validator-allowed value fits honestly.
        rows = [{"text": "x", "label": "0", "src": "s"}]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        assert mt.convert(args) == 0
        entry = json.loads(
            (source_dir / "manifest.jsonl").read_text(
                encoding="utf-8",
            ).strip().splitlines()[0]
        )
        assert "register" not in entry


class TestConvertEndToEndCSV:
    """End-to-end on the CSV input path. HF ships MAGE as
    `train.csv`, `valid.csv`, `test.csv`, plus two OOD slices
    (`test_ood_set_gpt.csv`, `test_ood_set_gpt_para.csv`)."""

    def test_csv_basic_conversion(self, tmp_path):
        rows = [
            {"text": "Human text here.", "label": "0",
             "source": "cnn_dailymail"},
            {"text": "Machine text here.", "label": "1",
             "source": "gpt-4-turbo"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
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
        assert statuses == ["ai_generated", "pre_ai_human"]
        # Notes block points at the CSV.
        for e in entries:
            assert e["notes"]["source_file"] == "train.csv"
            assert e["notes"]["split"] == "train"

    def test_ood_split_inferred_from_csv_name(self, tmp_path):
        # MAGE's OOD slices should be distinguishable in the
        # manifest so calibration runs can slice on them.
        rows = [{"text": "x", "label": "0", "source": "s"}]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(
            source_dir, "test_ood_set_gpt_para.csv", rows,
        )
        _install_mock_pyarrow({})
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
        entry = json.loads(
            manifest_path.read_text(
                encoding="utf-8",
            ).strip().splitlines()[0]
        )
        assert entry["notes"]["split"] == "test_ood_gpt_para"


class TestSplitForSourceFile:
    """The renamed helper recognizes both parquet- and CSV-style
    filename conventions, including MAGE's OOD slice names."""

    def test_recognizes_ood_gpt(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_source_file(
            "test_ood_set_gpt.csv"
        ) == "test_ood_gpt"

    def test_recognizes_ood_para(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_source_file(
            "test_ood_set_gpt_para.csv"
        ) == "test_ood_gpt_para"

    def test_valid_csv_is_val(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._split_for_source_file("valid.csv") == "val"

    def test_backwards_compatible_alias(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        # External callers that imported the old name still work.
        assert mt._split_for_parquet is mt._split_for_source_file


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
        assert statuses == ["ai_generated", "pre_ai_human"]
        for e in entries:
            assert e["source"] == "mage"
            assert e["privacy"] == "shareable"
            assert e["language_status"] == "native"
            assert e["editing_status"] == "raw_draft"
            assert "register" not in e
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


# ---------- v1.50.0+ (B.4): authorship-state refinements ----------


class TestB4OutlineSourceRouting:
    """``_ai_status_for_label(label, src, outline_sources)`` should
    return ``ai_generated_from_outline`` instead of ``ai_generated``
    when the row's src is in the configured outline-sources set.

    Case-insensitive + whitespace-tolerant — different MAGE exports
    use different src-column conventions (e.g.,
    ``"Hello-SimpleAI/HC3"`` vs ``"hello-simpleai-hc3"``), so the
    operator's outline-sources list should match without exact
    casing pedantry.
    """

    def test_default_outline_sources_is_empty(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        # Empty default = no row gets the outline refinement
        # without operator opt-in. Honest about the framework's
        # uncertainty about which MAGE subsets used outline-based
        # generation.
        assert mt.DEFAULT_OUTLINE_SOURCES == frozenset()

    def test_outline_source_routes_to_ai_generated_from_outline(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        result = mt._ai_status_for_label(
            1, src="hello-simpleai/hc3",
            outline_sources=frozenset({"hello-simpleai/hc3"}),
        )
        assert result == "ai_generated_from_outline"

    def test_outline_source_lookup_is_case_insensitive(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        result = mt._ai_status_for_label(
            1, src="Hello-SimpleAI/HC3",
            outline_sources=frozenset({"hello-simpleai/hc3"}),
        )
        assert result == "ai_generated_from_outline"

    def test_non_outline_source_stays_ai_generated(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        result = mt._ai_status_for_label(
            1, src="grover",
            outline_sources=frozenset({"hello-simpleai/hc3"}),
        )
        assert result == "ai_generated"

    def test_label_0_unaffected_by_outline_sources(self):
        """Human rows (label 0) should never become
        ai_generated_from_outline regardless of src match."""
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        result = mt._ai_status_for_label(
            0, src="hello-simpleai/hc3",
            outline_sources=frozenset({"hello-simpleai/hc3"}),
        )
        assert result == "pre_ai_human"


class TestB4ParaphraseDetection:
    """``_is_paraphrase_src`` heuristic + the convert() main loop
    flip from ai_generated → ai_edited when the heuristic fires."""

    def test_paraphrase_substring_matches(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._is_paraphrase_src("OOD-set-gpt-paraphrased") is True
        assert mt._is_paraphrase_src("dipper-attack") is True
        assert mt._is_paraphrase_src("foo_DIPPER_bar") is True
        # Case-insensitive.
        assert mt._is_paraphrase_src("PARAPHRASED") is True

    def test_non_paraphrase_returns_false(self):
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        assert mt._is_paraphrase_src("hc3") is False
        assert mt._is_paraphrase_src("grover") is False
        assert mt._is_paraphrase_src(None) is False
        assert mt._is_paraphrase_src("") is False

    def test_convert_remaps_paraphrase_row_to_ai_edited(self, tmp_path):
        """End-to-end: a row whose src indicates DIPPER paraphrase
        should land with ai_status=ai_edited and a
        notes.attack=dipper_paraphrase annotation."""
        rows = [
            {"text": "Original prose.", "label": "1", "src": "gpt4"},
            {"text": "Paraphrased prose.", "label": "1",
             "src": "OOD-set-gpt-paraphrased"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "test-ood-para.csv", rows)
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
            outline_sources="",
            no_paraphrase_detection=False,
        )
        assert mt.convert(args) == 0
        entries = [
            json.loads(line) for line in manifest_path.read_text().splitlines()
            if line
        ]
        assert len(entries) == 2
        # First row: normal ai_generated.
        assert entries[0]["ai_status"] == "ai_generated"
        assert "attack" not in entries[0].get("notes", {})
        # Second row: detected as paraphrase, remapped + annotated.
        assert entries[1]["ai_status"] == "ai_edited"
        assert entries[1]["notes"]["attack"] == "dipper_paraphrase"

    def test_no_paraphrase_detection_flag_disables_remap(self, tmp_path):
        """Operator can opt out of B.4 paraphrase detection via
        --no-paraphrase-detection. The paraphrase row then stays
        ai_generated with no attack annotation."""
        rows = [
            {"text": "Paraphrased prose.", "label": "1",
             "src": "OOD-set-gpt-paraphrased"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "test-ood-para.csv", rows)
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
            outline_sources="",
            no_paraphrase_detection=True,  # opt-out
        )
        assert mt.convert(args) == 0
        entry = json.loads(manifest_path.read_text().strip())
        assert entry["ai_status"] == "ai_generated"
        assert "attack" not in entry.get("notes", {})


class TestB4OutlineSourceEndToEnd:
    """End-to-end: a row whose src matches --outline-sources should
    land with ai_status=ai_generated_from_outline."""

    def test_outline_source_via_convert(self, tmp_path):
        rows = [
            {"text": "Outline-based prose.", "label": "1",
             "src": "hello-simpleai/hc3"},
            {"text": "Thin-prompt prose.", "label": "1",
             "src": "gpt4"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
            outline_sources="hello-simpleai/hc3",
            no_paraphrase_detection=False,
        )
        assert mt.convert(args) == 0
        entries = [
            json.loads(line) for line in manifest_path.read_text().splitlines()
            if line
        ]
        assert entries[0]["ai_status"] == "ai_generated_from_outline"
        assert entries[1]["ai_status"] == "ai_generated"


class TestB4BackwardsCompatibility:
    """The existing tests construct argparse.Namespace without the
    new outline_sources / no_paraphrase_detection fields. convert()
    must tolerate the missing attributes via getattr defaults so
    older test fixtures and any external callers don't break."""

    def test_convert_without_new_args_still_works(self, tmp_path):
        rows = [
            {"text": "Human prose.", "label": "0", "src": "cmv_human"},
            {"text": "AI prose.", "label": "1", "src": "gpt4"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "mage"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
        mt = _import_mage_to_manifest()
        mt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        # Namespace built without outline_sources / no_paraphrase_detection.
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, allow_public_output=False,
        )
        assert mt.convert(args) == 0
        entries = [
            json.loads(line) for line in manifest_path.read_text().splitlines()
            if line
        ]
        assert entries[0]["ai_status"] == "pre_ai_human"
        assert entries[1]["ai_status"] == "ai_generated"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
