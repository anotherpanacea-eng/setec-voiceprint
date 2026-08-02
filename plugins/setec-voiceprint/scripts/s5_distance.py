#!/usr/bin/env python3
"""Locked six-family S5 distance surface for Voicewright N10.

The only public grammar is::

    setec run s5_distance --request REQUEST.json --json

The request binds a target and sanitized baseline beneath one private root.
This surface validates those bytes, computes six existing
``stylometry_core.family_distance`` Burrows deltas, and returns their equal
mean. It makes no authorship, provenance, or quality determination.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from output_schema import (  # type: ignore
    OutputValidityError,
    build_error_output,
    build_output,
)
from register_taxonomy import assert_personal_register_isolated  # type: ignore
from stylometry_core import (  # type: ignore
    extract_entry_features,
    extract_features,
    family_distance,
    feature_vector,
    select_feature_names,
    vector_stats,
    word_tokens,
)


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "s5_distance"
SCRIPT_VERSION = "1.0"
REQUEST_SCHEMA = "setec-s5-distance-request/1"
RESULT_SCHEMA = "setec-s5-distance/1"
SENTENCE_SPLITTER_ID = "n10-regex-sentences/1"
PARSER_NAME = "en_core_web_sm"

S5_FAMILIES = (
    "char_ngrams_3",
    "char_ngrams_4",
    "char_ngrams_5",
    "pos_trigrams",
    "dependency_ngrams",
    "punctuation",
)
S5_LIMITS = {
    "char_ngrams_3": 200,
    "char_ngrams_4": 200,
    "char_ngrams_5": 200,
    "pos_trigrams": 300,
    "dependency_ngrams": 300,
}

REQUEST_KEYS = frozenset({
    "schema",
    "private_root",
    "target_relpath",
    "target_sha256",
    "baseline_manifest_relpath",
    "baseline_manifest_sha256",
    "baseline_content_inventory_sha256",
    "use",
    "split",
    "register",
    "persona",
    "ai_status",
    "sentence_splitter_id",
    "parser_inventory_sha256",
})
MANIFEST_KEYS = frozenset({
    "id",
    "path",
    "content_sha256",
    "source_seed_sha256",
    "source_paragraph_index",
    "seed_family",
    "use",
    "split",
    "register",
    "persona",
    "ai_status",
})
RESULT_KEYS = frozenset({
    "schema",
    "implementation_sha256",
    "target_sha256",
    "baseline_manifest_sha256",
    "baseline_content_inventory_sha256",
    "parser_name",
    "parser_version",
    "parser_inventory_sha256",
    "sentence_splitter_id",
    "family_scores",
    "s5_score",
})
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}")

# This is the producer-side implementation inventory. Voicewright snapshots and
# rehashes the same files in its runtime lock; no version string substitutes for
# their bytes.
IMPLEMENTATION_RELPATHS = (
    "plugins/setec-voiceprint/capabilities.d/s5_distance.yaml",
    "plugins/setec-voiceprint/scripts/capabilities.py",
    "plugins/setec-voiceprint/scripts/claim_license.py",
    "plugins/setec-voiceprint/scripts/claim_license_surfaces/voice_coherence.txt",
    "plugins/setec-voiceprint/scripts/output_schema.py",
    "plugins/setec-voiceprint/scripts/preprocessing.py",
    "plugins/setec-voiceprint/scripts/register_taxonomy.py",
    "plugins/setec-voiceprint/scripts/s5_distance.py",
    "plugins/setec-voiceprint/scripts/setec_run.py",
    "plugins/setec-voiceprint/scripts/stylometry_core.py",
    "plugins/setec-voiceprint/scripts/variance_audit.py",
)

DEFAULT_LICENSES = (
    "Reports six descriptive Burrows-Delta distances from one target to the "
    "request-bound baseline (character 3/4/5-grams, POS trigrams, dependency "
    "n-grams, and punctuation) and their equal, unweighted mean."
)
DEFAULT_DOES_NOT_LICENSE = (
    "Does not license an authorship, AI/human provenance, quality, plagiarism, "
    "or identity verdict; a threshold or calibrated operating point; or use of "
    "S5 as a training objective. The caller owns its preregistered paired "
    "comparison and all downstream decisions."
)


class S5DistanceError(ValueError):
    """Closed request, binding, dependency, or scoring refusal."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _canonical_json_or_refuse(value: Any, where: str) -> bytes:
    try:
        return canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise S5DistanceError(
            f"{where} contains a non-finite or non-canonical JSON value"
        ) from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regex_split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.split(text) if part.strip()]


def _require_hex64(value: Any, field: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        raise S5DistanceError(f"{field} must be lowercase sha256 hex")
    return value


def _require_exact_keys(value: Any, keys: frozenset[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise S5DistanceError(f"{where} has wrong keys: {actual!r}")
    return value


def _resolve_private_file(
    root: Path,
    relpath: Any,
    field: str,
    *,
    base: Path | None = None,
) -> Path:
    if not isinstance(relpath, str) or not relpath:
        raise S5DistanceError(f"{field} must be a non-empty POSIX relative path")
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != relpath:
        raise S5DistanceError(f"{field} must be a normalized contained POSIX relpath")
    path = ((base or root) / Path(*pure.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise S5DistanceError(f"{field} escapes private_root") from exc
    if not path.is_file():
        raise S5DistanceError(f"{field} does not resolve to a regular file")
    return path


def _read_canonical_request(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        request = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S5DistanceError("request is not readable canonical UTF-8 JSON") from exc
    request = _require_exact_keys(request, REQUEST_KEYS, "request")
    if raw != _canonical_json_or_refuse(request, "request"):
        raise S5DistanceError("request bytes are not canonical JSON")
    if request["schema"] != REQUEST_SCHEMA:
        raise S5DistanceError(f"request.schema must be {REQUEST_SCHEMA!r}")
    if request["use"] != "baseline" or request["split"] != "train":
        raise S5DistanceError("request use/split must be baseline/train")
    if request["register"] is not None:
        raise S5DistanceError("request.register must be null")
    if not isinstance(request["persona"], str) or not request["persona"]:
        raise S5DistanceError("request.persona must be a non-empty string")
    if request["ai_status"] != "pre_ai_human":
        raise S5DistanceError("request.ai_status must be pre_ai_human")
    if request["sentence_splitter_id"] != SENTENCE_SPLITTER_ID:
        raise S5DistanceError(
            f"request.sentence_splitter_id must be {SENTENCE_SPLITTER_ID!r}"
        )
    for field in (
        "target_sha256",
        "baseline_manifest_sha256",
        "baseline_content_inventory_sha256",
        "parser_inventory_sha256",
    ):
        _require_hex64(request[field], field)
    return request


def _validate_manifest(
    manifest_path: Path,
    root: Path,
    request: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = manifest_path.read_bytes()
    if sha256_bytes(raw) != request["baseline_manifest_sha256"]:
        raise S5DistanceError("baseline manifest sha256 mismatch")
    rows: list[dict[str, Any]] = []
    try:
        for line in raw.splitlines(keepends=True):
            if not line:
                continue
            row = json.loads(line.decode("utf-8"))
            row = _require_exact_keys(row, MANIFEST_KEYS, "baseline manifest row")
            if line != _canonical_json_or_refuse(row, "baseline manifest row"):
                raise S5DistanceError("baseline manifest row is not canonical JSONL")
            rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise S5DistanceError("baseline manifest is not canonical UTF-8 JSONL") from exc
    if not rows:
        raise S5DistanceError("baseline manifest contains no entries")

    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    entries: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        prefix = f"baseline row {index}"
        content_sha = _require_hex64(row["content_sha256"], f"{prefix}.content_sha256")
        if row["id"] != content_sha:
            raise S5DistanceError(f"{prefix}.id must equal content_sha256")
        _require_hex64(row["source_seed_sha256"], f"{prefix}.source_seed_sha256")
        if (
            not isinstance(row["source_paragraph_index"], int)
            or isinstance(row["source_paragraph_index"], bool)
            or row["source_paragraph_index"] < 0
        ):
            raise S5DistanceError(f"{prefix}.source_paragraph_index must be nonnegative int")
        if not isinstance(row["seed_family"], str) or not row["seed_family"]:
            raise S5DistanceError(f"{prefix}.seed_family must be non-empty string")
        if (
            row["use"] != request["use"]
            or row["split"] != request["split"]
            or row["register"] is not None
            or row["persona"] != request["persona"]
            or row["ai_status"] != request["ai_status"]
        ):
            raise S5DistanceError(f"{prefix} metadata does not match request filters")
        entry_path = _resolve_private_file(
            root,
            row["path"],
            f"{prefix}.path",
            base=manifest_path.parent,
        )
        try:
            content_bytes = entry_path.read_bytes()
        except OSError as exc:
            raise S5DistanceError(f"{prefix} content is unreadable") from exc
        if sha256_bytes(content_bytes) != content_sha:
            raise S5DistanceError(f"{prefix} content sha256 mismatch")
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise S5DistanceError(f"{prefix} content is not UTF-8") from exc
        if content_sha in seen_ids or row["path"] in seen_paths:
            raise S5DistanceError("baseline manifest ids and paths must be unique")
        seen_ids.add(content_sha)
        seen_paths.add(row["path"])
        entries.append({
            "id": row["id"],
            "path": str(entry_path),
            "text": content_text,
            "metadata": dict(row),
        })

    inventory = sha256_bytes("\n".join(sorted(seen_ids)).encode("ascii"))
    if inventory != request["baseline_content_inventory_sha256"]:
        raise S5DistanceError("baseline content inventory sha256 mismatch")
    return rows, entries


def implementation_sha256() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    if tuple(sorted(IMPLEMENTATION_RELPATHS)) != IMPLEMENTATION_RELPATHS:
        raise S5DistanceError("implementation inventory is not sorted")
    inventory = []
    for relpath in IMPLEMENTATION_RELPATHS:
        path = (repo_root / relpath).resolve()
        if not path.is_file():
            raise S5DistanceError(f"implementation file missing: {relpath}")
        data = path.read_bytes()
        inventory.append({
            "relpath": relpath,
            "sha256": sha256_bytes(data),
            "size_bytes": len(data),
        })
    return sha256_bytes(b"".join(canonical_json(row) for row in inventory))


def _load_parser() -> tuple[Any, str]:
    try:
        import spacy  # type: ignore

        nlp = spacy.load(PARSER_NAME)
        version = importlib.metadata.version("en-core-web-sm")
    except Exception as exc:  # dependency refusal; never download/fallback
        raise S5DistanceError("spaCy en_core_web_sm parser is unavailable") from exc
    pipe_names = set(getattr(nlp, "pipe_names", []))
    if not {"tagger", "parser"}.issubset(pipe_names):
        raise S5DistanceError("en_core_web_sm lacks required tagger/parser pipeline")
    return nlp, version


def _score(
    target_text: str,
    baseline_entries: list[dict[str, Any]],
    *,
    nlp: Any,
) -> dict[str, float]:
    family_set = frozenset(S5_FAMILIES)
    target_features = extract_features(
        target_text,
        include_spacy=True,
        allow_non_prose=True,
        sentence_splitter=regex_split_sentences,
        nlp=nlp,
        families=family_set,
    )
    baseline_features = extract_entry_features(
        baseline_entries,
        include_spacy=True,
        allow_non_prose=True,
        sentence_splitter=regex_split_sentences,
        nlp=nlp,
        families=family_set,
    )
    selected = select_feature_names(baseline_features, limits=S5_LIMITS)

    scores: dict[str, float] = {}
    for family in S5_FAMILIES:
        names = selected.get(family) or []
        if not names:
            raise S5DistanceError(f"family has no selected features: {family}")
        vectors = [feature_vector(item, family, names) for item in baseline_features]
        stats = vector_stats(vectors, names)
        if not any(info["sd"] > 0 for info in stats.values()):
            raise S5DistanceError(f"family has no nonzero-SD feature: {family}")
        value = family_distance(
            {
                "features": target_features["features"],
                "summary": target_features["summary"],
            },
            baseline_features,
            family,
            names,
        )["burrows_delta"]
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise S5DistanceError(f"family produced non-finite distance: {family}")
        scores[family] = float(value)
    return scores


def score_request(
    request_path: Path,
    *,
    nlp: Any | None = None,
    parser_version: str | None = None,
) -> tuple[dict[str, Any], int, int, int]:
    request = _read_canonical_request(request_path)
    root_value = request["private_root"]
    if not isinstance(root_value, str) or not root_value:
        raise S5DistanceError("private_root must be a resolved absolute path")
    root = Path(root_value)
    if not root.is_absolute() or not root.is_dir() or str(root.resolve()) != root_value:
        raise S5DistanceError("private_root must be a resolved existing directory")
    root = root.resolve()

    target_path = _resolve_private_file(root, request["target_relpath"], "target_relpath")
    target_bytes = target_path.read_bytes()
    if sha256_bytes(target_bytes) != request["target_sha256"]:
        raise S5DistanceError("target sha256 mismatch")
    try:
        target_text = target_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise S5DistanceError("target is not UTF-8") from exc

    manifest_path = _resolve_private_file(
        root, request["baseline_manifest_relpath"], "baseline_manifest_relpath"
    )
    _rows, entries = _validate_manifest(manifest_path, root, request)
    try:
        assert_personal_register_isolated(entries)
    except ValueError as exc:
        raise S5DistanceError(f"baseline register isolation refused: {exc}") from exc

    if nlp is None:
        nlp, loaded_version = _load_parser()
        parser_version = loaded_version
    if not isinstance(parser_version, str) or not parser_version:
        raise S5DistanceError("parser version is unavailable")

    family_scores = _score(target_text, entries, nlp=nlp)
    s5_score = sum(family_scores[family] for family in S5_FAMILIES) / len(S5_FAMILIES)
    if not math.isfinite(s5_score):
        raise S5DistanceError("S5 unweighted mean is non-finite")
    results = {
        "schema": RESULT_SCHEMA,
        "implementation_sha256": implementation_sha256(),
        "target_sha256": request["target_sha256"],
        "baseline_manifest_sha256": request["baseline_manifest_sha256"],
        "baseline_content_inventory_sha256": request[
            "baseline_content_inventory_sha256"
        ],
        "parser_name": PARSER_NAME,
        "parser_version": parser_version,
        "parser_inventory_sha256": request["parser_inventory_sha256"],
        "sentence_splitter_id": SENTENCE_SPLITTER_ID,
        "family_scores": family_scores,
        "s5_score": s5_score,
    }
    baseline_words = sum(len(word_tokens(entry["text"])) for entry in entries)
    return results, len(word_tokens(target_text)), len(entries), baseline_words


def build_envelope(
    results: dict[str, Any],
    *,
    target_words: int,
    baseline_files: int,
    baseline_words: int,
) -> dict[str, Any]:
    _require_exact_keys(results, RESULT_KEYS, "results")
    if results["schema"] != RESULT_SCHEMA:
        raise S5DistanceError(f"results.schema must be {RESULT_SCHEMA!r}")
    for field in (
        "implementation_sha256",
        "target_sha256",
        "baseline_manifest_sha256",
        "baseline_content_inventory_sha256",
        "parser_inventory_sha256",
    ):
        _require_hex64(results[field], f"results.{field}")
    if results["parser_name"] != PARSER_NAME:
        raise S5DistanceError(f"results.parser_name must be {PARSER_NAME!r}")
    if not isinstance(results["parser_version"], str) or not results["parser_version"]:
        raise S5DistanceError("results.parser_version must be non-empty string")
    if results["sentence_splitter_id"] != SENTENCE_SPLITTER_ID:
        raise S5DistanceError("results sentence splitter identity mismatch")
    family_scores = results["family_scores"]
    if not isinstance(family_scores, dict) or set(family_scores) != set(S5_FAMILIES):
        raise S5DistanceError("results.family_scores must have exactly the six S5 families")
    for family in S5_FAMILIES:
        value = family_scores[family]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise S5DistanceError(f"results.family_scores.{family} must be finite")
    expected_mean = sum(float(family_scores[f]) for f in S5_FAMILIES) / len(S5_FAMILIES)
    if not math.isfinite(expected_mean):
        raise S5DistanceError("results S5 unweighted mean is non-finite")
    if results["s5_score"] != expected_mean:
        raise S5DistanceError("results.s5_score is not the exact unweighted family mean")
    license_ = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "baseline_files": baseline_files,
            "families": list(S5_FAMILIES),
        },
        additional_caveats=[
            "Distances are descriptive and baseline-dependent; the normalized "
            "surface ships no threshold, band, or verdict."
        ],
    )
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=None,
        target_words=target_words,
        baseline={"n_files": baseline_files, "words": baseline_words},
        results=results,
        claim_license=license_,
        warnings=[],
        ai_status=None,
    )


def _error_envelope(reason: str, category: str) -> dict[str, Any]:
    return build_error_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        reason=reason,
        reason_category=category,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Canonical request JSON path.")
    parser.add_argument("--json", action="store_true", help="Emit normalized JSON envelope.")
    args = parser.parse_args(argv)
    try:
        results, target_words, baseline_files, baseline_words = score_request(
            Path(args.request)
        )
        envelope = build_envelope(
            results,
            target_words=target_words,
            baseline_files=baseline_files,
            baseline_words=baseline_words,
        )
    except (S5DistanceError, OutputValidityError) as exc:
        if isinstance(exc, OutputValidityError):
            category = "internal_error"
        else:
            category = "missing_dependency" if "parser is unavailable" in str(exc) else "bad_input"
        print(json.dumps(_error_envelope(str(exc), category), indent=2, sort_keys=True))
        return 0
    print(json.dumps(envelope, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
