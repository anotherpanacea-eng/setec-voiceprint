#!/usr/bin/env python3
"""Focused contract coverage for the optional closed source-family axis."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


def _validate(tmp_path: Path, source_family=Ellipsis, **extra):
    source = tmp_path / "source.txt"
    source.write_text("A small, valid corpus entry.", encoding="utf-8")
    entry = {
        "id": "entry-1",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        **extra,
    }
    if source_family is not Ellipsis:
        entry["source_family"] = source_family
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return mv.validate_manifest(manifest)


@pytest.mark.parametrize(
    "source_family",
    ["facebook", "metafilter", "wordpress", "unclassified"],
)
def test_allowed_source_family_is_accepted_without_issue(
    tmp_path: Path, source_family: str
):
    result = _validate(tmp_path, source_family)

    assert result["n_errors"] == 0
    assert not [i for i in result["issues"] if i["field"] == "source_family"]


def test_source_family_remains_optional(tmp_path: Path):
    result = _validate(tmp_path)

    assert result["n_errors"] == 0
    assert not [i for i in result["issues"] if i["field"] == "source_family"]


@pytest.mark.parametrize(
    "source_family",
    [None, 7, [], "", " ", " facebook", "facebook ", "dropbox"],
)
def test_invalid_source_family_is_rejected(tmp_path: Path, source_family):
    result = _validate(tmp_path, source_family)

    issues = [i for i in result["issues"] if i["field"] == "source_family"]
    assert len(issues) == 1
    assert issues[0]["severity"] == "error"
    assert result["n_errors"] == 1


def test_source_family_does_not_constrain_source_id_or_register(tmp_path: Path):
    result = _validate(
        tmp_path,
        "facebook",
        source_id="opaque-document-42",
        register="teaching",
    )

    assert result["n_errors"] == 0
    assert not [i for i in result["issues"] if i["field"] == "source_family"]
