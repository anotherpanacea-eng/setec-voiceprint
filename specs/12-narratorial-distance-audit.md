# 12-narratorial-distance-audit

> A descriptive profile of **narratorial distance / free-indirect discourse**:
> pronoun anchoring, perception/cognition-verb density, deictic anchoring
> (here/now/this), evaluative-adjective density, and FID markers — for developmental
> editing of literary fiction.

- **Status:** Spec (full-deps box — needs spaCy).
- **Tier:** Tier 4 (ROADMAP → "Narratorial Distance / Free Indirect Audit … outside standard stylometry literature but valuable for developmental editing of literary fiction. Adjacent to per-POV voice profile.").
- **GPU required:** no, but **needs spaCy** (POS/dep + lemma for verb classes).
- **License:** N/A (local).

## Motivation & orthogonality

Free-indirect discourse and narratorial distance are core to literary fiction craft
and invisible to every shipped surface. This audit measures *where the narration sits*
relative to a character's consciousness — close (FID, high perception-verb + deictic
density) vs. distant (low). Descriptive craft instrument, adjacent to
`pov_voice_profile`; **not** a voice-identity or AI tool.

## Method (spaCy-backed)

Per window/chapter: pronoun-anchoring ratio (3rd-person + proximal deixis),
perception/cognition-verb density (saw/felt/knew/wondered — lemma set), deictic
anchoring (here/now/this/that/today vs. there/then), evaluative-adjective density,
and a heuristic FID score (past-tense + proximal-deixis + no quotation/tag). Report a
**distance trajectory** across document position (close↔distant), descriptive only.

## Contract

- **task_surface:** new `narratorial_distance` (voice-coherence family; add to enum + labels).
- **CLI:** `python3 scripts/narratorial_distance_audit.py MANUSCRIPT [--window-strategy paragraph|chapter] [--json] [--out PATH]`.
- **JSON envelope:** `results` = per-window distance features + an overall close/distant distribution + the trajectory series. `ClaimLicense`.
- **Claim license:** *licenses* "descriptive narratorial-distance / FID features and their trajectory across the text"; *refuses* authorship, AI, and quality inference; notes FID detection is heuristic, not a parse of literary intent.
- **capabilities.yaml:** `id: narratorial_distance_audit`, `surface: narratorial_distance`, `status: heuristic`, `compute: {tier: spacy, length_floor_words: 1500}`, `dependencies.python: [spacy]`.

## Test contract (`tests/test_narratorial_distance_audit.py`)

- `test_surface_registered`; `test_perception_verb_density`; `test_deixis_split` (proximal vs distal); `test_fid_heuristic`; `test_trajectory_shape`; `test_claim_license_refuses_verdict`; `test_envelope_shape`; `test_deterministic`.

## Non-goals

- No literary-quality judgment; no authorship/AI call.
- FID detection is a heuristic signal, not a claim about authorial technique.

## Open questions

- Verb-class lists are register-tunable; ship a default set + `--verb-lexicon` override.
