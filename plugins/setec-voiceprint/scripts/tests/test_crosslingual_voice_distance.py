#!/usr/bin/env python3
"""Tests for crosslingual_voice_distance.py — parser-free, language-agnostic distance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import crosslingual_voice_distance as cvd  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

A_TEXT = ("the quick brown fox jumps over the lazy dog " * 60)   # ~540 words
B_TEXT = ("zyzzyx vrooom qwxk blipp gronk skronk " * 100)        # ~600 words
ES_TEXT = ("el rápido zorro café salta sobre el perro perezoso " * 60)


def _baseline(tmp_path, name, text, copies=3):
    bdir = tmp_path / name
    bdir.mkdir()
    for i in range(copies):
        (bdir / f"f{i}.txt").write_text(text, encoding="utf-8")
    return bdir


def test_task_surface_is_voice_coherence():
    assert cvd.TASK_SURFACE == "voice_coherence"
    assert cvd.TASK_SURFACE in VALID_TASK_SURFACES


def test_envelope_shape_validates(tmp_path):
    bdir = _baseline(tmp_path, "b", A_TEXT)
    target = tmp_path / "t.txt"
    target.write_text(A_TEXT, encoding="utf-8")
    out = tmp_path / "o.json"
    assert cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                     "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["task_surface"] == "voice_coherence"
    assert payload["baseline"]["n_files"] == 3
    for key in ("lang", "delta", "cosine_distance", "per_baseline_file",
                "top_contributing_ngrams"):
        assert key in payload["results"]


def test_no_spacy_import():
    src = Path(cvd.__file__).read_text(encoding="utf-8")
    assert "import spacy" not in src
    assert "en_core_web" not in src


def test_self_distance_small():
    res = cvd.compute_distance(A_TEXT, [A_TEXT, A_TEXT, A_TEXT], n=3, top_k=200)
    assert res["delta"] < 0.05
    assert res["cosine_distance"] < 0.05


def test_distinct_text_larger_distance():
    self_res = cvd.compute_distance(A_TEXT, [A_TEXT, A_TEXT, A_TEXT], n=3, top_k=200)
    diff_res = cvd.compute_distance(B_TEXT, [A_TEXT, A_TEXT, A_TEXT], n=3, top_k=200)
    assert diff_res["delta"] > self_res["delta"]


def test_non_ascii_text():
    prof = cvd.aux_profile(ES_TEXT)
    assert prof["non_ascii_letter_ratio"] > 0


def test_lang_recorded(tmp_path):
    bdir = _baseline(tmp_path, "b", ES_TEXT)
    target = tmp_path / "t.txt"
    target.write_text(ES_TEXT, encoding="utf-8")
    out = tmp_path / "o.json"
    assert cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "es",
                     "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["results"]["lang"] == "es"
    assert "es" in payload["claim_license"]["language_match"]


def test_baseline_required(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(A_TEXT, encoding="utf-8")
    with pytest.raises(SystemExit):
        cvd.main([str(target), "--lang", "en"])


def test_claim_license_refuses_morphology_and_crosslang():
    dn = cvd._claim_license("en").does_not_license.lower()
    assert "morpholog" in dn and ("cross-language" in dn or "cross language" in dn)


def test_too_short_unavailable(tmp_path):
    bdir = _baseline(tmp_path, "b", A_TEXT)
    target = tmp_path / "short.txt"
    target.write_text("just a few words here please", encoding="utf-8")
    out = tmp_path / "o.json"
    assert cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                     "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["available"] is False


def test_empty_baseline_files_unavailable(tmp_path):
    # A baseline dir of whitespace-only files must NOT read as available
    # (would otherwise produce a distance against zero vectors, baseline.words==0).
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "blank1.txt").write_text("   \n\n  \t\n", encoding="utf-8")
    (bdir / "blank2.txt").write_text("", encoding="utf-8")
    target = tmp_path / "t.txt"
    target.write_text(A_TEXT, encoding="utf-8")
    out = tmp_path / "o.json"
    assert cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                     "--json", "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["available"] is False


def test_load_baseline_skips_empty(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "good.txt").write_text(A_TEXT, encoding="utf-8")
    (bdir / "blank.txt").write_text("   \n", encoding="utf-8")
    texts, loaded, words = cvd._load_baseline(str(bdir))
    assert len(texts) == 1 and words > 0


def test_deterministic():
    a = cvd.compute_distance(B_TEXT, [A_TEXT, B_TEXT], n=3, top_k=150)
    b = cvd.compute_distance(B_TEXT, [A_TEXT, B_TEXT], n=3, top_k=150)
    assert a == b
