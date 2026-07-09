#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target placed in the voice-distance baseline
under a DIFFERENT filename must be dropped before the distance is computed. Otherwise the target pools
its own function-word vector into its own baseline centroid, collapsing the cosine min / Burrows Delta
toward 0 (a false "on-voice" result). The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint is matcher-aligned to ALL scored families, not just the
function-word tokenizer: it is sha256 over the WHOLE ``strip_non_prose``-cleaned text — the single
string every family (function words, char n-grams, POS, dependencies) reads before its own
normalization. So the equivalence class is a strict SUBSET of every family's class: an exact copy (even
one wrapped in front matter the preprocessing strips) is dropped, while a punctuation-/case-distinct
baseline the char-n-gram / POS families treat as distinct is KEPT rather than over-excluded (PR #307).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_distance as vd  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


TARGET = (
    "Officials noted that the process had followed the established guidelines, "
    "and that the review would continue through the winter into the early spring. "
) * 12
G1 = "The committee deliberated through the long grey afternoon and into the evening. " * 12
G2 = "Members reviewed the budget on Tuesday and again, more carefully, on the Thursday. " * 12

# Same words as TARGET with the sentence punctuation removed. The char-n-gram / POS families
# score this differently from TARGET, so the guard must NOT collapse it into the target (PR #307).
TARGET_NO_PUNCT = TARGET.replace(",", "").replace(".", "")
# An exact copy of TARGET wrapped in YAML front matter under a different apparent identity. The
# default strip rules remove the front matter, so the cleaned scoring input is identical to the
# target's and the entry must be dropped — self-exclusion computed on the preprocessed input.
TARGET_WITH_FRONT_MATTER = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    cleaned, _ = strip_non_prose(text, None)
    return cleaned


def _run_vd_main(argv):
    orig = sys.argv
    sys.argv = argv
    try:
        return vd.main()
    finally:
        sys.argv = orig


def test_content_fingerprint_keys_on_whole_cleaned_text():
    # Fingerprint is over the cleaned string itself, so it is stable and distinguishes distinct texts.
    assert vd._content_fingerprint(_cleaned(TARGET)) == vd._content_fingerprint(_cleaned(TARGET))
    assert vd._content_fingerprint(_cleaned(TARGET)) != vd._content_fingerprint(_cleaned(G1))


def test_fingerprint_does_not_collapse_punctuation_variants():
    # PR #307: a word-only fingerprint folded punctuation and treated these as identical, dropping a
    # baseline the actual matcher considers distinct. The whole-cleaned-text fingerprint keeps them apart.
    assert _cleaned(TARGET) != _cleaned(TARGET_NO_PUNCT)
    assert vd._content_fingerprint(_cleaned(TARGET)) != vd._content_fingerprint(_cleaned(TARGET_NO_PUNCT))


def test_front_matter_copy_shares_target_fingerprint():
    # Self-exclusion is computed on the preprocessed input: front matter is stripped, so an exact copy
    # wrapped in front matter has the same cleaned string as the target and the same fingerprint.
    assert vd._content_fingerprint(_cleaned(TARGET_WITH_FRONT_MATTER)) == vd._content_fingerprint(_cleaned(TARGET))


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


def test_front_matter_copy_at_other_path_is_dropped(tmp_path, capsys):
    # PR #307 / #306 alignment: a copy that differs only in stripped front matter is still a copy once
    # preprocessing runs, so it must be dropped — the guard fingerprints the cleaned scoring input.
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "genuine1.md").write_text(G1, encoding="utf-8")
    (bdir / "disguised_copy.md").write_text(TARGET_WITH_FRONT_MATTER, encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text(TARGET, encoding="utf-8")

    rc = _run_vd_main([
        "voice_distance.py", str(target),
        "--baseline-dir", str(bdir), "--no-spacy", "--json",
    ])
    err = capsys.readouterr().err
    assert rc == 0
    assert "content-duplicate" in err


def test_punctuation_variant_baseline_not_over_excluded(tmp_path, capsys):
    # PR #307 regression: a baseline that is the target with punctuation removed is a genuinely distinct
    # document to the char-n-gram / POS families and must survive the guard (not dropped as a duplicate).
    bdir = tmp_path / "baseline"
    bdir.mkdir()
    (bdir / "genuine1.md").write_text(G1, encoding="utf-8")
    (bdir / "punct_variant.md").write_text(TARGET_NO_PUNCT, encoding="utf-8")
    target = tmp_path / "target.md"
    target.write_text(TARGET, encoding="utf-8")

    rc = _run_vd_main([
        "voice_distance.py", str(target),
        "--baseline-dir", str(bdir), "--no-spacy", "--json",
    ])
    err = capsys.readouterr().err
    assert rc == 0
    # Neither baseline is the target or an exact-cleaned copy of it -> nothing dropped.
    assert "Dropped target file" not in err


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
