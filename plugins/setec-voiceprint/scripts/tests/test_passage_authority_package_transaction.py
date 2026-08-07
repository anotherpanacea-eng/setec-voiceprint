from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import sys
from pathlib import Path

import pytest


import passage_authority_package_transaction as transaction  # noqa: E402
from passage_remediation import PinnedPrivateRoot  # noqa: E402
from passage_remediation_projection import RemediationProjection  # noqa: E402
import reconstructibility_probe_set as spec75  # noqa: E402
from conftest import _digest  # noqa: E402


@pytest.fixture(autouse=True)
def _enable_synthetic_package_test_seam(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(transaction.SYNTHETIC_PACKAGE_ENV, "1")


def _capability() -> transaction._SyntheticPackageCapability:
    return transaction.synthetic_package_capability()


def _profile() -> dict:
    return {
        "schema": "setec-passage-authority-profile/1",
        "producer_revision": "a" * 40,
        "producer_script_git_blob_oid": "sha1:" + "b" * 40,
        "producer_script_sha256": _digest("producer-script"),
        "algorithm_commitment_sha256": _digest("algorithm"),
        "tokenizer_implementation_git_blob_oid": "sha1:" + "c" * 40,
        "tokenizer_implementation_sha256": _digest("tokenizer-code"),
        "tokenizer_data_git_blob_oid": "sha1:" + "d" * 40,
        "tokenizer_data_sha256": _digest("tokenizer-data"),
        "tokenizer_data_commitment_sha256": _digest("tokenizer-commitment"),
        "authority_builder_git_blob_oid": "sha1:" + "e" * 40,
        "authority_builder_script_sha256": _digest("builder"),
        "verifier_git_blob_oid": "sha1:" + "f" * 40,
        "verifier_implementation_sha256": _digest("verifier"),
        "profile_commitment_sha256": _digest("profile"),
    }


def _projection() -> RemediationProjection:
    unit_a = _digest("unit-a")
    unit_b = _digest("unit-b")
    input_a = _digest("input-component-a")
    input_b = _digest("input-component-b")
    projected_component = _digest("projected-component")
    source_a = _digest("source-a")
    normalized_span = _digest("normalized")
    raw_span = _digest("raw")
    evidence_id = transaction._semantic_sha(
        b"setec-passage-stage-b-evidence-id-v1\n",
        {
            "inventory_sha256": _digest("input-inventory"),
            "repeated_span_index": 0,
            "span_sha256": normalized_span,
            "occurrence_index": 0,
            "occurrence_span_id": "source-a#t000000",
            "source_doc_id": source_a,
            "token_start": 0,
            "token_end": 1,
            "char_start": 0,
            "char_end": 2,
            "n_words": 2,
            "normalized_span_sha256": normalized_span,
            "raw_text_sha256": raw_span,
        },
    )
    projection_a = _digest("projection-a")
    projection_b = _digest("projection-b")

    def population_row(
        unit_id: str,
        text_path: str,
        component: str,
        masks: list[list[int]],
    ) -> dict:
        return {
            "schema": spec75.SCHEMA_POPULATION,
            "unit_id": unit_id,
            "text_path": text_path,
            "content_sha256": _digest("content-" + unit_id),
            "corpus_split": "train",
            "evaluation_partition": "qualification",
            "source_group": _digest("source-group"),
            "document_family": _digest("document-family"),
            "duplicate_component": component,
            "loss_mask_intervals": masks,
        }

    # Deliberately B then A: the transaction must preserve input manifest order.
    rows = [
        population_row(
            unit_b, "units/b.txt", projected_component, [],
        ),
        population_row(
            unit_a, "units/a.txt", projected_component, [[0, 2]],
        ),
    ]

    def unit(
        row: dict,
        *,
        input_component: str,
        input_masks: list[list[int]],
        projection_id: str,
    ) -> dict:
        return {
            "unit_id": row["unit_id"],
            "content_sha256": row["content_sha256"],
            "evaluation_partition": row["evaluation_partition"],
            "register": "professional.essay",
            "source_kind": "document_local",
            "source_group": row["source_group"],
            "document_family": row["document_family"],
            "input_duplicate_component": input_component,
            "input_loss_mask_intervals": input_masks,
            "projected_duplicate_component": row["duplicate_component"],
            "projected_loss_mask_intervals": row["loss_mask_intervals"],
            "pairing_excluded": True,
            "evidence_refs": [evidence_id],
            "projection_refs": [projection_id],
        }

    units = sorted([
        unit(
            rows[0],
            input_component=input_b,
            input_masks=[],
            projection_id=projection_b,
        ),
        unit(
            rows[1],
            input_component=input_a,
            input_masks=[],
            projection_id=projection_a,
        ),
    ], key=lambda row: row["unit_id"].encode())
    passages = [
        {
            "source_doc_id": source_a,
            "passage_id": "source-a#p0000",
            "char_start": 0,
            "char_end": 5,
            "n_words": 1,
            "raw_text_sha256": _digest("passage-a"),
            "partition": "cluster_member",
            "disposition": "representative",
        },
        {
            "source_doc_id": _digest("source-b"),
            "passage_id": "source-b#p0000",
            "char_start": 0,
            "char_end": 5,
            "n_words": 1,
            "raw_text_sha256": _digest("passage-b"),
            "partition": "noncluster",
            "disposition": "not_assessed",
        },
    ]
    evidence = [{
        "kind": "stage_b_occurrence",
        "evidence_id": evidence_id,
        "repeated_span_index": 0,
        "occurrence_index": 0,
        "occurrence_span_id": "source-a#t000000",
        "source_doc_id": source_a,
        "token_start": 0,
        "token_end": 1,
        "char_start": 0,
        "char_end": 2,
        "n_words": 2,
        "normalized_span_sha256": normalized_span,
        "raw_text_sha256": raw_span,
        "unit_refs": sorted([unit_a, unit_b], key=lambda value: value.encode()),
    }]
    crosswalk_projection = [
        {
            "projection_index": index,
            "projection_id": projection_id,
            "lineage_slice_id": _digest(f"slice-{index}"),
            "source_doc_id": _digest(f"source-{source_label}"),
            "source_entry_fingerprint": _digest(f"fingerprint-{index}"),
            "source_content_sha256": _digest(f"source-content-{index}"),
            "source_char_start": 0,
            "source_char_end": 5,
            "source_slice_sha256": _digest(f"source-slice-{index}"),
            "unit_id": unit_id,
            "unit_char_start": 0,
            "unit_char_end": 5,
            "projection": "identity_text",
        }
        for index, (projection_id, unit_id, source_label) in enumerate(
            [
                (projection_b, unit_b, "b"),
                (projection_a, unit_a, "a"),
            ]
        )
    ]
    exclusions = sorted(
        [
            {"unit_id": row["unit_id"], "pairing_excluded": True}
            for row in rows
        ],
        key=lambda row: row["unit_id"].encode(),
    )
    return RemediationProjection(
        projected_population_rows=rows,
        passages=passages,
        crosswalk_projection=crosswalk_projection,
        evidence=evidence,
        units=units,
        pair_exclusion_projection=exclusions,
    )


def _bindings() -> transaction.PackageBindings:
    projection = _projection()
    units = {row["unit_id"]: row for row in projection.units}
    original_rows = []
    for row in projection.projected_population_rows:
        original = dict(row)
        original["duplicate_component"] = units[
            row["unit_id"]
        ]["input_duplicate_component"]
        original["loss_mask_intervals"] = units[
            row["unit_id"]
        ]["input_loss_mask_intervals"]
        original_rows.append(original)
    input_hashes = {
        key: _digest("input-" + key)
        for key in transaction._INPUT_HASH_KEYS
    }
    input_hashes["snapshot_manifest"] = spec75.plain_sha256(
        b"".join(transaction._canonical(row) for row in original_rows)
    )
    snapshot = {
        "authoritative_training_snapshot": _digest("snapshot-authority"),
        "population_manifest_sha256": input_hashes["snapshot_manifest"],
        "membership_projection_sha256":
            spec75.membership_projection(original_rows),
        "grouping_projection_sha256": spec75.grouping_projection(original_rows),
    }
    policy = {
        "spec_sha256": _digest("spec"),
        "review_sha256": _digest("review"),
        "consumer_policy_attestation_sha256":
            input_hashes["consumer_policy_attestation"],
    }
    source_commitment = _digest("source-commitment")
    crosswalk_core = {
        "schema": transaction.lineage.CROSSWALK_SCHEMA,
        "source_population_commitment_sha256": source_commitment,
        "population_manifest_sha256": input_hashes["snapshot_manifest"],
        "author_corpus_export_receipt_sha256":
            input_hashes["author_corpus_export_receipt"],
        "authorized_by": "owner",
        "attested_at": "2026-07-29T12:00:00Z",
        "rows": projection.crosswalk_projection,
    }
    crosswalk = dict(crosswalk_core)
    crosswalk["crosswalk_commitment_sha256"] = transaction._semantic_sha(
        b"setec-passage-source-lineage-crosswalk-v1\n",
        crosswalk_core,
    )
    crosswalk_raw = transaction._canonical(crosswalk)
    input_hashes["source_lineage_crosswalk"] = spec75.plain_sha256(
        crosswalk_raw
    )
    return transaction.PackageBindings(
        input_hashes=input_hashes,
        source_lineage_crosswalk_bytes=crosswalk_raw,
        authority_profile=_profile(),
        authority_profile_artifact_sha256=_digest("profile-artifact"),
        snapshot_binding=snapshot,
        policy_binding=policy,
        source_population_commitment_sha256=source_commitment,
        lineage_crosswalk_commitment_sha256=
            crosswalk["crosswalk_commitment_sha256"],
    )


def _package() -> transaction.AuthorityPackage:
    return transaction.build_authority_package(
        projection=_projection(),
        bindings=_bindings(),
        synthetic_capability=_capability(),
    )


def _private_root(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def _names() -> transaction.PackageNames:
    return transaction.PackageNames(
        projected_manifest="projected.jsonl",
        artifact="authority.json",
        receipt="receipt.json",
    )


def test_builds_strict_input_order_manifest_closed_artifact_and_false_receipt():
    projection = _projection()
    package = transaction.build_authority_package(
        projection=projection,
        bindings=_bindings(),
        synthetic_capability=_capability(),
    )

    rows = spec75.strict_jsonl_v1(package.projected_manifest_bytes)
    spec75.validate_population(rows)
    assert [row["unit_id"] for row in rows] == [
        row["unit_id"] for row in projection.projected_population_rows
    ]
    assert package.projected_membership_projection_sha256 == (
        spec75.membership_projection(rows)
    )
    assert package.projected_grouping_projection_sha256 == (
        spec75.grouping_projection(rows)
    )
    artifact = json.loads(package.artifact_bytes)
    assert set(artifact) == transaction._ARTIFACT_KEYS
    assert artifact["passages"] == projection.passages
    assert artifact["units"] == projection.units
    assert "head" not in {
        key.lower() for key in artifact["authority_profile"]
    }
    receipt = json.loads(package.receipt_bytes)
    assert set(receipt) == transaction._RECEIPT_KEYS
    assert receipt["artifact_sha256"] == package.artifact_sha256
    assert receipt["training_authorized"] is False
    assert receipt["activation_authorized"] is False
    assert receipt["eligibility_attested"] is False


def test_final_authority_shapes_require_exact_synthetic_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv(transaction.SYNTHETIC_PACKAGE_ENV)
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.synthetic_package_capability()

    monkeypatch.setenv(transaction.SYNTHETIC_PACKAGE_ENV, "1")
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=_projection(),
            bindings=_bindings(),
        )
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=_projection(),
            bindings=_bindings(),
            synthetic_capability=transaction._SyntheticPackageCapability(
                object()
            ),
        )
    private = _private_root(tmp_path / "private")
    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="derivation_invariant_refused",
        ):
            transaction.publish_authority_package(
                root=root,
                package=_package(),
                names=_names(),
            )
    assert list(private.iterdir()) == []


def test_refuses_no_effect_projection():
    projection = _projection()
    rows = copy.deepcopy(projection.projected_population_rows)
    units = copy.deepcopy(projection.units)
    for row in units:
        row["input_duplicate_component"] = row["projected_duplicate_component"]
        row["input_loss_mask_intervals"] = row["projected_loss_mask_intervals"]
    no_effect = replace(
        projection,
        projected_population_rows=rows,
        units=units,
    )

    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=no_effect,
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


def test_refuses_broken_evidence_inverse_and_manifest_unit_mismatch():
    projection = _projection()
    broken_units = copy.deepcopy(projection.units)
    broken_units[0]["evidence_refs"] = []
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(projection, units=broken_units),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )

    broken_rows = copy.deepcopy(projection.projected_population_rows)
    broken_rows[0]["loss_mask_intervals"] = [[3, 4]]
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(projection, projected_population_rows=broken_rows),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


@pytest.mark.parametrize(
    "mutant",
    ["cleared_ref", "missing", "extra", "duplicate", "wrong_unit"],
)
def test_refuses_noninverse_crosswalk_projection(mutant: str):
    projection = _projection()
    units = copy.deepcopy(projection.units)
    crosswalk = copy.deepcopy(projection.crosswalk_projection)

    if mutant == "cleared_ref":
        owner = crosswalk[0]["unit_id"]
        next(row for row in units if row["unit_id"] == owner)[
            "projection_refs"
        ] = []
    elif mutant == "missing":
        crosswalk.pop()
    elif mutant == "extra":
        extra = copy.deepcopy(crosswalk[-1])
        extra["projection_index"] = len(crosswalk)
        extra["projection_id"] = _digest("extra-projection")
        crosswalk.append(extra)
    elif mutant == "duplicate":
        duplicate = copy.deepcopy(crosswalk[0])
        duplicate["projection_index"] = len(crosswalk)
        crosswalk.append(duplicate)
    else:
        crosswalk[0]["unit_id"] = crosswalk[1]["unit_id"]

    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(
                projection,
                units=units,
                crosswalk_projection=crosswalk,
            ),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


def test_refuses_coordinated_projection_id_and_unit_ref_substitution():
    projection = _projection()
    crosswalk = copy.deepcopy(projection.crosswalk_projection)
    units = copy.deepcopy(projection.units)
    old_id = crosswalk[0]["projection_id"]
    new_id = _digest("coordinated-substitution")
    crosswalk[0]["projection_id"] = new_id
    owner = crosswalk[0]["unit_id"]
    owner_unit = next(row for row in units if row["unit_id"] == owner)
    owner_unit["projection_refs"] = [
        new_id if value == old_id else value
        for value in owner_unit["projection_refs"]
    ]

    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(
                projection,
                crosswalk_projection=crosswalk,
                units=units,
            ),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("passages", "char_start", "0"),
        ("passages", "raw_text_sha256", "not-a-digest"),
        ("evidence", "char_start", "0"),
        ("evidence", "normalized_span_sha256", "not-a-digest"),
    ],
)
def test_refuses_invalid_passage_and_evidence_scalars(
    section: str,
    field: str,
    value: object,
):
    projection = _projection()
    rows = copy.deepcopy(getattr(projection, section))
    rows[0][field] = value
    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(projection, **{section: rows}),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


def _stage_a_projection() -> RemediationProjection:
    projection = _projection()
    source_doc_id = _digest("source-a")
    raw_text_sha256 = _digest("stage-a-raw")
    evidence_id = transaction._semantic_sha(
        b"setec-passage-stage-a-evidence-id-v1\n",
        {
            "inventory_sha256": _digest("input-inventory"),
            "cluster_index": 0,
            "passage_id": "source-a#p0001",
            "source_doc_id": source_doc_id,
            "char_start": 2,
            "char_end": 5,
            "passage_sha256": raw_text_sha256,
            "disposition": "nonrepresentative",
        },
    )
    unit_refs = sorted(
        (row["unit_id"] for row in projection.units),
        key=lambda value: value.encode(),
    )
    evidence = [{
        "kind": "stage_a_nonrepresentative",
        "evidence_id": evidence_id,
        "cluster_index": 0,
        "passage_id": "source-a#p0001",
        "source_doc_id": source_doc_id,
        "char_start": 2,
        "char_end": 5,
        "n_words": 1,
        "raw_text_sha256": raw_text_sha256,
        "disposition": "nonrepresentative",
        "unit_refs": unit_refs,
    }]
    units = copy.deepcopy(projection.units)
    for unit in units:
        unit["evidence_refs"] = [evidence_id]
    return replace(projection, evidence=evidence, units=units)


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("stage_a", "cluster_index", 1),
        ("stage_b", "n_words", 3),
        ("stage_b", "repeated_span_index", 1),
        ("stage_b", "normalized_span_sha256", _digest("changed-span")),
    ],
)
def test_refuses_stale_evidence_id_after_semantic_tuple_mutation(
    kind: str,
    field: str,
    value: object,
):
    projection = _stage_a_projection() if kind == "stage_a" else _projection()
    evidence = copy.deepcopy(projection.evidence)
    evidence[0][field] = value

    with pytest.raises(
        transaction.PackageTransactionError,
        match="derivation_invariant_refused",
    ):
        transaction.build_authority_package(
            projection=replace(projection, evidence=evidence),
            bindings=_bindings(),
            synthetic_capability=_capability(),
        )


def test_fresh_commit_and_exact_committed_retry_are_idempotent(tmp_path: Path):
    private = _private_root(tmp_path / "private")
    package = _package()
    names = _names()
    with PinnedPrivateRoot(str(private)) as root:
        first = transaction.publish_authority_package(
            root=root, package=package, names=names,
            synthetic_capability=_capability(),
        )
        second = transaction.publish_authority_package(
            root=root, package=package, names=names,
            synthetic_capability=_capability(),
        )

    assert first == second
    assert (private / names.projected_manifest).read_bytes() == (
        package.projected_manifest_bytes
    )
    assert (private / names.artifact).read_bytes() == package.artifact_bytes
    assert (private / names.receipt).read_bytes() == package.receipt_bytes


@pytest.mark.parametrize(
    ("fault_name", "expected_files"),
    [
        ("after_projected_manifest_create", 1),
        ("after_artifact_create", 2),
        ("after_receipt_create", 3),
    ],
)
def test_fault_after_each_create_resumes_by_exact_prefix_adoption(
    tmp_path: Path,
    fault_name: str,
    expected_files: int,
):
    private = _private_root(tmp_path / fault_name)
    package = _package()
    names = _names()

    def fail(selected: str) -> None:
        if selected == fault_name:
            raise RuntimeError("synthetic crash")

    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root,
                package=package,
                names=names,
                synthetic_capability=_capability(),
                fault_hook=fail,
            )
        assert len(list(private.iterdir())) == expected_files
        result = transaction.publish_authority_package(
            root=root, package=package, names=names,
            synthetic_capability=_capability(),
        )

    assert result["status"] == "committed"
    assert len(list(private.iterdir())) == 3


@pytest.mark.parametrize(
    ("fault_name", "deleted_name", "receipt_exists"),
    [
        ("after_artifact_create", "projected_manifest", False),
        ("after_receipt_create", "artifact", True),
    ],
)
def test_late_output_loss_never_returns_committed(
    tmp_path: Path,
    fault_name: str,
    deleted_name: str,
    receipt_exists: bool,
):
    private = _private_root(tmp_path / fault_name)
    package = _package()
    names = _names()

    def delete_without_raising(selected: str) -> None:
        if selected == fault_name:
            (private / getattr(names, deleted_name)).unlink()

    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root,
                package=package,
                names=names,
                synthetic_capability=_capability(),
                fault_hook=delete_without_raising,
            )

    assert (private / names.receipt).exists() is receipt_exists


def test_receipt_without_exact_prefix_refuses_without_creating_files(
    tmp_path: Path,
):
    private = _private_root(tmp_path / "receipt-only")
    package = _package()
    names = _names()
    (private / names.receipt).write_bytes(package.receipt_bytes)
    (private / names.receipt).chmod(0o600)

    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root, package=package, names=names,
                synthetic_capability=_capability(),
            )

    assert [path.name for path in private.iterdir()] == [names.receipt]


def test_unsafe_exact_prefix_and_colliding_output_names_refuse(tmp_path: Path):
    private = _private_root(tmp_path / "unsafe-prefix")
    package = _package()
    names = _names()
    manifest = private / names.projected_manifest
    manifest.write_bytes(package.projected_manifest_bytes)
    manifest.chmod(0o644)

    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root, package=package, names=names,
                synthetic_capability=_capability(),
            )
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root,
                package=package,
                names=transaction.PackageNames(
                    projected_manifest="same.json",
                    artifact="SAME.JSON",
                    receipt="receipt.json",
                ),
                synthetic_capability=_capability(),
            )

    assert manifest.read_bytes() == package.projected_manifest_bytes
    assert stat_mode(manifest) == 0o644


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.mark.parametrize("conflict_index", [0, 1, 2])
def test_unequal_conflicts_are_preserved_without_overwrite_or_delete(
    tmp_path: Path,
    conflict_index: int,
):
    private = _private_root(tmp_path / f"conflict-{conflict_index}")
    package = _package()
    names = _names()
    ordered = [
        (names.projected_manifest, package.projected_manifest_bytes),
        (names.artifact, package.artifact_bytes),
        (names.receipt, package.receipt_bytes),
    ]
    for name, payload in ordered[:conflict_index]:
        (private / name).write_bytes(payload)
        (private / name).chmod(0o600)
    conflict_name = ordered[conflict_index][0]
    conflict_payload = b"foreign\n"
    (private / conflict_name).write_bytes(conflict_payload)
    (private / conflict_name).chmod(0o600)
    before = {
        path.name: path.read_bytes() for path in private.iterdir()
    }

    with PinnedPrivateRoot(str(private)) as root:
        with pytest.raises(
            transaction.PackageTransactionError,
            match="output_recovery_required",
        ):
            transaction.publish_authority_package(
                root=root, package=package, names=names,
                synthetic_capability=_capability(),
            )

    after = {path.name: path.read_bytes() for path in private.iterdir()}
    assert before == after
    assert after[conflict_name] == conflict_payload
