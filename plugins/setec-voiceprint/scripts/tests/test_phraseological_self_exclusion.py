#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the baseline dir under a
DIFFERENT filename must be dropped before phrase-frame mining. Otherwise the target pools its own
frames into its own baseline, inflating every reuse / hapax-survival rate toward a false "on-frame"
result. The path-only guard misses a copy at a different path; the content-fingerprint guard closes it.

PR #307 Codex review (whole-text, NOT the ``_tokenize`` stream): the fingerprint is sha256 over the
exact string ``audit_phraseology`` scores. An earlier ``_tokenize`` fingerprint folded case and dropped
punctuation and would OVER-EXCLUDE a baseline differing only in those — which the punctuation-/
case-sensitive slot-frame templates (literal ``,``/``;``, capitalized ``What``) score differently —
silently changing the reference corpus. The scored string drops only an EXACT copy and KEEPS any
baseline the audit scores differently. The scored input tracks ``keep_quotes``: under the default the
audit strips blockquote lines before tokenizing, so the fingerprint strips them too (a raw-text hash
under-excluded a quote-wrapped copy the audit scores identically); with ``--keep-quotes`` the quote
lines are scored, so a quote-bearing variant is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import phraseological_signature_audit as psa  # type: ignore


TARGET = " ".join(f"alpha{i % 41} beta{i % 29}" for i in range(120)) + ". What matters is the plan."
OTHER = " ".join(f"gamma{i % 41} delta{i % 29}" for i in range(120)) + ". Consider the river instead."
# Same words, punctuation/case changed: the old _tokenize fingerprint collapsed it into the target;
# the whole-text fingerprint keeps it (a distinct scoring input to the slot-frame templates).
PUNCT_VARIANT = TARGET.replace(".", "?").replace("What matters", "what matters")
# A copy of the target with an added blockquote line. Under the default keep_quotes=False the audit
# strips ``>`` lines before scoring, so this has the SAME scored input as the target.
BLOCKQUOTE_COPY = "> a quoted line the audit strips before scoring\n" + TARGET


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = psa._content_fingerprint(TARGET)
    texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
    names = {p.name for p in loaded}
    assert "sneaky_copy.txt" not in names          # the target's own copy is dropped
    assert "genuine.txt" in names                  # the genuinely-different doc is kept
    assert any(p.name == "sneaky_copy.txt" for p in skipped)


def test_punctuation_and_case_variant_not_over_excluded(tmp_path):
    # PR #307 regression: the old _tokenize fingerprint dropped this; whole-text keeps it.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(PUNCT_VARIANT, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = psa._content_fingerprint(TARGET)
    texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
    names = {p.name for p in loaded}
    assert "variant.txt" in names                  # NOT over-excluded
    assert "genuine.txt" in names


def test_blockquote_variant_copy_is_excluded(tmp_path):
    # Under default keep_quotes=False the audit strips blockquote lines before scoring, so a copy of
    # the target with added ``>`` lines has the SAME scored input and must be dropped. A raw-text
    # fingerprint under-excluded it (different bytes, identical scoring input); the scored-input
    # fingerprint closes that gap.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "quoted_copy.txt").write_text(BLOCKQUOTE_COPY, encoding="utf-8")
    fp = psa._content_fingerprint(TARGET)  # default keep_quotes=False
    texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
    names = {p.name for p in loaded}
    assert "quoted_copy.txt" not in names          # same scored input -> dropped
    assert "genuine.txt" in names
    assert any(p.name == "quoted_copy.txt" for p in skipped)


def test_blockquote_variant_kept_with_keep_quotes(tmp_path):
    # With --keep-quotes the audit scores the quote lines, so the quote-bearing variant is a DISTINCT
    # scored input and must be KEPT. The fingerprint tracks keep_quotes on both sides.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "quoted_copy.txt").write_text(BLOCKQUOTE_COPY, encoding="utf-8")
    fp = psa._content_fingerprint(TARGET, keep_quotes=True)
    texts, loaded, skipped = psa._walk_baseline(
        bdir, None, target_fingerprint=fp, keep_quotes=True,
    )
    names = {p.name for p in loaded}
    assert "quoted_copy.txt" in names              # distinct scored input under keep_quotes -> kept
    assert "genuine.txt" in names


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(" ".join(f"epsilon{i}" for i in range(120)), encoding="utf-8")
    fp = psa._content_fingerprint(TARGET)
    texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
    assert {p.name for p in loaded} == {"a.txt", "b.txt"}
    assert len(texts) == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    texts, loaded, skipped = psa._walk_baseline(bdir, None)
    assert {p.name for p in loaded} == {"copy.txt"}
