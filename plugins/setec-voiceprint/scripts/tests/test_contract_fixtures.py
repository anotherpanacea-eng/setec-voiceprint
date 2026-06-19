#!/usr/bin/env python3
"""Tests for the R5 contract fixtures (golden envelopes + fake + drift gate).

Pins (spec §6):

  * The generator's ``--check`` passes on the committed tree.
  * Every one of the nine goldens is a valid ``schema_version: 1.0``
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
}

REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

ALL_SURFACES = sorted(EXPECTED_TASK_SURFACE)


def test_generator_knows_the_nine_surfaces():
    assert gen.surfaces() == ALL_SURFACES


def test_fixtures_dir_holds_only_known_goldens():
    """Privacy defense-in-depth: the .gitignore negation re-includes every
    ``*.json`` under contract_fixtures/, escaping the ``*_voice_profile.json``
    privacy ratchet. Assert the directory contains ONLY the nine known,
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


# ---- (e) determinism ---------------------------------------------------

@pytest.mark.parametrize("surface", ALL_SURFACES)
def test_regeneration_is_byte_stable(surface):
    a = gen.serialize(gen.regenerate_surface(surface))
    b = gen.serialize(gen.regenerate_surface(surface))
    assert a == b
    # And identical to the committed golden.
    assert a == (FIXTURES_DIR / f"{surface}.json").read_text(encoding="utf-8")
