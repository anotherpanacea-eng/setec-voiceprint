#!/usr/bin/env python3
"""function_word_adjacency_audit.py — the function-word adjacency NETWORK (spec 32).

A descriptive stylometric **graph-structure** profile: model the target's
function-word transitions as a directed graph (nodes = the 135 canonical
`FUNCTION_WORDS`, edges = adjacent-in-text transition frequencies) and emit
graph-structural descriptors — node centralities (in/out-degree + a
PageRank-style stationary centrality), per-node and global transition entropy,
directionality (reciprocity / weight asymmetry), network density, and small
directed motifs (2-cycles, length-3 directed walks, self-loops).

This is the *structure* view of the SAME function-word transitions
`function_word_grammar_audit` reads as flat top-20 bigrams (spec 32 §2). The
grammar audit owns the bigram COUNTS; FWAN owns the GRAPH READ of those counts.
To avoid two sources of truth, FWAN builds its adjacency from the identical
content-word-delimited runs the grammar audit segments (`_tokens_lower` +
`len(run) >= 2`), and a regression test ties `total_transitions` to the
run-segmentation bigram total (NOT the truncated public `function_bigrams`
field — that is a top-20 VIEW, spec 32 §13 P1).

Method root: function-word adjacency networks for authorship attribution
(Segarra, Eisen, Ribeiro), arXiv:1406.4469 — SETEC clean-rooms the FEATURE
CONSTRUCTION (graph + descriptors) and explicitly does NOT reproduce the
paper's attribution CLASSIFIER (that would be a verdict, out of bounds).

Posture (no verdict): not authorship/AI, not a quality/readability score, and
NOT length-controlled (density and centrality concentration covary with text
length and function-word-set coverage; `n_active_nodes` / `total_transitions`
co-reported). The band is descriptive (structure-concentration phrases, never an
AI/human/author class), carried by NAMED provisional `flagged_signals` +
`calibration_status` — there is NO bare `band.score` (spec 32 §13 P2). Below a
concrete transition floor the band is suppressed. Thresholds operator-side /
PROVISIONAL. M1 = stdlib + numpy (numpy is an ambient CI transitive dep via
scipy); NO networkx — every graph descriptor is computed directly on a numpy
adjacency matrix.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
# Reuse the grammar audit's run-segmentation primitive + tokenizer so FWAN
# reads the IDENTICAL transitions (single source of truth, spec 32 §2c.3).
from function_word_grammar_audit import _tokens_lower  # noqa: E402
from stylometry_core import FUNCTION_WORDS  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "function_word_adjacency_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_TOP_K = 15
DEFAULT_PAGERANK_DAMPING = 0.85
_PAGERANK_MAX_ITER = 200
_PAGERANK_TOL = 1e-12

# Band floor: a stable ~135-node directed graph estimated from one document is
# sparse; PageRank / per-node entropy on a thin graph are high-variance. Below
# this many observed transitions the band is SUPPRESSED (raw values still
# emitted), mirroring variance_audit's "Insufficient signal" band (spec 32 §13
# P3). Provisional, operator-side.
BAND_TRANSITION_FLOOR = 200

# The four NAMED provisional band signals (spec 32 §3). Each is a structure-
# concentration cue with a PROVISIONAL, operator-side threshold — no calibrated
# provenance ships (n_calibrated == 0). These are the ONLY band drivers; there
# is no derived band scalar.
_BAND_SIGNAL_NAMES = (
    "low_global_transition_entropy",
    "high_pagerank_concentration",
    "low_per_node_entropy_mean",
    "low_graph_density",
)
# Provisional cut points (heuristic; calibration-pending — read as a cue, not a
# verdict). Direction is encoded by the comparison in `_band`.
_THRESH_LOW_GLOBAL_ENTROPY_BITS = 4.0
_THRESH_HIGH_PAGERANK_GINI = 0.65
_THRESH_LOW_PER_NODE_ENTROPY_BITS = 1.5
_THRESH_LOW_DENSITY = 0.10


def function_word_runs(text: str) -> list[list[str]]:
    """The content-word-delimited runs of `FUNCTION_WORDS` members, len >= 2.

    Identical segmentation to `function_word_grammar_audit` (its `_tokens_lower`
    tokenizer + the `len(cur_run) >= 2` rule, lines 153-163): a maximal run of
    consecutive function-word tokens, broken by any non-function token. Runs of
    length < 2 carry no adjacency and are dropped — the same rule that keeps the
    edge-total tie exact (spec 32 §13 P1)."""
    toks = _tokens_lower(text)
    runs: list[list[str]] = []
    cur: list[str] = []
    for tok in toks:
        if tok in FUNCTION_WORDS:
            cur.append(tok)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _bigram_counts(runs: list[list[str]]) -> Counter:
    """The FULL directed-transition Counter over the runs (no truncation).

    This is the un-truncated counterpart of the grammar audit's local
    `bigram_counts` (whose public view is `.most_common(20)`); FWAN recomputes
    it from the SAME runs so `total_transitions` ties to the run segmentation,
    not to the truncated `function_bigrams` field."""
    counts: Counter = Counter()
    for run in runs:
        for i in range(len(run) - 1):
            counts[(run[i], run[i + 1])] += 1
    return counts


def _entropy_bits(weights: np.ndarray) -> float:
    """Shannon entropy (bits) of a non-negative weight vector. 0.0 for an
    all-zero vector (no outgoing mass — a sink). Pure numpy."""
    total = float(weights.sum())
    if total <= 0.0:
        return 0.0
    p = weights[weights > 0] / total
    return float(-(p * np.log2(p)).sum())


def _pagerank(matrix: np.ndarray, damping: float) -> np.ndarray:
    """Damped PageRank via power iteration on the row-normalized transition
    matrix. Dangling nodes (zero out-weight) redistribute uniformly. Returns a
    probability vector that sums to ~1.0. Deterministic: fixed start (uniform),
    fixed iteration cap, L1 tolerance. No networkx, no scipy eigensolver."""
    n = matrix.shape[0]
    if n == 0:
        return np.zeros(0, dtype=float)
    if n == 1:
        return np.ones(1, dtype=float)
    out = matrix.sum(axis=1)
    # Row-normalize; dangling rows (out == 0) become uniform so mass is not lost.
    trans = np.zeros((n, n), dtype=float)
    nonzero = out > 0
    trans[nonzero] = matrix[nonzero] / out[nonzero, None]
    trans[~nonzero] = 1.0 / n
    teleport = np.full(n, (1.0 - damping) / n, dtype=float)
    rank = np.full(n, 1.0 / n, dtype=float)
    for _ in range(_PAGERANK_MAX_ITER):
        nxt = teleport + damping * (trans.T @ rank)
        if float(np.abs(nxt - rank).sum()) < _PAGERANK_TOL:
            rank = nxt
            break
        rank = nxt
    s = float(rank.sum())
    if s > 0:
        rank = rank / s
    return rank


def _gini(values: np.ndarray) -> float:
    """Gini concentration of a non-negative vector in [0, 1]. 0 = perfectly
    uniform, higher = more concentrated. Population form over the sorted vector;
    0.0 for an empty/all-zero/singleton vector (no concentration defined)."""
    n = values.size
    if n <= 1:
        return 0.0
    total = float(values.sum())
    if total <= 0.0:
        return 0.0
    sorted_v = np.sort(values)
    index = np.arange(1, n + 1, dtype=float)
    return float((2.0 * (index * sorted_v).sum()) / (n * total) - (n + 1.0) / n)


def _top_k(labels: list[str], scores: np.ndarray, k: int,
           *, as_int: bool = False) -> list[list[Any]]:
    """Top-K `[token, value]` pairs by descending score, ties broken by token
    (deterministic). Function-words-only labels — privacy-safe."""
    order = sorted(range(len(labels)), key=lambda i: (-scores[i], labels[i]))
    out: list[list[Any]] = []
    for i in order[:k]:
        v = int(scores[i]) if as_int else round(float(scores[i]), 6)
        out.append([labels[i], v])
    return out


def _band(global_entropy_bits: float, pagerank_gini: float,
          per_node_mean_bits: float, density: float,
          total_transitions: int) -> dict[str, Any]:
    """The descriptive structure-concentration band. Carried by NAMED provisional
    signals + calibration_status — NO bare derived score (spec 32 §13 P2).

    Below `BAND_TRANSITION_FLOOR` observed transitions the band is SUPPRESSED
    (`label == "insufficient structure"`, `band_offered == false`); the raw graph
    values are still emitted. All four signals are provisional/operator-side
    (n_calibrated == 0)."""
    calibration_status = {
        "n_calibrated": 0,
        "n_provisional": len(_BAND_SIGNAL_NAMES),
        "n_total": len(_BAND_SIGNAL_NAMES),
        "calibrated_signals": [],
        "provisional_signals": list(_BAND_SIGNAL_NAMES),
    }
    if total_transitions < BAND_TRANSITION_FLOOR:
        return {
            "label": "insufficient structure",
            "flagged_signals": [],
            "n_flagged": 0,
            "n_signals": len(_BAND_SIGNAL_NAMES),
            "band_offered": False,
            "calibration_status": calibration_status,
        }

    flagged: list[str] = []
    if global_entropy_bits < _THRESH_LOW_GLOBAL_ENTROPY_BITS:
        flagged.append("low_global_transition_entropy")
    if pagerank_gini > _THRESH_HIGH_PAGERANK_GINI:
        flagged.append("high_pagerank_concentration")
    if per_node_mean_bits < _THRESH_LOW_PER_NODE_ENTROPY_BITS:
        flagged.append("low_per_node_entropy_mean")
    if density < _THRESH_LOW_DENSITY:
        flagged.append("low_graph_density")

    # Descriptive structure-concentration phrase — NEVER an AI/human/author
    # class. Driven only by how many NAMED provisional signals fired.
    n_flagged = len(flagged)
    if n_flagged == 0:
        label = "diffuse structure"
    elif n_flagged <= 2:
        label = "typical structure"
    else:
        label = "concentrated structure"

    return {
        "label": label,
        "flagged_signals": flagged,
        "n_flagged": n_flagged,
        "n_signals": len(_BAND_SIGNAL_NAMES),
        "band_offered": True,
        "calibration_status": calibration_status,
    }


def audit_function_word_adjacency(text: str, *, top_k: int = DEFAULT_TOP_K,
                                  pagerank_damping: float = DEFAULT_PAGERANK_DAMPING
                                  ) -> dict[str, Any]:
    """The function-word adjacency network of `text`. Pure function; no I/O.

    Raises ValueError if the text yields zero function-word transitions (too
    short / no adjacent function words) — the caller maps that to bad_input."""
    runs = function_word_runs(text)
    counts = _bigram_counts(runs)
    total_transitions = int(sum(counts.values()))
    if total_transitions == 0:
        raise ValueError(
            "no function-word transitions found (too short / no adjacent "
            "function words)"
        )

    # Active nodes: function words that participate in >= 1 transition (as a
    # source or a target). Sorted for determinism.
    active: set[str] = set()
    for (a, b) in counts:
        active.add(a)
        active.add(b)
    nodes = sorted(active)
    n = len(nodes)
    idx = {w: i for i, w in enumerate(nodes)}

    matrix = np.zeros((n, n), dtype=float)
    for (a, b), c in counts.items():
        matrix[idx[a], idx[b]] = c

    # --- network-level descriptors ---
    # Binarized off-diagonal adjacency for edge counts / density / motifs.
    binar = (matrix > 0).astype(float)
    off = binar.copy()
    np.fill_diagonal(off, 0.0)
    n_directed_edges = int(binar.sum())            # incl. self-loops (A->A)
    n_self_loops = int(np.diag(binar).sum())
    n_off_edges = int(off.sum())
    possible = n * (n - 1)                          # directed, no self-loops
    density = round(n_off_edges / possible, 6) if possible > 0 else 0.0

    # Reciprocity over off-diagonal directed edges: share whose reverse exists.
    recip_mask = (off > 0) & (off.T > 0)
    n_recip_edges = int(recip_mask.sum())          # counts each direction
    reciprocity = round(n_recip_edges / n_off_edges, 6) if n_off_edges > 0 else 0.0

    # Weight asymmetry over reciprocated UNORDERED pairs (i<j). This is a
    # RECIPROCATED-only directionality measure: a purely one-directional edge has
    # no reverse weight to compare against and is NOT counted (it would read as
    # asymmetry 1.0, but the pair is not reciprocated). To keep the directionality
    # story complete, the share of off-diagonal edges that are one-directional is
    # co-reported as `one_directional_edge_share` so the field name cannot be read
    # as a global asymmetry on its own (spec 32 §13 P4).
    asyms: list[float] = []
    iu, ju = np.triu_indices(n, k=1)
    for i, j in zip(iu.tolist(), ju.tolist()):
        wij = matrix[i, j]
        wji = matrix[j, i]
        s = wij + wji
        if s > 0 and wij > 0 and wji > 0:
            asyms.append(abs(wij - wji) / s)
    reciprocated_weight_asymmetry_mean = (
        round(float(np.mean(asyms)), 6) if asyms else 0.0)
    # Off-diagonal edges with no reverse edge (the maximally asymmetric structure
    # that the reciprocated-only mean cannot see): one_directional / n_off_edges.
    n_one_directional_edges = int(((off > 0) & ~(off.T > 0)).sum())
    one_directional_edge_share = (
        round(n_one_directional_edges / n_off_edges, 6) if n_off_edges > 0 else 0.0)

    # --- centrality ---
    out_degree = matrix.sum(axis=1)                # weighted out-degree
    in_degree = matrix.sum(axis=0)                 # weighted in-degree
    pagerank = _pagerank(matrix, pagerank_damping)
    pagerank_gini = round(_gini(pagerank), 6)
    pagerank_top1_share = round(float(pagerank.max()), 6) if n > 0 else 0.0

    # --- transition entropy ---
    # Per-node outgoing-transition entropy is only defined where a successor
    # distribution EXISTS — i.e. over SOURCE nodes (out_degree > 0). SINK nodes
    # (out_degree == 0: function words that only ever appear as a transition
    # TARGET) have an all-zero outgoing row; `_entropy_bits` returns 0.0 for them,
    # which would (a) always win `argmin` and surface a sink as the "most
    # predictable successor distribution" (it has NO successor distribution), and
    # (b) dilute `per_node_mean_bits` downward, biasing the low_per_node_entropy
    # band signal. So the summaries are computed over source nodes ONLY; the sink
    # count is reported separately (spec 32 §13 P4).
    source_idx = [i for i in range(n) if out_degree[i] > 0]
    n_sink_nodes = n - len(source_idx)
    per_node_bits = np.array(
        [_entropy_bits(matrix[i]) for i in source_idx], dtype=float)
    # Global transition entropy over the FULL transition distribution (not top-20).
    global_bits = _entropy_bits(matrix.reshape(-1))
    if per_node_bits.size > 0:
        per_node_mean = round(float(per_node_bits.mean()) + 0.0, 6)  # +0.0: kill -0.0
        per_node_sd = round(float(per_node_bits.std()), 6)
        min_local = int(per_node_bits.argmin())
        max_local = int(per_node_bits.argmax())
        min_i = source_idx[min_local]
        max_i = source_idx[max_local]
        min_bits = round(float(per_node_bits[min_local]) + 0.0, 6)  # +0.0: kill -0.0
        max_bits = round(float(per_node_bits[max_local]) + 0.0, 6)
        min_entropy_node = [nodes[min_i], min_bits]
        max_entropy_node = [nodes[max_i], max_bits]
    else:
        # Defensive: every active node is a sink. Cannot happen while
        # total_transitions > 0 (every transition has a source), but keep the
        # leaves finite and the descriptor honest rather than picking a sink.
        per_node_mean = 0.0
        per_node_sd = 0.0
        min_entropy_node = None
        max_entropy_node = None

    # --- motifs ---
    # 2-cycles: unordered reciprocated off-diagonal pairs.
    two_cycles = int(recip_mask.sum() // 2)
    # length-3 directed WALKS A->B->C over the binarized off-diagonal adjacency:
    # (off @ off)[i,k] = #length-2 walks i->j->k; summing counts every ordered
    # (i,j,k) with both edges present. A WALK count (revisits allowed), NOT a
    # chordless-path count — documented as such.
    directed_path3 = int((off @ off).sum())
    self_loops = n_self_loops

    k = max(1, top_k)
    band = _band(global_bits, pagerank_gini, per_node_mean, density, total_transitions)

    return {
        "graph": {
            "n_active_nodes": n,
            "n_possible_nodes": len(FUNCTION_WORDS),
            "n_directed_edges": n_directed_edges,
            "total_transitions": total_transitions,
            "density": density,
            "reciprocity": reciprocity,
            "reciprocated_weight_asymmetry_mean": reciprocated_weight_asymmetry_mean,
            "one_directional_edge_share": one_directional_edge_share,
        },
        "centrality": {
            "top_by_pagerank": _top_k(nodes, pagerank, k),
            "top_by_out_degree": _top_k(nodes, out_degree, k, as_int=True),
            "top_by_in_degree": _top_k(nodes, in_degree, k, as_int=True),
            "pagerank_gini": pagerank_gini,
            "pagerank_top1_share": pagerank_top1_share,
            "pagerank_damping": round(float(pagerank_damping), 6),
        },
        "transition_entropy": {
            "global_bits": round(global_bits + 0.0, 6),  # +0.0: normalize -0.0
            "per_node_mean_bits": per_node_mean,
            "per_node_sd_bits": per_node_sd,
            "n_source_nodes": len(source_idx),
            "n_sink_nodes": n_sink_nodes,
            # Over SOURCE nodes only (out-degree > 0); sinks have no successor
            # distribution and are excluded. None iff every node is a sink.
            "min_entropy_node": min_entropy_node,
            "max_entropy_node": max_entropy_node,
        },
        "motifs": {
            "two_cycles": two_cycles,
            "directed_path3": directed_path3,
            "self_loops": self_loops,
        },
        "band": band,
        "assumptions": {
            "method": "function-word adjacency network (arXiv:1406.4469); directed, "
                      "content-word-delimited runs; nodes=FUNCTION_WORDS, edges=adjacent "
                      "transitions",
            "node_set": "the 135 canonical FUNCTION_WORDS (variance_audit.py); content "
                        "words break runs; runs of len<2 carry no edge",
            "edge_total_tie": "total_transitions == the run-segmentation bigram total "
                              "(same _tokens_lower + len(run)>=2 rule as "
                              "function_word_grammar_audit); NOT the truncated "
                              "function_bigrams top-20 view",
            "pagerank": "power-iteration on the row-normalized transition matrix, damping "
                        f"{pagerank_damping}, dangling nodes uniform; stdlib+numpy (no networkx)",
            "directed_path3": "count of length-3 directed WALKS (revisits allowed), not "
                              "chordless paths",
            "confounds": "graph density and centrality concentration rise with text length "
                         "and shrink with function-word-set coverage; n_active_nodes and "
                         "total_transitions co-reported so the confound is visible — NOT "
                         "length-controlled",
            "band": "descriptive structure-concentration phrase from NAMED provisional "
                    "signals; no derived score; suppressed below the transition floor",
            "no_verdict": "descriptive structure only; no authorship, no AI/human, no "
                          "quality claim",
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The graph-structural profile of the target's function-word transition "
            "network — node centralities (in/out-degree and a PageRank stationary "
            "centrality), per-node and global transition entropy, directionality "
            "(reciprocity / weight asymmetry), network density, and small directed "
            "motifs (2-cycles, length-3 directed walks, self-loops). A descriptive "
            "structural view of the SAME function-word transitions "
            "function_word_grammar_audit reads as flat top-20 bigrams. Reported as "
            "values plus a descriptive structure-concentration band (no derived score)."
        ),
        "does_not_license": (
            "Any AI/human or authorship verdict — arXiv:1406.4469 builds these networks "
            "for ATTRIBUTION, and SETEC reproduces only the feature construction; the "
            "paper's attribution CLASSIFIER is explicitly NOT reproduced. No "
            "writing-quality or readability judgment. No length-controlled reading: "
            "graph density and centrality concentration covary mechanically with text "
            "length and function-word-set coverage (n_active_nodes and total_transitions "
            "are co-reported so the confound is visible). No cross-language use (the "
            "FUNCTION_WORDS set is English). The band is a descriptive cue, NOT a "
            "decision: it is driven only by named provisional signals, ships "
            "n_calibrated==0, and is suppressed below the transition floor. No verdict; "
            "thresholds operator-side / PROVISIONAL."
        ),
    }


_CAVEATS = [
    "Without spaCy, function words like `that` / `which` / `as` are counted by "
    "surface form, not syntactic function (the M2 POS-disambiguated refinement is "
    "optional and deferred).",
    "The structure-concentration band is calibration-pending — treat it as a cue, "
    "not a verdict (calibration_status.n_calibrated == 0).",
    "Graph descriptors are genre-bound (telegraphic vs. periodic prose give "
    "different densities) — read alongside register match.",
]


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read target: {e}", reason_category="bad_input")
    try:
        results = audit_function_word_adjacency(
            text, top_k=args.top_k, pagerank_damping=args.pagerank_damping)
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), reason=str(e),
            reason_category="bad_input")

    n_words = len(_tokens_lower(text))
    warnings = None
    if results["graph"]["total_transitions"] < BAND_TRANSITION_FLOOR:
        warnings = [
            f"only {results['graph']['total_transitions']} function-word "
            f"transitions (< {BAND_TRANSITION_FLOOR}); the band is suppressed and "
            "the graph descriptors may be unstable"
        ]

    lic = from_legacy(_claim_license(), task_surface=TASK_SURFACE)
    lic.additional_caveats = list(_CAVEATS)
    lic.references = [
        "specs/32-function-word-adjacency.md",
        "Authorship Attribution Using Word Network Features (Segarra, Eisen, "
        "Ribeiro; arXiv:1406.4469): https://arxiv.org/abs/1406.4469",
    ]
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=n_words, baseline=None,
        results=results, claim_license=lic, warnings=warnings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Path to the target text.")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help=f"Top-K centrality rankings to emit (default {DEFAULT_TOP_K}).")
    ap.add_argument("--pagerank-damping", type=float, default=DEFAULT_PAGERANK_DAMPING,
                    help=f"PageRank damping factor (default {DEFAULT_PAGERANK_DAMPING}).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.top_k < 1:
        sys.stderr.write("[function_word_adjacency_audit] --top-k >= 1\n")
        return 2
    if not (0.0 < args.pagerank_damping < 1.0):
        sys.stderr.write("[function_word_adjacency_audit] --pagerank-damping in (0, 1)\n")
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
