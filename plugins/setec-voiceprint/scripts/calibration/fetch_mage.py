#!/usr/bin/env python3
"""fetch_mage.py — fetch the MAGE benchmark from HuggingFace.

Companion to `fetch_raid.py`. Downloads MAGE (Li et al., ACL
2024) into `ai-prose-baselines-private/mage/` and writes a
NOTICE.md with attribution + license declaration.

MAGE's current HuggingFace repository wrapper declares Apache-2.0,
while upstream metadata is not fully consistent and MAGE aggregates
source datasets with their own terms. SETEC therefore treats corpus
content and converted per-row text as local-only.

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
install command and exits cleanly. If the pinned HF repository
wrapper-license declaration is not one of the explicitly accepted
values, this script refuses to proceed.
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
# Historical upstream declarations have disagreed. Accept the two known
# wrapper-license families exactly, but record what the pinned HuggingFace
# revision actually declares and keep corpus content local-only.
ACCEPTED_WRAPPER_LICENSES = {
    "mit", "apache-2.0", "apache 2.0", "apache2.0",
}

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


def _verify_license(
    token: str | None, revision: str,
) -> tuple[bool, str]:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID, revision=revision)
    license_str = ""
    if info.card_data:
        raw_license = info.card_data.get("license") or ""
        if isinstance(raw_license, str):
            license_str = " ".join(raw_license.strip().lower().split())
    if not license_str and getattr(info, "tags", None):
        for tag in info.tags:
            if tag.startswith("license:"):
                license_str = " ".join(
                    tag.split(":", 1)[1].strip().lower().split()
                )
                break
    if license_str in ACCEPTED_WRAPPER_LICENSES:
        return True, license_str
    return False, license_str


def _resolve_revision(token: str | None) -> str:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID)
    return getattr(info, "sha", "") or ""


def _list_repo_files(token: str | None, revision: str) -> list[str]:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    return list(api.list_repo_files(
        HF_REPO_ID, repo_type="dataset", revision=revision,
    ))


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
    revision: str,
) -> list[Path]:
    from huggingface_hub import hf_hub_download  # type: ignore

    fetched: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    for repo_path in repo_files:
        local_path = hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=repo_path,
            repo_type="dataset",
            revision=revision,
            local_dir=str(target_dir),
            token=token,
        )
        fetched.append(Path(local_path))
    return fetched


def _invalidate_provenance(target_dir: Path) -> None:
    """Remove artifacts that describe the pre-download corpus state.

    Each unlink is atomic. Any filesystem error aborts before a corpus file is
    deleted, overwritten, or added, so a failed refresh cannot retain an old
    receipt or notice for a partially updated corpus.
    """
    for name in (".fetch_record.json", "NOTICE.md"):
        try:
            (target_dir / name).unlink()
        except FileNotFoundError:
            pass


def _write_notice(
    target_dir: Path, revision: str, observed_license: str | None,
    license_check: str, fetched_files: list[Path],
) -> Path:
    notice_path = target_dir / "NOTICE.md"
    iso_date = _dt.date.today().isoformat()
    rel_files = sorted(
        str(p.relative_to(target_dir)) for p in fetched_files
    )
    body = f"""# MAGE corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**Paper:** Li, Li, Cui, Bi, Wang, Yang, Shi, Zhang, "MAGE: Machine-generated Text Detection in the Wild," ACL 2024. arXiv:2305.13242.
**Repository wrapper-license metadata:** {f"`{observed_license}` at the pinned revision" if observed_license else "check skipped; no license conclusion recorded"}
**License check:** `{license_check}`
**SETEC content posture:** local-only

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_mage.py` for the purpose of locally
calibrating SETEC's empirical per-signal thresholds. MAGE's
shape complements RAID: 437 K binary-labeled text examples
across 10 source datasets, used as a cross-check on RAID-derived
threshold values.

## Content posture

MAGE aggregates source datasets with their own terms, and upstream wrapper
metadata has not always been consistent. A repository-level wrapper
declaration does not override per-source restrictions. SETEC keeps the
downloaded corpus, converted rows, and manifests local-only. Aggregate
calibration results remain subject to the framework's existing provenance and
policy gates; this notice does not authorize redistribution or promotion.

Per-row text files generated by
`scripts/calibration/mage_to_manifest.py` also live in this
directory under the same local-only posture.

## Files fetched

{chr(10).join(f"- `{f}`" for f in rel_files)}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def _write_revision_record(
    target_dir: Path, revision: str, args: argparse.Namespace,
    observed_license: str | None, license_check: str,
) -> Path:
    record_path = target_dir / ".fetch_record.json"
    record = {
        "record_schema_version": 2,
        "repo_id": HF_REPO_ID,
        "revision": revision,
        "fetch_date": _dt.date.today().isoformat(),
        "split": args.split,
        "observed_wrapper_license": observed_license,
        "license_check": license_check,
        "content_posture": "local_only",
    }
    record_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8",
    )
    return record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download MAGE from HuggingFace into "
            "ai-prose-baselines-private/mage/. SETEC records the "
            "pinned repository wrapper-license declaration and "
            "keeps corpus content local-only."
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
            "Bypass wrapper-license verification. The receipt records the "
            "check as skipped and makes no license conclusion."
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

    if not args.skip_license_check:
        try:
            ok, observed = _verify_license(token, revision)
        except Exception as exc:
            sys.stderr.write(
                f"Failed to verify wrapper license at pinned revision: "
                f"{exc}\n"
                "Pass --skip-license-check to bypass.\n"
            )
            return 2
        if not ok:
            sys.stderr.write(
                "Wrapper-license mismatch. Expected exactly MIT or "
                f"Apache-2.0; observed {observed!r} on the pinned HF "
                "dataset card. "
                f"Refusing to proceed.\n"
            )
            return 2
        license_check = "verified"
    else:
        observed = None
        license_check = "skipped"

    try:
        repo_files = _list_repo_files(token, revision)
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

    # Replacement artifacts are written only after all downloads succeed.
    _invalidate_provenance(TARGET_DIR)

    if args.refresh:
        for repo_path in files_to_download:
            local = TARGET_DIR / repo_path
            if local.exists():
                local.unlink()

    fetched = _download(
        files_to_download, TARGET_DIR, token, revision,
    )

    notice_path = _write_notice(
        TARGET_DIR, revision, observed, license_check, fetched,
    )
    record_path = _write_revision_record(
        TARGET_DIR, revision, args, observed, license_check,
    )

    sys.stdout.write(
        f"Fetched {len(fetched)} file(s) into {TARGET_DIR}\n"
        f"  HF revision: {revision}\n"
        f"  Wrapper license (observed): "
        f"{observed if observed is not None else 'check skipped'}\n"
        f"  Content posture: local_only\n"
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
