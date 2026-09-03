"""Fixture-backed tests for the Tanner public-metadata list builder."""

from __future__ import annotations

import argparse
import http.server
import importlib.util
import json
import math
from pathlib import Path
import threading

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
MODULE_PATH = SCRIPTS / "acquisition_sources" / "build_tanner_source_list.py"
SPEC = importlib.util.spec_from_file_location("build_tanner_source_list", MODULE_PATH)
assert SPEC and SPEC.loader
tanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tanner)

FIXTURE_DIR = SCRIPTS / "test_data" / "tanner_source_list_fixture"
SITEMAP = tanner.DEFAULT_SITEMAP_URL
ALPHA = "https://tannerlectures.org/lectures/alpha/"
ZETA = "https://tannerlectures.org/lectures/zeta/"
NO_PDF = "https://tannerlectures.org/lectures/no-pdf/"
BROKEN = "https://tannerlectures.org/lectures/broken/"


class FakeFetcher:
    def __init__(self, mapping):
        self.mapping = mapping
        self.fetched_urls = []

    def fetch(self, url):
        self.fetched_urls.append(url)
        target = self.mapping.get(url)
        if target is None:
            return tanner.FetchResult(url, 404, "", url)
        if isinstance(target, tanner.FetchResult):
            return target
        path = FIXTURE_DIR / target
        return tanner.FetchResult(url, 200, path.read_text(encoding="utf-8"), url)


def make_args(tmp_path, **overrides):
    base = dict(
        output=str(tmp_path / "tanner.jsonl"),
        state=str(tmp_path / "tanner.state.json"),
        sitemap_url=SITEMAP,
        rate_limit=10.0,
        timeout=1.0,
        user_agent=tanner.DEFAULT_USER_AGENT.format(version=tanner.SCRIPT_VERSION),
        max_pages=None,
        resume=False,
        retry_errors=False,
        allow_empty=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def full_mapping():
    return {
        SITEMAP: "sitemap.xml",
        ALPHA: "alpha.html",
        ZETA: "zeta.html",
        NO_PDF: "no_pdf.html",
        BROKEN: "broken.html",
    }


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def test_sitemap_filters_deduplicates_and_sorts():
    source = (FIXTURE_DIR / "sitemap.xml").read_text(encoding="utf-8")
    assert tanner.parse_sitemap(SITEMAP, source) == [ALPHA, BROKEN, NO_PDF, ZETA]


def test_sitemap_rejects_invalid_and_empty():
    with pytest.raises(tanner.TannerListError, match="invalid sitemap"):
        tanner.parse_sitemap(SITEMAP, "<not-closed>")
    with pytest.raises(tanner.TannerListError, match="no Tanner lecture"):
        tanner.parse_sitemap(SITEMAP, "<urlset />")


def test_inventory_hash_uses_unambiguous_framing():
    one_loc_with_newline = ["https://example.test/a\nhttps://example.test/b"]
    two_locs = ["https://example.test/a", "https://example.test/b"]
    assert (
        tanner._inventory_sha256(one_loc_with_newline)
        != tanner._inventory_sha256(two_locs)
    )


def test_page_parser_maps_exact_feed_fields_and_resolves_relative_pdf():
    rows = tanner.parse_lecture_page(
        ALPHA, (FIXTURE_DIR / "alpha.html").read_text(encoding="utf-8"),
    )
    assert rows == [{
        "url": "https://tannerlectures.org/wp-content/uploads/2024/07/example.pdf",
        "title": "A Synthetic Lecture",
        "author": "Ada Example",
        "date": "1979-05-04",
        "artifact_profile": "tanner",
    }]
    assert set(rows[0]) == {
        "url", "title", "author", "date", "artifact_profile",
    }


def test_void_element_cannot_extend_transcript_scope_to_unrelated_pdf():
    source = """
        <h1 class="page-title">A Synthetic Lecture</h1>
        <div class="speaker-name">Ada Example</div>
        <div class="lecture-date">January 1, 2000</div>
        <div class="lecture-transcript"><br></div>
        <div><a href="outside.pdf">Unrelated PDF</a></div>
    """
    assert tanner.parse_lecture_page(ALPHA, source) == []


@pytest.mark.parametrize("bad_url", [
    "https://@/a.pdf",
    "https://example.test/a\x80.pdf",
    "https://example.test/a\x9f.pdf",
    "https://[/a.pdf",
])
def test_page_parser_and_state_guard_reject_same_invalid_pdf_urls(bad_url):
    source = f"""
        <h1 class="page-title">T</h1>
        <div class="speaker-name">A</div>
        <div class="lecture-date">January 1, 2000</div>
        <div class="lecture-transcript"><a href="{bad_url}">PDF</a></div>
    """
    assert tanner._is_pdf_url(bad_url) is False
    assert tanner.parse_lecture_page(ALPHA, source) == []


def test_pdf_page_missing_required_metadata_refuses():
    with pytest.raises(tanner.TannerListError, match="date"):
        tanner.parse_lecture_page(
            BROKEN, (FIXTURE_DIR / "broken.html").read_text(encoding="utf-8"),
        )


def test_fresh_run_records_all_outcomes_and_never_fetches_pdf(tmp_path):
    fetcher = FakeFetcher(full_mapping())
    args = make_args(tmp_path)
    assert tanner.run(args, fetcher=fetcher) == 1  # parse_error remains visible
    assert fetcher.fetched_urls == [SITEMAP, ALPHA, BROKEN, NO_PDF, ZETA]
    assert not any(url.endswith(".pdf") for url in fetcher.fetched_urls)
    rows = read_jsonl(args.output)
    assert [row["url"] for row in rows] == [
        "https://tannerlectures.org/wp-content/uploads/2024/07/example.pdf",
        "https://tannerlectures.org/wp-content/uploads/2024/07/zeta.pdf",
    ]
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    assert {result["status"] for result in state["pages"].values()} == {
        "pdf_link_found", "no_pdf", "parse_error",
    }


def test_plain_resume_fetches_no_recorded_page_of_any_status(tmp_path):
    first = FakeFetcher({
        SITEMAP: "sitemap.xml", ALPHA: "alpha.html", ZETA: "zeta.html",
        NO_PDF: "no_pdf.html",
        # Unmapped BROKEN creates fetch_error; replace ZETA with parse_error.
    })
    args = make_args(tmp_path)
    assert tanner.run(args, fetcher=first) == 1
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    state["pages"][ZETA] = {
        "status": "parse_error", "rows": [], "detail": "synthetic",
    }
    Path(args.state).write_text(json.dumps(state), encoding="utf-8")

    second = FakeFetcher({SITEMAP: "sitemap.xml"})
    resumed = make_args(tmp_path, resume=True)
    assert tanner.run(resumed, fetcher=second) == 1
    assert second.fetched_urls == [SITEMAP]
    statuses = {
        result["status"] for result in
        json.loads(Path(args.state).read_text(encoding="utf-8"))["pages"].values()
    }
    assert statuses == {
        "pdf_link_found", "no_pdf", "fetch_error", "parse_error",
    }


def test_retry_errors_revisits_only_error_pages(tmp_path):
    first = FakeFetcher({
        SITEMAP: "sitemap.xml", ALPHA: "alpha.html", NO_PDF: "no_pdf.html",
        ZETA: "broken.html",
    })
    args = make_args(tmp_path)
    assert tanner.run(args, fetcher=first) == 1
    retry = FakeFetcher({
        SITEMAP: "sitemap.xml", BROKEN: "alpha.html", ZETA: "zeta.html",
    })
    resumed = make_args(tmp_path, resume=True, retry_errors=True)
    assert tanner.run(resumed, fetcher=retry) == 0
    assert retry.fetched_urls == [SITEMAP, BROKEN, ZETA]


def test_partial_resume_matches_fresh_output_bytes(tmp_path):
    partial_dir = tmp_path / "partial"
    fresh_dir = tmp_path / "fresh"
    partial = make_args(partial_dir, max_pages=1)
    assert tanner.run(partial, fetcher=FakeFetcher(full_mapping())) == 0
    resumed = make_args(partial_dir, resume=True)
    assert tanner.run(resumed, fetcher=FakeFetcher(full_mapping())) == 1
    fresh = make_args(fresh_dir)
    assert tanner.run(fresh, fetcher=FakeFetcher(full_mapping())) == 1
    assert Path(resumed.output).read_bytes() == Path(fresh.output).read_bytes()


def test_corrupt_resume_refuses(tmp_path):
    args = make_args(tmp_path, max_pages=1)
    assert tanner.run(args, fetcher=FakeFetcher(full_mapping())) == 0
    Path(args.state).write_text("{broken", encoding="utf-8")
    with pytest.raises(tanner.TannerListError, match="corrupt"):
        tanner.run(make_args(tmp_path, resume=True), fetcher=FakeFetcher(full_mapping()))


@pytest.mark.parametrize("bad_row", [
    {"url": " ", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a.pdf", "title": " ", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a.pdf", "title": "Title", "author": " ", "date": "2000-01-01"},
    {"url": "ftp://example.test/a.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a.txt", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a\n.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a\x00.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://@/a.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test:bad/a.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://[/a.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a\x80.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
    {"url": "https://example.test/a\x9f.pdf", "title": "Title", "author": "Author", "date": "2000-01-01"},
])
def test_resume_refuses_malformed_pdf_rows(tmp_path, bad_row):
    args = make_args(tmp_path, max_pages=1)
    assert tanner.run(args, fetcher=FakeFetcher(full_mapping())) == 0
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    state["pages"][ALPHA]["rows"] = [bad_row]
    Path(args.state).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tanner.TannerListError, match="invalid PDF row"):
        tanner.run(
            make_args(tmp_path, resume=True), fetcher=FakeFetcher(full_mapping()),
        )


def test_resume_refuses_page_outside_bound_sitemap(tmp_path):
    args = make_args(tmp_path, max_pages=1)
    assert tanner.run(args, fetcher=FakeFetcher(full_mapping())) == 0
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    state["pages"]["https://tannerlectures.org/lectures/injected/"] = {
        "status": "no_pdf", "rows": [], "detail": "",
    }
    Path(args.state).write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(tanner.TannerListError, match="outside"):
        tanner.run(make_args(tmp_path, resume=True), fetcher=FakeFetcher(full_mapping()))


def test_zero_pdf_output_fails_unless_explicitly_allowed(tmp_path):
    xml = tanner.FetchResult(
        SITEMAP, 200,
        "<urlset><url><loc>https://tannerlectures.org/lectures/no-pdf/</loc></url></urlset>",
    )
    mapping = {SITEMAP: xml, NO_PDF: "no_pdf.html"}
    assert tanner.run(make_args(tmp_path), fetcher=FakeFetcher(mapping)) == 1
    other = tmp_path / "allowed"
    assert tanner.run(
        make_args(other, allow_empty=True), fetcher=FakeFetcher(mapping),
    ) == 0


def test_output_and_state_must_resolve_to_different_files(tmp_path):
    alias_dir = tmp_path / "alias"
    alias_dir.mkdir()
    output = tmp_path / "same.json"
    aliased_state = alias_dir / ".." / "same.json"
    args = make_args(tmp_path, output=str(output), state=str(aliased_state))
    with pytest.raises(tanner.TannerListError, match="different files"):
        tanner.run(args, fetcher=FakeFetcher(full_mapping()))


def test_rate_limit_floor_is_parser_enforced():
    parser = tanner.build_arg_parser()
    for value in ("9.9", "nan", "inf", "-inf"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--output", "x.jsonl", "--rate-limit", value])


def test_timeout_is_finite_and_positive_at_parser_and_fetcher_boundaries():
    parser = tanner.build_arg_parser()
    for value in ("0", "-1", "nan", "inf", "-inf"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--output", "x.jsonl", "--timeout", value])
    for value in (0.0, -1.0, math.nan, math.inf, -math.inf):
        with pytest.raises(tanner.TannerListError, match="timeout"):
            tanner.PoliteHttpFetcher(
                rate_limit_seconds=10.0, user_agent="test", timeout=value,
            )


def test_fetcher_bounds_malformed_user_agent_and_url_transport_inputs():
    with pytest.raises(tanner.TannerListError, match="user agent"):
        tanner.PoliteHttpFetcher(
            rate_limit_seconds=10.0, user_agent="bad\r\nheader", timeout=1.0,
        )
    fetcher = tanner.PoliteHttpFetcher(
        rate_limit_seconds=10.0, user_agent="test", timeout=1.0,
    )
    assert fetcher._request("https://[/broken").status == 0


@pytest.mark.parametrize("robots_status", [0, 500, 503])
def test_robots_transport_or_server_failure_never_fetches_page(
    monkeypatch, robots_status,
):
    monkeypatch.setattr(tanner.time, "sleep", lambda _seconds: None)
    fetcher = tanner.PoliteHttpFetcher(
        rate_limit_seconds=10.0, user_agent="test", timeout=1.0,
    )
    calls = []

    def request(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return tanner.FetchResult(url, robots_status, "")
        return tanner.FetchResult(url, 200, "lecture")

    monkeypatch.setattr(fetcher, "_request", request)
    result = fetcher.fetch("https://example.test/lectures/x/")
    assert result.status == 0
    assert calls == ["https://example.test/robots.txt"]


def test_confirmed_absent_robots_file_explicitly_allows_page(monkeypatch):
    monkeypatch.setattr(tanner.time, "sleep", lambda _seconds: None)
    fetcher = tanner.PoliteHttpFetcher(
        rate_limit_seconds=10.0, user_agent="test", timeout=1.0,
    )
    calls = []

    def request(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return tanner.FetchResult(url, 404, "")
        return tanner.FetchResult(url, 200, "lecture")

    monkeypatch.setattr(fetcher, "_request", request)
    result = fetcher.fetch("https://example.test/lectures/x/")
    assert result.status == 200
    assert calls == [
        "https://example.test/robots.txt",
        "https://example.test/lectures/x/",
    ]


@pytest.mark.parametrize("redirect_path", ["/robots.txt", "/lectures/x/"])
def test_redirect_never_reaches_an_unvetted_destination_origin(
    monkeypatch, redirect_path,
):
    monkeypatch.setattr(tanner.time, "sleep", lambda _seconds: None)
    destination_hits = []

    class Destination(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            destination_hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"destination must remain unreachable")

        def log_message(self, _format, *_args):
            pass

    destination = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    destination_thread = threading.Thread(
        target=destination.serve_forever, daemon=True,
    )
    destination_thread.start()
    destination_url = (
        f"http://127.0.0.1:{destination.server_port}/redirected"
    )

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == redirect_path:
                self.send_response(302)
                self.send_header("Location", destination_url)
            elif self.path == "/robots.txt":
                self.send_response(404)
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    try:
        fetcher = tanner.PoliteHttpFetcher(
            rate_limit_seconds=10.0, user_agent="test", timeout=1.0,
        )
        page_url = f"http://127.0.0.1:{origin.server_port}/lectures/x/"
        result = fetcher.fetch(page_url)
        assert not result.ok
        assert destination_hits == []
    finally:
        origin.shutdown()
        origin.server_close()
        origin_thread.join(timeout=2)
        destination.shutdown()
        destination.server_close()
        destination_thread.join(timeout=2)


def test_polite_fetcher_rejects_nonfinite_rate_limits():
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(tanner.TannerListError, match="rate limit"):
            tanner.PoliteHttpFetcher(
                rate_limit_seconds=value, user_agent="test", timeout=1.0,
            )
