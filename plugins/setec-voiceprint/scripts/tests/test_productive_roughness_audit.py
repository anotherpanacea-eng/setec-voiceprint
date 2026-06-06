#!/usr/bin/env python3
"""Tests for productive_roughness_audit.py — the strictly baseline-relative
productive-roughness deviation profile (spec 10).

The audit is baseline-relative by hard constraint: it must refuse to run on a
single document, must report every feature as a deviation (draft +
baseline_mean + z) rather than an absolute band, and must never license an
absolute roughness/quality or voice/authorship/AI verdict.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import productive_roughness_audit as pra  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# A small writer baseline: smooth, conventional prose with finite verbs in
# every sentence, no fragments, no sentence-initial conjunctions.
_BASELINE_A = (
    "I walked to the store this morning. The day was bright and clear. "
    "She laughed at the joke he told. We went home together after lunch. "
    "The cat slept on the warm sill all afternoon."
)
_BASELINE_B = (
    "He opened the door slowly. The hallway stretched ahead of him. "
    "They waited in silence for a long time. The clock ticked on the wall. "
    "Nothing in the quiet house moved at all."
)
# A rough draft: fragments, sentence-initial conjunctions, contractions,
# adjacent repetition, an aside.
_DRAFT_ROUGH = (
    "And then nothing. The long road home. But he ran. "
    "She didn't stop, didn't even look back, not once. "
    "The the door stood open. A quiet street at dawn, empty and cold."
)


def _write_baseline(tmp_path) -> Path:
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "a.txt").write_text(_BASELINE_A, encoding="utf-8")
    (bdir / "b.txt").write_text(_BASELINE_B, encoding="utf-8")
    return bdir


def _write_draft(tmp_path, text: str = _DRAFT_ROUGH) -> Path:
    p = tmp_path / "draft.txt"
    p.write_text(text, encoding="utf-8")
    return p


# ---------- 1. surface registered ----------


def test_surface_registered():
    assert pra.TASK_SURFACE == "productive_roughness"
    # Registered in BOTH the envelope enum and the claim-license label map.
    assert pra.TASK_SURFACE in VALID_TASK_SURFACES
    assert pra.TASK_SURFACE in TASK_SURFACE_LABELS
    label = TASK_SURFACE_LABELS[pra.TASK_SURFACE].lower()
    assert "baseline-relative" in label


# ---------- 2. requires baseline ----------


def test_requires_baseline(tmp_path, capsys):
    """Running with NO --baseline-dir must produce a clean error, never a
    traceback. argparse enforces the required flag (clean SystemExit), and the
    programmatic guard returns a clean available=False payload if a baseline
    directory is missing."""
    draft = _write_draft(tmp_path)

    # (a) Omitting --baseline-dir is a clean argparse error (SystemExit), not a
    # traceback.
    with pytest.raises(SystemExit):
        pra.main([str(draft)])

    # (b) A non-existent baseline directory degrades cleanly to available=False
    # with a warning, not a traceback.
    rc = pra.main([str(draft), "--baseline-dir", str(tmp_path / "nope"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None
    assert payload["warnings"]


# ---------- 3. fragment detection ----------


def test_fragment_detection(tmp_path):
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    # Three fragments (no finite verb), two full sentences.
    text = (
        "The long road home. And into the dark. A quiet empty street. "
        "He ran fast. She opened the door."
    )
    feats = pra.extract_features(text)
    assert feats.n_sentences == 5
    # 3 of 5 sentences are fragments → rate 0.6.
    assert feats.rates["fragment_rate"] == pytest.approx(3 / 5)
    # The two finite-verb sentences are NOT fragments.
    assert pra._sentence_is_fragment(pra._NLP("He ran fast.")) is False
    assert pra._sentence_is_fragment(pra._NLP("The long road home.")) is True


# ---------- 4. features are relative ----------


def test_features_are_relative(tmp_path):
    """Every feature result is a DEVIATION: it carries baseline_mean + z, and
    there is no absolute band / quality key anywhere in the results."""
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = _write_baseline(tmp_path)
    draft = _write_draft(tmp_path)
    out_json = tmp_path / "out.json"
    rc = pra.main(
        [str(draft), "--baseline-dir", str(bdir), "--json", "--out", str(out_json)]
    )
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))

    assert payload["available"] is True
    results = payload["results"]
    assert set(results.keys()) == set(pra.FEATURE_KEYS)
    for key, row in results.items():
        # Relative shape: draft + baseline_mean + baseline_sd + z.
        assert "draft" in row
        assert "baseline_mean" in row
        assert "baseline_sd" in row
        assert "z" in row
        # No absolute band / verdict / quality / score keys.
        for forbidden in ("band", "verdict", "quality", "score", "absolute",
                          "rating", "grade"):
            assert forbidden not in row
    # No top-level absolute verdict either.
    for forbidden in ("band", "verdict", "compression", "quality_score"):
        assert forbidden not in results


# ---------- 5. claim license refuses absolute and verdict ----------


def test_claim_license_refuses_absolute_and_verdict(tmp_path):
    bdir = _write_baseline(tmp_path)
    baseline = pra.aggregate_baseline(bdir)
    lic = pra._claim_license(baseline=baseline, draft_words=500)
    dn = lic.does_not_license.lower()
    # Refuses absolute roughness / quality judgment.
    assert "absolute" in dn
    assert "quality" in dn
    # Refuses voice / authorship / AI verdict.
    assert "voice" in dn
    assert "authorship" in dn
    assert "ai" in dn
    # Refuses use without a writer-specific baseline.
    assert "without a writer" in dn or "single document" in dn
    # Licenses the baseline-relative deviation.
    assert "baseline" in lic.licenses.lower()
    assert "deviate" in lic.licenses.lower()


# ---------- 6. envelope shape ----------


def test_envelope_shape(tmp_path):
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = _write_baseline(tmp_path)
    draft = _write_draft(tmp_path)
    baseline = pra.aggregate_baseline(bdir, target_path=draft)
    feats = pra.extract_features(_DRAFT_ROUGH)
    results = pra.build_results(feats, baseline)
    payload = pra.build_payload(
        target_path=draft,
        draft_words=feats.n_words,
        draft_sentences=feats.n_sentences,
        results=results,
        baseline=baseline,
        available=True,
    )
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "productive_roughness"
    assert payload["tool"] == "productive_roughness_audit"
    assert payload["available"] is True
    # The baseline envelope block is populated.
    assert payload["baseline"] is not None
    assert payload["baseline"]["n_files"] == 2
    assert payload["baseline"]["words"] > 0
    assert payload["baseline"]["sentences"] > 0
    # Claim license present and surface-matched.
    assert payload["claim_license"] is not None
    assert payload["claim_license"]["task_surface"] == "productive_roughness"
    # All six features present.
    assert set(payload["results"].keys()) == set(pra.FEATURE_KEYS)


# ---------- 7. deterministic ----------


def test_deterministic(tmp_path):
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = _write_baseline(tmp_path)
    b1 = pra.aggregate_baseline(bdir)
    b2 = pra.aggregate_baseline(bdir)
    assert b1.per_feature == b2.per_feature
    f1 = pra.extract_features(_DRAFT_ROUGH)
    f2 = pra.extract_features(_DRAFT_ROUGH)
    assert f1.rates == f2.rates
    r1 = pra.build_results(f1, b1)
    r2 = pra.build_results(f2, b2)
    assert r1 == r2


# ---------- supporting coverage ----------


def test_z_is_null_when_baseline_has_no_variation(tmp_path):
    """When the writer's baseline shows no variation in a feature (sd == 0),
    z must be null — the audit refuses to fabricate a deviation magnitude."""
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = _write_baseline(tmp_path)
    baseline = pra.aggregate_baseline(bdir)
    feats = pra.extract_features(_DRAFT_ROUGH)
    results = pra.build_results(feats, baseline)
    # fragment_rate is 0 in both smooth baseline docs → sd 0 → z null.
    assert results["fragment_rate"]["baseline_sd"] == 0.0
    assert results["fragment_rate"]["z"] is None


def test_z_is_computed_when_baseline_varies():
    """With a synthetic baseline that varies, z is a real number."""
    baseline = pra.BaselineStats(
        n_files=3,
        n_words=300,
        n_sentences=30,
        per_feature={k: {"mean": 0.1, "sd": 0.05} for k in pra.FEATURE_KEYS},
        files_loaded=[],
        files_skipped=[],
    )
    feats = pra.DocFeatures(
        n_sentences=10, n_words=100,
        rates={k: 0.2 for k in pra.FEATURE_KEYS},
    )
    results = pra.build_results(feats, baseline)
    for k in pra.FEATURE_KEYS:
        # z = (0.2 - 0.1) / 0.05 = 2.0
        assert results[k]["z"] == pytest.approx(2.0)


def test_unavailable_without_spacy(monkeypatch, tmp_path, capsys):
    """Force the no-spaCy path: clean available=False payload, no traceback."""
    monkeypatch.setattr(pra, "HAS_SPACY", False)
    bdir = _write_baseline(tmp_path)
    draft = _write_draft(tmp_path)
    rc = pra.main([str(draft), "--baseline-dir", str(bdir), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert payload["claim_license"] is None
    assert any("spaCy" in w for w in payload["warnings"])


def test_draft_cannot_be_its_own_baseline(tmp_path, capsys):
    """A baseline directory that contains only the draft itself yields no usable
    baseline files → clean available=False."""
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    draft = bdir / "draft.txt"
    draft.write_text(_DRAFT_ROUGH, encoding="utf-8")
    rc = pra.main([str(draft), "--baseline-dir", str(bdir), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert payload["warnings"]


def test_cli_markdown_smoke(tmp_path, capsys):
    if not pra.HAS_SPACY:
        pytest.skip("spaCy + en_core_web_sm not available")
    bdir = _write_baseline(tmp_path)
    draft = _write_draft(tmp_path)
    rc = pra.main([str(draft), "--baseline-dir", str(bdir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Productive-roughness deviation" in out
    assert "baseline-relative" in out.lower()
    # The report renders the per-feature deviation table, not an absolute band.
    assert "Per-feature deviation" in out
    assert "Baseline mean" in out
