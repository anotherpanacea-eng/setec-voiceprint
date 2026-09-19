"""Contract behavior for the pure Arc 2 descriptive report builder."""

from __future__ import annotations

import copy
import hashlib
import json
import math

import pytest

from argument_descriptive_report import ReportValidationError, build_descriptive_report


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def field(value=None, state="assigned"):
    reasons = {"substantive_abstention": "no_supported_argumentative_function",
               "adjudication_pending": "unresolved_interpretation",
               "missing_or_malformed": "missing_response_field",
               "provider_failure": "provider_failure"}
    return {"value": value if state == "assigned" else None, "state": state,
            "reason": None if state == "assigned" else reasons[state]}


def bundle(*, units=None, dispositions=None, mutate=None, empty_population=False, equal_responses=False, structures=None):
    """Build a complete synthetic declared graph, with mutation before downstream hashing."""
    if units is None:
        units = [
            [("support", "argumentation"), ("support", "exposition"),
             ("proposal", "argumentation")],
            [("thesis", "argumentation"), ("rebuttal", "exposition")],
        ]
    if dispositions is None:
        dispositions = ["applicable_for_B1_B2"] * (2 if empty_population else 1)
    artifacts = {}
    mutated = mutate or (lambda _name, _value: None)

    def add(name, value):
        value = copy.deepcopy(value)
        mutated(name, value)
        raw = value if type(value) is bytes else encoded(value)
        key = digest(raw)
        artifacts[key] = raw
        return key

    def opaque(name):
        return add(name, ("opaque:" + ("response0A" if equal_responses and name == "response0B" else name)).encode())

    rubric, rule, form = (opaque(name) for name in ("rubric", "rule", "form"))
    labelers = []
    for letter in "AB":
        labelers.append({"labeler_id": "labeler" + letter,
                         "identity_sha256": opaque("identity" + letter),
                         "settings_sha256": opaque("settings" + letter),
                         "prompt_template_sha256": opaque("template" + letter),
                         "parser_identity_sha256": opaque("parser" + letter)})
    protocol = {"schema": "setec.argument_report_protocol.v1", "protocol_id": "p1",
                "frozen_at": "2026-01-01T00:00:00Z", "rubric_sha256": rubric,
                "adjudication_rule_sha256": rule, "applicability_form_sha256": form,
                "adjudicator_id": "adjudicator", "applicability_reviewer_id": "reviewer",
                "labelers": labelers, "independence_basis": "same_model_repeatability",
                "independence_note": "Declared repeatability, no validity inference"}
    protocol_hash = add("protocol", protocol)
    cohort_works = []
    maps = []
    for index, labels in enumerate(units):
        work_id = f"work{index}"
        kinds = structures[index] if structures is not None else ["prose"] * len(labels)
        assert kinds.count("prose") == len(labels)
        texts = [f"Private work {index} paragraph {i}." for i in range(len(kinds))]
        source = "\n".join(texts).encode()
        source_hash = add(f"source{index}", source)
        offset = 0
        nodes = []
        prose_ids = []
        for i, (kind, text) in enumerate(zip(kinds, texts)):
            if kind == "prose":
                prose_ids.append(f"u{i}")
            nodes.append({"id": f"u{i}", "kind": kind, "start": offset,
                          "end": offset + len(text), "text_sha256": digest(text.encode()),
                          "disposition": {"prose": "measure", "boundary": "substantive_nonprose",
                                          "excluded": "layout_only"}[kind]})
            offset += len(text) + 1
        block_map_hash = add(f"map{index}", {"schema": "setec.argument_block_map.v1",
                                                 "source_sha256": source_hash, "nodes": nodes})
        review = {"schema": "setec.argument_source_review.v1", "work_id": work_id,
                  "source_sha256": source_hash, "block_map_sha256": block_map_hash,
                  "reviewer_id": "source reviewer", "reviewed_at": "2026-01-01T00:00:00Z",
                  "source_disposition": "admitted_for_declared_pilot",
                  "map_disposition": "reviewed_whole_work_map",
                  "evidence_sha256": opaque(f"review-evidence{index}")}
        review_hash = add(f"review{index}", review)
        groups = {"author": {"state": "known", "ids": ["alice", "bob"] if index == 0 else ["bob"]},
                  "hearing": {"state": "unknown", "ids": []},
                  "agency": {"state": "not_applicable", "ids": []}}
        work = {"work_id": work_id, "population_id": "pilot", "source_identity": f"source{index}",
                "version_identity": "v1", "duplicate_family_id": f"family{index}",
                "source_sha256": source_hash, "block_map_sha256": block_map_hash,
                "source_review_sha256": review_hash, "groups": groups}
        mutated(f"cohort-work{index}", work)
        cohort_works.append(work)
        maps.append((source_hash, block_map_hash, prose_ids))
    populations = ["pilot", "empty"] if empty_population else ["pilot"]
    cohort_hash = add("cohort", {"schema": "setec.argument_report_cohort.v1",
                                 "cohort_id": "c1", "frozen_at": "2026-01-01T00:00:00Z",
                                 "populations": populations, "works": cohort_works})
    adjudication_hashes = []
    for index, labels in enumerate(units):
        work = cohort_works[index]
        source_hash, block_map_hash, prose_ids = maps[index]
        work_id = work["work_id"]

        def annotation(run_id, entries):
            return {"schema": "setec.argument_annotation_candidate.v1",
                    "block_map_sha256": block_map_hash, "annotator_run_id": run_id,
                    "entries": [{"unit_id": prose_ids[i], "role": field(role, state=role if role in
                                 ("substantive_abstention", "adjudication_pending", "missing_or_malformed", "provider_failure") else "assigned"),
                                 "mode": field(mode, state=mode if mode in
                                 ("adjudication_pending", "missing_or_malformed", "provider_failure") else "assigned")}
                                for i, (role, mode) in enumerate(entries)]}

        run_hashes = []
        for letter, labeler in zip("AB", labelers):
            candidate_hash = add(f"candidate{index}{letter}", annotation(f"run{index}{letter}", labels))
            run = {"schema": "setec.argument_label_run.v1", "run_id": f"run{index}{letter}",
                   "work_id": work_id, "cohort_sha256": cohort_hash, "protocol_sha256": protocol_hash,
                   "labeler_id": labeler["labeler_id"], "context_checked_at": "2026-01-02T00:00:00Z",
                   "started_at": "2026-01-02T00:00:00Z", "completed_at": "2026-01-02T01:00:00Z",
                   "source_sha256": source_hash, "block_map_sha256": block_map_hash,
                   "candidate_sha256": candidate_hash,
                   "rendered_prompt_sha256": opaque(f"prompt{index}{letter}"),
                   "raw_response_sha256": opaque(f"response{index}{letter}"),
                   "parse_diagnostics_sha256": opaque(f"parse{index}{letter}"),
                   "parser_identity_sha256": labeler["parser_identity_sha256"],
                   "context_evidence_sha256": opaque(f"context{index}{letter}")}
            run_hashes.append(add(f"run{index}{letter}", run))
        final_hash = add(f"final{index}", annotation(f"adjudication{index}", labels))
        adjudication = {"schema": "setec.argument_adjudication.v1", "adjudication_id": f"adjudication{index}",
                        "work_id": work_id, "cohort_sha256": cohort_hash, "protocol_sha256": protocol_hash,
                        "source_sha256": source_hash, "block_map_sha256": block_map_hash,
                        "adjudicator_id": "adjudicator", "completed_at": "2026-01-03T00:00:00Z",
                        "disposition": "completed_with_unresolved_preserved", "label_run_sha256": run_hashes,
                        "final_annotation_sha256": final_hash,
                        "decisions": [{"unit_id": prose_ids[i], "role_justification": "reviewed role",
                                       "mode_justification": "reviewed mode"} for i in range(len(labels))]}
        adjudication_hashes.append(add(f"adjudication{index}", adjudication))
    applicability_hashes = []
    for index, population in enumerate(populations):
        covered = adjudication_hashes if population == "pilot" else []
        record = {"schema": "setec.argument_applicability.v1", "population_id": population,
                  "cohort_sha256": cohort_hash, "protocol_sha256": protocol_hash,
                  "reviewer_id": "reviewer", "completed_at": "2026-01-04T00:00:00Z",
                  "disposition": dispositions[index], "reasons": "declared pilot determination",
                  "adjudication_sha256": covered}
        applicability_hashes.append(add(f"applicability{index}", record))
    manifest = {"schema": "setec.argument_descriptive_report_input.v1", "cohort_sha256": cohort_hash,
                "protocol_sha256": protocol_hash,
                "works": [{"work_id": f"work{i}", "adjudication_sha256": key}
                          for i, key in enumerate(adjudication_hashes)],
                "populations": [{"population_id": value, "applicability_sha256": key}
                                for value, key in zip(populations, applicability_hashes)]}
    mutated("manifest", manifest)
    return encoded(manifest), artifacts


def test_complete_report_is_final_only_ordered_and_receipt_is_content_free():
    manifest, artifacts = bundle()
    result = build_descriptive_report(manifest, artifacts)
    assert result.private_report["artifact_sha256"] == sorted(artifacts)
    assert [w["work_id"] for w in result.private_report["works"]] == ["work0", "work1"]
    first = result.private_report["works"][0]
    assert first["final_measures"]["support_to_support_rate"]["value"] == .5
    assert first["final_measures"]["support_to_proposal_rate"]["value"] == .5
    assert first["final_diagnostics"]["within_run_pair_n"] == 2
    assert sum(cell["count"] for cell in first["final_diagnostics"]["role_state_pairs"]) == 2
    assert result.receipt["work_n"] == 2
    assert result.receipt["artifact_n"] == len(artifacts)
    raw_receipt = json.dumps(result.receipt)
    for private in ("work0", "pilot", "alice", "labelerA", "adjudicator", "Private work",
                    "same_model_repeatability", "2026-01-04"):
        assert private not in raw_receipt
    assert build_descriptive_report(manifest, artifacts) == result


def test_support_denominator_and_opening_missingness():
    labels = [[("support", "argumentation"), ("adjudication_pending", "exposition"),
               ("proposal", "exposition")]]
    work = build_descriptive_report(*bundle(units=labels)).private_report["works"][0]
    assert work["final_diagnostics"]["within_run_pair_n"] == 2
    assert work["final_diagnostics"]["support_unassigned_successor_counts"]["adjudication_pending"] == 1
    assert work["final_measures"]["support_to_proposal_rate"]["unavailable_reason"] == "no_eligible_support_successor"
    labels = [[("support", "argumentation"), ("rebuttal", "exposition")]]
    work = build_descriptive_report(*bundle(units=labels)).private_report["works"][0]
    assert work["final_measures"]["support_to_proposal_rate"]["value"] == 0
    assert work["final_measures"]["support_to_support_rate"]["value"] == 0
    labels = [[("missing_or_malformed", "argumentation"), ("thesis", "exposition")]]
    work = build_descriptive_report(*bundle(units=labels)).private_report["works"][0]
    assert work["final_measures"]["thesis_opening_tendency"]["unavailable_reason"] == "opening_role_unassigned"
    assert work["final_diagnostics"]["opening_role_state_counts"]["missing_or_malformed"] == 1
    labels = [[("substantive_abstention", "argumentation"), ("support", "exposition")]]
    work = build_descriptive_report(*bundle(units=labels)).private_report["works"][0]
    assert work["final_measures"]["argumentation_share"]["value"] == .5
    assert work["final_measures"]["support_to_proposal_rate"]["value"] is None


def test_unweighted_mean_sample_sd_and_empty_population():
    labels = [[("thesis", "argumentation")],
              [("thesis", "argumentation")] + [("support", "exposition")] * 8]
    result = build_descriptive_report(*bundle(units=labels, empty_population=True)).private_report
    summary = result["populations"][0]["measures"]["argumentation_share"]
    assert summary["mean"] == pytest.approx(5 / 9)
    assert summary["sample_sd"] == pytest.approx(4 * math.sqrt(2) / 9)
    assert summary["mean"] != .2
    empty = result["populations"][1]
    assert empty["total_work_n"] == 0
    assert empty["measures"]["argumentation_share"]["mean"] is None
    assert empty["labeler_comparison"]["role"]["conditional_agreement"] is None
    assert result["populations"][0]["groups"]["author"] == {
        "distinct_known_id_n": 2, "known_work_n": 2,
        "unknown_work_n": 0, "not_applicable_work_n": 0}


@pytest.mark.parametrize("disposition,available", [
    ("B2_only", {"argumentation_share"}),
    ("insufficiently_specified", set()),
    ("requires_separately_versioned_instrument", set()),
])
def test_applicability_gates_final_and_both_candidates(disposition, available):
    result = build_descriptive_report(*bundle(dispositions=[disposition])).private_report
    for work in result["works"]:
        for mapping in [work["final_measures"]] + [c["measures"] for c in work["candidate_diagnostics"]]:
            for key, value in mapping.items():
                if key not in available:
                    assert value == {"numerator": None, "denominator": None, "value": None,
                                     "unavailable_reason": "applicability_not_permitted"}
        for candidate in work["candidate_diagnostics"]:
            assert all(candidate["final_minus_candidate"][key] is None for key in set(work["final_measures"]) - available)


@pytest.mark.parametrize("name,change,reason", [
    ("review0", lambda r: r.update(work_id="other"), "source_review_link_mismatch"),
    ("run0A", lambda r: r.update(labeler_id="labelerB"), "run_link_mismatch"),
    ("run0A", lambda r: r.update(context_checked_at="2025-01-01T00:00:00Z"), "run_time_order"),
    ("run0A", lambda r: r.update(parser_identity_sha256="0" * 64), "run_link_mismatch"),
    ("candidate0A", lambda r: r.update(annotator_run_id="other"), "annotator_run_mismatch"),
    ("adjudication0", lambda r: r.update(adjudicator_id="other"), "adjudication_link_mismatch"),
    ("adjudication0", lambda r: r["decisions"].reverse(), "decision_unit_mismatch"),
    ("adjudication0", lambda r: r.update(completed_at="2026-01-01T00:00:00Z"), "run_time_order"),
    ("applicability0", lambda r: r.update(reviewer_id="other"), "applicability_link_mismatch"),
    ("applicability0", lambda r: r.update(adjudication_sha256=[]), "applicability_coverage"),
    ("applicability0", lambda r: r.update(completed_at="2026-01-01T00:00:00Z"), "applicability_time_order"),
    ("manifest", lambda r: r["works"].reverse(), "work_link_mismatch"),
])
def test_relinked_but_false_authority_refuses(name, change, reason):
    def mutate(current, record):
        if current == name:
            change(record)
    manifest, artifacts = bundle(mutate=mutate)
    with pytest.raises(ReportValidationError) as exc:
        build_descriptive_report(manifest, artifacts)
    assert exc.value.reason == reason
    assert "other" not in str(exc.value)


def test_stale_hash_missing_unused_bad_utf8_duplicate_key_and_invalid_values_refuse():
    manifest, artifacts = bundle()
    key = next(k for k, v in artifacts.items() if v.startswith(b"Private work"))
    corrupt = dict(artifacts); corrupt[key] += b"x"
    with pytest.raises(ReportValidationError, match="artifact_hash_mismatch"):
        build_descriptive_report(manifest, corrupt)
    missing = dict(artifacts); del missing[key]
    with pytest.raises(ReportValidationError, match="missing_artifact"):
        build_descriptive_report(manifest, missing)
    unused = dict(artifacts); unused[digest(b"unused")] = b"unused"
    with pytest.raises(ReportValidationError, match="unused_artifact"):
        build_descriptive_report(manifest, unused)
    for raw, reason in [(b"\xff", "invalid_utf8"), (b'{"schema":1,"schema":2}', "duplicate_json_key"),
                        (b'{"schema":NaN}', "nonfinite_json_number"), (b"[]", "invalid_root")]:
        with pytest.raises(ReportValidationError) as exc:
            build_descriptive_report(raw, artifacts)
        assert exc.value.reason == reason
    for bad in ["1" * 64, False, b"not a hash"]:
        altered = dict(artifacts); altered[bad] = b"x"
        with pytest.raises(ReportValidationError):
            build_descriptive_report(manifest, altered)


def test_duplicate_declared_identity_and_bad_groups_refuse():
    for mutation, reason in [
        (lambda w: w.update(duplicate_family_id="family0"), "duplicate_work_identity"),
        (lambda w: w.update(source_identity="source0"), "duplicate_work_identity"),
        (lambda w: w["groups"]["author"].update(state="unknown"), "invalid_group"),
    ]:
        manifest, artifacts = bundle(mutate=lambda name, value: mutation(value) if name == "cohort-work1" else None)
        with pytest.raises(ReportValidationError) as exc:
            build_descriptive_report(manifest, artifacts)
        assert exc.value.reason == reason




def test_candidate_disagreement_effects_and_pooled_confusion_follow_hand_oracle():
    labels = [[("support", "argumentation"), ("support", "missing_or_malformed"),
               ("missing_or_malformed", "exposition"), ("proposal", "missing_or_malformed")]]

    def change(name, record):
        if name == "candidate0B":
            entries = record["entries"]
            entries[1]["role"] = field("proposal")
            entries[2]["mode"] = field("argumentation")
            entries[3]["mode"] = field("exposition")

    report = build_descriptive_report(*bundle(units=labels, mutate=change)).private_report
    work = report["works"][0]
    role = work["labeler_comparison"]["role"]
    mode = work["labeler_comparison"]["mode"]
    assert (role["jointly_assigned_n"], role["same_assigned_value_n"],
            role["different_assigned_value_n"], role["conditional_agreement"]) == (3, 2, 1, 2 / 3)
    assert (mode["jointly_assigned_n"], mode["same_assigned_value_n"],
            mode["different_assigned_value_n"], mode["conditional_agreement"]) == (2, 1, 1, .5)
    assert sum(cell["count"] for cell in role["state_pairs"]) == 4
    assert sum(cell["count"] for cell in mode["assigned_value_pairs"]) == 2
    assert work["candidate_diagnostics"][1]["final_minus_candidate"] == {
        "support_to_proposal_rate": -1,
        "support_to_support_rate": 1,
        "argumentation_share": pytest.approx(-1 / 6),
        "thesis_opening_tendency": 0,
    }
    assert all(v == 0 for v in work["candidate_diagnostics"][0]["final_minus_candidate"].values())
    pooled = report["populations"][0]["labeler_comparison"]
    assert pooled["scope"] == "pooled_unit_counts"
    assert pooled["role"]["conditional_agreement"] == 2 / 3
    assert report["populations"][0]["measures"]["argumentation_share"]["mean"] == .5


def test_same_semantic_candidates_and_identical_raw_responses_are_allowed():
    manifest, artifacts = bundle(units=[[('support', 'argumentation')]], equal_responses=True)
    # The default candidates already carry identical entry lists and distinct run IDs.
    result = build_descriptive_report(manifest, artifacts)
    first = result.private_report["works"][0]
    assert first["labeler_comparison"]["role"]["conditional_agreement"] == 1
    assert first["candidate_diagnostics"][0]["candidate_sha256"] != first["candidate_diagnostics"][1]["candidate_sha256"]


def test_private_ids_and_prose_never_enter_validation_errors():
    secret = "secret-corpus-prose-XYZ"
    def change(name, record):
        if name == "review0":
            record["reviewer_id"] = secret
            record["source_disposition"] = secret
    with pytest.raises(ReportValidationError) as exc:
        build_descriptive_report(*bundle(mutate=change))
    assert secret not in str(exc.value)
    assert exc.value.reason == "invalid_enum"


def test_coherently_rehashed_bundle_is_new_input_not_detected_authenticity():
    original = build_descriptive_report(*bundle())
    def change(name, record):
        if name == "protocol":
            record["independence_note"] = "Another declared interpretation"
    changed = build_descriptive_report(*bundle(mutate=change))
    assert original.receipt["manifest_sha256"] != changed.receipt["manifest_sha256"]
    assert changed.private_report["independence"]["note"] == "Another declared interpretation"




def test_boundaries_break_edges_but_layout_exclusions_do_not():
    labels = [[("support", "argumentation"), ("proposal", "exposition")]]
    for middle, expected_edge, expected_value in [
        ("boundary", 0, None), ("excluded", 1, 1),
    ]:
        work = build_descriptive_report(*bundle(units=labels,
            structures=[["prose", middle, "prose"]])).private_report["works"][0]
        assert work["final_diagnostics"]["within_run_pair_n"] == expected_edge
        assert work["final_measures"]["support_to_proposal_rate"]["value"] == expected_value
        assert work["final_diagnostics"]["boundary_nodes" if middle == "boundary" else "excluded_nodes"] == 1
        assert sum(item["count"] for item in work["final_diagnostics"]["role_state_pairs"]) == expected_edge


def test_single_work_sd_and_all_unavailable_work_denominators():
    labels = [[("missing_or_malformed", "missing_or_malformed")]]
    report = build_descriptive_report(*bundle(units=labels)).private_report
    population = report["populations"][0]
    for key in ("support_to_support_rate", "support_to_proposal_rate", "argumentation_share",
                "thesis_opening_tendency"):
        summary = population["measures"][key]
        assert (summary["total_work_n"], summary["valid_work_n"], summary["unavailable_work_n"]) == (1, 0, 1)
        assert summary["mean"] is None and summary["sample_sd"] is None
        assert sum(summary["unavailable_reason_counts"].values()) == 1
    one_valid = build_descriptive_report(*bundle(units=[[('thesis', 'argumentation')]])).private_report
    assert one_valid["populations"][0]["measures"]["argumentation_share"]["sample_sd"] is None


@pytest.mark.parametrize("name,change,reason", [
    ("cohort", lambda r: r["works"].append(copy.deepcopy(r["works"][0])), "manifest_coverage"),
    ("cohort-work1", lambda r: r.update(source_sha256="0" * 64), "missing_artifact"),
    ("map0", lambda r: r["nodes"][0].update(text_sha256="0" * 64), "candidate_text_hash_mismatch"),
    ("candidate0A", lambda r: r["entries"][0]["role"].update(value="secret-invalid-label"), "candidate_invalid_field_value"),
    ("final0", lambda r: r["entries"].reverse(), "candidate_reordered_entry"),
    ("adjudication0", lambda r: r["decisions"][0].update(role_justification="  "), "invalid_id"),
    ("applicability1", lambda r: r.update(population_id="pilot"), "applicability_link_mismatch"),
])
def test_structural_and_cross_population_substitutions_refuse(name, change, reason):
    def mutate(current, record):
        if current == name:
            change(record)
    with pytest.raises(ReportValidationError) as exc:
        build_descriptive_report(*bundle(empty_population=True, mutate=mutate))
    assert exc.value.reason == reason
    assert "secret-invalid-label" not in str(exc.value)


def test_manifest_population_reordering_refuses():
    def reverse_populations(name, record):
        if name == "manifest":
            record["populations"].reverse()
    with pytest.raises(ReportValidationError, match="population_link_mismatch"):
        build_descriptive_report(*bundle(empty_population=True, mutate=reverse_populations))


def test_two_distinct_runs_may_share_exact_raw_response_bytes():
    manifest, artifacts = bundle(units=[[('support', 'argumentation')]], equal_responses=True)
    runs = [json.loads(raw) for raw in artifacts.values()
            if raw.startswith(b'{"schema":"setec.argument_label_run.v1"')]
    assert len(runs) == 2
    assert runs[0]["run_id"] != runs[1]["run_id"]
    assert runs[0]["raw_response_sha256"] == runs[1]["raw_response_sha256"]
    build_descriptive_report(manifest, artifacts)


def test_duplicate_run_identity_across_works_refuses_after_coherent_relinking():
    def change(name, record):
        if name == "candidate1A":
            record["annotator_run_id"] = "run0A"
        if name == "run1A":
            record["run_id"] = "run0A"
    with pytest.raises(ReportValidationError, match="duplicate_record_identity"):
        build_descriptive_report(*bundle(mutate=change))


@pytest.mark.parametrize("name", ["protocol", "review0", "cohort", "run0A",
                                    "adjudication0", "applicability0", "manifest"])
def test_every_authority_record_has_exact_schema(name):
    def change(current, record):
        if current == name:
            record["schema"] = "wrong"
    with pytest.raises(ReportValidationError, match="unsupported_schema"):
        build_descriptive_report(*bundle(mutate=change))


def test_output_has_exact_public_record_keys_and_json_finite_values():
    result = build_descriptive_report(*bundle())
    report, receipt = result.private_report, result.receipt
    assert set(report) == {"schema", "scope", "manifest_sha256", "cohort_sha256",
                           "protocol_sha256", "artifact_sha256", "independence", "reviewers",
                           "labelers", "works", "populations"}
    assert set(receipt) == {"schema", "scope", "manifest_sha256", "cohort_sha256",
                            "protocol_sha256", "artifact_n", "artifact_sha256", "population_n",
                            "work_n", "measure_work_counts"}
    assert set(report["works"][0]) == {"work_id", "population_id", "source_identity",
        "version_identity", "duplicate_family_id", "source_sha256", "block_map_sha256",
        "source_review_sha256", "final_annotation_sha256", "adjudication_sha256",
        "groups", "final_diagnostics", "final_measures", "labeler_comparison",
        "candidate_diagnostics"}
    assert set(report["populations"][0]) == {"population_id", "applicability_sha256",
        "applicability_disposition", "total_work_n", "measures", "diagnostics", "groups",
        "labeler_comparison"}
    json.dumps(report, allow_nan=False)
    json.dumps(receipt, allow_nan=False)



def test_three_work_group_counts_use_whole_work_grain():
    labels = [[('thesis', 'argumentation')]] * 3
    def groups(name, record):
        if name == "cohort-work0":
            record["groups"] = {"author": {"state": "known", "ids": ["alice", "bob"]},
                "hearing": {"state": "known", "ids": ["h1"]},
                "agency": {"state": "unknown", "ids": []}}
        if name == "cohort-work1":
            record["groups"] = {"author": {"state": "known", "ids": ["bob"]},
                "hearing": {"state": "not_applicable", "ids": []},
                "agency": {"state": "known", "ids": ["ag1", "ag2"]}}
        if name == "cohort-work2":
            record["groups"] = {"author": {"state": "unknown", "ids": []},
                "hearing": {"state": "known", "ids": ["h2"]},
                "agency": {"state": "unknown", "ids": []}}
    report = build_descriptive_report(*bundle(units=labels, mutate=groups)).private_report
    summary = report["populations"][0]["groups"]
    assert summary["author"] == {"distinct_known_id_n": 2, "known_work_n": 2,
                                  "unknown_work_n": 1, "not_applicable_work_n": 0}
    assert summary["hearing"] == {"distinct_known_id_n": 2, "known_work_n": 2,
                                   "unknown_work_n": 0, "not_applicable_work_n": 1}
    assert summary["agency"] == {"distinct_known_id_n": 2, "known_work_n": 1,
                                  "unknown_work_n": 2, "not_applicable_work_n": 0}
