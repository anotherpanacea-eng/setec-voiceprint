from __future__ import annotations

import datetime as dt
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
import compose_imessage_review_bursts as B  # noqa: E402


KEY = bytes(range(32))


def _source_row(
    index: int,
    *,
    words: int = 2,
    timestamp_minutes: int | None = None,
    local_date: str = "2020-06-15",
    group_status: str = A.GROUP_STATUS_DIRECT,
    group_locator: str = "hmac-sha256:" + "a" * 64,
) -> B.RetainedSourceRow:
    timestamp = index * 10 if timestamp_minutes is None else timestamp_minutes
    text = " ".join(f"word{index}-{item}" for item in range(words)).encode()
    return B.RetainedSourceRow(
        source_index=index,
        source_ordinal=f"source-{index:06d}",
        entry_locator="hmac-sha256:" + f"{index + 1:064x}",
        text_bytes=text,
        content_sha256=B._sha256_tag(text),
        word_count=words,
        unix_nanoseconds=timestamp * 60 * 1_000_000_000,
        local_date=local_date,
        group_status=group_status,
        group_locator=group_locator,
    )


def _retained(row: B.RetainedSourceRow) -> B.SourceEvent:
    return B.SourceEvent(row, None)


def _excluded(reason: str = "automated_system") -> B.SourceEvent:
    return B.SourceEvent(None, reason)


def _adjudicated(row: B.RetainedSourceRow) -> B.SourceEvent:
    return B.SourceEvent(row, None, adjudicated=True)


def test_build_bursts_keeps_equal_gap_and_target_boundaries() -> None:
    rows = (
        _retained(_source_row(0, words=2, timestamp_minutes=0)),
        _retained(_source_row(1, words=2, timestamp_minutes=30)),
    )
    bursts = B.build_bursts(
        rows,
        B.BurstConfig(gap_minutes=30, target_words=4, min_review_words=5),
    )
    assert len(bursts) == 1
    assert bursts[0].text_bytes == rows[0].retained.text_bytes + b"\n\n" + (
        rows[1].retained.text_bytes
    )
    assert bursts[0].metadata["separator"]["inserted_bytes"] == 2
    assert bursts[0].metadata["too_short_review"] is True


@pytest.mark.parametrize(
    "second,config",
    [
        (_source_row(1, timestamp_minutes=31), B.BurstConfig(gap_minutes=30)),
        (
            _source_row(1, group_locator="hmac-sha256:" + "b" * 64),
            B.BurstConfig(),
        ),
        (_source_row(1, group_status=A.GROUP_STATUS_GROUP), B.BurstConfig()),
        (_source_row(1, local_date="2020-06-16"), B.BurstConfig()),
        (_source_row(1, words=3), B.BurstConfig(target_words=4)),
    ],
)
def test_build_bursts_splits_every_named_boundary(
    second: B.RetainedSourceRow,
    config: B.BurstConfig,
) -> None:
    bursts = B.build_bursts(
        (_retained(_source_row(0, words=2, timestamp_minutes=0)), _retained(second)),
        config,
    )
    assert [len(burst.members) for burst in bursts] == [1, 1]


def test_excluded_row_breaks_consecutiveness() -> None:
    events = (
        _retained(_source_row(0)),
        _excluded(),
        _retained(_source_row(2)),
    )
    bursts = B.build_bursts(events, B.BurstConfig())
    assert [tuple(row.source_index for row in burst.members) for burst in bursts] == [
        (0,),
        (2,),
    ]


def test_adjudicated_event_requires_its_retained_row() -> None:
    with pytest.raises(B.ReviewBurstError, match="adjudicated source event"):
        B.SourceEvent(None, "automated_system", adjudicated=True)
    with pytest.raises(B.ReviewBurstError, match="adjudicated source event"):
        B.SourceEvent(_source_row(0), None, adjudicated=1)  # type: ignore[arg-type]


def test_adjudicated_row_is_rejected_and_breaks_consecutiveness() -> None:
    events = (
        _retained(_source_row(0)),
        _adjudicated(_source_row(1)),
        _retained(_source_row(2)),
    )
    bursts = B.build_bursts(events, B.BurstConfig())
    assert [tuple(row.source_index for row in burst.members) for burst in bursts] == [
        (0,),
        (2,),
    ]
    assert events[1].retained is not None
    assert all(
        events[1].retained.text_bytes not in burst.text_bytes for burst in bursts
    )
    conservation = B._conservation_payload(
        events,
        bursts,
        {"holds": [], "held_missing_chat_join_rows": 0},
    )
    assert conservation["source_retained_rows"] == 3
    assert conservation["burst_member_rows"] == 2
    assert conservation["adjudicated_excluded_rows"] == 1
    assert conservation["adjudicated_excluded_words"] == 2
    assert conservation["source_retained_words"] == 6
    assert conservation["burst_member_words"] == 4


def test_conservation_refuses_adjudicated_row_leaked_into_bursts() -> None:
    row0, row1 = _source_row(0), _source_row(1)
    leaked = B.build_bursts((_retained(row0), _retained(row1)), B.BurstConfig())
    assert len(leaked) == 1 and len(leaked[0].members) == 2
    events = (_retained(row0), _adjudicated(row1))
    with pytest.raises(B.ReviewBurstError, match="conservation failed"):
        B._conservation_payload(
            events,
            leaked,
            {"holds": [], "held_missing_chat_join_rows": 0},
        )


def test_oversized_message_is_a_singleton_and_conserves_words() -> None:
    events = (
        _retained(_source_row(0, words=7)),
        _retained(_source_row(1, words=1)),
    )
    config = B.BurstConfig(target_words=5, min_review_words=2)
    bursts = B.build_bursts(events, config)
    assert bursts[0].metadata["oversized_singleton"] is True
    assert bursts[1].metadata["too_short_review"] is True
    conservation = B._conservation_payload(
        events,
        bursts,
        {"holds": [], "held_missing_chat_join_rows": 0},
    )
    assert conservation["source_retained_rows"] == 2
    assert conservation["source_retained_words"] == 8
    assert conservation["burst_member_words"] == 8


@pytest.mark.parametrize("second_index", [0, 1])
def test_non_increasing_source_index_refuses(second_index: int) -> None:
    events = (
        _retained(_source_row(1, timestamp_minutes=0)),
        _retained(_source_row(second_index, timestamp_minutes=10)),
    )
    with pytest.raises(B.ReviewBurstError, match="strictly increasing"):
        B.build_bursts(events, B.BurstConfig())


@pytest.mark.parametrize(
    "events",
    [
        (
            _retained(_source_row(0, timestamp_minutes=10)),
            _retained(_source_row(1, timestamp_minutes=9)),
        ),
        (
            _retained(_source_row(0, timestamp_minutes=10)),
            _excluded(),
            _retained(_source_row(2, timestamp_minutes=9)),
        ),
        (
            _retained(_source_row(0, timestamp_minutes=10)),
            _adjudicated(_source_row(1, timestamp_minutes=9)),
        ),
        (
            _retained(_source_row(0, timestamp_minutes=0)),
            _adjudicated(_source_row(1, timestamp_minutes=10)),
            _retained(_source_row(2, timestamp_minutes=9)),
        ),
    ],
)
def test_timestamp_regression_refuses_despite_increasing_index(
    events: tuple[B.SourceEvent, ...],
) -> None:
    with pytest.raises(B.ReviewBurstError, match="timestamp precedes"):
        B.build_bursts(events, B.BurstConfig())


@pytest.mark.parametrize(
    "payload,match",
    [
        (float("nan"), "canonical JSON domain"),
        (1.5, "canonical JSON domain"),
        ((1, 2), "canonical JSON domain"),
        ({1: "one"}, "not a string"),
    ],
)
def test_canonical_json_refuses_out_of_domain_payloads(
    payload: object,
    match: str,
) -> None:
    with pytest.raises(B.ReviewBurstError, match=match):
        B._canonical_json(payload)


def test_canonical_json_normalizes_unicode_encode_error() -> None:
    with pytest.raises(B.ReviewBurstError, match="canonically encoded"):
        B._canonical_json({"value": "\ud800"})


def test_canonical_json_normalizes_recursive_encoding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive_encoding(*_args: object, **_kwargs: object) -> str:
        raise RecursionError("nested too deeply")

    monkeypatch.setattr(B.json, "dumps", recursive_encoding)
    with pytest.raises(B.ReviewBurstError, match="canonically encoded"):
        B._canonical_json({"value": "text"})


def test_canonical_object_normalizes_recursive_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recursive_parse(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("nested too deeply")

    monkeypatch.setattr(B.json, "loads", recursive_parse)
    with pytest.raises(B.ReviewBurstError, match="source ledger is unreadable"):
        B._canonical_object(b"{}\n", "source ledger")


@pytest.mark.parametrize(
    "value",
    [None, 7, "package-\ud800", "package-\x00suffix"],
)
def test_safe_name_refuses_typed_and_filesystem_invalid_values(
    value: object,
) -> None:
    with pytest.raises(B.ReviewBurstError, match="package ID is invalid"):
        B._safe_name(value, "package ID")


def _atomic_schema(conn: sqlite3.Connection) -> None:
    definitions = {
        table: list(columns.items())
        for table, columns in A.REQUIRED_SCHEMA_AFFINITIES.items()
    }
    for table in ("chat", "message", "chat_message_join", "message_attachment_join"):
        columns = []
        if table in {"chat", "message"}:
            columns.append("ROWID INTEGER PRIMARY KEY")
        for name, declared_type in definitions[table]:
            columns.append(f"{name} {declared_type}")
        if table == "message":
            columns.append("thread_originator_guid TEXT")
        conn.execute(f"CREATE TABLE {table} ({', '.join(columns)})")


def _apple_ns(day: dt.date, minute: int) -> int:
    instant = dt.datetime(
        day.year,
        day.month,
        day.day,
        12,
        minute,
        tzinfo=dt.timezone.utc,
    )
    return (
        int(instant.timestamp()) * A.NANOSECONDS_PER_SECOND
        - A.APPLE_UNIX_EPOCH_SECONDS * A.NANOSECONDS_PER_SECOND
    )


def _retained_ledger_stems(source_run: Path) -> list[str]:
    ledger = json.loads((source_run / "source-ledger.json").read_bytes())
    return [
        row["row_stem"]
        for row in ledger["rows"]
        if row["disposition"] == "retained"
    ]


def _write_adjudication(
    source_run: Path,
    stems: list[str],
    *,
    owner_decision_date: str = "2026-07-19",
) -> bytes:
    raw = A._canonical_json_bytes({
        "schema": "setec-imessage-atomic-adjudicated-identity-exclusions/1",
        "rows": [
            {
                "row_stem": stem,
                "reason": (
                    "owner rejected identity-bearing row from corpus ingestion"
                ),
                "owner_decision_date": owner_decision_date,
            }
            for stem in sorted(stems)
        ],
    })
    path = source_run / A.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return raw


def _completed_source_run(
    tmp_path: Path,
    *,
    adjudicated_leak: bool = False,
) -> tuple[Path, Path]:
    private_root = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private_root.mkdir(mode=0o700)
    os.chmod(private_root, 0o700)
    source = private_root / "source.db"
    conn = sqlite3.connect(source)
    _atomic_schema(conn)
    conn.execute("INSERT INTO chat VALUES (10, 'chat-guid', '', NULL, 45)")
    day = dt.date(2020, 6, 15)
    messages = (
        (1, "message-guid-1", "one two", 1, _apple_ns(day, 0), 0, 0),
        (
            2,
            "message-guid-2",
            "three chat-guid four" if adjudicated_leak else "three four",
            1,
            _apple_ns(day, 10),
            0,
            0,
        ),
        (
            3,
            "message-guid-3",
            "seven eight" if adjudicated_leak else "call ended",
            1,
            _apple_ns(day, 20),
            0,
            0,
        ),
        (4, "message-guid-4", "five six", 1, _apple_ns(day, 25), 0, 0),
        (5, "message-guid-held", "held words", 1, _apple_ns(day, 30), 0, 0),
    )
    conn.executemany(
        "INSERT INTO message "
        "(ROWID, guid, text, is_from_me, date, associated_message_type, item_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        messages,
    )
    conn.executemany(
        "INSERT INTO chat_message_join VALUES (10, ?)",
        [(1,), (2,), (3,), (4,)],
    )
    conn.commit()
    conn.close()
    os.chmod(source, 0o600)
    input_root = private_root / "atomic-runs"
    output_root = private_root / "review-runs"
    input_root.mkdir(mode=0o700)
    output_root.mkdir(mode=0o700)
    os.chmod(input_root, 0o700)
    os.chmod(output_root, 0o700)
    config = A.AtomicRunConfig(
        source_db=source,
        output_root=input_root,
        run_id="source-run",
        persona="joshua",
        author="Joshua Miller",
        register="personal",
        since=None,
        until=None,
        include_group_chats=False,
        apple_date_unit="nanoseconds",
        timezone_name="UTC",
        max_messages=100,
        max_retained=None,
        allow_empty=False,
    )
    source_run = input_root / "source-run"
    if adjudicated_leak:
        with pytest.raises(
            A.AtomicAcquisitionError, match="row text leaks raw identity"
        ):
            A.run(
                config,
                key_bytes=KEY,
                bootstrap=A._synthetic_fixture_bootstrap,
                preprocessor=lambda text: (text, {"rules": []}),
            )
        leaking = [
            stem
            for stem in _retained_ledger_stems(source_run)
            if b"chat-guid" in (
                source_run / "rows" / stem / f"{stem}.txt"
            ).read_bytes()
        ]
        assert len(leaking) == 1
        _write_adjudication(source_run, leaking)
        summary = A.validate_atomic_run(source_run)
        assert summary["retained_rows"] == 4
        assert summary["identity_scan"]["adjudicated_excluded_txt_rows"] == 1
        return source_run, output_root
    A.run(
        config,
        key_bytes=KEY,
        bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    assert A.validate_atomic_run(source_run)["retained_rows"] == 3
    return source_run, output_root


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_compose_integration_conserves_rows_and_holds_privately(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    progress: list[dict[str, object]] = []
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        "review-package",
        config=B.BurstConfig(target_words=10, min_review_words=5),
        progress=progress.append,
    )
    package = output_root / "review-package"
    assert receipt["counts"]["bursts"] == 2
    assert receipt["counts"]["source_retained_rows"] == 3
    assert receipt["counts"]["burst_member_rows"] == 3
    assert receipt["counts"]["held_rows"] == 1
    assert receipt["counts"]["excluded_selected_eligible_rows"] == 1
    assert [item["closed_bursts"] for item in progress] == [1, 2]
    assert (package / "burst-000001.txt").read_bytes() == b"one two\n\nthree four"
    assert (package / "burst-000002.txt").read_bytes() == b"five six"
    held = json.loads((package / B.HELD_FILENAME).read_bytes())
    assert held["holds"][0]["reason"] == "missing_chat_join"
    outward = B._canonical_json(receipt)
    assert b"entry_locator" not in outward
    assert b"group_locator" not in outward
    assert b"message-guid" not in outward
    assert stat.S_IMODE(package.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in package.iterdir()
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_compose_rejects_adjudicated_row_from_corpus(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path, adjudicated_leak=True)
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        "review-package",
        config=B.BurstConfig(target_words=10, min_review_words=5),
    )
    package = output_root / "review-package"
    assert receipt["counts"]["bursts"] == 2
    assert receipt["counts"]["source_retained_rows"] == 4
    assert receipt["counts"]["burst_member_rows"] == 3
    assert receipt["counts"]["adjudicated_excluded_rows"] == 1
    assert receipt["counts"]["adjudicated_excluded_words"] == 3
    assert receipt["counts"]["excluded_selected_eligible_rows"] == 0
    assert receipt["counts"]["held_rows"] == 1
    assert (package / "burst-000001.txt").read_bytes() == b"one two"
    assert (package / "burst-000002.txt").read_bytes() == (
        b"seven eight\n\nfive six"
    )
    package_names = {path.name for path in package.iterdir()}
    assert package_names == {
        "burst-000001.txt", "burst-000001.meta.json",
        "burst-000002.txt", "burst-000002.meta.json",
        B.MANIFEST_FILENAME, B.HELD_FILENAME,
        B.CHECKPOINT_FILENAME, B.RECEIPT_FILENAME,
    }
    for name in package_names - {B.HELD_FILENAME}:
        raw = (package / name).read_bytes()
        assert b"chat-guid" not in raw
        assert b"three" not in raw
    checkpoint = json.loads((package / B.CHECKPOINT_FILENAME).read_bytes())
    assert checkpoint["conservation"]["adjudicated_excluded_rows"] == 1
    assert checkpoint["conservation"]["adjudicated_excluded_words"] == 3
    outward = B._canonical_json(receipt)
    assert b"row_stem" not in outward
    assert b"entry_locator" not in outward


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_completed_package_binds_adjudication_state_on_resume(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path, adjudicated_leak=True)
    config = B.BurstConfig(target_words=10, min_review_words=5)
    first = B.compose_review_bursts(
        source_run, output_root, "review-package", config=config
    )
    resumed = B.compose_review_bursts(
        source_run, output_root, "review-package", config=config, resume=True
    )
    assert resumed == first
    adjudicated = json.loads(
        (source_run / A.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME).read_bytes()
    )
    _write_adjudication(
        source_run,
        [row["row_stem"] for row in adjudicated["rows"]],
        owner_decision_date="2026-07-20",
    )
    summary = A.validate_atomic_run(source_run)
    assert summary["identity_scan"]["adjudicated_excluded_txt_rows"] == 1
    with pytest.raises(B.ReviewBurstError, match="journal binding drifted"):
        B.compose_review_bursts(
            source_run, output_root, "review-package", config=config, resume=True
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_fully_adjudicated_run_composes_zero_bursts(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    _write_adjudication(source_run, _retained_ledger_stems(source_run))
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        "review-package",
        config=B.BurstConfig(target_words=10, min_review_words=5),
    )
    package = output_root / "review-package"
    assert receipt["counts"]["bursts"] == 0
    assert receipt["counts"]["burst_member_rows"] == 0
    assert receipt["counts"]["burst_member_words"] == 0
    assert receipt["counts"]["source_retained_rows"] == 3
    assert receipt["counts"]["adjudicated_excluded_rows"] == 3
    assert (package / B.MANIFEST_FILENAME).read_bytes() == b""
    assert {path.name for path in package.iterdir()} == {
        B.MANIFEST_FILENAME, B.HELD_FILENAME,
        B.CHECKPOINT_FILENAME, B.RECEIPT_FILENAME,
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_cli_lone_surrogate_package_id_returns_two_on_real_compose_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    result = B.main([
        "--input-run", str(source_run),
        "--output-root", str(output_root),
        "--package-id", "package-\ud800",
    ])
    assert result == 2
    assert "package ID is invalid" in capsys.readouterr().err
    assert list(output_root.iterdir()) == []


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_interrupted_pair_resumes_only_with_explicit_resume(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    interrupted = False

    def fault(boundary: str) -> None:
        nonlocal interrupted
        if boundary == "after_metadata" and not interrupted:
            interrupted = True
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        B.compose_review_bursts(
            source_run,
            output_root,
            "resume-package",
            fault=fault,
        )
    with pytest.raises(B.ReviewBurstError, match="requires --resume"):
        B.compose_review_bursts(source_run, output_root, "resume-package")
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        "resume-package",
        resume=True,
    )
    assert receipt["counts"]["source_retained_rows"] == 3
    assert B.compose_review_bursts(
        source_run,
        output_root,
        "resume-package",
        resume=True,
    ) == receipt


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_journal_only_crash_state_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    original_create = A._create_private_staging_at

    def interrupt_before_staging(*args: object, **kwargs: object) -> object:
        raise RuntimeError("crash after durable journal")

    monkeypatch.setattr(A, "_create_private_staging_at", interrupt_before_staging)
    with pytest.raises(RuntimeError, match="crash after durable journal"):
        B.compose_review_bursts(source_run, output_root, "journal-only-package")

    assert (
        output_root / ".journal-only-package.review-burst-journal.json"
    ).is_file()
    assert not (
        output_root / ".journal-only-package.review-burst-staging"
    ).exists()

    monkeypatch.setattr(A, "_create_private_staging_at", original_create)
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        "journal-only-package",
        resume=True,
    )
    assert receipt["counts"]["source_retained_rows"] == 3


@pytest.mark.parametrize(
    "boundary,expected_journal,expected_copying",
    [
        ("journal_copying", False, True),
        ("after_journal", True, False),
    ],
)
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_create_journal_crash_windows_require_explicit_resume(
    tmp_path: Path,
    boundary: str,
    expected_journal: bool,
    expected_copying: bool,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    package_id = f"crash-{boundary}"
    interrupted = False

    def fault(observed: str) -> None:
        nonlocal interrupted
        if observed == boundary and not interrupted:
            interrupted = True
            raise RuntimeError("journal interruption")

    with pytest.raises(RuntimeError, match="journal interruption"):
        B.compose_review_bursts(
            source_run,
            output_root,
            package_id,
            fault=fault,
        )
    journal = output_root / f".{package_id}.review-burst-journal.json"
    copying = output_root / f".{journal.name}.copying"
    staging = output_root / f".{package_id}.review-burst-staging"
    assert journal.exists() is expected_journal
    assert copying.exists() is expected_copying
    assert not staging.exists()

    with pytest.raises(B.ReviewBurstError, match="requires --resume"):
        B.compose_review_bursts(source_run, output_root, package_id)
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        package_id,
        resume=True,
    )
    assert receipt["counts"]["source_retained_rows"] == 3
    assert (output_root / package_id).is_dir()
    assert not copying.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_zero_byte_journal_copy_refuses_changed_config_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    real_write = B.os.write
    failed = False

    def fail_before_first_byte(descriptor: int, raw: bytes | memoryview) -> int:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("journal write failed before first byte")
        return real_write(descriptor, raw)

    monkeypatch.setattr(B.os, "write", fail_before_first_byte)
    with pytest.raises(B.ReviewBurstError, match="cannot write review-burst journal"):
        B.compose_review_bursts(
            source_run,
            output_root,
            "zero-journal-package",
            config=B.BurstConfig(target_words=300),
        )
    monkeypatch.setattr(B.os, "write", real_write)

    journal = output_root / ".zero-journal-package.review-burst-journal.json"
    copying = output_root / f".{journal.name}.copying"
    assert copying.read_bytes() == b""
    with pytest.raises(B.ReviewBurstError, match="incomplete or binding drifted"):
        B.compose_review_bursts(
            source_run,
            output_root,
            "zero-journal-package",
            config=B.BurstConfig(target_words=301),
            resume=True,
        )
    assert not journal.exists()
    assert not (output_root / "zero-journal-package").exists()
    assert not (
        output_root / ".zero-journal-package.review-burst-staging"
    ).exists()


@pytest.mark.parametrize("boundary", ["journal_copying", "after_journal"])
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_create_journal_recovery_refuses_drifted_residue(
    tmp_path: Path,
    boundary: str,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    package_id = f"drift-{boundary}"

    def fault(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("journal interruption")

    with pytest.raises(RuntimeError, match="journal interruption"):
        B.compose_review_bursts(
            source_run,
            output_root,
            package_id,
            fault=fault,
        )
    journal = output_root / f".{package_id}.review-burst-journal.json"
    if boundary == "journal_copying":
        residue = output_root / f".{journal.name}.copying"
        residue.write_bytes(b"not-an-approved-prefix")
        expected = "incomplete or binding drifted"
    else:
        residue = journal
        payload = json.loads(residue.read_bytes())
        payload["source_config_fingerprint"] = "sha256:" + "0" * 64
        residue.write_bytes(B._canonical_json(payload))
        expected = "binding drifted"
    os.chmod(residue, 0o600)

    with pytest.raises(B.ReviewBurstError, match=expected):
        B.compose_review_bursts(
            source_run,
            output_root,
            package_id,
            resume=True,
        )
    assert not (output_root / package_id).exists()
    assert not (output_root / f".{package_id}.review-burst-staging").exists()


@pytest.mark.parametrize("boundary", ["checkpoint_after_next", "checkpoint_after_swap"])
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_checkpoint_transaction_resumes_at_each_durable_boundary(
    tmp_path: Path,
    boundary: str,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    interrupted = False

    def fault(observed: str) -> None:
        nonlocal interrupted
        if observed == boundary and not interrupted:
            interrupted = True
            raise RuntimeError("checkpoint interruption")

    with pytest.raises(RuntimeError, match="checkpoint interruption"):
        B.compose_review_bursts(
            source_run,
            output_root,
            f"{boundary}-package",
            fault=fault,
        )
    receipt = B.compose_review_bursts(
        source_run,
        output_root,
        f"{boundary}-package",
        resume=True,
    )
    assert receipt["counts"]["source_retained_rows"] == 3
    assert not (
        output_root / f"{boundary}-package" / B.CHECKPOINT_NEXT_FILENAME
    ).exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_resume_refuses_foreign_staging_residue(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)

    def fault(boundary: str) -> None:
        if boundary == "after_checkpoint":
            raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        B.compose_review_bursts(
            source_run,
            output_root,
            "foreign-package",
            fault=fault,
        )
    staging = output_root / ".foreign-package.review-burst-staging"
    foreign = staging / "foreign.bin"
    foreign.write_bytes(b"foreign")
    os.chmod(foreign, 0o600)
    with pytest.raises(B.ReviewBurstError, match="foreign residue"):
        B.compose_review_bursts(
            source_run,
            output_root,
            "foreign-package",
            resume=True,
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_preflight_refuses_bounded_receipt_before_output(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    receipt_path = source_run / "acquisition-receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["full_universe_eligibility_closure"] = False
    receipt_path.write_bytes(B._canonical_json(receipt))
    os.chmod(receipt_path, 0o600)
    with pytest.raises(B.ReviewBurstError, match="full-universe"):
        B.compose_review_bursts(source_run, output_root, "bounded-package")
    assert not (output_root / "bounded-package").exists()
    assert not (output_root / ".bounded-package.review-burst-staging").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_source_hash_drift_refuses_before_output(tmp_path: Path) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    row_text = next((source_run / "rows").glob("*/*.txt"))
    row_text.write_bytes(b"changed source")
    os.chmod(row_text, 0o600)
    with pytest.raises(B.ReviewBurstError, match="source atomic run validation"):
        B.compose_review_bursts(source_run, output_root, "drift-package")
    assert not (output_root / "drift-package").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS private review-burst production")
def test_post_validation_coherent_source_forgery_refuses_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_run, output_root = _completed_source_run(tmp_path)
    validate = B.atomic.validate_atomic_run

    def validate_then_forge(
        run_dir: Path,
        *,
        io: object | None = None,
    ) -> dict[str, object]:
        result = validate(run_dir, io=io)
        ledger_path = source_run / "source-ledger.json"
        ledger = json.loads(ledger_path.read_bytes())
        retained = next(row for row in ledger["rows"] if row["disposition"] == "retained")
        stem = retained["row_stem"]
        text_path = source_run / "rows" / stem / f"{stem}.txt"
        sidecar_path = source_run / "rows" / stem / f"{stem}.meta.json"
        forged_text = b"nine ten"
        forged_digest = B._sha256_tag(forged_text)
        sidecar = json.loads(sidecar_path.read_bytes())
        sidecar["content_hash"] = forged_digest
        sidecar["word_count"] = 2
        retained["content_sha256"] = forged_digest
        retained["word_count"] = 2
        text_path.write_bytes(forged_text)
        sidecar_path.write_bytes(B._canonical_json(sidecar))
        ledger_path.write_bytes(B._canonical_json(ledger))
        for path in (text_path, sidecar_path, ledger_path):
            os.chmod(path, 0o600)
        return result

    monkeypatch.setattr(B.atomic, "validate_atomic_run", validate_then_forge)
    with pytest.raises(B.ReviewBurstError, match="changed after validation"):
        B.compose_review_bursts(source_run, output_root, "forged-package")
    assert not (output_root / "forged-package").exists()
    assert not (output_root / ".forged-package.review-burst-staging").exists()
    assert not (output_root / ".forged-package.review-burst-journal.json").exists()


def test_ledger_retained_row_missing_source_ordinal_refuses(tmp_path: Path) -> None:
    source_run, _output_root = _completed_source_run(tmp_path)
    ledger_path = source_run / "source-ledger.json"
    ledger = json.loads(ledger_path.read_bytes())
    for row in ledger["rows"]:
        row.pop("source_ordinal", None)
    ledger_path.write_bytes(B._canonical_json(ledger))
    os.chmod(ledger_path, 0o600)
    reader = A._PrivateReadOnlyRowIo(source_run)
    try:
        with pytest.raises(B.ReviewBurstError, match="ordinal is invalid"):
            B._load_source_events(reader)
    finally:
        reader.close()


def test_private_production_refuses_non_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(B.sys, "platform", "win32")
    with pytest.raises(B.ReviewBurstError, match="requires macOS"):
        B.compose_review_bursts(tmp_path, tmp_path, "package")


def test_cli_option_domains_refuse_nonpositive_values() -> None:
    for kwargs in (
        {"gap_minutes": 0},
        {"target_words": -1},
        {"min_review_words": True},
    ):
        with pytest.raises(B.ReviewBurstError, match="positive integer"):
            B.BurstConfig(**kwargs)
