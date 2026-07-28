#!/usr/bin/env python3
"""narrative_decision_long_form.py — spec 79 M1: StoryScope long-form extension.

Score a work above the narrative-decision audit's 25,000-word ceiling by
deterministic segmentation (``narrative_longform_segment``) plus one base-audit
scoring pass per segment. M1 ships **no validation receipt** — none exists yet —
so every work-level aggregate is suppressed: ``per_signal_aggregates[*].value``
is null with status ``provisional_unvalidated`` (or ``not_aggregatable`` for
signals a mid-work fragment cannot answer). The real M1 payload is the
per-segment raw responses.

What this surface deliberately does NOT do in M1:

  * no receipt matching, no thresholds, no verdict derivation — a later
    increment owns licensing. The envelope is shaped so a receipt CAN later
    license without reshaping it (``validation_binding.match`` fills in;
    ``per_signal_aggregates[*].value``/``status`` flip per signal).
  * no ``contribution``, no ``target_value``, no work-level scalar of any
    kind. ``assert_no_work_level_reduction`` enforces this mechanically at
    emit time on the real results dict: no float leaf anywhere; int leaves
    only under count-shaped keys.

Judge provenance (S2, M1 subset):

  * ``manifest`` on a segmented run must be **keyed by segment content
    hash**: a JSON object whose top-level keys are the segments'
    ``content_sha256`` values ("sha256:..." strings), each value being the
    flat manifest shape the base judge expects. This module resolves the
    per-segment entry and hands the unchanged base judge a flat manifest.
    A flat (non-keyed) manifest on a segmented run refuses (``bad_input``);
    a missing segment key refuses (``bad_input``).
  * ``mock`` refuses (``policy_refused``) on any non-calibration run. It IS
    allowed under ``--calibration-emit-segments`` (calibration fixtures need
    it) — the envelope then carries ``judge_kind: "mock"`` prominently.
  * degenerate-judge tripwire: the ``manifest`` backend is text-blind by
    construction, so a flat manifest reused across segments yields identical
    vectors. If >= 3 segments produce byte-identical per-signal value
    vectors, the run refuses (``policy_refused``). Applies to every judge
    kind on scoring runs; calibration-only runs are exempt (they are
    mechanically ineligible for any claim, and the deterministic mock judge
    would otherwise always trip it).

Resume cache (``--cache-dir``): per-segment results cache under a key that
binds the segment content hash to the judge identity (kind, model,
model_revision, prompt_version), the RESOLVED JUDGE INPUT (for a manifest
judge, the framed digest of that segment's manifest entry — editing a
response must miss, not hit stale), the segmenter ``params_sha256``, and the
base-audit identity (script version + prompt fingerprint). Content hash alone
is NOT the key — a rerun under a different judge or prompt version must miss.

A cache entry is NEVER emitted on the strength of its filename. Every load
re-verifies, and refuses (``bad_input``) rather than falling back to a
recompute: the stored ``binding`` block must equal the live binding
field-for-field; the stored ``signals`` map must carry exactly the 33 signal
ids with exact leaf types and responses drawn from the feature schema's closed
vocabularies; and the stored ``values_vector`` — the string the degenerate-
judge tripwire counts — must re-derive from those signals. A hand-edited cache
file therefore cannot smuggle a forged vector past the tripwire, and a stale
entry cannot outlive the manifest it came from.

CLI (flat, no subcommands):

    python3 narrative_decision_long_form.py TARGET \
        [--segment-target-words N] [--calibration-emit-segments] \
        [--json] [--out PATH] [--cache-dir DIR] \
        --judge manifest --judge-manifest keyed.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path
from types import MappingProxyType
from typing import Any

import narrative_decision_audit as nda  # type: ignore
import narrative_judge as nj  # type: ignore
import narrative_longform_segment as nls  # type: ignore
from claim_license import ClaimLicense  # type: ignore
from narrative_feature_schema import (  # type: ignore
    BUNDLE_LABELS,
    CORE_FEATURES,
    CoreFeature,
    FeatureSignal,
)
from output_schema import (  # type: ignore
    REASON_CATEGORIES,
    build_error_output,
    build_output,
)

__all__ = [
    "TASK_SURFACE",
    "SCRIPT_VERSION",
    "signal_id_for",
    "all_signal_ids",
    "OPERATOR_TABLE",
    "OPERATOR_MEAN",
    "OPERATOR_PREVALENCE",
    "OPERATOR_NOT_AGGREGATABLE",
    "WorkLevelReductionError",
    "assert_no_work_level_reduction",
    "main",
]

TASK_SURFACE = "narrative_decision_long_form"
TOOL_NAME = "narrative_decision_long_form"
SCRIPT_VERSION = "0.1.0"

# The base audit's advisory ceiling; at or below it the base audit is the
# right surface and this one refuses (unless --calibration-emit-segments).
CEILING_WORDS = nls.CEILING_WORDS  # 25_000

# >= this many byte-identical per-signal value vectors across segments
# trips the degenerate-judge refusal on a scoring run.
DEGENERATE_VECTOR_MIN = 3

SUPPRESSION_REASON_M1 = "provisional_unvalidated"


# ---------- S1: signal identity --------------------------------------

def signal_id_for(feature: CoreFeature, signal: FeatureSignal) -> str:
    """Sole signal-id derivation for the long-form surface.

    ``narrative.{bundle}.{feature_key}`` when the signal's option is None
    (19 signals), else ``narrative.{bundle}.{feature_key}.{option}`` (14
    signals). Lives here rather than in the schema module so the base
    surface stays byte-identical in this increment.
    """
    if signal.option is None:
        return f"narrative.{signal.bundle}.{feature.key}"
    return f"narrative.{signal.bundle}.{feature.key}.{signal.option}"


def all_signal_ids() -> list[str]:
    """The 33 signal ids in schema order."""
    return [
        signal_id_for(f, s) for f in CORE_FEATURES for s in f.signals
    ]


# ---------- aggregation operator table (all 33, explicit) ------------

OPERATOR_MEAN = "mean"
OPERATOR_PREVALENCE = "prevalence"
OPERATOR_NOT_AGGREGATABLE = "not_aggregatable"

# Keyed by (feature_key, option). Explicit, total over the real
# CORE_FEATURES (a test asserts disjointness AND totality), frozen.
#
#   not_aggregatable (12): ending/resolution mechanics, subplot structure,
#     global temporal structure, position-anchored, whole-work-scope
#     signals — a mid-work fragment cannot answer a frozen whole-work
#     prompt, so these must never be averaged into a work-level claim.
#   mean (12): the remaining option=None scale/ordinal signals.
#   prevalence (9): the remaining option-bearing signals, as a fraction of
#     contributing segments.
OPERATOR_TABLE: MappingProxyType = MappingProxyType({
    # --- not_aggregatable a priori (12) ---
    ("mode_of_resolution", "resolved_internally"): OPERATOR_NOT_AGGREGATABLE,
    ("agency_in_resolution", "protagonist_choice"): OPERATOR_NOT_AGGREGATABLE,
    ("subplot_integration", "no_subplots"): OPERATOR_NOT_AGGREGATABLE,
    ("subplot_integration", "thematically_parallel"): OPERATOR_NOT_AGGREGATABLE,
    ("anachrony_intensity", None): OPERATOR_NOT_AGGREGATABLE,
    ("degree_of_chronological_discontinuity", None): OPERATOR_NOT_AGGREGATABLE,
    ("nonlinear_framing_for_delayed_disclosure", None): OPERATOR_NOT_AGGREGATABLE,
    ("depth_of_recontextualization_after_surprise", None): OPERATOR_NOT_AGGREGATABLE,
    ("opening_spatial_grounding", None): OPERATOR_NOT_AGGREGATABLE,
    ("character_introduction", "external_description"): OPERATOR_NOT_AGGREGATABLE,
    ("pre_threat_character_investment", None): OPERATOR_NOT_AGGREGATABLE,
    ("location_variety_scope", None): OPERATOR_NOT_AGGREGATABLE,
    # --- mean (12): remaining option=None ---
    ("thematic_explicitness_and_moralizing", None): OPERATOR_MEAN,
    ("moral_philosophical_weighting", None): OPERATOR_MEAN,
    ("thematic_unity", None): OPERATOR_MEAN,
    ("setting_as_psychological_mirror", None): OPERATOR_MEAN,
    ("environmental_ecological_emphasis", None): OPERATOR_MEAN,
    ("sensory_density", None): OPERATOR_MEAN,
    ("depth_of_interior_access", None): OPERATOR_MEAN,
    ("continuity_of_main_causal_chain", None): OPERATOR_MEAN,
    ("spatial_granularity_level", None): OPERATOR_MEAN,
    ("fourth_wall_permeability", None): OPERATOR_MEAN,
    ("frequency_of_direct_reader_address", None): OPERATOR_MEAN,
    ("dialogue_to_narration_proportion", None): OPERATOR_MEAN,
    # --- prevalence (9): remaining option-bearing ---
    ("narratorial_thematic_commentary", "yes"): OPERATOR_PREVALENCE,
    ("dialogue_function", "philosophical_debate"): OPERATOR_PREVALENCE,
    ("reference_explicitness", "implicit_echoes"): OPERATOR_PREVALENCE,
    ("reference_explicitness", "balanced_mix"): OPERATOR_PREVALENCE,
    ("dominant_emotional_expression", "embodied_metaphors"): OPERATOR_PREVALENCE,
    ("dominant_emotional_expression", "explicit_labels"): OPERATOR_PREVALENCE,
    ("dominant_sensory_modalities", "olfactory"): OPERATOR_PREVALENCE,
    ("intertextual_strategy_types", "explicit_named"): OPERATOR_PREVALENCE,
    ("moral_polarity_toward_protagonist", "ambivalent_or_mixed"): OPERATOR_PREVALENCE,
})


def operator_for(feature: CoreFeature, signal: FeatureSignal) -> str:
    return OPERATOR_TABLE[(feature.key, signal.option)]


# ---------- emit guard ------------------------------------------------

# Modeled on setec_run_set.assert_no_aggregate_verdict (rules 1 and 2
# reused in kind), with rule 3 replaced: floats forbidden everywhere; ints
# allowed only under count-shaped keys.
FORBIDDEN_REDUCTION_KEYS: frozenset[str] = frozenset({
    "score", "aggregate", "verdict_band", "contribution",
    "target_value", "mean_contribution", "weighted_delta",
})
FORBIDDEN_REDUCTION_SUBSTRINGS: tuple[str, ...] = ("verdict", "composite")

# Exact int-bearing keys allowed besides the n_* prefix convention.
ALLOWED_INT_KEYS: frozenset[str] = frozenset({
    "index", "segment_target_words", "start", "end",
})


class WorkLevelReductionError(RuntimeError):
    """Raised when the long-form results payload carries a work-level
    reduction: a banned key, a float leaf anywhere, or an int leaf outside
    the closed count allowlist."""


def assert_no_work_level_reduction(node: Any, _key: str = "") -> None:
    """Recursive guard, run at emit time on the real ``results`` dict.

    Rules:
      1. any dict KEY in ``FORBIDDEN_REDUCTION_KEYS`` (exact, case-folded)
         at any depth raises;
      2. any dict KEY containing a ``FORBIDDEN_REDUCTION_SUBSTRINGS`` token
         (case-folded) raises;
      3. floats are forbidden EVERYWHERE; ints are allowed only for keys
         named ``n_*``, ``index``, ``segment_target_words``, ``start``,
         ``end``. bools are skipped (never a metric). Per-segment judge
         responses therefore stay strings exactly as the judge returned
         them.
    """
    if isinstance(node, bool):
        return
    if isinstance(node, float):
        raise WorkLevelReductionError(
            f"no-reduction invariant: float leaf {node!r} at key {_key!r} "
            f"(this surface emits no float anywhere; responses stay "
            f"strings)"
        )
    if isinstance(node, int):
        k = _key.lower()
        if not (k.startswith("n_") or k in ALLOWED_INT_KEYS):
            raise WorkLevelReductionError(
                f"no-reduction invariant: int leaf {node!r} at "
                f"non-count key {_key!r} (ints only under n_*, index, "
                f"segment_target_words, start, end)"
            )
        return
    if isinstance(node, dict):
        for k, v in node.items():
            k_lower = str(k).lower()
            if k_lower in FORBIDDEN_REDUCTION_KEYS:
                raise WorkLevelReductionError(
                    f"forbidden reduction key {k!r} in long-form results "
                    f"(no work-level scalar, no verdict — ever)"
                )
            for sub in FORBIDDEN_REDUCTION_SUBSTRINGS:
                if sub in k_lower:
                    raise WorkLevelReductionError(
                        f"key {k!r} contains forbidden substring {sub!r} "
                        f"in long-form results"
                    )
            assert_no_work_level_reduction(v, str(k))
        return
    if isinstance(node, (list, tuple)):
        for item in node:
            assert_no_work_level_reduction(item, _key)
        return
    # str / None: nothing to check.


# ---------- refusals --------------------------------------------------

# Exit-code convention, extending the base audit's (0 success; 1 bad
# input; 2 argparse/judge-construction usage errors — and setec_run's
# privacy-ratchet bucket for policy refusals; 3 judge-execution/internal
# failure).
_EXIT_BY_CATEGORY = {
    "version_floor": 1,
    "missing_dependency": 1,
    "bad_input": 1,
    "text_too_short": 1,
    "policy_refused": 2,
    "internal_error": 3,
}


class _Refusal(Exception):
    def __init__(self, category: str, reason: str) -> None:
        if category not in REASON_CATEGORIES:
            raise ValueError(f"unknown refusal category {category!r}")
        super().__init__(reason)
        self.category = category
        self.reason = reason


# ---------- per-segment scoring ---------------------------------------

def _base_audit_identity() -> str:
    """Base-audit identity component of the cache key.

    The base audit exposes a module-level SCRIPT_VERSION and (via
    narrative_judge) a canonical prompt fingerprint; bind both. Fall back
    to a hash of the module's file bytes only if the version constant ever
    disappears.
    """
    version = getattr(nda, "SCRIPT_VERSION", None)
    prompt_fp = nj.fingerprint_prompt()
    if version:
        return f"narrative_decision_audit/{version}+prompt:{prompt_fp}"
    return nls.framed_digest(
        nls.DOMAIN_BASE_AUDIT_SOURCE, Path(nda.__file__).read_bytes()
    )


def _judge_input_digest(judge_kind: str, entry: Any) -> str | None:
    """Framed digest of the judge input actually resolved for a segment.

    For ``manifest`` this is the per-segment entry: two runs that agree on
    content hash and declared identity but disagree on the recorded responses
    are DIFFERENT runs, and a cache keyed without this returns the first run's
    answers for the second run's manifest.
    """
    if judge_kind != "manifest":
        return None
    return nls.framed_object_digest(nls.DOMAIN_JUDGE_INPUT, entry)


def _cache_binding(
    *,
    content_sha256: str,
    judge_kind: str,
    identity: dict[str, Any],
    params_sha256: str,
    judge_input_sha256: str | None,
) -> dict[str, Any]:
    """The closed set of facts a cached per-segment result is valid for."""
    return {
        "segment_content_sha256": content_sha256,
        "judge_kind": judge_kind,
        "judge_model": identity.get("model"),
        "judge_model_revision": identity.get("model_revision"),
        "judge_prompt_version": identity.get("prompt_version"),
        "judge_input_sha256": judge_input_sha256,
        "segmenter_params_sha256": params_sha256,
        "base_audit_identity": _base_audit_identity(),
    }


def _cache_key(binding: dict[str, Any]) -> str:
    """Cache key over the whole binding. Content hash ALONE is forbidden —
    a rerun under a different judge, prompt version, or manifest entry must
    MISS."""
    return nls.framed_object_digest(
        nls.DOMAIN_CACHE_KEY, binding
    ).split(":", 1)[1]


_CACHE_PAYLOAD_KEYS = frozenset({
    "binding", "signals", "register_warnings", "validation_warnings",
    "values_vector",
})


def _values_vector(cleaned: dict[str, Any]) -> str:
    """The canonical string the degenerate-judge tripwire counts."""
    return json.dumps(cleaned, sort_keys=True, separators=(",", ":"))


def _cleaned_from_signals(signals: dict[str, Any]) -> dict[str, Any]:
    """Invert ``_signals_map``: recover the per-feature cleaned values.

    Every signal of a feature carries that feature's response, so the
    inversion is total and the round trip is checkable.
    """
    cleaned: dict[str, Any] = {}
    for f in CORE_FEATURES:
        sid = signal_id_for(f, f.signals[0])
        cleaned[f.key] = signals[sid]["response"]
    return cleaned


def _validate_cached_signals(signals: Any, where: str) -> None:
    """Exact-shape, exact-type, closed-vocabulary re-validation.

    Applied to a cached payload at LOAD, not merely at store: an entry that
    was written correctly and edited afterwards is exactly the case a
    store-time check cannot see.
    """
    if not isinstance(signals, dict):
        raise _Refusal("bad_input", f"{where}: 'signals' must be an object")
    expected = set(all_signal_ids())
    if set(signals) != expected:
        missing = sorted(expected - set(signals))[:3]
        extra = sorted(set(signals) - expected)[:3]
        raise _Refusal(
            "bad_input",
            f"{where}: 'signals' key set does not match the 33 schema "
            f"signal ids (missing {missing}, unexpected {extra})",
        )
    for f in CORE_FEATURES:
        options = set(f.response_options)
        responses = []
        for s in f.signals:
            cell = signals[signal_id_for(f, s)]
            if not isinstance(cell, dict) or set(cell) != {
                "response", "available"
            }:
                raise _Refusal(
                    "bad_input",
                    f"{where}: signal cell for {f.key!r} must have exactly "
                    f"keys {{response, available}}",
                )
            if not isinstance(cell["available"], bool):
                raise _Refusal(
                    "bad_input",
                    f"{where}: 'available' for {f.key!r} must be a bool",
                )
            response = cell["response"]
            if response is None:
                pass
            elif f.feature_type == "multi":
                if not isinstance(response, list) or not all(
                    isinstance(x, str) for x in response
                ):
                    raise _Refusal(
                        "bad_input",
                        f"{where}: multi-select {f.key!r} needs a list of "
                        f"strings",
                    )
                illegal = [x for x in response if x not in options]
                if illegal:
                    raise _Refusal(
                        "bad_input",
                        f"{where}: {f.key!r} carries options outside the "
                        f"schema vocabulary: {illegal[:3]}",
                    )
            else:
                if not isinstance(response, str) or response not in options:
                    raise _Refusal(
                        "bad_input",
                        f"{where}: {f.key!r} response {response!r} is not "
                        f"one of the feature's options",
                    )
            if cell["available"] != (response is not None):
                raise _Refusal(
                    "bad_input",
                    f"{where}: 'available' for {f.key!r} contradicts its "
                    f"response",
                )
            responses.append(response)
        if any(r != responses[0] for r in responses[1:]):
            raise _Refusal(
                "bad_input",
                f"{where}: signals of feature {f.key!r} disagree; a "
                f"per-feature response cannot differ across its own signals",
            )


def _cache_load(
    cache_dir: Path | None, key: str, binding: dict[str, Any]
) -> dict[str, Any] | None:
    """Load a cached per-segment result, re-verified against ``binding``.

    Returns None only when nothing is cached. Anything present but not
    re-verifiable REFUSES — silently recomputing would let a tampered entry
    disappear without a trace, and silently emitting it is the defect.
    """
    if cache_dir is None:
        return None
    path = cache_dir / f"{key}.json"
    if not path.is_file():
        return None
    where = f"cache entry {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _Refusal("bad_input", f"{where}: cannot read ({exc})")
    except json.JSONDecodeError as exc:
        raise _Refusal(
            "bad_input",
            f"{where}: invalid JSON ({exc}); delete the cache directory to "
            f"recompute",
        )
    if not isinstance(payload, dict) or set(payload) != _CACHE_PAYLOAD_KEYS:
        raise _Refusal(
            "bad_input",
            f"{where}: payload must be an object with exactly "
            f"{sorted(_CACHE_PAYLOAD_KEYS)}",
        )
    if payload["binding"] != binding:
        raise _Refusal(
            "bad_input",
            f"{where}: recorded binding does not match this run's segment "
            f"content / judge identity / judge input / segmenter params / "
            f"base-audit identity",
        )
    _validate_cached_signals(payload["signals"], where)
    for k in ("register_warnings", "validation_warnings"):
        v = payload[k]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise _Refusal(
                "bad_input", f"{where}: {k!r} must be a list of strings"
            )
    expected_vector = _values_vector(
        _cleaned_from_signals(payload["signals"])
    )
    if payload["values_vector"] != expected_vector:
        raise _Refusal(
            "bad_input",
            f"{where}: 'values_vector' does not re-derive from 'signals' "
            f"(the tripwire input must be a function of the emitted "
            f"responses, not an independent assertion)",
        )
    return payload


def _cache_store(
    cache_dir: Path | None, key: str, payload: dict[str, Any]
) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{key}.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )


def _signals_map(cleaned: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-signal {response, available} from a segment's cleaned judge
    values. Responses stay exactly as the judge returned them (a string,
    or a list of strings for multi features); no encoding, no numerics."""
    out: dict[str, dict[str, Any]] = {}
    for f in CORE_FEATURES:
        v = cleaned.get(f.key)
        for s in f.signals:
            out[signal_id_for(f, s)] = {
                "response": list(v) if isinstance(v, list) else v,
                "available": v is not None,
            }
    return out


_IDENTITY_FIELDS = ("model", "model_revision", "prompt_version")


def _manifest_identity(entry: Any, where: str) -> dict[str, Any]:
    """Judge identity for one manifest entry, with EXACT types.

    Each field is a non-empty string or null; anything else refuses here
    rather than surfacing later as a ``TypeError`` from set construction (an
    unhashable ``model: []``) or as an uncaught reduction error (``model: 7``
    reaching the emit guard as a stray int leaf).
    """
    if not isinstance(entry, dict):
        raise _Refusal(
            "bad_input",
            f"{where}: manifest entry must be a JSON object, got "
            f"{type(entry).__name__}",
        )
    ji = entry.get("judge_identity")
    if ji is None:
        ji = {}
    if not isinstance(ji, dict):
        raise _Refusal(
            "bad_input",
            f"{where}: 'judge_identity' must be a JSON object, got "
            f"{type(ji).__name__}",
        )
    identity: dict[str, Any] = {}
    for field in _IDENTITY_FIELDS:
        value = ji.get(field)
        if value is None:
            identity[field] = None
            continue
        if not isinstance(value, str):
            raise _Refusal(
                "bad_input",
                f"{where}: judge_identity.{field} must be a string or null, "
                f"got {type(value).__name__}",
            )
        if not value.strip():
            raise _Refusal(
                "bad_input",
                f"{where}: judge_identity.{field} is empty; declare a "
                f"concrete identity or omit the field",
            )
        identity[field] = value
    return identity


def _load_keyed_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _Refusal("bad_input", f"manifest {path}: cannot read ({exc})")
    except json.JSONDecodeError as exc:
        raise _Refusal(
            "bad_input", f"manifest {path}: invalid JSON ({exc})"
        )
    if not isinstance(data, dict):
        raise _Refusal(
            "bad_input",
            f"manifest {path}: top level must be a JSON object keyed by "
            f"segment content_sha256, got {type(data).__name__}",
        )
    if "values" in data:
        raise _Refusal(
            "bad_input",
            f"manifest {path}: flat (non-keyed) manifest on a segmented "
            f"run. A long-form manifest must be a JSON object whose "
            f"top-level keys are segment content_sha256 values "
            f"('sha256:...'), each value the flat manifest shape the "
            f"base judge expects.",
        )
    return data


def _score_segments(
    *,
    seg: "nls.Segmentation",
    text: str,
    judge_kind: str,
    keyed_manifest: dict[str, Any] | None,
    shared_judge: Any | None,
    api_identity: dict[str, Any] | None,
    cache_dir: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    """Score every segment through the base audit's judge + validation
    path. Returns (per-segment payloads, per-segment judge identities,
    n_cache_hits, n_cache_misses)."""
    payloads: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    n_hits = 0
    n_misses = 0
    with tempfile.TemporaryDirectory(
        prefix="ndlf_manifest_"
    ) as tmp_dir:
        for s in seg.segments:
            entry: Any = None
            if judge_kind == "manifest":
                assert keyed_manifest is not None
                entry = keyed_manifest[s.content_sha256]
                identity = _manifest_identity(
                    entry,
                    f"segment {s.index} ({s.content_sha256})",
                )
            elif judge_kind == "mock":
                identity = {
                    "model": "mock", "model_revision": None,
                    "prompt_version": None,
                }
            else:
                assert api_identity is not None
                identity = dict(api_identity)
            identities.append(identity)

            binding = _cache_binding(
                content_sha256=s.content_sha256,
                judge_kind=judge_kind,
                identity=identity,
                params_sha256=seg.params_sha256,
                judge_input_sha256=_judge_input_digest(judge_kind, entry),
            )
            key = _cache_key(binding)
            cached = _cache_load(cache_dir, key, binding)
            if cached is not None:
                n_hits += 1
                payloads.append(cached)
                continue
            n_misses += 1

            seg_text = s.text(text)
            if judge_kind == "manifest":
                # Hand the UNCHANGED base judge a flat per-segment
                # manifest, so its own shape validation applies verbatim.
                flat_path = Path(tmp_dir) / f"segment_{s.index}.json"
                flat_path.write_text(
                    json.dumps(entry), encoding="utf-8"
                )
                try:
                    judge = nj.build_judge(
                        "manifest", manifest_path=flat_path
                    )
                except nj.JudgeError as exc:
                    raise _Refusal(
                        "bad_input",
                        f"segment {s.index} "
                        f"({s.content_sha256}): manifest entry rejected "
                        f"by the base judge: {exc}",
                    )
            else:
                assert shared_judge is not None
                judge = shared_judge

            try:
                result = judge(seg_text)
            except nj.JudgeError as exc:
                raise _Refusal(
                    "internal_error",
                    f"judge execution failed on segment {s.index} "
                    f"({s.content_sha256}): {exc}",
                )
            cleaned, validation_warnings = nj.validate_values(
                result.values
            )
            payload = {
                "binding": binding,
                "signals": _signals_map(cleaned),
                "register_warnings": list(nda.register_warnings_for(
                    seg_text, nda.count_words(seg_text)
                )),
                "validation_warnings": list(validation_warnings),
                # tripwire input + cache round-trip check; a function of
                # `signals`, never an independent assertion.
                "values_vector": _values_vector(cleaned),
            }
            _cache_store(cache_dir, key, payload)
            payloads.append(payload)
    return payloads, identities, n_hits, n_misses


# ---------- results assembly ------------------------------------------

def _per_segment_block(
    seg: "nls.Segmentation",
    payloads: list[dict[str, Any]],
    *,
    calibration_only: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s, payload in zip(seg.segments, payloads):
        entry: dict[str, Any] = {
            "index": s.index,
            "content_sha256": s.content_sha256,
            "signals": payload["signals"],
            "register_warnings": list(payload["register_warnings"]),
            # The base judge's schema validation drops out-of-vocabulary
            # options and nulls out-of-vocabulary scalars; carrying its
            # warnings per segment is what makes that cleaning auditable
            # rather than invisible.
            "validation_warnings": list(payload["validation_warnings"]),
            # Spec 79's mechanical residue against the reconstruction
            # limit: reducing these responses to a work-level scalar is
            # derivable and is NOT licensed, stated on every block.
            "reduction_licensed": False,
        }
        if calibration_only:
            entry["calibration_only"] = True
        out.append(entry)
    return out


def _per_signal_aggregates_block(
    payloads: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    n_total = len(payloads)
    out: dict[str, dict[str, Any]] = {}
    for f in CORE_FEATURES:
        for s in f.signals:
            sid = signal_id_for(f, s)
            op = operator_for(f, s)
            contributing = sum(
                1 for p in payloads if p["signals"][sid]["available"]
            )
            out[sid] = {
                "status": (
                    OPERATOR_NOT_AGGREGATABLE
                    if op == OPERATOR_NOT_AGGREGATABLE
                    else SUPPRESSION_REASON_M1
                ),
                "operator": op,
                # M1: every work-level value is suppressed — null, never
                # 0.0, never omitted. A later increment flips this only
                # under a matching validation receipt.
                "value": None,
                "coverage": {
                    "n_segments_contributing": contributing,
                    "n_segments_total": n_total,
                },
            }
    return out


def _per_bundle_block() -> dict[str, dict[str, Any]]:
    """Per-class sub-rollups, all values null in M1. No cross-class
    number exists even as a slot: mean and prevalence classes roll up
    separately, each with its own units."""
    out: dict[str, dict[str, Any]] = {}
    for bundle in BUNDLE_LABELS:
        mean_ids: list[str] = []
        prevalence_ids: list[str] = []
        excluded_ids: list[str] = []
        for f in CORE_FEATURES:
            for s in f.signals:
                if s.bundle != bundle:
                    continue
                sid = signal_id_for(f, s)
                op = operator_for(f, s)
                if op == OPERATOR_MEAN:
                    mean_ids.append(sid)
                elif op == OPERATOR_PREVALENCE:
                    prevalence_ids.append(sid)
                else:
                    excluded_ids.append(sid)
        out[bundle] = {
            "mean_class": {
                "value": None,
                "dispersion": None,
                "units": "response_units",
                "n_signals": len(mean_ids),
                "n_validated": 0,
            },
            "prevalence_class": {
                "value": None,
                "dispersion": None,
                "units": "prevalence",
                "n_signals": len(prevalence_ids),
                "n_validated": 0,
            },
            "excluded_signal_ids": excluded_ids,
            "basis": "longform_validated_subset",
        }
    return out


# Spec 79 S3's enumerated required-match fields, in spec order. Every one is
# emitted with an explicit verdict, because an ABSENT key and a key whose
# value is "absent" read the same to a machine and opposite to a person: an
# empty `match` object cannot be distinguished from a match object that
# forgot a field.
REQUIRED_MATCH_FIELDS: tuple[str, ...] = (
    "signal_id_set_sha256",
    "segmenter.version",
    "segmenter.params_sha256",
    "segmenter.segment_target_words",
    "judge.kind",
    "judge.model",
    "judge.model_revision",
    "judge.prompt_version",
    "validated_segment_count_range",
    "validated_segment_words",
)

MATCH_ABSENT = "absent"


def _validation_binding_block() -> dict[str, Any]:
    """M1: no receipt exists; every run is unlicensed by construction."""
    return {
        "receipt_present": False,
        "receipt_path": None,
        "receipt_sha256": None,
        "match": {field: MATCH_ABSENT for field in REQUIRED_MATCH_FIELDS},
        "licensed": False,
        "suppression_reason": SUPPRESSION_REASON_M1,
    }


# ---------- claim licenses --------------------------------------------

M1_LICENSES = (
    "Per-segment responses for the 33 StoryScope narrative-decision "
    "signals (Russell et al. 2026, arXiv:2604.03136v4) over a "
    "deterministic, hash-bound segmentation of a work above the base "
    "audit's 25,000-word ceiling: for each segment, the judge's response "
    "per signal AFTER the base audit's schema validation "
    "(narrative_judge.validate_values), plus availability, and the "
    "segmentation record (tier, per-segment word counts, boundary and "
    "parameter hashes). That validation is not a pass-through: an option "
    "outside a multi-select feature's closed vocabulary is DROPPED from "
    "the emitted list, a scalar outside its feature's vocabulary is "
    "emitted as null with available=false, and a missing feature is "
    "emitted as null — each such edit recorded verbatim in the segment's "
    "validation_warnings. No re-encoding, no numeric conversion, and no "
    "other rewriting occurs. Distributional, per-segment description only."
)

M1_DOES_NOT_LICENSE = (
    "Does not license ANY work-level aggregation: no validation receipt "
    "exists in M1, every per_signal_aggregates value is suppressed as "
    "provisional_unvalidated (value: null), and aggregating the "
    "per-segment responses yourself is outside this license. Does not "
    "license any AI/human provenance verdict, likeness, or author "
    "reading, at segment or work level. Reducing per-segment responses "
    "to contributions or a work-level scalar via the public "
    "narrative_decision_audit.signal_target_value function is "
    "mechanically derivable from what this surface emits and is "
    "explicitly OUTSIDE the license — derivation is possible, not "
    "prevented, and unsupported by any validation in this increment."
)

CALIBRATION_LICENSES = (
    "Calibration-fixture output only: the deterministic segmentation "
    "record and per-segment judge responses as validated by "
    "narrative_judge.validate_values (out-of-vocabulary options dropped, "
    "out-of-vocabulary scalars nulled, every edit recorded in the "
    "segment's validation_warnings), stamped calibration_only, for "
    "building the spec-79 calibration corpus."
)

CALIBRATION_DOES_NOT_LICENSE = (
    "Refuses ALL evidentiary use. calibration_only output licenses no "
    "claim about the work, its authorship, provenance, style, or any "
    "signal distribution — it exists solely to assemble calibration "
    "fixtures, and any appearance of this envelope in an evidentiary "
    "chain is a misuse. Also refuses everything the scoring surface "
    "refuses: work-level aggregation, AI/human provenance verdicts, and "
    "any reduction of per-segment values (including via the public "
    "narrative_decision_audit.signal_target_value function)."
)


# ---------- run --------------------------------------------------------

def _run(
    *,
    text: str,
    target_path: Path,
    target_words: int,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> dict[str, Any]:
    calibration = bool(args.calibration_emit_segments)

    # -- length routing ------------------------------------------------
    if not calibration and target_words <= CEILING_WORDS:
        raise _Refusal(
            "bad_input",
            f"target is {target_words} words (<= {CEILING_WORDS}): in "
            f"range — use the base audit "
            f"(narrative_decision_audit.py). The long-form surface "
            f"applies only above the ceiling; "
            f"--calibration-emit-segments segments in-range works for "
            f"calibration fixtures only.",
        )

    # -- segmentation ----------------------------------------------------
    try:
        seg = nls.segment_text(
            text, segment_target_words=args.segment_target_words
        )
    except nls.SegmentationInfeasible as exc:
        raise _Refusal("bad_input", f"segmentation infeasible: {exc}")
    except ValueError as exc:
        raise _Refusal("bad_input", f"bad segmentation parameters: {exc}")

    # -- judge setup -----------------------------------------------------
    judge_kind = args.judge
    keyed_manifest: dict[str, Any] | None = None
    shared_judge = None
    api_identity: dict[str, Any] | None = None

    if judge_kind == "manifest":
        if args.judge_manifest is None:
            parser.error(
                "judge construction failed: manifest judge requires "
                "--judge-manifest"
            )
        keyed_manifest = _load_keyed_manifest(args.judge_manifest)
        missing = [
            s.content_sha256 for s in seg.segments
            if s.content_sha256 not in keyed_manifest
        ]
        if missing:
            raise _Refusal(
                "bad_input",
                f"manifest {args.judge_manifest}: missing "
                f"{len(missing)} segment key(s) (keyed by segment "
                f"content_sha256): {missing[:3]}"
                f"{'…' if len(missing) > 3 else ''}",
            )
    elif judge_kind == "mock":
        if not calibration:
            raise _Refusal(
                "policy_refused",
                "mock judge is refused on scoring runs (it is a "
                "test/calibration constant, not a judge). It is allowed "
                "only under --calibration-emit-segments, whose output is "
                "mechanically ineligible for any claim.",
            )
        try:
            shared_judge = nj.build_judge("mock")
        except nj.JudgeError as exc:  # pragma: no cover — mock can't fail
            parser.error(f"judge construction failed: {exc}")
    else:
        try:
            shared_judge = nj.build_judge(
                judge_kind,
                model=args.judge_model,
                temperature=args.judge_temperature,
                max_tokens=args.judge_max_tokens,
            )
        except nj.JudgeError as exc:
            parser.error(f"judge construction failed: {exc}")
        api_identity = {
            "model": args.judge_model or (
                "host-resolved" if judge_kind == "agent_host" else None
            ),
            "model_revision": None,
            "prompt_version": nj.fingerprint_prompt(),
        }

    # -- per-segment scoring ----------------------------------------------
    payloads, identities, n_hits, n_misses = _score_segments(
        seg=seg,
        text=text,
        judge_kind=judge_kind,
        keyed_manifest=keyed_manifest,
        shared_judge=shared_judge,
        api_identity=api_identity,
        cache_dir=args.cache_dir,
    )

    # -- degenerate-judge tripwire (scoring runs only) ---------------------
    if not calibration and payloads:
        vector_counts = Counter(p["values_vector"] for p in payloads)
        worst = max(vector_counts.values())
        if worst >= DEGENERATE_VECTOR_MIN:
            raise _Refusal(
                "policy_refused",
                f"degenerate judge: {worst} of {len(payloads)} segments "
                f"produced byte-identical per-signal value vectors "
                f"(>= {DEGENERATE_VECTOR_MIN}). A text-blind judge "
                f"configuration (e.g. one flat manifest reused across "
                f"segments) cannot support a segmented run.",
            )

    # -- envelope-level warnings -------------------------------------------
    warnings: list[str] = []
    judge_block = {
        "kind": judge_kind,
        "model": identities[0].get("model") if identities else None,
        "model_revision": (
            identities[0].get("model_revision") if identities else None
        ),
        "prompt_version": (
            identities[0].get("prompt_version") if identities else None
        ),
    }
    distinct_identities = {
        (i.get("model"), i.get("model_revision"), i.get("prompt_version"))
        for i in identities
    }
    if len(distinct_identities) > 1:
        warnings.append(
            f"Per-segment judge identities are heterogeneous "
            f"({len(distinct_identities)} distinct model/revision/"
            f"prompt_version tuples). The judge block reports segment 0's "
            f"identity; a heterogeneous run can never match a single "
            f"validation receipt."
        )
    if any(
        judge_block.get(k) is None
        for k in ("model", "model_revision", "prompt_version")
    ):
        warnings.append(
            "Judge identity is not fully concrete (model / "
            "model_revision / prompt_version incomplete). A validation "
            "receipt can never match a non-concrete identity, so this "
            "run remains provisional_unvalidated permanently."
        )
    n_seg_validation_warnings = sum(
        len(p.get("validation_warnings") or []) for p in payloads
    )
    if n_seg_validation_warnings:
        warnings.append(
            f"Judge output had {n_seg_validation_warnings} validation "
            f"warning(s) across {len(payloads)} segment(s); affected "
            f"signals are emitted as unavailable."
        )

    # -- results ---------------------------------------------------------
    if calibration:
        results: dict[str, Any] = {
            "calibration_only": True,
            "judge_kind": judge_kind,
            "judge": judge_block,
            "segmentation": nls.segmentation_dict(seg),
            "per_segment": _per_segment_block(
                seg, payloads, calibration_only=True
            ),
            "per_signal_aggregates": {},
        }
        warnings.insert(
            0,
            "CALIBRATION-ONLY output: segmentation + per-segment "
            "responses for fixture construction. Mechanically ineligible "
            "for any evidentiary claim; aggregates suppressed entirely.",
        )
        if judge_kind == "mock":
            warnings.insert(
                1,
                "Judge is `mock` (deterministic constants; "
                "judge_kind: \"mock\" is stamped in results). Values "
                "describe nothing about the target text.",
            )
        licenses_text = CALIBRATION_LICENSES
        does_not_license_text = CALIBRATION_DOES_NOT_LICENSE
    else:
        results = {
            "segmentation": nls.segmentation_dict(seg),
            "per_segment": _per_segment_block(
                seg, payloads, calibration_only=False
            ),
            "per_signal_aggregates": _per_signal_aggregates_block(
                payloads
            ),
            "per_bundle": _per_bundle_block(),
            "validation_binding": _validation_binding_block(),
            "judge": judge_block,
        }
        warnings.insert(
            0,
            "All work-level aggregates are suppressed "
            "(provisional_unvalidated, value: null): no validation "
            "receipt exists in M1. The payload of record is the "
            "per-segment raw responses.",
        )
        licenses_text = M1_LICENSES
        does_not_license_text = M1_DOES_NOT_LICENSE

    results["cache"] = {
        "enabled": args.cache_dir is not None,
        "n_hits": n_hits,
        "n_misses": n_misses,
    }

    # -- emit guard: on the REAL results dict, every run -------------------
    assert_no_work_level_reduction(results)

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "literature_anchor": (
                "Russell et al. 2026 (StoryScope, arXiv:2604.03136v4); "
                "segmented long-form extension per spec 79"
            ),
            "judge_kind": judge_kind,
            "judge_model": judge_block.get("model") or "(unspecified)",
            "segmenter_version": seg.segmenter_version,
            "segmenter_params_sha256": seg.params_sha256,
            "prompt_fingerprint_sha256": nj.fingerprint_prompt(),
        },
        register_match=["long_form_fiction"],
        additional_caveats=list(warnings),
        references=[
            "Russell et al. 2026, 'StoryScope: Narrative-Level "
            "Detection of AI-Generated Fiction' (arXiv:2604.03136v4)",
            "specs/79-storyscope-long-form-extension.md (M1)",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=warnings,
    )


# ---------- CLI ---------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "StoryScope long-form extension (spec 79 M1): segment an "
            "over-ceiling work and run the narrative-decision audit's "
            "scoring path per segment. Work-level aggregates are "
            "suppressed (provisional_unvalidated) — no validation "
            "receipt exists in M1."
        )
    )
    parser.add_argument(
        "target",
        help="Path to target text file (UTF-8).",
    )
    parser.add_argument(
        "--segment-target-words",
        type=int,
        default=nls.DEFAULT_TARGET_WORDS,
        help=(
            f"Segmentation target in words (default "
            f"{nls.DEFAULT_TARGET_WORDS}; hashed into the segmenter "
            f"params)."
        ),
    )
    parser.add_argument(
        "--calibration-emit-segments",
        action="store_true",
        help=(
            "Segment a work of ANY length and emit ONLY the "
            "segmentation plus per-segment responses, stamped "
            "calibration_only — mechanically ineligible for any claim. "
            "The mock judge is allowed in this mode only."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON envelope to stdout.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Envelope JSON output path (default: "
            "<target>.narrative_long_form.json next to the target)."
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help=(
            "Optional resume cache directory of per-segment JSON "
            "results, keyed by segment content hash + judge identity "
            "(kind/model/revision/prompt_version) + segmenter params + "
            "base-audit identity."
        ),
    )
    # Judge flags mirror the base audit's.
    parser.add_argument(
        "--judge",
        choices=(
            "manifest", "mock", "anthropic", "openai", "gemini",
            "agent_host",
        ),
        default="manifest",
        help=(
            "Judge backend (mirrors narrative_decision_audit). "
            "'manifest' (default) on a segmented run must be KEYED by "
            "segment content_sha256 — a JSON object mapping each "
            "segment's 'sha256:...' hash to the flat manifest shape "
            "the base judge expects. 'mock' is refused outside "
            "--calibration-emit-segments."
        ),
    )
    parser.add_argument(
        "--judge-manifest",
        type=Path,
        default=None,
        help=(
            "Path to the KEYED JSON manifest of pre-computed feature "
            "values; required when --judge=manifest."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help=(
            "Model ID for API judges (e.g., claude-sonnet-4-6, "
            "gpt-5.4, gemini-3-flash)."
        ),
    )
    parser.add_argument(
        "--judge-temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for API judges (default 0.0).",
    )
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        default=4096,
        help="Max output tokens for API judges (default 4096).",
    )
    return parser


def _emit_refusal(
    args: argparse.Namespace,
    refusal: _Refusal,
    target_path: Path | None,
    target_words: int,
) -> int:
    envelope = build_error_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        reason=refusal.reason,
        reason_category=refusal.category,
        target_path=target_path,
        target_words=target_words,
    )
    if args.out is not None:
        args.out.write_text(
            json.dumps(envelope, indent=2, default=str), encoding="utf-8"
        )
    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    print(
        f"refused ({refusal.category}): {refusal.reason}",
        file=sys.stderr,
    )
    return _EXIT_BY_CATEGORY[refusal.category]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    target_path = Path(args.target)
    target_words = 0
    try:
        if not target_path.exists():
            raise _Refusal(
                "bad_input", f"target file not found at {target_path}"
            )
        try:
            text = target_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise _Refusal(
                "bad_input", f"target not valid UTF-8: {exc}"
            )
        target_words = nls.count_words(text)
        envelope = _run(
            text=text,
            target_path=target_path,
            target_words=target_words,
            args=args,
            parser=parser,
        )
    except _Refusal as refusal:
        return _emit_refusal(args, refusal, target_path, target_words)

    out_path = (
        args.out
        if args.out is not None
        else target_path.with_suffix(
            target_path.suffix + ".narrative_long_form.json"
        )
    )
    out_path.write_text(
        json.dumps(envelope, indent=2, default=str), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        seg_block = envelope["results"]["segmentation"]
        print(f"JSON written to {out_path}")
        print(
            f"Segments: {seg_block['n_segments']} "
            f"(tier: {seg_block['tier']}, target: "
            f"{seg_block['segment_target_words']} words)"
        )
        if envelope["results"].get("calibration_only"):
            print(
                "CALIBRATION-ONLY output — ineligible for any "
                "evidentiary claim."
            )
        else:
            print(
                "Work-level aggregates: suppressed "
                "(provisional_unvalidated — no validation receipt "
                "exists in M1)."
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
