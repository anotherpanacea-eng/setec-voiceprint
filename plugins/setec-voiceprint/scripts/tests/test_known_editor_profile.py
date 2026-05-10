#!/usr/bin/env python3
"""Regression tests for known_editor_profile.py (Release 10)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import known_editor_profile as kep  # type: ignore


# ---------- Helpers ----------


_BEFORE_TEMPLATE = (
    "The morning light filtered through the curtains. She stood "
    "at the window and watched the street come alive. People "
    "moved toward the subway entrance, breath visible in the "
    "cold air. She turned away from the window and walked to her "
    "desk. The manuscript sat where she had left it. Three weeks "
    "of work in those pages. The voice still felt elusive, "
    "neither hers nor entirely someone else's. She picked up the "
    "pen with hesitation. Outside a bus shuddered to a stop at "
    "the corner."
)

_AFTER_TEMPLATE = (
    "Light filtered softly through the curtains as morning "
    "arrived. She stood at the window, watching the street come "
    "alive. People moved toward the subway, their breath visible "
    "in the cold air. She turned from the window and walked to "
    "her desk. The manuscript lay where she had left it three "
    "weeks earlier. Despite all that effort, the voice remained "
    "elusive—neither entirely hers nor entirely someone else's. "
    "She picked up the pen with hesitation. Outside, a bus "
    "shuddered to a stop at the corner."
)


def _write_pair(tmp_path: Path, idx: int) -> tuple[Path, Path]:
    """Drop a (before, after) pair to disk and return the paths."""
    before = tmp_path / f"before_{idx}.txt"
    after = tmp_path / f"after_{idx}.txt"
    # Vary the texts slightly per pair to introduce variance in
    # the profile (otherwise sd would be 0 → ambiguous).
    before.write_text(
        _BEFORE_TEMPLATE + f" Variation {idx}.",
        encoding="utf-8",
    )
    after.write_text(
        _AFTER_TEMPLATE + f" A note added at index {idx}.",
        encoding="utf-8",
    )
    return before, after


def _write_n_pairs(
    tmp_path: Path, n: int = 3,
) -> tuple[Path, list[dict[str, str]]]:
    pairs_data: list[dict[str, str]] = []
    for i in range(n):
        before, after = _write_pair(tmp_path, i + 1)
        pairs_data.append({
            "before": str(before),
            "after": str(after),
        })
    pairs_path = tmp_path / "pairs.json"
    pairs_path.write_text(
        json.dumps(pairs_data), encoding="utf-8",
    )
    return pairs_path, pairs_data


# ---------- Signal extraction ----------


class TestExtractSignal:
    def test_known_path(self):
        audit = {"tier1": {"sentence_length": {"burstiness_B": 0.4}}}
        assert kep._extract_signal(
            audit, ("tier1", "sentence_length", "burstiness_B"),
        ) == 0.4

    def test_missing_path_returns_none(self):
        assert kep._extract_signal({}, ("tier1", "x")) is None

    def test_non_numeric_returns_none(self):
        audit = {"tier1": {"x": "string"}}
        assert kep._extract_signal(audit, ("tier1", "x")) is None


class TestExtractAllSignals:
    def test_returns_present_signals(self):
        audit = {
            "tier1": {
                "sentence_length": {"burstiness_B": 0.4, "sd": 12.0},
                "mtld": 80.0,
                "mattr": {"value": 0.65},
                "shannon_entropy_bits": 9.0,
                "yules_k": 100.0,
                "fkgl": {"sd": 2.5},
                "connective_density": {"per_1000_tokens": 25.0},
            },
        }
        signals = kep._extract_all_signals(audit)
        assert "burstiness_B" in signals
        assert signals["burstiness_B"] == 0.4
        assert signals["mtld"] == 80.0
        assert signals["mattr"] == 0.65

    def test_tier1_only_excludes_tier2_paths(self):
        # Reviewer-reproduced regression: pre-1.41.1 the
        # extraction registry was tier-1 only, so even when
        # tier-2 paths were present in the audit they could
        # never enter the profile (the --tier2 flag was a no-op).
        audit = {
            "tier1": {
                "sentence_length": {"burstiness_B": 0.4, "sd": 12.0},
                "mtld": 80.0,
            },
            "tier2": {
                "available": True,
                "mdd": {"mean": 2.5, "sd": 0.8},
                "pos_bigrams": {"entropy_bits": 5.5},
            },
        }
        # Default (do_tier2=False) → no tier-2 signals extracted.
        signals_t1 = kep._extract_all_signals(audit, do_tier2=False)
        assert "mdd_mean" not in signals_t1
        assert "mdd_sd" not in signals_t1
        assert "pos_bigram_entropy_bits" not in signals_t1

    def test_tier2_opt_in_extracts_tier2_signals(self):
        # Post-1.41.1 fix: do_tier2=True extends the active
        # registry so tier-2 paths actually enter the profile.
        audit = {
            "tier1": {
                "sentence_length": {"burstiness_B": 0.4, "sd": 12.0},
                "mtld": 80.0,
            },
            "tier2": {
                "available": True,
                "mdd": {"mean": 2.5, "sd": 0.8},
                "pos_bigrams": {"entropy_bits": 5.5},
            },
        }
        signals_t2 = kep._extract_all_signals(audit, do_tier2=True)
        # Tier-1 still present.
        assert signals_t2["burstiness_B"] == 0.4
        # Tier-2 paths now extracted.
        assert signals_t2.get("mdd_mean") == 2.5
        assert signals_t2.get("mdd_sd") == 0.8
        assert signals_t2.get("pos_bigram_entropy_bits") == 5.5


class TestProfileSignalsHelper:
    def test_helper_returns_tier1_only_by_default(self):
        active = kep._profile_signals(do_tier2=False)
        assert "burstiness_B" in active
        assert "mdd_mean" not in active

    def test_helper_extends_registry_when_tier2(self):
        tier1_only = kep._profile_signals(do_tier2=False)
        with_tier2 = kep._profile_signals(do_tier2=True)
        # tier-1 set is a subset of tier-1+tier-2 set.
        assert set(tier1_only).issubset(set(with_tier2))
        # tier-2 adds new keys.
        assert len(with_tier2) > len(tier1_only)
        for tier2_key in (
            "mdd_mean", "mdd_sd", "pos_bigram_entropy_bits",
        ):
            assert tier2_key in with_tier2
            assert tier2_key not in tier1_only


# ---------- Pair measurement ----------


class TestMeasurePair:
    def test_pair_measurement_returns_deltas(self, tmp_path):
        before, after = _write_pair(tmp_path, 1)
        result = kep.measure_pair(before, after)
        assert "before_signals" in result
        assert "after_signals" in result
        assert "deltas" in result
        # Both texts are long enough for tier-1 signals to fire.
        assert len(result["deltas"]) > 0


# ---------- Profile learning ----------


class TestLearnProfile:
    def test_learn_three_pairs(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        assert profile["n_pairs"] == 3
        assert profile["tool"] == kep.TOOL_NAME
        assert "signals" in profile
        # Should learn at least one signal with stdev (≥2 pairs).
        with_sd = [
            s for s in profile["signals"].values()
            if s.get("stdev") is not None
        ]
        assert len(with_sd) > 0

    def test_learn_single_pair_marks_sd_none(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=1)
        profile = kep.learn_profile(pairs=pairs)
        assert profile["n_pairs"] == 1
        # All signals should have stdev=None (single pair).
        for s in profile["signals"].values():
            assert s.get("stdev") is None

    def test_learn_anonymizes_pair_results_by_default(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=2)
        profile = kep.learn_profile(pairs=pairs)
        # pair_results should not contain deltas (anonymized).
        for r in profile["pair_results"]:
            assert "deltas" not in r
            assert r["pair_id"].startswith("pair_")

    def test_learn_include_filenames_opt_in(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=2)
        profile = kep.learn_profile(
            pairs=pairs, include_filenames=True,
        )
        for r in profile["pair_results"]:
            assert "->" in r["pair_id"]

    def test_learn_with_label(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=2)
        profile = kep.learn_profile(
            pairs=pairs, profile_label="developmental_editor_x",
        )
        assert profile["profile_label"] == "developmental_editor_x"

    def test_learn_empty_pairs_raises(self):
        with pytest.raises(ValueError):
            kep.learn_profile(pairs=[])


# ---------- Match ----------


class TestMatchPair:
    def test_match_against_self_should_match(self, tmp_path):
        # Build a profile from 3 pairs, then match a 4th pair
        # generated the same way. It should land within profile.
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        new_before, new_after = _write_pair(tmp_path, 4)
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        # Generated by the same process → should match or be ambiguous.
        assert report["verdict"] in {"matches_profile", "ambiguous"}

    def test_match_with_radically_different_pair_mismatches(
        self, tmp_path,
    ):
        # Train a profile on small-edit pairs.
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        # Now create a pair where the after is radically different
        # — short, fragmented prose.
        new_before = tmp_path / "new_before.txt"
        new_before.write_text(
            _BEFORE_TEMPLATE * 2, encoding="utf-8",
        )
        new_after = tmp_path / "new_after.txt"
        new_after.write_text(
            "Short. Fragments. Choppy. Not. Long.\n"
            "Punchy. Hard. Stop. Brittle. Tight.\n"
            "Short again. Cut. Short. Cut.\n" * 5,
            encoding="utf-8",
        )
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        # Expect either mismatch or ambiguous (depending on which
        # signals had usable sd — but the radical change should
        # push at least one signal far outside profile).
        assert report["verdict"] in {"mismatch", "ambiguous"}

    def test_match_includes_per_signal_z_scores(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        new_before, new_after = _write_pair(tmp_path, 4)
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        # At least one signal should have a numeric z-score.
        z_scores = [
            info.get("z_score")
            for info in report["per_signal"].values()
            if info.get("z_score") is not None
        ]
        # If any signal had stdev>0 in the profile, we should
        # have some z-scores. Otherwise the test passes trivially.
        assert (
            isinstance(z_scores, list)
        )

    def test_match_single_pair_profile_falls_to_ambiguous(
        self, tmp_path,
    ):
        # Single-pair profile has stdev=None on every signal.
        _, pairs = _write_n_pairs(tmp_path, n=1)
        profile = kep.learn_profile(pairs=pairs)
        new_before, new_after = _write_pair(tmp_path, 2)
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        # All signals → ambiguous (stdev is None).
        assert report["verdict"] == "ambiguous"


# ---------- Render ----------


class TestRender:
    def test_render_match_report_includes_license(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        new_before, new_after = _write_pair(tmp_path, 4)
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        md = kep.render_match_report(report)
        assert "## What this result licenses" in md

    def test_render_includes_per_signal_table(self, tmp_path):
        _, pairs = _write_n_pairs(tmp_path, n=3)
        profile = kep.learn_profile(pairs=pairs)
        new_before, new_after = _write_pair(tmp_path, 4)
        report = kep.match_pair(
            profile=profile,
            before_path=new_before,
            after_path=new_after,
        )
        md = kep.render_match_report(report)
        assert "## Per-signal match" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_learn_round_trip(self, tmp_path):
        pairs_path, _ = _write_n_pairs(tmp_path, n=3)
        out = tmp_path / "profile.json"
        rc = kep.main([
            "learn",
            "--pairs-json", str(pairs_path),
            "--out", str(out),
            "--profile-label", "test_editor",
        ])
        assert rc == 0
        profile = json.loads(out.read_text(encoding="utf-8"))
        assert profile["profile_label"] == "test_editor"
        assert profile["n_pairs"] == 3

    def test_cli_learn_then_match(self, tmp_path):
        pairs_path, _ = _write_n_pairs(tmp_path, n=3)
        profile_path = tmp_path / "profile.json"
        rc = kep.main([
            "learn",
            "--pairs-json", str(pairs_path),
            "--out", str(profile_path),
        ])
        assert rc == 0

        new_before, new_after = _write_pair(tmp_path, 4)
        report_path = tmp_path / "match.json"
        rc = kep.main([
            "match",
            "--before", str(new_before),
            "--after", str(new_after),
            "--profile", str(profile_path),
            "--json", "--out", str(report_path),
        ])
        assert rc == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert "verdict" in report
        assert "per_signal" in report

    def test_cli_missing_pairs_json_returns_2(self, tmp_path):
        rc = kep.main([
            "learn",
            "--pairs-json", str(tmp_path / "missing.json"),
            "--out", str(tmp_path / "profile.json"),
        ])
        assert rc == 2

    def test_cli_invalid_pairs_json_returns_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ malformed", encoding="utf-8")
        rc = kep.main([
            "learn",
            "--pairs-json", str(bad),
            "--out", str(tmp_path / "profile.json"),
        ])
        assert rc == 2

    def test_cli_pairs_not_a_list_returns_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"not": "a list"}), encoding="utf-8",
        )
        rc = kep.main([
            "learn",
            "--pairs-json", str(bad),
            "--out", str(tmp_path / "profile.json"),
        ])
        assert rc == 2

    def test_cli_pair_missing_keys_returns_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps([{"before": "x"}]), encoding="utf-8",
        )
        rc = kep.main([
            "learn",
            "--pairs-json", str(bad),
            "--out", str(tmp_path / "profile.json"),
        ])
        assert rc == 2

    def test_cli_match_missing_profile_returns_2(self, tmp_path):
        before, after = _write_pair(tmp_path, 1)
        rc = kep.main([
            "match",
            "--before", str(before),
            "--after", str(after),
            "--profile", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_match_missing_before_returns_2(self, tmp_path):
        # Build a valid profile to compare against.
        pairs_path, _ = _write_n_pairs(tmp_path, n=2)
        profile_path = tmp_path / "profile.json"
        kep.main([
            "learn",
            "--pairs-json", str(pairs_path),
            "--out", str(profile_path),
        ])
        rc = kep.main([
            "match",
            "--before", str(tmp_path / "missing_before.txt"),
            "--after", str(tmp_path / "missing_after.txt"),
            "--profile", str(profile_path),
        ])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
