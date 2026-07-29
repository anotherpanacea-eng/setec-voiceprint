"""Synthetic Spec-80 authority-package transaction layer.

This module packages an already live-derived and admitted remediation
projection.  It is deliberately *not* the final
``passage_consumer_authority.py`` CLI: it does not read descriptors, admit an
authority profile, rerun passage detection, validate policy attestations, or
mint from real private corpus inputs.  Those remain separate Spec-80 build
slices.

The narrow responsibility here is deterministic three-file construction and
create-new publication: projected Spec-75 manifest, closed authority artifact,
then the receipt commit marker.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat
from typing import Any, Callable, Mapping, Sequence

import reconstructibility_probe_set as spec75
import passage_lineage_crosswalk as lineage
from passage_remediation import (
    PinnedPrivateRoot,
    _OutputExists,
    _OversizePrivateIOError,
    _PrivateIOError,
    _PublicationError,
    _RecoveryRequired,
    _RootPolicyError,
)
from passage_remediation_projection import RemediationProjection, _semantic_sha


ARTIFACT_SCHEMA = "setec-passage-consumer-authority-artifact/1"
RECEIPT_SCHEMA = "setec-passage-consumer-authority-receipt/1"
SUCCESS_SCHEMA = "setec-passage-consumer-authority-success/1"
SYNTHETIC_PACKAGE_ENV = "SETEC_SPEC80_SYNTHETIC_PACKAGE_TEST_ONLY"

MAX_PROJECTED_MANIFEST_BYTES = 268_435_456
MAX_ARTIFACT_BYTES = 1_073_741_824
MAX_RECEIPT_BYTES = 1_048_576

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_INPUT_HASH_KEYS = {
    "inventory",
    "increment1_descriptor",
    "increment1_projection_receipt",
    "increment1_artifact",
    "increment1_receipt",
    "source_population_commitment",
    "source_population_receipt",
    "original_manifest",
    "author_corpus_export_receipt",
    "snapshot_manifest",
    "snapshot_metadata",
    "source_lineage_crosswalk",
    "snapshot_attestation",
    "consumer_policy_attestation",
}
_PROFILE_KEYS = {
    "schema",
    "producer_revision",
    "producer_script_git_blob_oid",
    "producer_script_sha256",
    "algorithm_commitment_sha256",
    "tokenizer_implementation_git_blob_oid",
    "tokenizer_implementation_sha256",
    "tokenizer_data_git_blob_oid",
    "tokenizer_data_sha256",
    "tokenizer_data_commitment_sha256",
    "authority_builder_git_blob_oid",
    "authority_builder_script_sha256",
    "verifier_git_blob_oid",
    "verifier_implementation_sha256",
    "profile_commitment_sha256",
}
_SNAPSHOT_BINDING_KEYS = {
    "authoritative_training_snapshot",
    "population_manifest_sha256",
    "membership_projection_sha256",
    "grouping_projection_sha256",
}
_POLICY_BINDING_KEYS = {
    "spec_sha256",
    "review_sha256",
    "consumer_policy_attestation_sha256",
}
_ARTIFACT_KEYS = {
    "schema",
    "status",
    "claim_license",
    "input_hashes",
    "authority_profile",
    "snapshot_binding",
    "projected_population_manifest_sha256",
    "coverage",
    "policy_binding",
    "passages",
    "evidence",
    "units",
    "pair_exclusion_projection",
    "unresolved",
}
_RECEIPT_KEYS = {
    "schema",
    "result",
    "artifact_sha256",
    "source_population_commitment_sha256",
    "lineage_crosswalk_artifact_sha256",
    "lineage_crosswalk_commitment_sha256",
    "original_population_manifest_sha256",
    "projected_population_manifest_sha256",
    "projected_membership_projection_sha256",
    "projected_grouping_projection_sha256",
    "input_snapshot_attestation_sha256",
    "consumer_policy_attestation_sha256",
    "authority_profile_artifact_sha256",
    "authority_profile_commitment_sha256",
    "spec_sha256",
    "review_sha256",
    "consumer_authority",
    "claim_license",
    "training_authorized",
    "activation_authorized",
    "eligibility_attested",
}
_UNIT_KEYS = {
    "unit_id",
    "content_sha256",
    "evaluation_partition",
    "register",
    "source_kind",
    "source_group",
    "document_family",
    "input_duplicate_component",
    "input_loss_mask_intervals",
    "projected_duplicate_component",
    "projected_loss_mask_intervals",
    "pairing_excluded",
    "evidence_refs",
    "projection_refs",
}
_PASSAGE_KEYS = {
    "source_doc_id",
    "passage_id",
    "char_start",
    "char_end",
    "n_words",
    "raw_text_sha256",
    "partition",
    "disposition",
}
_STAGE_A_EVIDENCE_KEYS = {
    "kind",
    "evidence_id",
    "cluster_index",
    "passage_id",
    "source_doc_id",
    "char_start",
    "char_end",
    "n_words",
    "raw_text_sha256",
    "disposition",
    "unit_refs",
}
_STAGE_B_EVIDENCE_KEYS = {
    "kind",
    "evidence_id",
    "repeated_span_index",
    "occurrence_index",
    "occurrence_span_id",
    "source_doc_id",
    "token_start",
    "token_end",
    "char_start",
    "char_end",
    "n_words",
    "normalized_span_sha256",
    "raw_text_sha256",
    "unit_refs",
}
_CROSSWALK_ROW_KEYS = {
    "projection_index",
    "projection_id",
    "lineage_slice_id",
    "source_doc_id",
    "source_entry_fingerprint",
    "source_content_sha256",
    "source_char_start",
    "source_char_end",
    "source_slice_sha256",
    "unit_id",
    "unit_char_start",
    "unit_char_end",
    "projection",
}
_COVERAGE = {
    "stage_a": "exact_itemized_and_noncluster_partition",
    "stage_b": "all_itemized_occurrences_masked",
    "below_floor": "unresolved",
    "edited_repetition": "unresolved",
    "calibration_status": "operational_uncalibrated",
}
_UNRESOLVED = {
    "below_floor": True,
    "edited_repetition": True,
    "eligibility": True,
    "training_authorization": True,
    "activation_authorization": True,
    "pair_consumer_implementation": True,
    "tokenizer_projection": True,
}

_ERROR_CODES = {
    "derivation_invariant_refused",
    "output_recovery_required",
    "projected_manifest_publication_refused",
    "artifact_publication_refused",
    "receipt_publication_refused",
}


class PackageTransactionError(Exception):
    def __init__(self, code: str):
        if code not in _ERROR_CODES:
            raise ValueError("unknown package transaction error")
        super().__init__(code)
        self.code = code


_SYNTHETIC_SENTINEL = object()


class _SyntheticPackageCapability:
    """Unforgeable-by-construction marker for this synthetic test seam."""

    __slots__ = ("_sentinel",)

    def __init__(self, sentinel: object) -> None:
        self._sentinel = sentinel


def synthetic_package_capability() -> _SyntheticPackageCapability:
    """Return the synthetic-only capability under the explicit test guard."""
    if os.environ.get(SYNTHETIC_PACKAGE_ENV) != "1":
        raise PackageTransactionError("derivation_invariant_refused")
    return _SyntheticPackageCapability(_SYNTHETIC_SENTINEL)


def _require_synthetic_capability(
    capability: _SyntheticPackageCapability | None,
) -> None:
    if (
        type(capability) is not _SyntheticPackageCapability
        or capability._sentinel is not _SYNTHETIC_SENTINEL
        or os.environ.get(SYNTHETIC_PACKAGE_ENV) != "1"
    ):
        raise PackageTransactionError("derivation_invariant_refused")


@dataclass(frozen=True)
class PackageBindings:
    """Previously admitted values consumed by this thin packaging layer."""

    input_hashes: Mapping[str, str]
    source_lineage_crosswalk_bytes: bytes
    authority_profile: Mapping[str, Any]
    authority_profile_artifact_sha256: str
    snapshot_binding: Mapping[str, str]
    policy_binding: Mapping[str, str]
    source_population_commitment_sha256: str
    lineage_crosswalk_commitment_sha256: str


@dataclass(frozen=True)
class AuthorityPackage:
    projected_manifest_bytes: bytes
    artifact_bytes: bytes
    receipt_bytes: bytes
    projected_population_manifest_sha256: str
    projected_membership_projection_sha256: str
    projected_grouping_projection_sha256: str
    artifact_sha256: str
    receipt_sha256: str
    passage_rows: int
    evidence_rows: int
    unit_rows: int


@dataclass(frozen=True)
class PackageNames:
    projected_manifest: str
    artifact: str
    receipt: str


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise PackageTransactionError("derivation_invariant_refused") from exc


def _digest(value: Any) -> bool:
    return type(value) is str and bool(_DIGEST.fullmatch(value))


def _closed(value: Any, keys: set[str]) -> bool:
    return type(value) is dict and set(value) == keys


def _int(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _ordered_interval(start: Any, end: Any, *, inclusive: bool = False) -> bool:
    return (
        _int(start)
        and _int(end)
        and (start <= end if inclusive else start < end)
    )


def _sorted_unique_digests(values: Any) -> bool:
    return (
        type(values) is list
        and all(_digest(value) for value in values)
        and values == sorted(set(values), key=lambda value: value.encode())
    )


def _validate_bindings(bindings: PackageBindings) -> list[dict[str, Any]]:
    input_hashes = dict(bindings.input_hashes)
    profile = dict(bindings.authority_profile)
    snapshot = dict(bindings.snapshot_binding)
    policy = dict(bindings.policy_binding)
    profile_digests = {
        "producer_script_sha256",
        "algorithm_commitment_sha256",
        "tokenizer_implementation_sha256",
        "tokenizer_data_sha256",
        "tokenizer_data_commitment_sha256",
        "authority_builder_script_sha256",
        "verifier_implementation_sha256",
        "profile_commitment_sha256",
    }
    blob_fields = {
        "producer_script_git_blob_oid",
        "tokenizer_implementation_git_blob_oid",
        "tokenizer_data_git_blob_oid",
        "authority_builder_git_blob_oid",
        "verifier_git_blob_oid",
    }
    if (
        set(input_hashes) != _INPUT_HASH_KEYS
        or not all(_digest(value) for value in input_hashes.values())
        or not _closed(profile, _PROFILE_KEYS)
        or profile["schema"] != "setec-passage-authority-profile/1"
        or type(profile["producer_revision"]) is not str
        or not _HEX40.fullmatch(profile["producer_revision"])
        or not all(_digest(profile[field]) for field in profile_digests)
        or not all(
            type(profile[field]) is str
            and profile[field].startswith("sha1:")
            and _HEX40.fullmatch(profile[field][5:])
            for field in blob_fields
        )
        or not _digest(bindings.authority_profile_artifact_sha256)
        or not _closed(snapshot, _SNAPSHOT_BINDING_KEYS)
        or not all(_digest(value) for value in snapshot.values())
        or snapshot["population_manifest_sha256"]
        != input_hashes["snapshot_manifest"]
        or not _closed(policy, _POLICY_BINDING_KEYS)
        or not all(_digest(value) for value in policy.values())
        or policy["consumer_policy_attestation_sha256"]
        != input_hashes["consumer_policy_attestation"]
        or not _digest(bindings.source_population_commitment_sha256)
        or not _digest(bindings.lineage_crosswalk_commitment_sha256)
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    crosswalk_raw = bindings.source_lineage_crosswalk_bytes
    if (
        type(crosswalk_raw) is not bytes
        or not crosswalk_raw
        or _sha(crosswalk_raw) != input_hashes["source_lineage_crosswalk"]
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    try:
        crosswalk = json.loads(crosswalk_raw)
        if _canonical(crosswalk) != crosswalk_raw:
            raise PackageTransactionError("derivation_invariant_refused")
        lineage.validate_crosswalk_shape(crosswalk)
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        lineage.LineageError,
        TypeError,
        ValueError,
    ) as exc:
        raise PackageTransactionError("derivation_invariant_refused") from exc
    if (
        crosswalk["crosswalk_commitment_sha256"]
        != bindings.lineage_crosswalk_commitment_sha256
        or crosswalk["source_population_commitment_sha256"]
        != bindings.source_population_commitment_sha256
        or crosswalk["population_manifest_sha256"]
        != snapshot["population_manifest_sha256"]
        or crosswalk["author_corpus_export_receipt_sha256"]
        != input_hashes["author_corpus_export_receipt"]
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    return crosswalk["rows"]


def _manifest_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical(dict(row)) for row in rows)


def _validate_projection(
    projection: RemediationProjection,
    expected_crosswalk: list[dict[str, Any]],
    inventory_sha256: str,
) -> tuple[bytes, str, str, str, str, str, str]:
    rows = projection.projected_population_rows
    if type(rows) is not list or not rows:
        raise PackageTransactionError("derivation_invariant_refused")
    try:
        spec75.validate_population(rows)
    except (spec75.ProbeSetError, TypeError, ValueError, KeyError) as exc:
        raise PackageTransactionError("derivation_invariant_refused") from exc

    manifest_raw = _manifest_bytes(rows)
    if not manifest_raw or len(manifest_raw) > MAX_PROJECTED_MANIFEST_BYTES:
        raise PackageTransactionError("derivation_invariant_refused")
    try:
        reparsed = spec75.strict_jsonl_v1(manifest_raw)
        spec75.validate_population(reparsed)
    except (spec75.ProbeSetError, TypeError, ValueError, KeyError) as exc:
        raise PackageTransactionError("derivation_invariant_refused") from exc
    if reparsed != rows:
        raise PackageTransactionError("derivation_invariant_refused")

    by_row = {row["unit_id"]: row for row in rows}
    if len(by_row) != len(rows):
        raise PackageTransactionError("derivation_invariant_refused")
    units = projection.units
    if (
        type(units) is not list
        or len(units) != len(rows)
        or any(not _closed(row, _UNIT_KEYS) for row in units)
        or units != sorted(units, key=lambda row: row["unit_id"].encode())
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    by_unit = {row["unit_id"]: row for row in units}
    if set(by_unit) != set(by_row) or len(by_unit) != len(units):
        raise PackageTransactionError("derivation_invariant_refused")

    crosswalk = projection.crosswalk_projection
    if (
        type(crosswalk) is not list
        or not crosswalk
        or crosswalk != expected_crosswalk
        or any(not _closed(row, _CROSSWALK_ROW_KEYS) for row in crosswalk)
        or [row["projection_index"] for row in crosswalk]
        != list(range(len(crosswalk)))
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    expected_projection_refs: dict[str, list[str]] = {
        unit_id: [] for unit_id in by_unit
    }
    seen_projection_ids: set[str] = set()
    seen_projection_rows: set[bytes] = set()
    for row in crosswalk:
        row_bytes = _canonical(row)
        if (
            not _digest(row["projection_id"])
            or row["projection_id"] in seen_projection_ids
            or row_bytes in seen_projection_rows
            or not _digest(row["lineage_slice_id"])
            or not _digest(row["source_doc_id"])
            or not _digest(row["source_entry_fingerprint"])
            or not _digest(row["source_content_sha256"])
            or not _ordered_interval(
                row["source_char_start"], row["source_char_end"],
            )
            or not _digest(row["source_slice_sha256"])
            or row["unit_id"] not in by_unit
            or not _ordered_interval(
                row["unit_char_start"], row["unit_char_end"],
            )
            or row["projection"] != "identity_text"
        ):
            raise PackageTransactionError("derivation_invariant_refused")
        seen_projection_ids.add(row["projection_id"])
        seen_projection_rows.add(row_bytes)
        expected_projection_refs[row["unit_id"]].append(row["projection_id"])
    for refs in expected_projection_refs.values():
        refs.sort(key=lambda value: value.encode())

    changed = False
    for unit_id, unit in by_unit.items():
        row = by_row[unit_id]
        if (
            unit["content_sha256"] != row["content_sha256"]
            or unit["evaluation_partition"] != row["evaluation_partition"]
            or unit["source_group"] != row["source_group"]
            or unit["document_family"] != row["document_family"]
            or unit["projected_duplicate_component"]
            != row["duplicate_component"]
            or unit["projected_loss_mask_intervals"]
            != row["loss_mask_intervals"]
            or type(unit["pairing_excluded"]) is not bool
            or not _sorted_unique_digests(unit["evidence_refs"])
            or not _sorted_unique_digests(unit["projection_refs"])
            or unit["projection_refs"] != expected_projection_refs[unit_id]
        ):
            raise PackageTransactionError("derivation_invariant_refused")
        if (
            unit["input_duplicate_component"]
            != unit["projected_duplicate_component"]
            or unit["input_loss_mask_intervals"]
            != unit["projected_loss_mask_intervals"]
        ):
            changed = True

    passages = projection.passages
    if (
        type(passages) is not list
        or any(not _closed(row, _PASSAGE_KEYS) for row in passages)
        or any(
            not _digest(row["source_doc_id"])
            or type(row["passage_id"]) is not str
            or not row["passage_id"]
            or not _ordered_interval(row["char_start"], row["char_end"])
            or not _int(row["n_words"], minimum=1)
            or not _digest(row["raw_text_sha256"])
            or (row["partition"], row["disposition"])
            not in {
                ("cluster_member", "representative"),
                ("cluster_member", "nonrepresentative"),
                ("noncluster", "not_assessed"),
            }
            for row in passages
        )
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    if (
        len({row["passage_id"] for row in passages}) != len(passages)
        or passages != sorted(
            passages,
            key=lambda row: (
                row["source_doc_id"],
                row["char_start"],
                row["char_end"],
                row["passage_id"],
            ),
        )
    ):
        raise PackageTransactionError("derivation_invariant_refused")

    evidence = projection.evidence
    if type(evidence) is not list:
        raise PackageTransactionError("derivation_invariant_refused")
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for row in evidence:
        keys = (
            _STAGE_A_EVIDENCE_KEYS
            if type(row) is dict and row.get("kind") == "stage_a_nonrepresentative"
            else _STAGE_B_EVIDENCE_KEYS
            if type(row) is dict and row.get("kind") == "stage_b_occurrence"
            else set()
        )
        if (
            not keys
            or not _closed(row, keys)
            or not _digest(row["evidence_id"])
            or row["evidence_id"] in evidence_by_id
            or not _sorted_unique_digests(row["unit_refs"])
            or any(unit_id not in by_unit for unit_id in row["unit_refs"])
        ):
            raise PackageTransactionError("derivation_invariant_refused")
        if row["kind"] == "stage_a_nonrepresentative":
            scalar_valid = (
                _int(row["cluster_index"])
                and type(row["passage_id"]) is str
                and bool(row["passage_id"])
                and _digest(row["source_doc_id"])
                and _ordered_interval(row["char_start"], row["char_end"])
                and _int(row["n_words"], minimum=1)
                and _digest(row["raw_text_sha256"])
                and row["disposition"] == "nonrepresentative"
            )
        else:
            scalar_valid = (
                _int(row["repeated_span_index"])
                and _int(row["occurrence_index"])
                and type(row["occurrence_span_id"]) is str
                and bool(row["occurrence_span_id"])
                and _digest(row["source_doc_id"])
                and _ordered_interval(
                    row["token_start"], row["token_end"], inclusive=True,
                )
                and _ordered_interval(row["char_start"], row["char_end"])
                and _int(row["n_words"], minimum=1)
                and _digest(row["normalized_span_sha256"])
                and _digest(row["raw_text_sha256"])
            )
        if not scalar_valid:
            raise PackageTransactionError("derivation_invariant_refused")
        if row["kind"] == "stage_a_nonrepresentative":
            expected_evidence_id = _semantic_sha(
                b"setec-passage-stage-a-evidence-id-v1\n",
                {
                    "inventory_sha256": inventory_sha256,
                    "cluster_index": row["cluster_index"],
                    "passage_id": row["passage_id"],
                    "source_doc_id": row["source_doc_id"],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                    "passage_sha256": row["raw_text_sha256"],
                    "disposition": row["disposition"],
                },
            )
        else:
            expected_evidence_id = _semantic_sha(
                b"setec-passage-stage-b-evidence-id-v1\n",
                {
                    "inventory_sha256": inventory_sha256,
                    "repeated_span_index": row["repeated_span_index"],
                    "span_sha256": row["normalized_span_sha256"],
                    "occurrence_index": row["occurrence_index"],
                    "occurrence_span_id": row["occurrence_span_id"],
                    "source_doc_id": row["source_doc_id"],
                    "token_start": row["token_start"],
                    "token_end": row["token_end"],
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                    "n_words": row["n_words"],
                    "normalized_span_sha256":
                        row["normalized_span_sha256"],
                    "raw_text_sha256": row["raw_text_sha256"],
                },
            )
        if row["evidence_id"] != expected_evidence_id:
            raise PackageTransactionError("derivation_invariant_refused")
        evidence_by_id[row["evidence_id"]] = row
    if evidence != sorted(
        evidence,
        key=lambda row: (
            row["kind"],
            row["source_doc_id"],
            row["char_start"],
            row["char_end"],
            row["evidence_id"],
        ),
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    if any(
        (unit_id in evidence_row["unit_refs"])
        != (evidence_id in by_unit[unit_id]["evidence_refs"])
        for evidence_id, evidence_row in evidence_by_id.items()
        for unit_id in by_unit
    ) or any(
        evidence_id not in evidence_by_id
        for unit in units
        for evidence_id in unit["evidence_refs"]
    ):
        raise PackageTransactionError("derivation_invariant_refused")

    exclusions = projection.pair_exclusion_projection
    if (
        type(exclusions) is not list
        or len(exclusions) != len(rows)
        or exclusions
        != [
            {
                "unit_id": unit_id,
                "pairing_excluded": by_unit[unit_id]["pairing_excluded"],
            }
            for unit_id in sorted(by_unit, key=lambda value: value.encode())
        ]
        or any(
            row["pairing_excluded"]
            != bool(by_unit[row["unit_id"]]["evidence_refs"])
            for row in exclusions
        )
    ):
        raise PackageTransactionError("derivation_invariant_refused")

    if not changed:
        raise PackageTransactionError("derivation_invariant_refused")

    original_rows = []
    for row in rows:
        original = dict(row)
        original["duplicate_component"] = by_unit[
            row["unit_id"]
        ]["input_duplicate_component"]
        original["loss_mask_intervals"] = by_unit[
            row["unit_id"]
        ]["input_loss_mask_intervals"]
        original_rows.append(original)
    try:
        spec75.validate_population(original_rows)
    except (spec75.ProbeSetError, TypeError, ValueError, KeyError) as exc:
        raise PackageTransactionError("derivation_invariant_refused") from exc
    original_raw = _manifest_bytes(original_rows)

    return (
        manifest_raw,
        _sha(manifest_raw),
        spec75.membership_projection(rows),
        spec75.grouping_projection(rows),
        _sha(original_raw),
        spec75.membership_projection(original_rows),
        spec75.grouping_projection(original_rows),
    )


def build_authority_package(
    *,
    projection: RemediationProjection,
    bindings: PackageBindings,
    synthetic_capability: _SyntheticPackageCapability | None = None,
) -> AuthorityPackage:
    """Build deterministic synthetic package bytes without opening a root."""
    _require_synthetic_capability(synthetic_capability)
    expected_crosswalk = _validate_bindings(bindings)
    (
        manifest_raw,
        manifest_sha,
        membership_sha,
        grouping_sha,
        original_manifest_sha,
        original_membership_sha,
        original_grouping_sha,
    ) = _validate_projection(
        projection,
        expected_crosswalk,
        dict(bindings.input_hashes)["inventory"],
    )

    input_hashes = dict(bindings.input_hashes)
    profile = dict(bindings.authority_profile)
    authority_profile = dict(profile)
    authority_profile["authority_profile_artifact_sha256"] = (
        bindings.authority_profile_artifact_sha256
    )
    snapshot_binding = dict(bindings.snapshot_binding)
    policy_binding = dict(bindings.policy_binding)
    if (
        original_manifest_sha != input_hashes["snapshot_manifest"]
        or original_membership_sha
        != snapshot_binding["membership_projection_sha256"]
        or original_grouping_sha
        != snapshot_binding["grouping_projection_sha256"]
        or membership_sha != original_membership_sha
        or manifest_sha == original_manifest_sha
    ):
        raise PackageTransactionError("derivation_invariant_refused")
    artifact = {
        "schema": ARTIFACT_SCHEMA,
        "status": "snapshot_bound_projection",
        "claim_license": "mechanical_projection_only",
        "input_hashes": input_hashes,
        "authority_profile": authority_profile,
        "snapshot_binding": snapshot_binding,
        "projected_population_manifest_sha256": manifest_sha,
        "coverage": dict(_COVERAGE),
        "policy_binding": policy_binding,
        "passages": projection.passages,
        "evidence": projection.evidence,
        "units": projection.units,
        "pair_exclusion_projection": projection.pair_exclusion_projection,
        "unresolved": dict(_UNRESOLVED),
    }
    if set(artifact) != _ARTIFACT_KEYS:
        raise PackageTransactionError("derivation_invariant_refused")
    artifact_raw = _canonical(artifact)
    if len(artifact_raw) > MAX_ARTIFACT_BYTES:
        raise PackageTransactionError("derivation_invariant_refused")
    artifact_sha = _sha(artifact_raw)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "result": "committed",
        "artifact_sha256": artifact_sha,
        "source_population_commitment_sha256":
            bindings.source_population_commitment_sha256,
        "lineage_crosswalk_artifact_sha256":
            input_hashes["source_lineage_crosswalk"],
        "lineage_crosswalk_commitment_sha256":
            bindings.lineage_crosswalk_commitment_sha256,
        "original_population_manifest_sha256":
            input_hashes["snapshot_manifest"],
        "projected_population_manifest_sha256": manifest_sha,
        "projected_membership_projection_sha256": membership_sha,
        "projected_grouping_projection_sha256": grouping_sha,
        "input_snapshot_attestation_sha256":
            input_hashes["snapshot_attestation"],
        "consumer_policy_attestation_sha256":
            input_hashes["consumer_policy_attestation"],
        "authority_profile_artifact_sha256":
            bindings.authority_profile_artifact_sha256,
        "authority_profile_commitment_sha256":
            profile["profile_commitment_sha256"],
        "spec_sha256": policy_binding["spec_sha256"],
        "review_sha256": policy_binding["review_sha256"],
        "consumer_authority": "snapshot_bound_projection_only",
        "claim_license": "mechanical_projection_only",
        "training_authorized": False,
        "activation_authorized": False,
        "eligibility_attested": False,
    }
    if set(receipt) != _RECEIPT_KEYS:
        raise PackageTransactionError("derivation_invariant_refused")
    receipt_raw = _canonical(receipt)
    if len(receipt_raw) > MAX_RECEIPT_BYTES:
        raise PackageTransactionError("derivation_invariant_refused")

    return AuthorityPackage(
        projected_manifest_bytes=manifest_raw,
        artifact_bytes=artifact_raw,
        receipt_bytes=receipt_raw,
        projected_population_manifest_sha256=manifest_sha,
        projected_membership_projection_sha256=membership_sha,
        projected_grouping_projection_sha256=grouping_sha,
        artifact_sha256=artifact_sha,
        receipt_sha256=_sha(receipt_raw),
        passage_rows=len(projection.passages),
        evidence_rows=len(projection.evidence),
        unit_rows=len(projection.units),
    )


def _validated_names(names: PackageNames) -> tuple[str, str, str]:
    raw_names = (names.projected_manifest, names.artifact, names.receipt)
    try:
        parts = [
            spec75.portable_private_relative_path_v1(value)
            for value in raw_names
        ]
    except spec75.ProbeSetError as exc:
        raise PackageTransactionError("output_recovery_required") from exc
    if (
        any(len(value) != 1 for value in parts)
        or len(set(parts)) != 3
        or len({spec75.portable_collision_key(value) for value in parts}) != 3
    ):
        raise PackageTransactionError("output_recovery_required")
    return raw_names


def _read_existing(
    root: PinnedPrivateRoot,
    name: str,
    maximum: int,
) -> bytes | None:
    try:
        raw = root.read_private_member((name,), maximum)
    except _PrivateIOError:
        return None
    except (_RootPolicyError, _OversizePrivateIOError) as exc:
        raise PackageTransactionError("output_recovery_required") from exc
    if os.name != "posix":
        raise PackageTransactionError("output_recovery_required")
    try:
        root.barrier()
        first = os.stat(
            name,
            dir_fd=root.root_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(first.st_mode)
            or first.st_nlink != 1
            or stat.S_IMODE(first.st_mode) != 0o600
            or first.st_size != len(raw)
        ):
            raise OSError("unsafe output")
        repeated = root.read_private_member((name,), maximum)
        second = os.stat(
            name,
            dir_fd=root.root_descriptor,
            follow_symlinks=False,
        )
        first_identity = (
            first.st_dev,
            first.st_ino,
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
            first.st_nlink,
            stat.S_IFMT(first.st_mode),
            stat.S_IMODE(first.st_mode),
        )
        second_identity = (
            second.st_dev,
            second.st_ino,
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
            second.st_nlink,
            stat.S_IFMT(second.st_mode),
            stat.S_IMODE(second.st_mode),
        )
        if repeated != raw or first_identity != second_identity:
            raise OSError("unstable output")
        root.barrier()
    except (
        OSError,
        _PrivateIOError,
        _RootPolicyError,
        _OversizePrivateIOError,
    ) as exc:
        raise PackageTransactionError("output_recovery_required") from exc
    return raw


def _reparse_projected_manifest(raw: bytes, expected: bytes) -> None:
    if raw != expected:
        raise PackageTransactionError("output_recovery_required")
    try:
        rows = spec75.strict_jsonl_v1(raw)
        spec75.validate_population(rows)
    except (spec75.ProbeSetError, TypeError, ValueError, KeyError) as exc:
        raise PackageTransactionError("output_recovery_required") from exc
    if _manifest_bytes(rows) != expected:
        raise PackageTransactionError("output_recovery_required")


def _revalidate_package_prefix(
    root: PinnedPrivateRoot,
    ordered: list[tuple[str, bytes, int, str, str]],
    count: int,
) -> None:
    for index, (name, payload, maximum, _code, _fault_name) in enumerate(
        ordered[:count]
    ):
        exact = _read_existing(root, name, maximum)
        if exact != payload:
            raise PackageTransactionError("output_recovery_required")
        if index == 0:
            _reparse_projected_manifest(exact, payload)


def publish_authority_package(
    *,
    root: PinnedPrivateRoot,
    package: AuthorityPackage,
    names: PackageNames,
    synthetic_capability: _SyntheticPackageCapability | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Publish/adopt manifest -> artifact -> receipt, never overwriting.

    ``fault_hook`` is a synthetic-test seam invoked only after an exact created
    file has been reopened and verified.
    """
    _require_synthetic_capability(synthetic_capability)
    manifest_name, artifact_name, receipt_name = _validated_names(names)
    ordered = [
        (
            manifest_name,
            package.projected_manifest_bytes,
            MAX_PROJECTED_MANIFEST_BYTES,
            "projected_manifest_publication_refused",
            "after_projected_manifest_create",
        ),
        (
            artifact_name,
            package.artifact_bytes,
            MAX_ARTIFACT_BYTES,
            "artifact_publication_refused",
            "after_artifact_create",
        ),
        (
            receipt_name,
            package.receipt_bytes,
            MAX_RECEIPT_BYTES,
            "receipt_publication_refused",
            "after_receipt_create",
        ),
    ]

    present = [
        _read_existing(root, name, maximum)
        for name, _payload, maximum, _code, _fault_name in ordered
    ]
    for existing, (_name, payload, _maximum, _code, _fault_name) in zip(
        present, ordered,
    ):
        if existing is not None and existing != payload:
            raise PackageTransactionError("output_recovery_required")
    present_indices = [index for index, value in enumerate(present) if value is not None]
    if present_indices and present_indices != list(range(present_indices[-1] + 1)):
        raise PackageTransactionError("output_recovery_required")

    for index, (name, payload, maximum, precreate_code, fault_name) in enumerate(
        ordered
    ):
        if index == 2:
            # The receipt is the commit marker.  Reconfirm its complete prefix
            # immediately before creating it.
            _revalidate_package_prefix(root, ordered, 2)
        created = False
        if present[index] is None:
            try:
                root.publish(name, payload)
                created = True
            except _OutputExists:
                raced = _read_existing(root, name, maximum)
                if raced != payload:
                    raise PackageTransactionError(
                        "output_recovery_required"
                    ) from None
            except _PublicationError as exc:
                raise PackageTransactionError(precreate_code) from exc
            except (_RecoveryRequired, _RootPolicyError) as exc:
                raise PackageTransactionError(
                    "output_recovery_required"
                ) from exc

        exact = _read_existing(root, name, maximum)
        if exact != payload:
            raise PackageTransactionError("output_recovery_required")
        if index == 0:
            _reparse_projected_manifest(exact, payload)
        if created and fault_hook is not None:
            try:
                fault_hook(fault_name)
            except BaseException as exc:
                raise PackageTransactionError(
                    "output_recovery_required"
                ) from exc

    # A committed result is only observable after all three exact files have
    # survived a fresh pinned-root read following receipt creation.
    _revalidate_package_prefix(root, ordered, 3)

    return {
        "schema": SUCCESS_SCHEMA,
        "status": "committed",
        "projected_population_manifest_sha256":
            package.projected_population_manifest_sha256,
        "artifact_sha256": package.artifact_sha256,
        "receipt_sha256": package.receipt_sha256,
        "passage_rows": package.passage_rows,
        "evidence_rows": package.evidence_rows,
        "unit_rows": package.unit_rows,
    }
