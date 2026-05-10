#!/usr/bin/env python3
"""Regression tests for voice_distance.py's register-match guard
(1.34.1 reviewer-flagged P2 fix).

Pre-1.34.1, the register-match check read
``entry.get("register")`` directly. But manifest-loaded entries
from ``stylometry_core.load_entries_from_manifest`` carry their
register under ``entry["metadata"]["register"]`` — so the check
always saw ``None`` for manifest baselines and emitted a false
"mismatch" warning on every normal run.

Reviewer reproduced: a `--baseline-dir` run warning that
`literary_fiction` was being compared against dominantly
`unknown`. Same shape on a manifest run with register-tagged
entries.

Tests pin:
  * `_baseline_registers` reads both shapes (top-level and nested
    under metadata).
  * `_build_register_match` returns ``strength="unavailable"`` when
    no entry exposes a register, not a false ``mismatch``.
  * When manifest entries DO carry register metadata, the check
    works correctly (strong / moderate / weak / mismatch as
    appropriate).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import voice_distance as vd  # type: ignore


# ---------- _baseline_registers -----------------------------------


class TestBaselineRegisters:
    def test_reads_metadata_register(self):
        """Manifest-loaded shape: entry["metadata"]["register"]."""
        entries = [
            {"id": "a", "metadata": {"register": "blog_essay"}},
            {"id": "b", "metadata": {"register": "literary_fiction"}},
        ]
        assert vd._baseline_registers(entries) == [
            "blog_essay", "literary_fiction",
        ]

    def test_reads_top_level_register_fallback(self):
        """Forward-compat: any caller passing top-level register
        still works."""
        entries = [
            {"id": "a", "register": "blog_essay"},
            {"id": "b", "register": "blog_essay"},
        ]
        assert vd._baseline_registers(entries) == [
            "blog_essay", "blog_essay",
        ]

    def test_metadata_takes_precedence(self):
        """If both shapes exist, the manifest-loaded shape wins."""
        entries = [
            {
                "id": "a",
                "register": "wrong",
                "metadata": {"register": "blog_essay"},
            },
        ]
        assert vd._baseline_registers(entries) == ["blog_essay"]

    def test_directory_baseline_returns_none(self):
        """Directory-baseline entries (no manifest) have no register
        metadata. Pre-1.34.1 the bug surface."""
        entries = [
            {"id": "a", "metadata": {"source": "directory"}},
            {"id": "b", "metadata": {"source": "directory"}},
        ]
        assert vd._baseline_registers(entries) == [None, None]

    def test_empty_metadata(self):
        """Entries with empty metadata."""
        entries = [
            {"id": "a", "metadata": {}},
            {"id": "b"},
        ]
        assert vd._baseline_registers(entries) == [None, None]


# ---------- _build_register_match --------------------------------


class TestBuildRegisterMatch:
    def test_no_register_returns_unavailable(self):
        """Pre-1.34.1: this would have called register_match with
        all-unknown baseline registers and returned 'mismatch'.
        Post-fix: returns 'unavailable' with an explanatory
        rationale."""
        entries = [
            {"id": "a", "metadata": {"source": "directory"}},
            {"id": "b", "metadata": {"source": "directory"}},
        ]
        match = vd._build_register_match(entries, "literary_fiction")
        assert match["strength"] == "unavailable"
        assert "register tags" in match["rationale"]
        assert match["target"] == "literary_fiction"
        assert match["baseline_distribution"] == {}

    def test_strong_match_via_metadata(self):
        """Manifest entries with register metadata produce a
        normal match strength (the bug-fix path)."""
        entries = [
            {"id": str(i), "metadata": {"register": "blog_essay"}}
            for i in range(5)
        ]
        match = vd._build_register_match(entries, "blog_essay")
        assert match["strength"] == "strong"

    def test_mismatch_via_metadata(self):
        """When metadata IS present and the target genuinely
        doesn't match, mismatch is the correct call (not the
        spurious one the bug produced)."""
        entries = [
            {"id": str(i), "metadata": {"register": "legal_memo"}}
            for i in range(5)
        ]
        match = vd._build_register_match(entries, "blog_essay")
        assert match["strength"] == "mismatch"
        assert "legal_memo" in match["rationale"]

    def test_partial_register_coverage(self):
        """Some entries with register, some without (mixed manifest
        with directory entries) — only the tagged entries
        contribute to the match."""
        entries = [
            {"id": "a", "metadata": {"register": "blog_essay"}},
            {"id": "b", "metadata": {"source": "directory"}},
            {"id": "c", "metadata": {"register": "blog_essay"}},
        ]
        match = vd._build_register_match(entries, "blog_essay")
        # The pre-fix behavior would have lumped the directory
        # entry into "unknown" and thrown the result. Post-fix:
        # at least one entry exposes register, so register_match
        # runs against the tagged-entries-plus-Nones distribution.
        assert match["strength"] != "unavailable"

    def test_empty_string_register_treated_as_missing(self):
        """An entry with `register=""` is functionally untagged."""
        entries = [
            {"id": "a", "metadata": {"register": ""}},
            {"id": "b", "metadata": {"register": "  "}},
        ]
        match = vd._build_register_match(entries, "blog_essay")
        assert match["strength"] == "unavailable"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
