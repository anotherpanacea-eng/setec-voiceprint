#!/usr/bin/env python3
"""Regression tests for task_surfaces.py — the multi-task
dispatch registry that shard_runner uses to look up scorer and
aggregator implementations.

Pins the contract:

  * ``TASK_REGISTRY`` is a module-level dict keyed by task name.
  * ``register_task`` adds (or overwrites) an entry idempotently.
  * ``get_task`` raises KeyError on unknown names.
  * ``task_for_state`` defaults to ``calibration_survey`` when
    ``state["task"]`` is missing — the load-bearing backwards-
    compat path for pre-v1.45.0 state.json files.
  * ``calibration_survey`` is registered as a side-effect of
    importing the module; its TaskSurface has the right shape
    and points at the legacy DEFAULT_SCORER adapter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))

import task_surfaces as ts  # type: ignore


# --------------- Registry contract ------------------------------


def test_calibration_survey_is_registered_on_import():
    """Importing task_surfaces should be sufficient to make the
    calibration_survey surface available. Operators who don't
    pass --task should still see the default surface registered.
    """
    assert "calibration_survey" in ts.TASK_REGISTRY
    surface = ts.TASK_REGISTRY["calibration_survey"]
    assert surface.name == "calibration_survey"
    assert callable(surface.score_shard)
    assert callable(surface.aggregate_records)


def test_get_task_returns_registered_surface():
    surface = ts.get_task("calibration_survey")
    assert surface.name == "calibration_survey"


def test_get_task_raises_on_unknown_name():
    with pytest.raises(KeyError):
        ts.get_task("not_a_real_task")


def test_registered_task_names_is_sorted():
    names = ts.registered_task_names()
    assert names == sorted(names)
    assert "calibration_survey" in names


# --------------- Backwards-compat helper ------------------------


def test_task_for_state_defaults_to_calibration_survey():
    """A state.json from before v1.45.0 has no ``task`` field; the
    helper must transparently route to calibration_survey so the
    existing operator paths keep working."""
    state_without_task = {"run_id": "legacy", "shards": {}}
    surface = ts.task_for_state(state_without_task)
    assert surface.name == "calibration_survey"


def test_task_for_state_honors_explicit_task():
    state_with_task = {
        "run_id": "new", "task": "calibration_survey", "shards": {},
    }
    surface = ts.task_for_state(state_with_task)
    assert surface.name == "calibration_survey"


def test_task_for_state_raises_on_unknown_task():
    state = {"run_id": "broken", "task": "nope", "shards": {}}
    with pytest.raises(KeyError):
        ts.task_for_state(state)


# --------------- register_task idempotence ----------------------


def test_register_task_is_idempotent():
    """Re-registering the same name overwrites the previous entry
    cleanly. This is the path tests use to swap implementations
    without leaking across test cases."""

    def _stub_scorer(**kwargs):
        return {"records": [], "meta": {"who": "stub"}, "cache_hit": False}

    def _stub_aggregator(**kwargs):
        return {"who": "stub_aggregator"}

    original = ts.TASK_REGISTRY.get("calibration_survey")
    try:
        ts.register_task(ts.TaskSurface(
            name="calibration_survey",
            score_shard=_stub_scorer,
            aggregate_records=_stub_aggregator,
        ))
        surface = ts.get_task("calibration_survey")
        assert surface.score_shard is _stub_scorer
        assert surface.aggregate_records is _stub_aggregator
    finally:
        if original is not None:
            ts.register_task(original)


def test_register_task_adds_new_name():
    """A previously-unregistered name becomes lookupable after
    register_task. Cleanup leaves the registry as we found it."""

    def _scorer(**kwargs):
        return {"records": [], "meta": {}, "cache_hit": False}

    def _aggregator(**kwargs):
        return {"ok": True}

    name = "test_only_surface_xyz"
    assert name not in ts.TASK_REGISTRY
    ts.register_task(ts.TaskSurface(
        name=name, score_shard=_scorer, aggregate_records=_aggregator,
    ))
    try:
        assert ts.get_task(name).name == name
    finally:
        ts.TASK_REGISTRY.pop(name, None)


# --------------- TaskSurface dataclass shape --------------------


def test_task_surface_defaults_empty_collections():
    """A TaskSurface constructed without default_task_params /
    required_state_fields should get an empty dict / list rather
    than a shared mutable default."""

    def _f(**kwargs):
        return {"records": [], "meta": {}, "cache_hit": False}

    a = ts.TaskSurface(name="a", score_shard=_f, aggregate_records=_f)
    b = ts.TaskSurface(name="b", score_shard=_f, aggregate_records=_f)
    assert a.default_task_params == {}
    assert b.default_task_params == {}
    a.default_task_params["leaked"] = True
    assert "leaked" not in b.default_task_params
    assert a.required_state_fields == []
    assert b.required_state_fields == []


def test_calibration_survey_default_params_include_tier_flags():
    """The legacy calibration_survey surface stores tier1/tier2/
    tier3/fpr_target defaults in its default_task_params blob so
    the orchestrator can write them through to state.json's
    task_params on shard. Pin the shape so a future schema
    refactor catches the change."""
    surface = ts.get_task("calibration_survey")
    params = surface.default_task_params
    assert "fpr_target" in params
    assert "tier1" in params
    assert "tier2" in params
    assert "tier3" in params


# --------------- calibration_survey adapter -------------------


def test_calibration_survey_score_shard_calls_default_scorer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """The calibration_survey scorer adapter must delegate to
    ``shard_runner.DEFAULT_SCORER`` so the existing test hook
    (monkeypatch DEFAULT_SCORER to a stub) continues to work."""
    import shard_runner as sr  # type: ignore

    captured: dict = {}

    def _stub(shard_manifest_path, *, fpr_target, tier1, tier2, tier3,
              use, cache_path, flush_every, sigterm_event, **_extra):
        captured["fpr_target"] = fpr_target
        captured["tier1"] = tier1
        captured["use"] = use
        # 1.80.0+ kwargs are captured for inspection but the stub
        # doesn't act on them; the dedicated unit test for the new
        # passthrough is below.
        captured["tier4"] = _extra.get("tier4")
        captured["embedding_model"] = _extra.get("embedding_model")
        captured["surprisal_model"] = _extra.get("surprisal_model")
        return {"records": [{"text_id": "r1"}], "meta": {"x": 1},
                "cache_hit": False}

    monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub)
    surface = ts.get_task("calibration_survey")
    result = surface.score_shard(
        shard_manifest_path=tmp_path / "manifest.jsonl",
        cache_path=tmp_path / "cache.json",
        sigterm_event=None,
        flush_every=5000,
        task_params={
            "fpr_target": 0.05, "tier1": True, "tier2": False,
            "tier3": False,
        },
        run_context={"use": "validation"},
    )
    assert captured["fpr_target"] == 0.05
    assert captured["use"] == "validation"
    assert result["records"] == [{"text_id": "r1"}]
    assert result["cache_hit"] is False


def test_calibration_survey_aggregate_with_no_derive_skips_ct(tmp_path: Path):
    """Passing --no-derive (args.no_derive=True) bypasses the
    calibrate_thresholds import and produces an empty per_signal
    dict. This is the test that runs under CI even when
    calibrate_thresholds' deps (spaCy) aren't installed."""
    surface = ts.get_task("calibration_survey")
    args = argparse.Namespace(no_derive=True, out=None, use="validation")
    state = {
        "run_id": "test_run",
        "source_manifest_path": str(tmp_path / "src.jsonl"),
        "source_manifest_sha256": "deadbeef" * 8,
        "fpr_target": 0.01,
    }
    payload = surface.aggregate_records(
        all_records=[{"text_id": "r1", "label": "ai_generated"}],
        meta_list=[{"scorer_version": "stub"}],
        contributing_shards=["000", "001"],
        state=state,
        args=args,
    )
    assert payload["n_records"] == 1
    assert payload["n_shards_contributed"] == 2
    assert payload["per_signal"] == {}
    assert payload["run_id"] == "test_run"
    assert payload["source_manifest_sha256"] == "deadbeef" * 8


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
