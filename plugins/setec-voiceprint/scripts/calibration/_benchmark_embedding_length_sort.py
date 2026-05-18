#!/usr/bin/env python3
"""Empirical benchmark: does ``sentence_transformers.encode()``
length-sort inputs internally?

PR #91 added length-sorted batching to ``SurprisalBackend.score_-
texts`` because raw HuggingFace ``model(input_ids, attention_mask)``
calls don't length-sort (padding waste collapses). The "future
work" note suggested mirroring the same pattern at the embedding-
backend layer. PR #101's investigation surfaced that
``sentence_transformers.SentenceTransformer.encode()`` *already*
length-sorts internally (since v2.2; the framework declares
``>=2.7``), making a wrapper-level pre-sort redundant.

This script puts numbers behind that claim. It feeds a synthetic
heterogeneous-length corpus to a real ``EmbeddingBackend.encode()``
in three orderings and measures wall-clock time:

  1. ``shuffled``   — interleaved short/long, the operator's likely
                      input shape (essays + tweet-length notes).
  2. ``pre_sorted`` — caller-side ascending-length sort.
  3. ``adversarial`` — caller-side descending-length sort (worst-
                       case for the naive batcher; an additional
                       falsifier of "ST resorts").

If ST already length-sorts internally, all three orderings produce
near-identical wall-clock times (within ~3-5% noise floor on a
warm CPU; cleaner on a GPU). If ST does NOT length-sort, the
pre-sorted ordering is measurably faster — by a margin
proportional to the length variance reduction within batches.

Usage (on a host with ``sentence-transformers`` + ``torch``):

  python3 _benchmark_embedding_length_sort.py
  python3 _benchmark_embedding_length_sort.py --model mxbai
  python3 _benchmark_embedding_length_sort.py --model minilm --n 200

The default model is ``minilm`` (small, fast, dt-cached
artifacts likely already on disk for the framework's primary
test loop). ``--model mxbai`` exercises the same code path on
the larger Phase-A model whose surprisal-side equivalent was
the original optimization target.

Exits 0 unconditionally — the benchmark prints results and is
informational only. The framework-side claim ("ST length-sorts
internally") is informed by this evidence but pinned in code at
PR #101's `EmbeddingBackend` dtype-contract docstring.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Iterable


def _build_corpus(
    *, n: int, short_len: int, long_len: int, seed: int = 42,
) -> list[str]:
    """Synthetic corpus with ``n`` texts of alternating short and
    long character counts. Half-half split; the alternation is
    interleaved so a naive same-order batcher pads every batch to
    the longest member regardless of batch_size."""
    rng = random.Random(seed)
    short_words = ["the", "and", "a", "of", "to", "in", "is", "that"]
    long_filler = [
        "stylometric", "polyphonic", "lexicographer", "morphological",
        "phonotactic", "transliteration", "calibration", "embedding",
    ]

    def gen(target_chars: int) -> str:
        out: list[str] = []
        acc = 0
        while acc < target_chars:
            pool = short_words if rng.random() < 0.7 else long_filler
            w = rng.choice(pool)
            out.append(w)
            acc += len(w) + 1
        return " ".join(out)

    texts: list[str] = []
    for i in range(n):
        if i % 2 == 0:
            texts.append(gen(short_len))
        else:
            texts.append(gen(long_len))
    return texts


def _time_encode(backend, texts: list[str], *, batch_size: int) -> float:
    """Single timed ``encode`` call. Returns wall-clock seconds.
    Warm-up is the caller's responsibility (first call pays the
    weight-load cost)."""
    t0 = time.perf_counter()
    backend.encode(texts, batch_size=batch_size)
    return time.perf_counter() - t0


def _percent_diff(a: float, b: float) -> float:
    """Relative difference from ``a`` to ``b``, signed percentage."""
    return 100.0 * (b - a) / a if a else 0.0


def _orderings(texts: list[str]) -> dict[str, list[str]]:
    """Three orderings of the same corpus — same set of strings,
    different presentation order to the backend."""
    shuffled = list(texts)
    random.Random(0).shuffle(shuffled)
    pre_sorted = sorted(texts, key=len)
    adversarial = sorted(texts, key=len, reverse=True)
    return {
        "shuffled": shuffled,
        "pre_sorted": pre_sorted,
        "adversarial": adversarial,
    }


def run_benchmark(
    *, model_alias: str, n: int, batch_size: int, repeats: int,
    short_len: int, long_len: int, out_stream=sys.stdout,
) -> dict[str, dict[str, float]]:
    """Run the benchmark and return a dict of
    ``{ordering: {mean, stdev, median}}``. Prints a Markdown summary
    as it goes so an operator running the script from a terminal
    sees progress without waiting for the full repeat loop."""
    # Import lazily — the script must import-check cleanly on slim
    # CI harnesses (where sentence-transformers isn't installed) so
    # ``--help`` works regardless.
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here.parent))
    import embedding_backend as eb  # type: ignore

    out_stream.write(
        f"# Embedding length-sort benchmark\n"
        f"\n"
        f"- model: `{model_alias}`\n"
        f"- corpus: {n} texts "
        f"(half ~{short_len} chars, half ~{long_len} chars, interleaved)\n"
        f"- batch_size: {batch_size}\n"
        f"- repeats: {repeats}\n"
        f"\n"
    )
    out_stream.write(
        "Hypothesis: if `sentence_transformers.encode()` already "
        "length-sorts internally,\n"
        "the three orderings produce near-identical wall-clock times "
        "(within ~3-5% noise).\n"
        "If it does NOT, `pre_sorted` is measurably faster than "
        "`shuffled` and `adversarial`.\n\n"
    )

    backend = eb.EmbeddingBackend(model_id=model_alias)
    texts = _build_corpus(n=n, short_len=short_len, long_len=long_len)
    orderings = _orderings(texts)

    # Warm-up call to pay weight-load cost outside the timed loop.
    out_stream.write("warming up... ")
    out_stream.flush()
    _time_encode(backend, texts[:min(8, len(texts))], batch_size=batch_size)
    out_stream.write("done.\n\n")

    timings: dict[str, list[float]] = {k: [] for k in orderings}
    for r in range(repeats):
        out_stream.write(f"  repeat {r + 1}/{repeats}:")
        for label, t_list in orderings.items():
            dt = _time_encode(backend, t_list, batch_size=batch_size)
            timings[label].append(dt)
            out_stream.write(f"  {label}={dt:.3f}s")
        out_stream.write("\n")
        out_stream.flush()

    out_stream.write("\n## Results\n\n")
    out_stream.write("| ordering | mean | stdev | median |\n")
    out_stream.write("|---|---|---|---|\n")
    summary: dict[str, dict[str, float]] = {}
    for label, t_list in timings.items():
        m = statistics.mean(t_list)
        s = statistics.stdev(t_list) if len(t_list) > 1 else 0.0
        med = statistics.median(t_list)
        summary[label] = {"mean": m, "stdev": s, "median": med}
        out_stream.write(
            f"| `{label}` | {m:.3f}s | {s:.3f}s | {med:.3f}s |\n"
        )

    base_mean = summary["shuffled"]["mean"]
    out_stream.write("\n## Interpretation\n\n")
    out_stream.write(
        f"Treating `shuffled` as the baseline ({base_mean:.3f}s):\n\n"
    )
    for label in ("pre_sorted", "adversarial"):
        d = _percent_diff(base_mean, summary[label]["mean"])
        sign = "+" if d >= 0 else ""
        out_stream.write(
            f"- `{label}`: {sign}{d:.2f}% vs shuffled.\n"
        )
    out_stream.write(
        "\nIf the three numbers are within ~3-5% of each other, "
        "sentence-transformers is length-sorting internally and a "
        "wrapper-level pre-sort would be redundant — the conclusion "
        "that motivated PR #101's pivot from length-sort to dtype/"
        "device awareness.\n"
        "\nIf `pre_sorted` is measurably faster (say, >10%) than "
        "`shuffled`, that's evidence ST is NOT internally sorting "
        "in the installed version, and the original Chunk C scope "
        "(wrapper-level length-sort) would in fact deliver a real "
        "perf win.\n"
    )
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Empirical benchmark of sentence-transformers' internal "
            "length-sort claim. Informational — exits 0 regardless "
            "of outcome. See module docstring for context."
        )
    )
    p.add_argument(
        "--model", default="minilm",
        help=(
            "Embedding-model alias or full HF id. Default 'minilm' "
            "(small, fast, framework's primary test model). Use "
            "'mxbai' / 'gemma' / 'harrier' to exercise the Phase A "
            "candidate set."
        ),
    )
    p.add_argument(
        "--n", type=int, default=100,
        help="Corpus size (number of texts). Default 100.",
    )
    p.add_argument(
        "--batch-size", type=int, default=32,
        help=(
            "Encode batch size. Default 32 (sentence-transformers' "
            "own default)."
        ),
    )
    p.add_argument(
        "--repeats", type=int, default=5,
        help=(
            "How many timing runs per ordering. Default 5 (gives "
            "a stable stdev without taking forever)."
        ),
    )
    p.add_argument(
        "--short-len", type=int, default=80,
        help="Target char count for the 'short' half. Default 80.",
    )
    p.add_argument(
        "--long-len", type=int, default=1200,
        help=(
            "Target char count for the 'long' half. Default 1200 "
            "(within ST's 512-token context for both mxbai and "
            "minilm after tokenisation)."
        ),
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    run_benchmark(
        model_alias=args.model,
        n=args.n,
        batch_size=args.batch_size,
        repeats=args.repeats,
        short_len=args.short_len,
        long_len=args.long_len,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
