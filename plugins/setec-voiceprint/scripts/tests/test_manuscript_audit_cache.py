#!/usr/bin/env python3
"""Tests for the chapter-level audit cache + resume in
manuscript_audit (PR feat/manuscript-audit-chapter-resume,
1.70.0).

Applies the MEASURE + SAVE PROGRESS principles to a script that
previously had neither. Each ``audit_text`` call is expensive
(spaCy + signal computation); on a 50-chapter manuscript with
tier3 on, one chapter can take minutes. A crash on chapter 40
of 50 used to discard the first 39 chapters' work.

Pins:

  * MEASURE — per-chapter completion log to stderr (audited
    chapter N/M with word count + elapsed time).
  * SAVE PROGRESS — when ``--chapter-audit-cache`` is set, each
    chapter's audit is written atomically to the cache after it
    completes with ``status: "in_progress"``; final write flips
    to ``"complete"``.
  * RESUME — chapters whose label matches a cached audit are
    loaded from cache and skipped.
  * BACK-COMPAT — no cache flag => original behavior plus the
    stderr progress log.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manuscript_audit as ma  # type: ignore  # noqa: E402


# --------------- Helpers ----------


def _stub_audit_text(text, **kwargs):
    """Cheap stand-in for ``audit_text`` so tests don't need spaCy
    or sentence-transformers. Returns a record shape the audit_
    manuscript pipeline + classify_compression can consume."""
    word_count = len(text.split())
    return {
        "summary": {"n_words": word_count},
        "preprocessing": {
            "rules_active": [], "applied": True, "opt_out": False,
        },
        "tier1": {
            "sentence_length": {"mean": 15.0, "sd": 5.0, "burstiness_B": -0.2},
            "mattr": {"value": 0.7},
            "mtld": 80.0,
            "yules_k": 100.0,
            "shannon_entropy_bits": 9.0,
            "fkgl": {"mean": 10.0, "sd": 3.0},
            "connective_density": {"per_1000_tokens": 10.0},
        },
    }


def _stub_classify_compression(audit):
    """Cheap stand-in for ``classify_compression``."""
    return {
        "band": "uncompressed",
        "compression_fraction": 0.1,
        "weighted_score": 0.0,
        "available_weight": 1.0,
    }


def _chapters(n: int) -> list[dict]:
    return [
        {"label": f"Chapter {i + 1}", "text": "word " * 100}
        for i in range(n)
    ]


# --------------- CLI surface ----------


def test_chapter_audit_cache_flags_exist():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ma.main()
        except SystemExit:
            pass
    # The CLI errors when no manuscript / chapter-dir given;
    # --help is the more reliable way to inspect the flag list.
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        import sys as _sys
        argv_orig = _sys.argv[:]
        _sys.argv = ["manuscript_audit.py", "--help"]
        try:
            ma.main()
        except SystemExit:
            pass
        finally:
            _sys.argv = argv_orig
    help_text = buf2.getvalue()
    assert "--chapter-audit-cache" in help_text
    assert "--refresh-chapter-cache" in help_text


# --------------- SAVE PROGRESS + RESUME ----------


def test_partial_cache_written_per_chapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With cache_path set, each chapter completion triggers an
    atomic partial-cache write."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    save_calls: list[str] = []
    real_save = ma._save_chapter_audit_cache

    def _spy(path, audits, prep, **kw):
        save_calls.append(kw.get("status", "?"))
        return real_save(path, audits, prep, **kw)

    monkeypatch.setattr(ma, "_save_chapter_audit_cache", _spy)
    cache = tmp_path / "chapters.json"
    chapters = _chapters(3)
    ma.audit_manuscript(
        chapters, baseline_dir=None,
        do_tier2=False, do_tier3=False,
        cache_path=cache,
    )
    in_progress = sum(1 for s in save_calls if s == "in_progress")
    complete = sum(1 for s in save_calls if s == "complete")
    assert in_progress == 3, save_calls  # one per chapter
    assert complete == 1, save_calls


def test_resume_skips_chapters_in_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A pre-populated cache with two chapter audits means the
    next run only re-audits the third."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "in_progress",
        "scoring_meta": {"do_tier2": False, "do_tier3": False},
        "chapter_audits": [
            {
                "label": "Chapter 1",
                "n_words": 100,
                "audit": {"summary": {"n_words": 100}},
                "compression": {"band": "cached"},
            },
            {
                "label": "Chapter 2",
                "n_words": 100,
                "audit": {"summary": {"n_words": 100}},
                "compression": {"band": "cached"},
            },
        ],
        "chapter_preprocessing": {
            "Chapter 1": {"applied": True},
            "Chapter 2": {"applied": True},
        },
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    result = ma.audit_manuscript(
        _chapters(3), baseline_dir=None,
        do_tier2=False, do_tier3=False,
        cache_path=cache,
    )
    assert audit_count["n"] == 1, (
        f"expected 1 fresh audit (2 resumed); got {audit_count['n']}"
    )
    # All three chapters appear in the result; the first two are
    # the cached entries (marked "cached" band).
    bands = [c["compression"]["band"] for c in result["chapters"]]
    assert bands[0] == "cached"
    assert bands[1] == "cached"
    assert bands[2] == "uncompressed"  # freshly audited


def test_refresh_cache_discards_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``refresh_cache=True`` ignores any existing cache."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {"do_tier2": False, "do_tier3": False},
        "chapter_audits": [
            {
                "label": "Chapter 1",
                "n_words": 100,
                "audit": {"summary": {"n_words": 100}},
                "compression": {"band": "cached"},
            },
        ],
        "chapter_preprocessing": {},
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        _chapters(2), baseline_dir=None,
        do_tier2=False, do_tier3=False,
        cache_path=cache,
        refresh_cache=True,
    )
    assert audit_count["n"] == 2, (
        f"expected 2 fresh audits (refresh ignores cache); got "
        f"{audit_count['n']}"
    )


def test_incompatible_cache_is_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A cache built with different tier flags is discarded."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "do_tier2": True,  # mismatch
            "do_tier3": False,
        },
        "chapter_audits": [{
            "label": "Chapter 1", "n_words": 100,
            "audit": {"summary": {"n_words": 100}},
            "compression": {"band": "cached"},
        }],
        "chapter_preprocessing": {},
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        _chapters(2), baseline_dir=None,
        do_tier2=False, do_tier3=False,  # tier2 differs from cache
        cache_path=cache,
    )
    assert audit_count["n"] == 2


# --------------- BACK-COMPAT ----------


def test_no_cache_path_back_compat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Without cache_path, audit_manuscript behaves exactly like
    the pre-1.70.0 version: every chapter is audited, no cache
    written, no resume logic."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    result = ma.audit_manuscript(
        _chapters(3), baseline_dir=None,
        do_tier2=False, do_tier3=False,
    )
    assert audit_count["n"] == 3
    assert result["n_chapters"] == 3


# --------------- MEASURE ----------


def test_per_chapter_progress_log_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """Each chapter completion logs to stderr (not stdout) so
    --json output stays parseable."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    ma.audit_manuscript(
        _chapters(3), baseline_dir=None,
        do_tier2=False, do_tier3=False,
    )
    captured = capsys.readouterr()
    # Three per-chapter progress lines on stderr; none on stdout.
    err_lines = [
        ln for ln in captured.err.splitlines()
        if "chapter" in ln.lower()
    ]
    assert len(err_lines) >= 3, (
        f"expected at least 3 chapter progress lines on stderr; "
        f"got {err_lines}"
    )
    assert "chapter" not in captured.out.lower(), (
        f"stdout polluted: {captured.out!r}"
    )


# --------------- Codex P2 on PR #70: text-hash + preprocessing compat ----


def test_edited_chapter_text_invalidates_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: editing a chapter's text under the same label
    must invalidate the cached audit for that chapter. Pre-fix,
    the cache was keyed by label only → edit served the stale
    audit silently."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    chapters = _chapters(3)
    ma.audit_manuscript(
        chapters, baseline_dir=None,
        do_tier2=False, do_tier3=False,
        cache_path=cache,
    )
    # Edit Chapter 2's text. Same label, different content.
    chapters[1] = {
        "label": "Chapter 2",
        "text": "completely revised text " * 100,
    }
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        chapters, baseline_dir=None,
        do_tier2=False, do_tier3=False,
        cache_path=cache,
    )
    # Exactly one chapter (Chapter 2) was re-audited because its
    # text_hash changed. The other two carried forward.
    assert audit_count["n"] == 1, (
        f"expected exactly 1 fresh audit for the edited chapter; "
        f"got {audit_count['n']}"
    )


def test_cache_refused_when_allow_non_prose_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: allow_non_prose changes audit_text's output;
    a cache produced under one value must be refused under
    another."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "do_tier2": False,
            "do_tier3": False,
            "allow_non_prose": True,
        },
        "chapter_audits": [{
            "label": "Chapter 1", "n_words": 100,
            "audit": {"summary": {"n_words": 100}},
            "compression": {"band": "cached"},
        }],
        "chapter_preprocessing": {},
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        _chapters(2), baseline_dir=None,
        do_tier2=False, do_tier3=False,
        allow_non_prose=False,
        cache_path=cache,
    )
    assert audit_count["n"] == 2


def test_cache_refused_when_strip_rules_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: strip_rules changes preprocessing → different
    audit output. Mismatch refuses the cache."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "do_tier2": False,
            "do_tier3": False,
            "strip_rules": "css_rule_block",
        },
        "chapter_audits": [{
            "label": "Chapter 1", "n_words": 100,
            "audit": {"summary": {"n_words": 100}},
            "compression": {"band": "cached"},
        }],
        "chapter_preprocessing": {},
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        _chapters(2), baseline_dir=None,
        do_tier2=False, do_tier3=False,
        strip_rules=None,
        cache_path=cache,
    )
    assert audit_count["n"] == 2


def test_cache_refused_when_strip_aggressive_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2: strip_aggressive changes preprocessing."""
    monkeypatch.setattr(ma, "audit_text", _stub_audit_text)
    monkeypatch.setattr(
        ma, "classify_compression", _stub_classify_compression,
    )
    cache = tmp_path / "chapters.json"
    cache.write_text(json.dumps({
        "status": "complete",
        "scoring_meta": {
            "do_tier2": False,
            "do_tier3": False,
            "strip_aggressive": True,
        },
        "chapter_audits": [{
            "label": "Chapter 1", "n_words": 100,
            "audit": {"summary": {"n_words": 100}},
            "compression": {"band": "cached"},
        }],
        "chapter_preprocessing": {},
    }))
    audit_count = {"n": 0}

    def _counting_audit(text, **kw):
        audit_count["n"] += 1
        return _stub_audit_text(text, **kw)

    monkeypatch.setattr(ma, "audit_text", _counting_audit)
    ma.audit_manuscript(
        _chapters(2), baseline_dir=None,
        do_tier2=False, do_tier3=False,
        strip_aggressive=False,
        cache_path=cache,
    )
    assert audit_count["n"] == 2
