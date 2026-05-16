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
import datetime as _dt
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

    # ----- Resume from existing partial output (1.70.0+).
    # The output JSONL is naturally newline-separated entries; any
    # complete prior run leaves a parseable file. If --resume is on
    # (default), we read the existing IDs and append to the file
    # instead of overwriting — operators can re-run after a crash
    # and only the missing rows are processed (text + manifest
    # entry).
    #
    # Conversion-settings sidecar (codex P2 on PR #72): a
    # <out_path>.meta.json file records the conversion args every
    # row in the output was produced under. On resume, the sidecar
    # is checked against the current run's args. If any
    # conversion arg differs (label_map, text_column, preset,
    # register, language_status, notes_columns,
    # mixed_composite_states, use, source_label), resume is
    # refused and the operator is told to use --refresh-output.
    # Without this check, changing args between runs would
    # silently mix incompatible-semantics rows in the same
    # manifest.
    resume_mode = bool(
        getattr(args, "resume", True)
        and not getattr(args, "refresh_output", False)
    )
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
    current_meta = {
        "label_map": label_map,
        "text_column": text_column,
        "label_column": label_column,
        "preset": getattr(args, "preset", None),
        "register": register,
        "language_status": language_status,
        "notes_columns": list(notes_columns),
        "mixed_composite_states": list(mixed_composite_states)
            if mixed_composite_states is not None else None,
        "use_tags": use_tags,
        "source_label": source_label,
        "source_basename": source.name,
    }
    already_written_ids: set[str] = set()
    open_mode = "w"
    if resume_mode and out_path.exists():
        # Sidecar check: refuse resume on conversion-settings drift.
        if meta_path.exists():
            try:
                prior_meta = json.loads(
                    meta_path.read_text(encoding="utf-8"),
                )
                # Compare every field. Any mismatch refuses the
                # resume and bails. The operator can then either
                # pass --refresh-output to overwrite, or use a
                # different --out path.
                mismatches = []
                for k, current_v in current_meta.items():
                    prior_v = prior_meta.get(k)
                    if prior_v != current_v:
                        mismatches.append(
                            f"{k}: prior={prior_v!r}, "
                            f"current={current_v!r}"
                        )
                if mismatches:
                    sys.stderr.write(
                        f"REFUSING RESUME: conversion settings in "
                        f"{meta_path} differ from current run "
                        f"({len(mismatches)} mismatch(es)):\n"
                    )
                    for m in mismatches:
                        sys.stderr.write(f"  - {m}\n")
                    sys.stderr.write(
                        f"Pass --refresh-output (or --no-resume) to "
                        f"discard the prior output and re-convert, "
                        f"or use a different --out path to preserve "
                        f"both manifests.\n"
                    )
                    return 1
            except (json.JSONDecodeError, OSError) as exc:
                sys.stderr.write(
                    f"Could not parse conversion-settings sidecar "
                    f"at {meta_path} ({exc}); refusing resume to "
                    f"avoid silent semantic drift. Pass "
                    f"--refresh-output to overwrite.\n"
                )
                return 1
        else:
            sys.stderr.write(
                f"Note: existing {out_path} has no conversion-"
                f"settings sidecar (pre-1.70.0 output). Proceeding "
                f"with resume but cannot verify that conversion "
                f"args match what produced the existing rows. If "
                f"args changed since the prior run, pass "
                f"--refresh-output instead.\n"
            )
        try:
            with out_path.open("r", encoding="utf-8") as fh_in:
                for line in fh_in:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        prior = json.loads(line)
                    except json.JSONDecodeError:
                        # Bad line (mid-row crash?) — bail and re-
                        # write from scratch to avoid mixing a
                        # truncated prior with new lines.
                        already_written_ids.clear()
                        sys.stderr.write(
                            f"Partial output at {out_path} has a "
                            f"malformed line; discarding and "
                            f"re-writing from scratch.\n"
                        )
                        break
                    prior_id = prior.get("id")
                    if isinstance(prior_id, str):
                        already_written_ids.add(prior_id)
            if already_written_ids:
                open_mode = "a"  # append; preserve prior rows
                sys.stderr.write(
                    f"Resuming conversion: {len(already_written_ids)} "
                    f"manifest entries already in {out_path}. "
                    f"Skipping rows with matching IDs. Pass "
                    f"--no-resume / --refresh-output to overwrite.\n"
                )
        except OSError as exc:
            sys.stderr.write(
                f"Could not read existing {out_path} for resume "
                f"({exc}); will overwrite.\n"
            )
            already_written_ids.clear()
            open_mode = "w"

    # Write/refresh the sidecar BEFORE the conversion loop so a
    # crash mid-loop still leaves the meta + partial JSONL paired.
    # The conversion is idempotent under stable args, so the same
    # meta is correct on re-write.
    try:
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_meta = meta_path.with_suffix(meta_path.suffix + ".tmp")
        with tmp_meta.open("w", encoding="utf-8") as fh:
            json.dump(current_meta, fh, indent=2, default=str)
        tmp_meta.replace(meta_path)
    except OSError as exc:
        sys.stderr.write(
            f"Could not write conversion-settings sidecar at "
            f"{meta_path} ({exc}); proceeding without it. Future "
            f"resume runs will lack drift detection.\n"
        )

    flush_every = max(1, int(getattr(args, "flush_every", 1000)))
    n_written = 0
    n_skipped_resume = 0
    convert_t0 = _dt.datetime.now()
    with out_path.open(open_mode, encoding="utf-8") as fh_out:
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
            if entry_id in already_written_ids:
                n_skipped_resume += 1
                continue
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
            # Periodic flush + progress log. Flushing keeps the
            # OS buffer's prior-row commitment minimal so a
            # crash loses at most flush_every rows of work that
            # hadn't yet hit disk. Progress goes to stderr so
            # downstream parsers of stdout (none here today, but
            # symmetry with the other 1.70.0 PRs) stay clean.
            if n_written % flush_every == 0:
                fh_out.flush()
                elapsed = (
                    _dt.datetime.now() - convert_t0
                ).total_seconds()
                rate = n_written / max(elapsed, 1e-9)
                sys.stderr.write(
                    f"  converted {n_written} rows "
                    f"({rate:.0f}/s) -> flushed at row "
                    f"{row_index + 1}.\n"
                )

    sys.stdout.write(
        f"Wrote {n_written} manifest entries to {out_path}\n"
        f"  per-row text files in {text_dir}\n"
    )
    if n_skipped_resume:
        sys.stdout.write(
            f"  resume: skipped {n_skipped_resume} row(s) already "
            f"present in prior output.\n"
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
    # Resume + incremental flush (1.70.0). EditLens parquet sources
    # can be 1M+ rows; the original pre-1.70.0 code opened --out in
    # 'w' mode and only flushed at the natural OS buffer cadence —
    # a crash at row 800K of 1M lost most of the work and the
    # operator had no way to pick up where they left off. Resume
    # mode reads the existing --out, builds the set of already-
    # written entry IDs, and skips matching source rows; periodic
    # flushes commit each batch to disk so a crash loses at most
    # --flush-every rows of work.
    parser.add_argument(
        "--no-resume", dest="resume",
        action="store_false", default=True,
        help=(
            "Force overwrite of --out instead of appending. Pre-"
            "1.70.0 behavior. Default behavior (resume on) reads "
            "any existing --out, derives already-converted entry "
            "IDs, opens the file in append mode, and skips rows "
            "with matching IDs. Use --no-resume when you want a "
            "clean re-conversion regardless of any partial output."
        ),
    )
    parser.add_argument(
        "--refresh-output", action="store_true",
        help=(
            "Alias for --no-resume. Discards any existing --out "
            "and starts conversion from row 0. Use when the prior "
            "output is from a stale --label-map / --text-column / "
            "any other arg that would silently invalidate the "
            "prior entries."
        ),
    )
    parser.add_argument(
        "--flush-every", type=int, default=1000,
        help=(
            "Flush --out and emit a progress line to stderr every "
            "N converted rows (default 1000). Lower (100-500) on "
            "long-tail rows where the OS buffer might lose many "
            "rows on a crash; higher (5000+) for fast pass-"
            "through where flush I/O would dominate."
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
