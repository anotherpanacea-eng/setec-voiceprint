#!/usr/bin/env python3
"""Coverage drift test for the private-dyadic register-isolation guard.

The hard constraint the guard exists for is a **privacy** guarantee, not a
stylistic preference: `message.imessage` and `message.facebook_messenger` are
`private_dyadic` leaves holding real private conversational material, and the
register-tier taxonomy (#369) rules them **profile-only** — the manifest
validator rejects `baseline` use on them, and *stylometric reference builders*
must refuse to mix them with any non-private-dyadic or missing register
(`references/manifest-schema.md`, "Register tiers"). A reference composed only
of private-dyadic leaves is allowed; a mixture is not.

`assert_personal_register_isolated` shipped with its call sites chosen **ad
hoc**, and that is exactly how the guard fell behind the surfaces:
`general_imposters` built a pooled candidate+impostor feature space with no
guard at all, and three more surfaces (`pov_voice_profile`, `controls_audit`,
`lambdag_audit`) pooled author references without one — some by reaching past
`build_profile` straight into `stylometry_core`'s pooling primitives, some by
loading entries through a bare loader that never guarded. This is the same
**clean-room pool loader** class `pool_guard.py` documents from the #306/#307
sweep, and it gets the same treatment `tests/test_pool_guard_coverage.py` gives
that one: a **complete classification map with a rationale on every entry,
firing and exempt alike**, pinned by closure sweeps so a new pooling surface
cannot appear unclassified.

Ad hoc selection cuts the other way too, which is why exemptions carry the same
burden of argument: `voice_validation_harness` reaches for the identical pooling
primitives and must NOT be guarded, because it scores cross-register pairs on
purpose.

Five closure sweeps and two obligation checks:

  (A) the guard-caller sweep is closed — every module binding
      ``assert_personal_register_isolated`` is classified;
  (B) the clean-room pooling-primitive sweep is closed — every module binding
      ``select_feature_names`` / ``vector_stats`` is classified. These two
      primitives are what turn a *set* of documents into pooled column
      statistics; a module that binds them has bypassed `build_profile`, which
      is where the guard used to be the only copy;
  (C) the pooled-baseline entrypoint sweep is closed — every module binding
      ``build_profile`` / ``compare_to_baseline`` / ``bootstrap_compare`` (which
      guard internally) or the bare ``load_entries*`` loaders (which do NOT)
      is classified;
  (D) the pool-loader DEFINER sweep is closed — reusing `pool_guard`'s exact
      name family (``_load_manifest`` / ``_load_reference_manifest`` /
      ``_load_reference_dir``), because the wholly clean-room shape — own
      manifest parser AND own featurizer, importing nothing from
      `stylometry_core` — is invisible to (A)–(C). `general_imposters` is that
      shape, and (D) is the only sweep that catches it;
  (E) the pool-loader IMPORTER sweep is closed — matched on the imported
      *names*, not the source module, for the same reason pool_guard's sweep
      (c) is: `cross_doc_argument_consistency` imports them from
      `cross_doc_novelty_profile` rather than from `originality_audit`;

  (f) every classified module is GUARDED (directly, or via a named entrypoint
      that is itself verified to call the guard) or EXEMPT **with a non-empty
      rationale**; and
  (g) the largest exempt family's premise is falsifiable rather than asserted —
      the diversity loaders return ``(id, text[, path])`` tuples and *discard*
      the row dict, so they structurally cannot see a register at all. That
      fact is pinned from the return annotations, so a loader that starts
      carrying row metadata breaks its own exemption.

(f) and (g) are what keep "add a name to a list" from being the cheapest green.
Classification is by whether the surface builds a **pooled author reference** —
a multi-document stylometric object that stands in for a writer and that
something else is scored against, or that is emitted as a voiceprint — NOT by
task-surface tag. Tag and classification diverge in both directions: 24 modules
declare ``TASK_SURFACE = "voice_coherence"`` and most of them are single-document
audits that pool nothing, while `binoculars_calibrate` pools a manifest under a
`calibration` tag.

The sweeps are source scans (this repo's drift-linter style) because no single
behavioural harness can generically drive eighteen modules; the behavioural
refusals and negative controls are pinned per surface, in each surface's own
test file (`test_general_imposters.py`, `test_pov_voice_profile.py`,
`test_controls_audit.py`, `test_lambdag_audit.py`,
`test_voice_validation_harness.py`) and in
`test_text_personal_register_isolation.py`.

Named limit, carried here the way `pool_guard.py` carries its own: a module
that hand-rolls BOTH its manifest parsing AND its featurizer under a loader
name outside the (D) family is not caught. `test_named_limit_*` below pins the
two nearest misses (`crosslingual_voice_distance`, `idiolect_detector`) with the
structural reason each is out of scope, so a change that gives either one a
register-carrying pooled reference fails this file rather than passing silently.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]

import register_taxonomy as rt  # type: ignore  # noqa: E402

# Scope: the non-recursive script glob, same as test_pool_guard_coverage.py.
# Subdirectories (`tests/`, `runners/`, `calibration/`, `oracle/`,
# `replication/`, `external_mirror/`) are fixtures and operator tooling, not
# envelope-emitting surfaces, and a non-recursive glob excludes them by
# construction.
GUARD_NAME = "assert_personal_register_isolated"

# (B) The two primitives that pool a SET of documents into shared column
# statistics. `extract_features` is deliberately absent: it is per-document and
# pools nothing, so binding it is not evidence of a pooled reference.
POOLING_PRIMITIVES = frozenset({"select_feature_names", "vector_stats"})

# (C) Entrypoints that build or load a pooled baseline, each pinned to its
# DEFINING module. The owner is declared rather than searched for because the
# names are not globally unique — `agency_abstraction_audit` has its own local
# `compare_to_baseline`, and a search would happily verify the wrong function.
# The first three call the guard themselves (verified below); the
# `load_entries*` family are bare loaders that do NOT, so a module whose only
# hit is one of those must guard itself.
GUARDING_ENTRYPOINTS = {
    "build_profile": "stylometry_core",
    "compare_to_baseline": "stylometry_core",
    "bootstrap_compare": "voice_distance",
}
BARE_LOADERS = {
    "load_entries": "stylometry_core",
    "load_entries_from_manifest": "stylometry_core",
    "load_entries_from_dir": "stylometry_core",
}
BASELINE_ENTRYPOINTS = frozenset(GUARDING_ENTRYPOINTS) | frozenset(BARE_LOADERS)

# (D)/(E) pool_guard's exact name family, reused verbatim. Sharing the family
# means the two coverage tests fail together if it ever drifts.
POOL_LOADER_DEF_NAMES = frozenset({
    "_load_manifest", "_load_reference_manifest", "_load_reference_dir",
})
POOL_LOADER_IMPORT_NAMES = frozenset({
    "_load_reference_manifest", "_load_reference_dir",
})

GUARDED = "GUARDED"
EXEMPT = "EXEMPT"
DIRECT = "direct"

# The shared rationale for the metadata-discarding diversity family. Their
# loaders return `(id, text[, path])` tuples and throw the row dict away, so the
# register never reaches the pool — the exemption is structural, and (g) pins
# the return annotations so it stays falsifiable.
_TUPLE_LOADER_RATIONALE = (
    "set-level-diversity measurement, not a writer stand-in: nothing is scored "
    "against this pool as an author reference and no voiceprint is emitted from "
    "it. Structurally it also CANNOT be tier-aware — its loader returns "
    "(id, text[, path]) tuples and discards the row dict where `register` "
    "lives, the same shape fact pool_guard.py names. Check (g) pins that return "
    "annotation, so a loader that starts carrying row metadata breaks this "
    "exemption instead of inheriting it"
)

# The complete classification map. EVERY entry carries a rationale — guarded and
# exempt alike — and an entry without one fails this module's own check below.
CLASSIFICATION: dict[str, dict] = {
    # ---------------- guarded ----------------
    "stylometry_core": {
        "sweeps": ("A",),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "the library that DEFINES the pooled-baseline entrypoints: "
            "`build_profile` and `compare_to_baseline` each assert isolation "
            "before extracting a single feature, which is what every "
            "`via`-guarded surface below inherits. It appears in sweep (A) "
            "because it now binds the guard from `register_taxonomy` rather "
            "than defining it — the move that let the stdlib-light surfaces "
            "reach it at all"
        ),
    },
    "voice_distance": {
        "sweeps": ("A", "C"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "the framework's headline pooled author reference: `baseline_entries` "
            "become one Burrows-Delta/cosine centroid the questioned draft is "
            "scored against. Guards directly in `bootstrap_compare` as well, "
            "because the bootstrap resamples the same pool"
        ),
    },
    "voice_drift_tracker": {
        "sweeps": ("A", "B"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "per-period voiceprints are pooled author references and the shared "
            "feature space spans every period at once; a clean-room copy of the "
            "profile pipeline, so it must call the guard itself rather than "
            "inherit it from build_profile"
        ),
    },
    "voice_profile": {
        "sweeps": ("C",),
        "guard": GUARDED,
        "via": "build_profile",
        "rationale": (
            "emits the writer's voiceprint verbatim from `build_profile`, which "
            "asserts isolation before any feature extraction runs; no clean-room "
            "copy of the pooling math exists here to bypass it"
        ),
    },
    "house_style_decomposition": {
        "sweeps": ("C",),
        "guard": GUARDED,
        "via": "compare_to_baseline",
        "rationale": (
            "decomposes a draft against a pooled baseline set, but every distance "
            "goes through `compare_to_baseline`, which asserts isolation on the "
            "same entry list before extracting features"
        ),
    },
    "general_imposters": {
        "sweeps": ("A", "D"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "the candidate identity-baseline pool IS a pooled author reference, "
            "and `run_gi` derives ONE shared feature vocabulary from "
            "candidate_docs + impostor_docs — so a private-dyadic doc in either "
            "pool contaminates the shared space. Wholly clean-room (own manifest "
            "loader, own featurizer, no stylometry_core import), which is why "
            "only sweep (D) catches it and why the guard had to become "
            "importable without the heavy stack"
        ),
    },
    "pov_voice_profile": {
        "sweeps": ("A", "B"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "per-POV centroids are pooled author references its own docstring "
            "calls voice-cloning input, and `select_feature_names` runs over the "
            "union of ALL POVs, so one private-dyadic document contaminates every "
            "POV's coordinates. It groups solely on `pov` and never reads "
            "`register`, so nothing else would notice the mixture"
        ),
    },
    "controls_audit": {
        "sweeps": ("A", "C"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "pools `load_entries` results into one function-word baseline mean "
            "that the questioned text and both controls are scored against — the "
            "same object voice_distance guards. `load_entries` is a bare loader "
            "with no guard of its own, and this surface drops metadata at the "
            "`baseline_texts` step, so it must guard while the entries are intact"
        ),
    },
    "lambdag_audit": {
        "sweeps": ("A", "C"),
        "guard": GUARDED,
        "via": DIRECT,
        "rationale": (
            "`_entries_to_sentences` concatenates POS streams across a corpus "
            "into ONE reference-author grammar LM the query is scored against — a "
            "pooled author reference in a different feature alphabet, not a "
            "different kind of object. Both corpora are guarded: the background "
            "LM is equally a pooled reference, for whoever wrote it"
        ),
    },
    # ---------------- exempt ----------------
    "voice_validation_harness": {
        "sweeps": ("B",),
        "guard": EXEMPT,
        "via": None,
        "rationale": (
            "scores PAIRS from a labelled MULTI-AUTHOR validation slice and ranks "
            "them by `same_author`; the shared feature space is a z-scoring "
            "normaliser over that slice, not a writer stand-in, and nothing is "
            "emitted as a voiceprint. It records `register_a`/`register_b` per "
            "pair because cross-register pairing is the OBJECT OF STUDY — "
            "register mismatch is the confounder this harness exists to quantify. "
            "Firing the guard here would refuse any slice containing a "
            "private-dyadic document alongside anything else, i.e. it would "
            "destroy the only way to measure whether the tier separation is "
            "empirically justified at all"
        ),
    },
    "binoculars_calibrate": {
        "sweeps": ("D",),
        "guard": EXEMPT,
        "via": None,
        "rationale": (
            "threshold calibration over a labelled eval corpus: every document is "
            "scored INDEPENDENTLY and the manifest supplies labels, not a pooled "
            "reference. Its loader does carry row dicts, so the exemption rests on "
            "the per-document scoring rather than on the tuple-shape argument the "
            "diversity family uses"
        ),
    },
    "originality_audit": {
        "sweeps": ("D",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "homogeneity_audit": {
        "sweeps": ("D",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "distinct_diversity_audit": {
        "sweeps": ("D",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "cross_doc_novelty_profile": {
        "sweeps": ("D",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "corpus_novelty_audit": {
        "sweeps": ("E",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "skeleton_overlap_audit": {
        "sweeps": ("E",),
        "guard": EXEMPT,
        "via": None,
        "rationale": _TUPLE_LOADER_RATIONALE,
    },
    "cross_doc_argument_consistency": {
        "sweeps": ("E",),
        "guard": EXEMPT,
        "via": None,
        "rationale": (
            "claim-consistency COMPARISON across one author's documents; it reads "
            "propositions, never builds a stylometric centroid, and it imports the "
            "shared loaders from cross_doc_novelty_profile rather than "
            "originality_audit — which is why sweep (E) keys on the imported names "
            "and not on the source module"
        ),
    },
}

# (g) The metadata-discarding loaders whose tuple return shape IS the exemption.
TUPLE_SHAPED_LOADERS = {
    "originality_audit": ("_load_reference_manifest", "_load_reference_dir"),
    "cross_doc_novelty_profile": ("_load_reference_manifest", "_load_reference_dir"),
    "homogeneity_audit": ("_load_manifest",),
    "distinct_diversity_audit": ("_load_manifest",),
}


# ---------------- structural helpers (scope-parameterized) ----------------


def _module_sources(scope: Path) -> dict[str, str]:
    """``{module_stem: source}`` for the non-recursive ``*.py`` glob of ``scope``.

    No name filter: an underscore-prefixed module (``_mirror_gate.py``) is still
    a module that could grow a pooled reference, and narrowing the glob is
    exactly how a closure sweep quietly stops closing.
    """
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in sorted(scope.glob("*.py"))
    }


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


def _bound_names(src: str) -> set[str]:
    """Every name an import statement BINDS in the module namespace."""
    out: set[str] = set()
    for node in ast.walk(_tree(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add(alias.asname or alias.name.split(".")[0])
    return out


def _defines_pool_loader(src: str) -> bool:
    """A MODULE-TOP-LEVEL def whose name is exactly a pool-loader name.

    Top-level-only (a nested helper doesn't count) and exact-name (so the prefix
    family — ``_load_manifest_records``, ``_load_manifest_entries`` — stays out,
    as in test_pool_guard_coverage.py).
    """
    return any(
        isinstance(node, ast.FunctionDef) and node.name in POOL_LOADER_DEF_NAMES
        for node in _tree(src).body
    )


def _calls_guard(src: str) -> bool:
    """The module BINDS the guard name and actually calls it.

    Binding alone is not enough — an unused import would otherwise satisfy the
    obligation — and a call alone is not enough either, since a same-named local
    helper would then count as coverage.
    """
    if GUARD_NAME not in _bound_names(src):
        return False
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == GUARD_NAME)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == GUARD_NAME)
        )
        for node in ast.walk(_tree(src))
    )


def _function_calls_guard(src: str, func_name: str) -> bool:
    """A specific top-level function's body calls the guard."""
    for node in _tree(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == GUARD_NAME
                for c in ast.walk(node)
            )
    return False


def _imports_from(src: str, name: str, module: str) -> bool:
    """The module binds ``name`` via ``from <module> import name``.

    Name-only matching is not enough for the transitive claim: several modules
    define a private helper that happens to share an entrypoint's name.
    """
    for node in ast.walk(_tree(src)):
        if not isinstance(node, ast.ImportFrom) or node.module != module:
            continue
        for alias in node.names:
            if alias.name == name and alias.asname is None:
                return True
    return False


def _return_annotation(src: str, func_name: str) -> str | None:
    for node in _tree(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.unparse(node.returns) if node.returns else None
    return None


def _sweep_hits(src: str) -> set[str]:
    bound = _bound_names(src)
    hits: set[str] = set()
    if GUARD_NAME in bound:
        hits.add("A")
    if bound & POOLING_PRIMITIVES:
        hits.add("B")
    if bound & BASELINE_ENTRYPOINTS:
        hits.add("C")
    if _defines_pool_loader(src):
        hits.add("D")
    if bound & POOL_LOADER_IMPORT_NAMES:
        hits.add("E")
    return hits


# ---------------- the map's own integrity ----------------


def test_every_classification_entry_carries_a_rationale():
    """(f)'s teeth: a rationale-free entry is a failure, so widening a name list
    is never the cheapest way back to green."""
    for module, row in CLASSIFICATION.items():
        assert row["guard"] in (GUARDED, EXEMPT), module
        assert row["sweeps"] and all(s in "ABCDE" for s in row["sweeps"]), module
        assert isinstance(row.get("rationale"), str), module
        assert len(row["rationale"].strip()) >= 60, (
            f"{module}: rationale must state WHY this surface does (or does not) "
            "build a pooled author reference, not just assert a classification"
        )
        if row["guard"] == GUARDED:
            assert row["via"] == DIRECT or row["via"] in GUARDING_ENTRYPOINTS, module
        else:
            assert row["via"] is None, module


def test_no_classified_module_is_missing_from_the_tree():
    for module in CLASSIFICATION:
        assert (SCRIPTS / f"{module}.py").is_file(), module


def test_the_guarded_set_is_not_vacuous():
    """If the private-dyadic tier ever emptied, every refusal below would pass
    for the wrong reason. Anchor the map to the live registry."""
    assert rt.PROFILE_ONLY_REGISTERS, "no private-dyadic leaves — guard is vacuous"
    for register in rt.PROFILE_ONLY_REGISTERS:
        assert rt.resolve_register_tier(register) == rt.PRIVATE_DYADIC_TIER


# ---------------- (A)-(E) closure sweeps ----------------


@pytest.mark.parametrize(
    ("sweep", "label"),
    [
        ("A", "binds assert_personal_register_isolated"),
        ("B", "binds a clean-room pooling primitive"),
        ("C", "binds a pooled-baseline entrypoint or loader"),
        ("D", "defines a pool loader"),
        ("E", "imports a shared pool loader"),
    ],
)
def test_sweep_is_closed(sweep: str, label: str):
    sources = _module_sources(SCRIPTS)
    found = {m for m, src in sources.items() if sweep in _sweep_hits(src)}
    expected = {m for m, r in CLASSIFICATION.items() if sweep in r["sweeps"]}
    assert found == expected, (
        f"sweep ({sweep}) — {label} — is no longer closed: "
        f"unclassified={sorted(found - expected)}, "
        f"stale={sorted(expected - found)}"
    )


def test_no_classified_module_lost_every_sweep_hit():
    """A map entry that no sweep reaches is dead weight the closure tests can no
    longer defend; it must be removed or re-anchored deliberately."""
    sources = _module_sources(SCRIPTS)
    for module in CLASSIFICATION:
        assert _sweep_hits(sources[module]), (
            f"{module} is classified but no sweep reaches it any more"
        )


# ---------------- (f) guarded-or-exempt-with-rationale ----------------


def test_directly_guarded_surfaces_bind_and_call_the_guard():
    for module, row in CLASSIFICATION.items():
        if row["guard"] != GUARDED or row["via"] != DIRECT:
            continue
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        assert _calls_guard(src), (
            f"{module} is classified GUARDED/direct but does not bind + call "
            f"{GUARD_NAME}"
        )


def test_transitively_guarded_surfaces_reach_an_entrypoint_that_guards():
    """The transitive claim is verified at both ends: the surface really binds
    the named entrypoint, AND that entrypoint's own body really calls the guard.
    Without the second half, `via` would be an unchecked assertion."""
    sources = _module_sources(SCRIPTS)
    for module, row in CLASSIFICATION.items():
        if row["guard"] != GUARDED or row["via"] == DIRECT:
            continue
        entrypoint = row["via"]
        owner = GUARDING_ENTRYPOINTS[entrypoint]
        assert _imports_from(sources[module], entrypoint, owner), (
            f"{module} claims guarding via {owner}.{entrypoint} but does not "
            "import that exact function"
        )
        assert _function_calls_guard(sources[owner], entrypoint), (
            f"{owner}.{entrypoint} no longer calls {GUARD_NAME}; every surface "
            f"claiming guarding through it is now unguarded"
        )


def test_bare_loaders_are_not_mistaken_for_guarding_entrypoints():
    """The (C) sweep deliberately mixes guarding entrypoints with bare loaders.
    Pin that the bare ones really do NOT guard, so a future `via: load_entries`
    cannot quietly claim coverage they never provided."""
    sources = _module_sources(SCRIPTS)
    for loader, owner in BARE_LOADERS.items():
        assert not _function_calls_guard(sources[owner], loader), (
            f"{owner}.{loader} now guards; move it into GUARDING_ENTRYPOINTS so "
            "surfaces may legitimately claim coverage through it"
        )


def test_exempt_surfaces_do_not_call_the_guard():
    """The inverse obligation: an EXEMPT entry that quietly calls the guard is a
    map that no longer describes the code. For voice_validation_harness the
    inversion is substantive — guarding it would refuse the cross-register pairs
    it exists to measure."""
    for module, row in CLASSIFICATION.items():
        if row["guard"] != EXEMPT:
            continue
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        assert not _calls_guard(src), (
            f"{module} is classified EXEMPT but calls {GUARD_NAME}; either the "
            "rationale is wrong or the classification is"
        )


# ---------------- (g) the big exemption's premise is falsifiable ----------------


def test_tuple_shaped_loaders_still_discard_row_metadata():
    """The diversity family's exemption rests on a SHAPE claim: their loaders
    return `(id, text[, path])` tuples and throw the row dict away, so `register`
    never reaches the pool. Pin the return annotations — a loader that starts
    returning row dicts has acquired register metadata and must be reclassified
    rather than inherit the old rationale."""
    for module, loaders in TUPLE_SHAPED_LOADERS.items():
        src = (SCRIPTS / f"{module}.py").read_text(encoding="utf-8")
        for loader in loaders:
            annotation = _return_annotation(src, loader)
            assert annotation is not None, f"{module}.{loader} lost its annotation"
            assert annotation.startswith("list[tuple["), (
                f"{module}.{loader} now returns {annotation!r}, not a metadata-"
                "discarding tuple; its EXEMPT rationale no longer holds"
            )


def test_metadata_carrying_pool_loaders_are_guarded_or_argued_separately():
    """The complement of (g): a pool loader that does NOT return tuples carries
    row metadata, so the shape argument is unavailable to it and it must either
    guard or carry its own rationale.

    The identity comparison is deliberate: it asks whether the entry REUSED the
    shared constant, which equality could not distinguish from an independently
    written rationale that happened to say the same thing."""
    for module, row in CLASSIFICATION.items():
        if "D" not in row["sweeps"] or module in TUPLE_SHAPED_LOADERS:
            continue
        assert row["guard"] == GUARDED or row["rationale"] is not _TUPLE_LOADER_RATIONALE, (
            f"{module}'s loader carries row metadata, so it cannot inherit the "
            "tuple-shape exemption — guard it or argue it separately"
        )


# ---------------- named limits ----------------


def test_named_limit_directory_only_surface_stays_out_of_scope():
    """`crosslingual_voice_distance` pools a baseline directory, but a directory
    input carries no row metadata at all — the same manifest-path limit
    `pool_guard.py` names. It has no manifest mode, so there is no register to
    isolate; if it grows one, sweep (C)/(D) drags it into the map."""
    src = (SCRIPTS / "crosslingual_voice_distance.py").read_text(encoding="utf-8")
    assert "--baseline-dir" in src
    assert "--manifest" not in src
    assert not _sweep_hits(src)


def test_named_limit_keyness_surface_stays_out_of_scope():
    """`idiolect_detector` does read a manifest, but its records carry no
    `register` and it builds a keyness/collocation table against a reference
    corpus rather than a stylometric centroid. Pinned so that adding a register
    to its record — or reaching for the pooling primitives — fails here."""
    src = (SCRIPTS / "idiolect_detector.py").read_text(encoding="utf-8")
    assert "--manifest" in src
    assert not _sweep_hits(src)
    assert '"register"' not in src and "'register'" not in src


# ---------------- self-test of the sweeps ----------------


def test_sweep_catches_an_unclassified_synthetic_clean_room_builder(tmp_path):
    """A synthetic module placed under the scope that reaches for the pooling
    primitives, or defines a pool loader, must make the closure sweeps fail —
    otherwise the sweeps are decorative."""
    (tmp_path / "synthetic_reference_builder.py").write_text(
        "from stylometry_core import select_feature_names, vector_stats\n\n\n"
        "def _load_manifest(path):\n    return []\n",
        encoding="utf-8",
    )
    sources = _module_sources(tmp_path)
    hits = _sweep_hits(sources["synthetic_reference_builder"])
    assert hits == {"B", "D"}
    assert "synthetic_reference_builder" not in CLASSIFICATION
    for sweep in ("B", "D"):
        found = {m for m, s in sources.items() if sweep in _sweep_hits(s)}
        expected = {m for m, r in CLASSIFICATION.items() if sweep in r["sweeps"]}
        assert found - expected == {"synthetic_reference_builder"}


def test_sweep_ignores_a_nested_or_prefixed_definition(tmp_path):
    """False-positive control: only top-level, exact-name defs count, and
    `extract_features` alone (per-document, pools nothing) is not a hit."""
    (tmp_path / "not_a_reference_builder.py").write_text(
        "from stylometry_core import extract_features\n\n\n"
        "def outer():\n"
        "    def _load_manifest(path):\n        return []\n"
        "    return _load_manifest\n\n\n"
        "def _load_manifest_entries(path):\n    return []\n",
        encoding="utf-8",
    )
    sources = _module_sources(tmp_path)
    assert not _sweep_hits(sources["not_a_reference_builder"])


def test_guard_binding_without_a_call_does_not_satisfy_the_obligation(tmp_path):
    """An unused import must not count as coverage — otherwise the cheapest
    green is to add an import line and change nothing."""
    p = tmp_path / "imports_but_never_calls.py"
    p.write_text(
        f"from register_taxonomy import {GUARD_NAME}\n\n\n"
        "def build(entries):\n    return entries\n",
        encoding="utf-8",
    )
    src = p.read_text(encoding="utf-8")
    assert "A" in _sweep_hits(src)
    assert not _calls_guard(src)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
