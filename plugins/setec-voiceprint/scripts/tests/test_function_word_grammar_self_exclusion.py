#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the baseline dir under a
DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target pulls
its own function-word vector into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

PR #307 Codex review (whole-cleaned-text, NOT the token stream): the fingerprint is sha256 over the
WHOLE ``strip_non_prose``-cleaned text — the exact scored input. An earlier ``_tokens_lower`` fingerprint
folded punctuation/case and would OVER-EXCLUDE a baseline that differs from the target only in
punctuation/case (which the run/sentence features score differently), silently changing the reference
corpus. The cleaned-text hash drops only an EXACT cleaned-text copy — including one wrapped in stripped
front matter — and KEEPS any baseline the audit would score differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import function_word_grammar_audit as fwg  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


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
# Same WORDS as TARGET, only the sentence punctuation changed. The old token-stream fingerprint
# collapsed this into the target (punctuation dropped) and would have dropped it; the run/sentence
# features score it differently, so it is a genuinely distinct baseline that must be KEPT.
PUNCT_VARIANT = TARGET.replace(".", "?")
# An exact copy wrapped in YAML front matter the preprocessing strips: same cleaned scoring input as
# the target, so it must be dropped.
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return fwg._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return fwg.audit_baseline_function_grammar(
        str(bdir), target_fingerprint=_fp(), include_filenames=True,
    )


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    names = _names(_run(bdir))
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept


def test_front_matter_wrapped_copy_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "disguised.txt").write_text(FRONT_MATTER_COPY, encoding="utf-8")
    names = _names(_run(bdir))
    assert "disguised.txt" not in names     # stripped to the same cleaned text -> dropped
    assert "genuine.txt" in names


def test_punctuation_variant_not_over_excluded(tmp_path):
    # PR #307 regression: the old token-stream fingerprint folded punctuation and dropped this;
    # the whole-cleaned-text fingerprint keeps it (a distinct scoring input).
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
        "A different voice entirely, terse and clipped, with none of the "
        "long clauses the others favored, only short blunt lines." * 4,
        encoding="utf-8",
    )
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = fwg.audit_baseline_function_grammar(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
