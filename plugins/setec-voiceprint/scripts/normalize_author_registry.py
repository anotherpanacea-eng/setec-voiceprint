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
import sys
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


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _private_path(path: Path) -> None:
    # Resolve symlinks and `..` first: a lexical parts check on the unresolved path lets a
    # symlink component or parent-escape place registry output outside the protected tree.
    real = path.expanduser().resolve()
    if "ai-prose-baselines-private" not in {part.casefold() for part in real.parts}:
        raise ValueError("registry paths must remain below ai-prose-baselines-private")


def _reject_symlink_components(path: Path) -> None:
    if ".." in path.parts:
        raise ValueError("private output path must not contain parent traversal")
    absolute = path if path.is_absolute() else Path.cwd() / path
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError("private output path must not contain symlinks")


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


def _persona_aliases(values: list[str]) -> dict[tuple[str, str], str]:
    """Parse exporter-compatible SOURCE:LEGACY=CANONICAL persona aliases."""
    result: dict[tuple[str, str], str] = {}
    for value in values:
        key, canonical = _assignment(value, "--source-persona-alias")
        source, sep, legacy = key.partition(":")
        if not sep or not source or not legacy or not canonical:
            raise ValueError(
                "source persona aliases must be SOURCE:LEGACY=CANONICAL"
            )
        pair = (source, legacy)
        if pair in result:
            raise ValueError(f"duplicate source persona alias {key!r}")
        result[pair] = canonical
    return result


def _secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("private output directories must not be symlinks")
    if path.exists():
        if not path.is_dir():
            raise ValueError("private output directory path is not a directory")
    else:
        missing: list[Path] = []
        current = path
        while not current.exists():
            if current.is_symlink():
                raise ValueError("private output directories must not be symlinks")
            missing.append(current)
            current = current.parent
        if current.is_symlink() or not current.is_dir():
            raise ValueError("private output parent is not a regular directory")
        for directory in reversed(missing):
            os.mkdir(directory, 0o700)
            os.chmod(directory, 0o700)
    os.chmod(path, 0o700)


def _write_atomic(path: Path, content: str) -> None:
    _secure_directory(path.parent)
    descriptor, raw_temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    )
    temporary = Path(raw_temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _load_json_object(raw: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("source manifest entry contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise ValueError("source manifest entry is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("source manifest entry must be a JSON object")
    return value


def _source_text_path(manifest: Path, raw_path: str) -> Path:
    """Resolve legacy manifest paths using the validator's two safe roots."""
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("source text paths must be relative and traversal-free")
    for root in (manifest.parent, manifest.parent.parent):
        path = root / candidate
        if not path.is_file() or path.is_symlink():
            continue
        # An intermediate-directory symlink can make `path` resolve outside the authorized
        # root even when the final component is not a symlink; require the fully resolved
        # real path to stay within the resolved root AND the private tree.
        real = path.resolve()
        if not _within(real, root.resolve()):
            continue
        if "ai-prose-baselines-private" not in {part.casefold() for part in real.parts}:
            continue
        return path
    raise ValueError(f"source text is missing for manifest path {raw_path!r}")


def build_registry(
    *, sources: dict[str, Path], register_map: dict[tuple[str, str], str],
    canonical_persona: str,
    source_persona_aliases: dict[tuple[str, str], str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(canonical_persona, str) or not canonical_persona:
        raise ValueError("canonical persona must be a non-empty string")
    if source_persona_aliases is None:
        source_persona_aliases = {}
    if any(source not in sources for source, _legacy in source_persona_aliases):
        raise ValueError("source persona alias refers to an unknown source manifest")
    if any(canonical != canonical_persona for canonical in source_persona_aliases.values()):
        raise ValueError("source persona aliases must target the canonical persona")
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
            entry = _load_json_object(raw)
            required_strings = (
                "id", "path", "persona", "register", "ai_status",
                "corpus_role", "split", "consent_status",
            )
            if any(
                not isinstance(entry.get(key), str) or not entry[key]
                for key in required_strings
            ) or not isinstance(entry.get("use"), list):
                raise ValueError(f"missing required source fields in {source_name} line {line_number}")
            if entry["ai_status"] not in ALLOWED_AI_STATUS:
                raise ValueError(f"unknown AI status in {source_name} line {line_number}")
            if (
                entry["persona"] != canonical_persona
                and source_persona_aliases.get((source_name, entry["persona"]))
                != canonical_persona
            ):
                raise ValueError(
                    f"source persona is not authorized in {source_name} line {line_number}"
                )
            if (
                entry["corpus_role"] != "identity_baseline"
                or entry["use"] != ["voice_profile"]
                or entry["split"] != "baseline"
                or entry["consent_status"] != "author_consent"
            ):
                raise ValueError(
                    f"source entry is not an authorized identity baseline in "
                    f"{source_name} line {line_number}"
                )
            if entry.get("impostor_for") not in (None, ""):
                raise ValueError(
                    f"source entry carries an impostor marker in {source_name} "
                    f"line {line_number}"
                )
            if "register_match" in entry or "topic_match" in entry:
                raise ValueError(
                    f"source entry carries impostor-comparison metadata in "
                    f"{source_name} line {line_number}"
                )
            if entry.get("impostor") or entry.get("role") not in (None, "author"):
                raise ValueError(
                    f"source entry is not author material in {source_name} "
                    f"line {line_number}"
                )
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
    parser.add_argument(
        "--source-persona-alias", action="append", default=[],
        metavar="SOURCE:LEGACY=CANONICAL",
    )
    parser.add_argument("--persona", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.expanduser()
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    _reject_symlink_components(output_dir)
    _private_path(output_dir)
    records, summary = build_registry(
        sources=_sources(args.source_manifest), register_map=_register_map(args.register_map),
        canonical_persona=args.persona,
        source_persona_aliases=_persona_aliases(args.source_persona_alias),
    )
    if not args.dry_run:
        _secure_directory(output_dir)
        rendered = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
        _write_atomic(output_dir / "author_registry.jsonl", rendered)
        _write_atomic(output_dir / "registry_summary.json", json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        summary = run(args)
    except (OSError, UnicodeError, ValueError, TypeError):
        sys.stderr.write(
            "normalize_author_registry: private input or policy validation failed\n"
        )
        return 2
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(f"normalized {summary['records']} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
