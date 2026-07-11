#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the stance baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own stance profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

PR #307 Codex review (whole-cleaned-text, NOT the token stream): the fingerprint is sha256 over the
WHOLE ``strip_non_prose``-cleaned text. An earlier lowercased-``_WORD_RE`` fingerprint folded
punctuation/case and would OVER-EXCLUDE a baseline differing only in punctuation — which the multi-word
markers matched with ``\\s+`` (e.g. ``ought\\s+to``) score differently — silently changing the
reference corpus. The cleaned-text hash drops only an EXACT cleaned-text copy (incl. one wrapped in
stripped front matter) and KEEPS any baseline the audit scores differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import stance_modality_audit as sma  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


TARGET = (
    "Perhaps this is right, though I suspect the truth is subtler. "
    "Clearly the evidence points one way, but arguably it could point another. "
    "We ought to proceed, and we need to move more or less at once."
) * 5
OTHER = (
    "The cart rolled down the lane and stopped beside the well. "
    "A dog barked twice and went quiet. The afternoon stretched long and "
    "flat over the fields, and nothing at all seemed likely to change."
) * 5
# Same words, a comma inserted mid-marker ("ought to" -> "ought, to", "need to" -> "need, to"): the
# old token-stream fingerprint collapsed it into the target; the markers now match differently, so it
# is a distinct baseline that must be KEPT.
PUNCT_VARIANT = TARGET.replace("ought to", "ought, to").replace("need to", "need, to")
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return sma._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return sma.audit_baseline_stance(
        str(bdir), target_fingerprint=_fp(), include_filenames=True,
    )


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")
    names = _names(_run(bdir))
    assert "sneaky_copy.txt" not in names
    assert "genuine.txt" in names


def test_front_matter_wrapped_copy_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "disguised.txt").write_text(FRONT_MATTER_COPY, encoding="utf-8")
    names = _names(_run(bdir))
    assert "disguised.txt" not in names
    assert "genuine.txt" in names


def test_punctuation_variant_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(PUNCT_VARIANT, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    names = _names(_run(bdir))
    assert "variant.txt" in names           # NOT over-excluded
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
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = sma.audit_baseline_stance(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
