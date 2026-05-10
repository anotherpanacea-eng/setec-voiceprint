#!/usr/bin/env python3
"""raid_to_manifest.py — convert RAID parquet files into a SETEC
manifest slice.

Step 3 of the calibration toolchain for the RAID corpus.
Companion to `fetch_raid.py`. Walks the local RAID parquet
files (under `ai-prose-baselines-private/raid/`), iterates rows,
spills per-row text to bucketed dirs, and emits a manifest
JSONL the harnesses (validation_harness.py,
voice_validation_harness.py) can consume.

RAID schema (per HF dataset card):

  - `id`              unique row id
  - `adv_source_id`   id of the base generation this row is an
                      adversarial variant of (null for base rows)
  - `source_id`       id of the human source the prompt was
                      derived from
  - `model`           "human" or one of 11 LLMs
                      (gpt-4, gpt-3.5, llama-chat, etc.)
  - `decoding`        sampling strategy (greedy, sampling, etc.)
  - `repetition_penalty` numeric
  - `attack`          adversarial transform name (or "none")
  - `domain`          one of 8 English domains for train/test
                      (News, Books, Abstracts, Reviews, Reddit,
                      Recipes, Wikipedia, Poetry) or 3 extra
                      domains (Code, Czech, German)
  - `title`           per-row title
  - `prompt`          prompt used to elicit the generation
  - `generation`      the text body — this is what SETEC's
                      stylometric tools see

Manifest mapping:

  - `id`              raid_<source_basename>_<row_id>
  - `path`            relative path under --text-dir to the
                      spilled text file
  - `ai_status`       "human" if model == "human"; else "ai"
                      (or "ai_edited" if attack != "none" and
                      the underlying model is human — RAID
                      doesn't actually expose this case but
                      the logic handles it)
  - `editing_status`  "adversarial:<attack>" if attack !=
                      "none", else "unedited"
  - `register`        domain (lowercased)
  - `language_status` "native" by default; "non_native_advanced"
                      for the extra subset's Czech/German
                      domains (these are MT outputs, not L2
                      English; flagged for caution)
  - `use`             "validation" by default
  - `privacy`         "public" (Apache-2.0)
  - `source`          "raid"
  - `source_id`       the row's RAID `source_id`
  - `notes`           {model, decoding, repetition_penalty,
                      attack, original_id}

Usage:

    # Convert everything in the local RAID dir to manifest:
    python3 scripts/calibration/raid_to_manifest.py

    # Limit for smoke-testing:
    python3 scripts/calibration/raid_to_manifest.py --limit 100

    # Only non-adversarial rows (skips adversarial variants
    # even if their parquet files are present locally):
    python3 scripts/calibration/raid_to_manifest.py \\
        --no-adversarial

    # Custom output paths:
    python3 scripts/calibration/raid_to_manifest.py \\
        --source-dir custom/raid_dir/ \\
        --manifest custom/raid_manifest.jsonl \\
        --text-dir custom/raid_text/

Defaults:
  --source-dir  ai-prose-baselines-private/raid/
  --manifest    ai-prose-baselines-private/raid/manifest.jsonl
  --text-dir    ai-prose-baselines-private/raid/text/

The text dir uses 4-level hash bucketing
(`text/ab/cd/<id>.txt`) so 8M files don't pile up in one
directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
DEFAULT_SOURCE_DIR = PRIVATE_DIR / "raid"
DEFAULT_MANIFEST = DEFAULT_SOURCE_DIR / "manifest.jsonl"
DEFAULT_TEXT_DIR = DEFAULT_SOURCE_DIR / "text"

# RAID's `domain` values for the `extra` subset (Code, Czech,
# German) get language_status overrides because the text is
# either non-prose (code) or non-English.
NONENGLISH_DOMAINS = {"czech", "german"}
NONPROSE_DOMAINS = {"code"}


def _read_rows(source: Path) -> Iterator[dict[str, Any]]:
    """Yield dict rows from a parquet file. Uses pyarrow batch
    iteration to keep memory bounded."""
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        sys.stderr.write(
            "pyarrow is required for parquet input. Install with:\n"
            "  pip install -r requirements-calibration.txt\n"
        )
        raise SystemExit(1)
    pf = pq.ParquetFile(str(source))
    for batch in pf.iter_batches():
        for row in batch.to_pylist():
            yield row


def _bucketed_text_path(
    text_dir: Path, row_id: str,
) -> Path:
    """Return a bucketed path for a row's text file:
    `<text_dir>/ab/cd/<row_id>.txt` where `ab` and `cd` are the
    first 4 hex chars of SHA-256(row_id). Bounds files-per-dir
    at ~4096 with 8M rows."""
    h = hashlib.sha256(row_id.encode("utf-8")).hexdigest()
    return text_dir / h[:2] / h[2:4] / f"{row_id}.txt"


def _load_revision_record(source_dir: Path) -> dict[str, Any]:
    record = source_dir / ".fetch_record.json"
    if record.is_file():
        try:
            return json.loads(record.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _ai_status_for_row(row: dict[str, Any]) -> str:
    """Map RAID's `model` field → ai_status."""
    model = (row.get("model") or "").strip().lower()
    if model in {"human", ""}:
        return "human"
    return "ai"


def _editing_status_for_row(row: dict[str, Any]) -> str:
    attack = (row.get("attack") or "").strip().lower()
    if attack in {"none", "", "no_attack"}:
        return "unedited"
    return f"adversarial:{attack}"


def _language_status_for_row(row: dict[str, Any]) -> str:
    domain = (row.get("domain") or "").strip().lower()
    if domain in NONENGLISH_DOMAINS:
        return "non_native_advanced"
    return "native"


def _register_for_row(row: dict[str, Any]) -> str:
    return (row.get("domain") or "unknown").strip().lower()


def _row_id(source_basename: str, raw_id: Any) -> str:
    """Stable per-row id. RAID's `id` field is unique within
    each parquet but we prefix with source-basename for
    cross-source uniqueness."""
    raw = str(raw_id) if raw_id is not None else "no_id"
    return f"raid_{Path(source_basename).stem}_{raw}"


def convert(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        sys.stderr.write(f"--source-dir not found: {source_dir}\n")
        return 1

    parquet_files = sorted(source_dir.rglob("*.parquet"))
    if not parquet_files:
        sys.stderr.write(
            f"No parquet files under {source_dir}. Run "
            "scripts/calibration/fetch_raid.py first.\n"
        )
        return 1

    manifest_path = Path(args.manifest).expanduser().resolve()
    text_dir = Path(args.text_dir).expanduser().resolve()

    # Refuse to write outside the private dir unless override.
    if not args.allow_public_output:
        for p in (manifest_path, text_dir):
            try:
                p.relative_to(PRIVATE_DIR)
            except ValueError:
                sys.stderr.write(
                    f"Refusing to write {p} outside "
                    f"{PRIVATE_DIR}. RAID is Apache-2.0 — "
                    "pass --allow-public-output if you want "
                    "to spill text files into a public "
                    "directory (the manifest still carries "
                    "Apache-2.0 attribution).\n"
                )
                return 2

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    fetch_record = _load_revision_record(source_dir)
    revision = fetch_record.get("revision", "unknown")

    n_written = 0
    n_skipped_adversarial = 0
    n_skipped_empty = 0
    n_skipped_nonprose = 0

    with manifest_path.open("w", encoding="utf-8") as fh_out:
        for parquet in parquet_files:
            if args.limit and n_written >= args.limit:
                break
            for row in _read_rows(parquet):
                if args.limit and n_written >= args.limit:
                    break
                generation = row.get("generation")
                if not isinstance(generation, str) or not generation.strip():
                    n_skipped_empty += 1
                    continue

                editing_status = _editing_status_for_row(row)
                if args.no_adversarial and editing_status != "unedited":
                    n_skipped_adversarial += 1
                    continue

                domain = _register_for_row(row)
                if args.no_nonprose and domain in NONPROSE_DOMAINS:
                    n_skipped_nonprose += 1
                    continue

                row_id = _row_id(parquet.name, row.get("id"))
                text_path = _bucketed_text_path(text_dir, row_id)
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(generation, encoding="utf-8")

                entry = {
                    "id": row_id,
                    "path": str(text_path.relative_to(
                        manifest_path.parent
                    )),
                    "ai_status": _ai_status_for_row(row),
                    "editing_status": editing_status,
                    "register": domain,
                    "language_status": _language_status_for_row(row),
                    "use": "validation",
                    "privacy": "public",
                    "source": "raid",
                    "source_id": row.get("source_id"),
                    "notes": {
                        "model": row.get("model"),
                        "decoding": row.get("decoding"),
                        "repetition_penalty": (
                            row.get("repetition_penalty")
                        ),
                        "attack": row.get("attack"),
                        "adv_source_id": row.get("adv_source_id"),
                        "title": row.get("title"),
                        "hf_revision": revision,
                        "source_parquet": parquet.name,
                    },
                }
                fh_out.write(
                    json.dumps(entry, default=str) + "\n",
                )
                n_written += 1

    sys.stdout.write(
        f"Wrote {n_written} manifest entries to {manifest_path}\n"
        f"  Text spilled to {text_dir}\n"
        f"  Skipped: {n_skipped_empty} empty, "
        f"{n_skipped_adversarial} adversarial, "
        f"{n_skipped_nonprose} non-prose (Code domain)\n"
        f"  HF revision: {revision}\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert RAID parquet files (in "
            "ai-prose-baselines-private/raid/) into a SETEC "
            "manifest slice."
        )
    )
    parser.add_argument(
        "--source-dir", default=str(DEFAULT_SOURCE_DIR),
        help=(
            "Directory containing RAID parquet files "
            "(default: ai-prose-baselines-private/raid/)."
        ),
    )
    parser.add_argument(
        "--manifest", default=str(DEFAULT_MANIFEST),
        help=(
            "Output manifest JSONL path (default: "
            "<source-dir>/manifest.jsonl)."
        ),
    )
    parser.add_argument(
        "--text-dir", default=str(DEFAULT_TEXT_DIR),
        help=(
            "Output text-spill directory (default: "
            "<source-dir>/text/). Uses 4-level hash bucketing."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help=(
            "Stop after N manifest entries (smoke-test mode). "
            "Default 0 = no limit."
        ),
    )
    parser.add_argument(
        "--no-adversarial", action="store_true",
        help=(
            "Skip rows whose `attack` field is non-empty. "
            "Useful when running threshold calibration "
            "(adversarial rows participate in R7's robustness "
            "card eval, not baseline calibration)."
        ),
    )
    parser.add_argument(
        "--no-nonprose", action="store_true",
        help=(
            "Skip rows in non-prose domains (Code). Useful "
            "when calibrating prose-stylometric signals."
        ),
    )
    parser.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Permit writing the manifest and text files "
            "outside ai-prose-baselines-private/. RAID is "
            "Apache-2.0; this is permitted but the framework's "
            "default is to keep all corpus material under the "
            "private dir."
        ),
    )
    return convert(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
