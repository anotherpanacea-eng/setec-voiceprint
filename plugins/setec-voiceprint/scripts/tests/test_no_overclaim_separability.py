#!/usr/bin/env python3
"""Structural over-claim-separability guardrail (spec 28, PR E).

Root: Sadasivan et al. 2023, 'Can AI-Generated Text be Reliably Detected?'
(arXiv:2303.11156). The posture doc states what the validation spine and the
adversarial robustness card will NOT claim; this test makes the absence
CHECKABLE: the rendered output of validation_harness, conformal_gate,
intrinsic_dimension_audit, and adversarial_robustness_card contains no
forbidden VERDICT PHRASE (a small closed denylist), outside a refusal/caveat
context.

Per the review finding, the matcher is **phrase-level on a closed verdict
list** — NOT bare words like 'reliable' / 'separable', which appear in
legitimate caveats (e.g. 'a reliable log-log scaling fit') and are not
verdicts.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conformal_gate as cg  # type: ignore  # noqa: E402
import intrinsic_dimension_audit as ida  # type: ignore  # noqa: E402
import adversarial_robustness_card as arc  # type: ignore  # noqa: E402
import validation_harness as vh  # type: ignore  # noqa: E402


# Closed list of VERDICT-PHRASE constructions (whole phrases, not bare words).
# A surface that renders any of these is over-claiming AI/human separability.
FORBIDDEN_VERDICT_PHRASES = (
    "this text is ai",
    "this text is human",
    "reliably detect ai",
    "reliably detects ai",
    "reliably separates ai from human",
    "ai-detectable",
    "proves ai",
    "proves provenance",
)

# Refusal/caveat anchors: if the surrounding context explicitly DENIES,
# withholds, or refuses-to-license the claim, the phrase is allowed (the
# doc/license/caveat may NAME what it refuses — that is the posture, not an
# over-claim). The window is generous so a multi-clause refusal sentence
# ("No card licenses a claim that any detector reliably separates ...")
# still anchors.
_REFUSAL_ANCHORS = (
    "not ", "no ", "never", "does not", "doesn't", "without", "cannot",
    "can't", "refus", "withhold", "will not", "won't", "claim that",
    "license", "licence", "not_license", "does_not", "no auc", "no detector",
)
_REFUSAL_WINDOW = 90


def _phrase_outside_refusal(text: str, phrase: str) -> bool:
    """True if `phrase` appears in `text` (lowercased) NOT preceded by a
    refusal anchor within the preceding ~90-char window."""
    low = text.lower()
    for m in re.finditer(re.escape(phrase), low):
        start = m.start()
        window = low[max(0, start - _REFUSAL_WINDOW):start]
        if not any(anchor in window for anchor in _REFUSAL_ANCHORS):
            return True
    return False


def _assert_clean(name: str, rendered: str):
    for phrase in FORBIDDEN_VERDICT_PHRASES:
        assert not _phrase_outside_refusal(rendered, phrase), (
            f"{name} rendered output over-claims separability: "
            f"verdict phrase {phrase!r} appears outside a refusal context"
        )


# ---- the four surfaces, rendered --------------------------------------------


def test_conformal_gate_rendered_clean():
    cal = [float(x) for x in range(1, 51)]
    res = cg.gate_one_class(cal, 25, alpha=0.1,
                            direction="higher_is_nonconforming",
                            reference_label="reference")
    payload = cg.build_payload(res, target_path="cal.txt", available=True)
    _assert_clean("conformal_gate one_class", cg.render_report(payload))
    # FPR-bound mode too.
    fb = cg.gate_fpr_bound(cal, 40.0, fpr_bound=0.1,
                           direction="higher_is_nonconforming",
                           reference_label="reference")
    payload_fb = cg.build_payload(fb, target_path="cal.txt", available=True)
    _assert_clean("conformal_gate fpr_bound", cg.render_report(payload_fb))


def test_intrinsic_dimension_rendered_clean():
    def stub(texts):
        out = []
        for i, t in enumerate(texts):
            rng = np.random.default_rng((abs(hash(t)) % (2**31)) ^ i)
            out.append(rng.standard_normal(16))
        return np.asarray(out)
    text = " ".join(f"Sentence number {i} here." for i in range(60))
    results = ida.audit(text, embed=stub, embedding_model_id="stub",
                        short_text_mode="auto")
    env = ida.compose_envelope(target_path="t.txt", target_words=300,
                               results=results)
    _assert_clean("intrinsic_dimension_audit", ida.render_markdown(env))


def test_adversarial_robustness_card_rendered_clean():
    base = {"compression": {"compression_fraction": 0.4}}
    fix = {"compression": {"compression_fraction": 0.6}}
    card = arc.build_robustness_card(base=base, fixtures=[("paraphrase", fix)])
    _assert_clean("adversarial_robustness_card", arc.render_report(card))


def test_validation_harness_rendered_clean():
    # Build a minimal non-failed result and render it.
    recs = []
    for i in range(8):
        recs.append({
            "id": f"r{i}", "ai_status": "ai_generated" if i % 2 else "pre_ai_human",
            "label": i % 2, "score": float(i), "usable_for_metrics": True,
            "register": "essay", "language_status": "unknown",
            "adversarial_class": "none", "length_bucket": "200_499",
            "topic": "t1" if i < 4 else "t2",
            "observed_word_count": 300, "band": "neutral",
        })

    class _Args:
        surface = "smoothing_diagnosis"
        manifest = "m.jsonl"
        use = "validation"
        seed = 1
        metric_bootstrap_resamples = 50
        confidence_level = 0.95
        ci_method = "wilson"
        no_records_table = False
        records_limit = 100
        topic_split = True
        simpson_check = "register"

    slices = vh.build_slices(
        recs, threshold=None, confidence_level=0.95, ci_method="wilson",
        metric_bootstrap_resamples=50, seed=1)
    topic_leakage = vh.topic_leakage_diagnostic(recs, seed=1, resamples=50)
    simpson = vh.simpson_inversion_check(
        recs, strata_field="register", seed=1, resamples=50)
    result = {
        "evaluated_surface": "smoothing_diagnosis",
        "manifest_path": "m.jsonl",
        "n_validation_entries": len(recs),
        "n_scored_records": len(recs),
        "slices": slices,
        "operating_point": {"available": False, "reason": "no FPR target"},
        "warnings": [],
        "records": recs,
        "report_options": {"include_records_table": True, "records_limit": 100},
        "language_slice_active": False,
        "topic_leakage": topic_leakage,
        "simpson_inversion": simpson,
    }
    result["claim_license"] = vh.claim_license_block(result)
    _assert_clean("validation_harness", vh.render_report(result))


# ---- the posture doc exists & is linked from the robustness card ------------


def test_posture_doc_exists():
    doc = REPO_ROOT / "references" / "POSTURE_no_overclaim_separability.md"
    assert doc.is_file()
    body = doc.read_text(encoding="utf-8").lower()
    assert "2303.11156" in body
    assert "reliability claim" in body or "reliably separates" in body


def test_robustness_card_links_posture_doc():
    base = {"compression": {"compression_fraction": 0.4}}
    card = arc.build_robustness_card(base=base, fixtures=[])
    lic = arc._claim_license(card)
    refs_blob = " ".join(lic.references or [])
    caveats_blob = " ".join(lic.additional_caveats or [])
    assert "POSTURE_no_overclaim_separability.md" in (refs_blob + caveats_blob)


# ---- the matcher itself: it must NOT false-positive on benign caveats -------


def test_matcher_allows_benign_reliable_caveat():
    """The existing intrinsic caveat 'a reliable log-log scaling fit' must
    NOT trip the matcher (it is not a verdict phrase)."""
    benign = "Short text destabilizes the estimate (too few embedding units " \
             "for a reliable log-log scaling fit)."
    for phrase in FORBIDDEN_VERDICT_PHRASES:
        assert not _phrase_outside_refusal(benign, phrase)


def test_matcher_catches_a_planted_verdict():
    """Sanity: a real verdict phrase outside a refusal context IS caught."""
    planted = "The result shows this text is AI with high confidence."
    assert _phrase_outside_refusal(planted, "this text is ai")
