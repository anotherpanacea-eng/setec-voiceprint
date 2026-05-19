#!/usr/bin/env python3
"""Regression tests for bakeoff_mage_tier34_compare.py.

Covers two load-bearing fixes:

1. _da_auc reads `survey["rows"][i]["direction_aware_auc"]` — the
   actual layout emitted by calibration_survey.SurveyRow.to_dict.
   An earlier draft probed `survey["per_signal"][sig].calibration
   .direction_aware_auc`, a path the survey has never produced; the
   table always rendered "MISSING / no winner".
2. Winner selection disqualifies configs with any target-signal
   inversion or missing signal, then ranks survivors by minimum
   da_AUC across signals. The previous logic ranked by max, so a
   config like [0.99, 0.10] won over a stable [0.70, 0.70].
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

import bakeoff_mage_tier34_compare as compare  # type: ignore  # noqa: E402


def _survey(rows: list[dict[str, object]]) -> dict[str, object]:
    """Minimal survey JSON shape matching calibration_survey output."""
    return {"rows": rows, "n_signals": len(rows)}


def _write_phase_a(tmp_path: Path, model: str, das: dict[str, float | None]) -> None:
    rows = [
        {"signal": sig, "direction_aware_auc": da}
        for sig, da in das.items()
    ]
    (tmp_path / f"survey_phaseA_{model}.json").write_text(
        json.dumps(_survey(rows)), encoding="utf-8",
    )


class TestDaAucReader:
    def test_reads_direction_aware_auc_from_rows(self):
        survey = _survey([
            {"signal": "adjacent_cosine_mean", "direction_aware_auc": 0.713},
            {"signal": "adjacent_cosine_sd", "direction_aware_auc": 0.642},
        ])
        assert compare._da_auc(survey, "adjacent_cosine_mean") == 0.713
        assert compare._da_auc(survey, "adjacent_cosine_sd") == 0.642

    def test_returns_none_for_unknown_signal(self):
        survey = _survey([
            {"signal": "adjacent_cosine_mean", "direction_aware_auc": 0.7},
        ])
        assert compare._da_auc(survey, "surprisal_mean") is None

    def test_returns_none_when_value_is_null(self):
        survey = _survey([
            {"signal": "adjacent_cosine_mean", "direction_aware_auc": None},
        ])
        assert compare._da_auc(survey, "adjacent_cosine_mean") is None

    def test_returns_none_when_rows_key_absent(self):
        # An old survey or a malformed file — graceful None, no crash.
        assert compare._da_auc({}, "adjacent_cosine_mean") is None

    def test_rejects_bool_values(self):
        # bool is a subclass of int in Python; guard against it.
        survey = _survey([
            {"signal": "adjacent_cosine_mean", "direction_aware_auc": True},
        ])
        assert compare._da_auc(survey, "adjacent_cosine_mean") is None

    def test_skips_legacy_per_signal_layout(self):
        # Pin the bug: a survey with ONLY the never-emitted `per_signal`
        # layout returns None, not the value, so we don't silently fall
        # back to a path the real survey doesn't populate.
        survey = {
            "per_signal": {
                "adjacent_cosine_mean": {
                    "calibration": {"direction_aware_auc": 0.9}
                }
            }
        }
        assert compare._da_auc(survey, "adjacent_cosine_mean") is None


class TestWinnerSelection:
    SIGNALS = ["adjacent_cosine_mean", "adjacent_cosine_sd"]

    def _format(self, tmp_path: Path, models: list[str]) -> str:
        return compare._format_table(
            "A", models, self.SIGNALS, tmp_path, "survey_phaseA",
        )

    def test_inverted_sibling_disqualifies_high_max_config(self, tmp_path):
        # mxbai = [0.99, 0.10] — one excellent, one polarity-inverted.
        # gemma = [0.70, 0.70] — stable across both signals.
        # Pre-fix: mxbai wins on max=0.99. Post-fix: mxbai disqualified.
        _write_phase_a(tmp_path, "mxbai", {
            "adjacent_cosine_mean": 0.99,
            "adjacent_cosine_sd": 0.10,
        })
        _write_phase_a(tmp_path, "gemma", {
            "adjacent_cosine_mean": 0.70,
            "adjacent_cosine_sd": 0.70,
        })
        table = self._format(tmp_path, ["mxbai", "gemma"])
        assert "recommended winner**: `gemma`" in table
        assert "recommended winner**: `mxbai`" not in table
        # Diagnostic line names the disqualification.
        assert "min da_AUC = 0.7000" in table

    def test_ranks_eligible_by_min_not_max(self, tmp_path):
        # Both configs are eligible (no signal < 0.5).
        # mxbai max is higher (0.80) but min is lower (0.55).
        # gemma min is higher (0.65); gemma should win on min-rank.
        _write_phase_a(tmp_path, "mxbai", {
            "adjacent_cosine_mean": 0.80,
            "adjacent_cosine_sd": 0.55,
        })
        _write_phase_a(tmp_path, "gemma", {
            "adjacent_cosine_mean": 0.70,
            "adjacent_cosine_sd": 0.65,
        })
        table = self._format(tmp_path, ["mxbai", "gemma"])
        assert "recommended winner**: `gemma`" in table

    def test_missing_signal_disqualifies(self, tmp_path):
        # A config that only scored one of two target signals is not
        # comparable across the phase — disqualify, even if the
        # observed signal is excellent.
        _write_phase_a(tmp_path, "harrier", {
            "adjacent_cosine_mean": 0.95,
            "adjacent_cosine_sd": None,
        })
        _write_phase_a(tmp_path, "minilm", {
            "adjacent_cosine_mean": 0.60,
            "adjacent_cosine_sd": 0.58,
        })
        table = self._format(tmp_path, ["harrier", "minilm"])
        assert "recommended winner**: `minilm`" in table
        assert "recommended winner**: `harrier`" not in table

    def test_no_winner_when_every_config_inverted(self, tmp_path):
        _write_phase_a(tmp_path, "mxbai", {
            "adjacent_cosine_mean": 0.45,
            "adjacent_cosine_sd": 0.30,
        })
        _write_phase_a(tmp_path, "gemma", {
            "adjacent_cosine_mean": 0.40,
            "adjacent_cosine_sd": 0.42,
        })
        table = self._format(tmp_path, ["mxbai", "gemma"])
        assert "no winner" in table
        assert "polarity-inverted" in table
        # Both models named in the diagnostic.
        assert "`mxbai`" in table
        assert "`gemma`" in table

    def test_missing_file_renders_missing_cells(self, tmp_path):
        # Don't write any survey for "ghost".
        _write_phase_a(tmp_path, "gemma", {
            "adjacent_cosine_mean": 0.70,
            "adjacent_cosine_sd": 0.70,
        })
        table = self._format(tmp_path, ["ghost", "gemma"])
        assert "MISSING" in table
        # gemma still wins; ghost disqualified for missing data.
        assert "recommended winner**: `gemma`" in table

    def test_winner_announcement_includes_signal_count(self, tmp_path):
        _write_phase_a(tmp_path, "gemma", {
            "adjacent_cosine_mean": 0.70,
            "adjacent_cosine_sd": 0.68,
        })
        table = self._format(tmp_path, ["gemma"])
        assert f"across {len(self.SIGNALS)} signals" in table


class TestFormatDa:
    def test_inversion_marker(self):
        assert compare._format_da(0.45).startswith("!")

    def test_clear_separation_marker(self):
        assert compare._format_da(0.60).startswith("*")

    def test_middle_band_unmarked(self):
        s = compare._format_da(0.52)
        assert not s.startswith("!")
        assert not s.startswith("*")

    def test_none_renders_dashes(self):
        assert "--" in compare._format_da(None)
