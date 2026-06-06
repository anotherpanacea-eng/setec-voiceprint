# SETEC normalized-entrypoint — implementation spec (producer side)

**Status:** implementation spec / proposal (SETEC producer side). Answers the open questions in APODICTIC's consumer-requirements note and maps R1–R5 onto SETEC's internals.
**Date:** 2026-05-30
**Direction:** SETEC Voiceprint (producer) ← APODICTIC (consumer).
**Companions:**
- APODICTIC `docs/setec-normalized-entrypoint-requirements.md` — the R1–R5 request this answers.
- APODICTIC `docs/setec-dependency-posture.md` — per-audit required/optional classification + the output-validity gate.
- SETEC `references/apodictic-handoff-storyscope.md` — the existing Surface-6 handoff contract this generalizes.

---

## 0. Grounding — what already exists vs. what's missing

The consumer note references APODICTIC-side modules (`setec_runner.run_supplement`, `setec_discovery`) and version floors (`1.86.0`, `1.107.0`) that live in **APODICTIC**, not here. On the SETEC side the relevant machinery is:

| Concern | SETEC location | State |
|---|---|---|
| Capabilities manifest | `plugins/setec-voiceprint/capabilities.yaml` (schema **v0.3.0**) + `capabilities.py` (`list` / `show`, `--handoff`, `--consumer`) | Exists. Has `id, script_path, surface, family, status, handoff, consumers, compute{tier}, dependencies, outputs{schema_version, artifacts}` per entry. |
| Envelope builder | `output_schema.py` → `build_output()` (`SCHEMA_VERSION = "1.0"`) | Exists. 12 fixed top-level keys + optional merged extras (see §5). |
| Surface registry | `calibration/task_surfaces.py` (`TASK_REGISTRY`, `get_task`) + `shard_runner.py` (`--task`) | Exists for the *sharded* calibration surfaces; the consumer surfaces are per-script CLIs. |
| Per-script CLIs | `variance_audit.py`, `voice_distance.py`, …, `narrative_decision_audit.py` | Each has its own argparse + `--json` (stdout) or `--json-out` (file). |
| Version SOT | `.claude-plugin/plugin.json` (semver; `feat:`→MINOR, `fix/chore/docs:`→PATCH) | Current ≈ **1.109.x**. |
| Contract tests | `tests/test_output_schema.py` (assertion-based key/shape checks) | Exists. **No golden-envelope fixtures, no reference fake-SETEC.** |

**Missing for R1–R5:** per-surface `min_setec_version`, `json_delivery`, and `inputs` in the manifest; a top-level `setec_version`; a `--json` emission of the whole manifest; a uniform `setec run <surface> --json` dispatcher; a machine-readable error model with `reason_category`; golden fixtures + a shared fake. This spec adds exactly those.

---

## 1. R1 — Capabilities query (data source + shape)

**Decision: extend `capabilities.py`, do not add a second source of truth.** `capabilities.yaml` stays the single manifest; the query is a thin emit + a few new per-entry fields.

New CLI:
- `capabilities.py emit --json` → the full manifest as the R1 envelope (alias the consumer can spell `setec capabilities --json` once the dispatcher in §2 lands).
- `capabilities.py show <id> --json` → one surface (alias `setec describe <surface> --json`).

New per-entry manifest fields (additive; `seed_capabilities.py` + `check_capabilities_drift.py` updated to lint them):
- `min_setec_version` (string semver) — the floor the consumer asserts against. **Retires APODICTIC's hardcoded `MIN_SETEC_VERSION` per shim (R1 acceptance criterion).**
- `json_delivery` (`"stdout"` | `"file"`) — current truth per surface; target is uniformly `"stdout"` once §2 lands.
- `calibration_status` — surface the existing `status` (`heuristic|literature_anchored|calibrated|…`) under the name the consumer expects.
- `inputs[]` — `{flag, type, required, values?}` so the consumer builds the arg list without guessing.

Top-level fields the emit adds around `entries`:
- `setec_version` — read from `.claude-plugin/plugin.json` (the SOT), not duplicated.
- `manifest_schema_version` — the existing `0.3.0` (distinct from the per-surface envelope `schema_version: 1.0`).

> **Answers OQ1 (capabilities source):** emit `capabilities.yaml` directly through `capabilities.py emit --json`; a thin normalization layer adds `setec_version` (from plugin.json) and validates the new fields. No second manifest.

---

## 2. R2 — Normalized entrypoint + dispatcher→script mapping

**Decision: a thin `setec run <surface> [args] --json` dispatcher, table-driven from `capabilities.yaml`.** Not a rewrite of each script.

- `surface → script` comes from the manifest's existing `script_path`. The dispatcher resolves `<surface>` → script, execs it, and **guarantees the envelope reaches stdout** regardless of the underlying script's native delivery.
- For surfaces already on stdout `--json` (variance_audit, voice_distance, idiolect_detector, punctuation_cadence, narrative_decision_audit, manuscript_audit, repetition_audit, voice_profile): the dispatcher passes `--json` through.
- For the one file-only surface (`pov_voice_profile.py`, `json_delivery: file`): the dispatcher injects a private `--json-out <tempdir>` under `ai-prose-baselines-private/`, reads the artifact, and **re-emits the consumer envelope to stdout** — so the consumer never touches `--json-out` (the exact thing that broke pov_voice_profile on PR #6). See §3.
- The dispatcher owns one flag (`--json`), eliminating argparse prefix-match ambiguity and the `--json-out=`/split-form variance from the consumer path.

> **Answers OQ2 (dispatcher vs. per-script):** prefer the **dispatcher** — it normalizes all nine surfaces in one place with no per-script churn, and it's where R3's structured-error wrapping and R1's floor check naturally live. Per-script flag normalization would touch nine argparsers and still leave the stdout-vs-file split. The dispatcher subsumes it. (Per-script normalization can still happen opportunistically; it's not a prerequisite.)

Dispatcher responsibilities, in order: (1) resolve surface from manifest or return R3 `bad_input`; (2) assert `min_setec_version` ≤ `setec_version` or return R3 `version_floor`; (3) check `dependencies.python` availability or return R3 `missing_dependency`; (4) exec the script; (5) ensure the envelope is on stdout; (6) on script failure, wrap as an R3 error envelope.

---

## 3. Artifact vs. consumer-envelope output handling

The consumer envelope and the private voice-cloning artifact are **two different things** and must not be conflated (the pov_voice_profile bug):

- **Consumer envelope** = the `schema_version: 1.0` `build_output()` payload. Always emitted to **stdout** via the dispatcher. Slim; safe to parse; no private voice-clone material beyond what the audit already licenses.
- **Artifact** = POV voiceprints, idiolect baselines, scored-record caches. Stay governed by SETEC's **default-private policy** (`--json-out` into `ai-prose-baselines-private/`). They remain an *internal* concern; the dispatcher may write them but never requires the consumer to fetch the envelope through them.

> **Answers OQ3 (envelope-vs-artifact split for pov_voice_profile / idiolect_detector):** the producing script writes its rich artifact under the private policy as today; the dispatcher (§2) reads it, projects the consumer-facing subset into the `build_output()` envelope, and emits that to stdout. `idiolect_detector` already has a stdout `--json` mode (it's required-but-stdout per the posture note), so it needs no projection — only `pov_voice_profile` does. Long-term: give `pov_voice_profile.py` a native stdout `--json` that emits the envelope while `--json-out` keeps writing the artifact, so the projection step can retire.

---

## 4. R3 — Structured error model

**Decision: one envelope shape for success and failure; a `reason_category` enum; a stable exit-code scheme.**

- A failed run emits the **same `schema_version: 1.0` envelope** with `available: false`, plus:
  - `reason` (human text) and `reason_category` ∈ `{version_floor, missing_dependency, bad_input, text_too_short, policy_refused, internal_error}`.
  - For `version_floor`, report the **requested** floor and the **observed** `setec_version` (never a default — the `_install_instructions` self-contradiction bug).
- Exit codes: **0** success (envelope, `available:true`); **2** discovery/version (bad surface, version floor); **3** contract/usage (bad input, policy refusal); **1** unexpected internal error. The envelope is still emitted on 2/3 so the consumer can branch on `reason_category` without scraping stderr.

> **Answers OQ4 (error surface):** `available:false` + `reason_category` in the standard envelope is sufficient for *runtime* failures; for *pre-run* failures (unknown surface, version floor) emit the **same envelope shape** with `available:false` — no separate typed-error schema. One shape, one parser. The exit code distinguishes the class for callers that branch on it before reading JSON.

---

## 5. R4 — Version & compatibility semantics

- `schema_version` (currently `"1.0"`, from `output_schema.SCHEMA_VERSION`) **is the contract**. Additive-only within the major; any breaking change to the 12-key envelope bumps it to `2.0` and is announced via the R1 manifest, never discovered by a consumer crash. (The CHANGELOG already commits to "Major version is reserved for breaking changes to the public CLI / JSON contract.")
- The 12 fixed envelope keys (`build_output()`): `schema_version, task_surface, tool, version, available, target, baseline, results, claim_license, claim_license_rendered, warnings, ai_status` (plus any optional merged `extra` keys). `test_output_schema.py::REQUIRED_TOP_LEVEL_KEYS` is the pin.
- `setec_version` (plugin.json) is independent of `schema_version`; per-surface `min_setec_version` (R1) gates feature availability. A surface below floor → R3 `version_floor`.
- **Output-validity gate (from the posture note):** computational surfaces self-validate raw outputs against cheap bounds (surprisal ≤ log │vocab│; cosine ∈ [−1,1]; finite vectors) and emit R3 `internal_error` rather than an out-of-bounds number. Add bounds checks at the `build_output()` boundary so an invalid computation can never enter the envelope.

---

## 6. R5 — Producer-side contract tests + shared fixtures

Current `tests/test_output_schema.py` asserts key shape but has **no golden fixtures and no reference fake**. Add:

- **Golden envelopes** — one canonical `schema_version: 1.0` JSON per `handoff: stable`/`experimental` surface, checked into `plugins/setec-voiceprint/references/contract_fixtures/`. Producer CI asserts each surface's `build_output()` matches its golden (modulo volatile fields: timestamps, run ids, paths — normalized before compare).
- **Reference fake-SETEC** — a tiny `fake_setec.py` that emits a valid envelope per surface from a fixture, so APODICTIC's CI verifies its parser **without installing torch/spaCy**. APODICTIC has hand-rolled `pov_voice_profile.py` three times; this replaces that.
- **Shared location (OQ5):** fixtures live **here** (`references/contract_fixtures/`, the producer owns the contract); APODICTIC vendors them via a pinned copy or git-subtree/submodule, refreshed on `schema_version`/manifest bumps. Avoids a circular build dependency — neither repo imports the other; both compare against the same JSON. The drift checker (`tools/check_capabilities_drift.py`) gains a `fixture_matches_build_output` check so a surface whose envelope drifts from its golden fails SETEC CI pre-merge.

> **Answers OQ5 (fixture ownership):** producer-owned (`setec-voiceprint/references/contract_fixtures/`), consumer-vendored by pinned copy. The contract's author is its fixture's author.

---

## 7. Target SETEC version

All changes are additive to `schema_version: 1.0` and the v0.3 manifest, so none forces a major bump:

- **R1 (capabilities emit + new manifest fields)** + **R5 (fixtures + fake)** — next **MINOR** (`feat:`), the highest-leverage/lowest-risk slice; ship together so conformance is CI-verified from day one. Target ≈ **1.110.0**.
- **R2 (dispatcher) + R3 (structured errors)** — a subsequent **MINOR** once R1 is in (the dispatcher reads R1's manifest fields). Target ≈ **1.111.0**.
- **R4** semantics are documentation + the validity-gate assertions; fold into the R1 minor.
- `schema_version` stays **1.0** throughout (additive only).

---

## 8. Phasing (producer order, mirrors the consumer note)

1. **Phase 1 — R1 + R5 (≈1.110.0).** Add `min_setec_version`/`json_delivery`/`inputs`/`calibration_status` to `capabilities.yaml`; `capabilities.py emit --json` with top-level `setec_version`; golden fixtures + reference fake; drift checks. APODICTIC can delete per-shim floors immediately while still calling existing scripts.
2. **Phase 2 — R2 + R3 (≈1.111.0).** Ship `setec run <surface> --json` dispatcher with the floor/dependency checks and the R3 error model; project pov_voice_profile's artifact to a stdout envelope. APODICTIC collapses `run_supplement` to one stdout path and removes the `--json-out` family.
3. **Cross-cutting — R4 + validity gate.** Land with Phase 1; enforce in `build_output()` and CI.

---

## 9. Open questions — consolidated answers

| OQ | Answer |
|---|---|
| 1. Capabilities source | Emit `capabilities.yaml` via `capabilities.py emit --json`; thin layer adds `setec_version` (plugin.json) + validates new fields. No second source. |
| 2. Dispatcher vs. per-script | Dispatcher (`setec run <surface> --json`), table-driven from the manifest. Normalizes all 9 surfaces + hosts floor/error logic in one place. |
| 3. Envelope vs. artifact (private surfaces) | Script writes the private artifact (default-private policy); dispatcher projects the consumer subset into the stdout envelope. Long-term, give pov_voice_profile a native stdout `--json`. |
| 4. Error model surface | One envelope shape; `available:false` + `reason_category` for both runtime and pre-run failures; exit codes 0/2/3/1 classify without stderr scraping. |
| 5. Fixture ownership | Producer-owned in `references/contract_fixtures/`; consumer vendors a pinned copy. No circular dependency. |

---

## Non-goals (inherited)

- Not fusing the tools or changing the subprocess boundary.
- Not dropping `--json-out` / the private-output policy for **artifacts** — only ensuring the consumer envelope is reachable on stdout independent of it.
- Not taking on APODICTIC's claim-license / verdict semantics.
