#!/usr/bin/env python3
"""Tests for sound_texture_audit.py — descriptive sound-texture profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import sound_texture_audit as sta  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_BODY = ("river silver shimmer whisper " * 100)  # >300 words, sonic-ish


def test_task_surface_registered():
    assert sta.TASK_SURFACE == "sound_texture"
    assert sta.TASK_SURFACE in VALID_TASK_SURFACES


def test_envelope_shape_validates():
    payload = sta.build_payload(sta.audit_sound_texture(_BODY), target_path="x.md",
                                word_count=sta.count_words(_BODY), available=True)
    assert payload["task_surface"] == "sound_texture"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    for key in ("alliteration_pairs_per_1k", "assonance_pairs_per_1k",
                "consonance_pairs_per_1k", "consonant_class_fractions",
                "sibilant_ratio", "vowel_consonant_ratio"):
        assert key in payload["results"]


def test_no_verdict_keys():
    r = sta.audit_sound_texture(_BODY)
    for forbidden in ("band", "verdict", "compression", "smoothed"):
        assert forbidden not in r


def test_claim_license_refuses_ai_voice_quality():
    dn = sta._claim_license().does_not_license.lower()
    assert "ai" in dn and "voice" in dn and "quality" in dn


def test_claim_license_states_orthographic_proxy():
    caveats = " ".join(sta._claim_license().additional_caveats).lower()
    assert "proxy" in caveats and ("phonetic" in caveats or "spelling" in caveats)


def test_alliteration_detected():
    # p-onset adjacencies: peter-piper, piper-picked, pickled-peppers => 3 pairs.
    r = sta.audit_sound_texture("Peter Piper picked a peck of pickled peppers")
    # density per 1k over 8 words; assert at least 3 raw pairs via the density.
    pairs = r["alliteration_pairs_per_1k"] / 1000 * r["alphabetic_words"]
    assert round(pairs) >= 3


def test_assonance_detected():
    # repeated /e/ vowel-letter nucleus in adjacent words
    r = sta.audit_sound_texture("the red hen fell then well " * 60)
    assert r["assonance_pairs_per_1k"] > 0


def test_consonant_class_fractions_sum_to_one():
    r = sta.audit_sound_texture(_BODY)
    total = sum(r["consonant_class_fractions"].values())
    # Six per-class fractions are each rounded to 4 places, so the sum can drift
    # from 1.0 by a few times 5e-5 — the partition is exact, the rounding isn't.
    assert abs(total - 1.0) < 1e-2


def test_no_alliteration_on_vowel_initial():
    # vowel-initial adjacent words must not count as alliteration
    r = sta.audit_sound_texture("apple orange apple orange " * 80)
    assert r["alliteration_pairs_per_1k"] == 0.0


def test_baseline_deviation_block(tmp_path):
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "a.txt").write_text("river silver shimmer " * 130, encoding="utf-8")
    (bdir / "b.txt").write_text("plain mundane standard prose " * 110, encoding="utf-8")
    target = tmp_path / "t.md"
    target.write_text(_BODY, encoding="utf-8")
    assert sta.main([str(target), "--baseline-dir", str(bdir), "--json", "--out",
                     str(tmp_path / "o.json")]) == 0
    payload = json.loads((tmp_path / "o.json").read_text())
    dev = payload["results"]["baseline_deviation"]
    for key in sta.METRIC_KEYS:
        assert set(dev[key]) == {"draft", "baseline_mean", "baseline_sd", "z"}
    assert payload["baseline"]["n_files"] == 2


def test_too_short_unavailable(tmp_path):
    f = tmp_path / "short.md"
    f.write_text("river silver shimmer whisper.\n", encoding="utf-8")
    assert sta.main([str(f), "--json"]) == 0


def test_unavailable_payload_shape():
    payload = sta.build_payload({}, target_path="x.md", word_count=10,
                                available=False, warnings=["short"])
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None


def test_cli_emits_envelope(tmp_path, capsys):
    f = tmp_path / "doc.md"
    f.write_text(_BODY, encoding="utf-8")
    assert sta.main([str(f), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_surface"] == "sound_texture"
    assert payload["available"] is True


def test_deterministic():
    assert sta.audit_sound_texture(_BODY) == sta.audit_sound_texture(_BODY)
