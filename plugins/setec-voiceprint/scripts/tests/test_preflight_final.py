"""Final packet graph projection regressions on synthetic packets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setec.preflight.common import Refusal, canonical_json, plain_hash
from setec.preflight.final import run as run_final
from setec.preflight.final_core import load_final_detail, load_final_receipt
from setec.preflight.overlap import run as run_overlap


def _packet(root: Path, texts: list[str], *, ids: list[str] | None = None,
            groups: list[str] | None = None) -> Path:
    root.mkdir(parents=True)
    rows = []
    for index, text in enumerate(texts):
        data = text.encode()
        name = f"row{index}.txt"
        (root / name).write_bytes(data)
        rows.append({"id": ids[index] if ids else f"r{index}",
                     "group_id": (groups[index] if groups else
                                  f"g{ids[index][1:]}" if ids and ids[index].startswith("r")
                                  else f"g{index}"),
                     "stratum": "synthetic", "path": name,
                     "span": {"source_path": name, "source_bytes_sha256": plain_hash(data),
                              "start_byte": 0, "end_byte": len(data)}})
    path = root / "packet.jsonl"
    path.write_bytes(b"".join(canonical_json(row) for row in rows))
    return path


def _policy(root: Path) -> Path:
    path = root / "policy.json"
    path.write_bytes(canonical_json({"schema": "setec-preflight-overlap-policy/1",
                                     "fuzzy": {"ngram": 2, "measure": "containment",
                                               "threshold_numerator": 1,
                                               "threshold_denominator": 2},
                                     "coordination_strata": ["synthetic"]}))
    return path


def _splits(root: Path, ids: list[str], *, split: str = "train") -> Path:
    path = root / "splits.json"
    path.write_bytes(canonical_json({"schema": "setec-preflight-splits/1",
                                     "assignments": {name: split for name in ids}}))
    return path


def _fixture(tmp_path: Path, intake_texts: list[str], final_texts: list[str],
             *, final_ids: list[str] | None = None,
             final_groups: list[str] | None = None):
    intake_manifest = _packet(tmp_path / "intake", intake_texts)
    policy = _policy(tmp_path)
    intake_bundle = tmp_path / "intake-bundle"
    run_overlap(intake_manifest, policy, intake_bundle,
                _splits(tmp_path / "intake", [f"r{i}" for i in range(len(intake_texts))]))
    final_manifest = _packet(tmp_path / "final-input", final_texts,
                             ids=final_ids, groups=final_groups)
    split_map = _splits(tmp_path / "final-input",
                         final_ids or [f"r{i}" for i in range(len(final_texts))])
    final_bundle = tmp_path / "final-bundle"
    receipt_bytes, statuses = run_final(intake_bundle, final_manifest, policy,
                                        split_map, final_bundle)
    return (json.loads((final_bundle / "detail.json").read_bytes()),
            json.loads(receipt_bytes), statuses, intake_bundle, final_bundle,
            final_manifest, policy, split_map)


def test_removed_only_projection_and_binding(tmp_path):
    detail, receipt, statuses, intake, final, *_ = _fixture(
        tmp_path, ["alpha beta gamma", "blue green yellow", "red violet indigo"],
        ["alpha beta gamma", "blue green yellow"])
    assert statuses["projection"] == "passed"
    assert detail["projection"]["removed_ids"] == ["r2"]
    assert receipt["projection_counts"]["removed"] == 1
    assert load_final_detail(final / "detail.json", receipt["detail_sha256"]) == detail
    assert load_final_receipt(final / "receipt.json",
                              plain_hash((final / "receipt.json").read_bytes())) == receipt
    assert receipt["intake_receipt_sha256"] == plain_hash((intake / "receipt.json").read_bytes())


@pytest.mark.parametrize("texts,ids,groups,expected", [
    (["unseen candidate"], ["new"], None, "added_id"),
    (["alpha beta gamma"], ["renamed"], None, "rekeyed_id"),
    (["alpha beta gamma"], ["r0"], ["changed-group"], "retained_tuple_changed"),
    (["changed candidate"], ["r0"], None, "retained_tuple_changed"),
])
def test_projection_findings(tmp_path, texts, ids, groups, expected):
    detail, receipt, statuses, *_ = _fixture(
        tmp_path, ["alpha beta gamma"], texts, final_ids=ids, final_groups=groups)
    assert statuses["projection"] == "failed"
    assert expected in detail["records"][0]["findings"]
    assert receipt["reason_counts"][expected] >= 1


def test_intake_policy_binding_and_detail_hash(tmp_path):
    _, _, _, intake, _, final_manifest, policy, split_map = _fixture(
        tmp_path, ["alpha beta gamma"], ["alpha beta gamma"])
    changed = tmp_path / "changed-policy.json"
    value = json.loads(policy.read_bytes())
    value["fuzzy"]["threshold_denominator"] = 3
    changed.write_bytes(canonical_json(value))
    with pytest.raises(Refusal, match="intake_binding"):
        run_final(intake, final_manifest, changed, split_map, tmp_path / "blocked")
    (intake / "detail.json").write_bytes((intake / "detail.json").read_bytes() + b" ")
    with pytest.raises(Refusal, match="detail_contract"):
        run_final(intake, final_manifest, policy, split_map, tmp_path / "blocked")


@pytest.mark.parametrize("mutation", ["wrong_hash", "added", "missing", "noncanonical"])
def test_final_detail_strict_reloader(tmp_path, mutation):
    _, receipt, _, _, final, *_ = _fixture(
        tmp_path, ["alpha beta gamma"], ["alpha beta gamma"])
    raw = (final / "detail.json").read_bytes()
    if mutation == "wrong_hash":
        candidate, expected = raw, "0" * 64
    elif mutation == "noncanonical":
        candidate = raw[:-1] + b" \n"
        expected = plain_hash(candidate)
    else:
        value = json.loads(raw)
        if mutation == "added":
            value["extra"] = 1
        else:
            del value["records"]
        candidate = canonical_json(value)
        expected = plain_hash(candidate)
    path = tmp_path / "mutated.json"
    path.write_bytes(candidate)
    with pytest.raises(Refusal, match="detail_contract"):
        load_final_detail(path, expected)


def test_bridge_removal_fragments_without_projection_failure(tmp_path):
    a = "alpha beta gamma"
    b = "alpha beta gamma delta epsilon"
    c = "delta epsilon zeta"
    detail, receipt, statuses, *_ = _fixture(
        tmp_path, [a, b, c], [a, c], final_ids=["r0", "r2"])
    assert statuses["projection"] == "passed"
    assert len(detail["projection"]["fragmented_intake_clusters"]) == 1
    assert receipt["projection_counts"]["fragmented_intake_clusters"] == 1


def test_retained_exact_duplicate_still_fails_final_overlap(tmp_path):
    detail, receipt, statuses, *_ = _fixture(
        tmp_path, ["same exact prose", "same exact prose"],
        ["same exact prose", "same exact prose"])
    assert statuses["projection"] == "passed"
    assert statuses["exact_overlap"] == "failed"
    assert receipt["reason_counts"]["exact_duplicate"] == 1


def test_split_reassignment_fails_projection(tmp_path):
    _, _, _, intake, _, manifest, policy, _ = _fixture(
        tmp_path, ["alpha beta gamma"], ["alpha beta gamma"])
    reassigned = tmp_path / "reassigned.json"
    reassigned.write_bytes(canonical_json({"schema": "setec-preflight-splits/1",
                                           "assignments": {"r0": "validation"}}))
    _, stages = run_final(intake, manifest, policy, reassigned, tmp_path / "reassigned-out")
    detail = json.loads((tmp_path / "reassigned-out" / "detail.json").read_bytes())
    assert stages["projection"] == "failed"
    assert detail["records"][0]["findings"] == ["split_reassigned"]


def test_intake_history_hash_binding_and_row_permutation(tmp_path):
    first = _fixture(tmp_path / "first", ["alpha beta gamma", "blue green yellow"],
                     ["alpha beta gamma"])
    second = _fixture(tmp_path / "second", ["alpha beta gamma", "red violet indigo"],
                      ["alpha beta gamma"])
    assert first[1]["intake_receipt_sha256"] != second[1]["intake_receipt_sha256"]
    assert first[1]["manifest_sha256"] == second[1]["manifest_sha256"]
    _, receipt, _, intake, _, manifest, policy, split_map = _fixture(
        tmp_path / "permute", ["alpha beta gamma", "blue green yellow"],
        ["alpha beta gamma", "blue green yellow"])
    manifest.write_bytes(b"".join(reversed(manifest.read_bytes().splitlines(keepends=True))))
    run_final(intake, manifest, policy, split_map, tmp_path / "permuted")
    changed = json.loads((tmp_path / "permuted" / "receipt.json").read_bytes())
    assert changed["manifest_sha256"] != receipt["manifest_sha256"]
    assert changed["record_set_sha256"] == receipt["record_set_sha256"]
    assert changed["projection_counts"] == receipt["projection_counts"]


@pytest.mark.parametrize("mutation", ["split_hash", "split_status"])
def test_intake_receipt_must_agree_with_private_detail(tmp_path, mutation):
    _, _, _, intake, _, manifest, policy, split_map = _fixture(
        tmp_path, ["alpha beta gamma"], ["alpha beta gamma"])
    receipt = json.loads((intake / "receipt.json").read_bytes())
    if mutation == "split_hash":
        receipt["split_map_sha256"] = "0" * 64
    else:
        receipt["stage_status"]["split_integrity"] = "failed"
        receipt["reason_counts"]["ok"] -= 1
    (intake / "receipt.json").write_bytes(canonical_json(receipt))
    with pytest.raises(Refusal, match="intake_binding"):
        run_final(intake, manifest, policy, split_map, tmp_path / "blocked")


@pytest.mark.parametrize("mutation", ["split_array", "stage_array",
                                        "cluster_split", "cross_cluster_edge"])
def test_final_detail_rejects_contradictory_or_malformed_values(tmp_path, mutation):
    _, _, _, _, final, *_ = _fixture(
        tmp_path, ["alpha beta gamma", "blue green yellow"],
        ["alpha beta gamma", "blue green yellow"])
    value = json.loads((final / "detail.json").read_bytes())
    if mutation == "split_array":
        value["records"][0]["split"] = []
    elif mutation == "stage_array":
        value["stage_status"]["projection"] = []
    elif mutation == "cluster_split":
        value["clusters"][0]["splits"] = ["test"]
    else:
        value["edges"].append({"left_id": "r0", "right_id": "r1", "edge_type": "exact"})
        value["edges"].sort(key=lambda row: (row["left_id"], row["right_id"],
                                             row["edge_type"]))
    data = canonical_json(value)
    path = tmp_path / "tampered-detail.json"
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_final_detail(path, plain_hash(data))


def test_final_receipt_rejects_exact_duplicate_status_forgery(tmp_path):
    _, _, _, _, final, *_ = _fixture(
        tmp_path, ["same exact prose", "same exact prose"],
        ["same exact prose", "same exact prose"])
    value = json.loads((final / "receipt.json").read_bytes())
    assert value["reason_counts"]["exact_duplicate"] == 1
    value["stage_status"]["exact_overlap"] = "passed"
    value["reason_counts"]["ok"] += 1
    data = canonical_json(value)
    path = tmp_path / "tampered-receipt.json"
    path.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_final_receipt(path, plain_hash(data))


@pytest.mark.parametrize("reason", ["exact_duplicate", "split_cluster"])
def test_final_detail_rejects_false_reason_count(tmp_path, reason):
    _, _, _, _, final, *_ = _fixture(
        tmp_path, ["same exact prose", "same exact prose"],
        ["same exact prose", "same exact prose"])
    value = json.loads((final / "detail.json").read_bytes())
    value["reason_counts"][reason] = 999
    data = canonical_json(value)
    path = tmp_path / "tampered-count-detail.json"
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_final_detail(path, plain_hash(data))
