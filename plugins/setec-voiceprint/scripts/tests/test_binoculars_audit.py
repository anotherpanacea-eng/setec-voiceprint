"""Tests for ``binoculars_audit.py``.

Pin the contract: audit shape, score computation, edge cases (tokenizer
mismatch, near-zero observer, too-short target, scorer==observer),
verdict banding, envelope schema, markdown rendering, CLI exit codes.
No real model loads — stub backends mock the SurprisalBackend interface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
sys.path.insert(0, str(_SCRIPTS))

import binoculars_audit as bin_audit  # noqa: E402


# ============================================================
# Stub backend
# ============================================================


class StubBackend:
    """Mocks SurprisalBackend's audit-facing surface. The audit pulls
    ``model_id``, ``revision``, ``identifier_block()``, and a surprisal
    series (via score_fn). We don't implement ``score_text`` here — tests
    inject the series via ``score_fn``."""

    def __init__(self, model_id: str, revision: str | None = None, alias: str | None = None):
        self.model_id = model_id
        self.revision = revision
        self._alias = alias or model_id

    def identifier_block(self):
        return {
            "id": self.model_id,
            "revision": self.revision,
            "alias": self._alias,
            "deterministic_mode": True,
            "method": "transformers-causal-lm",
            "dtype_requested": "auto",
            "dtype_loaded": "fp32",
        }


def _series(value: float, n: int = 100) -> list[float]:
    return [value] * n


def _score_fn_factory(per_model_series: dict[str, list[float]]):
    """Return a score_fn that looks up series by model_id."""
    def score(backend, text):
        return per_model_series[backend.model_id]
    return score


# ============================================================
# Audit shape + ratio computation
# ============================================================


def test_audit_basic_shape():
    scorer = StubBackend("scorer-model")
    observer = StubBackend("observer-model")
    score_fn = _score_fn_factory({
        "scorer-model": _series(3.0, 100),
        "observer-model": _series(4.0, 100),
    })
    result = bin_audit.audit("text" * 200, scorer=scorer, observer=observer, score_fn=score_fn)
    assert result["scorer"]["model_id"] == "scorer-model"
    assert result["observer"]["model_id"] == "observer-model"
    assert result["scorer_log_perplexity_bits"] == 3.0
    assert result["observer_log_perplexity_bits"] == 4.0
    assert abs(result["perplexity_ratio"] - 0.75) < 1e-6
    assert result["score_version"] == "perplexity_ratio_v1"
    assert result["scorer_series_length"] == 100
    assert result["observer_series_length"] == 100


def test_audit_identical_series_gives_ratio_one():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(2.5, 100),
        "b": _series(2.5, 100),
    })
    result = bin_audit.audit("x", scorer=scorer, observer=observer, score_fn=score_fn)
    assert result["perplexity_ratio"] == 1.0


def test_audit_low_scorer_high_observer_gives_ai_likely():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(2.0, 100),
        "b": _series(4.0, 100),
    })
    result = bin_audit.audit(
        "x", scorer=scorer, observer=observer, score_fn=score_fn,
        threshold_low=0.9, threshold_high=1.1,
    )
    assert result["verdict_band"] == "ai_likely"


def test_audit_high_scorer_low_observer_gives_human_likely():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(4.0, 100),
        "b": _series(2.0, 100),
    })
    result = bin_audit.audit(
        "x", scorer=scorer, observer=observer, score_fn=score_fn,
        threshold_low=0.9, threshold_high=1.1,
    )
    assert result["verdict_band"] == "human_likely"


def test_audit_near_identical_gives_indeterminate():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(3.0, 100),
        "b": _series(3.01, 100),
    })
    result = bin_audit.audit(
        "x", scorer=scorer, observer=observer, score_fn=score_fn,
        threshold_low=0.9, threshold_high=1.1,
    )
    assert result["verdict_band"] == "indeterminate"


# ============================================================
# Caveats
# ============================================================


def test_audit_flags_scorer_equals_observer():
    same = StubBackend("samemodel")
    score_fn = _score_fn_factory({"samemodel": _series(3.0, 100)})
    result = bin_audit.audit("x", scorer=same, observer=same, score_fn=score_fn)
    assert "scorer_equals_observer" in result["caveats"]


def test_audit_flags_tokenizer_mismatch():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(3.0, 100),
        "b": _series(4.0, 120),
    })
    result = bin_audit.audit("x", scorer=scorer, observer=observer, score_fn=score_fn)
    assert any(c.startswith("tokenizer_mismatch") for c in result["caveats"])
    assert result["scorer_series_length"] == 100
    assert result["observer_series_length"] == 120
    assert result["perplexity_ratio"] == 0.75


def test_audit_flags_target_too_short():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(3.0, 30),
        "b": _series(4.0, 30),
    })
    result = bin_audit.audit("x", scorer=scorer, observer=observer, score_fn=score_fn)
    assert "target_too_short_for_stable_estimate" in result["caveats"]


def test_audit_flags_observer_near_zero():
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(3.0, 100),
        "b": _series(0.0, 100),
    })
    result = bin_audit.audit("x", scorer=scorer, observer=observer, score_fn=score_fn)
    assert "observer_perplexity_near_zero" in result["caveats"]
    assert result["perplexity_ratio"] is None
    assert result["verdict_band"] == "unavailable"


# ============================================================
# Calibration discipline (PR #110 P1 review)
# ============================================================


def test_audit_default_thresholds_produce_uncalibrated_band():
    """Without operator-supplied thresholds, the verdict band must be
    'uncalibrated' and a caveat must fire. Hard-coding numeric defaults
    would violate the framework rule against shipping thresholded claims
    without calibration."""
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(2.0, 100),
        "b": _series(4.0, 100),
    })
    result = bin_audit.audit("x", scorer=scorer, observer=observer, score_fn=score_fn)
    assert result["verdict_band"] == "uncalibrated"
    assert "no_calibrated_thresholds_supplied" in result["caveats"]
    assert result["perplexity_ratio"] == 0.5  # raw score still reported


def test_audit_operator_supplied_thresholds_fire_normal_bands_with_caveat():
    """When the operator supplies thresholds explicitly, the verdict bands
    are computed normally, but a caveat fires noting the thresholds are
    operator-supplied (not framework-calibrated)."""
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(2.0, 100),
        "b": _series(4.0, 100),
    })
    result = bin_audit.audit(
        "x", scorer=scorer, observer=observer, score_fn=score_fn,
        threshold_low=0.9, threshold_high=1.1,
    )
    assert result["verdict_band"] == "ai_likely"
    assert "thresholds_operator_supplied_not_framework_calibrated" in result["caveats"]


def test_audit_only_low_threshold_supplied_still_uncalibrated():
    """Partial threshold specification (only one of low/high) is still
    uncalibrated — both are required."""
    scorer = StubBackend("a")
    observer = StubBackend("b")
    score_fn = _score_fn_factory({
        "a": _series(2.0, 100),
        "b": _series(4.0, 100),
    })
    result = bin_audit.audit(
        "x", scorer=scorer, observer=observer, score_fn=score_fn,
        threshold_low=0.9, threshold_high=None,
    )
    assert result["verdict_band"] == "uncalibrated"


def test_band_none_thresholds_return_uncalibrated():
    assert bin_audit._band(0.5, low=None, high=None) == "uncalibrated"
    assert bin_audit._band(0.5, low=0.9, high=None) == "uncalibrated"
    assert bin_audit._band(0.5, low=None, high=1.1) == "uncalibrated"


def test_default_threshold_constants_are_none():
    """Pin the module-level defaults so accidental reintroduction of
    numeric defaults is caught."""
    assert bin_audit.DEFAULT_THRESHOLD_LOW is None
    assert bin_audit.DEFAULT_THRESHOLD_HIGH is None


def test_does_not_license_text_names_uncalibrated_default():
    """The default claim_license text must surface the uncalibrated-default
    discipline so consumers reading the evidence pack know what 'verdict
    band: uncalibrated' means."""
    assert "uncalibrated" in bin_audit.DEFAULT_DOES_NOT_LICENSE.lower()


# ============================================================
# Envelope schema
# ============================================================


def _basic_results() -> dict:
    return bin_audit.audit(
        "text",
        scorer=StubBackend("scorer-model"),
        observer=StubBackend("observer-model"),
        score_fn=_score_fn_factory({
            "scorer-model": _series(3.0, 100),
            "observer-model": _series(4.0, 100),
        }),
    )


def test_envelope_has_required_fields():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "binoculars_discrimination"
    assert envelope["tool"] == "binoculars_audit"
    assert envelope["available"] is True
    assert envelope["claim_license"]["task_surface"] == "binoculars_discrimination"
    assert envelope["target"]["words"] == 500


def test_envelope_propagates_caveats():
    results = _basic_results()
    results["caveats"] = ["test_caveat_1", "test_caveat_2"]
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    assert "test_caveat_1" in envelope["warnings"]
    assert "test_caveat_2" in envelope["warnings"]
    assert "test_caveat_1" in envelope["claim_license"]["additional_caveats"]


def test_envelope_includes_hans_reference():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    refs = envelope["claim_license"]["references"]
    assert any("Hans et al. 2024" in r for r in refs)


def test_envelope_operator_license_override():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
        licenses_text="custom licenses text",
        does_not_license_text="custom does-not text",
    )
    assert envelope["claim_license"]["licenses"] == "custom licenses text"
    assert envelope["claim_license"]["does_not_license"] == "custom does-not text"


def test_envelope_comparison_set_records_model_pair():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    cs = envelope["claim_license"]["comparison_set"]
    assert cs["scorer_model"] == "scorer-model"
    assert cs["observer_model"] == "observer-model"
    assert cs["score_version"] == "perplexity_ratio_v1"


# ============================================================
# Markdown rendering
# ============================================================


def test_markdown_has_expected_sections():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    md = bin_audit.render_markdown(envelope)
    assert "# Binoculars Audit (Perplexity Ratio v1)" in md
    assert "## Score" in md
    assert "## Caveats" in md
    assert "## Claim license" in md
    assert "## Provenance" in md


def test_markdown_renders_model_ids():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    md = bin_audit.render_markdown(envelope)
    assert "scorer-model" in md
    assert "observer-model" in md


def test_markdown_renders_verdict_band():
    results = _basic_results()
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    md = bin_audit.render_markdown(envelope)
    assert "Verdict band" in md
    assert results["verdict_band"] in md


def test_markdown_handles_null_ratio():
    results = _basic_results()
    results["perplexity_ratio"] = None
    results["verdict_band"] = "unavailable"
    envelope = bin_audit.compose_envelope(
        target_path=Path("/tmp/dummy.txt"),
        target_words=500,
        results=results,
    )
    md = bin_audit.render_markdown(envelope)
    assert "(unavailable)" in md


# ============================================================
# Helpers
# ============================================================


def test_count_words_matches_variance_audit_convention():
    text = "Hello, world! This is a test."
    assert bin_audit.count_words(text) == 6


def test_count_words_strips_punctuation():
    text = "It's a 'test' string."
    # variance_audit's regex is [A-Za-z']+ lowercased
    assert bin_audit.count_words(text) == 4


def test_band_below_low_is_ai_likely():
    assert bin_audit._band(0.5, low=0.9, high=1.1) == "ai_likely"


def test_band_above_high_is_human_likely():
    assert bin_audit._band(1.5, low=0.9, high=1.1) == "human_likely"


def test_band_between_is_indeterminate():
    assert bin_audit._band(1.0, low=0.9, high=1.1) == "indeterminate"


def test_band_none_is_unavailable():
    assert bin_audit._band(None, low=0.9, high=1.1) == "unavailable"


# ============================================================
# CLI smoke
# ============================================================


def test_cli_returns_nonzero_on_missing_target(tmp_path):
    rc = bin_audit.main([str(tmp_path / "nonexistent.txt")])
    assert rc == 1


def test_cli_returns_three_on_score_text_failure(monkeypatch, tmp_path):
    """SurprisalBackendError raised by score_text() inside audit() must be
    caught by main() and surface as rc=3, same as a construction failure.
    Regression test for PR #110 P2 review comment — scoring-time errors
    were previously escaping through a Python traceback."""
    target = tmp_path / "target.txt"
    target.write_text("the cat sat on the mat " * 50)

    def stub_init(self, *, model_id, revision=None, dtype="auto"):
        self.model_id = model_id
        self.revision = revision
        self._alias = model_id
        self.deterministic = True
        self.dtype = dtype
        self._resolved_dtype_label = "fp32"

    def failing_score(self, text, *, return_top_k=0):
        raise bin_audit.SurprisalBackendError("simulated inference failure")

    monkeypatch.setattr(bin_audit.SurprisalBackend, "__init__", stub_init)
    monkeypatch.setattr(bin_audit.SurprisalBackend, "score_text", failing_score)
    rc = bin_audit.main([str(target)])
    assert rc == 3


def test_cli_returns_three_on_backend_failure(monkeypatch, tmp_path):
    """When SurprisalBackend construction fails, exit code 3 (per convention)."""
    target = tmp_path / "target.txt"
    target.write_text("some text to audit " * 50)

    def failing_init(self, *args, **kwargs):
        raise bin_audit.SurprisalBackendError("simulated load failure")

    monkeypatch.setattr(bin_audit.SurprisalBackend, "__init__", failing_init)
    rc = bin_audit.main([str(target)])
    assert rc == 3


def test_cli_end_to_end_with_stubbed_backend(monkeypatch, tmp_path):
    """Full CLI run with backends + score_text stubbed."""
    target = tmp_path / "target.txt"
    target.write_text("the cat sat on the mat " * 50)

    def stub_init(self, *, model_id, revision=None, dtype="auto"):
        self.model_id = model_id
        self.revision = revision
        self._alias = model_id
        self.deterministic = True
        self.dtype = dtype
        self._resolved_dtype_label = "fp32"

    def stub_score_text(self, text, *, return_top_k=0):
        if self.model_id == bin_audit.DEFAULT_SCORER:
            return _series(2.0, 100)
        return _series(4.0, 100)

    monkeypatch.setattr(bin_audit.SurprisalBackend, "__init__", stub_init)
    monkeypatch.setattr(bin_audit.SurprisalBackend, "score_text", stub_score_text)

    out_json = tmp_path / "result.json"
    out_md = tmp_path / "result.md"
    rc = bin_audit.main([
        str(target),
        "--out", str(out_json),
        "--out-md", str(out_md),
        "--threshold-low", "0.9",
        "--threshold-high", "1.1",
    ])
    assert rc == 0
    assert out_json.exists()
    assert out_md.exists()
    envelope = json.loads(out_json.read_text())
    assert envelope["task_surface"] == "binoculars_discrimination"
    assert envelope["results"]["perplexity_ratio"] == 0.5
    assert envelope["results"]["verdict_band"] == "ai_likely"
