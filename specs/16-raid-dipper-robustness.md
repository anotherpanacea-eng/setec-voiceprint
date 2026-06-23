# RAID + DIPPER robustness fixtures + recursive-paraphrase stress harness

> **Build note (folded review findings).** This spec was reviewed
> (`raid-dipper-robustness-findings.md`, verdict GO-WITH-CHANGES) against
> the real module source. Three contract errors in the original draft are
> corrected in-line below; the corrections are load-bearing and supersede any
> remaining prose that contradicts them:
>
> 1. **`pan_replay` has NO known-class-vocabulary filter.** `replay()` groups
>    pairs by whatever `obfuscation_class` string is present
>    (`by_class.setdefault`) and scores all of them; `DEFAULT_CLASSES` is CLI
>    help text, never a filter. RAID classes already flow through with **zero**
>    `pan_replay` changes. The "widen the known vocabulary" change and the
>    acceptance clause about *warning on unknown classes* are **dropped** —
>    that behavior does not exist and inventing it would contradict the
>    additive/default-preserving framing.
> 2. **This adds ONE capability on the EXISTING `validation` surface.** Register
>    **only** `capabilities.d/paraphrase_ladder.yaml` + its per-id golden
>    fragment `scripts/tests/_golden_capabilities/paraphrase_ladder.json`. Do
>    **not** add a `claim_license_surfaces/*.txt` row or a
>    `_golden_task_surface_labels.json` row — `paraphrase_ladder` sits on the
>    existing `validation` surface (like `pan_replay`, which added no such row),
>    and a row would break the surface-label bijection test. The capabilities
>    golden is now **drop-in** (one `<id>.json` fragment per entry); there is
>    **no `==N` count literal** to bump (it was removed in the #170 refactor).
> 3. **`score_ladder` must extract per-CELL `relative_change` / `label`** (the
>    `pan_replay` cell-extraction pattern) and **MUST NOT** embed
>    `build_robustness_card`'s top-level aggregate dict (`n_robust_signals`,
>    `n_fragile_signals`, `overall_robustness`, …). The extended `_walk`
>    banned-key test forbids those keys at every depth of `results`. "Reuse the
>    card verbatim" means *the per-cell values come from the card unchanged*,
>    not *the card dict is serialized*.
> 4. **Structural-separation ban set trimmed to modules that exist in
>    voiceprint:** `{calibrate_thresholds, conformal_gate}` (the validation
>    harness's calibration path). `comparator_class_calibration` and
>    `SetecFitness` do not exist in voiceprint and are dropped from the guard.
> 5. **ClaimLicense thresholding affordance closed:** the per-rung Δ is a
>    strictly DESCRIPTIVE per-cell `relative_change`; the license states that no
>    per-rung Δ is a retention threshold and that ranking robustness by
>    comparing Δ across signals/rungs is not licensed.

Builds on `specs/04-pan-obfuscation-replay-harness.md` (the **`pan_replay`** harness:
replays SETEC's *existing* Tier 1-3 signals over `(clean, obfuscated)` fixture pairs
grouped by obfuscation class, emits a per-`(signal × class)` robustness card with a
`stable / degraded / collapsed` tag and **no aggregate score**), the
**`adversarial_robustness_card.build_robustness_card`** output shape it reuses (per-`(signal,
fixture)` relative-change + label), and **`adversarial_fixtures.py`** (the deterministic
stdlib tokenizer-layer transforms — `insert_zero_width_spaces` / `apply_homoglyphs` /
`insert_soft_hyphens` — that already back `pan_replay`'s `unicode` class). It sits **on the
existing `validation` task surface**, beside `pan_replay`, and like it is **never** a
detector, never a selection / calibration-threshold target.

Roots: **RAID — A Shared Benchmark for Robust Evaluation of Machine-Generated Text
Detectors** ([arXiv:2405.07940](https://arxiv.org/abs/2405.07940)) — the largest
adversarial-robustness benchmark, models × domains × **11 adversarial attacks** — and
**DIPPER / "Paraphrasing evades detectors of AI-generated text, but retrieval is an
effective defense"** ([arXiv:2303.13408](https://arxiv.org/abs/2303.13408)) — the canonical
11B controllable paraphraser that breaks detectors and watermarks. The recursive-paraphrase
escalation and the *separability ceiling* it grounds come from **Sadasivan et al., "Can
AI-Generated Text Be Reliably Detected?"** ([arXiv:2303.11156](https://arxiv.org/abs/2303.11156))
— detector AUROC is upper-bounded by human/AI distribution overlap, the asymptote SETEC
already cites in `references/distributional-diagnostics.md` (the heavy-paraphrase 0.5-AUROC
ceiling).

- **Status:** M1 (model-free RAID-taxonomy transforms + recursive-paraphrase *scaffolding*)
  is stdlib/CI; the DIPPER-grade neural paraphraser is the M2 GPU seam.

## Goal

`pan_replay` already systematizes "how much does signal S move under obfuscation class C"
against four PAN classes (`unicode / paraphrase / lang_switch / short`). Two honest gaps
remain:

- **The fixture taxonomy is narrow.** PAN's four classes are a slice of the field's attack
  surface; **RAID** enumerates ~11 deterministic adversarial transforms. Most are model-free,
  CI-runnable, and `adversarial_fixtures.py` already ships three of them. Expanding the
  transform set to the RAID attacks, with the same per-class slicing `pan_replay` already
  enforces, is a **stdlib fixture/transform expansion (M1)**. Because `pan_replay` already
  replays every class string present in the manifest, **no `pan_replay` code change is
  required** — the RAID classes flow through unchanged.
- **The paraphrase axis is shallow and single-pass.** DIPPER's finding — and Sadasivan's
  recursive-paraphrase escalation — is that **iterating** the paraphrase monotonically erodes
  stylometric signals toward the human/AI overlap floor. SETEC has no harness that walks a
  *graded ladder* of paraphrase passes and reports the **per-signal decay curve** across
  rungs. The ladder *scaffold* (apply transform N times, re-score at each rung, build the
  decay curve) is **stdlib (M1)**; the **DIPPER-grade neural paraphraser** that makes the
  rungs realistic is a **GPU-gated model seam (M2)**.

This spec (a) **adds deterministic RAID-attack transforms** to `adversarial_fixtures.py`
(additive; the three existing transforms and their `TRANSFORMS` keys are untouched; a
`pan_replay` run over a fixture dir of only the old classes behaves identically), and (b)
adds a **recursive-paraphrase stress harness** — a new `validation`-surface tool,
`paraphrase_ladder.py`, that re-runs SETEC's existing signals at each rung of a paraphrase
ladder and emits a per-signal **decay curve** (reusing `build_robustness_card`'s per-CELL
shape, one fixture column per rung — never embedding the card's aggregate dict). Every
output is a **descriptive per-signal robustness reading with no aggregate score and no
provenance verdict** — and the harness **hardens** the over-claim-separability guardrail by
quoting Sadasivan's ceiling directly in its `ClaimLicense`.

## Honest framing (limits, surfaced not hidden)

- **This is an eval/fixture expansion, not a detector and not a new signal.** Nothing here
  decides whether a text is AI-written. It measures how SETEC's *own existing signals* move
  under transformation. The deliverable is a robustness card / decay curve, not a score, not
  a label, not a verdict.
- **A decay curve is a fixture observation, not a robustness guarantee.** The card is fixture-
  and paraphraser-specific; the `ClaimLicense` says so. A stable reading is **never** evidence
  the signal is generally robust; absence of collapse is not evidence of robustness.
- **The recursive ladder bottoms out at the separability ceiling, by construction.**
  Sadasivan's result is that as the paraphraser approaches the human distribution, every
  stylometric signal converges toward 0.5-AUROC separability. A flat decay curve means "this
  attack did not erode S **at this paraphrase strength**," never "S is robust to paraphrase."
- **The neural paraphraser is M2 and stays gated.** M1's recursive ladder uses a deterministic
  **stdlib paraphrase proxy** composed from `adversarial_fixtures.py` primitives. That proxy is
  honestly weaker than DIPPER; the M1 card labels its paraphraser `proxy_stdlib` so no reader
  mistakes a proxy-ladder flat curve for DIPPER robustness.
- **Fixtures are not redistributed.** RAID and DIPPER corpora have their own redistribution
  terms; this harness does **not** vendor them. The bundled fixtures are tiny synthetic
  stand-ins that exercise the orchestration only.

## The load-bearing design question (and its answer)

**Why is a recursive-paraphrase decay curve not just a robustness *score* fed into
calibration?** Optimizing it is the trap twice over:

1. **Goodhart.** A single "robustness number" (mean Δ across attacks, AUROC retained, area-
   under-the-decay-curve) would immediately become a target. So, exactly as `pan_replay`: **no
   aggregate robustness or accuracy score is emitted, anywhere.** The deliverable is the
   per-`(signal × rung)` decay curve — lists the operator reads, never a scalar. (Guard: the
   `pan_replay` `_walk` banned-key test, extended with the recursive-ladder banned keys —
   `auc_retained`, `robustness_score`, `area_under_decay`, `is_robust`, plus the card's
   `n_robust_signals` / `n_fragile_signals` / `overall_robustness`.)
2. **The separability ceiling is a *theoretical* upper bound, not a tuning target.** Sadasivan
   proves you *cannot* beat the human/AI overlap by hardening signals. The harness **quotes the
   ceiling in the card** and frames every reading against it — a humility instrument, not an
   optimization target.

- **Never a selector, never a calibration-threshold input.** The card/curve is a `validation`-
  surface read-only diagnostic routed to the human. It never enters threshold calibration,
  never becomes a reward, never re-labels a fixture. (Guard: the new modules import nothing from
  `{calibrate_thresholds, conformal_gate}` and name no threshold-setting symbol.)
- **One-way: read fixtures, score, emit. Mutate nothing, re-label nothing.** An adversarial
  fixture **inherits** its source label (the `adversarial_fixtures.py` discipline). No module-
  level function both takes and returns a fixture/manifest object.

## Design

Two deliverables on the existing `validation` surface. (a) is an **augmentation** of
`adversarial_fixtures.py` — default-preserving, no `pan_replay` change. (b) is a **new tool**,
`paraphrase_ladder.py` (one `capabilities.d/` fragment + its per-id golden — the drop-in
discipline; **no** count literal, **no** surface-label row).

### M1 — model-free core (stdlib; the "build first" piece, no model)

**(a) RAID attack transforms → `adversarial_fixtures.py`.**

- **New deterministic transforms** added beside the existing three, each a pure `text -> text`
  function, seed-free / deterministic: `article_deletion`, `number_swap`, `paragraph_shuffle`
  (fixed permutation so CI is stable), `misspelling` (deterministic char-edit),
  `alternative_spelling` (US↔UK table), `insert_paragraph` (boilerplate insertion), `case_swap`
  (upper/lower), `whitespace` (extra-space insertion), `synonym_swap` (a small bundled closed
  table — **not** a model). The three existing transforms (`zero_width`, `soft_hyphen`,
  `homoglyph`) and their `TRANSFORMS` keys are **kept verbatim** (default-preserving).
- **A `RAID_ATTACK_CLASSES` register** (module-level mapping) names each RAID attack as an
  obfuscation-class string mapped to its generating transform. The class names are additive;
  `pan_replay`'s four PAN classes are unaffected. **No `pan_replay` change** — `pan_replay`
  already scores every class present in the manifest.
- **Generator CLI extension** — the `--transform` choices gain the RAID transforms; a new
  `--raid-suite` mode emits one `(clean, obfuscated)` pair per RAID attack from one input into a
  `pairs.jsonl` in the layout `pan_replay` consumes (so the two compose with no glue).

**(b) Recursive-paraphrase stress harness → `paraphrase_ladder.py` (new tool, `validation`
surface).**

- **`Ladder` fixture shape.** Input is a fixture dir with a `ladder.jsonl` manifest; each line
  is one ladder: `{"id", "paraphraser": "<label>", "rungs": ["<text r0=clean>", "<text r1>", …]}`
  (rungs inline or via containment-checked relative paths — the `pan_replay` path-traversal
  hardening, copied verbatim). Rung 0 is the clean base; each later rung is "the previous rung
  after one more paraphrase pass." M1 does **not** generate rungs from a model — they are
  supplied (by the stdlib proxy generator below, by M2's DIPPER runner, or by the operator).
- **Stdlib proxy generator** — `build_proxy_ladder(text, *, passes, paraphraser="proxy_stdlib")`
  composes deterministic transforms (synonym swap + alternative-spelling + whitespace) into a
  repeatable paraphrase proxy, producing `passes+1` rungs deterministically, labeled
  `proxy_stdlib`.
- **`score_ladder(ladder) -> dict`** — re-runs the **same** `audit_text(do_tier4=False)` +
  `classify_compression` scoring `pan_replay._score_text` uses (reused, not reimplemented) on
  **every rung**, and builds a per-signal **decay curve** by calling
  `build_robustness_card(base=rung0, fixtures=[(f"rung_{i}", rung_i) for i in 1..N])` and then
  **extracting only the per-cell `base_value` / `fixture_value` / `relative_change` / `label`**
  (the `pan_replay` pattern). It **MUST NOT** embed the card's top-level aggregate dict. The
  per-signal `decay` is the ordered list of per-rung cells (rung 1..N). **Monotonicity is
  observed, not assumed**: a descriptive `monotone: bool` per signal, never enforced.
- **No aggregate.** The result carries `per_signal: {sig: {decay: [...], per_rung_label: [...],
  monotone: bool}}`, `n_rungs`, `n_ladders`, `paraphraser` (the label), and the reused card's
  per-cell vocabulary — and **no** `auc_retained` / `area_under_decay` / `robustness_score` /
  `is_robust` / `n_robust_signals` field.
- **`ClaimLicense` that hardens the separability guardrail.** The license **licenses** "signal S
  shows per-rung relative change Δ_i after i paraphrase passes by paraphraser P on this ladder
  fixture" and **does-not-license** any detector-accuracy headline, any aggregate robustness
  number, **any claim that a signal is robust to paraphrase**, and **any use of a per-rung Δ as
  a retention threshold or cross-signal robustness ranking**. It quotes Sadasivan directly:
  *"As a paraphraser approaches the human distribution, all stylometric signals converge toward
  0.5-AUROC separability (Sadasivan et al. 2023, arXiv:2303.11156); a flat decay curve here means
  this attack did not erode S at THIS paraphrase strength, never that S is paraphrase-robust."*
  A `proxy_stdlib` ladder adds the caveat that the proxy is weaker than a neural paraphraser.
- **`paraphrase_ladder` CLI** — `python3 .../paraphrase_ladder.py --fixtures DIR [--signals ...]
  [--json] [--out PATH]`, model-free, mirroring `pan_replay`'s CLI and envelope (`build_output`,
  `schema_version 1.0`, `task_surface="validation"`). A missing/malformed `ladder.jsonl` exits
  non-zero with a friendly message (the `FixtureError` pattern). A `--build-proxy IN --passes N`
  mode regenerates a stdlib-proxy ladder fixture.
- **Capability registration (drop-in discipline).** Add `capabilities.d/paraphrase_ladder.yaml`
  (`surface: validation`, `status: empirically_oriented`, `handoff: internal`, `consumers: []`,
  `compute.tier: core`, `dependencies.python: []`, `family: paraphrase-stress`) and its per-id
  golden fragment `scripts/tests/_golden_capabilities/paraphrase_ladder.json`. **No** surface-
  label row, **no** count literal (the golden is drop-in). `pan_replay`'s entry is unchanged.
- **Separation guards (structural):** after stripping comments + string literals (so a docstring
  may *name* a forbidden symbol as posture documentation), `paraphrase_ladder.py` and the new
  `adversarial_fixtures.py` transforms import nothing from `{calibrate_thresholds, conformal_gate}`
  and reference no threshold-setting / selection symbol; and **no module-level function both takes
  and returns** a fixture/manifest object. `import` stays stdlib.

### M2 — DIPPER paraphraser over the ladder (model seam; lazy, gated in CI)

- **The runner seam.** Define a `LadderParaphraser = Callable[[str, int], list[str]]` injectable
  runner whose default resolves a DIPPER-grade paraphrase model from a model env var (mirroring
  `SETEC_SURPRISAL_DEVICE`), gated with `pytest.mark.skipif` on the model/torch being absent. CI
  **injects a deterministic stub** runner; a real DIPPER ladder is the GPU-box exercise. The seam
  is lazy so `import` stays stdlib.
- **What M2 adds.** A realistic recursive-paraphrase ladder labeled `dipper` (vs M1's
  `proxy_stdlib`). The **scoring, card-building, decay-curve, and no-aggregate / separability-
  ceiling posture are entirely M1's and unchanged** — M2 only swaps the rung *generator*.
- **Strictly one-way + descriptive.** M2 re-labels no fixture, sets no threshold, is never a
  selector/validator.

## Considered & rejected (posture)

- **An aggregate robustness / "AUROC retained" / area-under-the-decay-curve score, or feeding any
  of it into threshold calibration.** The Goodhart trap and the separability-ceiling trap. The
  deliverable is a per-signal decay curve, **never** a scalar. The no-aggregate walk-test enforces it.
- **Enforcing monotone decay.** Real paraphrase noise produces non-monotone curves; hiding that
  would launder the fixture. Monotonicity is a *descriptive* `monotone: bool`, never enforced.
- **A new detector or a "paraphrase-survivability" classifier.** This is an eval/fixture
  expansion; there is no detector here to survive an attack.
- **Vendoring RAID / DIPPER corpora.** Redistribution-gated; the bundled fixtures are tiny
  synthetic stand-ins.
- **Generating the M1 ladder with a neural paraphraser (collapsing M1/M2).** M1 must be CI-runnable
  and model-free; the stdlib proxy exercises the ladder mechanics.
- **A `_golden_task_surface_labels.json` row / new `claim_license_surfaces` fragment.**
  `paraphrase_ladder` is on the EXISTING `validation` surface (like `pan_replay`, which added no
  row); a row would break the surface-label bijection test.
- **Widening a `pan_replay` "known class vocabulary."** No such filter exists; RAID classes already
  flow through unchanged.

## Non-goals

- A detector, a leaderboard entry, or any provenance verdict.
- Any aggregate robustness/accuracy score, or any change to `calibrate_thresholds.py` /
  `conformal_gate.py`.
- Redistributing RAID/DIPPER data.
- A realistic neural paraphrase in M1 (the stdlib proxy is honestly a proxy; DIPPER is M2).
- Changing `pan_replay`'s behavior, envelope, or tests (the RAID classes are additive; `pan_replay`
  is untouched).
- Changing the three existing `adversarial_fixtures.py` transforms or their `TRANSFORMS` keys.

## Acceptance (stdlib-only where a model isn't required)

1. **RAID transforms are deterministic + pure (M1):** each new `adversarial_fixtures.py` transform
   (`article_deletion`, `number_swap`, `paragraph_shuffle`, `misspelling`, `alternative_spelling`,
   `insert_paragraph`, `case_swap`, `whitespace`, `synonym_swap`) is a pure `text -> text` function
   whose output is **byte-identical across two calls** and **changes** the text (asserted per
   transform). The three existing transforms and their `TRANSFORMS` keys are **unchanged** (asserted).
2. **RAID class register (M1):** `RAID_ATTACK_CLASSES` maps each RAID attack class string to its
   transform; it is additive to the PAN classes. A `pan_replay` run over a fixture dir of only the
   original four classes produces **byte-identical** output to before (regression asserted).
3. **RAID classes flow through `pan_replay` (M1):** a fixture dir with RAID-class `(clean,
   obfuscated)` pairs (e.g. `case_swap`, `whitespace`) replays through `pan_replay` unchanged and
   appears as its own per-class card (per-class slicing intact). *(No "unknown class warning" clause
   — `pan_replay` scores every class present; it has no vocabulary gate.)*
4. **`--raid-suite` generator (M1):** `adversarial_fixtures.py --raid-suite` emits, from one input
   text, one `(clean, obfuscated)` pair per RAID attack in the `pairs.jsonl` layout `pan_replay`
   consumes; the emitted manifest loads under `pan_replay.load_fixture_pairs` without error.
5. **Ladder fixture loading + path hardening (M1):** `paraphrase_ladder` loads a `ladder.jsonl` of
   `{id, paraphraser, rungs:[...]}`; rung 0 is the clean base. A rung path that escapes the fixtures
   dir (via `..` or an absolute path) is **rejected before any read**, and the secret file's contents
   never appear in the error. A missing/malformed `ladder.jsonl` raises `FixtureError` / exits
   non-zero with a friendly message.
6. **Stdlib proxy ladder (M1):** `build_proxy_ladder(text, passes=N)` returns `N+1` deterministic
   rungs (rung 0 = input), byte-identical across calls, each labeled `proxy_stdlib`; rung i differs
   from rung i-1.
7. **Decay curve reuses the robustness card per-cell (M1):** `score_ladder` re-runs the **same**
   `audit_text(do_tier4=False)` + `classify_compression` path `pan_replay._score_text` uses, calls
   `build_robustness_card` with rung 0 as base and rungs 1..N as fixture columns, and the per-signal
   `decay` list's per-rung `relative_change` / label come from that card's **cells verbatim** — and
   the serialized payload does **not** embed the card's aggregate dict. A direct cross-check against
   `build_robustness_card`'s per-cell output is asserted. `monotone` is reported as a `bool`, not
   enforced (a deliberately non-monotone rung sequence still scores, `monotone=False`).
8. **No aggregate score (M1, structural):** neither the RAID expansion nor `paraphrase_ladder` emits
   any banned aggregate key anywhere in the serialized payload — the `pan_replay` `_walk` test, with
   the banned set extended to include `robustness_score`, `auc_retained`, `area_under_decay`,
   `is_robust`, `n_robust_signals`, `n_fragile_signals`, `overall_robustness`, `aggregate_score`,
   `headline` (asserted disjoint at every depth of `results`).
9. **ClaimLicense hardens the separability guardrail (M1):** `paraphrase_ladder`'s rendered
   `ClaimLicense` (a) licenses the per-rung relative-change statement, (b) `does_not_license` any
   detector-accuracy headline **and** the string "robust to paraphrase" **and** the per-rung-Δ-as-
   threshold affordance, and (c) **quotes Sadasivan** by name + arXiv id (`2303.11156`) and frames a
   flat curve as "did not erode at THIS strength," never "paraphrase-robust" (all asserted in the
   rendered block). A `proxy_stdlib` ladder additionally carries the "proxy is weaker than a neural
   paraphraser" caveat.
10. **Separation + no-mutation guards (M1, structural):** `paraphrase_ladder.py` + the new transforms
    import nothing from `{calibrate_thresholds, conformal_gate}` and reference no threshold-setting /
    selection symbol; and no module-level function both **takes and returns** a fixture/manifest type.
    `import` pulls no model dependency.
11. **Capability registration + golden (M1):** `capabilities.d/paraphrase_ladder.yaml` is present
    (`surface: validation`, `status: empirically_oriented`, `handoff: internal`, `compute.tier: core`,
    `dependencies.python: []`); its per-id golden fragment is in `_golden_capabilities/`; the drop-in
    bijection + alphabetical-order tests stay green (no count literal). `pan_replay`'s entry is
    unchanged. **No surface-label row.**
12. **DIPPER paraphraser over the ladder (M2, gated; injected stub `LadderParaphraser`, no model):**
    with a deterministic **stub** runner, `paraphrase_ladder` generates a `dipper`-labeled rung
    sequence, scores it through the **unchanged** M1 card/decay path, and the card records
    `paraphraser="dipper"`. The M2 entrypoint returns only rungs + a card; a real DIPPER run is the
    GPU-box exercise, skipped in CI. *(M2 is a separate PR.)*

## Milestones

1. ⏳ **M1 (model-free, stdlib):** the RAID transforms + `RAID_ATTACK_CLASSES` register +
   `--raid-suite` generator in `adversarial_fixtures.py` (additive, default-preserving, no
   `pan_replay` change); the new `paraphrase_ladder.py` (`Ladder` loader with path-hardening,
   `build_proxy_ladder`, `score_ladder` reusing `_score_text` + per-cell card extraction, the
   per-signal decay curve, the no-aggregate + Sadasivan-ceiling `ClaimLicense`, the CLI); the
   `capabilities.d/paraphrase_ladder.yaml` fragment + its per-id golden; the separation/no-mutation
   structural guards. No model. **Ships with a `proxy_stdlib` paraphraser** — honest mechanics, not a
   DIPPER-grade attack claim. Cite RAID ([arXiv:2405.07940](https://arxiv.org/abs/2405.07940)), DIPPER
   ([arXiv:2303.13408](https://arxiv.org/abs/2303.13408)), and Sadasivan
   ([arXiv:2303.11156](https://arxiv.org/abs/2303.11156)) in the PR body and the `changelog.d/` fragment.
2. ⏳ **M2 (DIPPER paraphraser; gated, stubbed in CI):** the injectable `LadderParaphraser` over the
   **unchanged** M1 scoring/card/decay/posture path. Lands as its own PR.

M1 is the stdlib core + posture surface and is independently useful — operators with their own
paraphrase rungs (an editor's passes, a humanizer tool's output) can run it immediately. M2 swaps the
stdlib proxy for a DIPPER-grade neural paraphraser, changing nothing else. Each lands as its own PR
(changelog fragment, version cut at release — merge commit, never squash; Codex 5.5 is the review gate).
