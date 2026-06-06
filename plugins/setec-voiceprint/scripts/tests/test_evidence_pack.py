#!/usr/bin/env python3
"""Tests for evidence_pack.py — the multi-envelope evidence-pack bundler."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import evidence_pack as ep  # type: ignore  # noqa: E402


def _env(tool, surface, target="draft.md", available=True, results=None,
         warnings=None, licenses="reports X", does_not="not Y"):
    return {
        "schema_version": "1.0", "task_surface": surface, "tool": tool,
        "version": "1.0", "available": available,
        "target": {"path": target, "words": 500},
        "results": results if results is not None else {"a": 1, "b": {"x": 2}},
        "claim_license": {"licenses": licenses, "does_not_license": does_not},
        "warnings": warnings or [],
    }


def test_is_setec_envelope():
    assert ep.is_setec_envelope(_env("t", "s")) is True
    assert ep.is_setec_envelope({"foo": 1}) is False
    assert ep.is_setec_envelope([1, 2]) is False


def test_load_skips_malformed_and_nonsetec(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_env("variance_audit", "smoothing_diagnosis")))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    notsetec = tmp_path / "ns.json"
    notsetec.write_text(json.dumps({"hello": "world"}))
    envs, warns = ep.load_envelopes([str(good), str(bad), str(notsetec),
                                     str(tmp_path / "missing.json")])
    assert len(envs) == 1
    assert len(warns) == 3  # bad json, not-setec, missing file


def test_render_groups_by_target():
    md = ep.render_pack(
        [_env("variance_audit", "smoothing_diagnosis", target="draft.md"),
         _env("document_layout_audit", "document_layout", target="draft.md")],
        title="Pack")
    assert md.count("## Target: `draft.md`") == 1
    assert "`variance_audit`" in md and "`document_layout_audit`" in md


def test_render_has_title_and_claim_license():
    md = ep.render_pack([_env("variance_audit", "smoothing_diagnosis",
                              licenses="reports smoothing", does_not="no verdict")],
                        title="My Pack")
    assert md.startswith("# My Pack")
    assert "**Reports:** reports smoothing" in md
    assert "**Does NOT report:** no verdict" in md


def test_unavailable_noted():
    md = ep.render_pack([_env("voice_distance", "voice_coherence", available=False)],
                        title="P")
    assert "_unavailable_" in md


def test_warnings_aggregated():
    md = ep.render_pack([_env("variance_audit", "smoothing_diagnosis",
                              warnings=["tier2 skipped"])], title="P")
    assert "## Warnings" in md
    assert "[variance_audit] tier2 skipped" in md


def test_render_deterministic():
    envs = [_env("a_audit", "validation"), _env("b_audit", "voice_coherence")]
    assert ep.render_pack(envs, title="P") == ep.render_pack(envs, title="P")


def test_empty_renders_placeholder():
    md = ep.render_pack([], title="P", load_warnings=["skipped x"])
    assert "No SETEC audit envelopes" in md


def test_html_conversion_escapes_and_structures():
    md = ep.render_pack([_env("variance_audit", "smoothing_diagnosis",
                              licenses="reports <b> & stuff")], title="P")
    out = ep.markdown_to_html(md, title="P")
    assert out.startswith("<!doctype html>")
    assert "<h1>P</h1>" in out
    assert "<ul>" in out and "<li>" in out
    assert "&lt;b&gt;" in out  # raw < > escaped
    assert "<code>variance_audit</code>" in out  # `code` inline


def test_cli_writes_markdown(tmp_path, capsys):
    f = tmp_path / "e.json"
    f.write_text(json.dumps(_env("variance_audit", "smoothing_diagnosis")))
    out = tmp_path / "pack.md"
    rc = ep.main([str(f), "--out", str(out)])
    assert rc == 0
    assert out.read_text().startswith("# SETEC evidence pack")


def test_cli_html(tmp_path):
    f = tmp_path / "e.json"
    f.write_text(json.dumps(_env("variance_audit", "smoothing_diagnosis")))
    out = tmp_path / "pack.html"
    assert ep.main([str(f), "--format", "html", "--out", str(out)]) == 0
    assert out.read_text().startswith("<!doctype html>")


def test_cli_no_valid_envelopes_returns_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("nope")
    assert ep.main([str(bad)]) == 2
