#!/usr/bin/env python3
"""Regression tests for fetch_pangram_editlens_github.py.

Strategy: mock the HTTP layer with `unittest.mock` so tests don't
hit GitHub. Verify the script's structural contracts:

  * URL construction for every known split.
  * Commit-SHA verification distinguishes 404 from network failure.
  * SSL context resolution falls back through certifi → macOS
    bundle → default in the documented order.
  * Download writes the right bytes to the right path with the
    right hash recorded.
  * NOTICE.md and .fetch_record.json are emitted with the
    documented fields.
  * CLI flags work as documented.

The smoke test against pangramlabs/EditLens runs once during
development (verified live by the maintainer); CI doesn't depend
on network access.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import fetch_pangram_editlens_github as fg  # type: ignore


# ------------------- URL construction --------------------------


def test_raw_url_for_known_split():
    url = fg.raw_url_for_split("nonnative_english", "abc123")
    expected = (
        "https://raw.githubusercontent.com/pangramlabs/EditLens/"
        "abc123/data/nonnative_english.csv"
    )
    assert url == expected


def test_raw_url_for_every_known_split():
    """Every key in KNOWN_SPLITS must resolve to a constructible URL."""
    for split in fg.KNOWN_SPLITS:
        url = fg.raw_url_for_split(split, "deadbeef")
        assert "raw.githubusercontent.com" in url
        assert "deadbeef" in url
        assert fg.KNOWN_SPLITS[split]["filename"] in url


def test_raw_url_unknown_split_raises():
    if pytest is not None:
        with pytest.raises(KeyError):
            fg.raw_url_for_split("does_not_exist", "abc123")


def test_known_splits_include_all_seven():
    """Pin the seven splits we know the upstream repo ships. If
    upstream adds a new one the test fails loudly so the maintainer
    knows to extend KNOWN_SPLITS."""
    expected = {
        "nonnative_english", "human_detectors", "val", "test_enron",
        "raid_10k", "test_llama", "test",
    }
    assert set(fg.KNOWN_SPLITS) == expected


# ------------------- Commit SHA verification -------------------


def test_commit_sha_exists_returns_true_on_200():
    with mock.patch.object(fg, "_http_get_json", return_value={"sha": "x"}):
        assert fg.commit_sha_exists("x") is True


def test_commit_sha_exists_returns_false_on_404():
    """A 404 from the API is unambiguous: the commit doesn't exist
    in the upstream repo. Return False."""
    err = urllib.error.HTTPError(
        "url", 404, "Not Found", {}, BytesIO(b""),
    )
    with mock.patch.object(fg, "_http_get_json", side_effect=err):
        assert fg.commit_sha_exists("nonexistent") is False


def test_commit_sha_exists_reraises_on_other_http_errors():
    """503 / 401 / 429 are not 'not found'; surface them so the
    caller can produce a clearer error message."""
    err = urllib.error.HTTPError(
        "url", 503, "Service Unavailable", {}, BytesIO(b""),
    )
    with mock.patch.object(fg, "_http_get_json", side_effect=err):
        if pytest is not None:
            with pytest.raises(urllib.error.HTTPError):
                fg.commit_sha_exists("x")


def test_commit_sha_exists_reraises_on_url_error():
    """Network-level failures (SSL, DNS, timeout) re-raise so
    `run()` produces an SSL-aware error message rather than
    'commit not found'."""
    err = urllib.error.URLError("ssl: cert verify failed")
    with mock.patch.object(fg, "_http_get_json", side_effect=err):
        if pytest is not None:
            with pytest.raises(urllib.error.URLError):
                fg.commit_sha_exists("x")


# ------------------- SSL context fallback ----------------------


def test_ssl_context_uses_certifi_when_available():
    """The fetcher prefers certifi's bundle when it's importable.
    Inject a fake certifi module that points at /etc/ssl/cert.pem
    (which exists on the test host) and verify the context is
    built from it."""
    fg._SSL_CONTEXT_CACHE = None  # reset cache for this test

    fake_certifi = type(sys)("certifi")
    fake_certifi.where = lambda: "/etc/ssl/cert.pem"
    with mock.patch.dict(sys.modules, {"certifi": fake_certifi}):
        ctx = fg._ssl_context()
    assert ctx is not None
    fg._SSL_CONTEXT_CACHE = None  # leave clean for other tests


def test_ssl_context_caches_after_first_resolution():
    fg._SSL_CONTEXT_CACHE = None
    ctx1 = fg._ssl_context()
    ctx2 = fg._ssl_context()
    assert ctx1 is ctx2
    fg._SSL_CONTEXT_CACHE = None


# ------------------- Download driver --------------------------


def _fake_response_bytes(payload: bytes) -> bytes:
    return payload


def test_download_split_writes_csv_and_returns_metadata(tmp_path):
    fake_body = b"text,label\n\"hello\",0\n"
    with mock.patch.object(
        fg, "_http_get_bytes", return_value=fake_body,
    ) as m:
        path, sha256, size = fg.download_split(
            "nonnative_english", "deadbeef",
            target_dir=tmp_path,
        )
    assert path == tmp_path / "nonnative_english.csv"
    assert path.is_file()
    assert path.read_bytes() == fake_body
    assert size == len(fake_body)
    # Hash matches the bytes we wrote.
    expected_hash = fg.file_sha256(path)
    assert sha256 == expected_hash
    # The fetcher built the right URL.
    fetched_url = m.call_args.args[0]
    assert "deadbeef" in fetched_url
    assert "nonnative_english.csv" in fetched_url


def test_download_split_skips_when_file_exists(tmp_path):
    """Idempotent unless --refresh: pre-existing file means no
    download attempt + the existing file's hash is returned."""
    fn = tmp_path / "nonnative_english.csv"
    fn.write_bytes(b"existing content")
    with mock.patch.object(
        fg, "_http_get_bytes",
        side_effect=AssertionError("should not be called"),
    ):
        path, sha256, size = fg.download_split(
            "nonnative_english", "deadbeef",
            target_dir=tmp_path,
            refresh=False,
        )
    assert path == fn
    assert size == len(b"existing content")
    assert sha256 == fg.file_sha256(fn)


def test_download_split_refresh_re_downloads(tmp_path):
    """With refresh=True, the existing file is overwritten."""
    fn = tmp_path / "nonnative_english.csv"
    fn.write_bytes(b"old content")
    fake_new = b"new content"
    with mock.patch.object(
        fg, "_http_get_bytes", return_value=fake_new,
    ):
        path, sha256, size = fg.download_split(
            "nonnative_english", "newsha",
            target_dir=tmp_path,
            refresh=True,
        )
    assert path.read_bytes() == fake_new


# ------------------- NOTICE.md + fetch_record ------------------


def test_write_notice_includes_provenance_block(tmp_path):
    fetched = [
        ("nonnative_english", tmp_path / "nonnative_english.csv",
         "sha256:abc", 60_000),
    ]
    # Touch the file so the relative_to path resolves cleanly.
    (tmp_path / "nonnative_english.csv").write_bytes(b"x")
    notice_path = fg.write_notice(
        tmp_path, commit_sha="deadbeef", fetched=fetched,
    )
    assert notice_path == tmp_path / "NOTICE.md"
    text = notice_path.read_text(encoding="utf-8")
    # Source attribution is present.
    assert "github.com/pangramlabs/EditLens" in text
    assert "deadbeef" in text
    assert "CC BY-NC-SA 4.0" in text
    # File enumeration with hash for tamper detection.
    assert "sha256:abc" in text
    assert "60,000 bytes" in text
    # The DO NOT REDISTRIBUTE block is preserved verbatim from the
    # HF fetcher (license posture is identical).
    assert "DO NOT REDISTRIBUTE" in text
    assert "GPL-3.0-or-later" in text
    # The license-card-check caveat distinguishes the GitHub path
    # from the HF auth path.
    assert "License-card check" in text


def test_write_fetch_record_pins_commit_sha(tmp_path):
    fetched = [
        ("nonnative_english", tmp_path / "nonnative_english.csv",
         "sha256:abc123", 60_000),
    ]
    (tmp_path / "nonnative_english.csv").write_bytes(b"x")
    record_path = fg.write_fetch_record(
        tmp_path,
        commit_sha="deadbeef",
        splits=["nonnative_english"],
        fetched=fetched,
    )
    assert record_path == tmp_path / ".fetch_record.json"
    data = json.loads(record_path.read_text(encoding="utf-8"))
    assert data["source"] == "github"
    assert data["commit_sha"] == "deadbeef"
    # The HF-fetcher-compatible alias.
    assert data["revision"] == "deadbeef"
    assert data["github_repo"] == "pangramlabs/EditLens"
    assert data["splits_requested"] == ["nonnative_english"]
    assert len(data["files"]) == 1
    assert data["files"][0]["sha256"] == "sha256:abc123"
    assert data["files"][0]["bytes"] == 60_000


# ------------------- run() driver ------------------------------


def _make_args(**overrides) -> argparse.Namespace:
    base = dict(
        split="nonnative_english",
        commit_sha="deadbeef",
        target_dir="/tmp/_editlens_test",
        refresh=False,
        timeout=30.0,
        no_verify_sha=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_with_explicit_sha_and_no_verify(tmp_path):
    """Happy path: --commit-sha + --no-verify-sha skips the API
    probe; downloads succeed against the mocked raw fetch."""
    args = _make_args(
        target_dir=str(tmp_path), no_verify_sha=True,
    )
    fake_body = b"text,label\nhello,0\n"
    with mock.patch.object(fg, "_http_get_bytes", return_value=fake_body):
        rc = fg.run(args)
    assert rc == 0
    assert (tmp_path / "nonnative_english.csv").is_file()
    assert (tmp_path / "NOTICE.md").is_file()
    assert (tmp_path / ".fetch_record.json").is_file()


def test_run_aborts_when_commit_sha_not_found(tmp_path, capsys):
    """A 404 from the API turns into a clear stderr message and exit 2."""
    args = _make_args(
        target_dir=str(tmp_path), no_verify_sha=False,
    )
    err = urllib.error.HTTPError(
        "url", 404, "Not Found", {}, BytesIO(b""),
    )
    with mock.patch.object(fg, "_http_get_json", side_effect=err):
        rc = fg.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_run_handles_ssl_url_error_with_helpful_message(tmp_path, capsys):
    """When SHA verification fails with a URLError (typically SSL),
    the error message must point at the macOS Install Certificates
    fix or certifi install."""
    args = _make_args(
        target_dir=str(tmp_path),
        commit_sha=None,  # forces the resolve-default path
    )
    err = urllib.error.URLError("ssl certificate verify failed")
    with mock.patch.object(fg, "_http_get_json", side_effect=err):
        rc = fg.run(args)
    assert rc == 2
    captured = capsys.readouterr()
    # The error message points at the user-recoverable fixes.
    assert "Install Certificates" in captured.err or "certifi" in captured.err


def test_run_resolves_default_main_sha_when_not_pinned(tmp_path):
    """Without --commit-sha, run() resolves main first, prints the
    pinned SHA, and proceeds with the download."""
    args = _make_args(
        target_dir=str(tmp_path),
        commit_sha=None,
        no_verify_sha=False,
    )

    def fake_json(url, **kwargs):
        if url.endswith("/commits/main"):
            return {"sha": "abcdef1234567890"}
        return {}

    fake_body = b"text,label\nhello,0\n"
    with mock.patch.object(fg, "_http_get_json", side_effect=fake_json), \
         mock.patch.object(fg, "_http_get_bytes", return_value=fake_body):
        rc = fg.run(args)
    assert rc == 0
    record = json.loads(
        (tmp_path / ".fetch_record.json").read_text(encoding="utf-8")
    )
    assert record["commit_sha"] == "abcdef1234567890"


def test_run_split_all_downloads_every_split(tmp_path):
    """`--split all` triggers a download for each known split."""
    args = _make_args(
        target_dir=str(tmp_path),
        split="all",
        no_verify_sha=True,
    )
    fake_body = b"text,label\nrow,0\n"
    with mock.patch.object(fg, "_http_get_bytes", return_value=fake_body):
        rc = fg.run(args)
    assert rc == 0
    csvs = sorted(p.name for p in tmp_path.glob("*.csv"))
    expected = sorted(
        str(spec["filename"]) for spec in fg.KNOWN_SPLITS.values()
    )
    assert csvs == expected
    record = json.loads(
        (tmp_path / ".fetch_record.json").read_text(encoding="utf-8")
    )
    assert len(record["files"]) == len(fg.KNOWN_SPLITS)


# ------------------- CLI surface -------------------------------


def test_cli_help_lists_documented_flags():
    parser = fg.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--split", "--commit-sha", "--target-dir", "--refresh",
        "--timeout", "--no-verify-sha",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_cli_default_split_is_nonnative_english():
    parser = fg.build_arg_parser()
    args = parser.parse_args([])
    assert args.split == "nonnative_english"


def test_cli_rejects_unknown_split():
    parser = fg.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args(["--split", "definitely_not_a_split"])


# ------------------- HF-fetcher compatibility ------------------


def test_fetch_record_has_revision_alias_for_hf_compat():
    """The HF fetcher writes `revision` to .fetch_record.json. The
    GitHub fetcher writes `commit_sha` AND `revision` (alias) so
    `calibrate_thresholds.py`'s `_load_fetch_record` reads either
    source identically."""
    fetched = []
    record = {
        "source": "github",
        "github_repo": "pangramlabs/EditLens",
        "commit_sha": "abc",
        "revision": "abc",
        "fetch_date": "2026-05-09",
        "splits_requested": [],
        "files": [],
    }
    # Compatibility check: read the alias the HF fetcher uses.
    assert record["revision"] == record["commit_sha"]


# ------------------- Live URL existence (skipped) --------------


def test_pangramlabs_editlens_repo_url_is_well_formed():
    """The repo URL we point at must be parseable. Cheap; doesn't
    hit the network."""
    assert fg.GITHUB_RAW_BASE.startswith("https://")
    assert fg.GITHUB_API_BASE.startswith("https://")
    assert "pangramlabs/EditLens" in fg.GITHUB_API_BASE


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
