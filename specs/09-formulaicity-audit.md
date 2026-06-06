# 09-formulaicity-audit

> A **non-voice** "phraseological texture" profile: the density of common
> generic / stock phrases (clichés, filler transitions, corporate boilerplate)
> drawn from a small, **user-extensible** built-in list. Descriptive only — and
> explicitly **not** an AI-signal: many writers use these phrases legitimately,
> and the list drifts as language changes.

- **Status:** Ready → building (this PR).
- **Tier:** Tier 5 "adjacent surface" (ROADMAP → "Stockness / Formulaicity Audit … the framing has to be *phraseological texture*, not *AI signal* … Build with skepticism, ship with very explicit claim-licensing.").
- **GPU required:** no — stdlib only (`re`).
- **License decision:** N/A — local code; the built-in phrase list is generic English idiom, not sourced from any vendor's "AI words" list.

## Motivation

Stock phrases ("at the end of the day," "it's important to note," "low-hanging
fruit," "in today's world") are a measurable *phraseological texture*. Useful as a
register/texture profile. But the roadmap is emphatic about the two failure modes:
(1) an "LLM-associated phrase" list drifts as models change — so this is **not**
shipped as an AI detector; (2) many humans use these phrases legitimately — so it's
**not** a quality judgment. This audit ships as descriptive density only, with a
small generic built-in list (clearly illustrative, not authoritative) that the user
can override, and a claim-license that refuses AI/voice/quality inference.

**Orthogonality:** distinct from the shipped `aic_pattern_audit` (syntactic AI
rhetorical *patterns* — correctio, triplet, etc.) and `phraseological_signature_audit`
(the writer's *idiolectic* frames — the opposite of generic). This measures the
density of *generic, shared* phrasing — a different axis.

## Method

Stdlib regex/substring over raw text (case-insensitive, word-boundary):

- A small built-in list (~35 phrases) in labelled groups: `stock_transitions`,
  `hedge_boilerplate`, `corporate_cliche`, `wordy_filler`.
- Per-group hit counts, total stock hits, **stock density / 1k words**, distinct
  phrases matched, and the top matched phrases with counts.
- `--phrases-file PATH` overrides/extends the built-in list (one phrase per line,
  optional `group:phrase`).

All descriptive. No thresholds, no banding.

## Contract (the testable interface)

- **task_surface:** new value `formulaicity` — added to
  `output_schema.VALID_TASK_SURFACES` + `claim_license.TASK_SURFACE_LABELS`
  (additive; inserted at a distinct anchor from #148/#149 so all auto-merge).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/formulaicity_audit.py INPUT[.md|.txt] [--phrases-file PATH] [--json] [--out PATH]` (stdout / `--json` / explicit `--out` only — collision-safe).
- **JSON envelope:** `build_output(task_surface="formulaicity", …)`. `results` keys:
  `total_hits`, `density_per_1k`, `distinct_phrases`, `by_group`, `top_phrases`,
  `list_size`, `custom_list` (bool). Carries a `ClaimLicense`. **No** band/verdict.
- **Claim license:** *licenses* "the density of common generic/stock phrases from a
  small, illustrative, user-extensible built-in list — a phraseological-texture
  measurement"; *refuses* AI-provenance, voice/authorship, and writing-quality
  inference. Caveats: the list is illustrative not exhaustive and drifts over time;
  many writers use these phrases legitimately; density is register-dependent.
- **capabilities.yaml entry:** `id: formulaicity_audit`, `surface: formulaicity`,
  `status: heuristic`, `handoff: none`, `compute: {tier: core, length_floor_words: 300}`,
  `dependencies.python: []`.
- **Availability:** under the 300-word floor → `available=False` + warning.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_formulaicity_audit.py`)

- `test_task_surface_registered` — `formulaicity` ∈ `VALID_TASK_SURFACES`.
- `test_envelope_shape` — payload validates; correct surface.
- `test_no_verdict_keys` — no `band`/`verdict`/`compression`.
- `test_claim_license_refuses_ai_voice_quality` — `does_not_license` names AI / voice / quality.
- `test_builtin_phrase_match` — a passage with 3 known stock phrases → 3 hits.
- `test_case_insensitive` — "At The End Of The Day" matches.
- `test_by_group_and_density` — group breakdown + density/1k computed.
- `test_custom_phrases_file` — `--phrases-file` overrides the built-in list.
- `test_no_false_match_on_substring` — "noteworthy" must NOT match the phrase "note".
- `test_too_short_unavailable`.
- `test_deterministic`.

## Calibration posture

None to calibrate — descriptive. The claim-license names it a texture measurement,
not a signal with an operating point, and explicitly disclaims AI-detection use.

## Out of scope / non-goals

- **Not** an AI detector and not a quality judgment — the roadmap's central guard.
- No band/verdict; no baseline comparison in v1.
- The built-in list is intentionally small and generic, not a curated "AI words"
  list (which the roadmap warns is a maintenance trap and a Pangram-style
  anti-goal).

## Open questions

- Whether to ship a larger curated list as an opt-in data file later (with a
  provenance note), vs. keeping the built-in list deliberately minimal.
