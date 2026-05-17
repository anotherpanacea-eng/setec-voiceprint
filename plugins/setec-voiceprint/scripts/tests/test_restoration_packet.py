#!/usr/bin/env python3
"""Regression tests for restoration_packet.py.

Synthetic JSON fixtures under scripts/test_data/restoration_packet/
exercise each input surface and the targetability classification.
The fixtures are crafted to fire specific packet IDs so the tests
can assert that the right top target surfaces from a given input.

The taxonomy itself is the load-bearing thing here: the framework's
metric-gaming resistance lives in correctly classifying signals as
direct / translated / investigate_first / avoid_direct. These tests
guard that classification against drift.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import restoration_packet as rp  # type: ignore


FIXTURE_DIR = ROOT / "test_data" / "restoration_packet"
BIGRAM_FIXTURE = FIXTURE_DIR / "synthetic_bigram_diff.json"
VARIANCE_FIXTURE = FIXTURE_DIR / "synthetic_variance.json"
IDIOLECT_FIXTURE = FIXTURE_DIR / "synthetic_idiolect.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---- Targetability taxonomy ----------------------------------


def test_known_direct_signals_are_classified_direct() -> None:
    """The named direct targets (connective_density, burstiness_B,
    fkgl_sd, etc.) must have entries in DIRECT_TARGETS. If a signal
    silently drops out of the dict, packets_from_variance will
    misclassify it as investigate_first."""
    expected_direct = {
        "connective_density",
        "burstiness_B",
        "fkgl_sd",
        "adjacent_cosine_mean",
        "adjacent_cosine_sd",
        "idiolect_preservation",
        "aic_pattern",
    }
    assert expected_direct <= set(rp.DIRECT_TARGETS)


def test_known_investigate_first_signals_are_classified_so() -> None:
    expected_investigate = {
        "mattr", "mtld", "yules_k", "shannon_entropy",
    }
    assert expected_investigate <= set(rp.INVESTIGATE_FIRST)


def test_known_avoid_direct_signals_are_classified_so() -> None:
    """Aggregate divergence / overall distances must NEVER become
    direct prompt targets. If `pos_bigram_kl_total` ever shows up in
    DIRECT_TARGETS, the framework's metric-gaming resistance has
    silently regressed."""
    assert "pos_bigram_kl_total" in rp.AVOID_DIRECT
    assert "burrows_delta_overall" in rp.AVOID_DIRECT
    assert "char_ngram_distance" in rp.AVOID_DIRECT
    # And these MUST NOT appear in the direct/translated/investigate
    # buckets:
    forbidden = set(rp.AVOID_DIRECT)
    assert forbidden.isdisjoint(rp.DIRECT_TARGETS)
    assert forbidden.isdisjoint(rp.INVESTIGATE_FIRST)


def test_pos_bigram_translations_have_required_fields() -> None:
    for bigram, t in rp.POS_BIGRAM_TRANSLATIONS.items():
        assert "over" in t, f"{bigram} missing 'over' translation"
        assert "under" in t, f"{bigram} missing 'under' translation"
        assert "move" in t, f"{bigram} missing 'move'"


def test_pos_trigram_translations_have_required_fields() -> None:
    for trigram, t in rp.POS_TRIGRAM_TRANSLATIONS.items():
        assert "translation" in t, f"{trigram} missing 'translation'"
        assert "move" in t, f"{trigram} missing 'move'"


# ---- Bigram-diff packet generation ---------------------------


def test_bigram_packet_surfaces_top_translated_target() -> None:
    """The synthetic fixture's top bigram by |kl_contrib| is
    DET-ADJ-NOUN at 0.041 (DET-ADJ is in POS_BIGRAM_TRANSLATIONS,
    but DET-ADJ-NOUN is a TRIGRAM not a bigram, so it won't match
    bigram translations). The next bigrams in the fixture (in order)
    are: ADJ-NOUN (0.037), NOUN-NOUN (0.025), ADV-ADJ (0.013), and
    PRON-VERB (-0.017). The first translated bigram surfaced should
    be ADJ-NOUN over_represented."""
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.packets_from_bigram(bigram, max_translated_targets=3)
    # Drop the trigram-shaped row that doesn't match bigram translations
    actionable = [p for p in packets if p.targetability == "translated"]
    assert actionable, "no translated packets surfaced"
    top = actionable[0]
    assert top.signal == "POS bigram ADJ-NOUN"
    assert top.direction == "over_represented"
    assert top.severity in ("heavy", "moderate", "light")


def test_bigram_packet_translates_under_represented_signed() -> None:
    """PRON-VERB has kl_contrib=-0.017 (under-represented in target).
    The packet must record direction='under_represented' and use the
    translation table's 'under' diagnosis, not 'over'."""
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.packets_from_bigram(bigram, max_translated_targets=10)
    pron_verb = [
        p for p in packets
        if p.signal == "POS bigram PRON-VERB"
    ]
    assert pron_verb, "PRON-VERB packet not surfaced"
    p = pron_verb[0]
    assert p.direction == "under_represented"
    assert "Human actors may be hidden" in p.plain_language_diagnosis


def test_bigram_packet_respects_max_translated_targets() -> None:
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.packets_from_bigram(bigram, max_translated_targets=2)
    translated = [p for p in packets if p.targetability == "translated"]
    assert len(translated) == 2


def test_bigram_packet_skips_unknown_bigrams() -> None:
    """The synthetic fixture includes 'X-Y' as a non-existent bigram.
    It must be skipped — unknown bigrams have no translation, so
    surfacing them would either crash the renderer or expose raw
    POS labels in a writer-facing prompt."""
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.packets_from_bigram(bigram, max_translated_targets=10)
    for p in packets:
        assert "X-Y" not in p.signal


# ---- Variance-audit packet generation ------------------------


def test_variance_flagged_signals_become_packets() -> None:
    """The synthetic fixture flags burstiness_B, connective_density,
    fkgl_sd, mattr, adjacent_cosine_mean. burstiness_B / connective /
    fkgl_sd / adjacent_cosine_mean are direct; mattr is investigate_
    first."""
    variance = _load(VARIANCE_FIXTURE)
    packets = rp.packets_from_variance(variance)
    signals_seen = {p.signal for p in packets}

    # Direct signals
    assert any("burstiness_B" in s for s in signals_seen)
    assert any("connective_density" in s for s in signals_seen)
    assert any("fkgl_sd" in s for s in signals_seen)
    assert any("adjacent_cosine_mean" in s for s in signals_seen)
    # Investigate-first signal
    assert any("mattr" in s for s in signals_seen)


def test_variance_classification_matches_taxonomy() -> None:
    variance = _load(VARIANCE_FIXTURE)
    packets = rp.packets_from_variance(variance)
    by_signal = {p.signal: p for p in packets}
    # Direct
    burst = by_signal.get("variance_audit signal 'burstiness_B'")
    assert burst is not None
    assert burst.targetability == "direct"
    # Investigate-first
    mattr = by_signal.get("variance_audit signal 'mattr'")
    assert mattr is not None
    assert mattr.targetability == "investigate_first"


def test_variance_pos_bigram_kl_aggregate_is_avoid_direct() -> None:
    """The aggregate POS-bigram KL is avoid_direct even when
    compressed; optimizing it directly invites syntactic gaming."""
    variance = _load(VARIANCE_FIXTURE)
    packets = rp.packets_from_variance(variance)
    kl_packets = [p for p in packets if "POS-bigram KL aggregate" in p.signal]
    assert kl_packets, "aggregate KL packet not surfaced"
    assert kl_packets[0].targetability == "avoid_direct"


# ---- Idiolect packet generation -----------------------------


def test_idiolect_packet_surfaces_preservation_list() -> None:
    idiolect = _load(IDIOLECT_FIXTURE)
    packets = rp.packets_from_idiolect(idiolect)
    assert len(packets) == 1
    p = packets[0]
    assert p.targetability == "direct"
    assert "preservation" in p.signal.lower()
    # The packet's evidence carries the count and a preview, not the
    # full list verbatim in revision_moves (which would bloat the
    # prompt and risk leakage in remote-LLM contexts).
    assert p.evidence["n_phrases"] == 7
    assert "phrases_preview" in p.evidence


# ---- Top-level packet assembly ------------------------------


def test_build_packets_orders_by_class_then_severity() -> None:
    variance = _load(VARIANCE_FIXTURE)
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.build_packets(
        variance=variance, bigram=bigram, voice=None,
        idiolect=None, aic=None,
        max_targets=3, targetability_filter=None,
    )
    classes = [p.targetability for p in packets]
    # Direct targets must come before translated, translated before
    # investigate_first, investigate_first before avoid_direct.
    rank = {"direct": 0, "translated": 1, "investigate_first": 2, "avoid_direct": 3}
    for i in range(1, len(packets)):
        assert rank[classes[i]] >= rank[classes[i - 1]], (
            f"out of order at index {i}: {classes}"
        )


def test_build_packets_caps_actionable_at_max_targets() -> None:
    """max_targets caps direct + translated; investigate_first +
    avoid_direct stay as context (not capped)."""
    variance = _load(VARIANCE_FIXTURE)
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.build_packets(
        variance=variance, bigram=bigram, voice=None,
        idiolect=None, aic=None,
        max_targets=2, targetability_filter=None,
    )
    actionable = [p for p in packets if p.targetability in ("direct", "translated")]
    assert len(actionable) <= 2


def test_targetability_filter_actionable_drops_investigate_and_avoid() -> None:
    variance = _load(VARIANCE_FIXTURE)
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.build_packets(
        variance=variance, bigram=bigram, voice=None,
        idiolect=None, aic=None,
        max_targets=10,
        targetability_filter={"direct", "translated"},
    )
    classes = {p.targetability for p in packets}
    assert classes <= {"direct", "translated"}


# ---- Render outputs -----------------------------------------


def test_render_json_includes_required_fields() -> None:
    variance = _load(VARIANCE_FIXTURE)
    packets = rp.build_packets(
        variance=variance, bigram=None, voice=None,
        idiolect=None, aic=None,
        max_targets=3, targetability_filter=None,
    )
    prompt = rp.build_prompt_block(packets, target_scope=None, genre="essay")
    out = rp.render_json(
        packets, prompt, inputs={"variance_json": "x"},
        target_scope=None, genre="essay", private=False,
    )
    parsed = json.loads(out)
    # schema_version 1.0 envelope.
    assert parsed["schema_version"] == "1.0"
    assert parsed["task_surface"] == "craft_restoration"
    assert parsed["tool"] == "restoration_packet"
    assert "claim_license" in parsed
    assert "packets" in parsed["results"]
    assert "prompt" in parsed["results"]


def test_render_markdown_does_not_expose_raw_pos_labels_alone() -> None:
    """When a translated POS-bigram packet renders, it must include a
    plain-language diagnosis (the translation), not just the raw
    POS-tag pair. Exposing 'DET-ADJ' to a writer-facing prompt
    without a gloss invites syntax theater."""
    bigram = _load(BIGRAM_FIXTURE)
    packets = rp.build_packets(
        variance=None, bigram=bigram, voice=None,
        idiolect=None, aic=None,
        max_targets=3, targetability_filter={"translated"},
    )
    prompt = rp.build_prompt_block(packets, target_scope=None, genre=None)
    md = rp.render_markdown(
        packets, prompt, target_scope=None, genre=None,
        private=False, show_poor_targets=False,
    )
    # Find the first translated packet: its diagnosis line must be
    # present (translation, not raw labels alone).
    assert "Diagnosis:" in md
    # The plain-language diagnosis must include words, not just
    # POS labels.
    assert any(
        keyword in md.lower()
        for keyword in (
            "evaluative", "adjective", "noun", "modifier",
            "scaffold", "padding", "stack",
        )
    )


# ---- CLI smoke test -----------------------------------------


def test_cli_main_runs_with_bigram_input(tmp_path) -> None:
    """End-to-end: invoke main() with the bigram fixture, verify the
    JSON output is well-formed and includes at least one translated
    packet."""
    out_md = tmp_path / "packet.md"
    out_json = tmp_path / "packet.json"
    rc = rp.main([
        "--bigram-json", str(BIGRAM_FIXTURE),
        "--out", str(out_md),
        "--json-out", str(out_json),
        "--genre", "essay",
        "--target-scope", "paragraphs 4-8",
    ])
    assert rc == 0
    assert out_md.is_file()
    assert out_json.is_file()
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["schema_version"] == "1.0"
    assert parsed["task_surface"] == "craft_restoration"
    assert parsed["results"]["n_packets"] >= 1
    # At least one translated packet should land.
    classes = {p["targetability"] for p in parsed["results"]["packets"]}
    assert "translated" in classes


def test_cli_refuses_zero_inputs(tmp_path, capsys) -> None:
    rc = rp.main([
        "--out", str(tmp_path / "packet.md"),
    ])
    assert rc == 1
    captured = capsys.readouterr() if pytest is not None else None
    if captured:
        assert "required" in captured.err.lower()
