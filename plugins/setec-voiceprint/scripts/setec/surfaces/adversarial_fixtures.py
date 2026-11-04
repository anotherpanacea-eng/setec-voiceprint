#!/usr/bin/env python3
"""
adversarial_fixtures.py

Deterministic text transforms for public validation stress fixtures.

These helpers model tokenizer-layer attacks (zero-width spaces,
homoglyphs, soft hyphens). They do not change the provenance label of a
sample; they create an adversarial variant whose source label is
inherited and whose transform is recorded in the manifest.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from stylometry_core import WORD_RE


ZERO_WIDTH_SPACE = "\u200b"
SOFT_HYPHEN = "\u00ad"
HOMOGLYPHS = {
    "a": "\u0430",  # Cyrillic small a
    "e": "\u0435",  # Cyrillic small ie
    "o": "\u043e",  # Cyrillic small o
    "p": "\u0440",  # Cyrillic small er
    "c": "\u0441",  # Cyrillic small es
    "x": "\u0445",  # Cyrillic small ha
    "y": "\u0443",  # Cyrillic small u
}


def _insert_inside_token(token: str, marker: str) -> str:
    if len(token) < 4:
        return token
    mid = len(token) // 2
    return token[:mid] + marker + token[mid:]


def _transform_every_word(text: str, *, every: int, transform) -> str:
    if every <= 0:
        raise ValueError("every must be positive")
    word_index = 0

    def repl(match):
        nonlocal word_index
        word_index += 1
        token = match.group(0)
        if word_index % every != 0:
            return token
        return transform(token)

    return WORD_RE.sub(repl, text)


def insert_zero_width_spaces(text: str, *, every: int = 4) -> str:
    return _transform_every_word(
        text,
        every=every,
        transform=lambda token: _insert_inside_token(token, ZERO_WIDTH_SPACE),
    )


def insert_soft_hyphens(text: str, *, every: int = 6) -> str:
    return _transform_every_word(
        text,
        every=every,
        transform=lambda token: _insert_inside_token(token, SOFT_HYPHEN),
    )


def apply_homoglyphs(text: str, *, every: int = 5) -> str:
    def transform(token: str) -> str:
        chars = list(token)
        for idx, char in enumerate(chars):
            lower = char.lower()
            if lower in HOMOGLYPHS:
                replacement = HOMOGLYPHS[lower]
                chars[idx] = replacement.upper() if char.isupper() else replacement
                break
        return "".join(chars)

    return _transform_every_word(text, every=every, transform=transform)


TRANSFORMS = {
    "zero_width": insert_zero_width_spaces,
    "soft_hyphen": insert_soft_hyphens,
    "homoglyph": apply_homoglyphs,
}


# --- RAID adversarial transforms (additive; spec 16) -----------
#
# Deterministic, model-free `text -> text` transforms modeling the
# RAID benchmark's adversarial attacks (arXiv:2405.07940). Each is
# pure and seed-free: byte-identical across calls, no RNG, no model.
# They are added BESIDE the three existing transforms above; the
# `TRANSFORMS` keys for `zero_width` / `soft_hyphen` / `homoglyph`
# are unchanged (default-preserving). Like the existing transforms,
# none of these changes a sample's provenance label — they create an
# adversarial variant whose source label is inherited.

# Closed US->UK spelling table (a small bundled lookup, NOT a model).
_ALT_SPELLING = {
    "color": "colour",
    "colors": "colours",
    "honor": "honour",
    "favor": "favour",
    "behavior": "behaviour",
    "organize": "organise",
    "organized": "organised",
    "realize": "realise",
    "realized": "realised",
    "recognize": "recognise",
    "analyze": "analyse",
    "center": "centre",
    "theater": "theatre",
    "meter": "metre",
    "traveling": "travelling",
    "traveled": "travelled",
    "defense": "defence",
    "offense": "offence",
    "gray": "grey",
}

# Small closed synonym table (a bundled lookup, NOT a model). The
# replacement is deterministic and reversible-by-table only.
_SYNONYMS = {
    "big": "large",
    "small": "little",
    "fast": "quick",
    "happy": "glad",
    "begin": "start",
    "end": "finish",
    "important": "crucial",
    "help": "assist",
    "show": "display",
    "use": "employ",
    "make": "create",
    "old": "aged",
    "new": "fresh",
    "many": "numerous",
    "good": "fine",
}

def _apply_case(template: str, repl: str) -> str:
    """Carry the source token's capitalization onto a replacement."""
    if template.isupper():
        return repl.upper()
    if template[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def article_deletion(text: str) -> str:
    """Delete English articles (a / an / the). Deterministic.

    Drops the article token and one trailing space (if present) so no
    double space is left behind."""
    # The word is captured in group 1; an optional trailing space is
    # matched and consumed too. Replacing the whole match with "" both
    # removes the article and collapses the following space.
    return re.sub(r"\b(?:a|an|the)\b[ ]?", "", text, flags=re.IGNORECASE)


def number_swap(text: str) -> str:
    """Increment each run of ASCII digits by 1 (mod its width).

    Deterministic and pure: '2024' -> '2025', '9' -> '0'. Models the
    RAID number-swap attack without an RNG."""
    def repl(match):
        digits = match.group(0)
        bumped = str((int(digits) + 1) % (10 ** len(digits)))
        return bumped.zfill(len(digits))

    return re.sub(r"\d+", repl, text)


def paragraph_shuffle(text: str) -> str:
    """Reverse the order of blank-line-separated paragraphs.

    A fixed permutation (reversal) — deterministic so CI is stable.
    On a single-paragraph input this is a no-op at the paragraph level;
    the operator's real multi-paragraph fixtures exercise it."""
    paras = text.split("\n\n")
    if len(paras) < 2:
        return text
    return "\n\n".join(reversed(paras))


def misspelling(text: str, *, every: int = 3) -> str:
    """Introduce a deterministic single-char transposition in every
    Nth long-enough word (swap the two characters at the midpoint)."""
    def transform(token: str) -> str:
        if len(token) < 4:
            return token
        mid = len(token) // 2
        chars = list(token)
        chars[mid - 1], chars[mid] = chars[mid], chars[mid - 1]
        return "".join(chars)

    return _transform_every_word(text, every=every, transform=transform)


def alternative_spelling(text: str) -> str:
    """Apply the US->UK alternative-spelling table. Deterministic."""
    def transform(token: str) -> str:
        repl = _ALT_SPELLING.get(token.lower())
        return _apply_case(token, repl) if repl else token

    return _transform_every_word(text, every=1, transform=transform)


def insert_paragraph(text: str) -> str:
    """Insert a fixed boilerplate sentence after the first sentence.

    Deterministic boilerplate insertion (the RAID insert-paragraph
    attack). Always inserts the same marker so the transform is pure."""
    marker = (
        " Note that the following passage continues the same line of "
        "thought without interruption."
    )
    # Insert after the first sentence terminator, else append.
    match = re.search(r"[.!?]", text)
    if match:
        idx = match.end()
        return text[:idx] + marker + text[idx:]
    return text + marker


def case_swap(text: str, *, every: int = 2) -> str:
    """Swap the case of every Nth word (upper<->lower). Deterministic."""
    def transform(token: str) -> str:
        return token.swapcase()

    return _transform_every_word(text, every=every, transform=transform)


def whitespace(text: str, *, every: int = 3) -> str:
    """Insert an extra space after every Nth word. Deterministic."""
    def transform(token: str) -> str:
        return token + " "

    return _transform_every_word(text, every=every, transform=transform)


def synonym_swap(text: str) -> str:
    """Replace closed-table words with their bundled synonym.

    A small closed lookup — NOT a model. Deterministic and reversible
    only by the table; models the RAID synonym-swap attack."""
    def transform(token: str) -> str:
        repl = _SYNONYMS.get(token.lower())
        return _apply_case(token, repl) if repl else token

    return _transform_every_word(text, every=1, transform=transform)


# RAID attack taxonomy: obfuscation-class string -> generating
# transform. Additive to the PAN classes pan_replay already replays;
# pan_replay needs NO change to consume these (it scores every class
# string present in the manifest). The three pre-existing tokenizer
# transforms are reachable under their RAID class names too, without
# touching the legacy `TRANSFORMS` keys.
RAID_ATTACK_CLASSES = {
    "article_deletion": article_deletion,
    "number_swap": number_swap,
    "paragraph_shuffle": paragraph_shuffle,
    "misspelling": misspelling,
    "alternative_spelling": alternative_spelling,
    "insert_paragraph": insert_paragraph,
    "case_swap": case_swap,
    "whitespace": whitespace,
    "synonym_swap": synonym_swap,
    "homoglyph": apply_homoglyphs,
    "zero_width": insert_zero_width_spaces,
    "soft_hyphen": insert_soft_hyphens,
}


def _emit_raid_suite(text: str) -> list[dict[str, str]]:
    """Build one (clean, obfuscated) pair per RAID attack from one
    input, in the pairs.jsonl layout pan_replay.load_fixture_pairs
    consumes. Deterministic; suitable for regenerating static
    fixtures (the corpus itself is never redistributed)."""
    rows: list[dict[str, str]] = []
    for cls, transform in RAID_ATTACK_CLASSES.items():
        rows.append({
            "id": f"raid_{cls}",
            "obfuscation_class": cls,
            "clean": text,
            "obfuscated": transform(text),
        })
    return rows


# All transforms available to the CLI: the legacy keys plus the RAID
# attack classes (the RAID register supersets the legacy three, so a
# union keeps every legacy key reachable by its original name).
_ALL_CLI_TRANSFORMS = {**TRANSFORMS, **RAID_ATTACK_CLASSES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic adversarial text variants."
    )
    parser.add_argument("input", help="Input text file.")
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="Output text file (single-transform mode), or "
             "pairs.jsonl path with --raid-suite.",
    )
    parser.add_argument(
        "--transform",
        choices=sorted(_ALL_CLI_TRANSFORMS),
        default=None,
        help="Transform to apply (single-transform mode).",
    )
    parser.add_argument(
        "--raid-suite",
        action="store_true",
        help=(
            "Emit one (clean, obfuscated) pair per RAID attack from the "
            "input into a pairs.jsonl manifest (the layout pan_replay "
            "consumes). OUTPUT is the pairs.jsonl path."
        ),
    )
    parser.add_argument("--every", type=int, default=None, help="Apply to every Nth word.")
    args = parser.parse_args(argv)

    text = Path(args.input).read_text(encoding="utf-8", errors="ignore")

    if args.raid_suite:
        if not args.output:
            parser.error("--raid-suite requires an OUTPUT pairs.jsonl path")
        rows = _emit_raid_suite(text)
        with Path(args.output).open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return 0

    if not args.transform or not args.output:
        parser.error("single-transform mode requires --transform and OUTPUT")

    transform = _ALL_CLI_TRANSFORMS[args.transform]
    # Only the every-word transforms accept an `every` kwarg; the
    # paragraph/regex/table transforms take text only. Detect support
    # by inspecting the callable's parameter names.
    kwargs = {}
    if args.every is not None:
        code = getattr(transform, "__code__", None)
        if code is not None and "every" in code.co_varnames[: code.co_nlocals]:
            kwargs["every"] = args.every
    out = transform(text, **kwargs)
    Path(args.output).write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
