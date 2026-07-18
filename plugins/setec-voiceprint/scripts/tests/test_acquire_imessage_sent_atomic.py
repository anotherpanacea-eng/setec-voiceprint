from __future__ import annotations

import datetime as dt
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_imessage_sent_atomic as A  # noqa: E402
import author_corpus_export as E  # noqa: E402


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


@pytest.mark.skipif(os.name != "nt", reason="Windows refusal contract")
def test_hmac_key_loader_refuses_windows_host(tmp_path: Path) -> None:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir()
    key_path = private_root / "identity.key"
    key_path.write_bytes(KEY)
    with pytest.raises(A.HmacKeyError, match="macOS/POSIX"):
        A.load_hmac_key(key_path)


def test_locator_guid_failure_does_not_echo_private_identity() -> None:
    private_guid = "private-sentinel\x00guid"
    with pytest.raises(A.StableGuidError) as caught:
        A.entry_locator(KEY, private_guid)
    assert "private-sentinel" not in str(caught.value)


def test_semantic_run_and_smoke_payload_bindings_are_closed() -> None:
    semantic = A.semantic_options_payload(
        since=dt.date(2020, 1, 1),
        until=dt.date(2024, 6, 30),
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="America/New_York",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
    )
    controls = A.run_controls_payload(
        max_messages=100,
        max_retained=6,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    metadata = A.SnapshotMetadata(
        schema="setec-imessage-atomic-snapshot-metadata/1",
        file_sha256="sha256:" + "1" * 64,
        byte_size=4096,
        page_size=4096,
        page_count=1,
        schema_fingerprint="sha256:" + "2" * 64,
        sqlite_user_version=0,
        sqlite_application_id=0,
        sqlite_library_version="3.50.0",
    )
    schema = A.AtomicSchemaInfo(
        schema="setec-imessage-atomic-schema-info/1",
        schema_fingerprint=metadata.schema_fingerprint,
        reply_column="thread_originator_guid",
    )
    smoke = A.smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=metadata,
        schema_info=schema,
        hmac_key_id_value=A.hmac_key_id(KEY),
    )
    assert controls["max_retained"] == 6
    assert "max_retained" not in A._canonical_json_bytes(smoke).decode("utf-8")
    assert A.canonical_payload_digest(semantic).startswith("sha256:")
    assert A.canonical_payload_digest(smoke) == A.canonical_payload_digest(
        A.smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=metadata,
            schema_info=schema,
            hmac_key_id_value=A.hmac_key_id(KEY),
        )
    )
    with pytest.raises(A.AtomicAcquisitionError, match="semantic options"):
        A.smoke_policy_payload(
            semantic_options={**semantic, "unexpected": True},
            snapshot_metadata=metadata,
            schema_info=schema,
            hmac_key_id_value=A.hmac_key_id(KEY),
        )
    changed_controls = A.run_controls_payload(
        max_messages=100,
        max_retained=1,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    assert A.canonical_payload_digest(changed_controls) != A.canonical_payload_digest(
        controls
    )
    assert A.canonical_payload_digest(smoke) == A.canonical_payload_digest(
        A.smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=metadata,
            schema_info=schema,
            hmac_key_id_value=A.hmac_key_id(KEY),
        )
    )


@pytest.mark.parametrize(
    "metadata_change",
    [
        {"schema": "wrong/1"},
        {"file_sha256": "sha256:bad"},
        {"byte_size": 0},
        {"page_size": True},
        {"page_count": -1},
        {"sqlite_user_version": True},
        {"sqlite_application_id": -1},
        {"sqlite_library_version": "bad\nversion"},
    ],
)
def test_smoke_policy_rejects_invalid_snapshot_metadata(
    metadata_change: dict[str, object],
) -> None:
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=True,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
    )
    metadata = A.SnapshotMetadata(
        schema="setec-imessage-atomic-snapshot-metadata/1",
        file_sha256="sha256:" + "1" * 64,
        byte_size=4096,
        page_size=4096,
        page_count=1,
        schema_fingerprint="sha256:" + "2" * 64,
        sqlite_user_version=0,
        sqlite_application_id=0,
        sqlite_library_version="3.50.0",
    )
    with pytest.raises(A.AtomicAcquisitionError, match="snapshot metadata"):
        A.smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=replace(metadata, **metadata_change),
            schema_info=A.AtomicSchemaInfo(
                schema="setec-imessage-atomic-schema-info/1",
                schema_fingerprint=metadata.schema_fingerprint,
                reply_column=None,
            ),
            hmac_key_id_value=A.hmac_key_id(KEY),
        )


@pytest.mark.parametrize(
    "schema_change",
    [
        {"schema": "wrong/1"},
        {"schema_fingerprint": "sha256:" + "3" * 64},
        {"reply_column": "unregistered_reply_column"},
    ],
)
def test_smoke_policy_rejects_invalid_schema_cross_binding(
    schema_change: dict[str, object],
) -> None:
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=True,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
    )
    metadata = A.SnapshotMetadata(
        schema="setec-imessage-atomic-snapshot-metadata/1",
        file_sha256="sha256:" + "1" * 64,
        byte_size=4096,
        page_size=4096,
        page_count=1,
        schema_fingerprint="sha256:" + "2" * 64,
        sqlite_user_version=0,
        sqlite_application_id=0,
        sqlite_library_version="3.50.0",
    )
    schema = A.AtomicSchemaInfo(
        schema="setec-imessage-atomic-schema-info/1",
        schema_fingerprint=metadata.schema_fingerprint,
        reply_column=None,
    )
    with pytest.raises(A.AtomicAcquisitionError, match="schema"):
        A.smoke_policy_payload(
            semantic_options=semantic,
            snapshot_metadata=metadata,
            schema_info=replace(schema, **schema_change),
            hmac_key_id_value=A.hmac_key_id(KEY),
        )


@pytest.mark.parametrize(
    "value",
    [float("nan"), {1: "not-a-string-key"}, ("tuple",), 1.5],
)
def test_canonical_json_rejects_values_outside_closed_domain(value: object) -> None:
    with pytest.raises(A.AtomicAcquisitionError, match="canonical JSON"):
        A.canonical_payload_digest(value)


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


@pytest.mark.parametrize("ratio", [0.0, 0.5, float("nan"), float("inf")])
def test_preprocessing_metadata_rejects_ratio_not_bound_to_counts(
    ratio: float,
) -> None:
    with pytest.raises(A.AtomicAcquisitionError, match="strip ratio"):
        A._canonical_preprocessing_metadata({
            "input_tokens_before": 3,
            "input_tokens_after": 2,
            "tokens_stripped": 1,
            "strip_ratio": ratio,
        })


def test_bootstrap_journal_state_chain_is_closed_and_deterministic() -> None:
    previous = None
    payloads = []
    for state in A.BOOTSTRAP_STATES:
        current = _bootstrap_payload(state, previous)
        if previous is not None:
            A.validate_bootstrap_transition(previous, current)
        payloads.append(current)
        previous = current
    assert [payload["state"] for payload in payloads] == list(A.BOOTSTRAP_STATES)
    assert payloads[0]["previous_journal_digest"] is None
    assert payloads[-1]["previous_journal_digest"] == A.canonical_payload_digest(
        payloads[-2]
    )
    assert A.canonical_payload_digest(payloads[-1]) == A.canonical_payload_digest(
        _bootstrap_payload("promoted", payloads[-2])
    )


def test_live_bootstrap_names_are_run_specific_and_closed() -> None:
    assert A.bootstrap_staging_name("run-2026") == ".run-2026.bootstrap-staging"
    assert A.bootstrap_journal_name("run-2026") == ".run-2026.bootstrap-journal.json"
    with pytest.raises(A.BootstrapStateError):
        A.bootstrap_journal_name("../run")


def test_run_owner_marker_binds_snapshot_options_smoke_key_and_maps() -> None:
    snapshot = A.SnapshotMetadata(**_bootstrap_snapshot_payload())
    schema = A.AtomicSchemaInfo(
        "setec-imessage-atomic-schema-info/1",
        snapshot.schema_fingerprint,
        None,
    )
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=True,
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
        max_retained=1,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    key_id = "sha256:" + "e" * 64
    smoke = A.smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=snapshot,
        schema_info=schema,
        hmac_key_id_value=key_id,
    )
    owner = A.run_owner_payload(
        snapshot_metadata=snapshot,
        semantic_options=semantic,
        run_controls=controls,
        smoke_policy=smoke,
        hmac_key_id_value=key_id,
        contact_map_hash="sha256:" + "8" * 64,
        source_identity_map_hash="sha256:" + "9" * 64,
        source_hold_ledger_hash="sha256:" + "7" * 64,
    )
    assert owner == A.run_owner_payload(
        snapshot_metadata=snapshot,
        semantic_options=semantic,
        run_controls=controls,
        smoke_policy=smoke,
        hmac_key_id_value=key_id,
        contact_map_hash="sha256:" + "8" * 64,
        source_identity_map_hash="sha256:" + "9" * 64,
        source_hold_ledger_hash="sha256:" + "7" * 64,
    )
    assert owner["snapshot_file_sha256"] == snapshot.file_sha256
    assert owner["semantic_options_digest"] == A.canonical_payload_digest(semantic)
    assert owner["run_controls_digest"] == A.canonical_payload_digest(controls)
    assert owner["smoke_policy_digest"] == A.canonical_payload_digest(smoke)
    assert "path" not in A._canonical_json_bytes(owner).decode("utf-8")


def test_run_owner_marker_refuses_smoke_or_control_drift() -> None:
    snapshot = A.SnapshotMetadata(**_bootstrap_snapshot_payload())
    schema = A.AtomicSchemaInfo(
        "setec-imessage-atomic-schema-info/1",
        snapshot.schema_fingerprint,
        None,
    )
    semantic = A.semantic_options_payload(
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit="seconds",
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
    key_id = "sha256:" + "e" * 64
    smoke = A.smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=snapshot,
        schema_info=schema,
        hmac_key_id_value=key_id,
    )
    kwargs = {
        "snapshot_metadata": snapshot,
        "semantic_options": semantic,
        "run_controls": controls,
        "smoke_policy": smoke,
        "hmac_key_id_value": key_id,
        "contact_map_hash": "sha256:" + "8" * 64,
        "source_identity_map_hash": "sha256:" + "9" * 64,
        "source_hold_ledger_hash": "sha256:" + "7" * 64,
    }
    with pytest.raises(A.AtomicAcquisitionError, match="smoke policy binding"):
        A.run_owner_payload(**{**kwargs, "smoke_policy": {**smoke, "semantic_options": {
            **semantic, "timezone": "America/New_York"
        }}})
    with pytest.raises(A.AtomicAcquisitionError, match="run controls"):
        A.run_owner_payload(**{**kwargs, "run_controls": {**controls, "max_messages": True}})


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


def test_private_source_map_refuses_contact_or_membership_drift() -> None:
    day = dt.date(2020, 1, 1)
    candidate = A.AtomicCandidate(
        1, "message-guid", "chat-guid", None, None, 45,
        A.GROUP_STATUS_DIRECT, 1, day, "text", None, 0, 0, None, (),
    )
    universe = _candidate_universe((candidate,), (candidate,))
    contacts = A.private_contact_map_payload(universe, b"m" * 32)
    with pytest.raises(A.AtomicAcquisitionError, match="contact map binding"):
        A.private_source_identity_map_payload(
            universe, b"m" * 32, {**contacts, "contacts": []}
        )
    with pytest.raises(A.AtomicAcquisitionError, match="selected membership"):
        A.private_contact_map_payload(
            replace(universe, selected=(replace(candidate, text="changed"),)),
            b"m" * 32,
        )


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


def test_smoke_policy_validator_rebuilds_exact_closed_schema() -> None:
    snapshot, schema, _universe, semantic, _controls = _initialization_fixture()
    smoke = A.smoke_policy_payload(
        semantic_options=semantic,
        snapshot_metadata=snapshot,
        schema_info=schema,
        hmac_key_id_value=A.hmac_key_id(KEY),
    )
    assert smoke["schema"] == "setec-imessage-atomic-smoke-policy/2"
    assert A._validated_smoke_policy(smoke) == smoke
    with pytest.raises(A.BootstrapStateError, match="drifted"):
        A._validated_smoke_policy(
            {**smoke, "tool": {**smoke["tool"], "version": "changed"}}
        )
    with pytest.raises(A.BootstrapStateError, match="key set"):
        A._validated_smoke_policy({**smoke, "alien": True})


def test_initialization_closure_is_exact_eight_file_tree_and_key_bound() -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closure = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    assert len(A.INITIALIZATION_DEPENDENCY_FILENAMES) == 6
    assert len(closure.artifacts) == 7
    assert len(closure.expected_tree.children) == 8
    assert [item.filename for item in closure.artifacts] == [
        A.SEMANTIC_OPTIONS_FILENAME,
        A.RUN_CONTROLS_FILENAME,
        A.SMOKE_POLICY_FILENAME,
        A.PRIVATE_CONTACT_MAP_FILENAME,
        A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
        A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME,
        A.RUN_OWNER_FILENAME,
    ]
    assert set(closure.expected_tree.children) == {
        A.SNAPSHOT_FILENAME,
        *(item.filename for item in closure.artifacts),
    }
    assert all(
        type(node) is A.ExpectedPrivateFile
        for node in closure.expected_tree.children.values()
    )
    owner = closure.artifact(A.RUN_OWNER_FILENAME).payload
    assert owner["schema"] == "setec-imessage-atomic-run-owner/2"
    assert owner["contact_map_hash"] == closure.artifact(
        A.PRIVATE_CONTACT_MAP_FILENAME
    ).digest
    assert owner["source_identity_map_hash"] == closure.artifact(
        A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME
    ).digest
    assert owner["source_hold_ledger_hash"] == closure.artifact(
        A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME
    ).digest
    source = closure.artifact(A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME).payload
    assert closure.universe_binding == {
        key: source[key]
        for key in (
            "candidate_outgoing_rows",
            "candidate_eligible_rows",
            "held_missing_chat_join_rows",
            "ambiguous_multi_chat_rows",
            "selected_outgoing_rows",
            "selected_eligible_rows",
            "selected_held_missing_chat_join_rows",
            "selected_ambiguous_multi_chat_rows",
            "candidate_locator_universe_hash",
            "selected_locator_universe_hash",
        )
    }
    rebuilt = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    assert [(item.filename, item.raw) for item in rebuilt.artifacts] == [
        (item.filename, item.raw) for item in closure.artifacts
    ]
    changed_key = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=b"z" * 32,
        semantic_options=semantic,
        run_controls=controls,
    )
    assert changed_key.artifact(A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME).raw != (
        closure.artifact(A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME).raw
    )
    assert changed_key.artifact(A.RUN_OWNER_FILENAME).raw != closure.artifact(
        A.RUN_OWNER_FILENAME
    ).raw


def test_initialization_closure_ceiling_refuses_before_artifact_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    monkeypatch.setattr(A, "MAX_RUN_CONTROLS_BYTES", 1)
    with pytest.raises(A.BootstrapStateError, match="size"):
        A.build_initialization_closure(
            snapshot_metadata=snapshot,
            schema_info=schema,
            universe=universe,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
        )


def test_private_map_membership_requires_tuple_unique_positive_rowids() -> None:
    _snapshot, _schema, universe, _semantic, _controls = _initialization_fixture()
    candidate = universe.candidates[0]
    with pytest.raises(A.AtomicAcquisitionError, match="membership is not closed"):
        A.private_contact_map_payload(
            replace(universe, candidates=[candidate]),  # type: ignore[arg-type]
            KEY,
        )
    for rowid in (0, True):
        malformed = replace(candidate, snapshot_rowid=rowid)
        with pytest.raises(A.AtomicAcquisitionError, match="row identity"):
            A.private_contact_map_payload(
                replace(universe, candidates=(malformed,), selected=(malformed,)),
                KEY,
            )
    duplicate = replace(candidate, message_guid="message-guid-two")
    with pytest.raises(A.AtomicAcquisitionError, match="row identity"):
        A.private_contact_map_payload(
            replace(
                universe,
                candidate_outgoing_rows=2,
                candidate_eligible_rows=2,
                selected_outgoing_rows=2,
                selected_eligible_rows=2,
                candidates=(candidate, duplicate),
                selected=(candidate, duplicate),
            ),
            KEY,
        )


def test_private_source_map_rejects_selected_unselected_group_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _snapshot, _schema, universe, _semantic, _controls = _initialization_fixture()
    selected = universe.candidates[0]
    outside = replace(
        selected,
        snapshot_rowid=2,
        message_guid="message-guid-outside",
        chat_guid="chat-guid-outside",
    )
    two = _candidate_universe((selected, outside), (selected,))
    monkeypatch.setattr(
        A, "group_locator", lambda *_args: "hmac-sha256:" + "0" * 64
    )
    contacts = A.private_contact_map_payload(two, KEY)
    with pytest.raises(A.AtomicAcquisitionError, match="group locators collide"):
        A.private_source_identity_map_payload(two, KEY, contacts)


def test_create_or_verify_initialization_artifact_is_idempotent_not_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closed = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    ).artifact(A.SEMANTIC_OPTIONS_FILENAME)
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: object())
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(
        A,
        "_read_private_canonical_json_at",
        lambda *_args, **_kwargs: (
            closed.payload,
            (1, 2, 3, 4, 5),
            closed.digest,
            closed.raw,
        ),
    )
    monkeypatch.setattr(
        A,
        "_write_private_canonical_json_at",
        lambda *_args, **kwargs: writes.append(kwargs) or closed.digest,
    )
    assert A._create_or_verify_private_json_at(11, closed) == closed.digest
    assert writes == []


@pytest.mark.parametrize("mode", ["malformed", "mismatched"])
def test_initialization_artifact_bad_residue_refuses_without_replacement(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closed = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    ).artifact(A.SEMANTIC_OPTIONS_FILENAME)
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: object())
    if mode == "malformed":
        monkeypatch.setattr(
            A,
            "_read_private_canonical_json_at",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                A.BootstrapStateError("malformed")
            ),
        )
        match = "malformed"
    else:
        monkeypatch.setattr(
            A,
            "_read_private_canonical_json_at",
            lambda *_args, **_kwargs: (
                closed.payload,
                (1, 2, 3, 4, 5),
                "sha256:" + "0" * 64,
                b"{}\n",
            ),
        )
        match = "drifted"
    monkeypatch.setattr(
        A,
        "_write_private_canonical_json_at",
        lambda *_args, **_kwargs: pytest.fail("bad residue must not be replaced"),
    )
    with pytest.raises(A.BootstrapStateError, match=match):
        A._create_or_verify_private_json_at(11, closed)


def test_initialization_artifact_missing_is_create_only_and_ordered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closure = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A.os,
        "stat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    created: list[tuple[str, bool, object]] = []

    def create(_parent: int, filename: str, _payload: object, **kwargs: object) -> str:
        created.append(
            (filename, bool(kwargs["replace_existing"]), kwargs["expected_existing_digest"])
        )
        return closure.artifact(filename).digest

    monkeypatch.setattr(A, "_write_private_canonical_json_at", create)
    dependency_digests = A._write_initialization_dependencies_at(11, closure)
    dependencies = closure.artifacts[:-1]
    assert list(dependency_digests) == [item.filename for item in dependencies]
    dependency_evidence = {
        item.filename: (item.digest, item.raw) for item in dependencies
    }
    owner_digest = A._write_initialization_owner_at(
        11, closure, dependency_evidence
    )
    assert owner_digest == closure.artifact(A.RUN_OWNER_FILENAME).digest
    assert created == [(item.filename, False, None) for item in closure.artifacts]


def test_initialization_dependency_reread_precedes_owner_and_binds_universe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closure = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )

    def read(_parent: int, filename: str, **_kwargs: object):
        closed = closure.artifact(filename)
        return closed.payload, (1, 2, 3, 4, 5), closed.digest, closed.raw

    monkeypatch.setattr(A, "_read_private_canonical_json_at", read)
    evidence = A._reread_initialization_dependencies_at(11, closure)
    owner = A._owner_from_initialization_dependency_evidence(closure, evidence)
    assert owner.raw == closure.artifact(A.RUN_OWNER_FILENAME).raw
    changed = dict(evidence)
    source = closure.artifact(A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME)
    changed[A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME] = (
        "sha256:" + "0" * 64,
        source.raw,
    )
    with pytest.raises(A.BootstrapStateError, match="evidence drifted"):
        A._owner_from_initialization_dependency_evidence(closure, changed)


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_initialization_artifact_create_refuses_non_macos() -> None:
    snapshot, schema, universe, semantic, controls = _initialization_fixture()
    closed = A.build_initialization_closure(
        snapshot_metadata=snapshot,
        schema_info=schema,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    ).artifact(A.SEMANTIC_OPTIONS_FILENAME)
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A._create_or_verify_private_json_at(11, closed)


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


@pytest.mark.parametrize(
    ("filename", "wrong_digest"),
    [
        (A.SEMANTIC_OPTIONS_FILENAME, "sha256:" + "0" * 64),
        (A.RUN_CONTROLS_FILENAME, "sha256:" + "1" * 64),
        (A.SMOKE_POLICY_FILENAME, "sha256:" + "2" * 64),
    ],
)
def test_bootstrap_option_artifacts_must_equal_declared_digests(
    filename: str, wrong_digest: str
) -> None:
    universe = _bootstrap_payload(
        "universe_closed",
        _bootstrap_payload(
            "snapshot_closed", _bootstrap_payload("snapshot_in_progress", _bootstrap_payload("staging_created", _bootstrap_payload("reserved")))
        ),
    )
    artifacts = _bootstrap_artifacts("options_maps_closed")
    artifacts[filename] = wrong_digest
    with pytest.raises(A.BootstrapStateError, match="option artifact"):
        A.bootstrap_journal_payload(
            state="options_maps_closed",
            previous_journal_digest=A.canonical_payload_digest(universe),
            staging_name="run.bootstrap-staging",
            final_name="run-final",
            semantic_options_digest="sha256:" + "b" * 64,
            run_controls_digest="sha256:" + "c" * 64,
            smoke_policy_digest="sha256:" + "d" * 64,
            hmac_key_id_value="sha256:" + "e" * 64,
            snapshot_metadata=_bootstrap_snapshot_payload(),
            universe_binding=_bootstrap_universe_payload(),
            completed_artifacts=artifacts,
        )


def test_bootstrap_transition_preserves_every_completed_artifact() -> None:
    chain: dict[str, object] | None = None
    by_state: dict[str, dict[str, object]] = {}
    for state in A.BOOTSTRAP_STATES[: A.BOOTSTRAP_STATES.index("owner_closed") + 1]:
        chain = _bootstrap_payload(state, chain)
        by_state[state] = chain
    options = by_state["options_maps_closed"]
    owner = by_state["owner_closed"]
    changed = dict(owner["completed_artifacts"])
    changed[A.PRIVATE_CONTACT_MAP_FILENAME] = "sha256:" + "f" * 64
    with pytest.raises(A.BootstrapStateError, match="artifact drifted"):
        A.validate_bootstrap_transition(
            options, {**owner, "completed_artifacts": changed}
        )


@pytest.mark.parametrize(
    "mutation",
    [
        {"staging_name": "../escape"},
        {"completed_artifacts": {A.SNAPSHOT_FILENAME: "sha256:" + "1" * 64}},
        {"snapshot_metadata": _bootstrap_snapshot_payload()},
        {"universe_binding": _bootstrap_universe_payload()},
        {"smoke_policy_digest": "sha256:" + "d" * 64},
    ],
)
def test_reserved_bootstrap_payload_rejects_early_or_unsafe_state(
    mutation: dict[str, object],
) -> None:
    args: dict[str, object] = {
        "state": "reserved",
        "previous_journal_digest": None,
        "staging_name": "run.bootstrap-staging",
        "final_name": "run-final",
        "semantic_options_digest": "sha256:" + "b" * 64,
        "run_controls_digest": "sha256:" + "c" * 64,
        "smoke_policy_digest": None,
        "hmac_key_id_value": "sha256:" + "e" * 64,
        "snapshot_metadata": None,
        "universe_binding": None,
        "completed_artifacts": {},
    }
    args.update(mutation)
    with pytest.raises(A.BootstrapStateError):
        A.bootstrap_journal_payload(**args)  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS durable journal")
def test_durable_bootstrap_journal_create_and_advance(tmp_path: Path) -> None:
    journal = _private_staging(tmp_path, A.BOOTSTRAP_JOURNAL_FILENAME)
    reserved = _bootstrap_payload("reserved")
    reserved_hash = A.write_bootstrap_journal(journal, reserved)
    assert reserved_hash == A.canonical_payload_digest(reserved)
    assert A._read_canonical_bootstrap_journal(journal) == reserved
    staging = _bootstrap_payload("staging_created", reserved)
    staging_hash = A.write_bootstrap_journal(journal, staging)
    assert staging_hash == A.canonical_payload_digest(staging)
    assert A._read_canonical_bootstrap_journal(journal) == staging
    assert list(journal.parent.glob(f".{journal.name}.*.tmp")) == []
    if os.name != "nt":
        assert stat.S_IMODE(journal.stat().st_mode) == 0o600


def test_bootstrap_journal_refuses_noncanonical_or_duplicate_existing_bytes(
    tmp_path: Path,
) -> None:
    journal = _private_staging(tmp_path, A.BOOTSTRAP_JOURNAL_FILENAME)
    journal.write_text('{"state":"reserved","state":"reserved"}\n', encoding="utf-8")
    os.chmod(journal, 0o600)
    with pytest.raises(A.BootstrapStateError, match="duplicate"):
        A._read_canonical_bootstrap_journal(journal)
    journal.write_bytes(b'{"state": "reserved"}\n')
    os.chmod(journal, 0o600)
    with pytest.raises(A.BootstrapStateError, match="noncanonical"):
        A._read_canonical_bootstrap_journal(journal)


def _private_fixture_validator(value: dict[str, object]) -> dict[str, object]:
    if set(value) != {"schema", "value"} or value.get("schema") != "fixture/1":
        raise A.BootstrapStateError("fixture schema is invalid")
    if type(value.get("value")) is not int:
        raise A.BootstrapStateError("fixture value is invalid")
    return dict(value)


def test_generic_private_json_decoder_is_closed_canonical_and_bounded() -> None:
    raw = b'{"schema":"fixture/1","value":7}\n'
    assert A._decode_canonical_private_json(
        raw,
        max_bytes=len(raw),
        validator=_private_fixture_validator,
        artifact_label="private fixture",
    ) == {"schema": "fixture/1", "value": 7}
    with pytest.raises(A.BootstrapStateError, match="duplicate"):
        A._decode_canonical_private_json(
            b'{"schema":"fixture/1","value":7,"value":8}\n',
            max_bytes=1024,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
        )
    with pytest.raises(A.BootstrapStateError, match="noncanonical"):
        A._decode_canonical_private_json(
            b'{"value":7,"schema":"fixture/1"}\n',
            max_bytes=1024,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
        )
    with pytest.raises(A.BootstrapStateError, match="size"):
        A._decode_canonical_private_json(
            raw,
            max_bytes=len(raw) - 1,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
        )
    with pytest.raises(A.BootstrapStateError, match="ceiling"):
        A._decode_canonical_private_json(
            raw,
            max_bytes=True,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
        )


def test_generic_private_json_decoder_rejects_validator_byte_drift() -> None:
    with pytest.raises(A.BootstrapStateError, match="validator changed"):
        A._decode_canonical_private_json(
            b'{"schema":"fixture/1","value":7}\n',
            max_bytes=1024,
            validator=lambda value: {**value, "value": 8},
            artifact_label="private fixture",
        )


@pytest.mark.parametrize(
    "validator",
    [
        lambda _value: (_ for _ in ()).throw(A.AtomicAcquisitionError("schema")),
        lambda _value: (_ for _ in ()).throw(TypeError("validator bug")),
    ],
)
def test_generic_private_json_decoder_normalizes_validator_failures(
    validator: object,
) -> None:
    with pytest.raises(A.BootstrapStateError, match="schema"):
        A._decode_canonical_private_json(
            b'{"schema":"fixture/1","value":7}\n',
            max_bytes=1024,
            validator=validator,  # type: ignore[arg-type]
            artifact_label="private fixture",
        )


def test_generic_private_json_decoder_normalizes_noncanonical_domain() -> None:
    with pytest.raises(A.BootstrapStateError, match="schema"):
        A._decode_canonical_private_json(
            b'{"schema":"fixture/1","value":1.5}\n',
            max_bytes=1024,
            validator=lambda value: value,
            artifact_label="private fixture",
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"fixture/1","value":' + b"9" * 5000 + b"}\n",
        b'{"schema":"fixture/1","value":'
        + b"[" * 1500
        + b"0"
        + b"]" * 1500
        + b"}\n",
    ],
)
def test_generic_private_json_decoder_normalizes_parser_limits(raw: bytes) -> None:
    with pytest.raises(A.BootstrapStateError, match="valid JSON"):
        A._decode_canonical_private_json(
            raw,
            max_bytes=64 * 1024,
            validator=lambda value: value,
            artifact_label="private fixture",
        )


def test_generic_private_json_writer_rejects_validator_byte_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A,
        "_durable_atomic_private_file_at",
        lambda *_args, **_kwargs: pytest.fail("drifted bytes must not be published"),
    )
    with pytest.raises(A.BootstrapStateError, match="validator changed"):
        A._write_private_canonical_json_at(
            11,
            "fixture.json",
            {"schema": "fixture/1", "value": 7},
            max_bytes=1024,
            validator=lambda value: {**value, "value": 8},
            artifact_label="private fixture",
            replace_existing=False,
            expected_existing_digest=None,
        )


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_generic_private_json_writer_refuses_non_macos_before_io() -> None:
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A._write_private_canonical_json_at(
            -1,
            "fixture.json",
            {"schema": "fixture/1", "value": 7},
            max_bytes=1024,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
            replace_existing=False,
            expected_existing_digest=None,
        )


def _portable_rename_exclusive_at(
    parent_fd: int, source: str, destination: str
) -> None:
    """Test-only rename-excl stand-in for non-macOS CI."""

    try:
        os.stat(destination, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise A.BootstrapStateError("portable exclusive rename destination exists")
    os.rename(
        source,
        destination,
        src_dir_fd=parent_fd,
        dst_dir_fd=parent_fd,
    )


def _portable_swap_names_at(parent_fd: int, left: str, right: str) -> None:
    """Test-only same-directory exchange stand-in for non-macOS CI."""

    holding = ".portable-swap-holding"
    os.rename(left, holding, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.rename(right, left, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.rename(holding, right, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)


def test_private_json_create_renames_exclusively_without_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b'{"schema":"fixture/1","value":7}\n'
    rename_calls: list[tuple[int, str, str]] = []
    verify_calls = 0
    real_verify = A._verify_private_bytes_at

    def rename(parent_fd: int, source: str, destination: str) -> None:
        rename_calls.append((parent_fd, source, destination))
        _portable_rename_exclusive_at(parent_fd, source, destination)

    def verify(*args: object, **kwargs: object) -> tuple[int, int, int, int, int]:
        nonlocal verify_calls
        verify_calls += 1
        return real_verify(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(A, "_macos_rename_exclusive_at", rename)
    monkeypatch.setattr(A, "_verify_private_bytes_at", verify)
    monkeypatch.setattr(
        A.os,
        "link",
        lambda *_args, **_kwargs: pytest.fail("create must not use hard links"),
    )
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        digest = A._durable_atomic_private_file_at(
            parent_fd,
            "fixture.json",
            raw,
            replace_existing=False,
            expected_existing_identity=None,
            max_bytes=1024,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
        )
    finally:
        os.close(parent_fd)

    assert digest == A._sha256_tag(raw)
    assert (tmp_path / "fixture.json").read_bytes() == raw
    published = (tmp_path / "fixture.json").stat()
    assert stat.S_IMODE(published.st_mode) == 0o600
    assert published.st_uid == os.getuid()
    assert published.st_nlink == 1
    assert len(rename_calls) == 1
    assert rename_calls[0][2] == "fixture.json"
    assert verify_calls == 2
    assert list(tmp_path.glob(".fixture.json.*.tmp")) == []


def test_private_opaque_create_renames_exclusively_without_hardlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b"opaque private fixture"
    rename_calls: list[tuple[int, str, str]] = []
    read_calls = 0
    real_read = A._read_private_bytes_at

    def rename(parent_fd: int, source: str, destination: str) -> None:
        rename_calls.append((parent_fd, source, destination))
        _portable_rename_exclusive_at(parent_fd, source, destination)

    def read(*args: object, **kwargs: object):
        nonlocal read_calls
        read_calls += 1
        return real_read(*args, **kwargs)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A, "_macos_rename_exclusive_at", rename)
    monkeypatch.setattr(A, "_read_private_bytes_at", read)
    monkeypatch.setattr(
        A.os,
        "link",
        lambda *_args, **_kwargs: pytest.fail("create must not use hard links"),
    )
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        A._durable_atomic_private_bytes_at(
            parent_fd,
            "fixture.bin",
            raw,
            expected_existing=None,
            max_bytes=1024,
            artifact_label="opaque fixture",
        )
    finally:
        os.close(parent_fd)

    assert (tmp_path / "fixture.bin").read_bytes() == raw
    published = (tmp_path / "fixture.bin").stat()
    assert stat.S_IMODE(published.st_mode) == 0o600
    assert published.st_uid == os.getuid()
    assert published.st_nlink == 1
    assert len(rename_calls) == 1
    assert rename_calls[0][2] == "fixture.bin"
    assert read_calls == 2
    assert list(tmp_path.glob(".fixture.bin.*.tmp")) == []


@pytest.mark.parametrize("kind", ["json", "opaque"])
def test_private_create_destination_race_refuses_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    raced = b"preexisting race winner"

    def race(parent_fd: int, source: str, destination: str) -> None:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.write(descriptor, raced)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _portable_rename_exclusive_at(parent_fd, source, destination)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A, "_macos_rename_exclusive_at", race)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="temporary residue"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert (tmp_path / filename).read_bytes() == raced
    residues = list(tmp_path.glob(f".{filename}.*.tmp"))
    assert len(residues) == 1
    assert stat.S_IMODE(residues[0].stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["json", "opaque"])
def test_private_create_preexisting_destination_refuses_before_temp_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    destination = tmp_path / filename
    destination.write_bytes(b"preexisting")
    os.chmod(destination, 0o600)
    monkeypatch.setattr(A.sys, "platform", "darwin")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapStateError, match="already exists|precondition"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert destination.read_bytes() == b"preexisting"
    assert list(tmp_path.glob(f".{filename}.*.tmp")) == []


@pytest.mark.parametrize("kind", ["json", "opaque"])
@pytest.mark.parametrize("phase", ["before-parent-fsync", "after-parent-fsync"])
def test_private_create_verification_drift_leaves_residue_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    phase: str,
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    fail_on_call = 1 if phase == "before-parent-fsync" else 2
    verification_calls = 0
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A, "_macos_rename_exclusive_at", _portable_rename_exclusive_at
    )
    if kind == "json":
        real_verify = A._verify_private_bytes_at

        def verify(*args: object, **kwargs: object):
            nonlocal verification_calls
            verification_calls += 1
            if verification_calls == fail_on_call:
                os.chmod(tmp_path / filename, 0o640)
            return real_verify(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(A, "_verify_private_bytes_at", verify)
    else:
        real_read = A._read_private_bytes_at

        def read(*args: object, **kwargs: object):
            nonlocal verification_calls
            verification_calls += 1
            if verification_calls == fail_on_call:
                os.chmod(tmp_path / filename, 0o640)
            return real_read(*args, **kwargs)

        monkeypatch.setattr(A, "_read_private_bytes_at", read)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="verification"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert (tmp_path / filename).exists()
    assert stat.S_IMODE((tmp_path / filename).stat().st_mode) == 0o640
    assert verification_calls == fail_on_call
    assert list(tmp_path.glob(f".{filename}.*.tmp")) == []


@pytest.mark.parametrize("kind", ["json", "opaque"])
def test_private_create_parent_fsync_failure_leaves_final_for_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    real_fsync = A.os.fsync
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    parent_fsync_calls = 0

    def fsync(descriptor: int) -> None:
        nonlocal parent_fsync_calls
        if descriptor == parent_fd:
            parent_fsync_calls += 1
            if parent_fsync_calls == 1:
                raise OSError("injected parent fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A, "_macos_rename_exclusive_at", _portable_rename_exclusive_at
    )
    monkeypatch.setattr(A.os, "fsync", fsync)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="parent durability"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert parent_fsync_calls == 1
    assert (tmp_path / filename).exists()
    assert list(tmp_path.glob(f".{filename}.*.tmp")) == []


@pytest.mark.parametrize("kind", ["json", "opaque"])
@pytest.mark.parametrize(
    "error_type",
    [A.BootstrapStateError, RuntimeError, KeyboardInterrupt, SystemExit],
    ids=["domain", "runtime", "keyboard-interrupt", "system-exit"],
)
def test_private_create_wrapper_rename_then_raise_leaves_exact_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    error_type: type[BaseException],
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    raw = (
        b'{"schema":"fixture/1","value":7}\n'
        if kind == "json"
        else b"new private value"
    )

    def rename_then_raise(parent_fd: int, source: str, destination: str) -> None:
        _portable_rename_exclusive_at(parent_fd, source, destination)
        raise error_type("injected wrapper failure after rename")

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A, "_macos_rename_exclusive_at", rename_then_raise)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="may have committed"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    raw,
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    raw,
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert (tmp_path / filename).read_bytes() == raw
    assert list(tmp_path.glob(f".{filename}.*.tmp")) == []


@pytest.mark.parametrize("kind", ["json", "opaque"])
def test_private_create_temp_verification_drift_leaves_temp_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    real_fsync = A.os.fsync
    descriptor_fsync_calls = 0

    def fsync(descriptor: int) -> None:
        nonlocal descriptor_fsync_calls
        real_fsync(descriptor)
        if descriptor_fsync_calls == 0:
            descriptor_fsync_calls += 1
            os.fchmod(descriptor, 0o640)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A,
        "_macos_rename_exclusive_at",
        lambda *_args, **_kwargs: pytest.fail("drifted temp must not be renamed"),
    )
    monkeypatch.setattr(A.os, "fsync", fsync)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="temporary"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert not (tmp_path / filename).exists()
    residues = list(tmp_path.glob(f".{filename}.*.tmp"))
    assert len(residues) == 1
    assert stat.S_IMODE(residues[0].stat().st_mode) == 0o640


@pytest.mark.parametrize("kind", ["json", "opaque"])
@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, KeyboardInterrupt, SystemExit],
    ids=["runtime", "keyboard-interrupt", "system-exit"],
)
def test_private_create_temp_fsync_nonordinary_failure_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    error_type: type[BaseException],
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"

    def fsync(_descriptor: int) -> None:
        raise error_type("injected temporary fsync failure")

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "fsync", fsync)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="temporary residue"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    b'{"schema":"fixture/1","value":7}\n',
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    b"new private value",
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        os.close(parent_fd)

    assert not (tmp_path / filename).exists()
    residues = list(tmp_path.glob(f".{filename}.*.tmp"))
    assert len(residues) == 1
    assert stat.S_IMODE(residues[0].stat().st_mode) == 0o600


@pytest.mark.parametrize("kind", ["json", "opaque"])
def test_private_create_temp_descriptor_close_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    filename = "fixture.json" if kind == "json" else "fixture.bin"
    raw = (
        b'{"schema":"fixture/1","value":7}\n'
        if kind == "json"
        else b"new private value"
    )
    real_open = A.os.open
    real_close = A.os.close
    write_descriptor: int | None = None
    close_failed = False
    parent_fd = real_open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def open_tracked(
        name: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        nonlocal write_descriptor
        descriptor = real_open(name, flags, *args, **kwargs)  # type: ignore[arg-type]
        if (
            isinstance(name, str)
            and name.startswith(f".{filename}.")
            and flags & os.O_WRONLY
        ):
            write_descriptor = descriptor
        return descriptor

    def close_tracked(descriptor: int) -> None:
        nonlocal close_failed
        real_close(descriptor)
        if descriptor == write_descriptor and not close_failed:
            close_failed = True
            raise RuntimeError("injected temporary descriptor close failure")

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A, "_macos_rename_exclusive_at", _portable_rename_exclusive_at
    )
    monkeypatch.setattr(A.os, "open", open_tracked)
    monkeypatch.setattr(A.os, "close", close_tracked)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="descriptor close"):
            if kind == "json":
                A._durable_atomic_private_file_at(
                    parent_fd,
                    filename,
                    raw,
                    replace_existing=False,
                    expected_existing_identity=None,
                    max_bytes=1024,
                    validator=_private_fixture_validator,
                    artifact_label="private fixture",
                )
            else:
                A._durable_atomic_private_bytes_at(
                    parent_fd,
                    filename,
                    raw,
                    expected_existing=None,
                    max_bytes=1024,
                    artifact_label="opaque fixture",
                )
    finally:
        real_close(parent_fd)

    assert close_failed is True
    assert (tmp_path / filename).read_bytes() == raw


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS renameatx_np collision")
def test_macos_rename_exclusive_collision_preserves_both_names(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".fixture.tmp"
    destination = tmp_path / "fixture.json"
    source.write_bytes(b"source")
    destination.write_bytes(b"destination")
    os.chmod(source, 0o600)
    os.chmod(destination, 0o600)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapStateError):
            A._macos_rename_exclusive_at(
                parent_fd, source.name, destination.name
            )
    finally:
        os.close(parent_fd)

    assert source.read_bytes() == b"source"
    assert destination.read_bytes() == b"destination"


def test_exact_final_is_adopted_after_parent_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"schema": "fixture/1", "value": 7}
    raw = A._canonical_json_bytes(payload)
    closed = A.ClosedPrivateJson(
        filename="fixture.json",
        label="private fixture",
        max_bytes=1024,
        payload=payload,
        raw=raw,
        digest=A._sha256_tag(raw),
    )
    real_fsync = A.os.fsync
    parent_fsync_calls = 0
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def fsync(descriptor: int) -> None:
        nonlocal parent_fsync_calls
        if descriptor == parent_fd:
            parent_fsync_calls += 1
            if parent_fsync_calls == 1:
                raise OSError("injected parent fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A, "_macos_rename_exclusive_at", _portable_rename_exclusive_at
    )
    monkeypatch.setattr(A.os, "fsync", fsync)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="parent durability"):
            A._write_private_canonical_json_at(
                parent_fd,
                closed.filename,
                closed.payload,
                max_bytes=closed.max_bytes,
                validator=_private_fixture_validator,
                artifact_label=closed.label,
                replace_existing=False,
                expected_existing_digest=None,
            )
        assert A._create_or_verify_private_json_at(parent_fd, closed) == closed.digest
    finally:
        os.close(parent_fd)

    assert parent_fsync_calls == 1
    assert (tmp_path / closed.filename).read_bytes() == raw


def test_private_json_swap_rollback_with_retained_temp_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filename = "fixture.json"
    old_raw = b'{"schema":"fixture/1","value":7}\n'
    new_raw = b'{"schema":"fixture/1","value":8}\n'
    destination = tmp_path / filename
    destination.write_bytes(old_raw)
    os.chmod(destination, 0o600)
    expected_identity = A._stat_identity(destination.stat())
    swap_calls = 0

    def swap(parent_fd: int, left: str, right: str) -> None:
        nonlocal swap_calls
        swap_calls += 1
        _portable_swap_names_at(parent_fd, left, right)

    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A, "_macos_swap_names_at", swap)
    monkeypatch.setattr(
        A,
        "_verify_private_bytes_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A.BootstrapStateError("injected post-swap drift")
        ),
    )
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="retained temporary"):
            A._durable_atomic_private_file_at(
                parent_fd,
                filename,
                new_raw,
                replace_existing=True,
                expected_existing_identity=expected_identity,
                max_bytes=1024,
                validator=_private_fixture_validator,
                artifact_label="private fixture",
            )
    finally:
        os.close(parent_fd)

    assert swap_calls == 2
    assert destination.read_bytes() == old_raw
    residues = list(tmp_path.glob(f".{filename}.*.tmp"))
    assert len(residues) == 1
    assert residues[0].read_bytes() == new_raw


def test_generic_private_json_replacement_binds_digest_and_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"schema": "fixture/1", "value": 8}
    old_raw = b'{"schema":"fixture/1","value":7}\n'
    old_digest = A._sha256_tag(old_raw)
    identity = (1, 2, 3, 4, 5)
    observed: list[tuple[object, ...]] = []
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A,
        "_read_private_canonical_json_at",
        lambda *_args, **_kwargs: (
            {"schema": "fixture/1", "value": 7},
            identity,
            old_digest,
            old_raw,
        ),
    )

    def durable(*args: object, **kwargs: object) -> str:
        observed.append((args, kwargs))
        return "sha256:" + "a" * 64

    monkeypatch.setattr(A, "_durable_atomic_private_file_at", durable)
    result = A._write_private_canonical_json_at(
        11,
        "fixture.json",
        payload,
        max_bytes=1024,
        validator=_private_fixture_validator,
        artifact_label="private fixture",
        replace_existing=True,
        expected_existing_digest=old_digest,
    )
    assert result == "sha256:" + "a" * 64
    assert observed[0][1]["expected_existing_identity"] == identity
    with pytest.raises(A.BootstrapStateError, match="digest failed"):
        A._write_private_canonical_json_at(
            11,
            "fixture.json",
            payload,
            max_bytes=1024,
            validator=_private_fixture_validator,
            artifact_label="private fixture",
            replace_existing=True,
            expected_existing_digest="sha256:" + "0" * 64,
        )
    assert len(observed) == 1


class _FakeTreeStat:
    def __init__(self, node: "_FakeTreeNode") -> None:
        self.st_dev = 7
        self.st_ino = node.inode
        self.st_size = len(node.data) if node.kind == "file" else len(node.children)
        self.st_mtime_ns = node.version
        self.st_ctime_ns = node.version
        kind_mode = {
            "file": stat.S_IFREG,
            "directory": stat.S_IFDIR,
            "symlink": stat.S_IFLNK,
            "fifo": stat.S_IFIFO,
            "socket": stat.S_IFSOCK,
            "device": stat.S_IFCHR,
        }.get(node.kind, 0)
        self.st_mode = kind_mode | node.mode
        self.st_uid = node.uid
        self.st_nlink = node.nlink


class _FakeTreeNode:
    _next_inode = 100

    def __init__(
        self,
        label: str,
        *,
        kind: str,
        data: bytes = b"",
        mode: int | None = None,
        uid: int = 1000,
        nlink: int = 1,
    ) -> None:
        self.label = label
        self.kind = kind
        self.data = data
        self.mode = mode if mode is not None else (0o600 if kind == "file" else 0o700)
        self.uid = uid
        self.nlink = nlink
        self.children: dict[str, _FakeTreeNode] = {}
        self.version = 1
        self.inode = _FakeTreeNode._next_inode
        _FakeTreeNode._next_inode += 1


class _FakeTreeOps:
    def __init__(self, parent: _FakeTreeNode) -> None:
        self.nodes: dict[int, _FakeTreeNode] = {10: parent}
        self.offsets: dict[int, int] = {10: 0}
        self.path_nodes: dict[Path, _FakeTreeNode] = {}
        self.path_open_flags: list[int] = []
        self.next_fd = 20
        self.fsync_labels: list[str] = []
        self.unlinked_labels: list[str] = []
        self.renamed_labels: list[tuple[str, str]] = []
        self.closed_labels: list[str] = []
        self.on_fsync: object = None
        self.on_read: object = None
        self.fail_close_label: str | None = None

    def getuid(self) -> int:
        return 1000

    def open(self, name: str, _flags: int, *, dir_fd: int) -> int:
        parent = self.nodes[dir_fd]
        if name not in parent.children:
            if not (_flags & os.O_CREAT):
                raise FileNotFoundError(name)
            parent.children[name] = _FakeTreeNode(
                name, kind="file", mode=0o600, uid=self.getuid()
            )
        elif _flags & os.O_CREAT and _flags & os.O_EXCL:
            raise FileExistsError(name)
        node = parent.children[name]
        fd = self.next_fd
        self.next_fd += 1
        self.nodes[fd] = node
        self.offsets[fd] = 0
        return fd

    def open_path(self, path: Path, _flags: int) -> int:
        self.path_open_flags.append(_flags)
        try:
            node = self.path_nodes[Path(path)]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc
        fd = self.next_fd
        self.next_fd += 1
        self.nodes[fd] = node
        self.offsets[fd] = 0
        return fd

    def mkdir(self, name: str, mode: int, *, dir_fd: int) -> None:
        parent = self.nodes[dir_fd]
        if name in parent.children:
            raise FileExistsError(name)
        parent.children[name] = _FakeTreeNode(
            name, kind="directory", mode=mode, uid=self.getuid()
        )

    def fchmod(self, descriptor: int, mode: int) -> None:
        self.nodes[descriptor].mode = mode

    def unlink(self, name: str, *, dir_fd: int) -> None:
        parent = self.nodes[dir_fd]
        node = parent.children.pop(name)
        parent.version += 1
        self.unlinked_labels.append(node.label)

    def rename_exclusive(
        self, source: str, destination: str, *, dir_fd: int
    ) -> None:
        parent = self.nodes[dir_fd]
        if destination in parent.children:
            raise FileExistsError(destination)
        try:
            node = parent.children.pop(source)
        except KeyError as exc:
            raise FileNotFoundError(source) from exc
        parent.children[destination] = node
        parent.version += 1
        self.renamed_labels.append((source, destination))

    def fstat(self, descriptor: int) -> _FakeTreeStat:
        return _FakeTreeStat(self.nodes[descriptor])

    def stat(self, name: str, *, dir_fd: int) -> _FakeTreeStat:
        try:
            node = self.nodes[dir_fd].children[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        return _FakeTreeStat(node)

    def stat_path(self, path: Path) -> _FakeTreeStat:
        return _FakeTreeStat(self.path_nodes[Path(path)])

    def listdir(self, descriptor: int) -> list[str]:
        return list(self.nodes[descriptor].children)

    def read(self, descriptor: int, size: int) -> bytes:
        node = self.nodes[descriptor]
        start = self.offsets[descriptor]
        chunk = node.data[start : start + size]
        self.offsets[descriptor] += len(chunk)
        callback = self.on_read
        if callable(callback):
            callback(node, self)
        return chunk

    def write(self, descriptor: int, raw: bytes | memoryview) -> int:
        node = self.nodes[descriptor]
        value = bytes(raw)
        start = self.offsets[descriptor]
        node.data = node.data[:start] + value + node.data[start + len(value):]
        self.offsets[descriptor] += len(value)
        node.version += 1
        return len(value)

    def seek(self, descriptor: int, offset: int, whence: int) -> int:
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self.offsets[descriptor] + offset
        elif whence == os.SEEK_END:
            position = len(self.nodes[descriptor].data) + offset
        else:
            raise OSError("invalid synthetic seek")
        if position < 0:
            raise OSError("negative synthetic seek")
        self.offsets[descriptor] = position
        return position

    def fsync(self, descriptor: int) -> None:
        node = self.nodes[descriptor]
        self.fsync_labels.append(node.label)
        callback = self.on_fsync
        if callable(callback):
            callback(node, self)

    def close(self, descriptor: int) -> None:
        node = self.nodes.pop(descriptor)
        self.offsets.pop(descriptor)
        self.closed_labels.append(node.label)
        if node.label == self.fail_close_label:
            raise OSError("injected close failure")


def _fake_tree() -> tuple[
    _FakeTreeOps,
    A.ExpectedPrivateDirectory,
    _FakeTreeNode,
    _FakeTreeNode,
    _FakeTreeNode,
]:
    parent = _FakeTreeNode("parent", kind="directory")
    root = _FakeTreeNode("root", kind="directory")
    alpha = _FakeTreeNode("alpha", kind="file", data=b"alpha\n")
    nested = _FakeTreeNode("nested", kind="directory")
    beta = _FakeTreeNode("beta", kind="file", data=b"beta\n")
    nested.children["beta.txt"] = beta
    root.children.update({"nested": nested, "alpha.txt": alpha})
    parent.children["root"] = root
    expected = A.ExpectedPrivateDirectory(
        children={
            "nested": A.ExpectedPrivateDirectory(
                children={
                    "beta.txt": A.ExpectedPrivateFile(
                        byte_size=len(beta.data), sha256=A._sha256_tag(beta.data)
                    )
                }
            ),
            "alpha.txt": A.ExpectedPrivateFile(
                byte_size=len(alpha.data), sha256=A._sha256_tag(alpha.data)
            ),
        }
    )
    return _FakeTreeOps(parent), expected, root, alpha, beta


def _fake_staging(*, existing: bool) -> tuple[_FakeTreeOps, _FakeTreeNode]:
    parent = _FakeTreeNode("parent", kind="directory")
    if existing:
        parent.children["run.staging"] = _FakeTreeNode(
            "run.staging", kind="directory"
        )
    return _FakeTreeOps(parent), parent


class _FakeRows(list[tuple[object, ...]]):
    def fetchone(self) -> tuple[object, ...] | None:
        return self[0] if self else None


class _FakeSnapshotConnection:
    def __init__(
        self,
        role: str,
        node: _FakeTreeNode | None,
        event: object = None,
        bound_device_inode: tuple[int, int] | None = None,
    ) -> None:
        self.role = role
        self.node = node
        self.event = event
        self.bound_device_inode = bound_device_inode
        self.closed = False

    def _emit(self, name: str) -> None:
        if callable(self.event):
            self.event(f"{self.role}:{name}")

    def backup(self, destination: "_FakeSnapshotConnection", **_kwargs: object) -> None:
        assert destination.node is not None
        destination.node.data = b"S" * 4096
        destination.node.version += 1
        self._emit("backup")

    def commit(self) -> None:
        self._emit("commit")

    def execute(self, sql: str) -> _FakeRows:
        normalized = " ".join(sql.split()).lower()
        if normalized == "pragma journal_mode=delete":
            return _FakeRows([("delete",)])
        if normalized == "pragma quick_check":
            return _FakeRows([("ok",)])
        if normalized == "pragma page_size":
            return _FakeRows([(4096,)])
        if normalized == "pragma page_count":
            return _FakeRows([(1,)])
        if normalized in {"pragma user_version", "pragma application_id"}:
            return _FakeRows([(0,)])
        if normalized.startswith("select type, name, tbl_name"):
            return _FakeRows()
        if normalized.startswith("pragma table_xinfo"):
            return _FakeRows()
        raise AssertionError(f"unexpected synthetic SQLite query: {sql}")

    def close(self) -> None:
        self._emit("close")
        self.closed = True


def _fake_pinned_snapshot_environment() -> tuple[
    _FakeTreeOps,
    _FakeTreeNode,
    _FakeTreeNode,
    int,
    Path,
    Path,
]:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging_fd = ops.open("run.staging", os.O_RDONLY, dir_fd=10)
    staging_path = Path(
        "D:/Code-PC/ai-prose-baselines-private/run.staging"
    ).absolute()
    source_path = Path("D:/Code-PC/synthetic-chat.db").absolute()
    source = _FakeTreeNode("source", kind="file", data=b"source")
    ops.path_nodes.update(
        {
            staging_path.parent: parent,
            staging_path: staging,
            source_path: source,
        }
    )
    original_open = ops.open

    def bind_created_snapshot(name: str, flags: int, *, dir_fd: int) -> int:
        descriptor = original_open(name, flags, dir_fd=dir_fd)
        if name == A.SNAPSHOT_FILENAME:
            ops.path_nodes[staging_path / name] = ops.nodes[descriptor]
        return descriptor

    ops.open = bind_created_snapshot  # type: ignore[method-assign]
    return ops, parent, staging, staging_fd, staging_path, source_path


def _fake_snapshot_openers(
    ops: _FakeTreeOps,
    staging: _FakeTreeNode,
    *,
    event: object = None,
) -> tuple[object, object, object]:
    def source_opener(path: Path) -> _FakeSnapshotConnection:
        node = ops.path_nodes[path]
        return _FakeSnapshotConnection(
            "source", None, event, (7, node.inode)
        )

    def destination_opener(_path: Path) -> _FakeSnapshotConnection:
        node = staging.children[A.SNAPSHOT_FILENAME]
        return _FakeSnapshotConnection(
            "destination", node, event, (7, node.inode)
        )

    def snapshot_opener(_path: Path) -> _FakeSnapshotConnection:
        node = staging.children[A.SNAPSHOT_FILENAME]
        return _FakeSnapshotConnection(
            "snapshot", node, event, (7, node.inode)
        )

    return source_opener, destination_opener, snapshot_opener


def _fake_connection_binder(
    opener: object,
    path: Path,
    expected_device_inode: tuple[int, int],
    label: str,
) -> _FakeSnapshotConnection:
    connection = opener(path)  # type: ignore[operator]
    if connection.bound_device_inode != expected_device_inode:
        connection.close()
        raise A.SnapshotError(f"{label} connection inode is not pinned")
    return connection


def test_sqlite_connection_binding_requires_new_matching_process_fd() -> None:
    expected = (7, 700)
    snapshots = iter(
        [
            {1: (1, 1), 9: expected},
            {1: (1, 1), 9: expected, 12: expected},
        ]
    )
    connection = _FakeSnapshotConnection("bound", None)
    assert A._open_inode_bound_sqlite_connection(
        lambda _path: connection,
        Path("database.db"),
        expected,
        "synthetic SQLite",
        _fd_snapshot=lambda: next(snapshots),
    ) is connection
    assert not connection.closed


def test_sqlite_connection_binding_rejects_aba_replacement_and_closes() -> None:
    expected = (7, 700)
    replacement = (7, 701)
    snapshots = iter(
        [
            {1: (1, 1), 9: expected},
            {1: (1, 1), 9: expected, 12: replacement},
        ]
    )
    connection = _FakeSnapshotConnection("aba", None)
    with pytest.raises(A.SnapshotError, match="inode is not pinned"):
        A._open_inode_bound_sqlite_connection(
            lambda _path: connection,
            Path("database.db"),
            expected,
            "synthetic SQLite",
            _fd_snapshot=lambda: next(snapshots),
        )
    assert connection.closed


def test_staging_inventory_names_are_exact_validated_and_byte_sorted() -> None:
    assert A._closed_staging_inventory_names(["zeta", "alpha", "éclair"]) == (
        "alpha",
        "zeta",
        "éclair",
    )
    for invalid in (
        None,
        {"alpha"},
        ("alpha", 7),
        ("../escape",),
        ("alpha", "alpha"),
    ):
        with pytest.raises(A.BootstrapStateError, match="inventory"):
            A._closed_staging_inventory_names(invalid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "prefix_length", range(len(A.INITIALIZATION_DEPENDENCY_FILENAMES) + 1)
)
def test_initialization_staging_opener_accepts_only_fixed_prefixes(
    prefix_length: int,
) -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging.children[A.SNAPSHOT_FILENAME] = _FakeTreeNode(
        "snapshot", kind="file", data=b"snapshot"
    )
    expected_prefix = A.INITIALIZATION_DEPENDENCY_FILENAMES[:prefix_length]
    for name in expected_prefix:
        staging.children[name] = _FakeTreeNode(name, kind="file", data=b"{}\n")
    descriptor, identity, prefix, inventory = (
        A._open_private_staging_dependency_prefix_at(
            10, "run.staging", _ops=ops
        )
    )
    assert identity == A._private_node_identity(ops.fstat(descriptor))
    assert prefix == expected_prefix
    assert inventory == A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *expected_prefix)
    )
    ops.close(descriptor)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    "residue_names",
    [
        (A.RUN_CONTROLS_FILENAME,),
        (A.SEMANTIC_OPTIONS_FILENAME, A.SMOKE_POLICY_FILENAME),
        (A.RUN_OWNER_FILENAME,),
        ("unknown.json",),
    ],
)
def test_initialization_staging_opener_refuses_nonprefix_residue(
    residue_names: tuple[str, ...],
) -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging.children[A.SNAPSHOT_FILENAME] = _FakeTreeNode(
        "snapshot", kind="file", data=b"snapshot"
    )
    for name in residue_names:
        staging.children[name] = _FakeTreeNode(name, kind="file", data=b"{}\n")
    with pytest.raises(A.BootstrapStateError, match="authorized prefix"):
        A._open_private_staging_dependency_prefix_at(
            10, "run.staging", _ops=ops
        )
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("owner_present", [False, True])
def test_owner_stage_opener_accepts_only_optional_owner_residue(
    owner_present: bool,
) -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging.children[A.SNAPSHOT_FILENAME] = _FakeTreeNode(
        "snapshot", kind="file", data=b"snapshot"
    )
    for name in A.INITIALIZATION_DEPENDENCY_FILENAMES:
        staging.children[name] = _FakeTreeNode(name, kind="file", data=b"{}\n")
    if owner_present:
        staging.children[A.RUN_OWNER_FILENAME] = _FakeTreeNode(
            A.RUN_OWNER_FILENAME, kind="file", data=b"{}\n"
        )
    descriptor, identity, observed_owner, inventory = (
        A._open_private_staging_owner_stage_at(10, "run.staging", _ops=ops)
    )
    assert identity == A._private_node_identity(ops.fstat(descriptor))
    assert observed_owner is owner_present
    assert inventory == A._closed_staging_inventory_names(
        (
            A.SNAPSHOT_FILENAME,
            *A.INITIALIZATION_DEPENDENCY_FILENAMES,
            *((A.RUN_OWNER_FILENAME,) if owner_present else ()),
        )
    )
    ops.close(descriptor)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-dependency",
        "unknown",
        "owner-without-dependencies",
    ],
)
def test_owner_stage_opener_refuses_other_inventory(mutation: str) -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging.children[A.SNAPSHOT_FILENAME] = _FakeTreeNode(
        "snapshot", kind="file", data=b"snapshot"
    )
    if mutation == "owner-without-dependencies":
        staging.children[A.RUN_OWNER_FILENAME] = _FakeTreeNode(
            A.RUN_OWNER_FILENAME, kind="file", data=b"{}\n"
        )
    else:
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES:
            staging.children[name] = _FakeTreeNode(
                name, kind="file", data=b"{}\n"
            )
        if mutation == "missing-dependency":
            staging.children.pop(A.SMOKE_POLICY_FILENAME)
        else:
            staging.children["unknown.json"] = _FakeTreeNode(
                "unknown.json", kind="file", data=b"{}\n"
            )
    with pytest.raises(A.BootstrapStateError, match="owner residue inventory"):
        A._open_private_staging_owner_stage_at(10, "run.staging", _ops=ops)
    assert set(ops.nodes) == {10}


def test_create_private_staging_is_empty_durable_and_returns_pinned_fd() -> None:
    ops, parent = _fake_staging(existing=False)
    descriptor, identity = A._create_private_staging_at(
        10, "run.staging", _ops=ops
    )
    staging = parent.children["run.staging"]
    assert ops.nodes[descriptor] is staging
    assert identity == A._private_node_identity(_FakeTreeStat(staging))
    assert staging.mode == 0o700
    assert staging.children == {}
    assert ops.fsync_labels == ["run.staging", "parent"]
    assert ops.closed_labels == []
    ops.close(descriptor)


def test_open_private_staging_requires_exact_inventory_and_returns_pinned_fd() -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    staging.children.update(
        {
            "zeta.json": _FakeTreeNode("zeta", kind="file"),
            "alpha.db": _FakeTreeNode("alpha", kind="file"),
        }
    )
    descriptor, identity = A._open_private_staging_at(
        10,
        "run.staging",
        expected_names=["zeta.json", "alpha.db"],
        _ops=ops,
    )
    assert ops.nodes[descriptor] is staging
    assert identity == A._private_node_identity(_FakeTreeStat(staging))
    assert ops.closed_labels == []
    ops.close(descriptor)


@pytest.mark.parametrize("failure", ["extra", "wrong_mode", "replacement"])
def test_open_private_staging_refuses_drift_and_closes_descriptor(
    failure: str,
) -> None:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    expected_names: tuple[str, ...] = ()
    if failure == "extra":
        staging.children["alien"] = _FakeTreeNode("alien", kind="file")
    elif failure == "wrong_mode":
        staging.mode = 0o755
    else:
        original_open = ops.open

        def replace_after_open(name: str, flags: int, *, dir_fd: int) -> int:
            descriptor = original_open(name, flags, dir_fd=dir_fd)
            parent.children[name] = _FakeTreeNode(
                "replacement", kind="directory"
            )
            return descriptor

        ops.open = replace_after_open  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapStateError):
        A._open_private_staging_at(
            10,
            "run.staging",
            expected_names=expected_names,
            _ops=ops,
        )
    assert set(ops.nodes) == {10}
    assert ops.closed_labels == ["run.staging"]


def test_create_private_staging_open_failure_after_mkdir_requires_recovery() -> None:
    ops, parent = _fake_staging(existing=False)

    def fail_open(_name: str, _flags: int, *, dir_fd: int) -> int:
        raise OSError(f"synthetic open failure at {dir_fd}")

    ops.open = fail_open  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert "run.staging" in parent.children
    assert set(ops.nodes) == {10}


def test_create_private_staging_existing_name_refuses_without_mutation() -> None:
    ops, parent = _fake_staging(existing=True)
    existing = parent.children["run.staging"]
    with pytest.raises(A.BootstrapStateError, match="cannot create"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert parent.children["run.staging"] is existing
    assert set(ops.nodes) == {10}
    assert ops.fsync_labels == []
    assert ops.closed_labels == []


def test_create_private_staging_path_replacement_requires_recovery_and_close() -> None:
    ops, parent = _fake_staging(existing=False)
    original_open = ops.open

    def replace_after_open(name: str, flags: int, *, dir_fd: int) -> int:
        descriptor = original_open(name, flags, dir_fd=dir_fd)
        parent.children[name] = _FakeTreeNode("replacement", kind="directory")
        return descriptor

    ops.open = replace_after_open  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert parent.children["run.staging"].label == "replacement"
    assert set(ops.nodes) == {10}
    assert ops.closed_labels == ["run.staging"]


@pytest.mark.parametrize("failure", ["fchmod", "staging_fsync", "parent_fsync"])
def test_create_private_staging_post_open_failure_requires_recovery_and_close(
    failure: str,
) -> None:
    ops, _parent = _fake_staging(existing=False)
    if failure == "fchmod":
        ops.fchmod = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
            OSError("synthetic fchmod failure")
        )
    else:

        def fail_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
            target = "run.staging" if failure == "staging_fsync" else "parent"
            if node.label == target:
                raise OSError("synthetic fsync failure")

        ops.on_fsync = fail_fsync
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert set(ops.nodes) == {10}
    assert ops.closed_labels == ["run.staging"]


def test_create_private_staging_post_fsync_inventory_drift_requires_recovery() -> None:
    ops, parent = _fake_staging(existing=False)

    def mutate_after_parent_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is parent:
            parent.children["run.staging"].children["alien"] = _FakeTreeNode(
                "alien", kind="file"
            )

    ops.on_fsync = mutate_after_parent_fsync
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert set(ops.nodes) == {10}
    assert ops.closed_labels == ["run.staging"]


def test_create_private_staging_late_path_replacement_requires_recovery() -> None:
    ops, parent = _fake_staging(existing=False)
    original_fstat = ops.fstat
    calls = 0

    def replace_during_final_inventory(descriptor: int) -> _FakeTreeStat:
        nonlocal calls
        calls += 1
        result = original_fstat(descriptor)
        if calls == 5:
            parent.children["run.staging"] = _FakeTreeNode(
                "late-replacement", kind="directory"
            )
        return result

    ops.fstat = replace_during_final_inventory  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._create_private_staging_at(10, "run.staging", _ops=ops)
    assert parent.children["run.staging"].label == "late-replacement"
    assert set(ops.nodes) == {10}
    assert ops.closed_labels == ["run.staging"]


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_live_private_staging_refuses_non_macos_before_io() -> None:
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A._create_private_staging_at(10, "run.staging")
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A._open_private_staging_at(
            10, "run.staging", expected_names=()
        )


def _fake_partial_snapshot(
    names: tuple[str, ...],
) -> tuple[_FakeTreeOps, _FakeTreeNode, int]:
    ops, parent = _fake_staging(existing=True)
    staging = parent.children["run.staging"]
    for name in names:
        staging.children[name] = _FakeTreeNode(name, kind="file")
    staging_fd = ops.open("run.staging", os.O_RDONLY, dir_fd=10)
    return ops, staging, staging_fd


def _reset_fake_partial_snapshot(
    ops: _FakeTreeOps,
    staging: _FakeTreeNode,
    staging_fd: int,
) -> tuple[str, ...]:
    return A._reset_recognized_snapshot_in_progress_at(
        10,
        staging_fd,
        "run.staging",
        expected_staging_device_inode=(7, staging.inode),
        _ops=ops,
    )


def test_snapshot_in_progress_reset_accepts_empty_without_mutation() -> None:
    ops, staging, staging_fd = _fake_partial_snapshot(())
    assert _reset_fake_partial_snapshot(ops, staging, staging_fd) == ()
    assert staging.children == {}
    assert ops.unlinked_labels == []
    assert ops.fsync_labels == []


def test_snapshot_in_progress_reset_durably_removes_only_recognized_files() -> None:
    names = tuple(A.SNAPSHOT_PARTIAL_FILENAMES)
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    removed = _reset_fake_partial_snapshot(ops, staging, staging_fd)
    expected = (
        A.SNAPSHOT_FILENAME + "-journal",
        A.SNAPSHOT_FILENAME + "-shm",
        A.SNAPSHOT_FILENAME + "-wal",
        A.SNAPSHOT_FILENAME,
    )
    assert removed == expected
    assert tuple(ops.unlinked_labels) == expected
    assert staging.children == {}
    assert ops.fsync_labels == ["run.staging"] * len(expected)


def test_snapshot_in_progress_reset_unknown_name_refuses_untouched() -> None:
    names = (A.SNAPSHOT_FILENAME, "foreign-private-file")
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    before = dict(staging.children)
    with pytest.raises(A.BootstrapStateError, match="unknown name"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert staging.children == before
    assert ops.unlinked_labels == []
    assert ops.fsync_labels == []


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("mode", 0o644),
        ("uid", 1001),
        ("nlink", 2),
        ("kind", "directory"),
        ("kind", "symlink"),
        ("kind", "fifo"),
        ("kind", "socket"),
        ("kind", "device"),
    ],
)
def test_snapshot_in_progress_reset_malformed_file_refuses_before_unlink(
    attribute: str,
    value: object,
) -> None:
    names = (A.SNAPSHOT_FILENAME, A.SNAPSHOT_FILENAME + "-wal")
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    setattr(staging.children[A.SNAPSHOT_FILENAME + "-wal"], attribute, value)
    before = dict(staging.children)
    with pytest.raises(A.BootstrapStateError):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert staging.children == before
    assert ops.unlinked_labels == []


def test_snapshot_in_progress_reset_fsync_failure_preserves_recognized_subset() -> None:
    names = (A.SNAPSHOT_FILENAME, A.SNAPSHOT_FILENAME + "-wal")
    ops, staging, staging_fd = _fake_partial_snapshot(names)

    def fail_first_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is staging:
            raise OSError("synthetic partial cleanup fsync failure")

    ops.on_fsync = fail_first_fsync
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert ops.unlinked_labels == [A.SNAPSHOT_FILENAME + "-wal"]
    assert tuple(staging.children) == (A.SNAPSHOT_FILENAME,)


def test_snapshot_in_progress_reset_preflight_oserror_is_normalized() -> None:
    ops, staging, staging_fd = _fake_partial_snapshot((A.SNAPSHOT_FILENAME,))
    ops.listdir = lambda _fd: (_ for _ in ()).throw(  # type: ignore[method-assign]
        OSError("synthetic list failure")
    )
    with pytest.raises(A.BootstrapStateError, match="cannot validate"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert tuple(staging.children) == (A.SNAPSHOT_FILENAME,)
    assert ops.unlinked_labels == []


def test_snapshot_in_progress_reset_refuses_replaced_staging_name_untouched() -> None:
    ops, staging, staging_fd = _fake_partial_snapshot((A.SNAPSHOT_FILENAME,))
    replacement = _FakeTreeNode("replacement-staging", kind="directory")
    ops.nodes[10].children["run.staging"] = replacement
    with pytest.raises(A.BootstrapStateError, match="pathname drifted"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert tuple(staging.children) == (A.SNAPSHOT_FILENAME,)
    assert replacement.children == {}
    assert ops.unlinked_labels == []


def test_snapshot_in_progress_reset_name_replacement_after_unlink_requires_recovery() -> None:
    names = (A.SNAPSHOT_FILENAME, A.SNAPSHOT_FILENAME + "-wal")
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    parent = ops.nodes[10]
    replacement = _FakeTreeNode("replacement-staging", kind="directory")

    def replace_after_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is staging:
            parent.children["run.staging"] = replacement

    ops.on_fsync = replace_after_fsync
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert ops.unlinked_labels == [A.SNAPSHOT_FILENAME + "-wal"]
    assert tuple(staging.children) == (A.SNAPSHOT_FILENAME,)
    assert replacement.children == {}


@pytest.mark.parametrize("fail_after_unlink", [False, True])
def test_snapshot_in_progress_reset_unlink_boundary_restarts_to_empty(
    fail_after_unlink: bool,
) -> None:
    names = (A.SNAPSHOT_FILENAME, A.SNAPSHOT_FILENAME + "-wal")
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    original_unlink = ops.unlink
    failed = False

    def fail_once(name: str, *, dir_fd: int) -> None:
        nonlocal failed
        if not failed:
            failed = True
            if fail_after_unlink:
                original_unlink(name, dir_fd=dir_fd)
            raise OSError("synthetic unlink boundary failure")
        original_unlink(name, dir_fd=dir_fd)

    ops.unlink = fail_once  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    ops.unlink = original_unlink  # type: ignore[method-assign]
    _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert staging.children == {}


@pytest.mark.parametrize("fsync_ordinal", [1, 2, 3, 4])
def test_snapshot_in_progress_reset_each_durable_subset_restarts_to_empty(
    fsync_ordinal: int,
) -> None:
    ops, staging, staging_fd = _fake_partial_snapshot(
        tuple(A.SNAPSHOT_PARTIAL_FILENAMES)
    )
    fsyncs = 0

    def fail_at_boundary(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal fsyncs
        if node is staging:
            fsyncs += 1
            if fsyncs == fsync_ordinal:
                raise OSError("synthetic durable subset boundary")

    ops.on_fsync = fail_at_boundary
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert set(staging.children).issubset(set(A.SNAPSHOT_PARTIAL_FILENAMES))
    ops.on_fsync = None
    _reset_fake_partial_snapshot(ops, staging, staging_fd)
    assert staging.children == {}


@pytest.mark.parametrize("fail_on_callback", [1, 2])
def test_snapshot_in_progress_reset_rechecks_lock_before_each_unlink(
    fail_on_callback: int,
) -> None:
    names = (A.SNAPSHOT_FILENAME, A.SNAPSHOT_FILENAME + "-wal")
    ops, staging, staging_fd = _fake_partial_snapshot(names)
    callbacks = 0

    def verify_lock() -> None:
        nonlocal callbacks
        callbacks += 1
        if callbacks == fail_on_callback:
            raise A.BootstrapStateError("synthetic held-lock drift")

    expected_error = (
        A.BootstrapStateError
        if fail_on_callback == 1
        else A.BootstrapRecoveryRequired
    )
    with pytest.raises(expected_error):
        A._reset_recognized_snapshot_in_progress_at(
            10,
            staging_fd,
            "run.staging",
            expected_staging_device_inode=(7, staging.inode),
            _before_unlink=verify_lock,
            _ops=ops,
        )
    if fail_on_callback == 1:
        assert set(staging.children) == set(names)
    else:
        assert set(staging.children) == {A.SNAPSHOT_FILENAME}


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_live_snapshot_in_progress_reset_refuses_non_macos() -> None:
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A._reset_recognized_snapshot_in_progress_at(
            9,
            10,
            "run.staging",
            expected_staging_device_inode=(7, 701),
        )


def _real_exact_snapshot_reuse_environment(
    tmp_path: Path,
) -> tuple[Path, Path, int, int, tuple[int, int]]:
    _private_root, run_dir, _receipt = _completed_private_smoke_run(tmp_path)
    source = run_dir / A.SNAPSHOT_FILENAME
    output_root = run_dir.parent
    staging = output_root / "exact-reuse.staging"
    staging.mkdir(mode=0o700)
    os.chmod(staging, 0o700)
    parent_fd = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
    staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
    return source, staging, parent_fd, staging_fd, A._device_inode(staging.stat())


def test_closed_snapshot_reuse_preserves_exact_hash_and_policy_digest(
    tmp_path: Path,
) -> None:
    source, staging, parent_fd, staging_fd, staging_inode = (
        _real_exact_snapshot_reuse_environment(tmp_path)
    )
    source_raw = source.read_bytes()
    smoke = json.loads((source.parent / A.SMOKE_POLICY_FILENAME).read_bytes())
    try:
        evidence = A._materialize_consistent_snapshot_in_precreated_staging_at(
            parent_fd,
            staging_fd,
            staging.name,
            staging,
            source,
            expected_staging_device_inode=staging_inode,
            _ops=A._PrivateTreeOsOps(),
        )
    finally:
        os.close(staging_fd)
        os.close(parent_fd)

    copied = staging / A.SNAPSHOT_FILENAME
    assert copied.read_bytes() == source_raw
    assert evidence.metadata.file_sha256 == A._sha256_tag(source_raw)
    assert evidence.metadata.file_sha256 == smoke["snapshot_metadata"]["file_sha256"]
    assert evidence.metadata.byte_size == len(source_raw)


def test_arbitrary_same_named_database_refuses_before_destination_mutation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "arbitrary-source"
    source_root.mkdir(mode=0o700)
    source = source_root / A.SNAPSHOT_FILENAME
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE fixture(value TEXT)")
    connection.commit()
    connection.close()
    os.chmod(source, 0o600)
    staging_parent = tmp_path / "destination"
    staging_parent.mkdir(mode=0o700)
    staging = staging_parent / "arbitrary.staging"
    staging.mkdir(mode=0o700)
    parent_fd = os.open(staging_parent, os.O_RDONLY | os.O_DIRECTORY)
    staging_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(A.SnapshotError, match="closed atomic source run"):
            A._materialize_consistent_snapshot_in_precreated_staging_at(
                parent_fd,
                staging_fd,
                staging.name,
                staging,
                source,
                expected_staging_device_inode=A._device_inode(staging.stat()),
                _ops=A._PrivateTreeOsOps(),
            )
    finally:
        os.close(staging_fd)
        os.close(parent_fd)
    assert tuple(staging.iterdir()) == ()


class _ExactReuseFaultOps(A._PrivateTreeOsOps):
    def __init__(
        self,
        source: Path,
        *,
        mutate_source_during_copy: bool = False,
        drift_destination_after_fsync: bool = False,
        short_writes: bool = False,
    ) -> None:
        self.source = source
        self.source_inode = A._device_inode(source.stat())
        self.mutate_source_during_copy = mutate_source_during_copy
        self.drift_destination_after_fsync = drift_destination_after_fsync
        self.short_writes = short_writes
        self.source_seek_count = 0
        self.source_mutated = False
        self.destination_drifted = False

    def seek(self, descriptor: int, offset: int, whence: int) -> int:
        if A._device_inode(os.fstat(descriptor)) == self.source_inode and offset == 0:
            self.source_seek_count += 1
        return super().seek(descriptor, offset, whence)

    def read(self, descriptor: int, size: int) -> bytes:
        raw = super().read(descriptor, size)
        if (
            raw
            and self.mutate_source_during_copy
            and self.source_seek_count == 2
            and not self.source_mutated
        ):
            with self.source.open("ab") as handle:
                handle.write(b"drift")
            self.source_mutated = True
        return raw

    def write(self, descriptor: int, raw: bytes | memoryview) -> int:
        if self.short_writes and len(raw) > 7:
            return os.write(descriptor, raw[:7])
        return super().write(descriptor, raw)

    def fsync(self, descriptor: int) -> None:
        super().fsync(descriptor)
        info = os.fstat(descriptor)
        if (
            self.drift_destination_after_fsync
            and stat.S_ISREG(info.st_mode)
            and A._device_inode(info) != self.source_inode
            and not self.destination_drifted
        ):
            os.lseek(descriptor, 0, os.SEEK_END)
            os.write(descriptor, b"drift")
            self.destination_drifted = True


@pytest.mark.parametrize("failure", ["source-mutation", "destination-drift"])
def test_closed_snapshot_reuse_copy_drift_requires_recovery(
    tmp_path: Path, failure: str
) -> None:
    source, staging, parent_fd, staging_fd, staging_inode = (
        _real_exact_snapshot_reuse_environment(tmp_path)
    )
    ops = _ExactReuseFaultOps(
        source,
        mutate_source_during_copy=failure == "source-mutation",
        drift_destination_after_fsync=failure == "destination-drift",
    )
    try:
        with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
            A._materialize_consistent_snapshot_in_precreated_staging_at(
                parent_fd,
                staging_fd,
                staging.name,
                staging,
                source,
                expected_staging_device_inode=staging_inode,
                _ops=ops,
            )
    finally:
        os.close(staging_fd)
        os.close(parent_fd)
    assert (staging / A.SNAPSHOT_FILENAME).exists()


def test_closed_snapshot_reuse_handles_short_destination_writes(
    tmp_path: Path,
) -> None:
    source, staging, parent_fd, staging_fd, staging_inode = (
        _real_exact_snapshot_reuse_environment(tmp_path)
    )
    source_hash = A._sha256_tag(source.read_bytes())
    try:
        evidence = A._materialize_consistent_snapshot_in_precreated_staging_at(
            parent_fd,
            staging_fd,
            staging.name,
            staging,
            source,
            expected_staging_device_inode=staging_inode,
            _ops=_ExactReuseFaultOps(source, short_writes=True),
        )
    finally:
        os.close(staging_fd)
        os.close(parent_fd)
    assert evidence.metadata.file_sha256 == source_hash


def test_pinned_snapshot_materialization_closes_exact_durable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.os, "O_NONBLOCK", 0x40000000, raising=False)
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging
    )
    evidence = A._materialize_consistent_snapshot_in_precreated_staging_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        source_path,
        expected_staging_device_inode=(7, staging.inode),
        _ops=ops,
        _source_opener=source_opener,  # type: ignore[arg-type]
        _destination_opener=destination_opener,  # type: ignore[arg-type]
        _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
        _connection_binder=_fake_connection_binder,
    )
    snapshot = staging.children[A.SNAPSHOT_FILENAME]
    assert evidence.inventory == (A.SNAPSHOT_FILENAME,)
    assert evidence.snapshot_device_inode == (7, snapshot.inode)
    assert evidence.staging_device_inode == (7, staging.inode)
    assert evidence.metadata.byte_size == 4096
    assert evidence.metadata.file_sha256 == A._sha256_tag(b"S" * 4096)
    assert ops.fsync_labels == [A.SNAPSHOT_FILENAME, "run.staging"]
    assert ops.path_open_flags[0] & 0x40000000
    assert set(ops.nodes) == {10, staging_fd}
    assert sorted(ops.closed_labels) == ["source", A.SNAPSHOT_FILENAME]


@pytest.mark.parametrize(
    "race",
    [
        "staging_replaced_during_destination_open",
        "snapshot_replaced_during_destination_open",
        "source_replaced_during_source_open",
        "source_aba_during_source_open",
        "snapshot_aba_during_destination_open",
        "snapshot_aba_during_verifier_open",
        "snapshot_replaced_after_backup",
        "sidecar_added_after_backup",
        "hardlink_count_changed_after_backup",
        "bytes_mutated_after_verifier_close",
    ],
)
def test_pinned_snapshot_materialization_races_require_locked_recovery(
    race: str,
) -> None:
    ops, parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )

    def event(name: str) -> None:
        if race == "snapshot_replaced_after_backup" and name == "source:backup":
            replacement = _FakeTreeNode(
                "snapshot-replacement", kind="file", data=b"S" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
        elif race == "sidecar_added_after_backup" and name == "source:backup":
            staging.children[A.SNAPSHOT_FILENAME + "-wal"] = _FakeTreeNode(
                "sidecar", kind="file"
            )
        elif (
            race == "hardlink_count_changed_after_backup"
            and name == "source:backup"
        ):
            staging.children[A.SNAPSHOT_FILENAME].nlink = 2
        elif (
            race == "bytes_mutated_after_verifier_close"
            and name == "snapshot:close"
        ):
            pinned = next(
                node
                for node in ops.nodes.values()
                if node.label == A.SNAPSHOT_FILENAME
            )
            pinned.data += b"drift"
            pinned.version += 1

    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging, event=event
    )
    if race == "staging_replaced_during_destination_open":
        original_destination_opener = destination_opener

        def replace_staging(path: Path) -> object:
            result = original_destination_opener(path)  # type: ignore[operator]
            replacement = _FakeTreeNode("staging-replacement", kind="directory")
            replacement.children = dict(staging.children)
            parent.children["run.staging"] = replacement
            ops.path_nodes[staging_path] = replacement
            return result

        destination_opener = replace_staging
    elif race == "snapshot_replaced_during_destination_open":
        original_destination_opener = destination_opener

        def replace_snapshot(path: Path) -> object:
            result = original_destination_opener(path)  # type: ignore[operator]
            replacement = _FakeTreeNode(
                "snapshot-open-replacement", kind="file", data=b"S" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
            return result

        destination_opener = replace_snapshot
    elif race == "source_replaced_during_source_open":
        original_source_opener = source_opener

        def replace_source(path: Path) -> object:
            result = original_source_opener(path)  # type: ignore[operator]
            ops.path_nodes[source_path] = _FakeTreeNode(
                "source-replacement", kind="file", data=b"other-source"
            )
            return result

        source_opener = replace_source
    elif race == "source_aba_during_source_open":
        original_source_opener = source_opener

        def aba_source(path: Path) -> object:
            original = ops.path_nodes[source_path]
            replacement = _FakeTreeNode(
                "source-aba", kind="file", data=b"other-source"
            )
            ops.path_nodes[source_path] = replacement
            result = original_source_opener(path)  # type: ignore[operator]
            ops.path_nodes[source_path] = original
            return result

        source_opener = aba_source
    elif race == "snapshot_aba_during_destination_open":
        original_destination_opener = destination_opener

        def aba_destination(path: Path) -> object:
            original = staging.children[A.SNAPSHOT_FILENAME]
            replacement = _FakeTreeNode(
                "destination-aba", kind="file", data=b"A" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
            result = original_destination_opener(path)  # type: ignore[operator]
            staging.children[A.SNAPSHOT_FILENAME] = original
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = original
            return result

        destination_opener = aba_destination
    elif race == "snapshot_aba_during_verifier_open":
        original_snapshot_opener = snapshot_opener

        def aba_verifier(path: Path) -> object:
            original = staging.children[A.SNAPSHOT_FILENAME]
            replacement = _FakeTreeNode(
                "verifier-aba", kind="file", data=b"V" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
            result = original_snapshot_opener(path)  # type: ignore[operator]
            staging.children[A.SNAPSHOT_FILENAME] = original
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = original
            return result

        snapshot_opener = aba_verifier
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _source_opener=source_opener,  # type: ignore[arg-type]
            _destination_opener=destination_opener,  # type: ignore[arg-type]
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    assert A.SNAPSHOT_FILENAME in ops.closed_labels
    assert "source" in ops.closed_labels


@pytest.mark.parametrize("failure", ["snapshot_fsync", "staging_fsync"])
def test_pinned_snapshot_fsync_failure_requires_recovery_and_closes_fds(
    failure: str,
) -> None:
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging
    )

    def fail_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        target = A.SNAPSHOT_FILENAME if failure == "snapshot_fsync" else "run.staging"
        if node.label == target:
            raise OSError("synthetic snapshot fsync failure")

    ops.on_fsync = fail_fsync
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _source_opener=source_opener,  # type: ignore[arg-type]
            _destination_opener=destination_opener,  # type: ignore[arg-type]
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    assert sorted(ops.closed_labels) == ["source", A.SNAPSHOT_FILENAME]


def test_pinned_snapshot_mutation_during_hash_requires_recovery() -> None:
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging
    )
    mutated = False

    def mutate_during_read(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal mutated
        if node.label == A.SNAPSHOT_FILENAME and not mutated:
            mutated = True
            node.data += b"drift"
            node.version += 1

    ops.on_read = mutate_during_read
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _source_opener=source_opener,  # type: ignore[arg-type]
            _destination_opener=destination_opener,  # type: ignore[arg-type]
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert mutated
    assert set(ops.nodes) == {10, staging_fd}


@pytest.mark.parametrize("late_race", ["sidecar_after_staging_fsync", "staging_replaced_during_hash"])
def test_pinned_snapshot_late_staging_races_require_recovery(
    late_race: str,
) -> None:
    ops, parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging
    )
    injected = False

    def inject_after_fsync(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal injected
        if late_race == "sidecar_after_staging_fsync" and node is staging:
            staging.children[A.SNAPSHOT_FILENAME + "-shm"] = _FakeTreeNode(
                "late-sidecar", kind="file"
            )
            injected = True

    def inject_during_hash(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal injected
        if (
            late_race == "staging_replaced_during_hash"
            and node.label == A.SNAPSHOT_FILENAME
            and not injected
        ):
            replacement = _FakeTreeNode("late-staging", kind="directory")
            replacement.children = dict(staging.children)
            parent.children["run.staging"] = replacement
            ops.path_nodes[staging_path] = replacement
            injected = True

    ops.on_fsync = inject_after_fsync
    ops.on_read = inject_during_hash
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _source_opener=source_opener,  # type: ignore[arg-type]
            _destination_opener=destination_opener,  # type: ignore[arg-type]
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert injected
    assert set(ops.nodes) == {10, staging_fd}


def test_pinned_snapshot_descriptor_close_failure_still_closes_source() -> None:
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    source_opener, destination_opener, snapshot_opener = _fake_snapshot_openers(
        ops, staging
    )
    ops.fail_close_label = A.SNAPSHOT_FILENAME
    with pytest.raises(A.BootstrapRecoveryRequired, match="close requires recovery"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _source_opener=source_opener,  # type: ignore[arg-type]
            _destination_opener=destination_opener,  # type: ignore[arg-type]
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    assert sorted(ops.closed_labels) == ["source", A.SNAPSHOT_FILENAME]


def test_pinned_snapshot_source_failure_before_create_is_not_recovery() -> None:
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    ops.path_nodes.pop(source_path)
    with pytest.raises(A.SnapshotError, match="cannot begin"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
        )
    assert staging.children == {}
    assert set(ops.nodes) == {10, staging_fd}


def test_pinned_snapshot_source_wrong_kind_refuses_before_create() -> None:
    ops, _parent, staging, staging_fd, staging_path, source_path = (
        _fake_pinned_snapshot_environment()
    )
    ops.path_nodes[source_path].kind = "directory"
    with pytest.raises(A.SnapshotError, match="not a regular file"):
        A._materialize_consistent_snapshot_in_precreated_staging_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            source_path,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
        )
    assert staging.children == {}
    assert set(ops.nodes) == {10, staging_fd}
    assert ops.closed_labels == ["source"]


def _fake_closed_snapshot_environment() -> tuple[
    _FakeTreeOps,
    _FakeTreeNode,
    int,
    Path,
    A.ClosedSnapshotEvidence,
]:
    ops, _parent, staging, staging_fd, staging_path, _source_path = (
        _fake_pinned_snapshot_environment()
    )
    snapshot = _FakeTreeNode(
        A.SNAPSHOT_FILENAME, kind="file", data=b"S" * 4096
    )
    staging.children[A.SNAPSHOT_FILENAME] = snapshot
    ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = snapshot
    connection = _FakeSnapshotConnection(
        "metadata", snapshot, bound_device_inode=(7, snapshot.inode)
    )
    metadata = A._snapshot_metadata_from_hash(
        connection,
        file_hash=A._sha256_tag(snapshot.data),
        byte_size=len(snapshot.data),
    )
    return ops, staging, staging_fd, staging_path, metadata


def test_existing_closed_snapshot_revalidates_exact_pinned_evidence() -> None:
    ops, staging, staging_fd, staging_path, metadata = (
        _fake_closed_snapshot_environment()
    )
    _source, _destination, snapshot_opener = _fake_snapshot_openers(ops, staging)
    evidence = A._verify_existing_closed_snapshot_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        metadata,
        expected_staging_device_inode=(7, staging.inode),
        _ops=ops,
        _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
        _connection_binder=_fake_connection_binder,
    )
    assert evidence.metadata == metadata
    assert evidence.inventory == (A.SNAPSHOT_FILENAME,)
    assert evidence.snapshot_identity[1] == staging.children[
        A.SNAPSHOT_FILENAME
    ].inode
    assert ops.fsync_labels == [A.SNAPSHOT_FILENAME, "run.staging"]
    assert set(ops.nodes) == {10, staging_fd}
    assert ops.closed_labels == [A.SNAPSHOT_FILENAME]


@pytest.mark.parametrize(
    "failure",
    [
        "extra_sidecar",
        "persistent_replacement",
        "aba_replacement",
        "bytes_after_close",
        "metadata_mismatch",
    ],
)
def test_existing_closed_snapshot_drift_refuses(
    failure: str,
) -> None:
    ops, staging, staging_fd, staging_path, metadata = (
        _fake_closed_snapshot_environment()
    )

    def event(name: str) -> None:
        if failure == "bytes_after_close" and name == "snapshot:close":
            staging.children[A.SNAPSHOT_FILENAME].data += b"drift"
            staging.children[A.SNAPSHOT_FILENAME].version += 1

    _source, _destination, snapshot_opener = _fake_snapshot_openers(
        ops, staging, event=event
    )
    if failure == "extra_sidecar":
        staging.children[A.SNAPSHOT_FILENAME + "-wal"] = _FakeTreeNode(
            "unexpected-sidecar", kind="file"
        )
    elif failure == "persistent_replacement":
        original = snapshot_opener

        def replace_snapshot(path: Path) -> object:
            result = original(path)  # type: ignore[operator]
            replacement = _FakeTreeNode(
                "closed-replacement", kind="file", data=b"S" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
            return result

        snapshot_opener = replace_snapshot
    elif failure == "aba_replacement":
        original = snapshot_opener

        def aba(path: Path) -> object:
            pinned = staging.children[A.SNAPSHOT_FILENAME]
            replacement = _FakeTreeNode(
                "closed-aba", kind="file", data=b"S" * 4096
            )
            staging.children[A.SNAPSHOT_FILENAME] = replacement
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = replacement
            result = original(path)  # type: ignore[operator]
            staging.children[A.SNAPSHOT_FILENAME] = pinned
            ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = pinned
            return result

        snapshot_opener = aba
    elif failure == "metadata_mismatch":
        metadata = replace(metadata, sqlite_user_version=metadata.sqlite_user_version + 1)
    with pytest.raises(A.BootstrapStateError):
        A._verify_existing_closed_snapshot_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            metadata,
            expected_staging_device_inode=(7, staging.inode),
            _ops=ops,
            _snapshot_opener=snapshot_opener,  # type: ignore[arg-type]
            _connection_binder=_fake_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS descriptor binding")
def test_live_pinned_snapshot_binds_fds_and_committed_wal(tmp_path: Path) -> None:
    source = tmp_path / "live-chat.db"
    writer = sqlite3.connect(source)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    writer.execute("PRAGMA wal_autocheckpoint=0")
    _atomic_schema(writer)
    writer.execute("INSERT INTO chat VALUES (1, 'guid', 'alias', NULL, 45)")
    writer.commit()
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    staging = private_root / "run.staging"
    staging.mkdir(mode=0o700)
    os.chmod(staging, 0o700)
    parent_fd = os.open(
        private_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    staging_fd = os.open(
        "run.staging",
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    try:
        opened = os.fstat(staging_fd)
        evidence = A._materialize_consistent_snapshot_in_precreated_staging_at(
            parent_fd,
            staging_fd,
            "run.staging",
            staging,
            source,
            expected_staging_device_inode=(opened.st_dev, opened.st_ino),
        )
        verified = A._verify_existing_closed_snapshot_at(
            parent_fd,
            staging_fd,
            "run.staging",
            staging,
            evidence.metadata,
            expected_staging_device_inode=(opened.st_dev, opened.st_ino),
        )
    finally:
        os.close(staging_fd)
        os.close(parent_fd)
        writer.close()
    assert evidence.inventory == (A.SNAPSHOT_FILENAME,)
    assert verified.metadata == evidence.metadata
    snapshot = sqlite3.connect(
        (staging / A.SNAPSHOT_FILENAME).resolve().as_uri() + "?mode=ro",
        uri=True,
    )
    try:
        assert snapshot.execute("SELECT guid FROM chat").fetchall() == [("guid",)]
        assert snapshot.execute("PRAGMA quick_check").fetchall() == [("ok",)]
    finally:
        snapshot.close()


def _closed_snapshot_fixture() -> A.ClosedSnapshotEvidence:
    metadata = A.SnapshotMetadata(**_bootstrap_snapshot_payload())
    snapshot_identity = (
        7,
        702,
        metadata.byte_size,
        2,
        2,
        stat.S_IFREG | 0o600,
        1000,
        1,
    )
    staging_identity = (
        7,
        701,
        1,
        2,
        2,
        stat.S_IFDIR | 0o700,
        1000,
        1,
    )
    return A.ClosedSnapshotEvidence(
        metadata=metadata,
        snapshot_identity=snapshot_identity,
        staging_identity=staging_identity,
        snapshot_device_inode=(7, 702),
        staging_device_inode=(7, 701),
        inventory=(A.SNAPSHOT_FILENAME,),
    )


def _closed_snapshot_seal(
    evidence: A.ClosedSnapshotEvidence,
    *,
    snapshot_identity: tuple[int, int, int, int, int, int, int, int] | None = None,
) -> A.PrivateTreeSeal:
    identity = snapshot_identity or evidence.snapshot_identity
    return A.PrivateTreeSeal(
        root_identity=evidence.staging_identity,
        nodes=(
            A.PrivateNodeSeal(
                relative_path=(A.SNAPSHOT_FILENAME,),
                kind="file",
                identity=identity,
                byte_size=evidence.metadata.byte_size,
                sha256=evidence.metadata.file_sha256,
            ),
            A.PrivateNodeSeal(
                relative_path=(),
                kind="directory",
                identity=evidence.staging_identity,
                byte_size=None,
                sha256=None,
            ),
        ),
    )


def _snapshot_in_progress_journal() -> dict[str, object]:
    reserved = _bootstrap_payload("reserved")
    staging_created = _bootstrap_payload("staging_created", reserved)
    return _bootstrap_payload("snapshot_in_progress", staging_created)


def test_close_bootstrap_snapshot_publishes_and_reseals_exact_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _snapshot_in_progress_journal()
    current_digest = A.canonical_payload_digest(current)
    evidence = _closed_snapshot_fixture()
    published: list[dict[str, object]] = []
    seals: list[A.ExpectedPrivateDirectory] = []
    reads = 0

    def read_journal(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if reads == 1:
            return current, (1, 2, 3, 4, 5), current_digest
        closed = published[0]
        return closed, (1, 2, 3, 4, 6), A.canonical_payload_digest(closed)

    def advance(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        published.append(payload)
        return A.canonical_payload_digest(payload)

    def seal(
        _parent_fd: int,
        _staging_name: str,
        expected: A.ExpectedPrivateDirectory,
    ) -> A.PrivateTreeSeal:
        seals.append(expected)
        return _closed_snapshot_seal(evidence)

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(
        A,
        "_materialize_consistent_snapshot_in_precreated_staging_at",
        lambda *_a, **_k: evidence,
    )
    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", advance)
    monkeypatch.setattr(A, "seal_private_tree_at", seal)
    closed, digest, returned = A._close_bootstrap_snapshot_locked_at(
        10,
        "run.bootstrap-journal.json",
        lock_fd=11,
        lock_name=".run.bootstrap-journal.json.lock",
        staging_fd=12,
        staging_path=Path("run.bootstrap-staging"),
        source_db=Path("chat.db"),
        expected_staging_device_inode=(7, 701),
    )
    assert returned is evidence
    assert closed["state"] == "snapshot_closed"
    assert closed["previous_journal_digest"] == current_digest
    assert closed["completed_artifacts"] == {
        A.SNAPSHOT_FILENAME: evidence.metadata.file_sha256
    }
    assert digest == A.canonical_payload_digest(closed)
    assert reads == 2
    assert len(seals) == 2
    for expected in seals:
        assert expected.children[A.SNAPSHOT_FILENAME] == A.ExpectedPrivateFile(
            evidence.metadata.byte_size,
            evidence.metadata.file_sha256,
        )


def test_close_bootstrap_snapshot_refuses_wrong_state_before_materializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _bootstrap_payload("staging_created", _bootstrap_payload("reserved"))
    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_a, **_k: (current, (1, 2, 3, 4, 5), A.canonical_payload_digest(current)),
    )
    monkeypatch.setattr(
        A,
        "_materialize_consistent_snapshot_in_precreated_staging_at",
        lambda *_a, **_k: pytest.fail("wrong state must not materialize"),
    )
    with pytest.raises(A.BootstrapStateError, match="snapshot_in_progress"):
        A._close_bootstrap_snapshot_locked_at(
            10,
            "run.bootstrap-journal.json",
            lock_fd=11,
            lock_name=".run.bootstrap-journal.json.lock",
            staging_fd=12,
            staging_path=Path("run.bootstrap-staging"),
            source_db=Path("chat.db"),
            expected_staging_device_inode=(7, 701),
        )


@pytest.mark.parametrize("failure", ["before_publish_seal", "after_publish_read"])
def test_close_bootstrap_snapshot_failure_requires_locked_recovery(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    current = _snapshot_in_progress_journal()
    evidence = _closed_snapshot_fixture()
    current_digest = A.canonical_payload_digest(current)
    published: list[dict[str, object]] = []
    reads = 0

    def read_journal(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if reads == 1:
            return current, (1, 2, 3, 4, 5), current_digest
        raise A.BootstrapStateError("injected reread failure")

    def seal(*_args: object, **_kwargs: object) -> A.PrivateTreeSeal:
        if failure == "before_publish_seal":
            raise A.BootstrapStateError("injected seal failure")
        return _closed_snapshot_seal(evidence)

    def advance(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        published.append(payload)
        return A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(
        A,
        "_materialize_consistent_snapshot_in_precreated_staging_at",
        lambda *_a, **_k: evidence,
    )
    monkeypatch.setattr(A, "seal_private_tree_at", seal)
    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", advance)
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._close_bootstrap_snapshot_locked_at(
            10,
            "run.bootstrap-journal.json",
            lock_fd=11,
            lock_name=".run.bootstrap-journal.json.lock",
            staging_fd=12,
            staging_path=Path("run.bootstrap-staging"),
            source_db=Path("chat.db"),
            expected_staging_device_inode=(7, 701),
        )
    assert bool(published) is (failure == "after_publish_read")


@pytest.mark.parametrize("replacement_phase", ["before_publish", "after_publish"])
def test_close_bootstrap_snapshot_rejects_same_bytes_replacement_inode(
    monkeypatch: pytest.MonkeyPatch,
    replacement_phase: str,
) -> None:
    current = _snapshot_in_progress_journal()
    current_digest = A.canonical_payload_digest(current)
    evidence = _closed_snapshot_fixture()
    replacement_identity = (
        evidence.snapshot_identity[0],
        evidence.snapshot_identity[1] + 1,
        *evidence.snapshot_identity[2:],
    )
    published: list[dict[str, object]] = []
    reads = 0
    seals = 0

    def read_journal(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if reads == 1:
            return current, (1, 2, 3, 4, 5), current_digest
        closed = published[0]
        return closed, (1, 2, 3, 4, 6), A.canonical_payload_digest(closed)

    def seal(*_args: object, **_kwargs: object) -> A.PrivateTreeSeal:
        nonlocal seals
        seals += 1
        replace = replacement_phase == "before_publish" or seals == 2
        return _closed_snapshot_seal(
            evidence,
            snapshot_identity=replacement_identity if replace else None,
        )

    def advance(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        published.append(payload)
        return A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(
        A,
        "_materialize_consistent_snapshot_in_precreated_staging_at",
        lambda *_a, **_k: evidence,
    )
    monkeypatch.setattr(A, "seal_private_tree_at", seal)
    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", advance)
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._close_bootstrap_snapshot_locked_at(
            10,
            "run.bootstrap-journal.json",
            lock_fd=11,
            lock_name=".run.bootstrap-journal.json.lock",
            staging_fd=12,
            staging_path=Path("run.bootstrap-staging"),
            source_db=Path("chat.db"),
            expected_staging_device_inode=(7, 701),
        )
    assert bool(published) is (replacement_phase == "after_publish")


def _closed_resume_environment() -> tuple[
    _FakeTreeOps,
    _FakeTreeNode,
    Path,
    dict[str, object],
    A.ClosedSnapshotEvidence,
]:
    journal = _bootstrap_payload(
        "snapshot_closed", _snapshot_in_progress_journal()
    )
    fixture = _closed_snapshot_fixture()
    parent = _FakeTreeNode("parent", kind="directory")
    staging = _FakeTreeNode(str(journal["staging_name"]), kind="directory")
    snapshot = _FakeTreeNode(
        A.SNAPSHOT_FILENAME, kind="file", data=b"S" * 4096
    )
    staging.children[A.SNAPSHOT_FILENAME] = snapshot
    parent.children[str(journal["staging_name"])] = staging
    parent.children["run.bootstrap-journal.json"] = _FakeTreeNode(
        "journal", kind="file"
    )
    ops = _FakeTreeOps(parent)
    staging_path = Path(
        f"D:/Code-PC/ai-prose-baselines-private/{journal['staging_name']}"
    ).absolute()
    ops.path_nodes[staging_path.parent] = parent
    ops.path_nodes[staging_path] = staging
    evidence = replace(
        fixture,
        snapshot_identity=A._private_node_identity(_FakeTreeStat(snapshot)),
        staging_identity=A._private_node_identity(_FakeTreeStat(staging)),
        snapshot_device_inode=(7, snapshot.inode),
        staging_device_inode=(7, staging.inode),
    )
    return ops, parent, staging_path, journal, evidence


def test_resume_snapshot_closed_revalidates_and_transfers_staging_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, _parent, staging_path, journal, evidence = _closed_resume_environment()
    digest = A.canonical_payload_digest(journal)
    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_a, **_k: (journal, (1, 2, 3, 4, 5), digest),
    )
    monkeypatch.setattr(
        A,
        "_verify_existing_closed_snapshot_at",
        lambda *_a, **_k: evidence,
    )
    monkeypatch.setattr(
        A,
        "seal_private_tree_at",
        lambda *_a, **_k: _closed_snapshot_seal(evidence),
    )
    result = A._resume_bootstrap_snapshot_closed_locked_at(
        10,
        "run.bootstrap-journal.json",
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        _ops=ops,
    )
    assert result.journal == journal
    assert result.journal_digest == digest
    assert result.evidence is evidence
    assert result.staging_identity == evidence.staging_identity
    assert result.staging_fd in ops.nodes
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("late_failure", ["journal_drift", "final_appears", "snapshot_inode"])
def test_resume_snapshot_closed_late_drift_refuses_and_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
    late_failure: str,
) -> None:
    ops, parent, staging_path, journal, evidence = _closed_resume_environment()
    digest = A.canonical_payload_digest(journal)
    reads = 0
    locks = 0
    seals = 0

    def read_journal(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if late_failure == "journal_drift" and reads == 2:
            return journal, (1, 2, 3, 4, 6), "sha256:" + "0" * 64
        return journal, (1, 2, 3, 4, 5), digest

    def verify_lock(*_args: object, **_kwargs: object):
        nonlocal locks
        locks += 1
        if late_failure == "final_appears" and locks == 2:
            parent.children[str(journal["final_name"])] = _FakeTreeNode(
                "late-final", kind="directory"
            )
        return (1, 2, 3, 4, 5)

    def seal(*_args: object, **_kwargs: object) -> A.PrivateTreeSeal:
        nonlocal seals
        seals += 1
        if late_failure == "snapshot_inode" and seals == 2:
            replacement = (
                evidence.snapshot_identity[0],
                evidence.snapshot_identity[1] + 1,
                *evidence.snapshot_identity[2:],
            )
            return _closed_snapshot_seal(
                evidence, snapshot_identity=replacement
            )
        return _closed_snapshot_seal(evidence)

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", verify_lock)
    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(
        A,
        "_verify_existing_closed_snapshot_at",
        lambda *_a, **_k: evidence,
    )
    monkeypatch.setattr(A, "seal_private_tree_at", seal)
    with pytest.raises(A.BootstrapStateError):
        A._resume_bootstrap_snapshot_closed_locked_at(
            10,
            "run.bootstrap-journal.json",
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            _ops=ops,
        )
    assert set(ops.nodes) == {10}


def _preparer_reserved_payload() -> dict[str, object]:
    final_name = "run-final"
    return A.bootstrap_journal_payload(
        state="reserved",
        previous_journal_digest=None,
        staging_name=A.bootstrap_staging_name(final_name),
        final_name=final_name,
        semantic_options_digest="sha256:" + "b" * 64,
        run_controls_digest="sha256:" + "c" * 64,
        smoke_policy_digest=None,
        hmac_key_id_value="sha256:" + "e" * 64,
        snapshot_metadata=None,
        universe_binding=None,
        completed_artifacts={},
    )


def _preparer_state(
    reserved: dict[str, object], state: str
) -> dict[str, object]:
    if state == "reserved":
        return reserved
    staging_created = A._empty_bootstrap_successor(
        reserved, A.canonical_payload_digest(reserved), "staging_created"
    )
    if state == "staging_created":
        return staging_created
    if state == "snapshot_in_progress":
        return A._empty_bootstrap_successor(
            staging_created,
            A.canonical_payload_digest(staging_created),
            "snapshot_in_progress",
        )
    raise AssertionError(state)


def _preparer_fake_environment(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
    *,
    partial_names: tuple[str, ...] = (),
) -> tuple[
    _FakeTreeOps,
    _FakeTreeNode,
    Path,
    dict[str, object],
    list[dict[str, object] | None],
    list[str],
]:
    reserved = _preparer_reserved_payload()
    staging_name = str(reserved["staging_name"])
    journal_name = A.bootstrap_journal_name(str(reserved["final_name"]))
    parent = _FakeTreeNode("parent", kind="directory")
    ops = _FakeTreeOps(parent)
    staging_path = Path(
        f"D:/Code-PC/ai-prose-baselines-private/{staging_name}"
    ).absolute()
    ops.path_nodes[staging_path.parent] = parent
    if initial_state is not None:
        parent.children[journal_name] = _FakeTreeNode(
            "journal", kind="file"
        )
        staging = _FakeTreeNode(staging_name, kind="directory")
        for name in partial_names:
            staging.children[name] = _FakeTreeNode(name, kind="file")
        parent.children[staging_name] = staging
        ops.path_nodes[staging_path] = staging
    original_mkdir = ops.mkdir

    def bind_staging(name: str, mode: int, *, dir_fd: int) -> None:
        original_mkdir(name, mode, dir_fd=dir_fd)
        if name == staging_name:
            ops.path_nodes[staging_path] = parent.children[name]

    ops.mkdir = bind_staging  # type: ignore[method-assign]
    holder: list[dict[str, object] | None] = [
        _preparer_state(reserved, initial_state)
        if initial_state is not None
        else None
    ]
    advances: list[str] = []

    def read_journal(*_args: object, **_kwargs: object):
        payload = holder[0]
        if payload is None:
            raise A.BootstrapStateError("synthetic missing journal")
        return payload, (1, 2, 3, 4, 5), A.canonical_payload_digest(payload)

    def advance(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        previous = holder[0]
        if previous is not None:
            A.validate_bootstrap_transition(previous, payload)
        elif payload["state"] != "reserved":
            raise AssertionError("synthetic journal must begin reserved")
        holder[0] = payload
        advances.append(str(payload["state"]))
        parent.children.setdefault(
            journal_name, _FakeTreeNode("journal", kind="file")
        )
        return A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", advance)
    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    return ops, parent, staging_path, reserved, holder, advances


@pytest.mark.parametrize(
    ("initial_state", "expected_advances"),
    [
        (None, ["reserved", "staging_created", "snapshot_in_progress"]),
        ("reserved", ["staging_created", "snapshot_in_progress"]),
        ("staging_created", ["snapshot_in_progress"]),
        ("snapshot_in_progress", []),
    ],
)
def test_prepare_snapshot_in_progress_handles_each_restart_state(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
    expected_advances: list[str],
) -> None:
    ops, parent, staging_path, reserved, holder, advances = (
        _preparer_fake_environment(monkeypatch, initial_state)
    )
    result = A._prepare_bootstrap_snapshot_in_progress_locked_at(
        10,
        A.bootstrap_journal_name(str(reserved["final_name"])),
        reserved,
        staging_path,
        lock_fd=11,
        lock_name=".synthetic.lock",
        _ops=ops,
    )
    assert result.journal["state"] == "snapshot_in_progress"
    assert holder[0] == result.journal
    assert result.journal_digest == A.canonical_payload_digest(result.journal)
    assert advances == expected_advances
    staging = parent.children[str(reserved["staging_name"])]
    assert staging.children == {}
    assert ops.nodes[result.staging_fd] is staging
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_prepare_snapshot_in_progress_resets_recognized_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, parent, staging_path, reserved, holder, advances = (
        _preparer_fake_environment(
            monkeypatch,
            "snapshot_in_progress",
            partial_names=tuple(A.SNAPSHOT_PARTIAL_FILENAMES),
        )
    )
    result = A._prepare_bootstrap_snapshot_in_progress_locked_at(
        10,
        A.bootstrap_journal_name(str(reserved["final_name"])),
        reserved,
        staging_path,
        lock_fd=11,
        lock_name=".synthetic.lock",
        _ops=ops,
    )
    staging = parent.children[str(reserved["staging_name"])]
    assert staging.children == {}
    assert advances == []
    assert holder[0] == result.journal
    assert ops.unlinked_labels[-1] == A.SNAPSHOT_FILENAME
    ops.close(result.staging_fd)


def test_prepare_snapshot_in_progress_unknown_partial_refuses_and_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, parent, staging_path, reserved, _holder, advances = (
        _preparer_fake_environment(
            monkeypatch,
            "snapshot_in_progress",
            partial_names=("foreign",),
        )
    )
    with pytest.raises(A.BootstrapStateError, match="unknown name"):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert advances == []
    assert tuple(parent.children[str(reserved["staging_name"])].children) == (
        "foreign",
    )
    assert set(ops.nodes) == {10}


def test_prepare_snapshot_in_progress_final_name_refuses_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, parent, staging_path, reserved, _holder, advances = (
        _preparer_fake_environment(monkeypatch, "reserved")
    )
    parent.children[str(reserved["final_name"])] = _FakeTreeNode(
        "unexpected-final", kind="directory"
    )
    with pytest.raises(A.BootstrapStateError, match="final name"):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert advances == []
    assert set(ops.nodes) == {10}


def test_prepare_snapshot_in_progress_create_collision_requires_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, parent, staging_path, reserved, _holder, advances = (
        _preparer_fake_environment(monkeypatch, "reserved")
    )
    staging_name = str(reserved["staging_name"])
    parent.children.pop(staging_name)
    ops.path_nodes.pop(staging_path)
    ops.mkdir = lambda *_a, **_k: (_ for _ in ()).throw(  # type: ignore[method-assign]
        FileExistsError("synthetic create collision")
    )
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert advances == []
    assert set(ops.nodes) == {10}


def test_prepare_snapshot_in_progress_does_not_adopt_same_invocation_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, parent, staging_path, reserved, holder, advances = (
        _preparer_fake_environment(monkeypatch, None)
    )
    journal_name = A.bootstrap_journal_name(str(reserved["final_name"]))
    staging_name = str(reserved["staging_name"])

    def collide_after_reserved(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        holder[0] = payload
        advances.append(str(payload["state"]))
        parent.children[journal_name] = _FakeTreeNode("journal", kind="file")
        collision = _FakeTreeNode("collision-staging", kind="directory")
        parent.children[staging_name] = collision
        ops.path_nodes[staging_path] = collision
        return A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", collide_after_reserved)
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            journal_name,
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert advances == ["reserved"]
    assert parent.children[staging_name].label == "collision-staging"
    assert set(ops.nodes) == {10}


def test_prepare_snapshot_in_progress_post_publish_reread_failure_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ops, _parent, staging_path, reserved, holder, advances = (
        _preparer_fake_environment(monkeypatch, "reserved")
    )
    reads = 0

    def fail_second_read(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if reads == 1:
            payload = holder[0]
            assert payload is not None
            return payload, (1, 2, 3, 4, 5), A.canonical_payload_digest(payload)
        raise A.BootstrapStateError("synthetic post-publication reread")

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", fail_second_read)
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert advances == ["staging_created"]
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("late_race", ["path_replacement", "foreign_child", "final_name"])
def test_prepare_snapshot_in_progress_final_lock_races_refuse_before_return(
    monkeypatch: pytest.MonkeyPatch,
    late_race: str,
) -> None:
    ops, parent, staging_path, reserved, _holder, advances = (
        _preparer_fake_environment(monkeypatch, "snapshot_in_progress")
    )
    staging_name = str(reserved["staging_name"])
    staging = parent.children[staging_name]
    lock_checks = 0

    def mutate_on_final_lock(*_args: object, **_kwargs: object):
        nonlocal lock_checks
        lock_checks += 1
        if lock_checks == 2:
            if late_race == "path_replacement":
                replacement = _FakeTreeNode("late-staging", kind="directory")
                parent.children[staging_name] = replacement
                ops.path_nodes[staging_path] = replacement
            elif late_race == "foreign_child":
                staging.children["foreign"] = _FakeTreeNode(
                    "foreign", kind="file"
                )
            else:
                parent.children[str(reserved["final_name"])] = _FakeTreeNode(
                    "late-final", kind="directory"
                )
        return (1, 2, 3, 4, 5)

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", mutate_on_final_lock)
    with pytest.raises(A.BootstrapStateError):
        A._prepare_bootstrap_snapshot_in_progress_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
        )
    assert lock_checks == 2
    assert advances == []
    assert set(ops.nodes) == {10}


def _integrator_closed_payload(
    previous: dict[str, object], evidence: A.ClosedSnapshotEvidence
) -> dict[str, object]:
    return A.bootstrap_journal_payload(
        state="snapshot_closed",
        previous_journal_digest=A.canonical_payload_digest(previous),
        staging_name=str(previous["staging_name"]),
        final_name=str(previous["final_name"]),
        semantic_options_digest=str(previous["semantic_options_digest"]),
        run_controls_digest=str(previous["run_controls_digest"]),
        smoke_policy_digest=None,
        hmac_key_id_value=str(previous["hmac_key_id"]),
        snapshot_metadata=A.snapshot_metadata_payload(evidence.metadata),
        universe_binding=None,
        completed_artifacts={
            A.SNAPSHOT_FILENAME: evidence.metadata.file_sha256
        },
    )


def _integrator_environment(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    list[dict[str, object] | None],
    A.ClosedSnapshotEvidence,
    list[str],
    object,
    object,
    object,
]:
    reserved = _preparer_reserved_payload()
    in_progress = _preparer_state(reserved, "snapshot_in_progress")
    fixture = _closed_snapshot_fixture()
    parent = _FakeTreeNode("parent", kind="directory")
    staging_node = _FakeTreeNode(
        str(reserved["staging_name"]), kind="directory"
    )
    snapshot_node = _FakeTreeNode(
        A.SNAPSHOT_FILENAME, kind="file", data=b"S" * 4096
    )
    staging_node.children[A.SNAPSHOT_FILENAME] = snapshot_node
    parent.children[str(reserved["staging_name"])] = staging_node
    journal_name = A.bootstrap_journal_name(str(reserved["final_name"]))
    ops = _FakeTreeOps(parent)
    staging_path = Path(
        f"D:/Code-PC/ai-prose-baselines-private/{reserved['staging_name']}"
    ).absolute()
    ops.path_nodes[staging_path.parent] = parent
    ops.path_nodes[staging_path] = staging_node
    evidence = replace(
        fixture,
        snapshot_identity=A._private_node_identity(_FakeTreeStat(snapshot_node)),
        staging_identity=A._private_node_identity(_FakeTreeStat(staging_node)),
        snapshot_device_inode=(7, snapshot_node.inode),
        staging_device_inode=(7, staging_node.inode),
    )
    if initial_state is None:
        current: dict[str, object] | None = None
    elif initial_state == "snapshot_closed":
        current = _integrator_closed_payload(in_progress, evidence)
    else:
        current = _preparer_state(reserved, initial_state)
    holder: list[dict[str, object] | None] = [current]
    if current is not None:
        parent.children[journal_name] = _FakeTreeNode("journal", kind="file")
    calls: list[str] = []

    def allocate_staging_fd() -> int:
        fd = ops.next_fd
        ops.next_fd += 1
        ops.nodes[fd] = staging_node
        ops.offsets[fd] = 0
        return fd

    def read_journal(*_args: object, **_kwargs: object):
        payload = holder[0]
        if payload is None:
            raise A.BootstrapStateError("synthetic missing journal")
        return (
            payload,
            (1, 2, 3, 4, 5),
            A.canonical_payload_digest(payload),
        )

    def preparer(*_args: object, **_kwargs: object) -> A.PreparedSnapshotInProgress:
        calls.append("prepare")
        holder[0] = in_progress
        parent.children.setdefault(
            journal_name, _FakeTreeNode("journal", kind="file")
        )
        return A.PreparedSnapshotInProgress(
            journal=in_progress,
            journal_digest=A.canonical_payload_digest(in_progress),
            staging_fd=allocate_staging_fd(),
            staging_identity=evidence.staging_identity,
            staging_device_inode=evidence.staging_device_inode,
        )

    def closer(*_args: object, **_kwargs: object):
        calls.append("close")
        closed = _integrator_closed_payload(in_progress, evidence)
        holder[0] = closed
        return closed, A.canonical_payload_digest(closed), evidence

    def resumer(*_args: object, **_kwargs: object) -> A.PreparedSnapshotClosed:
        calls.append("resume")
        closed = holder[0]
        assert closed is not None
        return A.PreparedSnapshotClosed(
            journal=closed,
            journal_digest=A.canonical_payload_digest(closed),
            staging_fd=allocate_staging_fd(),
            staging_identity=evidence.staging_identity,
            evidence=evidence,
        )

    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    return (
        ops,
        staging_path,
        reserved,
        holder,
        evidence,
        calls,
        preparer,
        closer,
        resumer,
    )


@pytest.mark.parametrize(
    "initial_state", [None, "reserved", "staging_created", "snapshot_in_progress"]
)
def test_integrate_snapshot_closed_routes_preclosed_states_through_all_phases(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, initial_state)
    result = A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
        10,
        A.bootstrap_journal_name(str(reserved["final_name"])),
        reserved,
        staging_path,
        Path("D:/synthetic/chat.db"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        _ops=ops,
        _preparer=preparer,  # type: ignore[arg-type]
        _closer=closer,  # type: ignore[arg-type]
        _resumer=resumer,  # type: ignore[arg-type]
    )
    assert calls == ["prepare", "close", "resume"]
    assert result.journal["state"] == "snapshot_closed"
    assert result.evidence is evidence
    assert ops.closed_labels == [str(reserved["staging_name"])]
    assert result.staging_fd in ops.nodes
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_routes_closed_state_verify_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_closed")
    result = A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
        10,
        A.bootstrap_journal_name(str(reserved["final_name"])),
        reserved,
        staging_path,
        Path("D:/synthetic/chat.db"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        _ops=ops,
        _preparer=preparer,  # type: ignore[arg-type]
        _closer=closer,  # type: ignore[arg-type]
        _resumer=resumer,  # type: ignore[arg-type]
    )
    assert calls == ["resume"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_refuses_later_state_before_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        holder,
        evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_closed")
    closed = holder[0]
    assert closed is not None
    later = A.bootstrap_journal_payload(
        state="universe_closed",
        previous_journal_digest=A.canonical_payload_digest(closed),
        staging_name=str(closed["staging_name"]),
        final_name=str(closed["final_name"]),
        semantic_options_digest=str(closed["semantic_options_digest"]),
        run_controls_digest=str(closed["run_controls_digest"]),
        smoke_policy_digest="sha256:" + "d" * 64,
        hmac_key_id_value=str(closed["hmac_key_id"]),
        snapshot_metadata=A.snapshot_metadata_payload(evidence.metadata),
        universe_binding=_bootstrap_universe_payload(),
        completed_artifacts=dict(closed["completed_artifacts"]),
    )
    holder[0] = later
    with pytest.raises(A.BootstrapStateError, match="not resumable"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=closer,  # type: ignore[arg-type]
            _resumer=resumer,  # type: ignore[arg-type]
        )
    assert calls == []
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_closer_failure_closes_prepared_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        _closer,
        resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_in_progress")

    def fail_close(*_args: object, **_kwargs: object):
        calls.append("close")
        raise A.BootstrapRecoveryRequired("synthetic close failure")

    with pytest.raises(A.BootstrapRecoveryRequired, match="synthetic"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=fail_close,
            _resumer=resumer,  # type: ignore[arg-type]
        )
    assert calls == ["prepare", "close"]
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("prepared_drift", ["malformed_journal", "immutable_binding"])
def test_integrate_snapshot_closed_invalid_prepared_result_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
    prepared_drift: str,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_in_progress")

    def invalid_preparer(*args: object, **kwargs: object):
        prepared = preparer(*args, **kwargs)
        journal = dict(prepared.journal)
        if prepared_drift == "malformed_journal":
            journal = {}
        else:
            journal["semantic_options_digest"] = "sha256:" + "f" * 64
        return replace(
            prepared,
            journal=journal,
            journal_digest=A.canonical_payload_digest(journal),
        )

    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=invalid_preparer,
            _closer=closer,  # type: ignore[arg-type]
            _resumer=resumer,  # type: ignore[arg-type]
        )
    assert calls == ["prepare"]
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_invalid_returned_close_requires_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_in_progress")

    def invalid_close(*args: object, **kwargs: object):
        closed, _digest, evidence = closer(*args, **kwargs)
        return closed, "sha256:" + "0" * 64, evidence

    with pytest.raises(A.BootstrapRecoveryRequired, match="published"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=invalid_close,
            _resumer=resumer,  # type: ignore[arg-type]
        )
    assert calls == ["prepare", "close"]
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_newly_closed_resume_failure_requires_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        _resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_in_progress")

    def fail_resume(*_args: object, **_kwargs: object):
        calls.append("resume")
        raise A.BootstrapStateError("synthetic reopen drift")

    with pytest.raises(A.BootstrapRecoveryRequired, match="reopen"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=closer,  # type: ignore[arg-type]
            _resumer=fail_resume,
        )
    assert calls == ["prepare", "close", "resume"]
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_existing_closed_drift_stays_verify_only_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        _resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_closed")

    def fail_resume(*_args: object, **_kwargs: object):
        calls.append("resume")
        raise A.BootstrapStateError("synthetic closed drift")

    with pytest.raises(A.BootstrapStateError) as failure:
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=closer,  # type: ignore[arg-type]
            _resumer=fail_resume,
        )
    assert type(failure.value) is A.BootstrapStateError
    assert calls == ["resume"]
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    ("initial_state", "expected_error"),
    [
        ("snapshot_closed", A.BootstrapStateError),
        ("snapshot_in_progress", A.BootstrapRecoveryRequired),
    ],
)
def test_integrate_snapshot_closed_malformed_resumed_identity_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str,
    expected_error: type[Exception],
) -> None:
    (
        ops,
        staging_path,
        reserved,
        _holder,
        _evidence,
        calls,
        preparer,
        closer,
        resumer,
    ) = _integrator_environment(monkeypatch, initial_state)

    def malformed_resumer(*args: object, **kwargs: object):
        result = resumer(*args, **kwargs)
        return replace(result, staging_identity=None)  # type: ignore[arg-type]

    with pytest.raises(expected_error):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=closer,  # type: ignore[arg-type]
            _resumer=malformed_resumer,
        )
    expected_calls = (
        ["resume"]
        if initial_state == "snapshot_closed"
        else ["prepare", "close", "resume"]
    )
    assert calls == expected_calls
    assert set(ops.nodes) == {10}


def test_integrate_snapshot_closed_rejects_reopened_inode_change_and_closes_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        ops,
        staging_path,
        reserved,
        holder,
        evidence,
        calls,
        preparer,
        closer,
        _resumer,
    ) = _integrator_environment(monkeypatch, "snapshot_in_progress")

    def changed_resumer(*_args: object, **_kwargs: object) -> A.PreparedSnapshotClosed:
        calls.append("resume")
        closed = holder[0]
        assert closed is not None
        changed = replace(
            evidence,
            snapshot_device_inode=(
                evidence.snapshot_device_inode[0],
                evidence.snapshot_device_inode[1] + 1,
            ),
            snapshot_identity=(
                evidence.snapshot_identity[0],
                evidence.snapshot_identity[1] + 1,
                *evidence.snapshot_identity[2:],
            ),
        )
        node = ops.nodes[10].children[str(reserved["staging_name"])]
        fd = ops.next_fd
        ops.next_fd += 1
        ops.nodes[fd] = node
        ops.offsets[fd] = 0
        return A.PreparedSnapshotClosed(
            journal=closed,
            journal_digest=A.canonical_payload_digest(closed),
            staging_fd=fd,
            staging_identity=evidence.staging_identity,
            evidence=changed,
        )

    with pytest.raises(A.BootstrapRecoveryRequired, match="reopen"):
        A._prepare_or_resume_bootstrap_snapshot_closed_locked_at(
            10,
            A.bootstrap_journal_name(str(reserved["final_name"])),
            reserved,
            staging_path,
            Path("D:/synthetic/chat.db"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            _ops=ops,
            _preparer=preparer,  # type: ignore[arg-type]
            _closer=closer,  # type: ignore[arg-type]
            _resumer=changed_resumer,
        )
    assert calls == ["prepare", "close", "resume"]
    assert set(ops.nodes) == {10}


def test_private_tree_seal_is_exact_postorder_and_closes_descriptors() -> None:
    ops, expected, _root, _alpha, _beta = _fake_tree()
    seal = A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert [node.relative_path for node in seal.nodes] == [
        ("alpha.txt",),
        ("nested", "beta.txt"),
        ("nested",),
        (),
    ]
    assert ops.fsync_labels == ["alpha", "beta", "nested", "root", "parent"]
    assert set(ops.nodes) == {10}
    assert sorted(ops.closed_labels) == ["alpha", "beta", "nested", "root"]


@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_private_tree_seal_rejects_inventory_drift_before_fsync(
    mutation: str,
) -> None:
    ops, expected, root, _alpha, _beta = _fake_tree()
    if mutation == "extra":
        root.children["alien"] = _FakeTreeNode("alien", kind="file")
    else:
        root.children.pop("alpha.txt")
    with pytest.raises(A.BootstrapStateError, match="inventory"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert ops.fsync_labels == []
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("mode", 0o644), ("uid", 1001), ("nlink", 2)],
)
def test_private_tree_seal_rejects_unsafe_file_inode(
    attribute: str, value: int
) -> None:
    ops, expected, _root, alpha, _beta = _fake_tree()
    setattr(alpha, attribute, value)
    with pytest.raises(A.BootstrapStateError, match="inode"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert ops.fsync_labels == []
    assert set(ops.nodes) == {10}


def test_private_tree_seal_rejects_digest_mismatch_before_fsync() -> None:
    ops, expected, _root, _alpha, _beta = _fake_tree()
    wrong = A.ExpectedPrivateDirectory(
        children={
            **expected.children,
            "alpha.txt": A.ExpectedPrivateFile(
                byte_size=6, sha256="sha256:" + "0" * 64
            ),
        }
    )
    with pytest.raises(A.BootstrapStateError, match="bytes"):
        A.seal_private_tree_at(10, "root", wrong, _ops=ops)
    assert ops.fsync_labels == []
    assert set(ops.nodes) == {10}


def test_private_tree_seal_file_drift_during_fsync_requires_recovery() -> None:
    ops, expected, _root, alpha, _beta = _fake_tree()

    def mutate(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is alpha:
            node.version += 1

    ops.on_fsync = mutate
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


def test_private_tree_seal_root_replacement_after_parent_fsync_requires_recovery() -> None:
    ops, expected, root, _alpha, _beta = _fake_tree()
    parent = ops.nodes[10]

    def replace(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is parent:
            replacement = _FakeTreeNode("replacement", kind="directory")
            replacement.children = dict(root.children)
            parent.children["root"] = replacement

    ops.on_fsync = replace
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_private_tree_seal_post_fsync_inventory_drift_requires_recovery(
    mutation: str,
) -> None:
    ops, expected, root, _alpha, _beta = _fake_tree()

    def mutate(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is root:
            if mutation == "extra":
                root.children["alien"] = _FakeTreeNode("alien", kind="file")
            else:
                root.children.pop("alpha.txt")

    ops.on_fsync = mutate
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


def test_private_tree_seal_child_inode_replacement_during_fsync_requires_recovery() -> None:
    ops, expected, root, alpha, _beta = _fake_tree()

    def replace(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node is root:
            root.children["alpha.txt"] = _FakeTreeNode(
                "alpha-replacement", kind="file", data=alpha.data
            )

    ops.on_fsync = replace
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("failure_label", ["alpha", "nested", "parent"])
def test_private_tree_seal_fsync_failure_requires_recovery(
    failure_label: str,
) -> None:
    ops, expected, _root, _alpha, _beta = _fake_tree()

    def fail(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        if node.label == failure_label:
            raise OSError("injected fsync failure")

    ops.on_fsync = fail
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    ("target", "attribute", "value"),
    [
        ("nested", "mode", 0o755),
        ("nested", "uid", 1001),
        ("nested", "kind", "file"),
        ("alpha", "kind", "directory"),
    ],
)
def test_private_tree_seal_rejects_directory_and_type_substitution(
    target: str, attribute: str, value: object
) -> None:
    ops, expected, root, alpha, _beta = _fake_tree()
    node = root.children["nested"] if target == "nested" else alpha
    if target == "nested":
        root.children.pop("alpha.txt")
        expected = A.ExpectedPrivateDirectory(
            children={"nested": expected.children["nested"]}
        )
    setattr(node, attribute, value)
    with pytest.raises(A.BootstrapStateError, match="inode"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)
    assert set(ops.nodes) == {10}


def test_private_tree_seal_recursive_failure_closes_every_open_descriptor() -> None:
    ops, expected, _root, _alpha, beta = _fake_tree()
    wrong_nested = A.ExpectedPrivateDirectory(
        children={
            "beta.txt": A.ExpectedPrivateFile(
                byte_size=len(beta.data), sha256="sha256:" + "0" * 64
            )
        }
    )
    wrong = A.ExpectedPrivateDirectory(
        children={**expected.children, "nested": wrong_nested}
    )
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A.seal_private_tree_at(10, "root", wrong, _ops=ops)
    assert set(ops.nodes) == {10}
    assert sorted(ops.closed_labels) == ["alpha", "beta", "nested", "root"]


def test_private_tree_seal_root_close_failure_after_durability_requires_recovery() -> None:
    ops, expected, _root, _alpha, _beta = _fake_tree()
    ops.fail_close_label = "root"
    with pytest.raises(A.BootstrapRecoveryRequired, match="descriptor close"):
        A.seal_private_tree_at(10, "root", expected, _ops=ops)


def test_private_tree_spec_rejects_unsafe_names_and_bool_size() -> None:
    with pytest.raises(A.BootstrapStateError):
        A._validated_expected_private_tree(
            A.ExpectedPrivateDirectory(
                children={"../escape": A.ExpectedPrivateFile(0, A._sha256_tag(b""))}
            )
        )
    with pytest.raises(A.BootstrapStateError, match="file expectation"):
        A._validated_expected_private_tree(
            A.ExpectedPrivateDirectory(
                children={"x": A.ExpectedPrivateFile(True, A._sha256_tag(b""))}
            )
        )
    with pytest.raises(A.BootstrapStateError, match="child name"):
        A._validated_expected_private_tree(
            A.ExpectedPrivateDirectory(
                children={7: A.ExpectedPrivateFile(0, A._sha256_tag(b""))}  # type: ignore[dict-item]
            )
        )
    with pytest.raises(A.BootstrapStateError, match="root expectation"):
        A._validated_expected_private_tree(  # type: ignore[arg-type]
            A.ExpectedPrivateFile(0, A._sha256_tag(b""))
        )


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_live_private_tree_seal_refuses_non_macos_before_io() -> None:
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A.seal_private_tree_at(10, "root", A.ExpectedPrivateDirectory(children={}))


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS durable journal")
def test_bootstrap_journal_refuses_existing_on_initial_or_skipped_advance(
    tmp_path: Path,
) -> None:
    journal = _private_staging(tmp_path, A.BOOTSTRAP_JOURNAL_FILENAME)
    reserved = _bootstrap_payload("reserved")
    A.write_bootstrap_journal(journal, reserved)
    with pytest.raises(A.BootstrapStateError):
        A.write_bootstrap_journal(journal, reserved)
    skipped = _bootstrap_payload("snapshot_in_progress", reserved)
    with pytest.raises(A.BootstrapStateError, match="sequential"):
        A.write_bootstrap_journal(journal, skipped)


@pytest.mark.skipif(sys.platform == "darwin", reason="non-macOS refusal contract")
def test_durable_bootstrap_journal_refuses_non_macos_host(tmp_path: Path) -> None:
    journal = _private_staging(tmp_path, A.BOOTSTRAP_JOURNAL_FILENAME)
    with pytest.raises(A.BootstrapStateError, match="macOS"):
        A.write_bootstrap_journal(journal, _bootstrap_payload("reserved"))


def test_locked_bootstrap_journal_create_and_advance_bind_held_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.sys, "platform", "darwin")
    identity = (1, 2, 3, 4, 5)
    monkeypatch.setattr(
        A,
        "_verify_bootstrap_lock_held_at",
        lambda *_args, **_kwargs: identity,
    )
    calls: list[dict[str, object]] = []

    def durable(*_args: object, **kwargs: object) -> str:
        calls.append(kwargs)
        return "sha256:" + "a" * 64

    monkeypatch.setattr(A, "_durable_atomic_private_file_at", durable)
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A.BootstrapStateError("missing")
        ),
    )
    monkeypatch.setattr(
        A.os,
        "stat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    reserved = _bootstrap_payload("reserved")
    assert A._advance_bootstrap_journal_locked_at(
        10,
        A.BOOTSTRAP_JOURNAL_FILENAME,
        reserved,
        lock_fd=11,
        lock_name=f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock",
    ) == "sha256:" + "a" * 64
    assert calls[-1]["replace_existing"] is False
    assert calls[-1]["expected_existing_identity"] is None

    staging = _bootstrap_payload("staging_created", reserved)
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_args, **_kwargs: (reserved, identity, A.canonical_payload_digest(reserved)),
    )
    assert A._advance_bootstrap_journal_locked_at(
        10,
        A.BOOTSTRAP_JOURNAL_FILENAME,
        staging,
        lock_fd=11,
        lock_name=f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock",
    ) == "sha256:" + "a" * 64
    assert calls[-1]["replace_existing"] is True
    assert calls[-1]["expected_existing_identity"] == identity


def test_locked_bootstrap_journal_post_publish_lock_drift_requires_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.sys, "platform", "darwin")
    identities = iter(((1, 2, 3, 4, 5), (1, 9, 3, 4, 5)))
    monkeypatch.setattr(
        A,
        "_verify_bootstrap_lock_held_at",
        lambda *_args, **_kwargs: next(identities),
    )
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A.BootstrapStateError("missing")
        ),
    )
    monkeypatch.setattr(
        A.os,
        "stat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        A,
        "_durable_atomic_private_file_at",
        lambda *_args, **_kwargs: "sha256:" + "a" * 64,
    )
    with pytest.raises(A.BootstrapRecoveryRequired, match="lock drifted"):
        A._advance_bootstrap_journal_locked_at(
            10,
            A.BOOTSTRAP_JOURNAL_FILENAME,
            _bootstrap_payload("reserved"),
            lock_fd=11,
            lock_name=f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock",
        )


def test_locked_bootstrap_journal_malformed_existing_never_becomes_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A,
        "_verify_bootstrap_lock_held_at",
        lambda *_args, **_kwargs: (1, 2, 3, 4, 5),
    )
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        A,
        "_read_bootstrap_journal_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A.BootstrapStateError("duplicate keys")
        ),
    )
    monkeypatch.setattr(
        A,
        "_durable_atomic_private_file_at",
        lambda *_args, **_kwargs: pytest.fail("malformed journal must not be replaced"),
    )
    with pytest.raises(A.BootstrapStateError, match="duplicate keys"):
        A._advance_bootstrap_journal_locked_at(
            10,
            A.BOOTSTRAP_JOURNAL_FILENAME,
            _bootstrap_payload("reserved"),
            lock_fd=11,
            lock_name=f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock",
        )


def test_public_journal_release_failure_after_publish_requires_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(
        A,
        "_open_private_parent_dirfd",
        lambda _path: (10, A.BOOTSTRAP_JOURNAL_FILENAME),
    )
    monkeypatch.setattr(
        A,
        "_acquire_bootstrap_lock_at",
        lambda *_args, **_kwargs: (11, f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock"),
    )
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_args, **_kwargs: "sha256:" + "a" * 64,
    )
    monkeypatch.setattr(
        A,
        "_release_bootstrap_lock_at",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            A.BootstrapStateError("unlock failed")
        ),
    )
    closed: list[int] = []
    monkeypatch.setattr(A.os, "close", lambda fd: closed.append(fd))
    with pytest.raises(A.BootstrapRecoveryRequired, match="published"):
        A.write_bootstrap_journal(
            Path("ignored"), _bootstrap_payload("reserved")
        )
    assert closed == [11, 10]


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


def test_stable_bootstrap_lock_acquire_release_never_unlinks_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    info = _lock_stat()
    opened_flags: list[int] = []
    flock_calls: list[tuple[int, bool]] = []
    fsync_calls: list[int] = []
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(
        A.os,
        "open",
        lambda _name, flags, *_args, **_kwargs: opened_flags.append(flags) or 22,
    )
    monkeypatch.setattr(A.os, "fstat", lambda _fd: info)
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: info)
    monkeypatch.setattr(A.os, "fsync", lambda fd: fsync_calls.append(fd))
    monkeypatch.setattr(
        A,
        "_flock_bootstrap_lock",
        lambda fd, *, acquire: flock_calls.append((fd, acquire)),
    )
    monkeypatch.setattr(
        A.os,
        "unlink",
        lambda *_args, **_kwargs: pytest.fail("stable lock name must persist"),
    )
    lock_fd, lock_name = A._acquire_bootstrap_lock_at(
        10, A.BOOTSTRAP_JOURNAL_FILENAME
    )
    assert lock_fd == 22
    assert lock_name == f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock"
    assert opened_flags[0] & os.O_CREAT
    assert not (opened_flags[0] & os.O_EXCL)
    A._release_bootstrap_lock_at(
        10, A.BOOTSTRAP_JOURNAL_FILENAME, lock_fd, lock_name
    )
    assert flock_calls[-1] == (22, False)
    assert fsync_calls == [22, 10]
    reacquired_fd, reacquired_name = A._acquire_bootstrap_lock_at(
        10, A.BOOTSTRAP_JOURNAL_FILENAME
    )
    assert (reacquired_fd, reacquired_name) == (lock_fd, lock_name)


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


def test_stable_bootstrap_lock_refuses_path_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = _lock_stat(inode=22)
    named = _lock_stat(inode=23)
    monkeypatch.setattr(A.sys, "platform", "darwin")
    monkeypatch.setattr(A.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(A.os, "fstat", lambda _fd: opened)
    monkeypatch.setattr(A.os, "stat", lambda *_args, **_kwargs: named)
    monkeypatch.setattr(
        A, "_flock_bootstrap_lock", lambda *_args, **_kwargs: None
    )
    with pytest.raises(A.BootstrapStateError, match="inode"):
        A._verify_bootstrap_lock_held_at(
            10,
            A.BOOTSTRAP_JOURNAL_FILENAME,
            22,
            f".{A.BOOTSTRAP_JOURNAL_FILENAME}.lock",
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"since": dt.date(2025, 1, 1), "until": dt.date(2024, 1, 1)},
        {"include_group_chats": 1},
        {"apple_date_unit": "auto"},
        {"timezone_name": ""},
        {"persona": "joshua\u202e"},
    ],
)
def test_semantic_options_reject_ambiguous_bindings(kwargs: dict[str, object]) -> None:
    valid: dict[str, object] = {
        "since": None,
        "until": None,
        "include_group_chats": True,
        "apple_date_unit": "nanoseconds",
        "timezone_name": "UTC",
        "preprocessing_version": "legacy-preprocess/1",
        "preprocessing_rules_id": "imessage-atomic-rules/1",
        "persona": "joshua",
        "author": "Joshua Miller",
        "register": "personal",
    }
    valid.update(kwargs)
    with pytest.raises(A.AtomicAcquisitionError):
        A.semantic_options_payload(**valid)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_messages": 0},
        {"max_retained": 0},
        {"allow_empty": 1},
        {"checkpoint_interval": 0},
        {"checkpoint_interval": 2},
        {"checkpoint_schema": "bad\nvalue"},
    ],
)
def test_run_controls_reject_invalid_values(kwargs: dict[str, object]) -> None:
    valid: dict[str, object] = {
        "max_messages": 100,
        "max_retained": None,
        "allow_empty": False,
        "checkpoint_schema": "setec-imessage-atomic-checkpoint/2",
        "checkpoint_interval": 1,
    }
    valid.update(kwargs)
    with pytest.raises(A.AtomicAcquisitionError):
        A.run_controls_payload(**valid)  # type: ignore[arg-type]


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


def test_parser_rejects_removed_mutable_checkpoint_interval() -> None:
    with pytest.raises(SystemExit) as caught:
        A.build_arg_parser().parse_args(
            _required_cli("--exclude-group-chats")
            + ["--checkpoint-interval", "2"]
        )
    assert caught.value.code == 2


def test_main_rejects_missing_acquisition_group_policy(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        A.main(_required_cli())
    assert caught.value.code == 2
    assert "--include-group-chats/--exclude-group-chats" in capsys.readouterr().err


def test_parser_rejects_both_group_policies() -> None:
    with pytest.raises(SystemExit) as caught:
        A.build_arg_parser().parse_args(
            _required_cli("--include-group-chats", "--exclude-group-chats")
        )
    assert caught.value.code == 2


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


def test_module_exposes_live_acquisition_and_portable_validation() -> None:
    assert callable(A.run)
    assert callable(A.validate_atomic_run)
    assert callable(A.mint_live_smoke_receipt)


def test_main_requires_live_acquisition_arguments(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        A.main(_required_cli("--exclude-group-chats"))
    assert caught.value.code == 2
    assert "live acquisition requires" in capsys.readouterr().err


def test_standalone_validate_mode_needs_no_acquisition_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    run_dir = tmp_path / "completed-run"
    observed: list[Path] = []
    monkeypatch.setattr(
        A,
        "validate_atomic_run",
        lambda path: observed.append(path) or {
            "status": "closed",
            "retained_rows": 1,
        },
    )
    parsed = A.build_arg_parser().parse_args(["--validate-run", str(run_dir)])
    assert parsed.validate_run == run_dir
    assert parsed.include_group_chats is None
    assert parsed.timezone is None
    assert parsed.hmac_key is None
    assert A.main(["--validate-run", str(run_dir)]) == 0
    assert observed == [run_dir]
    assert '"status": "closed"' in capsys.readouterr().out


def test_standalone_mint_mode_needs_only_action_specific_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "acquisition-receipt.json"
    destination = tmp_path / "imessage-atomic-live-smoke-receipt.json"
    observed: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        A,
        "mint_live_smoke_receipt",
        lambda first, second: observed.append((first, second)) or {},
    )
    argv = [
        "--mint-live-smoke-receipt",
        "--smoke-run-receipt",
        str(source),
        "--receipt-out",
        str(destination),
    ]
    parsed = A.build_arg_parser().parse_args(argv)
    assert parsed.mint_live_smoke_receipt is True
    assert parsed.timezone is None
    assert A.main(argv) == 0
    assert observed == [(source, destination)]


def test_parser_rejects_multiple_standalone_actions(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as caught:
        A.build_arg_parser().parse_args([
            "--validate-run",
            str(tmp_path / "run"),
            "--mint-live-smoke-receipt",
        ])
    assert caught.value.code == 2


def test_main_refuses_live_acquisition_off_macos_before_source_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(A, "load_hmac_key", lambda _path: KEY)
    monkeypatch.setattr(A.sys, "platform", "win32")
    argv = _required_cli("--exclude-group-chats") + [
        "--source-db", str(tmp_path / "must-not-be-read.db"),
        "--output-root", str(tmp_path / "output"),
        "--run-id", "run-1", "--persona", "joshua",
        "--author", "Joshua Miller", "--register", "personal",
        "--max-retained", "1",
    ]
    with pytest.raises(A.AtomicAcquisitionError, match="only on the macOS host"):
        A.main(argv)
    assert not (tmp_path / "must-not-be-read.db").exists()


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


def test_snapshot_verification_refuses_unbound_sidecar(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    hostile = snapshot.with_name(snapshot.name + "-wal")
    hostile.write_bytes(b"unbound-sidecar")
    with pytest.raises(A.SnapshotError, match="unexpected SQLite sidecars"):
        A.verify_snapshot(snapshot, metadata)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_snapshot_verification_refuses_dangling_sidecar_symlink(
    tmp_path: Path, suffix: str
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    hostile = snapshot.with_name(snapshot.name + suffix)
    try:
        os.symlink("missing-target", hostile)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    assert hostile.is_symlink() and not hostile.exists()
    with pytest.raises(A.SnapshotError, match="unexpected SQLite sidecars"):
        A.verify_snapshot(snapshot, metadata)


def test_snapshot_reopens_read_only_and_detects_mutation(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    read_only = A._open_read_only_database(snapshot)
    assert read_only.execute("PRAGMA query_only").fetchone() == (1,)
    read_only.close()
    A.verify_snapshot(snapshot, metadata)
    with snapshot.open("ab") as handle:
        handle.write(b"drift")
    with pytest.raises(A.SnapshotError, match="hash or size drifted"):
        A.verify_snapshot(snapshot, metadata)


def test_snapshot_requires_new_private_staging(tmp_path: Path) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    outside = tmp_path / "public" / "run.staging"
    with pytest.raises(A.SnapshotError, match="outside the required private root"):
        A.materialize_consistent_snapshot(source, outside)
    staging = _private_staging(tmp_path)
    staging.mkdir()
    with pytest.raises(A.SnapshotError, match="already exists"):
        A.materialize_consistent_snapshot(source, staging)


def test_snapshot_backup_accepts_exact_precreated_private_staging(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.execute("INSERT INTO chat VALUES (1, 'guid', 'alias', NULL, 45)")
    conn.commit()
    conn.close()
    staging = _private_staging(tmp_path)
    staging.mkdir(mode=0o700)
    snapshot, metadata = A._materialize_consistent_snapshot_in_precreated_staging(
        source, staging
    )
    assert snapshot == staging / A.SNAPSHOT_FILENAME
    assert tuple(path.name for path in staging.iterdir()) == (A.SNAPSHOT_FILENAME,)
    A.verify_snapshot(snapshot, metadata)


def test_snapshot_backup_refuses_nonempty_precreated_staging_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    staging = _private_staging(tmp_path)
    staging.mkdir(mode=0o700)
    foreign = staging / "foreign"
    foreign.write_bytes(b"leave-me-alone")
    with pytest.raises(A.SnapshotError, match="not empty"):
        A._materialize_consistent_snapshot_in_precreated_staging(source, staging)
    assert foreign.read_bytes() == b"leave-me-alone"
    assert not (staging / A.SNAPSHOT_FILENAME).exists()


def test_snapshot_rejects_parent_traversal_out_of_private_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir()
    escaped = private_root / ".." / "escaped-public" / "run.staging"
    with pytest.raises(A.SnapshotError, match="parent traversal"):
        A.materialize_consistent_snapshot(source, escaped)
    assert not (tmp_path / "escaped-public").exists()


def test_snapshot_hashing_never_uses_read_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("whole-file read forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )
    assert snapshot.is_file()
    A.verify_snapshot(snapshot, metadata)


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


def test_failed_backup_leaves_securely_precreated_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "chat.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.commit()
    conn.close()

    class FailingSource:
        def backup(self, destination, **kwargs):
            raise sqlite3.OperationalError("synthetic backup failure")

        def close(self):
            return None

    monkeypatch.setattr(A, "_open_read_only_database", lambda path: FailingSource())
    staging = _private_staging(tmp_path)
    with pytest.raises(A.SnapshotError, match="backup snapshot failed"):
        A.materialize_consistent_snapshot(source, staging)
    partial = staging / A.SNAPSHOT_FILENAME
    assert partial.is_file()
    if os.name != "nt":
        assert stat.S_IMODE(partial.stat().st_mode) == 0o600


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


@pytest.mark.parametrize(
    "forged",
    [
        lambda real: A.AtomicSchemaInfo(
            schema="forged-schema",
            schema_fingerprint=real.schema_fingerprint,
            reply_column=real.reply_column,
        ),
        lambda real: A.AtomicSchemaInfo(
            schema=real.schema,
            schema_fingerprint=real.schema_fingerprint,
            reply_column="guid",
        ),
        lambda real: A.AtomicSchemaInfo(
            schema=real.schema,
            schema_fingerprint=real.schema_fingerprint,
            reply_column=None,
        ),
    ],
)
def test_candidate_universe_requires_complete_fresh_schema_binding(
    tmp_path: Path, forged
) -> None:
    conn, real = _candidate_fixture(tmp_path / "candidate.db")
    with pytest.raises(A.SchemaPreflightError, match="schema binding drifted"):
        A.discover_candidate_universe(
            conn,
            forged(real),
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )
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


def test_snapshot_discovery_rehashes_after_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "chat.db"
    conn, _ = _candidate_fixture(source)
    conn.close()
    snapshot, metadata = A.materialize_consistent_snapshot(
        source, _private_staging(tmp_path)
    )

    def mutate_during_scan(connection, schema_info, **kwargs):
        connection.close()
        with snapshot.open("ab") as handle:
            handle.write(b"post-scan-drift")
        return _candidate_universe((), ())

    monkeypatch.setattr(A, "discover_candidate_universe", mutate_during_scan)
    with pytest.raises(A.SnapshotError, match="hash or size drifted"):
        A.discover_snapshot_candidate_universe(
            snapshot,
            metadata,
            apple_date_unit="nanoseconds",
            timezone_name="UTC",
            max_messages=3,
        )


def _closed_universe_scan_environment(
    tmp_path: Path,
) -> tuple[
    _FakeTreeOps,
    _FakeTreeNode,
    int,
    Path,
    A.ClosedSnapshotEvidence,
    dict[str, object],
    dict[str, object],
]:
    staging_path = (
        tmp_path / A.PRIVATE_ROOT_COMPONENT / "run.staging"
    ).absolute()
    staging_path.mkdir(parents=True)
    snapshot_path = staging_path / A.SNAPSHOT_FILENAME
    conn, _schema = _candidate_fixture(snapshot_path)
    conn.close()
    raw = snapshot_path.read_bytes()
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    verifier = A._open_read_only_database(snapshot_path)
    try:
        metadata = A._snapshot_metadata_from_hash(
            verifier, file_hash=digest, byte_size=len(raw)
        )
    finally:
        verifier.close()
    parent = _FakeTreeNode("parent", kind="directory")
    staging = _FakeTreeNode("run.staging", kind="directory")
    snapshot = _FakeTreeNode(
        A.SNAPSHOT_FILENAME, kind="file", data=raw
    )
    staging.children[A.SNAPSHOT_FILENAME] = snapshot
    parent.children["run.staging"] = staging
    ops = _FakeTreeOps(parent)
    staging_fd = ops.open("run.staging", os.O_RDONLY, dir_fd=10)
    ops.path_nodes.update(
        {
            staging_path.parent: parent,
            staging_path: staging,
            snapshot_path: snapshot,
        }
    )
    semantic = A.semantic_options_payload(
        since=dt.date(2020, 6, 15),
        until=dt.date(2020, 6, 15),
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="America/New_York",
        preprocessing_version="legacy-preprocess/1",
        preprocessing_rules_id="imessage-atomic-rules/1",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
    )
    controls = A.run_controls_payload(
        max_messages=3,
        max_retained=None,
        allow_empty=False,
        checkpoint_schema="setec-imessage-atomic-checkpoint/2",
        checkpoint_interval=1,
    )
    evidence = A.ClosedSnapshotEvidence(
        metadata=metadata,
        snapshot_identity=A._private_node_identity(_FakeTreeStat(snapshot)),
        staging_identity=A._private_node_identity(_FakeTreeStat(staging)),
        snapshot_device_inode=(7, snapshot.inode),
        staging_device_inode=(7, staging.inode),
        inventory=(A.SNAPSHOT_FILENAME,),
    )
    return (
        ops,
        staging,
        staging_fd,
        staging_path,
        evidence,
        semantic,
        controls,
    )


def _test_universe_connection_binder(
    opener: object,
    path: Path,
    _expected_device_inode: tuple[int, int],
    _label: str,
):
    return opener(path)  # type: ignore[operator]


def test_pinned_closed_snapshot_universe_scan_uses_canonical_controls(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    evidence, schema, universe = A._discover_closed_snapshot_universe_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        closed,
        expected_staging_device_inode=(7, staging.inode),
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _connection_binder=_test_universe_connection_binder,
    )
    assert evidence.metadata == closed.metadata
    assert evidence.snapshot_device_inode == (
        7,
        staging.children[A.SNAPSHOT_FILENAME].inode,
    )
    assert schema.schema_fingerprint == closed.metadata.schema_fingerprint
    assert universe.candidate_outgoing_rows == 3
    assert universe.selected_outgoing_rows == 3
    assert [item.message_guid for item in universe.selected] == [
        "message-guid-a",
        "message-guid-b",
        "message-guid-c",
    ]
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_group_policy_does_not_filter(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    included = dict(semantic)
    included["group_policy"] = "include_group_chats"
    excluded_result = A._discover_closed_snapshot_universe_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        closed,
        expected_staging_device_inode=(7, staging.inode),
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _connection_binder=_test_universe_connection_binder,
    )[2]
    included_result = A._discover_closed_snapshot_universe_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        closed,
        expected_staging_device_inode=(7, staging.inode),
        semantic_options=included,
        run_controls=controls,
        _ops=ops,
        _connection_binder=_test_universe_connection_binder,
    )[2]
    assert included_result == excluded_result
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_rejects_postscan_drift_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    original = A.discover_candidate_universe

    def mutate_after_scan(*args: object, **kwargs: object):
        universe = original(*args, **kwargs)
        snapshot = staging.children[A.SNAPSHOT_FILENAME]
        snapshot.data += b"drift"
        snapshot.version += 1
        return universe

    monkeypatch.setattr(A, "discover_candidate_universe", mutate_after_scan)
    with pytest.raises(A.BootstrapStateError):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=_test_universe_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_rejects_restored_inventory_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    original = A.discover_candidate_universe

    def mutate_directory_history(*args: object, **kwargs: object):
        universe = original(*args, **kwargs)
        transient = _FakeTreeNode("transient", kind="file")
        staging.children["transient"] = transient
        staging.children.pop("transient")
        staging.version += 1
        return universe

    monkeypatch.setattr(A, "discover_candidate_universe", mutate_directory_history)
    with pytest.raises(A.BootstrapStateError, match="full identity drifted"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=_test_universe_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_requires_query_only_and_closes(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )

    class NotQueryOnly:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(self, sql: str):
            if " ".join(sql.split()).lower() == "pragma query_only":
                return _FakeRows([(0,)])
            return self.connection.execute(sql)

        def close(self) -> None:
            self.connection.close()

    def not_query_only_binder(
        opener: object,
        path: Path,
        _expected_device_inode: tuple[int, int],
        _label: str,
    ) -> NotQueryOnly:
        return NotQueryOnly(opener(path))  # type: ignore[operator]

    with pytest.raises(A.BootstrapStateError, match="query-only"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=not_query_only_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_requires_prior_full_identity(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    drifted = replace(
        closed,
        snapshot_identity=(
            *closed.snapshot_identity[:3],
            closed.snapshot_identity[3] + 1,
            *closed.snapshot_identity[4:],
        ),
    )
    with pytest.raises(A.BootstrapStateError, match="identity drifted"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            drifted,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=_test_universe_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_refuses_extra_inventory(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    staging.children[A.SNAPSHOT_FILENAME + "-wal"] = _FakeTreeNode(
        "foreign-wal", kind="file"
    )
    with pytest.raises(A.BootstrapStateError, match="inventory"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=_test_universe_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_accepts_authorized_stage_inventory(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    staging.children[A.SEMANTIC_OPTIONS_FILENAME] = _FakeTreeNode(
        "semantic-options", kind="file", data=b"{}\n"
    )
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, A.SEMANTIC_OPTIONS_FILENAME)
    )
    staged = replace(
        closed,
        staging_identity=A._private_node_identity(ops.fstat(staging_fd)),
        inventory=inventory,
    )
    result = A._discover_closed_snapshot_universe_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        staged,
        expected_staging_device_inode=(7, staging.inode),
        expected_staging_names=inventory,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _connection_binder=_test_universe_connection_binder,
    )
    assert result[0].inventory == inventory
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_preserves_discovery_error_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    monkeypatch.setattr(
        A,
        "discover_candidate_universe",
        lambda *_a, **_k: (_ for _ in ()).throw(
            A.StableGuidError("synthetic private identity class")
        ),
    )
    with pytest.raises(A.StableGuidError, match="identity class"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=_test_universe_connection_binder,
        )
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def test_pinned_closed_snapshot_universe_scan_connection_close_is_single_attempt(
    tmp_path: Path,
) -> None:
    ops, staging, staging_fd, staging_path, closed, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    close_calls = 0

    class CloseFailsOnce:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def execute(self, sql: str):
            return self.connection.execute(sql)

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1
            self.connection.close()
            raise OSError("synthetic connection close failure")

    def close_failure_binder(
        opener: object,
        path: Path,
        _expected_device_inode: tuple[int, int],
        _label: str,
    ) -> CloseFailsOnce:
        return CloseFailsOnce(opener(path))  # type: ignore[operator]

    with pytest.raises(A.BootstrapStateError, match="cannot scan"):
        A._discover_closed_snapshot_universe_at(
            10,
            staging_fd,
            "run.staging",
            staging_path,
            closed,
            expected_staging_device_inode=(7, staging.inode),
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _connection_binder=close_failure_binder,
        )
    assert close_calls == 1
    assert set(ops.nodes) == {10, staging_fd}
    ops.close(staging_fd)


def _universe_close_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    A.PreparedSnapshotClosed,
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    ops, staging, staging_fd, staging_path, evidence, semantic, controls = (
        _closed_universe_scan_environment(tmp_path)
    )
    scan_result = A._discover_closed_snapshot_universe_at(
        10,
        staging_fd,
        "run.staging",
        staging_path,
        evidence,
        expected_staging_device_inode=(7, staging.inode),
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _connection_binder=_test_universe_connection_binder,
    )
    derived_staging = A.bootstrap_staging_name("run-final")
    staging_node = ops.nodes[10].children.pop("run.staging")
    staging_node.label = derived_staging
    ops.nodes[10].children[derived_staging] = staging_node
    old_staging_path = staging_path
    staging_path = staging_path.with_name(derived_staging)
    ops.path_nodes.pop(old_staging_path)
    ops.path_nodes[staging_path] = staging_node
    ops.path_nodes[staging_path / A.SNAPSHOT_FILENAME] = staging_node.children[
        A.SNAPSHOT_FILENAME
    ]
    reserved = A.bootstrap_journal_payload(
        state="reserved",
        previous_journal_digest=None,
        staging_name=derived_staging,
        final_name="run-final",
        semantic_options_digest=A.canonical_payload_digest(semantic),
        run_controls_digest=A.canonical_payload_digest(controls),
        smoke_policy_digest=None,
        hmac_key_id_value=A.hmac_key_id(KEY),
        snapshot_metadata=None,
        universe_binding=None,
        completed_artifacts={},
    )
    staging_created = A._empty_bootstrap_successor(
        reserved, A.canonical_payload_digest(reserved), "staging_created"
    )
    in_progress = A._empty_bootstrap_successor(
        staging_created,
        A.canonical_payload_digest(staging_created),
        "snapshot_in_progress",
    )
    closed = _integrator_closed_payload(in_progress, evidence)
    prepared = A.PreparedSnapshotClosed(
        journal=closed,
        journal_digest=A.canonical_payload_digest(closed),
        staging_fd=staging_fd,
        staging_identity=evidence.staging_identity,
        evidence=evidence,
    )
    journal_name = A.bootstrap_journal_name("run-final")
    ops.nodes[10].children[journal_name] = _FakeTreeNode(
        "journal", kind="file"
    )
    holder: list[dict[str, object]] = [closed]
    advances: list[dict[str, object]] = []

    def read_journal(*_args: object, **_kwargs: object):
        payload = holder[0]
        return (
            payload,
            (1, 2, 3, 4, 5),
            A.canonical_payload_digest(payload),
        )

    def advance(
        _parent_fd: int,
        _journal_name: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> str:
        A.validate_bootstrap_transition(holder[0], payload)
        holder[0] = payload
        advances.append(payload)
        return A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", read_journal)
    monkeypatch.setattr(A, "_advance_bootstrap_journal_locked_at", advance)
    monkeypatch.setattr(A, "_verify_bootstrap_lock_held_at", lambda *_a, **_k: (1, 2, 3, 4, 5))
    return (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        holder,
        advances,
    )


def test_close_bootstrap_universe_publishes_one_bound_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    result = A._close_bootstrap_universe_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_snapshot=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _scanner=lambda *_a, **_k: scan_result,
    )
    assert len(advances) == 1
    assert result.journal["state"] == "universe_closed"
    assert result.journal["previous_journal_digest"] == prepared.journal_digest
    assert result.journal["completed_artifacts"] == prepared.journal[
        "completed_artifacts"
    ]
    assert result.journal["universe_binding"] == (
        result.evidence.initialization.universe_binding
    )
    assert result.journal["smoke_policy_digest"] == (
        result.evidence.initialization.artifact(A.SMOKE_POLICY_FILENAME).digest
    )
    assert tuple(ops.nodes[result.staging_fd].children) == (
        A.SNAPSHOT_FILENAME,
    )
    assert result.staging_fd in ops.nodes
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_close_bootstrap_universe_input_drift_refuses_and_consumes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    drifted = dict(semantic)
    drifted["persona"] = "different-persona"
    with pytest.raises(A.BootstrapStateError, match="input binding"):
        A._close_bootstrap_universe_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_snapshot=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=drifted,
            run_controls=controls,
            _ops=ops,
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert advances == []
    assert set(ops.nodes) == {10}


def test_close_bootstrap_universe_scan_failure_is_ordinary_and_consumes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        _scan_result,
        _holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)

    def fail_scan(*_args: object, **_kwargs: object):
        raise A.SchemaPreflightError("synthetic scan refusal")

    with pytest.raises(A.SchemaPreflightError, match="scan refusal") as failure:
        A._close_bootstrap_universe_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_snapshot=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _scanner=fail_scan,
        )
    assert type(failure.value) is A.SchemaPreflightError
    assert advances == []
    assert set(ops.nodes) == {10}


def test_close_bootstrap_universe_duplicate_closure_refuses_before_cas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)

    def duplicate_builder(**kwargs: object) -> A.InitializationClosure:
        closure = A.build_initialization_closure(**kwargs)  # type: ignore[arg-type]
        return replace(
            closure,
            artifacts=closure.artifacts + (closure.artifacts[0],),
        )

    with pytest.raises(A.BootstrapStateError, match="artifacts drifted"):
        A._close_bootstrap_universe_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_snapshot=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _scanner=lambda *_a, **_k: scan_result,
            _closure_builder=duplicate_builder,
        )
    assert advances == []
    assert set(ops.nodes) == {10}


def test_close_bootstrap_universe_postpublish_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    reads = 0

    def fail_postpublish_read(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        if advances:
            raise A.BootstrapStateError("synthetic postpublish read")
        payload = holder[0]
        return (
            payload,
            (1, 2, 3, 4, 5),
            A.canonical_payload_digest(payload),
        )

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", fail_postpublish_read)
    with pytest.raises(A.BootstrapRecoveryRequired, match="published"):
        A._close_bootstrap_universe_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_snapshot=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert len(advances) == 1
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    ("phase", "expected_error", "expected_match", "expected_advances"),
    [
        ("prepublish", A.BootstrapStateError, "final name", 0),
        ("postpublish", A.BootstrapRecoveryRequired, "published", 1),
    ],
)
def test_close_bootstrap_universe_final_name_races_are_checkpointed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    expected_error: type[Exception],
    expected_match: str,
    expected_advances: int,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    parent = ops.nodes[10]
    parent_fsyncs = 0

    def insert_final(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal parent_fsyncs
        if node is not parent:
            return
        parent_fsyncs += 1
        target = 1 if phase == "prepublish" else 2
        if parent_fsyncs == target:
            parent.children["run-final"] = _FakeTreeNode(
                "late-final", kind="directory"
            )

    ops.on_fsync = insert_final
    with pytest.raises(expected_error, match=expected_match):
        A._close_bootstrap_universe_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_snapshot=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert len(advances) == expected_advances
    assert set(ops.nodes) == {10}


def _closed_universe_resume_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        holder,
        advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    closed = A._close_bootstrap_universe_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_snapshot=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _scanner=lambda *_a, **_k: scan_result,
    )
    ops.close(closed.staging_fd)
    assert set(ops.nodes) == {10}
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    )


def test_resume_bootstrap_universe_closed_reconstructs_without_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        _holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("verify-only resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_create_or_verify_private_json_at",
        lambda *_a, **_k: pytest.fail("verify-only resume must not write artifacts"),
    )
    result = A._resume_bootstrap_universe_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: scan_result[0],
        _scanner=lambda *_a, **_k: scan_result,
    )
    assert len(advances) == 1
    assert result.journal["state"] == "universe_closed"
    assert result.journal["smoke_policy_digest"] == (
        result.evidence.initialization.artifact(A.SMOKE_POLICY_FILENAME).digest
    )
    assert result.journal["universe_binding"] == (
        result.evidence.initialization.universe_binding
    )
    assert tuple(ops.nodes[result.staging_fd].children) == (
        A.SNAPSHOT_FILENAME,
    )
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_universe_closed_rejects_journal_reconstruction_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    drifted = dict(holder[0])
    drifted["smoke_policy_digest"] = "sha256:" + "0" * 64
    holder[0] = drifted
    with pytest.raises(A.BootstrapStateError, match="reconstruction drifted"):
        A._resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: scan_result[0],
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert len(advances) == 1
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_universe_closed_rejects_late_journal_drift_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reads = 0

    def drifting_read(*_args: object, **_kwargs: object):
        nonlocal reads
        reads += 1
        payload = holder[0]
        digest = A.canonical_payload_digest(payload)
        if reads == 2:
            digest = "sha256:" + "0" * 64
        return payload, (1, 2, 3, 4, 5), digest

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", drifting_read)
    with pytest.raises(A.BootstrapStateError, match="journal changed"):
        A._resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: scan_result[0],
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert len(advances) == 1
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("seal_number", [1, 2])
def test_resume_bootstrap_universe_closed_final_race_refuses_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seal_number: int
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        _holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    parent = ops.nodes[10]
    parent_fsyncs = 0

    def insert_final(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal parent_fsyncs
        if node is parent:
            parent_fsyncs += 1
        if node is parent and parent_fsyncs == seal_number:
            parent.children["run-final"] = _FakeTreeNode(
                "late-final", kind="directory"
            )

    ops.on_fsync = insert_final
    with pytest.raises(A.BootstrapStateError, match="final name"):
        A._resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: scan_result[0],
            _scanner=lambda *_a, **_k: scan_result,
        )
    assert len(advances) == 1
    assert set(ops.nodes) == {10}


def _universe_integration_reserved(
    journal: dict[str, object],
) -> dict[str, object]:
    return A.bootstrap_journal_payload(
        state="reserved",
        previous_journal_digest=None,
        staging_name=journal["staging_name"],  # type: ignore[arg-type]
        final_name=journal["final_name"],  # type: ignore[arg-type]
        semantic_options_digest=journal["semantic_options_digest"],  # type: ignore[arg-type]
        run_controls_digest=journal["run_controls_digest"],  # type: ignore[arg-type]
        smoke_policy_digest=None,
        hmac_key_id_value=journal["hmac_key_id"],  # type: ignore[arg-type]
        snapshot_metadata=None,
        universe_binding=None,
        completed_artifacts={},
    )


def _prepared_universe_integration_result(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    semantic: dict[str, object],
    controls: dict[str, object],
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
) -> A.PreparedUniverseClosed:
    evidence, schema_info, universe = scan_result
    closure = A.build_initialization_closure(
        snapshot_metadata=evidence.metadata,
        schema_info=schema_info,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    staging_fd = ops.open(
        journal["staging_name"],  # type: ignore[arg-type]
        os.O_RDONLY,
        dir_fd=10,
    )
    return A.PreparedUniverseClosed(
        journal=journal,  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(journal),
        staging_fd=staging_fd,
        staging_identity=evidence.staging_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
    )


@pytest.mark.parametrize(
    "initial_state",
    [
        None,
        "reserved",
        "staging_created",
        "snapshot_in_progress",
        "snapshot_closed",
    ],
)
def test_integrate_bootstrap_universe_routes_all_early_states_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        holder,
        _advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(prepared.journal)
    staging_created = A._empty_bootstrap_successor(
        reserved, A.canonical_payload_digest(reserved), "staging_created"
    )
    in_progress = A._empty_bootstrap_successor(
        staging_created,
        A.canonical_payload_digest(staging_created),
        "snapshot_in_progress",
    )
    states = {
        "reserved": reserved,
        "staging_created": staging_created,
        "snapshot_in_progress": in_progress,
        "snapshot_closed": prepared.journal,
    }
    journal_name = A.bootstrap_journal_name("run-final")
    if initial_state is None:
        ops.nodes[10].children.pop(journal_name)
    else:
        holder[0] = states[initial_state]
    calls: list[str] = []

    def integrate(*_args: object, **_kwargs: object) -> A.PreparedSnapshotClosed:
        calls.append("snapshot")
        holder[0] = prepared.journal
        ops.nodes[10].children.setdefault(
            journal_name, _FakeTreeNode("journal", kind="file")
        )
        return prepared

    def close(*args: object, **kwargs: object) -> A.PreparedUniverseClosed:
        calls.append("closer")
        return A._close_bootstrap_universe_locked_at(
            *args,
            **kwargs,
            _scanner=lambda *_a, **_k: scan_result,
        )

    result = A._prepare_or_resume_bootstrap_universe_closed_locked_at(
        10,
        journal_name,
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_integrator=integrate,
        _universe_closer=close,
        _universe_resumer=lambda *_a, **_k: pytest.fail(
            "early states must not use the universe resumer"
        ),
    )
    assert calls == ["snapshot", "closer"]
    assert result.journal["state"] == "universe_closed"
    assert result.staging_fd == prepared.staging_fd
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_resumes_exact_authority_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []

    def resume(*_args: object, **_kwargs: object) -> A.PreparedUniverseClosed:
        calls.append("resume")
        return _prepared_universe_integration_result(
            ops, holder[0], semantic, controls, scan_result
        )

    result = A._prepare_or_resume_bootstrap_universe_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_integrator=lambda *_a, **_k: pytest.fail(
            "universe_closed must not integrate a snapshot"
        ),
        _universe_closer=lambda *_a, **_k: pytest.fail(
            "universe_closed must not invoke the closer"
        ),
        _universe_resumer=resume,
    )
    assert calls == ["resume"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_rejects_alternate_resumer_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    alternate = dict(holder[0])
    alternate["previous_journal_digest"] = "sha256:" + "0" * 64
    with pytest.raises(A.BootstrapStateError, match="authority changed"):
        A._prepare_or_resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _universe_resumer=lambda *_a, **_k: (
                _prepared_universe_integration_result(
                    ops, alternate, semantic, controls, scan_result
                )
            ),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_rejects_unrelated_resumer_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])

    def unrelated(*_args: object, **_kwargs: object) -> A.PreparedUniverseClosed:
        result = _prepared_universe_integration_result(
            ops, holder[0], semantic, controls, scan_result
        )
        ops.close(result.staging_fd)
        other = _FakeTreeNode("other-private", kind="directory")
        ops.nodes[10].children["other-private"] = other
        other_fd = ops.open("other-private", os.O_RDONLY, dir_fd=10)
        return replace(result, staging_fd=other_fd)

    with pytest.raises(A.BootstrapStateError, match="descriptor identity"):
        A._prepare_or_resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _universe_resumer=unrelated,
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_rejects_unsequenced_closer_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        _advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(prepared.journal)

    def close(*args: object, **kwargs: object) -> A.PreparedUniverseClosed:
        result = A._close_bootstrap_universe_locked_at(
            *args,
            **kwargs,
            _scanner=lambda *_a, **_k: scan_result,
        )
        drifted = dict(result.journal)
        drifted["previous_journal_digest"] = "sha256:" + "0" * 64
        return replace(
            result,
            journal=drifted,
            journal_digest=A.canonical_payload_digest(drifted),
        )

    with pytest.raises(A.BootstrapRecoveryRequired, match="published"):
        A._prepare_or_resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_integrator=lambda *_a, **_k: prepared,
            _universe_closer=close,
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_requires_exact_closer_fd_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        prepared,
        scan_result,
        _holder,
        _advances,
    ) = _universe_close_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(prepared.journal)

    def close(*args: object, **kwargs: object) -> A.PreparedUniverseClosed:
        result = A._close_bootstrap_universe_locked_at(
            *args,
            **kwargs,
            _scanner=lambda *_a, **_k: scan_result,
        )
        ops.close(result.staging_fd)
        replacement_fd = ops.open(
            result.journal["staging_name"], os.O_RDONLY, dir_fd=10
        )
        return replace(result, staging_fd=replacement_fd)

    with pytest.raises(A.BootstrapRecoveryRequired, match="published"):
        A._prepare_or_resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_integrator=lambda *_a, **_k: prepared,
            _universe_closer=close,
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_universe_refuses_later_state_before_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    evidence, schema_info, universe = scan_result
    closure = A.build_initialization_closure(
        snapshot_metadata=evidence.metadata,
        schema_info=schema_info,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    completed = dict(holder[0]["completed_artifacts"])  # type: ignore[arg-type]
    for artifact in closure.artifacts[:-1]:
        completed[artifact.filename] = artifact.digest
    holder[0] = A.bootstrap_journal_payload(
        state="options_maps_closed",
        previous_journal_digest=A.canonical_payload_digest(holder[0]),
        staging_name=holder[0]["staging_name"],  # type: ignore[arg-type]
        final_name=holder[0]["final_name"],  # type: ignore[arg-type]
        semantic_options_digest=holder[0]["semantic_options_digest"],  # type: ignore[arg-type]
        run_controls_digest=holder[0]["run_controls_digest"],  # type: ignore[arg-type]
        smoke_policy_digest=holder[0]["smoke_policy_digest"],  # type: ignore[arg-type]
        hmac_key_id_value=holder[0]["hmac_key_id"],  # type: ignore[arg-type]
        snapshot_metadata=holder[0]["snapshot_metadata"],  # type: ignore[arg-type]
        universe_binding=holder[0]["universe_binding"],  # type: ignore[arg-type]
        completed_artifacts=completed,
    )
    with pytest.raises(A.BootstrapStateError, match="not resumable"):
        A._prepare_or_resume_bootstrap_universe_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_integrator=lambda *_a, **_k: pytest.fail(
                "later state must not invoke snapshot integration"
            ),
            _universe_closer=lambda *_a, **_k: pytest.fail(
                "later state must not invoke the universe closer"
            ),
            _universe_resumer=lambda *_a, **_k: pytest.fail(
                "later state must not invoke the universe resumer"
            ),
        )
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    "prefix_length", range(len(A.INITIALIZATION_DEPENDENCY_FILENAMES) + 1)
)
def test_resume_universe_for_options_maps_accepts_every_exact_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix_length: int,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    closure = A.build_initialization_closure(
        snapshot_metadata=evidence.metadata,
        schema_info=schema_info,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    prefix = A.INITIALIZATION_DEPENDENCY_FILENAMES[:prefix_length]
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    for name in prefix:
        staging.children[name] = _FakeTreeNode(
            name, kind="file", data=closure.artifact(name).raw
        )
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *prefix)
    )
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(ops.stat(
            holder[0]["staging_name"], dir_fd=10  # type: ignore[arg-type]
        )),
        inventory=inventory,
    )
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("verify-only prefix resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_create_or_verify_private_json_at",
        lambda *_a, **_k: pytest.fail("verify-only prefix resume must not write"),
    )

    def reread(
        _staging_fd: int,
        rebuilt: A.InitializationClosure,
        observed_prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        assert observed_prefix == prefix
        return {
            name: (
                rebuilt.artifact(name).digest,
                rebuilt.artifact(name).raw,
            )
            for name in prefix
        }

    result = A._resume_bootstrap_universe_for_options_maps_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _prefix_rereader=reread,
    )
    assert result.evidence.snapshot_evidence.inventory == inventory
    assert tuple(ops.nodes[result.staging_fd].children) == (
        A.SNAPSHOT_FILENAME,
        *prefix,
    )
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_resume_universe_for_options_maps_rejects_residue_evidence_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    closure = A.build_initialization_closure(
        snapshot_metadata=evidence.metadata,
        schema_info=schema_info,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    name = A.SEMANTIC_OPTIONS_FILENAME
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    staging.children[name] = _FakeTreeNode(
        name, kind="file", data=closure.artifact(name).raw
    )
    inventory = A._closed_staging_inventory_names((A.SNAPSHOT_FILENAME, name))
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(ops.stat(
            holder[0]["staging_name"], dir_fd=10  # type: ignore[arg-type]
        )),
        inventory=inventory,
    )
    with pytest.raises(A.BootstrapStateError, match="evidence drifted"):
        A._resume_bootstrap_universe_for_options_maps_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (
                staged_evidence,
                schema_info,
                universe,
            ),
            _prefix_rereader=lambda *_a, **_k: {
                name: ("sha256:" + "0" * 64, closure.artifact(name).raw)
            },
        )
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    "prefix_length", range(len(A.INITIALIZATION_DEPENDENCY_FILENAMES) + 1)
)
def test_close_bootstrap_options_maps_adopts_prefix_and_publishes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prefix_length: int,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    closure = A.build_initialization_closure(
        snapshot_metadata=evidence.metadata,
        schema_info=schema_info,
        universe=universe,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
    )
    prefix = A.INITIALIZATION_DEPENDENCY_FILENAMES[:prefix_length]
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    for name in prefix:
        staging.children[name] = _FakeTreeNode(
            name, kind="file", data=closure.artifact(name).raw
        )
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *prefix)
    )
    current_identity = A._private_node_identity(
        ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=current_identity,
        inventory=inventory,
    )
    staging_fd = ops.open(
        holder[0]["staging_name"], os.O_RDONLY, dir_fd=10  # type: ignore[arg-type]
    )
    prepared = A.PreparedUniverseClosed(
        journal=holder[0],  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(holder[0]),
        staging_fd=staging_fd,
        staging_identity=current_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=staged_evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
    )
    created: list[str] = []

    def writer(parent_fd: int, closed: A.ClosedPrivateJson) -> str:
        created.append(closed.filename)
        node = ops.nodes[parent_fd]
        assert closed.filename not in node.children
        node.children[closed.filename] = _FakeTreeNode(
            closed.filename, kind="file", data=closed.raw
        )
        return closed.digest

    def reread(
        parent_fd: int,
        rebuilt: A.InitializationClosure,
        observed_prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        node = ops.nodes[parent_fd]
        result: dict[str, tuple[str, bytes]] = {}
        for name in observed_prefix:
            closed = rebuilt.artifact(name)
            assert node.children[name].data == closed.raw
            result[name] = (closed.digest, closed.raw)
        return result

    result = A._close_bootstrap_options_maps_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_universe=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _writer=writer,
        _prefix_rereader=reread,
    )
    assert created == list(A.INITIALIZATION_DEPENDENCY_FILENAMES[prefix_length:])
    assert result.journal["state"] == "options_maps_closed"
    assert len(advances) == 2
    assert advances[-1] == result.journal
    assert result.journal["completed_artifacts"] == {
        A.SNAPSHOT_FILENAME: evidence.metadata.file_sha256,
        **{
            name: closure.artifact(name).digest
            for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
        },
    }
    assert A.RUN_OWNER_FILENAME not in ops.nodes[result.staging_fd].children
    assert set(ops.nodes[result.staging_fd].children) == {
        A.SNAPSHOT_FILENAME,
        *A.INITIALIZATION_DEPENDENCY_FILENAMES,
    }
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_close_bootstrap_options_maps_writer_drift_is_recovery_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    prepared = _prepared_universe_integration_result(
        ops, holder[0], semantic, controls, scan_result
    )

    def writer(parent_fd: int, closed: A.ClosedPrivateJson) -> str:
        ops.nodes[parent_fd].children[closed.filename] = _FakeTreeNode(
            closed.filename, kind="file", data=closed.raw
        )
        return "sha256:" + "0" * 64

    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._close_bootstrap_options_maps_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_universe=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _writer=writer,
            _prefix_rereader=lambda *_a, **_k: {},
        )
    assert len(advances) == 1
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    assert set(staging.children) == {
        A.SNAPSHOT_FILENAME,
        A.SEMANTIC_OPTIONS_FILENAME,
    }
    assert set(ops.nodes) == {10}


def _closed_options_maps_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    prepared = _prepared_universe_integration_result(
        ops, holder[0], semantic, controls, scan_result
    )
    closure = prepared.evidence.initialization

    def writer(parent_fd: int, closed: A.ClosedPrivateJson) -> str:
        ops.nodes[parent_fd].children[closed.filename] = _FakeTreeNode(
            closed.filename, kind="file", data=closed.raw
        )
        return closed.digest

    def reread(
        _parent_fd: int,
        rebuilt: A.InitializationClosure,
        prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        return {
            name: (rebuilt.artifact(name).digest, rebuilt.artifact(name).raw)
            for name in prefix
        }

    closed = A._close_bootstrap_options_maps_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_universe=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _writer=writer,
        _prefix_rereader=reread,
    )
    ops.close(closed.staging_fd)
    assert set(ops.nodes) == {10}
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    )


def test_resume_bootstrap_options_maps_closed_reconstructs_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_DEPENDENCY_FILENAMES)
    )
    current_identity = A._private_node_identity(
        ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=current_identity,
        inventory=inventory,
    )
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("options/maps resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_create_or_verify_private_json_at",
        lambda *_a, **_k: pytest.fail("options/maps resume must not write"),
    )

    def reread(
        _parent_fd: int,
        rebuilt: A.InitializationClosure,
        prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        assert prefix == A.INITIALIZATION_DEPENDENCY_FILENAMES
        return {
            name: (rebuilt.artifact(name).digest, rebuilt.artifact(name).raw)
            for name in prefix
        }

    result = A._resume_bootstrap_options_maps_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _dependency_rereader=reread,
    )
    assert len(advances) == 2
    assert result.journal["state"] == "options_maps_closed"
    assert result.dependency_evidence == {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_options_maps_closed_rejects_dependency_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_DEPENDENCY_FILENAMES)
    )
    current_identity = A._private_node_identity(
        ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=current_identity,
        inventory=inventory,
    )
    wrong = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    wrong[A.PRIVATE_CONTACT_MAP_FILENAME] = (
        "sha256:" + "0" * 64,
        closure.artifact(A.PRIVATE_CONTACT_MAP_FILENAME).raw,
    )
    with pytest.raises(A.BootstrapStateError, match="evidence drifted"):
        A._resume_bootstrap_options_maps_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (
                staged_evidence,
                schema_info,
                universe,
            ),
            _dependency_rereader=lambda *_a, **_k: wrong,
        )
    assert set(ops.nodes) == {10}


def _prepared_options_maps_integration_result(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    closure: A.InitializationClosure,
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
    *,
    owner_present: bool = False,
) -> A.PreparedOptionsMapsClosed:
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (
            A.SNAPSHOT_FILENAME,
            *A.INITIALIZATION_DEPENDENCY_FILENAMES,
            *((A.RUN_OWNER_FILENAME,) if owner_present else ()),
        )
    )
    staging_identity = A._private_node_identity(
        ops.stat(journal["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=staging_identity,
        inventory=inventory,
    )
    staging_fd = ops.open(
        journal["staging_name"], os.O_RDONLY, dir_fd=10  # type: ignore[arg-type]
    )
    return A.PreparedOptionsMapsClosed(
        journal=journal,  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(journal),
        staging_fd=staging_fd,
        staging_identity=staging_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=staged_evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
        dependency_evidence={
            name: (closure.artifact(name).digest, closure.artifact(name).raw)
            for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
        },
    )


def _fake_options_maps_closer(
    ops: _FakeTreeOps,
    calls: list[str],
):
    def writer(parent_fd: int, closed: A.ClosedPrivateJson) -> str:
        node = ops.nodes[parent_fd]
        node.children[closed.filename] = _FakeTreeNode(
            closed.filename, kind="file", data=closed.raw
        )
        return closed.digest

    def reread(
        _parent_fd: int,
        closure: A.InitializationClosure,
        prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        return {
            name: (closure.artifact(name).digest, closure.artifact(name).raw)
            for name in prefix
        }

    def close(*args: object, **kwargs: object) -> A.PreparedOptionsMapsClosed:
        calls.append("closer")
        return A._close_bootstrap_options_maps_locked_at(
            *args,
            **kwargs,
            _writer=writer,
            _prefix_rereader=reread,
        )

    return close


def test_integrate_bootstrap_options_maps_resumes_exact_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []

    def resume(*_args: object, **_kwargs: object) -> A.PreparedOptionsMapsClosed:
        calls.append("resume")
        return _prepared_options_maps_integration_result(
            ops, holder[0], closure, scan_result
        )

    result = A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _universe_integrator=lambda *_a, **_k: pytest.fail(
            "closed options/maps must not integrate universe"
        ),
        _prefix_universe_resumer=lambda *_a, **_k: pytest.fail(
            "closed options/maps must not resume universe"
        ),
        _closer=lambda *_a, **_k: pytest.fail(
            "closed options/maps must not invoke closer"
        ),
        _resumer=resume,
    )
    assert calls == ["resume"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_options_maps_routes_universe_prefix_to_closer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_universe_integration_result(
        ops, holder[0], semantic, controls, scan_result
    )
    calls: list[str] = []

    def prefix_resume(*_args: object, **_kwargs: object) -> A.PreparedUniverseClosed:
        calls.append("prefix-resume")
        return prepared

    result = A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _universe_integrator=lambda *_a, **_k: pytest.fail(
            "universe_closed must use prefix-aware resume"
        ),
        _prefix_universe_resumer=prefix_resume,
        _closer=_fake_options_maps_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail(
            "universe_closed must not use options/maps resumer"
        ),
    )
    assert calls == ["prefix-resume", "closer"]
    assert result.journal["state"] == "options_maps_closed"
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize(
    "initial_state",
    [None, "reserved", "staging_created", "snapshot_in_progress", "snapshot_closed"],
)
def test_integrate_bootstrap_options_maps_routes_early_states_through_universe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initial_state: str | None,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    universe_journal = holder[0]
    reserved = _universe_integration_reserved(universe_journal)
    prepared = _prepared_universe_integration_result(
        ops, universe_journal, semantic, controls, scan_result
    )
    staging_created = A._empty_bootstrap_successor(
        reserved, A.canonical_payload_digest(reserved), "staging_created"
    )
    in_progress = A._empty_bootstrap_successor(
        staging_created,
        A.canonical_payload_digest(staging_created),
        "snapshot_in_progress",
    )
    snapshot_closed = _integrator_closed_payload(in_progress, scan_result[0])
    states = {
        "reserved": reserved,
        "staging_created": staging_created,
        "snapshot_in_progress": in_progress,
        "snapshot_closed": snapshot_closed,
    }
    journal_name = A.bootstrap_journal_name("run-final")
    if initial_state is None:
        ops.nodes[10].children.pop(journal_name)
    else:
        holder[0] = states[initial_state]
    calls: list[str] = []

    def integrate(*_args: object, **_kwargs: object) -> A.PreparedUniverseClosed:
        calls.append("universe")
        holder[0] = universe_journal
        ops.nodes[10].children[journal_name] = _FakeTreeNode(
            "journal", kind="file"
        )
        return prepared

    result = A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
        10,
        journal_name,
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _universe_integrator=integrate,
        _prefix_universe_resumer=lambda *_a, **_k: pytest.fail(
            "early state must not use prefix resume"
        ),
        _closer=_fake_options_maps_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail(
            "early state must not use options/maps resumer"
        ),
    )
    assert calls == ["universe", "closer"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_options_maps_refuses_later_state_before_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        _scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    completed = dict(holder[0]["completed_artifacts"])  # type: ignore[arg-type]
    completed[A.RUN_OWNER_FILENAME] = closure.artifact(A.RUN_OWNER_FILENAME).digest
    holder[0] = A.bootstrap_journal_payload(
        state="owner_closed",
        previous_journal_digest=A.canonical_payload_digest(holder[0]),
        staging_name=holder[0]["staging_name"],  # type: ignore[arg-type]
        final_name=holder[0]["final_name"],  # type: ignore[arg-type]
        semantic_options_digest=holder[0]["semantic_options_digest"],  # type: ignore[arg-type]
        run_controls_digest=holder[0]["run_controls_digest"],  # type: ignore[arg-type]
        smoke_policy_digest=holder[0]["smoke_policy_digest"],  # type: ignore[arg-type]
        hmac_key_id_value=holder[0]["hmac_key_id"],  # type: ignore[arg-type]
        snapshot_metadata=holder[0]["snapshot_metadata"],  # type: ignore[arg-type]
        universe_binding=holder[0]["universe_binding"],  # type: ignore[arg-type]
        completed_artifacts=completed,
    )
    with pytest.raises(A.BootstrapStateError, match="not resumable"):
        A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _universe_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _prefix_universe_resumer=lambda *_a, **_k: pytest.fail("must not resume prefix"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=lambda *_a, **_k: pytest.fail("must not resume options/maps"),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_options_maps_rejects_alternate_resumer_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    alternate = dict(holder[0])
    alternate["previous_journal_digest"] = "sha256:" + "0" * 64
    with pytest.raises(A.BootstrapStateError, match="authority changed"):
        A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _resumer=lambda *_a, **_k: (
                _prepared_options_maps_integration_result(
                    ops, alternate, closure, scan_result
                )
            ),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_options_maps_requires_exact_closer_fd_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        holder,
        _advances,
    ) = _closed_universe_resume_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_universe_integration_result(
        ops, holder[0], semantic, controls, scan_result
    )
    calls: list[str] = []
    actual_closer = _fake_options_maps_closer(ops, calls)

    def wrong_transfer(*args: object, **kwargs: object) -> A.PreparedOptionsMapsClosed:
        result = actual_closer(*args, **kwargs)
        ops.close(result.staging_fd)
        replacement_fd = ops.open(
            result.journal["staging_name"], os.O_RDONLY, dir_fd=10
        )
        return replace(result, staging_fd=replacement_fd)

    with pytest.raises(A.BootstrapRecoveryRequired, match="invalid"):
        A._prepare_or_resume_bootstrap_options_maps_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _prefix_universe_resumer=lambda *_a, **_k: prepared,
            _closer=wrong_transfer,
        )
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("owner_present", [False, True])
def test_resume_options_maps_for_owner_accepts_only_exact_optional_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_present: bool,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    if owner_present:
        owner = closure.artifact(A.RUN_OWNER_FILENAME)
        staging.children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
    inventory = A._closed_staging_inventory_names(
        (
            A.SNAPSHOT_FILENAME,
            *A.INITIALIZATION_DEPENDENCY_FILENAMES,
            *((A.RUN_OWNER_FILENAME,) if owner_present else ()),
        )
    )
    current_identity = A._private_node_identity(
        ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=current_identity,
        inventory=inventory,
    )
    dependency_evidence = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    closure_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("owner-stage resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_write_initialization_owner_at",
        lambda *_a, **_k: pytest.fail("owner-stage resume must not write owner"),
    )
    result = A._resume_bootstrap_options_maps_for_owner_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _dependency_rereader=lambda *_a, **_k: dependency_evidence,
        _closure_rereader=lambda *_a, **_k: closure_evidence,
    )
    assert len(advances) == 2
    assert result.evidence.snapshot_evidence.inventory == inventory
    assert result.dependency_evidence == dependency_evidence
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


@pytest.mark.parametrize("owner_present", [False, True])
def test_close_bootstrap_owner_recomputes_adopts_and_publishes_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_present: bool,
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    if owner_present:
        owner = closure.artifact(A.RUN_OWNER_FILENAME)
        staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
        staging.children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
    prepared = _prepared_options_maps_integration_result(
        ops,
        holder[0],
        closure,
        scan_result,
        owner_present=owner_present,
    )
    dependency_evidence = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    writes: list[str] = []

    def write_owner(
        parent_fd: int,
        rebuilt: A.InitializationClosure,
        evidence: dict[str, tuple[str, bytes]],
    ) -> str:
        assert evidence == dependency_evidence
        owner = rebuilt.artifact(A.RUN_OWNER_FILENAME)
        writes.append(owner.filename)
        ops.nodes[parent_fd].children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
        return owner.digest

    result = A._close_bootstrap_owner_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_options_maps=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _owner_writer=write_owner,
        _dependency_rereader=lambda *_a, **_k: dependency_evidence,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert writes == ([] if owner_present else [A.RUN_OWNER_FILENAME])
    assert result.journal["state"] == "owner_closed"
    assert len(advances) == 3
    assert advances[-1] == result.journal
    assert result.journal["completed_artifacts"] == {
        A.SNAPSHOT_FILENAME: scan_result[0].metadata.file_sha256,
        **{
            artifact.filename: artifact.digest
            for artifact in closure.artifacts
        },
    }
    assert set(ops.nodes[result.staging_fd].children) == {
        A.SNAPSHOT_FILENAME,
        *A.INITIALIZATION_ARTIFACT_FILENAMES,
    }
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_close_bootstrap_owner_writer_drift_is_recovery_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    dependency_evidence = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }

    def bad_writer(
        parent_fd: int,
        rebuilt: A.InitializationClosure,
        _evidence: dict[str, tuple[str, bytes]],
    ) -> str:
        owner = rebuilt.artifact(A.RUN_OWNER_FILENAME)
        ops.nodes[parent_fd].children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
        return "sha256:" + "0" * 64

    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._close_bootstrap_owner_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_options_maps=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_writer=bad_writer,
            _dependency_rereader=lambda *_a, **_k: dependency_evidence,
        )
    assert len(advances) == 2
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    assert A.RUN_OWNER_FILENAME in staging.children
    assert set(ops.nodes) == {10}


def test_close_bootstrap_owner_fresh_dependency_drift_is_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    wrong = dict(prepared.dependency_evidence)
    closed = closure.artifact(A.PRIVATE_CONTACT_MAP_FILENAME)
    wrong[closed.filename] = ("sha256:" + "0" * 64, closed.raw)
    writes = 0

    def forbidden_writer(*_args: object, **_kwargs: object) -> str:
        nonlocal writes
        writes += 1
        return closed.digest

    with pytest.raises(A.BootstrapStateError, match="evidence drifted"):
        A._close_bootstrap_owner_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_options_maps=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_writer=forbidden_writer,
            _dependency_rereader=lambda *_a, **_k: wrong,
        )
    assert writes == 0
    assert len(advances) == 2
    assert A.RUN_OWNER_FILENAME not in ops.nodes[10].children[
        holder[0]["staging_name"]  # type: ignore[index]
    ].children
    assert set(ops.nodes) == {10}


def test_close_bootstrap_owner_postpublish_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    dependency_evidence = dict(prepared.dependency_evidence)
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }

    def writer(
        parent_fd: int,
        rebuilt: A.InitializationClosure,
        _evidence: dict[str, tuple[str, bytes]],
    ) -> str:
        owner = rebuilt.artifact(A.RUN_OWNER_FILENAME)
        ops.nodes[parent_fd].children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
        return owner.digest

    def fail_after_publish(*_args: object, **_kwargs: object):
        if len(advances) >= 3:
            raise A.BootstrapStateError("synthetic owner postpublish read")
        payload = holder[0]
        return payload, (1, 2, 3, 4, 5), A.canonical_payload_digest(payload)

    monkeypatch.setattr(A, "_read_bootstrap_journal_at", fail_after_publish)
    with pytest.raises(A.BootstrapRecoveryRequired, match="locked recovery"):
        A._close_bootstrap_owner_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_options_maps=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_writer=writer,
            _dependency_rereader=lambda *_a, **_k: dependency_evidence,
            _closure_rereader=lambda *_a, **_k: initialization_evidence,
        )
    assert len(advances) == 3
    assert holder[0]["state"] == "owner_closed"
    assert set(ops.nodes) == {10}


def _closed_owner_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    dependency_evidence = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }

    def write_owner(
        parent_fd: int,
        rebuilt: A.InitializationClosure,
        _evidence: dict[str, tuple[str, bytes]],
    ) -> str:
        owner = rebuilt.artifact(A.RUN_OWNER_FILENAME)
        ops.nodes[parent_fd].children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
        return owner.digest

    closed = A._close_bootstrap_owner_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_options_maps=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _owner_writer=write_owner,
        _dependency_rereader=lambda *_a, **_k: dependency_evidence,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    ops.close(closed.staging_fd)
    assert set(ops.nodes) == {10}
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    )


def test_resume_bootstrap_owner_closed_reconstructs_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    current_identity = A._private_node_identity(
        ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=current_identity,
        inventory=inventory,
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("owner resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_write_initialization_owner_at",
        lambda *_a, **_k: pytest.fail("owner resume must not write"),
    )
    result = A._resume_bootstrap_owner_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 3
    assert result.journal["state"] == "owner_closed"
    assert result.initialization_evidence == initialization_evidence
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_resume_options_maps_for_owner_rejects_corrupt_owner_without_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    owner = closure.artifact(A.RUN_OWNER_FILENAME)
    staging = ops.nodes[10].children[holder[0]["staging_name"]]  # type: ignore[index]
    staging.children[owner.filename] = _FakeTreeNode(
        owner.filename, kind="file", data=owner.raw
    )
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(
            ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
        ),
        inventory=inventory,
    )
    dependency_evidence = {
        name: (closure.artifact(name).digest, closure.artifact(name).raw)
        for name in A.INITIALIZATION_DEPENDENCY_FILENAMES
    }
    corrupt = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    corrupt[owner.filename] = ("sha256:" + "0" * 64, owner.raw)
    monkeypatch.setattr(
        A,
        "_write_initialization_owner_at",
        lambda *_a, **_k: pytest.fail("corrupt owner residue must not be replaced"),
    )
    with pytest.raises(A.BootstrapStateError, match="evidence drifted"):
        A._resume_bootstrap_options_maps_for_owner_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (
                staged_evidence,
                schema_info,
                universe,
            ),
            _dependency_rereader=lambda *_a, **_k: dependency_evidence,
            _closure_rereader=lambda *_a, **_k: corrupt,
        )
    assert staging.children[owner.filename].data == owner.raw
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_owner_closed_terminal_final_race_is_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(
            ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
        ),
        inventory=inventory,
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    parent = ops.nodes[10]
    parent_fsyncs = 0

    def insert_final(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal parent_fsyncs
        if node is parent:
            parent_fsyncs += 1
            if parent_fsyncs == 2:
                parent.children[holder[0]["final_name"]] = _FakeTreeNode(  # type: ignore[index]
                    "late-final", kind="directory"
                )

    ops.on_fsync = insert_final
    with pytest.raises(A.BootstrapStateError, match="final name"):
        A._resume_bootstrap_owner_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (
                staged_evidence,
                schema_info,
                universe,
            ),
            _closure_rereader=lambda *_a, **_k: initialization_evidence,
        )
    assert set(ops.nodes) == {10}


def _prepared_owner_integration_result(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    closure: A.InitializationClosure,
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
) -> A.PreparedOwnerClosed:
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staging_identity = A._private_node_identity(
        ops.stat(journal["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=staging_identity,
        inventory=inventory,
    )
    staging_fd = ops.open(
        journal["staging_name"], os.O_RDONLY, dir_fd=10  # type: ignore[arg-type]
    )
    return A.PreparedOwnerClosed(
        journal=journal,  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(journal),
        staging_fd=staging_fd,
        staging_identity=staging_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=staged_evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
        initialization_evidence={
            artifact.filename: (artifact.digest, artifact.raw)
            for artifact in closure.artifacts
        },
    )


def _fake_owner_closer(ops: _FakeTreeOps, calls: list[str]):
    def writer(
        parent_fd: int,
        closure: A.InitializationClosure,
        _evidence: dict[str, tuple[str, bytes]],
    ) -> str:
        owner = closure.artifact(A.RUN_OWNER_FILENAME)
        ops.nodes[parent_fd].children[owner.filename] = _FakeTreeNode(
            owner.filename, kind="file", data=owner.raw
        )
        return owner.digest

    def dependency_reread(
        _parent_fd: int,
        closure: A.InitializationClosure,
        prefix: tuple[str, ...],
    ) -> dict[str, tuple[str, bytes]]:
        return {
            name: (closure.artifact(name).digest, closure.artifact(name).raw)
            for name in prefix
        }

    def closure_reread(
        _parent_fd: int,
        closure: A.InitializationClosure,
    ) -> dict[str, tuple[str, bytes]]:
        return {
            artifact.filename: (artifact.digest, artifact.raw)
            for artifact in closure.artifacts
        }

    def close(*args: object, **kwargs: object) -> A.PreparedOwnerClosed:
        calls.append("closer")
        return A._close_bootstrap_owner_locked_at(
            *args,
            **kwargs,
            _owner_writer=writer,
            _dependency_rereader=dependency_reread,
            _closure_rereader=closure_reread,
        )

    return close


def test_integrate_bootstrap_owner_resumes_exact_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []

    def resume(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        calls.append("resume")
        return _prepared_owner_integration_result(
            ops, holder[0], closure, scan_result
        )

    result = A._prepare_or_resume_bootstrap_owner_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _options_integrator=lambda *_a, **_k: pytest.fail("must not integrate options"),
        _owner_stage_resumer=lambda *_a, **_k: pytest.fail("must not resume options"),
        _closer=lambda *_a, **_k: pytest.fail("must not close owner"),
        _resumer=resume,
    )
    assert calls == ["resume"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_routes_options_stage_to_closer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    calls: list[str] = []

    def stage_resume(*_args: object, **_kwargs: object) -> A.PreparedOptionsMapsClosed:
        calls.append("stage-resume")
        return prepared

    result = A._prepare_or_resume_bootstrap_owner_closed_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _options_integrator=lambda *_a, **_k: pytest.fail("must use stage resume"),
        _owner_stage_resumer=stage_resume,
        _closer=_fake_owner_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail("must not resume owner"),
    )
    assert calls == ["stage-resume", "closer"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_routes_missing_through_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    options_journal = holder[0]
    reserved = _universe_integration_reserved(options_journal)
    prepared = _prepared_options_maps_integration_result(
        ops, options_journal, closure, scan_result
    )
    journal_name = A.bootstrap_journal_name("run-final")
    ops.nodes[10].children.pop(journal_name)
    calls: list[str] = []

    def integrate(*_args: object, **_kwargs: object) -> A.PreparedOptionsMapsClosed:
        calls.append("options")
        holder[0] = options_journal
        ops.nodes[10].children[journal_name] = _FakeTreeNode(
            "journal", kind="file"
        )
        return prepared

    result = A._prepare_or_resume_bootstrap_owner_closed_locked_at(
        10,
        journal_name,
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _options_integrator=integrate,
        _owner_stage_resumer=lambda *_a, **_k: pytest.fail("must not stage-resume"),
        _closer=_fake_owner_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail("must not owner-resume"),
    )
    assert calls == ["options", "closer"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_refuses_later_state_before_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        _scan_result,
        _closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    holder[0] = A.bootstrap_journal_payload(
        state="ready_to_promote",
        previous_journal_digest=A.canonical_payload_digest(holder[0]),
        staging_name=holder[0]["staging_name"],  # type: ignore[arg-type]
        final_name=holder[0]["final_name"],  # type: ignore[arg-type]
        semantic_options_digest=holder[0]["semantic_options_digest"],  # type: ignore[arg-type]
        run_controls_digest=holder[0]["run_controls_digest"],  # type: ignore[arg-type]
        smoke_policy_digest=holder[0]["smoke_policy_digest"],  # type: ignore[arg-type]
        hmac_key_id_value=holder[0]["hmac_key_id"],  # type: ignore[arg-type]
        snapshot_metadata=holder[0]["snapshot_metadata"],  # type: ignore[arg-type]
        universe_binding=holder[0]["universe_binding"],  # type: ignore[arg-type]
        completed_artifacts=holder[0]["completed_artifacts"],  # type: ignore[arg-type]
    )
    with pytest.raises(A.BootstrapStateError, match="not resumable"):
        A._prepare_or_resume_bootstrap_owner_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _options_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_stage_resumer=lambda *_a, **_k: pytest.fail("must not stage-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=lambda *_a, **_k: pytest.fail("must not resume"),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_rejects_alternate_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    alternate = dict(holder[0])
    alternate["previous_journal_digest"] = "sha256:" + "f" * 64

    def resume(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        return _prepared_owner_integration_result(
            ops, alternate, closure, scan_result
        )

    with pytest.raises(A.BootstrapStateError, match="authority changed"):
        A._prepare_or_resume_bootstrap_owner_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _options_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_stage_resumer=lambda *_a, **_k: pytest.fail("must not stage-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=resume,
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_normalizes_direct_terminal_seal_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    authoritative = holder[0]
    reserved = _universe_integration_reserved(authoritative)

    def resume(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        return _prepared_owner_integration_result(
            ops, authoritative, closure, scan_result
        )

    def fail_seal(*_args: object, **_kwargs: object) -> A.PrivateTreeSeal:
        raise A.BootstrapRecoveryRequired("synthetic terminal seal recovery")

    monkeypatch.setattr(A, "seal_private_tree_at", fail_seal)
    with pytest.raises(A.BootstrapStateError) as caught:
        A._prepare_or_resume_bootstrap_owner_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _options_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_stage_resumer=lambda *_a, **_k: pytest.fail("must not stage-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=resume,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert holder[0] == authoritative
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_owner_rejects_closer_descriptor_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_options_maps_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_options_maps_integration_result(
        ops, holder[0], closure, scan_result
    )
    real_closer = _fake_owner_closer(ops, [])

    def closer(*args: object, **kwargs: object) -> A.PreparedOwnerClosed:
        result = real_closer(*args, **kwargs)
        substituted_fd = ops.open(
            result.journal["staging_name"], os.O_RDONLY, dir_fd=10
        )
        return replace(result, staging_fd=substituted_fd)

    with pytest.raises(
        A.BootstrapRecoveryRequired, match="integration result is invalid"
    ):
        A._prepare_or_resume_bootstrap_owner_closed_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _options_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_stage_resumer=lambda *_a, **_k: prepared,
            _closer=closer,
            _resumer=lambda *_a, **_k: pytest.fail("must not resume"),
        )
    assert holder[0]["state"] == "owner_closed"
    assert set(ops.nodes) == {10}


def _closed_ready_to_promote_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    ready = A._close_bootstrap_ready_to_promote_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_owner=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    ops.close(ready.staging_fd)
    assert set(ops.nodes) == {10}
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    )


def test_close_bootstrap_ready_to_promote_transfers_exact_closed_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    owner_journal = holder[0]
    prepared = _prepared_owner_integration_result(
        ops, owner_journal, closure, scan_result
    )
    consumed_fd = prepared.staging_fd
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    result = A._close_bootstrap_ready_to_promote_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_owner=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 4
    assert holder[0] == result.journal
    assert result.journal["state"] == "ready_to_promote"
    assert result.journal["previous_journal_digest"] == A.canonical_payload_digest(
        owner_journal
    )
    assert result.journal["completed_artifacts"] == owner_journal[
        "completed_artifacts"
    ]
    assert result.staging_fd == consumed_fd
    assert result.initialization_evidence == initialization_evidence
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_close_bootstrap_ready_to_promote_handoff_drift_is_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    fresh = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    drifted = dict(fresh)
    owner = closure.artifact(A.RUN_OWNER_FILENAME)
    drifted[owner.filename] = ("sha256:" + "0" * 64, owner.raw)
    with pytest.raises(A.BootstrapStateError, match="evidence") as caught:
        A._close_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_owner=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: drifted,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert holder[0]["state"] == "owner_closed"
    assert set(ops.nodes) == {10}


def test_close_bootstrap_ready_to_promote_postpublish_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    rereads = 0

    def fail_second_reread(
        *_args: object, **_kwargs: object
    ) -> dict[str, tuple[str, bytes]]:
        nonlocal rereads
        rereads += 1
        if rereads == 2:
            raise ValueError("synthetic postpublish reread failure")
        return evidence

    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._close_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_owner=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=fail_second_reread,
        )
    assert holder[0]["state"] == "ready_to_promote"
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_ready_to_promote_reconstructs_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(
            ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
        ),
        inventory=inventory,
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("ready resume must not advance"),
    )
    monkeypatch.setattr(
        A,
        "_write_initialization_owner_at",
        lambda *_a, **_k: pytest.fail("ready resume must not write"),
    )
    result = A._resume_bootstrap_ready_to_promote_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 4
    assert result.journal["state"] == "ready_to_promote"
    assert result.initialization_evidence == initialization_evidence
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_ready_to_promote_terminal_final_race_is_ordinary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staged_evidence = replace(
        evidence,
        staging_identity=A._private_node_identity(
            ops.stat(holder[0]["staging_name"], dir_fd=10)  # type: ignore[arg-type]
        ),
        inventory=inventory,
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    parent = ops.nodes[10]
    parent_fsyncs = 0

    def insert_final(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal parent_fsyncs
        if node is parent:
            parent_fsyncs += 1
            if parent_fsyncs == 2:
                parent.children[holder[0]["final_name"]] = _FakeTreeNode(  # type: ignore[index]
                    "late-final", kind="directory"
                )

    ops.on_fsync = insert_final
    with pytest.raises(A.BootstrapStateError, match="final name") as caught:
        A._resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (
                staged_evidence,
                schema_info,
                universe,
            ),
            _closure_rereader=lambda *_a, **_k: initialization_evidence,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert set(ops.nodes) == {10}


def _prepared_ready_integration_result(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    closure: A.InitializationClosure,
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
) -> A.PreparedReadyToPromote:
    evidence, schema_info, universe = scan_result
    inventory = A._closed_staging_inventory_names(
        (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
    )
    staging_identity = A._private_node_identity(
        ops.stat(journal["staging_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    staged_evidence = replace(
        evidence,
        staging_identity=staging_identity,
        inventory=inventory,
    )
    staging_fd = ops.open(
        journal["staging_name"], os.O_RDONLY, dir_fd=10  # type: ignore[arg-type]
    )
    return A.PreparedReadyToPromote(
        journal=journal,  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(journal),
        staging_fd=staging_fd,
        staging_identity=staging_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=staged_evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
        initialization_evidence={
            artifact.filename: (artifact.digest, artifact.raw)
            for artifact in closure.artifacts
        },
    )


def _fake_ready_closer(ops: _FakeTreeOps, calls: list[str]):
    def close(*args: object, **kwargs: object) -> A.PreparedReadyToPromote:
        calls.append("closer")
        prepared = kwargs["prepared_owner"]
        assert type(prepared) is A.PreparedOwnerClosed
        evidence = {
            artifact.filename: (artifact.digest, artifact.raw)
            for artifact in prepared.evidence.initialization.artifacts
        }
        return A._close_bootstrap_ready_to_promote_locked_at(
            *args,
            **kwargs,
            _closure_rereader=lambda *_a, **_k: evidence,
        )

    return close


def test_integrate_bootstrap_ready_resumes_exact_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []

    def resume(*_args: object, **_kwargs: object) -> A.PreparedReadyToPromote:
        calls.append("resume")
        return _prepared_ready_integration_result(
            ops, holder[0], closure, scan_result
        )

    result = A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate owner"),
        _owner_resumer=lambda *_a, **_k: pytest.fail("must not resume owner"),
        _closer=lambda *_a, **_k: pytest.fail("must not close ready"),
        _resumer=resume,
    )
    assert calls == ["resume"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_routes_owner_stage_to_closer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    calls: list[str] = []

    def owner_resume(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        calls.append("owner-resume")
        return prepared

    result = A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate owner"),
        _owner_resumer=owner_resume,
        _closer=_fake_ready_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail("must not resume ready"),
    )
    assert calls == ["owner-resume", "closer"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_routes_missing_through_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    owner_journal = holder[0]
    reserved = _universe_integration_reserved(owner_journal)
    prepared = _prepared_owner_integration_result(
        ops, owner_journal, closure, scan_result
    )
    journal_name = A.bootstrap_journal_name("run-final")
    ops.nodes[10].children.pop(journal_name)
    calls: list[str] = []

    def owner_integrate(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        calls.append("owner-integrate")
        holder[0] = owner_journal
        ops.nodes[10].children[journal_name] = _FakeTreeNode(
            "journal", kind="file"
        )
        return prepared

    result = A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
        10,
        journal_name,
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _owner_integrator=owner_integrate,
        _owner_resumer=lambda *_a, **_k: pytest.fail("must not resume owner"),
        _closer=_fake_ready_closer(ops, calls),
        _resumer=lambda *_a, **_k: pytest.fail("must not resume ready"),
    )
    assert calls == ["owner-integrate", "closer"]
    ops.close(result.staging_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_refuses_promoted_before_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        _scan_result,
        _closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    holder[0] = A.bootstrap_journal_payload(
        state="promoted",
        previous_journal_digest=A.canonical_payload_digest(holder[0]),
        staging_name=holder[0]["staging_name"],  # type: ignore[arg-type]
        final_name=holder[0]["final_name"],  # type: ignore[arg-type]
        semantic_options_digest=holder[0]["semantic_options_digest"],  # type: ignore[arg-type]
        run_controls_digest=holder[0]["run_controls_digest"],  # type: ignore[arg-type]
        smoke_policy_digest=holder[0]["smoke_policy_digest"],  # type: ignore[arg-type]
        hmac_key_id_value=holder[0]["hmac_key_id"],  # type: ignore[arg-type]
        snapshot_metadata=holder[0]["snapshot_metadata"],  # type: ignore[arg-type]
        universe_binding=holder[0]["universe_binding"],  # type: ignore[arg-type]
        completed_artifacts=holder[0]["completed_artifacts"],  # type: ignore[arg-type]
    )
    with pytest.raises(A.BootstrapStateError, match="not resumable"):
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=lambda *_a, **_k: pytest.fail("must not owner-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=lambda *_a, **_k: pytest.fail("must not ready-resume"),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_rejects_alternate_closed_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    alternate = dict(holder[0])
    alternate["previous_journal_digest"] = "sha256:" + "e" * 64

    def resume(*_args: object, **_kwargs: object) -> A.PreparedReadyToPromote:
        return _prepared_ready_integration_result(
            ops, alternate, closure, scan_result
        )

    with pytest.raises(A.BootstrapStateError, match="authority changed"):
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=lambda *_a, **_k: pytest.fail("must not owner-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=resume,
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_rejects_alternate_owner_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    alternate = dict(holder[0])
    alternate["previous_journal_digest"] = "sha256:" + "d" * 64

    def owner_resume(*_args: object, **_kwargs: object) -> A.PreparedOwnerClosed:
        return _prepared_owner_integration_result(
            ops, alternate, closure, scan_result
        )

    with pytest.raises(A.BootstrapStateError, match="authority changed"):
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=owner_resume,
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=lambda *_a, **_k: pytest.fail("must not ready-resume"),
        )
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_normalizes_direct_terminal_seal_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    authoritative = holder[0]
    reserved = _universe_integration_reserved(authoritative)

    def resume(*_args: object, **_kwargs: object) -> A.PreparedReadyToPromote:
        return _prepared_ready_integration_result(
            ops, authoritative, closure, scan_result
        )

    def fail_seal(*_args: object, **_kwargs: object) -> A.PrivateTreeSeal:
        raise A.BootstrapRecoveryRequired("synthetic terminal seal recovery")

    monkeypatch.setattr(A, "seal_private_tree_at", fail_seal)
    with pytest.raises(A.BootstrapStateError) as caught:
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=lambda *_a, **_k: pytest.fail("must not owner-resume"),
            _closer=lambda *_a, **_k: pytest.fail("must not close"),
            _resumer=resume,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert holder[0] == authoritative
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_rejects_closer_descriptor_substitution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    real_closer = _fake_ready_closer(ops, [])

    def closer(*args: object, **kwargs: object) -> A.PreparedReadyToPromote:
        result = real_closer(*args, **kwargs)
        substituted_fd = ops.open(
            result.journal["staging_name"], os.O_RDONLY, dir_fd=10
        )
        return replace(result, staging_fd=substituted_fd)

    with pytest.raises(
        A.BootstrapRecoveryRequired, match="closer result is invalid"
    ):
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=lambda *_a, **_k: prepared,
            _closer=closer,
            _resumer=lambda *_a, **_k: pytest.fail("must not resume ready"),
        )
    assert holder[0]["state"] == "ready_to_promote"
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_ready_invalid_closer_result_closes_consumed_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_owner_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_owner_integration_result(
        ops, holder[0], closure, scan_result
    )
    with pytest.raises(
        A.BootstrapRecoveryRequired, match="closer result is invalid"
    ):
        A._prepare_or_resume_bootstrap_ready_to_promote_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _owner_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _owner_resumer=lambda *_a, **_k: prepared,
            _closer=lambda *_a, **_k: object(),  # type: ignore[arg-type]
            _resumer=lambda *_a, **_k: pytest.fail("must not resume ready"),
        )
    assert holder[0]["state"] == "owner_closed"
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_exclusively_renames_and_transfers_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    ready_journal = holder[0]
    prepared = _prepared_ready_integration_result(
        ops, ready_journal, closure, scan_result
    )
    consumed_fd = prepared.staging_fd
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    result = A._promote_bootstrap_ready_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_ready=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 5
    assert result.journal["state"] == "promoted"
    assert result.journal["previous_journal_digest"] == A.canonical_payload_digest(
        ready_journal
    )
    assert result.journal["completed_artifacts"] == ready_journal[
        "completed_artifacts"
    ]
    assert result.final_fd == consumed_fd
    assert ops.renamed_labels == [
        (ready_journal["staging_name"], ready_journal["final_name"])
    ]
    parent = ops.nodes[10]
    assert ready_journal["staging_name"] not in parent.children
    assert ready_journal["final_name"] in parent.children
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_preexisting_final_refuses_without_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    parent = ops.nodes[10]
    parent.children[holder[0]["final_name"]] = _FakeTreeNode(  # type: ignore[index]
        "foreign-final", kind="directory"
    )
    with pytest.raises(A.BootstrapStateError, match="final name") as caught:
        A._promote_bootstrap_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_ready=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert holder[0]["state"] == "ready_to_promote"
    assert ops.renamed_labels == []
    assert holder[0]["staging_name"] in parent.children
    assert parent.children[holder[0]["final_name"]].label == "foreign-final"  # type: ignore[index]
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_postrename_parent_fsync_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    parent = ops.nodes[10]
    real_rename = ops.rename_exclusive

    def rename_then_fail_fsync(
        source: str, destination: str, *, dir_fd: int
    ) -> None:
        real_rename(source, destination, dir_fd=dir_fd)

        def fail_parent(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
            if node is parent:
                raise OSError("synthetic postrename parent fsync failure")

        ops.on_fsync = fail_parent

    ops.rename_exclusive = rename_then_fail_fsync  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._promote_bootstrap_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_ready=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
        )
    assert holder[0]["state"] == "ready_to_promote"
    assert holder[0]["staging_name"] not in parent.children
    assert holder[0]["final_name"] in parent.children
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_proves_final_binding_before_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    parent = ops.nodes[10]
    real_rename = ops.rename_exclusive
    fsync_count_at_rename: int | None = None

    def rename_then_substitute(
        source: str, destination: str, *, dir_fd: int
    ) -> None:
        nonlocal fsync_count_at_rename
        real_rename(source, destination, dir_fd=dir_fd)
        parent.children[destination] = _FakeTreeNode(
            "substituted-final", kind="directory"
        )
        fsync_count_at_rename = len(ops.fsync_labels)

    ops.rename_exclusive = rename_then_substitute  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._promote_bootstrap_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_ready=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
        )
    assert fsync_count_at_rename is not None
    assert len(ops.fsync_labels) == fsync_count_at_rename
    assert holder[0]["state"] == "ready_to_promote"
    assert holder[0]["staging_name"] not in parent.children
    assert parent.children[holder[0]["final_name"]].label == "substituted-final"  # type: ignore[index]
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_destination_race_never_replaces_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    parent = ops.nodes[10]
    real_rename = ops.rename_exclusive

    def insert_destination_then_rename(
        source: str, destination: str, *, dir_fd: int
    ) -> None:
        parent.children[destination] = _FakeTreeNode(
            "racing-final", kind="directory"
        )
        real_rename(source, destination, dir_fd=dir_fd)

    ops.rename_exclusive = insert_destination_then_rename  # type: ignore[method-assign]
    with pytest.raises(A.BootstrapStateError, match="cannot promote") as caught:
        A._promote_bootstrap_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_ready=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
        )
    assert type(caught.value) is A.BootstrapStateError
    assert holder[0]["state"] == "ready_to_promote"
    assert holder[0]["staging_name"] in parent.children
    assert parent.children[holder[0]["final_name"]].label == "racing-final"  # type: ignore[index]
    assert ops.renamed_labels == []
    assert set(ops.nodes) == {10}


def test_promote_bootstrap_ready_postcas_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    evidence = prepared.initialization_evidence
    rereads = 0

    def fail_second_reread(
        *_args: object, **_kwargs: object
    ) -> dict[str, tuple[str, bytes]]:
        nonlocal rereads
        rereads += 1
        if rereads == 2:
            raise ValueError("synthetic post-CAS evidence failure")
        return evidence

    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._promote_bootstrap_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            prepared_ready=prepared,
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=fail_second_reread,
        )
    parent = ops.nodes[10]
    assert holder[0]["state"] == "promoted"
    assert holder[0]["staging_name"] not in parent.children
    assert holder[0]["final_name"] in parent.children
    assert set(ops.nodes) == {10}


def _renamed_ready_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    environment = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    ops, _staging_path, _semantic, _controls, _scan, _closure, holder, _advances = (
        environment
    )
    ops.rename_exclusive(
        holder[0]["staging_name"],  # type: ignore[arg-type]
        holder[0]["final_name"],  # type: ignore[arg-type]
        dir_fd=10,
    )
    return environment


def _closed_promoted_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    promoted = A._promote_bootstrap_ready_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        prepared_ready=prepared,
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
    )
    ops.close(promoted.final_fd)
    assert set(ops.nodes) == {10}
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    )


def _final_scan_evidence(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
) -> tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse]:
    evidence, schema_info, universe = scan_result
    final_identity = A._private_node_identity(
        ops.stat(journal["final_name"], dir_fd=10)  # type: ignore[arg-type]
    )
    return (
        replace(
            evidence,
            staging_identity=final_identity,
            inventory=A._closed_staging_inventory_names(
                (A.SNAPSHOT_FILENAME, *A.INITIALIZATION_ARTIFACT_FILENAMES)
            ),
        ),
        schema_info,
        universe,
    )


def _verified_renamed_ready_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    _FakeTreeOps,
    Path,
    dict[str, object],
    dict[str, object],
    tuple[A.ClosedSnapshotEvidence, A.AtomicSchemaInfo, A.AtomicCandidateUniverse],
    A.InitializationClosure,
    list[dict[str, object]],
    list[dict[str, object]],
    A.VerifiedBootstrapFinalTree,
]:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _renamed_ready_environment(tmp_path, monkeypatch)
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, holder[0], scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    verified = A._verify_bootstrap_final_tree_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        expected_state="ready_to_promote",
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    return (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
        verified,
    )


def test_verify_bootstrap_renamed_ready_reconstructs_final_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _renamed_ready_environment(tmp_path, monkeypatch)
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, holder[0], scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    verified = A._verify_bootstrap_final_tree_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        expected_state="ready_to_promote",
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 4
    assert verified.journal["state"] == "ready_to_promote"
    assert verified.initialization_evidence == initialization_evidence
    ops.close(verified.final_fd)
    assert set(ops.nodes) == {10}


def test_recover_bootstrap_renamed_ready_publishes_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _renamed_ready_environment(tmp_path, monkeypatch)
    ready_journal = holder[0]
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, ready_journal, scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    verified = A._verify_bootstrap_final_tree_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        expected_state="ready_to_promote",
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    result = A._recover_bootstrap_renamed_ready_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        verified_final=verified,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 5
    assert holder[0] == result.journal
    assert result.journal["state"] == "promoted"
    assert result.journal["previous_journal_digest"] == A.canonical_payload_digest(
        ready_journal
    )
    assert result.final_fd == verified.final_fd
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_resume_bootstrap_promoted_reconstructs_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        advances,
    ) = _closed_promoted_environment(tmp_path, monkeypatch)
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, holder[0], scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    monkeypatch.setattr(
        A,
        "_advance_bootstrap_journal_locked_at",
        lambda *_a, **_k: pytest.fail("promoted resume must not advance"),
    )
    result = A._resume_bootstrap_promoted_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        lock_fd=11,
        lock_name=".synthetic.lock",
        staging_path=staging_path,
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _snapshot_verifier=lambda *_a, **_k: staged_evidence,
        _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
        _closure_rereader=lambda *_a, **_k: initialization_evidence,
    )
    assert len(advances) == 5
    assert result.journal["state"] == "promoted"
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_recover_bootstrap_renamed_ready_rejects_forged_closure_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        _staging_path,
        semantic,
        controls,
        _scan_result,
        closure,
        holder,
        _advances,
        verified,
    ) = _verified_renamed_ready_environment(tmp_path, monkeypatch)
    forged_binding = dict(closure.universe_binding)
    forged_binding["candidate_locator_universe_hash"] = "sha256:" + "0" * 64
    forged_closure = replace(closure, universe_binding=forged_binding)
    forged = replace(
        verified,
        evidence=replace(verified.evidence, initialization=forged_closure),
    )
    with pytest.raises(A.BootstrapRecoveryRequired, match="verification failed"):
        A._recover_bootstrap_renamed_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            verified_final=forged,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=lambda *_a, **_k: verified.initialization_evidence,
        )
    assert holder[0]["state"] == "ready_to_promote"
    assert set(ops.nodes) == {10}


def test_verify_bootstrap_final_rejects_wrong_completed_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _renamed_ready_environment(tmp_path, monkeypatch)
    completed = dict(holder[0]["completed_artifacts"])  # type: ignore[arg-type]
    completed[A.RUN_OWNER_FILENAME] = "sha256:" + "0" * 64
    holder[0] = {**holder[0], "completed_artifacts": completed}
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, holder[0], scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._verify_bootstrap_final_tree_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            expected_state="ready_to_promote",
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
            _closure_rereader=lambda *_a, **_k: initialization_evidence,
        )
    assert set(ops.nodes) == {10}


def test_verify_bootstrap_final_terminal_staging_race_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _renamed_ready_environment(tmp_path, monkeypatch)
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, holder[0], scan_result
    )
    initialization_evidence = {
        artifact.filename: (artifact.digest, artifact.raw)
        for artifact in closure.artifacts
    }
    parent = ops.nodes[10]
    parent_fsyncs = 0

    def reinsert_staging(node: _FakeTreeNode, _ops: _FakeTreeOps) -> None:
        nonlocal parent_fsyncs
        if node is parent:
            parent_fsyncs += 1
            if parent_fsyncs == 2:
                parent.children[holder[0]["staging_name"]] = _FakeTreeNode(  # type: ignore[index]
                    "late-staging", kind="directory"
                )

    ops.on_fsync = reinsert_staging
    with pytest.raises(A.BootstrapRecoveryRequired, match="requires locked recovery"):
        A._verify_bootstrap_final_tree_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            expected_state="ready_to_promote",
            lock_fd=11,
            lock_name=".synthetic.lock",
            staging_path=staging_path,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _snapshot_verifier=lambda *_a, **_k: staged_evidence,
            _scanner=lambda *_a, **_k: (staged_evidence, schema_info, universe),
            _closure_rereader=lambda *_a, **_k: initialization_evidence,
        )
    assert set(ops.nodes) == {10}


def test_recover_bootstrap_renamed_ready_postcas_failure_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        _staging_path,
        semantic,
        controls,
        _scan_result,
        _closure,
        holder,
        _advances,
        verified,
    ) = _verified_renamed_ready_environment(tmp_path, monkeypatch)
    rereads = 0

    def fail_second_reread(
        *_args: object, **_kwargs: object
    ) -> dict[str, tuple[str, bytes]]:
        nonlocal rereads
        rereads += 1
        if rereads == 2:
            raise ValueError("synthetic recovered post-CAS evidence failure")
        return verified.initialization_evidence

    with pytest.raises(A.BootstrapRecoveryRequired, match="requires recovery"):
        A._recover_bootstrap_renamed_ready_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            lock_fd=11,
            lock_name=".synthetic.lock",
            verified_final=verified,
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _closure_rereader=fail_second_reread,
        )
    assert holder[0]["state"] == "promoted"
    assert set(ops.nodes) == {10}


def _prepared_promoted_integration_result(
    ops: _FakeTreeOps,
    journal: dict[str, object],
    closure: A.InitializationClosure,
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
) -> A.PreparedPromoted:
    staged_evidence, schema_info, universe = _final_scan_evidence(
        ops, journal, scan_result
    )
    final_identity = staged_evidence.staging_identity
    final_fd = ops.open(
        journal["final_name"], os.O_RDONLY, dir_fd=10  # type: ignore[arg-type]
    )
    return A.PreparedPromoted(
        journal=journal,  # type: ignore[arg-type]
        journal_digest=A.canonical_payload_digest(journal),
        final_fd=final_fd,
        final_identity=final_identity,
        evidence=A.UniverseClosedEvidence(
            snapshot_evidence=staged_evidence,
            schema_info=schema_info,
            universe=universe,
            initialization=closure,
        ),
        initialization_evidence={
            artifact.filename: (artifact.digest, artifact.raw)
            for artifact in closure.artifacts
        },
    )


def _fake_promotion_promoter(ops: _FakeTreeOps, calls: list[str]):
    def promote(*args: object, **kwargs: object) -> A.PreparedPromoted:
        calls.append("promoter")
        prepared = kwargs["prepared_ready"]
        assert type(prepared) is A.PreparedReadyToPromote
        return A._promote_bootstrap_ready_locked_at(
            *args,
            **kwargs,
            _closure_rereader=lambda *_a, **_k: prepared.initialization_evidence,
        )

    return promote


def _fake_promoted_resumer(
    ops: _FakeTreeOps,
    closure: A.InitializationClosure,
    scan_result: tuple[
        A.ClosedSnapshotEvidence,
        A.AtomicSchemaInfo,
        A.AtomicCandidateUniverse,
    ],
    holder: list[dict[str, object]],
    calls: list[str],
):
    def resume(*_args: object, **_kwargs: object) -> A.PreparedPromoted:
        calls.append("promoted-resume")
        return _prepared_promoted_integration_result(
            ops, holder[0], closure, scan_result
        )

    return resume


def test_integrate_bootstrap_promoted_routes_ready_staging_through_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    prepared = _prepared_ready_integration_result(
        ops, holder[0], closure, scan_result
    )
    calls: list[str] = []

    def ready(*_args: object, **_kwargs: object) -> A.PreparedReadyToPromote:
        calls.append("ready")
        return prepared

    result = A._prepare_or_resume_bootstrap_promoted_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _ready_integrator=ready,
        _promoter=_fake_promotion_promoter(ops, calls),
        _final_verifier=lambda *_a, **_k: pytest.fail("must not final-verify"),
        _renamed_recoverer=lambda *_a, **_k: pytest.fail("must not recover"),
        _promoted_resumer=_fake_promoted_resumer(
            ops, closure, scan_result, holder, calls
        ),
    )
    assert calls == ["ready", "promoter", "promoted-resume"]
    assert result.journal["state"] == "promoted"
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_promoted_routes_ready_final_through_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        _scan_result,
        _closure,
        holder,
        _advances,
        verified,
    ) = _verified_renamed_ready_environment(tmp_path, monkeypatch)
    closure = verified.evidence.initialization
    scan_result = (
        verified.evidence.snapshot_evidence,
        verified.evidence.schema_info,
        verified.evidence.universe,
    )
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []

    def verify(*_args: object, **_kwargs: object) -> A.VerifiedBootstrapFinalTree:
        calls.append("final-verify")
        return verified

    def recover(*args: object, **kwargs: object) -> A.PreparedPromoted:
        calls.append("recover")
        return A._recover_bootstrap_renamed_ready_locked_at(
            *args,
            **kwargs,
            _closure_rereader=lambda *_a, **_k: verified.initialization_evidence,
        )

    result = A._prepare_or_resume_bootstrap_promoted_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _ready_integrator=lambda *_a, **_k: pytest.fail("must not integrate ready"),
        _promoter=lambda *_a, **_k: pytest.fail("must not rename"),
        _final_verifier=verify,
        _renamed_recoverer=recover,
        _promoted_resumer=_fake_promoted_resumer(
            ops, closure, scan_result, holder, calls
        ),
    )
    assert calls == ["final-verify", "recover", "promoted-resume"]
    assert result.journal["state"] == "promoted"
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_promoted_resumes_exact_promoted_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        scan_result,
        closure,
        holder,
        _advances,
    ) = _closed_promoted_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    calls: list[str] = []
    result = A._prepare_or_resume_bootstrap_promoted_locked_at(
        10,
        A.bootstrap_journal_name("run-final"),
        reserved,  # type: ignore[arg-type]
        staging_path,
        tmp_path / "unused-source.db",
        lock_fd=11,
        lock_name=".synthetic.lock",
        key_bytes=KEY,
        semantic_options=semantic,
        run_controls=controls,
        _ops=ops,
        _ready_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
        _promoter=lambda *_a, **_k: pytest.fail("must not promote"),
        _final_verifier=lambda *_a, **_k: pytest.fail("must not verify ready"),
        _renamed_recoverer=lambda *_a, **_k: pytest.fail("must not recover"),
        _promoted_resumer=_fake_promoted_resumer(
            ops, closure, scan_result, holder, calls
        ),
    )
    assert calls == ["promoted-resume"]
    ops.close(result.final_fd)
    assert set(ops.nodes) == {10}


def test_integrate_bootstrap_promoted_refuses_ready_with_both_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (
        ops,
        staging_path,
        semantic,
        controls,
        _scan_result,
        _closure,
        holder,
        _advances,
    ) = _closed_ready_to_promote_environment(tmp_path, monkeypatch)
    reserved = _universe_integration_reserved(holder[0])
    ops.nodes[10].children[holder[0]["final_name"]] = _FakeTreeNode(  # type: ignore[index]
        "foreign-final", kind="directory"
    )
    with pytest.raises(A.BootstrapRecoveryRequired, match="physical state"):
        A._prepare_or_resume_bootstrap_promoted_locked_at(
            10,
            A.bootstrap_journal_name("run-final"),
            reserved,  # type: ignore[arg-type]
            staging_path,
            tmp_path / "unused-source.db",
            lock_fd=11,
            lock_name=".synthetic.lock",
            key_bytes=KEY,
            semantic_options=semantic,
            run_controls=controls,
            _ops=ops,
            _ready_integrator=lambda *_a, **_k: pytest.fail("must not integrate"),
            _promoter=lambda *_a, **_k: pytest.fail("must not promote"),
            _final_verifier=lambda *_a, **_k: pytest.fail("must not verify"),
            _renamed_recoverer=lambda *_a, **_k: pytest.fail("must not recover"),
            _promoted_resumer=lambda *_a, **_k: pytest.fail("must not resume"),
        )
    assert set(ops.nodes) == {10}


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


def test_durable_row_publication_resumes_and_validates(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    planned, result = _synthetic_row_publication_case(run_dir)

    first = A.publish_planned_rows(run_dir, planned, result)
    second = A.publish_planned_rows(run_dir, planned, result)
    summary = A.validate_atomic_run(run_dir)

    assert first == second
    assert summary == {
        "status": "closed",
        "candidate_outgoing_rows": 3,
        "candidate_eligible_rows": 3,
        "retained_rows": 1,
        "held_missing_chat_join_rows": 0,
        "ambiguous_multi_chat_rows": 0,
        "selected_outgoing_rows": 3,
        "selected_eligible_rows": 3,
        "selected_held_missing_chat_join_rows": 0,
        "selected_ambiguous_multi_chat_rows": 0,
        "considered_rows": 1,
        "not_considered_after_bound": 2,
    }
    row_dir = run_dir / "rows" / planned[0].row_stem
    text_path = row_dir / f"{planned[0].row_stem}.txt"
    original = text_path.read_bytes()
    text_path.write_bytes(b"tampered")
    with pytest.raises(A.AtomicAcquisitionError, match="row binding"):
        A.validate_atomic_run(run_dir)
    text_path.write_bytes(original)
    assert A.validate_atomic_run(run_dir)["status"] == "closed"


def _synthetic_row_publication_case(
    run_dir: Path,
    *,
    chatless_message_ids: tuple[int, ...] = (),
    max_retained: int | None = 1,
) -> tuple[tuple[A.PlannedAtomicRow, ...], A.AtomicProcessingResult]:
    run_dir.mkdir()
    snapshot_path = run_dir / A.SNAPSHOT_FILENAME
    conn, schema = _candidate_fixture(snapshot_path)
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


def test_row_publication_preflight_is_constant_per_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "multi-row"
    planned, result = _synthetic_row_publication_case(
        run_dir, max_retained=None
    )
    assert len(planned) == 3
    real_preflight = A._preflight_row_publication
    calls = 0

    def counted(*args: object, **kwargs: object):
        nonlocal calls
        calls += 1
        return real_preflight(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(A, "_preflight_row_publication", counted)
    first = A.publish_planned_rows(run_dir, planned, result)
    assert calls == 1
    second = A.publish_planned_rows(run_dir, planned, result)
    assert calls == 2
    assert first == second
    assert A.validate_atomic_run(run_dir)["retained_rows"] == 3


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
    }
    receipt_raw = (run_dir / "acquisition-receipt.json").read_bytes()
    ledger_raw = (run_dir / "source-ledger.json").read_bytes()
    for private_value in (b"message-guid-b", b"message-guid-c", b"same text", b"third text"):
        assert private_value not in before
        assert private_value not in receipt_raw
        assert private_value not in ledger_raw


@pytest.mark.parametrize("mutation", ["missing", "mutated", "reordered"])
def test_atomic_validator_refuses_chatless_hold_ledger_drift(
    tmp_path: Path, mutation: str,
) -> None:
    run_dir = tmp_path / mutation
    planned, result = _synthetic_row_publication_case(
        run_dir, chatless_message_ids=(2, 3)
    )
    A.publish_planned_rows(run_dir, planned, result)
    assert A.validate_atomic_run(run_dir)["held_missing_chat_join_rows"] == 2
    path = run_dir / A.PRIVATE_SOURCE_HOLD_LEDGER_FILENAME
    if mutation == "missing":
        path.unlink()
    elif mutation == "mutated":
        _rewrite_canonical_json(
            path,
            lambda payload: payload["holds"][0].__setitem__(
                "reason", "forged"
            ),
        )
    else:
        _rewrite_canonical_json(
            path,
            lambda payload: payload.__setitem__(
                "holds", list(reversed(payload["holds"]))
            ),
        )
    with pytest.raises(A.AtomicAcquisitionError):
        A.validate_atomic_run(run_dir)


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


def test_row_resume_repairs_missing_final_checkpoint_only_with_journal(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "checkpoint-repair"
    planned, result = _synthetic_row_publication_case(run_dir)

    with pytest.raises(RuntimeError, match="ledger kill"):
        A.publish_planned_rows(
            run_dir,
            planned,
            result,
            fault=lambda boundary: (
                (_ for _ in ()).throw(RuntimeError("ledger kill"))
                if boundary == "after_ledger"
                else None
            ),
        )
    (run_dir / "checkpoint.json").unlink()
    A.publish_planned_rows(run_dir, planned, result)
    assert A.validate_atomic_run(run_dir)["status"] == "closed"


def test_row_preflight_refuses_unevidenced_next_residue(tmp_path: Path) -> None:
    run_dir = tmp_path / "unevidenced"
    planned, result = _synthetic_row_publication_case(run_dir)
    row = planned[0]
    assert row.row_stem is not None
    residue = run_dir / A.ROWS_DIRNAME / row.row_stem
    residue.mkdir(parents=True)
    for name, raw in A._expected_row_files(row).items():
        (residue / name).write_bytes(raw)

    with pytest.raises(A.AtomicAcquisitionError, match="unevidenced"):
        A.publish_planned_rows(run_dir, planned, result)


def _rewrite_canonical_json(path: Path, mutate) -> None:
    payload = json.loads(path.read_bytes())
    mutate(payload)
    path.write_bytes(A._canonical_json_bytes(payload))


@pytest.mark.parametrize(
    "mutation",
    [
        "snapshot",
        "semantic-option",
        "run-control",
        "contact-map",
        "source-map",
        "owner-ai-boundary",
        "ledger-equation",
        "checkpoint",
        "sidecar",
        "fragment",
        "manifest",
        "receipt-tool",
        "receipt-privacy",
        "unexpected-inventory",
    ],
)
def test_atomic_validator_mutation_matrix_refuses_every_authority_drift(
    tmp_path: Path, mutation: str,
) -> None:
    run_dir = tmp_path / mutation
    planned, result = _synthetic_row_publication_case(run_dir)
    A.publish_planned_rows(run_dir, planned, result)
    assert A.validate_atomic_run(run_dir)["status"] == "closed"
    stem = planned[0].row_stem
    assert stem is not None

    if mutation == "snapshot":
        with (run_dir / A.SNAPSHOT_FILENAME).open("ab") as handle:
            handle.write(b"drift")
    elif mutation == "semantic-option":
        _rewrite_canonical_json(
            run_dir / A.SEMANTIC_OPTIONS_FILENAME,
            lambda payload: payload.__setitem__("author", "Mutated Author"),
        )
    elif mutation == "run-control":
        _rewrite_canonical_json(
            run_dir / A.RUN_CONTROLS_FILENAME,
            lambda payload: payload.__setitem__("max_messages", 11),
        )
    elif mutation == "contact-map":
        _rewrite_canonical_json(
            run_dir / A.PRIVATE_CONTACT_MAP_FILENAME,
            lambda payload: payload["contacts"][0].__setitem__(
                "contact_alias", "contact-999999"
            ),
        )
    elif mutation == "source-map":
        _rewrite_canonical_json(
            run_dir / A.PRIVATE_SOURCE_IDENTITY_MAP_FILENAME,
            lambda payload: payload.__setitem__("selected_outgoing_rows", 0),
        )
    elif mutation == "owner-ai-boundary":
        _rewrite_canonical_json(
            run_dir / A.RUN_OWNER_FILENAME,
            lambda payload: payload.__setitem__("ai_boundary_version", "forged"),
        )
    elif mutation == "ledger-equation":
        _rewrite_canonical_json(
            run_dir / "source-ledger.json",
            lambda payload: payload.__setitem__("considered_rows", 2),
        )
    elif mutation == "checkpoint":
        _rewrite_canonical_json(
            run_dir / "checkpoint.json",
            lambda payload: payload.__setitem__("retained_rows", 0),
        )
    elif mutation == "sidecar":
        _rewrite_canonical_json(
            run_dir / A.ROWS_DIRNAME / stem / f"{stem}.meta.json",
            lambda payload: payload["tool"].__setitem__("version", "forged"),
        )
    elif mutation == "fragment":
        _rewrite_canonical_json(
            run_dir / A.ROWS_DIRNAME / stem / f"{stem}.fragment.json",
            lambda payload: payload["entry"].__setitem__("privacy", "public"),
        )
    elif mutation == "manifest":
        with (run_dir / "draft_manifest.jsonl").open("ab") as handle:
            handle.write(b"\n")
    elif mutation == "receipt-tool":
        _rewrite_canonical_json(
            run_dir / "acquisition-receipt.json",
            lambda payload: payload["tool"].__setitem__("name", "forged"),
        )
    elif mutation == "receipt-privacy":
        _rewrite_canonical_json(
            run_dir / "acquisition-receipt.json",
            lambda payload: payload["privacy"].__setitem__(
                "contains_raw_identity", True
            ),
        )
    else:
        (run_dir / "alien-state.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(A.AtomicAcquisitionError):
        A.validate_atomic_run(run_dir)


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


def _staged_live_row_case(
    tmp_path: Path,
) -> tuple[
    A.LiveDurableRowIo,
    A.PlannedAtomicRow,
    dict[str, bytes],
    Path,
    Path,
    tuple[int, str, int, str, int],
]:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    output_root = private_root / "runs"
    private_root.mkdir(mode=0o700)
    output_root.mkdir(mode=0o700)
    run_dir = output_root / "seal-row"
    planned, _result = _synthetic_row_publication_case(run_dir)
    row = planned[0]
    assert row.row_stem is not None
    os.chmod(run_dir, 0o700)
    for path in run_dir.iterdir():
        os.chmod(path, 0o600)
    rows = run_dir / A.ROWS_DIRNAME
    staging_root = run_dir / A.ROW_STAGING_DIRNAME
    stage = staging_root / row.row_stem
    rows.mkdir(mode=0o700)
    staging_root.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    expected = A._expected_row_files(row)
    for name, raw in expected.items():
        path = stage / name
        path.write_bytes(raw)
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
    row_io = A.LiveDurableRowIo(
        run_dir,
        final_fd=final_fd,
        parent_fd=parent_fd,
        final_name=final_name,
        journal_name=journal_name,
        lock_fd=lock_fd,
        lock_name=lock_name,
    )
    return (
        row_io,
        row,
        expected,
        stage,
        rows / row.row_stem,
        (parent_fd, journal_name, lock_fd, lock_name, final_fd),
    )


def _close_staged_live_row_case(
    row_io: A.LiveDurableRowIo,
    cleanup: tuple[int, str, int, str, int],
) -> None:
    parent_fd, journal_name, lock_fd, lock_name, final_fd = cleanup
    row_io.close()
    os.close(final_fd)
    A._release_bootstrap_lock_at(parent_fd, journal_name, lock_fd, lock_name)
    os.close(lock_fd)
    os.close(parent_fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS durable row I/O")
def test_live_row_seal_refuses_group_readable_child(tmp_path: Path) -> None:
    row_io, row, expected, stage, _destination, cleanup = (
        _staged_live_row_case(tmp_path)
    )
    try:
        target = stage / f"{row.row_stem}.txt"
        os.chmod(target, 0o644)
        with pytest.raises(A.AtomicAcquisitionError):
            row_io.seal_directory(
                f"{A.ROW_STAGING_DIRNAME}/{row.row_stem}", expected
            )
    finally:
        _close_staged_live_row_case(row_io, cleanup)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS durable row I/O")
@pytest.mark.parametrize("mutation", ["mode", "hardlink", "symlink", "inode-swap"])
def test_live_row_commit_refuses_child_substitution_after_seal(
    tmp_path: Path, mutation: str,
) -> None:
    row_io, row, expected, stage, destination, cleanup = (
        _staged_live_row_case(tmp_path)
    )
    source_relative = f"{A.ROW_STAGING_DIRNAME}/{row.row_stem}"
    destination_relative = f"{A.ROWS_DIRNAME}/{row.row_stem}"
    try:
        row_io.seal_directory(source_relative, expected)
        target = stage / f"{row.row_stem}.txt"
        if mutation == "mode":
            os.chmod(target, 0o644)
        elif mutation == "hardlink":
            os.link(target, stage.parent / "held-hardlink")
        elif mutation == "symlink":
            original = stage.parent / "held-original"
            target.rename(original)
            target.symlink_to(original)
        else:
            raw = target.read_bytes()
            replacement = stage.parent / "replacement"
            replacement.write_bytes(raw)
            os.chmod(replacement, 0o600)
            os.replace(replacement, target)
        with pytest.raises(A.AtomicAcquisitionError):
            row_io.commit_directory(
                source_relative,
                destination_relative,
                expected_files=expected,
            )
        assert not destination.exists()
        assert stage.exists()
    finally:
        _close_staged_live_row_case(row_io, cleanup)


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


class _TtyBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_chatless_holds_run_through_live_smoke_mint_and_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_portable_live_smoke_writer(monkeypatch)
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / "chat.db"
    conn, _schema = _candidate_fixture(source)
    conn.execute("DELETE FROM chat_message_join WHERE message_id IN (2, 3)")
    conn.commit()
    conn.close()
    output_root = private_root / "runs"
    output_root.mkdir(mode=0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=source, output_root=output_root, run_id="smoke-chatless",
        persona="joshua", author="Joshua Miller", register="personal",
        since=None, until=None, include_group_chats=False,
        apple_date_unit="nanoseconds", timezone_name="UTC",
        max_messages=10, max_retained=1, allow_empty=False,
    )
    A.run(
        config, key_bytes=KEY, bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    run_dir = output_root / "smoke-chatless"
    summary = A.validate_atomic_run(run_dir)
    assert summary["retained_rows"] == 1
    assert summary["candidate_outgoing_rows"] == 3
    assert summary["candidate_eligible_rows"] == 1
    assert summary["held_missing_chat_join_rows"] == 2
    assert summary["selected_held_missing_chat_join_rows"] == 2
    assert summary["ambiguous_multi_chat_rows"] == 0

    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    monkeypatch.setattr(
        A.sys, "stdin", _TtyBuffer("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n")
    )
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    minted = A.mint_live_smoke_receipt(
        run_dir / "acquisition-receipt.json", approval
    )
    A._consume_live_smoke_receipt(
        approval, minted["smoke_policy_digest"], run_dir=run_dir
    )


@pytest.mark.parametrize(
    "typed",
    [
        " APPROVE IMESSAGE ATOMIC LIVE SMOKE\n",
        "APPROVE IMESSAGE ATOMIC LIVE SMOKE \n",
        "APPROVE IMESSAGE ATOMIC LIVE SMOKE\t\n",
    ],
)
def test_live_smoke_confirmation_rejects_unstripped_whitespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str,
) -> None:
    private_root, _run_dir, run_receipt = _completed_private_smoke_run(tmp_path)
    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    monkeypatch.setattr(A.sys, "stdin", _TtyBuffer(typed))
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    with pytest.raises(A.AtomicAcquisitionError, match="phrase did not match"):
        A.mint_live_smoke_receipt(run_receipt, approval)
    assert not approval.exists()


def test_live_smoke_mint_validates_before_prompt_and_requires_exact_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root, run_dir, run_receipt = _completed_private_smoke_run(tmp_path)
    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    alias = run_dir / "receipt-copy.json"
    alias.write_bytes(run_receipt.read_bytes())
    os.chmod(alias, 0o600)
    monkeypatch.setattr(
        A.sys, "stdin", _TtyBuffer("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n")
    )
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    with pytest.raises(A.AtomicAcquisitionError, match="exact acquisition receipt"):
        A.mint_live_smoke_receipt(alias, approval)
    alias.unlink()

    _rewrite_canonical_json(
        run_receipt,
        lambda payload: payload["privacy"].__setitem__("contains_source_prose", True),
    )
    with pytest.raises(A.AtomicAcquisitionError, match="acquisition receipt drifted"):
        A.mint_live_smoke_receipt(run_receipt, approval)
    assert not approval.exists()


def test_live_smoke_mint_refuses_mutation_during_owner_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_root, run_dir, run_receipt = _completed_private_smoke_run(tmp_path)
    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    ledger = json.loads((run_dir / "source-ledger.json").read_bytes())
    stem = next(row["row_stem"] for row in ledger["rows"] if row["row_stem"])
    text_path = run_dir / A.ROWS_DIRNAME / stem / f"{stem}.txt"

    class MutatingTty(_TtyBuffer):
        def readline(self, *args, **kwargs):
            text_path.write_bytes(b"mutated after initial validation")
            os.chmod(text_path, 0o600)
            return super().readline(*args, **kwargs)

    monkeypatch.setattr(
        A.sys,
        "stdin",
        MutatingTty("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n"),
    )
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    with pytest.raises(A.AtomicAcquisitionError):
        A.mint_live_smoke_receipt(run_receipt, approval)
    assert not approval.exists()


@pytest.mark.parametrize(
    "topology",
    ["inside-run", "other-root", "repository", "ancestor-repository"],
)
def test_live_smoke_mint_refuses_unsafe_destination_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, topology: str,
) -> None:
    private_root, run_dir, run_receipt = _completed_private_smoke_run(tmp_path)
    if topology == "inside-run":
        approval = run_dir / "imessage-atomic-live-smoke-receipt.json"
    elif topology == "other-root":
        other_root = tmp_path / "other" / A.PRIVATE_ROOT_COMPONENT
        other_root.mkdir(parents=True, mode=0o700)
        os.chmod(other_root, 0o700)
        approval = other_root / "imessage-atomic-live-smoke-receipt.json"
    elif topology == "repository":
        repository = private_root / "approval-repository"
        repository.mkdir(mode=0o700)
        os.chmod(repository, 0o700)
        (repository / ".git").mkdir(mode=0o700)
        approval = repository / "imessage-atomic-live-smoke-receipt.json"
    else:
        (tmp_path / ".git").mkdir(mode=0o700)
        approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    monkeypatch.setattr(
        A.sys, "stdin", _TtyBuffer("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n")
    )
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    with pytest.raises(A.AtomicAcquisitionError):
        A.mint_live_smoke_receipt(run_receipt, approval)
    assert not approval.exists()


def test_live_smoke_consumption_reuses_pinned_topology_and_digest_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_portable_live_smoke_writer(monkeypatch)
    private_root, run_dir, run_receipt = _completed_private_smoke_run(tmp_path)
    approval = private_root / "imessage-atomic-live-smoke-receipt.json"
    monkeypatch.setattr(
        A.sys, "stdin", _TtyBuffer("APPROVE IMESSAGE ATOMIC LIVE SMOKE\n")
    )
    monkeypatch.setattr(A.sys, "stdout", _TtyBuffer())
    minted = A.mint_live_smoke_receipt(run_receipt, approval)
    assert stat.S_IMODE(approval.stat().st_mode) == 0o600
    A._consume_live_smoke_receipt(
        approval, minted["smoke_policy_digest"], run_dir=run_dir
    )
    with pytest.raises(A.AtomicAcquisitionError, match="does not authorize"):
        A._consume_live_smoke_receipt(
            approval, "sha256:" + "0" * 64, run_dir=run_dir
        )

    unsafe = run_dir / "imessage-atomic-live-smoke-receipt.json"
    unsafe.write_bytes(approval.read_bytes())
    os.chmod(unsafe, 0o600)
    with pytest.raises(A.AtomicAcquisitionError, match="outside every run"):
        A._consume_live_smoke_receipt(
            unsafe, minted["smoke_policy_digest"], run_dir=run_dir
        )


def test_candidate_receipt_rejects_alien_or_mutated_selected_rows(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "candidate-receipt-drift.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    alien = replace(universe.selected[0], snapshot_rowid=99999)
    with pytest.raises(A.AtomicAcquisitionError, match="selected membership"):
        A.candidate_universe_receipt_payload(
            replace(universe, selected=(alien, *universe.selected[1:])), KEY
        )
    mutated = replace(universe.selected[0], message_guid="mutated-guid")
    with pytest.raises(A.AtomicAcquisitionError, match="selected membership"):
        A.candidate_universe_receipt_payload(
            replace(universe, selected=(mutated, *universe.selected[1:])), KEY
        )
    conn.close()


@pytest.mark.parametrize(
    "mutation",
    [
        {"selected_outgoing_rows": 0},
        {"considered_rows": -1},
        {"not_considered_after_bound": 0},
        {"retained_rows": 7},
        {"excluded_considered_by_final_reason": {}},
    ],
)
def test_processing_receipt_rejects_forged_accounting(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "processing-receipt-drift.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    processing = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=True,
        max_retained=1,
        preprocessor=_identity_preprocessor,
    )
    with pytest.raises(A.AtomicAcquisitionError, match="processing receipt"):
        A.processing_receipt_payload(replace(processing, **mutation), KEY)
    conn.close()


def test_processing_receipt_rejects_row_disposition_or_count_drift(
    tmp_path: Path,
) -> None:
    conn, schema = _candidate_fixture(tmp_path / "processing-row-drift.db")
    universe = A.discover_candidate_universe(
        conn,
        schema,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=3,
    )
    processing = A.process_selected_candidates(
        universe,
        schema,
        include_group_chats=True,
        max_retained=1,
        preprocessor=_identity_preprocessor,
    )
    forged_row = replace(processing.rows[0], disposition="invented")
    with pytest.raises(A.AtomicAcquisitionError, match="disposition"):
        A.processing_receipt_payload(
            replace(processing, rows=(forged_row,)), KEY
        )
    duplicate_rows = (processing.rows[0], processing.rows[0])
    forged_counts = {reason: 0 for reason in A.EXCLUSION_REASONS}
    with pytest.raises(A.AtomicAcquisitionError, match="locator universe"):
        A.processing_receipt_payload(
            replace(
                processing,
                selected_outgoing_rows=2,
                considered_rows=2,
                not_considered_after_bound=0,
                retained_rows=2,
                excluded_considered_by_final_reason=forged_counts,
                rows=duplicate_rows,
            ),
            KEY,
        )
    conn.close()
