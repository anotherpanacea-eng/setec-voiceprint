#!/usr/bin/env python3
"""fetch_raid.py — fetch the RAID benchmark from HuggingFace.

Step 2 of the calibration toolchain (sibling to
`fetch_pangram_editlens.py`). Downloads RAID (Dugan et al., NAACL
2024) into `ai-prose-baselines-private/raid/` and writes a
NOTICE.md with attribution + license declaration + redistribution
posture.

RAID's current HuggingFace repository wrapper declares MIT. SETEC
treats corpus content and converted per-row text as local-only: a
repository-level license declaration does not establish that every
source row has identical redistribution terms.

The full RAID corpus is ~16.7 GB across three subsets:

  - **RAID-train** (`train.csv`, ~11.8 GB including attack rows):
    labels, 8 English domains (News, Books, Abstracts, Reviews,
    Reddit, Recipes, Wikipedia, Poetry).
  - **RAID-test** (`test.csv`, ~1.22 GB including attack rows): no labels,
    same 8 domains.
  - **RAID-extra** (`extra.csv`, ~3.71 GB including attack rows): labels,
    Code / Czech / German.

This script downloads all hosted CSV/parquet files for all subsets by
default, including adversarial transforms, because the user
greenlit full coverage. Pass `--subset train|test|extra` to
restrict. The current hosted CSVs co-locate adversarial and base
rows, so `--no-adversarial` fails before download and directs the
operator to the converter's row-level filter.

Usage:

    # Full default fetch (~17 GB total, all subsets + adversarial):
    python3 scripts/calibration/fetch_raid.py

    # Labeled English train (hosted as one monolithic CSV):
    python3 scripts/calibration/fetch_raid.py --subset train

    # Then omit adversarial rows during conversion:
    python3 scripts/calibration/raid_to_manifest.py --no-adversarial

    # Re-download even if files exist locally:
    python3 scripts/calibration/fetch_raid.py --refresh

Prerequisites:

  1. `pip install -r requirements-calibration.txt`
     (huggingface_hub + pyarrow).
  2. RAID is public; no HF token required. The script supports
     --token anyway for users behind authenticated proxies.

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

# After 1.16.0, scripts ship inside the plugin directory, so the
# file lives at
# ``<repo>/plugins/setec-voiceprint/scripts/calibration/foo.py``.
# parents[4] is the repo root in dev. When run from a marketplace
# install (no .git), the same parents[4] still resolves to the
# marketplace root, and the script still finds its sibling-of-
# repo private directory the same way it did before.
REPO_ROOT = Path(__file__).resolve().parents[4]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "raid"

HF_REPO_ID = "liamdugan/raid"
# Historical upstream declarations have disagreed. Accept the two known
# wrapper-license families exactly, but record what the pinned HuggingFace
# revision actually declares and keep corpus content local-only.
ACCEPTED_WRAPPER_LICENSES = {
    "apache-2.0", "apache 2.0", "apache2.0", "mit",
}

# Known RAID subsets. Each maps to a substring or list of
# substrings the script will match against filenames in the HF
# repo. Substring matching mirrors `fetch_pangram_editlens.py`'s
# `_select_files` approach.
KNOWN_SUBSETS = {
    "train": ("train",),
    "test": ("test",),
    "extra": ("extra",),
    "all": ("train", "test", "extra"),
}

# Adversarial-attack tokens RAID names. Used to identify which
# parquet files carry adversarial transforms vs. base generations.
# The base/no-attack files are the only ones used at calibration
# time; the adversarial files participate in R7's robustness card
# evaluation.
ADVERSARIAL_TOKENS = (
    "alternative_spelling",
    "article_deletion",
    "homoglyph",
    "insert_paragraphs",
    "misspelling",
    "number",
    "paraphrase",
    "perplexity_misspelling",
    "synonym",
    "upper_lower",
    "whitespace",
    "zero_width_space",
)

MONOLITHIC_RAID_CSVS = {"train.csv", "test.csv", "extra.csv"}


def _load_token(args: argparse.Namespace) -> str | None:
    """Return HF token from --token (file path or env-var name)
    or fall back to HF_TOKEN env var. RAID is public; this is
    only used for authenticated-proxy edge cases."""
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
            "(see internal/SPEC_calibration_toolchain.md for "
            "context)\n"
        )
        return False


def _verify_license(
    token: str | None, revision: str,
) -> tuple[bool, str]:
    """Verify the wrapper license declared by the pinned dataset card."""
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
    """Get the current main-branch revision SHA so PROVENANCE.md
    can pin a specific corpus version."""
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


def _is_adversarial_file(repo_path: str) -> bool:
    """Return True if the filename contains a recognized
    adversarial-attack token. Used to filter when
    `--no-adversarial` is passed."""
    base = Path(repo_path).name.lower()
    return any(tok in base for tok in ADVERSARIAL_TOKENS)


def _select_files(
    repo_files: list[str],
    subset: str,
    include_adversarial: bool,
) -> list[str]:
    """Return repo-relative paths to download based on
    `--subset` and `--no-adversarial`. Substring matching by
    subset name, plus an optional adversarial-token filter.

    The selection rules mirror RAID's file layout:
    `data/train-...parquet`, `data/test-...parquet`,
    `data/extra-...parquet` and the adversarial variants
    `data/train_paraphrase-...parquet`, etc.
    """
    subset_tokens = KNOWN_SUBSETS.get(subset)
    if subset_tokens is None:
        raise ValueError(
            f"Unknown subset {subset!r}. Known: "
            f"{', '.join(KNOWN_SUBSETS)}."
        )
    candidates: list[str] = []
    for f in repo_files:
        if not f.endswith((".parquet", ".csv")):
            continue
        base = Path(f).name.lower()
        if not any(tok in base for tok in subset_tokens):
            continue
        if not include_adversarial and _is_adversarial_file(f):
            continue
        candidates.append(f)
    return sorted(candidates)


def _contains_monolithic_raid_csv(repo_files: list[str]) -> bool:
    """Return whether selection contains a known root CSV with mixed attacks."""
    return any(path in MONOLITHIC_RAID_CSVS for path in repo_files)


def _download(
    repo_files: list[str], target_dir: Path, token: str | None,
    revision: str,
) -> list[Path]:
    """Download specified files from the HF repo into
    target_dir, preserving the in-repo path structure."""
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


def _write_notice(
    target_dir: Path, revision: str, observed_license: str | None,
    license_check: str, fetched_files: list[Path],
) -> Path:
    notice_path = target_dir / "NOTICE.md"
    iso_date = _dt.date.today().isoformat()
    rel_files = sorted(
        str(p.relative_to(target_dir)) for p in fetched_files
    )
    body = f"""# RAID corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**Paper:** Dugan, Hwang, Trhlík, et al., "RAID: A Shared Benchmark for Robust Evaluation of Machine-Generated Text Detectors," NAACL 2024. arXiv:2405.07940.
**Repository wrapper-license metadata:** {f"`{observed_license}` at the pinned revision" if observed_license else "check skipped; no license conclusion recorded"}
**License check:** `{license_check}`
**SETEC content posture:** local-only

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_raid.py` for the purpose of locally
calibrating SETEC's empirical per-signal thresholds and
populating the validation harness's adversarial-class slices.

## Content posture

The repository-level wrapper declaration does not establish that every
underlying source row has identical redistribution terms. SETEC keeps the
downloaded corpus, converted rows, and manifests local-only. Aggregate
calibration results remain subject to the framework's existing provenance and
policy gates; this notice does not authorize redistribution or promotion.

Per-row text files generated by
`scripts/calibration/raid_to_manifest.py` also live in this
directory under the same local-only posture.

## Files fetched

{chr(10).join(f"- `{f}`" for f in rel_files[:200])}
{"... (" + str(len(rel_files) - 200) + " more files)" if len(rel_files) > 200 else ""}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def _write_revision_record(
    target_dir: Path, revision: str, args: argparse.Namespace,
    observed_license: str | None, license_check: str,
) -> Path:
    """Record the HF revision SHA + fetch params in a stable
    JSON file so calibrate_thresholds.py can read it for
    provenance."""
    record_path = target_dir / ".fetch_record.json"
    record = {
        "record_schema_version": 2,
        "repo_id": HF_REPO_ID,
        "revision": revision,
        "fetch_date": _dt.date.today().isoformat(),
        "subset": args.subset,
        "include_adversarial": not args.no_adversarial,
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
            "Download the RAID benchmark from HuggingFace into "
            "ai-prose-baselines-private/raid/. "
            "SETEC records the pinned repository wrapper-license "
            "declaration and keeps corpus content local-only."
        )
    )
    parser.add_argument(
        "--subset", default="all",
        choices=list(KNOWN_SUBSETS),
        help=(
            "Which subset to fetch. Default: all (train + test "
            "+ extra). 'extra' adds Code/Czech/German."
        ),
    )
    parser.add_argument(
        "--no-adversarial", action="store_true",
        help=(
            "Omit independently named adversarial files when the hosted "
            "layout permits file-level filtering. The current monolithic "
            "RAID CSVs co-locate attack rows, so this request fails before "
            "download; filter rows with raid_to_manifest.py instead."
        ),
    )
    parser.add_argument(
        "--token", default=None,
        help=(
            "HF access token: a literal token, a file path, or "
            "an env-var name. RAID is public; use this only "
            "behind authenticated proxies. Falls back to "
            "HF_TOKEN env var."
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
        help=(
            "List the files that would be downloaded and exit. "
            "Useful for verifying --subset / --no-adversarial "
            "filters before committing to a ~17 GB pull."
        ),
    )
    args = parser.parse_args(argv)

    if not _check_huggingface_hub():
        return 1

    token = _load_token(args)
    # RAID is public; missing token is fine. Don't error like
    # the EditLens fetcher does.

    try:
        revision = _resolve_revision(token)
    except Exception as exc:
        sys.stderr.write(
            f"Could not resolve HF revision SHA: {exc}\n"
            "Provenance would be incomplete. Aborting.\n"
        )
        return 3
    if not revision:
        sys.stderr.write(
            "Could not resolve HF revision SHA; provenance "
            "would be incomplete. Aborting.\n"
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
                f"Refusing to proceed; the calibration "
                f"toolchain's legal posture depends on the "
                f"license. Re-run with --skip-license-check "
                f"only after manual verification.\n"
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

    files_to_download = _select_files(
        repo_files, args.subset,
        include_adversarial=not args.no_adversarial,
    )
    if not files_to_download:
        sys.stderr.write(
            f"No matching files for subset {args.subset!r} "
            f"(adversarial={not args.no_adversarial}). "
            f"Available files:\n  "
            + "\n  ".join(repo_files[:30])
            + ("\n  ..." if len(repo_files) > 30 else "")
            + "\n"
        )
        return 4

    if (
        args.no_adversarial
        and _contains_monolithic_raid_csv(files_to_download)
    ):
        sys.stderr.write(
            "Cannot satisfy --no-adversarial: RAID's hosted train.csv, "
            "test.csv, and extra.csv co-locate attack and non-attack rows. "
            "No files were downloaded. Fetch the desired subset without "
            "--no-adversarial, then run raid_to_manifest.py "
            "--no-adversarial for row-level filtering.\n"
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
        # Pre-delete matching local files so HF's cache doesn't
        # short-circuit the download.
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
        f"  Subset: {args.subset}\n"
        f"  Adversarial: "
        f"{'included' if not args.no_adversarial else 'skipped'}\n"
        f"  Wrote {notice_path.relative_to(REPO_ROOT)}\n"
        f"  Wrote {record_path.relative_to(REPO_ROOT)}\n"
        f"\n"
        f"Next: convert to a SETEC manifest with\n"
        f"  scripts/calibration/raid_to_manifest.py "
        f"--source-dir {TARGET_DIR.relative_to(REPO_ROOT)}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
