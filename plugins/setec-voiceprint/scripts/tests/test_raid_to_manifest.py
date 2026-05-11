#!/usr/bin/env python3
"""Regression tests for raid_to_manifest.py.

Mocks pyarrow.parquet via sys.modules so tests run without the
real pyarrow dependency. Verifies manifest mapping, the ai_status
/ editing_status / language_status mappings, and the privacy
guard on output paths.
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
    """Snapshot sys.modules before each test and restore after,
    so the pyarrow/pyarrow.parquet mocks injected by
    `_install_mock_pyarrow` don't leak into downstream tests.
    sklearn imports `pyarrow.__version__` and breaks if a mock
    without that attribute lingers in sys.modules."""
    saved = {
        name: sys.modules.get(name)
        for name in ("pyarrow", "pyarrow.parquet", "raid_to_manifest")
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
    """Inject a fake pyarrow.parquet that yields the supplied
    dict rows when ParquetFile(path).iter_batches() is called.

    `rows_by_file` maps `<basename>.parquet` → list of row
    dicts. The mock matches against the basename of the path
    passed to ParquetFile.

    The autouse `_pyarrow_cleanup` fixture restores sys.modules
    after each test so this mock doesn't leak.
    """
    fake_pa = types.ModuleType("pyarrow")
    fake_pq = types.ModuleType("pyarrow.parquet")
    # Add the attributes downstream importers (sklearn, pandas)
    # look up. Without these, a leftover mock would break any
    # later test that imports sklearn or pandas, because both
    # read pyarrow.__version__ during their optional-pyarrow
    # detection path.
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
            rows = rows_by_file.get(self._path.name, [])
            # Single-batch for simplicity.
            yield _FakeBatch(rows)

    fake_pq.ParquetFile = _FakeParquetFile
    fake_pa.parquet = fake_pq
    sys.modules["pyarrow"] = fake_pa
    sys.modules["pyarrow.parquet"] = fake_pq


def _import_raid_to_manifest():
    if "raid_to_manifest" in sys.modules:
        del sys.modules["raid_to_manifest"]
    import raid_to_manifest  # type: ignore
    return raid_to_manifest


def _write_fake_parquet(dirpath: Path, name: str) -> Path:
    """Drop a placeholder file with the parquet extension so the
    converter's rglob picks it up. Contents don't matter; the
    pyarrow mock reads rows by basename."""
    p = dirpath / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00FAKE PARQUET\x00")
    return p


def _write_real_csv(
    dirpath: Path, name: str, rows: list[dict],
) -> Path:
    """Drop a real CSV file with the supplied rows. Tests the
    converter's stdlib-csv path end-to-end without the pyarrow
    mock — HuggingFace ships RAID/MAGE as CSV, so this is the
    on-disk shape the converter actually sees in production."""
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


# ---------- Status mapping ----------


class TestAiStatusMapping:
    def test_human_model_returns_human(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._ai_status_for_row({"model": "human"}) == "pre_ai_human"

    def test_empty_model_returns_human(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._ai_status_for_row({"model": ""}) == "pre_ai_human"

    def test_llm_model_returns_ai(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._ai_status_for_row({"model": "gpt-4"}) == "ai_generated"

    def test_missing_model_returns_human(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._ai_status_for_row({}) == "pre_ai_human"


class TestEditingStatusMapping:
    def test_no_attack_returns_unedited(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._editing_status_for_row({"attack": "none"}) == "raw_draft"

    def test_empty_attack_returns_unedited(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._editing_status_for_row({"attack": ""}) == "raw_draft"

    def test_paraphrase_attack_still_raw_draft_for_validator_compat(self):
        # The validator's allowed editing_status set doesn't
        # have an "adversarial" tier. Adversarial info lives in
        # notes.attack; editing_status stays at raw_draft for
        # all rows. R7's robustness card reads notes.attack to
        # slice per-attack.
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._editing_status_for_row(
            {"attack": "paraphrase"}
        ) == "raw_draft"


class TestAttackTokenForRow:
    """The attack token gets preserved in notes.attack
    independently of editing_status."""

    def test_paraphrase_token(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._attack_token_for_row(
            {"attack": "paraphrase"}
        ) == "paraphrase"

    def test_none_or_missing(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._attack_token_for_row(
            {"attack": "none"}
        ) == "none"
        assert rt._attack_token_for_row({}) == "none"


class TestRegisterMapping:
    """RAID domain → manifest_validator.ALLOWED_REGISTER. Domains
    without a clean fit return None (the converter then omits
    the register field entirely; the raw domain stays in
    notes.domain)."""

    def test_news_maps_to_blog_essay(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "news"}
        ) == "blog_essay"

    def test_books_maps_to_literary_fiction(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "books"}
        ) == "literary_fiction"

    def test_abstracts_maps_to_academic_philosophy(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "abstracts"}
        ) == "academic_philosophy"

    def test_reddit_maps_to_personal(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "reddit"}
        ) == "personal"

    def test_code_returns_none(self):
        # Code has no register match in the validator's vocabulary.
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "code"}
        ) is None

    def test_czech_returns_none(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "czech"}
        ) is None

    def test_unknown_domain_returns_none(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._register_for_row(
            {"domain": "nonexistent"}
        ) is None


class TestLanguageStatusMapping:
    def test_english_domain_returns_native(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._language_status_for_row(
            {"domain": "news"}
        ) == "native"

    def test_czech_returns_non_native_advanced(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._language_status_for_row(
            {"domain": "czech"}
        ) == "non_native_advanced"

    def test_german_returns_non_native_advanced(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._language_status_for_row(
            {"domain": "german"}
        ) == "non_native_advanced"

    def test_code_returns_unknown(self):
        # Code isn't a natural language; SETEC has no business
        # adjudicating its variance against an English baseline.
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        assert rt._language_status_for_row(
            {"domain": "code"}
        ) == "unknown"


# ---------- Bucketed text path ----------


class TestBucketedTextPath:
    def test_two_level_buckets(self):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        p = rt._bucketed_text_path(
            Path("/tmp/text"), "raid_train_0001",
        )
        # Should be /tmp/text/<hex>/<hex>/raid_train_0001.txt
        parts = p.relative_to("/tmp/text").parts
        assert len(parts) == 3
        assert all(len(parts[i]) == 2 for i in range(2))
        assert parts[-1] == "raid_train_0001.txt"


# ---------- End-to-end convert ----------


class TestManifestValidatorRoundTrip:
    """End-to-end: the converter's output must pass
    `manifest_validator.validate_manifest` cleanly. v1.42.0
    shipped a converter that hit the validator's
    `ALLOWED_AI_STATUS` / `ALLOWED_PRIVACY` /
    `ALLOWED_EDITING_STATUS` / `ALLOWED_REGISTER` vocabularies
    with the wrong values (`ai`, `public`, `unedited`, raw
    domain names); v1.42.3 maps to validator vocabulary. This
    test catches any future schema drift between the converter
    and the validator."""

    def test_converted_manifest_passes_validator(self, tmp_path):
        rows = [
            # English domains: news → blog_essay,
            # books → literary_fiction.
            {"id": "1", "source_id": "src_1", "model": "human",
             "decoding": "n/a", "repetition_penalty": "",
             "attack": "none", "domain": "news",
             "title": "T", "prompt": "p",
             "generation": "Real prose here.",
             "adv_source_id": ""},
            {"id": "2", "source_id": "src_2", "model": "gpt-4",
             "decoding": "greedy", "repetition_penalty": "1.0",
             "attack": "paraphrase", "domain": "books",
             "title": "T2", "prompt": "p",
             "generation": "More prose.",
             "adv_source_id": "1"},
            # Czech: non_native_advanced; register omitted.
            {"id": "3", "source_id": "src_3", "model": "human",
             "decoding": "", "repetition_penalty": "",
             "attack": "none", "domain": "czech",
             "title": "T3", "prompt": "p",
             "generation": "Cesky text.",
             "adv_source_id": ""},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        assert rt.convert(args) == 0
        # Now run the validator. It returns (errors, warnings)
        # — we accept warnings (the impostor-track ratchets emit
        # warnings on shareable-no-consent-status entries) but
        # refuse any errors.
        sys.path.insert(0, str(ROOT))
        import manifest_validator as mv  # type: ignore
        result = mv.validate_manifest(str(manifest_path))
        errors = [
            i for i in result.get("issues", [])
            if getattr(i, "level", None) == "error"
        ]
        assert not errors, (
            f"Validator returned errors on the converted "
            f"manifest:\n"
            + "\n".join(str(e) for e in errors[:20])
        )

    def test_omitted_register_for_unmappable_domains(
        self, tmp_path,
    ):
        # Code domain → no register; the manifest entry should
        # OMIT the register field rather than write a bad value.
        rows = [
            {"id": "1", "model": "human", "attack": "none",
             "domain": "code", "generation": "def foo(): pass"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "extra.csv", rows)
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        assert rt.convert(args) == 0
        entry = json.loads(
            manifest_path.read_text(
                encoding="utf-8",
            ).strip().splitlines()[0]
        )
        # register field is intentionally absent.
        assert "register" not in entry
        # raw domain preserved in notes.
        assert entry["notes"]["domain"] == "code"


class TestConvertEndToEndCSV:
    """End-to-end coverage of the CSV input path. HuggingFace
    ships RAID at the repo root as `train.csv` / `test.csv` /
    `extra.csv`, so this is the actual on-disk shape the
    converter sees in production."""

    def test_csv_basic_conversion(self, tmp_path):
        rows = [
            {"id": "1", "source_id": "src_1", "model": "human",
             "decoding": "n/a", "repetition_penalty": "",
             "attack": "none", "domain": "news",
             "title": "T1", "prompt": "p",
             "generation": "Human prose here.",
             "adv_source_id": ""},
            {"id": "2", "source_id": "src_1", "model": "gpt-4",
             "decoding": "greedy", "repetition_penalty": "1.0",
             "attack": "none", "domain": "news",
             "title": "T1", "prompt": "p",
             "generation": "Machine prose here.",
             "adv_source_id": ""},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})  # no parquet files; pyarrow path unused
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        manifest_path = source_dir / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
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
        # The notes block points at the CSV file we wrote.
        for e in entries:
            assert e["notes"]["source_file"] == "train.csv"

    def test_csv_with_adversarial_rows(self, tmp_path):
        rows = [
            {"id": "1", "model": "gpt-4", "attack": "none",
             "domain": "news", "generation": "base text"},
            {"id": "2", "model": "gpt-4", "attack": "paraphrase",
             "domain": "news", "generation": "paraphrased"},
            {"id": "3", "model": "gpt-4", "attack": "homoglyph",
             "domain": "news", "generation": "homoglyph attack"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_real_csv(source_dir, "train.csv", rows)
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=True, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in (
                source_dir / "manifest.jsonl"
            ).read_text(encoding="utf-8").strip().splitlines()
        ]
        # With --no-adversarial, only the attack=none row survives.
        assert len(entries) == 1
        assert entries[0]["editing_status"] == "raw_draft"


class TestConvertEndToEnd:
    def test_basic_conversion(self, tmp_path):
        rows = [
            {
                "id": 1, "source_id": "src_1", "model": "human",
                "decoding": "n/a", "repetition_penalty": None,
                "attack": "none", "domain": "news",
                "title": "T1", "prompt": "p", "generation": "Human prose here.",
                "adv_source_id": None,
            },
            {
                "id": 2, "source_id": "src_1", "model": "gpt-4",
                "decoding": "greedy", "repetition_penalty": 1.0,
                "attack": "none", "domain": "news",
                "title": "T1", "prompt": "p",
                "generation": "Machine prose here.",
                "adv_source_id": None,
            },
        ]
        # Set up private dir + source dir under tmp.
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-00000.parquet")
        # Drop a fetch_record so the manifest carries revision.
        (source_dir / ".fetch_record.json").write_text(
            json.dumps({
                "repo_id": "liamdugan/raid",
                "revision": "abc123",
            }),
            encoding="utf-8",
        )

        _install_mock_pyarrow({"train-00000.parquet": rows})
        rt = _import_raid_to_manifest()
        import argparse
        # Re-point PRIVATE_DIR so the privacy guard passes.
        rt.PRIVATE_DIR = private_dir

        manifest_path = source_dir / "manifest.jsonl"
        text_dir = source_dir / "text"

        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(manifest_path),
            text_dir=str(text_dir),
            limit=0,
            no_adversarial=False,
            no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        assert manifest_path.is_file()
        entries = [
            json.loads(line)
            for line in manifest_path.read_text(
                encoding="utf-8",
            ).strip().splitlines()
        ]
        assert len(entries) == 2
        ai_statuses = sorted(e["ai_status"] for e in entries)
        assert ai_statuses == ["ai_generated", "pre_ai_human"]
        for e in entries:
            assert e["source"] == "raid"
            assert e["privacy"] == "shareable"
            assert e["use"] == ["validation"]
            assert e["editing_status"] == "raw_draft"
            assert e["language_status"] == "native"
            # Text file should exist at the path the manifest cites.
            text_file = manifest_path.parent / e["path"]
            assert text_file.is_file()

    def test_no_adversarial_filters_attack_rows(self, tmp_path):
        rows = [
            {"id": 1, "model": "gpt-4", "attack": "none",
             "domain": "news", "generation": "base text"},
            {"id": 2, "model": "gpt-4", "attack": "paraphrase",
             "domain": "news", "generation": "paraphrased text"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-00000.parquet")
        _install_mock_pyarrow({"train-00000.parquet": rows})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=True, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        manifest = source_dir / "manifest.jsonl"
        entries = [
            json.loads(line)
            for line in manifest.read_text(
                encoding="utf-8",
            ).strip().splitlines()
        ]
        assert len(entries) == 1
        assert entries[0]["editing_status"] == "raw_draft"

    def test_no_nonprose_filters_code_domain(self, tmp_path):
        rows = [
            {"id": 1, "model": "human", "attack": "none",
             "domain": "code", "generation": "def foo(): pass"},
            {"id": 2, "model": "human", "attack": "none",
             "domain": "news", "generation": "news text"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "extra-00000.parquet")
        _install_mock_pyarrow({"extra-00000.parquet": rows})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=False, no_nonprose=True,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in (
                source_dir / "manifest.jsonl"
            ).read_text(encoding="utf-8").strip().splitlines()
        ]
        assert len(entries) == 1
        # news domain → blog_essay per validator vocabulary;
        # raw domain preserved in notes.
        assert entries[0]["register"] == "blog_essay"
        assert entries[0]["notes"]["domain"] == "news"

    def test_empty_generations_skipped(self, tmp_path):
        rows = [
            {"id": 1, "model": "human", "attack": "none",
             "domain": "news", "generation": ""},
            {"id": 2, "model": "human", "attack": "none",
             "domain": "news", "generation": "real text"},
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({"train-x.parquet": rows})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        n = sum(1 for _ in (
            source_dir / "manifest.jsonl"
        ).read_text(encoding="utf-8").strip().splitlines() if _)
        assert n == 1

    def test_limit_respected(self, tmp_path):
        rows = [
            {"id": i, "model": "human", "attack": "none",
             "domain": "news", "generation": f"text {i}"}
            for i in range(10)
        ]
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({"train-x.parquet": rows})
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(source_dir / "manifest.jsonl"),
            text_dir=str(source_dir / "text"),
            limit=3, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 0
        entries = [
            json.loads(line)
            for line in (
                source_dir / "manifest.jsonl"
            ).read_text(encoding="utf-8").strip().splitlines()
        ]
        assert len(entries) == 3

    def test_missing_source_dir_returns_1(self, tmp_path):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        import argparse
        args = argparse.Namespace(
            source_dir=str(tmp_path / "nonexistent"),
            manifest=str(tmp_path / "manifest.jsonl"),
            text_dir=str(tmp_path / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 1

    def test_empty_source_dir_returns_1(self, tmp_path):
        _install_mock_pyarrow({})
        rt = _import_raid_to_manifest()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        import argparse
        args = argparse.Namespace(
            source_dir=str(empty_dir),
            manifest=str(tmp_path / "manifest.jsonl"),
            text_dir=str(tmp_path / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 1

    def test_privacy_guard_blocks_public_path(self, tmp_path):
        # Source under private; but try to write manifest OUTSIDE.
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({
            "train-x.parquet": [
                {"id": 1, "model": "human", "attack": "none",
                 "domain": "news", "generation": "text"},
            ],
        })
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        outside_path = tmp_path / "PUBLIC" / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(outside_path),
            text_dir=str(tmp_path / "PUBLIC" / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=False,
        )
        rc = rt.convert(args)
        assert rc == 2

    def test_allow_public_output_overrides_guard(self, tmp_path):
        private_dir = tmp_path / "private"
        source_dir = private_dir / "raid"
        source_dir.mkdir(parents=True)
        _write_fake_parquet(source_dir, "train-x.parquet")
        _install_mock_pyarrow({
            "train-x.parquet": [
                {"id": 1, "model": "human", "attack": "none",
                 "domain": "news", "generation": "text"},
            ],
        })
        rt = _import_raid_to_manifest()
        rt.PRIVATE_DIR = private_dir
        import argparse
        public_manifest = tmp_path / "PUBLIC" / "manifest.jsonl"
        args = argparse.Namespace(
            source_dir=str(source_dir),
            manifest=str(public_manifest),
            text_dir=str(tmp_path / "PUBLIC" / "text"),
            limit=0, no_adversarial=False, no_nonprose=False,
            allow_public_output=True,
        )
        rc = rt.convert(args)
        assert rc == 0
        assert public_manifest.is_file()


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
