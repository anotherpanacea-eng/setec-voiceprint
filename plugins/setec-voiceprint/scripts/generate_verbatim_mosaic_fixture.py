#!/usr/bin/env python3
"""Build deterministic mechanical mosaic fixtures from operator-supplied public-domain prose.

This is a weak proxy: it makes no relevance selection or smoothing edits.
Only supply material you have verified is public domain for the intended use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from originality_audit import _TOKEN, _load_reference_dir, audit_originality
from segmentation_feature_lens import sentence_spans

CONNECTIVES = (
    "Meanwhile, another account took up the matter.",
    "Afterward, the scene shifted to a different place.",
    "The next passage followed a separate course.",
)


def generate_fixture(reference: list[tuple[str, str]], *, seed: str,
                     paragraphs: int) -> tuple[str, list[dict[str, str | int | None]]]:
    if paragraphs < 1:
        raise ValueError("paragraphs must be >= 1")
    candidates: list[tuple[str, str]] = []
    for source, text in reference:
        for paragraph in re.split(r"\n\s*\n+", text):
            spans = sentence_spans(paragraph)
            if not spans:
                continue
            # Retain complete sentences only; omit trailing partial prose.
            complete = [end for _, end in spans if paragraph[end - 1:end] in (".", "!", "?")]
            if not complete:
                continue
            excerpt = paragraph[:complete[-1]].strip()
            if len(_TOKEN.findall(excerpt.lower())) >= 8:
                candidates.append((source, excerpt))
    # One paragraph per source prevents accidental repetition in the fixture.
    ranked = sorted(candidates,
                    key=lambda p: (hashlib.sha256(f"{seed}\x1f{p[0]}\x1f{p[1]}".encode()).hexdigest(),
                                   p[0], p[1]))
    chosen: list[tuple[str, str]] = []
    used: set[str] = set()
    for source, passage in ranked:
        if source not in used:
            chosen.append((source, passage))
            used.add(source)
        if len(chosen) == paragraphs:
            break
    if len(chosen) != paragraphs:
        raise ValueError("not enough distinct sources with complete paragraphs")
    parts: list[str] = []
    labels: list[dict[str, str | int | None]] = []
    cursor = 0
    for i, (source, passage) in enumerate(chosen):
        if i:
            connective = CONNECTIVES[(int(hashlib.sha256(f"{seed}:{i}".encode()).hexdigest(), 16)
                                       % len(CONNECTIVES))]
            parts.append(connective)
            n = len(_TOKEN.findall(connective.lower()))
            labels.append({"start": cursor, "length": n, "kind": "connective", "source": None})
            cursor += n
        parts.append(passage)
        n = len(_TOKEN.findall(passage.lower()))
        labels.append({"start": cursor, "length": n, "kind": "copied", "source": source})
        cursor += n
    target = "\n\n".join(parts) + "\n"
    # Labels are ground truth only when the shipped greedy matcher recovers
    # their token-level source assignments. Common/duplicated excerpts and
    # connective text that occurs in the pool can break that property even
    # when every chosen source ID is distinct. Refuse such a fixture rather
    # than publish plausible-looking but false labels.
    expected: list[str | None] = [None] * cursor
    for label in labels:
        expected[label["start"]:label["start"] + label["length"]] = [label["source"]] * label["length"]
    recovered = audit_originality(target, reference, min_ngram=8, include_spans=True)
    actual: list[str | None] = [None] * cursor
    for span in recovered["all_spans"]:
        actual[span["start"]:span["start"] + span["length"]] = [span["source"]] * span["length"]
    if actual != expected:
        raise ValueError("reference pool cannot yield unambiguous token-level fixture labels")
    return target, labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", required=True)
    parser.add_argument("--seed", default="0")
    parser.add_argument("--paragraphs", type=int, default=3)
    parser.add_argument("--out", required=True)
    parser.add_argument("--labels-out", required=True)
    args = parser.parse_args(argv)
    pool = [(src, text) for src, text, _ in _load_reference_dir(Path(args.reference_dir))]
    try:
        text, labels = generate_fixture(pool, seed=args.seed, paragraphs=args.paragraphs)
    except ValueError as exc:
        parser.error(str(exc))
    Path(args.out).write_text(text, encoding="utf-8")
    tokens = [{"token_index": i, "kind": label["kind"], "source": label["source"]}
              for label in labels
              for i in range(label["start"], label["start"] + label["length"])]
    Path(args.labels_out).write_text(json.dumps({"seed": args.seed, "segments": labels,
                                                "tokens": tokens},
                                               indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
