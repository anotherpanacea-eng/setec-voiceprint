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
import json
import shlex
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from manifest_validator import resolve_path, validate_manifest
from preprocessing import available_rule_names, strip_non_prose


TASK_SURFACE = "validation"
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


def check_corpus_paths(
    paths: list[str | Path],
    *,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
    collect_stripped: bool = False,
    warn_threshold: float = DEFAULT_WARN_THRESHOLD,
    fail_threshold: float = DEFAULT_FAIL_THRESHOLD,
) -> dict[str, Any]:
    records = [
        check_path(
            Path(path),
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
            collect_stripped=collect_stripped,
            warn_threshold=warn_threshold,
            fail_threshold=fail_threshold,
        )
        for path in paths
    ]
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
        )
    except CorpusCheckError as exc:
        print(f"CorpusCheckError: {exc}", file=sys.stderr)
        return 1

    output = json.dumps(result, indent=2, default=str) if args.json else render_report(result)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
