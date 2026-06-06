#!/usr/bin/env python3
"""Validation + manifest-shaping tests for acquire_manuscript.

bs4-free (imports only acquire_manuscript + acquisition_core), so these run in
core CI where the acquisition extras are absent — unlike the EPUB-fixture tests
in test_acquire_manuscript.py, which skip without bs4."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_manuscript as am  # type: ignore  # noqa: E402
import acquisition_core as ac  # type: ignore  # noqa: E402


def test_impostor_role_requires_impostor_for():
    # --corpus-role impostor without --impostor-for must error out (exit 2),
    # before any filesystem work.
    with pytest.raises(SystemExit) as ei:
        am.main(["src.txt", "--persona", "p", "--register", "r",
                 "--corpus-role", "impostor"])
    assert ei.value.code == 2


def test_identity_role_does_not_require_impostor_for():
    # The guard must NOT fire for the default identity_baseline role: parsing
    # succeeds (the run later fails on the missing source, but not on the guard).
    args = am.build_arg_parser().parse_args(
        ["src.txt", "--persona", "p", "--register", "r"])
    assert args.corpus_role == "identity_baseline"
    assert args.impostor_for == []


def test_era_preserved_for_identity_baseline(tmp_path):
    opts = am.ProcessOptions(
        persona="me", author="Me", register="literary_horror",
        corpus_role="identity_baseline", use=["voice_profile"],
        ai_status="pre_ai_human", consent_status="author_consent",
        era="pre_chatgpt", impostor_for=[], register_match="high",
        topic_match="medium", output_dir=tmp_path,
        manifest_path=tmp_path / "m.jsonl", max_items=10, dry_run=False,
        allow_non_prose=False, strip_rules=None, strip_aggressive=False,
        acquired_via="test", segment="work", window_words=2500, min_words=10,
    )
    piece = ac.AcquiredPiece(
        title="Ch1", author="Me", persona="me", register="literary_horror",
        date_written=dt.date(2019, 1, 1), source_url="loc",
        cleaned_text="word " * 200, raw_byte_length=1000, preprocessing_meta={},
        acquired_via="test", consent_status="author_consent", era="pre_chatgpt",
        register_match="high", topic_match="medium", impostor_for=[], notes="",
    )
    item = am.ItemMeta(locator="loc", title="Ch1", author="Me",
                       date=dt.date(2019, 1, 1), extra={})
    am.emit_piece(piece, item, options=opts, summary=ac.RunSummary())
    entry = json.loads((tmp_path / "m.jsonl").read_text().strip().splitlines()[-1])
    assert entry["corpus_role"] == "identity_baseline"
    assert entry["era"] == "pre_chatgpt"  # not dropped for identity entries


def test_since_until_flags_removed():
    # The inert --since/--until flags were dropped; argparse must now reject them.
    with pytest.raises(SystemExit):
        am.build_arg_parser().parse_args(
            ["src.txt", "--persona", "p", "--register", "r", "--since", "2020"])
