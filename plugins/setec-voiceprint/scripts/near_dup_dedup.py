#!/usr/bin/env python3
"""near_dup_dedup.py — MinHash-LSH cross-source near-duplicate dedup.

The acquisition pipeline already drops *exact* duplicates: acquisition_core.
content_hash_already_present() skips a piece whose SHA-256 matches one already
written to the same output directory. That guard is byte-exact and single-dir —
it does NOT catch a piece that is *nearly* identical to one already in the pool
(a syndicated repost with a different header, a lightly edited reprint, the same
essay pulled from two sources), nor does it see across output directories /
manifests.

Near-duplicates are a real corpus-hygiene problem for an impostor pool: they
silently over-weight one author/text in the reference distribution, biasing the
voice-distance and General-Imposters baselines the pool feeds. This module adds
the missing capability — a MinHash-LSH near-duplicate pass that runs across the
whole staged corpus (one or more manifests) *before* the final JSONL manifest is
committed, and drops all but one representative of each near-duplicate cluster.

Design:
  * **Model-free, opt-in.** This is an extra pass an operator runs on a staged
    manifest; the acquisition scripts do not call it automatically (their exact-
    hash dedup is unchanged). Invoke via the CLI or import `dedup_records`.
  * **MinHash-LSH** (datasketch, MIT). Each document is shingled into
    overlapping word k-grams, hashed into a MinHash signature, and indexed in an
    LSH bucketed for a Jaccard threshold. Candidate near-dupe pairs are then
    confirmed by their estimated Jaccard, and confirmed pairs are unioned into
    clusters. O(n) index build + near-O(n) candidate lookup — it scales to a
    corpus where an all-pairs cosine would not.
  * **Deterministic representative.** Within a near-duplicate cluster the kept
    record is chosen by a stable rule (longest text, then lowest id) so a rerun
    on the same input drops the same records.

Import purity: datasketch (and its numpy/scipy transitive deps) are imported
lazily inside the functions that need them, so base `import near_dup_dedup`
stays clean when the optional dep is absent. Callers that only want the shingle
helper or want to detect the missing dep get a clear RuntimeError at call time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

TASK_SURFACE = "voice_coherence_acquisition"

DEFAULT_NUM_PERM = 128
DEFAULT_SHINGLE_SIZE = 5
DEFAULT_THRESHOLD = 0.8

_WORD_RE = re.compile(r"\w+", re.UNICODE)


# --------------- Shingling (stdlib) -------------------------------


def shingles(text: str, *, k: int = DEFAULT_SHINGLE_SIZE) -> set[str]:
    """Return the set of overlapping word ``k``-gram shingles of ``text``.

    Tokenized on word characters and lowercased so near-duplicates that differ
    only in whitespace, punctuation, or case still share shingles. Documents
    shorter than ``k`` words collapse to a single whole-document shingle so a
    very short piece is still comparable (rather than yielding an empty set,
    which would make it a near-duplicate of every other empty-shingle piece).
    """
    tokens = [t.lower() for t in _WORD_RE.findall(text)]
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


# --------------- Cluster bookkeeping (stdlib) ---------------------


class _Union:
    """Minimal union-find over hashable ids for clustering confirmed pairs."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Keep the lexicographically smaller root for determinism.
            lo, hi = sorted((ra, rb))
            self._parent[hi] = lo

    def clusters(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in self._parent:
            out.setdefault(self.find(node), []).append(node)
        return out


# --------------- Core dedup (datasketch) --------------------------


@dataclass
class DedupResult:
    """Outcome of a near-duplicate dedup pass.

    ``kept`` / ``dropped`` are ids. ``clusters`` maps each kept
    representative id to the list of dropped near-duplicate ids it
    absorbed (representative excluded). ``threshold`` / ``num_perm`` /
    ``shingle_size`` echo the parameters for provenance.
    """
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    clusters: dict[str, list[str]] = field(default_factory=dict)
    threshold: float = DEFAULT_THRESHOLD
    num_perm: int = DEFAULT_NUM_PERM
    shingle_size: int = DEFAULT_SHINGLE_SIZE
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "kept_count": len(self.kept),
            "dropped_count": len(self.dropped),
            "kept": self.kept,
            "dropped": self.dropped,
            "clusters": self.clusters,
            "threshold": self.threshold,
            "num_perm": self.num_perm,
            "shingle_size": self.shingle_size,
        }


def _require_datasketch():
    try:
        from datasketch import MinHash, MinHashLSH  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "datasketch is not installed. Install acquisition dependencies "
            "with: pip install -r requirements-acquisition.txt "
            "(near-duplicate dedup is an optional acquisition capability)."
        ) from e
    return MinHash, MinHashLSH


def _build_minhash(MinHash, text: str, *, num_perm: int, k: int):
    m = MinHash(num_perm=num_perm)
    for sh in shingles(text, k=k):
        m.update(sh.encode("utf-8"))
    return m


def _pick_representative(
    cluster_ids: list[str], texts: dict[str, str],
) -> str:
    """Deterministic keeper: longest text wins; ties broken by lowest id.

    Longest-text-wins keeps the fullest version of a near-duplicate (a
    truncated repost loses to the complete original); the id tiebreak makes
    the choice stable across reruns.
    """
    return min(cluster_ids, key=lambda cid: (-len(texts.get(cid, "")), cid))


def dedup_records(
    records: Iterable[tuple[str, str]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = DEFAULT_NUM_PERM,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
) -> DedupResult:
    """Find near-duplicate clusters in ``records`` and pick one keeper each.

    ``records`` is an iterable of ``(id, text)``. Two documents are treated as
    near-duplicates when their MinHash-estimated Jaccard similarity is at or
    above ``threshold``. LSH buckets candidate pairs so this stays near-linear;
    each candidate pair is confirmed by estimated Jaccard before it joins a
    cluster (LSH banding admits some false candidates by design). Confirmed
    pairs are unioned into clusters; each cluster keeps one representative
    (:func:`_pick_representative`) and drops the rest.

    Returns a :class:`DedupResult`. Duplicate ids in the input raise
    ``ValueError`` — the manifest id is the join key, so a collision would make
    the keep/drop decision ambiguous.
    """
    MinHash, MinHashLSH = _require_datasketch()

    ids: list[str] = []
    texts: dict[str, str] = {}
    for rid, text in records:
        rid = str(rid)
        if rid in texts:
            raise ValueError(f"duplicate record id in dedup input: {rid!r}")
        ids.append(rid)
        texts[rid] = text or ""

    result = DedupResult(
        threshold=threshold, num_perm=num_perm,
        shingle_size=shingle_size, total=len(ids),
    )
    if len(ids) <= 1:
        result.kept = list(ids)
        return result

    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhashes: dict[str, Any] = {}
    for rid in ids:
        mh = _build_minhash(MinHash, texts[rid], num_perm=num_perm, k=shingle_size)
        minhashes[rid] = mh
        lsh.insert(rid, mh)

    union = _Union()
    for rid in ids:
        union.add(rid)
    # Confirm LSH candidates by estimated Jaccard, then union.
    for rid in ids:
        for cand in lsh.query(minhashes[rid]):
            if cand == rid:
                continue
            if minhashes[rid].jaccard(minhashes[cand]) >= threshold:
                union.union(rid, cand)

    # Order ids by first appearance for deterministic output.
    order = {rid: i for i, rid in enumerate(ids)}
    clusters = union.clusters()
    kept: list[str] = []
    dropped: list[str] = []
    rep_to_dropped: dict[str, list[str]] = {}
    for members in clusters.values():
        rep = _pick_representative(members, texts)
        kept.append(rep)
        others = sorted((m for m in members if m != rep), key=lambda m: order[m])
        if others:
            rep_to_dropped[rep] = others
            dropped.extend(others)

    result.kept = sorted(kept, key=lambda m: order[m])
    result.dropped = sorted(dropped, key=lambda m: order[m])
    result.clusters = {
        rep: rep_to_dropped[rep]
        for rep in result.kept
        if rep in rep_to_dropped
    }
    return result


# --------------- Manifest I/O (stdlib) ----------------------------


def _load_manifest_records(path: Path) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Return ``(rows, (id, text) records)`` from a JSONL manifest.

    Text is taken from an inline ``text`` field, else resolved from a
    ``text_path`` / ``path`` relative to the manifest's directory. Rows without
    resolvable text are kept in ``rows`` (so they pass through the rewrite
    untouched) but are not fed to the dedup comparison. Mirrors the
    manifest-loading shape used by homogeneity_audit / originality_audit.
    """
    rows: list[dict[str, Any]] = []
    records: list[tuple[str, str]] = []
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
        rid = str(row.get("id") or row.get("path") or row.get("text_path") or f"line{line_no}")
        rows.append({"_id": rid, "_row": row})
        if isinstance(row.get("text"), str):
            records.append((rid, row["text"]))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = base / rel
            if fp.is_file():
                records.append((rid, fp.read_text(encoding="utf-8", errors="replace")))
            else:
                sys.stderr.write(f"  manifest line {line_no}: {fp} not found; not compared\n")
    return rows, records


def dedup_manifest(
    manifest_path: Path,
    *,
    out_path: Path | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = DEFAULT_NUM_PERM,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    dry_run: bool = False,
) -> DedupResult:
    """Near-dedup a staged JSONL manifest, writing the kept rows to ``out_path``.

    Every row whose id is in ``result.dropped`` is removed; all other rows —
    including rows the loader couldn't resolve to text — pass through in their
    original order. When ``out_path`` is None the input is rewritten in place
    (unless ``dry_run``). Returns the :class:`DedupResult` for reporting.
    """
    rows, records = _load_manifest_records(manifest_path)
    result = dedup_records(
        records, threshold=threshold, num_perm=num_perm, shingle_size=shingle_size,
    )
    if dry_run:
        return result
    drop_ids = set(result.dropped)
    kept_rows = [r["_row"] for r in rows if r["_id"] not in drop_ids]
    target = out_path or manifest_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        for row in kept_rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return result


# --------------- CLI ----------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="near_dup_dedup",
        description=(
            "MinHash-LSH near-duplicate dedup for a staged acquisition "
            "manifest. Optional acquisition capability — run it before "
            "committing the final JSONL manifest to drop near-duplicate "
            "reposts/reprints the exact-hash guard misses."
        ),
    )
    p.add_argument("manifest", type=Path, help="Path to the JSONL manifest to dedup.")
    p.add_argument("--out", type=Path, default=None,
                   help="Write deduped manifest here (default: rewrite in place).")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"Jaccard near-duplicate threshold (default: {DEFAULT_THRESHOLD}).")
    p.add_argument("--num-perm", type=int, default=DEFAULT_NUM_PERM,
                   help=f"MinHash permutations (default: {DEFAULT_NUM_PERM}).")
    p.add_argument("--shingle-size", type=int, default=DEFAULT_SHINGLE_SIZE,
                   help=f"Word k-gram shingle size (default: {DEFAULT_SHINGLE_SIZE}).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report near-duplicate clusters without rewriting the manifest.")
    p.add_argument("--json", action="store_true",
                   help="Emit the dedup result as JSON on stdout.")
    return p


def run(args: argparse.Namespace) -> int:
    result = dedup_manifest(
        args.manifest,
        out_path=args.out,
        threshold=args.threshold,
        num_perm=args.num_perm,
        shingle_size=args.shingle_size,
        dry_run=args.dry_run,
    )
    if args.json:
        sys.stdout.write(json.dumps(result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(
            f"Near-dup dedup: {result.total} records → "
            f"{len(result.kept)} kept, {len(result.dropped)} dropped "
            f"(threshold={result.threshold}).\n"
        )
        for rep, others in result.clusters.items():
            sys.stdout.write(f"  keep {rep}  (drops: {', '.join(others)})\n")
        if not args.dry_run and result.dropped:
            target = args.out or args.manifest
            sys.stdout.write(f"Wrote deduped manifest to: {target}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
