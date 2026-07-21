"""POSIX fault/race probes for the B3 identity-bound publication helper.

The fixtures use generated control bytes only.  They deliberately exercise the
private helper directly so a CLI preflight cannot mask a publication defect.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import shingle_dedup_io as secure_io  # noqa: E402


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor backend only")


def _temp_entries(parent: Path) -> list[Path]:
    return sorted(item for item in parent.iterdir() if item.name.startswith(".tmp-"))


def _install_link_fault(monkeypatch: pytest.MonkeyPatch, replacement: object) -> None:
    """Replace ``os.link`` without tripping the helper's dir-fd feature gate."""

    monkeypatch.setattr(secure_io.os, "link", replacement)
    supported = set(secure_io.os.supports_dir_fd)
    supported.add(replacement)
    monkeypatch.setattr(secure_io.os, "supports_dir_fd", supported)


def test_create_new_winner_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    destination = tmp_path / "result.bin"
    destination.write_bytes(b"RACE_WINNER")

    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert destination.read_bytes() == b"RACE_WINNER"
    assert _temp_entries(tmp_path) == []


def test_injected_write_failure_leaves_no_final_and_cleans_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"

    def fail_write(_descriptor: int, _payload: bytes) -> None:
        raise OSError("injected write refusal")

    monkeypatch.setattr(secure_io, "_write_all", fail_write)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert not destination.exists()
    assert _temp_entries(tmp_path) == []


def test_injected_fsync_failure_leaves_no_final_and_cleans_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("injected fsync refusal")

    monkeypatch.setattr(secure_io.os, "fsync", fail_fsync)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert not destination.exists()
    assert _temp_entries(tmp_path) == []


def test_injected_link_failure_leaves_no_final_and_cleans_owned_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"

    def fail_link(
        _source: str,
        _destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        del src_dir_fd, dst_dir_fd, follow_symlinks
        raise OSError("injected link refusal")

    _install_link_fault(monkeypatch, fail_link)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert not destination.exists()
    assert _temp_entries(tmp_path) == []


def test_link_side_effect_then_memory_error_removes_only_owned_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"
    real_link = secure_io.os.link

    def link_then_memory(source: str, target: str, **kwargs: object) -> None:
        real_link(source, target, **kwargs)
        raise MemoryError

    _install_link_fault(monkeypatch, link_then_memory)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")
    assert not destination.exists()
    assert _temp_entries(tmp_path) == []


def test_first_payload_fstat_memory_error_recovers_identity_and_temp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"
    real_fstat = secure_io.os.fstat
    injected = False

    def first_regular_fstat(descriptor: int) -> os.stat_result:
        nonlocal injected
        info = real_fstat(descriptor)
        if stat.S_ISREG(info.st_mode) and not injected:
            injected = True
            raise MemoryError
        return info

    monkeypatch.setattr(secure_io.os, "fstat", first_regular_fstat)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")
    assert injected and not destination.exists()
    assert _temp_entries(tmp_path) == []


def test_destination_substitution_refuses_and_preserves_race_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"
    real_link = secure_io.os.link

    def substitute_destination(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        real_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        os.unlink(target, dir_fd=dst_dir_fd)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=dst_dir_fd,
        )
        try:
            os.write(descriptor, b"RACE_WINNER")
        finally:
            os.close(descriptor)

    _install_link_fault(monkeypatch, substitute_destination)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert destination.read_bytes() == b"RACE_WINNER"
    assert _temp_entries(tmp_path) == []


def test_temp_substitution_refuses_without_deleting_unverified_race_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"
    real_link = secure_io.os.link

    def substitute_temp(
        source: str,
        target: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        os.unlink(source, dir_fd=src_dir_fd)
        descriptor = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=src_dir_fd,
        )
        try:
            os.write(descriptor, b"RACE_TEMP")
        finally:
            os.close(descriptor)
        real_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    _install_link_fault(monkeypatch, substitute_temp)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    # Neither name identifies the helper's original fsynced inode, so the safe
    # refusal must not delete either unverified race winner.
    assert destination.read_bytes() == b"RACE_TEMP"
    race_temps = _temp_entries(tmp_path)
    assert len(race_temps) == 1
    assert race_temps[0].read_bytes() == b"RACE_TEMP"


def test_ancestor_move_refuses_without_publishing_or_deleting_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside = tmp_path / "inside"
    parent = inside / "output"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    moved = outside / "moved-output"
    destination = parent / "result.bin"
    real_write_all = secure_io._write_all
    moved_once = False

    def move_ancestor_then_write(descriptor: int, payload: bytes) -> None:
        nonlocal moved_once
        if not moved_once:
            parent.rename(moved)
            parent.mkdir()
            moved_once = True
        real_write_all(descriptor, payload)

    monkeypatch.setattr(secure_io, "_write_all", move_ancestor_then_write)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert not destination.exists()
    assert not (moved / destination.name).exists()
    assert _temp_entries(parent) == []
    assert _temp_entries(moved) == []


def test_post_link_ancestor_refusal_removes_owned_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "result.bin"
    original = secure_io._posix_revalidate_chain

    def refuse_after_link(path: Path, descriptors: list[int]) -> None:
        if destination.exists():
            raise secure_io.SecureIOError()
        original(path, descriptors)

    monkeypatch.setattr(secure_io, "_posix_revalidate_chain", refuse_after_link)
    with pytest.raises(secure_io.SecureIOError):
        secure_io.publish_create_new(destination, b"OWNED_PAYLOAD")

    assert not destination.exists()
    assert _temp_entries(tmp_path) == []
