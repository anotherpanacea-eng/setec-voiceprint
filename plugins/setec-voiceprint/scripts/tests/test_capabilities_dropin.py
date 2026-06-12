#!/usr/bin/env python3
"""Tests for the drop-in capabilities manifest (#170, PR2).

`capabilities.yaml` (an 82-entry monolith every audit PR edited) is now a
`capabilities.d/` directory: one `<id>.yaml` fragment per capability plus
`_meta.yaml` for `schema_version`. `capabilities.load_manifest()` aggregates
them, and the repo tools import that one loader.

Pinned guarantees:
  * No behavior change: the aggregated manifest carries every entry, byte-for
    -byte equal to a golden snapshot of the pre-split parse (compared by id, so
    the intentional alphabetical re-ordering doesn't mask a lost/changed entry).
  * Aggregation shape: schema_version from `_meta.yaml`; one fragment per id.
  * The real consumers (drift linter, docs-freshness coverage) run green against
    the committed directory end-to-end.
  * Drop-in works: a new fragment is picked up with no loader edit; an empty dir
    is rejected; a legacy single file still loads (test fixtures rely on it).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
for p in (str(SCRIPTS_ROOT), str(REPO_ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # type: ignore  # noqa: E402

pytest.importorskip("yaml")

import capabilities as cap  # type: ignore  # noqa: E402

_GOLDEN = Path(__file__).resolve().parent / "_golden_capabilities.json"
_CAP_DIR = SCRIPTS_ROOT.parent / "capabilities.d"


def _by_id(manifest):
    return {e["id"]: e for e in manifest["entries"]}


def test_aggregate_matches_golden_by_id():
    """Every entry survives the split byte-for-byte (compared by id, so the
    alphabetical re-order doesn't hide a lost or mutated entry)."""
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    m = cap.load_manifest(_CAP_DIR)
    assert m["schema_version"] == golden["schema_version"]
    assert _by_id(m) == _by_id(golden)
    assert len(m["entries"]) == len(golden["entries"]) == 87


def test_meta_carries_schema_version():
    meta = _CAP_DIR / "_meta.yaml"
    assert meta.exists()
    assert cap.load_manifest(_CAP_DIR)["schema_version"] == "0.3.0"


def test_one_fragment_per_entry_bijection():
    frag_ids = {p.stem for p in _CAP_DIR.glob("*.yaml") if p.name != "_meta.yaml"}
    manifest_ids = {e["id"] for e in cap.load_manifest(_CAP_DIR)["entries"]}
    assert frag_ids == manifest_ids


def test_entry_order_is_deterministic_alphabetical():
    """Aggregated order is sorted by fragment filename — the only collision-free
    order for a drop-in directory. Inert (consumers are order-independent or
    sort); pinned so it stays intentional."""
    ids = [e["id"] for e in cap.load_manifest(_CAP_DIR)["entries"]]
    assert ids == sorted(ids)


def test_empty_dir_is_rejected(tmp_path):
    (tmp_path / "_meta.yaml").write_text("schema_version: '0.3.0'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cap.load_manifest(tmp_path)


def test_legacy_single_file_still_loads(tmp_path):
    """Synthetic test fixtures pass a single temp manifest file; the loader must
    still accept that."""
    f = tmp_path / "m.yaml"
    f.write_text(
        "schema_version: '0.3.0'\nentries:\n  - id: x\n    surface: validation\n",
        encoding="utf-8",
    )
    m = cap.load_manifest(f)
    assert [e["id"] for e in m["entries"]] == ["x"]


def test_dropin_registers_a_new_entry(tmp_path):
    """Writing a fragment file is sufficient to add a capability — no loader edit."""
    (tmp_path / "_meta.yaml").write_text("schema_version: '0.3.0'\n", encoding="utf-8")
    (tmp_path / "demo_cap.yaml").write_text(
        "entries:\n  - id: demo_cap\n    surface: validation\n", encoding="utf-8"
    )
    m = cap.load_manifest(tmp_path)
    assert _by_id(m)["demo_cap"]["surface"] == "validation"


def test_fragment_must_be_single_entry_keyed_by_filename(tmp_path):
    """The loader enforces the drop-in invariant — one entry per fragment, id ==
    filename — so a mis-keyed or multi-entry fragment can't slip through green."""
    (tmp_path / "_meta.yaml").write_text("schema_version: '0.3.0'\n", encoding="utf-8")
    # id does not match filename stem
    bad = tmp_path / "expected_id.yaml"
    bad.write_text("entries:\n  - id: other_id\n    surface: validation\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cap.load_manifest(tmp_path)
    bad.unlink()
    # more than one entry in a single fragment
    (tmp_path / "two.yaml").write_text("entries:\n  - id: a\n  - id: b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cap.load_manifest(tmp_path)


def test_real_consumers_pass_against_committed_dir():
    """End-to-end: the drift linter and docs-freshness coverage run green against
    the committed capabilities.d/ — catches consumers that bypass the loader."""
    import check_capabilities_drift as drift  # type: ignore
    import check_docs_freshness as freshness  # type: ignore

    report = drift.check_drift(drift.DEFAULT_MANIFEST)
    assert report.violations == [], [v.render() for v in report.violations]

    missing = freshness.changelog_coverage(freshness.DEFAULT_MANIFEST, freshness.CHANGELOG)
    assert missing == []
