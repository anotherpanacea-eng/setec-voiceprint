"""Observable source-span proof and boundary classification regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from setec.preflight.common import Refusal, WorkBudget, canonical_json, load_manifest, plain_hash
from setec.preflight.span import run
from setec.preflight import span, span_core
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


def _packet(root: Path, sources: dict[str, bytes], rows: list[tuple], *,
            allowed: list[str] | None = None) -> tuple[Path, Path]:
    """Write sources and rows ``(candidate name, candidate bytes, source, start, end)``."""
    root.mkdir(parents=True)
    for name, data in sources.items():
        (root / name).write_bytes(data)
    lines = []
    for index, (name, data, source, start, end, *declared) in enumerate(rows):
        (root / name).write_bytes(data)
        source_bytes = (root / source).read_bytes()
        lines.append(canonical_json({
            "id": f"r{index}", "group_id": f"g{index}", "stratum": "synthetic", "path": name,
            "span": {"source_path": source,
                     "source_bytes_sha256": declared[0] if declared else plain_hash(source_bytes),
                     "start_byte": start, "end_byte": end}}))
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(lines))
    policy = root / "policy.json"
    policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                       "allowed_classes": allowed or ["none"]}))
    return manifest, policy


def _proof_results(out: Path) -> list[str]:
    return [row["proof_result"]
            for row in json.loads((out / "detail.json").read_bytes())["records"]]


def test_source_refusals_outrank_policy_contract(tmp_path, monkeypatch):
    """Slice 1 §4.6: size_limit and input_changed rank before policy_contract."""
    source = b"A.\n\nB."
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source},
                               [("candidate.txt", b"B.", "source.txt", 4, 6)])
    policy.write_bytes(canonical_json({"schema": "setec-preflight-span-policy/1",
                                       "allowed_classes": []}))
    with monkeypatch.context() as patch:
        patch.setattr(span_core, "SOURCE_LIMIT", len(source) - 1)
        with pytest.raises(Refusal, match="size_limit"):
            run(manifest, policy, tmp_path / "size")

    loaded = span.finish_manifest

    def load_then_replace(plan):
        result = loaded(plan)
        replacement = tmp_path / "in" / "replacement.txt"
        replacement.write_bytes(source)
        replacement.replace(tmp_path / "in" / "source.txt")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(span, "finish_manifest", load_then_replace)
        with pytest.raises(Refusal, match="input_changed"):
            run(manifest, policy, tmp_path / "changed")

    with monkeypatch.context() as patch:
        patch.setattr(span_core, "BOUNDARY_VISIT_LIMIT", 0)
        with pytest.raises(Refusal, match="policy_contract"):
            run(manifest, policy, tmp_path / "work")
    assert not any(path.name in {"size", "changed", "work"} for path in tmp_path.iterdir())


def test_hash_check_runs_before_range_check(tmp_path):
    manifest, policy = _packet(tmp_path / "in", {"source.txt": b"A."},
                               [("candidate.txt", b"A.", "source.txt", 0, 9, "0" * 64)])
    run(manifest, policy, tmp_path / "out")
    assert _proof_results(tmp_path / "out") == ["span_source_hash_mismatch"]


@pytest.mark.parametrize("tail", [b"\xff", b"\xed\xa0\x80"], ids=["invalid", "surrogate"])
def test_unencodable_source_is_a_finding_for_each_of_its_rows(tmp_path, tail):
    source = b"A. B." + tail
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source, "good.txt": b"A. B."}, [
        ("c0.txt", b"A.", "source.txt", 0, 2),
        ("c1.txt", b"B.", "source.txt", 3, 5),
        ("c2.txt", b"B. ", "good.txt", 3, 5),
    ])
    run(manifest, policy, tmp_path / "out")
    assert _proof_results(tmp_path / "out") == [
        "span_source_encoding", "span_source_encoding", "span_slice_mismatch"]


def test_one_bad_row_leaves_neighbours_on_its_source_proved(tmp_path):
    source = b"One.\n\nTwo.\n\nThree."
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source}, [
        ("c0.txt", b"One.", "source.txt", 0, 4),
        ("c1.txt", b"Two.", "source.txt", 7, 11),
        ("c2.txt", b"Three.", "source.txt", 12, 18),
    ])
    run(manifest, policy, tmp_path / "out")
    assert _proof_results(tmp_path / "out") == ["proved", "span_slice_mismatch", "proved"]


def _classes(source: bytes, candidate: bytes, occurrence: int = -1) -> tuple[str, ...]:
    start = source.rindex(candidate) if occurrence == -1 else source.index(candidate)
    return classify_span(source, start, start + len(candidate),
                         WorkBudget({"boundary byte visits": 1_000_000}))


@pytest.mark.parametrize("terminal", ".?!")
@pytest.mark.parametrize("closer", ['"', "'", ")", "]", "’", "”"])
def test_every_closer_after_every_terminal_ends_a_sentence(terminal, closer):
    first = f"Said {terminal}{closer}".encode()
    source = b"Before. " + first + b" After."
    assert "sentence_terminal" in _classes(source, first)
    assert "sentence_terminal" in _classes(source, b"After.")


@pytest.mark.parametrize("opener", ["‘", "“"])
def test_opening_curly_quotes_do_not_close_a_sentence(opener):
    source = f"Said.{opener} After.".encode()
    assert _classes(source, b"After.") == ("none",)


def test_span_after_abbreviation_reads_as_sentence_start():
    assert "sentence_terminal" in _classes(b"Mr. Smith went home.", b"Smith went home.")


def test_reused_candidate_and_identical_sources_are_read_once(tmp_path, monkeypatch):
    whole = b"First.\n\nSecond."
    root = tmp_path / "in"
    manifest, policy = _packet(root, {"s1.txt": b"Twin.", "s2.txt": b"Twin."}, [
        ("whole.txt", whole, "whole.txt", 0, len(whole)),
        ("part.txt", b"Second.", "whole.txt", 8, 15),
        ("t1.txt", b"Twin.", "s1.txt", 0, 5),
        ("t2.txt", b"Twin.", "s1.txt", 0, 5),
        ("t3.txt", b"Twin.", "s2.txt", 0, 5),
    ])
    reads: list[str] = []
    real_read = span_core.read_bounded

    def counting_read(anchor, rel, limit):
        reads.append(rel)
        return real_read(anchor, rel, limit)

    monkeypatch.setattr(span_core, "read_bounded", counting_read)
    run(manifest, policy, tmp_path / "out")
    assert sorted(reads) == ["s1.txt", "s2.txt"]
    assert _proof_results(tmp_path / "out") == ["proved"] * 5


def test_record_set_identity_matches_overlap_receipt(tmp_path):
    from setec.preflight.overlap import run as run_overlap

    source = b"Alpha.\n\nBeta gamma."
    root = tmp_path / "in"
    manifest, policy = _packet(root, {"source.txt": source}, [
        ("c0.txt", b"Alpha.", "source.txt", 0, 6),
        ("c1.txt", b"Beta gamma.", "source.txt", 8, 19),
    ])
    overlap_policy = root / "overlap-policy.json"
    overlap_policy.write_bytes(canonical_json({
        "schema": "setec-preflight-overlap-policy/1",
        "fuzzy": {"ngram": 2, "measure": "containment",
                  "threshold_numerator": 1, "threshold_denominator": 2},
        "coordination_strata": ["synthetic"]}))
    overlap_receipt, _ = run_overlap(manifest, overlap_policy, tmp_path / "overlap")
    span_receipt, _ = run(manifest, policy, tmp_path / "span")
    expected = json.loads(overlap_receipt)["record_set_sha256"]
    assert json.loads(span_receipt)["record_set_sha256"] == expected
    (root / "c0.txt").write_bytes(b"Alpha!")
    edited, _ = run(manifest, policy, tmp_path / "edited")
    assert json.loads(edited)["record_set_sha256"] != expected


def test_source_ceilings_pass_at_limit_and_refuse_one_over(tmp_path, monkeypatch):
    first, second = b"One.\n\nTwo.", b"Three.\n"
    manifest, policy = _packet(tmp_path / "in", {"a.txt": first, "b.txt": second}, [
        ("c0.txt", b"Two.", "a.txt", 6, 10),
        ("c1.txt", b"Three.", "b.txt", 0, 6),
    ])
    for name, limit in (("SOURCE_LIMIT", len(first)),
                        ("COMBINED_SOURCE_LIMIT", len(first) + len(second))):
        with monkeypatch.context() as patch:
            patch.setattr(span_core, name, limit)
            run(manifest, policy, tmp_path / f"{name}-at")
            patch.setattr(span_core, name, limit - 1)
            with pytest.raises(Refusal, match="size_limit"):
                run(manifest, policy, tmp_path / f"{name}-over")


def test_boundary_visit_ceiling_passes_at_limit_and_refuses_one_over(tmp_path, monkeypatch):
    source = b"Lead." + b" " * 64 + b"Body." + b" \t" * 32 + b"\n\nTail."
    start = source.index(b"Body.")
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source},
                               [("candidate.txt", b"Body.", "source.txt", start, start + 5)])
    budget = WorkBudget({"boundary byte visits": 1_000_000})
    classify_span(source, start, start + 5, budget)
    visits = budget.used["boundary byte visits"]
    assert visits > 64
    monkeypatch.setattr(span_core, "BOUNDARY_VISIT_LIMIT", visits)
    run(manifest, policy, tmp_path / "at")
    monkeypatch.setattr(span_core, "BOUNDARY_VISIT_LIMIT", visits - 1)
    with pytest.raises(Refusal, match="work_limit"):
        run(manifest, policy, tmp_path / "over")


def test_injected_exception_is_internal_refusal_only(tmp_path, monkeypatch, capsysbinary):
    manifest, policy = _packet(tmp_path / "in", {"source.txt": b"CANARY-SOURCE."},
                               [("canary-path.txt", b"CANARY-SOURCE.", "source.txt", 0, 14)])

    def explode(*_args):
        raise RuntimeError("CANARY-EXCEPTION canary-path.txt")

    monkeypatch.setattr(span_core.BoundaryReader, "classes", explode)
    out = tmp_path / "out"
    assert span.main(["--manifest", str(manifest), "--policy", str(policy),
                      "--out-bundle", str(out)]) == 2
    streams = capsysbinary.readouterr()
    assert streams.out == b"" and streams.err == b"internal_refusal\n"
    assert not out.exists()


def test_two_runs_publish_identical_receipts(tmp_path):
    source = b"A.\r\n\r\nB. C.\n"
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source}, [
        ("c0.txt", b"B.", "source.txt", 6, 8),
        ("c1.txt", b"C.", "source.txt", 9, 11),
    ])
    first, _ = run(manifest, policy, tmp_path / "first")
    second, _ = run(manifest, policy, tmp_path / "second")
    assert first == second == (tmp_path / "first" / "receipt.json").read_bytes()
    assert json.loads(first)["detail_sha256"] == plain_hash(
        (tmp_path / "second" / "detail.json").read_bytes())


def _forge(tmp_path: Path, obj: dict, loader) -> None:
    data = canonical_json(obj)
    path = tmp_path / f"forged-{plain_hash(data)[:12]}.json"
    path.write_bytes(data)
    loader(path, plain_hash(data))


def test_detail_reloader_refuses_proof_code_that_contradicts_row_fields(tmp_path):
    manifest, policy = _packet(tmp_path / "in", {"source.txt": b"A. B."}, [
        ("c0.txt", b"A.", "source.txt", 0, 2),
        ("c1.txt", b"B.", "source.txt", 3, 9),
    ])
    run(manifest, policy, tmp_path / "out")
    detail = json.loads((tmp_path / "out" / "detail.json").read_bytes())
    assert [row["proof_result"] for row in detail["records"]] == ["proved", "span_range"]
    for code in ("span_code_point", "span_slice_mismatch", "span_source_hash_mismatch"):
        forged = json.loads(json.dumps(detail))
        forged["records"][1]["proof_result"] = code
        forged["reason_counts"]["span_range"] = 0
        forged["reason_counts"][code] = 1
        with pytest.raises(Refusal, match="detail_contract"):
            _forge(tmp_path, forged, load_span_detail)
    forged = json.loads(json.dumps(detail))
    forged["records"][1]["source_size"] = 9
    with pytest.raises(Refusal, match="detail_contract"):
        _forge(tmp_path, forged, load_span_detail)


def test_receipt_reloader_refuses_counts_that_break_class_nesting(tmp_path):
    source = b"A.\n\nB."
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source},
                               [("candidate.txt", b"B.", "source.txt", 4, 6)])
    receipt = json.loads(run(manifest, policy, tmp_path / "out")[0])
    assert receipt["class_counts"]["blank_line_paragraph"]["allowed"] == 1
    moved = json.loads(json.dumps(receipt))
    moved["class_counts"]["blank_line_paragraph"]["allowed"] = 0
    moved["class_counts"]["physical_line"]["allowed"] = 1
    unnested = json.loads(json.dumps(receipt))
    unnested["matched_class_counts"]["blank_line_paragraph"] = 0
    for forged in (moved, unnested):
        with pytest.raises(Refusal, match="receipt_contract"):
            _forge(tmp_path, forged, load_span_receipt)


def _restamp(detail: dict) -> dict:
    """Recompute a forged detail's stage and reason tallies from its rows."""
    rows = detail["records"]
    proofs = [row["proof_result"] for row in rows]
    dispositions = [row["disposition"] for row in rows]
    statuses = {"intake": "passed",
                "span_proof": "passed" if set(proofs) == {"proved"} else "failed",
                "span_boundary": ("failed" if "disallowed" in dispositions else
                                  "needs_human_review" if "unproved" in dispositions else
                                  "passed")}
    detail["stage_status"] = statuses
    detail["reason_counts"] = {
        "ok": list(statuses.values()).count("passed"),
        **{code: proofs.count(code) for code in span_core.PROOF_RESULTS[1:]},
        "span_class_disallowed": dispositions.count("disallowed"),
        "span_unclassified": dispositions.count("unproved")}
    return detail


def _unprove(row: dict, code: str) -> None:
    row.update(proof_result=code, boundary_class=None, matched_classes=[],
               disposition="unproved")


@pytest.mark.parametrize("forgery", ["self_span_unproved", "one_source_two_decodes",
                                     "one_policy_two_dispositions"])
def test_detail_reloader_refuses_rows_that_contradict_each_other(tmp_path, forgery):
    whole = b"A. B."
    manifest, policy = _packet(tmp_path / "in", {}, [
        ("whole.txt", whole, "whole.txt", 0, 5),
        ("c1.txt", b"A.", "whole.txt", 0, 2),
        ("c2.txt", b"B.", "whole.txt", 3, 5),
    ])
    run(manifest, policy, tmp_path / "out")
    detail = json.loads((tmp_path / "out" / "detail.json").read_bytes())
    rows = detail["records"]
    assert [row["self_span"] for row in rows] == [True, False, False]
    assert rows[1]["matched_classes"] == rows[2]["matched_classes"]
    if forgery == "self_span_unproved":
        _unprove(rows[0], "span_slice_mismatch")
    elif forgery == "one_source_two_decodes":
        _unprove(rows[2], "span_source_encoding")
    else:
        rows[2]["disposition"] = "disallowed"
    with pytest.raises(Refusal, match="detail_contract"):
        _forge(tmp_path, _restamp(detail), load_span_detail)


@pytest.mark.parametrize("case, expected", [
    ("output_inside_manifest_dir+policy_contract", "path_confinement"),
    ("manifest_contract+policy_oversize", "size_limit"),
    ("manifest_contract+source_oversize", "size_limit"),
    ("manifest_contract+output_collision", "input_contract"),
])
def test_combined_violations_report_the_earliest_master_order_code(
        tmp_path, monkeypatch, capsysbinary, case, expected):
    # Slice 1 section 4.6, first match wins: confinement of every input and
    # the output, then every size ceiling, then contracts, then the output.
    source = b"A.\n\nB."
    manifest, policy = _packet(tmp_path / "in", {"source.txt": source},
                               [("candidate.txt", b"B.", "source.txt", 4, 6)])
    out = tmp_path / "out"
    if "output_inside" in case:
        out = manifest.parent / "out"
    if "policy_contract" in case:
        policy.write_bytes(b"{}")
    if "policy_oversize" in case:
        policy.write_bytes(b" " * (4 * 1024 + 1))
    if "source_oversize" in case:
        monkeypatch.setattr(span_core, "SOURCE_LIMIT", len(source) - 1)
    if "manifest_contract" in case:
        row = json.loads(manifest.read_bytes())
        row["unknown"] = True
        manifest.write_bytes(canonical_json(row))
    if "output_collision" in case:
        out.mkdir()
    assert span.main(["--manifest", str(manifest), "--policy", str(policy),
                      "--out-bundle", str(out)]) == 2
    assert capsysbinary.readouterr() == (b"", expected.encode() + b"\n")
    assert not (out / "receipt.json").exists()


class _BrokenStream:
    def write(self, data):
        raise BrokenPipeError()

    def flush(self):
        raise BrokenPipeError()


class _BrokenStdout:
    buffer = _BrokenStream()


def test_stdout_failure_after_publication_is_not_a_refusal(tmp_path, monkeypatch):
    # Slice 1 section 4.7: exit 0 means the bundle is published, and a stream
    # that fails afterwards cannot un-publish it.
    manifest, policy = _packet(tmp_path / "in", {"source.txt": b"A.\n\nB."},
                               [("candidate.txt", b"B.", "source.txt", 4, 6)])
    monkeypatch.setattr("sys.stdout", _BrokenStdout())
    out = tmp_path / "out"
    assert span.main(["--manifest", str(manifest), "--policy", str(policy),
                      "--out-bundle", str(out)]) == 0
    assert (out / "receipt.json").is_file()
