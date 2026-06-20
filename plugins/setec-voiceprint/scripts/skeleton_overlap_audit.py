#!/usr/bin/env python3
"""skeleton_overlap_audit.py — cross-document discourse-skeleton reuse (spec 28, M1b; QUDsim axis).

The QUDsim *unit of analysis* — compare documents by their ordered discourse skeleton, not their words
(arXiv:2504.09373) — with a **model-free skeleton proxy** so M1 stays CI-runnable. Per document we
segment into discourse units, give each unit a content-word-free *rhetorical-move* signature
(discourse-marker bucket × length-tercile × terminal-punctuation class), discretize it to a symbol, and
form an **ordered skeleton string**. Topic is washed out; what remains is the *shape*. Cross-document
skeleton overlap is a normalized longest-common-subsequence ratio over the symbol strings (stdlib
`difflib`), yielding a descriptive overlap matrix, a top-k pair table, and skeleton-similarity groups.

The M1b skeleton is a PROXY (discourse-marker/structural signatures, not LLM-parsed QUDs). The true
QUD lens is the gated M2 `--qud-lens model` (lazy-import, fail-loud missing_dependency, POC-gated).

Posture (no verdict): high overlap = shared discourse template, **NOT 'AI'** — a shared genre, a tight
prompt, or a house style all raise it legitimately. The claim license refuses any AI/human, plagiarism,
or selection determination; `--report-threshold` only GROUPS for display, it is not a gate.
"""

from __future__ import annotations

import argparse
import difflib
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
from originality_audit import _load_reference_dir, _load_reference_manifest  # noqa: E402
from stylometry_core import split_sentences  # noqa: E402 (regex fallback when NLTK absent — stdlib path)

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "skeleton_overlap_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_TOP_K = 20
DEFAULT_REPORT_THRESHOLD = 0.8
DEFAULT_MIN_DOCS = 3

# Fixed stdlib discourse-marker buckets — the model-free stand-in for the QUD "move". Lowercased,
# matched on the unit's leading words (longest phrase first so "for example" beats a bare "for").
# Content-word-free by construction (G6: topic cannot leak in).
_MARKER_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("concession", ("although", "though", "granted", "admittedly", "despite", "even though")),
    ("contrast", ("however", "but", "yet", "nevertheless", "nonetheless", "still", "whereas",
                  "on the other hand", "in contrast", "conversely")),
    ("cause", ("because", "since", "therefore", "thus", "hence", "consequently", "so", "as a result")),
    ("addition", ("also", "moreover", "furthermore", "additionally", "in addition", "besides")),
    ("exemplification", ("for example", "for instance", "such as", "e.g", "namely", "specifically")),
    ("sequence", ("first", "firstly", "second", "secondly", "third", "then", "next", "finally",
                  "lastly", "subsequently")),
]
_BUCKET_ORDER = [name for name, _ in _MARKER_BUCKETS] + ["none"]
# Compact one-letter codes per bucket so the skeleton string stays human-readable.
_BUCKET_CODE = {name: name[0].upper() for name, _ in _MARKER_BUCKETS}
_BUCKET_CODE["none"] = "N"
# concession C collides with contrast/cause first-letter; assign distinct codes deterministically.
_BUCKET_CODE = {
    "concession": "K", "contrast": "C", "cause": "U", "addition": "A",
    "exemplification": "E", "sequence": "S", "none": "N",
}
_TERMINAL_CODE = {".": "d", "?": "q", "!": "x", "": "o"}  # declarative / question / exclaim / other


def _marker_bucket(unit: str) -> str:
    """Leading-discourse-marker bucket for a unit (content-word-free). Longest phrase wins."""
    head = unit.strip().lower()
    # Strip a leading quote/paren so a quoted opener still classifies on its first word.
    head = head.lstrip("\"'(“‘ ")
    best: tuple[int, str] | None = None  # (phrase_len_words, bucket)
    for name, phrases in _MARKER_BUCKETS:
        for ph in phrases:
            # word-boundary leading match: the unit starts with the phrase followed by a non-letter.
            if head.startswith(ph):
                nxt = head[len(ph):len(ph) + 1]
                if nxt == "" or not nxt.isalpha():
                    pl = ph.count(" ") + 1
                    if best is None or pl > best[0]:
                        best = (pl, name)
    return best[1] if best else "none"


def _terminal_class(unit: str) -> str:
    s = unit.rstrip().rstrip("\"'”’)")
    if not s:
        return ""
    last = s[-1]
    return last if last in ".?!" else ""


def _length_terciles(lengths: list[int]) -> tuple[float, float]:
    """Two cut points splitting unit word-lengths into short/mid/long terciles. Deterministic; falls
    back to a degenerate split when there is no spread (all units same length -> all 'mid')."""
    if len(lengths) < 3 or len(set(lengths)) < 2:
        return (float("-inf"), float("inf"))  # every unit -> mid tercile (no spread to split on)
    ordered = sorted(lengths)
    n = len(ordered)
    return (ordered[n // 3], ordered[2 * n // 3])


def _tercile_code(length: int, cuts: tuple[float, float]) -> str:
    lo, hi = cuts
    if length < lo:
        return "s"
    if length >= hi:
        return "l"
    return "m"


def skeleton_for(text: str) -> dict[str, Any]:
    """Ordered discourse-skeleton for one document: a list of per-unit symbols + the joined string.

    Each symbol = marker-bucket code × length-tercile × terminal class (e.g. 'Cml' = contrast, mid
    length, declarative). Content words never enter the symbol (G6). Deterministic, stdlib."""
    units = split_sentences(text)
    lengths = [len(u.split()) for u in units]
    cuts = _length_terciles(lengths)
    symbols: list[str] = []
    for u, ln in zip(units, lengths):
        sym = (_BUCKET_CODE[_marker_bucket(u)]
               + _tercile_code(ln, cuts)
               + _TERMINAL_CODE.get(_terminal_class(u), "o"))
        symbols.append(sym)
    return {"symbols": symbols, "skeleton": " ".join(symbols), "n_units": len(symbols)}


def _overlap(sa: list[str], sb: list[str]) -> tuple[float, str]:
    """Normalized LCS-ratio over two symbol sequences (difflib) + the shared aligned run (readable).

    SequenceMatcher.ratio() = 2*M/T (M = matched symbols, T = total) — a normalized ordered-alignment
    score in [0,1]. `shared_skeleton` = the matched symbol run, so the operator can read WHY two docs
    overlap (glass-box). Empty-on-either-side -> 0.0 overlap, empty shared run."""
    if not sa or not sb:
        return 0.0, ""
    sm = difflib.SequenceMatcher(a=sa, b=sb, autojunk=False)
    ratio = sm.ratio()
    shared: list[str] = []
    for block in sm.get_matching_blocks():
        if block.size:
            shared.extend(sa[block.a:block.a + block.size])
    # Clamp to [0,1] defensively (difflib is already bounded; protects the R4 finiteness gate).
    ratio = max(0.0, min(1.0, ratio))
    return round(ratio, 6), " ".join(shared)


def _clusters(n: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Connected components over `edges` (union-find), returned as sorted index lists. Singletons are
    dropped (a cluster is >= 2 docs sharing a skeleton)."""
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
    return sorted((sorted(g) for g in groups.values() if len(g) >= 2),
                  key=lambda g: (-len(g), g))


def audit_skeleton_overlap(
    loaded: list[tuple[str, str, Path | None]],
    *,
    top_k: int = DEFAULT_TOP_K,
    report_threshold: float = DEFAULT_REPORT_THRESHOLD,
) -> dict[str, Any]:
    """Cross-document skeleton-overlap matrix + descriptive summaries over a loaded corpus.

    `loaded` is the §S2 loader's 3-tuples. Self-pairs are excluded from the off-diagonal (a doc never
    overlaps itself). Deterministic, stdlib. Raises ValueError if no document yields any discourse
    unit (caller -> bad_input)."""
    per_document: list[dict[str, Any]] = []
    sym_lists: list[list[str]] = []
    ids: list[str] = []
    for src, text, _abs in loaded:
        sk = skeleton_for(text)
        per_document.append({"id": src, "skeleton": sk["skeleton"], "n_units": sk["n_units"]})
        sym_lists.append(sk["symbols"])
        ids.append(src)

    if not any(sym_lists):
        raise ValueError("no document in the corpus segments into discourse units")

    n = len(loaded)
    pair_overlaps: list[float] = []
    pairs: list[dict[str, Any]] = []
    edges: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            ov, shared = _overlap(sym_lists[i], sym_lists[j])
            pair_overlaps.append(ov)
            pairs.append({"a": ids[i], "b": ids[j], "overlap": ov, "shared_skeleton": shared})
            if ov >= report_threshold:
                edges.append((i, j))

    # Descriptive off-diagonal summary. pair_overlaps is non-empty when n >= 2 (the set floor pins this).
    mean_p = round(statistics.fmean(pair_overlaps), 6) if pair_overlaps else 0.0
    median_p = round(statistics.median(pair_overlaps), 6) if pair_overlaps else 0.0
    max_p = round(max(pair_overlaps), 6) if pair_overlaps else 0.0

    # Top-k most-overlapping pairs (descriptive sorted table; NOT a winner pick — every pair is shown
    # in rank order, no boolean "is the top pair" field). Stable sort by (-overlap, a, b).
    pair_table = sorted(pairs, key=lambda p: (-p["overlap"], p["a"], p["b"]))[:top_k]

    cluster_idx = _clusters(n, edges)
    template_clusters = [[ids[i] for i in g] for g in cluster_idx]

    return {
        "n_documents": n,
        "skeleton_overlap": {
            "mean_pairwise": mean_p,
            "median_pairwise": median_p,
            "max_pairwise": max_p,
        },
        "pair_table": pair_table,
        "template_clusters": template_clusters,
        "per_document": per_document,
        "assumptions": {
            "method": "QUDsim-style ordered discourse-skeleton overlap (proxy; arXiv:2504.09373)",
            "proxy_note": "skeleton = ordered discourse-marker/length-tercile/terminal-class "
                          "signatures, NOT LLM-parsed QUDs; an M2 refinement (--qud-lens model)",
            "topic_robust": "content words excluded by construction — high overlap cannot be an "
                            "artifact of a shared subject",
            "orientation": "high overlap = shared discourse template, NOT 'AI' (a shared genre, a "
                           "tight prompt, or a house style all raise it legitimately)",
            "corpus_dependence": "overlap is corpus- and register-dependent; a deliberately templated "
                                 "genre inflates apparent homogenization; ESL/dialect is not adjudicated",
            "qud_lens": "proxy",
            # findings P3: report_threshold only GROUPS for display, surfaced here; it is not a gate.
            "report_threshold": report_threshold,
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The cross-document overlap of an ordered discourse-skeleton proxy — how much the corpus "
            "reuses the same structural template, reported as a descriptive {mean/median/max} pairwise "
            "overlap, a top-k pair table (with the shared aligned skeleton run), and "
            "skeleton-similarity groups. Topic-robust by construction (content words excluded)."
        ),
        "does_not_license": (
            "Any AI/human determination (high skeleton-overlap is NOT 'AI'; low overlap is NOT "
            "'human' — a shared genre, a tight prompt, a single source, or a house style all raise "
            "overlap legitimately). Any plagiarism, derivative-work, or copyright determination — this "
            "is a structural-shape measurement, not a legal claim. Any selection / ranking-as-decision "
            "of documents (the pair table and clusters are read by the human, never an automated "
            "filter); --report-threshold only groups for display. Thresholds are operator-side / "
            "PROVISIONAL; the surface emits no verdict."
        ),
    }


def _model_lens_unavailable() -> dict[str, Any]:
    """M2 --qud-lens model: lazy-import the model client INSIDE this branch; absent -> fail loud
    missing_dependency (G7). Never silently falls back to the proxy (a silent fallback would change
    the meaning of the number)."""
    try:  # pragma: no cover - exercised only when a client is actually installed
        import qud_model_client  # type: ignore  # noqa: F401
        return {}  # a future build wires the real lens here
    except ImportError:
        return {
            "reason": ("--qud-lens model requires an LLM QUD-extraction client (qud_model_client), "
                       "which is not installed; the model lens fails loud rather than silently "
                       "falling back to the stdlib proxy. Use --qud-lens proxy (default) for the "
                       "model-free skeleton."),
            "reason_category": "missing_dependency",
        }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_ref = args.corpus_dir or args.manifest

    if args.qud_lens == "model":
        err = _model_lens_unavailable()
        if err:
            return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                      version=SCRIPT_VERSION, target_path=str(corpus_ref),
                                      reason=err["reason"], reason_category=err["reason_category"])

    try:
        if args.corpus_dir:
            loaded = _load_reference_dir(Path(args.corpus_dir))
        else:
            loaded = _load_reference_manifest(Path(args.manifest))
    except (OSError, UnicodeDecodeError) as e:
        which = "--corpus-dir" if args.corpus_dir else "--manifest"
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(corpus_ref),
                                  reason=f"cannot read {which}: {e}", reason_category="bad_input")

    n_docs = len(loaded)
    if n_docs < args.min_docs:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(corpus_ref),
            reason=(f"corpus has {n_docs} document(s); the skeleton-overlap matrix needs at least "
                    f"--min-docs ({args.min_docs}) — a pairwise matrix over 1-2 docs is meaningless"),
            reason_category="bad_input")

    try:
        results = audit_skeleton_overlap(loaded, top_k=args.top_k,
                                         report_threshold=args.report_threshold)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(corpus_ref),
                                  reason=str(e), reason_category="bad_input")

    total_words = sum(len(t.split()) for _, t, _ in loaded)
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(corpus_ref), target_words=total_words,
        baseline={"corpus": corpus_ref, "n_docs": n_docs},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--corpus-dir", help="Directory of corpus texts (.txt/.md, recursive).")
    g.add_argument("--manifest", help="JSONL manifest of the corpus (id + text|text_path).")
    ap.add_argument("--qud-lens", choices=("proxy", "model"), default="proxy",
                    help="proxy (default, stdlib) | model (gated LLM QUD lens — fails loud if absent).")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help=f"Most-overlapping pairs in pair_table (default {DEFAULT_TOP_K}).")
    ap.add_argument("--report-threshold", type=float, default=DEFAULT_REPORT_THRESHOLD,
                    help=f"Overlap at/above which a pair joins a skeleton-similarity group for display "
                         f"(default {DEFAULT_REPORT_THRESHOLD}); a DESCRIPTIVE grouping threshold "
                         "surfaced in assumptions.report_threshold, NOT a verdict gate.")
    ap.add_argument("--min-docs", type=int, default=DEFAULT_MIN_DOCS,
                    help=f"Minimum corpus size for a matrix (default {DEFAULT_MIN_DOCS}); below it the "
                         "run abstains (bad_input).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.top_k < 1:
        sys.stderr.write("[skeleton_overlap_audit] --top-k must be >= 1\n")
        return 2
    if not (0.0 <= args.report_threshold <= 1.0):
        sys.stderr.write("[skeleton_overlap_audit] --report-threshold must be in [0, 1]\n")
        return 2
    if args.min_docs < 2:
        sys.stderr.write("[skeleton_overlap_audit] --min-docs must be >= 2 (a set needs >= 2 docs)\n")
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
