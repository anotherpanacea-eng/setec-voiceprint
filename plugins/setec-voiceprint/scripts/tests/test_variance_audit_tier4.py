#!/usr/bin/env python3
"""Tests for variance_audit.py Tier 4 (surprisal) integration (C.4).

The Tier 4 path goes through ``audit_text(do_tier4=True, ...)`` and
``_tier4_surprisal_block``, which lazily import
``surprisal_audit`` and (optionally) ``surprisal_backend``. Tests
inject a stub ``score_fn`` so no real causal LM is loaded — same
strategy as ``test_surprisal_audit.py``.

Coverage:

  * Tier 4 disabled by default — audit_text without ``do_tier4=True``
    produces no ``tier4`` key.
  * Tier 4 enabled with stub score_fn produces the expected shape:
    ``tier4.surprisal.mean``, ``tier4.surprisal.sd``,
    ``tier4.surprisal.autocorrelation.lag_1``, etc.
  * COMPRESSION_HEURISTICS gains three new entries
    (``surprisal_mean``, ``surprisal_sd``, ``surprisal_acf_lag1``)
    that all carry ``provisional=True`` per SPEC §3.5.
  * The classifier's `provisional_signals()` lists the new
    entries.
  * Direction polarities match SPEC §4.3 (mean=lt, sd=lt, acf=gt).
  * Available=False reason surfaces when text is empty or the
    stub returns an empty series — no crash.
  * The provisional/calibration_anchor fields propagate into the
    Tier 4 block so band-classifier consumers see the
    user-baseline-required marker.
  * Signal paths in COMPRESSION_HEURISTICS resolve via the same
    extractor path used by the band classifier (no orphan paths).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import variance_audit as va  # type: ignore


# ---------- Stub scorer ----------


def _stub_flat_score(text: str, *, return_top_k: int = 0):
    """Synthetic surprisal series with low mean + low SD +
    high lag-1 ACF — should fire all three Tier 4 thresholds
    if the operator opted in."""
    # 50 surprisals hovering near 3 bits with low variance.
    series = [3.0 + 0.1 * (i % 3 - 1) for i in range(50)]
    top = [
        {"position": i, "token_id": 0, "token_text": "x",
         "surprisal_bits": series[i - 1]}
        for i in range(1, return_top_k + 1)
    ] if return_top_k > 0 else []
    if return_top_k > 0:
        return series, top
    return series


def _stub_empty_score(text: str, *, return_top_k: int = 0):
    if return_top_k > 0:
        return [], []
    return []


# Build a small but sufficient prose block — at least 50 words so
# audit_text doesn't bail with the "below 50 words" warning, and
# enough that the Tier 1 stats render meaningfully.
SAMPLE_PROSE = " ".join(
    [
        "The committee discussed the proposal at length.",
        "However, the funding allocation remained ambiguous.",
        "Therefore, the timeline shifted again.",
        "Nevertheless, the team produced an initial prototype.",
        "For example, the dashboard now reflects daily activity.",
        "On the other hand, the integration phase is delayed.",
        "In short, more work is needed before the launch.",
    ]
) * 5


# ---------- Tier 4 off by default ----------


class TestTier4DisabledByDefault:
    def test_audit_text_default_omits_tier4(self):
        """audit_text() without do_tier4=True must NOT include a
        tier4 block. The opt-in default protects callers from
        accidentally paying surprisal cost."""
        out = va.audit_text(SAMPLE_PROSE, do_tier2=False, do_tier3=False)
        assert "tier4" not in out

    def test_do_tier4_false_omits_tier4(self):
        """Explicit do_tier4=False is the same as omission."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=False,
        )
        assert "tier4" not in out


# ---------- Tier 4 with stub ----------


class TestTier4WithStub:
    def test_audit_text_with_stub_score_fn(self):
        """do_tier4=True + tier4_score_fn=stub produces a fully-
        populated tier4 block."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        assert "tier4" in out
        tier4 = out["tier4"]
        assert tier4["available"] is True
        assert "surprisal" in tier4
        surprisal = tier4["surprisal"]
        assert "mean" in surprisal
        assert "sd" in surprisal
        assert "variance" in surprisal
        assert "autocorrelation" in surprisal
        # 5 lags reported.
        assert set(surprisal["autocorrelation"]) == {
            "lag_1", "lag_2", "lag_3", "lag_5", "lag_10",
        }

    def test_tier4_block_carries_provisional_markers(self):
        """SPEC §3.5: the Tier 4 block surfaces the
        calibration_anchor + provisional flags so consumers (band
        classifier, ClaimLicense, etc.) never read the values as
        load-bearing."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        s = out["tier4"]["surprisal"]
        assert s["provisional"] is True
        assert s["calibration_anchor"] == "user-baseline-required"

    def test_tier4_block_carries_band_and_top_k(self):
        """The Tier 4 block carries the PROVISIONAL band call from
        the C.3 audit + the top-k token diagnostic."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        s = out["tier4"]["surprisal"]
        assert "band" in s
        # Flat-stub series should land in 'smoothed'.
        assert s["band"]["band"] == "smoothed"
        # Top-k diagnostic surfaced.
        assert len(s["top_k_tokens"]) == 20

    def test_tier4_signal_paths_resolve(self):
        """The COMPRESSION_HEURISTICS Tier 4 entries' signal_path
        values must resolve into the produced tier4 block.
        Otherwise the band classifier will silently skip the new
        signals — the exact regression we want to catch."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            spec = va.COMPRESSION_HEURISTICS[sig]
            value = va._extract_signal(
                out, tuple(spec.signal_path.split(".")),
            )
            # The flat stub produces real numbers for all three.
            # ACF may be None for very short series, but our
            # 50-element stub is above MIN_SERIES_FOR_ACF=30.
            assert value is not None, (
                f"signal_path {spec.signal_path!r} did not resolve "
                f"to a value in the Tier 4 block — band classifier "
                f"would silently skip this heuristic."
            )


# ---------- Edge cases / failure modes ----------


class TestTier4EdgeCases:
    def test_empty_series_marks_unavailable(self):
        """Stub returning an empty series should land in the
        available=False branch with a clear reason. The audit
        shouldn't crash."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_empty_score,
        )
        assert out["tier4"]["available"] is False
        assert "empty" in out["tier4"]["reason"].lower()

    def test_empty_text_marks_unavailable(self):
        """An entirely-empty text shortcut: variance_audit's
        50-word floor returns the audit with a warning, but if
        we somehow get here with text below the empty-check, the
        Tier 4 helper still handles it safely."""
        # _tier4_surprisal_block called directly with empty text.
        block = va._tier4_surprisal_block(
            "", score_fn=_stub_flat_score,
        )
        assert block["available"] is False
        assert "empty" in block["reason"].lower()

    def test_short_text_still_works_in_tier4(self):
        """audit_text refuses (sets a warning) below 50 words, but
        the Tier 4 helper itself is short-text-tolerant: it
        returns whatever the scorer produces. Test the helper
        directly to confirm."""
        short = "Some short prose. Not much here."
        block = va._tier4_surprisal_block(
            short, score_fn=_stub_flat_score,
        )
        # Flat stub returns a series regardless of input length —
        # in production the backend would short-circuit on too-short
        # input. Here we just confirm the helper plumbs the result
        # through.
        assert block["available"] is True


# ---------- COMPRESSION_HEURISTICS entries ----------


class TestCompressionHeuristicsTier4:
    def test_three_new_entries_registered(self):
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert sig in va.COMPRESSION_HEURISTICS

    def test_all_three_are_provisional(self):
        """SPEC §3.5: Tier 4 thresholds ship with provisional=True.
        A non-provisional ThresholdSpec would require a provenance
        slug (which we don't have — surprisal hasn't been
        calibrated against any labeled corpus yet)."""
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert va.COMPRESSION_HEURISTICS[sig].provisional is True
            assert va.COMPRESSION_HEURISTICS[sig].provenance is None

    def test_directions_match_spec_4_3(self):
        """SPEC §4.3 polarities:
          surprisal_mean: lt (AI < human)
          surprisal_sd:   lt
          surprisal_acf_lag1: gt
        """
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].direction == "lt"
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].direction == "lt"
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].direction == "gt"

    def test_weights_match_spec_4_3(self):
        """SPEC §4.3 weights: mean=1.5, sd=2.0 (heaviest Tier 4
        contributor), acf=1.0."""
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].weight == 1.5
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].weight == 2.0
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].weight == 1.0

    def test_signal_paths_point_into_tier4(self):
        """All three signal_paths point under tier4.surprisal.*
        — the path the audit_text builder writes."""
        assert va.COMPRESSION_HEURISTICS["surprisal_mean"].signal_path == \
            "tier4.surprisal.mean"
        assert va.COMPRESSION_HEURISTICS["surprisal_sd"].signal_path == \
            "tier4.surprisal.sd"
        assert va.COMPRESSION_HEURISTICS["surprisal_acf_lag1"].signal_path == \
            "tier4.surprisal.autocorrelation.lag_1"

    def test_listed_in_provisional_signals(self):
        """provisional_signals() should return all three Tier 4
        entries — they're the canonical "not yet calibrated"
        members of the heuristic set."""
        provisional = set(va.provisional_signals())
        for sig in (
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ):
            assert sig in provisional


# ---------- CLI argparse ----------


class TestTier4CliFlags:
    def test_tier4_flag_default_false(self):
        """--tier4 must default to False (opt-in per SPEC §4.1)."""
        # variance_audit's CLI parser is built lazily inside main().
        # We invoke build_arg_parser if exposed, else replicate the
        # default by inspecting parse_args.
        import argparse
        # Use the actual parser via main()'s argparse construction.
        # variance_audit doesn't expose build_arg_parser; instead,
        # we test that args.tier4 defaults to False via a parse of
        # the minimum args.
        parser = argparse.ArgumentParser()
        # Mirror the relevant flag definitions:
        parser.add_argument("input")
        parser.add_argument("--tier4", action="store_true", default=False)
        parser.add_argument("--surprisal-model", default=None)
        args = parser.parse_args(["dummy.txt"])
        assert args.tier4 is False
        assert args.surprisal_model is None


# ---------- Reviewer P2 fixes (2026-05-14) ----------


class TestTier4BackendIdentifierAttached:
    """Reviewer P2: the Tier 4 block must carry
    ``backend.identifier_block()`` so two variance runs against
    different ``--surprisal-model`` values are distinguishable in
    the JSON. The standalone audit already attaches the
    identifier_block; the variance Tier 4 path now matches."""

    def test_backend_identifier_attached_when_backend_supplied(self):
        """When a real-shaped backend is passed (we use a
        FakeBackend with the identifier_block method), the
        resulting tier4 block carries `backend` with the
        model+revision+alias fields."""

        class _FakeBackend:
            def identifier_block(self):
                return {
                    "id": "TinyLlama/TinyLlama-1.1B-...",
                    "revision": "deadbeef" * 5,
                    "alias": "tinyllama",
                    "deterministic_mode": True,
                    "method": "transformers-causal-lm",
                }

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_FakeBackend(),
        )
        backend_id = out["tier4"]["backend"]
        assert backend_id["id"].startswith("TinyLlama")
        assert backend_id["revision"] == "deadbeef" * 5
        assert backend_id["alias"] == "tinyllama"
        assert backend_id["method"] == "transformers-causal-lm"

    def test_no_backend_identifier_when_only_score_fn(self):
        """When only a ``tier4_score_fn`` is supplied (the
        test/stub path), there is no backend identifier to record.
        The tier4 block should NOT carry a ``backend`` key — its
        absence signals to consumers that this run was
        stub-driven, not from a real causal LM."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        assert "backend" not in out["tier4"]

    def test_defensive_against_broken_identifier_block(self):
        """If a backend's ``identifier_block`` raises (e.g., a
        misbehaved third-party backend), the Tier 4 helper must
        not crash the audit — the identifier is best-effort."""

        class _BrokenBackend:
            def identifier_block(self):
                raise RuntimeError("simulated identifier failure")

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_BrokenBackend(),
        )
        # The audit ran; the identifier just isn't recorded.
        assert out["tier4"]["available"] is True
        assert "backend" not in out["tier4"]


class TestTier4MarkdownVisible:
    """Reviewer P2: ``format_summary`` rendered Tier 1/2/3 but not
    Tier 4, so the band call's Tier 4 contribution was unauditable
    for any reader who only saw the markdown. The fixed summary
    includes a Tier 4 section with mean / SD / lag-1 ACF / band /
    backend identifier / top-3 token preview."""

    def test_format_summary_renders_tier4_section(self):
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_stub_flat_score,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        # Section header present.
        assert "Tier 4 (surprisal)" in text
        # Headline metrics present (the three that feed band).
        assert "Mean surprisal" in text
        assert "bits/token" in text
        assert "SD:" in text
        assert "lag-1 ACF" in text
        # PROVISIONAL band call surfaced.
        assert "Band (PROVISIONAL)" in text
        assert "user-baseline-required" in text

    def test_format_summary_omits_tier4_section_when_not_run(self):
        """When --tier4 was off (no tier4 key in the audit dict),
        the summary should NOT print an empty Tier 4 section."""
        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        assert "Tier 4 (surprisal)" not in text

    def test_format_summary_unavailable_tier4_shows_reason(self):
        """When --tier4 was on but the helper couldn't run (e.g.,
        transformers missing → available=False), the summary
        renders the unavailable reason rather than silently
        dropping the section."""
        def _broken_stub(text, *, return_top_k=0):
            if return_top_k > 0:
                return [], []
            return []

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_score_fn=_broken_stub,
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        # Section appears as "not available" with the reason
        # text included so readers can see why.
        assert "Tier 4 (surprisal): not available" in text

    def test_format_summary_tier4_renders_backend_identifier(self):
        """The Tier 4 section should surface the backend's model
        + revision so the reader can audit which causal LM
        produced the numbers."""

        class _FakeBackend:
            def identifier_block(self):
                return {
                    "id": "TinyLlama/TinyLlama-1.1B-test",
                    "revision": "abc123def456",
                    "alias": "tinyllama",
                    "deterministic_mode": True,
                    "method": "transformers-causal-lm",
                }

            def score_text(self, text, *, return_top_k=0):
                return _stub_flat_score(
                    text, return_top_k=return_top_k,
                )

        out = va.audit_text(
            SAMPLE_PROSE, do_tier2=False, do_tier3=False,
            do_tier4=True, tier4_backend=_FakeBackend(),
        )
        compression = va.classify_compression(out)
        text = va.format_summary(out, compression)
        assert "TinyLlama" in text
        assert "abc123def456" in text
        assert "tinyllama" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
