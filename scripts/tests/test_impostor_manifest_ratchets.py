#!/usr/bin/env python3
"""Regression tests for the impostor-corpus manifest validator
extensions (1.14.3, per internal/2026-05-08-impostor-corpus-spec.md).

Five new ratchet rules:

  1. Impostor required fields — corpus_role: impostor entries must
     carry impostor_for, register_match, topic_match, consent_status,
     era, acquired_via. Missing → error.
  2. Persona-reference + cross-register warnings — an impostor's
     impostor_for should name personas that exist in the manifest's
     identity-baseline entries; high register_match should mean the
     impostor's register actually overlaps the target persona's.
  3. Consent-status redistribution ratchet — impostor + undocumented
     consent → warn.
  4. Post-AI-era warning — impostor + era: post_ai_widespread → warn.
  5. Identity-baseline era recommendation — entries with effective
     corpus_role: identity_baseline AND impostor-relevant `use` AND
     missing `era` → warn. Validation-only entries are exempt.

The synthetic 10-entry mixed-manifest fixture exercises each rule
with a passing and a failing case, plus the "validation-only entries
are exempt from the era warning" path.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from manifest_validator import (  # type: ignore
    ALLOWED_CONSENT_STATUS,
    ALLOWED_CORPUS_ROLE,
    ALLOWED_ERA,
    ALLOWED_REGISTER,
    ALLOWED_REGISTER_MATCH,
    ALLOWED_TOPIC_MATCH,
    ALLOWED_USE,
    KNOWN_FIELDS,
    validate_manifest,
)


FIXTURE = ROOT / "test_data" / "impostor_corpus" / "manifest.jsonl"


def _issues_by_id(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for issue in result["issues"]:
        eid = issue.get("id")
        if eid:
            out.setdefault(eid, []).append(issue)
    return out


# ---- Constants surface ------------------------------------


def test_new_constants_carry_expected_values() -> None:
    """If a future refactor regresses any of the new enum sets, the
    impostor-corpus tools that hardcode these values will silently
    misclassify. Pin the expected values."""
    assert ALLOWED_CORPUS_ROLE == {
        "identity_baseline", "impostor", "distractor", "adversarial",
    }
    assert ALLOWED_REGISTER_MATCH == {"high", "medium", "low"}
    assert ALLOWED_TOPIC_MATCH == {"high", "medium", "low"}
    assert ALLOWED_CONSENT_STATUS == {
        "public_record", "cc_licensed", "fair_use_research",
        "author_consent", "undocumented",
    }
    assert ALLOWED_ERA == {
        "pre_chatgpt", "pre_ai_widespread", "post_ai_widespread", "undated",
    }


def test_voice_impostor_added_to_allowed_use() -> None:
    assert "voice_impostor" in ALLOWED_USE


def test_literary_horror_added_to_allowed_register() -> None:
    assert "literary_horror" in ALLOWED_REGISTER


def test_impostor_fields_added_to_known_fields() -> None:
    new_fields = {
        "corpus_role", "impostor_for", "register_match", "topic_match",
        "consent_status", "era", "acquired_via", "content_hash",
    }
    assert new_fields <= KNOWN_FIELDS


# ---- Ratchet 1: impostor required fields --------------------


def test_ratchet_1_missing_impostor_fields_errors() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    incomplete = by_id.get("impostor_missing_fields", [])
    error_fields = {
        i.get("field") for i in incomplete if i.get("severity") == "error"
    }
    expected = {
        "impostor_for", "register_match", "topic_match",
        "consent_status", "era", "acquired_via",
    }
    assert expected <= error_fields, (
        f"Missing required-field errors. Expected fields {expected}; "
        f"got {error_fields}."
    )


def test_ratchet_1_full_impostor_metadata_passes() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    smith_clean = by_id.get("impostor_smith_clean", [])
    # No errors on impostor_smith_clean; it has the full block.
    errors = [i for i in smith_clean if i.get("severity") == "error"]
    assert errors == [], (
        f"impostor_smith_clean has full impostor metadata; "
        f"unexpected errors: {errors}"
    )


# ---- Ratchet 2: persona-reference + register-mismatch -------


def test_ratchet_2_unknown_persona_warns() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    unknown = by_id.get("impostor_unknown_persona", [])
    persona_warnings = [
        i for i in unknown
        if i.get("severity") == "warning"
        and i.get("field") == "impostor_for"
        and "nonexistent_persona" in i.get("message", "")
    ]
    assert persona_warnings, (
        "Expected a warning for impostor_for referencing an unknown "
        f"persona. Got: {unknown}"
    )


def test_ratchet_2_register_mismatch_warns_on_high_match() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    mismatch = by_id.get("impostor_register_mismatch", [])
    register_warnings = [
        i for i in mismatch
        if i.get("severity") == "warning"
        and i.get("field") == "register_match"
        and "academic_philosophy" in i.get("message", "")
        and "blog_essay" in i.get("message", "")
    ]
    assert register_warnings, (
        "Expected a warning for register_match='high' on an impostor "
        "whose register doesn't match any of the target persona's "
        f"registers. Got: {mismatch}"
    )


def test_ratchet_2_smith_high_match_passes() -> None:
    """impostor_smith_high_match has register: blog_essay and
    impostor_for: ['blog']; the 'blog' persona's identity baseline
    has register: blog_essay too. No register-match warning."""
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    smith_high = by_id.get("impostor_smith_high_match", [])
    register_warnings = [
        i for i in smith_high if i.get("field") == "register_match"
    ]
    assert register_warnings == []


# ---- Ratchet 3: consent-status redistribution ratchet ------


def test_ratchet_3_undocumented_consent_warns() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    undoc = by_id.get("impostor_undocumented", [])
    consent_warnings = [
        i for i in undoc
        if i.get("severity") == "warning"
        and i.get("field") == "consent_status"
        and "undocumented" in i.get("message", "")
    ]
    assert consent_warnings, (
        "Expected an undocumented-consent warning on the impostor. "
        f"Got: {undoc}"
    )


def test_ratchet_3_documented_consent_passes() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    smith_clean = by_id.get("impostor_smith_clean", [])
    consent_warnings = [
        i for i in smith_clean
        if i.get("field") == "consent_status"
    ]
    assert consent_warnings == []


# ---- Ratchet 4: post-AI-era warning ------------------------


def test_ratchet_4_post_ai_era_warns() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    post = by_id.get("impostor_post_ai", [])
    era_warnings = [
        i for i in post
        if i.get("severity") == "warning"
        and i.get("field") == "era"
        and "post_ai_widespread" in i.get("message", "")
    ]
    assert era_warnings, (
        "Expected a post-AI-era warning. Got: {post}"
    )


def test_ratchet_4_pre_chatgpt_era_passes() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    smith_clean = by_id.get("impostor_smith_clean", [])
    era_warnings = [
        i for i in smith_clean
        if i.get("field") == "era"
    ]
    assert era_warnings == []


# ---- Ratchet 5: identity-baseline era recommendation -------


def test_ratchet_5_baseline_no_era_warns_when_use_is_impostor_relevant() -> None:
    """`baseline_no_era` has use=['baseline','voice_profile'] which
    is impostor-relevant; era is missing → warn."""
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    baseline = by_id.get("baseline_no_era", [])
    era_warnings = [
        i for i in baseline
        if i.get("severity") == "warning"
        and i.get("field") == "era"
    ]
    assert era_warnings, (
        f"Expected era recommendation warning on baseline_no_era. "
        f"Got: {baseline}"
    )


def test_ratchet_5_validation_only_entry_does_not_warn_on_missing_era() -> None:
    """`validation_no_era_ok` has use=['validation'] only; era is
    missing but the ratchet should NOT fire because validation-only
    entries don't feed impostor calibration."""
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    val = by_id.get("validation_no_era_ok", [])
    era_warnings = [
        i for i in val
        if i.get("field") == "era"
    ]
    assert era_warnings == [], (
        f"validation-only entry should not get era warning. Got: {val}"
    )


def test_ratchet_5_baseline_with_era_passes() -> None:
    """`essay_blog_voice_first` has use=['baseline','voice_profile']
    AND era=pre_chatgpt — no warning."""
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    by_id = _issues_by_id(result)
    base = by_id.get("essay_blog_voice_first", [])
    era_warnings = [i for i in base if i.get("field") == "era"]
    assert era_warnings == []


# ---- Summary block ----------------------------------------


def test_summary_block_includes_new_buckets() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    result = validate_manifest(FIXTURE)
    summary = result["summary"]
    assert "by_corpus_role" in summary
    assert "by_era" in summary
    assert "by_consent_status" in summary
    assert "by_register_match" in summary
    # Concrete counts on the fixture: 7 impostor + 3 identity_baseline
    # (where identity_baseline includes the validation-only entry
    # that defaults to identity_baseline when corpus_role is absent).
    assert summary["by_corpus_role"]["impostor"] == 7
    assert summary["by_corpus_role"]["identity_baseline"] == 3
    # by_era: 6 entries with era=pre_chatgpt + 1 post_ai_widespread.
    # baseline_no_era and impostor_missing_fields and validation_no_
    # era_ok lack era so they're not bucketed.
    assert summary["by_era"]["pre_chatgpt"] == 6
    assert summary["by_era"]["post_ai_widespread"] == 1
    # by_consent_status: 3 fair_use + 2 public_record + 1 undocumented.
    # impostor_missing_fields lacks consent_status.
    assert summary["by_consent_status"]["fair_use_research"] == 3
    assert summary["by_consent_status"]["public_record"] == 2
    assert summary["by_consent_status"]["undocumented"] == 1


def test_render_report_includes_new_summary_lines() -> None:
    if not FIXTURE.exists():
        if pytest is not None:
            pytest.skip("Impostor manifest fixture not available")
        return
    from manifest_validator import render_report  # type: ignore
    result = validate_manifest(FIXTURE)
    md = render_report(result)
    assert "By corpus_role" in md
    assert "By era" in md
    assert "By consent_status" in md
    assert "By register_match" in md
