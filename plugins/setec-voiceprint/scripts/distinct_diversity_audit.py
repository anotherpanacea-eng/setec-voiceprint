#!/usr/bin/env python3
"""distinct_diversity_audit.py — set-level distinct-CLUSTER diversity (NoveltyBench axis, M1 stdlib).

A SET of generations to ONE prompt induces a *partition* into near-duplicate equivalence clusters: how
many genuinely distinct things did the model say, and what does each distinct thing look like. This
surface reports that **partition** — the distinct-cluster count, the cluster-size distribution, one
positional representative per cluster, and a utility-weighted distinctness — NOT a single "diversity
score". The read is the distribution + the representatives.

Clean-room reimplementation of the set-partition read from **NoveltyBench: Evaluating Language Models for
Humanlike Diversity** (arXiv:2504.05228), which partitions a set of generations into equivalence classes
and reports the number of distinct classes + a utility-weighted distinctness rather than averaging a
pairwise similarity. The paper's *learned deduper* is replaced here by a **lexical near-dup** equivalence
relation (word-shingle Jaccard >= threshold, single-link transitive closure) so M1 stays pure-stdlib,
deterministic, and CI-runnable. The model/embedding deduper is the POC-gated M2 `--lens model-dedup`
seam (lazy-import + fail-loud, not in this build).

Set-level axis (`set_level_diversity`, the surface `homogeneity_audit` / `originality_audit` /
`skeleton_overlap_audit` / `corpus_novelty_audit` share): the signal lives *between* the texts (in the
partition they induce), not inside any one — no per-document surface can see it.

How this differs from its surface-mates (it is a THIRD/fifth id on `set_level_diversity`, NOT a re-skin):

  * vs `homogeneity_audit` — that reads a *continuous* average-pairwise-cosine distribution + effective
    modes over a stylometric Gram matrix; it never PARTITIONS the set into discrete classes and emits no
    per-class representatives. This emits discrete cluster structure (a count + representatives).
  * vs `originality_audit` / `corpus_novelty_audit` — those are target-vs-pool reconstructibility
    (longest-n-gram coverage of a doc by a reference pool), a doc-vs-pool question, not a within-set
    partition.
  * vs `skeleton_overlap_audit` (its NEAREST neighbor — same surface, same union-find clustering
    machinery, same no-verdict-clusters posture). Two real differences, not a re-skin:
      - the EQUIVALENCE RELATION is different: word-shingle CONTENT near-dup Jaccard (the near-verbatim
        mode-collapse signature) vs skeleton's STRUCTURAL discourse-template LCS-ratio over a
        content-word-FREE symbol string (marker-bucket x length-tercile x terminal class).
      - the READ is different: a FULL partition INCLUDING singletons -> a distinct-cluster count /
        distinct_ratio (the NoveltyBench "how many distinct things were said" axis); skeleton DROPS
        singletons and reports only >= 2-member template-reuse clusters ("which docs reuse a template").
    Stated bluntly: this answers *how many distinct things were said (content)*; skeleton answers *which
    docs reuse a structural template (form)*.

Posture (no verdict): reports a partition + representatives — NOT an AI/human call, NOT a model-quality
determination. Low distinctness is NOT "mode-collapsed model": a tight topical prompt, a shared genre, or
a single source collapses clusters with no model defect; mixing prompts inflates apparent distinctness.
**No absolute band** (like `homogeneity_audit` / `originality_audit`); thresholding is operator-side. The
representative is the EARLIEST-by-input-order cluster member — a positional representative, never a
"best" / ranked / selected output. The claim license refuses any verdict.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from stylometry_core import word_tokens  # noqa: E402

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "distinct_diversity_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_MIN_SET = 10
# Per-text length floor (words). Below this a text has too few tokens for a stable shingle set; such
# texts are dropped before the set-floor (min_set) check. 15 admits normal sentence-length responses
# while excluding stubs (matches homogeneity_audit's floor on the same surface).
LENGTH_FLOOR_WORDS = 15

DEFAULT_SHINGLE_K = 5
DEFAULT_NEAR_DUP_THRESHOLD = 0.5
DEFAULT_UTILITY_DISCOUNT = 0.0
# First ~N tokens of a representative shown in repr_excerpt (a readable peek, not the whole text).
_REPR_EXCERPT_TOKENS = 30

# The paper's reference numbers use a LEARNED deduper; M1's lexical lens is NOT comparable to them.
_PAPER_LENS_INCOMPARABLE = (
    "arXiv:2504.05228 partitions with a LEARNED deduper; this M1 lens is a stdlib lexical near-dup "
    "(word-shingle Jaccard) relation — its distinct-counts are NOT comparable to the paper's figures, "
    "emitted for method provenance only, never as a band/cut"
)


# ---- pool loading (clean-room of homogeneity_audit's conventions; independent module) ----

def _load_manifest(path: Path) -> list[tuple[str, str]]:
    """(id, text) from a JSONL manifest (inline `text` or a `text_path`/`path` resolved relative to the
    manifest's dir). Malformed rows are skipped with a stderr note (skip-and-warn). Clean-room copy of
    homogeneity_audit._load_manifest's shape — the two scripts stay independent."""
    out: list[tuple[str, str]] = []
    base = path.resolve().parent
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  manifest line {line_no}: {e}; skipping\n")
            continue
        if not isinstance(row, dict):
            sys.stderr.write(f"  manifest line {line_no}: not a JSON object; skipping\n")
            continue
        src = str(row.get("id") or row.get("path") or row.get("text_path") or f"line{line_no}")
        if isinstance(row.get("text"), str):
            out.append((src, row["text"]))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = base / rel
            if fp.is_file():
                out.append((src, fp.read_text(encoding="utf-8", errors="replace")))
            else:
                sys.stderr.write(f"  manifest line {line_no}: {fp} not found; skipping\n")
    return out


def _load_dir(root: Path, suffixes=(".txt", ".md")) -> list[tuple[str, str]]:
    """(id, text) for every .txt/.md file under `root` (recursive, sorted-stable)."""
    out: list[tuple[str, str]] = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        if p.suffix.lower() in suffixes:
            out.append((p.relative_to(root).as_posix(),
                        p.read_text(encoding="utf-8", errors="replace")))
    return out


# ---- M1 lexical-near-dup lens (stdlib, model-free, deterministic) ------------

def _word_count(text: str) -> int:
    return len(word_tokens(text))


def word_shingles(text: str, k: int) -> frozenset[tuple[str, ...]]:
    """The set of length-`k` word k-grams over `word_tokens(text)`.

    Empty/under-`k` texts fall back to a singleton set of the whole token tuple so a short text still
    has a comparable, non-empty set (a length-1..k-1 text yields one shingle, never an empty set that
    would make every short text Jaccard-identical). Returns a frozenset (immutable; safe to share)."""
    toks = word_tokens(text)
    if len(toks) < k:
        # Singleton-token fallback: the whole (possibly empty) token tuple is the one shingle.
        return frozenset({tuple(toks)})
    return frozenset(tuple(toks[i:i + k]) for i in range(len(toks) - k + 1))


def jaccard(a: frozenset, b: frozenset) -> float:
    """|a ∩ b| / |a ∪ b|, bounded [0, 1]. Two empty sets -> 0.0 (degenerate but NOT NaN — the R4 gate
    rejects a NaN; an empty/empty union must not 0/0). One empty + one non-empty -> 0.0 (no overlap)."""
    if not a and not b:
        return 0.0
    union = len(a | b)
    if union == 0:
        return 0.0
    return len(a & b) / union


def _single_link_clusters(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Connected components over `edges` (single-link union-find), returned as sorted index lists,
    INCLUDING singletons (every input index appears in exactly one cluster — this is a FULL partition,
    unlike skeleton_overlap_audit which drops singletons). Deterministic: clusters sorted by their
    earliest member so the partition order is stable."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[min(ra, rb)] = max(ra, rb)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    # Sort members ascending, then clusters by their earliest member (stable, input-order anchored).
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def _quantile(ordered: list[float], q: float) -> float:
    """Linear-interpolation quantile on an already-sorted list (clean-room of
    homogeneity_audit._quantile / numpy's default 'linear' method; stdlib)."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def size_distribution(sizes: list[int]) -> dict[str, Any]:
    """7-key distribution summary over the cluster sizes (clean-room of homogeneity_audit's
    cosine_distribution shape): `n`, `mean`, `sd`, `min`, `p10`, `p50`, `p90`. Pure stdlib. Empty input
    -> all-None (well-formed empty block)."""
    if not sizes:
        return {"n": 0, "mean": None, "sd": None, "min": None,
                "p10": None, "p50": None, "p90": None}
    ordered = sorted(float(s) for s in sizes)
    return {
        "n": len(sizes),
        "mean": statistics.mean(sizes),
        "sd": statistics.stdev(sizes) if len(sizes) > 1 else 0.0,
        "min": min(sizes),
        "p10": _quantile(ordered, 0.10),
        "p50": _quantile(ordered, 0.50),
        "p90": _quantile(ordered, 0.90),
    }


def _repr_excerpt(text: str) -> str:
    """First ~_REPR_EXCERPT_TOKENS whitespace tokens of a representative, joined for a readable peek."""
    toks = text.split()
    excerpt = " ".join(toks[:_REPR_EXCERPT_TOKENS])
    return excerpt + (" …" if len(toks) > _REPR_EXCERPT_TOKENS else "")


def _assumptions(*, shingle_k: int, near_dup_threshold: float,
                 utility_discount: float) -> dict[str, Any]:
    return {
        "method": "NoveltyBench set-partition (distinct equivalence classes + utility-weighted "
                  "distinctness; clean-room, arXiv:2504.05228)",
        "lens": "lexical-near-dup (word-shingle Jaccard >= threshold, single-link transitive closure); "
                "model-free, NOT the paper's learned deduper",
        "shingle_k": shingle_k,
        "near_dup_threshold": near_dup_threshold,
        "utility_discount": utility_discount,
        "orientation": "distinct_ratio gt = MORE distinct (more equivalence clusters per text)",
        "confounds": "low distinctness is NOT a model defect — a tight topical prompt, a shared "
                     "genre/register, or a single source collapses clusters with no model involvement; "
                     "mixing prompts in the pool inflates apparent distinctness. The operator must "
                     "supply a prompt-matched pool (no implied default pool).",
        "lexical_limit": "Jaccard-over-word-shingles catches near-VERBATIM restatement (the dominant "
                         "mode-collapse signature) but SPLITS two semantically-equivalent paraphrases "
                         "that share few shingles. The conservative direction: M1 may OVER-count "
                         "distinct clusters (under-report collapse), never falsely claim collapse. The "
                         "semantic relation is the M2 model-dedup seam.",
        "no_band": "no absolute band is emitted (like homogeneity_audit / originality_audit); "
                   "thresholding is operator-side",
        "representative_note": "each cluster's representative is its EARLIEST member by input order — a "
                               "positional representative, NOT a 'best' / ranked / selected output",
        "paper_lens_incomparable": _PAPER_LENS_INCOMPARABLE,
    }


def audit_pool(
    pool: list[tuple[str, str]],
    *,
    shingle_k: int = DEFAULT_SHINGLE_K,
    near_dup_threshold: float = DEFAULT_NEAR_DUP_THRESHOLD,
    utility_discount: float = DEFAULT_UTILITY_DISCOUNT,
    min_set: int = DEFAULT_MIN_SET,
) -> dict[str, Any]:
    """Partition the pool into lexical near-dup equivalence clusters and report the distinct-cluster
    distribution + representatives. Raises ValueError (-> bad_input) on a too-small / empty pool.

    The [0, 1] bound on `distinct_ratio` is a property of THIS arithmetic (n_clusters in [1, n_texts]
    and n_texts >= min_set > 0), NOT of the R4 bounds gate: R4 guarantees only finiteness on a
    'ratio'-named leaf (output_schema._TRANSFORM_RE suppresses any [0,1] range-check on it). With
    `utility_discount > 0` the utility-weighted distinctness can EXCEED n_clusters and is intentionally
    not in [0, 1] (it credits redundant restatements); at discount 0.0 it equals n_clusters."""
    usable = [(src, t) for src, t in pool if _word_count(t) >= LENGTH_FLOOR_WORDS]
    if len(usable) < min_set:
        raise ValueError(
            f"pool has {len(usable)} text(s) with >= {LENGTH_FLOOR_WORDS} words "
            f"(< min_set {min_set}); a distinct-cluster partition is not shipped on too small a set"
        )

    ids = [src for src, _ in usable]
    texts = [t for _, t in usable]
    n_texts = len(usable)
    shingle_sets = [word_shingles(t, shingle_k) for t in texts]

    # All-pairs i<j Jaccard -> near-dup edges -> single-link partition (FULL, singletons included).
    edges: list[tuple[int, int]] = []
    for i in range(n_texts):
        for j in range(i + 1, n_texts):
            if jaccard(shingle_sets[i], shingle_sets[j]) >= near_dup_threshold:
                edges.append((i, j))
    clusters = _single_link_clusters(n_texts, edges)

    cluster_sizes = sorted((len(c) for c in clusters), reverse=True)
    n_clusters = len(clusters)
    # distinct_ratio in (0, 1]: n_clusters in [1, n_texts], n_texts >= min_set > 0. NOT R4-bounded
    # (the 'ratio' name suppresses the range-check) — guaranteed by this arithmetic + an acceptance.
    distinct_ratio = n_clusters / n_texts

    # Utility-weighted distinctness: each cluster contributes 1 for its representative + discount**rank
    # for each redundant member (rank 1..). At discount 0.0 -> pure distinct-count (== n_clusters);
    # discount in (0, 1] credits redundant restatements (can exceed n_clusters; not in [0, 1]).
    utility_weighted = 0.0
    for c in clusters:
        utility_weighted += 1.0
        for rank in range(1, len(c)):
            utility_weighted += utility_discount ** rank

    # Representatives: EARLIEST member by input order (clusters carry ascending indices, so member[0]
    # is the earliest). Positional, never "best" — no rank/score/selected key is emitted.
    representatives: list[dict[str, Any]] = []
    for c in clusters:
        rep_idx = c[0]
        representatives.append({
            "representative_id": ids[rep_idx],
            "size": len(c),
            "member_ids": [ids[k] for k in c],
            "repr_excerpt": _repr_excerpt(texts[rep_idx]),
        })

    return {
        "n_texts": n_texts,
        "n_clusters": n_clusters,
        "lens": "lexical-near-dup",
        "cluster_size_distribution": size_distribution(cluster_sizes),
        "cluster_sizes": cluster_sizes,
        "distinct_ratio": round(distinct_ratio, 6),
        "utility_weighted_distinctness": round(utility_weighted, 6),
        "representatives": representatives,
        "assumptions": _assumptions(shingle_k=shingle_k, near_dup_threshold=near_dup_threshold,
                                    utility_discount=utility_discount),
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The equivalence-cluster partition of the supplied pool under the named lexical near-dup "
            "lens: the number of distinct clusters, the cluster-size distribution, one POSITIONAL "
            "representative per cluster (the earliest member by input order), and a utility-weighted "
            "distinctness at the operator-supplied discount. A descriptive measurement of a PARTITION "
            "over a set, oriented distinct_ratio gt = more distinct."
        ),
        "does_not_license": (
            "Any AI/human determination — distinctness is a property of a partition, not a provenance "
            "call. Low distinctness is NOT a model defect: a tight topical prompt, a shared "
            "genre/register, or a single source collapses clusters with no model involvement; high "
            "distinctness is NOT 'human'. No 'this model is mode-collapsed / lacks diversity' "
            "determination, no plagiarism or derivative-work claim. The representative is POSITIONAL "
            "(earliest by input order), NOT a 'best' / ranked / selected output — the surface picks no "
            "winner and ranks no output. No absolute band is emitted; thresholds are operator-side. The "
            "lexical-near-dup number is NOT comparable to the paper's learned-deduper figures. The "
            "surface emits no verdict."
        ),
    }


# ---- M2 seam (lazy-import + fail-loud; NOT in this build) --------------------

def _model_lens_unavailable() -> dict[str, Any]:
    """M2 --lens model-dedup: this M1 build wires NO real deduper, so the model lens ALWAYS fails loud
    here — whether or not a module named ``noveltybench_deduper`` happens to be importable. It NEVER
    silently falls back to the lexical lens (a silent fallback would change the meaning of the partition
    — paraphrase-equivalence vs near-verbatim).

    Always returns a non-empty error block in this build (so the ``if err:`` guard in ``_run`` always
    fires for ``model-dedup``). Both branches use ``reason_category: missing_dependency`` — the missing
    thing is a *wired* learned/embedding deduper, absent in M1 either way — and differ only in the
    operator-facing reason:

      * ImportError: no ``noveltybench_deduper`` client is installed at all.
      * import SUCCESS: a module by that name IS importable, but M1 has NOT wired a real learned/
        embedding deduper to it. Returning ``{}`` (falsy) here would skip the guard and fall through to
        the LEXICAL lens, mislabeling lexical numbers as ``model-dedup`` — exactly the planted
        false-invariant a future M2 build (or a stub / name collision) would trip. We fail loud instead;
        the import-SUCCESS path becomes a real wiring point only when M2 actually lands.
    """
    try:
        import noveltybench_deduper  # type: ignore  # noqa: F401
    except ImportError:
        return {
            "reason": ("--lens model-dedup requires a model/embedding deduper client "
                       "(noveltybench_deduper), which is not installed; the model lens fails loud "
                       "rather than silently falling back to the stdlib lexical lens (which answers a "
                       "different, near-verbatim question). Use --lens lexical-near-dup (default)."),
            "reason_category": "missing_dependency",
        }
    # A noveltybench_deduper module is importable, but M1 has wired no real deduper to it. Fail loud
    # rather than fall through to the lexical lens — see the docstring (the planted false-invariant).
    return {
        "reason": ("--lens model-dedup is a POC-gated M2 seam not wired in this build: a "
                   "noveltybench_deduper module is importable, but no learned/embedding deduper has "
                   "been connected to it, so the model lens fails loud rather than silently falling "
                   "back to the stdlib lexical lens (which answers a different, near-verbatim "
                   "question). Use --lens lexical-near-dup (default)."),
        "reason_category": "missing_dependency",
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not (args.manifest or args.dir):
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason="needs --manifest or --dir (a prompt-matched response pool)",
            reason_category="bad_input")

    if args.lens == "model-dedup":
        err = _model_lens_unavailable()
        if err:
            ref = args.dir or args.manifest
            return build_error_output(
                task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                target_path=ref, reason=err["reason"], reason_category=err["reason_category"])

    try:
        if args.dir:
            pool = _load_dir(Path(args.dir))
            ref = args.dir
        else:
            pool = _load_manifest(Path(args.manifest))
            ref = args.manifest
    except (OSError, UnicodeDecodeError) as e:
        which = "--dir" if args.dir else "--manifest"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason=f"cannot read {which}: {e}", reason_category="bad_input")

    try:
        results = audit_pool(
            pool, shingle_k=args.shingle_k, near_dup_threshold=args.near_dup_threshold,
            utility_discount=args.utility_discount, min_set=args.min_set)
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=ref, reason=str(e), reason_category="bad_input")

    total_words = sum(_word_count(t) for _, t in pool)
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=ref, target_words=total_words,
        baseline={"pool": ref, "n_texts": results["n_texts"]},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--manifest", help="JSONL manifest of the response pool (id + text|text_path).")
    src.add_argument("--dir", help="Directory of pooled texts (.txt/.md, recursive).")
    ap.add_argument("--shingle-k", type=int, default=DEFAULT_SHINGLE_K,
                    help=f"Word k-gram length for the near-dup shingle sets (default "
                         f"{DEFAULT_SHINGLE_K}); a documented starting point, not a calibrated cut.")
    ap.add_argument("--near-dup-threshold", type=float, default=DEFAULT_NEAR_DUP_THRESHOLD,
                    help=f"Jaccard at/above which two texts are near-dup-equivalent (default "
                         f"{DEFAULT_NEAR_DUP_THRESHOLD}); a documented starting point, not calibrated.")
    ap.add_argument("--utility-discount", type=float, default=DEFAULT_UTILITY_DISCOUNT,
                    help=f"Per-rank discount for redundant cluster members in "
                         f"utility_weighted_distinctness (default {DEFAULT_UTILITY_DISCOUNT} = pure "
                         "distinct-count); the operator owns any redundancy credit.")
    ap.add_argument("--min-set", type=int, default=DEFAULT_MIN_SET,
                    help=f"Set floor: abstain (bad_input) below this many usable texts "
                         f"(default {DEFAULT_MIN_SET}).")
    ap.add_argument("--lens", choices=["lexical-near-dup", "model-dedup"], default="lexical-near-dup",
                    help="Equivalence relation. M1 ships only the model-free lexical-near-dup lens; "
                         "model-dedup is a POC-gated M2 seam (fails loud if absent, not in this build).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.shingle_k < 1:
        sys.stderr.write("[distinct_diversity_audit] --shingle-k must be >= 1\n")
        return 2
    if not (0.0 <= args.near_dup_threshold <= 1.0):
        sys.stderr.write("[distinct_diversity_audit] --near-dup-threshold must be in [0, 1]\n")
        return 2
    if not (0.0 <= args.utility_discount <= 1.0):
        sys.stderr.write("[distinct_diversity_audit] --utility-discount must be in [0, 1]\n")
        return 2
    if args.min_set < 2:
        sys.stderr.write("[distinct_diversity_audit] --min-set must be >= 2 (a partition needs "
                         "at least two texts)\n")
        return 2

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
