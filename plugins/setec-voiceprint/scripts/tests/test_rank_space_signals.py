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


# Test 2: the two summable LRR components — the per-token surprisal_nats series
# (the numerator terms) and the log_rank series (the denominator terms). The
# rank-0 position carries its FULL surprisal in the numerator series (it is NOT
# dropped) and a 0 in the log_rank series. There is no per-token ratio and no inf.
def test_lrr_component_series():
    log_probs_nats, token_ids, surprisal_bits = _fixture()
    out = rs.rank_series_from_distributions(
        log_probs_nats, token_ids, surprisal_bits
    )
    assert "lrr_series" not in out  # the per-token-ratio series is gone
    surp = out["surprisal_nats_series"]
    log_rank = out["log_rank_series"]
    # surprisal_nats = surprisal_bits / log2(e); finite and >= 0 at EVERY pos,
    # including the rank-0 position (pos 1) — it feeds the numerator.
    assert math.isclose(surp[0], surprisal_bits[0] / _LOG2E, rel_tol=1e-12)
    assert math.isclose(surp[1], surprisal_bits[1] / _LOG2E, rel_tol=1e-12)
    assert math.isclose(surp[2], surprisal_bits[2] / _LOG2E, rel_tol=1e-12)
    assert all(math.isfinite(x) and x >= 0.0 for x in surp)
    # pos 1 (rank 0) contributes 0.5 nats to the numerator, 0 to the denominator.
    assert surp[1] > 0.0
    assert log_rank[1] == 0.0


# Test 3: aggregate scalars match the stdlib formulas, and LRR is the RATIO OF
# SEQUENCE SUMS — sum(surprisal_nats) / sum(log_rank) — with the rank-0 position
# (log_rank == 0) feeding the numerator (its surprisal) but adding 0 to the
# denominator (it is NOT dropped from either the count or the numerator).
def test_aggregate_signals_basic():
    log_rank_series = [0.0, math.log(2), math.log(3), math.log(2), 0.0]
    # surprisal nats per position; the two rank-0 positions (0 and 4) carry real
    # surprisal that must reach the LRR numerator.
    surprisal_nats_series = [0.7, 1.0, 2.0, 3.0, 0.9]
    agg = rs.aggregate_rank_signals(
        log_rank_series, surprisal_nats_series, surprisal_bits=[]
    )
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
    # LRR = sum(all surprisal_nats, INCLUDING rank-0 positions) / sum(log_rank).
    expected_lrr = sum(surprisal_nats_series) / sum(log_rank_series)
    assert math.isclose(agg["lrr"], expected_lrr, rel_tol=1e-12)
    # the two rank-0 positions are reported (numerator-only), not "excluded".
    assert "lrr_excluded_positions" not in agg
    assert agg["log_rank_zero_positions"] == 2
    assert agg["n_positions"] == 5
    # every emitted scalar is finite-or-None (R4 gate safety)
    for k in ("log_rank_mean", "log_rank_sd", "log_rank_acf1", "lrr"):
        assert agg[k] is None or math.isfinite(agg[k])


# Test 4 (rank-0 edge case, REVIEW + build note): when EVERY scored token is the
# single most-probable token (rank 0), the SEQUENCE denominator sum(log_rank) is
# 0, so the LRR ratio is undefined and aggregate refuses with None (not a
# fabricated 0). No ZeroDivisionError is raised. This is the ONLY None case.
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
    # surprisal still carried per position (would feed the numerator) — finite.
    assert all(math.isfinite(x) for x in out["surprisal_nats_series"])
    agg = rs.aggregate_rank_signals(
        out["log_rank_series"], out["surprisal_nats_series"], surprisal_bits
    )
    assert agg["lrr"] is None  # sequence denominator == 0 -> refusal
    assert agg["log_rank_zero_positions"] == 2
    assert agg["log_rank_mean"] == 0.0  # finite


# Test 4b (P1 REGRESSION — DetectLLM ratio-of-sums vs mean-of-ratios): the
# load-bearing math finding. LRR (2306.05540) is sum(surprisal_nats) /
# sum(log(rank+1)) — a ratio of sequence aggregates — NOT the mean of the
# per-token ratios with rank-0 tokens dropped. This fixture is constructed so the
# two computations give DIFFERENT numbers, and the difference is driven entirely
# by the top-ranked (rank-0) token whose surprisal the old code discarded.
#
# Hand-computed (3 positions, vocab 5):
#   pos 0: actual token = rank 1 -> log_rank = log(2);   surprisal = 3.0 nats
#   pos 1: actual token = rank 0 (TOP-RANKED) -> log_rank = log(1) = 0; surp = 2.0
#   pos 2: actual token = rank 4 -> log_rank = log(5);   surprisal = 0.5 nats
#
#   CORRECT ratio-of-sums:
#     numerator = 3.0 + 2.0 + 0.5 = 5.5   (rank-0 surprisal 2.0 INCLUDED)
#     denominator = log(2) + 0 + log(5) = 0.6931472 + 1.6094379 = 2.3025851
#     LRR = 5.5 / 2.3025851 = 2.3886197...
#
#   OLD (wrong) mean-of-per-token-ratios, rank-0 dropped:
#     finite ratios = {3.0/log(2)=4.328085, 0.5/log(5)=0.310667}
#     mean = (4.328085 + 0.310667) / 2 = 2.3193763...   != 2.3886197
#   The 2.0 nats of the top-ranked token are entirely missing from the old value.
def test_lrr_is_ratio_of_sums_not_mean_of_ratios():
    # vocab 5. token_ids = [9, t1, t2, t3]; positions 0,1,2 predict t1,t2,t3.
    log_probs_nats = [
        # pos 0: descending token0 > token1 > ... ; actual = token 1 -> rank 1.
        [-0.1, -1.0, -2.0, -3.0, -4.0],
        # pos 1: token 2 is the single most-probable; actual = token 2 -> rank 0.
        [-3.0, -2.0, -0.1, -4.0, -5.0],
        # pos 2: actual = token 4, which is the LEAST probable -> rank 4.
        [-0.1, -0.2, -0.3, -0.4, -5.0],
    ]
    token_ids = [9, 1, 2, 4]
    # surprisal_bits chosen so nats = {3.0, 2.0, 0.5} (helper divides by log2(e)).
    surprisal_bits = [3.0 * _LOG2E, 2.0 * _LOG2E, 0.5 * _LOG2E]

    out = rs.rank_series_from_distributions(
        log_probs_nats, token_ids, surprisal_bits
    )
    # ranks land where the hand computation assumes.
    assert out["log_rank_series"][0] == math.log(2)  # rank 1
    assert out["log_rank_series"][1] == 0.0           # rank 0 (top-ranked)
    assert out["log_rank_series"][2] == math.log(5)  # rank 4
    # the top-ranked token's surprisal (2.0 nats) is carried, not discarded.
    assert math.isclose(out["surprisal_nats_series"][1], 2.0, rel_tol=1e-12)

    agg = rs.aggregate_rank_signals(
        out["log_rank_series"], out["surprisal_nats_series"], surprisal_bits
    )

    # The CORRECT DetectLLM statistic: ratio of the two sequence sums.
    expected_ratio_of_sums = 5.5 / (math.log(2) + math.log(5))
    assert math.isclose(expected_ratio_of_sums, 2.3886197, abs_tol=1e-6)
    assert math.isclose(agg["lrr"], expected_ratio_of_sums, rel_tol=1e-12)

    # The OLD (buggy) value: mean of the finite per-token ratios, rank-0 dropped.
    old_buggy_mean_of_ratios = (3.0 / math.log(2) + 0.5 / math.log(5)) / 2
    assert math.isclose(old_buggy_mean_of_ratios, 2.3193763, abs_tol=1e-6)
    # The two MUST differ — proves ratio-of-sums != mean-of-ratios on this input,
    # so this test fails against the pre-fix code and passes against the fix.
    assert not math.isclose(agg["lrr"], old_buggy_mean_of_ratios, rel_tol=1e-6)
    # And the fix's value reflects the included rank-0 surprisal (larger numerator).
    assert agg["lrr"] > old_buggy_mean_of_ratios
    assert agg["log_rank_zero_positions"] == 1


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
