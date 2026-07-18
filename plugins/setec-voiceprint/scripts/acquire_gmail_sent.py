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

_ATTR_TERMINAL = re.compile(r".*\bwrote:\s*$")
_ORIGINAL_MESSAGE = re.compile(r"^-+\s*Original Message\s*-+\s*$", re.I)
_FORWARD_MARKERS = (
    re.compile(r"^-+\s*Forwarded message\s*-+\s*$", re.I),
    re.compile(r"^Begin forwarded message:\s*$", re.I),
)
# Case-folded marker text used by the one-pass flowed-run scanner below.
_DASHED_SPAN_MARKERS = (
    ("original message", "original_message"),
    ("forwarded message", "forward"),
)
_BEGIN_FORWARDED_SPAN = re.compile(r"Begin forwarded message:", re.I)
_ATTR_SPAN_TERMINAL = re.compile(r"\bwrote:\s*")


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


def _record_thread_parent(
    msg: Message,
    parents: dict[str, str | None],
    ambiguous: set[str],
) -> str | None:
    own = _header_message_ids(msg.get("Message-ID"))
    if len(own) != 1:
        return None
    own_id = own[0]
    references = _header_message_ids(msg.get("References"))
    in_reply_to = _header_message_ids(msg.get("In-Reply-To"))
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
    return own_id


def _resolve_thread_roots(
    parents: dict[str, str | None], ambiguous: set[str],
) -> dict[str, str | None]:
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


def build_thread_roots(messages) -> dict[str, str | None]:
    """Resolve Message-ID parent chains globally without exporting raw ids."""
    parents: dict[str, str | None] = {}
    ambiguous: set[str] = set()
    for msg in messages:
        _record_thread_parent(msg, parents, ambiguous)
    return _resolve_thread_roots(parents, ambiguous)


def build_thread_roots_and_target_keys(
    box: mailbox.mbox, target_locators: set[str],
) -> tuple[dict[str, str | None], dict[str, object]]:
    """Index roots and retain only mailbox keys for committed locators."""
    parents: dict[str, str | None] = {}
    ambiguous: set[str] = set()
    target_keys: dict[str, object] = {}
    duplicate_targets: set[str] = set()
    for key in box.iterkeys():
        msg = box.get_message(key)
        own_id = _record_thread_parent(msg, parents, ambiguous)
        if own_id is None:
            continue
        entry_locator = _private_locator("entry", own_id)
        if entry_locator not in target_locators:
            continue
        if entry_locator in target_keys:
            # A Takeout export can carry the same sent message twice (a
            # label-overlap / export-merge duplicate). Byte-identical copies
            # are ONE source: either key reproduces the committed row
            # identically, so binding to the first is safe and the rerun /
            # resume path must not refuse. Differing content under one
            # Message-ID stays a hard refusal: the committed row's source
            # binding would be ambiguous (never guess which copy backs it).
            if box.get_bytes(key) != box.get_bytes(target_keys[entry_locator]):
                duplicate_targets.add(entry_locator)
        else:
            target_keys[entry_locator] = key
    if duplicate_targets:
        raise ManifestIntegrityError(
            "source mbox repeats a committed stable entry locator with "
            "differing message content"
        )
    return _resolve_thread_roots(parents, ambiguous), target_keys


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


@dataclass(frozen=True)
class _LineProvenance:
    """Transport facts that must survive RFC 3676 rendering."""

    quote_depth: int
    space_stuffed: bool
    authored_literal_gt: bool = False


class _ProvenancedText(str):
    """A normal string carrying immutable RFC 3676 line provenance."""

    def __new__(
        cls,
        value: str,
        line_provenance: tuple[_LineProvenance, ...],
    ):
        instance = super().__new__(cls, value)
        object.__setattr__(instance, "_line_provenance", line_provenance)
        return instance

    @property
    def line_provenance(self) -> tuple[_LineProvenance, ...]:
        return self._line_provenance

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("_ProvenancedText is immutable")


@dataclass(frozen=True)
class _ExtractedBody:
    text: str
    line_provenance: tuple[_LineProvenance, ...] | None = None


@dataclass(frozen=True)
class _SemanticLine:
    content: str
    quote_depth: int
    boundary: str | None = None


def _flowed_line_parts(line: str) -> tuple[_LineProvenance, str]:
    """Return RFC 3676 provenance and unstuffed line content.

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
    space_stuffed = content.startswith(" ")
    if space_stuffed:
        content = content[1:]
    return _LineProvenance(
        depth,
        space_stuffed,
        depth == 0 and space_stuffed and content.startswith(">"),
    ), content


def _render_flowed_line(depth: int, content: str) -> str:
    """Render an unstuffed flowed line while retaining its quote boundary."""
    return (">" * depth) + content


def _semantic_line_classes(
    contents: list[str],
    provenance: list[_LineProvenance] | tuple[_LineProvenance, ...],
    *,
    delsp: bool = False,
) -> tuple[_SemanticLine, ...]:
    """Classify every semantic boundary from one shared source of truth."""
    if len(contents) != len(provenance):
        raise ValueError("line provenance must align with body lines")

    boundaries: list[str | None] = [None] * len(contents)
    authored_literal_gt = [
        source.authored_literal_gt for source in provenance
    ]
    for i, content in enumerate(contents):
        if authored_literal_gt[i]:
            continue
        stripped = content.strip()
        if content == _SIG_DELIM:
            boundaries[i] = "signature"
        elif _ORIGINAL_MESSAGE.match(stripped):
            boundaries[i] = "original_message"
        elif any(pattern.match(stripped) for pattern in _FORWARD_MARKERS):
            boundaries[i] = "forward"
    # Reconstruct each maximal same-depth flowed run exactly once. Physical
    # start/end offsets let markers split across any number of RFC 3676
    # fragments remain visible without repeatedly rescanning a growing string.
    run_start = 0
    while run_start < len(contents):
        if authored_literal_gt[run_start] or boundaries[run_start] is not None:
            run_start += 1
            continue
        run_end = run_start
        while (
            run_end + 1 < len(contents)
            and contents[run_end].endswith(" ")
            and provenance[run_end + 1].quote_depth
            == provenance[run_start].quote_depth
            and not authored_literal_gt[run_end + 1]
            and boundaries[run_end + 1] is None
        ):
            run_end += 1
        if run_end == run_start:
            run_start += 1
            continue

        chunks: list[str] = []
        start_offsets: list[int] = []
        end_offset_to_index: dict[int, int] = {}
        cursor = 0
        for index in range(run_start, run_end + 1):
            start_offsets.append(cursor)
            chunk = contents[index]
            if index < run_end and delsp:
                chunk = chunk[:-1]
            chunks.append(chunk)
            cursor += len(chunk)
            end_offset_to_index[cursor] = index
        joined = "".join(chunks)

        # Scan fixed-form markers once per reconstructed run. A physical start
        # and physical end are both required; dash runs and whitespace are
        # advanced monotonically so hostile fragmentation remains linear.
        start_offset_to_index = {
            offset: run_start + local_index
            for local_index, offset in enumerate(start_offsets)
        }
        for match in _BEGIN_FORWARDED_SPAN.finditer(joined):
            offset = match.start()
            index = start_offset_to_index.get(offset)
            if index is None:
                continue
            terminal_offset = match.end()
            while terminal_offset < len(joined) and joined[terminal_offset].isspace():
                terminal_offset += 1
            terminal = end_offset_to_index.get(terminal_offset)
            if terminal is not None:
                for span_index in range(index, terminal + 1):
                    boundaries[span_index] = "forward"

        dash_cursor = 0
        physical_start_cursor = 0
        while True:
            offset = joined.find("-", dash_cursor)
            if offset < 0:
                break
            dash_end = offset + 1
            while dash_end < len(joined) and joined[dash_end] == "-":
                dash_end += 1
            dash_cursor = dash_end
            while (
                physical_start_cursor < len(start_offsets)
                and start_offsets[physical_start_cursor] < offset
            ):
                physical_start_cursor += 1
            if (
                physical_start_cursor >= len(start_offsets)
                or start_offsets[physical_start_cursor] >= dash_end
            ):
                continue
            index = run_start + physical_start_cursor
            phrase_start = dash_end
            while phrase_start < len(joined) and joined[phrase_start].isspace():
                phrase_start += 1
            boundary = None
            phrase_end = phrase_start
            for phrase, candidate_boundary in _DASHED_SPAN_MARKERS:
                if joined[phrase_start:phrase_start + len(phrase)].casefold() == phrase:
                    boundary = candidate_boundary
                    phrase_end = phrase_start + len(phrase)
                    break
            if boundary is None:
                continue
            while phrase_end < len(joined) and joined[phrase_end].isspace():
                phrase_end += 1
            if phrase_end >= len(joined) or joined[phrase_end] != "-":
                continue
            terminal_offset = phrase_end + 1
            while terminal_offset < len(joined) and joined[terminal_offset] == "-":
                terminal_offset += 1
            dash_cursor = terminal_offset
            while terminal_offset < len(joined) and joined[terminal_offset].isspace():
                terminal_offset += 1
            terminal = end_offset_to_index.get(terminal_offset)
            if terminal is not None:
                for span_index in range(index, terminal + 1):
                    boundaries[span_index] = boundary

        # Attribution starts and terminals are found independently in the one
        # reconstructed string. Pair each terminal with the nearest preceding
        # physical start that reconstructs to "On "; there is no fragment cap.
        on_candidates = [
            (offset, run_start + local_index)
            for local_index, offset in enumerate(start_offsets)
            if joined.startswith("On ", offset)
        ]
        candidate_cursor = 0
        latest_candidate: tuple[int, int] | None = None
        terminal_fragment_cursor = 0
        for match in _ATTR_SPAN_TERMINAL.finditer(joined):
            terminal = end_offset_to_index.get(match.end())
            if terminal is None:
                continue
            while (
                candidate_cursor < len(on_candidates)
                and on_candidates[candidate_cursor][0] <= match.start()
            ):
                latest_candidate = on_candidates[candidate_cursor]
                candidate_cursor += 1
            if latest_candidate is not None:
                _, attribution_start = latest_candidate
                # One physical 'On ' start can introduce only one attribution.
                # Consume it whether the span is accepted or crosses a marker.
                latest_candidate = None
                if not any(
                    boundaries[index] is not None
                    for index in range(attribution_start, terminal + 1)
                ):
                    for index in range(attribution_start, terminal + 1):
                        boundaries[index] = "attribution"
                    continue
            # A terminal with no trustworthy "On " start is still a hard weak
            # signal. Mark every physical fragment that contributed to wrote:.
            while (
                terminal_fragment_cursor + 1 < len(start_offsets)
                and start_offsets[terminal_fragment_cursor + 1] <= match.start()
            ):
                terminal_fragment_cursor += 1
            terminal_start = run_start + terminal_fragment_cursor
            for index in range(terminal_start, terminal + 1):
                if boundaries[index] is None:
                    boundaries[index] = "attribution_terminal_unresolved"

        run_start = run_end + 1

    # Classify an entire wrapped attribution so its start cannot be erased by
    # flowed joining before the later terminal 'wrote:' line is inspected.
    for terminal, content in enumerate(contents):
        if authored_literal_gt[terminal]:
            continue
        if not _ATTR_TERMINAL.match(content.strip()):
            continue
        # A terminal-looking physical continuation belongs to authored text
        # when an earlier same-depth flowed segment began with a space-stuffed
        # literal '>'. Preserve that provenance until the segments are joined.
        cursor = terminal - 1
        authored_flow_continuation = False
        while cursor >= 0:
            if (
                provenance[cursor].quote_depth
                != provenance[terminal].quote_depth
                or not contents[cursor].endswith(" ")
            ):
                break
            if authored_literal_gt[cursor]:
                authored_flow_continuation = True
                break
            if boundaries[cursor] is not None:
                break
            cursor -= 1
        if authored_flow_continuation:
            continue
        start = terminal
        lo = max(0, terminal - 6)
        found_start = False
        while start >= lo:
            if contents[start].lstrip().startswith("On "):
                for index in range(start, terminal + 1):
                    if boundaries[index] is None:
                        boundaries[index] = "attribution"
                found_start = True
                break
            start -= 1
        if not found_start and boundaries[terminal] is None:
            boundaries[terminal] = "attribution_terminal_unresolved"

    return tuple(
        _SemanticLine(content, provenance[i].quote_depth, boundaries[i])
        for i, content in enumerate(contents)
    )


def _rendered_semantic_lines(
    lines: list[str],
    provenance: tuple[_LineProvenance, ...] | None,
) -> tuple[_SemanticLine, ...]:
    """Recover classifier inputs without reinterpreting flowed authored text."""
    if provenance is not None:
        if len(lines) != len(provenance):
            raise ValueError("line provenance must align with body lines")
        contents: list[str] = []
        for line, source in zip(lines, provenance):
            prefix = ">" * source.quote_depth
            if prefix and not line.startswith(prefix):
                raise ValueError("rendered quote depth does not match provenance")
            contents.append(line[len(prefix):] if prefix else line)
        return _semantic_line_classes(contents, provenance)

    # For non-flowed/plain and HTML bodies, retain the existing conservative
    # inference that leading quote marks after indentation are genuine quotes.
    contents = []
    inferred = []
    for line in lines:
        content = line.lstrip()
        depth = 0
        while depth < len(content) and content[depth] == ">":
            depth += 1
        contents.append(content[depth:])
        inferred.append(_LineProvenance(depth, False))
    return _semantic_line_classes(contents, inferred)


def _unwrap_flowed_details(text: str, *, delsp: bool = False) -> _ExtractedBody:
    """Unwrap RFC 3676 ``format=flowed`` text.

    A content line ending in a space is soft-wrapped and may join only to a
    following line at the same quote depth.  The soft-break space is retained
    unless the MIME part declares ``DelSp=yes``.  Space-stuffing is removed,
    and the exact ``-- `` signature delimiter is never treated as flowed so
    Phase 2's signature detection still finds it.
    """
    parsed = [_flowed_line_parts(line) for line in text.split("\n")]
    provenance = [source for source, _ in parsed]
    contents = [content for _, content in parsed]
    classes = _semantic_line_classes(contents, provenance, delsp=delsp)
    out: list[str] = []
    out_provenance: list[_LineProvenance] = []
    i = 0
    while i < len(contents):
        source = provenance[i]
        rendered_source = source
        content = contents[i]
        semantic_boundary = classes[i].boundary
        joined_parts = [content]
        while joined_parts[-1].endswith(" ") and i + 1 < len(contents):
            next_source = provenance[i + 1]
            next_content = contents[i + 1]
            if next_source.quote_depth != source.quote_depth:
                break
            next_boundary = classes[i + 1].boundary
            # A boundary span may join internally so it renders as the semantic
            # marker the downstream trimmer recognizes. Crossing into or out
            # of a boundary remains forbidden.
            if semantic_boundary is None:
                if next_boundary is not None:
                    break
            elif next_boundary != semantic_boundary:
                break
            if delsp:
                joined_parts[-1] = joined_parts[-1][:-1]
            joined_parts.append(next_content)
            if next_source.authored_literal_gt:
                rendered_source = _LineProvenance(
                    source.quote_depth,
                    rendered_source.space_stuffed,
                    True,
                )
            i += 1
        content = "".join(joined_parts)
        out.append(_render_flowed_line(source.quote_depth, content))
        out_provenance.append(rendered_source)
        i += 1
    rendered_provenance = tuple(out_provenance)
    rendered = _ProvenancedText("\n".join(out), rendered_provenance)
    return _ExtractedBody(rendered, rendered_provenance)


def _unwrap_flowed(text: str, *, delsp: bool = False) -> str:
    """Backward-compatible string-only wrapper around flowed extraction."""
    return _unwrap_flowed_details(text, delsp=delsp).text


def _extract_body_details(msg: Message) -> _ExtractedBody:
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
        if plain_flowed:
            return _unwrap_flowed_details(plain, delsp=plain_delsp)
        return _ExtractedBody(plain)
    if html is not None and html.strip():
        text, _ = ac.html_to_text(html, strip_selectors=_HTML_STRIP_SELECTORS)
        return _ExtractedBody(text)
    return _ExtractedBody("")


def extract_body(msg: Message) -> str:
    """Return the preferred body as text, preserving the public v1 API."""
    return _extract_body_details(msg).text


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
    kept_provenance: tuple[_LineProvenance, ...] | None = None


def _find_forward_marker(
    lines: list[str],
    provenance: tuple[_LineProvenance, ...] | None = None,
) -> int:
    classes = _rendered_semantic_lines(lines, provenance)
    return next(
        (
            i for i, semantic in enumerate(classes)
            if semantic.quote_depth == 0
            and semantic.boundary == "forward"
        ),
        -1,
    )


def _find_quote_boundary(
    lines: list[str],
    provenance: tuple[_LineProvenance, ...] | None = None,
) -> int:
    """Return the first line index that begins quoted content, or -1.
    Attribution lines are matched wrap-aware by back-scanning from a
    terminal `... wrote:` line up to a leading `On ` within a small
    window, so a wrapped `On <date>, <name> <addr>\\nwrote:` is trimmed
    whole (its address never survives)."""
    classes = _rendered_semantic_lines(lines, provenance)
    return next(
        (
            i for i, semantic in enumerate(classes)
            if semantic.quote_depth > 0
            or semantic.boundary in {"attribution", "original_message"}
        ),
        -1,
    )


def _has_weak_quote_signal(
    body: str,
    provenance: tuple[_LineProvenance, ...] | None = None,
) -> bool:
    classes = _rendered_semantic_lines(body.split("\n"), provenance)
    return any(
        semantic.quote_depth > 0
        or semantic.boundary in {
            "attribution",
            "attribution_terminal_unresolved",
            "original_message",
        }
        for semantic in classes
    )


# Zillow's share-by-email service appends this exact four-part signature:
# recognized listing label, Zillow homedetails URL, recognized app label, and
# the matching app-store URL. Requiring the whole ordered sequence prevents a
# lone listing URL (or authored prose containing one) from becoming a deletion
# boundary merely because it happens to be the final line.
_ZILLOW_SHARE_FOOTER_RE = re.compile(
    r"(?is)(?:^|\n)"
    r"(?:view this (?:home|listing) on zillow):[ \t]*\n"
    r"https?://(?:www\.)?zillow\.com/homedetails/\S+[ \t]*\n"
    r"(?:[ \t]*\n)?"
    r"(?:download the free zillow iphone app:[ \t]*\n"
    r"https?://(?:itunes\.apple\.com|apps\.apple\.com)/\S+"
    r"|download the free zillow android app:[ \t]*\n"
    r"https?://play\.google\.com/\S+)"
    r"[ \t\n]*\Z"
)


def _strip_promo_footer(text: str) -> str:
    match = _ZILLOW_SHARE_FOOTER_RE.search(text)
    return text[:match.start()].rstrip() if match else text


def _trim_signature(
    text: str,
    own_sig_lines: Optional[list[str]],
    provenance: tuple[_LineProvenance, ...] | None = None,
) -> str:
    text = _strip_promo_footer(text)
    provenance = _prefix_provenance(text, provenance)
    lines = text.split("\n")
    for i, semantic in enumerate(_rendered_semantic_lines(lines, provenance)):
        if semantic.boundary == "signature":
            return "\n".join(lines[:i]).rstrip()
    if own_sig_lines:
        joined = "\n".join(own_sig_lines).strip()
        idx = text.rfind(joined)
        if idx != -1 and text[idx:].strip() == joined:
            return text[:idx].rstrip()
    return text.rstrip()


def _prefix_provenance(
    text: str,
    provenance: tuple[_LineProvenance, ...] | None,
) -> tuple[_LineProvenance, ...] | None:
    if provenance is None or not text:
        return None
    return provenance[:len(text.split("\n"))]


def trim_body(
    body: str,
    *,
    is_reply: bool,
    own_sig_lines: Optional[list[str]],
    line_provenance: tuple[_LineProvenance, ...] | None = None,
) -> TrimResult:
    if line_provenance is None and isinstance(body, _ProvenancedText):
        line_provenance = body.line_provenance
    lines = body.split("\n")
    if line_provenance is not None and len(line_provenance) != len(lines):
        raise ValueError("line provenance must align with body lines")

    # Phase 1a: forwarded marker (checked first).
    fwd = _find_forward_marker(lines, line_provenance)
    if fwd != -1:
        lead = "\n".join(lines[:fwd]).rstrip()
        if not lead.strip():
            return TrimResult(kept="", dropped=True,
                              forwarded_no_comment=True,
                              drop_reason="forwarded_no_comment")
        composed = lead
        composed_provenance = _prefix_provenance(
            composed,
            line_provenance[:fwd] if line_provenance is not None else None,
        )
    else:
        boundary = _find_quote_boundary(lines, line_provenance)
        if boundary != -1:
            composed = "\n".join(lines[:boundary]).rstrip()
            composed_provenance = _prefix_provenance(
                composed,
                line_provenance[:boundary]
                if line_provenance is not None else None,
            )
        else:
            # No boundary located.
            if is_reply and _has_weak_quote_signal(body, line_provenance):
                return TrimResult(kept="", dropped=True,
                                  drop_reason="quote_boundary_unresolved")
            composed = body.rstrip()
            composed_provenance = _prefix_provenance(
                composed, line_provenance,
            )
            if is_reply:
                # confirmed reply, no quote-shaped content -> clean.
                res = TrimResult(kept="", kept_no_signal=True)
            else:
                res = TrimResult(kept="", kept_no_headers=True)
            res.kept = _trim_signature(
                composed, own_sig_lines, composed_provenance,
            )
            res.kept_provenance = _prefix_provenance(
                res.kept, composed_provenance,
            )
            return res

    # Phase 2: always-run signature trim on whatever Phase 1 kept.
    kept = _trim_signature(composed, own_sig_lines, composed_provenance)
    return TrimResult(
        kept=kept,
        kept_provenance=_prefix_provenance(kept, composed_provenance),
    )


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


def _redact_addresses(text: str, recipients: ac.StableRedactionMap) -> str:
    """Replace raw email-address tokens with stable recipient labels."""
    return _ADDR_TOKEN.sub(lambda m: recipients.display(m.group(0)), text)


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
RECEIPT_SCHEMA = "gmail_live_smoke_receipt_v3"
SMOKE_DESCRIPTOR_NAME = ".smoke_descriptor.json"
SMOKE_DESCRIPTOR_SCHEMA = "gmail_smoke_descriptor_v2"
THREAD_INDEX_NAME = "._thread_index.json"
RESUME_CHECKPOINT_SCHEMA = "gmail_resume_checkpoint_v4"
EXTRACTION_POLICY_VERSION = "gmail_extraction_policy_2026-07-18_v1"
_BEHAVIOR_FP_DOMAIN = b"gmail-behavior-fp-v3\x00"
_RESUME_FP_DOMAIN = b"gmail-resume-fp-v3\x00"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()



def _extraction_code_sha256() -> str:
    """Hash the extraction implementation that a human-reviewed smoke ran."""
    return _file_sha256(Path(__file__).resolve())

def _atomic_write_text(path: Path, text: str) -> None:
    """Publish text to ``path`` atomically (unique temp + fsync + replace).

    Used for the internal dotfile checkpoints (thread index, receipt, smoke
    descriptor) and for the manifest tail-repair rewrite.  Separate from
    ``acquisition_core._write_text_atomic`` (which backs the per-piece sidecar)
    so a kill-point test can monkeypatch the sidecar seam without also
    intercepting these control-plane writes.
    """
    import os
    import uuid

    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(tmp, "wb") as fh:
            fh.write(text.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _behavior_fingerprint_from_public(params: object) -> str | None:
    """Recompute the behavior fingerprint from the PUBLIC descriptor params.

    Single source of truth shared by the live fingerprint (over an ``Options``)
    and ``approve-smoke``'s verification of an untrusted, possibly hand-edited
    ``.smoke_descriptor.json``.  Returns ``None`` when ``params`` is missing or
    malformed, which the verifier treats as a fingerprint mismatch (fail-closed).

    The exact byte layout below MUST match what ``_behavior_params_public``
    records, so a descriptor's recorded ``behavior_fingerprint`` is reproducible
    from its recorded ``behavior_params`` alone — tampering with either without
    the other is detected.
    """
    if not isinstance(params, dict):
        return None
    try:
        own_address = params["own_address"]
        sent_label_token = params["sent_label_token"]
        min_words = params["min_words"]
        register = params["register"]
        extraction_policy = params["extraction_policy_version"]
        extraction_code_sha = params["extraction_code_sha256"]
    except (KeyError, TypeError):
        return None
    if not isinstance(own_address, list) or not all(
        isinstance(a, str) for a in own_address
    ):
        return None
    name_map_sha = params.get("name_map_sha256")
    own_sig_sha = params.get("own_sig_lines_sha256")
    h = hashlib.sha256()
    h.update(_BEHAVIOR_FP_DOMAIN)
    h.update("\n".join(sorted(own_address)).encode())
    h.update(b"\x00")
    h.update(str(sent_label_token).encode())
    h.update(b"\x00")
    h.update(str(min_words).encode())
    h.update(b"\x00")
    h.update(str(register).encode())
    h.update(b"\x00")
    h.update(b"author_consent")  # enforced in parse_options; bound for honesty
    h.update(b"\x00")
    h.update(str(extraction_policy).encode())
    h.update(b"\x00")
    h.update(str(extraction_code_sha).encode())
    h.update(b"\x00")
    if name_map_sha:
        h.update(str(name_map_sha).encode())
    h.update(b"\x00")
    if own_sig_sha:
        h.update(str(own_sig_sha).encode())
    return h.hexdigest()


def _behavior_fingerprint(opts: "Options") -> str:
    """Location-independent fingerprint of every selection/cleaning/redaction
    determinant, so a windowed smoke tree and the full-run output tree can
    differ in path yet share one fingerprint.

    INCLUDES: own address set, Sent-label token, min-words floor, register,
    consent status, the name-map contents, and the own-signature-lines
    contents.  EXCLUDES all location (output_dir, recipient_map_path,
    manifest_path) and all window/run params (since, until, max_items, persona,
    author, dry_run, allow_empty) — none of which changes which bytes an
    accepted-and-reviewed piece would carry.

    Derived from ``_behavior_params_public(opts)`` via
    ``_behavior_fingerprint_from_public`` so the fingerprint recorded in a smoke
    descriptor is always reproducible from that descriptor's public params.
    """
    fp = _behavior_fingerprint_from_public(_behavior_params_public(opts))
    assert fp is not None  # Options-derived public params are always well-formed
    return fp


def enforce_live_smoke_gate(
    opts: "Options", *, windowed: bool, mbox_sha: str | None = None,
) -> None:
    if opts.dry_run:
        return
    receipt = opts.output_dir / RECEIPT_NAME
    if not windowed:
        ok = False
        if receipt.exists():
            try:
                rec = json.loads(receipt.read_text(encoding="utf-8"))
                current_sha = mbox_sha or _file_sha256(opts.mbox_path)
                ok = (
                    rec.get("schema") == RECEIPT_SCHEMA
                    and rec.get("mbox_sha256") == current_sha
                    and rec.get("params") == _behavior_fingerprint(opts)
                    and rec.get("extraction_policy_version")
                    == EXTRACTION_POLICY_VERSION
                    and rec.get("extraction_code_sha256")
                    == _extraction_code_sha256()
                )
            except (OSError, json.JSONDecodeError):
                ok = False
        if not ok:
            raise SystemExit(
                f"{TOOL_NAME}: refusing an unwindowed (full-export) write "
                "with no valid live-smoke receipt for this mbox + filter "
                "parameters. Run a windowed smoke (subcommand `smoke`), review "
                "the closed smoke tree, then mint approval with `approve-smoke` "
                "(it validates the smoke tree and mints the receipt without "
                "acquiring or changing any records)."
            )


def _write_receipt(
    output_dir: Path, *, mbox_sha: str, params: str, window: str,
    **extra: object,
) -> None:
    """Mint the live-smoke receipt into ``output_dir`` (atomic write).

    Shared by the in-band windowed-acquire mint and the standalone
    ``approve-smoke`` mint, so both produce a single receipt shape that the
    unwindowed gate validates by ``schema`` + ``mbox_sha256`` + ``params``.
    """
    data: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "mbox_sha256": mbox_sha,
        "params": params,
        "window": window,
        "confirmed": True,
        "extraction_policy_version": EXTRACTION_POLICY_VERSION,
        "extraction_code_sha256": _extraction_code_sha256(),
    }
    data.update(extra)
    _atomic_write_text(
        output_dir / RECEIPT_NAME,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


# --------------- crash-safe resume: reconciliation + index --------
#
# The manifest row is the single per-piece commit marker.  Dedupe is
# manifest-authoritative (an in-memory set built from the committed manifest),
# NOT disk-presence-authoritative, so a piece whose .txt/.meta were written but
# whose manifest row was never appended (a crash inside emit_piece) is treated
# as UNcommitted: reconciliation deletes its residue and the piece is
# re-acquired identically on replay.  A full re-scan + idempotent emit makes the
# resumed output reproduce an uninterrupted run's content and manifest rows
# identically EXCEPT for the per-run provenance stamps that are re-generated on
# every run: the manifest ``acquired_via`` date and the sidecar ``acquired_at``
# timestamp.  Those (and only those) volatile fields differ on a replay; the
# content hashes, .txt bytes, recipient map, and every other row field match.


class ManifestIntegrityError(ValueError):
    """Committed manifest state is unsafe to resume or mutate."""


@dataclass(frozen=True)
class _ManifestInspection:
    rows: tuple[dict, ...]
    repair_bytes: bytes | None = None


def _inspect_manifest(manifest_path: Path) -> _ManifestInspection:
    """Parse committed rows and identify only an unterminated final tail.

    ``append_manifest_entry`` writes a complete JSON object and then its newline.
    Therefore every newline-terminated row is committed and must parse as an
    object; only a final row lacking its newline is repairable crash residue.
    """
    if not manifest_path.exists():
        return _ManifestInspection(())
    data = manifest_path.read_bytes()
    if not data:
        return _ManifestInspection(())
    raw_lines = data.splitlines(keepends=True)
    rows: list[dict] = []
    prefix_len = 0
    for index, raw in enumerate(raw_lines):
        final = index == len(raw_lines) - 1
        terminated = raw.endswith(b"\n")
        payload = raw.rstrip(b"\r\n").strip()
        if final and not terminated:
            return _ManifestInspection(tuple(rows), data[:prefix_len])
        if not payload:
            prefix_len += len(raw)
            continue
        try:
            row = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManifestIntegrityError(
                f"manifest line {index + 1} is malformed"
            ) from exc
        if not isinstance(row, dict):
            raise ManifestIntegrityError(
                f"manifest line {index + 1} is not an object"
            )
        rows.append(row)
        prefix_len += len(raw)
    return _ManifestInspection(tuple(rows))


def _validate_committed_rows(
    rows: tuple[dict, ...], *, manifest_path: Path, output_dir: Path,
) -> tuple[set[str], set[str], set[str]]:
    """Validate every committed text/sidecar/hash/locator before any mutation."""
    hashes: set[str] = set()
    ids: set[str] = set()
    entry_locators: set[str] = set()
    locator_re = re.compile(r"^sha256:[0-9a-f]{64}$")
    for line_number, row in enumerate(rows, start=1):
        rid = row.get("id")
        chash = row.get("content_hash")
        rel_path = row.get("path")
        if (
            not isinstance(rid, str) or not rid
            or Path(rid).name != rid or "/" in rid or "\\" in rid
        ):
            raise ManifestIntegrityError(
                f"manifest line {line_number} has an invalid id"
            )
        if rid in ids:
            raise ManifestIntegrityError(
                f"manifest line {line_number} repeats an id"
            )
        if not isinstance(chash, str) or not locator_re.fullmatch(chash):
            raise ManifestIntegrityError(
                f"manifest line {line_number} has an invalid content_hash"
            )
        if chash in hashes:
            raise ManifestIntegrityError(
                f"manifest line {line_number} repeats a content hash"
            )
        if not isinstance(rel_path, str) or not rel_path:
            raise ManifestIntegrityError(
                f"manifest line {line_number} has no text path"
            )
        recorded_path = Path(rel_path)
        if not recorded_path.is_absolute():
            recorded_path = manifest_path.parent / recorded_path
        expected_text = output_dir / f"{rid}.txt"
        if recorded_path.resolve() != expected_text.resolve():
            raise ManifestIntegrityError(
                f"manifest line {line_number} text path does not match its id"
            )
        meta_path = output_dir / f"{rid}.meta.json"
        try:
            stored_text = expected_text.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ManifestIntegrityError(
                f"committed text for manifest line {line_number} is missing or unreadable"
            ) from exc
        if ac.compute_content_hash(stored_text) != chash:
            raise ManifestIntegrityError(
                f"committed text for manifest line {line_number} content hash mismatches"
            )
        try:
            sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManifestIntegrityError(
                f"committed sidecar for manifest line {line_number} is missing or unreadable"
            ) from exc
        if not isinstance(sidecar, dict) or sidecar.get("content_hash") != chash:
            raise ManifestIntegrityError(
                f"committed sidecar for manifest line {line_number} content hash mismatches"
            )
        if not locator_re.fullmatch(
            str(sidecar.get("author_corpus_entry_locator", ""))
        ):
            raise ManifestIntegrityError(
                f"committed sidecar for manifest line {line_number} lacks a valid entry locator"
            )
        ids.add(rid)
        hashes.add(chash)
        entry_locators.add(sidecar["author_corpus_entry_locator"])
    return hashes, ids, entry_locators


def _validate_committed_source_bindings(
    rows: tuple[dict, ...],
    *,
    opts: "Options",
    recipients: ac.StableRedactionMap,
    box: mailbox.mbox,
    thread_roots: dict[str, str | None],
    target_keys: dict[str, object],
) -> None:
    """Authenticate committed provenance/content using targeted mbox keys only."""
    expected_acquired_via = f"{TOOL_NAME}_{opts.acquired_via_date.isoformat()}"
    for line_number, row in enumerate(rows, start=1):
        rid = row["id"]
        meta_path = opts.output_dir / f"{rid}.meta.json"
        sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
        if row.get("acquired_via") != expected_acquired_via:
            raise ManifestIntegrityError(
                f"manifest line {line_number} has false acquisition provenance"
            )
        if (
            sidecar.get("scraper") != expected_acquired_via
            or sidecar.get("scraper_version") != SCRAPER_VERSION
            or sidecar.get("source_url") != "gmail_takeout_local"
        ):
            raise ManifestIntegrityError(
                f"committed sidecar for line {line_number} has false scraper provenance"
            )
        entry_locator = sidecar["author_corpus_entry_locator"]
        source_key = target_keys.get(entry_locator)
        if source_key is None:
            raise ManifestIntegrityError(
                f"committed sidecar for line {line_number} is not in the source"
            )
        msg = box.get_message(source_key)
        before_recipients = recipients.entry_count()
        try:
            prepared = process_message(
                msg, opts, recipients, Summary(), thread_roots=thread_roots,
            )
        except ValueError as exc:
            raise ManifestIntegrityError(
                f"source message for line {line_number} cannot be revalidated"
            ) from exc
        if recipients.entry_count() != before_recipients:
            raise ManifestIntegrityError(
                "recipient map is incomplete for committed source rows"
            )
        if prepared is None or prepared.private_entry_locator != entry_locator:
            raise ManifestIntegrityError(
                f"committed locator for line {line_number} cannot be reproduced"
            )
        if prepared.piece.content_hash != row["content_hash"]:
            raise ManifestIntegrityError(
                f"manifest line {line_number} does not match its source message"
            )
        expected_row = ac.compose_manifest_entry(
            prepared.piece,
            text_path=opts.output_dir / f"{rid}.txt",
            manifest_relative_to=opts.manifest_path.parent,
            corpus_role="identity_baseline",
            use=["voice_profile"],
            ai_status=_ai_status_from_date(prepared.piece.date_written),
        )
        expected_row.setdefault("era", prepared.piece.era)
        expected_row.setdefault("consent_status", prepared.piece.consent_status)
        expected_row.setdefault("acquired_via", prepared.piece.acquired_via)
        if row != expected_row:
            raise ManifestIntegrityError(
                f"manifest line {line_number} has false deterministic metadata"
            )

        acquired_at = sidecar.get("acquired_at")
        try:
            acquired_at_parsed = _dt.datetime.fromisoformat(acquired_at)
        except (TypeError, ValueError) as exc:
            raise ManifestIntegrityError(
                f"committed sidecar for line {line_number} has invalid acquired_at"
            ) from exc
        if acquired_at_parsed.tzinfo is None:
            raise ManifestIntegrityError(
                f"committed sidecar for line {line_number} has naive acquired_at"
            )
        actual_sidecar = dict(sidecar)
        actual_sidecar.pop("acquired_at")
        piece = prepared.piece
        expected_sidecar = {
            "source_url": piece.source_url,
            "title": piece.title,
            "author": piece.author,
            "date_written": (
                piece.date_written.isoformat() if piece.date_written else None
            ),
            "raw_byte_length": piece.raw_byte_length,
            "content_hash": piece.content_hash,
            "word_count": piece.word_count,
            "scraper": piece.acquired_via,
            "scraper_version": SCRAPER_VERSION,
            "preprocessing": piece.preprocessing_meta,
        }
        expected_sidecar.update(_private_meta_fields(prepared))
        if actual_sidecar != expected_sidecar:
            raise ManifestIntegrityError(
                f"committed sidecar for line {line_number} has false deterministic metadata"
            )


def _reconcile_output(output_dir: Path, committed_ids: set[str]) -> dict[str, int]:
    """Delete crash residue: any .txt/.meta.json whose stem is not committed,
    and any stray ``*.tmp`` (an atomic-write temp orphaned by a kill).

    Returns COUNTS only (stems are subject-derived slugs — never emit them to a
    receipt/progress log; console-only if ever surfaced).  This is what makes
    ``_unique_stem`` deterministic on replay: without it a leftover orphan
    ``.txt`` would force a suffixed stem and the re-acquired piece would land
    under a different id than an uninterrupted run.
    """
    counts = {"orphan_txt": 0, "orphan_meta": 0, "stray_tmp": 0}
    if not output_dir.exists():
        return counts
    for meta_path in output_dir.glob("*.meta.json"):
        stem = meta_path.name[: -len(".meta.json")]
        if stem not in committed_ids:
            meta_path.unlink()
            counts["orphan_meta"] += 1
    for txt_path in output_dir.glob("*.txt"):
        if txt_path.stem not in committed_ids:
            txt_path.unlink()
            counts["orphan_txt"] += 1
    for tmp_path in output_dir.glob("*.tmp"):
        try:
            tmp_path.unlink()
            counts["stray_tmp"] += 1
        except OSError:
            pass
    return counts


def _roots_sha256(roots: dict[str, str | None]) -> str:
    payload = json.dumps(
        roots,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(b"gmail-thread-roots-v1\x00" + payload).hexdigest()


def _thread_index_load(output_dir: Path) -> dict | None:
    path = output_dir / THREAD_INDEX_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema") != RESUME_CHECKPOINT_SCHEMA
        or not isinstance(data.get("resume_fingerprint"), str)
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(data.get("thread_roots_sha256", ""))
        )
        or not isinstance(data.get("acquired_via_date"), str)
    ):
        return None
    try:
        _dt.date.fromisoformat(data["acquired_via_date"])
    except ValueError:
        return None
    return data


def _thread_index_save(
    output_dir: Path, mbox_sha: str, resume_fingerprint: str,
    roots: dict[str, str | None], acquired_via_date: _dt.date,
) -> None:
    _atomic_write_text(
        output_dir / THREAD_INDEX_NAME,
        json.dumps(
            {
                "schema": RESUME_CHECKPOINT_SCHEMA,
                "mbox_sha256": mbox_sha,
                "resume_fingerprint": resume_fingerprint,
                "thread_roots_sha256": _roots_sha256(roots),
                "acquired_via_date": acquired_via_date.isoformat(),
            },
            indent=2, sort_keys=True,
        ) + "\n",
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
    acquired_via_date: _dt.date


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
        # Signature-contamination visibility without echoing private prose.
        if self.trailing_counter:
            maximum = max(self.trailing_counter.values())
            level = "WARNING: " if maximum >= 10 else ""
            lines.append(
                f"{level}Trailing-text repetition audit: "
                f"{len(self.trailing_counter)} patterns tracked; "
                f"maximum frequency {maximum}."
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


def _add_acquire_args(
    parser: argparse.ArgumentParser, *, include_live_smoke: bool = True,
) -> None:
    """Attach the acquisition flags shared by the `acquire` and `smoke`
    subcommands.  ``smoke`` omits --live-smoke-confirmed (it can never mint an
    approval receipt) but is otherwise identical, so a reviewed smoke tree is
    produced by the exact pipeline the full run uses."""
    parser.add_argument("--mbox-path", required=True)
    parser.add_argument("--own-address", nargs="+", required=True)
    parser.add_argument("--persona", default="joshua")
    parser.add_argument("--author", default=None)
    parser.add_argument("--register", default="personal")
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument(
        "--min-words-per-piece", type=int, default=DEFAULT_MIN_WORDS,
    )
    parser.add_argument("--sent-label-token", default=DEFAULT_SENT_TOKEN)
    parser.add_argument("--recipient-map-path", default=None)
    parser.add_argument("--name-map", default=None)
    parser.add_argument("--own-signature-lines", default=None)
    parser.add_argument("--consent-status", default="author_consent")
    parser.add_argument("--max-items", type=int, default=10**9)
    ac.add_allow_empty_arg(parser)
    parser.add_argument("--dry-run", action="store_true")
    if include_live_smoke:
        parser.add_argument("--live-smoke-confirmed", action="store_true")
    else:
        parser.set_defaults(live_smoke_confirmed=False)
    parser.add_argument("--emit-manifest", default=None)
    parser.add_argument("--output-dir", default=None)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description=(
        "Acquire the user's own SENT Gmail prose from a Google Takeout "
        "mbox export as an identity-baseline corpus."))
    sub = p.add_subparsers(dest="command")

    ap = sub.add_parser(
        "acquire",
        help="Acquire sent prose (default; the legacy flat CLI routes here).",
    )
    _add_acquire_args(ap, include_live_smoke=True)
    ap.set_defaults(func=run)

    sp = sub.add_parser(
        "smoke",
        help="Windowed review slice into a dedicated tree; mints NO approval.",
    )
    _add_acquire_args(sp, include_live_smoke=False)
    sp.set_defaults(func=run_smoke)

    vp = sub.add_parser(
        "validate-smoke",
        help="Read-only validation of a smoke tree (writes nothing).",
    )
    vp.add_argument("--mbox-path", required=True)
    vp.add_argument("--smoke-dir", required=True)
    vp.set_defaults(func=run_validate_smoke)

    qp = sub.add_parser(
        "approve-smoke",
        help="TTY mint of a live-smoke receipt from a validated smoke tree; "
             "acquires no messages and reads no message content (it does hash "
             "the mbox file for a staleness check).",
    )
    qp.add_argument("--mbox-path", required=True)
    qp.add_argument("--smoke-dir", required=True)
    qp.add_argument("--output-dir", required=True)
    qp.set_defaults(func=run_approve_smoke)

    return p


def _parse_date(value: str | None) -> _dt.date | None:
    return _dt.date.fromisoformat(value) if value else None


def _acquisition_date_today() -> _dt.date:
    return _dt.date.today()


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
        acquired_via_date=_acquisition_date_today(),
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

    extracted = _extract_body_details(msg)
    body = extracted.text
    if not body.strip():
        summary.skipped_no_body += 1
        return None

    is_reply = bool(msg.get("In-Reply-To") or msg.get("References"))
    fwd_present = _find_forward_marker(
        body.split("\n"), extracted.line_provenance,
    ) != -1
    if is_reply or fwd_present:
        summary.reply_checked += 1

    trim = trim_body(
        body,
        is_reply=is_reply,
        own_sig_lines=opts.own_sig_lines,
        line_provenance=extracted.line_provenance,
    )
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
    for semantic in _rendered_semantic_lines(
        kept.split("\n"), trim.kept_provenance,
    ):
        if semantic.boundary == "attribution":
            summary.skipped_html_residual_attribution += 1
            return None

    cleaned, meta = ac.preprocess_text(kept)
    cleaned = cleaned.strip()
    cleaned = _redact_addresses(cleaned, recipients)
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

    title = _redact_addresses(_decode_header(msg.get("Subject")), recipients)
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
        acquired_via=f"{TOOL_NAME}_{opts.acquired_via_date.isoformat()}",
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


def _private_meta_fields(prepared: PreparedMessage) -> dict[str, str | None]:
    """The three private sidecar fields, in exactly the shape the prior
    two-step ``_augment_private_meta`` produced: the order timestamp is always
    present (possibly ``None``); each locator key is emitted only when non-None.
    Merged into the sidecar in ONE atomic write (no locator-less intermediate).
    """
    extra: dict[str, str | None] = {
        "author_corpus_order_timestamp": prepared.private_order_timestamp,
    }
    if prepared.private_thread_locator is not None:
        extra["author_corpus_thread_locator"] = prepared.private_thread_locator
    if prepared.private_entry_locator is not None:
        extra["author_corpus_entry_locator"] = prepared.private_entry_locator
    return extra


def emit_piece(
    prepared: PreparedMessage, opts: Options, summary: Summary,
    committed_hashes: set[str], committed_ids: set[str],
) -> None:
    """Publish one piece with the manifest row as the single commit marker.

    Durable-write order (each atomic / append-only): (1) ``.txt``, (2)
    ``.meta.json`` WITH private locators, (3) manifest row (fsync).  Dedupe is
    manifest-authoritative: a content hash already in ``committed_hashes`` is a
    real duplicate; an on-disk piece with no manifest row is NOT a duplicate (it
    was swept by reconciliation before this pass) and is re-emitted.
    """
    piece = prepared.piece
    if prepared.private_entry_locator is None:
        raise ValueError("message has no stable entry locator")

    if piece.content_hash in committed_hashes:
        summary.skipped_duplicate += 1
        return
    text_path, _ = ac.write_piece(
        piece, output_dir=opts.output_dir, scraper_version=SCRAPER_VERSION,
        extra_meta=_private_meta_fields(prepared),
    )
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
    ac.append_manifest_entry(opts.manifest_path, entry, fsync=True)
    committed_hashes.add(piece.content_hash)
    committed_ids.add(text_path.stem)
    summary.acquired += 1
    summary.total_cleaned_words += piece.word_count
    if ai_status == "unknown":
        summary.ai_status_unknown += 1
    if piece.date_written is None:
        summary.undated += 1


# --------------- run ----------------------------------------------


def run(args: argparse.Namespace, *, mode: str = "acquire") -> int:
    opts = parse_options(args)
    if not opts.mbox_path.exists():
        sys.stderr.write(f"{TOOL_NAME}: no such mbox: {opts.mbox_path}\n")
        return 1

    windowed = opts.since is not None or opts.until is not None
    if mode == "smoke" and not windowed:
        sys.stderr.write(
            f"{TOOL_NAME}: `smoke` requires a window (--since/--until); it is a "
            "bounded review slice, never a full export.\n")
        return 2
    if opts.live_smoke_confirmed:
        # The acquisition-time in-band approval path has been removed. Approval
        # must never be minted while acquiring/writing records; it may only
        # validate an already-closed smoke tree and mint a receipt without
        # touching any message. Hard-refuse and point at the separated flow.
        sys.stderr.write(
            f"{TOOL_NAME}: --live-smoke-confirmed (in-band acquisition-time "
            "approval) has been removed. Approval never happens during "
            "acquisition. Run `smoke` (windowed review slice), review the "
            "closed smoke tree, then `approve-smoke` (validates the tree and "
            "mints the receipt without acquiring or changing any records).\n")
        return 2

    mbox_sha: str | None = None
    if not opts.dry_run:
        ac.check_output_privacy(
            [opts.output_dir, opts.manifest_path, opts.recipient_map_path],
            allow_public=False, tool=TOOL_NAME,
        )
        # Hash the mbox ONCE per invocation and reuse it for the gate + the
        # resume checkpoints (do not re-hash 19.8 GB per resume).
        mbox_sha = _file_sha256(opts.mbox_path)
        enforce_live_smoke_gate(opts, windowed=windowed, mbox_sha=mbox_sha)

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

    # --- resume is fail-closed: inspect + source-bind + validate every
    # committed artifact before repairing a tail or deleting crash residue. ---
    committed_hashes: set[str] = set()
    committed_ids: set[str] = set()
    thread_roots: dict[str, str | None] | None = None
    resume_fp: str | None = None
    if not opts.dry_run:
        resume_fp = _resume_fingerprint(opts)
        try:
            inspection = _inspect_manifest(opts.manifest_path)
        except (OSError, ManifestIntegrityError) as exc:
            sys.stderr.write(f"{TOOL_NAME}: refusing unsafe manifest: {exc}\n")
            return 2
        index_data = _thread_index_load(opts.output_dir)
        index_valid = bool(
            index_data
            and index_data.get("mbox_sha256") == mbox_sha
            and index_data.get("resume_fingerprint") == resume_fp
        )
        if inspection.rows and not index_valid:
            raise SystemExit(
                f"{TOOL_NAME}: refusing to resume - committed manifest rows "
                "have no valid source-bound checkpoint for this mbox and exact "
                "selection/metadata parameters. Start a fresh output directory, "
                "or restore the original checkpoint and inputs."
            )
        try:
            (
                committed_hashes,
                committed_ids,
                target_locators,
            ) = _validate_committed_rows(
                inspection.rows,
                manifest_path=opts.manifest_path,
                output_dir=opts.output_dir,
            )
            target_keys: dict[str, object] = {}
            if index_valid and index_data is not None:
                source_roots, target_keys = build_thread_roots_and_target_keys(
                    box, target_locators,
                )
                if (
                    _roots_sha256(source_roots)
                    != index_data["thread_roots_sha256"]
                ):
                    if inspection.rows:
                        raise SystemExit(
                            f"{TOOL_NAME}: refusing to resume - checkpoint thread "
                            "roots do not match the source mbox."
                        )
                    index_valid = False
                else:
                    thread_roots = source_roots
                    opts.acquired_via_date = _dt.date.fromisoformat(
                        index_data["acquired_via_date"]
                    )
            if inspection.rows:
                assert thread_roots is not None
                _validate_committed_source_bindings(
                    inspection.rows,
                    opts=opts,
                    recipients=recipients,
                    box=box,
                    thread_roots=thread_roots,
                    target_keys=target_keys,
                )
        except (OSError, ManifestIntegrityError) as exc:
            sys.stderr.write(
                f"{TOOL_NAME}: refusing corrupt committed output: {exc}\n"
            )
            return 2
        if not index_valid and not inspection.rows:
            # An UNEVIDENCED directory (no committed manifest rows, no valid
            # resume checkpoint) that already holds piece-shaped files cannot
            # be told apart from someone else's corpus: every tree under the
            # private root passes the privacy gate, so sweeping here could
            # silently destroy a foreign corpus reached via a mistyped
            # --output-dir. Legitimate crash residue always lives in an
            # EVIDENCED tree - the thread-index checkpoint is persisted before
            # the first piece is ever written. Refuse loudly, before any
            # repair/reconcile mutation. Counts only: stems are
            # subject-derived and must not be echoed.
            stray_pieces = 0
            if opts.output_dir.exists():
                stray_pieces = sum(
                    1 for _ in opts.output_dir.glob("*.txt")
                ) + sum(
                    1 for _ in opts.output_dir.glob("*.meta.json")
                )
            if stray_pieces:
                sys.stderr.write(
                    f"{TOOL_NAME}: refusing to write into {opts.output_dir}: "
                    f"it already holds {stray_pieces} .txt/.meta.json piece "
                    "file(s) but no committed manifest rows and no valid "
                    "resume checkpoint for this mbox + parameters. Use a "
                    "fresh output directory; reconciliation never deletes "
                    "pieces it cannot prove are its own crash residue.\n"
                )
                return 2
        if inspection.repair_bytes is not None:
            _atomic_write_text(
                opts.manifest_path,
                inspection.repair_bytes.decode("utf-8"),
            )
        counts = _reconcile_output(opts.output_dir, committed_ids)
        if any(counts.values()):
            sys.stderr.write(
                f"{TOOL_NAME}: reconciled crash residue "
                f"(orphan_txt={counts['orphan_txt']} "
                f"orphan_meta={counts['orphan_meta']} "
                f"stray_tmp={counts['stray_tmp']}).\n"
            )

    if thread_roots is None:
        thread_roots = build_thread_roots(box)
        if not opts.dry_run and mbox_sha is not None and resume_fp is not None:
            _thread_index_save(
                opts.output_dir, mbox_sha, resume_fp, thread_roots, opts.acquired_via_date,
            )

    # --max-items caps the TOTAL committed rows, not the rows added this run.
    # On a resume, rows already committed to the manifest count toward the cap,
    # so a crash after k commits followed by an identical resume finishes at
    # exactly N total (not k + N). committed_ids is empty on a dry run, so the
    # dry-run cap degrades to a pure this-run count.
    already_committed = len(committed_ids)
    own_matches_total = 0
    saved_recipient_count = recipients.entry_count()
    for msg in box:
        if already_committed + summary.acquired >= opts.max_items:
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
                sys.stderr.write(
                    f"  dry-run eligible item {summary.acquired}\n"
                )
            continue
        # Durability ordering: any recipient label this piece introduced must be
        # on disk BEFORE the manifest row that references it commits. Persist the
        # recipient map (atomic + fsync + read-back) whenever it grew, so at any
        # kill point every committed row's recipient_NN labels are recoverable.
        if recipients.entry_count() > saved_recipient_count:
            recipients.save(fsync=True)
            saved_recipient_count = recipients.entry_count()
        emit_piece(prepared, opts, summary, committed_hashes, committed_ids)

    dedupe_only = summary.acquired == 0 and summary.skipped_duplicate > 0
    at_committed_cap = (
        bool(committed_ids) and len(committed_ids) >= opts.max_items
    )
    empty_is_error = (
        summary.acquired == 0
        and not dedupe_only
        and not opts.allow_empty
        and not at_committed_cap
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
        # A misconfigured zero-output run must not leave debris. The only file a
        # zero-output pass writes is the pass-1 thread index; drop it and remove
        # the output dir if it is now empty (never touch a non-empty dir).
        if not opts.dry_run:
            index_path = opts.output_dir / THREAD_INDEX_NAME
            try:
                if index_path.exists():
                    index_path.unlink()
                if opts.output_dir.exists():
                    opts.output_dir.rmdir()
            except OSError:
                pass

    if not opts.dry_run and not empty_is_error:
        # Durable clean-close save (redundant with the per-growth loop saves, but
        # covers a map loaded from a prior run that grew by nothing this pass).
        recipients.save(fsync=True)
        (opts.output_dir / "README.md").write_text(README_TEXT, encoding="utf-8")
        window = f"{opts.since}..{opts.until}"
        if mode == "smoke" and committed_ids and mbox_sha is not None:
            # `smoke` never mints approval; it records a descriptor that a
            # later `approve-smoke` (TTY) validates and binds a receipt to.
            write_smoke_descriptor(
                opts, mbox_sha=mbox_sha, window=window,
                acquired=len(committed_ids), manifest_rows=len(committed_ids),
            )
        # NOTE: approval is NEVER minted here. The acquisition-time in-band
        # --live-smoke-confirmed path was removed (it is hard-refused above);
        # a live-smoke receipt is minted only by `approve-smoke`, which validates
        # an already-closed smoke tree and acquires nothing.

    sys.stderr.write("\n" + summary.render(
        manifest_path=opts.manifest_path,
        recipient_map_path=opts.recipient_map_path,
    ))
    return 1 if empty_is_error else 0


# --------------- smoke descriptor + separated approval ------------


def _behavior_params_public(opts: "Options") -> dict[str, object]:
    """Behavior determinants recorded (in the clear) in the smoke descriptor so
    `approve-smoke` can reproduce the smoke-time fingerprint without re-reading
    the name-map / signature files.  ``own_address`` is the operator's OWN
    address (not correspondent PII) and the tree is private; name-map and
    signature inputs are recorded only as shas.
    """
    name_map_sha = None
    if opts.name_map_path and opts.name_map_path.exists():
        name_map_sha = _file_sha256(opts.name_map_path)
    own_sig_sha = None
    if opts.own_sig_lines:
        own_sig_sha = hashlib.sha256(
            "\n".join(opts.own_sig_lines).encode("utf-8")
        ).hexdigest()
    return {
        "own_address": sorted(opts.own_address),
        "sent_label_token": opts.sent_label_token,
        "min_words": opts.min_words,
        "name_map_sha256": name_map_sha,
        "own_sig_lines_sha256": own_sig_sha,
        "register": opts.register,
        "consent_status": "author_consent",
        "extraction_policy_version": EXTRACTION_POLICY_VERSION,
        "extraction_code_sha256": _extraction_code_sha256(),
    }


def _resume_params_public(opts: "Options") -> dict[str, object]:
    """All source-selection and emitted-metadata determinants for resume."""
    return {
        "behavior": _behavior_params_public(opts),
        "since": opts.since.isoformat() if opts.since else None,
        "until": opts.until.isoformat() if opts.until else None,
        "author": opts.author,
        "persona": opts.persona,
        "output_dir": str(opts.output_dir.resolve()),
        "manifest_path": str(opts.manifest_path.resolve()),
        "recipient_map_path": str(opts.recipient_map_path.resolve()),
        "scraper_version": SCRAPER_VERSION,
    }


def _resume_fingerprint(opts: "Options") -> str:
    payload = json.dumps(
        _resume_params_public(opts),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(_RESUME_FP_DOMAIN + payload).hexdigest()


def write_smoke_descriptor(
    opts: "Options", *, mbox_sha: str, window: str,
    acquired: int, manifest_rows: int,
) -> None:
    data = {
        "schema": SMOKE_DESCRIPTOR_SCHEMA,
        "mbox_sha256": mbox_sha,
        "behavior_fingerprint": _behavior_fingerprint(opts),
        "behavior_params": _behavior_params_public(opts),
        "window": window,
        "acquired": acquired,
        "manifest_rows": manifest_rows,
        "orphans": 0,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "tool_version": SCRAPER_VERSION,
    }
    _atomic_write_text(
        opts.output_dir / SMOKE_DESCRIPTOR_NAME,
        json.dumps(data, indent=2, sort_keys=True) + "\n",
    )


@dataclass
class SmokeValidation:
    ok: bool
    reason: str
    counts: dict[str, int] = field(default_factory=dict)
    descriptor: dict | None = None


def validate_smoke_tree(smoke_dir: Path, mbox_path: Path) -> SmokeValidation:
    """READ-ONLY validation of a smoke tree; writes nothing.

    Hard checks (fail-closed): descriptor present + schema; mbox unchanged since
    smoke (staleness); the tree is CLOSED one-to-one (every sidecar stem is a
    manifest id and every manifest id has a .txt + .meta.json — folds the
    orphan/dangling reconciliation into the gate); manifest_rows > 0; the
    recipient map is present and parses; no raw recipient-map key leaks into any
    manifest row or sidecar.
    """
    descriptor_path = smoke_dir / SMOKE_DESCRIPTOR_NAME
    if not descriptor_path.exists():
        return SmokeValidation(False, "no_descriptor")
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SmokeValidation(False, "descriptor_unreadable")
    if not isinstance(descriptor, dict) or (
        descriptor.get("schema") != SMOKE_DESCRIPTOR_SCHEMA
    ):
        return SmokeValidation(False, "descriptor_schema")
    behavior_params = descriptor.get("behavior_params")
    if (
        not isinstance(behavior_params, dict)
        or behavior_params.get("extraction_policy_version")
        != EXTRACTION_POLICY_VERSION
        or behavior_params.get("extraction_code_sha256")
        != _extraction_code_sha256()
    ):
        return SmokeValidation(False, "stale_extraction_policy", descriptor=descriptor)
    if not mbox_path.exists():
        return SmokeValidation(False, "mbox_missing", descriptor=descriptor)
    if _file_sha256(mbox_path) != descriptor.get("mbox_sha256"):
        return SmokeValidation(False, "stale_mbox", descriptor=descriptor)

    manifest_path = smoke_dir / "draft_manifest.jsonl"
    if not manifest_path.exists():
        return SmokeValidation(False, "no_manifest", descriptor=descriptor)
    try:
        inspection = _inspect_manifest(manifest_path)
    except (OSError, ManifestIntegrityError):
        return SmokeValidation(False, "manifest_unreadable", descriptor=descriptor)
    if inspection.repair_bytes is not None:
        return SmokeValidation(False, "manifest_unreadable", descriptor=descriptor)
    if not inspection.rows:
        return SmokeValidation(False, "empty_tree", descriptor=descriptor)
    try:
        _, manifest_ids, _ = _validate_committed_rows(
            inspection.rows,
            manifest_path=manifest_path,
            output_dir=smoke_dir,
        )
    except ManifestIntegrityError:
        return SmokeValidation(
            False, "committed_artifact_invalid", descriptor=descriptor,
        )

    meta_stems = {
        p.name[: -len(".meta.json")] for p in smoke_dir.glob("*.meta.json")
    }
    txt_stems = {p.stem for p in smoke_dir.glob("*.txt")}
    orphan_meta = meta_stems - manifest_ids
    dangling_rows = manifest_ids - meta_stems
    missing_txt = manifest_ids - txt_stems
    counts = {
        "manifest_rows": len(manifest_ids),
        "orphan_sidecars": len(orphan_meta),
        "dangling_manifest_rows": len(dangling_rows),
        "manifest_rows_missing_txt": len(missing_txt),
    }
    if orphan_meta or dangling_rows or missing_txt:
        return SmokeValidation(
            False, "not_closed", counts=counts, descriptor=descriptor,
        )

    # Descriptor self-consistency — reject a hand-edited .smoke_descriptor.json.
    # Recompute the row/acquired counts from the ACTUAL tree and the fingerprint
    # from the recorded PUBLIC params; every recorded value must match what is
    # recomputed, so tampering with manifest_rows, acquired, behavior_params, or
    # behavior_fingerprint (without also re-acquiring the tree) fails closed here,
    # before approve-smoke ever prompts or mints.
    actual_rows = len(manifest_ids)
    if descriptor.get("manifest_rows") != actual_rows:
        return SmokeValidation(
            False, "descriptor_manifest_rows_mismatch", counts=counts,
            descriptor=descriptor,
        )
    if descriptor.get("acquired") != actual_rows:
        return SmokeValidation(
            False, "descriptor_acquired_mismatch", counts=counts,
            descriptor=descriptor,
        )
    recomputed_fp = _behavior_fingerprint_from_public(
        descriptor.get("behavior_params")
    )
    if recomputed_fp is None or recomputed_fp != descriptor.get(
        "behavior_fingerprint"
    ):
        return SmokeValidation(
            False, "descriptor_fingerprint_mismatch", counts=counts,
            descriptor=descriptor,
        )

    recipient_map_path = smoke_dir / "recipient_map.json"
    if not recipient_map_path.exists():
        return SmokeValidation(
            False, "no_recipient_map", counts=counts, descriptor=descriptor,
        )
    try:
        recipient_map = json.loads(
            recipient_map_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return SmokeValidation(
            False, "recipient_map_unreadable", counts=counts,
            descriptor=descriptor,
        )
    # Privacy: no raw recipient-map key may appear in manifest/sidecar text.
    if isinstance(recipient_map, dict) and recipient_map:
        blob = manifest_path.read_text(encoding="utf-8")
        for p in smoke_dir.glob("*.meta.json"):
            blob += p.read_text(encoding="utf-8")
        for raw_key in recipient_map:
            if isinstance(raw_key, str) and raw_key and raw_key in blob:
                return SmokeValidation(
                    False, "raw_recipient_leak", counts=counts,
                    descriptor=descriptor,
                )
    return SmokeValidation(True, "closed", counts=counts, descriptor=descriptor)


def run_smoke(args: argparse.Namespace) -> int:
    return run(args, mode="smoke")


def run_validate_smoke(args: argparse.Namespace) -> int:
    smoke_dir = Path(args.smoke_dir).expanduser()
    mbox_path = Path(args.mbox_path).expanduser()
    result = validate_smoke_tree(smoke_dir, mbox_path)
    sys.stderr.write(json.dumps({
        "smoke_validate": result.reason,
        "ok": result.ok,
        "counts": result.counts,
        "smoke_dir": str(smoke_dir),
    }, sort_keys=True) + "\n")
    return 0 if result.ok else 2


def run_approve_smoke(args: argparse.Namespace) -> int:
    smoke_dir = Path(args.smoke_dir).expanduser()
    mbox_path = Path(args.mbox_path).expanduser()
    output_dir = Path(args.output_dir).expanduser()

    result = validate_smoke_tree(smoke_dir, mbox_path)
    if not result.ok or result.descriptor is None:
        sys.stderr.write(json.dumps({
            "approve_smoke": "refused",
            "reason": result.reason,
            "counts": result.counts,
        }, sort_keys=True) + "\n")
        return 2
    if not sys.stdin.isatty():
        sys.stderr.write(
            f"{TOOL_NAME}: approve-smoke requires an interactive terminal (it "
            "attests a human reviewed real recipient data); stdin is not a "
            "TTY.\n")
        return 2
    # approve-smoke acquires no messages and reads no message content — it can
    # only mint a receipt into an approved private output directory. (It DOES
    # hash the mbox file, in validate_smoke_tree above, for the staleness check;
    # that is a whole-file digest, not a read of any message body.)
    ac.check_output_privacy(
        [output_dir], allow_public=False, tool=f"{TOOL_NAME} approve-smoke",
    )
    descriptor = result.descriptor
    fp = descriptor["behavior_fingerprint"]
    mbox_sha = descriptor["mbox_sha256"]
    window = descriptor.get("window", "")
    sys.stderr.write(
        f"approve-smoke: smoke_dir={smoke_dir} acquired={descriptor.get('acquired')} "
        f"window={window} fp={fp[:12]} -> output_dir={output_dir}\n"
        "Mint live-smoke approval receipt? [y/N] "
    )
    try:
        answer = input().strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        sys.stderr.write("approve-smoke: aborted (no receipt written).\n")
        return 1
    descriptor_sha = _file_sha256(smoke_dir / SMOKE_DESCRIPTOR_NAME)
    _write_receipt(
        output_dir,
        mbox_sha=mbox_sha,
        params=fp,
        window=window,
        smoke_tree=str(smoke_dir),
        smoke_descriptor_sha256=descriptor_sha,
        smoke_acquired=descriptor.get("acquired"),
        approved_via_tty=True,
    )
    sys.stderr.write(json.dumps({
        "approve_smoke": "minted",
        "output_dir": str(output_dir),
        "params": fp,
    }, sort_keys=True) + "\n")
    return 0


# --------------- CLI dispatch -------------------------------------


_KNOWN_SUBCOMMANDS = {"acquire", "smoke", "validate-smoke", "approve-smoke"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backward-compat shim: the legacy flat CLI (and all existing callers /
    # runbook invocations) begin with a flag, not a verb. Route those to
    # `acquire` so `G.main(["--mbox-path", ...])` behaves exactly as before.
    if argv and argv[0] in ("-h", "--help"):
        pass
    elif not argv or argv[0] not in _KNOWN_SUBCOMMANDS:
        argv = ["acquire"] + argv
    args = build_arg_parser().parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        build_arg_parser().print_help()
        return 0
    return func(args)


if __name__ == "__main__":
    sys.exit(main())
