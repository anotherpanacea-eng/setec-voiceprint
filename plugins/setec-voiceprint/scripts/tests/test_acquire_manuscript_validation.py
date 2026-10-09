#!/usr/bin/env python3
"""Validation + manifest-shaping tests for acquire_manuscript.

bs4-free (imports only acquire_manuscript + acquisition_core), so these run in
core CI where the acquisition extras are absent — unlike the EPUB-fixture tests
in test_acquire_manuscript.py, which skip without bs4."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

import acquire_manuscript as am  # type: ignore  # noqa: E402
import acquisition_core as ac  # type: ignore  # noqa: E402


def test_impostor_role_requires_impostor_for():
    # --corpus-role impostor without --impostor-for must error out (exit 2),
    # before any filesystem work.
    with pytest.raises(SystemExit) as ei:
        am.main(["src.txt", "--persona", "p", "--register", "r",
                 "--corpus-role", "impostor"])
    assert ei.value.code == 2


def test_identity_role_does_not_require_impostor_for():
    # The guard must NOT fire for the default identity_baseline role: parsing
    # succeeds (the run later fails on the missing source, but not on the guard).
    args = am.build_arg_parser().parse_args(
        ["src.txt", "--persona", "p", "--register", "r"])
    assert args.corpus_role == "identity_baseline"
    assert args.impostor_for == []


def test_era_preserved_for_identity_baseline(tmp_path):
    opts = am.ProcessOptions(
        persona="me", author="Me", register="literary_horror",
        corpus_role="identity_baseline", use=["voice_profile"],
        ai_status="pre_ai_human", consent_status="author_consent",
        era="pre_chatgpt", impostor_for=[], register_match="high",
        topic_match="medium", output_dir=tmp_path,
        manifest_path=tmp_path / "m.jsonl", max_items=10, dry_run=False,
        allow_non_prose=False, strip_rules=None, strip_aggressive=False,
        acquired_via="test", segment="work", window_words=2500, min_words=10,
    )
    piece = ac.AcquiredPiece(
        title="Ch1", author="Me", persona="me", register="literary_horror",
        date_written=dt.date(2019, 1, 1), source_url="loc",
        cleaned_text="word " * 200, raw_byte_length=1000, preprocessing_meta={},
        acquired_via="test", consent_status="author_consent", era="pre_chatgpt",
        register_match="high", topic_match="medium", impostor_for=[], notes="",
    )
    item = am.ItemMeta(locator="loc", title="Ch1", author="Me",
                       date=dt.date(2019, 1, 1), extra={})
    am.emit_piece(piece, item, options=opts, summary=ac.RunSummary())
    entry = json.loads((tmp_path / "m.jsonl").read_text().strip().splitlines()[-1])
    assert entry["corpus_role"] == "identity_baseline"
    assert entry["era"] == "pre_chatgpt"  # not dropped for identity entries


def test_since_until_flags_removed():
    # The inert --since/--until flags were dropped; argparse must now reject them.
    with pytest.raises(SystemExit):
        am.build_arg_parser().parse_args(
            ["src.txt", "--persona", "p", "--register", "r", "--since", "2020"])


@pytest.mark.parametrize("gap", ["\n\n", "\r\n\r\n", "\n \t\n", "\n\n\n\n"])
def test_word_windows_preserve_paragraphs_without_moving_word_cuts(gap):
    text = f"  one\ttwo{gap}three\nfour five{gap}six seven  "
    for size in (1, 2, 3, 5, 7, 10):
        windows = am._window_split(text, size)
        words = text.split()
        assert [window.split() for window in windows] == [
            words[i:i + size] for i in range(0, len(words), size)
        ]
        assert all(window and window == window.strip() for window in windows)
    assert am._window_split(text, 3) == ["one two\n\nthree", "four five\n\nsix", "seven"]
    assert am._window_split(text, 2) == ["one two", "three four", "five\n\nsix", "seven"]


def test_word_windows_keep_unaffected_whitespace_and_unicode_tokenization():
    assert am._window_split("\n \t\r\n", 3) == []
    text = "café\u00a0alpha\tbeta\ngamma  delta"
    assert am._window_split(text, 3) == ["café alpha beta", "gamma delta"]


@pytest.mark.parametrize("mode", ["chapter", "window"])
@pytest.mark.parametrize("heading", ["", "# One\n\n"])
def test_markdown_fallback_and_plaintext_keep_paragraph_boundaries(mode, heading):
    assert am._segment_markdown(heading + "one two\n\nthree four", mode, 10) == [
        heading.replace("# ", "").strip() + "\n\none two\n\nthree four"
        if heading else "one two\n\nthree four"
    ]


def test_plaintext_work_and_genuine_markdown_chapters_keep_existing_output():
    text = " one two\n\nthree four "
    for mode in ("chapter", "window"):
        assert am._segment_plaintext(text, mode, 10) == [text.strip()]
    assert am._segment_plaintext(text, "work", 10) == [text]
    chapters = "# First\n\nOne paragraph.\n\n# Second\n\nAnother paragraph."
    assert am._segment_markdown(chapters, "chapter", 10) == [
        "First\n\nOne paragraph.", "Second\n\nAnother paragraph."
    ]


def test_docx_windows_keep_semantic_paragraphs_without_changing_other_modes(tmp_path):
    paragraphs = [("First", True), ("one two", False), ("three four", False),
                  ("Second", True), ("five six", False)]
    body = "".join(
        '<w:p>' + ('<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if heading else '')
        + '<w:r><w:t>' + escape(text) + '</w:t></w:r></w:p>'
        for text, heading in paragraphs
    )
    path = tmp_path / "synthetic.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="'
                         'http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         '<w:body>' + body + '</w:body></w:document>')
    assert am._segment_docx(path, "window", 4) == [
        "First\n\none two\n\nthree", "four\n\nSecond\n\nfive six"
    ]
    assert am._segment_docx(path, "work", 4) == ["First\none two\nthree four\nSecond\nfive six"]
    assert am._segment_docx(path, "chapter", 4) == ["First\none two\nthree four", "Second\nfive six"]
    # A single real heading retains the existing fallback to word windows.
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<document><p><t>one two</t></p>'
                         '<p><t>three four</t></p></document>')
    assert am._segment_docx(path, "chapter", 10) == ["one two\n\nthree four"]


@pytest.mark.parametrize("text", [
    "file_name_here", "file__name__here", "file___name___here", "α_β_γ", "1_2_3",
    "_unfinished", "unfinished_", "_one__ and __two_", "____word____",
])
def test_markdown_keeps_literal_and_unbalanced_underscore_runs(text):
    assert am._strip_markdown(text) == text


@pytest.mark.parametrize("marker", ["_", "__", "___"])
def test_markdown_strips_balanced_underscore_emphasis_only(marker):
    text = f"file_name_here and {marker}file__name_here{marker} plus {marker}word{marker}."
    assert am._strip_markdown(text) == "file_name_here and file__name_here plus word."


def test_markdown_strips_asterisk_emphasis_around_identifiers():
    assert am._strip_markdown("**file_name_here** and *x_y* then *z*.") == "file_name_here and x_y then z."


def test_markdown_other_existing_markup_still_strips():
    text = "# Title\n\n*one* **two** ***three*** a*b*c [four](https://example.org).\n" \
           "![picture](image.png)\n> quoted\n```\ncode\n```"
    assert am._strip_markdown(text) == "Title\n\none two three abc four.\n\nquoted"


def test_corrected_manuscript_bytes_reach_stored_text_and_content_identity(tmp_path):
    paragraph = "She opened file_name_here and watched the rain beyond the window. " * 5
    markdown = paragraph.strip() + "\n\n" + paragraph.strip()
    source = tmp_path / "synthetic.md"
    source.write_text(markdown, encoding="utf-8")
    output = tmp_path / "identity"
    manifest = output / "manifest.jsonl"
    assert am.main([str(source), "--persona", "synthetic_writer", "--author", "Synthetic",
                    "--register", "literary_fiction", "--consent-status", "author_consent",
                    "--ai-status", "ai_assisted", "--segment", "chapter", "--min-words", "10",
                    "--output-dir", str(output), "--emit-manifest", str(manifest),
                    "--allow-public-output"]) == 0
    entries = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    stored = (manifest.parent / entries[0]["path"]).read_bytes()
    assert b"\n\n" in stored and b"file_name_here" in stored
    assert entries[0]["content_hash"] == "sha256:" + hashlib.sha256(stored).hexdigest()
