#!/usr/bin/env python3
"""Regression tests for acquire_everycrsreport.py.

Strategy mirrors test_acquire_blog.py: mock the network with
`acquisition_core.FixtureFetcher`, which maps URLs to local fixtures
under ``scripts/test_data/acquire_everycrsreport_fixture/``. The fixture
``reports.csv`` covers the cases the spec calls out:

  * R1, R2 — admissible long reports (in window, above the word floor).
  * IF1     — a too-short "In Focus" snapshot (below the word floor).
  * R3      — an HTML-missing / PDF-only row (skipped: no-html).
  * R4      — an out-of-window (2024) row (filtered at discovery).

Invariants exercised: CSV parse + tolerant column resolution; the
date-window filter; HTML -> clean text with chrome / masthead / contact
trailer removed; the ``--min-words`` length gate; the impostor manifest
schema (corpus_role, use, register, era, consent_status, content_hash,
acquired_via); content-hash dedupe; the privacy guard; argparse
required-flag rejection; and a manifest-validator integration that
confirms the new ``policy_brief`` register validates clean.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from conftest import read_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

# bs4 is the only third-party dep the CRS path needs (html_to_text);
# csv is stdlib. Skip cleanly when acquisition deps are absent.
_acq_deps_available = True
_skip_reason = ""
try:
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:
    _acq_deps_available = False
    _skip_reason = (
        f"acquisition deps missing ({_e}); install with "
        "`pip install -r requirements-acquisition.txt`"
    )

if _acq_deps_available:
    import acquisition_core as ac  # type: ignore
    import acquire_everycrsreport as ev  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquire_everycrsreport_fixture"
# Literal (not ev.DEFAULT_REPORTS_CSV_URL) so module import doesn't touch
# `ev` before the deps-available guard — test_argparse_accepts_when_provided
# asserts the two agree.
CSV_URL = "https://www.everycrsreport.com/reports.csv"
BASE = "https://www.everycrsreport.com"

# URL -> fixture mapping. The HTML URLs are what _html_url() builds by
# urljoin-ing the bare filenames in reports.csv against CSV_URL.
FIXTURE_URLS = {
    CSV_URL: "reports.csv",
    f"{BASE}/R1.html": "R1.html",
    f"{BASE}/R2.html": "R2.html",
    f"{BASE}/IF1.html": "IF1.html",
    f"{BASE}/R4.html": "R4.html",
}


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    """Default Namespace matching ev.build_arg_parser."""
    base = dict(
        reports_csv_url=CSV_URL,
        persona="crs",
        author=ev.CRS_AUTHOR,
        impostor_for=["argscope_policy_brief"],
        register="policy_brief",
        register_match="high",
        topic_match="medium",
        consent_status="public_record",
        era="pre_chatgpt",
        since="2010-01-01",
        until="2021-12-31",
        max_items=400,
        min_words=300,
        output_dir=None,
        emit_manifest=None,
        out=None,
        content_selector=None,
        rate_limit=0.0,
        user_agent=None,
        dry_run=False,
        allow_public_output=True,  # tests write into tmp dirs
        allow_empty=False,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def make_fetcher(url_map: dict | None = None) -> ac.FixtureFetcher:
    return ac.FixtureFetcher(
        url_map=dict(url_map if url_map is not None else FIXTURE_URLS),
        fixture_dir=FIXTURE_DIR,
        rate_limit_seconds=0.0,
        respect_robots=False,
    )


# ------------------- Unit: column resolution ---------------------


def test_resolve_column_case_insensitive():
    fields = ["Number", "Title", "latestPubDate", "latestHTML"]
    assert ev._resolve_column(fields, ev.CSV_TITLE_COLS) == "Title"
    assert ev._resolve_column(fields, ev.CSV_HTML_COLS) == "latestHTML"
    assert ev._resolve_column(fields, ev.CSV_DATE_COLS) == "latestPubDate"
    assert ev._resolve_column(fields, ("missing",)) is None


def test_html_url_builder():
    # Bare filename resolves against the CSV's directory.
    assert ev._html_url(CSV_URL, "R1.html") == f"{BASE}/R1.html"
    # Full URLs pass through unchanged.
    assert ev._html_url(CSV_URL, "https://x.test/y.html") == "https://x.test/y.html"
    # Empty value yields empty string (the no-html sentinel).
    assert ev._html_url(CSV_URL, "") == ""
    assert ev._html_url(CSV_URL, "   ") == ""


# ------------------- Unit: discover_items ------------------------


def test_discover_items_window_and_no_html():
    """Discovery yields in-window rows, filters the 2024 row, and
    surfaces the PDF-only row with an empty locator (the no-html
    sentinel the driver logs)."""
    options = ev.parse_options(make_args())
    items = list(ev.discover_items(CSV_URL, options, make_fetcher()))
    titles = {it.title for it in items}
    # R4 (2024) is filtered by the date window.
    assert not any("Outside the Window" in t for t in titles)
    # R1, R2, IF1, R3 are all in-window and yielded.
    assert "Federal Widget Policy: Analysis and Options" in titles
    assert "Interstate Data Flows and Regulatory Tradeoffs" in titles
    # The PDF-only row (R3) is yielded with an empty locator.
    r3 = [it for it in items if it.title == "Legacy PDF-Only Report"]
    assert len(r3) == 1 and r3[0].locator == ""
    # In-window HTML rows carry an absolute URL + parsed date.
    r1 = [it for it in items if it.number == "R1"][0]
    assert r1.locator == f"{BASE}/R1.html"
    assert r1.date == dt.date(2018, 5, 10)


def test_discover_raises_on_unknown_schema():
    """A reports.csv missing the title/HTML columns fails loudly."""
    bad = ac.FetchResult(
        url=CSV_URL, status=200, text="foo,bar\n1,2\n",
        content_type="text/csv", final_url=CSV_URL,
    )
    fetcher = ac.FixtureFetcher(
        url_map={CSV_URL: bad}, rate_limit_seconds=0.0, respect_robots=False,
    )
    options = ev.parse_options(make_args())
    if pytest is not None:
        with pytest.raises(ValueError):
            list(ev.discover_items(CSV_URL, options, fetcher))
    else:
        try:
            list(ev.discover_items(CSV_URL, options, fetcher))
            assert False, "expected ValueError on unknown schema"
        except ValueError:
            pass


# ------------------- Unit: trailer trim --------------------------


def test_trim_crs_trailer_trims_only_late_heading():
    body = ("Substantive argument paragraph. " * 60).strip()
    text = body + "\nAuthor Information\nZZCONTACTZZ contact line."
    trimmed = ev._trim_crs_trailer(text)
    assert "ZZCONTACTZZ" not in trimmed
    assert "Author Information" not in trimmed
    assert trimmed.startswith("Substantive argument")


def test_trim_crs_trailer_preserves_early_heading():
    # A "Contacts" heading near the very top (before 80%) is preserved —
    # we don't want to truncate a mid-document section.
    text = "Contacts\n" + ("Body sentence here. " * 200).strip()
    assert ev._trim_crs_trailer(text) == text


# ------------------- Unit: extract_one ---------------------------


def test_extract_one_clean_body():
    options = ev.parse_options(make_args())
    item = ev.ItemMeta(
        locator=f"{BASE}/R1.html",
        title="Federal Widget Policy: Analysis and Options",
        date=dt.date(2018, 5, 10),
    )
    body, title, author, date = ev.extract_one(item, options, make_fetcher())
    assert ac.html_text_is_clean(body)
    assert author == ev.CRS_AUTHOR
    assert title == "Federal Widget Policy: Analysis and Options"
    # Site chrome, cover masthead, and the contact trailer are gone.
    assert "EveryCRSReport navigation" not in body
    assert "MASTHEAD_TOKEN" not in body
    assert "ZZCONTACTZZ" not in body
    assert "Site footer" not in body
    # Substantive argument survives.
    assert "widget" in body.lower()


# ------------------- End-to-end ----------------------------------


def test_end_to_end(tmp_path):
    """Full run: R1 + R2 acquired; IF1 dropped (too short); R3 skipped
    (no html); R4 filtered (out of window). Manifest carries the
    impostor schema with register policy_brief."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "policy_brief" / "crs"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
    )
    rc = ev.run(args, fetcher=make_fetcher())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    meta_files = sorted(output_dir.glob("*.meta.json"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired reports, got {[f.name for f in txt_files]}"
    assert len(meta_files) == 2

    for txt in txt_files:
        body = txt.read_text(encoding="utf-8")
        assert ac.html_text_is_clean(body)
        assert "MASTHEAD_TOKEN" not in body
        assert "ZZCONTACTZZ" not in body

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["use"] == ["voice_impostor"]
        assert e["split"] == "baseline"
        assert e["privacy"] == "private"
        assert e["register"] == "policy_brief"
        assert e["era"] == "pre_chatgpt"
        assert e["consent_status"] == "public_record"
        assert e["impostor_for"] == ["argscope_policy_brief"]
        assert e["acquired_via"].startswith("acquire_everycrsreport_")
        assert e["content_hash"].startswith("sha256:")
        assert e["author"] == ev.CRS_AUTHOR
        assert e["persona"] == "crs"

    # Unique hashes (R1 != R2; dedupe didn't false-fire).
    assert len({e["content_hash"] for e in entries}) == 2

    # Meta sidecars carry preprocessing + scraper provenance.
    for meta_file in meta_files:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "preprocessing" in meta
        assert meta["scraper"].startswith("acquire_everycrsreport_")


def test_min_words_gate_drops_everything_when_high(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    ev.run(args, fetcher=make_fetcher())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_zero_output_exit_code(tmp_path):
    """A zero-output run that isn't a dedupe-only rerun fails (rc=1) unless
    --allow-empty; a dedupe-only rerun exits 0."""
    base = tmp_path / "ai-prose-baselines-private"
    # Everything below the floor → nothing acquired, no dupes → failure.
    ze = dict(output_dir=str(base / "ze"),
              emit_manifest=str(base / "ze" / "d.jsonl"), min_words=100000)
    assert ev.run(make_args(**ze), fetcher=make_fetcher()) == 1
    assert ev.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
    # Dedupe-only rerun is a valid empty result → 0.
    od = dict(output_dir=str(base / "do"),
              emit_manifest=str(base / "do" / "d.jsonl"), min_words=300)
    assert ev.run(make_args(**od), fetcher=make_fetcher()) == 0   # first acquires
    assert ev.run(make_args(**od), fetcher=make_fetcher()) == 0   # rerun: all dupe


def test_in_focus_dropped_below_floor(tmp_path):
    """The IF1 'In Focus' snapshot is below the default-ish floor and
    must not be acquired even when R1/R2 are."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "if"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
        min_words=300,
    )
    ev.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert not any("Focus" in (e.get("source") or "") for e in entries)
    assert not any("IF1" in f.name for f in output_dir.glob("*.txt"))


def test_dedupe_within_output_dir(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
    )
    ev.run(args, fetcher=make_fetcher())
    first = len(list(output_dir.glob("*.txt")))
    assert first == 2
    ev.run(args, fetcher=make_fetcher())
    assert len(list(output_dir.glob("*.txt"))) == first
    assert len(read_manifest(manifest_path)) == 2


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = ev.run(args, fetcher=make_fetcher())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))
    assert not (output_dir / "draft.jsonl").exists()


# ------------------- Privacy guard -------------------------------


def test_privacy_guard_refuses_non_private(tmp_path):
    public_dir = tmp_path / "public_oops"
    args = make_args(
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            ev.run(args, fetcher=make_fetcher())
        assert exc.value.code == 2
    else:
        try:
            ev.run(args, fetcher=make_fetcher())
            assert False, "expected SystemExit(2)"
        except SystemExit as e:
            assert e.code == 2
    assert not public_dir.exists() or not list(public_dir.glob("*.txt"))


# ------------------- argparse rejection --------------------------


def test_argparse_rejects_missing_required():
    parser = ev.build_arg_parser()
    for argv in (
        # missing --impostor-for
        ["--register", "policy_brief", "--consent-status", "public_record"],
        # missing --register
        ["--impostor-for", "x", "--consent-status", "public_record"],
        # missing --consent-status
        ["--impostor-for", "x", "--register", "policy_brief"],
    ):
        if pytest is not None:
            with pytest.raises(SystemExit):
                parser.parse_args(argv)
        else:
            try:
                parser.parse_args(argv)
                assert False, f"argparse should reject {argv}"
            except SystemExit:
                pass


def test_argparse_accepts_when_provided():
    parser = ev.build_arg_parser()
    args = parser.parse_args([
        "--impostor-for", "argscope_policy_brief",
        "--register", "policy_brief",
        "--consent-status", "public_record",
    ])
    assert args.impostor_for == ["argscope_policy_brief"]
    assert args.register == "policy_brief"
    # Positional defaults to the public reports.csv URL.
    assert args.reports_csv_url == ev.DEFAULT_REPORTS_CSV_URL


def test_cli_help_lists_flags():
    parser = ev.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--persona", "--impostor-for", "--register", "--consent-status",
        "--era", "--since", "--until", "--max-items", "--min-words",
        "--dry-run", "--emit-manifest", "--out", "--allow-public-output",
        "--allow-non-prose", "--strip-rules", "--strip-aggressive",
    ):
        assert flag in help_text, f"--help missing {flag}"


# ------------------- Manifest-validator integration --------------


def test_emitted_manifest_validates_with_policy_brief_register(tmp_path):
    """The draft manifest validates clean — no errors and, with the
    policy_brief register added to ALLOWED_REGISTER, no unknown-register
    warning — when augmented with an identity_baseline naming the
    impostor's target persona."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
    )
    rc = ev.run(args, fetcher=make_fetcher())
    assert rc == 0

    # Identity baseline so the impostor persona-reference + register
    # cross-checks have a target.
    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline",
        "path": "fake_baseline.txt",
        "author": "Operator",
        "persona": "argscope_policy_brief",
        "register": "policy_brief",
        "ai_status": "pre_ai_human",
        "language_status": "native",
        "use": ["baseline", "voice_profile"],
        "split": "baseline",
        "privacy": "private",
        "corpus_role": "identity_baseline",
        "era": "pre_chatgpt",
    }
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i.get("severity") == "error"]
    assert errors == [], f"Manifest should validate without errors: {errors}"
    # policy_brief is a known register now → no unknown-register warning.
    unknown_register = [
        i for i in report["issues"]
        if "register" in i.get("message", "").lower()
        and "policy_brief" in i.get("message", "")
    ]
    assert unknown_register == [], \
        f"policy_brief should be a known register: {unknown_register}"



def test_historical_selects_old_version_from_updated_index(tmp_path):
    """Historical discovery uses per-report versions even when CSV latest is new."""
    private = tmp_path / "ai-prose-baselines-private"
    parser = ev.build_arg_parser()
    args = parser.parse_args([
        CSV_URL, "--impostor-for", "synthetic_target",
        "--register", "policy_brief", "--consent-status", "public_record",
        "--historical-versions", "--until", "2021-12-31",
        "--metadata-limit", "1", "--min-words", "20",
        "--output-dir", str(private / "candidates"),
        "--out", str(private / "receipt.json"),
    ])
    html = "<html><body><article><div class='summary-box'>Summary retained.</div>" + (
        "<p>Substantive synthetic policy analysis and citation.</p>" * 20
    ) + "<h2>Author Information</h2><p>Analyst Example</p></article></body></html>"
    fixtures = {
        CSV_URL: ac.FetchResult(CSV_URL, 200,
            "number,url,latestPubDate,title,latestHTML\n"
            "RTEST,reports/RTEST.json,2025-01-01,New title,files/new.html\n"),
        f"{BASE}/reports/RTEST.json": ac.FetchResult(
            f"{BASE}/reports/RTEST.json", 200,
            json.dumps({"id": "RTEST", "versions": [
                {"id": 11, "date": "2025-01-01T00:00:00",
                 "title": "New title", "formats": [{"format": "HTML", "filename": "files/new.html"}]},
                {"id": 7, "date": "2020-06-01T00:00:00",
                 "title": "Old title", "formats": [{"format": "HTML", "filename": "files/old.html"}]},
            ]})),
        f"{BASE}/files/old.html": ac.FetchResult(f"{BASE}/files/old.html", 200, html),
    }
    fetcher = ac.FixtureFetcher(fixtures, rate_limit_seconds=0, respect_robots=False)
    assert ev.run(args, fetcher=fetcher) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "bounded_batch"
    assert receipt["batch_attempted"] == 1
    assert receipt["summary"]["draft_manifest_path"] is None
    texts = list((private / "candidates").glob("*.txt"))
    assert len(texts) == 1
    assert "Summary retained." in texts[0].read_text(encoding="utf-8")


def _historical_args(private, **overrides):
    base = dict(
        historical_versions=True, metadata_limit=1, report_number=None,
        after_report_number=None, expected_index_sha256=None,
        resume_receipt=None, until="2021-12-31", since=None,
        max_items=400, min_words=20, allow_public_output=False,
        emit_manifest=None, output_dir=str(private / "candidates"),
        out=str(private / "receipt.json"),
    )
    base.update(overrides)
    return make_args(**base)


def _inline_fetcher(index_text, metadata=None, html=None):
    urls = {CSV_URL: ac.FetchResult(CSV_URL, 200, index_text)}
    for number, document in (metadata or {}).items():
        url = f"{BASE}/reports/{number}.json"
        urls[url] = ac.FetchResult(url, 200, json.dumps(document))
    for filename, body in (html or {}).items():
        url = f"{BASE}/files/{filename}"
        urls[url] = ac.FetchResult(url, 200, body)
    return ac.FixtureFetcher(urls, rate_limit_seconds=0, respect_robots=False)


def _index(*numbers):
    return "number,url,latestPubDate,title,latestHTML\n" + "".join(
        f"{n},reports/{n}.json,2025-01-01,Current,files/current.html\n"
        for n in numbers
    )


def _version(day="2020-01-01T00:00:00", version_id=1, filename="old.html"):
    return {"id": version_id, "date": day, "title": "Historical synthetic report",
            "formats": [{"format": "HTML", "filename": f"files/{filename}"}]}


def _long_html():
    return ("<html><body><article><div class='summary-box'>Summary retained.</div>"
            "<table><tr><td>Table source note retained.</td></tr></table>"
            + "<p>Substantive synthetic policy analysis and citation.</p>" * 20
            + "<h2>Appendix</h2><p>Appendix retained.</p>"
            + "<h2>Author Information</h2><p>Analyst Example</p>"
            + "</article></body></html>")


def test_historical_resume_counts_missing_target_in_second_batch(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RC")
    targets = ["RA", "RB", "RC"]
    fetcher = _inline_fetcher(index, {
        "RA": {"id": "RA", "versions": [_version(filename="a.html")]},
        "RC": {"id": "RC", "versions": [_version(filename="c.html")]},
    }, {"a.html": _long_html(), "c.html": _long_html().replace("citation", "footnote")})
    first = private / "first.json"
    second = private / "second.json"
    third = private / "third.json"
    args1 = _historical_args(private, report_number=targets, out=str(first))
    assert ev.run(args1, fetcher=fetcher) == 0
    r1 = json.loads(first.read_text(encoding="utf-8"))
    assert r1["status_rows"] == [{"id": "RA", "status": "written"}]
    assert (r1["batch_start_ordinal"], r1["remaining_after_batch"], r1["next_cursor"]) == (0, 2, "RA")
    digest = hashlib.sha256(index.encode("utf-8")).hexdigest()
    args2 = _historical_args(private, report_number=targets, out=str(second),
                             after_report_number="RA", expected_index_sha256=digest,
                             resume_receipt=str(first), allow_empty=True)
    assert ev.run(args2, fetcher=fetcher) == 0
    r2 = json.loads(second.read_text(encoding="utf-8"))
    assert r2["status_rows"] == [{"id": "RB", "status": "absent-from-index"}]
    assert (r2["batch_start_ordinal"], r2["remaining_after_batch"], r2["next_cursor"]) == (1, 1, "RB")
    args3 = _historical_args(private, report_number=targets, out=str(third),
                             after_report_number="RB", expected_index_sha256=digest,
                             resume_receipt=str(second))
    assert ev.run(args3, fetcher=fetcher) == 0
    r3 = json.loads(third.read_text(encoding="utf-8"))
    assert r3["status_rows"] == [{"id": "RC", "status": "written"}]
    assert r3["scope_exhausted"] is True
    assert r3["predecessor_receipt_sha256"] == hashlib.sha256(second.read_bytes()).hexdigest()


def test_historical_index_failure_receipts_cannot_resume(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    for name, fetcher in (
        ("fetch", _inline_fetcher("")),
        ("schema", _inline_fetcher("foo,bar\n1,2\n")),
    ):
        out = private / f"{name}.json"
        args = _historical_args(private, out=str(out))
        assert ev.run(args, fetcher=fetcher) == 1
        receipt = json.loads(out.read_text(encoding="utf-8"))
        assert receipt["kind"] == "failure"
        assert receipt["batch_attempted"] == 0
        assert receipt["status_rows"] == []
        assert receipt["total_in_scope"] is None
        assert receipt["summary"]["draft_manifest_path"] is None
        # A failure envelope is never a continuation authority.
        import hashlib
        index = _index("RA")
        resume = _historical_args(
            private, out=str(private / f"{name}-resume.json"),
            after_report_number="RA", expected_index_sha256=hashlib.sha256(index.encode()).hexdigest(),
            resume_receipt=str(out),
        )
        with pytest.raises(SystemExit):
            ev.run(resume, fetcher=_inline_fetcher(index))


def test_historical_same_date_and_pdf_only_are_dispositions(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RB")
    documents = {
        "RA": {"id": "RA", "versions": [_version(version_id=1), _version(version_id=2)]},
        "RB": {"id": "RB", "versions": [
            _version(day="2020-01-01", filename="older.html"),
            {"id": 9, "date": "2021-01-01T00:00:00", "title": "PDF only",
             "formats": [{"format": "PDF", "filename": "files/newer.pdf"}]},
        ]},
    }
    args = _historical_args(private, metadata_limit=2, allow_empty=True)
    assert ev.run(args, fetcher=_inline_fetcher(index, documents)) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status_rows"] == [
        {"id": "RA", "status": "ambiguous-same-date-versions"},
        {"id": "RB", "status": "no-html"},
    ]
    assert receipt["files_written"] == 0
    assert not list((private / "candidates").glob("*.txt"))


def test_historical_zero_output_and_dry_run_receipts(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA")
    metadata = {"RA": {"id": "RA", "versions": [_version()]}}
    no_html = _historical_args(private)
    assert ev.run(no_html, fetcher=_inline_fetcher(index, metadata)) == 1
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["batch_complete"] is True
    assert receipt["status_rows"][0]["status"] == "html-fetch-failed"
    dry = _historical_args(private, dry_run=True, out=str(private / "dry.json"))
    assert ev.run(dry, fetcher=_inline_fetcher(index, metadata, {"old.html": _long_html()})) == 0
    dry_receipt = json.loads((private / "dry.json").read_text(encoding="utf-8"))
    assert dry_receipt["status_rows"][0]["status"] == "would_write"
    assert dry_receipt["files_written"] == 0
    assert not list((private / "candidates").glob("*.txt"))


def test_historical_preflight_refuses_manifest_and_overwrite(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    out = private / "receipt.json"
    out.parent.mkdir(parents=True)
    out.write_text("sentinel", encoding="utf-8")
    for overrides in (
        {"out": str(out)},
        {"out": str(private / "other.json"), "emit_manifest": str(private / "draft.jsonl")},
        {"out": str(private / "other.json"), "max_items": 0},
    ):
        with pytest.raises(SystemExit):
            ev.run(_historical_args(private, **overrides),
                   fetcher=_inline_fetcher(_index("RA")))
    assert out.read_text(encoding="utf-8") == "sentinel"
    assert not (private / "other.json").exists()


def test_historical_sidecar_binds_decoded_sources_and_retains_author(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA")
    metadata = {"id": "RA", "versions": [_version(version_id=23)]}
    html = _long_html()
    assert ev.run(_historical_args(private), fetcher=_inline_fetcher(
        index, {"RA": metadata}, {"old.html": html},
    )) == 0
    sidecars = list((private / "candidates").glob("*.meta.json"))
    assert len(sidecars) == 1
    meta = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert meta["provider_version_id"] == 23
    assert meta["provider_version_id_type"] == "int"
    assert meta["provider_version_date"] == "2020-01-01T00:00:00"
    assert meta["source_url"] == f"{BASE}/files/old.html"
    assert meta["byline_status"] == "source_block_unreviewed"
    assert type(meta["author_block_body_char_offset"]) is int
    assert len(meta["author_block_visible_text_sha256"]) == 64
    assert meta["rights_review_status"] == "pending_document_level_third_party_review"
    assert meta["source_snapshots_retained"] is False
    assert meta["html_decoded_text_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert meta["metadata_decoded_text_sha256"] == hashlib.sha256(json.dumps(metadata).encode()).hexdigest()
    body = next((private / "candidates").glob("*.txt")).read_text(encoding="utf-8")
    assert "Summary retained." in body
    assert "Table source note retained." in body
    assert "Appendix retained." in body
    assert "Analyst Example" in body
    assert not list(private.rglob("draft_manifest.jsonl"))


@pytest.mark.parametrize("versions,expected", [
    ([_version(day="2020-13-01")], "ambiguous-version-date"),
    ([_version(day="2020-01-01T00:00:00Z")], "ambiguous-version-date"),
    ([_version(day="2020-01-01", version_id=True)], "invalid-selected-version-id"),
    ([{"id": 1, "date": "2020-01-01", "title": "PDF only",
       "formats": [{"format": "PDF", "filename": "files/a.pdf"}]}], "no-html"),
    ([_version(filename="../escape.html")], "invalid-html-locator"),
    ([_version(), {"id": 2, "date": "2025-01-01", "title": "Future", "formats": {"bad": True}}], "html-fetch-failed"),
])
def test_historical_malformed_and_selected_format_rules(tmp_path, versions, expected):
    private = tmp_path / "ai-prose-baselines-private"
    fetcher = _inline_fetcher(_index("RA"), {"RA": {"id": "RA", "versions": versions}})
    assert ev.run(_historical_args(private, allow_empty=True), fetcher=fetcher) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status_rows"] == [{"id": "RA", "status": expected}]


def test_historical_resume_refuses_forged_prior_without_metadata_fetch(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RB")
    first = private / "first.json"
    args1 = _historical_args(private, out=str(first), allow_empty=True)
    assert ev.run(args1, fetcher=_inline_fetcher(index)) == 0
    forged = json.loads(first.read_text(encoding="utf-8"))
    forged["batch_attempted"] = 2
    first.write_text(json.dumps(forged), encoding="utf-8")
    second = private / "second.json"
    fetcher = _inline_fetcher(index)
    args2 = _historical_args(
        private, out=str(second), after_report_number="RA",
        expected_index_sha256=hashlib.sha256(index.encode()).hexdigest(),
        resume_receipt=str(first), allow_empty=True,
    )
    assert ev.run(args2, fetcher=fetcher) == 1
    assert fetcher.fetched_urls == [CSV_URL]
    failure = json.loads(second.read_text(encoding="utf-8"))
    assert failure["kind"] == "failure"
    assert failure["batch_attempted"] == 0
    assert failure["total_in_scope"] is None
    assert first.read_text(encoding="utf-8") == json.dumps(forged)


def test_historical_changed_index_refuses_resume(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RB")
    first = private / "first.json"
    assert ev.run(_historical_args(private, out=str(first), allow_empty=True),
                  fetcher=_inline_fetcher(index)) == 0
    modified = _index("RA", "RB", "RC")
    fetcher = _inline_fetcher(modified)
    second = private / "second.json"
    args = _historical_args(
        private, out=str(second), after_report_number="RA",
        expected_index_sha256=hashlib.sha256(index.encode()).hexdigest(),
        resume_receipt=str(first), allow_empty=True,
    )
    assert ev.run(args, fetcher=fetcher) == 1
    assert fetcher.fetched_urls == [CSV_URL]
    assert json.loads(second.read_text(encoding="utf-8"))["kind"] == "failure"


@pytest.mark.parametrize("number", ["00-000", "00-000X"])
def test_historical_legacy_digit_leading_id_index_and_metadata(tmp_path, number):
    """Synthetic legacy ID forms must survive both index and metadata lookup."""
    private = tmp_path / "ai-prose-baselines-private"
    index = _index(number)
    fetcher = _inline_fetcher(
        index,
        {number: {"id": number, "versions": [_version()]}},
        {"old.html": _long_html()},
    )
    assert ev.run(_historical_args(private), fetcher=fetcher) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status_rows"] == [{"id": number, "status": "written"}]
    assert f"{BASE}/reports/{number}.json" in fetcher.fetched_urls


def test_historical_refuses_redirected_index_source(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    redirected = ac.FetchResult(
        CSV_URL, 200, _index("RA"), final_url="https://other.example/reports.csv",
    )
    fetcher = ac.FixtureFetcher(
        {CSV_URL: redirected}, rate_limit_seconds=0, respect_robots=False,
    )
    assert ev.run(_historical_args(private), fetcher=fetcher) == 1
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "failure"
    assert receipt["failure_reason"] == "index-redirected"
    assert receipt["total_in_scope"] is None
    assert fetcher.fetched_urls == [CSV_URL]


def test_review_regression_historical_preserves_sibling_report_sections(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    html = ("<html><body><article>" + "<p>Substantive synthetic analysis.</p>" * 20
            + "</article><section class='footnotes'>Footnote citation retained.</section>"
            + "<section class='appendix'>Appendix data retained.</section></body></html>")
    assert ev.run(_historical_args(private), fetcher=_inline_fetcher(
        _index("RA"), {"RA": {"id": "RA", "versions": [_version()]}},
        {"old.html": html},
    )) == 0
    text = next((private / "candidates").glob("*.txt")).read_text(encoding="utf-8")
    assert "Footnote citation retained." in text
    assert "Appendix data retained." in text


def test_review_regression_historical_refuses_content_selector_before_fetch(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    fetcher = _inline_fetcher(_index("RA"))
    with pytest.raises(SystemExit):
        ev.run(_historical_args(private, content_selector="article"), fetcher=fetcher)
    assert fetcher.fetched_urls == []


@pytest.mark.parametrize("tamper", ["status", "files", "acquired"])
def test_review_regression_resume_rejects_impossible_receipt(tmp_path, tamper):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RB")
    first = private / "first.json"
    assert ev.run(_historical_args(private, out=str(first), allow_empty=True),
                  fetcher=_inline_fetcher(index)) == 0
    receipt = json.loads(first.read_text(encoding="utf-8"))
    if tamper == "status":
        receipt["status_rows"][0]["status"] = "made-up-terminal"
    elif tamper == "files":
        receipt["files_written"] = 999
    else:
        receipt["summary"]["acquired"] = 999
    first.write_text(json.dumps(receipt), encoding="utf-8")
    fetcher = _inline_fetcher(index)
    second = private / "second.json"
    args = _historical_args(
        private, out=str(second), after_report_number="RA",
        expected_index_sha256=hashlib.sha256(index.encode()).hexdigest(),
        resume_receipt=str(first), allow_empty=True,
    )
    assert ev.run(args, fetcher=fetcher) == 1
    assert fetcher.fetched_urls == [CSV_URL]
    assert json.loads(second.read_text(encoding="utf-8"))["kind"] == "failure"


def test_review_regression_resume_requires_noninitial_predecessor_hash(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA", "RB", "RC")
    first = private / "first.json"
    second = private / "second.json"
    assert ev.run(_historical_args(private, out=str(first), allow_empty=True),
                  fetcher=_inline_fetcher(index)) == 0
    digest = hashlib.sha256(index.encode()).hexdigest()
    assert ev.run(_historical_args(private, out=str(second), allow_empty=True,
                  after_report_number="RA", expected_index_sha256=digest,
                  resume_receipt=str(first)), fetcher=_inline_fetcher(index)) == 0
    forged = json.loads(second.read_text(encoding="utf-8"))
    forged["predecessor_receipt_sha256"] = None
    second.write_text(json.dumps(forged), encoding="utf-8")
    third = private / "third.json"
    fetcher = _inline_fetcher(index)
    args = _historical_args(private, out=str(third), allow_empty=True,
                            after_report_number="RB", expected_index_sha256=digest,
                            resume_receipt=str(second))
    assert ev.run(args, fetcher=fetcher) == 1
    assert fetcher.fetched_urls == [CSV_URL]
    assert json.loads(third.read_text(encoding="utf-8"))["kind"] == "failure"


def test_review_regression_malformed_csv_writes_failure_receipt(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    malformed = "number,url\nRA," + "x" * 132000 + "\n"
    fetcher = _inline_fetcher(malformed)
    assert ev.run(_historical_args(private), fetcher=fetcher) == 1
    assert fetcher.fetched_urls == [CSV_URL]
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "failure"
    assert receipt["batch_attempted"] == 0


def test_review_regression_exhausted_resume_refuses_new_batch(tmp_path):
    import hashlib
    private = tmp_path / "ai-prose-baselines-private"
    index = _index("RA")
    first = private / "first.json"
    assert ev.run(_historical_args(private, out=str(first), allow_empty=True),
                  fetcher=_inline_fetcher(index)) == 0
    second = private / "second.json"
    args = _historical_args(
        private, out=str(second), allow_empty=True,
        after_report_number="RA",
        expected_index_sha256=hashlib.sha256(index.encode()).hexdigest(),
        resume_receipt=str(first),
    )
    assert ev.run(args, fetcher=_inline_fetcher(index)) == 1
    assert json.loads(second.read_text(encoding="utf-8"))["kind"] == "failure"


def test_review_regression_malformed_authority_gets_locator_status(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    malformed = ("number,url,latestPubDate,title,latestHTML\n"
                 "RA,https://[bad/reports/RA.json,2025-01-01,Current,files/x.html\n")
    assert ev.run(_historical_args(private, allow_empty=True),
                  fetcher=_inline_fetcher(malformed)) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status_rows"] == [{"id": "RA", "status": "invalid-metadata-locator"}]


def test_review_regression_one_status_one_summary_skip(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    assert ev.run(_historical_args(private, allow_empty=True),
                  fetcher=_inline_fetcher(
                      _index("RA"), {"RA": {"id": "RA", "versions": [_version()]}},
                  )) == 0
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["status_rows"] == [{"id": "RA", "status": "html-fetch-failed"}]
    summary = receipt["summary"]
    assert summary["skipped_network_error"] == 1
    assert summary["skipped_filtered"] == 0
    assert len(summary["skip_log"]) == 1


def test_review_regression_empty_index_frame_has_no_invalid_batch(tmp_path):
    private = tmp_path / "ai-prose-baselines-private"
    index = "number,url,latestPubDate,title,latestHTML\n"
    fetcher = _inline_fetcher(index)
    assert ev.run(_historical_args(private, allow_empty=True), fetcher=fetcher) == 1
    receipt = json.loads((private / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["kind"] == "failure"
    assert receipt["batch_attempted"] == 0
    assert receipt["failure_reason"] == "empty-report-frame"
    assert fetcher.fetched_urls == [CSV_URL]

if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
