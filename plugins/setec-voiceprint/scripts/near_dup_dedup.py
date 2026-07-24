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

Passage mode (`--passages`, spec 36) adds the *sub-document* lens both of those
axes are structurally blind to — a passage repeated inside or across otherwise
distinct documents. It is two-stage because no single passage-unit similarity
threshold can see both classes:

  * **Stage A — near-duplicate passage units.** Documents are split into raw
    paragraphs (never coalesced, never split), short paragraphs are grouped by
    exact normalized-token equality instead of being fed to MinHash, and LSH
    candidates are confirmed against the **true shingle sets** rather than
    MinHash's estimate. Catches reused boilerplate, edited reprints, repeated
    section templates.
  * **Stage B — exact shared-span scan.** A stdlib inverted index over word
    8-shingles finds every contiguous verbatim span repeated at >= 2 locations,
    regardless of what surrounds it. A verbatim span of L tokens produces
    L - k + 1 identical consecutive shingles at every occurrence, so the
    detection guarantee is arithmetic, not thresholded: every verbatim span with
    L >= max(k, min_span_words) is reported. This is the case Stage A provably
    cannot see (a 41-token span shared between two ~120-word passages scores
    Jaccard ~0.19 — nowhere near any usable near-dup threshold). Word-granularity
    analogue of the exact-substring dedup pass in Lee et al., *Deduplicating
    Training Data Makes Language Models Better* (arXiv:2107.06499); repeated
    spans being memorized disproportionately fast is the mechanism in Carlini et
    al. (arXiv:2202.07646).

Passage mode is report-first: Stage B never drops anything (spans are reported
for consumer-side loss masking, never excised — excision mutilates prose and
shifts the stylometric properties the corpus exists to carry), and Stage A only
drops when an operator asks for an export with `--out` + `--passage-dir`. That
export is stamped with a `passage_dedup` marker, which `pool_guard.py` uses to
refuse the duplicate-dependent set-level-diversity pools: their signal lives *in*
the retained duplicates.

Import purity: datasketch (and its numpy/scipy transitive deps) are imported
lazily inside the functions that need them, so base `import near_dup_dedup`
stays clean when the optional dep is absent. Callers that only want the shingle
helper or want to detect the missing dep get a clear RuntimeError at call time.
Stage B and the whole passage-mode report path are stdlib (claim_license and
pool_guard are stdlib too).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pool_guard  # noqa: E402
from claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"

DEFAULT_NUM_PERM = 128
DEFAULT_SHINGLE_SIZE = 5
DEFAULT_THRESHOLD = 0.8

# --- passage mode (spec 36) ---
# Paragraphs below this many word tokens never enter the LSH: they are grouped by
# EXACT normalized-token equality instead. Two reasons, both mechanical. (1) It
# catches a repeated three-word sign-off exactly, which a similarity threshold
# cannot. (2) `shingles()`'s documented sub-k fallback collapses any text shorter
# than k words to a single whole-text shingle, so two unrelated short paragraphs
# can score an estimated Jaccard of exactly 1.0 — a false-merge class that is
# closed by never exposing sub-k texts to the estimator. The library fallback is
# unchanged; document mode keeps its shipped behavior.
DEFAULT_MIN_PASSAGE_WORDS = 10
# Stage B's shingle size is 8, not the 5 document mode uses, because the span
# index is EXACT: incidental shared n-grams are pure noise, and common English
# 5-grams ("at the end of the") collide constantly where 8-grams rarely do.
DEFAULT_SPAN_SHINGLE_K = 8
# Reporting floor only — shorter repeated spans are counted, not itemized. Chosen
# under the 41-token motivating evidence to keep idiom/quotation collisions out of
# the headline list. Both Stage B knobs are uncalibrated starting points echoed
# into the report's `assumptions`; lowering --min-span-words toward k widens the
# net to >= 8-token spans.
DEFAULT_MIN_SPAN_WORDS = 20
DEFAULT_STAGES = "a,b"

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Paragraph split. Same shape as stylometry_core.paragraphs, clean-room-copied
# here on purpose: this module deliberately imports nothing from the audit stack.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")

# A document id that itself ends in the passage-id suffix would make the derived
# provenance ambiguous ("is `x#p0007` a document or a passage of `x`?").
_PASSAGE_ID_SUFFIX_RE = re.compile(r"#p\d+$")


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


# --------------- Passage mode: chunking + provenance (stdlib) -----


class PassageModeError(Exception):
    """A passage-mode refusal an operator must act on (never a silent default)."""


@dataclass(frozen=True)
class Passage:
    """One raw paragraph of one document, with slice-back provenance.

    ``char_start`` / ``char_end`` index the document text **as loaded** (post
    ``errors="replace"`` decode, no normalization), so
    ``doc_text[char_start:char_end]`` reproduces ``text`` byte-for-byte.
    """
    passage_id: str
    doc_id: str
    ordinal: int
    char_start: int
    char_end: int
    text: str


def split_passages(text: str) -> list[tuple[int, int]]:
    """Return ``(char_start, char_end)`` for each raw paragraph of ``text``.

    Blank-line split, no coalescing and no further splitting. Coalescing short
    paragraphs into their neighbours was measured to *destroy* the short-
    boilerplate signal (a sub-floor bio/disclaimer glues to a different neighbour
    in each document, driving the shared-content Jaccard toward ~0.05), and the
    paragraph boundary is the authorial unit the whole-passage class recurs in.

    Offsets are of the *stripped* paragraph, so the slice reproduces the text.
    """
    segments: list[tuple[int, int]] = []
    pos = 0
    for m in _PARAGRAPH_SPLIT_RE.finditer(text):
        segments.append((pos, m.start()))
        pos = m.end()
    segments.append((pos, len(text)))

    spans: list[tuple[int, int]] = []
    for start, end in segments:
        seg = text[start:end]
        stripped = seg.strip()
        if not stripped:
            continue
        lead = len(seg) - len(seg.lstrip())
        spans.append((start + lead, start + lead + len(stripped)))
    return spans


def _norm_tokens(text: str) -> list[str]:
    """The module's existing normalization: lowercased ``_WORD_RE`` tokens."""
    return [t.lower() for t in _WORD_RE.findall(text)]


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(doc_id: str, text: str) -> list[Passage]:
    """Split one document into :class:`Passage` records with deterministic ids.

    Raises :class:`PassageModeError` when the document id itself ends in the
    ``#p<digits>`` passage-id pattern — emitting ``x#p0003#p0001`` would make the
    provenance ambiguous, and a hygiene tool must refuse rather than guess.
    """
    if _PASSAGE_ID_SUFFIX_RE.search(doc_id):
        raise PassageModeError(
            f"document id {doc_id!r} ends in the reserved passage-id pattern "
            "'#p<digits>'; passage ids are derived as '<doc_id>#p<NNNN>' and this "
            "would nest ambiguously. Rename the row's id, or rerun from the "
            "source document manifest."
        )
    out: list[Passage] = []
    for ordinal, (start, end) in enumerate(split_passages(text)):
        out.append(Passage(
            passage_id=f"{doc_id}#p{ordinal:04d}",
            doc_id=doc_id,
            ordinal=ordinal,
            char_start=start,
            char_end=end,
            text=text[start:end],
        ))
    return out


def _passage_provenance(p: Passage, source_manifest: str) -> dict[str, Any]:
    """The provenance record spec 36 pins for every itemized passage.

    ``sha256`` is of the EXACT raw slice — no case folding, no punctuation
    folding, no NFC. Folded fingerprints over-match distinct text; a provenance
    hash must be exact (the 2026-07-11 self-exclusion-fingerprint lesson).
    """
    return {
        "passage_id": p.passage_id,
        "source_doc_id": p.doc_id,
        "source_manifest": source_manifest,
        "ordinal": p.ordinal,
        "char_start": p.char_start,
        "char_end": p.char_end,
        "n_words": len(_norm_tokens(p.text)),
        "sha256": _sha256_hex(p.text),
    }


# --------------- Passage mode: Stage A (datasketch) ---------------


def _exact_jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def stage_a_clusters(
    passages: list[Passage],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = DEFAULT_NUM_PERM,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    min_passage_words: int = DEFAULT_MIN_PASSAGE_WORDS,
) -> dict[str, Any]:
    """Cluster near-duplicate passage *units*; return kept / dropped / clusters.

    Two disjoint comparison paths, by design:

      * Sub-floor passages (``< min_passage_words`` tokens) are grouped by exact
        equality of their normalized token sequence. They never reach the LSH.
      * The rest are shingled and indexed in MinHash-LSH exactly as document mode
        does, but candidate pairs are confirmed against the **true shingle sets**
        (already in memory from signature construction), never against
        ``MinHash.jaccard()``'s estimate. At ``num_perm=128`` that estimate has
        SE ~0.04 near J=0.8 — squarely in the borderline band — and passage-scale
        comparison produces far more borderline pairs than document scale. A
        false merge here is destructive (Stage A's export drops non-representative
        members from a *training* corpus), so LSH is candidate-generation only.

    Clustering, representative choice (longest text, then lowest id), and the
    duplicate-id refusal are the shipped document-mode rules, unchanged.
    """
    MinHash, MinHashLSH = _require_datasketch()

    ids: list[str] = []
    texts: dict[str, str] = {}
    for p in passages:
        if p.passage_id in texts:
            raise ValueError(f"duplicate passage id in Stage A input: {p.passage_id!r}")
        ids.append(p.passage_id)
        texts[p.passage_id] = p.text

    union = _Union()
    for pid in ids:
        union.add(pid)

    short_keys: dict[str, list[str]] = {}
    long_ids: list[str] = []
    for p in passages:
        tokens = _norm_tokens(p.text)
        if len(tokens) < min_passage_words:
            short_keys.setdefault(" ".join(tokens), []).append(p.passage_id)
        else:
            long_ids.append(p.passage_id)

    short_exact_groups = 0
    for members in short_keys.values():
        if len(members) < 2:
            continue
        short_exact_groups += 1
        for other in members[1:]:
            union.union(members[0], other)

    if len(long_ids) > 1:
        lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        minhashes: dict[str, Any] = {}
        shingle_sets: dict[str, set[str]] = {}
        for pid in long_ids:
            sset = shingles(texts[pid], k=shingle_size)
            shingle_sets[pid] = sset
            mh = MinHash(num_perm=num_perm)
            for sh in sset:
                mh.update(sh.encode("utf-8"))
            minhashes[pid] = mh
            lsh.insert(pid, mh)
        for pid in long_ids:
            for cand in lsh.query(minhashes[pid]):
                if cand == pid:
                    continue
                if _exact_jaccard(shingle_sets[pid], shingle_sets[cand]) >= threshold:
                    union.union(pid, cand)

    order = {pid: i for i, pid in enumerate(ids)}
    kept: list[str] = []
    dropped: list[str] = []
    rep_to_dropped: dict[str, list[str]] = {}
    for members in union.clusters().values():
        rep = _pick_representative(members, texts)
        kept.append(rep)
        others = sorted((m for m in members if m != rep), key=lambda m: order[m])
        if others:
            rep_to_dropped[rep] = others
            dropped.extend(others)

    kept.sort(key=lambda m: order[m])
    dropped.sort(key=lambda m: order[m])
    return {
        "kept": kept,
        "dropped": dropped,
        "clusters": {rep: rep_to_dropped[rep] for rep in kept if rep in rep_to_dropped},
        "short_exact_groups": short_exact_groups,
    }


# --------------- Passage mode: Stage B (stdlib) -------------------


def _aligned(prev_occ: list[tuple[str, int]], next_occ: list[tuple[str, int]]) -> bool:
    """True when every occurrence of a shingle continues into the next one.

    That is the exact condition for a repeated span to extend by one token at
    *all* of its locations simultaneously — the run boundary. Keying runs on this
    (rather than on the merged marked-region) keeps two spans that merely happen
    to abut in one document from being reported as one span.
    """
    if len(prev_occ) != len(next_occ):
        return False
    return set(next_occ) == {(d, q + 1) for (d, q) in prev_occ}


def stage_b_spans(
    docs: list[tuple[str, str]],
    *,
    span_shingle_k: int = DEFAULT_SPAN_SHINGLE_K,
    min_span_words: int = DEFAULT_MIN_SPAN_WORDS,
) -> dict[str, Any]:
    """Exact inverted-index scan for contiguous verbatim spans repeated >= 2x.

    Detection guarantee (arithmetic, not thresholded): a verbatim repeated span of
    ``L`` tokens produces ``L - k + 1`` identical ``k``-shingles at consecutive
    positions in *every* occurrence, so every verbatim repeated span with
    ``L >= max(k, min_span_words)`` is reported, exactly and deterministically,
    with no dependence on what surrounds it.

    Named limits: the scan is verbatim-exact, so an edit inside a repeated span
    splits it into verbatim sub-spans (each reported only if it still clears the
    floor) — lightly *edited* whole-passage reuse is Stage A's class, and edited
    sub-passage reuse below the floor is detected by neither stage. Memory is
    O(corpus tokens) for the index: fine at staged-personal-manifest scale, but a
    large-corpus run should shard via the repo's `shard_runner` conventions first.

    Nothing is ever dropped or excised — the output is an inventory for
    consumer-side action (loss masking / chunk-stream filtering at training time).
    """
    tokens: dict[str, list[tuple[str, int, int]]] = {}
    doc_order: dict[str, int] = {}
    for i, (doc_id, text) in enumerate(docs):
        if doc_id in tokens:
            # The id is the provenance join key; a collision would silently make
            # one document's span offsets point into another's text.
            raise ValueError(f"duplicate document id in span-scan input: {doc_id!r}")
        doc_order[doc_id] = i
        tokens[doc_id] = [
            (m.group(0).lower(), m.start(), m.end()) for m in _WORD_RE.finditer(text)
        ]

    # Inverted index: shingle -> occurrences, in (document order, position) order
    # because the documents are walked in input order. `keys[doc][p]` is the
    # shingle starting at token position p, so a run walk needs no re-hashing.
    index: dict[str, list[tuple[str, int]]] = {}
    keys: dict[str, list[str]] = {}
    for doc_id, _ in docs:
        tk = tokens[doc_id]
        klist: list[str] = []
        for p in range(len(tk) - span_shingle_k + 1):
            key = " ".join(t[0] for t in tk[p:p + span_shingle_k])
            klist.append(key)
            index.setdefault(key, []).append((doc_id, p))
        keys[doc_id] = klist

    # Maximal duplicated regions: mark every token position covered by a shingle
    # that occurs at >= 2 distinct locations, then merge consecutive marks.
    regions: list[dict[str, Any]] = []
    regions_below_floor = 0
    for doc_id, _ in docs:
        tk = tokens[doc_id]
        marked = [False] * len(tk)
        for p, key in enumerate(keys[doc_id]):
            if len(index[key]) >= 2:
                for q in range(p, p + span_shingle_k):
                    marked[q] = True
        run_start: int | None = None
        for q in range(len(tk) + 1):
            if q < len(tk) and marked[q]:
                if run_start is None:
                    run_start = q
                continue
            if run_start is None:
                continue
            n_words = q - run_start
            if n_words >= min_span_words:
                regions.append({
                    "source_doc_id": doc_id,
                    "token_start": run_start,
                    "token_end": q - 1,
                    "char_start": tk[run_start][1],
                    "char_end": tk[q - 1][2],
                    "n_words": n_words,
                })
            else:
                regions_below_floor += 1
            run_start = None

    # Repeated-span clusters: maximal runs of consecutive shingle positions whose
    # occurrence set advances in lockstep at every location.
    clusters: dict[tuple[Any, ...], dict[str, Any]] = {}
    spans_below_floor = 0
    for doc_id, _ in docs:
        klist = keys[doc_id]
        p = 0
        while p < len(klist):
            occ = index[klist[p]]
            if len(occ) < 2:
                p += 1
                continue
            if p > 0 and _aligned(index[klist[p - 1]], occ):
                p += 1  # not a run start; the run that covers p began earlier
                continue
            end = p
            while end + 1 < len(klist) and _aligned(index[klist[end]], index[klist[end + 1]]):
                end += 1
            n_words = (end - p) + span_shingle_k
            ckey = (tuple(occ), n_words)
            if ckey not in clusters:
                if n_words >= min_span_words:
                    clusters[ckey] = {"occ": occ, "n_words": n_words}
                else:
                    spans_below_floor += 1
                    clusters[ckey] = {}
            p = end + 1

    repeated_spans: list[dict[str, Any]] = []
    for entry in clusters.values():
        if not entry:
            continue
        occ = entry["occ"]
        n_words = entry["n_words"]
        first_doc, first_pos = occ[0]
        norm_seq = " ".join(
            t[0] for t in tokens[first_doc][first_pos:first_pos + n_words]
        )
        repeated_spans.append({
            "span_sha256": _sha256_hex(norm_seq),
            "n_words": n_words,
            "n_occurrences": len(occ),
            "occurrences": [
                {
                    "source_doc_id": d,
                    "token_start": q,
                    "token_end": q + n_words - 1,
                    "char_start": tokens[d][q][1],
                    "char_end": tokens[d][q + n_words - 1][2],
                }
                for (d, q) in occ
            ],
        })
    repeated_spans.sort(key=lambda s: (
        doc_order[s["occurrences"][0]["source_doc_id"]],
        s["occurrences"][0]["token_start"],
        s["n_words"],
    ))
    regions.sort(key=lambda r: (doc_order[r["source_doc_id"]], r["token_start"]))
    return {
        "repeated_spans": repeated_spans,
        "duplicated_regions": regions,
        "spans_below_floor": spans_below_floor,
        "regions_below_floor": regions_below_floor,
    }


# --------------- Passage mode: report -----------------------------


_PASSAGE_CLAIM_LICENSE = {
    "licenses": (
        "The inventory of near-duplicate passage clusters and verbatim repeated spans in the "
        "supplied manifest, with per-occurrence provenance, under the echoed parameters."
    ),
    "does_not_license": (
        "Any 'memorization-safe' or 'clean corpus' determination — an empty report at these "
        "floors is NOT absence of repetition below them, and edited sub-passage reuse below the "
        "floor is detected by neither stage. Any AI/human or provenance verdict — this is a "
        "repetition inventory, not an authorship call. Any claim that a reported repetition is "
        "ILLEGITIMATE: an epigraph, a refrain, a recurring quotation are authorial choices, and "
        "which repetitions matter is an editorial judgment the operator makes, never the tool. "
        "Any absolute memorization rate from the motivating calibration run: its aggregate "
        "movement is statistically unresolved (exact McNemar p = 0.453) and its base arm "
        "reproduced spans before any adaptation, so span reproduction there does NOT show that "
        "memorization worsened."
    ),
}

_PASSAGE_LIMITS = [
    "Stage B is VERBATIM-exact: an edit inside a repeated span splits it into verbatim sub-spans, "
    "each reported only if it still clears --min-span-words.",
    "Repetition below the reporting floors is counted, never itemized; edited sub-passage reuse "
    "below the floor is detected by neither stage (the honest residual).",
    "Stage A's residual false positive is LEGITIMATE authorial repetition (epigraph, refrain, "
    "recurring quotation) — an editorial judgment, not a similarity error.",
    "The pool_guard marker scan is a MANIFEST-PATH check: a directory input (--dir / --corpus-dir "
    "/ --reference-dir) carries no row metadata and cannot be checked, and an operator who "
    "hand-strips the marker has asserted responsibility.",
    "Passage-level dedup is corpus PREPROCESSING: if an export later serves as either side of a "
    "target-vs-baseline comparison, the identical pass must be applied to the other side or the "
    "comparison is asymmetric. The marker's params echo makes that auditable; enforcement is "
    "operator/consumer responsibility.",
    "Ships heuristic / uncalibrated. The thresholds and floors below are documented starting "
    "points, not calibrated cuts, and no band is emitted.",
]


def _load_documents(path: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """``(doc_id, text, row)`` for every manifest row with resolvable text.

    Mirrors :func:`_load_manifest_records`'s parsing but keeps the row dict —
    passage mode needs the source row for the export's provenance inheritance,
    which the ``(id, text)`` record shape discards. Rows without resolvable text
    are reported on stderr and skipped (passage mode writes nothing back to the
    input manifest, so there is no pass-through to preserve).
    """
    out: list[tuple[str, str, dict[str, Any]]] = []
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
        if isinstance(row.get("text"), str):
            out.append((rid, row["text"], row))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = base / rel
            if fp.is_file():
                out.append((rid, fp.read_text(encoding="utf-8", errors="replace"), row))
            else:
                sys.stderr.write(f"  manifest line {line_no}: {fp} not found; not compared\n")
    return out


def parse_stages(raw: str) -> list[str]:
    """``"a,b"`` -> ``["a", "b"]``. Unknown or empty selections refuse."""
    stages = [s.strip().lower() for s in raw.split(",") if s.strip()]
    unknown = [s for s in stages if s not in ("a", "b")]
    if unknown or not stages:
        raise PassageModeError(
            f"--stages {raw!r} is not a selection from 'a' (near-duplicate passage units) "
            "and 'b' (exact shared-span scan); e.g. --stages a,b or --stages b"
        )
    # Deduplicate while preserving the canonical a-then-b report order.
    return [s for s in ("a", "b") if s in stages]


def analyze_passages(
    manifest_path: Path,
    *,
    stages: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    num_perm: int = DEFAULT_NUM_PERM,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    min_passage_words: int = DEFAULT_MIN_PASSAGE_WORDS,
    span_shingle_k: int = DEFAULT_SPAN_SHINGLE_K,
    min_span_words: int = DEFAULT_MIN_SPAN_WORDS,
) -> tuple[dict[str, Any], list[Passage], dict[str, dict[str, Any]]]:
    """Run passage mode over a staged manifest.

    Returns ``(report, passages, source_rows_by_doc_id)``. The extra returns are
    what :func:`export_passages` needs; the report is the primary artifact and is
    complete on its own.

    Self-guard: an input manifest whose rows already carry the ``passage_dedup``
    marker is refused. Re-chunking an export nests provenance and silently
    re-dedups; a rerun starts from the source document manifest.
    """
    stages = list(stages) if stages else ["a", "b"]
    marked = pool_guard.scan_manifest_for_passage_dedup(manifest_path)
    if marked:
        raise PassageModeError(
            f"{manifest_path} is already a passage-deduped export "
            f"({len(marked)} row(s) carry `{pool_guard.PASSAGE_DEDUP_MARKER}`, e.g. "
            f"{marked[0]}). Re-chunking nests provenance and silently re-dedups — "
            "rerun passage mode from the SOURCE document manifest."
        )

    documents = _load_documents(manifest_path)
    source_manifest = manifest_path.name
    passages: list[Passage] = []
    seen_doc_ids: set[str] = set()
    for doc_id, text, _row in documents:
        if doc_id in seen_doc_ids:
            raise PassageModeError(
                f"duplicate document id {doc_id!r} in {manifest_path}; the id is the "
                "provenance join key, so passage ids derived from it would collide"
            )
        seen_doc_ids.add(doc_id)
        passages.extend(chunk_document(doc_id, text))

    rows_by_doc: dict[str, dict[str, Any]] = {d: r for d, _t, r in documents}
    text_by_doc: dict[str, str] = {d: t for d, t, _r in documents}

    stage_a: dict[str, Any] = {
        "run": False, "clusters": None, "kept": None, "dropped": None,
        "short_exact_groups": None,
    }
    stage_a_detail: dict[str, Any] | None = None
    if "a" in stages:
        stage_a_detail = stage_a_clusters(
            passages, threshold=threshold, num_perm=num_perm,
            shingle_size=shingle_size, min_passage_words=min_passage_words,
        )
        stage_a = {
            "run": True,
            "clusters": len(stage_a_detail["clusters"]),
            "kept": len(stage_a_detail["kept"]),
            "dropped": len(stage_a_detail["dropped"]),
            "short_exact_groups": stage_a_detail["short_exact_groups"],
        }

    stage_b: dict[str, Any] = {
        "run": False, "repeated_spans": None, "duplicated_regions": None,
        "n_below_floor": None,
    }
    stage_b_detail: dict[str, Any] | None = None
    if "b" in stages:
        stage_b_detail = stage_b_spans(
            [(d, t) for d, t, _r in documents],
            span_shingle_k=span_shingle_k, min_span_words=min_span_words,
        )
        stage_b = {
            "run": True,
            "repeated_spans": len(stage_b_detail["repeated_spans"]),
            "duplicated_regions": len(stage_b_detail["duplicated_regions"]),
            "n_below_floor": {
                "repeated_spans": stage_b_detail["spans_below_floor"],
                "duplicated_regions": stage_b_detail["regions_below_floor"],
            },
        }

    by_id = {p.passage_id: p for p in passages}
    provenance: dict[str, Any] = {
        "passage_clusters": [], "repeated_spans": [], "duplicated_regions": [],
    }
    # Per-document rollup as a LIST of records keyed by a `source_doc_id` FIELD,
    # matching corpus_novelty_audit / skeleton_overlap_audit's `per_document`
    # shape. Deliberately not a doc-id-keyed mapping: manifest ids are operator
    # data, and promoting them to JSON keys would put arbitrary strings into the
    # recursive no-verdict key walk every surface here is held to.
    affected: dict[str, dict[str, Any]] = {}

    def _affected(doc_id: str) -> dict[str, Any]:
        return affected.setdefault(
            doc_id,
            {"source_doc_id": doc_id, "passages_dropped": [], "spans_present": 0},
        )

    if stage_a_detail is not None:
        for rep, others in stage_a_detail["clusters"].items():
            members = [rep, *others]
            provenance["passage_clusters"].append({
                "representative": rep,
                "dropped": list(others),
                "passages": [_passage_provenance(by_id[m], source_manifest) for m in members],
            })
            for pid in others:
                _affected(by_id[pid].doc_id)["passages_dropped"].append(pid)

    if stage_b_detail is not None:
        for span in stage_b_detail["repeated_spans"]:
            occurrences = []
            for occ in span["occurrences"]:
                doc_id = occ["source_doc_id"]
                raw_slice = text_by_doc[doc_id][occ["char_start"]:occ["char_end"]]
                occurrences.append({
                    "span_id": f"{doc_id}#t{occ['token_start']:06d}",
                    "source_doc_id": doc_id,
                    "source_manifest": source_manifest,
                    "token_start": occ["token_start"],
                    "token_end": occ["token_end"],
                    "char_start": occ["char_start"],
                    "char_end": occ["char_end"],
                    "n_words": span["n_words"],
                    "sha256": _sha256_hex(raw_slice),
                })
                _affected(doc_id)["spans_present"] += 1
            provenance["repeated_spans"].append({
                "span_sha256": span["span_sha256"],
                "n_words": span["n_words"],
                "n_occurrences": span["n_occurrences"],
                "occurrences": occurrences,
            })
        for region in stage_b_detail["duplicated_regions"]:
            doc_id = region["source_doc_id"]
            provenance["duplicated_regions"].append({
                "source_doc_id": doc_id,
                "source_manifest": source_manifest,
                "token_start": region["token_start"],
                "token_end": region["token_end"],
                "char_start": region["char_start"],
                "char_end": region["char_end"],
                "n_words": region["n_words"],
            })

    assumptions = {
        "stages": stages,
        "stage_a": {
            "run": "a" in stages,
            "shingle_size": shingle_size,
            "threshold": threshold,
            "num_perm": num_perm,
            "min_passage_words": min_passage_words,
            "chunking": "raw paragraphs, never coalesced and never split",
            "confirmation": (
                "exact Jaccard over the true shingle sets; MinHash-LSH is candidate "
                "generation only, so no estimate participates in any accept/reject decision"
            ),
            "not_run_note": None if "a" in stages else (
                "Stage A was NOT run (not selected via --stages, or datasketch is absent). "
                "Its counts are null, NOT zero — this report says nothing about "
                "near-duplicate passage units."
            ),
        },
        "stage_b": {
            "run": "b" in stages,
            "span_shingle_k": span_shingle_k,
            "min_span_words": min_span_words,
            "detection_guarantee": (
                "every verbatim repeated span with L >= max(span_shingle_k, "
                "min_span_words) tokens is reported, exactly and deterministically"
            ),
            "n_below_floor_note": (
                "counts of repeated-span clusters / duplicated regions found but shorter "
                "than min_span_words, so counted and not itemized"
            ),
            "not_run_note": None if "b" in stages else (
                "Stage B was NOT run (not selected via --stages). Its counts are null, NOT "
                "zero — this report says nothing about verbatim repeated spans."
            ),
        },
        "calibration_status": "heuristic / uncalibrated — no bands, no thresholds promoted",
        "limits": list(_PASSAGE_LIMITS),
        "references": [
            "arXiv:2107.06499 — Lee et al., Deduplicating Training Data Makes Language "
            "Models Better (the exact-substring pass Stage B is the word-granularity "
            "analogue of)",
            "arXiv:2202.07646 — Carlini et al., Quantifying Memorization Across Language "
            "Models (repeated sequences are memorized disproportionately fast)",
        ],
    }

    report = {
        "mode": "passages",
        "stages": stages,
        "source_manifest": source_manifest,
        "n_documents": len(documents),
        "n_passages": len(passages),
        "stage_a": stage_a,
        "stage_b": stage_b,
        "documents_affected": [affected[d] for d in sorted(affected)],
        "provenance": provenance,
        "assumptions": assumptions,
        "claim_license": from_legacy(
            _PASSAGE_CLAIM_LICENSE, task_surface=TASK_SURFACE,
        ).to_dict(),
    }
    return report, passages, rows_by_doc


# --------------- Passage mode: export -----------------------------


# Recomputed per passage rather than inherited: the source values describe the
# whole document. `text` / `text_path` are excluded for the same reason and are
# load-bearing — inheriting them would emit a passage row whose `path` points at
# the passage file while its inline text is the ENTIRE source document.
_EXPORT_RECOMPUTED_FIELDS = frozenset({"id", "path", "word_count", "content_hash"})
_EXPORT_SOURCE_TEXT_FIELDS = frozenset({"text", "text_path"})

# Filenames are derived from passage ids, so a document id carrying a path
# separator would write outside --passage-dir (or collide). Refuse, don't sanitize:
# sanitizing two distinct ids to one filename silently loses a passage.
_UNSAFE_ID_CHARS = ("/", "\\", "\x00")


def _export_required_source_fields() -> tuple[str, ...]:
    """The REQUIRED output-row fields a source row must actually supply.

    Derived from ``manifest_validator.REQUIRED_FIELDS`` (imported lazily so base
    ``import near_dup_dedup`` stays light) minus the two the export produces
    itself, so this cannot drift from the validator it must satisfy.
    """
    import manifest_validator  # noqa: PLC0415  (lazy on purpose)
    return tuple(f for f in manifest_validator.REQUIRED_FIELDS if f not in ("id", "path"))


def _has_value(row: dict[str, Any], field_name: str) -> bool:
    """``manifest_validator._has``, plus empty containers.

    Deliberately one notch stricter than the validator: `_has` accepts
    ``use: []`` as "present", but an empty `use` list carries no provenance, and
    inheriting it onto a training-corpus row would satisfy the schema while
    saying nothing. The export fails closed on it rather than propagating an
    empty claim.
    """
    value = row.get(field_name)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict, tuple)) and not value:
        return False
    return True


def export_passages(
    passages: list[Passage],
    report: dict[str, Any],
    rows_by_doc: dict[str, dict[str, Any]],
    *,
    out_path: Path,
    passage_dir: Path,
    manifest_path: Path,
) -> int:
    """Write the kept Stage-A passages as a validator-clean passage manifest.

    Each kept passage becomes a text file ``<passage_dir>/<passage_id>.txt`` plus
    one manifest row carrying ``id`` (the passage id), ``path`` (relative to the
    manifest, so the validator's path check resolves), every inheritable field of
    its source row copied verbatim, and the ``passage_dedup`` marker.

    **Refusal, not fabrication.** If any source row feeding the export lacks a
    field REQUIRED on the output row, the export refuses entirely: no partial
    write, no invented provenance, no bypass flag. A hygiene tool must not stamp
    an ``ai_status`` or a ``use`` onto a training artifact.

    Returns the number of rows written.
    """
    stage_a = report.get("stage_a") or {}
    if not stage_a.get("run"):
        raise PassageModeError(
            "--out needs Stage A: the export is the Stage-A-deduplicated passage corpus, "
            "and Stage B contributes nothing to it (spans are reported, never excised). "
            "Rerun with --stages a or --stages a,b."
        )

    kept_ids = {p.passage_id for p in passages}
    for cluster in report["provenance"]["passage_clusters"]:
        kept_ids.difference_update(cluster["dropped"])
    kept = [p for p in passages if p.passage_id in kept_ids]

    required = _export_required_source_fields()
    missing: list[str] = []
    unsafe: list[str] = []
    for p in kept:
        row = rows_by_doc.get(p.doc_id, {})
        gaps = [f for f in required if not _has_value(row, f)]
        if gaps:
            missing.append(f"{p.doc_id} (missing: {', '.join(gaps)})")
        if any(c in p.doc_id for c in _UNSAFE_ID_CHARS) or p.doc_id in (".", ".."):
            unsafe.append(p.doc_id)
    if missing:
        # Dedupe by source doc: one line per offending row, not one per passage.
        named = sorted(set(missing))
        raise PassageModeError(
            "export refused — source row(s) lack fields REQUIRED on every exported "
            f"passage row ({', '.join(required)}). A hygiene tool must not invent "
            "provenance for a training corpus, and there is no bypass flag. "
            f"Offending source row(s): {'; '.join(named)}. "
            "Nothing was written; the report above is still valid."
        )
    if unsafe:
        raise PassageModeError(
            "export refused — document id(s) contain a path separator, so the derived "
            f"passage filename would escape --passage-dir: {', '.join(sorted(set(unsafe)))}. "
            "Rename the row ids; the export will not sanitize them (two distinct ids "
            "sanitized to one filename would silently lose a passage). Nothing was written."
        )

    passage_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    params = report["assumptions"]
    rows: list[dict[str, Any]] = []
    for p in kept:
        source_row = rows_by_doc.get(p.doc_id, {})
        text_file = passage_dir / f"{p.passage_id}.txt"
        text_file.write_text(p.text, encoding="utf-8")
        try:
            rel = text_file.resolve().relative_to(out_path.resolve().parent)
            rel_str = rel.as_posix()
        except ValueError:
            rel_str = text_file.resolve().as_posix()
        row: dict[str, Any] = {
            k: v for k, v in source_row.items()
            if k not in _EXPORT_RECOMPUTED_FIELDS and k not in _EXPORT_SOURCE_TEXT_FIELDS
        }
        row["id"] = p.passage_id
        row["path"] = rel_str
        row["word_count"] = len(_norm_tokens(p.text))
        row["content_hash"] = f"sha256:{_sha256_hex(p.text)}"
        row[pool_guard.PASSAGE_DEDUP_MARKER] = {
            "source_doc_id": p.doc_id,
            "source_manifest": manifest_path.name,
            "ordinal": p.ordinal,
            "char_start": p.char_start,
            "char_end": p.char_end,
            "stages": list(report["stages"]),
            "params": {
                "shingle_size": params["stage_a"]["shingle_size"],
                "threshold": params["stage_a"]["threshold"],
                "num_perm": params["stage_a"]["num_perm"],
                "min_passage_words": params["stage_a"]["min_passage_words"],
                "span_shingle_k": params["stage_b"]["span_shingle_k"],
                "min_span_words": params["stage_b"]["min_span_words"],
            },
        }
        rows.append(row)

    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    return len(rows)


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

    g = p.add_argument_group(
        "passage/span mode (spec 36)",
        "Sub-document repetition hygiene. Report-first: nothing is dropped unless "
        "--out is passed, and repeated spans are never excised.",
    )
    g.add_argument("--passages", action="store_true",
                   help="Run passage/span mode instead of document-level dedup. "
                        "Never rewrites the input manifest.")
    g.add_argument("--stages", default=DEFAULT_STAGES,
                   help="Comma-separated stage selection: 'a' (near-duplicate passage "
                        f"units, needs datasketch), 'b' (exact shared-span scan, stdlib). "
                        f"Default: {DEFAULT_STAGES}.")
    g.add_argument("--min-passage-words", type=int, default=DEFAULT_MIN_PASSAGE_WORDS,
                   help="Paragraphs below this many word tokens are grouped by exact "
                        "normalized-token equality instead of entering the LSH "
                        f"(default: {DEFAULT_MIN_PASSAGE_WORDS}).")
    g.add_argument("--span-shingle-k", type=int, default=DEFAULT_SPAN_SHINGLE_K,
                   help=f"Stage B shingle size (default: {DEFAULT_SPAN_SHINGLE_K}).")
    g.add_argument("--min-span-words", type=int, default=DEFAULT_MIN_SPAN_WORDS,
                   help="Stage B reporting floor in tokens; shorter repeated spans are "
                        f"counted, not itemized (default: {DEFAULT_MIN_SPAN_WORDS}).")
    g.add_argument("--report-out", type=Path, default=None,
                   help="Write the passage-mode JSON report here.")
    g.add_argument("--passage-dir", type=Path, default=None,
                   help="Directory for the exported passage text files. Required with "
                        "--out in passage mode.")
    return p


def run(args: argparse.Namespace) -> int:
    if getattr(args, "passages", False):
        return _run_passages(args)
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


def _run_passages(args: argparse.Namespace) -> int:
    """Passage-mode driver. Report first, export second, refusals loud.

    Ordering is load-bearing: the report is emitted BEFORE the export is
    attempted, so an export refusal (a source row missing `ai_status` / `use`)
    still leaves the operator the inventory they ran the pass for.
    """
    if args.dry_run:
        sys.stderr.write(
            "[near_dup_dedup] --dry-run is document-mode only; passage mode never "
            "rewrites the input manifest, so a report-only run is just --passages "
            "without --out.\n"
        )
        return 2
    if args.out and not args.passage_dir:
        sys.stderr.write(
            "[near_dup_dedup] --out requires --passage-dir in passage mode: each kept "
            "passage is written as a text file the exported row's `path` resolves to.\n"
        )
        return 2
    if args.passage_dir and not args.out:
        sys.stderr.write(
            "[near_dup_dedup] --passage-dir requires --out in passage mode (the passage "
            "files and the manifest that indexes them ship together).\n"
        )
        return 2

    try:
        stages = parse_stages(args.stages)
        report, passages, rows_by_doc = analyze_passages(
            args.manifest,
            stages=stages,
            threshold=args.threshold,
            num_perm=args.num_perm,
            shingle_size=args.shingle_size,
            min_passage_words=args.min_passage_words,
            span_shingle_k=args.span_shingle_k,
            min_span_words=args.min_span_words,
        )
    except PassageModeError as e:
        sys.stderr.write(f"[near_dup_dedup] {e}\n")
        return 2

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(text + "\n", encoding="utf-8")
    if args.json:
        sys.stdout.write(text + "\n")
    else:
        sys.stdout.write(_passage_summary(report))

    if not args.out:
        return 0
    try:
        n = export_passages(
            passages, report, rows_by_doc,
            out_path=args.out, passage_dir=args.passage_dir,
            manifest_path=args.manifest,
        )
    except PassageModeError as e:
        sys.stderr.write(f"[near_dup_dedup] {e}\n")
        return 2
    if not args.json:
        sys.stdout.write(
            f"Wrote {n} passage row(s) to {args.out} "
            f"(passage files under {args.passage_dir}).\n"
        )
    return 0


def _passage_summary(report: dict[str, Any]) -> str:
    """Human-readable stdout for a passage-mode run without --json."""
    a, b = report["stage_a"], report["stage_b"]
    lines = [
        f"Passage hygiene: {report['n_documents']} document(s) → "
        f"{report['n_passages']} passage(s); stages={','.join(report['stages'])}.",
    ]
    if a["run"]:
        lines.append(
            f"  Stage A: {a['clusters']} near-duplicate cluster(s), {a['kept']} kept, "
            f"{a['dropped']} dropped, {a['short_exact_groups']} short-exact group(s)."
        )
    else:
        lines.append("  Stage A: NOT RUN (counts are null, not zero).")
    if b["run"]:
        below = b["n_below_floor"]
        lines.append(
            f"  Stage B: {b['repeated_spans']} repeated span(s), "
            f"{b['duplicated_regions']} duplicated region(s); below floor: "
            f"{below['repeated_spans']} span(s) / {below['duplicated_regions']} region(s)."
        )
    else:
        lines.append("  Stage B: NOT RUN (counts are null, not zero).")
    lines.append(
        "  Report-first: no verdict, no 'memorization-safe' claim — see claim_license."
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
