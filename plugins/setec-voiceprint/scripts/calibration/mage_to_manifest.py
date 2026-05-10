#!/usr/bin/env python3
"""mage_to_manifest.py — convert MAGE parquet files into a SETEC
manifest slice.

Companion to `fetch_mage.py`. Walks the local MAGE parquet
files (under `ai-prose-baselines-private/mage/`), iterates
rows, spills per-row text to bucketed dirs, and emits a
manifest JSONL the harnesses consume.

MAGE schema (per HF dataset card):

  - `text`    the text body (what SETEC's tools see)
  - `label`   0 = human, 1 = machine
  - `source`  source dataset / generator name (e.g.,
              "cnn_dailymail", "xsum", "gpt-4-turbo")

Manifest mapping:

  - `id`              mage_<split>_<row_index>
  - `path`            relative path under --text-dir to the
                      spilled text file
  - `ai_status`       "human" if label == 0; else "ai"
  - `editing_status`  "unedited" (MAGE doesn't expose edit
                      provenance)
  - `register`        "mixed" (MAGE spans 10 source datasets;
                      per-row register would require source-
                      column mapping that isn't worth the
                      maintenance burden)
  - `language_status` "native" (MAGE is English-only)
  - `use`             "validation" by default
  - `privacy`         "public" (MIT)
  - `source`          "mage"
  - `source_id`       the row's `source` field (the original
                      generator / dataset name)
  - `notes`           {label, original_source, source_parquet,
                      hf_revision}

Usage:

    python3 scripts/calibration/mage_to_manifest.py

    # Smoke-test 100 rows:
    python3 scripts/calibration/mage_to_manifest.py --limit 100

Defaults:
  --source-dir  ai-prose-baselines-private/mage/
  --manifest    ai-prose-baselines-private/mage/manifest.jsonl
  --text-dir    ai-prose-baselines-private/mage/text/
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
DEFAULT_SOURCE_DIR = PRIVATE_DIR / "mage"
DEFAULT_MANIFEST = DEFAULT_SOURCE_DIR / "manifest.jsonl"
DEFAULT_TEXT_DIR = DEFAULT_SOURCE_DIR / "text"


def _read_rows(source: Path) -> Iterator[dict[str, Any]]:
    """Yield dict rows from a parquet file."""
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


def _bucketed_text_path(text_dir: Path, row_id: str) -> Path:
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


def _split_for_parquet(parquet_name: str) -> str:
    """Infer the split (train/val/test) from a parquet filename."""
    name = parquet_name.lower()
    if "train" in name:
        return "train"
    if "val" in name:
        return "val"
    if "test" in name:
        return "test"
    return "unknown"


def _ai_status_for_label(label: Any) -> str:
    """MAGE's label is binary: 0 = human, 1 = machine."""
    try:
        label_int = int(label)
    except (TypeError, ValueError):
        return "unknown"
    if label_int == 0:
        return "human"
    if label_int == 1:
        return "ai"
    return "unknown"


def convert(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        sys.stderr.write(f"--source-dir not found: {source_dir}\n")
        return 1

    parquet_files = sorted(source_dir.rglob("*.parquet"))
    if not parquet_files:
        sys.stderr.write(
            f"No parquet files under {source_dir}. Run "
            "scripts/calibration/fetch_mage.py first.\n"
        )
        return 1

    manifest_path = Path(args.manifest).expanduser().resolve()
    text_dir = Path(args.text_dir).expanduser().resolve()

    if not args.allow_public_output:
        for p in (manifest_path, text_dir):
            try:
                p.relative_to(PRIVATE_DIR)
            except ValueError:
                sys.stderr.write(
                    f"Refusing to write {p} outside "
                    f"{PRIVATE_DIR}. MAGE is MIT — pass "
                    "--allow-public-output to override.\n"
                )
                return 2

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    fetch_record = _load_revision_record(source_dir)
    revision = fetch_record.get("revision", "unknown")

    n_written = 0
    n_skipped_empty = 0
    n_skipped_unknown_label = 0

    with manifest_path.open("w", encoding="utf-8") as fh_out:
        for parquet in parquet_files:
            if args.limit and n_written >= args.limit:
                break
            split = _split_for_parquet(parquet.name)
            for row_index, row in enumerate(_read_rows(parquet)):
                if args.limit and n_written >= args.limit:
                    break
                text = row.get("text")
                if not isinstance(text, str) or not text.strip():
                    n_skipped_empty += 1
                    continue

                ai_status = _ai_status_for_label(row.get("label"))
                if ai_status == "unknown":
                    n_skipped_unknown_label += 1
                    continue

                row_id = f"mage_{split}_{row_index:07d}"
                text_path = _bucketed_text_path(text_dir, row_id)
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(text, encoding="utf-8")

                entry = {
                    "id": row_id,
                    "path": str(text_path.relative_to(
                        manifest_path.parent
                    )),
                    "ai_status": ai_status,
                    "editing_status": "unedited",
                    "register": "mixed",
                    "language_status": "native",
                    "use": "validation",
                    "privacy": "public",
                    "source": "mage",
                    "source_id": row.get("source"),
                    "notes": {
                        "label": row.get("label"),
                        "original_source": row.get("source"),
                        "split": split,
                        "source_parquet": parquet.name,
                        "hf_revision": revision,
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
        f"{n_skipped_unknown_label} unknown label\n"
        f"  HF revision: {revision}\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert MAGE parquet files (in "
            "ai-prose-baselines-private/mage/) into a SETEC "
            "manifest slice."
        )
    )
    parser.add_argument(
        "--source-dir", default=str(DEFAULT_SOURCE_DIR),
        help=(
            "Directory containing MAGE parquet files "
            "(default: ai-prose-baselines-private/mage/)."
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
            "<source-dir>/text/)."
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
        "--allow-public-output", action="store_true",
        help=(
            "Permit writing the manifest and text files "
            "outside ai-prose-baselines-private/. MAGE is "
            "MIT-licensed."
        ),
    )
    return convert(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
