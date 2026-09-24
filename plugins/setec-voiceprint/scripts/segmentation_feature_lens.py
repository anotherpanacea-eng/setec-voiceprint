"""Import-clean feature lens shared by document segmentation and mosaic audit.

The formulas are the shipped within-document lens. Keep this module stdlib-only:
the segmentation CLI may load optional tiers, but the mosaic M1 must not.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from setec.core.textprims import FUNCTION_WORDS
from stylometry_distance import safe_mean, safe_sd

WORD_RE = re.compile(r"[A-Za-z']+")
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}")
CHAR_NGRAM_NS = (3, 4, 5)
EPSILON = 1e-9


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Fixed rule splitter with exact offsets; no environment-dependent tokenizer."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in SENTENCE_BOUNDARY.finditer(text):
        raw = text[start:match.start()]
        if raw.strip():
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            spans.append((start + left, start + right))
        start = match.end()
    raw = text[start:]
    if raw.strip():
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        spans.append((start + left, start + right))
    return spans


def window_features(window_text: str) -> dict[str, float]:
    """Shipped function-word, character n-gram, and sentence-shape lens."""
    words = WORD_RE.findall(window_text.lower())
    total_words = len(words)
    counts = Counter(w for w in words if w in FUNCTION_WORDS)
    combined = {
        w: counts.get(w, 0) / total_words if total_words else 0.0
        for w in sorted(FUNCTION_WORDS)
    }
    normalized = re.sub(r"\s+", " ", window_text.lower()).strip()
    for n in CHAR_NGRAM_NS:
        if len(normalized) < n:
            continue
        grams = Counter(normalized[i:i + n] for i in range(len(normalized) - n + 1))
        total = sum(grams.values())
        combined.update({f"ch{n}:{g}": count / total for g, count in grams.items()})
    sentences = [window_text[a:b] for a, b in sentence_spans(window_text)]
    if not sentences:
        sentences = [window_text] if window_text.strip() else ["a"]
    lengths = [len(WORD_RE.findall(s.lower())) for s in sentences]
    mean = safe_mean(lengths)
    sd = safe_sd(lengths)
    variance = sd * sd
    shape = {
        "n_sentences": len(lengths), "mean": mean, "sd": sd,
        "min": float(min(lengths)), "max": float(max(lengths)),
        "variance": variance,
        "burstiness_B": (sd - mean) / (sd + mean) if len(lengths) > 1 and sd + mean > 0 else 0.0,
    }
    combined.update({f"sent_shape_{k}": float(v) for k, v in shape.items()})
    return combined


def z_score_features(raw: list[dict[str, float]], names: list[str]) -> list[dict[str, float]]:
    return z_score_against(raw, raw, names)


def z_score_against(raw: list[dict[str, float]], basis: list[dict[str, float]],
                    names: list[str]) -> list[dict[str, float]]:
    """Apply the document's fixed feature moments to another window population."""
    z: list[dict[str, float]] = [{} for _ in raw]
    for name in names:
        values = [row.get(name, 0.0) for row in basis]
        mean, sd = safe_mean(values), safe_sd(values)
        for i, row in enumerate(raw):
            # A clipped junction can contain a feature absent from every
            # whole-document basis window. Its standardized value is
            # undefined, not a billion-sigma observation.
            z[i][name] = (row.get(name, 0.0) - mean) / (sd + EPSILON) if sd else 0.0
    return z


def cosine_similarity(a: dict[str, float], b: dict[str, float], names: list[str]) -> float:
    dot = sum(a.get(n, 0.0) * b.get(n, 0.0) for n in names)
    norm_a = sum(a.get(n, 0.0) ** 2 for n in names)
    norm_b = sum(b.get(n, 0.0) ** 2 for n in names)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (math.sqrt(norm_a) * math.sqrt(norm_b))))


def cosine_distance(a: dict[str, float], b: dict[str, float], names: list[str]) -> float:
    if all(a.get(n, 0.0) == 0.0 for n in names) or all(b.get(n, 0.0) == 0.0 for n in names):
        return 0.0
    return (1.0 - cosine_similarity(a, b, names)) / 2.0
