#!/usr/bin/env python3
"""Behavior tests for period_ruler (VP-RS1, Chronologic conformal ruler).

Model-free throughout: rows carry precomputed instrument scores, or a counting
stub scorer is injected. The real DeBERTa instruments are never loaded here.
"""

from __future__ import annotations

import io
import json
import random
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

import period_ruler as pr  # type: ignore  # noqa: E402

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "class", "classification", "decision",
    "score", "confidence", "rank", "prediction", "flag", "selection", "best", "top", "selected",
})


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _real_row(rng, i, *, year=None, n_words=None, length_effect=0.0):
    year = year if year is not None else rng.randint(1840, 1880)
    n_words = n_words if n_words is not None else rng.randint(20, 300)
    return {
        "id": f"r{i}", "year": year, "group": f"g{i % 12}", "text": _words(n_words) + f" {i}",
        # Instrument bias (-5 years) plus a length confound the ruler must cancel.
        "date_estimate_year": year - 5 + rng.gauss(0, 15) + length_effect * n_words,
        "p_synthetic": min(1.0, max(0.0, rng.betavariate(2, 6) + length_effect * n_words / 300)),
    }


def _reference(seed=0, n=1200, length_effect=0.0):
    rng = random.Random(seed)
    rows = [_real_row(rng, i, length_effect=length_effect) for i in range(n)]
    for r in rows:
        r["n_words"] = pr.count_words(r["text"])
    return rows


def _candidates(seed=1, n=80, *, shift_years=0.0, shift_p=0.0, n_words=None, length_effect=0.0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        r = _real_row(rng, 10_000 + i, n_words=n_words, length_effect=length_effect)
        out.append({"id": f"c{i}", "year": r["year"], "text": r["text"],
                    "n_words": pr.count_words(r["text"]),
                    "date_estimate_year": r["date_estimate_year"] + shift_years,
                    "p_synthetic": min(1.0, r["p_synthetic"] + shift_p)})
    return out


def _run(cands, ref, **kw):
    kw.setdefault("n_null", 300)
    return pr.run_period_ruler(cands, ref, **kw)


class TestConformalMath:
    def test_ties_count_against_the_candidate(self):
        assert pr.q_strict(0.5, [0.5, 0.5, 0.5]) == 0.0
        assert pr.q_strict(0.6, [0.5, 0.5]) == 1.0
        assert pr.q_strict(0.6, []) is None

    def test_w1_is_zero_for_a_uniform_grid_and_half_at_a_point_mass(self):
        n = 1000
        assert pr.wasserstein1_uniform([(i + 0.5) / n for i in range(n)]) < 1e-3
        assert pr.wasserstein1_uniform([0.0] * 50) == pytest.approx(0.5)
        assert pr.wasserstein1_uniform([1.0] * 50) == pytest.approx(0.5)

    def test_fidelity_is_100_at_the_null_and_0_at_the_extreme(self):
        assert pr.period_fidelity(0.05, 0.05) == 100.0
        assert pr.period_fidelity(0.01, 0.05) == 100.0
        assert pr.period_fidelity(0.5, 0.05) == 0.0
        assert pr.authenticity_fidelity(1.0, 0.5) == 0.0
        assert pr.authenticity_fidelity(0.3, 0.5) == 100.0


class TestRuler:
    def test_real_like_set_sits_within_the_noise_floor(self):
        results, _ = _run(_candidates(), _reference())
        ch = results["period_ruler"]["channels"]
        assert ch["date"]["band"] == "within_reference_noise"
        assert ch["authenticity"]["band"] == "within_reference_noise"
        assert ch["date"]["period_fidelity"] > 80
        lo, hi = ch["date"]["null_period_fidelity_interval_95"]
        assert lo <= hi <= 100

    def test_anachronistic_and_synthetic_set_is_beyond_the_noise_floor(self):
        results, _ = _run(_candidates(shift_years=40, shift_p=0.4), _reference())
        ch = results["period_ruler"]["channels"]
        assert ch["date"]["band"] == "beyond_reference_noise"
        assert ch["authenticity"]["band"] == "beyond_reference_noise"
        assert ch["date"]["period_fidelity"] < 50
        assert ch["date"]["drift_years_bias_corrected"] == pytest.approx(40, abs=8)

    def test_instrument_bias_cancels(self):
        # The reference carries a -5 year instrument bias; real-like candidates
        # carry the same bias, so bias-corrected drift is near zero.
        results, _ = _run(_candidates(), _reference())
        assert abs(results["period_ruler"]["channels"]["date"]["drift_years_bias_corrected"]) < 5

    def test_length_binning_cancels_a_length_confounded_instrument(self):
        # Instruments read long passages as later and more synthetic. Real-like
        # candidates that are all long must not look anachronistic once the
        # comparison is length-matched; with a single length bin they would.
        ref = _reference(length_effect=0.1)
        cands = _candidates(n_words=290, length_effect=0.1)
        binned, _ = _run(cands, ref, n_length_bins=4)
        pooled, _ = _run(cands, ref, n_length_bins=1)
        assert binned["period_ruler"]["channels"]["date"]["band"] == "within_reference_noise"
        assert pooled["period_ruler"]["channels"]["date"]["band"] == "beyond_reference_noise"

    def test_same_seed_same_result(self):
        a, _ = _run(_candidates(), _reference(), seed=7)
        b, _ = _run(_candidates(), _reference(), seed=7)
        assert a == b

    def test_results_carry_no_verdict_or_selection_keys(self):
        results, _ = _run(_candidates(), _reference(), include_per_candidate=True)
        assert not (set(_walk_keys(results)) & _FORBIDDEN_KEYS)

    def test_single_group_reference_is_refused(self):
        ref = _reference()
        for r in ref:
            r["group"] = "only"
        with pytest.raises(pr.PeriodRulerError, match="two groups"):
            _run(_candidates(), ref)

    def test_candidates_outside_every_window_are_refused(self):
        cands = _candidates()
        for c in cands:
            c["year"] = 1990
        with pytest.raises(pr.PeriodRulerError, match="no candidate"):
            _run(cands, _reference())


class TestScoringAndResume:
    def test_cached_scores_are_reused_after_an_interrupted_run(self, tmp_path):
        calls = {"n": 0}

        def date_scorer(texts):
            calls["n"] += len(texts)
            return [1850.0 + (len(t) % 20) for t in texts]

        def auth_scorer(texts):
            return [((len(t) % 10) / 10) for t in texts]

        def strip(rows):
            return [{k: v for k, v in r.items() if k not in ("date_estimate_year", "p_synthetic")}
                    for r in rows]

        cache = tmp_path / "scores.jsonl"
        ref, cands = strip(_reference(n=200)), strip(_candidates(n=20))
        _run(cands, ref, date_scorer=date_scorer, auth_scorer=auth_scorer, cache_path=cache)
        first = calls["n"]
        assert first == 220
        _run(strip(_candidates(n=20)), strip(_reference(n=200)), date_scorer=date_scorer,
             auth_scorer=auth_scorer, cache_path=cache)
        assert calls["n"] == first


def _write_jsonl(path: Path, rows, year_key):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            out = {k: v for k, v in r.items() if k not in ("n_words", "year")}
            out[year_key] = r["year"]
            fh.write(json.dumps(out) + "\n")


class TestCli:
    def test_model_free_cli_envelope(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", _reference(), "year")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--n-null", "200", "--json"])
        env = json.loads(out.getvalue())
        assert rc == 0 and env["available"] is True
        assert env["task_surface"] == "period_register"
        assert "date" in env["results"]["period_ruler"]["channels"]

    def test_unscored_rows_without_torch_abstain(self, tmp_path, monkeypatch):
        rows = [{k: v for k, v in r.items() if k != "p_synthetic"} for r in _reference(n=100)]
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=5), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", rows, "year")
        monkeypatch.setitem(sys.modules, "torch", None)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--json"])
        env = json.loads(out.getvalue())
        assert rc == 3
        assert env["reason_category"] == "missing_dependency"

    def test_reference_without_groups_is_bad_input(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=5), "target_year")
        rows = [{k: v for k, v in r.items() if k != "group"} for r in _reference(n=50)]
        _write_jsonl(tmp_path / "r.jsonl", rows, "year")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--json"])
        assert rc == 3
        assert json.loads(out.getvalue())["reason_category"] == "bad_input"

    def test_detached_launcher_runs(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=20), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", _reference(n=300), "year")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "period_ruler.py"), "--candidates",
             str(tmp_path / "c.jsonl"), "--reference", str(tmp_path / "r.jsonl"),
             "--n-null", "100", "--json"],
            cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["tool"] == "period_ruler"
