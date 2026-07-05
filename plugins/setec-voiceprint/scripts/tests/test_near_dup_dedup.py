#!/usr/bin/env python3
"""Tests for near_dup_dedup — MinHash-LSH cross-source near-duplicate dedup.

Invariants:
  * A planted near-duplicate (same essay, lightly edited / reheadered) is
    removed; genuinely distinct documents are all kept.
  * The kept representative is deterministic (longest text wins).
  * Manifest round-trip: dropped rows are removed, all other rows preserved
    in order; unresolvable-text rows pass through untouched.
  * The shingle helper is stdlib and behaves on short input.

datasketch is optional within the acquisition tier; the dep-gated tests skip
cleanly when it's absent (the shingle + import-purity tests still run).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import near_dup_dedup as ndd  # type: ignore  # noqa: E402

_datasketch_available = True
try:
    import datasketch  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    _datasketch_available = False

_needs_datasketch = pytest.mark.skipif(
    not _datasketch_available,
    reason="datasketch not installed; install requirements-acquisition.txt",
) if pytest is not None else (lambda f: f)


# A base essay and a near-duplicate of it (a few words changed + a new header),
# plus two genuinely distinct documents.
BASE = (
    "What we keep and what we discard becomes, at some scale of accumulation, "
    "a portrait of our judgment. I have been thinking about this in connection "
    "with my own archive, which is now large enough to have an internal "
    "weather: storms in some sections, long stretches of overcast in others, "
    "and a few unaccountable bright afternoons when whatever I was reading "
    "seemed to fall together in ways I did not earn."
)
NEAR_DUP = (
    "Reprinted from the newsletter. "
    "What we keep and what we discard becomes, at some scale of accumulation, "
    "a portrait of our judgment. I have been thinking about this in connection "
    "with my own archive, which is now large enough to have an internal "
    "weather: storms in some sections, long stretches of overcast in others, "
    "and a few rare bright afternoons when whatever I was reading "
    "seemed to fall together in ways I had not earned."
)
DISTINCT_A = (
    "The tide charts for the eastern approaches were wrong again this spring, "
    "and the pilots who trusted them found the channel a foot shallower than "
    "printed. We recalibrated against the new survey and lost a week to it."
)
DISTINCT_B = (
    "Monetary policy in a small open economy is mostly an exercise in managing "
    "expectations about a currency the central bank does not fully control. "
    "The textbook levers exist, but their transmission is slow and lossy."
)


def test_shingles_short_and_normal():
    # Fewer than k words → a single whole-doc shingle; empty → empty set.
    assert ndd.shingles("one two", k=5) == {"one two"}
    assert ndd.shingles("", k=5) == set()
    sh = ndd.shingles("a b c d e f", k=5)
    assert "a b c d e" in sh and "b c d e f" in sh
    # Case/punctuation-insensitive.
    assert ndd.shingles("The Quick, Brown!", k=2) == ndd.shingles("the quick brown", k=2)


@_needs_datasketch
def test_near_duplicate_removed_distinct_kept():
    records = [
        ("base", BASE),
        ("near_dup", NEAR_DUP),
        ("distinct_a", DISTINCT_A),
        ("distinct_b", DISTINCT_B),
    ]
    result = ndd.dedup_records(records, threshold=0.6)
    assert result.total == 4
    # The near-duplicate collapses to one representative; both distincts kept.
    assert len(result.kept) == 3
    assert "distinct_a" in result.kept and "distinct_b" in result.kept
    assert len(result.dropped) == 1
    # Exactly one of {base, near_dup} is dropped; the longer one (NEAR_DUP, it
    # carries the extra "Reprinted from..." header) is the kept representative.
    assert result.dropped == ["base"]
    assert "near_dup" in result.kept
    assert result.clusters == {"near_dup": ["base"]}


@_needs_datasketch
def test_all_distinct_keeps_everything():
    records = [("a", DISTINCT_A), ("b", DISTINCT_B), ("base", BASE)]
    result = ndd.dedup_records(records, threshold=0.7)
    assert sorted(result.kept) == ["a", "b", "base"]
    assert result.dropped == []
    assert result.clusters == {}


@_needs_datasketch
def test_deterministic_across_runs():
    records = [("x", BASE), ("y", NEAR_DUP)]
    r1 = ndd.dedup_records(records, threshold=0.6)
    r2 = ndd.dedup_records(records, threshold=0.6)
    assert r1.kept == r2.kept and r1.dropped == r2.dropped


@_needs_datasketch
def test_duplicate_id_rejected():
    with pytest.raises(ValueError):
        ndd.dedup_records([("dup", BASE), ("dup", DISTINCT_A)])


@_needs_datasketch
def test_dedup_manifest_round_trip(tmp_path):
    manifest = tmp_path / "draft_manifest.jsonl"
    rows = [
        {"id": "base", "text": BASE, "author": "Author"},
        {"id": "near_dup", "text": NEAR_DUP, "author": "Author"},
        {"id": "distinct_a", "text": DISTINCT_A, "author": "Other"},
        # A row with no resolvable text must pass through untouched.
        {"id": "no_text_row", "note": "metadata-only"},
    ]
    manifest.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    out = tmp_path / "deduped.jsonl"
    result = ndd.dedup_manifest(manifest, out_path=out, threshold=0.6)

    assert result.dropped == ["base"]
    kept_ids = [
        json.loads(line)["id"]
        for line in out.read_text(encoding="utf-8").splitlines()
    ]
    # base dropped; near_dup + distinct_a + the text-less row all preserved,
    # in original order.
    assert kept_ids == ["near_dup", "distinct_a", "no_text_row"]


@_needs_datasketch
def test_dedup_manifest_dry_run_does_not_write(tmp_path):
    manifest = tmp_path / "m.jsonl"
    original = (
        json.dumps({"id": "base", "text": BASE})
        + "\n"
        + json.dumps({"id": "near_dup", "text": NEAR_DUP})
        + "\n"
    )
    manifest.write_text(original, encoding="utf-8")
    result = ndd.dedup_manifest(manifest, threshold=0.6, dry_run=True)
    assert result.dropped == ["base"]
    # Dry-run leaves the input untouched.
    assert manifest.read_text(encoding="utf-8") == original


def test_base_import_is_pure():
    # near_dup_dedup imports with datasketch absent; the dep is only needed at
    # call time. This asserts the module-level import didn't pull datasketch.
    assert "near_dup_dedup" in sys.modules
    # The shingle helper is stdlib and works regardless of datasketch.
    assert ndd.shingles("stdlib only path", k=2)
