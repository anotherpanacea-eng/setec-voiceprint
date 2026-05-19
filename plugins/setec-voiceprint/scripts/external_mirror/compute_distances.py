"""Phase B step 2: compute pairwise cosine distances.

Takes ingested.json (from ingest_outputs.py) and an optional target-
continuation file, embeds all per-family per-window normalized continuations
+ target continuations with embedding_backend, and emits a per-window
distance matrix.

v1 ships sbert distance only (via embedding_backend's default mxbai model
or operator-supplied alias). TF-IDF / POS-bigram / word-set Jaccard
distances are deferred to a v2 follow-up.

Implements SPEC_external_mirror_phase_b.md v0.1.

CLI:
    python3 compute_distances.py INGESTED_JSON \
        [--out PATH] [--embedding-alias mxbai] [--target-continuation PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


SCRIPT_VERSION = "0.2.0"

# v2 metric names. ``sbert`` is the v1 default; the others were
# deferred from v1 per SPEC_external_mirror_phase_b.md scope cut.
ALL_METRICS: tuple[str, ...] = (
    "sbert",
    "tfidf",
    "pos_bigram_cosine",
    "pos_bigram_jaccard",
    "word_jaccard",
)


# Lazy spaCy loader for the POS-bigram metrics. Mirrors the framework
# convention from stylometry_core (HAS_SPACY flag + module-level _NLP).
# Tests don't need spaCy installed — metrics gated on HAS_SPACY emit
# None matrices + a skip caveat when the model isn't available.
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

# Lazy sklearn import for TF-IDF. Same graceful-degradation pattern.
try:
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    HAS_SKLEARN = True
except ImportError:
    TfidfVectorizer = None  # type: ignore
    HAS_SKLEARN = False


def _load_embedding_backend(alias: str):
    """Import lazily so tests can stub without requiring sentence-transformers.

    The lazy import is gated on the module-load sys.path prepend above; running
    this file as a CLI (``python3 compute_distances.py ...``) only adds
    ``scripts/external_mirror/`` to sys.path, not the parent ``scripts/``
    directory where embedding_backend lives. Matches the pattern in
    compose_evidence_pack.py.

    The CLI's ``--embedding-alias`` flag is passed through as
    ``EmbeddingBackend(model_id=...)`` — the dataclass field accepts either a
    MODEL_ALIASES key (e.g. ``"mxbai"``) or a full HuggingFace identifier and
    resolves aliases in ``__post_init__``.
    """
    from embedding_backend import EmbeddingBackend  # type: ignore
    return EmbeddingBackend(model_id=alias)


def _load_target_continuations(path: Path, windows_count: int) -> list[str]:
    """Load target continuations.

    Two supported shapes:
    - JSON array: [{"window": int, "continuation": str}, ...]
    - JSON object: {"1": "...", "2": "...", ...}
    - Plain text: one window's continuation (only valid when windows_count == 1)
    """
    raw = path.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if windows_count == 1:
            return [raw]
        raise ValueError(
            f"--target-continuation file must be JSON when windows_count > 1; "
            f"got plain text with windows_count={windows_count}."
        )

    out = [""] * windows_count
    if isinstance(data, list):
        for entry in data:
            idx = int(entry["window"])
            if not (1 <= idx <= windows_count):
                raise ValueError(f"target_continuation window {idx} out of range [1,{windows_count}]")
            out[idx - 1] = str(entry["continuation"])
    elif isinstance(data, dict):
        for k, v in data.items():
            idx = int(k)
            if not (1 <= idx <= windows_count):
                raise ValueError(f"target_continuation window {idx} out of range [1,{windows_count}]")
            out[idx - 1] = str(v)
    else:
        raise ValueError(f"target_continuation file: unsupported JSON shape {type(data).__name__}")

    return out


# ============================================================
# v2 metric helpers
# ============================================================


_WORD_TOKEN_RE = re.compile(r"[A-Za-z']+")


def _cosine_from_dict_vectors(
    a: dict[str, float], b: dict[str, float],
) -> float | None:
    """Cosine distance over two sparse-dict vectors. Returns
    ``1.0 - cosine_similarity`` or None when either vector is zero
    (cosine is undefined). Mirror of
    ``stylometry_core.cosine_distance`` but takes the implicit
    union of keys rather than a names parameter."""
    keys = set(a.keys()) | set(b.keys())
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for k in keys:
        av = float(a.get(k, 0.0))
        bv = float(b.get(k, 0.0))
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    return 1.0 - (dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


def _jaccard_from_sets(a: set, b: set) -> float:
    """Jaccard distance over two sets: ``1.0 - |intersection| / |union|``.
    Empty-vs-empty returns 0.0 (both empty are identical sets)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return 1.0 - len(a & b) / len(union)


def _word_set(text: str) -> set[str]:
    """Lowercased whitespace-token set. Used for word-set Jaccard."""
    return set(text.lower().split())


def _pos_bigram_freqs(text: str) -> dict[str, float]:
    """spaCy POS-bigram relative-frequency dict for cosine.

    Returns empty dict when spaCy isn't loaded (HAS_SPACY=False);
    callers should treat empty-vs-empty as the gated case and emit
    a skip caveat at the metric level rather than per-pair."""
    if not HAS_SPACY or _NLP is None:
        return {}
    doc = _NLP(text)
    counts: Counter[str] = Counter()
    total = 0
    for sent in doc.sents:
        tags = [t.pos_ for t in sent if not t.is_space]
        for a, b in zip(tags, tags[1:]):
            counts[f"pos:{a}-{b}"] += 1
            total += 1
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def _pos_bigram_set(text: str) -> set[str]:
    """POS-bigram presence set (no frequencies). Used for Jaccard.

    Returns empty set when spaCy isn't loaded."""
    if not HAS_SPACY or _NLP is None:
        return set()
    doc = _NLP(text)
    bigrams: set[str] = set()
    for sent in doc.sents:
        tags = [t.pos_ for t in sent if not t.is_space]
        for a, b in zip(tags, tags[1:]):
            bigrams.add(f"{a}-{b}")
    return bigrams


def _tfidf_cosine_matrix(present_texts: list[str]) -> list[list[float]]:
    """Per-window TF-IDF cosine matrix over the (target + family) set.

    Raises RuntimeError if HAS_SKLEARN is False — callers must gate
    on the flag before invoking. The matrix is N×N where N is the
    number of present texts; callers map back to original indices.
    """
    if not HAS_SKLEARN or TfidfVectorizer is None:
        raise RuntimeError("sklearn unavailable")
    vec = TfidfVectorizer().fit_transform(present_texts)
    # `vec` is a sparse matrix; convert to dense list-of-lists for
    # the pure-Python cosine loop. The matrix is tiny (5-10 rows ×
    # vocab dim) so the perf cost is negligible. Avoid numpy in case
    # the test env lacks it.
    arr = vec.toarray().tolist()
    n = len(arr)
    matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    norms = [math.sqrt(sum(v * v for v in row)) for row in arr]
    for i in range(n):
        for j in range(n):
            if norms[i] == 0.0 or norms[j] == 0.0:
                matrix[i][j] = 1.0  # undefined cosine → max distance
            else:
                dot = sum(a * b for a, b in zip(arr[i], arr[j]))
                matrix[i][j] = 1.0 - dot / (norms[i] * norms[j])
    return matrix


def _project_to_full_matrix(
    sub_matrix: list[list[float | None]],
    present_indices: list[int],
    n_labels: int,
) -> list[list[float | None]]:
    """Map an N_present × N_present sub-matrix into the full
    n_labels × n_labels matrix with None in absent cells."""
    full: list[list[float | None]] = [[None] * n_labels for _ in range(n_labels)]
    for a_i, src_i in enumerate(present_indices):
        for b_i, src_j in enumerate(present_indices):
            full[src_i][src_j] = sub_matrix[a_i][b_i]
    return full


def _resolve_metrics(metrics_arg: list[str] | None) -> tuple[list[str], dict[str, str]]:
    """Return (metrics_to_run, skip_reasons).

    metrics_arg=None means "all available"; gated metrics (tfidf,
    pos_bigram_*) are filtered out with a skip reason when their
    dependencies are absent. Explicit operator request for a gated
    metric still surfaces a skip reason rather than erroring."""
    requested = list(metrics_arg) if metrics_arg else list(ALL_METRICS)
    skip_reasons: dict[str, str] = {}
    to_run: list[str] = []
    for m in requested:
        if m == "sbert":
            to_run.append(m)
        elif m == "tfidf":
            if HAS_SKLEARN:
                to_run.append(m)
            else:
                skip_reasons[m] = "sklearn unavailable"
        elif m in ("pos_bigram_cosine", "pos_bigram_jaccard"):
            if HAS_SPACY:
                to_run.append(m)
            else:
                skip_reasons[m] = "spaCy / en_core_web_sm unavailable"
        elif m == "word_jaccard":
            to_run.append(m)
        else:
            skip_reasons[m] = f"unknown metric {m!r}"
    return to_run, skip_reasons


def compute(
    ingested: dict,
    *,
    embedding_alias: str = "mxbai",
    target_continuations: list[str] | None = None,
    backend=None,
    metrics: list[str] | None = None,
) -> dict:
    """Compute the distance matrices. Returns the distances.json payload.

    ``backend`` is injectable for tests; if None, the embedding_backend is
    loaded lazily via the alias.

    ``metrics`` (v2): list of metric names to run. Defaults to all
    available given the test/runtime environment's installed
    dependencies. Metrics whose deps are unavailable are recorded in
    ``metric_skip_reasons`` rather than erroring.
    """
    windows_count = ingested["manifest"]["windows_count"]
    families = ingested["families"]
    family_labels = [f["family"] for f in families]

    have_target = target_continuations is not None and any(t.strip() for t in target_continuations)

    metrics_to_run, skip_reasons = _resolve_metrics(metrics)
    sbert_enabled = "sbert" in metrics_to_run

    if sbert_enabled and backend is None:
        backend = _load_embedding_backend(embedding_alias)

    labels_per_window: list[list[str]] = []
    matrices: list[list[list[float | None]]] = []
    per_window_caveats: list[list[str]] = []
    matrices_by_metric: dict[str, list[list[list[float | None]]] | None] = {
        m: [] for m in ALL_METRICS
    }
    for m in ALL_METRICS:
        if m not in metrics_to_run:
            matrices_by_metric[m] = None

    for w in range(1, windows_count + 1):
        labels: list[str] = []
        texts: list[str] = []
        absent_mask: list[bool] = []
        caveats: list[str] = []

        if have_target:
            t_text = target_continuations[w - 1]
            labels.append("__target__")
            texts.append(t_text)
            absent_mask.append(not t_text.strip())
            if absent_mask[-1]:
                caveats.append("target_continuation_missing_for_window")

        for fam in families:
            labels.append(fam["family"])
            rec = next((r for r in fam["windows"] if r["window_index"] == w), None)
            if rec is None:
                texts.append("")
                absent_mask.append(True)
                caveats.append(f"family_{fam['family']}_window_absent")
            elif "refused" in rec["caveats"]:
                texts.append("")
                absent_mask.append(True)
                caveats.append(f"family_{fam['family']}_refused")
            elif not rec["normalized_text"].strip():
                texts.append("")
                absent_mask.append(True)
                caveats.append(f"family_{fam['family']}_empty")
            else:
                texts.append(rec["normalized_text"])
                absent_mask.append(False)

        present_indices = [i for i, a in enumerate(absent_mask) if not a]
        n = len(labels)
        matrix: list[list[float | None]] = [[None] * n for _ in range(n)]

        present_texts = [texts[i] for i in present_indices] if present_indices else []

        if sbert_enabled and len(present_indices) >= 2:
            embeddings = backend.encode(present_texts, normalize=True)
            vectors = [[float(x) for x in row] for row in embeddings]
            for a_i, src_i in enumerate(present_indices):
                for b_i, src_j in enumerate(present_indices):
                    sim = sum(va * vb for va, vb in zip(vectors[a_i], vectors[b_i]))
                    matrix[src_i][src_j] = 1.0 - sim
            assert matrices_by_metric["sbert"] is not None
            matrices_by_metric["sbert"].append(matrix)
        elif sbert_enabled:
            assert matrices_by_metric["sbert"] is not None
            matrices_by_metric["sbert"].append(matrix)

        # v2 metrics: TF-IDF cosine
        if "tfidf" in metrics_to_run:
            tfidf_full: list[list[float | None]] = [[None] * n for _ in range(n)]
            if len(present_indices) >= 2:
                try:
                    sub = _tfidf_cosine_matrix(present_texts)
                    sub_with_none: list[list[float | None]] = [
                        [float(v) for v in row] for row in sub
                    ]
                    tfidf_full = _project_to_full_matrix(
                        sub_with_none, present_indices, n,
                    )
                except RuntimeError:
                    # sklearn went away between _resolve_metrics and
                    # here — shouldn't happen, but degrade gracefully.
                    pass
            assert matrices_by_metric["tfidf"] is not None
            matrices_by_metric["tfidf"].append(tfidf_full)

        # v2 metrics: POS-bigram cosine + Jaccard
        if "pos_bigram_cosine" in metrics_to_run or "pos_bigram_jaccard" in metrics_to_run:
            if len(present_indices) >= 2:
                pos_freqs = [_pos_bigram_freqs(t) for t in present_texts]
                pos_sets = [_pos_bigram_set(t) for t in present_texts]
            else:
                pos_freqs = []
                pos_sets = []

            if "pos_bigram_cosine" in metrics_to_run:
                pbc_full: list[list[float | None]] = [[None] * n for _ in range(n)]
                if len(present_indices) >= 2:
                    sub: list[list[float | None]] = [[None] * len(present_indices) for _ in present_indices]
                    for i in range(len(present_indices)):
                        for j in range(len(present_indices)):
                            sub[i][j] = _cosine_from_dict_vectors(pos_freqs[i], pos_freqs[j])
                    pbc_full = _project_to_full_matrix(sub, present_indices, n)
                assert matrices_by_metric["pos_bigram_cosine"] is not None
                matrices_by_metric["pos_bigram_cosine"].append(pbc_full)

            if "pos_bigram_jaccard" in metrics_to_run:
                pbj_full: list[list[float | None]] = [[None] * n for _ in range(n)]
                if len(present_indices) >= 2:
                    sub: list[list[float | None]] = [[None] * len(present_indices) for _ in present_indices]
                    for i in range(len(present_indices)):
                        for j in range(len(present_indices)):
                            sub[i][j] = _jaccard_from_sets(pos_sets[i], pos_sets[j])
                    pbj_full = _project_to_full_matrix(sub, present_indices, n)
                assert matrices_by_metric["pos_bigram_jaccard"] is not None
                matrices_by_metric["pos_bigram_jaccard"].append(pbj_full)

        # v2 metrics: word-set Jaccard (always runs)
        if "word_jaccard" in metrics_to_run:
            wj_full: list[list[float | None]] = [[None] * n for _ in range(n)]
            if len(present_indices) >= 2:
                word_sets = [_word_set(t) for t in present_texts]
                sub: list[list[float | None]] = [[None] * len(present_indices) for _ in present_indices]
                for i in range(len(present_indices)):
                    for j in range(len(present_indices)):
                        sub[i][j] = _jaccard_from_sets(word_sets[i], word_sets[j])
                wj_full = _project_to_full_matrix(sub, present_indices, n)
            assert matrices_by_metric["word_jaccard"] is not None
            matrices_by_metric["word_jaccard"].append(wj_full)

        labels_per_window.append(labels)
        matrices.append(matrix)
        per_window_caveats.append(caveats)

    summary = _summarize(family_labels, labels_per_window, matrices, have_target=have_target)

    if not have_target:
        global_caveat = "target_continuation_unavailable"
    else:
        global_caveat = None

    ingested_bytes = json.dumps(ingested, sort_keys=True).encode("utf-8")
    # v2: per-metric summary stats. v1 had a flat dict; v2 keeps the
    # flat dict (= sbert summary, back-compat) and adds a nested
    # ``summary_by_metric`` so consumers can read per-metric stats.
    summary_by_metric: dict[str, dict[str, Any]] = {}
    for m in ALL_METRICS:
        per_m = matrices_by_metric.get(m)
        if per_m is None:
            summary_by_metric[m] = {}
            continue
        summary_by_metric[m] = _summarize(
            family_labels, labels_per_window, per_m,
            have_target=have_target,
        )
    embedding_block_for_payload = (
        backend.identifier_block()
        if (sbert_enabled and backend is not None and hasattr(backend, "identifier_block"))
        else {"alias": embedding_alias} if sbert_enabled else None
    )
    metrics_available = [m for m in ALL_METRICS if matrices_by_metric.get(m) is not None]
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "script_version": SCRIPT_VERSION,
        "embedding_block": embedding_block_for_payload,
        "manifest": ingested["manifest"],
        "families": family_labels,
        "windows_count": windows_count,
        "have_target_continuation": have_target,
        "labels_per_window": labels_per_window,
        # v1 contract: ``distance_matrices`` is the sbert variant.
        # When sbert is not in the requested metrics, this falls back
        # to an empty matrix-list to preserve the back-compat key.
        "distance_matrices": matrices if sbert_enabled else [],
        # v2 contract: per-metric matrices keyed by metric name.
        # None for metrics whose deps were unavailable (see
        # metric_skip_reasons for why).
        "distance_matrices_by_metric": matrices_by_metric,
        "metrics_available": metrics_available,
        "metric_skip_reasons": skip_reasons,
        "per_window_caveats": per_window_caveats,
        # v1 contract: ``summary`` is the sbert summary. v2 adds
        # ``summary_by_metric`` with per-metric stats.
        "summary": summary_by_metric.get("sbert", {}),
        "summary_by_metric": summary_by_metric,
        "ingested_sha256": hashlib.sha256(ingested_bytes).hexdigest(),
        "global_caveats": [global_caveat] if global_caveat else [],
    }
    return payload


def _summarize(
    family_labels: list[str],
    labels_per_window: list[list[str]],
    matrices: list[list[list[float | None]]],
    *,
    have_target: bool,
) -> dict[str, Any]:
    """Per-family summary statistics: mean & median distance to target."""
    summary: dict[str, Any] = {}
    if not have_target:
        return summary

    for fam in family_labels:
        distances: list[float] = []
        for labels, matrix in zip(labels_per_window, matrices):
            if "__target__" not in labels or fam not in labels:
                continue
            ti = labels.index("__target__")
            fi = labels.index(fam)
            cell = matrix[ti][fi]
            if cell is not None:
                distances.append(cell)
        if distances:
            sorted_d = sorted(distances)
            mid = len(sorted_d) // 2
            median = (sorted_d[mid] if len(sorted_d) % 2 else (sorted_d[mid - 1] + sorted_d[mid]) / 2)
            summary[fam] = {
                "n_windows_compared": len(distances),
                "mean_vs_target": sum(distances) / len(distances),
                "median_vs_target": median,
                "min_vs_target": min(distances),
                "max_vs_target": max(distances),
            }
        else:
            summary[fam] = {"n_windows_compared": 0}
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase B step 2: compute pairwise cosine distances."
    )
    parser.add_argument("ingested_json", help="ingested.json from ingest_outputs.py")
    parser.add_argument("--out", default=None, help="Output JSON path (default: <ingested-parent>/distances.json)")
    parser.add_argument("--embedding-alias", default="mxbai", help="embedding_backend alias (default mxbai)")
    parser.add_argument("--target-continuation", default=None, help="JSON/text file with target continuations per window")
    parser.add_argument(
        "--metrics", default=None,
        help=(
            "Comma-separated list of distance metrics to run. Defaults "
            "to all available given the environment's installed deps. "
            f"Choices: {','.join(ALL_METRICS)}. Metrics whose deps are "
            "unavailable (sklearn for tfidf; spaCy+en_core_web_sm for "
            "pos_bigram_*) are recorded in metric_skip_reasons rather "
            "than erroring."
        ),
    )
    args = parser.parse_args(argv)

    ingested_path = Path(args.ingested_json)
    if not ingested_path.exists():
        print(f"error: ingested.json not found at {ingested_path}", file=sys.stderr)
        return 1
    ingested = json.loads(ingested_path.read_text(encoding="utf-8"))

    target_continuations = None
    if args.target_continuation:
        tc_path = Path(args.target_continuation)
        if not tc_path.exists():
            print(f"error: --target-continuation file not found at {tc_path}", file=sys.stderr)
            return 1
        try:
            target_continuations = _load_target_continuations(tc_path, ingested["manifest"]["windows_count"])
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    metrics_list = None
    if args.metrics:
        metrics_list = [m.strip() for m in args.metrics.split(",") if m.strip()]

    try:
        payload = compute(
            ingested,
            embedding_alias=args.embedding_alias,
            target_continuations=target_continuations,
            metrics=metrics_list,
        )
    except Exception as exc:
        print(f"error: distance computation failed: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else ingested_path.parent / "distances.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Computed distances for {len(payload['families'])} families × {payload['windows_count']} windows → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
