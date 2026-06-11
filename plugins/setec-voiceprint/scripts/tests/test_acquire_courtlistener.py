#!/usr/bin/env python3
"""Regression tests for acquire_courtlistener.py + the Fetcher extra_headers
shared-core addition.

Mocks the CourtListener v4 API with `acquisition_core.FixtureFetcher`.
Fixtures under ``scripts/test_data/acquire_courtlistener_fixture/``:

  * search.json   — 5 RECAP results in the real v4 shape (short_description,
    snippet, entry_date_filed): two briefs, a motion (fails the brief-type
    filter), a short reply brief, and a brief with an empty snippet (fails
    the text-availability gate).
  * recap_101/102/104.json — the per-document detail responses with
    plain_text (104 is below the word floor).

Invariants: the snippet text-availability gate; the short_description
brief-type filter; cursor pagination; the plain_text join; the min-words
gate; the impostor schema with register
legal_brief; dedupe; the privacy guard; argparse; the auth-header plumbing
(make_requests_fetcher carries the token; the token never enters a stored
source_url); and that the new optional Fetcher param does not disturb the
existing Fetcher / FixtureFetcher. No third-party deps (JSON + plaintext).
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
    import acquire_courtlistener as cl  # type: ignore
    import manifest_validator as mv  # type: ignore
except ImportError as _e:  # pragma: no cover
    _ok = False
    _reason = str(_e)

if pytest is not None and not _ok:
    pytestmark = pytest.mark.skip(reason=_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquire_courtlistener_fixture"
TOKEN = "TESTTOKEN"


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        api_key=TOKEN,
        query="brief",
        persona="courtlistener",
        author="",
        impostor_for=["argscope_legal_brief"],
        register="legal_brief",
        register_match="high",
        topic_match="medium",
        consent_status="public_record",
        era="pre_chatgpt",
        since="2000-01-01",
        until="2021-12-31",
        max_items=400,
        min_words=150,
        output_dir=None,
        emit_manifest=None,
        out=None,
        rate_limit=0.0,
        user_agent=None,
        dry_run=False,
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def fixture_url_map() -> dict:
    # discover_items applies the date window server-side, so the search key
    # must carry the same filed_after/filed_before as make_args' since/until.
    return {
        cl._search_url("brief", dt.date(2000, 1, 1), dt.date(2021, 12, 31)): "search.json",
        cl._recap_doc_url("101"): "recap_101.json",
        cl._recap_doc_url("102"): "recap_102.json",
        cl._recap_doc_url("104"): "recap_104.json",
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


# ------------------- Shared-core: Fetcher extra_headers ----------


def test_auth_headers():
    assert cl._auth_headers("TOK") == {"Authorization": "Token TOK"}
    assert cl._auth_headers("") == {}


def test_fetcher_extra_headers_attr_default_and_set():
    # Base Fetcher carries the new optional param...
    assert ac.Fetcher().extra_headers == {}
    assert ac.Fetcher(
        extra_headers={"Authorization": "Token X"}
    ).extra_headers == {"Authorization": "Token X"}
    # ...and the existing FixtureFetcher is unaffected (defaults to empty).
    assert ac.FixtureFetcher(url_map={}).extra_headers == {}


def test_make_requests_fetcher_carries_extra_headers():
    try:
        f = ac.make_requests_fetcher(extra_headers={"Authorization": "Token X"})
    except RuntimeError:
        if pytest is not None:
            pytest.skip("requests not installed")
        return
    assert f.extra_headers == {"Authorization": "Token X"}


# ------------------- URL + filter helpers ------------------------


def test_search_and_doc_urls_are_token_free():
    s = cl._search_url("brief")
    assert "type=rd" in s and "q=brief" in s
    assert "Token" not in s and "Authorization" not in s
    d = cl._recap_doc_url("101")
    assert d.endswith("/recap-documents/101/")
    assert "Token" not in d


def test_is_brief():
    assert cl._is_brief("Brief for Appellant")
    assert cl._is_brief("Amicus Brief of the Association")
    assert cl._is_brief("Memorandum in Support of Summary Judgment")
    assert not cl._is_brief("Motion for Extension of Time")
    assert not cl._is_brief("Notice of Appearance")
    assert not cl._is_brief("")


def test_iter_search_follows_next():
    p1url = cl._search_url("brief")
    p2url = "https://www.courtlistener.com/api/rest/v4/search/?type=rd&q=brief&cursor=CUR2"
    p1 = ac.FetchResult(url=p1url, status=200, final_url=p1url,
                        text=json.dumps({"results": [{"id": 1}], "next": p2url}))
    p2 = ac.FetchResult(url=p2url, status=200, final_url=p2url,
                        text=json.dumps({"results": [{"id": 2}], "next": None}))
    fetcher = ac.FixtureFetcher(url_map={p1url: p1, p2url: p2},
                                rate_limit_seconds=0.0, respect_robots=False)
    ids = [r["id"] for r in cl._iter_search("brief", fetcher)]
    assert ids == [1, 2]


class _SequenceFetcher:
    """Returns a queued FetchResult per call (to simulate transient failures)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]


def test_iter_search_retries_transient_page_failure(monkeypatch):
    """A failed search page is retried (not fatal): one 503 then a good page
    still yields results."""
    monkeypatch.setattr(cl, "_RETRY_SLEEP_SECONDS", 0)
    url = cl._search_url("brief")
    down = ac.FetchResult(url=url, status=503, final_url=url, text="")
    good = ac.FetchResult(url=url, status=200, final_url=url,
                          text=json.dumps({"results": [{"id": 7}], "next": None}))
    f = _SequenceFetcher([down, good])
    ids = [r["id"] for r in cl._iter_search("brief", f)]
    assert ids == [7]
    assert f.calls == 2  # failed once, retried, succeeded


def test_iter_search_gives_up_after_retries(monkeypatch):
    """A persistently failing page stops discovery after _SEARCH_RETRIES
    attempts (no infinite loop, no crash)."""
    monkeypatch.setattr(cl, "_RETRY_SLEEP_SECONDS", 0)
    url = cl._search_url("brief")
    down = ac.FetchResult(url=url, status=429, final_url=url, text="")
    f = _SequenceFetcher([down])
    assert list(cl._iter_search("brief", f)) == []
    assert f.calls == cl._SEARCH_RETRIES


# ------------------- Discovery + extraction ----------------------


def test_discover_filters_to_briefs():
    options = cl.parse_options(make_args())
    items = list(cl.discover_items(options, make_fetcher()))
    # 101, 102, 104 are text-bearing briefs (snippet + brief short_description);
    # 103 (motion) fails the brief filter; 105 (empty snippet) fails the
    # text-availability gate.
    assert {it.doc_id for it in items} == {"101", "102", "104"}
    b1 = [it for it in items if it.doc_id == "101"][0]
    assert b1.title == "Brief for Appellant"   # from short_description
    assert b1.date == dt.date(2018, 5, 10)     # from entry_date_filed
    assert b1.locator == cl._recap_doc_url("101")
    assert "Token" not in b1.locator


def test_discover_requires_indexed_text():
    """A brief-labelled result with an empty snippet (no indexed body) is
    dropped: fetching its detail would only yield empty plain_text."""
    options = cl.parse_options(make_args())
    items = list(cl.discover_items(options, make_fetcher()))
    assert "105" not in {it.doc_id for it in items}


def test_extract_one_plain_text():
    options = cl.parse_options(make_args())
    fetcher = make_fetcher()
    b1 = cl.ItemMeta(locator=cl._recap_doc_url("101"), title="Brief for Appellant",
                     date=dt.date(2018, 5, 10), doc_id="101")
    body, title, author, date = cl.extract_one(b1, options, fetcher)
    assert "summary of argument" in body.lower()
    assert author == cl.DEFAULT_AUTHOR


# ------------------- End-to-end ----------------------------------


def test_end_to_end(tmp_path):
    """101 + 102 acquired; 103 filtered (motion); 104 dropped (short)."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "legal_brief" / "courtlistener"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    rc = cl.run(args, fetcher=make_fetcher())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired briefs, got {[f.name for f in txt_files]}"

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["register"] == "legal_brief"
        assert e["consent_status"] == "public_record"
        assert e["impostor_for"] == ["argscope_legal_brief"]
        assert e["acquired_via"].startswith("acquire_courtlistener_")
        assert e["persona"] == "courtlistener"
        # The token is header-only; it must never appear in a stored source.
        assert "Token" not in e.get("source", "")
        assert TOKEN not in e.get("source", "")
    assert len({e["content_hash"] for e in entries}) == 2


def test_min_words_gate_high_drops_all(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    cl.run(args, fetcher=make_fetcher())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_short_brief_dropped(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "sh"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    cl.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert not any("104" in (e.get("source") or "") for e in entries)


def test_author_override(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "ov"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
        author="Legal Brief Pool",
    )
    cl.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert entries and all(e["author"] == "Legal Brief Pool" for e in entries)


def test_dedupe(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    cl.run(args, fetcher=make_fetcher())
    first = len(list(output_dir.glob("*.txt")))
    assert first == 2
    cl.run(args, fetcher=make_fetcher())
    assert len(list(output_dir.glob("*.txt"))) == first
    assert len(read_manifest(manifest_path)) == 2


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = cl.run(args, fetcher=make_fetcher())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


# ------------------- api token handling --------------------------


def test_token_default_empty(monkeypatch):
    monkeypatch.delenv("COURTLISTENER_API_KEY", raising=False)
    assert cl.parse_options(make_args(api_key=None)).api_token == ""


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("COURTLISTENER_API_KEY", "ENVTOK")
    assert cl.parse_options(make_args(api_key=None)).api_token == "ENVTOK"


# ------------------- Privacy + argparse + validator --------------


def test_privacy_guard_refuses_non_private(tmp_path):
    public_dir = tmp_path / "public_oops"
    args = make_args(
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            cl.run(args, fetcher=make_fetcher())
        assert exc.value.code == 2
    else:
        try:
            cl.run(args, fetcher=make_fetcher())
            assert False
        except SystemExit as e:
            assert e.code == 2


def test_argparse_rejects_missing_required():
    parser = cl.build_arg_parser()
    for argv in (
        ["--register", "legal_brief", "--consent-status", "public_record"],
        ["--impostor-for", "x", "--consent-status", "public_record"],
        ["--impostor-for", "x", "--register", "legal_brief"],
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
    help_text = cl.build_arg_parser().format_help()
    for flag in (
        "--api-key", "--query", "--persona", "--impostor-for", "--register",
        "--consent-status", "--since", "--until", "--max-items", "--min-words",
        "--dry-run", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_emitted_manifest_validates_with_legal_brief(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    cl.run(args, fetcher=make_fetcher())

    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline", "path": "fake_baseline.txt",
        "author": "Operator", "persona": "argscope_legal_brief",
        "register": "legal_brief", "ai_status": "pre_ai_human",
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
        and "legal_brief" in i.get("message", "")
    ]
    assert unknown_register == [], \
        f"legal_brief should be a known register: {unknown_register}"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
