#!/usr/bin/env python3
"""Regression tests for validation-harness corpus-hygiene preflight."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "validation_harness.py"
FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"


def write_manifest(path: Path, sample_path: Path) -> None:
    path.write_text(
        json.dumps({
            "id": "sample",
            "path": str(sample_path),
            "ai_status": "pre_ai_human",
            "use": ["validation"],
            "split": "test",
            "privacy": "shareable",
            "register": "blog_essay",
            "language_status": "native",
        })
        + "\n",
        encoding="utf-8",
    )


def run_harness(manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            str(manifest),
            "--no-tier2",
            "--no-tier3",
            "--metric-bootstrap-resamples",
            "0",
            "--check-corpus",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_validation_harness_check_corpus_fails_contaminated_entry(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, CONTAMINATED)

    proc = run_harness(manifest)
    result = json.loads(proc.stdout)

    assert proc.returncode == 1
    assert result["failed"] is True
    assert result["reason"] == "corpus hygiene check failed"
    assert result["corpus_hygiene"]["status"] == "fail"
    assert result["corpus_hygiene"]["dominant_rule"] == "css_rule_block"


def test_validation_harness_check_corpus_allows_clean_entry(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, CLEAN)

    proc = run_harness(manifest)
    result = json.loads(proc.stdout)

    assert proc.returncode == 0
    assert result["corpus_hygiene"]["checked"] is True
    assert result["corpus_hygiene"]["status"] == "clean"
    assert result["n_validation_entries"] == 1
