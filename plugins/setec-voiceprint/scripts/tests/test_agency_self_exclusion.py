#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the agency baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own agency/abstraction profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

PR #307 Codex review (whole-cleaned-text, NOT the token stream): the fingerprint is sha256 over the
WHOLE ``strip_non_prose``-cleaned text. An earlier case-preserved-``_WORD_RE`` fingerprint dropped
punctuation and would OVER-EXCLUDE a baseline differing only in punctuation — which the ``\\s+``-joined
passive/light-verb regexes score differently — silently changing the reference corpus. The cleaned-text
hash drops only an EXACT cleaned-text copy (incl. one wrapped in stripped front matter) and KEEPS any
baseline the audit scores differently, including a re-cased one (proper-noun rate is case-sensitive).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agency_abstraction_audit as aaa  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


TARGET = (
    "The transfer was approved by the board and the plan was completed on time. "
    "Katherine Powell reviewed it. The Committee accepted the resolution in March "
    "and the report was filed before the deadline had passed."
) * 5
OTHER = (
    "Something was decided somewhere by someone, and the matter was closed. "
    "A wall was painted. A field was mown. The results were tabulated quietly "
    "and the whole business was forgotten before the week was out."
) * 5
# Same words, a comma inserted after each "was" ("was approved" -> "was, approved"): the old
# case-preserved token-stream fingerprint dropped the comma and collapsed it into the target; the
# passive regex (`was\s+approved`) no longer matches, so it is a distinct baseline that must be KEPT.
PUNCT_VARIANT = TARGET.replace("was ", "was, ")
# A re-cased copy: proper-noun rate is case-sensitive, so it scores differently and must be KEPT.
RECASED = TARGET.lower()
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return aaa._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return aaa.audit_baseline_agency(
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
    assert "variant.txt" in names
    assert "genuine.txt" in names


def test_recased_document_is_kept_case_is_a_signal(tmp_path):
    # Agency's proper-noun rate is case-SENSITIVE: an all-lowercase copy scores a different profile
    # and is a genuinely different document, so it must NOT be over-excluded.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "lowercased.txt").write_text(RECASED, encoding="utf-8")
    assert "lowercased.txt" in _names(_run(bdir))


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "William Hart signed the deed. Chicago confirmed the arrangement. "
        "The Bureau documented the transaction and archived the correspondence." * 5,
        encoding="utf-8",
    )
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}
