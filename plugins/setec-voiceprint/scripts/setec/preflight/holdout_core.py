"""Cross-set sealed-holdout matching with a one-bit generator release."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
import heapq
from pathlib import Path
import re
import unicodedata

from .common import (
    Manifest, Refusal, WorkBudget, canonical_json, exact_keys, parse_json,
    plain_hash, read_bounded, record_set_sha256, require_hex,
)
from .overlap_core import preflight_word_ngrams_v1

TOOL = "setec.preflight.holdout_firewall"
POLICY_SCHEMA = "setec-preflight-holdout-policy/1"
DETAIL_SCHEMA = "setec-preflight-holdout-detail/1"
RECEIPT_SCHEMA = "setec-preflight-holdout-receipt/1"
CONFLICTS_SCHEMA = "setec-preflight-holdout-conflicts/1"
CLASSES = ("exact", "shared_run", "candidate_contained", "sealed_contained",
           "declared_span")
STAGES = ("intake", "exact", "ngram", "span", "release")
REASONS = ("ok", "exact_conflict", "ngram_conflict",
           "ngram_insufficient_evidence", "span_conflict", "sealed_below_floor")
LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
CEILINGS = {"tokens": 4_000_000, "gram memberships": 4_000_000,
            "posting visits": 20_000_000, "cross pairs": 1_000_000,
            "span pair visits": 1_000_000}
DETAIL_LIMIT = 256 * 1024 * 1024
RECEIPT_LIMIT = 64 * 1024
CONFLICTS_LIMIT = 2 * 1024 * 1024


@dataclass(frozen=True)
class SealedSet:
    label: str
    manifest: Manifest


@dataclass(frozen=True)
class HoldoutPolicy:
    ngram: int
    shared_run: bool
    candidate_containment: tuple[int, int] | None
    sealed_containment: tuple[int, int] | None
    min_sealed_distinct: int


@dataclass(frozen=True)
class FirewallResult:
    pairs: tuple[dict, ...]
    candidate_classes: dict[str, frozenset[str]]
    stage_status: dict[str, str]
    reason_counts: dict[str, int]
    release: str
    candidate_gram_counts: dict[str, int]
    sealed_gram_counts: dict[tuple[str, str], int]


def load_holdout_policy(path: Path) -> tuple[HoldoutPolicy, str]:
    snapshot = read_bounded(path.parent, path.name, 64 * 1024)
    value = exact_keys(parse_json(snapshot.data, "policy_contract"),
                       {"schema", "ngram", "shared_run", "candidate_containment",
                        "sealed_containment", "min_sealed_distinct"}, "policy_contract")
    if (value["schema"] != POLICY_SCHEMA or type(value["ngram"]) is not int or
            not 2 <= value["ngram"] <= 64 or type(value["shared_run"]) is not bool or
            type(value["min_sealed_distinct"]) is not int or
            not 1 <= value["min_sealed_distinct"] <= 1_000_000):
        raise Refusal("policy_contract")

    def threshold(name: str) -> tuple[int, int] | None:
        item = value[name]
        if item is None:
            return None
        obj = exact_keys(item, {"numerator", "denominator"}, "policy_contract")
        numerator, denominator = obj["numerator"], obj["denominator"]
        if (type(numerator) is not int or type(denominator) is not int or
                not 1 <= numerator <= denominator <= 1_000_000):
            raise Refusal("policy_contract")
        return numerator, denominator

    candidate = threshold("candidate_containment")
    sealed = threshold("sealed_containment")
    if not value["shared_run"] and candidate is None and sealed is None:
        raise Refusal("policy_contract")
    return HoldoutPolicy(value["ngram"], value["shared_run"], candidate, sealed,
                         value["min_sealed_distinct"]), snapshot.sha256


def _span_pairs(candidates: Manifest, sealed: tuple[SealedSet, ...],
                budget: WorkBudget):
    candidate_by_source: dict[str, list] = defaultdict(list)
    sealed_by_source: dict[str, list[tuple[str, object]]] = defaultdict(list)
    for record in candidates.records:
        candidate_by_source[record.source_bytes_sha256].append(record)
    for item in sealed:
        for record in item.manifest.records:
            sealed_by_source[record.source_bytes_sha256].append((item.label, record))
    for source_hash, candidate_records in candidate_by_source.items():
        sealed_records = sorted(sealed_by_source.get(source_hash, ()),
                                key=lambda item: (item[1].start_byte, item[1].end_byte,
                                                  item[0], item[1].id))
        if not sealed_records:
            continue
        starts = [item[1].start_byte for item in sealed_records]
        active: set[int] = set()
        ending: list[tuple[int, int]] = []
        pointer = 0
        for candidate in sorted(candidate_records,
                                key=lambda record: (record.start_byte, record.end_byte,
                                                    record.id)):
            while pointer < len(sealed_records) and starts[pointer] <= candidate.start_byte:
                index = pointer
                pointer += 1
                record = sealed_records[index][1]
                if record.end_byte > candidate.start_byte:
                    active.add(index)
                    heapq.heappush(ending, (record.end_byte, index))
            while ending and ending[0][0] <= candidate.start_byte:
                _, index = heapq.heappop(ending)
                active.discard(index)
            stop = bisect_left(starts, candidate.end_byte, pointer)
            for index in active:
                budget.charge("span pair visits")
                label, sealed_record = sealed_records[index]
                yield candidate.id, label, sealed_record.id
            for index in range(pointer, stop):
                budget.charge("span pair visits")
                label, sealed_record = sealed_records[index]
                yield candidate.id, label, sealed_record.id


def holdout_firewall(candidates: Manifest, sealed: tuple[SealedSet, ...],
                     policy: HoldoutPolicy, budget: WorkBudget) -> FirewallResult:
    sealed_records = {(item.label, record.id): record
                      for item in sealed for record in item.manifest.records}
    candidate_grams = {record.id: preflight_word_ngrams_v1(record.analysis_text,
                                                            policy.ngram, budget)
                       for record in candidates.records}
    sealed_grams = {key: preflight_word_ngrams_v1(record.analysis_text,
                                                  policy.ngram, budget)
                    for key, record in sealed_records.items()}
    pair_classes: dict[tuple[str, str, str], set[str]] = {}
    shared: dict[tuple[str, str, str], int] = defaultdict(int)

    def touch(key: tuple[str, str, str]) -> set[str]:
        if key not in pair_classes:
            budget.charge("cross pairs")
            pair_classes[key] = set()
        return pair_classes[key]

    by_analysis: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (label, sealed_id), record in sealed_records.items():
        by_analysis[record.analysis_sha256].append((label, sealed_id))
    for candidate in candidates.records:
        for label, sealed_id in by_analysis[candidate.analysis_sha256]:
            touch((candidate.id, label, sealed_id)).add("exact")

    postings: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for key, grams in sealed_grams.items():
        for gram in grams:
            postings[gram].append(key)
    for candidate in candidates.records:
        for gram in candidate_grams[candidate.id]:
            for label, sealed_id in postings.get(gram, ()):
                budget.charge("posting visits")
                key = (candidate.id, label, sealed_id)
                touch(key)
                shared[key] += 1
    for key, count in shared.items():
        candidate_id, label, sealed_id = key
        classes = pair_classes[key]
        if policy.shared_run:
            classes.add("shared_run")
        if policy.candidate_containment is not None:
            numerator, denominator = policy.candidate_containment
            size = len(candidate_grams[candidate_id])
            if size and count * denominator >= numerator * size:
                classes.add("candidate_contained")
        if policy.sealed_containment is not None:
            numerator, denominator = policy.sealed_containment
            size = len(sealed_grams[(label, sealed_id)])
            if size and count * denominator >= numerator * size:
                classes.add("sealed_contained")
    for key in _span_pairs(candidates, sealed, budget):
        touch(key).add("declared_span")
    pairs = tuple({"candidate_id": candidate_id, "sealed_label": label,
                   "sealed_id": sealed_id, "classes": sorted(classes),
                   "shared_grams": shared[(candidate_id, label, sealed_id)]}
                  for (candidate_id, label, sealed_id), classes in sorted(pair_classes.items())
                  if classes)
    candidate_classes: dict[str, set[str]] = {record.id: set() for record in candidates.records}
    for pair in pairs:
        candidate_classes[pair["candidate_id"]].update(pair["classes"])
    exact_count = sum("exact" in pair["classes"] for pair in pairs)
    ngram_count = sum(any(name in pair["classes"] for name in
                          ("shared_run", "candidate_contained", "sealed_contained"))
                      for pair in pairs)
    span_count = sum("declared_span" in pair["classes"] for pair in pairs)
    empty_grams = sum(not grams for grams in candidate_grams.values()) + sum(
        not grams for grams in sealed_grams.values())
    below_floor = sum(len({record.analysis_sha256 for record in item.manifest.records}) <
                      policy.min_sealed_distinct for item in sealed)
    release = "withheld" if below_floor else "released"
    statuses = {"intake": "passed", "exact": "failed" if exact_count else "passed",
                "ngram": "failed" if ngram_count else
                         "needs_human_review" if empty_grams else "passed",
                "span": "failed" if span_count else "passed",
                "release": "needs_human_review" if below_floor else "passed"}
    reasons = {"ok": sum(value == "passed" for value in statuses.values()),
               "exact_conflict": exact_count, "ngram_conflict": ngram_count,
               "ngram_insufficient_evidence": empty_grams,
               "span_conflict": span_count, "sealed_below_floor": below_floor}
    return FirewallResult(pairs,
                          {key: frozenset(value) for key, value in candidate_classes.items()},
                          statuses, reasons, release,
                          {key: len(value) for key, value in candidate_grams.items()},
                          {key: len(value) for key, value in sealed_grams.items()})


def build_outputs(result: FirewallResult, candidates: Manifest,
                  sealed: tuple[SealedSet, ...], policy_sha256: str) -> tuple[bytes, bytes, bytes | None]:
    sealed_manifests = sorted(
        ({"label": item.label, "manifest_sha256": item.manifest.manifest_sha256}
         for item in sealed), key=lambda item: item["label"])
    candidate_classes = result.candidate_classes
    detail = {
        "schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
        "unicode_version": unicodedata.unidata_version,
        "inputs": {"candidate_manifest_sha256": candidates.manifest_sha256,
                   "policy_sha256": policy_sha256,
                   "sealed_manifests": sealed_manifests},
        "candidates": [{"id": record.id, "analysis_sha256": record.analysis_sha256,
                        "gram_count": result.candidate_gram_counts[record.id],
                        "classes": sorted(candidate_classes[record.id])}
                       for record in sorted(candidates.records, key=lambda item: item.id)],
        "sealed_records": [{"label": item.label, "id": record.id,
                            "analysis_sha256": record.analysis_sha256,
                            "gram_count": result.sealed_gram_counts[(item.label, record.id)]}
                           for item in sorted(sealed, key=lambda item: item.label)
                           for record in sorted(item.manifest.records, key=lambda row: row.id)],
        "pairs": list(result.pairs), "stage_status": result.stage_status,
        "reason_counts": result.reason_counts,
    }
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > DETAIL_LIMIT:
        raise Refusal("size_limit")
    conflicts = {
        "schema": CONFLICTS_SCHEMA, "tool": TOOL, "tool_version": 1,
        "candidate_manifest_sha256": candidates.manifest_sha256,
        "record_set_sha256": record_set_sha256(candidates.records),
        "conflicts": [{"candidate_id": record_id, "conflict_class": "holdout_conflict",
                       "severity": "remove"}
                      for record_id in sorted(candidate_classes) if candidate_classes[record_id]],
    }
    conflicts_bytes = canonical_json(conflicts)
    if len(conflicts_bytes) > CONFLICTS_LIMIT:
        raise Refusal("size_limit")
    if result.release == "withheld":
        conflicts_to_publish = None
    else:
        conflicts_to_publish = conflicts_bytes
    sealed_counts = []
    for item in sorted(sealed, key=lambda sealed_set: sealed_set.label):
        label = item.label
        conflicting = {pair["sealed_id"] for pair in result.pairs
                       if pair["sealed_label"] == label}
        sealed_counts.append({
            "label": label, "manifest_sha256": item.manifest.manifest_sha256,
            "distinct_record_count": len({record.analysis_sha256
                                          for record in item.manifest.records}),
            "conflicting_record_count": len(conflicting),
        })
    class_counts = {name: sum(name in classes for classes in candidate_classes.values())
                    for name in CLASSES}
    receipt = {
        "schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
        "candidate_manifest_sha256": candidates.manifest_sha256,
        "policy_sha256": policy_sha256,
        "sealed": sealed_counts,
        "detail_sha256": plain_hash(detail_bytes),
        "conflicts_sha256": plain_hash(conflicts_bytes) if conflicts_to_publish else None,
        "release": result.release, "candidate_count": len(candidates.records),
        "conflicting_candidate_count": sum(bool(classes) for classes in candidate_classes.values()),
        "class_counts": class_counts, "stage_status": result.stage_status,
        "reason_counts": result.reason_counts, "span_evidence": "declared",
        "record_set_sha256": record_set_sha256(candidates.records),
    }
    receipt_bytes = canonical_json(receipt)
    if len(receipt_bytes) > RECEIPT_LIMIT:
        raise Refusal("size_limit")
    return detail_bytes, receipt_bytes, conflicts_to_publish


def _load(path: Path, expected_sha256: str, limit: int, schema: str,
          keys: set[str], code: str) -> dict:
    require_hex(expected_sha256, code)
    try:
        snapshot = read_bounded(path.parent, path.name, limit)
    except Refusal:
        raise Refusal(code) from None
    if snapshot.sha256 != expected_sha256:
        raise Refusal(code)
    value = exact_keys(parse_json(snapshot.data, code), keys, code)
    if (value["schema"] != schema or value["tool"] != TOOL or
            type(value["tool_version"]) is not int or value["tool_version"] != 1 or
            canonical_json(value) != snapshot.data):
        raise Refusal(code)
    return value


def _status_map(value: object, code: str) -> dict:
    result = exact_keys(value, set(STAGES), code)
    if any(type(item) is not str or item not in
           {"passed", "failed", "needs_human_review", "not_run"} for item in result.values()):
        raise Refusal(code)
    return result


def _reason_map(value: object, code: str) -> dict:
    result = exact_keys(value, set(REASONS), code)
    if any(type(item) is not int or item < 0 for item in result.values()):
        raise Refusal(code)
    return result


def _classes(value: object, code: str) -> list[str]:
    if (type(value) is not list or any(type(item) is not str or item not in CLASSES
                                      for item in value) or
            value != sorted(set(value))):
        raise Refusal(code)
    return value


def load_holdout_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load(path, expected_sha256, DETAIL_LIMIT, DETAIL_SCHEMA,
                  {"schema", "tool", "tool_version", "unicode_version", "inputs",
                   "candidates", "sealed_records", "pairs", "stage_status",
                   "reason_counts"}, code)
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    inputs = exact_keys(value["inputs"],
                        {"candidate_manifest_sha256", "policy_sha256", "sealed_manifests"}, code)
    require_hex(inputs["candidate_manifest_sha256"], code)
    require_hex(inputs["policy_sha256"], code)
    sealed_manifests = inputs["sealed_manifests"]
    if type(sealed_manifests) is not list or not 1 <= len(sealed_manifests) <= 8:
        raise Refusal(code)
    labels = []
    for item in sealed_manifests:
        obj = exact_keys(item, {"label", "manifest_sha256"}, code)
        if type(obj["label"]) is not str or LABEL.fullmatch(obj["label"]) is None:
            raise Refusal(code)
        require_hex(obj["manifest_sha256"], code)
        labels.append(obj["label"])
    if labels != sorted(set(labels)):
        raise Refusal(code)
    candidates = value["candidates"]
    sealed = value["sealed_records"]
    pairs = value["pairs"]
    if (type(candidates) is not list or not candidates or type(sealed) is not list or
            not sealed or type(pairs) is not list):
        raise Refusal(code)
    candidate_ids = []
    candidate_classes = {}
    candidate_facts = {}
    for item in candidates:
        obj = exact_keys(item, {"id", "analysis_sha256", "gram_count", "classes"}, code)
        if (type(obj["id"]) is not str or not obj["id"] or
                type(obj["gram_count"]) is not int or obj["gram_count"] < 0):
            raise Refusal(code)
        require_hex(obj["analysis_sha256"], code)
        candidate_ids.append(obj["id"])
        candidate_classes[obj["id"]] = _classes(obj["classes"], code)
        candidate_facts[obj["id"]] = (obj["analysis_sha256"], obj["gram_count"])
    if candidate_ids != sorted(set(candidate_ids)):
        raise Refusal(code)
    sealed_ids = []
    sealed_facts = {}
    for item in sealed:
        obj = exact_keys(item, {"label", "id", "analysis_sha256", "gram_count"}, code)
        if (type(obj["label"]) is not str or obj["label"] not in labels or
                type(obj["id"]) is not str or not obj["id"] or
                type(obj["gram_count"]) is not int or obj["gram_count"] < 0):
            raise Refusal(code)
        require_hex(obj["analysis_sha256"], code)
        sealed_ids.append((obj["label"], obj["id"]))
        sealed_facts[(obj["label"], obj["id"])] = (
            obj["analysis_sha256"], obj["gram_count"])
    if sealed_ids != sorted(set(sealed_ids)):
        raise Refusal(code)
    known_sealed_ids = set(sealed_ids)
    pair_ids = []
    union: dict[str, set[str]] = defaultdict(set)
    for item in pairs:
        obj = exact_keys(item, {"candidate_id", "sealed_label", "sealed_id",
                                "classes", "shared_grams"}, code)
        if (type(obj["candidate_id"]) is not str or obj["candidate_id"] not in candidate_classes or
                type(obj["sealed_label"]) is not str or type(obj["sealed_id"]) is not str or
                (obj["sealed_label"], obj["sealed_id"]) not in known_sealed_ids or
                type(obj["shared_grams"]) is not int or obj["shared_grams"] < 0):
            raise Refusal(code)
        classes = _classes(obj["classes"], code)
        candidate_analysis, candidate_gram_count = candidate_facts[obj["candidate_id"]]
        sealed_analysis, sealed_gram_count = sealed_facts[(obj["sealed_label"], obj["sealed_id"])]
        if (not classes or obj["shared_grams"] > min(candidate_gram_count, sealed_gram_count) or
                ("exact" in classes) != (candidate_analysis == sealed_analysis) or
                (any(name in classes for name in CLASSES[1:4]) and
                 obj["shared_grams"] == 0)):
            raise Refusal(code)
        pair_ids.append((obj["candidate_id"], obj["sealed_label"], obj["sealed_id"]))
        union[obj["candidate_id"]].update(classes)
    if pair_ids != sorted(set(pair_ids)) or any(
            candidate_classes[key] != sorted(union[key]) for key in candidate_ids):
        raise Refusal(code)
    statuses = _status_map(value["stage_status"], code)
    reasons = _reason_map(value["reason_counts"], code)
    exact_count = sum("exact" in pair["classes"] for pair in pairs)
    ngram_count = sum(any(name in pair["classes"] for name in CLASSES[1:4])
                      for pair in pairs)
    span_count = sum("declared_span" in pair["classes"] for pair in pairs)
    empty_grams = sum(item["gram_count"] == 0 for item in candidates + sealed)
    expected = {"intake": "passed", "exact": "failed" if exact_count else "passed",
                "ngram": "failed" if ngram_count else
                         "needs_human_review" if empty_grams else "passed",
                "span": "failed" if span_count else "passed",
                "release": statuses["release"]}
    if (statuses != expected or reasons["ok"] != sum(name == "passed" for name in statuses.values()) or
            reasons["exact_conflict"] != exact_count or reasons["ngram_conflict"] != ngram_count or
            reasons["ngram_insufficient_evidence"] != empty_grams or
            reasons["span_conflict"] != span_count or
            statuses["release"] != ("needs_human_review" if reasons["sealed_below_floor"] else "passed")):
        raise Refusal(code)
    return value


def load_holdout_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_LIMIT, RECEIPT_SCHEMA,
                  {"schema", "tool", "tool_version", "candidate_manifest_sha256",
                   "policy_sha256", "sealed", "detail_sha256", "conflicts_sha256",
                   "release", "candidate_count", "conflicting_candidate_count",
                   "class_counts", "stage_status", "reason_counts", "span_evidence",
                   "record_set_sha256"}, code)
    for name in ("candidate_manifest_sha256", "policy_sha256", "detail_sha256",
                 "record_set_sha256"):
        require_hex(value[name], code)
    if value["conflicts_sha256"] is not None:
        require_hex(value["conflicts_sha256"], code)
    if type(value["sealed"]) is not list or not 1 <= len(value["sealed"]) <= 8:
        raise Refusal(code)
    labels = []
    for item in value["sealed"]:
        obj = exact_keys(item, {"label", "manifest_sha256", "distinct_record_count",
                                "conflicting_record_count"}, code)
        if (type(obj["label"]) is not str or LABEL.fullmatch(obj["label"]) is None or
                type(obj["distinct_record_count"]) is not int or
                not 1 <= obj["distinct_record_count"] <= 5000 or
                type(obj["conflicting_record_count"]) is not int or
                not 0 <= obj["conflicting_record_count"] <= 5000):
            raise Refusal(code)
        require_hex(obj["manifest_sha256"], code)
        labels.append(obj["label"])
    if labels != sorted(set(labels)):
        raise Refusal(code)
    if (type(value["candidate_count"]) is not int or not 1 <= value["candidate_count"] <= 5000 or
            type(value["conflicting_candidate_count"]) is not int or
            not 0 <= value["conflicting_candidate_count"] <= value["candidate_count"]):
        raise Refusal(code)
    counts = exact_keys(value["class_counts"], set(CLASSES), code)
    if any(type(item) is not int or not 0 <= item <= value["candidate_count"]
           for item in counts.values()):
        raise Refusal(code)
    statuses = _status_map(value["stage_status"], code)
    reasons = _reason_map(value["reason_counts"], code)
    expected_stages = {
        "intake": "passed",
        "exact": "failed" if reasons["exact_conflict"] else "passed",
        "ngram": ("failed" if reasons["ngram_conflict"] else
                  "needs_human_review" if reasons["ngram_insufficient_evidence"] else "passed"),
        "span": "failed" if reasons["span_conflict"] else "passed",
        "release": "passed" if value["release"] == "released" else "needs_human_review",
    }
    ngram_candidates = max(counts[name] for name in CLASSES[1:4])
    if (value["release"] not in ("released", "withheld") or
            value["span_evidence"] != "declared" or
            (value["conflicts_sha256"] is None) != (value["release"] == "withheld") or
            statuses != expected_stages or
            (reasons["sealed_below_floor"] > 0) != (value["release"] == "withheld") or
            (reasons["exact_conflict"] > 0) != (counts["exact"] > 0) or
            (reasons["ngram_conflict"] > 0) != (ngram_candidates > 0) or
            (reasons["span_conflict"] > 0) != (counts["declared_span"] > 0) or
            max(counts.values()) > value["conflicting_candidate_count"] or
            value["conflicting_candidate_count"] > sum(counts.values()) or
            reasons["ok"] != sum(status == "passed" for status in statuses.values()) or
            reasons["sealed_below_floor"] > len(value["sealed"])):
        raise Refusal(code)
    return value


def load_holdout_conflicts(path: Path, candidate_manifest: Manifest) -> dict:
    code = "receipt_contract"
    try:
        snapshot = read_bounded(path.parent, path.name, CONFLICTS_LIMIT)
    except Refusal:
        raise Refusal(code) from None
    value = exact_keys(parse_json(snapshot.data, code),
                       {"schema", "tool", "tool_version", "candidate_manifest_sha256",
                        "record_set_sha256", "conflicts"}, code)
    if (value["schema"] != CONFLICTS_SCHEMA or value["tool"] != TOOL or
            type(value["tool_version"]) is not int or value["tool_version"] != 1 or
            canonical_json(value) != snapshot.data or
            value["candidate_manifest_sha256"] != candidate_manifest.manifest_sha256 or
            value["record_set_sha256"] != record_set_sha256(candidate_manifest.records) or
            type(value["conflicts"]) is not list):
        raise Refusal(code)
    known = {record.id for record in candidate_manifest.records}
    ids = []
    for item in value["conflicts"]:
        obj = exact_keys(item, {"candidate_id", "conflict_class", "severity"}, code)
        if (type(obj["candidate_id"]) is not str or obj["candidate_id"] not in known or
                obj["conflict_class"] != "holdout_conflict" or obj["severity"] != "remove"):
            raise Refusal(code)
        ids.append(obj["candidate_id"])
    if ids != sorted(set(ids)):
        raise Refusal(code)
    return value
