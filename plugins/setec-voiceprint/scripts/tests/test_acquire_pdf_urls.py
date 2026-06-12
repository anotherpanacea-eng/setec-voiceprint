#!/usr/bin/env python3
"""Regression tests for acquire_pdf_urls.py + the PDF enablement
(Fetcher.fetch_bytes + acquisition_core.pdf_text_from_bytes).

Fixtures under ``scripts/test_data/acquire_pdf_urls_fixture/``:

  * urls.jsonl  — 4 entries (a JSON grant, a bare-URL grant, a short grant,
    an image-only PDF) plus a comment line.
  * grant1/2/3.pdf — stand-in "PDF" files whose bytes the wiring tests decode
    (via the ``decode_pdf`` fixture) in place of real pypdf extraction.
  * imageonly.pdf — empty (no extractable text → skipped).

The real ``pdf_text_from_bytes`` is exercised separately against the repo's
existing fixture PDF (``pdf_inventory_fixture/text_layer_with_metadata.pdf``),
so the wiring tests don't need hand-crafted PDFs.

Invariants: the URL-list parse (JSON + bare URL + comments); fetch_bytes;
the download→extract join; the image-only skip; the min-words gate; the
impostor schema with register grant_proposal; dedupe; privacy guard;
argparse; manifest-validator integration; and that the new Fetcher bytes
path doesn't disturb existing callers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

_ok = True
_reason = ""
try:
    import acquisition_core as ac  # type: ignore
    import acquire_pdf_urls as pu  # type: ignore
    import manifest_validator as mv  # type: ignore
except ImportError as _e:  # pragma: no cover
    _ok = False
    _reason = str(_e)

if pytest is not None and not _ok:
    pytestmark = pytest.mark.skip(reason=_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquire_pdf_urls_fixture"
URLS_FILE = FIXTURE_DIR / "urls.jsonl"
REAL_PDF = ROOT / "test_data" / "pdf_inventory_fixture" / "text_layer_with_metadata.pdf"


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        urls_file=str(URLS_FILE),
        persona="opengrants",
        author="",
        impostor_for=["argscope_grant_proposal"],
        register="grant_proposal",
        register_match="high",
        topic_match="medium",
        consent_status="cc_licensed",
        era="pre_chatgpt",
        since=None,
        until=None,
        max_items=300,
        min_words=150,
        output_dir=None,
        emit_manifest=None,
        out=None,
        rate_limit=0.0,
        user_agent=None,
        dry_run=False,
        allow_empty=False,
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def fixture_url_map() -> dict:
    return {
        "https://ex.test/grant1.pdf": "grant1.pdf",
        "https://ex.test/grant2.pdf": "grant2.pdf",
        "https://ex.test/grant3.pdf": "grant3.pdf",
        "https://ex.test/imageonly.pdf": "imageonly.pdf",
    }


def make_fetcher(url_map: dict | None = None) -> ac.FixtureFetcher:
    return ac.FixtureFetcher(
        url_map=dict(url_map if url_map is not None else fixture_url_map()),
        fixture_dir=FIXTURE_DIR,
        rate_limit_seconds=0.0,
        respect_robots=False,
    )


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


if pytest is not None:
    @pytest.fixture
    def decode_pdf(monkeypatch):
        """Stand in for pypdf extraction: decode the fixture bytes as text, so
        the wiring tests run without hand-crafted real PDFs."""
        monkeypatch.setattr(
            ac, "pdf_text_from_bytes",
            lambda data: data.decode("utf-8", "replace"),
        )


# ------------------- Enablement: fetch_bytes + pdf_text -----------


def test_fetch_bytes_via_fixture():
    f = make_fetcher()
    data = f.fetch_bytes("https://ex.test/grant1.pdf")
    assert data and b"SPECIFIC AIMS" in data
    # Empty fixture (image-only) → empty bytes; unmapped → None.
    assert f.fetch_bytes("https://ex.test/imageonly.pdf") == b""
    assert f.fetch_bytes("https://ex.test/missing.pdf") is None


def test_pdf_text_from_bytes_real():
    if not REAL_PDF.is_file():
        if pytest is not None:
            pytest.skip("fixture PDF missing")
        return
    text = ac.pdf_text_from_bytes(REAL_PDF.read_bytes())
    if not text.strip():
        # pypdf not installed → extractor returns "" (graceful). Skip rather
        # than fail in a deps-less environment.
        if pytest is not None:
            pytest.skip("pypdf not installed; extractor returned empty")
        return
    assert isinstance(text, str) and len(text.strip()) > 0


def test_pdf_text_from_bytes_garbage():
    assert ac.pdf_text_from_bytes(b"") == ""
    assert ac.pdf_text_from_bytes(b"not a pdf at all") == ""


def test_fetcher_bytes_path_unaffects_text_path():
    # The existing text fetch still works alongside the new bytes path.
    f = make_fetcher()
    r = f.fetch("https://ex.test/grant1.pdf")
    assert "SPECIFIC AIMS" in r.text


# ------------------- URL-list parsing ----------------------------


def test_parse_line():
    assert pu._parse_line("# comment") is None
    assert pu._parse_line("   ") is None
    assert pu._parse_line("https://x.test/a.pdf") == {"url": "https://x.test/a.pdf"}
    assert pu._parse_line('{"url": "https://x.test/b.pdf", "title": "T"}') == {
        "url": "https://x.test/b.pdf", "title": "T"}
    # JSON without a url key, and malformed JSON, are skipped.
    assert pu._parse_line('{"title": "no url"}') is None
    assert pu._parse_line('{bad json') is None


def test_title_from_url():
    assert pu._title_from_url("https://x.test/path/MyGrant.pdf") == "MyGrant"
    assert pu._title_from_url("https://x.test/") == "untitled"


def test_discover_parses_all_entries():
    options = pu.parse_options(make_args())
    items = list(pu.discover_items(URLS_FILE, options))
    urls = {it.locator for it in items}
    assert urls == {
        "https://ex.test/grant1.pdf", "https://ex.test/grant2.pdf",
        "https://ex.test/grant3.pdf", "https://ex.test/imageonly.pdf",
    }
    g1 = [it for it in items if it.locator.endswith("grant1.pdf")][0]
    assert g1.title == "Specific Aims and Significance"
    assert g1.author == "Dr. PI One"
    assert g1.date == dt.date(2018, 5, 10)


def test_discover_date_window():
    options = pu.parse_options(make_args(since="2018-01-01", until="2018-12-31"))
    items = list(pu.discover_items(URLS_FILE, options))
    # Only grant1 (2018) has an in-window date; grant3 (2017) is excluded;
    # entries without a date are not date-filtered.
    dated = {it.locator for it in items if it.date}
    assert dated == {"https://ex.test/grant1.pdf"}


def test_discover_tolerates_utf8_bom(tmp_path):
    """A BOM-prefixed list (Windows editors) parses the same as plain utf-8:
    the leading ``#`` comment is skipped, not misread as a bare URL."""
    urls = tmp_path / "urls.jsonl"
    urls.write_text(
        "# curated grant PDFs\n"
        '{"url": "https://ex.test/a.pdf", "title": "A"}\n'
        "https://ex.test/b.pdf\n",
        encoding="utf-8-sig",  # writes a leading U+FEFF BOM
    )
    assert urls.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM really present
    options = pu.parse_options(make_args(urls_file=str(urls)))
    items = list(pu.discover_items(urls, options))
    assert {it.locator for it in items} == {
        "https://ex.test/a.pdf", "https://ex.test/b.pdf",
    }


# ------------------- End-to-end (decode stand-in) ----------------


def test_end_to_end(decode_pdf, tmp_path):
    """grant1 + grant2 acquired; grant3 dropped (short); imageonly skipped."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "grant_proposal" / "opengrants"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    rc = pu.run(args, fetcher=make_fetcher())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired PDFs, got {[f.name for f in txt_files]}"

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    by_src = {e["source"]: e for e in entries}
    assert "https://ex.test/grant1.pdf" in by_src
    assert "https://ex.test/grant2.pdf" in by_src
    # grant1 carries the JSON author; grant2 (bare URL) falls back.
    assert by_src["https://ex.test/grant1.pdf"]["author"] == "Dr. PI One"
    assert by_src["https://ex.test/grant2.pdf"]["author"] == "Unknown"
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["register"] == "grant_proposal"
        assert e["consent_status"] == "cc_licensed"
        assert e["impostor_for"] == ["argscope_grant_proposal"]
        assert e["acquired_via"].startswith("acquire_pdf_urls_")
        assert e["persona"] == "opengrants"
    assert len({e["content_hash"] for e in entries}) == 2


def test_image_only_skipped(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "io"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    pu.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert not any("imageonly" in (e.get("source") or "") for e in entries)


def test_short_dropped(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "sh"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    pu.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert not any("grant3" in (e.get("source") or "") for e in entries)


def test_min_words_gate_high_drops_all(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    pu.run(args, fetcher=make_fetcher())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_author_override(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "ov"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
        author="Grant Proposal Pool",
    )
    pu.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert entries and all(e["author"] == "Grant Proposal Pool" for e in entries)


def test_dedupe(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    pu.run(args, fetcher=make_fetcher())
    first = len(list(output_dir.glob("*.txt")))
    assert first == 2
    pu.run(args, fetcher=make_fetcher())
    assert len(list(output_dir.glob("*.txt"))) == first
    assert len(read_manifest(manifest_path)) == 2


def test_dry_run_writes_nothing(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = pu.run(args, fetcher=make_fetcher())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


# ------------------- Privacy + argparse + validator --------------


def test_privacy_guard_refuses_non_private(decode_pdf, tmp_path):
    public_dir = tmp_path / "public_oops"
    args = make_args(
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            pu.run(args, fetcher=make_fetcher())
        assert exc.value.code == 2
    else:
        try:
            pu.run(args, fetcher=make_fetcher())
            assert False
        except SystemExit as e:
            assert e.code == 2


def test_argparse_rejects_missing_required():
    parser = pu.build_arg_parser()
    for argv in (
        ["u.jsonl", "--register", "grant_proposal", "--consent-status", "cc_licensed"],
        ["u.jsonl", "--impostor-for", "x", "--consent-status", "cc_licensed"],
        ["u.jsonl", "--impostor-for", "x", "--register", "grant_proposal"],
        # missing the positional urls_file
        ["--impostor-for", "x", "--register", "grant_proposal",
         "--consent-status", "cc_licensed"],
    ):
        if pytest is not None:
            with pytest.raises(SystemExit):
                parser.parse_args(argv)
        else:
            try:
                parser.parse_args(argv)
                assert False
            except SystemExit:
                pass


def test_cli_help_lists_flags():
    help_text = pu.build_arg_parser().format_help()
    for flag in (
        "urls_file", "--persona", "--impostor-for", "--register",
        "--consent-status", "--min-words", "--dry-run", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_emitted_manifest_validates_with_grant_proposal(decode_pdf, tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    pu.run(args, fetcher=make_fetcher())

    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline", "path": "fake_baseline.txt",
        "author": "Operator", "persona": "argscope_grant_proposal",
        "register": "grant_proposal", "ai_status": "pre_ai_human",
        "language_status": "native", "use": ["baseline", "voice_profile"],
        "split": "baseline", "privacy": "private",
        "corpus_role": "identity_baseline", "era": "pre_chatgpt",
    }
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i.get("severity") == "error"]
    assert errors == [], f"Manifest should validate without errors: {errors}"
    unknown_register = [
        i for i in report["issues"]
        if "register" in i.get("message", "").lower()
        and "grant_proposal" in i.get("message", "")
    ]
    assert unknown_register == [], \
        f"grant_proposal should be a known register: {unknown_register}"


def test_zero_output_exit_code(decode_pdf, tmp_path):
    """A zero-output run that isn't a dedupe-only rerun fails (rc=1) unless
    --allow-empty; a dedupe-only rerun exits 0."""
    base = tmp_path / "ai-prose-baselines-private"
    # Everything below the floor -> nothing acquired, no dupes -> failure.
    ze = dict(output_dir=str(base / "ze"),
              emit_manifest=str(base / "ze" / "d.jsonl"), min_words=100000)
    assert pu.run(make_args(**ze), fetcher=make_fetcher()) == 1
    assert pu.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
    # Dedupe-only rerun is a valid empty result -> 0.
    od = dict(output_dir=str(base / "do"),
              emit_manifest=str(base / "do" / "d.jsonl"), min_words=150)
    assert pu.run(make_args(**od), fetcher=make_fetcher()) == 0   # first acquires
    assert pu.run(make_args(**od), fetcher=make_fetcher()) == 0   # rerun: all dupe


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
