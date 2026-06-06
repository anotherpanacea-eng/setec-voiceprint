#!/usr/bin/env python3
"""Tests for acquisition_core helpers (stdlib only — no acquisition network deps,
so these run in core CI where bs4/requests are absent)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquisition_core as ac  # type: ignore  # noqa: E402


def _piece(text: str, title: str = "A Long Shared Book Title",
           date: dt.date | None = dt.date(2020, 1, 1)) -> "ac.AcquiredPiece":
    return ac.AcquiredPiece(
        title=title, author="Author", persona="p", register="literary_fiction",
        date_written=date, source_url="u", cleaned_text=text,
        raw_byte_length=len(text.encode()), preprocessing_meta={},
        acquired_via="test", consent_status="fair_use_research", era="undated",
        register_match="high", topic_match="medium", impostor_for=[], notes="",
    )


def test_write_piece_disambiguates_stem_collision(tmp_path):
    # Two different-content pieces with the same title+date → same base stem.
    p1, p2 = _piece("alpha " * 200), _piece("beta " * 200)
    t1, _ = ac.write_piece(p1, output_dir=tmp_path, scraper_version="t")
    t2, _ = ac.write_piece(p2, output_dir=tmp_path, scraper_version="t")
    assert t1 != t2
    assert len(list(tmp_path.glob("*.txt"))) == 2
    assert t1.read_text().startswith("alpha")
    assert t2.read_text().startswith("beta")  # the first file is NOT clobbered


def test_disambiguated_stem_is_filesystem_safe(tmp_path):
    p1, p2 = _piece("x " * 200), _piece("y " * 200)
    ac.write_piece(p1, output_dir=tmp_path, scraper_version="t")
    t2, _ = ac.write_piece(p2, output_dir=tmp_path, scraper_version="t")
    assert ":" not in t2.name  # Windows-safe; no 'sha256:' colon
    assert t2.stem.endswith(p2.content_hash.split(":")[-1][:8])


def test_no_collision_keeps_base_stem(tmp_path):
    p = _piece("z " * 200)
    t, _ = ac.write_piece(p, output_dir=tmp_path, scraper_version="t")
    assert t.stem == p.filename_stem()  # unchanged when there is no clash
