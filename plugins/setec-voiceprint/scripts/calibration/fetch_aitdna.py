#!/usr/bin/env python3
"""fetch_aitdna.py — fetch the AITDNA benchmark from HuggingFace.

Companion to `fetch_mage.py` / `fetch_raid.py`. Downloads AITDNA
(*'Your AI Text is not Mine'*; Dycke, Sakharova, Daheim, Gurevych —
arXiv:2606.04906) into `ai-prose-baselines-private/aitdna/` and writes a
NOTICE.md with attribution + license declaration.

AITDNA is **CC-BY-SA-4.0** (Creative Commons Attribution-ShareAlike 4.0).
Share-alike means any redistributed *adaptation* of the text must carry the
same license — but this harness is **fetch-only + report-only**: it reads
the data to REPORT discrimination scores and re-publishes no text, so the
share-alike obligation is not triggered. Calibration thresholds are NOT
derived here (the AITDNA harness is external validation, never a fitter).

AITDNA's HF layout is one config per detection notion (`document`,
`boundary`, `sentence`, `intent`, `content`, `membership`, `span`,
`token`, `original`), each with a `test` split of ~362 rows. The
token-level configs (`token`, `membership`) carry the per-token genesis
provenance the adapter's document-level τ label needs.

Usage:

    # Fetch the token config (per-token genesis provenance):
    python3 scripts/calibration/fetch_aitdna.py --config token

    # Re-download even if files exist locally:
    python3 scripts/calibration/fetch_aitdna.py --config token --refresh

Prerequisites:

  1. `pip install -r requirements-calibration.txt`
     (huggingface_hub + pyarrow).
  2. AITDNA is public; no HF token required.

If huggingface_hub isn't installed, this script prints the install command
and exits cleanly. If the HF dataset's declared license differs from
CC-BY-SA-4.0 at fetch time, this script refuses to proceed.
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
TARGET_DIR = PRIVATE_DIR / "aitdna"

HF_REPO_ID = "UKPLab/AITDNA"
# AITDNA's HF dataset card declares CC-BY-SA-4.0 (verified on the Hub
# 2026-07-05). Share-alike; a fetch-only report-only harness re-publishes
# no text so SA is not triggered. Accept ONLY the CC-BY-SA family — the rest
# of this harness (gate error, NOTICE, provenance, report) all assert
# CC-BY-SA-4.0, so an unrelated license (e.g. creativeml-openrail) must NOT pass.
EXPECTED_LICENSE_PATTERNS = (
    "cc-by-sa-4.0", "cc-by-sa",
)

# The AITDNA notion configs (one per detection notion). token/membership
# carry per-token genesis provenance; original carries the full texts.
KNOWN_CONFIGS = (
    "token", "membership", "document", "boundary", "sentence",
    "intent", "content", "span", "original", "all",
)


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


def _select_files(repo_files: list[str], config: str) -> list[str]:
    """Return the parquet files for the requested notion config. AITDNA's
    HF layout buckets each config's parquet under a path that contains the
    config name (the auto-converted parquet export)."""
    if config not in KNOWN_CONFIGS:
        raise ValueError(
            f"Unknown config {config!r}. Known: {', '.join(KNOWN_CONFIGS)}."
        )
    candidates: list[str] = []
    for f in repo_files:
        if not f.endswith(".parquet"):
            continue
        if config == "all":
            candidates.append(f)
            continue
        # Match the config token in the path (e.g. `token/test/0000.parquet`
        # or `.../token-test.parquet`). Use path-component + basename match.
        low = f.lower()
        parts = set(Path(low).parts) | {Path(low).stem}
        if config in low.split("/") or any(config in p for p in parts):
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
    rel_files = sorted(str(p.relative_to(target_dir)) for p in fetched_files)
    body = f"""# AITDNA corpus — license + provenance

**Source:** https://huggingface.co/datasets/{HF_REPO_ID} (revision `{revision}`)
**Paper:** Dycke, Sakharova, Daheim, Gurevych, "'Your AI Text is not Mine': Redefining and Evaluating AI-generated Text Detection under Realistic Assumptions." arXiv:2606.04906.
**License:** CC-BY-SA-4.0 (HF dataset card observed at fetch time: `{observed_license or "unknown"}`)
  https://creativecommons.org/licenses/by-sa/4.0/

This directory contains a local copy fetched on {iso_date} by
`scripts/calibration/fetch_aitdna.py` for **held-out external validation**
of SETEC's existing detectors on realistic human-AI co-written text.

## Redistribution posture (share-alike)

CC-BY-SA-4.0 is copyleft: any redistributed *adaptation* of this text must
carry the same license. The AITDNA benchmark harness is **fetch-only +
report-only** — it reads these files to REPORT discrimination scores and
re-publishes none of the text, so the share-alike obligation is not
triggered. Attribution to UKPLab / AITDNA (arXiv:2606.04906) is retained.
No calibration threshold is derived from AITDNA (the harness is external
validation, never a fitter).

Per-row text files generated by
`scripts/calibration/aitdna_to_manifest.py` also live under the operator's
`--text-dir` with their own NOTICE.md; the output manifest carries
`privacy: shareable` and `source: aitdna`. It is NOT vendored into the repo.

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
        "config": args.config,
    }
    record_path.write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8",
    )
    return record_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download AITDNA (UKPLab/AITDNA, CC-BY-SA-4.0, arXiv:2606.04906) "
            "from HuggingFace into ai-prose-baselines-private/aitdna/ for "
            "held-out external validation. Fetch-only + report-only; no "
            "calibration threshold is derived (share-alike not triggered)."
        )
    )
    parser.add_argument(
        "--config", default="token", choices=list(KNOWN_CONFIGS),
        help=(
            "Which AITDNA notion config to fetch. Default: token (per-token "
            "genesis provenance for the document-level τ label)."
        ),
    )
    parser.add_argument(
        "--token", default=None,
        help="HF access token (literal / file path / env-var name). Public.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Re-download even if files exist locally.",
    )
    parser.add_argument(
        "--skip-license-check", action="store_true",
        help="Bypass the CC-BY-SA-4.0 verification (verified elsewhere).",
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
                f"License mismatch. Expected CC-BY-SA-4.0; observed "
                f"{observed!r} on the HF dataset card. Refusing to proceed.\n"
            )
            return 2
    else:
        observed = "skipped"

    try:
        revision = _resolve_revision(token)
    except Exception as exc:
        sys.stderr.write(f"Could not resolve HF revision SHA: {exc}\nAborting.\n")
        return 3
    if not revision:
        sys.stderr.write("Could not resolve HF revision SHA; aborting.\n")
        return 3

    try:
        repo_files = _list_repo_files(token)
    except Exception as exc:
        sys.stderr.write(f"Failed to list repo files via HF API: {exc}\n")
        return 3

    files_to_download = _select_files(repo_files, args.config)
    if not files_to_download:
        sys.stderr.write(
            f"No matching files for config {args.config!r}. Available:\n  "
            + "\n  ".join(repo_files[:30])
            + ("\n  ..." if len(repo_files) > 30 else "")
            + "\n"
        )
        return 4

    if args.dry_run:
        sys.stdout.write(
            f"DRY-RUN: would fetch {len(files_to_download)} file(s) from "
            f"{HF_REPO_ID} (revision {revision}) into {TARGET_DIR}:\n"
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
    notice_path = _write_notice(TARGET_DIR, revision, observed, fetched)
    record_path = _write_revision_record(TARGET_DIR, revision, args)

    sys.stdout.write(
        f"Fetched {len(fetched)} file(s) into {TARGET_DIR}\n"
        f"  HF revision: {revision}\n"
        f"  License (observed): {observed or 'unknown'}\n"
        f"  Config: {args.config}\n"
        f"  Wrote {notice_path.relative_to(REPO_ROOT)}\n"
        f"  Wrote {record_path.relative_to(REPO_ROOT)}\n"
        f"\n"
        f"Next: convert to a SETEC manifest with\n"
        f"  scripts/calibration/aitdna_to_manifest.py "
        f"--aitdna-dir {TARGET_DIR.relative_to(REPO_ROOT)} --config {args.config}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
