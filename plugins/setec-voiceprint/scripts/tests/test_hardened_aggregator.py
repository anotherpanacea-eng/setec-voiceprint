#!/usr/bin/env python3
"""Tests for the hardened parallel aggregator (PR
feat/hardened-parallel-aggregator).

Closes the wiring gap PRs #53 / #55 / #60 left open: the calibration
task surface (``task_surfaces._aggregate_calibration_records``) now
reads ``bootstrap_engine`` / ``bootstrap_chunk_size`` /
``bootstrap_device`` / ``aggregate_workers`` / ``executor`` /
``max_worker_rss_gb`` off the args namespace, so the new flags on
``shard_runner aggregate`` actually engage the fast path. These tests
pin:

  * The new CLI flags exist on shard_runner aggregate, parse to the
    expected types, and propagate into the per-signal Namespace.
  * Layer 1 (Belt): per-signal pairs are pre-extracted in main and
    dispatched to workers via ``pre_extracted_pairs``; the legacy
    records-list path is preserved as a per-signal fallback.
  * Layer 2 (Suspenders): ``--executor thread`` runs the
    ThreadPoolExecutor path end-to-end.
  * Layer 3 (Buttons): ``--executor process`` uses
    ``multiprocessing.shared_memory`` for the pair arrays; tasks
    pickle only the SharedMemory names + a small Namespace dict.
  * Layer 4 (Zip): ``_cap_workers`` honors free-RAM via psutil and
    the user-imposed ``--max-worker-rss-gb`` budget.
  * The ``aggregator_perf`` block in the survey JSON records what
    actually ran (engine, requested vs capped workers, executor).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import shard_runner as sr  # type: ignore  # noqa: E402
import task_surfaces as ts  # type: ignore  # noqa: E402
import calibrate_thresholds as ct  # type: ignore  # noqa: E402


# --------------- Stub scorer (carries per_signal_scores) ----------


def _stub_scorer_with_signals(
    shard_manifest_path: Path,
    *,
    fpr_target: float,
    tier1: bool,
    tier2: bool,
    tier3: bool,
    use: str,
    cache_path: Path,
    flush_every: int,
    sigterm_event,
    # 1.80.0+ kwargs propagated by _score_shard_calibration_survey.
    # The stub doesn't honor them (Tier 4 + embedding-model behavior
    # is tested in unit tests against the real scorer); we accept the
    # kwargs to keep the stub-injection test path compatible with the
    # post-1.80 dispatcher signature.
    **_extra,
):
    """Stub scorer that emits ``per_signal_scores`` for the signals
    in ``COMPRESSION_HEURISTICS`` so ``collect_signal_records`` can
    pre-extract pairs. Deterministic per text_id."""
    records = []
    with Path(shard_manifest_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            tid = row["text_id"]
            seed = abs(hash(tid)) % 10_000
            label = 1 if row.get("ai_status") == "ai_generated" else 0
            per_signal: dict[str, float] = {}
            for sig_name, spec in ct.COMPRESSION_HEURISTICS.items():
                # Deterministic per-signal score in a plausible range.
                base = (seed + abs(hash(sig_name)) % 1000) % 1000
                # Bias the AI class by signal direction so per-signal
                # AUCs sit above the chance line and the polarity-
                # inversion gate doesn't fire on the synthetic corpus.
                bias = 200 if (
                    label == 1 and getattr(spec, "direction", "gt") == "gt"
                ) else (-200 if label == 1 else 0)
                per_signal[spec.signal_path] = (base + bias) / 1000.0
            records.append({
                "text_id": tid,
                "label": label,
                "ai_status": row.get("ai_status"),
                "register": row.get("register"),
                "per_signal_scores": per_signal,
                "usable_for_metrics": True,
            })
    meta = {
        "scorer_version": "hardened-aggregator-test-stub-1.0",
        "tier1": tier1, "tier2": tier2, "tier3": tier3,
        "fpr_target": fpr_target, "use": use,
    }
    cp = Path(cache_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with cp.open("w", encoding="utf-8") as fh:
        json.dump(
            {"records": records, "meta": meta, "scoring_meta": meta},
            fh, sort_keys=True,
        )
    return {"records": records, "meta": meta, "cache_hit": False}


def _write_synth_manifest(path: Path, n_rows: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_rows):
            fh.write(json.dumps({
                "text_id": f"r{i:04d}",
                "text": f"synthetic prose {i}. " * 5,
                "register": ["literary_fiction", "blog_essay"][i % 2],
                "ai_status": (
                    "ai_generated" if i % 2 == 0 else "pre_ai_human"
                ),
                "use": "validation",
                "privacy": "shareable",
            }) + "\n")


@pytest.fixture
def hardened_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a sharded run with a stub scorer that emits real
    ``per_signal_scores`` so the pre-extraction fast path engages."""
    monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer_with_signals)
    base = tmp_path / "baselines"
    base.mkdir()
    src = base / "synth" / "manifest.jsonl"
    _write_synth_manifest(src, n_rows=80)
    run_id = "hardened_test_run"
    rc = sr.main([
        "--base-dir", str(base),
        "shard",
        "--source-manifest", str(src),
        "--run-id", run_id,
        "--shard-size", "20",
        "--shuffle-seed", "42",
        "--fpr-target", "0.01",
        "--no-tier2", "--no-tier3",
    ])
    assert rc == 0
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    return {"base": base, "src": src, "run_id": run_id}


# --------------- CLI surface ----------


def test_aggregate_parser_accepts_new_flags():
    """All four new flags exist on shard_runner aggregate, parse to
    the expected types, and have sensible defaults."""
    parser = sr.build_arg_parser()
    args = parser.parse_args([
        "--base-dir", "/tmp", "aggregate",
        "--run-id", "x",
    ])
    assert args.bootstrap_engine == "loop"  # default preserves pre-PR
    assert args.bootstrap_chunk_size is None
    assert args.bootstrap_device is None
    assert args.aggregate_workers == 1  # serial default
    assert args.executor == "thread"
    assert args.max_worker_rss_gb is None


def test_aggregate_parser_explicit_values():
    parser = sr.build_arg_parser()
    args = parser.parse_args([
        "--base-dir", "/tmp", "aggregate",
        "--run-id", "x",
        "--bootstrap-engine", "numpy",
        "--bootstrap-chunk-size", "32",
        "--aggregate-workers", "4",
        "--executor", "process",
        "--max-worker-rss-gb", "8.5",
    ])
    assert args.bootstrap_engine == "numpy"
    assert args.bootstrap_chunk_size == 32
    assert args.aggregate_workers == 4
    assert args.executor == "process"
    assert args.max_worker_rss_gb == 8.5


def test_aggregate_parser_rejects_unknown_engine():
    parser = sr.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--base-dir", "/tmp", "aggregate",
            "--run-id", "x",
            "--bootstrap-engine", "rocket-fuel",
        ])


def test_aggregate_parser_rejects_unknown_executor():
    parser = sr.build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--base-dir", "/tmp", "aggregate",
            "--run-id", "x",
            "--executor", "fibers",
        ])


# --------------- Layer 1 (Belt): pre-extraction propagates engine ----------


def test_engine_propagates_into_per_signal_namespace(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The wiring gap fix: ``--bootstrap-engine numpy`` on
    ``shard_runner aggregate`` must reach the per-signal Namespace
    that the calibration task surface builds. Without this, the
    sharded path silently falls back to the loop engine even when
    the user explicitly requested numpy."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    captured: list[argparse.Namespace] = []
    real_derive = ct.derive_threshold_from_records

    def _capturing(records, *, args, scoring_meta, **kwargs):
        captured.append(args)
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _capturing)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg.json"),
        "--bootstrap-engine", "numpy",
        "--bootstrap-chunk-size", "16",
    ])
    assert rc == 0
    assert captured, "expected derive_threshold_from_records to be called"
    for ns in captured:
        assert ns.bootstrap_engine == "numpy", (
            f"bootstrap_engine not propagated: got {ns.bootstrap_engine!r}"
        )
        assert ns.bootstrap_chunk_size == 16, (
            f"bootstrap_chunk_size not propagated: got "
            f"{ns.bootstrap_chunk_size!r}"
        )


def test_pair_extraction_fast_path_dispatches_pairs(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When ``validation_harness.collect_signal_records`` is
    available and the records carry the expected per_signal_scores
    structure, the dispatcher should pass ``pre_extracted_pairs``
    instead of the full records list."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    saw_pre_extracted: list[bool] = []
    real_derive = ct.derive_threshold_from_records

    def _capture(records, *, args, scoring_meta, **kwargs):
        saw_pre_extracted.append(
            kwargs.get("pre_extracted_pairs") is not None
        )
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _capture)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg.json"),
    ])
    assert rc == 0
    assert saw_pre_extracted, "expected at least one derive call"
    # At least one signal must have used the fast path. (Some signals
    # may legitimately fall back to the legacy path if their spec
    # rejected pair extraction; we just need the fast path to engage
    # for the canonical signals like burstiness_B / mattr that the
    # stub scorer emits.)
    assert any(saw_pre_extracted), (
        "expected fast path (pre_extracted_pairs) to engage for at "
        "least one signal; got all-legacy dispatch instead"
    )


def test_pair_extraction_failure_falls_back_to_legacy(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """If ``collect_signal_records`` raises for a signal (bad spec,
    missing path, etc.), the dispatcher must fall back to the legacy
    records-list path for that signal — never silently drop it from
    the per-signal output."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]

    # Force every collect_signal_records call to raise. The dispatcher
    # should still call derive_threshold_from_records for every signal.
    monkeypatch.setattr(
        "validation_harness.collect_signal_records",
        mock.Mock(side_effect=RuntimeError("simulated extraction failure")),
    )
    captured_calls: list[bool] = []
    real_derive = ct.derive_threshold_from_records

    def _capture(records, *, args, scoring_meta, **kwargs):
        captured_calls.append(
            kwargs.get("pre_extracted_pairs") is not None
        )
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _capture)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg.json"),
    ])
    assert rc == 0
    assert captured_calls, "expected at least one derive call"
    assert all(c is False for c in captured_calls), (
        "all calls should have used legacy path (pre_extracted_pairs "
        "is None) when collect_signal_records raised"
    )


# --------------- Layer 2 (Suspenders): thread executor end-to-end ----------


def test_thread_executor_runs_multiple_signals(
    hardened_run, tmp_path: Path,
):
    """ThreadPoolExecutor with N workers should produce per_signal
    output for every signal in the registry (modulo per-signal
    polarity / no-pairs failures, captured as errors). Smoke-test
    that the dispatcher path doesn't deadlock or drop signals."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
        "--bootstrap-engine", "numpy",
        "--bootstrap-chunk-size", "16",
        "--aggregate-workers", "3",
        "--executor", "thread",
        "--max-worker-rss-gb", "8",
    ])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["aggregator_perf"]["executor"] == "thread"
    assert payload["aggregator_perf"]["bootstrap_engine"] == "numpy"
    # Every signal in the registry should have an entry (success or
    # error dict — never silently missing).
    expected = set(ct.COMPRESSION_HEURISTICS.keys())
    actual = set(payload["per_signal"].keys())
    assert expected == actual, (
        f"missing signals in per_signal: {expected - actual}"
    )


def test_process_executor_routes_failed_pairs_to_legacy_fallback(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Codex P2 on PR #63: when ``--executor process`` is on and
    pre-extraction fails for some signals, those signals must
    run via the parent's legacy records-list path (NOT silently
    fail as "no usable pairs" inside a SharedMemory worker). The
    thread and serial paths already do this fallback; process
    must match so ``--executor`` stays a perf knob, not a
    feature-availability knob.

    Force pair extraction to raise for SOME signals (the cohort
    split should then route them through the legacy path), and
    confirm the legacy-fallback cohort is recorded in perf and
    the signals are NOT silently dropped from per_signal.
    """
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    import validation_harness as vh  # noqa: PLC0415
    real_collect = vh.collect_signal_records
    fail_signal_paths = {
        "tier1.sentence_length.burstiness_B",
        "tier1.mattr.value",
        "tier1.mtld",
    }

    def _selective_collect(records, signal_path):
        if signal_path in fail_signal_paths:
            raise RuntimeError(
                f"simulated extraction failure for {signal_path}"
            )
        return real_collect(records, signal_path)

    monkeypatch.setattr(
        vh, "collect_signal_records", _selective_collect,
    )
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
        "--executor", "process",
        "--aggregate-workers", "2",
        "--bootstrap-engine", "loop",
    ])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    expected = set(ct.COMPRESSION_HEURISTICS.keys())
    actual = set(payload["per_signal"].keys())
    assert expected == actual, (
        f"signals missing from per_signal: {expected - actual}"
    )
    perf = payload["aggregator_perf"]
    assert "process_signals_legacy_fallback" in perf, (
        "perf metadata should record the legacy-fallback cohort"
    )
    assert perf["process_signals_legacy_fallback"] >= 1, (
        "with 3 signals forced to fail pair extraction, the "
        "legacy-fallback cohort should be non-empty"
    )


# --------------- Layer 3 (Buttons): SharedMemory allocation ----------


def test_shared_memory_pair_array_round_trips_pairs():
    """The SharedMemory allocator must round-trip the (label, score)
    pairs through int8 + float64 numpy arrays without lossy
    conversion. This is the contract the process-executor worker
    relies on when it attaches via ``SharedMemory(name=...)``."""
    pairs = [
        (1, 0.42), (0, 0.13), (1, -0.71), (0, 1.0e3), (1, 0.0),
    ]
    sm_l, sm_s, l_name, s_name, n = (
        ts._allocate_shared_pair_arrays(pairs)
    )
    try:
        assert n == len(pairs)
        from multiprocessing import shared_memory
        import numpy as np
        sm_l_view = shared_memory.SharedMemory(name=l_name)
        sm_s_view = shared_memory.SharedMemory(name=s_name)
        try:
            labels = np.ndarray((n,), dtype=np.int8, buffer=sm_l_view.buf)
            scores = np.ndarray(
                (n,), dtype=np.float64, buffer=sm_s_view.buf,
            )
            recovered = list(zip(labels.tolist(), scores.tolist()))
            assert recovered == pairs
        finally:
            sm_l_view.close()
            sm_s_view.close()
    finally:
        sm_l.close()
        sm_l.unlink()
        sm_s.close()
        sm_s.unlink()


def test_shared_memory_handles_empty_pair_list():
    """Empty pair lists must still allocate (so the worker's attach-
    by-name doesn't fail). The worker's n=0 short-circuit takes care
    of the rest."""
    sm_l, sm_s, l_name, s_name, n = (
        ts._allocate_shared_pair_arrays([])
    )
    try:
        assert n == 0
        # Names exist and refer to a real (though trivial) block.
        from multiprocessing import shared_memory
        v = shared_memory.SharedMemory(name=l_name)
        v.close()
    finally:
        sm_l.close()
        sm_l.unlink()
        sm_s.close()
        sm_s.unlink()


# --------------- Layer 4 (Zip): adaptive worker cap ----------


def test_cap_workers_honors_requested_when_psutil_says_yes():
    """With abundant free RAM, the cap leaves the requested count
    unchanged."""
    fake_vm = mock.Mock()
    fake_vm.available = 100 * 1024**3  # 100 GB free
    with mock.patch.dict(
        sys.modules, {"psutil": mock.Mock(virtual_memory=lambda: fake_vm)},
    ):
        capped, reason = ts._cap_workers(8, "thread", None)
    assert capped == 8
    # Reason may be empty or empty-ish; just confirm we kept the count.


def test_cap_workers_caps_when_psutil_says_no():
    """With tight free RAM, the cap drops below the requested
    count. At 1 GB free with 0.5 GB / thread-worker, the cap is
    floor(1.0 / 0.5) = 2 — even when the user asked for 8."""
    fake_vm = mock.Mock()
    fake_vm.available = 1 * 1024**3  # 1 GB free
    with mock.patch.dict(
        sys.modules, {"psutil": mock.Mock(virtual_memory=lambda: fake_vm)},
    ):
        capped, reason = ts._cap_workers(8, "thread", None)
    assert capped == 2
    assert "system" in reason
    assert "cap=2" in reason


def test_cap_workers_honors_max_rss_gb_budget():
    """The user-imposed RSS budget caps the worker count
    independently of psutil. With 100 GB free but max_rss_gb=2 and
    0.5 GB/thread, the cap is floor(2.0 / 0.5) = 4."""
    fake_vm = mock.Mock()
    fake_vm.available = 100 * 1024**3
    with mock.patch.dict(
        sys.modules, {"psutil": mock.Mock(virtual_memory=lambda: fake_vm)},
    ):
        capped, reason = ts._cap_workers(8, "thread", 2.0)
    assert capped == 4
    assert "budget" in reason


def test_cap_workers_floor_at_one():
    """Even with 0 GB free, the cap must not drop below 1 — a
    serial run is always possible."""
    fake_vm = mock.Mock()
    fake_vm.available = 0
    with mock.patch.dict(
        sys.modules, {"psutil": mock.Mock(virtual_memory=lambda: fake_vm)},
    ):
        capped, _ = ts._cap_workers(8, "thread", None)
    assert capped == 1


def test_cap_workers_falls_back_when_psutil_missing():
    """Without psutil, the cap honors the requested count and
    surfaces a one-line warning so the operator knows the cap is
    inactive."""
    # Force ImportError by removing psutil from sys.modules and
    # installing a fake meta_path finder that raises.
    saved = sys.modules.pop("psutil", None)
    import builtins as _b
    real_import = _b.__import__

    def _no_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("simulated missing psutil")
        return real_import(name, *args, **kwargs)

    try:
        with mock.patch.object(_b, "__import__", _no_psutil):
            capped, reason = ts._cap_workers(8, "thread", None)
        assert capped == 8
        assert "psutil not installed" in reason
    finally:
        if saved is not None:
            sys.modules["psutil"] = saved


# --------------- aggregator_perf block in survey JSON ----------


def test_aggregator_perf_block_records_what_actually_ran(
    hardened_run, tmp_path: Path,
):
    """The ``aggregator_perf`` block in the survey JSON should
    record the engine, executor, requested vs capped worker count,
    and timing breakdown so operators can audit a run after the
    fact."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
        "--bootstrap-engine", "numpy",
        "--bootstrap-chunk-size", "16",
        "--aggregate-workers", "2",
        "--executor", "thread",
    ])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    perf = payload.get("aggregator_perf")
    assert perf is not None
    assert perf["bootstrap_engine"] == "numpy"
    assert perf["executor"] == "thread"
    assert perf["requested_workers"] == 2
    assert isinstance(perf["capped_workers"], int)
    assert perf["capped_workers"] >= 1
    assert "sweep_s" in perf
    assert "n_signals_dispatched" in perf
    # Pair extraction stats: at least one signal should hit fast path.
    assert perf.get("pair_extraction_signals_fast_path", 0) >= 1
