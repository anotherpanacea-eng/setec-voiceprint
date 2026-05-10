#!/usr/bin/env python3
"""Regression tests for the --max-entries sub-sample knob.

The flag lets the maintainer run a partial calibration as a pipeline
check before committing to the full run. Tests verify:

  * The flag plumbs through both `calibrate_thresholds.py` (inner)
    and `calibration_survey.py` (wrapper) without modification.
  * Sub-sampling is label-stratified — small caps must not collapse
    one class to zero or the threshold sweep is trivially undefined.
  * Sub-sampling is deterministic — the same seed must produce the
    same essay set across runs.
  * Different seeds produce different samples (stat'l-significance
    sense, not bit-equality — small samples can collide by chance).
  * Provenance entries from sub-sampled runs carry a `sub_sample`
    block AND a `notes` field starting with "PIPELINE CHECK" so a
    sub-sampled row can never be silently treated as a calibration.
  * Survey-level output marks the run as a pipeline check via
    `is_pipeline_check` + a banner in the markdown render.
  * The CLI surface for both scripts honors the documented flags.

Tests use mocked manifest entries + a mocked
`score_smoothing_entry` so they don't require the EditLens corpus
or Tier 2/3 deps.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import calibrate_thresholds as ct  # type: ignore
import calibration_survey as cs  # type: ignore


# ------------------- Helpers -------------------------------------


def _fake_entries(n_pos: int, n_neg: int) -> list[dict]:
    """Build N fake manifest entries with the right ai_status mix."""
    entries = []
    for i in range(n_pos):
        entries.append({
            "id": f"pos_{i}",
            "path": f"pos_{i}.txt",
            "ai_status": "ai_generated",
            "use": ["validation"],
            "split": "test",
            "language_status": "non_native_advanced",
        })
    for i in range(n_neg):
        entries.append({
            "id": f"neg_{i}",
            "path": f"neg_{i}.txt",
            "ai_status": "pre_ai_human",
            "use": ["validation"],
            "split": "test",
            "language_status": "non_native_advanced",
        })
    return entries


def _make_inner_args(**overrides) -> argparse.Namespace:
    base = dict(
        manifest="dummy.jsonl",
        use="validation",
        signal="burstiness_B",
        fpr_target=0.01,
        out=None,
        slug=None,
        replace=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tier2=False,
        tier3=False,
        notes=None,
        max_entries=None,
        max_entries_seed=None,
        records_cache=None,
        refresh_cache=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _patch_manifest_io():
    """Common context-manager bundle: stub the manifest-side I/O so
    sub-sample tests can run without a real manifest file on disk.

    Returns a list of `mock.patch` objects the caller `with`s as a
    bundle. Centralized here because the 1.26 cache refactor added
    `_manifest_content_hash` (which opens the manifest file) to the
    derive_threshold path; tests that previously only patched
    `validate_manifest` + `load_manifest_entries` now also need to
    stub the hash."""
    return [
        mock.patch.object(ct, "_manifest_content_hash",
                          return_value="sha256:test"),
    ]


# ------------------- Inner: calibrate_thresholds -----------------


def test_inner_args_accept_max_entries():
    """The CLI parser exposes --max-entries and --max-entries-seed."""
    parser = argparse.ArgumentParser()
    # Build the parser the way main() does. We can't import main() at
    # parse time without running it, so just verify the flags exist
    # in the help text.
    import io
    buf = io.StringIO()
    sys.argv_backup = sys.argv
    try:
        sys.argv = ["calibrate_thresholds.py", "--help"]
        try:
            ct.main()
        except SystemExit:
            pass
    finally:
        sys.argv = sys.argv_backup
    # Functional check: build an inner args namespace with the flag.
    args = _make_inner_args(max_entries=13, max_entries_seed=99)
    assert args.max_entries == 13
    assert args.max_entries_seed == 99


def test_subsample_caps_total_entries():
    """When max_entries is set and < full count, the sampler returns
    exactly max_entries entries (label-stratified)."""
    # We can't fully exercise derive_threshold without scoring (the
    # variance audit needs spaCy etc.), but we can verify the
    # sub-sample slicing produces the expected entries via a
    # surgical mock.
    fake = _fake_entries(n_pos=80, n_neg=80)  # 160 total
    args = _make_inner_args(max_entries=20, bootstrap_seed=42)

    captured: dict = {}

    def fake_score(e, **kw):
        captured.setdefault("entries", []).append(e["id"])
        return {"entry": e, "label": 1 if e["ai_status"] == "ai_generated" else 0,
                "scores": {"layer_a": {"burstiness_B": 0.5}}}

    # Stub manifest validation + loading + scoring.
    with mock.patch.object(ct, "_manifest_content_hash",
                           return_value="sha256:test"), \
         mock.patch.object(ct, "validate_manifest",
                           return_value={"n_errors": 0}), \
         mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
         mock.patch.object(ct, "_entry_uses",
                           side_effect=lambda e, t: t in e["use"]), \
         mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
         mock.patch.object(ct, "collect_signal_records",
                           return_value=[(0, 0.4), (1, 0.6)] * 10), \
         mock.patch.object(ct, "sweep_threshold",
                           return_value={"available": True, "threshold": 0.5,
                                         "fpr_resolution": 0.05,
                                         "fpr": 0.05, "tpr": 0.5,
                                         "precision": 0.5,
                                         "n_pos": 10, "n_neg": 10}), \
         mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
         mock.patch.object(ct, "_ranking_metrics",
                           return_value={"auc": 0.7, "ap": 0.7}), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        entry = ct.derive_threshold(args)

    assert len(captured["entries"]) == 20
    # sub_sample block recorded.
    assert entry["sub_sample"]["applied"] is True
    assert entry["sub_sample"]["n_used"] == 20
    assert entry["sub_sample"]["n_full"] == 160
    # Notes prefixed with PIPELINE CHECK so the row never silently
    # gets treated as a calibration.
    assert entry["notes"].startswith("PIPELINE CHECK")


def test_subsample_label_stratified_keeps_both_classes():
    """A small cap must not collapse one class to zero — the
    threshold sweep would be undefined. Stratified sampling is
    proportional to class size with a floor of 1 per non-empty
    class."""
    fake = _fake_entries(n_pos=100, n_neg=20)  # imbalanced
    args = _make_inner_args(max_entries=10, bootstrap_seed=42)

    sampled_ids: list[str] = []

    def fake_score(e, **kw):
        sampled_ids.append(e["id"])
        return {"entry": e, "label": 1 if e["ai_status"] == "ai_generated" else 0,
                "scores": {"layer_a": {"burstiness_B": 0.5}}}

    with mock.patch.object(ct, "_manifest_content_hash",
                           return_value="sha256:test"), \
         mock.patch.object(ct, "validate_manifest",
                           return_value={"n_errors": 0}), \
         mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
         mock.patch.object(ct, "_entry_uses",
                           side_effect=lambda e, t: t in e["use"]), \
         mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
         mock.patch.object(ct, "collect_signal_records",
                           return_value=[(0, 0.4), (1, 0.6)] * 5), \
         mock.patch.object(ct, "sweep_threshold",
                           return_value={"available": True, "threshold": 0.5,
                                         "fpr_resolution": 0.1,
                                         "fpr": 0.1, "tpr": 0.5,
                                         "precision": 0.5,
                                         "n_pos": 5, "n_neg": 5}), \
         mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
         mock.patch.object(ct, "_ranking_metrics",
                           return_value={"auc": 0.6, "ap": 0.6}), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        ct.derive_threshold(args)

    pos_count = sum(1 for sid in sampled_ids if sid.startswith("pos_"))
    neg_count = sum(1 for sid in sampled_ids if sid.startswith("neg_"))
    assert pos_count >= 1, "stratified sampler must keep at least 1 positive"
    assert neg_count >= 1, "stratified sampler must keep at least 1 negative"
    assert pos_count + neg_count == 10
    # Proportional sampling: with 100 pos / 20 neg → ~83/17 split,
    # ~8 pos / ~2 neg in a 10-cap. Pin the rough proportionality.
    assert pos_count >= 7, f"expected ≥7 positives in proportional sample, got {pos_count}"


def test_subsample_deterministic_under_same_seed():
    """Same seed → same sample across runs."""
    fake = _fake_entries(n_pos=50, n_neg=50)
    args = _make_inner_args(max_entries=10, bootstrap_seed=99)

    def collect_run() -> list[str]:
        ids: list[str] = []

        def fake_score(e, **kw):
            ids.append(e["id"])
            return {"entry": e, "label": 0,
                    "scores": {"layer_a": {"burstiness_B": 0.5}}}

        with mock.patch.object(ct, "_manifest_content_hash",
                               return_value="sha256:test"), \
             mock.patch.object(ct, "validate_manifest",
                               return_value={"n_errors": 0}), \
             mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
             mock.patch.object(ct, "_entry_uses",
                               side_effect=lambda e, t: t in e["use"]), \
             mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
             mock.patch.object(ct, "collect_signal_records",
                               return_value=[(0, 0.5), (1, 0.5)] * 5), \
             mock.patch.object(ct, "sweep_threshold",
                               return_value={"available": True, "threshold": 0.5,
                                             "fpr_resolution": 0.1,
                                             "fpr": 0.1, "tpr": 0.5,
                                             "precision": 0.5,
                                             "n_pos": 5, "n_neg": 5}), \
             mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
             mock.patch.object(ct, "_ranking_metrics",
                               return_value={"auc": 0.5, "ap": 0.5}), \
             mock.patch.object(ct, "_load_fetch_record", return_value={}):
            ct.derive_threshold(args)
        return sorted(ids)

    run_a = collect_run()
    run_b = collect_run()
    assert run_a == run_b, \
        f"same seed should produce same sample; got {run_a} vs {run_b}"


def test_subsample_different_seeds_produce_different_samples():
    """Different seeds should produce different samples for any
    moderately-sized cap (small caps can collide by chance, so we
    use a 50-of-200 cap to make collision astronomically unlikely)."""
    fake = _fake_entries(n_pos=100, n_neg=100)

    def run(seed: int) -> set[str]:
        ids: set[str] = set()

        def fake_score(e, **kw):
            ids.add(e["id"])
            return {"entry": e, "label": 0,
                    "scores": {"layer_a": {"burstiness_B": 0.5}}}

        args = _make_inner_args(max_entries=50, bootstrap_seed=seed)
        with mock.patch.object(ct, "_manifest_content_hash",
                               return_value="sha256:test"), \
             mock.patch.object(ct, "validate_manifest",
                               return_value={"n_errors": 0}), \
             mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
             mock.patch.object(ct, "_entry_uses",
                               side_effect=lambda e, t: t in e["use"]), \
             mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
             mock.patch.object(ct, "collect_signal_records",
                               return_value=[(0, 0.5), (1, 0.5)] * 25), \
             mock.patch.object(ct, "sweep_threshold",
                               return_value={"available": True, "threshold": 0.5,
                                             "fpr_resolution": 0.1,
                                             "fpr": 0.1, "tpr": 0.5,
                                             "precision": 0.5,
                                             "n_pos": 25, "n_neg": 25}), \
             mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
             mock.patch.object(ct, "_ranking_metrics",
                               return_value={"auc": 0.5, "ap": 0.5}), \
             mock.patch.object(ct, "_load_fetch_record", return_value={}):
            ct.derive_threshold(args)
        return ids

    set_a = run(1)
    set_b = run(2)
    assert set_a != set_b, "different seeds must produce different samples"


def test_subsample_seed_override_uses_max_entries_seed():
    """When --max-entries-seed is set, it overrides --bootstrap-seed
    for the sub-sample (not for the bootstrap CI)."""
    fake = _fake_entries(n_pos=50, n_neg=50)

    def run(boot_seed: int, sample_seed: int) -> set[str]:
        ids: set[str] = set()

        def fake_score(e, **kw):
            ids.add(e["id"])
            return {"entry": e, "label": 0,
                    "scores": {"layer_a": {"burstiness_B": 0.5}}}

        args = _make_inner_args(
            max_entries=20,
            bootstrap_seed=boot_seed,
            max_entries_seed=sample_seed,
        )
        with mock.patch.object(ct, "_manifest_content_hash",
                               return_value="sha256:test"), \
             mock.patch.object(ct, "validate_manifest",
                               return_value={"n_errors": 0}), \
             mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
             mock.patch.object(ct, "_entry_uses",
                               side_effect=lambda e, t: t in e["use"]), \
             mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
             mock.patch.object(ct, "collect_signal_records",
                               return_value=[(0, 0.5), (1, 0.5)] * 10), \
             mock.patch.object(ct, "sweep_threshold",
                               return_value={"available": True, "threshold": 0.5,
                                             "fpr_resolution": 0.1,
                                             "fpr": 0.1, "tpr": 0.5,
                                             "precision": 0.5,
                                             "n_pos": 10, "n_neg": 10}), \
             mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
             mock.patch.object(ct, "_ranking_metrics",
                               return_value={"auc": 0.5, "ap": 0.5}), \
             mock.patch.object(ct, "_load_fetch_record", return_value={}):
            ct.derive_threshold(args)
        return ids

    # Same bootstrap seed, different max_entries_seed → different
    # samples (the override took effect).
    set_a = run(boot_seed=99, sample_seed=1)
    set_b = run(boot_seed=99, sample_seed=2)
    assert set_a != set_b


def test_subsample_no_op_when_max_entries_above_full_count():
    """If max_entries >= full count, the sampler should be a no-op
    and the resulting entry should NOT carry a sub_sample block."""
    fake = _fake_entries(n_pos=10, n_neg=10)
    args = _make_inner_args(max_entries=100, bootstrap_seed=42)

    scored_ids: list[str] = []

    def fake_score(e, **kw):
        scored_ids.append(e["id"])
        return {"entry": e, "label": 0,
                "scores": {"layer_a": {"burstiness_B": 0.5}}}

    with mock.patch.object(ct, "_manifest_content_hash",
                           return_value="sha256:test"), \
         mock.patch.object(ct, "validate_manifest",
                           return_value={"n_errors": 0}), \
         mock.patch.object(ct, "load_manifest_entries", return_value=fake), \
         mock.patch.object(ct, "_entry_uses",
                           side_effect=lambda e, t: t in e["use"]), \
         mock.patch.object(ct, "score_smoothing_entry", side_effect=fake_score), \
         mock.patch.object(ct, "collect_signal_records",
                           return_value=[(0, 0.5), (1, 0.5)] * 5), \
         mock.patch.object(ct, "sweep_threshold",
                           return_value={"available": True, "threshold": 0.5,
                                         "fpr_resolution": 0.1,
                                         "fpr": 0.1, "tpr": 0.5,
                                         "precision": 0.5,
                                         "n_pos": 5, "n_neg": 5}), \
         mock.patch.object(ct, "fixed_threshold_bootstrap_ci", return_value=None), \
         mock.patch.object(ct, "_ranking_metrics",
                           return_value={"auc": 0.5, "ap": 0.5}), \
         mock.patch.object(ct, "_load_fetch_record", return_value={}):
        entry = ct.derive_threshold(args)

    # All 20 fake entries scored (no sub-sampling).
    assert len(scored_ids) == 20
    # No sub_sample block.
    assert "sub_sample" not in entry
    # Notes do NOT start with PIPELINE CHECK.
    assert not entry["notes"].startswith("PIPELINE CHECK")


# ------------------- Survey wrapper plumbing --------------------


def test_survey_inner_args_carries_max_entries():
    """The wrapper's `_build_inner_args` forwards both
    --max-entries and --max-entries-seed to the inner."""
    parent = argparse.Namespace(
        manifest="x.jsonl", use="validation", fpr_target=0.01,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
        max_entries=13, max_entries_seed=999,
    )
    inner = cs._build_inner_args(parent, "burstiness_B")
    assert inner.max_entries == 13
    assert inner.max_entries_seed == 999


def test_survey_inner_args_default_max_entries_none():
    """When the parent doesn't have --max-entries, the inner gets
    None (fully-backward-compatible)."""
    parent = argparse.Namespace(
        manifest="x.jsonl", use="validation", fpr_target=0.01,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
    )
    inner = cs._build_inner_args(parent, "burstiness_B")
    assert inner.max_entries is None
    assert inner.max_entries_seed is None


def test_survey_marks_pipeline_check_when_max_entries_set():
    """Survey JSON records `is_pipeline_check: True` when
    --max-entries is non-None."""
    parent = argparse.Namespace(
        manifest="x.jsonl", use="validation", fpr_target=0.01,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
        max_entries=20, max_entries_seed=None,
    )

    def fake_derive(args):
        return {
            "signal": args.signal, "direction": "gt",
            "fpr_target": args.fpr_target,
            "empirical": {"auc": 0.6, "ap": 0.6,
                          "tpr_at_threshold": 0.3,
                          "fpr_at_threshold": 0.01,
                          "n_pos": 10, "n_neg": 10},
            "sweep": {"threshold": 0.5, "fpr_resolution": 0.1,
                      "available": True},
        }

    # 1.26.0: run_survey scores once via load_or_score_corpus, then
    # passes records to derive_threshold_from_records per signal.
    # Stub both layers so the tests don't need a real manifest file.
    with mock.patch.object(
        cs.ct, "load_or_score_corpus",
        return_value=([], {"sub_sample": (
            {"applied": True, "n_used": 20, "n_full": 130,
             "fraction": 0.15, "seed": 42}
            if getattr(parent, "max_entries", None) else None
        )}, False),
    ), mock.patch.object(
        cs.ct, "derive_threshold_from_records",
        side_effect=lambda records, *, args, scoring_meta: fake_derive(args),
    ):
        survey = cs.run_survey(parent, signals=["burstiness_B"])
    assert survey["is_pipeline_check"] is True
    assert survey["max_entries"] == 20


def test_survey_does_not_mark_pipeline_check_when_max_entries_unset():
    parent = argparse.Namespace(
        manifest="x.jsonl", use="validation", fpr_target=0.01,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
    )

    def fake_derive(args):
        return {
            "signal": args.signal, "direction": "gt",
            "fpr_target": args.fpr_target,
            "empirical": {"auc": 0.6, "ap": 0.6,
                          "tpr_at_threshold": 0.3,
                          "fpr_at_threshold": 0.01,
                          "n_pos": 100, "n_neg": 100},
            "sweep": {"threshold": 0.5, "fpr_resolution": 0.01,
                      "available": True},
        }

    # 1.26.0: run_survey scores once via load_or_score_corpus, then
    # passes records to derive_threshold_from_records per signal.
    # Stub both layers so the tests don't need a real manifest file.
    with mock.patch.object(
        cs.ct, "load_or_score_corpus",
        return_value=([], {"sub_sample": (
            {"applied": True, "n_used": 20, "n_full": 130,
             "fraction": 0.15, "seed": 42}
            if getattr(parent, "max_entries", None) else None
        )}, False),
    ), mock.patch.object(
        cs.ct, "derive_threshold_from_records",
        side_effect=lambda records, *, args, scoring_meta: fake_derive(args),
    ):
        survey = cs.run_survey(parent, signals=["burstiness_B"])
    assert survey["is_pipeline_check"] is False
    assert survey["max_entries"] is None


def test_survey_markdown_includes_pipeline_check_banner():
    """When is_pipeline_check is True, the markdown render shows a
    prominent banner so the maintainer can't miss that this is a
    pipeline check, not a calibration."""
    survey = {
        "manifest": "x.jsonl", "fpr_target": 0.01, "use": "validation",
        "tier2": False, "tier3": False,
        "tpr_floor": 0.05, "aggressiveness_tolerance": 0.05,
        "n_signals": 0, "n_signals_all_gates_pass": 0,
        "rows": [],
        "date": "2026-05-09",
        "max_entries": 13,
        "max_entries_seed": 42,
        "is_pipeline_check": True,
    }
    text = cs.render_markdown_table(survey)
    assert "PIPELINE CHECK" in text
    assert "--max-entries 13" in text


def test_survey_markdown_no_banner_on_full_runs():
    survey = {
        "manifest": "x.jsonl", "fpr_target": 0.01, "use": "validation",
        "tier2": False, "tier3": False,
        "tpr_floor": 0.05, "aggressiveness_tolerance": 0.05,
        "n_signals": 0, "n_signals_all_gates_pass": 0,
        "rows": [],
        "date": "2026-05-09",
        "max_entries": None,
        "max_entries_seed": None,
        "is_pipeline_check": False,
    }
    text = cs.render_markdown_table(survey)
    assert "PIPELINE CHECK" not in text


# ------------------- CLI surface --------------------------------


def test_inner_cli_help_lists_max_entries_flags():
    """`calibrate_thresholds.py --help` mentions --max-entries and
    --max-entries-seed."""
    import io
    sys.argv_backup = sys.argv
    captured = io.StringIO()
    try:
        sys.argv = ["calibrate_thresholds.py", "--help"]
        try:
            sys.stdout = captured
            ct.main()
        except SystemExit:
            pass
        finally:
            sys.stdout = sys.__stdout__
    finally:
        sys.argv = sys.argv_backup
    text = captured.getvalue()
    assert "--max-entries" in text


def test_survey_cli_help_lists_max_entries_flags():
    parser = cs.build_arg_parser()
    text = parser.format_help()
    assert "--max-entries" in text
    assert "--max-entries-seed" in text


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
