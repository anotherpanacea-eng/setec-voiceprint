#!/usr/bin/env python3
"""Behavior tests for meaning_preservation (VP-RS5). Model-free: scorers are stubs."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

import meaning_preservation as mp  # type: ignore  # noqa: E402

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


def _overlap_mis(sources, rewrites):
    out = []
    for s, r in zip(sources, rewrites):
        a, b = set(s.lower().split()), set(r.lower().split())
        out.append(len(a & b) / len(a | b))
    return out


def _bag_encoder(texts):
    vocab = sorted({w for t in texts for w in t.lower().split()})
    return [[t.lower().split().count(w) for w in vocab] for t in texts]


PAIRS = [
    {"id": "same", "source": "the rain fell on the old roof", "rewrite": "the rain fell on the old roof"},
    {"id": "close", "source": "the rain fell on the old roof", "rewrite": "rain fell upon the old roof"},
    {"id": "far", "source": "the rain fell on the old roof", "rewrite": "a merchant counted his coins"},
]


class TestScores:
    def test_identical_pair_scores_highest_and_unrelated_lowest(self):
        res, _ = mp.run_meaning_preservation(PAIRS, mis_scorer=_overlap_mis, sbert_encoder=_bag_encoder)
        rows = {r["id"]: r for r in res["meaning_preservation"]["per_pair"]}
        for key in ("mis", "sbert_cosine"):
            assert rows["same"][key] == pytest.approx(1.0)
            assert rows["same"][key] > rows["close"][key] > rows["far"][key]
        ch = res["meaning_preservation"]["channels"]
        assert ch["mis"]["n"] == 3 and ch["mis"]["min"] == rows["far"]["mis"]
        assert "NC-SA" in ch["mis"]["license"]

    def test_batches_do_not_change_results(self):
        a, _ = mp.run_meaning_preservation(PAIRS, mis_scorer=_overlap_mis, sbert_encoder=_bag_encoder,
                                           batch_size=1)
        b, _ = mp.run_meaning_preservation(PAIRS, mis_scorer=_overlap_mis, sbert_encoder=_bag_encoder,
                                           batch_size=16)
        assert [r["mis"] for r in a["meaning_preservation"]["per_pair"]] == \
               [r["mis"] for r in b["meaning_preservation"]["per_pair"]]

    def test_missing_channel_is_dropped_with_a_warning(self):
        res, warnings = mp.run_meaning_preservation(PAIRS, mis_scorer=None, sbert_encoder=_bag_encoder)
        assert set(res["meaning_preservation"]["channels"]) == {"sbert_cosine"}
        assert any("mis skipped" in w for w in warnings)

    def test_no_channel_at_all_raises(self):
        with pytest.raises(LookupError):
            mp.run_meaning_preservation(PAIRS, mis_scorer=None, sbert_encoder=None)

    def test_long_pairs_warn_about_truncation(self):
        long = [{"id": "long", "source": "word " * 400, "rewrite": "word " * 400}]
        _, warnings = mp.run_meaning_preservation(long, mis_scorer=_overlap_mis, sbert_encoder=None,
                                                  channels=("mis",))
        assert any("truncated" in w for w in warnings)

    def test_results_carry_no_verdict_or_selection_keys(self):
        res, _ = mp.run_meaning_preservation(PAIRS, mis_scorer=_overlap_mis, sbert_encoder=_bag_encoder)
        assert not (set(_walk_keys(res)) & _FORBIDDEN_KEYS)


class TestCli:
    def _main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = mp.main(argv)
        return rc, json.loads(out.getvalue())

    def _pairs_file(self, tmp_path):
        p = tmp_path / "pairs.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in PAIRS), encoding="utf-8")
        return p

    def test_envelope_with_injected_models(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp, "_load_mis", lambda device: _overlap_mis)
        monkeypatch.setattr(mp, "_load_sbert", lambda model, device: _bag_encoder)
        rc, env = self._main(["--pairs", str(self._pairs_file(tmp_path)), "--json"])
        assert rc == 0 and env["available"] is True
        assert env["task_surface"] == "craft_restoration"
        assert len(env["results"]["meaning_preservation"]["per_pair"]) == 3

    def test_no_models_abstains_with_missing_dependency(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mp, "_load_mis", lambda device: None)
        monkeypatch.setattr(mp, "_load_sbert", lambda model, device: None)
        rc, env = self._main(["--pairs", str(self._pairs_file(tmp_path)), "--json"])
        assert rc == 3 and env["reason_category"] == "missing_dependency"

    def test_pair_without_rewrite_is_bad_input(self, tmp_path):
        p = tmp_path / "bad.jsonl"
        p.write_text(json.dumps({"id": "x", "source": "a"}) + "\n", encoding="utf-8")
        rc, env = self._main(["--pairs", str(p), "--json"])
        assert rc == 3 and env["reason_category"] == "bad_input"

    def test_detached_launcher_abstains_cleanly_without_models(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "meaning_preservation.py"), "--pairs",
             str(self._pairs_file(tmp_path)), "--json"],
            cwd=tmp_path, capture_output=True, text=True,
            env={"setec_hermetic_backend": "stub", "PATH": "/usr/bin:/bin"})
        assert proc.returncode == 3, proc.stderr
        env = json.loads(proc.stdout)
        assert env["tool"] == "meaning_preservation" and env["reason_category"] == "missing_dependency"
