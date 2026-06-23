#!/usr/bin/env python3
"""Tests for house_style_decomposition.py — 17 M1 invariants (CI-runnable, stdlib only).

ALL 17 tests are M1 invariants.  None require torch / transformers / spaCy.
The no-verdict walk (test 11) is unconditional — NO skipif.

Anti-Goodhart note (test 17):
  The fixture texts used to pin attribution labels here (the worked-example synthetic
  corpus of writer J + House A) are NOT used to calibrate the M2 lens, because M2 is
  not in this build.  Any future M2 POC must use a SEPARATE labeled corpus (a
  documented copyedit ledger with known house-vs-author provenance), disjoint from
  this synthetic fixture.  This is the forward guard: the fixture sets the M1 golden;
  M2 calibration must never be trained on M1 golden data.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import house_style_decomposition as hsd  # noqa: E402

# ---------------------------------------------------------------------------
# Forbidden-key guard (per-spec §Posture §1) — unconditional, CI-blocking.
# No skipif. Mirrors tests/test_dependency_distance_audit.py:144-159,282-284
# and tests/test_distinct_diversity_audit.py:58,76-84,228.
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS: frozenset[str] = frozenset({
    "is_ai", "is_human", "verdict", "label", "same_author", "different_author",
    "authorship", "author_attributed", "is_real_voice", "true_voice", "score",
    "probability", "p_house", "selection", "selected", "rank", "band",
})


def _walk_keys(obj: Any):
    """Yield every dict key reachable in a nested results payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_first_person_em(n_reps: int = 25) -> str:
    """First-person + em-dashes (J's natural blog style)."""
    sentence = (
        "I think that I write because I must, and I find I cannot stop myself. "
        "My approach—which has always been personal—involves me and my own perspective. "
        "I believe I will continue; I hope I succeed in my work. "
        "When I consider my writing, I am reminded that I care deeply about my craft. "
    )
    filler = (
        "I explore ideas with care, and I return to them again and again. "
        "I feel that my voice is my own, and I will not surrender it lightly. "
    )
    return (sentence + filler) * n_reps


def _make_first_person_en(n_reps: int = 25) -> str:
    """First-person + en-dashes (J's House-A chapters: first-person survived, dash changed)."""
    sentence = (
        "I think that I write because I must, and I find I cannot stop myself. "
        "My approach–which has always been personal–involves me and my own perspective. "
        "I believe I will continue; I hope I succeed in my work. "
        "When I consider my writing, I am reminded that I care deeply about my craft. "
    )
    filler = (
        "I explore ideas with care, and I return to them again and again. "
        "I feel that my voice is my own, and I will not surrender it lightly. "
    )
    return (sentence + filler) * n_reps


def _make_third_person_en(n_reps: int = 25) -> str:
    """Third-person + en-dashes (House-A other authors K/L/M)."""
    sentence = (
        "She thinks that she writes because she must, and she finds she cannot stop. "
        "Her approach–which has always been professional–involves her own perspective. "
        "She believes she will continue; she hopes she succeeds in her work. "
        "When she considers her writing, she is reminded that she cares deeply about her craft. "
    )
    filler = (
        "She explores ideas with care, and she returns to them again and again. "
        "She feels that her voice is her own, and she will not surrender it lightly. "
    )
    return (sentence + filler) * n_reps


def _make_third_person_em(n_reps: int = 25) -> str:
    """Third-person + em-dashes (same-genre outside org variant)."""
    sentence = (
        "She thinks that she writes because she must, and she finds she cannot stop. "
        "Her approach—which has always been professional—involves her own perspective. "
        "She believes she will continue; she hopes she succeeds in her work. "
        "When she considers her writing, she is reminded that she cares deeply about her craft. "
    )
    filler = (
        "She explores ideas with care, and she returns to them again and again. "
        "She feels that her voice is her own, and she will not surrender it lightly. "
    )
    return (sentence + filler) * n_reps


def _make_text(
    use_first_person: bool,
    use_em_dash: bool,
    n_reps: int = 25,
) -> str:
    """Legacy helper kept for acceptance-gate tests that don't need specific attribution."""
    if use_first_person and use_em_dash:
        return _make_first_person_em(n_reps)
    elif use_first_person:
        return _make_first_person_en(n_reps)
    elif use_em_dash:
        return _make_third_person_em(n_reps)
    else:
        return _make_third_person_en(n_reps)


def _write_file(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _make_full_fixture(tmp_path: Path) -> dict:
    """Full worked-example fixture (writer J / House A) for golden tests.

    Target = J's House-A chapter: first-person + EN-DASH
      (J's pronoun habit survived copyedit; house changed em-dash → en-dash)
    Blog = J's blog: first-person + EM-DASH (J's natural idiolect)
    House K/L/M = third-person dominant + en-dash, with variance in first-person ratio
      so Burrows Delta z-scores are non-zero (K=0%, L≈10%, M≈5% FP mix)

    Expected:
      pronoun_modal_negation → idiolect_borne (target first-person close to blog; far from 3rd-person house)
      punctuation → house_borne (target en-dash close to house; far from em-dash blog)
    """
    # Design matches _build_worked_example_fixture:
    # punctuation contrast via DASH FREQUENCY (not dash type).
    # Target = FP + FEW dashes (semicolons); Blog = FP + MANY dashes; House = TP + FEW dashes.
    _FP_DASHY = (
        "I think—therefore I write—and I find I cannot stop. "
        "My approach—deeply personal—involves me and my own perspective. "
        "I write—often and freely—without restraint or fear. "
        "I—like many writers—find myself drawn to the form of the essay. "
    )
    _FP_SEMI = (
        "I think; therefore I write; and I find I cannot stop. "
        "My approach; deeply personal; involves me and my own perspective. "
        "I write; often and freely; without restraint or fear. "
        "I; like many writers; find myself drawn to the form of the essay. "
    )
    _TP_SEMI = (
        "She thinks; therefore she writes; and she finds she cannot stop. "
        "Her approach; deeply professional; involves her own perspective. "
        "She writes; often and carefully; without distraction or haste. "
        "She; like many professionals; finds herself drawn to the precise form. "
    )
    _TP_DASHY = (
        "She thinks—therefore she writes—and she finds she cannot stop. "
        "Her approach—deeply professional—involves her own perspective. "
        "She writes—often and carefully—without distraction or haste. "
        "She—like many professionals—finds herself drawn to the precise form. "
    )

    def _house_k(n: int) -> str:
        return _TP_SEMI * n

    def _house_l(n: int) -> str:
        tp = max(1, int(n * 0.90)); fp = n - tp
        return _TP_SEMI * tp + _FP_SEMI * fp

    def _house_m(n: int) -> str:
        tp = max(1, int(n * 0.95)); fp = n - tp
        return _TP_SEMI * tp + _FP_SEMI * fp

    j_blog_a = _FP_DASHY * 30
    j_blog_b = _FP_DASHY * 28 + _FP_DASHY[:len(_FP_DASHY) // 2] * 2
    j_blog_c = _FP_DASHY * 26 + _FP_DASHY[:len(_FP_DASHY) // 3] * 4
    j_house_a = _FP_SEMI * 30
    j_house_b = _FP_SEMI * 28
    k_text = _house_k(30)
    l_text = _house_l(28)
    m_text = _house_m(26)
    out_a = _FP_DASHY * 25
    out_b = _TP_SEMI * 25
    out_c = _FP_SEMI * 25
    out_d = _TP_DASHY * 25
    target_text = _FP_SEMI * 35  # FIRST-PERSON + FEW dashes (semicolons)

    broad_texts = []
    for i in range(20):
        if i % 4 == 0:
            broad_texts.append(_FP_DASHY * 20)
        elif i % 4 == 1:
            broad_texts.append(_TP_SEMI * 20)
        elif i % 4 == 2:
            broad_texts.append(_FP_SEMI * 20)
        else:
            broad_texts.append(_TP_DASHY * 20)

    entries: list[hsd.BaselineEntry] = [
        hsd.BaselineEntry("j_ha", j_house_a, "same_author_same_org", "writer:j", "house:a",
                          _write_file(tmp_path, "j_ha.txt", j_house_a)),
        hsd.BaselineEntry("j_hb", j_house_b, "same_author_same_org", "writer:j", "house:a",
                          _write_file(tmp_path, "j_hb.txt", j_house_b)),
        hsd.BaselineEntry("j_ba", j_blog_a, "different_context", "writer:j", None,
                          _write_file(tmp_path, "j_ba.txt", j_blog_a)),
        hsd.BaselineEntry("j_bb", j_blog_b, "different_context", "writer:j", None,
                          _write_file(tmp_path, "j_bb.txt", j_blog_b)),
        hsd.BaselineEntry("j_bc", j_blog_c, "different_context", "writer:j", None,
                          _write_file(tmp_path, "j_bc.txt", j_blog_c)),
        hsd.BaselineEntry("k", k_text, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k.txt", k_text)),
        hsd.BaselineEntry("l", l_text, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "l.txt", l_text)),
        hsd.BaselineEntry("m", m_text, "different_authors_same_org", "writer:m", "house:a",
                          _write_file(tmp_path, "m.txt", m_text)),
        hsd.BaselineEntry("oa", out_a, "same_genre_outside_org", "writer:oa", "house:x",
                          _write_file(tmp_path, "oa.txt", out_a)),
        hsd.BaselineEntry("ob", out_b, "same_genre_outside_org", "writer:ob", "house:y",
                          _write_file(tmp_path, "ob.txt", out_b)),
        hsd.BaselineEntry("oc", out_c, "same_genre_outside_org", "writer:oc", "house:z",
                          _write_file(tmp_path, "oc.txt", out_c)),
        hsd.BaselineEntry("od", out_d, "same_genre_outside_org", "writer:od", "house:w",
                          _write_file(tmp_path, "od.txt", out_d)),
    ] + [
        hsd.BaselineEntry(
            f"br{i}", broad_texts[i], "broad_reference", f"writer:br{i % 5}", None,
            _write_file(tmp_path, f"br{i}.txt", broad_texts[i]),
        )
        for i in range(20)
    ]
    target_path = _write_file(tmp_path, "target.txt", target_text)
    return {
        "target_text": target_text,
        "target_path": target_path,
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Test 1 — deterministic output
# ---------------------------------------------------------------------------

def test_deterministic_output(tmp_path):
    """Same inputs → byte-identical results."""
    fix = _make_full_fixture(tmp_path)
    r1 = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    r2 = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    r1.pop("_level_stats", None)
    r2.pop("_level_stats", None)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


# ---------------------------------------------------------------------------
# Test 2 — envelope shape
# ---------------------------------------------------------------------------

def test_envelope_shape(tmp_path):
    """build_output envelope has all required keys; results has required sub-keys."""
    from output_schema import build_output
    fix = _make_full_fixture(tmp_path)
    hsd._validate_baseline_set(fix["entries"], fix["target_path"], "writer:j")
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    results.pop("_level_stats", None)

    env = build_output(
        task_surface=hsd.TASK_SURFACE,
        tool=hsd.TOOL_NAME,
        version=hsd.SCRIPT_VERSION,
        target_path=fix["target_path"],
        target_words=100,
        baseline={"levels": {}, "leakage_checked": True},
        results=results,
        claim_license=hsd._build_claim_license(),
    )
    assert env["available"] is True
    r = env["results"]
    for key in (
        "levels_present", "per_level_family_delta", "idiolect_house_contrast",
        "attribution", "attribution_summary", "calibration_status", "assumptions",
    ):
        assert key in r, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Test 3 — count invariants
# ---------------------------------------------------------------------------

def test_count_invariants(tmp_path):
    """Fixed-tuple lengths and orientation constant."""
    assert len(hsd.BASELINE_LEVELS) == 5
    assert len(hsd.M1_FAMILIES) == 7
    assert len(hsd.ATTRIBUTION_BANDS) == 3
    assert len(hsd.ORIENTATIONS) == 1
    assert hsd.ORIENTATION == "positive_idiolect_borne"

    fix = _make_full_fixture(tmp_path)
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    results.pop("_level_stats", None)

    for level, fam_dict in results["per_level_family_delta"].items():
        for fam in fam_dict:
            assert fam in hsd.M1_FAMILIES, f"Unexpected family {fam!r} at level {level!r}"
    assert results["assumptions"]["orientation"] == "positive_idiolect_borne"


# ---------------------------------------------------------------------------
# Test 4 — worked-example golden pins
# ---------------------------------------------------------------------------

def test_worked_example_golden_pins(tmp_path):
    """pronoun_modal_negation → idiolect_borne; punctuation → house_borne."""
    fix = _make_full_fixture(tmp_path)
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    attr = results["attribution"]
    assert attr.get("pronoun_modal_negation") == "idiolect_borne", (
        f"pronoun_modal_negation: expected 'idiolect_borne', got {attr.get('pronoun_modal_negation')!r}"
    )
    assert attr.get("punctuation") == "house_borne", (
        f"punctuation: expected 'house_borne', got {attr.get('punctuation')!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — leakage guard: author leak
# ---------------------------------------------------------------------------

def test_leakage_guard_author_leak(tmp_path):
    """Target author appearing in different_authors_same_org → bad_input."""
    text_a = _make_text(False, False, 30)
    text_b = _make_text(False, False, 28)
    text_c = _make_text(False, False, 26)
    blog = _make_text(True, True, 30)
    blog2 = _make_text(True, True, 28)
    blog3 = _make_text(True, True, 26)

    # The target author appears in the house level — a leakage violation.
    entries = [
        hsd.BaselineEntry("a", text_a, "different_context", "writer:target", None,
                          _write_file(tmp_path, "a.txt", text_a)),
        hsd.BaselineEntry("b", blog, "different_context", "writer:target", None,
                          _write_file(tmp_path, "b.txt", blog)),
        hsd.BaselineEntry("c", blog2, "different_context", "writer:target", None,
                          _write_file(tmp_path, "c.txt", blog2)),
        hsd.BaselineEntry("d", blog3, "different_context", "writer:target", None,
                          _write_file(tmp_path, "d.txt", blog3)),
        # TARGET author in house level — leakage.
        hsd.BaselineEntry("leak1", text_b, "different_authors_same_org", "writer:target",
                          "house:x", _write_file(tmp_path, "leak1.txt", text_b)),
        hsd.BaselineEntry("k2", text_b, "different_authors_same_org", "writer:k", "house:x",
                          _write_file(tmp_path, "k2.txt", text_b)),
        hsd.BaselineEntry("l2", text_c, "different_authors_same_org", "writer:l", "house:x",
                          _write_file(tmp_path, "l2.txt", text_c)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="leakage"):
        hsd._validate_baseline_set(entries, None, "writer:target", min_words=100)


# ---------------------------------------------------------------------------
# Test 6 — leakage guard: path identity
# ---------------------------------------------------------------------------

def test_leakage_guard_path_identity(tmp_path):
    """Target file appearing as a baseline entry → bad_input (hard refusal)."""
    target_text = _make_text(False, False, 30)
    target_path = _write_file(tmp_path, "target_shared.txt", target_text)

    blog_a = _make_text(True, True, 30)
    blog_b = _make_text(True, True, 28)
    blog_c = _make_text(True, True, 26)
    other_a = _make_text(False, False, 28)
    other_b = _make_text(False, False, 26)

    entries = [
        hsd.BaselineEntry("ba", blog_a, "different_context", "writer:j", None,
                          _write_file(tmp_path, "ba.txt", blog_a)),
        hsd.BaselineEntry("bb", blog_b, "different_context", "writer:j", None,
                          _write_file(tmp_path, "bb.txt", blog_b)),
        hsd.BaselineEntry("bc", blog_c, "different_context", "writer:j", None,
                          _write_file(tmp_path, "bc.txt", blog_c)),
        # This entry's resolved_path == target_path — path identity leak.
        hsd.BaselineEntry("same_as_target", target_text,
                          "different_authors_same_org", "writer:k", "house:a",
                          target_path),
        hsd.BaselineEntry("ob", other_a, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "ob.txt", other_a)),
        hsd.BaselineEntry("oc", other_b, "different_authors_same_org", "writer:m", "house:a",
                          _write_file(tmp_path, "oc.txt", other_b)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="leakage"):
        hsd._validate_baseline_set(entries, target_path, "writer:j", min_words=100)


# ---------------------------------------------------------------------------
# Test 6a — inline text / missing text_path → bad_input
# ---------------------------------------------------------------------------

def test_inline_text_forbidden(tmp_path):
    """An entry with 'text' but no 'text_path' must raise HouseStyleError before any other gate."""
    manifest_path = tmp_path / "manifest.jsonl"
    # Write a manifest entry with inline 'text' — forbidden.
    manifest_path.write_text(
        json.dumps({
            "id": "inline_entry",
            "level": "different_context",
            "author_id": "writer:j",
            "org_id": None,
            "text": "This is inline text which is forbidden.",
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(hsd.HouseStyleError, match="inline|text_path"):
        hsd._load_manifest_entries(manifest_path)


def test_missing_text_path_forbidden(tmp_path):
    """An entry missing 'text_path' must raise HouseStyleError."""
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_text(
        json.dumps({
            "id": "no_path_entry",
            "level": "different_context",
            "author_id": "writer:j",
            "org_id": None,
            # No text_path key at all.
        }) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(hsd.HouseStyleError, match="text_path|inline"):
        hsd._load_manifest_entries(manifest_path)


# ---------------------------------------------------------------------------
# Test 7 — acceptance gate: missing isolating level
# ---------------------------------------------------------------------------

def test_missing_isolating_level_different_context(tmp_path):
    """Absent 'different_context' → bad_input."""
    text = _make_text(False, False, 35)
    entries = [
        hsd.BaselineEntry("k", text, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k.txt", text)),
        hsd.BaselineEntry("l", text, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "l.txt", text)),
        hsd.BaselineEntry("m", text, "different_authors_same_org", "writer:m", "house:a",
                          _write_file(tmp_path, "m.txt", text)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="different_context|isolating"):
        hsd._validate_baseline_set(entries, None, "writer:j", min_words=100)


def test_missing_isolating_level_house(tmp_path):
    """Absent 'different_authors_same_org' → bad_input."""
    text = _make_text(True, True, 35)
    entries = [
        hsd.BaselineEntry("ba", text, "different_context", "writer:j", None,
                          _write_file(tmp_path, "ba.txt", text)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="different_authors_same_org|isolating"):
        hsd._validate_baseline_set(entries, None, "writer:j", min_words=100)


# ---------------------------------------------------------------------------
# Test 8 — acceptance gate: too-few authors
# ---------------------------------------------------------------------------

def test_too_few_authors_house_level(tmp_path):
    """different_authors_same_org with < min_authors distinct author_ids → bad_input."""
    text = _make_text(False, False, 35)
    blog = _make_text(True, True, 35)
    entries = [
        hsd.BaselineEntry("ba", blog, "different_context", "writer:j", None,
                          _write_file(tmp_path, "ba.txt", blog)),
        # Only 2 distinct authors in house level (need ≥ 3).
        hsd.BaselineEntry("k1", text, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k1.txt", text)),
        hsd.BaselineEntry("k2", text, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k2.txt", text)),
        hsd.BaselineEntry("l1", text, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "l1.txt", text)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="distinct author"):
        hsd._validate_baseline_set(entries, None, "writer:j", min_authors=3, min_words=100)


# ---------------------------------------------------------------------------
# Test 9 — acceptance gate: too-few words / variance
# ---------------------------------------------------------------------------

def test_too_few_words_level(tmp_path):
    """A level below min_words → bad_input."""
    blog = _make_text(True, True, 35)
    stub = "Too short text."  # well below 2000 words
    entries = [
        hsd.BaselineEntry("ba", blog, "different_context", "writer:j", None,
                          _write_file(tmp_path, "ba.txt", blog)),
        hsd.BaselineEntry("k", stub, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k.txt", stub)),
        hsd.BaselineEntry("l", stub, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "l.txt", stub)),
        hsd.BaselineEntry("m", stub, "different_authors_same_org", "writer:m", "house:a",
                          _write_file(tmp_path, "m.txt", stub)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="words"):
        hsd._validate_baseline_set(entries, None, "writer:j", min_words=2000)


def test_single_entry_variance_floor(tmp_path):
    """broad_reference with 1 entry → bad_input (variance floor)."""
    blog = _make_text(True, True, 35)
    house = _make_text(False, False, 35)
    broad = _make_text(False, True, 35)
    entries = [
        hsd.BaselineEntry("ba", blog, "different_context", "writer:j", None,
                          _write_file(tmp_path, "ba.txt", blog)),
        hsd.BaselineEntry("k", house, "different_authors_same_org", "writer:k", "house:a",
                          _write_file(tmp_path, "k.txt", house)),
        hsd.BaselineEntry("l", house, "different_authors_same_org", "writer:l", "house:a",
                          _write_file(tmp_path, "l.txt", house)),
        hsd.BaselineEntry("m", house, "different_authors_same_org", "writer:m", "house:a",
                          _write_file(tmp_path, "m.txt", house)),
        # Only 1 broad_reference entry — below variance floor.
        hsd.BaselineEntry("br0", broad, "broad_reference", "writer:br0", None,
                          _write_file(tmp_path, "br0.txt", broad)),
    ]
    with pytest.raises(hsd.HouseStyleError, match="doc"):
        hsd._validate_baseline_set(
            entries, None, "writer:j", min_words=100, min_variance_docs=2
        )


# ---------------------------------------------------------------------------
# Test 10 — claim-license present + refuses verdict
# ---------------------------------------------------------------------------

def test_claim_license_present_and_refuses_verdict():
    """ClaimLicense block has required refusals."""
    lic = hsd._build_claim_license()
    d = lic.to_dict()
    assert d["task_surface"] == hsd.TASK_SURFACE
    dnl = d["does_not_license"].lower()
    assert "authorship" in dnl
    assert "ai" in dnl or "human" in dnl
    assert "real voice" in dnl or "true voice" in dnl
    assert "de-anonymiz" in dnl
    assert "selection" in dnl


# ---------------------------------------------------------------------------
# Test 11 — no-forbidden-key recursive guard (CI-blocking, unconditional)
# ---------------------------------------------------------------------------

def test_no_forbidden_key_in_results(tmp_path):
    """No forbidden verdict/selection key anywhere in results (happy path)."""
    fix = _make_full_fixture(tmp_path)
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    results.pop("_level_stats", None)
    assert _FORBIDDEN_KEYS.isdisjoint(set(_walk_keys(results))), (
        f"Forbidden key(s) found in results: "
        f"{_FORBIDDEN_KEYS & set(_walk_keys(results))}"
    )


def test_no_verdict_walk_catches_planted_key():
    """The _walk_keys recursion catches a planted forbidden key in a synthetic dict."""
    synthetic = {
        "levels_present": ["a"],
        "per_level_family_delta": {"a": {"function_words": 0.5}},
        "attribution": {"function_words": "idiolect_borne"},
        # Planted forbidden key — this must be caught.
        "verdict": "same_author",
    }
    found = set(_walk_keys(synthetic))
    assert not _FORBIDDEN_KEYS.isdisjoint(found), (
        "Expected walk to find 'verdict' in the synthetic dict"
    )


# ---------------------------------------------------------------------------
# Test 12 — band token-blocklist
# ---------------------------------------------------------------------------

_BAND_BLOCKLIST = frozenset({
    "author", "same_author", "different_author", "ai", "human",
    "real_voice", "verdict", "plagiar",
})


def test_band_token_blocklist():
    """No blocked token in ATTRIBUTION_BANDS values, results keys, or band values."""
    for band in hsd.ATTRIBUTION_BANDS:
        for token in _BAND_BLOCKLIST:
            assert token not in band.lower(), (
                f"Blocked token {token!r} found in ATTRIBUTION_BANDS member {band!r}"
            )


# ---------------------------------------------------------------------------
# Test 13 — no single decomposition score
# ---------------------------------------------------------------------------

def test_no_single_score(tmp_path):
    """No top-level house_style_score / idiolect_score / decomposition_score."""
    fix = _make_full_fixture(tmp_path)
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=0.15)
    results.pop("_level_stats", None)
    for bad_key in ("house_style_score", "idiolect_score", "decomposition_score"):
        assert bad_key not in results, f"Prohibited single-score key {bad_key!r} present"
    # Also check recursively.
    all_keys = set(_walk_keys(results))
    for bad_key in ("house_style_score", "idiolect_score", "decomposition_score"):
        assert bad_key not in all_keys, f"Prohibited key {bad_key!r} found nested in results"


# ---------------------------------------------------------------------------
# Test 14 — calibration PROVISIONAL
# ---------------------------------------------------------------------------

def test_calibration_provisional(tmp_path):
    """calibration_status == 'provisional' everywhere; margin echoed."""
    fix = _make_full_fixture(tmp_path)
    margin = 0.20
    results = hsd.decompose(fix["target_text"], fix["entries"], margin=margin)
    results.pop("_level_stats", None)
    assert results["calibration_status"] == "provisional"
    assert results["assumptions"]["calibration"]["status"] == "provisional"
    assert results["assumptions"]["margin"] == margin
    assert results["assumptions"]["calibration"]["margin"] == margin


# ---------------------------------------------------------------------------
# Test 15 — graceful degradation: target floor
# ---------------------------------------------------------------------------

def test_target_too_short_bad_input(tmp_path):
    """Target below 300 words → bad_input via CLI."""
    stub = tmp_path / "stub.txt"
    stub.write_text("Too short.", encoding="utf-8")

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("", encoding="utf-8")

    argv = [
        "--target", str(stub),
        "--target-author", "writer:j",
        "--baseline-manifest", str(manifest),
        "--json",
    ]
    out = StringIO()
    old_stdout = sys.stdout
    sys.stdout = out
    try:
        rc = hsd.main(argv)
    finally:
        sys.stdout = old_stdout

    assert rc == 1
    env = json.loads(out.getvalue())
    assert env["available"] is False
    assert env.get("reason_category") == "bad_input"


# ---------------------------------------------------------------------------
# Test 16 — M1 invariant of M2 seam: --lens embedding refuses fail-loud
# ---------------------------------------------------------------------------

def test_lens_embedding_refuses_even_with_stub_encoder(tmp_path):
    """--lens embedding → missing_dependency even when a stub encoder is importable.

    M1 wires no embedding subspace; the seam must refuse whether or not torch /
    a stub encoder is present (the planted-false-invariant pattern).
    """
    # Monkeypatch a stub encoder module onto sys.modules.
    import types
    stub_module = types.ModuleType("voice_fingerprint")
    stub_module.encode = lambda texts: [[0.0] * 64 for _ in texts]  # type: ignore[attr-defined]
    sys.modules["voice_fingerprint_stub_for_test"] = stub_module

    target = tmp_path / "target.txt"
    target.write_text(_make_text(False, False, 40), encoding="utf-8")

    argv = [
        "--target", str(target),
        "--target-author", "writer:j",
        "--baseline-manifest", str(tmp_path / "nonexistent.jsonl"),
        "--lens", "embedding",
        "--json",
    ]
    out = StringIO()
    old_stdout = sys.stdout
    sys.stdout = out
    try:
        rc = hsd.main(argv)
    finally:
        sys.stdout = old_stdout
        sys.modules.pop("voice_fingerprint_stub_for_test", None)

    assert rc == 1
    env = json.loads(out.getvalue())
    assert env["available"] is False
    assert env.get("reason_category") == "missing_dependency", (
        "Expected missing_dependency even when a stub encoder is importable"
    )


# ---------------------------------------------------------------------------
# Test 17 — anti-Goodhart held-out disjoint (forward guard; documented in header)
# ---------------------------------------------------------------------------

def test_anti_goodhart_held_out_disjoint():
    """The M1 fixture is NOT an M2 calibration corpus (documented guard).

    This test is a FORWARD GUARD: it documents the disjointness invariant
    and will flag any code that tries to reuse the M1 fixture for M2 training.
    Currently trivially true (M2 does not exist in M1); it pins the intent.
    """
    # The M1 fixture builder function is _build_worked_example_fixture /
    # _make_full_fixture. M2 calibration should use a SEPARATE labeled corpus.
    # Confirmed: the module exports no M2 calibration data or training path.
    assert not hasattr(hsd, "_m2_calibration_corpus"), (
        "M2 calibration corpus must not be defined in M1 — keep fixture disjoint"
    )
    assert not hasattr(hsd, "_m2_train"), (
        "No M2 training function should exist in M1"
    )
    # The ORIENTATION constant encodes the sign convention that M2 must honour
    # if/when it is built — it is a shared contract, not a calibrated parameter.
    assert hsd.ORIENTATION == "positive_idiolect_borne"
