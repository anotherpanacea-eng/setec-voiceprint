"""Self-exclusion regression for aic_pattern_audit.

Bug (HIGH): ``list_baseline_paths`` / ``baseline_density`` took no target and had ZERO exclusion
guard, so a target present in ``--baseline-dir`` (same file, or a content-duplicate at a different
name) pools its OWN AIC-pattern hits into its own baseline density — understating the target's excess
over baseline (the whole point of the comparison).

Fix (sibling of the Codex self-exclusion sweep): a baseline path is dropped when its resolved path
equals the target's (path guard) OR its content fingerprint equals the target's (content guard).
The fingerprint is matcher-aligned: AIC density counts ``\\w+`` words and matches frames
case-insensitively, so the fingerprint is sha256 over the lowercased ``\\w+`` token stream — a
case/punctuation/whitespace variant of the target is AIC-equivalent and is self-excluded (fail-closed);
a genuinely different baseline doc is kept.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aic_pattern_audit as aic  # noqa: E402

# A passage dense in AIC frames so pooling it visibly moves baseline density.
TARGET = (
    "Not this. Not that. Research has shown that experts agree on the matter. "
    "We urge the committee to commit to reform. It is not a failure, but a lesson. "
    "There is a kind of clarity in restraint. Not loud. Not proud. "
    "Scholars have argued that the evidence is decisive and final."
)
OTHER = (
    "The cat sat quietly by the window while rain fell across the garden. "
    "She counted the drops and lost her place somewhere near the middle. "
    "Later the sky cleared and the street smelled of wet stone and leaves."
)

PATTERN_KEYS = list(aic.all_patterns(TARGET, aic.split_sentences(TARGET)).keys())


def test_baseline_density_excludes_content_duplicate(tmp_path):
    copy = tmp_path / "sneaky.txt"
    copy.write_text(TARGET, encoding="utf-8")   # a copy of the target under a different name
    other = tmp_path / "genuine.txt"
    other.write_text(OTHER, encoding="utf-8")
    fp = aic._content_fingerprint(TARGET)
    density, words, loaded, skipped, self_excluded = aic.baseline_density(
        [copy, other], PATTERN_KEYS, target_fingerprint=fp)
    assert copy not in loaded          # the target's own copy is not pooled
    assert other in loaded             # the genuinely-different doc is kept
    assert len(self_excluded) == 1


def test_baseline_density_excludes_case_punct_variant(tmp_path):
    # AIC density is case-insensitive and word-token based; a case/punctuation variant is
    # AIC-equivalent -> fail-closed self-exclusion.
    variant = tmp_path / "variant.txt"
    variant.write_text(TARGET.upper().replace(".", " . "), encoding="utf-8")
    fp = aic._content_fingerprint(TARGET)
    density, words, loaded, skipped, self_excluded = aic.baseline_density(
        [variant], PATTERN_KEYS, target_fingerprint=fp)
    assert variant not in loaded
    assert len(self_excluded) == 1


def test_baseline_density_keeps_distinct_doc(tmp_path):
    other = tmp_path / "genuine.txt"
    other.write_text(OTHER, encoding="utf-8")
    fp = aic._content_fingerprint(TARGET)
    density, words, loaded, skipped, self_excluded = aic.baseline_density(
        [other], PATTERN_KEYS, target_fingerprint=fp)
    assert other in loaded
    assert self_excluded == []


def test_list_baseline_paths_excludes_target_by_path(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    tgt = bdir / "target.md"
    tgt.write_text(TARGET, encoding="utf-8")
    (bdir / "genuine.md").write_text(OTHER, encoding="utf-8")
    paths = aic.list_baseline_paths(bdir, target_resolved=tgt.resolve())
    names = {p.name for p in paths}
    assert "target.md" not in names
    assert "genuine.md" in names


def test_end_to_end_self_exclusion_warns(tmp_path, capsys):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    tgt = tmp_path / "target.txt"
    tgt.write_text(TARGET, encoding="utf-8")
    rc = aic.main([str(tgt), "--baseline-dir", str(bdir), "--json"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "self-exclusion" in err.lower()
