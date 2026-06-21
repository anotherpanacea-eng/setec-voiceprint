#!/usr/bin/env python3
"""homogeneity_audit.py — pool-level set homogeneity ("AI hivemind"), M1 stdlib lens (spec 30).

Every per-document SETEC surface scores ONE text against a baseline. This scores a **pool of N
responses** to the same prompt: the *distribution of pairwise cosine similarities* across the pool
plus an *effective number of modes* (participation ratio over the Gram eigenvalues). The headline
scalar is `mean_pairwise_cosine` (= the paper's "average pairwise cosine"), oriented
`gt = more homogeneous`. A single-doc **hivemind-proximity** mode (`--target T` + an operator-supplied
`--centroid`/`--centroid-dir`) reports the cosine of one target to that centroid.

Set-level axis (`set_level_diversity`, the surface `originality_audit` shares): the signal lives
*between* the texts, not inside any one — no per-doc surface can see it.

M1 lens = **local-stylometric, model-free, stdlib**. Per-text vectors reuse
`stylometry_core.function_word_features` + `char_ngram_features` (regex/Counter, spaCy-free) over a
pool-fixed shared vocabulary. Clean-room reimplementation of the average-pairwise-cosine + mode-collapse
metric from **Artificial Hivemind** (Jiang, Choi, Sap et al., NeurIPS 2025 D&B,
arXiv:2510.22954). The M2 LUAR / text-embedding-3-small semantic lenses are a POC-gated `--lens` seam
(not in this build).

Posture (no verdict): reports a distribution + `effective_modes` — NOT an AI/human call. High
homogeneity is NOT "AI": a tight topical prompt, a shared genre, or a single source forces it with no AI
involvement. **No absolute band** (like `originality_audit`); thresholding is operator-side. The paper's
~0.8 AI-regime line is the *upstream semantic-lens* figure, NOT calibrated for this stylometric lens —
recorded in `assumptions`, never a code cut. The claim license refuses any verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from stylometry_core import (  # noqa: E402
    char_ngram_features,
    function_word_features,
    word_tokens,
)

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "homogeneity_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_MIN_SET = 10
# Per-text length floor (words). Below this a text has too few function-word / char-n-gram
# observations for a stable stylometric vector; such texts are dropped from the pool before the
# set-floor (min_set) check. 15 admits normal sentence-length responses while excluding stubs.
LENGTH_FLOOR_WORDS = 15
# Top char-n-grams per family kept in the pool-fixed vocabulary. Bounds the vector width;
# the busiest n-grams carry the stylometric signal and keep the cosine well-conditioned.
_TOP_NGRAMS_PER_FAMILY = 200

# The paper's AI-regime figure is on the SEMANTIC lens; M1 does NOT transfer it (no band).
_REFERENCE_THRESHOLD_SOURCE = (
    "arXiv:2510.22954, semantic lens (text-embedding-3-small) — NOT calibrated for this "
    "stylometric lens; emitted for reference only, never as a band/cut on the local-stylometric number"
)


# ---- pool loading (mirrors originality_audit / voice_fingerprint conventions) ----

def _load_manifest(path: Path) -> list[tuple[str, str]]:
    """(id, text) from a JSONL manifest (inline `text` or a `text_path`/`path` resolved relative to
    the manifest's dir). Mirrors originality_audit._load_reference_manifest's shape."""
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


# ---- M1 local-stylometric lens (stdlib, model-free) --------------------------

def _word_count(text: str) -> int:
    return len(word_tokens(text))


def build_vocabulary(texts: list[str]) -> dict[str, Any]:
    """Pool-fixed feature vocabulary, shared by every vector so coordinates align.

    function_word_features already returns a fixed, sorted key set (every FUNCTION_WORDS member), so
    those keys are constant across texts. For char n-grams we take the union of n-grams appearing in
    the pool, keep the most frequent _TOP_NGRAMS_PER_FAMILY per family (deterministic tie-break on the
    gram string), and freeze a sorted coordinate order. Returns the ordered (family, key) coordinate
    list so embed_text produces aligned vectors.
    """
    # Function-word coordinates: stable, text-independent (sorted FUNCTION_WORDS).
    fw_keys = sorted(function_word_features([]).keys())

    # Char-n-gram coordinates: pool-derived, frequency-pruned, then sorted for a stable order.
    family_totals: dict[str, dict[str, float]] = {}
    for t in texts:
        for fam, feats in char_ngram_features(t).items():
            acc = family_totals.setdefault(fam, {})
            for k, v in feats.items():
                acc[k] = acc.get(k, 0.0) + v
    ngram_keys: list[tuple[str, str]] = []
    for fam in sorted(family_totals):
        ranked = sorted(family_totals[fam].items(), key=lambda kv: (-kv[1], kv[0]))
        for k, _ in ranked[:_TOP_NGRAMS_PER_FAMILY]:
            ngram_keys.append((fam, k))
    ngram_keys.sort()

    coords: list[tuple[str, str]] = [("function_words", k) for k in fw_keys] + ngram_keys
    return {"coords": coords}


def embed_text(text: str, vocab: dict[str, Any]) -> list[float]:
    """Project one text onto the pool-fixed coordinate list → a plain list of floats (no numpy)."""
    fw = function_word_features(word_tokens(text))
    ng = char_ngram_features(text)
    vec: list[float] = []
    for fam, key in vocab["coords"]:
        if fam == "function_words":
            vec.append(float(fw.get(key, 0.0)))
        else:
            vec.append(float(ng.get(fam, {}).get(key, 0.0)))
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine of two equal-length stdlib vectors, clamped to [-1, 1]. 0.0 if either is the zero
    vector (a degenerate but not-NaN reading — the R4 bounds gate would reject a NaN)."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot / (na * nb)))


def _quantile(ordered: list[float], q: float) -> float:
    """Linear-interpolation quantile on an already-sorted list (mirrors voice_fingerprint._quantile
    / numpy's default 'linear' method, so the surface needs no numpy to summarize a float list)."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def cosine_distribution(values: list[float]) -> dict[str, Any]:
    """7-key distribution summary (clean-room of voice_fingerprint.cosine_distribution): `n`, `mean`,
    `sd`, `min`, `p10`, `p50`, `p90`. Pure stdlib. Empty input → all-None (well-formed empty block)."""
    if not values:
        return {"n": 0, "mean": None, "sd": None, "min": None,
                "p10": None, "p50": None, "p90": None}
    import statistics
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "p10": _quantile(ordered, 0.10),
        "p50": _quantile(ordered, 0.50),
        "p90": _quantile(ordered, 0.90),
    }


def pairwise_cosines(vectors: list[list[float]]) -> list[float]:
    """All unique i<j cosines across the pool vectors (the all-pairs within-pool distribution)."""
    n = len(vectors)
    out: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            out.append(_cosine(vectors[i], vectors[j]))
    return out


def effective_modes(vectors: list[list[float]]) -> float | None:
    """Participation ratio over the Gram eigenvalues of the mean-centered, unit-normed pool vectors:
    `(Σλ)² / Σ(λ²)`. ≈1 when the pool collapses to one direction, ≈N when maximally spread.

    Needs numpy for the N×N eigenvalues — the ONE non-stdlib piece of M1. The import is GUARDED:
    returns None (caller emits `effective_modes: null` + a warning, distribution still ships) if numpy
    is absent, never crashes. Bounded to [1, n]; eigenvalues are clamped >= 0 (a symmetric Gram matrix
    is PSD; tiny negative round-off is floored)."""
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    n = len(vectors)
    if n < 2:
        return float(n)  # a single (or empty) row trivially has one mode
    arr = np.asarray(vectors, dtype="float64")
    # Unit-normalize rows (zero rows stay zero), then mean-center.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    arr = arr / norms
    arr = arr - arr.mean(axis=0, keepdims=True)
    gram = arr @ arr.T  # N×N, symmetric PSD
    eig = np.linalg.eigvalsh(gram)
    eig = np.clip(eig, 0.0, None)  # floor round-off negatives
    s1 = float(eig.sum())
    s2 = float((eig * eig).sum())
    if s2 <= 0.0:
        # all-zero spread (identical centered vectors) → one effective mode
        return 1.0
    pr = (s1 * s1) / s2
    # Bound to [1, n] (participation ratio is mathematically in this range; clamp float drift).
    return float(max(1.0, min(float(n), pr)))


def _assumptions(*, mode: str) -> dict[str, str]:
    return {
        "method": "average pairwise cosine + participation-ratio effective modes "
                  "(clean-room, arXiv:2510.22954, Artificial Hivemind)",
        "lens": "local-stylometric (stdlib function-word + char-3/4/5-gram frequency vectors over a "
                "pool-fixed shared vocabulary); model-free, NOT the paper's semantic lens",
        "orientation": "mean_pairwise_cosine gt = MORE homogeneous (the pool clusters tighter)"
                       if mode == "pool" else
                       "hivemind_proximity gt = CLOSER to the operator-supplied centroid",
        "confounds": "high homogeneity is NOT 'AI' — a tight topical prompt, a shared genre/register, "
                     "or a single source forces a tight pool with no AI involvement; mixing prompts in "
                     "the pool inflates apparent diversity. The operator must supply a prompt-matched "
                     "pool (no implied default pool).",
        "no_band": "no absolute band is emitted (like originality_audit); thresholding is operator-side",
        "reference_threshold_source": _REFERENCE_THRESHOLD_SOURCE,
    }


def audit_pool(pool: list[tuple[str, str]], *, min_set: int = DEFAULT_MIN_SET) -> dict[str, Any]:
    """Pool-mode results. Raises ValueError (→ bad_input) on a too-small / empty / signal-less pool."""
    usable = [(src, t) for src, t in pool if _word_count(t) >= LENGTH_FLOOR_WORDS]
    if len(usable) < min_set:
        raise ValueError(
            f"pool has {len(usable)} text(s) with >= {LENGTH_FLOOR_WORDS} words "
            f"(< min_set {min_set}); a pairwise-cosine distribution is not shipped on too small a set"
        )
    texts = [t for _, t in usable]
    vocab = build_vocabulary(texts)
    if not vocab["coords"]:
        raise ValueError("pool has no stylometric features (no word tokens / n-grams)")
    vectors = [embed_text(t, vocab) for t in texts]
    cosines = pairwise_cosines(vectors)
    dist = cosine_distribution(cosines)
    modes = effective_modes(vectors)
    results: dict[str, Any] = {
        "n_texts": len(usable),
        "lens": "local-stylometric",
        "pairwise_cosine_distribution": dist,
        "mean_pairwise_cosine": round(dist["mean"], 6) if dist["mean"] is not None else None,
        "effective_modes": round(modes, 6) if modes is not None else None,
        "assumptions": _assumptions(mode="pool"),
    }
    return results


def audit_proximity(target_text: str, centroid_texts: list[tuple[str, str]],
                    *, centroid_source: str) -> dict[str, Any]:
    """Single-doc hivemind-proximity results. Raises ValueError (→ bad_input) on an empty/too-short
    target or a centroid with no text clearing the stylometric stability floor. Vocabulary is the
    union over target + centroid texts (same lens)."""
    target_words = _word_count(target_text)
    if target_words == 0:
        raise ValueError("--target has no word tokens")
    # Apply the SAME stylometric stability floor pool mode enforces (Codex P2): below
    # LENGTH_FLOOR_WORDS a text has too few function-word / char-n-gram observations for a stable
    # vector, so a one-word target or one-word centroid would otherwise emit an uncaveated, basically
    # meaningless cosine under the same lens. Refuse the target; drop sub-floor centroid members.
    if target_words < LENGTH_FLOOR_WORDS:
        raise ValueError(
            f"--target has {target_words} word(s) (< the {LENGTH_FLOOR_WORDS}-word stylometric "
            "stability floor); a proximity cosine is not shipped on too short a target")
    n_centroid_in = len(centroid_texts)
    centroid = [t for _, t in centroid_texts if _word_count(t) >= LENGTH_FLOOR_WORDS]
    if not centroid:
        raise ValueError(
            f"centroid has no text with >= {LENGTH_FLOOR_WORDS} words (the stylometric stability "
            "floor); supply longer centroid material")
    n_dropped_short = n_centroid_in - len(centroid)
    vocab = build_vocabulary([target_text] + centroid)
    if not vocab["coords"]:
        raise ValueError("no stylometric features (no word tokens / n-grams)")
    tvec = embed_text(target_text, vocab)
    cvecs = [embed_text(t, vocab) for t in centroid]
    # Mean vector over the centroid texts (clean-room of voice_fingerprint._centroid's shape, stdlib).
    dim = len(vocab["coords"])
    mean = [sum(v[i] for v in cvecs) / len(cvecs) for i in range(dim)]
    proximity = _cosine(tvec, mean)
    return {
        "lens": "local-stylometric",
        "hivemind_proximity": round(proximity, 6),
        "centroid_provenance": {"n_texts": len(centroid), "source": centroid_source,
                                "n_dropped_short": n_dropped_short,
                                "length_floor_words": LENGTH_FLOOR_WORDS},
        "assumptions": _assumptions(mode="proximity"),
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The pairwise-cosine distribution + the effective number of modes of the supplied POOL "
            "under the named lens (pool mode), or the cosine of the target to the operator-supplied "
            "centroid under the named lens (single-doc proximity mode). A descriptive measurement of a "
            "DISTRIBUTION over a set, oriented mean_pairwise_cosine gt = more homogeneous."
        ),
        "does_not_license": (
            "Any AI/human determination — homogeneity is a property of a distribution, not a "
            "provenance call. High homogeneity is NOT 'AI': a tight topical prompt, a shared "
            "genre/register, or a single source forces a tight pool with no AI involvement; low "
            "homogeneity is NOT 'human'. The single-doc proximity is NOT an identity or authenticity "
            "claim. No absolute band is emitted; thresholds are operator-side. The local-stylometric "
            "number is NOT comparable to the paper's semantic-lens (text-embedding-3-small) number — "
            "it is a different lens answering a style, not a content, question. The surface emits no "
            "verdict."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    proximity_mode = bool(args.target)
    if proximity_mode:
        return _run_proximity(args)
    return _run_pool(args)


def _run_pool(args: argparse.Namespace) -> dict[str, Any]:
    if not (args.manifest or args.dir):
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason="pool mode needs --manifest or --dir (or use --target + a centroid for proximity)",
            reason_category="bad_input")
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
        results = audit_pool(pool, min_set=args.min_set)
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=ref, reason=str(e), reason_category="bad_input")

    warnings: list[str] = []
    if results["effective_modes"] is None:
        warnings.append("numpy is unavailable; effective_modes omitted (the pairwise-cosine "
                        "distribution is stdlib-only and still shipped)")
    total_words = sum(_word_count(t) for _, t in pool)
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=ref, target_words=total_words,
        baseline={"pool": ref, "n_texts": results["n_texts"]},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
    )


def _run_proximity(args: argparse.Namespace) -> dict[str, Any]:
    if not (args.centroid or args.centroid_dir):
        # No bundled centroid — a shipped default would smuggle an implied verdict.
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.target,
            reason="single-doc proximity mode needs --centroid FILE or --centroid-dir DIR "
                   "(operator-supplied; there is no bundled default centroid)",
            reason_category="bad_input")
    try:
        target_text = Path(args.target).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.target, reason=f"cannot read --target: {e}",
            reason_category="bad_input")
    try:
        if args.centroid_dir:
            centroid_texts = _load_dir(Path(args.centroid_dir))
            csource = args.centroid_dir
        else:
            ctext = Path(args.centroid).read_text(encoding="utf-8")
            centroid_texts = [(args.centroid, ctext)]
            csource = args.centroid
    except (OSError, UnicodeDecodeError) as e:
        which = "--centroid-dir" if args.centroid_dir else "--centroid"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.target, reason=f"cannot read {which}: {e}",
            reason_category="bad_input")

    try:
        results = audit_proximity(target_text, centroid_texts, centroid_source=csource)
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.target, reason=str(e), reason_category="bad_input")

    nd = results["centroid_provenance"].get("n_dropped_short", 0)
    warnings = ([f"dropped {nd} centroid text(s) below the {LENGTH_FLOOR_WORDS}-word stylometric "
                 "stability floor before averaging"] if nd else None)
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=args.target, target_words=_word_count(target_text),
        baseline={"centroid": csource,
                  "n_texts": results["centroid_provenance"]["n_texts"]},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    pool = ap.add_argument_group("pool mode (default)")
    pool.add_argument("--manifest", help="JSONL manifest of the response pool (id + text|text_path).")
    pool.add_argument("--dir", help="Directory of pooled texts (.txt/.md, recursive).")
    pool.add_argument("--min-set", type=int, default=DEFAULT_MIN_SET,
                      help=f"Set floor: abstain (bad_input) below this many usable texts "
                           f"(default {DEFAULT_MIN_SET}).")
    prox = ap.add_argument_group("single-doc hivemind-proximity mode (--target + a centroid)")
    prox.add_argument("--target", help="Path to a single target text (switches to proximity mode).")
    prox.add_argument("--centroid", help="Operator-supplied centroid text file (one text).")
    prox.add_argument("--centroid-dir", help="Directory whose mean vector is the centroid.")
    ap.add_argument("--lens", choices=["local-stylometric"], default="local-stylometric",
                    help="Embedding lens. M1 ships only the model-free local-stylometric lens; the "
                         "luar / semantic lenses are a POC-gated M2 seam (not in this build).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.min_set < 2:
        sys.stderr.write("[homogeneity_audit] --min-set must be >= 2 (a pairwise distribution needs "
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
