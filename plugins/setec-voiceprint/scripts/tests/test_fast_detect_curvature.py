"""Tests for ``fast_detect_curvature.py``.

Pin the spec contract (specs/03-fast-detectgpt-curvature.md):

  * envelope shape + correct task_surface (discrimination_curvature),
  * no default threshold and no verdict band,
  * claim license refuses an AI/human verdict,
  * curvature deterministic with a fixed seed + stub LM,
  * the statistic is computed orthogonally (no Binoculars / surprisal reuse),
  * graceful clean install hint when torch is absent,
  * the capabilities manifest carries the fast_detect_curvature entry.

No real model loads and no GPU: a deterministic stub backend supplies the
per-position conditional log-probs via an injected ``score_fn``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
sys.path.insert(0, str(_SCRIPTS))

import fast_detect_curvature as fdc  # noqa: E402


# ============================================================
# Deterministic stub backend + score_fn
# ============================================================


class StubBackend:
    """Mocks the audit-facing surface of SurprisalBackend: ``model_id``
    and ``identifier_block()``. The per-position conditional log-probs are
    supplied by the injected ``score_fn`` instead of a real model, so no
    model loads."""

    def __init__(self, model_id: str = "stub-model"):
        self.model_id = model_id
        self.revision = None
        self._alias = model_id

    def identifier_block(self):
        return {
            "id": self.model_id,
            "revision": None,
            "alias": self._alias,
            "deterministic_mode": True,
            "method": "stub",
            "dtype_requested": "auto",
            "dtype_loaded": "fp32",
        }


def _deterministic_score_fn(per_position):
    """Return a score_fn that ignores the model/text/seed and yields a
    fixed per-position ``(actual_log_prob, sampled_log_probs)`` series."""

    def score(model, text, *, n_samples, seed):
        return list(per_position)

    return score


def _seeded_sampling_score_fn(dists, next_token_ids):
    """A score_fn that actually samples (seeded) from supplied per-position
    distributions — exercises that a fixed seed yields a stable score even
    when sampling is involved. ``dists[t]`` is a list of log-probs (nats)
    over a small vocab; ``next_token_ids[t]`` is the actual token at t+1."""
    import math
    import random

    def score(model, text, *, n_samples, seed):
        rng = random.Random(seed)
        positions = []
        for t, dist in enumerate(dists):
            actual_lp = dist[next_token_ids[t]]
            weights = [math.exp(lp) for lp in dist]
            sampled_ids = rng.choices(
                range(len(dist)), weights=weights, k=n_samples,
            )
            sampled = [dist[i] for i in sampled_ids]
            positions.append((actual_lp, sampled))
        return positions

    return score


def _uniform_positions(actual_lp, sampled_values, n=100):
    """n positions each with the same actual log-prob + the same sampled
    set. Useful for analytic-curvature assertions."""
    return [(actual_lp, list(sampled_values)) for _ in range(n)]


# ============================================================
# test_envelope_shape
# ============================================================


def test_envelope_shape():
    """Validates; correct task_surface + required results keys."""
    model = StubBackend("scoring-model")
    score_fn = _deterministic_score_fn(
        _uniform_positions(-1.0, [-2.0, -3.0, -4.0, -5.0], n=100)
    )
    results = fdc.audit("text " * 200, model=model, score_fn=score_fn)
    envelope = fdc.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
        include_per_position=True,
    )

    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "discrimination_curvature"
    assert envelope["tool"] == "fast_detect_curvature"
    assert envelope["available"] is True
    assert envelope["claim_license"]["task_surface"] == "discrimination_curvature"
    assert envelope["target"]["words"] == 500

    r = envelope["results"]
    # Spec-mandated keys under results.
    assert r["model_id"] == "scoring-model"
    assert "curvature_score" in r
    assert isinstance(r["curvature_score"], float)
    assert r["n_samples"] == fdc.DEFAULT_N_SAMPLES
    assert r["n_tokens"] == 100
    assert "per_position" in r  # optional — present because requested
    assert len(r["per_position"]) == 100


def test_envelope_omits_per_position_by_default():
    """per_position is optional; not emitted unless requested."""
    model = StubBackend()
    score_fn = _deterministic_score_fn(
        _uniform_positions(-1.0, [-2.0, -3.0], n=60)
    )
    results = fdc.audit("x", model=model, score_fn=score_fn)
    envelope = fdc.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    assert "per_position" not in envelope["results"]


# ============================================================
# test_no_default_threshold_no_band
# ============================================================


def test_no_default_threshold_no_band():
    """Output carries no verdict/band absent thresholds, and no shipped
    threshold constant exists in the module."""
    model = StubBackend()
    score_fn = _deterministic_score_fn(
        _uniform_positions(-1.0, [-2.0, -3.0, -4.0], n=100)
    )
    results = fdc.audit("x", model=model, score_fn=score_fn)
    envelope = fdc.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    r = envelope["results"]
    # No verdict / band fields at all.
    assert "verdict_band" not in r
    assert "verdict" not in r
    assert "band" not in r
    # No thresholds anywhere in results.
    assert "thresholds" not in r
    assert "threshold_low" not in r
    assert "threshold_high" not in r
    # The module ships NO default threshold constant.
    assert not hasattr(fdc, "DEFAULT_THRESHOLD_LOW")
    assert not hasattr(fdc, "DEFAULT_THRESHOLD_HIGH")
    # The uncalibrated posture is surfaced explicitly.
    assert "no_calibrated_thresholds_supplied" in r["caveats"]
    # claim_license comparison_set records threshold=None.
    assert envelope["claim_license"]["comparison_set"]["threshold"] is None


# ============================================================
# test_claim_license_refuses_verdict
# ============================================================


def test_claim_license_refuses_verdict():
    """The claim license must license the curvature statistic and REFUSE
    an AI/human label absent operator thresholds; it must note the
    in-distribution caveat + paraphrase sensitivity."""
    model = StubBackend("M")
    score_fn = _deterministic_score_fn(
        _uniform_positions(-1.0, [-2.0, -3.0], n=100)
    )
    results = fdc.audit("x", model=model, score_fn=score_fn)
    envelope = fdc.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    cl = envelope["claim_license"]

    # Licenses the curvature statistic under model M.
    assert "curvature" in cl["licenses"].lower()
    assert "not a verdict" in cl["licenses"].lower()

    dnl = cl["does_not_license"].lower()
    # Refuses a binary AI/human verdict absent operator thresholds.
    assert "verdict" in dnl
    assert "threshold" in dnl
    # Names the spec-required caveats.
    assert "in-distribution" in dnl
    assert "paraphrase" in dnl

    # The surface is registered in claim_license's label map (so the
    # rendered block doesn't fall back to the raw surface string).
    import claim_license as cl_mod
    assert "discrimination_curvature" in cl_mod.TASK_SURFACE_LABELS

    # References Fast-DetectGPT / Bao et al.
    assert any("Bao et al" in r for r in cl["references"])
    assert any("Fast-DetectGPT" in r for r in cl["references"])


# ============================================================
# test_curvature_deterministic_with_seed
# ============================================================


def test_curvature_deterministic_with_seed():
    """Fixed seed + stub LM ⇒ stable score. Run twice with the same seed
    through the actual seeded sampling path; the score must be identical."""
    import math
    # A small 4-token vocab; per-position log-probs (nats) of a slightly
    # peaked categorical. Actual next token deliberately varies.
    raw = [0.5, 0.2, 0.2, 0.1]
    total = sum(raw)
    dist = [math.log(p / total) for p in raw]
    dists = [list(dist) for _ in range(80)]
    next_token_ids = [(t % 4) for t in range(80)]

    score_fn = _seeded_sampling_score_fn(dists, next_token_ids)
    model = StubBackend()

    r1 = fdc.audit("x", model=model, score_fn=score_fn, seed=1234, n_samples=500)
    r2 = fdc.audit("x", model=model, score_fn=score_fn, seed=1234, n_samples=500)
    assert r1["curvature_score"] == r2["curvature_score"]
    assert r1["curvature_score"] is not None

    # A different seed generally yields a different sampled reference, so
    # the score should differ (guards against the seed being ignored).
    r3 = fdc.audit("x", model=model, score_fn=score_fn, seed=9999, n_samples=500)
    assert r3["curvature_score"] != r1["curvature_score"]


def test_curvature_math_matches_closed_form():
    """The headline z-score equals the closed-form value on a hand-built
    per-position series, independent of any sampling."""
    import math
    # 100 identical positions: actual_lp = -1.0; sampled = {-2.0, -4.0}
    # → mu_t = -3.0, var_t = 1.0. Sum over 100 positions:
    #   actual_sum = -100, mu_sum = -300, var_sum = 100
    #   curvature = (-100 - (-300)) / sqrt(100) = 200 / 10 = 20.0
    positions = _uniform_positions(-1.0, [-2.0, -4.0], n=100)
    stats = fdc.curvature_from_positions(positions)
    assert math.isclose(stats["curvature_score"], 20.0, rel_tol=1e-9)
    assert stats["n_tokens"] == 100


def test_curvature_none_when_reference_variance_degenerate():
    """When every sampled distribution is a point mass (zero variance),
    the z-score is undefined and reported as None with a caveat."""
    positions = _uniform_positions(-1.0, [-2.0, -2.0, -2.0], n=80)
    model = StubBackend()
    results = fdc.audit(
        "x", model=model, score_fn=_deterministic_score_fn(positions),
    )
    assert results["curvature_score"] is None
    assert "reference_variance_degenerate_curvature_unavailable" in results["caveats"]


# ============================================================
# test_orthogonal_statistic
# ============================================================


def test_orthogonal_statistic():
    """The curvature must be computed independently of any Binoculars
    cross-perplexity number or DivEye surprisal field — it reads only the
    single model's conditional log-probs."""
    model = StubBackend()
    score_fn = _deterministic_score_fn(
        _uniform_positions(-1.0, [-2.0, -4.0], n=100)
    )
    results = fdc.audit("x", model=model, score_fn=score_fn)

    # No Binoculars / cross-perplexity fields leaked into results.
    for forbidden in (
        "perplexity_ratio",
        "cross_perplexity_log_nats",
        "scorer_log_perplexity_nats",
        "observer",
        "scorer",
        "tokenizers_compatible",
    ):
        assert forbidden not in results, (
            f"Binoculars field {forbidden!r} leaked into curvature results"
        )

    # No DivEye-style surprisal-moment fields either.
    for forbidden in (
        "surprisal_mean",
        "surprisal_sd",
        "surprisal_acf_lag1",
        "surprisal_series",
    ):
        assert forbidden not in results

    # The audit takes a SINGLE model (no observer parameter), and the
    # public audit signature has no scorer/observer pair.
    import inspect
    params = inspect.signature(fdc.audit).parameters
    assert "model" in params
    assert "scorer" not in params
    assert "observer" not in params

    # The score equals the closed-form curvature computed purely from the
    # per-position records (200/10 = 20.0) — i.e. nothing else fed in.
    assert abs(results["curvature_score"] - 20.0) < 1e-9
    assert results["score_version"] == fdc.SCORE_VERSION


# ============================================================
# test_missing_torch_graceful
# ============================================================


def test_missing_torch_graceful(monkeypatch, tmp_path, capsys):
    """When torch is absent, main() returns a clean dependency-style
    install hint (no traceback) rather than crashing."""
    target = tmp_path / "target.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    rc = fdc.main([str(target)])
    assert rc == 2
    err = capsys.readouterr().err
    # Clean install hint, not a traceback.
    assert "surprisal tier" in err
    assert "pip install" in err
    assert "Traceback" not in err


# ============================================================
# test_capabilities_entry_present
# ============================================================


def test_capabilities_entry_present():
    """The drift linter passes and the manifest carries the
    fast_detect_curvature entry with the spec'd shape."""
    tools_dir = _REPO_ROOT / "tools"
    sys.path.insert(0, str(tools_dir))
    import check_capabilities_drift as drift  # type: ignore

    report = drift.check_drift()
    assert report.passed, (
        "capabilities drift detected:\n"
        + "\n".join(v.render() for v in report.violations)
    )

    # The manifest entry exists with the spec'd fields.
    manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
    entry = next(
        (e for e in manifest["entries"] if e.get("id") == "fast_detect_curvature"),
        None,
    )
    assert entry is not None, "fast_detect_curvature missing from capabilities.yaml"
    assert entry["surface"] == "discrimination_curvature"
    assert entry["status"] == "literature_anchored"
    assert entry["handoff"] == "experimental"
    compute = entry["compute"]
    assert compute["tier"] == "surprisal"
    assert compute["length_floor_words"] == 50
    assert "cost_note" in compute and compute["cost_note"]
    deps = entry["dependencies"]["python"]
    assert "transformers" in deps
    assert "torch" in deps


# ============================================================
# CLI smoke (no model load)
# ============================================================


def test_cli_returns_nonzero_on_missing_target(tmp_path):
    rc = fdc.main([str(tmp_path / "nonexistent.txt")])
    assert rc == 1


# ============================================================
# Regression: chunk-safe actual-token log-prob (real-backend path)
# ============================================================


class _StubDistBackend:
    """Fake backend exposing ``score_text_with_distributions`` so the
    real-model integration point (``score_curvature_with_backend``) can be
    tested without a model.

    Mirrors ``SurprisalBackend.score_text_with_distributions``: ``surprisal_
    bits`` and ``log_probs_nats`` are aligned 1:1 (the backend ``.extend()``s
    them in lockstep per chunk), while ``token_ids`` is the FULL token
    sequence — which, on the chunked path (inputs longer than the model
    context window), is longer than ``len(log_probs_nats) + 1``.
    """

    model_id = "stub-dist-model"

    def __init__(self, surprisal_bits, log_probs_nats, token_ids):
        self._ret = (surprisal_bits, log_probs_nats, token_ids)

    def score_text_with_distributions(self, text):
        return self._ret


def test_score_curvature_reads_actual_lp_from_surprisal_series():
    """Regression for the chunked-input misalignment (P2).

    The actual next-token log-prob must come from the aligned surprisal
    series, not a positional ``token_ids[t + 1]`` lookup. Here ``token_ids``
    is longer than ``len(log_probs_nats) + 1`` (as the multi-chunk path
    returns) and ``token_ids[t + 1]`` deliberately points at a DIFFERENT
    vocab entry than the true actual token. The old code read those wrong
    log-probs silently; the fix reads ``-surprisal_bits * ln 2``.
    """
    import math

    ln2 = math.log(2.0)
    # Two scored positions over a 4-token vocab.
    dist0 = [math.log(p) for p in (0.5, 0.25, 0.125, 0.125)]
    dist1 = [math.log(p) for p in (0.25, 0.5, 0.125, 0.125)]
    log_probs_nats = [dist0, dist1]
    # True actual tokens: index 0 at pos 0, index 1 at pos 1 (each p=0.5 ->
    # exactly 1 bit). surprisal_bits is aligned 1:1 with log_probs_nats.
    surprisal_bits = [1.0, 1.0]
    # FULL token sequence, longer than len(log_probs_nats) + 1 (= 3) to model
    # the per-chunk drop; token_ids[t + 1] points at the WRONG vocab entries.
    token_ids = [0, 2, 3, 99, 99]

    backend = _StubDistBackend(surprisal_bits, log_probs_nats, token_ids)
    positions = fdc.score_curvature_with_backend(
        backend, "ignored", n_samples=8, seed=1,
    )

    assert len(positions) == 2
    # Correct: actual_lp == -surprisal_bits[t] * ln2 == log p(true token).
    assert positions[0][0] == pytest.approx(-surprisal_bits[0] * ln2)
    assert positions[1][0] == pytest.approx(-surprisal_bits[1] * ln2)
    assert positions[0][0] == pytest.approx(dist0[0])  # true token at pos 0
    assert positions[1][0] == pytest.approx(dist1[1])  # true token at pos 1
    # And NOT the buggy token_ids[t + 1] lookups (dist0[2] / dist1[3]).
    assert positions[0][0] != pytest.approx(dist0[token_ids[1]])
    assert positions[1][0] != pytest.approx(dist1[token_ids[2]])


def test_score_curvature_rejects_misaligned_series():
    """A backend whose surprisal/distribution series differ in length is a
    contract violation -> SurprisalBackendError, not a silent bad score."""
    import math

    backend = _StubDistBackend(
        surprisal_bits=[1.0, 1.0],                       # length 2
        log_probs_nats=[[math.log(0.5), math.log(0.5)]],  # length 1
        token_ids=[0, 1, 2],
    )
    with pytest.raises(fdc.SurprisalBackendError):
        fdc.score_curvature_with_backend(backend, "x", n_samples=4, seed=0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
