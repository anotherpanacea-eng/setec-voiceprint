from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1]

import passage_consumer_authority as consumer  # noqa: E402
from passage_tokenizer_v1 import load_data  # noqa: E402


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _args(root: Path, **changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "mode": "validate",
        "private_root": str(root),
        "descriptor": "descriptor.json",
        "checkpoint": "checkpoint",
        "projected_manifest": "projected.jsonl",
        "artifact": "artifact.json",
        "receipt": "receipt.json",
        "resume": False,
        "progress": False,
        "json": False,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def _adjacent(tmp_path: Path) -> tuple[Path, consumer.CompileTimeBindings]:
    adjacent = tmp_path / "adjacent"
    adjacent.mkdir()
    builder_raw = b"# synthetic builder fixture\n"
    verifier_raw = b"# synthetic verifier fixture\n"
    tokenizer_source = SCRIPT_DIR / consumer.TOKENIZER_IMPLEMENTATION_NAME
    tokenizer_data_source = SCRIPT_DIR / consumer.TOKENIZER_DATA_NAME
    tokenizer_raw = tokenizer_source.read_bytes()
    tokenizer_data_raw = tokenizer_data_source.read_bytes()
    (adjacent / consumer.BUILDER_NAME).write_bytes(builder_raw)
    (adjacent / consumer.VERIFIER_NAME).write_bytes(verifier_raw)
    shutil.copyfile(
        tokenizer_source, adjacent / consumer.TOKENIZER_IMPLEMENTATION_NAME,
    )
    shutil.copyfile(
        tokenizer_data_source, adjacent / consumer.TOKENIZER_DATA_NAME,
    )
    _ranges, _mappings, tokenizer_data = load_data(tokenizer_data_source)
    core = {
        "schema": consumer.PROFILE_SCHEMA,
        "producer_revision": "a" * 40,
        "producer_script_git_blob_oid": "sha1:" + "b" * 40,
        "producer_script_sha256": _digest("producer"),
        "algorithm_commitment_sha256": _digest("algorithm"),
        "tokenizer_implementation_git_blob_oid":
            consumer._blob(tokenizer_raw),
        "tokenizer_implementation_sha256": consumer._sha(tokenizer_raw),
        "tokenizer_data_git_blob_oid": consumer._blob(tokenizer_data_raw),
        "tokenizer_data_sha256": consumer._sha(tokenizer_data_raw),
        "tokenizer_data_commitment_sha256":
            tokenizer_data["data_commitment_sha256"],
        "authority_builder_git_blob_oid": consumer._blob(builder_raw),
        "authority_builder_script_sha256": consumer._sha(builder_raw),
        "verifier_git_blob_oid": consumer._blob(verifier_raw),
        "verifier_implementation_sha256": consumer._sha(verifier_raw),
    }
    profile = dict(core)
    profile["profile_commitment_sha256"] = consumer._sha(
        b"setec-passage-authority-profile-v1\n"
        + consumer.canonical_frame_v1(core)
    )
    profile_raw = consumer._canonical(profile)
    (adjacent / consumer.PROFILE_NAME).write_bytes(profile_raw)
    bindings = consumer.CompileTimeBindings(
        policy_status="unresolved",
        profile_artifact_sha256=consumer._sha(profile_raw),
        profile_commitment_sha256=profile["profile_commitment_sha256"],
        spec_sha256=_digest("spec"),
        review_sha256=_digest("review"),
    )
    return adjacent, bindings


def _private_inputs(
    tmp_path: Path,
) -> tuple[Path, dict[str, bytes], dict[str, object]]:
    root = tmp_path / "private"
    root.mkdir()
    root.chmod(0o700)
    members = {
        name: json.dumps(
            {"synthetic_member": name},
            sort_keys=True,
            separators=(",", ":"),
        ).encode() + b"\n"
        for name in consumer._DESCRIPTOR_MEMBERS
    }
    descriptor: dict[str, object] = {"schema": consumer.DESCRIPTOR_SCHEMA}
    for name, raw in members.items():
        path = f"{name}.json"
        (root / path).write_bytes(raw)
        (root / path).chmod(0o600)
        descriptor[name] = {"path": path, "sha256": consumer._sha(raw)}
    descriptor_raw = consumer._canonical(descriptor)
    (root / "descriptor.json").write_bytes(descriptor_raw)
    (root / "descriptor.json").chmod(0o600)
    return root, members, descriptor


def _replace_member(
    root: Path,
    descriptor: dict[str, object],
    name: str,
    raw: bytes,
) -> None:
    (root / f"{name}.json").write_bytes(raw)
    descriptor[name] = {
        "path": f"{name}.json",
        "sha256": consumer._sha(raw),
    }
    (root / "descriptor.json").write_bytes(consumer._canonical(descriptor))


def test_mint_unresolved_refuses_before_any_private_or_adjacent_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("admission must not run")

    monkeypatch.setattr(consumer, "admit_validate_inputs", forbidden)
    with pytest.raises(
        consumer.AuthorityError,
        match="^policy_unresolved_refused$",
    ):
        consumer.run(_args(tmp_path / "absent", mode="mint"))


def test_validate_admits_exact_profile_descriptor_paths_and_member_hashes(
    tmp_path: Path,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, members, descriptor = _private_inputs(tmp_path)

    admitted = consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert admitted.descriptor == descriptor
    assert admitted.member_bytes == members
    assert admitted.descriptor_input_hashes == {
        name: consumer._sha(raw) for name, raw in members.items()
    }
    checkpoint = root / "checkpoint"
    assert checkpoint.stat().st_mode & 0o777 == 0o700
    assert set(path.name for path in checkpoint.iterdir()) == {
        *consumer._PHASE_DIRS,
        "BINDING.a0.payload.json",
        "BINDING.a0.commit.json",
    }
    binding = json.loads(
        (checkpoint / "BINDING.a0.payload.json").read_bytes()
    )
    assert (
        admitted.checkpoint_binding_sha256
        == binding["checkpoint_binding_sha256"]
    )
    assert json.loads(
        (checkpoint / "BINDING.a0.commit.json").read_bytes()
    ) == {
        "schema": "setec-passage-checkpoint-binding-commit/1",
        "attempt": 0,
        "payload_sha256": consumer._sha(
            (checkpoint / "BINDING.a0.payload.json").read_bytes()
        ),
    }
    assert not (root / "projected.jsonl").exists()
    assert not (root / "artifact.json").exists()
    assert not (root / "receipt.json").exists()


def test_validate_resume_adopts_exact_checkpoint_binding(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    first = consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )
    before = {
        path.name: path.read_bytes()
        for path in (root / "checkpoint").iterdir()
        if path.is_file()
    }

    resumed = consumer.admit_validate_inputs(
        _args(root, resume=True),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert resumed.checkpoint_binding_sha256 == first.checkpoint_binding_sha256
    assert {
        path.name: path.read_bytes()
        for path in (root / "checkpoint").iterdir()
        if path.is_file()
    } == before


def test_validate_without_resume_refuses_foreign_checkpoint_file(
    tmp_path: Path,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    foreign = root / "checkpoint"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)

    with pytest.raises(consumer.AuthorityError, match="^checkpoint_refused$"):
        consumer.admit_validate_inputs(
            _args(root),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )

    assert foreign.read_bytes() == b"foreign\n"


def test_validate_resume_refuses_missing_checkpoint(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)

    with pytest.raises(consumer.AuthorityError, match="^checkpoint_refused$"):
        consumer.admit_validate_inputs(
            _args(root, resume=True),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_validate_resume_refuses_unknown_checkpoint_member(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )
    foreign = root / "checkpoint" / "foreign"
    foreign.write_bytes(b"foreign\n")
    foreign.chmod(0o600)

    with pytest.raises(consumer.AuthorityError, match="^checkpoint_refused$"):
        consumer.admit_validate_inputs(
            _args(root, resume=True),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_validate_resume_marker_without_payload_requires_recovery(
    tmp_path: Path,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )
    (root / "checkpoint" / "BINDING.a0.payload.json").unlink()

    with pytest.raises(
        consumer.AuthorityError,
        match="^checkpoint_recovery_required$",
    ):
        consumer.admit_validate_inputs(
            _args(root, resume=True),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_validate_resume_completes_exact_uncommitted_binding(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )
    marker = root / "checkpoint" / "BINDING.a0.commit.json"
    marker.unlink()

    consumer.admit_validate_inputs(
        _args(root, resume=True),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert marker.is_file()
    assert not (root / "checkpoint" / "BINDING.a1.payload.json").exists()


def test_validate_resume_advances_partial_binding_attempt(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )
    checkpoint = root / "checkpoint"
    (checkpoint / "BINDING.a0.payload.json").unlink()
    (checkpoint / "BINDING.a0.commit.json").unlink()
    partial = checkpoint / "BINDING.a0.payload.json"
    partial.write_bytes(b'{"partial":')
    partial.chmod(0o600)

    consumer.admit_validate_inputs(
        _args(root, resume=True),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert partial.read_bytes() == b'{"partial":'
    assert (checkpoint / "BINDING.a1.payload.json").is_file()
    assert (checkpoint / "BINDING.a1.commit.json").is_file()


@pytest.mark.parametrize("name", ["original_manifest", "snapshot_manifest"])
def test_validate_refuses_oversize_jsonl_line_before_member_hash(
    tmp_path: Path,
    name: str,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    oversized = b"x" * consumer.MAX_JSONL_LINE_BYTES + b"\n"
    (root / f"{name}.json").write_bytes(oversized)

    with pytest.raises(
        consumer.AuthorityError,
        match="^resource_limit_refused$",
    ):
        consumer.admit_validate_inputs(
            _args(root),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )
    assert not (root / "checkpoint").exists()


def test_validate_accepts_jsonl_line_at_resource_limit(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, descriptor = _private_inputs(tmp_path)
    boundary = b"x" * (consumer.MAX_JSONL_LINE_BYTES - 1) + b"\n"
    _replace_member(root, descriptor, "original_manifest", boundary)

    admitted = consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert admitted.member_bytes["original_manifest"] == boundary


def test_validate_embedded_bare_cr_does_not_split_oversize_jsonl_line(
    tmp_path: Path,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    hostile = b"x" * 600_000 + b"\r" + b"y" * 600_000 + b"\n"
    assert len(hostile) == 1_200_002
    (root / "original_manifest.json").write_bytes(hostile)

    with pytest.raises(
        consumer.AuthorityError,
        match="^resource_limit_refused$",
    ):
        consumer.admit_validate_inputs(
            _args(root),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )

    assert not (root / "checkpoint").exists()


def test_validate_accepts_boundary_jsonl_line_with_embedded_bare_cr(
    tmp_path: Path,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, descriptor = _private_inputs(tmp_path)
    left_size = consumer.MAX_JSONL_LINE_BYTES // 2
    right_size = consumer.MAX_JSONL_LINE_BYTES - left_size - 2
    boundary = b"x" * left_size + b"\r" + b"y" * right_size + b"\n"
    assert len(boundary) == consumer.MAX_JSONL_LINE_BYTES
    _replace_member(root, descriptor, "original_manifest", boundary)

    admitted = consumer.admit_validate_inputs(
        _args(root),
        adjacent_dir=adjacent,
        bindings=bindings,
        platform="darwin",
    )

    assert admitted.member_bytes["original_manifest"] == boundary


def test_validate_platform_refusal_precedes_adjacent_read(tmp_path: Path):
    with pytest.raises(consumer.AuthorityError, match="^platform_refused$"):
        consumer.admit_validate_inputs(
            _args(tmp_path / "absent"),
            adjacent_dir=tmp_path / "also-absent",
            bindings=consumer.COMPILED_BINDINGS,
            platform="linux",
        )


def test_default_validate_refuses_missing_reviewed_profile_before_private_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    class ForbiddenRoot:
        def __init__(self, *_args: object, **_kwargs: object):
            raise AssertionError("private root must remain unopened")

    monkeypatch.setattr(consumer, "PinnedPrivateRoot", ForbiddenRoot)
    with pytest.raises(
        consumer.AuthorityError,
        match="^authority_profile_refused$",
    ):
        # platform pinned: this test's subject is profile-refusal-before-
        # private-root ordering; the platform gate that precedes it in §15
        # order has its own test above, and the sys.platform default made
        # this assertion host-dependent (Linux CI saw platform_refused
        # first).
        consumer.run(_args(tmp_path / "absent"), platform="darwin")


def test_profile_byte_drift_refuses_before_private_root(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    profile = adjacent / consumer.PROFILE_NAME
    profile.write_bytes(profile.read_bytes() + b" ")
    with pytest.raises(
        consumer.AuthorityError,
        match="^authority_profile_refused$",
    ):
        consumer.admit_validate_inputs(
            _args(tmp_path / "absent"),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_profile_bound_builder_byte_drift_refuses(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    builder = adjacent / consumer.BUILDER_NAME
    builder.write_bytes(builder.read_bytes() + b"# drift\n")
    with pytest.raises(
        consumer.AuthorityError,
        match="^authority_profile_refused$",
    ):
        consumer.admit_validate_inputs(
            _args(tmp_path / "absent"),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


@pytest.mark.parametrize("mutation", ["extra", "duplicate", "noncanonical"])
def test_descriptor_closed_canonical_schema_refuses(
    tmp_path: Path,
    mutation: str,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, descriptor = _private_inputs(tmp_path)
    descriptor_path = root / "descriptor.json"
    if mutation == "extra":
        changed = copy.deepcopy(descriptor)
        changed["extra"] = False
        descriptor_path.write_bytes(consumer._canonical(changed))
    elif mutation == "duplicate":
        raw = descriptor_path.read_bytes()
        descriptor_path.write_bytes(
            raw[:-2] + b',"schema":"duplicate"}\n'
        )
    else:
        descriptor_path.write_bytes(
            json.dumps(descriptor, indent=2, sort_keys=True).encode() + b"\n"
        )

    with pytest.raises(
        consumer.AuthorityError,
        match="^descriptor_schema_refused$",
    ):
        consumer.admit_validate_inputs(
            _args(root),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_descriptor_member_hash_drift_refuses(tmp_path: Path):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    (root / "inventory.json").write_bytes(b'{"changed":true}\n')

    with pytest.raises(consumer.AuthorityError, match="^input_hash_refused$"):
        consumer.admit_validate_inputs(
            _args(root),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


@pytest.mark.parametrize(
    ("changed_name", "changed_value"),
    [
        ("checkpoint", "nested/checkpoint"),
        ("artifact", "INVENTORY.JSON"),
        ("receipt", "descriptor.json"),
    ],
)
def test_cli_and_descriptor_path_collisions_refuse(
    tmp_path: Path,
    changed_name: str,
    changed_value: str,
):
    adjacent, bindings = _adjacent(tmp_path)
    root, _members, _descriptor = _private_inputs(tmp_path)
    with pytest.raises(consumer.AuthorityError, match="^private_path_refused$"):
        consumer.admit_validate_inputs(
            _args(root, **{changed_name: changed_value}),
            adjacent_dir=adjacent,
            bindings=bindings,
            platform="darwin",
        )


def test_closed_cli_usage_and_error_object(capsys: pytest.CaptureFixture[str]):
    assert consumer.main(["--unknown"]) == 2
    usage = capsys.readouterr()
    assert usage.out == ""
    assert usage.err == consumer._STATIC_USAGE

    argv = [
        "--mode", "mint",
        "--private-root", "/does/not/exist",
        "--descriptor", "descriptor.json",
        "--checkpoint", "checkpoint",
        "--projected-manifest", "projected.jsonl",
        "--artifact", "artifact.json",
        "--receipt", "receipt.json",
        "--json",
    ]
    assert consumer.main(argv) == 3
    result = capsys.readouterr()
    assert result.out == ""
    assert json.loads(result.err) == {
        "schema": consumer.ERROR_SCHEMA,
        "status": "error",
        "code": "policy_unresolved_refused",
    }
