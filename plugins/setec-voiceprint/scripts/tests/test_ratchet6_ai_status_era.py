#!/usr/bin/env python3
"""Ratchet 6: ai_status='pre_ai_human' + era='post_ai_widespread' -> warn,
for ANY corpus_role.

Per internal/2026-07-09-manifest-validator-ai-status-era-ratchet-spec.md.
Ratchet 6 is a NEW, additive check that does not touch the existing
Ratchet 4 (impostor + post_ai_widespread, no ai_status term). The two
compose: an impostor + pre_ai_human + post_ai_widespread entry trips both.

Five fixtures, one per spec-required case:
  (a) pre_ai_human + post_ai_widespread + identity_baseline
      -> Ratchet 6 fires.
  (b) unknown       + post_ai_widespread + identity_baseline
      -> clean; neither ratchet fires (this is what a careful acquirer
         tagging post-AI-era pieces `unknown` is expected to produce).
  (c) impostor + post_ai_widespread + ai_status:ai_assisted
      -> Ratchet 4 still fires (era warning); Ratchet 6 does NOT
         (ai_status != pre_ai_human) -> proves no regression to R4.
         (ai_assisted, not `mixed`: `mixed` carries its own separate
         ai_status consistency warning that would muddy the signal.)
  (d) identity_baseline + pre_ai_human + explicitly supplied
      post_ai_widespread era, matching acquire_manuscript.py when an
      operator combines its default --ai-status with
      --era post_ai_widespread -> Ratchet 6 fires.
  (e) impostor + pre_ai_human + post_ai_widespread
      -> BOTH Ratchet 4 (era) and Ratchet 6 (ai_status) fire -> proves
         the two compose rather than one masking the other.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from manifest_validator import validate_manifest  # type: ignore


def _impostor_block() -> dict:
    """Minimal impostor-required block so Ratchet 1 doesn't add
    unrelated errors that would muddy the ai_status/era signal."""
    return {
        "impostor_for": ["some_persona"],
        "register_match": "high",
        "topic_match": "medium",
        "consent_status": "fair_use_research",
        "acquired_via": "test_ratchet6",
    }


def _entries() -> list[dict]:
    return [
        {  # (a)
            "id": "a_baseline_pre_ai", "path": "a.txt",
            "use": ["voice_profile"], "corpus_role": "identity_baseline",
            "ai_status": "pre_ai_human", "era": "post_ai_widespread",
        },
        {  # (b)
            "id": "b_baseline_unknown", "path": "b.txt",
            "use": ["voice_profile"], "corpus_role": "identity_baseline",
            "ai_status": "unknown", "era": "post_ai_widespread",
        },
        {  # (c)
            "id": "c_impostor_ai_assisted", "path": "c.txt",
            "use": ["voice_impostor"], "corpus_role": "impostor",
            "ai_status": "ai_assisted", "era": "post_ai_widespread",
            **_impostor_block(),
        },
        {  # (d)
            "id": "d_manuscript_explicit_post_ai_era", "path": "d.txt",
            "use": ["voice_profile"], "corpus_role": "identity_baseline",
            "ai_status": "pre_ai_human", "era": "post_ai_widespread",
        },
        {  # (e)
            "id": "e_impostor_cofire", "path": "e.txt",
            "use": ["voice_impostor"], "corpus_role": "impostor",
            "ai_status": "pre_ai_human", "era": "post_ai_widespread",
            **_impostor_block(),
        },
    ]


def _validate() -> dict[str, list[dict]]:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "manifest.jsonl"
        path.write_text(
            "\n".join(json.dumps(e) for e in _entries()) + "\n",
            encoding="utf-8",
        )
        result = validate_manifest(path)
    by_id: dict[str, list[dict]] = {}
    for issue in result["issues"]:
        by_id.setdefault(issue.get("id"), []).append(issue)
    return by_id


def _warn_fields(issues: list[dict]) -> set[str]:
    return {i.get("field") for i in issues if i.get("severity") == "warning"}


def test_a_baseline_pre_ai_fires_ratchet6() -> None:
    fields = _warn_fields(_validate().get("a_baseline_pre_ai", []))
    assert "ai_status" in fields, (
        "Ratchet 6 should warn on identity_baseline + pre_ai_human + "
        f"post_ai_widespread; got warning fields {fields}."
    )


def test_b_baseline_unknown_is_clean() -> None:
    fields = _warn_fields(_validate().get("b_baseline_unknown", []))
    assert "ai_status" not in fields and "era" not in fields, (
        "ai_status=unknown + post_ai_widespread should trip neither "
        f"Ratchet 4 nor Ratchet 6; got warning fields {fields}."
    )


def test_c_impostor_ai_assisted_fires_ratchet4_only() -> None:
    fields = _warn_fields(_validate().get("c_impostor_ai_assisted", []))
    assert "era" in fields, (
        "Ratchet 4 (impostor + post_ai_widespread) must still fire; "
        f"got warning fields {fields}."
    )
    assert "ai_status" not in fields, (
        "Ratchet 6 must NOT fire when ai_status != pre_ai_human; "
        f"got warning fields {fields}."
    )


def test_d_manuscript_explicit_post_ai_era_fires_ratchet6() -> None:
    fields = _warn_fields(
        _validate().get("d_manuscript_explicit_post_ai_era", [])
    )
    assert "ai_status" in fields, (
        "An acquire_manuscript.py-shaped entry with an explicitly supplied "
        "post_ai_widespread era should warn under "
        f"Ratchet 6; got warning fields {fields}."
    )


def test_e_impostor_cofire_trips_both() -> None:
    fields = _warn_fields(_validate().get("e_impostor_cofire", []))
    assert "era" in fields and "ai_status" in fields, (
        "impostor + pre_ai_human + post_ai_widespread must trip BOTH "
        f"Ratchet 4 (era) and Ratchet 6 (ai_status); got fields {fields}."
    )


if __name__ == "__main__":
    test_a_baseline_pre_ai_fires_ratchet6()
    test_b_baseline_unknown_is_clean()
    test_c_impostor_ai_assisted_fires_ratchet4_only()
    test_d_manuscript_explicit_post_ai_era_fires_ratchet6()
    test_e_impostor_cofire_trips_both()
    print("ratchet 6: all 5 fixtures pass")
