#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the paragraph baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own paragraph-rhythm profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). This surface's signal is paragraph + sentence STRUCTURE over the raw
text, so the fingerprint is sha256 over the NFC-normalized WHOLE text (no word-token stream carries
the structure): a byte-/NFC-identical copy is dropped, and any text the surface would segment or score
differently is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paragraph_audit as pa  # type: ignore


def _doc(seed: str) -> str:
    # Several blank-line-separated paragraphs, each well over the 3-word floor.
    paras = [
        f"{seed} opening paragraph that runs on for a good while so the "
        f"segmentation has something real to chew on and measure here.",
        f"{seed} a shorter second block, still several words long.",
        f"{seed} the third and final paragraph closes the little document "
        f"with a clause or two more and then it simply stops.",
    ]
    return "\n\n".join(paras)


TARGET = _doc("Alpha")
OTHER = _doc("Bravo")


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = pa._content_fingerprint(TARGET)
    block = pa.audit_baseline_paragraphs(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert block["n_files"] == 1


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(_doc("Charlie"), encoding="utf-8")
    fp = pa._content_fingerprint(TARGET)
    block = pa.audit_baseline_paragraphs(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert _names(block) == {"a.txt", "b.txt"}
    assert block["n_files"] == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = pa.audit_baseline_paragraphs(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
