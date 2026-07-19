from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_imessage_sent_atomic as A  # noqa: E402


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows descriptor backend")


def _private_tree(tmp_path: Path) -> tuple[Path, A.PortableDurableRowIo]:
    root = tmp_path / A.PRIVATE_ROOT_COMPONENT / "output"
    root.mkdir(parents=True)
    return root, A.PortableDurableRowIo(root)


def _staged_row(io: A.PortableDurableRowIo, stem: str = "item") -> dict[str, bytes]:
    expected = {"artifact.bin": b"sealed bytes"}
    io.ensure_directory(f"{A.ROW_STAGING_DIRNAME}/{stem}")
    io.ensure_directory(A.ROWS_DIRNAME)
    io.write_bytes(
        f"{A.ROW_STAGING_DIRNAME}/{stem}/artifact.bin",
        expected["artifact.bin"],
        expected_existing=None,
        label="atomic row artifact",
    )
    io.seal_directory(f"{A.ROW_STAGING_DIRNAME}/{stem}", expected)
    return expected


def _journal_raw(state: str, previous_raw: bytes | None = None) -> bytes:
    payload = {
        "schema": "setec-imessage-atomic-row-transaction/1",
        "state": state,
        "previous_journal_digest": (
            None if previous_raw is None else A._sha256_tag(previous_raw)
        ),
        "row_index": 0,
        "source_ordinal": "0",
        "entry_locator": "fixture",
        "disposition": "missing_text",
        "row_stem": None,
        "expected_files": {},
        "predecessor_ledger_digest": "sha256:" + "0" * 64,
        "predecessor_checkpoint_digest": "sha256:" + "1" * 64,
    }
    return A._canonical_json_bytes(A._validated_row_transaction_payload(payload))


def _junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"cannot create test junction: {completed.stderr or completed.stdout}")


def test_windows_backend_writes_cas_and_commits_sealed_directory(tmp_path: Path) -> None:
    _root, io = _private_tree(tmp_path)
    try:
        expected = _staged_row(io)
        io.commit_directory(
            f"{A.ROW_STAGING_DIRNAME}/item",
            f"{A.ROWS_DIRNAME}/item",
            expected_files=expected,
        )
        assert io.read_bytes(f"{A.ROWS_DIRNAME}/item/artifact.bin", "row") == b"sealed bytes"
        io.write_bytes(
            "checkpoint.json",
            b"one",
            expected_existing=None,
            label="checkpoint",
        )
        io.write_bytes(
            "checkpoint.json",
            b"two",
            expected_existing=b"one",
            label="checkpoint",
        )
        assert io.read_bytes("checkpoint.json", "checkpoint") == b"two"
    finally:
        io.close()


def test_windows_backend_refuses_reparse_component_before_mutation(tmp_path: Path) -> None:
    root, io = _private_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "rows"
    _junction(link, outside)
    try:
        with pytest.raises(A.BootstrapStateError):
            io.ensure_directory("rows/item")
    finally:
        io.close()
    assert list(outside.iterdir()) == []


def test_windows_row_commit_retries_only_transient_sharing_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_portable_tree as backend

    _root, io = _private_tree(tmp_path)
    expected = _staged_row(io)
    real_rename = backend.W.rename
    calls = 0
    delays: list[float] = []

    def transient(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ctypes.WinError(32)
        real_rename(*args, **kwargs)

    monkeypatch.setattr(backend.W, "rename", transient)
    monkeypatch.setattr(backend.time, "sleep", delays.append)
    try:
        io.commit_directory(
            f"{A.ROW_STAGING_DIRNAME}/item",
            f"{A.ROWS_DIRNAME}/item",
            expected_files=expected,
        )
    finally:
        io.close()
    assert calls == 3
    assert delays == [0.1, 0.3]


def test_windows_row_commit_exhausts_bounded_retry_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_portable_tree as backend

    root, io = _private_tree(tmp_path)
    expected = _staged_row(io)
    calls = 0
    delays: list[float] = []

    def blocked(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise ctypes.WinError(5)

    monkeypatch.setattr(backend.W, "rename", blocked)
    monkeypatch.setattr(backend.time, "sleep", delays.append)
    try:
        with pytest.raises(A.BootstrapStateError, match="cannot commit atomic row"):
            io.commit_directory(
                f"{A.ROW_STAGING_DIRNAME}/item",
                f"{A.ROWS_DIRNAME}/item",
                expected_files=expected,
            )
    finally:
        io.close()
    assert calls == 4
    assert delays == [0.1, 0.3, 0.9]
    assert (root / A.ROW_STAGING_DIRNAME / "item").is_dir()
    assert not (root / A.ROWS_DIRNAME / "item").exists()


def test_windows_row_commit_keeps_destination_absent_guard(tmp_path: Path) -> None:
    root, io = _private_tree(tmp_path)
    expected = _staged_row(io)
    (root / A.ROWS_DIRNAME / "item").mkdir()
    try:
        with pytest.raises(A.BootstrapStateError, match="already exists"):
            io.commit_directory(
                f"{A.ROW_STAGING_DIRNAME}/item",
                f"{A.ROWS_DIRNAME}/item",
                expected_files=expected,
            )
    finally:
        io.close()
    assert (root / A.ROW_STAGING_DIRNAME / "item" / "artifact.bin").read_bytes() == b"sealed bytes"


def test_empty_wal_and_shm_warn_but_nonempty_sidecars_refuse(tmp_path: Path) -> None:
    snapshot = tmp_path / A.SNAPSHOT_FILENAME
    snapshot.write_bytes(b"snapshot")
    wal = snapshot.with_name(snapshot.name + "-wal")
    shm = snapshot.with_name(snapshot.name + "-shm")
    wal.write_bytes(b"")
    shm.write_bytes(b"")
    with pytest.warns(RuntimeWarning, match="provably empty") as caught:
        A._reject_snapshot_sidecars(snapshot)
    assert len(caught) == 2
    shm.write_bytes(b"live index")
    with pytest.raises(A.SnapshotError, match="unexpected SQLite sidecars"):
        A._reject_snapshot_sidecars(snapshot)
    shm.write_bytes(b"")
    wal.write_bytes(b"committed")
    with pytest.raises(A.SnapshotError, match="unexpected SQLite sidecars"):
        A._reject_snapshot_sidecars(snapshot)


def test_windows_reopens_and_recovers_proven_journal_rewrite_residues(
    tmp_path: Path,
) -> None:
    root, io = _private_tree(tmp_path)
    io.close()
    prepared = _journal_raw("prepared")
    temporary = root / f".{A.ROW_JOURNAL_FILENAME}.{'a' * 32}.tmp"
    temporary.write_bytes(prepared)

    io = A.PortableDurableRowIo(root)
    try:
        io.recover_row_journal_rewrite()
    finally:
        io.close()
    target = root / A.ROW_JOURNAL_FILENAME
    assert target.read_bytes() == prepared
    assert not temporary.exists()

    ledger_closed = _journal_raw("ledger_closed", prepared)
    checkpoint_closed = _journal_raw("checkpoint_closed", ledger_closed)
    backup = root / f".{A.ROW_JOURNAL_FILENAME}.{'b' * 32}.replaced"
    target.replace(backup)
    target.write_bytes(ledger_closed)
    temporary = root / f".{A.ROW_JOURNAL_FILENAME}.{'c' * 32}.tmp"
    temporary.write_bytes(checkpoint_closed)

    io = A.PortableDurableRowIo(root)
    try:
        io.recover_row_journal_rewrite()
    finally:
        io.close()
    assert target.read_bytes() == checkpoint_closed
    assert not backup.exists()
    assert not temporary.exists()


def test_windows_refuses_invalid_lone_journal_temporary_without_mutation(
    tmp_path: Path,
) -> None:
    root, io = _private_tree(tmp_path)
    io.close()
    prepared = _journal_raw("prepared")
    invalid = _journal_raw("ledger_closed", prepared)
    temporary = root / f".{A.ROW_JOURNAL_FILENAME}.{'d' * 32}.tmp"
    temporary.write_bytes(invalid)

    io = A.PortableDurableRowIo(root)
    try:
        with pytest.raises(A.BootstrapStateError, match="not an initial journal"):
            io.recover_row_journal_rewrite()
    finally:
        io.close()
    assert temporary.read_bytes() == invalid
    assert not (root / A.ROW_JOURNAL_FILENAME).exists()


def test_windows_hmac_key_loader_uses_direct_handle(tmp_path: Path) -> None:
    private = tmp_path / A.PRIVATE_ROOT_COMPONENT
    private.mkdir()
    key = private / "identity.key"
    key.write_bytes(b"k" * 32)
    assert A.load_hmac_key(key) == b"k" * 32



def test_windows_component_rejects_alternate_data_stream_syntax() -> None:
    import windows_descriptor_io as descriptor

    with pytest.raises(ValueError, match="one component"):
        descriptor._valid_component("artifact:stream")


def test_windows_seal_rejects_empty_evidence_before_lookup(tmp_path: Path) -> None:
    _root, io = _private_tree(tmp_path)
    try:
        with pytest.raises(A.BootstrapStateError, match="seal expectation"):
            io.seal_directory("missing", {})
    finally:
        io.close()


def test_windows_cas_preserves_concurrent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_portable_tree as backend

    root, io = _private_tree(tmp_path)
    target = root / "checkpoint.json"
    io.write_bytes(
        "checkpoint.json",
        b"one",
        expected_existing=None,
        label="checkpoint",
    )
    real_rename = backend.W.rename
    blocked = False

    def race(
        handle: int,
        destination_parent: int,
        destination: str,
        *,
        replace: bool,
    ) -> None:
        nonlocal blocked
        if not blocked:
            displaced = root / "concurrent-displaced"
            with pytest.raises(PermissionError):
                os.replace(target, displaced)
            with pytest.raises(PermissionError):
                target.write_bytes(b"winner")
            blocked = True
        real_rename(
            handle,
            destination_parent,
            destination,
            replace=replace,
        )

    monkeypatch.setattr(backend.W, "rename", race)
    try:
        io.write_bytes(
            "checkpoint.json",
            b"two",
            expected_existing=b"one",
            label="checkpoint",
        )
    finally:
        io.close()
    assert blocked
    assert target.read_bytes() == b"two"
