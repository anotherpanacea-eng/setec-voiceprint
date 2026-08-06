#!/usr/bin/env python3
"""Tests for setec/consumer_client.py — the shared client vendored into both
consumer repos (`setec-consumer-client-contract.md` C1.1 / C1.2 / C2.2).

Pins:
  * `parse_version` against the closed `semver_parser_cases.json` fixture —
    every row's `result` or `error` (exactly one non-null) must match.
  * `meets_floor` respects SemVer prerelease precedence: a prerelease build
    of a release does NOT satisfy a floor equal to that release
    (`1.129.0-rc.1 < 1.129.0` — the spec's own example).
  * `classify_warning` against the closed `warning_classifier_coverage.json`
    fixture, and the C1.2 regression: an UNMATCHED warning now tiers
    'reliability', never the pre-C1.2 'cosmetic' default.
  * `tier_envelope` end-to-end tiering of a schema-1.0 envelope.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setec import consumer_client as cc  # type: ignore  # noqa: E402

FIXTURES_DIR = ROOT.parent / "references" / "contract_fixtures"


def _load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


# ---------- 1.1 version parser --------------------------------------------

SEMVER_CASES = _load("semver_parser_cases.json")


@pytest.mark.parametrize("case", SEMVER_CASES, ids=lambda c: c["input"] or "<empty>")
def test_semver_parser_cases_fixture(case):
    is_error = case["error"] is not None
    is_result = case["result"] is not None
    assert is_error != is_result, f"exactly one of result/error must be non-null: {case}"
    if is_result:
        parsed = cc.parse_version(case["input"])
        assert parsed == case["result"]
    else:
        with pytest.raises(cc.VersionParseError):
            cc.parse_version(case["input"])


def test_semver_fixture_covers_required_categories():
    """The fixture covers 1/2/3/4-component release inputs, -rc.1, +build,
    empty, nonnumeric, leading-zero, and malformed-identifier cases (spec
    C1.1's exact coverage list)."""
    inputs = {c["input"] for c in SEMVER_CASES}
    assert "1" in inputs and "1.2" in inputs and "1.2.3" in inputs
    assert any(i.count(".") == 3 for i in inputs), "no 4-component input covered"
    assert "" in inputs
    assert any("-rc" in i for i in inputs)
    assert any("+build" in i for i in inputs)
    assert any(c["error"] is not None and "leading zero" in c["error"] for c in SEMVER_CASES)
    assert any(
        c["error"] is not None and "non-numeric" in c["error"] for c in SEMVER_CASES
    )
    assert any(
        c["error"] is not None and "malformed" in c["error"] for c in SEMVER_CASES
    )


def test_no_silent_floor_drop():
    """The old `_parse_version("garbage") == ()` silent-partial-parse path is
    gone: an unparseable version always raises, never returns a falsy/partial
    tuple that a floor comparison could misread as satisfied."""
    with pytest.raises(cc.VersionParseError):
        cc.parse_version("garbage")
    with pytest.raises(cc.VersionParseError):
        cc.parse_version("")


def test_prerelease_does_not_satisfy_numerically_equal_stable_floor():
    """The spec's own worked example: 1.129.0-rc.1 < 1.129.0."""
    assert cc.meets_floor("1.129.0", (1, 129, 0)) is True
    assert cc.meets_floor("1.129.0-rc.1", (1, 129, 0)) is False
    assert cc.meets_floor("1.129.1", (1, 129, 0)) is True
    assert cc.meets_floor("1.128.9", (1, 129, 0)) is False


def test_prerelease_precedence_chain_is_monotonic():
    """SemVer 11.4 worked chain: alpha < alpha.1 < alpha.beta < beta <
    beta.2 < beta.11 < rc.1 < (no prerelease)."""
    chain = [
        "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
        "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
    ]
    keys = [cc.version_precedence_key(cc.parse_version(v)) for v in chain]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)


def test_build_location_refuses_non_string_plugin_version(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / "scripts").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "setec-voiceprint", "version": 129}),
        encoding="utf-8",
    )
    with pytest.raises(cc.SetecDiscoveryError, match="non-string version"):
        cc.build_location(plugin, "test", (1, 0, 0))


# ---------- 1.2 warning classifier -----------------------------------------

CLASSIFIER_COVERAGE = _load("warning_classifier_coverage.json")
PRODUCER_EMISSIONS = _load("warning_producer_emissions.json")


@pytest.mark.parametrize(
    "row", CLASSIFIER_COVERAGE, ids=lambda r: r["case_id"],
)
def test_warning_classifier_coverage_fixture(row):
    assert cc.classify_warning(row["text"]) == row["expected_consumer_tier"]
    assert row["producer_disposition"] in ("live_emission", "classifier_only")


def test_unmatched_warning_fails_upward_to_reliability():
    assert cc.classify_warning(
        "a completely novel sentence with no pattern"
    ) == "reliability"


def test_classifier_coverage_has_all_eleven_branches_plus_unmatched():
    assert len(cc.RELIABILITY_PATTERNS) == 11
    matched_rows = CLASSIFIER_COVERAGE[:-1]
    unmatched = CLASSIFIER_COVERAGE[-1]
    assert len(matched_rows) == 11
    assert all(any(p.search(row["text"]) for p in cc.RELIABILITY_PATTERNS)
               for row in matched_rows)
    assert all(any(p.search(row["text"]) for row in matched_rows)
               for p in cc.RELIABILITY_PATTERNS)
    assert not any(p.search(unmatched["text"]) for p in cc.RELIABILITY_PATTERNS)


def test_every_live_emission_coverage_row_is_bound_to_a_real_emission():
    emission_keys = {(r["case_id"], r["text"]) for r in PRODUCER_EMISSIONS}
    for row in CLASSIFIER_COVERAGE:
        if row["producer_disposition"] == "live_emission":
            assert (row["case_id"], row["text"]) in emission_keys, row

    for row in PRODUCER_EMISSIONS:
        assert cc.classify_warning(row["text"]) == "reliability"
        assert isinstance(row["producer_test"], str) and "::" in row["producer_test"]


# ---------- schema-1.0 envelope tiering ------------------------------------

def _base_envelope(**overrides):
    env = {
        "schema_version": "1.0",
        "task_surface": "smoothing_diagnosis",
        "tool": "example",
        "version": "1.0",
        "available": True,
        "target": {},
        "baseline": None,
        "results": {},
        "claim_license": {"task_surface": "smoothing_diagnosis"},
        "claim_license_rendered": "## What this result licenses",
        "warnings": [],
        "ai_status": None,
    }
    env.update(overrides)
    return env


def test_tier_envelope_success_classifies_warnings():
    env = _base_envelope(warnings=["text too short", "an unrelated cosmetic-sounding note"])
    result = cc.tier_envelope(env)
    assert result.available is True
    assert result.blocking_warnings == []
    assert "text too short" in result.reliability_warnings
    # C1.2: the unmatched note ALSO tiers reliability now.
    assert "an unrelated cosmetic-sounding note" in result.reliability_warnings
    assert result.cosmetic_warnings == []


def test_tier_envelope_error_text_too_short_is_reliability():
    env = _base_envelope(
        available=False, claim_license=None, claim_license_rendered=None,
        reason="text too short", reason_category="text_too_short",
    )
    result = cc.tier_envelope(env)
    assert result.blocking_warnings == []
    assert result.reliability_warnings == ["text too short"]


def test_tier_envelope_error_unknown_category_fails_blocking():
    env = _base_envelope(
        available=False, claim_license=None, claim_license_rendered=None,
        reason="a brand new producer failure mode", reason_category="something_new",
    )
    result = cc.tier_envelope(env)
    assert result.blocking_warnings == ["a brand new producer failure mode"]
    assert result.reliability_warnings == []


def test_tier_envelope_rejects_wrong_schema_version():
    env = _base_envelope(schema_version="0.9")
    with pytest.raises(cc.SetecRunnerError):
        cc.tier_envelope(env)


@pytest.mark.parametrize("missing", ["results", "ai_status"])
def test_tier_envelope_rejects_missing_required_key(missing):
    env = _base_envelope()
    del env[missing]
    with pytest.raises(cc.SetecRunnerError):
        cc.tier_envelope(env)


@pytest.mark.parametrize("missing", ["reason", "reason_category"])
def test_tier_envelope_rejects_missing_error_key(missing):
    env = _base_envelope(
        available=False,
        reason="producer refused",
        reason_category="policy_refused",
    )
    del env[missing]
    with pytest.raises(cc.SetecRunnerError):
        cc.tier_envelope(env)


@pytest.mark.parametrize("field,bad_value", [
    ("available", "false"),
    ("available", 0),
    ("warnings", "not-a-list"),
    ("warnings", ["valid", 1]),
    ("target", []),
    ("baseline", []),
    ("results", []),
    ("claim_license", []),
    ("task_surface", 1),
    ("ai_status", 1),
    ("claim_license_rendered", 1),
])
def test_tier_envelope_rejects_mistyped_common_fields(field, bad_value):
    env = _base_envelope()
    env[field] = bad_value
    with pytest.raises(cc.SetecRunnerError):
        cc.tier_envelope(env)


def test_tier_envelope_rejects_nonmapping_top_level():
    with pytest.raises(cc.SetecRunnerError, match="mapping"):
        cc.tier_envelope([])


@pytest.mark.parametrize("field", ["reason", "reason_category"])
def test_tier_envelope_rejects_mistyped_error_fields(field):
    env = _base_envelope(
        available=False,
        reason="producer refused",
        reason_category="policy_refused",
    )
    env[field] = 1
    with pytest.raises(cc.SetecRunnerError):
        cc.tier_envelope(env)
