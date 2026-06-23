#!/usr/bin/env python3
"""within_doc_segmentation.py — within-document register-discontinuity locator (spec tier4b).

Slides a deterministic sentence-anchored window over ONE text, measures the stylometric distance
between adjacent windows (stdlib function-word + char-n-gram + sentence-shape features), and emits
the internal span boundaries where the register shifts most — each as a `stylistic_shift` /
`register_discontinuity` boundary with character offsets and an ordinal band.

NEVER emits an author count, a "different authors" claim, or any authorship inference. The
"never an authorship claim" guarantee is MECHANICAL: a FORBIDDEN_RESULT_KEYS frozenset +
FORBIDDEN_SUBSTRINGS tuple + a recursive assert_no_authorship() guard (raises AuthorshipClaimError)
called immediately before build_output, backed by a BAND_VOCAB whitelist and an available:false
policy_refused refusal path.

M1 lens — stylometric cosine distance (stdlib, model-free, deterministic, no torch/transformers/spacy).
M2 embedding lens is the POC-gated --lens embedding seam: lazy-import + fail-loud missing_dependency,
NOT in this build.

Single input only. Cross-document comparison is un-expressible: there is no --reference / --compare /
--manifest flag, so a "different authors across two documents" question cannot be asked.

Posture: descriptive / no-verdict / anti-Goodhart; calibration_status PROVISIONAL.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
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
    safe_mean,
    safe_sd,
    CHAR_NGRAM_NS,
)
from variance_audit import (  # noqa: E402
    split_sentences,
    sentence_length_stats,
    _WORD_RE,
)

TASK_SURFACE = "document_segmentation"
TOOL_NAME = "within_doc_segmentation"
SCRIPT_VERSION = "1.0"

# Default CLI knobs (echoed into results.assumptions)
DEFAULT_WINDOW_SENTENCES = 5
DEFAULT_STRIDE_SENTENCES = 2
DEFAULT_PEAK_K = 2.5
DEFAULT_MIN_WINDOWS = 3

# Minimum word count for the text to be at all usable.
LENGTH_FLOOR_WORDS = 30

# ---------- Layer 2: BAND_VOCAB whitelist -----------------------------------
# The strongest band is "marked_shift". There is NO "different_author" band —
# mirroring voice_verifier.VERIFIER_BANDS whose strongest is "inconsistent".
BAND_VOCAB: tuple[str, ...] = ("none", "slight_shift", "moderate_shift", "marked_shift")

# ---------- Layer 1: authorship-claim firewall ------------------------------

# Exact keys that must never appear at any depth in the results dict.
FORBIDDEN_RESULT_KEYS: frozenset[str] = frozenset({
    "different_authors", "different_author", "same_author", "authorship",
    "authorship_change", "author_change", "author_count", "n_authors",
    "num_authors", "author_id", "author_ids", "author", "authors",
    "segments_by_author", "p_different_author", "p_same_author",
    "authorship_attribution", "identity", "writer_count", "n_writers",
})

# Substring blocklist — applied to KEYS ONLY at any nesting depth.
# NOT applied to string VALUES, because the surface's own mandated honest
# caveat (assumptions.confounds) legitimately contains the substring "author"
# (the required string "within-author register shifts"). A blanket key-AND-value
# substring walk would raise AuthorshipClaimError on the happy-path envelope.
# The full key+VALUE exact-match walk is kept for FORBIDDEN_RESULT_KEYS (below);
# the substring walk is KEY-ONLY.
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = ("author", "authorship", "writer", "identity")


class AuthorshipClaimError(RuntimeError):
    """Raised when results contain a forbidden authorship key, value, or band."""


def assert_no_authorship(results: Any, _key: str = "") -> None:  # noqa: C901
    """Recursively walk results and raise AuthorshipClaimError on any authorship claim.

    Rules (mirrors the recursion shape of output_schema.validate_results_bounds,
    output_schema.py:194-214):
    1. Any dict KEY in FORBIDDEN_RESULT_KEYS (exact, case-folded) at any depth.
    2. Any string leaf VALUE that exactly equals (case-folded) a member of
       FORBIDDEN_RESULT_KEYS — catches a band rendered as "different_author".
    3. Any dict KEY containing a FORBIDDEN_SUBSTRINGS token (case-folded substring,
       KEY-ONLY — not applied to values so assumptions.confounds passes).
    4. Any value reached under a "band" key that is not in BAND_VOCAB (out-of-whitelist
       band is a posture breach, not a silent coercion).
    """
    if isinstance(results, dict):
        for k, v in results.items():
            k_lower = str(k).lower()
            # Rule 1: exact key membership
            if k_lower in FORBIDDEN_RESULT_KEYS:
                raise AuthorshipClaimError(
                    f"Forbidden authorship key {k!r} found in results (policy_refused)"
                )
            # Rule 3: substring match on KEY only
            for sub in FORBIDDEN_SUBSTRINGS:
                if sub in k_lower:
                    raise AuthorshipClaimError(
                        f"Key {k!r} contains forbidden substring {sub!r} (policy_refused)"
                    )
            # Rule 4: band whitelist enforcement
            if k_lower == "band":
                if isinstance(v, str) and v not in BAND_VOCAB:
                    raise AuthorshipClaimError(
                        f"Band value {v!r} is not in BAND_VOCAB {BAND_VOCAB} (policy_refused)"
                    )
            assert_no_authorship(v, str(k))
        return
    if isinstance(results, (list, tuple)):
        for item in results:
            assert_no_authorship(item, _key)
        return
    if isinstance(results, str):
        # Rule 2: exact-match on string leaf values (case-folded)
        if results.lower() in FORBIDDEN_RESULT_KEYS:
            raise AuthorshipClaimError(
                f"String value {results!r} exactly matches a forbidden authorship key "
                f"(policy_refused)"
            )
        return
    # int / float / bool / None: nothing to check


# ---------- Offset reconstruction (spec § Method step 1) -------------------
# variance_audit.split_sentences returns a bare list[str] with NO char offsets.
# We reconstruct them locally using a forward str.find with a monotonically advancing
# cursor (sentences are in document order, so this is correct).

def _sentence_spans(text: str, sentences: list[str]) -> list[tuple[int, int]]:
    """Reconstruct (char_start, char_end) for each sentence in text.

    Sentences are located by a forward str.find from a running cursor.
    When find() fails (e.g. NLTK-normalized whitespace), we fall back to
    advancing the cursor via the _WORD_RE pattern and snapping to the nearest
    match start >= cursor.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for sent in sentences:
        pos = text.find(sent, cursor)
        if pos >= 0:
            span = (pos, pos + len(sent))
            cursor = span[1]
        else:
            # Fallback: snap to nearest _WORD_RE match >= cursor
            matches = list(_WORD_RE.finditer(text, cursor))
            if matches:
                snap_start = matches[0].start()
                snap_end = snap_start + len(sent)
                span = (snap_start, min(snap_end, len(text)))
                cursor = span[1]
            else:
                # Exhausted text; anchor at end
                span = (len(text), len(text))
                cursor = len(text)
        spans.append(span)
    return spans


# ---------- Sentence-anchored windowing (spec § Method step 1) -------------

def _build_windows(
    text: str,
    sentences: list[str],
    sentence_spans: list[tuple[int, int]],
    window_sentences: int,
    stride_sentences: int,
) -> list[dict[str, Any]]:
    """Slide a sentence-anchored window over the sentence list.

    Each window records window_index, (char_start, char_end), n_sentences, and text.
    """
    n = len(sentences)
    if n == 0:
        return []
    windows: list[dict[str, Any]] = []
    start = 0
    idx = 0
    while start < n:
        end = min(start + window_sentences, n)
        char_start = sentence_spans[start][0]
        char_end = sentence_spans[end - 1][1]
        windows.append({
            "window_index": idx,
            "char_start": char_start,
            "char_end": char_end,
            "n_sentences": end - start,
            "text": text[char_start:char_end],
        })
        idx += 1
        start += stride_sentences
    return windows


# ---------- Feature extraction (spec § Method step 2) ----------------------
# FEATURE_NAMES is computed once from an empty/representative call to the three
# families. We use the same families as stylometry_core-based surfaces.

def _window_features(window_text: str) -> dict[str, float]:
    """Build a flat feature dict for one window text."""
    # Function-word features (stylometry_core.py:243)
    words = _WORD_RE.findall(window_text.lower())
    fw = function_word_features(words)

    # Char-n-gram features (stylometry_core.py:249, families ns=(3,4,5))
    cng_families = char_ngram_features(window_text, ns=CHAR_NGRAM_NS)
    cng_flat: dict[str, float] = {}
    for fam_dict in cng_families.values():
        cng_flat.update(fam_dict)

    # Sentence-shape stats from variance_audit.sentence_length_stats (variance_audit.py:202)
    # Use the window's own sentence split for the shape stats.
    window_sents = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}", window_text) if s.strip()]
    if not window_sents:
        window_sents = [window_text] if window_text.strip() else ["a"]
    shape = sentence_length_stats(window_sents)

    combined: dict[str, float] = {}
    combined.update(fw)
    combined.update(cng_flat)
    # Include the shape stats that are floats
    for k, v in shape.items():
        if isinstance(v, (int, float)):
            combined[f"sent_shape_{k}"] = float(v)

    return combined


def _get_feature_names(sample_text: str = "Hello world. This is a test sentence.") -> list[str]:
    """Compute the fixed, ordered feature name list from a sample text."""
    feats = _window_features(sample_text)
    return sorted(feats.keys())


# ---------- Distance computation (spec § Method steps 3-5b) ----------------

EPSILON = 1e-9  # explicit zero-variance guard (spec §5(a))


def _z_score_features(
    raw: list[dict[str, float]],
    feature_names: list[str],
) -> list[dict[str, float]]:
    """Within-document z-score: standardize each feature across all windows."""
    z: list[dict[str, float]] = [{} for _ in raw]
    for name in feature_names:
        vals = [r.get(name, 0.0) for r in raw]
        mu = safe_mean(vals)
        sd = safe_sd(vals)
        for i, r in enumerate(raw):
            z[i][name] = (r.get(name, 0.0) - mu) / (sd + EPSILON)
    return z


def _cosine_similarity(
    a: dict[str, float],
    b: dict[str, float],
    feature_names: list[str],
) -> float:
    """Cosine similarity clamped to [-1, 1]. Returns 0.0 on zero-norm vector."""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for name in feature_names:
        av = a.get(name, 0.0)
        bv = b.get(name, 0.0)
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    sim = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    return max(-1.0, min(1.0, sim))  # explicit clamp


def _adjacent_distance_profile(
    z_vecs: list[dict[str, float]],
    feature_names: list[str],
) -> list[float]:
    """Compute bounded cosine distances d_i in [0, 1] for adjacent window pairs.

    d_i = (1 - cosine_similarity(z[i], z[i+1])) / 2.0  →  [0, 1]
    A zero-norm window → d_i = 0.0 (no shift, never None).
    """
    profile: list[float] = []
    for i in range(len(z_vecs) - 1):
        sim = _cosine_similarity(z_vecs[i], z_vecs[i + 1], feature_names)
        d_i = (1.0 - sim) / 2.0
        profile.append(d_i)
    return profile


# ---------- Band assignment (spec § Method step 5c) ------------------------

def _median(vals: list[float]) -> float:
    """Clean-room median (statistics.median). No such helper exists in
    stylometry_core/variance_audit — grep-verified."""
    return statistics.median(vals) if vals else 0.0


def _mad(vals: list[float], med: float) -> float:
    """Median absolute deviation."""
    if not vals:
        return 0.0
    return statistics.median([abs(d - med) for d in vals])


def _band_thresholds(profile: list[float], peak_k: float) -> tuple[float, float, float]:
    """Compute (T_slight, T_moderate, T_marked) from the profile's own MAD.

    T_moderate == med + peak_k * mad (the same k drives peak detection).
    MAD == 0: all thresholds collapse to med (degenerate flat profile).
    """
    med = _median(profile)
    mad = _mad(profile, med)
    T_slight = med + 1.0 * mad
    T_moderate = med + peak_k * mad
    T_marked = med + 4.0 * mad
    return T_slight, T_moderate, T_marked


def _assign_band(d: float, T_slight: float, T_moderate: float, T_marked: float) -> str:
    """Map a continuous distance to BAND_VOCAB."""
    if d >= T_marked:
        return "marked_shift"
    if d >= T_moderate:
        return "moderate_shift"
    if d >= T_slight:
        return "slight_shift"
    return "none"


# ---------- Excerpt extraction (spec § Method step 5e) ---------------------

def _excerpt_before(text: str, char_offset: int, n_tokens: int = 20) -> str:
    """Verbatim excerpt ending at char_offset, starting at the n_tokens-th _WORD_RE
    token before char_offset (or start-of-text). Snaps to _WORD_RE boundaries."""
    matches = [m for m in _WORD_RE.finditer(text, 0, char_offset)]
    if not matches:
        return text[:char_offset] if char_offset > 0 else ""
    start_match = matches[-n_tokens] if len(matches) >= n_tokens else matches[0]
    return text[start_match.start():char_offset]


def _excerpt_after(text: str, char_offset: int, n_tokens: int = 20) -> str:
    """Verbatim excerpt from char_offset to end of the 20th _WORD_RE token at or after
    char_offset (or end-of-text). Snaps to _WORD_RE boundaries."""
    matches = list(_WORD_RE.finditer(text, char_offset))
    if not matches:
        return text[char_offset:] if char_offset < len(text) else ""
    end_match = matches[n_tokens - 1] if len(matches) >= n_tokens else matches[-1]
    return text[char_offset:end_match.end()]


# ---------- Distance distribution summary (spec § Method step 5) -----------

def _quantile(ordered: list[float], q: float) -> float:
    """Linear-interpolation quantile on a sorted list."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = lo + 1
    if hi >= len(ordered):
        return ordered[-1]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _distance_distribution(profile: list[float]) -> dict[str, Any]:
    """7-key distribution summary: n, mean, sd, min, p10, p50, p90."""
    if not profile:
        return {"n": 0, "mean": 0.0, "sd": 0.0, "min": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0}
    ordered = sorted(profile)
    n = len(profile)
    return {
        "n": n,
        "mean": safe_mean(list(profile)),
        "sd": safe_sd(list(profile)),
        "min": ordered[0],
        "p10": _quantile(ordered, 0.10),
        "p50": _quantile(ordered, 0.50),
        "p90": _quantile(ordered, 0.90),
    }


# ---------- Core analysis function -----------------------------------------

def analyze_document(
    text: str,
    *,
    window_sentences: int = DEFAULT_WINDOW_SENTENCES,
    stride_sentences: int = DEFAULT_STRIDE_SENTENCES,
    peak_k: float = DEFAULT_PEAK_K,
    min_windows: int = DEFAULT_MIN_WINDOWS,
) -> dict[str, Any]:
    """Full analysis pipeline. Returns a results dict or raises ValueError (→ bad_input).

    Deterministic: no model, no randomness, stdlib only.
    """
    # 1. Sentence splitting and offset reconstruction
    sentences = split_sentences(text)
    if not sentences:
        raise ValueError("text has no detectable sentences")

    sentence_spans = _sentence_spans(text, sentences)

    # 2. Build sentence-anchored windows
    windows = _build_windows(text, sentences, sentence_spans, window_sentences, stride_sentences)
    if len(windows) < min_windows:
        raise ValueError(
            f"text yields only {len(windows)} window(s) with window_sentences={window_sentences} "
            f"(< min_windows={min_windows}); too short to have an internal boundary profile"
        )

    # 3. Per-window feature extraction
    raw_features = [_window_features(w["text"]) for w in windows]

    # 4. Determine FEATURE_NAMES from the union of all window feature keys
    all_names: set[str] = set()
    for feats in raw_features:
        all_names.update(feats.keys())
    feature_names = sorted(all_names)

    # Normalize raw features: fill missing keys with 0.0
    for feats in raw_features:
        for name in feature_names:
            if name not in feats:
                feats[name] = 0.0

    # 5. Within-document z-score
    z_vecs = _z_score_features(raw_features, feature_names)

    # 6. Adjacent distance profile
    profile = _adjacent_distance_profile(z_vecs, feature_names)
    if not profile:
        raise ValueError("too few windows to compute an adjacent distance profile")

    # 7. Band thresholds (the same k drives both band assignment and peak detection)
    T_slight, T_moderate, T_marked = _band_thresholds(profile, peak_k)

    # 8. Boundary detection: local peaks above T_moderate
    boundaries: list[dict[str, Any]] = []
    n_pairs = len(profile)
    for i, d_i in enumerate(profile):
        # Local-peak condition: d_i >= neighbors; edges are -inf
        left = profile[i - 1] if i > 0 else float("-inf")
        right = profile[i + 1] if i < n_pairs - 1 else float("-inf")
        is_peak = d_i >= left and d_i >= right
        if not is_peak:
            continue
        if d_i < T_moderate:
            continue
        band = _assign_band(d_i, T_slight, T_moderate, T_marked)
        # char_offset = char_start of window i+1 (the seam where the next window begins)
        char_offset = windows[i + 1]["char_start"]
        boundaries.append({
            "char_offset": char_offset,
            "between_windows": [i, i + 1],
            "distance": d_i,
            "band": band,
            "excerpt_before": _excerpt_before(text, char_offset),
            "excerpt_after": _excerpt_after(text, char_offset),
        })

    # Sort boundaries by char_offset
    boundaries.sort(key=lambda b: b["char_offset"])

    dist_summary = _distance_distribution(profile)

    results: dict[str, Any] = {
        "n_windows": len(windows),
        "window_sentences": window_sentences,
        "stride_sentences": stride_sentences,
        "adjacent_distance_profile": profile,
        "distance_distribution": dist_summary,
        "boundaries": boundaries,
        "calibration_status": "provisional",
        "assumptions": {
            "method": (
                "sentence-anchored sliding window + adjacent stylometric cosine distance "
                "(stdlib function-word + char-3/4/5-gram + sentence-shape features); "
                "within-document z-score baseline; MAD-relative peak detection"
            ),
            "lens": "stylometric (M1; model-free, stdlib, deterministic)",
            "window_sentences": window_sentences,
            "stride_sentences": stride_sentences,
            "peak_k": peak_k,
            "internal_baseline": (
                "within-document window distribution (z-score / MAD over the one text); "
                "never an external corpus"
            ),
            "orientation": "higher distance = greater register discontinuity at the seam",
            "confounds": (
                "copyedits / translations / quotation / genre-switch / mode-switch (e.g. "
                "abstract→narrative) produce within-author register shifts that the stylometric "
                "distance detects as boundaries; a boundary marks where the style changes, "
                "never who wrote it"
            ),
            "no_band_absolute": (
                "bands are within-document MAD-relative (median + k*MAD), not absolute calibrated "
                "cuts; the same k drives both band assignment and peak detection"
            ),
            "posture": "descriptive / no-verdict / never an authorship or identity claim",
            "posture_guarantee": (
                "this surface never infers who wrote the text, how many people wrote it, "
                "or whether sections differ by person; the never-claim guarantee is "
                "mechanical (FORBIDDEN_RESULT_KEYS + FORBIDDEN_SUBSTRINGS + "
                "assert_no_authorship guard), not rhetorical"
            ),
        },
    }
    return results


# ---------- Envelope builder (the firewall call-site) ----------------------

def compose_envelope(
    text: str,
    target_path: str | None,
    *,
    window_sentences: int = DEFAULT_WINDOW_SENTENCES,
    stride_sentences: int = DEFAULT_STRIDE_SENTENCES,
    peak_k: float = DEFAULT_PEAK_K,
    min_windows: int = DEFAULT_MIN_WINDOWS,
) -> dict[str, Any]:
    """Run analysis and build the output envelope.

    Calls assert_no_authorship(results) IMMEDIATELY BEFORE build_output so the guard
    runs on the same results object the envelope will carry (the call-site anchor
    for the Layer-1 firewall, per the spec).
    """
    target_words = len(_WORD_RE.findall(text))
    if target_words < LENGTH_FLOOR_WORDS:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target_path, target_words=target_words,
            reason=f"text has {target_words} word(s) (< floor {LENGTH_FLOOR_WORDS}); "
                   "too short for a meaningful boundary profile",
            reason_category="text_too_short",
        )

    try:
        results = analyze_document(
            text,
            window_sentences=window_sentences,
            stride_sentences=stride_sentences,
            peak_k=peak_k,
            min_windows=min_windows,
        )
    except ValueError as exc:
        cat = "text_too_short" if "too short" in str(exc).lower() else "bad_input"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target_path, target_words=target_words,
            reason=str(exc),
            reason_category=cat,
        )

    # Layer-1 guard: assert_no_authorship IMMEDIATELY BEFORE build_output.
    # Any authorship key/value or out-of-whitelist band raises AuthorshipClaimError,
    # which main() catches and routes to policy_refused.
    assert_no_authorship(results)

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,  # single input; the baseline IS the document's own window distribution
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        available=True,
        validate_bounds=True,
    )


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The within-document register-discontinuity profile of the ONE supplied text under "
            "the named lens: the adjacent-window stylometric-distance series, its distribution "
            "summary, and the internal boundary loci (character offset, ordinal shift band, and "
            "a verbatim excerpt on each side) where the register shifts most."
        ),
        "does_not_license": (
            "Any authorship or identity claim; any 'different authors' / 'multiple authors' / "
            "author-count / 'this section was written by X' determination; any cross-document "
            "attribution; any absolute band; any claim that a boundary marks an authorial change "
            "rather than a stylistic / register change. A register discontinuity is NOT an "
            "authorship boundary — copyedits, translations, quotation, genre-switches, and "
            "mode-switches all produce within-author register shifts. The surface never licenses "
            "an inference about who wrote any part of the text."
        ),
    }


# ---------- M2 seam: embedding lens (NOT in this build) --------------------
# --lens embedding MUST fail loud whether or not a model module is importable.
# A stub or name-collision must NEVER make "embedding" silently emit stylometric
# numbers mislabeled as embedding results.

def _run_embedding_lens(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Fail loud: embedding lens is POC-gated and NOT in this build."""
    return build_error_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        reason=(
            "The --lens embedding (M2 semantic/embedding boundary lens) is not available in "
            "this build. Install the embedding dependency and use --lens embedding once the "
            "M2 seam is released."
        ),
        reason_category="missing_dependency",
    )


# ---------- CLI ------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Single input only — no --reference / --compare / --manifest (Layer 3: cross-doc un-expressible)
    ap.add_argument(
        "input", nargs="?", metavar="INPUT",
        help="Path to input text file (.txt / .md), or '-' for stdin.",
    )
    ap.add_argument("--input", dest="input_flag", metavar="INPUT",
                    help="Path to input text file (alternative to positional).")
    ap.add_argument("--window-sentences", type=int, default=DEFAULT_WINDOW_SENTENCES,
                    help=f"Sentences per window (default: {DEFAULT_WINDOW_SENTENCES}).")
    ap.add_argument("--stride-sentences", type=int, default=DEFAULT_STRIDE_SENTENCES,
                    help=f"Stride between windows in sentences (default: {DEFAULT_STRIDE_SENTENCES}).")
    ap.add_argument("--peak-k", type=float, default=DEFAULT_PEAK_K,
                    help=f"Peak-detection k (T_moderate = median + k*MAD; default: {DEFAULT_PEAK_K}).")
    ap.add_argument("--min-windows", type=int, default=DEFAULT_MIN_WINDOWS,
                    help=f"Minimum usable windows; fewer → bad_input (default: {DEFAULT_MIN_WINDOWS}).")
    ap.add_argument("--lens", choices=["stylometric", "embedding"], default="stylometric",
                    help="Analysis lens (default: stylometric; M2 'embedding' not in this build).")
    ap.add_argument("--json", dest="json_out", action="store_true",
                    help="Output JSON to stdout (default when --out is not given).")
    ap.add_argument("--out", metavar="FILE",
                    help="Write JSON output to FILE instead of stdout.")
    args = ap.parse_args(argv)

    # M2 embedding lens: fail loud unconditionally (no silent fallback)
    if args.lens == "embedding":
        env = _run_embedding_lens()
        _emit(env, args)
        return 3

    # Resolve input path
    input_src = args.input_flag or args.input
    if not input_src or input_src == "-":
        try:
            text = sys.stdin.read()
        except (OSError, UnicodeDecodeError) as exc:
            env = build_error_output(
                task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                reason=f"cannot read stdin: {exc}", reason_category="bad_input",
            )
            _emit(env, args)
            return 1
        target_path = "-"
    else:
        p = Path(input_src)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            env = build_error_output(
                task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                target_path=input_src, reason=f"cannot read {input_src!r}: {exc}",
                reason_category="bad_input",
            )
            _emit(env, args)
            return 1
        target_path = str(p)

    try:
        env = compose_envelope(
            text,
            target_path,
            window_sentences=args.window_sentences,
            stride_sentences=args.stride_sentences,
            peak_k=args.peak_k,
            min_windows=args.min_windows,
        )
    except AuthorshipClaimError as exc:
        # main() is the AuthorshipClaimError catcher (per the spec).
        # compose_envelope raises; main() converts to an available:false policy_refused envelope.
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target_path,
            reason=str(exc),
            reason_category="policy_refused",
        )
        _emit(env, args)
        return 3

    rc = 0 if env.get("available") else 1
    _emit(env, args)
    return rc


def _emit(env: dict[str, Any], args: argparse.Namespace) -> None:
    out = json.dumps(env, indent=2, ensure_ascii=False)
    if hasattr(args, "out") and args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    else:
        sys.stdout.write(out + "\n")


if __name__ == "__main__":
    sys.exit(main())
