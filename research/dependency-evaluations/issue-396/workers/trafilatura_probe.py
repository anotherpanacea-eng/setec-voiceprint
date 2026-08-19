#!/usr/bin/env python3
"""Version-local Trafilatura worker for the Voiceprint #396 packet.

The worker reads exactly one JSON request from stdin and writes exactly one
JSON response to stdout.  It imports the shipped acquisition seams from the
repository instead of reproducing them.  Fixture bytes are hash-checked before
use; timing samples are returned outside the semantic result block so the
controller can keep them out of byte-stability comparisons.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "setec-dependency-evaluation/1"
WORKER = Path(__file__).resolve()
REPO_ROOT = WORKER.parents[4]
SCRIPTS = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import acquisition_core as ac  # type: ignore  # noqa: E402
import acquire_blog as acquire_blog  # type: ignore  # noqa: E402
import bs4  # type: ignore  # noqa: E402
import feedparser  # type: ignore  # noqa: E402
import trafilatura  # type: ignore  # noqa: E402
from trafilatura import feeds as trafilatura_feeds  # type: ignore  # noqa: E402


class ProtocolError(ValueError):
    """Request or environment cannot support a truthful measurement."""


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError("response contains a non-finite number")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError("JSON object keys must be strings")
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def canonical_json_bytes(value: Any) -> bytes:
    _reject_non_finite(value)
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize(
        "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _fixture_path(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ProtocolError(f"fixture path escapes root: {relative}") from exc
    if not candidate.is_file():
        raise ProtocolError(f"fixture is missing: {relative}")
    return candidate


def _fixture_bytes(root: Path, row: dict[str, Any]) -> bytes:
    relative = row.get("path")
    expected = row.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected, str):
        raise ProtocolError("fixture row requires string path and sha256")
    data = _fixture_path(root, relative).read_bytes()
    actual = _sha256_bytes(data)
    if actual != expected:
        raise ProtocolError(
            f"fixture hash mismatch for {relative}: expected {expected}, got {actual}"
        )
    return data


def _environment() -> dict[str, str]:
    return {
        "beautifulsoup4": str(bs4.__version__),
        "feedparser": str(feedparser.__version__),
        "implementation": platform.python_implementation(),
        "python": platform.python_version(),
        "trafilatura": str(trafilatura.__version__),
    }


def _validate_environment(expected: Any, actual: dict[str, str]) -> None:
    if expected is None:
        return
    if not isinstance(expected, dict):
        raise ProtocolError("expected_environment must be an object")
    for key, value in expected.items():
        if key not in actual:
            raise ProtocolError(f"unknown environment identity: {key}")
        if actual[key] != value:
            raise ProtocolError(
                f"environment mismatch for {key}: expected {value}, got {actual[key]}"
            )


Extractor = Callable[[], tuple[str, str | None] | None]


def _outcome(
    call: Extractor,
    *,
    required: Iterable[str],
    forbidden: Iterable[str],
    expected_title: str | None,
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    try:
        extracted = call()
    except Exception as exc:  # the packet records fail-soft violations
        elapsed = time.perf_counter() - started
        return {
            "error_type": type(exc).__name__,
            "hit": False,
            "raised": True,
        }, elapsed
    elapsed = time.perf_counter() - started
    if extracted is None:
        text, title = "", None
        hit = False
    else:
        text, title = extracted
        text = text or ""
        hit = bool(text.strip())
    normalized = _normalize(text)
    required_list = list(required)
    forbidden_list = list(forbidden)
    result: dict[str, Any] = {
        "hit": hit,
        "raised": False,
        "text_length": len(text),
        "text_sha256": _sha256_bytes(text.encode("utf-8")),
        "title": title,
        "title_retained": (
            _normalize(title) == _normalize(expected_title)
            if expected_title is not None
            else None
        ),
    }
    if required_list or forbidden_list:
        required_found = [marker for marker in required_list if _normalize(marker) in normalized]
        forbidden_found = [marker for marker in forbidden_list if _normalize(marker) in normalized]
        result["required"] = {
            "found": required_found,
            "found_count": len(required_found),
            "total": len(required_list),
        }
        result["forbidden"] = {
            "found": forbidden_found,
            "found_count": len(forbidden_found),
            "total": len(forbidden_list),
        }
    return result, elapsed


def _html_calls(
    html: str, content_selector: str | None, strip_selectors: tuple[str, ...]
) -> dict[str, Extractor]:
    return {
        "fallback": lambda: ac.extract_main_content(
            html,
            content_selector=content_selector,
            strip_selectors=strip_selectors,
            prefer_trafilatura=False,
        ),
        "primary": lambda: ac._trafilatura_extract(
            html, strip_selectors=strip_selectors
        ),
        "full_seam": lambda: ac.extract_main_content(
            html,
            content_selector=content_selector,
            strip_selectors=strip_selectors,
            prefer_trafilatura=True,
        ),
    }


def _evaluate_html(
    root: Path, row: dict[str, Any], reruns: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    html = _fixture_bytes(root, row).decode("utf-8")
    content_selector = row.get("content_selector")
    if content_selector is not None and not isinstance(content_selector, str):
        raise ProtocolError(f"invalid content_selector for {row.get('path')}")
    strip_value = row.get("strip_selectors", [])
    if not isinstance(strip_value, list) or not all(isinstance(x, str) for x in strip_value):
        raise ProtocolError(f"invalid strip_selectors for {row.get('path')}")
    strip_selectors = tuple(strip_value)
    required = row.get("required_markers", [])
    forbidden = row.get("forbidden_markers", [])
    if not isinstance(required, list) or not all(isinstance(x, str) for x in required):
        raise ProtocolError(f"invalid required markers for {row.get('path')}")
    if not isinstance(forbidden, list) or not all(isinstance(x, str) for x in forbidden):
        raise ProtocolError(f"invalid forbidden markers for {row.get('path')}")
    if row.get("score_bearing") and (not required or not forbidden):
        raise ProtocolError(f"score-bearing fixture has empty markers: {row.get('path')}")

    calls = _html_calls(html, content_selector, strip_selectors)
    semantic_runs: dict[str, list[dict[str, Any]]] = {}
    timing_runs: dict[str, list[float]] = {}
    for name, call in calls.items():
        # One unmeasured warm-up keeps import/parser initialization out of the
        # five warm-run samples required by the evaluation contract.
        _outcome(
            call,
            required=required,
            forbidden=forbidden,
            expected_title=row.get("expected_title"),
        )
        outcomes: list[dict[str, Any]] = []
        samples: list[float] = []
        for _ in range(reruns):
            outcome, elapsed = _outcome(
                call,
                required=required,
                forbidden=forbidden,
                expected_title=row.get("expected_title"),
            )
            outcomes.append(outcome)
            samples.append(elapsed)
        semantic_runs[name] = outcomes
        timing_runs[name] = samples

    semantic = {
        "path": row["path"],
        "score_bearing": bool(row.get("score_bearing")),
        "structure_class": row.get("structure_class"),
        "runs": semantic_runs,
        "deterministic": {
            name: all(run == runs[0] for run in runs[1:])
            for name, runs in semantic_runs.items()
        },
    }
    timing = {
        "path": row["path"],
        "score_bearing": bool(row.get("score_bearing")),
        "samples_seconds": timing_runs,
        "warm_median_seconds": {
            name: statistics.median(samples) for name, samples in timing_runs.items()
        },
    }
    return semantic, timing


def _serialize_feed_item(item: acquire_blog.FeedItem) -> dict[str, Any]:
    return {
        "body_html": item.body_html,
        "date": item.date.isoformat() if item.date else None,
        "is_paid": item.is_paid,
        "link": item.link,
        "raw_byte_length": item.raw_byte_length,
        "title": item.title,
    }


def _trafilatura_feed_links(feed_text: str, reference: str) -> list[str]:
    domain, base = trafilatura_feeds.get_hostinfo(reference)
    if domain is None:
        raise ProtocolError(f"invalid feed reference URL: {reference}")
    params = trafilatura_feeds.FeedParameters(base, domain, reference)
    return trafilatura_feeds.extract_links(feed_text, params)


def _evaluate_feed(root: Path, row: dict[str, Any]) -> dict[str, Any]:
    feed_text = _fixture_bytes(root, row).decode("utf-8")
    items = [
        _serialize_feed_item(item)
        for item in acquire_blog.parse_feed(
            feed_text, source_type=str(row.get("source_type", "wordpress"))
        )
    ]
    expected_items = row.get("expected_items")
    if not isinstance(expected_items, list):
        raise ProtocolError(f"feed fixture lacks expected_items: {row.get('path')}")
    reference = row.get("source_url")
    if not isinstance(reference, str):
        raise ProtocolError(f"feed fixture lacks source_url: {row.get('path')}")
    trafilatura_links = _trafilatura_feed_links(feed_text, reference)
    feedparser_links = [item["link"] for item in items]
    parser_counts = Counter(feedparser_links)
    trafilatura_counts = Counter(trafilatura_links)
    return {
        "contract_fields": [
            "title", "link", "date", "body_html", "is_paid", "raw_byte_length"
        ],
        "expected_items_match": items == expected_items,
        "feedparser_items": items,
        "format": row.get("format"),
        "link_comparison": {
            "deduplication_equal": parser_counts == trafilatura_counts,
            "feedparser_links": feedparser_links,
            "order_equal": feedparser_links == trafilatura_links,
            "set_equal": set(feedparser_links) == set(trafilatura_links),
            "trafilatura_links": trafilatura_links,
        },
        "path": row["path"],
        # Trafilatura's feed API returns links only.  Even perfect link parity
        # cannot satisfy the shipped six-field FeedItem contract.
        "replacement_allowed": False,
        "replacement_blockers": [
            "Trafilatura feed extraction does not return title/date/body_html/is_paid/raw_byte_length",
            "link-only parity cannot replace acquire_blog.parse_feed",
        ],
    }


def _aggregate_html(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if row["score_bearing"]]
    paths = ("fallback", "primary", "full_seam")
    aggregates: dict[str, Any] = {}
    for path_name in paths:
        first = [row["runs"][path_name][0] for row in scored]
        required_found = sum(run["required"]["found_count"] for run in first)
        required_total = sum(run["required"]["total"] for run in first)
        forbidden_found = sum(run["forbidden"]["found_count"] for run in first)
        forbidden_total = sum(run["forbidden"]["total"] for run in first)
        recalls = [run["required"]["found_count"] / run["required"]["total"] for run in first]
        leakages = [run["forbidden"]["found_count"] / run["forbidden"]["total"] for run in first]
        aggregates[path_name] = {
            "macro_leakage": sum(leakages) / len(leakages),
            "macro_recall": sum(recalls) / len(recalls),
            "micro_leakage": forbidden_found / forbidden_total,
            "micro_recall": required_found / required_total,
            "title_retained_count": sum(run["title_retained"] is True for run in first),
        }
    classes = sorted({str(row["structure_class"]) for row in scored})
    primary_success_by_class = {
        structure: any(
            row["structure_class"] == structure and row["runs"]["primary"][0]["hit"]
            for row in scored
        )
        for structure in classes
    }
    fallback_found = {
        row["path"]: row["runs"]["fallback"][0]["required"]["found_count"]
        for row in scored
    }
    full_lost_all = [
        row["path"]
        for row in scored
        if fallback_found[row["path"]] > 0
        and row["runs"]["full_seam"][0]["required"]["found_count"] == 0
    ]
    failure_rows = [row for row in rows if not row["score_bearing"]]
    return {
        "paths": aggregates,
        "primary_success_by_structure_class": primary_success_by_class,
        "full_seam_lost_all_required": full_lost_all,
        "all_reruns_deterministic": all(
            all(row["deterministic"].values()) for row in rows
        ),
        "failure_cases_fail_soft": all(
            not run["raised"]
            for row in failure_rows
            for path_name in paths
            for run in row["runs"][path_name]
        ),
    }


def _aggregate_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize per-fixture warm medians without entering stable semantics."""
    scored = [row for row in rows if row["score_bearing"]]
    if not scored:
        return {
            "fallback_median_seconds": 0.0,
            "full_seam_median_seconds": 0.0,
            "full_seam_to_fallback_ratio": 0.0,
        }
    fallback = statistics.median(
        row["warm_median_seconds"]["fallback"] for row in scored
    )
    full_seam = statistics.median(
        row["warm_median_seconds"]["full_seam"] for row in scored
    )
    return {
        "fallback_median_seconds": fallback,
        "full_seam_median_seconds": full_seam,
        "full_seam_to_fallback_ratio": (
            full_seam / fallback if fallback else 0.0
        ),
    }


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("schema") != SCHEMA:
        raise ProtocolError("request schema mismatch")
    if request.get("action") != "trafilatura":
        raise ProtocolError("unsupported action")
    reruns = request.get("reruns", 5)
    if not isinstance(reruns, int) or not 1 <= reruns <= 20:
        raise ProtocolError("reruns must be an integer from 1 through 20")
    fixture_root_value = request.get("fixture_root")
    if not isinstance(fixture_root_value, str):
        raise ProtocolError("fixture_root must be a string")
    fixture_root = Path(fixture_root_value)
    fixtures = request.get("fixtures")
    if not isinstance(fixtures, list) or not all(isinstance(row, dict) for row in fixtures):
        raise ProtocolError("fixtures must be an array of objects")

    environment = _environment()
    _validate_environment(request.get("expected_environment"), environment)
    html_semantic: list[dict[str, Any]] = []
    html_timing: list[dict[str, Any]] = []
    feeds: list[dict[str, Any]] = []
    for row in fixtures:
        kind = row.get("kind")
        if kind == "html":
            semantic, timing = _evaluate_html(fixture_root, row, reruns)
            html_semantic.append(semantic)
            html_timing.append(timing)
        elif kind == "feed":
            feeds.append(_evaluate_feed(fixture_root, row))
        else:
            raise ProtocolError(f"unsupported fixture kind: {kind!r}")

    return {
        "schema": SCHEMA,
        "environment": environment,
        "semantic": {
            "feed_fixtures": feeds,
            "html_aggregate": _aggregate_html(html_semantic),
            "html_fixtures": html_semantic,
        },
        "performance": {
            "html_aggregate": _aggregate_performance(html_timing),
            "html_fixtures": html_timing,
        },
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ProtocolError("request must be a JSON object")
        response = evaluate(request)
    except (ProtocolError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        sys.stdout.buffer.write(
            canonical_json_bytes(
                {"schema": SCHEMA, "error": type(exc).__name__, "message": str(exc)}
            )
        )
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
