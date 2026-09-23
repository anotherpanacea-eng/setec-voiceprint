"""Complete, deterministic overlap enumeration over a bounded packet."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Mapping, Sequence
import unicodedata

from .common import (
    Manifest, Record, Refusal, Snapshot, WorkBudget, canonical_json, collapse_strata,
    domain_hash, exact_keys, load_strict_json, plain_hash, read_bounded,
    record_set_sha256, require_hex, validate_coordination_strata, parse_json,
    OVERLAP_DETAIL_LIMIT, RECEIPT_LIMIT,
    STAGE_STATUSES,
)


TOOL = "setec.preflight.overlap"
DETAIL_SCHEMA = "setec-preflight-overlap-detail/1"
RECEIPT_SCHEMA = "setec-preflight-overlap-receipt/1"
POLICY_SCHEMA = "setec-preflight-overlap-policy/1"
SPLIT_SCHEMA = "setec-preflight-splits/1"
STAGES = ("intake", "exact_overlap", "fuzzy_overlap", "span_overlap", "split_integrity")
REASONS = ("ok", "exact_duplicate", "fuzzy_insufficient_evidence",
           "split_cluster", "split_group", "split_not_supplied")
EDGE_TYPES = ("exact", "fuzzy", "span")
SPLITS = ("train", "develop", "validation", "test")
CEILINGS = {
    "tokens": 4_000_000, "gram memberships": 4_000_000,
    "posting visits": 20_000_000, "candidate pairs": 1_000_000,
    "membership lookups": 50_000_000, "span pair visits": 1_000_000,
    "edges": 1_000_000,
}


@dataclass(frozen=True, order=True)
class Edge:
    left_id: str
    right_id: str
    edge_type: str


@dataclass(frozen=True)
class Cluster:
    cluster_sha256: str
    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class SplitIntegrity:
    status: str
    cross_split_clusters: tuple[str, ...]
    cross_split_groups: tuple[str, ...]


def parse_overlap_policy(snapshot: Snapshot) -> tuple[dict, str]:
    """Validate policy bytes already read under ``POLICY_LIMIT`` (§5)."""
    keys = frozenset({"schema", "fuzzy", "coordination_strata"})
    policy = exact_keys(parse_json(snapshot.data, "policy_contract"), keys,
                        "policy_contract")
    if policy["schema"] != POLICY_SCHEMA:
        raise Refusal("policy_contract")
    fuzzy = exact_keys(policy["fuzzy"],
                       {"ngram", "measure", "threshold_numerator", "threshold_denominator"},
                       "policy_contract")
    n = fuzzy["ngram"]
    numerator = fuzzy["threshold_numerator"]
    denominator = fuzzy["threshold_denominator"]
    if (type(n) is not int or not 2 <= n <= 16 or
            type(fuzzy["measure"]) is not str or
            fuzzy["measure"] not in {"jaccard", "containment"} or
            type(numerator) is not int or type(denominator) is not int or
            not 1 <= numerator <= denominator <= 1_000_000):
        raise Refusal("policy_contract")
    validate_coordination_strata(policy["coordination_strata"])
    return policy, snapshot.sha256


def parse_split_map(snapshot: Snapshot,
                    records: Sequence[Record]) -> tuple[dict[str, str], str]:
    """Validate split-map bytes already read under ``SPLIT_LIMIT`` (§6.6)."""
    value = exact_keys(parse_json(snapshot.data, "split_contract"),
                       {"schema", "assignments"}, "split_contract")
    if value["schema"] != SPLIT_SCHEMA:
        raise Refusal("split_contract")
    assignments = value["assignments"]
    if (type(assignments) is not dict or set(assignments) != {r.id for r in records}
            or any(type(item) is not str or item not in SPLITS
                   for item in assignments.values())):
        raise Refusal("split_contract")
    return assignments, snapshot.sha256


def _separator(char: str) -> bool:
    value = ord(char)
    return (9 <= value <= 13 or 32 <= value <= 38 or 40 <= value <= 47
            or 58 <= value <= 64 or 91 <= value <= 96 or 123 <= value <= 126
            or value == 0xA0 or 0x2000 <= value <= 0x206F and value != 0x2019
            or value in {0x3000, 0xFEFF})


def _word_atom(char: str) -> bool:
    return char not in {"'", "\u2019"} and not _separator(char)


def _tokens(text: str, budget: WorkBudget | None) -> tuple[str, ...]:
    result: list[str] = []
    current: list[str] = []
    for index, char in enumerate(text):
        apostrophe = char in {"'", "\u2019"}
        joined = (apostrophe and index > 0 and index + 1 < len(text)
                  and _word_atom(text[index - 1]) and _word_atom(text[index + 1]))
        if _separator(char) or apostrophe and not joined:
            if current:
                if budget is not None:
                    budget.charge("tokens")
                result.append("".join(current))
                current = []
            continue
        value = ord(char)
        current.append(chr(value + 32) if 65 <= value <= 90 else char)
    if current:
        if budget is not None:
            budget.charge("tokens")
        result.append("".join(current))
    return tuple(result)


def preflight_word_ngrams_v1(text: str, n: int,
                             budget: WorkBudget | None = None) -> frozenset[tuple[str, ...]]:
    if type(n) is not int or not 1 <= n <= 64:
        raise ValueError("ngram out of range")
    tokens = _tokens(text, budget)
    grams: set[tuple[str, ...]] = set()
    for index in range(max(0, len(tokens) - n + 1)):
        gram = tokens[index:index + n]
        if gram not in grams:
            if budget is not None:
                budget.charge("gram memberships")
            grams.add(gram)
    return frozenset(grams)


def _append_edge(edges: list[Edge], left: str, right: str, kind: str,
                 budget: WorkBudget | None) -> None:
    if budget is not None:
        budget.charge("edges")
    edges.append(Edge(min(left, right), max(left, right), kind))


def _enumerate(records: Sequence[Record], policy: Mapping,
               budget: WorkBudget | None = None) -> tuple[tuple[Edge, ...], tuple[int, ...]]:
    edges: list[Edge] = []
    by_analysis: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_analysis[record.analysis_sha256].append(record)
    for group in by_analysis.values():
        for left, right in combinations(group, 2):
            _append_edge(edges, left.id, right.id, "exact", budget)

    fuzzy = policy["fuzzy"]
    grams = [preflight_word_ngrams_v1(r.analysis_text, fuzzy["ngram"], budget)
             for r in records]
    postings: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for ordinal, record_grams in enumerate(grams):
        for gram in record_grams:
            postings[gram].append(ordinal)
    candidates: set[tuple[int, int]] = set()
    for posting in postings.values():
        for left, right in combinations(posting, 2):
            if budget is not None:
                budget.charge("posting visits")
            pair = (left, right)
            if pair not in candidates:
                if budget is not None:
                    budget.charge("candidate pairs")
                candidates.add(pair)
    numerator = fuzzy["threshold_numerator"]
    denominator = fuzzy["threshold_denominator"]
    for left, right in sorted(candidates):
        smaller, larger = (grams[left], grams[right]) if len(grams[left]) <= len(grams[right]) else (grams[right], grams[left])
        shared = 0
        for gram in smaller:
            if budget is not None:
                budget.charge("membership lookups")
            shared += gram in larger
        if fuzzy["measure"] == "jaccard":
            denominator_size = len(grams[left]) + len(grams[right]) - shared
        else:
            denominator_size = min(len(grams[left]), len(grams[right]))
        if shared * denominator >= numerator * denominator_size:
            _append_edge(edges, records[left].id, records[right].id, "fuzzy", budget)

    by_source: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_source[record.source_bytes_sha256].append(record)
    for group in by_source.values():
        ordered = sorted(group, key=lambda r: (r.start_byte, r.end_byte, r.id))
        for index, left in enumerate(ordered):
            for right_index in range(index + 1, len(ordered)):
                right = ordered[right_index]
                if right.start_byte >= left.end_byte:
                    break
                if budget is not None:
                    budget.charge("span pair visits")
                if max(left.start_byte, right.start_byte) < min(left.end_byte, right.end_byte):
                    _append_edge(edges, left.id, right.id, "span", budget)
    return tuple(sorted(edges)), tuple(len(item) for item in grams)


def overlap_edges(records: Sequence[Record], policy: Mapping,
                  budget: WorkBudget | None = None) -> tuple[Edge, ...]:
    return _enumerate(records, policy, budget)[0]


def clusters(records: Sequence[Record], edges: Sequence[Edge]) -> tuple[Cluster, ...]:
    parent = {record.id: record.id for record in records}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for edge in edges:
        left, right = find(edge.left_id), find(edge.right_id)
        if left != right:
            parent[max(left, right)] = min(left, right)
    components: dict[str, list[str]] = defaultdict(list)
    content = {record.id: record.content_sha256 for record in records}
    for record in records:
        components[find(record.id)].append(record.id)
    result = []
    for member_ids in components.values():
        digest = domain_hash("setec-preflight-cluster-v1",
                             canonical_json(sorted(content[item] for item in member_ids)))
        result.append(Cluster(digest, tuple(sorted(member_ids))))
    return tuple(sorted(result, key=lambda c: c.cluster_sha256))


def split_integrity(records: Sequence[Record], found: Sequence[Cluster],
                    split_map: Mapping[str, str] | None) -> SplitIntegrity:
    if split_map is None:
        return SplitIntegrity("not_run", (), ())
    if set(split_map) != {record.id for record in records} or any(
            item not in SPLITS for item in split_map.values()):
        raise Refusal("split_contract")
    bad_clusters = tuple(c.cluster_sha256 for c in found
                         if len({split_map[item] for item in c.member_ids}) > 1)
    groups: dict[str, set[str]] = defaultdict(set)
    for record in records:
        groups[record.group_id].add(split_map[record.id])
    bad_groups = tuple(sorted(group for group, values in groups.items() if len(values) > 1))
    return SplitIntegrity("failed" if bad_clusters or bad_groups else "passed",
                          bad_clusters, bad_groups)


def build_overlap(manifest: Manifest, policy: dict, policy_sha256: str,
                  assignments: dict[str, str] | None,
                  split_map_sha256: str | None) -> tuple[bytes, bytes, dict[str, str]]:
    budget = WorkBudget(CEILINGS)
    records = manifest.records
    edges, gram_counts = _enumerate(records, policy, budget)
    found = clusters(records, edges)
    split = split_integrity(records, found, assignments)
    exact_count = sum(edge.edge_type == "exact" for edge in edges)
    empty_count = sum(count == 0 for count in gram_counts)
    statuses = {
        "intake": "passed", "exact_overlap": "failed" if exact_count else "passed",
        "fuzzy_overlap": "needs_human_review" if empty_count else "passed",
        "span_overlap": "passed", "split_integrity": split.status,
    }
    reasons = {key: 0 for key in REASONS}
    reasons.update({
        "ok": sum(value == "passed" for value in statuses.values()),
        "exact_duplicate": exact_count,
        "fuzzy_insufficient_evidence": empty_count,
        "split_cluster": len(split.cross_split_clusters),
        "split_group": len(split.cross_split_groups),
        "split_not_supplied": int(assignments is None),
    })
    by_id = {record.id: record for record in records}
    cluster_of = {member: cluster.cluster_sha256 for cluster in found
                  for member in cluster.member_ids}
    edge_counts_by_cluster: dict[str, dict[str, int]] = {
        c.cluster_sha256: {kind: 0 for kind in EDGE_TYPES} for c in found}
    for edge in edges:
        edge_counts_by_cluster[cluster_of[edge.left_id]][edge.edge_type] += 1
    input_hashes = {
        "manifest_sha256": manifest.manifest_sha256,
        "record_set_sha256": record_set_sha256(records),
        "policy_sha256": policy_sha256, "split_map_sha256": split_map_sha256,
    }
    detail = {
        "schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
        "unicode_version": unicodedata.unidata_version, "inputs": input_hashes,
        "records": [{
            "id": r.id, "group_id": r.group_id, "stratum": r.stratum,
            "candidate_bytes_sha256": r.candidate.sha256,
            "analysis_sha256": r.analysis_sha256,
            "content_sha256": r.content_sha256,
            "source_bytes_sha256": r.source_bytes_sha256,
            "start_byte": r.start_byte, "end_byte": r.end_byte,
            "self_span": r.self_span, "fuzzy_gram_count": gram_counts[index],
            "split": assignments[r.id] if assignments is not None else "unassigned",
            "cluster_sha256": cluster_of[r.id],
        } for index, r in sorted(enumerate(records), key=lambda item: item[1].id)],
        "edges": [{"left_id": e.left_id, "right_id": e.right_id,
                   "edge_type": e.edge_type} for e in edges],
        "clusters": [{
            "cluster_sha256": c.cluster_sha256, "member_ids": list(c.member_ids),
            "edge_counts": edge_counts_by_cluster[c.cluster_sha256],
            "splits": sorted({assignments[item] if assignments else "unassigned"
                              for item in c.member_ids}),
        } for c in found],
        "split_integrity": {
            "status": split.status,
            "cross_split_clusters": list(split.cross_split_clusters),
            "cross_split_groups": list(split.cross_split_groups),
        },
        "stage_status": statuses, "reason_counts": reasons,
    }
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > OVERLAP_DETAIL_LIMIT:
        raise Refusal("size_limit")
    pair_kinds = {(e.left_id, e.right_id) for e in edges}
    edge_counts = {kind: sum(e.edge_type == kind for e in edges) for kind in EDGE_TYPES}
    edge_counts.update({
        "cross_group": sum(by_id[a].group_id != by_id[b].group_id for a, b in pair_kinds),
        "cross_stratum": sum(by_id[a].stratum != by_id[b].stratum for a, b in pair_kinds),
    })
    strata: dict[str, int] = defaultdict(int)
    for record in records:
        strata[record.stratum] += 1
    split_counts = None if assignments is None else {
        **{name: sum(value == name for value in assignments.values()) for name in SPLITS},
        "cross_split_clusters": len(split.cross_split_clusters),
        "cross_split_groups": len(split.cross_split_groups),
    }
    receipt = {
        "schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
        **input_hashes, "detail_sha256": plain_hash(detail_bytes),
        "record_count": len(records), "group_count": len({r.group_id for r in records}),
        "stage_status": statuses, "reason_counts": reasons,
        "edge_counts": edge_counts,
        "cluster_counts": {
            "clusters": len(found),
            "multi_member_clusters": sum(len(c.member_ids) > 1 for c in found),
            "max_cluster_size": max(len(c.member_ids) for c in found),
            "records_in_multi_member_clusters": sum(
                len(c.member_ids) for c in found if len(c.member_ids) > 1),
        },
        "split_counts": split_counts,
        "stratum_counts": collapse_strata(strata, tuple(policy["coordination_strata"])),
        "span_evidence": "declared",
    }
    return detail_bytes, canonical_json(receipt), statuses


def _load_artifact(path: Path, expected_sha256: str, limit: int,
                   code: str, schema: str, keys: set[str]) -> dict:
    require_hex(expected_sha256, code)
    try:
        snapshot = read_bounded(path.parent, path.name, limit)
    except Refusal:
        raise Refusal(code) from None
    if snapshot.sha256 != expected_sha256:
        raise Refusal(code)
    value = exact_keys(parse_json(snapshot.data, code), keys, code)
    if (value.get("schema") != schema or value.get("tool") != TOOL or
            type(value.get("tool_version")) is not int or value["tool_version"] != 1):
        raise Refusal(code)
    if canonical_json(value) != snapshot.data:
        raise Refusal(code)
    return value


def _map(value: object, keys: set[str], code: str) -> dict:
    return exact_keys(value, keys, code)


def _nonnegative_map(value: object, keys: set[str], code: str) -> dict:
    obj = _map(value, keys, code)
    if any(type(item) is not int or item < 0 for item in obj.values()):
        raise Refusal(code)
    return obj


def _hash_or_null(value: object, code: str) -> None:
    if value is not None:
        require_hex(value, code)


def load_overlap_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load_artifact(path, expected_sha256, OVERLAP_DETAIL_LIMIT, code, DETAIL_SCHEMA,
                           {"schema", "tool", "tool_version", "unicode_version", "inputs",
                            "records", "edges", "clusters", "split_integrity",
                            "stage_status", "reason_counts"})
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    inputs = _map(value["inputs"], {"manifest_sha256", "record_set_sha256",
                                    "policy_sha256", "split_map_sha256"}, code)
    for name in ("manifest_sha256", "record_set_sha256", "policy_sha256"):
        require_hex(inputs[name], code)
    _hash_or_null(inputs["split_map_sha256"], code)
    if type(value["records"]) is not list or type(value["edges"]) is not list or type(value["clusters"]) is not list:
        raise Refusal(code)
    record_keys = {"id", "group_id", "stratum", "candidate_bytes_sha256", "analysis_sha256",
                   "content_sha256", "source_bytes_sha256", "start_byte", "end_byte",
                   "self_span", "fuzzy_gram_count", "split", "cluster_sha256"}
    record_ids: list[str] = []
    record_splits: dict[str, str] = {}
    record_clusters: dict[str, str] = {}
    record_contents: dict[str, str] = {}
    record_groups: dict[str, str] = {}
    for record in value["records"]:
        obj = _map(record, record_keys, code)
        for name in ("id", "group_id", "stratum"):
            if type(obj[name]) is not str or not obj[name]:
                raise Refusal(code)
        if type(obj["split"]) is not str or obj["split"] not in (*SPLITS, "unassigned"):
            raise Refusal(code)
        for name in ("candidate_bytes_sha256", "analysis_sha256", "content_sha256",
                     "source_bytes_sha256", "cluster_sha256"):
            require_hex(obj[name], code)
        if (type(obj["start_byte"]) is not int or obj["start_byte"] < 0 or
                type(obj["end_byte"]) is not int or obj["end_byte"] <= obj["start_byte"] or
                type(obj["fuzzy_gram_count"]) is not int or obj["fuzzy_gram_count"] < 0 or
                type(obj["self_span"]) is not bool):
            raise Refusal(code)
        expected_content = domain_hash("setec-preflight-record-content-v1", canonical_json({
            "candidate_bytes_sha256": obj["candidate_bytes_sha256"],
            "source_bytes_sha256": obj["source_bytes_sha256"],
            "start_byte": obj["start_byte"], "end_byte": obj["end_byte"],
        }))
        if obj["content_sha256"] != expected_content:
            raise Refusal(code)
        record_ids.append(obj["id"])
        record_splits[obj["id"]] = obj["split"]
        record_clusters[obj["id"]] = obj["cluster_sha256"]
        record_contents[obj["id"]] = obj["content_sha256"]
        record_groups[obj["id"]] = obj["group_id"]
    if (not record_ids or len(record_ids) > 5000 or record_ids != sorted(set(record_ids)) or
            (inputs["split_map_sha256"] is None) !=
            all(split_name == "unassigned" for split_name in record_splits.values())):
        raise Refusal(code)
    expected_record_set = domain_hash(
        "setec-preflight-record-set-v1",
        canonical_json([[record_id, record_contents[record_id]] for record_id in record_ids]))
    if inputs["record_set_sha256"] != expected_record_set:
        raise Refusal(code)
    edge_keys: list[tuple[str, str, str]] = []
    edge_counts_by_cluster: dict[str, dict[str, int]] = defaultdict(
        lambda: {kind: 0 for kind in EDGE_TYPES})
    for edge in value["edges"]:
        obj = _map(edge, {"left_id", "right_id", "edge_type"}, code)
        if (type(obj["left_id"]) is not str or type(obj["right_id"]) is not str
                or obj["edge_type"] not in EDGE_TYPES or
                obj["left_id"] >= obj["right_id"] or
                obj["left_id"] not in record_splits or obj["right_id"] not in record_splits or
                record_clusters[obj["left_id"]] != record_clusters[obj["right_id"]]):
            raise Refusal(code)
        edge_keys.append((obj["left_id"], obj["right_id"], obj["edge_type"]))
        edge_counts_by_cluster[record_clusters[obj["left_id"]]][obj["edge_type"]] += 1
    if edge_keys != sorted(set(edge_keys)):
        raise Refusal(code)
    cluster_ids: list[str] = []
    all_members: list[str] = []
    expected_cross_clusters: list[str] = []
    for cluster in value["clusters"]:
        obj = _map(cluster, {"cluster_sha256", "member_ids", "edge_counts", "splits"}, code)
        require_hex(obj["cluster_sha256"], code)
        if _nonnegative_map(obj["edge_counts"], set(EDGE_TYPES), code) != edge_counts_by_cluster[
                obj["cluster_sha256"]]:
            raise Refusal(code)
        if (type(obj["member_ids"]) is not list or type(obj["splits"]) is not list
                or any(type(item) is not str for item in obj["member_ids"] + obj["splits"])):
            raise Refusal(code)
        members = obj["member_ids"]
        if (not members or members != sorted(set(members)) or
                any(member not in record_splits or
                    record_clusters[member] != obj["cluster_sha256"] for member in members) or
                obj["splits"] != sorted({record_splits[member] for member in members}) or
                obj["cluster_sha256"] != domain_hash(
                    "setec-preflight-cluster-v1",
                    canonical_json(sorted(record_contents[member] for member in members)))):
            raise Refusal(code)
        cluster_ids.append(obj["cluster_sha256"])
        all_members.extend(members)
        if len(obj["splits"]) > 1:
            expected_cross_clusters.append(obj["cluster_sha256"])
    if (cluster_ids != sorted(set(cluster_ids)) or
            sorted(all_members) != record_ids):
        raise Refusal(code)
    integrity = _map(value["split_integrity"],
                     {"status", "cross_split_clusters", "cross_split_groups"}, code)
    if (type(integrity["status"]) is not str or integrity["status"] not in STAGE_STATUSES
            or any(type(integrity[name]) is not list or
                   any(type(item) is not str for item in integrity[name])
                   for name in ("cross_split_clusters", "cross_split_groups"))):
        raise Refusal(code)
    if (integrity["cross_split_clusters"] != sorted(set(integrity["cross_split_clusters"])) or
            integrity["cross_split_groups"] != sorted(set(integrity["cross_split_groups"])) or
            any(item not in cluster_ids for item in integrity["cross_split_clusters"])):
        raise Refusal(code)
    group_splits: dict[str, set[str]] = defaultdict(set)
    for record_id in record_ids:
        group_splits[record_groups[record_id]].add(record_splits[record_id])
    expected_cross_groups = sorted(group for group, names in group_splits.items()
                                   if len(names) > 1)
    split_supplied = inputs["split_map_sha256"] is not None
    expected_integrity = ("not_run" if not split_supplied else
                          "failed" if expected_cross_clusters or expected_cross_groups else "passed")
    if (integrity["status"] != expected_integrity or
            integrity["cross_split_clusters"] != sorted(expected_cross_clusters) or
            integrity["cross_split_groups"] != expected_cross_groups):
        raise Refusal(code)
    statuses = _map(value["stage_status"], set(STAGES), code)
    if any(type(item) is not str or item not in STAGE_STATUSES for item in statuses.values()):
        raise Refusal(code)
    reasons = _nonnegative_map(value["reason_counts"], set(REASONS), code)
    exact_count = sum(kind == "exact" for _, _, kind in edge_keys)
    empty_grams = sum(record["fuzzy_gram_count"] == 0 for record in value["records"])
    expected_statuses = {
        "intake": "passed",
        "exact_overlap": "failed" if exact_count else "passed",
        "fuzzy_overlap": "needs_human_review" if empty_grams else "passed",
        "span_overlap": "passed",
        "split_integrity": expected_integrity,
    }
    if statuses != expected_statuses:
        raise Refusal(code)
    if (statuses["split_integrity"] != expected_integrity or
            reasons["ok"] != sum(item == "passed" for item in statuses.values()) or
            reasons["exact_duplicate"] != exact_count or
            reasons["fuzzy_insufficient_evidence"] != empty_grams or
            reasons["split_cluster"] != len(expected_cross_clusters) or
            reasons["split_group"] != len(expected_cross_groups) or
            reasons["split_not_supplied"] != int(not split_supplied)):
        raise Refusal(code)
    return value


def load_overlap_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load_artifact(path, expected_sha256, RECEIPT_LIMIT, code, RECEIPT_SCHEMA,
                           {"schema", "tool", "tool_version", "manifest_sha256",
                            "record_set_sha256", "policy_sha256", "split_map_sha256",
                            "detail_sha256", "record_count", "group_count", "stage_status",
                            "reason_counts", "edge_counts", "cluster_counts", "split_counts",
                            "stratum_counts", "span_evidence"})
    for name in ("manifest_sha256", "record_set_sha256", "policy_sha256", "detail_sha256"):
        require_hex(value[name], code)
    _hash_or_null(value["split_map_sha256"], code)
    for name in ("record_count", "group_count"):
        if type(value[name]) is not int or value[name] < 1:
            raise Refusal(code)
    statuses = _map(value["stage_status"], set(STAGES), code)
    if any(type(item) is not str or item not in STAGE_STATUSES for item in statuses.values()):
        raise Refusal(code)
    _nonnegative_map(value["reason_counts"], set(REASONS), code)
    _nonnegative_map(value["edge_counts"], set(EDGE_TYPES) | {"cross_group", "cross_stratum"}, code)
    cluster_counts = _nonnegative_map(value["cluster_counts"],
                                      {"clusters", "multi_member_clusters", "max_cluster_size",
                                       "records_in_multi_member_clusters"}, code)
    if (not 1 <= value["group_count"] <= value["record_count"] or
            not 1 <= cluster_counts["clusters"] <= value["record_count"] or
            not 1 <= cluster_counts["max_cluster_size"] <= value["record_count"] or
            cluster_counts["multi_member_clusters"] > cluster_counts["clusters"] or
            cluster_counts["records_in_multi_member_clusters"] > value["record_count"]):
        raise Refusal(code)
    if value["split_counts"] is not None:
        split_counts = _nonnegative_map(value["split_counts"], set(SPLITS) |
                                         {"cross_split_clusters", "cross_split_groups"}, code)
        if sum(split_counts[name] for name in SPLITS) != value["record_count"]:
            raise Refusal(code)
    elif value["split_map_sha256"] is not None:
        raise Refusal(code)
    if value["split_counts"] is not None and value["split_map_sha256"] is None:
        raise Refusal(code)
    if value["stratum_counts"] is not None:
        if type(value["stratum_counts"]) is not dict or any(
                type(key) is not str or type(item) is not int or item < 5
                for key, item in value["stratum_counts"].items()):
            raise Refusal(code)
        if sum(value["stratum_counts"].values()) != value["record_count"]:
            raise Refusal(code)
    if value["span_evidence"] != "declared":
        raise Refusal(code)
    return value
