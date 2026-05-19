"""Tests for ``binoculars_calibrate.py``.

Pure helpers tested directly against synthetic distributions; the
calibration pipeline tested end-to-end with a stubbed audit_fn so
no real LLM loads or backends are needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
sys.path.insert(0, str(_SCRIPTS))

import binoculars_calibrate as cal  # noqa: E402


# ============================================================
# Pure helpers: _distributions, _compute_auc, _derive_thresholds
# ============================================================


def test_distributions_empty():
    d = cal._distributions([])
    assert d["n"] == 0
    assert d["mean"] is None


def test_distributions_singleton():
    d = cal._distributions([3.0])
    assert d["n"] == 1
    assert d["mean"] == 3.0
    assert d["median"] == 3.0
    assert d["std"] == 0.0


def test_distributions_mean_and_median():
    d = cal._distributions([1.0, 2.0, 3.0, 4.0, 5.0])
    assert d["n"] == 5
    assert abs(d["mean"] - 3.0) < 1e-9
    assert abs(d["median"] - 3.0) < 1e-9


def test_distributions_percentiles():
    d = cal._distributions([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert abs(d["p05"] - 1.45) < 1e-6
    assert abs(d["p25"] - 3.25) < 1e-6
    assert abs(d["p75"] - 7.75) < 1e-6
    assert abs(d["p95"] - 9.55) < 1e-6


def test_compute_auc_identical_distributions_is_half():
    s = [(1.0, 0), (1.0, 0), (1.0, 1), (1.0, 1)]
    auc = cal._compute_auc(s)
    assert auc == 0.5


def test_compute_auc_cleanly_separable_is_one():
    """Positives all have score 0.5, negatives all have score 1.5.
    Per the lower-is-AI convention, AUC = 1.0."""
    s = [(0.5, 1), (0.5, 1), (1.5, 0), (1.5, 0)]
    auc = cal._compute_auc(s)
    assert auc == 1.0


def test_compute_auc_inverted_is_zero():
    """Positives at HIGHER scores → AUC = 0 (the inverted-polarity case)."""
    s = [(1.5, 1), (1.5, 1), (0.5, 0), (0.5, 0)]
    auc = cal._compute_auc(s)
    assert auc == 0.0


def test_compute_auc_empty_returns_none():
    assert cal._compute_auc([]) is None
    assert cal._compute_auc([(1.0, 0)]) is None
    assert cal._compute_auc([(1.0, 1)]) is None


def test_derive_thresholds_clean_separation():
    """Positives at 0.3-0.5, negatives at 1.0-1.5. Expect threshold-low
    pulled below the negative-class p01 (no negatives below it) and
    threshold-high near the positive median."""
    pos = [(0.3, 1), (0.4, 1), (0.5, 1)] * 20  # 60 positives
    neg = [(1.0, 0), (1.2, 0), (1.5, 0)] * 20  # 60 negatives
    derived = cal._derive_thresholds(
        pos + neg, fpr_target=0.01, target_tpr=0.5,
    )
    assert derived["low"] is not None
    assert derived["high"] is not None
    # threshold-low should be at or below 1.0 (the lowest negative).
    assert derived["low"] <= 1.0
    # threshold-high should be in the positive range.
    assert 0.3 <= derived["high"] <= 0.5


def test_derive_thresholds_recovers_target_fpr():
    """With 100 distinct negatives uniformly in [0, 1], fpr_target=0.05
    should put threshold-low at roughly the 5th percentile = 0.05."""
    pos = [(0.5, 1)] * 30
    neg = [(i / 99.0, 0) for i in range(100)]
    derived = cal._derive_thresholds(pos + neg, fpr_target=0.05, target_tpr=0.5)
    # The 5th-percentile index of 100 sorted values is index 5 = 5/99.
    assert abs(derived["low"] - 5.0 / 99.0) < 0.05


def test_derive_thresholds_diagnostic_metrics_computed():
    pos = [(0.3, 1), (0.4, 1), (0.5, 1)] * 20
    neg = [(1.0, 0), (1.2, 0), (1.5, 0)] * 20
    derived = cal._derive_thresholds(pos + neg, fpr_target=0.01, target_tpr=0.5)
    assert derived["tpr_at_low"] is not None
    assert derived["fpr_at_high"] is not None
    assert derived["indeterminate_rate"] is not None


def test_derive_thresholds_no_positives_returns_none():
    derived = cal._derive_thresholds([(1.0, 0)] * 30, fpr_target=0.01, target_tpr=0.5)
    assert derived["low"] is None
    assert derived["high"] is None


# ============================================================
# Gates + caveats
# ============================================================


def test_gates_polarity_correct_when_pos_mean_below_neg_mean():
    g = cal._evaluate_gates(
        pos_count=40, neg_count=40, auc=0.85,
        pos_mean=0.5, neg_mean=1.5,
    )
    assert g["polarity_correct"] is True


def test_gates_polarity_incorrect_when_pos_mean_above_neg_mean():
    g = cal._evaluate_gates(
        pos_count=40, neg_count=40, auc=0.85,
        pos_mean=1.5, neg_mean=0.5,
    )
    assert g["polarity_correct"] is False


def test_gates_sample_size_floor():
    g_few_pos = cal._evaluate_gates(
        pos_count=5, neg_count=40, auc=0.9,
        pos_mean=0.5, neg_mean=1.5,
    )
    assert g_few_pos["sufficient_positives"] is False
    g_few_neg = cal._evaluate_gates(
        pos_count=40, neg_count=5, auc=0.9,
        pos_mean=0.5, neg_mean=1.5,
    )
    assert g_few_neg["sufficient_negatives"] is False


def test_gates_auc_floor():
    g_low = cal._evaluate_gates(
        pos_count=40, neg_count=40, auc=0.55,
        pos_mean=0.5, neg_mean=1.5,
    )
    assert g_low["auc_sufficient"] is False
    g_high = cal._evaluate_gates(
        pos_count=40, neg_count=40, auc=0.85,
        pos_mean=0.5, neg_mean=1.5,
    )
    assert g_high["auc_sufficient"] is True


def test_caveats_fire_on_each_gate_failure():
    gates = {
        "polarity_correct": False,
        "sufficient_positives": False,
        "sufficient_negatives": False,
        "auc_sufficient": False,
    }
    cavs = cal._build_caveats(
        gates=gates, pos_count=5, neg_count=5, auc=0.4,
        derived={"low": None, "high": None},
    )
    assert any("polarity_inverted" in c for c in cavs)
    assert any("insufficient_positives" in c for c in cavs)
    assert any("insufficient_negatives" in c for c in cavs)
    assert any("low_auc" in c for c in cavs)


def test_caveats_quiet_when_all_gates_pass():
    gates = {
        "polarity_correct": True,
        "sufficient_positives": True,
        "sufficient_negatives": True,
        "auc_sufficient": True,
    }
    cavs = cal._build_caveats(
        gates=gates, pos_count=50, neg_count=50, auc=0.85,
        derived={"low": 0.5, "high": 1.0},
    )
    assert cavs == []


def test_caveats_degenerate_thresholds_when_low_ge_high():
    gates = {
        "polarity_correct": True,
        "sufficient_positives": True,
        "sufficient_negatives": True,
        "auc_sufficient": True,
    }
    cavs = cal._build_caveats(
        gates=gates, pos_count=50, neg_count=50, auc=0.7,
        derived={"low": 1.0, "high": 0.5},  # inverted: low > high
    )
    assert any("degenerate_thresholds" in c for c in cavs)


# ============================================================
# Manifest loading
# ============================================================


def _make_manifest(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "manifest.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return p


def test_load_manifest_skips_empty_lines(tmp_path):
    p = tmp_path / "manifest.jsonl"
    p.write_text('{"a": 1}\n\n{"b": 2}\n\n')
    entries = cal._load_manifest(p)
    assert len(entries) == 2


def test_load_manifest_errors_on_malformed_json(tmp_path):
    p = tmp_path / "manifest.jsonl"
    p.write_text("not json\n")
    with pytest.raises(ValueError, match="line 1"):
        cal._load_manifest(p)


# ============================================================
# calibrate() end-to-end with stubbed audit_fn
# ============================================================


class StubBackend:
    def __init__(self, model_id: str):
        self.model_id = model_id

    def identifier_block(self):
        return {"id": self.model_id, "alias": self.model_id, "method": "stub"}


def _make_text_files(tmp_path: Path, count: int) -> list[Path]:
    """Make ``count`` tiny text files; return their paths."""
    paths = []
    for i in range(count):
        p = tmp_path / f"text_{i}.txt"
        p.write_text(f"text content {i}\n")
        paths.append(p)
    return paths


def test_calibrate_assembles_scores_per_label(tmp_path):
    """Stub audit_fn returns fixed scores per (ai_status); calibrate
    aggregates them and exposes per-label distributions."""
    files = _make_text_files(tmp_path, 80)
    entries = []
    for i, f in enumerate(files):
        entries.append({
            "path": str(f),
            "ai_status": "ai_generated" if i < 40 else "pre_ai_human",
        })
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        # AI-likely → low score (0.5), human-likely → high score (1.5).
        score = 0.5 if "0\n" not in text else 0.5
        # Actually deterministic by file index: use len to vary.
        # Encoded via the path number — extract from text.
        idx = int(text.split()[2])  # "text content N"
        return {
            "perplexity_ratio": 0.5 if idx < 40 else 1.5,
            "score_version": "perplexity_ratio_v1",
        }

    scorer = StubBackend("scorer")
    observer = StubBackend("observer")
    results = cal.calibrate(
        manifest_path=manifest,
        scorer=scorer, observer=observer,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["n_entries_scored"] == 80
    assert results["n_positives"] == 40
    assert results["n_negatives"] == 40
    assert results["distributions"]["positive"]["mean"] == 0.5
    assert results["distributions"]["negative"]["mean"] == 1.5
    assert results["auc"] == 1.0  # cleanly separable
    assert results["gates"]["polarity_correct"] is True
    assert results["gates"]["auc_sufficient"] is True


def test_calibrate_records_score_version_from_audit(tmp_path):
    files = _make_text_files(tmp_path, 30)
    entries = [
        {"path": str(f), "ai_status": "ai_generated"}
        for f in files
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        return {
            "perplexity_ratio": 0.5,
            "score_version": "binoculars_cross_perplexity_v2",
        }

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["score_version"] == "binoculars_cross_perplexity_v2"


def test_calibrate_skips_unlabelled_entries(tmp_path):
    files = _make_text_files(tmp_path, 5)
    entries = [
        {"path": str(files[0]), "ai_status": "ai_generated"},
        {"path": str(files[1]), "ai_status": "pre_ai_human"},
        {"path": str(files[2]), "ai_status": "mixed_authorship"},  # unlabelled
        {"path": str(files[3]), "ai_status": None},  # unlabelled
        {"path": str(files[4]), "ai_status": "unknown"},  # unlabelled
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        return {"perplexity_ratio": 1.0, "score_version": "v1"}

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["n_entries_scored"] == 2  # only labelled ones
    assert any("skipped_unlabelled_entries:3" in c for c in results["caveats"])


def test_calibrate_skips_missing_path(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "nonexistent.txt"), "ai_status": "ai_generated"},
    ])

    def audit_fn(text, scorer, observer, score_version):
        return {"perplexity_ratio": 1.0, "score_version": "v1"}

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["n_entries_scored"] == 0
    assert any("skipped_missing_path" in c for c in results["caveats"])


def test_calibrate_skips_score_failures(tmp_path):
    files = _make_text_files(tmp_path, 5)
    entries = [
        {"path": str(f), "ai_status": "ai_generated"}
        for f in files
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        # All scores are None → all skipped.
        return {"perplexity_ratio": None, "score_version": "v1"}

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["n_entries_scored"] == 0
    assert any("skipped_score_failed:5" in c for c in results["caveats"])


def test_calibrate_max_entries_subsamples(tmp_path):
    files = _make_text_files(tmp_path, 100)
    entries = [
        {"path": str(f), "ai_status": "ai_generated" if i < 50 else "pre_ai_human"}
        for i, f in enumerate(files)
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        return {"perplexity_ratio": 1.0, "score_version": "v1"}

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        max_entries=20,
        audit_fn=audit_fn,
    )
    assert results["n_entries_scored"] == 20


# ============================================================
# Envelope + markdown
# ============================================================


def _make_full_results(tmp_path, n_pos=40, n_neg=40):
    """Build a clean fixture that:
    - passes all four discipline gates (polarity, sample-size, AUC),
    - produces non-degenerate thresholds (low < high),
    so the "all gates pass" rendering path is exercised.

    Distributions overlap (necessary for threshold-low < threshold-high
    given my percentile-of-class formula) but polarity is correct (pos
    mean < neg mean) and AUC stays well above 0.6.
    Positives: linspace(0.3, 1.0); negatives: linspace(0.5, 1.5)."""
    files = _make_text_files(tmp_path, n_pos + n_neg)
    entries = []
    for i, f in enumerate(files):
        entries.append({
            "path": str(f),
            "ai_status": "ai_generated" if i < n_pos else "pre_ai_human",
        })
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        idx = int(text.split()[2])
        if idx < n_pos:
            # Positives spread 0.3 to 1.0.
            score = 0.3 + 0.7 * (idx / max(1, n_pos - 1))
        else:
            j = idx - n_pos
            # Negatives spread 0.5 to 1.5.
            score = 0.5 + 1.0 * (j / max(1, n_neg - 1))
        return {
            "perplexity_ratio": score,
            "score_version": "binoculars_cross_perplexity_v2",
        }

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("scorer-model"),
        observer=StubBackend("observer-model"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    return manifest, results


def test_envelope_has_required_fields(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "calibration"
    assert envelope["tool"] == "binoculars_calibrate"
    assert envelope["available"] is True
    assert envelope["claim_license"]["task_surface"] == "calibration"


def test_envelope_records_model_pair_and_score_version_in_comparison_set(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    cs = envelope["claim_license"]["comparison_set"]
    assert cs["scorer_model"] == "scorer-model"
    assert cs["observer_model"] == "observer-model"
    assert cs["score_version"] == "binoculars_cross_perplexity_v2"


def test_envelope_includes_hans_reference(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    refs = envelope["claim_license"]["references"]
    assert any("Hans et al. 2024" in r for r in refs)


def test_markdown_has_expected_sections(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)
    assert "# Binoculars Threshold Calibration" in md
    assert "## Score distributions" in md
    assert "## Derived thresholds" in md
    assert "## Discipline gates" in md
    assert "## Caveats" in md
    assert "## Claim license" in md


def test_markdown_includes_threshold_replay_snippet(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)
    # When thresholds are derived, the markdown shows a
    # copy-pasteable invocation of binoculars_audit.py.
    assert "binoculars_audit.py" in md
    assert "--threshold-low" in md
    assert "--threshold-high" in md


def test_markdown_renders_gate_pass_fail_markers(tmp_path):
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)
    # Gates pass → check mark; we have a clean fixture so all pass.
    assert "✓" in md


# ============================================================
# Polarity-inversion gate
# ============================================================


def test_polarity_inverted_fixture_flags_caveat(tmp_path):
    """A fixture where AI scores HIGHER than humans (the inverted-
    polarity case) must flag `polarity_inverted` in caveats."""
    files = _make_text_files(tmp_path, 80)
    entries = []
    for i, f in enumerate(files):
        entries.append({
            "path": str(f),
            "ai_status": "ai_generated" if i < 40 else "pre_ai_human",
        })
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        idx = int(text.split()[2])
        # INVERTED: positives at HIGHER scores.
        return {
            "perplexity_ratio": 1.5 if idx < 40 else 0.5,
            "score_version": "v1",
        }

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("s"), observer=StubBackend("o"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    assert results["gates"]["polarity_correct"] is False
    assert any("polarity_inverted" in c for c in results["caveats"])


# ============================================================
# Input validation (PR #115 P2 review fix)
# ============================================================


def test_calibrate_rejects_fpr_target_above_one(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="fpr_target must be in"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses={"pre_ai_human"},
            fpr_target=2.0,
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_rejects_fpr_target_below_zero(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="fpr_target must be in"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses={"pre_ai_human"},
            fpr_target=-0.1,
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_rejects_target_tpr_above_one(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="target_tpr must be in"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses={"pre_ai_human"},
            target_tpr=1.5,
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_rejects_target_tpr_below_zero(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="target_tpr must be in"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses={"pre_ai_human"},
            target_tpr=-1.0,
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_rejects_empty_positive_statuses(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="positive_statuses must be non-empty"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses=set(),
            negative_statuses={"pre_ai_human"},
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_rejects_empty_negative_statuses(tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")
    with pytest.raises(ValueError, match="negative_statuses must be non-empty"):
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses=set(),
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


def test_calibrate_accepts_boundary_zero_and_one():
    """fpr_target=0 and target_tpr=1 are degenerate but valid rates.
    The script accepts them (operator's call); only out-of-range
    values are rejected."""
    # No manifest read needed; expect ValueError if rejected, no
    # error otherwise. Use a tmp manifest path that won't be read
    # since the boundary check happens first.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        manifest = tdp / "manifest.jsonl"
        manifest.write_text("")  # empty manifest is OK; no entries
        # No raise on boundary values.
        cal.calibrate(
            manifest_path=manifest,
            scorer=StubBackend("s"), observer=StubBackend("o"),
            positive_statuses={"ai_generated"},
            negative_statuses={"pre_ai_human"},
            fpr_target=0.0,
            target_tpr=1.0,
            audit_fn=lambda t, s, o, sv: {"perplexity_ratio": 1.0, "score_version": "v1"},
        )


# ============================================================
# Provisional-threshold rendering (PR #115 P1 review fix)
# ============================================================


def test_markdown_emits_inspection_only_block_when_gates_fail(tmp_path):
    """When any discipline gate fails, the markdown must NOT render
    the copy-pasteable `binoculars_audit.py --threshold-low X
    --threshold-high Y` recommendation. It should emit an
    inspection-only block with explicit refusal language naming
    which gates failed.

    Fixture: small samples (8 each) → sufficient_positives /
    sufficient_negatives gates fail."""
    files = _make_text_files(tmp_path, 16)
    entries = [
        {"path": str(f), "ai_status": "ai_generated" if i < 8 else "pre_ai_human"}
        for i, f in enumerate(files)
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        idx = int(text.split()[2])
        return {
            "perplexity_ratio": 0.5 if idx < 8 else 1.5,
            "score_version": "v1",
        }

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("scorer"), observer=StubBackend("observer"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)

    # The recommendation form must NOT appear.
    assert "To use these thresholds in subsequent audits" not in md
    # The inspection-only form MUST appear with refusal language.
    assert "INSPECTION ONLY" in md
    assert "DO NOT pass these" in md
    # The failing gate is named.
    assert "sufficient_positives" in md or "sufficient_negatives" in md


def test_markdown_emits_inspection_only_block_on_polarity_inversion(tmp_path):
    """Polarity-inverted fixture (gates report polarity_correct=False)
    must also surface as inspection-only."""
    files = _make_text_files(tmp_path, 80)
    entries = [
        {"path": str(f), "ai_status": "ai_generated" if i < 40 else "pre_ai_human"}
        for i, f in enumerate(files)
    ]
    manifest = _make_manifest(tmp_path, entries)

    def audit_fn(text, scorer, observer, score_version):
        idx = int(text.split()[2])
        # INVERTED: positives at HIGHER scores.
        return {
            "perplexity_ratio": 1.5 if idx < 40 else 0.5,
            "score_version": "v1",
        }

    results = cal.calibrate(
        manifest_path=manifest,
        scorer=StubBackend("scorer"), observer=StubBackend("observer"),
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
        audit_fn=audit_fn,
    )
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)
    assert "To use these thresholds in subsequent audits" not in md
    assert "INSPECTION ONLY" in md
    assert "polarity_correct" in md


def test_markdown_emits_recommendation_block_when_all_gates_pass(tmp_path):
    """Sanity: with the clean fixture (≥30 per class, separable
    distributions, polarity correct, high AUC), the recommendation
    form is rendered."""
    manifest, results = _make_full_results(tmp_path)
    envelope = cal.compose_envelope(manifest_path=manifest, results=results)
    md = cal.render_markdown(envelope)
    assert "To use these thresholds in subsequent audits" in md
    assert "INSPECTION ONLY" not in md


# ============================================================
# CLI smoke
# ============================================================


def test_cli_returns_one_on_missing_manifest(tmp_path):
    rc = cal.main([str(tmp_path / "missing.jsonl")])
    assert rc == 1


def test_cli_returns_three_on_backend_construction_failure(monkeypatch, tmp_path):
    manifest = _make_manifest(tmp_path, [
        {"path": str(tmp_path / "x.txt"), "ai_status": "ai_generated"},
    ])
    (tmp_path / "x.txt").write_text("x")

    def failing_init(self, *args, **kwargs):
        raise cal.bin_audit.SurprisalBackendError("simulated load fail")

    monkeypatch.setattr(cal.bin_audit.SurprisalBackend, "__init__", failing_init)
    rc = cal.main([str(manifest)])
    assert rc == 3
