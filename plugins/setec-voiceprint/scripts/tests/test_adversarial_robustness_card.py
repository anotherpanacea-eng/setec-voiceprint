#!/usr/bin/env python3
"""Regression tests for adversarial_robustness_card.py (Release 7)."""

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

import adversarial_robustness_card as arc  # type: ignore


# ---------- Fixtures ----------


def _make_audit(burstiness: float = -0.40, mtld: float = 80.0,
                connective: float = 25.0,
                compression_fraction: float = 0.20) -> dict:
    return {
        "audit": {
            "tier1": {
                "sentence_length": {
                    "burstiness_B": burstiness, "sd": 12.0,
                },
                "mtld": mtld,
                "mattr": {"value": 0.65},
                "shannon_entropy_bits": 9.0,
                "yules_k": 100.0,
                "fkgl": {"sd": 2.5},
                "connective_density": {"per_1000_tokens": connective},
            },
        },
        "compression": {"compression_fraction": compression_fraction},
    }


# ---------- Signal extraction ----------


class TestExtractSignal:
    def test_known_signal_extracted(self):
        audit = _make_audit()
        val = arc._extract_signal(
            audit, ("audit", "tier1", "sentence_length", "burstiness_B"),
        )
        assert val == -0.40

    def test_missing_path_returns_none(self):
        val = arc._extract_signal({}, ("audit", "tier1", "x"))
        assert val is None

    def test_non_numeric_returns_none(self):
        audit = {"audit": {"tier1": {"x": "string-value"}}}
        val = arc._extract_signal(audit, ("audit", "tier1", "x"))
        assert val is None


class TestExtractAllSignals:
    def test_returns_all_known_signals(self):
        signals = arc._extract_all_signals(_make_audit())
        for required in (
            "burstiness_B", "mtld", "mattr",
            "shannon_entropy", "yules_k",
            "connective_density", "compression_fraction",
        ):
            assert required in signals
            assert isinstance(signals[required], float)


# ---------- Movement classifier ----------


class TestClassifyMovement:
    def test_stable_when_change_small(self):
        # 5% change → stable (default threshold 10%).
        label, rel = arc._classify_movement(
            base_value=1.0, fixture_value=1.05,
        )
        assert label == "stable"

    def test_moderate_at_intermediate_change(self):
        # 20% change → moderate (between 10% and 30%).
        label, _ = arc._classify_movement(
            base_value=1.0, fixture_value=1.20,
        )
        assert label == "moderate"

    def test_fragile_at_large_change(self):
        # 50% change → fragile.
        label, _ = arc._classify_movement(
            base_value=1.0, fixture_value=1.50,
        )
        assert label == "fragile"

    def test_inverted_polarity_on_sign_flip(self):
        # base = -0.40, fixture = +0.30 → sign flip on a clearly
        # non-zero base.
        label, _ = arc._classify_movement(
            base_value=-0.40, fixture_value=0.30,
        )
        assert label == "inverted_polarity"

    def test_unknown_when_either_value_missing(self):
        assert arc._classify_movement(None, 1.0)[0] == "unknown"
        assert arc._classify_movement(1.0, None)[0] == "unknown"

    def test_small_base_when_fixture_also_near_zero(self):
        # Near-zero base + near-zero fixture → uninterpretable
        # AND uninteresting; label `small_base`.
        label, _ = arc._classify_movement(
            base_value=1e-9, fixture_value=0.05,
        )
        assert label == "small_base"

    def test_unstable_small_base_when_fixture_moves(self):
        # Near-zero base + large absolute fixture movement →
        # `unstable_small_base`. Reviewer-reproduced regression:
        # the previous code returned `small_base` here, which the
        # aggregator dropped, so a 0.0 → 0.5 movement showed up as
        # `overall_robustness=unknown`. The fix distinguishes the
        # two cases.
        label, _ = arc._classify_movement(
            base_value=0.0, fixture_value=0.5,
        )
        assert label == "unstable_small_base"

    def test_unstable_small_base_for_negative_fixture(self):
        label, _ = arc._classify_movement(
            base_value=1e-9, fixture_value=-0.4,
        )
        assert label == "unstable_small_base"


# ---------- build_robustness_card ----------


class TestBuildRobustnessCard:
    def test_no_fixtures_returns_unknown_for_all(self):
        card = arc.build_robustness_card(
            base=_make_audit(), fixtures=[],
        )
        for sig, info in card["per_signal"].items():
            assert info["overall_robustness"] == "unknown"

    def test_stable_under_light_change(self):
        # Light copyedit: tiny changes within 10% threshold.
        light = _make_audit(burstiness=-0.41, mtld=78.0, connective=25.5)
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("light_copyedit", light)],
        )
        # burstiness_B moved from -0.40 to -0.41 (2.5% change). Stable.
        burstiness_info = card["per_signal"]["burstiness_B"]
        assert burstiness_info["overall_robustness"] == "stable"

    def test_fragile_under_heavy_change(self):
        # Heavy paraphrase: large changes.
        para = _make_audit(burstiness=0.30, mtld=50.0, connective=35.0)
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("paraphrase", para)],
        )
        # burstiness_B sign-flips → inverted_polarity → fragile overall.
        burstiness_info = card["per_signal"]["burstiness_B"]
        assert burstiness_info["overall_robustness"] == "fragile"

    def test_inverted_polarity_recorded(self):
        para = _make_audit(burstiness=0.30)
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("paraphrase", para)],
        )
        # The inverted polarity is recorded at per-fixture level.
        cell = card["per_signal"]["burstiness_B"]["per_fixture"]["paraphrase"]
        assert cell["label"] == "inverted_polarity"

    def test_aggregate_counts(self):
        light = _make_audit(burstiness=-0.41)  # stable on burstiness
        para = _make_audit(burstiness=0.30)  # inverted on burstiness
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("light_copyedit", light), ("paraphrase", para)],
        )
        # burstiness is fragile across fixtures (paraphrase inverted it).
        assert card["n_inverted_polarity_readings"] >= 1

    def test_unstable_small_base_aggregates_to_fragile(self):
        # Reviewer-reproduced regression: compression_fraction
        # 0.0 → 0.5 in a fixture should NOT silently fall to
        # `overall_robustness=unknown`. The fix flags
        # `unstable_small_base` at the cell level and aggregates
        # it to `fragile` overall.
        base = _make_audit(compression_fraction=0.0)
        fixture = _make_audit(compression_fraction=0.5)
        card = arc.build_robustness_card(
            base=base, fixtures=[("paraphrase", fixture)],
        )
        info = card["per_signal"]["compression_fraction"]
        cell = info["per_fixture"]["paraphrase"]
        assert cell["label"] == "unstable_small_base"
        assert info["overall_robustness"] == "fragile"

    def test_unstable_small_base_counted_in_aggregate(self):
        base = _make_audit(compression_fraction=0.0)
        fixture = _make_audit(compression_fraction=0.5)
        card = arc.build_robustness_card(
            base=base, fixtures=[("paraphrase", fixture)],
        )
        assert card["n_unstable_small_base_readings"] >= 1
        # And the signal counts as fragile in the aggregate.
        assert card["n_fragile_signals"] >= 1

    def test_pure_small_base_does_not_aggregate_to_fragile(self):
        # Both base and fixture near zero → label `small_base`,
        # NOT counted toward fragile.
        base = _make_audit(compression_fraction=0.0)
        fixture = _make_audit(compression_fraction=0.01)
        card = arc.build_robustness_card(
            base=base, fixtures=[("light_copyedit", fixture)],
        )
        info = card["per_signal"]["compression_fraction"]
        cell = info["per_fixture"]["light_copyedit"]
        assert cell["label"] == "small_base"
        # No labels_seen → unknown (preserves existing behavior).
        assert info["overall_robustness"] == "unknown"

    def test_threshold_customization(self):
        # Tighten thresholds: 5% / 15%. A 10% change becomes
        # moderate instead of stable.
        light = _make_audit(burstiness=-0.44)  # 10% change
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("test", light)],
            stability_threshold=0.05,
            fragile_threshold=0.15,
        )
        burstiness_info = card["per_signal"]["burstiness_B"]
        cell = burstiness_info["per_fixture"]["test"]
        assert cell["label"] == "moderate"


# ---------- Render ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("paraphrase", _make_audit(burstiness=0.30))],
        )
        md = arc.render_report(card)
        assert "## What this result licenses" in md

    def test_markdown_renders_card_table(self):
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("paraphrase", _make_audit(burstiness=0.30))],
        )
        md = arc.render_report(card)
        assert "## Robustness card" in md
        assert "paraphrase" in md

    def test_markdown_no_fixtures_message(self):
        card = arc.build_robustness_card(
            base=_make_audit(), fixtures=[],
        )
        md = arc.render_report(card)
        assert "No fixtures supplied" in md

    def test_markdown_renders_notable_signals(self):
        card = arc.build_robustness_card(
            base=_make_audit(),
            fixtures=[("paraphrase", _make_audit(burstiness=0.30))],
        )
        md = arc.render_report(card)
        # Notable section appears for fragile / mixed signals.
        assert "## Notable signals" in md or "burstiness_B" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        base_path = tmp_path / "base.json"
        base_path.write_text(
            json.dumps(_make_audit()),
            encoding="utf-8",
        )
        fixture_path = tmp_path / "para.json"
        fixture_path.write_text(
            json.dumps(_make_audit(burstiness=0.30)),
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = arc.main([
            "--base", str(base_path),
            "--fixture", f"paraphrase:{fixture_path}",
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "validation"
        assert "per_signal" in payload

    def test_cli_missing_base_returns_2(self, tmp_path):
        rc = arc.main([
            "--base", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_missing_fixture_returns_2(self, tmp_path):
        base_path = tmp_path / "base.json"
        base_path.write_text(
            json.dumps(_make_audit()), encoding="utf-8",
        )
        rc = arc.main([
            "--base", str(base_path),
            "--fixture", f"missing:{tmp_path / 'missing.json'}",
        ])
        assert rc == 2

    def test_cli_invalid_fixture_format(self, tmp_path):
        base_path = tmp_path / "base.json"
        base_path.write_text(
            json.dumps(_make_audit()), encoding="utf-8",
        )
        # No colon in fixture spec → argparse raises SystemExit
        # via type=callable.
        with pytest.raises(SystemExit) as excinfo:
            arc.main([
                "--base", str(base_path),
                "--fixture", "no_colon",
            ])
        # argparse returns code 2 for argument errors.
        assert excinfo.value.code == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
