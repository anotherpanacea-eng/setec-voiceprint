#!/usr/bin/env python3
"""fetch_raid.py — fetch the RAID benchmark from HuggingFace.

Step 2 of the calibration toolchain (sibling to
`fetch_pangram_editlens.py`). Downloads RAID (Dugan et al., NAACL
2024) into `ai-prose-baselines-private/raid/` and writes a
NOTICE.md with attribution + license declaration + redistribution
posture.

RAID is **Apache-2.0** — unlike EditLens, RAID is freely
redistributable. The calibration thresholds SETEC ships under
GPL-3 can cite RAID directly without the CC-NC awkwardness that
governs the EditLens pipeline. The fetcher's legal posture is
therefore simpler: download, attribute, run. No
"DO NOT REDISTRIBUTE" guard on derived measurements.

The full RAID corpus is ~16.7 GB across three subsets:

  - **RAID-train** (~802 MB without adversarial, ~11.8 GB with):
    labels, 8 English domains (News, Books, Abstracts, Reviews,
    Reddit, Recipes, Wikipedia, Poetry).
  - **RAID-test** (~81 MB without, ~1.22 GB with): no labels,
    same 8 domains.
  - **RAID-extra** (~275 MB without, ~3.71 GB with): labels,
    Code / Czech / German.

This script downloads ALL parquet files for ALL subsets by
default, including adversarial transforms, because the user
greenlit full coverage. Pass `--subset train|test|extra` to
restrict; pass `--no-adversarial` to skip the 11 adversarial-
transform variants.

Usage:

    # Full default fetch (~17 GB total, all subsets + adversarial):
    python3 scripts/calibration/fetch_raid.py

    # Labeled English train only, no adversarial (~802 MB):
    python3 scripts/calibration/fetch_raid.py \\
        --subset train --no-adversarial

    # Re-download even if files exist locally:
    python3 scripts/calibration/fetch_raid.py --refresh

Prerequisites:

  1. `pip install -r requirements-calibration.txt`
     (huggingface_hub + pyarrow).
  2. RAID is public; no HF token required. The script supports
     --token anyway for users behind authenticated proxies.

If huggingface_hub isn't installed, this script prints the
install command and exits cleanly. If the HF dataset's declared
license differs from Apache-2.0 at fetch time, this script
refuses to proceed (the corpus may have been re-licensed; the
legal posture needs review).
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
EXPECTED_LICENSE_PATTERNS = ("apache-2.0", "apache 2.0", "apache2.0")

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


def _verify_license(token: str | None) -> tuple[bool, str]:
    """Read the HF dataset card and verify the license string
    matches Apache-2.0. Returns (ok, observed_license)."""
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
    """Get the current main-branch revision SHA so PROVENANCE.md
    can pin a specific corpus version."""
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    info = api.dataset_info(HF_REPO_ID)
    return getattr(info, "sha", "") or ""


def _list_repo_files(token: str | None) -> list[str]:
    from huggingface_hub import HfApi  # type: ignore

    api = HfApi(token=token)
    return list(api.list_repo_files(HF_REPO_ID, repo_type="dataset"))


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


def _download(
    repo_files: list[str], target_dir: Path, token: str | None,
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
    body = f"""# RAID corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**Paper:** Dugan, Hwang, Trhlík, et al., "RAID: A Shared Benchmark for Robust Evaluation of Machine-Generated Text Detectors," NAACL 2024. arXiv:2405.07940.
**License:** Apache-2.0 (observed at fetch time: `{observed_license or "unknown"}`)
  https://www.apache.org/licenses/LICENSE-2.0

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_raid.py` for the purpose of locally
calibrating SETEC's empirical per-signal thresholds and
populating the validation harness's adversarial-class slices.

## Redistribution posture

Apache-2.0 is permissive. Calibration thresholds derived from
RAID can be encoded into SETEC's GPL-3 codebase and shipped as
public defaults; SETEC's NOTICE retains the RAID attribution
trailer when those thresholds land. This is the simpler legal
posture than EditLens, which is CC BY-NC-SA 4.0 and stays
local-only.

Per-row text files generated by
`scripts/calibration/raid_to_manifest.py` also live in this
directory. They inherit the Apache-2.0 license; the script's
output manifest carries `privacy: public` and `source: raid`.

## Files fetched

{chr(10).join(f"- `{f}`" for f in rel_files[:200])}
{"... (" + str(len(rel_files) - 200) + " more files)" if len(rel_files) > 200 else ""}
"""
    notice_path.write_text(body, encoding="utf-8")
    return notice_path


def _write_revision_record(
    target_dir: Path, revision: str, args: argparse.Namespace,
) -> Path:
    """Record the HF revision SHA + fetch params in a stable
    JSON file so calibrate_thresholds.py can read it for
    provenance."""
    record_path = target_dir / ".fetch_record.json"
    record = {
        "repo_id": HF_REPO_ID,
        "revision": revision,
        "fetch_date": _dt.date.today().isoformat(),
        "subset": args.subset,
        "include_adversarial": not args.no_adversarial,
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
            "RAID is Apache-2.0; public redistribution is "
            "permitted; calibration thresholds derived from "
            "RAID can ship in GPL-3 SETEC defaults with "
            "attribution."
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
            "Skip the 11 adversarial-transform variants "
            "(homoglyph, paraphrase, etc.). Cuts the fetch "
            "from ~17 GB to ~1.4 GB on the full subset. "
            "Adversarial files are required for R7's "
            "robustness-card evaluation but not for baseline "
            "threshold calibration."
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
            "Bypass the Apache-2.0 verification. Use only if "
            "you have verified the license through another "
            "channel."
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
                f"License mismatch. Expected Apache-2.0; "
                f"observed {observed!r} on the HF dataset card. "
                f"Refusing to proceed; the calibration "
                f"toolchain's legal posture depends on the "
                f"license. Re-run with --skip-license-check "
                f"only after manual verification.\n"
            )
            return 2
    else:
        observed = "skipped"

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

    try:
        repo_files = _list_repo_files(token)
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
