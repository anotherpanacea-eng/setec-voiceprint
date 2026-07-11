#!/usr/bin/env python3
"""Acquire the user's sent iMessage/SMS prose from macOS ``chat.db``.

This is a local, identity-baseline acquirer.  It reads only
``message.is_from_me = 1`` rows, groups the sender's prose by conversation and
local calendar day, redacts conversation identifiers behind a persisted
``contact_NN`` map, and emits private ``voice_profile`` manifest entries.

v1 deliberately does not structurally parse ``attributedBody`` archives.  It
uses a conservative byte scan for a likely string payload and drops every
attributedBody-only reply row.  A structural typedstream/NSKeyedArchiver
parser, including quote-boundary semantics, belongs to a future increment.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_imessage_sent"
SCRAPER_VERSION = "1.0"

COCOA_EPOCH_OFFSET_SECONDS = 978_307_200
NANOSECONDS_CUTOFF = 10_000_000_000
OBJECT_REPLACEMENT = "\ufffc"
DEFAULT_MIN_WORDS = 150
DEFAULT_MAX_MESSAGES = 200_000
RECEIPT_NAME = ".live_smoke_passed"
KNOWN_GROUP_CHAT_STYLES = frozenset({43})

REQUIRED_MESSAGE_AFFINITIES = {
    "text": "TEXT",
    "attributedBody": "BLOB",
    "is_from_me": "INTEGER",
    "associated_message_type": "INTEGER",
    "item_type": "INTEGER",
    "date": "INTEGER",
}
REPLY_LINK_COLUMN_VARIANTS = ("thread_originator_guid",)

# Best-effort, exact-match exclusions for Apple-authored event text.  The
# authoritative structural exclusions remain associated_message_type/item_type;
# these strings drift across OS releases and are intentionally not substring
# heuristics that could remove the user's own prose.
AUTOMATED_SYSTEM_TEMPLATES = frozenset(
    {
        "missed call",
        "call ended",
        "no answer",
        "facetime call",
        "facetime audio call",
    }
)


class AcquisitionError(RuntimeError):
    """A cleanly reportable acquisition refusal."""


class FullDiskAccessError(AcquisitionError):
    """macOS TCC denied access to Messages data."""


class PossiblyMergedDayError(AcquisitionError):
    """A same-day replacement cannot be proven draft-only."""


# ---------------------------------------------------------------------------
# Dates and attributedBody byte scanning


def _ai_status_from_era(era: str) -> str:
    if era in {"pre_chatgpt", "pre_ai_widespread"}:
        return "pre_ai_human"
    return "unknown"


def apple_date_to_unix_seconds(raw: int | float | None) -> float | None:
    """Normalize one Cocoa seconds/nanoseconds value to a Unix instant."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(value) > NANOSECONDS_CUTOFF:
        value /= 1_000_000_000.0
    return value + COCOA_EPOCH_OFFSET_SECONDS


def apple_date_to_local_date(raw: int | float | None) -> _dt.date | None:
    """Convert per-row Cocoa seconds/nanoseconds to the user's local date."""
    unix_seconds = apple_date_to_unix_seconds(raw)
    if unix_seconds is None:
        return None
    try:
        return _dt.datetime.fromtimestamp(unix_seconds).date()
    except (OverflowError, OSError, ValueError):
        return None


_ARCHIVE_ONLY_STRINGS = {
    "streamtyped",
    "nsstring",
    "nsattributedstring",
    "nsmutableattributedstring",
    "nsdictionary",
    "nsobject",
    "bplist00",
}


def _length_prefixed_nsstring(data: bytes) -> str:
    """Byte-scan the common typedstream NSString payload shape.

    This does not walk the archive graph or infer object relationships; it
    simply recognizes the local marker/length/run shape used by existing
    Messages databases and the checked-in synthetic fixture.
    """
    marker = b"NSString"
    start = 0
    candidates: list[str] = []
    while True:
        idx = data.find(marker, start)
        if idx < 0:
            break
        start = idx + len(marker)
        # A small opaque typedstream header precedes the payload length.  Scan
        # only a bounded window for the '+' marker; this is regex/byte-scan
        # extraction, not a structural archive parser.
        plus = data.find(b"+", start, min(len(data), start + 20))
        if plus < 0 or plus + 1 >= len(data):
            continue
        pos = plus + 1
        length = data[pos]
        pos += 1
        if length == 0x81 and pos + 2 <= len(data):
            length = int.from_bytes(data[pos : pos + 2], "little")
            pos += 2
        elif length == 0x82 and pos + 4 <= len(data):
            length = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
        if length <= 0 or pos + length > len(data):
            continue
        value = data[pos : pos + length].decode("utf-8", "replace").strip()
        if value:
            candidates.append(value)
    return max(candidates, key=lambda s: (len(s.split()), len(s)), default="")


def _printable_byte_candidates(data: bytes) -> list[str]:
    """Return plausible strings without interpreting archive structure."""
    candidates: list[str] = []
    for run in re.findall(rb"[\t\n\r\x20-\x7e]{4,}", data):
        text = re.sub(r"\s+", " ", run.decode("utf-8", "ignore")).strip()
        if text:
            candidates.append(text)
    # Some archived strings are UTF-16.  Matching printable ASCII code units
    # is still a byte scan; it is not a keyed-archive object-graph walk.
    for pattern, encoding in (
        (rb"(?:[\x20-\x7e]\x00){4,}", "utf-16-le"),
        (rb"(?:\x00[\x20-\x7e]){4,}", "utf-16-be"),
    ):
        for run in re.findall(pattern, data):
            text = re.sub(r"\s+", " ", run.decode(encoding, "ignore")).strip()
            if text:
                candidates.append(text)
    return candidates


def decode_attributed_body(blob: bytes | memoryview | None) -> str:
    """Best-effort regex/byte-scan extraction; never a structural parse."""
    if not blob:
        return ""
    data = bytes(blob)
    direct = _length_prefixed_nsstring(data)
    if direct:
        return direct
    plausible = []
    for candidate in _printable_byte_candidates(data):
        normalized = candidate.casefold().strip(" .:_-")
        if normalized in _ARCHIVE_ONLY_STRINGS:
            continue
        if not re.search(r"[A-Za-z]", candidate):
            continue
        plausible.append(candidate)
    return max(plausible, key=lambda s: (len(s.split()), len(s)), default="")


# ---------------------------------------------------------------------------
# SQLite access and schema preflight


def _looks_like_access_denial(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return (
        "authorization denied" in message
        or "unable to open database file" in message
    )


def _full_disk_access_message(db_path: Path, exc: BaseException) -> str:
    return (
        f"Could not read {db_path}: {exc}.\n"
        "macOS Messages data requires Full Disk Access for the invoking "
        f"Python process ({sys.executable}) and its parent app (for example "
        "Terminal, iTerm, or the Claude desktop app). Grant access in System "
        "Settings -> Privacy & Security -> Full Disk Access, or point "
        "--db-path at a copy made in a plain terminal. No AppleScript or "
        "other consent-bypass workaround is attempted."
    )


def open_chat_db(db_path: Path) -> sqlite3.Connection:
    path = db_path.expanduser().resolve()
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        if _looks_like_access_denial(exc):
            raise FullDiskAccessError(_full_disk_access_message(path, exc)) from exc
        raise


def _sqlite_affinity(declared_type: str) -> str:
    declared = (declared_type or "").upper()
    if "INT" in declared:
        return "INTEGER"
    if any(token in declared for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in declared or not declared:
        return "BLOB"
    if any(token in declared for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _table_info(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {str(row[1]): str(row[2] or "") for row in conn.execute(
        f'PRAGMA table_info("{table}")'
    )}


@dataclass(frozen=True)
class SchemaInfo:
    reply_column: str | None
    has_room_name: bool
    has_style: bool


def schema_preflight(conn: sqlite3.Connection) -> SchemaInfo:
    message_columns = _table_info(conn, "message")
    missing = [name for name in REQUIRED_MESSAGE_AFFINITIES if name not in message_columns]
    wrong_types = [
        f"{name}={message_columns[name] or '<undeclared>'} "
        f"(expected {expected} affinity)"
        for name, expected in REQUIRED_MESSAGE_AFFINITIES.items()
        if name in message_columns
        and _sqlite_affinity(message_columns[name]) != expected
    ]
    if missing or wrong_types:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(missing))
        if wrong_types:
            parts.append("retyped " + "; ".join(wrong_types))
        raise AcquisitionError(
            "message schema pre-flight failed (" + "; ".join(parts) + "). "
            "This does not match a supported macOS Messages chat.db; refusing."
        )

    join_columns = _table_info(conn, "chat_message_join")
    chat_columns = _table_info(conn, "chat")
    if not {"chat_id", "message_id"}.issubset(join_columns):
        raise AcquisitionError(
            "schema pre-flight failed: chat_message_join must contain "
            "chat_id and message_id."
        )
    if "chat_identifier" not in chat_columns:
        raise AcquisitionError(
            "schema pre-flight failed: chat.chat_identifier is required."
        )

    # A known reply name with an unexpected declared type is not trusted as a
    # linking signal. Treat it like an absent variant and take the same
    # fail-closed attributedBody path; only the stable fixed-set retypes above
    # hard-fail the whole run.
    reply_column = next(
        (
            name
            for name in REPLY_LINK_COLUMN_VARIANTS
            if name in message_columns
            and _sqlite_affinity(message_columns[name]) == "TEXT"
        ),
        None,
    )
    return SchemaInfo(
        reply_column=reply_column,
        has_room_name="room_name" in chat_columns,
        has_style="style" in chat_columns,
    )


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _select_sql(schema: SchemaInfo) -> str:
    reply = (
        f"m.{_quoted_identifier(schema.reply_column)}"
        if schema.reply_column
        else "NULL"
    )
    room = "c.room_name" if schema.has_room_name else "NULL"
    style = "c.style" if schema.has_style else "NULL"
    return f"""
        SELECT m.ROWID, m.text, m.attributedBody, m.date,
               m.associated_message_type, m.item_type,
               {reply} AS reply_link,
               c.ROWID AS chat_rowid, c.chat_identifier, {room}, {style}
        FROM message AS m
        JOIN chat_message_join AS cmj ON cmj.message_id = m.ROWID
        JOIN chat AS c ON c.ROWID = cmj.chat_id
        WHERE m.is_from_me = 1
        ORDER BY m.date ASC, m.ROWID ASC
    """


@dataclass(frozen=True)
class OutgoingMessage:
    rowid: int
    chat_identifier: str
    date: _dt.date
    unix_seconds: float
    text: str
    is_group: bool


@dataclass
class DiscoveryStats:
    outgoing_rows_in_window: int = 0
    reply_rows_with_link: int = 0


def _log_skip(
    summary: ac.RunSummary, reason: str, rowid: int | str, detail: str = ""
) -> None:
    summary.log_skip(
        reason=reason,
        url="imessage_local",
        detail=f"row={rowid}" + (f"; {detail}" if detail else ""),
    )


def discover_messages(
    conn: sqlite3.Connection,
    schema: SchemaInfo,
    *,
    since: _dt.date | None,
    until: _dt.date | None,
    max_messages: int,
    summary: ac.RunSummary,
    stats: DiscoveryStats,
) -> list[OutgoingMessage]:
    """Read only outgoing rows and apply all per-message exclusions."""
    seen_rowids: set[int] = set()
    messages: list[OutgoingMessage] = []
    reply_detection_available = schema.reply_column is not None

    for row in conn.execute(_select_sql(schema)):
        (
            rowid,
            text,
            attributed_body,
            raw_date,
            associated_type,
            item_type,
            reply_link,
            chat_rowid,
            chat_identifier,
            room_name,
            style,
        ) = row
        if rowid in seen_rowids:
            continue
        seen_rowids.add(rowid)

        unix_seconds = apple_date_to_unix_seconds(raw_date)
        if unix_seconds is None:
            _log_skip(summary, "invalid_date", rowid)
            continue
        try:
            local_date = _dt.datetime.fromtimestamp(unix_seconds).date()
        except (OverflowError, OSError, ValueError):
            _log_skip(summary, "invalid_date", rowid)
            continue
        if since and local_date < since:
            continue
        if until and local_date > until:
            continue

        stats.outgoing_rows_in_window += 1
        if stats.outgoing_rows_in_window > max_messages:
            raise AcquisitionError(
                f"exceeded --max-messages ({max_messages}) inside the selected "
                "date window; narrow it with --since/--until."
            )
        if stats.outgoing_rows_in_window % 10_000 == 0:
            sys.stderr.write(
                f"  scanned {stats.outgoing_rows_in_window:,} outgoing rows "
                "inside the selected window...\n"
            )

        is_reply = reply_link not in (None, "")
        if is_reply:
            stats.reply_rows_with_link += 1

        if associated_type not in (0, None):
            _log_skip(summary, "tapback_reaction", rowid)
            continue
        if item_type not in (0, None):
            _log_skip(summary, "group_action_rename", rowid)
            continue

        text_value = (text or "").strip()
        plain_text_available = bool(text_value) and set(text_value) != {OBJECT_REPLACEMENT}
        if plain_text_available:
            # Final v1 contract: a text-column reply row contains the sender's
            # own text; its quoted parent is a separate row and is never read.
            body = text_value
        else:
            decoded = decode_attributed_body(attributed_body).strip()
            if not decoded or set(decoded) == {OBJECT_REPLACEMENT}:
                _log_skip(summary, "attachment_only", rowid)
                continue
            if is_reply or not reply_detection_available:
                _log_skip(summary, "quoted_reply_unresolved", rowid)
                continue
            body = decoded

        if re.sub(r"\s+", " ", body).strip().casefold() in AUTOMATED_SYSTEM_TEMPLATES:
            _log_skip(summary, "automated_system_message", rowid)
            continue

        identifier = str(chat_identifier or f"chat_row_{chat_rowid}")
        # Modern Messages schemas use style 43 for group conversations and 45
        # for direct chats; room_name independently identifies named groups.
        # Keep the known style set explicit so future values are not guessed.
        is_group = bool(room_name) or style in KNOWN_GROUP_CHAT_STYLES
        messages.append(
            OutgoingMessage(
                rowid=int(rowid),
                chat_identifier=identifier,
                date=local_date,
                unix_seconds=unix_seconds,
                text=body,
                is_group=is_group,
            )
        )
    # Ordering the raw mixed-unit column is not chronological: seconds values
    # sort before nanoseconds values regardless of their actual instants.
    messages.sort(key=lambda message: (message.unix_seconds, message.rowid))
    return messages


# ---------------------------------------------------------------------------
# Stable contact redaction, item discovery, and preprocessing


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _load_name_map(path: Path | None, known_handles: set[str]) -> dict[str, str]:
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"could not read --name-map {path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AcquisitionError("--name-map must contain a JSON object.")
    result: dict[str, str] = {}
    folded_handles = {handle.casefold() for handle in known_handles if handle}
    for raw_handle, raw_label in loaded.items():
        if not isinstance(raw_handle, str) or not isinstance(raw_label, str):
            raise AcquisitionError("--name-map keys and values must be strings.")
        label = raw_label.strip()
        folded_label = label.casefold()
        contains_known_handle = any(
            handle in folded_label for handle in folded_handles
        )
        contains_email = bool(
            re.search(
                r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
                label,
                flags=re.IGNORECASE,
            )
        )
        contains_phone = len(re.sub(r"\D", "", label)) >= 7
        if not label or contains_known_handle or contains_email or contains_phone:
            raise AcquisitionError(
                "--name-map display labels must be non-empty aliases without "
                "raw handles, phone-number-like runs, or email addresses."
            )
        # A conservative slug keeps a user-curated label from accidentally
        # becoming a path or control sequence, and explicitly consumes the
        # shared slug convention required of acquisition scripts.
        safe = ac.slugify(label, max_length=60).replace("-", "_")
        safe_handles = {
            ac.slugify(handle, max_length=60).replace("-", "_")
            for handle in known_handles
            if handle
        }
        if safe == "untitled" or any(
            handle != "untitled" and handle in safe for handle in safe_handles
        ):
            raise AcquisitionError(f"--name-map label {label!r} is not usable.")
        result[raw_handle] = safe
    return result


@dataclass
class ItemMeta:
    locator: str
    title: str
    date: _dt.date
    stable_contact_id: str
    notes: str
    lines: list[str] = field(default_factory=list)


def discover_items(
    messages: Iterable[OutgoingMessage],
    contacts: ac.StableRedactionMap,
    name_map: dict[str, str],
) -> list[ItemMeta]:
    """Return one synthetic item per stable-contact/local-date group."""
    message_list = list(messages)
    contacts.ensure_all(message.chat_identifier for message in message_list)
    groups: dict[tuple[str, _dt.date], ItemMeta] = {}
    for message in message_list:
        stable_id = contacts.stable_id(message.chat_identifier)
        display_label = name_map.get(message.chat_identifier, stable_id)
        key = (stable_id, message.date)
        item = groups.get(key)
        if item is None:
            item = ItemMeta(
                locator=f"{stable_id}|{message.date.isoformat()}",
                title=f"{display_label} — {message.date.isoformat()}",
                date=message.date,
                stable_contact_id=stable_id,
                notes="group_chat" if message.is_group else "direct",
            )
            groups[key] = item
        item.lines.append(message.text)
        if message.is_group:
            item.notes = "group_chat"
    return [groups[key] for key in sorted(groups, key=lambda k: (k[1], k[0]))]


def extract_one(item: ItemMeta) -> tuple[str, str, _dt.date]:
    return "\n".join(item.lines), item.title, item.date


def conversation_day_key(stable_contact_id: str, date: _dt.date) -> str:
    return f"{stable_contact_id}|{date.isoformat()}"


@dataclass(frozen=True)
class PreparedPiece:
    item: ItemMeta
    piece: ac.AcquiredPiece
    day_key: str


def process_item(
    item: ItemMeta,
    *,
    options: "Options",
    summary: ac.RunSummary,
) -> PreparedPiece | None:
    raw_text, title, date = extract_one(item)
    cleaned, preprocessing_meta = ac.preprocess_text(raw_text)
    cleaned = cleaned.strip()
    if not cleaned:
        summary.log_skip(
            reason="empty_after_preprocess", url=item.locator, detail=""
        )
        return None
    word_count = len(re.findall(r"\S+", cleaned))
    if word_count < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="below_min_words",
            url=item.locator,
            detail=f"{word_count} < {options.min_words}",
        )
        return None
    era = ac.era_from_date(date)
    piece = ac.AcquiredPiece(
        title=title,
        author=options.author,
        persona=options.persona,
        register="personal",
        date_written=date,
        source_url="imessage_local",
        cleaned_text=cleaned,
        raw_byte_length=len(raw_text.encode("utf-8")),
        preprocessing_meta=preprocessing_meta,
        acquired_via=f"{TOOL_NAME}_{_dt.date.today().isoformat()}",
        consent_status="author_consent",
        era=era,
        notes=item.notes,
    )
    # Use the shared function explicitly as well as AcquiredPiece.content_hash;
    # the equality is a guard against any future dataclass contract drift.
    if ac.compute_content_hash(cleaned) != piece.content_hash:
        raise AssertionError("content hash contract diverged")
    return PreparedPiece(
        item=item,
        piece=piece,
        day_key=conversation_day_key(item.stable_contact_id, date),
    )


# ---------------------------------------------------------------------------
# Draft-only grown-day replacement and manifest emission


@dataclass(frozen=True)
class ExistingDay:
    meta_path: Path
    text_path: Path
    metadata: dict[str, Any]


def _existing_day(output_dir: Path, day_key: str) -> ExistingDay | None:
    matches: list[ExistingDay] = []
    if not output_dir.exists():
        return None
    for meta_path in output_dir.glob("*.meta.json"):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("conversation_day_key") != day_key:
            continue
        stem = meta_path.name[: -len(".meta.json")]
        matches.append(
            ExistingDay(
                meta_path=meta_path,
                text_path=meta_path.parent / f"{stem}.txt",
                metadata=metadata,
            )
        )
    if len(matches) > 1:
        raise PossiblyMergedDayError(
            "Refused (possibly-merged day, manual cleanup required): "
            f"multiple existing pieces claim {day_key}; delete the stale "
            "conversation-day files manually before re-running."
        )
    return matches[0] if matches else None


def _manifest_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        manifest_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PossiblyMergedDayError(
            f"cannot inspect manifest {path} while proving draft-only state: {exc}."
        ) from exc
    for lineno, raw in enumerate(manifest_text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PossiblyMergedDayError(
                f"cannot safely rewrite draft manifest {path}: malformed JSON "
                f"on line {lineno}: {exc.msg}."
            ) from exc
        if not isinstance(entry, dict):
            raise PossiblyMergedDayError(
                f"cannot safely rewrite draft manifest {path}: line {lineno} "
                "is not an object."
            )
        entries.append(entry)
    return entries


def _private_tree_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate.name == ac.PRIVATE_DIR_NAME:
            return candidate
    raise PossiblyMergedDayError(
        f"cannot locate the {ac.PRIVATE_DIR_NAME} root for {path}."
    )


def _refuse_if_canonical_copy_exists(
    existing: ExistingDay, draft_manifest: Path, old_id: str
) -> None:
    """Refuse replacement if any other private-tree JSONL names the piece.

    The acquirer cannot mutate an already-merged canonical line.  Scanning the
    entire mechanically private tree, rather than only the default canonical
    filename, catches both ``corpus_manifest.jsonl`` and user-renamed merge
    destinations.  An unreadable/malformed candidate also fails closed.
    """
    private_root = _private_tree_root(existing.meta_path)
    try:
        manifests = list(private_root.rglob("*.jsonl"))
    except OSError as exc:
        raise PossiblyMergedDayError(
            f"cannot scan private manifests for a merged copy: {exc}."
        ) from exc
    draft_resolved = draft_manifest.resolve()
    old_text_resolved = existing.text_path.resolve()
    for candidate in manifests:
        if candidate.resolve() == draft_resolved:
            continue
        for entry in _manifest_entries(candidate):
            raw_path = entry.get("path")
            referenced_path: Path | None = None
            if isinstance(raw_path, str) and raw_path.strip():
                path_value = Path(raw_path).expanduser()
                referenced_path = (
                    path_value.resolve()
                    if path_value.is_absolute()
                    else (candidate.parent / path_value).resolve()
                )
            if entry.get("id") == old_id or referenced_path == old_text_resolved:
                raise PossiblyMergedDayError(
                    "Refused (possibly-merged day, manual cleanup required): "
                    f"{candidate} already references {old_id}. Reconcile the "
                    "canonical manifest and stale day files manually before "
                    "re-running."
                )


def _confirm_draft_only(existing: ExistingDay, manifest_path: Path) -> str:
    old_id = existing.meta_path.name[: -len(".meta.json")]
    recorded_manifest = existing.metadata.get("draft_manifest_path")
    expected_owned_draft = existing.meta_path.parent / "draft_manifest.jsonl"
    if (
        not existing.text_path.is_file()
        or existing.metadata.get("scraper", "").split("_")[0:3]
        != ["acquire", "imessage", "sent"]
        or recorded_manifest != str(manifest_path.expanduser().resolve())
        or manifest_path.expanduser().resolve()
        != expected_owned_draft.expanduser().resolve()
    ):
        raise PossiblyMergedDayError(
            "Refused (possibly-merged day, manual cleanup required): "
            f"found existing {existing.meta_path.name} but cannot confirm it "
            "belongs to this acquirer's default, current unmerged draft. "
            "Automatic replacement is intentionally disabled for custom "
            "--emit-manifest paths. Delete that "
            "day's .txt/.meta.json files manually (and reconcile any canonical "
            "manifest entry) before re-running."
        )
    _confirm_single_draft_entry(existing, manifest_path)
    _refuse_if_canonical_copy_exists(existing, manifest_path, old_id)
    return old_id


def _confirm_single_draft_entry(
    existing: ExistingDay, manifest_path: Path
) -> str:
    """Require exactly one draft line for an existing emitted artifact."""
    old_id = existing.meta_path.name[: -len(".meta.json")]
    matching = [
        entry
        for entry in _manifest_entries(manifest_path)
        if entry.get("id") == old_id
    ]
    if len(matching) != 1:
        raise PossiblyMergedDayError(
            "Refused (possibly-merged day, manual cleanup required): "
            f"found existing {existing.meta_path.name} without exactly one "
            f"matching line in {manifest_path}. Pre-merge state is not "
            "confirmable; clean up the stale day manually before re-running."
        )
    return old_id


def _rewrite_manifest_without_id(path: Path, old_id: str) -> None:
    kept = [entry for entry in _manifest_entries(path) if entry.get("id") != old_id]
    text = "".join(
        json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n"
        for entry in kept
    )
    _atomic_write_text(path, text)


def _compose_and_write(
    prepared: PreparedPiece,
    options: "Options",
) -> tuple[Path, Path]:
    manifest_existed = options.manifest_path.exists()
    old_manifest = (
        options.manifest_path.read_bytes() if manifest_existed else b""
    )
    text_path: Path | None = None
    meta_path: Path | None = None
    try:
        text_path, meta_path = ac.write_piece(
            prepared.piece,
            output_dir=options.output_dir,
            scraper_version=SCRAPER_VERSION,
        )
        # The shared composer has no sidecar API. Pass the discrete key through
        # its requested `extra` surface, then move it into this acquirer's
        # sidecar before manifest emission so the validator sees no unknown
        # top-level key.
        entry = ac.compose_manifest_entry(
            prepared.piece,
            text_path=text_path,
            manifest_relative_to=options.manifest_path.parent,
            corpus_role="identity_baseline",
            use=["voice_profile"],
            ai_status=_ai_status_from_era(prepared.piece.era),
            extra={"conversation_day_key": prepared.day_key},
        )
        sidecar_key = entry.pop("conversation_day_key")
        entry.setdefault("era", prepared.piece.era)
        entry.setdefault("consent_status", prepared.piece.consent_status)
        entry.setdefault("acquired_via", prepared.piece.acquired_via)

        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata["conversation_day_key"] = sidecar_key
        metadata["draft_manifest_path"] = str(options.manifest_path.resolve())
        _atomic_write_text(
            meta_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )
        ac.append_manifest_entry(options.manifest_path, entry)
        return text_path, meta_path
    except Exception:
        if text_path is not None:
            text_path.unlink(missing_ok=True)
        if meta_path is not None:
            meta_path.unlink(missing_ok=True)
        if manifest_existed:
            options.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            options.manifest_path.write_bytes(old_manifest)
        else:
            options.manifest_path.unlink(missing_ok=True)
        raise


def _replacement_needed(existing: ExistingDay, prepared: PreparedPiece) -> bool:
    return not (
        existing.metadata.get("content_hash") == prepared.piece.content_hash
        and existing.metadata.get("title") == prepared.piece.title
    )


def _preflight_replacements(
    prepared_pieces: Iterable[PreparedPiece], options: "Options"
) -> None:
    for prepared in prepared_pieces:
        existing = _existing_day(options.output_dir, prepared.day_key)
        if existing is not None and _replacement_needed(existing, prepared):
            _confirm_draft_only(existing, options.manifest_path)


def _emit_prepared(
    prepared: PreparedPiece,
    *,
    options: "Options",
    summary: ac.RunSummary,
) -> bool:
    existing = _existing_day(options.output_dir, prepared.day_key)
    if existing is not None and not _replacement_needed(existing, prepared):
        # A no-op rerun must not bless corruption produced by an older buggy
        # head. Exactly one draft line is part of the dedupe-only invariant.
        _confirm_single_draft_entry(existing, options.manifest_path)
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate_hash", url=prepared.item.locator,
            detail=str(existing.meta_path),
        )
        return False

    if existing is None:
        duplicate = ac.content_hash_already_present(
            prepared.piece.content_hash, options.output_dir
        )
        if duplicate is not None:
            summary.skipped_duplicate += 1
            summary.log_skip(
                reason="duplicate_hash", url=prepared.item.locator,
                detail=str(duplicate),
            )
            return False
        _compose_and_write(prepared, options)
    else:
        old_id = _confirm_draft_only(existing, options.manifest_path)
        old_text = existing.text_path.read_bytes()
        old_meta = existing.meta_path.read_bytes()
        old_manifest = options.manifest_path.read_bytes()
        try:
            existing.text_path.unlink()
            existing.meta_path.unlink()
            _rewrite_manifest_without_id(options.manifest_path, old_id)
            _compose_and_write(prepared, options)
        except Exception as exc:
            # Restore all three draft artifacts if replacement emission fails.
            existing.text_path.write_bytes(old_text)
            existing.meta_path.write_bytes(old_meta)
            options.manifest_path.write_bytes(old_manifest)
            raise AcquisitionError(
                f"failed to replace grown conversation-day {prepared.day_key}: {exc}"
            ) from exc
        summary.log_skip(
            reason="superseded_by_grown_day",
            url=prepared.item.locator,
            detail=old_id,
        )

    summary.acquired += 1
    summary.total_cleaned_words += prepared.piece.word_count
    summary.record_strip_meta(prepared.piece.preprocessing_meta)
    return True


# ---------------------------------------------------------------------------
# Live-smoke gate, README, summary, CLI, and driver


def _db_fingerprint(db_path: Path) -> str:
    digest = hashlib.sha256()
    with db_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_live_smoke_receipt(output_dir: Path, db_path: Path) -> bool:
    receipt = output_dir / RECEIPT_NAME
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        data.get("confirmed") is True
        and data.get("db_sha256") == _db_fingerprint(db_path)
    )


def enforce_live_smoke_gate(
    *, output_dir: Path, db_path: Path, windowed: bool
) -> None:
    if windowed:
        return
    if not _valid_live_smoke_receipt(output_dir, db_path):
        raise AcquisitionError(
            "refusing an unwindowed/full-history write without a valid "
            "live-smoke receipt bound to this database. First run a windowed "
            "real write, review its .txt files in a plain interactive terminal, "
            "then repeat that windowed command with --live-smoke-confirmed."
        )


def write_live_smoke_receipt(
    output_dir: Path,
    db_path: Path,
    *,
    since: _dt.date | None,
    until: _dt.date | None,
) -> None:
    payload = {
        "confirmed": True,
        "confirmed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "db_sha256": _db_fingerprint(db_path),
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    _atomic_write_text(
        output_dir / RECEIPT_NAME,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


README_PREAMBLE = """\
# Sent-iMessage identity-baseline corpus (PRIVATE)

This corpus contains only the user's own outgoing (`is_from_me=1`) message
rows, bundled by redacted conversation and local calendar day.

- **In-body PII is deliberately accepted.** The user's own prose can contain
  names, phone numbers, addresses, or unstructured pasted quotations. Content-
  level PII redaction was not attempted because it would distort stylometric
  signal. The corpus must remain under the private baseline tree. Raw addressed
  conversation identifiers appear only in `contact_map.json`; titles,
  sidecars, filenames, and manifest fields use redacted labels.
- **Structured reply limitation.** v1 uses regex/byte-scan extraction for
  `attributedBody` and drops attributedBody-only reply rows fail-closed. It does
  not claim to detect unstructured copy-pasted third-party text.
- **Composition provenance.** This register is predominantly mobile-composed
  and is subject to iOS/macOS QuickType autocorrect, autocomplete, and
  predictive-text shaping. Consumers should not treat it as production-
  equivalent to keyboard-drafted manuscripts or blog posts.
"""


def _reply_rate_text(summary: ac.RunSummary, stats: DiscoveryStats) -> str:
    unresolved = sum(
        1 for skip in summary.skip_log
        if skip.get("reason") == "quoted_reply_unresolved"
    )
    if stats.reply_rows_with_link:
        rate = 100.0 * unresolved / stats.reply_rows_with_link
        return (
            f"{unresolved}/{stats.reply_rows_with_link} "
            f"({rate:.1f}% quoted-reply-unresolved rate)"
        )
    if unresolved:
        return (
            f"{unresolved} unresolved drops; denominator unavailable because "
            "the reply-linking column was absent"
        )
    return "0/0 (no linked reply rows in this run)"


def write_corpus_readme(
    output_dir: Path, summary: ac.RunSummary, stats: DiscoveryStats
) -> None:
    text = (
        README_PREAMBLE
        + "\nLast acquisition run's structured-reply disclosure: **"
        + _reply_rate_text(summary, stats)
        + "**.\n"
    )
    _atomic_write_text(output_dir / "README.md", text)


_SUMMARY_REASONS = (
    ("duplicate_hash", "duplicate hash"),
    ("superseded_by_grown_day", "superseded by grown day"),
    ("below_min_words", "below min-words"),
    ("tapback_reaction", "tapback/reaction"),
    ("attachment_only", "attachment-only"),
    ("group_action_rename", "group action/rename"),
    ("automated_system_message", "automated system message"),
    ("quoted_reply_unresolved", "quoted-reply unresolved — dropped for safety"),
    ("group_chat_excluded", "group chat excluded"),
    ("invalid_date", "invalid date"),
    ("empty_after_preprocess", "empty after preprocessing"),
)


def render_summary(
    summary: ac.RunSummary,
    stats: DiscoveryStats,
    *,
    options: "Options",
) -> str:
    counts = Counter(skip.get("reason", "") for skip in summary.skip_log)
    lines = [f"Acquired: {summary.acquired} files (conversation-days)"]
    for reason, label in _SUMMARY_REASONS:
        lines.append(f"Skipped ({label}): {counts[reason]}")
    lines.extend(
        [
            "Reply rows with thread_originator_guid set: "
            f"{stats.reply_rows_with_link}  ({_reply_rate_text(summary, stats)})",
            f"Total cleaned words: {summary.total_cleaned_words:,}",
        ]
    )
    if not options.dry_run:
        lines.append(f"Draft manifest written to: {options.manifest_path}")
        lines.append(
            f"Contact map written to: {options.contact_map_path} "
            "(KEEP PRIVATE, do not commit)"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Options:
    db_path: Path
    persona: str
    author: str
    since: _dt.date | None
    until: _dt.date | None
    min_words: int
    max_messages: int
    output_dir: Path
    manifest_path: Path
    contact_map_path: Path
    name_map_path: Path | None
    include_group_chats: bool
    max_items: int
    dry_run: bool
    live_smoke_confirmed: bool


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire the user's own sent iMessage/SMS prose from macOS "
            "Messages chat.db as a private identity baseline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Requires Full Disk Access for the invoking process. A live WAL "
            "database can be locked, so prefer: cp ~/Library/Messages/chat.db "
            "/tmp/chat_copy.db, then --db-path /tmp/chat_copy.db.\n\n"
            "The 150-word default is intentionally lower than the essayistic "
            "500-word floor: texting is short-form, and the corpus-level word "
            "count carries the signal. Lower values retain more, noisier "
            "conversation-days. Apple-authored system-template removal is "
            "best-effort because templates drift across OS releases.\n\n"
            "ai_status is pre_ai_human before 2024-07-01 and unknown on/after "
            "that date because chat.db cannot reveal Apple Intelligence use."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=str(Path.home() / "Library" / "Messages" / "chat.db"),
        help="Messages SQLite database (default: ~/Library/Messages/chat.db).",
    )
    parser.add_argument("--persona", default="joshua", help="Persona slug (default: joshua).")
    parser.add_argument("--author", help="Display author (default: persona).")
    parser.add_argument("--register", choices=["personal"], default="personal")
    parser.add_argument("--since", help="Inclusive local date, YYYY-MM-DD.")
    parser.add_argument("--until", help="Inclusive local date, YYYY-MM-DD.")
    parser.add_argument(
        "--min-words-per-piece",
        type=int,
        default=DEFAULT_MIN_WORDS,
        help=(
            "Drop conversation-days below this cleaned word count (default: "
            "150; lower values retain more, noisier short pieces)."
        ),
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help=(
            "Maximum outgoing rows loaded inside the selected date window "
            "(default: 200000); narrow large runs with --since/--until."
        ),
    )
    parser.add_argument(
        "--contact-map-path",
        help="Persisted raw-handle map (default: <output-dir>/contact_map.json).",
    )
    parser.add_argument("--name-map", help="Optional raw-handle to safe display-label JSON.")
    parser.add_argument(
        "--include-group-chats",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include group-chat turns (default: include; tagged group_chat).",
    )
    parser.add_argument(
        "--consent-status", choices=["author_consent"], default="author_consent"
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=100_000,
        help="Maximum emitted conversation-day pieces (default: 100000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write nothing; report counters and up to five redacted titles.",
    )
    parser.add_argument(
        "--live-smoke-confirmed",
        action="store_true",
        help=(
            "TTY-only attestation after manual review of a windowed real write; "
            "writes a database-bound receipt after success."
        ),
    )
    parser.add_argument(
        "--emit-manifest",
        help="Draft JSONL path (default: <output-dir>/draft_manifest.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Private output tree (default: <baselines>/identity/personal/"
            "<persona>)."
        ),
    )
    return parser


def _parse_date(value: str | None, flag: str) -> _dt.date | None:
    if value is None:
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise AcquisitionError(f"{flag} must be YYYY-MM-DD, got {value!r}.") from exc


def parse_options(args: argparse.Namespace) -> Options:
    since = _parse_date(args.since, "--since")
    until = _parse_date(args.until, "--until")
    if since and until and since > until:
        raise AcquisitionError("--since must be on or before --until.")
    if args.min_words_per_piece < 0:
        raise AcquisitionError("--min-words-per-piece must be non-negative.")
    if args.max_messages <= 0:
        raise AcquisitionError("--max-messages must be positive.")
    if args.max_items <= 0:
        raise AcquisitionError("--max-items must be positive.")

    persona = str(args.persona)
    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else ac.resolve_baselines_dir() / "identity" / "personal" / persona
    )
    manifest_path = (
        Path(args.emit_manifest).expanduser()
        if args.emit_manifest
        else output_dir / "draft_manifest.jsonl"
    )
    contact_map_path = (
        Path(args.contact_map_path).expanduser()
        if args.contact_map_path
        else output_dir / "contact_map.json"
    )
    return Options(
        db_path=Path(args.db_path).expanduser(),
        persona=persona,
        author=args.author or persona,
        since=since,
        until=until,
        min_words=args.min_words_per_piece,
        max_messages=args.max_messages,
        output_dir=output_dir,
        manifest_path=manifest_path,
        contact_map_path=contact_map_path,
        name_map_path=Path(args.name_map).expanduser() if args.name_map else None,
        include_group_chats=bool(args.include_group_chats),
        max_items=args.max_items,
        dry_run=bool(args.dry_run),
        live_smoke_confirmed=bool(args.live_smoke_confirmed),
    )


def _is_dedupe_only(summary: ac.RunSummary) -> bool:
    # A normal rerun still encounters permanent row-level exclusions (tapbacks,
    # attachments, etc.). The meaningful condition is that at least one
    # eligible conversation-day was recognized as a duplicate and no new piece
    # was emitted; callers check the latter separately.
    return summary.skipped_duplicate > 0


def run(args: argparse.Namespace) -> int:
    try:
        options = parse_options(args)
        ac.check_output_privacy(
            [options.output_dir, options.manifest_path, options.contact_map_path],
            allow_public=False,
            tool=TOOL_NAME,
        )
        if not options.db_path.is_file():
            raise AcquisitionError(f"no such database file: {options.db_path}")

        windowed = options.since is not None or options.until is not None
        # Dry-run has first precedence: no confirmation/receipt checks apply.
        if not options.dry_run:
            if options.live_smoke_confirmed and not sys.stdin.isatty():
                raise AcquisitionError(
                    "--live-smoke-confirmed requires an interactive TTY; "
                    "review real recipient data outside agent/logged channels."
                )
            enforce_live_smoke_gate(
                output_dir=options.output_dir,
                db_path=options.db_path,
                windowed=windowed,
            )

        summary = ac.RunSummary(
            draft_manifest_path=(
                None if options.dry_run else str(options.manifest_path)
            ),
            output_dir=str(options.output_dir),
        )
        stats = DiscoveryStats()
        try:
            connection = open_chat_db(options.db_path)
            try:
                schema = schema_preflight(connection)
                messages = discover_messages(
                    connection,
                    schema,
                    since=options.since,
                    until=options.until,
                    max_messages=options.max_messages,
                    summary=summary,
                    stats=stats,
                )
            finally:
                connection.close()
        except sqlite3.OperationalError as exc:
            if _looks_like_access_denial(exc):
                raise FullDiskAccessError(
                    _full_disk_access_message(options.db_path, exc)
                ) from exc
            raise AcquisitionError(f"SQLite read failed: {exc}") from exc

        if not options.include_group_chats:
            retained: list[OutgoingMessage] = []
            for message in messages:
                if message.is_group:
                    _log_skip(summary, "group_chat_excluded", message.rowid)
                else:
                    retained.append(message)
            messages = retained

        contacts = ac.StableRedactionMap(
            options.contact_map_path,
            label_prefix="contact",
            map_name="contact map",
            error_factory=AcquisitionError,
        )
        known_handles = {message.chat_identifier for message in messages}
        name_map = _load_name_map(options.name_map_path, known_handles)
        items = discover_items(messages, contacts, name_map)
        prepared = [
            piece
            for item in items
            if (piece := process_item(item, options=options, summary=summary))
            is not None
        ]
        _preflight_replacements(prepared, options)

        if options.dry_run:
            seen_hashes: set[str] = set()
            samples = 0
            for candidate in prepared:
                if summary.acquired >= options.max_items:
                    break
                existing = _existing_day(options.output_dir, candidate.day_key)
                if existing is not None and not _replacement_needed(existing, candidate):
                    summary.skipped_duplicate += 1
                    summary.log_skip(
                        reason="duplicate_hash", url=candidate.item.locator,
                        detail=str(existing.meta_path),
                    )
                    continue
                if existing is None and (
                    candidate.piece.content_hash in seen_hashes
                    or ac.content_hash_already_present(
                        candidate.piece.content_hash, options.output_dir
                    )
                ):
                    summary.skipped_duplicate += 1
                    summary.log_skip(
                        reason="duplicate_hash", url=candidate.item.locator,
                        detail="dry-run in-memory/output-dir match",
                    )
                    continue
                if existing is not None:
                    summary.log_skip(
                        reason="superseded_by_grown_day",
                        url=candidate.item.locator,
                        detail=str(existing.meta_path),
                    )
                seen_hashes.add(candidate.piece.content_hash)
                summary.acquired += 1
                summary.total_cleaned_words += candidate.piece.word_count
                summary.record_strip_meta(candidate.piece.preprocessing_meta)
                if samples < 5:
                    sys.stderr.write(f"  would write: {candidate.piece.title}\n")
                    samples += 1
        else:
            for candidate in prepared:
                if summary.acquired >= options.max_items:
                    break
                _emit_prepared(candidate, options=options, summary=summary)

            if summary.acquired or _is_dedupe_only(summary):
                options.output_dir.mkdir(parents=True, exist_ok=True)
                contacts.save()
                write_corpus_readme(options.output_dir, summary, stats)
                if options.live_smoke_confirmed and windowed:
                    write_live_smoke_receipt(
                        options.output_dir,
                        options.db_path,
                        since=options.since,
                        until=options.until,
                    )

        sys.stderr.write("\n" + render_summary(summary, stats, options=options))
        if summary.acquired == 0 and not _is_dedupe_only(summary):
            sys.stderr.write(
                "No conversation-day pieces were acquired. Check the date "
                "window, group-chat option, and word floor.\n"
            )
            return 1
        return 0
    except (AcquisitionError, PossiblyMergedDayError) as exc:
        sys.stderr.write(f"{TOOL_NAME}: {exc}\n")
        return 2


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
