"""Closed Spec-80 consumer CLI and synthetic validation admission scaffold.

The ratified specification still lacks the reviewed adjacent authority profile
and frozen verifier required for real authority production.  Consequently the
compiled default remains unresolved: ``mint`` refuses before any private read,
and ``validate`` cannot pass adjacent-profile admission in the repository as
shipped.  The pure admission functions are intentionally injectable for
synthetic hostile tests; they do not mint or publish authority artifacts.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Mapping

from passage_remediation import (
    PinnedPrivateRoot,
    UsageError,
    _PrivateIOError,
    _RootPolicyError,
    _SafeParser,
    _OversizePrivateIOError,
)
from passage_source_population_commitment import canonical_frame_v1
from passage_tokenizer_v1 import TokenizerDataError, load_data
from reconstructibility_probe_set import (
    ProbeSetError,
    portable_collision_key,
    portable_private_relative_path_v1,
)


ERROR_SCHEMA = "setec-passage-consumer-authority-error/1"
DESCRIPTOR_SCHEMA = "setec-passage-consumer-authority-descriptor/1"
PROFILE_SCHEMA = "setec-passage-authority-profile/1"
PROFILE_NAME = "passage_consumer_authority_profile.json"
BUILDER_NAME = "passage_consumer_authority.py"
VERIFIER_NAME = "passage_verifier_v1.py"
TOKENIZER_IMPLEMENTATION_NAME = "passage_tokenizer_v1.py"
TOKENIZER_DATA_NAME = "passage_tokenizer_data_v1.json"

MAX_PROFILE_BYTES = 65_536
MAX_TOKENIZER_IMPLEMENTATION_BYTES = 1_048_576
MAX_TOKENIZER_DATA_BYTES = 16_777_216
MAX_DESCRIPTOR_BYTES = 65_536
MAX_NON_TEXT_INPUT_BYTES = 3_221_225_472
MAX_JSONL_LINE_BYTES = 1_048_576
MAX_CHECKPOINT_BYTES = 8_589_934_592
MAX_CHECKPOINT_FILES = 8_000_128
MAX_CHECKPOINT_SHARDS = 1_000_000
MAX_CHECKPOINT_MEMBER_BYTES = 16_777_216
RESOURCE_PROFILE = "passage-consumer-bounds-v1"

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BLOB = re.compile(r"sha1:[0-9a-f]{40}\Z")
_REVISION = re.compile(r"[0-9a-f]{40}\Z")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

_REFUSALS = {
    "policy_unresolved_refused",
    "platform_refused",
    "private_root_refused",
    "private_path_refused",
    "descriptor_schema_refused",
    "resource_limit_refused",
    "input_hash_refused",
    "checkpoint_refused",
    "checkpoint_recovery_required",
    "source_commitment_refused",
    "authority_profile_refused",
    "source_population_refused",
    "detection_replay_refused",
    "increment1_linkage_refused",
    "snapshot_refused",
    "projection_refused",
    "raw_confirmation_refused",
    "coverage_refused",
    "policy_attestation_refused",
    "boundary_invariant_refused",
    "derivation_invariant_refused",
    "output_recovery_required",
    "projected_manifest_publication_refused",
    "artifact_publication_refused",
    "receipt_publication_refused",
}
_DESCRIPTOR_MEMBERS = (
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
)
_MEMBER_CAPS = {
    "inventory": 67_108_864,
    "increment1_descriptor": 65_536,
    "increment1_projection_receipt": 16_777_216,
    "increment1_artifact": 268_435_456,
    "increment1_receipt": 16_777_216,
    "source_population_commitment": 268_435_456,
    "source_population_receipt": 65_536,
    "original_manifest": 268_435_456,
    "author_corpus_export_receipt": 268_435_456,
    "snapshot_manifest": 268_435_456,
    "snapshot_metadata": 268_435_456,
    "source_lineage_crosswalk": 536_870_912,
    "snapshot_attestation": 1_048_576,
    "consumer_policy_attestation": 1_048_576,
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
_PHASE_DIRS = (
    "00-source_admission",
    "01-passage_tokenization",
    "02-stage_a_candidates",
    "03-stage_b_postings",
    "04-report_comparison",
    "05-crosswalk_projection",
    "06-evidence_projection",
    "07-unit_derivation",
    "08-output_serialization",
)
_BINDING_PAYLOAD_KEYS = {
    "schema",
    "checkpoint_binding_sha256",
    "input_hashes",
    "authority_profile_artifact_sha256",
    "authority_profile_commitment_sha256",
    "spec_sha256",
    "review_sha256",
    "tokenizer_implementation_sha256",
    "tokenizer_data_sha256",
    "tokenizer_data_commitment_sha256",
    "policy_attestation_sha256",
    "resource_profile",
    "output_names",
}
_BINDING_COMMIT_KEYS = {"schema", "attempt", "payload_sha256"}
_BINDING_NAME = re.compile(
    r"BINDING\.a([0-3])\.(payload\.json|commit\.json)\Z"
)
_TERMINAL_NAME = re.compile(r"CHECKPOINT\.a[0-3]\.complete\.json\Z")
_SHARD_NAME = re.compile(
    r"s([0-9]{12})\.a[0-3]\.(payload|commit\.json)\Z"
)
_PHASE_MARKER_NAME = re.compile(r"PHASE\.a[0-3]\.done\.json\Z")


class AuthorityError(Exception):
    def __init__(self, code: str):
        if code not in _REFUSALS:
            raise ValueError("unknown authority refusal")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CompileTimeBindings:
    policy_status: str
    profile_artifact_sha256: str | None
    profile_commitment_sha256: str | None
    spec_sha256: str | None
    review_sha256: str | None


# No reviewed final profile or compile-time spec/review bindings exist at this
# revision.  These are deliberately absent rather than invented.
COMPILED_BINDINGS = CompileTimeBindings(
    policy_status="unresolved",
    profile_artifact_sha256=None,
    profile_commitment_sha256=None,
    spec_sha256=None,
    review_sha256=None,
)


@dataclass(frozen=True)
class AdjacentAuthority:
    profile: dict[str, Any]
    profile_bytes: bytes
    profile_artifact_sha256: str


@dataclass(frozen=True)
class ValidateAdmission:
    authority: AdjacentAuthority
    descriptor: dict[str, Any]
    descriptor_input_hashes: dict[str, str]
    member_bytes: dict[str, bytes]
    checkpoint_binding_sha256: str


_STATIC_USAGE = (
    "usage: passage_consumer_authority --mode validate|mint "
    "--private-root PRIVATE_ROOT --descriptor RELATIVE_DESCRIPTOR.json "
    "--checkpoint RELATIVE_CHECKPOINT "
    "--projected-manifest RELATIVE_POPULATION.jsonl "
    "--artifact RELATIVE_ARTIFACT.json --receipt RELATIVE_RECEIPT.json "
    "[--resume] [--progress] [--json]\n"
)


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _blob(raw: bytes) -> str:
    header = b"blob " + str(len(raw)).encode("ascii") + b"\0"
    return "sha1:" + hashlib.sha1(header + raw).hexdigest()


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
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise AuthorityError("descriptor_schema_refused") from exc


def _strict_object(raw: bytes, *, maximum: int, code: str) -> dict[str, Any]:
    if len(raw) > maximum:
        raise AuthorityError("resource_limit_refused")

    def reject_constant(_value: str) -> None:
        raise ValueError("nonfinite")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
        if type(value) is not dict or _canonical(value) != raw:
            raise ValueError("noncanonical")
    except (
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise AuthorityError(code) from exc
    return value


def _check_jsonl_lines(raw: bytes) -> None:
    start = 0
    while True:
        newline = raw.find(b"\n", start)
        if newline < 0:
            if len(raw) - start > MAX_JSONL_LINE_BYTES:
                raise AuthorityError("resource_limit_refused")
            return
        if newline + 1 - start > MAX_JSONL_LINE_BYTES:
            raise AuthorityError("resource_limit_refused")
        start = newline + 1


def _optional_flag(name: str) -> int:
    return int(getattr(os, name, 0))


def _posix_identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _checkpoint_dir_flags() -> int:
    return (
        os.O_RDONLY
        | _optional_flag("O_DIRECTORY")
        | _optional_flag("O_NOFOLLOW")
        | _optional_flag("O_CLOEXEC")
    )


def _checkpoint_file_flags() -> int:
    return os.O_RDONLY | _optional_flag("O_NOFOLLOW") | _optional_flag("O_CLOEXEC")


def _require_private_directory(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or int(info.st_uid) != int(os.geteuid())
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AuthorityError("checkpoint_refused")


def _require_private_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or int(info.st_uid) != int(os.geteuid())
        or int(info.st_nlink) != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or int(info.st_size) < 0
    ):
        raise AuthorityError("checkpoint_refused")


def _stable_private_file_stat(
    directory_fd: int,
    name: str,
) -> os.stat_result:
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_private_file(before)
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        fingerprint = lambda value: (
            int(value.st_dev),
            int(value.st_ino),
            int(value.st_size),
            int(value.st_mtime_ns),
            int(value.st_ctime_ns),
            int(value.st_nlink),
            int(stat.S_IFMT(value.st_mode)),
            int(stat.S_IMODE(value.st_mode)),
            int(value.st_uid),
        )
        if fingerprint(before) != fingerprint(after):
            raise OSError("unstable checkpoint member")
        return after
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError("checkpoint_refused") from exc


def _revalidate_checkpoint(
    root: PinnedPrivateRoot,
    checkpoint_name: str,
    checkpoint_fd: int,
) -> None:
    root.barrier()
    try:
        named = os.stat(
            checkpoint_name,
            dir_fd=root.root_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(checkpoint_fd)
    except OSError as exc:
        raise AuthorityError("checkpoint_recovery_required") from exc
    _require_private_directory(named)
    _require_private_directory(opened)
    if _posix_identity(named) != _posix_identity(opened):
        raise AuthorityError("checkpoint_recovery_required")


def _open_checkpoint_directory(
    root: PinnedPrivateRoot,
    checkpoint_name: str,
) -> int:
    try:
        named = os.stat(
            checkpoint_name,
            dir_fd=root.root_descriptor,
            follow_symlinks=False,
        )
        _require_private_directory(named)
        descriptor = os.open(
            checkpoint_name,
            _checkpoint_dir_flags(),
            dir_fd=root.root_descriptor,
        )
        opened = os.fstat(descriptor)
        _require_private_directory(opened)
        if _posix_identity(named) != _posix_identity(opened):
            raise OSError("checkpoint rebound")
        return descriptor
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError("checkpoint_refused") from exc


def _create_private_directory(
    parent_fd: int,
    name: str,
    *,
    recovery: bool,
) -> int:
    created = False
    descriptor = -1
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
        descriptor = os.open(name, _checkpoint_dir_flags(), dir_fd=parent_fd)
        os.fchmod(descriptor, 0o700)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(descriptor)
        _require_private_directory(named)
        _require_private_directory(opened)
        if _posix_identity(named) != _posix_identity(opened):
            raise OSError("created directory rebound")
        os.fsync(descriptor)
        os.fsync(parent_fd)
        return descriptor
    except AuthorityError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if created or recovery:
            raise AuthorityError("checkpoint_recovery_required") from exc
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        if created or recovery:
            raise AuthorityError("checkpoint_recovery_required") from exc
        raise AuthorityError("checkpoint_refused") from exc


def _read_checkpoint_file(
    directory_fd: int,
    name: str,
    *,
    maximum: int = MAX_CHECKPOINT_MEMBER_BYTES,
) -> bytes:
    descriptor = -1
    try:
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _require_private_file(before)
        if int(before.st_size) > maximum:
            raise AuthorityError("checkpoint_refused")
        descriptor = os.open(name, _checkpoint_file_flags(), dir_fd=directory_fd)
        opened = os.fstat(descriptor)
        _require_private_file(opened)
        if _posix_identity(before) != _posix_identity(opened):
            raise OSError("checkpoint member rebound")
        remaining = int(opened.st_size)
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(1_048_576, remaining))
            if not chunk:
                raise OSError("short checkpoint member")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("growing checkpoint member")
        after = os.fstat(descriptor)
        named_after = os.stat(
            name, dir_fd=directory_fd, follow_symlinks=False,
        )
        fingerprint = lambda value: (
            int(value.st_dev),
            int(value.st_ino),
            int(value.st_size),
            int(value.st_mtime_ns),
            int(value.st_ctime_ns),
            int(value.st_nlink),
            int(stat.S_IFMT(value.st_mode)),
            int(stat.S_IMODE(value.st_mode)),
            int(value.st_uid),
        )
        if (
            fingerprint(opened) != fingerprint(after)
            or fingerprint(after) != fingerprint(named_after)
        ):
            raise OSError("mutating checkpoint member")
        return b"".join(chunks)
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError("checkpoint_refused") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish_checkpoint_file(
    root: PinnedPrivateRoot,
    checkpoint_name: str,
    checkpoint_fd: int,
    name: str,
    payload: bytes,
) -> None:
    descriptor = -1
    created = False
    try:
        _revalidate_checkpoint(root, checkpoint_name, checkpoint_fd)
        descriptor = os.open(
            name,
            (
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | _optional_flag("O_NOFOLLOW")
                | _optional_flag("O_CLOEXEC")
            ),
            0o600,
            dir_fd=checkpoint_fd,
        )
        created = True
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("checkpoint write")
            view = view[written:]
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
        _require_private_file(opened)
        if int(opened.st_size) != len(payload):
            raise OSError("checkpoint size")
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = len(payload)
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise OSError("checkpoint short readback")
            chunks.append(chunk)
            remaining -= len(chunk)
        if b"".join(chunks) != payload:
            raise OSError("checkpoint readback")
        os.fsync(checkpoint_fd)
        named = os.stat(name, dir_fd=checkpoint_fd, follow_symlinks=False)
        _require_private_file(named)
        if (
            _posix_identity(named) != _posix_identity(opened)
            or int(named.st_size) != len(payload)
        ):
            raise OSError("checkpoint published rebound")
        _revalidate_checkpoint(root, checkpoint_name, checkpoint_fd)
    except (AuthorityError, OSError) as exc:
        if created:
            raise AuthorityError("checkpoint_recovery_required") from exc
        if isinstance(exc, AuthorityError):
            raise
        raise AuthorityError("checkpoint_recovery_required") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _scan_checkpoint(
    checkpoint_fd: int,
) -> set[str]:
    try:
        root_names = set(os.listdir(checkpoint_fd))
    except OSError as exc:
        raise AuthorityError("checkpoint_refused") from exc
    allowed_root = set(_PHASE_DIRS)
    file_count = 0
    byte_count = 0
    shard_ids: set[tuple[str, str]] = set()

    for name in root_names:
        try:
            info = os.stat(name, dir_fd=checkpoint_fd, follow_symlinks=False)
        except OSError as exc:
            raise AuthorityError("checkpoint_refused") from exc
        if name in allowed_root:
            _require_private_directory(info)
            continue
        if not (_BINDING_NAME.fullmatch(name) or _TERMINAL_NAME.fullmatch(name)):
            raise AuthorityError("checkpoint_refused")
        info = _stable_private_file_stat(checkpoint_fd, name)
        file_count += 1
        byte_count += int(info.st_size)

    if not allowed_root.issubset(root_names):
        raise AuthorityError("checkpoint_refused")

    for phase in _PHASE_DIRS:
        phase_fd = -1
        try:
            named = os.stat(
                phase, dir_fd=checkpoint_fd, follow_symlinks=False,
            )
            _require_private_directory(named)
            phase_fd = os.open(
                phase, _checkpoint_dir_flags(), dir_fd=checkpoint_fd,
            )
            opened = os.fstat(phase_fd)
            _require_private_directory(opened)
            if _posix_identity(named) != _posix_identity(opened):
                raise OSError("phase rebound")
            phase_names = set(os.listdir(phase_fd))
            payload_names: set[str] = set()
            commit_names: set[str] = set()
            for name in phase_names:
                match = _SHARD_NAME.fullmatch(name)
                if match:
                    shard_ids.add((phase, match.group(1)))
                    if match.group(2) == "payload":
                        payload_names.add(name)
                    else:
                        commit_names.add(name)
                elif not _PHASE_MARKER_NAME.fullmatch(name):
                    raise AuthorityError("checkpoint_refused")
                info = _stable_private_file_stat(phase_fd, name)
                if int(info.st_size) > MAX_CHECKPOINT_MEMBER_BYTES:
                    raise AuthorityError("checkpoint_refused")
                file_count += 1
                byte_count += int(info.st_size)
            for commit_name in commit_names:
                payload_name = commit_name.replace(".commit.json", ".payload")
                if payload_name not in payload_names:
                    raise AuthorityError("checkpoint_recovery_required")
        except AuthorityError:
            raise
        except OSError as exc:
            raise AuthorityError("checkpoint_refused") from exc
        finally:
            if phase_fd >= 0:
                os.close(phase_fd)

    for attempt in range(4):
        marker = f"BINDING.a{attempt}.commit.json"
        payload = f"BINDING.a{attempt}.payload.json"
        if marker in root_names and payload not in root_names:
            raise AuthorityError("checkpoint_recovery_required")
    if (
        file_count > MAX_CHECKPOINT_FILES
        or byte_count > MAX_CHECKPOINT_BYTES
        or len(shard_ids) > MAX_CHECKPOINT_SHARDS
    ):
        raise AuthorityError("checkpoint_refused")
    return root_names


def _binding_payload(
    args: argparse.Namespace,
    authority: AdjacentAuthority,
    bindings: CompileTimeBindings,
    input_hashes: Mapping[str, str],
) -> tuple[dict[str, Any], bytes, str]:
    output_names = {
        "artifact": args.artifact,
        "projected_manifest": args.projected_manifest,
        "receipt": args.receipt,
    }
    core = {
        "descriptor_input_hashes": dict(input_hashes),
        "authority_profile_artifact_sha256":
            authority.profile_artifact_sha256,
        "authority_profile_commitment_sha256":
            authority.profile["profile_commitment_sha256"],
        "tokenizer_implementation_sha256":
            authority.profile["tokenizer_implementation_sha256"],
        "tokenizer_data_sha256":
            authority.profile["tokenizer_data_sha256"],
        "tokenizer_data_commitment_sha256":
            authority.profile["tokenizer_data_commitment_sha256"],
        "spec_sha256": bindings.spec_sha256,
        "review_sha256": bindings.review_sha256,
        "policy_attestation_sha256":
            input_hashes["consumer_policy_attestation"],
        "resource_profile": RESOURCE_PROFILE,
        "projected_manifest_name": args.projected_manifest,
        "artifact_name": args.artifact,
        "receipt_name": args.receipt,
    }
    digest = _sha(
        b"setec-passage-consumer-checkpoint-binding-v1\n"
        + canonical_frame_v1(core)
    )
    payload = {
        "schema": "setec-passage-checkpoint-binding/1",
        "checkpoint_binding_sha256": digest,
        "input_hashes": dict(input_hashes),
        "authority_profile_artifact_sha256":
            authority.profile_artifact_sha256,
        "authority_profile_commitment_sha256":
            authority.profile["profile_commitment_sha256"],
        "spec_sha256": bindings.spec_sha256,
        "review_sha256": bindings.review_sha256,
        "tokenizer_implementation_sha256":
            authority.profile["tokenizer_implementation_sha256"],
        "tokenizer_data_sha256":
            authority.profile["tokenizer_data_sha256"],
        "tokenizer_data_commitment_sha256":
            authority.profile["tokenizer_data_commitment_sha256"],
        "policy_attestation_sha256":
            input_hashes["consumer_policy_attestation"],
        "resource_profile": RESOURCE_PROFILE,
        "output_names": output_names,
    }
    return payload, _canonical(payload), digest


def _valid_binding_payload(value: Mapping[str, Any]) -> bool:
    return (
        set(value) == _BINDING_PAYLOAD_KEYS
        and value.get("schema") == "setec-passage-checkpoint-binding/1"
        and all(
            _DIGEST.fullmatch(value.get(name, ""))
            for name in (
                "checkpoint_binding_sha256",
                "authority_profile_artifact_sha256",
                "authority_profile_commitment_sha256",
                "spec_sha256",
                "review_sha256",
                "tokenizer_implementation_sha256",
                "tokenizer_data_sha256",
                "tokenizer_data_commitment_sha256",
                "policy_attestation_sha256",
            )
        )
        and type(value.get("input_hashes")) is dict
        and set(value["input_hashes"]) == set(_DESCRIPTOR_MEMBERS)
        and all(
            _DIGEST.fullmatch(item)
            for item in value["input_hashes"].values()
            if type(item) is str
        )
        and all(type(item) is str for item in value["input_hashes"].values())
        and value.get("resource_profile") == RESOURCE_PROFILE
        and type(value.get("output_names")) is dict
        and set(value["output_names"])
        == {"artifact", "projected_manifest", "receipt"}
        and all(type(item) is str for item in value["output_names"].values())
    )


def _admit_binding_attempts(
    root: PinnedPrivateRoot,
    checkpoint_name: str,
    checkpoint_fd: int,
    root_names: set[str],
    expected_payload: bytes,
) -> None:
    attempts: list[tuple[int, bytes, bool, bool]] = []
    seen_gap = False
    exact_committed: list[int] = []
    exact_uncommitted: list[int] = []
    for attempt in range(4):
        payload_name = f"BINDING.a{attempt}.payload.json"
        commit_name = f"BINDING.a{attempt}.commit.json"
        has_payload = payload_name in root_names
        has_commit = commit_name in root_names
        if has_commit and not has_payload:
            raise AuthorityError("checkpoint_recovery_required")
        if not has_payload:
            seen_gap = True
            continue
        if seen_gap:
            raise AuthorityError("checkpoint_recovery_required")
        payload_raw = _read_checkpoint_file(checkpoint_fd, payload_name)
        try:
            payload_value = _strict_object(
                payload_raw,
                maximum=MAX_CHECKPOINT_MEMBER_BYTES,
                code="checkpoint_refused",
            )
        except AuthorityError:
            payload_value = None
        payload_valid = (
            payload_value is not None
            and _valid_binding_payload(payload_value)
        )
        exact = payload_valid and payload_raw == expected_payload
        if has_commit:
            commit_raw = _read_checkpoint_file(checkpoint_fd, commit_name)
            try:
                commit = _strict_object(
                    commit_raw,
                    maximum=MAX_CHECKPOINT_MEMBER_BYTES,
                    code="checkpoint_refused",
                )
            except AuthorityError:
                commit = None
            commit_valid = (
                commit is not None
                and set(commit) == _BINDING_COMMIT_KEYS
                and commit.get("schema")
                == "setec-passage-checkpoint-binding-commit/1"
                and commit.get("attempt") == attempt
                and commit.get("payload_sha256") == _sha(payload_raw)
            )
            if commit_valid:
                if not payload_valid or not exact:
                    raise AuthorityError("checkpoint_refused")
                exact_committed.append(attempt)
        elif exact:
            exact_uncommitted.append(attempt)
        attempts.append((attempt, payload_raw, has_commit, exact))

    if exact_committed:
        first = exact_committed[0]
        if (
            len(exact_committed) != 1
            or exact_uncommitted
            or any(attempt > first for attempt, *_rest in attempts)
        ):
            raise AuthorityError("checkpoint_recovery_required")
        return
    if exact_uncommitted:
        attempt = exact_uncommitted[0]
        if (
            len(exact_uncommitted) != 1
            or any(index > attempt for index, *_rest in attempts)
        ):
            raise AuthorityError("checkpoint_recovery_required")
        payload_name = f"BINDING.a{attempt}.payload.json"
        payload_raw = _read_checkpoint_file(checkpoint_fd, payload_name)
        commit = _canonical({
            "schema": "setec-passage-checkpoint-binding-commit/1",
            "attempt": attempt,
            "payload_sha256": _sha(payload_raw),
        })
        _publish_checkpoint_file(
            root, checkpoint_name, checkpoint_fd,
            f"BINDING.a{attempt}.commit.json", commit,
        )
        return

    next_attempt = len(attempts)
    if next_attempt >= 4:
        raise AuthorityError("checkpoint_recovery_required")
    payload_name = f"BINDING.a{next_attempt}.payload.json"
    commit_name = f"BINDING.a{next_attempt}.commit.json"
    _publish_checkpoint_file(
        root, checkpoint_name, checkpoint_fd,
        payload_name, expected_payload,
    )
    commit = _canonical({
        "schema": "setec-passage-checkpoint-binding-commit/1",
        "attempt": next_attempt,
        "payload_sha256": _sha(expected_payload),
    })
    _publish_checkpoint_file(
        root, checkpoint_name, checkpoint_fd, commit_name, commit,
    )


def _admit_checkpoint(
    root: PinnedPrivateRoot,
    args: argparse.Namespace,
    authority: AdjacentAuthority,
    bindings: CompileTimeBindings,
    input_hashes: Mapping[str, str],
    checkpoint_name: str,
) -> str:
    checkpoint_fd = -1
    created = False
    try:
        root.barrier()
        try:
            os.stat(
                checkpoint_name,
                dir_fd=root.root_descriptor,
                follow_symlinks=False,
            )
            exists = True
        except FileNotFoundError:
            exists = False
        except OSError as exc:
            raise AuthorityError("checkpoint_refused") from exc

        if not args.resume:
            if exists:
                raise AuthorityError("checkpoint_refused")
            checkpoint_fd = _create_private_directory(
                root.root_descriptor, checkpoint_name, recovery=False,
            )
            created = True
            for phase in _PHASE_DIRS:
                phase_fd = _create_private_directory(
                    checkpoint_fd, phase, recovery=True,
                )
                os.close(phase_fd)
            os.fsync(checkpoint_fd)
            os.fsync(root.root_descriptor)
            _revalidate_checkpoint(root, checkpoint_name, checkpoint_fd)
        else:
            if not exists:
                raise AuthorityError("checkpoint_refused")
            checkpoint_fd = _open_checkpoint_directory(root, checkpoint_name)

        root_names = _scan_checkpoint(checkpoint_fd)
        _payload, payload_raw, digest = _binding_payload(
            args, authority, bindings, input_hashes,
        )
        _admit_binding_attempts(
            root, checkpoint_name, checkpoint_fd, root_names,
            payload_raw,
        )
        _revalidate_checkpoint(root, checkpoint_name, checkpoint_fd)
        return digest
    except AuthorityError as exc:
        if created and exc.code == "checkpoint_refused":
            raise AuthorityError("checkpoint_recovery_required") from exc
        raise
    finally:
        if checkpoint_fd >= 0:
            os.close(checkpoint_fd)


def _portable(value: Any) -> tuple[str, ...]:
    try:
        return portable_private_relative_path_v1(value)
    except (ProbeSetError, TypeError, ValueError, UnicodeError) as exc:
        raise AuthorityError("private_path_refused") from exc


def _stable_adjacent(path: Path, maximum: int) -> bytes:
    try:
        before = path.stat(follow_symlinks=False)
        if before.st_size > maximum:
            raise AuthorityError("resource_limit_refused")
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            raise OSError("unsafe adjacent file")
        raw = path.read_bytes()
        after = path.stat(follow_symlinks=False)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
            value.st_nlink,
            stat.S_IFMT(value.st_mode),
        )
        if len(raw) != before.st_size or identity(before) != identity(after):
            raise OSError("unstable adjacent file")
        return raw
    except AuthorityError:
        raise
    except OSError as exc:
        raise AuthorityError("authority_profile_refused") from exc


def admit_adjacent_authority(
    adjacent_dir: Path,
    bindings: CompileTimeBindings,
) -> AdjacentAuthority:
    """Admit the fixed adjacent profile and all local profile-bound bytes."""
    profile_raw = _stable_adjacent(
        adjacent_dir / PROFILE_NAME, MAX_PROFILE_BYTES,
    )
    tokenizer_raw = _stable_adjacent(
        adjacent_dir / TOKENIZER_IMPLEMENTATION_NAME,
        MAX_TOKENIZER_IMPLEMENTATION_BYTES,
    )
    tokenizer_data_raw = _stable_adjacent(
        adjacent_dir / TOKENIZER_DATA_NAME, MAX_TOKENIZER_DATA_BYTES,
    )
    if (
        len(profile_raw) + len(tokenizer_raw) + len(tokenizer_data_raw)
        > MAX_NON_TEXT_INPUT_BYTES
    ):
        raise AuthorityError("resource_limit_refused")
    if (
        bindings.policy_status not in {"unresolved", "ratified_reviewed"}
        or not _DIGEST.fullmatch(bindings.profile_artifact_sha256 or "")
        or not _DIGEST.fullmatch(bindings.profile_commitment_sha256 or "")
        or not _DIGEST.fullmatch(bindings.spec_sha256 or "")
        or not _DIGEST.fullmatch(bindings.review_sha256 or "")
        or _sha(profile_raw) != bindings.profile_artifact_sha256
    ):
        raise AuthorityError("authority_profile_refused")
    profile = _strict_object(
        profile_raw, maximum=MAX_PROFILE_BYTES,
        code="authority_profile_refused",
    )
    if (
        set(profile) != _PROFILE_KEYS
        or profile.get("schema") != PROFILE_SCHEMA
        or not _REVISION.fullmatch(profile.get("producer_revision", ""))
        or any(
            not _BLOB.fullmatch(profile.get(field, ""))
            for field in (
                "producer_script_git_blob_oid",
                "tokenizer_implementation_git_blob_oid",
                "tokenizer_data_git_blob_oid",
                "authority_builder_git_blob_oid",
                "verifier_git_blob_oid",
            )
        )
        or any(
            not _DIGEST.fullmatch(profile.get(field, ""))
            for field in (
                "producer_script_sha256",
                "algorithm_commitment_sha256",
                "tokenizer_implementation_sha256",
                "tokenizer_data_sha256",
                "tokenizer_data_commitment_sha256",
                "authority_builder_script_sha256",
                "verifier_implementation_sha256",
                "profile_commitment_sha256",
            )
        )
    ):
        raise AuthorityError("authority_profile_refused")
    core = dict(profile)
    del core["profile_commitment_sha256"]
    commitment = _sha(
        b"setec-passage-authority-profile-v1\n"
        + canonical_frame_v1(core)
    )
    if (
        commitment != profile["profile_commitment_sha256"]
        or commitment != bindings.profile_commitment_sha256
    ):
        raise AuthorityError("authority_profile_refused")

    builder_raw = _stable_adjacent(
        adjacent_dir / BUILDER_NAME, MAX_NON_TEXT_INPUT_BYTES,
    )
    verifier_raw = _stable_adjacent(
        adjacent_dir / VERIFIER_NAME, MAX_NON_TEXT_INPUT_BYTES,
    )
    if (
        profile["tokenizer_implementation_git_blob_oid"] != _blob(tokenizer_raw)
        or profile["tokenizer_implementation_sha256"] != _sha(tokenizer_raw)
        or profile["tokenizer_data_git_blob_oid"] != _blob(tokenizer_data_raw)
        or profile["tokenizer_data_sha256"] != _sha(tokenizer_data_raw)
        or profile["authority_builder_git_blob_oid"] != _blob(builder_raw)
        or profile["authority_builder_script_sha256"] != _sha(builder_raw)
        or profile["verifier_git_blob_oid"] != _blob(verifier_raw)
        or profile["verifier_implementation_sha256"] != _sha(verifier_raw)
    ):
        raise AuthorityError("authority_profile_refused")
    try:
        _ranges, _mappings, tokenizer_data = load_data(
            adjacent_dir / TOKENIZER_DATA_NAME
        )
    except (OSError, TokenizerDataError) as exc:
        raise AuthorityError("authority_profile_refused") from exc
    if (
        tokenizer_data["data_commitment_sha256"]
        != profile["tokenizer_data_commitment_sha256"]
    ):
        raise AuthorityError("authority_profile_refused")
    return AdjacentAuthority(
        profile=profile,
        profile_bytes=profile_raw,
        profile_artifact_sha256=_sha(profile_raw),
    )


def validate_descriptor(value: Any) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != {"schema", *_DESCRIPTOR_MEMBERS}
        or value.get("schema") != DESCRIPTOR_SCHEMA
    ):
        raise AuthorityError("descriptor_schema_refused")
    for name in _DESCRIPTOR_MEMBERS:
        member = value[name]
        if (
            type(member) is not dict
            or set(member) != {"path", "sha256"}
            or type(member["path"]) is not str
            or not _DIGEST.fullmatch(member["sha256"])
        ):
            raise AuthorityError("descriptor_schema_refused")
    return value


def _validate_all_paths(
    args: argparse.Namespace,
    descriptor: Mapping[str, Any] | None = None,
) -> dict[str, tuple[str, ...]]:
    named = {
        "descriptor": _portable(args.descriptor),
        "checkpoint": _portable(args.checkpoint),
        "projected_manifest": _portable(args.projected_manifest),
        "artifact": _portable(args.artifact),
        "receipt": _portable(args.receipt),
    }
    for name in ("checkpoint", "projected_manifest", "artifact", "receipt"):
        if len(named[name]) != 1:
            raise AuthorityError("private_path_refused")
    if descriptor is not None:
        for name in _DESCRIPTOR_MEMBERS:
            named[name] = _portable(descriptor[name]["path"])
    values = list(named.values())
    if (
        len(set(values)) != len(values)
        or len({portable_collision_key(value) for value in values})
        != len(values)
    ):
        raise AuthorityError("private_path_refused")
    return named


def admit_validate_inputs(
    args: argparse.Namespace,
    *,
    adjacent_dir: Path,
    bindings: CompileTimeBindings,
    platform: str,
) -> ValidateAdmission:
    """Execute the implemented nonpublication admission prefix in §15 order."""
    if platform != "darwin" or os.name != "posix":
        raise AuthorityError("platform_refused")
    authority = admit_adjacent_authority(adjacent_dir, bindings)
    try:
        with PinnedPrivateRoot(args.private_root) as root:
            preliminary = _validate_all_paths(args)
            try:
                descriptor_raw = root.read_private_member(
                    preliminary["descriptor"], MAX_DESCRIPTOR_BYTES,
                )
            except _OversizePrivateIOError as exc:
                raise AuthorityError("resource_limit_refused") from exc
            except _PrivateIOError as exc:
                raise AuthorityError("descriptor_schema_refused") from exc
            descriptor = validate_descriptor(
                _strict_object(
                    descriptor_raw,
                    maximum=MAX_DESCRIPTOR_BYTES,
                    code="descriptor_schema_refused",
                )
            )
            paths = _validate_all_paths(args, descriptor)
            members: dict[str, bytes] = {}
            total = (
                len(authority.profile_bytes)
                + len(_stable_adjacent(
                    adjacent_dir / TOKENIZER_IMPLEMENTATION_NAME,
                    MAX_TOKENIZER_IMPLEMENTATION_BYTES,
                ))
                + len(_stable_adjacent(
                    adjacent_dir / TOKENIZER_DATA_NAME,
                    MAX_TOKENIZER_DATA_BYTES,
                ))
            )
            for name in _DESCRIPTOR_MEMBERS:
                try:
                    raw = root.read_private_member(paths[name], _MEMBER_CAPS[name])
                except _OversizePrivateIOError as exc:
                    raise AuthorityError("resource_limit_refused") from exc
                except _PrivateIOError as exc:
                    raise AuthorityError("input_hash_refused") from exc
                total += len(raw)
                if total > MAX_NON_TEXT_INPUT_BYTES:
                    raise AuthorityError("resource_limit_refused")
                if name in {"original_manifest", "snapshot_manifest"}:
                    _check_jsonl_lines(raw)
                members[name] = raw
            input_hashes = {
                name: _sha(raw) for name, raw in members.items()
            }
            if any(
                input_hashes[name] != descriptor[name]["sha256"]
                for name in _DESCRIPTOR_MEMBERS
            ):
                raise AuthorityError("input_hash_refused")
            checkpoint_binding_sha256 = _admit_checkpoint(
                root,
                args,
                authority,
                bindings,
                input_hashes,
                paths["checkpoint"][0],
            )
            return ValidateAdmission(
                authority=authority,
                descriptor=descriptor,
                descriptor_input_hashes=input_hashes,
                member_bytes=members,
                checkpoint_binding_sha256=checkpoint_binding_sha256,
            )
    except _RootPolicyError as exc:
        raise AuthorityError("private_root_refused") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(add_help=False)
    parser.add_argument("--mode", choices=("validate", "mint"), required=True)
    parser.add_argument("--private-root", required=True)
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--projected-manifest", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--help", action="store_true")
    return parser


def run(
    args: argparse.Namespace,
    *,
    adjacent_dir: Path = Path(__file__).resolve().parent,
    bindings: CompileTimeBindings = COMPILED_BINDINGS,
    platform: str = sys.platform,
) -> None:
    if args.mode == "mint" and bindings.policy_status != "ratified_reviewed":
        raise AuthorityError("policy_unresolved_refused")
    admit_validate_inputs(
        args,
        adjacent_dir=adjacent_dir,
        bindings=bindings,
        platform=platform,
    )
    # The next live gate requires the not-yet-built frozen verifier/checkpoint
    # implementation.  Never report validation success for this partial prefix.
    raise AuthorityError("source_commitment_refused")


def _write_error(code: str) -> None:
    payload = _canonical({
        "schema": ERROR_SCHEMA,
        "status": "error",
        "code": code,
    })
    stream = sys.stderr.buffer if hasattr(sys.stderr, "buffer") else sys.stderr
    try:
        stream.write(payload)
    except TypeError:
        stream.write(payload.decode("utf-8"))
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv == ["--help"]:
        sys.stdout.write(_STATIC_USAGE)
        return 0
    try:
        args = _build_parser().parse_args(raw_argv)
    except UsageError:
        sys.stderr.write(_STATIC_USAGE)
        return 2
    if args.help:
        sys.stdout.write(_STATIC_USAGE)
        return 0
    try:
        run(args)
    except AuthorityError as exc:
        _write_error(exc.code)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
