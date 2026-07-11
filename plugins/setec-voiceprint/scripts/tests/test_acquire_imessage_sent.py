#!/usr/bin/env python3
"""Contract tests for ``acquire_imessage_sent.py``.

The primary fixture is a checked-in, synthetic SQLite database.  Tests that
need schema drift or a growing day create private copies at runtime.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_imessage_sent as A  # type: ignore  # noqa: E402
import manifest_validator as mv  # type: ignore  # noqa: E402
from test_data.acquisition_imessage_fixture import build_fixture as bf  # type: ignore  # noqa: E402

FIXTURE_DB = (
    ROOT / "test_data" / "acquisition_imessage_fixture" / "chat_fixture.db"
)
WINDOW = ("2019-01-01", "2025-01-01")


def private_output(tmp_path: Path, name: str = "joshua") -> Path:
    return (
        tmp_path
        / "ai-prose-baselines-private"
        / "identity"
        / "personal"
        / name
    )


def run_windowed(
    db_path: Path,
    output_dir: Path,
    *extra: str,
    min_words: int = 5,
) -> int:
    return A.main(
        [
            "--db-path",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--persona",
            "joshua",
            "--min-words-per-piece",
            str(min_words),
            "--since",
            WINDOW[0],
            "--until",
            WINDOW[1],
            *extra,
        ]
    )


def entries(output_dir: Path) -> list[dict]:
    path = output_dir / "draft_manifest.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def text_files(output_dir: Path) -> list[Path]:
    return sorted(output_dir.glob("*.txt"))


def all_text(output_dir: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in text_files(output_dir))


def metadata_blob(output_dir: Path) -> str:
    names = "\n".join(path.name for path in output_dir.iterdir())
    sidecars = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(output_dir.glob("*.meta.json"))
    )
    manifest = (output_dir / "draft_manifest.jsonl").read_text(encoding="utf-8")
    return "\n".join([names, sidecars, manifest])


def copied_db(tmp_path: Path) -> Path:
    target = tmp_path / "chat_fixture.db"
    shutil.copy2(FIXTURE_DB, target)
    return target


def sidecars(output_dir: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(output_dir.glob("*.meta.json"))
    ]


def test_checked_in_sqlite_fixture_is_present_and_complete():
    assert FIXTURE_DB.is_file()
    assert FIXTURE_DB.read_bytes().startswith(b"SQLite format 3\x00")
    conn = sqlite3.connect(f"file:{FIXTURE_DB}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM message").fetchone()[0] == 20
        assert conn.execute(
            "SELECT count(*) FROM message WHERE is_from_me = 0"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_end_to_end_bundles_filters_and_decodes(tmp_path, capsys):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    body = all_text(output)

    # Nonreply attributedBody survives; attributedBody-only reply drops.
    assert bf.ATTRIBUTED_NONREPLY_TEXT in body
    assert bf.ATTRIBUTED_REPLY_QUOTE not in body
    assert bf.ATTRIBUTED_REPLY_OWN not in body

    # Query-level received exclusion and every mechanical row exclusion.
    for sentinel in (
        bf.RECEIVED_SENTINEL,
        bf.TAPBACK_SENTINEL,
        bf.GROUP_ACTION_SENTINEL,
        bf.AUTOMATED_SENTINEL,
        bf.SHORT_SENTINEL,
    ):
        assert sentinel not in body

    # Final v1 position: text-column reply is the sender's own row and stays.
    assert bf.TEXT_REPLY_OWN in body
    assert bf.IN_BODY_PHONE in body  # accepted body PII, not metadata

    stderr = capsys.readouterr().err
    assert "Skipped (tapback/reaction): 1" in stderr
    assert "Skipped (attachment-only): 2" in stderr
    assert "Skipped (group action/rename): 1" in stderr
    assert "Skipped (quoted-reply unresolved" in stderr
    assert "Reply rows with thread_originator_guid set: 2" in stderr


def test_bundles_same_chat_per_local_day_without_cross_day_merge(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    contact_map = json.loads((output / "contact_map.json").read_text())
    stable = contact_map[bf.RAW_HANDLE_DIRECT]
    matching = [
        meta for meta in sidecars(output)
        if meta["conversation_day_key"].startswith(stable + "|")
    ]
    assert {meta["date_written"] for meta in matching} == {
        "2020-06-15",
        "2020-06-16",
    }
    day_one = next(meta for meta in matching if meta["date_written"] == "2020-06-15")
    sidecar_path = next(
        path for path in output.glob("*.meta.json")
        if json.loads(path.read_text())["content_hash"] == day_one["content_hash"]
    )
    paired = output / (sidecar_path.name[: -len(".meta.json")] + ".txt")
    text = paired.read_text()
    assert text.index("first outgoing") < text.index("second outgoing")


def test_epoch_seconds_and_nanoseconds_land_on_same_date(tmp_path):
    assert A.apple_date_to_local_date(
        bf.cocoa_seconds(dt.date(2020, 6, 15))
    ) == A.apple_date_to_local_date(
        bf.cocoa_nanoseconds(dt.date(2020, 6, 15))
    )
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    epoch_piece = next(path for path in text_files(output) if "Epoch seconds" in path.read_text())
    epoch_text = epoch_piece.read_text()
    assert "Epoch nanoseconds" in epoch_text
    assert epoch_text.index("nanoseconds variant beta") < epoch_text.index(
        "seconds variant alpha"
    )
    epoch_meta = json.loads(
        epoch_piece.with_name(epoch_piece.stem + ".meta.json").read_text()
    )
    assert epoch_meta["date_written"] == "2020-06-15"


def test_group_chat_note_and_exclusion_flag(tmp_path):
    included = private_output(tmp_path, "included")
    assert run_windowed(FIXTURE_DB, included) == 0
    assert "group_chat" in {entry.get("notes") for entry in entries(included)}
    contact_map = json.loads((included / "contact_map.json").read_text())
    for raw_group in (bf.RAW_HANDLE_GROUP, bf.RAW_HANDLE_UNNAMED_GROUP):
        stable_slug = contact_map[raw_group].replace("_", "-")
        matching = [
            entry for entry in entries(included)
            if stable_slug in entry["id"]
        ]
        assert matching and all(entry.get("notes") == "group_chat" for entry in matching)

    excluded = private_output(tmp_path, "excluded")
    assert run_windowed(
        FIXTURE_DB, excluded, "--no-include-group-chats"
    ) == 0
    assert "group_chat" not in {entry.get("notes") for entry in entries(excluded)}
    assert "unnamed group-chat turn" not in all_text(excluded)


def test_metadata_privacy_grep_and_body_pii_scope(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    metadata = metadata_blob(output)
    for raw_handle in bf.RAW_HANDLES:
        assert raw_handle not in metadata
    # Explicit id/path assertions are load-bearing filename-safety checks.
    for entry in entries(output):
        assert all(raw not in entry["id"] for raw in bf.RAW_HANDLES)
        assert all(raw not in entry["path"] for raw in bf.RAW_HANDLES)
        assert "contact-" in entry["id"]
    assert bf.IN_BODY_PHONE in all_text(output)
    assert bf.IN_BODY_PHONE not in metadata


def test_manifest_identity_fields_ai_status_and_validator_clean(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    manifest_entries = entries(output)
    assert manifest_entries
    for entry in manifest_entries:
        assert entry["corpus_role"] == "identity_baseline"
        assert entry["use"] == ["voice_profile"]
        assert entry["consent_status"] == "author_consent"
        assert entry["register"] == "personal"
        assert entry["source"] == "imessage_local"
        assert entry["acquired_via"].startswith("acquire_imessage_sent_")
        assert entry["era"] in {
            "pre_chatgpt",
            "pre_ai_widespread",
            "post_ai_widespread",
        }
        expected = (
            "unknown"
            if entry["date_written"] >= "2024-07-01"
            else "pre_ai_human"
        )
        assert entry["ai_status"] == expected
        assert "conversation_day_key" not in entry

    report = mv.validate_manifest(output / "draft_manifest.jsonl")
    assert report["issues"] == []


def test_content_hash_dedup_and_uniqueness(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    manifest_entries = entries(output)
    hashes = [entry["content_hash"] for entry in manifest_entries]
    assert len(hashes) == len(set(hashes))
    assert all_text(output).count(bf.DUPLICATE_TEXT) == 1


def test_default_word_floor_and_short_bundle_drop(tmp_path, capsys):
    parser = A.build_arg_parser()
    parsed = parser.parse_args([])
    assert parsed.min_words_per_piece == 150

    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output, min_words=20) == 0
    assert bf.SHORT_SENTINEL not in all_text(output)
    assert "Skipped (below min-words)" in capsys.readouterr().err


def test_contact_map_is_stable_and_uses_next_unused_number(tmp_path):
    output = private_output(tmp_path)
    output.mkdir(parents=True)
    (output / "contact_map.json").write_text(
        json.dumps({bf.RAW_HANDLE_DIRECT: "contact_03", "old": "contact_01"})
    )
    assert run_windowed(FIXTURE_DB, output) == 0
    first = json.loads((output / "contact_map.json").read_text())
    assert first[bf.RAW_HANDLE_DIRECT] == "contact_03"
    assert "contact_02" in first.values()
    manifest_count = len(entries(output))
    assert run_windowed(FIXTURE_DB, output) == 0  # dedupe-only rerun is success
    second = json.loads((output / "contact_map.json").read_text())
    assert second == first
    assert len(entries(output)) == manifest_count
    assert mv.validate_manifest(output / "draft_manifest.jsonl")["issues"] == []


def test_contact_map_rejects_duplicate_stable_labels(tmp_path, capsys):
    output = private_output(tmp_path)
    output.mkdir(parents=True)
    (output / "contact_map.json").write_text(
        json.dumps(
            {
                bf.RAW_HANDLE_DIRECT: "contact_01",
                bf.RAW_HANDLE_EPOCH: "contact_01",
            }
        )
    )

    assert run_windowed(FIXTURE_DB, output) == 2
    assert "reuses a contact_NN label" in capsys.readouterr().err
    assert not list(output.glob("*.txt"))
    assert not (output / "draft_manifest.jsonl").exists()


@pytest.mark.parametrize(
    "raw",
    [
        "{not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({bf.RAW_HANDLE_DIRECT: "not-a-contact-label"}),
    ],
)
def test_contact_map_rejects_malformed_shapes(tmp_path, raw, capsys):
    output = private_output(tmp_path)
    output.mkdir(parents=True)
    (output / "contact_map.json").write_text(raw)

    assert run_windowed(FIXTURE_DB, output) == 2
    assert "contact map" in capsys.readouterr().err
    assert not list(output.glob("*.txt"))


def _insert_grown_messages(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        bf.insert_message(
            conn,
            100,
            1,
            text="A fourth same-day message arrived later and must replace the smaller draft bundle.",
            raw_date=bf.cocoa_nanoseconds(dt.date(2020, 6, 15), 15),
        )
        bf.insert_message(
            conn,
            101,
            1,
            text="A fifth same-day message proves the grown conversation survives as one piece only.",
            raw_date=bf.cocoa_nanoseconds(dt.date(2020, 6, 15), 16),
        )
        conn.commit()
    finally:
        conn.close()


def test_grown_day_transaction_replaces_piece_and_manifest_line(tmp_path):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path)
    assert run_windowed(db_path, output) == 0
    before_count = len(entries(output))
    stable = json.loads((output / "contact_map.json").read_text())[bf.RAW_HANDLE_DIRECT]

    _insert_grown_messages(db_path)
    assert run_windowed(db_path, output) == 0
    assert len(entries(output)) == before_count
    matching = [
        meta for meta in sidecars(output)
        if meta.get("conversation_day_key") == f"{stable}|2020-06-15"
    ]
    assert len(matching) == 1
    assert all_text(output).count("A fifth same-day message") == 1


def test_grown_day_unlink_failure_rolls_back_all_draft_artifacts(
    tmp_path, monkeypatch
):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path)
    assert run_windowed(db_path, output) == 0
    stable = json.loads((output / "contact_map.json").read_text())[bf.RAW_HANDLE_DIRECT]
    existing = A._existing_day(output, f"{stable}|2020-06-15")
    assert existing is not None
    old_text = existing.text_path.read_bytes()
    old_meta = existing.meta_path.read_bytes()
    old_manifest = (output / "draft_manifest.jsonl").read_bytes()
    _insert_grown_messages(db_path)

    original_unlink = Path.unlink

    def fail_meta_unlink(path, *args, **kwargs):
        if path == existing.meta_path:
            raise PermissionError("synthetic meta unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_meta_unlink)
    assert run_windowed(db_path, output) == 2
    assert existing.text_path.read_bytes() == old_text
    assert existing.meta_path.read_bytes() == old_meta
    assert (output / "draft_manifest.jsonl").read_bytes() == old_manifest


def test_unchanged_rerun_rejects_preexisting_duplicate_manifest_line(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    manifest = output / "draft_manifest.jsonl"
    original = manifest.read_text()
    first_line = next(line for line in original.splitlines() if line.strip())
    manifest.write_text(original + first_line + "\n")

    assert run_windowed(FIXTURE_DB, output) == 2
    assert manifest.read_text() == original + first_line + "\n"


def test_name_map_relabel_uses_stable_key_and_keeps_one_piece_per_day(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    stable = json.loads((output / "contact_map.json").read_text())[bf.RAW_HANDLE_DIRECT]
    old_keys = {
        meta["conversation_day_key"]
        for meta in sidecars(output)
        if meta["conversation_day_key"].startswith(stable + "|")
    }
    before_count = len(entries(output))

    name_map = tmp_path / "name_map.json"
    name_map.write_text(json.dumps({bf.RAW_HANDLE_DIRECT: "family chat"}))
    assert run_windowed(FIXTURE_DB, output, "--name-map", str(name_map)) == 0
    assert len(entries(output)) == before_count
    renamed = [
        meta for meta in sidecars(output)
        if meta["conversation_day_key"].startswith(stable + "|")
    ]
    assert {meta["conversation_day_key"] for meta in renamed} == old_keys
    assert all(meta["title"].startswith("family_chat — ") for meta in renamed)


@pytest.mark.parametrize(
    "contaminated",
    [
        f"spouse {bf.RAW_HANDLE_DIRECT}",
        "friend somebody@example.com",
        "friend 555 123 0001",
    ],
)
def test_name_map_rejects_identifier_contaminated_aliases(
    tmp_path, contaminated
):
    output = private_output(tmp_path)
    name_map = tmp_path / "contaminated-name-map.json"
    name_map.write_text(json.dumps({bf.RAW_HANDLE_DIRECT: contaminated}))
    assert run_windowed(
        FIXTURE_DB, output, "--name-map", str(name_map)
    ) == 2
    assert not output.exists()


def test_possibly_merged_grown_day_refuses_without_deleting_old_files(tmp_path):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path)
    assert run_windowed(db_path, output) == 0
    before = {path.name: path.read_bytes() for path in output.glob("*.txt")}
    _insert_grown_messages(db_path)
    (output / "draft_manifest.jsonl").write_text("")

    assert run_windowed(db_path, output) == 2
    after = {path.name: path.read_bytes() for path in output.glob("*.txt")}
    assert after == before
    assert "A fifth same-day message" not in all_text(output)


def test_grown_day_refuses_when_draft_line_was_copied_to_canonical_manifest(
    tmp_path,
):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path)
    assert run_windowed(db_path, output) == 0
    before = {path.name: path.read_bytes() for path in output.glob("*.txt")}
    private_root = next(
        path for path in (output, *output.parents)
        if path.name == A.ac.PRIVATE_DIR_NAME
    )
    canonical = private_root / "corpus_manifest.jsonl"
    canonical.write_text((output / "draft_manifest.jsonl").read_text())
    _insert_grown_messages(db_path)

    assert run_windowed(db_path, output) == 2
    assert {path.name: path.read_bytes() for path in output.glob("*.txt")} == before
    assert "A fifth same-day message" not in all_text(output)


@pytest.mark.parametrize(
    "schema_sql, expected",
    [
        (
            "CREATE TABLE message (text TEXT, attributedBody BLOB, "
            "is_from_me INTEGER, associated_message_type INTEGER, "
            "date INTEGER)",
            "missing item_type",
        ),
        (
            "CREATE TABLE message (text TEXT, attributedBody BLOB, "
            "is_from_me INTEGER, associated_message_type INTEGER, "
            "item_type INTEGER, date TEXT)",
            "retyped date=TEXT",
        ),
    ],
)
def test_fixed_schema_missing_or_retyped_hard_fails(tmp_path, schema_sql, expected):
    db_path = tmp_path / "broken.db"
    conn = sqlite3.connect(db_path)
    conn.execute(schema_sql)
    conn.commit()
    with pytest.raises(A.AcquisitionError, match=expected):
        A.schema_preflight(conn)
    conn.close()


def test_reply_column_missing_degrades_without_hard_failure(tmp_path, capsys):
    db_path = tmp_path / "no-reply-column.db"
    conn = sqlite3.connect(db_path)
    bf.create_schema(conn, include_reply_column=False)
    bf.insert_chat(conn, 1, "missing-reply-column@example.invalid")
    day = dt.date(2020, 6, 15)
    bf.insert_message(
        conn,
        1,
        1,
        text="A safe text-column row still makes the degraded run useful.",
        raw_date=bf.cocoa_nanoseconds(day),
        include_reply_column=False,
    )
    bf.insert_message(
        conn,
        2,
        1,
        text=None,
        attributed=bf.attributed_body("Potential reply-shaped attributed content drops."),
        raw_date=bf.cocoa_nanoseconds(day),
        include_reply_column=False,
    )
    conn.commit()
    schema = A.schema_preflight(conn)
    assert schema.reply_column is None
    conn.close()

    output = private_output(tmp_path)
    assert run_windowed(db_path, output, min_words=1) == 0
    body = all_text(output)
    assert "safe text-column" in body
    assert "Potential reply-shaped" not in body
    assert "denominator unavailable" in capsys.readouterr().err


def test_window_filter_applies_before_max_messages_cap(tmp_path):
    db_path = tmp_path / "window-cap.db"
    conn = sqlite3.connect(db_path)
    bf.create_schema(conn)
    bf.insert_chat(conn, 1, "window@example.invalid")
    old = dt.date(2010, 1, 1)
    current = dt.date(2020, 6, 15)
    for rowid in range(1, 6):
        bf.insert_message(
            conn,
            rowid,
            1,
            text=f"Old message {rowid} lies outside the requested date window.",
            raw_date=bf.cocoa_nanoseconds(old),
        )
    bf.insert_message(
        conn,
        10,
        1,
        text="The one in-window message is allowed under a cap of one row.",
        raw_date=bf.cocoa_nanoseconds(current),
    )
    conn.commit()
    conn.close()

    output = private_output(tmp_path)
    assert A.main(
        [
            "--db-path", str(db_path),
            "--output-dir", str(output),
            "--since", "2020-01-01",
            "--until", "2021-01-01",
            "--max-messages", "1",
            "--min-words-per-piece", "1",
        ]
    ) == 0


def test_empty_window_is_nonzero_and_does_not_create_corpus(tmp_path, capsys):
    output = private_output(tmp_path)

    assert A.main(
        [
            "--db-path", str(FIXTURE_DB),
            "--output-dir", str(output),
            "--since", "2099-01-01",
            "--until", "2099-12-31",
            "--min-words-per-piece", "1",
        ]
    ) == 1
    assert "No conversation-day pieces were acquired" in capsys.readouterr().err
    assert not output.exists()


def test_contact_map_privacy_has_no_public_bypass(tmp_path):
    output = private_output(tmp_path)
    public_map = tmp_path / "public" / "contact_map.json"
    with pytest.raises(SystemExit) as exc:
        run_windowed(
            FIXTURE_DB,
            output,
            "--contact-map-path",
            str(public_map),
        )
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        A.build_arg_parser().parse_args(["--allow-public-output"])


def test_dry_run_writes_nothing_and_precedes_tty_confirmation(tmp_path, capsys):
    output = private_output(tmp_path)
    assert run_windowed(
        FIXTURE_DB, output, "--dry-run", "--live-smoke-confirmed"
    ) == 0
    assert not output.exists()
    stderr = capsys.readouterr().err
    assert "would write: contact_" in stderr
    assert "Contact map written to" not in stderr


def test_unwindowed_write_requires_database_bound_scoped_receipt(
    tmp_path, monkeypatch
):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path, "primary")
    bare = [
        "--db-path", str(db_path),
        "--output-dir", str(output),
        "--min-words-per-piece", "5",
    ]
    assert A.main(bare) == 2

    monkeypatch.setattr(A.sys.stdin, "isatty", lambda: True)
    assert run_windowed(db_path, output, "--live-smoke-confirmed") == 0
    receipt = json.loads((output / A.RECEIPT_NAME).read_text())
    assert receipt["db_sha256"] == A._db_fingerprint(db_path)
    assert A.main(bare) == 0

    # Receipt is scoped to its output tree.
    other = private_output(tmp_path, "other")
    assert A.main([
        "--db-path", str(db_path), "--output-dir", str(other),
        "--min-words-per-piece", "5",
    ]) == 2

    # Mutating the exact source invalidates the hash-bound receipt.
    _insert_grown_messages(db_path)
    assert A.main(bare) == 2


def test_live_smoke_confirmation_refuses_non_tty(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(
        FIXTURE_DB, output, "--live-smoke-confirmed"
    ) == 2
    assert not (output / A.RECEIPT_NAME).exists()


def test_readme_records_privacy_quicktype_and_reply_drop_rate(tmp_path):
    output = private_output(tmp_path)
    assert run_windowed(FIXTURE_DB, output) == 0
    readme = (output / "README.md").read_text()
    assert "In-body PII is deliberately accepted" in readme
    assert "QuickType" in readme
    assert "quoted-reply-unresolved rate" in readme


def test_full_disk_access_error_is_clear_and_nonzero(tmp_path, monkeypatch, capsys):
    db_path = copied_db(tmp_path)
    output = private_output(tmp_path)

    def denied(*_args, **_kwargs):
        raise sqlite3.OperationalError("authorization denied")

    monkeypatch.setattr(A.sqlite3, "connect", denied)
    assert run_windowed(db_path, output) == 2
    stderr = capsys.readouterr().err
    assert "Full Disk Access" in stderr
    assert sys.executable in stderr
    assert "No AppleScript" in stderr


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
