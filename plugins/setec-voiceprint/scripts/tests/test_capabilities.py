#!/usr/bin/env python3
"""Tests for capabilities.py — the discoverability CLI for SETEC's
capabilities manifest.

Pins:

  * Manifest loads, has the expected structural keys
    (`schema_version`, `entries`).
  * Filter chain (surface / family / status / tier / register /
    length_floor_max / available_only / include_todo) behaves
    correctly.
  * `recommend()` returns curated-route matches first, ranked by
    keyword count, with TODO entries hidden by default.
  * `entry_available()` correctly distinguishes installed from
    missing deps via importlib.util.find_spec.
  * `show()` renders the expected fields for a curated entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import capabilities as cap  # type: ignore  # noqa: E402


def _manifest() -> dict:
    return cap.load_manifest()


# ---------- loading -----------------------------------------------

def test_manifest_loads_and_has_entries():
    m = _manifest()
    # Schema v0.3.0 added handoff posture (stable / experimental /
    # internal / none) + consumers free-list to make the consumer-
    # pinning contract explicit and queryable.
    assert m.get("schema_version") == "0.3.0"
    assert isinstance(m.get("entries"), list)
    assert len(m["entries"]) > 30


def test_every_entry_has_required_fields():
    m = _manifest()
    for entry in m["entries"]:
        assert entry.get("id"), f"entry missing id: {entry}"
        assert entry.get("script_path"), (
            f"{entry['id']} missing script_path"
        )
        assert entry.get("surface"), (
            f"{entry['id']} missing surface"
        )
        assert entry.get("status"), (
            f"{entry['id']} missing status"
        )
        compute = entry.get("compute")
        assert isinstance(compute, dict), (
            f"{entry['id']} missing compute block"
        )
        assert compute.get("tier"), (
            f"{entry['id']} missing compute.tier"
        )


# ---------- filtering ---------------------------------------------

def test_filter_excludes_todo_by_default():
    m = _manifest()
    out = cap.filter_entries(m["entries"])
    for e in out:
        assert e.get("status") != "todo", (
            f"todo leaked into default-filter output: {e['id']}"
        )


def test_filter_includes_todo_when_asked():
    m = _manifest()
    out = cap.filter_entries(m["entries"], include_todo=True)
    assert any(e.get("status") == "todo" for e in out)


def test_filter_by_surface():
    m = _manifest()
    out = cap.filter_entries(
        m["entries"], surface="narrative_decision_audit",
    )
    assert len(out) == 1
    assert out[0]["id"] == "narrative_decision_audit"


def test_filter_by_handoff_stable():
    """v0.3.0: --handoff stable returns only the entries that have
    been explicitly promoted for consumer pinning. Six entries are
    currently stable per the Phase A curation (variance_audit,
    voice_distance, idiolect_detector, aic_pattern_audit,
    restoration_packet, validation_harness)."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], handoff="stable")
    ids = {e["id"] for e in out}
    assert "variance_audit" in ids
    assert "aic_pattern_audit" in ids
    assert "narrative_decision_audit" not in ids  # experimental
    assert "dependency_check" not in ids  # internal
    # Every stable entry must carry a non-empty references list (the
    # drift linter's stable_without_references check enforces this).
    for e in out:
        assert (e.get("references") or []), (
            f"{e['id']} is handoff: stable but has empty references"
        )


def test_filter_by_handoff_experimental():
    """v0.3.0: --handoff experimental returns the new-surface
    entries whose envelope shape may evolve before 2.0.0."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], handoff="experimental")
    ids = {e["id"] for e in out}
    assert "narrative_decision_audit" in ids
    assert "binoculars_audit" in ids


def test_filter_by_handoff_internal():
    """v0.3.0: --handoff internal returns operator-side tooling."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], handoff="internal")
    ids = {e["id"] for e in out}
    assert "dependency_check" in ids
    assert "manifest_validator" in ids


def test_filter_by_consumer_apodictic():
    """v0.3.0: --consumer apodictic returns every audit that names
    apodictic in its consumers list. This is the canonical query
    APODICTIC's verdict layer runs to find its pinned surface."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], consumer="apodictic")
    ids = {e["id"] for e in out}
    # Phase A consumer-list: the 6 stable + narrative_decision_audit
    # (experimental) all name apodictic.
    expected_subset = {
        "variance_audit", "aic_pattern_audit", "voice_distance",
        "idiolect_detector", "restoration_packet",
        "validation_harness", "narrative_decision_audit",
    }
    assert expected_subset.issubset(ids), (
        f"missing from --consumer apodictic: "
        f"{expected_subset - ids}"
    )


def test_filter_by_consumer_setec_voicewright():
    """v1.115.0: --consumer setec-voicewright returns the voicewright
    fitness-loop surfaces — the selection ensemble (voice_fingerprint,
    voice_distance) + held-out validators (mimicry_cosplay_audit,
    general_imposters, binoculars_audit) + idiolect_detector (whose JSON
    is mimicry_cosplay_audit's required cross-check input).
    Plus narrative_decision_audit — added as a consumer for voicewright's
    spec-17 M3 work-level narrative diagnostic (a read-only check over the
    assembled draft, NOT a fitness-loop surface)."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], consumer="setec-voicewright")
    ids = {e["id"] for e in out}
    expected = {
        "voice_fingerprint", "voice_distance", "idiolect_detector",
        "mimicry_cosplay_audit", "general_imposters", "binoculars_audit",
        "narrative_decision_audit",
    }
    assert ids == expected, (
        f"--consumer setec-voicewright mismatch: "
        f"missing {expected - ids}, unexpected {ids - expected}"
    )


def test_filter_by_consumer_unknown_returns_empty():
    """A consumer name that no entry lists returns an empty result —
    not an error. The list is documentation, not enforcement, so
    operators can probe for hypothetical consumers."""
    m = _manifest()
    out = cap.filter_entries(m["entries"], consumer="nonexistent_consumer")
    assert out == []


def test_filter_handoff_and_consumer_compose():
    """`--handoff stable --consumer apodictic` is the canonical
    query APODICTIC uses to find its pin-against surface (entries
    SETEC promises stability on AND names APODICTIC as a consumer)."""
    m = _manifest()
    out = cap.filter_entries(
        m["entries"], handoff="stable", consumer="apodictic",
    )
    ids = {e["id"] for e in out}
    # narrative_decision_audit is experimental, so excluded by
    # handoff=stable even though it lists apodictic.
    assert "narrative_decision_audit" not in ids
    assert "variance_audit" in ids


def test_show_surfaces_handoff_and_consumers():
    """v0.3.0: `show` must surface handoff posture and the
    named-consumers list when populated."""
    m = _manifest()
    entry = next(
        e for e in m["entries"]
        if e["id"] == "narrative_decision_audit"
    )
    md = cap.render_show(entry)
    assert "handoff posture" in md.lower()
    assert "experimental" in md
    assert "apodictic" in md.lower()


def test_show_omits_consumers_when_empty():
    """`show` should NOT render the named-consumers line when the
    list is empty (avoids `named consumers:` followed by nothing)."""
    m = _manifest()
    entry = next(
        e for e in m["entries"]
        if e["id"] == "dependency_check"
    )
    md = cap.render_show(entry)
    # dependency_check is handoff: internal, consumers: []
    assert "named consumers" not in md.lower()


def test_filter_by_tier():
    m = _manifest()
    out = cap.filter_entries(m["entries"], tier="core")
    assert all(
        e["compute"]["tier"] == "core" for e in out
    )
    assert any(e["id"] == "voice_distance" for e in out)


def test_filter_by_status():
    m = _manifest()
    out = cap.filter_entries(
        m["entries"], status="literature_anchored",
    )
    ids = {e["id"] for e in out}
    assert "narrative_decision_audit" in ids
    assert "binoculars_audit" in ids


def test_filter_by_length_floor():
    m = _manifest()
    # Audits with length floor ≤ 500 words shouldn't include the
    # narrative-decision audit (its floor is 2000) but should include
    # variance_audit (its floor is 200).
    out = cap.filter_entries(
        m["entries"], length_floor_max=500,
    )
    ids = {e["id"] for e in out}
    assert "narrative_decision_audit" not in ids
    assert "variance_audit" in ids


def test_filter_combines_constraints():
    m = _manifest()
    out = cap.filter_entries(
        m["entries"],
        tier="core",
        status="empirically_oriented",
    )
    for e in out:
        assert e["compute"]["tier"] == "core"
        assert e["status"] == "empirically_oriented"


# ---------- recommend ---------------------------------------------

def test_recommend_fiction_routes_to_narrative_decision():
    m = _manifest()
    results = cap.recommend(
        "I have a 5000-word short story and I want to know if it was AI-edited",
        manifest=m,
    )
    assert results, "expected at least one recommendation"
    ids = [r[0] for r in results]
    assert "narrative_decision_audit" in ids
    assert "aic_pattern_audit" in ids


def test_recommend_essay_routes_to_variance_and_aic():
    m = _manifest()
    results = cap.recommend(
        "Is this short essay AI-generated?", manifest=m,
    )
    ids = [r[0] for r in results]
    assert "variance_audit" in ids
    assert "aic_pattern_audit" in ids


def test_recommend_setup_routes_to_dependency_check():
    m = _manifest()
    results = cap.recommend(
        "First-run setup; getting an ImportError from a SETEC script.",
        manifest=m,
    )
    ids = [r[0] for r in results]
    assert "dependency_check" in ids


def test_recommend_no_match_returns_empty_or_few():
    m = _manifest()
    results = cap.recommend(
        "purple monkey dishwasher xyzqwerty",
        manifest=m,
    )
    # Strict "no match" semantics: nothing comes back since none of
    # the tokens map to curated routes and the gibberish tokens
    # don't appear in any use_when string.
    assert results == []


def test_recommend_excludes_todo_entries():
    m = _manifest()
    results = cap.recommend(
        "short story essay novel revision draft fiction calibration setup",
        manifest=m,
    )
    for entry_id, entry, _ in results:
        assert entry.get("status") != "todo", (
            f"todo entry leaked into recommendations: {entry_id}"
        )


# ---------- availability ------------------------------------------

def test_entry_available_for_stdlib_only():
    """An entry whose required dependencies.python is empty is always
    available (no missing required deps)."""
    m = _manifest()
    for e in m["entries"]:
        if e.get("status") == "todo":
            continue
        required = (e.get("dependencies") or {}).get("python") or []
        if not required:
            ok, missing_req, _ = cap.entry_available(e)
            assert ok is True, (
                f"{e['id']} reported unavailable with empty required "
                f"deps: missing_req={missing_req}"
            )
            assert missing_req == []


def test_entry_available_reports_missing_required():
    """A synthetic entry with a deliberately fake required dep
    reports missing-required and available=False."""
    fake_entry = {
        "id": "fake",
        "dependencies": {"python": ["zzz_definitely_not_installed"]},
    }
    ok, missing_req, missing_opt = cap.entry_available(fake_entry)
    assert ok is False
    assert "zzz_definitely_not_installed" in missing_req
    assert missing_opt == []


def test_entry_available_optional_does_not_block():
    """Missing optional deps surface in missing_optional but do not
    flip available to False. This is the regression that motivated
    the v0.2 schema split — variance_audit's Tier 2/3/4 work runs
    on optional deps but its Tier 1 primary use case is stdlib only.
    """
    fake_entry = {
        "id": "fake_graceful",
        "dependencies": {
            "python": [],  # no required deps
            "python_optional": ["zzz_optional_missing"],
        },
    }
    ok, missing_req, missing_opt = cap.entry_available(fake_entry)
    assert ok is True, (
        "missing optional dep must not flip availability to False"
    )
    assert missing_req == []
    assert "zzz_optional_missing" in missing_opt


def test_entry_available_mixed_required_and_optional():
    """Required missing + optional missing: available=False and both
    lists populated independently."""
    fake_entry = {
        "id": "fake_mixed",
        "dependencies": {
            "python": ["zzz_required_missing"],
            "python_optional": ["zzz_optional_missing"],
        },
    }
    ok, missing_req, missing_opt = cap.entry_available(fake_entry)
    assert ok is False
    assert missing_req == ["zzz_required_missing"]
    assert missing_opt == ["zzz_optional_missing"]


def test_variance_audit_available_when_only_optional_deps_missing():
    """Regression: pre-v0.2, variance_audit was unavailable on any
    machine without sentence_transformers + textstat + nltk
    installed, even though its Tier 1 primary path is stdlib-only.
    Post-v0.2, the curated entry lists nothing as required so it
    reports available even with no extra deps installed."""
    m = _manifest()
    entry = next(
        e for e in m["entries"] if e["id"] == "variance_audit"
    )
    ok, missing_req, _ = cap.entry_available(entry)
    assert ok is True, (
        f"variance_audit should be available with stdlib alone; "
        f"got missing_req={missing_req}"
    )


# ---------- show + render -----------------------------------------

def test_render_show_includes_required_sections():
    m = _manifest()
    entry = next(
        e for e in m["entries"]
        if e["id"] == "narrative_decision_audit"
    )
    md = cap.render_show(entry)
    assert "# narrative_decision_audit" in md
    assert "## Purpose" in md
    assert "## Use when" in md
    assert "## Do not use when" in md
    assert "## Examples" in md
    assert "## References" in md
    assert "literature_anchored" in md


def test_render_table_handles_empty():
    out = cap.render_table([])
    assert "no entries" in out


def test_render_json_round_trip():
    m = _manifest()
    sample = m["entries"][:3]
    raw = cap.render_json(sample)
    parsed = json.loads(raw)
    assert len(parsed) == 3
    assert parsed[0]["id"] == sample[0]["id"]


# ---------- CLI ---------------------------------------------------

def test_cli_list_returns_zero():
    rc = cap.main(["list", "--format", "ids"])
    assert rc == 0


def test_cli_show_returns_zero_for_known_entry():
    rc = cap.main(["show", "narrative_decision_audit"])
    assert rc == 0


def test_cli_show_returns_nonzero_for_unknown_entry():
    rc = cap.main(["show", "this_does_not_exist"])
    assert rc != 0


def test_cli_recommend_returns_zero():
    rc = cap.main([
        "recommend", "--situation",
        "I have a short essay; is it AI?",
        "--format", "json",
    ])
    assert rc == 0


# ---------- R1: emit + projected calibration_status ----------------

# The consumer surfaces that carry the R1 field bundle
# (min_setec_version / json_delivery / structured inputs[]): the nine
# APODICTIC subprocess-shim surfaces plus the four setec-voicewright
# fitness surfaces promoted in 1.115.0.
_R1_CONSUMER_SURFACES = [
    "variance_audit", "manuscript_audit", "repetition_audit",
    "voice_distance", "voice_profile", "pov_voice_profile",
    "punctuation_cadence_audit", "idiolect_detector",
    "narrative_decision_audit",
    "voice_fingerprint", "mimicry_cosplay_audit",
    "general_imposters", "binoculars_audit",
]

# Per-surface version floors (R1 Step B for the apodictic nine;
# 1.115.0 for the voicewright four). Everything not listed floors at
# the 1.86.0 baseline.
_R1_FLOOR_EXCEPTIONS = {
    "narrative_decision_audit": "1.107.0",
    "voice_fingerprint": "1.115.0",
    "mimicry_cosplay_audit": "1.115.0",
    "general_imposters": "1.115.0",
    "binoculars_audit": "1.115.0",
}


def test_setec_version_matches_plugin_json():
    """setec_version() reads the version SOT from .claude-plugin/plugin.json
    (resolved relative to PLUGIN_ROOT, not the CWD)."""
    plugin_json = json.loads(
        cap.PLUGIN_JSON_PATH.read_text(encoding="utf-8")
    )
    assert cap.setec_version() == plugin_json["version"]


def test_emit_has_expected_top_level_fields():
    """(a) `emit --json` carries top-level setec_version (== plugin.json
    version), manifest_schema_version (== _meta schema_version), and
    entries[]."""
    m = _manifest()
    env = cap.build_emit_envelope(m)
    assert set(env.keys()) == {
        "setec_version", "manifest_schema_version", "entries",
    }
    plugin_json = json.loads(
        cap.PLUGIN_JSON_PATH.read_text(encoding="utf-8")
    )
    assert env["setec_version"] == plugin_json["version"]
    assert env["manifest_schema_version"] == m["schema_version"] == "0.3.0"
    assert isinstance(env["entries"], list)
    assert len(env["entries"]) == len(m["entries"])


def test_emit_projects_calibration_status_on_every_entry():
    """Every entry in the emit envelope carries calibration_status projected
    from its status — and the projection does not mutate the stored entry."""
    m = _manifest()
    env = cap.build_emit_envelope(m)
    for e in env["entries"]:
        assert e.get("calibration_status") == e.get("status")
    # Projection must be non-mutating: the source manifest entries never gain
    # a calibration_status key.
    assert all(
        "calibration_status" not in e for e in m["entries"]
    ), "build_emit_envelope mutated the source manifest entries"


def test_emit_consumer_surfaces_carry_full_r1_bundle():
    """(b) each of the 9 consumer entries in emit output has
    min_setec_version, json_delivery, structured inputs[] (list of dicts),
    and the projected calibration_status."""
    env = cap.build_emit_envelope(_manifest())
    by_id = {e["id"]: e for e in env["entries"]}
    for sid in _R1_CONSUMER_SURFACES:
        e = by_id[sid]
        assert isinstance(e.get("min_setec_version"), str), sid
        assert e.get("json_delivery") in ("stdout", "file"), sid
        inputs = e.get("inputs")
        assert isinstance(inputs, list) and inputs, sid
        assert all(isinstance(item, dict) for item in inputs), sid
        assert all(
            {"flag", "type", "required"} <= set(item) for item in inputs
        ), sid
        assert "calibration_status" in e, sid


def test_emit_floor_and_delivery_decisions():
    """Pin the per-surface floor and delivery decisions: the R1 Step B
    floors (narrative_decision_audit at 1.107.0, the other apodictic
    surfaces at 1.86.0), the voicewright four at 1.115.0, and the
    file-delivery set — voice_profile + pov_voice_profile + general_imposters
    (all privacy-gate non-private output, so the R2 dispatcher must inject
    a private --json-out and project their envelopes)."""
    by_id = {
        e["id"]: e for e in cap.build_emit_envelope(_manifest())["entries"]
    }
    for sid in _R1_CONSUMER_SURFACES:
        expected_floor = _R1_FLOOR_EXCEPTIONS.get(sid, "1.86.0")
        assert by_id[sid]["min_setec_version"] == expected_floor, sid
    file_surfaces = {"voice_profile", "pov_voice_profile", "general_imposters"}
    for sid in _R1_CONSUMER_SURFACES:
        expected = "file" if sid in file_surfaces else "stdout"
        assert by_id[sid]["json_delivery"] == expected, sid


def test_show_json_projects_calibration_status():
    """`show <id> --json` projects calibration_status the same way emit does,
    so a consumer can read a single surface consistently."""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cap.main(["show", "variance_audit", "--format", "json"])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["calibration_status"] == payload["status"]
    # The R1 bundle flows through `show` (it dumps the entry).
    assert isinstance(payload["inputs"], list)
    assert payload["min_setec_version"] == "1.86.0"


def test_cli_emit_returns_zero_and_valid_json():
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cap.main(["emit", "--json"])
    assert rc == 0
    env = json.loads(buf.getvalue())
    assert "setec_version" in env and "entries" in env


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
