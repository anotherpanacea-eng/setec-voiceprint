#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the punctuation baseline
dir under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the
target pulls its own cadence into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). This surface's signal IS punctuation over the raw character sequence, so
the fingerprint is sha256 over the NFC-normalized WHOLE text (there is no word-token stream that
carries the cadence): a byte-/NFC-identical copy is dropped, and any text the surface would score
differently (different whitespace, punctuation, or wording) is KEPT.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import punctuation_cadence_audit as pca  # type: ignore


TARGET = (
    "The room was quiet — too quiet, perhaps; nobody spoke. She waited "
    "(as one does), counting the seconds. Then: a knock! Who could it be? "
    "The door opened slowly... and there he stood, dripping, silent, unsure."
) * 4
OTHER = (
    "Rain fell all day and the gutters ran full. The children stayed inside "
    "and read their books and drew their pictures and waited for the sun to "
    "come back out again over the long flat empty fields beyond the town."
) * 4


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = pca._content_fingerprint(TARGET)
    block = pca.audit_baseline_punctuation(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    names = _names(block)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert block["n_files"] == 1


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "Plain declarative prose. No dashes. No parentheses. Short sentences. "
        "Every line ends with a period and nothing else at all happens here." * 4,
        encoding="utf-8",
    )
    fp = pca._content_fingerprint(TARGET)
    block = pca.audit_baseline_punctuation(
        str(bdir), target_fingerprint=fp, include_filenames=True,
    )
    assert _names(block) == {"a.txt", "b.txt"}
    assert block["n_files"] == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = pca.audit_baseline_punctuation(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
