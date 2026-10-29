#!/usr/bin/env python3
"""Behavior tests for phrase_overuse_profile (VP-RS4, Antislop-style profile)."""

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

import phrase_overuse_profile as pop  # type: ignore  # noqa: E402

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "class", "classification", "decision",
    "score", "confidence", "rank", "prediction", "flag", "selection", "best", "top", "selected",
})
_VOCAB = ("river stone window lantern meadow harbour letter candle orchard bridge garden "
          "shadow morning evening winter summer village carriage cottage hedge").split()


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _doc(rng, n_sentences, inject=None):
    sents = []
    for i in range(n_sentences):
        words = [rng.choice(_VOCAB) for _ in range(rng.randint(6, 14))]
        if inject and i % 3 == 0:
            words[2:2] = inject.split()
        sents.append(" ".join(words))
    return ". ".join(sents) + "."


def _corpora(seed=0):
    rng = random.Random(seed)
    target = [_doc(rng, 30, inject="tapestry of whispers") for _ in range(8)]
    reference = [_doc(rng, 60) for _ in range(40)]
    return target, reference


class TestProfile:
    def test_injected_phrase_leads_the_list(self):
        target, reference = _corpora()
        block = pop.profile_overuse(target, reference)["phrase_overuse"]
        top_ngrams = [r["ngram"] for r in block["over_represented"][:3]]
        assert "tapestry of whispers" in top_ngrams
        row = next(r for r in block["over_represented"] if r["ngram"] == "tapestry of whispers")
        assert row["reference_count"] == 0 and row["overuse_ratio"] > 10 and row["g2"] > 0

    def test_list_is_ordered_by_ratio_and_only_over_represented(self):
        target, reference = _corpora()
        rows = pop.profile_overuse(target, reference)["phrase_overuse"]["over_represented"]
        ratios = [r["overuse_ratio"] for r in rows]
        assert ratios == sorted(ratios, reverse=True)
        assert all(r > 1.0 for r in ratios)

    def test_one_repetitive_document_cannot_dominate(self):
        rng = random.Random(3)
        target = [_doc(rng, 30, inject="tapestry of whispers")] + [_doc(rng, 30) for _ in range(5)]
        _, reference = _corpora()
        rows = pop.profile_overuse(target, reference, min_docs=2)["phrase_overuse"]["over_represented"]
        assert "tapestry of whispers" not in {r["ngram"] for r in rows}

    def test_ngrams_do_not_cross_sentence_boundaries(self):
        segs = pop.tokenize_segments("The end. Of days; and nights\nbegin")
        assert segs == [["the", "end"], ["of", "days"], ["and", "nights"], ["begin"]]

    def test_function_word_ngrams_dropped_unless_kept(self):
        target = ["of the of the of the. of the of the."] * 3
        reference = ["river stone window lantern meadow. harbour letter candle."] * 3
        dropped = pop.profile_overuse(target, reference)["phrase_overuse"]["over_represented"]
        kept = pop.profile_overuse(target, reference, keep_function_ngrams=True)["phrase_overuse"]
        assert not dropped
        assert "of the" in {r["ngram"] for r in kept["over_represented"]}

    def test_results_carry_no_verdict_or_selection_keys(self):
        target, reference = _corpora()
        assert not (set(_walk_keys(pop.profile_overuse(target, reference))) & _FORBIDDEN_KEYS)


def _write_dir(path: Path, docs):
    path.mkdir()
    for i, d in enumerate(docs):
        (path / f"d{i:03d}.txt").write_text(d, encoding="utf-8")


class TestCli:
    def _main(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pop.main(argv)
        return rc, json.loads(out.getvalue())

    def test_envelope_on_directories(self, tmp_path):
        target, reference = _corpora()
        _write_dir(tmp_path / "t", target)
        _write_dir(tmp_path / "r", reference)
        rc, env = self._main(["--target", str(tmp_path / "t"), "--reference", str(tmp_path / "r"),
                              "--min-reference-tokens", "1000", "--json"])
        assert rc == 0 and env["available"] is True
        assert env["task_surface"] == "formulaicity"
        assert env["results"]["phrase_overuse"]["over_represented"]

    def test_small_reference_abstains(self, tmp_path):
        target, reference = _corpora()
        _write_dir(tmp_path / "t", target)
        _write_dir(tmp_path / "r", reference[:2])
        rc, env = self._main(["--target", str(tmp_path / "t"), "--reference", str(tmp_path / "r"),
                              "--json"])
        assert rc == 3 and env["reason_category"] == "text_too_short"

    def test_jsonl_without_text_is_bad_input(self, tmp_path):
        (tmp_path / "t.jsonl").write_text(json.dumps({"body": "x"}) + "\n", encoding="utf-8")
        rc, env = self._main(["--target", str(tmp_path / "t.jsonl"), "--reference",
                              str(tmp_path / "t.jsonl"), "--json"])
        assert rc == 3 and env["reason_category"] == "bad_input"

    def test_bad_n_is_rejected(self):
        with pytest.raises(SystemExit):
            pop.main(["--target", "x", "--reference", "y", "--n", "0"])

    def test_detached_launcher_runs(self, tmp_path):
        target, reference = _corpora()
        _write_dir(tmp_path / "t", target)
        _write_dir(tmp_path / "r", reference)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "phrase_overuse_profile.py"), "--target",
             str(tmp_path / "t"), "--reference", str(tmp_path / "r"),
             "--min-reference-tokens", "1000", "--json"],
            cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["tool"] == "phrase_overuse_profile"
