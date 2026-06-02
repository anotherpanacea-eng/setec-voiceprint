#!/usr/bin/env python3
"""Regression tests for acquire_manuscript.py.

Fixtures are synthetic .docx/.md/.txt built at runtime (no third-party prose).
Guarded on bs4 to match the other acquire_* tests' CI-skip behavior (the
functional pipeline runs locally where acquisition deps are installed).
"""

from __future__ import annotations

import argparse
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

_deps = True
_skip = ""
try:
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:  # pragma: no cover
    _deps = False
    _skip = f"acquisition deps missing ({_e})"

if _deps:
    import acquire_manuscript as am  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _deps:
    pytestmark = pytest.mark.skip(reason=_skip)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SENTENCES = [
    "The house at the end of the lane had been empty for years.",
    "She pressed her hand to the cold glass and waited for the light to change.",
    "Below the floorboards a slow and patient sound kept its own time.",
    "He counted the steps twice and got a different number each way.",
    "The orchard smelled of rain and rust and something older underneath.",
    "When the wind turned, the curtains moved as if the room were breathing.",
]


def _prose(n_words: int, seed: int) -> str:
    import random
    rng = random.Random(seed)
    out, count = [], 0
    while count < n_words:
        s = rng.choice(_SENTENCES)
        out.append(s)
        count += len(s.split())
    return " ".join(out)


def make_docx(path: Path, paras: list[tuple[str, bool]]) -> Path:
    body = []
    for text, is_h in paras:
        ppr = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if is_h else ""
        body.append(f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    doc = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{_W}"><w:body>{"".join(body)}</w:body></w:document>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
    return path


def make_args(source: str, **ov) -> argparse.Namespace:
    base = dict(
        source=source, persona="test_fiction_persona", author="Pen Name",
        register="literary_horror", corpus_role="identity_baseline", use=None,
        ai_status="ai_assisted", consent_status="author_consent", era=None,
        impostor_for=[], register_match="high", topic_match="medium",
        segment="chapter", window_words=2500, min_words=50,
        since=None, until=None, max_items=100000,
        output_dir=None, emit_manifest=None, out=None, dry_run=False,
        allow_public_output=True, allow_non_prose=False,
        strip_rules=None, strip_aggressive=False,
    )
    base.update(ov)
    return argparse.Namespace(**base)


def read_manifest(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------- unit ---------------


def test_strip_markdown():
    md = "# Chapter One\n\nShe said *hello* and [left](http://x).\n\n```\ncode\n```\n> quote"
    out = am._strip_markdown(md)
    assert "#" not in out and "*" not in out and "code" not in out
    assert "hello" in out and "left" in out and "quote" in out


def test_segment_markdown_chapters():
    md = "# One\n" + _prose(80, 1) + "\n# Two\n" + _prose(80, 2)
    segs = am._segment_markdown(md, "chapter", 2500)
    assert len(segs) == 2


def test_window_split():
    assert len(am._window_split(" ".join(["w"] * 250), 100)) == 3


def test_docx_paragraphs_and_chaptering(tmp_path):
    p = make_docx(tmp_path / "n.docx", [
        ("Chapter One", True), (_prose(80, 1), False),
        ("Chapter Two", True), (_prose(80, 2), False),
    ])
    paras = am._docx_paragraphs(p)
    assert any(is_h for _, is_h in paras)
    segs = am._segment_docx(p, "chapter", 2500)
    assert len(segs) == 2


def test_discover_mixed_dir(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "a.md").write_text("# One\n" + _prose(120, 1), encoding="utf-8")
    (d / "b.txt").write_text(_prose(120, 2), encoding="utf-8")
    make_docx(d / "c.docx", [("H", True), (_prose(120, 3), False)])
    items = list(am.discover_items(str(d), am.parse_options(make_args(str(d)))))
    assert len(items) >= 3


# --------------- e2e ---------------


def test_end_to_end_identity_baseline(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "novel.md").write_text(
        "# One\n" + _prose(200, 1) + "\n# Two\n" + _prose(200, 2), encoding="utf-8")
    out = tmp_path / "ai-prose-baselines-private" / "identity"
    args = make_args(str(d), output_dir=str(out),
                     emit_manifest=str(out / "draft_manifest.jsonl"))
    assert am.run(args) == 0
    entries = read_manifest(out / "draft_manifest.jsonl")
    assert len(entries) == 2
    for e in entries:
        assert e["corpus_role"] == "identity_baseline"
        assert e["use"] == ["voice_profile"]
        assert e["persona"] == "test_fiction_persona"
        assert e["ai_status"] == "ai_assisted"
        assert "impostor_for" not in e
        assert "<p>" not in (out / e["path"]).read_text(encoding="utf-8")
    report = mv.validate_manifest(out / "draft_manifest.jsonl")
    assert [i for i in report["issues"] if i["severity"] == "error"] == []


def test_impostor_mode(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "x.md").write_text("# One\n" + _prose(200, 5), encoding="utf-8")
    out = tmp_path / "ai-prose-baselines-private" / "imp"
    args = make_args(str(d), output_dir=str(out),
                     emit_manifest=str(out / "m.jsonl"),
                     corpus_role="impostor", impostor_for=["someone_fiction"])
    assert am.run(args) == 0
    e = read_manifest(out / "m.jsonl")[0]
    assert e["corpus_role"] == "impostor"
    assert e["use"] == ["voice_impostor"]
    assert e["impostor_for"] == ["someone_fiction"]


def test_min_words_floor(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "x.md").write_text("# Short\n" + _prose(40, 1) + "\n# Long\n" + _prose(300, 2),
                            encoding="utf-8")
    out = tmp_path / "ai-prose-baselines-private" / "o"
    args = make_args(str(d), output_dir=str(out),
                     emit_manifest=str(out / "m.jsonl"), min_words=120)
    assert am.run(args) == 0
    assert len(list(out.glob("*.txt"))) == 1


def test_segment_work_mode(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "x.md").write_text("# One\n" + _prose(120, 1) + "\n# Two\n" + _prose(120, 2),
                            encoding="utf-8")
    out = tmp_path / "ai-prose-baselines-private" / "o"
    args = make_args(str(d), output_dir=str(out),
                     emit_manifest=str(out / "m.jsonl"), segment="work")
    assert am.run(args) == 0
    assert len(list(out.glob("*.txt"))) == 1   # whole file = one entry


def test_privacy_guard(tmp_path):
    d = tmp_path / "ms"; d.mkdir()
    (d / "x.md").write_text(_prose(200, 1), encoding="utf-8")
    public = tmp_path / "public"
    args = make_args(str(d), output_dir=str(public),
                     emit_manifest=str(public / "m.jsonl"), allow_public_output=False)
    try:
        am.run(args)
        assert False, "privacy guard should refuse a non-private path"
    except SystemExit:
        pass
