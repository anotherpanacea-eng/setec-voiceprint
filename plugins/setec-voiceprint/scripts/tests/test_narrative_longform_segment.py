#!/usr/bin/env python3
"""Tests for narrative_longform_segment.py (spec 79, M1).

Stdlib + pytest only. No network, no model, no judge.

Build-first posture: where the module was ambiguous, the sane behavior is
encoded here as the contract. Decisions taken (also see the "behavioral
decisions" tests at the bottom):

  * Empty text is SegmentationInfeasible (no unit ever starts).
  * A whole text shorter than the floor passes through as a single segment —
    the floor applies only to multi-segment results; the base audit owns the
    floor for whole-work passthrough.
  * A tier with no real matches is SKIPPED (revised at integration): results
    carry the tier that actually matched, or "whole_text" when nothing did.
  * Among compliant tiers, fewest excluded words wins, then coarsest tier
    (revised at integration): a coarse tier that drops a tail loses to a
    finer tier that covers every word.
  * An excluded sub_floor_tail is recorded by word count only; its offsets
    are deliberately absent from the envelope (raw offsets would violate the
    n_*-integer leaf rule) and live nowhere else either.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import narrative_longform_segment as nls  # type: ignore  # noqa: E402

TARGET = nls.DEFAULT_TARGET_WORDS          # 5000
FLOOR = nls.FLOOR_WORDS                    # 2000
CEILING = nls.CEILING_WORDS                # 25000
LIMIT = int(TARGET * nls.MAX_TARGET_RATIO)  # 7500

_SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def expected_framed(domain: bytes, payload: bytes) -> str:
    """Independent re-implementation of the framing rule.

    Deliberately does NOT call the module's helper: the point is to pin the
    construction ``SHA256(domain_ascii_LF || uint64_be(len) || payload)``, and
    a test that calls the function under test pins nothing.
    """
    return "sha256:" + hashlib.sha256(
        domain + struct.pack(">Q", len(payload)) + payload
    ).hexdigest()


# ---------------------------------------------------------------- fixtures

_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
          (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
          (5, "V"), (4, "IV"), (1, "I"))


def roman(n: int) -> str:
    out = []
    for value, glyph in _ROMAN:
        while n >= value:
            out.append(glyph)
            n -= value
    return "".join(out)


def body(n_words: int, salt: str) -> str:
    """One long line of n_words tokens that cannot match any tier pattern.

    Tokens contain digits and a 'q', so no line is a bare roman numeral and
    none starts with CHAPTER/BOOK/PART/STAVE.
    """
    return " ".join(f"{salt}w{i}q" for i in range(n_words))


def build_gutenberg(n_chapters: int = 10, body_words: int = 1000) -> str:
    """Plain-text Gutenberg-format novel: preamble + 'CHAPTER I.' headings."""
    parts = ["PROJECT TEXTBERG SAMPLE NOVEL",
             "Produced by nobody in particular"]
    for c in range(1, n_chapters + 1):
        parts.append(f"CHAPTER {roman(c)}.")
        parts.append(body(body_words, f"ch{c}"))
    return "\n\n".join(parts) + "\n"


def chaptered(word_counts: list[int]) -> str:
    """Chapters of exact body sizes; each unit = 2 heading words + body."""
    parts = []
    for c, n in enumerate(word_counts, start=1):
        parts.append(f"CHAPTER {roman(c)}.")
        parts.append(body(n, f"ch{c}"))
    return "\n\n".join(parts) + "\n"


def check_invariants(seg: "nls.Segmentation", text: str) -> None:
    """Structural invariants every shipped segmentation must satisfy."""
    assert seg.segmenter_version == nls.SEGMENTER_VERSION
    assert seg.n_segments == len(seg.segments) >= 1
    assert [s.index for s in seg.segments] == list(range(seg.n_segments))
    prev_end = None
    for s in seg.segments:
        assert 0 <= s.start < s.end <= len(text)
        if prev_end is not None:
            assert s.start == prev_end  # contiguous tiling, no gaps
        prev_end = s.end
        chunk = s.text(text)
        assert s.n_words == nls.count_words(chunk)
        assert s.content_sha256 == expected_framed(
            nls.DOMAIN_SEGMENT_CONTENT, chunk.encode("utf-8"))
    assert seg.segments[0].start == 0
    if not seg.excluded_spans:
        assert seg.segments[-1].end == len(text)  # full coverage
    # boundary hash preimage: compact JSON of [[start, end], ...]
    offsets = json.dumps([[s.start, s.end] for s in seg.segments],
                         separators=(",", ":")).encode()
    assert seg.boundary_offsets_sha256 == expected_framed(
        nls.DOMAIN_BOUNDARY_OFFSETS, offsets)
    assert seg.params_sha256 == nls.params_digest(seg.segment_target_words)
    assert _SHA_RE.match(seg.boundary_offsets_sha256)
    assert _SHA_RE.match(seg.params_sha256)


# ------------------------------------------------- 1. subprocess determinism

_RUNNER = """
import json, sys
sys.path.insert(0, sys.argv[1])
import narrative_longform_segment as m
text = open(sys.argv[2], "rb").read().decode("utf-8")
seg = m.segment_text(text)
sys.stdout.write(json.dumps({
    "boundary": seg.boundary_offsets_sha256,
    "params": seg.params_sha256,
    "tier": seg.tier,
    "words": seg.segment_words,
}, sort_keys=True, separators=(",", ":")))
"""


def test_determinism_across_subprocesses(tmp_path):
    text = build_gutenberg()
    fixture = tmp_path / "gutenberg.txt"
    fixture.write_bytes(text.encode("utf-8"))

    outputs = []
    for hashseed in ("0", "424242"):  # different hash seeds: no dict/set-order leak
        env = dict(os.environ, PYTHONHASHSEED=hashseed)
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER, str(ROOT), str(fixture)],
            capture_output=True, env=env, timeout=120)
        assert proc.returncode == 0, proc.stderr.decode()
        outputs.append(proc.stdout)

    assert outputs[0] == outputs[1]  # byte-identical across processes

    got = json.loads(outputs[0])
    seg = nls.segment_text(text)
    assert got["boundary"] == seg.boundary_offsets_sha256
    assert got["params"] == seg.params_sha256
    assert got["tier"] == seg.tier
    assert got["words"] == seg.segment_words


# ----------------------------------------------------- 2. greedy packing

def test_greedy_packing_sixty_chapters():
    # 60 chapters x (2 heading words + 500 body words) = 502-word units.
    # Greedy packing accumulates 9 units per segment (10 would exceed the
    # 5000 target), so 60 units pack to 6 x 4518 + one 3012-word tail = 7.
    text = chaptered([500] * 60)
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "chapter_heading"
    assert seg.n_segments == 7
    assert seg.segment_words == [4518] * 6 + [3012]
    assert all(FLOOR <= w <= LIMIT for w in seg.segment_words)
    # no sub-floor interior segments — packing, not one-unit-per-segment
    assert all(w >= FLOOR for w in seg.segment_words[:-1])
    assert not seg.excluded_spans


# ----------------------------------------------------- 3. the four tiers

def test_tier1_gutenberg_chapter_headings():
    text = build_gutenberg(n_chapters=10, body_words=1000)
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "chapter_heading"
    # preamble folds into the first segment; later segments start on headings
    assert seg.segment_words == [4017, 4008, 2004]
    assert seg.segments[1].text(text).startswith("CHAPTER V.")
    assert seg.segments[2].text(text).startswith("CHAPTER IX.")


def test_tier1_stave_and_bare_roman_numeral_headings():
    parts = [
        "A preamble of a few words before the first stave begins here",
        "STAVE I: MARLEYS GHOST", body(2400, "st1"),
        "II", body(2400, "st2"),
        "III", body(2400, "st3"),
        "IV", body(2400, "st4"),
    ]
    text = "\n\n".join(parts) + "\n"
    # every heading variant registers as a unit start (offset 0 = preamble)
    starts = nls._unit_starts(text, 0)
    for heading in ("STAVE I: MARLEYS GHOST", "\nII\n", "\nIII\n", "\nIV\n"):
        pos = text.index(heading)
        pos += 1 if heading.startswith("\n") else 0
        assert pos in starts, f"no unit start at {heading!r}"
    assert 0 in starts

    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "chapter_heading"
    assert seg.n_segments == 2
    # the second segment opens on the bare roman-numeral heading "III"
    assert seg.segments[1].text(text).startswith("III")


def test_tier2_scene_breaks_asterisks_and_dashes():
    scenes = [body(2000, f"s{k}") for k in range(5)]
    text = (scenes[0] + "\n\n***\n\n" + scenes[1] + "\n\n---\n\n" +
            scenes[2] + "\n\n* * *\n\n" + scenes[3] + "\n\n-----\n\n" +
            scenes[4] + "\n")
    # tier 1 sees no headings -> one 10006-word unit > 7500 -> descend
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "scene_break"
    assert seg.n_segments == 3
    assert all(FLOOR <= w <= LIMIT for w in seg.segment_words)
    # boundaries land right after separator lines
    assert seg.segments[1].text(text).lstrip().startswith("s2w0q")
    assert seg.segments[2].text(text).lstrip().startswith("s4w0q")


def test_tier3_blank_line_runs():
    secs = [body(2000, f"b{k}") for k in range(5)]
    text = (secs[0] + "\n\n\n" + secs[1] + "\n \n\t\n" + secs[2] +
            "\n\n\n" + secs[3] + "\n\n\n" + secs[4] + "\n")
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "blank_line_run"
    assert seg.n_segments == 3
    assert seg.segment_words == [4000, 4000, 2000]
    # the whitespace-bearing run ("\n \n\t\n") also split
    assert seg.segments[1].text(text).lstrip().startswith("b2w0q")


def test_tier4_paragraph_fallback():
    text = "\n\n".join(body(500, f"p{k}") for k in range(20)) + "\n"
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "paragraph"
    assert seg.segment_words == [5000, 5000]


# ------------------------------------ 4. descent re-checked AFTER tail merge

def test_descent_triggered_by_post_merge_oversize():
    """Review P1: the compliance check must run on the MERGED spans.

    Tier-1 packing here yields [6504, 1500]: every interior span is legal and
    only the tail is sub-floor — exactly the shape a merge 'fixes'. The merge
    produces a single 8004-word span > 1.5x target, so the tier must be
    rejected AFTER the merge and the segmenter must descend to scene breaks.
    An implementation that validated pre-merge (modulo the tail) and shipped
    the merged result would emit an oversized chapter_heading segment.
    """
    text = ("CHAPTER I.\n\n" + body(2500, "a") + "\n\n***\n\n" +
            body(2000, "b") + "\n\n***\n\n" + body(2000, "c") +
            "\n\nCHAPTER II.\n\n" + body(1498, "d") + "\n")

    # pin the fixture shape via the module's own internals
    spans = nls._pack(text, nls._unit_starts(text, 0), TARGET)
    words = [nls.count_words(text[s:e]) for s, e in spans]
    assert words == [6504, 1500]           # legal interior, sub-floor tail
    assert all(w <= LIMIT for w in words)  # pre-merge: nothing oversized
    merged, excluded = nls._merge_tail(text, spans)
    assert not excluded
    assert len(merged) == 1
    assert nls.count_words(text[merged[0][0]:merged[0][1]]) == 8004 > LIMIT

    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "scene_break"       # descended past chapter tier
    assert all(w <= LIMIT for w in seg.segment_words)
    assert all(w >= FLOOR for w in seg.segment_words)


def test_book_divided_100k_novel_descends_below_the_book_tier():
    """The fixture spec 79 mandates by name and the build never wrote.

    A 100,000-word novel divided on five BOOK headings packs to five
    ~20,000-word segments: a legal `{3,5}` count range that would license
    whole-novel claims off a study validated on 5,000-word segments. Segment
    SIZE is as load-bearing as segment count, so the tier must be rejected and
    the segmenter must descend.
    """
    books = []
    for b in range(1, 6):
        books.append(f"BOOK {roman(b)}.")
        # 20 paragraphs of 1,000 words: 20,000 words per BOOK, and the
        # paragraph tier below has real units to pack.
        books.append("\n\n".join(body(1000, f"bk{b}p{p}") for p in range(20)))
    text = "\n\n".join(books) + "\n"
    assert 99_000 < nls.count_words(text) < 101_000

    # The BOOK tier on its own yields exactly the shape the spec warns about.
    book_spans = nls._pack(text, nls._unit_starts(text, 0), TARGET)
    book_words = [nls.count_words(text[s:e]) for s, e in book_spans]
    assert len(book_words) == 5
    assert all(w > 20_000 for w in book_words)

    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier != "chapter_heading"        # descended past BOOK
    assert seg.n_segments >= 14
    assert all(w <= LIMIT for w in seg.segment_words)
    assert all(w >= FLOOR for w in seg.segment_words)
    assert not seg.excluded_spans


# ---------------------------------- 4b. a keyword alone is not a heading

@pytest.mark.parametrize("line", [
    # Codex P3, verbatim: a narrative sentence opening on the keyword.
    "CHAPTER headings are conventions of the printed book, not of the tale",
    "BOOK I read yesterday and did not much care for",
    "PART of the difficulty was that nobody agreed",
    "STAVE upon stave the cooper fitted, and the barrel took shape",
    "CHAPTERS were still being written when the money ran out",
])
def test_keyword_without_numeral_is_not_a_boundary(line):
    assert not nls._TIER_PATTERNS[0][1].match(line), line


@pytest.mark.parametrize("line", [
    "CHAPTER 1", "CHAPTER I.", "Chapter 12.", "CHAPTER XIV. THE HOUSE",
    "STAVE I: MARLEYS GHOST", "BOOK THE FIRST—THE CUP AND THE LIP",
    "PART TWO", "  BOOK 3 ", "chapter iv,",
])
def test_keyword_with_numeral_is_a_boundary(line):
    assert nls._TIER_PATTERNS[0][1].match(line), line


def test_keyword_prose_line_does_not_split_a_work():
    """End to end: the sentence must not open a segment.

    Two chapters of 2,500 words with a `CHAPTER headings...` sentence sitting
    mid-body. Under the keyword-only rule that sentence was a third unit
    start; under keyword+numeral there are exactly two.
    """
    prose = ("CHAPTER headings are conventions of the printed book, and "
             "this one begins a paragraph rather than a chapter.")
    text = ("CHAPTER I.\n\n" + body(2500, "a") + "\n\n" + prose + "\n\n" +
            body(2500, "b") + "\n\nCHAPTER II.\n\n" + body(2500, "c") + "\n")
    starts = nls._unit_starts(text, 0)
    assert len(starts) == 2                      # offset 0 + "CHAPTER II."
    assert text.index(prose) not in starts


# --------------------------------------------------- 5. oversized single unit

def test_single_paragraph_over_ceiling_is_infeasible():
    # Under the ratio limit for target=17000 (25500) but over the 25000
    # ceiling: the ceiling must reject it on its own.
    text = body(25_100, "big")
    with pytest.raises(nls.SegmentationInfeasible):
        nls.segment_text(text, segment_target_words=17_000)


def test_single_paragraph_over_ratio_limit_is_infeasible():
    text = body(8000, "wide")
    with pytest.raises(nls.SegmentationInfeasible):
        nls.segment_text(text)


def test_oversized_interior_unit_never_ships():
    # A 26000-word indivisible middle paragraph poisons every tier: the
    # segmenter must refuse rather than emit an oversized segment.
    text = body(500, "pre") + "\n\n" + body(26_000, "mid") + "\n\n" + \
        body(500, "post") + "\n"
    with pytest.raises(nls.SegmentationInfeasible):
        nls.segment_text(text)


# ------------------------------------------------------------- 6. tail rules

def test_subfloor_tail_merges_into_predecessor():
    # chapters of 5000 and 1000 body words -> packed [5002, 1002];
    # the 1002-word tail is sub-floor, merged result 6004 <= 25000 -> merge.
    text = chaptered([5000, 1000])
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "chapter_heading"
    assert seg.n_segments == 1
    assert seg.segment_words == [6004]
    assert seg.segments[-1].end == len(text)  # tail words retained
    assert seg.excluded_spans == ()


def test_subfloor_tail_excluded_when_merge_would_exceed_ceiling():
    # target 16800 -> ratio limit 25200. Packed [24002, 1200]: the tail is
    # sub-floor and merging would hit 25202 > 25000, so the tail is dropped
    # and recorded, and the surviving 24002-word segment ships (<= 25200).
    text = chaptered([24_000, 1198])
    seg = nls.segment_text(text, segment_target_words=16_800)
    check_invariants(seg, text)
    assert seg.tier == "chapter_heading"
    assert seg.n_segments == 1
    assert seg.segment_words == [24_002]
    assert seg.segments[-1].end < len(text)  # tail words NOT covered
    assert seg.excluded_spans == ({"reason": "sub_floor_tail",
                                   "n_words": 1200},)


# ------------------------------------------- 7. params digest sensitivity

def test_params_digest_stable_and_target_sensitive():
    assert nls.params_digest(5000) == nls.params_digest(5000)
    assert nls.params_digest(5000) != nls.params_digest(6000)
    seg = nls.segment_text(chaptered([2500, 2500]))
    assert seg.params_sha256 == nls.params_digest(5000)


def test_params_digest_changes_when_ratio_changes(monkeypatch):
    before = nls.params_digest(5000)
    monkeypatch.setattr(nls, "MAX_TARGET_RATIO", 2.0)
    assert nls.params_digest(5000) != before


def test_params_digest_changes_when_tier_list_changes(monkeypatch):
    before = nls.params_digest(5000)
    monkeypatch.setattr(nls, "_TIER_PATTERNS", nls._TIER_PATTERNS[:-1])
    assert nls.params_digest(5000) != before


def test_params_digest_changes_when_only_regex_flags_change(monkeypatch):
    """Codex P2: `params_digest` bound `p.pattern` and not `p.flags`.

    The shipped chapter tier depends on `re.I`. Recompile the IDENTICAL
    pattern text without it and `chapter i.` stops being a boundary while the
    receipt-bound segmenter identity says nothing changed.
    """
    name, pattern = nls._TIER_PATTERNS[0]
    assert pattern.flags & re.I, "fixture assumes the chapter tier is re.I"
    before = nls.params_digest(5000)

    case_sensitive = re.compile(pattern.pattern, pattern.flags & ~re.I)
    assert case_sensitive.pattern == pattern.pattern  # text is unchanged
    monkeypatch.setattr(
        nls, "_TIER_PATTERNS",
        ((name, case_sensitive),) + nls._TIER_PATTERNS[1:])
    assert nls.params_digest(5000) != before

    # ...and the flag really is behavioural, so the digest change is earned.
    assert pattern.match("chapter i.")
    assert not case_sensitive.match("chapter i.")


def test_params_digest_changes_when_word_pattern_flags_change(monkeypatch):
    before = nls.params_digest(5000)
    monkeypatch.setattr(
        nls, "_WORD", re.compile(nls._WORD.pattern, re.ASCII))
    assert nls.params_digest(5000) != before


# ------------------------------------------- 7b. framed digest domains

def test_framed_digest_construction_and_domain_registry():
    # Every frozen domain is ASCII, LF-terminated, and unique — a domain
    # reused across two payload schemas is the whole failure mode.
    assert len(set(nls.FROZEN_DOMAINS)) == len(nls.FROZEN_DOMAINS)
    for domain in nls.FROZEN_DOMAINS:
        assert isinstance(domain, bytes)
        assert domain.isascii() and domain.endswith(b"\n")
    # The construction is pinned independently of the implementation.
    assert nls.framed_digest(nls.DOMAIN_SEGMENT_CONTENT, b"abc") == \
        expected_framed(nls.DOMAIN_SEGMENT_CONTENT, b"abc")
    # An unregistered domain has no payload schema and is refused.
    with pytest.raises(nls.DomainError):
        nls.framed_digest(b"setec-not-a-registered-domain-v1\n", b"abc")
    with pytest.raises(nls.DomainError):
        nls.framed_digest(nls.DOMAIN_SEGMENT_CONTENT, "abc")


def test_same_bytes_under_two_schemas_do_not_collide():
    """Codex P2, the demonstrated collision.

    The source text `[[0,7]]` and the one-segment boundary-offsets payload
    `[[0,7]]` are the SAME BYTES. Under raw sha256 the content hash and the
    boundary hash of that segmentation were identical, so a receipt could not
    say which schema it had hashed.
    """
    text = "[[0,7]]"
    seg = nls.segment_text(text)
    assert seg.n_segments == 1
    assert (seg.segments[0].start, seg.segments[0].end) == (0, 7)
    offsets = json.dumps([[0, 7]], separators=(",", ":")).encode()
    assert offsets == text.encode("utf-8")  # the collision precondition

    assert seg.segments[0].content_sha256 != seg.boundary_offsets_sha256
    # ...and each is the framed digest under its OWN domain.
    assert seg.segments[0].content_sha256 == expected_framed(
        nls.DOMAIN_SEGMENT_CONTENT, offsets)
    assert seg.boundary_offsets_sha256 == expected_framed(
        nls.DOMAIN_BOUNDARY_OFFSETS, offsets)
    # The raw digest they used to share is emitted nowhere.
    raw = "sha256:" + hashlib.sha256(offsets).hexdigest()
    assert raw not in (seg.segments[0].content_sha256,
                       seg.boundary_offsets_sha256, seg.params_sha256)


def test_params_digest_changes_when_a_tier_pattern_changes(monkeypatch):
    before = nls.params_digest(5000)
    name, _ = nls._TIER_PATTERNS[1]
    swapped = (nls._TIER_PATTERNS[0],
               (name, re.compile(r"^#{3,}$", re.M))) + nls._TIER_PATTERNS[2:]
    monkeypatch.setattr(nls, "_TIER_PATTERNS", swapped)
    assert nls.params_digest(5000) != before


def test_params_digest_changes_when_bounds_change(monkeypatch):
    before = nls.params_digest(5000)
    monkeypatch.setattr(nls, "FLOOR_WORDS", 1500)
    mid = nls.params_digest(5000)
    assert mid != before
    monkeypatch.setattr(nls, "CEILING_WORDS", 30_000)
    assert nls.params_digest(5000) not in (before, mid)


# --------------------------------------------- 8. envelope leaf discipline

def _leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, path + (k,))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _leaves(v, path + (f"[{i}]",))
    else:
        yield path, obj


def _last_key(path):
    for part in reversed(path):
        if not (part.startswith("[") and part.endswith("]")):
            return part
    return ""


ALLOWED_INT_KEYS = {"segment_target_words", "index"}


def _assert_leaf_discipline(d: dict) -> None:
    for path, value in _leaves(d):
        key = _last_key(path)
        assert not isinstance(value, float), f"float leaf at {path}"
        if isinstance(value, bool):
            raise AssertionError(f"bool leaf at {path}")
        if isinstance(value, int):
            assert key in ALLOWED_INT_KEYS or key.startswith("n_"), \
                f"integer leaf {path} not n_*-named"
        if key.endswith("sha256"):
            assert isinstance(value, str) and _SHA_RE.match(value), \
                f"malformed hash at {path}: {value!r}"


def test_segmentation_dict_leaf_discipline():
    d = nls.segmentation_dict(nls.segment_text(build_gutenberg()))
    _assert_leaf_discipline(d)
    assert d["n_segments"] == len(d["segments"])
    assert d["segmenter_version"] == nls.SEGMENTER_VERSION
    assert d["segment_target_words"] == TARGET
    json.dumps(d)  # envelope-ready: JSON-serializable as-is


def test_segmentation_dict_leaf_discipline_with_excluded_spans():
    seg = nls.segment_text(chaptered([24_000, 1198]),
                           segment_target_words=16_800)
    d = nls.segmentation_dict(seg)
    _assert_leaf_discipline(d)
    assert d["excluded_spans"] == [{"reason": "sub_floor_tail",
                                    "n_words": 1200}]
    json.dumps(d)


# ------------------------------------------------------ 9. exotic separators

def _uni_body(salt: str, n_words: int) -> str:
    """Body whose word separators cycle through exotic Unicode whitespace."""
    seps = [" ", " ", " ", " ", " ", " ", " ", " "]
    parts = []
    for i in range(n_words):
        parts.append(f"{salt}w{i}q")
        if i < n_words - 1:
            parts.append(seps[i % len(seps)])
    return "".join(parts)


def test_unicode_line_separators_and_crlf():
    chunks = []
    for c in range(1, 5):
        chunks.append(f"CHAPTER {roman(c)}.")
        chunks.append(_uni_body(f"u{c}", 2500))
    text = "\r\n\r\n".join(chunks) + "\r\n"

    seg = nls.segment_text(text)
    check_invariants(seg, text)  # includes n_words == recount per segment
    assert seg.tier == "chapter_heading"
    assert seg.n_segments == 4
    assert seg.segment_words == [2502] * 4
    # word counts are stable: segments tile the text, so counts sum exactly
    reference = len(re.findall(r"\S+", text))
    assert sum(seg.segment_words) == nls.count_words(text) == reference
    # U+2028/U+2029/NBSP all separate words (each body has n_words tokens)
    assert nls.count_words(_uni_body("z", 100)) == 100


def test_unicode_text_determinism_in_process():
    text = "CHAPTER I.\r\n\r\n" + _uni_body("d", 2500) + "\r\n"
    a = nls.segment_text(text)
    b = nls.segment_text(text)
    assert a.boundary_offsets_sha256 == b.boundary_offsets_sha256
    assert a.params_sha256 == b.params_sha256
    assert a.segments == b.segments


# --------------------------------------------------------- 10. count_words

def test_count_words_matches_reference_regex():
    rng = random.Random(770101)
    tokens = ["a", "ab", "a,b", "--", "—", "don't", "x1q", "...",
              "CHAPTER", "I.", "***", "word"]
    seps = [" ", "  ", "\t", "\n", "\r\n", " ", " ", " ",
            "　", "\n\n", " \t "]
    for _ in range(200):
        parts = []
        for _ in range(rng.randrange(0, 60)):
            parts.append(rng.choice(tokens) if rng.random() < 0.6
                         else rng.choice(seps))
        s = "".join(parts)
        assert nls.count_words(s) == len(re.findall(r"\S+", s))
    for edge in ("", " ", "  ", "a b", "\r\n", "one"):
        assert nls.count_words(edge) == len(re.findall(r"\S+", edge))


# ------------------------------------------------- behavioral decisions

def test_empty_text_is_infeasible():
    # Decision: no units can start in "" -> refuse, don't return 0 segments.
    with pytest.raises(nls.SegmentationInfeasible):
        nls.segment_text("")


def test_target_below_floor_rejected():
    with pytest.raises(ValueError):
        nls.segment_text(build_gutenberg(), segment_target_words=FLOOR - 1)


def test_short_text_passes_through_as_single_segment():
    # Decision: the floor applies only to multi-segment results. A whole
    # short work is the identity segmentation; the base audit downstream
    # owns rejecting sub-floor inputs.
    text = body(300, "tiny") + "\n"
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.n_segments == 1
    assert seg.segment_words == [300]


def test_no_match_tiers_are_skipped_not_mislabelled():
    # REVISED decision (integration, 2026-07-27): a tier whose pattern matched
    # nothing is SKIPPED — it must not ship the whole text under its own name.
    # No chapter headings here, so the result carries the tier that actually
    # matched (scene_break), packed to one segment since both halves fit.
    text = body(1500, "x") + "\n\n***\n\n" + body(1500, "y") + "\n"
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "scene_break"
    assert seg.n_segments == 1
    assert seg.segment_words == [3001]  # includes the separator token


def test_boundaryless_in_range_text_ships_as_whole_text():
    # When NO tier matches anywhere and the text itself is compliant, it
    # ships as one segment under the honest label "whole_text".
    text = body(3000, "z")  # one giant line, no newlines at all
    seg = nls.segment_text(text)
    check_invariants(seg, text)
    assert seg.tier == "whole_text"
    assert seg.n_segments == 1


def test_finer_tier_with_fewer_exclusions_beats_coarse_tier():
    # REVISED decision (integration, 2026-07-27): a coarse tier that drops a
    # sub-floor tail LOSES to a finer tier covering every word. At tier 1 the
    # second chapter is a 1,001-word tail that cannot merge (24,001 + 1,001 >
    # ceiling), forcing an exclusion; the paragraph tier packs everything.
    ch1 = "\n\n".join(body(1000, f"a{i}") for i in range(24))
    text = ("CHAPTER I\n" + ch1 + "\n\n" +
            "CHAPTER II\n" + body(1000, "b") + "\n")
    seg = nls.segment_text(text, segment_target_words=17000)
    check_invariants(seg, text)
    assert seg.excluded_spans == ()
    assert seg.tier != "chapter_heading"
    assert sum(seg.segment_words) == nls.count_words(text)
