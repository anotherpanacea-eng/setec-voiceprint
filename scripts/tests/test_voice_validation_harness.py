#!/usr/bin/env python3
"""Regression tests for voice_validation_harness.py.

Smoke tests against the public-domain Federalist fixture
(`scripts/test_data/federalist_voice_validation_manifest.jsonl` →
`scripts/test_data/federalist_oracle/*.txt`). The fixture is six
docs (3 Hamilton + 3 Madison) → 15 unordered pairs → 6 same-author
+ 9 different-author. AUC values on this tiny fixture are smoke
regression values, not calibration claims.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import voice_validation_harness as vvh  # type: ignore


MANIFEST = (
    ROOT / "test_data" / "federalist_voice_validation_manifest.jsonl"
)


def _args(**overrides: object) -> argparse.Namespace:
    base = {
        "manifest": str(MANIFEST),
        "use": "voice_validation",
        "json_path": None,
        "md_path": None,
        "label_by": "author",
        "bootstrap_method": "naive_pair",
        "bootstrap_resamples": 200,
        "bootstrap_confidence": 0.95,
        "bootstrap_seed": 42,
        "fpr_target": None,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return argparse.Namespace(**base)


def test_smoke_run_succeeds_on_federalist_fixture() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = vvh.run_harness(_args())
    assert not result.get("failed"), result.get("reason")
    assert result["task_surface"] == "voice_coherence"
    assert result["tool"] == "voice_validation_harness"
    assert result["n_pairs"] == 15
    assert result["n_same_author"] == 6
    assert result["n_different_author"] == 9


def test_pair_labels_respect_manifest_authorship() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = vvh.run_harness(_args())
    same_author_pairs = [p for p in result["pairs"] if p["same_author"]]
    diff_author_pairs = [p for p in result["pairs"] if not p["same_author"]]
    assert len(same_author_pairs) == 6
    assert len(diff_author_pairs) == 9
    for p in same_author_pairs:
        assert p["doc_a_author"] == p["doc_b_author"], (
            f"Same-author pair has different authors: "
            f"{p['doc_a']} ({p['doc_a_author']}) vs "
            f"{p['doc_b']} ({p['doc_b_author']})"
        )
    for p in diff_author_pairs:
        assert p["doc_a_author"] != p["doc_b_author"], (
            f"Different-author pair has same author: "
            f"{p['doc_a']} ({p['doc_a_author']}) vs "
            f"{p['doc_b']} ({p['doc_b_author']})"
        )


def test_function_word_smoke_aucs_within_tolerance() -> None:
    """The spec records approximate smoke values for function-word
    Burrows-Delta and cosine AUC on this fixture: ~0.611 and ~0.796.
    Treat these as regression sanity values: a tolerance of 0.10
    captures small variations from feature-selection edge cases without
    accepting wholesale drift. If the implementation diverges
    materially from these, investigate selection, z-score population,
    pair labeling, and denominator handling."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = vvh.run_harness(_args())
    fw_rows = [
        r for r in result["slices"]["overall"]["per_family_ranking"]
        if r["family"] == "function_words"
    ]
    by_metric = {r["metric"]: r["auc"] for r in fw_rows}
    assert "burrows_delta" in by_metric, "function-word Burrows-Delta missing"
    assert "cosine_distance" in by_metric, "function-word cosine missing"
    delta_auc = by_metric["burrows_delta"]
    cos_auc = by_metric["cosine_distance"]
    assert delta_auc is not None
    assert cos_auc is not None
    # Polarity check: AUC > 0.5 expected for both. If either is < 0.5,
    # something is inverted (label polarity, score sign, etc.).
    assert delta_auc >= 0.5, (
        f"function-word Burrows-Delta AUC {delta_auc} inverted; "
        f"check pair labeling and z-score population"
    )
    assert cos_auc >= 0.5, (
        f"function-word cosine AUC {cos_auc} inverted; "
        f"check pair labeling"
    )
    # Tolerance band around the spec's documented smoke values.
    assert abs(delta_auc - 0.611) < 0.10, (
        f"function-word Burrows-Delta AUC {delta_auc} drifted from "
        f"documented 0.611 smoke value by more than 0.10"
    )
    assert abs(cos_auc - 0.796) < 0.10, (
        f"function-word cosine AUC {cos_auc} drifted from documented "
        f"0.796 smoke value by more than 0.10"
    )


def test_refuses_aggregate_accuracy_without_fpr_target() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = vvh.run_harness(_args(fpr_target=None))
    assert result.get("operating_point") is None
    license_block = result["claim_license"]
    assert "No FPR target" in license_block["operating_point"]
    # The license text must explicitly refuse single-aggregate-accuracy
    # claims regardless of operating-point status.
    assert (
        "single aggregate accuracy" in license_block["does_not_license"]
    )


def test_operating_point_appears_when_fpr_target_supplied() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = vvh.run_harness(_args(fpr_target=0.10))
    op = result.get("operating_point")
    assert op is not None
    assert op["fpr_target"] == 0.10
    # On 9 different-author / 6 same-author, FPR target 0.10 is
    # achievable: 0/6 = 0.0 satisfies; the harness should pick the
    # highest-TPR threshold within the FPR ceiling.
    if op.get("available"):
        assert op["fpr"] <= 0.10
        assert "threshold" in op


def test_bootstrap_ci_is_reproducible_across_processes() -> None:
    """The bootstrap CIs must be byte-identical across two separate
    Python processes when --bootstrap-seed is the same. Python's
    built-in hash() of strings/tuples is salted per process via
    PYTHONHASHSEED, so any seed-derivation that uses hash() will
    produce different RNG sequences on every run. _stable_seed uses
    SHA-256 instead. This regression test guards against hash()
    sneaking back in.

    We invoke run_harness twice within the same process AND verify
    that derived seeds are stable across processes by checking the
    helper directly with a known input/output pair.
    """
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return

    # Same-process reproducibility: two run_harness calls with the
    # same seed must produce identical CIs.
    r1 = vvh.run_harness(_args(bootstrap_seed=42, bootstrap_resamples=200))
    r2 = vvh.run_harness(_args(bootstrap_seed=42, bootstrap_resamples=200))
    rows_1 = r1["slices"]["overall"]["per_family_ranking"]
    rows_2 = r2["slices"]["overall"]["per_family_ranking"]
    assert len(rows_1) == len(rows_2)
    for a, b in zip(rows_1, rows_2):
        assert a["family"] == b["family"] and a["metric"] == b["metric"]
        ci_a = a.get("auc_ci") or {}
        ci_b = b.get("auc_ci") or {}
        assert ci_a.get("lower") == ci_b.get("lower"), (
            f"{a['family']}/{a['metric']}: lower bound drift "
            f"{ci_a.get('lower')} vs {ci_b.get('lower')}"
        )
        assert ci_a.get("upper") == ci_b.get("upper"), (
            f"{a['family']}/{a['metric']}: upper bound drift "
            f"{ci_a.get('upper')} vs {ci_b.get('upper')}"
        )

    # Cross-process reproducibility: _stable_seed must produce a
    # known fixed value for a known input. SHA-256 is process-stable;
    # built-in hash() is not. If this assertion ever drifts, the seed
    # derivation has switched away from a process-stable hash and
    # bootstrap CIs will silently vary across runs.
    expected = vvh._stable_seed(42, "function_words", "burrows_delta",
                                "document_cluster")
    assert expected == 5400004389535003544, (
        f"_stable_seed regressed: got {expected}, expected stable "
        f"SHA-256 derivation. Has the seed-derivation function "
        f"switched back to Python's built-in hash()?"
    )


def test_stable_seed_returns_none_when_base_seed_is_none() -> None:
    """A None base seed must propagate to None so random.Random()
    falls back to non-deterministic system-entropy seeding."""
    assert vvh._stable_seed(None, "function_words", "burrows_delta") is None


def test_manifest_validator_accepts_voice_validation_use() -> None:
    """Round-trip the manifest through validate_manifest; should be
    error-free. Guards against ALLOWED_USE regressing."""
    from manifest_validator import validate_manifest  # type: ignore
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist voice manifest not available")
        return
    result = validate_manifest(str(MANIFEST))
    issues = result.get("issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    assert not errors, f"manifest has errors: {errors}"
