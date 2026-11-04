#!/usr/bin/env python3
"""meaning_preservation.py — MIS and SBERT meaning-preservation scores for rewrite pairs (VP-RS5).

For each (source, rewrite) pair this reports two model reads of how much meaning the
rewrite kept, the pair that style-transfer work reports beside every style score:

* ``mis``: Mutual Implication Score (Babakov et al. 2022), a RoBERTa-large model
  trained to give a symmetric 0-1 read of whether each text implies the other.
  Loaded at run time through the ``mutual-implication-score`` package from
  ``s-nlp/Mutual_Implication_Score``. Those weights are CC BY-NC-SA 4.0; nothing
  from them is vendored here, and the license travels in the output.
* ``sbert_cosine``: cosine similarity of sentence-transformers embeddings, through
  the shared SETEC embedding backend (default ``mxbai``; any alias or HF id).

The set is summarised per channel by mean, median, minimum and quartiles. Both
models truncate at 512 tokens, so pairs much longer than a paragraph are warned
about: chunk them to paragraphs first. No cut-off is calibrated, and neither read
says whether a rewrite is good; low preservation can be the point of a rewrite.

TASK_SURFACE: craft_restoration (beside semantic_preservation_check).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any, Callable, Sequence

from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.contract.claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "craft_restoration"
TOOL_NAME = "meaning_preservation"
SCRIPT_VERSION = "1.0"

CHANNELS = ("mis", "sbert")
MIS_MODEL_ID = "s-nlp/Mutual_Implication_Score"
MIS_LICENSE = "CC BY-NC-SA 4.0 (weights loaded at run time, not redistributed)"
DEFAULT_SBERT_MODEL = "mxbai"
TRUNCATION_WARN_WORDS = 350

MisScorer = Callable[[Sequence[str], Sequence[str]], Sequence[float]]
Encoder = Callable[[Sequence[str]], Sequence[Sequence[float]]]


class MeaningPreservationError(ValueError):
    """Bad input reported as a bad_input envelope."""


def _cosine(a: Sequence[float], b: Sequence[float]) -> float | None:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return None
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None, "quartiles": None}
    qs = statistics.quantiles(values, n=4, method="inclusive") if len(values) > 1 else [values[0]] * 3
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
            "min": min(values), "quartiles": list(qs)}


def run_meaning_preservation(
    pairs: list[dict[str, Any]],
    *,
    mis_scorer: MisScorer | None,
    sbert_encoder: Encoder | None,
    channels: Sequence[str] = CHANNELS,
    batch_size: int = 16,
) -> tuple[dict[str, Any], list[str]]:
    """Score pairs; channels with no scorer are dropped with a warning."""
    if not pairs:
        raise MeaningPreservationError("no pairs to score")
    warnings: list[str] = []
    active = []
    for ch in channels:
        if (ch == "mis" and mis_scorer is None) or (ch == "sbert" and sbert_encoder is None):
            warnings.append(f"channel {ch} skipped: its model could not be loaded")
        else:
            active.append(ch)
    if not active:
        raise LookupError("no requested channel could be loaded")

    sources = [p["source"] for p in pairs]
    rewrites = [p["rewrite"] for p in pairs]
    mis_vals: list[float | None] = [None] * len(pairs)
    cos_vals: list[float | None] = [None] * len(pairs)
    for start in range(0, len(pairs), batch_size):
        sl = slice(start, start + batch_size)
        if "mis" in active:
            mis_vals[sl] = [float(v) for v in mis_scorer(sources[sl], rewrites[sl])]
        if "sbert" in active:
            vecs = sbert_encoder(sources[sl] + rewrites[sl])
            k = len(sources[sl])
            cos_vals[sl] = [_cosine(vecs[i], vecs[k + i]) for i in range(k)]

    per_pair = []
    long_ids = []
    for p, m, c in zip(pairs, mis_vals, cos_vals):
        sw, rw = len(p["source"].split()), len(p["rewrite"].split())
        if max(sw, rw) > TRUNCATION_WARN_WORDS:
            long_ids.append(p["id"])
        row = {"id": p["id"], "source_words": sw, "rewrite_words": rw,
               "length_ratio": (rw / sw) if sw else None}
        if "mis" in active:
            row["mis"] = m
        if "sbert" in active:
            row["sbert_cosine"] = c
        per_pair.append(row)
    if long_ids:
        warnings.append(f"{len(long_ids)} pair(s) exceed {TRUNCATION_WARN_WORDS} words and are "
                        f"truncated at 512 tokens by both models (e.g. {long_ids[0]}); "
                        "chunk to paragraphs for a full read")

    channels_out: dict[str, Any] = {}
    if "mis" in active:
        channels_out["mis"] = {**_summary([v for v in mis_vals if v is not None]),
                               "model": MIS_MODEL_ID, "license": MIS_LICENSE}
    if "sbert" in active:
        channels_out["sbert_cosine"] = _summary([v for v in cos_vals if v is not None])
    return {
        "meaning_preservation": {
            "n_pairs": len(pairs),
            "channels": channels_out,
            "per_pair": per_pair,
            "calibration_status": "provisional",
            "assumptions": {
                "mis": "Mutual Implication Score, symmetric 0-1, higher means more mutual implication",
                "sbert_cosine": "cosine of sentence-transformers embeddings of the whole source and rewrite",
                "truncation": "both models read at most 512 tokens per text",
            },
        }
    }, warnings


class _MisPackageScorer:
    """Lazy wrapper over the ``mutual-implication-score`` package."""

    def __init__(self, device: str | None):
        from mutual_implication_score import MIS  # type: ignore
        self._mis = MIS(device=device or "cpu")

    def __call__(self, sources, rewrites):
        return self._mis.compute(list(sources), list(rewrites))


def _load_mis(device: str | None) -> MisScorer | None:
    if os.environ.get("setec_hermetic_backend") == "stub":
        return None
    try:
        return _MisPackageScorer(device)
    except (ImportError, OSError, RuntimeError):
        return None


def _load_sbert(model: str, device: str | None) -> Encoder | None:
    try:
        from setec.core.embedding_backend import EmbeddingBackend, EmbeddingBackendError
    except ImportError:
        return None
    backend = EmbeddingBackend(model_id=model, device=device)

    def encode(texts):
        return [list(map(float, v)) for v in backend.encode(list(texts), normalize=True)]

    try:
        backend._load()
    except EmbeddingBackendError:
        return None
    return encode


def _read_pairs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.pairs:
        pairs = []
        with Path(args.pairs).open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                src, rew = row.get("source"), row.get("rewrite")
                if not isinstance(src, str) or not isinstance(rew, str):
                    raise MeaningPreservationError(f"line {lineno}: need string 'source' and 'rewrite'")
                pairs.append({"id": str(row.get("id", lineno)), "source": src, "rewrite": rew})
        return pairs
    if args.source and args.rewrite:
        return [{"id": "pair", "source": Path(args.source).read_text(encoding="utf-8"),
                 "rewrite": Path(args.rewrite).read_text(encoding="utf-8")}]
    raise MeaningPreservationError("pass --pairs, or both --source and --rewrite")


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "Per-pair Mutual Implication Score and sentence-embedding cosine between a source "
            "and its rewrite, with per-channel summaries, as a read of how much meaning the "
            "rewrite kept."
        ),
        "does_not_license": (
            "This does NOT say a rewrite is good or bad, faithful in every detail, or fit to "
            "publish: both models miss small factual changes, negation and named-entity swaps "
            "(use semantic_preservation_check for those), and both truncate at 512 tokens. "
            "No cut-off is calibrated; calibration is PROVISIONAL."
        ),
    }


def _run(args: argparse.Namespace, *, mis_scorer=None, sbert_encoder=None, load=True) -> dict[str, Any]:
    target = args.pairs or args.rewrite

    def error(reason: str, category: str) -> dict[str, Any]:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=target, reason=reason, reason_category=category)

    try:
        pairs = _read_pairs(args)
    except (OSError, json.JSONDecodeError, MeaningPreservationError) as exc:
        return error(f"cannot read input: {exc}", "bad_input")
    channels = tuple(args.channels)
    if load:
        if "mis" in channels and mis_scorer is None:
            mis_scorer = _load_mis(args.device)
        if "sbert" in channels and sbert_encoder is None:
            sbert_encoder = _load_sbert(args.sbert_model, args.device)
    try:
        results, warnings = run_meaning_preservation(
            pairs, mis_scorer=mis_scorer, sbert_encoder=sbert_encoder, channels=channels,
            batch_size=args.batch_size)
    except MeaningPreservationError as exc:
        return error(str(exc), "bad_input")
    except LookupError:
        return error("no requested channel could be loaded: install mutual-implication-score "
                     "(MIS) and/or sentence-transformers (SBERT)", "missing_dependency")
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION, target_path=target,
        target_words=sum(len(p["rewrite"].split()) for p in pairs),
        baseline={"n_files": len(pairs), "words": sum(len(p["source"].split()) for p in pairs)},
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
        validate_bounds=True,
    )


def _parse_channels(value: str) -> list[str]:
    chans = [c.strip() for c in value.split(",") if c.strip()]
    bad = [c for c in chans if c not in CHANNELS]
    if not chans or bad:
        raise argparse.ArgumentTypeError(f"--channels takes a comma list of {', '.join(CHANNELS)}")
    return chans


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", help="JSONL {id, source, rewrite}")
    ap.add_argument("--source", help="source text file (single pair, with --rewrite)")
    ap.add_argument("--rewrite", help="rewrite text file (single pair, with --source)")
    ap.add_argument("--channels", type=_parse_channels, default=list(CHANNELS),
                    help="comma list of mis, sbert (default both)")
    ap.add_argument("--sbert-model", default=DEFAULT_SBERT_MODEL,
                    help="embedding alias or HF id for the SBERT channel (default mxbai)")
    ap.add_argument("--device")
    ap.add_argument("--batch-size", type=int, default=16)
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
