#!/usr/bin/env python3
"""Tests for formulaicity_audit.py — the non-voice phraseological-texture profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import formulaicity_audit as fa  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_BODY = ("word " * 360)
SAMPLE = (
    "At the end of the day, we picked the low-hanging fruit in order to win.\n\n"
    + _BODY
)


def _audit(text, groups=None, is_custom=False):
    return fa.audit_formulaicity(text, groups or fa.BUILTIN_PHRASES,
                                 is_custom=is_custom)


def test_task_surface_registered():
    assert fa.TASK_SURFACE == "formulaicity"
    assert fa.TASK_SURFACE in VALID_TASK_SURFACES


def test_envelope_shape_validates():
    payload = fa.build_payload(_audit(SAMPLE), target_path="x.md",
                               word_count=fa.count_words(SAMPLE), available=True)
    assert payload["task_surface"] == "formulaicity"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    for key in ("total_hits", "density_per_1k", "by_group", "top_phrases"):
        assert key in payload["results"]


def test_no_verdict_keys():
    r = _audit(SAMPLE)
    for forbidden in ("band", "verdict", "compression", "smoothed"):
        assert forbidden not in r


def test_claim_license_refuses_ai_voice_quality():
    dn = fa._claim_license().does_not_license.lower()
    assert "ai" in dn and "voice" in dn and "quality" in dn


def test_builtin_phrase_match():
    r = _audit("At the end of the day, we picked the low-hanging fruit in order to win.")
    assert r["total_hits"] == 3
    assert r["distinct_phrases"] == 3


def test_case_insensitive():
    r = _audit("At The End Of The Day, things change.")
    assert r["total_hits"] == 1


def test_by_group_and_density():
    r = _audit("it's important to note that going forward we move the needle. " + _BODY)
    assert r["by_group"]["hedge_boilerplate"] >= 1
    assert r["by_group"]["corporate_cliche"] >= 2  # "going forward" + "move the needle"
    assert r["density_per_1k"] > 0


def test_custom_phrases_file(tmp_path):
    pf = tmp_path / "phrases.txt"
    pf.write_text("mygroup:zonk widget\nplain marker\n", encoding="utf-8")
    groups, is_custom = fa.load_phrases(str(pf))
    assert is_custom is True
    r = _audit("the zonk widget appeared and a plain marker too", groups,
               is_custom=True)
    assert r["custom_list"] is True
    assert r["total_hits"] == 2
    assert r["by_group"]["mygroup"] == 1


def test_no_false_match_on_substring():
    r = _audit("noteworthy footnotes everywhere", {"custom": ["note"]},
               is_custom=True)
    assert r["total_hits"] == 0


def test_too_short_unavailable(tmp_path):
    f = tmp_path / "short.md"
    f.write_text("at the end of the day, short.\n", encoding="utf-8")
    assert fa.main([str(f), "--json"]) == 0


def test_unavailable_payload_shape():
    payload = fa.build_payload({}, target_path="x.md", word_count=10,
                               available=False, warnings=["short"])
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None


def test_cli_emits_envelope(tmp_path, capsys):
    f = tmp_path / "doc.md"
    f.write_text(SAMPLE, encoding="utf-8")
    assert fa.main([str(f), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_surface"] == "formulaicity"
    assert payload["available"] is True


def test_deterministic():
    assert _audit(SAMPLE) == _audit(SAMPLE)
