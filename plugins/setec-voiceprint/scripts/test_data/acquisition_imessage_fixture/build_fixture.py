#!/usr/bin/env python3
"""Generate a tiny synthetic chat.db-shaped SQLite fixture for
acquire_imessage_sent.py's tests. Run standalone to (re)create
chat_fixture.db; tests import build_fixture() and regenerate on demand.

Every row here exercises a case from the acquirer spec's Tests section.
The Cocoa/Apple-epoch date values are computed so that the acquirer's
own apple_date_to_local_date() decodes them to the intended local dates
(local noon is used so a timezone offset can't shift the calendar day).
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from pathlib import Path

COCOA_EPOCH_OFFSET = 978_307_200

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "chat_fixture.db"

# Distinctive strings the tests grep for.
RAW_HANDLE_1 = "+15551230001"
RAW_HANDLE_2 = "+15551230002"
RAW_HANDLE_GROUP = "chat999group"
RAW_HANDLE_ABODY = "+15551230003"
RAW_HANDLE_EPOCH = "+15551230004"
RAW_HANDLE_POST2024 = "+15551230005"
RECEIVED_SENTINEL = "RECEIVED_FROM_OTHER_PERSON_SHOULD_NEVER_APPEAR"
TAPBACK_SENTINEL = "Liked a message TAPBACK_SHOULD_NEVER_APPEAR"
GROUP_ACTION_SENTINEL = "named the conversation OTHERNAME_SHOULD_NEVER_APPEAR"
ABODY_REPLY_QUOTE = "QUOTED_PARENT_TEXT_SHOULD_NEVER_APPEAR"
ABODY_NONREPLY_TEXT = "This is a substantive standalone message decoded from attributedBody with plenty of words to clear any floor."


def _cocoa_ns(date: _dt.date) -> int:
    unix_ts = _dt.datetime(date.year, date.month, date.day, 12, 0, 0).timestamp()
    return int((unix_ts - COCOA_EPOCH_OFFSET) * 1_000_000_000)


def _cocoa_seconds(date: _dt.date) -> int:
    unix_ts = _dt.datetime(date.year, date.month, date.day, 12, 0, 0).timestamp()
    return int(unix_ts - COCOA_EPOCH_OFFSET)


def _attributed_body(text: str) -> bytes:
    """Encode ``text`` in the minimal streamtyped shape the acquirer's
    decode_attributed_body() reads: NSString + 5 header bytes + length
    + utf-8 bytes."""
    payload = text.encode("utf-8")
    assert len(payload) < 128, "fixture strings stay single-byte-length"
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
        + b"NSString"
        + b"\x01\x94\x84\x01\x2b"
        + bytes([len(payload)])
        + payload
    )


def build_fixture(db_path: Path = DB_PATH) -> Path:
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT, chat_identifier TEXT, service_name TEXT,
            room_name TEXT, display_name TEXT, style INTEGER
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT, text TEXT, attributedBody BLOB,
            is_from_me INTEGER, date INTEGER,
            associated_message_type INTEGER, item_type INTEGER,
            thread_originator_guid TEXT, handle_id INTEGER
        );
        CREATE TABLE chat_message_join (
            chat_id INTEGER, message_id INTEGER
        );
        """
    )

    chats = [
        (1, RAW_HANDLE_1, None, 45),          # 1:1
        (2, RAW_HANDLE_2, None, 45),          # 1:1 (dedup partner)
        (3, RAW_HANDLE_GROUP, "grp!room", 43),  # group
        (4, RAW_HANDLE_ABODY, None, 45),      # 1:1 (attributedBody)
        (5, RAW_HANDLE_EPOCH, None, 45),      # 1:1 (epoch pair)
        (6, RAW_HANDLE_POST2024, None, 45),   # 1:1 (post-2024)
    ]
    for rid, ident, room, style in chats:
        cur.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, room_name, style)"
            " VALUES (?,?,?,?,?)",
            (rid, f"guid{rid}", ident, room, style),
        )

    day1 = _dt.date(2020, 6, 15)
    day2 = _dt.date(2020, 6, 16)
    post = _dt.date(2024, 9, 1)

    # (rowid, chat_id, text, attributedBody, is_from_me, date_raw,
    #  assoc_type, item_type, reply_guid)
    long1 = "Hey there this is the first outgoing message of the day with enough words to clear a low floor."
    long2 = "And here is a second outgoing message on the same day so the two bundle into one conversation-day piece."
    dup_text = "This exact sentence is byte identical across two different conversation days for the dedup test only here."
    rows = [
        # A: 1:1, day1, two same-day messages -> one bundle
        (10, 1, long1, None, 1, _cocoa_ns(day1), 0, 0, None),
        (11, 1, long2, None, 1, _cocoa_ns(day1), 0, 0, None),
        # B: same chat, day2 -> separate piece
        (12, 1, long1 + " Second day.", None, 1, _cocoa_ns(day2), 0, 0, None),
        # C: group chat, day1 -> notes group_chat
        (13, 3, "A group message from me with several words to clear the floor easily here now.", None, 1, _cocoa_ns(day1), 0, 0, None),
        # D: tapback -> excluded
        (14, 1, TAPBACK_SENTINEL, None, 1, _cocoa_ns(day1), 2000, 0, None),
        # E: attachment-only (object-replacement text) -> excluded
        (15, 1, "￼", None, 1, _cocoa_ns(day1), 0, 0, None),
        # F: attributedBody-only REPLY -> fail-closed drop
        (16, 4, None, _attributed_body(ABODY_REPLY_QUOTE), 1, _cocoa_ns(day1), 0, 0, "parent-guid-xyz"),
        # G: received (is_from_me=0) -> never read
        (17, 1, RECEIVED_SENTINEL, None, 0, _cocoa_ns(day1), 0, 0, None),
        # H: group-action row (item_type != 0) naming someone -> excluded
        (18, 3, GROUP_ACTION_SENTINEL, None, 1, _cocoa_ns(day1), 0, 1, None),
        # I: epoch pair — same real date, one ns one seconds -> same day
        (19, 5, "Epoch message alpha with sufficient words to clear the small floor for this bundle here.", None, 1, _cocoa_ns(day1), 0, 0, None),
        (20, 5, "Epoch message beta stored in seconds units for the same calendar day as alpha here now.", None, 1, _cocoa_seconds(day1), 0, 0, None),
        # K: attributedBody-only NON-reply -> decoded text kept
        (21, 4, None, _attributed_body(ABODY_NONREPLY_TEXT), 1, _cocoa_ns(day2), 0, 0, None),
        # L: post-2024 message -> ai_status unknown / era post_ai_widespread
        (22, 6, "A message sent well after mid 2024 to exercise the unknown ai status derivation path here now.", None, 1, _cocoa_ns(post), 0, 0, None),
        # dedup partner: identical bundled text, different chat+day
        (23, 2, dup_text, None, 1, _cocoa_ns(day1), 0, 0, None),
        (24, 2, dup_text, None, 1, _cocoa_ns(day2), 0, 0, None),
    ]
    for (rid, chat_id, text, abody, isme, date_raw, at, it, rg) in rows:
        cur.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, "
            "is_from_me, date, associated_message_type, item_type, "
            "thread_originator_guid) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, f"m{rid}", text, abody, isme, date_raw, at, it, rg),
        )
        cur.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?,?)",
            (chat_id, rid),
        )
    conn.commit()
    conn.close()
    return db_path


if __name__ == "__main__":
    p = build_fixture()
    print(f"wrote {p}")
