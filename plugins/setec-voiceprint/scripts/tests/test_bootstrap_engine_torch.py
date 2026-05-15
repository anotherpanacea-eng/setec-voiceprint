#!/usr/bin/env python3
"""Regression tests for the optional torch+ROCm bootstrap backend.

PR C in the "stylometry to the people" performance series. The
``numpy`` engine (PR A) gets CPU SIMD lanes; ``torch`` (this PR)
gets GPU lanes on top, with auto-detect for CUDA + ROCm builds of
PyTorch. Torch is an optional dependency; these tests skip
cleanly when torch isn't installed.

Skip semantics:

  * Torch missing: skip — the engine raises a clear RuntimeError
    at the dispatcher; that error path is covered by
    ``test_torch_engine_raises_when_torch_unavailable`` which
    forces ``_torch_available`` to False via mock.
  * Torch installed, GPU absent: run on CPU. The torch engine's
    auto-detect picks ``cpu`` and the inner work runs in torch
    tensor ops. This is the path most CI runners take.
  * Torch installed, GPU present: same code, but the inner work
    runs on-device. The performance test below is a smoke check
    that the GPU is being used when reachable.

These tests pin:

  * The torch engine is dispatched from the public API by
    ``engine="torch"``.
  * ``--bootstrap-engine torch`` is a CLI choice on both
    calibrate_thresholds and calibration_survey.
  * ``--bootstrap-device`` overrides auto-detect.
  * Statistical equivalence: torch and numpy engines produce CIs
    within Monte Carlo noise of each other on well-separated
    classes at N=500 / R=2000.
  * Schema parity: the torch engine returns the same dict keys
    + adds a ``device`` field for ledger provenance.
  * Graceful failure when torch isn't importable.
  * Edge cases: empty input, single-class input.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibrate_thresholds as ct  # type: ignore  # noqa: E402


# ---------- Synthesize a paired-record corpus ----------


def _synthetic_pairs(
    n_pos: int = 250,
    n_neg: int = 250,
    *,
    pos_mean: float = 1.0,
    neg_mean: float = -1.0,
    seed: int = 42,
) -> list[tuple[int, float]]:
    """Two well-separated Gaussian classes. AUC should be high
    enough that the CI bounds are stable across bootstrap RNGs."""
    import random
    rng = random.Random(seed)
    pairs: list[tuple[int, float]] = []
    for _ in range(n_pos):
        pairs.append((1, rng.gauss(pos_mean, 1.0)))
    for _ in range(n_neg):
        pairs.append((0, rng.gauss(neg_mean, 1.0)))
    return pairs


# ---------- Torch-presence detection ----------


def _have_torch() -> bool:
    return ct._torch_available()


requires_torch = pytest.mark.skipif(
    not _have_torch(),
    reason="PyTorch not installed; skipping torch-engine tests.",
)


# ---------- Dispatcher behavior (no torch needed) ----------


def test_dispatcher_accepts_torch_engine_choice():
    """The public dispatcher's ``engine`` parameter should list
    'torch' as known. Probe by sending a tiny call and checking
    we get the right error code path (RuntimeError if torch
    isn't installed, valid dict if it is) — NOT the 'Unknown
    bootstrap engine' ValueError."""
    pairs = _synthetic_pairs(n_pos=20, n_neg=20)

    if _have_torch():
        result = ct.fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=10, confidence=0.95, seed=42,
            engine="torch",
        )
        assert result is not None
        assert result["engine"] == "torch"
    else:
        with pytest.raises(RuntimeError, match="torch"):
            ct.fixed_threshold_bootstrap_ci(
                pairs, threshold=0.0, direction="gt",
                resamples=10, confidence=0.95, seed=42,
                engine="torch",
            )


def test_dispatcher_rejects_unknown_engine():
    """An invalid engine name should still raise ValueError with
    a listing of known engines (now three, not two)."""
    pairs = _synthetic_pairs(n_pos=5, n_neg=5)
    with pytest.raises(ValueError) as exc:
        ct.fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=10, confidence=0.95, seed=42,
            engine="cuda9000",
        )
    msg = str(exc.value)
    assert "loop" in msg
    assert "numpy" in msg
    assert "torch" in msg


def test_torch_engine_raises_when_torch_unavailable():
    """Force the torch-missing branch by mocking _torch_available
    to False. The dispatcher should raise a RuntimeError that
    spells out the install path. Silent fallback to numpy would
    mask a misconfigured environment."""
    pairs = _synthetic_pairs(n_pos=5, n_neg=5)
    with mock.patch.object(ct, "_torch_available", return_value=False):
        with pytest.raises(RuntimeError) as exc:
            ct._fixed_threshold_bootstrap_ci_torch(
                pairs, threshold=0.0, direction="gt",
                resamples=10, confidence=0.95, seed=42,
            )
    msg = str(exc.value)
    assert "torch" in msg.lower()
    # Surface install guidance, not just 'failed'.
    assert "install" in msg.lower() or "pip" in msg.lower()


# ---------- CLI surface ----------


def test_calibrate_thresholds_cli_lists_torch_in_help():
    """``calibrate_thresholds.py``'s parser is built inline in
    ``main`` (no extracted ``build_arg_parser``), so probe via the
    --help surface: 'torch' should appear in the
    ``--bootstrap-engine`` choices, and ``--bootstrap-device`` should
    be a documented flag. The end-user CLI is the equivalent
    survey wrapper which has its own (covered) test below; this
    probe is the minimum surface check for the primary CLI."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), \
         contextlib.redirect_stderr(buf):
        try:
            ct.main(["--help"])
        except SystemExit:
            pass
    help_text = buf.getvalue()
    assert "--bootstrap-engine" in help_text
    assert "torch" in help_text
    assert "--bootstrap-device" in help_text


def test_calibration_survey_cli_accepts_torch_engine():
    """``--bootstrap-engine torch`` is exposed on the survey CLI
    too, since most users invoke through the survey wrapper."""
    import calibration_survey as cs  # type: ignore  # noqa: E402
    parser = cs.build_arg_parser()
    args = parser.parse_args([
        "--manifest", "x.jsonl",
        "--fpr-target", "0.01",
        "--bootstrap-engine", "torch",
        "--bootstrap-device", "cuda:0",
    ])
    assert args.bootstrap_engine == "torch"
    assert args.bootstrap_device == "cuda:0"


# ---------- Schema parity ----------


@requires_torch
def test_torch_engine_returns_canonical_dict_shape():
    """Same keys as numpy + loop, plus a ``device`` field for
    ledger provenance (records which hardware produced the CI)."""
    pairs = _synthetic_pairs(n_pos=50, n_neg=50)
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=100, confidence=0.95, seed=42,
        engine="torch",
    )
    assert result is not None
    expected_keys = {
        "method", "engine", "confidence", "resamples",
        "tpr_ci", "fpr_ci", "precision_ci", "note", "device",
    }
    assert set(result.keys()) >= expected_keys
    assert result["method"] == "fixed_threshold_paired_bootstrap"
    assert result["engine"] == "torch"
    # CIs are 2-element [low, high] lists.
    assert len(result["tpr_ci"]) == 2
    assert len(result["fpr_ci"]) == 2
    assert len(result["precision_ci"]) == 2
    # Device records the actual device used, not the requested one.
    assert isinstance(result["device"], str)


@requires_torch
def test_torch_engine_honors_explicit_cpu_device():
    """``device='cpu'`` forces the CPU torch path even if a GPU
    is reachable. Useful for cross-platform reproducibility."""
    pairs = _synthetic_pairs(n_pos=50, n_neg=50)
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=100, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",
    )
    assert result is not None
    assert "cpu" in result["device"]


# ---------- Statistical equivalence to numpy ----------


@requires_torch
def test_torch_and_numpy_agree_within_monte_carlo_noise():
    """Different RNG streams produce different per-resample
    compositions but the CI bounds should converge.

    Loose bound: 0.04 absolute on TPR/FPR/precision bounds at
    N=500 / R=2000 with well-separated classes. Sets the test
    cost at ~2 seconds while still catching algorithmic
    divergence (e.g. an off-by-one in the categorical encoding
    or a chunk-boundary bug).
    """
    pairs = _synthetic_pairs(n_pos=250, n_neg=250)

    numpy_result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=2000, confidence=0.95, seed=42,
        engine="numpy",
    )
    torch_result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=2000, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",  # equalize: numpy is CPU
    )
    assert numpy_result is not None
    assert torch_result is not None

    tol = 0.04
    for key in ("tpr_ci", "fpr_ci", "precision_ci"):
        n_lo, n_hi = numpy_result[key]
        t_lo, t_hi = torch_result[key]
        assert abs(n_lo - t_lo) < tol, (
            f"{key} low diverges: numpy={n_lo} torch={t_lo}"
        )
        assert abs(n_hi - t_hi) < tol, (
            f"{key} high diverges: numpy={n_hi} torch={t_hi}"
        )


# ---------- Direction handling ----------


@requires_torch
@pytest.mark.parametrize("direction", ["gt", "lt"])
def test_torch_engine_handles_direction(direction: str):
    """gt = positive when score > threshold; lt inverts. Test
    both directions produce non-degenerate CIs on the same
    synthetic corpus."""
    # For lt, swap class means so the lt-direction sweep finds
    # the same separation in the same direction the test is
    # measuring.
    pos_mean = 1.0 if direction == "gt" else -1.0
    neg_mean = -1.0 if direction == "gt" else 1.0
    pairs = _synthetic_pairs(
        n_pos=100, n_neg=100,
        pos_mean=pos_mean, neg_mean=neg_mean,
    )
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction=direction,
        resamples=200, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",
    )
    assert result is not None
    assert result["engine"] == "torch"
    # Non-degenerate: TPR CI low should be > 0.0 for these
    # well-separated classes.
    assert result["tpr_ci"][0] > 0.0


@requires_torch
def test_torch_engine_raises_on_invalid_direction():
    pairs = _synthetic_pairs(n_pos=10, n_neg=10)
    with pytest.raises(ValueError, match="direction"):
        ct._fixed_threshold_bootstrap_ci_torch(
            pairs, threshold=0.0, direction="sideways",
            resamples=10, confidence=0.95, seed=42,
        )


# ---------- Edge cases ----------


@requires_torch
def test_torch_engine_returns_none_for_empty_input():
    """Mirrors the numpy + loop engine contract: empty input →
    None (callers handle as 'no CI'), not an exception."""
    result = ct.fixed_threshold_bootstrap_ci(
        [], threshold=0.5, direction="gt",
        resamples=10, confidence=0.95, seed=42,
        engine="torch",
    )
    assert result is None


@requires_torch
def test_torch_engine_handles_single_class_resamples():
    """If every resample draws only one class, the per-chunk
    valid mask is all-False and the function returns None. This
    is the same skip-don't-substitute behavior as the loop +
    numpy engines."""
    # All positives — every resample is single-class.
    pairs = [(1, float(i)) for i in range(20)]
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=10.0, direction="gt",
        resamples=10, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",
    )
    assert result is None


# ---------- _torch_available probe ----------


def test_torch_available_returns_bool():
    """The helper should return True or False — never raise,
    never return None. Tests + the dispatcher both depend on
    its total-function-ness."""
    result = ct._torch_available()
    assert isinstance(result, bool)


# ---------- Codex P1 fixes: chunk_size + device in provenance ----


@requires_torch
def test_torch_engine_default_chunk_size_uses_auto_sizing():
    """Codex review (PR #56, P1): the original fixed
    chunk_size=200 default would have produced a ~13 GB int64
    index tensor at RAID scale. The torch engine now defaults to
    auto-sizing via ``_auto_chunk_size`` with the more
    conservative torch (int64) per-cell budget."""
    pairs = _synthetic_pairs(n_pos=50, n_neg=50)
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=100, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",
        # chunk_size omitted → auto.
    )
    assert result is not None
    # n=100 is small; auto-size hits the cap.
    assert result["chunk_size"] == ct._AUTO_CHUNK_MAX


@requires_torch
def test_torch_engine_honors_explicit_chunk_size():
    pairs = _synthetic_pairs(n_pos=50, n_neg=50)
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=200, confidence=0.95, seed=42,
        engine="torch",
        device="cpu",
        chunk_size=11,
    )
    assert result is not None
    assert result["chunk_size"] == 11


@requires_torch
def test_torch_engine_records_chunk_size_in_result():
    """Schema parity: the torch dict includes ``chunk_size``
    so the ledger can persist what actually ran (post auto-
    sizing). Symmetric with the numpy engine."""
    pairs = _synthetic_pairs(n_pos=50, n_neg=50)
    result = ct.fixed_threshold_bootstrap_ci(
        pairs, threshold=0.0, direction="gt",
        resamples=100, confidence=0.95, seed=42,
        engine="torch", device="cpu",
    )
    assert result is not None
    assert "chunk_size" in result
    assert isinstance(result["chunk_size"], int)
    assert result["chunk_size"] >= 1


def _stub_provenance_pipeline(monkeypatch):
    """Shared monkeypatch for the end-to-end provenance tests
    below. Stubs the manifest / sweep / ranking calls so the
    test exercises the engine-threading code path without spaCy
    or HF I/O."""
    monkeypatch.setattr(
        ct, "collect_signal_records",
        lambda records, signal_path: [
            (i % 2, float(i)) for i in range(40)
        ],
    )
    monkeypatch.setattr(
        ct, "sweep_threshold",
        lambda pairs, direction, target: {
            "available": True, "threshold": 20.0,
            "fpr_resolution": 0.05,
            "fpr": 0.05, "tpr": 0.5, "precision": 0.5,
            "n_pos": 20, "n_neg": 20,
        },
    )
    monkeypatch.setattr(
        ct, "_ranking_metrics",
        lambda pairs, *, direction: {
            "auc": 0.80, "ap": 0.78,
            "direction_aware_auc": 0.80,
            "direction_aware_ap": 0.78,
        },
    )
    monkeypatch.setattr(
        ct, "_load_fetch_record", lambda manifest_path: {},
    )


def _provenance_args(**overrides):
    import argparse
    base = dict(
        manifest="dummy.jsonl", use="validation",
        signal="burstiness_B", fpr_target=0.01,
        out=None, slug=None, replace=False,
        bootstrap_resamples=20, bootstrap_confidence=0.95,
        bootstrap_seed=42,
        bootstrap_engine="loop",
        bootstrap_chunk_size=None,
        bootstrap_device=None,
        tier2=False, tier3=False, notes=None,
        max_entries=None, max_entries_seed=None,
        records_cache=None, refresh_cache=False,
        allow_polarity_inversion=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@requires_torch
def test_torch_engine_provenance_records_device(monkeypatch):
    """End-to-end through derive_threshold_from_records: the
    resolved device string lands in entry['calibration']
    ['bootstrap_device']. Codex review (PR #56, P1): without
    this an ``auto`` device that resolved to CPU on one host
    and GPU on another would be conflated in the ledger."""
    _stub_provenance_pipeline(monkeypatch)
    args = _provenance_args(
        bootstrap_engine="torch",
        bootstrap_device="cpu",
    )
    entry = ct.derive_threshold_from_records(
        [], args=args, scoring_meta={},
    )
    cal = entry["calibration"]
    assert cal["bootstrap_engine"] == "torch"
    assert cal["bootstrap_device"] == "cpu"
    # And the harness_command carries both --bootstrap-engine and
    # --bootstrap-device so a replay reaches the same code path.
    cmd = entry["harness_command"]
    assert "--bootstrap-engine torch" in cmd
    assert "--bootstrap-device cpu" in cmd


def test_loop_engine_provenance_omits_device(monkeypatch):
    """device is torch-specific; the loop engine's entry should
    have ``bootstrap_device: None`` and harness_command should
    omit ``--bootstrap-device``. This works without torch
    installed since the loop path is pure Python."""
    _stub_provenance_pipeline(monkeypatch)
    args = _provenance_args(bootstrap_engine="loop")
    entry = ct.derive_threshold_from_records(
        [], args=args, scoring_meta={},
    )
    cal = entry["calibration"]
    assert cal["bootstrap_engine"] == "loop"
    assert cal["bootstrap_device"] is None
    assert "--bootstrap-device" not in entry["harness_command"]


def test_numpy_engine_provenance_omits_device(monkeypatch):
    """device is torch-specific — even with --bootstrap-engine
    numpy and a non-None device kwarg, the field should not
    appear in provenance for the numpy entry (it's ignored)."""
    _stub_provenance_pipeline(monkeypatch)
    args = _provenance_args(
        bootstrap_engine="numpy",
        bootstrap_device="cpu",  # ignored
    )
    entry = ct.derive_threshold_from_records(
        [], args=args, scoring_meta={},
    )
    cal = entry["calibration"]
    assert cal["bootstrap_engine"] == "numpy"
    assert cal["bootstrap_device"] is None
    # And harness_command omits --bootstrap-device for the numpy
    # engine (only the engine flag is needed).
    assert "--bootstrap-device" not in entry["harness_command"]
    assert "--bootstrap-engine numpy" in entry["harness_command"]
