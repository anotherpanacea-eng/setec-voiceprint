#!/usr/bin/env python3
"""Regression tests for acquire_openalex_core.py.

Mocks both APIs with `acquisition_core.FixtureFetcher`. Fixtures under
``scripts/test_data/acquire_openalex_core_fixture/``:

  * openalex_works.json — 4 works (all with DOIs): d1/d2 have CORE full
    text, d3 is absent from CORE, d4's CORE full text is too short.
  * core_d1..d4.json     — the per-DOI CORE responses (d3 = empty results).

Invariants: OpenAlex cursor pagination; DOI parse; the CORE-by-DOI full-text
join; the no-fulltext skip; the min-words gate (d4 dropped); the impostor
schema with register scholarly_article; dedupe; the privacy guard; argparse;
the CORE api_key added only at the fetch boundary (never in the stored
source_url); and a manifest-validator integration.

No third-party deps: OpenAlex/CORE responses are JSON and CORE fullText is
plain text, so this suite runs without bs4/requests.
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
    import acquire_openalex_core as oc  # type: ignore
    import manifest_validator as mv  # type: ignore
except ImportError as _e:  # pragma: no cover
    _ok = False
    _reason = str(_e)

if pytest is not None and not _ok:
    pytestmark = pytest.mark.skip(reason=_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquire_openalex_core_fixture"
KEY = "TESTKEY"
DOIS = ["10.1234/d1", "10.1234/d2", "10.1234/d3", "10.1234/d4"]


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        api_key=KEY,
        openalex_filter=oc.DEFAULT_OPENALEX_FILTER,
        persona="scholar",
        author="",
        impostor_for=["argscope_scholarly_article"],
        register="scholarly_article",
        register_match="high",
        topic_match="medium",
        consent_status="cc_licensed",
        era="pre_chatgpt",
        since="2000-01-01",
        until="2021-12-31",
        max_items=500,
        min_words=300,
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


def _filter_str() -> str:
    return (
        f"{oc.DEFAULT_OPENALEX_FILTER}"
        ",from_publication_date:2000-01-01,to_publication_date:2021-12-31"
    )


def fixture_url_map() -> dict:
    m = {
        oc._openalex_works_url(_filter_str(), cursor="*"): "openalex_works.json",
    }
    for doi in DOIS:
        keyed = oc._add_query(oc._core_doi_search_url(doi), api_key=KEY)
        m[keyed] = f"core_{doi.split('/')[-1]}.json"
    return m


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


# ------------------- Unit helpers --------------------------------


def test_add_query_overrides_without_dup():
    u = oc._add_query("https://x.test/a?b=1", api_key="K")
    u2 = oc._add_query(u, api_key="K2")
    assert u2.count("api_key=") == 1 and "api_key=K2" in u2


def test_bare_doi():
    assert oc._bare_doi("https://doi.org/10.1/x") == "10.1/x"
    assert oc._bare_doi("http://doi.org/10.1/x") == "10.1/x"
    assert oc._bare_doi("doi:10.1/x") == "10.1/x"
    assert oc._bare_doi("10.1/x") == "10.1/x"
    assert oc._bare_doi("") == ""


def test_first_author_and_date():
    work = {
        "authorships": [{"author": {"display_name": "Jane Scholar"}}],
        "publication_date": "2018-05-10", "publication_year": 2018,
    }
    assert oc._first_author(work) == "Jane Scholar"
    assert oc._work_date(work) == dt.date(2018, 5, 10)
    # Year-only fallback.
    assert oc._work_date({"publication_year": 2015}) == dt.date(2015, 1, 1)
    assert oc._first_author({}) == "Unknown"


def test_core_doi_search_url_is_key_free():
    u = oc._core_doi_search_url("10.1234/d1")
    assert "api_key" not in u
    assert "doi" in u and "d1" in u


def test_iter_openalex_follows_cursor():
    f = "x"
    page1_url = oc._openalex_works_url(f, cursor="*")
    page2_url = oc._openalex_works_url(f, cursor="CUR2")
    p1 = ac.FetchResult(
        url=page1_url, status=200, final_url=page1_url,
        text=json.dumps({"results": [{"id": "W1"}], "meta": {"next_cursor": "CUR2"}}),
    )
    p2 = ac.FetchResult(
        url=page2_url, status=200, final_url=page2_url,
        text=json.dumps({"results": [{"id": "W2"}], "meta": {"next_cursor": None}}),
    )
    fetcher = ac.FixtureFetcher(
        url_map={page1_url: p1, page2_url: p2},
        rate_limit_seconds=0.0, respect_robots=False,
    )
    ids = [w["id"] for w in oc._iter_openalex(f, fetcher)]
    assert ids == ["W1", "W2"]


# ------------------- Discovery + extraction ----------------------


def test_discover_yields_candidates():
    options = oc.parse_options(make_args())
    items = list(oc.discover_items(options, make_fetcher()))
    assert len(items) == 4
    d1 = [it for it in items if it.doi == "10.1234/d1"][0]
    assert d1.title == "On the Justification of Legal Norms"
    assert d1.author == "Jane Scholar"
    assert d1.date == dt.date(2018, 5, 10)
    # Stored locator is the clean DOI URL (no credential).
    assert d1.locator == "https://doi.org/10.1234/d1"
    assert "api_key" not in d1.locator


def test_extract_one_core_join():
    options = oc.parse_options(make_args())
    fetcher = make_fetcher()
    d1 = oc.ItemMeta(locator="https://doi.org/10.1234/d1", title="T",
                     date=dt.date(2018, 5, 10), doi="10.1234/d1", author="Jane Scholar")
    body, title, author, date = oc.extract_one(d1, options, fetcher)
    assert "legal norms" in body.lower()
    assert author == "Jane Scholar"
    # d3 has no CORE record → empty body (skip signal).
    d3 = oc.ItemMeta(locator="https://doi.org/10.1234/d3", doi="10.1234/d3")
    assert oc.extract_one(d3, options, fetcher)[0] == ""


# ------------------- End-to-end ----------------------------------


def test_end_to_end(tmp_path):
    """d1 + d2 acquired; d3 skipped (no CORE fulltext); d4 dropped (short)."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "scholarly_article" / "scholar"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    rc = oc.run(args, fetcher=make_fetcher())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired articles, got {[f.name for f in txt_files]}"

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    assert {e["author"] for e in entries} == {"Jane Scholar", "Robert Theorist"}
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["register"] == "scholarly_article"
        assert e["consent_status"] == "cc_licensed"
        assert e["impostor_for"] == ["argscope_scholarly_article"]
        assert e["acquired_via"].startswith("acquire_openalex_core_")
        assert e["persona"] == "scholar"
        # The CORE api_key must not leak into the stored source.
        assert "api_key" not in e.get("source", "")
        assert KEY not in e.get("source", "")
    assert len({e["content_hash"] for e in entries}) == 2


def test_min_words_gate_high_drops_all(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    oc.run(args, fetcher=make_fetcher())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_short_article_dropped(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "sh"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    oc.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    # d4 ("A Short Research Note") is below the floor.
    assert not any("Short Research Note" in (e.get("source") or "") for e in entries)
    assert not any("d4" in (e.get("source") or "") for e in entries)


def test_author_override(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "ov"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
        author="Scholarly Article Pool",
    )
    oc.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert entries and all(e["author"] == "Scholarly Article Pool" for e in entries)


def test_dedupe(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    oc.run(args, fetcher=make_fetcher())
    first = len(list(output_dir.glob("*.txt")))
    assert first == 2
    oc.run(args, fetcher=make_fetcher())
    assert len(list(output_dir.glob("*.txt"))) == first
    assert len(read_manifest(manifest_path)) == 2


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = oc.run(args, fetcher=make_fetcher())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


# ------------------- api_key handling ----------------------------


def test_core_key_default_empty(monkeypatch):
    monkeypatch.delenv("CORE_API_KEY", raising=False)
    assert oc.parse_options(make_args(api_key=None)).core_api_key == ""


def test_core_key_from_env(monkeypatch):
    monkeypatch.setenv("CORE_API_KEY", "ENVKEY")
    assert oc.parse_options(make_args(api_key=None)).core_api_key == "ENVKEY"


def test_core_key_added_at_fetch_only():
    """The CORE fetch carries the key; OpenAlex (keyless) does not, and the
    key never appears in a stored locator."""
    options = oc.parse_options(make_args(api_key="SECRET"))
    fetcher = make_fetcher({})  # empty map → 404s, but record URLs
    list(oc.discover_items(options, fetcher))  # OpenAlex only
    assert fetcher.fetched_urls
    assert all("api_key" not in u for u in fetcher.fetched_urls), \
        "OpenAlex discovery is keyless"
    # Now the CORE fetch must carry the key.
    f2 = make_fetcher({})
    oc._core_fulltext("10.1/x", options, f2)
    assert any("api_key=SECRET" in u for u in f2.fetched_urls)


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
            oc.run(args, fetcher=make_fetcher())
        assert exc.value.code == 2
    else:
        try:
            oc.run(args, fetcher=make_fetcher())
            assert False
        except SystemExit as e:
            assert e.code == 2


def test_argparse_rejects_missing_required():
    parser = oc.build_arg_parser()
    for argv in (
        ["--register", "scholarly_article", "--consent-status", "cc_licensed"],
        ["--impostor-for", "x", "--consent-status", "cc_licensed"],
        ["--impostor-for", "x", "--register", "scholarly_article"],
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
    help_text = oc.build_arg_parser().format_help()
    for flag in (
        "--api-key", "--openalex-filter", "--persona", "--impostor-for",
        "--register", "--consent-status", "--since", "--until",
        "--max-items", "--min-words", "--dry-run", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_emitted_manifest_validates_with_scholarly_article(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    oc.run(args, fetcher=make_fetcher())

    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline", "path": "fake_baseline.txt",
        "author": "Operator", "persona": "argscope_scholarly_article",
        "register": "scholarly_article", "ai_status": "pre_ai_human",
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
        and "scholarly_article" in i.get("message", "")
    ]
    assert unknown_register == [], \
        f"scholarly_article should be a known register: {unknown_register}"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
