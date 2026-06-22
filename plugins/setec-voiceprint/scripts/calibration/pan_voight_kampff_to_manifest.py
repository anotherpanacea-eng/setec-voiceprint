#!/usr/bin/env python3
"""pan_voight_kampff_to_manifest.py — convert a locally-staged PAN@CLEF
Voight-Kampff Subtask-1 release into a SETEC manifest slice.

Companion to the Voight-Kampff benchmark harness
(``pan_voight_kampff_benchmark.py``). Mirrors ``mage_to_manifest.py`` /
``raid_to_manifest.py``: walks the PAN instance + label files, spills
each text body to a bucketed dir, and emits a manifest JSONL the harness
consumes.

PAN Subtask-1 is **binary human-vs-machine** text classification where
the machine text was generated to **mimic a specific human author**. The
PAN release format (pinned against the Apache-2.0 reference repo
``pan-webis-de/pan25-generative-ai-authorship-verification``, not
reconstructed from the names):

  - **Instance file (JSONL):** one object per line with ``id`` + ``text``
    (the baseline CLI reads ``j['text']`` / ``j['id']`` — see
    ``baselines/cli.py``).
  - **Label / truth file (JSONL):** one object per line with ``id`` +
    ``label`` (the evaluator's ``load_problem_file`` reads ``j['label']``;
    ``0`` = human, ``1`` = machine; a ``[bool, bool]`` one-hot is also
    accepted, where ``label[0]`` true => human => ``0``).

  The labeled split may carry the gold label **in the instance file**
  (an ``is_human`` / ``is_ai`` / ``label`` field on the same row) **or in
  a separate truth file** (joined on ``id``). The adapter tolerates both,
  plus a CSV form of either file (BOM-tolerant, streaming) — matching the
  MAGE/RAID precedent.

**Field-map = the single edit point.** If PAN 2026 changes the instance
or label field names, edit ``INSTANCE_ID_KEYS`` / ``INSTANCE_TEXT_KEYS``
/ ``LABEL_KEYS`` / ``LABEL_ID_KEYS`` below and nothing else. The dataset
**download is an out-of-M1 seam**: this adapter reads whatever the
operator staged locally from Zenodo record 14962653; it vendors NO PAN
text and writes a ``NOTICE.md`` (attribution + redistribution
prohibition) next to the spilled text.

Manifest mapping (aligned with ``manifest_validator.ALLOWED_*``):

  - ``id``              ``pan_vk_<split>_<instance id>``
  - ``path``            relative path under --text-dir to the spilled text
  - ``ai_status``       "pre_ai_human" if label == 0 (human);
                        "ai_generated" if label == 1 (machine).
  - ``editing_status``  "raw_draft" (PAN exposes no edit provenance)
  - ``language_status`` "unknown" (PAN 2025 VK Subtask-1 is multilingual
                        across editions; "unknown" is the honest default
                        rather than asserting "native")
  - ``use``             ["validation"] — a LIST (manifest_validator hard-
                        ERRORs on a scalar ``use``; matches the
                        mage/raid/editlens precedent).
  - ``privacy``         "shareable" (PAN is redistribution-gated but
                        permissively licensed for research with
                        attribution; "shareable" is the manifest tier,
                        and the harness never re-publishes the text).
  - ``source``          "pan25_voight_kampff"
  - ``source_id``       the PAN instance id
  - ``register``        OMITTED. PAN VK spans genres; no single register
                        value is honest (MAGE precedent).
  - ``notes``           {label, split, source_file}

Usage:

    python3 scripts/calibration/pan_voight_kampff_to_manifest.py \
        --pan-dir /path/to/staged/pan25-vk \
        --split validation \
        --manifest .pan_vk_manifest.jsonl \
        --text-dir .pan_vk_text/
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ---- THE SINGLE EDIT POINT: PAN field map -------------------------------
# If a future PAN edition renames a field, change these tuples and nothing
# else. Keys are tried in order; first present wins.
INSTANCE_ID_KEYS = ("id", "instance_id", "doc_id")
INSTANCE_TEXT_KEYS = ("text", "document", "body")
# A label carried ON the instance row (labeled split, single-file form).
INLINE_LABEL_KEYS = ("label", "is_ai", "is_machine", "is_human", "target")
# A separate truth/label file's id + label fields.
LABEL_ID_KEYS = ("id", "instance_id", "doc_id")
LABEL_KEYS = ("label", "is_ai", "is_machine", "is_human", "target")
# -------------------------------------------------------------------------

# Filenames (substrings, lowercased) that mark the LABEL/TRUTH file vs the
# INSTANCE file, so the adapter can tell them apart in a --pan-dir that
# holds both.
TRUTH_FILE_TOKENS = ("truth", "label", "ground", "gold")

NOTICE_TEXT = """\
# NOTICE — PAN@CLEF Voight-Kampff Subtask-1 (local staging only)

The text files under this directory are derived from the **PAN@CLEF
Generative AI Authorship Verification, Subtask 1 (Voight-Kampff)**
dataset, staged locally by the operator from **Zenodo record 14962653**.

- **DO NOT redistribute.** The PAN dataset is redistribution-gated. This
  directory is a *local* working copy for held-out external validation;
  it is NOT vendored into the repository and MUST NOT be committed or
  shared.
- **Attribution:** PAN@CLEF 2025 Generative AI Authorship Verification.
  Dataset: Zenodo 14962653. Task:
  https://pan.webis.de/clef25/pan25-web/generated-content-analysis.html
- The `setec-voiceprint` benchmark harness reads these files to REPORT
  PAN-metric discrimination scores; it writes only a report artifact and
  re-publishes none of this text.
"""


def _read_rows(source: Path) -> Iterator[dict[str, Any]]:
    """Yield dict rows from a JSONL or CSV file (streaming, BOM-tolerant).

    JSONL: one JSON object per non-blank line. CSV: stdlib
    ``csv.DictReader`` with ``utf-8-sig`` (strips a BOM the way
    ``mage_to_manifest`` does). One row in flight at a time — never holds
    the whole file in memory.
    """
    suffix = source.suffix.lower()
    if suffix in (".jsonl", ".ndjson", ".json"):
        # ``utf-8-sig`` strips a leading BOM transparently; JSONL is read
        # one line at a time for bounded memory.
        with source.open("r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
        return
    if suffix == ".csv":
        try:
            csv.field_size_limit(sys.maxsize)
        except (OverflowError, ValueError):
            csv.field_size_limit(2**31 - 1)
        fh = source.open("r", encoding="utf-8-sig", newline="")
        try:
            reader = csv.DictReader(fh)
            for row in reader:
                yield dict(row)
        finally:
            fh.close()
        return
    raise ValueError(
        f"Unsupported file extension {suffix!r}: {source}. "
        "Expected .jsonl/.ndjson/.json or .csv."
    )


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _normalize_label(raw: Any, *, key: str | None = None) -> int | None:
    """Map a PAN label value to the SETEC binary convention:
    ``0`` = human, ``1`` = machine/AI.

    Handles, per the PAN reference (`load_problem_file`):
      - int / float / numeric-string ``0``/``1`` (0 = human, 1 = machine);
      - a one-hot ``[human_bool, machine_bool]`` list (``label[0]`` true
        => human => 0; ``label[1]`` true => machine => 1);
      - a boolean / "true"/"false" carried under an ``is_human`` /
        ``is_ai`` / ``is_machine`` field (sense inferred from the key
        name).
    Returns ``None`` for an unrecognized / missing label (caller skips).
    """
    if raw is None:
        return None
    # One-hot [human, machine] form (PAN's list label).
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2:
            human_flag = bool(raw[0])
            machine_flag = bool(raw[1])
            if human_flag == machine_flag:
                return None  # not a clean one-hot; skip
            return 0 if human_flag else 1
        return None
    # Boolean / string-bool under a named sense key.
    if isinstance(raw, bool):
        val = 1 if raw else 0
        # An ``is_human`` field inverts the sense (human=true => label 0).
        if key is not None and "human" in key.lower():
            return 0 if raw else 1
        return val
    s = str(raw).strip().lower()
    if s in ("1", "1.0", "true", "ai", "machine", "generated"):
        sense = 1
    elif s in ("0", "0.0", "false", "human"):
        sense = 0
    else:
        try:
            f = float(s)
        except (TypeError, ValueError):
            return None
        sense = 1 if f >= 0.5 else 0
    # ``is_human`` flips the sense.
    if key is not None and "human" in key.lower():
        return 1 - sense
    return sense


def _ai_status_for_label(label: int) -> str:
    """0 (human) -> pre_ai_human; 1 (machine) -> ai_generated."""
    return "pre_ai_human" if label == 0 else "ai_generated"


def _bucketed_text_path(text_dir: Path, row_id: str) -> Path:
    h = hashlib.sha256(row_id.encode("utf-8")).hexdigest()
    return text_dir / h[:2] / h[2:4] / f"{row_id}.txt"


def _is_truth_file(path: Path) -> bool:
    name = path.name.lower()
    return any(tok in name for tok in TRUTH_FILE_TOKENS)


def _load_label_index(truth_files: list[Path]) -> dict[str, int]:
    """Build an ``id -> binary label`` index from separate truth files.

    Streams each truth file; holds only the (id -> small int) index in
    memory, never the text.
    """
    index: dict[str, int] = {}
    for tf in truth_files:
        for row in _read_rows(tf):
            tid = _first_present(row, LABEL_ID_KEYS)
            if tid is None:
                continue
            # Find which label key is present so we can read its sense.
            label_key = next((k for k in LABEL_KEYS if k in row and row[k] is not None), None)
            label = _normalize_label(
                row.get(label_key) if label_key else None,
                key=label_key,
            )
            if label is None:
                continue
            index[str(tid)] = label
    return index


def convert(args: argparse.Namespace) -> int:
    pan_dir = Path(args.pan_dir).expanduser().resolve()
    if not pan_dir.is_dir():
        sys.stderr.write(f"--pan-dir not found: {pan_dir}\n")
        return 1

    all_files = sorted(
        list(pan_dir.rglob("*.jsonl"))
        + list(pan_dir.rglob("*.ndjson"))
        + list(pan_dir.rglob("*.json"))
        + list(pan_dir.rglob("*.csv"))
    )
    if not all_files:
        sys.stderr.write(
            f"No .jsonl/.ndjson/.json/.csv files under {pan_dir}. Stage the "
            "PAN Voight-Kampff release (Zenodo 14962653) there first.\n"
        )
        return 1

    truth_files = [p for p in all_files if _is_truth_file(p)]
    instance_files = [p for p in all_files if not _is_truth_file(p)]
    if not instance_files:
        sys.stderr.write(
            f"No instance files under {pan_dir} (every file looked like a "
            "truth/label file). Expected at least one instance JSONL/CSV.\n"
        )
        return 1

    label_index = _load_label_index(truth_files)

    manifest_path = Path(args.manifest).expanduser().resolve()
    text_dir = Path(args.text_dir).expanduser().resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)

    # Write the NOTICE next to the spilled text (no vendored PAN data).
    (text_dir / "NOTICE.md").write_text(NOTICE_TEXT, encoding="utf-8")

    split = args.split
    n_written = 0
    n_skipped_empty = 0
    n_skipped_no_label = 0

    with manifest_path.open("w", encoding="utf-8") as fh_out:
        for source_file in instance_files:
            for row in _read_rows(source_file):
                if args.limit and n_written >= args.limit:
                    break
                text = _first_present(row, INSTANCE_TEXT_KEYS)
                if not isinstance(text, str) or not text.strip():
                    n_skipped_empty += 1
                    continue
                inst_id = _first_present(row, INSTANCE_ID_KEYS)
                if inst_id is None:
                    # Stable fallback id from the text hash so we never
                    # collide silently.
                    inst_id = hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest()[:16]
                inst_id = str(inst_id)

                # Label: inline on the instance row, else from the truth
                # index. An unlabeled instance is skipped (the harness
                # needs gold labels to score).
                inline_key = next(
                    (k for k in INLINE_LABEL_KEYS if k in row and row[k] is not None),
                    None,
                )
                if inline_key is not None:
                    label = _normalize_label(row.get(inline_key), key=inline_key)
                else:
                    label = label_index.get(inst_id)
                if label is None:
                    n_skipped_no_label += 1
                    continue

                row_id = f"pan_vk_{split}_{inst_id}"
                text_path = _bucketed_text_path(text_dir, row_id)
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(text, encoding="utf-8")

                entry = {
                    "id": row_id,
                    "path": str(text_path.relative_to(manifest_path.parent)),
                    "ai_status": _ai_status_for_label(label),
                    "editing_status": "raw_draft",
                    "language_status": "unknown",
                    # ``use`` is LIST-typed (manifest_validator hard-ERRORs
                    # on a scalar). Matches mage/raid/editlens precedent.
                    "use": ["validation"],
                    "privacy": "shareable",
                    "source": "pan25_voight_kampff",
                    "source_id": inst_id,
                    "notes": {
                        "label": label,
                        "split": split,
                        "source_file": source_file.name,
                    },
                }
                fh_out.write(json.dumps(entry, default=str) + "\n")
                n_written += 1
            if args.limit and n_written >= args.limit:
                break

    sys.stdout.write(
        f"Wrote {n_written} manifest entries to {manifest_path}\n"
        f"  Text spilled to {text_dir}\n"
        f"  NOTICE.md written (no vendored PAN data)\n"
        f"  Skipped: {n_skipped_empty} empty, {n_skipped_no_label} no-label\n"
        f"  Label sources: {len(truth_files)} truth file(s), "
        f"{len(instance_files)} instance file(s)\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a locally-staged PAN Voight-Kampff Subtask-1 release "
            "(Zenodo 14962653) into a SETEC manifest slice. Vendors no PAN "
            "data; writes a NOTICE.md next to the spilled text."
        )
    )
    parser.add_argument(
        "--pan-dir", required=True,
        help="Directory holding the locally-staged PAN VK release files.",
    )
    parser.add_argument(
        "--split", default="validation",
        help="Split tag recorded in the manifest ids/notes (default: validation).",
    )
    parser.add_argument(
        "--manifest", default=".pan_vk_manifest.jsonl",
        help="Output manifest JSONL path (default: .pan_vk_manifest.jsonl).",
    )
    parser.add_argument(
        "--text-dir", default=".pan_vk_text",
        help="Output text-spill directory (default: .pan_vk_text/).",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Stop after N manifest entries (smoke-test). Default 0 = no limit.",
    )
    return convert(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
