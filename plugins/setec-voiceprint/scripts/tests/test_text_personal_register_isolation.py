#!/usr/bin/env python3
"""Owner-ruling guard for the profile-only conversational register."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import stylometry_core as sc  # type: ignore
import voice_distance as vd  # type: ignore
import voice_drift_tracker as drift  # type: ignore


def _entry(register: object, *, entry_id: str = "fixture") -> dict:
    return {
        "id": entry_id,
        "path": None,
        "text": "A small synthetic fixture with enough words for the guard.",
        "metadata": {"register": register},
    }


@pytest.mark.parametrize(
    "entries",
    [
        [_entry("message.imessage"), _entry("blog_essay", entry_id="essay")],
        [_entry("message.imessage"), _entry(None, entry_id="missing")],
    ],
)
def test_profile_and_distance_refuse_personal_register_mixture_before_features(
    monkeypatch: pytest.MonkeyPatch, entries: list[dict],
):
    monkeypatch.setattr(
        sc,
        "extract_entry_features",
        lambda *args, **kwargs: pytest.fail("feature extraction ran before guard"),
    )
    with pytest.raises(ValueError, match="message\\.imessage.*profile-only"):
        sc.build_profile(entries, include_spacy=False)
    with pytest.raises(ValueError, match="message\\.imessage.*profile-only"):
        sc.compare_to_baseline("target fixture", entries, include_spacy=False)


def test_personal_only_composition_is_allowed(monkeypatch: pytest.MonkeyPatch):
    seen: list[list[dict]] = []

    def fake_extract(entries, **kwargs):
        seen.append(entries)
        return []

    monkeypatch.setattr(sc, "extract_entry_features", fake_extract)
    monkeypatch.setattr(sc, "select_feature_names", lambda entries, limits=None: {})

    profile = sc.build_profile(
        [_entry("message.imessage"), _entry("message.imessage", entry_id="second")],
        include_spacy=False,
    )
    assert profile["families"] == {}
    assert seen


def test_existing_non_personal_mixture_behavior_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[list[dict]] = []

    def fake_extract(entries, **kwargs):
        seen.append(entries)
        return []

    monkeypatch.setattr(sc, "extract_entry_features", fake_extract)
    monkeypatch.setattr(sc, "select_feature_names", lambda entries, limits=None: {})

    sc.build_profile(
        [_entry("blog_essay"), _entry("policy_brief", entry_id="policy")],
        include_spacy=False,
    )
    assert seen


def test_bootstrap_direct_call_refuses_before_optional_import():
    with pytest.raises(ValueError, match="message\\.imessage.*profile-only"):
        vd.bootstrap_compare(
            "target fixture",
            [_entry("message.imessage"), _entry("blog_essay", entry_id="essay")],
        )


def test_drift_manifest_loader_refuses_mixture(tmp_path: Path):
    manifest = tmp_path / "corpus_manifest.jsonl"
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    manifest.write_text(
        "\n".join(
            [
                '{"id":"first","path":"first.txt","use":["voice_profile"],'
                '"date_written":"2020-01-01","register":"message.imessage"}',
                '{"id":"second","path":"second.txt","use":["voice_profile"],'
                '"date_written":"2020-01-02","register":"blog_essay"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="message\\.imessage.*profile-only"):
        drift._load_manifest_entries(manifest, "voice_profile")


def test_drift_period_profiles_refuse_mixture_before_file_read(tmp_path: Path):
    missing_one = drift.DatedEntry(
        "first", tmp_path / "missing-first.txt", "2020-01-01",
        (2020, 1, 1), {"register": "message.imessage"},
    )
    missing_two = drift.DatedEntry(
        "second", tmp_path / "missing-second.txt", "2020-01-02",
        (2020, 1, 2), {"register": "blog_essay"},
    )
    with pytest.raises(ValueError, match="message\\.imessage.*profile-only"):
        drift.build_period_profiles({"2020": [missing_one, missing_two]})
