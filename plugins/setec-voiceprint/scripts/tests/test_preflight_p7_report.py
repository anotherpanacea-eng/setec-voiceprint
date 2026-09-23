"""P7 report binding, precedence, and unreachable clearance regressions."""

from __future__ import annotations

import json
import itertools
from pathlib import Path
import random

import pytest

from setec.preflight.common import Refusal, canonical_json, plain_hash
from setec.preflight.artifacts import run_calibrate, run_census
from setec.preflight.final import run as run_final
from setec.preflight.holdout_firewall import run as run_holdout
from setec.preflight.multiplicity import run as run_multiplicity
from setec.preflight.overlap import run as run_overlap
from setec.preflight.p7_report import main, run as run_report
from setec.preflight.p7_report_core import (
    P7_OBLIGATIONS, Row, compose_rows, decide,
)
from setec.preflight.multiplicity_core import PURPOSES
from setec.preflight.span import run as run_span


def _fixture(tmp_path: Path, *, text: str = "alpha beta gamma"):
    root = tmp_path / "packet"
    root.mkdir()
    data = text.encode()
    (root / "r0.txt").write_bytes(data)
    source = data
    (root / "source.txt").write_bytes(source)
    row = {"id": "r0", "group_id": "g0", "stratum": "synthetic", "path": "r0.txt",
           "span": {"source_path": "source.txt", "source_bytes_sha256": plain_hash(source),
                    "start_byte": 0, "end_byte": len(source)}}
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json(row))
    policy = tmp_path / "policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-overlap-policy/1",
                                       "fuzzy": {"ngram": 2, "measure": "containment",
                                                 "threshold_numerator": 1,
                                                 "threshold_denominator": 1},
                                       "coordination_strata": ["synthetic"]}))
    splits = tmp_path / "splits.json"
    splits.write_bytes(canonical_json({"schema": "setec-preflight-splits/1",
                                       "assignments": {"r0": "train"}}))
    intake = tmp_path / "intake"
    run_overlap(manifest, policy, intake, splits)
    final = tmp_path / "final"
    run_final(intake, manifest, policy, splits, final)
    return manifest, intake, final, policy, splits


def _paths(intake: Path, final: Path) -> dict[str, Path | None]:
    return {"final": final / "receipt.json", "intake": intake / "receipt.json",
            "span": None, "multiplicity": None, "holdout": None,
            "artifact": None, "calibration": None}


def test_minimal_report_never_clears_and_exits_three(tmp_path, capsys):
    manifest, intake, final, *_ = _fixture(tmp_path)
    paths = _paths(intake, final)
    data, rows = run_report(manifest, "conditioning_target", paths, None,
                            tmp_path / "report")
    report = json.loads(data)
    assert report["decision"] == "incomplete_required_stage"
    assert report["span_evidence"] == "declared"
    assert [row["obligation"] for row in report["rows"]] == list(P7_OBLIGATIONS)
    assert {row["status"] for row in report["rows"]
            if row["obligation"] in {"semantic_dedup", "perplexity_two_tail"}} == {"unavailable"}
    assert b"r0" not in data and b"alpha beta gamma" not in data
    args = ["--manifest", str(manifest), "--purpose", "conditioning_target",
            "--final-receipt", str(paths["final"]),
            "--intake-overlap-receipt", str(paths["intake"]),
            "--out-bundle", str(tmp_path / "report2")]
    assert main(args) == 3
    streams = capsys.readouterr()
    assert "semantic_dedup unavailable" in streams.err
    assert json.loads(streams.out)["decision"] == report["decision"]


def test_replacing_candidate_after_receipts_refuses_binding(tmp_path):
    manifest, intake, final, *_ = _fixture(tmp_path)
    (manifest.parent / "r0.txt").write_text("changed content")
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", _paths(intake, final), None,
                   tmp_path / "report")


def test_required_receipt_contract_and_intake_binding(tmp_path):
    manifest, intake, final, *_ = _fixture(tmp_path)
    paths = _paths(intake, final)
    paths["final"] = intake / "receipt.json"
    with pytest.raises(Refusal, match="receipt_contract"):
        run_report(manifest, "conditioning_target", paths, None, tmp_path / "report")
    paths = _paths(intake, final)
    forged = json.loads(paths["final"].read_bytes())
    forged["intake_receipt_sha256"] = "0" * 64
    fake_path = tmp_path / "fake-final.json"
    fake_path.write_bytes(canonical_json(forged))
    paths["final"] = fake_path
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", paths, None, tmp_path / "report")


def test_clusters_at_intake_row_reads_intake_split_integrity(tmp_path):
    manifest, _, final, policy, _ = _fixture(tmp_path)
    unsplit = tmp_path / "intake-without-splits"
    run_overlap(manifest, policy, unsplit)
    intake_receipt = json.loads((unsplit / "receipt.json").read_bytes())
    assert intake_receipt["stage_status"]["split_integrity"] == "not_run"
    # Task A refuses such an intake, so the final receipt must be hand-built.
    forged = json.loads((final / "receipt.json").read_bytes())
    forged["intake_receipt_sha256"] = plain_hash((unsplit / "receipt.json").read_bytes())
    forged["intake_detail_sha256"] = intake_receipt["detail_sha256"]
    forged_path = tmp_path / "hand-built-final.json"
    forged_path.write_bytes(canonical_json(forged))
    paths = _paths(unsplit, final)
    paths["final"] = forged_path
    data, _ = run_report(manifest, "conditioning_target", paths, None,
                         tmp_path / "report")
    row = next(item for item in json.loads(data)["rows"]
               if item["obligation"] == "clusters_at_intake")
    assert row["status"] == "not_run"
    assert row["receipt_sha256"] == forged["intake_receipt_sha256"]


def test_refusal_order_follows_master_order(tmp_path):
    manifest, intake, final, *_ = _fixture(tmp_path)
    bad_manifest = manifest.parent / "bad.jsonl"
    bad_manifest.write_bytes(b"not json\n")
    # input_contract outranks policy_contract (purpose) and receipt_contract
    # (a holdout receipt without a register).
    with pytest.raises(Refusal, match="input_contract"):
        run_report(bad_manifest, "unknown_purpose", _paths(intake, final), None,
                   tmp_path / "r1")
    paths = _paths(intake, final)
    paths["holdout"] = final / "receipt.json"
    with pytest.raises(Refusal, match="input_contract"):
        run_report(bad_manifest, "conditioning_target", paths, None, tmp_path / "r2")
    # receipt_contract (a malformed register) outranks receipt_binding (rule 1).
    sealed = _sealed(tmp_path, "unrelated sealed words", "sealed-order")
    holdout_receipt, _ = _holdout(tmp_path, manifest, sealed, 50)
    unbound = json.loads((final / "receipt.json").read_bytes())
    unbound["record_set_sha256"] = "0" * 64
    unbound_path = tmp_path / "unbound-final.json"
    unbound_path.write_bytes(canonical_json(unbound))
    paths = _paths(intake, final)
    paths["final"] = unbound_path
    paths["holdout"] = holdout_receipt
    malformed = tmp_path / "malformed-register.json"
    malformed.write_bytes(b"not a register")
    with pytest.raises(Refusal, match="receipt_contract"):
        run_report(manifest, "conditioning_target", paths, malformed, tmp_path / "r3")


def test_sealed_register_binding_compares_sets(tmp_path):
    # Spec 06 section 6.2 rule 6 compares the set of sealed manifest hashes.
    manifest, intake, final, *_ = _fixture(tmp_path)
    sealed = _sealed(tmp_path, "unrelated sealed words", "sealed-set")
    holdout_receipt, register = _holdout(tmp_path, manifest, sealed, 60, label="a")
    receipt = json.loads(holdout_receipt.read_bytes())
    receipt["sealed"] = [receipt["sealed"][0], {**receipt["sealed"][0], "label": "b"}]
    repeated = tmp_path / "repeated-sealed-hash.json"
    repeated.write_bytes(canonical_json(receipt))
    paths = _paths(intake, final)
    paths["holdout"] = repeated
    run_report(manifest, "conditioning_target", paths, register, tmp_path / "report")


def test_decision_precedence_and_all_purposes_unreachable():
    base = tuple(Row(name, "unavailable" if name in
                     {"semantic_dedup", "perplexity_two_tail"} else "passed",
                     None, None, None) for name in P7_OBLIGATIONS)
    for purpose in PURPOSES:
        assert decide(purpose, base) in {"not_eligible_by_policy",
                                         "incomplete_required_stage"}
    failed = tuple(Row(row.obligation, "failed" if row.obligation == "exact_dedup"
                       else row.status, row.source, row.receipt_sha256,
                       row.manifest_sha256) for row in base)
    assert decide("conditioning_target", failed) == "ineligible"
    assert decide("evaluation_fixture", failed) == "not_eligible_by_policy"
    assert decide("conditioning_target", tuple(
        Row(row.obligation, "passed", None, None, None) for row in base)) == "eligible"
    rng = random.Random(20260922)
    for _ in range(10_000):
        rows = tuple(Row(row.obligation,
                         "unavailable" if row.obligation in
                         {"semantic_dedup", "perplexity_two_tail"} else
                         rng.choice(("passed", "failed", "not_run", "needs_human_review")),
                         None, None, None) for row in base)
        for purpose in PURPOSES:
            assert decide(purpose, rows) not in {"eligible", "needs_human_review"}


def test_bound_artifact_categories_and_span_proof(tmp_path):
    manifest, intake, final, *_ = _fixture(tmp_path, text="ordinary \ufffd prose")
    artifact_policy = tmp_path / "artifact-policy.json"
    artifact_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-artifact-policy/1",
        "dispositions": {name: "review" for name in
                         ("markup", "apparatus", "verse", "encoding_normalization")},
        "coordination_strata": ["synthetic"],
    }))
    artifact_out = tmp_path / "artifact"
    run_census(manifest, artifact_policy, artifact_out)
    span_policy = tmp_path / "span-policy.json"
    span_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-span-policy/1",
        "allowed_classes": ["whole_document"],
    }))
    span_out = tmp_path / "span"
    run_span(manifest, span_policy, span_out)
    paths = _paths(intake, final)
    paths["artifact"] = artifact_out / "receipt.json"
    paths["span"] = span_out / "receipt.json"
    data, _ = run_report(manifest, "conditioning_target", paths, None,
                         tmp_path / "report")
    report = json.loads(data)
    rows = {row["obligation"]: row for row in report["rows"]}
    assert rows["encoding_normalization"]["status"] == "needs_human_review"
    assert rows["markup_verse_apparatus"]["status"] == "passed"
    assert report["span_evidence"] == "proved"
    assert rows["span_boundary"]["status"] == "passed"


def _sealed(tmp_path: Path, text: str, name: str) -> Path:
    root = tmp_path / name
    root.mkdir()
    data = text.encode()
    (root / "sealed.txt").write_bytes(data)
    path = root / "packet.jsonl"
    path.write_bytes(canonical_json({
        "id": "secretid", "group_id": "secretgroup", "stratum": "secretstratum",
        "path": "sealed.txt",
        "span": {"source_path": "sealed.txt", "source_bytes_sha256": plain_hash(data),
                 "start_byte": 0, "end_byte": len(data)},
    }))
    return path


def _holdout(tmp_path: Path, manifest: Path, sealed: Path, index: int,
             *, floor: int = 1, label: str = "secretlabel") -> tuple[Path, Path]:
    policy = tmp_path / f"holdout-policy{index}.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-holdout-policy/1",
                                       "ngram": 2, "shared_run": True,
                                       "candidate_containment": None,
                                       "sealed_containment": None,
                                       "min_sealed_distinct": floor}))
    private = tmp_path / f"private{index}"
    run_holdout(manifest, [(label, sealed)], policy, private,
                tmp_path / f"conflicts{index}")
    receipt = private / "receipt.json"
    register = tmp_path / f"register{index}.json"
    hash_value = json.loads(receipt.read_bytes())["sealed"][0]["manifest_sha256"]
    register.write_bytes(canonical_json({"schema": "setec-preflight-sealed-register/1",
                                         "manifest_sha256s": [hash_value]}))
    return receipt, register


def test_withheld_holdout_does_not_restore_conflict_bit(tmp_path):
    manifest, intake, final, *_ = _fixture(tmp_path)
    matching = _sealed(tmp_path, "alpha beta gamma", "matching")
    different = _sealed(tmp_path, "completely different text", "different")
    outputs = []
    for index, sealed in enumerate((matching, different)):
        receipt, register = _holdout(tmp_path, manifest, sealed, index, floor=2)
        paths = _paths(intake, final)
        paths["holdout"] = receipt
        data, _ = run_report(manifest, "conditioning_target", paths, register,
                             tmp_path / f"report{index}")
        outputs.append(data)
    assert outputs[0] == outputs[1]
    row = next(item for item in json.loads(outputs[0])["rows"]
               if item["obligation"] == "holdout_decontamination")
    assert row["status"] == "not_run"
    assert row["source"] is None and row["receipt_sha256"] is None


def _all_optional(tmp_path: Path, manifest: Path, intake: Path, final: Path,
                  overlap_policy: Path, splits: Path) -> tuple[dict[str, Path | None], Path]:
    paths = _paths(intake, final)
    span_policy = tmp_path / "span-policy-all.json"
    span_policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                            "allowed_classes": ["whole_document"]}))
    span_out = tmp_path / "span-all"
    run_span(manifest, span_policy, span_out)
    paths["span"] = span_out / "receipt.json"

    final_overlap = tmp_path / "final-overlap"
    run_overlap(manifest, overlap_policy, final_overlap, splits)
    multi_policy = tmp_path / "multiplicity-policy.json"
    multi_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-multiplicity-policy/1",
        "purpose": "conditioning_target", "rule": "one_representative_per_cluster", "cap": None,
    }))
    multi_out = tmp_path / "multiplicity-all"
    detail_path = final_overlap / "detail.json"
    run_multiplicity(manifest, multi_policy, detail_path,
                     plain_hash(detail_path.read_bytes()), multi_out)
    paths["multiplicity"] = multi_out / "receipt.json"

    sealed = _sealed(tmp_path, "alpha beta gamma", "sealed-all")
    holdout_receipt, register = _holdout(tmp_path, manifest, sealed, 100)
    paths["holdout"] = holdout_receipt

    artifact_policy = tmp_path / "artifact-policy-all.json"
    artifact_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-artifact-policy/1",
        "dispositions": {name: "review" for name in
                         ("markup", "apparatus", "verse", "encoding_normalization")},
        "coordination_strata": ["synthetic"],
    }))
    artifact_out = tmp_path / "artifact-all"
    run_census(manifest, artifact_policy, artifact_out)
    paths["artifact"] = artifact_out / "receipt.json"

    label_root = tmp_path / "calibration-packet"
    label_root.mkdir()
    texts = ["<p>artifact</p>", "dialect", "archaic", "rough", "unusual"]
    rows = []
    for index, text in enumerate(texts):
        data = text.encode()
        filename = f"row{index}.txt"
        (label_root / filename).write_bytes(data)
        rows.append({"id": f"r{index}", "group_id": f"g{index}",
                     "stratum": "synthetic", "path": filename,
                     "span": {"source_path": filename,
                              "source_bytes_sha256": plain_hash(data),
                              "start_byte": 0, "end_byte": len(data)}})
    cal_manifest = label_root / "packet.jsonl"
    cal_manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    cal_labels = tmp_path / "calibration-labels.json"
    classes = [None, "dialect", "archaic_spelling", "purposeful_roughness",
               "unusual_valid_syntax"]
    cal_labels.write_bytes(canonical_json({
        "schema": "setec-preflight-artifact-labels/1", "register_cells": ["synthetic"],
        "labels": [{"id": f"r{i}", "label": "artifact" if i == 0 else
                    "legitimate_variation", "register_cell": "synthetic",
                    "variation_class": classes[i]} for i in range(5)],
    }))
    cal_out = tmp_path / "calibration-all"
    run_calibrate(cal_manifest, plain_hash(cal_manifest.read_bytes()),
                  cal_labels, plain_hash(cal_labels.read_bytes()),
                  artifact_policy, cal_out)
    paths["calibration"] = cal_out / "receipt.json"
    return paths, register


@pytest.mark.parametrize("kind", ["span", "multiplicity", "holdout", "artifact"])
def test_each_optional_receipt_record_set_binding(tmp_path, kind):
    manifest, intake, final, overlap_policy, splits = _fixture(tmp_path)
    paths, register = _all_optional(tmp_path, manifest, intake, final,
                                    overlap_policy, splits)
    original = json.loads(paths[kind].read_bytes())
    original["record_set_sha256"] = "0" * 64
    changed = tmp_path / f"forged-{kind}.json"
    changed.write_bytes(canonical_json(original))
    paths[kind] = changed
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", paths, register,
                   tmp_path / "report")


def test_optional_receipt_purpose_register_and_calibration_binding(tmp_path):
    manifest, intake, final, overlap_policy, splits = _fixture(tmp_path)
    paths, register = _all_optional(tmp_path, manifest, intake, final,
                                    overlap_policy, splits)
    data, _ = run_report(manifest, "conditioning_target", paths, register,
                         tmp_path / "full-report")
    assert json.loads(data)["decision"] in {"ineligible", "incomplete_required_stage"}
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "rewrite_mirror", paths, register,
                   tmp_path / "wrong-purpose")
    multiplicity = json.loads(paths["multiplicity"].read_bytes())
    multiplicity["overlap_detail_sha256"] = "0" * 64
    wrong_graph = tmp_path / "wrong-graph-multiplicity.json"
    wrong_graph.write_bytes(canonical_json(multiplicity))
    original_multiplicity = paths["multiplicity"]
    paths["multiplicity"] = wrong_graph
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", paths, register,
                   tmp_path / "wrong-graph-report")
    paths["multiplicity"] = original_multiplicity
    changed_register = tmp_path / "wrong-register.json"
    changed_register.write_bytes(canonical_json({
        "schema": "setec-preflight-sealed-register/1",
        "manifest_sha256s": ["0" * 64],
    }))
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", paths, changed_register,
                   tmp_path / "wrong-register-report")
    calibration = json.loads(paths["calibration"].read_bytes())
    calibration["detector_sha256"] = "0" * 64
    changed_cal = tmp_path / "wrong-calibration.json"
    changed_cal.write_bytes(canonical_json(calibration))
    paths["calibration"] = changed_cal
    with pytest.raises(Refusal, match="receipt_binding"):
        run_report(manifest, "conditioning_target", paths, register,
                   tmp_path / "wrong-calibration-report")


def test_composed_rows_never_clear_for_any_optional_subset_or_purpose(tmp_path):
    manifest, intake, final, overlap_policy, splits = _fixture(tmp_path)
    paths, _ = _all_optional(tmp_path, manifest, intake, final,
                             overlap_policy, splits)
    available = {name: (json.loads(path.read_bytes()), plain_hash(path.read_bytes()))
                 for name, path in paths.items() if path is not None}
    optional = ("span", "multiplicity", "holdout", "artifact", "calibration")
    for purpose in PURPOSES:
        for flags in itertools.product((False, True), repeat=len(optional)):
            receipts = dict(available)
            for name, included in zip(optional, flags):
                if not included:
                    receipts[name] = None
            for status in ("passed", "failed", "not_run", "needs_human_review"):
                current = {name: (dict(value[0]), value[1]) if value else None
                           for name, value in receipts.items()}
                for item in current.values():
                    if item is not None and "stage_status" in item[0]:
                        item[0]["stage_status"] = {
                            key: status for key in item[0]["stage_status"]}
                rows = compose_rows(purpose, current)
                assert tuple(row.obligation for row in rows) == P7_OBLIGATIONS
                assert {row.status for row in rows if row.obligation in
                        {"semantic_dedup", "perplexity_two_tail"}} == {"unavailable"}
                assert decide(purpose, rows) not in {"eligible", "needs_human_review"}
