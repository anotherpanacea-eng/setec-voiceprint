#!/usr/bin/env python3
"""calibration_survey.py — survey every COMPRESSION_HEURISTICS signal.

Runs `calibrate_thresholds.derive_threshold` against every key in the
registry under one labeled corpus + one FPR target, then aggregates
the results into a single comparison table the maintainer reads to
pick the **first** signal whose calibration entry passes the five
selection criteria documented in
``scripts/calibration/PROVENANCE.md``.

Pre-1.23.0, the documented workflow asked the maintainer to run
`calibrate_thresholds.py` once per signal in a shell loop and read
the JSON output by hand. Two friction points the wrapper closes:

  * **Coverage drift.** The PROVENANCE.md shell loop enumerated 7
    of the 11 signals; ``yules_k``, ``shannon_entropy``,
    ``sentence_length_sd``, and ``mdd_sd`` were silently missing.
    This wrapper iterates the registry directly so coverage is
    always 11/11.
  * **Comparison cost.** Picking which signal earns the first
    committed threshold means weighing AUC, TPR-at-target-FPR,
    threshold interpretability, n_neg sufficiency, and ESL
    conservatism across all candidates. Reading 11 separate JSON
    files to make that judgment is enough friction that the
    workflow stalled. The wrapper produces one markdown table plus
    one JSON survey ledger — judgment becomes one read, not eleven.

What the wrapper does NOT do: pick the winning signal. The five
selection criteria explicitly include "AUC / AP not embarrassing
(no fixed cutoff baked into the toolchain — left to maintainer
judgment per signal)" and the ESL conservatism gate is context-
dependent. The wrapper marks the automatable gates (polarity, FPR
resolution, TPR-above-floor, calibrated-vs-heuristic
aggressiveness) and leaves the judgment to the maintainer.

Usage:

    # Survey all 11 signals at FPR 0.01:
    python3 scripts/calibration/calibration_survey.py \\
        --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \\
        --fpr-target 0.01 \\
        --out /tmp/calibration_survey_2026-05-09.json

    # Only the cheap stylometric signals (skip Tier 2 spaCy + Tier 3 cohesion):
    python3 scripts/calibration/calibration_survey.py \\
        --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \\
        --fpr-target 0.01 \\
        --no-tier2 --no-tier3 \\
        --out /tmp/survey_tier1.json

    # JSON-only output (no markdown table on stdout):
    python3 scripts/calibration/calibration_survey.py \\
        --manifest ... --fpr-target 0.01 --json-only

The output ledger's ``rows[*].gates`` block records which
selection-criteria gates each signal passes. The maintainer reads
this, picks the winner, and follows the existing 5-step commit
sequence in PROVENANCE.md (edit registry, add markdown section,
append to ledger, bump version).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import calibrate_thresholds as ct  # noqa: E402

# Reach the variance audit registry without triggering a heavy
# spaCy import: COMPRESSION_HEURISTICS is a pure dataclass dict.
PARENT_SCRIPTS = SCRIPT_DIR.parent
if str(PARENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PARENT_SCRIPTS))
from variance_audit import COMPRESSION_HEURISTICS  # type: ignore  # noqa: E402

TASK_SURFACE = "smoothing_diagnosis_calibration"
TOOL_NAME = "calibration_survey"
SCRIPT_VERSION = "1.0"


# ---- Selection-criteria gates ------------------------------------


# TPR floor below which a calibrated threshold is "predicts almost
# nothing." Per PROVENANCE.md gate 4: a threshold that fires on
# 1/130 positives is technically valid but operationally
# meaningless. The 5% floor is intentionally permissive — the
# maintainer can lower it via --tpr-floor if they're calibrating
# against a corpus where the AI-prose signal is genuinely rare.
DEFAULT_TPR_FLOOR = 0.05

# Tolerance for the "calibrated threshold not more aggressive than
# heuristic" gate. Per PROVENANCE.md gate 5: when calibrating
# against ESL, the calibrated threshold should NOT be more
# aggressive than the heuristic (which would mean the calibration
# wants to flag MORE ESL essays as compressed). A small relaxation
# (default 5% of the heuristic value) catches cases where the
# direction-of-aggressiveness flipped without complaining about
# noise-level disagreement.
DEFAULT_AGGRESSIVENESS_TOLERANCE = 0.05


@dataclass
class GateResults:
    """Five gates from PROVENANCE.md "Selection criteria" section.

    Each gate is True / False / None. None = not evaluable in this
    survey (e.g. gate 2 is maintainer judgment, gate 5 requires the
    ESL slice, etc.). The maintainer reads the booleans + the
    accompanying numerics and decides.
    """
    polarity_matches: bool | None = None      # gate 1
    auc_ap_not_embarrassing: bool | None = None  # gate 2 (judgment)
    enough_negatives: bool | None = None       # gate 3
    interpretable_threshold: bool | None = None  # gate 4
    esl_conservative: bool | None = None       # gate 5

    @property
    def all_pass(self) -> bool:
        """All evaluable gates pass. None values count as 'unknown';
        a row with any None is not all-pass."""
        return all(
            g is True for g in (
                self.polarity_matches,
                self.auc_ap_not_embarrassing,
                self.enough_negatives,
                self.interpretable_threshold,
                self.esl_conservative,
            )
        )

    @property
    def n_passes(self) -> int:
        """Count of gates that explicitly pass."""
        return sum(
            1 for g in (
                self.polarity_matches,
                self.auc_ap_not_embarrassing,
                self.enough_negatives,
                self.interpretable_threshold,
                self.esl_conservative,
            )
            if g is True
        )

    @property
    def n_evaluated(self) -> int:
        """Count of gates we evaluated (not None)."""
        return sum(
            1 for g in (
                self.polarity_matches,
                self.auc_ap_not_embarrassing,
                self.enough_negatives,
                self.interpretable_threshold,
                self.esl_conservative,
            )
            if g is not None
        )


def evaluate_gates(
    entry: dict[str, Any],
    *,
    heuristic_value: float | None,
    direction: str,
    tpr_floor: float,
    aggressiveness_tolerance: float,
) -> GateResults:
    """Map a derive_threshold provenance entry → gate booleans.

    Gates 1, 3, 4 are automatable (polarity, FPR resolution, TPR
    floor). Gate 5 is automatable when a heuristic value is
    available (the registry always has one). Gate 2 stays None —
    maintainer judgment.
    """
    g = GateResults()
    # Real provenance entries from `derive_threshold_from_records`
    # nest the metrics under `calibration` and put the threshold at
    # `derived_value`. Test fixtures using `empirical` + `sweep` keys
    # are also accepted as a fallback for back-compat with synthetic
    # test data. Real-data path is preferred.
    cal = entry.get("calibration") or entry.get("empirical") or {}
    sweep = entry.get("sweep") or {}

    # Gate 1: polarity (DIRECTION-AWARE).
    #
    # The calibrator computes AUC via raw `roc_auc_score(labels,
    # scores)` — direction-blind. AUC > 0.5 means positives have
    # higher scores than negatives; AUC < 0.5 means the opposite.
    #
    # The registry's `direction` declares the smoothing-diagnosis
    # hypothesis: `gt` = compressed when value HIGH (so AI > human);
    # `lt` = compressed when value LOW (so AI < human). For polarity
    # to *match* the hypothesis:
    #   - direction='gt': raw AUC > 0.5 (AI has higher scores)
    #   - direction='lt': raw AUC < 0.5 (AI has lower scores)
    #
    # Pre-1.26.1 this gate read AUC ≥ 0.5 for both directions, which
    # silently passed `lt` signals whose corpus actually inverted the
    # registry's hypothesis. The maintainer's first real calibration
    # run on EditLens val caught this — `mtld` showed raw AUC 0.87 in
    # `lt` direction, suggesting strong discrimination, but threshold
    # sweeps at the registry's direction returned TPR ≈ 0 because AI
    # essays were in fact HIGHER on mtld than human ESL essays. Real
    # finding, surfaced once the gate read direction-aware.
    auc = cal.get("auc")
    if isinstance(auc, (int, float)):
        if direction == "gt":
            g.polarity_matches = float(auc) >= 0.5
        elif direction == "lt":
            g.polarity_matches = float(auc) <= 0.5
        else:
            g.polarity_matches = None
    else:
        g.polarity_matches = None

    # Gate 3: enough negatives for the requested FPR. The toolchain's
    # `fpr_resolution = 1 / n_neg` check is structural; pass-fail is
    # whether fpr_resolution ≤ fpr_target.
    fpr_target = (
        entry.get("fpr_target")
        or cal.get("fpr_target")
    )
    fpr_resolution = (
        cal.get("fpr_resolution")
        or sweep.get("fpr_resolution")
    )
    if isinstance(fpr_resolution, (int, float)) and isinstance(fpr_target, (int, float)):
        g.enough_negatives = float(fpr_resolution) <= float(fpr_target)

    # Gate 4: interpretable threshold (TPR substantially above zero
    # at the chosen FPR target). Real entries name it
    # ``empirical_tpr``; synthetic fixtures used ``tpr_at_threshold``.
    tpr = cal.get("empirical_tpr")
    if tpr is None:
        tpr = cal.get("tpr_at_threshold")
    if isinstance(tpr, (int, float)):
        g.interpretable_threshold = float(tpr) >= tpr_floor

    # Gate 5: calibrated NOT more aggressive than heuristic.
    # "More aggressive" = flags MORE positives. For a `gt` signal
    # (compressed when value high), more aggressive = lower threshold.
    # For a `lt` signal (compressed when value low), more aggressive =
    # higher threshold.
    threshold = entry.get("derived_value") or sweep.get("threshold")
    if (
        isinstance(threshold, (int, float))
        and isinstance(heuristic_value, (int, float))
    ):
        diff = float(threshold) - float(heuristic_value)
        rel = abs(diff) / max(abs(float(heuristic_value)), 1e-9)
        if rel <= aggressiveness_tolerance:
            # Within tolerance — count as conservative.
            g.esl_conservative = True
        elif direction == "gt":
            # gt signal: calibrated threshold lower than heuristic =
            # more aggressive (flags more). Conservative = ≥ heuristic.
            g.esl_conservative = float(threshold) >= float(heuristic_value)
        elif direction == "lt":
            # lt signal: calibrated threshold higher than heuristic =
            # more aggressive. Conservative = ≤ heuristic.
            g.esl_conservative = float(threshold) <= float(heuristic_value)
        else:
            g.esl_conservative = None

    # Gate 2 stays None — judgment. We surface AUC + AP for the
    # maintainer to weigh.
    return g


# ---- Survey runner -----------------------------------------------


@dataclass
class SurveyRow:
    """One signal's row in the comparison table.

    Mirrors the columns the maintainer needs to make a pick:
    signal name + direction + AUC / AP for ranking sense + threshold
    + TPR-at-threshold + FPR-at-threshold + n_neg + fpr_resolution
    + the gate booleans.

    ``direction_aware_auc`` is the direction-flipped AUC: for `gt`
    direction it's the raw AUC; for `lt` direction it's ``1 - raw
    AUC``. ``da_auc ≥ 0.5`` ↔ polarity matches the registry's
    hypothesis. The maintainer reads this column to compare
    discrimination strength across signals on a consistent scale —
    raw AUC alone is misleading for `lt` signals because high values
    can indicate either matching polarity (good) or inverted polarity
    (bad), and you can't tell from the number alone.
    """
    signal: str
    direction: str
    heuristic_value: float | None
    auc: float | None = None
    direction_aware_auc: float | None = None
    ap: float | None = None
    direction_aware_ap: float | None = None
    threshold: float | None = None
    tpr_at_threshold: float | None = None
    fpr_at_threshold: float | None = None
    n_pos: int | None = None
    n_neg: int | None = None
    fpr_resolution: float | None = None
    gates: GateResults = field(default_factory=GateResults)
    error: str | None = None  # populated if derive_threshold raised
    full_entry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "direction": self.direction,
            "heuristic_value": self.heuristic_value,
            "auc": self.auc,
            "direction_aware_auc": self.direction_aware_auc,
            "ap": self.ap,
            "direction_aware_ap": self.direction_aware_ap,
            "threshold": self.threshold,
            "tpr_at_threshold": self.tpr_at_threshold,
            "fpr_at_threshold": self.fpr_at_threshold,
            "n_pos": self.n_pos,
            "n_neg": self.n_neg,
            "fpr_resolution": self.fpr_resolution,
            "gates": {
                "polarity_matches": self.gates.polarity_matches,
                "auc_ap_not_embarrassing": self.gates.auc_ap_not_embarrassing,
                "enough_negatives": self.gates.enough_negatives,
                "interpretable_threshold": self.gates.interpretable_threshold,
                "esl_conservative": self.gates.esl_conservative,
                "n_passes": self.gates.n_passes,
                "n_evaluated": self.gates.n_evaluated,
                "all_pass": self.gates.all_pass,
            },
            "error": self.error,
        }


def _build_inner_args(
    parent_args: argparse.Namespace, signal: str,
) -> argparse.Namespace:
    """Build the argparse.Namespace `derive_threshold` expects from
    the survey-wrapper's args + a target signal."""
    return argparse.Namespace(
        manifest=parent_args.manifest,
        use=parent_args.use,
        signal=signal,
        fpr_target=parent_args.fpr_target,
        out=None,  # never used; we don't write per-signal ledger
        slug=None,
        replace=False,
        bootstrap_resamples=parent_args.bootstrap_resamples,
        bootstrap_confidence=parent_args.bootstrap_confidence,
        bootstrap_seed=parent_args.bootstrap_seed,
        # ``getattr`` for back-compat with the existing tests
        # that build parent_args without the new flags.
        bootstrap_engine=getattr(
            parent_args, "bootstrap_engine", "loop",
        ),
        bootstrap_chunk_size=getattr(
            parent_args, "bootstrap_chunk_size", None,
        ),
        bootstrap_device=getattr(
            parent_args, "bootstrap_device", None,
        ),
        tier2=parent_args.tier2,
        tier3=parent_args.tier3,
        # Codex P2 on PR #78: must forward the 1.80.0 / 1.81.0 fields
        # to the inner Namespace or ``load_or_score_corpus`` /
        # ``score_corpus`` will see ``None`` / ``False`` defaults via
        # their own ``getattr`` fallbacks -- the parent_args parser
        # accepted the flags but the inner Namespace dropped them,
        # so the standalone calibration_survey CLI silently fell back
        # to Tier 1+2+3 (legacy MiniLM) regardless of what
        # ``--tier4`` / ``--surprisal-model`` / ``--embedding-model``
        # the operator passed. ``getattr`` with back-compat defaults
        # so any pre-1.81 test fixture that hand-constructs a
        # parent_args without these flags still works.
        tier4=getattr(parent_args, "tier4", False),
        embedding_model=getattr(parent_args, "embedding_model", None),
        embedding_revision=getattr(parent_args, "embedding_revision", None),
        surprisal_model=getattr(parent_args, "surprisal_model", None),
        surprisal_revision=getattr(parent_args, "surprisal_revision", None),
        # 1.90.0+: forward the batched-Tier-4 batch size so
        # calibration_survey runs honor the operator-chosen value
        # rather than falling back to score_corpus's default of 8.
        surprisal_batch_size=getattr(
            parent_args, "surprisal_batch_size", 8,
        ),
        notes=None,
        # Forward the sub-sample knob so partial surveys hit the
        # same essays across signals (deterministic per seed).
        max_entries=getattr(parent_args, "max_entries", None),
        max_entries_seed=getattr(parent_args, "max_entries_seed", None),
        # Forward the incremental-cache flush cadence so
        # load_or_score_corpus checkpoints at the operator-chosen
        # frequency (1.69.0+).
        records_cache_flush_every=getattr(
            parent_args, "records_cache_flush_every", 100,
        ),
    )


def survey_one_signal(
    signal: str,
    parent_args: argparse.Namespace,
    *,
    tpr_floor: float,
    aggressiveness_tolerance: float,
    cached_records: list[dict[str, Any]] | None = None,
    cached_scoring_meta: dict[str, Any] | None = None,
) -> SurveyRow:
    """Run derive_threshold against one signal; return a SurveyRow.

    When ``cached_records`` is supplied, this skips the scoring step
    and uses the cached records directly — the score-once-survey-many
    optimization that lets a full 11-signal survey reuse one corpus
    scoring pass instead of re-scoring 11 times.

    Catches the common failure modes — derive_threshold raises
    ``SystemExit`` on registry mismatch / unscored corpus / missing
    manifest entries / unreachable FPR target — and stores them as
    the row's ``error`` field so a single bad signal doesn't abort
    the whole survey.
    """
    spec = COMPRESSION_HEURISTICS[signal]
    direction = spec.direction
    heuristic_value = getattr(spec, "value", None)
    if not isinstance(heuristic_value, (int, float)):
        heuristic_value = None

    inner = _build_inner_args(parent_args, signal)
    row = SurveyRow(
        signal=signal,
        direction=direction,
        heuristic_value=heuristic_value,
    )
    try:
        if cached_records is not None and cached_scoring_meta is not None:
            entry = ct.derive_threshold_from_records(
                cached_records,
                args=inner,
                scoring_meta=cached_scoring_meta,
            )
        else:
            entry = ct.derive_threshold(inner)
    except SystemExit as exc:
        row.error = str(exc) or "derive_threshold raised SystemExit"
        return row
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"
        return row

    # Real provenance entries from `derive_threshold_from_records`
    # use the nested `calibration` block + top-level `derived_value`;
    # synthetic test fixtures used flat `empirical` + `sweep`. Read
    # the real shape first, fall back to the test shape so existing
    # tests continue to pass.
    cal = entry.get("calibration") or entry.get("empirical") or {}
    sweep = entry.get("sweep") or {}
    row.auc = cal.get("auc")
    # Direction-aware AUC for polarity reading — same value the gate
    # uses, surfaced so the maintainer compares signals on a
    # consistent "matches/inverts" scale.
    if isinstance(row.auc, (int, float)):
        if direction == "gt":
            row.direction_aware_auc = float(row.auc)
        elif direction == "lt":
            row.direction_aware_auc = 1.0 - float(row.auc)
    row.ap = cal.get("ap")
    # Direction-aware AP: prefer the value derive_threshold writes
    # (calibrate_thresholds 1.29.1+); fall back to AP for `gt` and
    # ``None`` for `lt` legacy entries (re-deriving AP would require
    # the score column we no longer have at this layer).
    da_ap = cal.get("direction_aware_ap")
    if da_ap is None and isinstance(row.ap, (int, float)):
        if direction == "gt":
            da_ap = float(row.ap)
    row.direction_aware_ap = da_ap
    row.threshold = entry.get("derived_value") or sweep.get("threshold")
    row.tpr_at_threshold = (
        cal.get("empirical_tpr") or cal.get("tpr_at_threshold")
    )
    row.fpr_at_threshold = (
        cal.get("empirical_fpr") or cal.get("fpr_at_threshold")
    )
    row.n_pos = cal.get("n_pos")
    row.n_neg = cal.get("n_neg")
    row.fpr_resolution = cal.get("fpr_resolution") or sweep.get("fpr_resolution")
    row.gates = evaluate_gates(
        entry,
        heuristic_value=heuristic_value,
        direction=direction,
        tpr_floor=tpr_floor,
        aggressiveness_tolerance=aggressiveness_tolerance,
    )
    row.full_entry = entry
    return row


# ---- Pool helpers for parallel per-signal sweep ------------------
#
# ProcessPoolExecutor pickles every submitted callable's positional
# and keyword arguments and ships them to the worker process for
# each task. At MAGE scale ``cached_records`` is a ~100 MB list of
# dicts; at RAID scale closer to ~1.8 GB. Naively passing it as a
# kwarg to each ``survey_one_signal`` submission would re-serialize
# that payload N times (once per signal) and dominate any
# parallelism benefit.
#
# The standard workaround is ``initializer`` + module-level globals:
# the pool calls ``_pool_init`` exactly once per worker process on
# startup, the worker stashes the heavy state in module globals, and
# subsequent task submissions only need to ship the per-signal name
# (a single string). The worker pulls the heavy state out of the
# globals to reconstruct the call to ``survey_one_signal``.
#
# These globals are intentionally module-level (not class-level) so
# ``ProcessPoolExecutor`` on a ``spawn``-start platform (Windows,
# macOS-Python-3.8+) initializes them correctly when the worker
# re-imports this module. On Linux ``fork`` they'd be inherited
# either way; the module-global approach is portable across both.
_POOL_RECORDS: list[dict[str, Any]] | None = None
_POOL_SCORING_META: dict[str, Any] | None = None
_POOL_PARENT_ARGS: argparse.Namespace | None = None
_POOL_TPR_FLOOR: float = DEFAULT_TPR_FLOOR
_POOL_AGGRESSIVENESS_TOLERANCE: float = DEFAULT_AGGRESSIVENESS_TOLERANCE


def _pool_init(
    records: list[dict[str, Any]],
    scoring_meta: dict[str, Any],
    parent_args: argparse.Namespace,
    tpr_floor: float,
    aggressiveness_tolerance: float,
) -> None:
    """``ProcessPoolExecutor`` initializer.

    Stashes the per-signal-invariant state — the scored-records
    cache, the scoring metadata, the parent args namespace, and the
    two gate floors — into module globals so the worker process
    holds exactly one copy. Subsequent ``_survey_one_signal_pooled``
    calls then ship only the signal name across the pickle boundary
    instead of the full records list.
    """
    global _POOL_RECORDS, _POOL_SCORING_META, _POOL_PARENT_ARGS
    global _POOL_TPR_FLOOR, _POOL_AGGRESSIVENESS_TOLERANCE
    _POOL_RECORDS = records
    _POOL_SCORING_META = scoring_meta
    _POOL_PARENT_ARGS = parent_args
    _POOL_TPR_FLOOR = tpr_floor
    _POOL_AGGRESSIVENESS_TOLERANCE = aggressiveness_tolerance


def _survey_one_signal_pooled(signal: str) -> SurveyRow:
    """``ProcessPoolExecutor`` task: dispatch ``survey_one_signal``
    using the per-signal-invariant state stashed in module globals
    by ``_pool_init``. Returns a fully-populated ``SurveyRow`` that
    pickles cleanly back to the parent process.
    """
    if _POOL_PARENT_ARGS is None:
        # Defensive: should never happen because the pool's
        # ``initializer`` runs before any task is dispatched. Raise
        # an explicit error so silent ``None`` propagation can't
        # masquerade as a per-signal failure.
        raise RuntimeError(
            "_survey_one_signal_pooled called without _pool_init; "
            "pool was not initialized correctly"
        )
    return survey_one_signal(
        signal,
        _POOL_PARENT_ARGS,
        tpr_floor=_POOL_TPR_FLOOR,
        aggressiveness_tolerance=_POOL_AGGRESSIVENESS_TOLERANCE,
        cached_records=_POOL_RECORDS,
        cached_scoring_meta=_POOL_SCORING_META,
    )


def run_survey(
    parent_args: argparse.Namespace,
    *,
    signals: Sequence[str] | None = None,
    tpr_floor: float = DEFAULT_TPR_FLOOR,
    aggressiveness_tolerance: float = DEFAULT_AGGRESSIVENESS_TOLERANCE,
) -> dict[str, Any]:
    """Run `derive_threshold` against every signal in `signals`
    (default: all 11) and aggregate the results.

    Score-once-survey-many: the corpus is scored exactly once
    up-front; per-signal calls then re-use the cached records via
    ``derive_threshold_from_records``. This is an 11× speedup
    versus the pre-1.26 path where each signal re-scored the corpus.

    When ``--records-cache`` is set, the cache is loaded if
    compatible and survives across invocations — a re-run with a
    different ``--fpr-target`` or different gate floors is then
    threshold-sweep-only (seconds, not minutes). The cache is
    automatically invalidated when the manifest content, the tier
    flags, the use filter, or the scorer version changes.
    """
    if signals is None:
        signals = list(COMPRESSION_HEURISTICS.keys())

    cache_path_str = getattr(parent_args, "records_cache", None)
    cache_path = Path(cache_path_str).expanduser() if cache_path_str else None
    refresh = bool(getattr(parent_args, "refresh_cache", False))

    inner_for_scoring = _build_inner_args(parent_args, signals[0])
    sys.stderr.write(
        f"Surveying {len(signals)} signal(s) at FPR target "
        f"{parent_args.fpr_target}...\n"
        "Step 1: scoring corpus once "
        "(cached records are reused across signals).\n"
    )
    try:
        cached_records, cached_scoring_meta, cache_hit = (
            ct.load_or_score_corpus(
                inner_for_scoring,
                cache_path=cache_path,
                refresh=refresh,
            )
        )
    except SystemExit as exc:
        sys.stderr.write(f"corpus scoring failed: {exc}\n")
        raise

    workers = max(1, int(getattr(parent_args, "aggregate_workers", 1)))
    sys.stderr.write(
        f"Step 2: sweeping per-signal thresholds "
        f"(cache_hit={cache_hit}, workers={workers}).\n"
    )
    if workers <= 1 or len(signals) <= 1:
        # Serial path — keeps the historical behavior for small
        # signal lists and avoids ProcessPoolExecutor's spawn
        # overhead for the trivial case.
        rows: list[SurveyRow] = []
        for signal in signals:
            sys.stderr.write(f"  --> {signal}\n")
            row = survey_one_signal(
                signal, parent_args,
                tpr_floor=tpr_floor,
                aggressiveness_tolerance=aggressiveness_tolerance,
                cached_records=cached_records,
                cached_scoring_meta=cached_scoring_meta,
            )
            rows.append(row)
    else:
        # Parallel path: per-signal bootstrap is the dominant cost
        # at MAGE/RAID scale (~30-60 min per signal × 11 signals
        # serial = ~6 hours). Each ``survey_one_signal`` call is
        # independent of every other — same ``cached_records``
        # input, no cross-signal state — so a process pool gives
        # near-linear speedup up to the per-signal CPU work.
        #
        # The records list is passed via ProcessPoolExecutor's
        # initializer + module-level globals so each worker
        # process inherits one copy at spawn rather than
        # re-deserializing the ~100MB records list per signal
        # call (which would dominate the parallelism benefit).
        # See ``_pool_init`` / ``_survey_one_signal_pooled``.
        from concurrent.futures import ProcessPoolExecutor, as_completed

        sys.stderr.write(
            f"  ({len(signals)} signals across {workers} worker "
            f"process(es); each worker holds one copy of the "
            f"records cache)\n"
        )
        rows = [None] * len(signals)  # type: ignore[list-item]
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_pool_init,
            initargs=(
                cached_records, cached_scoring_meta, parent_args,
                tpr_floor, aggressiveness_tolerance,
            ),
        ) as pool:
            future_to_idx = {
                pool.submit(_survey_one_signal_pooled, signal): i
                for i, signal in enumerate(signals)
            }
            for fut in as_completed(future_to_idx):
                i = future_to_idx[fut]
                row = fut.result()
                rows[i] = row
                sys.stderr.write(f"  --> {signals[i]} (done)\n")
        # rows is now populated in original-signals order;
        # downstream sort handles re-ranking.
        rows = [r for r in rows if r is not None]
        if len(rows) != len(signals):
            sys.stderr.write(
                f"WARNING: pool returned {len(rows)} of "
                f"{len(signals)} expected rows; some signal(s) "
                f"failed silently. Falling back to serial path is "
                f"safer for diagnostic completeness; --aggregate-"
                f"workers 1 reproduces the historical execution.\n"
            )

    # Rank rows: signals that pass all evaluable gates float to the
    # top, then by descending direction-aware AUC (NOT raw AUC —
    # raw is direction-blind and would put inverted-polarity
    # signals like mtld with raw AUC 0.87 ahead of polarity-
    # matching signals like burstiness_B with raw AUC 0.32 / da_AUC
    # 0.68), then by descending TPR.
    def _rank_key(r: SurveyRow) -> tuple:
        da = r.direction_aware_auc
        return (
            -r.gates.n_passes,
            -(da if da is not None else -1),
            -(r.tpr_at_threshold if r.tpr_at_threshold is not None else -1),
        )
    rows.sort(key=_rank_key)

    max_entries = getattr(parent_args, "max_entries", None)
    is_pipeline_check = max_entries is not None and max_entries > 0

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "manifest": str(parent_args.manifest),
        "fpr_target": parent_args.fpr_target,
        "use": parent_args.use,
        "tier2": parent_args.tier2,
        "tier3": parent_args.tier3,
        # 1.81.0+: surface the Tier 4 + model selections in the
        # output JSON's provenance block so downstream consumers
        # (band classifier, ledger writer, side-by-side bake-off
        # comparators) can tell which embedding / surprisal model
        # the survey was scored under. Without these fields the
        # output JSON's provenance is ambiguous between a default
        # MiniLM run and a swap to mxbai/gemma/harrier.
        "tier4": getattr(parent_args, "tier4", False),
        "embedding_model": getattr(parent_args, "embedding_model", None),
        "embedding_revision": getattr(parent_args, "embedding_revision", None),
        "surprisal_model": getattr(parent_args, "surprisal_model", None),
        "surprisal_revision": getattr(parent_args, "surprisal_revision", None),
        "tpr_floor": tpr_floor,
        "aggressiveness_tolerance": aggressiveness_tolerance,
        "max_entries": max_entries,
        "max_entries_seed": getattr(parent_args, "max_entries_seed", None),
        "is_pipeline_check": is_pipeline_check,
        "n_signals": len(rows),
        "n_signals_all_gates_pass": sum(1 for r in rows if r.gates.all_pass),
        "rows": [r.to_dict() for r in rows],
        "date": _dt.date.today().isoformat(),
    }


# ---- Rendering ---------------------------------------------------


def _fmt(v: Any, places: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{places}f}"
    return str(v)


def _gate_glyph(gate: bool | None) -> str:
    if gate is True:
        return "✓"
    if gate is False:
        return "✗"
    return "?"


def render_markdown_table(survey: dict[str, Any]) -> str:
    """Markdown comparison table: one row per signal.

    Columns are chosen to match the maintainer's decision criteria.
    Errors render as a separate table beneath the main one so they
    don't pollute the comparison view.
    """
    rows = survey["rows"]
    ok_rows = [r for r in rows if r.get("error") is None]
    err_rows = [r for r in rows if r.get("error") is not None]

    lines: list[str] = ["# Calibration survey", ""]
    if survey.get("is_pipeline_check"):
        lines.extend([
            "> **PIPELINE CHECK** — `--max-entries "
            f"{survey['max_entries']}` was set. This is NOT a "
            "calibration; small-N gates won't pass meaningfully and "
            "the resulting thresholds must NOT be committed to "
            "`thresholds_calibrated.json`. Use this output to verify "
            "the pipeline runs end-to-end and to estimate wall-clock "
            "for the full run.",
            "",
        ])
    lines.extend([
        f"- **Manifest:** `{survey['manifest']}`",
        f"- **FPR target:** {survey['fpr_target']}",
        f"- **Use filter:** `{survey['use']}`",
        f"- **Tier 2:** {survey['tier2']}, **Tier 3:** {survey['tier3']}",
        f"- **TPR floor (gate 4):** {survey['tpr_floor']}",
        f"- **Aggressiveness tol. (gate 5):** "
        f"{survey['aggressiveness_tolerance']}",
        f"- **Signals all-gates-pass:** "
        f"{survey['n_signals_all_gates_pass']} / {survey['n_signals']}",
        f"- **Date:** {survey['date']}",
        "",
        "Gate legend: ✓ = pass, ✗ = fail, ? = not evaluated.",
        "Gates: 1 polarity, 2 AUC/AP not embarrassing (judgment, "
        "always shown ?), 3 enough negatives, 4 TPR ≥ floor, "
        "5 not more aggressive than heuristic.",
        "AUC is raw `roc_auc_score` (direction-blind). da_AUC is "
        "direction-aware: ≥0.5 means the signal's polarity matches "
        "the registry's hypothesis (registry direction `lt` → "
        "da_AUC = 1 − raw AUC; `gt` → da_AUC = raw AUC). AP is also "
        "raw (computed on raw scores); da_AP negates scores for `lt` "
        "signals so the precision curve reads on the registry's "
        "polarity. Sort by da_AUC and read da_AP for ranking quality "
        "— raw AP can make a strong `lt` discriminator look weak.",
        "",
        "| signal | dir | heur | AUC | da_AUC | AP | da_AP | thresh | TPR | FPR | n_neg | "
        "1 | 2 | 3 | 4 | 5 |",
        "|---|:-:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
        ":-:|:-:|:-:|:-:|:-:|",
    ])
    for r in ok_rows:
        gates = r["gates"]
        lines.append(
            "| `{signal}` | {dir} | {heur} | {auc} | {da_auc} | {ap} | {da_ap} | "
            "{thr} | {tpr} | {fpr} | {nneg} | "
            "{g1} | {g2} | {g3} | {g4} | {g5} |".format(
                signal=r["signal"],
                dir=r["direction"],
                heur=_fmt(r["heuristic_value"]),
                auc=_fmt(r["auc"]),
                da_auc=_fmt(r.get("direction_aware_auc")),
                ap=_fmt(r["ap"]),
                da_ap=_fmt(r.get("direction_aware_ap")),
                thr=_fmt(r["threshold"]),
                tpr=_fmt(r["tpr_at_threshold"]),
                fpr=_fmt(r["fpr_at_threshold"]),
                nneg=_fmt(r["n_neg"], places=0) if r["n_neg"] is not None else "—",
                g1=_gate_glyph(gates["polarity_matches"]),
                g2=_gate_glyph(gates["auc_ap_not_embarrassing"]),
                g3=_gate_glyph(gates["enough_negatives"]),
                g4=_gate_glyph(gates["interpretable_threshold"]),
                g5=_gate_glyph(gates["esl_conservative"]),
            )
        )
    if err_rows:
        lines.extend([
            "",
            "## Signals that failed to derive a threshold",
            "",
            "| signal | error |",
            "|---|---|",
        ])
        for r in err_rows:
            lines.append(f"| `{r['signal']}` | {r['error']} |")
    lines.append("")
    lines.append(
        "Read this table top-down: signals with more pass-glyphs "
        "(✓) come first. Pick the signal whose gate row is "
        "all-✓ AND whose AUC / AP feel substantive, then follow the "
        "5-step commit sequence in `scripts/calibration/PROVENANCE.md`."
    )
    return "\n".join(lines) + "\n"


# ---- CLI ---------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Survey every COMPRESSION_HEURISTICS signal at one FPR "
            "target and aggregate the results into a single table the "
            "maintainer reads to pick the first signal to commit. "
            "See scripts/calibration/PROVENANCE.md for the workflow."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--manifest", required=True,
                   help="Path to the labeled corpus manifest JSONL.")
    p.add_argument("--fpr-target", type=float, required=True,
                   help="FPR ceiling for the threshold sweep (e.g. 0.01).")
    p.add_argument("--use", default="validation",
                   help="Manifest 'use' tag to filter on (default validation).")
    p.add_argument("--out",
                   help=(
                       "Write the survey JSON ledger here. "
                       "Defaults to /tmp/calibration_survey_<date>.json."
                   ))
    p.add_argument("--signal", action="append", default=[],
                   help=(
                       "Restrict survey to this signal (repeatable). "
                       "Default: every key in COMPRESSION_HEURISTICS."
                   ))
    p.add_argument("--tier2", action="store_true", default=True,
                   help="Run Tier 2 features (POS bigrams + MDD-SD).")
    p.add_argument("--no-tier2", dest="tier2", action="store_false",
                   help="Skip Tier 2 features (faster).")
    p.add_argument("--tier3", action="store_true", default=True,
                   help="Run Tier 3 features (cohesion).")
    p.add_argument("--no-tier3", dest="tier3", action="store_false",
                   help="Skip Tier 3 features (faster).")
    # 1.81.0+: standalone-CLI exposure of the pipeline-wired Tier 4 +
    # pluggable-embedding flags landed in 1.80.0. The scoring path
    # (score_corpus -> score_smoothing_entry -> audit_text) reads
    # these via getattr; before 1.81.0 only shard_runner shard's CLI
    # populated them on the args Namespace, which made bake-off
    # subsample runs against the standalone calibration_survey
    # impossible without writing an ad-hoc Python driver.
    p.add_argument(
        "--tier4", action="store_true", default=False,
        help=(
            "Enable Tier 4 (surprisal) signals on the scoring run. "
            "Opt-in. Requires the surprisal dependency layer "
            "(transformers + torch); see scripts/calibration/"
            "RUNBOOK_tier4_install.md."
        ),
    )
    p.add_argument(
        "--no-tier4", dest="tier4", action="store_false",
        help="Explicitly disable Tier 4 (default is off; no-op when not paired with prior --tier4).",
    )
    p.add_argument(
        "--surprisal-model", default=None,
        help=(
            "Causal LM alias or HuggingFace id for Tier 4. "
            "Default (when --tier4 is set): tinyllama. See "
            "surprisal_backend.MODEL_ALIASES for the 9 candidates."
        ),
    )
    p.add_argument(
        "--surprisal-revision", default=None,
        help=(
            "Pin a HuggingFace commit SHA for the Tier 4 causal LM "
            "(reproducibility). Default: revision-less."
        ),
    )
    p.add_argument(
        "--surprisal-batch-size", type=int, default=8,
        help=(
            "Batch size for Tier 4 surprisal scoring under the "
            "batched ``score_texts`` path (1.90.0+). Larger values "
            "improve GPU utilisation but raise VRAM peak; 8 is "
            "conservative for 1-2B-param causal LMs on a 24 GB L4. "
            "Bump to 16 or 32 on A100 / H100. Set to 1 to bypass "
            "batching and reproduce the legacy per-entry scoring "
            "path exactly. No effect when --tier4 is off."
        ),
    )
    p.add_argument(
        "--embedding-model", default=None,
        help=(
            "Embedding-model alias or HuggingFace id for Tier 3 "
            "cohesion. Default: legacy MiniLM hardcode (for back-"
            "compat with pre-1.80 surveys). Aliases: mxbai, gemma, "
            "harrier, minilm. See embedding_backend.MODEL_ALIASES."
        ),
    )
    p.add_argument(
        "--embedding-revision", default=None,
        help=(
            "Pin a HuggingFace commit SHA for the Tier 3 embedding "
            "model (reproducibility). Default: revision-less."
        ),
    )
    p.add_argument("--bootstrap-resamples", type=int, default=2000)
    p.add_argument("--bootstrap-confidence", type=float, default=0.95)
    p.add_argument("--bootstrap-seed", type=int, default=42)
    p.add_argument(
        "--bootstrap-engine",
        choices=["loop", "numpy", "torch"],
        default="loop",
        help=(
            "Bootstrap-CI implementation. ``loop`` (default) is "
            "pure Python; bit-exact with pre-1.60 ledger entries. "
            "``numpy`` is a vectorized NumPy implementation that "
            "is 50-200x faster on >=100K-row corpora. ``torch`` is "
            "a PyTorch implementation that auto-detects a CUDA/"
            "ROCm GPU for an additional 5-15x speedup on top of "
            "``numpy``. All three are statistically equivalent for "
            "2000+ resamples; only ``loop`` is bit-exact with the "
            "pre-1.60 ledger. Recommended: ``numpy`` for MAGE/RAID "
            "CPU-only runs, ``torch`` if a GPU is available."
        ),
    )
    p.add_argument(
        "--bootstrap-device",
        default=None,
        help=(
            "Device override for ``--bootstrap-engine torch``. "
            "Default auto-detects (``cuda`` if a CUDA/ROCm GPU is "
            "reachable, else ``cpu``). Pass ``cpu`` to force the "
            "CPU torch path or a specific device string like "
            "``cuda:1`` to target a non-default GPU."
        ),
    )
    p.add_argument(
        "--bootstrap-chunk-size",
        type=int,
        default=None,
        help=(
            "Override the inner-loop chunk size for the vectorized "
            "bootstrap engines. Default auto-sizes to cap peak "
            "memory at ~500 MB. Pass a smaller value on memory-"
            "tight hosts or a larger one when memory is plentiful. "
            "See calibrate_thresholds.py --help for the full "
            "memory math."
        ),
    )
    p.add_argument(
        "--aggregate-workers", type=int, default=1,
        help=(
            "Number of worker processes for the per-signal threshold "
            "sweep. Default 1 (serial), which preserves the historical "
            "execution order and is byte-identical with pre-1.61 "
            "ledger entries on the deterministic-seed path. With N>1, "
            "the per-signal bootstrap calls — the dominant cost at "
            "MAGE/RAID scale (~30-60 min per signal × 11 signals = "
            "~6 hours serial) — run in a ``ProcessPoolExecutor``. On "
            "a 12-core consumer CPU, ``--aggregate-workers 11`` "
            "collapses the 11-signal survey to roughly the slowest "
            "single signal's wall-clock. Each worker process holds "
            "one copy of the scored-records cache (~100 MB at MAGE "
            "scale, ~1.8 GB at RAID scale); size the pool to fit "
            "available RAM. ``--aggregate-workers 0`` is treated as "
            "1 (no parallelism) rather than as 'auto-detect' to "
            "avoid surprising the user."
        ),
    )
    p.add_argument("--tpr-floor", type=float, default=DEFAULT_TPR_FLOOR,
                   help=(
                       "Gate 4 TPR floor: thresholds that fire on fewer "
                       "than this fraction of positives are 'predict almost "
                       "nothing'. Default 0.05."
                   ))
    p.add_argument("--aggressiveness-tolerance", type=float,
                   default=DEFAULT_AGGRESSIVENESS_TOLERANCE,
                   help=(
                       "Gate 5 tolerance: calibrated threshold differing "
                       "from the heuristic by at most this fraction "
                       "passes regardless of direction. Default 0.05."
                   ))
    p.add_argument("--records-cache", default=None,
                   help=(
                       "Path to a JSON cache of scored records. The "
                       "survey scores the corpus once up-front and "
                       "iterates signals over the cache; setting this "
                       "flag persists the cache across invocations. "
                       "Re-runs with different --fpr-target / --tpr-"
                       "floor / --aggressiveness-tolerance values "
                       "become threshold-sweep-only (seconds, not "
                       "minutes). Cache invalidates on manifest "
                       "change, tier toggle change, or scorer "
                       "version bump."
                   ))
    p.add_argument("--refresh-cache", action="store_true",
                   help=(
                       "Force re-scoring even if a compatible cache "
                       "exists. Use after a code change that should "
                       "invalidate cached records but didn't bump "
                       "SCORER_CACHE_VERSION."
                   ))
    p.add_argument("--records-cache-flush-every", type=int, default=100,
                   help=(
                       "Write the --records-cache atomically every N "
                       "scored entries with status='in_progress' "
                       "(1.69.0+). A crash mid-scoring loses at most "
                       "N entries of work; the next run automatically "
                       "resumes from the partial cache. Default 100. "
                       "Set lower (10-50) for very long-per-entry "
                       "tier3 runs; higher (500+) for short-per-entry "
                       "tier1-only runs where flush I/O would "
                       "dominate. Ignored when --records-cache is "
                       "unset (no checkpoint target)."
                   ))
    p.add_argument("--max-entries", type=int, default=None,
                   help=(
                       "Cap the manifest entries scored per signal. "
                       "Label-stratified sub-sampling (seeded). Use for "
                       "pipeline checks before committing to a full run "
                       "— small-N gates won't pass meaningfully. The "
                       "survey marks rows from a sub-sampled run with "
                       "a 'pipeline check' flag so the JSON output "
                       "is visibly distinct from a full-corpus survey."
                   ))
    p.add_argument("--max-entries-seed", type=int, default=None,
                   help=(
                       "Override the sub-sample seed. Defaults to "
                       "--bootstrap-seed so the same essays are scored "
                       "across signals (consistency across the survey)."
                   ))
    p.add_argument("--json-only", action="store_true",
                   help="Emit only the JSON ledger; skip the markdown table.")
    return p


def run(args: argparse.Namespace) -> int:
    if args.fpr_target <= 0 or args.fpr_target >= 1:
        sys.stderr.write(
            f"--fpr-target must be in (0, 1); got {args.fpr_target}\n"
        )
        return 2
    signals = args.signal or None
    if signals:
        unknown = [s for s in signals if s not in COMPRESSION_HEURISTICS]
        if unknown:
            sys.stderr.write(
                f"Unknown signal(s): {unknown}. "
                f"Known: {sorted(COMPRESSION_HEURISTICS)}\n"
            )
            return 2

    survey = run_survey(
        args, signals=signals,
        tpr_floor=args.tpr_floor,
        aggressiveness_tolerance=args.aggressiveness_tolerance,
    )

    out_path = (
        Path(args.out).expanduser() if args.out
        else Path(f"/tmp/calibration_survey_{survey['date']}.json")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(survey, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    sys.stderr.write(f"\nSurvey JSON written to: {out_path}\n")

    if not args.json_only:
        sys.stdout.write(render_markdown_table(survey))

    return 0 if survey["n_signals_all_gates_pass"] > 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
