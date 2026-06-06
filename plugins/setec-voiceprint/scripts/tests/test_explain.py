#!/usr/bin/env python3
"""Tests for explain.py — the plain-language envelope explainer."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import explain  # type: ignore  # noqa: E402


def _env(surface="smoothing_diagnosis", tool="variance_audit", available=True,
         results=None, warnings=None, licenses="reports compression evidence",
         does_not="no AI/human verdict"):
    cl = None
    if licenses is not None:
        cl = {"licenses": licenses, "does_not_license": does_not}
    return {
        "schema_version": "1.0", "task_surface": surface, "tool": tool,
        "version": "1.0", "available": available,
        "results": results if results is not None else {"tier1": {"x": 1}},
        "claim_license": cl, "warnings": warnings or [],
    }


def test_renders_surface_label():
    out = explain.render_explain(_env())
    assert "variance_audit" in out
    assert "`smoothing_diagnosis`" in out
    assert "AI-prose smoothing diagnosis" in out  # from TASK_SURFACE_LABELS


def test_reports_licenses_and_refusals_verbatim():
    out = explain.render_explain(_env(licenses="reports X", does_not="not Y"))
    assert "## What you may conclude" in out
    assert "reports X" in out
    assert "## What you may NOT conclude" in out
    assert "not Y" in out


def test_unavailable_uses_warnings_no_fabricated_results():
    out = explain.render_explain(
        _env(available=False, results={}, licenses=None,
             warnings=["target is 40 words; below the floor"]))
    assert "did **not** produce a result" in out
    assert "below the floor" in out
    assert "`tier1`" not in out  # no fabricated results section


def test_next_step_rule_table():
    assert "baseline" in explain.render_explain(_env(surface="smoothing_diagnosis"))
    assert "non-voice" in explain.render_explain(_env(surface="document_layout"))
    # unknown surface → generic evidence-not-verdict default
    assert "evidence with a stated scope" in explain.render_explain(
        _env(surface="totally_new_surface"))


def test_deterministic():
    e = _env()
    assert explain.render_explain(e) == explain.render_explain(e)


def test_non_envelope_errors(tmp_path):
    f = tmp_path / "ns.json"
    f.write_text(json.dumps({"foo": 1}))
    assert explain.main([str(f)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert explain.main([str(bad)]) == 2


def test_cli_writes_out(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps(_env()))
    out = tmp_path / "exp.md"
    assert explain.main([str(f), "--out", str(out)]) == 0
    assert "## What this is" in out.read_text()


def test_reads_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_env(tool="voice_distance",
                                                                 surface="voice_coherence"))))
    assert explain.main(["-"]) == 0
    assert "voice_distance" in capsys.readouterr().out
