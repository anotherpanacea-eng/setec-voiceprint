#!/usr/bin/env python3
"""
check_corpus.py

Content-level corpus hygiene gate.

The manifest validator checks schema and provenance. This script checks
whether the files themselves contain suspected non-prose contamination
that would distort POS/KL and other distributional diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from manifest_validator import resolve_path, validate_manifest
from output_schema import build_output  # type: ignore
from preprocessing import available_rule_names, strip_non_prose


TASK_SURFACE = "validation"
TOOL_NAME = "check_corpus"
SCRIPT_VERSION = "1.0"
DEFAULT_WARN_THRESHOLD = 0.01
DEFAULT_FAIL_THRESHOLD = 0.05

# Above this many input files, the single-process iteration this
# script does becomes the wrong tool for the job: NTFS small-file
# open latency + no parallelism push wall-clock into many hours
# on corpora at the scale of RAID (~8M files). The sharded path
# via ``shard_runner --task corpus_hygiene`` reuses the same
# scoring logic with workers, state.json checkpointing, and
# multi-host coordination — typically 10-30× faster end-to-end.
# The threshold is intentionally generous: MAGE-scale (~436K
# files) completes in ~30 min single-process and doesn't warrant
# the sharded ceremony; an order of magnitude above that is where
# the trade-off flips. Tuned for the practical complaint, not for
# theoretical optimality.
LARGE_MANIFEST_WARN_THRESHOLD = 1_000_000


class CorpusCheckError(Exception):
    """Raised for invalid input sources or unreadable corpus files."""


def md_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def parse_filter(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    out: dict[str, str] = {}
    for raw in value.split(","):
        part = raw.strip()
        if not part:
            continue
        if "=" not in part:
            raise CorpusCheckError(
                f"Manifest filter '{part}' is not field=value syntax."
            )
        key, expected = part.split("=", 1)
        key = key.strip()
        expected = expected.strip()
        if not key or not expected:
            raise CorpusCheckError(
                f"Manifest filter '{part}' is not field=value syntax."
            )
        out[key] = expected
    return out


def matches_filter(value: Any, expected: str) -> bool:
    if isinstance(value, list):
        return expected in {str(v) for v in value}
    return str(value) == expected


def paths_from_dir(directory: str | Path) -> list[Path]:
    base = Path(directory)
    if not base.exists():
        raise CorpusCheckError(f"Directory '{base}' does not exist.")
    if not base.is_dir():
        raise CorpusCheckError(f"Path '{base}' is not a directory.")
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    return [
        path for path in paths
        if not path.name.startswith(".")
        and not path.name.lower().startswith("readme")
    ]


def paths_from_manifest(manifest_path: str | Path, filter_text: str | None) -> list[Path]:
    manifest = Path(manifest_path)
    validation = validate_manifest(manifest)
    if validation.get("n_errors", 0):
        messages = "; ".join(
            issue.get("message", "")
            for issue in validation.get("issues", [])
            if issue.get("severity") == "error"
        )
        raise CorpusCheckError(
            "Manifest has validation errors; refusing to check corpus. "
            + (messages or "Run manifest_validator.py for details.")
        )
    filters = parse_filter(filter_text)
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CorpusCheckError(f"Could not read manifest '{manifest}': {exc}.") from exc

    out: list[Path] = []
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusCheckError(
                f"Malformed JSON on manifest line {lineno}: {exc.msg}."
            ) from exc
        if not isinstance(entry, dict):
            raise CorpusCheckError(f"Manifest line {lineno} is not a JSON object.")
        if any(not matches_filter(entry.get(k), v) for k, v in filters.items()):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CorpusCheckError(f"Manifest line {lineno} is missing a path.")
        out.append(resolve_path(manifest, raw_path))
    if not out:
        raise CorpusCheckError("Manifest filters matched no files.")
    return out


def collect_paths(
    *,
    paths: list[str] | None = None,
    dirs: list[str] | None = None,
    manifest: str | None = None,
    filter_text: str | None = None,
) -> list[Path]:
    collected: list[Path] = []
    for raw in paths or []:
        collected.append(Path(raw))
    for raw_dir in dirs or []:
        collected.extend(paths_from_dir(raw_dir))
    if manifest:
        collected.extend(paths_from_manifest(manifest, filter_text))

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in collected:
        try:
            key = path.expanduser().resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    if not unique:
        raise CorpusCheckError("No corpus files were supplied.")
    return unique


def warn_if_large_manifest(
    n_files: int,
    manifest: str | None,
    *,
    threshold: int = LARGE_MANIFEST_WARN_THRESHOLD,
    out: Any = None,
) -> bool:
    """If the input is large enough that the sharded path is the
    right tool, print a stderr discoverability warning and return
    True. Returns False when the input is below threshold (no
    warning) or when no manifest was supplied (the sharded
    workflow needs a manifest input; `--path` / `--dir` aren't
    addressable that way).

    The ``out`` parameter is for tests; defaults to ``sys.stderr``.
    """
    if out is None:
        out = sys.stderr
    if n_files < threshold:
        return False
    if not manifest:
        return False
    # ``shlex.quote`` so the manifest path is a copy-pasteable
    # POSIX-shell token even when it contains spaces, ampersands,
    # parentheses, or other characters the shell treats specially.
    # Without this the recipe fails for exactly the operator the
    # warning is trying to help — RAID-scale corpora often live
    # under user-named directories with spaces (e.g., today's
    # workspace at ``C:\Users\Joshua\Documents\Claude Cowork
    # Working Folder\...``).
    #
    # Codex P2 on PR #52. Quoted with POSIX shell rules; if the
    # operator is on cmd.exe rather than bash they may still need
    # to adjust the quoting style, but bash / WSL / pwsh-with-bash
    # is the documented host for the sharded workflow anyway
    # (RUNBOOK_corpus_hygiene_sharded.md).
    manifest_arg = shlex.quote(str(manifest))
    out.write(
        f"\n  warning: {n_files:,} input files matched. Single-process "
        f"check_corpus at this scale typically runs for many hours\n"
        f"  on Windows (NTFS small-file open latency dominates).\n"
        f"\n"
        f"  Consider the sharded path, which reuses the same scoring\n"
        f"  logic via shard_runner --task corpus_hygiene with workers\n"
        f"  and state.json checkpointing:\n"
        f"\n"
        f"    shard_runner shard --task corpus_hygiene \\\n"
        f"        --source-manifest {manifest_arg} \\\n"
        f"        --run-id <YOUR_RUN_ID>\n"
        f"    shard_runner work --task corpus_hygiene \\\n"
        f"        --run-id <YOUR_RUN_ID> --workers 8\n"
        f"    shard_runner aggregate --task corpus_hygiene \\\n"
        f"        --run-id <YOUR_RUN_ID> --out hygiene_report.json\n"
        f"\n"
        f"  See plugins/setec-voiceprint/scripts/calibration/"
        f"RUNBOOK_corpus_hygiene_sharded.md for details.\n"
        f"\n"
        f"  Continuing with single-process check_corpus anyway...\n\n"
    )
    out.flush()
    return True


def classify_file(strip_ratio: float, warn_threshold: float, fail_threshold: float) -> str:
    if strip_ratio >= fail_threshold:
        return "fail"
    if strip_ratio >= warn_threshold:
        return "warning"
    return "clean"


def check_path(
    path: Path,
    *,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
    collect_stripped: bool = False,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return {
            "path": str(path),
            "status": "error",
            "error": str(exc),
            "input_tokens_before": 0,
            "input_tokens_after": 0,
            "tokens_stripped": 0,
            "tokens_stripped_by_rule": {},
            "strip_ratio": 0.0,
            "dominant_rule": None,
        }
    try:
        _cleaned, meta = strip_non_prose(
            text,
            strip_rules,
            strip_aggressive=strip_aggressive,
            collect_stripped=collect_stripped,
        )
    except ValueError as exc:
        return {
            "path": str(path),
            "status": "error",
            "error": str(exc),
            "input_tokens_before": 0,
            "input_tokens_after": 0,
            "tokens_stripped": 0,
            "tokens_stripped_by_rule": {},
            "strip_ratio": 0.0,
            "dominant_rule": None,
        }
    ratio = float(meta.get("strip_ratio", 0.0) or 0.0)
    meta["path"] = str(path)
    meta["status"] = classify_file(ratio, warn_threshold, fail_threshold)
    meta["error"] = None
    return meta


def score_manifest_rows(
    shard_manifest_path: Path,
    *,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
    collect_stripped: bool = False,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-path hygiene scoring for the shard_runner corpus_hygiene
    task surface.

    Reads a JSON-lines shard manifest where each row carries a
    ``path`` field (resolved relative to the manifest's parent
    directory, matching ``paths_from_manifest``'s contract). For
    every row, runs :func:`check_path` and emits one record. The
    returned summary dict has the per-shard aggregate counts a
    multi-shard aggregator can fold together.

    Lifted out of :func:`check_corpus_paths` so the sharded
    hygiene scorer reuses the per-path checking loop without
    re-implementing path resolution or status classification. The
    single-process :func:`check_corpus_paths` retains its
    historical signature by delegating here.
    """
    manifest = Path(shard_manifest_path)
    records: list[dict[str, Any]] = []

    def _error_record(
        lineno: int, raw_line: str, msg: str, *, row_id: Any = None,
    ) -> dict[str, Any]:
        """Build the same error-shape record ``check_path`` emits
        for unreadable files, so the aggregator's existing
        ``n_error`` counter picks it up and the operator sees the
        bad row in the report rather than getting silent under-
        counting."""
        rec: dict[str, Any] = {
            "path": f"<manifest line {lineno}>",
            "status": "error",
            "error": msg,
            "input_tokens_before": 0,
            "input_tokens_after": 0,
            "tokens_stripped": 0,
            "tokens_stripped_by_rule": {},
            "strip_ratio": 0.0,
            "dominant_rule": None,
            "manifest_path": str(manifest),
            "manifest_lineno": lineno,
            "raw_line_excerpt": raw_line[:200],
        }
        if row_id is not None:
            rec["id"] = row_id
        return rec

    with manifest.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                # Surface as an error record rather than silently
                # skipping. Hygiene gates must be loud about
                # broken inputs — silent skipping caused under-
                # counting that the aggregator's "n_clean ==
                # n_files" check would have missed entirely.
                records.append(_error_record(
                    lineno, line,
                    f"Malformed JSON: {exc.msg}",
                ))
                continue
            if not isinstance(entry, dict):
                records.append(_error_record(
                    lineno, line,
                    "Manifest row is not a JSON object.",
                ))
                continue
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                records.append(_error_record(
                    lineno, line,
                    "Manifest row is missing a non-empty `path`.",
                    # Match the success-path logic at lines ~387-389:
                    # accept the manifest's identifier under either
                    # `text_id` or `id` (manifests use `text_id` in
                    # practice; `id` is the legacy / shorter alias).
                    # Without this fallback, the error record drops
                    # the identifier and the aggregator can't join
                    # back to the source manifest row.
                    row_id=entry.get("text_id") or entry.get("id"),
                ))
                continue
            target = resolve_path(manifest, raw_path)
            record = check_path(
                target,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
                collect_stripped=collect_stripped,
                warn_threshold=warn_threshold,
                fail_threshold=fail_threshold,
            )
            # Carry the manifest text_id (or id) through so the
            # aggregator can join back to the source manifest.
            for key in ("text_id", "id"):
                if key in entry and key not in record:
                    record[key] = entry[key]
            records.append(record)
    summary = _summarize_hygiene_records(
        records,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )
    return records, summary


def _summarize_hygiene_records(
    records: list[dict[str, Any]],
    *,
    warn_threshold: float,
    fail_threshold: float,
) -> dict[str, Any]:
    """Roll a list of per-path hygiene records into the aggregate
    shape :func:`check_corpus_paths` returns.

    Pulled out as a helper so the per-shard scorer and the
    cross-shard aggregator (in task_surfaces.py) share one
    implementation of the rollup logic.
    """
    counts = Counter(record["status"] for record in records)
    tokens_before = sum(
        int(r.get("input_tokens_before", 0) or 0) for r in records
    )
    tokens_after = sum(
        int(r.get("input_tokens_after", 0) or 0) for r in records
    )
    by_rule: Counter[str] = Counter()
    for record in records:
        by_rule.update(record.get("tokens_stripped_by_rule") or {})
    dominant_rule = by_rule.most_common(1)[0][0] if by_rule else None
    if counts.get("error", 0) or counts.get("fail", 0):
        status = "fail"
    elif counts.get("warning", 0):
        status = "warning"
    else:
        status = "clean"
    stripped = max(0, tokens_before - tokens_after)
    return {
        "task_surface": TASK_SURFACE,
        "status": status,
        "thresholds": {
            "warn_threshold": warn_threshold,
            "fail_threshold": fail_threshold,
        },
        "n_files": len(records),
        "n_clean": counts.get("clean", 0),
        "n_warning": counts.get("warning", 0),
        "n_fail": counts.get("fail", 0),
        "n_error": counts.get("error", 0),
        "input_tokens_before": tokens_before,
        "input_tokens_after": tokens_after,
        "tokens_stripped": stripped,
        "strip_ratio": (stripped / tokens_before) if tokens_before else 0.0,
        "tokens_stripped_by_rule": dict(by_rule),
        "dominant_rule": dominant_rule,
    }


# ---- scored-records cache (belt/suspenders/buttons for corpus-scale runs) ----
#
# The single-process loop becomes the wrong tool at corpus scale (see
# warn_if_large_manifest). When --records-cache is given, the loop becomes
# recoverable: it skips already-scored paths, flushes an atomic cache every N
# files (status in_progress -> complete), and logs progress — so a host hang
# resumes from the last flush instead of rescoring from file 1 (#133). The
# cache is keyed by file path AND a per-file content fingerprint, gated by a
# compat meta of the scoring args that change check_path's output. A cached
# record is reused only when the file still exists and its content is
# byte-identical to when it was scored — a path+settings match alone is NOT
# safe for a hygiene gate, since a changed file could let a stale "clean"
# record mask newly-contaminated input (Codex #212 P1).

_RECORDS_CACHE_TOOL = "check_corpus"
# 1.1: cache payload now carries per-file content fingerprints. Bumping the
# version invalidates pre-fingerprint (1.0) caches, forcing a safe rescore.
_RECORDS_CACHE_VERSION = "1.1"


def _records_cache_meta(
    *, strip_rules, strip_aggressive, collect_stripped, warn_threshold, fail_threshold,
) -> dict[str, Any]:
    return {
        "tool": _RECORDS_CACHE_TOOL,
        "version": _RECORDS_CACHE_VERSION,
        "strip_rules": strip_rules,
        "strip_aggressive": bool(strip_aggressive),
        # collect_stripped changes the per-file record PAYLOAD (snippet fields),
        # so a cache built without it must not be reused for a --show-stripped
        # run, nor vice versa (which would leak snippets into a run that didn't
        # request them). Part of the compat gate. (Codex #212 P2.)
        "collect_stripped": bool(collect_stripped),
        "warn_threshold": warn_threshold,
        "fail_threshold": fail_threshold,
    }


def _file_content_fingerprint(path: Path) -> str | None:
    """SHA-256 of the file's bytes, or ``None`` if it can't be read. Used to
    refuse reuse of a cached record after the underlying file's CONTENT changed
    (or the file vanished) — see the module note above (Codex #212 P1)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _save_records_cache(
    path: Path, records: list[dict[str, Any]], fingerprints: dict[str, Any],
    *, status: str, meta: dict[str, Any],
) -> None:
    """Atomic write (tmp + os.replace) so a crash mid-write can't corrupt the
    cache the next run loads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "status": status, "meta": meta,
        "records": records, "fingerprints": fingerprints,
    }
    tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _load_records_cache(
    path: Path, *, expected_meta: dict[str, Any], refresh: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return ``({path: record}, {path: content_sha256})`` for already-scored
    files, or two empty dicts (no cache, unreadable, or incompatible meta)."""
    if refresh or not path.exists():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(payload, dict) or payload.get("meta") != expected_meta:
        return {}, {}
    records = {
        rec["path"]: rec
        for rec in payload.get("records", [])
        if isinstance(rec, dict) and isinstance(rec.get("path"), str)
    }
    fps = payload.get("fingerprints")
    if not isinstance(fps, dict):
        fps = {}
    return records, fps


def check_corpus_paths(
    paths: list[str | Path],
    *,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
    collect_stripped: bool = False,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
    cache_path: str | Path | None = None,
    cache_flush_every: int = 200,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    cache_path = Path(cache_path) if cache_path is not None else None
    meta = _records_cache_meta(
        strip_rules=strip_rules, strip_aggressive=strip_aggressive,
        collect_stripped=collect_stripped,
        warn_threshold=warn_threshold, fail_threshold=fail_threshold,
    )
    cached_records, cached_fps = (
        _load_records_cache(cache_path, expected_meta=meta, refresh=refresh_cache)
        if cache_path is not None else ({}, {})
    )
    if cached_records:
        sys.stderr.write(
            f"check_corpus: found cache at {cache_path} "
            f"({len(cached_records)} entries); reusing files whose content is "
            f"unchanged.\n"
        )

    flush_every = max(1, int(cache_flush_every))
    records: list[dict[str, Any]] = []
    fingerprints: dict[str, Any] = {}
    n_total = len(paths)
    n_reused = 0
    t0 = time.time()
    since_flush = 0
    for i, path in enumerate(paths):
        key = str(Path(path))
        if cache_path is not None:
            fp = _file_content_fingerprint(Path(path))
            # Reuse only when the file still exists AND its content fingerprint
            # matches the cached one — never on a missing or changed file.
            if (
                key in cached_records and fp is not None
                and cached_fps.get(key) == fp
            ):
                records.append(cached_records[key])
                fingerprints[key] = fp
                n_reused += 1
                continue
        record = check_path(
            Path(path),
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
            collect_stripped=collect_stripped,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
        )
        records.append(record)
        if cache_path is not None:
            fingerprints[key] = fp  # computed above; None if currently unreadable
            since_flush += 1
            if since_flush >= flush_every:
                _save_records_cache(
                    cache_path, records, fingerprints,
                    status="in_progress", meta=meta,
                )
                since_flush = 0
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed if elapsed > 0 else 0.0
                sys.stderr.write(
                    f"  scored {i + 1}/{n_total} ({rate:.1f} files/s) "
                    f"-> cache flushed\n"
                )
    if cache_path is not None:
        _save_records_cache(
            cache_path, records, fingerprints, status="complete", meta=meta,
        )
        if n_reused:
            sys.stderr.write(
                f"check_corpus: reused {n_reused}/{n_total} unchanged files; "
                f"re-scored {n_total - n_reused}.\n"
            )

    summary = _summarize_hygiene_records(
        records,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )
    summary["files"] = records
    return summary


def render_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Corpus Hygiene Check")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(f"**Status:** {result['status']}")
    lines.append(f"**Files:** {result['n_files']}")
    lines.append(
        f"**Counts:** {result['n_clean']} clean, {result['n_warning']} warning, "
        f"{result['n_fail']} fail, {result['n_error']} error"
    )
    lines.append(
        f"**Aggregate stripped:** {result['tokens_stripped']} / "
        f"{result['input_tokens_before']} tokens "
        f"({result['strip_ratio']:.1%}; dominant rule: "
        f"{result.get('dominant_rule') or 'none'})"
    )
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append(
        "| status | stripped | ratio | dominant rule | path | error |"
    )
    lines.append("|---|---:|---:|---|---|---|")
    for record in result["files"]:
        lines.append(
            f"| {record['status']} | "
            f"{record.get('tokens_stripped', 0)} | "
            f"{float(record.get('strip_ratio', 0.0) or 0.0):.1%} | "
            f"{record.get('dominant_rule') or ''} | "
            f"`{md_cell(record['path'])}` | "
            f"{md_cell(record.get('error') or '')} |"
        )
    if result.get("tokens_stripped_by_rule"):
        lines.append("")
        lines.append("## Rule Totals")
        lines.append("")
        lines.append("| rule | tokens stripped |")
        lines.append("|---|---:|")
        for rule, count in sorted(
            result["tokens_stripped_by_rule"].items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            lines.append(f"| `{rule}` | {count} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check corpus files for non-prose contamination that would be "
            "stripped by SETEC preprocessing."
        )
    )
    parser.add_argument("--path", action="append", help="File to check; repeatable.")
    parser.add_argument("--dir", action="append", help="Directory of .txt/.md files; repeatable.")
    parser.add_argument("--manifest", help="Optional JSONL corpus manifest.")
    parser.add_argument("--filter", help="Manifest filter, e.g. use=baseline,register=blog_essay.")
    parser.add_argument("--warn-threshold", type=float, default=DEFAULT_WARN_THRESHOLD)
    parser.add_argument("--fail-threshold", type=float, default=DEFAULT_FAIL_THRESHOLD)
    parser.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rules to enable. Default: all conservative rules. Available: "
        + ", ".join(available_rule_names()) + ".",
    )
    parser.add_argument(
        "--strip-aggressive",
        action="store_true",
        help="Also check aggressive URL/image/footnote/citation stripping rules.",
    )
    parser.add_argument(
        "--show-stripped",
        action="store_true",
        help="Include representative stripped snippets in JSON output.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write report to file instead of stdout.")
    parser.add_argument(
        "--records-cache",
        help=(
            "Path to a scored-records cache. Makes a corpus-scale run "
            "recoverable: skips already-scored files, flushes atomically every "
            "--records-cache-flush-every files, and resumes from the last flush "
            "after a crash / host hang (the single-process belt/suspenders/buttons path)."
        ),
    )
    parser.add_argument(
        "--records-cache-flush-every",
        type=int,
        default=200,
        help="Flush --records-cache every N files (default 200). Ignored when --records-cache is unset.",
    )
    parser.add_argument(
        "--refresh-records-cache",
        action="store_true",
        help="Discard any existing --records-cache and rescore from scratch.",
    )
    args = parser.parse_args()

    if args.warn_threshold < 0 or args.fail_threshold < 0:
        parser.error("Thresholds must be non-negative.")
    if args.warn_threshold > args.fail_threshold:
        parser.error("--warn-threshold must be <= --fail-threshold.")
    try:
        paths = collect_paths(
            paths=args.path,
            dirs=args.dir,
            manifest=args.manifest,
            filter_text=args.filter,
        )
        # Discoverability: at corpus scales where the single-
        # process loop is the wrong tool, warn the operator about
        # the sharded path before sinking hours into the run.
        warn_if_large_manifest(len(paths), args.manifest)
        result = check_corpus_paths(
            paths,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
            collect_stripped=args.show_stripped,
            warn_threshold=args.warn_threshold,
            fail_threshold=args.fail_threshold,
            cache_path=args.records_cache,
            cache_flush_every=args.records_cache_flush_every,
            refresh_cache=args.refresh_records_cache,
        )
    except CorpusCheckError as exc:
        print(f"CorpusCheckError: {exc}", file=sys.stderr)
        return 1

    if args.json:
        envelope = build_audit_payload(
            result, target_path=args.manifest or (args.dir or [None])[0],
        )
        output = json.dumps(envelope, indent=2, default=str)
    else:
        output = render_report(result)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 1 if result["status"] == "fail" else 0


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Corpus-hygiene gate report. For each file in the input "
            "set, classifies the file as clean / warning / fail / "
            "error based on how much of the text gets stripped by "
            "the configured preprocessing rules. Reports aggregate "
            "counts, the dominant stripping rule, and the overall "
            "strip ratio."
        ),
        does_not_license=(
            "An authorship verdict or a stylometric reading. The "
            "gate measures preprocessing impact (how much of the "
            "corpus survives strip rules), not the prose itself. "
            "A high strip ratio means the corpus has substantial "
            "non-prose contamination; it does not say whether the "
            "surviving prose is AI-written, human, or anything else."
        ),
        comparison_set={
            "n_files": result.get("n_files"),
            "status": result.get("status"),
            "thresholds": result.get("thresholds"),
            "strip_ratio": result.get("strip_ratio"),
            "dominant_rule": result.get("dominant_rule"),
        },
        additional_caveats=[
            "Strip rules are register-aware but not domain-specific. "
            "Code-heavy or markup-heavy corpora that are legitimate "
            "research material will register as high-strip; use "
            "--allow-non-prose at downstream audits when this is "
            "the intended workflow.",
            "The gate operates pre-audit: a clean file here is a "
            "necessary but not sufficient condition for downstream "
            "stylometric validity.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap check_corpus's result dict in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    results_payload: dict[str, Any] = {}
    for k in (
        "status", "thresholds", "n_files",
        "n_clean", "n_warning", "n_fail", "n_error",
        "input_tokens_before", "input_tokens_after",
        "tokens_stripped", "strip_ratio",
        "tokens_stripped_by_rule", "dominant_rule",
        "files",
    ):
        if k in result:
            results_payload[k] = result[k]

    warnings: list[str] = []
    if result.get("status") in {"warning", "fail"}:
        warnings.append(
            f"Corpus gate status: {result.get('status')!r} "
            f"({result.get('n_warning', 0)} warning(s), "
            f"{result.get('n_fail', 0)} fail(s), "
            f"{result.get('n_error', 0)} error(s))."
        )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=int(result.get("input_tokens_before", 0) or 0),
        baseline=None,
        results=results_payload,
        claim_license=_claim_license(result),
        warnings=warnings,
    )


if __name__ == "__main__":
    sys.exit(main())
