#!/usr/bin/env python3
"""watermark_probe.py — KGW green-list z-test ("was this watermarked?").

A model-free presence probe for ONE family of token-level watermarks:
the KGW green-list/red-list scheme of Kirchenbauer et al., "A Watermark
for Large Language Models" (arXiv:2301.10226). Given a target text **and
the operator-supplied watermark key/hash parameters** ``(key, gamma,
hash_scheme, vocab)``, it computes the green-list z-statistic and reports
it as one more per-signal value with an operator-side reliability band.

Implements ``specs/29-watermark-probe.md`` (M1).

The asymmetry that makes this stdlib
-------------------------------------
The *generator* needs a model (it biases the next-token logits toward a
hash-seeded green list). The *detector* does not: it is a hash-seeded
partition of the vocabulary + a green-token count + a one-proportion
z-score. That is counting and arithmetic — no model is loaded on any
path. The operator supplies the tokenization; the probe never sniffs.

What a positive means — and what it does NOT
--------------------------------------------
A large positive z says the text's green-token count is statistically
improbable under the null that tokens were chosen independently of *this*
green-list partition — i.e. it is **watermark-consistent with the named
KGW scheme** (relative to the supplied key). It is NOT "AI-generated."

A low/zero z, or no available key, means **nothing** about human
authorship. The probe is blind to every scheme it doesn't hold a key
for, to semantic / SynthID-class watermarks, and to any watermark
scrubbed below the noise floor by paraphrase/rewrite (the 2306.04634 /
WaterPark decay result). **Absence of a watermark signal is not evidence
of human authorship.** The surface is architecturally unable to be read
that way: it emits NO ``is_watermarked`` / ``is_ai`` / ``is_human`` /
``verdict`` field, the two descriptive bands name evidence STRENGTH (not
a class), and the claim license forbids thresholding any of z / p_value /
band into such a decision.

Posture (mirrors fast_detect_curvature's "uncalibrated, no verdict",
with the extra absence-≠-human refusal)
---------------------------------------
  * No shipped threshold on the z; the band's cut-points are operator-
    side and PROVISIONAL (a detection-power band, not a verdict).
  * The negative is ``unknown`` (band ``under_powered``), structurally —
    never ``human`` and never an unqualified ``false``.
  * Keyed, parameterized, never sniffed. No "detect any watermark" mode
    (that is the watermark-stealing attack — out of scope).
  * One value among many; designed to sit in an evidence pack beside the
    stylometric signals, never to override them or feed a selector.

CLI / ``setec run watermark_probe``
-----------------------------------
    python3 plugins/setec-voiceprint/scripts/watermark_probe.py \\
        --target TARGET.txt --key KEY --vocab V.json \\
        [--gamma 0.5] [--hash-scheme left-hash|prefix-h] [--prefix-h H] \\
        [--tokens TOKENS.json | --tokenizer fallback:whitespace] \\
        [--rewrite-exposure none|light|heavy] \\
        [--catalog CATALOG.json]  (M2 sweep) \\
        [--json] [--out PATH]

Loads NO model. The secret key is never echoed to stdout/logs — only a
non-secret ``key_id`` (a truncated SHA-256 of the key) is emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore
from stylometry_core import word_tokens  # type: ignore

# IMPORTANT (posture): this module imports ONLY stdlib + the house
# rendering helpers (claim_license, output_schema) + the documented
# whitespace-fallback tokenizer (stylometry_core.word_tokens). It must
# NOT import any selection / calibration / threshold-setting layer
# (e.g. conformal_gate, calibration.calibrate_thresholds,
# calibration_drift_monitor). A structural test pins this so the headline
# z-test surface stays a clean, un-gated producer card.

SCRIPT_VERSION = "0.1.0"
# Finding 5: this ONE name is used for the capability `surface:` field, the
# claim_license_surfaces/<name>.txt filename, the task_surface string, and the
# envelope task_surface — all four MUST be identical or the TASK_SURFACE_LABELS
# lookup fails at build_output time. Chosen deliberately: `watermark_probe`.
TASK_SURFACE = "watermark_probe"
TOOL_NAME = "watermark_probe"
SCORE_VERSION = "kgw_green_list_ztest_v1"

# The green-list PARTITION this detector scores against (Codex P1). The green
# list at a position is a SHA-256(key, context) seed fed to Python's
# random.Random.shuffle — a Voiceprint-DEFINED PRF. It is NOT byte-compatible
# with the official KGW reference processor's seeding schemes (simple_1 /
# selfhash / minhash) or its torch RNG device, so tokens generated by a
# DIFFERENT processor (including the official one) fall in an UNRELATED green
# list here and systematically false-negative. A detection is meaningful only
# when the operator generated with THIS exact partition: same key / gamma /
# hash_scheme / vocab AND this PRF. Stamped in every result's assumptions and
# named in the claim license so the scope is never implicit.
PARTITION_PRF = "voiceprint-greenlist-v1/sha256-seed+pyrandom-shuffle"

# Paper default green fraction.
DEFAULT_GAMMA = 0.5
# Hash schemes. ``left-hash`` (seed from the single previous token id — the
# paper's simplest scheme) is the M1 default and the only one required.
# ``prefix-h`` (seed from the prior ``h`` tokens) is an operator variant.
HASH_SCHEMES = ("left-hash", "prefix-h")
DEFAULT_HASH_SCHEME = "left-hash"
DEFAULT_PREFIX_H = 4

# Hard minimum scored positions below which no z is meaningful → bad_input.
MIN_SCORED_TOKENS = 1
# Length floor below which the band is forced ``under_powered`` (a short or
# possibly-rewritten text is reported as insufficient evidence, never "no
# watermark"). PROVISIONAL, operator-tunable.
DEFAULT_LENGTH_FLOOR = 50
# z margin: below this the band is ``under_powered`` (insufficient evidence
# either way); at/above it the band is ``watermark_consistent``. PROVISIONAL.
# NOTE (finding 3a): there is deliberately NO "strongly_*" top tier — two
# descriptive bands only, so there is no "maximum" band that reads as a fire
# signal that a downstream consumer could promote to is_watermarked=True.
DEFAULT_Z_MARGIN = 4.0

REWRITE_EXPOSURES = ("none", "light", "heavy")

DECAY_CAVEAT = (
    "Detection power decays under rewrite: the KGW watermark survives light "
    "paraphrase but erodes under heavy rewrite, translation, and copy-paste "
    "mixing (Kirchenbauer et al. 2306.04634; WaterPark 2411.13425). A near-"
    "margin z on a short or possibly-rewritten text is under-powered — "
    "insufficient evidence either way — not 'no watermark'."
)

WHITESPACE_FALLBACK_WARNING = (
    "watermark_probe: using the whitespace fallback tokenizer. KGW partitions "
    "the MODEL's BPE vocabulary and biases MODEL tokens; whitespace word "
    "tokens are misaligned with a real BPE watermark, so the z collapses "
    "toward 0 — this UNDER-DETECTS real watermarks and is for toy/demo/CI "
    "fixtures only. Supply --tokens (a token-id stream matching the "
    "watermarking tokenizer) + --vocab for a real detection run."
)

# Two descriptive bands only (finding 3a) — evidence STRENGTH for a NAMED
# scheme, never a class. PROVISIONAL by default. The under-powered band is
# the structural home of the "negative is unknown, not human" reading.
BAND_UNDER_POWERED = "under_powered"
BAND_WATERMARK_CONSISTENT = "watermark_consistent"
BAND_STRINGS = (BAND_UNDER_POWERED, BAND_WATERMARK_CONSISTENT)


class WatermarkProbeError(ValueError):
    """House refusal type for invalid parameters (raised by
    validate_params; surfaced by the CLI as a ``bad_input`` exit)."""


# =====================================================================
# Key provenance
# =====================================================================


def key_id(key: str) -> str:
    """Non-secret provenance label for a key: a truncated SHA-256 hex
    digest. NEVER the key itself. Lets two runs be compared/audited
    without exposing the secret."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"kid_{digest[:16]}"


# =====================================================================
# Keyed green-list partition (the KGW detector, clean-roomed from the paper)
# =====================================================================


def _context_seed(key: str, context_ids: Sequence[int]) -> int:
    """Deterministic PRNG seed from the key and the position context.

    The green list at position t is a function of hash(key, context(t)).
    We build a stable integer seed from the key bytes + the context token
    ids via SHA-256 so the partition is reproducible across processes
    (Python's ``hash()`` is salted per-process and would NOT be stable).
    """
    h = hashlib.sha256()
    h.update(key.encode("utf-8"))
    h.update(b"|")
    h.update(",".join(str(i) for i in context_ids).encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big")


def green_list(
    key: str,
    context_ids: Sequence[int],
    *,
    gamma: float,
    vocab_size: int,
) -> frozenset[int]:
    """Return the green-list token-id set for one position's context.

    Deterministic: same ``(key, context_ids, gamma, vocab_size)`` ⇒ same
    set. Size is ``floor(gamma * vocab_size)``. The remaining ids are the
    red list. Implemented as a seeded shuffle-and-take so the partition is
    a uniform random subset keyed on the context (the paper's scheme).
    """
    green_size = int(math.floor(gamma * vocab_size))
    if green_size <= 0:
        return frozenset()
    if green_size >= vocab_size:
        return frozenset(range(vocab_size))
    rng = random.Random(_context_seed(key, context_ids))
    ids = list(range(vocab_size))
    rng.shuffle(ids)
    return frozenset(ids[:green_size])


def _contexts(
    token_ids: Sequence[int],
    *,
    hash_scheme: str,
    prefix_h: int,
) -> list[tuple[int, Sequence[int]]]:
    """Yield ``(scored_position_index, context_ids)`` for every position
    that has a valid context.

    ``left-hash``: context is the single previous token id; positions
    1..N-1 are scored. ``prefix-h``: context is the prior ``h`` tokens;
    positions h..N-1 are scored (a position needs h predecessors).
    """
    out: list[tuple[int, Sequence[int]]] = []
    n = len(token_ids)
    if hash_scheme == "left-hash":
        for t in range(1, n):
            out.append((t, (token_ids[t - 1],)))
    elif hash_scheme == "prefix-h":
        for t in range(prefix_h, n):
            out.append((t, tuple(token_ids[t - prefix_h:t])))
    else:  # pragma: no cover - validated upstream
        raise WatermarkProbeError(f"unknown hash_scheme {hash_scheme!r}")
    return out


# =====================================================================
# z-statistic + p-value (the paper's detector)
# =====================================================================


def _normal_sf(z: float) -> float:
    """One-sided upper-tail survival P(Z >= z) for a standard normal,
    via the complementary error function. Full float precision (NOT
    rounded — finding 4: rounding a z=6 tail to a [0,1] floor erases the
    signal)."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def green_z_test(
    token_ids: Sequence[int],
    *,
    key: str,
    gamma: float,
    vocab_size: int,
    hash_scheme: str,
    prefix_h: int = DEFAULT_PREFIX_H,
) -> dict[str, Any]:
    """Compute the KGW green-list z-statistic + p-value over a token-id
    stream. Pure: no I/O, no model.

    Over the T scored positions (positions with a valid context), count
    ``green_count`` = tokens that fell in their position's green list.
    Under the null "the text was generated without knowledge of the
    green-list rule", green_count ~ Binomial(T, gamma), so:

        z = (green_count - gamma*T) / sqrt(T * gamma * (1 - gamma))
        p = P(Z >= z)            (one-sided upper tail)

    Returns z, p_value (full precision), neg_log10_p (a transform-safe
    tail-precision field — finding 4), green_fraction, green_count,
    n_scored_tokens.
    """
    contexts = _contexts(
        token_ids, hash_scheme=hash_scheme, prefix_h=prefix_h,
    )
    # Dedupe repeated n-grams (Codex P1 / the KGW reference's
    # ignore_repeated_ngrams=True). A repeated (context, current_token) event reuses
    # the SAME deterministic green list, so counting each repeat as an independent
    # Bernoulli trial violates the iid null and inflates z — a token repeated 201x
    # would report z≈14, T=200 from a SINGLE unique transition. Score each unique
    # (context, current_token) event once; report the effective T (n_scored_tokens)
    # and the raw position count (n_positions) so the dedup is visible.
    n_positions = len(contexts)
    seen: set[tuple] = set()
    green_count = 0
    for pos, ctx in contexts:
        event = (tuple(ctx), token_ids[pos])
        if event in seen:
            continue
        seen.add(event)
        g = green_list(key, ctx, gamma=gamma, vocab_size=vocab_size)
        if token_ids[pos] in g:
            green_count += 1
    t_scored = len(seen)

    if t_scored <= 0:
        return {
            "z": None,
            "p_value": None,
            "neg_log10_p": None,
            "green_fraction": None,
            "green_count": green_count,
            "n_scored_tokens": 0,
            "n_positions": n_positions,
        }

    expected = gamma * t_scored
    denom = math.sqrt(t_scored * gamma * (1.0 - gamma))
    z = (green_count - expected) / denom if denom > 0 else 0.0
    p_value = _normal_sf(z)
    # neg_log10_p: a transform-safe tail-precision field. Named so the
    # output_schema [0,1] probability bound does NOT apply to it (it is a
    # -log10 transform: 0 at p=0.5, large+ in the tail). This preserves a
    # z=6 tail (p~1e-9 → neg_log10_p~9) that a round(p, 4) would floor to 0.
    if p_value > 0.0:
        neg_log10_p = -math.log10(p_value)
    else:
        # p underflowed to 0.0; recover the tail magnitude from the normal
        # log-survival asymptotic so the field never reports a bogus "inf".
        # log10 SF(z) ~ -(z^2/2)/ln(10) - log10(z*sqrt(2*pi)) for large z.
        neg_log10_p = (
            (z * z / 2.0) / math.log(10.0)
            + math.log10(z * math.sqrt(2.0 * math.pi))
        ) if z > 0 else 0.0
    return {
        "z": z,
        "p_value": p_value,
        "neg_log10_p": neg_log10_p,
        "green_fraction": green_count / t_scored,
        "green_count": green_count,
        "n_scored_tokens": t_scored,
        "n_positions": n_positions,
    }


# =====================================================================
# Reliability band (descriptive, PROVISIONAL — two tiers only)
# =====================================================================


def reliability_band(
    z: float | None,
    *,
    n_scored_tokens: int,
    rewrite_exposure: str,
    length_floor: int = DEFAULT_LENGTH_FLOOR,
    z_margin: float = DEFAULT_Z_MARGIN,
) -> str:
    """Map (z, T, rewrite_exposure) to a DESCRIPTIVE band string. Two
    tiers only (finding 3a): ``under_powered`` / ``watermark_consistent``.

    Detection-power language, never a verdict. A too-short T, a heavy
    declared rewrite exposure, an unavailable z, or a z below the margin
    → ``under_powered`` (insufficient evidence either way — the low-z case
    is ``unknown``, NOT "no watermark" and NOT "human"). Only a z at/above
    the margin on a long-enough, not-heavily-rewritten text reaches
    ``watermark_consistent``.
    """
    if z is None:
        return BAND_UNDER_POWERED
    if n_scored_tokens < length_floor:
        return BAND_UNDER_POWERED
    if rewrite_exposure == "heavy":
        return BAND_UNDER_POWERED
    if z >= z_margin:
        return BAND_WATERMARK_CONSISTENT
    return BAND_UNDER_POWERED


# =====================================================================
# Parameter validation (the house refusal)
# =====================================================================


def validate_params(
    *,
    key: str | None,
    gamma: float,
    vocab_size: int,
    hash_scheme: str,
    n_scored_tokens: int | None = None,
    prefix_h: int = DEFAULT_PREFIX_H,
    token_ids: Sequence[int] | None = None,
) -> None:
    """Raise ``WatermarkProbeError`` (a ``ValueError``) on an invalid
    parameter set: missing/empty key, gamma not in (0,1), empty vocab,
    unknown hash scheme, a non-positive prefix-h window, T below the hard
    minimum, or (when ``token_ids`` is given) any token id that is not a
    plain int in ``[0, vocab_size)``. The CLI surfaces these as
    ``bad_input`` non-zero exits."""
    if not key:
        raise WatermarkProbeError("key is required (the green-list hash seed)")
    if not (0.0 < gamma < 1.0):
        raise WatermarkProbeError(
            f"gamma must be in (0, 1) (got {gamma!r})"
        )
    if vocab_size <= 0:
        raise WatermarkProbeError(
            f"vocab must be non-empty (got vocab_size={vocab_size})"
        )
    if hash_scheme not in HASH_SCHEMES:
        raise WatermarkProbeError(
            f"unknown hash_scheme {hash_scheme!r} "
            f"(choices: {', '.join(HASH_SCHEMES)})"
        )
    if hash_scheme == "prefix-h" and prefix_h <= 0:
        raise WatermarkProbeError(
            f"prefix-h window must be >= 1 (got {prefix_h})"
        )
    if n_scored_tokens is not None and n_scored_tokens < MIN_SCORED_TOKENS:
        raise WatermarkProbeError(
            f"too few scored positions ({n_scored_tokens}); need at least "
            f"{MIN_SCORED_TOKENS} for any z to be meaningful"
        )
    if token_ids is not None:
        for i, tid in enumerate(token_ids):
            # bool is an int subclass; a token id is never a bool. Reject
            # non-ints and ids outside the declared vocabulary [0, V) before any
            # green list is computed — otherwise [-1, 999, True, 3] over V=4 would
            # silently score against an unrelated partition.
            if isinstance(tid, bool) or not isinstance(tid, int):
                raise WatermarkProbeError(
                    f"token_ids[{i}]={tid!r} must be an int token id in "
                    f"[0, {vocab_size}) (got {type(tid).__name__})"
                )
            if not (0 <= tid < vocab_size):
                raise WatermarkProbeError(
                    f"token_ids[{i}]={tid} is outside the vocabulary "
                    f"[0, {vocab_size})"
                )


# =====================================================================
# Tokenization input
# =====================================================================


def tokens_from_text_whitespace(
    text: str, vocab: dict[str, int],
) -> list[int]:
    """The LABELLED whitespace fallback (toy/demo/CI only). Tokenize with
    ``stylometry_core.word_tokens`` and map each word to its vocab id,
    dropping out-of-vocab words. Records ``whitespace_fallback`` in
    assumptions and emits a stderr under-detection warning at the call
    site. Using this against a real BPE watermark is guaranteed to
    under-detect (the tokenizer-mismatch hazard)."""
    return [vocab[w] for w in word_tokens(text) if w in vocab]


# =====================================================================
# Result
# =====================================================================


@dataclass(frozen=True)
class WatermarkProbeResult:
    z: float | None
    p_value: float | None
    neg_log10_p: float | None
    green_fraction: float | None
    gamma: float
    n_scored_tokens: int
    green_count: int
    band: str
    key_id: str
    hash_scheme: str
    assumptions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "z": self.z,
            "p_value": self.p_value,
            "neg_log10_p": self.neg_log10_p,
            "green_fraction": self.green_fraction,
            "gamma": self.gamma,
            "n_scored_tokens": self.n_scored_tokens,
            "green_count": self.green_count,
            "band": self.band,
            "key_id": self.key_id,
            "hash_scheme": self.hash_scheme,
            "assumptions": dict(self.assumptions),
        }

    def render(self) -> str:
        """Plain-text render. ALWAYS ends with the two load-bearing
        caveats; the low-z / under-powered line says ``unknown``, never
        "unwatermarked"."""
        lines: list[str] = []
        lines.append("# KGW green-list watermark probe")
        lines.append("")
        lines.append(f"- key_id: {self.key_id}  (the secret key is never shown)")
        lines.append(f"- hash_scheme: {self.hash_scheme}")
        lines.append(f"- partition: {self.assumptions.get('partition_prf', PARTITION_PRF)}"
                     "  (NOT the official KGW processor)")
        lines.append(f"- gamma: {self.gamma}")
        _raw = self.assumptions.get("n_positions")
        if _raw is not None and _raw != self.n_scored_tokens:
            lines.append(f"- scored tokens (effective T): {self.n_scored_tokens} "
                         f"(of {_raw} positions; repeated n-grams scored once)")
        else:
            lines.append(f"- scored tokens (T): {self.n_scored_tokens}")
        z_str = f"{self.z:.4f}" if self.z is not None else "(unavailable)"
        lines.append(f"- z: {z_str}")
        # p_value at full precision (finding 4) — NOT rounded to a floor.
        if self.p_value is not None:
            lines.append(f"- p_value: {self.p_value!r}")
            lines.append(f"- neg_log10_p: {self.neg_log10_p:.4f}")
        else:
            lines.append("- p_value: (unavailable)")
        gf = (
            f"{self.green_fraction:.4f}"
            if self.green_fraction is not None else "(unavailable)"
        )
        lines.append(f"- green_fraction: {gf}")
        lines.append(f"- band (PROVISIONAL, descriptive): {self.band}")
        lines.append("")
        if self.band == BAND_UNDER_POWERED:
            # The under-powered / low-z line: structurally `unknown`.
            lines.append(
                "No green-list signal for this key reaching the operator's "
                "margin — this is `unknown`, not 'no watermark' and not "
                "'human'."
            )
            lines.append("")
        # The two load-bearing caveats, ALWAYS.
        lines.append(
            f"A positive is consistent with the named scheme {self.key_id}, "
            "not 'AI'. A negative is not evidence of human authorship (this "
            "key sees one token-level scheme; other schemes, semantic / "
            "SynthID-class watermarks, and scrubbed watermarks are invisible "
            "to it)."
        )
        return "\n".join(lines)


# =====================================================================
# Claim license
# =====================================================================

DEFAULT_LICENSES = (
    "The green-list z-statistic and p-value of the target under the "
    "OPERATOR-SUPPLIED scheme parameters (key, gamma, hash_scheme, vocab) "
    "scored against THIS module's Voiceprint-defined green-list partition "
    f"({PARTITION_PRF}) — a SHA-256(key, context) seed feeding a deterministic "
    "shuffle, the KGW *construction* of Kirchenbauer et al. 2301.10226 but NOT "
    "the official reference processor's seeding scheme/RNG. Plus a non-secret "
    "key_id and a PROVISIONAL detection-power band — i.e. whether the "
    "green-token count is statistically improbable under the null for THIS "
    "named partition, with repeated n-grams scored once (the effective T and "
    "the raw position count are both reported). A positive is "
    "watermark-consistent with this partition; the band names evidence "
    "STRENGTH for it, not a class. The z / p_value / green_fraction are "
    "reported as raw values; no threshold is shipped."
)

DEFAULT_DOES_NOT_LICENSE = (
    # The verbatim no-threshold refusal (finding 3b). Names is_watermarked /
    # is_ai / is_human explicitly.
    "Do not threshold the band or p_value (or z, or green_fraction) to "
    "manufacture an is_watermarked / is_ai / is_human decision. This surface "
    "emits no such verdict field and the math does not entitle one. "
    # not AI / not authorship.
    "A positive is watermark-consistent with the named scheme, NOT "
    "'AI-generated' (it is relative to the supplied key; a human fed green-"
    "listed words, or a different model sharing the key, would also score "
    "high). "
    # absence is not evidence of human authorship.
    "Absence is not evidence of human authorship: a low/zero z, or no "
    "available key, is `unknown`, never `human` and never an unqualified "
    "`false`. "
    # tests only the supplied key / blind to other, scrubbed, semantic schemes.
    "It tests ONLY the operator-supplied key; it is blind to other watermark "
    "schemes, to semantic / SynthID-class watermarks (a different family with "
    "a different detector), and to any watermark scrubbed below the noise "
    "floor by paraphrase/rewrite — so a negative is never 'no watermark of "
    "any kind'. "
    # NOT the official KGW processor — partition-PRF scope (Codex P1).
    f"It scores against THIS module's green-list partition ({PARTITION_PRF}), "
    "which is NOT byte-compatible with the official KGW reference processor's "
    "seeding schemes (simple_1 / selfhash / minhash) or its RNG device: tokens "
    "generated by a different processor (including the official one) fall in an "
    "unrelated green list here and systematically FALSE-NEGATIVE. A positive is "
    "meaningful only when the operator generated with this exact partition; this "
    "surface does not claim to detect official-KGW or third-party watermarks "
    "absent a fixture proving the partition matches. "
    # whitespace-fallback under-detection.
    "The whitespace fallback tokenizer under-detects real BPE watermarks "
    "(the tokenizer-mismatch hazard); use it only for toy/demo/CI fixtures. "
    "Bands are operator-side / PROVISIONAL; this is one per-signal card "
    "routed to a human, never a selection / validation target or a combined "
    "AI-score input."
)


def build_claim_license(
    result: WatermarkProbeResult,
    *,
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
    n_schemes_tried: int | None = None,
) -> ClaimLicense:
    caveats = [DECAY_CAVEAT]
    if result.assumptions.get("tokenization") == "whitespace_fallback":
        caveats.append(WHITESPACE_FALLBACK_WARNING)
    if n_schemes_tried is not None and n_schemes_tried > 1:
        # M2 multiple-comparisons caveat.
        caveats.append(
            f"Multiple-comparisons caveat: this run tried {n_schemes_tried} "
            "schemes; trying many keys inflates the chance one fires "
            "spuriously. Keep the key set principled, not a brute-force sweep."
        )
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "key_id": result.key_id,
            "gamma": result.gamma,
            "hash_scheme": result.hash_scheme,
            "vocab_size": result.assumptions.get("vocab_size"),
            "tokenization": result.assumptions.get("tokenization"),
            "score_version": SCORE_VERSION,
            "threshold": None,
            "band_is_provisional": True,
        },
        additional_caveats=caveats,
        references=[
            "Kirchenbauer et al. 2024, 'A Watermark for Large Language "
            "Models' (arXiv:2301.10226) — the KGW green-list scheme + "
            "z-statistic detector this surface implements.",
            "Kirchenbauer et al. 2023, 'On the Reliability of Watermarks "
            "for Large Language Models' (arXiv:2306.04634) — the detection-"
            "power bands and the rewrite-decay caveat.",
            "Liang et al. 2024, 'Watermark under Fire (WaterPark)' "
            "(arXiv:2411.13425) — the catalog of scrubbing/spoofing blind "
            "spots a token-level z-test cannot see.",
        ],
    )


# =====================================================================
# Probe (M1 single-scheme) + envelope
# =====================================================================


def probe(
    token_ids: Sequence[int],
    *,
    key: str,
    vocab_size: int,
    gamma: float = DEFAULT_GAMMA,
    hash_scheme: str = DEFAULT_HASH_SCHEME,
    prefix_h: int = DEFAULT_PREFIX_H,
    tokenization: str = "operator_tokens",
    rewrite_exposure: str = "none",
    length_floor: int = DEFAULT_LENGTH_FLOOR,
    z_margin: float = DEFAULT_Z_MARGIN,
) -> WatermarkProbeResult:
    """Run the M1 single-scheme probe over a token-id stream. Validates
    params (raises ``WatermarkProbeError``), computes the z-test, maps the
    band, and returns a frozen ``WatermarkProbeResult``. No model, no I/O."""
    # Validate scheme params AND every token id BEFORE any statistic (Codex P2):
    # reject non-int/bool/out-of-range ids so a malformed stream can't produce a
    # spurious z instead of a bad_input.
    validate_params(
        key=key, gamma=gamma, vocab_size=vocab_size,
        hash_scheme=hash_scheme, prefix_h=prefix_h, token_ids=token_ids,
    )
    stats = green_z_test(
        token_ids, key=key, gamma=gamma, vocab_size=vocab_size,
        hash_scheme=hash_scheme, prefix_h=prefix_h,
    )
    # Then validate T (depends on the tokenization + scheme).
    validate_params(
        key=key, gamma=gamma, vocab_size=vocab_size,
        hash_scheme=hash_scheme, prefix_h=prefix_h,
        n_scored_tokens=stats["n_scored_tokens"],
    )
    band = reliability_band(
        stats["z"],
        n_scored_tokens=stats["n_scored_tokens"],
        rewrite_exposure=rewrite_exposure,
        length_floor=length_floor,
        z_margin=z_margin,
    )
    n_positions = stats.get("n_positions", stats["n_scored_tokens"])
    assumptions = {
        "tokenization": tokenization,
        "vocab_size": vocab_size,
        "hash_scheme": hash_scheme,
        "prefix_h": prefix_h if hash_scheme == "prefix-h" else None,
        "length_floor": length_floor,
        "z_margin": z_margin,
        "rewrite_exposure": rewrite_exposure,
        "band_is_provisional": True,
        "decay_caveat": DECAY_CAVEAT,
        # Partition-PRF identity (Codex P1): this detector scores against the
        # green list THIS module defines, not the official KGW reference processor.
        "partition_prf": PARTITION_PRF,
        # n-gram dedup transparency (Codex P1): effective T vs raw positions.
        "n_positions": n_positions,
        "n_repeated_ngrams_excluded": n_positions - stats["n_scored_tokens"],
        "ngram_dedup": "ignore_repeated_ngrams",
    }
    return WatermarkProbeResult(
        z=stats["z"],
        p_value=stats["p_value"],
        neg_log10_p=stats["neg_log10_p"],
        green_fraction=stats["green_fraction"],
        gamma=gamma,
        n_scored_tokens=stats["n_scored_tokens"],
        green_count=stats["green_count"],
        band=band,
        key_id=key_id(key),
        hash_scheme=hash_scheme,
        assumptions=assumptions,
    )


def compose_envelope(
    result: WatermarkProbeResult,
    *,
    target_path: Path | str | None,
    target_words: int,
    n_schemes_tried: int | None = None,
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
) -> dict[str, Any]:
    """Wrap a single result in the schema_version 1.0 envelope. The
    ``results`` dict carries the WatermarkProbeResult fields and NO
    verdict key; the claim license refuses the verdict + the
    absence-is-human reading."""
    lic = build_claim_license(
        result,
        licenses_text=licenses_text,
        does_not_license_text=does_not_license_text,
        n_schemes_tried=n_schemes_tried,
    )
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=result.to_dict(),
        claim_license=lic,
        available=True,
        warnings=[],
    )


# =====================================================================
# M2 — multi-key / multi-parameter sweep convenience (additive, no aggregate)
# =====================================================================


def sweep(
    token_ids: Sequence[int],
    catalog: Sequence[dict[str, Any]],
    *,
    tokenization: str = "operator_tokens",
    rewrite_exposure: str = "none",
) -> list[WatermarkProbeResult]:
    """Run the M1 probe across an operator-supplied catalog of candidate
    schemes. Each card is an independent ``WatermarkProbeResult`` (an M1
    result). The sweep adds NO cross-scheme aggregate, NO "best match"
    verdict, and NO boolean — it reports each scheme's (z, p, band)
    independently. ``assumptions.n_schemes_tried`` records the count; the
    render footer warns about multiple comparisons. This buys convenience,
    not power.

    Each catalog entry: ``{key, vocab_size, gamma?, hash_scheme?,
    prefix_h?}``.
    """
    n = len(catalog)
    results: list[WatermarkProbeResult] = []
    for entry in catalog:
        r = probe(
            token_ids,
            key=entry["key"],
            vocab_size=entry["vocab_size"],
            gamma=entry.get("gamma", DEFAULT_GAMMA),
            hash_scheme=entry.get("hash_scheme", DEFAULT_HASH_SCHEME),
            prefix_h=entry.get("prefix_h", DEFAULT_PREFIX_H),
            tokenization=tokenization,
            rewrite_exposure=rewrite_exposure,
        )
        # Stamp n_schemes_tried into each card's assumptions (additive).
        stamped = dict(r.assumptions)
        stamped["n_schemes_tried"] = n
        r = WatermarkProbeResult(
            z=r.z, p_value=r.p_value, neg_log10_p=r.neg_log10_p,
            green_fraction=r.green_fraction, gamma=r.gamma,
            n_scored_tokens=r.n_scored_tokens, green_count=r.green_count,
            band=r.band, key_id=r.key_id, hash_scheme=r.hash_scheme,
            assumptions=stamped,
        )
        results.append(r)
    return results


# =====================================================================
# CLI
# =====================================================================


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watermark_probe.py",
        description=(
            "KGW green-list z-test: was this watermarked with the "
            "operator-supplied scheme? Model-free. Emits z / p_value / "
            "green_fraction + a PROVISIONAL descriptive band; NO verdict. "
            "Absence of a signal is NOT evidence of human authorship."
        ),
    )
    p.add_argument("--target", help="Path to target text file (UTF-8).")
    p.add_argument(
        "--key", help="The green-list hash seed (required; never echoed).",
    )
    p.add_argument(
        "--vocab",
        help="Path to a JSON vocab: {token: id} map, or an int vocab size.",
    )
    p.add_argument(
        "--gamma", type=float, default=DEFAULT_GAMMA,
        help=f"Green fraction in (0,1) (default {DEFAULT_GAMMA}).",
    )
    p.add_argument(
        "--hash-scheme", choices=HASH_SCHEMES, default=DEFAULT_HASH_SCHEME,
        help=f"Context hash scheme (default {DEFAULT_HASH_SCHEME}).",
    )
    p.add_argument(
        "--prefix-h", type=int, default=DEFAULT_PREFIX_H,
        help=f"prefix-h window size (default {DEFAULT_PREFIX_H}).",
    )
    p.add_argument(
        "--tokens",
        help=(
            "Path to a JSON list of operator token ids (the REAL detection "
            "path — must match the watermarking tokenizer)."
        ),
    )
    p.add_argument(
        "--tokenizer",
        help=(
            "Tokenizer mode. Only 'fallback:whitespace' is supported — a "
            "LABELLED toy path that under-detects real BPE watermarks."
        ),
    )
    p.add_argument(
        "--rewrite-exposure", choices=REWRITE_EXPOSURES, default="none",
        help="Declared rewrite exposure (heavy forces under_powered).",
    )
    p.add_argument(
        "--catalog",
        help="M2 sweep: path to a JSON list of candidate scheme dicts.",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON envelope.")
    p.add_argument("--out", help="Write output to this path.")
    return p


def _resolve_vocab(vocab_arg: str) -> tuple[dict[str, int] | None, int]:
    """Return (vocab_map_or_None, vocab_size). ``--vocab`` may be a path to
    a {token: id} JSON map, or a path to a JSON int, or a bare int string."""
    # Bare integer?
    try:
        size = int(vocab_arg)
        return None, size
    except (TypeError, ValueError):
        pass
    loaded = _load_json(vocab_arg)
    if isinstance(loaded, int):
        return None, loaded
    if isinstance(loaded, dict):
        # Codex P2: do NOT trust len(map) as the vocab size — the ids must be a
        # dense, unique [0, V) domain, else `{a: 100, b: 200}` would compare ids
        # {100, 200} against green lists drawn over {0, 1}. Validate the domain.
        ids = list(loaded.values())
        for tok, tid in loaded.items():
            if isinstance(tid, bool) or not isinstance(tid, int):
                raise WatermarkProbeError(
                    f"--vocab id for {tok!r} must be an int (got "
                    f"{type(tid).__name__})"
                )
        size = len(loaded)
        if set(ids) != set(range(size)):
            raise WatermarkProbeError(
                "--vocab ids must be unique and dense over [0, len(vocab)) "
                f"(got {sorted(ids)[:8]}… for {size} tokens); a sparse or "
                "duplicated id domain makes the green-list partition meaningless"
            )
        return loaded, size
    raise WatermarkProbeError(
        "--vocab must be a {token: id} JSON map, a JSON int, or a bare int"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    def _err(reason: str) -> int:
        env = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=reason,
            reason_category="bad_input",
            target_path=args.target,
        )
        if args.json:
            print(json.dumps(env, indent=2, default=str))
        else:
            print(f"error: {reason}", file=sys.stderr)
        return 2

    if not args.target:
        return _err("--target is required")
    if not args.key:
        return _err("--key is required (the green-list hash seed)")
    if not args.vocab:
        return _err("--vocab is required")

    target_path = Path(args.target)
    if not target_path.is_file():
        return _err(f"target file not found at {target_path}")
    try:
        text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return _err(f"target not valid UTF-8: {exc}")
    target_words = len(word_tokens(text))

    try:
        vocab_map, vocab_size = _resolve_vocab(args.vocab)
    except (WatermarkProbeError, OSError, json.JSONDecodeError) as exc:
        return _err(f"could not load --vocab: {exc}")

    # Resolve the tokenization.
    tokenization = "operator_tokens"
    if args.tokens:
        try:
            token_ids = _load_json(args.tokens)
        except (OSError, json.JSONDecodeError) as exc:
            return _err(f"could not load --tokens: {exc}")
        if not isinstance(token_ids, list):
            return _err("--tokens must be a JSON list of integer token ids")
    elif args.tokenizer == "fallback:whitespace":
        if vocab_map is None:
            return _err(
                "--tokenizer fallback:whitespace needs a {token: id} --vocab "
                "map (not a bare size) to map words to ids"
            )
        token_ids = tokens_from_text_whitespace(text, vocab_map)
        tokenization = "whitespace_fallback"
        # The stderr under-detection warning (always, on this path).
        print(WHITESPACE_FALLBACK_WARNING, file=sys.stderr)
    else:
        return _err(
            "supply either --tokens TOKENS.json (real path) or "
            "--tokenizer fallback:whitespace (labelled toy path)"
        )

    # M2 sweep path.
    if args.catalog:
        try:
            catalog = _load_json(args.catalog)
        except (OSError, json.JSONDecodeError) as exc:
            return _err(f"could not load --catalog: {exc}")
        if not isinstance(catalog, list) or not catalog:
            return _err("--catalog must be a non-empty JSON list of schemes")
        try:
            cards = sweep(
                token_ids, catalog,
                tokenization=tokenization,
                rewrite_exposure=args.rewrite_exposure,
            )
        except WatermarkProbeError as exc:
            return _err(str(exc))
        # Each card is an independent envelope; NO cross-scheme aggregate.
        envelopes = [
            compose_envelope(
                c, target_path=target_path, target_words=target_words,
                n_schemes_tried=len(catalog),
            )
            for c in cards
        ]
        out = json.dumps({"schemes": envelopes}, indent=2, default=str)
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
            print(f"Wrote {args.out}", file=sys.stderr)
        else:
            print(out)
        return 0

    # M1 single-scheme path.
    try:
        result = probe(
            token_ids,
            key=args.key,
            vocab_size=vocab_size,
            gamma=args.gamma,
            hash_scheme=args.hash_scheme,
            prefix_h=args.prefix_h,
            tokenization=tokenization,
            rewrite_exposure=args.rewrite_exposure,
        )
    except WatermarkProbeError as exc:
        return _err(str(exc))

    envelope = compose_envelope(
        result, target_path=target_path, target_words=target_words,
    )

    if args.json:
        out = json.dumps(envelope, indent=2, default=str)
    else:
        out = result.render() + "\n\n" + envelope["claim_license_rendered"]
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
