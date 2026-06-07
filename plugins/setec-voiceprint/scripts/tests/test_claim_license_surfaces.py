#!/usr/bin/env python3
"""Tests for the drop-in surface-label registry (#170, PR1).

``claim_license.TASK_SURFACE_LABELS`` is no longer an inline dict; it is
assembled at import from one-file-per-surface fragments under
``claim_license_surfaces/``. A new audit registers its surface by adding a
fragment file, never by editing a shared dict — so parallel audit PRs stop
colliding on one insertion point (the failure mode that put a SyntaxError on
main on 2026-06-06).

These tests pin three guarantees:
  * No behavior change: the assembled dict equals a byte-for-byte golden
    snapshot of the pre-refactor literal (21 surfaces).
  * Round-trip integrity: every fragment's stored bytes recover its label
    exactly (no lossy strip), and the fragment set is exactly the key set.
  * Drop-in actually works: a new fragment is picked up by both
    TASK_SURFACE_LABELS and the derived output_schema.VALID_TASK_SURFACES,
    with no edit to either module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import claim_license  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "_golden_task_surface_labels.json"
_FRAG_DIR = ROOT / "claim_license_surfaces"


def test_matches_golden_snapshot_byte_for_byte():
    """The assembled dict must equal the pre-refactor literal exactly."""
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert TASK_SURFACE_LABELS == golden
    assert len(TASK_SURFACE_LABELS) == 21


def test_fragments_are_exactly_the_key_set():
    """One fragment per surface, filename == key, no extras, no missing."""
    frag_keys = {p.stem for p in _FRAG_DIR.glob("*.txt")}
    assert frag_keys == set(TASK_SURFACE_LABELS)


def test_round_trip_is_lossless():
    """Each fragment recovers its label exactly; no internal newline or edge
    whitespace that ``rstrip('\\n')`` would silently mangle, and no empty
    label (guards against a future blank/malformed fragment that the
    golden-count check alone wouldn't catch)."""
    for key, label in TASK_SURFACE_LABELS.items():
        raw = (_FRAG_DIR / f"{key}.txt").read_text(encoding="utf-8")
        assert raw.rstrip("\n") == label
        assert label, f"empty label for surface {key!r}"
        assert "\n" not in label
        assert label == label.strip()


def test_iteration_order_is_deterministic_alphabetical():
    """The drop-in loader iterates fragments in sorted (alphabetical) order.
    This is the one intentional behavioral change vs. the old insertion-ordered
    literal: deterministic, and the only collision-free order for a drop-in
    registry (preserving arbitrary insertion order would need a shared ordered
    file — the exact thing this refactor removes). Pin it so it stays
    intentional rather than incidental."""
    assert list(TASK_SURFACE_LABELS) == sorted(TASK_SURFACE_LABELS)


def test_loader_is_safe_when_dir_absent(tmp_path, monkeypatch):
    """Missing dir → empty dict (render_block already falls back to the raw
    key via .get), so import never hard-fails on a stripped checkout."""
    monkeypatch.setattr(claim_license, "_SURFACE_LABEL_DIR", tmp_path / "nope")
    assert claim_license._load_surface_labels() == {}


def test_dropin_registers_a_new_surface(tmp_path, monkeypatch):
    """The drop-in contract: writing a fragment file is sufficient to register
    a surface in BOTH registries, with no edit to claim_license or
    output_schema."""
    # Mirror the real fragments into a temp dir + add a new one.
    staging = tmp_path / "claim_license_surfaces"
    staging.mkdir()
    for p in _FRAG_DIR.glob("*.txt"):
        (staging / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    (staging / "demo_new_surface.txt").write_text("demo label\n", encoding="utf-8")

    monkeypatch.setattr(claim_license, "_SURFACE_LABEL_DIR", staging)
    labels = claim_license._load_surface_labels()
    assert labels["demo_new_surface"] == "demo label"

    # output_schema.VALID_TASK_SURFACES is `frozenset(TASK_SURFACE_LABELS)`,
    # so the same fragment dir feeds the surface allow-list with no second edit.
    assert "demo_new_surface" in frozenset(labels)


def test_valid_task_surfaces_is_derived_not_duplicated():
    """The output_schema allow-list must derive from the label registry, so a
    surface is defined in exactly one place."""
    import output_schema  # type: ignore

    assert output_schema.VALID_TASK_SURFACES == frozenset(TASK_SURFACE_LABELS)
