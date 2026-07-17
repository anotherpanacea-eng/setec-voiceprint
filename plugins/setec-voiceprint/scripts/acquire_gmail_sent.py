#!/usr/bin/env python3
"""acquire_gmail_sent.py - acquire the user's own SENT Gmail prose from a
Google Takeout mbox export as an identity-baseline corpus.

Local, offline sibling to acquire_imessage_sent.py. Reads a Takeout
`.mbox` file (NOT the live Gmail API — no OAuth surface), keeps only
messages the user actually sent (From matches --own-address AND the
X-Gmail-Labels header carries the Sent token), trims quoted-reply,
signature, and forwarded content fail-closed, redacts recipients behind
stable recipient_NN labels, and emits identity-baseline manifest entries
(corpus_role: identity_baseline / use: ["voice_profile"] /
register: personal / consent_status: author_consent). One document per
email; no cross-message bundling.

Per internal/2026-07-10-acquire-gmail-sent-spec.md.

NOTE: the exact X-Gmail-Labels Sent token and Gmail's HTML reply DOM are
inferred, not yet verified against a real Takeout export; both carry
mechanical safety nets (an empty-corpus WARNING and a residual-attribution
fail-closed backstop) and are flagged in the spec's open questions.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import email
import email.header
import email.utils
import hashlib
import json
import mailbox
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_gmail_sent"
SCRAPER_VERSION = "1.0"

DEFAULT_MIN_WORDS = 40
DEFAULT_SENT_TOKEN = "Sent"
# Gmail Smart Compose (predictive completion) launched 2018-05; on/after
# that date an outgoing message may be AI-assisted and the mbox carries no
# signal for it, so ai_status is "unknown". Distinct from era's boundary.
SMART_COMPOSE_DATE = _dt.date(2018, 5, 1)

_ATTR_LINE = re.compile(r"^On .+ wrote:\s*$")
_ATTR_TERMINAL = re.compile(r".*\bwrote:\s*$")
_ORIGINAL_MESSAGE = re.compile(r"^-+\s*Original Message\s*-+\s*$", re.I)
_FORWARD_MARKERS = (
    re.compile(r"^-+\s*Forwarded message\s*-+\s*$", re.I),
    re.compile(r"^Begin forwarded message:\s*$", re.I),
)
_SIG_DELIM = "-- "
_HTML_STRIP_SELECTORS = ("div.gmail_quote", ".gmail_attr", "blockquote")
_ADDR_TOKEN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_MESSAGE_ID = re.compile(r"<[^<>\s]+>")
_PRIVATE_LOCATOR_DOMAIN = b"setec-gmail-private-locator-v1\x00"


# --------------- date + era ---------------------------------------


def _ai_status_from_date(date: _dt.date | None) -> str:
    """pre_ai_human only before Smart Compose's launch; unknown otherwise
    (Smart Compose/Smart Reply involvement is unverifiable from the mbox).
    Derived from the date directly, NOT from era, since Smart Compose
    (2018) predates era's pre_chatgpt boundary (2022)."""
    if date is not None and date < SMART_COMPOSE_DATE:
        return "pre_ai_human"
    return "unknown"


def _message_datetime(msg: Message) -> _dt.datetime | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("non-empty Date header is malformed") from exc
    if parsed is None:
        raise ValueError("non-empty Date header is malformed")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # A timezone-naive Date cannot be canonicalized to UTC. Treat it as
        # undated (order-degraded) rather than aborting the whole run, matching
        # the missing-Date path; genuinely malformed headers still refuse above.
        return None
    return parsed


def _message_order_timestamp(parsed: _dt.datetime | None) -> str | None:
    if parsed is None:
        return None
    return parsed.astimezone(_dt.timezone.utc).isoformat(timespec="seconds")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out)


def _header_message_ids(value: str | None) -> list[str]:
    """Structurally parse RFC-style message-id tokens without storing them."""
    return _MESSAGE_ID.findall(value or "")


def _private_locator(kind: str, raw: str) -> str:
    payload = kind.encode("ascii") + b"\x00" + raw.encode("utf-8")
    return "sha256:" + hashlib.sha256(_PRIVATE_LOCATOR_DOMAIN + payload).hexdigest()


def build_thread_roots(messages) -> dict[str, str | None]:
    """Resolve Message-ID parent chains globally without exporting raw ids."""
    parents: dict[str, str | None] = {}
    ambiguous: set[str] = set()
    for msg in messages:
        own = _header_message_ids(msg.get("Message-ID"))
        if len(own) != 1:
            continue
        own_id = own[0]
        references = _header_message_ids(msg.get("References"))
        in_reply_to = _header_message_ids(msg.get("In-Reply-To"))
        parent: str | None
        if references:
            parent = references[0]
        elif len(in_reply_to) == 1:
            parent = in_reply_to[0]
        elif in_reply_to:
            parent = None
            ambiguous.add(own_id)
        else:
            parent = None
        if own_id in parents:
            ambiguous.add(own_id)
        else:
            parents[own_id] = parent

    resolved: dict[str, str | None] = {}
    for start in parents:
        if start in resolved:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while True:
            if current in resolved:
                root = resolved[current]
                break
            if current in ambiguous:
                root = None
                break
            if current in positions:
                # A directed parent cycle invalidates the cycle and every row
                # whose only route reaches it.
                root = None
                break
            if current not in parents or parents[current] is None:
                root = current
                if current in parents:
                    resolved[current] = root
                break
            positions[current] = len(path)
            path.append(current)
            current = parents[current]
        for message_id in reversed(path):
            resolved[message_id] = root
    return resolved


def private_message_locators(
    msg: Message, thread_roots: dict[str, str | None] | None = None,
) -> tuple[str | None, str | None]:
    """Return non-reversible (thread, entry) preimages for R1a export.

    A thread roots at the first References id, then In-Reply-To, then its own
    Message-ID for a newly composed thread.  The entry locator always needs the
    message's own Message-ID.  Missing structure is explicit: the later exporter
    marks that record atomic/degraded instead of inventing a thread.
    """
    references = _header_message_ids(msg.get("References"))
    parents = _header_message_ids(msg.get("In-Reply-To"))
    own = _header_message_ids(msg.get("Message-ID"))
    entry_id = own[0] if len(own) == 1 else None
    if entry_id is None:
        root_id = None
    elif thread_roots is not None:
        root_id = thread_roots.get(entry_id)
    elif references:
        root_id = references[0]
    elif parents:
        # An IRT-only chain needs the global graph; never guess that the immediate
        # parent is the stable root when no graph was supplied.
        root_id = None
    else:
        root_id = entry_id
    thread = _private_locator("thread", root_id) if root_id else None
    entry = _private_locator("entry", entry_id) if entry_id else None
    return thread, entry


# --------------- selection ----------------------------------------


def _own_address_match(msg: Message, own: set[str]) -> bool:
    from_hdr = msg.get("From", "")
    _, addr = email.utils.parseaddr(from_hdr)
    return addr.lower() in own


def _gmail_labels(msg: Message) -> list[str]:
    raw = msg.get("X-Gmail-Labels")
    if raw is None:
        return []
    return [tok.strip() for tok in raw.split(",")]


def _is_sent(msg: Message, sent_token: str) -> bool:
    return sent_token in _gmail_labels(msg)


def _is_auto_generated(msg: Message) -> bool:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    prec = (msg.get("Precedence") or "").strip().lower()
    return prec in {"bulk", "list"}


# --------------- MIME body extraction -----------------------------


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    decoded = payload.decode(charset, "replace")
    # MIME payload bytes commonly retain CRLF even though the rest of the
    # acquisition pipeline is line-oriented around LF.  Canonicalize here,
    # before format=flowed unwrapping and preprocessing.  Otherwise CR remains
    # attached to each logical line, defeating both ``line.endswith(" ")`` and
    # LF-based whitespace rules.  It also prevents Windows text-mode writing
    # from translating CRLF a second time into CR-CR-LF.
    return decoded.replace("\r\n", "\n").replace("\r", "\n")


def _flowed_line_parts(line: str) -> tuple[int, str]:
    """Return RFC 3676 quote depth and unstuffed line content.

    Space-stuffing is applied after any quote marks on transmission.  Remove
    that transport-only space before deciding whether the content is flowed.
    Keeping quote depth separate is security-relevant: joining an authored
    line to a quoted continuation would hide the continuation's leading ``>``
    from the downstream third-party-quote trimmer.
    """
    depth = 0
    while depth < len(line) and line[depth] == ">":
        depth += 1
    content = line[depth:]
    if content.startswith(" "):
        content = content[1:]
    return depth, content


def _render_flowed_line(depth: int, content: str) -> str:
    """Render an unstuffed flowed line while retaining its quote boundary."""
    return (">" * depth) + content


def _unwrap_flowed(text: str, *, delsp: bool = False) -> str:
    """Unwrap RFC 3676 ``format=flowed`` text.

    A content line ending in a space is soft-wrapped and may join only to a
    following line at the same quote depth.  The soft-break space is retained
    unless the MIME part declares ``DelSp=yes``.  Space-stuffing is removed,
    and the exact ``-- `` signature delimiter is never treated as flowed so
    Phase 2's signature detection still finds it.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        depth, content = _flowed_line_parts(lines[i])
        if content == _SIG_DELIM:
            out.append(_render_flowed_line(depth, content))
            i += 1
            continue
        while (
            content.endswith(" ")
            and content != _SIG_DELIM
            and i + 1 < len(lines)
        ):
            next_depth, next_content = _flowed_line_parts(lines[i + 1])
            if next_depth != depth:
                break
            if next_content == _SIG_DELIM:
                break
            # A same-depth reply attribution is a semantic boundary, not a
            # flowed continuation.  Joining it to authored text would hide
            # its leading ``On `` from the downstream quote trimmer.
            if _ATTR_LINE.match(next_content.strip()):
                break
            # RFC 3676 preserves the trailing soft-break space by default.
            # Only ``DelSp=yes`` removes it before the physical lines join.
            content = (content[:-1] if delsp else content) + next_content
            i += 1
        out.append(_render_flowed_line(depth, content))
        i += 1
    return "\n".join(out)


def extract_body(msg: Message) -> str:
    """Prefer text/plain; fall back to text/html with the Gmail
    reply-DOM containers stripped BEFORE flattening. Returns "" when no
    body part exists (attachment-only message)."""
    plain: Optional[str] = None
    plain_flowed = False
    plain_delsp = False
    html: Optional[str] = None
    for part in _iter_body_parts(msg):
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _decode_part(part)
            plain_flowed = (part.get_param("format") or "").lower() == "flowed"
            plain_delsp = (part.get_param("delsp") or "").lower() == "yes"
        elif ctype == "text/html" and html is None:
            html = _decode_part(part)
    if plain is not None and plain.strip():
        return _unwrap_flowed(plain, delsp=plain_delsp) if plain_flowed else plain
    if html is not None and html.strip():
        text, _ = ac.html_to_text(html, strip_selectors=_HTML_STRIP_SELECTORS)
        return text
    return ""


def _iter_body_parts(part: Message):
    """Yield inline body leaves without descending into attachments.

    ``Message.walk()`` traverses the children of an attached
    ``message/rfc822`` part, where their leaf text often has no attachment
    disposition of its own. Prune attachment containers before recursion so
    third-party attached messages can never become the sender's baseline.
    """
    disposition = (part.get("Content-Disposition") or "").lower()
    if "attachment" in disposition or part.get_content_type() == "message/rfc822":
        return
    if part.is_multipart():
        payload = part.get_payload()
        if isinstance(payload, list):
            for child in payload:
                yield from _iter_body_parts(child)
        return
    yield part


# --------------- quote / signature / forward trimming -------------


@dataclass
class TrimResult:
    kept: str
    dropped: bool = False
    drop_reason: str = ""
    forwarded_no_comment: bool = False
    kept_no_signal: bool = False
    kept_no_headers: bool = False
    residual_attribution: bool = False


def _find_forward_marker(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        for pat in _FORWARD_MARKERS:
            if pat.match(line.strip()):
                return i
    return -1


def _find_quote_boundary(lines: list[str]) -> int:
    """Return the first line index that begins quoted content, or -1.
    Attribution lines are matched wrap-aware by back-scanning from a
    terminal `... wrote:` line up to a leading `On ` within a small
    window, so a wrapped `On <date>, <name> <addr>\\nwrote:` is trimmed
    whole (its address never survives)."""
    for i, line in enumerate(lines):
        s = line.strip()
        if _ATTR_LINE.match(s) or _ORIGINAL_MESSAGE.match(s):
            return i
        if _ATTR_TERMINAL.match(s):
            # wrapped attribution: back-scan for the 'On ' start.
            j = i
            lo = max(0, i - 6)
            while j >= lo:
                if lines[j].lstrip().startswith("On "):
                    return j
                j -= 1
        if s.startswith(">"):
            return i
    return -1


def _has_weak_quote_signal(body: str) -> bool:
    low = body.lower()
    if "wrote:" in low or "original message" in low:
        # anchored: a line-ending 'wrote:' or the original-message rule,
        # not the bare word mid-sentence.
        for line in body.split("\n"):
            s = line.strip()
            if _ATTR_TERMINAL.match(s) or _ORIGINAL_MESSAGE.match(s):
                return True
    for line in body.split("\n"):
        if line.lstrip().startswith(">"):
            return True
    return False


def _trim_signature(text: str, own_sig_lines: Optional[list[str]]) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line == _SIG_DELIM:
            return "\n".join(lines[:i]).rstrip()
    if own_sig_lines:
        joined = "\n".join(own_sig_lines).strip()
        idx = text.rfind(joined)
        if idx != -1 and text[idx:].strip() == joined:
            return text[:idx].rstrip()
    return text.rstrip()


def trim_body(
    body: str,
    *,
    is_reply: bool,
    own_sig_lines: Optional[list[str]],
) -> TrimResult:
    lines = body.split("\n")

    # Phase 1a: forwarded marker (checked first).
    fwd = _find_forward_marker(lines)
    if fwd != -1:
        lead = "\n".join(lines[:fwd]).strip()
        if not lead:
            return TrimResult(kept="", dropped=True,
                              forwarded_no_comment=True,
                              drop_reason="forwarded_no_comment")
        composed = lead
    else:
        boundary = _find_quote_boundary(lines)
        if boundary != -1:
            composed = "\n".join(lines[:boundary]).rstrip()
        else:
            # No boundary located.
            if is_reply and _has_weak_quote_signal(body):
                return TrimResult(kept="", dropped=True,
                                  drop_reason="quote_boundary_unresolved")
            composed = body.rstrip()
            if is_reply:
                # confirmed reply, no quote-shaped content -> clean.
                res = TrimResult(kept="", kept_no_signal=True)
            else:
                res = TrimResult(kept="", kept_no_headers=True)
            res.kept = _trim_signature(composed, own_sig_lines)
            return res

    # Phase 2: always-run signature trim on whatever Phase 1 kept.
    kept = _trim_signature(composed, own_sig_lines)
    return TrimResult(kept=kept)


# --------------- recipient redaction ------------------------------


def _validate_name_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("--name-map must contain a JSON object")
    normalized_addresses = {
        address.strip().lower()
        for address in raw
        if isinstance(address, str) and address.strip()
    }
    clean: dict[str, str] = {}
    for address, label in raw.items():
        if not isinstance(address, str) or not isinstance(label, str):
            raise ValueError("--name-map keys and values must be strings")
        normalized_address = address.strip().lower()
        normalized_label = label.strip()
        if not normalized_address or not normalized_label:
            raise ValueError("--name-map keys and values must be non-empty")
        if "\n" in normalized_label or "\r" in normalized_label:
            raise ValueError("--name-map labels must be single-line")
        folded_label = normalized_label.casefold()
        if _ADDR_TOKEN.search(normalized_label) or any(
            address.casefold() in folded_label for address in normalized_addresses
        ):
            raise ValueError("a --name-map label contains a raw recipient address")
        clean[normalized_address] = normalized_label
    return clean


def _addresses(msg: Message, header: str) -> list[str]:
    out = []
    for _, addr in email.utils.getaddresses(msg.get_all(header, [])):
        if addr:
            out.append(addr)
    return out


def build_notes(msg: Message, recipients: ac.StableRedactionMap, *,
                forwarded_with_comment: bool) -> str:
    to = _addresses(msg, "To")
    cc = _addresses(msg, "Cc")
    primary = to[0] if to else (cc[0] if cc else None)
    n_others = (len(to) - 1 if to else 0) + len(cc)
    if primary is None:
        base = "to: (no visible recipient)"
    else:
        label = recipients.display(primary)
        base = f"to: {label}"
        if n_others > 0:
            base += f" +{n_others} others"
    if forwarded_with_comment:
        base += "; forwarded-with-comment"
    return base


# --------------- live-smoke gate ----------------------------------


RECEIPT_NAME = ".live_smoke_passed"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _param_fingerprint(opts: "Options") -> str:
    h = hashlib.sha256()
    h.update("\n".join(sorted(opts.own_address)).encode())
    h.update(b"\x00")
    h.update(opts.sent_label_token.encode())
    h.update(b"\x00")
    h.update(str(opts.recipient_map_path).encode())
    h.update(b"\x00")
    if opts.name_map_path and opts.name_map_path.exists():
        h.update(_file_sha256(opts.name_map_path).encode())
    return h.hexdigest()


def enforce_live_smoke_gate(opts: "Options", *, windowed: bool) -> None:
    if opts.dry_run:
        return
    receipt = opts.output_dir / RECEIPT_NAME
    if not windowed:
        ok = False
        if receipt.exists():
            try:
                rec = json.loads(receipt.read_text(encoding="utf-8"))
                ok = (rec.get("mbox_sha256") == _file_sha256(opts.mbox_path)
                      and rec.get("params") == _param_fingerprint(opts))
            except (OSError, json.JSONDecodeError):
                ok = False
        if not ok:
            raise SystemExit(
                f"{TOOL_NAME}: refusing an unwindowed (full-export) write "
                "with no valid live-smoke receipt for this mbox + filter "
                "parameters. Run a windowed write (--since ...) first, "
                "review it, then re-run that windowed command with "
                "--live-smoke-confirmed."
            )


def write_live_smoke_receipt(opts: "Options", *, window: str) -> None:
    receipt = opts.output_dir / RECEIPT_NAME
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({
            "mbox_sha256": _file_sha256(opts.mbox_path),
            "params": _param_fingerprint(opts),
            "window": window,
            "confirmed": True,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# --------------- options + summary --------------------------------


@dataclass
class Options:
    mbox_path: Path
    own_address: set[str]
    persona: str
    author: str
    register: str
    since: _dt.date | None
    until: _dt.date | None
    min_words: int
    sent_label_token: str
    recipient_map_path: Path
    name_map_path: Optional[Path]
    own_sig_lines: Optional[list[str]]
    output_dir: Path
    manifest_path: Path
    max_items: int
    allow_empty: bool
    dry_run: bool
    live_smoke_confirmed: bool


@dataclass
class Summary:
    acquired: int = 0
    undated: int = 0
    skipped_not_own: int = 0
    skipped_not_sent: int = 0
    skipped_auto_generated: int = 0
    skipped_no_body: int = 0
    skipped_below_min_words: int = 0
    skipped_forwarded_no_comment: int = 0
    skipped_quote_unresolved: int = 0
    skipped_empty_after_preprocess: int = 0
    skipped_html_residual_attribution: int = 0
    skipped_duplicate: int = 0
    kept_reply_no_quote: int = 0
    kept_no_headers: int = 0
    ai_status_unknown: int = 0
    total_cleaned_words: int = 0
    reply_checked: int = 0
    trailing_counter: Counter = field(default_factory=Counter)

    def render(self, *, manifest_path: Path, recipient_map_path: Path) -> str:
        unresolved_rate = (
            100.0 * self.skipped_quote_unresolved / self.reply_checked
            if self.reply_checked else 0.0
        )
        lines = [
            f"Acquired: {self.acquired} files (individual emails, of which "
            f"{self.undated} undated)",
            f"Skipped (duplicate hash): {self.skipped_duplicate}",
            f"Skipped (not in Sent label): {self.skipped_not_sent}",
            f"Skipped (below min-words): {self.skipped_below_min_words}",
            f"Skipped (no body content): {self.skipped_no_body}",
            "Skipped (forwarded, no added comment): "
            f"{self.skipped_forwarded_no_comment}",
            f"Skipped (auto-generated): {self.skipped_auto_generated}",
            "Skipped (quote-boundary unresolved): "
            f"{self.skipped_quote_unresolved}",
            "Skipped (HTML pre-strip residual attribution): "
            f"{self.skipped_html_residual_attribution}",
            "Skipped (empty after preprocessing): "
            f"{self.skipped_empty_after_preprocess}",
            "Kept - reply with no quote-shaped content detected: "
            f"{self.kept_reply_no_quote}",
            "Kept with no boundary check possible, no reply headers "
            f"present: {self.kept_no_headers}",
            f"Reply/forward messages checked for boundary: "
            f"{self.reply_checked}  (unresolved rate: {unresolved_rate:.1f}%)",
            f"Acquired pieces with ai_status=unknown: {self.ai_status_unknown} "
            f"({100.0 * self.ai_status_unknown / self.acquired:.1f}% of total)"
            if self.acquired else
            "Acquired pieces with ai_status=unknown: 0",
            f"Total cleaned words: {self.total_cleaned_words:,}",
            f"Draft manifest: {manifest_path}",
            f"Recipient map written to: {recipient_map_path} "
            "(KEEP PRIVATE, do not commit)",
        ]
        # Signature-contamination visibility guardrail.
        for text, n in self.trailing_counter.most_common(3):
            level = "WARNING: " if n >= 10 else ""
            lines.append(
                f"{level}Most common trailing text (check for an un-caught "
                f"signature): {text!r} ({n} occurrences)"
            )
        return "\n".join(lines) + "\n"


README_TEXT = """\
# Sent-Gmail identity-baseline corpus (PRIVATE - voice-cloning input)

Acquired by `acquire_gmail_sent.py` from a Google Takeout mbox export,
one document per sent email.

Disclosed, deliberate scope boundaries:

- **In-body PII accepted, not redacted.** The user's own composed email
  bodies may name or quote other people; content-level redaction was not
  attempted (private-tree storage covers it). Recipient *addresses* ARE
  redacted behind stable `recipient_NN` labels; raw addresses live only
  in the sibling `recipient_map.json`, which must never be committed.
- **Keyboard-composed** (contrast the sibling sent-iMessage corpus, which
  is mobile-composed/QuickType-shaped).
- **`ai_status: unknown` from 2018-05-01 on.** Gmail Smart Compose could
  have touched any message from then; it is unverifiable from the mbox.
  No consumer currently *filters* on ai_status, so the label is honest
  but not mechanically enforced.
- **Reading this corpus via voice_profile.py requires BOTH
  `--use voice_profile` AND `--ai-status unknown`** (each defaults
  otherwise, and either default alone filters the whole corpus out).
  `--ai-status` is single-valued; to include both pre_ai_human and
  unknown pieces, run twice or pre-filter the manifest.
- **Quote/signature trimming caveats:** interleaved replies may be
  truncated; undelimited Gmail-web signatures survive unless
  `--own-signature-lines` is supplied; the fail-open (no reply headers,
  no recognized pattern) case keeps the body as-is; weak-signal
  detection is English-locale/plain-text oriented.
"""


# --------------- CLI ----------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description=(
        "Acquire the user's own SENT Gmail prose from a Google Takeout "
        "mbox export as an identity-baseline corpus."))
    p.add_argument("--mbox-path", required=True)
    p.add_argument("--own-address", nargs="+", required=True)
    p.add_argument("--persona", default="joshua")
    p.add_argument("--author", default=None)
    p.add_argument("--register", default="personal")
    p.add_argument("--since", default=None)
    p.add_argument("--until", default=None)
    p.add_argument("--min-words-per-piece", type=int, default=DEFAULT_MIN_WORDS)
    p.add_argument("--sent-label-token", default=DEFAULT_SENT_TOKEN)
    p.add_argument("--recipient-map-path", default=None)
    p.add_argument("--name-map", default=None)
    p.add_argument("--own-signature-lines", default=None)
    p.add_argument("--consent-status", default="author_consent")
    p.add_argument("--max-items", type=int, default=10**9)
    ac.add_allow_empty_arg(p)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live-smoke-confirmed", action="store_true")
    p.add_argument("--emit-manifest", default=None)
    p.add_argument("--output-dir", default=None)
    return p


def _parse_date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def parse_options(args: argparse.Namespace) -> Options:
    if args.consent_status != "author_consent":
        raise SystemExit(f"{TOOL_NAME}: --consent-status must be author_consent.")
    if args.register != "personal":
        raise SystemExit(f"{TOOL_NAME}: --register must be 'personal' in v1.")
    persona = args.persona
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = (
            ac.resolve_baselines_dir() / "identity" / "personal_email" / persona
        )
    manifest_path = (
        Path(args.emit_manifest).expanduser() if args.emit_manifest
        else output_dir / "draft_manifest.jsonl"
    )
    recipient_map_path = (
        Path(args.recipient_map_path).expanduser() if args.recipient_map_path
        else output_dir / "recipient_map.json"
    )
    sig_lines = None
    if args.own_signature_lines:
        sig_lines = Path(args.own_signature_lines).expanduser().read_text(
            encoding="utf-8").splitlines()
    return Options(
        mbox_path=Path(args.mbox_path).expanduser(),
        own_address={a.lower() for a in args.own_address},
        persona=persona,
        author=args.author or persona,
        register=args.register,
        since=_parse_date(args.since),
        until=_parse_date(args.until),
        min_words=args.min_words_per_piece,
        sent_label_token=args.sent_label_token,
        recipient_map_path=recipient_map_path,
        name_map_path=Path(args.name_map).expanduser() if args.name_map else None,
        own_sig_lines=sig_lines,
        output_dir=output_dir,
        manifest_path=manifest_path,
        max_items=args.max_items,
        allow_empty=bool(args.allow_empty),
        dry_run=args.dry_run,
        live_smoke_confirmed=args.live_smoke_confirmed,
    )


# --------------- per-message processing ---------------------------


@dataclass(frozen=True)
class PreparedMessage:
    piece: ac.AcquiredPiece
    private_thread_locator: str | None
    private_entry_locator: str | None
    private_order_timestamp: str | None


def process_message(
    msg: Message,
    opts: Options,
    recipients: ac.StableRedactionMap,
    summary: Summary,
    thread_roots: dict[str, str | None] | None = None,
):
    """Return an AcquiredPiece or None, updating summary counters."""
    if not _own_address_match(msg, opts.own_address):
        summary.skipped_not_own += 1
        return None
    if not _is_sent(msg, opts.sent_label_token):
        summary.skipped_not_sent += 1
        return None
    if _is_auto_generated(msg):
        summary.skipped_auto_generated += 1
        return None

    message_datetime = _message_datetime(msg)
    date = message_datetime.date() if message_datetime is not None else None
    # Undated messages are included in every run (windowed or not) so a
    # windowed live-smoke review actually sees them before a full write.
    if date is not None:
        if opts.since and date < opts.since:
            return None
        if opts.until and date > opts.until:
            return None

    body = extract_body(msg)
    if not body.strip():
        summary.skipped_no_body += 1
        return None

    is_reply = bool(msg.get("In-Reply-To") or msg.get("References"))
    fwd_present = _find_forward_marker(body.split("\n")) != -1
    if is_reply or fwd_present:
        summary.reply_checked += 1

    trim = trim_body(body, is_reply=is_reply, own_sig_lines=opts.own_sig_lines)
    if trim.dropped:
        if trim.forwarded_no_comment:
            summary.skipped_forwarded_no_comment += 1
        else:
            summary.skipped_quote_unresolved += 1
        return None
    if trim.kept_no_signal:
        summary.kept_reply_no_quote += 1
    if trim.kept_no_headers:
        summary.kept_no_headers += 1

    kept = trim.kept

    # HTML residual-attribution backstop (fail-closed if the DOM strip
    # missed a reply container and an attribution line survived).
    for line in kept.split("\n"):
        if _ATTR_LINE.match(line.strip()):
            summary.skipped_html_residual_attribution += 1
            return None

    cleaned, meta = ac.preprocess_text(kept)
    cleaned = cleaned.strip()
    if not cleaned:
        summary.skipped_empty_after_preprocess += 1
        return None
    if len(re.findall(r"\S+", cleaned)) < opts.min_words:
        summary.skipped_below_min_words += 1
        return None

    # signature-visibility tally (last 30-80 chars, whitespace-collapsed).
    tail = re.sub(r"\s+", " ", cleaned).strip()[-80:]
    if len(tail) >= 30:
        summary.trailing_counter[tail] += 1

    title = _decode_header(msg.get("Subject"))
    notes = build_notes(msg, recipients, forwarded_with_comment=fwd_present)

    piece = ac.AcquiredPiece(
        title=title,
        author=opts.author,
        persona=opts.persona,
        register=opts.register,
        date_written=date,
        source_url="gmail_takeout_local",
        cleaned_text=cleaned,
        raw_byte_length=len(body.encode("utf-8")),
        preprocessing_meta=meta,
        acquired_via=f"{TOOL_NAME}_{_dt.date.today().isoformat()}",
        consent_status="author_consent",
        era=ac.era_from_date(date),
        notes=notes,
    )
    thread_locator, entry_locator = private_message_locators(msg, thread_roots)
    return PreparedMessage(
        piece,
        thread_locator,
        entry_locator,
        _message_order_timestamp(message_datetime),
    )


def _augment_private_meta(meta_path: Path, prepared: PreparedMessage) -> None:
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if prepared.private_thread_locator is not None:
        data["author_corpus_thread_locator"] = prepared.private_thread_locator
    if prepared.private_entry_locator is not None:
        data["author_corpus_entry_locator"] = prepared.private_entry_locator
    data["author_corpus_order_timestamp"] = prepared.private_order_timestamp
    meta_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def emit_piece(prepared: PreparedMessage, opts: Options, summary: Summary) -> None:
    piece = prepared.piece
    if ac.content_hash_already_present(piece.content_hash, opts.output_dir):
        summary.skipped_duplicate += 1
        return
    text_path, meta_path = ac.write_piece(
        piece, output_dir=opts.output_dir, scraper_version=SCRAPER_VERSION,
    )
    _augment_private_meta(meta_path, prepared)
    ai_status = _ai_status_from_date(piece.date_written)
    entry = ac.compose_manifest_entry(
        piece,
        text_path=text_path,
        manifest_relative_to=opts.manifest_path.parent,
        corpus_role="identity_baseline",
        use=["voice_profile"],
        ai_status=ai_status,
    )
    entry.setdefault("era", piece.era)
    entry.setdefault("consent_status", piece.consent_status)
    entry.setdefault("acquired_via", piece.acquired_via)
    ac.append_manifest_entry(opts.manifest_path, entry)
    summary.acquired += 1
    summary.total_cleaned_words += piece.word_count
    if ai_status == "unknown":
        summary.ai_status_unknown += 1
    if piece.date_written is None:
        summary.undated += 1


# --------------- run ----------------------------------------------


def run(args: argparse.Namespace) -> int:
    opts = parse_options(args)
    if not opts.mbox_path.exists():
        sys.stderr.write(f"{TOOL_NAME}: no such mbox: {opts.mbox_path}\n")
        return 1

    windowed = opts.since is not None or opts.until is not None
    if opts.live_smoke_confirmed and not sys.stdin.isatty():
        sys.stderr.write(
            f"{TOOL_NAME}: --live-smoke-confirmed requires an interactive "
            "terminal (it attests a human reviewed real recipient data); "
            "stdin is not a TTY.\n")
        return 2

    if not opts.dry_run:
        ac.check_output_privacy(
            [opts.output_dir, opts.manifest_path, opts.recipient_map_path],
            allow_public=False, tool=TOOL_NAME,
        )
        enforce_live_smoke_gate(opts, windowed=windowed)

    try:
        name_map = None
        if opts.name_map_path and opts.name_map_path.exists():
            name_map = json.loads(opts.name_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"{TOOL_NAME}: invalid --name-map: {exc}\n")
        return 2
    try:
        recipients = ac.StableRedactionMap(
            opts.recipient_map_path,
            label_prefix="recipient",
            normalize_key=lambda address: address.strip().lower(),
            display_names=_validate_name_map(name_map or {}),
            reuse_gaps=False,
            map_name="recipient map",
        )
    except ValueError as exc:
        sys.stderr.write(f"{TOOL_NAME}: invalid recipient map: {exc}\n")
        return 2

    summary = Summary()
    box = mailbox.mbox(str(opts.mbox_path))
    thread_roots = build_thread_roots(box)
    own_matches_total = 0
    for msg in box:
        if summary.acquired >= opts.max_items:
            break
        if _own_address_match(msg, opts.own_address):
            own_matches_total += 1
        try:
            prepared = process_message(
                msg, opts, recipients, summary, thread_roots=thread_roots,
            )
        except ValueError:
            sys.stderr.write(
                f"{TOOL_NAME}: malformed private message metadata; refusing.\n"
            )
            return 2
        if prepared is None:
            continue
        if opts.dry_run:
            summary.acquired += 1
            if summary.acquired <= 5:
                sys.stderr.write(f"  would write: {prepared.piece.title!r}\n")
            continue
        emit_piece(prepared, opts, summary)

    dedupe_only = summary.acquired == 0 and summary.skipped_duplicate > 0
    empty_is_error = (
        summary.acquired == 0
        and not dedupe_only
        and not opts.allow_empty
    )
    if empty_is_error:
        if (
            own_matches_total >= 10
            and summary.skipped_not_sent == own_matches_total
        ):
            sys.stderr.write(
                f"ERROR: {own_matches_total} messages matched --own-address but "
                "0 passed the Sent-label filter. Your Gmail 'Sent' label may be "
                "localized; pass --sent-label-token with the actual token.\n"
            )
        else:
            sys.stderr.write(
                "ERROR: no messages were acquired. Check --own-address, the date "
                "window, Sent-label token, and word floor.\n"
            )

    if not opts.dry_run and not empty_is_error:
        recipients.save()
        (opts.output_dir / "README.md").write_text(README_TEXT, encoding="utf-8")
        # The receipt attests that the operator reviewed real acquired output.
        # An explicit --allow-empty run may succeed, but cannot mint that proof.
        if opts.live_smoke_confirmed and windowed and summary.acquired > 0:
            write_live_smoke_receipt(opts, window=f"{opts.since}..{opts.until}")

    sys.stderr.write("\n" + summary.render(
        manifest_path=opts.manifest_path,
        recipient_map_path=opts.recipient_map_path,
    ))
    return 1 if empty_is_error else 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
