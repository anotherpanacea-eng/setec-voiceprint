#!/usr/bin/env python3
"""acquire_imessage_sent.py - acquire the user's own SENT iMessage/SMS
prose from the macOS Messages database as identity-baseline corpus.

Local, offline sibling to the other ``acquire_*`` scripts. Reads
``~/Library/Messages/chat.db`` read-only, keeps only ``is_from_me = 1``
rows (the user's own outgoing text), bundles them into per-conversation-
day documents, redacts every recipient identity behind a stable
``contact_NN`` label, and emits identity-baseline manifest entries
(``corpus_role: identity_baseline`` / ``use: ["voice_profile"]`` /
``register: personal`` / ``consent_status: author_consent``).

Per internal/2026-07-09-acquire-imessage-sent-spec.md. This is the v1
implementation: attributedBody bodies are decoded by a best-effort
byte-scan (regex-extract), and any reply row whose only body came from
attributedBody is dropped fail-closed rather than risk emitting an
un-trimmed quote. A structural typedstream parser is a future increment.

Access requires macOS Full Disk Access for the invoking process; SQLite
returns "authorization denied" otherwise, which this script reports and
exits on rather than attempting any workaround.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_imessage_sent"
SCRAPER_VERSION = "1.0"

# Seconds between the Unix epoch (1970-01-01) and the Cocoa/Apple epoch
# (2001-01-01), both UTC. message.date is Cocoa-epoch-based.
COCOA_EPOCH_OFFSET = 978_307_200
# A Cocoa-seconds value stays below this well past the year 2300; a
# Cocoa-nanoseconds value for any real date after 2001 is far above it.
# Per-row detection, since chat.db mixes units across macOS versions.
NS_THRESHOLD = 10_000_000_000
# iMessage attachment placeholder (Unicode object-replacement char).
OBJ_REPLACEMENT = "￼"

DEFAULT_MIN_WORDS = 150
DEFAULT_MAX_MESSAGES = 200_000

# Columns the extractor depends on. Absent/renamed -> hard fail (we are
# almost certainly pointed at the wrong database).
REQUIRED_MESSAGE_COLUMNS = (
    "text", "attributedBody", "is_from_me",
    "associated_message_type", "item_type", "date",
)
# Reply-linking column has drifted across macOS versions; try known
# variants in order. If none is present the run still proceeds, but any
# reply-shaped row degrades to a fail-closed drop.
REPLY_LINK_COLUMN_VARIANTS = ("thread_originator_guid",)


# --------------- date + body helpers ------------------------------


def _era_from_date(date: _dt.date | None) -> str:
    """Map a date to the manifest ``era`` enum. Boundaries copied
    verbatim from acquire_epub.py's private helper (promotion to a
    shared acquisition_core helper is a named follow-up for the sibling
    Gmail acquirer's PR, not this one)."""
    if date is None:
        return "undated"
    if date < _dt.date(2022, 11, 1):
        return "pre_chatgpt"
    if date < _dt.date(2024, 7, 1):
        return "pre_ai_widespread"
    return "post_ai_widespread"


def _ai_status_from_date(date: _dt.date | None) -> str:
    """Derive ai_status from the date directly, NOT from era.

    Texting predates ubiquitous on-device AI features for the whole
    pre-2024 window, so pre-2024-07-01 messages are ``pre_ai_human``.
    On or after mid-2024, Apple Intelligence smart-reply/rewrite could
    have touched an outgoing message and chat.db carries no per-message
    signal for it, so we emit ``unknown`` rather than assert a claim we
    cannot verify. (Never hardcode a constant here.)"""
    if date is not None and date < _dt.date(2024, 7, 1):
        return "pre_ai_human"
    return "unknown"


def apple_date_to_local_date(raw: int | float | None) -> _dt.date | None:
    """Convert a message.date value to the user's local calendar date.

    Detects seconds vs. nanoseconds per row (chat.db mixes units across
    macOS versions) and converts through the local timezone so bundling
    keys on the date the user actually saw."""
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(val) > NS_THRESHOLD:
        val = val / 1_000_000_000.0
    unix_ts = val + COCOA_EPOCH_OFFSET
    try:
        return _dt.datetime.fromtimestamp(unix_ts).date()
    except (OverflowError, OSError, ValueError):
        return None


def decode_attributed_body(blob: bytes | None) -> str:
    """Best-effort (regex-extract) plain-string extraction from a
    serialized NSAttributedString ``attributedBody`` blob.

    v1 does not structurally parse the typedstream/NSKeyedArchiver
    archive; it locates the NSString payload's length-prefixed byte run
    following the class marker. Returns "" when no string is found.
    Callers treat an attributedBody-only *reply* row as fail-closed
    (dropped) regardless of what this returns, since a byte-scan cannot
    prove the extracted run excludes quoted parent content."""
    if not blob:
        return ""
    marker = b"NSString"
    idx = blob.find(marker)
    if idx == -1:
        return ""
    # After the class name: a short streamtyped header (\x01\x94\x84\x01
    # then the 0x2b '+' marker == 5 bytes), then a length, then bytes.
    p = idx + len(marker) + 5
    if p >= len(blob):
        return ""
    length = blob[p]
    p += 1
    if length == 0x81:
        length = int.from_bytes(blob[p:p + 2], "little")
        p += 2
    elif length == 0x82:
        length = int.from_bytes(blob[p:p + 4], "little")
        p += 4
    raw = blob[p:p + length]
    if not raw:
        return ""
    return raw.decode("utf-8", "replace")


# --------------- database access + schema pre-flight --------------


class FullDiskAccessError(RuntimeError):
    """chat.db could not be opened because macOS TCC denied access."""


def open_chat_db(db_path: Path) -> sqlite3.Connection:
    """Open chat.db read-only, translating the TCC/Full-Disk-Access
    denial into a clear, actionable error rather than a bare
    OperationalError. No workaround is attempted."""
    uri = f"file:{db_path}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "authorization denied" in msg or "unable to open database" in msg:
            raise FullDiskAccessError(
                f"Could not open {db_path}: {exc}.\n"
                "This is almost always macOS Full Disk Access, not a file "
                "permission. Grant Full Disk Access to the process invoking "
                "this script (System Settings -> Privacy & Security -> Full "
                "Disk Access), or point --db-path at a copy you made with "
                "`cp ~/Library/Messages/chat.db /tmp/chat_copy.db`."
            ) from exc
        raise


@dataclass
class SchemaInfo:
    reply_column: Optional[str]  # present reply-linking column, or None


def schema_preflight(conn: sqlite3.Connection) -> SchemaInfo:
    """Verify the fixed-set columns exist (hard-fail if not — we are
    pointed at the wrong database), and detect which reply-linking
    column variant is present (degrade, not fail, if none)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(message)")}
    missing = [c for c in REQUIRED_MESSAGE_COLUMNS if c not in cols]
    if missing:
        raise SystemExit(
            f"{TOOL_NAME}: message table is missing expected column(s) "
            f"{missing}. This does not look like a Messages chat.db "
            "(schema pre-flight failed); refusing to proceed."
        )
    reply_col = next(
        (c for c in REPLY_LINK_COLUMN_VARIANTS if c in cols), None
    )
    return SchemaInfo(reply_column=reply_col)


# --------------- message model + discovery ------------------------


@dataclass
class OutgoingMessage:
    """One is_from_me=1 message that survived exclusion filtering."""
    rowid: int
    chat_identifier: str
    date: _dt.date | None
    text: str
    is_group: bool


def _select_sql(reply_col: Optional[str]) -> str:
    reply_expr = f"m.{reply_col}" if reply_col else "NULL"
    return f"""
        SELECT m.ROWID, m.text, m.attributedBody, m.date,
               m.associated_message_type, m.item_type,
               {reply_expr} AS reply_link,
               c.chat_identifier, c.style, c.room_name
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE m.is_from_me = 1
        ORDER BY m.date ASC, m.ROWID ASC
    """


@dataclass
class DiscoveryStats:
    scanned: int = 0
    skipped_tapback: int = 0
    skipped_group_action: int = 0
    skipped_attachment_only: int = 0
    skipped_quoted_reply_unresolved: int = 0


def discover_messages(
    conn: sqlite3.Connection,
    schema: SchemaInfo,
    *,
    since: _dt.date | None,
    until: _dt.date | None,
    max_messages: int,
    stats: DiscoveryStats,
) -> list[OutgoingMessage]:
    """Yield the user's own outgoing prose messages, post-exclusion.

    A message can be joined to more than one chat; the first chat wins
    (dedup by ROWID). Exclusions, in order: tapbacks
    (associated_message_type != 0), group-action rows (item_type != 0),
    attachment-only rows (no usable body), and fail-closed drops of
    attributedBody-only reply rows."""
    seen: set[int] = set()
    out: list[OutgoingMessage] = []
    count = 0
    # When no reply-linking column is present we cannot tell a reply
    # from a non-reply, so every attributedBody-only row degrades to a
    # fail-closed drop (we can't prove it carries no quoted content).
    reply_detection = schema.reply_column is not None
    for row in conn.execute(_select_sql(schema.reply_column)):
        count += 1
        if count > max_messages:
            raise SystemExit(
                f"{TOOL_NAME}: exceeded --max-messages ({max_messages}). "
                "Narrow the run with --since/--until."
            )
        (rowid, text, attr_body, raw_date, assoc_type, item_type,
         reply_link, chat_identifier, style, room_name) = row
        if rowid in seen:
            continue
        seen.add(rowid)
        stats.scanned += 1

        # Tapbacks / reactions.
        if assoc_type not in (0, None):
            stats.skipped_tapback += 1
            continue
        # Group membership / rename action rows (Apple-generated text,
        # not the user's prose, and may name other participants).
        if item_type not in (0, None):
            stats.skipped_group_action += 1
            continue

        date = apple_date_to_local_date(raw_date)
        if since and (date is None or date < since):
            continue
        if until and (date is None or date > until):
            continue

        text_val = (text or "").strip()
        text_is_placeholder = (
            not text_val or set(text_val) <= {OBJ_REPLACEMENT}
        )

        is_reply = reply_link not in (None, "")

        if text_is_placeholder:
            # No plain text: try attributedBody.
            decoded = decode_attributed_body(attr_body).strip()
            if not decoded or set(decoded) <= {OBJ_REPLACEMENT}:
                stats.skipped_attachment_only += 1
                continue
            if is_reply or not reply_detection:
                # v1 fail-closed: a byte-scan of an attributedBody reply
                # cannot prove the decoded run excludes quoted content —
                # and without a reply-linking column we can't even rule a
                # row out as a reply, so drop all attributedBody-only
                # rows in that degraded case.
                stats.skipped_quoted_reply_unresolved += 1
                continue
            body = decoded
        else:
            # text-column body. Reply rows keep their own text as-is; the
            # quoted parent lives in a separate row we never read (only
            # is_from_me=1 rows are selected).
            body = text_val

        is_group = bool(room_name) or (style not in (None, 45))
        out.append(OutgoingMessage(
            rowid=rowid,
            chat_identifier=chat_identifier or "unknown",
            date=date,
            text=body,
            is_group=is_group,
        ))
    return out


# --------------- contact-map redaction ----------------------------


class ContactMap:
    """Persisted, stable ``chat_identifier -> contact_NN`` map. Loaded
    and extended in place; existing numbers are never reassigned. The
    raw handle lives ONLY in this file (kept under a private root)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._map: dict[str, str] = {}
        if path.exists():
            try:
                self._map = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._map = {}
        used = {
            int(v.split("_")[-1]) for v in self._map.values()
            if v.startswith("contact_") and v.split("_")[-1].isdigit()
        }
        self._next = (max(used) + 1) if used else 1

    def label(self, chat_identifier: str) -> str:
        if chat_identifier not in self._map:
            self._map[chat_identifier] = f"contact_{self._next:02d}"
            self._next += 1
        return self._map[chat_identifier]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._map, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


# --------------- bundling + piece construction --------------------


@dataclass
class DayBundle:
    label: str  # redacted contact_NN
    date: _dt.date | None
    is_group: bool
    lines: list[str] = field(default_factory=list)


def bundle_by_day(
    messages: Iterable[OutgoingMessage], contacts: ContactMap,
) -> list[DayBundle]:
    """Group outgoing messages by (redacted contact label, local date),
    preserving send order within a bundle."""
    groups: dict[tuple[str, _dt.date | None], DayBundle] = {}
    for msg in messages:
        label = contacts.label(msg.chat_identifier)
        key = (label, msg.date)
        bundle = groups.get(key)
        if bundle is None:
            bundle = DayBundle(label=label, date=msg.date, is_group=msg.is_group)
            groups[key] = bundle
        bundle.lines.append(msg.text)
    return list(groups.values())


def conversation_day_key(label: str, date: _dt.date | None) -> str:
    return f"{label}|{date.isoformat() if date else 'undated'}"


# --------------- live-smoke receipt gating ------------------------


RECEIPT_NAME = ".live_smoke_passed"


def _db_fingerprint(db_path: Path) -> str:
    h = hashlib.sha256()
    with db_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def enforce_live_smoke_gate(
    *,
    output_dir: Path,
    db_path: Path,
    windowed: bool,
    confirm: bool,
    dry_run: bool,
) -> None:
    """Precedence: (1) --dry-run exits caller-side before this. (2) An
    unwindowed run without a valid receipt for this db refuses. (3) A
    windowed run proceeds; --live-smoke-confirmed (TTY-only) writes the
    receipt after a successful write. (4) An unwindowed run with a valid
    receipt proceeds."""
    if dry_run:
        return
    receipt = output_dir / RECEIPT_NAME
    fingerprint = _db_fingerprint(db_path)
    if not windowed:
        ok = False
        if receipt.exists():
            try:
                rec = json.loads(receipt.read_text(encoding="utf-8"))
                ok = rec.get("db_sha256") == fingerprint
            except (OSError, json.JSONDecodeError):
                ok = False
        if not ok:
            raise SystemExit(
                f"{TOOL_NAME}: refusing an unwindowed (full-history) write "
                "with no valid live-smoke receipt for this database. Run a "
                "windowed write (--since ...) first, manually review the "
                "output, then re-run that windowed command with "
                "--live-smoke-confirmed."
            )


def write_live_smoke_receipt(
    output_dir: Path, db_path: Path, *, window: str,
) -> None:
    receipt = output_dir / RECEIPT_NAME
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "db_sha256": _db_fingerprint(db_path),
                "window": window,
                "confirmed": True,
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


# --------------- options + summary --------------------------------


@dataclass
class Options:
    db_path: Path
    persona: str
    author: str
    register: str
    since: _dt.date | None
    until: _dt.date | None
    min_words: int
    max_messages: int
    output_dir: Path
    manifest_path: Path
    contact_map_path: Path
    include_group_chats: bool
    max_items: int
    dry_run: bool
    live_smoke_confirmed: bool


@dataclass
class Summary:
    acquired: int = 0
    skipped_duplicate: int = 0
    skipped_superseded: int = 0
    skipped_below_min_words: int = 0
    skipped_tapback: int = 0
    skipped_attachment_only: int = 0
    skipped_group_action: int = 0
    skipped_quoted_reply_unresolved: int = 0
    skipped_empty_after_preprocess: int = 0
    total_cleaned_words: int = 0

    def render(self, *, manifest_path: Path, contact_map_path: Path) -> str:
        lines = [
            f"Acquired: {self.acquired} files (conversation-days)",
            f"Skipped (duplicate hash): {self.skipped_duplicate}",
            f"Skipped (superseded by grown day): {self.skipped_superseded}",
            f"Skipped (below min-words): {self.skipped_below_min_words}",
            f"Skipped (tapback/reaction): {self.skipped_tapback}",
            f"Skipped (attachment-only): {self.skipped_attachment_only}",
            f"Skipped (group action/rename): {self.skipped_group_action}",
            "Skipped (quoted-reply unresolved): "
            f"{self.skipped_quoted_reply_unresolved}",
            "Skipped (empty after preprocessing): "
            f"{self.skipped_empty_after_preprocess}",
            f"Total cleaned words: {self.total_cleaned_words:,}",
            f"Draft manifest: {manifest_path}",
            f"Contact map written to: {contact_map_path} "
            "(KEEP PRIVATE, do not commit)",
        ]
        return "\n".join(lines) + "\n"


# --------------- README disclosure --------------------------------


README_TEXT = """\
# Sent-iMessage identity-baseline corpus (PRIVATE — voice-cloning input)

Acquired by `acquire_imessage_sent.py` from the user's own `is_from_me=1`
Messages, one document per (recipient, calendar-day) bundle.

Deliberate, consented scope boundaries recorded here so a later reader
isn't surprised:

- **In-body PII is accepted, not redacted.** These are the user's own
  composed messages; they may name or quote other people the user chose
  to mention. Content-level PII redaction was not attempted and was not
  the goal — the private-tree storage posture (this corpus never leaves
  disk) is what covers it, exactly as for the user's other baselines.
  Recipient *identities* (the addressed handle) ARE redacted, behind
  stable `contact_NN` labels; the raw handles live only in the sibling
  `contact_map.json`, which must never be committed.
- **Register provenance: mobile-composed.** This text is predominantly
  phone-typed and therefore subject to iOS/macOS QuickType
  autocorrect/predictive shaping that free-composed prose (a manuscript,
  a blog post) is not. That's a real difference in how the prose was
  produced, not a defect — note it when comparing this baseline against
  other registers.
"""


# --------------- CLI ----------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire the user's own SENT iMessage/SMS prose from a macOS "
            "Messages chat.db as an identity-baseline corpus. Requires "
            "Full Disk Access for the invoking process."
        ),
        epilog=(
            "Recommended: run against a copy — "
            "`cp ~/Library/Messages/chat.db /tmp/chat_copy.db` — and pass "
            "--db-path /tmp/chat_copy.db, since a live database can be "
            "locked by Messages.app. ai_status is derived from the message "
            "date (pre_ai_human before 2024-07-01, else unknown) and is "
            "never a hardcoded constant. --min-words-per-piece defaults to "
            "150, deliberately below the essayistic 500-word floor: texting "
            "is short-form and a higher floor would drop most real "
            "conversation-days."
        ),
    )
    default_db = Path.home() / "Library" / "Messages" / "chat.db"
    p.add_argument("--db-path", default=str(default_db))
    p.add_argument("--persona", default="joshua")
    p.add_argument("--author", default=None)
    p.add_argument("--register", default="personal")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-words-per-piece", type=int, default=DEFAULT_MIN_WORDS)
    p.add_argument("--max-messages", type=int, default=DEFAULT_MAX_MESSAGES)
    p.add_argument("--contact-map-path", default=None)
    p.add_argument(
        "--include-group-chats", dest="include_group_chats",
        action="store_true", default=True,
    )
    p.add_argument(
        "--no-include-group-chats", dest="include_group_chats",
        action="store_false",
    )
    p.add_argument(
        "--consent-status", default="author_consent",
        help="Fixed for this acquirer; only 'author_consent' is accepted.",
    )
    p.add_argument("--max-items", type=int, default=10**9)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--live-smoke-confirmed", action="store_true",
        help="Operator attestation, TTY-only, after manually reviewing a "
             "windowed real write. Writes the .live_smoke_passed receipt.",
    )
    p.add_argument("--emit-manifest", default=None)
    p.add_argument("--output-dir", default=None)
    return p


def _parse_date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    return _dt.date.fromisoformat(value)


def parse_options(args: argparse.Namespace) -> Options:
    if args.consent_status != "author_consent":
        raise SystemExit(
            f"{TOOL_NAME}: --consent-status must be 'author_consent' "
            "(this acquirer only makes sense for the user's own texts)."
        )
    if args.register != "personal":
        raise SystemExit(f"{TOOL_NAME}: --register must be 'personal' in v1.")
    persona = args.persona
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = (
            ac.resolve_baselines_dir() / "identity" / "personal" / persona
        )
    manifest_path = (
        Path(args.emit_manifest).expanduser() if args.emit_manifest
        else output_dir / "draft_manifest.jsonl"
    )
    contact_map_path = (
        Path(args.contact_map_path).expanduser() if args.contact_map_path
        else output_dir / "contact_map.json"
    )
    return Options(
        db_path=Path(args.db_path).expanduser(),
        persona=persona,
        author=args.author or persona,
        register=args.register,
        since=_parse_date(args.since),
        until=_parse_date(args.until),
        min_words=args.min_words_per_piece,
        max_messages=args.max_messages,
        output_dir=output_dir,
        manifest_path=manifest_path,
        contact_map_path=contact_map_path,
        include_group_chats=args.include_group_chats,
        max_items=args.max_items,
        dry_run=args.dry_run,
        live_smoke_confirmed=args.live_smoke_confirmed,
    )


# --------------- emit ---------------------------------------------


def _augment_meta(meta_path: Path, key: str, value: str) -> None:
    """Add one discrete field to this acquirer's own .meta.json sidecar
    after write_piece — used by the grown-day supersede scan, which
    keys on the redacted label + date (never the raw handle)."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data[key] = value
    meta_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _existing_meta_for_day(output_dir: Path, day_key: str) -> Path | None:
    if not output_dir.exists():
        return None
    for meta_file in output_dir.glob("*.meta.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("conversation_day_key") == day_key:
            return meta_file
    return None


def process_bundle(bundle: DayBundle, options: Options, summary: Summary):
    """Build an AcquiredPiece from a day bundle, or None if it drops."""
    label = bundle.label
    date = bundle.date
    raw_text = "\n".join(bundle.lines)
    cleaned, meta = ac.preprocess_text(raw_text)
    cleaned = cleaned.strip()
    if not cleaned:
        summary.skipped_empty_after_preprocess += 1
        return None
    if len(re.findall(r"\S+", cleaned)) < options.min_words:
        summary.skipped_below_min_words += 1
        return None
    date_label = date.isoformat() if date else "undated"
    piece = ac.AcquiredPiece(
        title=f"{label} - {date_label}",
        author=options.author,
        persona=options.persona,
        register=options.register,
        date_written=date,
        source_url="imessage_local",
        cleaned_text=cleaned,
        raw_byte_length=len(raw_text.encode("utf-8")),
        preprocessing_meta=meta,
        acquired_via=f"{TOOL_NAME}_{_dt.date.today().isoformat()}",
        consent_status="author_consent",
        era=_era_from_date(date),
        notes="group_chat" if bundle.is_group else "direct",
    )
    return piece


def emit_piece(piece, bundle: DayBundle, options: Options, summary: Summary):
    day_key = conversation_day_key(bundle.label, bundle.date)
    # Grown-day supersede: replace an existing unmerged bundle for the
    # same (label, date).
    existing = _existing_meta_for_day(options.output_dir, day_key)
    if existing is not None:
        stem = existing.name[: -len(".meta.json")]
        (options.output_dir / f"{stem}.txt").unlink(missing_ok=True)
        existing.unlink(missing_ok=True)
        summary.skipped_superseded += 1
    elif ac.content_hash_already_present(piece.content_hash, options.output_dir):
        summary.skipped_duplicate += 1
        return

    text_path, meta_path = ac.write_piece(
        piece, output_dir=options.output_dir, scraper_version=SCRAPER_VERSION,
    )
    _augment_meta(meta_path, "conversation_day_key", day_key)
    entry = ac.compose_manifest_entry(
        piece,
        text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
        corpus_role="identity_baseline",
        use=["voice_profile"],
        ai_status=_ai_status_from_date(piece.date_written),
    )
    entry.setdefault("era", piece.era)
    entry.setdefault("consent_status", piece.consent_status)
    entry.setdefault("acquired_via", piece.acquired_via)
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    summary.total_cleaned_words += piece.word_count


# --------------- run ----------------------------------------------


def run(args: argparse.Namespace) -> int:
    options = parse_options(args)

    if not options.db_path.exists():
        sys.stderr.write(f"{TOOL_NAME}: no such database: {options.db_path}\n")
        return 1

    windowed = options.since is not None or options.until is not None

    # TTY guard on the confirmation flag.
    if options.live_smoke_confirmed and not sys.stdin.isatty():
        sys.stderr.write(
            f"{TOOL_NAME}: --live-smoke-confirmed requires an interactive "
            "terminal (it attests a human reviewed real recipient data "
            "outside any logged/agent channel); stdin is not a TTY.\n"
        )
        return 2

    # Privacy guard on every write path (no --allow-public-output exists).
    if not options.dry_run:
        ac.check_output_privacy(
            [options.output_dir, options.manifest_path, options.contact_map_path],
            allow_public=False, tool=TOOL_NAME,
        )
        enforce_live_smoke_gate(
            output_dir=options.output_dir,
            db_path=options.db_path,
            windowed=windowed,
            confirm=options.live_smoke_confirmed,
            dry_run=options.dry_run,
        )

    try:
        conn = open_chat_db(options.db_path)
    except FullDiskAccessError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2

    stats = DiscoveryStats()
    summary = Summary()
    try:
        schema = schema_preflight(conn)
        messages = discover_messages(
            conn, schema,
            since=options.since, until=options.until,
            max_messages=options.max_messages, stats=stats,
        )
    finally:
        conn.close()

    summary.skipped_tapback = stats.skipped_tapback
    summary.skipped_group_action = stats.skipped_group_action
    summary.skipped_attachment_only = stats.skipped_attachment_only
    summary.skipped_quoted_reply_unresolved = stats.skipped_quoted_reply_unresolved

    if not options.include_group_chats:
        messages = [m for m in messages if not m.is_group]

    contacts = ContactMap(options.contact_map_path)
    bundles = bundle_by_day(messages, contacts)

    if options.dry_run:
        kept = 0
        for bundle in bundles:
            piece = process_bundle(bundle, options, summary)
            if piece is not None:
                kept += 1
                if kept <= 5:
                    sys.stderr.write(f"  would write: {piece.title}\n")
        summary.acquired = kept
        sys.stderr.write("\n" + summary.render(
            manifest_path=options.manifest_path,
            contact_map_path=options.contact_map_path,
        ))
        return 0

    options.output_dir.mkdir(parents=True, exist_ok=True)
    for bundle in bundles:
        if summary.acquired >= options.max_items:
            break
        piece = process_bundle(bundle, options, summary)
        if piece is not None:
            emit_piece(piece, bundle, options, summary)

    contacts.save()
    (options.output_dir / "README.md").write_text(README_TEXT, encoding="utf-8")

    if options.live_smoke_confirmed and windowed:
        window = f"{options.since}..{options.until}"
        write_live_smoke_receipt(options.output_dir, options.db_path, window=window)

    sys.stderr.write("\n" + summary.render(
        manifest_path=options.manifest_path,
        contact_map_path=options.contact_map_path,
    ))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
