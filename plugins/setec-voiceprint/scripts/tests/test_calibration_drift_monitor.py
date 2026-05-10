#!/usr/bin/env python3
"""Regression tests for calibration_drift_monitor.py (Release 9)."""

from __future__ import annotations

import copy
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

import calibration_drift_monitor as cdm  # type: ignore


# ---------- Helpers ----------


_SAMPLE_TEXT = (
    "The morning was clear. Light filtered through the curtains. "
    "She walked to the window and watched the street. People were "
    "moving toward the subway, their breath visible in the cold. "
    "She turned back to her desk. The manuscript lay open. She "
    "had been working on the second chapter for three weeks now. "
    "Each sentence had been rewritten at least twice. The voice "
    "still felt elusive. She picked up the pen. Outside, a bus "
    "shuddered to a stop. The day was beginning. Slowly, she "
    "started to write again. The words came reluctantly at first, "
    "then with more confidence as the paragraph took shape. "
    "Coffee cooled beside her. The hour passed quickly."
)


def _write_benchmarks(tmp_path: Path) -> Path:
    """Drop a small benchmark directory and return the dir path."""
    bdir = tmp_path / "benchmarks"
    bdir.mkdir()
    (bdir / "bench_a.txt").write_text(_SAMPLE_TEXT, encoding="utf-8")
    (bdir / "bench_b.txt").write_text(
        _SAMPLE_TEXT + " " + _SAMPLE_TEXT, encoding="utf-8",
    )
    return bdir


# ---------- Stack and constants ----------


class TestCollectStackMetadata:
    def test_collects_python_version(self):
        meta = cdm.collect_stack_metadata()
        assert "python_version" in meta
        assert "platform" in meta
        assert "has_spacy" in meta

    def test_handles_missing_dependencies_gracefully(self):
        # Should not raise even if some deps are unavailable.
        meta = cdm.collect_stack_metadata()
        # spacy_version may be None or a string.
        assert "spacy_version" in meta
        assert "scipy_version" in meta


class TestCollectFrameworkConstants:
    def test_collects_compression_heuristics(self):
        constants = cdm.collect_framework_constants()
        assert "compression_heuristics" in constants
        # When variance_audit is loaded, we should have some entries.
        if cdm.HAS_VARIANCE_AUDIT and cdm.COMPRESSION_HEURISTICS:
            assert len(constants["compression_heuristics"]) > 0
            # Each entry has the required fields.
            sample = next(
                iter(constants["compression_heuristics"].values())
            )
            for k in (
                "value", "direction", "weight", "length_floor",
                "signal_path", "provenance", "provisional",
            ):
                assert k in sample


# ---------- Snapshot ----------


class TestSnapshot:
    def test_snapshot_basic_shape(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snapshot = cdm.take_snapshot(bdir, do_tier2=False)
        assert snapshot["tool"] == cdm.TOOL_NAME
        assert "stack" in snapshot
        assert "framework_constants" in snapshot
        assert "benchmarks" in snapshot
        assert snapshot["n_benchmarks"] == 2

    def test_snapshot_anonymizes_filenames_by_default(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snapshot = cdm.take_snapshot(bdir, do_tier2=False)
        keys = list(snapshot["benchmarks"].keys())
        # IDs are benchmark_001, benchmark_002 — no filenames.
        assert all(k.startswith("benchmark_") for k in keys)

    def test_snapshot_include_filenames_opt_in(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snapshot = cdm.take_snapshot(
            bdir, do_tier2=False, include_filenames=True,
        )
        keys = list(snapshot["benchmarks"].keys())
        assert "bench_a.txt" in keys
        assert "bench_b.txt" in keys

    def test_snapshot_label_propagates(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snapshot = cdm.take_snapshot(
            bdir, do_tier2=False, benchmark_label="v1.39.0",
        )
        assert snapshot["snapshot_label"] == "v1.39.0"

    def test_snapshot_records_per_benchmark_signals(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snapshot = cdm.take_snapshot(bdir, do_tier2=False)
        for bench_id, info in snapshot["benchmarks"].items():
            assert "n_words" in info
            assert "signals" in info
            assert "compression" in info
            # At minimum we should have sentence_length signals.
            assert any(
                k.startswith("sentence_length")
                for k in info["signals"]
            )

    def test_snapshot_missing_benchmark_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cdm.take_snapshot(tmp_path / "missing-dir")

    def test_snapshot_empty_benchmark_dir_raises(self, tmp_path):
        bdir = tmp_path / "empty"
        bdir.mkdir()
        with pytest.raises(FileNotFoundError):
            cdm.take_snapshot(bdir)


# ---------- Drift detection ----------


class TestCompareSignals:
    def test_stable_when_within_threshold(self):
        snap = {"sentence_length.burstiness_B": 0.40}
        curr = {"sentence_length.burstiness_B": 0.41}
        diffs = cdm._compare_signals(snap, curr)
        assert diffs["sentence_length.burstiness_B"]["verdict"] == (
            "stable"
        )

    def test_drifted_when_exceeding_threshold(self):
        snap = {"sentence_length.burstiness_B": 0.40}
        curr = {"sentence_length.burstiness_B": 0.80}
        diffs = cdm._compare_signals(snap, curr)
        assert diffs["sentence_length.burstiness_B"]["verdict"] == (
            "drifted"
        )

    def test_added_signal(self):
        diffs = cdm._compare_signals({}, {"new_signal": 1.0})
        assert diffs["new_signal"]["verdict"] == "added"

    def test_removed_signal(self):
        diffs = cdm._compare_signals({"old_signal": 1.0}, {})
        assert diffs["old_signal"]["verdict"] == "removed"


class TestCompareConstants:
    def test_no_changes_returns_empty(self):
        constants = {
            "compression_heuristics": {
                "burstiness_B": {
                    "value": 0.5, "direction": "lt",
                    "weight": 1.5, "length_floor": 50,
                },
            },
        }
        diffs = cdm._compare_constants(constants, constants)
        assert diffs == {}

    def test_value_change_detected(self):
        snap = {
            "compression_heuristics": {
                "burstiness_B": {
                    "value": 0.5, "direction": "lt",
                    "weight": 1.5, "length_floor": 50,
                },
            },
        }
        curr = copy.deepcopy(snap)
        curr["compression_heuristics"]["burstiness_B"]["value"] = 0.6
        diffs = cdm._compare_constants(snap, curr)
        assert "burstiness_B" in diffs
        assert "value" in diffs["burstiness_B"]["fields"]
        assert (
            diffs["burstiness_B"]["fields"]["value"]["snapshot"]
            == 0.5
        )

    def test_added_heuristic(self):
        snap = {"compression_heuristics": {}}
        curr = {
            "compression_heuristics": {
                "new_signal": {
                    "value": 1.0, "direction": "lt",
                    "weight": 1.0, "length_floor": 50,
                },
            },
        }
        diffs = cdm._compare_constants(snap, curr)
        assert diffs["new_signal"]["verdict"] == "added"


class TestCompareStack:
    def test_no_changes(self):
        stack = {
            "python_version": "3.13.7",
            "spacy_version": "3.7.0",
            "spacy_model": "en_core_web_sm-3.7.0",
        }
        assert cdm._compare_stack(stack, stack) == {}

    def test_python_version_change(self):
        snap = {"python_version": "3.13.7"}
        curr = {"python_version": "3.14.0"}
        diffs = cdm._compare_stack(snap, curr)
        assert diffs["python_version"]["snapshot"] == "3.13.7"
        assert diffs["python_version"]["current"] == "3.14.0"


class TestDetectDrift:
    def test_no_drift_when_identical(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        report = cdm.detect_drift(snapshot=snap, current=snap)
        assert report["infrastructure_drift_detected"] is False
        assert report["recalibration_recommended"] is False
        assert report["n_signals_drifted"] == 0
        assert report["drifted_benchmarks"] == []

    def test_drift_detected_when_signals_differ(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        # Mutate one signal to force drift.
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"][
            "sentence_length.burstiness_B"
        ] = 5.0
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["infrastructure_drift_detected"] is True
        assert report["n_signals_drifted"] >= 1
        assert first_bench in report["drifted_benchmarks"]

    def test_constant_change_recommends_recalibration(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        # Mutate a threshold constant.
        constants = curr["framework_constants"]["compression_heuristics"]
        if constants:
            first_key = next(iter(constants.keys()))
            constants[first_key] = {
                **constants[first_key],
                "value": constants[first_key].get("value", 0) + 1.0,
            }
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["recalibration_recommended"] is True

    def test_stack_change_with_drift_recommends_recalibration(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        curr["stack"]["spacy_version"] = "9.99.0"
        # Force a drift on one benchmark.
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"][
            "sentence_length.burstiness_B"
        ] = 5.0
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["recalibration_recommended"] is True


# ---------- Render ----------


class TestRender:
    def test_no_drift_renders_clean_report(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        report = cdm.detect_drift(snapshot=snap, current=snap)
        md = cdm.render_report(report)
        assert "Infrastructure drift detected" in md
        assert "## What this result licenses" in md

    def test_drift_renders_drifted_section(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"][
            "sentence_length.burstiness_B"
        ] = 5.0
        report = cdm.detect_drift(snapshot=snap, current=curr)
        md = cdm.render_report(report)
        assert "## Drifted benchmarks" in md
        assert first_bench in md


# ---------- CLI ----------


class TestCli:
    def test_cli_snapshot_then_check_no_drift(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap_path = tmp_path / "snapshot.json"
        rc = cdm.main([
            "snapshot",
            "--benchmark-dir", str(bdir),
            "--out", str(snap_path),
            "--no-tier2",
        ])
        assert rc == 0
        assert snap_path.exists()

        # Now check against the same snapshot.
        report_path = tmp_path / "drift.json"
        rc = cdm.main([
            "check",
            "--benchmark-dir", str(bdir),
            "--snapshot", str(snap_path),
            "--out", str(report_path),
            "--json",
            "--no-tier2",
        ])
        assert rc == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["infrastructure_drift_detected"] is False
        assert report["n_signals_drifted"] == 0

    def test_cli_missing_benchmark_dir_returns_2(self, tmp_path):
        rc = cdm.main([
            "snapshot",
            "--benchmark-dir", str(tmp_path / "missing"),
            "--out", str(tmp_path / "snap.json"),
        ])
        assert rc == 2

    def test_cli_missing_snapshot_returns_2(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        rc = cdm.main([
            "check",
            "--benchmark-dir", str(bdir),
            "--snapshot", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_invalid_snapshot_json_returns_2(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json", encoding="utf-8")
        rc = cdm.main([
            "check",
            "--benchmark-dir", str(bdir),
            "--snapshot", str(bad),
        ])
        assert rc == 2

    def test_cli_exit_nonzero_on_drift(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap_path = tmp_path / "snap.json"
        rc = cdm.main([
            "snapshot",
            "--benchmark-dir", str(bdir),
            "--out", str(snap_path),
            "--no-tier2",
        ])
        assert rc == 0

        # Mutate the snapshot to simulate drift on the next run
        # (the CURRENT run will produce the original values; the
        # snapshot will look out-of-date).
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        first_bench = next(iter(snap["benchmarks"].keys()))
        snap["benchmarks"][first_bench]["signals"][
            "sentence_length.burstiness_B"
        ] = -99.0
        snap_path.write_text(
            json.dumps(snap, indent=2), encoding="utf-8",
        )

        rc = cdm.main([
            "check",
            "--benchmark-dir", str(bdir),
            "--snapshot", str(snap_path),
            "--no-tier2",
            "--exit-nonzero-on-drift",
        ])
        # Drift detected → exit code 3.
        assert rc == 3


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
