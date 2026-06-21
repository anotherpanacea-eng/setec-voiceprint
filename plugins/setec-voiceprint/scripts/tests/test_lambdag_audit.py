#!/usr/bin/env python3
"""Tests for lambdag_audit.py (spec 32) — the LambdaG grammar likelihood-ratio AV signal.

The n-gram LM is pure stdlib (collections + math), so the LR-math acceptances (5, 6, 11) run on
hand-built FIXTURE POS streams with NO parser — the core is CI-runnable without spaCy. The
end-to-end-with-parser cases are skipif(not HAS_SPACY). Covers the spec-32 numbered acceptances:
deterministic output, envelope shape, the no-verdict recursive-walk + never-selects posture guards
(3-4), the LR math pins both directions (5), finite add-k smoothing (6), the held-out-disjoint
anti-Goodhart guard + self-scoring refusal (7), the corpus-relativity caveat (8), graceful
degradation (9), the length floor (10), and bounds-gate compatibility (11)."""

from __future__ import annotations

import io
import json
import math
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import lambdag_audit as lg  # type: ignore  # noqa: E402
import variance_audit as va  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    VALID_TASK_SURFACES,
    OutputValidityError,
    validate_results_bounds,
)

_needs_parser = pytest.mark.skipif(
    not va.HAS_SPACY or va._NLP is None, reason="needs spaCy + en_core_web_sm")

# The forbidden keys: a same-author / AI-provenance / selection determination must
# not appear at ANY depth of the envelope (Acceptance 3).
_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "same_author", "different_author",
    "verdict", "match", "prob_same_author",
})
# Acceptance 4: no author-ranking / argmax field anywhere.
_RANKING_KEYS = frozenset({
    "ranking", "author_ranking", "ranked_authors", "argmax",
    "most_likely_author", "best_author", "author_scores",
})


# --- fixture POS streams (no parser) ---------------------------------------

# A "reference-author" grammar profile and a clearly different "background"
# profile, each repeated so the count-based LM is well-estimated.
_REF_STREAMS = [["PRON", "AUX", "VERB", "ADP", "NOUN"]] * 12
_BG_STREAMS = [["ADP", "DET", "NOUN", "VERB", "ADV"]] * 12
_QUERY_LIKE_REF = [["PRON", "AUX", "VERB", "ADP", "NOUN"]] * 4


def _ref_bg_lms(n=3, k=0.5):
    return (lg.build_lm(_REF_STREAMS, n=n, k=k), lg.build_lm(_BG_STREAMS, n=n, k=k))


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = lg.main(argv)
    return rc, json.loads(out.getvalue())


def _walk_keys(obj):
    """Yield every dict key at every depth of a JSON-like structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# ---------------------------------------------------------------------------
# Registration / import (runs without the model)
# ---------------------------------------------------------------------------

def test_surface_registered():
    assert lg.TASK_SURFACE == "voice_coherence"
    assert "voice_coherence" in VALID_TASK_SURFACES  # else build_output raises


def test_module_imports_without_spacy(monkeypatch):
    """Acceptance 9a: the module imports and the LM math runs with HAS_SPACY False."""
    monkeypatch.setattr(lg, "HAS_SPACY", False)
    monkeypatch.setattr(lg, "_NLP", None)
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    assert math.isfinite(r["lambda_g"])


# ---------------------------------------------------------------------------
# Acceptance 5 — LR math pins (no parser)
# ---------------------------------------------------------------------------

def test_identical_lms_give_zero():
    """5a: reference and background LMs identical → lambda_g == 0 exactly."""
    lm_a = lg.build_lm(_REF_STREAMS, n=3, k=0.5)
    lm_b = lg.build_lm(_REF_STREAMS, n=3, k=0.5)
    r = lg.score_query(_QUERY_LIKE_REF, lm_a, lm_b, top_k=5)
    assert r["lambda_g"] == 0.0
    assert r["lambda_g_per_token"] == 0.0


def test_ref_and_bg_share_one_vocab_for_unbiased_ratio():
    """Codex P1: ref and bg must use ONE add-k support so the smoothing denominators match and the
    likelihood ratio is unbiased. The support is now the fixed closed UPOS set + EOS, so all-UPOS
    corpora share it BY CONSTRUCTION; share_vocab additionally unions any non-standard observed tag."""
    # all-UPOS corpora: identical support already (the fixed closed inventory), no bias possible
    ref = lg.build_lm([["NOUN", "VERB", "NOUN"]], n=2, k=0.5)
    bg = lg.build_lm([["NOUN", "ADJ", "VERB"], ["DET", "NOUN", "ADP"]], n=2, k=0.5)
    assert ref.vocab == bg.vocab and ref.vocab_size == bg.vocab_size
    assert ref.vocab == set(lg.UPOS_TAGS) | {lg.EOS}
    # a NON-standard observed tag in only one corpus → differs before share, share_vocab unions it
    ref_x = lg.build_lm([["NOUN", "WEIRD", "VERB"]], n=2, k=0.5)
    bg_x = lg.build_lm([["NOUN", "VERB"]], n=2, k=0.5)
    assert "WEIRD" in ref_x.vocab and "WEIRD" not in bg_x.vocab and ref_x.vocab_size != bg_x.vocab_size
    lg.share_vocab(ref_x, bg_x)
    assert ref_x.vocab == bg_x.vocab and ref_x.vocab_size == bg_x.vocab_size
    # score_query enforces the shared support even if the caller builds the LMs and forgets to share
    ref2 = lg.build_lm([["NOUN", "WEIRD", "VERB"]], n=2, k=0.5)
    bg2 = lg.build_lm([["NOUN", "VERB"]], n=2, k=0.5)
    assert ref2.vocab_size != bg2.vocab_size
    lg.score_query([["NOUN", "VERB", "NOUN"]], ref2, bg2, top_k=5)
    assert ref2.vocab_size == bg2.vocab_size


def test_lambda_g_equals_difference_of_halves():
    """5b: lambda_g == logL_ref_nats − logL_bg_nats to float tolerance."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    assert math.isclose(
        r["lambda_g"], r["logL_ref_nats"] - r["logL_bg_nats"], abs_tol=1e-6)


def test_per_token_is_length_normalized():
    """5c: lambda_g_per_token == lambda_g / n_scored_ngrams."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    assert r["n_scored_ngrams"] > 0
    assert math.isclose(
        r["lambda_g_per_token"], r["lambda_g"] / r["n_scored_ngrams"], abs_tol=1e-6)


def test_orientation_pinned_both_directions():
    """5d: a query matching the reference profile → lambda_g > 0; sign-flipped → < 0."""
    ref, bg = _ref_bg_lms()
    r_author = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    assert r_author["lambda_g"] > 0.0
    assert r_author["band"]["band"] == "author_leaning"
    # Swap which LM is the reference: the same query now leans the other way.
    r_flip = lg.score_query(_QUERY_LIKE_REF, bg, ref, top_k=5)
    assert r_flip["lambda_g"] < 0.0
    assert r_flip["band"]["band"] == "background_leaning"
    assert math.isclose(r_author["lambda_g"], -r_flip["lambda_g"], abs_tol=1e-6)


# ---------------------------------------------------------------------------
# Acceptance 6 — smoothing well-defined (no parser)
# ---------------------------------------------------------------------------

def test_unseen_ngram_is_finite():
    """6: a query n-gram absent from BOTH corpora still gets a finite log-prob
    under add-k (no log(0), no inf) — lambda_g stays finite."""
    ref, bg = _ref_bg_lms()
    unseen = [["SYM", "X", "INTJ"]]  # tags absent from both fixture corpora
    r = lg.score_query(unseen, ref, bg, top_k=5)
    assert math.isfinite(r["lambda_g"])
    assert math.isfinite(r["logL_ref_nats"])
    assert math.isfinite(r["logL_bg_nats"])


def test_log_prob_never_zero_probability():
    """Every add-k log-prob is a finite real (a log of a strictly-positive number)."""
    ref, _ = _ref_bg_lms()
    lp = ref.log_prob(("NOUN", "NOUN"), "SYM")  # unseen tag in unseen context
    assert math.isfinite(lp) and lp < 0.0


def test_smoothing_k_must_be_positive():
    with pytest.raises(ValueError):
        lg.build_lm(_REF_STREAMS, n=3, k=0.0)


def test_n_out_of_range_rejected():
    with pytest.raises(ValueError):
        lg.build_lm(_REF_STREAMS, n=1, k=0.5)
    with pytest.raises(ValueError):
        lg.build_lm(_REF_STREAMS, n=5, k=0.5)


# ---------------------------------------------------------------------------
# Acceptance 3 — refuses-verdict (recursive walk), on the results payload
# ---------------------------------------------------------------------------

def test_results_payload_has_no_forbidden_keys():
    """3 (payload half): no forbidden verdict/selection key in the results dict."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    keys = set(_walk_keys(r))
    assert keys.isdisjoint(_FORBIDDEN_KEYS)
    assert keys.isdisjoint(_RANKING_KEYS)


def test_band_is_a_leaning_not_a_boolean():
    """The only categorical is a 3-level leaning band stamped provisional."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    band = r["band"]
    assert band["band"] in {"author_leaning", "indeterminate", "background_leaning"}
    assert band["provisional"] is True
    assert "calibration_status" in band
    assert "thresholds_used" in band
    # No boolean same/different-author lives on the band.
    assert _FORBIDDEN_KEYS.isdisjoint(band.keys())


def test_claim_license_refuses_same_author_and_ai_human():
    """3 (license half): does_not_license refuses BOTH the same-author and the
    AI/human inferences in words."""
    lic = lg._claim_license()
    dnl = lic["does_not_license"].lower()
    assert "same-author" in dnl or "same author" in dnl
    assert "ai/human" in dnl or "ai-detector" in dnl or "ai/human provenance" in dnl


# ---------------------------------------------------------------------------
# Acceptance 7 — held-out disjoint anti-Goodhart guard
# ---------------------------------------------------------------------------

def test_disjoint_passes_on_disjoint_sets():
    ref = [{"id": "a", "path": "/ref/a.txt", "text": "x"},
           {"id": "b", "path": "/ref/b.txt", "text": "y"}]
    bg = [{"id": "c", "path": "/bg/c.txt", "text": "z"},
          {"id": "d", "path": "/bg/d.txt", "text": "w"}]
    lg.assert_disjoint(ref, bg)  # no raise


def test_overlap_raises_naming_offender():
    # A genuinely shared source file (same path) IS the train-on-test overlap.
    ref = [{"id": "a", "path": "/c/a.txt", "text": "x"},
           {"id": "shared", "path": "/c/shared.txt", "text": "y"}]
    bg = [{"id": "shared", "path": "/c/shared.txt", "text": "z"},
          {"id": "d", "path": "/c/d.txt", "text": "w"}]
    with pytest.raises(lg.CorpusError) as exc:
        lg.assert_disjoint(ref, bg)
    assert "shared" in str(exc.value)


def test_self_scoring_is_refused():
    """A corpus scored against itself is the full-overlap degenerate case."""
    corpus = [{"id": "a", "path": "/c/a.txt", "text": "x"},
              {"id": "b", "path": "/c/b.txt", "text": "y"}]
    with pytest.raises(lg.CorpusError):
        lg.assert_disjoint(corpus, corpus)


def test_same_stem_in_two_dirs_is_not_falsely_refused(tmp_path):
    """mode-6 regression: two genuinely distinct files in two distinct dirs that
    share a basename (ref/doc.txt vs bg/doc.txt) collide on the stem-derived `id`
    but have distinct `path`s — assert_disjoint must NOT false-positive. Loads via
    the REAL dir loader so the stem-id derivation is exercised, not mocked."""
    from stylometry_core import load_entries_from_dir  # type: ignore
    ref_dir = tmp_path / "ref"; ref_dir.mkdir()
    bg_dir = tmp_path / "bg"; bg_dir.mkdir()
    (ref_dir / "doc.txt").write_text("Reference author text for the doc file.", encoding="utf-8")
    (bg_dir / "doc.txt").write_text("Distinct background text in this doc file.", encoding="utf-8")
    ref = load_entries_from_dir(str(ref_dir))
    bg = load_entries_from_dir(str(bg_dir))
    assert ref[0]["id"] == bg[0]["id"] == "doc"  # ids collide (stem-derived)
    assert ref[0]["path"] != bg[0]["path"]       # but paths are distinct
    lg.assert_disjoint(ref, bg)  # must NOT raise


# ---------------------------------------------------------------------------
# Acceptance 8 — background-relativity caveat surfaced
# ---------------------------------------------------------------------------

def test_corpus_dependence_caveat_present():
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    caveat = r["assumptions"]["corpus_dependence"]
    assert "relative" in caveat.lower()
    assert "background" in caveat.lower()


# ---------------------------------------------------------------------------
# Acceptance 11 — bounds-gate compatibility
# ---------------------------------------------------------------------------

def test_negative_lambda_g_passes_bounds_gate():
    """11: negative lambda_g / logL_*_nats pass validate_results_bounds — they are
    not surprisal/probability-classified keys, so no >=0 / [0,1] check applies."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, lg.build_lm(_BG_STREAMS, n=3, k=0.5), bg, top_k=5)
    # A reference LM unlike the query → negative lambda_g, negative logL halves.
    assert r["logL_ref_nats"] < 0.0 and r["logL_bg_nats"] < 0.0
    validate_results_bounds(r)  # must not raise


def test_nan_injected_payload_is_rejected():
    """11: a NaN on any leaf is rejected by the bounds gate (the real corruption guard)."""
    ref, bg = _ref_bg_lms()
    r = lg.score_query(_QUERY_LIKE_REF, ref, bg, top_k=5)
    r["lambda_g"] = float("nan")
    with pytest.raises(OutputValidityError):
        validate_results_bounds(r)


# ---------------------------------------------------------------------------
# Acceptance 9c — bad-input error paths (no parser needed for the arg-level ones)
# ---------------------------------------------------------------------------

def test_missing_query_is_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(lg, "HAS_SPACY", True)
    monkeypatch.setattr(lg, "_NLP", object())  # truthy; we never reach the parse
    rc, env = _envelope([str(tmp_path / "nope.txt"),
                         "--reference-dir", str(tmp_path), "--background-dir", str(tmp_path),
                         "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert rc == 3


def test_missing_parser_abstains(tmp_path, monkeypatch):
    """Acceptance 9b: end-to-end with no parser → available:false / missing_dependency."""
    q = tmp_path / "q.txt"
    q.write_text("Some prose to score.", encoding="utf-8")
    monkeypatch.setattr(lg, "HAS_SPACY", False)
    monkeypatch.setattr(lg, "_NLP", None)
    rc, env = _envelope([str(q), "--reference-dir", str(tmp_path),
                         "--background-dir", str(tmp_path), "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    assert rc == 3


def test_bad_n_returns_usage_error(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("x", encoding="utf-8")
    rc = lg.main([str(q), "--reference-dir", str(tmp_path),
                  "--background-dir", str(tmp_path), "--n", "9"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Parser-dependent end-to-end (skipif not HAS_SPACY)
# ---------------------------------------------------------------------------

_REF_TEXT = (
    "The committee deliberated at length about the proposed amendment. Members raised "
    "concerns regarding the implementation timeline and the budgetary impact. After "
    "considerable discussion, a revised motion was tabled for the following session. "
    "The chair noted that the secretariat would circulate the minutes in due course."
)
_BG_TEXT = (
    "She wandered through the old market, touching the worn fabrics. A vendor smiled. "
    "The smell of warm bread drifted past her. Somewhere a child laughed, and the "
    "afternoon light fell soft across the cobbled stones. She lingered, unhurried."
)
_QUERY_TEXT = (
    "The board reviewed the quarterly report and discussed the strategic implications. "
    "Several directors questioned the assumptions underlying the revenue forecast, and a "
    "follow-up analysis was requested before the next scheduled meeting of the council."
)


def _write_corpora(tmp_path):
    ref_dir = tmp_path / "ref"; ref_dir.mkdir()
    bg_dir = tmp_path / "bg"; bg_dir.mkdir()
    (ref_dir / "r1.txt").write_text(_REF_TEXT, encoding="utf-8")
    (bg_dir / "b1.txt").write_text(_BG_TEXT, encoding="utf-8")
    q = tmp_path / "q.txt"; q.write_text(_QUERY_TEXT, encoding="utf-8")
    return q, ref_dir, bg_dir


@_needs_parser
def test_end_to_end_envelope_shape(tmp_path):
    """Acceptance 2: full envelope shape with the results keys and a claim license."""
    q, ref_dir, bg_dir = _write_corpora(tmp_path)
    rc, env = _envelope([str(q), "--reference-dir", str(ref_dir),
                         "--background-dir", str(bg_dir), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "voice_coherence"
    assert env["tool"] == "lambdag_audit"
    assert env["claim_license"] is not None
    r = env["results"]
    for key in ("lambda_g", "lambda_g_per_token", "logL_ref_nats", "logL_bg_nats",
                "n_scored_ngrams", "n", "smoothing", "pos_tagset", "band",
                "per_sentence", "top_author_favoring_ngrams",
                "top_background_favoring_ngrams", "reference_summary",
                "background_summary", "assumptions"):
        assert key in r, f"missing results key {key!r}"
    assert r["reference_summary"]["n_docs"] == 1
    assert r["background_summary"]["n_docs"] == 1


@_needs_parser
def test_end_to_end_no_forbidden_key_recursive(tmp_path):
    """Acceptance 3 (full envelope): a recursive walk of the WHOLE envelope finds no
    forbidden verdict/selection key at any depth."""
    q, ref_dir, bg_dir = _write_corpora(tmp_path)
    _, env = _envelope([str(q), "--reference-dir", str(ref_dir),
                        "--background-dir", str(bg_dir), "--json"])
    keys = set(_walk_keys(env))
    assert keys.isdisjoint(_FORBIDDEN_KEYS)
    assert keys.isdisjoint(_RANKING_KEYS)


@_needs_parser
def test_end_to_end_deterministic(tmp_path):
    """Acceptance 1: same query + reference + background → identical results."""
    q, ref_dir, bg_dir = _write_corpora(tmp_path)
    argv = [str(q), "--reference-dir", str(ref_dir), "--background-dir", str(bg_dir), "--json"]
    _, env1 = _envelope(argv)
    _, env2 = _envelope(argv)
    assert env1["results"] == env2["results"]


@_needs_parser
def test_never_selects_with_multi_author_manifest(tmp_path):
    """Acceptance 4: given a manifest with multiple authors the surface scores the
    query against ONE reference selection + the background and emits no ranking."""
    # Build a manifest: persona A is the reference, persona B the background.
    a1 = tmp_path / "a1.txt"; a1.write_text(_REF_TEXT, encoding="utf-8")
    b1 = tmp_path / "b1.txt"; b1.write_text(_BG_TEXT, encoding="utf-8")
    q = tmp_path / "q.txt"; q.write_text(_QUERY_TEXT, encoding="utf-8")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"id": "a1", "path": "a1.txt", "use": "baseline",
                    "persona": "author_a", "ai_status": "pre_ai_human"}) + "\n"
        + json.dumps({"id": "b1", "path": "b1.txt", "use": "baseline",
                      "persona": "author_b", "ai_status": "pre_ai_human"}) + "\n",
        encoding="utf-8",
    )
    rc, env = _envelope([str(q), "--manifest", str(manifest),
                         "--reference-persona", "author_a",
                         "--background-persona", "author_b", "--json"])
    assert rc == 0 and env["available"] is True
    keys = set(_walk_keys(env))
    assert keys.isdisjoint(_RANKING_KEYS)
    assert env["results"]["reference_summary"]["n_docs"] == 1


@_needs_parser
def test_manifest_untagged_reference_is_not_silently_dropped(tmp_path):
    """P1 regression: the documented persona-only manifest example must load a
    reference author whose entries are NOT tagged use:baseline / ai_status:pre_ai_human
    (the operator's own writing usually isn't). Before the fix, _load_corpus let the
    loader fall back to use='baseline'/ai_status='pre_ai_human', dropping every such
    entry and raising 'reference corpus is empty'. The entries here carry ONLY id +
    path + persona — no use, no ai_status — so this test FAILS if the loader defaults
    leak back in."""
    a1 = tmp_path / "a1.txt"; a1.write_text(_REF_TEXT, encoding="utf-8")
    b1 = tmp_path / "b1.txt"; b1.write_text(_BG_TEXT, encoding="utf-8")
    q = tmp_path / "q.txt"; q.write_text(_QUERY_TEXT, encoding="utf-8")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"id": "a1", "path": "a1.txt", "persona": "me"}) + "\n"
        + json.dumps({"id": "b1", "path": "b1.txt", "persona": "pool"}) + "\n",
        encoding="utf-8",
    )
    rc, env = _envelope([str(q), "--manifest", str(manifest),
                         "--reference-persona", "me",
                         "--background-persona", "pool", "--json"])
    assert rc == 0 and env["available"] is True, env.get("reason")
    assert env["results"]["reference_summary"]["n_docs"] == 1
    assert env["results"]["background_summary"]["n_docs"] == 1


@_needs_parser
def test_manifest_use_filter_still_honored_when_passed(tmp_path):
    """P1 fix must not over-correct: when the operator DOES pass --reference-use, the
    filter is honored (an entry not matching that use is dropped). Guards against the
    'always None' degenerate that would make --reference-use a no-op."""
    a1 = tmp_path / "a1.txt"; a1.write_text(_REF_TEXT, encoding="utf-8")
    b1 = tmp_path / "b1.txt"; b1.write_text(_BG_TEXT, encoding="utf-8")
    q = tmp_path / "q.txt"; q.write_text(_QUERY_TEXT, encoding="utf-8")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"id": "a1", "path": "a1.txt", "persona": "me", "use": "validation"}) + "\n"
        + json.dumps({"id": "b1", "path": "b1.txt", "persona": "pool"}) + "\n",
        encoding="utf-8",
    )
    # Ask for use=baseline on the reference, but the only 'me' entry is use=validation.
    rc, env = _envelope([str(q), "--manifest", str(manifest),
                         "--reference-persona", "me", "--reference-use", "baseline",
                         "--background-persona", "pool", "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert "reference corpus is empty" in env["reason"]


@_needs_parser
def test_manifest_overlap_refused_end_to_end(tmp_path):
    """Acceptance 7 end-to-end: the same persona for both corpora → full overlap →
    bad_input naming the offending id."""
    a1 = tmp_path / "a1.txt"; a1.write_text(_REF_TEXT, encoding="utf-8")
    q = tmp_path / "q.txt"; q.write_text(_QUERY_TEXT, encoding="utf-8")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"id": "a1", "path": "a1.txt", "use": "baseline",
                    "persona": "author_a", "ai_status": "pre_ai_human"}) + "\n",
        encoding="utf-8",
    )
    rc, env = _envelope([str(q), "--manifest", str(manifest),
                         "--reference-persona", "author_a",
                         "--background-persona", "author_a", "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert "a1" in env["reason"]


@_needs_parser
def test_length_floor_warns(tmp_path):
    """Acceptance 10: a query below the ~150-word floor still computes but warns."""
    q, ref_dir, bg_dir = _write_corpora(tmp_path)  # query is < 150 words
    _, env = _envelope([str(q), "--reference-dir", str(ref_dir),
                        "--background-dir", str(bg_dir), "--json"])
    assert env["available"] is True
    assert any("unstable on short text" in w for w in env["warnings"])


@_needs_parser
def test_end_to_end_passes_bounds_gate(tmp_path):
    """Acceptance 11 end-to-end: build_output's internal validate_results_bounds did
    not raise (available:true means the gate passed on the real payload)."""
    q, ref_dir, bg_dir = _write_corpora(tmp_path)
    _, env = _envelope([str(q), "--reference-dir", str(ref_dir),
                        "--background-dir", str(bg_dir), "--json"])
    assert env["available"] is True
    validate_results_bounds(env["results"])  # belt-and-suspenders, must not raise


def test_conditional_distribution_normalizes_over_closed_upos_support():
    """Codex round-9 P1: the add-k support is the CLOSED UPOS inventory + EOS, NOT just the
    observed tags. Each P(tag | context) must sum to exactly 1 over the full support —
    including for a valid UPOS tag (ADJ) seen in neither corpus. Pre-fix, vocab_size counted
    only observed tags while log_prob gave any tag add-k mass, so the conditional summed > 1."""
    lm = lg.GrammarLM(n=2, k=0.5)
    lm.add_sentences([["NOUN", "VERB"], ["VERB", "NOUN"]])  # ADJ never observed
    support = set(lg.UPOS_TAGS) | {lg.EOS}
    # a valid UPOS tag absent from training is still in the support (this was the bug)
    assert "ADJ" in lm.vocab
    assert lm.vocab_size == len(support)
    for ctx in [("NOUN",), ("VERB",), ("<s>",), ("ZZZ",)]:  # seen, seen, BOS-context, unseen
        total = sum(math.exp(lm.log_prob(ctx, t)) for t in support)
        assert abs(total - 1.0) < 1e-9, (ctx, total)
