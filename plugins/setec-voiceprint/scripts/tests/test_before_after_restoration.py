#!/usr/bin/env python3
"""Regression tests for before_after_restoration.py.

Synthetic fixtures simulate the four verdict paths:

  improved   targets moved in the intended direction by more than the
             noise threshold.
  no_change  targets moved within the noise threshold.
  degraded   targets moved opposite to the intended direction.
  gamed      targets improved AND the avoid_direct aggregate (POS-bigram
             KL) moved against improvement.

Plus the preservation-list survival check: the "preserved" revised
text contains every phrase from the packet's preservation list; the
"dropped" revised text omits two of them.

The taxonomy-+-direction logic is the load-bearing thing: a verdict
must reflect the registry's compression direction (lt vs. gt) for
variance signals, and |kl_contrib|-reduction for bigram signals.
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

import before_after_restoration as bar  # type: ignore


FIXTURE_DIR = ROOT / "test_data" / "before_after_restoration"
PACKET = FIXTURE_DIR / "synthetic_packet.json"
BEFORE_VAR = FIXTURE_DIR / "before_variance.json"
BEFORE_BIGRAM = FIXTURE_DIR / "before_bigram.json"

AFTER_VAR_IMPROVED = FIXTURE_DIR / "after_variance_improved.json"
AFTER_VAR_GAMED = FIXTURE_DIR / "after_variance_gamed.json"
AFTER_VAR_DEGRADED = FIXTURE_DIR / "after_variance_degraded.json"
AFTER_BIGRAM_IMPROVED = FIXTURE_DIR / "after_bigram_improved.json"

REVISED_PRESERVED = FIXTURE_DIR / "revised_text_preserved.txt"
REVISED_DROPPED = FIXTURE_DIR / "revised_text_dropped.txt"


def _args(**overrides) -> argparse.Namespace:
    base = {
        "packet_json": str(PACKET),
        "before_variance_json": str(BEFORE_VAR),
        "after_variance_json": str(AFTER_VAR_IMPROVED),
        "before_bigram_json": str(BEFORE_BIGRAM),
        "after_bigram_json": str(AFTER_BIGRAM_IMPROVED),
        "before_voice_json": None,
        "after_voice_json": None,
        "before_idiolect_json": None,
        "after_idiolect_json": None,
        "original_text": None,
        "revised_text": None,
        "diff_only": False,
        "out": None,
        "json_out": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---- Verdict classification --------------------------------


def test_improved_path_classifies_burstiness_as_improved() -> None:
    """burstiness_B has registry direction 'lt' (compressed when low).
    Before: -0.48. After: -0.30. Delta = +0.18. signed_improvement
    = +0.18 (going up = improvement). Noise = 0.05. Verdict =
    'improved'."""
    result = bar.run(_args())
    by_id = {v.target_id: v for v in result["verdicts"]}
    burst = by_id["variance_burstiness_B_direct"]
    assert burst.before == -0.48
    assert burst.after == -0.30
    assert burst.delta is not None and abs(burst.delta - 0.18) < 1e-9
    assert burst.signed_improvement > 0.05
    assert burst.verdict == "improved"


def test_improved_path_classifies_connective_density_as_improved() -> None:
    """connective_density has registry direction 'gt' (compressed
    when high). Before: 28.0. After: 22.0. signed_improvement = +6.0.
    Verdict = 'improved'."""
    result = bar.run(_args())
    by_id = {v.target_id: v for v in result["verdicts"]}
    cd = by_id["variance_connective_density_direct"]
    assert cd.before == 28.0
    assert cd.after == 22.0
    assert cd.signed_improvement > 0
    assert cd.verdict == "improved"


def test_improved_path_classifies_bigram_as_improved() -> None:
    """ADJ-NOUN kl_contrib reduced from 0.037 to 0.008. |reduction|
    = 0.029. Noise = 0.005. Verdict = 'improved'."""
    result = bar.run(_args())
    by_id = {v.target_id: v for v in result["verdicts"]}
    bg = by_id["pos_bigram_ADJ_NOUN_over_represented"]
    assert bg.before == 0.037
    assert bg.after == 0.008
    assert bg.signed_improvement > 0.005
    assert bg.verdict == "improved"


def test_degraded_path_classifies_burstiness_as_degraded() -> None:
    """burstiness_B before -0.48, after -0.62. Delta = -0.14
    (more compressed). signed_improvement = -0.14. Verdict =
    'degraded'."""
    result = bar.run(_args(after_variance_json=str(AFTER_VAR_DEGRADED)))
    by_id = {v.target_id: v for v in result["verdicts"]}
    burst = by_id["variance_burstiness_B_direct"]
    assert burst.signed_improvement < -0.05
    assert burst.verdict == "degraded"


def test_degraded_path_classifies_connective_density_as_degraded() -> None:
    """connective_density before 28, after 33. signed_improvement
    = -5.0. Verdict = 'degraded'."""
    result = bar.run(_args(after_variance_json=str(AFTER_VAR_DEGRADED)))
    by_id = {v.target_id: v for v in result["verdicts"]}
    cd = by_id["variance_connective_density_direct"]
    assert cd.signed_improvement < -1.0
    assert cd.verdict == "degraded"


# ---- Metric-gaming detection -------------------------------


def test_gaming_flips_improved_to_gamed_when_aggregate_kl_rose() -> None:
    """In the gamed fixture, burstiness_B and connective_density
    improved (slightly), but aggregate POS-bigram KL went from 0.18
    to 0.26 (delta +0.08, over the 0.02 noise threshold). The
    metric-gaming heuristic should flip the verdict for the actionable
    targets that improved."""
    result = bar.run(_args(after_variance_json=str(AFTER_VAR_GAMED)))
    by_id = {v.target_id: v for v in result["verdicts"]}
    burst = by_id["variance_burstiness_B_direct"]
    cd = by_id["variance_connective_density_direct"]
    # Both targets moved in the right direction by enough to count as
    # improved before the gaming check.
    assert burst.signed_improvement > 0.05
    assert cd.signed_improvement > 1.0
    # Both should now be flagged as gamed.
    assert burst.verdict == "gamed"
    assert cd.verdict == "gamed"
    # And the notes should explain why.
    assert any("Metric-gaming flag" in n for n in burst.notes)


def test_gaming_records_aggregate_delta() -> None:
    result = bar.run(_args(after_variance_json=str(AFTER_VAR_GAMED)))
    delta = result["aggregate_deltas"]["pos_bigram_kl_total"]
    assert delta is not None
    assert abs(delta - 0.08) < 1e-9


def test_no_gaming_flag_when_aggregate_did_not_rise() -> None:
    """In the improved fixture, aggregate KL went from 0.18 to 0.12
    (improvement). No gaming flag should fire."""
    result = bar.run(_args())
    by_id = {v.target_id: v for v in result["verdicts"]}
    burst = by_id["variance_burstiness_B_direct"]
    assert burst.verdict == "improved"
    assert not any("Metric-gaming flag" in n for n in burst.notes)


# ---- avoid_direct never claims improvement -----------------


def test_avoid_direct_pos_bigram_kl_aggregate_is_not_measurable() -> None:
    """The avoid_direct aggregate packet should never produce a
    verdict of 'improved' or 'degraded' -- those would imply the
    metric is a target. Even when the aggregate moved a lot, the
    verdict stays 'not_measurable' and the delta is reported as
    evidence."""
    result = bar.run(_args(after_variance_json=str(AFTER_VAR_GAMED)))
    by_id = {v.target_id: v for v in result["verdicts"]}
    agg = by_id.get("variance_pos_bigram_kl_aggregate")
    assert agg is not None
    assert agg.verdict == "not_measurable"
    assert agg.delta is not None
    assert agg.before == 0.18
    assert agg.after == 0.26


# ---- Preservation-list survival ----------------------------


def test_preservation_check_passes_when_all_phrases_survive() -> None:
    result = bar.run(_args(revised_text=str(REVISED_PRESERVED)))
    pc = result["preservation_check"]
    assert pc is not None
    assert pc["checked"] is True
    assert pc["n_total"] == 4
    assert pc["n_survived"] == 4
    assert pc["n_missing"] == 0
    assert pc["survival_rate"] == 1.0


def test_preservation_check_flags_missing_phrases() -> None:
    """The 'dropped' fixture omits 'moral allocation' and
    'load-bearing'; 'as a practical matter' and 'policy adjacent'
    survive. Survival = 2/4."""
    result = bar.run(_args(revised_text=str(REVISED_DROPPED)))
    pc = result["preservation_check"]
    assert pc is not None
    assert pc["n_total"] == 4
    assert pc["n_survived"] == 2
    assert pc["n_missing"] == 2
    missing = set(pc["missing_phrases"])
    assert "moral allocation" in missing
    assert "load-bearing" in missing


def test_preservation_check_skips_when_no_revised_text() -> None:
    """Without --revised-text, the check is skipped silently (the
    JSON output reports the absence)."""
    result = bar.run(_args())
    assert result["preservation_check"] is None


# ---- Diff-only mode ----------------------------------------


def test_diff_only_mode_reports_all_signals() -> None:
    args = _args(packet_json=None)
    result = bar.run(args)
    diff = result["diff_only"]
    assert diff is not None
    var = diff["variance"]
    # Every signal in the registry that has both before and after
    # values should appear.
    assert "burstiness_B" in var
    assert var["burstiness_B"]["before"] == -0.48
    assert var["burstiness_B"]["after"] == -0.30
    assert "connective_density" in var
    assert "pos_bigram_kl_total" in var


def test_diff_only_includes_band_shift() -> None:
    args = _args(packet_json=None)
    result = bar.run(args)
    band = result["diff_only"]["variance"]["band"]
    assert band["before"] == "Moderately smoothed"
    assert band["after"] == "Lightly smoothed"


# ---- Summary + JSON / Markdown output ----------------------


def test_summary_counts_match_verdicts() -> None:
    result = bar.run(_args())
    json_str = bar.render_json(
        verdicts=result["verdicts"],
        aggregate_deltas=result["aggregate_deltas"],
        preservation_check=result["preservation_check"],
        diff_only=result["diff_only"],
        inputs=result["inputs"],
        packet_summary=result["packet_summary"],
    )
    parsed = json.loads(json_str)
    assert parsed["task_surface"] == "craft_restoration"
    assert parsed["tool"] == "before_after_restoration"
    summary = parsed["verdict_summary"]
    assert summary["improved"] >= 2  # burstiness, connective, ADJ-NOUN
    assert summary["improved"] + summary["no_change"] + summary["degraded"] + summary["gamed"] + summary["not_measurable"] == len(parsed["verdicts"])


def test_markdown_renders_verdict_table() -> None:
    result = bar.run(_args())
    md = bar.render_markdown(
        verdicts=result["verdicts"],
        aggregate_deltas=result["aggregate_deltas"],
        preservation_check=result["preservation_check"],
        diff_only=result["diff_only"],
        packet_summary=result["packet_summary"],
    )
    assert "Verdict summary" in md
    assert "Per-target verdicts" in md
    assert "improved" in md.lower()
    # The avoid_direct packet's notes should appear (its verdict is
    # not_measurable, but the delta is informative evidence).
    assert "avoid_direct" in md.lower()


# ---- CLI smoke test ----------------------------------------


def test_cli_main_runs_end_to_end(tmp_path) -> None:
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"
    rc = bar.main([
        "--packet-json", str(PACKET),
        "--before-variance-json", str(BEFORE_VAR),
        "--after-variance-json", str(AFTER_VAR_IMPROVED),
        "--before-bigram-json", str(BEFORE_BIGRAM),
        "--after-bigram-json", str(AFTER_BIGRAM_IMPROVED),
        "--revised-text", str(REVISED_PRESERVED),
        "--out", str(out_md),
        "--json-out", str(out_json),
    ])
    assert rc == 0
    assert out_md.is_file()
    assert out_json.is_file()
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["task_surface"] == "craft_restoration"


def test_cli_refuses_zero_inputs() -> None:
    rc = bar.main([])
    assert rc == 1


def test_cli_diff_only_works_without_packet(tmp_path) -> None:
    out_json = tmp_path / "diff.json"
    rc = bar.main([
        "--before-variance-json", str(BEFORE_VAR),
        "--after-variance-json", str(AFTER_VAR_IMPROVED),
        "--json-out", str(out_json),
    ])
    assert rc == 0
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["diff_only"] is not None
    assert "variance" in parsed["diff_only"]
