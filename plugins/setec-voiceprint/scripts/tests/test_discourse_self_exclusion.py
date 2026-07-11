#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the discourse baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own move profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

PR #307 Codex review (whole-cleaned-text, NOT the token stream): the fingerprint is sha256 over the
WHOLE ``strip_non_prose``-cleaned text. An earlier lowercased-``_WORD_RE`` fingerprint folded
punctuation/case and would OVER-EXCLUDE a baseline that differs only in punctuation/case — which the
per-sentence move classification (``_split_sentences`` / ``_SENTENCE_TERMINATORS``) scores differently
— silently changing the reference corpus. The cleaned-text hash drops only an EXACT cleaned-text copy
(incl. one wrapped in stripped front matter) and KEEPS any baseline the audit scores differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discourse_move_signature as dms  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


TARGET = (
    "However, the point is that the argument holds. Therefore we accept it. "
    "For example, consider the first case; that is, the simplest one. "
    "In other words, the claim is modest, though perhaps still contestable."
) * 5
OTHER = (
    "The river moved slowly under the bridge while the town slept on. "
    "No one watched it go. The lamps burned low and the streets stayed empty "
    "until a grey light crept in from the east and the birds began."
) * 5
PUNCT_VARIANT = TARGET.replace(".", "?")
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return dms._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return dms.audit_baseline_discourse(
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
    # PR #307 regression: the old token-stream fingerprint dropped this; whole-cleaned-text keeps it.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(PUNCT_VARIANT, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    names = _names(_run(bdir))
    assert "variant.txt" in names
    assert "genuine.txt" in names


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "A third voice, terse and declarative, states its claims and stops. "
        "It concedes nothing and connects little. Each line ends where it began." * 5,
        encoding="utf-8",
    )
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = dms.audit_baseline_discourse(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
