#!/usr/bin/env python3
"""Regression tests for acquire_epub.py.

Fixtures are synthetic EPUBs built at runtime (no third-party prose). The
builder writes a minimal but valid EPUB: META-INF/container.xml -> OPF
package document (Dublin Core metadata + manifest + reading-order spine)
-> XHTML content documents.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

_acq_deps_available = True
_skip_reason = ""
try:
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:  # pragma: no cover
    _acq_deps_available = False
    _skip_reason = (
        f"acquisition deps missing ({_e}); install with "
        "`pip install -r requirements-acquisition.txt`"
    )

if _acq_deps_available:
    import acquire_epub as ep  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


# --------------- synthetic EPUB builder --------------------------

# Original synthetic sentences (not drawn from any real work) used to pad
# chapters past the word floor with genuine prose so the hygiene gate passes.
_SENTENCES = [
    "The house at the end of the lane had been empty for as long as anyone remembered.",
    "She pressed her hand against the cold glass and waited for the light to change.",
    "Somewhere below the floorboards a slow and patient sound kept its own time.",
    "He counted the steps twice and still arrived at a different number each way.",
    "The orchard smelled of rain and rust and something older underneath it all.",
    "When the wind turned, the curtains moved as though the room were breathing.",
    "Nobody in town would say the name aloud after the second winter came.",
    "Her mother's voice carried down the hall, thin and bright and very far away.",
    "The map showed a road that the road itself had quietly decided to forget.",
    "I have been here too long, she thought, and the thought did not feel like hers.",
    "A door that should have opened inward opened, instead, onto more of the dark.",
    "The clock in the parlor ran backward for an hour and no one corrected it.",
    "They found the garden exactly where the garden had promised it would not be.",
    "Each photograph held one more person than the family could account for.",
    "The letter arrived addressed to a version of him he had not yet become.",
]


def _gen_prose(n_words: int, seed: int) -> str:
    """Deterministic-ish synthetic prose of about ``n_words`` words."""
    import random
    rng = random.Random(seed)
    out: list[str] = []
    count = 0
    para: list[str] = []
    while count < n_words:
        s = rng.choice(_SENTENCES)
        para.append(s)
        count += len(s.split())
        if len(para) >= rng.randint(3, 5):
            out.append("<p>" + " ".join(para) + "</p>")
            para = []
    if para:
        out.append("<p>" + " ".join(para) + "</p>")
    return "\n".join(out)


def _chapter_xhtml(title: str, body_html: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        f"<title>{title}</title></head><body>{body_html}</body></html>"
    )


def make_epub(
    path: Path,
    *,
    title: str,
    author: str,
    date: str,
    language: str = "en",
    chapters: list[tuple[str, str]],  # (chapter-title, body-xhtml)
) -> Path:
    """Write a minimal valid EPUB to ``path``."""
    container = (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opf:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    items, itemrefs, chapter_files = [], [], []
    for i, (_ctitle, _body) in enumerate(chapters, start=1):
        cid = f"ch{i:02d}"
        href = f"{cid}.xhtml"
        items.append(
            f'<item id="{cid}" href="{href}" '
            'media-type="application/xhtml+xml"/>'
        )
        itemrefs.append(f'<itemref idref="{cid}"/>')
        chapter_files.append((f"OEBPS/{href}", _chapter_xhtml(_ctitle, _body)))
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid"><metadata '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="bookid">urn:uuid:synthetic-fixture</dc:identifier>'
        f"<dc:title>{title}</dc:title>"
        f"<dc:creator>{author}</dc:creator>"
        f"<dc:date>{date}</dc:date>"
        f"<dc:language>{language}</dc:language>"
        "</metadata>"
        f"<manifest>{''.join(items)}"
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/></manifest>'
        f"<spine>{''.join(itemrefs)}</spine></package>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for name, content in chapter_files:
            zf.writestr(name, content)
    return path


def _two_book_corpus(dirpath: Path) -> Path:
    """Two synthetic books by different authors (one in 'Last, First' form),
    each with a short front-matter chapter + two real chapters."""
    dirpath.mkdir(parents=True, exist_ok=True)
    make_epub(
        dirpath / "book_a.epub",
        title="The Hollow Year", author="Doe, Jane", date="2015-06-01",
        chapters=[
            ("Title Page", "<p>The Hollow Year</p>"),               # front matter
            ("One", _gen_prose(260, 1)),
            ("Two", _gen_prose(260, 2)),
        ],
    )
    make_epub(
        dirpath / "book_b.epub",
        title="Quiet Machines", author="John Roe", date="2024-09-17",
        chapters=[
            ("One", _gen_prose(260, 3)),
            ("Two", _gen_prose(260, 4)),
        ],
    )
    return dirpath


def make_args(source: str, **overrides) -> argparse.Namespace:
    base = dict(
        source=source,
        persona=None,
        impostor_for=["target_fiction_persona"],
        register="literary_horror",
        register_match="high",
        topic_match="medium",
        consent_status="fair_use_research",
        era=None,
        segment="chapter",
        min_words=50,
        languages=["en"],
        since=None,
        until=None,
        max_items=100000,
        output_dir=None,
        emit_manifest=None,
        out=None,
        dry_run=False,
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def read_manifest(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------- unit tests --------------------------------------


def test_normalize_author_flips_last_first():
    assert ep._normalize_author("Evenson, Brian") == "Brian Evenson"
    assert ep._normalize_author("Tremblay, Paul") == "Paul Tremblay"
    assert ep._normalize_author("Evenson, Brian Keith") == "Brian Keith Evenson"


def test_normalize_author_already_natural():
    assert ep._normalize_author("Brian Evenson") == "Brian Evenson"


def test_normalize_author_multi_author_takes_first():
    # 2-token + 2-token comma => co-authors, keep the primary.
    assert ep._normalize_author("Brian Evenson, Peter Straub") == "Brian Evenson"
    assert ep._normalize_author("Helen Oyeyemi; Someone Else") == "Helen Oyeyemi"
    assert ep._normalize_author("Jane Doe & John Roe") == "Jane Doe"


def test_era_from_date_boundaries():
    assert ep._era_from_date(dt.date(2015, 6, 1)) == "pre_chatgpt"
    assert ep._era_from_date(dt.date(2022, 10, 31)) == "pre_chatgpt"
    assert ep._era_from_date(dt.date(2023, 1, 1)) == "pre_ai_widespread"
    assert ep._era_from_date(dt.date(2024, 6, 30)) == "pre_ai_widespread"
    assert ep._era_from_date(dt.date(2024, 7, 1)) == "post_ai_widespread"
    assert ep._era_from_date(None) == "undated"


def test_parse_epub_date_tolerant():
    assert ep._parse_epub_date("2015-06-01 23:00:00+00:00") == dt.date(2015, 6, 1)
    assert ep._parse_epub_date("2019") == dt.date(2019, 1, 1)
    assert ep._parse_epub_date("2020-03") == dt.date(2020, 3, 1)
    assert ep._parse_epub_date("no date here") is None
    assert ep._parse_epub_date(None) is None


def test_read_epub_info_metadata_and_spine(tmp_path):
    p = make_epub(
        tmp_path / "b.epub", title="T", author="Doe, Jane", date="2015-06-01",
        chapters=[("c1", _gen_prose(60, 1)), ("c2", _gen_prose(60, 2))],
    )
    info = ep._read_epub_info(p)
    assert info.title == "T"
    assert info.author == "Jane Doe"          # normalized
    assert info.date == dt.date(2015, 6, 1)
    assert info.language == "en"
    assert len(info.chapter_hrefs) == 2       # nav (no spine ref) excluded


def test_discover_items_per_chapter_and_per_book_persona(tmp_path):
    src = _two_book_corpus(tmp_path / "corpus")
    items = list(ep.discover_items(str(src), make_args(str(src))))
    # 3 spine chapters in book_a + 2 in book_b
    assert len(items) == 5
    personas = {it.extra["persona"] for it in items}
    assert personas == {"doe_jane_personal", "roe_john_personal"}
    # era derived per book
    eras = {it.extra["era"] for it in items}
    assert eras == {"pre_chatgpt", "post_ai_widespread"}


def test_extract_one_returns_clean_text(tmp_path):
    src = _two_book_corpus(tmp_path / "corpus")
    opts = ep.parse_options(make_args(str(src)))
    items = list(ep.discover_items(str(src), opts))
    body, title, author, date = ep.extract_one(items[1], str(src), opts)
    assert "<" not in body and ">" not in body          # no HTML residue
    assert "house" in body.lower()                       # synthetic prose present
    assert author in ("Jane Doe", "John Roe")


# --------------- end-to-end --------------------------------------


def test_end_to_end_emits_impostor_manifest(tmp_path):
    src = _two_book_corpus(tmp_path / "corpus")
    output_dir = (
        tmp_path / "ai-prose-baselines-private" / "impostors"
        / "literary_horror" / "pool"
    )
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(
        str(src), output_dir=str(output_dir), emit_manifest=str(manifest_path),
    )
    rc = ep.run(args)
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    meta_files = sorted(output_dir.glob("*.meta.json"))
    # 2 real chapters per book (front-matter title page dropped) = 4
    assert len(txt_files) == 4
    assert len(meta_files) == 4

    # no HTML residue in any acquired text
    for t in txt_files:
        assert "<p>" not in t.read_text(encoding="utf-8")

    entries = read_manifest(manifest_path)
    assert len(entries) == 4
    hashes = {e["content_hash"] for e in entries}
    assert len(hashes) == 4                               # dedupe-clean, unique
    personas = {e["persona"] for e in entries}
    assert personas == {"doe_jane_personal", "roe_john_personal"}
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["use"] == ["voice_impostor"]
        assert e["register"] == "literary_horror"
        assert e["consent_status"] == "fair_use_research"
        assert e["impostor_for"] == ["target_fiction_persona"]
        assert e["acquired_via"].startswith("acquire_epub_")
        assert e["content_hash"].startswith("sha256:")
        assert e["ai_status"] == "pre_ai_human"

    # per-book era landed on the entries
    eras = {e["era"] for e in entries}
    assert eras == {"pre_chatgpt", "post_ai_widespread"}

    # manifest validates with zero errors (cross-ref + era are warnings only)
    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    assert errors == []


def test_min_words_floor_drops_short_chapters(tmp_path):
    src = tmp_path / "corpus"
    src.mkdir()
    make_epub(
        src / "b.epub", title="Shorts", author="Jane Doe", date="2015-01-01",
        chapters=[
            ("short", "<p>" + " ".join(["word"] * 60) + "</p>"),   # ~60 words
            ("long", _gen_prose(260, 9)),
        ],
    )
    output_dir = src / "ai-prose-baselines-private" / "out"
    args = make_args(
        str(src), output_dir=str(output_dir),
        emit_manifest=str(output_dir / "m.jsonl"), min_words=120,
    )
    assert ep.run(args) == 0
    assert len(list(output_dir.glob("*.txt"))) == 1        # only the long chapter


def test_segment_book_mode_one_entry_per_book(tmp_path):
    src = _two_book_corpus(tmp_path / "corpus")
    output_dir = src.parent / "ai-prose-baselines-private" / "out"
    args = make_args(
        str(src), output_dir=str(output_dir),
        emit_manifest=str(output_dir / "m.jsonl"), segment="book",
    )
    assert ep.run(args) == 0
    assert len(list(output_dir.glob("*.txt"))) == 2        # one per book


def test_mobi_is_skipped(tmp_path, capsys):
    src = _two_book_corpus(tmp_path / "corpus")
    (src / "ignored.mobi").write_bytes(b"not a real mobi")
    items = list(ep.discover_items(str(src), make_args(str(src))))
    # mobi contributes no items; only the two epubs do
    assert all(it.extra["epub_path"].endswith(".epub") for it in items)


# --------------- guards ------------------------------------------


def test_privacy_guard_blocks_non_private_output(tmp_path):
    src = _two_book_corpus(tmp_path / "corpus")
    public_dir = tmp_path / "public_output"          # no 'private' marker
    args = make_args(
        str(src), output_dir=str(public_dir),
        emit_manifest=str(public_dir / "m.jsonl"),
        allow_public_output=False,
    )
    try:
        ep.run(args)
        assert False, "privacy guard should refuse a non-private output path"
    except SystemExit:
        pass


def test_argparse_requires_impostor_for():
    parser = ep.build_arg_parser()
    try:
        parser.parse_args([
            "somedir",
            "--register", "literary_horror",
            "--consent-status", "fair_use_research",
        ])
        assert False, "argparse should reject missing --impostor-for"
    except SystemExit:
        pass
