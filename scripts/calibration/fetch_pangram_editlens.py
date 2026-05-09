#!/usr/bin/env python3
"""fetch_pangram_editlens.py

Step 2 of the calibration toolchain. Downloads Pangram Labs' EditLens
corpus from HuggingFace (`pangram/editlens_iclr`) into
`ai-prose-baselines-private/editlens/` and writes a NOTICE.md with
attribution + license declaration + redistribution prohibition.

License: EditLens is CC BY-NC-SA 4.0. SETEC's calibration toolchain
treats it as local-only — the corpus content stays in
ai-prose-baselines-private/ (gitignored), never committed. SETEC's
calibrated thresholds (single floating-point values summarizing many
corpus rows) are encoded in GPL-3 SETEC code as a SETEC policy
stance: aggregate measurements of pipeline behavior, not adaptations
of any specific corpus row. Maintainers should revisit this stance
if these thresholds ever ship as commercially-distributed defaults.

Usage:

    # Fetch the smallest split (ESL, ~62 KB on the GitHub mirror):
    python3 scripts/calibration/fetch_pangram_editlens.py \\
        --split nonnative_english

    # Fetch all available splits:
    python3 scripts/calibration/fetch_pangram_editlens.py --split all

    # Re-download even if files exist:
    python3 scripts/calibration/fetch_pangram_editlens.py --refresh

Prerequisites:

  1. `pip install -r requirements-calibration.txt` (huggingface_hub).
  2. Accept the dataset's license terms at
     https://huggingface.co/datasets/pangram/editlens_iclr
  3. Set the HF access token:
       export HF_TOKEN=<your token>
     or pass --token <path-to-token-file>.

If huggingface_hub isn't installed, this script prints the install
command and exits cleanly. If the HF dataset's declared license
differs from CC BY-NC-SA 4.0 at fetch time, this script refuses to
proceed (the corpus may have been re-licensed; the legal posture
needs review).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "editlens"

HF_REPO_ID = "pangram/editlens_iclr"
EXPECTED_LICENSE_PATTERNS = ("cc-by-nc-sa-4.0", "cc-by-nc-sa-4-0")

# Splits Pangram publishes as separate files. The HF dataset may not
# expose all of these as named splits; the script also supports
# fetching the whole repo via `--split all`.
KNOWN_SPLITS = (
    "nonnative_english",
    "test",
    "val",
    "test_enron",
    "test_llama",
    "raid_10k",
    "human_detectors",
)


def _load_token(args: argparse.Namespace) -> str | None:
    """Return HF token from --token (file path or env-var name) or
    fall back to HF_TOKEN env var."""
    if args.token:
        # File path?
        p = Path(args.token).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        # Env var name?
        env_val = os.environ.get(args.token)
        if env_val:
            return env_val.strip()
        # Treat as literal token if neither path nor env match.
        return args.token.strip()
    return os.environ.get("HF_TOKEN")


def _check_huggingface_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401
        return True
    except ImportError:
        sys.stderr.write(
            "huggingface_hub is not installed. Install with:\n"
            "  pip install -r requirements-calibration.txt\n"
            "(see internal/SPEC_calibration_toolchain.md for context)\n"
        )
        return False


def _verify_license(token: str | None) -> tuple[bool, str]:
    """Read the HF dataset card and verify the license string matches
    CC BY-NC-SA 4.0. Returns (ok, observed_license)."""
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID)
    license_str = ""
    if info.card_data:
        license_str = (info.card_data.get("license") or "").strip().lower()
    if not license_str and getattr(info, "tags", None):
        for tag in info.tags:
            if tag.startswith("license:"):
                license_str = tag.split(":", 1)[1].strip().lower()
                break
    if any(p in license_str for p in EXPECTED_LICENSE_PATTERNS):
        return True, license_str
    return False, license_str


def _resolve_revision(token: str | None) -> str:
    """Get the current main-branch revision SHA so PROVENANCE.md can
    pin a specific corpus version."""
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID)
    return getattr(info, "sha", "") or ""


def _list_repo_files(token: str | None) -> list[str]:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    return list(api.list_repo_files(HF_REPO_ID, repo_type="dataset"))


def _select_files(repo_files: list[str], split: str) -> list[str]:
    """Return repo-relative paths to download. Handles the typical HF
    dataset layouts: `data/<split>-<n-of-n>.parquet`,
    `<split>/data.parquet`, or top-level `<split>.csv` / `<split>.parquet`."""
    if split == "all":
        # Pull every parquet/CSV file in the repo.
        return [
            f for f in repo_files
            if f.endswith((".parquet", ".csv"))
        ]
    candidates: list[str] = []
    for f in repo_files:
        base = Path(f).name.lower()
        # Match split name in basename (handles
        # "nonnative_english.parquet", "nonnative_english-00000-of-00001.parquet",
        # "data/nonnative_english.parquet", etc.).
        if split in base:
            if f.endswith((".parquet", ".csv")):
                candidates.append(f)
    return candidates


def _download(
    repo_files: list[str], target_dir: Path, token: str | None,
) -> list[Path]:
    """Download specified files from the HF repo into target_dir,
    preserving the in-repo path structure."""
    from huggingface_hub import hf_hub_download  # type: ignore

    fetched: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for repo_path in repo_files:
        local_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=repo_path,
            repo_type="dataset",
            local_dir=str(target_dir),
            token=token,
        )
        fetched.append(Path(local_path))
    return fetched


def _write_notice(
    target_dir: Path, revision: str, observed_license: str,
    fetched_files: list[Path],
) -> Path:
    notice_path = target_dir / "NOTICE.md"
    iso_date = _dt.date.today().isoformat()
    rel_files = sorted(
        str(p.relative_to(target_dir)) for p in fetched_files
    )
    body = f"""# EditLens corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**GitHub mirror (paper-companion code only):** https://github.com/pangramlabs/EditLens
**Paper:** Thai et al., "EditLens: Quantifying the Extent of AI Editing in Text," ICLR 2026. arXiv:2510.03154.
**License:** CC BY-NC-SA 4.0 (observed at fetch time: `{observed_license or "unknown"}`)
  https://creativecommons.org/licenses/by-nc-sa/4.0/

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_pangram_editlens.py` for the purpose of
locally calibrating SETEC's empirical per-signal thresholds.

## DO NOT REDISTRIBUTE

The CC-NC clause prohibits commercial use; the SA clause requires
that adaptations carry the same license. SETEC ships under
GPL-3.0-or-later, which is incompatible with NC. SETEC's calibration
toolchain works on local copies only; aggregate derived thresholds
(single floating-point values summarizing many corpus rows) are
encoded into SETEC under GPL as a SETEC policy stance: aggregate
measurements of pipeline behavior, not adaptations of any specific
corpus row. Maintainers should revisit this stance if these
thresholds become commercially-distributed SETEC defaults.

Per-row text files generated by `scripts/calibration/editlens_to_manifest.py`
also live in this directory and inherit the same license posture.

If you are not the maintainer who fetched this corpus: do not commit
any file in this directory or any of its subdirectories to SETEC's
public repo. `ai-prose-baselines-private/` is gitignored; keep it
that way.

## Files fetched

{chr(10).join(f"- `{f}`" for f in rel_files)}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def _write_revision_record(target_dir: Path, revision: str) -> Path:
    """Record the HF revision SHA in a stable JSON file so
    calibrate_thresholds.py can read it for provenance."""
    record_path = target_dir / ".fetch_record.json"
    record = {
        "repo_id": HF_REPO_ID,
        "revision": revision,
        "fetch_date": _dt.date.today().isoformat(),
    }
    record_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    return record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download Pangram's EditLens corpus from HuggingFace into "
            "ai-prose-baselines-private/editlens/."
        )
    )
    parser.add_argument(
        "--split", default="nonnative_english",
        help=(
            "Which split to fetch. Default: nonnative_english (smallest, "
            "ESL-focused). Use 'all' to fetch every parquet/CSV in the repo. "
            f"Known: {', '.join(KNOWN_SPLITS)}, all"
        ),
    )
    parser.add_argument(
        "--token", default=None,
        help=(
            "HF access token: a literal token, a file path, or an env-var "
            "name. If omitted, reads HF_TOKEN env var. Required because the "
            "dataset is gated."
        ),
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Re-download even if files exist locally.",
    )
    parser.add_argument(
        "--skip-license-check", action="store_true",
        help=(
            "Bypass the CC BY-NC-SA 4.0 verification. Use only if you have "
            "verified the license through another channel; the calibration "
            "toolchain's legal posture depends on this license."
        ),
    )
    args = parser.parse_args(argv)

    if not _check_huggingface_hub():
        return 1

    token = _load_token(args)
    if not token:
        sys.stderr.write(
            "No HF access token found. Set HF_TOKEN env var or pass "
            "--token. The dataset is gated on HuggingFace; you must "
            "accept the license terms at "
            f"https://huggingface.co/datasets/{HF_REPO_ID} first.\n"
        )
        return 1

    if not args.skip_license_check:
        ok, observed = _verify_license(token)
        if not ok:
            sys.stderr.write(
                f"License mismatch. Expected CC BY-NC-SA 4.0; observed "
                f"{observed!r} on the HF dataset card. Refusing to "
                f"proceed; the calibration toolchain's legal posture "
                f"depends on the license. Re-run with --skip-license-"
                f"check only after manual verification.\n"
            )
            return 2
    else:
        observed = "skipped"

    revision = _resolve_revision(token)
    if not revision:
        sys.stderr.write(
            "Could not resolve HF revision SHA; provenance will be "
            "incomplete. Aborting.\n"
        )
        return 3

    repo_files = _list_repo_files(token)
    files_to_download = _select_files(repo_files, args.split)
    if not files_to_download:
        sys.stderr.write(
            f"No matching files for split {args.split!r} in repo. "
            f"Available files:\n  "
            + "\n  ".join(repo_files[:30])
            + ("\n  ..." if len(repo_files) > 30 else "")
            + "\n"
        )
        return 4

    if args.refresh:
        # Pre-delete matching local files so HF's cache doesn't short-
        # circuit the download. (snapshot_download otherwise dedups
        # against the cache transparently.)
        for repo_path in files_to_download:
            local = TARGET_DIR / repo_path
            if local.exists():
                local.unlink()

    fetched = _download(files_to_download, TARGET_DIR, token)

    notice_path = _write_notice(TARGET_DIR, revision, observed, fetched)
    record_path = _write_revision_record(TARGET_DIR, revision)

    sys.stdout.write(
        f"Fetched {len(fetched)} file(s) into {TARGET_DIR}\n"
        f"  HF revision: {revision}\n"
        f"  License (observed): {observed or 'unknown'}\n"
        f"  Wrote {notice_path.relative_to(REPO_ROOT)}\n"
        f"  Wrote {record_path.relative_to(REPO_ROOT)}\n"
        f"\n"
        f"Next: convert to a SETEC manifest with\n"
        f"  scripts/calibration/editlens_to_manifest.py --inspect "
        f"--source <one of the fetched files>\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
