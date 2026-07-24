#!/usr/bin/env python3
"""Coverage drift test for the passage-dedup pool guard (spec 36 M1).

The hard constraint the guard exists for: **duplicate-dependent set-level-diversity
surfaces must not consume a passage-deduped corpus.** Collapse / homogeneity /
template-reuse / leave-one-out novelty are signals that live *in* the retained
duplicates, so deduping their pool destroys the measured object. This repo's
recurring bug class (#306/#307) is exactly this — dedup applied to a pool whose
purpose is diversity measurement.

A prose warning is not the fix, and neither is a kwarg on one shared loader: the
pool-loader class contains clean-room copies a shared signature cannot reach. The
fix is a producer stamp + a file-level `pool_guard` scan called per surface + this
test, which pins the **complete** classification map so the guard cannot silently
fall behind a new surface.

Three closure sweeps and one obligation check:

  (a) the set-level-diversity axis is closed — every module declaring
      ``TASK_SURFACE = "set_level_diversity"`` is classified;
  (b) the loader-DEFINER sweep is closed — every module defining a top-level
      ``_load_manifest`` / ``_load_reference_manifest`` / ``_load_reference_dir``
      is classified (note the deliberate exclusion of the *prefix* family:
      ``_load_manifest_records``/``_load_manifest_entries``/``_load_manifest_dir``
      are single-consumer private helpers, not the shared pool-loader shape);
  (c) the loader-IMPORTER sweep is closed — matched on the imported *names*, not
      the source module, because ``cross_doc_argument_consistency`` imports them
      from ``cross_doc_novelty_profile`` rather than from ``originality_audit``;
  (d) every classified module is either guarded (imports AND calls `pool_guard`)
      or exempt **with a non-empty rationale**.

(d) is what keeps "add a name to a list" from being the cheapest green: an exempt
entry without a rationale is itself a failure, and classification is by
duplicate-dependence of the MEASUREMENT (the #306/#307 comparison-vs-diversity
purpose rule), not by surface tag — tag and classification diverge in both
directions here.

The sweeps are source scans (the repo's drift-linter style) because no single
behavioral harness can generically drive nine CLIs; the behavioral refusals and
negative controls are pinned per surface, in each surface's own test file.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pool_guard  # type: ignore  # noqa: E402

# Scope: the non-recursive script glob. Subdirectories (`tests/`, `runners/`,
# `calibration/`, `oracle/`, `replication/`, `external_mirror/`) are fixtures and
# operator tooling, not envelope-emitting surfaces, and a non-recursive glob
# excludes them by construction.
POOL_LOADER_DEF_NAMES = frozenset({
    "_load_manifest", "_load_reference_manifest", "_load_reference_dir",
})
POOL_LOADER_IMPORT_NAMES = frozenset({
    "_load_reference_manifest", "_load_reference_dir",
})
DIVERSITY_SURFACE = "set_level_diversity"

FIRES = "FIRES"
EXEMPT = "EXEMPT"

# The spec-36 classification map, verbatim. EVERY entry carries a rationale —
# firing and exempt alike — and an entry without one fails this module's own
# structural check below.
CLASSIFICATION: dict[str, dict[str, str]] = {
    "corpus_novelty_audit": {
        "surface": DIVERSITY_SURFACE,
        "loader": "importer",
        "guard": FIRES,
        "rationale": (
            "leave-one-out novelty — a repeated passage IS the low-novelty signal, so "
            "removing it removes the measurement"
        ),
    },
    "homogeneity_audit": {
        "surface": DIVERSITY_SURFACE,
        "loader": "definer",
        "guard": FIRES,
        "rationale": (
            "pool collapse is the read; its own _load_manifest only 'mirrors' the shared "
            "loader's shape (it imports nothing), which is exactly why a shared-loader "
            "kwarg could not reach it"
        ),
    },
    "distinct_diversity_audit": {
        "surface": DIVERSITY_SURFACE,
        "loader": "definer",
        "guard": FIRES,
        "rationale": "cluster sizes are the read — the partition IS the measured object",
    },
    "skeleton_overlap_audit": {
        "surface": DIVERSITY_SURFACE,
        "loader": "importer",
        "guard": FIRES,
        "rationale": "cross-document skeleton REUSE is the read",
    },
    "cross_doc_novelty_profile": {
        "surface": DIVERSITY_SURFACE,
        "loader": "definer",
        "guard": FIRES,
        "rationale": (
            "the pool mean/SD as-it-is (including a collapsed mode) is the measured "
            "object; silent dedup widens the SD and inflates apparent novelty"
        ),
    },
    "originality_audit": {
        "surface": DIVERSITY_SURFACE,
        "loader": "definer",
        "guard": EXEMPT,
        "rationale": (
            "pinned negative control: duplicate pool members are IDEMPOTENT for "
            "longest-match coverage — copy count provably cannot change the result, so "
            "the measurement is not duplicate-dependent despite the surface tag"
        ),
    },
    "cross_doc_argument_consistency": {
        "surface": "argument_consistency",
        "loader": "importer",
        "guard": EXEMPT,
        "rationale": (
            "claim-consistency COMPARISON across one author's documents; not "
            "duplicate-dependent. It imports the shared loaders from "
            "cross_doc_novelty_profile, not originality_audit — which is why sweep (c) "
            "keys on the imported names rather than the source module"
        ),
    },
    "general_imposters": {
        "surface": "voice_coherence",
        "loader": "definer",
        "guard": EXEMPT,
        "rationale": (
            "impostor-pool COMPARISON consumer — near-dup dedup of an impostor pool is "
            "legitimate and sometimes required (the #306/#307 purpose rule); firing here "
            "would be the exact inversion the guard forbids"
        ),
    },
    "binoculars_calibrate": {
        "surface": "calibration",
        "loader": "definer",
        "guard": EXEMPT,
        "rationale": (
            "labeled-corpus calibration scoring loader — a labeled eval set, not a "
            "diversity pool"
        ),
    },
}


# ---------------- structural sweeps (scope-parameterized) ----------------


def _module_sources(scope: Path) -> dict[str, str]:
    """``{module_stem: source}`` for the non-recursive ``*.py`` glob of ``scope``.

    No name filter: an underscore-prefixed module (``_mirror_gate.py``) is still a
    module that could grow a pool loader, and narrowing the glob is exactly how a
    closure sweep quietly stops closing.
    """
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted(scope.glob("*.py"))
    }


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def _declares_diversity_surface(src: str) -> bool:
    """``TASK_SURFACE = "set_level_diversity"`` at module top level.

    Parsed structurally (the shape `check_capabilities_drift.py` uses) rather
    than by regex, so a reformatted assignment cannot slip past the sweep.
    """
    for node in _tree(src).body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "TASK_SURFACE"
                and isinstance(node.value, ast.Constant)
                and node.value.value == DIVERSITY_SURFACE
            ):
                return True
    return False


def _defines_pool_loader(src: str) -> bool:
    """A MODULE-TOP-LEVEL def whose name is exactly a pool-loader name.

    This is the structural form of the spec's anchored ``^def _load_manifest\\(``
    predicate: top-level-only (so a nested helper doesn't count) and exact-name
    (so the prefix family — ``_load_manifest_records``, ``_load_manifest_entries``,
    ``_load_manifest_dir`` — is excluded, as intended: those are single-consumer
    private helpers, not the shared pool-loader shape).
    """
    return any(
        isinstance(node, ast.FunctionDef) and node.name in POOL_LOADER_DEF_NAMES
        for node in _tree(src).body
    )


def _imports_pool_loader(src: str) -> bool:
    """An import statement that BINDS a shared pool-loader name, from anywhere."""
    for node in ast.walk(_tree(src)):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            if bound in POOL_LOADER_IMPORT_NAMES:
                return True
    return False


def _calls_pool_guard(src: str) -> bool:
    """The module imports pool_guard AND actually calls the scanner."""
    tree = _tree(src)
    imported = any(
        isinstance(node, ast.Import)
        and any(a.name == "pool_guard" for a in node.names)
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, ast.ImportFrom) and node.module == "pool_guard"
        for node in ast.walk(tree)
    )
    called = any(
        isinstance(node, ast.Attribute)
        and node.attr == "scan_manifest_for_passage_dedup"
        for node in ast.walk(tree)
    )
    return imported and called


# ---------------- the map's own integrity ----------------


def test_every_classification_entry_carries_a_rationale():
    """(d)'s teeth: a rationale-free entry is a failure, so widening a name list
    is never the cheapest way back to green."""
    for module, row in CLASSIFICATION.items():
        assert row["guard"] in (FIRES, EXEMPT), module
        assert row["loader"] in ("definer", "importer"), module
        assert isinstance(row.get("rationale"), str), module
        assert len(row["rationale"].strip()) >= 40, (
            f"{module}: rationale must state WHY the measurement is (or is not) "
            "duplicate-dependent, not just assert a classification"
        )


# ---------------- (a) axis enumeration is closed ----------------


def test_diversity_axis_enumeration_is_closed():
    sources = _module_sources(SCRIPTS)
    found = {m for m, src in sources.items() if _declares_diversity_surface(src)}
    expected = {m for m, r in CLASSIFICATION.items() if r["surface"] == DIVERSITY_SURFACE}
    assert found == expected, (
        "a set_level_diversity surface is unclassified (or a classified one moved): "
        f"unclassified={sorted(found - expected)}, missing={sorted(expected - found)}"
    )


# ---------------- (b) loader-definer sweep is closed ----------------


def test_loader_definer_sweep_is_closed():
    sources = _module_sources(SCRIPTS)
    found = {m for m, src in sources.items() if _defines_pool_loader(src)}
    expected = {m for m, r in CLASSIFICATION.items() if r["loader"] == "definer"}
    assert found == expected, (
        "a pool-loader definition (or clean-room copy) is unclassified: "
        f"unclassified={sorted(found - expected)}, missing={sorted(expected - found)}"
    )


def test_prefix_family_is_deliberately_out_of_scope():
    """The exclusion is intentional and pinned: these are single-consumer private
    helpers, so a same-prefix name must NOT drag a module into the sweep."""
    for module in ("near_dup_dedup", "house_style_decomposition", "pov_voice_profile",
                   "voice_drift_tracker"):
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        assert "_load_manifest" in src, f"{module} fixture drift: expected a prefixed name"
        assert not _defines_pool_loader(src), module


# ---------------- (c) loader-importer sweep is closed ----------------


def test_loader_importer_sweep_is_closed():
    sources = _module_sources(SCRIPTS)
    found = {m for m, src in sources.items() if _imports_pool_loader(src)}
    expected = {m for m, r in CLASSIFICATION.items() if r["loader"] == "importer"}
    assert found == expected, (
        "a module binds a shared pool-loader name but is unclassified: "
        f"unclassified={sorted(found - expected)}, missing={sorted(expected - found)}"
    )


def test_importer_sweep_keys_on_names_not_source_module():
    """The predicate that catches cross_doc_argument_consistency: it imports the
    loaders from cross_doc_novelty_profile, so a source-module-keyed sweep misses
    it entirely."""
    src = (SCRIPTS / "cross_doc_argument_consistency.py").read_text(encoding="utf-8")
    assert _imports_pool_loader(src)
    assert "from cross_doc_novelty_profile import" in src
    assert "from originality_audit import" not in src


# ---------------- (d) guarded-or-exempt-with-rationale ----------------


def test_fires_surfaces_import_and_call_the_guard():
    for module, row in CLASSIFICATION.items():
        if row["guard"] != FIRES:
            continue
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        assert _calls_pool_guard(src), (
            f"{module} is classified FIRES but does not import + call pool_guard"
        )


def test_exempt_surfaces_do_not_call_the_guard():
    """The inverse obligation: bolting the guard onto a COMPARISON pool is the
    exact inversion #306/#307 warns about, so exemption is enforced too."""
    for module, row in CLASSIFICATION.items():
        if row["guard"] != EXEMPT:
            continue
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        assert not _calls_pool_guard(src), (
            f"{module} is classified EXEMPT but calls pool_guard; deduping a "
            "comparison/calibration pool is legitimate and must not be refused"
        )


def test_no_classified_module_is_missing_from_the_tree():
    for module in CLASSIFICATION:
        assert (SCRIPTS / f"{module}.py").is_file(), module


# ---------------- self-test of the sweep ----------------


def test_sweep_catches_an_unclassified_synthetic_loader(tmp_path):
    """A synthetic module placed under the scope with a matching loader def and no
    classification must make the closure sweep fail — otherwise the sweep is
    decorative."""
    (tmp_path / "synthetic_pool_surface.py").write_text(
        'TASK_SURFACE = "set_level_diversity"\n\n\n'
        "def _load_manifest(path):\n    return []\n",
        encoding="utf-8",
    )
    sources = _module_sources(tmp_path)
    assert _declares_diversity_surface(sources["synthetic_pool_surface"])
    assert _defines_pool_loader(sources["synthetic_pool_surface"])
    assert "synthetic_pool_surface" not in CLASSIFICATION
    # The same comparison the closure tests make, against the synthetic scope.
    found_defs = {m for m, src in sources.items() if _defines_pool_loader(src)}
    expected = {m for m, r in CLASSIFICATION.items() if r["loader"] == "definer"}
    assert found_defs - expected == {"synthetic_pool_surface"}


def test_sweep_ignores_a_nested_or_prefixed_definition(tmp_path):
    """False-positive control: only top-level, exact-name defs count."""
    (tmp_path / "not_a_pool_surface.py").write_text(
        "def outer():\n"
        "    def _load_manifest(path):\n        return []\n"
        "    return _load_manifest\n\n\n"
        "def _load_manifest_records(path):\n    return []\n",
        encoding="utf-8",
    )
    sources = _module_sources(tmp_path)
    assert not _defines_pool_loader(sources["not_a_pool_surface"])


# =====================================================================
# pool_guard unit pins (spec 36 test contract 20)
# =====================================================================


def _write(tmp_path, rows, name="m.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_marked_row_is_reported_with_id_and_line(tmp_path):
    m = _write(tmp_path, [
        '{"id": "clean", "text": "a"}',
        '{"id": "marked", "text": "b", "passage_dedup": {"source_doc_id": "d"}}',
    ])
    marked = pool_guard.scan_manifest_for_passage_dedup(m)
    assert marked == ["marked (line 2)"]


def test_unmarked_manifest_scans_empty(tmp_path):
    m = _write(tmp_path, ['{"id": "a", "text": "x"}', '{"id": "b", "text": "y"}'])
    assert pool_guard.scan_manifest_for_passage_dedup(m) == []


def test_malformed_lines_are_skipped_without_crashing(tmp_path):
    m = _write(tmp_path, [
        "{not json at all",
        "",
        "[1, 2, 3]",
        '"a bare string"',
        '{"id": "marked", "passage_dedup": {}}',
    ])
    assert pool_guard.scan_manifest_for_passage_dedup(m) == ["marked (line 5)"]


def test_missing_path_scans_empty_rather_than_raising(tmp_path):
    """The caller's own loader reports the read error with its `bad_input`
    envelope; the guard must not pre-empt it with a different failure."""
    assert pool_guard.scan_manifest_for_passage_dedup(tmp_path / "nope.jsonl") == []


def test_id_fallback_mirrors_the_pool_loaders(tmp_path):
    m = _write(tmp_path, ['{"path": "x.txt", "passage_dedup": {}}', '{"passage_dedup": {}}'])
    assert pool_guard.scan_manifest_for_passage_dedup(m) == [
        "x.txt (line 1)", "line2 (line 2)",
    ]


def test_refusal_reason_names_the_invariant_and_the_limit(tmp_path):
    reason = pool_guard.refusal_reason("pool.jsonl", ["a (line 1)"], flag="--manifest")
    assert "retained duplicates" in reason
    assert "passage-deduped" in reason
    assert "--manifest pool.jsonl" in reason
    assert "manifest-path check" in reason


def test_refusal_reason_truncates_a_long_list():
    marked = [f"row{i} (line {i})" for i in range(12)]
    reason = pool_guard.refusal_reason("p.jsonl", marked, flag="--manifest")
    assert "12 marked row(s)" in reason and "(+7 more)" in reason


def test_pool_guard_is_pure_stdlib():
    """It is imported by five audit surfaces; a heavy dep here would tax them all."""
    src = (SCRIPTS / "pool_guard.py").read_text(encoding="utf-8")
    third_party = {
        "numpy", "scipy", "torch", "datasketch", "spacy", "nltk", "transformers",
        "sklearn", "yaml", "pandas",
    }
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in third_party, a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in third_party, node.module


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
