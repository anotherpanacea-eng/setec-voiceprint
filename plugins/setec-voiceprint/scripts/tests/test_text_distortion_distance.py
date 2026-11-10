#!/usr/bin/env python3
"""Behavior tests for text_distortion_distance (VP-TD1, Stamatatos 2017 text distortion)."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

import text_distortion_distance as tdd  # type: ignore  # noqa: E402

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "class", "classification", "decision",
    "score", "confidence", "rank", "prediction", "flag", "selection", "best", "top", "selected",
})
_NOUNS = "river window lantern meadow harbour letter candle orchard bridge garden".split()
_ADJS = "quiet narrow golden distant broken patient gentle hollow".split()
_VERBS = "watched carried folded crossed gathered answered".split()


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _periodic(rng, n_sentences):
    """Long, clause-heavy sentences: asides, semicolons, relative clauses."""
    out = []
    for _ in range(n_sentences):
        n1, n2, n3 = rng.sample(_NOUNS, 3)
        a, v = rng.choice(_ADJS), rng.choice(_VERBS)
        out.append(f"It was in the {a} {n1}, which she had {v} so often, that the {n2}, "
                   f"for all that it was {rng.choice(_ADJS)}, seemed to her, if not more than "
                   f"the {n3}, at least as much; and yet she would not have said so.")
    return " ".join(out)


def _plain(rng, n_sentences):
    """Short declaratives with no subordination."""
    out = []
    for _ in range(n_sentences):
        out.append(f"{rng.choice(_NOUNS).title()} {rng.choice(_VERBS)} {rng.choice(_NOUNS)}s. "
                   f"{rng.choice(_ADJS).title()} {rng.choice(_NOUNS)}s {rng.choice(_VERBS)}.")
    return " ".join(out)


def test_masking_keeps_function_words_punctuation_and_word_lengths():
    text = "In the morning, which was grey, she walked 12 miles."
    assert tdd.distort(text, tdd.FUNCTION_WORDS) == "In the *******, which was ****, she ****** ## *****."
    assert tdd.distort(text, tdd.FUNCTION_WORDS, "dv-sa") == "In the *, which was *, she * ## *."


def test_content_words_of_equal_length_do_not_move_the_distance():
    """The point of text distortion: swapping topic words leaves the masked view unchanged."""
    base = ["The candle, which was quiet, watched the bridge; and yet it was not so. " * 30] * 3
    swapped = [{"id": "x", "text": "The harbor, which was blank, crossed the meadow; and yet it was not so. " * 30}]
    block = tdd.compare(swapped, base)["text_distortion"]
    assert block["distance"] == pytest.approx(0.0, abs=1e-12)


def test_matching_structure_is_closer_than_different_structure():
    rng = random.Random(7)
    baseline = [_periodic(rng, 15) for _ in range(5)]
    near = [{"id": "near", "text": _periodic(rng, 15)}]
    far = [{"id": "far", "text": _plain(rng, 40)}]
    d_near = tdd.compare(near, baseline)["text_distortion"]
    d_far = tdd.compare(far, baseline)["text_distortion"]
    assert d_near["distance"] < d_far["distance"]
    # The baseline's own spread brackets the matching target and not the plain one.
    loo = d_far["baseline_leave_one_out"]
    assert loo["available"] and loo["n"] == 5
    assert d_far["per_document"][0]["percentile"] == 1.0
    assert d_near["baseline_leave_one_out"]["target_percentile"] < 1.0


def test_copy_of_target_in_baseline_is_excluded():
    rng = random.Random(3)
    target_text = _plain(rng, 30)
    baseline = [target_text] + [_periodic(rng, 15) for _ in range(3)]
    block = tdd.compare([{"id": "t", "text": target_text}], baseline)["text_distortion"]
    assert block["baseline_self_excluded"] == 1
    assert block["baseline_documents"] == 3
    assert block["distance"] > 0.05


def test_leave_one_out_needs_three_baseline_documents():
    rng = random.Random(5)
    block = tdd.compare([{"id": "t", "text": _plain(rng, 20)}],
                        [_periodic(rng, 10), _periodic(rng, 10)])["text_distortion"]
    assert block["baseline_leave_one_out"]["available"] is False
    assert "percentile" not in block["per_document"][0]


def test_baseline_top_k_keep_set_comes_from_the_baseline():
    rng = random.Random(9)
    baseline = [_periodic(rng, 15) for _ in range(3)]
    kept = tdd.keep_set(baseline, "baseline-top-k", 5)
    assert len(kept) == 5 and "the" in kept


def _run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "text_distortion_distance.py"), *map(str, args)],
                          capture_output=True, text=True, timeout=60)


def test_cli_success_envelope_names_jsonl_rows(tmp_path):
    rng = random.Random(11)
    target = tmp_path / "t.jsonl"
    target.write_text("".join(json.dumps({"id": f"item-{i}", "text": _plain(rng, 30)}) + "\n"
                              for i in range(2)), encoding="utf-8")
    base = tmp_path / "base"
    base.mkdir()
    for i in range(3):
        (base / f"{i}.txt").write_text(_periodic(rng, 15), encoding="utf-8")
    result = _run_cli("--target", target, "--baseline", base, "--json")
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["available"] is True and env["task_surface"] == "voice_coherence"
    block = env["results"]["text_distortion"]
    assert [r["id"] for r in block["per_document"]] == ["item-0", "item-1"]
    assert not (_FORBIDDEN_KEYS & set(_walk_keys(env["results"])))


def test_cli_abstains_on_short_input_and_rejects_bad_jsonl(tmp_path):
    rng = random.Random(13)
    base = tmp_path / "base.txt"
    base.write_text(_periodic(rng, 30), encoding="utf-8")
    short = tmp_path / "short.txt"
    short.write_text("Too short to read.", encoding="utf-8")
    result = _run_cli("--target", short, "--baseline", base, "--json")
    assert result.returncode == 3
    assert json.loads(result.stdout)["reason_category"] == "text_too_short"
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"body": "no text field"}\n', encoding="utf-8")
    result = _run_cli("--target", bad, "--baseline", base, "--json")
    assert result.returncode == 3
    assert json.loads(result.stdout)["reason_category"] == "bad_input"


def test_copied_plugin_runs_without_install(tmp_path):
    """Zero-install contract: a copied plugin runs the CLI with no PYTHONPATH or site packages."""
    plugin = tmp_path / "plugin"
    shutil.copytree(SCRIPTS.parent, plugin, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    result = subprocess.run(
        [sys.executable, "-B", "-S", str(plugin / "scripts/text_distortion_distance.py"), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--baseline" in result.stdout
