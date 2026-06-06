#!/usr/bin/env python3
"""Tests for tools/check_docs_freshness.py.

Pins:

  * The committed tree passes the gate (changelog coverage holds; readiness
    sub-check is ok-or-skipped depending on branch state).
  * A synthetic curated entry with no changelog mention trips changelog coverage.
  * TODO-status entries are exempt from changelog coverage.
  * The readiness sub-check is tolerant: 'skipped' when the generator/doc are
    absent, never a hard failure on that account alone.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

pytest.importorskip("yaml")

import check_docs_freshness as cdf  # type: ignore  # noqa: E402


def test_committed_tree_passes_gate():
    assert cdf.main([]) == 0


def test_changelog_coverage_clean_on_committed_manifest():
    missing = cdf.changelog_coverage(cdf.DEFAULT_MANIFEST, cdf.CHANGELOG)
    assert missing == [], f"curated capabilities missing from CHANGELOG: {missing}"


def test_missing_changelog_entry_is_flagged(tmp_path):
    manifest = tmp_path / "caps.yaml"
    manifest.write_text(
        "schema_version: '0.3.0'\n"
        "entries:\n"
        "  - id: a_brand_new_capability_xyz\n"
        "    status: heuristic\n",
        encoding="utf-8",
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\nnothing relevant here\n", encoding="utf-8")
    missing = cdf.changelog_coverage(manifest, changelog)
    assert missing == ["a_brand_new_capability_xyz"]


def test_todo_entries_exempt_from_coverage(tmp_path):
    manifest = tmp_path / "caps.yaml"
    manifest.write_text(
        "schema_version: '0.3.0'\n"
        "entries:\n"
        "  - id: still_a_todo_capability\n"
        "    status: todo\n",
        encoding="utf-8",
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n", encoding="utf-8")
    assert cdf.changelog_coverage(manifest, changelog) == []


def test_readiness_subcheck_is_tolerant():
    status, _ = cdf.readiness_freshness()
    assert status in {"ok", "skipped", "stale"}
    # On a branch without the readiness kit it must be 'skipped', not 'stale'.
    import importlib.util

    has_gen = importlib.util.find_spec("gen_calibration_readiness") is not None
    if not has_gen:
        assert status == "skipped"


def test_json_output_is_pure_json(capsys):
    """[P2 regression] --json must emit ONLY a JSON object. The readiness
    sub-check compares the rendered region directly instead of calling the
    generator's --check (which prints a human status line), so no stray text
    leaks onto stdout even once the generator is present."""
    import json

    rc = cdf.main(["--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)  # must not raise
    assert "ok" in parsed and "readiness" in parsed
    assert rc in (0, 1)
