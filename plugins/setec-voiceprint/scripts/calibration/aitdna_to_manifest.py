#!/usr/bin/env python3
"""aitdna_to_manifest.py — convert the AITDNA benchmark release into SETEC
manifest slices for the AITDNA external-validation harness.

Companion to ``aitdna_benchmark.py`` (the harness) and a sibling of
``pan_voight_kampff_to_manifest.py`` / ``mage_to_manifest.py`` /
``raid_to_manifest.py``. AITDNA (*'Your AI Text is not Mine': Redefining and
Evaluating AI-generated Text Detection under Realistic Assumptions*; Dycke,
Sakharova, Daheim, Gurevych — **arXiv:2606.04906**) is a public benchmark of
realistic **human-AI co-written** text: HF ``datasets/UKPLab/AITDNA``,
license **CC-BY-SA-4.0**.

--------------------------------------------------------------------------
ONE-WAY LABEL FLOW (anti-Goodhart). This adapter computes the per-notion
GOLD label ``M_d`` from AITDNA's own genesis annotations under **declared,
fixed, never-swept** constants (τ / co-written / n / p — see below), and
writes a SETEC manifest. It reads NO detector output and fits NOTHING. The
labels flow AITDNA -> adapter -> manifest -> harness, never back.
--------------------------------------------------------------------------

AITDNA real schema (verified on the Hub 2026-07-05,
``UKPLab/AITDNA`` — one config per notion, split ``test``, 362 rows each):

  - **``data``** — a LIST of segments, each ``{text, author, queries}``.
    * In the ``token`` / ``membership`` configs each segment is ONE token,
      so ``data`` is the per-token genesis stream: ``author`` labels each
      token as ``"User"`` (human) or ``"Bot"`` (AI-suggested).
    * In the ``document`` config ``data`` is one whole-document segment
      whose ``author`` is the dominant author; the per-token provenance is
      only recoverable from the token-level configs.
    The document TEXT is the concatenation of the segment ``text`` fields
    (token configs join on whitespace; a whole-document segment is the
    text as-is).
  - **``metadata``** — ``{author, human_only, model, temperature, setting,
    task}``. ``human_only`` (bool) is the clean provenance flag: True iff
    the text was produced with no AI suggestion accepted. The 95-strong
    human-only reference subset is exactly ``human_only == True``.

Notion label (the GOLD ``M_d`` this adapter computes), all constants FIXED:

  - **Document-level** (τ = ``DOC_TAU`` = 0.5): a doc is AI iff its
    AI-token ratio (Bot tokens / total tokens, from the genesis stream) is
    **strictly greater than** τ. Requires a token-level config
    (``--config token`` or ``membership``); a whole-document ``document``
    config carries no per-token provenance, so the adapter falls back to
    the ``metadata.human_only`` flag (human_only True -> human -> 0;
    else 1) and records ``label_basis`` so the honest source is legible.
  - **Co-written** (a hard-coded rule, ``CO_WRITTEN`` needs BOTH): a doc is
    co-written iff its genesis stream contains **>= 1 human token AND >= 1
    AI token**. Co-written FPR is a first-class harness report cell.
  - **Membership-based** and **authorship-ID-based** notion labels are NOT
    a per-token τ threshold; they are computed by the harness against the
    fixed reference corpus (n = 4-gram overlap, p = 5th percentile) and are
    reported as distributions where a per-doc binary label needs an
    operator operating point — see ``aitdna_benchmark.py`` / the spec's
    honest-gap ``notion_coverage`` posture. This adapter emits the manifest
    + the reference-corpus slice they consume.

**Field-map = the single edit point (§3b).** If a future AITDNA edition
renames a field, edit ``SEGMENT_*_KEYS`` / ``META_*_KEYS`` /
``HUMAN_AUTHOR_TOKENS`` / ``AI_AUTHOR_TOKENS`` below and nothing else.

The dataset **download is an out-of-M1 seam**: this adapter reads whatever
the operator staged locally (parquet/JSONL, e.g. from ``fetch_aitdna.py``);
it vendors NO AITDNA text and writes a ``NOTICE.md`` (CC-BY-SA-4.0
attribution + share-alike + redistribution posture) next to the spilled
text.

Manifest mapping (aligned with ``manifest_validator.ALLOWED_*``):

  - ``id``              ``aitdna_<config>_<row index>``
  - ``path``            relative path under --text-dir to the spilled text
  - ``ai_status``       "pre_ai_human" if label == 0 (human);
                        "ai_generated" if label == 1 (AI).
  - ``editing_status``  "coauthored" for a co-written doc, else "raw_draft".
  - ``language_status`` "unknown" (AITDNA is en, but writer nativeness is
                        not recorded — "unknown" is the honest default).
  - ``use``             ["validation"] — a LIST (manifest_validator hard-
                        ERRORs on a scalar ``use``; mage/raid/pan precedent).
  - ``privacy``         "shareable" (CC-BY-SA-4.0; the harness re-publishes
                        no text — a fetch-only, report-only harness is
                        unaffected by share-alike).
  - ``source``          "aitdna"
  - ``source_id``       the AITDNA row id (``<config>_<index>``)
  - ``notes``           {label, label_basis, co_written, human_only,
                        ai_token_ratio, n_tokens, config, model, task,
                        setting, author}

Usage:

    python3 scripts/calibration/aitdna_to_manifest.py \
        --aitdna-dir /path/to/staged/aitdna \
        --config token \
        --manifest .aitdna_manifest.jsonl \
        --reference-manifest .aitdna_reference.jsonl \
        --text-dir .aitdna_text/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ---- FIXED, NEVER-SWEPT notion constants (guarded by
# ``test_notion_parameters_fixed``). These are the declared decisions from
# the spec §0 — they are module constants, read from NOTHING, and the guard
# asserts no ``swept_parameter`` / ``optimal_*`` field is ever emitted.
DOC_TAU = 0.5          # document-level: AI iff Bot-token ratio > 0.5
CO_WRITTEN_MIN = 1     # co-written iff >= 1 human token AND >= 1 AI token
MEMBERSHIP_NGRAM = 4   # membership: n-gram overlap unit (harness-side)
MEMBERSHIP_PERCENTILE = 5  # membership: fixed p-th self-overlap percentile

REFERENCE_CORPUS_NAME = "AITDNA human-only subset"
REFERENCE_CORPUS_LICENSE = "CC-BY-SA-4.0"

# ---- THE SINGLE EDIT POINT: AITDNA field map (§3b) ----------------------
# If a future AITDNA edition renames a field, change these tuples and
# nothing else. Keys are tried in order; first present wins.
#
# Row shape: {"data": [{text, author, queries}, ...], "metadata": {...}}.
INSTANCE_DATA_KEYS = ("data", "segments", "tokens")
SEGMENT_TEXT_KEYS = ("text", "token", "content")
SEGMENT_AUTHOR_KEYS = ("author", "role", "source")
INSTANCE_META_KEYS = ("metadata", "meta")
META_HUMAN_ONLY_KEYS = ("human_only", "is_human_only", "human")
META_AUTHOR_KEYS = ("author", "writer", "user")
META_MODEL_KEYS = ("model", "llm", "generator")
META_TASK_KEYS = ("task", "genre", "prompt_type")
META_SETTING_KEYS = ("setting", "condition")
# Genesis author-label vocabularies: which ``author`` strings mean human
# vs AI. Matched case-insensitively; first match wins.
HUMAN_AUTHOR_TOKENS = ("user", "human", "writer")
AI_AUTHOR_TOKENS = ("bot", "ai", "model", "assistant", "llm")
# -------------------------------------------------------------------------

NOTICE_TEXT = """\
# NOTICE — AITDNA (local staging only)

The text files under this directory are derived from the **AITDNA**
dataset (*'Your AI Text is not Mine': Redefining and Evaluating
AI-generated Text Detection under Realistic Assumptions*; Dycke,
Sakharova, Daheim, Gurevych — arXiv:2606.04906), staged locally by the
operator from **HF `datasets/UKPLab/AITDNA`**.

- **License: CC-BY-SA-4.0** (Creative Commons Attribution-ShareAlike 4.0).
  https://creativecommons.org/licenses/by-sa/4.0/
- **Attribution:** UKPLab / AITDNA, arXiv:2606.04906,
  https://huggingface.co/datasets/UKPLab/AITDNA
- **Share-alike:** any redistributed adaptation of this text must carry the
  same CC-BY-SA-4.0 license. This directory is a *local* working copy for
  held-out external validation; it is NOT vendored into the repository and
  MUST NOT be committed. The `setec-voiceprint` benchmark harness reads
  these files to REPORT discrimination scores; it writes only a report
  artifact and re-publishes none of this text (a fetch-only, report-only
  harness is unaffected by share-alike).
"""


def _read_rows(source: Path) -> Iterator[dict[str, Any]]:
    """Yield dict rows from a JSONL/JSON or Parquet file (streaming).

    JSONL: one JSON object per non-blank line (BOM-tolerant). Parquet:
    read via ``pyarrow`` batch iteration (an out-of-stdlib dep already
    used by the fetch/calibration path; requirements-calibration.txt). One
    row in flight at a time — never holds the whole file in memory.
    """
    suffix = source.suffix.lower()
    if suffix in (".jsonl", ".ndjson", ".json"):
        with source.open("r", encoding="utf-8-sig") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj
                elif isinstance(obj, list):
                    # A top-level JSON array (a .json export of the whole
                    # split) — yield each object element.
                    for el in obj:
                        if isinstance(el, dict):
                            yield el
        return
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise SystemExit(
                "Reading a .parquet AITDNA export needs pyarrow: "
                "pip install -r requirements-calibration.txt"
            ) from exc
        pf = pq.ParquetFile(str(source))
        for batch in pf.iter_batches():
            for row in batch.to_pylist():
                if isinstance(row, dict):
                    yield row
        return
    raise ValueError(
        f"Unsupported file extension {suffix!r}: {source}. "
        "Expected .jsonl/.ndjson/.json or .parquet."
    )


def _first_present(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _classify_author(author: Any) -> str | None:
    """Map a segment ``author`` string to ``"H"`` / ``"AI"`` / None.

    Case-insensitive substring match against the field-map vocabularies.
    An unrecognized author string returns None (counted, never guessed).
    """
    if not isinstance(author, str):
        return None
    a = author.strip().lower()
    if not a:
        return None
    for tok in HUMAN_AUTHOR_TOKENS:
        if tok in a:
            return "H"
    for tok in AI_AUTHOR_TOKENS:
        if tok in a:
            return "AI"
    return None


def _segments(row: dict[str, Any]) -> list[dict[str, Any]]:
    data = _first_present(row, INSTANCE_DATA_KEYS)
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict)]


def _join_text(segments: list[dict[str, Any]]) -> str:
    """Reconstruct the document text from segment ``text`` fields.

    Token-level configs (one token per segment) join on whitespace; a
    single whole-document segment yields its text as-is. Both collapse to
    the same rule: join non-empty segment texts with a single space, then
    strip — a whitespace-tolerant reconstruction that is stable across the
    token / document configs.
    """
    parts = []
    for s in segments:
        t = _first_present(s, SEGMENT_TEXT_KEYS)
        if isinstance(t, str) and t:
            parts.append(t)
    if len(parts) == 1:
        return parts[0].strip()
    return " ".join(p.strip() for p in parts if p.strip()).strip()


def _genesis_counts(segments: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return (n_human, n_ai, n_unknown) segment/token counts from the
    genesis ``author`` labels. Whole-document configs collapse to one
    segment, so counts there are 1/0 or 0/1 (or 0/0/1 if unrecognized)."""
    n_h = n_ai = n_unk = 0
    for s in segments:
        cls = _classify_author(_first_present(s, SEGMENT_AUTHOR_KEYS))
        if cls == "H":
            n_h += 1
        elif cls == "AI":
            n_ai += 1
        else:
            n_unk += 1
    return n_h, n_ai, n_unk


def _meta_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return None


def compute_notion_label(
    row: dict[str, Any],
) -> dict[str, Any]:
    """Compute the document-level GOLD notion label ``M_d`` for one AITDNA
    row under the FIXED constants (τ = ``DOC_TAU``; co-written =
    ``CO_WRITTEN_MIN`` on each side). Returns a dict:

      {label, label_basis, co_written, human_only, ai_token_ratio,
       n_tokens, n_human_tokens, n_ai_tokens, text}

    ``label`` is 0 (human) / 1 (AI) / None (not scorable). ``label_basis``
    names the honest source: ``"genesis_ratio_tau"`` when per-token
    provenance is available, ``"metadata_human_only"`` when only the
    whole-document ``human_only`` flag is (the ``document`` config), or a
    ``not_scorable:<reason>`` string. NO constant is ever read from a
    sweep, config, or the row.
    """
    segments = _segments(row)
    text = _join_text(segments)
    meta = _first_present(row, INSTANCE_META_KEYS)
    meta = meta if isinstance(meta, dict) else {}
    human_only = _meta_bool(_first_present(meta, META_HUMAN_ONLY_KEYS))

    n_h, n_ai, n_unk = _genesis_counts(segments)
    n_labeled = n_h + n_ai

    result: dict[str, Any] = {
        "text": text,
        "human_only": human_only,
        "n_human_tokens": n_h,
        "n_ai_tokens": n_ai,
        "n_tokens": n_labeled,
        "co_written": bool(n_h >= CO_WRITTEN_MIN and n_ai >= CO_WRITTEN_MIN),
        "ai_token_ratio": None,
        "label": None,
        "label_basis": None,
    }

    if not text:
        result["label_basis"] = "not_scorable:empty_text"
        return result

    # Per-token genesis provenance available (token / membership config):
    # document-level label = (Bot ratio > τ). This is the notion's own
    # segmentation function, applied with the FIXED τ.
    if n_labeled >= 2 or (n_labeled == 1 and n_unk == 0):
        # A meaningful per-token stream (more than a single whole-document
        # segment). A single labeled segment with no unknowns is a
        # whole-document config whose one segment IS the provenance.
        if n_labeled >= 2:
            ratio = n_ai / n_labeled
            result["ai_token_ratio"] = round(ratio, 6)
            result["label"] = 1 if ratio > DOC_TAU else 0
            result["label_basis"] = "genesis_ratio_tau"
            return result

    # Whole-document config (one segment) or an all-unknown genesis stream:
    # fall back to the metadata ``human_only`` flag, recording the honest
    # basis. human_only True -> human -> 0; human_only False -> AI -> 1.
    if human_only is not None:
        result["label"] = 0 if human_only else 1
        result["label_basis"] = "metadata_human_only"
        # A whole-document AI doc is not "co-written" by the token rule
        # (no per-token stream), so co_written stays False here — honest.
        return result

    result["label_basis"] = "not_scorable:no_genesis_and_no_human_only"
    return result


def _ai_status_for_label(label: int) -> str:
    """0 (human) -> pre_ai_human; 1 (AI) -> ai_generated."""
    return "pre_ai_human" if label == 0 else "ai_generated"


def reference_provenance() -> dict[str, Any]:
    """The fixed reference-corpus provenance block (spec §0 P1-1). The
    membership/authorship-ID reference is AITDNA's own published
    human-only subset — pre-specified, immutable, chosen BEFORE any AITDNA
    metric is computed so it cannot be retro-tuned to results."""
    return {
        "name": REFERENCE_CORPUS_NAME,
        "expected_size": 95,
        "license": REFERENCE_CORPUS_LICENSE,
        "selection": "metadata.human_only == True",
        "precedence": (
            "Fixed before any AITDNA detector metric is computed; the "
            "reference corpus is AITDNA's own published human-only subset "
            "(immutable, part of the public CC-BY-SA-4.0 release), so it "
            "cannot be retro-tuned to results. If a voiceprint corpus is "
            "ever substituted it must be pinned to a fixed commit/tag with "
            "the same provenance block."
        ),
        "notion_constants": {
            "doc_tau": DOC_TAU,
            "co_written_min_each_side": CO_WRITTEN_MIN,
            "membership_ngram_n": MEMBERSHIP_NGRAM,
            "membership_percentile_p": MEMBERSHIP_PERCENTILE,
        },
    }


def convert(args: argparse.Namespace) -> int:
    aitdna_dir = Path(args.aitdna_dir).expanduser().resolve()
    if not aitdna_dir.is_dir():
        sys.stderr.write(f"--aitdna-dir not found: {aitdna_dir}\n")
        return 1

    # Prefer files whose basename matches the requested config; fall back
    # to every data file if none match (a single-file staging).
    all_files = sorted(
        list(aitdna_dir.rglob("*.parquet"))
        + list(aitdna_dir.rglob("*.jsonl"))
        + list(aitdna_dir.rglob("*.ndjson"))
        + list(aitdna_dir.rglob("*.json"))
    )
    if not all_files:
        sys.stderr.write(
            f"No .parquet/.jsonl/.ndjson/.json files under {aitdna_dir}. "
            "Stage the AITDNA release (HF UKPLab/AITDNA) there first.\n"
        )
        return 1

    config = args.config
    config_files = [p for p in all_files if config in p.name.lower()]
    source_files = config_files or all_files

    manifest_path = Path(args.manifest).expanduser().resolve()
    reference_path = (
        Path(args.reference_manifest).expanduser().resolve()
        if args.reference_manifest else None
    )
    text_dir = Path(args.text_dir).expanduser().resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "NOTICE.md").write_text(NOTICE_TEXT, encoding="utf-8")

    n_written = 0
    n_skipped_not_scorable = 0
    n_co_written = 0
    n_reference = 0
    reference_rows: list[dict[str, Any]] = []

    ref_fh = (
        reference_path.open("w", encoding="utf-8") if reference_path else None
    )
    try:
        with manifest_path.open("w", encoding="utf-8") as fh_out:
            row_index = 0
            for source_file in source_files:
                for row in _read_rows(source_file):
                    if args.limit and n_written >= args.limit:
                        break
                    idx = row_index
                    row_index += 1
                    nl = compute_notion_label(row)
                    if nl["label"] is None:
                        n_skipped_not_scorable += 1
                        continue
                    text = nl["text"]
                    source_id = f"{config}_{idx}"
                    row_id = f"aitdna_{source_id}"
                    text_path = _bucketed_text_path(text_dir, row_id)
                    text_path.parent.mkdir(parents=True, exist_ok=True)
                    text_path.write_text(text, encoding="utf-8")

                    meta = _first_present(row, INSTANCE_META_KEYS) or {}
                    meta = meta if isinstance(meta, dict) else {}
                    entry = {
                        "id": row_id,
                        "path": str(text_path.relative_to(manifest_path.parent)),
                        "ai_status": _ai_status_for_label(nl["label"]),
                        "editing_status": (
                            "coauthored" if nl["co_written"] else "raw_draft"
                        ),
                        "language_status": "unknown",
                        "use": ["validation"],
                        "privacy": "shareable",
                        "source": "aitdna",
                        "source_id": source_id,
                        "notes": {
                            "label": nl["label"],
                            "label_basis": nl["label_basis"],
                            "co_written": nl["co_written"],
                            "human_only": nl["human_only"],
                            "ai_token_ratio": nl["ai_token_ratio"],
                            "n_tokens": nl["n_tokens"],
                            "config": config,
                            "model": _first_present(meta, META_MODEL_KEYS),
                            "task": _first_present(meta, META_TASK_KEYS),
                            "setting": _first_present(meta, META_SETTING_KEYS),
                            "author": _first_present(meta, META_AUTHOR_KEYS),
                        },
                    }
                    fh_out.write(json.dumps(entry, default=str) + "\n")
                    n_written += 1
                    if nl["co_written"]:
                        n_co_written += 1

                    # The human-only reference corpus: exactly the docs
                    # whose provenance is clean human (metadata.human_only).
                    if ref_fh is not None and nl["human_only"] is True:
                        ref_entry = {
                            "id": row_id,
                            "text_path": str(
                                text_path.relative_to(reference_path.parent)
                            ),
                            "source": "aitdna_human_only",
                        }
                        ref_fh.write(json.dumps(ref_entry, default=str) + "\n")
                        n_reference += 1
                if args.limit and n_written >= args.limit:
                    break
    finally:
        if ref_fh is not None:
            ref_fh.close()

    # Write the reference-provenance sidecar next to the reference manifest
    # (or the manifest) so it is always co-located with the labels.
    prov_target = (reference_path or manifest_path).with_suffix(
        ".provenance.json"
    )
    prov_block = reference_provenance()
    prov_block["observed_reference_size"] = n_reference
    prov_target.write_text(
        json.dumps(prov_block, indent=2) + "\n", encoding="utf-8"
    )

    sys.stdout.write(
        f"Wrote {n_written} manifest entries to {manifest_path}\n"
        f"  Text spilled to {text_dir}\n"
        f"  NOTICE.md written (CC-BY-SA-4.0; no vendored AITDNA data)\n"
        f"  Co-written docs: {n_co_written}\n"
        f"  Skipped: {n_skipped_not_scorable} not-scorable\n"
        + (
            f"  Reference (human-only) corpus: {n_reference} entries -> "
            f"{reference_path}\n"
            if reference_path else ""
        )
        + f"  Reference provenance: {prov_target}\n"
    )
    return 0


def _bucketed_text_path(text_dir: Path, row_id: str) -> Path:
    import hashlib

    h = hashlib.sha256(row_id.encode("utf-8")).hexdigest()
    return text_dir / h[:2] / h[2:4] / f"{row_id}.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a locally-staged AITDNA release (HF UKPLab/AITDNA, "
            "CC-BY-SA-4.0) into a SETEC manifest slice + a human-only "
            "reference-corpus slice for the AITDNA external-validation "
            "harness. Vendors no AITDNA data; writes a NOTICE.md + a "
            "reference-provenance sidecar. Computes the GOLD notion label "
            "under FIXED constants (τ=0.5; co-written); labels flow one way."
        )
    )
    parser.add_argument(
        "--aitdna-dir", required=True,
        help="Directory holding the locally-staged AITDNA release files.",
    )
    parser.add_argument(
        "--config", default="token",
        help=(
            "Which AITDNA notion config to convert (default: token). The "
            "token/membership configs carry per-token genesis provenance "
            "for the τ document-level label; the document config falls "
            "back to metadata.human_only (label_basis records which)."
        ),
    )
    parser.add_argument(
        "--manifest", default=".aitdna_manifest.jsonl",
        help="Output manifest JSONL path (default: .aitdna_manifest.jsonl).",
    )
    parser.add_argument(
        "--reference-manifest", default=".aitdna_reference.jsonl",
        help=(
            "Output human-only reference-corpus manifest (the membership/"
            "authorship-ID reference). Default: .aitdna_reference.jsonl. "
            "Pass empty to skip."
        ),
    )
    parser.add_argument(
        "--text-dir", default=".aitdna_text",
        help="Output text-spill directory (default: .aitdna_text/).",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Stop after N manifest entries (smoke-test). Default 0 = no limit.",
    )
    args = parser.parse_args(argv)
    if args.reference_manifest == "":
        args.reference_manifest = None
    return convert(args)


if __name__ == "__main__":
    sys.exit(main())
