#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the stance baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own stance profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint is matcher-aligned: every stance / modality marker in
``_PATTERNS`` is matched case-INSENSITIVELY (``re.IGNORECASE``), so the fingerprint is sha256 over the
LOWERCASED ``_WORD_RE`` word stream — a re-cased / re-punctuated copy is marker-equivalent and is
self-excluded (fail-closed); a genuinely different doc has a different token stream and is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import stance_modality_audit as sma  # type: ignore


TARGET = (
    "Perhaps this is right, though I suspect the truth is subtler. "
    "Clearly the evidence points one way, but arguably it could point another. "
    "It may be that we are wrong; it might also be that we are only partly so."
) * 5
OTHER = (
    "The cart rolled down the lane and stopped beside the well. "
    "A dog barked twice and went quiet. The afternoon stretched long and "
    "flat over the fields, and nothing at all seemed likely to change."
) * 5


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = sma._content_fingerprint(TARGET)
    block = sma.audit_baseline_stance(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert block["n_files"] == 1


def test_recased_and_repunctuated_variant_excluded(tmp_path):
    # markers are matched case-insensitively, so an upper-cased, re-punctuated copy is
    # stance-equivalent to the target and must be self-excluded (fail-closed).
    variant = TARGET.upper().replace(".", " .. ").replace(",", " ;; ")
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(variant, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = sma._content_fingerprint(TARGET)
    block = sma.audit_baseline_stance(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "variant.txt" not in names
    assert "genuine.txt" in names


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "This is certain and requires no hedging. The result is definite. "
        "We assert it plainly and move on without qualification of any kind." * 5,
        encoding="utf-8",
    )
    fp = sma._content_fingerprint(TARGET)
    block = sma.audit_baseline_stance(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert _names(block) == {"a.txt", "b.txt"}
    assert block["n_files"] == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = sma.audit_baseline_stance(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
