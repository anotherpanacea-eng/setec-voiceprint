#!/usr/bin/env python3
"""
variance_audit.py
Layer A distributional diagnostics for the ai-prose-detection skill.

Computes the eleven variance signals documented in
references/distributional-diagnostics.md and reports per-document
statistics that predict mode-collapse magnitude.

Usage:
    python variance_audit.py INPUT.txt [--json] [--baseline-dir DIR]
                              [--mattr-window 50] [--no-tier2] [--no-tier3]

Tiers:
  Tier 1 (always):  sentence-length stats + burstiness, MATTR, MTLD,
                    Yule's K, Shannon entropy, FKGL stats, connective
                    density, function-word fingerprint.
  Tier 2 (spaCy):   POS-bigram entropy + KL against reference, MDD-SD.
  Tier 3 (embeddings): adjacent-sentence cosine mean and SD.
                       Falls back to TF-IDF if sentence-transformers
                       is unavailable; falls back to nothing if
                       scikit-learn is also unavailable.

Outputs a JSON object and/or a human-readable summary. With a baseline
directory, also reports the draft's z-score on each signal against
the baseline distribution.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from dataclasses import dataclass

# Task-surface tag. The framework distinguishes four surfaces:
#   - smoothing_diagnosis: prose-quality diagnosis, regardless of provenance
#   - voice_coherence:     does this draft match a writer/register baseline
#   - validation:          empirical performance against a labeled corpus
#   - craft_restoration:   what to do (lives in skill references, not scripts)
# Every script's JSON output carries its surface so a downstream harness
# can refuse to mix scores across surfaces, and reports self-identify
# which question they answer. See ROADMAP.md "Phase 1 -> Phase 2
# operational sequence" for the contract.
TASK_SURFACE = "smoothing_diagnosis"
from collections import Counter
from pathlib import Path
from typing import Any

from preprocessing import (
    aggregate_preprocessing_metadata,
    available_rule_names,
    strip_non_prose,
)

# ---------- Optional dependencies ----------
try:
    import textstat  # type: ignore
    HAS_TEXTSTAT = True
except ImportError:
    HAS_TEXTSTAT = False

try:
    import nltk  # type: ignore
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError:
        try:
            nltk.download("punkt", quiet=True)
        except Exception:
            pass
    HAS_NLTK = True
except ImportError:
    HAS_NLTK = False

try:
    import spacy  # type: ignore
    try:
        _NLP = spacy.load("en_core_web_sm")
        HAS_SPACY = True
    except Exception:
        HAS_SPACY = False
        _NLP = None
except ImportError:
    HAS_SPACY = False
    _NLP = None

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
    _ST_MODEL = None
    HAS_ST = True
except ImportError:
    HAS_ST = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.metrics.pairwise import cosine_similarity  # type: ignore
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ---------- Resource lists ----------

# Top function words (Mosteller-Wallace + extensions).
FUNCTION_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is",
    "it", "its", "itself", "just", "me", "might", "mine", "more", "most",
    "must", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "one", "only", "or", "other", "ought", "our", "ours", "ourselves",
    "out", "over", "own", "same", "shall", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "upon", "us", "very", "was", "we", "were", "what",
    "when", "where", "which", "while", "who", "whom", "whose", "why", "will",
    "with", "would", "yet", "you", "your", "yours", "yourself", "yourselves",
}

# Discourse markers / connectives flagged for over-density.
CONNECTIVES = {
    "furthermore", "moreover", "additionally", "in addition", "however",
    "therefore", "thus", "consequently", "hence", "in conclusion",
    "to summarize", "to conclude", "in summary", "it is important to note",
    "it should be noted", "notably", "interestingly", "importantly",
    "remarkably", "specifically", "particularly", "in particular",
    "for example", "for instance", "namely", "in other words",
    "that is to say", "as a result", "as such", "indeed", "in fact",
    "of course", "naturally", "clearly", "obviously", "ultimately",
    "essentially", "fundamentally", "in essence",
}


# ---------- Tokenization ----------

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}")
_WORD_RE = re.compile(r"[A-Za-z']+")


def split_sentences(text: str) -> list[str]:
    if HAS_NLTK:
        try:
            from nltk.tokenize import sent_tokenize  # type: ignore
            sents = [s.strip() for s in sent_tokenize(text) if s.strip()]
            if sents:
                return sents
        except Exception:
            pass
    parts = _SENT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def split_words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def count_syllables_word(word: str) -> int:
    """Heuristic syllable count for a single word (lowercase, alpha)."""
    word = word.lower().strip()
    if not word:
        return 0
    if HAS_TEXTSTAT:
        try:
            return max(1, int(textstat.syllable_count(word)))
        except Exception:
            pass
    # Heuristic: count vowel groups; subtract silent e.
    vowels = "aeiouy"
    count = 0
    prev = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev:
            count += 1
        prev = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def count_syllables(words: list[str]) -> int:
    return sum(count_syllables_word(w) for w in words)


# ---------- Tier 1 metrics ----------

def sentence_length_stats(sentences: list[str]) -> dict[str, float]:
    lengths = [len(split_words(s)) for s in sentences]
    if len(lengths) < 2:
        return {
            "n_sentences": len(lengths),
            "mean": float(lengths[0]) if lengths else 0.0,
            "sd": 0.0,
            "min": float(lengths[0]) if lengths else 0.0,
            "max": float(lengths[0]) if lengths else 0.0,
            "variance": 0.0,
            "burstiness_B": 0.0,
        }
    mean = statistics.mean(lengths)
    sd = statistics.stdev(lengths)
    var = statistics.variance(lengths)
    B = (sd - mean) / (sd + mean) if (sd + mean) > 0 else 0.0
    return {
        "n_sentences": len(lengths),
        "mean": mean,
        "sd": sd,
        "min": float(min(lengths)),
        "max": float(max(lengths)),
        "variance": var,
        "burstiness_B": B,
    }


def mattr(words: list[str], window: int = 50) -> float:
    if len(words) < window:
        if not words:
            return 0.0
        return len(set(words)) / len(words)
    ratios = []
    for i in range(0, len(words) - window + 1):
        chunk = words[i:i + window]
        ratios.append(len(set(chunk)) / window)
    return sum(ratios) / len(ratios) if ratios else 0.0


def mtld_one_direction(words: list[str], threshold: float = 0.72) -> float:
    if not words:
        return 0.0
    factor_count = 0
    types: set[str] = set()
    token_count = 0
    last_ttr = 1.0
    for w in words:
        token_count += 1
        types.add(w)
        ttr = len(types) / token_count
        last_ttr = ttr
        if ttr <= threshold and token_count > 1:
            factor_count += 1
            types = set()
            token_count = 0
    if token_count > 0:
        # fractional credit for trailing partial factor
        if last_ttr < 1.0:
            partial = (1 - last_ttr) / (1 - threshold)
            factor_count += min(partial, 1.0)
    if factor_count == 0:
        return float(len(words))
    return len(words) / factor_count


def mtld(words: list[str], threshold: float = 0.72) -> float:
    forward = mtld_one_direction(words, threshold)
    backward = mtld_one_direction(list(reversed(words)), threshold)
    return (forward + backward) / 2


def yules_k(words: list[str]) -> float:
    if not words:
        return 0.0
    counts = Counter(words)
    N = len(words)
    M2 = sum(c * c for c in counts.values())
    if N == 0:
        return 0.0
    return 1e4 * (M2 - N) / (N * N)


def shannon_entropy(words: list[str]) -> float:
    if not words:
        return 0.0
    counts = Counter(words)
    N = len(words)
    H = 0.0
    for c in counts.values():
        p = c / N
        H -= p * math.log2(p)
    return H


def fkgl_per_sentence(sentence: str) -> float | None:
    words = split_words(sentence)
    if not words:
        return None
    syllables = count_syllables(words)
    W = len(words)
    if W == 0:
        return None
    # Single-sentence form: 0.39 * W + 11.8 * (Sy/W) - 15.59
    return 0.39 * W + 11.8 * (syllables / W) - 15.59


def fkgl_stats(sentences: list[str]) -> dict[str, float]:
    scores = [s for s in (fkgl_per_sentence(sent) for sent in sentences) if s is not None]
    if not scores:
        return {"mean": 0.0, "sd": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    if len(scores) == 1:
        return {"mean": scores[0], "sd": 0.0, "min": scores[0], "max": scores[0], "n": 1}
    return {
        "mean": statistics.mean(scores),
        "sd": statistics.stdev(scores),
        "min": min(scores),
        "max": max(scores),
        "n": len(scores),
    }


def connective_density(text: str, total_tokens: int) -> dict[str, float]:
    if total_tokens == 0:
        return {"per_1000_tokens": 0.0, "count": 0, "by_marker": {}}
    text_lower = text.lower()
    by_marker = {}
    total = 0
    for marker in CONNECTIVES:
        # word-boundary match with possible trailing comma/period
        pattern = r"\b" + re.escape(marker) + r"\b"
        matches = re.findall(pattern, text_lower)
        if matches:
            by_marker[marker] = len(matches)
            total += len(matches)
    return {
        "count": total,
        "per_1000_tokens": (total / total_tokens) * 1000,
        "by_marker": by_marker,
    }


def function_word_fingerprint(words: list[str], top_n: int = 100) -> dict[str, Any]:
    if not words:
        return {"top_n": top_n, "frequencies": {}, "function_word_ratio": 0.0}
    counts = Counter(w for w in words if w in FUNCTION_WORDS)
    total = len(words)
    fw_total = sum(counts.values())
    top = dict(counts.most_common(top_n))
    return {
        "top_n": top_n,
        "frequencies": {w: c / total for w, c in top.items()},
        "function_word_ratio": fw_total / total if total else 0.0,
    }


# ---------- Tier 2 metrics (spaCy) ----------

def pos_bigram_distribution(text: str) -> dict[str, Any] | None:
    if not HAS_SPACY or _NLP is None:
        return None
    doc = _NLP(text)
    bigrams: Counter[str] = Counter()
    for sent in doc.sents:
        tags = [t.pos_ for t in sent if not t.is_space]
        for a, b in zip(tags, tags[1:]):
            bigrams[f"{a}-{b}"] += 1
    total = sum(bigrams.values())
    if total == 0:
        return None
    probs = {k: v / total for k, v in bigrams.items()}
    H = -sum(p * math.log2(p) for p in probs.values() if p > 0)
    return {
        "n_bigrams": total,
        "n_unique": len(bigrams),
        "entropy_bits": H,
        "top_20": dict(bigrams.most_common(20)),
        # Full counts so a baseline aggregator can sum them and a
        # downstream helper can compute KL/JSD against the aggregate.
        # Bounded by the POS tag inventory (typically 17 universal
        # tags = 289 possible bigrams), so the dict stays small.
        "counts": dict(bigrams),
    }


def pos_bigram_distance(
    target_counts: dict[str, int],
    baseline_counts: dict[str, int],
) -> dict[str, Any] | None:
    """KL divergence and Jensen-Shannon divergence between two POS-bigram
    count distributions.

    Returns ``None`` if either distribution is empty. KL is computed as
    ``KL(target ‖ baseline)`` with Laplace smoothing on the union of
    bigrams seen in either distribution to handle zeros. JSD is
    symmetric and bounded in ``[0, 1]`` (using log base 2). The
    distributional-diagnostics reference describes these as the
    canonical Layer A POS-bigram comparison; the helper makes the
    numbers reportable when a baseline is supplied.
    """
    if not target_counts or not baseline_counts:
        return None
    keys = set(target_counts) | set(baseline_counts)
    n_t = sum(target_counts.values())
    n_b = sum(baseline_counts.values())
    if n_t == 0 or n_b == 0:
        return None
    # Laplace smoothing: add one count per key to both distributions
    # before normalizing. Prevents log(0) on bigrams missing from one
    # side of the comparison without distorting the larger structure
    # because the smoothing mass is small relative to typical counts.
    smoothed_t = {k: target_counts.get(k, 0) + 1 for k in keys}
    smoothed_b = {k: baseline_counts.get(k, 0) + 1 for k in keys}
    total_t = sum(smoothed_t.values())
    total_b = sum(smoothed_b.values())
    p = {k: v / total_t for k, v in smoothed_t.items()}
    q = {k: v / total_b for k, v in smoothed_b.items()}
    kl = sum(
        p[k] * math.log2(p[k] / q[k])
        for k in keys
        if p[k] > 0 and q[k] > 0
    )
    m = {k: 0.5 * (p[k] + q[k]) for k in keys}
    jsd = 0.5 * sum(
        p[k] * math.log2(p[k] / m[k])
        for k in keys if p[k] > 0 and m[k] > 0
    ) + 0.5 * sum(
        q[k] * math.log2(q[k] / m[k])
        for k in keys if q[k] > 0 and m[k] > 0
    )
    return {
        "kl_to_baseline": round(kl, 4),
        "jsd_to_baseline": round(jsd, 4),
        "n_target_bigrams": n_t,
        "n_baseline_bigrams": n_b,
        "n_unique_union": len(keys),
    }


def normalize_pos_bigram_counts(
    counts: dict[str, int],
    keys: set[str] | None = None,
    *,
    alpha: float = 0.0,
) -> dict[str, float]:
    """Normalize POS-bigram counts to probabilities.

    Optional Laplace add-α smoothing over ``keys`` (if provided) or the
    distribution's own keys. ``alpha=0`` returns raw normalized
    frequencies; ``alpha=1.0`` matches ``pos_bigram_distance``'s
    Laplace smoothing convention. Returns an empty dict if the
    smoothed total is zero.
    """
    base = keys if keys is not None else set(counts.keys())
    if alpha > 0:
        smoothed: dict[str, float] = {k: counts.get(k, 0) + alpha for k in base}
    else:
        smoothed = {k: float(counts.get(k, 0)) for k in base}
    total = sum(smoothed.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in smoothed.items()}


def pos_bigram_kl_contributions(
    target_probs: dict[str, float],
    baseline_probs: dict[str, float],
    *,
    target_counts: dict[str, int] | None = None,
    baseline_counts: dict[str, int] | None = None,
    eps: float = 1e-9,
    min_count: int = 1,
) -> list[dict[str, Any]]:
    """Decompose POS-bigram KL into per-bigram contributions.

    For each bigram ``b`` in the union of seen bigrams (after the
    optional ``min_count`` filter), returns a row carrying the bigram
    string, raw counts (when supplied), smoothed probabilities, the
    delta and log2 ratio of those probabilities, and the per-bigram KL
    contribution ``p * log2(p / q)``. Rows are sorted by ``abs(kl_contrib)``
    descending so the largest-magnitude contributors appear first.

    Smoothing is applied as add-``eps`` to each probability followed by
    renormalization, so ``log2(p/q)`` is always defined. Pre-smooth at
    the count level via ``normalize_pos_bigram_counts(alpha=...)`` if
    Laplace add-α smoothing is preferred (the convention used by
    ``pos_bigram_distance``).

    ``min_count`` filters out bigrams where neither corpus reaches the
    count threshold. Suppresses sampling noise from rare bigrams. Has
    no effect if ``target_counts`` and ``baseline_counts`` are not
    supplied.
    """
    keys = set(target_probs) | set(baseline_probs)
    if min_count > 1 and target_counts is not None and baseline_counts is not None:
        keys = {
            k for k in keys
            if max(target_counts.get(k, 0), baseline_counts.get(k, 0)) >= min_count
        }
    if not keys:
        return []
    p_smoothed = {k: target_probs.get(k, 0.0) + eps for k in keys}
    q_smoothed = {k: baseline_probs.get(k, 0.0) + eps for k in keys}
    p_total = sum(p_smoothed.values())
    q_total = sum(q_smoothed.values())
    rows: list[dict[str, Any]] = []
    for k in keys:
        p = p_smoothed[k] / p_total
        q = q_smoothed[k] / q_total
        log2_ratio = math.log2(p / q)
        row: dict[str, Any] = {
            "bigram": k,
            "target_prob": p,
            "baseline_prob": q,
            "delta": p - q,
            "log2_ratio": log2_ratio,
            "kl_contrib": p * log2_ratio,
        }
        if target_counts is not None:
            row["target_count"] = target_counts.get(k, 0)
        if baseline_counts is not None:
            row["baseline_count"] = baseline_counts.get(k, 0)
        rows.append(row)
    rows.sort(key=lambda r: abs(r["kl_contrib"]), reverse=True)
    return rows


def mdd_stats(text: str) -> dict[str, Any] | None:
    if not HAS_SPACY or _NLP is None:
        return None
    doc = _NLP(text)
    per_sentence = []
    for sent in doc.sents:
        toks = [t for t in sent if not t.is_space]
        if len(toks) < 2:
            continue
        distances = []
        for t in toks:
            if t.dep_ == "ROOT" or t.head is t:
                continue
            distances.append(abs(t.i - t.head.i))
        if distances:
            per_sentence.append(sum(distances) / len(distances))
    if len(per_sentence) < 2:
        return {
            "n_sentences": len(per_sentence),
            "mean": per_sentence[0] if per_sentence else 0.0,
            "sd": 0.0,
        }
    return {
        "n_sentences": len(per_sentence),
        "mean": statistics.mean(per_sentence),
        "sd": statistics.stdev(per_sentence),
    }


# ---------- Tier 3 metrics (embeddings) ----------

def _get_st_model():
    global _ST_MODEL
    if _ST_MODEL is not None:
        return _ST_MODEL
    if not HAS_ST:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _ST_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        return _ST_MODEL
    except Exception:
        return None


def adjacent_sentence_cosine(sentences: list[str]) -> dict[str, Any] | None:
    if len(sentences) < 2:
        return None

    model = _get_st_model() if HAS_ST else None
    if model is not None:
        try:
            import numpy as np  # type: ignore
            embeddings = model.encode(sentences, show_progress_bar=False)
            sims = []
            for i in range(len(embeddings) - 1):
                a = embeddings[i]
                b = embeddings[i + 1]
                denom = (np.linalg.norm(a) * np.linalg.norm(b))
                if denom == 0:
                    continue
                sims.append(float(np.dot(a, b) / denom))
            if not sims:
                return None
            return {
                "method": "sentence-transformers (all-MiniLM-L6-v2)",
                "n_pairs": len(sims),
                "mean": statistics.mean(sims),
                "sd": statistics.stdev(sims) if len(sims) > 1 else 0.0,
                "min": min(sims),
                "max": max(sims),
            }
        except Exception:
            pass

    if HAS_SKLEARN:
        try:
            vec = TfidfVectorizer().fit_transform(sentences)
            sims = []
            for i in range(vec.shape[0] - 1):
                s = float(cosine_similarity(vec[i], vec[i + 1])[0][0])
                sims.append(s)
            if not sims:
                return None
            return {
                "method": "tfidf-cosine",
                "n_pairs": len(sims),
                "mean": statistics.mean(sims),
                "sd": statistics.stdev(sims) if len(sims) > 1 else 0.0,
                "min": min(sims),
                "max": max(sims),
            }
        except Exception:
            pass

    return None


# ---------- Tier 4 (surprisal) helper, v1.47.0+ (C.4) ----------
#
# Reuses the `audit_surprisal` math from the C.3 standalone module so
# the standalone CLI and the variance-audit Tier 4 path share the
# same numbers. The lazy import means an operator who doesn't run
# Tier 4 never pays the surprisal_audit / surprisal_backend import
# cost. Operators who run Tier 4 without `transformers` installed
# get an available=False block with a clear reason rather than a
# crash — same posture as Tier 3 when sentence_transformers / sklearn
# are missing.

def _tier4_surprisal_block(
    text: str,
    *,
    score_fn=None,
    backend=None,
    sliding_window: bool = False,
    window_size: int = 200,
    stride: int = 100,
    top_k: int = 20,
) -> dict[str, Any]:
    """Compute Tier 4 surprisal statistics for ``text``.

    Returns a dict whose ``surprisal`` sub-block carries the same
    statistics the C.3 ``surprisal_audit`` script produces — mean,
    sd, variance, min/max, autocorrelation at lags 1/2/3/5/10,
    skew, excess kurtosis, position of max, top-k surprising
    tokens — plus a ``provisional`` marker so consumers see the
    band-calibration story (``user-baseline-required``) carries
    through to the variance-audit context. Shape is keyed by
    ``signal_path`` matching the new ``COMPRESSION_HEURISTICS``
    entries (``tier4.surprisal.mean``, ``tier4.surprisal.sd``,
    ``tier4.surprisal.autocorrelation.lag_1``).

    Either ``score_fn`` or ``backend`` may be supplied; defaults to
    constructing a ``SurprisalBackend`` with the module's
    ``DEFAULT_MODEL`` (TinyLlama). The test suite passes
    ``score_fn`` so no real causal LM is loaded.

    Returns ``{"available": False, "reason": ...}`` for any of:
      * transformers / surprisal_backend unimportable
      * empty or whitespace text
      * surprisal series empty (input too short)

    The band classifier downstream uses
    ``COMPRESSION_HEURISTICS`` entries which carry
    ``provisional=True`` — Tier 4 contributes to the band call
    only when the operator has explicitly opted in via
    ``--tier4`` AND has either calibrated the thresholds or
    accepted the PROVISIONAL ones documented in the C.3 SPEC.
    """
    if not text or not text.strip():
        return {"available": False, "reason": "empty text"}
    try:
        # Lazy import: avoids surprisal_audit / surprisal_backend
        # import cost when Tier 4 is off (the common case).
        from surprisal_audit import audit_surprisal  # type: ignore
    except ImportError as exc:
        return {
            "available": False,
            "reason": (
                f"surprisal_audit unimportable: {exc}. Install the "
                f"Tier-4 dependencies (transformers + torch) and "
                f"ensure surprisal_audit.py is on the path."
            ),
        }
    if score_fn is None and backend is None:
        try:
            # Construct a default backend lazily. Same lazy-load
            # semantics as the audit script's CLI path.
            from surprisal_backend import (  # type: ignore
                DEFAULT_MODEL, SurprisalBackend,
            )
            backend = SurprisalBackend(model_id=DEFAULT_MODEL)
        except ImportError as exc:
            return {
                "available": False,
                "reason": (
                    f"surprisal_backend unimportable: {exc}. Tier-4 "
                    f"requires transformers + torch."
                ),
            }
    try:
        sub = audit_surprisal(
            text,
            backend=backend,
            score_fn=score_fn,
            sliding_window=sliding_window,
            window_size=window_size,
            stride=stride,
            top_k=top_k,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "reason": (
                f"surprisal scoring failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    if not sub.get("available"):
        return {
            "available": False,
            "reason": sub.get("reason", "surprisal audit unavailable"),
        }
    # Reshape the audit dict so the COMPRESSION_HEURISTICS
    # signal_path entries (tier4.surprisal.mean, etc.) can be
    # walked directly via _extract_signal. The standalone audit's
    # `summary` key is the new tier4.surprisal block.
    summary = sub["summary"]
    tier4_block: dict[str, Any] = {
        "available": True,
        "surprisal": {
            "mean": summary["mean_surprisal_bits"],
            "sd": summary["sd_surprisal_bits"],
            "variance": summary["variance_surprisal_bits"],
            "min": summary["min"],
            "max": summary["max"],
            "autocorrelation": dict(summary["autocorrelation"]),
            "skew": summary.get("skew"),
            "excess_kurtosis": summary.get("excess_kurtosis"),
            "position_of_max": summary.get("position_of_max"),
            "series_too_short_for_acf": summary.get(
                "series_too_short_for_acf", False,
            ),
            "n_tokens_scored": sub.get("n_tokens_scored"),
            "series_length": sub.get("series_length"),
            "top_k_tokens": sub.get("top_k_tokens", []),
            "band": sub.get("band"),
            "sliding_window": sub.get("sliding_window"),
            "provisional": True,
            "calibration_anchor": "user-baseline-required",
        },
    }
    # Reviewer P2 (2026-05-14): attach the backend identifier so
    # the variance Tier 4 JSON carries the model + revision +
    # alias the standalone audit's PROVENANCE block already
    # records. Surprisal means/SD/ACF are tokenizer- and
    # checkpoint-dependent, so two variance runs against
    # different --surprisal-model values must be distinguishable
    # in their output. When score_fn was supplied (the test path),
    # there is no backend identifier; tier4_block["backend"] stays
    # absent and downstream consumers see that this run was
    # stub-driven.
    if backend is not None and hasattr(backend, "identifier_block"):
        try:
            tier4_block["backend"] = backend.identifier_block()
        except Exception:  # noqa: BLE001 — defensive
            pass
    return tier4_block


# ---------- AIC-7 / AIC-8 / AIC-9 named-pattern integration ----------
#
# v1.65.0: opt-in `--aic7` / `--aic8` / `--aic9` flags wire the named-
# pattern detectors (`aic_pattern_audit.py`, `image_conjunction.py`
# + `prestige_metaphor.py`, `kicker_density.py`) into the audit
# dict. Closes spec Step 10 from `SPEC_aic_8_9_implementation.md`:
# COMPRESSION_HEURISTICS gets corresponding entries below, and
# `_ABLATION_SIGNAL_FAMILIES` gets three new families
# (assistant_register_intrusion / closure_inflation /
# aesthetic_authority_laundering).
#
# Same opt-in posture as `--tier4`: lazy import inside the block
# helpers so callers that don't enable the flags never pay the
# import cost.


def _aic7_named_pattern_block(text: str) -> dict[str, Any]:
    """Run `aic_pattern_audit.all_patterns()` and reshape its output
    to the `patterns.<key>.density_per_1k` shape that
    COMPRESSION_HEURISTICS reads.

    Returns ``{"available": True, "patterns": {...}, "diagnostics":
    {...}}`` on success. Returns ``{"available": False, "reason":
    ...}`` if the detector is unimportable (extremely unlikely
    since aic_pattern_audit is regex-only) or the text is too
    short for meaningful pattern density.
    """
    if not text or not text.strip():
        return {"available": False, "reason": "empty text"}
    try:
        import aic_pattern_audit as ap  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        return {
            "available": False,
            "reason": (
                f"aic_pattern_audit unimportable: {exc}. Ensure "
                f"aic_pattern_audit.py is on the path."
            ),
        }
    sentences = split_sentences(text)
    words = split_words(text)
    n_words = max(len(words), 1)
    results = ap.all_patterns(text, sentences)
    pattern_blocks: dict[str, dict[str, Any]] = {}
    for key, result in results.items():
        count = len(result.hits)
        density_per_1k = (count / n_words) * 1000.0
        pattern_blocks[key] = {
            "label": result.label,
            "count": count,
            "density_per_1k": density_per_1k,
            "severity_note": result.severity_note,
        }
    return {
        "available": True,
        "patterns": pattern_blocks,
        "diagnostics": {
            "n_words": n_words,
            "n_sentences": len(sentences),
            "pattern_keys": sorted(pattern_blocks.keys()),
        },
    }


def _aic8_image_prestige_block(text: str) -> dict[str, Any]:
    """Run `image_conjunction_density()` and
    `prestige_metaphor_density()` and reshape their output to the
    `aic_8_9.image_conjunction_density.*` and
    `aic_8_9.prestige_metaphor_density.*` shapes that
    COMPRESSION_HEURISTICS reads.

    Heavier dependency chain than AIC-7: requires spaCy with parsing
    (`en_core_web_sm`) AND a vectors-bearing model
    (`en_core_web_md` or `_lg`). Returns
    ``{"available": False, "reason": ...}`` on missing dependencies.
    """
    if not text or not text.strip():
        return {"available": False, "reason": "empty text"}
    try:
        import image_conjunction as ic  # type: ignore  # noqa: PLC0415
        import prestige_metaphor as pm  # type: ignore  # noqa: PLC0415
        import embeddings as emb  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        return {
            "available": False,
            "reason": f"AIC-8 modules unimportable: {exc}",
        }
    try:
        nlp = ic._load_spacy_with_parsing()
    except emb.EmbeddingsBackendError as exc:
        return {
            "available": False,
            "reason": str(exc),
        }
    try:
        pm_block = pm.prestige_metaphor_density(text, nlp=nlp)
    except emb.EmbeddingsBackendError as exc:
        return {
            "available": False,
            "reason": str(exc),
        }
    # The prestige-metaphor block embeds the full image-conjunction
    # detection. Surface both subtypes at the spec-registered
    # signal_path locations so _extract_signal can walk them.
    total_tokens = pm_block["diagnostics"].get("total_tokens", 0)
    n_conjs = pm_block["diagnostics"].get("conjunction_count", 0)
    ic_density_per_1k = (
        (n_conjs / total_tokens) * 1000.0 if total_tokens > 0 else 0.0
    )
    return {
        "available": True,
        "image_conjunction_density": {
            "value": ic_density_per_1k,
            "conjunction_count": n_conjs,
            "total_tokens": total_tokens,
        },
        "prestige_metaphor_density": {
            "value": pm_block.get("value", 0.0),
            "domain_scatter_entropy": pm_block.get(
                "domain_scatter_entropy", 0.0
            ),
            "domain_distribution": pm_block.get("domain_distribution", {}),
            "flag_fires": pm_block.get("flag_fires", False),
        },
        "diagnostics": {
            "total_tokens": total_tokens,
            "conjunction_count": n_conjs,
            "n_distinct_domains": pm_block["diagnostics"].get(
                "n_distinct_domains", 0
            ),
        },
    }


def _aic9_kicker_block(text: str) -> dict[str, Any]:
    """Run `kicker_density.kicker_density()` and reshape to the
    `aic_8_9.kicker_density.value` shape that COMPRESSION_HEURISTICS
    reads.

    Lightest dependency chain: prefers `en_core_web_sm` for the
    proper-noun PROPN/NER check but falls back to a regex
    heuristic when spaCy isn't installed, so this never fails
    hard on missing dependencies.
    """
    if not text or not text.strip():
        return {"available": False, "reason": "empty text"}
    try:
        import kicker_density as kd  # type: ignore  # noqa: PLC0415
    except ImportError as exc:
        return {
            "available": False,
            "reason": f"kicker_density unimportable: {exc}",
        }
    # spaCy is preferred but optional. Pass nlp=None when sm isn't
    # installed; the detector's regex fallback handles it.
    nlp = None
    try:
        import spacy  # type: ignore  # noqa: PLC0415
        nlp = spacy.load("en_core_web_sm")
    except (ImportError, OSError):
        pass
    block = kd.kicker_density(text, nlp=nlp)
    return {
        "available": True,
        "kicker_density": {
            "value": block.get("value", 0.0),
            "spacing_variance": block.get("spacing_variance", 0.0),
            "paragraph_count": block.get("diagnostics", {}).get(
                "total_paragraphs", 0
            ),
            "kicker_count": block.get("diagnostics", {}).get(
                "kicker_count", 0
            ),
        },
    }


# ---------- Aggregator ----------

def audit_text(
    text: str,
    mattr_window: int = 50,
    do_tier2: bool = True,
    do_tier3: bool = True,
    do_tier4: bool = False,
    tier4_score_fn=None,
    tier4_backend=None,
    do_aic7: bool = False,
    do_aic8: bool = False,
    do_aic9: bool = False,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
    collect_stripped: bool = False,
) -> dict[str, Any]:
    original_text = text
    text, preprocessing = strip_non_prose(
        original_text,
        strip_rules,
        allow_non_prose=allow_non_prose,
        strip_aggressive=strip_aggressive,
        collect_stripped=collect_stripped,
    )
    sentences = split_sentences(text)
    words = split_words(text)
    n_words = len(words)
    n_sentences = len(sentences)

    out: dict[str, Any] = {
        "preprocessing": preprocessing,
        "summary": {
            "n_words": n_words,
            "n_words_original": preprocessing.get("input_tokens_before", n_words),
            "n_sentences": n_sentences,
            "reliable": n_words >= 200,
            "preprocessing_applied": preprocessing.get("applied", False),
        },
        "tier1": {},
    }

    if n_words < 50:
        out["warning"] = "Text below 50 words; Layer A statistics are not meaningful."
        return out

    out["tier1"]["sentence_length"] = sentence_length_stats(sentences)
    out["tier1"]["mattr"] = {"window": mattr_window, "value": mattr(words, mattr_window)}
    out["tier1"]["mtld"] = mtld(words)
    out["tier1"]["yules_k"] = yules_k(words)
    out["tier1"]["shannon_entropy_bits"] = shannon_entropy(words)
    out["tier1"]["fkgl"] = fkgl_stats(sentences)
    out["tier1"]["connective_density"] = connective_density(text, n_words)
    out["tier1"]["function_words"] = function_word_fingerprint(words)

    if do_tier2:
        out["tier2"] = {
            "available": HAS_SPACY,
            "pos_bigrams": pos_bigram_distribution(text) if HAS_SPACY else None,
            "mdd": mdd_stats(text) if HAS_SPACY else None,
        }
    if do_tier3:
        out["tier3"] = {
            "available": HAS_ST or HAS_SKLEARN,
            "adjacent_cosine": adjacent_sentence_cosine(sentences) if (HAS_ST or HAS_SKLEARN) else None,
        }
    if do_tier4:
        # v1.47.0+ (C.4): Tier 4 (surprisal). Opt-in; lazy import
        # of surprisal_audit + surprisal_backend so callers that
        # don't enable Tier 4 never pay the import cost. When
        # tier4_score_fn is supplied (the test path), no real
        # causal LM is loaded.
        out["tier4"] = _tier4_surprisal_block(
            text,
            score_fn=tier4_score_fn,
            backend=tier4_backend,
        )
    # v1.65.0: AIC-7 / AIC-8 / AIC-9 named-pattern integration.
    # Each is opt-in via its own flag. The three families have
    # very different dependency profiles:
    #   AIC-7: regex-only; always available when aic_pattern_audit
    #     imports.
    #   AIC-8: requires spaCy with parsing + a vectors-bearing
    #     model (en_core_web_md or _lg); falls back to
    #     `available: False` with install hint otherwise.
    #   AIC-9: regex + optional spaCy POS check; falls back to
    #     regex when spaCy isn't installed.
    # Each block emits at the signal_path location that
    # COMPRESSION_HEURISTICS registers, so _extract_signal walks
    # them automatically and the ablation families measure their
    # load-bearing contribution.
    if do_aic7:
        out["patterns"] = _aic7_named_pattern_block(text).get(
            "patterns", {}
        )
        # Keep the diagnostic / availability metadata under a
        # parallel key so classify_compression can still walk
        # patterns.<key>.density_per_1k cleanly.
        aic7_full = _aic7_named_pattern_block(text)
        out["aic7_diagnostics"] = {
            "available": aic7_full.get("available", False),
            "reason": aic7_full.get("reason"),
            **(aic7_full.get("diagnostics") or {}),
        }
    if do_aic8:
        out.setdefault("aic_8_9", {})
        aic8 = _aic8_image_prestige_block(text)
        if aic8.get("available"):
            out["aic_8_9"]["image_conjunction_density"] = (
                aic8["image_conjunction_density"]
            )
            out["aic_8_9"]["prestige_metaphor_density"] = (
                aic8["prestige_metaphor_density"]
            )
            out["aic_8_9"].setdefault("diagnostics", {}).update(
                aic8.get("diagnostics") or {}
            )
        else:
            out["aic_8_9"].setdefault("diagnostics", {})[
                "aic8_unavailable_reason"
            ] = aic8.get("reason")
    if do_aic9:
        out.setdefault("aic_8_9", {})
        aic9 = _aic9_kicker_block(text)
        if aic9.get("available"):
            out["aic_8_9"]["kicker_density"] = aic9["kicker_density"]
        else:
            out["aic_8_9"].setdefault("diagnostics", {})[
                "aic9_unavailable_reason"
            ] = aic9.get("reason")
    return out


def audit_baseline(baseline_dir: str, **kwargs: Any) -> dict[str, Any]:
    paths = sorted(Path(baseline_dir).glob("*.txt")) + sorted(Path(baseline_dir).glob("*.md"))
    paths = [p for p in paths if not p.name.lower().startswith("readme")]
    audits = []
    preprocessing_by_file: dict[str, dict[str, Any]] = {}
    for p in paths:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
            a = audit_text(txt, **kwargs)
            preprocessing_by_file[p.name] = a.get("preprocessing", {})
            audits.append({"file": str(p.name), "audit": a})
        except Exception as e:
            print(f"Warning: failed baseline file {p}: {e}", file=sys.stderr)
            audits.append({"file": str(p.name), "error": str(e)})
    rules_active: list[str] = []
    applied = True
    opt_out = False
    if preprocessing_by_file:
        first = next(iter(preprocessing_by_file.values()))
        rules_active = list(first.get("rules_active") or [])
        applied = bool(first.get("applied", True))
        opt_out = bool(first.get("opt_out", False))
    return {
        "n_files": len(paths),
        "audits": audits,
        "aggregate": _aggregate_baseline(audits),
        "pos_bigram_aggregate": _aggregate_pos_bigrams(audits),
        "preprocessing": aggregate_preprocessing_metadata(
            preprocessing_by_file,
            rules_active=rules_active,
            applied=applied,
            opt_out=opt_out,
        ),
    }


def _emit_preprocessing_warning(
    meta: dict[str, Any] | None,
    *,
    label: str,
    threshold: float,
) -> None:
    if not meta or not meta.get("applied", False):
        return
    ratio = meta.get("strip_ratio", 0.0)
    if not isinstance(ratio, (int, float)) or ratio <= threshold:
        return
    stripped = int(meta.get("tokens_stripped", 0) or 0)
    dominant = meta.get("dominant_rule") or "unknown"
    print(
        f"Warning: preprocessing stripped {stripped} tokens from {label} "
        f"({ratio:.1%}; dominant rule: {dominant}). "
        "See JSON preprocessing block for details.",
        file=sys.stderr,
    )


def _emit_baseline_preprocessing_warnings(
    baseline_meta: dict[str, Any] | None,
    *,
    threshold: float,
) -> None:
    if not baseline_meta or not baseline_meta.get("applied", False):
        return
    for name, meta in (baseline_meta.get("per_file") or {}).items():
        _emit_preprocessing_warning(
            meta,
            label=f"baseline file {name}",
            threshold=threshold,
        )


def _write_stripped_debug(
    meta: dict[str, Any] | None,
    destination: str | None,
) -> None:
    if not destination or not meta:
        return
    by_rule = meta.get("stripped_text_by_rule") or {}
    if not by_rule:
        message = "(No stripped text captured.)\n"
    else:
        chunks: list[str] = []
        for rule, snippets in by_rule.items():
            chunks.append(f"## {rule}")
            chunks.extend(str(s) for s in snippets)
            chunks.append("")
        message = "\n".join(chunks)
    if destination == "-":
        print(message, file=sys.stderr)
    else:
        Path(destination).write_text(message, encoding="utf-8")


def _aggregate_pos_bigrams(
    audits: list[dict[str, Any]],
) -> dict[str, int]:
    """Sum POS-bigram counts across baseline files.

    Empty if spaCy was unavailable for any baseline file. The result is
    consumed by ``compare_distributions`` to compute KL/JSD between a
    target document and the baseline corpus.
    """
    total: Counter[str] = Counter()
    for entry in audits:
        a = entry.get("audit", {})
        pb = (a.get("tier2") or {}).get("pos_bigrams") or {}
        counts = pb.get("counts")
        if isinstance(counts, dict):
            total.update(counts)
    return dict(total)


def _aggregate_baseline(audits: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-statistic mean and SD across baseline files."""
    keys: list[tuple[str, ...]] = [
        ("tier1", "sentence_length", "sd"),
        ("tier1", "sentence_length", "burstiness_B"),
        ("tier1", "mattr", "value"),
        ("tier1", "mtld"),
        ("tier1", "yules_k"),
        ("tier1", "shannon_entropy_bits"),
        ("tier1", "fkgl", "sd"),
        ("tier1", "fkgl", "mean"),
        ("tier1", "connective_density", "per_1000_tokens"),
        ("tier1", "function_words", "function_word_ratio"),
        ("tier2", "pos_bigrams", "entropy_bits"),
        ("tier2", "mdd", "sd"),
        ("tier3", "adjacent_cosine", "mean"),
        ("tier3", "adjacent_cosine", "sd"),
    ]
    agg: dict[str, dict[str, float]] = {}
    for key in keys:
        vals: list[float] = []
        for a in audits:
            d: Any = a.get("audit", {})
            for k in key:
                if not isinstance(d, dict):
                    d = None
                    break
                d = d.get(k)
                if d is None:
                    break
            if isinstance(d, (int, float)):
                vals.append(float(d))
        if not vals:
            continue
        name = ".".join(key)
        if len(vals) == 1:
            agg[name] = {"mean": vals[0], "sd": 0.0, "n": 1}
        else:
            agg[name] = {
                "mean": statistics.mean(vals),
                "sd": statistics.stdev(vals),
                "n": len(vals),
            }
    return agg


def _z_score(value: float, agg: dict[str, float]) -> float | None:
    if "mean" not in agg or "sd" not in agg or agg["sd"] == 0:
        return None
    return (value - agg["mean"]) / agg["sd"]


# Map dotted z-score path names to their heuristic key in
# COMPRESSION_HEURISTICS so length-floor warnings can be carried through
# from the band classification to the baseline z-score output. Signals
# without an entry in COMPRESSION_HEURISTICS (function_word_ratio,
# pos_bigrams.entropy_bits) have no length-floor convention; their
# z-scores are reported without a length-floor flag.
_BASELINE_PATH_TO_HEURISTIC: dict[str, str] = {
    "tier1.sentence_length.sd": "sentence_length_sd",
    "tier1.sentence_length.burstiness_B": "burstiness_B",
    "tier1.mattr.value": "mattr",
    "tier1.mtld": "mtld",
    "tier1.yules_k": "yules_k",
    "tier1.shannon_entropy_bits": "shannon_entropy",
    "tier1.fkgl.sd": "fkgl_sd",
    "tier1.connective_density.per_1000_tokens": "connective_density",
    "tier2.mdd.sd": "mdd_sd",
    "tier3.adjacent_cosine.mean": "adjacent_cosine_mean",
    "tier3.adjacent_cosine.sd": "adjacent_cosine_sd",
}


_SIGNAL_PATHS: list[tuple[str, tuple[str, ...]]] = [
    ("tier1.sentence_length.sd", ("tier1", "sentence_length", "sd")),
    ("tier1.sentence_length.burstiness_B", ("tier1", "sentence_length", "burstiness_B")),
    ("tier1.mattr.value", ("tier1", "mattr", "value")),
    ("tier1.mtld", ("tier1", "mtld")),
    ("tier1.yules_k", ("tier1", "yules_k")),
    ("tier1.shannon_entropy_bits", ("tier1", "shannon_entropy_bits")),
    ("tier1.fkgl.sd", ("tier1", "fkgl", "sd")),
    ("tier1.connective_density.per_1000_tokens",
     ("tier1", "connective_density", "per_1000_tokens")),
    ("tier1.function_words.function_word_ratio",
     ("tier1", "function_words", "function_word_ratio")),
    ("tier2.pos_bigrams.entropy_bits", ("tier2", "pos_bigrams", "entropy_bits")),
    ("tier2.mdd.sd", ("tier2", "mdd", "sd")),
    ("tier3.adjacent_cosine.mean", ("tier3", "adjacent_cosine", "mean")),
    ("tier3.adjacent_cosine.sd", ("tier3", "adjacent_cosine", "sd")),
]


def _extract_signal(audit: dict[str, Any], key_path: tuple[str, ...]) -> float | None:
    """Walk a tuple key-path through an audit dict; return the scalar at
    the end or None if any intermediate key is missing or non-dict."""
    d: Any = audit
    for k in key_path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
        if d is None:
            return None
    if isinstance(d, (int, float)):
        return float(d)
    return None


def compare_to_baseline(audit: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    agg = baseline.get("aggregate", {})
    z_scores: dict[str, Any] = {}
    n_words = audit.get("summary", {}).get("n_words", 0)
    paths = [
        ("tier1.sentence_length.sd", ("tier1", "sentence_length", "sd")),
        ("tier1.sentence_length.burstiness_B", ("tier1", "sentence_length", "burstiness_B")),
        ("tier1.mattr.value", ("tier1", "mattr", "value")),
        ("tier1.mtld", ("tier1", "mtld")),
        ("tier1.yules_k", ("tier1", "yules_k")),
        ("tier1.shannon_entropy_bits", ("tier1", "shannon_entropy_bits")),
        ("tier1.fkgl.sd", ("tier1", "fkgl", "sd")),
        ("tier1.connective_density.per_1000_tokens",
         ("tier1", "connective_density", "per_1000_tokens")),
        ("tier1.function_words.function_word_ratio",
         ("tier1", "function_words", "function_word_ratio")),
        ("tier2.pos_bigrams.entropy_bits", ("tier2", "pos_bigrams", "entropy_bits")),
        ("tier2.mdd.sd", ("tier2", "mdd", "sd")),
        ("tier3.adjacent_cosine.mean", ("tier3", "adjacent_cosine", "mean")),
        ("tier3.adjacent_cosine.sd", ("tier3", "adjacent_cosine", "sd")),
    ]
    for name, key in paths:
        if name not in agg:
            continue
        d: Any = audit
        for k in key:
            if not isinstance(d, dict):
                d = None
                break
            d = d.get(k)
            if d is None:
                break
        if isinstance(d, (int, float)):
            z = _z_score(float(d), agg[name])
            entry: dict[str, Any] = {
                "value": float(d),
                "baseline_mean": agg[name]["mean"],
                "baseline_sd": agg[name]["sd"],
                "z_score": z,
            }
            heuristic_key = _BASELINE_PATH_TO_HEURISTIC.get(name)
            if heuristic_key and heuristic_key in COMPRESSION_HEURISTICS:
                length_floor = COMPRESSION_HEURISTICS[heuristic_key].length_floor
                entry["length_floor"] = length_floor
                entry["length_floor_satisfied"] = n_words >= length_floor
                if n_words < length_floor:
                    entry["warning"] = (
                        f"Target has {n_words} words, below the "
                        f"{length_floor}-word floor for {heuristic_key}. "
                        "Z-score is reported but should be treated as "
                        "noisy and not used for band-relevant inference."
                    )
            z_scores[name] = entry
    return z_scores


def bootstrap_compare(
    audit: dict[str, Any],
    baseline_dir: str,
    *,
    n_windows_per_file: int = 50,
    max_total_windows: int = 500,
    n_resamples: int = 9999,
    confidence_level: float = 0.95,
    seed: int | None = None,
    do_tier2: bool = True,
    do_tier3: bool = True,
    mattr_window: int = 50,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
) -> dict[str, Any]:
    """Length-matched bootstrap of every Layer A signal against the
    baseline corpus. Returns a dict keyed by dotted signal path with the
    target value, the empirical baseline distribution at the target's
    length, the target's percentile in that distribution, and a
    bootstrap confidence interval on the percentile.

    Phase 1 step 3 of the validation spine. Replaces noisy z-scores at
    small N: at length N the baseline file's mean and SD across full
    files (often much longer than N) over- or under-estimate the
    expected statistic value at that length. The empirical
    length-matched distribution is the right comparison.

    Re-reads the baseline files because ``audit_baseline()`` returns
    aggregates rather than raw texts. Honors the same Tier flags as the
    main audit so the bootstrap measures the statistic the user
    actually computes.
    """
    try:
        from length_bootstrap import (  # type: ignore
            length_matched_bootstrap, HAS_SCIPY,
        )
    except ImportError:
        return {
            "available": False,
            "reason": "length_bootstrap module not importable",
        }
    if not HAS_SCIPY:
        return {
            "available": False,
            "reason": "scipy not installed; bootstrap CIs unavailable",
        }

    target_n_words = int(audit.get("summary", {}).get("n_words", 0))
    if target_n_words <= 0:
        return {
            "available": False,
            "reason": "target has zero words",
        }

    paths = (
        sorted(Path(baseline_dir).glob("*.txt"))
        + sorted(Path(baseline_dir).glob("*.md"))
    )
    paths = [p for p in paths if not p.name.lower().startswith("readme")]
    baseline_texts: list[str] = []
    baseline_files_loaded: list[str] = []
    baseline_files_skipped: list[dict[str, str]] = []
    for p in paths:
        try:
            baseline_texts.append(p.read_text(encoding="utf-8", errors="ignore"))
            baseline_files_loaded.append(p.name)
        except OSError as exc:
            baseline_files_skipped.append({"file": p.name, "reason": str(exc)})

    if not baseline_texts:
        return {
            "available": False,
            "reason": "no readable baseline files",
        }

    audit_kwargs = dict(
        do_tier2=do_tier2,
        do_tier3=do_tier3,
        mattr_window=mattr_window,
        allow_non_prose=allow_non_prose,
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
    )

    per_signal: dict[str, dict[str, Any]] = {}
    for name, key_path in _SIGNAL_PATHS:
        target_value = _extract_signal(audit, key_path)
        if target_value is None:
            continue

        # The statistic_fn closure runs audit_text on a window slice and
        # extracts the per-signal scalar. Captures key_path by default
        # arg to avoid late-binding gotchas in the loop.
        def _stat(text: str, _key_path: tuple[str, ...] = key_path) -> float | None:
            try:
                window_audit = audit_text(text, **audit_kwargs)
                return _extract_signal(window_audit, _key_path)
            except Exception:
                return None

        result = length_matched_bootstrap(
            baseline_texts,
            statistic_fn=_stat,
            target_value=target_value,
            target_n_words=target_n_words,
            n_windows_per_file=n_windows_per_file,
            max_total_windows=max_total_windows,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=seed,
        )
        per_signal[name] = result

    return {
        "available": True,
        "target_n_words": target_n_words,
        "n_baseline_files": len(baseline_files_loaded),
        "baseline_files_loaded": baseline_files_loaded,
        "baseline_files_skipped": baseline_files_skipped,
        "n_windows_per_file": n_windows_per_file,
        "max_total_windows": max_total_windows,
        "n_resamples": n_resamples,
        "confidence_level": confidence_level,
        "seed": seed,
        "per_signal": per_signal,
    }


def split_into_windows(
    text: str,
    window_size: int,
    stride: int | None = None,
) -> list[dict[str, Any]]:
    """Slice ``text`` into overlapping word-count-bounded windows.

    Each window slices the *original* text at word boundaries, so
    paragraph breaks, punctuation, and quoted spans inside the window
    are preserved (rather than reconstructed from a whitespace-split).
    The windows are then run through ``audit_text`` the same way the
    whole-document pass would be run.

    AI contamination often arrives as patches rather than whole-chapter
    drift; whole-chapter scores can mask localized problems where the
    compressed region averages out against clean prose. The sliding
    window catches the patches.

    If ``stride`` is None or zero, defaults to ``window_size`` (non-
    overlapping windows). Pass ``stride = window_size // 2`` for the
    typical 50%-overlap scan.
    """
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    if stride is None or stride <= 0:
        stride = window_size
    offsets = [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]
    n_words = len(offsets)
    if n_words == 0:
        return []
    if n_words <= window_size:
        return [{
            "start_word": 0,
            "end_word": n_words,
            "char_start": offsets[0][0],
            "char_end": offsets[-1][1],
            "text": text[offsets[0][0]:offsets[-1][1]],
        }]
    windows: list[dict[str, Any]] = []
    seen_ends: set[int] = set()
    for start in range(0, n_words - window_size + 1, stride):
        end = start + window_size
        if end in seen_ends:
            continue
        seen_ends.add(end)
        char_start = offsets[start][0]
        char_end = offsets[end - 1][1]
        windows.append({
            "start_word": start,
            "end_word": end,
            "char_start": char_start,
            "char_end": char_end,
            "text": text[char_start:char_end],
        })
    # Ensure the final window covers the document tail; the strided
    # loop may stop short of the end if (n_words - window_size) is not
    # divisible by stride.
    if windows[-1]["end_word"] < n_words:
        last_start = n_words - window_size
        windows.append({
            "start_word": last_start,
            "end_word": n_words,
            "char_start": offsets[last_start][0],
            "char_end": offsets[n_words - 1][1],
            "text": text[offsets[last_start][0]:offsets[n_words - 1][1]],
        })
    return windows


def audit_windows(
    text: str,
    window_size: int,
    *,
    stride: int | None = None,
    baseline: dict[str, Any] | None = None,
    do_tier2: bool = True,
    do_tier3: bool = True,
    mattr_window: int = 50,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
) -> list[dict[str, Any]]:
    """Run ``audit_text`` + ``classify_compression`` on each sliding window.

    When a ``baseline`` block (the result of ``audit_baseline``) is
    supplied, also runs ``compare_to_baseline`` and
    ``compare_distributions`` per window so each window carries its
    own z-scores and POS-bigram divergence against the same baseline
    aggregate the whole-document pass would use. Z-scores at small
    window sizes are noisy by construction; the length-floor warnings
    in ``compare_to_baseline`` flag them. The roadmap pairs this mode
    with length-matched bootstrap percentiles, which would replace the
    z-score noise with empirical confidence intervals; until then,
    read window z-scores as inspection leads, not verdicts.
    """
    windows = split_into_windows(text, window_size, stride)
    results: list[dict[str, Any]] = []
    for w in windows:
        a = audit_text(
            w["text"],
            do_tier2=do_tier2,
            do_tier3=do_tier3,
            mattr_window=mattr_window,
            allow_non_prose=allow_non_prose,
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
        )
        divergences: dict[str, Any] | None = None
        if baseline is not None:
            divergences = compare_distributions(a, baseline)
        c = classify_compression(a, divergences=divergences)
        entry = {
            "start_word": w["start_word"],
            "end_word": w["end_word"],
            "char_start": w["char_start"],
            "char_end": w["char_end"],
            "n_words": a.get("summary", {}).get("n_words", 0),
            "audit": a,
            "compression": c,
        }
        if baseline is not None:
            entry["baseline_comparison"] = compare_to_baseline(a, baseline)
            if divergences:
                entry["baseline_divergences"] = divergences
        results.append(entry)
    return results


def compare_distributions(
    audit: dict[str, Any],
    baseline: dict[str, Any],
) -> dict[str, Any]:
    """Distribution-level distances between target and baseline.

    Reported separately from z-scores because the shape is different:
    KL/JSD are single distances, not z-scores against a baseline mean
    and SD. Currently covers the POS-bigram divergence the
    distributional-diagnostics reference describes; future additions
    can plug into the same dict (function-word distribution, sentence-
    length distribution shape, etc.) without changing the call site.
    """
    out: dict[str, Any] = {}
    target_pb = (audit.get("tier2") or {}).get("pos_bigrams") or {}
    target_counts = target_pb.get("counts")
    baseline_counts = baseline.get("pos_bigram_aggregate") or {}
    if isinstance(target_counts, dict) and baseline_counts:
        dist = pos_bigram_distance(target_counts, baseline_counts)
        if dist is not None:
            out["pos_bigrams"] = dist
    return out


# ---------- Band classification ----------

# Heuristic compression thresholds calibrated against fluent native-English prose.
# These are fallback heuristics for use without a baseline corpus; with a baseline,
# the z-score interpretation is more reliable. Each entry is a tuple of
# (threshold, direction, weight, length_floor) where:
#   - direction is "lt" (compressed when value < threshold) or "gt" (when value > threshold)
#   - weight is the contribution to band classification (signals differ in reliability)
#   - length_floor is the minimum word count for the heuristic to be reliable
#
# Each threshold is a ThresholdSpec dataclass. Calibration status is
# tracked via the `status` field (4-tier enum + 1 marginal), retiered
# in v1.66.0 per `internal/SPEC_calibration_status_retier.md` to
# replace the old binary `provisional: bool`. Provenance conventions
# per status:
#
#   * `calibrated`: provenance MUST cite a labeled corpus with
#     reported metrics (e.g., "raid_v1_fpr0.01_2026-05-13").
#   * `literature_anchored`: provenance MUST cite a publication
#     (e.g., "diveye_basani_chen_tmlr_2026").
#   * `empirically_oriented`: provenance MUST cite a local source
#     (e.g., "voice_profile_aggregation_v1").
#   * `heuristic`: provenance MUST be None. Default for new signals.
#   * `structural_only`: provenance describes downstream use.
#
# See `internal/SPEC_calibration_status_retier.md` for the full
# tier definitions and the per-signal triage. See
# `internal/SPEC_calibration_toolchain.md` for the calibration
# toolchain that promotes signals from heuristic / literature /
# empirical tiers into calibrated.

# Allowed status values. Order is loose-to-tight calibration tier.
THRESHOLD_STATUS_VALUES = frozenset({
    "calibrated",
    "literature_anchored",
    "empirically_oriented",
    "heuristic",
    "structural_only",
})


@dataclass
class ThresholdSpec:
    """Per-signal threshold specification + calibration metadata.

    `signal_path` is the dotted audit-output path the validation
    harness uses to extract scores (e.g., `tier1.sentence_length.
    burstiness_B`). `direction` is the polarity ("gt" = compressed
    when score > threshold; "lt" = compressed when score < threshold).
    `weight` and `length_floor` carry through from the original
    tuple-registry shape.

    `status` (v1.66.0): one of {calibrated, literature_anchored,
    empirically_oriented, heuristic, structural_only}. Replaces the
    old `provisional: bool` per the v1.66.0 retier. Each tier has
    its own provenance convention enforced in `__post_init__`.

    For backward compatibility: the `provisional` property returns
    True for any non-calibrated, non-structural_only status. Code
    that read the old `spec.provisional` keeps working with the
    same semantics (calibrated → False, everything else → True).
    """

    signal_path: str
    value: float
    direction: str
    weight: float
    length_floor: int
    provenance: str | None = None
    status: str = "heuristic"

    def __post_init__(self) -> None:
        if self.direction not in ("gt", "lt"):
            raise ValueError(
                f"ThresholdSpec.direction must be 'gt' or 'lt', "
                f"got {self.direction!r}"
            )
        if self.status not in THRESHOLD_STATUS_VALUES:
            raise ValueError(
                f"ThresholdSpec.status must be one of "
                f"{sorted(THRESHOLD_STATUS_VALUES)!r}, got "
                f"{self.status!r}"
            )
        # Per-tier provenance invariants.
        if self.status == "calibrated" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='calibrated' requires a "
                "provenance slug citing the labeled corpus + version "
                "+ reported metrics."
            )
        if self.status == "literature_anchored" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='literature_anchored' requires "
                "a provenance slug citing the publication."
            )
        if self.status == "empirically_oriented" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='empirically_oriented' requires "
                "a provenance slug citing the local source."
            )
        if self.status == "heuristic" and self.provenance is not None:
            raise ValueError(
                "ThresholdSpec: status='heuristic' must have "
                "provenance=None. Promote to one of "
                "{calibrated, literature_anchored, "
                "empirically_oriented} to attach provenance."
            )

    @property
    def provisional(self) -> bool:
        """Backward-compat alias: True for any non-calibrated,
        non-structural status. Existing code that filtered by
        `spec.provisional` keeps the same operational meaning
        (calibrated signals are False; everything else is True).

        Deprecated: prefer explicit per-tier checks via
        `status_signals()` helpers below.
        """
        return self.status not in ("calibrated", "structural_only")


COMPRESSION_HEURISTICS: dict[str, ThresholdSpec] = {
    # Burstiness magnitude is the most reliable single signal.
    # Heuristic note: literature suggests B < -0.2 is compressed, but real
    # human essayistic prose with long sentences can reach B = -0.4 naturally
    # (verified on pre-AI testimony at B = -0.40). Threshold tightened to -0.4
    # so the heuristic catches the genuine AI mode-collapse case (B < -0.4
    # in the smoke-test AI passage) while sparing essayistic human registers.
    #
    # Provisional under the "Stylometry to the people" policy (2026-05-11).
    # An EditLens-anchored calibration was run on 2026-05-10 (val split,
    # 1506 student essays, 753 AI / 753 ESL human; derived threshold
    # -0.6227 at FPR 0.93% / TPR 7.0%, da_AUC 0.683). That calibration
    # is preserved as an audit record in
    # `scripts/calibration/thresholds_calibrated.json` and as a
    # PROVENANCE entry, but is NOT loaded as the framework's threshold.
    # The framework's posture is that anchored thresholds derived from
    # one labeled corpus (EditLens, RAID, MAGE) do not generalize to the
    # user's register mix without local recalibration. Users wanting a
    # corpus-anchored threshold should run `calibrate_thresholds.py`
    # against their own labeled baseline; the PROVENANCE pattern is the
    # methodology, not a registry of authoritative numbers. See
    # `scripts/calibration/PROVENANCE.md` for the policy statement.
    "burstiness_B": ThresholdSpec(
        signal_path="tier1.sentence_length.burstiness_B",
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        value=-0.40, direction="lt",
        weight=2.0, length_floor=200,
    ),
    # Connective density: AI-prose runs 25-50 per 1000 tokens; humans 5-15.
    # Local da_AUC measurement on EditLens v1 (2026-05-10 findings):
    # 0.529 — the weakest of the six variance signals measured but
    # the band shape is consistent with literature.
    "connective_density": ThresholdSpec(
        signal_path="tier1.connective_density.per_1000_tokens",
        value=20.0, direction="gt", weight=2.0, length_floor=200,
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
    ),
    # MATTR: literary fluent fiction runs 0.70-0.82 at window 50.
    "mattr": ThresholdSpec(
        signal_path="tier1.mattr.value",
        value=0.65, direction="lt", weight=1.0, length_floor=300,
        status="literature_anchored",
        provenance="mattr_literary_fiction_baseline_window_50",
    ),
    # MTLD: noisy below ~500 words; threshold tightened.
    "mtld": ThresholdSpec(
        signal_path="tier1.mtld",
        value=60.0, direction="lt", weight=1.0, length_floor=500,
    ),
    # Yule's K: concentration on frequent types.
    "yules_k": ThresholdSpec(
        signal_path="tier1.yules_k",
        value=200.0, direction="gt", weight=1.0, length_floor=500,
    ),
    # Shannon entropy: the literature reports 9.5-10.5 bits/token for native
    # fiction, but this depends heavily on vocabulary scope and register.
    # Empirical testing on pre-AI human prose across registers found values
    # 8.0-9.6, so the threshold has been removed (set very low) to avoid
    # firing false positives on writers whose vocabulary is naturally focused.
    # Use a personal baseline for entropy comparison instead of the heuristic.
    "shannon_entropy": ThresholdSpec(
        signal_path="tier1.shannon_entropy_bits",
        value=7.0, direction="lt", weight=1.0, length_floor=2000,
        status="literature_anchored",
        provenance="shannon_entropy_native_fiction_literature",
    ),
    # FKGL SD: human prose typically 3-5 across sentences; LLM 0.8-1.5.
    # Local da_AUC measurement on EditLens v1 (2026-05-10 findings):
    # 0.635. Tier note: literature anchor + local measurement; the
    # local da_AUC promotes this from literature_anchored.
    "fkgl_sd": ThresholdSpec(
        signal_path="tier1.fkgl.sd",
        value=1.5, direction="lt", weight=1.5, length_floor=200,
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
    ),
    # Sentence-length SD is unreliable as a standalone signal because mean
    # sentence length varies dramatically by register (fiction with fragments
    # has SD 6-9; essay has SD 15-20). Burstiness B normalizes for mean and
    # carries this signal more reliably. Threshold raised so it almost never
    # fires; rely on B and on personal baseline z-scores instead.
    "sentence_length_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier1.sentence_length.sd",
        value=5.0, direction="lt", weight=0.5, length_floor=5000,
    ),
    # Adjacent-sentence cosine: tight cohesion is the LLM tell.
    "adjacent_cosine_mean": ThresholdSpec(
        signal_path="tier3.adjacent_cosine.mean",
        value=0.60, direction="gt", weight=1.5, length_floor=200,
    ),
    "adjacent_cosine_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier3.adjacent_cosine.sd",
        value=0.12, direction="lt", weight=1.0, length_floor=300,
    ),
    # MDD-SD: compressed syntactic variation.
    "mdd_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier2.mdd.sd",
        value=0.7, direction="lt", weight=1.0, length_floor=300,
    ),
    # ---- Tier 4 (surprisal), C.4, v1.47.0+ ----
    # PROVISIONAL per SPEC_surprisal_signal.md §3.5 and §4.3: the
    # three Tier 4 thresholds ship with provisional=True. Values
    # come from fixture-derived heuristics (the same numbers used
    # in surprisal_audit.py's PROVISIONAL_BAND_THRESHOLDS), NOT
    # from a labeled-corpus calibration. The framework's posture
    # is that load-bearing surprisal thresholds belong to the
    # operator's calibration; these provisional values exist so
    # the band classifier has something to chew on when Tier 4 is
    # enabled but never get treated as authoritative. The
    # ClaimLicense block downstream surfaces
    # calibration_anchor: user-baseline-required.
    #
    # Direction semantics:
    #   surprisal_mean (lt): AI prose tends LOWER (LM samples near
    #     its own mode → more predictable).
    #   surprisal_sd (lt): AI prose tends LOWER (more uniform
    #     surprise). DivEye's load-bearing signal.
    #   surprisal_acf_lag1 (gt): AI prose tends HIGHER (smooth
    #     local predictability).
    "surprisal_mean": ThresholdSpec(
        signal_path="tier4.surprisal.mean",
        value=3.5, direction="lt", weight=1.5, length_floor=300,
        status="literature_anchored",
        provenance="diveye_basani_chen_tmlr_2026",
    ),
    "surprisal_sd": ThresholdSpec(
        signal_path="tier4.surprisal.sd",
        value=1.5, direction="lt", weight=2.0, length_floor=300,
        status="literature_anchored",
        provenance="diveye_basani_chen_tmlr_2026",
    ),
    "surprisal_acf_lag1": ThresholdSpec(
        signal_path="tier4.surprisal.autocorrelation.lag_1",
        value=0.30, direction="gt", weight=1.0, length_floor=500,
        status="literature_anchored",
        provenance="diveye_basani_chen_tmlr_2026",
    ),
    # AIC-7 named-pattern density (v1.65.0). Per
    # `internal/SPEC_aic_8_9_implementation.md` Step 10 part 2.
    # Backfilled when `--aic7` integration landed; emits at
    # `patterns.<key>.density_per_1k` via `_aic7_named_pattern_block`.
    #
    # The four spec-named patterns get registry entries. Other
    # patterns the detector emits (negation_hedge, pseudo_aphorism,
    # false_balance, hedge_and_affirm, recommendation_template,
    # authority_laundering) are visible in the audit output but
    # not in the band-classifier loop; they remain diagnostic-only
    # until empirical work pins thresholds.
    #
    # Thresholds are PROVISIONAL placeholders. The Schnell case
    # study (Glass-Box Stylometry Sequence Post 2) anchors
    # correctio at 15.8 / 1000 words but that's the Schnell-author
    # baseline, not a smoothing-detection threshold. Calibration
    # against a labeled corpus replaces these.
    "correctio_density": ThresholdSpec(
        signal_path="patterns.correctio.density_per_1k",
        value=12.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "triplet_density": ThresholdSpec(
        signal_path="patterns.triplet.density_per_1k",
        value=8.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "manifesto_cadence_density": ThresholdSpec(
        signal_path="patterns.manifesto_cadence.density_per_1k",
        value=3.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "professional_parallel_stack_density": ThresholdSpec(
        signal_path="patterns.professional_parallel_stack.density_per_1k",
        value=2.0, direction="gt", weight=1.0, length_floor=400,
    ),
    # AIC-9 closure inflation (v1.65.0). Per spec Step 10 part 1.
    # Emits at `aic_8_9.kicker_density.value` via
    # `_aic9_kicker_block`. Threshold 0.25 (25% of paragraphs)
    # starts well above register-typical contemporary essay
    # (~0.08) so the default catches the elevation pattern, not
    # the genre-typical aphoristic essay.
    "kicker_density": ThresholdSpec(
        signal_path="aic_8_9.kicker_density.value",
        value=0.25, direction="gt", weight=1.0, length_floor=400,
    ),
    # AIC-8 aesthetic authority laundering (v1.65.0). Per spec
    # Step 10 part 1. Two signals emitted at
    # `aic_8_9.image_conjunction_density.value` and
    # `aic_8_9.prestige_metaphor_density.domain_scatter_entropy`
    # via `_aic8_image_prestige_block`. PROVISIONAL thresholds;
    # spec's T1/T2/T3 starting values don't crisply separate
    # idioms from AI positives on Brysbaert data (documented
    # finding in 1.61.0 CHANGELOG + ROADMAP).
    "image_conjunction_density": ThresholdSpec(
        signal_path="aic_8_9.image_conjunction_density.value",
        value=15.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "prestige_metaphor_scatter": ThresholdSpec(
        signal_path="aic_8_9.prestige_metaphor_density.domain_scatter_entropy",
        value=0.7, direction="gt", weight=1.0, length_floor=400,
    ),
}


def signals_by_status(
    status: str,
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return signal keys whose `status` matches the given tier.

    Tiers (v1.66.0): "calibrated", "literature_anchored",
    "empirically_oriented", "heuristic", "structural_only".
    """
    if status not in THRESHOLD_STATUS_VALUES:
        raise ValueError(
            f"Unknown status {status!r}; expected one of "
            f"{sorted(THRESHOLD_STATUS_VALUES)!r}"
        )
    return [k for k, spec in heuristics.items() if spec.status == status]


def calibrated_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "calibrated"`.

    A `calibrated` signal carries provenance citing a labeled corpus
    with reported metrics (FPR / TPR / AUC / distribution moments).
    The Stylometry-to-the-people invariant: until the §5.4
    calibration corpus runs, this list is expected to be empty.
    """
    return signals_by_status("calibrated", heuristics)


def literature_anchored_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "literature_anchored"`.

    A `literature_anchored` signal carries provenance citing a
    publication (e.g., `diveye_basani_chen_tmlr_2026`). The bands
    are taken from published work but not corpus-validated locally.
    """
    return signals_by_status("literature_anchored", heuristics)


def empirically_oriented_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "empirically_oriented"`.

    An `empirically_oriented` signal carries provenance citing a
    local source (`voice_profile_aggregation_v1`, internal fixture
    testing). The bands come from local experimentation, not a
    published study.
    """
    return signals_by_status("empirically_oriented", heuristics)


def heuristic_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "heuristic"`.

    A `heuristic` signal carries no provenance. The threshold is a
    plausible working value awaiting validation. The §5.4
    calibration corpus track is the named promotion path:
    `heuristic` → `calibrated` as corpus data lands.
    """
    return signals_by_status("heuristic", heuristics)


def provisional_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals whose status is not `calibrated` and
    not `structural_only` — i.e., everything else (`heuristic`,
    `literature_anchored`, `empirically_oriented`).

    Backward-compat helper (v1.66.0 retier kept the name but
    redefined the semantics around the new four-tier enum). Prefer
    the explicit per-tier helpers (`heuristic_signals`,
    `literature_anchored_signals`, `empirically_oriented_signals`)
    for new code. Existing callers that just wanted "not calibrated"
    keep working with the same operational meaning.
    """
    return [k for k, spec in heuristics.items() if spec.provisional]


# POS-bigram KL divergence against a baseline aggregate. Unlike the 11
# heuristics above, this signal is baseline-relative and only
# participates in the band classification when a baseline is supplied
# (and POS-bigram counts are available, which requires Tier 2 / spaCy).
#
# Empirical motivation: on AI-composed prose where every variance
# metric (burstiness, MATTR, MTLD, Yule's K, Shannon entropy, FKGL SD,
# MDD SD, function-word ratio, sentence-length SD) reads inside human
# bounds against the writer's own pre-AI baseline, POS-bigram KL has
# elevated as the single signal carrying the syntactic-template-collapse
# evidence. The 2026 multi-model collaborative regime (notes -> AI ->
# comments -> AI fix) preserves surface variance because human editing
# reintroduces it; what remains different from the writer's idiolect is
# the syntactic palette the models draw from. KL catches that.
#
# Threshold 0.15 from the literature anchor in
# references/distributional-diagnostics.md ("KL > 0.15 against a
# register-matched human baseline is a meaningful syntactic-template-
# collapse signal"). Cross-human KL on matched genres typically below
# 0.05; human-vs-LLM KL typically 0.10-0.30.
#
# Weight 2.0 matches burstiness_B and connective_density (the two
# highest-weighted variance signals). This is a starting calibration;
# pending recalibration against the validation harness on a labeled
# corpus, where the empirical ROC will tell us whether KL deserves a
# higher weight than the variance signals on this generation of AI
# assistance.
POS_BIGRAM_KL_HEURISTIC: ThresholdSpec = ThresholdSpec(
    signal_path="baseline_divergences.pos_bigrams.kl",
    value=0.15, direction="gt", weight=2.0, length_floor=500,
    status="literature_anchored",
    provenance="pos_bigram_kl_distributional_diagnostics",
)


def classify_compression(
    audit: dict[str, Any],
    *,
    divergences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flagged: list[str] = []
    skipped: list[str] = []
    available_signals: list[str] = []  # 1.35.1 — see ablation contract
    notes: dict[str, str] = {}
    weighted_score = 0.0
    available_weight = 0.0
    n_words = audit.get("summary", {}).get("n_words", 0)
    t1 = audit.get("tier1", {})
    t2 = audit.get("tier2") or {}
    t3 = audit.get("tier3") or {}

    def check(
        signal: str,
        value: float | None,
    ) -> None:
        nonlocal weighted_score, available_weight
        if value is None:
            # Signal was not computed at all (Tier 2/3 disabled, or
            # the audit didn't produce a value). Distinct from
            # length-floor skip — the signal isn't in `available`
            # AND isn't reported as `skipped` because it never
            # entered the band-call calculus.
            return
        if signal not in COMPRESSION_HEURISTICS:
            return
        spec = COMPRESSION_HEURISTICS[signal]
        thresh, direction, weight, length_floor = (
            spec.value, spec.direction, spec.weight, spec.length_floor
        )
        if n_words < length_floor:
            skipped.append(f"{signal} (need {length_floor}+ words, have {n_words})")
            return
        # Signal is in scope: it cleared its length floor and has a
        # value. Count its weight as available evidence regardless of
        # whether it ends up flagged. Track its name in
        # ``available_signals`` so the ablation contract can
        # distinguish "signal evaluated and weight contributed" from
        # "signal never computed because tier disabled."
        available_weight += weight
        available_signals.append(signal)
        compressed = (
            (direction == "lt" and value < thresh)
            or (direction == "gt" and value > thresh)
        )
        if compressed:
            flagged.append(signal)
            weighted_score += weight

    sl = t1.get("sentence_length", {})
    check("burstiness_B", sl.get("burstiness_B"))
    check("sentence_length_sd", sl.get("sd"))

    cd = t1.get("connective_density", {})
    check("connective_density", cd.get("per_1000_tokens"))

    check("mattr", (t1.get("mattr") or {}).get("value"))
    check("mtld", t1.get("mtld"))
    check("yules_k", t1.get("yules_k"))
    check("shannon_entropy", t1.get("shannon_entropy_bits"))
    check("fkgl_sd", (t1.get("fkgl") or {}).get("sd"))

    mdd = t2.get("mdd")
    if mdd:
        check("mdd_sd", mdd.get("sd"))

    adj = t3.get("adjacent_cosine")
    if adj:
        check("adjacent_cosine_mean", adj.get("mean"))
        check("adjacent_cosine_sd", adj.get("sd"))

    # Tier 4 — surprisal (Codex PR #31 review P0). The C.4 signals
    # were registered in COMPRESSION_HEURISTICS but never wired into
    # classify_compression's check() loop, so they never entered
    # ``available_signals`` / ``available_weight`` and the ablation
    # arithmetic couldn't see them. Now they're checked here under
    # the same length-floor + threshold contract as Tier 1-3.
    t4 = audit.get("tier4") or {}
    surprisal = t4.get("surprisal") if isinstance(t4, dict) else None
    if isinstance(surprisal, dict) and surprisal.get("available", True):
        check("surprisal_mean", surprisal.get("mean"))
        check("surprisal_sd", surprisal.get("sd"))
        # ACF lives one level deeper.
        acf = surprisal.get("autocorrelation") or {}
        check("surprisal_acf_lag1", acf.get("lag_1"))

    # AIC-7 named-pattern density (v1.65.0). Same wiring contract
    # as Tier 4 above: signals are registered in COMPRESSION_HEURISTICS
    # AND in `_ABLATION_SIGNAL_FAMILIES["assistant_register_intrusion"]`
    # AND checked here. Without the check() call the registry
    # entry would be orphaned (the Tier-4-pre-Codex-#31 failure
    # mode).
    patterns = audit.get("patterns") or {}
    if patterns:
        for pattern_key in (
            "correctio", "triplet",
            "manifesto_cadence", "professional_parallel_stack",
        ):
            pattern_block = patterns.get(pattern_key) or {}
            density = pattern_block.get("density_per_1k")
            check(f"{pattern_key}_density", density)

    # AIC-8 + AIC-9 (v1.65.0). Three signals across two flag
    # families; same wiring contract.
    aic_8_9 = audit.get("aic_8_9") or {}
    kicker = aic_8_9.get("kicker_density") or {}
    check("kicker_density", kicker.get("value"))
    image_conj = aic_8_9.get("image_conjunction_density") or {}
    check("image_conjunction_density", image_conj.get("value"))
    prestige = aic_8_9.get("prestige_metaphor_density") or {}
    check("prestige_metaphor_scatter", prestige.get("domain_scatter_entropy"))

    # POS-bigram KL divergence against baseline aggregate. Only
    # participates when a baseline is supplied and the POS-bigram
    # divergence was computed (which requires Tier 2 / spaCy on both
    # sides). When in scope, this signal often carries more diagnostic
    # weight than any single variance metric on AI-composed prose where
    # human editing has restored surface variance.
    pos_bigram_kl_info: dict[str, Any] | None = None
    if divergences is not None:
        pos_pb = divergences.get("pos_bigrams")
        if isinstance(pos_pb, dict) and "kl_to_baseline" in pos_pb:
            kl_value = pos_pb["kl_to_baseline"]
            kl_threshold = POS_BIGRAM_KL_HEURISTIC.value
            kl_direction = POS_BIGRAM_KL_HEURISTIC.direction
            kl_weight = POS_BIGRAM_KL_HEURISTIC.weight
            kl_floor = POS_BIGRAM_KL_HEURISTIC.length_floor
            pos_bigram_kl_info = {
                "value": kl_value,
                "threshold": kl_threshold,
                "weight": kl_weight,
                "length_floor": kl_floor,
                "n_target_bigrams": pos_pb.get("n_target_bigrams"),
                "n_baseline_bigrams": pos_pb.get("n_baseline_bigrams"),
                "in_band": False,
                "compressed": False,
            }
            if n_words < kl_floor:
                skipped.append(
                    f"pos_bigram_kl (need {kl_floor}+ words, have {n_words})"
                )
            elif isinstance(kl_value, (int, float)):
                available_weight += kl_weight
                available_signals.append("pos_bigram_kl")
                pos_bigram_kl_info["in_band"] = True
                kl_compressed = (
                    (kl_direction == "lt" and kl_value < kl_threshold)
                    or (kl_direction == "gt" and kl_value > kl_threshold)
                )
                if kl_compressed:
                    flagged.append("pos_bigram_kl")
                    weighted_score += kl_weight
                    pos_bigram_kl_info["compressed"] = True

    # Band assignment by *fraction* of available signal weight. The
    # absolute weighted_score depends on which signals had data and
    # which were skipped for length, so a fixed-score threshold reads
    # the same flag count differently in long and short documents:
    # one signal at weight 2.0 firing in a 200-word doc with only 7.0
    # available weight is 29% of available evidence, but in a 2000-
    # word doc with 13.5 available weight it is 15%. Normalizing makes
    # the band classification a fraction-of-evidence statement that
    # carries across document lengths. Threshold values below are
    # calibrated near the old absolute-weight cutoffs at the
    # full-evidence (13.5) case: old < 2.0 ≈ 0.15, old ≥ 5.0 ≈ 0.37.
    if available_weight <= 0:
        band = "Insufficient signal"
        compression_fraction: float | None = None
    else:
        compression_fraction = weighted_score / available_weight
        if compression_fraction < 0.15:
            band = "Lightly smoothed"
        elif compression_fraction < 0.40:
            band = "Moderately smoothed"
        else:
            band = "Heavily smoothed"

    if n_words < 200:
        notes["reliability"] = (
            "Document below 200 words; band classification unreliable. "
            "Use Layer B and C primarily."
        )
    elif n_words < 500:
        notes["reliability"] = (
            "Document below 500 words; some length-sensitive signals skipped. "
            "Cross-check against Layer B."
        )
    if available_weight > 0 and available_weight < 5.0:
        max_weight = sum(spec.weight for spec in COMPRESSION_HEURISTICS.values())
        notes["evidence"] = (
            f"Only {available_weight:.1f} of {max_weight:.1f} "
            "max signal weight cleared its length floor. Band is a "
            "fraction-of-available statement, not an absolute count."
        )

    thresholds_used = {
        k: {
            "threshold": spec.value,
            "direction": spec.direction,
            "weight": spec.weight,
            "length_floor": spec.length_floor,
            "signal_path": spec.signal_path,
            "provenance": spec.provenance,
            "status": spec.status,
            # Backward-compat: keep `provisional` in the JSON for
            # consumers from before the v1.66.0 retier. Semantics
            # unchanged (True for any non-calibrated, non-structural
            # signal).
            "provisional": spec.provisional,
        }
        for k, spec in COMPRESSION_HEURISTICS.items()
    }
    # POS-bigram KL is a baseline-relative signal; surface its threshold
    # so consumers see what cutoff the band call used. The
    # `requires_baseline` flag distinguishes it from the variance
    # heuristics that participate in every run.
    thresholds_used["pos_bigram_kl"] = {
        "threshold": POS_BIGRAM_KL_HEURISTIC.value,
        "direction": POS_BIGRAM_KL_HEURISTIC.direction,
        "weight": POS_BIGRAM_KL_HEURISTIC.weight,
        "length_floor": POS_BIGRAM_KL_HEURISTIC.length_floor,
        "signal_path": POS_BIGRAM_KL_HEURISTIC.signal_path,
        "provenance": POS_BIGRAM_KL_HEURISTIC.provenance,
        "status": POS_BIGRAM_KL_HEURISTIC.status,
        "provisional": POS_BIGRAM_KL_HEURISTIC.provisional,
        "requires_baseline": True,
    }

    # Calibration status block: surfaces which signal thresholds carry
    # calibration provenance vs. which are still provisional/heuristic.
    # Populated by the calibration toolchain at scripts/calibration/
    # (see internal/SPEC_calibration_toolchain.md). v1 release ships
    # with all signals provisional; thresholds get `provenance` slugs
    # set as calibrated values land.
    calibrated = calibrated_signals(COMPRESSION_HEURISTICS)
    provisional = provisional_signals(COMPRESSION_HEURISTICS)
    calibration_status = {
        "n_calibrated": len(calibrated),
        "n_provisional": len(provisional),
        "n_total": len(COMPRESSION_HEURISTICS),
        "calibrated_signals": calibrated,
        "provisional_signals": provisional,
    }

    result: dict[str, Any] = {
        "band": band,
        "weighted_score": round(weighted_score, 2),
        "available_weight": round(available_weight, 2),
        "compression_fraction": (
            round(compression_fraction, 3)
            if compression_fraction is not None
            else None
        ),
        "flagged_signals": flagged,
        # 1.35.1 — `available_signals` is the explicit list of
        # signal names whose value was retrieved AND cleared the
        # length floor (i.e., contributed to `available_weight`).
        # Distinct from `flagged_signals` (signals that fired) and
        # from `skipped_signals` (signals length-floor-skipped).
        # Read by `ablation_band_calls` to avoid double-counting
        # weights for signals that were never in scope (e.g.
        # tier-3 signals when --no-tier3 is set).
        "available_signals": available_signals,
        "skipped_signals": skipped,
        "n_flagged": len(flagged),
        "notes": notes,
        "thresholds_used": thresholds_used,
        "calibration_status": calibration_status,
    }
    if pos_bigram_kl_info is not None:
        result["pos_bigram_kl"] = pos_bigram_kl_info
    return result


# ---------- Ablation reports (Release 5, Trustworthiness Tier 2) ----------
#
# Leave-one-feature-family-out band call. Tells the reader which
# signal families are *load-bearing* for the compression call: a
# band that drops from "Heavily smoothed" to "Lightly smoothed"
# when one family is removed is a fragile, family-driven call;
# a band that holds across all ablations is a robust, multi-
# signal-driven call.
#
# The implementation reuses the existing classify_compression
# output rather than re-running the audit — we know which signals
# fired (flagged_signals), the weighted-score totals, and the
# weights of each family, so the ablation arithmetic is closed-
# form: subtract the family's weight contribution from the
# numerator (if fired) and the denominator (always, when in scope),
# then re-bucket the resulting fraction.
#
# Family taxonomy mirrors the `_SIGNAL_FAMILIES` mapping in
# `sliding_window_heatmap.py`'s phenomenon classifier — same
# four families, same signal membership.

_ABLATION_SIGNAL_FAMILIES: dict[str, tuple[str, ...]] = {
    "syntactic_flattening": (
        "burstiness_B", "sentence_length_sd",
        "fkgl_sd", "mdd_sd",
    ),
    "lexical_compression": (
        "mtld", "mattr", "shannon_entropy", "yules_k",
    ),
    "over_cohesion": (
        "adjacent_cosine_mean", "adjacent_cosine_sd",
    ),
    "connective_overuse": (
        "connective_density",
    ),
    # 1.35.1 — baseline_divergence family. `pos_bigram_kl`
    # participates in compression when a baseline is supplied
    # (weight 2.0). Pre-1.35.1 it was absent from the ablation
    # mapping, so a band call carried mostly or entirely by KL
    # could still report `is_robust_call=True` with no
    # load-bearing families. Now ablation has a family for it.
    # The family name `baseline_divergence` reflects that this
    # is the only baseline-relative signal in the registry — if
    # future signals join (e.g., syntactic-template divergence
    # against a register-matched baseline), they belong here.
    "baseline_divergence": (
        "pos_bigram_kl",
    ),
    # Codex PR #31 review P0 — predictability_uniformity family.
    # The Tier 4 surprisal signals (C.4) participate in compression
    # when `--tier4` is on (combined weight 4.5: mean=1.5 + sd=2.0
    # + acf=1.0). Pre-fix they were absent from the ablation
    # mapping, so a band call carried by Tier 4 could report
    # `is_robust_call=True` with no load-bearing families — the
    # ablation arithmetic never subtracted the surprisal weight.
    # The family name `predictability_uniformity` reflects what the
    # three signals jointly measure: how uniformly the LM
    # distributes surprise across the text — the operational
    # fingerprint of LLM smoothing per SPEC §1.2.
    "predictability_uniformity": (
        "surprisal_mean",
        "surprisal_sd",
        "surprisal_acf_lag1",
    ),
    # v1.65.0: three new families for the AIC-7 / AIC-8 / AIC-9
    # integration. Each family bundles the signals from one
    # discourse-pattern family so ablation can ask "is this AIC
    # subtype load-bearing for the band call?" separately.
    #
    # assistant_register_intrusion: AIC-7's four spec-named
    # patterns (correctio, triplet, manifesto cadence, professional-
    # parallel stack). Combined weight 4.0 when --aic7 is on.
    "assistant_register_intrusion": (
        "correctio_density",
        "triplet_density",
        "manifesto_cadence_density",
        "professional_parallel_stack_density",
    ),
    # closure_inflation: AIC-9's single signal. Weight 1.0 when
    # --aic9 is on. Single-signal family kept separate from
    # aesthetic_authority_laundering so ablation distinguishes
    # "the kicker shape was load-bearing" from "the image
    # conjunctions were load-bearing."
    "closure_inflation": (
        "kicker_density",
    ),
    # aesthetic_authority_laundering: AIC-8's two signals
    # (image conjunction density + prestige metaphor scatter
    # entropy). Combined weight 2.0 when --aic8 is on. Per the
    # spec's compound-diagnostic framing: image conjunctions and
    # prestige metaphors jointly produce the "metaphor confetti"
    # signature; ablation should remove them together.
    "aesthetic_authority_laundering": (
        "image_conjunction_density",
        "prestige_metaphor_scatter",
    ),
}


def ablation_band_calls(
    compression_result: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    """For each signal family, compute the band call that would
    result if that family's signals were excluded.

    Output: a dict mapping family name → ``{band,
    compression_fraction, weight_excluded, fired_weight_excluded,
    removed_signals, robustness}``. The ``robustness`` field
    summarizes the change vs. the original band:
      - ``stable`` — same band as the original.
      - ``fragile_drop`` — band dropped one or more levels (the
        original call relied on this family).
      - ``fragile_rise`` — band rose one or more levels (rare;
        means the family was suppressing a higher band call by
        diluting the available_weight without firing).

    Tells the reader which families are load-bearing for the
    compression call.
    """
    flagged = set(compression_result.get("flagged_signals") or [])
    weighted_score = float(
        compression_result.get("weighted_score", 0.0)
    )
    available_weight = float(
        compression_result.get("available_weight", 0.0)
    )
    # 1.35.1 — read the explicit available-signals list rather
    # than re-deriving from length floors. Pre-1.35.1 the
    # ablation checked `n_words < spec.length_floor` to gate
    # signal inclusion, but `classify_compression` only adds a
    # signal's weight to `available_weight` when its VALUE was
    # retrieved (which fails for tier-2/3 signals when the
    # corresponding tier was disabled). Reviewer reproduced
    # `--no-tier3` runs reporting tier-3 weight in `weight_excluded`
    # for the over_cohesion family even though those signals were
    # never in `available_weight` to begin with.
    available_signals_set = set(
        compression_result.get("available_signals") or []
    )
    original_band = compression_result.get("band", "unknown")
    n_words = (audit.get("summary") or {}).get("n_words", 0)

    band_rank = {
        "Insufficient signal": -1,
        "Lightly smoothed": 0,
        "Moderately smoothed": 1,
        "Heavily smoothed": 2,
    }
    original_rank = band_rank.get(original_band, -2)

    def _signal_weight(signal: str) -> float | None:
        """Return the registry weight for `signal`, looking in
        both `COMPRESSION_HEURISTICS` and `POS_BIGRAM_KL_HEURISTIC`.
        Returns None if the signal isn't in any registry."""
        if signal in COMPRESSION_HEURISTICS:
            return float(COMPRESSION_HEURISTICS[signal].weight)
        if signal == "pos_bigram_kl":
            return float(POS_BIGRAM_KL_HEURISTIC.weight)
        return None

    out: dict[str, Any] = {}
    for family, signals in _ABLATION_SIGNAL_FAMILIES.items():
        family_weight_available = 0.0
        family_weight_fired = 0.0
        for sig in signals:
            # 1.35.1 — only count weight for signals that actually
            # contributed to the compression call. A signal not in
            # `available_signals_set` was either below its length
            # floor (already excluded from `available_weight`) or
            # never computed (tier disabled, baseline absent for
            # KL, etc.) — both cases mean its weight is NOT in
            # the denominator and "removing" it costs nothing.
            if sig not in available_signals_set:
                continue
            weight = _signal_weight(sig)
            if weight is None:
                continue
            family_weight_available += weight
            if sig in flagged:
                family_weight_fired += weight

        ablated_available = available_weight - family_weight_available
        ablated_score = weighted_score - family_weight_fired

        if ablated_available <= 0:
            ablated_band = "Insufficient signal"
            ablated_fraction = None
        else:
            ablated_fraction = ablated_score / ablated_available
            if ablated_fraction < 0.15:
                ablated_band = "Lightly smoothed"
            elif ablated_fraction < 0.40:
                ablated_band = "Moderately smoothed"
            else:
                ablated_band = "Heavily smoothed"

        ablated_rank = band_rank.get(ablated_band, -2)
        if ablated_rank == original_rank:
            robustness = "stable"
        elif ablated_rank < original_rank:
            robustness = "fragile_drop"
        else:
            robustness = "fragile_rise"

        out[family] = {
            "band": ablated_band,
            "compression_fraction": (
                round(ablated_fraction, 3)
                if ablated_fraction is not None else None
            ),
            "weight_excluded": round(family_weight_available, 2),
            "fired_weight_excluded": round(family_weight_fired, 2),
            "removed_signals": list(signals),
            "robustness": robustness,
        }

    # Summary: which families are load-bearing? Any with
    # robustness != "stable" carry the original band call;
    # families with robustness=="stable" are non-load-bearing.
    load_bearing = [
        f for f, info in out.items()
        if info["robustness"] != "stable"
    ]
    return {
        "original_band": original_band,
        "original_compression_fraction": (
            compression_result.get("compression_fraction")
        ),
        "per_family": out,
        "load_bearing_families": load_bearing,
        "is_robust_call": len(load_bearing) == 0,
    }


def format_ablation_block(ablation: dict[str, Any]) -> list[str]:
    """Markdown rendering of the ablation table."""
    out: list[str] = ["", "## Ablation: leave-one-family-out band call", ""]
    out.append(
        "Removes each signal family in turn and recomputes the "
        "band. Tells you which families are *load-bearing* for "
        "the compression call: a band that drops when one family "
        "is removed is a fragile, family-driven call; a band that "
        "holds across all ablations is a robust, multi-family call."
    )
    out.append("")
    out.append(
        f"**Original band:** {ablation.get('original_band')}  "
        f"({'robust' if ablation.get('is_robust_call') else 'fragile'} call)"
    )
    if ablation.get("load_bearing_families"):
        out.append(
            f"**Load-bearing families:** "
            + ", ".join(
                f"`{f}`" for f in ablation["load_bearing_families"]
            )
        )
    out.append("")
    out.append(
        "| family removed | resulting band | fraction | "
        "weight excluded | fired weight excluded | robustness |"
    )
    out.append("|---|---|---:|---:|---:|---|")
    for family, info in ablation.get("per_family", {}).items():
        frac = info.get("compression_fraction")
        frac_str = f"{frac:.3f}" if isinstance(frac, (int, float)) else "n/a"
        out.append(
            f"| `{family}` | {info['band']} | {frac_str} | "
            f"{info['weight_excluded']:.1f} | "
            f"{info['fired_weight_excluded']:.1f} | "
            f"`{info['robustness']}` |"
        )
    out.append("")
    return out


# ---------- Output formatting ----------

def format_summary(audit: dict[str, Any], compression: dict[str, Any]) -> str:
    lines = []
    s = audit.get("summary", {})
    lines.append("=" * 60)
    lines.append("LAYER A: DISTRIBUTIONAL DIAGNOSTIC")
    lines.append(f"task_surface: {TASK_SURFACE}")
    lines.append("=" * 60)
    lines.append(f"Words: {s.get('n_words', 0)}    Sentences: {s.get('n_sentences', 0)}")
    prep = audit.get("preprocessing") or {}
    if prep:
        if prep.get("opt_out"):
            lines.append("Preprocessing: skipped by --allow-non-prose")
        else:
            stripped = int(prep.get("tokens_stripped", 0) or 0)
            ratio = prep.get("strip_ratio", 0.0)
            dominant = prep.get("dominant_rule") or "none"
            ratio_str = f"{ratio:.1%}" if isinstance(ratio, (int, float)) else "n/a"
            lines.append(
                f"Preprocessing: stripped {stripped} tokens "
                f"({ratio_str}; dominant rule: {dominant})"
            )
    if not s.get("reliable", True):
        lines.append("WARNING: Document below 200 words; results are noisy.")
    lines.append("")
    fraction = compression.get("compression_fraction")
    fraction_str = (
        f"{fraction:.2f}" if isinstance(fraction, (int, float)) else "n/a"
    )
    lines.append(
        f"Band: {compression['band']}  "
        f"(compression fraction: {fraction_str}, "
        f"weighted score: {compression.get('weighted_score', 0)} of "
        f"{compression.get('available_weight', 0)} available)"
    )
    # Surface POS-bigram KL prominently in the headline. On AI-composed
    # prose where every variance metric reads clean against the writer's
    # baseline, KL is often the single signal carrying the diagnostic
    # weight; users reading only the band line should see it without
    # scrolling to the divergences block.
    pb_kl = compression.get("pos_bigram_kl")
    if isinstance(pb_kl, dict):
        kl_value = pb_kl.get("value")
        kl_thresh = pb_kl.get("threshold")
        in_band = pb_kl.get("in_band", False)
        compressed = pb_kl.get("compressed", False)
        kl_value_str = (
            f"{kl_value:.3f}" if isinstance(kl_value, (int, float)) else "n/a"
        )
        if in_band and compressed:
            verdict = "FIRED  (above threshold; contributed to band call)"
        elif in_band:
            verdict = "below threshold"
        else:
            verdict = "below length floor; not in band"
        lines.append(
            f"POS-bigram KL: {kl_value_str}  "
            f"(threshold {kl_thresh}, weight {pb_kl.get('weight')})  "
            f"-- {verdict}"
        )
    if compression["flagged_signals"]:
        lines.append("Flagged signals (compression observed):")
        for sig in compression["flagged_signals"]:
            lines.append(f"  - {sig}")
    else:
        lines.append("No compression flags fired against fallback heuristics.")
    if compression.get("skipped_signals"):
        lines.append("Skipped (insufficient text length):")
        for sig in compression["skipped_signals"]:
            lines.append(f"  - {sig}")
    if compression["notes"]:
        for k, v in compression["notes"].items():
            lines.append(f"Note ({k}): {v}")
    cal_status = compression.get("calibration_status")
    if isinstance(cal_status, dict):
        n_calibrated = cal_status.get("n_calibrated", 0)
        n_total = cal_status.get("n_total", 0)
        if n_calibrated == 0:
            lines.append(
                f"Calibration status: 0 of {n_total} signal thresholds carry "
                f"calibration provenance; all are heuristic. See "
                f"scripts/calibration/PROVENANCE.md once thresholds land."
            )
        else:
            lines.append(
                f"Calibration status: {n_calibrated} of {n_total} signal "
                f"thresholds carry calibration provenance. See "
                f"scripts/calibration/PROVENANCE.md for derivation details."
            )
    lines.append("")

    t1 = audit.get("tier1", {})
    lines.append("Tier 1 (always):")
    sl = t1.get("sentence_length", {})
    lines.append(
        f"  Sentence length: mean={sl.get('mean', 0):.2f} "
        f"sd={sl.get('sd', 0):.2f} "
        f"min={sl.get('min', 0):.0f} max={sl.get('max', 0):.0f} "
        f"B={sl.get('burstiness_B', 0):.3f}"
    )
    mat = t1.get("mattr", {})
    lines.append(f"  MATTR (window {mat.get('window', 50)}): {mat.get('value', 0):.4f}")
    lines.append(f"  MTLD: {t1.get('mtld', 0):.2f}")
    lines.append(f"  Yule's K: {t1.get('yules_k', 0):.2f}")
    lines.append(f"  Shannon entropy: {t1.get('shannon_entropy_bits', 0):.3f} bits/token")
    fk = t1.get("fkgl", {})
    lines.append(
        f"  FKGL: mean={fk.get('mean', 0):.2f} sd={fk.get('sd', 0):.2f} "
        f"min={fk.get('min', 0):.2f} max={fk.get('max', 0):.2f} (n={fk.get('n', 0)})"
    )
    cd = t1.get("connective_density", {})
    lines.append(
        f"  Connective density: {cd.get('per_1000_tokens', 0):.2f} per 1000 tokens "
        f"(total: {cd.get('count', 0)})"
    )
    fw = t1.get("function_words", {})
    lines.append(f"  Function-word ratio: {fw.get('function_word_ratio', 0):.4f}")

    t2 = audit.get("tier2")
    if t2:
        if t2.get("available"):
            lines.append("")
            lines.append("Tier 2 (spaCy):")
            pb = t2.get("pos_bigrams")
            if pb:
                lines.append(
                    f"  POS-bigrams: n={pb.get('n_bigrams', 0)} "
                    f"unique={pb.get('n_unique', 0)} "
                    f"entropy={pb.get('entropy_bits', 0):.3f} bits"
                )
            mdd = t2.get("mdd")
            if mdd:
                lines.append(
                    f"  MDD per sentence: mean={mdd.get('mean', 0):.3f} "
                    f"sd={mdd.get('sd', 0):.3f} (n={mdd.get('n_sentences', 0)})"
                )
        else:
            lines.append("")
            lines.append("Tier 2 (spaCy): not available. Install `spacy` and `en_core_web_sm`.")

    t3 = audit.get("tier3")
    if t3:
        if t3.get("available"):
            lines.append("")
            lines.append("Tier 3 (embeddings):")
            adj = t3.get("adjacent_cosine")
            if adj:
                lines.append(
                    f"  Adjacent-sentence cosine ({adj.get('method', 'unknown')}): "
                    f"mean={adj.get('mean', 0):.3f} sd={adj.get('sd', 0):.3f} "
                    f"(n={adj.get('n_pairs', 0)})"
                )
        else:
            lines.append("")
            lines.append(
                "Tier 3 (embeddings): not available. "
                "Install `sentence-transformers` or `scikit-learn`."
            )

    # Reviewer P2 (2026-05-14): Tier 4 surprisal block. Until this
    # render existed, --tier4 silently contributed to the band call
    # (via the three COMPRESSION_HEURISTICS entries) but the
    # human-readable summary never showed the causal-LM stats. That
    # makes the band call unauditable for any reader who only sees
    # the markdown. Render mirrors the Tier 3 shape (header,
    # available-or-not branch, one metric line per signal),
    # plus the PROVISIONAL marker + calibration anchor + backend
    # identifier so readers see immediately that:
    #   (a) the band call's Tier 4 contribution is provisional, and
    #   (b) which causal LM produced these numbers.
    t4 = audit.get("tier4")
    if t4:
        lines.append("")
        if t4.get("available"):
            lines.append("Tier 4 (surprisal):")
            s4 = t4.get("surprisal", {})
            mean_b = s4.get("mean")
            sd_b = s4.get("sd")
            acf = s4.get("autocorrelation", {}) or {}
            lag1 = acf.get("lag_1")
            mean_str = (
                f"{mean_b:.3f}" if isinstance(mean_b, (int, float)) else "n/a"
            )
            sd_str = (
                f"{sd_b:.3f}" if isinstance(sd_b, (int, float)) else "n/a"
            )
            lag1_str = (
                f"{lag1:.3f}" if isinstance(lag1, (int, float)) else "n/a"
            )
            lines.append(
                f"  Mean surprisal: {mean_str} bits/token   "
                f"SD: {sd_str}   lag-1 ACF: {lag1_str}"
            )
            n_scored = s4.get("n_tokens_scored")
            slen = s4.get("series_length")
            if isinstance(n_scored, int) and isinstance(slen, int):
                lines.append(
                    f"  Tokens scored: {n_scored:,}   "
                    f"series length: {slen:,}"
                )
            band = s4.get("band") or {}
            if band:
                lines.append(
                    f"  Band (PROVISIONAL): "
                    f"{band.get('band', 'indeterminate')}   "
                    f"calibration_anchor: "
                    f"{band.get('calibration_anchor', 'unknown')}"
                )
            backend_id = t4.get("backend") or {}
            if backend_id:
                model = backend_id.get("id", "unknown")
                rev = backend_id.get("revision") or "(no revision pin)"
                alias = backend_id.get("alias")
                alias_str = f" (alias `{alias}`)" if alias else ""
                lines.append(
                    f"  Model: {model}{alias_str}   "
                    f"revision: {rev}"
                )
            top = s4.get("top_k_tokens") or []
            if top:
                # Show only the top-3 in the summary (the full list
                # is in the JSON output); the markdown is a quick
                # scan, not the full diagnostic.
                preview = ", ".join(
                    f"{tok.get('token_text', '?')!r}"
                    f"@{tok.get('position', '?')} "
                    f"({tok.get('surprisal_bits', 0):.1f}b)"
                    for tok in top[:3]
                )
                lines.append(f"  Top surprising tokens: {preview}")
        else:
            reason = t4.get("reason", "unknown")
            lines.append(
                f"Tier 4 (surprisal): not available "
                f"({reason}). Install the optional surprisal "
                f"dependencies (`transformers + torch`) and pass "
                f"--surprisal-model to enable."
            )

    return "\n".join(lines)


def format_baseline_comparison(z_scores: dict[str, Any]) -> str:
    if not z_scores:
        return ""
    lines = []
    lines.append("")
    lines.append(
        "Baseline comparison (z-scores; |z| > 1.0 is meaningful; "
        "rows marked [!] fall below their length floor and are noisy):"
    )
    unreliable: list[str] = []
    for name, info in z_scores.items():
        z = info.get("z_score")
        if z is None:
            continue
        marker = " *" if abs(z) > 1.0 else ""
        floor_marker = ""
        if info.get("length_floor_satisfied") is False:
            floor_marker = " [!]"
            unreliable.append(name)
        lines.append(
            f"  {name}: value={info['value']:.4f} "
            f"baseline_mean={info['baseline_mean']:.4f} "
            f"z={z:+.2f}{marker}{floor_marker}"
        )
    if unreliable:
        lines.append("")
        lines.append(
            "Length-floor warnings (target word count below the floor "
            "for these signals; z-scores are noisy):"
        )
        for name in unreliable:
            info = z_scores[name]
            lines.append(
                f"  {name}: floor={info.get('length_floor')}"
            )
    return "\n".join(lines)


def format_windows_dashboard(windows: list[dict[str, Any]]) -> str:
    """Markdown table summarizing per-window band classifications."""
    if not windows:
        return ""
    lines = []
    lines.append("")
    lines.append(
        "Sliding-window scan (per-window band; whole-chapter scores can "
        "mask localized compression):"
    )
    lines.append("")
    has_baseline = any("baseline_comparison" in w for w in windows)
    if has_baseline:
        lines.append(
            "| # | start | end | n_words | band | fraction | "
            "max\\|z\\| | flagged |"
        )
        lines.append("|---|---:|---:|---:|---|---:|---:|---|")
    else:
        lines.append("| # | start | end | n_words | band | fraction | flagged |")
        lines.append("|---|---:|---:|---:|---|---:|---|")
    for i, w in enumerate(windows):
        c = w.get("compression", {})
        fraction = c.get("compression_fraction")
        fraction_str = (
            f"{fraction:.2f}" if isinstance(fraction, (int, float)) else "n/a"
        )
        flagged = ", ".join(c.get("flagged_signals", [])) or "(none)"
        if has_baseline:
            zs = w.get("baseline_comparison", {})
            abs_zs = [
                abs(info.get("z_score"))
                for info in zs.values()
                if isinstance(info, dict)
                and isinstance(info.get("z_score"), (int, float))
                and info.get("length_floor_satisfied", True)
            ]
            max_z = max(abs_zs) if abs_zs else None
            max_z_str = f"{max_z:.2f}" if isinstance(max_z, (int, float)) else "n/a"
            lines.append(
                f"| {i+1} | {w['start_word']} | {w['end_word']} | "
                f"{w['n_words']} | {c.get('band', 'unknown')} | "
                f"{fraction_str} | {max_z_str} | {flagged} |"
            )
        else:
            lines.append(
                f"| {i+1} | {w['start_word']} | {w['end_word']} | "
                f"{w['n_words']} | {c.get('band', 'unknown')} | "
                f"{fraction_str} | {flagged} |"
            )
    lines.append("")
    bands = Counter(w.get("compression", {}).get("band", "unknown") for w in windows)
    band_summary = ", ".join(
        f"{band}={count}"
        for band, count in sorted(bands.items(), key=lambda kv: -kv[1])
    )
    lines.append(
        f"Window band distribution ({len(windows)} windows): {band_summary}."
    )
    if has_baseline:
        lines.append(
            "Z-scores at window scope are noisy by construction; treat "
            "them as inspection leads rather than verdicts. Length-matched "
            "bootstrap percentiles are roadmap."
        )
    return "\n".join(lines)


def format_baseline_bootstrap(boot: dict[str, Any]) -> str:
    if not boot or not boot.get("available"):
        reason = boot.get("reason") if isinstance(boot, dict) else "(unavailable)"
        return f"Length-matched bootstrap: unavailable ({reason})."

    lines: list[str] = []
    lines.append("Length-matched bootstrap (Phase 1 step 3):")
    lines.append(
        f"  target n_words={boot['target_n_words']}  "
        f"baseline files={boot['n_baseline_files']}  "
        f"resamples={boot['n_resamples']}  "
        f"confidence={boot['confidence_level']:.2f}"
    )
    lines.append(
        "  Each row reports the target's percentile in the empirical"
    )
    lines.append(
        "  distribution of length-matched baseline windows, with a BCa"
    )
    lines.append(
        "  CI on the percentile. CIs collapse to [1.000,1.000] or"
    )
    lines.append(
        "  [0.000,0.000] when the target falls past the extreme of the"
    )
    lines.append(
        "  baseline distribution (every resample produces the same"
    )
    lines.append(
        "  percentile); the headline finding in those cases is the"
    )
    lines.append(
        "  point estimate, not the interval."
    )
    lines.append("")
    lines.append(
        f"  {'signal':38s} {'target':>10s} {'pct':>6s} {'CI':>17s} "
        f"{'p5':>9s} {'p50':>9s} {'p95':>9s} {'n':>4s}"
    )

    def fmt_num(v: float | None, width: int = 9) -> str:
        if v is None:
            return f"{'-':>{width}s}"
        return f"{v:>{width}.4f}"

    for sig, r in boot.get("per_signal", {}).items():
        if not isinstance(r, dict) or not r.get("available"):
            continue
        b = r.get("bootstrap", {})
        d = r.get("baseline_distribution", {})
        qs = d.get("quantiles", {}) or {}
        ci_low, ci_high = b.get("ci_low"), b.get("ci_high")
        if ci_low is None or ci_high is None:
            ci_str = f"({b.get('method', 'none')})"
        else:
            ci_str = f"[{ci_low:.3f},{ci_high:.3f}]"
        lines.append(
            f"  {sig:38s} {r['target_value']:10.4f} "
            f"{b.get('percentile', 0.0):6.3f} {ci_str:>17s} "
            f"{fmt_num(qs.get('p5'))} {fmt_num(qs.get('p50'))} "
            f"{fmt_num(qs.get('p95'))} {b.get('n_baseline_windows', 0):4d}"
        )

    skipped = boot.get("baseline_files_skipped") or []
    if skipped:
        lines.append("")
        lines.append("  Baseline files skipped:")
        for s in skipped:
            lines.append(f"    - {s.get('file', '?')}: {s.get('reason', '')}")
    return "\n".join(lines)


def format_baseline_divergences(divergences: dict[str, Any]) -> str:
    if not divergences:
        return ""
    lines = []
    lines.append("")
    lines.append(
        "Distribution divergences (target vs. baseline aggregate):"
    )
    pb = divergences.get("pos_bigrams")
    if pb:
        lines.append(
            f"  POS-bigrams: KL={pb.get('kl_to_baseline'):.4f}, "
            f"JSD={pb.get('jsd_to_baseline'):.4f} "
            f"(target n={pb.get('n_target_bigrams')}, "
            f"baseline n={pb.get('n_baseline_bigrams')}, "
            f"union {pb.get('n_unique_union')} types)"
        )
        lines.append(
            "  KL > 0.15 against a register-matched human baseline is "
            "a meaningful syntactic-template-collapse signal; cross-"
            "human KL on matched genres is typically below 0.05."
        )
    return "\n".join(lines)


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Layer A variance audit for ai-prose-detection skill."
    )
    parser.add_argument("input", help="Path to text file to audit.")
    parser.add_argument(
        "--baseline-dir",
        help="Optional directory of .txt files for baseline comparison.",
    )
    parser.add_argument(
        "--mattr-window",
        type=int,
        default=50,
        help="Window size for MATTR (default 50).",
    )
    parser.add_argument(
        "--no-tier2", action="store_true",
        help="Skip Tier 2 metrics (POS bigrams, MDD)."
    )
    parser.add_argument(
        "--no-tier3", action="store_true",
        help="Skip Tier 3 metrics (adjacent-sentence cosine)."
    )
    # v1.47.0+ (C.4): Tier 4 (surprisal). Opt-IN by default — the
    # extra cost is 1-2 orders of magnitude over Tiers 1-3 and the
    # PROVISIONAL bands need operator calibration to be load-bearing.
    parser.add_argument(
        "--tier4", action="store_true", default=False,
        help=(
            "Enable Tier 4 (surprisal) metrics. OPT-IN by default. "
            "Requires the optional `transformers + torch` install "
            "(see plugins/setec-voiceprint/requirements-surprisal.txt "
            "for the dependency layer and per-accelerator torch-wheel "
            "selection; see plugins/setec-voiceprint/scripts/"
            "calibration/RUNBOOK_tier4_install.md for the full "
            "cross-platform install runbook with decision tree, smoke "
            "test, and fallback ladder) and a causal LM (see "
            "--surprisal-model). PROVISIONAL bands per SPEC §3.5; "
            "calibration_anchor: user-baseline-required."
        ),
    )
    # v1.65.0: AIC-7 / AIC-8 / AIC-9 named-pattern integration per
    # `internal/SPEC_aic_8_9_implementation.md` Step 10. All three
    # are opt-in; default off because they add per-document cost
    # (AIC-8 in particular requires spaCy + word vectors).
    parser.add_argument(
        "--aic7", action="store_true", default=False,
        help=(
            "Enable AIC-7 named-pattern density (Discourse Leak / "
            "Assistant-Register Intrusion). Regex-only; cheap. "
            "Reports per-pattern density per 1000 tokens for "
            "correctio, triplet, manifesto cadence, professional-"
            "parallel stack, plus four nonfiction parallel patterns "
            "and three diagnostic fiction patterns. Registers four "
            "load-bearing signals in COMPRESSION_HEURISTICS; "
            "PROVISIONAL bands."
        ),
    )
    parser.add_argument(
        "--aic8", action="store_true", default=False,
        help=(
            "Enable AIC-8 image conjunction + prestige metaphor "
            "(Aesthetic Authority Laundering). Requires spaCy with "
            "`en_core_web_md` or `_lg` (~50-700 MB) for word vectors "
            "and the Brysbaert concreteness norms (ship in-repo at "
            "`plugins/setec-voiceprint/data/brysbaert_concreteness.csv`). "
            "Registers two load-bearing signals (image_conjunction_"
            "density, prestige_metaphor_scatter); PROVISIONAL bands."
        ),
    )
    parser.add_argument(
        "--aic9", action="store_true", default=False,
        help=(
            "Enable AIC-9 kicker density (Closure Inflation). Regex-"
            "only with optional spaCy POS check; cheap. Registers "
            "one load-bearing signal (kicker_density); PROVISIONAL "
            "bands."
        ),
    )
    parser.add_argument(
        "--surprisal-model", default=None,
        help=(
            "Causal LM alias or HuggingFace id for Tier 4. "
            "Default: tinyllama. See surprisal_backend.MODEL_ALIASES."
        ),
    )
    # Reviewer P2 (2026-05-14): --surprisal-revision parity with the
    # standalone surprisal_audit.py CLI. Pinning a HuggingFace commit
    # SHA in PROVENANCE entries is the reproducibility contract for
    # any load-bearing surprisal calibration. The flag is optional;
    # when omitted, the backend records `revision: null`.
    parser.add_argument(
        "--surprisal-revision", default=None,
        help=(
            "Pin a HuggingFace commit SHA for the Tier 4 causal LM "
            "(reproducibility). Default: revision-less (records "
            "`revision: null` in the Tier 4 backend identifier block)."
        ),
    )
    parser.add_argument(
        "--allow-non-prose", action="store_true",
        help="Skip default corpus-hygiene stripping. Use only when "
             "intentionally auditing code-heavy or markup-heavy text."
    )
    parser.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rules to enable. Default: all "
             "conservative rules. Available conservative rules: "
             + ", ".join(available_rule_names()) + "."
    )
    parser.add_argument(
        "--strip-aggressive", action="store_true",
        help="Also strip URL-only lines, Markdown image URLs, link wrappers, "
             "footnote markers, and high-confidence citations."
    )
    parser.add_argument(
        "--strip-warn-threshold",
        type=float,
        default=0.05,
        help="Emit a stderr warning when preprocessing strips more than "
             "this fraction of tokens from target or any baseline file "
             "(default 0.05)."
    )
    parser.add_argument(
        "--show-stripped",
        nargs="?",
        const="-",
        default=None,
        help="Write stripped target portions to stderr, or to the provided "
             "path when a path is supplied."
    )
    parser.add_argument(
        "--window-size", type=int, default=0,
        help="When > 0, also run a sliding-window pass over the document "
             "with windows of this many words. Surfaces localized "
             "compression that whole-document scores can mask."
    )
    parser.add_argument(
        "--window-stride", type=int, default=0,
        # argparse %-substitutes help strings against a params dict, so
        # any literal % must be doubled to %% to avoid a TypeError on
        # `--help`. (`%o` here would be read as an octal format spec.)
        help="Word stride between sliding windows (default = window-size, "
             "i.e. non-overlapping). Pass window-size // 2 for 50%% overlap."
    )
    parser.add_argument(
        "--window-only", action="store_true",
        help="Skip the whole-document audit and emit only the sliding-"
             "window pass. Requires --window-size > 0."
    )
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="When set with --baseline-dir, replace per-signal z-scores "
             "with empirical percentiles drawn from length-matched windows "
             "of the baseline corpus, plus BCa confidence intervals on "
             "those percentiles via scipy.stats.bootstrap. Slower than the "
             "z-score path because each signal is recomputed on every "
             "window. Requires scipy."
    )
    parser.add_argument(
        "--bootstrap-windows-per-file", type=int, default=50,
        help="Length-matched windows to sample per baseline file "
             "(default 50). Capped by --bootstrap-max-windows."
    )
    parser.add_argument(
        "--bootstrap-max-windows", type=int, default=500,
        help="Total cap on length-matched windows pooled across baseline "
             "files (default 500)."
    )
    parser.add_argument(
        "--bootstrap-resamples", type=int, default=9999,
        help="Bootstrap resamples for the percentile CI (default 9999)."
    )
    parser.add_argument(
        "--bootstrap-confidence", type=float, default=0.95,
        help="Confidence level for the bootstrap CI (default 0.95)."
    )
    parser.add_argument(
        "--bootstrap-seed", type=int, default=None,
        help="Seed for the window sampler and the bootstrap resampler. "
             "Set for reproducible runs."
    )
    parser.add_argument(
        "--ablation", action="store_true",
        help=(
            "Compute leave-one-feature-family-out band calls "
            "(Trustworthiness Tier 2). Tells you which signal "
            "families (syntactic_flattening, lexical_compression, "
            "over_cohesion, connective_overuse) are load-bearing "
            "for the compression call. Closed-form on top of the "
            "main classify_compression result; no extra audit run."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Output JSON only.")
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress human-readable summary."
    )
    args = parser.parse_args()

    if args.window_only and args.window_size <= 0:
        parser.error("--window-only requires --window-size > 0.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))

    text = Path(args.input).read_text(encoding="utf-8", errors="ignore")

    audit = None
    compression = None
    # v1.47.0+ (C.4): Tier 4 backend constructed lazily here so
    # tests that monkeypatch SurprisalBackend.score_text take effect
    # without us having to thread tier4_score_fn through the CLI
    # surface. When --tier4 isn't set, tier4_backend stays None.
    tier4_backend = None
    if args.tier4:
        try:
            from surprisal_backend import (  # type: ignore
                SurprisalBackend,
                resolve_model_arg as _resolve_surprisal_model,
            )
            tier4_backend = SurprisalBackend(
                model_id=_resolve_surprisal_model(args.surprisal_model),
                revision=args.surprisal_revision,
            )
        except ImportError:
            # Leave tier4_backend None; the audit_text path will
            # fall back to its own lazy import and report
            # available=False with a clear reason.
            pass
    if not args.window_only:
        audit = audit_text(
            text,
            mattr_window=args.mattr_window,
            do_tier2=not args.no_tier2,
            do_tier3=not args.no_tier3,
            do_tier4=args.tier4,
            tier4_backend=tier4_backend,
            do_aic7=args.aic7,
            do_aic8=args.aic8,
            do_aic9=args.aic9,
            allow_non_prose=args.allow_non_prose,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
            collect_stripped=args.show_stripped is not None,
        )
        _emit_preprocessing_warning(
            audit.get("preprocessing"),
            label=Path(args.input).name,
            threshold=args.strip_warn_threshold,
        )
        _write_stripped_debug(audit.get("preprocessing"), args.show_stripped)

    output: dict[str, Any] = {"task_surface": TASK_SURFACE}

    # Compute the baseline (and any baseline-derived comparisons) before
    # classification, so POS-bigram KL can participate in the band call
    # when a baseline is supplied. Without a baseline, the band rests on
    # the 11 variance heuristics alone.
    baseline_block: dict[str, Any] | None = None
    z_scores: dict[str, Any] | None = None
    divergences: dict[str, Any] | None = None
    if args.baseline_dir and os.path.isdir(args.baseline_dir):
        baseline_block = audit_baseline(
            args.baseline_dir,
            mattr_window=args.mattr_window,
            do_tier2=not args.no_tier2,
            do_tier3=not args.no_tier3,
            allow_non_prose=args.allow_non_prose,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
        )
        _emit_baseline_preprocessing_warnings(
            baseline_block.get("preprocessing"),
            threshold=args.strip_warn_threshold,
        )
        if audit is not None:
            z_scores = compare_to_baseline(audit, baseline_block)
            divergences = compare_distributions(audit, baseline_block)

    if audit is not None:
        compression = classify_compression(audit, divergences=divergences)
        output["preprocessing"] = audit.get("preprocessing", {})
        output["audit"] = audit
        output["compression"] = compression
        if args.ablation:
            output["ablation"] = ablation_band_calls(compression, audit)

    if baseline_block is not None:
        output["baseline"] = {
            "n_files": baseline_block["n_files"],
            "aggregate": baseline_block["aggregate"],
            "preprocessing": baseline_block.get("preprocessing", {}),
        }
        if z_scores is not None:
            output["baseline_comparison"] = z_scores
        if divergences:
            output["baseline_divergences"] = divergences
        if audit is not None and args.bootstrap:
            output["baseline_bootstrap"] = bootstrap_compare(
                audit, args.baseline_dir,
                n_windows_per_file=args.bootstrap_windows_per_file,
                max_total_windows=args.bootstrap_max_windows,
                n_resamples=args.bootstrap_resamples,
                confidence_level=args.bootstrap_confidence,
                seed=args.bootstrap_seed,
                do_tier2=not args.no_tier2,
                do_tier3=not args.no_tier3,
                mattr_window=args.mattr_window,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
            )

    if args.window_size > 0:
        windows = audit_windows(
            text,
            args.window_size,
            stride=args.window_stride or None,
            baseline=baseline_block,
            do_tier2=not args.no_tier2,
            do_tier3=not args.no_tier3,
            mattr_window=args.mattr_window,
            allow_non_prose=args.allow_non_prose,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
        )
        output["windows"] = {
            "window_size": args.window_size,
            "stride": args.window_stride or args.window_size,
            "n_windows": len(windows),
            "results": windows,
        }

    if args.json:
        print(json.dumps(output, indent=2, default=str))
        return 0

    if not args.quiet:
        if audit is not None:
            print(format_summary(audit, compression))
            if "baseline_comparison" in output:
                print(format_baseline_comparison(output["baseline_comparison"]))
            if "baseline_divergences" in output:
                print(format_baseline_divergences(output["baseline_divergences"]))
            if "baseline_bootstrap" in output:
                print(format_baseline_bootstrap(output["baseline_bootstrap"]))
            if "ablation" in output:
                print("\n".join(format_ablation_block(output["ablation"])))
        if "windows" in output:
            print(format_windows_dashboard(output["windows"]["results"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
