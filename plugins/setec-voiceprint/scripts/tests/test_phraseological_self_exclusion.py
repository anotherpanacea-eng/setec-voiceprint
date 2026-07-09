#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the baseline dir under a
DIFFERENT filename must be dropped from the baseline BEFORE phrase-frame mining. Otherwise the target
pools its own frames into its own baseline, inflating every reuse / hapax-survival rate toward a false
"on-frame" result. The path-only guard misses a copy at a different path; the content-fingerprint
guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint is matcher-aligned: phraseology mines n-grams / frames
over ``_tokenize`` (lowercased ``[A-Za-z][A-Za-z'’-]*``), so the fingerprint is sha256 over that same
token stream — a case/punctuation/whitespace variant of the target is ``_tokenize``-equivalent and is
self-excluded (fail-closed); a genuinely different doc has a different token stream and is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import phraseological_signature_audit as psa  # type: ignore


TARGET = " ".join(f"alpha{i % 41} beta{i % 29}" for i in range(120))
OTHER = " ".join(f"gamma{i % 41} delta{i % 29}" for i in range(120))


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


def test_case_and_punctuation_variant_is_matcher_equivalent_and_excluded(tmp_path):
    # _tokenize lowercases and drops punctuation, so an upper-cased, re-punctuated copy is
    # frame-equivalent to the target and must be self-excluded (fail-closed).
    variant = (TARGET.upper() + " !!! ... ---").replace(" ", ",  ")
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "variant.txt").write_text(variant, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = psa._content_fingerprint(TARGET)
    texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
    names = {p.name for p in loaded}
    assert "variant.txt" not in names
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
    # Without a target fingerprint, nothing is content-excluded (path guard still applies elsewhere).
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    texts, loaded, skipped = psa._walk_baseline(bdir, None)
    assert {p.name for p in loaded} == {"copy.txt"}
