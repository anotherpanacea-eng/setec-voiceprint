#!/usr/bin/env python3
"""fetch_mage.py — fetch the MAGE benchmark from HuggingFace.

Companion to `fetch_raid.py`. Downloads MAGE (Li et al., ACL
2024) into `ai-prose-baselines-private/mage/` and writes a
NOTICE.md with attribution + license declaration.

MAGE is **MIT-licensed** — like RAID, freely redistributable.
Calibration thresholds derived from MAGE can be encoded into
SETEC's GPL-3 codebase and shipped as public defaults; SETEC's
NOTICE retains the MAGE attribution trailer when those
thresholds land.

The full MAGE corpus is ~554 MB across three splits:

  - **train** (~319 K rows)
  - **validation** (~57 K rows)
  - **test** (~61 K rows)

Total ~437 K rows of binary human-or-machine labeled text.
Companion to RAID; the recommended calibration frame uses both
(plus EditLens for ESL-specific slices).

Usage:

    # Full default fetch (~554 MB, all splits):
    python3 scripts/calibration/fetch_mage.py

    # Just train split:
    python3 scripts/calibration/fetch_mage.py --split train

    # Re-download even if files exist locally:
    python3 scripts/calibration/fetch_mage.py --refresh

Prerequisites:

  1. `pip install -r requirements-calibration.txt`
     (huggingface_hub + pyarrow).
  2. MAGE is public; no HF token required.

If huggingface_hub isn't installed, this script prints the
install command and exits cleanly. If the HF dataset's declared
license differs from MIT at fetch time, this script refuses to
proceed.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "mage"

HF_REPO_ID = "yaful/MAGE"
# MAGE's accompanying paper cites MIT, but the HuggingFace
# dataset card declares Apache-2.0 (verified 2026-05-10 against
# revision 342663f...). Both are permissive and functionally
# equivalent for the framework's GPL-3-with-attribution posture,
# so accept either. The NOTICE.md records the OBSERVED license
# string from the HF card so consumers can audit what the
# framework actually saw at fetch time.
EXPECTED_LICENSE_PATTERNS = (
    "mit", "apache-2.0", "apache 2.0", "apache2.0",
)

KNOWN_SPLITS = ("train", "validation", "test", "all")


def _load_token(args: argparse.Namespace) -> str | None:
    if args.token:
        p = Path(args.token).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        env_val = os.environ.get(args.token)
        if env_val:
            return env_val.strip()
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
        )
        return False


def _verify_license(token: str | None) -> tuple[bool, str]:
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
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID)
    return getattr(info, "sha", "") or ""


def _list_repo_files(token: str | None) -> list[str]:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    return list(api.list_repo_files(HF_REPO_ID, repo_type="dataset"))


def _select_files(
    repo_files: list[str], split: str,
) -> list[str]:
    """Return files for the requested split. MAGE's HF layout
    is `data/<split>-...parquet` (auto-converted from CSV by
    HF's parquet conversion bot)."""
    if split not in KNOWN_SPLITS:
        raise ValueError(
            f"Unknown split {split!r}. Known: "
            f"{', '.join(KNOWN_SPLITS)}."
        )
    candidates: list[str] = []
    for f in repo_files:
        if not f.endswith((".parquet", ".csv")):
            continue
        base = Path(f).name.lower()
        if split == "all":
            candidates.append(f)
            continue
        # Match split name in basename. Treat 'val' and
        # 'validation' as synonyms because MAGE's source CSVs
        # use either form.
        split_tokens = (
            {"val", "validation"} if split == "validation"
            else {split}
        )
        if any(tok in base for tok in split_tokens):
            candidates.append(f)
    return sorted(candidates)


def _download(
    repo_files: list[str], target_dir: Path, token: str | None,
) -> list[Path]:
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
    body = f"""# MAGE corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**Paper:** Li, Li, Cui, Bi, Wang, Yang, Shi, Zhang, "MAGE: Machine-generated Text Detection in the Wild," ACL 2024. arXiv:2305.13242.
**License:** Permissive (paper cites MIT; HF dataset card observed at fetch time: `{observed_license or "unknown"}`)
  https://opensource.org/licenses/MIT  ·  https://www.apache.org/licenses/LICENSE-2.0

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_mage.py` for the purpose of locally
calibrating SETEC's empirical per-signal thresholds. MAGE's
shape complements RAID: 437 K binary-labeled text examples
across 10 source datasets, used as a cross-check on RAID-derived
threshold values.

## Redistribution posture

MIT is permissive. Calibration thresholds derived from MAGE can
be encoded into SETEC's GPL-3 codebase and shipped as public
defaults; SETEC's NOTICE retains the MAGE attribution trailer
when those thresholds land.

Per-row text files generated by
`scripts/calibration/mage_to_manifest.py` also live in this
directory. They inherit the MIT license; the script's output
manifest carries `privacy: public` and `source: mage`.

## Files fetched

{chr(10).join(f"- `{f}`" for f in rel_files)}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def _write_revision_record(
    target_dir: Path, revision: str, args: argparse.Namespace,
) -> Path:
    record_path = target_dir / ".fetch_record.json"
    record = {
        "repo_id": HF_REPO_ID,
        "revision": revision,
        "fetch_date": _dt.date.today().isoformat(),
        "split": args.split,
    }
    record_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8",
    )
    return record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download MAGE from HuggingFace into "
            "ai-prose-baselines-private/mage/. MIT-licensed; "
            "public redistribution permitted; calibration "
            "thresholds derived from MAGE can ship in GPL-3 "
            "SETEC defaults with attribution."
        )
    )
    parser.add_argument(
        "--split", default="all",
        choices=list(KNOWN_SPLITS),
        help=(
            "Which split to fetch. Default: all (train + "
            "validation + test)."
        ),
    )
    parser.add_argument(
        "--token", default=None,
        help=(
            "HF access token: a literal token, a file path, or "
            "an env-var name. MAGE is public; use this only "
            "behind authenticated proxies."
        ),
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Re-download even if files exist locally.",
    )
    parser.add_argument(
        "--skip-license-check", action="store_true",
        help=(
            "Bypass the MIT verification. Use only if you have "
            "verified the license through another channel."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List the files that would be downloaded and exit.",
    )
    args = parser.parse_args(argv)

    if not _check_huggingface_hub():
        return 1

    token = _load_token(args)

    if not args.skip_license_check:
        try:
            ok, observed = _verify_license(token)
        except Exception as exc:
            sys.stderr.write(
                f"Failed to verify license via HF API: {exc}\n"
                "Pass --skip-license-check to bypass.\n"
            )
            return 2
        if not ok:
            sys.stderr.write(
                f"License mismatch. Expected MIT; observed "
                f"{observed!r} on the HF dataset card. "
                f"Refusing to proceed.\n"
            )
            return 2
    else:
        observed = "skipped"

    try:
        revision = _resolve_revision(token)
    except Exception as exc:
        sys.stderr.write(
            f"Could not resolve HF revision SHA: {exc}\n"
            "Aborting.\n"
        )
        return 3
    if not revision:
        sys.stderr.write(
            "Could not resolve HF revision SHA; aborting.\n"
        )
        return 3

    try:
        repo_files = _list_repo_files(token)
    except Exception as exc:
        sys.stderr.write(
            f"Failed to list repo files via HF API: {exc}\n"
        )
        return 3

    files_to_download = _select_files(repo_files, args.split)
    if not files_to_download:
        sys.stderr.write(
            f"No matching files for split {args.split!r}. "
            f"Available files:\n  "
            + "\n  ".join(repo_files[:30])
            + ("\n  ..." if len(repo_files) > 30 else "")
            + "\n"
        )
        return 4

    if args.dry_run:
        sys.stdout.write(
            f"DRY-RUN: would fetch {len(files_to_download)} "
            f"file(s) from {HF_REPO_ID} (revision {revision}) "
            f"into {TARGET_DIR}:\n"
        )
        for f in files_to_download:
            sys.stdout.write(f"  {f}\n")
        return 0

    if args.refresh:
        for repo_path in files_to_download:
            local = TARGET_DIR / repo_path
            if local.exists():
                local.unlink()

    fetched = _download(files_to_download, TARGET_DIR, token)

    notice_path = _write_notice(
        TARGET_DIR, revision, observed, fetched,
    )
    record_path = _write_revision_record(
        TARGET_DIR, revision, args,
    )

    sys.stdout.write(
        f"Fetched {len(fetched)} file(s) into {TARGET_DIR}\n"
        f"  HF revision: {revision}\n"
        f"  License (observed): {observed or 'unknown'}\n"
        f"  Split: {args.split}\n"
        f"  Wrote {notice_path.relative_to(REPO_ROOT)}\n"
        f"  Wrote {record_path.relative_to(REPO_ROOT)}\n"
        f"\n"
        f"Next: convert to a SETEC manifest with\n"
        f"  scripts/calibration/mage_to_manifest.py "
        f"--source-dir {TARGET_DIR.relative_to(REPO_ROOT)}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
