"""Observable source-span proof and boundary classification regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setec.preflight.common import Refusal, WorkBudget, canonical_json, load_manifest, plain_hash
from setec.preflight.span import run
from setec.preflight import span_core
from setec.preflight.span_core import (
    classify_span, load_span_detail, load_span_policy, load_span_receipt, prove_spans,
)


def _run(tmp_path: Path, source: bytes, candidate: bytes, start: int, end: int,
         *, allowed: list[str] | None = None, source_hash: str | None = None):
    root = tmp_path / "inputs"
    root.mkdir(parents=True)
    (root / "source.txt").write_bytes(source)
    (root / "candidate.txt").write_bytes(candidate)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json({
        "id": "r1", "group_id": "g1", "stratum": "synthetic", "path": "candidate.txt",
        "span": {"source_path": "source.txt",
                 "source_bytes_sha256": source_hash or plain_hash(source),
                 "start_byte": start, "end_byte": end},
    }))
    policy = root / "policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                       "allowed_classes": allowed or ["blank_line_paragraph"]}))
    out = tmp_path / "bundle"
    receipt_bytes, statuses = run(manifest, policy, out)
    detail = json.loads((out / "detail.json").read_bytes())
    receipt = json.loads(receipt_bytes)
    return detail, receipt, statuses, out


def test_exact_indented_paragraph_matches_sentence_and_line(tmp_path):
    source = b"A.\n\n    B.\n\nC."
    start = source.index(b"B.")
    detail, receipt, statuses, out = _run(tmp_path, source, b"B.", start, start + 2)
    row = detail["records"][0]
    assert row["proof_result"] == "proved"
    assert row["boundary_class"] == "blank_line_paragraph"
    assert row["matched_classes"] == ["blank_line_paragraph", "physical_line",
                                       "sentence_terminal", "none"]
    assert row["disposition"] == "allowed"
    assert statuses == {"intake": "passed", "span_proof": "passed",
                        "span_boundary": "passed"}
    assert receipt["span_evidence"] == "proved"
    assert load_span_detail(out / "detail.json", receipt["detail_sha256"]) == detail
    assert load_span_receipt(out / "receipt.json",
                             plain_hash((out / "receipt.json").read_bytes())) == receipt


def test_source_mismatch_is_per_record_finding(tmp_path):
    detail, receipt, statuses, out = _run(tmp_path, b"real source", b"real", 0, 4,
                                          source_hash="0" * 64)
    assert detail["records"][0]["proof_result"] == "span_source_hash_mismatch"
    assert statuses["span_proof"] == "failed"
    assert statuses["span_boundary"] == "needs_human_review"
    assert receipt["span_evidence"] == "not_proved"
    assert out.is_dir()


def test_utf8_mid_code_point_and_exact_bytes(tmp_path):
    source = "é!".encode()
    detail, _, _, _ = _run(tmp_path / "offset", source, b"!", 1, 3)
    assert detail["records"][0]["proof_result"] == "span_code_point"
    detail, _, _, _ = _run(tmp_path / "slice", source, b"E!", 0, 3)
    assert detail["records"][0]["proof_result"] == "span_slice_mismatch"


@pytest.mark.parametrize("source", [b"A.\n\nB.", b"A.\r\n\r\nB.", b"A.\r\rB."])
def test_paragraph_newline_forms(tmp_path, source):
    start = source.index(b"B.")
    detail, _, _, _ = _run(tmp_path, source, b"B.", start, start + 2)
    assert detail["records"][0]["boundary_class"] == "blank_line_paragraph"


def test_sentence_only_policy_accepts_line_aligned_sentence(tmp_path):
    source = b"First sentence.\nSecond sentence.\n"
    end = source.index(b"\n")
    detail, _, statuses, _ = _run(tmp_path, source, source[:end], 0, end,
                                  allowed=["sentence_terminal"])
    assert detail["records"][0]["boundary_class"] == "physical_line"
    assert "sentence_terminal" in detail["records"][0]["matched_classes"]
    assert statuses["span_boundary"] == "passed"


def test_reloader_refuses_forged_proof_status(tmp_path):
    detail, _, _, _ = _run(tmp_path, b"real source", b"real", 0, 4,
                           source_hash="0" * 64)
    detail["records"][0]["proof_result"] = "proved"
    detail["records"][0]["boundary_class"] = "none"
    detail["records"][0]["matched_classes"] = ["none"]
    detail["records"][0]["disposition"] = "allowed"
    artifact = tmp_path / "forged.json"
    data = canonical_json(detail)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_span_detail(artifact, plain_hash(data))


def test_bom_document_edge_and_self_span(tmp_path):
    source = b"\xef\xbb\xbfA.\n"
    detail, _, _, _ = _run(tmp_path / "bom", source, b"A.", 3, 5)
    assert detail["records"][0]["boundary_class"] == "whole_document"

    root = tmp_path / "self"
    root.mkdir()
    (root / "candidate.txt").write_bytes(b"A.")
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json({
        "id": "r1", "group_id": "g1", "stratum": "synthetic", "path": "candidate.txt",
        "span": {"source_path": "candidate.txt", "source_bytes_sha256": plain_hash(b"A."),
                 "start_byte": 0, "end_byte": 2},
    }))
    policy = root / "policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                       "allowed_classes": ["whole_document"]}))
    receipt, _ = run(manifest, policy, tmp_path / "self-bundle")
    assert json.loads(receipt)["span_evidence"] == "proved"


@pytest.mark.parametrize("allowed", [[], ["none", "whole_document"],
                                      ["none", "none"], ["unknown"], [True]])
def test_invalid_boundary_policy_refuses(tmp_path, allowed):
    path = tmp_path / "policy.json"
    path.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                     "allowed_classes": allowed}))
    with pytest.raises(Refusal, match="policy_contract"):
        load_span_policy(path)


def test_source_replacement_after_manifest_load_refuses(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    source = b"A.\n\nB."
    (root / "source.txt").write_bytes(source)
    (root / "candidate.txt").write_bytes(b"B.")
    manifest_path = root / "packet.jsonl"
    manifest_path.write_bytes(canonical_json({
        "id": "r1", "group_id": "g1", "stratum": "synthetic", "path": "candidate.txt",
        "span": {"source_path": "source.txt", "source_bytes_sha256": plain_hash(source),
                 "start_byte": 4, "end_byte": 6},
    }))
    policy_path = root / "policy.json"
    policy_path.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                            "allowed_classes": ["none"]}))
    manifest = load_manifest(manifest_path)
    (root / "source-new.txt").write_bytes(source)
    (root / "source.txt").unlink()
    (root / "source-new.txt").rename(root / "source.txt")
    with pytest.raises(Refusal, match="input_changed"):
        prove_spans(manifest, load_span_policy(policy_path))


def test_boundary_nesting_over_small_generated_sources():
    for source in (b"A.\n\nB.", b" A.\r\n\r\nB. ", "é. B.".encode()):
        for start in range(len(source)):
            for end in range(start + 1, len(source) + 1):
                classes = classify_span(source, start, end,
                                        WorkBudget({"boundary byte visits": 1_000_000}))
                assert classes[-1] == "none"
                if "whole_document" in classes:
                    assert "blank_line_paragraph" in classes
                if "blank_line_paragraph" in classes:
                    assert "physical_line" in classes


def test_output_collision_and_source_size_limit(tmp_path, monkeypatch):
    root = tmp_path / "collision"
    root.mkdir()
    (tmp_path / "bundle").mkdir()
    source = b"A."
    (root / "source.txt").write_bytes(source)
    (root / "candidate.txt").write_bytes(source)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json({
        "id": "r1", "group_id": "g1", "stratum": "synthetic", "path": "candidate.txt",
        "span": {"source_path": "source.txt", "source_bytes_sha256": plain_hash(source),
                 "start_byte": 0, "end_byte": 2},
    }))
    policy = root / "policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                       "allowed_classes": ["none"]}))
    with pytest.raises(Refusal, match="output_collision"):
        run(manifest, policy, tmp_path / "bundle")
    monkeypatch.setattr(span_core, "SOURCE_LIMIT", 1)
    with pytest.raises(Refusal, match="size_limit"):
        run(manifest, policy, tmp_path / "limited-bundle")


def test_reloader_rejects_added_key_and_receipt_tamper(tmp_path):
    detail, receipt, _, _ = _run(tmp_path, b"A.", b"A.", 0, 2)
    detail["extra"] = True
    data = canonical_json(detail)
    path = tmp_path / "bad-detail.json"
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_span_detail(path, plain_hash(data))
    receipt["disposition_counts"]["allowed"] = 0
    data = canonical_json(receipt)
    path = tmp_path / "bad-receipt.json"
    path.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_span_receipt(path, plain_hash(data))
