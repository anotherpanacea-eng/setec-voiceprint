#!/usr/bin/env python3
"""fetch_pan24_voightkampff.py

Local-only fetcher for the PAN@CLEF 2024 "Voight-Kampff" Generative-AI
Authorship Verification bootstrap corpus (spec 04 / `pan_replay`). Models
`fetch_pangram_editlens.py`: download into `ai-prose-baselines-private/pan24/`
(gitignored, never committed) and write a NOTICE.md with attribution +
the no-redistribution term.

Source: Zenodo record 10718757 (DOI 10.5281/zenodo.10718757),
  file `pan24-generative-authorship-news.zip` (~12 MB, openly downloadable).
Terms (Zenodo): "Copyrighted Material ... may be used only for research
  purposes. No redistribution allowed." → SETEC treats it LOCAL-ONLY: the
  corpus stays in ai-prose-baselines-private/ (the whole dir is gitignored),
  never committed; only aggregate measurements may ship in code.

Usage:  py -3.12 fetch_pan24_voightkampff.py [--refresh] [--dest DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import ssl
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from acquisition_core import is_private_safe_path  # type: ignore  # noqa: E402

ZENODO_URL = "https://zenodo.org/records/10718757/files/pan24-generative-authorship-news.zip?download=1"
DOI = "10.5281/zenodo.10718757"
ARCHIVE_NAME = "pan24-generative-authorship-news.zip"
# Pinned from Zenodo record 10718757. This is an upstream content-integrity
# identifier, not a password hash; MD5 is the digest family Zenodo publishes.
ARCHIVE_MD5 = "47e17f58fd3509a4c649119ada3ae78e"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 50_000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
_SSL_CONTEXT_CACHE: ssl.SSLContext | None = None

# Same REPO_ROOT idiom as fetch_pangram_editlens.py: scripts ship at
# ``<repo>/plugins/setec-voiceprint/scripts/calibration/foo.py``, so parents[4]
# is the repo root in dev and the marketplace root in an installed copy. The
# private corpus dir is that root's sibling-of-repo neighbour either way — never
# a machine-local absolute path (those don't travel across the fleet).
REPO_ROOT = Path(__file__).resolve().parents[4]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "pan24"
NOTICE = """# PAN@CLEF 2024 — Voight-Kampff Generative-AI Authorship Verification (bootstrap corpus)

- **Source:** Zenodo record {doi} — `{archive_name}`
- **Pinned archive MD5:** `{archive_md5}` (published by Zenodo)
- **Task:** "Given two texts, one human, one machine: pick out the human." (PAN24 + ELOQUENT)
- **Terms:** Copyrighted material; **research use only; NO redistribution.**
- **SETEC posture:** LOCAL-ONLY. This corpus lives under `ai-prose-baselines-private/`
  (the entire directory is gitignored) and is NEVER committed. Used by `pan_replay`
  (spec 04) as the clean side of (clean, obfuscated) robustness pairs. Only aggregate
  measurements (no corpus rows) may appear in shipped code.
- **Fetched by:** `scripts/calibration/fetch_pan24_voightkampff.py`
"""


def _resolve_destination(raw_dest: str | None) -> Path:
    """Return a private-only destination or refuse the write.

    This corpus is copyrighted and cannot be redistributed, so unlike generic
    acquisition tools there is intentionally no public-output escape hatch.
    """
    dest = Path(raw_dest).expanduser().resolve() if raw_dest else TARGET_DIR.resolve()
    if not is_private_safe_path(dest):
        raise ValueError(
            "PAN24 is research-only and may not be redistributed; --dest must "
            "resolve beneath a directory named 'ai-prose-baselines-private'"
        )
    return dest


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - matches Zenodo's published checksum
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_archive(path: Path) -> None:
    observed = _file_md5(path)
    if observed != ARCHIVE_MD5:
        raise ValueError(
            f"archive checksum mismatch for {path}: expected md5:{ARCHIVE_MD5}, "
            f"observed md5:{observed}; use --refresh to replace a corrupt cache"
        )


def _ssl_context() -> ssl.SSLContext:
    """Resolve a usable CA bundle, including Python.org macOS installs."""
    global _SSL_CONTEXT_CACHE
    if _SSL_CONTEXT_CACHE is not None:
        return _SSL_CONTEXT_CACHE
    context: ssl.SSLContext | None = None
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        context = None
    if context is None:
        macos_bundle = Path("/etc/ssl/cert.pem")
        if macos_bundle.is_file():
            try:
                context = ssl.create_default_context(cafile=str(macos_bundle))
            except (OSError, ssl.SSLError):
                context = None
    if context is None:
        context = ssl.create_default_context()
    _SSL_CONTEXT_CACHE = context
    return context


def _download_archive(zip_path: Path) -> None:
    """Stream, bound, verify, and atomically publish the Zenodo archive."""
    req = urllib.request.Request(
        ZENODO_URL,
        headers={"User-Agent": "Mozilla/5.0 (SETEC research fetcher)"},
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{ARCHIVE_NAME}.", suffix=".tmp", dir=str(zip_path.parent),
    )
    tmp_path = Path(tmp_name)
    digest = hashlib.md5()  # noqa: S324 - matches Zenodo's published checksum
    total = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            with urllib.request.urlopen(
                req,
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
                context=_ssl_context(),
            ) as response:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"archive exceeds download limit of {MAX_DOWNLOAD_BYTES} bytes"
                        )
                    digest.update(chunk)
                    fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        observed = digest.hexdigest()
        if observed != ARCHIVE_MD5:
            raise ValueError(
                f"download checksum mismatch: expected md5:{ARCHIVE_MD5}, "
                f"observed md5:{observed}"
            )
        os.replace(tmp_path, zip_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _safe_extract(z: zipfile.ZipFile, dest: Path) -> list[str]:
    """Extract, refusing any member that would land outside ``dest``.

    ``extractall`` follows absolute paths and ``..`` segments in member names, so
    a malicious or malformed archive can write anywhere the process can reach.
    Zenodo is a trusted host, but the guard is cheap and the fetcher runs against
    a URL, not a vetted local file.
    """
    dest = dest.resolve()
    members = z.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ValueError(
            f"archive has {len(members)} members; limit is {MAX_ARCHIVE_MEMBERS}"
        )
    total_uncompressed = 0
    names: list[str] = []
    for member in members:
        name = member.filename
        names.append(name)
        total_uncompressed += member.file_size
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ValueError(
                "archive exceeds uncompressed-size limit of "
                f"{MAX_UNCOMPRESSED_BYTES} bytes"
            )
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"refusing symbolic-link archive member: {name!r}")
        target = (dest / name).resolve()
        if target != dest and dest not in target.parents:
            raise ValueError(f"refusing archive member outside destination: {name!r}")
    z.extractall(dest, members=members)
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download even if present")
    ap.add_argument("--dest", default=None,
                    help=f"destination dir (default: {TARGET_DIR}, the gitignored private corpus dir)")
    a = ap.parse_args()
    try:
        dest = _resolve_destination(a.dest)
    except ValueError as exc:
        ap.error(str(exc))
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / ARCHIVE_NAME
    try:
        if zip_path.exists() and not a.refresh:
            print(
                f"already present: {zip_path} "
                f"({zip_path.stat().st_size/1e6:.1f} MB) -- use --refresh to re-pull"
            )
        else:
            print(f"downloading PAN24 bootstrap corpus ({DOI})...", flush=True)
            _download_archive(zip_path)
            print(
                f"  -> {zip_path} ({zip_path.stat().st_size/1e6:.1f} MB)",
                flush=True,
            )
        _verify_archive(zip_path)
        with zipfile.ZipFile(zip_path) as z:
            names = _safe_extract(z, dest)
    except (OSError, ValueError, zipfile.BadZipFile, urllib.error.URLError) as exc:
        sys.stderr.write(f"PAN24 fetch failed: {exc}\n")
        return 2
    print(f"  unzipped {len(names)} entries -> {dest}")
    (dest / "NOTICE.md").write_text(
        NOTICE.format(doi=DOI, archive_name=ARCHIVE_NAME, archive_md5=ARCHIVE_MD5),
        encoding="utf-8",
    )
    print("  wrote NOTICE.md (research-only, no-redistribution, local-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
