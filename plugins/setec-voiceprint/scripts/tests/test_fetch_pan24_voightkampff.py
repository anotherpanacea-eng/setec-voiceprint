#!/usr/bin/env python3
"""Model-free regression tests for the private PAN24 fetcher."""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

CALIBRATION_DIR = Path(__file__).resolve().parents[1] / "calibration"
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

import fetch_pan24_voightkampff as pan24  # type: ignore  # noqa: E402


def _zip_bytes(name: str = "dataset/clean.txt", body: bytes = b"sample") -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(name, body)
    return payload.getvalue()


def test_destination_requires_private_marker(tmp_path: Path):
    with pytest.raises(ValueError, match="ai-prose-baselines-private"):
        pan24._resolve_destination(str(tmp_path / "tracked-output"))


def test_destination_accepts_private_marker(tmp_path: Path):
    private = tmp_path / "ai-prose-baselines-private" / "pan24"
    assert pan24._resolve_destination(str(private)) == private.resolve()


@pytest.mark.parametrize("name", ("../escape.txt", "/tmp/escape.txt"))
def test_safe_extract_refuses_paths_outside_destination(tmp_path: Path, name: str):
    archive_path = tmp_path / "bad.zip"
    archive_path.write_bytes(_zip_bytes(name))
    destination = tmp_path / "dest"
    destination.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(ValueError, match="outside destination"):
            pan24._safe_extract(archive, destination)


def test_safe_extract_writes_valid_member(tmp_path: Path):
    archive_path = tmp_path / "ok.zip"
    archive_path.write_bytes(_zip_bytes())
    destination = tmp_path / "dest"
    destination.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        names = pan24._safe_extract(archive, destination)
    assert names == ["dataset/clean.txt"]
    assert (destination / "dataset" / "clean.txt").read_bytes() == b"sample"


def test_safe_extract_refuses_oversized_uncompressed_archive(tmp_path: Path):
    member = SimpleNamespace(
        filename="dataset/huge.txt",
        file_size=pan24.MAX_UNCOMPRESSED_BYTES + 1,
        external_attr=0,
    )
    fake_archive = mock.Mock()
    fake_archive.infolist.return_value = [member]
    with pytest.raises(ValueError, match="uncompressed-size limit"):
        pan24._safe_extract(fake_archive, tmp_path)
    fake_archive.extractall.assert_not_called()


def test_download_is_verified_before_atomic_publish(tmp_path: Path):
    payload = _zip_bytes()
    expected = hashlib.md5(payload).hexdigest()  # noqa: S324 - test fixture
    target = tmp_path / pan24.ARCHIVE_NAME
    fake_context = object()
    with (
        mock.patch.object(pan24, "ARCHIVE_MD5", expected),
        mock.patch.object(pan24, "_ssl_context", return_value=fake_context),
        mock.patch.object(
            pan24.urllib.request, "urlopen", return_value=io.BytesIO(payload),
        ) as mocked_open,
    ):
        pan24._download_archive(target)
    assert target.read_bytes() == payload
    assert list(tmp_path.glob(f".{pan24.ARCHIVE_NAME}.*.tmp")) == []
    assert mocked_open.call_args.kwargs == {
        "timeout": pan24.DOWNLOAD_TIMEOUT_SECONDS,
        "context": fake_context,
    }


def test_ssl_context_uses_certifi_bundle_when_available(monkeypatch):
    fake_certifi = SimpleNamespace(where=lambda: "/tmp/test-ca-bundle.pem")
    sentinel = object()
    pan24._SSL_CONTEXT_CACHE = None
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    with mock.patch.object(
        pan24.ssl, "create_default_context", return_value=sentinel,
    ) as create_context:
        assert pan24._ssl_context() is sentinel
    create_context.assert_called_once_with(cafile="/tmp/test-ca-bundle.pem")
    pan24._SSL_CONTEXT_CACHE = None


def test_ssl_context_falls_back_after_broken_certifi_bundle(monkeypatch):
    fake_certifi = SimpleNamespace(where=lambda: "/missing/test-ca-bundle.pem")
    sentinel = object()
    calls = []

    def _create_context(*, cafile=None):
        calls.append(cafile)
        if cafile == "/missing/test-ca-bundle.pem":
            raise FileNotFoundError(cafile)
        return sentinel

    pan24._SSL_CONTEXT_CACHE = None
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setattr(pan24.ssl, "create_default_context", _create_context)
    assert pan24._ssl_context() is sentinel
    assert calls[0] == "/missing/test-ca-bundle.pem"
    assert len(calls) == 2  # system bundle when present, otherwise default store
    pan24._SSL_CONTEXT_CACHE = None


def test_failed_refresh_preserves_existing_archive(tmp_path: Path):
    target = tmp_path / pan24.ARCHIVE_NAME
    target.write_bytes(b"previous valid cache")
    bad_payload = b"replacement with the wrong digest"
    with (
        mock.patch.object(pan24, "ARCHIVE_MD5", "0" * 32),
        mock.patch.object(pan24, "_ssl_context", return_value=object()),
        mock.patch.object(
            pan24.urllib.request, "urlopen", return_value=io.BytesIO(bad_payload),
        ),
        pytest.raises(ValueError, match="download checksum mismatch"),
    ):
        pan24._download_archive(target)
    assert target.read_bytes() == b"previous valid cache"
    assert list(tmp_path.glob(f".{pan24.ARCHIVE_NAME}.*.tmp")) == []


def test_cached_archive_checksum_mismatch_is_rejected(tmp_path: Path):
    target = tmp_path / pan24.ARCHIVE_NAME
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="archive checksum mismatch"):
        pan24._verify_archive(target)
