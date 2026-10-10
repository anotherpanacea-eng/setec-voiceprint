#!/usr/bin/env python3
"""Regression tests for calibration_drift_monitor.py (Release 9)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

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


class TestSchemaChangeDrift:
    """Reviewer-reproduced regression: `_compare_signals` emitted
    `added` / `removed` verdicts but `detect_drift` only counted
    `drifted`, so a signal disappearing or newly appearing did
    NOT trigger `infrastructure_drift_detected: true`."""

    def test_added_signal_counts_as_drift(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"]["new_signal"] = (
            42.0
        )
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["infrastructure_drift_detected"] is True
        assert report["n_signals_schema_changed"] >= 1
        assert first_bench in report["drifted_benchmarks"]

    def test_removed_signal_counts_as_drift(self, tmp_path):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        # Remove an existing signal from the current run.
        signals = curr["benchmarks"][first_bench]["signals"]
        if signals:
            removed_key = next(iter(signals.keys()))
            del signals[removed_key]
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["infrastructure_drift_detected"] is True
        assert report["n_signals_schema_changed"] >= 1

    def test_added_and_removed_with_stack_change_recommends_recalibration(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        curr["stack"]["spacy_version"] = "9.99.0"
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"]["new_signal"] = (
            42.0
        )
        report = cdm.detect_drift(snapshot=snap, current=curr)
        assert report["recalibration_recommended"] is True

    def test_per_benchmark_records_added_removed_counts(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"]["new_signal"] = (
            42.0
        )
        report = cdm.detect_drift(snapshot=snap, current=curr)
        bench_info = report["per_benchmark"][first_bench]
        assert bench_info.get("n_signals_added", 0) >= 1
        assert "n_signals_removed" in bench_info
        assert "n_signals_schema_changed" in bench_info


class TestIncludeFilenamesNoCollision:
    """Reviewer-reproduced regression: pre-1.41.1
    `--include-filenames` set bench_id to `path.name`, so two
    benchmarks with the same basename in different subdirs
    overwrote each other. `n_benchmarks` shrank silently."""

    def test_duplicate_basenames_in_nested_dirs(self, tmp_path):
        bdir = tmp_path / "benchmarks"
        (bdir / "a").mkdir(parents=True)
        (bdir / "b").mkdir(parents=True)
        (bdir / "a" / "same.txt").write_text(
            _SAMPLE_TEXT, encoding="utf-8",
        )
        (bdir / "b" / "same.txt").write_text(
            _SAMPLE_TEXT + " More prose to vary the file.",
            encoding="utf-8",
        )
        snapshot = cdm.take_snapshot(
            bdir, do_tier2=False, include_filenames=True,
        )
        # Both benchmarks must be present; no overwrite.
        assert snapshot["n_benchmarks"] == 2
        ids = list(snapshot["benchmarks"].keys())
        # IDs are relative paths, not just basenames.
        assert "a/same.txt" in ids
        assert "b/same.txt" in ids

    def test_anonymized_ids_unaffected_by_basename_collision(
        self, tmp_path,
    ):
        bdir = tmp_path / "benchmarks"
        (bdir / "a").mkdir(parents=True)
        (bdir / "b").mkdir(parents=True)
        (bdir / "a" / "same.txt").write_text(
            _SAMPLE_TEXT, encoding="utf-8",
        )
        (bdir / "b" / "same.txt").write_text(
            _SAMPLE_TEXT + " More prose.", encoding="utf-8",
        )
        snapshot = cdm.take_snapshot(
            bdir, do_tier2=False, include_filenames=False,
        )
        # benchmark_001, benchmark_002 — always unique.
        assert snapshot["n_benchmarks"] == 2
        ids = list(snapshot["benchmarks"].keys())
        assert all(k.startswith("benchmark_") for k in ids)


class TestRenderSchemaChangeDrift:
    """Reviewer-reproduced regression: pre-1.41.1 the JSON
    correctly counted added/removed signals as schema drift
    (per the 1.40.1 fix), but the Markdown report only surfaced
    `n_signals_drifted`. A removed signal showed
    `infrastructure_drift_detected: true` in JSON but
    `Signals drifted / stable: 0 / 0` in Markdown with no
    signal name listed."""

    def test_summary_line_includes_schema_changed(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"]["new_signal"] = (
            42.0
        )
        report = cdm.detect_drift(snapshot=snap, current=curr)
        md = cdm.render_report(report)
        assert "schema-changed" in md
        assert "1" in md  # n_signals_schema_changed = 1

    def test_added_signals_named_in_drifted_section(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        curr["benchmarks"][first_bench]["signals"][
            "infrastructure_only_new_signal"
        ] = 42.0
        report = cdm.detect_drift(snapshot=snap, current=curr)
        md = cdm.render_report(report)
        # The added signal is named, not just aggregated.
        assert "Added signals" in md
        assert "infrastructure_only_new_signal" in md

    def test_removed_signals_named_in_drifted_section(
        self, tmp_path,
    ):
        bdir = _write_benchmarks(tmp_path)
        snap = cdm.take_snapshot(bdir, do_tier2=False)
        curr = copy.deepcopy(snap)
        first_bench = next(iter(curr["benchmarks"].keys()))
        signals = curr["benchmarks"][first_bench]["signals"]
        if signals:
            removed_key = next(iter(signals.keys()))
            del signals[removed_key]
            report = cdm.detect_drift(snapshot=snap, current=curr)
            md = cdm.render_report(report)
            assert "Removed signals" in md
            assert removed_key in md


class TestEmptyBenchmarkDirRejected:
    """Reviewer-reproduced regression: a directory containing
    only empty / whitespace files produced n_benchmarks=0 with
    a successful exit. CI drift checks would silently pass
    without measuring anything."""

    def test_only_empty_files_raises(self, tmp_path):
        bdir = tmp_path / "empty_benchmarks"
        bdir.mkdir()
        (bdir / "empty.txt").write_text("", encoding="utf-8")
        (bdir / "whitespace.md").write_text(
            "   \n\n   \n", encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="non-empty"):
            cdm.take_snapshot(bdir, do_tier2=False)

    def test_cli_empty_files_only_returns_2(self, tmp_path):
        bdir = tmp_path / "empty_benchmarks"
        bdir.mkdir()
        (bdir / "empty.txt").write_text("", encoding="utf-8")
        snap_path = tmp_path / "snap.json"
        rc = cdm.main([
            "snapshot",
            "--benchmark-dir", str(bdir),
            "--out", str(snap_path),
            "--no-tier2",
        ])
        assert rc == 2
        # The snapshot file should NOT exist (writes happen
        # only after the snapshot succeeds).
        assert not snap_path.exists()

    def test_skipped_empty_recorded_when_some_files_succeed(
        self, tmp_path,
    ):
        bdir = tmp_path / "mixed"
        bdir.mkdir()
        # One real benchmark + one empty file.
        (bdir / "real.txt").write_text(
            _SAMPLE_TEXT, encoding="utf-8",
        )
        (bdir / "empty.txt").write_text("", encoding="utf-8")
        snapshot = cdm.take_snapshot(bdir, do_tier2=False)
        # The real file measured; empty was skipped.
        assert snapshot["n_benchmarks"] == 1
        assert "skipped_empty" in snapshot


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


def _numeric_snapshot(signals):
    return {"benchmarks": {"invented": {"signals": signals}}}


_UNUSABLE_NUMBERS = [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="positive-infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param(True, id="true"),
    pytest.param(False, id="false"),
    pytest.param("1.0", id="string"),
    pytest.param(10 ** 400, id="unrepresentable-integer"),
]


class TestFiniteComparisons:
    @pytest.mark.parametrize("value", _UNUSABLE_NUMBERS)
    @pytest.mark.parametrize("location", ["snapshot", "current", "added", "removed"])
    def test_unusable_signal_cannot_be_reported(self, value, location):
        snap = {"signal": value if location in {"snapshot", "removed"} else 1.0}
        curr = {"signal": value if location in {"current", "added"} else 1.0}
        if location == "added":
            snap = {}
        elif location == "removed":
            curr = {}
        with pytest.raises(ValueError, match="signal must be a finite real number"):
            cdm.detect_drift(snapshot=_numeric_snapshot(snap), current=_numeric_snapshot(curr))

    @pytest.mark.parametrize("value", _UNUSABLE_NUMBERS + [pytest.param(-0.1, id="negative")])
    @pytest.mark.parametrize("kind", ["relative", "unused-absolute"])
    @pytest.mark.parametrize("empty", [False, True])
    def test_invalid_tolerance_cannot_license_stability(self, value, kind, empty):
        options = (
            {"relative_threshold": value}
            if kind == "relative" else {"absolute_thresholds": {"unused": value}}
        )
        snapshot = {} if empty else _numeric_snapshot({"signal": 1.0})
        with pytest.raises(ValueError, match="threshold must be"):
            cdm.detect_drift(snapshot=snapshot, current=snapshot, **options)

    @pytest.mark.parametrize("snapshot,current,options,field", [
        (-1e308, 1e308, {}, "Signal delta"),
        (1e308, 1e307, {"relative_threshold": 2.0}, "Relative noise floor"),
        (1e-8, 1e308, {}, "Relative signal change"),
    ])
    def test_finite_inputs_with_overflow_cannot_be_reported(self, snapshot, current, options, field):
        with pytest.raises(ValueError, match=field):
            cdm.detect_drift(
                snapshot=_numeric_snapshot({"signal": snapshot}),
                current=_numeric_snapshot({"signal": current}),
                **options,
            )

    def test_none_absence_and_numeric_types_are_preserved(self):
        diffs = cdm._compare_signals(
            {"both": None, "added": None, "removed": 2, "paired": 1},
            {"both": None, "added": 3.0, "removed": None, "paired": 1.0},
        )
        assert "both" not in diffs
        assert diffs["added"] == {"snapshot": None, "current": 3.0, "verdict": "added"}
        assert diffs["removed"] == {"snapshot": 2, "current": None, "verdict": "removed"}
        assert type(diffs["paired"]["snapshot"]) is int
        assert type(diffs["paired"]["current"]) is float
        assert diffs["paired"]["verdict"] == "stable"
        json.dumps(diffs, allow_nan=False)

    def test_empty_absolute_mapping_keeps_default_floor_and_ties(self):
        signal = "sentence_length.burstiness_B"
        # A zero snapshot eliminates the relative floor; exact absolute ties are stable.
        diffs = cdm._compare_signals({signal: 0}, {signal: 0.05}, absolute_thresholds={})
        assert diffs[signal]["noise_floor"] == 0.05
        assert diffs[signal]["verdict"] == "stable"
        assert cdm._compare_signals({"signal": 1}, {"signal": 2}, relative_threshold=1)["signal"]["verdict"] == "stable"


class TestFiniteCheckCli:
    @staticmethod
    def forbid_measurement(*args, **kwargs):
        raise AssertionError("Invalid numeric input reached benchmark measurement")

    @pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "1e999", "-0.1"])
    def test_invalid_relative_threshold_refuses_before_snapshot_or_measurement(self, tmp_path, monkeypatch, capsys, threshold):
        assert cdm.HAS_VARIANCE_AUDIT
        monkeypatch.setattr(cdm, "take_snapshot", self.forbid_measurement)
        monkeypatch.setattr(cdm, "_read_snapshot", self.forbid_measurement)
        output = tmp_path / "report.json"
        output.write_bytes(b"previous report\r\n")
        assert cdm.main([
            "check", "--benchmark-dir", str(tmp_path),
            "--snapshot", str(tmp_path / "not-read.json"),
            "--relative-threshold=" + threshold, "--json", "--out", str(output),
        ]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("--relative-threshold:")
        captured.err.encode("ascii")
        assert output.read_bytes() == b"previous report\r\n"

    @pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "true", '"1.0"', "1" + "0" * 400])
    def test_invalid_snapshot_number_refuses_before_measurement(self, tmp_path, monkeypatch, capsys, token):
        assert cdm.HAS_VARIANCE_AUDIT
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text('{"benchmarks":{"invented":{"signals":{"signal":' + token + '}}}}', encoding="utf-8")
        monkeypatch.setattr(cdm, "take_snapshot", self.forbid_measurement)
        output = tmp_path / "report.json"
        assert cdm.main([
            "check", "--benchmark-dir", str(tmp_path), "--snapshot", str(snapshot),
            "--json", "--out", str(output),
        ]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("--snapshot:")
        captured.err.encode("ascii")
        assert not output.exists()

    @pytest.mark.parametrize("value,current,threshold", [
        (-1e308, 1e308, "0.1"),
        (1e308, 1e307, "2.0"),
        (1e-8, 1e308, "0.1"),
    ])
    @pytest.mark.parametrize("destination", ["stdout", "new", "existing"])
    def test_derived_overflow_never_publishes_a_report(self, tmp_path, monkeypatch, capsys, value, current, threshold, destination):
        assert cdm.HAS_VARIANCE_AUDIT
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(json.dumps(_numeric_snapshot({"signal": value})), encoding="utf-8")
        calls = []
        def measure(*args, **kwargs):
            calls.append(True)
            return _numeric_snapshot({"signal": current})
        monkeypatch.setattr(cdm, "take_snapshot", measure)
        output = tmp_path / "report.json"
        if destination == "existing":
            output.write_bytes(b"previous report\r\n")
        args = ["check", "--benchmark-dir", str(tmp_path), "--snapshot", str(snapshot), "--json", "--relative-threshold", threshold]
        if destination != "stdout":
            args += ["--out", str(output)]
        assert cdm.main(args) == 2
        assert calls == [True]
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.startswith("Signal comparison:")
        if destination == "existing":
            assert output.read_bytes() == b"previous report\r\n"
        else:
            assert not output.exists()

    @pytest.mark.parametrize("current,exit_on_drift,expected", [(1.05, False, 0), (100.0, False, 0), (100.0, True, 3)])
    def test_valid_floats_still_produce_strict_reports(self, tmp_path, monkeypatch, capsys, current, exit_on_drift, expected):
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(json.dumps(_numeric_snapshot({"signal": 1.0})), encoding="utf-8")
        monkeypatch.setattr(cdm, "take_snapshot", lambda *args, **kwargs: _numeric_snapshot({"signal": current}))
        args = ["check", "--benchmark-dir", str(tmp_path), "--snapshot", str(snapshot), "--json"]
        if exit_on_drift:
            args += ["--exit-nonzero-on-drift"]
        assert cdm.main(args) == expected
        report = json.loads(capsys.readouterr().out)
        assert report["infrastructure_drift_detected"] is (current == 100.0)
        json.dumps(report, allow_nan=False)
        assert type(report["per_benchmark"]["invented"]["signal_diffs"]["signal"]["snapshot"]) is float


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
