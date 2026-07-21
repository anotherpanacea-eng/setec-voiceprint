from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import types

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import shingle_dedup_checkpoint as checkpoint
import shingle_dedup_io as secure_io


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8") + b"\n"


def _cursor(doc_id: str) -> str:
    return _canonical({"doc_id": doc_id}).decode("ascii").strip()


def _batch_cursor() -> str:
    return _canonical({
        "draft_id": "draft", "query_id": "later", "query_stage_order": 2,
        "reference_id": "earlier", "reference_stage_order": 1,
    }).decode("ascii").strip()


def _meta(kind: str, *, chunk: int = 0, first: str | None = None,
          next_item: str | None = None, item_count: int = 1) -> dict[str, str]:
    values = {
        "schema_version": "setec-shingle-checkpoint/1", "tool": "shingle_dedup",
        "method_version": "1", "checkpoint_kind": {
            "inventory": "build_inventory", "build": "build_index", "batch": "batch_report"
        }[kind],
        "chunk_number": str(chunk), "source_manifest_sha256": HASH_A if kind != "batch" else "-",
        "canonical_descriptors_sha256": HASH_B if kind == "build" else "-",
        "index_sha256": HASH_B if kind == "batch" else "-",
        "logical_index_sha256": HASH_C if kind == "batch" else "-",
        "config_sha256": HASH_C, "first_item": first if first is not None else (
            _batch_cursor() if kind == "batch" else _cursor("doc")),
        "next_item": next_item if next_item is not None else "null", "item_count": str(item_count),
        "potential_pairs": "0", "unassessed_pairs": "0", "assessed_pairs": "0",
        "no_overlap_pairs": "0", "below_0_35_pairs": "0",
        "containment_0_35_to_0_60_pairs": "0", "containment_at_least_0_60_pairs": "0",
        "reported_pairs": "0",
    }
    if kind == "batch":
        values.update({"potential_pairs": str(item_count), "assessed_pairs": str(item_count),
                       "containment_0_35_to_0_60_pairs": str(item_count),
                       "reported_pairs": str(item_count)})
    return values


def _inventory_row(doc_id: str = "doc") -> tuple[object, ...]:
    return doc_id, "draft", "stage", 1, hashlib.sha256(doc_id.encode()).digest()


def _pair(query_id: str = "later") -> dict[str, object]:
    return {
        "pair_kind": "draft_stage_pair_candidate", "query_id": query_id,
        "reference_id": "earlier", "draft_id": "draft", "query_stage": "later-stage",
        "query_stage_order": 2, "reference_stage": "earlier-stage", "reference_stage_order": 1,
        "query_tokens": 15, "reference_tokens": 15, "query_shingles": 8,
        "reference_shingles": 8, "shared_shingles": 4, "containment_numerator": 4,
        "containment_denominator": 8, "containment": 0.5,
        "reverse_containment_numerator": 4, "reverse_containment_denominator": 8,
        "reverse_containment": 0.5, "jaccard_numerator": 4, "jaccard_denominator": 12,
        "jaccard": 0.333333, "tier_metric_numerator": 4, "tier_metric_denominator": 8,
        "tier_metric": 0.5, "pair_containment_direction": "equal",
        "overlap_tier": "containment_0_35_to_0_60",
    }


def _publish_inventory(path: Path) -> bytes:
    with checkpoint.CheckpointDirectory.open_new(path) as directory:
        snapshot = directory.publish(kind="inventory", meta=_meta("inventory"),
                                     inventory_rows=[_inventory_row()])
        return snapshot.raw


def _rewrite_database(raw: bytes, statement: str, parameters: tuple[object, ...] = ()) -> bytes:
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(raw)
        connection.execute(statement, parameters)
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def test_round_trip_uses_frozen_owned_snapshot(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    raw = _publish_inventory(state_dir)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        state = directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A)
    assert len(state.inventory) == 1
    assert raw.startswith(b"SQLite format 3\x00")
    assert state.inventory[0].raw == b""  # exact bytes are discarded after owned deserialization
    assert state.inventory[0].inventory_rows == (_inventory_row(),)


def test_inventory_meta_and_logical_seal_are_frozen_bytes() -> None:
    _raw, sealed = checkpoint._encode_checkpoint(
        "inventory", _meta("inventory"), inventory_rows=[_inventory_row()],
        document_rows=(), posting_rows=(), pairs=(),
    )
    assert sealed == {
        "schema_version": "setec-shingle-checkpoint/1", "tool": "shingle_dedup",
        "method_version": "1", "checkpoint_kind": "build_inventory", "chunk_number": "0",
        "source_manifest_sha256": "a" * 64, "canonical_descriptors_sha256": "-",
        "index_sha256": "-", "logical_index_sha256": "-", "config_sha256": "c" * 64,
        "first_item": '{"doc_id":"doc"}', "next_item": "null", "item_count": "1",
        "potential_pairs": "0", "unassessed_pairs": "0", "assessed_pairs": "0",
        "no_overlap_pairs": "0", "below_0_35_pairs": "0",
        "containment_0_35_to_0_60_pairs": "0", "containment_at_least_0_60_pairs": "0",
        "reported_pairs": "0",
        "checkpoint_sha256": "bd0c1cffa3177fa5db0051791a14c0538fdd3d5e261e472ba30f9da8e5e8b4cd",
    }


def test_build_shard_matches_inventory_and_restores_postings(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    inventory = _inventory_row()
    shingle = hashlib.sha256(b"shingle").digest()
    document = (*inventory, 8, 1, "eligible")
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        directory.publish(kind="inventory", meta=_meta("inventory"),
                          inventory_rows=[inventory])
        directory.publish(kind="build", meta=_meta("build"),
                          document_rows=[document], posting_rows=[(shingle, "doc")])
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        state = directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A,
                               canonical_descriptors_sha256=HASH_B)
    assert state.build[0].document_rows == (document,)
    assert state.build[0].posting_rows == ((shingle, "doc"),)


def test_create_new_collision_refuses_and_preserves_winner(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        winner = directory.publish(kind="inventory", meta=_meta("inventory"),
                                   inventory_rows=[_inventory_row()]).raw
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="inventory", meta=_meta("inventory"),
                              inventory_rows=[_inventory_row("other")])
    assert (state_dir / "inventory-00000000.sqlite").read_bytes() == winner


def test_directory_addition_after_enumeration_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "state"
    _publish_inventory(state_dir)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        original = directory._read_final

        def add_after_read(name: str) -> bytes:
            raw = original(name)
            (state_dir / "unknown.bin").write_bytes(b"race")
            return raw

        monkeypatch.setattr(directory, "_read_final", add_after_read)
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


def test_shard_swap_after_enumeration_refuses_even_when_replacement_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    _publish_inventory(state_dir)
    replacement, _meta_with_seal = checkpoint._encode_checkpoint(
        "inventory", _meta("inventory"), inventory_rows=[_inventory_row("other")],
        document_rows=(), posting_rows=(), pairs=(),
    )
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        original = directory._read_final

        def swap_before_read(name: str) -> bytes:
            (state_dir / name).write_bytes(replacement)
            return original(name)

        monkeypatch.setattr(directory, "_read_final", swap_before_read)
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


def test_pair_json_corruption_with_unchanged_pair_hash_refuses(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        snapshot = directory.publish(kind="batch", meta=_meta("batch"), pairs=[_pair()])
    corrupt_pair = _canonical(_pair("changed"))
    corrupt = _rewrite_database(snapshot.raw, "UPDATE pairs SET pair_json=? WHERE sequence=0", (corrupt_pair,))
    (state_dir / "batch-00000000.sqlite").write_bytes(corrupt)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="batch", config_sha256=HASH_C, index_sha256=HASH_B,
                           logical_index_sha256=HASH_C)


@pytest.mark.parametrize("failure", ["write", "flush", "rename", "identity", "memory"])
def test_general_windows_publish_fake_backend_faults_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    events: list[tuple[object, ...]] = []
    class Direct:
        def __init__(self, identity: tuple[int, int]) -> None: self.identity = identity
    def write(handle: int, view: memoryview) -> int:
        events.append(("write", handle))
        if failure == "write": raise OSError("injected")
        return len(view)
    def flush(handle: int) -> None:
        events.append(("flush", handle))
        if failure == "flush": raise OSError("injected")
    def close(handle: int) -> None: events.append(("close", handle))
    def open_file(_parent: int, name: str, **_kwargs: object) -> int:
        return 20 if name.startswith(".tmp-") else 30
    def require_direct(handle: int, _kind: str) -> Direct:
        return Direct((9, 9) if handle == 30 and failure == "identity" else (1, 2))
    def rename(_handle: int, _parent: int, _name: str, *, replace: bool) -> None:
        assert replace is False
        if failure == "rename": raise FileExistsError("winner")
    def delete(handle: int) -> None: events.append(("delete", handle))
    fake = types.SimpleNamespace(
        pin_directory=lambda _path, writable_final: (1, 2, "parent"),
        create_file=lambda _parent, _name: 10, write=write, flush=flush, close=close,
        open_file=open_file, require_direct=require_direct, rename=rename, delete=delete,
    )
    monkeypatch.setattr(secure_io, "_windows_module", lambda: fake)
    revalidations = 0
    def revalidate(*_args: object) -> None:
        nonlocal revalidations
        revalidations += 1
        if failure == "memory" and revalidations == 2:
            raise MemoryError
    monkeypatch.setattr(secure_io, "_windows_revalidate_directory", revalidate)
    with pytest.raises(secure_io.SecureIOError):
        secure_io._windows_publish(tmp_path / "final.bin", b"payload")
    assert ("delete", 10 if failure in {"write", "flush"} else 20) in events
    if failure == "memory":
        assert revalidations == 2
        assert all(("close", handle) in events for handle in (10, 20, 2, 1))


@pytest.mark.parametrize("mutation", ["wrong_tier", "wrong_selected_fraction"])
def test_semantically_false_sealed_pair_refuses(tmp_path: Path, mutation: str) -> None:
    state_dir = tmp_path / mutation
    pair = _pair()
    if mutation == "wrong_tier":
        pair["overlap_tier"] = "containment_at_least_0_60"
    else:
        pair["tier_metric_numerator"] = 2
        pair["tier_metric_denominator"] = 2
        pair["tier_metric"] = 1.0
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="batch", meta=_meta("batch"), pairs=[pair])
    assert not list(state_dir.iterdir())


@pytest.mark.parametrize("mutation", ["order", "same_id", "same_stage", "tokens", "shingles"])
def test_invalid_batch_orientation_or_eligible_domains_refuse(tmp_path: Path, mutation: str) -> None:
    pair = _pair()
    if mutation == "order": pair["query_stage_order"] = pair["reference_stage_order"]
    elif mutation == "same_id": pair["query_id"] = pair["reference_id"]
    elif mutation == "same_stage": pair["query_stage"] = pair["reference_stage"]
    elif mutation == "tokens": pair["query_tokens"] = 500_001
    else: pair["query_shingles"] = pair["query_tokens"]
    state_dir = tmp_path / mutation
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="batch", meta=_meta("batch"), pairs=[pair])
    assert not list(state_dir.iterdir())


def test_extra_sqlite_object_refuses(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    raw = _publish_inventory(state_dir)
    corrupt = _rewrite_database(raw, "CREATE TABLE surprise(value TEXT)")
    (state_dir / "inventory-00000000.sqlite").write_bytes(corrupt)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


@pytest.mark.parametrize("mutation", ["trailing", "sqlite_stat"])
def test_checkpoint_trailing_bytes_and_internal_schema_objects_refuse(
    tmp_path: Path, mutation: str,
) -> None:
    state_dir = tmp_path / mutation
    raw = _publish_inventory(state_dir)
    corrupt = raw + b"TRAILING" if mutation == "trailing" else _rewrite_database(raw, "ANALYZE")
    (state_dir / "inventory-00000000.sqlite").write_bytes(corrupt)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


def test_cumulative_byte_ceiling_is_checked_before_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    _publish_inventory(state_dir)
    monkeypatch.setattr(checkpoint, "MAX_CUMULATIVE_BYTES", 1)
    def forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cumulative preflight must refuse before SQLite")
    def forbidden_decode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cumulative preflight must refuse before deserialization")
    monkeypatch.setattr(checkpoint.sqlite3, "connect", forbidden_connect)
    monkeypatch.setattr(checkpoint, "_validate_snapshot", forbidden_decode)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


def test_unknown_entry_and_too_many_reserved_temps_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "state"
    _publish_inventory(state_dir)
    (state_dir / "unknown").write_bytes(b"")
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)
    (state_dir / "unknown").unlink()
    monkeypatch.setattr(checkpoint, "MAX_RESERVED_TEMPS", 1)
    (state_dir / (".tmp-" + "1" * 32)).write_bytes(b"")
    (state_dir / (".tmp-" + "2" * 32)).write_bytes(b"")
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="build", config_sha256=HASH_C,
                           source_manifest_sha256=HASH_A)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")
def test_symlinked_checkpoint_directory_refuses(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _publish_inventory(real)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(checkpoint.CheckpointRefusal):
        checkpoint.CheckpointDirectory.open_resume(link)


def test_cursor_discontinuity_and_nonterminal_short_shard_refuse(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="inventory", meta=_meta(
                "inventory", chunk=0, next_item=_cursor("next"), item_count=1,
            ), inventory_rows=[_inventory_row()])
    assert not list(state_dir.iterdir())


def test_publish_rejects_noncontiguous_chunk_before_disk(tmp_path: Path) -> None:
    state_dir = tmp_path / "chunk-gap"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="inventory", meta=_meta("inventory", chunk=99),
                              inventory_rows=[_inventory_row()])
    assert not list(state_dir.iterdir())


def _encoded(kind: str, meta: dict[str, str], *, inventory=(), documents=(), postings=(), pairs=()) -> bytes:
    raw, _sealed = checkpoint._encode_checkpoint(
        kind, meta, inventory_rows=inventory, document_rows=documents,
        posting_rows=postings, pairs=pairs,
    )
    return raw


def _many_inventory(start: int, count: int) -> list[tuple[object, ...]]:
    return [
        (f"doc{number:03d}", f"draft{number:03d}", "stage", 0,
         hashlib.sha256(f"doc{number:03d}".encode()).digest())
        for number in range(start, start + count)
    ]


def test_interrupted_inventory_full_shard_exposes_continuation(tmp_path: Path) -> None:
    state_dir = tmp_path / "inventory-interrupted"; state_dir.mkdir()
    rows = _many_inventory(0, 250)
    meta = _meta("inventory", first=_cursor("doc000"), next_item=_cursor("doc250"), item_count=250)
    (state_dir / "inventory-00000000.sqlite").write_bytes(_encoded("inventory", meta, inventory=rows))
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        state = directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A)
    assert state.continuation("inventory") == _cursor("doc250")


def test_interrupted_build_full_shard_exposes_continuation(tmp_path: Path) -> None:
    state_dir = tmp_path / "build-interrupted"; state_dir.mkdir()
    inventory = _many_inventory(0, 251)
    inv0 = _meta("inventory", first=_cursor("doc000"), next_item=_cursor("doc250"), item_count=250)
    inv1 = _meta("inventory", chunk=1, first=_cursor("doc250"), item_count=1)
    (state_dir / "inventory-00000000.sqlite").write_bytes(_encoded("inventory", inv0, inventory=inventory[:250]))
    (state_dir / "inventory-00000001.sqlite").write_bytes(_encoded("inventory", inv1, inventory=inventory[250:]))
    documents = [(*row, 8, 1, "eligible") for row in inventory[:250]]
    postings = [(hashlib.sha256(f"shingle{number:03d}".encode()).digest(), row[0])
                for number, row in enumerate(inventory[:250])]
    build = _meta("build", first=_cursor("doc000"), next_item=_cursor("doc250"), item_count=250)
    (state_dir / "build-00000000.sqlite").write_bytes(_encoded(
        "build", build, documents=documents, postings=postings,
    ))
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        state = directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A,
                               canonical_descriptors_sha256=HASH_B)
    assert state.continuation("build") == _cursor("doc250")


def test_interrupted_batch_full_shard_exposes_continuation(tmp_path: Path) -> None:
    state_dir = tmp_path / "batch-interrupted"; state_dir.mkdir()
    meta = _meta("batch", first=_batch_cursor(), next_item=_canonical({
        "draft_id": "next-draft", "query_id": "next-query", "query_stage_order": 2,
        "reference_id": "next-reference", "reference_stage_order": 1,
    }).decode("ascii").strip(), item_count=250)
    meta.update({"unassessed_pairs": "250", "assessed_pairs": "0",
                 "containment_0_35_to_0_60_pairs": "0", "reported_pairs": "0"})
    (state_dir / "batch-00000000.sqlite").write_bytes(_encoded("batch", meta))
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        state = directory.load(mode="batch", config_sha256=HASH_C,
                               index_sha256=HASH_B, logical_index_sha256=HASH_C)
    assert state.continuation("batch") == meta["next_item"]


@pytest.mark.parametrize("token_count,shingle_count", [(500_001, 1), (8, 2)])
def test_build_document_token_and_shingle_domains_refuse(
    tmp_path: Path, token_count: int, shingle_count: int,
) -> None:
    state_dir = tmp_path / f"bad-{token_count}-{shingle_count}"
    inventory = _inventory_row()
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        directory.publish(kind="inventory", meta=_meta("inventory"), inventory_rows=[inventory])
        document = (*inventory, token_count, shingle_count, "eligible")
        postings = [(hashlib.sha256(f"s{number}".encode()).digest(), "doc")
                    for number in range(shingle_count)]
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="build", meta=_meta("build"),
                              document_rows=[document], posting_rows=postings)
    assert not (state_dir / "build-00000000.sqlite").exists()


def test_completed_build_must_cover_completed_inventory(tmp_path: Path) -> None:
    state_dir = tmp_path / "partial-terminal"
    inventory = [_inventory_row("doc"), ("other", "other-draft", "stage", 1,
                                         hashlib.sha256(b"other").digest())]
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        directory.publish(kind="inventory", meta=_meta("inventory", item_count=2),
                          inventory_rows=inventory)
        document = (*inventory[0], 8, 1, "eligible")
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="build", meta=_meta("build"), document_rows=[document],
                              posting_rows=[(hashlib.sha256(b"s").digest(), "doc")])
    assert not (state_dir / "build-00000000.sqlite").exists()


def test_first_publish_rejects_concurrent_unknown_entry(tmp_path: Path) -> None:
    state_dir = tmp_path / "unknown-first"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        (state_dir / "unknown").write_bytes(b"race")
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="inventory", meta=_meta("inventory"),
                              inventory_rows=[_inventory_row()])
    assert not (state_dir / "inventory-00000000.sqlite").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX handle-relative cleanup")
def test_post_link_ancestor_refusal_cleans_only_owned_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "post-link"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        original = directory._revalidate

        def fail_after_link() -> None:
            if (state_dir / "inventory-00000000.sqlite").exists():
                raise checkpoint.CheckpointRefusal("injected ancestor move")
            original()

        monkeypatch.setattr(directory, "_revalidate", fail_after_link)
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.publish(kind="inventory", meta=_meta("inventory"),
                              inventory_rows=[_inventory_row()])
    assert not (state_dir / "inventory-00000000.sqlite").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX handle-relative cleanup")
def test_checkpoint_link_side_effect_then_memory_error_cleans_owned_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "link-memory"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        real_link = checkpoint.os.link
        def link_then_memory(source: str, target: str, **kwargs: object) -> None:
            real_link(source, target, **kwargs)
            raise MemoryError
        monkeypatch.setattr(checkpoint.os, "link", link_then_memory)
        supported = set(checkpoint.os.supports_dir_fd); supported.add(link_then_memory)
        monkeypatch.setattr(checkpoint.os, "supports_dir_fd", supported)
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory._posix_publish("owned.sqlite", b"payload")
    assert not (state_dir / "owned.sqlite").exists()
    assert not list(state_dir.glob(".tmp-*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX handle-relative cleanup")
def test_checkpoint_first_payload_fstat_memory_error_recovers_temp_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = tmp_path / "fstat-memory"
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        real_fstat = checkpoint.os.fstat
        injected = False
        def first_regular_fstat(descriptor: int) -> os.stat_result:
            nonlocal injected
            info = real_fstat(descriptor)
            if stat.S_ISREG(info.st_mode) and not injected:
                injected = True
                raise MemoryError
            return info
        monkeypatch.setattr(checkpoint.os, "fstat", first_regular_fstat)
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory._posix_publish("owned.sqlite", b"payload")
        assert injected
    assert not (state_dir / "owned.sqlite").exists()
    assert not list(state_dir.glob(".tmp-*"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor lifecycle")
def test_posix_pin_closes_component_when_fstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "parent" / "child"
    target.mkdir(parents=True)
    real_open = checkpoint.os.open
    real_fstat = checkpoint.os.fstat
    opened: list[int] = []

    def recording_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def injected_fstat(descriptor: int) -> os.stat_result:
        if len(opened) >= 2 and descriptor == opened[-1]:
            raise OSError("injected fstat failure")
        return real_fstat(descriptor)

    monkeypatch.setattr(checkpoint.os, "open", recording_open)
    monkeypatch.setattr(checkpoint.os, "fstat", injected_fstat)
    with pytest.raises(checkpoint.CheckpointRefusal):
        checkpoint._posix_pin_directory(target)

    assert len(opened) >= 2
    for descriptor in opened:
        with pytest.raises(OSError):
            real_fstat(descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor lifecycle")
def test_posix_pin_closes_chain_on_component_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "parent" / "child"
    target.mkdir(parents=True)
    real_open = checkpoint.os.open
    real_fstat = checkpoint.os.fstat
    real_identity = checkpoint._identity
    opened: list[int] = []
    identity_calls = 0

    def recording_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def mismatched_identity(info: os.stat_result) -> tuple[int, int]:
        nonlocal identity_calls
        identity_calls += 1
        device, inode = real_identity(info)
        if identity_calls == 2:
            return device, inode + 1
        return device, inode

    monkeypatch.setattr(checkpoint.os, "open", recording_open)
    monkeypatch.setattr(checkpoint, "_identity", mismatched_identity)
    with pytest.raises(checkpoint.CheckpointRefusal):
        checkpoint._posix_pin_directory(target)

    assert identity_calls == 2
    assert len(opened) >= 2
    for descriptor in opened:
        with pytest.raises(OSError):
            real_fstat(descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor lifecycle")
def test_open_new_closes_created_directory_when_revalidation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "new-checkpoint"
    real_open = checkpoint.os.open
    real_fstat = checkpoint.os.fstat
    real_revalidate = checkpoint._posix_revalidate
    opened: list[int] = []
    calls = 0

    def recording_open(*args: object, **kwargs: object) -> int:
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def injected_revalidate(path: Path, descriptors: list[int]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise checkpoint.CheckpointRefusal("injected post-open revalidation")
        real_revalidate(path, descriptors)

    monkeypatch.setattr(checkpoint.os, "open", recording_open)
    monkeypatch.setattr(checkpoint, "_posix_revalidate", injected_revalidate)
    with pytest.raises(checkpoint.CheckpointRefusal):
        checkpoint.CheckpointDirectory.open_new(target)

    assert calls == 2
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            real_fstat(descriptor)


def test_windows_open_new_transfers_full_chain_and_closes_it_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    fail_second_revalidation = False
    revalidation_count = 0

    def pin_directory_chain(path: Path, *, writable_final: bool) -> tuple[int, ...]:
        events.append(("pin", path, writable_final)); return (10, 11)

    def revalidate_directory_chain(path: Path, handles: tuple[int, ...]) -> None:
        nonlocal revalidation_count
        revalidation_count += 1
        events.append(("revalidate", path, handles))
        if fail_second_revalidation and revalidation_count == 2:
            raise OSError("injected child-chain failure")

    def create_directory(parent: int, name: str) -> int:
        events.append(("create", parent, name)); return 12

    def close(handle: int) -> None:
        events.append(("close", handle))

    fake = types.SimpleNamespace(pin_directory_chain=pin_directory_chain,
                                 revalidate_directory_chain=revalidate_directory_chain,
                                 create_directory=create_directory, close=close)
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", fake)
    monkeypatch.setattr(checkpoint, "_absolute", lambda path: Path(path))
    monkeypatch.setattr(checkpoint, "os", types.SimpleNamespace(name="nt"))

    target = tmp_path / "success-state"
    directory = checkpoint.CheckpointDirectory.open_new(target)
    assert directory._windows_handles == (10, 11, 12)
    assert [event for event in events if event[0] == "revalidate"] == [
        ("revalidate", target.parent, (10, 11)),
        ("revalidate", target, (10, 11, 12)),
    ]
    assert not [event for event in events if event[0] == "close"]
    directory.close()
    assert [event for event in events if event[0] == "close"] == [
        ("close", 12), ("close", 11), ("close", 10),
    ]

    events.clear(); revalidation_count = 0; fail_second_revalidation = True
    with pytest.raises(checkpoint.CheckpointRefusal):
        checkpoint.CheckpointDirectory.open_new(tmp_path / "failed-state")
    assert [event for event in events if event[0] == "close"] == [
        ("close", 12), ("close", 11), ("close", 10),
    ]


@pytest.mark.parametrize("failure", [None, "flush", "rename", "identity"])
def test_windows_publish_fake_backend_has_fail_closed_handle_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    events: list[tuple[object, ...]] = []

    class Direct:
        def __init__(self, identity: tuple[int, int]) -> None:
            self.identity = identity

    def create_file(parent: int, name: str) -> int:
        events.append(("create", parent, name)); return 10

    def write(handle: int, view: memoryview) -> int:
        events.append(("write", handle, len(view))); return len(view)

    def flush(handle: int) -> None:
        events.append(("flush", handle))
        if failure == "flush": raise OSError("injected flush failure")

    def close(handle: int) -> None:
        events.append(("close", handle))

    def open_file(parent: int, name: str, **kwargs: object) -> int:
        handle = 20 if name.startswith(".tmp-") else 30
        events.append(("open", parent, name, kwargs, handle)); return handle

    def require_direct(handle: int, kind: str) -> Direct:
        events.append(("direct", handle, kind))
        identity = (1, 2) if handle != 30 or failure != "identity" else (9, 9)
        return Direct(identity)

    def rename(handle: int, parent: int, name: str, *, replace: bool) -> None:
        events.append(("rename", handle, parent, name, replace))
        if failure == "rename": raise FileExistsError("winner arrived")

    def delete(handle: int) -> None:
        events.append(("delete", handle))

    fake = types.SimpleNamespace(create_file=create_file, write=write, flush=flush,
                                 close=close, open_file=open_file,
                                 require_direct=require_direct, rename=rename,
                                 delete=delete)
    monkeypatch.setitem(sys.modules, "windows_descriptor_io", fake)
    directory = checkpoint.CheckpointDirectory(tmp_path, windows_handles=(99,))
    monkeypatch.setattr(directory, "_revalidate", lambda: events.append(("revalidate",)))

    if failure is None:
        directory._windows_publish("final.sqlite", b"payload")
    else:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory._windows_publish("final.sqlite", b"payload")

    assert events.index(("flush", 10)) < events.index(("close", 10))
    revalidations = [index for index, event in enumerate(events) if event == ("revalidate",)]
    if failure == "flush":
        assert len(revalidations) == 1
        assert ("delete", 10) in events and not any(event[0] == "rename" for event in events)
    else:
        control_open = next(event for event in events if event[0] == "open" and event[-1] == 20)
        assert control_open[3] == {"delete_access": True, "share_delete": True, "share_write": False}
        assert ("rename", 20, 99, "final.sqlite", False) in events
        assert len(revalidations) == (2 if failure == "rename" else 3)
        assert revalidations[0] < events.index(("create", 99, control_open[2]))
        assert revalidations[1] < events.index(("rename", 20, 99, "final.sqlite", False))
        if failure != "rename":
            final_open = next(index for index, event in enumerate(events)
                              if event[0] == "open" and event[-1] == 30)
            assert events.index(("rename", 20, 99, "final.sqlite", False)) < revalidations[2] < final_open
            assert events.count(("close", 30)) == 1
        if failure is None:
            assert ("delete", 20) not in events and events.count(("close", 20)) == 1
        else:
            assert ("delete", 20) in events and events.count(("close", 20)) == 1


@pytest.mark.parametrize("ceiling", [
    "MAX_ENTRIES", "MAX_FINAL_SHARDS", "MAX_RESERVED_TEMPS", "MAX_SHARD_BYTES",
    "MAX_CUMULATIVE_BYTES", "MAX_CUMULATIVE_VM_OPCODES", "MAX_ITEM_COUNT",
    "MAX_DESCRIPTORS", "MAX_TOKENS_PER_DOCUMENT", "MAX_TOTAL_TOKENS",
    "MAX_POSTINGS", "MAX_POTENTIAL_PAIRS", "MAX_REPORTED_PAIRS",
    "MAX_SHINGLES_PER_DOCUMENT",
])
def test_checkpoint_ceiling_accepts_control_and_refuses_lowered_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ceiling: str,
) -> None:
    if ceiling in {"MAX_ENTRIES", "MAX_FINAL_SHARDS", "MAX_RESERVED_TEMPS",
                   "MAX_SHARD_BYTES", "MAX_CUMULATIVE_BYTES",
                   "MAX_CUMULATIVE_VM_OPCODES"}:
        state_dir = tmp_path / ceiling
        raw = _publish_inventory(state_dir)
        if ceiling == "MAX_RESERVED_TEMPS":
            (state_dir / (".tmp-" + "1" * 32)).write_bytes(b"")
            boundary, refused = 1, 0
        elif ceiling in {"MAX_SHARD_BYTES", "MAX_CUMULATIVE_BYTES"}:
            boundary, refused = len(raw), len(raw) - 1
        elif ceiling == "MAX_CUMULATIVE_VM_OPCODES":
            boundary, refused = 2_000_000_000, 0
        else:
            boundary, refused = 1, 0
        monkeypatch.setattr(checkpoint, ceiling, boundary)
        with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
            assert directory.load(mode="build", config_sha256=HASH_C,
                                  source_manifest_sha256=HASH_A).inventory
        monkeypatch.setattr(checkpoint, ceiling, refused)
        if ceiling == "MAX_CUMULATIVE_VM_OPCODES":
            monkeypatch.setattr(checkpoint, "VM_INTERVAL", 1)
        with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
            with pytest.raises(checkpoint.CheckpointRefusal):
                directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A)
        return

    if ceiling in {"MAX_ITEM_COUNT", "MAX_DESCRIPTORS"}:
        monkeypatch.setattr(checkpoint, ceiling, 1)
        _publish_inventory(tmp_path / "ok")
        monkeypatch.setattr(checkpoint, ceiling, 0)
        with checkpoint.CheckpointDirectory.open_new(tmp_path / "bad") as directory:
            with pytest.raises(checkpoint.CheckpointRefusal):
                directory.publish(kind="inventory", meta=_meta("inventory"),
                                  inventory_rows=[_inventory_row()])
        assert not list((tmp_path / "bad").iterdir())
        return

    if ceiling in {"MAX_TOKENS_PER_DOCUMENT", "MAX_TOTAL_TOKENS", "MAX_POSTINGS",
                   "MAX_SHINGLES_PER_DOCUMENT"}:
        inventory = _inventory_row(); shingle = hashlib.sha256(b"s").digest()
        document = (*inventory, 8, 1, "eligible")
        boundary = {"MAX_TOKENS_PER_DOCUMENT": 8, "MAX_TOTAL_TOKENS": 8,
                    "MAX_POSTINGS": 1, "MAX_SHINGLES_PER_DOCUMENT": 1}[ceiling]
        monkeypatch.setattr(checkpoint, ceiling, boundary)
        ok = tmp_path / "ok"
        with checkpoint.CheckpointDirectory.open_new(ok) as directory:
            directory.publish(kind="inventory", meta=_meta("inventory"), inventory_rows=[inventory])
            directory.publish(kind="build", meta=_meta("build"), document_rows=[document],
                              posting_rows=[(shingle, "doc")])
        with checkpoint.CheckpointDirectory.open_resume(ok) as directory:
            assert directory.load(mode="build", config_sha256=HASH_C,
                                  source_manifest_sha256=HASH_A,
                                  canonical_descriptors_sha256=HASH_B).build
        monkeypatch.setattr(checkpoint, ceiling, boundary - 1)
        with checkpoint.CheckpointDirectory.open_resume(ok) as directory:
            with pytest.raises(checkpoint.CheckpointRefusal):
                directory.load(mode="build", config_sha256=HASH_C,
                               source_manifest_sha256=HASH_A,
                               canonical_descriptors_sha256=HASH_B)
        return

    state_dir = tmp_path / ceiling
    monkeypatch.setattr(checkpoint, ceiling, 1)
    with checkpoint.CheckpointDirectory.open_new(state_dir) as directory:
        directory.publish(kind="batch", meta=_meta("batch"), pairs=[_pair()])
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        assert directory.load(mode="batch", config_sha256=HASH_C, index_sha256=HASH_B,
                              logical_index_sha256=HASH_C).batch
    monkeypatch.setattr(checkpoint, ceiling, 0)
    with checkpoint.CheckpointDirectory.open_resume(state_dir) as directory:
        with pytest.raises(checkpoint.CheckpointRefusal):
            directory.load(mode="batch", config_sha256=HASH_C, index_sha256=HASH_B,
                           logical_index_sha256=HASH_C)
