"""Observable multiplicity and withholding regressions on real overlap output."""

from __future__ import annotations

import json
from pathlib import Path
import random

import pytest

from setec.preflight.common import Refusal, canonical_json, plain_hash
from setec.preflight.overlap import run as run_overlap
from setec.preflight.multiplicity import run as run_multiplicity
from setec.preflight.multiplicity_core import (
    load_multiplicity_detail, load_multiplicity_receipt,
)


def _fixture(tmp_path: Path, texts: list[str]):
    root = tmp_path / "inputs"
    root.mkdir(parents=True)
    rows = []
    for index, text in enumerate(texts):
        data = text.encode()
        name = f"candidate-{index}.txt"
        (root / name).write_bytes(data)
        rows.append({"id": f"r{index}", "group_id": f"g{index}", "stratum": "synthetic",
                     "path": name,
                     "span": {"source_path": name, "source_bytes_sha256": plain_hash(data),
                              "start_byte": 0, "end_byte": len(data)}})
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    overlap_policy = root / "overlap-policy.json"
    overlap_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-overlap-policy/1",
        "fuzzy": {"ngram": 2, "measure": "containment",
                  "threshold_numerator": 1, "threshold_denominator": 2},
        "coordination_strata": ["synthetic"],
    }))
    overlap_bundle = tmp_path / "overlap-bundle"
    overlap_receipt, _ = run_overlap(manifest, overlap_policy, overlap_bundle)
    detail_hash = json.loads(overlap_receipt)["detail_sha256"]
    return root, manifest, overlap_bundle / "detail.json", detail_hash


def _run(tmp_path: Path, texts: list[str], weights: list[int] | None,
         *, rule: str = "one_representative_per_cluster", cap: int | None = None,
         purpose: str = "conditioning_target"):
    root, manifest, detail, detail_hash = _fixture(tmp_path, texts)
    policy_path = root / "multiplicity-policy.json"
    policy_path.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                            "purpose": purpose, "rule": rule, "cap": cap}))
    admission_path = None
    if weights is not None:
        admission_path = root / "admission.json"
        admission_path.write_bytes(canonical_json({
            "schema": "setec-preflight-admission/1",
            "overlap_detail_sha256": detail_hash,
            "assignments": {f"r{index}": {"admitted": weight > 0,
                                         "micro_weight": weight}
                            for index, weight in enumerate(weights)},
        }))
    out = tmp_path / "multiplicity-bundle"
    receipt_bytes, statuses = run_multiplicity(manifest, policy_path, detail,
                                               detail_hash, out, admission_path)
    return json.loads((out / "detail.json").read_bytes()), json.loads(receipt_bytes), statuses, out


def test_one_representative_and_exact_copy_withheld(tmp_path):
    detail, receipt, statuses, out = _run(
        tmp_path, ["alpha beta gamma", "alpha beta gamma", "independent text"],
        [1_000_000, 0, 1_000_000])
    assert statuses == {"multiplicity": "passed"}
    assert receipt["counts"]["admitted"] == 2
    assert receipt["counts"]["withheld_exact_copy"] == 1
    assert receipt["reason_counts"]["distinct_text_withheld"] == 0
    assert detail["withheld"][0]["reason"] == "exact_copy_of_admitted"
    assert load_multiplicity_detail(out / "detail.json", receipt["detail_sha256"]) == detail
    assert load_multiplicity_receipt(out / "receipt.json",
                                     plain_hash((out / "receipt.json").read_bytes())) == receipt


def test_two_exact_copies_admitted_fail_all_training_rules(tmp_path):
    for rule, weights, cap in (
        ("one_representative_per_cluster", [1_000_000, 1_000_000], None),
        ("cap_per_cluster", [1, 1], 2),
        ("cluster_weighting", [500_000, 500_000], None),
    ):
        detail, receipt, statuses, _ = _run(tmp_path / rule, ["same text", "same text"],
                                            weights, rule=rule, cap=cap)
        assert statuses["multiplicity"] == "failed"
        assert receipt["reason_counts"]["admitted_exact_duplicate"] == 1
        assert "admitted_exact_duplicate" in detail["clusters"][0]["violations"]


def test_all_zero_training_map_never_passes(tmp_path):
    _, receipt, statuses, _ = _run(tmp_path, ["alpha beta", "independent text"], [0, 0])
    assert statuses["multiplicity"] == "needs_human_review"
    assert receipt["reason_counts"]["no_admitted_records"] == 1
    assert receipt["counts"]["admitted"] == 0


def test_no_training_consumption_not_run_and_map_refused(tmp_path):
    _, receipt, statuses, _ = _run(tmp_path / "none", ["one text"], None,
                                   rule="no_training_consumption", purpose="evaluation_fixture")
    assert statuses["multiplicity"] == "not_run"
    assert receipt["counts"] is None
    with pytest.raises(Refusal, match="admission_contract"):
        _run(tmp_path / "map", ["one text"], [1], rule="no_training_consumption",
             purpose="evaluation_fixture")


def test_distinct_text_withheld_requires_review(tmp_path):
    detail, receipt, statuses, _ = _run(
        tmp_path, ["alpha beta gamma", "alpha beta gamma delta"], [1_000_000, 0])
    assert statuses["multiplicity"] == "needs_human_review"
    assert receipt["counts"]["withheld_distinct_in_admitted_cluster"] == 1
    assert detail["withheld"][0]["reason"] == "distinct_text_in_admitted_cluster"


def test_candidate_edit_after_overlap_refuses_detail_binding(tmp_path):
    root, manifest, detail, detail_hash = _fixture(tmp_path, ["original words"])
    (root / "source.txt").write_bytes(b"original words")
    row = json.loads(manifest.read_bytes())
    row["span"]["source_path"] = "source.txt"
    manifest.write_bytes(canonical_json(row))
    receipt, _ = run_overlap(manifest, root / "overlap-policy.json",
                             tmp_path / "overlap-bundle2")
    detail = tmp_path / "overlap-bundle2" / "detail.json"
    detail_hash = json.loads(receipt)["detail_sha256"]
    (root / "candidate-0.txt").write_bytes(b"revised text!")
    policy = root / "multiplicity-policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                       "purpose": "conditioning_target",
                                       "rule": "cap_per_cluster", "cap": 1}))
    with pytest.raises(Refusal, match="detail_contract"):
        run_multiplicity(manifest, policy, detail, detail_hash, tmp_path / "out")


def test_admission_contradiction_refuses_before_rule(tmp_path):
    root, manifest, detail, detail_hash = _fixture(tmp_path, ["same text"])
    policy = root / "multiplicity-policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                       "purpose": "conditioning_target",
                                       "rule": "one_representative_per_cluster", "cap": None}))
    admission = root / "admission.json"
    admission.write_bytes(canonical_json({
        "schema": "setec-preflight-admission/1", "overlap_detail_sha256": detail_hash,
        "assignments": {"r0": {"admitted": False, "micro_weight": 1}},
    }))
    with pytest.raises(Refusal, match="admission_contract"):
        run_multiplicity(manifest, policy, detail, detail_hash,
                         tmp_path / "out", admission)


def test_reloader_refuses_forged_all_zero_pass(tmp_path):
    detail, receipt, _, _ = _run(tmp_path, ["first prose", "second prose"], [0, 0])
    detail["stage_status"]["multiplicity"] = "passed"
    detail["reason_counts"]["ok"] = 1
    path = tmp_path / "forged-detail.json"
    data = canonical_json(detail)
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_multiplicity_detail(path, plain_hash(data))
    receipt["stage_status"]["multiplicity"] = "passed"
    receipt["reason_counts"]["ok"] = 1
    path = tmp_path / "forged-receipt.json"
    data = canonical_json(receipt)
    path.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_multiplicity_receipt(path, plain_hash(data))


@pytest.mark.parametrize("weight,admitted", [(True, True), (1.0, True),
                                              ("1", True), (-1, False),
                                              (1_000_001, True), (0, True)])
def test_malformed_admission_weight_refuses(tmp_path, weight, admitted):
    root, manifest, detail, detail_hash = _fixture(tmp_path, ["one text"])
    policy = root / "multiplicity-policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                       "purpose": "conditioning_target",
                                       "rule": "cap_per_cluster", "cap": 1}))
    admission = root / "admission.json"
    admission.write_bytes(canonical_json({
        "schema": "setec-preflight-admission/1", "overlap_detail_sha256": detail_hash,
        "assignments": {"r0": {"admitted": admitted, "micro_weight": weight}},
    }))
    with pytest.raises(Refusal, match="admission_contract"):
        run_multiplicity(manifest, policy, detail, detail_hash,
                         tmp_path / "out", admission)


def test_seeded_reconciliation_against_independent_cluster_partition(tmp_path):
    rng = random.Random(90217)
    vocabulary = ["alpha beta gamma", "alpha beta gamma delta", "unique violet star",
                  "second unrelated prose"]
    for trial in range(12):
        texts = [rng.choice(vocabulary) for _ in range(5)]
        weights = [rng.choice((0, 1, 250_000, 1_000_000)) for _ in texts]
        root, manifest, overlap_path, detail_hash = _fixture(tmp_path / str(trial), texts)
        overlap = json.loads(overlap_path.read_bytes())
        policy = root / "multiplicity-policy.json"
        policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                           "purpose": "conditioning_target",
                                           "rule": "cap_per_cluster", "cap": 5}))
        admission = root / "admission.json"
        admission.write_bytes(canonical_json({
            "schema": "setec-preflight-admission/1", "overlap_detail_sha256": detail_hash,
            "assignments": {f"r{i}": {"admitted": weight > 0, "micro_weight": weight}
                            for i, weight in enumerate(weights)},
        }))
        receipt_bytes, _ = run_multiplicity(manifest, policy, overlap_path, detail_hash,
                                            tmp_path / str(trial) / "out", admission)
        receipt = json.loads(receipt_bytes)
        analysis = {row["id"]: row["analysis_sha256"] for row in overlap["records"]}
        expected = {"exact_copy_of_admitted": 0,
                    "distinct_text_in_admitted_cluster": 0,
                    "cluster_not_admitted": 0}
        for cluster in overlap["clusters"]:
            members = cluster["member_ids"]
            admitted_ids = {member for member in members if weights[int(member[1:])] > 0}
            admitted_analysis = {analysis[member] for member in admitted_ids}
            for member in members:
                if member in admitted_ids:
                    continue
                reason = ("exact_copy_of_admitted" if analysis[member] in admitted_analysis else
                          "distinct_text_in_admitted_cluster" if admitted_ids else
                          "cluster_not_admitted")
                expected[reason] += 1
        counts = receipt["counts"]
        assert counts["admitted"] == sum(weight > 0 for weight in weights)
        assert counts["withheld"] == sum(weight == 0 for weight in weights)
        assert counts["withheld_exact_copy"] == expected["exact_copy_of_admitted"]
        assert counts["withheld_distinct_in_admitted_cluster"] == expected[
            "distinct_text_in_admitted_cluster"]
        assert counts["withheld_cluster_not_admitted"] == expected["cluster_not_admitted"]
        assert counts["admitted"] + counts["withheld"] == counts["records"]


def test_row_and_id_permutation_preserves_aggregate_outcome(tmp_path):
    texts = ["alpha beta gamma", "alpha beta gamma", "different prose"]
    weights = [1_000_000, 0, 0]
    _, first, _, _ = _run(tmp_path / "a", texts, weights)
    _, second, _, _ = _run(tmp_path / "b", list(reversed(texts)), list(reversed(weights)))
    assert first["counts"] == second["counts"]
    assert first["reason_counts"] == second["reason_counts"]


def test_coordination_receipt_excludes_private_canaries(tmp_path):
    root, manifest, _, _ = _fixture(tmp_path, ["PRIVATE-PROSE-CANARY"])
    row = json.loads(manifest.read_bytes())
    row["id"] = "PRIVATE-ID-CANARY"
    row["group_id"] = "PRIVATE-GROUP-CANARY"
    row["stratum"] = "PRIVATE-STRATUM-CANARY"
    (root / "PRIVATE-PATH-CANARY.txt").write_bytes((root / row["path"]).read_bytes())
    row["path"] = "PRIVATE-PATH-CANARY.txt"
    row["span"]["source_path"] = row["path"]
    manifest.write_bytes(canonical_json(row))
    overlap_receipt, _ = run_overlap(manifest, root / "overlap-policy.json",
                                     tmp_path / "overlap-private")
    overlap_path = tmp_path / "overlap-private" / "detail.json"
    detail_hash = json.loads(overlap_receipt)["detail_sha256"]
    policy = root / "multiplicity-policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                       "purpose": "conditioning_target",
                                       "rule": "cap_per_cluster", "cap": 1}))
    admission = root / "admission.json"
    admission.write_bytes(canonical_json({
        "schema": "setec-preflight-admission/1", "overlap_detail_sha256": detail_hash,
        "assignments": {row["id"]: {"admitted": True, "micro_weight": 1}},
    }))
    receipt, _ = run_multiplicity(manifest, policy, overlap_path, detail_hash,
                                  tmp_path / "private-out", admission)
    for canary in (b"PRIVATE-PROSE-CANARY", b"PRIVATE-ID-CANARY",
                   b"PRIVATE-GROUP-CANARY", b"PRIVATE-STRATUM-CANARY",
                   b"PRIVATE-PATH-CANARY"):
        assert canary not in receipt


def test_control_ceiling_and_strict_reloader_fail_closed(tmp_path):
    root, manifest, overlap_path, detail_hash = _fixture(tmp_path, ["one text"])
    policy = root / "multiplicity-policy.json"
    base = canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                           "purpose": "conditioning_target", "rule": "cap_per_cluster",
                           "cap": 1})
    policy.write_bytes(base + b" " * (64 * 1024 - len(base)))
    receipt, _ = run_multiplicity(manifest, policy, overlap_path, detail_hash,
                                  tmp_path / "at-limit")
    assert json.loads(receipt)["stage_status"]["multiplicity"] == "not_run"
    policy.write_bytes(policy.read_bytes() + b" ")
    with pytest.raises(Refusal, match="size_limit"):
        run_multiplicity(manifest, policy, overlap_path, detail_hash,
                         tmp_path / "over-limit")
    detail_file = tmp_path / "at-limit" / "detail.json"
    receipt_file = tmp_path / "at-limit" / "receipt.json"
    with pytest.raises(Refusal, match="detail_contract"):
        load_multiplicity_detail(detail_file, "0" * 64)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_multiplicity_receipt(receipt_file, "0" * 64)
    detail = json.loads(detail_file.read_bytes())
    bad = tmp_path / "noncanonical-detail.json"
    data = json.dumps(detail).encode()
    bad.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_multiplicity_detail(bad, plain_hash(data))
    receipt_obj = json.loads(receipt_file.read_bytes())
    bad = tmp_path / "noncanonical-receipt.json"
    data = json.dumps(receipt_obj).encode()
    bad.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_multiplicity_receipt(bad, plain_hash(data))
    for obj, loader, code in ((detail, load_multiplicity_detail, "detail_contract"),
                              (receipt_obj, load_multiplicity_receipt, "receipt_contract")):
        for changed in (dict(obj, unknown_key=1), {key: value for key, value in obj.items()
                                                    if key != "purpose"}):
            data = canonical_json(changed)
            bad = tmp_path / f"bad-{code}-{len(data)}.json"
            bad.write_bytes(data)
            with pytest.raises(Refusal, match=code):
                loader(bad, plain_hash(data))


def test_admission_control_file_ceiling(tmp_path):
    root, manifest, detail, detail_hash = _fixture(tmp_path, ["one text"])
    policy = root / "multiplicity-policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-multiplicity-policy/1",
                                       "purpose": "conditioning_target", "rule": "cap_per_cluster",
                                       "cap": 1}))
    admission = root / "admission.json"
    base = canonical_json({"schema": "setec-preflight-admission/1",
                           "overlap_detail_sha256": detail_hash,
                           "assignments": {"r0": {"admitted": True, "micro_weight": 1}}})
    admission.write_bytes(base + b" " * (8 * 1024 * 1024 - len(base)))
    receipt, _ = run_multiplicity(manifest, policy, detail, detail_hash,
                                  tmp_path / "at-limit", admission)
    assert json.loads(receipt)["stage_status"]["multiplicity"] == "passed"
    with admission.open("ab") as handle:
        handle.write(b" ")
    with pytest.raises(Refusal, match="size_limit"):
        run_multiplicity(manifest, policy, detail, detail_hash,
                         tmp_path / "over-limit", admission)


def test_reloader_rejects_impossible_withholding_reason(tmp_path):
    detail, _, _, _ = _run(tmp_path, ["alpha beta gamma", "alpha beta gamma delta"],
                           [1_000_000, 0])
    detail["withheld"][0]["reason"] = "cluster_not_admitted"
    detail["counts"]["withheld_distinct_in_admitted_cluster"] = 0
    detail["counts"]["withheld_cluster_not_admitted"] = 1
    detail["reason_counts"]["distinct_text_withheld"] = 0
    detail["stage_status"]["multiplicity"] = "passed"
    detail["reason_counts"]["ok"] = 1
    path = tmp_path / "forged-withholding.json"
    data = canonical_json(detail)
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_multiplicity_detail(path, plain_hash(data))


@pytest.mark.parametrize("rule,cap", [("one_representative_per_cluster", None),
                                      ("cap_per_cluster", 1)])
def test_receipt_reloader_derives_cluster_limit_from_counts(tmp_path, rule, cap):
    _, receipt, statuses, _ = _run(
        tmp_path, ["alpha beta gamma", "alpha beta gamma delta"],
        [1_000_000, 1_000_000], rule=rule, cap=cap)
    assert statuses["multiplicity"] == "failed"
    assert receipt["counts"]["clusters_multi_admitted"] == 1
    receipt["reason_counts"]["cluster_over_limit"] = 0
    receipt["reason_counts"]["ok"] = 1
    receipt["stage_status"]["multiplicity"] = "passed"
    path = tmp_path / "forged-limit-receipt.json"
    data = canonical_json(receipt)
    path.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_multiplicity_receipt(path, plain_hash(data))
