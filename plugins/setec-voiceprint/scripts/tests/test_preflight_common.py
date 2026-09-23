"""Shared packet-manifest loader contract (ingestion preflight slice 1 §4.2 to §4.6).

Every preflight slice loads its packet through ``load_manifest``, so these
tests pin the loader's observable contract: which inputs refuse, with which
public code when several checks fail at once (§4.6, first match wins), and
what analysis identity an accepted candidate gets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from setec.preflight.common import (
    Refusal, bind_input, canonical_json, load_manifest, load_manifest_for_calibration,
    load_strict_json, parse_json, plain_hash, validate_output_path,
)


def _row(index: int, path: str, data: bytes, **overrides) -> dict:
    row = {"id": f"r{index}", "group_id": f"g{index}", "stratum": "synthetic",
           "path": path,
           "span": {"source_path": path, "source_bytes_sha256": plain_hash(data),
                    "start_byte": 0, "end_byte": len(data)}}
    row.update(overrides)
    return row


def _packet(root: Path, texts: list[bytes]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, data in enumerate(texts):
        name = f"candidate-{index}.txt"
        (root / name).write_bytes(data)
        rows.append(_row(index, name, data))
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    return manifest


def _refuses(code: str, manifest: Path) -> None:
    with pytest.raises(Refusal) as caught:
        load_manifest(manifest)
    assert caught.value.code == code


# --- §4.4 analysis view ---------------------------------------------------

def test_line_endings_and_normalization_share_one_analysis_identity(tmp_path):
    """§9.1: LF, CRLF, lone CR (and NFC, NFD) give one analysis hash, distinct bytes."""
    endings = [b"one two\nthree four\n", b"one two\r\nthree four\r\n",
               b"one two\rthree four\r"]
    forms = ["caf\u00e9 cr\u00e8me".encode(), "cafe\u0301 cre\u0300me".encode()]
    records = load_manifest(_packet(tmp_path / "packet", endings + forms)).records
    by_ending, by_form = records[:3], records[3:]
    assert len({r.analysis_sha256 for r in by_ending}) == 1
    assert len({r.candidate.sha256 for r in by_ending}) == 3
    assert len({r.analysis_sha256 for r in by_form}) == 1
    assert len({r.candidate.sha256 for r in by_form}) == 2


def test_delete_character_is_not_a_c1_control(tmp_path):
    """U+007F is neither C0 nor C1, so §4.2 labels and §4.4 text allow it."""
    data = b"alpha\x7fbeta gamma"
    root = tmp_path / "packet"
    root.mkdir()
    (root / "a.txt").write_bytes(data)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json(_row(0, "a.txt", data, id="id\x7f")))
    [record] = load_manifest(manifest).records
    assert record.id == "id\x7f"


@pytest.mark.parametrize("data", [b"alpha\xc2\x80beta", b"alpha\xc2\x9fbeta"])
def test_c1_boundaries_still_refuse(tmp_path, data):
    _refuses("input_contract", _packet(tmp_path / "packet", [data]))


def test_shared_candidate_is_analysed_once_per_file(tmp_path):
    """Many rows sharing one large candidate cost about one analysis, not one per row.

    Reproduces the review finding: 20 rows over one 4 MiB file took ~16 s
    because the text rules and NFC analysis ran per row.
    """
    data = (b"lorem ipsum dolor sit amet " * 40_000)[: 1024 * 1024]
    root = tmp_path / "packet"
    root.mkdir()
    (root / "shared.txt").write_bytes(data)
    single = root / "single.jsonl"
    single.write_bytes(canonical_json(_row(0, "shared.txt", data)))
    many = root / "many.jsonl"
    many.write_bytes(b"".join(canonical_json(_row(i, "shared.txt", data))
                              for i in range(40)))
    load_manifest(single)  # warm caches before timing
    started = time.perf_counter()
    load_manifest(single)
    one = time.perf_counter() - started
    started = time.perf_counter()
    assert len(load_manifest(many).records) == 40
    forty = time.perf_counter() - started
    assert forty < 4 * one + 0.25


# --- §4.2 manifest rows -----------------------------------------------------

def test_deeply_nested_json_is_a_contract_refusal_not_internal(tmp_path):
    """Nesting deep enough to exhaust the decoder stack is malformed input."""
    root = tmp_path / "packet"
    root.mkdir()
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"[" * 20_000 + b"\n")
    _refuses("input_contract", manifest)
    policy = root / "policy.json"
    policy.write_bytes(b"[" * 30_000)
    with pytest.raises(Refusal) as caught:
        load_strict_json(policy, 64 * 1024, "any/1", frozenset({"schema"}), "policy_contract")
    assert caught.value.code == "policy_contract"


@pytest.mark.parametrize("frame", [
    lambda line: line + b"\r",
    lambda line: b" " + line,
    lambda line: line + b" ",
    lambda line: b"\t" + line,
])
def test_manifest_rows_must_be_exact_physical_lines(tmp_path, frame):
    data = b"alpha beta"
    root = tmp_path / "packet"
    root.mkdir()
    (root / "a.txt").write_bytes(data)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(frame(canonical_json(_row(0, "a.txt", data))[:-1]) + b"\n")
    _refuses("input_contract", manifest)


def test_repeated_path_string_is_accepted(tmp_path):
    data = b"alpha beta"
    root = tmp_path / "packet"
    root.mkdir()
    (root / "a.txt").write_bytes(data)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json(_row(0, "a.txt", data)) +
                         canonical_json(_row(1, "a.txt", data)))
    records = load_manifest(manifest).records
    assert [r.id for r in records] == ["r0", "r1"]
    assert records[0].analysis_sha256 == records[1].analysis_sha256


# --- §4.2 rule 3 and §4.3 confinement ------------------------------------------

def _single(tmp_path: Path, path: str, data: bytes = b"alpha beta") -> Path:
    root = tmp_path / "packet"
    root.mkdir(exist_ok=True)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json(_row(0, path, data)))
    return manifest


@pytest.mark.parametrize("name", ["/etc/hosts", "../outside.txt", "sub/../a.txt",
                                  "./a.txt", "a\\b.txt", "c:a.txt", ""])
def test_non_relative_or_escaping_names_refuse(tmp_path, name):
    (tmp_path / "outside.txt").write_bytes(b"alpha beta")
    manifest = _single(tmp_path, name)
    (manifest.parent / "a.txt").write_bytes(b"alpha beta")
    _refuses("path_confinement", manifest)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks and FIFOs")
def test_symlink_component_and_non_regular_targets_refuse(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"alpha beta")
    root = tmp_path / "packet"
    root.mkdir()
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    (root / "link.txt").symlink_to(outside / "a.txt")
    (root / "dir.txt").mkdir()
    os.mkfifo(root / "fifo.txt")
    for name in ("linkdir/a.txt", "link.txt", "dir.txt", "fifo.txt"):
        _refuses("path_confinement", _single(tmp_path, name))


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_symlink_above_manifest_directory_is_tolerated(tmp_path):
    """macOS /tmp -> /private/tmp style ancestors must not refuse."""
    real = tmp_path / "real"
    manifest = _packet(real / "packet", [b"alpha beta"])
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    via_link = tmp_path / "alias" / "packet" / manifest.name
    assert [r.id for r in load_manifest(via_link).records] == ["r0"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_symlink_at_manifest_directory_still_refuses(tmp_path):
    manifest = _packet(tmp_path / "packet", [b"alpha beta"])
    (tmp_path / "linked").symlink_to(manifest.parent, target_is_directory=True)
    _refuses("path_confinement", tmp_path / "linked" / manifest.name)


# --- §4.2 rule 5 self-spans -----------------------------------------------------

@pytest.mark.parametrize("span", [
    {"end_byte": 9},                       # wrong end
    {"source_bytes_sha256": "0" * 64},     # wrong hash
    {"start_byte": 1},                     # same path, not the whole file
])
def test_bad_self_span_refuses_input_contract(tmp_path, span):
    data = b"alpha beta"
    manifest = _single(tmp_path, "a.txt", data)
    (manifest.parent / "a.txt").write_bytes(data)
    row = json.loads(manifest.read_bytes())
    row["span"].update(span)
    manifest.write_bytes(canonical_json(row))
    _refuses("input_contract", manifest)


# --- §4.6 refusal precedence ----------------------------------------------------

def test_path_confinement_outranks_a_bad_label_in_the_same_row(tmp_path):
    manifest = _single(tmp_path, "/etc/hosts")
    row = json.loads(manifest.read_bytes())
    row["id"] = "bad\x01id"
    manifest.write_bytes(canonical_json(row))
    _refuses("path_confinement", manifest)


def test_path_confinement_outranks_an_earlier_malformed_row(tmp_path):
    data = b"alpha beta"
    manifest = _single(tmp_path, "a.txt", data)
    (manifest.parent / "a.txt").write_bytes(data)
    manifest.write_bytes(b"{not json}\n" + canonical_json(_row(1, "../x.txt", data)))
    _refuses("path_confinement", manifest)


@pytest.mark.skipif(os.name != "posix", reason="POSIX hard links and symlinks")
def test_path_confinement_outranks_path_alias(tmp_path):
    data = b"alpha beta"
    root = tmp_path / "packet"
    root.mkdir()
    (root / "a.txt").write_bytes(data)
    os.link(root / "a.txt", root / "b.txt")
    (root / "z.txt").symlink_to(root / "a.txt")
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(canonical_json(_row(i, name, data))
                                  for i, name in enumerate(["a.txt", "b.txt", "z.txt"])))
    _refuses("path_confinement", manifest)


def test_size_limit_outranks_input_contract(tmp_path):
    big = b"a " * (2 * 1024 * 1024 + 1)
    root = tmp_path / "packet"
    root.mkdir()
    (root / "big.txt").write_bytes(big)
    manifest = root / "packet.jsonl"
    manifest.write_bytes(canonical_json(_row(0, "a.txt", b"alpha")) +
                         canonical_json({**_row(1, "big.txt", big), "extra": 1}))
    (root / "a.txt").write_bytes(b"alpha")
    _refuses("size_limit", manifest)


def test_input_contract_outranks_calibration_binding(tmp_path):
    manifest = _single(tmp_path, "a.txt")
    (manifest.parent / "a.txt").write_bytes(b"alpha beta")
    row = json.loads(manifest.read_bytes())
    row["unknown"] = True
    manifest.write_bytes(canonical_json(row))
    with pytest.raises(Refusal) as caught:
        load_manifest_for_calibration(manifest, expected_sha256="0" * 64)
    assert caught.value.code == "input_contract"


# --- output location (§4.2 last paragraph) ---------------------------------------

def test_output_inside_manifest_directory_refuses_even_when_parent_is_missing(tmp_path):
    manifest = _packet(tmp_path / "packet", [b"alpha beta"])
    root = bind_input(manifest).root
    with pytest.raises(Refusal) as caught:
        validate_output_path(root, root / "missing" / "deeper" / "bundle")
    assert caught.value.code == "path_confinement"
    with pytest.raises(Refusal) as caught:
        validate_output_path(root, tmp_path)
    assert caught.value.code == "path_confinement"


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_output_through_a_symlink_into_manifest_directory_refuses(tmp_path):
    manifest = _packet(tmp_path / "packet", [b"alpha beta"])
    (tmp_path / "sneaky").symlink_to(manifest.parent, target_is_directory=True)
    with pytest.raises(Refusal) as caught:
        validate_output_path(manifest.parent, tmp_path / "sneaky" / "bundle")
    assert caught.value.code == "path_confinement"


def test_parse_json_rejects_nesting_with_the_callers_code():
    with pytest.raises(Refusal) as caught:
        parse_json(b"{\"a\":" * 20_000, "split_contract")
    assert caught.value.code == "split_contract"
