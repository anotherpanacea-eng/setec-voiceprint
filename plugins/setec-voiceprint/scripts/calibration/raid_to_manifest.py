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

Manifest mapping (aligned with
`manifest_validator.ALLOWED_*` vocabularies):

  - `id`              raid_<source_basename>_<row_id>
  - `path`            relative path under --text-dir to the
                      spilled text file
  - `ai_status`       "pre_ai_human" if model == "human"; else
                      "ai_generated"
  - `editing_status`  "raw_draft" (the validator's allowed set
                      doesn't have an "adversarial" tier;
                      adversarial-transform info lives in
                      `notes.attack` for R7's robustness card)
  - `register`        validator-vocabulary mapping per RAID
                      domain (news → blog_essay, books →
                      literary_fiction, abstracts →
                      academic_philosophy, reviews/reddit/recipes
                      → personal, wikipedia → blog_essay, poetry
                      → literary_fiction). Domains without a
                      clean fit (code, czech, german) OMIT the
                      register field; the raw domain is always
                      preserved in `notes.domain`.
  - `language_status` "native" for English domains;
                      "non_native_advanced" for the extra
                      subset's Czech/German (MT outputs);
                      "unknown" for Code.
  - `use`             "validation" by default
  - `privacy`         "shareable" (MIT — permissive with
                      attribution; not public_domain)
  - `source`          "raid"
  - `source_id`       the row's RAID `source_id`
  - `notes`           {model, decoding, repetition_penalty,
                      attack, domain, adv_source_id, title,
                      hf_revision, source_file}

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
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator

try:
    from tqdm import tqdm  # type: ignore
except ImportError:
    class _NullBar:
        def update(self, n: int = 1) -> None: pass
        def set_postfix_str(self, s: str, refresh: bool = True) -> None: pass
        def close(self) -> None: pass

    def tqdm(  # type: ignore[no-redef]
        iterable=None, total=None, initial=0, unit="it",
        unit_scale=False, desc=None, file=None, **kwargs,
    ):
        return iterable if iterable is not None else _NullBar()


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
    """Yield dict rows from a parquet or CSV file. CSV uses
    stdlib `csv.DictReader` (streaming; bounded memory). Parquet
    uses pyarrow's batch iteration (also streaming).

    HuggingFace's RAID/MAGE repos ship as CSV files at the repo
    root; the parquet view in the HF data viewer is a downstream
    auto-conversion. The fetcher pulls the source files as-is,
    so this converter handles both extensions.
    """
    suffix = source.suffix.lower()
    if suffix == ".csv":
        # csv.field_size_limit defaults to ~128 KB which is too
        # small for RAID generations (some Books / Wikipedia
        # rows are multi-KB blocks). Raise to a generous ceiling.
        try:
            csv.field_size_limit(sys.maxsize)
        except (OverflowError, ValueError):
            csv.field_size_limit(2**31 - 1)
        # ``utf-8-sig`` strips a BOM if present and otherwise
        # behaves identically to ``utf-8``. MAGE's CSVs ship
        # with a UTF-8 BOM that would otherwise corrupt the
        # first column name in DictReader.fieldnames; RAID's
        # CSVs have no BOM and are unaffected.
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
    """Map RAID's `model` field → manifest_validator's
    ALLOWED_AI_STATUS vocabulary. Human rows → `pre_ai_human`;
    everything else (the 11 LLMs) → `ai_generated`."""
    model = (row.get("model") or "").strip().lower()
    if model in {"human", ""}:
        return "pre_ai_human"
    return "ai_generated"


def _editing_status_for_row(row: dict[str, Any]) -> str:
    """Map RAID's `attack` field → manifest_validator's
    ALLOWED_EDITING_STATUS vocabulary.

    The validator's allowed set is
    {raw_draft, revised_human, published_cleaned, coauthored}.
    None of these naturally describes an adversarial transform.
    We map base rows to `raw_draft` and stash the attack
    information in `notes.attack` for downstream filtering by
    R7's robustness card. The manifest's `editing_status` field
    is a property of the writing pipeline; adversarial post-
    processing is closer to a data-transformation flag than an
    editorial pass, so keeping it out of editing_status and in
    the notes block is the more honest mapping.
    """
    return "raw_draft"


# Adversarial-attack token recorded in notes (and used by
# `--no-adversarial` to filter rows). The framework's R7
# robustness card reads `notes.attack` to slice per-attack.
def _attack_token_for_row(row: dict[str, Any]) -> str:
    attack = (row.get("attack") or "").strip().lower()
    if attack in {"none", "", "no_attack"}:
        return "none"
    return attack


def _language_status_for_row(row: dict[str, Any]) -> str:
    domain = (row.get("domain") or "").strip().lower()
    if domain in NONENGLISH_DOMAINS:
        return "non_native_advanced"
    if domain in NONPROSE_DOMAINS:
        # Code is not a natural language; SETEC's stylometric
        # tools have no business adjudicating its variance
        # signals against an English baseline. Map to `unknown`
        # so downstream consumers either skip it or treat it
        # explicitly. Users who want only English prose should
        # also pass `--no-nonprose` at conversion time.
        return "unknown"
    return "native"


# RAID's 8 English domains → manifest_validator.ALLOWED_REGISTER.
# The validator's vocabulary is fiction/blog/academic/testimony/
# personal/policy + literary_horror. RAID's domains don't match
# one-to-one; we pick the closest fit per domain. The original
# domain is preserved in `notes.domain` for finer-grained
# slicing at calibration time.
_DOMAIN_TO_REGISTER = {
    "news": "blog_essay",
    "books": "literary_fiction",
    "abstracts": "academic_philosophy",
    "reviews": "personal",
    "reddit": "personal",
    "recipes": "personal",
    "wikipedia": "blog_essay",
    "poetry": "literary_fiction",
    # `extra` subset:
    "code": None,  # No clean register; omit field.
    "czech": None,  # Non-English; omit field.
    "german": None,  # Non-English; omit field.
}


def _register_for_row(row: dict[str, Any]) -> str | None:
    """Map RAID's `domain` to manifest_validator's
    ALLOWED_REGISTER vocabulary. Returns None when no clean fit
    exists; the converter then omits the `register` field on
    that entry (the field is optional per
    `manifest_validator.REQUIRED_FIELDS`)."""
    domain = (row.get("domain") or "").strip().lower()
    return _DOMAIN_TO_REGISTER.get(domain)


def _raw_domain_for_row(row: dict[str, Any]) -> str:
    """The original RAID `domain` value, preserved in notes for
    fine-grained slicing."""
    return (row.get("domain") or "unknown").strip().lower()


def _row_id(source_basename: str, raw_id: Any) -> str:
    """Stable per-row id. RAID's `id` field is unique within
    each parquet but we prefix with source-basename for
    cross-source uniqueness."""
    raw = str(raw_id) if raw_id is not None else "no_id"
    return f"raid_{Path(source_basename).stem}_{raw}"


def _count_rows_in_file(source: Path) -> int:
    """Row count for tqdm total. For CSV, parses with
    ``csv.reader`` so embedded newlines inside quoted generation
    fields don't inflate the count (RAID generations frequently
    span multiple lines). For parquet, reads the metadata's
    ``num_rows`` (O(1))."""
    suffix = source.suffix.lower()
    if suffix == ".csv":
        try:
            csv.field_size_limit(sys.maxsize)
        except (OverflowError, ValueError):
            csv.field_size_limit(2**31 - 1)
        with source.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            try:
                next(reader)  # discard header
            except StopIteration:
                return 0
            n = sum(1 for _ in reader)
        return n
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore
            pf = pq.ParquetFile(str(source))
            return pf.metadata.num_rows
        except (ImportError, AttributeError):
            # AttributeError covers test fixtures / stub parquet
            # implementations that don't expose .metadata.num_rows.
            # In that case, fall through to "unknown total" (0)
            # and let tqdm show indeterminate progress; we don't
            # want pre-counting to break real conversion runs.
            return 0
    return 0


def convert(args: argparse.Namespace) -> int:
    source_dir = Path(args.source_dir).expanduser().resolve()
    if not source_dir.is_dir():
        sys.stderr.write(f"--source-dir not found: {source_dir}\n")
        return 1

    # Walk for both CSV and parquet — HF ships RAID/MAGE as CSV,
    # but parquet variants may exist in custom mirrors or after
    # a manual conversion. Both are handled by `_read_rows`.
    source_files = sorted(
        list(source_dir.rglob("*.csv"))
        + list(source_dir.rglob("*.parquet"))
    )
    if not source_files:
        sys.stderr.write(
            f"No .csv or .parquet files under {source_dir}. Run "
            "scripts/calibration/fetch_raid.py first.\n"
        )
        return 1

    manifest_path = Path(args.manifest).expanduser().resolve()
    text_dir = Path(args.text_dir).expanduser().resolve()

    # Refuse to write outside the private dir unless override.
    # PRIVATE_DIR is resolved so this check survives Windows
    # junctions / POSIX symlinks pointing the private dir at a
    # different physical location (e.g. an Obsidian-synced Cowork
    # folder). Without ``.resolve()``, manifest_path's resolved
    # form would diverge from PRIVATE_DIR's logical form and the
    # ``relative_to`` check would refuse a legitimate write.
    private_dir_check = (
        PRIVATE_DIR.resolve() if PRIVATE_DIR.exists() else PRIVATE_DIR
    )
    if not args.allow_public_output:
        for p in (manifest_path, text_dir):
            try:
                p.relative_to(private_dir_check)
            except ValueError:
                sys.stderr.write(
                    f"Refusing to write {p} outside "
                    f"{private_dir_check}. RAID is Apache-2.0 — "
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

    # Resume support: if the manifest already exists and --refresh
    # is not set, read the existing manifest and build a set of
    # already-written row IDs. During iteration we compute each
    # source row's would-be ID and skip rows whose ID is already
    # in the set.
    #
    # Skip-by-ID rather than skip-by-position is the load-bearing
    # choice. The previous (1.42.x) implementation used manifest
    # line count as a source-row cursor: ``rows_seen <= n_skip``.
    # That breaks under any filter that consumes a source row
    # without writing a manifest line (``--no-adversarial``,
    # ``--no-nonprose``, empty-generation skips, decode failures).
    # On resume the cursor lands too early in the source stream;
    # rows past the previous endpoint that *were* filter-skipped
    # the first time get newly written this time, and rows that
    # were written the first time get *re-written* under their
    # original IDs — silent duplicates the validator catches
    # only at audit time (we hit this on RAID overnight: 2
    # duplicate IDs across crash-restart cycles).
    #
    # Skip-by-ID is correct regardless of filters or skip-emitting
    # branches: if a row's ID is in the manifest we skip it, full
    # stop. As a side benefit, dedup against the existing manifest
    # is automatic across restarts.
    #
    # ``getattr`` with a default keeps backward compatibility with
    # callers (notably tests) that construct ``argparse.Namespace``
    # objects directly without the new ``refresh`` attribute.
    refresh = getattr(args, "refresh", False)
    seen_ids: set[str] = set()
    if manifest_path.exists() and not refresh:
        with manifest_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Tolerate the malformed line in the
                    # existing manifest. The downstream validator
                    # is the right place to flag this; here we
                    # just want to know which IDs are durable.
                    continue
                rid = entry.get("id")
                if isinstance(rid, str) and rid:
                    seen_ids.add(rid)
        if seen_ids:
            sys.stderr.write(
                f"Resuming: {len(seen_ids):,} row IDs already in "
                f"manifest; rows with matching computed IDs will "
                f"be skipped.\n"
            )
    elif refresh and manifest_path.exists():
        manifest_path.unlink()

    # Pre-count source rows for tqdm total. Fast (byte-level) on
    # parquet, single-pass csv-aware count on CSV. ~1-3 min of
    # upfront I/O for RAID-scale corpora; cheap relative to the
    # conversion run.
    sys.stderr.write("Counting source rows for progress bar...\n")
    total_rows = sum(_count_rows_in_file(p) for p in source_files)
    sys.stderr.write(f"Total source rows: {total_rows:,}\n")

    mode = "a" if seen_ids else "w"

    n_written = 0
    n_skipped_adversarial = 0
    n_skipped_empty = 0
    n_skipped_nonprose = 0
    n_skipped_resume = 0  # rows skipped because already in manifest

    bar = tqdm(
        total=total_rows,
        initial=len(seen_ids),
        unit="row",
        unit_scale=True,
        desc=f"convert {manifest_path.name}",
        file=sys.stderr,
    )
    try:
      with manifest_path.open(mode, encoding="utf-8") as fh_out:
        for source_file in source_files:
            if args.limit and n_written >= args.limit:
                break
            for row in _read_rows(source_file):
                if args.limit and n_written >= args.limit:
                    break
                # Compute the would-be ID first; if it's already
                # in the manifest, skip the entire row (incl. all
                # filter checks and text-file writes). This is
                # what makes resume correct under filters: a row
                # that was filter-skipped in the first run has
                # NO entry in the manifest, so its ID isn't in
                # seen_ids, so we process it normally. A row that
                # was successfully written in the first run has
                # its ID in seen_ids, so we skip it cleanly.
                row_id = _row_id(source_file.name, row.get("id"))
                if row_id in seen_ids:
                    n_skipped_resume += 1
                    continue
                bar.update(1)
                generation = row.get("generation")
                if not isinstance(generation, str) or not generation.strip():
                    n_skipped_empty += 1
                    continue

                attack = _attack_token_for_row(row)
                if args.no_adversarial and attack != "none":
                    n_skipped_adversarial += 1
                    continue

                raw_domain = _raw_domain_for_row(row)
                if args.no_nonprose and raw_domain in NONPROSE_DOMAINS:
                    n_skipped_nonprose += 1
                    continue

                text_path = _bucketed_text_path(text_dir, row_id)
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(generation, encoding="utf-8")

                entry: dict[str, Any] = {
                    "id": row_id,
                    "path": str(text_path.relative_to(
                        manifest_path.parent
                    )),
                    "ai_status": _ai_status_for_row(row),
                    "editing_status": _editing_status_for_row(row),
                    "language_status": _language_status_for_row(row),
                    # ``use`` is list-typed per manifest spec.
                    "use": ["validation"],
                    # RAID's HF card declares MIT (verified 2026-
                    # 05-10) — permissive but attribution-required.
                    # `shareable` is the right manifest tier;
                    # `public_domain` would be wrong because MIT
                    # is not public-domain (it retains copyright).
                    "privacy": "shareable",
                    "source": "raid",
                    "source_id": row.get("source_id"),
                    "notes": {
                        "model": row.get("model"),
                        "decoding": row.get("decoding"),
                        "repetition_penalty": (
                            row.get("repetition_penalty")
                        ),
                        "attack": attack,
                        "domain": raw_domain,
                        "adv_source_id": row.get("adv_source_id"),
                        "title": row.get("title"),
                        "hf_revision": revision,
                        "source_file": source_file.name,
                    },
                }
                # Register is optional in the manifest schema; we
                # only emit it when there's a clean fit between
                # RAID's domain and the validator's vocabulary.
                # The original domain is always preserved in
                # notes.domain for slicing.
                mapped_register = _register_for_row(row)
                if mapped_register is not None:
                    entry["register"] = mapped_register
                fh_out.write(
                    json.dumps(entry, default=str) + "\n",
                )
                # Track this row's ID in seen_ids so within-run
                # dedup (e.g., from a malformed source row with a
                # repeating "no_id" fallback) is also automatic.
                seen_ids.add(row_id)
                n_written += 1
    finally:
        bar.close()

    n_resumed = len(seen_ids) - n_written  # IDs present at start
    total_in_manifest = n_resumed + n_written
    sys.stdout.write(
        f"Wrote {n_written} new manifest entries to {manifest_path}"
        + (
            f" (resumed from {n_resumed:,}; total {total_in_manifest:,})\n"
            if n_resumed else "\n"
        )
        + f"  Text spilled to {text_dir}\n"
        f"  Skipped this run: {n_skipped_empty} empty, "
        f"{n_skipped_adversarial} adversarial, "
        f"{n_skipped_nonprose} non-prose (Code domain), "
        f"{n_skipped_resume} already in manifest\n"
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
    parser.add_argument(
        "--refresh", action="store_true",
        help=(
            "Discard any existing manifest at the output path "
            "and start over from row 0. Default behavior is to "
            "resume: if the manifest already has N lines, the "
            "converter skips the first N rows in deterministic "
            "iteration order and appends from there."
        ),
    )
    return convert(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
