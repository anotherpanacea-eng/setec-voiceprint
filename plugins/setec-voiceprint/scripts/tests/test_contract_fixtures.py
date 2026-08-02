#!/usr/bin/env python3
"""Tests for the R5 contract fixtures (golden envelopes + fake + drift gate).

Pins (spec §6):

  * The generator's ``--check`` passes on the committed tree.
  * Every one of the goldens is a valid ``schema_version: 1.0``
    envelope with the 12 required top-level keys and the correct
    ``task_surface`` (= the surface fragment's ``surface`` field).
  * ``fake_setec.py <surface>`` emits parseable JSON byte-identical to the
    committed golden (checked for ≥ 2 surfaces) and ``--list`` enumerates
    them.
  * The drift check FAILS when a committed golden is mutated.
  * Generation is deterministic: regenerating twice yields identical bytes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # scripts/
PLUGIN_ROOT = ROOT.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
FIXTURES_DIR = PLUGIN_ROOT / "references" / "contract_fixtures"
TOOLS = REPO_ROOT / "tools"

for p in (str(ROOT), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import gen_contract_fixtures as gen  # type: ignore  # noqa: E402

# The surface -> task_surface mapping the goldens must reflect (the
# fragment `surface` value each script declares as TASK_SURFACE).
EXPECTED_TASK_SURFACE = {
    "author_corpus_export": "voice_coherence_acquisition",
    "variance_audit": "smoothing_diagnosis",
    "manuscript_audit": "smoothing_diagnosis",
    "repetition_audit": "smoothing_diagnosis",
    "voice_distance": "voice_coherence",
    "voice_profile": "voice_coherence",
    "pov_voice_profile": "voice_coherence",
    "punctuation_cadence_audit": "voice_coherence",
    "idiolect_detector": "voice_coherence",
    "narrative_decision_audit": "narrative_decision_audit",
    "voice_fingerprint": "authorship_embedding",
    "mimicry_cosplay_audit": "voice_coherence",
    "general_imposters": "voice_coherence",
    "binoculars_audit": "binoculars_discrimination",
    "argument_decision_audit": "argument_decision_audit",
    "position_pair_register": "position_pair_register",
    "agd_move_scan": "agd_move_scan",
    "s5_distance": "voice_coherence",
}

REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

ALL_SURFACES = sorted(EXPECTED_TASK_SURFACE)


def test_generator_knows_every_surface():
    assert gen.surfaces() == ALL_SURFACES


def test_author_corpus_fixture_preserves_null_target_path():
    envelope = json.loads(
        (FIXTURES_DIR / "author_corpus_export.json").read_text(encoding="utf-8")
    )
    assert envelope["target"]["path"] is None


def test_fixtures_dir_holds_only_known_goldens():
    """Privacy defense-in-depth: the .gitignore negation re-includes every
    ``*.json`` under contract_fixtures/, escaping the ``*_voice_profile.json``
    privacy ratchet. Assert the directory contains ONLY the known,
    sentinelized goldens, so a stray real voice-clone artifact dropped here
    can never be committed past the ratchet."""
    present = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))
    assert present == ALL_SURFACES, (
        "unexpected .json under contract_fixtures/ (privacy-ratchet escape "
        f"risk): {sorted(set(present) ^ set(ALL_SURFACES))}"
    )


def test_generator_check_passes_on_committed_tree():
    """(a) The committed goldens are consistent with build_output."""
    problems = gen.check_all()
    assert not problems, "contract-fixture drift on committed tree: " + "; ".join(problems)


@pytest.mark.parametrize("surface", ALL_SURFACES)
def test_every_golden_is_a_valid_envelope(surface):
    """(b) Each golden has the 12 required keys, schema 1.0, correct
    task_surface, tool == surface id, and normalized version."""
    path = FIXTURES_DIR / f"{surface}.json"
    assert path.exists(), f"missing golden for {surface}"
    env = json.loads(path.read_text(encoding="utf-8"))

    assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == EXPECTED_TASK_SURFACE[surface]
    assert env["tool"] == surface
    # claim_license is present and its surface matches the envelope's.
    assert env["claim_license"] is not None
    assert env["claim_license"]["task_surface"] == env["task_surface"]
    assert env["claim_license_rendered"]
    # Volatile fields are normalized.
    assert env["version"] == gen.VERSION_SENTINEL
    if surface in {"author_corpus_export", "s5_distance"}:
        assert env["target"]["path"] is None
    if surface == "author_corpus_export":
        assert env["results"]["producer_receipt"]["source_persona_aliases"] == {}
    else:
        if surface != "s5_distance":
            assert env["target"]["path"] == gen.PATH_SENTINEL


def test_normalization_sentinels_applied_for_narrative():
    """narrative_decision_audit carries the extra volatile fields."""
    env = json.loads((FIXTURES_DIR / "narrative_decision_audit.json").read_text())
    results = env["results"]
    assert results["run_timestamp_utc"] == gen.TIMESTAMP_SENTINEL
    assert results["prompt_fingerprint_sha256"] == gen.SHA_SENTINEL


def test_pov_manifest_path_normalized():
    env = json.loads((FIXTURES_DIR / "pov_voice_profile.json").read_text())
    assert env["results"]["inputs"]["manifest"] == gen.PATH_SENTINEL


def test_mimicry_fixture_pairings_are_registered():
    """The SHIPPED mimicry_cosplay_audit golden names five companion surfaces
    in prose — `confounder_audit` + "the evidentiary-conditions gate" in
    ``does_not_license``, and `before_after_restoration` /
    `surface_disagreement_resolver` / `semantic_preservation_check` in
    ``additional_caveats``. That prose is a contract statement vendored into
    apodictic and setec-voicewright, but nothing pinned it to the registry, so
    a companion surface could be renamed or retired while the fixture kept
    naming it (the FM-4 finding).

    Pin both directions: the fixture prose must still name each pairing, AND
    each named capability must record `mimicry_cosplay_audit` in its
    `consumers`. Retiring one of these five now fails here instead of leaving
    the shipped fixture pointing at a capability that no longer exists.
    """
    import capabilities as cap  # type: ignore

    env = json.loads(
        (FIXTURES_DIR / "mimicry_cosplay_audit.json").read_text(encoding="utf-8")
    )
    license_ = env["claim_license"]
    caveats = " ".join(license_["additional_caveats"])
    does_not = license_["does_not_license"]

    # (1) The prose still names each pairing.
    assert "confounder_audit" in caveats
    assert "the confounder audit" in does_not
    assert "the evidentiary-conditions gate" in does_not
    for surface in (
        "before_after_restoration",
        "surface_disagreement_resolver",
        "semantic_preservation_check",
    ):
        assert surface in caveats, f"{surface} no longer named in the fixture prose"

    # (2) The registry records the pairing, so `consumers` is not silently
    # wrong about who reads these surfaces.
    manifest = cap.load_manifest(
        Path(__file__).resolve().parents[2] / "capabilities.d"
    )
    entries = {e["id"]: e for e in manifest["entries"]}
    for surface in (
        "confounder_audit",
        "evidentiary_conditions_gate",
        "before_after_restoration",
        "surface_disagreement_resolver",
        "semantic_preservation_check",
    ):
        assert surface in entries, f"{surface} named by the shipped fixture is not registered"
        assert "mimicry_cosplay_audit" in (entries[surface].get("consumers") or []), (
            f"{surface} is named by the shipped mimicry_cosplay_audit "
            f"claim_license but does not record it in `consumers`"
        )


# ---- (c) fake_setec.py -------------------------------------------------

def _run_fake(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(FIXTURES_DIR / "fake_setec.py"), *args],
        capture_output=True, text=True,
    )


def test_fake_setec_list_enumerates_surfaces():
    proc = _run_fake("--list")
    assert proc.returncode == 0
    listed = proc.stdout.split()
    assert listed == ALL_SURFACES


@pytest.mark.parametrize("surface", ["variance_audit", "narrative_decision_audit"])
def test_fake_setec_emits_golden_json(surface):
    """fake_setec output is parseable JSON byte-identical to the golden."""
    proc = _run_fake(surface)
    assert proc.returncode == 0
    parsed = json.loads(proc.stdout)  # parseable
    committed = json.loads((FIXTURES_DIR / f"{surface}.json").read_text())
    assert parsed == committed
    # Byte-identical to the committed golden (both sort_keys, indent 2).
    assert proc.stdout == (FIXTURES_DIR / f"{surface}.json").read_text()


def test_fake_setec_unknown_surface_exits_2():
    proc = _run_fake("does_not_exist")
    assert proc.returncode == 2
    assert "unknown surface" in proc.stderr


# ---- (d) drift check fails on mutation ---------------------------------

def test_drift_check_fails_when_golden_mutated(tmp_path, monkeypatch):
    """Corrupt one golden and assert both the generator and the capabilities
    drift checker flag it. Operates on a temp *copy* of the fixtures dir
    (monkeypatched into gen) so it never mutates the committed tree — required
    to be safe under ``pytest -n auto``, where other workers read that tree."""
    tmp_fixtures = tmp_path / "contract_fixtures"
    shutil.copytree(gen.FIXTURES_DIR, tmp_fixtures)
    # Both sides resolve goldens via gen.FIXTURES_DIR (the drift checker's
    # Check 9 delegates to gen.check_all()), so this one patch redirects both.
    monkeypatch.setattr(gen, "FIXTURES_DIR", tmp_fixtures)

    target = tmp_fixtures / "variance_audit.json"
    mutated = json.loads(target.read_text(encoding="utf-8"))
    mutated["results"]["compression"]["band"] = "MUTATED-FOR-TEST"
    target.write_text(
        json.dumps(mutated, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Generator side.
    problems = gen.check_all()
    assert any(p.startswith("variance_audit:") for p in problems), problems

    # Drift-checker side (Check 9 / fixture_drift) — delegates to gen.check_all().
    import check_capabilities_drift as ccd  # type: ignore
    report = ccd.check_drift()
    assert not report.passed
    kinds = {(v.kind, v.where) for v in report.violations}
    assert ("fixture_drift", "variance_audit") in kinds, kinds


def test_clean_tree_passes_drift_checker():
    import check_capabilities_drift as ccd  # type: ignore
    report = ccd.check_drift()
    assert report.passed, [f"{v.kind}:{v.where}" for v in report.violations]


def test_voice_distance_fixture_has_live_register_family_contract():
    """Static shape gate: mutually stale generator+golden must still fail."""
    regenerated = gen.regenerate_surface("voice_distance")
    committed = gen.load_golden("voice_distance")
    assert committed is not None
    for envelope in (regenerated, committed):
        block = envelope["results"]["register_match"]
        classification = block["target_classification"]
        match = block["match"]
        assert classification["taxonomy"] == "register_families/v2"
        assert match["taxonomy"] == "register_families/v2"
        assert classification["primary"] in {
            "formal_legal_policy", "formal_first_person", "academic",
            "journalism", "narrative_fiction", "first_person_essay",
            "promotional", "short_social", "unknown",
        }
        assert isinstance(classification["secondary"], list)
        assert match["target_family"] == classification["primary"]
        assert "baseline_family_distribution" in match
        assert "strength" in match
        assert "verdict" not in match
        assert (
            envelope["claim_license"]["comparison_set"]["register_match"]
            == match["strength"]
        )


# ---- (e) determinism ---------------------------------------------------

@pytest.mark.parametrize("surface", ALL_SURFACES)
def test_regeneration_is_byte_stable(surface):
    a = gen.serialize(gen.regenerate_surface(surface))
    b = gen.serialize(gen.regenerate_surface(surface))
    assert a == b
    # And identical to the committed golden.
    assert a == (FIXTURES_DIR / f"{surface}.json").read_text(encoding="utf-8")
