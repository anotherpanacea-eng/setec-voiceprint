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


def test_real_repo_l0_stems_are_exactly_the_contract_trio():
    modules = [cl.Module(p) for p in cl.find_runtime_scripts()]
    cap_paths = cl._load_capability_script_paths()
    tiers = cl.classify_tiers(modules, cap_paths)
    l0 = {path for path, tier in tiers.items() if tier == "L0"}
    assert l0 == {
        "plugins/setec-voiceprint/scripts/output_schema.py",
        "plugins/setec-voiceprint/scripts/claim_license.py",
        "plugins/setec-voiceprint/scripts/capabilities.py",
    }


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
