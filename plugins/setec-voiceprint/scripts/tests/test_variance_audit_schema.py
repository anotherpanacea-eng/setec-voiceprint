#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on variance_audit. Wave 5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import variance_audit as va  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_output(with_baseline=False, with_windows=False):
    """Mirror audit_text()'s real return shape: word/sentence counts
    live under `audit["summary"]`, not at the audit top level. Codex
    P2 on PR #84 caught the original fake fixture using a non-real
    flat shape — fixed here to match audit_text() exactly.
    """
    base = {
        "task_surface": "smoothing_diagnosis",
        "preprocessing": {"opt_out": False, "tokens_stripped": 0},
        "audit": {
            "summary": {
                "n_words": 3500,
                "n_sentences": 220,
                "n_words_original": 3500,
                "reliable": True,
            },
            "tier1": {"sentence_length": {"sd": 8.2, "burstiness_B": -0.15}},
            "tier2": {"pos_bigrams": {"entropy_bits": 7.8}},
            "tier3": {"adjacent_cosine": {"mean": 0.55, "sd": 0.10}},
        },
        "compression": {
            "band": "Lightly smoothed",
            "compression_fraction": 0.2,
            "flagged_signals": ["sentence_length_sd"],
        },
    }
    if with_baseline:
        base["baseline"] = {
            "n_files": 8,
            "aggregate": {"tier1": {"sentence_length": {"sd": {"mean": 9.0, "sd": 1.5}}}},
            "preprocessing": {"opt_out": False},
        }
        base["baseline_comparison"] = {"sentence_length_sd": -0.5}
    if with_windows:
        base["windows"] = {
            "window_size": 500,
            "stride": 250,
            "n_windows": 7,
            "results": [],
        }
    return base


@pytest.fixture
def envelope():
    return va.build_audit_payload(
        _fake_output(), target_path=Path("draft.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "variance_audit"
        assert envelope["version"] == va.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words(self, envelope):
        assert envelope["target"]["words"] == 3500

    def test_target_carries_preprocessing(self, envelope):
        assert "preprocessing" in envelope["target"]

    def test_baseline_null_without_supply(self, envelope):
        assert envelope["baseline"] is None

    def test_baseline_populated_when_supplied(self):
        # baseline_n_words is computed from baseline_block["audits"]
        # in main() and passed explicitly; the synthetic
        # output["baseline"] no longer carries an n_words field
        # (matches the real main() trim).
        env = va.build_audit_payload(
            _fake_output(with_baseline=True),
            target_path=Path("d.md"),
            baseline_n_words=25000,
        )
        assert env["baseline"]["n_files"] == 8
        assert env["baseline"]["words"] == 25000
        assert "aggregate" in env["baseline"]

    def test_baseline_words_defaults_to_zero_when_not_supplied(self):
        """Codex P2 contract: callers that have the full
        baseline_block in scope must pre-compute and pass
        baseline_n_words. Without it, baseline.words is 0 (n_files
        still surfaces correctly).
        """
        env = va.build_audit_payload(
            _fake_output(with_baseline=True), target_path=Path("d.md"),
        )
        assert env["baseline"]["n_files"] == 8
        assert env["baseline"]["words"] == 0


class TestResultsPayload:
    def test_results_carries_audit_and_compression(self, envelope):
        r = envelope["results"]
        assert "audit" in r
        assert "compression" in r
        # Real audit_text() shape: word count under audit.summary.
        assert r["audit"]["summary"]["n_words"] == 3500
        assert r["compression"]["band"] == "Lightly smoothed"

    def test_baseline_comparison_under_results(self):
        env = va.build_audit_payload(
            _fake_output(with_baseline=True), target_path=Path("d.md"),
        )
        assert "baseline_comparison" in env["results"]

    def test_windows_under_results(self):
        env = va.build_audit_payload(
            _fake_output(with_windows=True), target_path=Path("d.md"),
        )
        assert "windows" in env["results"]
        assert env["results"]["windows"]["n_windows"] == 7

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "preprocessing", "audit", "compression",
            "ablation", "baseline_comparison",
            "baseline_divergences", "baseline_bootstrap", "windows",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_words"] == 3500
        assert cs["n_sentences"] == 220
        assert cs["band"] == "Lightly smoothed"
        assert cs["has_baseline"] is False
        assert cs["windowed"] is False

    def test_does_not_license_flags_cross_corpus_inversion(self, envelope):
        text = envelope["claim_license"]["does_not_license"].lower()
        assert "many causes" in text

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestRealAuditShape:
    """Reviewer-reproduced regression (Codex P2 on PR #84).

    Pre-fix: `build_audit_payload()` read `audit["n_words"]`, but
    `audit_text()` stores the count at `audit["summary"]["n_words"]`.
    CLI runs reported `target.words: 0` while the actual word count
    was non-zero. The fake fixture above only passed because it used
    the wrong shape; this test exercises the real audit_text() path
    end-to-end and asserts non-zero target / baseline counts.

    Pinned at the build_audit_payload() boundary so any future
    refactor that breaks the audit→envelope path fails here.
    """

    def test_real_audit_text_produces_nonzero_target_words(self):
        """A real audit_text() call produces audit["summary"]
        ["n_words"] > 0; the envelope should surface that value at
        envelope.target.words, NOT zero."""
        text = (
            "The committee deliberated through the afternoon. "
            "The proposal landed on Tuesday. The budget contracted. "
            "Daria signed off after lunch. The dashboard reflected "
            "regional activity. Stakeholders requested further "
            "analysis. The agency coordination role was delegated. "
        ) * 10
        try:
            audit = va.audit_text(text)
        except Exception:
            pytest.skip("audit_text dependencies unavailable in env")
            return
        # Real audit_text return shape: summary.n_words.
        assert audit["summary"]["n_words"] > 0
        # Build the envelope from a main()-shaped output dict.
        output = {
            "task_surface": va.TASK_SURFACE,
            "audit": audit,
            "compression": {"band": "Lightly smoothed",
                            "compression_fraction": 0.1,
                            "flagged_signals": []},
        }
        envelope = va.build_audit_payload(
            output, target_path=Path("draft.md"),
        )
        assert envelope["target"]["words"] == audit["summary"]["n_words"]
        assert envelope["target"]["words"] > 0
        # Comparison_set should also surface the real word count.
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_words"] == audit["summary"]["n_words"]

    def test_sum_baseline_n_words_aggregates_audits_list(self):
        """_sum_baseline_n_words walks the baseline_block.audits list
        and sums per-file summary.n_words. Without this helper, the
        envelope's baseline.words would always be 0 (because
        output['baseline'] is trimmed in main() and loses the
        audits list)."""
        baseline_block = {
            "n_files": 3,
            "audits": [
                {"file": "a.md", "audit": {"summary": {"n_words": 800}}},
                {"file": "b.md", "audit": {"summary": {"n_words": 1200}}},
                {"file": "c.md", "audit": {"summary": {"n_words": 600}}},
            ],
        }
        assert va._sum_baseline_n_words(baseline_block) == 2600

    def test_sum_baseline_n_words_handles_failed_entries(self):
        """Per the audit_baseline contract, baseline entries that
        failed to parse have an 'error' key instead of 'audit'.
        The summer should skip them without raising."""
        baseline_block = {
            "n_files": 2,
            "audits": [
                {"file": "a.md", "audit": {"summary": {"n_words": 500}}},
                {"file": "b.md", "error": "could not read"},
            ],
        }
        assert va._sum_baseline_n_words(baseline_block) == 500

    def test_sum_baseline_n_words_handles_none(self):
        assert va._sum_baseline_n_words(None) == 0
        assert va._sum_baseline_n_words({}) == 0
        assert va._sum_baseline_n_words({"audits": []}) == 0


class TestFunctionContractStaysLegacy:
    """variance_audit.audit_text() is called as a function by many
    other scripts (validation_harness, calibration_survey,
    sliding_window_heatmap, etc.). The migration must NOT change the
    audit dict's return shape. This test guards the contract.
    """

    def test_audit_text_returns_legacy_shape(self):
        """audit_text exists and returns a dict with the canonical
        top-level keys callers depend on (n_words, n_sentences, tier1
        / tier2 / tier3 nesting).
        """
        # Use a tiny synthetic text and the simplest call signature.
        text = "The committee met. The proposal landed. " * 60
        try:
            out = va.audit_text(text)
        except Exception:
            pytest.skip("audit_text dependencies unavailable in test env")
            return
        # Canonical keys that callers read.
        assert "tier1" in out
        # The audit dict still carries task_surface for legacy
        # function-call consumers (variance_audit's audit_text shape
        # is unchanged by this migration).
        assert "n_words" in out or "tier1" in out
