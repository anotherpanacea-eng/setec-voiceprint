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
    # Schema v0.2.0 split dependencies into required (python) +
    # optional (python_optional) to fix --available false-negatives
    # on graceful-degradation audits like variance_audit.
    assert m.get("schema_version") == "0.2.0"
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
