#!/usr/bin/env python3
"""Regression tests for punctuation_cadence_audit.py (Release 5)."""

from __future__ import annotations

import sys
import json
import os
import subprocess
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import punctuation_cadence_audit as pa  # type: ignore


_VARIED = (
    "The discipline of attention is older than the disciplines that "
    "depend on it. The mathematician (and the carpenter — each in "
    "their own register) shares a single habit: each looks until "
    "the looking changes the looker. Why does this matter? Because "
    "attention, as everyone knows, is not what you give; it is what "
    "you discover. \"Look longer,\" she said. And so they did."
) * 3

_FLAT = (
    "The implementation of the framework requires consideration of "
    "multiple dimensions. Actionable insights are provided through "
    "holistic analysis. The challenges and opportunities of "
    "stakeholder engagement must be addressed in a robust manner. "
    "Decisions are made about the strategy, and recommendations are "
    "provided. The methodology leverages key takeaways from the "
    "literature."
) * 3


class TestAuditBasics:
    def test_empty_text_unavailable(self):
        a = pa.audit_punctuation_cadence("")
        assert a["available"] is False

    def test_returns_per_mark_densities(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        for key in (
            "comma_per_1k", "semicolon_per_1k", "em_dash_per_1k",
            "parenthesis_per_1k", "question_per_1k",
        ):
            assert key in a["densities_per_1k"]

    def test_sentence_final_distribution_sums_to_one(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        total = sum(a["sentence_final_distribution"].values())
        assert abs(total - 1.0) < 1e-9

    def test_interruption_grammar_records_three_types(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        ig = a["interruption_grammar"]
        for key in (
            "parenthetical_per_1k", "em_dash_aside_per_1k",
            "comma_appositive_per_1k", "total_interruption_per_1k",
        ):
            assert key in ig


class TestBandCall:
    def test_varied_lightly_regularized(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        assert a["compression"]["band"] == "Lightly regularized"

    def test_flat_at_least_moderately_regularized(self):
        a = pa.audit_punctuation_cadence(_FLAT)
        assert a["compression"]["band"] in {
            "Moderately regularized", "Heavily regularized",
        }

    def test_flat_flags_dominance_signals(self):
        a = pa.audit_punctuation_cadence(_FLAT)
        flagged = set(a["compression"]["flagged_signals"])
        assert "comma_period_dominance" in flagged
        assert "uniform_sentence_finals" in flagged

    def test_varied_punctuation_no_flags(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        # Varied prose has interruption grammar + sentence-final
        # variety; should fire few or no flags.
        assert a["compression"]["n_flagged"] <= 1


class TestPunctuationBigrams:
    def test_bigrams_recorded(self):
        text = "The dog said, \"hello!\" The cat replied, \"goodbye?\""
        a = pa.audit_punctuation_cadence(text)
        bigrams = a["punctuation_bigrams"]
        # Comma + opening-quote should be recorded.
        assert isinstance(bigrams, dict)


class TestBaselineHardening:
    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            pa.audit_baseline_punctuation(str(tmp_path / "no_dir"))

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        target = base / "draft.txt"
        target.write_text(_VARIED, encoding="utf-8")
        (base / "other.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1
        captured = capsys.readouterr()
        assert "draft.txt" in captured.err

    def test_filenames_anonymized_by_default(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_secret.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(str(base))
        for s in block["per_file_summaries"]:
            assert "client_secret" not in s["file"]
            assert s["file"].startswith("baseline_")

    def test_filenames_opt_in(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(
            str(base), include_filenames=True,
        )
        names = [s["file"] for s in block["per_file_summaries"]]
        assert "client.txt" in names

    def test_skipped_recorded(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "empty.txt").write_text("", encoding="utf-8")
        block = pa.audit_baseline_punctuation(str(base))
        assert block["n_skipped"] >= 1


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        md = pa.render_report(a)
        assert "## What this result licenses" in md
        assert "Punctuation cadence" in md or "punctuation" in md.lower()


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_VARIED, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = pa.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0


def test_legacy_and_package_share_function_globals(monkeypatch):
    from setec.surfaces import punctuation_cadence_audit as packaged

    assert pa is packaged
    assert pa.SCRIPT_DIR == Path(__file__).resolve().parents[1]
    monkeypatch.setattr(packaged, "_word_count", lambda text: 17)
    assert pa.audit_punctuation_cadence("some words")["n_words"] == 17
    monkeypatch.setattr(pa, "_word_count", lambda text: 23)
    assert packaged.audit_punctuation_cadence("some words")["n_words"] == 23


@pytest.mark.parametrize("first", ["punctuation_cadence_audit", "setec.surfaces.punctuation_cadence_audit"])
def test_fresh_import_order_and_runpy_main_identity(tmp_path, first):
    scripts = Path(__file__).resolve().parents[1]
    code = """
import importlib, runpy, sys
sys.path.insert(0, sys.argv[1])
first = importlib.import_module(sys.argv[2])
old = importlib.import_module('punctuation_cadence_audit')
new = importlib.import_module('setec.surfaces.punctuation_cadence_audit')
assert first is old is new
main_module = sys.modules['__main__']
new.main = lambda: 9
try:
    runpy.run_path(sys.argv[3], run_name='__main__')
except SystemExit as exc:
    assert exc.code == 9
else:
    raise AssertionError('main was not called')
assert sys.modules['__main__'] is main_module
"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", code, str(scripts), first,
                           str(scripts / "punctuation_cadence_audit.py")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_cli_refusals_and_empty_envelope(tmp_path, capsys):
    missing = tmp_path / "missing.txt"
    assert pa.main([str(missing), "--json"]) == 2
    assert "Input not found" in capsys.readouterr().err
    target = tmp_path / "target.txt"
    target.write_text(_VARIED, encoding="utf-8")
    assert pa.main([str(target), "--baseline-dir", str(missing), "--json"]) == 2
    assert "baseline error" in capsys.readouterr().err
    target.write_text("", encoding="utf-8")
    assert pa.main([str(target), "--json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["available"] is False and envelope["results"] == {}


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
