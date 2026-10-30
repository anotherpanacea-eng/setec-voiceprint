#!/usr/bin/env python3
"""period_ruler.py — conformal period-register ruler (Chronologic design, VP-RS1).

Measures how far a SET of candidate passages, each written for a target year,
sits from real prose of that year. It ports the scoring layer of Chronologic-EN
(Underwood et al., arXiv:2609.23178; code MIT,
https://github.com/Historical-AI-Lab/Chronologic-EN) onto SETEC's envelope:

  1. Two frozen instruments score every passage:
       * date channel — a decade-distribution date model; its probability-weighted
         mean year minus the target year is the signed residual;
       * authenticity channel — P(synthetic) from an authentic-vs-LLM-imitation
         classifier.
     Real path: the released MIT checkpoints
     ``chronologic/chronologic-date-deberta`` (temperature 1.0551, as the model
     card states) and ``chronologic/chronologic-authenticity-deberta``, behind a
     lazy torch/transformers import. Model-free path: rows that already carry
     ``date_estimate_year`` / ``p_synthetic`` (a scored cache), or injected
     scorers (tests).
  2. Each candidate is turned into a percentile ``q`` against REAL reference
     passages in the same length bin (quantile cut points on log word count,
     fit on the reference) and within ±``window_years`` of the candidate's
     target year. Ties do not count in the candidate's favour (strict ``<``).
     The instruments' own biases cancel: a writer indistinguishable from the
     period yields uniform q's.
  3. Model-level statistics: W1 distance of the date q's from Uniform(0,1) and
     the mean authenticity q.
  4. Noise floor: ``n_null`` pseudo-models drawn from the reference itself
     (same decade/length-bin profile as the candidates, volume-blocked, each
     reference row scored leave-one-group-out against its own year). Their mean
     W1 / mean q set the 100 point of each 0-100 fidelity score; their 95%
     interval is the noise floor the observed set is read against.

  5. Architecture band, the PRIMARY channel (p22 answers: Fable's Score A with
     GPT Pro's panel amendments; date and authenticity are secondary
     diagnostics that never bar or pass an arm alone): each candidate is cut
     into complete 500-800-word scene windows (paragraph or sentence
     boundaries, scene breaks always close a window); Spec 99-style syntax features
     (root deferral, pre-root load, weighted clause depth, hypotaxis and
     parataxis, mean dependency distance and its IQR, interruptions, sentence
     length mean / IQR / W1 to the pool, long-then-short turns) become mid-rank
     percentiles against REAL windows matched on length, dialogue share and
     narrative mode (thin cells fall back to length and dialogue, then length;
     pool-wide, no date window; reference windows leave their own group out).
     An energy distance of the arm's standardized feature vectors from matched
     real windows is read against a human-vs-human band built from
     work-disjoint halves of the reference. A window is in band
     on a feature between the 10th and 90th percentile; the set reports its
     in-band share, a SIGNED displacement (over or under, never "more is
     better"), W1 of all percentiles from uniform, per-feature readings, and a
     stdlib degradation veto (a repeated word 4-gram, or a distinct-token
     ratio below the real low tail; a rich vocabulary is not degradation),
     calibrated so real windows from an unseen author are vetoed about 5% of
     the time. Vetoed windows are reported as a veto rate beside the in-band
     share, not inside it. Same pseudo-model noise floor. Parser: spaCy en_core_web_sm,
     or ``architecture_features`` precomputed on each row.

Descriptive instrument. It validates a training run; it must never be its
reward (the Chronologic authors warn the authenticity half is easy to inflate
by fine-tuning). It is not a passage-level detector and does not date
individual documents.

TASK_SURFACE: period_register (new fragment claim_license_surfaces/period_register.txt).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.contract.claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "period_register"
TOOL_NAME = "period_ruler"
SCRIPT_VERSION = "1.0"

DATE_MODEL_ID = "chronologic/chronologic-date-deberta"
AUTH_MODEL_ID = "chronologic/chronologic-authenticity-deberta"
# T* from the date model card / stylejudge/e3_temperature_fit.json (t_nll).
DATE_TEMPERATURE = 1.0551
MAX_TOKENS = 256  # both instruments were trained at max_length 256

DEFAULT_WINDOW_YEARS = 10
DEFAULT_LENGTH_BINS = 3
DEFAULT_N_NULL = 2000
DEFAULT_SEED = 1913
# A candidate whose reference cell holds fewer passages than this gets a
# percentile from a thin window; it is kept but counted and warned about.
THIN_CELL = 10
CHANNELS = ("date", "authenticity", "architecture")
MODEL_CHANNELS = ("date", "authenticity")

ScorerFn = Callable[[list[str]], list[float]]


class PeriodRulerError(ValueError):
    """Bad input the CLI reports as a bad_input envelope."""


# ---------------------------------------------------------------------------
# Text and length handling
# ---------------------------------------------------------------------------

_SINGLE_QUOTES = "‘’‚‛′´`"
_DOUBLE_QUOTES = "“”„‟″«»"
_DASHES = "—–―‒‐‑−⁃"


def normalize_typography(text: str) -> str:
    """Fold quotes, dashes and whitespace the way Chronologic's instruments
    were trained (normalize.normalize_typography, simplified): both sides of
    every comparison pass through it, so typography carries no signal."""
    for ch in _SINGLE_QUOTES:
        text = text.replace(ch, "'")
    for ch in _DOUBLE_QUOTES:
        text = text.replace(ch, '"')
    for ch in _DASHES:
        text = text.replace(ch, "-")
    text = text.replace("…", "...").replace("ſ", "s")
    return " ".join(text.split())


def count_words(text: str) -> int:
    return len(text.split())


def fit_length_edges(word_counts: Iterable[int], n_bins: int) -> list[float]:
    """``n_bins - 1`` interior quantile cut points on log(word count)."""
    logs = sorted(math.log(max(n, 1)) for n in word_counts)
    if n_bins <= 1 or not logs:
        return []
    n = len(logs)
    edges = [logs[min(round(n * i / n_bins), n - 1)] for i in range(1, n_bins)]
    # Duplicate cut points (many equal lengths) would leave empty bins.
    return sorted(set(edges))


def length_bin(n_words: int, edges: list[float]) -> int:
    x = math.log(max(n_words, 1))
    return sum(1 for cut in edges if x >= cut)


def decade_of(year: int) -> int:
    return (int(year) // 10) * 10


# ---------------------------------------------------------------------------
# Conformal math (ported from Chronologic stylejudge/typicality.py)
# ---------------------------------------------------------------------------

def q_strict(value: float, reference: list[float]) -> float | None:
    """Fraction of ``reference`` strictly below ``value``; None for an empty
    window. Ties go against the candidate, as in Chronologic's q_score."""
    if not reference:
        return None
    return sum(1 for r in reference if r < value) / len(reference)


def _abs_integral(c: float, a: float, b: float) -> float:
    if c <= a:
        return ((b - c) ** 2 - (a - c) ** 2) / 2.0
    if c >= b:
        return ((c - a) ** 2 - (c - b) ** 2) / 2.0
    return ((c - a) ** 2 + (b - c) ** 2) / 2.0


def wasserstein1_uniform(values: list[float]) -> float:
    """Exact W1 between the empirical distribution of ``values`` in [0,1] and
    Uniform(0,1). 0 is uniform; 0.5 is the maximum."""
    n = len(values)
    if n == 0:
        raise ValueError("wasserstein1_uniform needs at least one value")
    return sum(_abs_integral(x, i / n, (i + 1) / n) for i, x in enumerate(sorted(values)))


def period_fidelity(w_obs: float, w_null_mean: float) -> float:
    """0-100: 100 at or below the pseudo-model mean W1, 0 at W1 = 0.5."""
    denom = 0.5 - w_null_mean
    if denom <= 0:
        raise ValueError(f"degenerate null: mean W1 {w_null_mean!r} >= 0.5")
    d = max(0.0, w_obs - w_null_mean) / denom
    return max(0.0, min(100.0, 100.0 * (1.0 - d)))


def authenticity_fidelity(mean_q: float, null_mean_q: float) -> float:
    """0-100: 100 at or below the pseudo-model mean authenticity q, 0 at 1.
    One-sided: reading as less synthetic than real prose also scores 100."""
    denom = 1.0 - null_mean_q
    if denom <= 0:
        raise ValueError(f"degenerate null: mean q {null_mean_q!r} >= 1")
    d = max(0.0, mean_q - null_mean_q) / denom
    return max(0.0, min(100.0, 100.0 * (1.0 - d)))


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        raise ValueError("empty")
    k = (len(sorted_vals) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


# ---------------------------------------------------------------------------
# Row loading and scoring
# ---------------------------------------------------------------------------

def _row_key(text: str) -> str:
    return hashlib.sha256(normalize_typography(text).encode("utf-8")).hexdigest()


def load_jsonl(path: Path, *, year_field: str, kind: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PeriodRulerError(f"{kind} line {lineno}: invalid JSON ({exc.msg})") from exc
            text = obj.get("text")
            year = obj.get(year_field)
            if not isinstance(text, str) or not text.strip():
                raise PeriodRulerError(f"{kind} line {lineno}: missing non-empty 'text'")
            if isinstance(year, bool) or not isinstance(year, int):
                raise PeriodRulerError(f"{kind} line {lineno}: '{year_field}' must be an integer year")
            row = {
                "id": str(obj.get("id", f"{kind}-{lineno}")),
                "year": year,
                "text": text,
                "n_words": count_words(text),
            }
            if kind == "candidate" and obj.get("author"):
                row["block"] = str(obj["author"])
            if kind == "reference":
                group = obj.get("group", obj.get("volume_id", obj.get("author")))
                if group is None:
                    raise PeriodRulerError(
                        f"reference line {lineno}: needs 'group' (or 'volume_id'/'author') "
                        "so the noise floor can leave a whole source out"
                    )
                row["group"] = str(group)
                # Leave-one-out and the pseudo-model nulls block by author when the
                # reference names one, so between-author variation is in the noise floor.
                row["block"] = str(obj["author"]) if obj.get("author") else row["group"]
            for field in ("date_estimate_year", "p_synthetic"):
                if field in obj and obj[field] is not None:
                    row[field] = float(obj[field])
            if isinstance(obj.get("architecture_features"), dict):
                row["architecture_features"] = obj["architecture_features"]
            rows.append(row)
    return rows


def _load_cache(path: Path | None) -> dict[str, dict[str, float]]:
    cache: dict[str, dict[str, float]] = {}
    if path is None or not path.exists():
        return cache
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                obj = json.loads(line)
                cache.setdefault(obj["key"], {}).update(
                    {k: float(v) for k, v in obj.items() if k != "key" and v is not None})
    return cache


def score_rows(
    rows: list[dict[str, Any]],
    *,
    channels: tuple[str, ...],
    date_scorer: ScorerFn | None,
    auth_scorer: ScorerFn | None,
    cache_path: Path | None = None,
    batch_size: int = 32,
    progress: Callable[[str], None] | None = None,
) -> None:
    """Fill ``date_estimate_year`` / ``p_synthetic`` on rows that lack them.

    Recoverable and continuable: with ``cache_path`` every scored batch is
    appended and flushed, keyed by the sha256 of the normalized text, so a
    killed run resumes where it stopped. Progress goes to ``progress``.
    """
    fields = {"date": ("date_estimate_year", date_scorer),
              "authenticity": ("p_synthetic", auth_scorer)}
    cache = _load_cache(cache_path)
    for row in rows:
        hit = cache.get(_row_key(row["text"]), {})
        for field, _ in fields.values():
            if field not in row and field in hit:
                row[field] = hit[field]
    sink = cache_path.open("a", encoding="utf-8") if cache_path is not None else None
    try:
        for channel in channels:
            field, scorer = fields[channel]
            todo = [r for r in rows if field not in r]
            if not todo:
                continue
            if scorer is None:
                raise PeriodRulerError(f"{len(todo)} rows lack '{field}' and no {channel} scorer is loaded")
            for start in range(0, len(todo), batch_size):
                batch = todo[start:start + batch_size]
                values = scorer([normalize_typography(r["text"]) for r in batch])
                if len(values) != len(batch):
                    raise PeriodRulerError(f"{channel} scorer returned {len(values)} values for {len(batch)} texts")
                for r, v in zip(batch, values):
                    v = float(v)
                    if not math.isfinite(v):
                        raise PeriodRulerError(f"{channel} scorer returned a non-finite value")
                    r[field] = v
                    if sink is not None:
                        sink.write(json.dumps({"key": _row_key(r["text"]), field: v}) + "\n")
                if sink is not None:
                    sink.flush()
                if progress is not None:
                    progress(f"period_ruler: {channel} scored {min(start + batch_size, len(todo))}/{len(todo)}")
    finally:
        if sink is not None:
            sink.close()


# ---------------------------------------------------------------------------
# Windows, q's and the pseudo-model null
# ---------------------------------------------------------------------------

def _index_by_year(rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["year"], []).append(r)
    return out


def _window(by_year, year: int, h: int, lbin: int, exclude_group: str | None = None):
    rows = []
    for y in range(year - h, year + h + 1):
        rows.extend(r for r in by_year.get(y, ())
                    if r["length_bin"] == lbin and r["block"] != exclude_group)
    return rows


def _channel_values(row: dict[str, Any], window: list[dict[str, Any]], target_year: int, channels):
    q_date = q_auth = None
    if "date" in channels:
        resid = row["date_estimate_year"] - target_year
        q_date = q_strict(resid, [w["date_estimate_year"] - w["year"] for w in window])
    if "authenticity" in channels:
        q_auth = q_strict(row["p_synthetic"], [w["p_synthetic"] for w in window])
    return q_date, q_auth


def compute_lovo_qs(reference: list[dict[str, Any]], h: int, channels) -> None:
    """Own-year, own-length-bin, leave-one-group-out q's for every reference
    row: the material pseudo-models are drawn from."""
    by_year = _index_by_year(reference)
    for r in reference:
        window = _window(by_year, r["year"], h, r["length_bin"], exclude_group=r["block"])
        r["lovo_q_date"], r["lovo_q_auth"] = _channel_values(r, window, r["year"], channels)


def _set_stats(q_date: list[float], q_auth: list[float]) -> dict[str, float | None]:
    return {
        "w1": wasserstein1_uniform(q_date) if q_date else None,
        "mean_q_date": _mean(q_date) if q_date else None,
        "mean_abs_q_dev": _mean([abs(q - 0.5) for q in q_date]) if q_date else None,
        "mean_q_auth": _mean(q_auth) if q_auth else None,
    }


def _volume_blocked(cell: dict[str, list[dict[str, Any]]], n: int, rng: random.Random):
    """Round-robin across groups so one long source cannot dominate a draw."""
    shuffled = {g: rng.sample(rows, len(rows)) for g, rows in cell.items() if rows}
    if not shuffled or n <= 0:
        return []
    groups = sorted(shuffled)
    rng.shuffle(groups)
    out, idx = [], {g: 0 for g in groups}
    while len(out) < n:
        for g in groups:
            if len(out) >= n:
                break
            rows = shuffled[g]
            out.append(rows[idx[g] % len(rows)])
            idx[g] += 1
    return out


def simulate_null(reference, profile: Counter, n_null: int, rng: random.Random):
    """``n_null`` pseudo-models with the candidates' (decade, length bin)
    profile, drawn from the reference's own LOVO q's."""
    cells: dict[tuple[int, int], dict[str, list[dict[str, Any]]]] = {}
    for r in reference:
        if r["lovo_q_date"] is None and r["lovo_q_auth"] is None:
            continue
        cells.setdefault((decade_of(r["year"]), r["length_bin"]), {}).setdefault(r["block"], []).append(r)
    draws, unmet = [], 0
    for _ in range(n_null):
        drawn = []
        for cell, n in sorted(profile.items()):
            got = _volume_blocked(cells.get(cell, {}), n, rng)
            unmet += n - len(got)
            drawn.extend(got)
        draws.append(_set_stats([r["lovo_q_date"] for r in drawn if r["lovo_q_date"] is not None],
                                [r["lovo_q_auth"] for r in drawn if r["lovo_q_auth"] is not None]))
    return draws, unmet


# ---------------------------------------------------------------------------
# Architecture band, the primary channel of the panel (p22 answers: Fable's
# Score A with GPT Pro's amendments). Spec 99-style syntax features of complete
# 500-800-word scene windows, cut at paragraph or sentence boundaries, become
# percentiles against real windows matched on length, dialogue share and
# narrative mode; read as a 10-90 band with signed displacement, a
# multivariate energy distance against a work-disjoint human-vs-human band,
# and a stdlib degradation veto. Pool-wide (no date window): one period.
# ---------------------------------------------------------------------------

DEFAULT_NULL_BLOCKS = 1
DEFAULT_SCENE_MIN_WORDS = 500
DEFAULT_SCENE_MAX_WORDS = 800
DIALOGUE_BINS = 3
FIRST_PERSON_PER_100 = 2.0
ENERGY_MAX_WINDOWS = 200
ENERGY_ARM_DRAWS = 20
ENERGY_NULL_DRAWS = 200
BAND_LO, BAND_HI = 0.10, 0.90
VETO_NGRAM, VETO_REPEATS = 4, 3
# Leave-one-author-out veto rate allowed on real reference windows, split
# evenly between the repeated-4-gram and the distinct-token-ratio tests.
VETO_REAL_RATE = 0.05
SUBORD_DEPS = frozenset({"advcl", "ccomp", "xcomp", "acl", "relcl"})
PARA_DEPS = frozenset({"conj", "parataxis"})
_INTERRUPTERS = frozenset({";", ":", "(", ")", "-", "--"})

FeatureFn = Callable[[str], dict[str, float | None]]


_SCENE_BREAK = re.compile(r"^\s*(?:(?:\*\s*){3,}|#{1,3}\s*$|(?:CHAPTER|Chapter|BOOK|Book)\b.*)$")
_SENTENCE_END = re.compile(r"(?<=[.!?][\"')\]])\s+|(?<=[.!?])\s+")


def split_scene_windows(text: str, min_words: int, max_words: int) -> list[str]:
    """Complete windows of ``min_words`` to ``max_words`` words, never cut inside a
    sentence. Paragraph ends are preferred cut points; a scene break (a row of
    asterisks, a chapter heading) always closes the window. A run that cannot meet
    both bounds (a sentence that would carry it past ``max_words`` before it reaches
    ``min_words``, or one sentence longer than ``max_words``) is dropped, so no
    window exceeds ``max_words``. A remainder under ``min_words`` is dropped, unless
    the text has no other window."""
    windows: list[str] = []
    buf: list[str] = []
    n_buf = 0

    def flush(force: bool = False) -> None:
        nonlocal buf, n_buf
        if buf and (n_buf >= min_words or force):
            windows.append(" ".join(buf))
        buf, n_buf = [], 0

    for para in re.split(r"\n\s*\n", text):
        if _SCENE_BREAK.match(para.strip()):
            flush()
            continue
        for sent in (x for x in _SENTENCE_END.split(" ".join(para.split())) if x):
            n = len(sent.split())
            if n > max_words:
                flush()
                buf, n_buf = [], 0
                continue
            if buf and n_buf + n > max_words:
                if n_buf >= min_words:
                    flush()
                else:
                    buf, n_buf = [], 0
            buf.append(sent)
            n_buf += n
        if n_buf >= min_words:
            flush()
    if not windows and buf:
        flush(force=True)
    else:
        flush()
    return windows


_QUOTED = re.compile(r'"[^"]*"')
_FIRST_PERSON = frozenset({"i", "me", "my", "mine", "myself", "we", "us", "our", "ours"})


def narrative_strata(text: str) -> dict[str, float | str]:
    """Dialogue share (words inside double quotes, after typography folding) and
    narrative mode (first person when I/me/my/we... outside quotes reach
    ``FIRST_PERSON_PER_100`` per 100 words)."""
    t = normalize_typography(text)
    total = max(1, len(t.split()))
    quoted = sum(len(m.group(0).split()) for m in _QUOTED.finditer(t))
    outside = [w.strip(".,;:!?'()").lower() for w in _QUOTED.sub(" ", t).split()]
    fp = 100 * sum(w in _FIRST_PERSON for w in outside) / max(1, len(outside))
    return {"dialogue_share": quoted / total,
            "narrative_mode": "first_person" if fp >= FIRST_PERSON_PER_100 else "third_person"}


def _quantile(xs: list[float], p: float) -> float | None:
    return _percentile(sorted(xs), p) if xs else None


def _median(xs: list[float]) -> float | None:
    return _quantile(xs, 0.5)


def _iqr(xs: list[float]) -> float | None:
    return (_quantile(xs, 0.75) - _quantile(xs, 0.25)) if len(xs) >= 4 else None


def _clause_depth(tok) -> int:
    d = 0
    while True:
        if tok.dep_ in SUBORD_DEPS:
            d += 1
        if tok.head.i == tok.i:
            return d
        tok = tok.head


def architecture_features(text: str, nlp) -> dict[str, float | None]:
    """Spec 99-style architecture features, with the p22 answer's continuous
    forms in place of the floored ones (mean dependency distance and its IQR
    for the p90; weighted clause depth; sentence-length IQR). The
    sentence-length distribution itself rides along as ``_sentence_lengths``
    so the band can read its W1 against the reference pool."""
    doc = nlp(normalize_typography(text))
    rd, pre, mdd, hypo, para, depths, lengths, interrupt = [], [], [], [], [], [], [], []
    n_clauses = 0
    for s in doc.sents:
        toks = [t for t in s if not t.is_space]
        words = [t for t in toks if t.is_alpha]
        if len(words) < 2:
            continue
        n = len(toks)
        lengths.append(len(words))
        root = s.root
        if root.pos_ in ("VERB", "AUX"):
            idx = [t.i for t in toks].index(root.i)
            rd.append(idx / n)
            pre.append(idx)
        dists = [abs(t.i - t.head.i) for t in toks if t.head.i != t.i]
        if dists:
            mdd.append(sum(dists) / len(dists))
        heads = [t for t in toks if t.dep_ in SUBORD_DEPS]
        n_clauses += len(heads)
        depths.extend(_clause_depth(t) for t in heads)
        hypo.append(100 * len(heads) / n)
        para.append(100 * sum(t.dep_ in PARA_DEPS for t in toks) / n)
        interrupt.append(sum(t.text in _INTERRUPTERS for t in toks))
    if not lengths:
        return {}
    turns = sum(1 for a, b in zip(lengths, lengths[1:]) if a >= 40 and b <= 12)
    return {
        "root_deferral_median": _median(rd),
        "pre_root_tokens_median": _median(pre),
        "clause_depth_weighted": (sum(depths) / n_clauses) if n_clauses else 0.0,
        "clauses_per_sentence": n_clauses / len(lengths),
        "hypotaxis_per_100_median": _median(hypo),
        "parataxis_per_100_median": _median(para),
        "dependency_distance_mean": _mean(mdd) if mdd else None,
        "dependency_distance_iqr": _iqr(mdd),
        "interruptions_per_sentence": _mean(interrupt),
        "sentence_length_mean": _mean(lengths),
        "sentence_length_iqr": _iqr(lengths),
        "long_short_turns_per_sentence": turns / len(lengths),
        "_sentence_lengths": lengths,
    }


def _load_feature_fn() -> FeatureFn | None:
    try:
        from setec.core.dependency_primitives import _NLP  # type: ignore
    except ImportError:
        return None
    if _NLP is None:
        return None
    return lambda text: architecture_features(text, _NLP)


def degradation_signals(text: str) -> dict[str, float]:
    """Stdlib veto inputs: the most-repeated word 4-gram and the distinct-token ratio."""
    toks = [w.strip(".,;:!?\"'()").lower() for w in text.split()]
    toks = [w for w in toks if w]
    grams = Counter(tuple(toks[i:i + VETO_NGRAM]) for i in range(len(toks) - VETO_NGRAM + 1))
    return {"max_4gram_repeats": max(grams.values()) if grams else 0,
            "distinct_token_ratio": len(set(toks)) / len(toks) if toks else 0.0}


def _quantile_points(xs: list[float], k: int = 50) -> list[float]:
    s = sorted(xs)
    return [_percentile(s, (i + 0.5) / k) for i in range(k)]


def _w1_samples(a: list[float], b_points: list[float]) -> float:
    a_points = _quantile_points(a, len(b_points))
    return _mean([abs(x - y) for x, y in zip(a_points, b_points)])


def q_mid(value: float, reference: list[float]) -> float | None:
    """Mid-rank percentile: ties split evenly, so the band is symmetric."""
    if not reference:
        return None
    below = sum(1 for r in reference if r < value)
    equal = sum(1 for r in reference if r == value)
    return (below + 0.5 * equal) / len(reference)


def build_windows(rows, *, scene_words: tuple[int, int], feature_fn: FeatureFn | None, kind: str):
    """Rows carrying ``architecture_features`` are one window each; others are cut
    into scene windows and featurized."""
    out = []
    for r in rows:
        given = r.get("architecture_features")
        texts = [r["text"]] if given else split_scene_windows(r["text"], *scene_words)
        for i, t in enumerate(texts):
            if given:
                feats = dict(given)
            elif feature_fn is None:
                raise LookupError(f"{kind} {r['id']} has no architecture_features and no parser is loaded")
            else:
                feats = feature_fn(t)
            out.append({"id": f"{r['id']}#{i}", "group": r.get("group"), "block": r.get("block", r.get("group")),
                        "n_words": count_words(t),
                        "features": feats, **degradation_signals(t), **narrative_strata(t)})
    return out


def _match_key(win, pools):
    """The (length, dialogue, mode) cell ``_matched_pool`` settles on for ``win``."""
    keys = [(win["length_bin"], win["dialogue_bin"], win["narrative_mode"]),
            (win["length_bin"], win["dialogue_bin"]), (win["length_bin"],)]
    for key in keys:
        if len(pools.get(key, [])) >= THIN_CELL:
            return key
    return keys[-1]


def _matched_pool(win, pools, exclude_group=None):
    """Reference windows matched on (length bin, dialogue bin, narrative mode),
    falling back to (length, dialogue) and then length alone when a cell holds
    fewer than ``THIN_CELL`` windows. Returns (pool, level)."""
    keys = [(win["length_bin"], win["dialogue_bin"], win["narrative_mode"]),
            (win["length_bin"], win["dialogue_bin"]), (win["length_bin"],)]
    pool: list[dict[str, Any]] = []
    for level, key in enumerate(keys):
        pool = [p for p in pools.get(key, []) if exclude_group is None or p["block"] != exclude_group]
        if len(pool) >= THIN_CELL:
            return pool, level
    return pool, len(keys) - 1


def _arch_percentiles(win, pool, names, len_points):
    qs: dict[str, float | None] = {}
    for name in names:
        v = win["features"].get(name)
        ref = [p["features"][name] for p in pool if p["features"].get(name) is not None]
        qs[name] = q_mid(v, ref) if v is not None else None
    lens = win["features"].get("_sentence_lengths")
    if lens and len_points.get(win["length_bin"]):
        own = _w1_samples(lens, len_points[win["length_bin"]])
        ref = [p["len_w1"] for p in pool if p.get("len_w1") is not None]
        qs["sentence_length_w1_to_pool"] = q_mid(own, ref)
    return qs


def _block_rank_counts(win, pools, names, len_points):
    """Per feature, per source block: (below, equal, n) of the window's value in
    its matched pool, so its percentile can be re-taken without any set of blocks."""
    pool = pools.get(_pool_key(win, pools), [])
    counts: dict[str, dict[str, list[int]]] = {}

    def add(name, value, ref_values):
        per = counts.setdefault(name, {})
        for b, r in ref_values:
            c = per.setdefault(b, [0, 0, 0])
            c[0] += r < value
            c[1] += r == value
            c[2] += 1

    for name in names:
        v = win["features"].get(name)
        if v is not None:
            add(name, v, [(p["block"], p["features"][name]) for p in pool if p["features"].get(name) is not None])
    if win.get("len_w1") is not None:
        add("sentence_length_w1_to_pool", win["len_w1"],
            [(p["block"], p["len_w1"]) for p in pool if p.get("len_w1") is not None])
    return counts


def _pool_key(win, pools):
    keys = [(win["length_bin"], win["dialogue_bin"], win["narrative_mode"]),
            (win["length_bin"], win["dialogue_bin"]), (win["length_bin"],)]
    for key in keys:
        if sum(1 for p in pools.get(key, []) if p["block"] != win["block"]) >= THIN_CELL:
            return key
    return keys[-1]


def _qs_without(win, blocks) -> dict[str, float | None]:
    qs: dict[str, float | None] = {}
    for name in win["qs"]:
        per = win["q_counts"].get(name)
        if not per:
            qs[name] = None
            continue
        below = equal = n = 0
        for b, (lo, eq, k) in per.items():
            if b not in blocks:
                below, equal, n = below + lo, equal + eq, n + k
        # No reference window left once the sources are excluded: no rank.
        qs[name] = (below + 0.5 * equal) / n if n else None
    return qs


def _calibrate_veto(ref_w, pool_by_bin):
    """Veto thresholds set from real windows with their own author left out:
    the smallest 4-gram repeat count (at least ``VETO_REPEATS``) and the
    highest low-tail cut on the distinct-token ratio each veto at most half of
    ``VETO_REAL_RATE``. The ratio veto is one-sided: degradation (loops,
    repetition) lowers the ratio. When no cut meets its budget (one author's
    ratios sit below every other author's), the ratio veto is switched off
    (tail ``None``) rather than left vetoing that author. Returns (repeats,
    tail, band for candidates, band per (bin, block), achieved real rates)."""
    half = VETO_REAL_RATE / 2
    repeats = VETO_REPEATS
    while sum(w["max_4gram_repeats"] >= repeats for w in ref_w) > half * len(ref_w):
        repeats += 1
    others: dict[tuple, list[float]] = {}
    for b, ws in pool_by_bin.items():
        for block in {w["block"] for w in ws}:
            others[(b, block)] = sorted(w["distinct_token_ratio"] for w in ws if w["block"] != block)

    def bands(tail):
        full = {b: (_percentile(sorted(w["distinct_token_ratio"] for w in ws), tail), math.inf)
                for b, ws in pool_by_bin.items()}
        loao = {k: ((_percentile(v, tail), math.inf) if v else full[k[0]]) for k, v in others.items()}
        return full, loao

    for step in range(21):
        tail = 0.05 - step * 0.0025
        full, loao = bands(tail)
        out = sum(w["distinct_token_ratio"] < loao[(w["length_bin"], w["block"])][0] for w in ref_w)
        if out <= half * len(ref_w):
            break
    else:
        tail = None
        full = {b: (-math.inf, math.inf) for b in pool_by_bin}
        loao = {k: (-math.inf, math.inf) for k in others}
        out = 0
    n = len(ref_w) or 1
    achieved = {"repeats": sum(w["max_4gram_repeats"] >= repeats for w in ref_w) / n,
                "distinct_token_ratio": out / n}
    return repeats, tail, full, loao, achieved


def _vetoed(win, band, repeats) -> bool:
    lo, hi = band
    return win["max_4gram_repeats"] >= repeats or not lo <= win["distinct_token_ratio"] <= hi


def _arm_stats(wins: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not wins:
        return None
    shares, disp, pooled = [], [], []
    vetoed = ranked_or_vetoed = vetoed_ranks = 0
    for w in wins:
        qs = [q for q in w["qs"].values() if q is not None]
        if w["vetoed"]:
            vetoed += 1
            ranked_or_vetoed += 1
            vetoed_ranks += len(qs) or len(w["qs"])
            continue
        if not qs:
            continue
        ranked_or_vetoed += 1
        shares.append(sum(BAND_LO <= q <= BAND_HI for q in qs) / len(qs))
        disp.append(_mean([100 * (q - 0.5) for q in qs]))
        pooled.extend(qs)
    return {
        "in_band_share": _mean(shares) if shares else None,
        # Vetoed windows counted as out of band: turning an outlying window into
        # a vetoed one can only lower this, never raise it.
        "in_band_share_with_vetoes": sum(shares) / ranked_or_vetoed if ranked_or_vetoed else None,
        "signed_displacement": _mean(disp) if disp else None,
        "w1_from_uniform": wasserstein1_uniform(pooled) if pooled else None,
        "w1_from_uniform_with_vetoes": _w1_with_vetoes(pooled, vetoed_ranks),
        "veto_rate": sum(w["vetoed"] for w in wins) / len(wins),
    }


def _w1_with_vetoes(pooled: list[float], vetoed_ranks: int) -> float | None:
    """Upper bound on W1 from uniform over every placement of the vetoed ranks.
    W1 to a fixed distribution is convex in the measure, so a share ``a`` of
    unknown ranks moves it at most to (1 - a) * W1(rest) + a * 0.5 (0.5 is the
    largest W1 any mass can have). Turning any window into a vetoed one can
    therefore never lower this below the W1 it had before."""
    if not vetoed_ranks:
        return wasserstein1_uniform(pooled) if pooled else None
    if not pooled:
        return 0.5
    a = vetoed_ranks / (len(pooled) + vetoed_ranks)
    return (1 - a) * wasserstein1_uniform(pooled) + a * 0.5


def _standardizer(ref_w, names):
    stats = {}
    for name in names:
        vals = [w["features"][name] for w in ref_w if w["features"].get(name) is not None]
        mu = _mean(vals)
        sd = math.sqrt(_mean([(v - mu) ** 2 for v in vals])) or 1.0
        stats[name] = (mu, sd)

    def vec(w):
        return [((w["features"].get(n) if w["features"].get(n) is not None else mu) - mu) / sd
                for n, (mu, sd) in stats.items()]
    return vec


def _mean_dist(xs, ys) -> float:
    return _mean([math.sqrt(sum((a - b) ** 2 for a, b in zip(x, y))) for x in xs for y in ys])


def energy_distance(xs: list[list[float]], ys: list[list[float]]) -> float:
    """Szekely-Rizzo energy distance (V-statistic, never negative) between two
    samples of feature vectors: 2E|X-Y| - E|X-X'| - E|Y-Y'|. Zero when the
    samples coincide; it sees spread as well as location."""
    return 2 * _mean_dist(xs, ys) - _mean_dist(xs, xs) - _mean_dist(ys, ys)


def _draw_matched(profile_wins, pools, rng, groups=None):
    """One reference window per profile window from its matched pool (optionally
    restricted to ``groups``)."""
    out = []
    for w in profile_wins:
        pool, _ = _matched_pool(w, pools)
        if groups is not None:
            pool = [p for p in pool if p["block"] in groups]
        if pool:
            out.append(rng.choice(pool))
    return out


def run_architecture_band(
    candidates, reference, *, feature_fn: FeatureFn | None, scene_words: tuple[int, int],
    n_length_bins: int, n_null: int, rng: random.Random, include_per_candidate: bool,
    null_blocks: int = DEFAULT_NULL_BLOCKS,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    ref_w = build_windows(reference, scene_words=scene_words, feature_fn=feature_fn, kind="reference")
    cand_w = build_windows(candidates, scene_words=scene_words, feature_fn=feature_fn, kind="candidate")
    if not ref_w or not cand_w:
        raise PeriodRulerError("architecture band needs at least one reference and one candidate window")
    covered = {w["id"].rsplit("#", 1)[0] for w in cand_w}
    uncut = sum(1 for r in candidates if r["id"] not in covered)
    if uncut:
        warnings.append(f"{uncut} candidate passage(s) yielded no {scene_words[0]}-{scene_words[1]}-word "
                        "scene window and were left out of the architecture band")
    edges = fit_length_edges((w["n_words"] for w in ref_w), n_length_bins)
    shares = sorted(w["dialogue_share"] for w in ref_w)
    d_edges = sorted({_percentile(shares, i / DIALOGUE_BINS) for i in range(1, DIALOGUE_BINS)})
    for w in ref_w + cand_w:
        w["length_bin"] = length_bin(w["n_words"], edges)
        w["dialogue_bin"] = sum(1 for cut in d_edges if w["dialogue_share"] > cut)
    pool_by_bin: dict[int, list[dict[str, Any]]] = {}
    pools: dict[tuple, list[dict[str, Any]]] = {}
    for w in ref_w:
        pool_by_bin.setdefault(w["length_bin"], []).append(w)
        for key in ((w["length_bin"], w["dialogue_bin"], w["narrative_mode"]),
                    (w["length_bin"], w["dialogue_bin"]), (w["length_bin"],)):
            pools.setdefault(key, []).append(w)
    names = sorted({k for w in ref_w for k, v in w["features"].items()
                    if not k.startswith("_") and v is not None}
                   & {k for w in cand_w for k in w["features"] if not k.startswith("_")})
    if not names:
        raise PeriodRulerError("candidates and reference share no architecture features")
    len_points = {b: _quantile_points([n for w in ws for n in w["features"].get("_sentence_lengths") or []])
                  for b, ws in pool_by_bin.items()
                  if any(w["features"].get("_sentence_lengths") for w in ws)}
    for w in ref_w:
        lens = w["features"].get("_sentence_lengths")
        w["len_w1"] = _w1_samples(lens, len_points[w["length_bin"]]) if lens and w["length_bin"] in len_points else None
    veto_repeats, veto_tail, veto_band, loao_veto_band, veto_achieved = _calibrate_veto(ref_w, pool_by_bin)
    if veto_tail is None:
        warnings.append("the distinct-token-ratio veto was switched off: no cut kept real windows from "
                        f"unseen authors under a {100 * VETO_REAL_RATE / 2:g}% veto rate")
    for w in ref_w:
        pool, _ = _matched_pool(w, pools, exclude_group=w["block"])
        w["qs"] = _arch_percentiles(w, pool, names, len_points)
        w["q_counts"] = _block_rank_counts(w, pools, names, len_points)
        w["vetoed"] = _vetoed(w, loao_veto_band[(w["length_bin"], w["block"])], veto_repeats)
    match_levels: Counter = Counter()
    for w in cand_w:
        pool, level = _matched_pool(w, pools)
        match_levels[level] += 1
        w["qs"] = _arch_percentiles(w, pool, names, len_points)
        w["vetoed"] = _vetoed(w, veto_band.get(w["length_bin"], (0.0, 1.0)), veto_repeats)
    if match_levels[1] or match_levels[2]:
        warnings.append(f"{match_levels[1] + match_levels[2]} candidate window(s) were matched on length "
                        "(and dialogue) only, because their full length/dialogue/mode cell was thin")

    # Multivariate read: energy distance of the arm from matched real windows,
    # against a human-vs-human band from work-disjoint halves of the reference.
    vec = _standardizer(ref_w, names)
    live = [w for w in cand_w if not w["vetoed"]]
    sample = rng.sample(live, min(len(live), ENERGY_MAX_WINDOWS)) if live else []
    energy = energy_band = None
    if len(sample) >= 2:
        arm = [_draw_matched(sample, pools, rng) for _ in range(ENERGY_ARM_DRAWS)]
        energy = _mean([energy_distance([vec(w) for w in sample], [vec(r) for r in d]) for d in arm if d])
        groups = sorted({w["block"] for w in ref_w})
        null_e = []
        for _ in range(ENERGY_NULL_DRAWS):
            rng.shuffle(groups)
            half_a, half_b = set(groups[: len(groups) // 2]), set(groups[len(groups) // 2:])
            a = _draw_matched(sample, pools, rng, groups=half_a)
            b = _draw_matched(sample, pools, rng, groups=half_b)
            if len(a) >= 2 and len(b) >= 2:
                null_e.append(energy_distance([vec(r) for r in a], [vec(r) for r in b]))
        if null_e:
            null_e.sort()
            energy_band = [_percentile(null_e, 0.5), _percentile(null_e, 0.95)]

    observed = _arm_stats(cand_w)
    # Pseudo-model draws follow the candidates' full matching profile (length,
    # dialogue, mode, with the same thin-cell fallback), so the noise band does
    # not move with the reference's mixture of cells.
    profile = Counter(_match_key(w, pools) for w in cand_w)
    cells: dict[tuple, dict[str, list[dict[str, Any]]]] = {}
    for key in profile:
        for w in pools.get(key, []):
            cells.setdefault(key, {}).setdefault(w["block"], []).append(w)
    # Each pseudo-model writes like as many real sources (authors when the
    # reference names them) as the arm has: its named authors when every
    # candidate names one, else ``null_blocks`` (default 1: a generated arm is
    # one voice, not an average of every reference author).
    all_blocks = sorted({w["block"] for w in ref_w})
    cand_sources = {r.get("block") for r in candidates}
    from_candidates = None not in cand_sources
    k_blocks = len(cand_sources) if from_candidates else null_blocks
    k_blocks = max(1, min(k_blocks, len(all_blocks)))
    null = []
    widened = starved = 0
    for _ in range(n_null):
        chosen = set(rng.sample(all_blocks, k_blocks))
        drawn = []
        for key, n in sorted(profile.items()):
            cell = {b: ws for b, ws in cells.get(key, {}).items() if b in chosen}
            if not cell:
                cell = cells.get(key, {})
                widened += 1
            drawn.extend(_volume_blocked(cell, n, rng))
        # A pseudo-model is held out the way the arm is: every window is ranked
        # against the reference without any source it actually drew from
        # (the chosen ones, plus any a thin cell widened to).
        sources = chosen | {w["block"] for w in drawn}
        reranked = [{**w, "qs": _qs_without(w, sources)} for w in drawn]
        if any(all(q is None for q in w["qs"].values()) for w in reranked):
            starved += 1
        stats = _arm_stats(reranked)
        if stats:
            null.append(stats)
    if not null:
        raise PeriodRulerError("pseudo-model null is empty for the architecture band")
    if starved > 0.05 * n_null:
        raise PeriodRulerError(
            f"{starved} of {n_null} pseudo-model draws left some window with no reference window from "
            "another source; the reference has too few sources per matching cell for a source-disjoint null")
    if widened:
        warnings.append(f"{widened} pseudo-model cell draw(s) had no window from the chosen "
                        f"{k_blocks} source(s) and drew from every source instead")

    def interval(key):
        vals = sorted(s[key] for s in null if s[key] is not None)
        return [_percentile(vals, 0.025), _percentile(vals, 0.975)] if vals else None

    share_iv, disp_iv, w1_iv = interval("in_band_share"), interval("signed_displacement"), interval("w1_from_uniform")
    veto_iv = interval("veto_rate")
    # Vetoed windows leave the reported statistics, so noise is judged on
    # veto-inclusive ones (a vetoed window is out of band, and W1 takes the
    # worst case over wherever its ranks could have been) against the null's clean floors: degraded output can
    # never improve the reading by being vetoed, whatever the null's own veto
    # ceiling.
    with_vetoes = observed["in_band_share_with_vetoes"]
    w1_vetoes = observed["w1_from_uniform_with_vetoes"]
    beyond = bool(
        with_vetoes is None
        or observed["veto_rate"] > veto_iv[1]
        or (share_iv and with_vetoes < share_iv[0])
        or (w1_vetoes is not None and w1_iv and w1_vetoes > w1_iv[1]))
    direction = "inside"
    if observed["signed_displacement"] is not None and disp_iv:
        if observed["signed_displacement"] > disp_iv[1]:
            direction = "over"
        elif observed["signed_displacement"] < disp_iv[0]:
            direction = "under"
    all_names = names + (["sentence_length_w1_to_pool"] if len_points else [])
    per_feature = {}
    for name in all_names:
        qs = [w["qs"].get(name) for w in cand_w if not w["vetoed"] and w["qs"].get(name) is not None]
        per_feature[name] = {
            "in_band_share": sum(BAND_LO <= q <= BAND_HI for q in qs) / len(qs) if qs else None,
            "signed_displacement": _mean([100 * (q - 0.5) for q in qs]) if qs else None,
            "n_windows": len(qs),
        }
    if any(not w["features"] for w in cand_w):
        warnings.append("some candidate windows yielded no parseable sentences")
    block = {
        "role": "primary",
        **observed,
        "energy_distance": energy,
        "human_vs_human_energy_median_and_95th": energy_band,
        "energy_band": (None if energy is None or energy_band is None else
                        "beyond_human_vs_human" if energy > energy_band[1] else "within_human_vs_human"),
        "null_in_band_share_interval_95": share_iv,
        "null_signed_displacement_interval_95": disp_iv,
        "null_w1_interval_95": w1_iv,
        "null_veto_rate_interval_95": veto_iv,
        "band": "beyond_reference_noise" if beyond else "within_reference_noise",
        "displacement_direction": direction,
        "per_feature": per_feature,
        "n_candidate_windows": len(cand_w),
        "n_reference_windows": len(ref_w),
        "null_sources_per_pseudo_model": k_blocks,
        "null_sources_from": "candidate_authors" if from_candidates else "null_blocks",
        "null_blocked_by": ("author" if any(w["block"] != w["group"] for w in ref_w) else "group"),
        "scene_window_words": list(scene_words),
        "dialogue_bin_edges": d_edges,
        "candidate_match_levels": {"length_dialogue_mode": match_levels[0],
                                   "length_dialogue": match_levels[1], "length_only": match_levels[2]},
        "band_percentiles": [BAND_LO, BAND_HI],
        "length_bin_edges_log_words": edges,
        "veto": {"repeated_4gram_at": veto_repeats,
                 "distinct_token_ratio_tail": veto_tail,
                 "distinct_token_ratio": ("switched off (no cut met its budget)" if veto_tail is None else
                                          f"below the reference {100 * veto_tail:g}th percentile for the "
                                          "length bin (each author left out of its own)"),
                 "target_real_veto_rate": VETO_REAL_RATE,
                 "achieved_real_veto_rate": veto_achieved,
                 "vetoed_windows": "reported as veto_rate, left out of in_band_share and displacement"},
    }
    if include_per_candidate:
        block["per_window"] = [{"id": w["id"], "length_bin": w["length_bin"], "dialogue_share": w["dialogue_share"],
                                "narrative_mode": w["narrative_mode"], "vetoed": w["vetoed"],
                                "percentiles": w["qs"]} for w in cand_w]
    return block, warnings


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_period_ruler(
    candidates: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    *,
    channels: tuple[str, ...] = CHANNELS,
    date_scorer: ScorerFn | None = None,
    auth_scorer: ScorerFn | None = None,
    feature_fn: FeatureFn | None = None,
    scene_words: tuple[int, int] = (DEFAULT_SCENE_MIN_WORDS, DEFAULT_SCENE_MAX_WORDS),
    window_years: int = DEFAULT_WINDOW_YEARS,
    n_length_bins: int = DEFAULT_LENGTH_BINS,
    n_null: int = DEFAULT_N_NULL,
    seed: int = DEFAULT_SEED,
    include_per_candidate: bool = False,
    null_blocks: int = DEFAULT_NULL_BLOCKS,
    cache_path: Path | None = None,
    batch_size: int = 32,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not candidates:
        raise PeriodRulerError("no candidate passages")
    for r in reference:
        r.setdefault("block", str(r["author"]) if r.get("author") else r["group"])
    if len({r["block"] for r in reference}) < 2:
        raise PeriodRulerError("reference needs passages from at least two groups (or authors) for a "
                               "leave-one-out null")
    if n_null < 100:
        raise PeriodRulerError("n_null must be at least 100")
    warnings: list[str] = []
    want_arch = "architecture" in channels
    channels = tuple(c for c in channels if c in MODEL_CHANNELS)
    rng = random.Random(seed)
    block: dict[str, Any] = {"channels": {}, "n_candidates": len(candidates), "n_reference": len(reference),
                             "n_reference_groups": len({r["group"] for r in reference}),
                             "n_reference_blocks": len({r["block"] for r in reference}),
                             "n_pseudo_models": n_null, "seed": seed, "calibration_status": "provisional"}
    if channels:
        score_rows(candidates + reference, channels=channels, date_scorer=date_scorer,
                   auth_scorer=auth_scorer, cache_path=cache_path, batch_size=batch_size,
                   progress=progress)

        edges = fit_length_edges((r["n_words"] for r in reference), n_length_bins)
        for r in candidates + reference:
            r["length_bin"] = length_bin(r["n_words"], edges)
        compute_lovo_qs(reference, window_years, channels)

        by_year = _index_by_year(reference)
        per_candidate, q_date, q_auth = [], [], []
        drift_years: list[float] = []
        profile: Counter = Counter()
        thin = empty = 0
        for c in candidates:
            window = _window(by_year, c["year"], window_years, c["length_bin"])
            if not window:
                empty += 1
                continue
            if len(window) < THIN_CELL:
                thin += 1
            qd, qa = _channel_values(c, window, c["year"], channels)
            if qd is not None:
                q_date.append(qd)
                local_bias = _mean([w["date_estimate_year"] - w["year"] for w in window])
                drift_years.append(c["date_estimate_year"] - c["year"] - local_bias)
            if qa is not None:
                q_auth.append(qa)
            profile[(decade_of(c["year"]), c["length_bin"])] += 1
            per_candidate.append({
                "id": c["id"], "target_year": c["year"], "n_words": c["n_words"],
                "length_bin": c["length_bin"], "q_date": qd, "q_authenticity": qa,
                "window_passages": len(window), "window_groups": len({w["group"] for w in window}),
            })
        if not per_candidate:
            raise PeriodRulerError(
                f"no candidate has reference passages within ±{window_years} years in its length bin")
        if empty:
            warnings.append(f"{empty} candidate(s) had no reference passages in their date/length cell and were left out")
        if thin:
            warnings.append(f"{thin} candidate(s) were scored against fewer than {THIN_CELL} reference passages")

        rng = random.Random(seed)
        null, unmet = simulate_null(reference, profile, n_null, rng)
        if unmet:
            warnings.append(f"{unmet} pseudo-model slots across {n_null} draws had no reference rows to draw from")
        observed = _set_stats(q_date, q_auth)

        channel_blocks: dict[str, Any] = {}
        if "date" in channels:
            w_null = sorted(d["w1"] for d in null if d["w1"] is not None)
            if not w_null:
                raise PeriodRulerError("pseudo-model null is empty for the date channel")
            w0 = _mean(w_null)
            null_scores = sorted(period_fidelity(w, w0) for w in w_null)
            hi = _percentile(w_null, 0.975)
            channel_blocks["date"] = {
                "role": "secondary_diagnostic",
                "period_fidelity": period_fidelity(observed["w1"], w0),
                "w1_from_uniform": observed["w1"],
                "null_w1_mean": w0,
                "null_w1_interval_95": [_percentile(w_null, 0.025), hi],
                "null_period_fidelity_interval_95": [_percentile(null_scores, 0.025),
                                                     _percentile(null_scores, 0.975)],
                "mean_q": observed["mean_q_date"],
                "mean_abs_q_deviation": observed["mean_abs_q_dev"],
                "drift_years_bias_corrected": _mean(drift_years),
                "band": "beyond_reference_noise" if observed["w1"] > hi else "within_reference_noise",
                "n_scored": len(q_date),
            }
        if "authenticity" in channels:
            a_null = sorted(d["mean_q_auth"] for d in null if d["mean_q_auth"] is not None)
            if not a_null:
                raise PeriodRulerError("pseudo-model null is empty for the authenticity channel")
            mu0 = _mean(a_null)
            null_scores = sorted(authenticity_fidelity(a, mu0) for a in a_null)
            hi = _percentile(a_null, 0.975)
            channel_blocks["authenticity"] = {
                "role": "secondary_diagnostic",
                "authenticity_fidelity": authenticity_fidelity(observed["mean_q_auth"], mu0),
                "mean_q": observed["mean_q_auth"],
                "null_mean_q": mu0,
                "null_mean_q_interval_95": [_percentile(a_null, 0.025), hi],
                "null_authenticity_fidelity_interval_95": [_percentile(null_scores, 0.025),
                                                           _percentile(null_scores, 0.975)],
                "band": "beyond_reference_noise" if observed["mean_q_auth"] > hi else "within_reference_noise",
                "n_scored": len(q_auth),
            }

        cells = [
            {"target_decade": dec, "length_bin": lb, "n_candidates": n,
             "n_reference_passages": sum(1 for r in reference
                                         if decade_of(r["year"]) == dec and r["length_bin"] == lb)}
            for (dec, lb), n in sorted(profile.items())
        ]
        block: dict[str, Any] = {
            "channels": channel_blocks,
            "n_candidates": len(candidates),
            "n_candidates_in_reference_cells": len(per_candidate),
            "n_reference": len(reference),
            "n_reference_groups": len({r["group"] for r in reference}),
            "n_reference_blocks": len({r["block"] for r in reference}),
            "window_years": window_years,
            "length_bin_edges_log_words": edges,
            "n_pseudo_models": n_null,
            "seed": seed,
            "cells": cells,
            "calibration_status": "provisional",
            "assumptions": {
                "method": "Chronologic conformal scoring (arXiv:2609.23178): length-binned, date-windowed "
                          "percentiles vs real passages; W1 from uniform; pseudo-model noise floor",
                "instruments": f"{DATE_MODEL_ID} (T={DATE_TEMPERATURE}) and {AUTH_MODEL_ID}, or precomputed scores",
                "reference": "operator-supplied dated real passages with a source group per passage; "
                             "leave-one-out and nulls block by author when rows name one",
                "use": "validator for a set of generations; never a training reward",
            },
        }
        if include_per_candidate:
            block["per_candidate"] = per_candidate
    if want_arch:
        arch_block, arch_warnings = run_architecture_band(
            candidates, reference, feature_fn=feature_fn, scene_words=scene_words,
            n_length_bins=n_length_bins, n_null=n_null, rng=rng,
            include_per_candidate=include_per_candidate, null_blocks=null_blocks)
        block["channels"]["architecture"] = arch_block
        warnings.extend(arch_warnings)
        block.setdefault("assumptions", {})["architecture"] = (
            "primary channel (p22 panel): Spec 99-style syntax features of complete 500-800-word "
            "scene windows as mid-rank percentiles against real windows matched on length, "
            "dialogue share and narrative mode (leave-one-group-out for the reference), 10-90 "
            "band, signed displacement, energy distance against a work-disjoint human band, "
            "stdlib degradation veto; pool-wide, no date window")
        block["assumptions"]["reading_rule"] = (
            "architecture is the primary read; date and authenticity are secondary diagnostics "
            "and never bar or pass an arm alone")
    return {"period_ruler": block}, warnings


# ---------------------------------------------------------------------------
# Real instruments (M2: torch + transformers, never loaded in CI)
# ---------------------------------------------------------------------------

class _ChronologicDate:
    """Probability-weighted mean year from chronologic-date-deberta."""

    def __init__(self, device: str | None = None) -> None:
        import torch  # type: ignore
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore

        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(DATE_MODEL_ID)
        self._model = AutoModelForSequenceClassification.from_pretrained(DATE_MODEL_ID)
        self._model.eval()
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model.to(self._device)
        cfg = self._model.config
        lo, width = int(cfg.date_grid_lo), int(cfg.date_bin_width)
        self._mid = torch.tensor([lo + width * i + width / 2 for i in range(cfg.num_labels)],
                                 dtype=torch.float32)

    def __call__(self, texts: list[str]) -> list[float]:
        torch = self._torch
        enc = self._tok(texts, padding=True, truncation=True, max_length=MAX_TOKENS,
                        return_tensors="pt").to(self._device)
        with torch.no_grad():
            logits = self._model(**enc).logits.float().cpu() / DATE_TEMPERATURE
        probs = torch.softmax(logits, dim=-1)
        return (probs * self._mid).sum(dim=-1).tolist()


class _ChronologicAuthenticity:
    """P(synthetic) from chronologic-authenticity-deberta (sigmoid of one logit)."""

    def __init__(self, device: str | None = None) -> None:
        import torch  # type: ignore
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore

        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(AUTH_MODEL_ID)
        self._model = AutoModelForSequenceClassification.from_pretrained(AUTH_MODEL_ID)
        self._model.eval()
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model.to(self._device)

    def __call__(self, texts: list[str]) -> list[float]:
        torch = self._torch
        enc = self._tok(texts, padding=True, truncation=True, max_length=MAX_TOKENS,
                        return_tensors="pt").to(self._device)
        with torch.no_grad():
            logits = self._model(**enc).logits.float().cpu().squeeze(-1)
        return torch.sigmoid(logits).reshape(-1).tolist()


def _load_scorers(channels, device):
    """Return {channel: scorer} or None when torch/transformers are absent."""
    try:
        import torch  # type: ignore  # noqa: F401
        import transformers  # type: ignore  # noqa: F401
    except ImportError:
        return None
    out: dict[str, ScorerFn] = {}
    if "date" in channels:
        out["date"] = _ChronologicDate(device)
    if "authenticity" in channels:
        out["authenticity"] = _ChronologicAuthenticity(device)
    return out


# ---------------------------------------------------------------------------
# Claim license and CLI
# ---------------------------------------------------------------------------

def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "A model-level description of how a SET of candidate passages, each written for a "
            "target year, sits against length-matched real passages from within the date window, "
            "on a date channel (W1 of percentiles from uniform) and an authenticity channel (mean "
            "percentile) and an architecture band (share of syntax-feature percentiles inside the "
            "10-90 band of length-matched real windows, with signed displacement), each read "
            "against a pseudo-model noise floor drawn from the reference. "
            "Fidelity scores are relative to the supplied reference corpus and instruments."
        ),
        "does_not_license": (
            "This does NOT date an individual document, does NOT decide whether any passage was "
            "machine-written, and does NOT license an authorship or provenance claim. It is a "
            "validator: do not use it as a training reward or selection objective (the authenticity "
            "channel is easy to inflate by fine-tuning, per arXiv:2609.23178). Scores from a "
            "different reference corpus or instrument are not comparable to published Chronologic "
            "scores. Calibration is PROVISIONAL."
        ),
    }


def _parse_channels(value: str) -> tuple[str, ...]:
    parts = tuple(p.strip() for p in value.split(",") if p.strip())
    bad = [p for p in parts if p not in CHANNELS]
    if bad or not parts:
        raise argparse.ArgumentTypeError(f"channels must be a comma list of {CHANNELS}")
    return tuple(c for c in CHANNELS if c in parts)


def _error(reason: str, category: str, target: str | None = None) -> dict[str, Any]:
    return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                              target_path=target, reason=reason, reason_category=category)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        candidates = load_jsonl(Path(args.candidates), year_field="target_year", kind="candidate")
        reference = load_jsonl(Path(args.reference), year_field="year", kind="reference")
    except OSError as exc:
        return _error(f"cannot read input: {exc}", "bad_input", args.candidates)
    except PeriodRulerError as exc:
        return _error(str(exc), "bad_input", args.candidates)

    fields = {"date": "date_estimate_year", "authenticity": "p_synthetic"}
    cache = Path(args.score_cache) if args.score_cache else None
    cached = _load_cache(cache)
    need_model = any(fields[ch] not in r and fields[ch] not in cached.get(_row_key(r["text"]), {})
                     for ch in args.channels if ch in MODEL_CHANNELS for r in candidates + reference)
    scorers: dict[str, ScorerFn] = {}
    if need_model:
        loaded = _load_scorers(args.channels, args.device)
        if loaded is None:
            return _error(
                "Some passages carry no precomputed scores and torch + transformers are not "
                "installed. Install the surprisal tier (pip install -r requirements-surprisal.txt) "
                f"to load {DATE_MODEL_ID} / {AUTH_MODEL_ID}, or supply date_estimate_year / "
                "p_synthetic on every row (or a --score-cache).",
                "missing_dependency", args.candidates)
        scorers = loaded

    feature_fn = None
    if "architecture" in args.channels and any(
            "architecture_features" not in r for r in candidates + reference):
        feature_fn = _load_feature_fn()
        if feature_fn is None:
            return _error(
                "The architecture channel needs spaCy with en_core_web_sm to parse windows "
                "(pip install spacy && python -m spacy download en_core_web_sm), or "
                "architecture_features on every row; or drop it with --channels date,authenticity.",
                "missing_dependency", args.candidates)

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        results, warnings = run_period_ruler(
            candidates, reference, channels=args.channels,
            date_scorer=scorers.get("date"), auth_scorer=scorers.get("authenticity"),
            feature_fn=feature_fn, scene_words=(args.scene_min_words, args.scene_max_words),
            window_years=args.window_years, n_length_bins=args.length_bins,
            n_null=args.n_null, seed=args.seed, include_per_candidate=args.per_candidate,
            null_blocks=args.null_blocks,
            cache_path=cache, batch_size=args.batch_size, progress=progress)
    except PeriodRulerError as exc:
        return _error(str(exc), "bad_input", args.candidates)

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=args.candidates,
        target_words=sum(r["n_words"] for r in candidates),
        baseline={"n_files": len(reference), "words": sum(r["n_words"] for r in reference)},
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
        validate_bounds=True,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True,
                    help="JSONL of candidate passages: {id, text, target_year[, date_estimate_year, p_synthetic]}")
    ap.add_argument("--reference", required=True,
                    help="JSONL of real dated passages: {id, text, year, group[, date_estimate_year, p_synthetic]}")
    ap.add_argument("--channels", type=_parse_channels, default=CHANNELS,
                    help="comma list from: date,authenticity,architecture (default all three)")
    ap.add_argument("--scene-min-words", type=int, default=DEFAULT_SCENE_MIN_WORDS,
                    help="architecture band: shortest complete scene window (default 500 words)")
    ap.add_argument("--scene-max-words", type=int, default=DEFAULT_SCENE_MAX_WORDS,
                    help="architecture band: longest scene window before a cut (default 800 words)")
    ap.add_argument("--window-years", type=int, default=DEFAULT_WINDOW_YEARS)
    ap.add_argument("--length-bins", type=int, default=DEFAULT_LENGTH_BINS)
    ap.add_argument("--n-null", type=int, default=DEFAULT_N_NULL)
    ap.add_argument("--null-blocks", type=int, default=DEFAULT_NULL_BLOCKS,
                    help="architecture band: real sources (authors, else groups) per pseudo-model (default 1)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--score-cache", help="JSONL cache of instrument scores; appended per batch and reused on resume")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--device", default=None)
    ap.add_argument("--per-candidate", action="store_true",
                    help="include per-candidate percentiles (diagnostic; not a passage-level detector)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
