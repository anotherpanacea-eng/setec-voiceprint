#!/usr/bin/env python3
"""narrative_longform_segment.py — deterministic segmentation for works above
the narrative-decision audit's supported length range (spec 77, M1).

The audit's home range is 2,000–25,000 words. Novels exceed it, so a long work
is cut into in-range segments, each scored independently. This module owns only
the cut. It has no judge, no model, no network, and no aggregation.

Segment construction is **greedy packing over boundary candidates**, which is
the rule the spec drafts kept omitting and which decides everything else:

  1. Pick the coarsest boundary tier (chapter → scene break → blank line →
     paragraph) whose *packed* result satisfies the size rules.
  2. Within a tier, walk the units in order and accumulate into the current
     segment until adding the next unit would exceed the target; then close.
     Packing is what keeps interior segments near target — a 30,000-word work
     of sixty 500-word chapters packs to ~6 segments rather than leaving 59
     units below the floor.
  3. Merge a sub-floor tail into its predecessor when the result stays under
     the ceiling; otherwise record it as an excluded span.
  4. Re-check the size rules on the merged result, and descend a tier if the
     merge pushed a segment over the limit.

Determinism: same bytes and parameters produce byte-identical boundaries in any
process. No RNG, no dict-ordering dependence, no clock.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from typing import Iterable

SEGMENTER_VERSION = "narrative-longform-segmenter/1"

DEFAULT_TARGET_WORDS = 5000
FLOOR_WORDS = 2000          # the base audit's advisory register floor
CEILING_WORDS = 25000       # the base audit's advisory upper bound
MAX_TARGET_RATIO = 1.5      # a tier is rejected if any packed segment exceeds this

_WORD = re.compile(r"\S+")

# Boundary tiers, coarsest first. Each matches the START of a unit.
#
# CRLF: every pattern tolerates \r before a line end — Gutenberg plain text is
# frequently CRLF, and a bare-\n pattern set silently refuses such files at
# every tier below chapter headings (found by the M1 test build).
#
# Bare roman-numeral heading lines must be WELL-FORMED numerals: a subtractive
# roman regex, so one-word lines like "DID.", "MID" or "CIVIC" (letters drawn
# from IVXLCDM but not a numeral) are not chapter boundaries.
_ROMAN = r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_TIER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chapter_heading", re.compile(
        r"^[ \t]*(?:CHAPTER|BOOK|PART|STAVE)\b.*$"
        r"|^[ \t]*(?=[IVXLCDM])" + _ROMAN + r"\.?[ \t]*\r?$",
        re.M | re.I)),
    ("scene_break", re.compile(
        r"^[ \t]*(?:\*[ \t]*){3,}[ \t]*\r?$|^[ \t]*-{3,}[ \t]*\r?$", re.M)),
    ("blank_line_run", re.compile(r"\r?\n(?:[ \t]*\r?\n){2,}")),
    ("paragraph", re.compile(r"\r?\n(?:[ \t]*\r?\n)+")),
)


class SegmentationInfeasible(ValueError):
    """No tier yields a compliant segmentation of this text."""


@dataclass(frozen=True)
class Segment:
    index: int
    start: int
    end: int
    n_words: int
    content_sha256: str

    def text(self, source: str) -> str:
        return source[self.start:self.end]


@dataclass(frozen=True)
class Segmentation:
    segmenter_version: str
    tier: str
    segment_target_words: int
    segments: tuple[Segment, ...]
    excluded_spans: tuple[dict, ...]
    params_sha256: str
    boundary_offsets_sha256: str

    @property
    def n_segments(self) -> int:
        return len(self.segments)

    @property
    def segment_words(self) -> list[int]:
        return [s.n_words for s in self.segments]


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def params_digest(target: int) -> str:
    """Hash of every constant that changes a segmentation.

    Bound into receipts, so a builder who tunes the ratio or adds a tier
    invalidates prior licences by construction rather than by discipline.
    """
    payload = {
        "segmenter_version": SEGMENTER_VERSION,
        "segment_target_words": target,
        "floor_words": FLOOR_WORDS,
        "ceiling_words": CEILING_WORDS,
        "max_target_ratio": MAX_TARGET_RATIO,
        "tiers": [name for name, _ in _TIER_PATTERNS],
        "tier_patterns": [p.pattern for _, p in _TIER_PATTERNS],
        "word_pattern": _WORD.pattern,
    }
    return _sha(json.dumps(payload, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False).encode())


def _unit_starts(text: str, tier_index: int) -> list[int]:
    """Character offsets at which a unit begins, always including 0."""
    _, pattern = _TIER_PATTERNS[tier_index]
    starts = {0}
    for m in pattern.finditer(text):
        # a heading starts its unit; a separator ends the previous one
        starts.add(m.start() if tier_index == 0 else m.end())
    return sorted(s for s in starts if s < len(text))


def _pack(text: str, starts: Iterable[int], target: int) -> list[tuple[int, int]]:
    """Greedy accumulation of units into (start, end) spans near `target`."""
    starts = list(starts)
    bounds = starts + [len(text)]
    units = [(bounds[i], bounds[i + 1]) for i in range(len(starts))]
    spans: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_words = 0
    for u_start, u_end in units:
        u_words = count_words(text[u_start:u_end])
        if cur_start is None:
            cur_start, cur_words = u_start, u_words
            continue
        if cur_words + u_words > target and cur_words > 0:
            spans.append((cur_start, u_start))
            cur_start, cur_words = u_start, u_words
        else:
            cur_words += u_words
    if cur_start is not None:
        spans.append((cur_start, len(text)))
    return spans


def _merge_tail(text: str, spans: list[tuple[int, int]]
                ) -> tuple[list[tuple[int, int]], list[dict]]:
    excluded: list[dict] = []
    if len(spans) < 2:
        return spans, excluded
    last_start, last_end = spans[-1]
    tail_words = count_words(text[last_start:last_end])
    if tail_words >= FLOOR_WORDS:
        return spans, excluded
    prev_start, _ = spans[-2]
    merged_words = count_words(text[prev_start:last_end])
    if merged_words <= CEILING_WORDS:
        return spans[:-2] + [(prev_start, last_end)], excluded
    excluded.append({"reason": "sub_floor_tail", "n_words": tail_words})
    return spans[:-1], excluded


def _compliant(text: str, spans: list[tuple[int, int]], target: int) -> bool:
    if not spans:
        return False
    limit = target * MAX_TARGET_RATIO
    for start, end in spans:
        w = count_words(text[start:end])
        if w > limit or w > CEILING_WORDS:
            return False
        if len(spans) > 1 and w < FLOOR_WORDS:
            return False
    return True


def segment_text(text: str, *, segment_target_words: int = DEFAULT_TARGET_WORDS
                 ) -> Segmentation:
    """Cut `text` into in-range segments, or refuse.

    Raises SegmentationInfeasible when no tier produces a compliant result —
    e.g. a single undivided paragraph longer than the ceiling.
    """
    if segment_target_words < FLOOR_WORDS:
        raise ValueError("segment_target_words below the audit's register floor")
    if count_words(text) == 0:
        raise SegmentationInfeasible("empty or whitespace-only text")

    # Tier selection: among tiers whose PACKED-then-MERGED result is compliant,
    # prefer the one excluding the fewest words, then the coarsest tier. A
    # coarse tier that drops a sub-floor tail must not beat a finer tier that
    # covers every word (M1 build decision; the draft specs left this open).
    # A tier whose pattern matched nothing is skipped rather than shipping the
    # whole text under a misleading tier name; if NO tier matches anywhere and
    # the whole text is itself compliant, it ships as one segment labelled
    # "whole_text".
    candidates: list[tuple[int, int, str, list[tuple[int, int]], list[dict]]] = []
    any_tier_matched = False
    for tier_index, (tier_name, _) in enumerate(_TIER_PATTERNS):
        starts = _unit_starts(text, tier_index)
        if len(starts) <= 1:
            continue
        any_tier_matched = True
        spans = _pack(text, starts, segment_target_words)
        spans, excluded = _merge_tail(text, spans)
        # descent is re-evaluated AFTER the merge: a legal segment plus a
        # merged tail can exceed the limit and must not ship.
        if not _compliant(text, spans, segment_target_words):
            continue
        excluded_words = sum(x["n_words"] for x in excluded)
        candidates.append((excluded_words, tier_index, tier_name, spans, excluded))
        if excluded_words == 0:
            break  # nothing finer can beat zero exclusions at a coarser tier

    tier_name: str | None = None
    spans: list[tuple[int, int]] = []
    excluded: list[dict] = []
    if candidates:
        _, _, tier_name, spans, excluded = min(candidates, key=lambda c: (c[0], c[1]))
    elif not any_tier_matched and _compliant(text, [(0, len(text))],
                                             segment_target_words):
        tier_name, spans = "whole_text", [(0, len(text))]

    if tier_name is not None:
        segments = tuple(
            Segment(index=i, start=s, end=e,
                    n_words=count_words(text[s:e]),
                    content_sha256=_sha(text[s:e].encode("utf-8")))
            for i, (s, e) in enumerate(spans)
        )
        offsets = json.dumps([[s.start, s.end] for s in segments],
                             separators=(",", ":")).encode()
        return Segmentation(
            segmenter_version=SEGMENTER_VERSION,
            tier=tier_name,
            segment_target_words=segment_target_words,
            segments=segments,
            excluded_spans=tuple(excluded),
            params_sha256=params_digest(segment_target_words),
            boundary_offsets_sha256=_sha(offsets),
        )

    raise SegmentationInfeasible(
        "no boundary tier yields segments within "
        f"[{FLOOR_WORDS}, {min(CEILING_WORDS, int(segment_target_words * MAX_TARGET_RATIO))}] words"
    )


def segmentation_dict(seg: Segmentation) -> dict:
    """Envelope-ready projection. Counts use n_* names; no derived numerics."""
    return {
        "segmenter_version": seg.segmenter_version,
        "tier": seg.tier,
        "segment_target_words": seg.segment_target_words,
        "params_sha256": seg.params_sha256,
        "boundary_offsets_sha256": seg.boundary_offsets_sha256,
        "n_segments": seg.n_segments,
        "segments": [
            {"index": s.index, "n_words": s.n_words,
             "content_sha256": s.content_sha256}
            for s in seg.segments
        ],
        "excluded_spans": [dict(x) for x in seg.excluded_spans],
    }
