#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the punctuation baseline
dir under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the
target pulls its own cadence into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

The fingerprint is sha256 over the WHOLE ``strip_non_prose``-cleaned text (this surface's signal IS
punctuation over the raw character sequence — no token stream carries it). PR #307 review aligned it to
the CLEANED scoring input (not raw), so a copy wrapped in stripped front matter is caught; it drops
only an exact cleaned-text copy and keeps any text the audit scores differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import punctuation_cadence_audit as pca  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


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
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return pca._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return pca.audit_baseline_punctuation(
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
    assert _run(bdir)["n_files"] == 1


def test_front_matter_wrapped_copy_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "disguised.txt").write_text(FRONT_MATTER_COPY, encoding="utf-8")
    names = _names(_run(bdir))
    assert "disguised.txt" not in names     # stripped to the target's cleaned text -> dropped
    assert "genuine.txt" in names


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        "Plain declarative prose. No dashes. No parentheses. Short sentences. "
        "Every line ends with a period and nothing else at all happens here." * 4,
        encoding="utf-8",
    )
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}


def test_unicode_composition_variant_not_over_excluded(tmp_path):
    # Dropping the prior NFC fold: an NFD copy of the target is a DISTINCT cleaned
    # scoring input — this surface's word tokenizer splits the accented words
    # differently (per-thousand densities shift), so the audit scores it
    # differently. The old NFC-folded fingerprint over-collapsed the two; the
    # verbatim cleaned-text fingerprint keeps the variant.
    import unicodedata

    accented = (
        "The café — too quiet, perhaps; nobody spoke. She left her résumé "
        "(naïve, unsigned) on the façade. Then: a knock! Who could it be?"
    ) * 4
    nfc = unicodedata.normalize("NFC", accented)
    nfd = unicodedata.normalize("NFD", nfc)
    assert _cleaned(nfc).encode("utf-8") != _cleaned(nfd).encode("utf-8")
    assert pca._word_count(_cleaned(nfc)) != pca._word_count(_cleaned(nfd))
    assert pca._content_fingerprint(_cleaned(nfc)) != pca._content_fingerprint(_cleaned(nfd))

    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "nfd_variant.txt").write_text(nfd, encoding="utf-8")
    block = pca.audit_baseline_punctuation(
        str(bdir),
        target_fingerprint=pca._content_fingerprint(_cleaned(nfc)),
        include_filenames=True,
    )
    assert _names(block) == {"genuine.txt", "nfd_variant.txt"}


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = pca.audit_baseline_punctuation(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
