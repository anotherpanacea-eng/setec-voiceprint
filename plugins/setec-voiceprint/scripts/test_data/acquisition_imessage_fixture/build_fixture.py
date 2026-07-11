#!/usr/bin/env python3
"""Build the checked-in synthetic Messages ``chat.db`` fixture.

All prose and identifiers are invented.  Re-run this file after changing rows,
then commit both this generator and ``chat_fixture.db``.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

COCOA_EPOCH_OFFSET = 978_307_200
HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "chat_fixture.db"

RAW_HANDLE_DIRECT = "+15551230001"
RAW_HANDLE_DUPLICATE = "synthetic-person@example.invalid"
RAW_HANDLE_GROUP = "chat.synthetic.group-guid"
RAW_HANDLE_ATTRIBUTED = "+15551230003"
RAW_HANDLE_EPOCH = "+15551230004"
RAW_HANDLE_POST2024 = "+15551230005"
RAW_HANDLE_SHORT = "+15551230006"
RAW_HANDLE_UNNAMED_GROUP = "chat.synthetic.unnamed-group-guid"

RAW_HANDLES = (
    RAW_HANDLE_DIRECT,
    RAW_HANDLE_DUPLICATE,
    RAW_HANDLE_GROUP,
    RAW_HANDLE_ATTRIBUTED,
    RAW_HANDLE_EPOCH,
    RAW_HANDLE_POST2024,
    RAW_HANDLE_SHORT,
    RAW_HANDLE_UNNAMED_GROUP,
)

RECEIVED_SENTINEL = "RECEIVED_PARENT_WORDS_MUST_NOT_APPEAR"
TAPBACK_SENTINEL = "TAPBACK_ROW_MUST_NOT_APPEAR"
GROUP_ACTION_SENTINEL = "GROUP_ACTION_ROW_MUST_NOT_APPEAR"
ATTRIBUTED_REPLY_QUOTE = "A third party said this arbitrary forbidden sentence."
ATTRIBUTED_REPLY_OWN = "My attributed reply also drops in v1."
ATTRIBUTED_NONREPLY_TEXT = (
    "This standalone attributed body contains ordinary synthetic prose and "
    "must survive conservative byte scan extraction."
)
TEXT_REPLY_OWN = (
    "This is my own text column reply and it stays exactly as I sent it."
)
IN_BODY_PHONE = "+15559998888"
AUTOMATED_SENTINEL = "Missed call"
SHORT_SENTINEL = "tiny bundle"
DUPLICATE_TEXT = (
    "This exact synthetic sentence is byte identical across separate "
    "conversation days for the content hash deduplication test."
)


def cocoa_seconds(day: dt.date, hour: int = 12) -> int:
    unix = dt.datetime(
        day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc
    ).timestamp()
    return int(unix - COCOA_EPOCH_OFFSET)


def cocoa_nanoseconds(day: dt.date, hour: int = 12) -> int:
    return cocoa_seconds(day, hour) * 1_000_000_000


def attributed_body(text: str) -> bytes:
    """A minimal synthetic streamtyped-like NSString byte run.

    This is not claimed to reproduce a complete archive.  It exercises v1's
    explicitly non-structural marker/length/string byte scanner.
    """
    payload = text.encode("utf-8")
    if len(payload) >= 128:
        raise ValueError("fixture payload must fit the one-byte length form")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
        b"NSString\x01\x94\x84\x01+"
        + bytes([len(payload)])
        + payload
    )


def create_schema(conn: sqlite3.Connection, *, include_reply_column: bool = True) -> None:
    reply = ", thread_originator_guid TEXT" if include_reply_column else ""
    conn.executescript(
        f"""
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            chat_identifier TEXT,
            room_name TEXT,
            style INTEGER
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY,
            guid TEXT,
            text TEXT,
            attributedBody BLOB,
            is_from_me INTEGER,
            date INTEGER,
            associated_message_type INTEGER,
            item_type INTEGER
            {reply}
        );
        CREATE TABLE chat_message_join (
            chat_id INTEGER,
            message_id INTEGER
        );
        """
    )


def insert_chat(
    conn: sqlite3.Connection,
    rowid: int,
    identifier: str,
    *,
    room_name: str | None = None,
    style: int = 45,
) -> None:
    conn.execute(
        "INSERT INTO chat (ROWID, guid, chat_identifier, room_name, style) "
        "VALUES (?, ?, ?, ?, ?)",
        (rowid, f"chat-guid-{rowid}", identifier, room_name, style),
    )


def insert_message(
    conn: sqlite3.Connection,
    rowid: int,
    chat_id: int,
    *,
    text: str | None,
    attributed: bytes | None = None,
    from_me: int = 1,
    raw_date: int,
    associated_type: int = 0,
    item_type: int = 0,
    reply_guid: str | None = None,
    include_reply_column: bool = True,
) -> None:
    columns = (
        "ROWID, guid, text, attributedBody, is_from_me, date, "
        "associated_message_type, item_type"
    )
    values: list[object] = [
        rowid,
        f"message-guid-{rowid}",
        text,
        attributed,
        from_me,
        raw_date,
        associated_type,
        item_type,
    ]
    if include_reply_column:
        columns += ", thread_originator_guid"
        values.append(reply_guid)
    placeholders = ",".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO message ({columns}) VALUES ({placeholders})", values
    )
    conn.execute(
        "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
        (chat_id, rowid),
    )


def build_fixture(db_path: Path = DB_PATH) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    create_schema(conn)

    insert_chat(conn, 1, RAW_HANDLE_DIRECT)
    insert_chat(conn, 2, RAW_HANDLE_DUPLICATE)
    insert_chat(conn, 3, RAW_HANDLE_GROUP, room_name="synthetic-room")
    insert_chat(conn, 4, RAW_HANDLE_ATTRIBUTED)
    insert_chat(conn, 5, RAW_HANDLE_EPOCH)
    insert_chat(conn, 6, RAW_HANDLE_POST2024)
    insert_chat(conn, 7, RAW_HANDLE_SHORT)
    insert_chat(conn, 8, RAW_HANDLE_UNNAMED_GROUP, room_name=None, style=43)

    pre = dt.date(2020, 6, 15)
    next_day = dt.date(2020, 6, 16)
    boundary = dt.date(2024, 7, 1)

    rows = [
        # Same direct chat/day -> one bundle. The user's explicitly typed phone
        # number is accepted body content and intentionally differs from every
        # addressed handle used by the metadata privacy grep.
        dict(rowid=10, chat_id=1, text=(
            "The first outgoing message has enough ordinary words to join "
            f"today's bundle, and I typed {IN_BODY_PHONE} in my own prose."
        ), raw_date=cocoa_nanoseconds(pre)),
        dict(rowid=11, chat_id=1, text=(
            "A second outgoing message on the same day proves bundling keeps "
            "send order without merging across calendar dates."
        ), raw_date=cocoa_nanoseconds(pre, 13)),
        # Same chat, different day -> separate piece.
        dict(rowid=12, chat_id=1, text=(
            "This next-day message becomes a distinct precisely dated "
            "conversation-day document."
        ), raw_date=cocoa_nanoseconds(next_day)),
        # Text-column reply stays; its received parent is a separate row.
        dict(rowid=13, chat_id=1, text=TEXT_REPLY_OWN,
             raw_date=cocoa_nanoseconds(pre, 14), reply_guid="parent-guid"),
        dict(rowid=14, chat_id=1, text=RECEIVED_SENTINEL,
             from_me=0, raw_date=cocoa_nanoseconds(pre, 14)),
        # Structural exclusions.
        dict(rowid=15, chat_id=1, text=TAPBACK_SENTINEL,
             raw_date=cocoa_nanoseconds(pre), associated_type=2001),
        dict(rowid=16, chat_id=1, text="\ufffc",
             raw_date=cocoa_nanoseconds(pre)),
        dict(rowid=17, chat_id=1, text="   ",
             raw_date=cocoa_nanoseconds(pre)),
        dict(rowid=18, chat_id=3, text=GROUP_ACTION_SENTINEL,
             raw_date=cocoa_nanoseconds(pre), item_type=1),
        dict(rowid=19, chat_id=1, text=AUTOMATED_SENTINEL,
             raw_date=cocoa_nanoseconds(pre)),
        # Group chat retained and tagged separately.
        dict(rowid=20, chat_id=3, text=(
            "My own group-chat turn contains enough synthetic words to make "
            "the register note visible in the manifest."
        ), raw_date=cocoa_nanoseconds(pre)),
        # attributedBody-only nonreply survives; attributedBody-only reply
        # containing an independently chosen quote drops entirely.
        dict(rowid=21, chat_id=4, text=None,
             attributed=attributed_body(ATTRIBUTED_NONREPLY_TEXT),
             raw_date=cocoa_nanoseconds(next_day)),
        dict(rowid=22, chat_id=4, text=None,
             attributed=attributed_body(
                 ATTRIBUTED_REPLY_QUOTE + " " + ATTRIBUTED_REPLY_OWN
             ), raw_date=cocoa_nanoseconds(pre), reply_guid="quoted-parent"),
        # Same real timestamp represented in both Cocoa units.
        # The earlier row uses nanoseconds but has a higher rowid; the later
        # row uses seconds but has a lower rowid. Raw-SQL ordering is therefore
        # wrong unless the acquirer normalizes each instant and re-sorts.
        dict(rowid=23, chat_id=5, text=(
            "Epoch seconds variant alpha is later and must follow the beta "
            "message after mixed-unit chronological sorting."
        ), raw_date=cocoa_seconds(pre, 13)),
        dict(rowid=24, chat_id=5, text=(
            "Epoch nanoseconds variant beta is earlier and must lead the alpha "
            "message on their identical local calendar date."
        ), raw_date=cocoa_nanoseconds(pre, 12)),
        # Boundary day is post_ai_widespread/unknown, never pre_ai_human.
        dict(rowid=25, chat_id=6, text=(
            "This message sits exactly on the July 2024 boundary and therefore "
            "has conservative unknown AI status."
        ), raw_date=cocoa_nanoseconds(boundary)),
        # One deliberately short conversation-day.
        dict(rowid=26, chat_id=7, text=SHORT_SENTINEL,
             raw_date=cocoa_nanoseconds(pre)),
        # Identical complete bundles on two dates -> second dedupes.
        dict(rowid=27, chat_id=2, text=DUPLICATE_TEXT,
             raw_date=cocoa_nanoseconds(pre)),
        dict(rowid=28, chat_id=2, text=DUPLICATE_TEXT,
             raw_date=cocoa_nanoseconds(next_day)),
        # Unnamed group: room_name is NULL, so style must carry classification.
        dict(rowid=29, chat_id=8, text=(
            "My unnamed group-chat turn must still receive the group note and "
            "obey the no-include-group-chats filter."
        ), raw_date=cocoa_nanoseconds(pre)),
    ]
    for row in rows:
        insert_message(conn, **row)

    conn.commit()
    conn.close()
    return db_path


if __name__ == "__main__":
    print(f"wrote {build_fixture()}")
