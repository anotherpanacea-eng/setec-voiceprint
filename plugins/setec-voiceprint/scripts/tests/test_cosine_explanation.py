"""Tests for cosine_explanation.py (spec 27 M1).

Torch-free: the LUAR cosine + features are supplied via `--inputs-json` (the
explicit injected path) or a monkeypatched `compute_inputs` seam. The central
contract is the no-verdict / no-fabricated-number POSTURE.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cosine_explanation as ce  # type: ignore

# cosine HIGH (>=0.5): a near-identical feature -> high sim -> tracks; a far
# feature -> low sim -> diverges. Pins BOTH agreement directions at a high cosine.
INPUTS_HI = {
    "cosine": 0.80,
    "features": {
        "burstiness_B": [0.40, 0.41],            # |Δ|≈0.01, scale 0.30 -> sim high -> tracks
        "mattr": [0.70, 0.70],                   # identical -> sim 1 -> tracks
        "mtld": [90, 92],                        # close -> tracks
        "function_word_ratio": [0.45, 0.46],     # close -> tracks
        "mean_dependency_distance": [2.0, 3.6],  # |Δ|=1.6 > scale 0.8 -> sim 0 -> diverges
    },
}
# cosine LOW (<0.5): a near-identical feature now DIVERGES (sim high, cosine low);
# a far feature TRACKS (both low). The opposite direction.
INPUTS_LO = {
    "cosine": 0.20,
    "features": {
        "burstiness_B": [0.40, 0.40],            # identical -> sim 1 (high) -> diverges (cosine low)
        "mean_dependency_distance": [2.0, 4.0],  # far -> sim 0 (low) -> tracks (cosine low)
    },
}


def _write(tmp_path, obj):
    p = tmp_path / "inputs.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _run_injected(tmp_path, inputs, *args):
    target = tmp_path / "t.txt"
    target.write_text("x", encoding="utf-8")
    out = tmp_path / "out.json"
    rc = ce.main([str(target), "--inputs-json", str(_write(tmp_path, inputs)),
                  "--out", str(out), "--out-md", str(tmp_path / "out.md"), *args])
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _results(env):
    return env.get("results", env)


def test_side_by_side_shape(tmp_path):
    rc, env = _run_injected(tmp_path, INPUTS_HI)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    assert r["luar_cosine"] == 0.80
    rows = r["named_feature_comparison"]
    assert len(rows) == 5
    for row in rows:
        assert set(row) == {"feature", "target_value", "comparison_value",
                            "feature_similarity", "agreement"}
        assert row["agreement"] in ("tracks", "diverges")
    assert r["calibration_status"] == "uncalibrated"


def test_agreement_both_directions(tmp_path):
    # high cosine: identical mattr tracks, far dependency-distance diverges
    _, env = _run_injected(tmp_path, INPUTS_HI)
    rows = {x["feature"]: x for x in _results(env)["named_feature_comparison"]}
    assert rows["mattr"]["agreement"] == "tracks"
    assert rows["mean_dependency_distance"]["agreement"] == "diverges"
    assert _results(env)["divergent_features"] == ["mean_dependency_distance"]
    # low cosine: identical burstiness now DIVERGES, far dep-distance TRACKS
    _, env2 = _run_injected(tmp_path, INPUTS_LO)
    rows2 = {x["feature"]: x for x in _results(env2)["named_feature_comparison"]}
    assert rows2["burstiness_B"]["agreement"] == "diverges"
    assert rows2["mean_dependency_distance"]["agreement"] == "tracks"


def test_no_verdict_no_fabricated_number(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS_HI)
    r = _results(env)
    forbidden = {"same_author", "verdict", "is_ai", "score",
                 "explained_fraction", "residual_fraction"}
    for k in r:
        assert k not in forbidden, f"forbidden key: {k}"
        assert not k.startswith("authorship"), k
    # v1 ships NO fraction unless --fit-baseline
    assert "fit_baseline" not in r


def test_claim_license_refuses_verdict(tmp_path):
    _, env = _run_injected(tmp_path, INPUTS_HI)
    dnl = env["claim_license"]["does_not_license"].lower()
    assert "same-author" in dnl
    assert "authenticity" in dnl
    assert "lens" in dnl and "ground truth" in dnl
    assert "injected" in dnl


def test_injected_vs_computed_provenance(tmp_path, monkeypatch):
    _, env = _run_injected(tmp_path, INPUTS_HI)
    assert _results(env)["inputs_source"] == "injected"
    assert any("injected" in w for w in env.get("warnings", []))

    # computed path via the monkeypatched seam (no LUAR load)
    def _stub(target, comparison):
        return 0.80, INPUTS_HI["features"]
    monkeypatch.setattr(ce, "compute_inputs", _stub)
    target = tmp_path / "t.txt"; target.write_text("x", encoding="utf-8")
    comp = tmp_path / "c.txt"; comp.write_text("y", encoding="utf-8")
    out = tmp_path / "c.json"
    ce.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env2 = json.loads(out.read_text(encoding="utf-8"))
    assert _results(env2)["inputs_source"] == "computed"


def test_computed_path_without_luar_is_missing_dependency(tmp_path, monkeypatch):
    # #231 P1: an ABSENT style-embedding tier -> missing_dependency (deterministic: simulate the
    # encoder raising VoiceFingerprintError, rather than depending on whether transformers is here).
    import voice_fingerprint as vf  # type: ignore
    def _no_tier(model, device=None):
        raise vf.VoiceFingerprintError("transformers not installed")
    monkeypatch.setattr(vf, "_load_encoder", _no_tier)
    target = tmp_path / "t.txt"; target.write_text("some real argument text here", encoding="utf-8")
    comp = tmp_path / "c.txt"; comp.write_text("a different argument text here", encoding="utf-8")
    out = tmp_path / "o.json"
    ce.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False
    assert "missing_dependency" in json.dumps(env)


def test_computed_path_works_when_tier_present(tmp_path, monkeypatch):
    # #231 P1: the production path must actually COMPUTE when the tier is present (it previously
    # always raised RuntimeError). Stub only the encoder; the named features run for real.
    import numpy as np  # type: ignore
    import voice_fingerprint as vf  # type: ignore

    class _StubEnc:
        def encode(self, texts):
            return np.array([[float(len(t)), float(t.count("e")), 1.0] for t in texts])

    monkeypatch.setattr(vf, "_load_encoder", lambda model, device=None: _StubEnc())
    target = tmp_path / "t.txt"
    target.write_text("The harbor was quiet at dawn, because the tide turned; therefore boats came.",
                      encoding="utf-8")
    comp = tmp_path / "c.txt"
    comp.write_text("Terse. Clipped. Punchy prose. No flourish here at all, however brief.",
                    encoding="utf-8")
    out = tmp_path / "o.json"
    rc = ce.main([str(target), "--comparison", str(comp), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0 and env["available"] is True
    r = env["results"]
    assert r["inputs_source"] == "computed"
    assert r["named_feature_comparison"] and isinstance(r["luar_cosine"], float)


def test_malformed_injected_feature_is_bad_input(tmp_path):
    # #231 P2: a non-numeric injected feature value must be a clean bad_input, not a traceback.
    rc, env = _run_injected(tmp_path, {"cosine": 0.5, "features": {"mattr": ["a", "b"]}})
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_fit_baseline_unreadable_is_bad_input(tmp_path):
    # #231 P2: a REQUESTED --fit-baseline that can't be read/parsed is bad_input, not silently ignored.
    rc, env = _run_injected(tmp_path, INPUTS_HI, "--fit-baseline", str(tmp_path / "nope.json"))
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_fit_baseline_insufficient_corpus_warns(tmp_path):
    # #231 P2: a valid-but-unusable corpus (too few rows) surfaces a warning, not a silent no-op.
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"cosine": 0.5, "features": {}}]))   # 1 row, far too few
    rc, env = _run_injected(tmp_path, INPUTS_HI, "--fit-baseline", str(corpus))
    assert env["available"] is True
    assert "fit_baseline" not in env["results"]                # no fit computed
    assert "fit_baseline_warning" in env["results"]            # but NOT silent


def test_fit_baseline_malformed_rows_skipped_not_traceback(tmp_path):
    # Codex #231 P2: rows that are valid JSON but NOT objects (a bare number/string/list/
    # bool) — or whose `features` isn't a dict — must be SKIPPED, not `.get`'d into an
    # AttributeError. A corpus of only such junk is unusable -> warns, never tracebacks.
    junk = [42, "x", [1, 2], True, {"cosine": "nope", "features": {}},
            {"cosine": 0.5, "features": [1, 2]}]   # non-dict features
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(junk), encoding="utf-8")
    rc, env = _run_injected(tmp_path, INPUTS_HI, "--fit-baseline", str(corpus))
    assert env["available"] is True                        # ran cleanly, no traceback
    assert "fit_baseline" not in env["results"]            # nothing usable
    assert "fit_baseline_warning" in env["results"]        # surfaced, not silent


def test_fit_baseline_dict_row_nonnumeric_pair_skipped_not_traceback(tmp_path):
    # Codex #231 re-review: a dict row with numeric cosine but a non-numeric (["bad", 0.2]) or
    # non-finite ([NaN, 0.2]) feature pair must SKIP that row at float(pair[0]) — not raise
    # ValueError. Mixed with enough valid rows, the fit still runs and counts ONLY the valid rows.
    pytest.importorskip("numpy")

    def full(c, b1):
        return {"cosine": c, "features": {
            "burstiness_B": [0.40, b1], "mattr": [0.7, 0.7], "mtld": [90, 90],
            "function_word_ratio": [0.45, 0.45], "mean_dependency_distance": [2.0, 2.0]}}

    bad_pair = {"cosine": 0.5, "features": {
        "burstiness_B": ["bad", 0.2], "mattr": [0.7, 0.7], "mtld": [90, 90],
        "function_word_ratio": [0.45, 0.45], "mean_dependency_distance": [2.0, 2.0]}}
    nan_pair = {"cosine": 0.5, "features": {
        "burstiness_B": [float("nan"), 0.2], "mattr": [0.7, 0.7], "mtld": [90, 90],
        "function_word_ratio": [0.45, 0.45], "mean_dependency_distance": [2.0, 2.0]}}
    corpus = [full(0.9 - (i % 4) * 0.1, 0.40 + (i % 4) * 0.05) for i in range(10)] + [bad_pair, nan_pair]
    cpath = tmp_path / "corpus.json"
    cpath.write_text(json.dumps(corpus), encoding="utf-8")
    rc, env = _run_injected(tmp_path, INPUTS_HI, "--fit-baseline", str(cpath))
    assert env["available"] is True                        # no traceback
    fb = _results(env).get("fit_baseline")
    assert fb is not None and fb["n_corpus_rows"] == 10     # the 2 malformed rows skipped, not fatal


def test_no_inputs_is_bad_input(tmp_path):
    target = tmp_path / "t.txt"; target.write_text("x", encoding="utf-8")
    out = tmp_path / "o.json"
    ce.main([str(target), "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_empty_features_is_bad_input(tmp_path):
    rc, env = _run_injected(tmp_path, {"cosine": 0.5, "features": {}})
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_fit_baseline_emits_corpus_relative_split(tmp_path):
    pytest.importorskip("numpy")
    # a corpus where cosine ~ tracks the burstiness similarity
    corpus = []
    for i in range(12):
        b0 = 0.40
        b1 = 0.40 + (i % 4) * 0.05
        cos = 0.9 - (i % 4) * 0.1
        corpus.append({"cosine": cos, "features": {
            "burstiness_B": [b0, b1], "mattr": [0.7, 0.7], "mtld": [90, 90],
            "function_word_ratio": [0.45, 0.45], "mean_dependency_distance": [2.0, 2.0],
        }})
    cpath = tmp_path / "corpus.json"
    cpath.write_text(json.dumps(corpus), encoding="utf-8")
    rc, env = _run_injected(tmp_path, INPUTS_HI, "--fit-baseline", str(cpath))
    fb = _results(env).get("fit_baseline")
    assert fb is not None
    assert "fit_r2" in fb and "fit_residual" in fb
    assert abs(fb["fit_r2"] + fb["fit_residual"] - 1.0) < 1e-6


def test_feature_similarity_defined():
    assert ce.feature_similarity(0.4, 0.4, 0.3) == 1.0
    assert ce.feature_similarity(0.4, 0.7, 0.3) == pytest.approx(0.0, abs=1e-9)  # |Δ|≈scale -> 0
    assert ce.agreement(0.9, 0.8) == "tracks"            # both high
    assert ce.agreement(0.1, 0.8) == "diverges"          # split
    assert ce.agreement(0.1, 0.2) == "tracks"            # both low
