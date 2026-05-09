#!/usr/bin/env python3
"""Regression tests for acquire_magazine.py.

Strategy mirrors test_acquire_blog.py: an `acquisition_core.FixtureFetcher`
with a URL→fixture-file map drives the script through the full
discover-issues / discover-stories / fetch-story pipeline without
network access. Two magazine modules ship in v1 (Nightmare and The
Dark); both are exercised with the same kind of fixture HTML.

Test invariants (per the 2026-05-08 spec's required assertions):

  * Author-derived persona slugs are deterministic.
  * Per-author subdirectories are used in the output layout.
  * Draft manifest entries carry the impostor fields and
    ``use: ["voice_impostor"]``.
  * Cleaned text contains the story body without editorial /
    interview residue (Author Spotlight blocks, ebook widgets,
    share / comments cruft).
  * ``--filter-author`` excludes stories by other writers.
  * Privacy guard refuses non-private outputs.
  * Manifest validates clean against the schema.
"""

from __future__ import annotations

import argparse
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
    import acquire_magazine as am  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquisition_magazine_fixture"

# Nightmare URL map.
NIGHTMARE_URL = "https://nightmare-magazine.com"
NIGHTMARE_URLS = {
    f"{NIGHTMARE_URL}/issues/": "nightmare_archive.html",
    f"{NIGHTMARE_URL}/issues/issue-101/": "nightmare_issue_101.html",
    f"{NIGHTMARE_URL}/issues/issue-102/": "nightmare_issue_102.html",
    f"{NIGHTMARE_URL}/fiction/the-glass-room/": "nightmare_story_glass_room.html",
    f"{NIGHTMARE_URL}/fiction/quiet-house/": "nightmare_story_quiet_house.html",
    f"{NIGHTMARE_URL}/fiction/something-old/": "nightmare_story_something_old.html",
}

# The Dark URL map.
DARK_URL = "https://thedarkmagazine.com"
DARK_URLS = {
    f"{DARK_URL}/issues/": "the_dark_archive.html",
    f"{DARK_URL}/issues/issue-50/": "the_dark_issue_50.html",
    f"{DARK_URL}/the-bone-orchard/": "the_dark_story_bone_orchard.html",
}


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        magazine="nightmare",
        persona_from_author=True,
        persona=None,
        register="literary_horror",
        register_match="high",
        topic_match="medium",
        consent_status="fair_use_research",
        era="pre_chatgpt",
        impostor_for=["fiction"],
        filter_author=[],
        since=None,
        until=None,
        max_stories=30,
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


def make_fetcher(url_map: dict) -> ac.FixtureFetcher:
    return ac.FixtureFetcher(
        url_map=url_map, fixture_dir=FIXTURE_DIR,
        rate_limit_seconds=0.0, respect_robots=False,
    )


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ------------------- Module wiring -------------------------------


def test_magazine_modules_registered():
    """Both v1 magazines must be registered under the documented keys."""
    assert "nightmare" in am.MAGAZINE_MODULES
    assert "the_dark" in am.MAGAZINE_MODULES
    n = am.MAGAZINE_MODULES["nightmare"]
    assert n.archive_url == "https://nightmare-magazine.com/issues/"
    d = am.MAGAZINE_MODULES["the_dark"]
    assert d.archive_url == "https://thedarkmagazine.com/issues/"


# ------------------- Helper unit tests ---------------------------


def test_clean_author_strips_by_prefix():
    assert am._clean_author("By Brian Evenson") == "Brian Evenson"
    assert am._clean_author("by Kelly Link") == "Kelly Link"
    assert am._clean_author("BY  Carmen Maria Machado") == \
        "Carmen Maria Machado"
    assert am._clean_author("Kelly Link") == "Kelly Link"
    assert am._clean_author("") == ""


def test_select_text_picks_first_nonempty_selector_in_order():
    """When the comma-list contains both a parent and a more specific
    descendant, the descendant is preferred."""
    from bs4 import BeautifulSoup
    html = (
        '<div class="byline">By <a class="author">'
        'Inner Author</a></div>'
    )
    soup = BeautifulSoup(html, "lxml")
    # Author-first selector should pick the inner anchor.
    assert am._select_text(soup, ".author, .byline") == "Inner Author"


def test_author_filter_substring_match():
    """Filter is case-insensitive substring match — handles 'By X'
    and 'X' bylines alike."""
    assert am._author_matches_filter("Brian Evenson", ["Brian Evenson"])
    assert am._author_matches_filter("BRIAN EVENSON", ["brian evenson"])
    assert am._author_matches_filter("By Brian Evenson", ["Brian Evenson"])
    assert not am._author_matches_filter("Kelly Link", ["Brian Evenson"])
    # Empty filter list = no filtering.
    assert am._author_matches_filter("Anybody", [])


# ------------------- Discover / parse helpers --------------------


def test_discover_issue_urls_filters_self_link():
    """The /issues/ self-link should NOT be returned as an issue URL."""
    html = (FIXTURE_DIR / "nightmare_archive.html").read_text(encoding="utf-8")
    config = am.MAGAZINE_MODULES["nightmare"]
    urls = am.discover_issue_urls(
        html, config=config, base_url=config.archive_url,
    )
    # Both real issue URLs should be present.
    assert any("issue-101" in u for u in urls)
    assert any("issue-102" in u for u in urls)
    # The self-link to /issues/ should NOT be present.
    assert not any(u.rstrip("/").endswith("/issues") for u in urls)


def test_parse_issue_page_returns_story_metadata():
    """Issue TOC pages yield (title, author, url) per story."""
    html = (FIXTURE_DIR / "nightmare_issue_101.html").read_text(encoding="utf-8")
    config = am.MAGAZINE_MODULES["nightmare"]
    stories = am.parse_issue_page(
        html, config=config,
        base_url=f"{NIGHTMARE_URL}/issues/issue-101/",
    )
    assert len(stories) == 2
    titles = {s.title for s in stories}
    assert {"The Glass Room", "Quiet House"} <= titles
    authors = {s.author for s in stories}
    assert "Synthetic Author A" in authors
    assert "Synthetic Author B" in authors


def test_parse_story_page_strips_author_spotlight():
    """The 'Author Spotlight' interview block must NOT survive
    extraction. This is the spec's canonical post-body cruft case."""
    html = (FIXTURE_DIR / "nightmare_story_glass_room.html").read_text(
        encoding="utf-8",
    )
    config = am.MAGAZINE_MODULES["nightmare"]
    body, title, author, date = am.parse_story_page(html, config=config)
    assert title == "The Glass Room"
    assert author == "Synthetic Author A"
    assert "garden" in body.lower()
    # Author Spotlight contents must be gone.
    assert "Author Spotlight" not in body
    assert "influences and process" not in body.lower()
    assert "strip-after pass has failed" not in body
    # Share + comments cruft should also be gone.
    assert "Share this" not in body
    assert "Comments are closed" not in body


def test_parse_story_page_extracts_date():
    """Date is parsed from the <time datetime=...> attribute."""
    import datetime as dt
    html = (FIXTURE_DIR / "nightmare_story_glass_room.html").read_text(
        encoding="utf-8",
    )
    config = am.MAGAZINE_MODULES["nightmare"]
    _, _, _, date = am.parse_story_page(html, config=config)
    assert date == dt.date(2019, 3, 15)


# ------------------- End-to-end Nightmare ------------------------


def test_nightmare_end_to_end(tmp_path):
    """Full pipeline: 2 issues × stories → 3 written files (one per
    author), each in its own persona subdir, each with manifest
    entry carrying the impostor fields."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "nightmare"
    manifest = output_dir / "draft.jsonl"
    args = make_args(
        magazine="nightmare",
        output_dir=str(output_dir),
        emit_manifest=str(manifest),
    )
    fetcher = make_fetcher(NIGHTMARE_URLS)

    rc = am.run(args, fetcher=fetcher)
    assert rc == 0

    # 3 stories total: 2 in issue 101 (different authors) +
    # 1 in issue 102 (Excluded Writer, no filter so included).
    txt_files = list(output_dir.rglob("*.txt"))
    assert len(txt_files) == 3, \
        f"expected 3 stories, got {[f.name for f in txt_files]}"

    # Per-author subdir layout: each .txt lives in its own
    # persona-slug directory.
    parents = {f.parent.name for f in txt_files}
    assert len(parents) == 3, \
        f"expected 3 author subdirs, got {parents}"

    # Cleaned text doesn't carry the Author Spotlight block or
    # share widget.
    for f in txt_files:
        body = f.read_text(encoding="utf-8")
        assert "Author Spotlight" not in body
        assert "Share this" not in body
        assert "Comments are closed" not in body

    # Manifest entries.
    entries = read_jsonl(manifest)
    assert len(entries) == 3
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["use"] == ["voice_impostor"]
        assert e["split"] == "baseline"
        assert e["privacy"] == "private"
        assert e["register"] == "literary_horror"
        assert e["era"] == "pre_chatgpt"
        assert e["consent_status"] == "fair_use_research"
        assert e["impostor_for"] == ["fiction"]
        assert e["acquired_via"].startswith("acquire_magazine_nightmare_")
        assert e["content_hash"].startswith("sha256:")

    # Persona slugs are deterministic — three distinct slugs from
    # three distinct authors.
    personas = {e["persona"] for e in entries}
    assert len(personas) == 3


def test_filter_author_excludes_other_writers(tmp_path):
    """--filter-author cuts the run to the named subset.
    'Excluded Writer' should not produce a .txt or manifest entry."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "filtered"
    manifest = output_dir / "draft.jsonl"
    args = make_args(
        magazine="nightmare",
        filter_author=["Synthetic Author A", "Synthetic Author B"],
        output_dir=str(output_dir),
        emit_manifest=str(manifest),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))

    txt_files = list(output_dir.rglob("*.txt"))
    # Two stories acquired (A + B), one filtered out (Excluded).
    assert len(txt_files) == 2
    for f in txt_files:
        assert "something-old" not in f.name, \
            "Excluded Writer's story should have been filtered"

    entries = read_jsonl(manifest)
    authors = {e["author"] for e in entries}
    assert "Excluded Writer" not in authors


def test_filter_author_substring_match_works_against_byline_with_prefix(tmp_path):
    """Filter matches 'By Synthetic Author A' — the substring match
    handles the 'By ' prefix the magazine puts on bylines."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "byprefix"
    args = make_args(
        magazine="nightmare",
        filter_author=["Synthetic Author A"],  # no "By"
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    txt_files = list(output_dir.rglob("*.txt"))
    assert len(txt_files) == 1
    assert "glass-room" in txt_files[0].name


def test_persona_from_author_uses_deterministic_slug(tmp_path):
    """Persona slugs are derived from author names via the documented
    rule (lastname_firstname_personal). Same author → same slug
    across runs."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "perslug"
    args = make_args(
        magazine="nightmare",
        filter_author=["Synthetic Author A"],
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    entries = read_jsonl(output_dir / "draft.jsonl")
    assert len(entries) == 1
    persona = entries[0]["persona"]
    # The slug is deterministic; pin it.
    assert persona == ac.author_to_persona_slug("Synthetic Author A")
    assert persona.endswith("_personal")


def test_explicit_persona_lumps_all_stories(tmp_path):
    """--persona overrides the per-author rule and lumps every
    acquired story under one slug."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "lump"
    args = make_args(
        magazine="nightmare",
        persona_from_author=False,
        persona="combined_horror_pool",
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    entries = read_jsonl(output_dir / "draft.jsonl")
    assert len(entries) == 3
    personas = {e["persona"] for e in entries}
    assert personas == {"combined_horror_pool"}


# ------------------- End-to-end The Dark -------------------------


def test_the_dark_end_to_end(tmp_path):
    """The Dark archive → 1 story written, ebook widget stripped."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "thedark"
    manifest = output_dir / "draft.jsonl"
    args = make_args(
        magazine="the_dark",
        output_dir=str(output_dir),
        emit_manifest=str(manifest),
    )
    am.run(args, fetcher=make_fetcher(DARK_URLS))
    txt_files = list(output_dir.rglob("*.txt"))
    assert len(txt_files) == 1
    body = txt_files[0].read_text(encoding="utf-8")
    assert "orchard" in body.lower()
    # The ebook widget block gets stripped via `strip_after_selector`.
    assert "Buy ebook" not in body
    assert "Bottom-of-page" not in body
    entries = read_jsonl(manifest)
    assert len(entries) == 1
    assert entries[0]["acquired_via"].startswith("acquire_magazine_the_dark_")


def test_the_dark_pattern_excludes_author_pages():
    """The Dark's story_href_pattern explicitly excludes /author/<x>/
    so author profile links inside issue pages don't cause spurious
    network errors."""
    config = am.MAGAZINE_MODULES["the_dark"]
    import re
    pat = re.compile(config.story_href_pattern, re.IGNORECASE)
    assert pat.search("https://thedarkmagazine.com/the-bone-orchard/")
    assert not pat.search("https://thedarkmagazine.com/author/synthetic-c/")
    assert not pat.search("https://thedarkmagazine.com/issues/issue-50/")
    assert not pat.search("https://thedarkmagazine.com/category/horror/")


# ------------------- Date window --------------------------------


def test_since_until_filters_by_date(tmp_path):
    """--since/--until filters stories by date_written."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "window"
    args = make_args(
        magazine="nightmare",
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        since="2019-04-01",  # excludes both March 2019 stories
        until="2019-04-30",
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    txt_files = list(output_dir.rglob("*.txt"))
    # Only 'Something Old' (April 1) is in window.
    assert len(txt_files) == 1
    assert "something-old" in txt_files[0].name


# ------------------- Privacy guard ------------------------------


def test_privacy_guard_refuses_non_private(tmp_path):
    public = tmp_path / "public_oops"
    args = make_args(
        magazine="nightmare",
        output_dir=str(public),
        emit_manifest=str(public / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
        assert exc.value.code == 2
    else:
        try:
            am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
            assert False
        except SystemExit as e:
            assert e.code == 2


# ------------------- Dedupe --------------------------------------


def test_dedupe_within_persona_dir(tmp_path):
    """A second run against the same output dir produces no new
    .txt files (all 3 hashes already present)."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest = output_dir / "draft.jsonl"
    args = make_args(
        magazine="nightmare",
        output_dir=str(output_dir),
        emit_manifest=str(manifest),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    first_count = len(list(output_dir.rglob("*.txt")))
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    second_count = len(list(output_dir.rglob("*.txt")))
    assert second_count == first_count


# ------------------- Dry run -------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        magazine="nightmare",
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))
    assert not list(output_dir.rglob("*.txt"))
    assert not (output_dir / "draft.jsonl").exists()


# ------------------- CLI surface ---------------------------------


def test_cli_help_lists_required_flags():
    parser = am.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--magazine", "--persona-from-author", "--persona", "--register",
        "--register-match", "--topic-match", "--consent-status", "--era",
        "--impostor-for", "--filter-author", "--since", "--until",
        "--max-stories", "--output-dir", "--emit-manifest", "--out",
        "--rate-limit", "--user-agent", "--dry-run",
        "--allow-public-output", "--allow-non-prose", "--strip-rules",
        "--strip-aggressive",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_argparse_rejects_missing_impostor_for():
    parser = am.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args([
                "--magazine", "nightmare",
                "--register", "literary_horror",
                "--consent-status", "fair_use_research",
            ])


def test_argparse_rejects_unknown_magazine():
    parser = am.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args([
                "--magazine", "clarkesworld",  # not in v1
                "--register", "literary_horror",
                "--consent-status", "fair_use_research",
                "--impostor-for", "fiction",
            ])


# ------------------- Manifest validates clean --------------------


def test_emitted_manifest_passes_validator(tmp_path):
    """End-to-end: emitted manifest validates clean once an
    identity_baseline entry naming the impostor target persona is
    appended (matches the acquire_blog and pdf_extract tests)."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest = output_dir / "draft.jsonl"
    args = make_args(
        magazine="nightmare",
        filter_author=["Synthetic Author A"],
        output_dir=str(output_dir),
        emit_manifest=str(manifest),
    )
    am.run(args, fetcher=make_fetcher(NIGHTMARE_URLS))

    # Add an identity_baseline entry naming the "fiction" persona so
    # the cross-check has a target.
    persona_dir = next(p for p in output_dir.iterdir() if p.is_dir())
    baseline = persona_dir / "fake_baseline.txt"
    baseline.write_text("Identity baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline",
        "path": str(baseline.relative_to(manifest.parent)),
        "author": "Test User",
        "persona": "fiction",
        "register": "literary_horror",
        "ai_status": "pre_ai_human",
        "language_status": "native",
        "use": ["baseline", "voice_profile"],
        "split": "baseline",
        "privacy": "private",
        "corpus_role": "identity_baseline",
        "era": "pre_chatgpt",
    }
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest)
    error_issues = [
        i for i in report["issues"] if i.get("severity") == "error"
    ]
    assert error_issues == [], \
        f"manifest should validate without errors: {error_issues}"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
