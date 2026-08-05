"""Synthetic contract tests for the spec-74 Increment-1 remediation tool."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "passage_remediation.py"
import passage_remediation as pr  # noqa: E402


INVENTORY_BYTES = (
    b'{"assumptions":{"calibration_status":"heuristic / uncalibrated \xe2\x80\x94 no bands, no thresholds promoted"},'
    b'"claim_license":{},"documents_affected":[],"input_rows_skipped":[],"mode":"passages","n_documents":3,'
    b'"n_passages":3,"provenance":{"duplicated_regions":[],"passage_clusters":[{"dropped":["ctl-doc-b#p0000",'
    b'"ctl-doc-c#p0000"],"passages":[{"char_end":12,"char_start":0,"n_words":4,"ordinal":0,'
    b'"passage_id":"ctl-doc-a#p0000","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    b'"source_doc_id":"ctl-doc-a","source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},'
    b'{"char_end":12,"char_start":0,"n_words":4,"ordinal":0,"passage_id":"ctl-doc-b#p0000",'
    b'"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_doc_id":"ctl-doc-b",'
    b'"source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},'
    b'{"char_end":12,"char_start":0,"n_words":4,"ordinal":0,"passage_id":"ctl-doc-c#p0000",'
    b'"sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","source_doc_id":"ctl-doc-c",'
    b'"source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"}],'
    b'"representative":"ctl-doc-a#p0000"}],"repeated_spans":[{"n_occurrences":2,"n_words":4,'
    b'"occurrences":[{"char_end":12,"char_start":0,"n_words":4,"sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",'
    b'"source_doc_id":"ctl-doc-a","source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
    b'"span_id":"ctl-doc-a#t000000","token_end":3,"token_start":0},{"char_end":12,"char_start":0,"n_words":4,'
    b'"sha256":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","source_doc_id":"ctl-doc-b",'
    b'"source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
    b'"span_id":"ctl-doc-b#t000000","token_end":3,"token_start":0}],"span_sha256":'
    b'"9999999999999999999999999999999999999999999999999999999999999999"}]},'
    b'"source_manifest":"sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",'
    b'"stage_a":{"clusters":1,"dropped":2,"kept":1,"run":true,"short_exact_groups":0},'
    b'"stage_b":{"duplicated_regions":0,"n_below_floor":{"duplicated_regions":0,"repeated_spans":0},'
    b'"repeated_spans":1,"run":true},"stages":["a","b"]}\n'
)
PROJECTION_BYTES = b'{"control":"projection"}\n'
INVENTORY_SHA256 = "4ed12e1357d639847667df794537bf85c3aa7306591a7b6fd045e4c7bd17204a"
PROJECTION_SHA256 = "a5b56d8a7c1bd2611edb3ad75098d8c0ec154cab8caedba11b6d131f998d4beb"
DESCRIPTOR_BYTES = (
    b'{"expected_counts":{"candidate_drops":2,"passage_clusters":1,"repeated_spans":1},'
    b'"inventory_path":"fixtures/inventory.json","inventory_sha256":"sha256:'
    b'4ed12e1357d639847667df794537bf85c3aa7306591a7b6fd045e4c7bd17204a","policy":'
    b'"stage-a-retain-one-loss-bearing-representative-v1","projection_receipt_path":'
    b'"fixtures/projection.json","projection_receipt_sha256":"sha256:'
    b'a5b56d8a7c1bd2611edb3ad75098d8c0ec154cab8caedba11b6d131f998d4beb","schema":'
    b'"setec-passage-remediation-descriptor/1"}\n'
)
ARTIFACT_BYTES = (
    b'{"counts":{"candidate_drops":2,"decision_rows":3,"nonrepresentatives":2,'
    b'"passage_clusters":1,"repeated_spans_observed":1,"representatives":1,'
    b'"stage_a_loss_excluded":2,"stage_a_loss_not_excluded":1,"stage_a_pairing_excluded":2,'
    b'"stage_a_pairing_not_excluded":1},"decisions":[{"candidate_role":"representative",'
    b'"cluster_index":0,"passage_id":"ctl-doc-a#p0000","passage_sha256":'
    b'"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","policy":'
    b'"stage-a-retain-one-loss-bearing-representative-v1","reason_code":'
    b'"single_loss_bearing_representative","schema":"setec-passage-remediation-decision/1",'
    b'"source_doc_id":"ctl-doc-a","stage_a_loss_excluded":false,"stage_a_masking_decision":'
    b'"unmasked","stage_a_pairing_excluded":false},{"candidate_role":"nonrepresentative",'
    b'"cluster_index":0,"passage_id":"ctl-doc-b#p0000","passage_sha256":'
    b'"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","policy":'
    b'"stage-a-retain-one-loss-bearing-representative-v1","reason_code":'
    b'"repeated_passage_nonrepresentative","schema":"setec-passage-remediation-decision/1",'
    b'"source_doc_id":"ctl-doc-b","stage_a_loss_excluded":true,"stage_a_masking_decision":'
    b'"mask_all_training_targets","stage_a_pairing_excluded":true},{"candidate_role":'
    b'"nonrepresentative","cluster_index":0,"passage_id":"ctl-doc-c#p0000","passage_sha256":'
    b'"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","policy":'
    b'"stage-a-retain-one-loss-bearing-representative-v1","reason_code":'
    b'"repeated_passage_nonrepresentative","schema":"setec-passage-remediation-decision/1",'
    b'"source_doc_id":"ctl-doc-c","stage_a_loss_excluded":true,"stage_a_masking_decision":'
    b'"mask_all_training_targets","stage_a_pairing_excluded":true}],"inventory_sha256":'
    b'"sha256:4ed12e1357d639847667df794537bf85c3aa7306591a7b6fd045e4c7bd17204a",'
    b'"policy":"stage-a-retain-one-loss-bearing-representative-v1",'
    b'"projection_receipt_sha256":"sha256:'
    b'a5b56d8a7c1bd2611edb3ad75098d8c0ec154cab8caedba11b6d131f998d4beb",'
    b'"schema":"setec-passage-remediation-decisions/1","scope":{"calibration_status":'
    b'"operational_uncalibrated","consumer_authority":"none","coverage":'
    b'"itemized_stage_a_clusters_only","noncluster_passages":"not_assessed",'
    b'"stage_b_disposition":"unresolved"}}\n'
)
RECEIPT_BYTES = (
    b'{"counts":{"candidate_drops":2,"decision_rows":3,"nonrepresentatives":2,'
    b'"passage_clusters":1,"repeated_spans_observed":1,"representatives":1,'
    b'"stage_a_loss_excluded":2,"stage_a_loss_not_excluded":1,"stage_a_pairing_excluded":2,'
    b'"stage_a_pairing_not_excluded":1},"inventory_sha256":"sha256:'
    b'4ed12e1357d639847667df794537bf85c3aa7306591a7b6fd045e4c7bd17204a",'
    b'"output_sha256":"sha256:21cc617d23dbe23bd03a639c4f3c2d3a03ff0b2d941f2a7d260fa7fb0f39ebdb",'
    b'"policy":"stage-a-retain-one-loss-bearing-representative-v1",'
    b'"projection_receipt_sha256":"sha256:'
    b'a5b56d8a7c1bd2611edb3ad75098d8c0ec154cab8caedba11b6d131f998d4beb",'
    b'"schema":"setec-passage-remediation-receipt/1","scope":{"calibration_status":'
    b'"operational_uncalibrated","consumer_authority":"none","coverage":'
    b'"itemized_stage_a_clusters_only","noncluster_passages":"not_assessed",'
    b'"stage_b_disposition":"unresolved"}}\n'
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _descriptor(
    inventory_bytes: bytes = INVENTORY_BYTES,
    projection_bytes: bytes = PROJECTION_BYTES,
    *,
    inventory_path: str = "fixtures/inventory.json",
    projection_path: str = "fixtures/projection.json",
    counts: tuple[int, int, int] = (1, 2, 1),
) -> bytes:
    return _canonical(
        {
            "schema": pr.DESCRIPTOR_SCHEMA,
            "policy": pr.POLICY,
            "inventory_path": inventory_path,
            "inventory_sha256": "sha256:" + hashlib.sha256(inventory_bytes).hexdigest(),
            "projection_receipt_path": projection_path,
            "projection_receipt_sha256": "sha256:"
            + hashlib.sha256(projection_bytes).hexdigest(),
            "expected_counts": {
                "passage_clusters": counts[0],
                "candidate_drops": counts[1],
                "repeated_spans": counts[2],
            },
        }
    )


def _prepare(
    tmp_path: Path,
    *,
    inventory_bytes: bytes = INVENTORY_BYTES,
    projection_bytes: bytes = PROJECTION_BYTES,
    descriptor_bytes: bytes | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    root = root.resolve()
    fixtures = root / "fixtures"
    fixtures.mkdir(mode=0o700)
    (fixtures / "inventory.json").write_bytes(inventory_bytes)
    (fixtures / "projection.json").write_bytes(projection_bytes)
    (root / "descriptor.json").write_bytes(
        descriptor_bytes
        if descriptor_bytes is not None
        else _descriptor(inventory_bytes, projection_bytes)
    )
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    return root


def _args(root: Path, *, output: str = "decisions.json") -> argparse.Namespace:
    return argparse.Namespace(
        private_root=str(root),
        descriptor="descriptor.json",
        output=output,
        json=True,
        help=False,
    )


def _run(
    root: Path, *extra: str, json_mode: bool = True
) -> subprocess.CompletedProcess[bytes]:
    arguments = [
        sys.executable,
        str(SCRIPT),
        "--private-root",
        str(root),
        "--descriptor",
        "descriptor.json",
        "--output",
        "decisions.json",
    ]
    if json_mode:
        arguments.append("--json")
    arguments.extend(extra)
    return subprocess.run(
        arguments,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _base_inventory() -> dict[str, object]:
    return json.loads(INVENTORY_BYTES)


def _error_code(result: subprocess.CompletedProcess[bytes]) -> str:
    assert result.returncode == 3
    assert result.stdout == b""
    value = json.loads(result.stderr)
    assert value == {
        "code": value["code"],
        "schema": pr.ERROR_SCHEMA,
        "status": "error",
    }
    expected = (
        b'{"code":"'
        + value["code"].encode("ascii")
        + b'","schema":"setec-passage-remediation-error/1","status":"error"}\n'
    )
    assert result.stderr == expected
    return value["code"]


@pytest.mark.parametrize("json_mode", [False, True])
def test_worked_example_golden_bytes(tmp_path: Path, json_mode: bool) -> None:
    assert hashlib.sha256(INVENTORY_BYTES).hexdigest() == INVENTORY_SHA256
    assert hashlib.sha256(PROJECTION_BYTES).hexdigest() == PROJECTION_SHA256
    assert _descriptor() == DESCRIPTOR_BYTES
    root = _prepare(tmp_path, descriptor_bytes=DESCRIPTOR_BYTES)
    result = _run(root, json_mode=json_mode)
    assert result.returncode == 0 and result.stderr == b""
    assert (root / "decisions.json").read_bytes() == ARTIFACT_BYTES
    assert result.stdout == RECEIPT_BYTES


def test_decision_order_and_frozen_policy_tuple() -> None:
    inventory = _base_inventory()
    clusters = pr.validate_inventory(
        inventory,
        {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
    )
    artifact = pr.derive_cluster_decisions(
        clusters,
        inventory_sha256="sha256:" + "1" * 64,
        projection_receipt_sha256="sha256:" + "2" * 64,
        repeated_spans=1,
    )
    assert [
        (
            row["passage_id"],
            row["candidate_role"],
            row["stage_a_masking_decision"],
            row["stage_a_loss_excluded"],
            row["stage_a_pairing_excluded"],
            row["reason_code"],
        )
        for row in artifact["decisions"]
    ] == [
        (
            "ctl-doc-a#p0000",
            "representative",
            "unmasked",
            False,
            False,
            "single_loss_bearing_representative",
        ),
        (
            "ctl-doc-b#p0000",
            "nonrepresentative",
            "mask_all_training_targets",
            True,
            True,
            "repeated_passage_nonrepresentative",
        ),
        (
            "ctl-doc-c#p0000",
            "nonrepresentative",
            "mask_all_training_targets",
            True,
            True,
            "repeated_passage_nonrepresentative",
        ),
    ]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema", "wrong"),
        ("policy", "wrong"),
        ("cluster_index", 2),
        ("passage_id", "wrong"),
        ("source_doc_id", "wrong"),
        ("passage_sha256", "0" * 64),
        ("candidate_role", "representative"),
        ("stage_a_masking_decision", "unmasked"),
        ("stage_a_loss_excluded", False),
        ("stage_a_pairing_excluded", False),
        ("reason_code", "single_loss_bearing_representative"),
    ],
)
def test_truth_table_independent_mutations_refuse(
    field: str, replacement: object
) -> None:
    inventory = _base_inventory()
    clusters = pr.validate_inventory(
        inventory,
        {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
    )
    artifact = pr.derive_cluster_decisions(
        clusters,
        inventory_sha256="sha256:" + "1" * 64,
        projection_receipt_sha256="sha256:" + "2" * 64,
        repeated_spans=1,
    )
    artifact["decisions"][1][field] = replacement
    with pytest.raises(pr.RemediationError, match="^decision_invariant_refused$"):
        pr.validate_decision_truth_table(artifact, clusters, 1)


def test_truth_table_count_and_scope_mutations_refuse() -> None:
    inventory = _base_inventory()
    clusters = pr.validate_inventory(
        inventory,
        {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
    )
    artifact = pr.derive_cluster_decisions(
        clusters,
        inventory_sha256="sha256:" + "1" * 64,
        projection_receipt_sha256="sha256:" + "2" * 64,
        repeated_spans=1,
    )
    artifact["counts"]["repeated_spans_observed"] = 9
    with pytest.raises(pr.RemediationError, match="^decision_invariant_refused$"):
        pr.validate_decision_truth_table(artifact, clusters, 1)
    artifact["counts"]["repeated_spans_observed"] = 1
    artifact["scope"]["consumer_authority"] = "training"
    with pytest.raises(pr.RemediationError, match="^decision_invariant_refused$"):
        pr.validate_decision_truth_table(artifact, clusters, 1)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["provenance"]["passage_clusters"][0]["dropped"].append(
            "ctl-doc-a#p0000"
        ),
        lambda value: value["provenance"]["passage_clusters"][0]["passages"].pop(),
        lambda value: value["provenance"]["passage_clusters"][0]["passages"].reverse(),
        lambda value: value["provenance"]["passage_clusters"][0].update(dropped=[]),
        lambda value: value["stage_a"].update(dropped=3),
        lambda value: value["provenance"]["passage_clusters"][0]["passages"][1].update(
            source_manifest="sha256:" + "0" * 64
        ),
    ],
)
def test_cluster_conservation_mutations_refuse(mutation: object) -> None:
    value = _base_inventory()
    mutation(value)
    with pytest.raises(pr.RemediationError):
        pr.validate_inventory(
            value,
            {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["stage_a"].update(run=False),
        lambda value: value["stage_b"].update(run=False),
        lambda value: value.update(input_rows_skipped=[0]),
        lambda value: value.update(mode="documents"),
        lambda value: value.update(stages=["b", "a"]),
        lambda value: value["stage_a"].update(clusters=True),
    ],
)
def test_inventory_schema_mutations_refuse(mutation: object) -> None:
    value = _base_inventory()
    mutation(value)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr.validate_inventory(
            value,
            {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}\n',
        b'{"a":NaN}\n',
        b'{"a":"\\ud800"}\n',
        b"\xff",
    ],
)
def test_strict_json_refuses_ambiguous_or_invalid_bytes(raw: bytes) -> None:
    with pytest.raises(pr.RemediationError):
        pr._strict_json_object(raw, maximum=1024, code="descriptor_schema_refused")


def test_resource_limit_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "MAX_JSON_NODES", 2)
    with pytest.raises(pr.RemediationError, match="^descriptor_schema_refused$"):
        pr._strict_json_object(
            b'{"a":[1,2]}\n', maximum=1024, code="descriptor_schema_refused"
        )


def test_all_structural_resource_ceilings_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path / "bytes")
    monkeypatch.setattr(pr, "MAX_INVENTORY_BYTES", len(INVENTORY_BYTES) - 1)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr.run(_args(root))

    monkeypatch.setattr(pr, "MAX_INVENTORY_BYTES", 67_108_864)
    monkeypatch.setattr(pr, "MAX_JSON_DEPTH", 1)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr._strict_json_object(
            b'{"a":{"b":1}}\n',
            maximum=1024,
            code="inventory_schema_refused",
        )
    monkeypatch.setattr(pr, "MAX_JSON_DEPTH", 16)
    monkeypatch.setattr(pr, "MAX_JSON_STRING_BYTES", 2)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr._strict_json_object(
            b'{"a":"abc"}\n',
            maximum=1024,
            code="inventory_schema_refused",
        )
    monkeypatch.setattr(pr, "MAX_JSON_STRING_BYTES", 1_048_576)
    monkeypatch.setattr(pr, "MAX_JSON_CONTAINER_ITEMS", 1)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr._strict_json_object(
            b'{"a":1,"b":2}\n',
            maximum=1024,
            code="inventory_schema_refused",
        )


@pytest.mark.parametrize(
    ("constant", "limit"),
    [
        ("MAX_CLUSTERS", 0),
        ("MAX_DECISIONS", 2),
        ("MAX_STAGE_B_SPANS", 0),
    ],
)
def test_inventory_collection_ceilings_refuse(
    monkeypatch: pytest.MonkeyPatch, constant: str, limit: int
) -> None:
    monkeypatch.setattr(pr, constant, limit)
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr.validate_inventory(
            _base_inventory(),
            {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
        )


def test_output_byte_ceiling_refuses_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)
    monkeypatch.setattr(pr, "MAX_OUTPUT_BYTES", 1)
    with pytest.raises(pr.RemediationError, match="^decision_invariant_refused$"):
        pr.run(_args(root))
    assert not (root / "decisions.json").exists()


def test_schema_refusal_precedes_conservation_refusal() -> None:
    value = _base_inventory()
    value["stage_a"]["dropped"] = 99
    value["provenance"]["repeated_spans"][0]["occurrences"][0][
        "token_start"
    ] = True
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr.validate_inventory(
            value,
            {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
        )


def test_invalid_dropped_member_type_is_schema_refusal() -> None:
    value = _base_inventory()
    value["provenance"]["passage_clusters"][0]["dropped"][0] = 7
    with pytest.raises(pr.RemediationError, match="^inventory_schema_refused$"):
        pr.validate_inventory(
            value,
            {"passage_clusters": 1, "candidate_drops": 2, "repeated_spans": 1},
        )


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("inventory", "inventory_hash_refused"),
        ("projection", "projection_receipt_hash_refused"),
    ],
)
def test_bound_member_hash_drift_refuses(
    tmp_path: Path, member: str, expected: str
) -> None:
    root = _prepare(tmp_path)
    (root / "fixtures" / f"{member}.json").write_bytes(b"drift\n")
    assert _error_code(_run(root)) == expected


def test_descriptor_expected_count_drift_refuses(tmp_path: Path) -> None:
    root = _prepare(tmp_path, descriptor_bytes=_descriptor(counts=(1, 3, 1)))
    assert _error_code(_run(root)) == "inventory_conservation_refused"


@pytest.mark.parametrize(
    "path",
    [
        "/absolute.json",
        "../parent.json",
        "./dot.json",
        "part//empty.json",
        r"part\\backslash.json",
        "CON.json",
        "part/\x00bad.json",
        "a" * 256 + ".json",
    ],
)
def test_private_member_path_grammar_refuses(tmp_path: Path, path: str) -> None:
    root = _prepare(
        tmp_path,
        descriptor_bytes=_descriptor(inventory_path=path),
    )
    assert _error_code(_run(root)) == "private_path_refused"


def test_case_colliding_descriptor_members_refuse(tmp_path: Path) -> None:
    root = _prepare(
        tmp_path,
        descriptor_bytes=_descriptor(
            inventory_path="fixtures/Input.json",
            projection_path="fixtures/input.json",
        ),
    )
    assert _error_code(_run(root)) == "private_path_refused"


def test_symlink_and_hardlink_members_refuse(tmp_path: Path) -> None:
    symlink_root = _prepare(tmp_path / "symlink")
    target = symlink_root / "fixtures" / "target.json"
    target.write_bytes(INVENTORY_BYTES)
    target.chmod(0o600)
    inventory = symlink_root / "fixtures" / "inventory.json"
    inventory.unlink()
    inventory.symlink_to(target.name)
    assert _error_code(_run(symlink_root)) == "inventory_hash_refused"

    hardlink_root = _prepare(tmp_path / "hardlink")
    inventory = hardlink_root / "fixtures" / "inventory.json"
    other = hardlink_root / "fixtures" / "other.json"
    os.link(inventory, other)
    assert _error_code(_run(hardlink_root)) == "inventory_hash_refused"


def test_private_root_mode_and_post_open_replacement_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path / "mode")
    root.chmod(0o755)
    assert _error_code(_run(root)) == "private_root_refused"

    root = _prepare(tmp_path / "swap")

    def swap(label: str) -> None:
        if label != "after_root_open":
            return
        root.rename(root.with_name("private-old"))
        root.mkdir(mode=0o700)

    monkeypatch.setattr(pr, "_FAULT_HOOK", swap)
    with pytest.raises(pr.RemediationError, match="^private_root_refused$"):
        pr.run(_args(root))


def test_output_alias_existing_and_race_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path / "alias")
    with pytest.raises(pr.RemediationError, match="^private_path_refused$"):
        pr.run(_args(root, output="descriptor.json"))

    root = _prepare(tmp_path / "exists")
    (root / "decisions.json").write_bytes(b"winner\n")
    (root / "decisions.json").chmod(0o600)
    assert _error_code(_run(root)) == "output_exists_refused"
    assert (root / "decisions.json").read_bytes() == b"winner\n"

    root = _prepare(tmp_path / "race")
    real_open = pr.os.open

    def race_open(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == "decisions.json" and flags & os.O_EXCL:
            winner = real_open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=dir_fd,
            )
            os.write(winner, b"winner\n")
            os.close(winner)
            raise FileExistsError()
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(pr.os, "open", race_open)
    with pytest.raises(pr.RemediationError, match="^output_exists_refused$"):
        pr.run(_args(root))
    assert (root / "decisions.json").read_bytes() == b"winner\n"


def test_precreate_publication_fault_publishes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)
    real_open = pr.os.open

    def fail_open(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == "decisions.json" and flags & os.O_EXCL:
            raise OSError("private detail")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(pr.os, "open", fail_open)
    with pytest.raises(pr.RemediationError, match="^output_publication_refused$"):
        pr.run(_args(root))
    assert not (root / "decisions.json").exists()


def test_ambiguous_create_success_then_raise_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)
    real_open = pr.os.open

    def create_then_raise(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "decisions.json" and flags & os.O_EXCL:
            os.close(descriptor)
            raise OSError("ambiguous syscall wrapper")
        return descriptor

    monkeypatch.setattr(pr.os, "open", create_then_raise)
    with pytest.raises(pr.RemediationError, match="^output_recovery_required$"):
        pr.run(_args(root))
    assert (root / "decisions.json").exists()


@pytest.mark.parametrize(
    "label",
    [
        "after_commit",
        "after_parent_fsync",
        "before_reopen_verify",
        "after_reopen_verify",
    ],
)
def test_postcreate_fault_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    root = _prepare(tmp_path)

    def fail(point: str) -> None:
        if point == label:
            raise OSError("private detail")

    monkeypatch.setattr(pr, "_FAULT_HOOK", fail)
    with pytest.raises(pr.RemediationError, match="^output_recovery_required$"):
        pr.run(_args(root))
    assert (root / "decisions.json").exists()


def test_final_byte_mismatch_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)

    def tamper(point: str) -> None:
        if point == "before_reopen_verify":
            (root / "decisions.json").write_bytes(b"tamper\n")

    monkeypatch.setattr(pr, "_FAULT_HOOK", tamper)
    with pytest.raises(pr.RemediationError, match="^output_recovery_required$"):
        pr.run(_args(root))
    assert (root / "decisions.json").read_bytes() == b"tamper\n"


def test_final_identity_mismatch_requires_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)

    def replace(point: str) -> None:
        if point == "before_reopen_verify":
            final = root / "decisions.json"
            final.unlink()
            final.write_bytes(b"winner\n")
            final.chmod(0o600)

    monkeypatch.setattr(pr, "_FAULT_HOOK", replace)
    with pytest.raises(pr.RemediationError, match="^output_recovery_required$"):
        pr.run(_args(root))
    assert (root / "decisions.json").read_bytes() == b"winner\n"


def test_postcleanup_name_replacement_cannot_yield_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare(tmp_path)

    def replace(point: str) -> None:
        if point == "after_postcleanup_read":
            final = root / "decisions.json"
            final.unlink()
            final.write_bytes(b"winner\n")
            final.chmod(0o600)

    monkeypatch.setattr(pr, "_FAULT_HOOK", replace)
    with pytest.raises(pr.RemediationError, match="^output_recovery_required$"):
        pr.run(_args(root))
    assert (root / "decisions.json").read_bytes() == b"winner\n"


@pytest.mark.parametrize("raise_after_rename", [False, True])
def test_windows_publication_uses_stable_file_identity_and_handles_ambiguity(
    monkeypatch: pytest.MonkeyPatch, raise_after_rename: bool
) -> None:
    class FakeWindowsIO:
        def __init__(self) -> None:
            self.data = bytearray()
            self.offsets = {1: 0, 2: 0}
            self.final = False

        def info(self, handle: int) -> SimpleNamespace:
            if handle == 99:
                return SimpleNamespace(
                    volume_serial=1, file_id=99, size=0, write_time=0
                )
            return SimpleNamespace(
                volume_serial=1,
                file_id=7,
                size=len(self.data),
                write_time=len(self.data),
            )

        def probe_leaf_node(
            self, _root: int, _name: str
        ) -> SimpleNamespace | None:
            return self.info(1) if self.final else None

        def create_owner_private_file(
            self,
            _root: int,
            _temp: str,
            *,
            share_delete: bool,
            share_write: bool,
        ) -> int:
            assert share_delete is False
            assert share_write is False
            return 1

        def require_owner_private(
            self, handle: int, _kind: str
        ) -> SimpleNamespace:
            return self.info(handle)

        def require_direct(self, handle: int, _kind: str) -> SimpleNamespace:
            return self.info(handle)

        def revalidate_directory_chain(
            self, _path: Path, _handles: tuple[int, ...]
        ) -> None:
            return None

        def write(self, handle: int, raw: object) -> int:
            data = bytes(raw)
            self.data.extend(data)
            self.offsets[handle] += len(data)
            return len(data)

        def flush(self, _handle: int) -> None:
            return None

        def seek(self, handle: int, offset: int) -> None:
            self.offsets[handle] = offset

        def read(self, handle: int, size: int) -> bytes:
            offset = self.offsets[handle]
            raw = bytes(self.data[offset : offset + size])
            self.offsets[handle] += len(raw)
            return raw

        def rename(
            self,
            _handle: int,
            _root: int,
            _name: str,
            *,
            replace: bool,
        ) -> None:
            assert replace is False
            self.final = True
            if raise_after_rename:
                raise OSError("ambiguous rename")

        def open_file(
            self,
            _root: int,
            _name: str,
            *,
            allow_multiple_links: bool,
        ) -> int:
            assert allow_multiple_links is False
            self.offsets[2] = 0
            return 2

        def delete(self, _handle: int) -> None:
            self.final = False

        def close(self, _handle: int) -> None:
            return None

    fake = FakeWindowsIO()
    root = object.__new__(pr.PinnedPrivateRoot)
    root.path = Path("private-root")
    root._fds = []
    root._identities = []
    root._winio = fake
    root._handles = (99,)
    monkeypatch.setattr(pr.os, "name", "nt")
    if raise_after_rename:
        with pytest.raises(pr._PublicationError):
            root._publish_windows("decisions.json", b"payload\n")
        assert fake.final is False
    else:
        root._publish_windows("decisions.json", b"payload\n")
        assert fake.final is True


def test_identical_fresh_builds_are_byte_identical(tmp_path: Path) -> None:
    outputs: list[bytes] = []
    receipts: list[bytes] = []
    for name in ("one", "two"):
        root = _prepare(tmp_path / name)
        result = _run(root)
        assert result.returncode == 0 and result.stderr == b""
        outputs.append((root / "decisions.json").read_bytes())
        receipts.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert receipts[0] == receipts[1]


def test_stage_b_content_binds_hash_but_not_decisions(tmp_path: Path) -> None:
    changed = _base_inventory()
    changed["provenance"]["repeated_spans"][0]["span_sha256"] = "8" * 64
    changed_bytes = _canonical(changed)
    roots = [
        _prepare(tmp_path / "base"),
        _prepare(tmp_path / "changed", inventory_bytes=changed_bytes),
    ]
    artifacts = []
    receipts = []
    for root in roots:
        result = _run(root)
        assert result.returncode == 0
        artifacts.append(json.loads((root / "decisions.json").read_bytes()))
        receipts.append(json.loads(result.stdout))
    assert artifacts[0]["decisions"] == artifacts[1]["decisions"]
    assert artifacts[0]["counts"] == artifacts[1]["counts"]
    assert artifacts[0]["inventory_sha256"] != artifacts[1]["inventory_sha256"]
    assert receipts[0]["output_sha256"] != receipts[1]["output_sha256"]


def test_stage_b_length_changes_observed_count_only(tmp_path: Path) -> None:
    changed = _base_inventory()
    second = copy.deepcopy(changed["provenance"]["repeated_spans"][0])
    second["span_sha256"] = "7" * 64
    changed["provenance"]["repeated_spans"].append(second)
    changed["stage_b"]["repeated_spans"] = 2
    changed_bytes = _canonical(changed)
    root = _prepare(
        tmp_path,
        inventory_bytes=changed_bytes,
        descriptor_bytes=_descriptor(changed_bytes, counts=(1, 2, 2)),
    )
    result = _run(root)
    assert result.returncode == 0
    artifact = json.loads((root / "decisions.json").read_bytes())
    assert artifact["counts"]["repeated_spans_observed"] == 2
    assert [row["passage_id"] for row in artifact["decisions"]] == [
        "ctl-doc-a#p0000",
        "ctl-doc-b#p0000",
        "ctl-doc-c#p0000",
    ]


@pytest.mark.parametrize(
    "extra",
    [
        ["private-secret"],
        ["--unknown-private-secret"],
        ["--output"],
        ["--json=private-secret"],
    ],
)
def test_syntax_errors_emit_static_usage_only(extra: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == pr._STATIC_USAGE.encode("utf-8")
    assert b"private-secret" not in result.stderr


def test_refusals_do_not_leak_private_argument_or_fixture_details(
    tmp_path: Path,
) -> None:
    root = _prepare(tmp_path)
    (root / "private-secret.json").write_bytes(b'{"private-secret":')
    (root / "private-secret.json").chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--private-root",
            str(root),
            "--descriptor",
            "private-secret.json",
            "--output",
            "decisions.json",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert _error_code(result) == "descriptor_schema_refused"
    assert str(root).encode() not in result.stderr
    assert b"private-secret" not in result.stderr
    assert b"ctl-doc" not in result.stderr


def test_import_has_no_observable_io_side_effect(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            (
                "import os,runpy,sys;"
                f"sys.path.insert(0,{str(SCRIPTS)!r});"
                f"os.chdir({str(tmp_path)!r});"
                f"runpy.run_path({str(SCRIPT)!r},run_name='import_probe');"
                "print(sorted(os.listdir('.')))"
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout == b"[]\n"
    assert result.stderr == b""
