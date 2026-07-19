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


def _completed_source_run(tmp_path: Path) -> tuple[Path, Path]:
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
        (2, "message-guid-2", "three four", 1, _apple_ns(day, 10), 0, 0),
        (3, "message-guid-3", "call ended", 1, _apple_ns(day, 20), 0, 0),
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
    A.run(
        config,
        key_bytes=KEY,
        bootstrap=A._synthetic_fixture_bootstrap,
        preprocessor=lambda text: (text, {"rules": []}),
    )
    source_run = input_root / "source-run"
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
