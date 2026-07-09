#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the productive-roughness
baseline dir under a DIFFERENT filename must be dropped before the per-feature mean/SD is built.
Otherwise the target pulls its own roughness rates into its own baseline, deflating every z-score
toward a false "in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The rates are per-SENTENCE and depend on segmentation + spaCy + words, so
the fingerprint is sha256 over the NFC-normalized WHOLE text: a byte-/NFC-identical copy is dropped,
and any text the surface would segment or score differently is KEPT.

Runs without spaCy: ``aggregate_baseline`` is called directly and ``extract_features`` degrades
gracefully (fragment / aside signals simply do not fire); the loader still loads / excludes / counts.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import productive_roughness_audit as pra  # type: ignore


TARGET = (
    "The kettle sang. She let it. Outside, a dog barked at nothing much. "
    "And then the rain, sudden and hard, drummed on the tin roof above. "
    "She didn't move. Couldn't, maybe. The moment held her where she sat."
) * 3
OTHER = (
    "The report concluded that the measures were adequate for the stated "
    "purpose. It recommended a review after twelve months. The committee "
    "accepted the recommendation and adjourned the meeting until the spring."
) * 3


def _names(stats):
    return {p.name for p in stats.files_loaded}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = pra._content_fingerprint(TARGET)
    stats = pra.aggregate_baseline(bdir, target_fingerprint=fp)
    names = _names(stats)
    assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
    assert "genuine.txt" in names           # the genuinely-different doc is kept
    assert stats.n_files == 1


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "A wholly different draft. Long, unbroken, careful sentences that "
        "never fragment and never lean on a contraction of any kind at all." * 3,
        encoding="utf-8",
    )
    fp = pra._content_fingerprint(TARGET)
    stats = pra.aggregate_baseline(bdir, target_fingerprint=fp)
    assert _names(stats) == {"a.txt", "b.txt"}
    assert stats.n_files == 2


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    stats = pra.aggregate_baseline(bdir)
    assert _names(stats) == {"copy.txt"}
