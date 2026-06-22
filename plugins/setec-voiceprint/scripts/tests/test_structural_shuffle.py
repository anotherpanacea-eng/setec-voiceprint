"""Tests for ``structural_shuffle_audit.py`` (spec 32, M1 — model-free).

Pin the spec contract:

  * sentence splitter (regex fallback) + seeded sentence/word shuffles,
  * the 5-feature arithmetic AND the sign/direction (silent-inversion guard),
  * envelope shape + correct task_surface (discrimination_structural_shuffle),
  * no default threshold / verdict band, and a recursive no-verdict key walk,
  * claim license refuses an AI/human verdict AND names the ESL failure mode,
  * graceful clean install hint when torch is absent,
  * the separation guard (no fitness/setec_signals/... imports) + Binoculars
    orthogonality (no binoculars_audit import, no cross-perplexity key),
  * the capabilities manifest carries the structural_shuffle_audit entry,
  * math/bounds edge cases (empty, single sentence, tie/saturated, rank-0).

No real model loads and no GPU: ``score_window`` accepts injected perplexities.
"""

from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
sys.path.insert(0, str(_SCRIPTS))

import structural_shuffle_audit as ss  # noqa: E402


# ============================================================
# Stub backend (no model)
# ============================================================


class StubBackend:
    """Minimal scorer stub: a ``model_id`` and a ``score_text`` that returns a
    fixed per-token surprisal series in BITS. Used only for the real-scoring
    path test; the feature/envelope tests inject perplexities directly."""

    def __init__(self, model_id="stub-model", bits_by_text=None, default_bits=None):
        self.model_id = model_id
        self._bits_by_text = bits_by_text or {}
        self._default_bits = default_bits if default_bits is not None else [1.0, 1.0]

    def score_text(self, text):
        return list(self._bits_by_text.get(text, self._default_bits))


_THREE_SENTENCES = "The cat sat on the mat. The dog ran in the park. Birds flew away."


# ============================================================
# 1. split_sentences
# ============================================================


def test_split_sentences_three():
    sents = ss.split_sentences(_THREE_SENTENCES)
    assert len(sents) == 3


def test_split_sentences_regex_fallback(monkeypatch):
    """Force the regex fallback (simulate spaCy absent) — still 3 sentences."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "spacy" or name.startswith("spacy."):
            raise ImportError("No module named 'spacy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    sents = ss.split_sentences(_THREE_SENTENCES)
    assert len(sents) == 3


def test_split_sentences_empty():
    assert ss.split_sentences("") == []
    assert ss.split_sentences("   \n  ") == []


# ============================================================
# 2-4. Shuffle determinism + word preservation
# ============================================================


def test_shuffle_sentences_deterministic():
    sents = ss.split_sentences(_THREE_SENTENCES)
    a, a_changed = ss.shuffle_sentences(sents, seed=0)
    b, b_changed = ss.shuffle_sentences(sents, seed=0)
    assert a == b  # same seed -> same order
    assert a_changed and b_changed
    # A different seed should (for >= 3 sentences) generally differ. Find one.
    assert any(ss.shuffle_sentences(sents, seed=s)[0] != a for s in range(1, 12))


def test_shuffle_words_deterministic():
    a, a_changed = ss.shuffle_words(_THREE_SENTENCES, seed=0)
    b, b_changed = ss.shuffle_words(_THREE_SENTENCES, seed=0)
    assert a == b  # same seed -> same order
    assert a_changed and b_changed
    assert any(ss.shuffle_words(_THREE_SENTENCES, seed=s)[0] != a for s in range(1, 12))


def test_shuffle_sentences_same_words():
    sents = ss.split_sentences(_THREE_SENTENCES)
    shuffled, changed = ss.shuffle_sentences(sents, seed=3)
    assert changed
    assert sorted(shuffled.split()) == sorted(" ".join(sents).split())


def test_shuffle_words_same_words():
    shuffled, changed = ss.shuffle_words(_THREE_SENTENCES, seed=3)
    assert changed
    assert sorted(shuffled.split()) == sorted(_THREE_SENTENCES.split())


# ============================================================
# Codex round-9 P2 #2: sentence-shuffle must change order
# ============================================================


def test_two_sentence_seed0_non_identity_permutation():
    """REGRESSION (Codex round-9 P2): a 2-sentence input under the DEFAULT seed
    0 lands on the identity for a single `random.shuffle`, so ppl_sent would
    silently re-score the original order. With the re-roll guard the joined
    order MUST actually change and `order_changed` MUST be True."""
    sents = ["The cat sat on the mat.", "The dog ran in the park."]
    original = " ".join(sents)
    shuffled, changed = ss.shuffle_sentences(sents, seed=ss.DEFAULT_SEED)
    assert changed is True
    assert shuffled != original, "seed-0 sentence shuffle returned the identity"
    # Same multiset of sentences, just reordered.
    assert sorted(shuffled.split()) == sorted(original.split())


def test_score_window_two_sentence_seed0_measures_order_change():
    """End-to-end: score_window over a 2-sentence input at seed 0 must feed a
    genuinely reordered string to ppl_sent (not the original twice). With a
    per-text stub, a reordered sent string scores differently from the
    original; an identity (pre-fix) would score identically."""
    s1 = "The cat sat on the mat."
    s2 = "The dog ran in the park."
    text = f"{s1} {s2}"
    reordered = f"{s2} {s1}"
    backend = StubBackend(
        "M",
        bits_by_text={text: [1.0, 1.0], reordered: [3.0, 3.0]},
        default_bits=[1.0, 1.0],
    )
    results = ss.score_window(text, backend=backend, seed=0)
    # ppl_orig = 2**1 = 2.0; ppl_sent must reflect the REORDERED string
    # (2**3 = 8.0), proving a real order change was scored, not the original.
    assert results["ppl_orig"] == pytest.approx(2.0)
    assert results["ppl_sent"] == pytest.approx(8.0)
    assert "sentence_shuffle_no_distinct_order" not in results["caveats"]


def test_identical_sentences_surface_caveat():
    """When every sentence is textually identical no order change is possible;
    surface a caveat instead of silently scoring the original twice."""
    sents = ["Same sentence.", "Same sentence.", "Same sentence."]
    shuffled, changed = ss.shuffle_sentences(sents, seed=0)
    assert changed is False
    assert shuffled == " ".join(sents)
    # And score_window surfaces it as a caveat.
    text = "Same sentence. Same sentence. Same sentence."
    backend = StubBackend("M", default_bits=[1.0, 1.0])
    results = ss.score_window(text, backend=backend, seed=0)
    assert "sentence_shuffle_no_distinct_order" in results["caveats"]


# ============================================================
# Word-shuffle identity guard (sibling of the sentence guard).
# REGRESSION: the round-9 P2 re-roll + no-distinct-order caveat was applied to
# shuffle_sentences only; the word side was left untreated, so a degenerate
# word-shuffle silently re-scored the original ordering with NO operator signal.
# ============================================================


def test_shuffle_words_returns_order_changed_flag():
    """shuffle_words must mirror shuffle_sentences and return (text, changed).
    Pre-fix it returned a bare str, so this unpacking would fail."""
    shuffled, changed = ss.shuffle_words(_THREE_SENTENCES, seed=ss.DEFAULT_SEED)
    assert isinstance(shuffled, str)
    assert isinstance(changed, bool)
    assert changed is True
    assert shuffled != _THREE_SENTENCES, "word shuffle returned the identity"


def test_shuffle_words_single_token_no_order_change():
    """A 1-token passage cannot be word-shuffled; order_changed MUST be False
    and the text is returned unchanged (no phantom jump)."""
    shuffled, changed = ss.shuffle_words("solo", seed=ss.DEFAULT_SEED)
    assert changed is False
    assert shuffled == "solo"


def test_shuffle_words_identical_tokens_no_order_change():
    """When every whitespace token is textually identical no order change is
    observable; order_changed MUST be False instead of claiming a real
    perturbation (the word-side sibling of test_identical_sentences)."""
    shuffled, changed = ss.shuffle_words("na na na na na na", seed=ss.DEFAULT_SEED)
    assert changed is False
    assert shuffled == "na na na na na na"


def test_shuffle_words_seed0_short_input_non_identity():
    """REGRESSION (sibling of test_two_sentence_seed0_non_identity_permutation):
    a short distinct-token input under the DEFAULT seed can land on the identity
    for a single random.shuffle. With the re-roll guard the joined order MUST
    actually change and order_changed MUST be True."""
    found_reroll_case = False
    for txt in ("a b", "a b c", "x y z w"):
        shuffled, changed = ss.shuffle_words(txt, seed=ss.DEFAULT_SEED)
        assert changed is True, f"{txt!r}: word shuffle returned the identity"
        assert shuffled != txt, f"{txt!r}: joined order did not change"
        assert sorted(shuffled.split()) == sorted(txt.split())
        # Confirm at least one of these would have been an identity under a
        # single un-re-rolled shuffle (proving the guard does real work).
        import random as _r
        toks = txt.split()
        single = list(toks)
        _r.Random(ss.DEFAULT_SEED).shuffle(single)
        if " ".join(single) == txt:
            found_reroll_case = True
    assert found_reroll_case, (
        "test fixture no longer exercises the re-roll path; pick a short input "
        "whose single seed-0 shuffle is the identity"
    )


def test_score_window_word_identity_surfaces_caveat():
    """END-TO-END REGRESSION: a passage whose word-shuffle is the identity
    (all-identical tokens) must surface `word_shuffle_no_distinct_order`. Pre-fix
    score_window scored the original ordering twice for ppl_word with NO caveat —
    exactly the failure the sentence guard already prevents on its sibling."""
    text = "na na na na na na"
    backend = StubBackend("M", default_bits=[1.0, 1.0])
    results = ss.score_window(text, backend=backend, seed=ss.DEFAULT_SEED)
    assert "word_shuffle_no_distinct_order" in results["caveats"]
    # ppl_word must NOT be silently presented as a real shuffle jump.
    assert results["ppl_word"] == results["ppl_orig"]


def test_score_window_single_token_surfaces_caveat():
    """A single-token passage cannot be word-shuffled; surface
    `single_token_no_word_shuffle` rather than a phantom word-shuffle jump."""
    backend = StubBackend("M", default_bits=[1.0, 1.0])
    results = ss.score_window("solo", backend=backend, seed=ss.DEFAULT_SEED)
    assert "single_token_no_word_shuffle" in results["caveats"]


def test_score_window_word_shuffle_real_change_no_caveat():
    """A normal multi-distinct-token passage must NOT carry either word-identity
    caveat — the guard fires only on the degenerate case."""
    s1 = "The cat sat on the mat."
    s2 = "The dog ran in the park."
    text = f"{s1} {s2}"
    backend = StubBackend("M", default_bits=[1.0, 1.0])
    results = ss.score_window(text, backend=backend, seed=ss.DEFAULT_SEED)
    assert "word_shuffle_no_distinct_order" not in results["caveats"]
    assert "single_token_no_word_shuffle" not in results["caveats"]


# ============================================================
# 5. Feature arithmetic (pinned to 6 d.p.)
# ============================================================


def test_feature_extraction_arithmetic():
    f = ss.extract_shuffle_features(ppl_orig=2.0, ppl_sent=3.0, ppl_word=4.0)
    assert round(f["ppl_sum"], 6) == 7.0
    assert round(f["ppl_diff"], 6) == 1.5
    assert round(f["ppl_ratio"], 6) == 1.75
    assert round(f["ppl_log_ratio"], 6) == round(math.log(1.75), 6)
    assert round(f["ppl_pct_change"], 6) == 75.0


# ============================================================
# 6. Sign / direction (the silent-inversion guard)
# ============================================================


def test_feature_direction_sign():
    """A LARGER shuffled perplexity (a bigger jump) must yield strictly larger
    ppl_diff / ppl_ratio / ppl_log_ratio / ppl_pct_change — the human-likely
    direction. Pins the perturbation family's shared sign-inversion failure."""
    low = ss.extract_shuffle_features(ppl_orig=10.0, ppl_sent=11.0, ppl_word=11.0)
    high = ss.extract_shuffle_features(ppl_orig=10.0, ppl_sent=30.0, ppl_word=30.0)
    for key in ("ppl_diff", "ppl_ratio", "ppl_log_ratio", "ppl_pct_change"):
        assert high[key] > low[key], f"{key} did not increase with a bigger jump"
    # A jump (shuffled > orig) is positive; no jump (shuffled == orig) is zero.
    none = ss.extract_shuffle_features(ppl_orig=10.0, ppl_sent=10.0, ppl_word=10.0)
    assert none["ppl_diff"] == 0.0
    assert none["ppl_ratio"] == 1.0
    assert none["ppl_log_ratio"] == 0.0
    assert none["ppl_pct_change"] == 0.0
    assert low["ppl_diff"] > 0.0


# ============================================================
# 7. Envelope shape
# ============================================================


def test_envelope_shape():
    results = ss.audit(
        "word " * 200,
        backend=StubBackend("scoring-model"),
        injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/dummy.txt"), target_words=200, results=results,
    )
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "discrimination_structural_shuffle"
    assert env["tool"] == "structural_shuffle_audit"
    assert env["available"] is True
    assert env["claim_license"]["task_surface"] == "discrimination_structural_shuffle"

    r = env["results"]
    assert r["model_id"] == "scoring-model"
    assert r["seed"] == ss.DEFAULT_SEED
    assert r["ppl_orig"] == 2.0 and r["ppl_sent"] == 3.0 and r["ppl_word"] == 4.0
    assert r["score_version"] == "structural_shuffle_v1"
    feats = r["features"]
    for key in ("ppl_sum", "ppl_diff", "ppl_ratio", "ppl_log_ratio", "ppl_pct_change"):
        assert key in feats


def test_surface_registered_in_label_map():
    import claim_license as cl_mod
    assert "discrimination_structural_shuffle" in cl_mod.TASK_SURFACE_LABELS
    import output_schema as os_mod
    assert "discrimination_structural_shuffle" in os_mod.VALID_TASK_SURFACES


# ============================================================
# 8-9. No verdict / no band / no-verdict key walk
# ============================================================


_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "is_smoothed", "verdict", "verdict_band", "band",
    "label", "class", "classification", "decision", "score", "confidence",
    "rank", "prediction", "flag", "selection", "best", "top", "selected",
    "threshold", "threshold_low", "threshold_high",
})


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


def test_no_default_verdict_band():
    results = ss.audit(
        "x", backend=StubBackend(), injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    r = env["results"]
    assert "verdict_band" not in r
    assert "verdict" not in r
    assert "band" not in r
    # No shipped threshold constant anywhere in the module.
    assert not hasattr(ss, "DEFAULT_THRESHOLD_LOW")
    assert not hasattr(ss, "DEFAULT_THRESHOLD_HIGH")
    assert "no_calibrated_thresholds_supplied" in r["caveats"]
    assert env["claim_license"]["comparison_set"]["threshold"] is None


def test_no_verdict_keys_in_results():
    results = ss.audit(
        "x", backend=StubBackend(), injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    keys = set(_walk_keys(env["results"]))
    leaked = keys & _FORBIDDEN_KEYS
    assert not leaked, f"verdict-shaped keys leaked into results: {leaked}"


# ============================================================
# 10-11. Claim license refuses verdict + names the ESL failure mode
# ============================================================


def test_claim_license_refuses_verdict():
    results = ss.audit(
        "x", backend=StubBackend("M"), injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    cl = env["claim_license"]
    assert "shuffl" in cl["licenses"].lower()
    assert "not a verdict" in cl["licenses"].lower()
    dnl = cl["does_not_license"].lower()
    assert "verdict" in dnl
    assert "threshold" in dnl
    # References cite the (provisional) Luminol arXiv id + the DetectGPT fallback.
    refs = " ".join(cl["references"])
    assert "2604.25860" in refs
    assert "DetectGPT" in refs or "2301.11305" in refs


def test_claim_license_esl_failure_mode():
    results = ss.audit(
        "x", backend=StubBackend(), injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    dnl = env["claim_license"]["does_not_license"]
    assert ("ESL" in dnl) or ("non-native" in dnl.lower())


def test_claim_license_does_not_assert_paper_fpr():
    """The paper's news-domain 0.001 FPR must NOT be carried as fact in the
    licenses block (it is UNVERIFIED). It may only be named as not-carried."""
    results = ss.audit(
        "x", backend=StubBackend(), injected_perplexities=(2.0, 3.0, 4.0),
    )
    env = ss.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    assert "0.001" not in env["claim_license"]["licenses"]


# ============================================================
# Real-scoring path (stub score_text in BITS -> perplexity)
# ============================================================


def test_score_window_real_backend_path():
    """With a stub exposing score_text (bits), the orchestrator computes
    perplexity = 2 ** mean_bits and derives the features — no injection."""
    text = "The cat sat on the mat. The dog ran in the park. Birds flew away. " * 4
    # All variants score a constant 2.0 bits/token -> perplexity 4.0 each.
    backend = StubBackend("M", default_bits=[2.0, 2.0, 2.0])
    results = ss.score_window(text, backend=backend, seed=0)
    assert results["ppl_orig"] == pytest.approx(4.0)
    assert results["ppl_sent"] == pytest.approx(4.0)
    assert results["ppl_word"] == pytest.approx(4.0)
    # Equal perplexities -> no jump.
    assert results["features"]["ppl_diff"] == pytest.approx(0.0)
    assert results["features"]["ppl_ratio"] == pytest.approx(1.0)


def test_score_window_requires_backend_or_injection():
    with pytest.raises(ValueError):
        ss.score_window("x", backend=None, injected_perplexities=None)


# ============================================================
# Math / bounds edge cases
# ============================================================


def test_rank_zero_ppl_orig_yields_none_ratio_features():
    """ppl_orig == 0 (degenerate/empty) -> relative-lift features are None, not a
    fabricated 0.0/1.0; ppl_diff / ppl_sum stay defined."""
    f = ss.extract_shuffle_features(ppl_orig=0.0, ppl_sent=3.0, ppl_word=5.0)
    assert f["ppl_ratio"] is None
    assert f["ppl_log_ratio"] is None
    assert f["ppl_pct_change"] is None
    assert f["ppl_sum"] == 8.0
    assert f["ppl_diff"] == 4.0  # avg(3,5) - 0


def test_log_ratio_none_when_avg_shuffled_zero():
    f = ss.extract_shuffle_features(ppl_orig=2.0, ppl_sent=0.0, ppl_word=0.0)
    assert f["ppl_ratio"] == 0.0
    assert f["ppl_log_ratio"] is None  # log(0) undefined
    assert f["ppl_pct_change"] == -100.0


def test_saturated_tie_features_finite():
    f = ss.extract_shuffle_features(ppl_orig=5.0, ppl_sent=5.0, ppl_word=5.0)
    assert f["ppl_diff"] == 0.0
    assert f["ppl_ratio"] == 1.0
    assert f["ppl_log_ratio"] == 0.0


def test_empty_input_does_not_crash():
    backend = StubBackend("M", default_bits=[])  # empty series -> perplexity 0.0
    results = ss.score_window("", backend=backend, seed=0)
    assert results["ppl_orig"] == 0.0
    assert "ppl_orig_degenerate_relative_features_unavailable" in results["caveats"]
    assert results["features"]["ppl_ratio"] is None


def test_single_sentence_caveat():
    backend = StubBackend("M", default_bits=[1.5, 1.5])
    results = ss.score_window("Just one sentence here.", backend=backend, seed=0)
    assert "single_sentence_no_sentence_shuffle" in results["caveats"]


# ============================================================
# 12. Missing torch graceful (CLI)
# ============================================================


def test_missing_torch_graceful(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")

    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    # --scorer is required; supply one so we reach the torch gate (the path
    # under test) rather than exiting at argparse.
    rc = ss.main([str(target), "--scorer", "gpt2"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "surprisal tier" in err
    assert "pip install" in err
    assert "Traceback" not in err


def test_cli_returns_nonzero_on_missing_target(tmp_path):
    # --scorer is required, so supply one; the missing-target check fires first.
    assert ss.main([str(tmp_path / "nonexistent.txt"), "--scorer", "gpt2"]) == 1


# ============================================================
# Codex round-9 P2 #1: default/required scorer must resolve
# ============================================================


def test_scorer_arg_is_required(tmp_path, capsys):
    """REGRESSION (Codex round-9 P2): the CLI must NOT advertise a default
    scorer that cannot resolve. With no default, omitting --scorer is an
    argparse usage error (SystemExit 2), not a run that fails deep in the
    backend on a bogus HF id."""
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ss.main([str(target)])
    assert exc.value.code == 2  # argparse "required argument" exit
    err = capsys.readouterr().err
    assert "scorer" in err.lower()


def test_no_argparse_default_scorer():
    """There is no argparse default that would let the documented/default
    command run against the unlanded gpt_neo_2_7b alias. The planned scorer is
    a doc label only (PLANNED_M2_SCORER), NOT wired as a default, and is not a
    resolvable MODEL_ALIASES key."""
    assert not hasattr(ss, "DEFAULT_SCORER")
    assert ss.PLANNED_M2_SCORER == "gpt_neo_2_7b"
    import surprisal_backend as sb
    assert ss.PLANNED_M2_SCORER not in sb.MODEL_ALIASES


def test_unlanded_alias_fails_loudly_not_silently(tmp_path, capsys):
    """REGRESSION (Codex round-9 P2): explicitly passing the not-yet-landed
    gpt_neo_2_7b bare alias must FAIL loudly (rc 3) with a clear message —
    never silently pass it through to a confusing weight-download error and
    never claim an audit ran. (torch IS installed in CI, so this exercises the
    resolve guard, which fires BEFORE any weights load.)"""
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    rc = ss.main([str(target), "--scorer", "gpt_neo_2_7b"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "does not resolve" in err
    assert "gpt_neo_2_7b" in err
    assert "Traceback" not in err


def test_bare_unknown_token_scorer_fails_loudly(tmp_path, capsys):
    """A bare token that is neither a known alias nor a 'org/model' HF id must
    be rejected before weight loading rather than passed through verbatim."""
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    rc = ss.main([str(target), "--scorer", "not_a_real_model"])
    assert rc == 3
    err = capsys.readouterr().err
    assert "does not resolve" in err


# ============================================================
# Codex round-10 P2: scorer validation must precede the torch gate
# (the MODEL-FREE CI path — torch absent — must still refuse a bad
# scorer with rc=3, not misdiagnose it as rc=2 "deps missing")
# ============================================================


def _hide_torch(monkeypatch):
    """Make ``import torch`` raise ImportError, simulating the model-free CI
    environment where the surprisal tier is not installed."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)


def test_unlanded_alias_fails_loudly_without_torch(monkeypatch, tmp_path, capsys):
    """REGRESSION (Codex round-10 P2): on the model-free CI path (torch ABSENT)
    the unlanded gpt_neo_2_7b alias must STILL fail with rc=3 (invalid scorer),
    NOT rc=2 (deps missing). Pre-fix the torch gate ran before scorer
    validation, so torch-absent CI short-circuited to rc=2 and CI went red.
    Scorer validation only needs the torch-free MODEL_ALIASES + resolve_model_arg,
    so it must precede the torch gate and be reachable without torch."""
    _hide_torch(monkeypatch)
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    rc = ss.main([str(target), "--scorer", "gpt_neo_2_7b"])
    assert rc == 3, "torch-absent CI must refuse a bad scorer (rc 3), not rc 2"
    err = capsys.readouterr().err
    assert "does not resolve" in err
    assert "gpt_neo_2_7b" in err
    # Must be the scorer refusal, not the dependency hint.
    assert "surprisal tier" not in err
    assert "Traceback" not in err


def test_bare_unknown_scorer_fails_loudly_without_torch(monkeypatch, tmp_path, capsys):
    """REGRESSION (Codex round-10 P2, sibling): a bare unknown token must also
    fail with rc=3 on the model-free CI path (torch absent), not be misdiagnosed
    as a missing dependency (rc=2). Pins that the scorer-arg guard is reachable
    before the heavy torch check."""
    _hide_torch(monkeypatch)
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    rc = ss.main([str(target), "--scorer", "not_a_real_model"])
    assert rc == 3, "torch-absent CI must refuse a bad scorer (rc 3), not rc 2"
    err = capsys.readouterr().err
    assert "does not resolve" in err
    assert "surprisal tier" not in err


def test_valid_scorer_without_torch_still_reports_missing_tier(
    monkeypatch, tmp_path, capsys
):
    """Ordering must NOT regress the missing-torch hint: a VALID scorer with
    torch absent must still reach the torch gate and return rc=2 with the
    surprisal-tier install hint (the scorer validation passes, then the
    dependency check fires). This guards against over-correcting the reorder."""
    _hide_torch(monkeypatch)
    target = tmp_path / "t.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")
    rc = ss.main([str(target), "--scorer", "gpt2"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "surprisal tier" in err
    assert "pip install" in err
    assert "does not resolve" not in err
    assert "Traceback" not in err


# ============================================================
# 13. Capabilities entry present (drift linter)
# ============================================================


def test_capabilities_entry_present():
    tools_dir = _REPO_ROOT / "tools"
    sys.path.insert(0, str(tools_dir))
    import check_capabilities_drift as drift  # type: ignore

    report = drift.check_drift()
    assert report.passed, (
        "capabilities drift detected:\n"
        + "\n".join(v.render() for v in report.violations)
    )
    manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
    entry = next(
        (e for e in manifest["entries"] if e.get("id") == "structural_shuffle_audit"),
        None,
    )
    assert entry is not None, "structural_shuffle_audit missing from manifest"
    assert entry["surface"] == "discrimination_structural_shuffle"
    assert entry["status"] == "heuristic"
    assert entry["handoff"] == "experimental"
    compute = entry["compute"]
    assert compute["tier"] == "surprisal"
    assert compute["length_floor_words"] == 50
    assert "cost_note" in compute and compute["cost_note"]
    deps = entry["dependencies"]["python"]
    assert "transformers" in deps and "torch" in deps and "spacy" in deps


# ============================================================
# 14-15. Separation guard + Binoculars orthogonality (static import scan)
# ============================================================


_SEPARATION_SET = frozenset({
    "fitness", "setec_signals", "loop", "qlora", "reviser", "cosplay",
    "splits", "provenance",
})


def _imported_module_roots(py_path: Path) -> set[str]:
    """Top-level module names imported by a script (static AST scan)."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_separation_guard():
    roots = _imported_module_roots(_SCRIPTS / "structural_shuffle_audit.py")
    leaked = roots & _SEPARATION_SET
    assert not leaked, f"structural_shuffle_audit imports separation-set modules: {leaked}"


def test_orthogonal_to_binoculars():
    # Imports nothing from binoculars_audit.
    roots = _imported_module_roots(_SCRIPTS / "structural_shuffle_audit.py")
    assert "binoculars_audit" not in roots
    # No Binoculars / cross-perplexity field leaks into results.
    results = ss.audit(
        "x", backend=StubBackend(), injected_perplexities=(2.0, 3.0, 4.0),
    )
    keys = set(_walk_keys(results))
    for forbidden in (
        "binoculars_score", "binoculars_B", "cross_perplexity",
        "perplexity_ratio", "observer", "scorer",
    ):
        assert forbidden not in keys, f"Binoculars field {forbidden!r} leaked"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
