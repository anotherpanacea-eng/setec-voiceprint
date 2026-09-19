"""Pure, declared-evidence descriptive argument report builder.

Hashes establish byte consistency, not source authenticity or label validity.
All returned identifiers and diagnostics belong in private custody except receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import statistics
from typing import Any, Mapping

from setec.core.argument_annotation_contract import ValidationError, validate_candidate_bundle
from argument_feature_schema import MODE_OPTIONS, ROLE_OPTIONS


STATES = ("assigned", "substantive_abstention", "adjudication_pending",
          "missing_or_malformed", "provider_failure")
MEASURES = ("support_to_proposal_rate", "support_to_support_rate",
            "argumentation_share", "thesis_opening_tendency")
REASONS = ("no_eligible_support_successor", "no_assigned_modes",
           "opening_role_unassigned", "applicability_not_permitted")
GROUPS = ("author", "hearing", "agency")
DISPOSITIONS = ("applicable_for_B1_B2", "B2_only", "insufficiently_specified",
                "requires_separately_versioned_instrument")


class ReportValidationError(ValueError):
    """Input refusal with a stable code and optional ordinal, never input text."""

    def __init__(self, reason: str, ordinal: int | None = None):
        self.reason = reason
        self.ordinal = ordinal
        super().__init__(reason if ordinal is None else f"{reason} at ordinal {ordinal}")


@dataclass(frozen=True)
class ReportResult:
    private_report: dict[str, Any]
    receipt: dict[str, Any]


def _fail(reason: str, ordinal: int | None = None) -> None:
    raise ReportValidationError(reason, ordinal)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_field(value: Any) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail("invalid_hash")
    return value


def _id(value: Any) -> str:
    if type(value) is not str or not value.strip():
        _fail("invalid_id")
    return value


def _keys(value: Any, names: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(names.split()):
        _fail("invalid_keys")
    return value


def _list(value: Any) -> list[Any]:
    if type(value) is not list:
        _fail("invalid_list")
    return value


def _enum(value: Any, allowed: tuple[str, ...] | set[str]) -> str:
    if type(value) is not str or value not in allowed:
        _fail("invalid_enum")
    return value


def _time(value: Any) -> datetime:
    if (type(value) is not str or len(value) != 20 or value[4] != "-" or
            value[7] != "-" or value[10] != "T" or value[13] != ":" or
            value[16] != ":" or value[19] != "Z"):
        _fail("invalid_timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("invalid_timestamp")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value: str) -> None:
    _fail("nonfinite_json_number")


def _float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        _fail("nonfinite_json_number")
    return result


def _json(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes:
        _fail("invalid_bytes")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        _fail("invalid_utf8")
    try:
        result = json.loads(text, object_pairs_hook=_pairs, parse_constant=_nonfinite,
                            parse_float=_float)
    except ReportValidationError:
        raise
    except RecursionError:
        _fail("invalid_json_depth")
    except (json.JSONDecodeError, ValueError):
        _fail("invalid_json")
    if type(result) is not dict:
        _fail("invalid_root")
    return result


def _record(data: bytes, schema: str, keys: str) -> dict[str, Any]:
    value = _keys(_json(data), "schema " + keys)
    if value["schema"] != schema:
        _fail("unsupported_schema")
    return value


class _Artifacts:
    def __init__(self, artifacts: Mapping[str, bytes]):
        if not isinstance(artifacts, Mapping):
            _fail("invalid_artifacts")
        # Read each supplied value once. A caller-owned Mapping may mutate or
        # compute different bytes on later reads; all validation and resolution
        # must use the same byte snapshot.
        try:
            self.items = dict(artifacts.items())
        except Exception:
            raise ReportValidationError("invalid_artifacts") from None
        self.used: set[str] = set()
        for key, value in self.items.items():
            _hash_field(key)
            if type(value) is not bytes:
                _fail("invalid_bytes")
            if _hash(value) != key:
                _fail("artifact_hash_mismatch")

    def get(self, key: Any, *, opaque: bool = False) -> bytes:
        key = _hash_field(key)
        if key not in self.items:
            _fail("missing_artifact")
        self.used.add(key)
        value = self.items[key]
        if opaque and not value:
            _fail("empty_supporting_artifact")
        return value

    def finish(self) -> list[str]:
        if set(self.items) != self.used:
            _fail("unused_artifact")
        return sorted(self.used)


def _hashes(value: Any, artifacts: _Artifacts, *, opaque: bool = False) -> list[str]:
    hashes = []
    for item in _list(value):
        key = _hash_field(item)
        artifacts.get(key, opaque=opaque)
        hashes.append(key)
    return hashes


def _candidate(source: bytes, block_map: bytes, candidate: bytes, run_id: str):
    try:
        result = validate_candidate_bundle(source, block_map, candidate)
    except ValidationError as exc:
        raise ReportValidationError("candidate_" + exc.reason, exc.ordinal) from None
    # The helper has already validated this exact JSON object and its annotation schema.
    if _json(candidate)["annotator_run_id"] != run_id:
        _fail("annotator_run_mismatch")
    return result


def _state_counts() -> dict[str, int]:
    return dict.fromkeys(STATES, 0)


def _state_pairs() -> list[dict[str, Any]]:
    return [dict(first_state=a, second_state=b, count=0) for a in STATES for b in STATES]


def _value_pairs(options: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(first_value=a, second_value=b, count=0) for a in options for b in options]


def _bump(pairs: list[dict[str, Any]], first: str, second: str, vocabulary: tuple[str, ...]) -> None:
    pairs[vocabulary.index(first) * len(vocabulary) + vocabulary.index(second)]["count"] += 1


def _diagnostics(result: Any) -> dict[str, Any]:
    units = result.candidate_projection.units
    summary = result.validation_summary
    pairs = _state_pairs()
    successor = _state_counts()
    edge_n = 0
    for first, second in zip(units, units[1:]):
        if first.adjacency_run_id != second.adjacency_run_id:
            continue
        edge_n += 1
        _bump(pairs, first.role_state, second.role_state, STATES)
        if first.role_state == "assigned" and first.role_value == "support" and second.role_state != "assigned":
            successor[second.role_state] += 1
    opening = _state_counts()
    opening[units[0].role_state] = 1
    return dict(total_nodes=summary.total_nodes, prose_nodes=summary.prose_nodes,
                boundary_nodes=summary.boundary_nodes, excluded_nodes=summary.excluded_nodes,
                role_state_counts=dict(summary.role_state_counts),
                mode_state_counts=dict(summary.mode_state_counts), within_run_pair_n=edge_n,
                role_state_pairs=pairs, support_unassigned_successor_counts=successor,
                opening_role_state_counts=opening, first_prose_unit_id=result.candidate_projection.first_prose_unit_id)


def _measure(numerator: int, denominator: int, reason: str, permitted: bool) -> dict[str, Any]:
    if not permitted:
        return dict(numerator=None, denominator=None, value=None,
                    unavailable_reason="applicability_not_permitted")
    return dict(numerator=numerator, denominator=denominator,
                value=numerator / denominator if denominator else None,
                unavailable_reason=None if denominator else reason)


def _measures(result: Any, disposition: str) -> dict[str, dict[str, Any]]:
    units = result.candidate_projection.units
    eligible = proposal = support = 0
    for first, second in zip(units, units[1:]):
        if (first.adjacency_run_id == second.adjacency_run_id and
                first.role_value == "support" and first.role_state == "assigned" and
                second.role_state == "assigned"):
            eligible += 1
            proposal += second.role_value == "proposal"
            support += second.role_value == "support"
    assigned_modes = sum(u.mode_state == "assigned" for u in units)
    argumentation = sum(u.mode_state == "assigned" and u.mode_value == "argumentation" for u in units)
    opening = units[0]
    opening_assigned = opening.role_state == "assigned"
    b1 = disposition == "applicable_for_B1_B2"
    b2 = disposition in ("applicable_for_B1_B2", "B2_only")
    return dict(zip(MEASURES, (
        _measure(proposal, eligible, "no_eligible_support_successor", b1),
        _measure(support, eligible, "no_eligible_support_successor", b1),
        _measure(argumentation, assigned_modes, "no_assigned_modes", b2),
        _measure(int(opening.role_value == "thesis") if opening_assigned else 0,
                 int(opening_assigned), "opening_role_unassigned", b1),
    )))


def _field_comparison(first: Any, second: Any, field: str, options: tuple[str, ...]) -> dict[str, Any]:
    states = _state_pairs()
    values = _value_pairs(options)
    joint = same = 0
    for left, right in zip(first.candidate_projection.units, second.candidate_projection.units):
        left_state, right_state = getattr(left, field + "_state"), getattr(right, field + "_state")
        _bump(states, left_state, right_state, STATES)
        if left_state == right_state == "assigned":
            joint += 1
            left_value, right_value = getattr(left, field + "_value"), getattr(right, field + "_value")
            same += left_value == right_value
            _bump(values, left_value, right_value, options)
    return dict(state_pairs=states, assigned_value_pairs=values, jointly_assigned_n=joint,
                same_assigned_value_n=same, different_assigned_value_n=joint - same,
                conditional_agreement=same / joint if joint else None)


def _comparison(first: Any, second: Any, scope: str) -> dict[str, Any]:
    return dict(scope=scope,
                role=_field_comparison(first, second, "role", ROLE_OPTIONS),
                mode=_field_comparison(first, second, "mode", MODE_OPTIONS))


def _summary(measures: list[dict[str, Any]]) -> dict[str, Any]:
    values = [item["value"] for item in measures if item["value"] is not None]
    reasons = dict.fromkeys(REASONS, 0)
    for item in measures:
        if item["unavailable_reason"] is not None:
            reasons[item["unavailable_reason"]] += 1
    return dict(total_work_n=len(measures), valid_work_n=len(values),
                unavailable_work_n=len(measures) - len(values), unavailable_reason_counts=reasons,
                mean=statistics.mean(values) if values else None,
                min=min(values) if values else None, max=max(values) if values else None,
                sample_sd=statistics.stdev(values) if len(values) >= 2 else None)


def _aggregate_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scalar = ("total_nodes", "prose_nodes", "boundary_nodes", "excluded_nodes", "within_run_pair_n")
    count_fields = ("role_state_counts", "mode_state_counts", "support_unassigned_successor_counts",
                    "opening_role_state_counts")
    result = {key: sum(row[key] for row in rows) for key in scalar}
    for key in count_fields:
        result[key] = {state: sum(row[key][state] for row in rows) for state in STATES}
    result["role_state_pairs"] = _state_pairs()
    for row in rows:
        for target, source in zip(result["role_state_pairs"], row["role_state_pairs"]):
            target["count"] += source["count"]
    return result


def _pooled_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(scope="pooled_unit_counts")
    for field, options in (("role", ROLE_OPTIONS), ("mode", MODE_OPTIONS)):
        target = dict(state_pairs=_state_pairs(), assigned_value_pairs=_value_pairs(options))
        for row in rows:
            source = row[field]
            for key in ("state_pairs", "assigned_value_pairs"):
                for out, item in zip(target[key], source[key]):
                    out["count"] += item["count"]
        joint = sum(row[field]["jointly_assigned_n"] for row in rows)
        same = sum(row[field]["same_assigned_value_n"] for row in rows)
        target.update(jointly_assigned_n=joint, same_assigned_value_n=same,
                      different_assigned_value_n=joint - same,
                      conditional_agreement=same / joint if joint else None)
        result[field] = target
    return result


def _group_summary(works: list[dict[str, Any]], dimension: str) -> dict[str, int]:
    ids: set[str] = set()
    states = {"known": 0, "unknown": 0, "not_applicable": 0}
    for work in works:
        group = work["groups"][dimension]
        states[group["state"]] += 1
        ids.update(group["ids"])
    return dict(distinct_known_id_n=len(ids), known_work_n=states["known"],
                unknown_work_n=states["unknown"], not_applicable_work_n=states["not_applicable"])


def build_descriptive_report(manifest_bytes: bytes, artifacts: Mapping[str, bytes]) -> ReportResult:
    """Validate a complete declared graph and calculate final-only descriptive summaries.

    Raises ReportValidationError on any malformed/inconsistent input. All caller
    supplied IDs and prose remain confined to private_report, never receipt or errors.
    """
    manifest = _record(manifest_bytes, "setec.argument_descriptive_report_input.v1",
                       "cohort_sha256 protocol_sha256 works populations")
    store = _Artifacts(artifacts)
    cohort_hash, protocol_hash = _hash_field(manifest["cohort_sha256"]), _hash_field(manifest["protocol_sha256"])
    cohort = _record(store.get(cohort_hash), "setec.argument_report_cohort.v1",
                     "cohort_id frozen_at populations works")
    protocol = _record(store.get(protocol_hash), "setec.argument_report_protocol.v1",
                       "protocol_id frozen_at rubric_sha256 adjudication_rule_sha256 applicability_form_sha256 adjudicator_id applicability_reviewer_id labelers independence_basis independence_note")
    _id(cohort["cohort_id"]); _id(protocol["protocol_id"])
    cohort_time, protocol_time = _time(cohort["frozen_at"]), _time(protocol["frozen_at"])
    for key in ("rubric_sha256", "adjudication_rule_sha256", "applicability_form_sha256"):
        store.get(protocol[key], opaque=True)
    _id(protocol["adjudicator_id"]); _id(protocol["applicability_reviewer_id"])
    _enum(protocol["independence_basis"], ("separate_reviewers", "distinct_model_families",
                                           "same_model_repeatability", "other_declared"))
    _id(protocol["independence_note"])
    labelers = _list(protocol["labelers"])
    if len(labelers) != 2:
        _fail("invalid_labelers")
    labeler_ids = []
    for labeler in labelers:
        _keys(labeler, "labeler_id identity_sha256 settings_sha256 prompt_template_sha256 parser_identity_sha256")
        labeler_ids.append(_id(labeler["labeler_id"]))
        for key in ("identity_sha256", "settings_sha256", "prompt_template_sha256", "parser_identity_sha256"):
            store.get(labeler[key], opaque=True)
    if len(set(labeler_ids)) != 2:
        _fail("duplicate_labeler")
    populations = [_id(item) for item in _list(cohort["populations"])]
    if not populations or len(set(populations)) != len(populations):
        _fail("invalid_populations")
    cohort_works = _list(cohort["works"])
    manifest_works, manifest_populations = _list(manifest["works"]), _list(manifest["populations"])
    if len(manifest_works) != len(cohort_works) or len(manifest_populations) != len(populations):
        _fail("manifest_coverage")
    seen = {"work_id": set(), "duplicate_family_id": set(), "source_sha256": set(),
            "source_version": set()}
    validated_works = []
    for ordinal, (work, link) in enumerate(zip(cohort_works, manifest_works)):
        try:
            _keys(work, "work_id population_id source_identity version_identity duplicate_family_id source_sha256 block_map_sha256 source_review_sha256 groups")
            _keys(link, "work_id adjudication_sha256")
            for key in ("work_id", "population_id", "source_identity", "version_identity", "duplicate_family_id"):
                _id(work[key])
            if work["population_id"] not in populations or link["work_id"] != work["work_id"]:
                _fail("work_link_mismatch")
            _hash_field(work["source_sha256"]); _hash_field(work["block_map_sha256"])
            identities = {"work_id": work["work_id"], "duplicate_family_id": work["duplicate_family_id"],
                          "source_sha256": work["source_sha256"],
                          "source_version": (work["source_identity"], work["version_identity"])}
            for key, value in identities.items():
                if value in seen[key]:
                    _fail("duplicate_work_identity")
                seen[key].add(value)
            groups = _keys(work["groups"], "author hearing agency")
            for dimension in GROUPS:
                group = _keys(groups[dimension], "state ids")
                state = _enum(group["state"], ("known", "unknown", "not_applicable"))
                ids = [_id(item) for item in _list(group["ids"])]
                if (state == "known" and (not ids or len(set(ids)) != len(ids))) or (state != "known" and ids):
                    _fail("invalid_group")
            source = store.get(work["source_sha256"])
            block_map = store.get(work["block_map_sha256"])
            review = _record(store.get(work["source_review_sha256"]), "setec.argument_source_review.v1",
                             "work_id source_sha256 block_map_sha256 reviewer_id reviewed_at source_disposition map_disposition evidence_sha256")
            _id(review["reviewer_id"])
            if (review["work_id"] != work["work_id"] or review["source_sha256"] != work["source_sha256"]
                    or review["block_map_sha256"] != work["block_map_sha256"]):
                _fail("source_review_link_mismatch")
            if _time(review["reviewed_at"]) > cohort_time:
                _fail("source_review_after_freeze")
            _enum(review["source_disposition"], ("admitted_for_declared_pilot",))
            _enum(review["map_disposition"], ("reviewed_whole_work_map",))
            store.get(review["evidence_sha256"], opaque=True)
            adjudication = _record(store.get(link["adjudication_sha256"]), "setec.argument_adjudication.v1",
                                   "adjudication_id work_id cohort_sha256 protocol_sha256 source_sha256 block_map_sha256 adjudicator_id completed_at disposition label_run_sha256 final_annotation_sha256 decisions")
            _id(adjudication["adjudication_id"])
            if any(adjudication[key] != expected for key, expected in (
                ("work_id", work["work_id"]), ("cohort_sha256", cohort_hash),
                ("protocol_sha256", protocol_hash), ("source_sha256", work["source_sha256"]),
                ("block_map_sha256", work["block_map_sha256"]),
                ("adjudicator_id", protocol["adjudicator_id"]))):
                _fail("adjudication_link_mismatch")
            _enum(adjudication["disposition"], ("completed_with_unresolved_preserved",))
            adjudication_time = _time(adjudication["completed_at"])
            run_hashes = _hashes(adjudication["label_run_sha256"], store)
            if len(run_hashes) != 2 or len(set(run_hashes)) != 2:
                _fail("invalid_label_runs")
            candidates = []
            runs = []
            for labeler, run_hash in zip(labelers, run_hashes):
                run = _record(store.get(run_hash), "setec.argument_label_run.v1",
                              "run_id work_id cohort_sha256 protocol_sha256 labeler_id context_checked_at started_at completed_at source_sha256 block_map_sha256 candidate_sha256 rendered_prompt_sha256 raw_response_sha256 parse_diagnostics_sha256 parser_identity_sha256 context_evidence_sha256")
                _id(run["run_id"])
                if any(run[key] != expected for key, expected in (
                    ("work_id", work["work_id"]), ("cohort_sha256", cohort_hash),
                    ("protocol_sha256", protocol_hash), ("labeler_id", labeler["labeler_id"]),
                    ("source_sha256", work["source_sha256"]), ("block_map_sha256", work["block_map_sha256"]),
                    ("parser_identity_sha256", labeler["parser_identity_sha256"]))):
                    _fail("run_link_mismatch")
                if not (max(cohort_time, protocol_time) <= _time(run["context_checked_at"])
                        <= _time(run["started_at"]) <= _time(run["completed_at"]) <= adjudication_time):
                    _fail("run_time_order")
                for key in ("rendered_prompt_sha256", "raw_response_sha256", "parse_diagnostics_sha256",
                            "parser_identity_sha256", "context_evidence_sha256"):
                    store.get(run[key], opaque=True)
                candidates.append(_candidate(source, block_map, store.get(run["candidate_sha256"]), run["run_id"]))
                runs.append(run)
            if runs[0]["run_id"] == runs[1]["run_id"]:
                _fail("duplicate_run_id")
            final = _candidate(source, block_map, store.get(adjudication["final_annotation_sha256"]),
                               adjudication["adjudication_id"])
            decisions = _list(adjudication["decisions"])
            if len(decisions) != len(final.candidate_projection.units):
                _fail("decision_coverage")
            for decision, unit in zip(decisions, final.candidate_projection.units):
                _keys(decision, "unit_id role_justification mode_justification")
                if decision["unit_id"] != unit.unit_id:
                    _fail("decision_unit_mismatch")
                _id(decision["role_justification"]); _id(decision["mode_justification"])
            validated_works.append((work, link, adjudication, adjudication_time, runs, run_hashes,
                                    candidates, final))
        except ReportValidationError as exc:
            if exc.ordinal is None:
                raise ReportValidationError(exc.reason, ordinal) from None
            raise
    run_ids = [run["run_id"] for _, _, _, _, runs, _, _, _ in validated_works for run in runs]
    adjudication_ids = [adj["adjudication_id"] for _, _, adj, _, _, _, _, _ in validated_works]
    if len(set(run_ids)) != len(run_ids) or len(set(adjudication_ids)) != len(adjudication_ids):
        _fail("duplicate_record_identity")
    applicability = {}
    for ordinal, (population_id, link) in enumerate(zip(populations, manifest_populations)):
        try:
            _keys(link, "population_id applicability_sha256")
            if link["population_id"] != population_id:
                _fail("population_link_mismatch")
            record = _record(store.get(link["applicability_sha256"]), "setec.argument_applicability.v1",
                             "population_id cohort_sha256 protocol_sha256 reviewer_id completed_at disposition reasons adjudication_sha256")
            if any(record[key] != expected for key, expected in (
                ("population_id", population_id), ("cohort_sha256", cohort_hash),
                ("protocol_sha256", protocol_hash), ("reviewer_id", protocol["applicability_reviewer_id"]))):
                _fail("applicability_link_mismatch")
            _id(record["reasons"])
            _enum(record["disposition"], DISPOSITIONS)
            expected_hashes = [link["adjudication_sha256"] for work, link, *_ in validated_works
                               if work["population_id"] == population_id]
            actual_hashes = _hashes(record["adjudication_sha256"], store)
            if actual_hashes != expected_hashes:
                _fail("applicability_coverage")
            latest = max((time for work, _, _, time, *_ in validated_works
                          if work["population_id"] == population_id),
                         default=max(cohort_time, protocol_time))
            if _time(record["completed_at"]) < max(cohort_time, protocol_time, latest):
                _fail("applicability_time_order")
            applicability[population_id] = (link, record)
        except ReportValidationError as exc:
            if exc.ordinal is None:
                raise ReportValidationError(exc.reason, ordinal) from None
            raise
    artifact_hashes = store.finish()
    works = []
    for work, link, _adj, _time_completed, runs, run_hashes, candidates, final in validated_works:
        disposition = applicability[work["population_id"]][1]["disposition"]
        diagnostics = _diagnostics(final)
        measures = _measures(final, disposition)
        candidate_diagnostics = []
        for labeler, run, run_hash, candidate in zip(labelers, runs, run_hashes, candidates):
            candidate_measures = _measures(candidate, disposition)
            differences = {key: (measures[key]["value"] - candidate_measures[key]["value"]
                                 if measures[key]["value"] is not None and
                                 candidate_measures[key]["value"] is not None else None)
                           for key in MEASURES}
            candidate_diagnostics.append(dict(labeler_id=labeler["labeler_id"], run_id=run["run_id"],
                                              label_run_sha256=run_hash, candidate_sha256=run["candidate_sha256"],
                                              measures=candidate_measures, final_minus_candidate=differences))
        works.append(dict(work_id=work["work_id"], population_id=work["population_id"],
                          source_identity=work["source_identity"], version_identity=work["version_identity"],
                          duplicate_family_id=work["duplicate_family_id"], source_sha256=work["source_sha256"],
                          block_map_sha256=work["block_map_sha256"], source_review_sha256=work["source_review_sha256"],
                          final_annotation_sha256=_adj["final_annotation_sha256"],
                          adjudication_sha256=link["adjudication_sha256"], groups=work["groups"],
                          final_diagnostics=diagnostics, final_measures=measures,
                          labeler_comparison=_comparison(*candidates, "one_work_units"),
                          candidate_diagnostics=candidate_diagnostics))
    population_reports = []
    for population_id in populations:
        link, record = applicability[population_id]
        subset = [work for work in works if work["population_id"] == population_id]
        population_reports.append(dict(population_id=population_id,
                                       applicability_sha256=link["applicability_sha256"],
                                       applicability_disposition=record["disposition"], total_work_n=len(subset),
                                       measures={key: _summary([work["final_measures"][key] for work in subset])
                                                 for key in MEASURES},
                                       diagnostics=_aggregate_diagnostics([work["final_diagnostics"] for work in subset]),
                                       groups={key: _group_summary(subset, key) for key in GROUPS},
                                       labeler_comparison=_pooled_comparison([work["labeler_comparison"] for work in subset])))
    report = dict(schema="setec.argument_descriptive_report.v1",
                  scope="declared_adjudicated_whole_work_descriptive", manifest_sha256=_hash(manifest_bytes),
                  cohort_sha256=cohort_hash, protocol_sha256=protocol_hash, artifact_sha256=artifact_hashes,
                  independence=dict(basis=protocol["independence_basis"], note=protocol["independence_note"]),
                  reviewers=dict(adjudicator_id=protocol["adjudicator_id"],
                                 applicability_reviewer_id=protocol["applicability_reviewer_id"]),
                  labelers=labelers, works=works, populations=population_reports)
    receipt = dict(schema="setec.argument_descriptive_receipt.v1",
                   scope="hash_schema_consistency_and_declared_descriptive_calculation",
                   manifest_sha256=_hash(manifest_bytes), cohort_sha256=cohort_hash,
                   protocol_sha256=protocol_hash, artifact_n=len(artifact_hashes),
                   artifact_sha256=artifact_hashes.copy(), population_n=len(populations), work_n=len(works),
                   measure_work_counts={key: {
                       "valid_work_n": sum(p["measures"][key]["valid_work_n"] for p in population_reports),
                       "unavailable_work_n": sum(p["measures"][key]["unavailable_work_n"] for p in population_reports)}
                       for key in MEASURES})
    return ReportResult(report, receipt)
