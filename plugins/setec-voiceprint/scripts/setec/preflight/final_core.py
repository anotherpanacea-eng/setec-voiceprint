"""Final-packet induced-subgraph projection and strict output contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unicodedata

from .common import (
    Manifest, Refusal, canonical_json, collapse_strata, exact_keys, parse_json, plain_hash,
    read_bounded, record_set_sha256, require_hex,
)
from .overlap_core import (
    REASONS as OVERLAP_REASONS, STAGES as OVERLAP_STAGES,
    build_overlap, load_overlap_detail, load_overlap_receipt,
)

TOOL = "setec.preflight.final"
DETAIL_SCHEMA = "setec-preflight-final-detail/1"
RECEIPT_SCHEMA = "setec-preflight-final-receipt/1"
FINDINGS = ("added_id", "rekeyed_id", "retained_tuple_changed",
            "split_reassigned", "edge_changed", "cluster_merged")
STAGES = (*OVERLAP_STAGES, "projection")
REASONS = (*OVERLAP_REASONS, *FINDINGS)
TUPLE_FIELDS = ("group_id", "stratum", "candidate_bytes_sha256", "analysis_sha256",
                "content_sha256", "source_bytes_sha256", "start_byte", "end_byte",
                "self_span")
DETAIL_LIMIT = 256 * 1024 * 1024
RECEIPT_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class FinalResult:
    detail: dict
    receipt: dict


def load_intake_bundle(bundle: Path, policy: dict,
                       policy_sha256: str) -> tuple[dict, dict, str, str]:
    receipt_path = bundle / "receipt.json"
    try:
        receipt_raw = read_bounded(bundle, "receipt.json", RECEIPT_LIMIT)
    except Refusal:
        raise Refusal("receipt_contract") from None
    receipt = load_overlap_receipt(receipt_path, receipt_raw.sha256)
    detail_path = bundle / "detail.json"
    detail = load_overlap_detail(detail_path, receipt["detail_sha256"])
    inputs = detail["inputs"]
    records = detail["records"]
    edges = detail["edges"]
    clusters = detail["clusters"]
    by_id = {row["id"]: row for row in records}
    pairs = {(edge["left_id"], edge["right_id"]) for edge in edges}
    expected_edges = {kind: sum(edge["edge_type"] == kind for edge in edges)
                      for kind in ("exact", "fuzzy", "span")}
    expected_edges.update({
        "cross_group": sum(by_id[left]["group_id"] != by_id[right]["group_id"]
                           for left, right in pairs),
        "cross_stratum": sum(by_id[left]["stratum"] != by_id[right]["stratum"]
                             for left, right in pairs),
    })
    expected_clusters = {
        "clusters": len(clusters),
        "multi_member_clusters": sum(len(item["member_ids"]) > 1 for item in clusters),
        "max_cluster_size": max(len(item["member_ids"]) for item in clusters),
        "records_in_multi_member_clusters": sum(len(item["member_ids"])
                                               for item in clusters
                                               if len(item["member_ids"]) > 1),
    }
    expected_splits = {name: sum(row["split"] == name for row in records)
                       for name in ("train", "develop", "validation", "test")}
    expected_splits.update({
        "cross_split_clusters": len(detail["split_integrity"]["cross_split_clusters"]),
        "cross_split_groups": len(detail["split_integrity"]["cross_split_groups"]),
    })
    strata = {}
    for row in records:
        strata[row["stratum"]] = strata.get(row["stratum"], 0) + 1
    if (inputs["manifest_sha256"] != receipt["manifest_sha256"] or
            inputs["policy_sha256"] != receipt["policy_sha256"] or
            inputs["record_set_sha256"] != receipt["record_set_sha256"] or
            inputs["split_map_sha256"] != receipt["split_map_sha256"] or
            detail["stage_status"] != receipt["stage_status"] or
            detail["reason_counts"] != receipt["reason_counts"] or
            len(detail["records"]) != receipt["record_count"] or
            len({row["group_id"] for row in detail["records"]}) != receipt["group_count"] or
            expected_edges != receipt["edge_counts"] or
            expected_clusters != receipt["cluster_counts"] or
            expected_splits != receipt["split_counts"] or
            collapse_strata(strata, tuple(policy["coordination_strata"])) !=
            receipt["stratum_counts"] or
            policy_sha256 != receipt["policy_sha256"] or
            detail["unicode_version"] != unicodedata.unidata_version or
            detail["split_integrity"]["status"] != "passed"):
        raise Refusal("intake_binding")
    return detail, receipt, receipt_raw.sha256, receipt["detail_sha256"]


def project_final(intake_detail: dict, manifest: Manifest, policy: dict,
                  assignments: dict[str, str], split_map_sha256: str,
                  intake_receipt_sha256: str, intake_detail_sha256: str) -> FinalResult:
    overlap_bytes, _, _ = build_overlap(
        manifest, policy, intake_detail["inputs"]["policy_sha256"],
        assignments, split_map_sha256)
    final_overlap = parse_json(overlap_bytes, "internal_refusal")
    intake_records = {row["id"]: row for row in intake_detail["records"]}
    final_records = {row["id"]: row for row in final_overlap["records"]}
    intake_contents = {row["content_sha256"] for row in intake_records.values()}
    retained = set(final_records) & set(intake_records)
    removed = sorted(set(intake_records) - set(final_records))
    findings: dict[str, set[str]] = {record_id: set() for record_id in final_records}
    for record_id, row in final_records.items():
        old = intake_records.get(record_id)
        if old is None:
            findings[record_id].add("rekeyed_id" if row["content_sha256"] in intake_contents
                                    else "added_id")
            continue
        if any(row[name] != old[name] for name in TUPLE_FIELDS):
            findings[record_id].add("retained_tuple_changed")
        if row["split"] != old["split"]:
            findings[record_id].add("split_reassigned")

    def edge_key(edge: dict) -> tuple[str, str, str]:
        return edge["left_id"], edge["right_id"], edge["edge_type"]

    intake_edges = {edge_key(edge) for edge in intake_detail["edges"]
                    if edge["left_id"] in retained and edge["right_id"] in retained}
    final_edges = {edge_key(edge) for edge in final_overlap["edges"]
                   if edge["left_id"] in retained and edge["right_id"] in retained}
    edge_changes = []
    for side, changes in (("intake_only", intake_edges - final_edges),
                          ("final_only", final_edges - intake_edges)):
        for left, right, kind in changes:
            edge_changes.append({"left_id": left, "right_id": right,
                                 "edge_type": kind, "side": side})
            findings[left].add("edge_changed")
            findings[right].add("edge_changed")
    edge_changes.sort(key=lambda row: (row["left_id"], row["right_id"],
                                       row["edge_type"], row["side"]))
    intake_cluster_of = {record_id: row["cluster_sha256"]
                         for record_id, row in intake_records.items()}
    final_cluster_of = {record_id: row["cluster_sha256"]
                        for record_id, row in final_records.items()}
    clusters = []
    merged = []
    for cluster in final_overlap["clusters"]:
        member_ids = cluster["member_ids"]
        origins = sorted({intake_cluster_of[item] for item in member_ids
                          if item in intake_cluster_of})
        if len(origins) > 1:
            merged.append(cluster["cluster_sha256"])
            for member in member_ids:
                findings[member].add("cluster_merged")
        clusters.append({"cluster_sha256": cluster["cluster_sha256"],
                         "member_ids": member_ids,
                         "intake_cluster_sha256s": origins,
                         "splits": cluster["splits"]})
    fragmented = sorted(cluster["cluster_sha256"] for cluster in intake_detail["clusters"]
                        if len({final_cluster_of[item] for item in cluster["member_ids"]
                                if item in retained}) > 1)
    projection_status = "failed" if edge_changes or any(findings.values()) else "passed"
    stages = {**final_overlap["stage_status"], "projection": projection_status}
    reasons = {**final_overlap["reason_counts"],
               "added_id": sum("added_id" in value for value in findings.values()),
               "rekeyed_id": sum("rekeyed_id" in value for value in findings.values()),
               "retained_tuple_changed": sum("retained_tuple_changed" in value
                                             for value in findings.values()),
               "split_reassigned": sum("split_reassigned" in value for value in findings.values()),
               "edge_changed": len(edge_changes),
               "cluster_merged": len(merged)}
    reasons["ok"] = sum(status == "passed" for status in stages.values())
    inputs = {"manifest_sha256": manifest.manifest_sha256,
              "policy_sha256": intake_detail["inputs"]["policy_sha256"],
              "split_map_sha256": split_map_sha256,
              "intake_manifest_sha256": intake_detail["inputs"]["manifest_sha256"],
              "intake_receipt_sha256": intake_receipt_sha256,
              "intake_detail_sha256": intake_detail_sha256}
    detail = {"schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
              "unicode_version": unicodedata.unidata_version, "inputs": inputs,
              "records": [{"id": record_id, "split": row["split"],
                           "cluster_sha256": row["cluster_sha256"],
                           "intake_cluster_sha256": intake_cluster_of.get(record_id),
                           "findings": sorted(findings[record_id])}
                          for record_id, row in sorted(final_records.items())],
              "edges": final_overlap["edges"], "clusters": clusters,
              "projection": {"status": projection_status, "removed_ids": removed,
                             "edge_changes": edge_changes,
                             "fragmented_intake_clusters": fragmented,
                             "merged_final_clusters": merged},
              "stage_status": stages, "reason_counts": reasons}
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > DETAIL_LIMIT:
        raise Refusal("size_limit")
    counts = {"intake_records": len(intake_records), "retained": len(retained),
              "removed": len(removed),
              **{name: reasons[name] for name in FINDINGS},
              "final_clusters": len(clusters),
              "fragmented_intake_clusters": len(fragmented)}
    receipt = {"schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
               **inputs, "detail_sha256": plain_hash(detail_bytes),
               "record_set_sha256": record_set_sha256(manifest.records),
               "record_count": len(final_records), "stage_status": stages,
               "reason_counts": reasons, "projection_counts": counts,
               "span_evidence": "declared"}
    if len(canonical_json(receipt)) > RECEIPT_LIMIT:
        raise Refusal("size_limit")
    return FinalResult(detail, receipt)


def _load(path: Path, expected_sha256: str, limit: int, keys: set[str],
          schema: str, code: str) -> dict:
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


def _statuses(value: object, code: str) -> dict:
    stages = exact_keys(value, set(STAGES), code)
    if (any(type(item) is not str for item in stages.values()) or
            stages["intake"] != "passed" or
            stages["projection"] not in {"passed", "failed"} or
            any(item not in
                {"passed", "failed", "needs_human_review", "not_run"}
                for item in stages.values())):
        raise Refusal(code)
    return stages


def _reasons(value: object, code: str) -> dict:
    reasons = exact_keys(value, set(REASONS), code)
    if any(type(item) is not int or item < 0 for item in reasons.values()):
        raise Refusal(code)
    return reasons


def load_final_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load(path, expected_sha256, DETAIL_LIMIT,
                  {"schema", "tool", "tool_version", "unicode_version", "inputs",
                   "records", "edges", "clusters", "projection", "stage_status",
                   "reason_counts"}, DETAIL_SCHEMA, code)
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    inputs = exact_keys(value["inputs"], {"manifest_sha256", "policy_sha256",
                                           "split_map_sha256", "intake_manifest_sha256",
                                           "intake_receipt_sha256", "intake_detail_sha256"}, code)
    for item in inputs.values():
        require_hex(item, code)
    if (type(value["records"]) is not list or not value["records"] or
            type(value["edges"]) is not list or type(value["clusters"]) is not list):
        raise Refusal(code)
    ids = []
    findings = {}
    clusters_of = {}
    splits_of = {}
    intake_clusters_of = {}
    for item in value["records"]:
        row = exact_keys(item, {"id", "split", "cluster_sha256",
                                "intake_cluster_sha256", "findings"}, code)
        if (type(row["id"]) is not str or not row["id"] or
                type(row["split"]) is not str or
                row["split"] not in {"train", "develop", "validation", "test"} or
                type(row["findings"]) is not list or
                any(type(name) is not str or name not in FINDINGS for name in row["findings"]) or
                row["findings"] != sorted(set(row["findings"]))):
            raise Refusal(code)
        require_hex(row["cluster_sha256"], code)
        if row["intake_cluster_sha256"] is not None:
            require_hex(row["intake_cluster_sha256"], code)
        if (row["intake_cluster_sha256"] is None) != bool(
                {"added_id", "rekeyed_id"} & set(row["findings"])):
            raise Refusal(code)
        ids.append(row["id"])
        findings[row["id"]] = row["findings"]
        clusters_of[row["id"]] = row["cluster_sha256"]
        splits_of[row["id"]] = row["split"]
        intake_clusters_of[row["id"]] = row["intake_cluster_sha256"]
    if ids != sorted(set(ids)):
        raise Refusal(code)
    edge_keys = []
    for item in value["edges"]:
        row = exact_keys(item, {"left_id", "right_id", "edge_type"}, code)
        if (type(row["left_id"]) is not str or type(row["right_id"]) is not str or
                type(row["edge_type"]) is not str or
                row["left_id"] not in findings or row["right_id"] not in findings or
                row["left_id"] >= row["right_id"] or
                clusters_of[row["left_id"]] != clusters_of[row["right_id"]] or
                row["edge_type"] not in {"exact", "fuzzy", "span"}):
            raise Refusal(code)
        edge_keys.append((row["left_id"], row["right_id"], row["edge_type"]))
    if edge_keys != sorted(set(edge_keys)):
        raise Refusal(code)
    cluster_ids = []
    members = []
    expected_merged = []
    cross_split_clusters = 0
    for item in value["clusters"]:
        row = exact_keys(item, {"cluster_sha256", "member_ids",
                                "intake_cluster_sha256s", "splits"}, code)
        require_hex(row["cluster_sha256"], code)
        if (type(row["member_ids"]) is not list or not row["member_ids"] or
                any(type(member) is not str for member in row["member_ids"]) or
                row["member_ids"] != sorted(set(row["member_ids"])) or
                any(member not in findings or clusters_of[member] != row["cluster_sha256"]
                    for member in row["member_ids"]) or
                type(row["intake_cluster_sha256s"]) is not list or
                any(type(item_hash) is not str for item_hash in row["intake_cluster_sha256s"]) or
                row["intake_cluster_sha256s"] != sorted(set(row["intake_cluster_sha256s"])) or
                type(row["splits"]) is not list or
                any(type(split) is not str for split in row["splits"]) or
                row["splits"] != sorted(set(row["splits"]))):
            raise Refusal(code)
        origins = sorted({intake_clusters_of[member] for member in row["member_ids"]
                          if intake_clusters_of[member] is not None})
        if (row["intake_cluster_sha256s"] != origins or
                row["splits"] != sorted({splits_of[member] for member in row["member_ids"]})):
            raise Refusal(code)
        if len(origins) > 1:
            expected_merged.append(row["cluster_sha256"])
        cross_split_clusters += len(row["splits"]) > 1
        if any(("cluster_merged" in findings[member]) != (len(origins) > 1)
               for member in row["member_ids"]):
            raise Refusal(code)
        for item_hash in row["intake_cluster_sha256s"]:
            require_hex(item_hash, code)
        cluster_ids.append(row["cluster_sha256"])
        members.extend(row["member_ids"])
    if cluster_ids != sorted(set(cluster_ids)) or sorted(members) != ids:
        raise Refusal(code)
    projection = exact_keys(value["projection"], {"status", "removed_ids",
                                                  "edge_changes", "fragmented_intake_clusters",
                                                  "merged_final_clusters"}, code)
    if type(projection["status"]) is not str or projection["status"] not in {"passed", "failed"}:
        raise Refusal(code)
    for name in ("removed_ids", "fragmented_intake_clusters", "merged_final_clusters"):
        if (type(projection[name]) is not list or
                any(type(item) is not str for item in projection[name]) or
                projection[name] != sorted(set(projection[name]))):
            raise Refusal(code)
    if type(projection["edge_changes"]) is not list:
        raise Refusal(code)
    edge_change_keys = []
    for item in projection["edge_changes"]:
        row = exact_keys(item, {"left_id", "right_id", "edge_type", "side"}, code)
        if (any(type(row[name]) is not str for name in
                ("left_id", "right_id", "edge_type", "side")) or
                row["left_id"] not in findings or row["right_id"] not in findings or
                row["left_id"] >= row["right_id"] or row["edge_type"] not in
                {"exact", "fuzzy", "span"} or row["side"] not in
                {"intake_only", "final_only"}):
            raise Refusal(code)
        edge_change_keys.append((row["left_id"], row["right_id"], row["edge_type"], row["side"]))
    if edge_change_keys != sorted(set(edge_change_keys)):
        raise Refusal(code)
    current_edges = set(edge_keys)
    if (any(((left, right, kind) in current_edges) != (side == "final_only")
            for left, right, kind, side in edge_change_keys) or
            any("edge_changed" not in findings[item]
                for left, right, _, _ in edge_change_keys for item in (left, right))):
        raise Refusal(code)
    expected_fragmented = sorted(intake_hash for intake_hash in
                                 {item for item in intake_clusters_of.values() if item is not None}
                                 if len({clusters_of[record_id] for record_id in ids
                                         if intake_clusters_of[record_id] == intake_hash}) > 1)
    if (projection["merged_final_clusters"] != expected_merged or
            projection["fragmented_intake_clusters"] != expected_fragmented or
            any(item in findings for item in projection["removed_ids"])):
        raise Refusal(code)
    stages = _statuses(value["stage_status"], code)
    reasons = _reasons(value["reason_counts"], code)
    expected = {name: sum(name in names for names in findings.values())
                for name in FINDINGS}
    expected["edge_changed"] = len(edge_change_keys)
    expected["cluster_merged"] = len(projection["merged_final_clusters"])
    if (stages["projection"] != projection["status"] or
            projection["status"] != ("failed" if edge_change_keys or any(findings.values())
                                     else "passed") or
            reasons["ok"] != sum(item == "passed" for item in stages.values()) or
            reasons["exact_duplicate"] != sum(kind == "exact" for _, _, kind in edge_keys) or
            reasons["split_cluster"] != cross_split_clusters or
            stages["exact_overlap"] != ("failed" if any(kind == "exact" for _, _, kind in edge_keys)
                                        else "passed") or
            stages["fuzzy_overlap"] != ("needs_human_review" if
                                           reasons["fuzzy_insufficient_evidence"] else "passed") or
            stages["span_overlap"] != "passed" or
            stages["split_integrity"] != ("failed" if
                                           reasons["split_cluster"] or reasons["split_group"]
                                           else "passed") or
            reasons["split_not_supplied"] != 0 or
            any(reasons[name] != expected[name] for name in FINDINGS)):
        raise Refusal(code)
    return value


def load_final_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_LIMIT,
                  {"schema", "tool", "tool_version", "manifest_sha256", "policy_sha256",
                   "split_map_sha256", "intake_manifest_sha256", "intake_receipt_sha256",
                   "intake_detail_sha256", "detail_sha256", "record_set_sha256",
                   "record_count", "stage_status", "reason_counts",
                   "projection_counts", "span_evidence"}, RECEIPT_SCHEMA, code)
    for name in ("manifest_sha256", "policy_sha256", "split_map_sha256",
                 "intake_manifest_sha256", "intake_receipt_sha256",
                 "intake_detail_sha256", "detail_sha256", "record_set_sha256"):
        require_hex(value[name], code)
    if (type(value["record_count"]) is not int or not 1 <= value["record_count"] <= 5000 or
            value["span_evidence"] != "declared"):
        raise Refusal(code)
    stages = _statuses(value["stage_status"], code)
    reasons = _reasons(value["reason_counts"], code)
    counts = exact_keys(value["projection_counts"], {"intake_records", "retained",
                                                   "removed", *FINDINGS, "final_clusters",
                                                   "fragmented_intake_clusters"}, code)
    if any(type(item) is not int or item < 0 for item in counts.values()):
        raise Refusal(code)
    if (counts["intake_records"] < 1 or counts["retained"] + counts["removed"] !=
            counts["intake_records"] or
            counts["retained"] + counts["added_id"] + counts["rekeyed_id"] !=
            value["record_count"] or
            any(counts[name] != reasons[name] for name in FINDINGS) or
            reasons["ok"] != sum(item == "passed" for item in stages.values()) or
            stages["exact_overlap"] != ("failed" if reasons["exact_duplicate"] else "passed") or
            stages["fuzzy_overlap"] != ("needs_human_review" if
                                           reasons["fuzzy_insufficient_evidence"] else "passed") or
            stages["span_overlap"] != "passed" or
            stages["split_integrity"] != ("failed" if
                                           reasons["split_cluster"] or reasons["split_group"]
                                           else "passed") or
            reasons["split_not_supplied"] != 0 or
            stages["projection"] != ("failed" if any(counts[name] for name in FINDINGS)
                                      else "passed")):
        raise Refusal(code)
    return value
