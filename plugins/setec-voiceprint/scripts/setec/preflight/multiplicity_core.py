"""Validate operator admission weights against overlap clusters."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .common import (
    Manifest, Refusal, canonical_json, exact_keys, parse_json, plain_hash,
    read_bounded, require_hex, OVERLAP_DETAIL_LIMIT,
)

TOOL = "setec.preflight.multiplicity"
POLICY_SCHEMA = "setec-preflight-multiplicity-policy/1"
ADMISSION_SCHEMA = "setec-preflight-admission/1"
DETAIL_SCHEMA = "setec-preflight-multiplicity-detail/1"
RECEIPT_SCHEMA = "setec-preflight-multiplicity-receipt/1"
PURPOSES = ("rewrite_mirror", "conditioning_target", "pretraining_corpus",
            "set_level_diversity", "evaluation_fixture")
TRAINING_PURPOSES = PURPOSES[:3]
RULES = ("one_representative_per_cluster", "cap_per_cluster",
         "cluster_weighting", "no_training_consumption")
WITHHOLDING_REASONS = ("exact_copy_of_admitted", "distinct_text_in_admitted_cluster",
                       "cluster_not_admitted")
REASON_CODES = ("ok", "admission_not_supplied", "no_training_consumption",
                "no_admitted_records", "cluster_over_limit", "representative_weight",
                "cluster_weight_sum", "admitted_exact_duplicate", "distinct_text_withheld")
COUNT_KEYS = ("records", "clusters", "admitted", "withheld", "withheld_exact_copy",
              "withheld_distinct_in_admitted_cluster", "withheld_cluster_not_admitted",
              "clusters_with_admitted", "clusters_multi_admitted", "max_admitted_per_cluster")


@dataclass(frozen=True)
class MultiplicityPolicy:
    purpose: str
    rule: str
    cap: int | None


@dataclass(frozen=True)
class Admission:
    admitted: bool
    micro_weight: int


@dataclass(frozen=True)
class MultiplicityResult:
    status: str
    reason_counts: dict[str, int]
    counts: dict[str, int] | None
    clusters: tuple[dict, ...] | None
    withheld: tuple[dict, ...] | None


def _control(path: Path, limit: int, schema: str, keys: set[str], code: str) -> tuple[dict, str]:
    snapshot = read_bounded(path.parent, path.name, limit)
    value = exact_keys(parse_json(snapshot.data, code), keys, code)
    if value["schema"] != schema:
        raise Refusal(code)
    return value, snapshot.sha256


def load_multiplicity_policy(path: Path) -> tuple[MultiplicityPolicy, str]:
    value, sha = _control(path, 64 * 1024, POLICY_SCHEMA,
                          {"schema", "purpose", "rule", "cap"}, "policy_contract")
    purpose, rule, cap = value["purpose"], value["rule"], value["cap"]
    if (type(purpose) is not str or purpose not in PURPOSES or
            type(rule) is not str or rule not in RULES or
            (purpose in TRAINING_PURPOSES) == (rule == "no_training_consumption") or
            (rule == "cap_per_cluster" and
             (type(cap) is not int or not 1 <= cap <= 5000)) or
            (rule != "cap_per_cluster" and cap is not None)):
        raise Refusal("policy_contract")
    return MultiplicityPolicy(purpose, rule, cap), sha


def load_admission_map(path: Path, manifest: Manifest, overlap_detail_sha256: str,
                       policy: MultiplicityPolicy) -> tuple[dict[str, Admission], str]:
    # An empty argument names no map; it is refused, never read as "not supplied".
    if policy.rule == "no_training_consumption" or not path.name:
        raise Refusal("admission_contract")
    value, sha = _control(path, 8 * 1024 * 1024, ADMISSION_SCHEMA,
                          {"schema", "overlap_detail_sha256", "assignments"},
                          "admission_contract")
    if value["overlap_detail_sha256"] != overlap_detail_sha256:
        raise Refusal("admission_contract")
    assignments = value["assignments"]
    if type(assignments) is not dict or set(assignments) != {r.id for r in manifest.records}:
        raise Refusal("admission_contract")
    result: dict[str, Admission] = {}
    for record_id, entry in assignments.items():
        item = exact_keys(entry, {"admitted", "micro_weight"}, "admission_contract")
        weight = item["micro_weight"]
        if (type(weight) is not int or not 0 <= weight <= 1_000_000 or
                type(item["admitted"]) is not bool or item["admitted"] != (weight > 0)):
            raise Refusal("admission_contract")
        result[record_id] = Admission(item["admitted"], weight)
    return result, sha


def evaluate_multiplicity(detail: dict, policy: MultiplicityPolicy,
                          admission: dict[str, Admission] | None) -> MultiplicityResult:
    reasons = {name: 0 for name in REASON_CODES}
    if policy.rule == "no_training_consumption":
        reasons["no_training_consumption"] = 1
        return MultiplicityResult("not_run", reasons, None, None, None)
    if admission is None:
        reasons["admission_not_supplied"] = 1
        return MultiplicityResult("not_run", reasons, None, None, None)
    records = {row["id"]: row for row in detail["records"]}
    if set(admission) != set(records):
        raise Refusal("admission_contract")
    cluster_rows = []
    withheld_rows = []
    admitted_total = 0
    clusters_with_admitted = 0
    clusters_multi_admitted = 0
    maximum = 0
    for cluster in detail["clusters"]:
        members = cluster["member_ids"]
        admitted = [record_id for record_id in members if admission[record_id].admitted]
        admitted_total += len(admitted)
        clusters_with_admitted += bool(admitted)
        clusters_multi_admitted += len(admitted) >= 2
        maximum = max(maximum, len(admitted))
        total_weight = sum(admission[record_id].micro_weight for record_id in members)
        violations: list[str] = []
        if (policy.rule == "one_representative_per_cluster" and len(admitted) > 1 or
                policy.rule == "cap_per_cluster" and len(admitted) > policy.cap):
            violations.append("cluster_over_limit")
            reasons["cluster_over_limit"] += 1
        if policy.rule == "one_representative_per_cluster":
            bad_weights = sum(admission[record_id].micro_weight != 1_000_000
                              for record_id in admitted)
            if bad_weights:
                violations.append("representative_weight")
                reasons["representative_weight"] += bad_weights
        if policy.rule == "cluster_weighting" and total_weight not in (0, 1_000_000):
            violations.append("cluster_weight_sum")
            reasons["cluster_weight_sum"] += 1
        analysis_of_admitted = Counter(records[record_id]["analysis_sha256"]
                                       for record_id in admitted)
        duplicate_values = sum(count >= 2 for count in analysis_of_admitted.values())
        reasons["admitted_exact_duplicate"] += duplicate_values
        if duplicate_values:
            violations.append("admitted_exact_duplicate")
        for record_id in members:
            if admission[record_id].admitted:
                continue
            if records[record_id]["analysis_sha256"] in analysis_of_admitted:
                reason = "exact_copy_of_admitted"
            elif admitted:
                reason = "distinct_text_in_admitted_cluster"
            else:
                reason = "cluster_not_admitted"
            withheld_rows.append({"id": record_id, "cluster_sha256": cluster["cluster_sha256"],
                                  "reason": reason})
        cluster_rows.append({"cluster_sha256": cluster["cluster_sha256"],
                             "member_count": len(members), "admitted_count": len(admitted),
                             "weight_sum": total_weight, "violations": sorted(violations)})
    reason_partition = Counter(row["reason"] for row in withheld_rows)
    reasons["distinct_text_withheld"] = reason_partition["distinct_text_in_admitted_cluster"]
    reasons["no_admitted_records"] = int(admitted_total == 0)
    if any(reasons[name] for name in ("cluster_over_limit", "representative_weight",
                                     "cluster_weight_sum", "admitted_exact_duplicate")):
        status = "failed"
    elif reasons["no_admitted_records"] or reasons["distinct_text_withheld"]:
        status = "needs_human_review"
    else:
        status = "passed"
    reasons["ok"] = int(status == "passed")
    counts = {
        "records": len(records), "clusters": len(cluster_rows),
        "admitted": admitted_total, "withheld": len(withheld_rows),
        "withheld_exact_copy": reason_partition["exact_copy_of_admitted"],
        "withheld_distinct_in_admitted_cluster":
            reason_partition["distinct_text_in_admitted_cluster"],
        "withheld_cluster_not_admitted": reason_partition["cluster_not_admitted"],
        "clusters_with_admitted": clusters_with_admitted,
        "clusters_multi_admitted": clusters_multi_admitted,
        "max_admitted_per_cluster": maximum,
    }
    if (counts["admitted"] + counts["withheld"] != counts["records"] or
            sum(counts[name] for name in ("withheld_exact_copy",
                                          "withheld_distinct_in_admitted_cluster",
                                          "withheld_cluster_not_admitted")) != counts["withheld"] or
            len(withheld_rows) != counts["withheld"]):
        raise Refusal("internal_refusal")
    return MultiplicityResult(status, reasons, counts,
                              tuple(sorted(cluster_rows, key=lambda row: row["cluster_sha256"])),
                              tuple(sorted(withheld_rows, key=lambda row: row["id"])))


def build_multiplicity_detail(manifest: Manifest, policy: MultiplicityPolicy,
                              policy_sha256: str, overlap_detail_sha256: str,
                              admission_map_sha256: str | None,
                              result: MultiplicityResult) -> dict:
    from .common import record_set_sha256

    return {
        "schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
        "inputs": {"manifest_sha256": manifest.manifest_sha256,
                   "record_set_sha256": record_set_sha256(manifest.records),
                   "policy_sha256": policy_sha256,
                   "overlap_detail_sha256": overlap_detail_sha256,
                   "admission_map_sha256": admission_map_sha256},
        "purpose": policy.purpose, "rule": policy.rule, "cap": policy.cap,
        "clusters": list(result.clusters) if result.clusters is not None else None,
        "withheld": list(result.withheld) if result.withheld is not None else None,
        "stage_status": {"multiplicity": result.status},
        "reason_counts": result.reason_counts, "counts": result.counts,
    }


def build_multiplicity_receipt(detail: dict) -> dict:
    return {
        "schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
        **detail["inputs"], "detail_sha256": plain_hash(canonical_json(detail)),
        "purpose": detail["purpose"], "rule": detail["rule"], "cap": detail["cap"],
        "stage_status": detail["stage_status"], "reason_counts": detail["reason_counts"],
        "counts": detail["counts"],
    }


def _load(path: Path, expected_sha256: str, schema: str, keys: set[str], code: str,
          limit: int) -> dict:
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


def _validate_policy_fields(value: dict, code: str) -> None:
    purpose, rule, cap = value["purpose"], value["rule"], value["cap"]
    if (type(purpose) is not str or purpose not in PURPOSES or
            type(rule) is not str or rule not in RULES or
            (purpose in TRAINING_PURPOSES) == (rule == "no_training_consumption") or
            (rule == "cap_per_cluster" and (type(cap) is not int or not 1 <= cap <= 5000)) or
            (rule != "cap_per_cluster" and cap is not None)):
        raise Refusal(code)


def _validate_common(value: dict, code: str) -> str:
    _validate_policy_fields(value, code)
    status = exact_keys(value["stage_status"], {"multiplicity"}, code)["multiplicity"]
    if type(status) is not str or status not in {"passed", "failed", "needs_human_review", "not_run"}:
        raise Refusal(code)
    reasons = exact_keys(value["reason_counts"], set(REASON_CODES), code)
    if any(type(number) is not int or number < 0 for number in reasons.values()):
        raise Refusal(code)
    if (reasons["ok"] != int(status == "passed") or
            any(reasons[name] > 1 for name in ("admission_not_supplied",
                                                  "no_training_consumption", "no_admitted_records"))):
        raise Refusal(code)
    admission_hash = (value["inputs"]["admission_map_sha256"] if "inputs" in value
                      else value["admission_map_sha256"])
    if value["rule"] == "no_training_consumption":
        expected_status = "not_run"
        if (admission_hash is not None or reasons["no_training_consumption"] != 1 or
                reasons["admission_not_supplied"] or reasons["no_admitted_records"]):
            raise Refusal(code)
    elif admission_hash is None:
        expected_status = "not_run"
        if (reasons["admission_not_supplied"] != 1 or
                reasons["no_training_consumption"] or reasons["no_admitted_records"]):
            raise Refusal(code)
    elif any(reasons[name] for name in ("cluster_over_limit", "representative_weight",
                                         "cluster_weight_sum", "admitted_exact_duplicate")):
        expected_status = "failed"
    elif reasons["no_admitted_records"] or reasons["distinct_text_withheld"]:
        expected_status = "needs_human_review"
    else:
        expected_status = "passed"
    if status != expected_status:
        raise Refusal(code)
    if admission_hash is not None and (reasons["admission_not_supplied"] or
                                       reasons["no_training_consumption"]):
        raise Refusal(code)
    if status == "not_run" and any(reasons[name] for name in (
            "cluster_over_limit", "representative_weight", "cluster_weight_sum",
            "admitted_exact_duplicate", "distinct_text_withheld")):
        raise Refusal(code)
    counts = value["counts"]
    if status == "not_run":
        if counts is not None:
            raise Refusal(code)
    else:
        counts = exact_keys(counts, set(COUNT_KEYS), code)
        if (any(type(number) is not int or number < 0 for number in counts.values()) or
                not 1 <= counts["records"] <= 5000 or
                not 1 <= counts["clusters"] <= counts["records"] or
                counts["admitted"] + counts["withheld"] != counts["records"] or
                counts["withheld"] != sum(counts[name] for name in (
                    "withheld_exact_copy", "withheld_distinct_in_admitted_cluster",
                    "withheld_cluster_not_admitted")) or
                reasons["distinct_text_withheld"] != counts["withheld_distinct_in_admitted_cluster"] or
                reasons["no_admitted_records"] != int(counts["admitted"] == 0)):
            raise Refusal(code)
        if (counts["clusters_with_admitted"] > counts["clusters"] or
                counts["clusters_multi_admitted"] > counts["clusters_with_admitted"] or
                counts["max_admitted_per_cluster"] > counts["admitted"] or
                (counts["max_admitted_per_cluster"] == 0) != (counts["admitted"] == 0) or
                (counts["max_admitted_per_cluster"] >= 2) !=
                (counts["clusters_multi_admitted"] > 0) or
                reasons["admitted_exact_duplicate"] > counts["admitted"] // 2):
            raise Refusal(code)
        # A violating cluster has an admitted member (a zero-sum cluster admits
        # nothing), each counted record is admitted, and every cluster without
        # an admitted member withholds at least one record as not admitted.
        if (reasons["cluster_over_limit"] > counts["clusters_with_admitted"] or
                reasons["cluster_weight_sum"] > counts["clusters_with_admitted"] or
                reasons["representative_weight"] > counts["admitted"] or
                counts["clusters_with_admitted"] + counts["clusters_multi_admitted"] >
                counts["admitted"] or
                counts["withheld_cluster_not_admitted"] <
                counts["clusters"] - counts["clusters_with_admitted"] or
                (counts["withheld_cluster_not_admitted"] == 0) !=
                (counts["clusters_with_admitted"] == counts["clusters"])):
            raise Refusal(code)
        if value["rule"] == "one_representative_per_cluster" or (
                value["rule"] == "cap_per_cluster" and value["cap"] == 1):
            if reasons["cluster_over_limit"] != counts["clusters_multi_admitted"]:
                raise Refusal(code)
        elif value["rule"] == "cap_per_cluster":
            if (reasons["cluster_over_limit"] > counts["clusters_multi_admitted"] or
                    (counts["max_admitted_per_cluster"] > value["cap"] and
                     reasons["cluster_over_limit"] == 0) or
                    (counts["max_admitted_per_cluster"] <= value["cap"] and
                     reasons["cluster_over_limit"] != 0)):
                raise Refusal(code)
        elif reasons["cluster_over_limit"] != 0:
            raise Refusal(code)
        if (value["rule"] != "one_representative_per_cluster" and
                reasons["representative_weight"] != 0 or
                value["rule"] != "cluster_weighting" and reasons["cluster_weight_sum"] != 0):
            raise Refusal(code)
    return status


def load_multiplicity_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load(path, expected_sha256, DETAIL_SCHEMA,
                  {"schema", "tool", "tool_version", "inputs", "purpose", "rule", "cap",
                   "clusters", "withheld", "stage_status", "reason_counts", "counts"},
                  code, OVERLAP_DETAIL_LIMIT)
    inputs = exact_keys(value["inputs"], {"manifest_sha256", "record_set_sha256",
                                           "policy_sha256", "overlap_detail_sha256",
                                           "admission_map_sha256"}, code)
    for name, item in inputs.items():
        if name == "admission_map_sha256" and item is None:
            continue
        require_hex(item, code)
    status = _validate_common(value, code)
    if status == "not_run":
        if value["clusters"] is not None or value["withheld"] is not None:
            raise Refusal(code)
    else:
        if type(value["clusters"]) is not list or type(value["withheld"]) is not list:
            raise Refusal(code)
        cluster_ids = []
        for cluster in value["clusters"]:
            item = exact_keys(cluster, {"cluster_sha256", "member_count", "admitted_count",
                                        "weight_sum", "violations"}, code)
            require_hex(item["cluster_sha256"], code)
            if (any(type(item[name]) is not int or item[name] < 0 for name in
                    ("member_count", "admitted_count", "weight_sum")) or
                    item["member_count"] < 1 or item["admitted_count"] > item["member_count"] or
                    type(item["violations"]) is not list or
                    any(type(name) is not str or name not in
                        {"cluster_over_limit", "representative_weight", "cluster_weight_sum",
                         "admitted_exact_duplicate"}
                        for name in item["violations"]) or
                    item["violations"] != sorted(set(item["violations"]))):
                raise Refusal(code)
            cluster_ids.append(item["cluster_sha256"])
        if not cluster_ids or cluster_ids != sorted(set(cluster_ids)):
            raise Refusal(code)
        over_limit = 0
        weight_sum_bad = 0
        representative_bad_clusters = 0
        duplicate_clusters = 0
        for cluster in value["clusters"]:
            admitted = cluster["admitted_count"]
            weight_sum = cluster["weight_sum"]
            violations = set(cluster["violations"])
            limit_failed = (value["rule"] == "one_representative_per_cluster" and admitted > 1 or
                            value["rule"] == "cap_per_cluster" and admitted > value["cap"])
            sum_failed = (value["rule"] == "cluster_weighting" and
                          weight_sum not in (0, 1_000_000))
            representative_failed = (value["rule"] == "one_representative_per_cluster" and
                                     weight_sum != admitted * 1_000_000)
            if weight_sum < admitted or weight_sum > admitted * 1_000_000:
                raise Refusal(code)
            if (("cluster_over_limit" in violations) != limit_failed or
                    ("cluster_weight_sum" in violations) != sum_failed or
                    ("representative_weight" in violations) != representative_failed):
                raise Refusal(code)
            over_limit += limit_failed
            weight_sum_bad += sum_failed
            representative_bad_clusters += representative_failed
            duplicate_clusters += "admitted_exact_duplicate" in violations
        reasons = value["reason_counts"]
        if (reasons["cluster_over_limit"] != over_limit or
                reasons["cluster_weight_sum"] != weight_sum_bad or
                (reasons["representative_weight"] == 0) != (representative_bad_clusters == 0) or
                reasons["representative_weight"] < representative_bad_clusters or
                reasons["representative_weight"] > value["counts"]["admitted"] or
                (reasons["admitted_exact_duplicate"] == 0) != (duplicate_clusters == 0) or
                reasons["admitted_exact_duplicate"] < duplicate_clusters or
                reasons["admitted_exact_duplicate"] > value["counts"]["admitted"] // 2):
            raise Refusal(code)
        ids = []
        for row in value["withheld"]:
            item = exact_keys(row, {"id", "cluster_sha256", "reason"}, code)
            require_hex(item["cluster_sha256"], code)
            if (type(item["id"]) is not str or not item["id"] or
                    item["cluster_sha256"] not in cluster_ids or
                    type(item["reason"]) is not str or item["reason"] not in WITHHOLDING_REASONS):
                raise Refusal(code)
            ids.append(item["id"])
        if ids != sorted(set(ids)) or len(ids) != value["counts"]["withheld"]:
            raise Refusal(code)
        withholding = Counter(row["reason"] for row in value["withheld"])
        if any(withholding[reason] != value["counts"][key] for reason, key in (
                ("exact_copy_of_admitted", "withheld_exact_copy"),
                ("distinct_text_in_admitted_cluster", "withheld_distinct_in_admitted_cluster"),
                ("cluster_not_admitted", "withheld_cluster_not_admitted"))):
            raise Refusal(code)
        withheld_by_cluster: dict[str, list[dict]] = defaultdict(list)
        for row in value["withheld"]:
            withheld_by_cluster[row["cluster_sha256"]].append(row)
        for cluster in value["clusters"]:
            rows = withheld_by_cluster[cluster["cluster_sha256"]]
            if (len(rows) != cluster["member_count"] - cluster["admitted_count"] or
                    any((row["reason"] == "cluster_not_admitted") !=
                        (cluster["admitted_count"] == 0) for row in rows)):
                raise Refusal(code)
        if (len(value["clusters"]) != value["counts"]["clusters"] or
                sum(row["member_count"] for row in value["clusters"]) != value["counts"]["records"] or
                sum(row["admitted_count"] for row in value["clusters"]) != value["counts"]["admitted"] or
                sum(row["admitted_count"] > 0 for row in value["clusters"]) !=
                value["counts"]["clusters_with_admitted"] or
                sum(row["admitted_count"] > 1 for row in value["clusters"]) !=
                value["counts"]["clusters_multi_admitted"] or
                max(row["admitted_count"] for row in value["clusters"]) !=
                value["counts"]["max_admitted_per_cluster"]):
            raise Refusal(code)
    return value


def load_multiplicity_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_SCHEMA,
                  {"schema", "tool", "tool_version", "manifest_sha256", "record_set_sha256",
                   "policy_sha256", "overlap_detail_sha256", "admission_map_sha256",
                   "detail_sha256", "purpose", "rule", "cap", "stage_status",
                   "reason_counts", "counts"}, code, 64 * 1024)
    for name in ("manifest_sha256", "record_set_sha256", "policy_sha256",
                 "overlap_detail_sha256", "detail_sha256"):
        require_hex(value[name], code)
    if value["admission_map_sha256"] is not None:
        require_hex(value["admission_map_sha256"], code)
    _validate_common(value, code)
    return value
