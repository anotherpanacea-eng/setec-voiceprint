#!/usr/bin/env python3
"""Closed, stdlib-only production gate for annotated text mirrors (v3).

This is deliberately an internal packet helper.  Its JSON result is aggregate
only: callers must not use it to print source material or sidecar metadata.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

MAX_BYTES = 1_048_576
MAX_TOKENS = 20_000
MAX_QUOTE_SPANS = 4096
REGISTERS = ("unknown", "published", "informal")
SCOPE = "structural_only_no_arbitrary_bare_completeness"


class MirrorGateError(Exception):
    def __init__(self, code: str):
        self.code = code


@dataclass(frozen=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True)
class Region:
    spans: tuple[Span, ...]
    extent: Span
    kind: str = "annotation"

    def payload(self, raw: bytes) -> bytes:
        return b"".join(raw[s.start:s.end] for s in self.spans)


def _similarity(source: str, mirror: str) -> float:
    """Test seam: ceilings must reject before this legacy operation runs."""
    return difflib.SequenceMatcher(None, source, mirror).ratio()


def _write_error(code: str) -> None:
    sys.stderr.buffer.write(("mirror_gate_error:" + code + "\n").encode("ascii"))


def _read_input(path: str) -> bytes:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise MirrorGateError("input_unreadable")
    if len(raw) > MAX_BYTES:
        raise MirrorGateError("input_too_large")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise MirrorGateError("input_invalid_utf8")
    if len(text.split()) > MAX_TOKENS:
        raise MirrorGateError("input_token_limit")
    return raw


def _read_sidecar(path: str, source: bytes) -> tuple[bool, list[Region]]:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise MirrorGateError("sidecar_unreadable")
    if len(raw) > MAX_BYTES:
        raise MirrorGateError("sidecar_too_large")
    try:
        decoded = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise MirrorGateError("sidecar_invalid_utf8")
    try:
        value = json.loads(decoded)
    except (ValueError, TypeError):
        raise MirrorGateError("sidecar_invalid_json")
    return _validate_sidecar(value, source)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _utf8_boundaries(raw: bytes) -> set[int]:
    text = raw.decode("utf-8", "strict")
    out, pos = {0}, 0
    for ch in text:
        pos += len(ch.encode("utf-8"))
        out.add(pos)
    return out


def _validate_sidecar(value: object, source: bytes) -> tuple[bool, list[Region]]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "source_sha256", "complete", "regions"}:
        raise MirrorGateError("sidecar_invalid_schema")
    if value["schema_version"] != "setec-mirror-quote-regions/1" or not isinstance(value["complete"], bool):
        raise MirrorGateError("sidecar_invalid_schema")
    digest = value["source_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise MirrorGateError("sidecar_invalid_schema")
    if digest != hashlib.sha256(source).hexdigest():
        raise MirrorGateError("sidecar_stale")
    rows = value["regions"]
    if not isinstance(rows, list):
        raise MirrorGateError("sidecar_invalid_schema")
    boundaries = _utf8_boundaries(source)
    result: list[Region] = []
    previous_end = -1
    total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"spans"} or not isinstance(row["spans"], list) or not row["spans"]:
            raise MirrorGateError("sidecar_invalid_schema")
        spans: list[Span] = []
        for item in row["spans"]:
            if not isinstance(item, dict) or set(item) != {"start_byte", "end_byte"}:
                raise MirrorGateError("sidecar_invalid_schema")
            start, end = item["start_byte"], item["end_byte"]
            if not _is_int(start) or not _is_int(end) or not 0 <= start < end <= len(source):
                raise MirrorGateError("sidecar_invalid_schema")
            if start not in boundaries or end not in boundaries or (spans and start < spans[-1].end):
                raise MirrorGateError("sidecar_invalid_schema")
            spans.append(Span(start, end))
            total += 1
            if total > MAX_QUOTE_SPANS:
                raise MirrorGateError("sidecar_span_limit")
        if spans[0].start < previous_end:
            raise MirrorGateError("sidecar_invalid_schema")
        previous_end = spans[-1].end
        result.append(Region(tuple(spans), Span(spans[0].start, spans[-1].end)))
    return value["complete"], result


def _char_offsets(text: str) -> list[int]:
    offsets, pos = [0], 0
    for char in text:
        pos += len(char.encode("utf-8"))
        offsets.append(pos)
    return offsets


@dataclass(frozen=True)
class _Line:
    start: int
    content_end: int
    end: int
    text: str
    terminator: bytes


def _lines(raw: bytes) -> list[_Line]:
    text = raw.decode("utf-8", "strict")
    offsets = _char_offsets(text)
    ans: list[_Line] = []
    i = 0
    while i < len(text):
        start = i
        while i < len(text) and text[i] not in "\r\n":
            i += 1
        content_end = i
        if i < len(text):
            if text[i] == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
                i += 2
            else:
                i += 1
        ans.append(_Line(offsets[start], offsets[content_end], offsets[i], text[start:content_end], raw[offsets[content_end]:offsets[i]]))
    if not ans or ans[-1].end != len(raw):
        ans.append(_Line(len(raw), len(raw), len(raw), "", b""))
    return ans


def _markdown_regions(raw: bytes) -> list[Region]:
    out: list[Region] = []
    lines = _lines(raw)
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^( {0,3})>", line.text)
        if not m:
            i += 1
            continue
        group, spans = [line], []
        while i < len(lines):
            cur = lines[i]
            match = re.match(r"^( {0,3})>", cur.text)
            if not match or not cur.text.strip():
                break
            # Marker coordinates are ASCII, so character and byte lengths agree here.
            prefix = len(match.group(0))
            if cur.text[prefix:prefix + 1] == " ":
                prefix += 1
            start = cur.start + prefix
            if start < cur.end:
                spans.append(Span(start, cur.end))
            group.append(cur)
            i += 1
        extent = Span(group[0].start, group[-1].end)
        region = Region(tuple(spans), extent, "markdown")
        if region.payload(raw):
            out.append(region)
    return out


def _colon_regions(raw: bytes) -> list[Region]:
    return _colon_regions_after(raw, ())


def _colon_candidate_end(
    first: int,
    tab_style: bool,
    tab_run_ends: Sequence[int],
    next_lower: Sequence[int],
) -> int:
    """Constant-time candidate end seam used by complexity regressions."""
    return tab_run_ends[first] if tab_style else next_lower[first]


def _colon_regions_after(raw: bytes, blocked: Sequence[Region]) -> list[Region]:
    lines, out = _lines(raw), []
    count = len(lines)
    leading_spaces: list[int] = []
    tab_member: list[bool] = []
    for line in lines:
        nonblank = bool(line.text.strip())
        spaces = len(line.text) - len(line.text.lstrip(" ")) if nonblank else 0
        leading_spaces.append(spaces)
        tab_member.append(nonblank and line.text.startswith("\t"))
    tab_run_ends = [0] * count
    for i in range(count - 1, -1, -1):
        tab_run_ends[i] = tab_run_ends[i + 1] if tab_member[i] and i + 1 < count and tab_member[i + 1] else (i + 1)
    next_lower = [count] * count
    stack: list[int] = []
    for i in range(count - 1, -1, -1):
        while stack and leading_spaces[stack[-1]] >= leading_spaces[i]:
            stack.pop()
        next_lower[i] = stack[-1] if stack else count
        stack.append(i)
    blocked_starts = [region.extent.start for region in blocked]
    last_accepted_end = -1
    for i, intro in enumerate(lines[:-1]):
        if not intro.text or not intro.text.rstrip(" \t").endswith(":"):
            continue
        first = lines[i + 1]
        tab_style = tab_member[i + 1]
        if tab_style:
            remove = 1
        else:
            n = leading_spaces[i + 1]
            if n < 2:
                continue
            remove = n
        end = _colon_candidate_end(i + 1, tab_style, tab_run_ends, next_lower)
        extent = Span(first.start, lines[end - 1].end)
        if extent.start < last_accepted_end or _overlaps_sorted(extent, blocked, blocked_starts):
            continue
        spans = tuple(Span(lines[j].start + remove, lines[j].end) for j in range(i + 1, end))
        region = Region(spans, extent, "colon")
        if region.payload(raw):
            out.append(region)
            last_accepted_end = extent.end
    return out


def _next_inline_closer(positions: Sequence[int], opener: int) -> int | None:
    """Indexed closer lookup seam used by complexity regressions."""
    at = bisect.bisect_right(positions, opener)
    return positions[at] if at < len(positions) else None


def _inline_regions(raw: bytes) -> list[Region]:
    text = raw.decode("utf-8", "strict")
    offsets = _char_offsets(text)
    out: list[Region] = []
    # Atomic line-break groups: a single CRLF is one line break, not two, so it
    # must not be split into `\r`+`\n` and mistaken for a blank-line boundary.
    boundary = re.compile(r"(?>\r\n|\r|\n)[^\S\r\n]*(?>\r\n|\r|\n)")
    paragraphs: list[tuple[int, int]] = []
    start = 0
    for match in boundary.finditer(text):
        paragraphs.append((start, match.start()))
        start = match.end()
    paragraphs.append((start, len(text)))
    for paragraph_start, paragraph_end in paragraphs:
        straight: list[int] = []
        curly_double: list[int] = []
        curly_single: list[int] = []
        for pos in range(paragraph_start, paragraph_end):
            char = text[pos]
            if char == '"':
                straight.append(pos)
            elif char == "”":
                curly_double.append(pos)
            elif char == "’" and not (
                pos > 0 and pos + 1 < len(text)
                and (text[pos - 1].isalnum() or text[pos - 1] == "_")
                and (text[pos + 1].isalnum() or text[pos + 1] == "_")
            ):
                curly_single.append(pos)
        closers = {'"': straight, '“': curly_double, '‘': curly_single}
        i = paragraph_start
        while i < paragraph_end:
            char = text[i]
            positions = closers.get(char)
            if positions is None:
                i += 1
                continue
            closer = _next_inline_closer(positions, i)
            if closer is None:
                i += 1
                continue
            span = Span(offsets[i + 1], offsets[closer])
            region = Region((span,) if span.start < span.end else (), Span(offsets[i], offsets[closer + 1]), "inline")
            if region.spans and region.payload(raw):
                out.append(region)
            i = closer + 1
    return out


def _overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def _overlaps_sorted(extent: Span, regions: Sequence[Region], starts: Sequence[int]) -> bool:
    """O(log n) overlap query against sorted, nonoverlapping regions."""
    at = bisect.bisect_right(starts, extent.start) - 1
    return (
        (at >= 0 and _overlap(extent, regions[at].extent))
        or (at + 1 < len(regions) and _overlap(extent, regions[at + 1].extent))
    )


def _merge_regions(left: Sequence[Region], right: Sequence[Region]) -> list[Region]:
    merged: list[Region] = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i].extent.start <= right[j].extent.start:
            merged.append(left[i]); i += 1
        else:
            merged.append(right[j]); j += 1
    merged.extend(left[i:]); merged.extend(right[j:])
    return merged


def _accept_priority_class(candidates: Sequence[Region], higher: Sequence[Region]) -> list[Region]:
    ordered = sorted(candidates, key=lambda r: (r.extent.start, -(r.extent.end - r.extent.start)))
    higher_starts = [region.extent.start for region in higher]
    accepted: list[Region] = []
    last_end = -1
    for region in ordered:
        if region.extent.start < last_end:
            continue
        if _overlaps_sorted(region.extent, higher, higher_starts):
            continue
        accepted.append(region)
        last_end = region.extent.end
    return accepted


def _automatic_regions(raw: bytes) -> list[Region]:
    markdown = _accept_priority_class(_markdown_regions(raw), ())
    colon = _colon_regions_after(raw, markdown)
    higher = _merge_regions(markdown, colon)
    inline = _accept_priority_class(_inline_regions(raw), higher)
    return _merge_regions(higher, inline)


def _same_spans(left: Region, right: Region) -> bool:
    return left.spans == right.spans


def _reconcile(source: bytes, automatic: list[Region], annotations: list[Region], complete: bool) -> tuple[list[Region], bool]:
    confirmed: set[int] = set()
    bare: list[Region] = []
    auto_at = 0
    for ann in annotations:
        while auto_at < len(automatic) and automatic[auto_at].extent.end <= ann.extent.start:
            auto_at += 1
        hits: list[int] = []
        probe = auto_at
        while probe < len(automatic) and automatic[probe].extent.start < ann.extent.end:
            if _overlap(ann.extent, automatic[probe].extent):
                hits.append(probe)
            probe += 1
        if hits:
            if len(hits) != 1 or hits[0] in confirmed or not _same_spans(ann, automatic[hits[0]]):
                raise MirrorGateError("sidecar_invalid_schema")
            confirmed.add(hits[0])
        else:
            if len(ann.spans) != 1:
                raise MirrorGateError("sidecar_invalid_schema")
            bare.append(ann)
    merged = _merge_regions(automatic, bare)
    return merged, complete and len(confirmed) == len(automatic)


def _mask(spans: Iterable[Span], length: int) -> bytearray:
    mask = bytearray(length)
    for span in spans:
        mask[span.start:span.end] = b"\x01" * (span.end - span.start)
    return mask


def _first_raw_match(
    raw: bytes,
    payload: bytes,
    forbidden: Sequence[Region],
    forbidden_starts: Sequence[int],
    after: int,
    stop_at: int | None = None,
) -> Region | None:
    start = after
    while True:
        pos = raw.find(payload, start)
        if pos < 0:
            return None
        if stop_at is not None and pos >= stop_at:
            return None
        span = Span(pos, pos + len(payload))
        at = bisect.bisect_right(forbidden_starts, span.start) - 1
        overlapping: Region | None = None
        if at >= 0 and _overlap(span, forbidden[at].extent):
            overlapping = forbidden[at]
        elif at + 1 < len(forbidden) and _overlap(span, forbidden[at + 1].extent):
            overlapping = forbidden[at + 1]
        if overlapping is None:
            return Region((span,), span, "raw")
        # The whole structural extent is forbidden, so probing its remaining
        # bytes cannot find a valid raw candidate.
        start = max(pos + 1, overlapping.extent.end)


def _quote_fidelity(source: bytes, mirror: bytes, source_regions: list[Region], mirror_auto: list[Region], complete_required: bool, complete_verified: bool) -> tuple[dict[str, object], bytearray, bytearray]:
    used_until = 0
    consumed_auto: set[int] = set()
    selected_spans: list[Span] = []
    preserved = 0
    fidelity_ok = True
    auto_by_payload: dict[bytes, list[tuple[int, int, int, Region]]] = {}
    for index, region in enumerate(mirror_auto):
        auto_by_payload.setdefault(region.payload(mirror), []).append(
            (region.extent.start, region.extent.end - region.extent.start, index, region)
        )
    auto_starts = {
        payload: [item[0] for item in items]
        for payload, items in auto_by_payload.items()
    }
    forbidden_starts = [region.extent.start for region in mirror_auto]
    raw_cache: dict[tuple[bytes, int, int | None], Region | None] = {}
    for src in source_regions:
        payload = src.payload(source)
        choices: list[tuple[int, int, Region, int | None]] = []
        same_payload = auto_by_payload.get(payload, [])
        auto_at = bisect.bisect_left(auto_starts.get(payload, []), used_until)
        if auto_at < len(same_payload):
            start, length, auto_i, candidate = same_payload[auto_at]
            choices.append((start, length, candidate, auto_i))
        raw_key = (payload, used_until, choices[0][0] if choices else None)
        if raw_key not in raw_cache:
            raw_cache[raw_key] = _first_raw_match(
                mirror,
                payload,
                mirror_auto,
                forbidden_starts,
                used_until,
                raw_key[2],
            )
        raw_candidate = raw_cache[raw_key]
        if raw_candidate is not None:
            choices.append((raw_candidate.extent.start, raw_candidate.extent.end - raw_candidate.extent.start, raw_candidate, None))
        if not choices:
            fidelity_ok = False
            continue
        _, _, chosen, auto_i = min(choices, key=lambda item: (item[0], item[1]))
        used_until = chosen.extent.end
        selected_spans.extend(chosen.spans)
        preserved += 1
        if auto_i is not None:
            consumed_auto.add(auto_i)
    added = any(i not in consumed_auto for i in range(len(mirror_auto)))
    source_mask = _mask((span for region in source_regions for span in region.spans), len(source))
    mirror_mask = _mask(selected_spans + [span for r in mirror_auto for span in r.spans], len(mirror))
    if complete_required and not complete_verified:
        reason = "complete_annotation_required"
    elif not complete_verified and complete_required is False:  # unknown remains a normal auto-only evaluation
        reason = "quote_fidelity_failed" if not fidelity_ok else ("added_mirror_quote_region" if added else "ok")
    elif not complete_verified:
        reason = "complete_annotation_omits_detected_source_quote"
    elif not fidelity_ok:
        reason = "quote_fidelity_failed"
    elif added:
        reason = "added_mirror_quote_region"
    else:
        reason = "ok"
    # A complete sidecar that merely omits an automatic quote is distinct from no sidecar.
    if complete_required and not complete_verified:
        reason = "complete_annotation_required"
    result = {"annotation_complete": complete_verified, "source_regions": len(source_regions), "preserved_regions": preserved, "ok": reason == "ok", "reason": reason, "automatic_scope": SCOPE}
    return result, source_mask, mirror_mask


_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_'’\-])(?:[A-Z][A-Za-z0-9]*[0-9][A-Za-z0-9]*|[A-Z]{2,}|[A-Z][A-Za-z]*[A-Z][A-Za-z]*|[A-Z][A-Za-z]*(?:['’\-][A-Za-z]+)+|[A-Z][a-z]{2,})(?![A-Za-z0-9_'’\-])")
_CONNECTIVES = {"Another", "But", "However", "Now", "So", "Take", "Then", "Therefore", "Thus", "Yet"}


def _initial(text: str, start: int) -> bool:
    prefix = text[:start]
    if not prefix.strip():
        return True
    line = prefix.rsplit("\n", 1)[-1]
    if not line.strip():
        return True
    return prefix.rstrip().endswith((".", "!", "?"))


def _initial_flags(text: str, matches: Sequence[re.Match[str]]) -> dict[int, bool]:
    """Classify every candidate start in one left-to-right text scan."""
    flags: dict[int, bool] = {}
    match_at = 0
    seen_nonwhitespace = False
    line_has_nonwhitespace = False
    last_nonwhitespace = ""
    for pos, char in enumerate(text):
        while match_at < len(matches) and matches[match_at].start() == pos:
            flags[pos] = (
                not seen_nonwhitespace
                or not line_has_nonwhitespace
                or last_nonwhitespace in ".!?"
            )
            match_at += 1
        if char == "\n":
            line_has_nonwhitespace = False
        elif not char.isspace():
            seen_nonwhitespace = True
            line_has_nonwhitespace = True
            last_nonwhitespace = char
    return flags


def _phrases(text: str, suppress_initial_connectives: bool = True) -> list[tuple[str, bool, bool]]:
    matches = list(_TOKEN_RE.finditer(text))
    initial = _initial_flags(text, matches)
    groups: list[list[re.Match[str]]] = []
    for match in matches:
        if suppress_initial_connectives and match.group() in _CONNECTIVES and initial[match.start()]:
            continue
        if groups and re.fullmatch(r"[ \t]+", text[groups[-1][-1].end():match.start()]):
            groups[-1].append(match)
        else:
            groups.append([match])
    token_occurs: dict[str, int] = {}
    token_noninitial: dict[str, int] = {}
    for m in matches:
        if suppress_initial_connectives and m.group() in _CONNECTIVES and initial[m.start()]:
            continue
        token_occurs[m.group()] = token_occurs.get(m.group(), 0) + 1
        if not initial[m.start()]:
            token_noninitial[m.group()] = token_noninitial.get(m.group(), 0) + 1
    phrase_occurs: dict[str, int] = {}
    phrase_noninitial: dict[str, int] = {}
    built: list[tuple[str, bool, list[re.Match[str]]]] = []
    for group in groups:
        phrase = text[group[0].start():group[-1].end()]
        noninitial = not initial[group[0].start()]
        built.append((phrase, noninitial, group))
        phrase_occurs[phrase] = phrase_occurs.get(phrase, 0) + 1
        if noninitial:
            phrase_noninitial[phrase] = phrase_noninitial.get(phrase, 0) + 1
    out = []
    for phrase, noninitial, group in built:
        strong = any(re.fullmatch(r"[A-Z][A-Za-z0-9]*[0-9][A-Za-z0-9]*|[A-Z]{2,}|[A-Z][A-Za-z]*[A-Z][A-Za-z]*", m.group()) for m in group)
        hard = strong or (
            phrase_occurs.get(phrase, 0) >= 2 and phrase_noninitial.get(phrase, 0) >= 1
        ) or any(
            token_occurs.get(m.group(), 0) >= 2 and token_noninitial.get(m.group(), 0) >= 1
            for m in group
        )
        out.append((phrase, hard, noninitial))
    return out


def _entity_result(source: str, mirror: str, register: str) -> tuple[float, int, bool, dict[str, object]]:
    source_phrases = _phrases(source)
    advisory = {item[0] for item in source_phrases}
    hard = {item[0] for item in source_phrases if item[1]}
    mirror_phrases = {
        item[0]
        for suppress in (True, False)
        for item in _phrases(mirror, suppress_initial_connectives=suppress)
    }
    retained = advisory & mirror_phrases
    missing = hard - retained
    retention = len(retained) / len(advisory) if advisory else 1.0
    return retention, len(advisory), not missing, {"hard_source": len(hard), "hard_missing": len(missing), "advisory_source": len(advisory), "published_retention_below_0_90": retention < 0.90 if register == "published" else None}


def _token_spans(raw: bytes) -> list[tuple[Span, bytes]]:
    text = raw.decode("utf-8", "strict")
    offs = _char_offsets(text)
    ans = []
    for m in re.finditer(r"\S+", text):
        span = Span(offs[m.start()], offs[m.end()])
        ans.append((span, raw[span.start:span.end]))
    return ans


def _paragraph_break(raw: bytes, left: Span, right: Span) -> bool:
    between = raw[left.end:right.start].decode("utf-8", "strict")
    # Atomic line-break groups: a single CRLF is one line break and must not be
    # split into `\r`+`\n`, which would fake a paragraph boundary between tokens
    # on adjacent CRLF lines and let a verbatim CRLF copy evade the exact-copy gate.
    return bool(re.search(r"(?>\r\n|\r|\n)[^\S\r\n]*(?>\r\n|\r|\n)", between))


def _exact_copy(source: bytes, mirror: bytes, source_mask: bytearray, mirror_mask: bytearray) -> dict[str, object]:
    s_tokens, m_tokens = _token_spans(source), _token_spans(mirror)
    def windows(tokens: list[tuple[Span, bytes]], mask: bytearray) -> set[tuple[bytes, ...]]:
        valid = [not any(mask[span.start:span.end]) for span, _ in tokens]
        found: set[tuple[bytes, ...]] = set()
        for i in range(len(tokens) - 12):
            if not all(valid[i:i + 13]):
                continue
            if any(_paragraph_break((source if tokens is s_tokens else mirror), tokens[j][0], tokens[j + 1][0]) for j in range(i, i + 12)):
                continue
            found.add(tuple(token for _, token in tokens[i:i + 13]))
        return found
    s_windows = windows(s_tokens, source_mask)
    m_valid = [not any(mirror_mask[span.start:span.end]) for span, _ in m_tokens]
    covered: set[int] = set()
    for i in range(len(m_tokens) - 12):
        if not all(m_valid[i:i + 13]) or any(_paragraph_break(mirror, m_tokens[j][0], m_tokens[j + 1][0]) for j in range(i, i + 12)):
            continue
        if tuple(token for _, token in m_tokens[i:i + 13]) in s_windows:
            covered.update(range(i, i + 13))
    eligible = sum(m_valid)
    if eligible < 13:
        return {"eligible_mirror_tokens": eligible, "covered_mirror_tokens": 0, "coverage": None, "ok": False, "reason": "insufficient_nonquote_evidence"}
    count = len(covered)
    return {"eligible_mirror_tokens": eligible, "covered_mirror_tokens": count, "coverage": round(count / eligible, 4), "ok": count * 100 <= eligible * 30, "reason": "ok" if count * 100 <= eligible * 30 else "over_0_30"}


def evaluate(source_raw: bytes, mirror_raw: bytes, register: str = "unknown", sidecar: tuple[bool, list[Region]] | None = None) -> dict[str, object]:
    source = source_raw.decode("utf-8", "strict")
    mirror = mirror_raw.decode("utf-8", "strict")
    source_text, mirror_text = source.replace("\r\n", "\n").replace("\r", "\n"), mirror.replace("\r\n", "\n").replace("\r", "\n")
    source_auto, mirror_auto = _automatic_regions(source_raw), _automatic_regions(mirror_raw)
    supplied_complete, annotations = sidecar if sidecar is not None else (False, [])
    source_regions, complete_verified = _reconcile(source_raw, source_auto, annotations, supplied_complete)
    required = register in {"published", "informal"}
    quotes, source_mask, mirror_mask = _quote_fidelity(source_raw, mirror_raw, source_regions, mirror_auto, required, complete_verified)
    # Distinguish a supplied complete sidecar that omitted detected auto regions.
    if supplied_complete and not complete_verified:
        quotes["reason"], quotes["ok"] = "complete_annotation_omits_detected_source_quote", False
    similarity = _similarity(source_text, mirror_text)
    retention, entities_source, ok_ent, entities = _entity_result(source_text, mirror_text, register)
    exact = _exact_copy(source_raw, mirror_raw, source_mask, mirror_mask)
    source_words, mirror_words = len(source_text.split()), len(mirror_text.split())
    paragraphs = lambda t: len([p for p in re.split(r"\n\s*\n", t) if p.strip()])
    ps, pm = paragraphs(source_text), paragraphs(mirror_text)
    ratio = mirror_words / source_words if source_words else 0.0
    ok_len, ok_par, ok_sim = ratio >= .85, abs(pm - ps) <= max(1, round(.10 * ps)), similarity <= .75
    all_pass = ok_len and ok_par and ok_sim and ok_ent and bool(quotes["ok"]) and bool(exact["ok"])
    return {"source_words": source_words, "mirror_words": mirror_words, "ratio": round(ratio, 4), "paragraphs_source": ps, "paragraphs_mirror": pm, "similarity": round(similarity, 4), "entity_retention": round(retention, 4), "entities_source": entities_source, "ok_len": ok_len, "ok_par": ok_par, "ok_sim": ok_sim, "ok_ent": ok_ent, "all_pass": all_pass, "gate_v3": {"schema_version": "setec-mirror-gate/3", "register": register, "quotes": quotes, "entities": entities, "advisories": {"similarity_below_0_15": similarity < .15}, "exact_copy": exact, "all_hard_pass": all_pass}}


class _ClosedParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - parser paths are subprocess-tested
        raise MirrorGateError("usage_error")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ClosedParser(add_help=False, allow_abbrev=False)
    parser.add_argument("source")
    parser.add_argument("mirror")
    parser.add_argument("--register", choices=REGISTERS, default="unknown")
    parser.add_argument("--quote-spans")
    try:
        args = parser.parse_args(argv)
        source, mirror = _read_input(args.source), _read_input(args.mirror)
        sidecar = _read_sidecar(args.quote_spans, source) if args.quote_spans else None
        result = evaluate(source, mirror, args.register, sidecar)
        sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        return 0
    except MirrorGateError as exc:
        _write_error(exc.code)
        return 2 if exc.code == "usage_error" else 3
    except Exception:
        _write_error("internal_error")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
