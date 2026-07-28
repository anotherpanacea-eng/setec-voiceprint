#!/usr/bin/env python3
"""Judge-free calibration consumer for StoryScope polarity extension (spec 78).

This module deliberately consumes precomputed manifest values.  It never loads a
judge or a model; register/evaluate/verify are deterministic file operations.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from narrative_decision_long_form import (  # type: ignore
    OPERATOR_TABLE, all_signal_ids, signal_id_for, signals_map,
)
from narrative_feature_schema import iter_signals  # type: ignore
from narrative_longform_segment import CEILING_WORDS, FLOOR_WORDS, SEGMENTER_VERSION  # type: ignore
import narrative_longform_segment as nls  # type: ignore
from narrative_longform_agreement import SIGNALS, convert_mean_response, option_present  # type: ignore
from narrative_polarity_audit import auc_mannwhitney, direction_aware_auc, hanley_mcneil_se, polarity_verdict  # type: ignore
from storyscope_polarity_contract import (  # type: ignore
    FRAME_DOMAINS, framed_digest, framed_file_digest, framed_json_digest,
    count_source_words, source_work_sha256,
)

try:  # promoted by the sibling increment; retain an explicit failure if absent.
    from narrative_decision_long_form import DEGENERATE_VECTOR_MIN  # type: ignore
except ImportError:  # pragma: no cover - protects partial landing order
    DEGENERATE_VECTOR_MIN = 3

__all__ = [
    "CalibrationRefusal", "FRAME_DOMAINS", "DROP_REASONS", "REFUSAL_REASONS",
    "SIGNAL_IDS", "RESPONSE_CLASS_BY_SIGNAL_ID", "LEANING_BY_SIGNAL_ID",
    "framed_sha256", "canonical_json", "load_thresholds", "build_registration",
    "evaluate", "verify_receipt", "derive_polarity_verdict",
    "hedges_g", "paired_shift", "assert_no_per_text_disclosure", "main",
]

DROP_REASONS = frozenset({"source_work_in_range", "below_length_band", "above_length_band", "duplicate_content_sha256", "whole_text_tier", "single_segment_work"})
REFUSAL_REASONS = frozenset({
    "malformed_artifact", "registration_manifest_not_values_free", "post_hoc_thresholds", "registration_mismatch", "duplicate_text_id", "mock_row_judge", "unbound_source_envelope", "source_envelope_mismatch", "row_judge_identity_mismatch", "cross_source_kind_primary", "mixed_arm_manifest", "segmenter_binding_violation", "segmenter_binding_mismatch", "bridge_row_word_count_mismatch", "bridge_read_mode_unsupported", "source_work_words_exceeds_judge_context", "missing_license_amendment", "unknown_amendment_id", "unknown_signal_id", "manifest_schema_violation", "illegal_response", "degenerate_manifest_vectors", "cross_work_degenerate_vectors", "prompt_signal_blindness_violation", "unregistered_prompt_family", "band_table_inconsistent", "floor_arithmetic_violation", "length_overlap_below_floor",
})
VERDICTS = frozenset({"polarity_matches", "polarity_inverted", "polarity_chance", "fragment_artifact_confounded", "subfloor_artifact_confounded", "bridge_inconclusive", "insufficient_support", "indeterminate", "judge_answer_absent"})
SIGNAL_IDS = tuple(all_signal_ids())

class CalibrationRefusal(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in REFUSAL_REASONS:
            raise ValueError(f"unknown refusal reason: {reason}")
        super().__init__(reason + (f": {detail}" if detail else ""))
        self.reason = reason

def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

def framed_sha256(domain: str, payload: bytes) -> str:
    """Compatibility spelling for the shared, frozen framing helper."""
    return framed_digest(domain, payload)

def _digest_file(domain: str, path: Path) -> str:
    return framed_file_digest(domain, path)

def _is_int(v: Any) -> bool: return isinstance(v, int) and not isinstance(v, bool)
def _finite(v: Any) -> bool: return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

SIGNAL_SPECS: dict[str, Any] = dict(SIGNALS)
LEANING_BY_SIGNAL_ID: dict[str, str] = {}
RESPONSE_CLASS_BY_SIGNAL_ID: dict[str, str] = {}
for _feature, _i, _signal in iter_signals():
    _sid = signal_id_for(_feature, _signal)
    LEANING_BY_SIGNAL_ID[_sid] = _signal.leaning
    RESPONSE_CLASS_BY_SIGNAL_ID[_sid] = "indicator" if _signal.option is not None else "numeric"
if set(SIGNAL_IDS) != set(SIGNAL_SPECS) or len(SIGNAL_IDS) != 33:
    raise RuntimeError("StoryScope signal identity drift")

FLOOR_KEYS = ("min_source_works", "min_authors", "min_generator_families", "min_signal_support", "min_class_n", "class_n_margin", "min_availability_rate", "min_segment_count_by_work", "min_bridge_works", "min_source_work_words", "max_share_single_work", "length_overlap_min", "length_bins", "fragment_shift_ceiling", "subfloor_shift_ceiling", "effect_threshold_numeric", "pre_ai_cutoff_year")
BAND_KEYS = {"segment_regime": {"primary", "bridge"}, "subfloor": {"primary", "bridge_full", "bridge_truncated"}}
DESIGN_KEYS = ("text_id", "label", "role", "source_kind", "source_work_id", "subfloor_bridge_side", "content_sha256", "source_work_sha256")

def _require_exact(obj: Any, keys: Iterable[str], reason: str = "malformed_artifact") -> dict[str, Any]:
    if not isinstance(obj, dict) or set(obj) != set(keys): raise CalibrationRefusal(reason)
    return obj

def load_thresholds(path: Path, date: str | None = None) -> dict[str, Any]:
    try: obj = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError): raise CalibrationRefusal("malformed_artifact")
    _require_exact(obj, ("schema", "floors", "bands"))
    if obj["schema"] != "narrative-polarity-thresholds/1": raise CalibrationRefusal("malformed_artifact")
    floors = _require_exact(obj["floors"], FLOOR_KEYS)
    bands = _require_exact(obj["bands"], BAND_KEYS)
    for arm, names in BAND_KEYS.items():
        _require_exact(bands[arm], names)
        for name in names:
            b = _require_exact(bands[arm][name], ("min_words", "max_words"))
            if not _is_int(b["min_words"]) or not _is_int(b["max_words"]) or b["min_words"] < 1 or b["min_words"] > b["max_words"]: raise CalibrationRefusal("malformed_artifact")
    ints = ("min_source_works", "min_authors", "min_generator_families", "min_signal_support", "min_class_n", "class_n_margin", "min_segment_count_by_work", "min_bridge_works", "min_source_work_words", "length_bins", "pre_ai_cutoff_year")
    if any(not _is_int(floors[k]) for k in ints) or any(not _finite(floors[k]) for k in FLOOR_KEYS): raise CalibrationRefusal("malformed_artifact")
    if floors["min_source_works"] < 24 or floors["min_authors"] < 8 or floors["min_generator_families"] < 2 or floors["min_signal_support"] < 24 or floors["min_class_n"] < 20 or floors["class_n_margin"] < 4 or floors["min_segment_count_by_work"] < 3 or floors["min_bridge_works"] < 8 or floors["min_source_work_words"] < CEILING_WORDS + 1 or floors["length_bins"] < 2 or not (0 < floors["max_share_single_work"] <= .15 and .9 <= floors["min_availability_rate"] <= 1 and .8 <= floors["length_overlap_min"] <= 1 and 0 <= floors["fragment_shift_ceiling"] <= 1 and 0 <= floors["subfloor_shift_ceiling"] <= 1 and floors["effect_threshold_numeric"] > 0): raise CalibrationRefusal("floor_arithmetic_violation")
    if date is not None and (floors["pre_ai_cutoff_year"] < 1 or floors["pre_ai_cutoff_year"] > _validate_date(date)): raise CalibrationRefusal("malformed_artifact")
    if floors["min_source_works"] < floors["min_signal_support"] or floors["min_signal_support"] < floors["min_class_n"] + floors["class_n_margin"]: raise CalibrationRefusal("floor_arithmetic_violation")
    bridge = bands["segment_regime"]["bridge"]
    if bridge["min_words"] <= CEILING_WORDS or bridge["max_words"] > 200_000 or bands["segment_regime"]["primary"]["min_words"] < FLOOR_WORDS or bands["segment_regime"]["primary"]["max_words"] > CEILING_WORDS or bands["subfloor"]["primary"]["max_words"] >= FLOOR_WORDS or bands["subfloor"]["bridge_truncated"] != bands["subfloor"]["primary"]: raise CalibrationRefusal("band_table_inconsistent")
    return obj

def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError): raise CalibrationRefusal("malformed_artifact")
    if not all(isinstance(x, dict) for x in rows): raise CalibrationRefusal("malformed_artifact")
    return rows

def _design(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not set(r) <= set(DESIGN_KEYS): raise CalibrationRefusal("registration_manifest_not_values_free")
        if set(r) != set(DESIGN_KEYS): raise CalibrationRefusal("registration_manifest_not_values_free")
        out.append({k:r[k] for k in DESIGN_KEYS})
    return sorted(out, key=lambda r: tuple(str(r[k] or "") for k in DESIGN_KEYS))

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FORBIDDEN_PROMPT_TOKENS = ("storyscope", "arxiv:2604.03136", "narrabench")

def _validate_date(date: str) -> int:
    if not isinstance(date, str) or not _DATE_RE.fullmatch(date):
        raise CalibrationRefusal("malformed_artifact")
    try:
        return int(date[:4])
    except ValueError:
        raise CalibrationRefusal("malformed_artifact")

def _prompt_names_signal(text: str) -> bool:
    folded = " ".join(text.casefold().split())
    terms = list(_FORBIDDEN_PROMPT_TOKENS)
    for feature, _, signal in iter_signals():
        terms.extend((feature.key, feature.label))
        if signal.option:
            terms.append(signal.option)
    for term in terms:
        raw = str(term).casefold()
        if "_" in raw and raw in folded:
            return True
        token = " ".join(str(term).casefold().replace("_", " ").split())
        if not token:
            continue
        if " " in token or "_" in str(term):
            if token in folded:
                return True
        elif re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", folded):
            return True
    return False

def _validate_registration(reg: Any, *, arm: str, date: str) -> dict[str, Any]:
    _require_exact(reg, ("schema", "date", "arm", "thresholds_sha256", "work_ids_sha256", "design_sha256", "signal_id_set_sha256", "segmenter", "judge", "generation_prompts"))
    if reg["schema"] != "narrative-polarity-registration/1" or reg["arm"] != arm or reg["date"] != date:
        raise CalibrationRefusal("registration_mismatch")
    judge = reg["judge"]
    if not isinstance(judge, dict) or set(judge) != {"kind", "model", "model_revision", "prompt_version", "context_bound_words"} or any(not isinstance(judge.get(k), str) or not judge[k] for k in ("kind", "model", "model_revision", "prompt_version")) or judge["kind"] == "mock" or judge["model"] in {"host-resolved", "(unspecified)", "unknown"} or not _is_int(judge.get("context_bound_words")) or judge["context_bound_words"] < 1:
        raise CalibrationRefusal("malformed_artifact")
    segmenter = reg["segmenter"]
    if arm == "subfloor":
        if segmenter is not None: raise CalibrationRefusal("malformed_artifact")
    else:
        if not isinstance(segmenter, dict) or set(segmenter) != {"emitter", "segmenter_version", "params_sha256", "segment_target_words"} or segmenter["emitter"] != "narrative_decision_long_form" or segmenter["segmenter_version"] != SEGMENTER_VERSION or not isinstance(segmenter["params_sha256"], str) or not _is_int(segmenter["segment_target_words"]) or not FLOOR_WORDS <= segmenter["segment_target_words"] <= CEILING_WORDS:
            raise CalibrationRefusal("malformed_artifact")
    prompts = reg["generation_prompts"]
    if not isinstance(prompts, list): raise CalibrationRefusal("malformed_artifact")
    families=[]
    for item in prompts:
        if not isinstance(item, dict) or set(item) != {"prompt_family", "prompt_sha256", "prompt_text_path"} or not all(isinstance(item.get(k), str) and item[k] for k in item) or Path(item["prompt_text_path"]).name != item["prompt_text_path"]:
            raise CalibrationRefusal("malformed_artifact")
        families.append(item["prompt_family"])
    if families != sorted(families) or len(set(families)) != len(families): raise CalibrationRefusal("malformed_artifact")
    return reg

def build_registration(*, arm: str, manifest: Path, thresholds: Path, date: str, segmenter: dict[str, Any] | None, judge: dict[str, Any], prompts: list[dict[str, str]]) -> dict[str, Any]:
    if arm not in {"segment_regime", "subfloor"}: raise CalibrationRefusal("malformed_artifact")
    if not isinstance(judge, dict) or set(judge) != {"kind", "model", "model_revision", "prompt_version", "context_bound_words"} or any(not isinstance(judge.get(k), str) or not judge[k] for k in ("kind", "model", "model_revision", "prompt_version")) or judge.get("kind") == "mock" or judge.get("model") in {"host-resolved", "(unspecified)", "unknown"} or not _is_int(judge.get("context_bound_words")) or judge["context_bound_words"] < 1:
        raise CalibrationRefusal("malformed_artifact")
    if arm == "segment_regime" and judge["kind"] != "manifest":
        raise CalibrationRefusal("malformed_artifact")
    _validate_date(date); load_thresholds(thresholds, date); rows = _read_jsonl(manifest); design = _design(rows)
    if arm == "subfloor" and segmenter is not None: raise CalibrationRefusal("malformed_artifact")
    if arm == "segment_regime":
        if not isinstance(segmenter, dict) or set(segmenter) != {"emitter", "segmenter_version", "params_sha256", "segment_target_words"} or segmenter.get("emitter") != "narrative_decision_long_form" or segmenter.get("segmenter_version") != SEGMENTER_VERSION or not isinstance(segmenter.get("params_sha256"), str) or not _is_int(segmenter.get("segment_target_words")) or not FLOOR_WORDS <= segmenter["segment_target_words"] <= CEILING_WORDS: raise CalibrationRefusal("malformed_artifact")
    if [x.get("prompt_family") for x in prompts] != sorted(x.get("prompt_family") for x in prompts) or len({x.get("prompt_family") for x in prompts}) != len(prompts): raise CalibrationRefusal("malformed_artifact")
    if arm == "segment_regime" and thresholds:
        threshold_obj=load_thresholds(thresholds, date)
        if threshold_obj["bands"]["segment_regime"]["bridge"]["max_words"] > judge["context_bound_words"]:
            raise CalibrationRefusal("band_table_inconsistent")
    work_ids = sorted({r["source_work_id"] for r in design})
    return {"schema":"narrative-polarity-registration/1", "date":date, "arm":arm, "thresholds_sha256":_digest_file("setec.voiceprint.spec78.thresholds-file.v1", thresholds), "work_ids_sha256":framed_sha256("setec.voiceprint.spec78.work-id-set-json.v1", canonical_json(work_ids)), "design_sha256":framed_sha256("setec.voiceprint.spec78.design-projection-json.v1", canonical_json(design)), "signal_id_set_sha256":framed_sha256("setec.voiceprint.spec78.signal-id-set-json.v1", canonical_json(sorted(SIGNAL_IDS))), "segmenter":segmenter if arm == "segment_regime" else None, "judge":judge, "generation_prompts":sorted(prompts, key=lambda x:x["prompt_family"])}

def hedges_g(ai: list[float], human: list[float], leaning: str) -> tuple[float, float] | None:
    if len(ai) < 2 or len(human) < 2: return None
    va, vh = statistics.variance(ai), statistics.variance(human); n = len(ai)+len(human)
    pooled = math.sqrt(((len(ai)-1)*va + (len(human)-1)*vh)/(n-2))
    if pooled == 0: return None
    raw = (statistics.mean(ai)-statistics.mean(human))/pooled; j = 1-3/(4*n-9)
    g = j*raw * (1 if leaning == "ai" else -1); se = j*math.sqrt(n/(len(ai)*len(human)) + raw*raw/(2*n))
    return (g, se) if se else None

def paired_shift(values: list[float]) -> tuple[float, float] | None:
    if len(values) < 2: return None
    mean = statistics.mean(values); upper = mean + 1.645*statistics.stdev(values)/math.sqrt(len(values))
    return mean, upper

def derive_polarity_verdict(*, arm: str, availability: tuple[float,float], availability_floor: float, support: tuple[int,int], min_support: int, bridge_support: tuple[int,int], min_bridge: int, response_class: str, min_class_n: int, bridge: tuple[tuple[float,float],tuple[float,float]] | None, ceiling: float, degenerate: bool, interval: tuple[float,float] | None, threshold: float, corpus_ok: bool = True) -> tuple[str,int]:
    if min(availability) < availability_floor: return "judge_answer_absent", 1
    if not corpus_ok or min(support) < min_support or min(bridge_support) < min_bridge: return "insufficient_support", 2
    assert bridge is not None
    states = [("artifact" if p >= ceiling else "inconclusive" if u >= ceiling else "pass") for p,u in bridge]
    if "artifact" in states: return ("fragment_artifact_confounded" if arm == "segment_regime" else "subfloor_artifact_confounded"), 3
    if "inconclusive" in states: return "bridge_inconclusive", 3
    if response_class == "indicator" and min(support) < min_class_n: return "polarity_chance", 4
    if degenerate or interval is None: return "indeterminate", 5
    lo, hi = interval
    if response_class == "indicator": return ({"matches":"polarity_matches","inverted":"polarity_inverted","chance":"polarity_chance"}[polarity_verdict((lo+hi)/2, (hi-lo)/(2*1.96))], 6)
    return ("polarity_matches" if lo > threshold else "polarity_inverted" if hi < -threshold else "polarity_chance"), 6

def _validate_row(r: dict[str, Any], arm: str, registered_judge: dict[str, Any]) -> None:
    keys = {"text_id","label","role","source_kind","source_work_id","source_work_words","source_work_sha256","n_words","content_sha256","subfloor_bridge_side","provenance","segmenter","read_mode","judge","signals"}
    if set(r) != keys: raise CalibrationRefusal("manifest_schema_violation")
    if r["label"] not in {"pre_ai_human","ai_generated"} or r["role"] not in {"primary","bridge"} or r["source_kind"] not in {"segment","whole_work"} or not isinstance(r["text_id"],str) or not r["text_id"] or not isinstance(r["source_work_id"], str) or not r["source_work_id"] or not _is_int(r["n_words"]) or r["n_words"] < 1: raise CalibrationRefusal("manifest_schema_violation")
    if not isinstance(r["signals"], dict): raise CalibrationRefusal("manifest_schema_violation")
    if set(r["signals"]) != set(SIGNAL_IDS): raise CalibrationRefusal("unknown_signal_id")
    for cell in r["signals"].values():
        if (
            not isinstance(cell, dict)
            or set(cell) != {"value", "available"}
            or not isinstance(cell["available"], bool)
            or cell["available"] != (cell["value"] is not None)
        ):
            raise CalibrationRefusal("manifest_schema_violation")
    j = r["judge"]
    if not isinstance(j,dict) or set(j) != {"kind", "model", "model_revision", "prompt_version", "source_envelope_sha256", "source_envelope_path"}: raise CalibrationRefusal("manifest_schema_violation")
    if j.get("kind") == "mock" or j.get("kind") not in {"manifest", "anthropic", "openai", "gemini", "agent_host"} or not all(isinstance(j.get(k), str) and j[k] for k in ("kind", "model", "model_revision", "prompt_version")) or j.get("model") in {"host-resolved","(unspecified)","unknown"}: raise CalibrationRefusal("mock_row_judge")
    if any(j.get(k) != registered_judge.get(k) for k in ("kind","model","model_revision","prompt_version")): raise CalibrationRefusal("row_judge_identity_mismatch")
    if not j.get("source_envelope_sha256") or not j.get("source_envelope_path"): raise CalibrationRefusal("unbound_source_envelope")
    if r["label"] == "pre_ai_human":
        if not isinstance(r["provenance"], dict) or set(r["provenance"]) != {"class", "author_id", "publication_year", "source_corpus_id", "claim_license_amendment", "register_extension"} or r["provenance"].get("class") != "human" or not isinstance(r["provenance"].get("author_id"), str) or not _is_int(r["provenance"].get("publication_year")): raise CalibrationRefusal("manifest_schema_violation")
    else:
        if not isinstance(r["provenance"], dict) or set(r["provenance"]) != {"class", "generator_family", "model", "model_revision", "prompt_family", "generated_date", "claim_license_amendment", "register_extension"} or r["provenance"].get("class") != "ai" or not all(isinstance(r["provenance"].get(k), str) and r["provenance"][k] for k in ("generator_family", "model", "model_revision", "prompt_family", "generated_date")): raise CalibrationRefusal("manifest_schema_violation")
    if arm == "segment_regime":
        if not _is_int(r["source_work_words"]) or r["source_work_words"] < 1 or not isinstance(r["source_work_sha256"], str): raise CalibrationRefusal("mixed_arm_manifest")
        if r["role"] == "primary" and r["source_kind"] != "segment": raise CalibrationRefusal("cross_source_kind_primary")
        if r["role"] == "bridge" and (r["source_kind"] != "whole_work" or r["n_words"] != r["source_work_words"]): raise CalibrationRefusal("bridge_row_word_count_mismatch")
        if r["role"] == "bridge" and r["read_mode"] != "single_pass_whole_text": raise CalibrationRefusal("bridge_read_mode_unsupported")
        if r["role"] == "bridge" and r["source_work_words"] > registered_judge["context_bound_words"]: raise CalibrationRefusal("source_work_words_exceeds_judge_context")
        if r["source_kind"] == "segment":
            s=r["segmenter"]
            if not isinstance(s, dict) or set(s) != {"emitter", "segmenter_version", "params_sha256", "segment_target_words", "tier", "segment_index", "n_segments_in_work"} or s.get("emitter") != "narrative_decision_long_form" or s.get("segmenter_version") != SEGMENTER_VERSION or not isinstance(s.get("params_sha256"), str) or not _is_int(s.get("segment_target_words")) or not FLOOR_WORDS <= s["segment_target_words"] <= CEILING_WORDS or s.get("tier") not in {"chapter_heading", "scene_break", "blank_line_run", "paragraph", "whole_text"} or not _is_int(s.get("segment_index")) or s["segment_index"] < 0 or not _is_int(s.get("n_segments_in_work")) or s["n_segments_in_work"] < 1: raise CalibrationRefusal("segmenter_binding_violation")
        elif r["segmenter"] is not None: raise CalibrationRefusal("segmenter_binding_violation")
    elif r["source_work_words"] is not None or r["segmenter"] is not None: raise CalibrationRefusal("mixed_arm_manifest")
    expected = ("CLA-79-A1", None) if arm == "segment_regime" and r["role"] == "primary" else (("CLA-79-A2", "REG-AUDIT-B1") if arm == "segment_regime" else (None, None))
    if (r["provenance"].get("claim_license_amendment"), r["provenance"].get("register_extension")) != expected:
        raise CalibrationRefusal("missing_license_amendment" if expected[0] and r["provenance"].get("claim_license_amendment") is None else "unknown_amendment_id")

def _encode(r: dict[str, Any], sid: str) -> float | None:
    c = r["signals"][sid]
    if not isinstance(c,dict) or set(c) != {"value","available"} or not isinstance(c["available"],bool) or (c["available"] != (c["value"] is not None)): raise CalibrationRefusal("manifest_schema_violation")
    if not c["available"]: return None
    try: return option_present(SIGNAL_SPECS[sid], c["value"]) if RESPONSE_CLASS_BY_SIGNAL_ID[sid] == "indicator" else convert_mean_response(SIGNAL_SPECS[sid], c["value"])
    except Exception: raise CalibrationRefusal("illegal_response")

def _response_range(sid: str) -> float:
    spec=SIGNAL_SPECS[sid]
    values=[convert_mean_response(spec, option) for option in spec.response_options]
    return max(values)-min(values)

def _reopen_envelopes(rows: list[dict[str, Any]]) -> None:
    """Bind every declared producer envelope to its exact bytes before use.

    Producers retain their own envelope schemas; this consumer's universal
    custody invariant is consequently intentionally small: every row names a
    regular local envelope and carries its framed byte digest.  The producer
    specific checks are performed only after this binding has succeeded.
    """
    for row in rows:
        judge = row["judge"]
        path = Path(judge["source_envelope_path"])
        try:
            if not path.is_file() or _digest_file("setec.voiceprint.spec78.source-envelope-file.v1", path) != judge["source_envelope_sha256"]:
                raise CalibrationRefusal("source_envelope_mismatch")
            envelope = json.loads(path.read_text("utf-8"))
            if not isinstance(envelope, dict): raise CalibrationRefusal("source_envelope_mismatch")
            # A producer's exact judge identity must agree with the manifest.
            result = envelope.get("results", {})
            producer_judge = result.get("judge") or result.get("judge_kind")
            if isinstance(producer_judge, dict):
                candidate = producer_judge.get("judge_identity", producer_judge)
                if isinstance(candidate, dict) and any(candidate.get(k) != judge[k] for k in ("kind", "model", "model_revision", "prompt_version")):
                    raise CalibrationRefusal("source_envelope_mismatch")
            target = envelope.get("target")
            if not isinstance(target, dict): raise CalibrationRefusal("source_envelope_mismatch")
            target_path = target.get("path")
            if not isinstance(target_path, str) or not target_path: raise CalibrationRefusal("source_envelope_mismatch")
            text = Path(target_path).read_text("utf-8")
            # Base-audit producers are bound to exact judged bytes and their
            # canonical source counter.  Arm-A producer envelopes additionally
            # carry the shared whole-work hash.
            if row["source_work_words"] is not None:
                if (
                    target.get("source_content_sha256") != row["source_work_sha256"]
                    or source_work_sha256(text) != row["source_work_sha256"]
                    or count_source_words(text) != row["source_work_words"]
                    or target.get("words") != row["source_work_words"]
                ):
                    raise CalibrationRefusal("source_envelope_mismatch")
            if row["source_kind"] == "whole_work":
                if count_source_words(text) != row["n_words"]:
                    raise CalibrationRefusal("source_envelope_mismatch")
                if framed_sha256("setec.voiceprint.spec78.content-text.v1", text.encode("utf-8")) != row["content_sha256"]:
                    raise CalibrationRefusal("source_envelope_mismatch")
                if row["source_work_words"] is not None:
                    if result.get("register_extension") != "REG-AUDIT-B1" or result.get("bridge_judge") != {k: judge[k] for k in ("kind", "model", "model_revision", "prompt_version")} or signals_map(result.get("values", {}), value_key="value") != row["signals"]:
                        raise CalibrationRefusal("source_envelope_mismatch")
                elif signals_map(result.get("values", {}), value_key="value") != row["signals"]:
                    raise CalibrationRefusal("source_envelope_mismatch")
            else:
                if result.get("calibration_only") is not True or target.get("source_content_sha256") != row["source_work_sha256"] or source_work_sha256(text) != row["source_work_sha256"]:
                    raise CalibrationRefusal("source_envelope_mismatch")
                segmenter=row.get("segmenter")
                if not isinstance(segmenter, dict) or not _is_int(segmenter.get("segment_index")):
                    raise CalibrationRefusal("source_envelope_mismatch")
                segmentation=nls.segment_text(text, segment_target_words=segmenter.get("segment_target_words"))
                index=segmenter["segment_index"]
                if index < 0 or index >= len(segmentation.segments): raise CalibrationRefusal("source_envelope_mismatch")
                segment=segmentation.segments[index]
                producer_seg=result.get("segmentation")
                if not isinstance(producer_seg, dict) or any(producer_seg.get(k) != getattr(segmentation, {"segmenter_version":"segmenter_version", "tier":"tier", "params_sha256":"params_sha256", "segment_target_words":"segment_target_words", "n_segments":"n_segments"}[k]) for k in ("segmenter_version", "tier", "params_sha256", "segment_target_words", "n_segments")):
                    raise CalibrationRefusal("source_envelope_mismatch")
                if segment.n_words != row["n_words"] or segmenter["tier"] != segmentation.tier or segmenter["n_segments_in_work"] != segmentation.n_segments or framed_sha256("setec.voiceprint.spec78.content-text.v1", text[segment.start:segment.end].encode("utf-8")) != row["content_sha256"]:
                    raise CalibrationRefusal("source_envelope_mismatch")
                emitted=result.get("per_segment")
                if not isinstance(emitted, list): raise CalibrationRefusal("source_envelope_mismatch")
                emitted_entry=next((entry for entry in emitted if isinstance(entry, dict) and entry.get("index") == index), None)
                if emitted_entry is None: raise CalibrationRefusal("source_envelope_mismatch")
                source_signals=emitted_entry.get("signals")
                if not isinstance(source_signals, dict): raise CalibrationRefusal("source_envelope_mismatch")
                projected={sid:{"value":cell.get("response"),"available":cell.get("available")} for sid,cell in source_signals.items() if isinstance(cell,dict)}
                if projected != row["signals"]: raise CalibrationRefusal("source_envelope_mismatch")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            raise CalibrationRefusal("source_envelope_mismatch")

def _validate_truncations(rows: list[dict[str, Any]], arm: str) -> None:
    if arm != "subfloor": return
    pairs=defaultdict(dict)
    for row in rows:
        if row["role"] == "bridge": pairs[(row["label"], row["source_work_id"])][row["subfloor_bridge_side"]]=row
    for pair in pairs.values():
        if set(pair) != {"full", "truncated"}: raise CalibrationRefusal("source_envelope_mismatch")
        try:
            full=Path(json.loads(Path(pair["full"]["judge"]["source_envelope_path"]).read_text("utf-8"))["target"]["path"]).read_text("utf-8")
            truncated=Path(json.loads(Path(pair["truncated"]["judge"]["source_envelope_path"]).read_text("utf-8"))["target"]["path"]).read_text("utf-8")
        except (OSError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
            raise CalibrationRefusal("source_envelope_mismatch")
        matches=list(re.finditer(r"\S+", full))
        n=pair["truncated"]["n_words"]
        if not _is_int(n) or n < 1 or n > len(matches) or truncated != full[:matches[n-1].end()]:
            raise CalibrationRefusal("source_envelope_mismatch")

def _round(v: Any) -> Any:
    if isinstance(v,float): return round(v,10)
    if isinstance(v,list): return [_round(x) for x in v]
    if isinstance(v,dict): return {k:_round(x) for k,x in v.items()}
    return v

def assert_no_per_text_disclosure(node: Any, path: tuple[str,...] = ()) -> None:
    bad = {"text_id","work_id","source_work_id","segment_id","content_sha256","source_envelope_sha256","score","aggregate","aggregate_score","rank","ranking","per_text","per_work","work_value","provenance_verdict"}
    allowed = {"per_signal.*.statistics.*.value","per_signal.*.statistics.*.threshold","per_signal.*.ci.lo","per_signal.*.ci.hi","per_signal.*.ci.z","per_signal.*.availability_by_class.*","per_signal.*.bridge.value","per_signal.*.bridge.value_response_units","per_signal.*.bridge.ci_upper","per_signal.*.bridge.threshold","per_signal.*.bridge.by_class.*","per_signal.*.bridge.ci_upper_by_class.*","class_counts.*.max_share_single_work","class_counts.*.segment_count_stats.median","covered_length_range.median_words","covered_source_work_range.median_words","floors_applied.*"}
    if isinstance(node,dict):
        for k,v in node.items():
            if k in bad or any(x in k for x in ("per_text","per_work","text_id","work_value","ranking")): raise ValueError("per-text disclosure")
            assert_no_per_text_disclosure(v,path+(k,))
    elif isinstance(node,list):
        for v in node: assert_no_per_text_disclosure(v,path+("*",))
    elif isinstance(node,float):
        pattern=".".join(path); pattern=re.sub(r"\.\d+(?=\.|$)", ".*", pattern)
        if pattern not in allowed: raise ValueError(f"unlisted float leaf: {pattern}")

def _aliases(out: Path, inputs: Iterable[Path]) -> bool:
    if out.exists(): out_s = out.stat()
    else: out_s = None
    for p in inputs:
        if out.resolve() == p.resolve(): return True
        if out_s and p.exists() and (out_s.st_dev,out_s.st_ino)==(p.stat().st_dev,p.stat().st_ino): return True
    return False

def _length_overlap(rows: list[dict[str, Any]], bins: int) -> float:
    by = {label: sorted(r["n_words"] for r in rows if r["label"] == label) for label in ("pre_ai_human", "ai_generated")}
    if not by["pre_ai_human"] or not by["ai_generated"]: return 0.0
    pooled = sorted(by["pre_ai_human"] + by["ai_generated"])
    edges=[]
    for j in range(1, bins):
        edge=pooled[math.ceil(j * len(pooled) / bins) - 1]
        if not edges or edge != edges[-1]: edges.append(edge)
    def counts(values: list[int]) -> list[int]:
        out=[0] * (len(edges)+1)
        for value in values:
            index=next((i for i, edge in enumerate(edges) if value <= edge), len(edges))
            out[index]+=1
        return out
    h,a=counts(by["pre_ai_human"]),counts(by["ai_generated"])
    return sum(min(x/len(by["pre_ai_human"]), y/len(by["ai_generated"])) for x,y in zip(h,a))

def _class_counts(rows: list[dict[str, Any]], dropped: Counter, arm: str) -> dict[str, Any]:
    out={}
    for label in ("pre_ai_human", "ai_generated"):
        for role in ("primary", "bridge"):
            group=[r for r in rows if r["label"] == label and r["role"] == role]
            works=Counter(r["source_work_id"] for r in group)
            seg=[r for r in group if r["source_kind"] == "segment"]
            tiers=Counter(r["segmenter"].get("tier") for r in seg if isinstance(r["segmenter"],dict))
            counts=list(works.values())
            out[f"{label}.{role}"]={
                "n_texts":len(group), "n_source_works":len(works),
                "n_source_envelopes":len({r["judge"]["source_envelope_sha256"] for r in group}),
                "n_authors":len({r["provenance"].get("author_id") for r in group}) if label == "pre_ai_human" else None,
                "n_generator_families":len({r["provenance"].get("generator_family") for r in group}) if label == "ai_generated" else None,
                "max_share_single_work":(max(counts)/len(group) if group and role == "primary" else None),
                "segment_count_stats":({"min":min(counts),"max":max(counts),"median":float(statistics.median(counts))} if arm == "segment_regime" and counts else None),
                "tier_counts":(dict(sorted(tiers.items())) if arm == "segment_regime" else None),
                "dropped_by_reason":{reason:dropped[(label,reason)] for reason in sorted(DROP_REASONS)},
            }
    return out

def _covered_range(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    values=[r[key] for r in rows if r["role"] == "primary" and r.get(key) is not None]
    if not values: return None
    return {"min_words":min(values),"max_words":max(values),"median_words":float(statistics.median(values))}

def evaluate(*, arm: str, manifest: Path, thresholds: Path, registration: Path, date: str) -> dict[str,Any]:
    th=load_thresholds(thresholds,date); rows=_read_jsonl(manifest)
    try: reg=json.loads(registration.read_text("utf-8"))
    except (OSError,json.JSONDecodeError): raise CalibrationRefusal("malformed_artifact")
    _validate_date(date)
    reg=_validate_registration(reg, arm=arm, date=date)
    if arm == "segment_regime" and th["bands"]["segment_regime"]["bridge"]["max_words"] > reg["judge"]["context_bound_words"]:
        raise CalibrationRefusal("band_table_inconsistent")
    if reg.get("thresholds_sha256") != _digest_file("setec.voiceprint.spec78.thresholds-file.v1",thresholds): raise CalibrationRefusal("post_hoc_thresholds")
    design=sorted([{k:r.get(k) for k in DESIGN_KEYS} for r in rows], key=lambda r:tuple(str(r[k] or "") for k in DESIGN_KEYS))
    if reg.get("design_sha256") != framed_sha256("setec.voiceprint.spec78.design-projection-json.v1",canonical_json(design)): raise CalibrationRefusal("registration_mismatch")
    if len({r.get("text_id") for r in rows}) != len(rows): raise CalibrationRefusal("duplicate_text_id")
    source_hashes: dict[str, str] = {}
    for r in rows:
        _validate_row(r,arm,reg["judge"])
        if arm == "segment_regime":
            prior=source_hashes.setdefault(r["source_work_id"], r["source_work_sha256"])
            if prior != r["source_work_sha256"]: raise CalibrationRefusal("source_envelope_mismatch")
            if r["source_kind"] == "segment":
                s=r["segmenter"]
                registered=reg["segmenter"]
                if any(s[k] != registered[k] for k in ("emitter", "segmenter_version", "params_sha256", "segment_target_words")):
                    raise CalibrationRefusal("segmenter_binding_mismatch")
    _reopen_envelopes(rows)
    _validate_truncations(rows, arm)
    registered_families={p["prompt_family"] for p in reg["generation_prompts"]}
    if any(r["label"] == "ai_generated" and r["provenance"]["prompt_family"] not in registered_families for r in rows): raise CalibrationRefusal("unregistered_prompt_family")
    fingerprints=defaultdict(list)
    for r in rows:
        fp=canonical_json([(sid, r["signals"][sid]["value"], r["signals"][sid]["available"]) for sid in sorted(SIGNAL_IDS)])
        fingerprints[(r["label"],r["role"],r["source_work_id"],fp)].append(r)
    if any(len(group) >= DEGENERATE_VECTOR_MIN for group in fingerprints.values()): raise CalibrationRefusal("degenerate_manifest_vectors")
    across=defaultdict(list)
    for (label,role,wid,fp), group in fingerprints.items(): across[(label,role,fp)].extend(group)
    if any(len(group) >= DEGENERATE_VECTOR_MIN and len({r["source_work_id"] for r in group}) >= 2 for group in across.values()): raise CalibrationRefusal("cross_work_degenerate_vectors")
    # composition drops; malformed rows always refused before they can disappear.
    kept=[]; dropped=Counter(); seen=defaultdict(set)
    for r in rows:
        f=th["floors"]; band=th["bands"][arm]["primary" if r["role"]=="primary" else ("bridge" if arm=="segment_regime" else "bridge_"+r["subfloor_bridge_side"])]
        why=None
        if arm=="segment_regime" and r["source_work_words"] < f["min_source_work_words"]: why="source_work_in_range"
        elif r["n_words"]<band["min_words"]: why="below_length_band"
        elif r["n_words"]>band["max_words"]: why="above_length_band"
        elif r["content_sha256"] in seen[r["label"]]: why="duplicate_content_sha256"
        elif arm=="segment_regime" and r["role"]=="primary" and isinstance(r["segmenter"], dict) and r["segmenter"].get("tier")=="whole_text": why="whole_text_tier"
        if why: dropped[(r["label"],why)]+=1
        else: kept.append(r); seen[r["label"]].add(r["content_sha256"])
    if arm=="segment_regime":
        counts=Counter((r["label"],r["source_work_id"]) for r in kept if r["role"]=="primary")
        too={x for x,n in counts.items() if n<th["floors"]["min_segment_count_by_work"]}
        kept2=[]
        for r in kept:
            if r["role"]=="primary" and (r["label"],r["source_work_id"]) in too: dropped[(r["label"],"single_segment_work")]+=1
            else: kept2.append(r)
        kept=kept2
    primary=[r for r in kept if r["role"]=="primary"]
    if _length_overlap(primary, th["floors"]["length_bins"]) < th["floors"]["length_overlap_min"]: raise CalibrationRefusal("length_overlap_below_floor")
    corpus_ok=True
    for label in ("pre_ai_human", "ai_generated"):
        group=[r for r in primary if r["label"] == label]
        work_counts=Counter(r["source_work_id"] for r in group)
        if len(work_counts) < th["floors"]["min_source_works"]: corpus_ok=False
        if group and max(work_counts.values()) / len(group) > th["floors"]["max_share_single_work"]: corpus_ok=False
        if label == "pre_ai_human" and len({r["provenance"]["author_id"] for r in group}) < th["floors"]["min_authors"]: corpus_ok=False
        if label == "ai_generated" and len({r["provenance"]["generator_family"] for r in group}) < th["floors"]["min_generator_families"]: corpus_ok=False
    per={}
    for sid in SIGNAL_IDS:
        vals={lab:defaultdict(list) for lab in ("pre_ai_human","ai_generated")}; totals=Counter(); avail=Counter()
        for r in primary:
            totals[r["label"]]+=1; x=_encode(r,sid)
            if x is not None: vals[r["label"]][r["source_work_id"]].append(x); avail[r["label"]]+=1
        workvals={lab:[statistics.mean(v) for v in vals[lab].values()] for lab in vals}
        support=(len(workvals["pre_ai_human"]),len(workvals["ai_generated"])); availability=tuple(avail[x]/totals[x] if totals[x] else 0.0 for x in ("pre_ai_human","ai_generated"))
        # Bridge is deliberately isolated from the class statistic. A fully bound bridge is producer-verified by shared contract in the parent surface.
        bvals={lab:[] for lab in vals}
        for lab in bvals:
            bywork=defaultdict(dict)
            for r in kept:
                if r["label"]!=lab or r["role"]!="bridge": continue
                x=_encode(r,sid)
                if x is not None: bywork[r["source_work_id"]][r.get("subfloor_bridge_side") or "whole"]=x
            for wid,d in bywork.items():
                if arm=="subfloor" and {"full","truncated"}<=set(d):
                    raw_shift=abs(d["full"]-d["truncated"])
                    bvals[lab].append(raw_shift if RESPONSE_CLASS_BY_SIGNAL_ID[sid] == "indicator" else raw_shift / _response_range(sid))
                elif arm=="segment_regime" and "whole" in d and wid in vals[lab]:
                    segment_side=(1.0 if sum(vals[lab][wid]) > len(vals[lab][wid]) / 2 else 0.0) if RESPONSE_CLASS_BY_SIGNAL_ID[sid] == "indicator" else statistics.mean(vals[lab][wid])
                    raw_shift=abs(d["whole"]-segment_side)
                    bvals[lab].append(raw_shift if RESPONSE_CLASS_BY_SIGNAL_ID[sid] == "indicator" else raw_shift / _response_range(sid))
        shifts=[paired_shift(bvals[x]) for x in ("pre_ai_human","ai_generated")]
        rc=RESPONSE_CLASS_BY_SIGNAL_ID[sid]; est=None; interval=None; deg=False; stats=[]
        ai,human=workvals["ai_generated"],workvals["pre_ai_human"]
        if rc=="indicator":
            raw=auc_mannwhitney(ai,human); deg=raw is None or (len(set(ai))==1 and len(set(human))==1)
            if raw is not None and not deg:
                da=direction_aware_auc(raw,LEANING_BY_SIGNAL_ID[sid]); se=hanley_mcneil_se(da,len(ai),len(human)); deg=se==0
                if not deg: interval=(da-1.96*se,da+1.96*se); stats=[{"name":"direction_aware_auc","value":da,"threshold":.5,"direction":"interval_around_fixed_null","role":"verdict_bearing","estimand":"per_source_work"}]
        else:
            est=hedges_g(ai,human,LEANING_BY_SIGNAL_ID[sid]); deg=est is None
            if est: interval=(est[0]-1.96*est[1],est[0]+1.96*est[1]); raw=auc_mannwhitney(ai,human); da=direction_aware_auc(raw,LEANING_BY_SIGNAL_ID[sid]) if raw is not None else None; stats=[{"name":"hedges_g","value":est[0],"threshold":th["floors"]["effect_threshold_numeric"],"direction":"absolute_interval","role":"verdict_bearing","estimand":"per_source_work"},{"name":"direction_aware_auc","value":da,"threshold":None,"direction":"comparison_only","role":"comparison_only","estimand":"per_source_work"}]
        bridge_pair=tuple(s for s in shifts if s is not None)
        bridge_for_verdict=(shifts[0],shifts[1]) if all(shifts) else None
        verdict,step=derive_polarity_verdict(arm=arm,availability=availability,availability_floor=th["floors"]["min_availability_rate"],support=support,min_support=th["floors"]["min_signal_support"],bridge_support=(len(bvals["pre_ai_human"]),len(bvals["ai_generated"])),min_bridge=th["floors"]["min_bridge_works"],response_class=rc,min_class_n=th["floors"]["min_class_n"],bridge=bridge_for_verdict,ceiling=th["floors"]["fragment_shift_ceiling"] if arm=="segment_regime" else th["floors"]["subfloor_shift_ceiling"],degenerate=deg,interval=interval,threshold=th["floors"]["effect_threshold_numeric"],corpus_ok=corpus_ok)
        operator=OPERATOR_TABLE[(next(f.key for f,i,s in iter_signals() if signal_id_for(f,s)==sid),SIGNAL_SPECS[sid].option)]
        pre_bridge=step <= 2
        bridge_value=max(s[0] for s in shifts if s) if all(shifts) and not pre_bridge else None
        bridge_upper=max(s[1] for s in shifts if s) if all(shifts) and not pre_bridge else None
        per[sid]={"verdict":verdict,"verdict_step":step,"operator":operator,"units":"response_units","transfer_caveat":"not_aggregatable_per_segment_only" if operator == "not_aggregatable" else "none","response_class":rc,"support":min(support),"availability_by_class":{"pre_ai_human":availability[0],"ai_generated":availability[1]},"separation_saturated":deg,"sign_stability":None,"statistics":stats if step==6 else [],"ci":({"lo":interval[0],"hi":interval[1],"z":1.96,"method":"wald"} if step==6 and interval else None),"bridge":{"statistic":"paired_absolute_shift","value":bridge_value,"ci_upper":bridge_upper,"value_response_units":None,"threshold":None if pre_bridge else th["floors"]["fragment_shift_ceiling"] if arm=="segment_regime" else th["floors"]["subfloor_shift_ceiling"],"by_class":{"pre_ai_human":shifts[0][0] if shifts[0] and not pre_bridge else None,"ai_generated":shifts[1][0] if shifts[1] and not pre_bridge else None},"ci_upper_by_class":{"pre_ai_human":shifts[0][1] if shifts[0] and not pre_bridge else None,"ai_generated":shifts[1][1] if shifts[1] and not pre_bridge else None},"n_works_by_class":{"pre_ai_human":len(bvals["pre_ai_human"]),"ai_generated":len(bvals["ai_generated"])}},"multiplicity":None,"joint_claim_suppressed":operator == "not_aggregatable" or verdict in {"fragment_artifact_confounded","subfloor_artifact_confounded","bridge_inconclusive"}}
    receipt={"schema_version":"narrative_polarity_extension_receipt/1","date":date,"arm":arm,"signal_id_set_sha256":framed_sha256("setec.voiceprint.spec78.signal-id-set-json.v1",canonical_json(sorted(SIGNAL_IDS))),"thresholds_sha256":_digest_file("setec.voiceprint.spec78.thresholds-file.v1",thresholds),"registration_sha256":_digest_file("setec.voiceprint.spec78.registration-file.v1",registration),"derivation_sha256":None,"manifest_sha256":_digest_file("setec.voiceprint.spec78.manifest-file.v1",manifest),"source_envelopes_sha256":framed_sha256("setec.voiceprint.spec78.source-envelope-set-json.v1",canonical_json(sorted({r["judge"]["source_envelope_sha256"] for r in kept}))),"registration_path":registration.name,"manifest_path":manifest.name,"class_counts":_class_counts(kept,dropped,arm),"covered_length_range":_covered_range(primary,"n_words"),"covered_source_work_range":_covered_range(primary,"source_work_words") if arm == "segment_regime" else None,"segmenter":reg.get("segmenter"),"judge":reg["judge"],"bridge_read_mode":"single_pass_whole_text" if arm=="segment_regime" else None,"floors_applied":th["floors"],"bands_applied":th["bands"],"multiplicity":{"method":None,"alpha":None,"family":None},"deferrals":{"sign_stability":"deferred to M2","multiplicity":"deferred to M2"},"stated_limits":["custody_residue","judge_read_unproven","envelope_path_custody","prompt_scan_naming_only","bridge_read_unverified","shortness_residue"],"per_signal":per}
    receipt["derivation_sha256"]=framed_sha256("setec.voiceprint.spec78.derivation-json.v1",canonical_json(_round({k:v for k,v in receipt.items() if k!="derivation_sha256"})))
    return receipt

def verify_receipt(*, receipt: Path, arm: str, manifest: Path, thresholds: Path, registration: Path, date: str) -> None:
    try: actual=json.loads(receipt.read_text("utf-8"))
    except (OSError,json.JSONDecodeError): raise CalibrationRefusal("malformed_artifact")
    expected=evaluate(arm=arm,manifest=manifest,thresholds=thresholds,registration=registration,date=date)
    if _round(actual)!=_round(expected): raise CalibrationRefusal("registration_mismatch")

def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(); p.add_argument("--arm",required=True,choices=("segment_regime","subfloor")); p.add_argument("--manifest",type=Path,required=True); p.add_argument("--thresholds",type=Path,required=True); p.add_argument("--registration",type=Path); p.add_argument("--out",type=Path,required=True); p.add_argument("--date",required=True); mode=p.add_mutually_exclusive_group(required=True); mode.add_argument("--register",action="store_true"); mode.add_argument("--evaluate",action="store_true"); mode.add_argument("--verify",action="store_true"); p.add_argument("--generation-prompt",action="append",default=[]); p.add_argument("--segmenter-version"); p.add_argument("--segmenter-params-sha256"); p.add_argument("--segment-target-words",type=int); p.add_argument("--judge-kind"); p.add_argument("--judge-model"); p.add_argument("--judge-model-revision"); p.add_argument("--judge-prompt-version"); p.add_argument("--judge-context-bound-words",type=int); a=p.parse_args(argv)
    try:
        inputs=[a.manifest,a.thresholds]+([a.registration] if a.registration else [])
        if not a.verify and _aliases(a.out,inputs): raise CalibrationRefusal("malformed_artifact")
        if a.register:
            prompts=[]
            for item in a.generation_prompt:
                if "=" not in item: raise CalibrationRefusal("malformed_artifact")
                fam,path=item.split("=",1); q=Path(path)
                if not fam or not q.is_file(): raise CalibrationRefusal("malformed_artifact")
                try: prompt_text=q.read_text("utf-8")
                except (OSError, UnicodeDecodeError): raise CalibrationRefusal("malformed_artifact")
                if _prompt_names_signal(prompt_text): raise CalibrationRefusal("prompt_signal_blindness_violation")
                prompts.append({"prompt_family":fam,"prompt_sha256":_digest_file("setec.voiceprint.spec78.prompt-file.v1",q),"prompt_text_path":q.name})
            judge={"kind":a.judge_kind,"model":a.judge_model,"model_revision":a.judge_model_revision,"prompt_version":a.judge_prompt_version,"context_bound_words":a.judge_context_bound_words}
            segmenter=None if a.arm == "subfloor" else {"emitter":"narrative_decision_long_form","segmenter_version":a.segmenter_version,"params_sha256":a.segmenter_params_sha256,"segment_target_words":a.segment_target_words}
            result=build_registration(arm=a.arm,manifest=a.manifest,thresholds=a.thresholds,date=a.date,segmenter=segmenter,judge=judge,prompts=prompts)
            a.out.write_bytes(canonical_json(result)+b"\n")
        elif a.verify:
            if not a.registration: raise CalibrationRefusal("registration_mismatch")
            verify_receipt(receipt=a.out,arm=a.arm,manifest=a.manifest,thresholds=a.thresholds,registration=a.registration,date=a.date)
        else:
            if not a.registration: raise CalibrationRefusal("registration_mismatch")
            result=evaluate(arm=a.arm,manifest=a.manifest,thresholds=a.thresholds,registration=a.registration,date=a.date)
            assert_no_per_text_disclosure(result)
            a.out.write_bytes(canonical_json(result)+b"\n")
        return 0
    except CalibrationRefusal as e:
        print(e.reason,file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
