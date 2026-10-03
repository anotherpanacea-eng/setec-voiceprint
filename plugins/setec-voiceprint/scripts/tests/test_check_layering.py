#!/usr/bin/env python3
"""Tests for tools/check_layering.py.

Pins:

  * The real repo's L0/L1/L2 layering, as measured today, passes the
    gate (every violation is exempted via the committed
    `layer_exemptions:` section).
  * Tier classification is predicate-based (capabilities.d fragment /
    `__main__` / `build_output(...)` emission), not directory-based.
  * A PLANTED L0-outbound, L1->L2, and L2->L2 violation are each
    caught when unexempted, and each passes once a matching
    `layer_exemptions` row is added — the "a gate that cannot fail is
    worthless" proof for all three enforced categories.
  * Cycles are reported but never gate (a planted 2-cycle between two
    L2 modules does not fail the check).
  * `--strict` catches a ghost exemption row and a merge-base ratchet
    violation (a new row beyond what's committed at the merge base);
    an empty-vs-absent-key merge base is a no-op (this PR's own
    bootstrap case).
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

import check_layering as cl  # type: ignore  # noqa: E402


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


# --------------------------- real-repo pins -----------------------------


def test_real_repo_layering_passes():
    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    cap_paths = cl._load_capability_script_paths()
    tiers = cl.classify_tiers(modules, cap_paths)
    edges = cl.build_internal_graph(modules)
    violations = cl.find_violations(edges, tiers)
    rows = cl.load_layer_exemptions()
    assert not cl.validate_layer_exemption_rows(rows)
    exempted = {cl._exemption_key(r) for r in rows}
    unexempted = [
        v for v in violations
        if (v.from_path, v.to_path, v.edge_kind) not in exempted
    ]
    assert not unexempted, [
        (v.from_path, v.to_path, v.edge_kind) for v in unexempted
    ]


def test_paragraph_alias_requires_its_metadata_row(monkeypatch, capsys):
    """A permanent launcher stays a visible edge, never an implicit bypass."""
    source = "plugins/setec-voiceprint/scripts/paragraph_audit.py"
    target = "plugins/setec-voiceprint/scripts/setec/surfaces/paragraph_audit.py"
    rows = cl.load_layer_exemptions()
    paragraph_rows = [r for r in rows if r["from_path"] == source and r["to_path"] == target]
    assert len(paragraph_rows) == 1
    monkeypatch.setattr(cl, "load_layer_exemptions", lambda: rows)
    assert cl.main(["--strict", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    monkeypatch.setattr(cl, "load_layer_exemptions", lambda: [r for r in rows if r not in paragraph_rows])
    assert cl.main(["--strict", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(v["from_path"] == source and v["to_path"] == target
               and v["edge_kind"] == "l2_to_l2" for v in report["unexempted"])


@pytest.mark.parametrize("case", [
    "proper_row", "missing_row", "missing_owner", "ghost_row",
    "arbitrary_import", "dynamic_alias", "wrong_main", "renamed_surface",
    "wrong_target", "unrelated_source", "extra_dependency", "side_effect",
])
def test_permanent_launcher_row_admission(tmp_path, monkeypatch, capsys, case):
    scripts = tmp_path / "plugins" / "setec-voiceprint" / "scripts"
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    exemptions = tmp_path / "plugins" / "setec-voiceprint" / "packaging_migration_exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions)
    # Isolate candidate admission against an existing, empty baseline.
    monkeypatch.setattr(cl, "_merge_base", lambda ref: "baseline")
    monkeypatch.setattr(cl, "_layer_exemptions_at", lambda sha: [])
    for package in ("setec", "setec/surfaces"):
        _write(scripts, package + "/__init__.py", "")
    implementation = (
        'TASK_SURFACE = "voice_coherence"\n'
        "def main():\n    return 0\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    _write(scripts, "setec/surfaces/audit.py", implementation)
    _write(scripts, "setec/surfaces/other.py", implementation)
    launcher = (
        '"""Permanent ordinary launcher."""\n'
        "import sys\nfrom pathlib import Path\n"
        "_SCRIPT_DIR = Path(__file__).resolve().parent\n"
        "if str(_SCRIPT_DIR) not in sys.path:\n"
        "    sys.path.insert(0, str(_SCRIPT_DIR))\n"
        "from setec.surfaces import audit as _mod\n"
        "from setec.surfaces.audit import TASK_SURFACE\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(_mod.main())\n"
        "else:\n    sys.modules[__name__] = _mod\n"
    )
    prefix = "plugins/setec-voiceprint/scripts/"
    source = "audit.py"
    target = "setec/surfaces/audit.py"
    if case == "arbitrary_import":
        launcher = "from setec.surfaces.audit import TASK_SURFACE\n"
    elif case == "dynamic_alias":
        launcher = launcher.replace("sys.modules[__name__] = _mod", "globals().update(vars(_mod))")
    elif case == "wrong_main":
        launcher = launcher.replace("sys.exit(_mod.main())", "sys.modules['__main__'] = _mod")
    elif case == "renamed_surface":
        launcher = launcher.replace("import TASK_SURFACE", "import TASK_SURFACE as OTHER")
    elif case == "wrong_target":
        launcher = launcher.replace("import audit as _mod", "import other as _mod")
        launcher = launcher.replace("surfaces.audit import", "surfaces.other import")
        target = "setec/surfaces/other.py"
    elif case == "unrelated_source":
        source = "another.py"
    elif case == "extra_dependency":
        launcher += "from setec.surfaces import other\n"
    elif case == "side_effect":
        launcher += "_mod.main()\n"
    elif case == "ghost_row":
        launcher = implementation
    _write(scripts, source, launcher)
    monkeypatch.setattr(cl, "_load_capability_script_paths", lambda: {prefix + source})
    row = {
        "from_path": prefix + source, "to_path": prefix + target,
        "edge_kind": "l2_to_l2", "reason": "Permanent ordinary launcher to its own implementation",
        "owner": "packaging", "introduced_sha": "baseline", "removal_phase": "not-applicable",
    }
    rows = [row]
    if case == "missing_row":
        rows = []
    elif case == "missing_owner":
        del row["owner"]
    elif case == "extra_dependency":
        rows.append(dict(row, to_path=prefix + "setec/surfaces/other.py"))
    exemptions.write_text(json.dumps({"layer_exemptions": rows}), encoding="utf-8")
    assert cl.main(["--strict", "--json"]) == (0 if case == "proper_row" else 1)
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is (case == "proper_row")
    if case == "proper_row":
        assert report["counts"]["l2_to_l2"] == 1
        assert report["strict_problems"] == []
    elif case == "missing_row":
        assert report["unexempted"]
    elif case == "missing_owner":
        assert report["exemption_shape_problems"]
    elif case == "ghost_row":
        assert any("ghost/expired" in problem for problem in report["strict_problems"])
    else:
        assert any(
            problem.startswith(f"{row['from_path']} -> {row['to_path']} ")
            and "NEW layer_exemptions row" in problem
            for problem in report["strict_problems"]
        )


@pytest.mark.parametrize("location", ["", "setec/contract/"])
@pytest.mark.parametrize("stem", ["output_schema", "claim_license", "capabilities"])
def test_contract_outbound_prohibition_survives_relocation(tmp_path, monkeypatch, location, stem):
    _, _, _, violations = _synthetic_check(
        tmp_path, monkeypatch,
        files={location + stem + ".py": "import a_surface\n",
               "a_surface.py": "if __name__ == '__main__':\n    pass\n"},
        cap_paths=set(),
    )
    assert any(v.edge_kind == "l0_outbound" for v in violations)


def test_real_repo_cycles_are_reported_and_known_count():
    """Informational only. This pins the CURRENT measured cycle count so a
    silent regression (a NEW cycle) is visible in a test diff even though
    the gate itself never fails on cycles. If this test breaks because a
    real new cycle appeared, that's a signal to look, not to blindly bump
    the number."""
    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    edges = cl.build_internal_graph(modules)
    cycles = cl.find_cycles(edges)
    assert len(cycles) == 5


# --------------------------- tier predicates -----------------------------


def test_tier_classification_is_predicate_not_directory(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)

    # Pure library: no capability fragment, no __main__, no build_output.
    _write(scripts, "pure_lib.py", "def helper():\n    return 1\n")
    # Has a __main__ guard -> L2, even with zero capability fragment.
    _write(
        scripts, "cli_only.py",
        "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n",
    )
    # Emits an envelope via build_output -> L2.
    _write(
        scripts, "emits_envelope.py",
        "from output_schema import build_output\n"
        "def go():\n    return build_output(task_surface='x')\n",
    )
    # Nested subdirectory module with NO capabilities fragment and no
    # __main__/build_output -- still L1, because classification is by
    # predicate, not by "lives at the top level".
    _write(scripts, "calibration/nested_pure_lib.py", "VALUE = 1\n")

    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    by_rel = {m.rel.as_posix(): m for m in modules}
    cap_paths: set[str] = set()  # no capabilities.d fragments in this synthetic tree
    tiers = cl.classify_tiers(modules, cap_paths)

    def tier_of(rel: str) -> str:
        m = by_rel[rel]
        return tiers[m.repo_rel]

    assert tier_of("pure_lib.py") == "L1"
    assert tier_of("cli_only.py") == "L2"
    assert tier_of("emits_envelope.py") == "L2"
    assert tier_of("calibration/nested_pure_lib.py") == "L1"


def test_capability_fragment_predicate_promotes_to_l2(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    _write(scripts, "has_fragment.py", "VALUE = 1\n")
    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    repo_rel = modules[0].repo_rel
    tiers = cl.classify_tiers(modules, {repo_rel})
    assert tiers[repo_rel] == "L2"


# --------------------------- planted violations ---------------------------


def _synthetic_check(tmp_path, monkeypatch, files: dict[str, str], cap_paths: set[str]):
    scripts = tmp_path / "scripts"
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", tmp_path / "packaging_migration_exemptions.yaml")
    monkeypatch.setattr(cl, "_load_capability_script_paths", lambda: cap_paths)
    for rel, content in files.items():
        _write(scripts, rel, content)
    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    tiers = cl.classify_tiers(modules, cap_paths)
    edges = cl.build_internal_graph(modules)
    violations = cl.find_violations(edges, tiers)
    return modules, tiers, edges, violations


def test_planted_l0_outbound_violation_is_caught(tmp_path, monkeypatch):
    _, tiers, edges, violations = _synthetic_check(
        tmp_path, monkeypatch,
        files={
            # output_schema is an L0 module by stem predicate; make it
            # import a real L2 surface -- a genuine layering break.
            "output_schema.py": "import a_surface\n",
            "a_surface.py": (
                "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
            ),
        },
        cap_paths=set(),
    )
    kinds = {v.edge_kind for v in violations}
    assert "l0_outbound" in kinds
    l0 = [v for v in violations if v.edge_kind == "l0_outbound"]
    assert l0[0].to_path.endswith("a_surface.py")

    exempted: set[tuple[str, str, str]] = set()
    unexempted = [
        v for v in violations if (v.from_path, v.to_path, v.edge_kind) not in exempted
    ]
    assert any(v.edge_kind == "l0_outbound" for v in unexempted)


def test_planted_l1_to_l2_violation_is_caught(tmp_path, monkeypatch):
    _, tiers, edges, violations = _synthetic_check(
        tmp_path, monkeypatch,
        files={
            "pure_lib.py": "import a_surface\n",  # L1 -> L2
            "a_surface.py": (
                "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
            ),
        },
        cap_paths=set(),
    )
    l1_to_l2 = [v for v in violations if v.edge_kind == "l1_to_l2"]
    assert len(l1_to_l2) == 1
    assert l1_to_l2[0].from_path.endswith("pure_lib.py")
    assert l1_to_l2[0].to_path.endswith("a_surface.py")


def test_planted_l2_to_l2_violation_is_caught_and_exemption_clears_it(tmp_path, monkeypatch):
    files = {
        "surface_a.py": (
            "import surface_b\n"
            "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
        ),
        "surface_b.py": (
            "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
        ),
    }
    _, tiers, edges, violations = _synthetic_check(
        tmp_path, monkeypatch, files=files, cap_paths=set(),
    )
    l2_to_l2 = [v for v in violations if v.edge_kind == "l2_to_l2"]
    assert len(l2_to_l2) == 1
    v = l2_to_l2[0]
    assert v.from_path.endswith("surface_a.py") and v.to_path.endswith("surface_b.py")

    # Unexempted -> fails.
    exempted: set[tuple[str, str, str]] = set()
    unexempted = [
        x for x in violations if (x.from_path, x.to_path, x.edge_kind) not in exempted
    ]
    assert unexempted

    # A matching exemption row clears exactly this violation.
    exempted = {(v.from_path, v.to_path, v.edge_kind)}
    unexempted = [
        x for x in violations if (x.from_path, x.to_path, x.edge_kind) not in exempted
    ]
    assert not unexempted


def test_planted_cycle_is_reported_not_gated(tmp_path, monkeypatch):
    files = {
        "surface_a.py": (
            "import surface_b\n"
            "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
        ),
        "surface_b.py": (
            "import surface_a\n"
            "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
        ),
    }
    _, tiers, edges, violations = _synthetic_check(
        tmp_path, monkeypatch, files=files, cap_paths=set(),
    )
    cycles = cl.find_cycles(edges)
    assert len(cycles) == 1
    assert {p.rsplit("/", 1)[-1] for p in cycles[0]} == {"surface_a.py", "surface_b.py"}
    # Both directions are real L2->L2 violations (gated), independent of
    # the cycle itself (never gated) -- the cycle finder doesn't suppress
    # or replace the edge-level violations.
    l2_to_l2 = {(v.from_path, v.to_path) for v in violations if v.edge_kind == "l2_to_l2"}
    assert len(l2_to_l2) == 2


# --------------------------- exemption shape / ghost / ratchet -----------


@pytest.mark.parametrize("bad_row,expected_fragment", [
    ({"from_path": "a.py", "to_path": "b.py", "edge_kind": "l2_to_l2",
      "reason": "r", "owner": "o", "introduced_sha": "s"}, "removal_phase"),
    ({"from_path": "a.py", "to_path": "b.py", "edge_kind": "bogus_kind",
      "reason": "r", "owner": "o", "introduced_sha": "s",
      "removal_phase": "not-applicable"}, "edge_kind"),
    ({"from_path": "a.py", "to_path": "b.py", "edge_kind": "l2_to_l2",
      "reason": "r", "owner": "o", "introduced_sha": "s",
      "removal_phase": "P99"}, "not in"),
])
def test_validate_layer_exemption_rows_catches_shape_problems(bad_row, expected_fragment):
    problems = cl.validate_layer_exemption_rows([bad_row])
    assert problems
    assert any(expected_fragment in p for p in problems)


def test_check_ghost_rows_catches_a_row_with_no_matching_violation():
    violations = [cl.Violation("a.py", "b.py", "l2_to_l2")]
    rows = [{
        "from_path": "x.py", "to_path": "y.py", "edge_kind": "l2_to_l2",
        "reason": "r", "owner": "o", "introduced_sha": "s", "removal_phase": "not-applicable",
    }]
    problems = cl.check_ghost_rows(rows, violations)
    assert len(problems) == 1
    assert "x.py" in problems[0]


def test_check_ratchet_flags_a_new_row_beyond_a_committed_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions_path)
    exemptions_path.write_text(
        "schema_version: 1\nlayer_exemptions: []\n", encoding="utf-8",
    )
    sha = _init_git_repo(tmp_path)
    exemptions_path.write_text(
        json.dumps({"schema_version": 1, "layer_exemptions": [{
            "from_path": "a.py", "to_path": "b.py", "edge_kind": "l2_to_l2",
            "reason": "r", "owner": "o", "introduced_sha": sha,
            "removal_phase": "not-applicable",
        }]}), encoding="utf-8",
    )
    problems = cl.check_ratchet(sha)
    assert problems  # committed baseline had zero rows; this adds one


@pytest.mark.parametrize("target", ["s5_distance.py", "setec/consumer_client.py"])
def test_ratchet_preserves_only_existing_p2_dependencies(tmp_path, monkeypatch, target):
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions_path)
    prefix = "plugins/setec-voiceprint/scripts/"
    row = {"from_path": prefix + "capabilities.py", "to_path": prefix + target,
           "edge_kind": "l0_outbound", "reason": "existing dependency", "owner": "packaging",
           "introduced_sha": "base", "removal_phase": "not-applicable"}
    exemptions_path.write_text(json.dumps({"layer_exemptions": [row]}), encoding="utf-8")
    sha = _init_git_repo(tmp_path)
    row["from_path"] = prefix + "setec/contract/capabilities.py"
    exemptions_path.write_text(json.dumps({"layer_exemptions": [row]}), encoding="utf-8")
    assert cl.check_ratchet(sha) == []
    # Ghost matching uses the new real path, not a normalization of the graph.
    assert cl.check_ghost_rows([row], [cl.Violation(row["from_path"], row["to_path"], row["edge_kind"])]) == []
    assert cl.check_ghost_rows([row], [])
    for field, value in [("to_path", prefix + "new_dependency.py"),
                         ("from_path", prefix + "setec/other/capabilities.py"),
                         ("edge_kind", "l2_to_l2")]:
        changed = dict(row, **{field: value})
        exemptions_path.write_text(json.dumps({"layer_exemptions": [changed]}), encoding="utf-8")
        assert cl.check_ratchet(sha)


@pytest.mark.parametrize("case", [
    "both_endpoints", "source_only", "target_only", "new_target", "new_source",
    "changed_kind", "malformed_launcher", "wrong_target", "extra_dependency",
])
def test_surface_relocation_ratchet_preserves_only_existing_edges(tmp_path, monkeypatch, case):
    prefix = "plugins/setec-voiceprint/scripts/"
    scripts = tmp_path / prefix
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    for package in ("setec", "setec/surfaces"):
        _write(scripts, package + "/__init__.py", "")
    implementation = 'TASK_SURFACE = "smoothing_diagnosis"\ndef main():\n    return 0\n'
    for stem in ("audit", "baseline", "other"):
        _write(scripts, "setec/surfaces/" + stem + ".py", implementation)
        launcher = (
            "import sys\nfrom pathlib import Path\n"
            "_SCRIPT_DIR = Path(__file__).resolve().parent\n"
            "if str(_SCRIPT_DIR) not in sys.path:\n    sys.path.insert(0, str(_SCRIPT_DIR))\n"
            f"from setec.surfaces import {stem} as _mod\n"
            f"from setec.surfaces.{stem} import TASK_SURFACE\n"
            "if __name__ == '__main__':\n    sys.exit(_mod.main())\n"
            "else:\n    sys.modules[__name__] = _mod\n"
        )
        if stem == "audit":
            if case == "malformed_launcher":
                launcher = launcher.replace("sys.modules[__name__] = _mod", "globals().update(vars(_mod))")
            elif case == "wrong_target":
                launcher = launcher.replace("import audit as", "import other as").replace("surfaces.audit import", "surfaces.other import")
            elif case == "extra_dependency":
                launcher += "from setec.surfaces import other\n"
        _write(scripts, stem + ".py", launcher)
    old = {"from_path": prefix + "audit.py", "to_path": prefix + "baseline.py",
           "edge_kind": "l2_to_l2", "reason": "existing edge", "owner": "packaging",
           "introduced_sha": "base", "removal_phase": "not-applicable"}
    new = dict(old, from_path=prefix + "setec/surfaces/audit.py",
               to_path=prefix + "setec/surfaces/baseline.py")
    if case == "source_only":
        new["to_path"] = old["to_path"]
    elif case == "target_only":
        new["from_path"] = old["from_path"]
    elif case == "new_target":
        new["to_path"] = prefix + "setec/surfaces/other.py"
    elif case == "new_source":
        new["from_path"] = prefix + "setec/surfaces/other.py"
    elif case == "changed_kind":
        new["edge_kind"] = "l1_to_l2"
    monkeypatch.setattr(cl, "_layer_exemptions_at", lambda sha: [old])
    monkeypatch.setattr(cl, "load_layer_exemptions", lambda: [new])
    problems = cl.check_ratchet("base")
    assert bool(problems) is (case not in {"both_endpoints", "source_only", "target_only"})
    # Matching and ghost checks retain the actual new endpoints.
    assert not cl.check_ghost_rows([new], [cl.Violation(new["from_path"], new["to_path"], new["edge_kind"])])
    assert cl.check_ghost_rows([new], [])


@pytest.mark.parametrize("stem", [
    "repetition_audit", "manuscript_repetition_audit", "chapter_distinctiveness_audit",
])
def test_repetition_alias_requires_its_metadata_row(monkeypatch, capsys, stem):
    source = "plugins/setec-voiceprint/scripts/" + stem + ".py"
    target = "plugins/setec-voiceprint/scripts/setec/surfaces/" + stem + ".py"
    rows = cl.load_layer_exemptions()
    missing = [r for r in rows if (r["from_path"], r["to_path"]) != (source, target)]
    assert len(rows) - len(missing) == 1
    assert cl.main(["--strict", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    monkeypatch.setattr(cl, "load_layer_exemptions", lambda: missing)
    assert cl.main(["--strict", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(v["from_path"] == source and v["to_path"] == target for v in report["unexempted"])


def test_l0_path_plumbing_does_not_allow_other_internal_dependencies():
    contract = "plugins/setec-voiceprint/scripts/setec/contract/claim_license.py"
    paths = "plugins/setec-voiceprint/scripts/setec/paths.py"
    other = "plugins/setec-voiceprint/scripts/setec/core/helper.py"
    tiers = {contract: "L0", paths: "L1", other: "L1"}
    violations = cl.find_violations({(contract, paths), (contract, other)}, tiers)
    assert [(v.from_path, v.to_path, v.edge_kind) for v in violations] == [(contract, other, "l0_outbound")]
    # The resolver remains visible: its own downward coupling is still a violation.
    surface = "plugins/setec-voiceprint/scripts/surface.py"
    assert cl.find_violations({(paths, surface)}, {paths: "L1", surface: "L2"})


def test_check_ratchet_allows_only_the_generator_hub_addition(tmp_path, monkeypatch):
    """A surface joining the R5 golden regime adds one generator edge; that
    row may be added.  Any other from_path -- including a surface importing
    the generator, the reverse direction -- stays shrink-only."""
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions_path)
    exemptions_path.write_text(
        "schema_version: 1\nlayer_exemptions: []\n", encoding="utf-8",
    )
    sha = _init_git_repo(tmp_path)
    def rows(from_path, to_path):
        return [{
            "from_path": from_path, "to_path": to_path,
            "edge_kind": "l2_to_l2", "reason": "r", "owner": "o",
            "introduced_sha": sha, "removal_phase": "not-applicable",
        }]
    generator = cl._GENERATOR_FROM_PATH
    surface = "plugins/setec-voiceprint/scripts/setec/surfaces/x.py"
    exemptions_path.write_text(
        json.dumps({"schema_version": 1,
                    "layer_exemptions": rows(generator, surface)}),
        encoding="utf-8")
    assert cl.check_ratchet(sha) == []  # sanctioned: generator -> surface
    exemptions_path.write_text(
        json.dumps({"schema_version": 1,
                    "layer_exemptions": rows(surface, generator)}),
        encoding="utf-8")
    assert cl.check_ratchet(sha)  # reverse direction still refuses
    exemptions_path.write_text(
        json.dumps({"schema_version": 1,
                    "layer_exemptions": [{
                        "from_path": generator, "to_path": surface,
                        "edge_kind": "l1_to_l2", "reason": "r", "owner": "o",
                        "introduced_sha": sha,
                        "removal_phase": "not-applicable"}]}),
        encoding="utf-8")
    assert cl.check_ratchet(sha)  # other edge kinds from the generator too


def test_check_ratchet_absent_key_at_merge_base_is_a_no_op(tmp_path, monkeypatch):
    """This PR's own bootstrap situation: the merge base's file exists but
    has no `layer_exemptions` key at all -- nothing to ratchet against,
    not the same as "a committed empty list"."""
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions_path)
    exemptions_path.write_text(
        "schema_version: 1\nexemptions: []\n", encoding="utf-8",
    )  # no layer_exemptions key yet
    sha = _init_git_repo(tmp_path)
    exemptions_path.write_text(
        json.dumps({"schema_version": 1, "exemptions": [], "layer_exemptions": [{
            "from_path": "a.py", "to_path": "b.py", "edge_kind": "l2_to_l2",
            "reason": "r", "owner": "o", "introduced_sha": sha,
            "removal_phase": "not-applicable",
        }]}), encoding="utf-8",
    )
    problems = cl.check_ratchet(sha)
    assert problems == []


def test_seed_preserves_exemptions_key_and_check_round_trips(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    monkeypatch.setattr(cl, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cl, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cl, "EXEMPTIONS_PATH", exemptions_path)
    monkeypatch.setattr(cl, "_load_capability_script_paths", lambda: set())
    exemptions_path.write_text(
        "schema_version: 1\nexemptions:\n- path: x.py\n  symbol: Y\n", encoding="utf-8",
    )
    _write(scripts, "surface_a.py", (
        "import surface_b\n"
        "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
    ))
    _write(scripts, "surface_b.py", (
        "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
    ))

    class Args:
        introduced_sha = "deadbeef"

    assert cl.cmd_seed(Args()) == 0

    import yaml
    doc = yaml.safe_load(exemptions_path.read_text(encoding="utf-8"))
    assert doc["exemptions"] == [{"path": "x.py", "symbol": "Y"}]
    assert len(doc["layer_exemptions"]) == 1

    rows = cl.load_layer_exemptions()
    assert not cl.validate_layer_exemption_rows(rows)
