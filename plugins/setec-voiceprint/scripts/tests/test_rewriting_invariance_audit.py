#!/usr/bin/env python3
"""Tests for rewriting_invariance_audit.py — Raidar-style rewrite-invariance.

Every LLM contact is stubbed: the tests inject a deterministic ``rewrite_fn``
so NO real model is loaded and NO network/LLM call is made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rewriting_invariance_audit as ria  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402

# A target comfortably over the 50-word floor.
ORIGINAL = (
    "The committee deliberated for several hours before reaching a decision "
    "that nobody in the room found entirely satisfying. Each member arrived "
    "with strong convictions, and the discussion wandered through budgets, "
    "timelines, and a dozen competing priorities before anyone proposed a "
    "compromise that could plausibly survive a vote. By the end, the mood was "
    "less triumphant than merely exhausted, and the chair adjourned without "
    "much ceremony or any real sense of resolution among the participants."
)

STUB_MODEL = "stub-judge-1"


def _identity_rewriter(text, *, trial=0):
    """A judge that 'rewrites' to an identical string ⇒ distance 0 (AI-like)."""
    return text


def _heavy_rewriter(text, *, trial=0):
    """A judge that rewrites heavily ⇒ large distance (human-like)."""
    return f"Completely different prose for trial {trial}: " + ("zzz " * 40)


def _per_trial_rewriter(text, *, trial=0):
    """Distinct output per trial so aggregation is observable."""
    if trial == 0:
        return text  # distance 0
    return text + (" extra appended words " * (trial * 5))


# ---------------------------------------------------------------- surface

def test_surface_registered():
    assert ria.TASK_SURFACE == "rewriting_invariance"
    assert ria.TASK_SURFACE in VALID_TASK_SURFACES
    assert ria.TASK_SURFACE in TASK_SURFACE_LABELS


# ---------------------------------------------------------------- stub judge

def test_distance_with_stub_judge():
    # Identity rewriter ⇒ zero distance (maximally AI-like).
    same = ria.audit_rewriting_invariance(
        ORIGINAL, _identity_rewriter, n=3, judge_model=STUB_MODEL,
    )
    assert same["mean_rewrite_distance"] == 0.0
    assert same["mean_token_overlap_distance"] == 0.0

    # Heavy rewriter ⇒ large distance (maximally human-like).
    diff = ria.audit_rewriting_invariance(
        ORIGINAL, _heavy_rewriter, n=3, judge_model=STUB_MODEL,
    )
    assert diff["mean_rewrite_distance"] > same["mean_rewrite_distance"]
    assert 0.0 <= diff["mean_rewrite_distance"] <= 1.0
    assert 0.0 <= diff["mean_token_overlap_distance"] <= 1.0

    # Deterministic: same stub ⇒ same numbers (provenance timestamp aside).
    again = ria.audit_rewriting_invariance(
        ORIGINAL, _identity_rewriter, n=3, judge_model=STUB_MODEL,
    )
    assert again["mean_rewrite_distance"] == same["mean_rewrite_distance"]
    assert again["per_trial_distances"][0]["edit_distance"] == 0.0


# ----------------------------------------------- no default threshold / band

def test_no_default_threshold_no_band():
    res = ria.audit_rewriting_invariance(
        ORIGINAL, _identity_rewriter, n=3, judge_model=STUB_MODEL,
    )
    for forbidden in ("band", "verdict", "threshold", "label",
                      "is_ai", "ai_human", "classification"):
        assert forbidden not in res
    payload = ria.build_payload(
        res, target_path="x.txt", word_count=ria.count_words(ORIGINAL),
        judge_model=STUB_MODEL, available=True,
    )
    # No band/verdict surfaces anywhere in the envelope top level either.
    assert "band" not in payload
    assert "verdict" not in payload
    lic = payload["claim_license"]
    assert lic["fpr_target"] is None
    assert lic["confidence_interval_95"] is None


def test_claim_license_names_judge_dependence():
    lic = ria._claim_license(judge_model="claude-sonnet-4-6")
    licenses = lic.licenses.lower()
    does_not = lic.does_not_license.lower()
    caveats = " ".join(lic.additional_caveats).lower()

    # Licenses names the judge model + prompt dependence.
    assert "claude-sonnet-4-6" in lic.licenses
    assert "prompt" in licenses
    assert "rewriting distance" in licenses or "rewrite" in licenses

    # Refuses an AI/human verdict absent thresholds.
    assert "verdict" in does_not
    assert "threshold" in does_not

    # Caveats note strong dependence on model+prompt and per-call cost.
    assert "judge model" in caveats and "prompt" in caveats
    assert "cost" in caveats or "billed" in caveats


# ------------------------------------------------------------ envelope shape

def test_envelope_shape():
    res = ria.audit_rewriting_invariance(
        ORIGINAL, _per_trial_rewriter, n=3, judge_model=STUB_MODEL,
    )
    payload = ria.build_payload(
        res, target_path="x.txt", word_count=ria.count_words(ORIGINAL),
        judge_model=STUB_MODEL, available=True,
    )
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "rewriting_invariance"
    assert payload["tool"] == "rewriting_invariance_audit"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    assert payload["baseline"] is None

    r = payload["results"]
    for key in ("mean_rewrite_distance", "per_trial_distances",
                "n_trials", "provenance"):
        assert key in r
    prov = r["provenance"]
    assert prov["judge_model"] == STUB_MODEL
    assert prov["rewrite_prompt"] == ria.REWRITE_PROMPT
    assert prov["prompt_fingerprint_sha256"] == ria.prompt_fingerprint()
    # JSON-serializable.
    json.loads(json.dumps(payload, default=str))


def test_envelope_unavailable_shape():
    payload = ria.build_payload(
        {}, target_path="x.txt", word_count=5,
        judge_model=STUB_MODEL, available=False,
        warnings=["too short"],
    )
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None
    assert payload["warnings"]


# ---------------------------------------------------------- n-trial aggregation

def test_n_trials_aggregated():
    for n in (1, 2, 5):
        res = ria.audit_rewriting_invariance(
            ORIGINAL, _per_trial_rewriter, n=n, judge_model=STUB_MODEL,
        )
        assert res["n_trials"] == n
        assert len(res["per_trial_distances"]) == n
        trials = [t["trial"] for t in res["per_trial_distances"]]
        assert trials == list(range(n))

        # Mean equals the arithmetic mean of the per-trial edit distances.
        per = [t["edit_distance"] for t in res["per_trial_distances"]]
        assert res["mean_rewrite_distance"] == pytest.approx(
            sum(per) / len(per), abs=1e-6
        )

        # SD reported only for n >= 2.
        if n >= 2:
            assert res["edit_distance_sd"] is not None
        else:
            assert res["edit_distance_sd"] is None

    # More heavily-rewritten later trials raise the mean above trial 0 alone.
    res5 = ria.audit_rewriting_invariance(
        ORIGINAL, _per_trial_rewriter, n=5, judge_model=STUB_MODEL,
    )
    assert res5["per_trial_distances"][0]["edit_distance"] == 0.0
    assert res5["mean_rewrite_distance"] > 0.0


def test_n_zero_rejected():
    with pytest.raises(ValueError):
        ria.audit_rewriting_invariance(
            ORIGINAL, _identity_rewriter, n=0, judge_model=STUB_MODEL,
        )


# ------------------------------------------------- end-to-end CLI with a stub

def test_cli_with_injected_stub(tmp_path, capsys):
    f = tmp_path / "target.txt"
    f.write_text(ORIGINAL, encoding="utf-8")
    rc = ria.main(
        [str(f), "--judge", STUB_MODEL, "--n", "2", "--json"],
        rewrite_fn=_identity_rewriter,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_surface"] == "rewriting_invariance"
    assert payload["available"] is True
    assert payload["results"]["n_trials"] == 2
    assert payload["results"]["mean_rewrite_distance"] == 0.0


def test_cli_too_short_unavailable(tmp_path, capsys):
    f = tmp_path / "short.txt"
    f.write_text("just a few words here, nothing more.", encoding="utf-8")
    rc = ria.main(
        [str(f), "--judge", STUB_MODEL, "--json"],
        rewrite_fn=_identity_rewriter,
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert payload["warnings"]
