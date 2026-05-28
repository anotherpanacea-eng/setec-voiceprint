#!/usr/bin/env python3
"""feature_dedup.py — Stage B4: embedding-based dedup of B3 candidates.

Russell et al. 2026 §2.1 ("Feature deduplication"):

  > We deduplicate them with embedding-based clustering: each feature
  > is represented by its name, question, and detection method,
  > encoded with F2LLM-4B, then clustered with single linkage at
  > cosine threshold 0.85. We keep the feature nearest each cluster
  > centroid as the representative, reducing the taxonomy from 408
  > to 304 features (25.5% reduction) and merging 65 multi-feature
  > clusters.

This script implements the same procedure with two adaptations:

  * The default embedding backend is the SETEC framework's existing
    `embedding_backend.py` (`mxbai-embed-large-v1`). Operators wanting
    the paper's F2LLM-4B install it locally and pass
    `--embedding-model f2llm-4b`; the sentence-transformers loader
    in `embedding_backend.py` accepts any HF model id.

  * Clustering is "single linkage at cosine threshold 0.85": iteratively
    merge feature pairs whose cosine similarity exceeds the threshold.
    Equivalent to building the similarity graph and taking connected
    components. The output cluster representative is the feature
    nearest the cluster centroid in cosine distance.

Input shape (`--candidates-jsonl`):

    {"feature_id": "...", "name": "...", "question": "...",
     "options": [...], "dimension": "...", "response_type": "...",
     "detection_method": "...", "source_run": int}

The `source_run` field is the index of the discovery pass that
proposed the feature (paper runs 3 passes); it propagates into the
cluster lineage for provenance.

Output shape (`--out-jsonl`):

    {"feature_id": "...", ..., "cluster_id": int,
     "cluster_members": [...source feature_ids...],
     "merged_from_runs": [int, ...]}
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
for p in (str(SCRIPT_DIR), str(PARENT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from manifest_format import (  # type: ignore  # noqa: E402
    StageSidecar, load_jsonl, utc_now, write_jsonl,
)

SCRIPT_VERSION = "0.1.0"

DEFAULT_THRESHOLD = 0.85
DEFAULT_EMBEDDING_MODEL = "mxbai"


@dataclass
class Candidate:
    feature_id: str
    name: str
    question: str
    options: list[str] = field(default_factory=list)
    dimension: str = ""
    response_type: str = ""
    detection_method: str = ""
    source_run: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def encode_text(self) -> str:
        return "\n".join([
            f"name: {self.name}",
            f"question: {self.question}",
            f"options: {', '.join(self.options)}",
            f"dimension: {self.dimension}",
            f"detection_method: {self.detection_method}",
        ])


def load_candidates(path: Path) -> list[Candidate]:
    out: list[Candidate] = []
    for d in load_jsonl(path):
        out.append(Candidate(
            feature_id=str(d["feature_id"]),
            name=str(d.get("name", "")),
            question=str(d.get("question", "")),
            options=list(d.get("options", []) or []),
            dimension=str(d.get("dimension", "")),
            response_type=str(d.get("response_type", "")),
            detection_method=str(d.get("detection_method", "")),
            source_run=d.get("source_run"),
            raw=dict(d),
        ))
    return out


# ---------- cosine similarity --------------------------------------

def normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return list(vec)
    return [x / norm for x in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------- single-linkage clustering ------------------------------

def single_linkage_clusters(
    similarities: dict[tuple[int, int], float],
    n: int,
    threshold: float,
) -> list[list[int]]:
    """Connected-components clustering: merge pairs above threshold."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for (i, j), sim in similarities.items():
        if sim >= threshold:
            union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        clusters.setdefault(root, []).append(i)
    return list(clusters.values())


def cluster_representative(
    members: list[int],
    embeddings: list[list[float]],
) -> int:
    """Return the index of the feature nearest the cluster centroid in
    cosine distance. For singleton clusters, the lone member."""
    if len(members) == 1:
        return members[0]
    dim = len(embeddings[0])
    centroid = [0.0] * dim
    for i in members:
        for d in range(dim):
            centroid[d] += embeddings[i][d]
    centroid = normalize(centroid)
    best_idx = members[0]
    best_sim = -2.0
    for i in members:
        sim = cosine_similarity(embeddings[i], centroid)
        if sim > best_sim:
            best_sim = sim
            best_idx = i
    return best_idx


# ---------- embedding -----------------------------------------------

def embed_candidates(
    candidates: list[Candidate],
    *,
    model_alias: str,
) -> list[list[float]]:
    try:
        from embedding_backend import EmbeddingBackend  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "embedding_backend.py is required for stage B4; "
            "ensure scripts/embedding_backend.py is on PYTHONPATH"
        ) from exc
    backend = EmbeddingBackend(model_alias)
    texts = [c.encode_text() for c in candidates]
    raw = backend.encode(texts)
    # raw is a 2-D array-like; normalize per-row.
    out: list[list[float]] = []
    for vec in raw:
        out.append(normalize(list(vec)))
    return out


# ---------- main ---------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage B4: dedup candidate features via embedding clustering.",
    )
    parser.add_argument(
        "--candidates-jsonl", type=Path, required=True,
    )
    parser.add_argument(
        "--out-jsonl", type=Path, required=True,
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD,
    )
    parser.add_argument(
        "--embedding-model", default=DEFAULT_EMBEDDING_MODEL,
        help=(
            "Embedding model alias resolvable by "
            "embedding_backend.py. Default 'mxbai' "
            "(mxbai-embed-large-v1). Paper used F2LLM-4B; pass "
            "'f2llm-4b' to use that instead, provided the model is "
            "installed locally."
        ),
    )
    parser.add_argument(
        "--no-embed", action="store_true",
        help=(
            "Skip the embedding step and use Jaccard similarity over "
            "the candidate text tokens (debug / smoke-test path; "
            "produces lower-quality clusters than embeddings)."
        ),
    )
    args = parser.parse_args(argv)

    candidates = load_candidates(args.candidates_jsonl)
    if not candidates:
        print("error: no candidates loaded", file=sys.stderr)
        return 2

    if args.no_embed:
        # Token-Jaccard fallback for offline smoke-tests.
        token_sets = [
            set(c.encode_text().lower().split())
            for c in candidates
        ]
        embeddings = None
        similarities: dict[tuple[int, int], float] = {}
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                a, b = token_sets[i], token_sets[j]
                inter = len(a & b)
                union = len(a | b)
                similarities[(i, j)] = (
                    inter / union if union else 0.0
                )
    else:
        embeddings = embed_candidates(
            candidates, model_alias=args.embedding_model,
        )
        similarities = {}
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                similarities[(i, j)] = cosine_similarity(
                    embeddings[i], embeddings[j],
                )

    clusters = single_linkage_clusters(
        similarities, len(candidates), args.threshold,
    )

    out_rows = []
    for cid, members in enumerate(sorted(
        clusters, key=lambda m: -len(m),
    )):
        if embeddings is not None:
            rep_idx = cluster_representative(members, embeddings)
        else:
            # No embeddings → keep the alphabetically-first feature_id
            rep_idx = min(
                members,
                key=lambda i: candidates[i].feature_id,
            )
        rep = candidates[rep_idx]
        member_ids = [candidates[i].feature_id for i in members]
        merged_runs = sorted({
            candidates[i].source_run
            for i in members
            if candidates[i].source_run is not None
        })
        row = dict(rep.raw)
        row["cluster_id"] = cid
        row["cluster_members"] = member_ids
        row["merged_from_runs"] = merged_runs
        out_rows.append(row)

    n_written = write_jsonl(args.out_jsonl, out_rows)

    sidecar = StageSidecar(
        stage="B4",
        tool="scripts/replication/feature_dedup.py",
        version=SCRIPT_VERSION,
        prompt_fingerprint_sha256=None,
        judge_identity={
            "kind": "embedding",
            "model": args.embedding_model if not args.no_embed
            else "jaccard_tokens_fallback",
            "threshold": args.threshold,
        },
        input_manifest_sha256=None,
        row_count=n_written,
        completed_at_utc=utc_now(),
        row_status={
            "ok": n_written,
            "input_candidates": len(candidates),
            "merged_clusters": sum(
                1 for r in out_rows if len(r["cluster_members"]) > 1
            ),
        },
    )
    sidecar.write(
        args.out_jsonl.with_name(
            args.out_jsonl.stem + ".manifest.json",
        ),
    )

    n_merged = sum(1 for r in out_rows if len(r["cluster_members"]) > 1)
    print(
        f"Dedup: {len(candidates)} candidates → {n_written} "
        f"features ({n_merged} multi-feature clusters)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
