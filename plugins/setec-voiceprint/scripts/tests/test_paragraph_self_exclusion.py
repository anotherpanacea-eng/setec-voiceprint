#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target planted in the paragraph baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is built. Otherwise the target
pulls its own paragraph-rhythm profile into its own baseline, deflating every z-score toward a false
"in-distribution" result. The path-only guard misses a copy at a different path; the
content-fingerprint guard closes it.

The fingerprint is sha256 over the WHOLE ``strip_non_prose``-cleaned text (this surface's signal is
paragraph + sentence STRUCTURE — no token stream carries it). PR #307 review aligned it to the CLEANED
scoring input (not raw), so a copy wrapped in stripped front matter is caught; it drops only an exact
cleaned-text copy and keeps any text the audit segments/scores differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paragraph_audit as pa  # type: ignore
from preprocessing import strip_non_prose  # type: ignore


def _doc(seed: str) -> str:
    paras = [
        f"{seed} opening paragraph that runs on for a good while so the "
        f"segmentation has something real to chew on and measure here.",
        f"{seed} a shorter second block, still several words long.",
        f"{seed} the third and final paragraph closes the little document "
        f"with a clause or two more and then it simply stops.",
    ]
    return "\n\n".join(paras)


TARGET = _doc("Alpha")
OTHER = _doc("Bravo")
FRONT_MATTER_COPY = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{TARGET}"


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _fp() -> str:
    return pa._content_fingerprint(_cleaned(TARGET))


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _run(bdir):
    return pa.audit_baseline_paragraphs(
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
    (bdir / "b.txt").write_text(_doc("Charlie"), encoding="utf-8")
    assert _names(_run(bdir)) == {"a.txt", "b.txt"}


def test_unicode_composition_variant_not_over_excluded(tmp_path):
    # Dropping the prior NFC fold: an NFD copy of the target is a DISTINCT cleaned
    # scoring input (the word tokenizer splits the accented words differently), so
    # it must be KEPT, not over-collapsed into the target and excluded.
    import unicodedata

    nfc = unicodedata.normalize("NFC", _doc("Café résumé naïve façade"))
    nfd = unicodedata.normalize("NFD", nfc)
    assert _cleaned(nfc).encode("utf-8") != _cleaned(nfd).encode("utf-8")
    assert pa.word_count(_cleaned(nfc)) != pa.word_count(_cleaned(nfd))
    assert pa._content_fingerprint(_cleaned(nfc)) != pa._content_fingerprint(_cleaned(nfd))

    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "nfd_variant.txt").write_text(nfd, encoding="utf-8")
    block = pa.audit_baseline_paragraphs(
        str(bdir),
        target_fingerprint=pa._content_fingerprint(_cleaned(nfc)),
        include_filenames=True,
    )
    assert _names(block) == {"genuine.txt", "nfd_variant.txt"}


def test_no_fingerprint_is_backward_compatible(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    block = pa.audit_baseline_paragraphs(str(bdir), include_filenames=True)
    assert _names(block) == {"copy.txt"}
