#!/usr/bin/env python3
"""editlens_to_manifest.py

Step 3 of the calibration toolchain. Converts a Pangram parquet
(or any compatible labeled CSV/parquet) into a SETEC
`corpus_manifest.jsonl` slice that the existing harnesses
(validation_harness.py, voice_validation_harness.py) can consume.

Schema-discovery is required: explicit `--text-column`,
`--label-column`, and `--label-map` flags are required unless a known
`--preset` matches. `--inspect` mode prints the columns and a sample
row, then exits cleanly so users can figure out the right flag values.

Usage:

    # Discover schema:
    python3 scripts/calibration/editlens_to_manifest.py \\
        --inspect \\
        --source ai-prose-baselines-private/editlens/.../nonnative_english.parquet

    # Convert with explicit columns:
    python3 scripts/calibration/editlens_to_manifest.py \\
        --source ai-prose-baselines-private/editlens/.../nonnative_english.parquet \\
        --text-column text \\
        --label-column label \\
        --label-map "0=pre_ai_human,1=ai_generated" \\
        --register essay \\
        --language-status non_native_advanced \\
        --out ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \\
        --text-dir ai-prose-baselines-private/editlens/nonnative_text

    # Or use a built-in preset:
    python3 scripts/calibration/editlens_to_manifest.py \\
        --source ai-prose-baselines-private/editlens/.../nonnative_english.parquet \\
        --preset editlens_nonnative \\
        --out ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \\
        --text-dir ai-prose-baselines-private/editlens/nonnative_text

Per-row text files are spilled to `--text-dir`. SETEC's tools read
paths, not inline text, so the spillout is required. The text files
are CC-NC corpus content and must stay in
`ai-prose-baselines-private/`; never commit them.

The output manifest passes `manifest_validator.validate_manifest`
without errors. Reference-detector scores from the source row
(`fastdetectgpt_score`, `binoculars_score`, `editlens_*`,
`pangram_v3.2_score`) are preserved in the entry's `notes` field for
cross-tool comparison; the manifest itself stays in the gitignored
private directory.

**v1.49.0+ (B.4)**: Pangram label `-1` (the "edited/mixed" class)
maps to `ai_status: mixed` with `notes.composite_states: ["ai_edited"]`
by default, satisfying the B.2 validator soft warning and giving
downstream consumers the sub-state granularity to route on. Previous
behavior dropped label `-1` rows silently; the new behavior preserves
them. Operators who want the previous "drop -1" behavior can pass
`--label-map "0=pre_ai_human,1=ai_generated"` (without the `-1`
entry) to keep those rows out of the manifest. See
`internal/SPEC_authorship_states.md` §7.1 for the mapping rationale.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

# After 1.16.0, scripts live inside the plugin directory.
# parents[4] is the repo root; parents[1] is scripts/ for the
# sibling-import sys.path manipulation.
REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# Default composite-states list for any row mapped to `ai_status:
# mixed`. Per SPEC_authorship_states.md §7.1, EditLens's label `-1`
# (mixed/edited class) is operationally "human-authored AI-modified"
# without further granularity, so a single-element list with
# `ai_edited` captures that honestly. Operators can override per-run
# via the --mixed-composite-states CLI flag.
DEFAULT_MIXED_COMPOSITE_STATES: tuple[str, ...] = ("ai_edited",)


# Built-in presets covering the common Pangram split shapes. Each maps
# to the column / labeling decisions a v1 calibration run would use.
# Users are not required to use a preset; explicit flags always
# override the preset.
#
# v1.49.0+ (B.4): label `-1` maps to `mixed` instead of being silently
# dropped. The row receives a `notes.composite_states` list (default
# `["ai_edited"]`) so the soft validator check from B.2 is satisfied
# and downstream consumers can route by sub-state.
PRESETS: dict[str, dict[str, Any]] = {
    "editlens_nonnative": {
        "text_column": "text",
        "label_column": "label",
        "label_map": {
            "0": "pre_ai_human",
            "1": "ai_generated",
            "-1": "mixed",
        },
        "register": "essay",
        "language_status": "non_native_advanced",
        "notes_columns": (
            "fastdetectgpt_score", "binoculars_score",
            "editlens_roberta-large_score", "editlens_roberta-large_bucket",
            "editlens_Llama-3.2-3B_score", "editlens_Llama-3.2-3B_bucket",
            "pangram_v3.2_score",
        ),
    },
    "editlens_test": {
        "text_column": "text",
        "label_column": "label",
        "label_map": {
            "0": "pre_ai_human",
            "1": "ai_generated",
            "-1": "mixed",
        },
        "register": "essay",
        "language_status": "native",
        "notes_columns": (
            "fastdetectgpt_score", "binoculars_score",
            "editlens_roberta-large_score", "editlens_roberta-large_bucket",
            "editlens_Llama-3.2-3B_score", "editlens_Llama-3.2-3B_bucket",
            "pangram_v3.2_score",
        ),
    },
    "editlens_human_detectors": {
        "text_column": "text",
        "label_column": "label",
        "label_map": {
            "0": "pre_ai_human",
            "1": "ai_generated",
            "-1": "mixed",
        },
        "register": "mixed",
        "language_status": "native",
        "notes_columns": (
            "model", "title", "source", "comments",
            "fastdetectgpt_score", "binoculars_score",
            "editlens_roberta-large_score", "editlens_roberta-large_bucket",
            "editlens_Llama-3.2-3B_score", "editlens_Llama-3.2-3B_bucket",
            "pangram_v3.2_score",
        ),
    },
}


def _read_rows(source: Path) -> tuple[list[str], Iterator[dict[str, Any]]]:
    """Open a CSV or parquet file and return (column_names, row_iter).
    Lazy iteration so we don't pull every row into memory at once."""
    suffix = source.suffix.lower()
    if suffix == ".csv":
        fh = source.open("r", encoding="utf-8", newline="")
        reader = csv.DictReader(fh)
        columns = list(reader.fieldnames or [])

        def _iter_csv() -> Iterator[dict[str, Any]]:
            try:
                for row in reader:
                    yield dict(row)
            finally:
                fh.close()

        return columns, _iter_csv()

    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore
        except ImportError:
            sys.stderr.write(
                "pyarrow is required for parquet input. Install with:\n"
                "  pip install -r requirements-calibration.txt\n"
            )
            raise SystemExit(1)
        table = pq.read_table(str(source))
        columns = list(table.column_names)

        def _iter_parquet() -> Iterator[dict[str, Any]]:
            for batch in table.to_batches():
                for row in batch.to_pylist():
                    yield row

        return columns, _iter_parquet()

    raise ValueError(
        f"Unsupported source format: {suffix!r}. Use .csv or .parquet."
    )


def _stable_id(source_basename: str, row_index: int) -> str:
    """Deterministic per-row id. SHA-256 truncated to 16 hex chars,
    prefixed with the source basename for human readability."""
    h = hashlib.sha256(
        f"{source_basename}:{row_index}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{Path(source_basename).stem}_{row_index:06d}_{h}"


def _load_revision_record(source: Path) -> dict[str, Any]:
    """Walk up from the source file looking for a `.fetch_record.json`
    that fetch_pangram_editlens.py wrote. Used to record the HF
    revision SHA in each manifest entry's `source` field."""
    cur = source.parent
    while cur != cur.parent:
        record = cur / ".fetch_record.json"
        if record.is_file():
            try:
                return json.loads(record.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        cur = cur.parent
    return {}


def _parse_label_map(raw: str) -> dict[str, str]:
    """Parse `KEY=VALUE,KEY=VALUE` into {KEY: VALUE}."""
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise ValueError(
                f"Bad --label-map entry {chunk!r}: expected KEY=VALUE"
            )
        k, v = chunk.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text))


def inspect(source: Path, n_sample_rows: int = 1) -> int:
    columns, row_iter = _read_rows(source)
    sys.stdout.write(f"Source: {source}\n")
    sys.stdout.write(f"Columns ({len(columns)}):\n")
    for c in columns:
        sys.stdout.write(f"  {c}\n")
    sys.stdout.write("\nSample rows:\n")
    for i, row in enumerate(row_iter):
        if i >= n_sample_rows:
            break
        for col in columns:
            val = row.get(col)
            if isinstance(val, str) and len(val) > 200:
                val = val[:200] + "..."
            sys.stdout.write(f"  {col}: {val!r}\n")
        sys.stdout.write("\n")
    sys.stdout.write(
        "Recommended next: choose --text-column, --label-column, and "
        "--label-map (or use --preset if your shape matches a known "
        "Pangram split).\n"
    )
    return 0


def convert(args: argparse.Namespace) -> int:
    source = Path(args.source).resolve()
    if not source.is_file():
        sys.stderr.write(f"Source not found: {source}\n")
        return 1

    # Resolve preset, then let explicit flags override.
    preset_data: dict[str, Any] = {}
    if args.preset:
        if args.preset not in PRESETS:
            sys.stderr.write(
                f"Unknown preset {args.preset!r}. Known: "
                f"{', '.join(sorted(PRESETS))}\n"
            )
            return 1
        preset_data = dict(PRESETS[args.preset])

    text_column = args.text_column or preset_data.get("text_column")
    label_column = args.label_column or preset_data.get("label_column")
    label_map_raw = args.label_map
    if label_map_raw:
        label_map = _parse_label_map(label_map_raw)
    else:
        label_map = preset_data.get("label_map", {})
    register = args.register or preset_data.get("register", "essay")
    language_status = args.language_status or preset_data.get(
        "language_status", "native"
    )
    use_tags = args.use.split(",") if args.use else ["validation"]
    notes_columns = (
        tuple(args.notes_columns.split(",")) if args.notes_columns
        else tuple(preset_data.get("notes_columns", ()))
    )
    # B.4: composite states for ai_status=mixed rows. CLI flag wins;
    # preset's mixed_composite_states is the fallback; module-level
    # DEFAULT_MIXED_COMPOSITE_STATES is the floor. The empty-string
    # case (--mixed-composite-states "") leaves it empty so the
    # validator's B.2 soft check still fires (useful for operators
    # who want to surface the warning).
    if args.mixed_composite_states is not None:
        mixed_composite_states = tuple(
            s.strip() for s in args.mixed_composite_states.split(",")
            if s.strip()
        )
    else:
        mixed_composite_states = tuple(
            preset_data.get(
                "mixed_composite_states",
                DEFAULT_MIXED_COMPOSITE_STATES,
            )
        )

    if not text_column or not label_column or not label_map:
        sys.stderr.write(
            "Missing required column flags. Provide --text-column, "
            "--label-column, and --label-map, or use a --preset that "
            "fills them in.\n"
            "Hint: run with --inspect to see the source's columns.\n"
        )
        return 1

    out_path = Path(args.out).resolve()
    text_dir = Path(args.text_dir).resolve() if args.text_dir else (
        out_path.parent / f"{out_path.stem}_text"
    )

    # Refuse to write outside the private directory unless override.
    private_dir = REPO_ROOT / "ai-prose-baselines-private"
    if not args.allow_public_output:
        for p in (out_path, text_dir):
            try:
                p.relative_to(private_dir)
            except ValueError:
                sys.stderr.write(
                    f"Refusing to write {p} outside "
                    f"{private_dir}. CC-NC corpora must stay local. "
                    "Pass --allow-public-output to override (only for "
                    "non-CC-NC corpora).\n"
                )
                return 2

    text_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    columns, row_iter = _read_rows(source)
    if text_column not in columns:
        sys.stderr.write(
            f"--text-column {text_column!r} not found in source "
            f"columns {columns}.\n"
        )
        return 1
    if label_column not in columns:
        sys.stderr.write(
            f"--label-column {label_column!r} not found in source "
            f"columns {columns}.\n"
        )
        return 1

    fetch_record = _load_revision_record(source)
    revision = fetch_record.get("revision", "unknown")
    repo_id = fetch_record.get("repo_id", "")
    source_label = (
        f"{source.name} @ {repo_id} revision {revision}"
        if repo_id else source.name
    )

    n_written = 0
    with out_path.open("w", encoding="utf-8") as fh_out:
        for row_index, row in enumerate(row_iter):
            if args.max_rows and n_written >= args.max_rows:
                break
            text = row.get(text_column)
            if not isinstance(text, str) or not text.strip():
                continue
            raw_label = row.get(label_column)
            if raw_label is None:
                continue
            label_key = str(raw_label).strip()
            ai_status = label_map.get(label_key)
            if ai_status is None:
                # Unknown label value — log and skip.
                sys.stderr.write(
                    f"Skipping row {row_index}: label {label_key!r} "
                    f"not in --label-map.\n"
                )
                continue

            entry_id = _stable_id(source.name, row_index)
            text_path = text_dir / f"{entry_id}.txt"
            text_path.write_text(text, encoding="utf-8")

            notes: dict[str, Any] = {}
            for col in notes_columns:
                if col in row and row[col] is not None:
                    notes[col] = row[col]
            # B.4: ai_status=mixed rows carry `composite_states` so
            # the B.2 validator soft check is satisfied and downstream
            # consumers can route by sub-state (per SPEC §6.4).
            if ai_status == "mixed" and mixed_composite_states:
                notes["composite_states"] = list(mixed_composite_states)

            entry: dict[str, Any] = {
                "id": entry_id,
                "path": str(text_path.resolve()),
                "ai_status": ai_status,
                "language_status": language_status,
                "register": register,
                "word_count": _word_count(text),
                "use": use_tags,
                "split": "test",
                "privacy": "private",
                "source": source_label,
                "adversarial_class": "none",
            }
            # B.4 (v1.49.0+): write notes as a dict, not a JSON
            # string. The manifest validator inspects
            # `entry["notes"]["composite_states"]` for the B.2 soft
            # check on `ai_status: mixed`; a JSON-string `notes`
            # field defeats that check (and any other downstream
            # consumer that walks notes structurally). MAGE's
            # converter has always written notes as a dict; this
            # change aligns EditLens with that convention.
            #
            # Backwards compat: consumers that previously read
            # `notes` as a JSON string get a structured dict
            # instead. Calling `json.loads()` on the dict-form is
            # a TypeError; any downstream code that defensively
            # checks `isinstance(notes, str)` keeps working.
            if notes:
                entry["notes"] = notes
            fh_out.write(
                json.dumps(entry, ensure_ascii=False) + "\n"
            )
            n_written += 1

    sys.stdout.write(
        f"Wrote {n_written} manifest entries to {out_path}\n"
        f"  per-row text files in {text_dir}\n"
    )

    # Validate the output manifest.
    try:
        from manifest_validator import validate_manifest  # type: ignore
    except ImportError:
        sys.stderr.write(
            "Could not import manifest_validator; skipping validation.\n"
        )
        return 0

    result = validate_manifest(str(out_path))
    issues = result.get("issues") or []
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    if errors:
        sys.stderr.write(f"\nManifest validation FAILED ({len(errors)} errors):\n")
        for e in errors[:10]:
            sys.stderr.write(
                f"  line {e.get('lineno', '?')}: {e.get('message', '?')}\n"
            )
        return 3
    if warnings:
        sys.stdout.write(
            f"\nManifest validation passed with {len(warnings)} "
            f"warning(s):\n"
        )
        for w in warnings[:10]:
            sys.stdout.write(
                f"  line {w.get('lineno', '?')}: {w.get('message', '?')}\n"
            )
    else:
        sys.stdout.write("\nManifest validation: clean.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a Pangram-shaped CSV/parquet into a SETEC "
            "corpus_manifest.jsonl slice."
        )
    )
    parser.add_argument(
        "--source", required=True,
        help="Path to a CSV or parquet file from the EditLens corpus.",
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Print columns + a sample row and exit. Don't write anything.",
    )
    parser.add_argument(
        "--out",
        help="Output manifest path (.jsonl). Required unless --inspect.",
    )
    parser.add_argument(
        "--text-dir",
        help=(
            "Directory for per-row .txt files. Default: sibling of --out "
            "named '<out_stem>_text'."
        ),
    )
    parser.add_argument(
        "--text-column",
        help="Source column containing the prose text.",
    )
    parser.add_argument(
        "--label-column",
        help="Source column containing the binary label.",
    )
    parser.add_argument(
        "--label-map",
        help=(
            "Comma-separated KEY=VALUE pairs mapping source label values "
            "to SETEC ai_status enum, e.g. '0=pre_ai_human,1=ai_generated'."
        ),
    )
    parser.add_argument(
        "--register", help="SETEC register tag (default: essay).",
    )
    parser.add_argument(
        "--language-status",
        help=(
            "SETEC language_status tag. Default: native. "
            "For nonnative_english: non_native_advanced."
        ),
    )
    parser.add_argument(
        "--use", default="validation",
        help="Comma-separated SETEC `use` tags. Default: validation.",
    )
    parser.add_argument(
        "--notes-columns",
        help=(
            "Comma-separated source columns to preserve in each entry's "
            "notes field for cross-tool comparison."
        ),
    )
    parser.add_argument(
        "--mixed-composite-states",
        default=None,
        help=(
            "Comma-separated SETEC ai_status sub-values to record as "
            "`notes.composite_states` for any row mapped to "
            "`ai_status: mixed`. Default: ai_edited. Pass an empty "
            "string to omit (which will trigger the B.2 validator "
            "soft warning). See "
            "internal/SPEC_authorship_states.md §6.4."
        ),
    )
    parser.add_argument(
        "--preset", help=f"Use a built-in preset: {', '.join(sorted(PRESETS))}",
    )
    parser.add_argument(
        "--max-rows", type=int, default=None,
        help="Cap the number of rows converted (useful for smoke tests).",
    )
    parser.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Allow writing the manifest and per-row text files outside "
            "ai-prose-baselines-private/. Use only for non-CC-NC corpora."
        ),
    )

    args = parser.parse_args(argv)

    if args.inspect:
        return inspect(Path(args.source))

    if not args.out:
        sys.stderr.write("--out is required unless --inspect is passed.\n")
        return 1

    return convert(args)


if __name__ == "__main__":
    sys.exit(main())
