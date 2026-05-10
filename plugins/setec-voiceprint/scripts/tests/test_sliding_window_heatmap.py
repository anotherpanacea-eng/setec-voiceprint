#!/usr/bin/env python3
"""Regression tests for sliding_window_heatmap.py.

The heatmap script is cathedral upgrade #5's finisher — it consumes
``variance_audit.py``'s sliding-window output (band classification
per window + flagged signals per window) and renders it as a
markdown localization map. Tests verify:

  * Loading accepts both the full variance_audit output and the
    raw windows sub-dict.
  * Sparkline rendering maps fractions onto the eight-level block
    scale and tolerates None / Insufficient-signal windows.
  * Band tape encodes Heavily/Moderately/Lightly/Insufficient bands
    via the H/M/L/- single-character codes.
  * Hot-zone detection finds contiguous Moderately/Heavily smoothed
    runs in word coordinates and surfaces the highest band per run.
  * Per-signal × per-window grid groups firings by signal across
    windows.
  * The privacy guard refuses non-private output paths unless
    ``--allow-public-output`` is set.
  * End-to-end CLI run on a synthetic windows block produces both
    markdown and JSON outputs with stable shapes.
  * The claim-license block from claim_license.py is embedded.

Synthetic windows are constructed in-memory; no real corpus or
file I/O beyond tempfiles for the CLI test.
"""

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

import sliding_window_heatmap as swh  # type: ignore


# ------------------- Fixtures -----------------------------------


def _make_window(
    *,
    start_word: int,
    end_word: int,
    band: str,
    fraction: float | None = None,
    flagged: list[str] | None = None,
    n_words: int | None = None,
) -> dict:
    """Build one fake window result matching the variance_audit shape."""
    return {
        "start_word": start_word,
        "end_word": end_word,
        "char_start": start_word * 6,  # ~6 chars/word, doesn't matter
        "char_end": end_word * 6,
        "n_words": n_words if n_words is not None else (end_word - start_word),
        "audit": {},  # heatmap doesn't read this
        "compression": {
            "band": band,
            "compression_fraction": fraction,
            "flagged_signals": flagged or [],
            "n_flagged": len(flagged or []),
        },
    }


def _make_windows_block(windows: list[dict]) -> dict:
    return {
        "window_size": 500,
        "stride": 250,
        "n_windows": len(windows),
        "results": windows,
    }


# ------------------- Loading -----------------------------------


class TestLoading:
    def test_load_windows_block_accepts_full_audit_output(self):
        full = {
            "audit": {},
            "compression": {},
            "windows": _make_windows_block([
                _make_window(start_word=0, end_word=500,
                             band="Lightly smoothed", fraction=0.10),
            ]),
        }
        block = swh.load_windows_block(full)
        assert block["n_windows"] == 1

    def test_load_windows_block_accepts_raw_windows_dict(self):
        raw = _make_windows_block([
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
        ])
        block = swh.load_windows_block(raw)
        assert block["n_windows"] == 1

    def test_load_windows_block_rejects_garbage(self):
        with pytest.raises(ValueError):
            swh.load_windows_block({"foo": "bar"})
        with pytest.raises(ValueError):
            swh.load_windows_block([1, 2, 3])  # type: ignore[arg-type]


# ------------------- Sparkline -----------------------------------


class TestSparkline:
    def test_sparkline_returns_one_char_per_window(self):
        bars = swh.render_sparkline([0.1, 0.3, 0.5, 0.7])
        assert len(bars) == 4

    def test_sparkline_with_none_uses_low_block(self):
        bars = swh.render_sparkline([None, 0.5])
        assert bars[0] == swh.SPARK_CHARS[0]

    def test_sparkline_max_bar_at_max_value(self):
        # The largest fraction should map to the tallest block.
        bars = swh.render_sparkline([0.05, 0.1, 0.5])
        assert bars[-1] == swh.SPARK_CHARS[-1]

    def test_sparkline_handles_all_zero(self):
        bars = swh.render_sparkline([0.0, 0.0, 0.0])
        # All bars at the lowest level when there's no variance.
        assert all(b == swh.SPARK_CHARS[0] for b in bars)

    def test_sparkline_empty_input(self):
        assert swh.render_sparkline([]) == ""


# ------------------- Band tape -----------------------------------


class TestBandTape:
    def test_band_tape_encodes_known_bands(self):
        tape = swh.render_band_tape([
            "Heavily smoothed",
            "Moderately smoothed",
            "Lightly smoothed",
            "Insufficient signal",
        ])
        assert tape == "HML-"

    def test_band_tape_unknown_band_renders_question_mark(self):
        tape = swh.render_band_tape(["Frobnozzle smoothed"])
        assert tape == "?"


# ------------------- Hot-zone detection -----------------------------------


class TestHotZones:
    def test_no_hot_windows_returns_empty(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.12),
        ]
        zones = swh.find_hot_zones(windows)
        assert zones == []

    def test_single_hot_window_is_one_zone(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
            _make_window(start_word=500, end_word=1000,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
            _make_window(start_word=1000, end_word=1500,
                         band="Lightly smoothed", fraction=0.12),
        ]
        zones = swh.find_hot_zones(windows)
        assert len(zones) == 1
        assert zones[0].band == "Heavily smoothed"
        assert zones[0].start_word == 500
        assert zones[0].end_word == 1000
        assert zones[0].n_windows == 1

    def test_consecutive_hot_windows_merge_into_one_zone(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
            _make_window(start_word=500, end_word=1000,
                         band="Moderately smoothed", fraction=0.30),
            _make_window(start_word=1000, end_word=1500,
                         band="Heavily smoothed", fraction=0.55),
            _make_window(start_word=1500, end_word=2000,
                         band="Moderately smoothed", fraction=0.35),
            _make_window(start_word=2000, end_word=2500,
                         band="Lightly smoothed", fraction=0.10),
        ]
        zones = swh.find_hot_zones(windows)
        assert len(zones) == 1
        # Heaviest band wins the run's headline label.
        assert zones[0].band == "Heavily smoothed"
        assert zones[0].n_windows == 3
        assert zones[0].start_word == 500
        assert zones[0].end_word == 2000

    def test_two_disjoint_hot_zones(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.10),
            _make_window(start_word=1000, end_word=1500,
                         band="Moderately smoothed", fraction=0.30),
        ]
        zones = swh.find_hot_zones(windows)
        assert len(zones) == 2
        assert zones[0].band == "Heavily smoothed"
        assert zones[1].band == "Moderately smoothed"

    def test_insufficient_signal_breaks_a_run(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55),
            _make_window(start_word=500, end_word=1000,
                         band="Insufficient signal", fraction=None),
            _make_window(start_word=1000, end_word=1500,
                         band="Heavily smoothed", fraction=0.50),
        ]
        zones = swh.find_hot_zones(windows)
        # Insufficient is NOT a hot band; the run breaks across it.
        assert len(zones) == 2

    def test_hot_zone_records_dominant_signals(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B", "mtld"]),
            _make_window(start_word=500, end_word=1000,
                         band="Heavily smoothed", fraction=0.50,
                         flagged=["burstiness_B"]),
            _make_window(start_word=1000, end_word=1500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
        ]
        zones = swh.find_hot_zones(windows)
        assert len(zones) == 1
        # Top-ranked dominant signal should be burstiness_B (3/3),
        # then mtld (1/3).
        assert any(
            "burstiness_B" in s for s in zones[0].dominant_signals
        )
        # The format uses "name (count/n_windows)".
        assert any(
            s.startswith("burstiness_B") and "3/3" in s
            for s in zones[0].dominant_signals
        )


# ------------------- Signal grid -----------------------------------


class TestSignalGrid:
    def test_grid_collects_signals_across_windows(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10,
                         flagged=["burstiness_B"]),
            _make_window(start_word=500, end_word=1000,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B", "mtld"]),
            _make_window(start_word=1000, end_word=1500,
                         band="Moderately smoothed", fraction=0.30,
                         flagged=["mtld"]),
        ]
        signals, grid = swh.collect_signal_grid(windows)
        # Both signals fired at least once.
        assert "burstiness_B" in signals
        assert "mtld" in signals
        # Grid is signals × windows.
        assert len(grid) == len(signals)
        assert all(len(row) == len(windows) for row in grid)
        # Burstiness fires in windows 0 and 1; mtld in 1 and 2.
        b_idx = signals.index("burstiness_B")
        m_idx = signals.index("mtld")
        assert grid[b_idx] == [True, True, False]
        assert grid[m_idx] == [False, True, True]

    def test_grid_orders_by_fire_count_desc(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["b"]),
            _make_window(start_word=500, end_word=1000,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["a", "b", "c"]),
            _make_window(start_word=1000, end_word=1500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["b", "c"]),
        ]
        signals, _ = swh.collect_signal_grid(windows)
        # b fires 3, c fires 2, a fires 1: order should be b, c, a.
        assert signals == ["b", "c", "a"]

    def test_grid_with_no_signals_is_empty(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
        ]
        signals, grid = swh.collect_signal_grid(windows)
        assert signals == []
        assert grid == []


# ------------------- Markdown rendering -----------------------------------


class TestRenderReport:
    def test_renders_top_level_sections(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.12),
        ]
        report = swh.render_report(_make_windows_block(windows))
        assert "# Sliding-window compression heatmap" in report
        assert "## Compression-fraction sparkline" in report
        assert "## Band tape" in report
        assert "## Hot zones" in report
        assert "## Per-signal × per-window grid" in report
        assert "## Window detail" in report
        # Claim-license block, contributed by claim_license.py.
        assert "## What this result licenses" in report

    def test_no_hot_zones_message_when_all_light(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.12),
        ]
        report = swh.render_report(_make_windows_block(windows))
        assert "No hot zones" in report

    def test_band_tape_shows_in_report(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55),
            _make_window(start_word=500, end_word=1000,
                         band="Moderately smoothed", fraction=0.30),
        ]
        report = swh.render_report(_make_windows_block(windows))
        # The band tape contains "HM" somewhere (inside a code block).
        assert "HM" in report

    def test_source_label_appears_when_provided(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
        ]
        report = swh.render_report(
            _make_windows_block(windows),
            source_label="path/to/source.json",
        )
        assert "path/to/source.json" in report


# ------------------- JSON rendering -----------------------------------


class TestRenderJson:
    def test_json_shape_is_stable(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.12),
        ]
        out = swh.render_json(_make_windows_block(windows))
        for key in (
            "task_surface", "tool", "version", "n_windows",
            "window_size", "stride", "bands", "compression_fractions",
            "band_distribution", "sparkline", "band_tape",
            "hot_zones", "signal_grid",
        ):
            assert key in out, f"missing key: {key}"
        assert out["task_surface"] == "smoothing_diagnosis"
        assert out["n_windows"] == 2
        assert out["bands"][0] == "Heavily smoothed"

    def test_json_hot_zone_fields(self):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
        ]
        out = swh.render_json(_make_windows_block(windows))
        assert len(out["hot_zones"]) == 1
        z = out["hot_zones"][0]
        for key in (
            "start_window", "end_window", "start_word", "end_word",
            "band", "n_windows", "fraction_min", "fraction_max",
            "dominant_signals",
        ):
            assert key in z, f"missing hot-zone key: {key}"


# ------------------- Privacy guard -----------------------------------


class TestPrivacyGuard:
    def test_path_under_private_root_is_allowed(self, tmp_path):
        priv = tmp_path / "ai-prose-baselines-private" / "out.md"
        assert swh._is_under_private_root(priv) is True

    def test_path_outside_private_root_is_blocked(self, tmp_path):
        public = tmp_path / "public" / "out.md"
        assert swh._is_under_private_root(public) is False


# ------------------- End-to-end CLI -----------------------------------


class TestCli:
    def test_cli_round_trip_to_files(self, tmp_path, monkeypatch, capsys):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Heavily smoothed", fraction=0.55,
                         flagged=["burstiness_B"]),
            _make_window(start_word=500, end_word=1000,
                         band="Lightly smoothed", fraction=0.12),
        ]
        in_path = tmp_path / "audit.json"
        in_path.write_text(json.dumps({
            "windows": _make_windows_block(windows),
        }), encoding="utf-8")
        out_dir = tmp_path / "ai-prose-baselines-private"
        out_md = out_dir / "heatmap.md"
        out_json = out_dir / "heatmap.json"

        rc = swh.main([
            "--in", str(in_path),
            "--out", str(out_md),
            "--json-out", str(out_json),
        ])
        assert rc == 0
        assert out_md.exists()
        assert out_json.exists()
        body = out_md.read_text(encoding="utf-8")
        assert "# Sliding-window compression heatmap" in body
        payload = json.loads(out_json.read_text(encoding="utf-8"))
        assert payload["n_windows"] == 2

    def test_cli_refuses_public_output_without_flag(self, tmp_path):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
        ]
        in_path = tmp_path / "audit.json"
        in_path.write_text(json.dumps({
            "windows": _make_windows_block(windows),
        }), encoding="utf-8")
        out_md = tmp_path / "public" / "heatmap.md"
        rc = swh.main([
            "--in", str(in_path),
            "--out", str(out_md),
        ])
        assert rc == 3
        assert not out_md.exists()

    def test_cli_allows_public_output_with_flag(self, tmp_path):
        windows = [
            _make_window(start_word=0, end_word=500,
                         band="Lightly smoothed", fraction=0.10),
        ]
        in_path = tmp_path / "audit.json"
        in_path.write_text(json.dumps({
            "windows": _make_windows_block(windows),
        }), encoding="utf-8")
        out_md = tmp_path / "public" / "heatmap.md"
        rc = swh.main([
            "--in", str(in_path),
            "--out", str(out_md),
            "--allow-public-output",
        ])
        assert rc == 0
        assert out_md.exists()

    def test_cli_handles_missing_input(self, tmp_path):
        rc = swh.main([
            "--in", str(tmp_path / "does-not-exist.json"),
        ])
        assert rc == 2

    def test_cli_handles_corrupt_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        rc = swh.main(["--in", str(bad)])
        assert rc == 2

    def test_cli_help_succeeds(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            swh.main(["--help"])
        assert excinfo.value.code == 0
        captured = capsys.readouterr()
        assert "sliding_window_heatmap" in captured.out


# ---------- Source-of-smoothing localization (Release 2) ---------


class TestPhenomenonClassification:
    """Each hot zone gets a `phenomenon` label classifying which
    family of signals dominates the firing pattern. This is the
    Release-2 trustworthiness extension that turns 'where the band
    fires' into 'what kind of smoothing is happening here.'"""

    def test_syntactic_flattening_when_rhythm_signals_dominate(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["burstiness_B", "sentence_length_sd", "mdd_sd"],
            ),
            _make_window(
                start_word=500, end_word=1000,
                band="Heavily smoothed", fraction=0.50,
                flagged=["burstiness_B", "sentence_length_sd", "fkgl_sd"],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        assert len(zones) == 1
        assert zones[0].phenomenon == "syntactic_flattening"
        assert zones[0].phenomenon_evidence

    def test_lexical_compression_when_diversity_signals_dominate(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["mtld", "mattr", "shannon_entropy"],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        assert zones[0].phenomenon == "lexical_compression"

    def test_over_cohesion_when_cohesion_signals_dominate(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["adjacent_cosine_mean", "adjacent_cosine_sd"],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        assert zones[0].phenomenon == "over_cohesion"

    def test_connective_overuse_when_connective_dominates(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["connective_density"],
            ),
            _make_window(
                start_word=500, end_word=1000,
                band="Heavily smoothed", fraction=0.55,
                flagged=["connective_density"],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        assert zones[0].phenomenon == "connective_overuse"

    def test_mixed_smoothing_when_no_dominant_family(self):
        # Roughly equal contributions from three families → mixed.
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=[
                    "burstiness_B", "mtld",
                    "adjacent_cosine_mean", "connective_density",
                ],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        # No family has ≥ 60% share: 1/4 each → mixed.
        assert zones[0].phenomenon == "mixed_smoothing"

    def test_unclassified_when_no_signals_fire(self):
        # A hot band with no flagged signals (band came from
        # threshold weighting alone, e.g. POS-bigram KL) → no
        # signals to classify → unclassified.
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=[],
            ),
        ]
        zones = swh.find_hot_zones(windows)
        assert zones[0].phenomenon == "unclassified"

    def test_phenomenon_in_markdown(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["burstiness_B", "sentence_length_sd"],
            ),
        ]
        block = swh._make_windows_block(windows) if hasattr(swh, "_make_windows_block") else {
            "window_size": 500, "stride": 250,
            "n_windows": 1, "results": windows,
        }
        report = swh.render_report(block)
        assert "syntactic flattening" in report or "phenomenon" in report

    def test_phenomenon_in_json(self):
        windows = [
            _make_window(
                start_word=0, end_word=500,
                band="Heavily smoothed", fraction=0.55,
                flagged=["adjacent_cosine_mean", "adjacent_cosine_sd"],
            ),
        ]
        block = {
            "window_size": 500, "stride": 250,
            "n_windows": 1, "results": windows,
        }
        out = swh.render_json(block)
        assert "hot_zones" in out
        zone = out["hot_zones"][0]
        assert zone["phenomenon"] == "over_cohesion"
        assert zone["phenomenon_evidence"]


class TestClassifyZonePhenomenonInternal:
    """Direct tests of `_classify_zone_phenomenon` to pin the
    family-mapping and dominance-threshold contracts."""

    def test_dominant_family_at_threshold(self):
        # 3 of 5 signals in syntactic family = 60%, hits threshold.
        counts = {
            "burstiness_B": 1, "sentence_length_sd": 1, "mdd_sd": 1,
            "mtld": 1, "adjacent_cosine_mean": 1,
        }
        phenomenon, evidence = swh._classify_zone_phenomenon(counts, 5)
        assert phenomenon == "syntactic_flattening"

    def test_below_threshold_yields_mixed(self):
        # 2 of 5 in each of two families → 40% each → mixed.
        counts = {
            "burstiness_B": 1, "sentence_length_sd": 1,
            "mtld": 1, "mattr": 1, "adjacent_cosine_mean": 1,
        }
        phenomenon, _ = swh._classify_zone_phenomenon(counts, 5)
        assert phenomenon == "mixed_smoothing"

    def test_unknown_signals_only_yield_unclassified(self):
        counts = {"some_unknown_signal": 5}
        phenomenon, evidence = swh._classify_zone_phenomenon(counts, 5)
        assert phenomenon == "unclassified"

    def test_empty_counts(self):
        phenomenon, evidence = swh._classify_zone_phenomenon({}, 0)
        assert phenomenon == "unclassified"
        assert evidence == []
