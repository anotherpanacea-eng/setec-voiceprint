"""Closed-schema and resource validation for :mod:`shingle_dedup`.

This module is deliberately independent of the CLI and report code.  It accepts
an already-owned SQLite connection, installs the frozen work limits, and
validates the complete ``setec-shingle-index/1`` logical database contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
import unicodedata
from typing import Callable, Mapping


APPLICATION_ID = 0x53484431  # SHD1
USER_VERSION = 1
SCHEMA_VERSION = "setec-shingle-index/1"
TOOL = "shingle_dedup"
METHOD_VERSION = "1"
TOKENIZER_ID = "unicode-w-lower-v1"

MAX_DESCRIPTORS = 5_000
MAX_TOKENS_PER_DOCUMENT = 500_000
MAX_TOTAL_TOKENS = 5_000_000
MAX_SHINGLES_PER_DOCUMENT = 500_000
MAX_POSTINGS = 5_000_000
MAX_DISTINCT_SHINGLES = 5_000_000
MAX_POSTING_FANOUT = 5_000
MAX_SQLITE_PAGES = 131_072

SQLITE_PAGE_SIZE = 4_096
SQLITE_CACHE_KIB = 16_384
SQLITE_VM_CALLBACK_INTERVAL = 1_000
SQLITE_VM_CALLBACK_BUDGET = 500_000

_META_KEYS = frozenset(
    {
        "schema_version",
        "tool",
        "method_version",
        "tokenizer_id",
        "unicode_version",
        "shingle_k",
        "minimum_tokens",
        "low_threshold_numerator",
        "low_threshold_denominator",
        "high_threshold_numerator",
        "high_threshold_denominator",
        "source_manifest_sha256",
        "canonical_descriptors_sha256",
        "document_count",
        "eligible_document_count",
        "unassessed_document_count",
        "posting_count",
        "distinct_shingle_count",
        "maximum_posting_fanout",
        "logical_sha256",
    }
)

_CONSTANT_META = {
    "schema_version": SCHEMA_VERSION,
    "tool": TOOL,
    "method_version": METHOD_VERSION,
    "tokenizer_id": TOKENIZER_ID,
    "shingle_k": "8",
    "minimum_tokens": "8",
    "low_threshold_numerator": "35",
    "low_threshold_denominator": "100",
    "high_threshold_numerator": "60",
    "high_threshold_denominator": "100",
}

_COUNT_META = (
    "document_count",
    "eligible_document_count",
    "unassessed_document_count",
    "posting_count",
    "distinct_shingle_count",
    "maximum_posting_fanout",
)

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UNSIGNED_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\\/]")

_EXPECTED_COLUMNS = {
    "meta": (
        (0, "key", "TEXT", 1, None, 1, 0),
        (1, "value", "TEXT", 1, None, 0, 0),
    ),
    "documents": (
        (0, "doc_id", "TEXT", 1, None, 1, 0),
        (1, "draft_id", "TEXT", 1, None, 0, 0),
        (2, "stage", "TEXT", 1, None, 0, 0),
        (3, "stage_order", "INTEGER", 1, None, 0, 0),
        (4, "content_sha256", "BLOB", 1, None, 0, 0),
        (5, "token_count", "INTEGER", 1, None, 0, 0),
        (6, "shingle_count", "INTEGER", 1, None, 0, 0),
        (7, "status", "TEXT", 1, None, 0, 0),
    ),
    "postings": (
        (0, "shingle_sha256", "BLOB", 1, None, 1, 0),
        (1, "doc_id", "TEXT", 1, None, 2, 0),
    ),
}

_EXPECTED_SQL = {
    ("table", "meta"): (
        "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID"
    ),
    ("table", "documents"): (
        "CREATE TABLE documents(doc_id TEXT PRIMARY KEY COLLATE BINARY,"
        "draft_id TEXT NOT NULL COLLATE BINARY,stage TEXT NOT NULL COLLATE BINARY,"
        "stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL,"
        "token_count INTEGER NOT NULL,shingle_count INTEGER NOT NULL,status TEXT NOT NULL) WITHOUT ROWID"
    ),
    ("table", "postings"): (
        "CREATE TABLE postings(shingle_sha256 BLOB NOT NULL,"
        "doc_id TEXT NOT NULL COLLATE BINARY REFERENCES documents(doc_id),"
        "PRIMARY KEY(shingle_sha256,doc_id)) WITHOUT ROWID"
    ),
    ("index", "documents_shingle_lookup"): (
        "CREATE INDEX documents_shingle_lookup ON postings(doc_id,shingle_sha256)"
    ),
}

# WITHOUT ROWID primary-key autoindexes are intentionally absent from
# sqlite_master on supported SQLite runtimes.  Their exact names, flags,
# columns, and collations remain mandatory through _EXPECTED_INDEX_LISTS and
# index_xinfo below; every object that *is* materialized in sqlite_master must
# match this complete inventory.
_EXPECTED_MASTER = {
    ("table", "meta", "meta"): _EXPECTED_SQL[("table", "meta")],
    ("table", "documents", "documents"): _EXPECTED_SQL[("table", "documents")],
    ("table", "postings", "postings"): _EXPECTED_SQL[("table", "postings")],
    ("index", "documents_shingle_lookup", "postings"): _EXPECTED_SQL[
        ("index", "documents_shingle_lookup")
    ],
}

_EXPECTED_INDEX_LISTS = {
    "meta": ((0, "sqlite_autoindex_meta_1", 1, "pk", 0),),
    "documents": ((0, "sqlite_autoindex_documents_1", 1, "pk", 0),),
    "postings": (
        (0, "documents_shingle_lookup", 0, "c", 0),
        (1, "sqlite_autoindex_postings_1", 1, "pk", 0),
    ),
}


class IndexValidationError(Exception):
    """The SQLite index does not satisfy the frozen closed schema."""


@dataclass
class VmBudget:
    """Mutable state retained by SQLite's progress-handler closure."""

    callbacks: int = 0
    maximum_callbacks: int = SQLITE_VM_CALLBACK_BUDGET

    def callback(self) -> int:
        self.callbacks += 1
        return int(self.callbacks > self.maximum_callbacks)


def _refuse() -> None:
    raise IndexValidationError()


def _pragma_scalar(connection: sqlite3.Connection, statement: str) -> object:
    row = connection.execute(statement).fetchone()
    if row is None or len(row) != 1:
        _refuse()
    return row[0]


def install_sqlite_limits(connection: sqlite3.Connection) -> VmBudget:
    """Install the frozen VM and ``sqlite3_limit`` ceilings.

    Call this immediately after ``sqlite3.connect`` and before the first
    application statement.  The returned object keeps the callback alive and is
    also useful to callers that need to account for work across connections.
    """

    budget = VmBudget()
    connection.set_progress_handler(budget.callback, SQLITE_VM_CALLBACK_INTERVAL)

    setlimit = getattr(connection, "setlimit", None)
    if setlimit is not None:
        limits = (
            ("SQLITE_LIMIT_LENGTH", 16_777_216),
            ("SQLITE_LIMIT_SQL_LENGTH", 65_536),
            ("SQLITE_LIMIT_COLUMN", 64),
            ("SQLITE_LIMIT_EXPR_DEPTH", 32),
            ("SQLITE_LIMIT_COMPOUND_SELECT", 16),
            ("SQLITE_LIMIT_VARIABLE_NUMBER", 32),
            ("SQLITE_LIMIT_ATTACHED", 0),
            ("SQLITE_LIMIT_TRIGGER_DEPTH", 0),
        )
        for constant_name, ceiling in limits:
            constant = getattr(sqlite3, constant_name, None)
            if constant is not None:
                setlimit(constant, ceiling)
    return budget


def configure_creation_connection(
    connection: sqlite3.Connection,
    *,
    in_memory: bool = False,
) -> VmBudget:
    """Harden a newly opened connection before index schema creation.

    Named production artifacts retain the default ``DELETE`` journal contract.
    Callers that explicitly build an owned serialization database in memory may
    opt into SQLite's only valid in-memory journal mode.
    """

    if type(in_memory) is not bool:
        _refuse()

    budget = install_sqlite_limits(connection)
    connection.execute("PRAGMA encoding='UTF-8'")
    connection.execute(f"PRAGMA page_size={SQLITE_PAGE_SIZE}")
    if _pragma_scalar(connection, f"PRAGMA max_page_count={MAX_SQLITE_PAGES}") != MAX_SQLITE_PAGES:
        _refuse()
    connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
    journal_mode = "MEMORY" if in_memory else "DELETE"
    if str(_pragma_scalar(connection, f"PRAGMA journal_mode={journal_mode}")).lower() != journal_mode.lower():
        _refuse()
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    if _pragma_scalar(connection, "PRAGMA foreign_keys") != 1:
        _refuse()
    return budget


def configure_read_connection(connection: sqlite3.Connection) -> VmBudget:
    """Harden an owned immutable snapshot before validation or querying."""

    budget = install_sqlite_limits(connection)
    connection.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    if _pragma_scalar(connection, "PRAGMA trusted_schema") != 0:
        _refuse()
    if _pragma_scalar(connection, "PRAGMA query_only") != 1:
        _refuse()
    if _pragma_scalar(connection, "PRAGMA foreign_keys") != 1:
        _refuse()
    return budget


def _validate_objects(connection: sqlite3.Connection) -> None:
    object_rows = tuple(
        connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        )
    )
    if len(object_rows) != len(_EXPECTED_MASTER):
        _refuse()
    actual_master = {
        (kind, name, table_name): sql
        for kind, name, table_name, sql in object_rows
    }
    if actual_master != _EXPECTED_MASTER:
        _refuse()
    for sql in actual_master.values():
        if type(sql) is not str:
            _refuse()

    for table, expected in _EXPECTED_COLUMNS.items():
        actual = tuple(connection.execute(f"PRAGMA table_xinfo({table})"))
        if actual != expected:
            _refuse()
        if tuple(connection.execute(f"PRAGMA index_list({table})")) != _EXPECTED_INDEX_LISTS[table]:
            _refuse()

    explicit_index = tuple(connection.execute("PRAGMA index_info(documents_shingle_lookup)"))
    if explicit_index != ((0, 1, "doc_id"), (1, 0, "shingle_sha256")):
        _refuse()
    expected_key_indexes = {
        "sqlite_autoindex_meta_1": (("key", "BINARY"),),
        "sqlite_autoindex_documents_1": (("doc_id", "BINARY"),),
        "sqlite_autoindex_postings_1": (("shingle_sha256", "BINARY"), ("doc_id", "BINARY")),
        "documents_shingle_lookup": (("doc_id", "BINARY"), ("shingle_sha256", "BINARY")),
    }
    for index_name, expected in expected_key_indexes.items():
        key_columns = tuple(
            (str(name), str(collation))
            for _sequence, _column_id, name, descending, collation, is_key
            in connection.execute(f"PRAGMA index_xinfo({index_name})")
            if is_key and not descending
        )
        if key_columns != expected:
            _refuse()
    foreign_keys = tuple(connection.execute("PRAGMA foreign_key_list(postings)"))
    if foreign_keys != ((0, 0, "documents", "doc_id", "doc_id", "NO ACTION", "NO ACTION", "NONE"),):
        _refuse()


def _load_and_validate_meta(
    connection: sqlite3.Connection,
    *,
    unicode_version: str,
) -> tuple[dict[str, str], dict[str, int]]:
    if _pragma_scalar(connection, "SELECT COUNT(*) FROM meta") != len(_META_KEYS):
        _refuse()
    rows = tuple(connection.execute("SELECT key,value FROM meta ORDER BY key COLLATE BINARY"))
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in rows):
        _refuse()
    meta = dict(rows)
    if set(meta) != _META_KEYS or any(meta.get(key) != value for key, value in _CONSTANT_META.items()):
        _refuse()
    if meta["unicode_version"] != unicode_version or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", meta["unicode_version"]):
        _refuse()
    for key in ("source_manifest_sha256", "canonical_descriptors_sha256", "logical_sha256"):
        if _HEX64.fullmatch(meta[key]) is None:
            _refuse()

    counts: dict[str, int] = {}
    for key in _COUNT_META:
        value = meta[key]
        if _UNSIGNED_DECIMAL.fullmatch(value) is None:
            _refuse()
        counts[key] = int(value)
    if not (1 <= counts["document_count"] <= MAX_DESCRIPTORS):
        _refuse()
    if not (1 <= counts["eligible_document_count"] <= counts["document_count"]):
        _refuse()
    if counts["eligible_document_count"] + counts["unassessed_document_count"] != counts["document_count"]:
        _refuse()
    if counts["posting_count"] > MAX_POSTINGS:
        _refuse()
    if counts["distinct_shingle_count"] > MAX_DISTINCT_SHINGLES:
        _refuse()
    if counts["maximum_posting_fanout"] > MAX_POSTING_FANOUT:
        _refuse()
    return meta, counts


def _valid_opaque(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        1 <= len(encoded) <= 128
        and value == value.strip()
        and value not in {".", ".."}
        and _CONTROL_OR_SEPARATOR.search(value) is None
    )


def _validate_documents_and_postings(
    connection: sqlite3.Connection,
    *,
    declared: Mapping[str, int],
) -> None:
    actual_document_count = _pragma_scalar(connection, "SELECT COUNT(*) FROM documents")
    if actual_document_count != declared["document_count"]:
        _refuse()

    total_tokens = 0
    eligible = 0
    unassessed = 0
    seen_draft_stage: set[tuple[str, str]] = set()
    seen_draft_order: set[tuple[str, int]] = set()
    for row in connection.execute(
        "SELECT doc_id,draft_id,stage,stage_order,content_sha256,"
        "token_count,shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"
    ):
        doc_id, draft_id, stage, stage_order, content_digest, token_count, shingle_count, status = row
        if not all(_valid_opaque(item) for item in (doc_id, draft_id, stage)):
            _refuse()
        if type(stage_order) is not int or not -(2**63) <= stage_order <= 2**63 - 1:
            _refuse()
        if not isinstance(content_digest, bytes) or len(content_digest) != 32:
            _refuse()
        if type(token_count) is not int or type(shingle_count) is not int:
            _refuse()
        if not 0 <= token_count <= MAX_TOKENS_PER_DOCUMENT:
            _refuse()
        if not 0 <= shingle_count <= MAX_SHINGLES_PER_DOCUMENT:
            _refuse()
        if shingle_count > max(0, token_count - 7):
            _refuse()
        if status == "eligible":
            if token_count < 8 or shingle_count == 0:
                _refuse()
            eligible += 1
        elif status == "too_short_unassessed":
            if token_count >= 8 or shingle_count != 0:
                _refuse()
            unassessed += 1
        else:
            _refuse()
        total_tokens += token_count
        if total_tokens > MAX_TOTAL_TOKENS:
            _refuse()
        draft_stage = (draft_id, stage)
        draft_order = (draft_id, stage_order)
        if draft_stage in seen_draft_stage or draft_order in seen_draft_order:
            _refuse()
        seen_draft_stage.add(draft_stage)
        seen_draft_order.add(draft_order)

    if eligible != declared["eligible_document_count"] or unassessed != declared["unassessed_document_count"]:
        _refuse()

    actual_posting_count = _pragma_scalar(connection, "SELECT COUNT(*) FROM postings")
    if actual_posting_count != declared["posting_count"] or actual_posting_count > MAX_POSTINGS:
        _refuse()
    bad_posting = connection.execute(
        "SELECT 1 FROM postings WHERE typeof(shingle_sha256)!='blob' "
        "OR length(shingle_sha256)!=32 OR typeof(doc_id)!='text' LIMIT 1"
    ).fetchone()
    if bad_posting is not None:
        _refuse()
    if connection.execute(
        "SELECT 1 FROM postings AS p JOIN documents AS d ON d.doc_id=p.doc_id "
        "WHERE d.status!='eligible' LIMIT 1"
    ).fetchone() is not None:
        _refuse()
    if connection.execute(
        "SELECT 1 FROM documents AS d LEFT JOIN "
        "(SELECT doc_id,COUNT(*) AS n FROM postings GROUP BY doc_id) AS p USING(doc_id) "
        "WHERE COALESCE(p.n,0)!=d.shingle_count LIMIT 1"
    ).fetchone() is not None:
        _refuse()

    distinct = _pragma_scalar(connection, "SELECT COUNT(DISTINCT shingle_sha256) FROM postings")
    fanout = _pragma_scalar(
        connection,
        "SELECT COALESCE(MAX(n),0) FROM (SELECT COUNT(*) AS n FROM postings GROUP BY shingle_sha256)",
    )
    if distinct != declared["distinct_shingle_count"] or distinct > MAX_DISTINCT_SHINGLES:
        _refuse()
    if fanout != declared["maximum_posting_fanout"] or fanout > MAX_POSTING_FANOUT:
        _refuse()


def validate_index(
    connection: sqlite3.Connection,
    *,
    logical_seal: Callable[[sqlite3.Connection, dict[str, str]], str],
    raw_length: int,
    unicode_version: str = unicodedata.unidata_version,
) -> dict[str, str]:
    """Validate and return the exact frozen index metadata.

    ``logical_seal`` is supplied by the main module so this helper does not
    duplicate the canonical JSONL encoder.  The callback must return lowercase
    64-hex for the open connection and the validated metadata mapping.
    ``raw_length`` is the exact pinned snapshot length; matching it to SQLite's
    page geometry rejects bytes outside the database image.
    """

    try:
        if _pragma_scalar(connection, "PRAGMA application_id") != APPLICATION_ID:
            _refuse()
        if _pragma_scalar(connection, "PRAGMA user_version") != USER_VERSION:
            _refuse()
        if _pragma_scalar(connection, "PRAGMA encoding") != "UTF-8":
            _refuse()
        if _pragma_scalar(connection, "PRAGMA page_size") != SQLITE_PAGE_SIZE:
            _refuse()
        page_count = _pragma_scalar(connection, "PRAGMA page_count")
        if type(page_count) is not int or not 0 <= page_count <= MAX_SQLITE_PAGES:
            _refuse()
        if type(raw_length) is not int or raw_length <= 0:
            _refuse()
        if raw_length != page_count * SQLITE_PAGE_SIZE:
            _refuse()
        _validate_objects(connection)
        meta, counts = _load_and_validate_meta(connection, unicode_version=unicode_version)
        _validate_documents_and_postings(connection, declared=counts)
        foreign_key_rows = tuple(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_rows:
            _refuse()
        quick_check = tuple(connection.execute("PRAGMA quick_check"))
        if quick_check != (("ok",),):
            _refuse()
        recomputed = logical_seal(connection, meta)
        if not isinstance(recomputed, str) or _HEX64.fullmatch(recomputed) is None:
            _refuse()
        if recomputed != meta["logical_sha256"]:
            _refuse()
        return meta
    except IndexValidationError:
        raise
    except (OverflowError, sqlite3.Error, TypeError, UnicodeError, ValueError):
        raise IndexValidationError() from None
