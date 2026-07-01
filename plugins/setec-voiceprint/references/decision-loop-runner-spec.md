# Spec 02 — Decision-loop runner: wire the orphaned disagreement resolver (setec-voiceprint)

> A multi-surface run-set runner (`setec_run_set.py`) that executes a named set of
> surfaces over one target, collects their schema-1.0 envelopes into a run folder,
> feeds them to the existing `surface_disagreement_resolver`, and emits disagreement
> patterns + a mechanical next-action block. **No composite score. No verdict. Ever.**

**Status:** **Built** (`scripts/setec_run_set.py` + fragment completion + skill wiring, this PR; spec-review pass 1 folded 2026-07-01 verdict BUILD-READY-WITH-FIXES 0 P1; Opus build-review READY-TO-PR, 0 P1/P2, spec-reference P3 folded). **Estimate:** 2–3 build sessions, one PR.
**Owner of the decision:** craft.
**Provenance:** Fable pick #2; territory the Opus 4.8 audit did not enter (APODICTIC-only read).
**Repo:** `~/Documents/Code-Mac/setec-voiceprint`. All paths below are repo-relative; all `file:line` cites verified against `main` @ `8e85dac` (2026-07-01).

---

## 0. Anchor corrections (verified this session — supersede the stub where they differ)

Every anchor below was opened/grepped before being asserted (AGENTS.md mode-1 rule).

1. **The mechanical anti-Goodhart precedent is NOT `corpus_novelty_audit` / `voice_verifier.py`.**
   Neither file contains a banned-key walk (grepped both). The real precedents are:
   - **Runtime guard:** `within_doc_segmentation.py:89-158` — `FORBIDDEN_RESULT_KEYS`
     (frozenset, exact key match at any depth), `FORBIDDEN_SUBSTRINGS` (key-only
     substring walk), `BAND_VOCAB` whitelist, and `assert_no_authorship()` (recursive
     walk raising `AuthorshipClaimError`). This is the shape to mirror.
   - **Test-side guard:** `scripts/tests/test_distinct_diversity_audit.py:58-228` —
     `_FORBIDDEN_KEYS = {"is_ai","is_human","verdict","label","same_author","score"}`
     + `_walk_keys()` recursive no-verdict test; same pattern in
     `test_within_doc_segmentation.py:196-330`.
   - `corpus_novelty_audit.py:7-13` contributes the *posture prose* ("a lone scalar is
     a verdict in disguise"), not a mechanical walk. Cite it for posture only.
2. **`_golden_capabilities/` lives at `plugins/setec-voiceprint/scripts/tests/_golden_capabilities/`**
   (not under `scripts/` directly). A seeded, all-TODO
   `surface_disagreement_resolver.json` golden **already exists** there mirroring the
   TODO yaml — completing the yaml fragment requires reblessing that golden in the same
   commit (rebless recipe is in the comment block above `_load_golden_by_id()` /
   `_golden_meta()` in `test_capabilities_dropin.py`).
3. **`capabilities.py recommend` = `cmd_recommend` at `capabilities.py:710`; the engine
   is `recommend()` at `capabilities.py:497`** (returns ranked
   `(id, entry, matched_keywords)` tuples; `--format json` supported). Stub said :710 —
   confirmed close enough; both cited exactly now.
4. **`setec run` cannot execute most of the resolver's input surfaces.** The dispatcher's
   consumer gate is *presence of `json_delivery`* (`setec_run.py:99-113`); only the 14
   contract surfaces carry it. Of the resolver's eight inputs, only `variance_audit`,
   `voice_distance`, `general_imposters`, `idiolect_detector` are consumer surfaces;
   `paragraph_audit`, `discourse_move_signature`, `agency_abstraction_audit`,
   `aic_pattern_audit` are **not** — so a runner built on `setec run <surface>`
   subprocess calls is impossible as scoped. The runner must exec member scripts
   directly from the manifest's `script_path` (§4).
5. **Member CLI variance (verified per script):** positional arg is `input` for
   `variance_audit.py:3655`, `paragraph_audit.py:~901`, `discourse_move_signature.py:~1127`,
   `agency_abstraction_audit.py:~631`, but **`target`** for `aic_pattern_audit.py:817`.
   `voice_distance.py:690` *requires* `--baseline-dir` or `--manifest` (argparse error
   otherwise). `idiolect_detector.py:~924` has **no single-file target flag** — its
   target group is `--target-dir | --manifest` (corpus-shaped), so it cannot run from a
   lone draft file. Consequence: idiolect + GI are **attach-only** set members in M1 (§4.3).
6. **The resolver consumes *raw report dicts*, not envelopes.** `_read_smoothing_level`
   reads `variance["compression"]["band"]` (`surface_disagreement_resolver.py:88-98`);
   the schema-1.0 envelope nests that under `results` (verified against
   `references/contract_fixtures/variance_audit.json` — `results.compression.band`;
   `voice_distance` → `results.overall.band`; `general_imposters` → `results.decision`;
   `idiolect_detector` → `results.preservation_list`). Feeding envelopes unmodified
   would silently read every signal as `unknown`. The runner unwraps
   `envelope["results"]` before calling `resolve()` — **zero resolver changes needed**
   (§5). The resolver's own CLI (`surface_disagreement_resolver.py:753-799`) is left
   exactly as it exists.
7. **`general_imposters.py:451-454` emits `consistent_with_candidate` /
   `inconsistent_with_candidate` / `gray_zone_refused`** — matching the resolver's
   `_read_gi_decision` (`surface_disagreement_resolver.py:161-173`). ✓ compatible.
8. **Post-#170 drop-in conventions confirmed:** `capabilities.d/<id>.yaml` fragment
   (one entry per file, `entries:` list + `script_path:` — sibling model:
   `capabilities.d/dependency_distance_audit.yaml`); per-id
   `_golden_capabilities/<id>.json`; `changelog.d/<slug>.md` fragment; **no `==N` count
   literal anywhere**; `claim_license_surfaces/validation.txt` already exists so **no
   new task-surface fragment is needed** (`TASK_SURFACE = "validation"`, same as the
   resolver, `surface_disagreement_resolver.py:70`).
9. **Drift-lint gates on promotion:** `tools/check_capabilities_drift.py:308-336` —
   any `status != todo` entry must have non-TODO `family` / `use_when` /
   `do_not_use_when`; `handoff: stable` additionally requires non-empty `references`.
   The resolver fragment completion must satisfy the first; we stay `handoff: none` so
   the second doesn't bind.
10. **`restoration_packet.py:1295-1326`** consumes `--variance-json --bigram-json
    --voice-json --idiolect-json --aic-json` (+ `--genre`, `--target-scope`,
    `--json-out`). **`before_after_restoration.py:991-1013`** consumes paired
    `--before-*/--after-*` JSONs. Both are file-path CLIs — the next-action block can
    prefill their commands from run-folder paths verbatim (§6.3).
11. **Neither skill references the resolver or any multi-surface path** (grepped
    `skills/setec/SKILL.md` and `skills/smoothing-diagnosis/SKILL.md` for
    `setec_run|setec run|surface_disagreement` — zero hits). The wiring in §8 is new.

---

## 1. Problem & verified evidence

Cross-surface interpretation is built but orphaned; multi-surface execution doesn't
exist at all.

- `plugins/setec-voiceprint/scripts/setec_run.py` (645 lines): dispatches exactly
  **one** surface per invocation (`setec run <surface> --json`); `--list`
  (`setec_run.py:609-617`) enumerates, nothing chains. Its R1–R5 responsibilities
  (resolve → version floor → dep check → exec → envelope guarantee → R3 error wrap)
  are documented in-file (`setec_run.py:21-47`) and in
  `references/setec-normalized-entrypoint-spec.md` §2–§4.
- `plugins/setec-voiceprint/scripts/surface_disagreement_resolver.py` (853 lines, real
  implementation): reads any subset of audit JSONs and surfaces interpretable
  disagreement patterns via a 10-entry `DISAGREEMENT_PATTERNS` table
  (`surface_disagreement_resolver.py:330-476`: `edited_authorial_voice`,
  `register_shift_or_collaboration`, `self_conscious_imitation`,
  `syntactic_template_shift`, `rhetorical_habit_not_smoothing`,
  `gi_inconclusive_despite_drift`, `register_drift_to_institutional`,
  `discourse_scaffolding_overload`, `paragraph_regularization_only`,
  `agreement_high_compression`). Its own docstring: cross-surface interpretation
  "*has been left to the reader to do by hand*"
  (`surface_disagreement_resolver.py:10-11`).
- `capabilities.d/surface_disagreement_resolver.yaml`: seeded stub — `status: todo`,
  `consumers: []`, `use_when: [TODO]`, `family: TODO`. Zero references from any skill.
  Because `status: todo`, `recommend()` **skips it entirely**
  (`capabilities.py:524-525, 538-539`) — the router literally cannot suggest it.
- `capabilities.py recommend --situation` exists (`cmd_recommend`,
  `capabilities.py:710`) — the "which surfaces" half of the loop is shipped.
- `restoration_packet.py` + `before_after_restoration.py` exist — the "what next" half
  is shipped (anchor #10).

The operator today hand-chains 3–6 scripts, carries JSON between them, and does the
cross-surface read by eye. Every diagnostic session.

## 2. Decision it changes

"What do I run next, and does this draft need another restoration pass" — the
per-session craft decision, currently made by eyeballing separate JSON envelopes.
After M1: one command produces the full-picture readings table, the matched
disagreement interpretations, and the exact follow-up commands — with the
interpretive posture (differential, never verdict) enforced mechanically.

## 3. CLI shape — decision

**Decision: a sibling script `plugins/setec-voiceprint/scripts/setec_run_set.py`, NOT
an extension of `setec_run.py`.** Argued:

- `setec_run.py` **is the pinned R2 consumer contract**. Its docstring commits to
  owning "exactly ONE consumer flag (`--json`)" (`setec_run.py:11-15`); apodictic and
  setec-voicewright vendor drift gates around its behavior (AGENTS.md §Fleet). Adding
  `run-set` / `--set` modes expands the contract surface for consumers who never asked,
  and every future runner change would ripple through two downstream re-pin trains.
- The runner's exit semantics differ (partial success is *normal* — a set member
  abstaining on `missing_dependency` must not fail the run), so grafting it onto the
  dispatcher would fork `_CATEGORY_DEFAULT_EXIT` behavior inside one tool.
- Precedent: `capabilities.py`, `restoration_packet.py`, `before_after_restoration.py`
  are all operator-side siblings, not dispatcher modes.

The `setec run-set` *spelling* can arrive in M2 if/when the runner is promoted to a
consumer surface (§7). M1 invocation:

```bash
python3 plugins/setec-voiceprint/scripts/setec_run_set.py \
    --set full_picture \
    --target draft.md \
    [--baseline-dir baselines/blog-essay/] \
    [--attach general_imposters=out/gi.json] \
    [--attach idiolect_detector=out/idiolect.json] \
    [--ai-status ai_edited] \
    [--out-dir setec-run-sets/2026-07-01-draft/] \
    [--resume] [--json] [--situation "…"] [--list-sets]
```

- `--set <name>` — named preset (§4.2). Mutually exclusive with `--surfaces a,b,c`
  (explicit member list, same execution rules).
- `--target` — the draft file. Required unless `--list-sets`.
- `--baseline-dir` — optional; projected into every member that accepts it
  (anchor #5). When absent, `voice_distance` is **skipped** with a synthesized
  `bad_input` member record naming the missing flag (never argparse-crashed, §4.4).
- `--attach <surface_id>=<path>` — repeatable. Joins an operator-supplied,
  pre-computed envelope (or legacy raw report) to the collection without executing
  anything. This is how GI / idiolect / any expensive or comparator-heavy surface
  enters the loop — **operator-supplied comparators unchanged**, honored verbatim.
- `--ai-status` — passed through to the resolver report (B.3 state-routed caveats,
  `surface_disagreement_resolver.py:788-798`) and stamped on the combined envelope.
  Pass-through only; no interpretation.
- `--situation "<free text>"` — **report-only in M1**: calls
  `capabilities.recommend()` (`capabilities.py:497`) and prints the ranked matches +
  which preset covers them; it does NOT drive execution (recommend can return
  comparator-requiring surfaces the runner has no args for; execution-from-situation
  is M2). It is an informational, report-only convenience: it does **not** supersede
  the `/setec` skill's recommendation authority — the skill remains the router of
  record for "which surfaces should I run."
- `--json` — emit the combined envelope to stdout (default: rendered markdown to
  stdout; both are always written to the run folder regardless).
- `--resume` — reuse any parseable `envelopes/<id>.json` already in `--out-dir`
  instead of re-running that member (§4.5).
- `--list-sets` — enumerate presets and their members, exit 0 (parallel to
  `setec_run.py --list`).

Exit codes (mirroring the R3 scheme, `setec_run.py:76-79`): **0** = run completed and
the resolver produced a report (even with some members unavailable — graceful
degradation is the design, `surface_disagreement_resolver.py:614-619`); **2** =
discovery (unknown set name, unknown surface id in `--surfaces`/`--attach`); **3** =
contract/usage (no target, unreadable attach file, non-empty `--out-dir` without
`--resume`); **1** = internal (including a tripped aggregate-verdict guard, §6.4). On
2/3/1 the runner still emits a `build_error_output()` envelope with `reason` +
`reason_category` (`output_schema.py:348+`), same as the dispatcher.

## 4. M1 scope — increments

### 4.1 Increment 1: run-set execution

`setec_run_set.py` resolves each member id via `capabilities.load_manifest()`
(`capabilities.py:98` — the canonical `capabilities.d/` loader), then for each member:

1. **Dependency pre-check** via `capabilities.entry_available(entry)`
   (`capabilities.py:208` — returns `(available, missing_required, missing_optional)`).
   Missing required deps → synthesize a `build_error_output(...,
   reason_category="missing_dependency")` member envelope **and continue** — the R3
   `reason_category` pass-through the stub requires. Compute tier is read from the
   fragment's `compute.tier` and recorded in the member record; nothing is gated on it
   (the dep check is the gate; tier is telemetry for the report).
2. **Exec the member script directly** by manifest `script_path` (absolute-resolved,
   same shape as `setec_run._script_abspath`, `setec_run.py:147-151`), with argv built
   from a **fixed projection table** (no argparse-prefix guessing — the exact defect
   class the dispatcher was built to kill, `setec_run.py:12-15`):

   | member id | argv projection |
   |---|---|
   | `variance_audit` | `<target> --json` [`--baseline-dir D`] |
   | `paragraph_audit` | `<target> --json` [`--baseline-dir D`] |
   | `discourse_move_signature` | `<target> --json` [`--baseline-dir D`] |
   | `agency_abstraction_audit` | `<target> --json` [`--baseline-dir D`] |
   | `aic_pattern_audit` | `<target> --json` [`--baseline-dir D`] |
   | `voice_distance` | `<target> --baseline-dir D --json` — **skipped (`bad_input` record) when D absent** |

   (Positional-name variance per anchor #5 is irrelevant at exec time — all are
   positional-first; the table pins it anyway and the tests assert exact argv.)
3. **Envelope recovery** reuses `setec_run._extract_envelope` /
   `setec_run._is_envelope` **by import** (`setec_run.py:298-374` — the fast-path +
   balanced-brace preamble-tolerant scanner). Nonzero exit / no envelope → wrap as an
   R3 member record with the `_wrap_script_failure` classification logic
   (`setec_run.py:244-295`; import it rather than copy).
4. **Write `envelopes/<surface_id>.json`** (the verbatim envelope, success or error)
   before moving to the next member — this file-per-member layout IS the checkpoint
   (§4.5), and a one-line progress record goes to **stderr** after each member
   (AGENTS.md §Long-running: belt/suspenders/buttons).

**Membership guard:** a member whose manifest entry carries `json_delivery: file`
(the voice-clone privacy surfaces `pov_voice_profile` / `voice_profile`,
`setec_run.py:33-37`) is **refused** with `bad_input` — the runner never injects
`--json-out` and never handles private artifacts. Those surfaces are out of the
decision loop by construction.

### 4.2 Increment 1a: presets

A module-level constant in `setec_run_set.py` (a new file — no shared-registry
collision, so post-#170 drop-in machinery is not needed for presets in M1):

```python
RUN_SETS: dict[str, tuple[str, ...]] = {
    # target-only; core/spaCy tier; every id verified in capabilities.d/
    "smoothing_core": (
        "variance_audit", "paragraph_audit", "aic_pattern_audit",
        "discourse_move_signature", "agency_abstraction_audit",
    ),
    # adds the comparator surfaces; voice_distance runs only with --baseline-dir;
    # general_imposters + idiolect_detector are attach-only (anchor #5) and listed
    # here so the report names them as expected-but-absent when not attached.
    "full_picture": (
        "variance_audit", "paragraph_audit", "aic_pattern_audit",
        "discourse_move_signature", "agency_abstraction_audit",
        "voice_distance", "general_imposters", "idiolect_detector",
    ),
}
ATTACH_ONLY: frozenset[str] = frozenset({"general_imposters", "idiolect_detector"})
```

Preset ids are resolved against the live manifest at runtime; an id that no longer
resolves → `bad_input` naming it (and a CI test pins every preset id to an existing
fragment, so a surface rename breaks the build, not the operator).

### 4.3 Attach-only members

`general_imposters` needs a candidate + impostor-pool manifest; `idiolect_detector`
has no single-file target mode (anchor #5). Both join via `--attach <id>=<path>`.
Attached files are copied verbatim into `envelopes/<id>.json` with an
`"attached": true` marker **in the member record (run_meta.json), never inside the
envelope itself** (pass-through purity, §6.4).

**Mechanical attach validation (accept/reject, no heuristics):** an attached file is
accepted iff it parses as a JSON object AND either (a) has `schema_version` +
`results` (a schema-1.0 envelope), or (b) has the attached surface's required
top-level reading key(s) — a legacy raw report. Required-key table (the same keys the
resolver's readers consume, anchor #6): `general_imposters` → `decision`;
`idiolect_detector` → `preservation_list`; `variance_audit` → `compression`;
`voice_distance` → `overall`; and analogously per the §4.4 kwarg map for the rest.
A file satisfying neither shape → the member record is `reason_category: "bad_input"`
naming the path and the missing keys; the member is excluded from `resolve()` and
surfaces in `next_action.unavailable_members` (§6.3) with the standalone command to
regenerate it. (This closes the "garbage attach silently reads as all-`unknown`"
hole.) When a `full_picture` run lacks an
attach for one of these, the member record is `reason_category: "bad_input"`,
`reason: "attach-only member; supply --attach <id>=<path> (see next_action)"`, and
the next-action block emits the exact standalone command to produce it.

### 4.4 Increment 2: resolver wiring

The runner **imports the resolver module** (same directory, same `sys.path` idiom as
every sibling) and calls `resolve()` programmatically
(`surface_disagreement_resolver.py:515-574`) — the resolver's CLI, pattern table, and
reading functions are consumed **exactly as they exist; zero code changes to
`surface_disagreement_resolver.py`**. The one mapping the runner owns (anchor #6):

- For each collected envelope with `available: true`: pass `envelope["results"]` as
  the corresponding `resolve()` kwarg. For attached files: if the parsed dict has
  `schema_version` + `results`, unwrap the same way; else pass as-is (legacy raw
  report — the resolver's readers already tolerate both shapes, e.g. the legacy
  fallbacks at `surface_disagreement_resolver.py:191-193, 232-238`).
- Kwarg map: `variance_audit→variance`, `voice_distance→voice_distance`,
  `general_imposters→gi`, `paragraph_audit→paragraph`,
  `discourse_move_signature→discourse`, `agency_abstraction_audit→agency`,
  `aic_pattern_audit→aic`, `idiolect_detector→idiolect`; `target_text` = the
  `--target` file's text (enables the idiolect-survival reading,
  `surface_disagreement_resolver.py:289-316`).
- `--ai-status` is set on the report dict post-`resolve()` exactly as the resolver's
  own `main()` does (`surface_disagreement_resolver.py:831-833`).
- The combined envelope's `claim_license` **reuses the resolver's**
  `_claim_license(report)` (`surface_disagreement_resolver.py:580-628`) so the
  licenses/does-not-license language ("A verdict on which interpretation is
  correct… the framework declines to pick one") is pinned to one source of truth.
  **Contract — ordering matters:** `_claim_license` reads `report.get("ai_status")`
  for the B.3 `with_state_caveats` routing (`surface_disagreement_resolver.py:626-628`,
  no-op when absent), so the runner MUST populate `report["ai_status"]` (from
  `--ai-status`, previous bullet) **before** calling `_claim_license(report)` —
  calling it on the raw `resolve()` output silently drops the state-routed caveats.
  §9 test 5a pins this.

**Sanity tripwire (mechanical, not a threshold):** if a member envelope was
`available: true` but its resolver reading came back `unknown`, emit a warning
naming the surface and the reading (a shape drift between that surface's `results`
and the resolver's reader — today's silent failure mode). The warning MUST appear
**inside the combined envelope** — as an entry in the `next_action` block's
`unavailable_members[]` (with `reason_category: "shape_drift"`-style reason text) —
not only on stderr; stderr gets a mirror line, but the envelope is the record of
truth (a `--json` consumer never sees stderr). Warning only; never blocks.

### 4.5 Resume / recoverability

Per AGENTS.md §Long-running (belt/suspenders/buttons): the per-member
`envelopes/<id>.json` files are the checkpoint (belt); per-member stderr progress
lines (suspenders); `--resume` skips any member whose envelope file already exists
and parses as schema-1.0 (buttons). A run folder that already contains files is
refused without `--resume` (prevents silent cross-run contamination).

### 4.6 Increment 3: fragment completion (CI requires it)

1. **Complete `capabilities.d/surface_disagreement_resolver.yaml`**: `status:
   heuristic` (its own claim-license says the pattern catalog is "heuristic and
   curated, not labeled-corpus-validated",
   `surface_disagreement_resolver.py:610-614`), `family: cross-surface-interpretation`,
   real `purpose`/`use_when`/`do_not_use_when` (source them from the module docstring
   lines 2-49 and the claim license), `inputs` = the eight `--*-json` flags +
   `--target-text` + `--ai-status`, `outputs.artifacts` = stdout markdown / `--json`
   envelope / `--out`, `compute: {tier: core, cost_note: "stdlib; reads pre-computed
   audit JSONs only", length_floor_words: null}`, `registers` = the union of its input
   surfaces' registers, `handoff: none`, `consumers: []`, `examples` = one real
   two-input invocation, `references` = this spec + the restoration references.
   Satisfies drift-lint checks 3/4 (`tools/check_capabilities_drift.py:308-336`).
2. **Rebless `scripts/tests/_golden_capabilities/surface_disagreement_resolver.json`**
   with the rebless recipe documented above `_load_golden_by_id()` / `_golden_meta()`
   in `test_capabilities_dropin.py` (drop-in golden; no count literal exists to bump —
   post-#170).
3. **Add `capabilities.d/setec_run_set.yaml`** + its own
   `_golden_capabilities/setec_run_set.json`: `surface: validation`, `status:
   heuristic`, `handoff: none`, `consumers: []`, **no `json_delivery`, no
   `min_setec_version`** (deliberately not a consumer surface, §7), `family:
   cross-surface-interpretation`, `compute: {tier: core, cost_note: "orchestration
   only; member cost = sum of members (spaCy-tier for the default presets)"}`.
4. **`changelog.d/run-set-decision-loop.md`** fragment (`### Added`, class `feat` →
   MINOR at release; references both capability ids). Never touch `CHANGELOG.md` or
   `plugin.json` in the PR.
5. Regenerate the calibration-readiness matrix (`tools/gen_calibration_readiness.py`)
   and run the full gate trio from AGENTS.md §Keeping docs current before push.
6. **No new task-surface fragment**: both entries use the existing `validation`
   surface (`claim_license_surfaces/validation.txt` verified present), so
   `VALID_TASK_SURFACES` already admits it (`output_schema.py:69,282-285`).

### 4.7 Increment 4: skill wiring

- **`skills/setec/SKILL.md`** (the router): add the multi-surface route — in "Step 5:
  Hand off" and "Common situations + canonical routes," when ≥2 audits are
  recommended for one target, present the `setec_run_set.py` one-liner as the default
  "full picture" option (with the same "this skill recommends; the user runs"
  posture, lines 43-51). Name both presets and the attach-only rule. Note explicitly
  that the runner's `--situation` output is informational only — the `/setec` skill's
  recommendation remains authoritative when the two differ.
- **`skills/smoothing-diagnosis/SKILL.md`**: add to "Quick CLI" (line 38) — a
  `full_picture` run-set invocation as the "cross-surface read" follow-up after a
  single variance audit, and a sentence in "Interpreting the output" pointing at the
  disagreement report ("multiple matches are expected; the framework refuses to rank
  them").
- Update the plugin-level `SKILL.md`/`README.md` capability tables only if the
  docs-freshness gate demands it (it keys on changelog id coverage; the fragment in
  4.6.4 satisfies it).

## 5. Run-folder layout

```
<out-dir>/                                # default: ./setec-run-sets/<UTCstamp>-<set>/
  run_meta.json                           # set name, member records (id, attached?,
                                          #   argv, exit, reason_category, sha256 of
                                          #   envelope file, compute tier), setec_version
                                          #   (capabilities.setec_version()), argv, timestamps
  envelopes/<surface_id>.json             # verbatim member envelope (success or R3
                                          #   error; attached copies included)
  report.json                             # the combined schema-1.0 envelope (§6)
  report.md                               # rendered report (readings table verbatim
                                          #   from the resolver's renderer + next-action)
```

Nothing here is a private artifact (voice-clone surfaces are excluded by the §4.1
membership guard), so the folder has no `ai-prose-baselines-private/` obligations.

## 6. Output schema (the combined envelope)

Built with `output_schema.build_output()` (`output_schema.py:220`) — so
`schema_version: "1.0"`, the 12 fixed keys, and the R4 bounds walk all apply
automatically. Fields:

- `task_surface: "validation"`, `tool: "setec_run_set"`, `version: SCRIPT_VERSION`,
  `target` = the `--target` path/wordcount, `baseline` = `--baseline-dir` summary or
  null, `ai_status` pass-through, `claim_license` = the resolver-derived license (§4.4).
- **Routing note (verified):** `task_surface` does NOT distinguish the runner from
  the resolver — both use the shared `validation` surface, whose claim-license
  fragment is the one-line label `"validation / labeled-corpus harness"`
  (`scripts/claim_license_surfaces/validation.txt`, read this session). Consumers
  key on **`tool`**: `"setec_run_set"` for the combined envelope vs
  `"surface_disagreement_resolver"` for the standalone resolver's own output
  (`TOOL_NAME`, `surface_disagreement_resolver.py:71`, stamped at :557/:650). §9
  test 6 pins `tool == "setec_run_set"`.

### 6.1 `results.run_set`

`{name, requested_members[], member_records[]}` — each record: `{surface_id,
executed|attached|skipped, available, reason_category?, reason?, compute_tier,
envelope_path, envelope_sha256}`. Counts only; no aggregate numeric of any kind.

### 6.2 `results.envelopes` + `results.disagreement`

- `results.envelopes`: `{<surface_id>: <verbatim member envelope>}` — the per-surface
  envelopes **passed through unreduced** (uncalibrated bands, `calibration_status`,
  `claim_license` blocks, warnings — all intact, byte-for-byte the same JSON as the
  run-folder file).
- `results.disagreement`: the `resolve()` report verbatim — `readings`,
  `n_known_readings`, `matched_interpretations` (name + interpretation +
  supporting_signals), `n_matches`, `inputs_used`
  (`surface_disagreement_resolver.py:555-574`). No re-ranking, no filtering, no
  added fields inside it.

### 6.3 `results.next_action` (all-mechanical; commands, not judgments)

- `unknown_readings[]`: `{reading, populating_surface, command}` from a static
  READING→SURFACE table (the inverse of §4.4's kwarg map) — e.g. `gi_decision` →
  `general_imposters` → the exact `setec run general_imposters … --json` +
  `--attach` invocation.
- `unavailable_members[]`: `{surface_id, reason_category, unlock}` — for
  `missing_dependency`, `unlock` is the install hint derived from the fragment's
  `dependencies.python` (mechanical); for `bad_input` (missing `--baseline-dir`,
  missing attach), the flag to add.
- `restoration_handoff`: emitted **whenever ≥1 of variance/voice/idiolect/aic
  envelopes is available** (a set-membership condition, not a threshold): the
  prefilled `restoration_packet.py` command using the run-folder paths
  (`--variance-json <out-dir>/envelopes/variance_audit.json …`, anchor #10), plus the
  matching `before_after_restoration.py` template with the `--before-*-json` flags
  prefilled and `--after-*` left as `<rerun>` placeholders.
- `rerun`: the exact `setec_run_set.py` command line to reproduce/extend this run
  (with `--resume` and the suggested `--attach` slots).

No priority ordering, no recommendation strength, no "should": every entry is a
condition-triggered command string whose trigger is set membership or a
`reason_category`, never a value comparison. **No new thresholds anywhere in M1.**

### 6.4 The mechanical anti-Goodhart gate (lands in the SAME PR)

Mirroring `within_doc_segmentation.assert_no_authorship`
(`within_doc_segmentation.py:111-158`), `setec_run_set.py` ships module-level:

```python
FORBIDDEN_AGGREGATE_KEYS: frozenset[str] = frozenset({
    "is_ai", "is_human", "verdict", "label", "score", "composite",
    "composite_score", "overall_score", "p_ai", "probability_ai",
    "confidence", "rating", "grade",
})
FORBIDDEN_AGGREGATE_SUBSTRINGS: tuple[str, ...] = ("verdict", "composite")

class AggregateVerdictError(RuntimeError): ...

def assert_no_aggregate_verdict(runner_authored: Any) -> None: ...
```

- **Recursive banned-key walk** (exact key match, case-folded, any depth; plus
  key-only substring match for the two substrings) over every **runner-authored**
  subtree — `results.run_set`, `results.disagreement`, `results.next_action`, and the
  top-level `results` key set — executed at emit time on the real output dict, every
  run (not only in tests). The **pass-through envelopes are exempt from the key walk**
  (their own surfaces already run their own guards; e.g. `voice_distance` legitimately
  contains `weighted_delta`) but are covered by the next check instead.
- **Pass-through shape check**: for every member, `results.envelopes[<id>]` must be
  JSON-equal to the parsed `envelopes/<id>.json` file (and `run_meta.json` carries the
  file's sha256). The runner is structurally incapable of quietly "adjusting" a member
  envelope — reduction or mutation fails the run.
- **No-reduction invariant (RUNTIME — same walk, numeric-leaf check)**: the
  banned-key walk is extended with a numeric-leaf bounds check, executed at emit time
  on the same runner-authored subtrees: **any float leaf** trips the guard, as does
  any int leaf other than whitelisted counts (`n_*` keys) — a composite score cannot
  exist even under an unbanned name. Runtime, not test-only, per the
  `within_doc_segmentation.assert_no_authorship` precedent (firewall checks run on
  every real emit, `within_doc_segmentation.py:111-158`); §9 test 8 pins the behavior
  but the enforcement lives in `assert_no_aggregate_verdict` itself.
- A tripped guard → `build_error_output(reason_category="internal_error")`, exit 1
  (a runner bug by definition, not an operator-branchable condition), report **not**
  written. Fail-closed.

Cite in code comments: `within_doc_segmentation.py` (runtime precedent),
`test_distinct_diversity_audit.py` (test precedent), `corpus_novelty_audit.py:7`
(posture prose). Not `voice_verifier.py` — anchor #0.1.

## 7. Contract / consumer implications — decision

**Decision: `setec_run_set` is NOT a consumer surface in M1.** It ships `handoff:
none`, `consumers: []`, **no `json_delivery`, no `min_setec_version`**; therefore it
is invisible to `setec_run.py --list` (`consumer_entries` gates on `json_delivery`,
`setec_run.py:99-113`), gets **no** golden envelope in `references/contract_fixtures/`
(that directory is exactly the 14 consumer surfaces + `fake_setec.py`, per its
README), no `fake_setec.py` entry, and **zero drift-gate impact on apodictic /
setec-voicewright** — their weekly syncs see only two new manifest fragments, which
are additive and outside their vendored contract.

Rationale: the consumers pin the R1–R5 single-surface contract; neither has asked for
orchestration; a consumer surface here would ripple two re-pin trains for a tool whose
output shape will move during its first sessions of real use. **Promotion path (M2,
explicitly out of scope):** add `json_delivery: stdout` + `min_setec_version` +
`consumers:` to the fragment, a golden envelope in `references/contract_fixtures/`, a
`fake_setec.py` fixture, and the `setec run run_set …` spelling — triggered when a
consumer (most plausibly apodictic's diagnostic loop) requests it, on that repo's next
re-pin train.

What IS required in M1 (and is *not* consumer-contract work): the two drop-in
`_golden_capabilities/` fragments in §4.6 — those pin the *manifest*, not the
envelope contract, and every capability gets one regardless of consumer status
(anchor #0.2/#0.8).

## 8. Posture (the centerpiece — restated as build constraints)

- **No composite score. No verdict field. Ever.** Aggregation output = disagreement
  patterns + next-action commands. Enforced mechanically (§6.4), in the same PR.
- No-verdict surfaces stay no-verdict: member envelopes and the resolver report pass
  through byte-identical; the combined claim license is the resolver's own
  refuses-verdict license (§4.4).
- Uncalibrated bands pass through untouched — the runner never reads, filters, or
  re-labels a `band` or `calibration_status`; the only band consumer is the resolver's
  existing categorical readers, unchanged.
- Operator-supplied comparators unchanged: `--baseline-dir` is forwarded verbatim;
  attached envelopes are honored verbatim; the runner supplies **no default
  comparator, no built-in baseline, no register guess**.
- No new thresholds: every branch in the runner is set-membership, dep-availability,
  or `reason_category` — never a numeric comparison. (The resolver's internal
  read-mappings are pre-existing and untouched.)

## 9. Test contract (`plugins/setec-voiceprint/scripts/tests/test_setec_run_set.py`)

Fixture strategy mirrors `test_setec_run.py` (injectable manifest + fake member
scripts writing canned envelopes) and reuses resolver-style fixture dicts. Invariants:

1. **Preset integrity**: every id in every `RUN_SETS` value resolves in the live
   `capabilities.d/` manifest; `ATTACH_ONLY ⊆ full_picture`.
2. **Argv projection**: exact argv per member (table of §4.1), including
   `--baseline-dir` presence/absence and the voice_distance skip-with-`bad_input`
   path (never an argparse crash).
3. **R3 pass-through**: a member with a missing required dep yields a synthesized
   envelope with `available: false`, `reason_category: "missing_dependency"`, run
   continues, exit 0, and the member appears in `next_action.unavailable_members`
   with the dep-derived unlock hint.
4. **Attach unwrap + validation**: an attached full envelope is unwrapped to
   `results` for `resolve()`; an attached legacy raw report (required keys present)
   is passed as-is; both land verbatim in `envelopes/` and `results.envelopes`.
   **Malformed-attach cases (§4.3 mechanical validation):** (a) non-JSON file, (b)
   JSON object with neither `schema_version`+`results` nor the surface's required
   keys (e.g. a GI attach lacking `decision`, an idiolect attach lacking
   `preservation_list`), (c) JSON non-object — each yields a `bad_input` member
   record naming the missing keys, is excluded from `resolve()`, and appears in
   `next_action.unavailable_members` with the regeneration command; the run still
   exits 0 when other members succeeded.
5. **Resolver wiring**: canned variance (`results.compression.band = "Heavily
   smoothed"`) + voice_distance (`results.overall.band = "Close to baseline (...)"`)
   envelopes → `readings.smoothing == "high"`, `readings.voice_drift == "low"`,
   `edited_authorial_voice` ∈ matched interpretations — proving the unwrap fixed the
   all-unknown failure mode; plus the §4.4 tripwire warning fires on an
   available-but-unreadable fixture **and lands inside the combined envelope's
   `next_action.unavailable_members` block (§6.3), not only on stderr**.

   5a. **Claim-license state routing**: a run with `--ai-status ai_edited` produces a
   combined envelope whose `claim_license` contains the B.3 `with_state_caveats`
   caveats (and a run without `--ai-status` does not) — proving `report["ai_status"]`
   was populated before the `_claim_license(report)` call (§4.4 ordering contract).
6. **Envelope shape + routing**: combined output has `REQUIRED_TOP_LEVEL_KEYS`
   (`test_setec_run.py:44-49`), `schema_version == "1.0"`, **`tool ==
   "setec_run_set"`** (≠ the resolver's `"surface_disagreement_resolver"` — the
   consumer routing key, §6), claim license present and containing the resolver's
   refuses-verdict language.
7. **Anti-Goodhart guard**: `assert_no_aggregate_verdict` raises on an injected
   `verdict`/`score`/`composite_ranking` key at depth in a runner-authored subtree;
   the happy-path combined output passes; the guard demonstrably does NOT walk
   pass-through envelopes (a member envelope containing `weighted_delta` passes);
   tripped guard → `internal_error` envelope + exit 1 + no `report.json`.
8. **No-reduction (runtime)**: an injected float leaf (or non-`n_*` int leaf) in a
   runner-authored subtree trips `assert_no_aggregate_verdict` at emit time →
   `internal_error` envelope, exit 1, no `report.json`; the happy-path combined
   output (which contains no runner-authored numerics beyond `n_*` counts) passes.
9. **Pass-through identity**: `results.envelopes[<id>]` JSON-equal to
   `envelopes/<id>.json`; sha256 in `run_meta.json` matches.
10. **Resume**: second run with `--resume` re-executes nothing (fake scripts count
    invocations); non-empty out-dir without `--resume` → `bad_input`, exit 3.
11. **Exit codes**: unknown set → 2; no target → 3; all-members-failed → the modal
    member `reason_category` with mapped exit; ≥1 envelope collected → 0.
12. **`--list-sets` / `--situation`**: enumerate without executing; `--situation`
    prints recommend output and executes nothing.
13. **Fragment round-trip**: covered automatically by `test_capabilities_dropin.py`
    once the two goldens land; drift/docs-freshness gates run in CI.

## 10. Failure modes → guards

| Failure mode | Guard |
|---|---|
| The aggregate quietly becomes a verdict (the architected-against violation, laundered through a convenience wrapper) | §6.4 runtime walk (banned keys **+ runtime numeric-leaf no-reduction check**) + pass-through identity check, **same PR as the runner** — not a follow-up |
| One member's crash aborts the whole session | per-member R3 wrap + continue (§4.1); checkpointed envelope files + `--resume` (§4.5) |
| Runner silently feeds envelopes where the resolver expects raw reports → all-`unknown` readings, empty-looking report | §4.4 unwrap + test 5 + the available-but-unknown tripwire warning |
| Private voice-clone artifacts pulled into a run folder | membership guard refuses `json_delivery: file` surfaces (§4.1) |
| Preset drifts from renamed/removed surfaces | runtime manifest resolution + test 1 pins ids in CI |
| argparse prefix-match / flag variance re-enters via the runner | fixed projection table + exact-argv tests (the PR-#6 lesson, `setec_run.py:12-15`) |
| Attached file is stale/mismatched register | out of mechanical scope; the resolver's claim license already caveats thin/mismatched inputs — passed through, and `run_meta.json` records the attach path + sha for audit |
| Fragment completion breaks CI gates | drift-lint checks verified against `check_capabilities_drift.py:308-336` (§4.6); gate trio run pre-push |

## 11. Estimated build sessions & PR shape

- **Session 1:** `setec_run_set.py` (execution loop, projection table, attach, resume,
  membership guard) + the anti-Goodhart guard + core tests (1–4, 10–12).
- **Session 2:** resolver wiring + combined envelope + next-action block + tests
  (5–9); both capability fragments + goldens + changelog fragment + gate trio.
- **Session 3 (buffer):** skill wiring (§4.7), report.md rendering polish, review-fix
  headroom before the Codex window.

**One PR** (single capability + its paper trail; no sub-PR split warranted): branch
per repo convention (non-roadmap, non-trivial → open a Task-brief Issue per AGENTS.md
§Where work comes from; branch `chore/run-set-decision-loop` or slot an `r<N>` if the
maintainer adds it to the paired-release schedule). Ships: 1 new script, 1 new test
file, 1 completed + 1 new `capabilities.d/` fragment, 2 `_golden_capabilities/`
fragments (1 reblessed, 1 new), 1 `changelog.d/` fragment (`feat` → MINOR), 2 skill
edits, regenerated readiness matrix. **No edits to `setec_run.py`,
`surface_disagreement_resolver.py`, `output_schema.py`, `plugin.json`,
`CHANGELOG.md`, or `references/contract_fixtures/`.** Codex-gated (authored logic).

## 12. Out of scope / non-goals

- Consumer promotion of `run_set` (json_delivery / min_setec_version / contract
  fixture / fake_setec / `setec run run-set` spelling) — M2, trigger in §7.
- Execution-from-`--situation` (recommend-driven membership) — M2; M1 is report-only.
- Any change to the resolver's pattern table, readers, or CLI; any new disagreement
  patterns; any calibration of the pattern catalog.
- Parallel member execution (members are seconds-to-minutes; sequential + checkpoint
  is simpler and satisfies belt/suspenders/buttons).
- Auto-invoking `restoration_packet.py` / `before_after_restoration.py` (the
  next-action block emits commands; the operator runs them — same posture as the
  router skill, `skills/setec/SKILL.md:43-51`).
- Preset registry as a drop-in dir (`run_sets.d/`) — revisit only if presets become a
  multi-PR collision point; M1's in-script constant is a new file with no collision
  surface.

## 13. Open questions (operator calls)

None remaining. Attach-only preset membership is decided in-spec, not open:

- **Operator call (default folded 2026-07-01): `full_picture` lists the attach-only
  pair (`general_imposters`, `idiolect_detector`) — visible-but-absent, generating
  next-action prompts, per §4.2/§4.3; override before build if disagreed.**
- **Preset governance:** the maintainer curates `RUN_SETS` (in-script constant, §4.2);
  presets are opt-in by name (never auto-populated from the manifest or from
  `recommend()`); deprecating a preset = one release with a `bad_input` message naming
  the replacement, then removal.

(Everything else — CLI shape, consumer posture, resolver-extension question, guard
severity, run-folder layout — is decided above.)
