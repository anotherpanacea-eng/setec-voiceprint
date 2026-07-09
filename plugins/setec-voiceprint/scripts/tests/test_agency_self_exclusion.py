#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the agency baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own agency/abstraction profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). Unlike the case-insensitive stance/discourse siblings, agency's
``_PROPER_NOUN_RE`` is a case-SENSITIVE primary signal, so the fingerprint hashes the CASE-PRESERVED
``_WORD_RE`` word stream verbatim: a byte/whitespace copy is dropped, but a re-cased document — which
scores a genuinely different proper-noun rate here — is KEPT (no over-exclusion).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agency_abstraction_audit as aaa  # type: ignore


TARGET = (
    "Katherine Powell approved the transfer. The Committee accepted her "
    "recommendation. Denver and Boston reported the resolution of the dispute. "
    "The organization completed its evaluation of the proposal in March."
) * 5
OTHER = (
    "Something was decided somewhere by someone, and the matter was closed. "
    "A wall was painted. A field was mown. The results were tabulated quietly "
    "and the whole business was forgotten before the week was out."
) * 5


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = aaa._content_fingerprint(TARGET)
    block = aaa.audit_baseline_agency(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert block["n_files"] == 1


def test_whitespace_reformatted_copy_excluded(tmp_path):
    # _WORD_RE tokenization folds whitespace/punctuation outside word tokens, so a re-wrapped copy
    # (same words, same case) is tokenization-equivalent and must be self-excluded (fail-closed).
    variant = TARGET.replace(" ", "\n  ").replace(".", ".\n\n")
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(variant, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = aaa._content_fingerprint(TARGET)
    block = aaa.audit_baseline_agency(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "variant.txt" not in names
    assert "genuine.txt" in names


def test_recased_document_is_kept_case_is_a_signal(tmp_path):
    # Agency's proper-noun rate is case-SENSITIVE: an all-lowercase version of the target scores a
    # very different profile and is a genuinely different document, so it must NOT be over-excluded.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "lowercased.txt").write_text(TARGET.lower(), encoding="utf-8")
    fp = aaa._content_fingerprint(TARGET)
    block = aaa.audit_baseline_agency(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert "lowercased.txt" in _names(block)


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "William Hart signed the deed. Chicago confirmed the arrangement. "
        "The Bureau documented the transaction and archived the correspondence." * 5,
        encoding="utf-8",
    )
    fp = aaa._content_fingerprint(TARGET)
    block = aaa.audit_baseline_agency(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert _names(block) == {"a.txt", "b.txt"}
    assert block["n_files"] == 2
