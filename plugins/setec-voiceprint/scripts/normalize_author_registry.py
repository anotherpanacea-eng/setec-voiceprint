#!/usr/bin/env python3
"""Build a private, no-prose registry over personal author-corpus manifests.

The acquisition tools deliberately preserve each source's local persona and
register labels.  This utility is the explicit bridge to the hierarchical
registers consumed by ``author_corpus_export`` and Voicewright.  It never
copies prose, contact maps, or recipient identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "setec-author-registry/1"
ALLOWED_AI_STATUS = {
    "pre_ai_human", "ai_generated", "ai_generated_from_outline",
    "ai_assisted", "ai_edited", "mixed", "unknown",
    # Historical personal-manifest labels are retained verbatim in the
    # registry but remain review-or-exclude for training.
    "mixed_pre_and_post_ai", "post_june_2025_uncertain",
}


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _private_path(path: Path) -> None:
    if "ai-prose-baselines-private" not in {part.casefold() for part in path.parts}:
        raise ValueError("registry paths must remain below ai-prose-baselines-private")


def _assignment(value: str, flag: str) -> tuple[str, str]:
    left, sep, right = value.partition("=")
    if not sep or not left or not right:
        raise ValueError(f"{flag} values must be KEY=VALUE")
    return left, right


def _sources(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        key, raw_path = _assignment(value, "--source-manifest")
        if key in result:
            raise ValueError(f"duplicate source name {key!r}")
        path = Path(raw_path).expanduser()
        _private_path(path)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"source manifest must be a regular private file: {key}")
        result[key] = path
    if not result:
        raise ValueError("at least one --source-manifest is required")
    return result


def _register_map(values: list[str]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for value in values:
        key, canonical = _assignment(value, "--register-map")
        source, sep, legacy = key.partition(":")
        if not sep or not source or not legacy or "." not in canonical:
            raise ValueError("register maps must be SOURCE:LEGACY=family.member")
        pair = (source, legacy)
        if pair in result:
            raise ValueError(f"duplicate register mapping {key!r}")
        result[pair] = canonical
    if not result:
        raise ValueError("at least one --register-map is required")
    return result


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _source_text_path(manifest: Path, raw_path: str) -> Path:
    """Resolve legacy manifest paths using the validator's two safe roots."""
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("source text paths must be relative and traversal-free")
    for root in (manifest.parent, manifest.parent.parent):
        path = root / candidate
        if path.is_file() and not path.is_symlink():
            return path
    raise ValueError(f"source text is missing for manifest path {raw_path!r}")


def build_registry(
    *, sources: dict[str, Path], register_map: dict[tuple[str, str], str],
    canonical_persona: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    source_id_counts: Counter[tuple[str, str]] = Counter()
    seen_content: dict[str, str] = {}
    duplicate_hashes = 0
    declared_hash_mismatches = 0
    for source_name, manifest in sorted(sources.items()):
        manifest_hash = _sha256_bytes(manifest.read_bytes())
        for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {source_name} line {line_number}") from exc
            if not isinstance(entry, dict):
                raise ValueError(f"source entry is not an object in {source_name} line {line_number}")
            required = ("id", "path", "register", "ai_status")
            if any(not isinstance(entry.get(key), str) or not entry[key] for key in required):
                raise ValueError(f"missing required source fields in {source_name} line {line_number}")
            if entry["ai_status"] not in ALLOWED_AI_STATUS:
                raise ValueError(f"unknown AI status in {source_name} line {line_number}")
            mapping = (source_name, entry["register"])
            if mapping not in register_map:
                raise ValueError(f"missing explicit mapping for {source_name}:{entry['register']}")
            source_text = _source_text_path(manifest, entry["path"])
            content_hash = _sha256_bytes(source_text.read_bytes())
            source_id = (source_name, entry["id"])
            source_id_counts[source_id] += 1
            declared_hash = entry.get("content_hash")
            # Different acquisition products have historically hashed either
            # normalized text or exact output bytes.  Preserve both identities;
            # only the computed exact hash is used for registry deduplication.
            declared_hash_matches_exact = declared_hash in (None, content_hash)
            if declared_hash is not None and not declared_hash_matches_exact:
                declared_hash_mismatches += 1
            duplicate_of = seen_content.get(content_hash)
            if duplicate_of is None:
                seen_content[content_hash] = f"{source_name}:{entry['id']}"
            else:
                duplicate_hashes += 1
            records.append({
                "schema": SCHEMA,
                # Legacy acquisitions can reuse a human-readable ID for
                # distinct messages.  The exact artifact hash makes the
                # registry identity unambiguous without rewriting the source.
                "canonical_id": f"{source_name}:{entry['id']}:{content_hash[7:19]}:{line_number}",
                "canonical_persona": canonical_persona,
                "canonical_register": register_map[mapping],
                "source_manifest": source_name,
                "source_manifest_sha256": manifest_hash,
                "source_id": entry["id"],
                "source_line": line_number,
                "source_persona": entry.get("persona"),
                "source_register": entry["register"],
                "source_relative_path": entry["path"],
                "content_hash": content_hash,
                "source_declared_content_hash": declared_hash,
                "source_declared_hash_matches_exact": declared_hash_matches_exact,
                "duplicate_of": duplicate_of,
                "date_written": entry.get("date_written"),
                "ai_status": entry["ai_status"],
                "training_eligibility": "eligible_pre_ai" if entry["ai_status"] == "pre_ai_human" else "review_or_exclude",
                "source_split": entry.get("split", "unspecified"),
            })
    records.sort(key=lambda item: (item["canonical_register"], item["date_written"] or "", item["canonical_id"]))
    summary = {
        "schema": SCHEMA,
        "canonical_persona": canonical_persona,
        "records": len(records),
        "unique_content_hashes": len(seen_content),
        "duplicate_content_hash_records": duplicate_hashes,
        "duplicate_source_id_records": sum(count - 1 for count in source_id_counts.values() if count > 1),
        "source_declared_hash_mismatches": declared_hash_mismatches,
        "by_register": dict(sorted(Counter(item["canonical_register"] for item in records).items())),
        "by_ai_status": dict(sorted(Counter(item["ai_status"] for item in records).items())),
        "by_training_eligibility": dict(sorted(Counter(item["training_eligibility"] for item in records).items())),
    }
    return records, summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="normalize_author_registry")
    parser.add_argument("--source-manifest", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--register-map", action="append", default=[], metavar="NAME:LEGACY=CANONICAL")
    parser.add_argument("--persona", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.expanduser()
    _private_path(output_dir)
    records, summary = build_registry(
        sources=_sources(args.source_manifest), register_map=_register_map(args.register_map),
        canonical_persona=args.persona,
    )
    if not args.dry_run:
        rendered = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
        _write_atomic(output_dir / "author_registry.jsonl", rendered)
        _write_atomic(output_dir / "registry_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, UnicodeError, ValueError) as exc:
        if args.json:
            print(json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True))
        else:
            print(f"normalize_author_registry: {exc}")
        return 2
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"normalized {summary['records']} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
