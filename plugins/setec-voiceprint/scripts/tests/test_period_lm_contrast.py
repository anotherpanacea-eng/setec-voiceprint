#!/usr/bin/env python3
"""Behavior tests for period_lm_contrast (VP-RS6). Model-free: bpb scorers are stubs."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

import period_lm_contrast as plc  # type: ignore  # noqa: E402

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "class", "classification", "decision",
    "score", "confidence", "rank", "prediction", "flag", "selection", "best", "top", "selected",
})
_OLD = {"whilst", "thee", "hath", "upon", "doth"}


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _period(texts):  # finds old words easy
    return [1.2 - 0.1 * sum(w in _OLD for w in t.split()) for t in texts]


def _modern(texts):  # finds old words hard
    return [1.0 + 0.1 * sum(w in _OLD for w in t.split()) for t in texts]


def _rows(n, old_words):
    return [{"id": f"p{i}", "text": " ".join(["the", "house"] + ["upon"] * old_words + [str(i)])}
            for i in range(n)]


def _run(passages, reference=None, **kw):
    kw.setdefault("n_boot", 300)
    return plc.run_contrast(passages, reference, period_model="P", modern_model="M",
                            period_scorer=kw.pop("ps", _period), modern_scorer=kw.pop("ms", _modern), **kw)


class TestContrast:
    def test_period_like_passages_have_negative_contrast(self):
        block = _run(_rows(20, 4))["period_lm_contrast"]["candidates"]
        assert block["mean_contrast_bpb"] == pytest.approx(-0.6)
        assert block["share_period_easier"] == 1.0
        lo, hi = block["mean_contrast_bpb_interval_95"]
        assert lo <= block["mean_contrast_bpb"] <= hi

    def test_gap_against_a_real_period_reference(self):
        out = _run(_rows(20, 0), _rows(20, 4))["period_lm_contrast"]
        assert out["candidate_minus_reference_mean_bpb"] == pytest.approx(0.8)
        lo, hi = out["candidate_minus_reference_interval_95"]
        assert lo > 0

    def test_same_seed_same_result(self):
        assert _run(_rows(10, 2), seed=3) == _run(_rows(10, 2), seed=3)

    def test_precomputed_rows_need_no_model(self):
        rows = [{"id": "a", "text": "x", "period_bpb": 1.0, "modern_bpb": 1.5},
                {"id": "b", "text": "y", "period_bpb": 1.2, "modern_bpb": 1.1}]
        out = _run(rows, ps=None, ms=None)["period_lm_contrast"]["candidates"]
        assert out["mean_contrast_bpb"] == pytest.approx(-0.2)

    def test_unscored_rows_without_a_model_raise(self):
        with pytest.raises(LookupError):
            _run(_rows(3, 1), ps=None)

    def test_cache_resumes_without_rescoring(self, tmp_path):
        calls = {"n": 0}

        def counting(texts):
            calls["n"] += len(texts)
            return _period(texts)

        cache = tmp_path / "c.jsonl"
        _run(_rows(6, 1), ps=counting, cache_path=cache, batch_size=2)
        assert calls["n"] == 6
        _run(_rows(6, 1), ps=counting, cache_path=cache)
        assert calls["n"] == 6

    def test_cache_is_bound_to_the_context_length(self, tmp_path):
        """A score cached under one chunk size is not reused under another (review 4411700188)."""
        def by_window(max_tokens):
            return lambda texts: [float(max_tokens)] * len(texts)

        cache = tmp_path / "c.jsonl"
        first = _run(_rows(4, 1), ps=by_window(1024), cache_path=cache, max_tokens=1024)
        second = _run(_rows(4, 1), ps=by_window(512), cache_path=cache, max_tokens=512)
        assert first["period_lm_contrast"]["candidates"]["mean_period_bpb"] == 1024.0
        assert second["period_lm_contrast"]["candidates"]["mean_period_bpb"] == 512.0

    def test_results_carry_no_verdict_or_selection_keys(self):
        out = _run(_rows(5, 1), _rows(5, 3), include_per_passage=True)
        assert not (set(_walk_keys(out)) & _FORBIDDEN_KEYS)


class TestPolicy:
    def test_undeclared_license_model_gets_a_warning(self):
        assert "declares no license" in plc.license_warning("typewriter-ai/typewriter-1913-7B-base")
        assert plc.license_warning("talkie-lm/talkie-1930-13b-base") is None


class TestCli:
    def _main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = plc.main(argv)
        return rc, json.loads(out.getvalue())

    def _write(self, path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return str(path)

    def test_envelope_with_injected_scorers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plc, "_load_scorer", lambda model_id, **_: _period if "1930" in model_id else _modern)
        rc, env = self._main(["--passages", self._write(tmp_path / "p.jsonl", _rows(8, 2)),
                              "--n-boot", "200", "--json"])
        assert rc == 0 and env["task_surface"] == "period_register"
        assert env["results"]["period_lm_contrast"]["modern_model"] == "talkie-lm/talkie-web-13b-base"

    def test_typewriter_loads_by_default_with_a_license_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plc, "_load_scorer", lambda model_id, **_: _period if "1913" in model_id else _modern)
        rc, env = self._main(["--passages", self._write(tmp_path / "p.jsonl", _rows(4, 1)),
                              "--pair", "typewriter", "--modern-model", "talkie-lm/talkie-web-13b-base",
                              "--n-boot", "100", "--json"])
        assert rc == 0
        assert any("declares no license" in w for w in env["warnings"])

    def test_full_cache_loads_no_model(self, tmp_path, monkeypatch):
        """A fully populated --score-cache never constructs a scorer (review 4411700171)."""
        monkeypatch.setattr(plc, "_load_scorer", lambda model_id, **_: _period if "1930" in model_id else _modern)
        passages = self._write(tmp_path / "p.jsonl", _rows(5, 1))
        cache = str(tmp_path / "c.jsonl")
        rc, first = self._main(["--passages", passages, "--score-cache", cache, "--n-boot", "100", "--json"])
        assert rc == 0
        loaded = []
        monkeypatch.setattr(plc, "_load_scorer", lambda model_id, **_: loaded.append(model_id))
        rc, again = self._main(["--passages", passages, "--score-cache", cache, "--n-boot", "100", "--json"])
        assert rc == 0 and loaded == []
        assert again["results"] == first["results"]

    def test_typewriter_needs_a_modern_twin(self, tmp_path):
        rc, env = self._main(["--passages", self._write(tmp_path / "p.jsonl", _rows(3, 1)),
                              "--pair", "typewriter", "--json"])
        assert rc == 3 and env["reason_category"] == "bad_input"

    def test_detached_launcher_abstains_without_models(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "period_lm_contrast.py"), "--passages",
             self._write(tmp_path / "p.jsonl", _rows(3, 1)), "--json"],
            cwd=tmp_path, capture_output=True, text=True,
            env={"setec_hermetic_backend": "stub", "PATH": "/usr/bin:/bin"})
        assert proc.returncode == 3, proc.stderr
        assert json.loads(proc.stdout)["reason_category"] == "missing_dependency"
