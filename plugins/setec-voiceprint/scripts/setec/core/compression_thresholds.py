"""Compression-threshold metadata and POS-bigram distribution math.

Moved out of ``setec.surfaces.variance_audit`` (CB-5) so calibration and other
surfaces can read the threshold registry without importing the audit's text and
NLP stack. ``variance_audit`` re-exports every name here as the same object.
Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def normalize_pos_bigram_counts(
    counts: dict[str, int],
    keys: set[str] | None = None,
    *,
    alpha: float = 0.0,
) -> dict[str, float]:
    """Normalize POS-bigram counts to probabilities.

    Optional Laplace add-α smoothing over ``keys`` (if provided) or the
    distribution's own keys. ``alpha=0`` returns raw normalized
    frequencies; ``alpha=1.0`` matches ``pos_bigram_distance``'s
    Laplace smoothing convention. Returns an empty dict if the
    smoothed total is zero.
    """
    base = keys if keys is not None else set(counts.keys())
    if alpha > 0:
        smoothed: dict[str, float] = {k: counts.get(k, 0) + alpha for k in base}
    else:
        smoothed = {k: float(counts.get(k, 0)) for k in base}
    total = sum(smoothed.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in smoothed.items()}


def pos_bigram_kl_contributions(
    target_probs: dict[str, float],
    baseline_probs: dict[str, float],
    *,
    target_counts: dict[str, int] | None = None,
    baseline_counts: dict[str, int] | None = None,
    eps: float = 1e-9,
    min_count: int = 1,
) -> list[dict[str, Any]]:
    """Decompose POS-bigram KL into per-bigram contributions.

    For each bigram ``b`` in the union of seen bigrams (after the
    optional ``min_count`` filter), returns a row carrying the bigram
    string, raw counts (when supplied), smoothed probabilities, the
    delta and log2 ratio of those probabilities, and the per-bigram KL
    contribution ``p * log2(p / q)``. Rows are sorted by ``abs(kl_contrib)``
    descending so the largest-magnitude contributors appear first.

    Smoothing is applied as add-``eps`` to each probability followed by
    renormalization, so ``log2(p/q)`` is always defined. Pre-smooth at
    the count level via ``normalize_pos_bigram_counts(alpha=...)`` if
    Laplace add-α smoothing is preferred (the convention used by
    ``pos_bigram_distance``).

    ``min_count`` filters out bigrams where neither corpus reaches the
    count threshold. Suppresses sampling noise from rare bigrams. Has
    no effect if ``target_counts`` and ``baseline_counts`` are not
    supplied.
    """
    keys = set(target_probs) | set(baseline_probs)
    if min_count > 1 and target_counts is not None and baseline_counts is not None:
        keys = {
            k for k in keys
            if max(target_counts.get(k, 0), baseline_counts.get(k, 0)) >= min_count
        }
    if not keys:
        return []
    p_smoothed = {k: target_probs.get(k, 0.0) + eps for k in keys}
    q_smoothed = {k: baseline_probs.get(k, 0.0) + eps for k in keys}
    p_total = sum(p_smoothed.values())
    q_total = sum(q_smoothed.values())
    rows: list[dict[str, Any]] = []
    for k in keys:
        p = p_smoothed[k] / p_total
        q = q_smoothed[k] / q_total
        log2_ratio = math.log2(p / q)
        row: dict[str, Any] = {
            "bigram": k,
            "target_prob": p,
            "baseline_prob": q,
            "delta": p - q,
            "log2_ratio": log2_ratio,
            "kl_contrib": p * log2_ratio,
        }
        if target_counts is not None:
            row["target_count"] = target_counts.get(k, 0)
        if baseline_counts is not None:
            row["baseline_count"] = baseline_counts.get(k, 0)
        rows.append(row)
    rows.sort(key=lambda r: abs(r["kl_contrib"]), reverse=True)
    return rows


# Heuristic compression thresholds calibrated against fluent native-English prose.
# These are fallback heuristics for use without a baseline corpus; with a baseline,
# the z-score interpretation is more reliable. Each entry is a tuple of
# (threshold, direction, weight, length_floor) where:
#   - direction is "lt" (compressed when value < threshold) or "gt" (when value > threshold)
#   - weight is the contribution to band classification (signals differ in reliability)
#   - length_floor is the minimum word count for the heuristic to be reliable
#
# Each threshold is a ThresholdSpec dataclass. Calibration status is
# tracked via the `status` field (4-tier enum + 1 marginal), retiered
# in v1.66.0 per `internal/SPEC_calibration_status_retier.md` to
# replace the old binary `provisional: bool`. Provenance conventions
# per status:
#
#   * `calibrated`: provenance MUST cite a labeled corpus with
#     reported metrics (e.g., "raid_v1_fpr0.01_2026-05-13").
#   * `literature_anchored`: provenance MUST cite a publication
#     (e.g., "diveye_basani_chen_tmlr_2026").
#   * `empirically_oriented`: provenance MUST cite a local source
#     (e.g., "voice_profile_aggregation_v1").
#   * `heuristic`: provenance MUST be None. Default for new signals.
#   * `structural_only`: provenance describes downstream use.
#
# See `internal/SPEC_calibration_status_retier.md` for the full
# tier definitions and the per-signal triage. See
# `internal/SPEC_calibration_toolchain.md` for the calibration
# toolchain that promotes signals from heuristic / literature /
# empirical tiers into calibrated.

# Allowed status values. Order is loose-to-tight calibration tier.
THRESHOLD_STATUS_VALUES = frozenset({
    "calibrated",
    "literature_anchored",
    "empirically_oriented",
    "heuristic",
    "structural_only",
})


@dataclass
class ThresholdSpec:
    """Per-signal threshold specification + calibration metadata.

    `signal_path` is the dotted audit-output path the validation
    harness uses to extract scores (e.g., `tier1.sentence_length.
    burstiness_B`). `direction` is the polarity ("gt" = compressed
    when score > threshold; "lt" = compressed when score < threshold).
    `weight` and `length_floor` carry through from the original
    tuple-registry shape.

    `status` (v1.66.0): one of {calibrated, literature_anchored,
    empirically_oriented, heuristic, structural_only}. Replaces the
    old `provisional: bool` per the v1.66.0 retier. Each tier has
    its own provenance convention enforced in `__post_init__`.

    For backward compatibility: the `provisional` property returns
    True for any non-calibrated, non-structural_only status. Code
    that read the old `spec.provisional` keeps working with the
    same semantics (calibrated → False, everything else → True).
    """

    signal_path: str
    value: float
    direction: str
    weight: float
    length_floor: int
    provenance: str | None = None
    status: str = "heuristic"
    # 1.98.0+: per-comparator direction overrides. When set, callers
    # passing a ``comparator_class`` to ``resolve_direction(spec,
    # comparator_class)`` get the per-class direction; unknown
    # classes fall back to ``direction`` (the default).
    #
    # The empirical motivator: the 2026-05-18 RAID 5K bake-off
    # showed that the MAGE-correct directions (PR #99 / 1.95.1)
    # *do not generalise* to mixed-humans corpora. ``surprisal_sd``
    # in particular comes back as ``globally_inverted`` on RAID
    # under the MAGE direction — that signal needs ``"lt"`` on
    # RAID and ``"gt"`` on MAGE. Other RAID cells are
    # ``comparator_dependent`` at the (judge × generator) level —
    # finer-grained than ``comparator_class`` — and stay deferred
    # to a per-(signal × judge × generator) follow-up.
    direction_by_comparator: dict[str, str] | None = None
    # 1.100.0+: per-(comparator × judge × generator) direction
    # overrides. Three-level nested dict: outer keyed by
    # comparator class (e.g., "raid"), middle by LM-judge id
    # (e.g., "chatgpt"), inner by generator family (e.g.,
    # "gpt-4o-mini") → direction "gt" or "lt".
    #
    # The empirical motivator: the 2026-05-18 RAID 5K bake-off
    # surfaced 13 ``comparator_dependent`` cells (per polarity_-
    # audit verdict) where the direction holds for some
    # ``(LM-judge × generator-family)`` slices and inverts on
    # others within the same comparator class. PR #103's per-
    # comparator-class field was too coarse for those cells;
    # this field's three-level dict is the cell-resolution
    # closure.
    #
    # The fallback chain ``resolve_direction_with_slice`` walks
    # is:
    #   1. direction_by_comparator_and_slice[comp][judge][gen]
    #   2. direction_by_comparator[comp]   (PR #103, per-class)
    #   3. direction                        (spec default)
    #
    # 1.100.0 ships the field + helper + plumbing through
    # ``classify_compression``. The override TABLE stays empty
    # (no populated cells yet) -- the per-(judge × generator)
    # taxonomy still needs operator data to settle. Once
    # operator data lands, the 13 RAID ``comparator_dependent``
    # cells get populated here without further plumbing.
    direction_by_comparator_and_slice: (
        dict[str, dict[str, dict[str, str]]] | None
    ) = None

    def __post_init__(self) -> None:
        if self.direction not in ("gt", "lt"):
            raise ValueError(
                f"ThresholdSpec.direction must be 'gt' or 'lt', "
                f"got {self.direction!r}"
            )
        if self.direction_by_comparator is not None:
            for comparator, dir_val in self.direction_by_comparator.items():
                if dir_val not in ("gt", "lt"):
                    raise ValueError(
                        f"ThresholdSpec.direction_by_comparator"
                        f"[{comparator!r}] must be 'gt' or 'lt', "
                        f"got {dir_val!r}"
                    )
        if self.direction_by_comparator_and_slice is not None:
            # Validate every leaf direction in the nested dict.
            # Three levels: comparator → judge → generator → "gt"/"lt".
            # A typo at any level (e.g., a string value where a dict
            # is expected, or a leaf "xx" instead of "gt"/"lt") fails
            # fast at construction with the offending path named.
            for comp, by_judge in (
                self.direction_by_comparator_and_slice.items()
            ):
                if not isinstance(by_judge, dict):
                    raise ValueError(
                        f"ThresholdSpec.direction_by_comparator_and_slice"
                        f"[{comp!r}] must be a dict[str, dict[str, str]], "
                        f"got {type(by_judge).__name__}"
                    )
                for judge, by_gen in by_judge.items():
                    if not isinstance(by_gen, dict):
                        raise ValueError(
                            f"ThresholdSpec.direction_by_comparator_and_slice"
                            f"[{comp!r}][{judge!r}] must be a dict[str, str], "
                            f"got {type(by_gen).__name__}"
                        )
                    for generator, dir_val in by_gen.items():
                        if dir_val not in ("gt", "lt"):
                            raise ValueError(
                                f"ThresholdSpec.direction_by_comparator_"
                                f"and_slice[{comp!r}][{judge!r}]"
                                f"[{generator!r}] must be 'gt' or 'lt', "
                                f"got {dir_val!r}"
                            )
        if self.status not in THRESHOLD_STATUS_VALUES:
            raise ValueError(
                f"ThresholdSpec.status must be one of "
                f"{sorted(THRESHOLD_STATUS_VALUES)!r}, got "
                f"{self.status!r}"
            )
        # Per-tier provenance invariants.
        if self.status == "calibrated" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='calibrated' requires a "
                "provenance slug citing the labeled corpus + version "
                "+ reported metrics."
            )
        if self.status == "literature_anchored" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='literature_anchored' requires "
                "a provenance slug citing the publication."
            )
        if self.status == "empirically_oriented" and self.provenance is None:
            raise ValueError(
                "ThresholdSpec: status='empirically_oriented' requires "
                "a provenance slug citing the local source."
            )
        if self.status == "heuristic" and self.provenance is not None:
            raise ValueError(
                "ThresholdSpec: status='heuristic' must have "
                "provenance=None. Promote to one of "
                "{calibrated, literature_anchored, "
                "empirically_oriented} to attach provenance."
            )

    @property
    def provisional(self) -> bool:
        """Backward-compat alias: True for any non-calibrated,
        non-structural status. Existing code that filtered by
        `spec.provisional` keeps the same operational meaning
        (calibrated signals are False; everything else is True).

        Deprecated: prefer explicit per-tier checks via
        `status_signals()` helpers below.
        """
        return self.status not in ("calibrated", "structural_only")


def resolve_direction_with_slice(
    spec: ThresholdSpec,
    comparator_class: str | None = None,
    judge: str | None = None,
    generator: str | None = None,
) -> str:
    """Resolve a spec's direction against the full
    (comparator × judge × generator) routing chain (1.100.0+).

    Fallback chain (most-specific to least-specific):

      1. ``spec.direction_by_comparator_and_slice[comparator]
         [judge][generator]`` -- when all FIVE conditions hold
         (comparator_class set, judge set, generator set, the
         comparator-and-slice table set, and the full path present
         in the nested dict).
      2. ``spec.direction_by_comparator[comparator_class]`` --
         per-class override from PR #103 / 1.98.0. Used when the
         (judge, generator) lookup misses but the per-class entry
         hits.
      3. ``spec.direction`` -- the spec default.

    Pre-1.100 callers (passing only comparator_class via
    ``resolve_direction``) get exactly the PR #103 fallback chain
    -- the new helper is a strict superset that adds an outer
    layer above the per-comparator dict.

    The function is a pure read -- same inputs, same output, no
    state -- so it's safe to call from hot loops.
    """
    # Layer 1: per-(comparator × judge × generator) override.
    if (
        comparator_class is not None
        and judge is not None
        and generator is not None
        and spec.direction_by_comparator_and_slice is not None
    ):
        by_judge = spec.direction_by_comparator_and_slice.get(
            comparator_class,
        )
        if by_judge is not None:
            by_generator = by_judge.get(judge)
            if by_generator is not None:
                resolved = by_generator.get(generator)
                if resolved is not None:
                    return resolved
    # Layer 2: per-comparator-class override (PR #103 fallback).
    if (
        comparator_class is not None
        and spec.direction_by_comparator is not None
    ):
        resolved = spec.direction_by_comparator.get(comparator_class)
        if resolved is not None:
            return resolved
    # Layer 3: spec default.
    return spec.direction


def resolve_direction(
    spec: ThresholdSpec,
    comparator_class: str | None = None,
) -> str:
    """Per-comparator-class direction resolver (PR #103 / 1.98.0).

    Thin wrapper over ``resolve_direction_with_slice`` for callers
    that don't yet supply judge / generator. Preserves the exact
    pre-1.100 fallback chain bit-for-bit so pre-1.100 callers that
    don't pass judge / generator keep getting the same answer.

    For new callers wanting the full (comparator × judge ×
    generator) chain, use ``resolve_direction_with_slice``
    directly.
    """
    return resolve_direction_with_slice(
        spec, comparator_class, judge=None, generator=None,
    )


COMPRESSION_HEURISTICS: dict[str, ThresholdSpec] = {
    # Burstiness magnitude is the most reliable single signal.
    # Heuristic note: literature suggests B < -0.2 is compressed, but real
    # human essayistic prose with long sentences can reach B = -0.4 naturally
    # (verified on pre-AI testimony at B = -0.40). Threshold tightened to -0.4
    # so the heuristic catches the genuine AI mode-collapse case (B < -0.4
    # in the smoke-test AI passage) while sparing essayistic human registers.
    #
    # Provisional under the "Stylometry to the people" policy (2026-05-11).
    # An EditLens-anchored calibration was run on 2026-05-10 (val split,
    # 1506 student essays, 753 AI / 753 ESL human; derived threshold
    # -0.6227 at FPR 0.93% / TPR 7.0%, da_AUC 0.683). That calibration
    # is preserved as an audit record in
    # `scripts/calibration/thresholds_calibrated.json` and as a
    # PROVENANCE entry, but is NOT loaded as the framework's threshold.
    # The framework's posture is that anchored thresholds derived from
    # one labeled corpus (EditLens, RAID, MAGE) do not generalize to the
    # user's register mix without local recalibration. Users wanting a
    # corpus-anchored threshold should run `calibrate_thresholds.py`
    # against their own labeled baseline; the PROVENANCE pattern is the
    # methodology, not a registry of authoritative numbers. See
    # `scripts/calibration/PROVENANCE.md` for the policy statement.
    "burstiness_B": ThresholdSpec(
        signal_path="tier1.sentence_length.burstiness_B",
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        value=-0.40, direction="lt",
        weight=2.0, length_floor=200,
    ),
    # Connective density: AI-prose runs 25-50 per 1000 tokens; humans 5-15.
    # Local da_AUC measurement on EditLens v1 (2026-05-10 findings):
    # 0.529 — the weakest of the six variance signals measured but
    # the band shape is consistent with literature.
    "connective_density": ThresholdSpec(
        signal_path="tier1.connective_density.per_1000_tokens",
        value=20.0, direction="gt", weight=2.0, length_floor=200,
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
    ),
    # MATTR: literary fluent fiction runs 0.70-0.82 at window 50.
    "mattr": ThresholdSpec(
        signal_path="tier1.mattr.value",
        value=0.65, direction="lt", weight=1.0, length_floor=300,
        status="literature_anchored",
        provenance="mattr_literary_fiction_baseline_window_50",
    ),
    # MTLD: noisy below ~500 words; threshold tightened.
    "mtld": ThresholdSpec(
        signal_path="tier1.mtld",
        value=60.0, direction="lt", weight=1.0, length_floor=500,
    ),
    # Yule's K: concentration on frequent types.
    "yules_k": ThresholdSpec(
        signal_path="tier1.yules_k",
        value=200.0, direction="gt", weight=1.0, length_floor=500,
    ),
    # Shannon entropy: the literature reports 9.5-10.5 bits/token for native
    # fiction, but this depends heavily on vocabulary scope and register.
    # Empirical testing on pre-AI human prose across registers found values
    # 8.0-9.6, so the threshold has been removed (set very low) to avoid
    # firing false positives on writers whose vocabulary is naturally focused.
    # Use a personal baseline for entropy comparison instead of the heuristic.
    "shannon_entropy": ThresholdSpec(
        signal_path="tier1.shannon_entropy_bits",
        value=7.0, direction="lt", weight=1.0, length_floor=2000,
        status="literature_anchored",
        provenance="shannon_entropy_native_fiction_literature",
    ),
    # FKGL SD: human prose typically 3-5 across sentences; LLM 0.8-1.5.
    # Local da_AUC measurement on EditLens v1 (2026-05-10 findings):
    # 0.635. Tier note: literature anchor + local measurement; the
    # local da_AUC promotes this from literature_anchored.
    "fkgl_sd": ThresholdSpec(
        signal_path="tier1.fkgl.sd",
        value=1.5, direction="lt", weight=1.5, length_floor=200,
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
    ),
    # Sentence-length SD is unreliable as a standalone signal because mean
    # sentence length varies dramatically by register (fiction with fragments
    # has SD 6-9; essay has SD 15-20). Burstiness B normalizes for mean and
    # carries this signal more reliably. Threshold raised so it almost never
    # fires; rely on B and on personal baseline z-scores instead.
    "sentence_length_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier1.sentence_length.sd",
        value=5.0, direction="lt", weight=0.5, length_floor=5000,
    ),
    # Adjacent-sentence cosine: pre-1.95 direction was ``gt`` (tight
    # cohesion = AI tell). The 2026-05-18 MAGE 5K polarity audit
    # (PR #92 + corrected reading per the 1.92.0 direction-aware fix)
    # found this was empirically inverted: all four Phase A
    # embeddings (mxbai, minilm, harrier, gemma) produced raw AUC
    # 0.41-0.45 — i.e., AI prose has LOWER adjacent-sentence cosine
    # against the MAGE curated-human comparator. Direction flipped to
    # ``lt`` to match the empirical sign. See
    # internal/polarity_audit_results/polarity_audit.md for the
    # per-(model, signal) verdicts that motivated the flip.
    "adjacent_cosine_mean": ThresholdSpec(
        signal_path="tier3.adjacent_cosine.mean",
        value=0.60, direction="lt", weight=1.5, length_floor=200,
        status="empirically_oriented",
        provenance="mage_5k_polarity_audit_2026-05-18",
        # Polarity corpus-unstable (ROADMAP cross-corpus inversion):
        # standard theory says smoothed = HIGH cohesion ("gt"), but the
        # MAGE audit oriented it "lt". Rather than assert a contested
        # direction with no comparator, this signal is GATED OUT of the
        # unbaselined band (see ``_POLARITY_UNBASELINED_GATE``). The "lt"
        # default still applies when a comparator_class is supplied.
    ),
    "adjacent_cosine_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier3.adjacent_cosine.sd",
        value=0.12, direction="lt", weight=1.0, length_floor=300,
    ),
    # MDD-SD: compressed syntactic variation.
    "mdd_sd": ThresholdSpec(
        status="empirically_oriented",
        provenance="editlens_v1_findings_2026-05-10",
        signal_path="tier2.mdd.sd",
        value=0.7, direction="lt", weight=1.0, length_floor=300,
    ),
    # ---- Tier 4 (surprisal), C.4, v1.47.0+ ----
    # PROVISIONAL per SPEC_surprisal_signal.md §3.5 and §4.3: the
    # three Tier 4 thresholds ship with provisional=True. Values
    # come from fixture-derived heuristics (the same numbers used
    # in surprisal_audit.py's PROVISIONAL_BAND_THRESHOLDS), NOT
    # from a labeled-corpus calibration. The framework's posture
    # is that load-bearing surprisal thresholds belong to the
    # operator's calibration; these provisional values exist so
    # the band classifier has something to chew on when Tier 4 is
    # enabled but never get treated as authoritative. The
    # ClaimLicense block downstream surfaces
    # calibration_anchor: user-baseline-required.
    #
    # Direction semantics (compression-polarity fix, 2026-06-02; see the
    # bug report SETEC_compression_polarity).
    #
    # The DEFAULT direction is now the SMOOTHING direction, shared with
    # surprisal_audit.py's standalone band via the canonical
    # ``surprisal_backend.SMOOTHED_DIRECTION`` (mean lt, sd lt, acf gt):
    # smoothed prose has LOWER surprisal mean/SD and HIGHER lag-1 ACF.
    # This is what an UNBASELINED run (no comparator_class) must assert,
    # and it makes this integrator AGREE with the standalone band on the
    # same numbers. Previously the defaults were the MAGE directions,
    # which made the integrator flag high-variance / anti-smoothed prose
    # as "smoothed" while the standalone band correctly called it typical.
    #
    # The 2026-05-18 MAGE 5K polarity audit found the OPPOSITE directions
    # discriminate AI from the *curated-human* comparator (AI prose runs
    # HIGHER surprisal mean/SD and LOWER ACF than curated humans). That is
    # an AI-DETECTION polarity against one comparator class, NOT the
    # smoothing default — so it is preserved as a per-comparator override
    # keyed "mage", applied only when a caller passes
    # comparator_class="mage". RAID (mixed-humans) keeps "lt" for
    # surprisal_sd, which now equals the smoothing default. Only the
    # ROUTING changed (default vs per-comparator); the MAGE/RAID empirical
    # findings (all 6 Phase B models) are unchanged.
    "surprisal_mean": ThresholdSpec(
        signal_path="tier4.surprisal.mean",
        value=3.5, direction="lt", weight=1.5, length_floor=300,
        status="empirically_oriented",
        provenance="mage_5k_polarity_audit_2026-05-18",
        direction_by_comparator={"mage": "gt"},
    ),
    "surprisal_sd": ThresholdSpec(
        signal_path="tier4.surprisal.sd",
        value=1.5, direction="lt", weight=2.0, length_floor=300,
        status="empirically_oriented",
        provenance="mage_5k_polarity_audit_2026-05-18",
        # Smoothing default "lt" (smoothed = LOW SD) == surprisal_audit's
        # band and SMOOTHED_DIRECTION. MAGE curated-human AI-detection
        # direction ("gt": AI > human SD) and RAID mixed-humans ("lt",
        # == the new default) preserved as per-comparator overrides.
        direction_by_comparator={"mage": "gt", "raid": "lt"},
    ),
    "surprisal_acf_lag1": ThresholdSpec(
        signal_path="tier4.surprisal.autocorrelation.lag_1",
        value=0.30, direction="gt", weight=1.0, length_floor=500,
        status="empirically_oriented",
        provenance="mage_5k_polarity_audit_2026-05-18",
        # Smoothing default "gt" (smoothed = HIGH lag-1 ACF) ==
        # surprisal_audit's smoothed_above and SMOOTHED_DIRECTION. MAGE
        # AI-detection direction ("lt": AI < human ACF) as override.
        direction_by_comparator={"mage": "lt"},
    ),
    # AIC-7 named-pattern density (v1.65.0). Per
    # `internal/SPEC_aic_8_9_implementation.md` Step 10 part 2.
    # Backfilled when `--aic7` integration landed; emits at
    # `patterns.<key>.density_per_1k` via `_aic7_named_pattern_block`.
    #
    # The four spec-named patterns get registry entries. Other
    # patterns the detector emits (negation_hedge, pseudo_aphorism,
    # false_balance, hedge_and_affirm, recommendation_template,
    # authority_laundering) are visible in the audit output but
    # not in the band-classifier loop; they remain diagnostic-only
    # until empirical work pins thresholds.
    #
    # Thresholds are PROVISIONAL placeholders. The Schnell case
    # study (Glass-Box Stylometry Sequence Post 2) anchors
    # correctio at 15.8 / 1000 words but that's the Schnell-author
    # baseline, not a smoothing-detection threshold. Calibration
    # against a labeled corpus replaces these.
    "correctio_density": ThresholdSpec(
        signal_path="patterns.correctio.density_per_1k",
        value=12.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "triplet_density": ThresholdSpec(
        signal_path="patterns.triplet.density_per_1k",
        value=8.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "manifesto_cadence_density": ThresholdSpec(
        signal_path="patterns.manifesto_cadence.density_per_1k",
        value=3.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "professional_parallel_stack_density": ThresholdSpec(
        signal_path="patterns.professional_parallel_stack.density_per_1k",
        value=2.0, direction="gt", weight=1.0, length_floor=400,
    ),
    # AIC-9 closure inflation (v1.65.0). Per spec Step 10 part 1.
    # Emits at `aic_8_9.kicker_density.value` via
    # `_aic9_kicker_block`. Threshold 0.25 (25% of paragraphs)
    # starts well above register-typical contemporary essay
    # (~0.08) so the default catches the elevation pattern, not
    # the genre-typical aphoristic essay.
    "kicker_density": ThresholdSpec(
        signal_path="aic_8_9.kicker_density.value",
        value=0.25, direction="gt", weight=1.0, length_floor=400,
    ),
    # AIC-8 aesthetic authority laundering (v1.65.0). Per spec
    # Step 10 part 1. Two signals emitted at
    # `aic_8_9.image_conjunction_density.value` and
    # `aic_8_9.prestige_metaphor_density.domain_scatter_entropy`
    # via `_aic8_image_prestige_block`. PROVISIONAL thresholds;
    # spec's T1/T2/T3 starting values don't crisply separate
    # idioms from AI positives on Brysbaert data (documented
    # finding in 1.61.0 CHANGELOG + ROADMAP).
    "image_conjunction_density": ThresholdSpec(
        signal_path="aic_8_9.image_conjunction_density.value",
        value=15.0, direction="gt", weight=1.0, length_floor=400,
    ),
    "prestige_metaphor_scatter": ThresholdSpec(
        signal_path="aic_8_9.prestige_metaphor_density.domain_scatter_entropy",
        value=0.7, direction="gt", weight=1.0, length_floor=400,
    ),
}


def signals_by_status(
    status: str,
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return signal keys whose `status` matches the given tier.

    Tiers (v1.66.0): "calibrated", "literature_anchored",
    "empirically_oriented", "heuristic", "structural_only".
    """
    if status not in THRESHOLD_STATUS_VALUES:
        raise ValueError(
            f"Unknown status {status!r}; expected one of "
            f"{sorted(THRESHOLD_STATUS_VALUES)!r}"
        )
    return [k for k, spec in heuristics.items() if spec.status == status]


def calibrated_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "calibrated"`.

    A `calibrated` signal carries provenance citing a labeled corpus
    with reported metrics (FPR / TPR / AUC / distribution moments).
    The Stylometry-to-the-people invariant: until the §5.4
    calibration corpus runs, this list is expected to be empty.
    """
    return signals_by_status("calibrated", heuristics)


def literature_anchored_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "literature_anchored"`.

    A `literature_anchored` signal carries provenance citing a
    publication (e.g., `diveye_basani_chen_tmlr_2026`). The bands
    are taken from published work but not corpus-validated locally.
    """
    return signals_by_status("literature_anchored", heuristics)


def empirically_oriented_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "empirically_oriented"`.

    An `empirically_oriented` signal carries provenance citing a
    local source (`voice_profile_aggregation_v1`, internal fixture
    testing). The bands come from local experimentation, not a
    published study.
    """
    return signals_by_status("empirically_oriented", heuristics)


def heuristic_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals with `status == "heuristic"`.

    A `heuristic` signal carries no provenance. The threshold is a
    plausible working value awaiting validation. The §5.4
    calibration corpus track is the named promotion path:
    `heuristic` → `calibrated` as corpus data lands.
    """
    return signals_by_status("heuristic", heuristics)


def provisional_signals(
    heuristics: dict[str, ThresholdSpec] = COMPRESSION_HEURISTICS,
) -> list[str]:
    """Return keys of signals whose status is not `calibrated` and
    not `structural_only` — i.e., everything else (`heuristic`,
    `literature_anchored`, `empirically_oriented`).

    Backward-compat helper (v1.66.0 retier kept the name but
    redefined the semantics around the new four-tier enum). Prefer
    the explicit per-tier helpers (`heuristic_signals`,
    `literature_anchored_signals`, `empirically_oriented_signals`)
    for new code. Existing callers that just wanted "not calibrated"
    keep working with the same operational meaning.
    """
    return [k for k, spec in heuristics.items() if spec.provisional]


# POS-bigram KL divergence against a baseline aggregate. Unlike the 11
# heuristics above, this signal is baseline-relative and only
# participates in the band classification when a baseline is supplied
# (and POS-bigram counts are available, which requires Tier 2 / spaCy).
#
# Empirical motivation: on AI-composed prose where every variance
# metric (burstiness, MATTR, MTLD, Yule's K, Shannon entropy, FKGL SD,
# MDD SD, function-word ratio, sentence-length SD) reads inside human
# bounds against the writer's own pre-AI baseline, POS-bigram KL has
# elevated as the single signal carrying the syntactic-template-collapse
# evidence. The 2026 multi-model collaborative regime (notes -> AI ->
# comments -> AI fix) preserves surface variance because human editing
# reintroduces it; what remains different from the writer's idiolect is
# the syntactic palette the models draw from. KL catches that.
#
# Threshold 0.15 from the literature anchor in
# references/distributional-diagnostics.md ("KL > 0.15 against a
# register-matched human baseline is a meaningful syntactic-template-
# collapse signal"). Cross-human KL on matched genres typically below
# 0.05; human-vs-LLM KL typically 0.10-0.30.
#
# Weight 2.0 matches burstiness_B and connective_density (the two
# highest-weighted variance signals). This is a starting calibration;
# pending recalibration against the validation harness on a labeled
# corpus, where the empirical ROC will tell us whether KL deserves a
# higher weight than the variance signals on this generation of AI
# assistance.
POS_BIGRAM_KL_HEURISTIC: ThresholdSpec = ThresholdSpec(
    signal_path="baseline_divergences.pos_bigrams.kl",
    value=0.15, direction="gt", weight=2.0, length_floor=500,
    status="literature_anchored",
    provenance="pos_bigram_kl_distributional_diagnostics",
)
