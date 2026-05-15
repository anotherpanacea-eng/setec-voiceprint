#!/usr/bin/env python3
"""Regression tests for the sharded per-signal aggregator.

PR B in the "stylometry to the people" performance series. At MAGE
scale the per-signal threshold sweep was the dominant wall-clock
cost (~30-60 min per signal × 11 signals = ~6 hours serial). The
sweep is embarrassingly parallel — each call to
``survey_one_signal`` reads the same scored-records cache, writes
no shared state, and the pool initializer copies the records once
per worker process rather than re-serializing them per submitted
future. ``--aggregate-workers N`` is the user-visible knob.

These tests pin:

  * The CLI flag exists, defaults to 1 (serial), accepts ints.
  * ``_pool_init`` populates the module-level globals used by
    worker processes.
  * ``_survey_one_signal_pooled`` reads those globals and
    dispatches ``survey_one_signal`` with them.
  * ``_survey_one_signal_pooled`` raises ``RuntimeError`` if the
    pool wasn't initialized — silent ``None`` propagation would
    masquerade as a per-signal failure.
  * The dispatch in ``run_survey`` takes the serial path when
    workers <= 1 OR when only one signal is in scope (no
    ProcessPoolExecutor spawn overhead for the trivial case).
  * The dispatch takes the parallel path when workers >= 2 AND
    len(signals) >= 2.
  * Both paths produce functionally equivalent rows (same signals
    surveyed, same gate evaluations).
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

import calibration_survey as cs  # type: ignore  # noqa: E402


# ---------- Synthetic provenance entry helper ----------


def _entry(
    signal: str,
    *,
    direction: str = "gt",
    auc: float = 0.85,
    threshold: float = 0.42,
    tpr: float = 0.60,
    fpr: float = 0.009,
    n_pos: int = 100,
    n_neg: int = 200,
    fpr_resolution: float = 0.005,
) -> dict:
    return {
        "signal": signal,
        "direction": direction,
        "fpr_target": 0.01,
        "empirical": {
            "auc": auc,
            "ap": 0.80,
            "tpr_at_threshold": tpr,
            "fpr_at_threshold": fpr,
            "n_pos": n_pos,
            "n_neg": n_neg,
        },
        "sweep": {
            "threshold": threshold,
            "fpr_resolution": fpr_resolution,
            "available": True,
        },
    }


def _stub_args(**overrides) -> argparse.Namespace:
    base = dict(
        manifest="dummy.jsonl",
        use="validation",
        fpr_target=0.01,
        out=None,
        signal=[],
        tier2=False,
        tier3=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tpr_floor=0.05,
        aggressiveness_tolerance=0.05,
        json_only=False,
        aggregate_workers=1,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------- CLI surface ----------


def test_aggregate_workers_default_is_one():
    """Default is serial. Pre-1.61 ledger entries used the historical
    serial execution; ``--aggregate-workers 1`` reproduces them
    byte-for-byte on the deterministic-seed path. A default of >1
    would silently change the ledger shape for callers that don't
    pass the flag — opt-in is safer."""
    parser = cs.build_arg_parser()
    args = parser.parse_args(
        ["--manifest", "x.jsonl", "--fpr-target", "0.01"]
    )
    assert args.aggregate_workers == 1


def test_aggregate_workers_accepts_explicit_int():
    parser = cs.build_arg_parser()
    args = parser.parse_args([
        "--manifest", "x.jsonl",
        "--fpr-target", "0.01",
        "--aggregate-workers", "7",
    ])
    assert args.aggregate_workers == 7


# ---------- Pool helpers ----------


def test_pool_init_populates_module_globals():
    """``_pool_init`` is the ProcessPoolExecutor initializer: it
    must stash records / scoring_meta / parent_args / floors into
    module globals so subsequent task submissions don't ship the
    ~100MB records list per signal."""
    parent = _stub_args(aggregate_workers=4)
    records = [{"id": "doc1", "score": 0.5}, {"id": "doc2", "score": 0.7}]
    meta = {"scorer_version": "test"}

    # Reset before init to make the test independent of order.
    cs._POOL_RECORDS = None
    cs._POOL_SCORING_META = None
    cs._POOL_PARENT_ARGS = None

    cs._pool_init(records, meta, parent, 0.10, 0.07)

    assert cs._POOL_RECORDS is records
    assert cs._POOL_SCORING_META is meta
    assert cs._POOL_PARENT_ARGS is parent
    assert cs._POOL_TPR_FLOOR == 0.10
    assert cs._POOL_AGGRESSIVENESS_TOLERANCE == 0.07


def test_pooled_signal_dispatches_to_survey_one_signal():
    """``_survey_one_signal_pooled`` is the pool task. After
    ``_pool_init`` it should pull from globals and dispatch
    ``survey_one_signal`` with the same arguments the serial path
    would use."""
    parent = _stub_args(aggregate_workers=2)
    records = [{"id": "doc1"}]
    meta = {"scorer_version": "test"}
    cs._pool_init(records, meta, parent, 0.05, 0.05)

    fake_entry = _entry("burstiness_B", auc=0.85, threshold=0.42, tpr=0.60)
    with mock.patch.object(
        cs.ct, "derive_threshold_from_records",
        return_value=fake_entry,
    ) as derive:
        row = cs._survey_one_signal_pooled("burstiness_B")

    # Real call signature: positional records, kwarg args + scoring_meta.
    assert derive.called
    call_args, call_kwargs = derive.call_args
    assert call_args[0] is records
    assert call_kwargs["scoring_meta"] is meta
    # And the row carries the per-signal metrics.
    assert row.signal == "burstiness_B"
    assert row.error is None


def test_pooled_signal_raises_without_pool_init():
    """Defensive: a worker that somehow skipped ``_pool_init``
    should fail loudly, not silently propagate ``None``. Silent
    ``None`` would let a corrupted worker pretend it produced a
    SurveyRow and skew the survey ledger."""
    # Reset globals to simulate an un-initialized worker.
    cs._POOL_RECORDS = None
    cs._POOL_SCORING_META = None
    cs._POOL_PARENT_ARGS = None

    try:
        cs._survey_one_signal_pooled("burstiness_B")
    except RuntimeError as exc:
        assert "_pool_init" in str(exc)
    else:
        raise AssertionError(
            "_survey_one_signal_pooled should raise without _pool_init"
        )


# ---------- Dispatch in run_survey ----------


def test_run_survey_serial_when_workers_one():
    """workers <= 1 must NOT spawn a ProcessPoolExecutor; the
    serial branch is the documented byte-identical path."""
    parent = _stub_args(aggregate_workers=1)
    fake_entry = _entry("dummy")

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry), \
         mock.patch("concurrent.futures.ProcessPoolExecutor") as ppe:
        survey = cs.run_survey(
            parent, signals=["burstiness_B", "mattr", "yules_k"],
        )

    assert ppe.called is False, (
        "ProcessPoolExecutor must not be instantiated for workers=1"
    )
    assert survey["n_signals"] == 3


def test_run_survey_serial_when_only_one_signal():
    """Pool spawn overhead isn't worth it for a single-signal
    survey. The dispatcher should fall back to serial even if
    --aggregate-workers > 1."""
    parent = _stub_args(aggregate_workers=4)
    fake_entry = _entry("burstiness_B")

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry), \
         mock.patch("concurrent.futures.ProcessPoolExecutor") as ppe:
        survey = cs.run_survey(parent, signals=["burstiness_B"])

    assert ppe.called is False, (
        "Single-signal survey must skip the pool"
    )
    assert survey["n_signals"] == 1


def test_run_survey_treats_zero_workers_as_one():
    """``--aggregate-workers 0`` is explicit-zero, not auto-detect.
    Treat as 1 (serial) rather than crash with 'max_workers must
    be >= 1' from the pool, and rather than silently pick an
    arbitrary core count the user didn't request."""
    parent = _stub_args(aggregate_workers=0)
    fake_entry = _entry("dummy")

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry), \
         mock.patch("concurrent.futures.ProcessPoolExecutor") as ppe:
        survey = cs.run_survey(
            parent, signals=["burstiness_B", "mattr"],
        )

    assert ppe.called is False
    assert survey["n_signals"] == 2


def test_run_survey_parallel_when_workers_and_signals_both_plural():
    """workers >= 2 AND len(signals) >= 2 takes the parallel path —
    a ProcessPoolExecutor is instantiated, ``initializer`` is
    ``_pool_init``, and the tasks dispatched are
    ``_survey_one_signal_pooled``.

    We mock the executor so this runs synchronously in-process
    rather than spawning real workers; that keeps the test fast
    and avoids the spawn-vs-fork start-method portability issues
    on Windows + macOS.
    """
    parent = _stub_args(aggregate_workers=3)

    signals = ["burstiness_B", "mattr", "yules_k"]
    fake_rows = {
        s: cs.SurveyRow(signal=s, direction="gt", heuristic_value=0.5)
        for s in signals
    }

    class _FakeFuture:
        def __init__(self, result):
            self._r = result

        def result(self):
            return self._r

    class _FakePool:
        instances: list["_FakePool"] = []

        def __init__(
            self, max_workers=None, initializer=None, initargs=(),
        ):
            self.max_workers = max_workers
            self.initializer = initializer
            self.initargs = initargs
            _FakePool.instances.append(self)
            self.submitted_tasks: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, fn, signal):
            self.submitted_tasks.append(signal)
            assert fn is cs._survey_one_signal_pooled
            return _FakeFuture(fake_rows[signal])

    def _fake_as_completed(d):
        # Iterate in original submission order; the dispatch
        # logic only requires "some completion order" — not the
        # original order — so the test still passes.
        return list(d.keys())

    _FakePool.instances.clear()
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([{"id": "doc1"}], {"v": "x"}, False)), \
         mock.patch("concurrent.futures.ProcessPoolExecutor",
                    _FakePool), \
         mock.patch("concurrent.futures.as_completed",
                    _fake_as_completed):
        survey = cs.run_survey(parent, signals=signals)

    # The pool was constructed exactly once with the right initializer
    # and worker count.
    assert len(_FakePool.instances) == 1
    pool = _FakePool.instances[0]
    assert pool.max_workers == 3
    assert pool.initializer is cs._pool_init
    # initargs shape: (records, scoring_meta, parent_args, tpr_floor, agg_tol)
    assert len(pool.initargs) == 5
    assert pool.initargs[0] == [{"id": "doc1"}]
    assert pool.initargs[2] is parent

    # Every signal was submitted to the pool.
    assert set(pool.submitted_tasks) == set(signals)

    # And the survey aggregated the rows produced by the pool.
    surveyed = {r["signal"] for r in survey["rows"]}
    assert surveyed == set(signals)


def test_serial_and_parallel_paths_produce_equivalent_signal_set():
    """Different code paths, same observable output: the set of
    signals surveyed and their per-signal metrics should match
    when both paths see the same stub derive_threshold."""
    fake_entry_for = lambda s: _entry(s, auc=0.85)

    signals = ["burstiness_B", "mattr", "yules_k"]

    # --- Serial path ---
    parent_serial = _stub_args(aggregate_workers=1)
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(
             cs.ct, "derive_threshold_from_records",
             side_effect=lambda records, *, args, scoring_meta:
                 fake_entry_for(args.signal),
         ):
        survey_serial = cs.run_survey(parent_serial, signals=signals)

    # --- Parallel path via FakePool ---
    parent_parallel = _stub_args(aggregate_workers=3)

    class _FakeFuture:
        def __init__(self, result):
            self._r = result

        def result(self):
            return self._r

    class _FakePool:
        def __init__(self, *a, **kw):
            self.initializer = kw["initializer"]
            self.initargs = kw["initargs"]

        def __enter__(self):
            # Synthesize what _pool_init would do inside a real
            # worker process, but in-process so the same
            # ``derive_threshold_from_records`` mock applies.
            self.initializer(*self.initargs)
            return self

        def __exit__(self, *exc):
            return False

        def submit(self, fn, signal):
            return _FakeFuture(fn(signal))

    def _fake_as_completed(d):
        return list(d.keys())

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(
             cs.ct, "derive_threshold_from_records",
             side_effect=lambda records, *, args, scoring_meta:
                 fake_entry_for(args.signal),
         ), \
         mock.patch("concurrent.futures.ProcessPoolExecutor",
                    _FakePool), \
         mock.patch("concurrent.futures.as_completed",
                    _fake_as_completed):
        survey_parallel = cs.run_survey(parent_parallel, signals=signals)

    serial_signals = {r["signal"] for r in survey_serial["rows"]}
    parallel_signals = {r["signal"] for r in survey_parallel["rows"]}
    assert serial_signals == parallel_signals
    assert survey_serial["n_signals"] == survey_parallel["n_signals"]

    # Per-signal AUCs match (within the trivial fixture).
    serial_aucs = {r["signal"]: r["auc"] for r in survey_serial["rows"]}
    parallel_aucs = {r["signal"]: r["auc"] for r in survey_parallel["rows"]}
    assert serial_aucs == parallel_aucs


def test_pool_init_is_module_level_and_picklable():
    """ProcessPoolExecutor with the ``spawn`` start method (the
    default on Windows + macOS-3.8+) re-imports the module in
    each worker and requires the initializer + task callables to
    be importable by qualified name. Closures and lambdas can't
    survive that boundary. Pin the invariant by verifying both
    callables resolve to ``calibration_survey`` module-level
    functions."""
    assert cs._pool_init.__module__ == "calibration_survey"
    assert cs._survey_one_signal_pooled.__module__ == "calibration_survey"
    # And neither is a bound method or partial — they're free functions.
    assert cs._pool_init.__qualname__ == "_pool_init"
    assert cs._survey_one_signal_pooled.__qualname__ == (
        "_survey_one_signal_pooled"
    )
