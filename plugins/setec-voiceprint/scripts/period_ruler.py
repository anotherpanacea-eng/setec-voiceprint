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
CHANNELS = ("date", "authenticity")

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
            if kind == "reference":
                group = obj.get("group", obj.get("volume_id", obj.get("author")))
                if group is None:
                    raise PeriodRulerError(
                        f"reference line {lineno}: needs 'group' (or 'volume_id'/'author') "
                        "so the noise floor can leave a whole source out"
                    )
                row["group"] = str(group)
            for field in ("date_estimate_year", "p_synthetic"):
                if field in obj and obj[field] is not None:
                    row[field] = float(obj[field])
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
                    if r["length_bin"] == lbin and r["group"] != exclude_group)
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
        window = _window(by_year, r["year"], h, r["length_bin"], exclude_group=r["group"])
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
        cells.setdefault((decade_of(r["year"]), r["length_bin"]), {}).setdefault(r["group"], []).append(r)
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
# Run
# ---------------------------------------------------------------------------

def run_period_ruler(
    candidates: list[dict[str, Any]],
    reference: list[dict[str, Any]],
    *,
    channels: tuple[str, ...] = CHANNELS,
    date_scorer: ScorerFn | None = None,
    auth_scorer: ScorerFn | None = None,
    window_years: int = DEFAULT_WINDOW_YEARS,
    n_length_bins: int = DEFAULT_LENGTH_BINS,
    n_null: int = DEFAULT_N_NULL,
    seed: int = DEFAULT_SEED,
    include_per_candidate: bool = False,
    cache_path: Path | None = None,
    batch_size: int = 32,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if not candidates:
        raise PeriodRulerError("no candidate passages")
    if len({r["group"] for r in reference}) < 2:
        raise PeriodRulerError("reference needs passages from at least two groups for a leave-one-group-out null")
    if n_null < 100:
        raise PeriodRulerError("n_null must be at least 100")
    warnings: list[str] = []
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
            "reference": "operator-supplied dated real passages with a source group per passage",
            "use": "validator for a set of generations; never a training reward",
        },
    }
    if include_per_candidate:
        block["per_candidate"] = per_candidate
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
            "percentile), each read against a pseudo-model noise floor drawn from the reference. "
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
                     for ch in args.channels for r in candidates + reference)
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

    def progress(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    try:
        results, warnings = run_period_ruler(
            candidates, reference, channels=args.channels,
            date_scorer=scorers.get("date"), auth_scorer=scorers.get("authenticity"),
            window_years=args.window_years, n_length_bins=args.length_bins,
            n_null=args.n_null, seed=args.seed, include_per_candidate=args.per_candidate,
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
                    help="comma list from: date,authenticity (default both)")
    ap.add_argument("--window-years", type=int, default=DEFAULT_WINDOW_YEARS)
    ap.add_argument("--length-bins", type=int, default=DEFAULT_LENGTH_BINS)
    ap.add_argument("--n-null", type=int, default=DEFAULT_N_NULL)
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
