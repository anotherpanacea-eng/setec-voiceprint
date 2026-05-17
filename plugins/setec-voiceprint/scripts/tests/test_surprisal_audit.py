#!/usr/bin/env python3
"""Tests for surprisal_audit.py (Phase C.3).

Stub-backend strategy mirrors test_surprisal_backend.py: the audit
script accepts either a SurprisalBackend or a ``score_fn`` callable,
and these tests pass a deterministic stub that returns synthetic
surprisal series. No real causal LM is loaded — the math layer is
what's under test, not transformers.

Coverage:

  * Pure math helpers (_mean, _sample_variance, _sample_sd,
    _acf_at_lag, _skew, _excess_kurtosis, _position_of_max) against
    hand-computable inputs.
  * audit_surprisal happy path: shape, summary keys, sliding-window
    on/off, top-k surfacing.
  * Degenerate inputs: empty text, empty series, too-short series
    (n < MIN_SERIES_FOR_ACF) flagged.
  * PROVISIONAL band classifier: known-flat synthetic series →
    `smoothed` band; known-spiky → `typical`; mixed → indeterminate.
  * Markdown rendering doesn't crash on a well-formed audit dict.
  * ClaimLicense block carries calibration_anchor: user-baseline-
    required (the load-bearing PROVISIONAL-marker contract per
    SPEC §3.5).
  * CLI end-to-end via main() with a monkeypatched
    SurprisalBackend.score_text.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import surprisal_audit as sa  # type: ignore
from surprisal_backend import SurprisalBackend  # type: ignore


# ---------- Stub scorer ----------


def _flat_stub(text: str, *, return_top_k: int = 0):
    """Stub: returns a low-SD, high-ACF series (smoothed-AI shape)."""
    # 100 surprisals that hover near 3.0 with small variance.
    series = [3.0 + 0.1 * (i % 3 - 1) for i in range(100)]
    top = [
        {"position": i, "token_id": 0, "token_text": "x",
         "surprisal_bits": series[i - 1]}
        for i in range(1, return_top_k + 1)
    ] if return_top_k > 0 else []
    if return_top_k > 0:
        return series, top
    return series


def _spiky_stub(text: str, *, return_top_k: int = 0):
    """Stub: high-variance series with occasional spikes (human-ish)."""
    import random
    random.seed(42)
    series = []
    for _ in range(100):
        # 80% low (around 2 bits), 20% high spike (around 12 bits).
        if random.random() < 0.2:
            series.append(12.0 + random.random())
        else:
            series.append(2.0 + random.random())
    top = [
        {"position": i, "token_id": 0, "token_text": "x",
         "surprisal_bits": series[i - 1]}
        for i in range(1, return_top_k + 1)
    ] if return_top_k > 0 else []
    if return_top_k > 0:
        return series, top
    return series


def _short_stub(text: str, *, return_top_k: int = 0):
    """Stub: 10-element series (below MIN_SERIES_FOR_ACF=30)."""
    series = [3.0, 4.0, 2.0, 5.0, 1.5, 6.0, 2.5, 4.5, 3.5, 5.5]
    top = [
        {"position": 1, "token_id": 0, "token_text": "x",
         "surprisal_bits": series[0]}
    ] if return_top_k > 0 else []
    if return_top_k > 0:
        return series, top
    return series


def _empty_stub(text: str, *, return_top_k: int = 0):
    if return_top_k > 0:
        return [], []
    return []


# ---------- Pure math ----------


class TestPureMath:
    def test_mean_empty_is_zero(self):
        assert sa._mean([]) == 0.0

    def test_mean_known(self):
        assert sa._mean([1.0, 2.0, 3.0, 4.0]) == 2.5

    def test_sample_variance_matches_statistics(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert sa._sample_variance(xs) == pytest.approx(
            statistics.variance(xs), rel=1e-9,
        )

    def test_sample_variance_n_lt_2_is_zero(self):
        assert sa._sample_variance([]) == 0.0
        assert sa._sample_variance([5.0]) == 0.0

    def test_sample_sd_matches_sqrt_variance(self):
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert sa._sample_sd(xs) == pytest.approx(
            math.sqrt(sa._sample_variance(xs)), rel=1e-9,
        )

    def test_acf_too_short_returns_none(self):
        """Below MIN_SERIES_FOR_ACF the helper refuses to estimate."""
        xs = [1.0, 2.0, 3.0, 4.0, 5.0]  # length 5 << 30
        assert sa._acf_at_lag(xs, 1) is None

    def test_acf_zero_lag_invalid(self):
        xs = [float(i) for i in range(50)]
        assert sa._acf_at_lag(xs, 0) is None

    def test_acf_lag_geq_n_invalid(self):
        xs = [float(i) for i in range(50)]
        assert sa._acf_at_lag(xs, 50) is None
        assert sa._acf_at_lag(xs, 60) is None

    def test_acf_constant_series_returns_none(self):
        """A constant series has zero variance → ACF undefined."""
        xs = [3.0] * 50
        assert sa._acf_at_lag(xs, 1) is None

    def test_acf_increasing_series_is_positive(self):
        """A monotonically-increasing series has high positive ACF
        at small lags. We don't pin the exact value but it must be
        large positive and finite."""
        xs = [float(i) for i in range(60)]
        acf1 = sa._acf_at_lag(xs, 1)
        assert acf1 is not None
        assert acf1 > 0.9  # near-perfect autocorrelation

    def test_skew_too_short_returns_none(self):
        assert sa._skew([1.0]) is None
        assert sa._skew([1.0, 2.0]) is None

    def test_skew_symmetric_near_zero(self):
        """A symmetric series should have skew ~0."""
        xs = [-3.0, -2.0, -1.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0]
        s = sa._skew(xs)
        assert s is not None
        assert abs(s) < 0.5

    def test_kurtosis_constant_returns_none(self):
        assert sa._excess_kurtosis([3.0] * 10) is None

    def test_position_of_max(self):
        assert sa._position_of_max([1.0, 5.0, 2.0, 4.0]) == 1
        assert sa._position_of_max([]) is None


# ---------- Sliding windows ----------


class TestSlidingWindows:
    def test_empty_returns_empty(self):
        assert sa._sliding_windows([], window_size=10, stride=5) == []

    def test_invalid_params_return_empty(self):
        assert sa._sliding_windows(
            [1.0, 2.0], window_size=0, stride=5,
        ) == []
        assert sa._sliding_windows(
            [1.0, 2.0], window_size=5, stride=0,
        ) == []

    def test_window_count_with_full_strides(self):
        """A series of length 30 with W=10, S=10 → 3 windows."""
        series = list(range(30))
        ws = sa._sliding_windows(series, window_size=10, stride=10)
        assert len(ws) == 3
        # Check the third window's bounds.
        assert ws[2]["start_index"] == 20
        assert ws[2]["end_index"] == 30

    def test_window_count_with_overlap(self):
        """Length 100, W=20, S=10 → windows at 0,10,20,...,80 = 9."""
        series = list(range(100))
        ws = sa._sliding_windows(series, window_size=20, stride=10)
        # Windows start at 0,10,20,30,40,50,60,70,80 → 9 windows.
        assert len(ws) == 9

    def test_last_window_truncated_for_short_remainder(self):
        """Length 25, W=10, S=10 → windows at 0,10,20; last is
        length 5."""
        series = list(range(25))
        ws = sa._sliding_windows(series, window_size=10, stride=10)
        assert len(ws) == 3
        assert ws[2]["length"] == 5

    def test_window_stats_correct(self):
        """A window over a constant slice has mean=that value,
        sd=0, and ACF=None (constant → zero variance)."""
        series = [3.0] * 50
        ws = sa._sliding_windows(series, window_size=25, stride=25)
        assert len(ws) == 2
        for w in ws:
            assert w["mean"] == pytest.approx(3.0)
            assert w["sd"] == pytest.approx(0.0)
            # 25-element window < MIN_SERIES_FOR_ACF (30) so ACF is None.
            assert w["acf_lag1"] is None


# ---------- Provisional banding ----------


class TestProvisionalBand:
    def test_flat_stats_yields_smoothed(self):
        """Low mean + low SD + high ACF → smoothed."""
        summary = {
            "mean_surprisal_bits": 3.0,
            "sd_surprisal_bits": 1.0,
            "autocorrelation": {"lag_1": 0.5},
        }
        band = sa._provisional_band(summary)
        assert band["band"] == "smoothed"
        assert band["provisional"] is True
        assert band["calibration_anchor"] == "user-baseline-required"
        # Flags name which signals triggered the call.
        assert "mean_surprisal_low" in band["flags"]
        assert "sd_surprisal_low" in band["flags"]
        assert "acf_lag1_high" in band["flags"]

    def test_typical_stats_yields_typical(self):
        """High mean + high SD + low ACF → typical."""
        summary = {
            "mean_surprisal_bits": 6.0,
            "sd_surprisal_bits": 3.0,
            "autocorrelation": {"lag_1": 0.05},
        }
        band = sa._provisional_band(summary)
        assert band["band"] == "typical"
        assert band["provisional"] is True

    def test_mixed_stats_yields_indeterminate(self):
        """One smoothed + one typical signal → indeterminate
        (no 2-of-3 majority)."""
        summary = {
            "mean_surprisal_bits": 3.0,  # low → smoothed
            "sd_surprisal_bits": 3.0,    # high → typical
            "autocorrelation": {"lag_1": 0.2},  # middle
        }
        band = sa._provisional_band(summary)
        assert band["band"] == "indeterminate"

    def test_missing_acf_doesnt_crash(self):
        """If ACF is None (too-short series) the helper still
        returns a band call based on the available signals."""
        summary = {
            "mean_surprisal_bits": 3.0,
            "sd_surprisal_bits": 1.0,
            "autocorrelation": {"lag_1": None},
        }
        band = sa._provisional_band(summary)
        # 2 of 2 available signals say smoothed → smoothed.
        assert band["band"] == "smoothed"


# ---------- audit_surprisal end-to-end ----------


class TestAuditSurprisal:
    def test_empty_text_returns_unavailable(self):
        out = sa.audit_surprisal("", score_fn=_flat_stub)
        assert out["available"] is False
        assert "empty" in out["reason"].lower()

    def test_whitespace_text_returns_unavailable(self):
        out = sa.audit_surprisal("   \n\t  ", score_fn=_flat_stub)
        assert out["available"] is False

    def test_empty_series_returns_unavailable(self):
        out = sa.audit_surprisal("ok text", score_fn=_empty_stub)
        assert out["available"] is False
        assert "empty" in out["reason"].lower()

    def test_requires_backend_or_score_fn(self):
        with pytest.raises(ValueError):
            sa.audit_surprisal("text", backend=None, score_fn=None)

    def test_basic_shape_with_stub_backend(self):
        out = sa.audit_surprisal("text", score_fn=_flat_stub)
        assert out["available"] is True
        assert out["task_surface"] == "smoothing_diagnosis"
        assert out["tool"] == "surprisal_audit"
        assert out["series_length"] == 100
        assert out["n_tokens_scored"] == 101  # series_length + 1
        summary = out["summary"]
        assert "mean_surprisal_bits" in summary
        assert "sd_surprisal_bits" in summary
        assert "autocorrelation" in summary
        # Five lags reported.
        assert set(summary["autocorrelation"]) == {
            "lag_1", "lag_2", "lag_3", "lag_5", "lag_10",
        }
        # Top-k tokens surfaced.
        assert len(out["top_k_tokens"]) == sa.DEFAULT_TOP_K

    def test_top_k_zero_omits_diagnostic(self):
        out = sa.audit_surprisal(
            "text", score_fn=_flat_stub, top_k=0,
        )
        assert out["top_k_tokens"] == []

    def test_short_series_flags_acf_unstable(self):
        """A 10-element series should set the
        series_too_short_for_acf marker and report all lags as
        None."""
        out = sa.audit_surprisal("text", score_fn=_short_stub)
        assert out["available"] is True
        assert out["summary"]["series_too_short_for_acf"] is True
        for lag in (1, 2, 3, 5, 10):
            assert out["summary"]["autocorrelation"][f"lag_{lag}"] is None

    def test_sliding_window_enabled(self):
        out = sa.audit_surprisal(
            "text", score_fn=_flat_stub,
            sliding_window=True,
            window_size=30, stride=20,
        )
        sw = out["sliding_window"]
        assert sw["enabled"] is True
        assert sw["window_size_tokens"] == 30
        assert sw["stride_tokens"] == 20
        assert sw["n_windows"] >= 1
        # First window has the expected stats shape.
        w0 = sw["trajectory"][0]
        assert "start_index" in w0
        assert "end_index" in w0
        assert "mean" in w0
        assert "sd" in w0
        assert "acf_lag1" in w0

    def test_sliding_window_default_disabled(self):
        out = sa.audit_surprisal("text", score_fn=_flat_stub)
        assert out["sliding_window"]["enabled"] is False

    def test_band_block_present(self):
        out = sa.audit_surprisal("text", score_fn=_flat_stub)
        band = out["band"]
        assert band["provisional"] is True
        assert band["calibration_anchor"] == "user-baseline-required"
        assert band["band"] in {"smoothed", "typical", "indeterminate"}

    def test_flat_stub_lands_in_smoothed_band(self):
        """The flat stub is constructed to produce a smoothed-AI
        signature: low mean (~3 bits), low SD, high ACF."""
        out = sa.audit_surprisal("text", score_fn=_flat_stub)
        assert out["band"]["band"] == "smoothed"


class TestAuditSurprisalRuntimeFailures:
    """Reviewer P2 regression (2026-05-14): scoring-time exceptions
    other than ``SurprisalBackendError`` used to escape
    ``audit_surprisal`` and produce a traceback. Common causes:
    RuntimeError on context-window overflow, IndexError on
    tokenizer surprises, MemoryError on too-large inputs. The
    fixed code converts these to an ``available=False`` return
    value so the CLI exits via the documented unavailable path
    rather than a stacktrace, and the variance Tier 4 helper sees
    the same clean failure shape it sees for empty-series inputs.
    """

    def test_runtime_error_is_caught_and_reported(self):
        def _raise_runtime(text, *, return_top_k=0):
            raise RuntimeError(
                "CUDA out of memory: tried to allocate 4.20 GiB"
            )

        out = sa.audit_surprisal("text", score_fn=_raise_runtime)
        assert out["available"] is False
        assert "scoring failed" in out["reason"].lower()
        assert "RuntimeError" in out["reason"]
        # Reason mentions the SPEC §3.3 chunking contract so the
        # operator has a pointer to the documented remediation.
        assert "context window" in out["reason"] or "§3.3" in out["reason"]

    def test_index_error_is_caught_and_reported(self):
        """Tokenizer surprises (e.g., unexpected sequence shapes)
        can raise IndexError out of the model's forward pass.
        Should not traceback."""
        def _raise_index(text, *, return_top_k=0):
            raise IndexError("index 4097 is out of bounds for axis 1")

        out = sa.audit_surprisal("text", score_fn=_raise_index)
        assert out["available"] is False
        assert "IndexError" in out["reason"]

    def test_memory_error_is_caught_and_reported(self):
        def _raise_memory(text, *, return_top_k=0):
            raise MemoryError("simulated host OOM")

        out = sa.audit_surprisal("text", score_fn=_raise_memory)
        assert out["available"] is False
        assert "MemoryError" in out["reason"]

    def test_backend_error_still_propagates(self):
        """``SurprisalBackendError`` from the backend (e.g., model
        failed to load) must still bubble up so the CLI's existing
        rc=3 path keeps working — callers depending on the
        distinction (load failure vs runtime failure) shouldn't
        regress."""
        from surprisal_backend import (  # type: ignore
            SurprisalBackendError,
        )

        def _raise_backend(text, *, return_top_k=0):
            raise SurprisalBackendError("model load failed")

        with pytest.raises(SurprisalBackendError):
            sa.audit_surprisal("text", score_fn=_raise_backend)

    def test_cli_main_handles_runtime_error_cleanly(
        self, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """End-to-end via main(): a RuntimeError from
        SurprisalBackend.score_text must produce a clean
        unavailable-audit output rather than a traceback."""
        target = tmp_path / "essay.txt"
        target.write_text("Some prose text.", encoding="utf-8")

        def _stub_runtime_err(self_, text, *, return_top_k=0):
            raise RuntimeError("context overflow")

        monkeypatch.setattr(
            SurprisalBackend, "score_text", _stub_runtime_err,
        )
        out = tmp_path / "out.md"
        rc = sa.main([str(target), "--out", str(out)])
        # rc=0: the audit completed and the unavailable-path was
        # rendered cleanly. The CLI's existing rc=3 is for
        # SurprisalBackendError specifically; runtime errors land
        # in audit_surprisal's converted-result path.
        assert rc == 0
        text = out.read_text()
        assert "Unavailable" in text
        # The reason text was surfaced in the markdown.
        assert "context overflow" in text.lower() or "scoring failed" in text.lower()


# ---------- Markdown rendering ----------


class TestRenderMarkdown:
    def test_unavailable_renders_short_note(self):
        text = sa.render_markdown({
            "available": False, "reason": "test reason",
        })
        assert "Surprisal audit" in text
        assert "Unavailable" in text
        assert "test reason" in text

    def test_full_audit_renders_sections(self):
        out = sa.audit_surprisal(
            "text", score_fn=_flat_stub,
            sliding_window=True, window_size=30, stride=15,
        )
        out["backend"] = {
            "id": "stub-model", "revision": None, "alias": "stub",
            "deterministic_mode": True, "method": "stub",
        }
        text = sa.render_markdown(out)
        # Top-level sections.
        assert "# Surprisal audit" in text
        assert "## Distribution summary" in text
        assert "## Autocorrelation" in text
        assert "## Sliding-window trajectory" in text
        assert "## Band (PROVISIONAL)" in text
        # ClaimLicense block.
        assert "## What this result licenses" in text
        # Surprisal stats appear with units.
        assert "bits/token" in text

    def test_claim_license_block_names_user_baseline_required(self):
        """SPEC §3.5: the ClaimLicense must name calibration_anchor:
        user-baseline-required so the PROVISIONAL nature of bands is
        legible at the point readers see the band call."""
        out = sa.audit_surprisal("text", score_fn=_flat_stub)
        out["backend"] = {"id": "stub", "revision": None}
        text = sa.render_markdown(out)
        assert "user-baseline-required" in text
        assert "PROVISIONAL" in text


# ---------- CLI ----------


class TestCli:
    def test_cli_missing_file_returns_2(self, tmp_path: Path):
        rc = sa.main([str(tmp_path / "nope.txt")])
        assert rc == 2

    def test_cli_end_to_end_with_monkeypatched_backend(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """End-to-end through main() — monkeypatch SurprisalBackend
        so no real causal LM gets loaded."""
        target = tmp_path / "essay.txt"
        target.write_text("Some prose text.", encoding="utf-8")

        # Stub the backend's score_text method so SurprisalBackend
        # never tries to load a real causal LM.
        def _stub_score(self_, text, *, return_top_k=0):
            return _flat_stub(text, return_top_k=return_top_k)

        # identifier_block doesn't touch any model state — it just
        # reports what model_id is set on the instance — so we can
        # leave it alone.
        monkeypatch.setattr(
            SurprisalBackend, "score_text", _stub_score,
        )
        out = tmp_path / "out.json"
        rc = sa.main([
            str(target), "--json", "--out", str(out),
            "--model", "tinyllama",
        ])
        assert rc == 0
        payload = json.loads(out.read_text())
        # schema_version 1.0 envelope: per-script payload under
        # results; top-level keys stay envelope-canonical.
        assert payload["schema_version"] == "1.0"
        assert payload["available"] is True
        assert payload["task_surface"] == "smoothing_diagnosis"
        assert payload["tool"] == "surprisal_audit"
        # Backend identifier block lives under results.
        assert payload["results"]["backend"]["id"]  # full HF id resolved

    def test_cli_markdown_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        target = tmp_path / "essay.txt"
        target.write_text("Some prose text.", encoding="utf-8")

        def _stub_score(self_, text, *, return_top_k=0):
            return _flat_stub(text, return_top_k=return_top_k)

        monkeypatch.setattr(
            SurprisalBackend, "score_text", _stub_score,
        )
        out = tmp_path / "out.md"
        rc = sa.main([str(target), "--out", str(out)])
        assert rc == 0
        text = out.read_text()
        assert "# Surprisal audit" in text
        assert "user-baseline-required" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
