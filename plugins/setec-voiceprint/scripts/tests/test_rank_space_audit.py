#!/usr/bin/env python3
"""Tests for rank_space_audit.py — the registered rank-space surface (spec 32, M1).

These tests exercise ``audit_rank_space`` and ``_band`` over INJECTED stub
distributions (the ``distributions_fn`` seam), so no model / torch / GPU is ever
loaded or run. They fold the review findings on the surface:

  * NO verdict band ships by default (spec §3.5 / §9): with no operator
    thresholds the band is ``"uncalibrated"`` with ``thresholds: None`` — the raw
    scalars only, no default categorical leaf from invented cutoffs. A band
    appears ONLY with operator-supplied ``--threshold-low`` / ``--threshold-high``
    and then carries the operator-supplied caveat.
  * A multi-window (chunked) target — where the backend returns
    ``len(token_ids) > len(log_probs) + 1`` — is REFUSED with a specific
    ``text_too_long`` message naming the scorer context window, NOT a silent
    mis-rank and NOT the generic scorer-blaming "rank computation failed" string.
  * The actual import footprint of ``rank_space_audit`` is pinned so the module
    docstring's import claim can't drift: the genuinely stdlib-clean helper is
    ``rank_space_signals`` (torch-free), while ``rank_space_audit`` pulls
    ``stylometry_core`` (and transitively torch) at import — the tocsin sibling's
    footprint — so the rank math runs model-free over injected stubs but the
    module itself is NOT torch-free.
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

import rank_space_audit as ra  # type: ignore  # noqa: E402

_LOG2E = 1.0 / math.log(2.0)


# A single-window 3-position fixture (vocab 4, token_ids length 4) satisfying the
# contract len(token_ids) == len(log_probs) + 1. Mirrors the signals fixture so
# the surface path is exercised end-to-end without a model.
def _single_window_fn(_text: str):
    log_probs_nats = [
        [-0.1, -2.0, -3.0, -4.0],  # pos 0: actual token 1 -> rank 1
        [-3.0, -2.0, -0.5, -4.0],  # pos 1: actual token 2 -> rank 0 (inf case)
        [-0.5, -1.0, -2.0, -0.5],  # pos 2: tie at top, actual token 3 -> rank 1
    ]
    token_ids = [0, 1, 2, 3]
    surprisal_bits = [2.0 * _LOG2E, 0.5 * _LOG2E, 0.5 * _LOG2E]
    return surprisal_bits, log_probs_nats, token_ids


# A CHUNKED-shape stub: the backend chunks a long target and each chunk forfeits
# its first prediction, so len(log_probs) = N - k (k = num chunks >= 2) while
# token_ids keeps length N. Here N = 8, k = 3 -> len(log_probs) = 5,
# len(token_ids) = 8 (> len(log_probs) + 1 = 6). This is the shape the M1 signals
# tests never injected.
def _chunked_window_fn(_text: str):
    vocab = 4
    log_probs_nats = [[-0.1, -2.0, -3.0, -4.0] for _ in range(5)]
    token_ids = [t % vocab for t in range(8)]  # length 8 = 5 + 3
    surprisal_bits = [1.0 for _ in range(5)]
    return surprisal_bits, log_probs_nats, token_ids


# Default (no operator thresholds): band is "uncalibrated", thresholds None, and
# NO low_lrr/high_lrr leaf is emitted — the raw scalars only.
def test_band_uncalibrated_by_default():
    results = ra.audit_rank_space("x", distributions_fn=_single_window_fn)
    band = results["band"]
    assert band["band"] == "uncalibrated"
    assert band["calibration_status"] == "uncalibrated"
    assert band["thresholds"] is None
    assert "low_lrr" not in band["band"]
    assert "high_lrr" not in band["band"]
    # The scalars are still reported.
    assert results["lrr"] is not None
    assert results["log_rank_mean"] is not None
    # No invented framework cutoff is exported anywhere on the surface.
    assert not hasattr(ra, "PROVISIONAL_BAND_THRESHOLDS")
    assert ra.DEFAULT_THRESHOLD_LOW is None
    assert ra.DEFAULT_THRESHOLD_HIGH is None


# A high LRR with operator-supplied thresholds yields a band that carries the
# operator-supplied (NOT framework-calibrated) caveat. Direction: lrr above the
# high threshold -> high_lrr leaf over the value's OWN axis (not "is AI").
def test_band_only_with_operator_thresholds():
    results = ra.audit_rank_space(
        "x", distributions_fn=_single_window_fn,
        threshold_low=0.5, threshold_high=1.0,
    )
    band = results["band"]
    assert band["band"] in {"low_lrr", "indeterminate", "high_lrr"}
    assert band["calibration_status"] == "heuristic"
    assert band["calibration_anchor"] == "user-baseline-required"
    assert (
        "thresholds_operator_supplied_not_framework_calibrated" in band["caveats"]
    )
    assert band["thresholds"]["lrr"]["low_below"] == 0.5
    assert band["thresholds"]["lrr"]["high_above"] == 1.0


# Supplying only ONE threshold is not a calibrated operating point -> stays
# uncalibrated (both are required, matching the binoculars_audit contract).
def test_band_requires_both_thresholds():
    one = ra.audit_rank_space(
        "x", distributions_fn=_single_window_fn, threshold_high=1.0,
    )
    assert one["band"]["band"] == "uncalibrated"
    assert one["band"]["thresholds"] is None


# The load-bearing chunking finding: a multi-window stub must REFUSE with a
# specific text_too_long message (naming the scorer context window), NOT silently
# mis-rank and NOT the generic "rank computation failed" string.
def test_chunked_input_refuses_text_too_long():
    try:
        ra.audit_rank_space("x", distributions_fn=_chunked_window_fn)
    except ra.RankSpaceTextTooLongError as exc:
        msg = str(exc)
        assert "scorer context window" in msg
        assert "windows" in msg
        # names the actionable remedy, not a scorer-blame
        assert "rank computation failed" not in msg
    else:
        raise AssertionError(
            "a chunked (multi-window) target must raise RankSpaceTextTooLongError"
        )


# RankSpaceTextTooLongError is a RankSpaceInputError subclass, so the CLI's
# ordered handlers can map it to the specific message while still catching the
# base class for other bad inputs.
def test_text_too_long_is_input_error_subclass():
    assert issubclass(ra.RankSpaceTextTooLongError, ra.RankSpaceInputError)


# Pin the actual import footprint so the module docstring's import claim can't
# drift. rank_space_signals is genuinely torch-free; rank_space_audit is NOT
# (it pulls stylometry_core -> torch via the word_tokens import). Asserting both
# in one subprocess keeps the docstring honest.
def test_import_footprint_is_pinned():
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        import rank_space_signals  # the stdlib-clean helper
        assert "torch" not in sys.modules, (
            "rank_space_signals must stay torch-free"
        )
        import rank_space_audit  # the registered surface
        assert "stylometry_core" in sys.modules, (
            "rank_space_audit imports stylometry_core (word_tokens) at module top"
        )
        print("ok")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
