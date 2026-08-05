# SVP Packaging Conversion — from 233 loose scripts to an installable package

**Status:** DRAFT v2 (post-swarm-review) · **Date:** 2026-08-05 · **Repo:** `setec-voiceprint`
**Provenance:** three-agent modularization audit, Cowork session 2026-08-05 (voiceprint structural audit + cross-repo seam analysis)
**Depends on:** nothing (this is the foundation spec). Blocks: `svp-text-primitives-identity`, parts of `setec-consumer-client-contract`.
**Review round 1:** adversarial spec-review swarm, 2026-08-05 → **VERDICT: NEEDS-REWORK**. "The migration architecture is sound in outline but the spec's core mechanism (src-layout + star-import shims) provably breaks every real consumer invocation path, its acceptance oracles are green precisely where the failures occur, and its P4 harness opens a silent-softening channel through the claim-license Deficit Lock." 6 load-bearing gaps (P1) and 10 prioritized fixes (P2) found; all addressed below. Facts the review corrected are re-verified against the live checkouts (`setec-voiceprint`, `setec-voicewright`, `apodictic`) as of this rewrite.

## Problem

`plugins/setec-voiceprint/scripts/` is a flat directory of 233 non-test modules (197,384 LOC, re-verified by direct count) with **no package structure**: no `pyproject.toml`, no setup.py, exactly two `__init__.py` files (both under `replication/`), and no conftest.py anywhere. Consequences, measured:

- **317 `sys.path` references in production code, 665 in tests.** 298 of 306 test files hand-roll the same `sys.path.insert(0, str(ROOT))` bootstrap with at least 6 naming variants (`ROOT`, `SCRIPTS`, `CALIB_DIR`, `_SCRIPTS`, `SCRIPTS_ROOT`, inline). The 306 test files live at `plugins/setec-voiceprint/scripts/tests/` — verified; **there is no top-level `tests/` directory**, and the repo-root `pytest.ini` is the only pytest config file that exists today.
- **Dual-name module loading is a live hazard.** `acquire_imessage_sent_atomic.py:33-54` carries a 22-line comment explaining a `sys.modules` self-aliasing hack needed because the module can be loaded as both `__main__` and `acquire_imessage_sent_atomic`.
- **5 import cycles**: `acquisition_core↔pdf_extract`, `variance_audit↔aic_pattern_audit`, `manifest_validator↔register_sweep`, `calibration/task_surfaces↔calibration/shard_runner`, `acquire_imessage_sent_atomic↔windows_portable_tree`.
- **~31,104 LOC (15.8% of non-test code) is per-script CLI/envelope/report boilerplate**: 183 `main()`s, 84 `build_arg_parser`s, ~73 `_claim_license`s, 46 `render_report`s, 25 `compose_envelope`s. `voice_profile.py` is 80.1% boilerplate.
- Layering exists de facto but is under-measured, not merely unenforced: a corrected AST pass finds **~91 surface-to-surface import edges** (not the ~4 originally reported), of which ~73 remain after the L2 reclassification below; `variance_audit` alone is imported by 17+ other surfaces (`validation_harness`, `cosine_explanation`, `controls_audit`, `skeleton_overlap_audit`, and more).
- **No mechanism makes `import setec` resolve at consumer runtime.** `setec-voicewright`'s `discovery.py (line 237)` invokes `[sys.executable, str(script_path), *args]` from voicewright's own venv, with no install step against voiceprint. `apodictic` does the same from `evals/benchmark/agd-scan/`, `evals/panels/shared-blind-editor/`, and its setec_discovery.py skill script. All 8 SKILL.md files (`craft-restoration`, `voice-coherence`, `corpus-acquisition`, `setup`, `smoothing-diagnosis`, `metric-targeted-restoration`, `setec`, `validation`) invoke bare `python3` against a marketplace subtree copy — verified present, 8 files. **Any layout that requires an install step breaks every one of these paths**; this is the mechanism decision the rest of the spec now depends on (see Design §1).

## Review-corrected facts (were wrong in v1)

- **Consumer pins differ, not match.** `setec-voicewright/setec-plugin.lock` pins **v1.128.1** (`s5_distance` exists there). `apodictic/setec-plugin.lock` pins **v1.127.0** — verified by reading both lock files directly; v1 of this spec claimed both consumers pinned v1.128.1. P0 must satisfy both floors, and `setec-consumer-client-contract` §D gets a reciprocal pointer to this fact rather than assuming one shared floor.
- **Repo-root symlinks are real and load-bearing**, not hypothetical: `scripts -> plugins/setec-voiceprint/scripts`, `references -> plugins/setec-voiceprint/references`, plus `requirements.txt`, `requirements-acquisition.txt`, `requirements-calibration.txt`, `requirements-surprisal.txt` all symlink into the plugin subtree (verified `ls -la` at repo root). Any codemod that walks the tree without `-not -type l` / no-follow-symlinks will double-visit every file reachable both ways.
- **`claim_license.py._load_surface_labels` is fail-open**, confirmed at `claim_license.py:53-58`: `_SURFACE_LABEL_DIR = Path(__file__).resolve().parent / "claim_license_surfaces"`, and on a missing directory the function `return {}` rather than raising — exactly the failure mode a naive P2 module move induces.
- **Three more `__file__`-relative anchors confirmed**, all using `parents[N]` (none use an upward marker search): `capabilities.py:69` (`PLUGIN_ROOT = Path(__file__).resolve().parents[1]`), `register_taxonomy.py:62` (`REGISTRY_PATH = Path(__file__).resolve().parents[1] / "register_tiers.d"`), `setec_run.py:64,67` (`PLUGIN_ROOT = parents[1]`, `REPO_ROOT = PLUGIN_ROOT.parent.parent`). Any of these silently re-anchors to the wrong directory the moment the defining module moves one level deeper (as P2's `contract/` subpackage does).
- **The L1 module list in v1 named two files that don't exist** (manifest_io.py, `stylometry_distance.py` — confirmed absent from `scripts/`) **and two that are miscategorized as pure-library:** `acquisition_core.py` and `length_bootstrap.py` both have live capability fragments (`capabilities.d/acquisition_core.yaml`, `capabilities.d/length_bootstrap.yaml` — confirmed), so they are capability-bearing surfaces, not L1 core.
- **`register_sweep` already has a capability fragment** (`capabilities.d/register_composition_sweep.yaml` — confirmed), so P5's original "give it a fragment" framing was wrong; it needs auditing for correctness, not minting.
- **The byte-oracle is thin: 17 golden files exist** (`find … -iname "*.json" -ipath "*contract_fixtures*"` → 17, confirmed) against ~137 capability-fragment surfaces (138 fragments in `capabilities.d/`, confirmed) and ~73 `_claim_license` definers. P4's harness rewrites the boilerplate the goldens are supposed to police, and the goldens don't reach most of it.
- **CI substrate confirmed:** `tests.yml` defines 7 jobs (`pytest` plus 6 focused OS-specific jobs: `macos-descriptor-confinement`, `windows-descriptor-backend`, `windows-owner-corrections`, `windows-shingle-dedup`, `windows-nonprose-sweep`, `windows-private-writer-guards`). All 6 focused jobs install bare `pytest` only — no package, no `requirements.txt`. Only the main `pytest` job installs real dependencies, and even it never installs `setec` as a package (no such package exists yet).
- **Shim-set inventory:** 181 top-level `scripts/*.py` files (confirmed); ~35-40 lack both a `main()` and a `TASK_SURFACE` constant (rough AST scan: 35) and so need a different, simpler shim template (see Design §2). `oracle/`, `external_mirror/`, and `replication/` (the latter with 2 pre-existing `__init__.py`s, its only package structure today) have no assigned destination in v1's layout and are addressed explicitly below.

## Firewall risk (why this spec is NEEDS-REWORK, not APPROVE-WITH-NOTES)

P4's harness proposal ("surfaces declare their deltas; the harness owns the invariants") is a default-plus-override pattern applied *directly to the claim-license Deficit Lock*, the framework's licensing-claim safety mechanism. Combined with a byte-oracle covering 17 of 137 surfaces (and validating hand-transcribed golden mirrors, not live emission), a P4 family PR could soften a surface's `does_not_license` text, regenerate its own goldens in the same PR, and pass every stated gate — including gate 2 (goldens byte-identical, because the PR *is* the source of the new goldens) and gate 3 (`check_capabilities_drift`, which never inspects license text). Compounded by the fail-open `_load_surface_labels`: a P2 move that breaks the label directory silently returns `{}` rather than failing loudly, so a labeling regression and a content regression can both go undetected by the same phase boundary. Design §5 below closes this before P4 is allowed to touch any `_claim_license` body.

## Hard constraints (things this spec must NOT break)

1. **`capabilities.d/*.yaml` fragments pin `script_path`** (e.g. `plugins/setec-voiceprint/scripts/voice_fingerprint.py`), and `setec_run.py` resolves surfaces from those paths. Consumers (`setec-voicewright` @ v1.128.1; `apodictic` @ v1.127.0 — different floors, both must be satisfied) invoke `[sys.executable, script_path, *args]` through the dispatcher, with **no install step**, from foreign venvs. **Every existing script path must keep working, with zero-install resolution, for the life of the M1 contract.**
2. `output_schema.py` (108 importers) and `claim_license.py` (109 importers) define the versioned envelope. Envelope bytes must not change: `gen_contract_fixtures.py` goldens and `references/contract_fixtures/` (vendored byte-identical by voicewright and APODICTIC) are the acceptance oracle for the 17 surfaces they cover — for the other ~120, see Design §5.
3. `tools/check_capabilities_drift.py` gates `surface` against each script's `TASK_SURFACE` constant — the constant must remain AST-discoverable in the shim (see Design §2), and the drift gate itself must be taught to resolve the new shim shape before any shim ships (see Design §7, moved to P4).
4. The plugin is distributed as the `plugins/setec-voiceprint/` subtree (plugin marketplace + skills reference `scripts/*.py` paths in 8 SKILL files, confirmed). The package must live inside that subtree, resolvable with **zero install**, so a vendored plugin copy and a bare `python3 scripts/foo.py` invocation both work unmodified.
5. The 306-file test suite (7,437 tests) must stay green at every phase boundary; CI passes `-n auto` explicitly, across all 7 confirmed workflow jobs, not just the main `pytest` job.

## Design

### 1. Import mechanism decides the layout — ORCHESTRATOR RULING (recommended default, owner may override)

**Adopt option (a): drop src-layout.** The package lives at `plugins/setec-voiceprint/scripts/setec/`, a sibling directory to the existing flat scripts, not under a separate `src/`. When Python runs scripts/foo.py directly — via the dispatcher, a SKILL script, or a bare `python3` invocation from a foreign venv — `sys.path[0]` is automatically the script's own directory (`scripts/`), which already contains `setec/`. `import setec.surfaces.foo` therefore resolves with **zero install, zero `PYTHONPATH` setup, zero venv coupling**, for every invocation style in Constraint 1 and 4. This was not evaluated in v1, whose `src/` layout requires an editable install that none of the four consumer invocation paths perform.

```
plugins/setec-voiceprint/
  pyproject.toml              # NEW — build metadata only; see §6 for why it owns no pytest config
  scripts/
    setec/                    # NEW — the installable package, sibling to the flat scripts
      __init__.py             # version = plugin.json version (single-sourced via setec.paths)
      paths.py                 # NEW, P1 — see §2
      contract/                # L0 — the versioned consumer contract
        output_schema.py  claim_license.py  capabilities.py
      core/                     # L1 — shared libraries (no CLI, no envelope emission, no capability fragment)
        stylometry_core.py  preprocessing.py  register_taxonomy.py
        atomic_publish.py  judge_backends.py  embedding_backend.py
        surprisal_backend.py  pool_guard.py  ...
      surfaces/                 # L2 — audit implementations + shared-surface tier (see §4)
        harness.py               # NEW — shared CLI/envelope/report harness, layout only (§5)
        variance_audit.py  voice_distance.py  acquisition_core.py  length_bootstrap.py  ...
      calibration/               # the scripts/calibration/ subtree
      oracle_pkg/  mirror_pkg/  replication_pkg/   # subpackage alias-shims for oracle/, external_mirror/, replication/ (§3)
    voice_fingerprint.py       # UNCHANGED PATH — sys.modules alias shim, ~6 lines (§2)
    ...
    tests/                      # STAYS at scripts/tests/ pending confirmation — owner decision point 2
```

Package name decision unchanged from v1: **`setec`**. Hermetic gate (new, required before P1 closes): from a **scratch copy** of `plugins/setec-voiceprint/`, in a venv with **no `setec` install**, `PYTHONPATH` empty, `cwd` outside the repo, run `python3 scripts/setec_run.py <surface> --json` and one SKILL-invoked script; both must produce a schema-1.0 envelope. This is the mechanical proof that Constraint 1/4 hold, not just an assertion.

### 2. `setec.paths` — the single anchor, landed before any module moves (P1, blocking)

One module, `scripts/setec/paths.py`, replaces every `parents[N]` escape (confirmed in `capabilities.py`, `register_taxonomy.py`, `setec_run.py`, and `claim_license.py`'s directory anchor):

```python
def find_plugin_root(start: Path | None = None) -> Path:
    """Walk upward from `start` (default: this file) until a directory
    containing `.claude-plugin/plugin.json` is found. Never uses parents[N]."""
```

A P1 codemod replaces all confirmed `parents[N]` call sites with `paths.find_plugin_root()`. Stay-put data roots — directories that keep their current absolute location regardless of where code that reads them lives — are enumerated explicitly: `capabilities.d/`, `.claude-plugin/plugin.json`, `claim_license_surfaces/`, `register_tiers.d/`, `references/`, `data/`. `claim_license._load_surface_labels`'s missing-directory branch changes from `return {}` to `raise FileNotFoundError` — closing the fail-open hole the firewall risk depends on. Every subsequent phase that moves a module gates on: the registry it anchors loads a non-zero count, and the resolved absolute path is identical before and after the move.

### 3. Shim contract — sys.modules alias, not star-import

v1's star-import shim (`from setec.surfaces.foo import *`) is not mutation-transparent: with 227 confirmed `mock.patch` call sites and heavy `monkeypatch.setattr` usage across the 306 test files, patches targeting `scripts.foo.SOME_NAME` land on the shim's copy of the binding, not the package's, and silently no-op; private (`_`-prefixed) names never cross a star-import at all, and `__all__` narrows re-export in at least 17 confirmed modules. The replacement:

```python
# scripts/voice_fingerprint.py — canonical shim template, pinned by a conformance test
import sys, importlib
from setec.surfaces.voice_fingerprint import TASK_SURFACE  # noqa: F401 — AST-visible for check_capabilities_drift
_mod = importlib.import_module("setec.surfaces.voice_fingerprint")
sys.modules[__name__] = _mod
if __name__ == "__main__":
    raise SystemExit(_mod.main())
```

This makes `scripts.voice_fingerprint` and `setec.surfaces.voice_fingerprint` the **same object** in `sys.modules` after import — patches, whether by public or private name, land on the one real module. Gate 6 is restated: patch a constant *and* a private helper through the script-name import path, assert the package module observes both. The explicit `from ... import TASK_SURFACE` line exists only so `check_capabilities_drift`'s AST walker (§7) has a static import edge to resolve; runtime resolution goes through the `sys.modules` swap. The ~35-40 scripts confirmed to have neither `main()` nor `TASK_SURFACE` get a second, simpler shim template (alias-only, no `__main__` guard, no `TASK_SURFACE` import line). `oracle/`, `external_mirror/`, and `replication/` (whose flat sibling-relative imports can't survive a single-file shim) get subpackage alias-shims — `import setec.oracle_pkg as oracle_pkg` at the directory level — rather than per-file shims. A shim-conformance AST test pins the canonical template so a hand-edited shim can't drift from it.

### 4. Layering — re-measured, with a shared-surface tier

The corrected AST pass finds ~91 surface-to-surface import edges. A new **L2 shared-surface tier** — `variance_audit`, `manifest_validator`, `originality_audit`, `repetition_audit`, `manuscript_audit`, `voice_fingerprint`, `acquisition_core`, `check_corpus` — legitimizes ~60 of those edges as sanctioned fan-in (these are audited as shared dependencies, not violations), with an acyclicity check enforced across the tier. The remaining edges become a **pinned `frozenset` allowlist** with a max-length ratchet: the allowlist may only shrink release-over-release, never grow (same ratchet discipline applies to the `sys.path.insert` lint count in §6). The gate contract is explicit: `tools/check_layering.py` (new), reads the AST-derived edge list, compares against `core/` (imports only `contract/`), `contract/` (imports nothing internal), and the L2 allowlist; exits non-zero on any edge not in {sanctioned L2, pinned allowlist}.

### 5. Claim-license firewall closure — required before P4 touches any surface

Before P4 opens: a **pre-migration snapshot** of `claim_license` output and `claim_license_rendered` text for all ~73 confirmed `_claim_license` definers. Per-family P4 gate becomes: bytes identical to snapshot, **OR** an owner-signed change entry in the PR body quoting old vs. new `does_not_license` text verbatim. `harness.py` owns **layout only** — argparse groups, envelope assembly plumbing, report skeleton — and is explicitly barred from supplying a default for `licenses`, `does_not_license`, or `comparison_set`; there is no harness-level fallback content for any of the three. A mechanical check (new, P4 gate) asserts every surface's `does_not_license` is non-empty and not string-identical to any other surface's. The 31k-LOC boilerplate-reduction target is amended to exclude `_claim_license` bodies — the harness reduces plumbing, never claim-license content.

### 6. Test/CI substrate

Tests stay at `plugins/setec-voiceprint/scripts/tests/` (default; owner decision point 2 below asks for explicit confirmation, since a move to a top-level `tests/` is the plausible alternative and this spec takes no position on which is "more correct," only on landing one config owner). Config ownership resolves to **one file**: the repo-root `pytest.ini` remains sole authority; the new `pyproject.toml` carries build metadata (`[build-system]`, `[project]`) only and defines no `[tool.pytest.ini_options]` section, so there is no second config to go silently inert. `pytest.ini` gains `pythonpath = plugins/setec-voiceprint/scripts`, which — because every one of the 7 confirmed CI jobs invokes `python -m pytest` from repo root and inherits the same `pytest.ini` — resolves package availability for all 7 jobs from one edit rather than 7 separate ones; no job needs its install step changed for local-only imports, though the 6 focused jobs' bare-`pytest`-only installs are still a real gap for tests that import third-party deps (unrelated to this spec, noted for `tools/gate_all.py`-style follow-up). Repairing a red confinement/mode-guard job by deselecting its failing tests is explicitly prohibited — a red focused job blocks the phase, full stop. A P1 gate requires bare `pytest` from repo root and the CI path-arg invocation form to collect **identical test counts** (catches config drift immediately). The `sys.path.insert` bootstrap codemod (P1) splits into two AST-decided passes: 199 files where the bootstrap is used only for import resolution (mechanical deletion), and 99 files that also reuse the same root binding for on-disk data paths (need a `paths.find_plugin_root()`-based replacement, not deletion).

### 7. Drift gate — filed to P4, both halves specified

v1 filed this under P2; it belongs in P4, after the shim template (§3) is finalized, not before. Two changes land together: `parse_task_surface` (in `check_capabilities_drift.py`) resolves one level of `ImportFrom` — following the shim's `from setec.surfaces.foo import TASK_SURFACE` line to the real constant; `find_scripts` gains an explicit package-directory rule so it doesn't also walk `scripts/setec/` as if it were a second flat script tree. `script_path` in `capabilities.d/*.yaml` stays pointed at the shim (Constraint 1), never at the package module. Every packaged surface carrying a `TASK_SURFACE` constant must have a matching shim; a new check enforces the converse (no orphan package surfaces without a shim, no orphan shims without a package surface). A shim-conformance AST test (§3) pins the canonical template so this gate can rely on its shape.

### 8. Zero required dependencies

`pyproject.toml`'s `[project]` dependencies are empty. The 5 confirmed requirements files (`requirements.txt`, `requirements-acquisition.txt`, `requirements-calibration.txt`, `requirements-surprisal.txt`, `requirements-replication.txt`) are mirrored as `[project.optional-dependencies]` extras of the same names. A new no-extras CI job installs `setec` bare and asserts a tier-1 surface still emits a schema-1.0 envelope whose `ai_status` truthfully reports the degraded tier — proving graceful degradation isn't just a docstring claim.

### 9. `tools/` retargeting

Five confirmed hardcoded-root tools move into the P1 work item, not left implicit: `check_capabilities_drift.py`, `check_docs_freshness.py`, `gen_calibration_readiness.py`, `seed_capabilities.py`, `check_register_sweep_h1_gate.py` — each gets its root resolution routed through `setec.paths.find_plugin_root()`. `gen_calibration_readiness.py --check` is added to every phase's acceptance gates, not just T4-equivalent checkpoints. `tools/spec_anchor_lint.py`'s boundary is stated explicitly: it lints spec-anchor comments in `tools/` and `scripts/` top level only; it does not descend into `scripts/setec/`, which carries no spec-anchor convention of its own.

## Phases (each an independent PR train, suite green at every boundary)

- **P0 — checkout & hygiene preflight.** Local checkout restored to `main` at/past **both** confirmed floors (voicewright v1.128.1, APODICTIC v1.127.0 — the higher one governs). Prune ~30 stale registered worktrees; delete `.claude/worktrees/musing-lederberg-59d71a/` (confirmed second copy of `scripts/`+`tools/` that will poison every codemod grep). **New:** rule on the confirmed repo-root symlinks (`scripts`, `references`, 4× `requirements-*.txt`) — keep as the distribution mechanism they already are; every P1+ codemod must pass `-not -type l` / equivalent no-follow-symlinks so it doesn't double-visit files reachable both directly and through the symlink.
- **P1 — packaging skeleton, mechanism, paths, tools/, zero moves.** Land the layout from Design §1 (hermetic gate required to close this phase), `setec.paths` (§2) and its codemod, the two-pass `sys.path` bootstrap codemod (§6), `pyproject.toml` with zero required deps + extras (§8), the `tools/` retargeting (§9), and the pytest-config consolidation (§6, including the identical-test-count gate). No production module moves.
- **P2 — L0 contract.** Move `output_schema`, `claim_license`, `capabilities` into `setec.contract`; sys.modules alias shims in place (§3); codemod the 108/109 importers to package imports; convert `_load_surface_labels`'s fail-open branch to raise (§2). Gate: contract fixtures byte-identical for the 17 covered surfaces; each moved registry loads a non-zero count at an identical absolute path pre/post move. (Drift-gate teaching is **not** in this phase — see P4.)
- **P3 — L1 core.** Move the confirmed pure-library modules (no `__main__`, no capability fragment — `acquisition_core.py` and `length_bootstrap.py` are explicitly **excluded**, corrected to L2 per Design §4). Break the 5 import cycles here.
- **P4 — harness + surface migration + firewall closure + drift gate.** Land the claim-license snapshot and per-family gate (§5) **first**, before `harness.py` lands. Then `harness.py` (layout-only, §5), migrated family-by-family, highest-boilerplate-share first: `calibration/fetch_*` (4 files, Jaccard up to 0.51), the LLM-judge scan family (~59% boilerplate each), AIC audits, acquirers, then long-tail. Drift-gate teaching (§7) and the shim-conformance test land in the same PR train as the first family. Envelope goldens re-checked per family; claim-license snapshot re-checked per family.
- **P5 — enforcement on.** Layering gate (§4, `check_layering.py`, L2 tier + pinned allowlist + ratchet) and `sys.path.insert` lint become required checks, both with the ratchet-may-only-shrink rule. Registry gap sweep, **fragment-minting removed from scope** (corrected — see Owner decision points): the confirmed already-fragmented surfaces (`acquisition_core`, `length_bootstrap`, `register_sweep`/`register_composition_sweep`) are audited for correctness, not re-minted; the remaining plausibly-user-facing scripts with `__main__` but no capability entry get explicit internal markers, not new fragments.

**Out of scope (deliberately, unchanged from v1):** splitting `variance_audit.py` (4,252 LOC) and `acquire_imessage_sent_atomic.py` (19,127 LOC) internally — P2-P4 only *relocate* them.

## Acceptance gates (every phase, in addition to the phase-specific gates above)

1. Full pytest suite green (7,437 tests), bare-`pytest`-from-root and CI-path-arg counts identical (§6).
2. `gen_contract_fixtures.py --check`: envelope goldens byte-identical for the 17 covered surfaces; claim-license snapshot check (§5) for the ~73 `_claim_license` surfaces from P4 onward.
3. `tools/check_capabilities_drift.py` (P4+: shim-aware per §7) + `tools/check_docs_freshness.py` + `tools/gen_calibration_readiness.py --check` green.
4. Hermetic dispatcher smoke (§1): scratch copy, no install, empty `PYTHONPATH`, `cwd` outside repo — `setec_run.py <surface> --json` for all 10 voicewright-consumed surfaces + the 5 skill-headlined surfaces (derived from SKILL frontmatter, not hand-picked) produces schema-1.0 envelopes. Heavy-dependency surfaces get a stub-backend contract so this gate doesn't require GPU/model weights.
5. Consumer cross-check: voicewright's `tools/check_setec_contract.py` and `scripts/sync_setec.py --check` pass against the local checkout at v1.128.1; APODICTIC contract tests pass at v1.127.0.
6. Shim mutation-transparency test (§3): patch a constant and a private helper through the script-name path; assert the package module observes both.
7. Layering gate (§4) and `sys.path.insert` lint: both ratchets hold or shrink.

"P4-complete" is defined as an enumerable predicate, not a narrative milestone: every surface with a `capabilities.d` fragment has (a) a shim passing the conformance test, (b) a claim-license entry that is either byte-identical to its snapshot or has a signed change entry, and (c) an envelope that round-trips gate 4 in stub-backend mode where applicable.

## Risks

| Risk | Mitigation |
|---|---|
| Dual-instance modules via shim + package name | shim identity is now structural (`sys.modules[__name__]` swap), not convention; mutation-transparency test (gate 6) checks it directly |
| Envelope drift from moved constants | goldens as byte-oracle for the 17 covered surfaces (gate 2); L0 moves isolated in their own PR |
| Claim-license content softened under harness cover | firewall closure (§5) lands before P4's harness; harness barred from content defaults; mechanical non-empty/non-identical check |
| `TASK_SURFACE` invisible to drift gate after move | drift-gate teaching is now its own P4 deliverable (§7), sequenced after the shim template is final, not raced against P2 |
| `parents[N]` re-anchoring on module move | `setec.paths` + codemod lands in P1, before any move; each move phase gates on non-zero registry counts + identical absolute paths |
| Windows/portable paths (`windows_descriptor_io`, `windows_portable_tree`) behave differently under package `__file__` resolution | those modules move late in P3 with their dedicated tests; no data-file relocation in this spec |
| 23 near-simultaneous PR streams colliding (351 merge commits of history says traffic is real) | phases are strictly serialized; within P4, one family per PR |
| Repo-root symlinks double-visited by codemods | P0 ruling + no-follow-symlinks rule applies to every codemod from P1 onward |
| CI jobs silently skip real coverage (6 of 7 install bare pytest) | pytest.ini pythonpath fix (§6) closes the import-resolution gap; dependency-coverage gap flagged as pre-existing, out of this spec's scope, referred to `tools/gate_all.py` follow-up |

## Owner decision points

1. **Layout ruling (a) confirmation.** This spec adopts option (a) — package at `scripts/setec/`, no `src/`, no install step — as the recommended default per the review's orchestrator ruling. Owner may override, but any override must re-clear the hermetic gate (§1) against the actual consumer invocation paths before it ships.
2. **Tests-directory destination.** Default in this rewrite: stay at `plugins/setec-voiceprint/scripts/tests/` (least churn, consistent with the layout (a) package also living inside `scripts/`). Alternative: a top-level `tests/` sibling to `scripts/`. Needs explicit confirmation before P1's pytest-config consolidation (§6) locks in.
3. **L2 shared-surface tier membership.** Proposed set: `variance_audit`, `manifest_validator`, `originality_audit`, `repetition_audit`, `manuscript_audit`, `voice_fingerprint`, `acquisition_core`, `check_corpus`. Owner confirms membership before P5's layering gate goes required, since the pinned allowlist ratchet only shrinks from here.
4. **Shim permanence.** Unchanged question from v1: does the shim layer live as long as the M1 contract does, or get a deprecation horizon in the next major contract rev? Now sharper given §3's structural (not star-import) shim design — a permanent sys.modules-alias shim has near-zero maintenance cost, which weakens the case for ever deprecating it.
5. *(Carried from v1, now resolved rather than open)* Package name: **`setec`**, confirmed, no change.
6. *(Carried from v1, now resolved rather than open)* P5 registry gap sweep: **explicit internal markers**, not fragment-per-script — fragment-minting is cut from scope per the review (already-fragmented surfaces need auditing, not new fragments).
