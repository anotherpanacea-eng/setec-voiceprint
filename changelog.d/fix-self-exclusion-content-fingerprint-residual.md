### Fixed

**Content-fingerprint self-exclusion for the remaining comparative baselines — closes the residual
bucket of the Codex self-exclusion sweep (`voice_distance`, `dialogue_voice_audit`,
`phraseological_signature_audit`, `discourse_move_signature`, `function_word_grammar_audit`,
`stance_modality_audit`, `productive_roughness_audit`, `punctuation_cadence_audit`, `paragraph_audit`,
`agency_abstraction_audit`).** These surfaces already self-excluded the target from their baseline by
PATH only (`path.resolve() == target`), so a content-duplicate of the target at a DIFFERENT path in a
directory baseline still pooled the target's own profile into its own comparison — pulling the
baseline mean/SD (or centroid) toward the target and deflating the measured distance / z-scores toward
a false "on-voice / in-distribution" result. (Their loaders are file-glob or path-required manifests
with no null-path inline-text vector, so the primary inline vector was already closed; this is the
lower-severity content-duplicate-FILE-at-another-path variant.) Each loader now drops a baseline entry
on PATH match OR content-fingerprint match, where the fingerprint hashes that surface's OWN matcher
tokenization (fail-closed: over-collapsing can only drop a copy, never re-admit one; a content match
only ever DROPS):

- `voice_distance` (`voice_coherence`) — the manifest-level target filter gained a content guard;
  fingerprint over `stylometry_core.word_tokens` (the tokenizer the load-bearing function-word family
  reads).
- `phraseological_signature_audit` / `function_word_grammar_audit` — fingerprint over each surface's
  own lowercased word tokenizer (`_tokenize` / `_tokens_lower`), the exact stream its n-gram / frame /
  function-word features are built from.
- `discourse_move_signature` / `stance_modality_audit` — fingerprint over the LOWERCASED `_WORD_RE`
  word stream, lowercased because every move / stance / modality marker is matched case-insensitively
  (`re.I` / `re.IGNORECASE`), so a re-cased copy is marker-equivalent and is dropped.
- `agency_abstraction_audit` — fingerprint over the CASE-PRESERVED `_WORD_RE` word stream: unlike the
  case-insensitive siblings, `_PROPER_NOUN_RE` is a case-SENSITIVE primary signal, so a re-cased
  document scores a genuinely different profile and must NOT be over-excluded.
- `punctuation_cadence_audit` / `paragraph_audit` / `productive_roughness_audit` — fingerprint over the
  NFC-normalized WHOLE text: these surfaces' signals are punctuation / paragraph-and-sentence structure
  / per-sentence roughness over the raw character sequence, so no word-token stream carries them and
  collapsing whitespace would misrepresent the (whitespace-sensitive) matcher.
- `dialogue_voice_audit` — fingerprint over the extracted DIALOGUE-TURN sequence
  (`(speaker, tag_verb, attributed, text)`), the actual matcher input: per-character profiles are built
  from `extract_dialogue` turns and narration is ignored, so a copy of the target's dialogue (even
  re-wrapped in different narration) is turn-equivalent and dropped; a no-dialogue target fingerprints
  to `None` and disables the guard (no mass-exclusion of narration-only baseline files).

`homogeneity_audit`'s single-doc **proximity** mode (`audit_proximity`, target-vs-`--centroid`) was
assessed and deliberately LEFT UNCHANGED (WONTFIX): its centroid is OPERATOR-DECLARED reference
material — a deliberately supplied set, not an incidentally-scoped baseline — and the surface ships no
verdict and no band (descriptive-only), so there is no automated pass to deflate; silently dropping an
operator-supplied centroid member on a content match would violate the operator's explicit
declaration. Its pool mode, along with `corpus_novelty_audit` and `distinct_diversity_audit`, is a
set-level diversity audit where identical-but-distinct documents are the redundancy SIGNAL
(content-dedup there inverts the metric) and is likewise untouched.

Regression tests (fail-before-fix: plant a content-duplicate at a different path → assert exclusion,
plus a distinct-doc-still-included test) added per surface.
