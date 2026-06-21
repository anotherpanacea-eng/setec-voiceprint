#!/usr/bin/env python3
"""Tests for rank_space_signals.py — DetectLLM rank-space helpers (spec 32, M1).

Every test runs over INJECTED stub log-prob distributions. No model, no torch,
no transformers, no GPU is ever loaded or imported (test 6 asserts this in a
subprocess). The six numbered tests map to the spec's §3.6 M1 test plan and fold
the REVIEW's two CHANGE-REQUIRED items: the argsort sort DIRECTION (rank 0 =
highest log-prob, pinned so the family's shared silent sign-inversion can't
regress) and the rank-0 -> inf convention.
"""

from __future__ import annotations

import math
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rank_space_signals as rs  # type: ignore  # noqa: E402

_LOG2E = 1.0 / math.log(2.0)


# A hand-computed 3-position fixture (4 tokens). vocab size 4. token_ids =
# [0, 1, 2, 3], so positions 0,1,2 predict tokens 1,2,3 respectively. Designed
# to exercise: a mid-rank token (pos 0), a rank-0 most-probable token (pos 1,
# the inf case), and a TIE at the top of the distribution broken by ascending
# vocab id (pos 2).
def _fixture():
    log_probs_nats = [
        # pos 0: descending order is token 0 > 1 > 2 > 3. Actual token = 1 ->
        # rank 1 (only token 0 is strictly more probable).
        [-0.1, -2.0, -3.0, -4.0],
        # pos 1: token 2 is the single most-probable. Actual token = 2 ->
        # rank 0 (the inf case for LRR).
        [-3.0, -2.0, -0.5, -4.0],
        # pos 2: tokens 0 and 3 TIE at -0.5 (the max). Actual token = 3 ->
        # nothing is strictly more probable; token 0 ties and sorts ahead
        # (id 0 < 3), so rank 1.
        [-0.5, -1.0, -2.0, -0.5],
    ]
    token_ids = [0, 1, 2, 3]
    # surprisal_bits passed independently (the helper converts bits->nats for
    # the ratio). nats = -log_prob(actual); pos0 actual lp=-2.0 -> nats 2.0;
    # pos1 actual lp=-0.5 -> nats 0.5; pos2 actual lp=-0.5 -> nats 0.5.
    surprisal_bits = [2.0 * _LOG2E, 0.5 * _LOG2E, 0.5 * _LOG2E]
    return log_probs_nats, token_ids, surprisal_bits


# Test 1 (REVIEW Issue 1 — sort direction pinned): the rank series matches
# hand-computed log(rank+1) at every position, with the DESCENDING sort
# convention (rank 0 = most probable -> log(1) = 0.0, NOT a large value).
def test_rank_series_fixture():
    log_probs_nats, token_ids, surprisal_bits = _fixture()
    out = rs.rank_series_from_distributions(
        log_probs_nats, token_ids, surprisal_bits
    )
    log_rank = out["log_rank_series"]
    assert len(log_rank) == 3
    # pos 0: rank 1 -> log(2)
    assert math.isclose(log_rank[0], math.log(2), rel_tol=1e-12)
    # pos 1: rank 0 (most probable) -> log(1) == 0.0. The load-bearing
    # direction pin: a DESCENDING sort puts the most-probable token at rank 0;
    # an ASCENDING (numpy default) sort would make it the LARGEST rank and
    # silently invert the signal. Pin the 0.0.
    assert log_rank[1] == 0.0
    # pos 2: tie at the top, actual token sorts to rank 1 -> log(2)
    assert math.isclose(log_rank[2], math.log(2), rel_tol=1e-12)


# Test 2: the LRR series is surprisal_nats / log_rank at every finite position,
# is strictly > 0 for valid (positive surprisal, rank > 0), and is exactly inf
# at the rank-0 position (the pinned convention).
def test_lrr_series_ratio():
    log_probs_nats, token_ids, surprisal_bits = _fixture()
    out = rs.rank_series_from_distributions(
        log_probs_nats, token_ids, surprisal_bits
    )
    lrr = out["lrr_series"]
    log_rank = out["log_rank_series"]
    # pos 0: (2.0) / log(2)
    expected0 = (surprisal_bits[0] / _LOG2E) / log_rank[0]
    assert math.isclose(lrr[0], expected0, rel_tol=1e-12)
    assert lrr[0] > 0.0
    # pos 1: rank 0 -> log(1) = 0 in the denominator -> inf (pinned convention)
    assert math.isinf(lrr[1])
    # pos 2: (0.5) / log(2), finite and positive
    expected2 = (surprisal_bits[2] / _LOG2E) / log_rank[2]
    assert math.isclose(lrr[2], expected2, rel_tol=1e-12)
    assert lrr[2] > 0.0


# Test 3: aggregate scalars match the stdlib formulas, and the LRR mean EXCLUDES
# the rank-0 (inf) position (averaging over the finite denominator only).
def test_aggregate_signals_basic():
    log_rank_series = [0.0, math.log(2), math.log(3), math.log(2), 0.0]
    lrr_series = [math.inf, 1.0, 2.0, 3.0, math.inf]
    agg = rs.aggregate_rank_signals(log_rank_series, lrr_series, surprisal_bits=[])
    # mean over the full log_rank series
    expected_mean = sum(log_rank_series) / len(log_rank_series)
    assert math.isclose(agg["log_rank_mean"], expected_mean, rel_tol=1e-12)
    # population SD
    m = expected_mean
    expected_sd = math.sqrt(
        sum((x - m) ** 2 for x in log_rank_series) / len(log_rank_series)
    )
    assert math.isclose(agg["log_rank_sd"], expected_sd, rel_tol=1e-12)
    # biased lag-1 ACF: sum((x_i - m)(x_{i+1} - m)) / sum((x_i - m)^2)
    denom = sum((x - m) ** 2 for x in log_rank_series)
    numer = sum(
        (log_rank_series[i] - m) * (log_rank_series[i + 1] - m)
        for i in range(len(log_rank_series) - 1)
    )
    assert math.isclose(agg["log_rank_acf1"], numer / denom, rel_tol=1e-12)
    # LRR mean over the FINITE entries only (the two inf positions excluded)
    assert math.isclose(agg["lrr"], (1.0 + 2.0 + 3.0) / 3.0, rel_tol=1e-12)
    assert agg["lrr_excluded_positions"] == 2
    assert agg["n_positions"] == 5
    # every emitted scalar is finite-or-None (R4 gate safety)
    for k in ("log_rank_mean", "log_rank_sd", "log_rank_acf1", "lrr"):
        assert agg[k] is None or math.isfinite(agg[k])


# Test 4 (rank-0 edge case, REVIEW + build note): a distribution where the
# actual token is the single most-probable token -> log_rank 0.0 and an inf in
# the LRR series that raises no ZeroDivisionError; when EVERY position is rank 0
# the LRR mean is None (refusal, not a fabricated 0).
def test_rank_0_edge_case():
    # Two positions, each predicting the single most-probable token (rank 0).
    log_probs_nats = [
        [-0.1, -5.0, -6.0],  # pos 0 argmax = token 0
        [-5.0, -0.1, -6.0],  # pos 1 argmax = token 1
    ]
    # token_ids[t+1] is each position's most-probable token (rank 0):
    # token_ids[1] = 0 (pos 0 argmax), token_ids[2] = 1 (pos 1 argmax).
    # token_ids[0] is the prompt token and is never scored.
    token_ids = [7, 0, 1]
    surprisal_bits = [0.1 * _LOG2E, 0.1 * _LOG2E]
    out = rs.rank_series_from_distributions(
        log_probs_nats, token_ids, surprisal_bits
    )
    assert out["log_rank_series"] == [0.0, 0.0]
    assert all(math.isinf(x) for x in out["lrr_series"])  # no ZeroDivisionError
    agg = rs.aggregate_rank_signals(
        out["log_rank_series"], out["lrr_series"], surprisal_bits
    )
    assert agg["lrr"] is None  # every position excluded -> refusal
    assert agg["lrr_excluded_positions"] == 2
    assert agg["log_rank_mean"] == 0.0  # finite


# Test 5: short series -> nullable moments (consistent with surprisal_sd/acf1).
def test_short_text_returns_none():
    single = rs.aggregate_rank_signals([0.5], [1.0], surprisal_bits=[1.0])
    assert single["log_rank_sd"] is None  # < 2 points
    assert single["log_rank_acf1"] is None  # < 3 points
    assert single["n_positions"] == 1
    two = rs.aggregate_rank_signals([0.5, 0.7], [1.0, 1.2], surprisal_bits=[])
    assert two["log_rank_sd"] is not None  # 2 points: SD defined
    assert two["log_rank_acf1"] is None  # still < 3 points


# Test 6: importing rank_space_signals pulls NO model stack. Run in a clean
# subprocess and assert torch / transformers / numpy / scipy never enter
# sys.modules — the stdlib-clean guarantee that keeps M1 CI model-free.
def test_import_is_stdlib():
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        import rank_space_signals  # noqa: F401
        banned = [m for m in ("torch", "transformers", "numpy", "scipy")
                  if m in sys.modules]
        assert not banned, f"rank_space_signals pulled: {{banned}}"
        print("ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
