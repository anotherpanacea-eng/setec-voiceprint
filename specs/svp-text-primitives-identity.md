# SVP Text-Primitives Identity — one tokenizer, versioned measurement primitives

**Status:** DRAFT v2 (post-swarm-review) · **Date:** 2026-08-05 · **Repos:** `setec-voiceprint` (primary), `setec-voicewright` (naming/documentation half)
**Provenance:** three-agent modularization audit, Cowork session 2026-08-05 (voiceprint audit §2.4; cross-repo analysis §2a–2c, §4b–4e)
**Review round 1:** NEEDS-REWORK (adversarial swarm review, 2026-08-05) — 7 P1 / 8 P2 findings, all addressed below. Pre-review draft preserved at `svp-text-primitives-identity.pre-swarm-review-20260805.md`.
**Depends on:** `svp-packaging-conversion` P1–P3 (package home) — **and, as a hard prerequisite, not a see-also, `setec-consumer-client-contract` C1 and C3** (T2 cannot ship until voicewright's exact-set envelope check is presence-based; §2, Risks). Independent of P4.
**Cross-spec note:** packaging is being reworked to place the package at `plugins/setec-voiceprint/scripts/setec/` (no `src/` layout); every path below assumes that landing point.

## Problem

The framework's most basic measurement primitives are defined many times, differently, and the differences reach reported numbers:

- **`count_words` is defined 23 times, backed by 9+ distinct word regexes** (`[A-Za-z']+` ×14, `\b\w[\w'-]*\b` ×7, `\b\w+\b` ×6, `\w+` unicode, `\S+`, four one-offs). `target_words` — in **every** schema-1.0 envelope, gating every length floor — means different things per surface.
- **`_quantile`-family functions are defined 9 times, not 8** (verified sites: `homogeneity_audit.py:173`, `distinct_diversity_audit.py:189`, `voice_fingerprint.py:626`, `within_doc_segmentation.py:433`, `voice_validation_harness.py:272`, `validation_harness.py:434`, `calibrate_thresholds.py:99`, a nested `corpus_novelty_audit.py:60` closure, and `paragraph_audit.py:211`'s `_quantiles` — a different signature, `Sequence[float] -> dict[str, float]`), three edge-case behaviors (`0.0`/`None`/clamp-to-last), two docstrings *admitting* clean-room copies.
- **17 `_content_fingerprint` definitions**, each a different normalization contract, no registry of what means what.
- **Cross-repo drift, corrected counts:** voiceprint `FUNCTION_WORDS` has **135** entries (`variance_audit.py:117` — not 137), voicewright's has **53** (`voicediff.py (line 32)` — not 55) — not comparable even before counting **voiceprint's third, independent 89-entry set** (`dialogue_voice_audit.py:128`, docstring: "mirrors variance_audit.FUNCTION_WORDS but kept local"). Voiceprint's splitter prefers NLTK (`variance_audit.py:155-163`, falls back to regex `_SENT_RE` on any exception) and handles abbreviations only on that path; voicewright's `_SENT = r"[.!?]+"` breaks on `Dr.` and recurs at **7 sites**, not 4 (`voicediff.py (line 41)`, `voice_sheet.py (line 85)`, `rag.py (line 244)`, `qlora/distmatch.py (line 281)`, `qlora/spin.py (line 83)`, `qlora/reward.py (line 49)`, plus `contamination.py (line 114)` — a *different* pattern used to locate a split offset, not enumerate sentences, and excluded from any collapse). **"Burstiness" names two formulas** — Goh–Barabási in voiceprint vs shifted CV in voicewright's flatness.py — both can appear in one voicewright run, since it also consumes `variance_audit`.
- Voiceprint has already built the right endgame artifact once: **`passage_tokenizer_v1.py`** — frozen, table-driven, hash-bound, no host-runtime Unicode classification. Verified: ships from `release/v1.128.0`/`v1.128.1` (commit `585c05f`, spec-80) and **is absent from local HEAD** (`agent/author-corpus-export-pr` @ v1.126.1 — staleness `svp-packaging-conversion` P0 and `setec-consumer-client-contract` finding 7 already flag). This spec makes its *pattern*, not its universal application, the registry's frozen-tier member.

## Non-negotiable constraint: calibrated numbers must not silently move

Thresholds in `calibration/` and recorded baseline artifacts were computed under each surface's *current* tokenization. Blindly unifying regexes silently shifts MATTR/MTLD/FKGL/sentence-length stats against frozen baselines — corrupting the calibration receipts the framework exists to protect. **Observability first, unification second, recalibration where required.**

## Firewall risk (why round 1 failed)

An identity stamp that isn't provably true is worse than no stamp: it is fabricated evidence laundered through machine-readable metadata, and every downstream consumer will trust it without re-deriving it. Round 1's design let a stamp be **declared, not derived** — nothing bound the string a caller wrote into a `textprims` block to the tokenizer that actually ran, and every named enforcement mechanism (goldens, gate 3's tool, the AST lint, golden regeneration) was structurally incapable of catching a false stamp. §2–§3 rebuild the design so the stamp is mechanically true or explicitly provisional; nothing here claims comparability it can't back.

## Design

### 1. `setec.core.textprims` — versioned primitive registry, rebuilt from an inventory, not asserted

Round 1 named 4 tokenizer IDs against a live pattern count the swarm review's grep-level check put at 11 (case-folding alone splits the count: `stylometry_core.word_tokens` lowercases, several `count_words` sites don't). A hand-authored list undercounts by construction and gate 1 is arithmetically unsatisfiable against it. Fix: **T1 ships a committed, re-runnable inventory script** (tools/gen_textprims_inventory.py, shaped like `gen_calibration_readiness.py` but walking source, not the manifest) that AST-scans `setec.surfaces` for every `re.compile`/inline `re.findall`/`re.split` word-shaped pattern and every function-word/sentence/paragraph/fingerprint definition, emitting the registry skeleton plus a diff against the last run. The registry is authored *from* that output:

```python
TOKENIZERS = {
  "letters-apostrophe-v1":       ...,  # [A-Za-z']+, no case-fold (variance_audit._WORD_RE)
  "letters-apostrophe-lower-v1": ...,  # [A-Za-z']+ + .lower()    (stylometry_core.WORD_RE — DIFFERENT id)
  "unicode-w-lower-v1":          ...,  # \w+ lower (shingle_dedup lineage; spec-71 owns, §Ownership)
  "word-boundary-v1":            ...,  # \b\w[\w'-]*\b (second-largest cohort)
  "passage-v1":                  ...,  # frozen table (passage_tokenizer_v1; spec-80; NOT ON LOCAL MAIN)
  # ...remaining IDs from the T1 inventory run, 1:1 with its pattern list — not hand-picked.
}
```

Identity is a **contract**, not a bare regex: `(pattern, case_policy, unicode_normalization) -> tokenize(text) -> list[str]`. Host-dependent entries stamp the *resolved* backend, not the family: `sentences-nltk-v1` is retired as a single ID (verified: `variance_audit.split_sentences` tries `nltk.sent_tokenize`, falls back to regex `_SENT_RE` on **any** exception — a fresh environment without `punkt` silently produces a different tokenizer under the same name) and replaced by `sentences-nltk-punkt-v1` / `sentences-regex-fallback-v1`, chosen and stamped at call time by which branch executed, plus the punkt/NLTK version when it runs (same discipline `shingle_dedup.py`'s `_logical_seal` already applies — verified `TOKENIZER_ID`/`unicode_version` at `shingle_dedup.py:336-337`). Multi-tokenizer calls stamp a list, not a scalar — value type `str | list[str]` from day one, not a T4-time schema break.

Same registry shape for `SENTENCE_SPLITTERS`, `PARAGRAPH_SPLITTERS` (adopt `near_dup_dedup.split_passages`, the only one returning char offsets), `FUNCTION_WORDS` (§Ownership), quantile (§4), and a documented `FINGERPRINTS` registry naming all 17 content-fingerprint contracts.

### 2. Identity stamping — non-forgeable, additive, correctly placed

**The stamp must be derived, not typed.** `textprims.tokenizer(id)` returns a handle (compiled pattern/table + metadata), not a bare string; `build_output`/`build_baseline_metadata` accept only handles via a new `textprims=` parameter and derive the envelope block from the handle's own `.stamp()` — a bare string is a `TypeError`, not silently trusted. This closes the gap where `build_baseline_metadata` was a plain trust-the-caller dict builder (verified: `output_schema.py:422-462` takes `n_files`, `words`, `register`, `split`, and an open `extra` dict, no validation of what tokenizer ran). A per-family **mutation test** ships in the same PR: swap the handle a surface uses, assert both the stamp changes and the T3 characterization test (§3) for that surface fails.

**Placement:** `beat_matched.validate_s5_envelope` performs exact root-key set equality (`set(envelope) != root_keys`, verified at `beat_matched.py (line 1637–1642)`) against a fixed 11-key set excluding `textprims`. A root-level block is a hard refusal on the frozen N10/N11 evaluation surface the day T2 ships — exactly the `s5_distance` surface C1–C3 exists to fix. Until `beat_matched` moves to presence-based validation (C3), `textprims` nests under `target.textprims`/`baseline.textprims` (paths the exact-set check doesn't enumerate), not at root. **T2 gate: a stamped envelope must pass every vendored consumer contract test — including `beat_matched`'s S5 fixture — before any golden regenerates.** T2 does not start until C3 lands or the nested workaround is verified against current `beat_matched.py`.

Otherwise unchanged from round 1: additive-only (schema stays 1.0), every surface stamps what it used. Comparability is now checkable by inspecting matching handles, not asserted as already true.

### 3. Cohort migration (T3) — a real oracle, not a shape fixture

Round 1's "byte-identical envelope goldens" don't exist as a behavior oracle. Verified: `gen_contract_fixtures.py`'s docstring states **"No heavy audit is run (no spaCy / torch / scipy / sentence-transformers)"** (line 31) — every numeric field, including `n_words: 2480` (line 163), is a **hand-typed literal**, never a tokenizer's output. The generator covers **17 surfaces** against **112 files / ~142 call sites** calling `build_output` and **23** `count_words` definitions — of those 23, only **4** sites live inside a golden-covered surface (`narrative_decision_audit`, `binoculars_audit`, `argument_decision_audit`, `agd_move_scan`, verified by grep). A tokenizer swap elsewhere goes green by construction — the goldens never call a tokenizer.

**Replacement oracle, both parts required before any T3 migration PR:**

1. **Per-site characterization table**, committed before migration starts: one row per `count_words` site (and the splitter/quantile inventory from T1) = `{module, callable, committed fixture input, exact expected output, current regex/ID}` — a fixture *of the tokenizer*, independent of envelope shape.
2. **Token-level differential harness**: old inline regex vs new `textprims.tokenizer(id)` handle over a fixed adversarial corpus (digits, hyphens, curly vs straight apostrophes, non-ASCII letters, `Dr.`/`e.g.`, empty string), asserting identical output per migrated site.

`gen_contract_fixtures.py`'s goldens are **demoted to shape-oracle status**, documented as such in its own docstring and here: they catch envelope-key drift, not tokenizer-value drift.

### 4. Convergence (T4) — full quantile contract, gated per surface

Only after stamping and the T3 oracle exist: surfaces migrate to a canonical tokenizer per family, one surface per PR, each PR either (a) shows no calibrated thresholds/frozen baselines downstream, or (b) ships recalibrated thresholds + regenerated baseline metadata, with numeric before/after `signal_path` deltas recorded in the PR body and `changelog.d`. Priority: the 6 `\b\w+\b` and 7 `\b\w[\w'-]*\b` sites converging onto `letters-apostrophe-v1`/`-lower-v1`/`passage-v1`; the four one-offs get keep-or-kill rulings.

**Quantile contract, specified in full (round 1 named one axis of a four-axis problem and undercounted the sites — corrected to 9, §Problem):** `quantile(xs, q, *, empty: Literal["none","zero","raise"])`, always sorts its input internally, implements exactly one pinned interpolation formula (linear, matching `numpy.percentile`'s default `"linear"` method, written out in the module docstring), returns `float | None` per the `empty` policy — never a silently different type per branch. Sites whose formula doesn't match the pinned one reclassify from T3 to **T4, behavior-changing**, same recalibration-receipt requirement. `paragraph_audit._quantiles` (verified: `Sequence[float] -> dict[str,float]`, several quantiles from one sorted pass) doesn't fit the single-value signature and registers as a distinct `quantile-linear-multi-v1` wrapper over the same formula.

### 5. Voicewright half (VW1) — split by behavior change, correct blast radius

Round 1's risk table claimed a 5-module blast radius (`anticentroid`, `authenticity`, `content_distance`, `flatness`, `voice_sheet`) for the `flatness._burstiness` rename. Verified: **none of `anticentroid.py`, `authenticity.py`, `content_distance.py`, or `voice_sheet.py` reference `flatness`/`burstiness` at all.** The real reader set: `__init__.py`, `bakeoff.py`, `cli.py` (imports `_cmd_flatness`), `cli_cmds/output_audits.py` (calls `machine_flatness_report`), `tests/test_flatness.py`.

Split by risk:

- **VW1a — rename, no behavior change.** `flatness._burstiness` → `_sentence_length_cv`; field `burstiness_gap` (`flatness.py (line 151)`) → `sentence_length_cv_gap`; `norm_scales` key (`flatness.py (line 326)`) → `norm_scales.sentence_length_cv`. Changelog fragment + capability doc note. Lands with T1/T2 — no number changes.
- **VW1b — splitter upgrade.** The naive `_SENT = r"[.!?]+"` sites: verified **6**, not 4 (`voicediff.py (line 41)`, `voice_sheet.py (line 85)`, `rag.py (line 244)`, `qlora/distmatch.py (line 281)`, `qlora/spin.py (line 83)`, `qlora/reward.py (line 49)`). `contamination.py (line 114)`'s visually similar `r"[.!?]+\s+"` locates a prefix/continuation split offset, not sentences, and is **excluded** from the collapse — folding it in changes M2 split points. The 6 true sites get a shared helper; upgrading is allowed since nothing calibrated sits downstream directly — but flatness.py's `machine_flatness`/band/abstain counts and the bakeoff that consumes them are threshold-gated (`_MIN_SENTENCES`). VW1b ships **before/after `machine_flatness`/band/abstain counts on a fixed probe corpus**, same discipline as a T4 PR, despite landing independently.

`voicediff.FUNCTION_WORDS` (53) gets ID `fw-53-vw-v1` and a docstring on non-comparability with voiceprint's `fw-135-v1`. No forced convergence — honest labeling, per the fleet's deferral architecture.

## Ownership

Named because round 1's registry design would have silently redefined constants owned elsewhere:

1. **`unicode-w-lower-v1` is owned by spec-71** (`specs/71-shingle-dedup-library.md`; verified `TOKENIZER_ID`/`unicode_version` already feed `shingle_dedup._logical_seal`'s hash preimage). `textprims` **re-exports**, doesn't redefine.
2. **`passage-v1` is owned by spec-80** (`passage_tokenizer_v1.py`, verified shipping from `release/v1.128.0`/commit `585c05f`, absent from local HEAD). No surface migrates onto it until the checkout reaches v1.128.0+ (shared precondition with `svp-packaging-conversion` P0 and `setec-consumer-client-contract` D).
3. **`MachineFlatnessReport` field names (incl. `burstiness_gap`) are owned by voicewright spec-39** (`voicewright's spec-39 (imitation-distance-machine-flatness)`). VW1a's PR must touch voicewright spec-39's doc, not just the code.
4. **`stylometry_core.py` is the de facto primitives module round 1 never mentioned** — verified: `WORD_RE = re.compile(r"[A-Za-z']+")`, `word_tokens()` (lowercases), `paragraphs()`, **40 importers**. `textprims` must either absorb its tokenizer and re-export as `letters-apostrophe-lower-v1`, or declare `stylometry_core` canonical owner and import from it (Owner decision 2). Either is acceptable; silence is not — 40 importers is the largest blast radius in this spec if gotten wrong.

## Phases

- **T1** — `textprims` module + registry (from the committed inventory script, §1) + tests (pure addition).
- **Increment 1 (T1 + T2 + VW1a)** — additive only, zero numeric behavior change. Ships together; doesn't need C3 first if the nested placement (§2) is used.
- **T3 — Increment 2.** Cohort migration, behavior-preserving, gated on the characterization table + differential harness (§3) existing *before* the first migration PR. Cohort: the 23 `count_words` sites, splitter sites, 9 quantile sites (pinned-formula subset — non-matching sites defer to T4).
- **T4 — deferred behind Owner decisions 1 and 3.** Default if unruled: **pinned-forever** — calibrated surfaces keep their tokenizer; only uncalibrated surfaces converge. A third dependent class branch (a) cannot wave through: **hash-bound identity domains** — `shingle_dedup` index digests, spec-36 passage manifests — where the tokenizer is baked into a stored SHA-256 preimage, not a threshold, but needs the same recalibration-receipt discipline.
- **VW1b** — naive-splitter upgrade (independent, any time after VW1a; before/after `machine_flatness` requirement in §5).

## Acceptance gates

1. **After T3, decidable, not aspirational:** a committed denylist of exact literal regex patterns (covering `re.compile(...)` and inline `re.findall`/`re.split` — the T1 inventory found inline tokenization an AST-walk for `re.compile` alone would miss) plus function-word **list/set/tuple** literals matching known cardinalities (135, 53, 89 — any new word-shaped literal collection over ~20 entries trips the lint). A counted `# textprims-exempt: <reason>` escape hatch. Scoped to paths existing at T3 time (`plugins/setec-voiceprint/scripts/**`, pre-P4 layout).
2. **After T2:** every envelope carries a `textprims` block (nested per §2 until C3 lands, root-level after); `gen_contract_fixtures` goldens regenerated exactly once, consumers' vendored fixtures re-synced via `sync_setec.py`; **the regenerated envelope passes `beat_matched.validate_s5_envelope` and every other vendored consumer contract test before the golden write is done** — a golden `--write` accepts but a real consumer refuses is not passing.
3. **T4 PRs, rebuilt around real artifacts, not `gen_calibration_readiness.py`.** Verified: that tool derives its matrix entirely from `capabilities.d/*.yaml` fields — it never opens `thresholds_calibrated.json` or any baseline artifact, so it's dropped as the drift instrument. Replacement: diff `thresholds_calibrated.json` per PR — empty diff licenses branch (a); non-empty diff requires re-running the recorded `harness_command` with numeric before/after `signal_path` values on a fixed probe corpus. **Severity-direction rule:** a recalibration that *lowers* a flag rate requires a recorded owner ruling — quieter needs human confirmation.
4. VW1a: no output key/field named `burstiness`/`burstiness_gap` from `flatness`; both readers updated in the same PR. VW1b: before/after `machine_flatness`/band/abstain counts attached; voicewright full suite + `gate_all.py` green.

## Risks

| Risk | Mitigation |
|---|---|
| False/stale stamp trusted as ground truth (round-1 firewall risk) | stamp derived from a `textprims.tokenizer(id)` handle, never a bare string; per-family mutation test proves the oracle catches a swapped handle |
| T2 hard-breaks `beat_matched.validate_s5_envelope` on N10/N11 | C1/C3 promoted to hard `Depends-on`; nested `target/baseline.textprims` as interim workaround, verified against current `beat_matched.py` |
| Silent metric shift vs frozen baselines | hard T3/T4 separation; characterization table + differential harness as the real T3 oracle; recalibration receipts gated on `thresholds_calibrated.json` diffs, not `gen_calibration_readiness.py` |
| Golden churn noise in T2 | single deliberate regeneration PR, gated on passing vendored consumer tests, not just `--write` |
| Registry underdetermines behavior (4 IDs vs 11 patterns) | T1 ships a re-runnable inventory script; registry authored from its output; case-folding and host-dependent backends get distinct IDs |
| `textprims` silently redefines a constant owned elsewhere | Ownership section: spec-71, spec-80, voicewright spec-39, `stylometry_core.py` (40 importers) |
| VW1 rename/collapse touches an unnamed reader or number | verified reader list replaces the unverified 5-module claim; VW1b requires before/after counts; `contamination.py (line 114)` excluded |

## Owner decision points

1. **T4 convergence target:** `letters-apostrophe-v1`/`-lower-v1` (largest calibrated cohort) vs `passage-v1` (frozen contract-grade, not yet on local main — spec-80 dependency). **Default if unruled: pinned-forever** — calibrated surfaces never move; only uncalibrated surfaces converge.
2. **Function-word set ownership:** absorb `stylometry_core.py`'s tokenizer/word-list machinery (40 importers, never named in round 1) into `textprims` with re-export, or declare `stylometry_core` canonical owner? Should `fw-135-v1` absorb voicewright's `fw-53-v1` as a derived subset, or stay independent (and what about `dialogue_voice_audit`'s third 89-entry set)?
3. **Recalibration budget:** T4 is open-ended; is partial convergence (calibrated pinned forever, uncalibrated converge) an acceptable **permanent** end state, or does it need a revisit date? (Default: permanent, until overridden.)
4. **Severity-direction ruling rule (new):** per gate 3, any T4 recalibration lowering a flag rate needs a recorded owner ruling, not just a clean diff. Does that extend to VW1b's before/after `machine_flatness` counts, or is VW1b's independent-landing status sufficient?
