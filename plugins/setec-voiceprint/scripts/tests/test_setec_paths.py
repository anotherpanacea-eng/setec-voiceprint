"""Tests for setec.paths — plugin-root resolution + named data paths.

Per specs/svp-packaging-conversion.md §1. Covers:

  - find_plugin_root() resolves the real plugin root from its default
    start and from an explicit deep start under the tree.
  - Zero-match (a tree with no `.claude-plugin/plugin.json` ancestor)
    and multi-match (a nested/vendored plugin checkout) both raise
    PluginRootError — fail closed, never guess.
  - plugin_paths() / the convenience accessors resolve to the real,
    existing on-disk locations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

from setec import paths as setec_paths  # noqa: E402

REAL_PLUGIN_ROOT = SCRIPTS.parent


def test_find_plugin_root_default_start_resolves_real_root():
    assert setec_paths.find_plugin_root() == REAL_PLUGIN_ROOT


def test_find_plugin_root_explicit_deep_start_resolves_same_root():
    deep_start = SCRIPTS / "tests" / "test_setec_paths.py"
    assert setec_paths.find_plugin_root(deep_start) == REAL_PLUGIN_ROOT


def test_find_plugin_root_accepts_directory_start():
    assert setec_paths.find_plugin_root(SCRIPTS / "tests") == REAL_PLUGIN_ROOT


def test_find_plugin_root_zero_match_raises(tmp_path):
    orphan = tmp_path / "a" / "b" / "c"
    orphan.mkdir(parents=True)
    with pytest.raises(setec_paths.PluginRootError, match="no ancestor"):
        setec_paths.find_plugin_root(orphan)


def test_find_plugin_root_multiple_matches_raises(tmp_path):
    # Nested/vendored plugin checkout: an outer marker AND an inner
    # marker on the same ancestor chain from `start`. Both are real
    # candidates; the resolver must refuse rather than pick one.
    outer_marker = tmp_path / ".claude-plugin" / "plugin.json"
    outer_marker.parent.mkdir(parents=True)
    outer_marker.write_text("{}", encoding="utf-8")

    inner_root = tmp_path / "vendored" / "setec-voiceprint"
    inner_marker = inner_root / ".claude-plugin" / "plugin.json"
    inner_marker.parent.mkdir(parents=True)
    inner_marker.write_text("{}", encoding="utf-8")

    start = inner_root / "scripts" / "tests"
    start.mkdir(parents=True)

    with pytest.raises(setec_paths.PluginRootError, match="ambiguous"):
        setec_paths.find_plugin_root(start)


def test_plugin_paths_named_locations_exist_on_real_tree():
    p = setec_paths.plugin_paths()
    assert p.root == REAL_PLUGIN_ROOT
    assert p.scripts.is_dir()
    assert p.capabilities_d.is_dir()
    assert p.plugin_json.is_file()
    assert p.claim_license_surfaces.is_dir()
    assert p.register_tiers_d.is_dir()
    assert p.references.is_dir()
    assert p.data.is_dir()


def test_convenience_accessors_match_plugin_paths():
    bundle = setec_paths.plugin_paths()
    assert setec_paths.scripts_dir() == bundle.scripts
    assert setec_paths.capabilities_d_dir() == bundle.capabilities_d
    assert setec_paths.plugin_json_path() == bundle.plugin_json
    assert (
        setec_paths.claim_license_surfaces_dir()
        == bundle.claim_license_surfaces
    )
    assert setec_paths.register_tiers_d_dir() == bundle.register_tiers_d
    assert setec_paths.references_dir() == bundle.references
    assert setec_paths.data_dir() == bundle.data


def test_scripts_dir_equals_real_scripts_root():
    assert setec_paths.scripts_dir() == SCRIPTS


def test_plugin_json_path_content_matches_setec_voiceprint():
    import json

    p = setec_paths.plugin_paths()
    data = json.loads(p.plugin_json.read_text(encoding="utf-8"))
    assert data["name"] == "setec-voiceprint"
