#!/usr/bin/env python3
"""Tests for tools/check_claim_license_guard.py — the no-change
claim-license deficit lock (specs/svp-packaging-conversion.md §3).

Pins:

  * The real repo's protected set, self-compared (merge-base HEAD vs.
    HEAD, no network required), passes with an empty move map.
  * A synthetic protected-module edit with NO declared move is caught
    (fail-before: this is the semantic-change class the gate exists to
    block).
  * A synthetic file MOVE that IS declared in `moves` (old_path ->
    new_path, symbol rewritten) passes — relocation is authorized.
  * `load_move_map` rejects a non-one-to-one map (two moves claiming
    the same `new_path` — an unlisted second destination) and a
    malformed row (missing required keys).
  * A missing `packaging_move_map.json` fails closed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_claim_license_guard as clg  # type: ignore  # noqa: E402


def test_real_repo_protected_set_passes_self_comparison():
    """merge-base HEAD vs. HEAD is always identical, so this proves the
    protected-set construction + normalized-AST comparison machinery
    runs clean end-to-end on the real tree with no network dependency."""
    passed, report = clg.run(base_ref="HEAD")
    assert passed, report["deltas"]
    assert report["protected_module_count"] > 0
    assert "plugins/setec-voiceprint/scripts/claim_license.py" in (
        report["protected_modules"]
    )


def test_real_repo_judge_backends_is_a_protected_supplier():
    """Build-review P1 finding #4's named repro: judge_backends.py must
    be in the protected set (reached via narrative_judge.py's whole-
    body scan, itself a supplier through
    narrative_decision_long_form.py's ClaimLicense comparison_set
    referencing nj.fingerprint_prompt()). The narrower, symbol-scoped
    revision of build_protected_set silently excluded it."""
    protected = clg.build_protected_set()
    paths = {p.path for p in protected}
    assert "plugins/setec-voiceprint/scripts/judge_backends.py" in paths


def test_module_path_for_import_resolves_setec_package_submodule(tmp_path, monkeypatch):
    """Build-review P2 finding (d): `from setec import paths` (a bare
    package-member import, not `from setec.paths import x`) must
    resolve to `setec/paths.py`, not the nonexistent `setec.py` — the
    resolution hole that would silently stop tracing supplier
    provenance once P2 starts routing claim-license-adjacent code
    through the setec package."""
    monkeypatch.setattr(clg, "SCRIPTS_ROOT", tmp_path)
    pkg = tmp_path / "setec"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "paths.py").write_text("def find_plugin_root():\n    return None\n", encoding="utf-8")
    resolved = clg._module_path_for_import(tmp_path / "an_audit.py", "setec", 0, symbol="paths")
    assert resolved == pkg / "paths.py"

    # `from setec.paths import find_plugin_root` (module_dotted already
    # names the submodule) resolves the same way via the flat-.py branch.
    resolved2 = clg._module_path_for_import(
        tmp_path / "an_audit.py", "setec.paths", 0, symbol="find_plugin_root",
    )
    assert resolved2 == pkg / "paths.py"

    # A package with no matching submodule falls back to __init__.py.
    resolved3 = clg._module_path_for_import(tmp_path / "an_audit.py", "setec", 0, symbol="nonexistent")
    assert resolved3 == pkg / "__init__.py"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return proc.stdout


def _init_fake_plugin_repo(tmp_path: Path) -> Path:
    """A minimal synthetic checkout with the same relative layout
    check_claim_license_guard expects: <repo>/plugins/setec-voiceprint/
    scripts/{claim_license.py, an_audit.py}."""
    repo = tmp_path / "fake_repo"
    scripts = repo / "plugins" / "setec-voiceprint" / "scripts"
    scripts.mkdir(parents=True)

    (scripts / "claim_license.py").write_text(
        "class ClaimLicense:\n"
        "    def __init__(self, licenses):\n"
        "        self.licenses = licenses\n",
        encoding="utf-8",
    )
    (scripts / "an_audit.py").write_text(
        "from claim_license import ClaimLicense\n\n"
        "def _claim_license():\n"
        "    return ClaimLicense(licenses='reports X')\n",
        encoding="utf-8",
    )
    (repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json").write_text(
        json.dumps({"schema": 1, "moves": [], "path_rewrites": []}),
        encoding="utf-8",
    )

    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    repo = _init_fake_plugin_repo(tmp_path)
    monkeypatch.setattr(clg, "REPO_ROOT", repo)
    monkeypatch.setattr(clg, "PLUGIN_ROOT", repo / "plugins" / "setec-voiceprint")
    monkeypatch.setattr(clg, "SCRIPTS_ROOT", repo / "plugins" / "setec-voiceprint" / "scripts")
    monkeypatch.setattr(
        clg, "MOVE_MAP_PATH",
        repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json",
    )
    return repo


def test_unlisted_semantic_edit_is_caught(fake_repo):
    audit = fake_repo / "plugins" / "setec-voiceprint" / "scripts" / "an_audit.py"
    audit.write_text(
        "from claim_license import ClaimLicense\n\n"
        "def _claim_license():\n"
        "    return ClaimLicense(licenses='reports Y')\n",  # X -> Y, no move declared
        encoding="utf-8",
    )
    passed, report = clg.run(base_ref="HEAD")
    assert not passed
    paths = {d["path"] for d in report["deltas"]}
    assert "plugins/setec-voiceprint/scripts/an_audit.py" in paths


def test_declared_move_with_symbol_rewrite_passes(fake_repo):
    scripts = fake_repo / "plugins" / "setec-voiceprint" / "scripts"
    old = scripts / "an_audit.py"
    content = old.read_text(encoding="utf-8")
    old.unlink()
    new_dir = scripts / "setec" / "surfaces"
    new_dir.mkdir(parents=True)
    new = new_dir / "an_audit.py"
    # Relocation-only rewrite: the function name is the ONLY thing that
    # changes, and it is declared in `moves` below.
    new.write_text(content.replace("_claim_license", "_claim_license_v2"), encoding="utf-8")

    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{
            "old_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "new_path": "plugins/setec-voiceprint/scripts/setec/surfaces/an_audit.py",
            "old_symbol": "_claim_license",
            "new_symbol": "_claim_license_v2",
            "phase": "P4",
        }],
        "path_rewrites": [],
    }), encoding="utf-8")

    passed, report = clg.run(base_ref="HEAD")
    assert passed, report["deltas"]


def test_moves_substring_laundering_is_blocked(fake_repo):
    """The attack this fix closes: a `moves` row whose old_symbol is a
    STRING-LEVEL SUBSTRING of a license sentence must not launder a
    content change. Before the fix, `_normalized_dump` did a raw
    `str.replace` on the whole `ast.dump()` TEXT, so
    old_symbol="REPORTS" / new_symbol="REFUSES" silently rewrote a
    `Constant` string containing "REPORTS" too — an inverted refusal
    sentence passed as a clean relocation. Confirmed exploitable against
    the pre-fix implementation before writing this test; must now fail
    the guard (no relocation actually happened — old_path == new_path,
    so there is nothing legitimate for the row to explain)."""
    scripts = fake_repo / "plugins" / "setec-voiceprint" / "scripts"
    audit = scripts / "an_audit.py"
    audit.write_text(
        "from claim_license import ClaimLicense\n\n"
        "def _claim_license():\n"
        "    return ClaimLicense(licenses='This audit REPORTS smoothing evidence')\n",
        encoding="utf-8",
    )
    # Same file, unchanged path — an inverted sentence, "explained" only
    # by a bogus old_symbol/new_symbol pair chosen to string-match text
    # inside the license constant.
    audit.write_text(
        "from claim_license import ClaimLicense\n\n"
        "def _claim_license():\n"
        "    return ClaimLicense(licenses='This audit REFUSES smoothing evidence')\n",
        encoding="utf-8",
    )
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{
            "old_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "new_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "old_symbol": "REPORTS",
            "new_symbol": "REFUSES",
            "phase": "P4",
        }],
        "path_rewrites": [],
    }), encoding="utf-8")

    passed, report = clg.run(base_ref="HEAD")
    assert not passed
    paths = {d["path"] for d in report["deltas"]}
    assert "plugins/setec-voiceprint/scripts/an_audit.py" in paths


def test_moves_rejects_non_identifier_symbol(fake_repo):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{
            "old_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "new_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "old_symbol": "REPORTS smoothing",  # not a legal identifier
            "new_symbol": "x",
            "phase": "P4",
        }],
        "path_rewrites": [],
    }), encoding="utf-8")
    with pytest.raises(clg.GuardError, match="not a legal Python identifier"):
        clg.load_move_map(move_map_path)


def test_moves_rejects_old_path_absent_at_merge_base(fake_repo):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{
            "old_path": "plugins/setec-voiceprint/scripts/does_not_exist.py",
            "new_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "old_symbol": "x",
            "new_symbol": "y",
            "phase": "P4",
        }],
        "path_rewrites": [],
    }), encoding="utf-8")
    with pytest.raises(clg.GuardError, match="does not resolve as a real git object"):
        clg.run(base_ref="HEAD")


def test_moves_rejects_new_path_absent_from_candidate(fake_repo):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{
            "old_path": "plugins/setec-voiceprint/scripts/an_audit.py",
            "new_path": "plugins/setec-voiceprint/scripts/nowhere.py",
            "old_symbol": "x",
            "new_symbol": "y",
            "phase": "P4",
        }],
        "path_rewrites": [],
    }), encoding="utf-8")
    with pytest.raises(clg.GuardError, match="does not resolve to a real file"):
        clg.run(base_ref="HEAD")


def test_structural_rename_never_touches_string_constants():
    """Unit-level pin on _StructuralRename directly: a Constant node
    with a value equal to a renamed symbol's TEXT must be left byte-
    identical, even though a Name with that same id is renamed."""
    import ast as ast_mod
    import copy as copy_mod
    tree = ast_mod.parse("x = REPORTS\ny = 'REPORTS'\n")
    renamed = clg._StructuralRename({"REPORTS": "REFUSES"}).visit(
        copy_mod.deepcopy(tree)
    )
    ast_mod.fix_missing_locations(renamed)
    dumped = ast_mod.dump(renamed, include_attributes=False)
    assert "id='REFUSES'" in dumped  # the Name was renamed
    assert "value='REPORTS'" in dumped  # the string Constant was NOT


def test_load_move_map_rejects_duplicate_new_path(fake_repo, monkeypatch):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [
            {
                "old_path": "a.py", "new_path": "c.py",
                "old_symbol": "x", "new_symbol": "x", "phase": "P4",
            },
            {
                "old_path": "b.py", "new_path": "c.py",
                "old_symbol": "y", "new_symbol": "y", "phase": "P4",
            },
        ],
        "path_rewrites": [],
    }), encoding="utf-8")
    with pytest.raises(clg.GuardError, match="second destination"):
        clg.load_move_map(move_map_path)


def test_load_move_map_rejects_malformed_row(fake_repo):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.write_text(json.dumps({
        "schema": 1,
        "moves": [{"old_path": "a.py"}],  # missing required keys
        "path_rewrites": [],
    }), encoding="utf-8")
    with pytest.raises(clg.GuardError, match="exactly keys"):
        clg.load_move_map(move_map_path)


def test_missing_move_map_fails_closed(fake_repo):
    move_map_path = fake_repo / "plugins" / "setec-voiceprint" / "packaging_move_map.json"
    move_map_path.unlink()
    with pytest.raises(clg.GuardError, match="is missing"):
        clg.load_move_map(move_map_path)


def test_supplier_closure_scans_the_whole_module_body_spec_literal(
    tmp_path, monkeypatch,
):
    """Build-review P1 finding #4: the closure must implement the
    spec-literal whole-module-body rule, not a narrower per-symbol
    scope. A seed module that references ONE function from a bare-
    imported utility module (`import helpers; ...
    helpers.used_fn(...)`) pulls the WHOLE utility module in as a
    supplier "in full" (spec §3) — including an UNRELATED function
    elsewhere in that module (`unused_fn`), whose own import of a
    third module is then followed too on the next hop. A prior,
    narrower revision of this function scoped supplier hops to just
    the referenced symbol's own definition; that was MORE precise but
    not what the spec describes, and it silently excluded a real
    supplier (`judge_backends.py`) from the protected set. Reverted —
    see build_protected_set's docstring."""
    monkeypatch.setattr(clg, "REPO_ROOT", tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # _module_path_for_import resolves plugin-local imports against the
    # module-global SCRIPTS_ROOT (not the scripts_root passed to
    # build_protected_set — that parameter only controls which files get
    # SEED-scanned), so it must be patched too for this synthetic tree's
    # imports to resolve as plugin-local.
    monkeypatch.setattr(clg, "SCRIPTS_ROOT", scripts)
    (scripts / "an_audit.py").write_text(
        "from claim_license import ClaimLicense\n"
        "import helpers\n\n"
        "def _claim_license():\n"
        "    return ClaimLicense(\n"
        "        licenses=helpers.used_fn(),\n"
        "    )\n",
        encoding="utf-8",
    )
    (scripts / "claim_license.py").write_text(
        "class ClaimLicense:\n"
        "    def __init__(self, licenses):\n"
        "        self.licenses = licenses\n",
        encoding="utf-8",
    )
    (scripts / "helpers.py").write_text(
        "import unrelated_third_module\n\n"
        "def used_fn():\n"
        "    return 'x'\n\n"
        "def unused_fn():\n"
        "    return unrelated_third_module.thing()\n",
        encoding="utf-8",
    )
    (scripts / "unrelated_third_module.py").write_text(
        "def thing():\n    return 1\n",
        encoding="utf-8",
    )

    protected = clg.build_protected_set(scripts_root=scripts)
    paths = {p.path for p in protected}
    assert "scripts/helpers.py" in paths
    assert "scripts/unrelated_third_module.py" in paths


def test_claim_license_surfaces_accessor_name_is_not_a_false_seed(tmp_path, monkeypatch):
    """Regression: a public accessor merely NAMED after the
    claim_license_surfaces/ directory (e.g. setec.paths'
    claim_license_surfaces_dir()) must not be swept into the protected
    set by a bare substring match on "claim_license" — only a name
    STARTING WITH `_claim_license` (the spec's literal `_claim_license*`
    glob) or a direct `ClaimLicense(...)` call qualifies."""
    monkeypatch.setattr(clg, "REPO_ROOT", tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "paths_like.py").write_text(
        "def claim_license_surfaces_dir(start=None):\n"
        "    return start\n",
        encoding="utf-8",
    )
    protected = clg.build_protected_set(scripts_root=scripts)
    assert protected == []
