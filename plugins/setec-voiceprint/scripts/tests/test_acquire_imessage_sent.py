#!/usr/bin/env python3
"""Tests for acquire_imessage_sent.py against a synthetic chat.db fixture.

Per internal/2026-07-09-acquire-imessage-sent-spec.md Tests section:
bundling, every exclusion class, the attributedBody-only reply
fail-closed drop, the received-message exclusion, the load-bearing
metadata-privacy grep, the seconds-vs-nanoseconds epoch pair, the
schema pre-flight (fixed-set hard-fail vs reply-column degrade),
ai_status-by-era, dedup, and the use/ai_status manifest kwargs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_imessage_sent as A  # type: ignore
from test_data.acquisition_imessage_fixture import build_fixture as bf  # type: ignore

RAW_HANDLES = [
    bf.RAW_HANDLE_1, bf.RAW_HANDLE_2, bf.RAW_HANDLE_GROUP,
    bf.RAW_HANDLE_ABODY, bf.RAW_HANDLE_EPOCH, bf.RAW_HANDLE_POST2024,
]
FORBIDDEN_IN_TXT = [
    bf.RECEIVED_SENTINEL, bf.TAPBACK_SENTINEL,
    bf.GROUP_ACTION_SENTINEL, bf.ABODY_REPLY_QUOTE,
]


@pytest.fixture
def db_path(tmp_path) -> Path:
    return bf.build_fixture(tmp_path / "chat_fixture.db")


def _run(db: Path, out: Path, extra=None) -> int:
    argv = [
        "--db-path", str(db),
        "--output-dir", str(out),
        "--persona", "joshua",
        "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--until", "2100-01-01",
    ]
    if extra:
        argv += extra
    return A.main(argv)


def _private_out(tmp_path) -> Path:
    return tmp_path / "ai-prose-baselines-private" / "identity" / "personal" / "joshua"


def _manifest_entries(out: Path) -> list[dict]:
    mf = out / "draft_manifest.jsonl"
    return [json.loads(l) for l in mf.read_text().splitlines() if l.strip()]


def _txt_blob(out: Path) -> str:
    return "\n".join(p.read_text() for p in out.glob("*.txt"))


def test_end_to_end_acquires_and_excludes(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    entries = _manifest_entries(out)
    assert entries, "expected at least one acquired piece"
    txt = _txt_blob(out)

    # Non-reply attributedBody body was decoded and kept.
    assert bf.ABODY_NONREPLY_TEXT in txt
    # Every exclusion sentinel is absent from all bodies.
    for sentinel in FORBIDDEN_IN_TXT:
        assert sentinel not in txt, f"{sentinel!r} leaked into output"


def test_metadata_privacy_grep_no_raw_handles(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    # Grep every metadata surface (titles, notes, .meta.json, manifest
    # id/path/fields) for the fixture's known raw handles.
    surfaces = "\n".join(p.read_text() for p in out.glob("*.meta.json"))
    surfaces += (out / "draft_manifest.jsonl").read_text()
    for handle in RAW_HANDLES:
        assert handle not in surfaces, (
            f"raw handle {handle!r} leaked into a metadata surface"
        )
    # Titles/ids use redacted contact_NN labels (slugified to contact-NN).
    for entry in _manifest_entries(out):
        assert "contact-" in entry["id"], entry["id"]


def test_manifest_fields_and_kwargs(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    for entry in _manifest_entries(out):
        assert entry["corpus_role"] == "identity_baseline"
        assert entry["use"] == ["voice_profile"], (
            "use must be voice_profile (a direct kwarg), never the "
            "compose_manifest_entry default voice_impostor"
        )
        assert entry["consent_status"] == "author_consent"
        assert entry["register"] == "personal"
        assert entry["source"] == "imessage_local"
        assert entry["acquired_via"].startswith("acquire_imessage_sent_")
        assert entry["era"] in {
            "pre_chatgpt", "pre_ai_widespread", "post_ai_widespread", "undated",
        }
        assert entry["ai_status"] in {"pre_ai_human", "unknown"}


def test_ai_status_derived_from_era_not_hardcoded(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    by_era = {}
    for e in _manifest_entries(out):
        by_era.setdefault(e["era"], set()).add(e["ai_status"])
    # A post-2024 piece exists and is tagged unknown, not pre_ai_human.
    assert by_era.get("post_ai_widespread") == {"unknown"}, by_era
    # Pre-2024 pieces are pre_ai_human.
    assert by_era.get("pre_chatgpt") == {"pre_ai_human"}, by_era


def test_group_vs_direct_notes(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    notes = {e.get("notes") for e in _manifest_entries(out)}
    assert "group_chat" in notes and "direct" in notes, notes


def test_epoch_pair_bundles_to_one_day(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    # The epoch-pair chat (contact for RAW_HANDLE_EPOCH) has one ns and
    # one seconds message on the same real date -> exactly one piece,
    # both lines present.
    for meta in out.glob("*.meta.json"):
        data = json.loads(meta.read_text())
        if "Epoch message alpha" in (out / (meta.name[:-len(".meta.json")] + ".txt")).read_text():
            body = (out / (meta.name[:-len(".meta.json")] + ".txt")).read_text()
            assert "Epoch message beta" in body
            assert data["date_written"] == "2020-06-15"
            return
    pytest.fail("epoch-pair piece not found")


def test_dedup_identical_bundles(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    txt = _txt_blob(out)
    # The dedup-partner text appears in exactly one written .txt.
    assert txt.count("This exact sentence is byte identical") == 1


def test_min_words_drop_is_a_separate_gate(tmp_path):
    # Unit-level: a short bundle drops under a high floor.
    opts = A.Options(
        db_path=Path("/x"), persona="j", author="j", register="personal",
        since=None, until=None, min_words=150, max_messages=1000,
        output_dir=Path("/x"), manifest_path=Path("/x/m.jsonl"),
        contact_map_path=Path("/x/c.json"), include_group_chats=True,
        max_items=10, dry_run=False, live_smoke_confirmed=False,
    )
    summary = A.Summary()
    bundle = A.DayBundle(label="contact_01", date=None, is_group=False,
                         lines=["too short"])
    assert A.process_bundle(bundle, opts, summary) is None
    assert summary.skipped_below_min_words == 1


def test_schema_preflight_fixed_set_missing_hard_fails(tmp_path):
    db = tmp_path / "broken.db"
    conn = sqlite3.connect(db)
    # message table missing item_type (a fixed-set column).
    conn.executescript(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, "
        "attributedBody BLOB, is_from_me INTEGER, date INTEGER, "
        "associated_message_type INTEGER);"
    )
    conn.commit(); conn.close()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(SystemExit):
        A.schema_preflight(conn)
    conn.close()


def test_schema_preflight_reply_column_absent_degrades(tmp_path):
    db = tmp_path / "noreply.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, "
        "attributedBody BLOB, is_from_me INTEGER, date INTEGER, "
        "associated_message_type INTEGER, item_type INTEGER);"
    )
    conn.commit(); conn.close()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    schema = A.schema_preflight(conn)  # must NOT raise
    assert schema.reply_column is None
    conn.close()


def test_received_and_reply_and_actions_never_read(tmp_path, db_path):
    out = _private_out(tmp_path)
    assert _run(db_path, out) == 0
    # Full-tree grep (bodies + metadata) for the leak sentinels.
    whole = _txt_blob(out)
    whole += "\n".join(p.read_text() for p in out.glob("*.meta.json"))
    whole += (out / "draft_manifest.jsonl").read_text()
    for sentinel in FORBIDDEN_IN_TXT:
        assert sentinel not in whole


def test_unwindowed_full_history_refuses_without_receipt(tmp_path, db_path):
    out = _private_out(tmp_path)
    # No --since -> unwindowed -> must refuse (no live-smoke receipt).
    with pytest.raises(SystemExit):
        A.main([
            "--db-path", str(db_path), "--output-dir", str(out),
            "--persona", "joshua", "--min-words-per-piece", "5",
        ])


def test_live_smoke_confirmed_requires_tty(tmp_path, db_path):
    out = _private_out(tmp_path)
    # Non-TTY stdin (pytest) + --live-smoke-confirmed -> hard error (2).
    rc = A.main([
        "--db-path", str(db_path), "--output-dir", str(out),
        "--persona", "joshua", "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--live-smoke-confirmed",
    ])
    assert rc == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
