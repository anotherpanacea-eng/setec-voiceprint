#!/usr/bin/env python3
"""narrative_longform_agreement.py — whole-vs-segmented agreement study
for the narrative-decision audit's long-form extension (spec 77, M1).

Purpose
-------

The study that will one day license work-level aggregation of the 33
StoryScope signals asks: does a signal aggregated over ~5,000-word
segments agree with the same signal judged on the whole work? M1 builds
the machinery judge-free over PRECOMPUTED values — no study runs now.
Judge cost stays external, mirroring
``calibration/narrative_polarity_audit.py``: an operator produces the
judged values elsewhere and hands this script a manifest.

Everything here is pure Python (stdlib only), deterministic, and
clock-free: the only date that ever appears in an artifact comes from
the required ``--date`` flag.

Signal identity (spec 77 S1, implemented locally)
-------------------------------------------------

::

    signal_id = "narrative.{bundle}.{feature_key}"          # option None (19)
    signal_id = "narrative.{bundle}.{feature_key}.{option}"  # w/ option (14)

Derived locally from ``narrative_feature_schema.CORE_FEATURES`` — this
module does not import any sibling's helper.

Operator classes (frozen; 12 not_aggregatable / 12 mean / 9 prevalence)
-----------------------------------------------------------------------

The same frozen table the long-form orchestrator uses, duplicated here
and cross-checked against ``CORE_FEATURES`` at import time (disjoint,
total, 12/12/9). Keys are ``feature_key`` or ``feature_key.option``.

Manifest format (JSONL, one row per work)
-----------------------------------------

::

    {"work_id": str, "n_words": int,
     "whole_work": {signal_id: {"value": ..., "available": bool}},
     "segments": [{"segment_id": str, "content_sha256": str,
                   "n_words": int,
                   "signals": {signal_id: {"value": ..., "available": bool}}}]}

Values are RAW judge responses: Likert strings ("4"), option strings
("resolved_internally"), or lists of option strings for multi-select.
Conversion happens here:

* mean class — scale: ``float(int(response))``; ordinal: the 0-based
  index of the response in the feature's ``response_options``.
* prevalence class — option present → 1.0, else 0.0 (categorical /
  binary: string equality; multi: list membership). A work's segment
  prevalence is the fraction of AVAILABLE segments where the option is
  present; unavailable segments leave the denominator.

Thresholds artifact (schema ``narrative-longform-thresholds/1``)
----------------------------------------------------------------

::

    {"schema": "narrative-longform-thresholds/1",
     "floors": {"min_works": 24, "min_signal_support": 18,
                "min_class_support": 6},
     "per_operator": {
        "mean": {"spearman_min": <float>,
                 "mad_max_response_units": <float>},
        "prevalence": {"auc_min": <float>}}}

Direction is explicit in the key names: ``*_min`` means
higher-is-better (value >= threshold passes), ``*_max`` means
lower-is-better (value <= threshold passes). A ``mean`` signal passes
only if BOTH statistics pass. Real values are operator-frozen later;
tests carry a documented example.

Two-step pre-registration (spec 77 S4)
--------------------------------------

``--register`` writes a ``narrative-longform-registration/1`` record
binding the design before any judged value exists::

    {"schema": "narrative-longform-registration/1", "date": ...,
     "thresholds_sha256": "sha256:...",  # plain hash, thresholds file bytes
     "work_ids_sha256": "sha256:...",    # canonical JSON, sorted work_id list
     "segmenter": {"version": ..., "params_sha256": ...,
                   "segment_target_words": ...},
     "judge": {"kind": ..., "model": ..., "model_revision": ...,
               "prompt_version": ...}}

The registration manifest must be VALUES-FREE: ``--register`` refuses
if any row carries a non-empty ``whole_work`` or any segment with
non-empty ``signals``. ``mock`` judges and non-concrete identities
(empty, null, or the ``host-resolved`` sentinel) refuse at registration
AND evaluation, always.

``--evaluate`` refuses without a registration whose
``thresholds_sha256`` AND ``work_ids_sha256`` both match the live
inputs — that pair of equalities is the definition of "matching".

Statistics
----------

* mean class: Spearman rho (average-rank ties, stdlib implementation)
  between the whole-work value and the segment MEAN across works, plus
  the mean absolute deviation ``mean(|whole_i - segment_mean_i|)`` in
  response units. If either vector is constant the signal is
  indeterminate — Spearman is undefined and there is NO epsilon rescue.
* prevalence class: rank-based AUC (Mann-Whitney form; ties count 0.5)
  of segment prevalence predicting the whole-work indicator. If the
  whole-work class is single-valued across supported works, the class
  counts collapse and the signal is insufficient_support.

Verdict derivation (THE rule; the spec sketch never fixed it)
-------------------------------------------------------------

``derive_verdict`` is one pure function, deterministic given
(statistics, thresholds). Verdict domain (closed):
``validated_aggregatable | not_aggregatable | insufficient_support |
indeterminate``. In order:

1. operator == not_aggregatable → ``not_aggregatable`` a priori. These
   12 signals are NEVER evaluated; floors and statistics are irrelevant
   by construction (more data could never validate them, so the floor
   rules below do not reach them).
2. corpus works < floors.min_works → ``insufficient_support``.
3. per-signal support < floors.min_signal_support →
   ``insufficient_support``. Support = number of works whose whole-work
   cell is available AND that have at least one available segment cell
   for the signal.
4. prevalence only: either whole-work class among supported works has
   fewer than max(1, floors.min_class_support) members →
   ``insufficient_support`` (a single-valued whole-work class is the
   n == 0 case of this rule).
5. degenerate inputs (mean only: either the whole-work vector or the
   segment-mean vector is constant) → ``indeterminate``.
6. threshold comparison per operator class: every recorded statistic
   passes its direction (``min``: value >= threshold; ``max``: value <=
   threshold) → ``validated_aggregatable``; any failure →
   ``not_aggregatable`` (empirical — distinguishable from the a-priori
   case by the ``operator`` field and non-empty ``statistics``).

``statistics`` is recorded (non-empty) only when the derivation reaches
step 6; every earlier exit records ``statistics: []``.

Receipt (schema ``narrative_longform_validation_receipt/1``)
------------------------------------------------------------

Written only by ``--evaluate``; keys exactly: ``schema_version``,
``date``, ``arm`` ("stability"), ``signal_id_set_sha256`` (canonical
JSON of the sorted 33 ids), ``thresholds_sha256``,
``registration_sha256`` (plain hash of the registration file),
``derivation_sha256``, ``manifest_sha256`` (plain hash of the manifest
file), ``registration_path``, ``manifest_path``, ``corpus_n_works``,
``segmenter``, ``judge``, ``validated_segment_count_range``
({min,max} of per-work segment counts), ``validated_segment_words``
({min,max,median} over all segments' n_words) — both COMPUTED from the
manifest's segments — and ``per_signal``:
``{id: {verdict, operator, units, support,
statistics: [{name, value, threshold, direction}]}}``.

derivation_sha256 — exact construction
--------------------------------------

::

    preimage = [
        registration_sha256,                       # "sha256:..." string
        manifest_sha256,                          # "sha256:..." string
        [[signal_id, support], ...],              # all 33, sorted by id
        [[signal_id, name, round(value, 10),
          round(threshold, 10), direction], ...], # every recorded stat,
                                                  # sorted (signal_id, name)
        {"version": ..., "params_sha256": ...,    # segmenter identity,
         "segment_target_words": ...},            #   exactly these keys
        {"kind": ..., "model": ...,               # judge identity,
         "model_revision": ..., "prompt_version": ...},
    ]
    derivation_sha256 = (
        "sha256:" + sha256(canonical_json(preimage)).hexdigest())

where ``canonical_json(x)`` is ``json.dumps(x, sort_keys=True,
separators=(",", ":"), ensure_ascii=False).encode("utf-8")``. Floats
are rounded to 10 decimal places in the preimage (and stored rounded in
the receipt) so the digest is byte-stable.

Hashing convention (spec 77 S2a): every ``*_sha256`` is an ordinary
SHA-256 with a ``"sha256:"`` prefix. ``thresholds_sha256``,
``registration_sha256``, and ``manifest_sha256`` are over exact FILE
bytes (chunked, mirroring ``calibrate_thresholds._manifest_content_hash``);
``derivation_sha256``, ``signal_id_set_sha256``, and
``work_ids_sha256`` are over canonical JSON.

Verification
------------

``verify_receipt(receipt_path, thresholds_path, registration_path,
manifest_path)`` RE-DERIVES the verdicts from the manifest + thresholds
and the derivation_sha256 from the artifacts, and refuses on any
mismatch. A hand-edited "validated_aggregatable with Spearman 0.02"
receipt is therefore detectable: the verifier never trusts the
receipt's verdict strings.

CLI (flat flags)
----------------

::

    --manifest M --thresholds T [--registration R] --out O
        {--register | --evaluate | --verify} [--date YYYY-MM-DD]

* ``--register``: reads the values-free design manifest, writes the
  registration to ``--out``. Requires ``--date`` plus the segmenter /
  judge identity flags (``--segmenter-version``,
  ``--segmenter-params-sha256``, ``--segment-target-words``,
  ``--judge-kind``, ``--judge-model``, ``--judge-model-revision``,
  ``--judge-prompt-version``).
* ``--evaluate``: requires ``--registration`` and ``--date``; writes
  the receipt to ``--out``.
* ``--verify``: requires ``--registration``; READS the receipt at
  ``--out`` and re-derives everything. Exit 0 iff the receipt is
  reproducible from the artifacts.

Refusals exit 2 with a one-line reason on stderr.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# narrative_feature_schema lives one directory up.
SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from narrative_feature_schema import (  # type: ignore  # noqa: E402
    CORE_FEATURES,
)

__all__ = [
    "CalibrationRefusal",
    "THRESHOLDS_SCHEMA",
    "REGISTRATION_SCHEMA",
    "RECEIPT_SCHEMA",
    "VERDICT_VALIDATED",
    "VERDICT_NOT_AGGREGATABLE",
    "VERDICT_INSUFFICIENT",
    "VERDICT_INDETERMINATE",
    "OPERATOR_NOT_AGGREGATABLE",
    "OPERATOR_MEAN",
    "OPERATOR_PREVALENCE",
    "SIGNALS",
    "SIGNAL_IDS",
    "signal_id_set_sha256",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "file_sha256",
    "average_ranks",
    "spearman_rho",
    "mean_absolute_deviation",
    "auc_mannwhitney",
    "convert_mean_response",
    "option_present",
    "derive_verdict",
    "load_thresholds",
    "load_registration",
    "load_manifest_rows",
    "work_ids_sha256_for_rows",
    "build_registration",
    "build_receipt",
    "verify_receipt",
    "main",
]


# ---------- schemas and closed vocabularies -------------------------

THRESHOLDS_SCHEMA = "narrative-longform-thresholds/1"
REGISTRATION_SCHEMA = "narrative-longform-registration/1"
RECEIPT_SCHEMA = "narrative_longform_validation_receipt/1"
RECEIPT_ARM = "stability"

VERDICT_VALIDATED = "validated_aggregatable"
VERDICT_NOT_AGGREGATABLE = "not_aggregatable"
VERDICT_INSUFFICIENT = "insufficient_support"
VERDICT_INDETERMINATE = "indeterminate"

OPERATOR_NOT_AGGREGATABLE = "not_aggregatable"
OPERATOR_MEAN = "mean"
OPERATOR_PREVALENCE = "prevalence"

OPERATOR_UNITS = {
    OPERATOR_MEAN: "response_units",
    OPERATOR_PREVALENCE: "prevalence",
    OPERATOR_NOT_AGGREGATABLE: "none",
}

# The sentinel judge_backends.judge_identity_is_concrete() rejects.
_NON_CONCRETE_SENTINEL = "host-resolved"

_RECEIPT_KEYS = frozenset({
    "schema_version", "date", "arm", "signal_id_set_sha256",
    "thresholds_sha256", "registration_sha256", "derivation_sha256",
    "manifest_sha256", "registration_path", "manifest_path",
    "corpus_n_works", "segmenter", "judge",
    "validated_segment_count_range", "validated_segment_words",
    "per_signal",
})

_SEGMENTER_KEYS = frozenset({
    "version", "params_sha256", "segment_target_words",
})
_JUDGE_KEYS = frozenset({
    "kind", "model", "model_revision", "prompt_version",
})


class CalibrationRefusal(Exception):
    """Raised on any refusal; the CLI maps it to exit code 2."""


# ---------- the frozen operator table (spec 77, duplicated locally) --
#
# Keyed by "feature_key" (option=None signals) or "feature_key.option"
# (option-bearing signals). The import-time self-check below asserts
# the three sets are pairwise disjoint, cover the 33 schema signals
# with nothing left over, and split 12 / 12 / 9.

NOT_AGGREGATABLE_KEYS = frozenset({
    # Ending / resolution mechanics
    "mode_of_resolution.resolved_internally",
    "agency_in_resolution.protagonist_choice",
    # Subplot structure
    "subplot_integration.no_subplots",
    "subplot_integration.thematically_parallel",
    # Global temporal structure
    "anachrony_intensity",
    "degree_of_chronological_discontinuity",
    "nonlinear_framing_for_delayed_disclosure",
    "depth_of_recontextualization_after_surprise",
    # Position-anchored
    "opening_spatial_grounding",
    "character_introduction.external_description",
    "pre_threat_character_investment",
    # Whole-work scope by definition
    "location_variety_scope",
})

MEAN_KEYS = frozenset({
    "continuity_of_main_causal_chain",
    "depth_of_interior_access",
    "dialogue_to_narration_proportion",
    "environmental_ecological_emphasis",
    "moral_philosophical_weighting",
    "sensory_density",
    "setting_as_psychological_mirror",
    "thematic_explicitness_and_moralizing",
    "thematic_unity",
    "fourth_wall_permeability",
    "frequency_of_direct_reader_address",
    "spatial_granularity_level",
})

PREVALENCE_KEYS = frozenset({
    "narratorial_thematic_commentary.yes",
    "dialogue_function.philosophical_debate",
    "dominant_sensory_modalities.olfactory",
    "intertextual_strategy_types.explicit_named",
    "moral_polarity_toward_protagonist.ambivalent_or_mixed",
    "dominant_emotional_expression.embodied_metaphors",
    "dominant_emotional_expression.explicit_labels",
    "reference_explicitness.balanced_mix",
    "reference_explicitness.implicit_echoes",
})


# ---------- signal registry -----------------------------------------

@dataclass(frozen=True)
class SignalSpec:
    """One of the 33 signals, with its long-form operator class."""

    signal_id: str
    feature_key: str
    feature_type: str
    response_options: tuple[str, ...]
    option: str | None
    bundle: str
    operator: str

    @property
    def units(self) -> str:
        return OPERATOR_UNITS[self.operator]


def _build_registry() -> dict[str, SignalSpec]:
    """Derive signal ids locally (spec 77 S1) and join the frozen
    operator table. Raises at import on any drift between the table
    and the schema."""
    registry: dict[str, SignalSpec] = {}
    consumed: set[str] = set()
    for feat in CORE_FEATURES:
        for sig in feat.signals:
            if sig.option is None:
                signal_id = f"narrative.{sig.bundle}.{feat.key}"
                short = feat.key
            else:
                signal_id = (
                    f"narrative.{sig.bundle}.{feat.key}.{sig.option}"
                )
                short = f"{feat.key}.{sig.option}"
            membership = [
                op for op, keys in (
                    (OPERATOR_NOT_AGGREGATABLE, NOT_AGGREGATABLE_KEYS),
                    (OPERATOR_MEAN, MEAN_KEYS),
                    (OPERATOR_PREVALENCE, PREVALENCE_KEYS),
                ) if short in keys
            ]
            if len(membership) != 1:
                raise RuntimeError(
                    f"operator table drift: {short!r} appears in "
                    f"{len(membership)} operator classes (must be "
                    f"exactly 1)"
                )
            if signal_id in registry:
                raise RuntimeError(
                    f"duplicate signal_id derived: {signal_id!r}"
                )
            consumed.add(short)
            registry[signal_id] = SignalSpec(
                signal_id=signal_id,
                feature_key=feat.key,
                feature_type=feat.feature_type,
                response_options=feat.response_options,
                option=sig.option,
                bundle=sig.bundle,
                operator=membership[0],
            )
    leftovers = (
        (NOT_AGGREGATABLE_KEYS | MEAN_KEYS | PREVALENCE_KEYS) - consumed
    )
    if leftovers:
        raise RuntimeError(
            f"operator table drift: keys match no schema signal: "
            f"{sorted(leftovers)}"
        )
    if len(registry) != 33:
        raise RuntimeError(
            f"expected 33 signals, derived {len(registry)}"
        )
    counts = {
        OPERATOR_NOT_AGGREGATABLE: 0,
        OPERATOR_MEAN: 0,
        OPERATOR_PREVALENCE: 0,
    }
    for spec in registry.values():
        counts[spec.operator] += 1
    if counts != {
        OPERATOR_NOT_AGGREGATABLE: 12,
        OPERATOR_MEAN: 12,
        OPERATOR_PREVALENCE: 9,
    }:
        raise RuntimeError(f"operator split drift: {counts}")
    return registry


SIGNALS: dict[str, SignalSpec] = _build_registry()
SIGNAL_IDS: tuple[str, ...] = tuple(sorted(SIGNALS))


# ---------- hashing --------------------------------------------------

def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, raw unicode."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def canonical_json_sha256(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def file_sha256(path: Path) -> str:
    """Chunked SHA-256 over exact file bytes, "sha256:"-prefixed —
    the ``calibrate_thresholds._manifest_content_hash`` idiom."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def signal_id_set_sha256() -> str:
    """Canonical-JSON hash of the sorted 33 signal ids."""
    return canonical_json_sha256(list(SIGNAL_IDS))


# ---------- statistics (stdlib) --------------------------------------

def average_ranks(values: list[float]) -> list[float]:
    """1-based ranks with ties assigned the average of their span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while (
            j + 1 < len(order)
            and values[order[j + 1]] == values[order[i]]
        ):
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_rho(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rho with average-rank ties: Pearson over the ranks.

    Returns None when either vector is constant (undefined; the caller
    must treat the signal as indeterminate — no epsilon division).
    """
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    if len(set(xs)) <= 1 or len(set(ys)) <= 1:
        return None
    rx = average_ranks(xs)
    ry = average_ranks(ys)
    n = len(rx)
    mx = sum(rx) / n
    my = sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0.0 or vy == 0.0:
        return None  # all-tied ranks; constant after ranking
    return cov / math.sqrt(vx * vy)


def mean_absolute_deviation(xs: list[float], ys: list[float]) -> float:
    """mean(|x_i - y_i|) — agreement deviation in response units."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("mean_absolute_deviation needs paired vectors")
    return sum(abs(a - b) for a, b in zip(xs, ys)) / len(xs)


def auc_mannwhitney(
    pos_scores: list[float],
    neg_scores: list[float],
) -> float | None:
    """Rank-based AUC, Mann-Whitney form; ties count 0.5.

    Returns None when either class is empty (single-valued whole-work
    class → the caller emits insufficient_support).
    """
    if not pos_scores or not neg_scores:
        return None
    wins = 0.0
    for p in pos_scores:
        for n in neg_scores:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos_scores) * len(neg_scores))


# ---------- raw-response conversion ----------------------------------

def convert_mean_response(spec: SignalSpec, response: Any) -> float:
    """Convert a raw mean-class response to a float in response units.

    scale → float(int(response)); ordinal → 0-based index into the
    feature's response_options. Illegal responses refuse loudly: a bad
    value in a precomputed manifest is a broken upstream pipeline, not
    something to average over.
    """
    text = str(response)
    if text not in spec.response_options:
        raise CalibrationRefusal(
            f"illegal response {response!r} for signal "
            f"{spec.signal_id} (legal: {list(spec.response_options)})"
        )
    if spec.feature_type == "scale":
        return float(int(text))
    if spec.feature_type == "ordinal":
        return float(spec.response_options.index(text))
    raise CalibrationRefusal(
        f"signal {spec.signal_id} has feature_type "
        f"{spec.feature_type!r}, which is not a mean-class type"
    )


def option_present(spec: SignalSpec, response: Any) -> float:
    """Prevalence-class indicator: option present → 1.0, else 0.0.

    categorical / binary: string equality with the signal's option.
    multi: membership of the option in the response list.
    """
    if spec.option is None:
        raise CalibrationRefusal(
            f"signal {spec.signal_id} carries no option; prevalence "
            f"conversion is undefined"
        )
    if spec.feature_type == "multi":
        if not isinstance(response, (list, tuple)):
            raise CalibrationRefusal(
                f"multi-select signal {spec.signal_id} needs a list "
                f"response; got {type(response).__name__}"
            )
        for item in response:
            if str(item) not in spec.response_options:
                raise CalibrationRefusal(
                    f"illegal multi-select item {item!r} for signal "
                    f"{spec.signal_id}"
                )
        return 1.0 if spec.option in [str(i) for i in response] else 0.0
    text = str(response)
    if text not in spec.response_options:
        raise CalibrationRefusal(
            f"illegal response {response!r} for signal "
            f"{spec.signal_id} (legal: {list(spec.response_options)})"
        )
    return 1.0 if text == spec.option else 0.0


# ---------- thresholds artifact ---------------------------------------

def _require_keys(
    obj: dict, keys: frozenset[str] | set[str], what: str,
) -> None:
    got = set(obj)
    if got != set(keys):
        missing = sorted(set(keys) - got)
        extra = sorted(got - set(keys))
        raise CalibrationRefusal(
            f"{what}: key set mismatch"
            + (f"; missing {missing}" if missing else "")
            + (f"; unexpected {extra}" if extra else "")
        )


def _require_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationRefusal(f"{what} must be a number; got {value!r}")
    return float(value)


def load_thresholds(path: Path) -> dict[str, Any]:
    """Load + strictly validate a thresholds artifact."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationRefusal(
            f"cannot read thresholds file {path}: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise CalibrationRefusal("thresholds artifact must be a JSON object")
    _require_keys(obj, {"schema", "floors", "per_operator"}, "thresholds")
    if obj["schema"] != THRESHOLDS_SCHEMA:
        raise CalibrationRefusal(
            f"thresholds schema must be {THRESHOLDS_SCHEMA!r}; got "
            f"{obj['schema']!r}"
        )
    floors = obj["floors"]
    if not isinstance(floors, dict):
        raise CalibrationRefusal("thresholds.floors must be an object")
    _require_keys(
        floors,
        {"min_works", "min_signal_support", "min_class_support"},
        "thresholds.floors",
    )
    for k, v in floors.items():
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise CalibrationRefusal(
                f"thresholds.floors.{k} must be an integer >= 1; "
                f"got {v!r}"
            )
    per_op = obj["per_operator"]
    if not isinstance(per_op, dict):
        raise CalibrationRefusal("thresholds.per_operator must be an object")
    _require_keys(per_op, {"mean", "prevalence"}, "thresholds.per_operator")
    mean_block = per_op["mean"]
    if not isinstance(mean_block, dict):
        raise CalibrationRefusal(
            "thresholds.per_operator.mean must be an object"
        )
    _require_keys(
        mean_block,
        {"spearman_min", "mad_max_response_units"},
        "thresholds.per_operator.mean",
    )
    spearman_min = _require_number(
        mean_block["spearman_min"], "spearman_min",
    )
    if not (-1.0 <= spearman_min <= 1.0):
        raise CalibrationRefusal(
            f"spearman_min must be in [-1, 1]; got {spearman_min}"
        )
    mad_max = _require_number(
        mean_block["mad_max_response_units"], "mad_max_response_units",
    )
    if mad_max < 0.0:
        raise CalibrationRefusal(
            f"mad_max_response_units must be >= 0; got {mad_max}"
        )
    prev_block = per_op["prevalence"]
    if not isinstance(prev_block, dict):
        raise CalibrationRefusal(
            "thresholds.per_operator.prevalence must be an object"
        )
    _require_keys(
        prev_block, {"auc_min"}, "thresholds.per_operator.prevalence",
    )
    auc_min = _require_number(prev_block["auc_min"], "auc_min")
    if not (0.0 <= auc_min <= 1.0):
        raise CalibrationRefusal(
            f"auc_min must be in [0, 1]; got {auc_min}"
        )
    return obj


# ---------- registration artifact -------------------------------------

def _validate_identity_block(
    block: Any, keys: frozenset[str], what: str,
) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise CalibrationRefusal(f"{what} must be an object")
    _require_keys(block, keys, what)
    return block


def _validate_segmenter(block: Any) -> dict[str, Any]:
    seg = _validate_identity_block(block, _SEGMENTER_KEYS, "segmenter")
    for k in ("version", "params_sha256"):
        v = seg[k]
        if not isinstance(v, str) or not v.strip():
            raise CalibrationRefusal(
                f"segmenter.{k} must be a non-empty string; got {v!r}"
            )
    if not str(seg["params_sha256"]).startswith("sha256:"):
        raise CalibrationRefusal(
            "segmenter.params_sha256 must be 'sha256:'-prefixed"
        )
    stw = seg["segment_target_words"]
    if isinstance(stw, bool) or not isinstance(stw, int) or stw < 1:
        raise CalibrationRefusal(
            f"segmenter.segment_target_words must be an integer >= 1; "
            f"got {stw!r}"
        )
    return seg


def _validate_judge(block: Any) -> dict[str, Any]:
    judge = _validate_identity_block(block, _JUDGE_KEYS, "judge")
    for k in sorted(_JUDGE_KEYS):
        v = judge[k]
        if not isinstance(v, str) or not v.strip():
            raise CalibrationRefusal(
                f"judge.{k} must be a non-empty string; got {v!r} "
                f"(null identity refuses — spec 77 S2)"
            )
        if v == _NON_CONCRETE_SENTINEL:
            raise CalibrationRefusal(
                f"judge.{k} is the non-concrete sentinel "
                f"{_NON_CONCRETE_SENTINEL!r}; refused (spec 77 S2)"
            )
    if judge["kind"] == "mock":
        raise CalibrationRefusal(
            "judge.kind 'mock' is refused at registration and "
            "evaluation, always (spec 77 S2)"
        )
    return judge


def load_registration(path: Path) -> dict[str, Any]:
    """Load + strictly validate a registration record."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationRefusal(
            f"cannot read registration file {path}: {exc}"
        ) from exc
    if not isinstance(obj, dict):
        raise CalibrationRefusal("registration must be a JSON object")
    _require_keys(
        obj,
        {
            "schema", "date", "thresholds_sha256", "work_ids_sha256",
            "segmenter", "judge",
        },
        "registration",
    )
    if obj["schema"] != REGISTRATION_SCHEMA:
        raise CalibrationRefusal(
            f"registration schema must be {REGISTRATION_SCHEMA!r}; "
            f"got {obj['schema']!r}"
        )
    _validate_date(obj["date"])
    for k in ("thresholds_sha256", "work_ids_sha256"):
        v = obj[k]
        if not isinstance(v, str) or not v.startswith("sha256:"):
            raise CalibrationRefusal(
                f"registration.{k} must be a 'sha256:'-prefixed string"
            )
    _validate_segmenter(obj["segmenter"])
    _validate_judge(obj["judge"])
    return obj


# ---------- manifest loading -------------------------------------------

def _validate_cell(cell: Any, where: str) -> tuple[Any, bool]:
    if not isinstance(cell, dict):
        raise CalibrationRefusal(f"{where}: signal cell must be an object")
    _require_keys(cell, {"value", "available"}, where)
    if not isinstance(cell["available"], bool):
        raise CalibrationRefusal(f"{where}: 'available' must be a bool")
    return cell["value"], cell["available"]


def _validate_signal_map(obj: Any, where: str) -> dict[str, tuple[Any, bool]]:
    if not isinstance(obj, dict):
        raise CalibrationRefusal(f"{where} must be an object")
    out: dict[str, tuple[Any, bool]] = {}
    for signal_id, cell in obj.items():
        if signal_id not in SIGNALS:
            raise CalibrationRefusal(
                f"{where}: unknown signal_id {signal_id!r}"
            )
        out[signal_id] = _validate_cell(cell, f"{where}[{signal_id}]")
    return out


def load_manifest_rows(
    path: Path, *, values_free: bool,
) -> list[dict[str, Any]]:
    """Load JSONL manifest rows.

    ``values_free=True`` (registration mode): rows need only
    ``work_id``; any row carrying a non-empty ``whole_work`` or a
    segment with non-empty ``signals`` refuses — the registration must
    precede every judged value.

    ``values_free=False`` (evaluation mode): the full row shape is
    required and every signal cell is validated.
    """
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationRefusal(
            f"cannot read manifest {path}: {exc}"
        ) from exc
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationRefusal(
                f"manifest line {lineno}: invalid JSON: {exc}"
            ) from exc
        if not isinstance(obj, dict):
            raise CalibrationRefusal(
                f"manifest line {lineno}: row must be an object"
            )
        work_id = obj.get("work_id")
        if not isinstance(work_id, str) or not work_id:
            raise CalibrationRefusal(
                f"manifest line {lineno}: work_id must be a non-empty "
                f"string"
            )
        if work_id in seen_ids:
            raise CalibrationRefusal(
                f"manifest line {lineno}: duplicate work_id {work_id!r}"
            )
        seen_ids.add(work_id)
        if values_free:
            whole = obj.get("whole_work")
            if isinstance(whole, dict) and whole:
                raise CalibrationRefusal(
                    f"registration manifest must be values-free: work "
                    f"{work_id!r} carries whole_work values"
                )
            for seg in obj.get("segments") or []:
                if isinstance(seg, dict) and seg.get("signals"):
                    raise CalibrationRefusal(
                        f"registration manifest must be values-free: "
                        f"work {work_id!r} carries segment values"
                    )
            rows.append({"work_id": work_id})
            continue
        n_words = obj.get("n_words")
        if isinstance(n_words, bool) or not isinstance(n_words, int) \
                or n_words < 1:
            raise CalibrationRefusal(
                f"manifest work {work_id!r}: n_words must be an "
                f"integer >= 1"
            )
        whole = _validate_signal_map(
            obj.get("whole_work"), f"work {work_id!r} whole_work",
        )
        segments_in = obj.get("segments")
        if not isinstance(segments_in, list):
            raise CalibrationRefusal(
                f"manifest work {work_id!r}: segments must be a list"
            )
        segments: list[dict[str, Any]] = []
        seg_ids: set[str] = set()
        for seg in segments_in:
            if not isinstance(seg, dict):
                raise CalibrationRefusal(
                    f"manifest work {work_id!r}: segment must be an "
                    f"object"
                )
            _require_keys(
                seg,
                {"segment_id", "content_sha256", "n_words", "signals"},
                f"work {work_id!r} segment",
            )
            seg_id = seg["segment_id"]
            if not isinstance(seg_id, str) or not seg_id:
                raise CalibrationRefusal(
                    f"manifest work {work_id!r}: segment_id must be a "
                    f"non-empty string"
                )
            if seg_id in seg_ids:
                raise CalibrationRefusal(
                    f"manifest work {work_id!r}: duplicate segment_id "
                    f"{seg_id!r}"
                )
            seg_ids.add(seg_id)
            csha = seg["content_sha256"]
            if not isinstance(csha, str) or not csha.startswith("sha256:"):
                raise CalibrationRefusal(
                    f"manifest work {work_id!r} segment {seg_id!r}: "
                    f"content_sha256 must be 'sha256:'-prefixed"
                )
            snw = seg["n_words"]
            if isinstance(snw, bool) or not isinstance(snw, int) or snw < 1:
                raise CalibrationRefusal(
                    f"manifest work {work_id!r} segment {seg_id!r}: "
                    f"n_words must be an integer >= 1"
                )
            signals = _validate_signal_map(
                seg["signals"],
                f"work {work_id!r} segment {seg_id!r} signals",
            )
            segments.append({
                "segment_id": seg_id,
                "content_sha256": csha,
                "n_words": snw,
                "signals": signals,
            })
        rows.append({
            "work_id": work_id,
            "n_words": n_words,
            "whole_work": whole,
            "segments": segments,
        })
    if not rows:
        raise CalibrationRefusal(f"manifest {path} yielded 0 rows")
    return rows


def work_ids_sha256_for_rows(rows: list[dict[str, Any]]) -> str:
    """Canonical-JSON hash of the sorted work_id list."""
    return canonical_json_sha256(sorted(r["work_id"] for r in rows))


# ---------- verdict derivation (THE rule) ------------------------------

def derive_verdict(
    *,
    operator: str,
    corpus_n_works: int,
    support: int,
    floors: dict[str, int],
    n_pos: int | None = None,
    n_neg: int | None = None,
    degenerate: bool = False,
    statistics: list[dict[str, Any]] | None = None,
) -> str:
    """One pure function; deterministic given (statistics, thresholds).

    See the module docstring ("Verdict derivation") for the numbered
    rule. ``statistics`` entries carry their own threshold + direction,
    so this function needs no separate thresholds argument: the
    comparison is value >= threshold for direction "min" and
    value <= threshold for direction "max".
    """
    # 1. a priori: never evaluated; floors are irrelevant by design.
    if operator == OPERATOR_NOT_AGGREGATABLE:
        return VERDICT_NOT_AGGREGATABLE
    # 2. corpus floor.
    if corpus_n_works < floors["min_works"]:
        return VERDICT_INSUFFICIENT
    # 3. per-signal support floor.
    if support < floors["min_signal_support"]:
        return VERDICT_INSUFFICIENT
    # 4. prevalence class floor (single-valued whole-work class is the
    #    n == 0 case).
    if operator == OPERATOR_PREVALENCE:
        class_floor = max(1, floors["min_class_support"])
        if n_pos is None or n_neg is None \
                or n_pos < class_floor or n_neg < class_floor:
            return VERDICT_INSUFFICIENT
    # 5. degenerate inputs (mean: constant vector → Spearman undefined).
    if degenerate:
        return VERDICT_INDETERMINATE
    # 6. threshold comparison.
    if not statistics:
        # Nothing to compare — cannot license.
        return VERDICT_INSUFFICIENT
    for stat in statistics:
        value = stat["value"]
        threshold = stat["threshold"]
        direction = stat["direction"]
        if direction == "min":
            passed = value >= threshold
        elif direction == "max":
            passed = value <= threshold
        else:
            raise CalibrationRefusal(
                f"unknown statistic direction {direction!r}"
            )
        if not passed:
            return VERDICT_NOT_AGGREGATABLE
    return VERDICT_VALIDATED


# ---------- per-signal evaluation --------------------------------------

def _signal_pairs(
    spec: SignalSpec, rows: list[dict[str, Any]],
) -> tuple[list[float], list[float], int]:
    """Extract (whole_value, segment_aggregate) pairs for one signal.

    A work contributes iff its whole-work cell is available AND at
    least one segment cell is available. Returns (whole_vec, seg_vec,
    support). For not_aggregatable signals only availability is
    counted — values are never converted (the signals are never
    evaluated), so (whole_vec, seg_vec) come back empty.
    """
    whole_vec: list[float] = []
    seg_vec: list[float] = []
    support = 0
    for row in rows:
        cell = row["whole_work"].get(spec.signal_id)
        if cell is None or not cell[1]:
            continue
        available_segments = [
            seg["signals"][spec.signal_id]
            for seg in row["segments"]
            if spec.signal_id in seg["signals"]
            and seg["signals"][spec.signal_id][1]
        ]
        if not available_segments:
            continue
        support += 1
        if spec.operator == OPERATOR_NOT_AGGREGATABLE:
            continue
        if spec.operator == OPERATOR_MEAN:
            whole = convert_mean_response(spec, cell[0])
            seg_values = [
                convert_mean_response(spec, seg_cell[0])
                for seg_cell in available_segments
            ]
        else:  # prevalence
            whole = option_present(spec, cell[0])
            seg_values = [
                option_present(spec, seg_cell[0])
                for seg_cell in available_segments
            ]
        whole_vec.append(whole)
        seg_vec.append(sum(seg_values) / len(seg_values))
    return whole_vec, seg_vec, support


def _evaluate_signal(
    spec: SignalSpec,
    rows: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    """Compute one per_signal receipt cell."""
    floors = thresholds["floors"]
    per_op = thresholds["per_operator"]
    whole_vec, seg_vec, support = _signal_pairs(spec, rows)
    corpus_n_works = len(rows)

    n_pos: int | None = None
    n_neg: int | None = None
    degenerate = False
    statistics: list[dict[str, Any]] = []

    if spec.operator == OPERATOR_MEAN:
        degenerate = (
            len(set(whole_vec)) <= 1 or len(set(seg_vec)) <= 1
        )
        if not degenerate and len(whole_vec) >= 2:
            rho = spearman_rho(whole_vec, seg_vec)
            if rho is None:  # all-tied ranks: constant after ranking
                degenerate = True
            else:
                mad = mean_absolute_deviation(whole_vec, seg_vec)
                statistics = [
                    {
                        "name": "spearman_rho",
                        "value": round(rho, 10),
                        "threshold": round(
                            float(per_op["mean"]["spearman_min"]), 10,
                        ),
                        "direction": "min",
                    },
                    {
                        "name": "mad_response_units",
                        "value": round(mad, 10),
                        "threshold": round(
                            float(
                                per_op["mean"]["mad_max_response_units"],
                            ), 10,
                        ),
                        "direction": "max",
                    },
                ]
    elif spec.operator == OPERATOR_PREVALENCE:
        pos = [s for w, s in zip(whole_vec, seg_vec) if w == 1.0]
        neg = [s for w, s in zip(whole_vec, seg_vec) if w == 0.0]
        n_pos, n_neg = len(pos), len(neg)
        auc = auc_mannwhitney(pos, neg)
        if auc is not None:
            statistics = [
                {
                    "name": "auc",
                    "value": round(auc, 10),
                    "threshold": round(
                        float(per_op["prevalence"]["auc_min"]), 10,
                    ),
                    "direction": "min",
                },
            ]

    verdict = derive_verdict(
        operator=spec.operator,
        corpus_n_works=corpus_n_works,
        support=support,
        floors=floors,
        n_pos=n_pos,
        n_neg=n_neg,
        degenerate=degenerate,
        statistics=statistics or None,
    )
    # statistics are recorded only when the derivation reached the
    # threshold comparison (step 6); every earlier exit records [].
    if verdict not in (VERDICT_VALIDATED, VERDICT_NOT_AGGREGATABLE):
        statistics = []
    if verdict == VERDICT_NOT_AGGREGATABLE \
            and spec.operator == OPERATOR_NOT_AGGREGATABLE:
        statistics = []
    return {
        "verdict": verdict,
        "operator": spec.operator,
        "units": spec.units,
        "support": support,
        "statistics": statistics,
    }


# ---------- receipt assembly --------------------------------------------

def _segment_bands(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, float]]:
    """Compute validated_segment_count_range and
    validated_segment_words from the manifest's segments."""
    counts = [len(row["segments"]) for row in rows]
    words = [
        seg["n_words"] for row in rows for seg in row["segments"]
    ]
    if not words:
        raise CalibrationRefusal(
            "manifest carries no segments; segment bands are "
            "uncomputable"
        )
    words_sorted = sorted(words)
    n = len(words_sorted)
    if n % 2 == 1:
        median = float(words_sorted[n // 2])
    else:
        median = (
            words_sorted[n // 2 - 1] + words_sorted[n // 2]
        ) / 2.0
    return (
        {"min": min(counts), "max": max(counts)},
        {
            "min": min(words),
            "max": max(words),
            "median": median,
        },
    )


def _derivation_sha256(
    *,
    registration_sha256: str,
    manifest_sha256: str,
    per_signal: dict[str, dict[str, Any]],
    segmenter: dict[str, Any],
    judge: dict[str, Any],
) -> str:
    """Exact construction — see the module docstring."""
    supports = [
        [signal_id, per_signal[signal_id]["support"]]
        for signal_id in sorted(per_signal)
    ]
    stats_rows = sorted(
        [
            signal_id,
            stat["name"],
            round(float(stat["value"]), 10),
            round(float(stat["threshold"]), 10),
            stat["direction"],
        ]
        for signal_id, cell in per_signal.items()
        for stat in cell["statistics"]
    )
    preimage = [
        registration_sha256,
        manifest_sha256,
        supports,
        stats_rows,
        {
            "version": segmenter["version"],
            "params_sha256": segmenter["params_sha256"],
            "segment_target_words": segmenter["segment_target_words"],
        },
        {
            "kind": judge["kind"],
            "model": judge["model"],
            "model_revision": judge["model_revision"],
            "prompt_version": judge["prompt_version"],
        },
    ]
    return canonical_json_sha256(preimage)


def build_registration(
    *,
    date: str,
    thresholds_path: Path,
    manifest_path: Path,
    segmenter: dict[str, Any],
    judge: dict[str, Any],
) -> dict[str, Any]:
    """--register: bind thresholds + work-id list + segmenter + judge
    before any judged value exists."""
    _validate_date(date)
    load_thresholds(thresholds_path)  # must be a valid artifact
    rows = load_manifest_rows(manifest_path, values_free=True)
    registration = {
        "schema": REGISTRATION_SCHEMA,
        "date": date,
        "thresholds_sha256": file_sha256(thresholds_path),
        "work_ids_sha256": work_ids_sha256_for_rows(rows),
        "segmenter": _validate_segmenter(segmenter),
        "judge": _validate_judge(judge),
    }
    return registration


def build_receipt(
    *,
    date: str,
    thresholds_path: Path,
    registration_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """--evaluate: compute the full validation receipt.

    Refuses unless the registration's thresholds_sha256 AND
    work_ids_sha256 both match the live inputs.
    """
    _validate_date(date)
    thresholds = load_thresholds(thresholds_path)
    registration = load_registration(registration_path)
    rows = load_manifest_rows(manifest_path, values_free=False)

    thresholds_sha = file_sha256(thresholds_path)
    work_ids_sha = work_ids_sha256_for_rows(rows)
    if registration["thresholds_sha256"] != thresholds_sha:
        raise CalibrationRefusal(
            "registration does not match: thresholds_sha256 "
            f"{registration['thresholds_sha256']} != live "
            f"{thresholds_sha} (post-hoc thresholds refused)"
        )
    if registration["work_ids_sha256"] != work_ids_sha:
        raise CalibrationRefusal(
            "registration does not match: work_ids_sha256 "
            f"{registration['work_ids_sha256']} != live {work_ids_sha}"
        )

    per_signal = {
        signal_id: _evaluate_signal(SIGNALS[signal_id], rows, thresholds)
        for signal_id in SIGNAL_IDS
    }
    count_range, words_band = _segment_bands(rows)
    registration_sha = file_sha256(registration_path)
    manifest_sha = file_sha256(manifest_path)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "date": date,
        "arm": RECEIPT_ARM,
        "signal_id_set_sha256": signal_id_set_sha256(),
        "thresholds_sha256": thresholds_sha,
        "registration_sha256": registration_sha,
        "derivation_sha256": _derivation_sha256(
            registration_sha256=registration_sha,
            manifest_sha256=manifest_sha,
            per_signal=per_signal,
            segmenter=registration["segmenter"],
            judge=registration["judge"],
        ),
        "manifest_sha256": manifest_sha,
        "registration_path": str(registration_path),
        "manifest_path": str(manifest_path),
        "corpus_n_works": len(rows),
        "segmenter": dict(registration["segmenter"]),
        "judge": dict(registration["judge"]),
        "validated_segment_count_range": count_range,
        "validated_segment_words": words_band,
        "per_signal": per_signal,
    }
    assert set(receipt) == _RECEIPT_KEYS
    return receipt


# ---------- verification ---------------------------------------------------

def verify_receipt(
    receipt_path: Path,
    thresholds_path: Path,
    registration_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    """Re-derive everything from the artifacts and refuse on mismatch.

    The verdict strings inside the receipt are NEVER trusted: the
    verdicts, statistics, supports, hashes, bands, and
    derivation_sha256 are all recomputed from (manifest, thresholds,
    registration) and compared field-by-field. Only ``date``,
    ``registration_path``, and ``manifest_path`` are exempt from
    comparison (the date is an operator input and paths may legally
    differ across machines). Returns the verified receipt.
    """
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationRefusal(
            f"cannot read receipt {receipt_path}: {exc}"
        ) from exc
    if not isinstance(receipt, dict):
        raise CalibrationRefusal("receipt must be a JSON object")
    if set(receipt) != _RECEIPT_KEYS:
        missing = sorted(_RECEIPT_KEYS - set(receipt))
        extra = sorted(set(receipt) - _RECEIPT_KEYS)
        raise CalibrationRefusal(
            "receipt key set mismatch"
            + (f"; missing {missing}" if missing else "")
            + (f"; unexpected {extra}" if extra else "")
        )
    if receipt["schema_version"] != RECEIPT_SCHEMA:
        raise CalibrationRefusal(
            f"receipt schema_version must be {RECEIPT_SCHEMA!r}; got "
            f"{receipt['schema_version']!r}"
        )
    date = receipt["date"]
    _validate_date(date)
    expected = build_receipt(
        date=date,
        thresholds_path=thresholds_path,
        registration_path=registration_path,
        manifest_path=manifest_path,
    )
    exempt = {"date", "registration_path", "manifest_path"}
    for key in sorted(_RECEIPT_KEYS - exempt):
        if receipt[key] != expected[key]:
            raise CalibrationRefusal(
                f"receipt field {key!r} does not re-derive from the "
                f"artifacts: receipt has {receipt[key]!r}, re-derived "
                f"{expected[key]!r}"
            )
    return receipt


# ---------- CLI -------------------------------------------------------------

def _validate_date(date: Any) -> str:
    if not isinstance(date, str):
        raise CalibrationRefusal("--date must be an ISO date string")
    try:
        parsed = _datetime.date.fromisoformat(date)
    except ValueError as exc:
        raise CalibrationRefusal(
            f"--date must be ISO YYYY-MM-DD; got {date!r}"
        ) from exc
    if parsed.isoformat() != date:
        raise CalibrationRefusal(
            f"--date must be canonical ISO YYYY-MM-DD; got {date!r}"
        )
    return date


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Whole-vs-segmented agreement study for the narrative "
            "long-form extension (spec 77 M1; judge-free, precomputed "
            "values)."
        ),
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--registration", type=Path)
    parser.add_argument(
        "--out", type=Path, required=True,
        help=(
            "Output path (--register: registration; --evaluate: "
            "receipt). In --verify mode this names the receipt to "
            "verify."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--evaluate", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--date",
        help=(
            "ISO date stamped into the artifact (required for "
            "--register/--evaluate; never read from the clock)."
        ),
    )
    parser.add_argument("--segmenter-version")
    parser.add_argument("--segmenter-params-sha256")
    parser.add_argument("--segment-target-words", type=int)
    parser.add_argument("--judge-kind")
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-model-revision")
    parser.add_argument("--judge-prompt-version")
    args = parser.parse_args(argv)

    try:
        if args.register:
            if args.date is None:
                raise CalibrationRefusal("--register requires --date")
            identity_flags = {
                "--segmenter-version": args.segmenter_version,
                "--segmenter-params-sha256": args.segmenter_params_sha256,
                "--segment-target-words": args.segment_target_words,
                "--judge-kind": args.judge_kind,
                "--judge-model": args.judge_model,
                "--judge-model-revision": args.judge_model_revision,
                "--judge-prompt-version": args.judge_prompt_version,
            }
            missing = sorted(
                flag for flag, v in identity_flags.items() if v is None
            )
            if missing:
                raise CalibrationRefusal(
                    f"--register requires {', '.join(missing)}"
                )
            registration = build_registration(
                date=args.date,
                thresholds_path=args.thresholds,
                manifest_path=args.manifest,
                segmenter={
                    "version": args.segmenter_version,
                    "params_sha256": args.segmenter_params_sha256,
                    "segment_target_words": args.segment_target_words,
                },
                judge={
                    "kind": args.judge_kind,
                    "model": args.judge_model,
                    "model_revision": args.judge_model_revision,
                    "prompt_version": args.judge_prompt_version,
                },
            )
            _write_json(args.out, registration)
            print(f"registration → {args.out}")
            return 0
        if args.evaluate:
            if args.date is None:
                raise CalibrationRefusal("--evaluate requires --date")
            if args.registration is None:
                raise CalibrationRefusal(
                    "--evaluate requires --registration"
                )
            receipt = build_receipt(
                date=args.date,
                thresholds_path=args.thresholds,
                registration_path=args.registration,
                manifest_path=args.manifest,
            )
            _write_json(args.out, receipt)
            verdicts: dict[str, int] = {}
            for cell in receipt["per_signal"].values():
                verdicts[cell["verdict"]] = (
                    verdicts.get(cell["verdict"], 0) + 1
                )
            print(f"receipt → {args.out}")
            print(
                "verdicts: " + ", ".join(
                    f"{k}={v}" for k, v in sorted(verdicts.items())
                )
            )
            return 0
        # --verify
        if args.registration is None:
            raise CalibrationRefusal("--verify requires --registration")
        verify_receipt(
            args.out, args.thresholds, args.registration, args.manifest,
        )
        print(f"receipt verified: {args.out}")
        return 0
    except CalibrationRefusal as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
