from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import atomic_publish as publish  # noqa: E402


def test_atomic_write_preserves_exact_bytes_replaces_and_hardens(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o777)
    target = parent / "payload.txt"
    target.write_bytes(b"old")
    if os.name == "posix":
        os.chmod(parent, 0o777)
        os.chmod(target, 0o666)

    publish.atomic_write_private(target, "exact\r\nutf-8 \N{SNOWMAN}\n")

    assert target.read_bytes() == "exact\r\nutf-8 \N{SNOWMAN}\n".encode()
    if os.name == "posix":
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_uses_random_same_directory_temps_and_fsyncs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    created: list[tuple[Path, str, str, Path]] = []
    fsynced: list[int] = []
    real_mkstemp = publish.tempfile.mkstemp
    real_fsync = publish.os.fsync

    def recording_mkstemp(*, dir, prefix, suffix):
        descriptor, raw = real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
        created.append((Path(dir), prefix, suffix, Path(raw)))
        return descriptor, raw

    def recording_fsync(descriptor: int) -> None:
        fsynced.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(publish.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(publish.os, "fsync", recording_fsync)
    publish.atomic_write_private(target, b"first")
    publish.atomic_write_private(target, b"second")

    assert target.read_bytes() == b"second"
    assert len(created) == 2
    assert created[0][0] == created[1][0] == target.parent
    assert created[0][1:3] == created[1][1:3] == (
        ".payload.bin.",
        ".tmp",
    )
    assert created[0][3] != created[1][3]
    assert all(not item[3].exists() for item in created)
    assert len(fsynced) == 2


def test_atomic_write_cleans_its_temp_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    target.parent.mkdir()
    target.write_bytes(b"winner")

    def refuse_replace(_source: Path, _destination: Path) -> None:
        raise OSError(errno.EIO, "synthetic replace failure")

    monkeypatch.setattr(publish.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        publish.atomic_write_private(target, b"loser")

    assert target.read_bytes() == b"winner"
    assert not list(target.parent.glob(".payload.bin.*.tmp"))


def test_atomic_write_never_cleans_a_substituted_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    displaced: list[Path] = []

    def substitute_then_fail(source: Path, _destination: Path) -> None:
        owned = source.with_name(source.name + ".owned")
        os.rename(source, owned)
        displaced.append(owned)
        source.write_bytes(b"not-owned")
        raise OSError(errno.EIO, "synthetic post-substitution failure")

    monkeypatch.setattr(publish.os, "replace", substitute_then_fail)
    with pytest.raises(OSError, match="post-substitution"):
        publish.atomic_write_private(target, b"owned")

    substitutes = list(target.parent.glob(".payload.bin.*.tmp"))
    assert len(substitutes) == 1
    assert substitutes[0].read_bytes() == b"not-owned"
    assert len(displaced) == 1 and displaced[0].read_bytes() == b"owned"


def test_atomic_write_cleans_descriptor_and_temp_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    created_descriptors: list[int] = []
    real_mkstemp = publish.tempfile.mkstemp
    real_fdopen = publish.os.fdopen

    def recording_mkstemp(*, dir, prefix, suffix):
        descriptor, raw = real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
        created_descriptors.append(descriptor)
        return descriptor, raw

    class FailingHandle:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def write(self, _payload):
            raise OSError(errno.EIO, "synthetic write failure")

        def __exit__(self, *_args):
            self.handle.close()

    def failing_fdopen(descriptor: int, mode: str):
        return FailingHandle(real_fdopen(descriptor, mode))

    monkeypatch.setattr(publish.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(publish.os, "fdopen", failing_fdopen)
    with pytest.raises(OSError, match="synthetic write failure"):
        publish.atomic_write_private(target, b"payload")

    assert not target.exists()
    assert not list(target.parent.glob(".payload.bin.*.tmp"))
    assert len(created_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(created_descriptors[0])


def test_atomic_write_cleans_descriptor_and_temp_after_identity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    created: list[tuple[int, Path]] = []
    identity_events: list[str] = []
    real_mkstemp = publish.tempfile.mkstemp
    real_fstat = publish.os.fstat
    real_stat = publish.os.stat

    def recording_mkstemp(*, dir, prefix, suffix):
        descriptor, raw = real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
        created.append((descriptor, Path(raw)))
        return descriptor, raw

    def recording_stat(path, *, follow_symlinks=True):
        if (
            created
            and not isinstance(path, int)
            and Path(path) == created[0][1]
            and not follow_symlinks
        ):
            identity_events.append("name")
        return real_stat(path, follow_symlinks=follow_symlinks)

    def fail_fstat(_descriptor: int):
        identity_events.append("descriptor")
        raise OSError(errno.EIO, "synthetic identity failure")

    monkeypatch.setattr(publish.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(publish.os, "stat", recording_stat)
    monkeypatch.setattr(publish.os, "fstat", fail_fstat)
    with pytest.raises(OSError, match="synthetic identity failure"):
        publish.atomic_write_private(target, b"payload")

    assert len(created) == 1
    descriptor, temporary = created[0]
    assert identity_events[:2] == ["name", "descriptor"]
    assert not temporary.exists()
    with pytest.raises(OSError):
        real_fstat(descriptor)


def test_atomic_write_cleans_descriptor_and_temp_after_name_identity_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    created: list[tuple[int, Path]] = []
    identity_events: list[str] = []
    failed_name_stat = False
    real_mkstemp = publish.tempfile.mkstemp
    real_fstat = publish.os.fstat
    real_stat = publish.os.stat

    def recording_mkstemp(*, dir, prefix, suffix):
        descriptor, raw = real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
        created.append((descriptor, Path(raw)))
        return descriptor, raw

    def fail_first_name_stat(path, *, follow_symlinks=True):
        nonlocal failed_name_stat
        if (
            created
            and Path(path) == created[0][1]
            and not follow_symlinks
            and not failed_name_stat
        ):
            failed_name_stat = True
            identity_events.append("name")
            raise OSError(errno.EIO, "synthetic name identity failure")
        return real_stat(path, follow_symlinks=follow_symlinks)

    def recording_fstat(descriptor: int):
        identity_events.append("descriptor")
        return real_fstat(descriptor)

    monkeypatch.setattr(publish.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(publish.os, "stat", fail_first_name_stat)
    monkeypatch.setattr(publish.os, "fstat", recording_fstat)
    with pytest.raises(OSError, match="synthetic name identity failure"):
        publish.atomic_write_private(target, b"payload")

    assert len(created) == 1
    descriptor, temporary = created[0]
    assert identity_events[:2] == ["name", "descriptor"]
    assert not temporary.exists()
    with pytest.raises(OSError):
        real_fstat(descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor duplicate")
def test_name_identity_error_survives_cleanup_descriptor_dup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    created: list[tuple[int, Path]] = []
    failed_name_stat = False
    real_mkstemp = publish.tempfile.mkstemp
    real_fstat = publish.os.fstat
    real_stat = publish.os.stat

    def recording_mkstemp(*, dir, prefix, suffix):
        descriptor, raw = real_mkstemp(dir=dir, prefix=prefix, suffix=suffix)
        created.append((descriptor, Path(raw)))
        return descriptor, raw

    def fail_first_name_stat(path, *, follow_symlinks=True):
        nonlocal failed_name_stat
        if (
            created
            and Path(path) == created[0][1]
            and not follow_symlinks
            and not failed_name_stat
        ):
            failed_name_stat = True
            raise OSError(errno.EIO, "synthetic name identity failure")
        return real_stat(path, follow_symlinks=follow_symlinks)

    def fail_dup(_descriptor: int):
        raise OSError(errno.EMFILE, "synthetic descriptor exhaustion")

    monkeypatch.setattr(publish.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(publish.os, "stat", fail_first_name_stat)
    monkeypatch.setattr(publish.os, "dup", fail_dup)
    with pytest.raises(OSError, match="synthetic name identity failure") as caught:
        publish.atomic_write_private(target, b"payload")

    assert caught.value.errno == errno.EIO
    assert len(created) == 1
    descriptor, temporary = created[0]
    assert not temporary.exists()
    with pytest.raises(OSError):
        real_fstat(descriptor)


@pytest.mark.skipif(os.name != "posix", reason="open-file substitution probe")
def test_atomic_write_never_cleans_a_substitute_seen_before_descriptor_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "private" / "payload.bin"
    displaced: list[Path] = []
    replacement: list[Path] = []
    real_stat = publish.os.stat

    def substitute_before_named_stat(path, *, follow_symlinks=True):
        candidate = Path(path)
        if (
            not replacement
            and candidate.name.startswith(".payload.bin.")
            and candidate.name.endswith(".tmp")
            and not follow_symlinks
        ):
            owned = candidate.with_name(candidate.name + ".owned")
            os.rename(candidate, owned)
            candidate.write_bytes(b"not-owned")
            displaced.append(owned)
            replacement.append(candidate)
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(publish.os, "stat", substitute_before_named_stat)
    with pytest.raises(OSError, match="temporary identity changed"):
        publish.atomic_write_private(target, b"owned")

    assert len(replacement) == 1
    assert replacement[0].read_bytes() == b"not-owned"
    assert len(displaced) == 1
    assert displaced[0].read_bytes() == b""
    assert not target.exists()


@pytest.mark.parametrize("base", [str, bytes])
def test_atomic_write_rejects_str_and_bytes_subclasses(
    tmp_path: Path,
    base: type[str] | type[bytes],
) -> None:
    if base is str:
        class HostileStr(str):
            def encode(self, *_args, **_kwargs):
                return b"substituted"

        value = HostileStr("value")
    else:
        class HostileBytes(bytes):
            pass

        value = HostileBytes(b"value")

    target = tmp_path / "private" / "payload"
    with pytest.raises(TypeError, match="must be str or bytes"):
        publish.atomic_write_private(target, value)
    assert not target.exists()


def test_atomic_write_tolerates_missing_fchmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(publish.os, "fchmod", raising=False)
    target = tmp_path / "private" / "payload.bin"
    publish.atomic_write_private(target, b"bytes")
    assert target.read_bytes() == b"bytes"


@pytest.mark.parametrize(
    "case",
    ["missing", "file", "symlink", "non_sibling", "identical"],
)
def test_directory_publish_rejects_invalid_staging(
    tmp_path: Path,
    case: str,
) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    if case == "file":
        staging.write_bytes(b"not a directory")
    elif case == "symlink":
        real = tmp_path / "real-stage"
        real.mkdir()
        try:
            staging.symlink_to(real, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
    elif case == "non_sibling":
        staging.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        destination = other / "destination"
    elif case == "identical":
        staging.mkdir()
        destination = staging

    with pytest.raises(ValueError):
        publish.publish_directory_noreplace(staging, destination)

    if staging.exists() and case != "identical":
        assert not destination.exists()


def test_directory_publish_succeeds_atomically(tmp_path: Path) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    staging.mkdir()
    (staging / "payload").write_bytes(b"published")

    publish.publish_directory_noreplace(staging, destination)

    assert not staging.exists()
    assert (destination / "payload").read_bytes() == b"published"


@pytest.mark.parametrize("winner_kind", ["file", "directory", "symlink"])
def test_directory_publish_never_replaces_an_occupied_name(
    tmp_path: Path,
    winner_kind: str,
) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    staging.mkdir()
    (staging / "payload").write_bytes(b"loser")
    if winner_kind == "file":
        destination.write_bytes(b"file-winner")
    elif winner_kind == "directory":
        destination.mkdir()
        (destination / "sentinel").write_bytes(b"directory-winner")
    else:
        symlink_target = tmp_path / "symlink-winner"
        symlink_target.mkdir()
        try:
            destination.symlink_to(symlink_target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(FileExistsError):
        publish.publish_directory_noreplace(staging, destination)

    assert (staging / "payload").read_bytes() == b"loser"
    if winner_kind == "file":
        assert destination.read_bytes() == b"file-winner"
    elif winner_kind == "directory":
        assert (destination / "sentinel").read_bytes() == b"directory-winner"
    else:
        assert destination.is_symlink()
        assert destination.resolve() == symlink_target.resolve()


@pytest.mark.skipif(os.name != "posix", reason="unsupported POSIX branch")
def test_unsupported_posix_publication_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    staging.mkdir()
    monkeypatch.setattr(publish.sys, "platform", "unsupported-posix")

    with pytest.raises(OSError) as caught:
        publish.publish_directory_noreplace(staging, destination)

    assert caught.value.errno in {
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    assert staging.is_dir()
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX native-loader branch")
@pytest.mark.parametrize("failure", ["loader", "missing_symbol", "kernel"])
def test_native_posix_unavailability_is_normalized_to_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    staging.mkdir()

    if failure == "loader":
        def fail_loader(*_args, **_kwargs):
            raise OSError(errno.ENOENT, "synthetic loader failure")

        monkeypatch.setattr(publish.ctypes, "CDLL", fail_loader)
    elif failure == "missing_symbol":
        monkeypatch.setattr(
            publish.ctypes,
            "CDLL",
            lambda *_args, **_kwargs: object(),
        )
    else:
        class RefusingFunction:
            argtypes = None
            restype = None

            def __call__(self, *_args):
                publish.ctypes.set_errno(errno.EINVAL)
                return -1

        class RefusingLibc:
            renamex_np = RefusingFunction()
            renameat2 = RefusingFunction()

        monkeypatch.setattr(
            publish.ctypes,
            "CDLL",
            lambda *_args, **_kwargs: RefusingLibc(),
        )

    with pytest.raises(OSError) as caught:
        publish.publish_directory_noreplace(staging, destination)

    assert caught.value.errno in {
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    assert staging.is_dir()
    assert not destination.exists()


@pytest.mark.parametrize("failure_type", [NotImplementedError, AttributeError])
def test_windows_native_unavailability_is_normalized_to_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    staging = tmp_path / "stage"
    destination = tmp_path / "destination"
    staging.mkdir()

    def unavailable(*_args, **_kwargs):
        raise failure_type("synthetic Windows rename unavailability")

    monkeypatch.setattr(publish.os, "rename", unavailable)
    with pytest.raises(OSError) as caught:
        publish._windows_rename_noreplace(staging, destination)

    assert caught.value.errno in {
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    assert staging.is_dir()
    assert not destination.exists()
