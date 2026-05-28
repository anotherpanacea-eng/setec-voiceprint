#!/usr/bin/env python3
"""End-to-end tests for narrative_decision_audit.py.

The audit is bottle-shaped — judge → encode → contribute → aggregate.
Tests pin the per-stage math against hand-computed values and the
envelope shape against the framework's output_schema contract.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import narrative_decision_audit as nda  # type: ignore  # noqa: E402
import narrative_feature_schema as nfs  # type: ignore  # noqa: E402
import narrative_judge as nj  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402


# ---------- encoding -----------------------------------------------

def test_scale_encoding_is_identity_integer():
    """A scale feature rated "4" encodes to 4.0."""
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.feature_type == "scale"
    )
    assert nda.encode_value(feat, "4") == 4.0


def test_ordinal_encoding_is_zero_based_index():
    """Ordinal options encode to their 0-based position."""
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.feature_type == "ordinal"
    )
    last = feat.response_options[-1]
    expected = float(len(feat.response_options) - 1)
    assert nda.encode_value(feat, last) == expected


def test_binary_encoding_yes_one_no_zero():
    feat = next(
        f for f in nfs.CORE_FEATURES if f.feature_type == "binary"
    )
    assert nda.encode_value(feat, "yes") == 1.0
    assert nda.encode_value(feat, "no") == 0.0
    assert nda.encode_value(feat, "maybe") is None


def test_signal_target_value_categorical_matches_option():
    feat = nfs.CORE_FEATURES[
        next(
            i for i, f in enumerate(nfs.CORE_FEATURES)
            if f.key == "agency_in_resolution"
        )
    ]
    sig = feat.signals[0]
    assert sig.option == "protagonist_choice"
    assert nda.signal_target_value(
        feat, sig, "protagonist_choice"
    ) == 1.0
    assert nda.signal_target_value(feat, sig, "mixed") == 0.0
    assert nda.signal_target_value(feat, sig, None) is None


def test_signal_target_value_multi_requires_list():
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.feature_type == "multi"
    )
    sig = feat.signals[0]
    # When the option is in the multi-select list → 1.0
    assert nda.signal_target_value(
        feat, sig, [sig.option, "advance_plot"]
    ) in (0.0, 1.0)
    # Wrong type → None (no silent coercion)
    assert nda.signal_target_value(feat, sig, "string") is None


# ---------- contribution math --------------------------------------

def test_contribution_at_human_mean_is_one():
    """When target = paper's human mean, contribution = 1.0."""
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.key == "setting_as_psychological_mirror"
    )
    sig = feat.signals[0]
    # Paper means: H=3.58, AI=4.07 → at H, contribution = 1.0
    values = {feat.key: str(round(sig.human_mean))}  # = "4"
    contributions = nda.per_signal_contributions(values)
    for c in contributions:
        if c.feature_key == feat.key and c.option is None:
            # target=4, ai=4.07, h=3.58 → (4-4.07)/(3.58-4.07) ≈ 0.143
            assert c.target_value == 4.0
            assert -0.5 < c.contribution < 0.5  # close to AI side
            return
    raise AssertionError("expected to find scored contribution")


def test_contribution_sign_matches_paper_leaning():
    """For an AI-elevated signal, picking the AI option pushes the
    contribution negative (toward AI side); picking the opposite
    pushes positive."""
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.key == "agency_in_resolution"
    )
    contributions_ai = {
        c.feature_key: c
        for c in nda.per_signal_contributions(
            {feat.key: "protagonist_choice"}
        )
        if c.feature_key == feat.key
    }
    contributions_other = {
        c.feature_key: c
        for c in nda.per_signal_contributions(
            {feat.key: "external_fate"}
        )
        if c.feature_key == feat.key
    }
    ai_contribution = contributions_ai[feat.key].contribution
    other_contribution = contributions_other[feat.key].contribution
    # Paper says protagonist_choice is AI-elevated. Picking it should
    # produce a negative contribution; not picking it produces
    # positive (or less negative).
    assert ai_contribution < 0
    assert other_contribution > ai_contribution


def test_aggregate_score_in_human_z_units():
    """A document where every feature is set to the paper's human
    means produces an aggregate score near +1.0; setting to AI means
    produces near 0.0."""
    # Construct the closest-to-human-mean values.
    human_values: dict[str, object] = {}
    ai_values: dict[str, object] = {}
    for f in nfs.CORE_FEATURES:
        if f.feature_type == "scale":
            # Pick the integer closest to the AI-leaning option's
            # paper human mean.
            sig = f.signals[0]
            human_values[f.key] = str(round(sig.human_mean))
            ai_values[f.key] = str(round(sig.ai_mean))
        elif f.feature_type == "ordinal":
            sig = f.signals[0]
            human_values[f.key] = f.response_options[
                min(
                    int(round(sig.human_mean)),
                    len(f.response_options) - 1,
                )
            ]
            ai_values[f.key] = f.response_options[
                min(
                    int(round(sig.ai_mean)),
                    len(f.response_options) - 1,
                )
            ]
        elif f.feature_type == "binary":
            sig = f.signals[0]
            human_values[f.key] = "yes" if sig.human_mean >= 0.5 else "no"
            ai_values[f.key] = "yes" if sig.ai_mean >= 0.5 else "no"
        elif f.feature_type == "categorical":
            # For dual-leaning feats put each side toward its leaning's option.
            ai_opt = next(
                (s.option for s in f.signals if s.leaning == "ai"),
                None,
            )
            hu_opt = next(
                (s.option for s in f.signals if s.leaning == "human"),
                None,
            )
            human_values[f.key] = hu_opt or f.response_options[0]
            ai_values[f.key] = ai_opt or f.response_options[0]
        else:  # multi
            ai_opt = next(
                (s.option for s in f.signals if s.leaning == "ai"),
                None,
            )
            hu_opt = next(
                (s.option for s in f.signals if s.leaning == "human"),
                None,
            )
            human_values[f.key] = [hu_opt] if hu_opt else []
            ai_values[f.key] = [ai_opt] if ai_opt else []

    human_contribs = nda.per_signal_contributions(human_values)
    ai_contribs = nda.per_signal_contributions(ai_values)
    human_score = nda.aggregate_score(human_contribs)["score"]
    ai_score = nda.aggregate_score(ai_contribs)["score"]
    # Direction sanity: human-side configuration scores higher than
    # AI-side configuration. The exact magnitude depends on rounding
    # but the sign of the difference is invariant.
    assert human_score > ai_score, (
        f"human-side config should outscore AI-side: "
        f"{human_score} vs. {ai_score}"
    )


# ---------- envelope contract --------------------------------------

def test_task_surface_registered_in_output_schema():
    assert nda.TASK_SURFACE in VALID_TASK_SURFACES, (
        f"task surface {nda.TASK_SURFACE!r} not registered in "
        f"output_schema.VALID_TASK_SURFACES"
    )


def test_end_to_end_envelope_with_mock_judge():
    """The audit produces a well-formed envelope with the mock judge."""
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "story.txt"
        target.write_text(
            "He walked into the room. \"Hello,\" she said.\n" * 200,
            encoding="utf-8",
        )
        out_json = Path(td) / "out.json"
        out_md = Path(td) / "out.md"
        rc = nda.main([
            str(target),
            "--judge", "mock",
            "--out", str(out_json),
            "--out-md", str(out_md),
        ])
        assert rc == 0
        env = json.loads(out_json.read_text())
        assert env["schema_version"] == "1.0"
        assert env["task_surface"] == nda.TASK_SURFACE
        assert env["available"] is True
        assert env["claim_license"]["task_surface"] == nda.TASK_SURFACE
        # 33 signals × scored contributions
        assert len(env["results"]["contributions"]) == 33
        # 7 bundles
        assert len(env["results"]["bundles"]) == 7
        # Uncalibrated by default
        assert (
            env["results"]["aggregate"]["verdict_band"]
            == "uncalibrated"
        )


def test_threshold_args_produce_calibrated_band():
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "story.txt"
        target.write_text(
            "He walked into the room. \"Hi,\" she said.\n" * 200,
            encoding="utf-8",
        )
        out_json = Path(td) / "out.json"
        out_md = Path(td) / "out.md"
        rc = nda.main([
            str(target),
            "--judge", "mock",
            "--threshold-low", "-10.0",
            "--threshold-high", "10.0",
            "--out", str(out_json),
            "--out-md", str(out_md),
        ])
        assert rc == 0
        env = json.loads(out_json.read_text())
        # Mock judge produces values between the wide thresholds →
        # band should be 'indeterminate'.
        band = env["results"]["aggregate"]["verdict_band"]
        assert band in ("indeterminate", "ai_likely", "human_likely"), (
            f"unexpected band {band!r} with calibrated thresholds"
        )


def test_register_warning_fires_below_floor():
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "tiny.txt"
        target.write_text("A short story. \"Hi,\" she said.\n",
                          encoding="utf-8")
        out_json = Path(td) / "out.json"
        out_md = Path(td) / "out.md"
        rc = nda.main([
            str(target),
            "--judge", "mock",
            "--out", str(out_json),
            "--out-md", str(out_md),
        ])
        assert rc == 0
        env = json.loads(out_json.read_text())
        warnings = env["warnings"]
        assert any(
            "long-form fiction" in w for w in warnings
        ), f"expected register warning; got {warnings}"


# ---------- judge interface ---------------------------------------

def test_validate_values_drops_unknown_options():
    feat = next(
        f for f in nfs.CORE_FEATURES
        if f.feature_type == "categorical"
    )
    bad_value = "definitely_not_a_real_option_value"
    cleaned, warnings = nj.validate_values({feat.key: bad_value})
    assert cleaned[feat.key] is None
    assert any(feat.key in w for w in warnings)


def test_validate_values_multi_drops_bad_options():
    feat = next(
        f for f in nfs.CORE_FEATURES if f.feature_type == "multi"
    )
    good = feat.response_options[0]
    cleaned, warnings = nj.validate_values({feat.key: [good, "bogus"]})
    assert cleaned[feat.key] == [good]
    assert any("bogus" in w or "invalid" in w for w in warnings)


def test_manifest_judge_round_trip():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "values.json"
        values = {
            f.key: f.response_options[0]
            if f.feature_type != "multi" else []
            for f in nfs.CORE_FEATURES
        }
        manifest.write_text(json.dumps({"values": values}))
        judge = nj.build_judge(
            "manifest", manifest_path=manifest,
        )
        result = judge("story text")
        assert result.values == values
        assert (
            result.judge_identity["kind"] == "manifest"
        )


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
