#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the baseline dir under a
DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target pulls
its own function-word vector into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint is matcher-aligned: every compared feature is built over
``_tokens_lower`` (lowercased ``\\b\\w+\\b``), so the fingerprint is sha256 over that same token
stream — a case/punctuation/whitespace variant of the target is ``_tokens_lower``-equivalent and is
self-excluded (fail-closed); a genuinely different doc has a different token stream and is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import function_word_grammar_audit as fwg  # type: ignore


TARGET = (
    "The house on the hill was where they had lived for years, and it "
    "was there that the two of them first learned to be still. When the "
    "wind came through, it moved the curtains but not the quiet."
) * 4
OTHER = (
    "Beyond the harbor a ship waited under a grey sky, though nobody "
    "aboard could say whether it would sail. If the tide turned, they "
    "would go; if not, they would wait as they always had."
) * 4


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = fwg._content_fingerprint(TARGET)
    block = fwg.audit_baseline_function_grammar(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert block["n_files"] == 1


def test_case_and_punctuation_variant_excluded(tmp_path):
    # _tokens_lower lowercases + drops punctuation, so an upper-cased, re-punctuated copy is
    # feature-equivalent to the target and must be self-excluded (fail-closed).
    variant = TARGET.upper().replace(".", " ... ").replace(",", " ;; ")
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(variant, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = fwg._content_fingerprint(TARGET)
    block = fwg.audit_baseline_function_grammar(
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
        "A different voice entirely, terse and clipped, with none of the "
        "long clauses the others favored, only short blunt lines." * 4,
        encoding="utf-8",
    )
    fp = fwg._content_fingerprint(TARGET)
    block = fwg.audit_baseline_function_grammar(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert _names(block) == {"a.txt", "b.txt"}
    assert block["n_files"] == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = fwg.audit_baseline_function_grammar(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
