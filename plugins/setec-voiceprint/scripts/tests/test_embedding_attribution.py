"""Tests for embedding_attribution.py (the HIATUS glass-box explanation layer).

Torch-free: the LUAR cosine + named features + the attribution model are supplied
via ``--inputs-json`` (the explicit injected path) or a monkeypatched
``compute_inputs`` seam. The central contract is the DESCRIPTIVE / no-verdict
POSTURE plus the faithful (fitted) explained+residual decomposition with the
explained_fraction gated behind the calibration discipline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import embedding_attribution as ea  # type: ignore

# An injected pair with an explicit attribution model (declared-input fit, D3
# `injected_model`). weights × per-feature similarity = signed contribution.
INJECTED_MODEL = {
    "weights": {
        "burstiness_B": 0.20,
        "mattr": 0.10,
        "mtld": 0.05,
        "function_word_ratio": 0.15,
        "mean_dependency_distance": 0.10,
    },
    "intercept": 0.0,
}
INPUTS = {
    "cosine": 0.62,
    "features": {
        "burstiness_B": [0.40, 0.41],            # close -> sim high
        "mattr": [0.70, 0.70],                   # identical -> sim 1
        "mtld": [90, 92],                        # close
        "function_word_ratio": [0.45, 0.46],     # close
        "mean_dependency_distance": [2.0, 3.6],  # |Δ|=1.6 > scale 0.8 -> sim 0 -> divergent
    },
    "attribution_model": INJECTED_MODEL,
}


def _write(tmp_path, obj, name="inputs.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _run_injected(tmp_path, inputs, *args):
    target = tmp_path / "t.txt"
    target.write_text("x", encoding="utf-8")
    out = tmp_path / "out.json"
    rc = ea.main([str(target), "--inputs-json", str(_write(tmp_path, inputs)),
                  "--out", str(out), "--out-md", str(tmp_path / "out.md"), *args])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _results(env):
    return env.get("results", env)


# --------------------------------------------------------------------------
# AC-1 / AC-2 — surface exists, builds, runs torch-free over injected inputs
# --------------------------------------------------------------------------

def test_surface_is_registered_and_builds():
    # AC-1: the surface must be in VALID_TASK_SURFACES (the label fragment) or
    # build_output raises ValueError and M1 literally does not build.
    from output_schema import VALID_TASK_SURFACES  # type: ignore
    assert "embedding_attribution" in VALID_TASK_SURFACES
    assert ea.TASK_SURFACE == "embedding_attribution"


def test_injected_m1_path_is_ci_runnable(tmp_path):
    rc, env = _run_injected(tmp_path, INPUTS)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    assert r["inputs_source"] == "injected"
    assert r["luar_cosine"] == 0.62
    assert r["calibration_status"] == "uncalibrated"
    # injected-run warning present
    assert any("injected" in w for w in env.get("warnings", []))


def test_module_imports_without_torch_transformers():
    # Importing the module + running an injected decomposition must not pull in
    # torch/transformers — the M1 path is pure Python + numpy. Checked in a CLEAN
    # subprocess (torch may already be in this process's sys.modules from another
    # test / the env, which would make an in-process check environment-dependent).
    import subprocess
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import embedding_attribution as ea;"
        "names, sims = ea._feature_vector({'mattr':[0.7,0.7]});"
        "rows = ea.feature_attribution(names, sims, [0.1]);"
        "ea.decompose(0.5, rows, 'injected_model', {'weights':{'mattr':0.1}});"
        "assert 'torch' not in sys.modules, 'torch leaked';"
        "assert 'transformers' not in sys.modules, 'transformers leaked';"
        "print('clean')"
    ) % str(SCRIPTS)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "clean" in out.stdout


# --------------------------------------------------------------------------
# AC-3 — Latent-Space Interpretation: signed named contributions
# --------------------------------------------------------------------------

def test_feature_attribution_signed_and_named(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    fa = _results(env)["feature_attribution"]
    assert fa, "must have one row per present named feature"
    for row in fa:
        assert set(row) == {"feature", "feature_similarity", "contribution", "direction"}
        assert row["direction"] in ("shared", "divergent")
        assert row["contribution"] is not None
        assert isinstance(row["contribution"], (int, float))
        import math
        assert math.isfinite(row["contribution"])
    by = {r["feature"]: r for r in fa}
    # mattr identical -> sim 1 -> shared; dependency-distance far -> sim 0 -> divergent
    assert by["mattr"]["direction"] == "shared"
    assert by["mean_dependency_distance"]["direction"] == "divergent"
    # signed contribution = weight × similarity (the injected_model fit)
    assert by["mattr"]["contribution"] == pytest.approx(0.10 * 1.0, abs=1e-6)
    assert by["mean_dependency_distance"]["contribution"] == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------
# AC-4 — Residualized decomposition output
# --------------------------------------------------------------------------

def test_decomposition_shape(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    deco = _results(env)["decomposition"]
    for k in ("explained_similarity", "residual_similarity",
              "explained_fraction", "residual_fraction", "fit_provenance"):
        assert k in deco
    assert 0.0 <= deco["explained_fraction"] <= 1.0
    assert 0.0 <= deco["residual_fraction"] <= 1.0
    fp = deco["fit_provenance"]
    assert fp["fit_source"] in ("injected_model", "corpus_fit", "unfit")
    assert fp["fit_source"] == "injected_model"
    assert fp["model_id"] == "rrivera1849/LUAR-MUD"


# --------------------------------------------------------------------------
# AC-5 — Faithfulness. The reconstruction invariant (definitional) PLUS a REAL
# faithfulness check: the named-feature explanation must PREDICT a held-out
# cosine with non-trivial fit quality (not the algebraic identity).
# --------------------------------------------------------------------------

def test_reconstruction_invariant_holds(tmp_path):
    # explained + residual == cosine within 1e-6 (definitional; pinned on the fixture).
    _, env = _run_injected(tmp_path, INPUTS)
    deco = _results(env)["decomposition"]
    assert (deco["explained_similarity"] + deco["residual_similarity"]
            == pytest.approx(_results(env)["luar_cosine"], abs=1e-6))


def test_real_faithfulness_nontrivial_fit_quality(tmp_path):
    # The P2 fold: AC-5's faithfulness must be a REAL test, not the algebraic
    # identity. A corpus_fit where the cosine genuinely tracks a named-feature
    # similarity must produce a non-trivial R² (the explanation PREDICTS the
    # cosine on held-out-style rows), and the fit must not be a degenerate R²≈0.
    pytest.importorskip("numpy")
    # Build a corpus where cosine is a clear linear function of the burstiness
    # similarity (+ noise-free other features held identical) -> high R².
    corpus = []
    for i in range(16):
        b1 = 0.40 + (i % 4) * 0.05
        # cosine increases as burstiness similarity decreases (a real signal)
        cos = 0.90 - (i % 4) * 0.12
        corpus.append({"cosine": cos, "features": {
            "burstiness_B": [0.40, b1], "mattr": [0.7, 0.7], "mtld": [90, 90],
            "function_word_ratio": [0.45, 0.45], "mean_dependency_distance": [2.0, 2.0]}})
    inputs = dict(INPUTS)
    inputs = {"cosine": 0.62, "features": INPUTS["features"],
              "attribution_model": {"corpus": corpus}}
    _, env = _run_injected(tmp_path, inputs)
    deco = _results(env)["decomposition"]
    fp = deco["fit_provenance"]
    assert fp["fit_source"] == "corpus_fit"
    assert fp["n_corpus_rows"] == 16
    # NON-TRIVIAL fit quality: the named feature genuinely predicts the cosine.
    assert "r2" in fp
    assert fp["r2"] > 0.5, "the explanation must actually predict the cosine, not be decorative"


def test_corpus_fit_intercept_dominated_tracks_reported_r2(tmp_path):
    # The P2 fold: a corpus_fit's EXPLAINED part must use the SAME
    # (intercept-inclusive) OLS whose R² is reported. Build an intercept-DOMINATED
    # corpus — cosine ~0.70 (the normal LUAR same-author regime: high cosine) that
    # the OLS fits PERFECTLY (r2≈1.0) with the level carried almost entirely by the
    # intercept (~0.70) and only a small per-feature slope. Before the fix the
    # intercept was DROPPED from `explained`, so explained≈0.01,
    # residual_fraction≈0.98 and the band read `largely-unnamed` while r2≈1.0 — the
    # reported coverage flatly contradicted the reported fit (the inversion the
    # finding names). After the fix the intercept rides `explained`, so a perfect
    # fit reads `well-named`.
    pytest.importorskip("numpy")
    scales = dict(ea.NAMED_FEATURES)
    bscale = scales["burstiness_B"]

    def feats(delta):
        # burstiness carries the cosine VARIATION (a low, varying sim band); every
        # other feature is pinned DIVERGENT (sim 0) so it cannot absorb the level —
        # forcing the ~0.70 constant into the intercept.
        return {"burstiness_B": [0.40, 0.40 + delta],
                "mattr": [0.30, 0.95], "mtld": [40, 160],
                "function_word_ratio": [0.20, 0.95],
                "mean_dependency_distance": [1.0, 5.0]}

    deltas = [0.24, 0.25, 0.26, 0.27]  # burstiness sims ~0.20..0.10 (low, varying)
    corpus = []
    for i in range(16):
        d = deltas[i % 4]
        sim_b = ea.ce.feature_similarity(0.40, 0.40 + d, bscale)
        cos = round(0.70 + 0.10 * sim_b, 6)  # cosine genuinely tracks burstiness sim
        corpus.append({"cosine": cos, "features": feats(d)})
    # The target pair sits in the SAME regime (cosine high, features mostly divergent).
    target_delta = 0.255
    cos_t = round(0.70 + 0.10 * ea.ce.feature_similarity(0.40, 0.40 + target_delta, bscale), 6)
    inputs = {"cosine": cos_t, "features": feats(target_delta),
              "attribution_model": {"corpus": corpus}}
    _, env = _run_injected(tmp_path, inputs)
    r = _results(env)
    deco = r["decomposition"]
    fp = deco["fit_provenance"]
    assert fp["fit_source"] == "corpus_fit"
    # The OLS fits the cosine essentially perfectly.
    assert fp["r2"] > 0.99, "intercept-dominated corpus should fit near-perfectly"
    # The intercept rides `explained`: explained ≈ cosine, so residual ≈ 0.
    # (Pre-fix this was ~0.01 — the dropped intercept — and the assert below failed.)
    assert deco["explained_similarity"] == pytest.approx(cos_t, abs=0.02)
    assert deco["residual_fraction"] < 0.10, (
        "the fitted intercept must be IN the explained part — a near-perfect fit "
        "cannot report a large residual")
    # The reported coverage tracks the reported fit quality.
    assert r["coverage_band"] == "well-named", (
        "coverage must read well-named when r2≈1.0 (P2: the explained part and "
        "the reported R² describe ONE intercept-inclusive fit)")


def test_no_naninf_leaks_in_results(tmp_path):
    # AC-5: build_output's R4 gate (validate_results_bounds) passes — i.e. no
    # NaN/inf reached the envelope (build_output would have raised otherwise).
    rc, env = _run_injected(tmp_path, INPUTS)
    assert rc == 0 and env["available"] is True
    blob = json.dumps(env)
    assert "NaN" not in blob and "Infinity" not in blob


# --------------------------------------------------------------------------
# AC-6 — coverage band names the measured property
# --------------------------------------------------------------------------

def test_coverage_band_values_and_meaning(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    band = _results(env)["coverage_band"]
    assert band in ("well-named", "mostly-named", "largely-unnamed", "indeterminate")
    # no band names an inference target
    for forbidden in ("ai", "suspicious", "authentic"):
        assert forbidden not in band


def test_coverage_band_derivation_from_residual_fraction():
    assert ea.coverage_band({"residual_fraction": 0.10}) == "well-named"
    assert ea.coverage_band({"residual_fraction": 0.40}) == "mostly-named"
    assert ea.coverage_band({"residual_fraction": 0.80}) == "largely-unnamed"
    assert ea.coverage_band({"residual_fraction": None}) == "indeterminate"


# --------------------------------------------------------------------------
# AC-7 — the residual is NOT a verdict (no threshold/flag keys touch it)
# --------------------------------------------------------------------------

def test_residual_is_not_a_verdict(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    r = _results(env)

    def walk_keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from walk_keys(v)
        elif isinstance(obj, (list, tuple)):
            for x in obj:
                yield from walk_keys(x)

    for k in walk_keys(r):
        lk = str(k).lower()
        assert not lk.endswith("_threshold"), k
        assert not lk.endswith("_flag"), k
        assert not lk.endswith("_verdict"), k
        assert not lk.startswith("is_"), k


# --------------------------------------------------------------------------
# AC-8 — claim license refuses verdict; cites both arXiv ids
# --------------------------------------------------------------------------

def test_claim_license_refuses_verdict(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    dnl = env["claim_license"]["does_not_license"].lower()
    assert "same-author" in dnl
    assert "different-author" in dnl
    assert "ai-generated or human-written" in dnl
    assert "authenticity" in dnl
    assert "suspicion" in dnl
    assert "lens" in dnl and "not ground truth" in dnl
    assert "no threshold" in dnl
    assert "injected" in dnl
    lic = env["claim_license"]["licenses"].lower()
    assert "2409.07072" in lic and "2510.05362" in lic


# --------------------------------------------------------------------------
# AC-9 — recursive no-verdict walk over the entire results tree
# --------------------------------------------------------------------------

def test_no_verdict_recursive_walk(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    r = _results(env)
    forbidden = {
        "same_author", "different_author", "verdict", "is_ai", "is_human",
        "label", "score", "selection", "decision", "suspicion", "authenticity",
    }

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in forbidden, f"forbidden key at depth: {k}"
                assert not str(k).startswith("authorship"), k
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for x in obj:
                walk(x)

    walk(r)


# --------------------------------------------------------------------------
# AC-10 — anti-Goodhart disjointness: the basis is the reused named features
# --------------------------------------------------------------------------

def test_attribution_basis_is_reused_named_features(tmp_path):
    import cosine_explanation as ce  # type: ignore
    # The attribution basis is EXACTLY cosine_explanation's curated set, not
    # re-derived from the LUAR embedding.
    assert ea.NAMED_FEATURES is ce.NAMED_FEATURES
    _, env = _run_injected(tmp_path, INPUTS)
    names = {r["feature"] for r in _results(env)["feature_attribution"]}
    assert names <= {n for n, _ in ce.NAMED_FEATURES}
    assert names == set(INPUTS["features"])  # all present features attributed


# --------------------------------------------------------------------------
# AC-12 — live-LUAR seam is M2-gated
# --------------------------------------------------------------------------

def test_computed_path_via_monkeypatched_seam(tmp_path, monkeypatch):
    def _stub(target, comparison):
        return 0.62, INPUTS["features"], INJECTED_MODEL
    monkeypatch.setattr(ea, "compute_inputs", _stub)
    target = tmp_path / "t.txt"; target.write_text("x", encoding="utf-8")
    comp = tmp_path / "c.txt"; comp.write_text("y", encoding="utf-8")
    out = tmp_path / "c.json"
    ea.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert _results(env)["inputs_source"] == "computed"


def test_computed_path_without_luar_is_missing_dependency(tmp_path, monkeypatch):
    import voice_fingerprint as vf  # type: ignore
    def _no_tier(model, device=None):
        raise vf.VoiceFingerprintError("transformers not installed")
    monkeypatch.setattr(vf, "_load_encoder", _no_tier)
    target = tmp_path / "t.txt"; target.write_text("some real argument text here", encoding="utf-8")
    comp = tmp_path / "c.txt"; comp.write_text("a different argument text here", encoding="utf-8")
    out = tmp_path / "o.json"
    ea.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False
    assert "missing_dependency" in json.dumps(env)


def _torch_present() -> bool:
    try:
        import torch  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _torch_present(),
                    reason="live-LUAR smoke needs torch; M2 seam, never CI")
def test_live_luar_seam_smoke(tmp_path, monkeypatch):
    # M2 seam: when torch is present, the live path computes via the
    # monkeypatched encoder (the real encoder classes exist but are never
    # executed under the unit suite). This is the skipif(no torch) discipline.
    import numpy as np  # type: ignore
    import voice_fingerprint as vf  # type: ignore

    class _StubEnc:
        def encode(self, texts):
            return np.array([[float(len(t)), float(t.count("e")), 1.0] for t in texts])

    monkeypatch.setattr(vf, "_load_encoder", lambda model, device=None: _StubEnc())
    target = tmp_path / "t.txt"
    target.write_text("The harbor was quiet at dawn, because the tide turned.", encoding="utf-8")
    comp = tmp_path / "c.txt"
    comp.write_text("Terse. Clipped. Punchy prose. No flourish here at all.", encoding="utf-8")
    out = tmp_path / "o.json"
    rc = ea.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and env["available"] is True
    assert _results(env)["inputs_source"] == "computed"


# --------------------------------------------------------------------------
# AC-13 — degenerate input fails loud
# --------------------------------------------------------------------------

def test_no_inputs_is_bad_input(tmp_path):
    target = tmp_path / "t.txt"; target.write_text("x", encoding="utf-8")
    out = tmp_path / "o.json"
    ea.main([str(target), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_empty_features_is_bad_input(tmp_path):
    rc, env = _run_injected(tmp_path, {"cosine": 0.5, "features": {}})
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_malformed_injected_feature_is_bad_input(tmp_path):
    rc, env = _run_injected(tmp_path, {"cosine": 0.5, "features": {"mattr": ["a", "b"]}})
    assert env["available"] is False and "bad_input" in json.dumps(env)


@pytest.mark.parametrize("bad_cos", [float("nan"), float("inf"), float("-inf")])
def test_injected_nonfinite_cosine_is_bad_input(tmp_path, bad_cos):
    bad = dict(INPUTS)
    bad = {"cosine": bad_cos, "features": INPUTS["features"]}
    rc, env = _run_injected(tmp_path, bad)
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_bad_attribution_model_is_bad_input(tmp_path):
    bad = {"cosine": 0.5, "features": INPUTS["features"], "attribution_model": [1, 2, 3]}
    rc, env = _run_injected(tmp_path, bad)
    assert env["available"] is False and "bad_input" in json.dumps(env)


# --------------------------------------------------------------------------
# D3 / abstention — unfit & near-zero-cosine degeneracies abstain, never fabricate
# --------------------------------------------------------------------------

def test_unfit_abstains_indeterminate(tmp_path):
    # No attribution_model -> unfit -> indeterminate band, no fraction, but the
    # side-by-side + per-feature directions still survive.
    no_model = {"cosine": 0.62, "features": INPUTS["features"]}
    rc, env = _run_injected(tmp_path, no_model)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    assert r["decomposition"]["fit_provenance"]["fit_source"] == "unfit"
    assert r["decomposition"]["explained_fraction"] is None
    assert r["coverage_band"] == "indeterminate"
    # the descriptive side-by-side survives an unfit run
    assert r["named_feature_comparison"]
    assert all(row["contribution"] is None for row in r["feature_attribution"])


def test_near_zero_cosine_abstains_on_fraction(tmp_path):
    # The single-pair near-zero-cosine degeneracy: |cosine| < 1e-3 -> the
    # explained/|cosine| ratio is unstable -> abstain (band indeterminate), but
    # explained/residual SIMILARITIES are still reported.
    near_zero = {"cosine": 0.0005, "features": INPUTS["features"],
                 "attribution_model": INJECTED_MODEL}
    rc, env = _run_injected(tmp_path, near_zero)
    assert rc == 0 and env["available"] is True
    deco = _results(env)["decomposition"]
    assert deco["explained_fraction"] is None
    assert deco["residual_fraction"] is None
    assert "fraction_abstained" in deco
    assert deco["explained_similarity"] is not None  # similarities still reported
    assert _results(env)["coverage_band"] == "indeterminate"


def test_negative_cosine_explained_exactly_reports_full_coverage():
    # Codex P2: a NEGATIVE cosine the model reproduces EXACTLY (explained == cosine,
    # residual == 0) used to report 0% explained / 100% residual, because the fraction
    # divided by |cosine| while explained kept its sign (-0.8/0.8 = -1 -> clamped to 0).
    # The signed denominator reports it as fully explained.
    deco = ea.decompose(
        -0.8, [{"feature": "f", "contribution": -0.8}], "injected_model", {"intercept": 0.0})
    assert deco["explained_fraction"] == pytest.approx(1.0, abs=1e-6)
    assert deco["residual_fraction"] == pytest.approx(0.0, abs=1e-6)
    # ...but an explained part pointing the WRONG way (opposite sign to a negative
    # cosine) is still 0% explained, not spuriously positive.
    wrong = ea.decompose(
        -0.8, [{"feature": "f", "contribution": 0.2}], "injected_model", {"intercept": 0.0})
    assert wrong["explained_fraction"] == pytest.approx(0.0, abs=1e-6)
    assert wrong["residual_fraction"] == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------
# Operator decision — explained_fraction gated behind calibration; side-by-side
# is the default/headline view.
# --------------------------------------------------------------------------

def test_fraction_is_calibration_gated_and_side_by_side_is_default(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS)
    r = _results(env)
    # ships uncalibrated -> the numeric fraction is NOT presented as calibrated
    assert r["calibration_status"] == "uncalibrated"
    assert r["fraction_calibrated"] is False
    # the explained_fraction is carried (honest), but the headline is the
    # side-by-side agreement table
    assert "named_feature_comparison" in r and r["named_feature_comparison"]
    assert "divergent_features" in r
    # the explained_fraction is present but gated
    assert r["decomposition"]["explained_fraction"] is not None
