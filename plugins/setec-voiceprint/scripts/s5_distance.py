#!/usr/bin/env python3
"""Locked six-family S5 Burrows-Delta surface over normalized feature entries.

The caller supplies already-extracted feature maps.  This surface performs no
text loading, corpus discovery, parsing, model loading, or network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import from_legacy  # noqa: E402
from output_schema import OutputValidityError, build_error_output, build_output  # noqa: E402
from stylometry_distance import family_distance  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "s5_distance"
SCRIPT_VERSION = "1.0"
REQUEST_SCHEMA = "setec-s5-distance-request/1"

FAMILY_ORDER = (
    "char_ngrams_3",
    "char_ngrams_4",
    "char_ngrams_5",
    "pos_trigrams",
    "dependency_ngrams",
    "punctuation",
)
FAMILY_LIMITS: dict[str, int | None] = {
    "char_ngrams_3": 200,
    "char_ngrams_4": 200,
    "char_ngrams_5": 200,
    "pos_trigrams": 300,
    "dependency_ngrams": 300,
    "punctuation": None,
}

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_KEYS = {"schema", "target", "baseline", "parser_inventory_sha256"}
_BASELINE_KEYS = {"manifest_sha256", "content_inventory_sha256", "entries"}
_ENTRY_KEYS = {"id", "content_sha256", "word_count", "features"}


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be sha256:<64 lowercase hex characters>")
    return value


def _validate_entry(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        raise ValueError(f"{field} must contain exactly {sorted(_ENTRY_KEYS)}")
    if not isinstance(value["id"], str) or not value["id"].strip():
        raise ValueError(f"{field}.id must be a non-empty string")
    _require_sha256(value["content_sha256"], f"{field}.content_sha256")
    if isinstance(value["word_count"], bool) or not isinstance(value["word_count"], int) \
            or value["word_count"] < 1:
        raise ValueError(f"{field}.word_count must be a positive integer")
    features = value["features"]
    if not isinstance(features, dict) or set(features) != set(FAMILY_ORDER):
        raise ValueError(f"{field}.features must contain exactly the six frozen families")
    for family in FAMILY_ORDER:
        family_values = features[family]
        if not isinstance(family_values, dict) or not family_values:
            raise ValueError(f"{field}.features.{family} must be a non-empty object")
        for name, number in family_values.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{field}.features.{family} feature names must be non-empty strings")
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError(f"{field}.features.{family}.{name} must be numeric")
            try:
                normalized = float(number)
            except OverflowError as exc:
                raise ValueError(
                    f"{field}.features.{family}.{name} must be finite and non-negative"
                ) from exc
            if not math.isfinite(normalized) or normalized < 0:
                raise ValueError(f"{field}.features.{family}.{name} must be finite and non-negative")
    return value


def validate_request(value: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        raise ValueError(f"request must contain exactly {sorted(_REQUEST_KEYS)}")
    if value["schema"] != REQUEST_SCHEMA:
        raise ValueError(f"schema must be {REQUEST_SCHEMA!r}")
    _require_sha256(value["parser_inventory_sha256"], "parser_inventory_sha256")
    target = _validate_entry(value["target"], "target")
    baseline = value["baseline"]
    if not isinstance(baseline, dict) or set(baseline) != _BASELINE_KEYS:
        raise ValueError(f"baseline must contain exactly {sorted(_BASELINE_KEYS)}")
    _require_sha256(baseline["manifest_sha256"], "baseline.manifest_sha256")
    _require_sha256(baseline["content_inventory_sha256"], "baseline.content_inventory_sha256")
    raw_entries = baseline["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) < 2:
        raise ValueError("baseline.entries must contain at least two entries")
    entries = [_validate_entry(item, f"baseline.entries[{i}]") for i, item in enumerate(raw_entries)]
    if [item["id"] for item in entries] != sorted(item["id"] for item in entries):
        raise ValueError("baseline.entries must be sorted by id")
    ids = [target["id"], *(item["id"] for item in entries)]
    hashes = [target["content_sha256"], *(item["content_sha256"] for item in entries)]
    if len(ids) != len(set(ids)):
        raise ValueError("target and baseline entry ids must be disjoint and unique")
    if len(hashes) != len(set(hashes)):
        raise ValueError("target and baseline content hashes must be disjoint and unique")
    return target, baseline, entries


def _selected_names(entries: list[dict[str, Any]], family: str) -> list[str]:
    totals: dict[str, float] = {}
    for item in entries:
        for name, value in item["features"][family].items():
            totals[name] = totals.get(name, 0.0) + float(value)
    names = sorted(totals, key=lambda name: (-totals[name], name))
    limit = FAMILY_LIMITS[family]
    return names if limit is None else names[:limit]


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__), SCRIPT_DIR / "stylometry_distance.py"):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _canonical_sha256(value: Any) -> str:
    data = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def compute_s5(request: dict[str, Any]) -> dict[str, Any]:
    target, baseline, entries = validate_request(request)
    values: dict[str, float] = {}
    feature_counts: dict[str, int] = {}
    for family in FAMILY_ORDER:
        names = _selected_names(entries, family)
        feature_counts[family] = len(names)
        value = family_distance(target, entries, family, names)["burrows_delta"]
        if not math.isfinite(value):
            raise ValueError(f"computed non-finite Burrows Delta for {family}")
        values[family] = value
    mean = sum(values.values()) / len(FAMILY_ORDER)
    if not math.isfinite(mean):
        raise ValueError("computed non-finite S5 unweighted mean")
    feature_inventory = {
        "target": target,
        "baseline_entries": entries,
    }
    return {
        "method": "unweighted mean of six family Burrows-Delta values",
        "family_order": list(FAMILY_ORDER),
        "family_limits": FAMILY_LIMITS,
        "selected_feature_counts": feature_counts,
        "family_burrows_delta": values,
        "s5_unweighted_mean": mean,
        "target_content_sha256": target["content_sha256"],
        "baseline_manifest_sha256": baseline["manifest_sha256"],
        "baseline_content_inventory_sha256": baseline["content_inventory_sha256"],
        "parser_inventory_sha256": request["parser_inventory_sha256"],
        "normalized_feature_inventory_sha256": _canonical_sha256(feature_inventory),
        "request_sha256": _canonical_sha256(request),
        "implementation_sha256": _implementation_sha256(),
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "Six descriptive Burrows-Delta measurements over the caller-supplied normalized "
            "feature maps, plus their unweighted arithmetic mean."
        ),
        "does_not_license": (
            "Any authorship, AI/human, quality, similarity, pass/fail, ranking, selection, or "
            "training-target judgment; any claim that this surface verified source text, corpus "
            "membership, parser provenance, or the supplied content hashes. No thresholds or verdicts."
        ),
    }


def _run(request_path: str) -> dict[str, Any]:
    path = Path(request_path)
    try:
        request = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        target, baseline, entries = validate_request(request)
        results = compute_s5(request)
        return build_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            target_path=path,
            target_words=target["word_count"],
            target_extra={"content_sha256": target["content_sha256"], "entry_id": target["id"]},
            baseline={
                "n_files": len(entries),
                "words": sum(item["word_count"] for item in entries),
                "manifest_sha256": baseline["manifest_sha256"],
                "content_inventory_sha256": baseline["content_inventory_sha256"],
            },
            results=results,
            claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        )
    except (
        OSError, UnicodeError, json.JSONDecodeError, ValueError,
        OutputValidityError,
    ) as exc:
        return build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            target_path=path,
            reason=str(exc),
            reason_category="bad_input",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="Path to a setec-s5-distance-request/1 JSON document")
    parser.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout")
    parser.add_argument("--out", help="Write the JSON envelope to this path")
    args = parser.parse_args(argv)
    envelope = _run(args.request)
    rendered = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        try:
            Path(args.out).write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"[{TOOL_NAME}] cannot write --out: {exc}\n")
            return 2
    if args.json or not args.out:
        print(rendered)
    # The normalized dispatcher parses this structured envelope from stdout;
    # a nonzero child status would make it discard the closed refusal as an
    # internal error before parsing. Availability lives in the envelope.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
