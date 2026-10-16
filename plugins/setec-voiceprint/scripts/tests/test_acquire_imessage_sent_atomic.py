"""Behavior tests for the atomic iMessage sent-message acquisition.

These tests cover failures that would pass silently: which messages are selected,
what text and sidecars they produce, dates and timezones, group-chat policy, HMAC
locators, private-file modes, resume byte-identity and conserved counts. The
bootstrap/snapshot/promotion state machine fails loudly, so it keeps one
representative refusal per class plus the end-to-end offline runs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

import acquire_imessage_sent_atomic as A  # noqa: E402
import author_corpus_export as E  # noqa: E402
import register_taxonomy as rt  # noqa: E402


KEY = bytes(range(32))


def _atomic_schema(
    conn: sqlite3.Connection,
    *,
    attachment_join: bool = True,
    type_overrides: dict[tuple[str, str], str] | None = None,
    omitted: set[tuple[str, str]] | None = None,
) -> None:
    overrides = type_overrides or {}
    omitted_columns = omitted or set()
    definitions = {
        table: list(columns.items())
        for table, columns in A.REQUIRED_SCHEMA_AFFINITIES.items()
    }
    for table in ("chat", "message", "chat_message_join", "message_attachment_join"):
        if table == "message_attachment_join" and not attachment_join:
            continue
        columns = []
        if table in {"chat", "message"}:
            columns.append("ROWID INTEGER PRIMARY KEY")
        for name, declared_type in definitions[table]:
            if (table, name) in omitted_columns:
                continue
            columns.append(
                f"{name} {overrides.get((table, name), declared_type)}"
            )
        if table == "message":
            columns.append("thread_originator_guid TEXT")
        conn.execute(f"CREATE TABLE {table} ({', '.join(columns)})")


def _private_staging(tmp_path: Path, name: str = "run.staging") -> Path:
    root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    root.mkdir(mode=0o700)
    return root / name


def _bootstrap_snapshot_payload() -> dict[str, object]:
    return {
        "schema": "setec-imessage-atomic-snapshot-metadata/1",
        "file_sha256": "sha256:" + "1" * 64,
        "byte_size": 4096,
        "page_size": 4096,
        "page_count": 1,
        "schema_fingerprint": "sha256:" + "2" * 64,
        "sqlite_user_version": 0,
        "sqlite_application_id": 0,
        "sqlite_library_version": "3.50.0",
    }


def _bootstrap_universe_payload() -> dict[str, object]:
    return {
        "candidate_outgoing_rows": 3,
        "candidate_eligible_rows": 3,
        "held_missing_chat_join_rows": 0,
        "ambiguous_multi_chat_rows": 0,
        "selected_outgoing_rows": 3,
        "selected_eligible_rows": 3,
        "selected_held_missing_chat_join_rows": 0,
        "selected_ambiguous_multi_chat_rows": 0,
        "candidate_locator_universe_hash": "sha256:" + "3" * 64,
        "selected_locator_universe_hash": "sha256:" + "4" * 64,
    }


def _bootstrap_artifacts(state: str) -> dict[str, str]:
    index = A.BOOTSTRAP_STATES.index(state)
    if index < A.BOOTSTRAP_STATES.index("snapshot_closed"):
        return {}
    result = {A.SNAPSHOT_FILENAME: "sha256:" + "1" * 64}
    if index >= A.BOOTSTRAP_STATES.index("options_maps_closed"):
        result.update(
            {
                A.SEMANTIC_OPTIONS_FILENAME: "sha256:" + "b" * 64,
                A.RUN_CONTROLS_FILENAME: "sha256:" + "c" * 64,
                A.SMOKE_POLICY_FILENAME: "sha256:" + "d" * 64,
                A.PRIVATE_CONTACT_MAP_FILENAME: "sha256:" + "8" * 64,
                A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME: "sha256:" + "9" * 64,
                A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME: "sha256:" + "7" * 64,
            }
        )
    if index >= A.BOOTSTRAP_STATES.index("owner_closed"):
        result[A.RUN_OWNER_FILENAME] = "sha256:" + "a" * 64
    return result


def _bootstrap_payload(state: str, previous: dict[str, object] | None = None):
    index = A.BOOTSTRAP_STATES.index(state)
    snapshot_closed = index >= A.BOOTSTRAP_STATES.index("snapshot_closed")
    universe_closed = index >= A.BOOTSTRAP_STATES.index("universe_closed")
    return A.bootstrap_journal_payload(
        state=state,
        previous_journal_digest=(
            A.canonical_payload_digest(previous) if previous is not None else None
        ),
        staging_name="run.bootstrap-staging",
        final_name="run-final",
        semantic_options_digest="sha256:" + "b" * 64,
        run_controls_digest="sha256:" + "c" * 64,
        smoke_policy_digest="sha256:" + "d" * 64 if universe_closed else None,
        hmac_key_id_value="sha256:" + "e" * 64,
        snapshot_metadata=_bootstrap_snapshot_payload() if snapshot_closed else None,
        universe_binding=_bootstrap_universe_payload() if universe_closed else None,
        completed_artifacts=_bootstrap_artifacts(state),
    )


def _apple_ns(day: dt.date, hour: int = 12) -> int:
    instant = dt.datetime(
        day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc
    )
    unix_ns = int(instant.timestamp()) * A.NANOSECONDS_PER_SECOND
    return unix_ns - A.APPLE_UNIX_EPOCH_SECONDS * A.NANOSECONDS_PER_SECOND


def _candidate_fixture(
    path: Path, *, message_offset: int = 0, chat_rowid: int = 10
) -> tuple[sqlite3.Connection, A.AtomicSchemaInfo]:
    conn = sqlite3.connect(path)
    _atomic_schema(conn)
    conn.execute(
        "INSERT INTO chat VALUES (?, 'chat-guid-shared', '', NULL, 45)",
        (chat_rowid,),
    )
    day = dt.date(2020, 6, 15)
    rows = [
        (message_offset + 1, "message-guid-b", "same text", 1, _apple_ns(day), 0, 0),
        (message_offset + 2, "message-guid-a", "same text", 1, _apple_ns(day), 0, 0),
        (message_offset + 3, "message-guid-c", "third text", 1, _apple_ns(day, 13), 0, 0),
        (message_offset + 4, "incoming-guid", "incoming text", 0, _apple_ns(day), 0, 0),
    ]
    conn.executemany(
        "INSERT INTO message "
        "(ROWID, guid, text, is_from_me, date, associated_message_type, item_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO chat_message_join VALUES (?, ?)",
        [
            (chat_rowid, message_offset + 1),
            (chat_rowid, message_offset + 1),
            (chat_rowid, message_offset + 2),
            (chat_rowid, message_offset + 3),
            (chat_rowid, message_offset + 4),
        ],
    )
    conn.executemany(
        "INSERT INTO message_attachment_join VALUES (?, ?)",
        [
            (message_offset + 1, 100),
            (message_offset + 1, 100),
            (message_offset + 1, 101),
        ],
    )
    conn.commit()
    return conn, A.atomic_schema_preflight(conn)


def _candidate_universe(
    candidates: tuple[A.AtomicCandidate, ...],
    selected: tuple[A.AtomicCandidate, ...],
    *,
    held: tuple[A.AtomicHeldSourceRow, ...] = (),
    selected_held: tuple[A.AtomicHeldSourceRow, ...] = (),
) -> A.AtomicCandidateUniverse:
    """Construct an exact v2 universe for focused synthetic tests."""

    return A.AtomicCandidateUniverse(
        schema="setec-imessage-atomic-candidate-universe/2",
        candidate_outgoing_rows=len(candidates) + len(held),
        candidate_eligible_rows=len(candidates),
        held_missing_chat_join_rows=len(held),
        ambiguous_multi_chat_rows=0,
        selected_outgoing_rows=len(selected) + len(selected_held),
        selected_eligible_rows=len(selected),
        selected_held_missing_chat_join_rows=len(selected_held),
        selected_ambiguous_multi_chat_rows=0,
        candidates=candidates,
        selected=selected,
        held=held,
        selected_held=selected_held,
    )


def _required_cli(*group_flag: str) -> list[str]:
    return [
        *group_flag,
        "--timezone",
        "America/New_York",
        "--apple-date-unit",
        "nanoseconds",
        "--hmac-key",
        "private-hmac.key",
    ]


def test_apple_date_conversion_uses_exact_integer_formulas() -> None:
    assert A.apple_date_to_unix_ns(0, "seconds") == 978_307_200_000_000_000
    assert A.apple_date_to_unix_ns(-978_307_200, "seconds") == 0
    assert A.apple_date_to_unix_ns(0, "nanoseconds") == 978_307_200_000_000_000
    assert A.apple_date_to_unix_ns(-978_307_200_000_000_000, "nanoseconds") == 0

    beyond_float_precision = 2**53
    first = A.apple_date_to_unix_ns(beyond_float_precision, "nanoseconds")
    second = A.apple_date_to_unix_ns(beyond_float_precision + 1, "nanoseconds")
    assert second - first == 1


@pytest.mark.parametrize("raw", [True, 1.0, "1", None])
def test_apple_date_conversion_rejects_non_exact_integers(raw: object) -> None:
    with pytest.raises(A.ExactTimestampError, match="exact integer"):
        A.apple_date_to_unix_ns(raw, "seconds")  # type: ignore[arg-type]


def test_apple_date_conversion_rejects_unbound_unit() -> None:
    with pytest.raises(A.ExactTimestampError, match="seconds or nanoseconds"):
        A.apple_date_to_unix_ns(0, "auto")


def test_explicit_zoneinfo_controls_near_midnight_date() -> None:
    instant = dt.datetime(2024, 1, 1, 4, 30, tzinfo=dt.timezone.utc)
    unix_ns = int((instant - dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)).total_seconds()) * 10**9
    assert A.unix_ns_to_local_date(unix_ns, "America/New_York") == dt.date(
        2023, 12, 31
    )
    assert A.unix_ns_to_local_date(unix_ns, "UTC") == dt.date(2024, 1, 1)


def test_explicit_zoneinfo_uses_historical_dst_rules() -> None:
    # On the 2024 fall-back date, 03:30 UTC is still 23:30 on Nov 2 in New
    # York, while 04:30 UTC is 00:30 on Nov 3.
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    before = dt.datetime(2024, 11, 3, 3, 30, tzinfo=dt.timezone.utc)
    after = dt.datetime(2024, 11, 3, 4, 30, tzinfo=dt.timezone.utc)
    before_ns = int((before - epoch).total_seconds()) * 10**9
    after_ns = int((after - epoch).total_seconds()) * 10**9
    assert A.unix_ns_to_local_date(before_ns, "America/New_York") == dt.date(
        2024, 11, 2
    )
    assert A.unix_ns_to_local_date(after_ns, "America/New_York") == dt.date(
        2024, 11, 3
    )


def test_explicit_timezone_is_independent_of_host_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    unix_ns = 1_704_084_600_000_000_000
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    first = A.unix_ns_to_local_date(unix_ns, "America/New_York")
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    second = A.unix_ns_to_local_date(unix_ns, "America/New_York")
    assert first == second == dt.date(2023, 12, 31)


@pytest.mark.parametrize("timezone_name", ["", " UTC", "No/Such_Zone", "UTC\x00bad"])
def test_explicit_timezone_rejects_missing_or_invalid_names(timezone_name: str) -> None:
    with pytest.raises(A.ExplicitTimezoneError):
        A.unix_ns_to_local_date(0, timezone_name)


def test_stable_guid_validation_preserves_exact_utf8_identity() -> None:
    guid = "p:0/βeta-Case-Sensitive"
    assert A.validate_stable_guid(guid, identity="message") is guid


@pytest.mark.parametrize(
    "bad_guid",
    [None, 7, "", "   ", " sentinel", "sentinel ", "sentinel\x00value", "sentinel\nvalue", "sentinel\x85value"],
)
def test_stable_guid_validation_fails_without_echoing_raw_value(bad_guid: object) -> None:
    with pytest.raises(A.StableGuidError) as caught:
        A.validate_stable_guid(bad_guid, identity="chat")
    assert repr(bad_guid) not in str(caught.value)
    assert "sentinel" not in str(caught.value)


def test_hmac_key_id_and_locator_fixed_vectors() -> None:
    assert A.hmac_key_id(KEY) == (
        "sha256:2a0ec87d15516b1aa6e3e3c85f6b2612ac2704b2994d0d2756fae0ab5cb2d0df"
    )
    assert A.group_locator(KEY, "iMessage;+;chat-guid-ABC") == (
        "hmac-sha256:602348cba8f3f82a3e09e311178f7f67bdefb5fcc5a22bf97d6323b5fc221bb8"
    )
    assert A.entry_locator(KEY, "message-guid-XYZ") == (
        "hmac-sha256:869dc1961364c3479407d8431ac724c5ee5d8a08f00a91f4c1e5a8ac40d73b39"
    )


def test_locators_do_not_normalize_or_case_fold_guid() -> None:
    assert A.group_locator(KEY, "é") != A.group_locator(KEY, "e\u0301")
    assert A.entry_locator(KEY, "Case") != A.entry_locator(KEY, "case")


@pytest.mark.parametrize("key", [b"", b"short", bytearray(range(32))])
def test_hmac_helpers_require_at_least_32_immutable_bytes(key: object) -> None:
    with pytest.raises(A.HmacKeyError, match="at least 32 bytes"):
        A.hmac_key_id(key)  # type: ignore[arg-type]


@pytest.mark.skipif(os.name == "nt", reason="macOS/POSIX atomic acquirer")
def test_hmac_key_loader_requires_private_regular_bounded_file(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    key_path = private_root / "identity.key"
    key_path.write_bytes(KEY)
    os.chmod(key_path, 0o600)
    assert A.load_hmac_key(key_path) == KEY
    outside = tmp_path / "outside.key"
    outside.write_bytes(KEY)
    with pytest.raises(A.HmacKeyError, match="private path"):
        A.load_hmac_key(outside)
    short = private_root / "short.key"
    short.write_bytes(b"short")
    os.chmod(short, 0o600)
    with pytest.raises(A.HmacKeyError, match="size"):
        A.load_hmac_key(short)


@pytest.mark.skipif(os.name == "nt", reason="macOS/POSIX atomic acquirer")
def test_hmac_key_loader_rejects_symlink(tmp_path: Path) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    target = private_root / "target.key"
    target.write_bytes(KEY)
    os.chmod(target, 0o600)
    link = private_root / "link.key"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    with pytest.raises((A.SnapshotError, A.HmacKeyError)):
        A.load_hmac_key(link)


@pytest.mark.skipif(os.name == "nt", reason="macOS/POSIX atomic acquirer")
def test_hmac_key_loader_is_anchored_against_private_root_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    key_path = private_root / "identity.key"
    key_path.write_bytes(KEY)
    os.chmod(key_path, 0o600)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / key_path.name).write_bytes(b"z" * len(KEY))
    os.chmod(outside / key_path.name, 0o600)
    moved_private = tmp_path / "moved-private"
    real_open = A.os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == key_path.name and not swapped:
            os.rename(private_root, moved_private)
            os.symlink(outside, private_root)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(A.os, "open", swapping_open)
    assert A.load_hmac_key(key_path) == KEY
    assert swapped


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-mode contract")
def test_hmac_key_loader_rejects_group_or_world_access(tmp_path: Path) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    key_path = private_root / "identity.key"
    key_path.write_bytes(KEY)
    os.chmod(key_path, 0o640)
    with pytest.raises(A.HmacKeyError, match="permissions"):
        A.load_hmac_key(key_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows descriptor backend")
def test_hmac_key_loader_accepts_direct_windows_file(tmp_path: Path) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir()
    key_path = private_root / "identity.key"
    key_path.write_bytes(KEY)
    assert A.load_hmac_key(key_path) == KEY


def test_locator_guid_failure_does_not_echo_private_identity() -> None:
    private_guid = "private-sentinel\x00guid"
    with pytest.raises(A.StableGuidError) as caught:
        A.entry_locator(KEY, private_guid)
    assert "private-sentinel" not in str(caught.value)


def test_preprocessing_metadata_encodes_legacy_strip_ratio_exactly() -> None:
    normalized = A._canonical_preprocessing_metadata({
        "applied": True,
        "input_tokens_before": 3,
        "input_tokens_after": 2,
        "tokens_stripped": 1,
        "strip_ratio": 1 / 3,
    })

    assert normalized["strip_ratio"] == {"numerator": 1, "denominator": 3}
    assert b"0.333" not in A._canonical_json_bytes(normalized)


def test_private_maps_are_candidate_complete_selected_chat_stable_and_rowid_free() -> None:
    day = dt.date(2020, 1, 1)
    base = A.AtomicCandidate(
        snapshot_rowid=300,
        message_guid="message-guid-c",
        chat_guid="chat-guid-selected-a",
        chat_identifier="private-handle-a",
        room_name=None,
        style=45,
        group_status=A.GROUP_STATUS_DIRECT,
        unix_nanoseconds=1,
        local_date=day,
        text="third",
        attributed_body=None,
        associated_message_type=0,
        item_type=0,
        reply_link=None,
        attachment_ids=(),
    )
    candidates = (
        base,
        replace(base, snapshot_rowid=100, message_guid="message-guid-a"),
        replace(
            base,
            snapshot_rowid=200,
            message_guid="message-guid-b",
            chat_guid="chat-guid-selected-b",
            chat_identifier=None,
            room_name="private-room-b",
            style=43,
            group_status=A.GROUP_STATUS_GROUP,
        ),
        replace(
            base,
            snapshot_rowid=400,
            message_guid="message-guid-outside",
            chat_guid="chat-guid-outside",
            chat_identifier="private-handle-outside",
        ),
    )
    selected = (candidates[2], candidates[1], candidates[0])
    universe = _candidate_universe(
        tuple(reversed(candidates)), tuple(reversed(selected))
    )
    key = b"m" * 32
    contacts = A.private_contact_map_payload(universe, key)
    sources = A.private_source_identity_map_payload(universe, key, contacts)
    assert [row["contact_alias"] for row in contacts["contacts"]] == [
        "contact-000001", "contact-000002"
    ]
    assert sources["candidate_outgoing_rows"] == 4
    assert sources["selected_outgoing_rows"] == 3
    assert sources["schema"] == "setec-imessage-atomic-private-source-identity-map/2"
    assert [row["source_ordinal"] for row in sources["entries"]] == [
        "source-000001", "source-000002", "source-000003", "source-000004"
    ]
    assert all("rowid" not in key.casefold() for row in sources["entries"] for key in row)
    outside = next(row for row in sources["entries"] if not row["selected_by_date"])
    assert outside["contact_alias"] is None
    rebuilt = _candidate_universe(tuple(candidates), tuple(selected))
    assert A.private_contact_map_payload(rebuilt, key) == contacts
    assert A.private_source_identity_map_payload(rebuilt, key, contacts) == sources


def test_private_source_map_does_not_alias_unselected_row_in_selected_chat() -> None:
    day = dt.date(2020, 1, 1)
    selected = A.AtomicCandidate(
        1, "message-guid-selected", "chat-guid-shared", None, None, 45,
        A.GROUP_STATUS_DIRECT, 1, day, "selected", None, 0, 0, None, (),
    )
    outside = replace(
        selected,
        snapshot_rowid=2,
        message_guid="message-guid-outside",
        local_date=dt.date(2020, 1, 2),
        text="outside",
    )
    universe = _candidate_universe((selected, outside), (selected,))
    contact = A.private_contact_map_payload(universe, KEY)
    source = A.private_source_identity_map_payload(universe, KEY, contact)
    rows = {row["message_guid"]: row for row in source["entries"]}
    assert rows[selected.message_guid]["contact_alias"] == "contact-000001"
    assert rows[outside.message_guid]["contact_alias"] is None


def test_private_maps_canonicalize_blank_contact_and_refuse_boolean_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = dt.date(2020, 1, 1)
    candidate = A.AtomicCandidate(
        1, "message-guid", "chat-guid", "   ", "", 45,
        A.GROUP_STATUS_DIRECT, 1, day, "text", None, 0, 0, None, (),
    )
    universe = _candidate_universe((candidate,), (candidate,))
    contact = A.private_contact_map_payload(universe, b"m" * 32)
    assert contact["contacts"][0]["chat_identifier"] is None
    assert contact["contacts"][0]["room_name"] is None
    for field in ("candidate_outgoing_rows", "selected_outgoing_rows"):
        with pytest.raises(A.AtomicAcquisitionError, match="counts"):
            A.private_contact_map_payload(
                replace(universe, **{field: True}), b"m" * 32
            )
    for field in ("chat_identifier", "room_name"):
        malformed = replace(candidate, **{field: 7})
        with pytest.raises(A.AtomicAcquisitionError, match="contact metadata"):
            A.private_contact_map_payload(
                replace(universe, candidates=(malformed,), selected=(malformed,)),
                b"m" * 32,
            )

    second = replace(candidate, snapshot_rowid=2, message_guid="message-guid-2")
    two = _candidate_universe((candidate, second), (candidate, second))
    contact_two = A.private_contact_map_payload(two, b"m" * 32)
    monkeypatch.setattr(A, "entry_locator", lambda *_args: "hmac-sha256:" + "0" * 64)
    with pytest.raises(A.AtomicAcquisitionError, match="entry locators collide"):
        A.private_source_identity_map_payload(two, b"m" * 32, contact_two)


def test_chatless_hold_ledger_is_prose_free_and_preserves_date_selection(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "chatless-hold.db")
    conn.execute("DELETE FROM chat_message_join WHERE message_id IN (2, 3)")
    conn.execute(
        "UPDATE message SET date = ? WHERE ROWID = 3",
        (_apple_ns(dt.date(2020, 6, 16)),),
    )
    conn.commit()
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        until=dt.date(2020, 6, 15),
        max_messages=3,
    )
    contact = A.private_contact_map_payload(universe, KEY)
    source = A.private_source_identity_map_payload(universe, KEY, contact)
    ledger = A.private_source_hold_ledger_payload(
        universe,
        KEY,
        source,
        snapshot_file_sha256="sha256:" + "a" * 64,
    )

    assert universe.candidate_outgoing_rows == 3
    assert universe.candidate_eligible_rows == 1
    assert universe.held_missing_chat_join_rows == 2
    assert universe.selected_outgoing_rows == 2
    assert universe.selected_eligible_rows == 1
    assert universe.selected_held_missing_chat_join_rows == 1
    held_source = [
        row for row in source["entries"]
        if row["chat_join_disposition"] == "missing_chat_join"
    ]
    assert sorted(row["selected_by_date"] for row in held_source) == [False, True]
    assert sorted(row["selected_by_date"] for row in ledger["holds"]) == [False, True]
    assert all(row["group_locator"] is None for row in held_source)
    assert all(row["contact_alias"] is None for row in held_source)
    serialized = A._canonical_json_bytes(ledger).decode("utf-8")
    for private_value in (
        "message-guid-b", "message-guid-c", "same text", "third text",
        "chat-guid-shared",
    ):
        assert private_value not in serialized
    assert "snapshot_rowid" not in serialized
    conn.close()


def _offline_approved_case(
    tmp_path: Path,
    *,
    chatless: bool = False,
) -> tuple[A.AtomicRunConfig, A.OfflineApprovedImport, Path, Path]:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / 'approved-source.db'
    conn, _schema = _candidate_fixture(source)
    if chatless:
        conn.execute('DELETE FROM chat_message_join WHERE message_id IN (2, 3)')
        conn.commit()
    conn.close()

    approved_root = private_root / 'approved-runs'
    approved_root.mkdir(mode=0o700)
    os.chmod(approved_root, 0o700)
    approved_config = A.AtomicRunConfig(
        source_db=source,
        output_root=approved_root,
        run_id='approved-smoke',
        persona='joshua',
        author='Joshua Miller',
        register='message.imessage',
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit='nanoseconds',
        timezone_name='UTC',
        max_messages=10,
        max_retained=1,
        allow_empty=False,
    )
    A.run(
        approved_config,
        key_bytes=KEY,
        bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {'rules': []}),
    )
    approved_run = approved_root / 'approved-smoke'
    approved_receipt_raw = (approved_run / 'acquisition-receipt.json').read_bytes()
    approved_receipt = json.loads(approved_receipt_raw)
    live_receipt = private_root / 'imessage-atomic-live-smoke-receipt.json'
    live_receipt.write_bytes(A._canonical_json_bytes({
        'schema': 'setec-imessage-atomic-live-smoke-receipt/1',
        'smoke_policy_digest': approved_receipt['smoke_policy_digest'],
        'approved_run_receipt_sha256': A._sha256_tag(approved_receipt_raw),
        'retained_rows': 1,
        'approved_by': 'owner-tty',
        'confirmed_at': '2026-07-18T12:00:00+00:00',
    }))
    os.chmod(live_receipt, 0o600)

    archive = private_root / 'copied-archive.db'
    source_conn = sqlite3.connect(source)
    archive_conn = sqlite3.connect(archive)
    try:
        source_conn.backup(archive_conn)
    finally:
        archive_conn.close()
        source_conn.close()
    os.chmod(archive, 0o600)
    output_root = private_root / 'offline-runs'
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=archive,
        output_root=output_root,
        run_id='offline-full',
        persona='joshua',
        author='Joshua Miller',
        register='message.imessage',
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit='nanoseconds',
        timezone_name='UTC',
        max_messages=10,
        max_retained=None,
        allow_empty=False,
    )
    authorization = A.OfflineApprovedImport(
        approved_smoke_run=approved_run,
        live_smoke_receipt=live_receipt,
        archive_equivalence_db=archive,
    )
    return config, authorization, approved_run, archive


def _offline_semantic_controls(
    config: A.AtomicRunConfig,
) -> tuple[dict[str, object], dict[str, object]]:
    semantic = A.semantic_options_payload(
        since=config.since,
        until=config.until,
        include_group_chats=config.include_group_chats,
        apple_date_unit=config.apple_date_unit,
        timezone_name=config.timezone_name,
        preprocessing_version='legacy-preprocess/1',
        preprocessing_rules_id='imessage-atomic-rules/1',
        persona=config.persona,
        author=config.author,
        register=config.register,
    )
    controls = A.run_controls_payload(
        max_messages=config.max_messages,
        max_retained=config.max_retained,
        allow_empty=config.allow_empty,
        checkpoint_schema='setec-imessage-atomic-checkpoint/2',
        checkpoint_interval=1,
    )
    return semantic, controls


def test_offline_approved_refuses_hmac_key_mismatch(tmp_path: Path) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    with pytest.raises(A.HmacKeyError, match='does not match'):
        A.run_offline_approved(
            config,
            authorization,
            key_bytes=bytes(reversed(KEY)),
            preprocessor=lambda text: (text, {'rules': []}),
        )


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_approved_refuses_output_outside_private_root_without_writes(
    tmp_path: Path,
) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    public_output = tmp_path / 'public-output'
    public_output.mkdir(mode=0o700)
    with pytest.raises(A.AtomicAcquisitionError, match='private root|private-root'):
        A.run_offline_approved(
            replace(config, output_root=public_output),
            authorization,
            key_bytes=KEY,
            preprocessor=lambda text: (text, {'rules': []}),
        )
    assert list(public_output.iterdir()) == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission contract')
@pytest.mark.parametrize('artifact', ['archive', 'receipt'])
def test_offline_approved_refuses_readable_approval_artifact(
    tmp_path: Path,
    artifact: str,
) -> None:
    config, authorization, _approved, archive = _offline_approved_case(tmp_path)
    path = archive if artifact == 'archive' else authorization.live_smoke_receipt
    os.chmod(path, 0o644)
    with pytest.raises(A.AtomicAcquisitionError, match='permissions|private root'):
        A.run_offline_approved(
            config,
            authorization,
            key_bytes=KEY,
            preprocessor=lambda text: (text, {'rules': []}),
        )


def test_offline_archive_scan_is_immutable_and_sidecar_free(tmp_path: Path) -> None:
    config, authorization, _approved, archive = _offline_approved_case(tmp_path)
    conn = sqlite3.connect(archive)
    try:
        assert conn.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    finally:
        conn.close()
    if os.name != 'nt':
        os.chmod(archive, 0o600)
    for suffix in ('-wal', '-shm', '-journal'):
        archive.with_name(archive.name + suffix).unlink(missing_ok=True)
    before = A._stream_hash_and_size(archive)
    semantic, controls = _offline_semantic_controls(config)
    context = A._authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    assert context.universe.candidates
    assert A._stream_hash_and_size(archive) == before
    assert not any(
        archive.with_name(archive.name + suffix).exists()
        for suffix in ('-wal', '-shm', '-journal')
    )


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_bootstrap_resumes_interrupted_approved_snapshot_copy(
    tmp_path: Path,
) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    semantic, controls = _offline_semantic_controls(config)
    context = A._authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )

    def interrupt(boundary: str) -> None:
        if boundary == 'snapshot_published':
            raise RuntimeError('simulated interruption')

    with pytest.raises(RuntimeError, match='simulated interruption'):
        A._offline_approved_bootstrap(
            config,
            KEY,
            semantic,
            controls,
            context=context,
            fault=interrupt,
        )
    run_dir, universe, _schema, _initialization = A._offline_approved_bootstrap(
        config,
        KEY,
        semantic,
        controls,
        context=context,
    )
    assert run_dir == config.output_root / config.run_id
    assert universe == context.universe
    assert A._stream_hash_and_size(run_dir / A.SNAPSHOT_FILENAME)[0] == (
        context.snapshot_metadata.file_sha256
    )


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_bootstrap_refuses_foreign_staging_artifact(tmp_path: Path) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    semantic, controls = _offline_semantic_controls(config)
    context = A._authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )

    def interrupt(boundary: str) -> None:
        if boundary == 'staging_created':
            raise RuntimeError('simulated interruption')

    with pytest.raises(RuntimeError):
        A._offline_approved_bootstrap(
            config,
            KEY,
            semantic,
            controls,
            context=context,
            fault=interrupt,
        )
    staging_name, _journal_name = A._offline_bootstrap_names(config.run_id)
    (config.output_root / staging_name / 'foreign.bin').write_bytes(b'foreign')
    with pytest.raises(A.AtomicAcquisitionError, match='foreign'):
        A._offline_approved_bootstrap(
            config,
            KEY,
            semantic,
            controls,
            context=context,
        )


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_bootstrap_resumes_zero_byte_snapshot_prefix(tmp_path: Path) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    semantic, controls = _offline_semantic_controls(config)
    context = A._authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )

    def interrupt(boundary: str) -> None:
        if boundary == 'staging_created':
            raise RuntimeError('simulated interruption')

    with pytest.raises(RuntimeError, match='simulated interruption'):
        A._offline_approved_bootstrap(
            config,
            KEY,
            semantic,
            controls,
            context=context,
            fault=interrupt,
        )
    staging_name, _journal_name = A._offline_bootstrap_names(config.run_id)
    partial = config.output_root / staging_name / f'.{A.SNAPSHOT_FILENAME}.copying'
    partial.write_bytes(b'')
    os.chmod(partial, 0o600)
    run_dir, _universe, _schema, _initialization = A._offline_approved_bootstrap(
        config,
        KEY,
        semantic,
        controls,
        context=context,
    )
    assert A._stream_hash_and_size(run_dir / A.SNAPSHOT_FILENAME)[0] == (
        context.snapshot_metadata.file_sha256
    )


def _bounded_tree_state(root: Path) -> tuple[tuple[object, ...], ...]:
    root_info = root.lstat()
    state: list[tuple[object, ...]] = [(
        '.',
        stat.S_IFMT(root_info.st_mode),
        stat.S_IMODE(root_info.st_mode),
        root_info.st_dev,
        root_info.st_ino,
        root_info.st_nlink,
        root_info.st_size,
        root_info.st_mtime_ns,
        root_info.st_ctime_ns,
        None,
    )]
    for parent, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort(key=os.fsencode)
        file_names.sort(key=os.fsencode)
        for name in (*directory_names, *file_names):
            path = Path(parent) / name
            info = path.lstat()
            raw = path.read_bytes() if stat.S_ISREG(info.st_mode) else None
            state.append((
                path.relative_to(root).as_posix(),
                stat.S_IFMT(info.st_mode),
                stat.S_IMODE(info.st_mode),
                info.st_dev,
                info.st_ino,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                A._sha256_tag(raw) if raw is not None else None,
            ))
    return tuple(state)


@pytest.mark.skipif(sys.platform != 'darwin', reason='real macOS descriptor race')
def test_portable_row_write_contains_intermediate_symlink_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    os.chmod(outside, 0o700)
    sentinel = outside / 'sentinel.bin'
    sentinel.write_bytes(b'outside sentinel')
    os.chmod(sentinel, 0o600)
    outside_before = _bounded_tree_state(outside)
    io = A.PortableDurableRowIo(root)
    io.ensure_directory('rows')
    original = A._durable_atomic_private_bytes_at
    raced = False

    def race(parent_fd: int, filename: str, raw: bytes, **kwargs: object) -> None:
        nonlocal raced
        if not raced:
            raced = True
            os.rename(root / 'rows', root / 'rows-parked')
            os.symlink(outside, root / 'rows', target_is_directory=True)
        original(parent_fd, filename, raw, **kwargs)

    monkeypatch.setattr(A, '_durable_atomic_private_bytes_at', race)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match='parent binding'):
            io.write_bytes(
                'rows/artifact.json',
                b'{}',
                expected_existing=None,
                label='row artifact',
            )
    finally:
        io.close()
    assert _bounded_tree_state(outside) == outside_before
    assert (root / 'rows-parked' / 'artifact.json').read_bytes() == b'{}'


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_bootstrap_refuses_raced_empty_final_destination(
    tmp_path: Path,
) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    semantic, controls = _offline_semantic_controls(config)
    context = A._authorize_offline_approved_import(
        config,
        authorization,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    last_artifact = A._offline_initialization_artifacts(context)[-1].filename
    final = config.output_root / config.run_id

    def race(boundary: str) -> None:
        if boundary == f'initialization:{last_artifact}':
            final.mkdir(mode=0o700)
            os.chmod(final, 0o700)

    with pytest.raises(A.AtomicAcquisitionError, match='destination already exists'):
        A._offline_approved_bootstrap(
            config,
            KEY,
            semantic,
            controls,
            context=context,
            fault=race,
        )
    assert list(final.iterdir()) == []


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_full_run_preserves_approved_policy_and_resumes_rows(
    tmp_path: Path,
) -> None:
    config, authorization, approved_run, archive = _offline_approved_case(tmp_path)
    conn = sqlite3.connect(archive)
    try:
        conn.execute('PRAGMA user_version=7')
        conn.commit()
    finally:
        conn.close()
    approved_hash = A._stream_hash_and_size(
        approved_run / A.SNAPSHOT_FILENAME
    )[0]
    archive_hash = A._stream_hash_and_size(archive)[0]
    assert archive_hash != approved_hash

    receipt = A.run_offline_approved(
        config,
        authorization,
        key_bytes=KEY,
        preprocessor=lambda text: (text, {'rules': []}),
    )
    run_dir = config.output_root / config.run_id
    smoke = json.loads((run_dir / A.SMOKE_POLICY_FILENAME).read_bytes())
    live = json.loads(authorization.live_smoke_receipt.read_bytes())
    assert A._stream_hash_and_size(run_dir / A.SNAPSHOT_FILENAME)[0] == approved_hash
    if os.name != 'nt':
        assert stat.S_IMODE((run_dir / A.SNAPSHOT_FILENAME).stat().st_mode) == 0o600
    assert A.canonical_payload_digest(smoke) == live['smoke_policy_digest']
    assert A.validate_atomic_run(run_dir)['retained_rows'] == 3
    assert A.run_offline_approved(
        config,
        authorization,
        key_bytes=KEY,
        preprocessor=lambda text: (text, {'rules': []}),
    ) == receipt
    evidence_raw = (run_dir / A.OFFLINE_APPROVED_EVIDENCE_FILENAME).read_bytes()
    evidence = json.loads(evidence_raw)
    assert evidence['archive_file_sha256'] == archive_hash
    assert evidence['approved_snapshot_file_sha256'] == approved_hash
    assert evidence['counts']['candidate_outgoing_rows'] == 3
    assert receipt['offline_approved_evidence_sha256'] == A._sha256_tag(evidence_raw)
    assert not any('path' in key for key in evidence)
    archive.unlink()
    assert A.validate_atomic_run(run_dir)['retained_rows'] == 3


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_run_resumes_after_k_row_publications(tmp_path: Path) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)
    published = 0

    def interrupt(boundary: str) -> None:
        nonlocal published
        if boundary == 'after_journal_removed':
            published += 1
            if published == 2:
                raise RuntimeError('simulated row interruption')

    with pytest.raises(RuntimeError, match='row interruption'):
        A.run_offline_approved(
            config,
            authorization,
            key_bytes=KEY,
            preprocessor=lambda text: (text, {'rules': []}),
            publication_fault=interrupt,
        )
    receipt = A.run_offline_approved(
        config,
        authorization,
        key_bytes=KEY,
        preprocessor=lambda text: (text, {'rules': []}),
    )
    assert receipt['counts']['retained'] == 3
    assert A.validate_atomic_run(config.output_root / config.run_id)['retained_rows'] == 3


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_run_refuses_foreign_root_artifact_on_resume(tmp_path: Path) -> None:
    config, authorization, _approved, _archive = _offline_approved_case(tmp_path)

    def interrupt(boundary: str) -> None:
        if boundary == 'after_initial_checkpoint':
            raise RuntimeError('simulated row interruption')

    with pytest.raises(RuntimeError):
        A.run_offline_approved(
            config,
            authorization,
            key_bytes=KEY,
            preprocessor=lambda text: (text, {'rules': []}),
            publication_fault=interrupt,
        )
    (config.output_root / config.run_id / 'foreign.bin').write_bytes(b'foreign')
    with pytest.raises(A.AtomicAcquisitionError, match='residue'):
        A.run_offline_approved(
            config,
            authorization,
            key_bytes=KEY,
            preprocessor=lambda text: (text, {'rules': []}),
        )


@pytest.mark.skipif(sys.platform not in {'darwin', 'win32'}, reason='descriptor backend')
def test_offline_cli_loads_approved_portable_key_and_runs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, authorization, _approved, archive = _offline_approved_case(tmp_path)
    key_path = authorization.live_smoke_receipt.parent / 'author-corpus-r1a.key'
    key_path.write_bytes(KEY)
    os.chmod(key_path, 0o600)
    monkeypatch.setattr(
        A, '_SyntheticFixtureRowIo',
        lambda _root: pytest.fail('production offline flow used fixture-only reader'),
    )
    approved_snapshot = authorization.approved_smoke_run / A.SNAPSHOT_FILENAME
    real_open_read_only = A._open_read_only_database
    snapshot_move_blocked = False

    def guarded_open(path: Path) -> sqlite3.Connection:
        nonlocal snapshot_move_blocked
        if Path(path) == approved_snapshot and sys.platform == 'win32':
            with pytest.raises(PermissionError):
                os.replace(
                    approved_snapshot,
                    approved_snapshot.with_name("concurrent-snapshot"),
                )
            snapshot_move_blocked = True
        return real_open_read_only(path)

    monkeypatch.setattr(A, '_open_read_only_database', guarded_open)
    assert A.main([
        '--offline-approved-import',
        '--exclude-group-chats',
        '--timezone', 'UTC',
        '--apple-date-unit', 'nanoseconds',
        '--hmac-key', str(key_path),
        '--approved-smoke-run', str(authorization.approved_smoke_run),
        '--archive-equivalence-db', str(archive),
        '--live-smoke-receipt', str(authorization.live_smoke_receipt),
        '--output-root', str(config.output_root),
        '--run-id', 'offline-cli-full',
        '--persona', config.persona,
        '--author', config.author,
        '--register', config.register,
        '--max-messages', '10',
    ]) == 0
    capsys.readouterr()
    assert A.validate_atomic_run(
        config.output_root / 'offline-cli-full'
    )['retained_rows'] == 3


    if sys.platform == 'win32':
        assert snapshot_move_blocked


def _initialization_fixture() -> tuple[
    A.ClosedSnapshotEvidence,
    A.AtomicSchemaInfo,
    A.AtomicCandidateUniverse,
    dict[str, object],
    dict[str, object],
]:
    snapshot = A.SnapshotMetadata(**_bootstrap_snapshot_payload())
    schema = A.AtomicSchemaInfo(
        "setec-imessage-atomic-schema-info/1",
        snapshot.schema_fingerprint,
        None,
    )
    candidate = A.AtomicCandidate(
        1,
        "message-guid",
        "chat-guid",
        None,
        None,
        45,
        A.GROUP_STATUS_DIRECT,
        1,
        dt.date(2020, 1, 1),
        "text",
        None,
        0,
        0,
        None,
        (),
    )
    universe = _candidate_universe((candidate,), (candidate,))
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="text.personal",
    )
    controls = A.run_controls_payload(
        max_messages=100,
        max_retained=None,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    return snapshot, schema, universe, semantic, controls


def test_bootstrap_journal_rejects_skip_rollback_or_binding_drift() -> None:
    reserved = _bootstrap_payload("reserved")
    staging = _bootstrap_payload("staging_created", reserved)
    in_progress = _bootstrap_payload("snapshot_in_progress", staging)
    with pytest.raises(A.BootstrapStateError, match="sequential"):
        A.validate_bootstrap_transition(reserved, in_progress)
    with pytest.raises(A.BootstrapStateError, match="sequential"):
        A.validate_bootstrap_transition(staging, reserved)
    with pytest.raises(A.BootstrapStateError, match="immutable"):
        A.validate_bootstrap_transition(
            reserved, {**staging, "final_name": "changed-final"}
        )
    with pytest.raises(A.BootstrapStateError, match="chain digest"):
        A.validate_bootstrap_transition(
            reserved,
            {**staging, "previous_journal_digest": "sha256:" + "0" * 64},
        )


def _lock_stat(*, inode: int = 22):
    class LockStat:
        st_mode = stat.S_IFREG | 0o600
        st_uid = 1000
        st_nlink = 1
        st_dev = 7
        st_ino = inode
        st_size = 0
        st_mtime_ns = 1
        st_ctime_ns = 1

    return LockStat()


def test_stable_bootstrap_lock_contention_closes_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = _lock_stat()
    closed: list[int] = []
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(A.os, "open", lambda *_args, **_kwargs: 22)
    monkeypatch.setattr(A.os, "fstat", lambda _fd: info)
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: info)
    monkeypatch.setattr(A.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(
        A,
        "_flock_bootstrap_lock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BlockingIOError("held")
        ),
    )
    with pytest.raises(A.BootstrapStateError, match="already held"):
        A._acquire_bootstrap_lock_at(10, A.BOOTSTRAP_JOURNAL_FILENAME)
    assert closed == [22]


@pytest.mark.parametrize(
    ("room_name", "style", "expected"),
    [
        ("Project Room", 45, A.GROUP_STATUS_GROUP),
        ("", 43, A.GROUP_STATUS_GROUP),
        ("   ", 43, A.GROUP_STATUS_GROUP),
        (None, 45, A.GROUP_STATUS_DIRECT),
        ("", 45, A.GROUP_STATUS_DIRECT),
        (None, 99, A.GROUP_STATUS_UNKNOWN),
    ],
)
def test_group_status_closed_decision_table(
    room_name: object, style: object, expected: str
) -> None:
    assert A.classify_group_status(room_name, style) == expected


@pytest.mark.parametrize(
    ("room_name", "style"),
    [
        (7, 43),
        (None, None),
        (None, "45"),
        (None, True),
        ("Project Room", None),
    ],
)
def test_group_status_rejects_missing_or_retyped_fields(
    room_name: object, style: object
) -> None:
    with pytest.raises(A.GroupClassificationError):
        A.classify_group_status(room_name, style)


@pytest.mark.parametrize(
    ("local_date", "expected"),
    [
        (dt.date(2024, 6, 30), "pre_ai_human"),
        (dt.date(2024, 7, 1), "unknown"),
        (dt.date(2026, 1, 1), "unknown"),
    ],
)
def test_ai_date_posture(local_date: dt.date, expected: str) -> None:
    assert A.ai_status_for_local_date(local_date) == expected


@pytest.mark.parametrize("value", [None, "2024-06-30", dt.datetime(2024, 6, 30)])
def test_ai_date_posture_requires_calendar_date(value: object) -> None:
    with pytest.raises(A.AtomicAcquisitionError, match="calendar date"):
        A.ai_status_for_local_date(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("group_flag", "expected"),
    [
        ("--include-group-chats", True),
        ("--exclude-group-chats", False),
    ],
)
def test_parser_accepts_exactly_one_group_policy(
    group_flag: str, expected: bool
) -> None:
    args = A.build_arg_parser().parse_args(_required_cli(group_flag))
    assert args.include_group_chats is expected
    assert args.timezone == "America/New_York"
    assert args.apple_date_unit == "nanoseconds"
    assert args.hmac_key == Path("private-hmac.key")
    assert args.progress_interval == 100
    assert not hasattr(args, "checkpoint_interval")


def test_main_rejects_missing_acquisition_group_policy(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        A.main(_required_cli())
    assert caught.value.code == 2
    assert "--include-group-chats/--exclude-group-chats" in capsys.readouterr().err


@pytest.mark.parametrize(
    "missing_pair",
    [
        ("--timezone", "America/New_York"),
        ("--apple-date-unit", "nanoseconds"),
        ("--hmac-key", "private-hmac.key"),
    ],
)
def test_parser_requires_timezone_date_unit_and_hmac_key(
    missing_pair: tuple[str, str], capsys,
) -> None:
    argv = _required_cli("--exclude-group-chats")
    index = argv.index(missing_pair[0])
    del argv[index : index + 2]
    with pytest.raises(SystemExit) as caught:
        A.main(argv)
    assert caught.value.code == 2
    assert missing_pair[0] in capsys.readouterr().err


def test_parser_rejects_invalid_timezone_and_auto_date_unit() -> None:
    invalid_timezone = _required_cli("--exclude-group-chats")
    invalid_timezone[invalid_timezone.index("America/New_York")] = "No/Such_Zone"
    with pytest.raises(SystemExit) as caught_timezone:
        A.build_arg_parser().parse_args(invalid_timezone)
    assert caught_timezone.value.code == 2

    auto_unit = _required_cli("--exclude-group-chats")
    auto_unit[auto_unit.index("nanoseconds")] = "auto"
    with pytest.raises(SystemExit) as caught_unit:
        A.build_arg_parser().parse_args(auto_unit)
    assert caught_unit.value.code == 2


def test_main_refuses_live_acquisition_off_macos_before_source_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(A, "load_hmac_key", lambda _path: KEY)
    monkeypatch.setattr(A.sys, "platform", "win32")
    argv = _required_cli("--exclude-group-chats") + [
        "--source-db", str(tmp_path / "must-not-be-read.db"),
        "--output-root", str(tmp_path / "output"),
        "--run-id", "run-1", "--persona", "joshua",
        "--author", "Joshua Miller", "--register", "message.imessage",
        "--max-retained", "1",
    ]
    with pytest.raises(A.AtomicAcquisitionError, match="only on the macOS host"):
        A.main(argv)
    assert not (tmp_path / "must-not-be-read.db").exists()


def _register_output_root(tmp_path: Path) -> Path:
    # A private-root-shaped output path, so the bootstrap's own private-root
    # refusal cannot stand in for the register gate under test.
    return tmp_path / A.PRIVATE_ROOT_COMPONENT / "output"


def _register_cli(tmp_path: Path, register: str) -> list[str]:
    return _required_cli("--exclude-group-chats") + [
        "--source-db", str(tmp_path / "must-not-be-read.db"),
        "--output-root", str(_register_output_root(tmp_path)),
        "--run-id", "run-1", "--persona", "joshua",
        "--author", "Joshua Miller", "--register", register,
        "--max-retained", "1",
    ]


@pytest.mark.parametrize(
    "register",
    ["blog_essay", "personal", "message.facebook_messenger", "no_such_leaf"],
)
def test_main_refuses_a_non_imessage_register_before_sealing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, register: str,
) -> None:
    """An iMessage run must not be sealable under another source's register.

    ``--register`` is copied verbatim into every emitted row and bound into
    the run's source-config fingerprint, and completed-run bindings are
    immutable. The refusal therefore has to land at option-parse time, before
    the HMAC key is loaded or any output root exists, so a mislabel can never
    be frozen into a sealed run.
    """
    loaded: list[Path] = []
    monkeypatch.setattr(
        A, "load_hmac_key", lambda path: loaded.append(path) or KEY
    )
    with pytest.raises(SystemExit) as caught:
        A.main(_register_cli(tmp_path, register))
    message = str(caught.value)
    assert "--register" in message
    assert A.IMESSAGE_REGISTER in message
    assert rt.PRIVATE_DYADIC_TIER in message
    assert loaded == []
    assert not _register_output_root(tmp_path).exists()


def test_sqlite_backup_includes_committed_wal_state(tmp_path: Path) -> None:
    source = tmp_path / "live-chat.db"
    writer = sqlite3.connect(source)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    _atomic_schema(writer)
    writer.execute(
        "INSERT INTO chat VALUES (1, 'chat-guid', 'alias', NULL, 45)"
    )
    writer.commit()
    wal_path = source.with_name(source.name + "-wal")
    assert wal_path.is_file() and wal_path.stat().st_size > 0
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    reader = sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        assert reader.execute("SELECT guid FROM chat").fetchall() == [("chat-guid",)]
        assert reader.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert reader.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        reader.close()
        writer.close()
    assert metadata.schema == "setec-imessage-atomic-snapshot-metadata/1"
    assert metadata.file_sha256.startswith("sha256:")
    assert metadata.byte_size == snapshot.stat().st_size
    assert metadata.sqlite_library_version == sqlite3.sqlite_version
    assert A.snapshot_metadata_payload(metadata)["schema_fingerprint"].startswith(
        "sha256:"
    )
    assert A._snapshot_sidecars(snapshot) == ()


def test_live_source_mutation_after_snapshot_is_irrelevant(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    conn, _ = _candidate_fixture(source)
    conn.close()
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    writer = sqlite3.connect(source)
    writer.execute("UPDATE chat SET chat_identifier = 'changed-live-source'")
    writer.commit()
    writer.close()
    A.verify_snapshot(snapshot, metadata)
    reader = A._open_read_only_database(snapshot)
    try:
        assert reader.execute("SELECT DISTINCT chat_identifier FROM chat").fetchall() == [
            ("",)
        ]
    finally:
        reader.close()


def test_canonical_json_bytes_have_sorted_utf8_and_one_lf() -> None:
    assert A._canonical_json_bytes({"b": 2, "a": "é"}) == (
        b'{"a":"\xc3\xa9","b":2}\n'
    )


def test_atomic_schema_preflight_accepts_required_surface(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "schema.db")
    _atomic_schema(conn)
    info = A.atomic_schema_preflight(conn)
    assert info.schema == "setec-imessage-atomic-schema-info/1"
    assert info.schema_fingerprint == (
        "sha256:aadb12b82d0a6e7d416ddbdd7f505f912cf36648ad480390cdcec3e3c72af6a2"
    )
    assert info.reply_column == "thread_originator_guid"
    conn.close()


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE TABLE message_attachment_join (message_id INTEGER)",
        "CREATE TABLE message_attachment_join "
        "(message_id TEXT, attachment_id INTEGER)",
    ],
)
def test_atomic_schema_preflight_requires_attachment_join_contract(
    tmp_path: Path, schema_sql: str
) -> None:
    conn = sqlite3.connect(tmp_path / "schema.db")
    _atomic_schema(conn, attachment_join=False)
    conn.execute(schema_sql)
    with pytest.raises(A.SchemaPreflightError):
        A.atomic_schema_preflight(conn)
    conn.close()


def test_atomic_schema_preflight_rejects_same_named_view(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "schema.db")
    _atomic_schema(conn, attachment_join=False)
    conn.execute(
        "CREATE VIEW message_attachment_join AS "
        "SELECT CAST(1 AS INTEGER) AS message_id, "
        "CAST(2 AS INTEGER) AS attachment_id"
    )
    with pytest.raises(A.SchemaPreflightError, match="missing or retyped"):
        A.atomic_schema_preflight(conn)
    conn.close()


@pytest.mark.parametrize(
    ("table", "column", "declared_type"),
    [
        (
            table,
            column,
            {"TEXT": "BLOB", "BLOB": "TEXT", "INTEGER": "TEXT"}[affinity],
        )
        for table, columns in A.REQUIRED_SCHEMA_AFFINITIES.items()
        for column, affinity in columns.items()
    ],
)
def test_atomic_schema_preflight_rejects_retyped_required_columns(
    tmp_path: Path, table: str, column: str, declared_type: str
) -> None:
    conn = sqlite3.connect(tmp_path / "schema.db")
    _atomic_schema(conn, type_overrides={(table, column): declared_type})
    with pytest.raises(A.SchemaPreflightError, match="affinity"):
        A.atomic_schema_preflight(conn)
    conn.close()


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (table, column)
        for table, columns in A.REQUIRED_SCHEMA_AFFINITIES.items()
        for column in columns
    ],
)
def test_atomic_schema_preflight_rejects_each_missing_required_column(
    tmp_path: Path, table: str, column: str
) -> None:
    conn = sqlite3.connect(tmp_path / "schema.db")
    _atomic_schema(conn, omitted={(table, column)})
    with pytest.raises(A.SchemaPreflightError, match="column is missing"):
        A.atomic_schema_preflight(conn)
    conn.close()


def test_candidate_universe_preserves_atomic_events_and_attachment_evidence(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="America/New_York",
        since=dt.date(2020, 6, 15),
        until=dt.date(2020, 6, 15),
        max_messages=3,
    )
    assert universe.candidate_outgoing_rows == 3
    assert universe.selected_outgoing_rows == 3
    assert [row.message_guid for row in universe.candidates] == [
        "message-guid-a",
        "message-guid-b",
        "message-guid-c",
    ]
    assert len({row.chat_guid for row in universe.candidates}) == 1
    assert universe.candidates[1].attachment_ids == (100, 101)
    assert universe.candidates[0].attachment_ids == ()
    assert {row.group_status for row in universe.candidates} == {A.GROUP_STATUS_DIRECT}
    conn.close()


def test_candidate_identity_and_date_validate_before_window_filter(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute("UPDATE message SET date = 'private-sentinel' WHERE ROWID = 3")
    conn.commit()
    with pytest.raises(A.SchemaPreflightError) as caught:
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            since=dt.date(1990, 1, 1),
            until=dt.date(1990, 1, 2),
            max_messages=3,
        )
    assert "private-sentinel" not in str(caught.value)
    conn.close()


def test_candidate_universe_rejects_duplicate_message_guid(tmp_path: Path) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute(
        "UPDATE message SET guid = 'message-guid-a' WHERE ROWID = 3"
    )
    conn.commit()
    with pytest.raises(A.StableGuidError, match="duplicate stable message"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


def test_candidate_universe_rejects_conflicting_chat_join(tmp_path: Path) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute(
        "INSERT INTO chat VALUES (11, 'other-chat-guid', NULL, NULL, 45)"
    )
    conn.execute("INSERT INTO chat_message_join VALUES (11, 1)")
    conn.commit()
    with pytest.raises(A.StableGuidError, match="ambiguous multi-chat"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


def test_candidate_universe_rejects_contradictory_chat_metadata(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute(
        "INSERT INTO chat VALUES (11, 'chat-guid-shared', 'different', NULL, 45)"
    )
    conn.execute("INSERT INTO chat_message_join VALUES (11, 3)")
    conn.commit()
    with pytest.raises(A.StableGuidError, match="contradictory"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        ("UPDATE message SET text = CAST(7 AS BLOB) WHERE ROWID = 1", ()),
        ("UPDATE message SET attributedBody = 'wrong' WHERE ROWID = 1", ()),
        ("UPDATE message SET associated_message_type = 'wrong' WHERE ROWID = 1", ()),
        ("UPDATE message SET item_type = 'wrong' WHERE ROWID = 1", ()),
        ("UPDATE message SET is_from_me = CAST(1 AS BLOB) WHERE ROWID = 1", ()),
        ("UPDATE message SET guid = CAST('wrong' AS BLOB) WHERE ROWID = 1", ()),
        ("UPDATE chat SET guid = CAST('wrong' AS BLOB) WHERE ROWID = 10", ()),
        (
            "UPDATE chat SET chat_identifier = CAST('wrong' AS BLOB) "
            "WHERE ROWID = 10",
            (),
        ),
        ("UPDATE chat SET room_name = CAST('wrong' AS BLOB) WHERE ROWID = 10", ()),
        ("UPDATE chat SET style = 'wrong' WHERE ROWID = 10", ()),
        ("UPDATE chat_message_join SET chat_id = 'wrong' WHERE message_id = 1", ()),
        (
            "UPDATE chat_message_join SET message_id = CAST(1 AS BLOB) "
            "WHERE message_id = 1",
            (),
        ),
        ("UPDATE message_attachment_join SET message_id = CAST(1 AS BLOB)", ()),
        ("UPDATE message_attachment_join SET attachment_id = 'wrong'", ()),
    ],
)
def test_candidate_universe_rejects_runtime_type_drift(
    tmp_path: Path, sql: str, params: tuple[object, ...]
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute(sql, params)
    conn.commit()
    with pytest.raises((A.SchemaPreflightError, A.StableGuidError)):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


def test_candidate_universe_holds_missing_chat_join_without_blocking(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute("DELETE FROM chat_message_join WHERE message_id = 1")
    conn.commit()
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    assert universe.candidate_outgoing_rows == 3
    assert universe.candidate_eligible_rows == 2
    assert universe.held_missing_chat_join_rows == 1
    assert universe.ambiguous_multi_chat_rows == 0
    assert universe.selected_outgoing_rows == 3
    assert universe.selected_eligible_rows == 2
    assert universe.selected_held_missing_chat_join_rows == 1
    assert universe.selected_ambiguous_multi_chat_rows == 0
    assert len(universe.held) == len(universe.selected_held) == 1
    assert universe.held[0].reason == "missing_chat_join"
    assert all(
        candidate.message_guid != universe.held[0].message_guid
        for candidate in universe.candidates
    )
    conn.close()


def test_candidate_universe_rejects_join_to_nonexistent_chat(tmp_path: Path) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute("UPDATE chat_message_join SET chat_id = 999 WHERE message_id = 1")
    conn.commit()
    with pytest.raises(A.SchemaPreflightError, match="runtime SQLite source"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


def test_candidate_universe_rejects_orphan_attachment_join(tmp_path: Path) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    conn.execute("INSERT INTO message_attachment_join VALUES (999, 1)")
    conn.commit()
    with pytest.raises(A.SchemaPreflightError, match="runtime SQLite source"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
    conn.close()


def test_candidate_universe_max_messages_is_a_ceiling(tmp_path: Path) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate.db")
    with pytest.raises(A.AtomicAcquisitionError, match="ceiling"):
        A.discover_candidate_universe(
            conn,
            schema,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=2,
        )
    conn.close()


def test_candidate_semantics_are_stable_across_changed_rowids(tmp_path: Path) -> None:
    first_conn, first_schema = _candidate_fixture(tmp_path / "first.db")
    second_conn, second_schema = _candidate_fixture(
        tmp_path / "second.db", message_offset=100, chat_rowid=210
    )
    first = A.discover_candidate_universe(
        first_conn,
        first_schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    second = A.discover_candidate_universe(
        second_conn,
        second_schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )

    def semantic_projection(universe):
        return [
            {
                key: value
                for key, value in candidate.__dict__.items()
                if key != "snapshot_rowid"
            }
            for candidate in universe.candidates
        ]

    assert semantic_projection(first) == semantic_projection(second)
    assert [
        A.entry_locator(KEY, row.message_guid) for row in first.candidates
    ] == [A.entry_locator(KEY, row.message_guid) for row in second.candidates]
    assert [
        A.group_locator(KEY, row.chat_guid) for row in first.candidates
    ] == [A.group_locator(KEY, row.chat_guid) for row in second.candidates]
    first_conn.close()
    second_conn.close()


def _base_candidate(tmp_path: Path) -> tuple[sqlite3.Connection, A.AtomicSchemaInfo, A.AtomicCandidate]:
    conn, schema = _candidate_fixture(tmp_path / "processing.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    return conn, schema, universe.candidates[0]


def _identity_preprocessor(text: str) -> tuple[str, dict[str, object]]:
    return text.strip(), {"synthetic": True}


@pytest.mark.parametrize(
    ("changes", "include_groups", "expected"),
    [
        (
            {"group_status": A.GROUP_STATUS_UNKNOWN, "associated_message_type": 2001},
            False,
            "unknown_group_status",
        ),
        (
            {"group_status": A.GROUP_STATUS_GROUP, "associated_message_type": 2001},
            False,
            "group_chat_excluded",
        ),
        ({"associated_message_type": 2001}, True, "reaction"),
        ({"item_type": 1}, True, "group_action"),
        ({"text": "Missed call"}, True, "automated_system"),
        (
            {"text": A.OBJECT_REPLACEMENT, "attachment_ids": (100,)},
            True,
            "attachment_only",
        ),
        ({"text": None, "attributed_body": None}, True, "missing_text"),
    ],
)
def test_candidate_processing_closed_exclusion_precedence(
    tmp_path: Path,
    changes: dict[str, object],
    include_groups: bool,
    expected: str,
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    processed = A.process_candidate(
        replace(base, **changes),
        include_group_chats=include_groups,
        reply_detection_available=schema.reply_column is not None,
        preprocessor=_identity_preprocessor,
    )
    assert processed.disposition == expected
    assert processed.cleaned_text is None
    conn.close()


def test_candidate_processing_attributed_body_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    monkeypatch.setattr(A, "_decode_attributed_body", lambda blob: "synthetic own text")
    reply = replace(
        base,
        text=None,
        attributed_body=b"archive",
        reply_link="parent-guid",
        attachment_ids=(),
    )
    assert A.process_candidate(
        reply,
        include_group_chats=True,
        reply_detection_available=True,
        preprocessor=_identity_preprocessor,
    ).disposition == "unresolved_attributed_body"
    assert A.process_candidate(
        replace(reply, attachment_ids=(100,)),
        include_group_chats=True,
        reply_detection_available=True,
        preprocessor=_identity_preprocessor,
    ).disposition == "attachment_only"
    assert A.process_candidate(
        replace(reply, text=A.OBJECT_REPLACEMENT),
        include_group_chats=True,
        reply_detection_available=True,
        preprocessor=_identity_preprocessor,
    ).disposition == "attachment_only"
    nonreply = replace(reply, reply_link=None)
    retained = A.process_candidate(
        nonreply,
        include_group_chats=True,
        reply_detection_available=True,
        preprocessor=_identity_preprocessor,
    )
    assert retained.disposition == "retained"
    assert retained.cleaned_text == "synthetic own text"
    assert A.process_candidate(
        nonreply,
        include_group_chats=True,
        reply_detection_available=False,
        preprocessor=_identity_preprocessor,
    ).disposition == "unresolved_attributed_body"
    conn.close()


def test_candidate_processing_decoded_object_marker_is_attachment_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    monkeypatch.setattr(
        A, "_decode_attributed_body", lambda blob: A.OBJECT_REPLACEMENT
    )
    result = A.process_candidate(
        replace(
            base,
            text=None,
            attributed_body=b"archive",
            reply_link=None,
            attachment_ids=(),
        ),
        include_group_chats=True,
        reply_detection_available=schema.reply_column is not None,
        preprocessor=_identity_preprocessor,
    )
    assert result.disposition == "attachment_only"
    assert result.cleaned_text is None
    conn.close()


@pytest.mark.parametrize(
    ("reply_link", "reply_detection_available"),
    [("parent-guid", True), (None, False)],
)
def test_candidate_processing_decoded_object_marker_wins_reply_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reply_link: str | None,
    reply_detection_available: bool,
) -> None:
    conn, _, base = _base_candidate(tmp_path)
    monkeypatch.setattr(
        A, "_decode_attributed_body", lambda blob: A.OBJECT_REPLACEMENT
    )
    result = A.process_candidate(
        replace(
            base,
            text=None,
            attributed_body=b"archive",
            reply_link=reply_link,
            attachment_ids=(),
        ),
        include_group_chats=True,
        reply_detection_available=reply_detection_available,
        preprocessor=_identity_preprocessor,
    )
    assert result.disposition == "attachment_only"
    conn.close()


@pytest.mark.parametrize(
    ("changes", "decoded", "expected"),
    [
        ({"text": None, "attributed_body": None, "attachment_ids": (100,)}, "", "attachment_only"),
        ({"text": A.OBJECT_REPLACEMENT, "attributed_body": None, "attachment_ids": ()}, "", "attachment_only"),
        ({"text": None, "attributed_body": b"archive", "attachment_ids": ()}, "", "unresolved_attributed_body"),
        ({"text": None, "attributed_body": b"archive", "attachment_ids": (100,)}, "", "attachment_only"),
        ({"text": None, "attributed_body": None, "attachment_ids": ()}, "", "missing_text"),
    ],
)
def test_candidate_processing_attachment_missing_unresolved_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    decoded: str,
    expected: str,
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    monkeypatch.setattr(A, "_decode_attributed_body", lambda blob: decoded)
    result = A.process_candidate(
        replace(base, **changes),
        include_group_chats=True,
        reply_detection_available=schema.reply_column is not None,
        preprocessor=_identity_preprocessor,
    )
    assert result.disposition == expected
    conn.close()


def test_candidate_processing_keeps_short_nonempty_sender_text(
    tmp_path: Path,
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    retained = A.process_candidate(
        replace(base, text="ok", attachment_ids=(100,)),
        include_group_chats=True,
        reply_detection_available=schema.reply_column is not None,
        preprocessor=_identity_preprocessor,
    )
    assert retained.disposition == "retained"
    assert retained.cleaned_text == "ok"
    emptied = A.process_candidate(
        replace(base, text="remove me"),
        include_group_chats=True,
        reply_detection_available=True,
        preprocessor=lambda text: ("", {"synthetic": True}),
    )
    assert emptied.disposition == "empty_after_preprocess"
    conn.close()


def test_bounded_processing_counts_prefix_without_relabeling_tail(
    tmp_path: Path,
) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    rows = (
        replace(base, group_status=A.GROUP_STATUS_UNKNOWN),
        replace(base, message_guid="retained-guid-1", text="first retained"),
        replace(base, message_guid="retained-guid-2", text="second retained"),
    )
    universe = _candidate_universe(rows, rows)
    result = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=True,
        max_retained=1,
        preprocessor=_identity_preprocessor,
    )
    assert result.considered_rows == 2
    assert result.retained_rows == 1
    assert result.not_considered_after_bound == 1
    assert result.excluded_considered_by_final_reason["unknown_group_status"] == 1
    assert len(result.rows) == 2
    conn.close()


def test_selected_messages_are_preprocessed_independently(tmp_path: Path) -> None:
    conn, schema, base = _base_candidate(tmp_path)
    rows = (
        replace(base, message_guid="first-guid", text="first atomic message"),
        replace(base, message_guid="second-guid", text="second atomic message"),
    )
    universe = _candidate_universe(rows, rows)
    seen: list[str] = []

    def recording_preprocessor(text: str):
        seen.append(text)
        return text, {"synthetic": True}

    result = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=True,
        preprocessor=recording_preprocessor,
    )
    assert seen == ["first atomic message", "second atomic message"]
    assert result.retained_rows == 2
    conn.close()


def test_prose_free_candidate_and_bounded_processing_receipts(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "receipt.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    candidate_receipt = A.candidate_universe_receipt_payload(universe, KEY)
    processing = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=True,
        max_retained=1,
        preprocessor=_identity_preprocessor,
    )
    processing_receipt = A.processing_receipt_payload(processing, KEY)
    serialized = A._canonical_json_bytes(
        {"candidate": candidate_receipt, "processing": processing_receipt}
    ).decode("utf-8")
    for candidate in universe.candidates:
        assert candidate.message_guid not in serialized
        assert candidate.chat_guid not in serialized
        assert (candidate.text or "") not in serialized
    assert candidate_receipt["candidate_outgoing_rows"] == 3
    assert candidate_receipt["selected_outgoing_rows"] == 3
    assert processing_receipt["considered_rows"] == 1
    assert processing_receipt["not_considered_after_bound"] == 2
    assert processing_receipt["full_universe_eligibility_closure"] is False
    assert processing_receipt["records"][0]["disposition"] == "retained"
    assert "content_sha256" in processing_receipt["records"][0]
    conn.close()


def test_row_plan_derives_exportable_atomic_artifacts_from_closed_bootstrap() -> None:
    snapshot, schema, universe, _semantic, controls = _initialization_fixture()
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
    )
    initialization = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    result = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=False,
        preprocessor=lambda text: (text, {
            "rules": [],
            "input_tokens_before": 1,
            "input_tokens_after": 1,
            "tokens_stripped": 0,
            "strip_ratio": 0.0,
        }),
    )
    planned = A.plan_row_artifacts(result, universe, initialization, semantic, KEY)

    assert len(planned) == 1
    row = planned[0]
    assert row.disposition == "retained"
    assert row.row_stem is not None and row.row_stem.startswith("contact-000001-")
    assert row.text_bytes == b"text"
    assert row.sidecar["author_corpus_unit_kind"] == "atomic_message"
    assert row.sidecar["author_corpus_unit_count"] == 1
    assert row.sidecar["preprocessing"]["strip_ratio"] == {
        "numerator": 0,
        "denominator": 1,
    }
    assert row.fragment["entry"]["path"] == f"rows/{row.row_stem}/{row.row_stem}.txt"
    assert row.fragment["entry"]["register"] == "personal"
    assert row.ledger_row["entry_locator"] == row.entry_locator
    assert "message-guid" not in A._canonical_json_bytes(row.sidecar).decode("utf-8")


def test_progress_interval_is_nonsemantic_aggregate_only_and_sentinel_safe(
    tmp_path: Path,
) -> None:
    sentinel = "HOSTILE-PROGRESS-PROSE-SENTINEL"
    conn, schema = _candidate_fixture(tmp_path / "progress.db")
    conn.execute("UPDATE message SET text = ? WHERE guid = 'message-guid-a'", (sentinel,))
    conn.commit()
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=10,
    )
    events: list[dict[str, int]] = []
    A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=False,
        preprocessor=lambda text: (text, {"rules": []}),
        progress=events.append,
        progress_interval=1,
    )
    conn.close()

    assert len(events) == 3
    assert all(set(event) == {"considered", "retained", "excluded"} for event in events)
    assert all(all(type(value) is int for value in event.values()) for event in events)
    assert sentinel not in json.dumps(events, sort_keys=True)
    assert "message-guid" not in json.dumps(events, sort_keys=True)


def _synthetic_row_publication_case(
    run_dir: Path,
    *,
    chatless_message_ids: tuple[int, ...] = (),
    max_retained: int | None = 1,
    chat_identifier: str = "",
    message_text: str | None = None,
) -> tuple[tuple[A.PlannedAtomicRow, ...], A.AtomicProcessingResult]:
    run_dir.mkdir()
    snapshot_path = run_dir / A.SNAPSHOT_FILENAME
    conn, schema = _candidate_fixture(snapshot_path)
    if chat_identifier:
        conn.execute(
            "UPDATE chat SET chat_identifier = ?",
            (chat_identifier,),
        )
    if message_text is not None:
        conn.execute(
            "UPDATE message SET text = ? WHERE is_from_me = 1",
            (message_text,),
        )
    if chat_identifier or message_text is not None:
        conn.commit()
    if chatless_message_ids:
        conn.executemany(
            "DELETE FROM chat_message_join WHERE message_id = ?",
            ((message_id,) for message_id in chatless_message_ids),
        )
        conn.commit()
    snapshot = A._snapshot_metadata(conn, snapshot_path)
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=10,
    )
    conn.close()
    semantic = A.semantic_options_payload(
        since=None, until=None, include_group_chats=False,
        apple_date_unit="nanoseconds", timezone_name="UTC",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua", author="Joshua Miller", register="personal",
    )
    controls = A.run_controls_payload(
        max_messages=10,
        max_retained=max_retained,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
    )
    initialization = A.build_initialization_closure(
        snapshot_metadata=snapshot, schema_info=schema, universe=universe,
        key_bytes=KEY, semantic_options=semantic, run_controls=controls,
    )
    result = A.process_selected_candidates(
        universe, schema, include_group_chats=False,
        max_retained=max_retained,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    planned = A.plan_row_artifacts(
        result, universe, initialization, semantic, KEY
    )
    for artifact in initialization.artifacts:
        (run_dir / artifact.filename).write_bytes(artifact.raw)
    return planned, result


def test_atomic_validator_scans_txt_only_and_uses_digit_boundaries(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "shortcode-in-structured-data"
    planned, result = _synthetic_row_publication_case(
        run_dir,
        chat_identifier="22224",
        message_text="receipt token 1222249 remains non-identifying",
    )
    A.publish_planned_rows(run_dir, planned, result)
    stem = planned[0].row_stem
    assert stem is not None
    text_raw = (
        run_dir / A.ROWS_DIRNAME / stem / f"{stem}.txt"
    ).read_bytes()
    sidecar_raw = (
        run_dir / A.ROWS_DIRNAME / stem / f"{stem}.meta.json"
    ).read_bytes()
    assert b"22224" in text_raw
    assert b"22224" in sidecar_raw
    assert not A._text_contains_forbidden_identity(
        text_raw.decode("utf-8"), ("22224",)
    )
    assert A._text_contains_forbidden_identity(
        "send the message to 22224 now", ("22224",)
    )

    summary = A.validate_atomic_run(run_dir)

    assert summary["identity_scan"] == {
        "scanned_txt_rows": 1,
        "adjudicated_excluded_txt_rows": 0,
        "adjudicated_exclusions_sha256": None,
    }


def test_atomic_validator_requires_adjudication_for_email_in_txt_body(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "email-in-body"
    email = "author@example.test"
    planned, result = _synthetic_row_publication_case(
        run_dir,
        chat_identifier=email,
        message_text=f"Please write to {email} about this.",
    )
    A.publish_planned_rows(run_dir, planned, result)
    stem = planned[0].row_stem
    assert stem is not None

    with pytest.raises(A.AtomicAcquisitionError, match="row text leaks raw identity"):
        A.validate_atomic_run(run_dir)

    exclusions_raw = A._canonical_json_bytes({
        "schema": "setec-imessage-atomic-adjudicated-identity-exclusions/1",
        "rows": [{
            "row_stem": stem,
            "reason": "owner rejected identity-bearing row from corpus ingestion",
            "owner_decision_date": "2026-07-19",
        }],
    })
    (run_dir / A.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME).write_bytes(
        exclusions_raw
    )

    summary = A.validate_atomic_run(run_dir)

    assert summary["identity_scan"] == {
        "scanned_txt_rows": 0,
        "adjudicated_excluded_txt_rows": 1,
        "adjudicated_exclusions_sha256": A._sha256_tag(exclusions_raw),
    }


def test_row_publication_resumes_after_identity_adjudication(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "resume-after-adjudication"
    email = "author@example.test"
    planned, result = _synthetic_row_publication_case(
        run_dir,
        chat_identifier=email,
        message_text=f"Please write to {email} about this.",
    )
    first_receipt = A.publish_planned_rows(run_dir, planned, result)
    stem = planned[0].row_stem
    assert stem is not None
    (run_dir / A.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME).write_bytes(
        A._canonical_json_bytes({
            "schema": "setec-imessage-atomic-adjudicated-identity-exclusions/1",
            "rows": [{
                "row_stem": stem,
                "reason": (
                    "owner rejected identity-bearing row from corpus ingestion"
                ),
                "owner_decision_date": "2026-07-19",
            }],
        })
    )

    resumed_receipt = A.publish_planned_rows(run_dir, planned, result)

    assert resumed_receipt == first_receipt
    summary = A.validate_atomic_run(run_dir)
    assert summary["identity_scan"]["adjudicated_excluded_txt_rows"] == 1


def test_atomic_validator_rejects_free_form_sidecar_preprocessing(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "sidecar-preprocessing"
    email = "author@example.test"
    planned, result = _synthetic_row_publication_case(
        run_dir, chat_identifier=email
    )
    A.publish_planned_rows(run_dir, planned, result)
    stem = planned[0].row_stem
    assert stem is not None
    sidecar_raw = (
        run_dir / A.ROWS_DIRNAME / stem / f"{stem}.meta.json"
    ).read_bytes()
    published = json.loads(sidecar_raw)["preprocessing"]

    assert A._validated_sidecar_preprocessing(published, (email,)) == published

    smuggled_snippets = json.loads(json.dumps(published))
    smuggled_snippets["stripped_text_by_rule"] = {
        "signature_block": [f"Contact {email}"]
    }
    with pytest.raises(
        A.AtomicAcquisitionError, match="preprocessing leaks raw identity"
    ):
        A._validated_sidecar_preprocessing(smuggled_snippets, (email,))

    smuggled_key = json.loads(json.dumps(published))
    smuggled_key[email] = True
    with pytest.raises(
        A.AtomicAcquisitionError, match="preprocessing leaks raw identity"
    ):
        A._validated_sidecar_preprocessing(smuggled_key, (email,))

    shortcode_in_count = json.loads(json.dumps(published))
    shortcode_in_count["tokens_stripped"] = 22224
    assert A._validated_sidecar_preprocessing(
        shortcode_in_count, ("22224",)
    ) == shortcode_in_count

    boundary_hit = json.loads(json.dumps(published))
    boundary_hit["warning"] = "Stripped tokens near 22224 today."
    with pytest.raises(
        A.AtomicAcquisitionError, match="preprocessing leaks raw identity"
    ):
        A._validated_sidecar_preprocessing(boundary_hit, ("22224",))

    non_json_scalar = json.loads(json.dumps(published))
    non_json_scalar["rules"] = [b"bytes"]
    with pytest.raises(
        A.AtomicAcquisitionError, match="sidecar preprocessing drifted"
    ):
        A._validated_sidecar_preprocessing(non_json_scalar, (email,))

    summary = A.validate_atomic_run(run_dir)
    assert summary["identity_scan"]["scanned_txt_rows"] == 1


def test_row_publication_fresh_resume_rechecks_prior_row_mutation(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "mutated-resume"
    planned, result = _synthetic_row_publication_case(
        run_dir, max_retained=None
    )
    removed = 0

    def stop_after_first(observed: str) -> None:
        nonlocal removed
        if observed == "after_journal_removed":
            removed += 1
            if removed == 1:
                raise RuntimeError("stop after first row")

    with pytest.raises(RuntimeError, match="first row"):
        A.publish_planned_rows(run_dir, planned, result, fault=stop_after_first)
    first = planned[0]
    assert first.row_stem is not None
    target = run_dir / A.ROWS_DIRNAME / first.row_stem / f"{first.row_stem}.txt"
    target.write_bytes(b"mutated")
    with pytest.raises(A.AtomicAcquisitionError, match="committed atomic row bytes"):
        A.publish_planned_rows(run_dir, planned, result)


def test_one_row_run_with_two_chatless_holds_reports_conserved_aggregate_counts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "one-row-two-holds"
    planned, result = _synthetic_row_publication_case(
        run_dir, chatless_message_ids=(2, 3)
    )
    before = (run_dir / A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME).read_bytes()

    with pytest.raises(RuntimeError, match="synthetic hold crash"):
        A.publish_planned_rows(
            run_dir,
            planned,
            result,
            fault=lambda boundary: (
                (_ for _ in ()).throw(RuntimeError("synthetic hold crash"))
                if boundary == "after_row_commit"
                else None
            ),
        )
    assert (run_dir / A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME).read_bytes() == before
    A.publish_planned_rows(run_dir, planned, result)
    A.publish_planned_rows(run_dir, planned, result)
    assert (run_dir / A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME).read_bytes() == before

    assert A.validate_atomic_run(run_dir) == {
        "status": "closed",
        "candidate_outgoing_rows": 3,
        "candidate_eligible_rows": 1,
        "retained_rows": 1,
        "held_missing_chat_join_rows": 2,
        "ambiguous_multi_chat_rows": 0,
        "selected_outgoing_rows": 3,
        "selected_eligible_rows": 1,
        "selected_held_missing_chat_join_rows": 2,
        "selected_ambiguous_multi_chat_rows": 0,
        "considered_rows": 1,
        "not_considered_after_bound": 0,
        "identity_scan": {
            "scanned_txt_rows": 1,
            "adjudicated_excluded_txt_rows": 0,
            "adjudicated_exclusions_sha256": None,
        },
    }
    receipt_raw = (run_dir / "acquisition-receipt.json").read_bytes()
    ledger_raw = (run_dir / "source-ledger.json").read_bytes()
    for private_value in (b"message-guid-b", b"message-guid-c", b"same text", b"third text"):
        assert private_value not in before
        assert private_value not in receipt_raw
        assert private_value not in ledger_raw


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "boundary",
    [
        "after_initial_ledger",
        "after_initial_checkpoint",
        "after_journal_prepared",
        "after_text",
        "after_sidecar",
        "after_fragment",
        "after_journal_staged",
        "after_row_commit",
        "after_journal_committed_unledgered",
        "after_ledger",
        "after_journal_ledger_closed",
        "after_checkpoint",
        "after_journal_checkpoint_closed",
        "after_journal_removed",
        "after_manifest",
        "after_receipt",
    ],
)
def test_row_kill_points_resume_byte_identically(
    tmp_path: Path, boundary: str,
) -> None:
    interrupted = tmp_path / "interrupted"
    baseline = tmp_path / "baseline"
    planned, result = _synthetic_row_publication_case(interrupted)
    baseline_planned, baseline_result = _synthetic_row_publication_case(baseline)

    def kill(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("synthetic durable kill")

    with pytest.raises(RuntimeError, match="durable kill"):
        A.publish_planned_rows(interrupted, planned, result, fault=kill)
    A.publish_planned_rows(interrupted, planned, result)
    A.publish_planned_rows(baseline, baseline_planned, baseline_result)

    assert _tree_bytes(interrupted) == _tree_bytes(baseline)
    assert not (interrupted / A.ROW_JOURNAL_FILENAME).exists()
    assert list((interrupted / A.ROW_STAGING_DIRNAME).iterdir()) == []
    assert A.validate_atomic_run(interrupted)["retained_rows"] == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS durable row I/O")
def test_live_row_io_publishes_owner_only_descriptor_relative_tree(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    output_root = private_root / "runs"
    private_root.mkdir(mode=0o700)
    output_root.mkdir(mode=0o700)
    run_dir = output_root / "descriptor-row"
    planned, result = _synthetic_row_publication_case(run_dir)
    os.chmod(run_dir, 0o700)
    for path in run_dir.iterdir():
        os.chmod(path, 0o600)

    final_name = run_dir.name
    journal_name = A.bootstrap_journal_name(final_name)
    parent_fd, _ = A._open_private_parent_dirfd(output_root / journal_name)
    lock_fd, lock_name = A._acquire_bootstrap_lock_at(parent_fd, journal_name)
    final_fd = os.open(
        final_name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )
    try:
        row_io = A.LiveDurableRowIo(
            run_dir,
            final_fd=final_fd,
            parent_fd=parent_fd,
            final_name=final_name,
            journal_name=journal_name,
            lock_fd=lock_fd,
            lock_name=lock_name,
        )
        A.publish_planned_rows(run_dir, planned, result, io=row_io)
        for directory in (
            run_dir / A.ROWS_DIRNAME,
            run_dir / A.ROW_STAGING_DIRNAME,
            run_dir / A.ROWS_DIRNAME / planned[0].row_stem,
        ):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        for path in run_dir.rglob("*"):
            if path.is_file() and path.name not in {
                A.SNAPSHOT_FILENAME,
                *A.INITIALIZATION_ARTIFACT_FILENAMES,
            }:
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
    finally:
        os.close(final_fd)
        A._release_bootstrap_lock_at(parent_fd, journal_name, lock_fd, lock_name)
        os.close(lock_fd)
        os.close(parent_fd)


def test_synthetic_database_runs_through_actual_atomic_producer(tmp_path: Path) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / "chat.db"
    conn, _schema = _candidate_fixture(source)
    conn.close()
    output_root = private_root / "runs"
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=source, output_root=output_root, run_id="synthetic-three",
        persona="joshua", author="Joshua Miller", register="personal",
        since=None, until=None, include_group_chats=False,
        apple_date_unit="nanoseconds", timezone_name="UTC",
        max_messages=10, max_retained=None, allow_empty=False,
    )

    receipt = A.run(
        config, key_bytes=KEY, bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    run_dir = output_root / "synthetic-three"
    summary = A.validate_atomic_run(run_dir)
    manifest = [json.loads(line) for line in (run_dir / "draft_manifest.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()]

    assert receipt["counts"]["retained"] == 3
    assert summary["retained_rows"] == 3
    assert len(manifest) == 3
    assert len({entry["id"] for entry in manifest}) == 3
    assert len({entry["content_hash"] for entry in manifest}) == 2
    assert all(entry["source"] == "imessage_local" for entry in manifest)

    records, texts, producer_receipt, _config_hash, evidence = E.build_export(
        sources={"imessage_sent_atomic": run_dir / "draft_manifest.jsonl"},
        register_map={"imessage_sent_atomic:personal": "text.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=KEY,
    )
    package = private_root / "author-package"
    E.publish_package(
        package, records, texts, producer_receipt, hmac_key=KEY, evidence=evidence,
    )
    try:
        from voicewright.author_corpus import (
            AuthorizationRecord,
            RegisterAuthorizationScope,
            load_author_corpus_package,
        )
    except ImportError:
        pytest.skip("Voicewright consumer is not installed")
    scope = RegisterAuthorizationScope(
        AuthorizationRecord(
            persona="joshua", authorized_by="owner",
            basis="synthetic producer-consumer seam",
            attested_at="2026-07-18T00:00:00+00:00",
        ),
        registers=("text.personal",),
        allowed_ai_status=("pre_ai_human",),
    )
    sealed = load_author_corpus_package(
        package, package / "producer_receipt.json", scope,
    )
    assert len(sealed._records) == 3
    assert sealed.record_atomic_degraded is False

    assert A.run(
        config, key_bytes=KEY, bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    ) == receipt


def _completed_private_smoke_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / "chat.db"
    conn, _schema = _candidate_fixture(source)
    conn.close()
    output_root = private_root / "runs"
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=source,
        output_root=output_root,
        run_id="smoke-one",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=10,
        max_retained=1,
        allow_empty=False,
    )
    A.run(
        config,
        key_bytes=KEY,
        bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    run_dir = output_root / "smoke-one"
    return private_root, run_dir, run_dir / "acquisition-receipt.json"


@pytest.mark.parametrize("mutation", ["mode", "hard-link", "symlink"])
def test_private_validator_refuses_mode_link_and_no_follow_mutations(
    tmp_path: Path, mutation: str,
) -> None:
    private_root, run_dir, _receipt = _completed_private_smoke_run(tmp_path)
    assert A.validate_atomic_run(run_dir)["status"] == "closed"
    ledger = json.loads((run_dir / "source-ledger.json").read_bytes())
    stem = next(row["row_stem"] for row in ledger["rows"] if row["row_stem"])
    target = run_dir / A.ROWS_DIRNAME / stem / f"{stem}.meta.json"
    if mutation == "mode":
        os.chmod(target, 0o640)
    elif mutation == "hard-link":
        os.link(target, private_root / "hard-link-residue")
    else:
        original = private_root / "sidecar-original"
        target.rename(original)
        target.symlink_to(original)
    with pytest.raises(A.AtomicAcquisitionError):
        A.validate_atomic_run(run_dir)


def _install_portable_live_smoke_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep receipt contract tests portable without weakening the macOS writer."""

    def create_only(
        parent_fd: int,
        filename: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> str:
        assert kwargs["replace_existing"] is False
        assert kwargs["expected_existing_digest"] is None
        validator = kwargs["validator"]
        max_bytes = kwargs["max_bytes"]
        artifact_label = kwargs["artifact_label"]
        assert callable(validator)
        assert type(max_bytes) is int
        assert type(artifact_label) is str
        raw = A._canonical_json_bytes(payload)
        A._decode_canonical_private_json(
            raw,
            max_bytes=max_bytes,
            validator=validator,
            artifact_label=artifact_label,
        )
        descriptor = os.open(
            filename,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            pending = memoryview(raw)
            while pending:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise OSError("portable receipt fixture write made no progress")
                pending = pending[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(parent_fd)
        return A._sha256_tag(raw)

    monkeypatch.setattr(A, "_write_private_canonical_json_at", create_only)


def test_live_smoke_receipt_requires_tty_and_one_row_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_portable_live_smoke_writer(monkeypatch)
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / "chat.db"
    conn, _schema = _candidate_fixture(source)
    conn.close()
    output_root = private_root / "runs"
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=source, output_root=output_root, run_id="smoke-one",
        persona="joshua", author="Joshua Miller", register="personal",
        since=None, until=None, include_group_chats=False,
        apple_date_unit="nanoseconds", timezone_name="UTC",
        max_messages=10, max_retained=1, allow_empty=False,
    )
    A.run(
        config, key_bytes=KEY, bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    run_receipt = output_root / "smoke-one" / "acquisition-receipt.json"
    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    monkeypatch.setattr(A.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(A.sys, "stdout", io.StringIO())
    with pytest.raises(A.AtomicAcquisitionError, match="interactive TTY"):
        A.mint_live_smoke_receipt(run_receipt, approval)

    class TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(
        A.sys, "stdin", TtyBuffer("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n")
    )
    monkeypatch.setattr(A.sys, "stdout", TtyBuffer())
    minted = A.mint_live_smoke_receipt(run_receipt, approval)
    assert minted["retained_rows"] == 1
    assert approval.is_file()
    with pytest.raises(A.AtomicAcquisitionError, match="cannot publish"):
        A.mint_live_smoke_receipt(run_receipt, approval)


SCRIPT_PATH = ROOT / "acquire_imessage_sent_atomic.py"


_IMPORT_CYCLE_MARKERS = (
    "ImportError",
    "partially initialized module",
    "cannot import name",
)


def test_direct_script_invocation_help_avoids_circular_import() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    for marker in _IMPORT_CYCLE_MARKERS:
        assert marker not in proc.stderr, proc.stderr
    assert "usage:" in proc.stdout


def test_direct_script_invocation_validate_run_fails_controlled_not_via_import_error(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "no-such-atomic-run"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--validate-run", str(missing_root)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    for marker in _IMPORT_CYCLE_MARKERS:
        assert marker not in proc.stderr, proc.stderr
    # The failure must be the domain-level error the validator raises for a
    # missing run, not an unrelated crash.
    assert "AtomicAcquisitionError" in proc.stderr, proc.stderr
