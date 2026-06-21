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
    a = ss.shuffle_sentences(sents, seed=0)
    b = ss.shuffle_sentences(sents, seed=0)
    assert a == b  # same seed -> same order
    # A different seed should (for >= 3 sentences) generally differ. Find one.
    assert any(ss.shuffle_sentences(sents, seed=s) != a for s in range(1, 12))


def test_shuffle_words_deterministic():
    a = ss.shuffle_words(_THREE_SENTENCES, seed=0)
    b = ss.shuffle_words(_THREE_SENTENCES, seed=0)
    assert a == b
    assert any(ss.shuffle_words(_THREE_SENTENCES, seed=s) != a for s in range(1, 12))


def test_shuffle_sentences_same_words():
    sents = ss.split_sentences(_THREE_SENTENCES)
    shuffled = ss.shuffle_sentences(sents, seed=3)
    assert sorted(shuffled.split()) == sorted(" ".join(sents).split())


def test_shuffle_words_same_words():
    shuffled = ss.shuffle_words(_THREE_SENTENCES, seed=3)
    assert sorted(shuffled.split()) == sorted(_THREE_SENTENCES.split())


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

    rc = ss.main([str(target)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "surprisal tier" in err
    assert "pip install" in err
    assert "Traceback" not in err


def test_cli_returns_nonzero_on_missing_target(tmp_path):
    assert ss.main([str(tmp_path / "nonexistent.txt")]) == 1


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
