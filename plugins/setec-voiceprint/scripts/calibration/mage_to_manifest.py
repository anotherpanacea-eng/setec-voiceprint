#!/usr/bin/env python3
"""mage_to_manifest.py — convert MAGE parquet files into a SETEC
manifest slice.

Companion to `fetch_mage.py`. Walks the local MAGE parquet
files (under `ai-prose-baselines-private/mage/`), iterates
rows, spills per-row text to bucketed dirs, and emits a
manifest JSONL the harnesses consume.

MAGE schema (per the HF on-disk CSVs, verified 2026-05-10):

  - `text`    the text body (what SETEC's tools see)
  - `label`   0 = human, 1 = machine
  - `src`     source dataset / generator name (e.g.,
              "cmv_human", "xsum_machine_specified_GLM130B").
              (The HF dataset card calls this column `source`;
              the actual CSV header uses `src`. The converter
              accepts either.)

The CSVs ship with a UTF-8 BOM; the converter reads them with
``utf-8-sig`` encoding to strip the BOM transparently.

Manifest mapping (aligned with
`manifest_validator.ALLOWED_*` vocabularies):

  - `id`              mage_<split>_<row_index>
  - `path`            relative path under --text-dir to the
                      spilled text file
  - `ai_status`       "pre_ai_human" if label == 0;
                      "ai_generated" if label == 1.
                      v1.50.0+ (B.4) refinements:
                      * `src` listed in --outline-sources →
                        "ai_generated_from_outline"
                      * `src` matches DIPPER paraphrase tokens
                        ('paraphrase' or 'dipper') → "ai_edited"
                        with `notes.attack: "dipper_paraphrase"`
                        (disable via --no-paraphrase-detection)
  - `editing_status`  "raw_draft" (MAGE doesn't expose edit
                      provenance; raw_draft is the validator's
                      most honest default)
  - `register`        OMITTED. MAGE spans 10 source datasets
                      with per-row variation; no single register
                      value is honest, and the validator doesn't
                      include a "mixed" value. The original
                      source dataset is preserved in
                      `notes.original_source` for slicing.
  - `language_status` "native" (MAGE is English-only)
  - `use`             "validation" by default
  - `privacy`         "shareable" (MIT/Apache-2.0 permissive
                      with attribution; not public_domain)
  - `source`          "mage"
  - `source_id`       the row's `src` field (the original
                      generator / dataset name; the HF dataset
                      card calls this `source` but the on-disk
                      CSV uses `src`)
  - `notes`           {label, original_source, split,
                      source_file, hf_revision}

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
import csv
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
    """Yield dict rows from a parquet or CSV file. CSV uses
    stdlib `csv.DictReader`; parquet uses pyarrow batch iteration.

    HuggingFace ships RAID/MAGE as CSV at the repo root; the
    parquet view in the HF data viewer is a downstream auto-
    conversion. Both extensions are supported.
    """
    suffix = source.suffix.lower()
    if suffix == ".csv":
        try:
            csv.field_size_limit(sys.maxsize)
        except (OverflowError, ValueError):
            csv.field_size_limit(2**31 - 1)
        # ``utf-8-sig`` strips MAGE's UTF-8 BOM. Without this,
        # DictReader.fieldnames would have `﻿text` as its
        # first entry and every `row.get("text")` would return
        # None, dropping every row as "empty."
        fh = source.open("r", encoding="utf-8-sig", newline="")
        try:
            reader = csv.DictReader(fh)
            for row in reader:
                yield dict(row)
        finally:
            fh.close()
        return

    if suffix == ".parquet":
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
        return

    raise ValueError(
        f"Unsupported file extension {suffix!r}: {source}. "
        "Expected .csv or .parquet."
    )


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


def _split_for_source_file(source_name: str) -> str:
    """Infer the split (train/val/test/test_ood) from a source
    filename.

    MAGE ships these splits on HF:
      - train.csv         → "train"
      - valid.csv         → "val"
      - test.csv          → "test"
      - test_ood_set_gpt.csv      → "test_ood_gpt"
      - test_ood_set_gpt_para.csv → "test_ood_gpt_para"

    OOD slices are kept distinct from the standard test split so
    downstream calibration runs can slice on them without
    treating them as part of the in-distribution test.
    """
    name = source_name.lower()
    if "train" in name:
        return "train"
    if "ood" in name and "para" in name:
        return "test_ood_gpt_para"
    if "ood" in name:
        return "test_ood_gpt"
    if "val" in name:
        return "val"
    if "test" in name:
        return "test"
    return "unknown"


# Backwards-compatible alias for the previous private name; some
# external callers may have imported it.
_split_for_parquet = _split_for_source_file


# B.4 (v1.50.0+): MAGE source subsets known to use documented
# outline-based generation, where the LLM was given a substantive
# human seed (outline, brief, point-by-point structure). The exact
# set depends on the maintainer's MAGE export — different MAGE
# distributions on HF use different `src` column conventions — so
# the module ships an empty default and operators opt in via
# ``--outline-sources``. See internal/SPEC_authorship_states.md
# §7.2 for the policy and which subsets warrant the refined value
# (Hello-SimpleAI-hardcoded subsets, when present).
DEFAULT_OUTLINE_SOURCES: frozenset[str] = frozenset()


# B.4: substring tokens that mark a `src` value as a DIPPER (or
# similar) adversarial-paraphrase rewrite. When a row's src matches,
# the row's ai_status flips from ai_generated → ai_edited (the
# paraphrased text is a human-or-AI source rewritten by an LLM —
# closer to ai_edited semantically) and a ``notes.attack`` field is
# emitted. Default-on per SPEC §7.2 bullet 4; ``--no-paraphrase-
# detection`` disables.
PARAPHRASE_SRC_TOKENS: tuple[str, ...] = ("paraphrase", "dipper")


def _ai_status_for_label(
    label: Any,
    src: Any = None,
    *,
    outline_sources: frozenset[str] | set[str] = DEFAULT_OUTLINE_SOURCES,
) -> str:
    """MAGE's label is binary: 0 = human, 1 = machine.

    Maps to manifest_validator.ALLOWED_AI_STATUS:
      0 → "pre_ai_human"
      1 → "ai_generated" — unless the row's ``src`` is in
           ``outline_sources``, in which case we emit
           "ai_generated_from_outline" per SPEC §7.2 (B.4).
      anything else → "unknown"

    The ``src`` lookup is case-insensitive and whitespace-tolerant so
    a MAGE export with ``"Hello-SimpleAI/HC3"`` matches an
    outline-sources entry of ``"hello-simpleai/hc3"`` (and vice
    versa).
    """
    try:
        label_int = int(label)
    except (TypeError, ValueError):
        return "unknown"
    if label_int == 0:
        return "pre_ai_human"
    if label_int == 1:
        if src is not None and outline_sources:
            normalized = str(src).strip().lower()
            if normalized in {s.lower() for s in outline_sources}:
                return "ai_generated_from_outline"
        return "ai_generated"
    return "unknown"


def _is_paraphrase_src(src: Any) -> bool:
    """B.4: heuristic for adversarial-paraphrase rows.

    Returns True when the row's ``src`` value contains one of the
    documented paraphrase-attack tokens (e.g., MAGE's
    ``OOD-set-gpt-paraphrased`` subset surfaces as ``src`` strings
    containing 'paraphrase'). Caller pairs the True result with an
    ``ai_status`` override to ``ai_edited`` and a
    ``notes.attack: "dipper_paraphrase"`` annotation.
    """
    if src is None:
        return False
    low = str(src).lower()
    return any(token in low for token in PARAPHRASE_SRC_TOKENS)


def convert(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        sys.stderr.write(f"--source-dir not found: {source_dir}\n")
        return 1

    # B.4: parse --outline-sources once at startup. ``getattr`` so
    # callers that build a Namespace directly (tests, other
    # callers in the framework) don't have to include the new
    # arguments.
    outline_sources_raw = getattr(args, "outline_sources", "") or ""
    outline_sources: frozenset[str] = frozenset(
        s.strip() for s in outline_sources_raw.split(",") if s.strip()
    )
    detect_paraphrase = not getattr(
        args, "no_paraphrase_detection", False,
    )

    source_files = sorted(
        list(source_dir.rglob("*.csv"))
        + list(source_dir.rglob("*.parquet"))
    )
    if not source_files:
        sys.stderr.write(
            f"No .csv or .parquet files under {source_dir}. Run "
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
        for source_file in source_files:
            if args.limit and n_written >= args.limit:
                break
            split = _split_for_source_file(source_file.name)
            for row_index, row in enumerate(_read_rows(source_file)):
                if args.limit and n_written >= args.limit:
                    break
                text = row.get("text")
                if not isinstance(text, str) or not text.strip():
                    n_skipped_empty += 1
                    continue

                src_value = row.get("src") or row.get("source")
                ai_status = _ai_status_for_label(
                    row.get("label"),
                    src=src_value,
                    outline_sources=outline_sources,
                )
                if ai_status == "unknown":
                    n_skipped_unknown_label += 1
                    continue

                # B.4: adversarial-paraphrase rows (DIPPER and
                # similar) are operationally human/AI source text
                # rewritten by an LLM — ai_edited is the more
                # accurate state than ai_generated. Skip the
                # remapping for pre_ai_human rows (no AI in the
                # pipeline → no remap).
                attack_label: str | None = None
                if (
                    detect_paraphrase
                    and ai_status in ("ai_generated", "ai_generated_from_outline")
                    and _is_paraphrase_src(src_value)
                ):
                    attack_label = "dipper_paraphrase"
                    ai_status = "ai_edited"

                row_id = f"mage_{split}_{row_index:07d}"
                text_path = _bucketed_text_path(text_dir, row_id)
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(text, encoding="utf-8")

                # MAGE spans 10 source datasets with per-row
                # source variation; no single `register` value
                # is honest. We omit the field entirely (it's
                # optional in the manifest schema) and preserve
                # the source dataset in `notes.original_source`
                # for any calibration run that wants to slice
                # per-source.
                entry = {
                    "id": row_id,
                    "path": str(text_path.relative_to(
                        manifest_path.parent
                    )),
                    "ai_status": ai_status,
                    # The validator's allowed editing_status set
                    # is {raw_draft, revised_human,
                    # published_cleaned, coauthored}. MAGE
                    # doesn't expose edit provenance; `raw_draft`
                    # is the most honest default.
                    "editing_status": "raw_draft",
                    "language_status": "native",
                    # ``use`` is list-typed per manifest spec.
                    "use": ["validation"],
                    # MAGE's HF card declares Apache-2.0
                    # (verified 2026-05-10) — permissive but
                    # attribution-required. `shareable` is the
                    # right manifest tier; `public_domain` would
                    # be wrong (MIT/Apache retain copyright).
                    "privacy": "shareable",
                    "source": "mage",
                    "source_id": src_value,
                    "notes": {
                        "label": row.get("label"),
                        "original_source": src_value,
                        "split": split,
                        "source_file": source_file.name,
                        "hf_revision": revision,
                    },
                }
                # B.4: attach attack annotation when the row was
                # detected as adversarial-paraphrase, and record
                # composite_states when ai_status was flipped to
                # mixed (currently only outline-detection produces
                # mixed; this branch is forward-compat).
                if attack_label is not None:
                    entry["notes"]["attack"] = attack_label
                if ai_status == "mixed":
                    entry["notes"].setdefault(
                        "composite_states", ["ai_edited"],
                    )
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
    # B.4 (v1.50.0+): per SPEC_authorship_states.md §7.2, certain
    # MAGE subsets used documented outline-based generation and
    # should map to ``ai_generated_from_outline`` rather than the
    # default ``ai_generated``. The exact src strings depend on the
    # operator's MAGE export, so we ship an empty default and let
    # the operator opt in.
    parser.add_argument(
        "--outline-sources",
        default="",
        help=(
            "Comma-separated MAGE `src` values to map to "
            "ai_status=ai_generated_from_outline instead of "
            "ai_generated. Case-insensitive. Example: "
            "'hello-simpleai/hc3,hello-simpleai-finance'. "
            "Default empty (everything stays ai_generated). "
            "See internal/SPEC_authorship_states.md §7.2."
        ),
    )
    parser.add_argument(
        "--no-paraphrase-detection",
        action="store_true",
        default=False,
        help=(
            "Disable B.4 adversarial-paraphrase detection. Default: "
            "rows whose `src` contains 'paraphrase' or 'dipper' are "
            "remapped to ai_status=ai_edited with "
            "notes.attack=dipper_paraphrase. See SPEC §7.2 bullet 4."
        ),
    )
    return convert(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
