#!/usr/bin/env python3
"""Owner-ruling guard for the profile-only conversational register."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import stylometry_core as sc  # type: ignore
import voice_distance as vd  # type: ignore
import voice_drift_tracker as drift  # type: ignore
from register_taxonomy import REGISTER_TIERS  # type: ignore


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
        [_entry("message.facebook_messenger"), _entry("blog_essay", entry_id="essay")],
        [_entry("message.facebook_messenger"), _entry(None, entry_id="missing")],
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
    with pytest.raises(ValueError, match="private-dyadic.*profile-only"):
        sc.build_profile(entries, include_spacy=False)
    with pytest.raises(ValueError, match="private-dyadic.*profile-only"):
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


def test_same_tier_cross_leaf_composition_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[list[dict]] = []

    def fake_extract(entries, **kwargs):
        seen.append(entries)
        return []

    monkeypatch.setattr(sc, "extract_entry_features", fake_extract)
    monkeypatch.setattr(sc, "select_feature_names", lambda entries, limits=None: {})

    profile = sc.build_profile(
        [
            _entry("message.imessage"),
            _entry("message.facebook_messenger", entry_id="messenger"),
        ],
        include_spacy=False,
    )
    assert profile["families"] == {}
    assert seen


def test_conflicting_register_shapes_refuse_before_features(
    monkeypatch: pytest.MonkeyPatch,
):
    entry = _entry("blog_essay")
    entry["register"] = "message.facebook_messenger"
    monkeypatch.setattr(
        sc,
        "extract_entry_features",
        lambda *args, **kwargs: pytest.fail("feature extraction ran before guard"),
    )
    with pytest.raises(ValueError, match="conflicting.*register"):
        sc.build_profile([entry], include_spacy=False)


def test_direct_extractor_refuses_conflict_before_text_features(
    monkeypatch: pytest.MonkeyPatch,
):
    entry = _entry("blog_essay")
    entry["register"] = "message.facebook_messenger"
    monkeypatch.setattr(
        sc,
        "extract_features",
        lambda *args, **kwargs: pytest.fail("text feature extraction ran"),
    )
    with pytest.raises(ValueError, match="conflicting.*register"):
        sc.extract_entry_features([entry], include_spacy=False)


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


def test_public_responsive_stays_in_pooled_reference(
    monkeypatch: pytest.MonkeyPatch,
):
    seen: list[list[dict]] = []

    def fake_extract(entries, **kwargs):
        seen.append(entries)
        return []

    monkeypatch.setattr(sc, "extract_entry_features", fake_extract)
    monkeypatch.setattr(sc, "select_feature_names", lambda entries, limits=None: {})

    sc.build_profile(
        [
            _entry("social_media_twitter"),
            _entry("blog_essay", entry_id="essay"),
            _entry("social_media_facebook_comments", entry_id="comments"),
        ],
        include_spacy=False,
    )
    assert seen

    summary = sc.summarize_entries([
        {
            "id": "twitter",
            "path": "twitter.txt",
            "summary": {"n_words": 20},
            "metadata": {"register": "social_media_twitter"},
        },
        {
            "id": "essay",
            "path": "essay.txt",
            "summary": {"n_words": 40},
            "metadata": {"register": "blog_essay"},
        },
        {
            "id": "comments",
            "path": "comments.txt",
            "summary": {"n_words": 30},
            "metadata": {"register": "social_media_facebook_comments"},
        },
    ])
    assert summary["register_tier_counts"] == {
        "private_composed": 0,
        "private_dyadic": 0,
        "public_composed": 1,
        "public_responsive": 2,
    }
    assert summary["unresolved_register_count"] == 0


def test_top_level_only_register_survives_into_tier_receipt(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        sc,
        "extract_features",
        lambda *args, **kwargs: {
            "summary": {"n_words": 9},
            "features": {},
            "preprocessing": {},
        },
    )
    features = sc.extract_entry_features(
        [{
            "id": "essay",
            "path": "essay.txt",
            "text": "A synthetic top-level register fixture.",
            "register": "blog_essay",
        }],
        include_spacy=False,
    )
    summary = sc.summarize_entries(features)
    assert summary["register_tier_counts"]["public_composed"] == 1
    assert summary["unresolved_register_count"] == 0


def test_unresolved_registers_are_counted_not_silently_dropped():
    """Pin a NON-ZERO unresolved count.

    ``unresolved_register_count`` is the only receipt telling a consumer
    that a pooled reference holds entries whose privacy tier could not be
    resolved — a fail-open indicator. Every other assertion on the field
    in this repo is ``== 0``, which a counter that never increments would
    satisfy forever. The field deliberately conflates two cases (no
    register declared at all; a register declared but absent from the
    closed registry), so both are exercised here.
    """
    summary = sc.summarize_entries([
        {"id": "a", "path": "a.txt", "summary": {"n_words": 10},
         "metadata": {}},
        {"id": "b", "path": "b.txt", "summary": {"n_words": 10},
         "metadata": {"register": "not.registered"}},
        {"id": "c", "path": "c.txt", "summary": {"n_words": 10},
         "metadata": {"register": "blog_essay"}},
    ])
    assert summary["unresolved_register_count"] == 2
    assert summary["register_tier_counts"]["public_composed"] == 1
    assert sum(summary["register_tier_counts"].values()) == 1
    # Every entry lands in exactly one bucket: tiered or unresolved.
    assert (
        sum(summary["register_tier_counts"].values())
        + summary["unresolved_register_count"]
        == summary["n_files"]
    )


def test_directory_mode_baseline_reports_every_file_unresolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Pin the documented directory-mode behavior.

    ``load_entries_from_dir`` attaches only ``{"source": "directory"}``,
    so a directory-mode baseline declares no register anywhere. The
    released changelog states such a baseline reports zero tier counts
    and an ``unresolved_register_count`` equal to ``n_files``; that
    claim had no test.
    """
    (tmp_path / "first.txt").write_text("first fixture body", encoding="utf-8")
    (tmp_path / "second.md").write_text("second fixture body", encoding="utf-8")

    entries = sc.load_entries_from_dir(tmp_path)
    assert [entry["metadata"] for entry in entries] == [
        {"source": "directory"}, {"source": "directory"},
    ]

    monkeypatch.setattr(
        sc,
        "extract_features",
        lambda *args, **kwargs: {
            "summary": {"n_words": 3},
            "features": {},
            "preprocessing": {},
        },
    )
    summary = sc.summarize_entries(
        sc.extract_entry_features(entries, include_spacy=False)
    )
    assert summary["n_files"] == 2
    assert summary["unresolved_register_count"] == summary["n_files"]
    assert summary["register_tier_counts"] == {
        tier: 0 for tier in REGISTER_TIERS
    }


@pytest.mark.parametrize(
    "register", ["message.imessage", "message.facebook_messenger"]
)
def test_bootstrap_direct_call_refuses_before_optional_import(register: str):
    with pytest.raises(ValueError, match="private-dyadic.*profile-only"):
        vd.bootstrap_compare(
            "target fixture",
            [_entry(register), _entry("blog_essay", entry_id="essay")],
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
    with pytest.raises(ValueError, match="private-dyadic.*profile-only"):
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
    with pytest.raises(ValueError, match="private-dyadic.*profile-only"):
        drift.build_period_profiles({"2020": [missing_one, missing_two]})
