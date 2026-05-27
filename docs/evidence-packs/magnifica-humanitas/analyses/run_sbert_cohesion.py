#!/usr/bin/env python3
"""Tier 3 sentence-transformer adjacent-sentence cohesion audit.

Loads all-MiniLM-L6-v2, embeds each sentence, computes cosine similarity
of every adjacent sentence pair, and reports mean/median/std for each file.

Target: magnifica-humanitas-en.txt
Baselines: dilexit-nos, fratelli-tutti, laudato-si, lumen-fidei, laudate-deum.
"""
from __future__ import annotations
import json
import os
import re
import statistics
import sys
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

TEXTS_DIR = Path("/sessions/vibrant-charming-babbage/mnt/Claude Cowork Working Folder/Writing/stylometry sequence/magnifica-humanitas/texts")
OUT = Path("/sessions/vibrant-charming-babbage/mnt/Claude Cowork Working Folder/Writing/stylometry sequence/magnifica-humanitas/analyses/tier3_sbert_cohesion.json")

# Simple sentence segmenter — splits on period/question/exclamation followed by
# whitespace + capital. Good enough for prose comparison.
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def segment(text: str) -> list[str]:
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    sents = SENT_SPLIT.split(text)
    # Filter very short fragments (likely artifacts)
    return [s.strip() for s in sents if len(s.strip()) >= 20]


def cosine_pairs(embs: np.ndarray) -> list[float]:
    # embs already L2-normalized by sentence-transformers normalize_embeddings=True
    sims = (embs[:-1] * embs[1:]).sum(axis=1)
    return sims.tolist()


def main():
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    files = sorted([p for p in TEXTS_DIR.glob("*-en.txt")])
    out = {
        "method": "Adjacent-sentence cosine cohesion via sentence-transformers all-MiniLM-L6-v2",
        "files": {},
    }
    for fp in files:
        text = fp.read_text(encoding="utf-8")
        sents = segment(text)
        if len(sents) < 5:
            continue
        embs = model.encode(
            sents,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=64,
        )
        sims = cosine_pairs(np.asarray(embs))
        out["files"][fp.name] = {
            "n_sentences": len(sents),
            "n_pairs": len(sims),
            "mean": float(statistics.mean(sims)),
            "median": float(statistics.median(sims)),
            "std": float(statistics.pstdev(sims)),
            "min": float(min(sims)),
            "max": float(max(sims)),
            # Quantiles
            "q10": float(np.quantile(sims, 0.10)),
            "q25": float(np.quantile(sims, 0.25)),
            "q75": float(np.quantile(sims, 0.75)),
            "q90": float(np.quantile(sims, 0.90)),
        }
        print(f"{fp.name}: n={len(sents)} mean={statistics.mean(sims):.4f} std={statistics.pstdev(sims):.4f}")

    # Compute baseline aggregate (everything except magnifica)
    baseline_files = [n for n in out["files"] if "magnifica" not in n]
    baseline_means = [out["files"][n]["mean"] for n in baseline_files]
    baseline_stds = [out["files"][n]["std"] for n in baseline_files]
    out["baseline_aggregate"] = {
        "mean_of_means": statistics.mean(baseline_means),
        "sd_of_means": statistics.stdev(baseline_means) if len(baseline_means) > 1 else 0.0,
        "mean_of_stds": statistics.mean(baseline_stds),
        "sd_of_stds": statistics.stdev(baseline_stds) if len(baseline_stds) > 1 else 0.0,
        "baseline_files": baseline_files,
    }
    if "magnifica-humanitas-en.txt" in out["files"]:
        m = out["files"]["magnifica-humanitas-en.txt"]
        bm, bs = out["baseline_aggregate"]["mean_of_means"], out["baseline_aggregate"]["sd_of_means"]
        bsm, bss = out["baseline_aggregate"]["mean_of_stds"], out["baseline_aggregate"]["sd_of_stds"]
        out["magnifica_vs_baseline"] = {
            "z_mean_cohesion": (m["mean"] - bm) / bs if bs > 0 else None,
            "z_std_cohesion": (m["std"] - bsm) / bss if bss > 0 else None,
            "interpretation": "Higher mean = more semantic smoothing; lower std = less topical variance",
        }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
