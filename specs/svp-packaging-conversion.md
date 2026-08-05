# SVP Packaging Conversion — flat launchers to a zero-install package

**Status:** BUILD-READY (v3, round-2 findings folded; round-3 sequential six-lens self-check clear) · **Date:** 2026-08-05 · **Repo:** `setec-voiceprint`
**Provenance:** modularization audit plus two adversarial six-lens reviews. Round 2 returned NEEDS-REWORK (8 P1 / 9 P2); this revision incorporates every verified P1 and the prioritized P2 fixes.
**Round-3 check:** completeness, dependency, scope/overlap, firewall, mechanizability, and hostile-review passes each completed separately; no remaining P1/P2 within the authorized increment.
**Depends on:** nothing. It supplies the package home used by `svp-text-primitives-identity`; it does not block `setec-consumer-client-contract`, whose producer client remains in the existing scripts/consumer destination specified by that companion contract.

## Outcome and governing rule

Convert the implementations below `plugins/setec-voiceprint/scripts/` into an importable `setec` package while preserving every normalized launcher path used by `capabilities.d`, skills, and vendored consumers.

This is a **zero-install plugin layout**, not a wheel/distribution project. The package lives at `plugins/setec-voiceprint/scripts/setec/`. A copied `plugins/setec-voiceprint/` subtree, a foreign virtual environment, and `python3 scripts/<launcher>.py` must work without `pip install`, `PYTHONPATH`, or repository-relative current working directory. A future wheel is a separate spec; this increment adds no build backend, package discovery, project metadata, or optional-dependency extras. Existing `requirements*.txt` files and repo-root symlinks stay where they are.

All inventories and migration gates are derived from the fetched merge base and candidate tree. Narrative counts are diagnostic only and are never acceptance criteria.

## Verified constraints at `origin/main`

- `capabilities.d/*.yaml` records exact `script_path` launchers. Most are top-level, but live entries also dispatch into `scripts/calibration/`, `scripts/external_mirror/`, and `scripts/replication/`. Those subdirectory entry points do not get a directory alias; each retains an executable per-file launcher.
- `plugins/setec-voiceprint/scripts/setec_run.py` executes the selected file path in a subprocess. Voicewright and APODICTIC consume the copied plugin without installing a Python distribution.
- `output_schema.build_output` rejects an unknown task surface. That existing boundary, plus non-zero registry/path checks after each move, remains the fail-closed mechanism. This spec does **not** change `claim_license._load_surface_labels` from its tested missing-directory behavior and does not introduce an import-time exception across its importer graph.
- The contract-fixture generator executes registered builders and includes a live `s5_distance` path; it is not a hand-transcribed-only oracle. Its inventory is `gen_contract_fixtures.surfaces()`, never a typed count.
- `tools/` programs resolve the repository root. Plugin runtime modules resolve the plugin root. These are different jobs and must not share one resolver.
- `tools/spec_anchor_lint.py` recursively indexes source except its explicit `_SKIP_DIRS`; it has no implicit “top-level only” boundary.

## Layout and ownership

```
plugins/setec-voiceprint/
  scripts/
    setec/
      __init__.py
      paths.py                 # plugin-root and stay-put data paths
      contract/                # output_schema, claim_license, capabilities
      core/                    # libraries that pass the L1 predicate
      surfaces/                # capability-bearing and shared surface modules
      calibration/             # implementations behind calibration launchers
      external_mirror/         # implementations behind mirror launchers
      replication/             # implementations behind replication launchers
    <existing launcher>.py     # permanent compatibility launcher
    calibration/<launcher>.py  # permanent per-file compatibility launcher
    external_mirror/<launcher>.py
    replication/<launcher>.py
    tests/
      conftest.py              # created by fleet-coordination/specs/setec-test-consolidation.md
```

Resolved defaults for this revision:

1. Package location is `scripts/setec/`; no `src/` layout and no install step.
2. Tests stay at `plugins/setec-voiceprint/scripts/tests/`. The repo-root `pytest.ini` remains the sole pytest configuration owner.
3. This spec owns `pytest.ini` and the per-test-file `sys.path` codemod only. `fleet-coordination/specs/setec-test-consolidation.md` Part A creates `scripts/tests/conftest.py` and owns all shared fixtures, clone collapse, and markers after P1 and the P4 content-authority ruling are complete.
4. The shared-surface tier starts with `variance_audit`, `manifest_validator`, `originality_audit`, `repetition_audit`, `manuscript_audit`, `voice_fingerprint`, `acquisition_core`, and `check_corpus`. The live AST graph plus committed exceptional-edge baseline, not a prose count, is the enforcement input.
5. Compatibility launchers remain for the life of schema 1.x. Removing them requires a major-contract spec and consumer migration.

## 1. Root resolution before moves

`scripts/setec/paths.py` provides a typed `find_plugin_root(start=None)` that walks upward for `.claude-plugin/plugin.json`, refuses zero or multiple matches, and exposes named paths for `capabilities.d/`, `.claude-plugin/plugin.json`, `claim_license_surfaces/`, `register_tiers.d/`, `references/`, and `data/`.

Before a module moves, tools/check_packaging_migration.py AST-scans **every** `__file__`-relative anchor, including `.parent`, `.parents[...]`, chained `with_name`, and helper aliases. Each plugin-runtime anchor is either converted to `setec.paths` or listed in the single `packaging_migration_exemptions.yaml` file with path, symbol, reason, and removal phase. `claim_license.py`'s `.parent / "claim_license_surfaces"` anchor is explicitly covered.

Tools do not move, so they keep their existing repo-root ownership instead of gaining a second path abstraction. The builder AST-scans tools for `REPO_ROOT` to verify they still resolve the repository rather than the plugin. Only the duplicated script-discovery/candidate-path code in `seed_capabilities.py` and `check_capabilities_drift.py` is shared, because that duplication directly affects migration drift.

`spec_anchor_lint.py` gets one explicit decision: `scripts/setec/` remains in scope, and package paths are written as repo-relative anchors. It is not added to `_SKIP_DIRS`.

Every move proves that each affected registry resolves the same absolute stay-put data path and loads a non-zero value before and after. A missing label directory continues to follow the existing loader contract; invalid `task_surface` still fails at `output_schema.build_output` with the established exception. CLI boundaries catch expected runtime/configuration failures and emit the existing schema-1.0 unavailable envelope and `reason_category`; imports themselves do not crash because a data directory is absent.

## 2. Compatibility launcher contract

The migration generator classifies every top-level Python file by the pair `(has_main, has_TASK_SURFACE)` and emits one of four pinned templates:

| `main` | `TASK_SURFACE` | launcher behavior |
|---|---|---|
| yes | yes | static `TASK_SURFACE` import, alias, and `_mod.main()` when executed |
| yes | no | alias and `_mod.main()` when executed |
| no | yes | static `TASK_SURFACE` import and alias; no main call |
| no | no | alias only |

Every template saves the original module name and `__main__` state, imports the package implementation, copies all implementation attributes except import metadata into the launcher globals, and then aliases the original name in `sys.modules`. The globals copy is required because file-path loaders keep the module object they created even after a `sys.modules` replacement. Tests exercise ordinary import, `runpy`, and `importlib.util.spec_from_file_location`, then patch a public constant and a private helper through the legacy object and assert the package implementation sees both.

`setec_run.py` and `capabilities.py` are explicit generated-shim exclusions: they remain dedicated launchers until their own L0 move. Any additional exclusion is a row in the single migration-exemptions file; the generator fails on an unlisted fifth shape.

The capability-bearing subdirectory launchers are currently:

- `calibration/paraphrase_robustness.py`
- `calibration/pan_replay.py`
- `calibration/calibration_survey.py`
- `calibration/paraphrase_ladder.py`
- `calibration/shard_runner.py`
- `external_mirror/compose_evidence_pack.py`
- `replication/train_xgboost.py`

Their existing “add `scripts/` to `sys.path`” bootstraps are load-bearing under direct file execution and are exact-path lint exemptions. When their implementations move, the old files become **per-file** launchers using one generated upward-marker bootstrap before importing `setec`; no directory-level alias is accepted. The exemption set is derived by selecting non-top-level `capabilities.d` script paths, so a new nested surface cannot evade the rule.

## 3. Claim-license Deficit Lock

This gate lands in P1, before any mass codemod or harness work.

`tools/check_claim_license_guard.py` compares the candidate against the fetched merge base using Git objects (`git merge-base` + `git show`), never against a baseline regenerated in the PR. It AST-discovers every `_claim_license` definer, the transitive plugin-local constants/helpers they reference across imports, and every emission argument that can affect the full rendered block, `additional_caveats`, or `ai_status`; it canonicalizes that closure independent of file relocation. Unchanged canonical closures pass; moved-but-semantically-identical closures also pass. Existing live contract builders remain a second oracle where they exist, but fixture coverage is not misrepresented as universal.

**No semantic claim-license change is authorized by this packaging spec.** Any canonical change fails CI with no override. A genuine content change must be a later, separately reviewed PR under its own contract. This is both stronger and smaller than an owner-signature mechanism, a regenerable snapshot, or a new approval-artifact format. CI also fails if the merge base is unavailable. PR-body prose is irrelevant.

`setec.surfaces.harness` owns only CLI grouping, envelope plumbing, and report layout. It has no defaults for `licenses`, `does_not_license`, `comparison_set`, `additional_caveats`, or `ai_status`; a surface supplies its own existing values. The migration target excludes claim-license content from boilerplate reduction. No “unique/non-empty text” heuristic is presented as a semantic Deficit Lock.

## 4. Layering and movement predicates

The migration checker derives module shapes and anchors directly from the candidate tree; it does not generate a second inventory artifact. Classification is mechanical:

- L0 contract modules may not import another internal layer.
- L1 core modules have no capability fragment, CLI entry, or envelope emission and may import only L0/L1.
- L2 contains capability-bearing modules and the resolved shared-surface tier. L2 may import L0/L1 and only the generated sanctioned L2 edges.

`stylometry_distance.py` is present on `origin/main` and is an L1 candidate. `stylometry_core.py` is **not** moved to L1 while it imports `variance_audit`; it stays in L2/stay-put inventory until `svp-text-primitives-identity` removes that dependency. No module is typed into L1 merely because its name sounds library-like.

`tools/check_layering.py` derives the live graph itself. The initial exceptional edge file is generated once from the merge-base graph, committed, and exact-diff reviewed; nobody types an approximate seed. New edges are errors. Existing exceptional edges may only disappear. Shared-tier cycles are errors. Layer exemptions use the same migration-exemptions file; `--strict` treats expired or unmatched rows as errors.

## 5. Drift, reachability, and truthful degradation

`check_capabilities_drift.py` and `seed_capabilities.py` share package-aware discovery. The drift parser follows the pinned static `TASK_SURFACE` import in a launcher to the implementation. Package implementations are not double-counted as new flat surfaces. For every manifest entry, CI proves: the recorded path exists in a scratch plugin copy, resolves without following repo-root symlinks, has a matching implementation, and retains its declared `TASK_SURFACE`.

P1 also introduces the exact test-only environment setting `setec_hermetic_backend=stub`, honored at the construction boundary of judge, embedding, and surprisal backends. It performs no model, network, GPU, or corpus access and emits deterministic unavailable/degraded results with a pinned `ai_status`; production mode cannot select it accidentally. A committed `hermetic_surface_cases.yaml` covers the runtime-derived union of manifest entries whose `consumers` include voicewright or APODICTIC. Every row supplies argv, expected schema/tool/surface, expected availability, and expected `ai_status`. Missing rows, ad-hoc “where applicable” skips, or an unlisted exemption fail CI.

The hermetic gate runs from a scratch copy, empty `PYTHONPATH`, outside-repo cwd, and a venv with no `setec` install:

1. structurally resolve **every** `capabilities.d` script path;
2. execute every consumer-listed row under the stub mode and validate its schema-1.0 envelope;
3. execute representative top-level and each nested-path launcher class via direct file path;
4. run one command copied from a shipped skill.

## 6. Test substrate and cross-spec boundary

`pytest.ini` gains the single scripts-root `pythonpath`. The codemod is AST-driven: delete a per-test-file bootstrap only when its binding is used exclusively for imports; convert data-path users to `setec.paths`. It never follows the repo-root `scripts` symlink. This spec does not create `conftest.py`.

The existing Windows private-writer deselect is grandfathered. No new deselect may appear, and no phase may repair a red focused job by expanding it. Bare `python -m pytest` from repo root and every workflow invocation form must collect the same node IDs for their intended selectors.

`fleet-coordination/specs/setec-test-consolidation.md` Part A creates `scripts/tests/conftest.py` and owns domain-fixture hoisting, clone collapse, and marker registration. A1–A3 sequence after packaging P1 and after P4's content-authority ruling has landed; they do not alter package launchers or own pytest configuration. This resolves the prior two-spec ownership conflict.

## 7. Companion-spec sequencing and file ownership

| Boundary | This spec owns | Companion owns | Order |
|---|---|---|---|
| `fleet-coordination/specs/setec-consumer-client-contract.md` | P2 relocation plus compatibility shims for `output_schema.py` and `capabilities.py`; no semantic contract edits | shared client, capabilities manifest `contract` block, committed warning-phrase fixture, consumer-side reliability floor, contract fixtures, and vendoring | consumer C2 starts after packaging P2; it does not block P4 because the 12-key envelope and producer warning emission stay unchanged |
| `fleet-coordination/specs/setec-test-consolidation.md` | P1 `pytest.ini` and per-file `sys.path` codemod; P4 claim-license content-authority ruling | creates `scripts/tests/conftest.py`; shared fixtures, clone collapse, markers, CI-tier consolidation | test A1–A3 start only after both named packaging prerequisites |
| `specs/svp-text-primitives-identity.md` | package home, launchers, and layer rules | primitive registry, identity policy/stamps, characterization, strict no-behavior-change gate | text T1 may start after packaging P1; any moved-module edits follow the owning packaging phase |

If two companion changes would edit the same file, relocation lands first as a semantics-preserving commit; the semantic owner then edits the relocated implementation in a later PR. A packaging PR never folds semantic consumer-contract changes into a move.

## Phases

- **P0 — exact-base preflight.** Fetch `origin/main`; record base and candidate SHA; run all inventories from a clean isolated worktree; never use a stale developer checkout as evidence. Record repo-root symlinks and make every codemod no-follow.
- **P1 — guards and zero-install skeleton, no production moves.** Land `scripts/setec/`, plugin path resolver, migration checker + one exemptions file, no-change claim-license guard, `pytest.ini` plus per-file pytest bootstrap conversion, hermetic backend mode/cases, and scratch-copy gates. Do not create `conftest.py`.
- **P2 — L0 contract.** Move `output_schema.py`, `claim_license.py`, and `capabilities.py` behind pinned compatibility launchers. Do not change missing-directory or envelope semantics. Contract fixtures and stay-put path checks remain identical.
- **P3 — eligible L1 core.** Move only modules that satisfy the live predicate. Break cycles by explicit dependency inversion; modules that still import L2 remain stay-put/L2. `stylometry_distance.py` is included when it satisfies the predicate.
- **P4 — surfaces family by family.** Land package-aware drift discovery and launcher conformance with the first family. Then add the layout-only harness. Each family is a separate PR. Nested capability paths receive per-file launchers, never directory aliases. The claim-license guard and content-authority ruling are required before test-consolidation A1–A3. Consumer C2 may proceed independently after P2 because it does not change producer envelope or warning emission.
- **P5 — enforcement required.** Turn on layering, anchor, shim, reachability, and remaining `sys.path` ratchets in CI; audit already-fragmented `acquisition_core`, `length_bootstrap`, and `register_composition_sweep` rather than minting duplicate fragments.

Every phase is independently mergeable and keeps the full existing suite green.

## Acceptance gates

All new checkers use the repository convention: violations are errors and exit non-zero by default; informational inventory output is not a warning gate; `--strict` additionally rejects expired/unmatched committed exemptions. An exemption is accepted only from the named YAML file and must include owner, reason, introduced SHA, and removal phase. There is no CLI flag that silently ignores a violation.

1. Full suite and all focused workflow selectors pass; no new deselect; root and CI selector collection matches.
2. `gen_contract_fixtures.py --check` passes over `gen_contract_fixtures.surfaces()`; no acceptance rule embeds a fixture count.
3. Claim-license merge-base guard, capability drift, docs freshness, calibration readiness, packaging migration checker, and existing tool-root tests pass.
4. Scratch-copy reachability and consumer-case execution pass for the runtime-derived manifest sets, including every nested script-path class.
5. Legacy import, direct execution, `runpy`, and file-path-load launcher tests prove identity/mutation transparency for all four templates.
6. Layering inventory and shrink-only exceptional-edge file pass.
7. Voicewright and APODICTIC contract checks run against the candidate plugin at their recorded lock floors; any consumer fixture change is a coordinated consumer PR, not an exemption.

## Risks and mechanical defenses

| Risk | Mechanical defense |
|---|---|
| A nested launcher loses its scripts-root bootstrap | derive nested paths from `capabilities.d`; permanent exact-path lint exemption; scratch-copy direct-execution gate |
| A relocation changes claim-license severity or honesty | merge-base canonical AST/emission diff; any semantic change is out of scope and fails with no override |
| A launcher works by import but fails through file loaders | globals copy plus ordinary/runpy/spec-loader conformance tests |
| Optional backends make hermetic CI meaningless | one deterministic backend mode, pinned `ai_status`, generated consumer-case table, no unlisted skip |
| Tools are incorrectly retargeted to plugin-root discovery | tools remain fixed repo-root owners; tool-root tests cover the existing marker/layout |
| A typed inventory goes stale | all sets generated from source/manifests with `--check`; narrative counts are non-gating |
| Packaging and test consolidation edit the same ownership seam | packaging owns `pytest.ini`/per-file bootstrap only; consolidation creates `conftest.py` and sequences after the named P1/P4 prerequisites |

## Out of scope

- Building or publishing a wheel, installing `setec`, or translating requirements files into extras.
- Numeric/text-primitive convergence (owned by `svp-text-primitives-identity`).
- Internal decomposition of large modules beyond the import-cycle cuts needed to satisfy layering.
- Removing schema-1.x compatibility launchers.
