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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


SCRIPT_VERSION = "0.1.0"


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


def compute(
    ingested: dict,
    *,
    embedding_alias: str = "mxbai",
    target_continuations: list[str] | None = None,
    backend=None,
) -> dict:
    """Compute the distance matrices. Returns the distances.json payload.

    ``backend`` is injectable for tests; if None, the embedding_backend is
    loaded lazily via the alias.
    """
    windows_count = ingested["manifest"]["windows_count"]
    families = ingested["families"]
    family_labels = [f["family"] for f in families]

    have_target = target_continuations is not None and any(t.strip() for t in target_continuations)

    if backend is None:
        backend = _load_embedding_backend(embedding_alias)

    labels_per_window: list[list[str]] = []
    matrices: list[list[list[float | None]]] = []
    per_window_caveats: list[list[str]] = []

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

        if len(present_indices) >= 2:
            present_texts = [texts[i] for i in present_indices]
            embeddings = backend.encode(present_texts, normalize=True)
            vectors = [[float(x) for x in row] for row in embeddings]
            for a_i, src_i in enumerate(present_indices):
                for b_i, src_j in enumerate(present_indices):
                    sim = sum(va * vb for va, vb in zip(vectors[a_i], vectors[b_i]))
                    matrix[src_i][src_j] = 1.0 - sim

        labels_per_window.append(labels)
        matrices.append(matrix)
        per_window_caveats.append(caveats)

    summary = _summarize(family_labels, labels_per_window, matrices, have_target=have_target)

    if not have_target:
        global_caveat = "target_continuation_unavailable"
    else:
        global_caveat = None

    ingested_bytes = json.dumps(ingested, sort_keys=True).encode("utf-8")
    payload = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "script_version": SCRIPT_VERSION,
        "embedding_block": backend.identifier_block() if hasattr(backend, "identifier_block") else {"alias": embedding_alias},
        "manifest": ingested["manifest"],
        "families": family_labels,
        "windows_count": windows_count,
        "have_target_continuation": have_target,
        "labels_per_window": labels_per_window,
        "distance_matrices": matrices,
        "per_window_caveats": per_window_caveats,
        "summary": summary,
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

    try:
        payload = compute(
            ingested,
            embedding_alias=args.embedding_alias,
            target_continuations=target_continuations,
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
