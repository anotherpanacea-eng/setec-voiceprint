#!/usr/bin/env python3
"""Regression tests for corpus hygiene checking."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_corpus import check_corpus_paths, paths_from_manifest


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
