#!/usr/bin/env python3
"""fetch_pangram_editlens_github.py — sibling fetcher with no HF auth.

`fetch_pangram_editlens.py` downloads Pangram's EditLens corpus from
HuggingFace (`pangram/editlens_iclr`). The HF dataset is license-
gated: users must accept the CC BY-NC-SA 4.0 terms in the HF UI,
generate an `HF_TOKEN`, and pass it to the fetcher. For maintainers
without an HF account — and for CI environments where the auth
dance is friction — the same data ships unauthenticated in
`pangramlabs/EditLens` on GitHub at
`https://raw.githubusercontent.com/pangramlabs/EditLens/<sha>/data/<file>.csv`.

This script downloads from the GitHub raw URLs instead. The
downstream `editlens_to_manifest.py` doesn't care which source
produced the CSVs — it consumes them by file shape, not by origin.

License posture is identical: CC BY-NC-SA 4.0, local-only,
gitignored under `ai-prose-baselines-private/editlens/`. The
emitted `NOTICE.md` declares both the GitHub source URL and the
pinned commit SHA so reproducibility is preserved.

Usage:

    # Default: download just the ESL slice (60 KB, smallest):
    python3 scripts/calibration/fetch_pangram_editlens_github.py \\
        --split nonnative_english

    # All seven splits (~92 MB total):
    python3 scripts/calibration/fetch_pangram_editlens_github.py \\
        --split all

    # Pin a specific commit SHA (recommended for calibration runs
    # whose results will be committed to the SETEC ledger):
    python3 scripts/calibration/fetch_pangram_editlens_github.py \\
        --split nonnative_english \\
        --commit-sha 05a588f15d792330ccaf91be8ee4fdb54ce26835

    # Re-download even if files exist:
    python3 scripts/calibration/fetch_pangram_editlens_github.py \\
        --split nonnative_english --refresh

The fetcher writes a `.fetch_record.json` alongside the CSVs that
records the GitHub commit SHA and per-file SHA-256 hashes. The
hashes provide tamper detection: a future re-fetch with the same
commit SHA must produce identical file content. The calibration
toolchain reads `.fetch_record.json` for provenance.

Stdlib only. Does NOT require `huggingface_hub` or `requests` —
this script unblocks the calibration run on machines that haven't
installed the calibration deps.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Resolve REPO_ROOT robustly (mirrors the HF fetcher's pattern).
# parents[4] is the actual repo root after the 1.16.0 plugin reorg.
REPO_ROOT = Path(__file__).resolve().parents[4]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "editlens"

GITHUB_OWNER = "pangramlabs"
GITHUB_REPO = "EditLens"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
GITHUB_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}"
)

# Splits the upstream repo ships in `data/`. Sizes are approximate
# (current as of the pinned commit); the fetcher recomputes them
# from the actual download.
KNOWN_SPLITS: dict[str, dict[str, str | int]] = {
    "nonnative_english": {"filename": "nonnative_english.csv"},  # ~62 KB
    "human_detectors": {"filename": "human_detectors.csv"},      # ~2 MB
    "val": {"filename": "val.csv"},                              # ~9 MB
    "test_enron": {"filename": "test_enron.csv"},                # ~15 MB
    "raid_10k": {"filename": "raid_10k.csv"},                    # ~17 MB
    "test_llama": {"filename": "test_llama.csv"},                # ~24 MB
    "test": {"filename": "test.csv"},                            # ~25 MB
}

# Default user-agent for the API + raw-content fetches. GitHub
# requires a UA on API calls; raw.githubusercontent.com is permissive.
USER_AGENT = (
    "setec-voiceprint/1.24.0 (+https://github.com/anotherpanacea-eng/"
    "setec-voiceprint; calibration corpus fetcher)"
)

# Default request timeout (seconds). Large CSVs (~25 MB) over
# slow connections need a generous ceiling; raise if needed.
DEFAULT_TIMEOUT = 120.0


# --------------- Hash + download helpers ------------------------


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, ``sha256:`` prefixed."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


_SSL_CONTEXT_CACHE: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """Build a default SSL context with cert-bundle fallback.

    Python.org's macOS installer ships without running
    ``Install Certificates.command``, so the system Python's default
    cert store can be empty even though TLS works. Try paths in
    decreasing order of reliability:

      1. ``certifi`` package's bundle (almost always present —
         ``certifi`` is a transitive dep of ``requests``, ``pip``,
         ``huggingface_hub``, and most HTTP libraries).
      2. macOS system bundle at ``/etc/ssl/cert.pem``.
      3. Plain ``ssl.create_default_context()``.

    Cached so subsequent calls don't re-resolve.
    """
    global _SSL_CONTEXT_CACHE
    if _SSL_CONTEXT_CACHE is not None:
        return _SSL_CONTEXT_CACHE
    ctx: ssl.SSLContext | None = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    if ctx is None:
        # macOS system bundle — fallback when certifi isn't installed.
        macos_bundle = Path("/etc/ssl/cert.pem")
        if macos_bundle.is_file():
            try:
                ctx = ssl.create_default_context(cafile=str(macos_bundle))
            except (OSError, ssl.SSLError):
                ctx = None
    if ctx is None:
        ctx = ssl.create_default_context()
    _SSL_CONTEXT_CACHE = ctx
    return ctx


def _http_get_bytes(
    url: str, *, timeout: float = DEFAULT_TIMEOUT,
) -> bytes:
    """Fetch a URL; return body bytes. Raises urllib errors verbatim
    so the caller can decide whether to retry or abort."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(
        req, timeout=timeout, context=_ssl_context(),
    ) as resp:
        if resp.status != 200:
            raise urllib.error.HTTPError(
                url, resp.status, f"{resp.status} {resp.reason}",
                resp.headers, None,
            )
        return resp.read()


def _http_get_json(
    url: str, *, timeout: float = DEFAULT_TIMEOUT,
) -> dict | list:
    raw = _http_get_bytes(url, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


# --------------- Commit SHA resolution --------------------------


def resolve_default_commit_sha(*, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Look up the current ``main`` commit SHA via the GitHub API.

    Used when the user doesn't pass ``--commit-sha`` — the fetcher
    pins the resolved SHA in ``.fetch_record.json`` so a later
    re-fetch can reproduce the same content. Calibration runs
    whose results commit to the SETEC ledger should always pass an
    explicit ``--commit-sha`` so the pin is loud rather than
    "whatever main was when I ran this."
    """
    url = f"{GITHUB_API_BASE}/commits/main"
    data = _http_get_json(url, timeout=timeout)
    sha = data.get("sha")
    if not sha or not isinstance(sha, str):
        raise RuntimeError(
            f"Could not resolve main commit SHA from {url}; got {data!r}"
        )
    return sha


def commit_sha_exists(sha: str, *, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Probe the GitHub API to confirm a commit SHA exists in the
    upstream repo. Cheap pre-flight check before downloading.

    Distinguishes "commit not found" (HTTP 404) from "couldn't reach
    GitHub" (every other error). Network-level failures re-raise so
    the caller can produce a clearer message than "commit not found"
    — the user might be hitting an SSL config issue, a corporate
    proxy, or rate limiting, not a missing commit.
    """
    url = f"{GITHUB_API_BASE}/commits/{sha}"
    try:
        _http_get_json(url, timeout=timeout)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
    # Other URLError / SSLError / timeout → re-raise so the caller
    # surfaces the actual problem rather than masking it as "not found".


# --------------- Download driver --------------------------------


def raw_url_for_split(split: str, sha: str) -> str:
    """Construct the raw.githubusercontent.com URL for one CSV."""
    if split not in KNOWN_SPLITS:
        raise KeyError(
            f"Unknown split {split!r}. Known: {sorted(KNOWN_SPLITS)}"
        )
    fn = KNOWN_SPLITS[split]["filename"]
    return f"{GITHUB_RAW_BASE}/{sha}/data/{fn}"


def download_split(
    split: str,
    sha: str,
    *,
    target_dir: Path,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[Path, str, int]:
    """Download one split's CSV. Returns (path, sha256, bytes).

    Idempotent unless ``refresh=True``: if a file with the right
    name already exists in ``target_dir``, skip the download and
    return the existing file's hash + size.
    """
    fn = str(KNOWN_SPLITS[split]["filename"])
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / fn
    if out_path.is_file() and not refresh:
        sys.stderr.write(
            f"  exists; skipping (pass --refresh to re-download): "
            f"{out_path.name}\n"
        )
        return out_path, file_sha256(out_path), out_path.stat().st_size

    url = raw_url_for_split(split, sha)
    sys.stderr.write(f"  fetching {url}\n")
    body = _http_get_bytes(url, timeout=timeout)
    out_path.write_bytes(body)
    sha256 = file_sha256(out_path)
    return out_path, sha256, len(body)


# --------------- NOTICE.md + fetch record -----------------------


def write_notice(
    target_dir: Path,
    *,
    commit_sha: str,
    fetched: list[tuple[str, Path, str, int]],
) -> Path:
    """Mirror the HF fetcher's NOTICE.md shape with GitHub-source
    attribution.

    ``fetched`` is a list of ``(split_name, path, sha256, byte_size)``
    tuples; the NOTICE enumerates them with hashes for tamper
    detection.
    """
    notice_path = target_dir / "NOTICE.md"
    iso_date = _dt.date.today().isoformat()
    file_lines: list[str] = []
    for split, path, sha256, size in sorted(fetched, key=lambda x: x[0]):
        rel = path.relative_to(target_dir) if path.is_relative_to(target_dir) else path
        file_lines.append(
            f"- `{rel}` ({size:,} bytes, `{sha256}`)"
        )
    body = f"""# EditLens corpus — license + provenance (GitHub fetch)

**Source:** https://github.com/{GITHUB_OWNER}/{GITHUB_REPO} (commit `{commit_sha}`)
**Raw download base:** {GITHUB_RAW_BASE}/{commit_sha}/data/
**HuggingFace mirror (license-gated, equivalent content):** https://huggingface.co/datasets/pangram/editlens_iclr
**Paper:** Thai et al., "EditLens: Quantifying the Extent of AI Editing in Text," ICLR 2026. arXiv:2510.03154.
**License:** CC BY-NC-SA 4.0
  https://creativecommons.org/licenses/by-nc-sa/4.0/

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_pangram_editlens_github.py` for the
purpose of locally calibrating SETEC's empirical per-signal
thresholds.

## DO NOT REDISTRIBUTE

The CC-NC clause prohibits commercial use; the SA clause requires
that adaptations carry the same license. SETEC ships under
GPL-3.0-or-later, which is incompatible with NC. SETEC's
calibration toolchain works on local copies only; aggregate
derived thresholds (single floating-point values summarizing many
corpus rows) are encoded into SETEC under GPL as a SETEC policy
stance: aggregate measurements of pipeline behavior, not
adaptations of any specific corpus row. Maintainers should revisit
this stance if these thresholds become commercially-distributed
SETEC defaults.

Per-row text files generated by
`scripts/calibration/editlens_to_manifest.py` also live in this
directory and inherit the same license posture.

If you are not the maintainer who fetched this corpus: do not
commit any file in this directory or any of its subdirectories to
SETEC's public repo. `ai-prose-baselines-private/` is gitignored;
keep it that way.

## License-card check

Unlike the HuggingFace fetcher (`fetch_pangram_editlens.py`), the
GitHub repo does not expose a structured dataset card via API.
This fetcher relies on the upstream repository's `LICENSE` file +
README declaration of CC BY-NC-SA 4.0 at fetch time. If the
upstream repo's licensing posture ever changes, this fetcher will
NOT detect that automatically — re-verify by visiting
`https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}` before any new
calibration run.

## Files fetched

{chr(10).join(file_lines)}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def write_fetch_record(
    target_dir: Path,
    *,
    commit_sha: str,
    splits: list[str],
    fetched: list[tuple[str, Path, str, int]],
) -> Path:
    """Pin the commit SHA + per-file hashes in a structured record.

    Read by `calibrate_thresholds.py` for provenance. Fields match
    the HF fetcher's record shape where possible (``revision`` ↔
    ``commit_sha``, ``repo_id`` ↔ ``github_repo``).
    """
    record_path = target_dir / ".fetch_record.json"
    record = {
        "source": "github",
        "github_repo": f"{GITHUB_OWNER}/{GITHUB_REPO}",
        "commit_sha": commit_sha,
        "revision": commit_sha,  # alias for HF-fetcher compatibility
        "fetch_date": _dt.date.today().isoformat(),
        "splits_requested": list(splits),
        "files": [
            {
                "split": split,
                "filename": path.name,
                "sha256": sha256,
                "bytes": size,
            }
            for split, path, sha256, size in sorted(
                fetched, key=lambda x: x[0],
            )
        ],
    }
    record_path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record_path


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fetch_pangram_editlens_github",
        description=(
            "Download Pangram's EditLens corpus from the public "
            "GitHub mirror at pangramlabs/EditLens. Stdlib only — no "
            "huggingface_hub, no HF_TOKEN, no license-acceptance UI. "
            "Sibling to fetch_pangram_editlens.py for users without "
            "HF auth."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--split", default="nonnative_english",
        choices=sorted(KNOWN_SPLITS) + ["all"],
        help=(
            "Which CSV split to download. Default: nonnative_english "
            "(60 KB, ESL-focused, smallest). 'all' downloads every "
            "split (~92 MB total)."
        ),
    )
    p.add_argument(
        "--commit-sha",
        help=(
            "Pin downloads to a specific commit SHA (recommended for "
            "calibration runs whose results will commit to the SETEC "
            "ledger). Default: resolve current main."
        ),
    )
    p.add_argument(
        "--target-dir", default=str(TARGET_DIR),
        help=(
            "Where to write the CSVs + NOTICE.md. Default: "
            "<repo>/ai-prose-baselines-private/editlens/."
        ),
    )
    p.add_argument(
        "--refresh", action="store_true",
        help="Re-download even if files exist.",
    )
    p.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )
    p.add_argument(
        "--no-verify-sha", action="store_true",
        help=(
            "Skip the cheap GitHub API probe that verifies the "
            "commit SHA exists. Useful when the API is rate-limited."
        ),
    )
    return p


def run(args: argparse.Namespace) -> int:
    target_dir = Path(args.target_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)

    if args.commit_sha:
        commit_sha = args.commit_sha
        if not args.no_verify_sha:
            sys.stderr.write(f"Verifying commit SHA {commit_sha}...\n")
            try:
                exists = commit_sha_exists(commit_sha, timeout=args.timeout)
            except urllib.error.URLError as exc:
                sys.stderr.write(
                    f"Could not reach GitHub API to verify SHA: {exc}\n"
                    "Pass --no-verify-sha if you trust the SHA and the "
                    "raw download will work without API access.\n"
                )
                return 2
            if not exists:
                sys.stderr.write(
                    f"Commit SHA {commit_sha} not found in "
                    f"{GITHUB_OWNER}/{GITHUB_REPO}. Aborting.\n"
                )
                return 2
    else:
        sys.stderr.write("Resolving current main commit SHA...\n")
        try:
            commit_sha = resolve_default_commit_sha(timeout=args.timeout)
        except (urllib.error.URLError, RuntimeError) as exc:
            sys.stderr.write(
                f"Could not resolve main SHA: {exc}\n"
                "If this is an SSL certificate error on macOS, run\n"
                "  /Applications/Python\\ 3.13/Install\\ Certificates.command\n"
                "(adjust the version in the path) to install the "
                "Python.org cert bundle. Or pip install certifi.\n"
            )
            return 2
        sys.stderr.write(
            f"  pinned to {commit_sha}\n"
            f"  (pass --commit-sha {commit_sha} to make this pin "
            "explicit on re-runs)\n"
        )

    splits = (
        list(KNOWN_SPLITS.keys()) if args.split == "all" else [args.split]
    )
    sys.stderr.write(
        f"Downloading {len(splits)} split(s) into {target_dir}\n"
    )

    fetched: list[tuple[str, Path, str, int]] = []
    for split in splits:
        try:
            path, sha256, size = download_split(
                split, commit_sha,
                target_dir=target_dir,
                refresh=args.refresh,
                timeout=args.timeout,
            )
        except urllib.error.URLError as exc:
            sys.stderr.write(f"  download failed for {split}: {exc}\n")
            return 2
        fetched.append((split, path, sha256, size))
        sys.stderr.write(
            f"    --> {path.name}: {size:,} bytes, {sha256}\n"
        )

    notice_path = write_notice(
        target_dir, commit_sha=commit_sha, fetched=fetched,
    )
    record_path = write_fetch_record(
        target_dir,
        commit_sha=commit_sha,
        splits=splits,
        fetched=fetched,
    )
    sys.stderr.write(
        f"\nWrote NOTICE.md to {notice_path}\n"
        f"Wrote .fetch_record.json to {record_path}\n"
        f"\nNext: convert to a SETEC manifest with\n"
        f"  python3 scripts/calibration/editlens_to_manifest.py \\\n"
        f"      --source {target_dir / KNOWN_SPLITS[splits[0]]['filename']} \\\n"
        f"      --preset editlens_{splits[0]} \\\n"
        f"      --out {target_dir / f'manifest_{splits[0]}.jsonl'} \\\n"
        f"      --text-dir {target_dir / f'{splits[0]}_text'}\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
