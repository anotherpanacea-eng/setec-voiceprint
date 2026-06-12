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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


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


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
