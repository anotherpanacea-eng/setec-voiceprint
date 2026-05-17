#!/usr/bin/env python3
"""Regression tests for draft_history_analysis.py (Release 11)."""

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

import draft_history_analysis as dha  # type: ignore


# ---------- Helpers ----------


_BASE_PROSE = (
    "The morning light filtered through the curtains. She stood "
    "at the window and watched the street come alive. People "
    "moved toward the subway entrance, breath visible in the "
    "cold air. She turned away from the window and walked to her "
    "desk. The manuscript sat where she had left it. Three weeks "
    "of work in those pages. The voice still felt elusive, "
    "neither hers nor entirely someone else's. She picked up the "
    "pen with hesitation. Outside a bus shuddered to a stop at "
    "the corner. The afternoon would arrive too soon."
)


def _write_versions(
    tmp_path: Path, n: int = 4,
) -> tuple[Path, list[dict[str, str]]]:
    """Drop n version files and return the manifest path + list."""
    manifest: list[dict[str, str]] = []
    for i in range(n):
        path = tmp_path / f"v{i + 1}.txt"
        # Vary the texts so trajectory has measurable deltas.
        # Each version slightly extends or rewords the base prose.
        text = _BASE_PROSE + (
            f" Version {i + 1} adds a sentence here. "
            * (i + 1)
        )
        path.write_text(text, encoding="utf-8")
        manifest.append({
            "label": f"v{i + 1}",
            "path": str(path),
        })
    manifest_path = tmp_path / "versions.json"
    manifest_path.write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    return manifest_path, manifest


# ---------- Signal extraction ----------


class TestExtractSignal:
    def test_known_path_returns_value(self):
        audit = {
            "tier1": {"sentence_length": {"burstiness_B": 0.4}},
        }
        assert dha._extract_signal(
            audit, ("tier1", "sentence_length", "burstiness_B"),
        ) == 0.4

    def test_missing_path_returns_none(self):
        assert dha._extract_signal({}, ("tier1", "x")) is None


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
        signals = dha._extract_all_signals(audit)
        assert "burstiness_B" in signals
        assert signals["mtld"] == 80.0


# ---------- Per-version measurement ----------


class TestMeasureVersion:
    def test_returns_signals_dict(self, tmp_path):
        m = dha.measure_version(_BASE_PROSE)
        assert "n_words" in m
        assert "signals" in m
        # Tier-1 signals should be present given the prose length.
        assert len(m["signals"]) > 0


# ---------- Trajectory verdict classification ----------


class TestClassifyTrajectoryVerdict:
    def test_stable_when_all_within_floor(self):
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[0.01, -0.02, 0.005],
            noise_floor=0.05,
        )
        assert verdict == "stable_throughout"

    def test_sudden_shift_when_one_dominates(self):
        # One delta dominates: 1.0 vs. 0.01 mean.
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[0.01, 1.0, 0.01],
            noise_floor=0.05,
        )
        assert verdict == "sudden_shift"

    def test_gradual_drift_when_consistent_no_dominant(self):
        # Three similar-sized deltas above floor, none dominant.
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[0.10, 0.12, 0.11],
            noise_floor=0.05,
        )
        assert verdict == "gradual_drift"

    def test_restored_after_drift_when_sign_reverses(self):
        # Sum cancels (cumulative within floor); sign reversal
        # with deltas above floor.
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[0.5, -0.5, 0.0],
            noise_floor=0.05,
        )
        assert verdict == "restored_after_drift"

    def test_unknown_when_no_deltas(self):
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[None, None],
            noise_floor=0.05,
        )
        assert verdict == "unknown"

    def test_single_pair_above_floor_is_sudden(self):
        verdict, _ = dha._classify_trajectory_verdict(
            deltas=[0.5],
            noise_floor=0.05,
        )
        assert verdict == "sudden_shift"


# ---------- build_trajectory ----------


class TestBuildTrajectory:
    def test_two_versions_produces_one_pair(self, tmp_path):
        v1 = dha.measure_version(_BASE_PROSE)
        v2 = dha.measure_version(_BASE_PROSE + " Extra prose.")
        report = dha.build_trajectory(
            versions=[
                {"label": "v1", **v1},
                {"label": "v2", **v2},
            ],
        )
        assert report["n_versions"] == 2
        assert len(report["pair_labels"]) == 1
        assert report["pair_labels"][0] == "v1→v2"

    def test_trajectories_have_per_signal_blocks(self, tmp_path):
        m1 = dha.measure_version(_BASE_PROSE)
        m2 = dha.measure_version(_BASE_PROSE + " More text.")
        report = dha.build_trajectory(
            versions=[
                {"label": "v1", **m1},
                {"label": "v2", **m2},
            ],
        )
        traj = report["trajectories"]
        assert "mtld" in traj
        # Each trajectory has values, deltas, verdict.
        for sig_info in traj.values():
            assert "values" in sig_info
            assert "deltas" in sig_info
            assert "verdict" in sig_info
            assert "noise_floor" in sig_info

    def test_single_version_raises(self):
        m = dha.measure_version(_BASE_PROSE)
        with pytest.raises(ValueError):
            dha.build_trajectory(
                versions=[{"label": "v1", **m}],
            )

    def test_summary_counts_verdicts(self):
        # Synthetic trajectory: two stable signals, one sudden.
        v1 = {
            "label": "v1",
            "n_words": 100,
            "signals": {
                "burstiness_B": 0.40,
                "mtld": 80.0,
                "mattr": 0.65,
            },
        }
        v2 = {
            "label": "v2",
            "n_words": 100,
            "signals": {
                "burstiness_B": 0.41,  # within floor
                "mtld": 200.0,         # +120 → way above floor 5
                "mattr": 0.66,          # within floor
            },
        }
        report = dha.build_trajectory(versions=[v1, v2])
        # 8 tier-1 signals tracked in registry, only 3 supplied
        # → 5 register as `unknown` (None values), the 3 supplied
        # produce per-signal verdicts based on the deltas.
        summary = report["summary"]
        assert summary["n_signals"] == 8
        # mtld has a sudden shift; burstiness + mattr stable.
        assert summary["n_sudden_shift"] >= 1
        assert summary["n_stable_throughout"] >= 1


# ---------- Render ----------


class TestRender:
    def test_render_includes_claim_license(self, tmp_path):
        m1 = dha.measure_version(_BASE_PROSE)
        m2 = dha.measure_version(_BASE_PROSE + " More.")
        report = dha.build_trajectory(
            versions=[
                {"label": "v1", **m1},
                {"label": "v2", **m2},
            ],
        )
        md = dha.render_report(report)
        assert "## What this result licenses" in md

    def test_render_per_signal_table(self, tmp_path):
        m1 = dha.measure_version(_BASE_PROSE)
        m2 = dha.measure_version(_BASE_PROSE + " Some more.")
        report = dha.build_trajectory(
            versions=[
                {"label": "v1", **m1},
                {"label": "v2", **m2},
            ],
        )
        md = dha.render_report(report)
        assert "## Per-signal trajectory" in md
        assert "v1" in md
        assert "v2" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        manifest, _ = _write_versions(tmp_path, n=4)
        out = tmp_path / "trajectory.json"
        rc = dha.main([
            "--versions-json", str(manifest),
            "--json", "--out", str(out),
        ])
        assert rc == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        # schema_version 1.0 envelope: n_versions and trajectories
        # live under results.
        assert report["schema_version"] == "1.0"
        assert report["task_surface"] == "validation"
        assert report["tool"] == "draft_history_analysis"
        assert report["results"]["n_versions"] == 4
        assert "trajectories" in report["results"]

    def test_cli_two_versions_minimum(self, tmp_path):
        manifest, _ = _write_versions(tmp_path, n=2)
        rc = dha.main([
            "--versions-json", str(manifest),
        ])
        assert rc == 0

    def test_cli_single_version_returns_2(self, tmp_path):
        manifest_data = [{
            "label": "v1",
            "path": str(tmp_path / "v1.txt"),
        }]
        (tmp_path / "v1.txt").write_text(
            _BASE_PROSE, encoding="utf-8",
        )
        manifest = tmp_path / "versions.json"
        manifest.write_text(
            json.dumps(manifest_data), encoding="utf-8",
        )
        rc = dha.main([
            "--versions-json", str(manifest),
        ])
        assert rc == 2

    def test_cli_missing_versions_json(self, tmp_path):
        rc = dha.main([
            "--versions-json", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_malformed_versions_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ malformed", encoding="utf-8")
        rc = dha.main([
            "--versions-json", str(bad),
        ])
        assert rc == 2

    def test_cli_versions_not_a_list(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"not": "a list"}), encoding="utf-8",
        )
        rc = dha.main([
            "--versions-json", str(bad),
        ])
        assert rc == 2

    def test_cli_entry_missing_keys(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps([{"label": "v1"}]), encoding="utf-8",
        )
        rc = dha.main([
            "--versions-json", str(bad),
        ])
        assert rc == 2

    def test_cli_missing_version_file(self, tmp_path):
        manifest = tmp_path / "versions.json"
        manifest.write_text(
            json.dumps([
                {"label": "v1", "path": str(tmp_path / "v1.txt")},
                {"label": "v2", "path": str(tmp_path / "missing.txt")},
            ]),
            encoding="utf-8",
        )
        (tmp_path / "v1.txt").write_text(
            _BASE_PROSE, encoding="utf-8",
        )
        rc = dha.main([
            "--versions-json", str(manifest),
        ])
        assert rc == 2

    def test_cli_empty_version_file(self, tmp_path):
        manifest = tmp_path / "versions.json"
        manifest.write_text(
            json.dumps([
                {"label": "v1", "path": str(tmp_path / "v1.txt")},
                {"label": "v2", "path": str(tmp_path / "v2.txt")},
            ]),
            encoding="utf-8",
        )
        (tmp_path / "v1.txt").write_text(
            _BASE_PROSE, encoding="utf-8",
        )
        (tmp_path / "v2.txt").write_text("", encoding="utf-8")
        rc = dha.main([
            "--versions-json", str(manifest),
        ])
        assert rc == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
