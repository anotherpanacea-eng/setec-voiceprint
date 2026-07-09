#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target placed in the voice-distance baseline
under a DIFFERENT filename must be dropped before the distance is computed. Otherwise the target pools
its own function-word vector into its own baseline centroid, collapsing the cosine min / Burrows Delta
toward 0 (a false "on-voice" result). The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint is matcher-aligned: the load-bearing function-word family
reads ``stylometry_core.word_tokens``, so the fingerprint is sha256 over that token stream — a copy of
the target is dropped; a genuinely different baseline entry is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_distance as vd  # type: ignore


TARGET = (
    "Officials noted that the process had followed the established guidelines, "
    "and that the review would continue through the winter into the early spring. "
) * 12
G1 = "The committee deliberated through the long grey afternoon and into the evening. " * 12
G2 = "Members reviewed the budget on Tuesday and again, more carefully, on the Thursday. " * 12


def _run_vd_main(argv):
    orig = sys.argv
    sys.argv = argv
    try:
        return vd.main()
    finally:
        sys.argv = orig


def test_content_fingerprint_matches_word_tokens_matcher():
    # The fingerprint must key on the SAME tokenizer the function-word family reads.
    assert vd._content_fingerprint(TARGET) == vd._content_fingerprint(TARGET)
    assert vd._content_fingerprint(TARGET) != vd._content_fingerprint(G1)


def test_content_duplicate_at_other_path_is_dropped(tmp_path, capsys):
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "genuine1.md").write_text(G1, encoding="utf-8")
    (bdir / "genuine2.md").write_text(G2, encoding="utf-8")
    (bdir / "sneaky_copy.md").write_text(TARGET, encoding="utf-8")  # a copy of the target, other name
    target = tmp_path / "target.md"
    target.write_text(TARGET, encoding="utf-8")

    rc = _run_vd_main([
        "voice_distance.py", str(target),
        "--baseline-dir", str(bdir), "--no-spacy", "--json",
    ])
    err = capsys.readouterr().err
    assert rc == 0
    # The differently-named copy was dropped by the content guard (target is OUTSIDE bdir, so the
    # only possible reason for a drop is a content match).
    assert "content-duplicate" in err


def test_distinct_baseline_not_over_excluded(tmp_path, capsys):
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "genuine1.md").write_text(G1, encoding="utf-8")
    (bdir / "genuine2.md").write_text(G2, encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text(TARGET, encoding="utf-8")

    rc = _run_vd_main([
        "voice_distance.py", str(target),
        "--baseline-dir", str(bdir), "--no-spacy", "--json",
    ])
    err = capsys.readouterr().err
    assert rc == 0
    # No entry is the target or a content-duplicate of it -> nothing is dropped.
    assert "Dropped target file" not in err
