"""Focused regressions for the sealed shingle-index validator.

All rows are generated control data.  The tests exercise the validator against
owned in-memory snapshots so no named SQLite reopen can mask an invalid file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unicodedata

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import shingle_dedup_validate as validator  # noqa: E402


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _logical_seal(connection: sqlite3.Connection, meta: dict[str, str]) -> str:
    digest = hashlib.sha256()
    header = {key: value for key, value in meta.items() if key != "logical_sha256"}
    digest.update(_canonical({
        "domain": "setec-shingle-index-logical-v1",
        "meta": header,
        "record": "header",
    }))
    for row in connection.execute(
        "SELECT doc_id,draft_id,stage,stage_order,content_sha256,token_count,"
        "shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"
    ):
        digest.update(_canonical({
            "content_sha256": bytes(row[4]).hex(),
            "doc_id": row[0],
            "draft_id": row[1],
            "record": "document",
            "shingle_count": row[6],
            "stage": row[2],
            "stage_order": row[3],
            "status": row[7],
            "token_count": row[5],
        }))
    for shingle_sha256, doc_id in connection.execute(
        "SELECT shingle_sha256,doc_id FROM postings "
        "ORDER BY shingle_sha256,doc_id COLLATE BINARY"
    ):
        digest.update(_canonical({
            "doc_id": doc_id,
            "record": "posting",
            "shingle_sha256": bytes(shingle_sha256).hex(),
        }))
    return digest.hexdigest()


def _valid_raw() -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        validator.configure_creation_connection(connection, in_memory=True)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("memory",)
        connection.execute(f"PRAGMA application_id={validator.APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={validator.USER_VERSION}")
        connection.execute(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE documents(doc_id TEXT PRIMARY KEY COLLATE BINARY,"
            "draft_id TEXT NOT NULL COLLATE BINARY,stage TEXT NOT NULL COLLATE BINARY,"
            "stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL,"
            "token_count INTEGER NOT NULL,shingle_count INTEGER NOT NULL,"
            "status TEXT NOT NULL) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE TABLE postings(shingle_sha256 BLOB NOT NULL,"
            "doc_id TEXT NOT NULL COLLATE BINARY REFERENCES documents(doc_id),"
            "PRIMARY KEY(shingle_sha256,doc_id)) WITHOUT ROWID"
        )
        connection.execute(
            "CREATE INDEX documents_shingle_lookup ON postings(doc_id,shingle_sha256)"
        )
        content_sha256 = hashlib.sha256(b"control-document").digest()
        shingle_sha256 = hashlib.sha256(b"control-shingle").digest()
        connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
            ("doc", "draft", "stage", 0, content_sha256, 8, 1, "eligible"),
        )
        connection.execute("INSERT INTO postings VALUES(?,?)", (shingle_sha256, "doc"))
        meta = {
            "schema_version": validator.SCHEMA_VERSION,
            "tool": validator.TOOL,
            "method_version": validator.METHOD_VERSION,
            "tokenizer_id": validator.TOKENIZER_ID,
            "unicode_version": unicodedata.unidata_version,
            "shingle_k": "8",
            "minimum_tokens": "8",
            "low_threshold_numerator": "35",
            "low_threshold_denominator": "100",
            "high_threshold_numerator": "60",
            "high_threshold_denominator": "100",
            "source_manifest_sha256": "a" * 64,
            "canonical_descriptors_sha256": "b" * 64,
            "document_count": "1",
            "eligible_document_count": "1",
            "unassessed_document_count": "0",
            "posting_count": "1",
            "distinct_shingle_count": "1",
            "maximum_posting_fanout": "1",
            "logical_sha256": "0" * 64,
        }
        meta["logical_sha256"] = _logical_seal(connection, meta)
        connection.executemany("INSERT INTO meta VALUES(?,?)", sorted(meta.items()))
        connection.commit()
        raw = connection.serialize()
        assert len(raw) == (
            connection.execute("PRAGMA page_count").fetchone()[0]
            * connection.execute("PRAGMA page_size").fetchone()[0]
        )
        return raw
    finally:
        connection.close()


def _validate(raw: bytes, *, raw_length: int | None = None) -> dict[str, str]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        validator.configure_read_connection(connection)
        return validator.validate_index(
            connection,
            logical_seal=_logical_seal,
            raw_length=len(raw) if raw_length is None else raw_length,
        )
    finally:
        connection.close()


def _mutated_raw(statement: str) -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(_valid_raw())
        connection.execute(statement)
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def test_valid_owned_snapshot_and_explicit_in_memory_creation_mode() -> None:
    raw = _valid_raw()
    meta = _validate(raw)
    assert meta["schema_version"] == validator.SCHEMA_VERSION
    assert meta["document_count"] == "1"


def test_creation_mode_default_remains_delete_for_named_database(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "named.sqlite")
    try:
        validator.configure_creation_connection(connection)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    finally:
        connection.close()


def test_analyze_sqlite_statistics_object_refuses_closed_schema() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(_valid_raw())
        connection.execute("ANALYZE")
        connection.commit()
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='sqlite_stat1'"
        ).fetchone() == (1,)
        raw = connection.serialize()
    finally:
        connection.close()

    with pytest.raises(validator.IndexValidationError):
        _validate(raw)


def test_trailing_bytes_refuse_exact_page_geometry() -> None:
    raw = _valid_raw()
    appended = raw + b"CONTROL_TRAILER"
    with pytest.raises(validator.IndexValidationError):
        _validate(appended)


@pytest.mark.parametrize("statement", [
    "UPDATE documents SET content_sha256=x'00' WHERE doc_id='doc'",
    "UPDATE documents SET content_sha256='text-digest' WHERE doc_id='doc'",
    "UPDATE postings SET shingle_sha256=x'00' WHERE doc_id='doc'",
])
def test_digest_storage_type_and_length_checks_refuse_before_logical_seal(
    statement: str,
) -> None:
    raw = _mutated_raw(statement)
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        validator.configure_read_connection(connection)
        seal_called = False
        def non_inspecting_seal(_connection: sqlite3.Connection, meta: dict[str, str]) -> str:
            nonlocal seal_called
            seal_called = True
            return meta["logical_sha256"]
        with pytest.raises(validator.IndexValidationError):
            validator.validate_index(connection, logical_seal=non_inspecting_seal,
                                     raw_length=len(raw))
        assert not seal_called
    finally:
        connection.close()


def test_forced_quick_check_failure_refuses_even_when_all_rows_are_valid() -> None:
    connection = sqlite3.connect(":memory:")
    connection.deserialize(_valid_raw())
    validator.configure_read_connection(connection)

    class QuickCheckFailure:
        def __init__(self, delegate: sqlite3.Connection) -> None:
            self.delegate = delegate

        def execute(self, sql: str, parameters: object = ()) -> object:
            if sql == "PRAGMA quick_check":
                return iter((("injected corruption",),))
            return self.delegate.execute(sql, parameters)

    try:
        with pytest.raises(validator.IndexValidationError):
            validator.validate_index(
                QuickCheckFailure(connection),  # type: ignore[arg-type]
                logical_seal=_logical_seal,
                raw_length=len(_valid_raw()),
            )
    finally:
        connection.close()


@pytest.mark.parametrize("stage_order", [-(2**63) - 1, 2**63])
def test_document_row_validator_reaches_signed_64_bit_stage_order_bound(stage_order: int) -> None:
    class OneRowConnection:
        def execute(self, sql: str, _parameters: object = ()) -> object:
            if sql == "SELECT COUNT(*) FROM documents":
                return type("Cursor", (), {"fetchone": lambda self: (1,)})()
            if sql.startswith("SELECT doc_id,draft_id,stage,stage_order"):
                return [("doc", "draft", "stage", stage_order, b"x" * 32,
                         8, 1, "eligible")]
            raise AssertionError(f"stage-order rejection did not happen before: {sql}")

    declared = {"document_count": 1, "eligible_document_count": 1,
                "unassessed_document_count": 0, "posting_count": 1,
                "distinct_shingle_count": 1, "maximum_posting_fanout": 1}
    with pytest.raises(validator.IndexValidationError):
        validator._validate_documents_and_postings(  # type: ignore[arg-type]
            OneRowConnection(), declared=declared,
        )


def test_sqlite_limits_and_progress_budget_are_installed_at_frozen_values() -> None:
    progress: list[tuple[object, int]] = []
    limits: list[tuple[int, int]] = []

    class FakeConnection:
        def set_progress_handler(self, callback: object, interval: int) -> None:
            progress.append((callback, interval))

        def setlimit(self, category: int, ceiling: int) -> None:
            limits.append((category, ceiling))

    budget = validator.install_sqlite_limits(FakeConnection())  # type: ignore[arg-type]
    assert progress == [(budget.callback, validator.SQLITE_VM_CALLBACK_INTERVAL)]
    expected = {
        getattr(sqlite3, name): ceiling for name, ceiling in (
            ("SQLITE_LIMIT_LENGTH", 16_777_216), ("SQLITE_LIMIT_SQL_LENGTH", 65_536),
            ("SQLITE_LIMIT_COLUMN", 64), ("SQLITE_LIMIT_EXPR_DEPTH", 32),
            ("SQLITE_LIMIT_COMPOUND_SELECT", 16), ("SQLITE_LIMIT_VARIABLE_NUMBER", 32),
            ("SQLITE_LIMIT_ATTACHED", 0), ("SQLITE_LIMIT_TRIGGER_DEPTH", 0),
        ) if hasattr(sqlite3, name)
    }
    assert dict(limits) == expected
    budget.maximum_callbacks = 1
    assert budget.callback() == 0 and budget.callback() == 1


def test_sqlite_page_ceiling_refuses_before_schema_or_row_work() -> None:
    values = {
        "PRAGMA application_id": validator.APPLICATION_ID,
        "PRAGMA user_version": validator.USER_VERSION,
        "PRAGMA encoding": "UTF-8",
        "PRAGMA page_size": validator.SQLITE_PAGE_SIZE,
        "PRAGMA page_count": validator.MAX_SQLITE_PAGES + 1,
    }

    class PageOverflow:
        def execute(self, sql: str, _parameters: object = ()) -> object:
            if sql not in values:
                raise AssertionError(f"page refusal must precede: {sql}")
            value = values[sql]
            return type("Cursor", (), {"fetchone": lambda self: (value,)})()

    with pytest.raises(validator.IndexValidationError):
        validator.validate_index(  # type: ignore[arg-type]
            PageOverflow(), logical_seal=lambda _connection, _meta: "0" * 64,
            raw_length=validator.SQLITE_PAGE_SIZE,
        )
