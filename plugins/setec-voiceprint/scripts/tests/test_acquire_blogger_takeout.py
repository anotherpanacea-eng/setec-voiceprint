#!/usr/bin/env python3
"""Regression tests for acquire_blogger_takeout.py."""

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
    import acquire_blogger_takeout as bt  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


FIXTURE_DIR = ROOT / "test_data" / "blogger_takeout_fixture"
BLOG_FEED = FIXTURE_DIR / "Blogger" / "Blogs" / "Test Blog" / "feed.atom"
COMMENT_FEED = (
    FIXTURE_DIR / "Blogger" / "Comments" / "Test Blog" / "feed.atom"
)


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        takeout_path=str(FIXTURE_DIR),
        persona="test_blog_persona",
        author="Test Blog Author",
        impostor_for=["blog"],
        register="blog_essay",
        register_match="high",
        topic_match="medium",
        consent_status="author_consent",
        era="pre_chatgpt",
        since=None,
        until="2022-11-01",
        min_words=20,
        max_posts=0,
        output_dir=None,
        emit_manifest=None,
        out=None,
        dry_run=False,
        include_comments=False,
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


def test_discover_blog_feeds_excludes_comments_by_default():
    feeds = bt.discover_blog_feeds(FIXTURE_DIR)
    assert feeds == [BLOG_FEED]


def test_direct_comment_feed_requires_include_comments():
    try:
        bt.discover_blog_feeds(COMMENT_FEED)
        assert False, "comment feed should require --include-comments"
    except ValueError as e:
        assert "--include-comments" in str(e)
    assert bt.discover_blog_feeds(COMMENT_FEED, include_comments=True) == [
        COMMENT_FEED
    ]


def test_parse_blogger_feed_preserves_titleless_entries():
    title, entries = bt.parse_blogger_feed(BLOG_FEED)
    assert title == "Test Blog"
    assert len(entries) == 4
    titleless = [e for e in entries if not e.title]
    assert len(titleless) == 1
    assert titleless[0].short_id.endswith("2222222222222222")
    assert titleless[0].published is not None
    assert titleless[0].published.isoformat() == "2019-03-04"


def test_locator_only_entry_detection():
    assert bt._looks_like_locator_only("https://example.com/only-a-link")
    assert not bt._looks_like_locator_only("<p>https://example.com in prose</p>")


def test_blogger_takeout_end_to_end(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "blog_essay" / "test_blog_persona"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
    )
    rc = bt.run(args)
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    meta_files = sorted(output_dir.glob("*.meta.json"))
    assert len(txt_files) == 2
    assert len(meta_files) == 2
    names = [p.name for p in txt_files]
    assert any("the-long-essay" in name for name in names)
    assert any("untitled-2222222222222222" in name for name in names)

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    for entry in entries:
        assert entry["corpus_role"] == "impostor"
        assert entry["use"] == ["voice_impostor"]
        assert entry["consent_status"] == "author_consent"
        assert entry["era"] == "pre_chatgpt"
        assert entry["impostor_for"] == ["blog"]
        assert entry["acquired_via"].startswith("acquire_blogger_takeout_")
        assert entry["content_hash"].startswith("sha256:")

    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert "blogger_takeout" in meta
    assert "entry_id" in meta["blogger_takeout"]

    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    assert errors == []


def test_argparse_requires_impostor_for():
    parser = bt.build_arg_parser()
    try:
        parser.parse_args([
            str(FIXTURE_DIR),
            "--persona", "test_blog_persona",
            "--author", "Test Blog Author",
            "--register", "blog_essay",
            "--consent-status", "author_consent",
        ])
        assert False, "argparse should reject missing --impostor-for"
    except SystemExit:
        pass
