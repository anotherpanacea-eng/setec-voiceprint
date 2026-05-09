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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic adversarial text variants."
    )
    parser.add_argument("input", help="Input text file.")
    parser.add_argument("output", help="Output text file.")
    parser.add_argument(
        "--transform",
        choices=sorted(TRANSFORMS),
        required=True,
        help="Transform to apply.",
    )
    parser.add_argument("--every", type=int, default=None, help="Apply to every Nth word.")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8", errors="ignore")
    kwargs = {"every": args.every} if args.every is not None else {}
    out = TRANSFORMS[args.transform](text, **kwargs)
    Path(args.output).write_text(out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
