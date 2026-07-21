# SETEC corpus manifest schema

The canonical reference for the `corpus_manifest.jsonl` contract. SETEC's task surfaces (smoothing diagnosis, voice coherence, validation + calibration, craft restoration, and the uncalibrated-by-default discrimination-evidence Surface 5) all read from manifests; the validator at `scripts/manifest_validator.py` enforces this schema and emits ratchet warnings when downstream tools would otherwise produce subtly-wrong results.

**Format:** JSONL — one JSON object per line, blank lines and lines starting with `#` skipped.

**Validate:** `python3 -u scripts/manifest_validator.py path/to/manifest.jsonl --progress-every 1000`. Exits non-zero on errors; warnings print but don't fail the run unless `--strict`. The unbuffered launcher makes the aggregate stderr heartbeat visible during long scans; set `--progress-every 0` to disable it.

## Required fields

Every entry must carry these. Missing any → error.

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable, unique within the manifest. The handle downstream tools use to refer to the entry. |
| `path` | string | Absolute path or relative to the manifest's directory. Must resolve to an existing file (not a directory). |
| `ai_status` | string enum | One of `ALLOWED_AI_STATUS` (see below). |
| `use` | list[string] | Non-empty list of strings drawn from `ALLOWED_USE`. Multiple tags allowed (e.g., `["voice_validation", "validation"]` for entries serving two surfaces). |

## Common optional fields

| Field | Type | Used by |
|---|---|---|
| `author` | string | Voice-coherence (POV / drift / impostor work) |
| `persona` | string | Voice-coherence; one author can have multiple personas with measurably different voiceprints |
| `register` | string enum | All surfaces; selects which baseline a draft compares against |
| `genre` | string | Narrower than register |
| `date_written` | string | ISO partial date (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`). `voice_drift_tracker.py` requires this for grouping |
| `editing_status` | string enum | Sanity-checked against `ai_status` |
| `word_count` | number | Non-negative; informational |
| `split` | string enum | Train / test / holdout / baseline. Validator catches `use: validation` + `split: baseline` contradiction |
| `privacy` | string enum | Voice-cloning ratchet enforces `private` for voiceprint-sourced entries |
| `language_status` | string enum | ESL ratchet warns when non-native entries land in voiceprint-sourced uses |
| `pov` | string | Multi-POV fiction; `pov_voice_profile.py` groups by this |
| `adversarial_class` | string | Adversarial-fixture work; `validation_harness.py` slices by this |
| `source` | string | Provenance breadcrumb |
| `notes` | string | Free-text |

## Owner-corrections sidecar

`apply_owner_corrections.py` is an explicit, pre-registration metadata pass for
an owner-reviewed correction. It is not part of a manifest: the source manifest
and its correction JSONL remain separate, and the tool writes a new corrected
manifest. No registration consumer discovers or applies a sidecar implicitly.

Each nonblank sidecar line is one object with this closed schema:

```json
{
  "schema": "setec-owner-correction/1",
  "match": {"id": "doc-1", "content_hash": "sha256:..."},
  "expect": {"register": "blog_essay"},
  "rewrite": {"register": "personal"},
  "note": "owner-reviewed classification"
}
```

- `match` is a nonempty ANDed set of exact, case-sensitive string equalities on
  `id`, `path`, `source_id`, and/or `content_hash`; each rule must match exactly
  one row. It never resolves paths, coerces values, or uses glob/regex matching.
- `expect` is optional stale-state protection for existing `register` and `era`
  values. `rewrite` is a nonempty replacement limited to those same
  validator-approved enum fields. Identity, content, provenance, privacy,
  consent, authorship/training, and `notes` fields are immutable.
- `note` is a nonempty owner audit rationale. It stays in the sidecar and is
  bound by the sidecar hash in the aggregate receipt; it is never copied into a
  manifest `notes` field or stdout.
- Unknown keys, duplicate JSON keys, invalid UTF-8/BOM, non-finite JSON,
  malformed identities, zero/multiple matches, conflicting rules, stale
  expectations, or a validator-invalid result refuse the whole operation.

The corrected output is canonical UTF-8 JSONL with one LF per data row. The
applier preserves data-row order, refuses unsafe publication, and reports only
aggregate hashes/counts. It does not inspect or copy corpus prose, infer a
classification, or alter source text/content hashes. Use the corrected manifest
only by passing it explicitly to a compatible consumer. In particular, an
existing `document_local` attestation binds its original manifest bytes: a
corrected manifest requires a separately attested workflow and must not be
substituted under that attestation.

## Impostor-corpus fields (1.14.3+)

For impostor-pool support per `internal/2026-05-08-impostor-corpus-spec.md`. Required only when the entry's `corpus_role` is `impostor`; recommended otherwise.

| Field | Type | Required when | Values / notes |
|---|---|---|---|
| `corpus_role` | string enum | always (default `identity_baseline` for backward compatibility when absent) | `identity_baseline`, `impostor`, `distractor`, `adversarial` |
| `impostor_for` | list[string] | `corpus_role: impostor` | persona slugs this entry serves as impostor against; one entry can serve multiple personas if registers match |
| `register_match` | string enum | `corpus_role: impostor` | `high`, `medium`, `low` — closeness of register match to the impostor target |
| `topic_match` | string enum | `corpus_role: impostor` | `high`, `medium`, `low` — closeness of topical overlap |
| `consent_status` | string enum | always for `corpus_role: impostor`; recommended for all entries | `public_record`, `cc_licensed`, `fair_use_research`, `author_consent`, `undocumented` |
| `era` | string enum | always for `corpus_role: impostor`; recommended for `identity_baseline` entries with impostor-relevant `use` | `pre_chatgpt` (before Nov 2022), `pre_ai_widespread` (before mid-2024), `post_ai_widespread`, `undated` — finer than `ai_status` for impostor calibration |
| `acquired_via` | string | `corpus_role: impostor` | provenance string; e.g. `acquire_blog_substack_rss_2026-05-08`, `pdf_extract_text_layer_2026-05-08` |
| `content_hash` | string | recommended for all entries | SHA-256 of the cleaned text, for deduplication and tamper-detection |

## Allowed enum values

| Field | Values |
|---|---|
| `ai_status` | `pre_ai_human`, `ai_generated`, `ai_generated_from_outline`, `ai_assisted`, `ai_edited`, `mixed`, `unknown` |
| `register` | `literary_fiction`, `blog_essay`, `academic_philosophy`, `testimony_policy`, `personal`, `policy_advocacy`, `literary_horror`, `policy_brief`, `scholarly_article`, `legal_brief`, `grant_proposal`, `expert_affidavit`, `regulatory_comment`, `professional_letter`, `teaching` |
| `split` | `baseline`, `train`, `test`, `holdout` |
| `privacy` | `private`, `shareable`, `public_domain` |
| `use` | `baseline`, `validation`, `voice_validation`, `voice_profile`, `voice_impostor`, `idiolect`, `negative_baseline`, `exclude` |
| `editing_status` | `raw_draft`, `revised_human`, `published_cleaned`, `coauthored` |
| `language_status` | `native`, `non_native_advanced`, `non_native_intermediate`, `learner`, `unknown` |
| `corpus_role` | `identity_baseline`, `impostor`, `distractor`, `adversarial` |
| `register_match` / `topic_match` | `high`, `medium`, `low` |
| `consent_status` | `public_record`, `cc_licensed`, `fair_use_research`, `author_consent`, `undocumented` |
| `era` | `pre_chatgpt`, `pre_ai_widespread`, `post_ai_widespread`, `undated` |

## Operational definitions: `ai_status`

The framework's `ai_status` vocabulary is more granular than the binary "AI / not AI" pattern most manifests carry. Use the value that best describes how the prose came into existence.

| Value | When to use |
|---|---|
| `pre_ai_human` | Authored before ChatGPT release (Nov 2022) or attested by the human author as no-AI involvement. The cleanest negative class for AI-vs-human surveys. |
| `ai_assisted` | Human-authored prose where the writer engaged an LLM collaboratively during drafting — per-suggestion human adjudication. The LLM proposed phrasing, alternatives, restructuring; the human accepted, rejected, or rewrote each suggestion. Writer's agency over the final form is strong. |
| `ai_edited` | Human-authored prose passed through an LLM for low-touch editing — "polish this," "fix grammar," "improve flow." The LLM made changes the human did not individually adjudicate; suggestions accepted in bulk. Writer's agency weaker than `ai_assisted`. |
| `ai_generated` | LLM-generated, human-input degree unspecified or unknown. The backwards-compat catch-all. |
| `ai_generated_from_outline` | LLM-generated with a documented substantive human seed (outline, draft, brief, transcript, point-by-point structure). Use when the manifest authority knows the LLM received more than a thin prompt. Added 2026-05-13. |
| `mixed` | Multiple authorship states across sections of one document. Requires `notes.composite_states` array listing which states appear (warning if absent). |
| `unknown` | Genuine label ambiguity (e.g., scraped corpus with labels not preserved). Discouraged when other fields are knowable. |

The `ai_assisted` / `ai_edited` distinction is a writer's judgment call; the validator does not enforce semantic correctness, only vocabulary membership.

## Ratchet rules

The validator enforces these beyond the per-field schema. All emit warnings unless noted as errors.

1. **Required-field check** (error). Every entry must have `id`, `path`, `ai_status`, `use`. Missing → error.
2. **Path integrity** (error). The path must resolve to an existing file, not a directory.
3. **Duplicate id / two-ids-one-file** (error / warning). Same id twice → error; two ids pointing at one file → warning.
4. **Unknown enum values** (warning). Any enum-valued field with a value outside its allowed set warns.
5. **Unknown field name** (warning). Top-level fields not in the schema warn (typo catcher).
6. **`use: validation` + `split: baseline`** (error). Validation entries must live outside the baseline split.
7. **Voiceprint privacy ratchet** (warning). Entries with `use: voice_profile` or `use: idiolect` and `privacy != "private"` warn. Voiceprint sources are voice-cloning inputs.
8. **ESL ratchet** (warning). Entries with `language_status: non_native_*` and `use: baseline` / `voice_profile` / `idiolect` warn. ESL prose sits in the low-variance region the AI-smoothing detector flags.
9. **AI-status / editing-status sanity** (warning). `pre_ai_human` + `editing_status: coauthored` is contradictory.
10. **Impostor required fields** (error). `corpus_role: impostor` requires `impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`, `acquired_via`.
11. **Persona-reference cross-check** (warning). An impostor's `impostor_for` should name personas that exist in the manifest's identity-baseline entries.
12. **High register-match cross-check** (warning). An impostor with `register_match: high` whose own `register` doesn't appear in any of the named target persona's registers warns.
13. **Consent-status redistribution ratchet** (warning). `corpus_role: impostor` + `consent_status: undocumented` warns. Future public-report harnesses should escalate to refusal.
14. **Era recommendation for impostor entries from post-AI era** (warning). `corpus_role: impostor` + `era: post_ai_widespread` warns; post-2024 prose may include AI-collaborated writing that contaminates the human-impostor signal.
15. **Era recommendation for impostor-relevant identity baselines** (warning). Entries with effective `corpus_role: identity_baseline` and `use` overlapping `{baseline, voice_profile, voice_validation, idiolect, voice_impostor}` warn when `era` is missing.
16. **`ai_status: mixed` composite-states consistency** (warning). Entries with `ai_status: mixed` should carry a `notes.composite_states` array listing the authorship states present across sections. Without it, the `mixed` value is semantically empty and downstream consumers cannot route by state. Soft warning so legacy `mixed` entries don't break, but new ones get nudged toward the structured form.

## Summary block

The validator's report includes per-field counts:

- `by_register`, `by_ai_status`, `by_split`, `by_use`, `by_privacy`, `by_persona`, `by_language_status`, `by_adversarial_class` (existing)
- `by_corpus_role`, `by_era`, `by_consent_status`, `by_register_match` (1.14.3+; impostor-corpus support)

## Examples

### Identity baseline (writer's own pre-AI prose)

```json
{
  "id": "essay_2018_03_voice_first",
  "path": "essays/2018-03-voice-first.md",
  "author": "Jane Q. Author",
  "persona": "blog",
  "register": "blog_essay",
  "date_written": "2018-03-14",
  "ai_status": "pre_ai_human",
  "language_status": "native",
  "word_count": 1850,
  "use": ["baseline", "voice_profile"],
  "split": "baseline",
  "privacy": "private",
  "corpus_role": "identity_baseline",
  "era": "pre_chatgpt",
  "content_hash": "sha256:..."
}
```

### Impostor entry (non-self writer matched to a target persona)

```json
{
  "id": "impostor_smith_blog_2019_05",
  "path": "impostors/blog_essay/smith_jeh/2019-05-essay.txt",
  "author": "Justin E. H. Smith",
  "persona": "smith_jeh_substack",
  "register": "blog_essay",
  "date_written": "2019-05-22",
  "ai_status": "pre_ai_human",
  "language_status": "native",
  "word_count": 3210,
  "use": ["voice_impostor"],
  "split": "baseline",
  "privacy": "private",
  "corpus_role": "impostor",
  "impostor_for": ["blog"],
  "register_match": "high",
  "topic_match": "medium",
  "consent_status": "fair_use_research",
  "era": "pre_chatgpt",
  "acquired_via": "acquire_blog_substack_rss_2026-05-08",
  "content_hash": "sha256:...",
  "source": "https://jehsmith.substack.com/p/...",
  "notes": "Stylometrically similar register; topic varies"
}
```

### Validation entry (labeled AI sample for the smoothing harness)

```json
{
  "id": "ai_smoke_chatgpt_2024_08_test",
  "path": "validation/ai_smoke_chatgpt_2024_08_test.md",
  "author": "ChatGPT-4o",
  "register": "blog_essay",
  "date_written": "2024-08-15",
  "ai_status": "ai_generated",
  "language_status": "native",
  "word_count": 1200,
  "use": ["validation"],
  "split": "test",
  "privacy": "shareable",
  "adversarial_class": "none",
  "content_hash": "sha256:..."
}
```

## See also

- `scripts/manifest_validator.py` — the validator, importable as `validate_manifest(path) -> dict`
- `scripts/README.md` — task surfaces and which scripts read from manifests
- `internal/2026-05-08-impostor-corpus-spec.md` (gitignored) — the impostor-corpus design that introduced the new fields
