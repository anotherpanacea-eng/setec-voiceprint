#!/usr/bin/env python3
"""Regression tests for corpus hygiene checking."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_corpus import (
    LARGE_MANIFEST_WARN_THRESHOLD,
    check_corpus_paths,
    paths_from_manifest,
    warn_if_large_manifest,
)


FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"


def test_contaminated_css_fixture_fails_default_gate() -> None:
    result = check_corpus_paths([CONTAMINATED])

    assert result["status"] == "fail"
    assert result["n_fail"] == 1
    assert result["dominant_rule"] == "css_rule_block"
    assert result["files"][0]["dominant_rule"] == "css_rule_block"
    assert result["files"][0]["strip_ratio"] >= 0.05


def test_clean_fixture_passes_default_gate() -> None:
    result = check_corpus_paths([CLEAN])

    assert result["status"] == "clean"
    assert result["n_clean"] == 1
    assert result["n_fail"] == 0


def test_manifest_filter_loads_paths_for_checking(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "contaminated",
            "path": str(CONTAMINATED),
            "ai_status": "pre_ai_human",
            "use": ["baseline"],
            "privacy": "shareable",
            "split": "baseline",
        })
        + "\n"
        + json.dumps({
            "id": "clean",
            "path": str(CLEAN),
            "ai_status": "pre_ai_human",
            "use": ["validation"],
            "privacy": "shareable",
            "split": "test",
        })
        + "\n",
        encoding="utf-8",
    )

    paths = paths_from_manifest(manifest, "use=baseline")
    assert paths == [CONTAMINATED]
    result = check_corpus_paths(paths)
    assert result["status"] == "fail"


# ----- warn_if_large_manifest ----------------------------------


class _CaptureStream:
    """Tiny stand-in for sys.stderr that records writes for
    assertion."""

    def __init__(self) -> None:
        self._chunks: list[str] = []

    def write(self, s: str) -> None:
        self._chunks.append(s)

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def test_warn_below_threshold_no_output() -> None:
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=100,
        manifest="path/to/manifest.jsonl",
        threshold=1_000_000,
        out=out,
    )
    assert fired is False
    assert out.text == ""


def test_warn_above_threshold_with_manifest_prints_guidance() -> None:
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest="path/to/raid_manifest.jsonl",
        threshold=1_000_000,
        out=out,
    )
    assert fired is True
    text = out.text
    assert "5,000,000" in text
    # The warning must surface the sharded invocation so the
    # operator can copy/paste; without that, the warning is
    # noise rather than discoverability.
    assert "shard_runner" in text
    assert "--task corpus_hygiene" in text
    assert "path/to/raid_manifest.jsonl" in text
    # And it must point at the runbook for the long-form
    # walkthrough.
    assert "RUNBOOK_corpus_hygiene_sharded.md" in text


def test_warn_above_threshold_without_manifest_no_output() -> None:
    """When the operator passed --path or --dir rather than
    --manifest, the sharded path isn't directly applicable (it
    requires a manifest input). Suppress the warning to avoid
    pointing at an inappropriate alternative."""
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest=None,
        threshold=1_000_000,
        out=out,
    )
    assert fired is False
    assert out.text == ""


def test_warn_default_threshold_is_a_million() -> None:
    """The threshold default tracks the practical crossover
    point: MAGE-scale (~436K) doesn't warrant the sharded
    ceremony; an order of magnitude above that is where the
    trade-off flips. Pinning the constant in a test makes the
    decision explicit and reviewable."""
    assert LARGE_MANIFEST_WARN_THRESHOLD == 1_000_000
