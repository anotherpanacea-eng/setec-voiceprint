### Fixed

**Self-exclusion content fingerprints no longer OVER-EXCLUDE baselines the audit scores differently —
generalizes the PR #307 Codex fix from `voice_distance` to its comparison-against-baseline siblings
(`function_word_grammar_audit`, `discourse_move_signature`, `stance_modality_audit`,
`agency_abstraction_audit`, `phraseological_signature_audit`, `punctuation_cadence_audit`,
`paragraph_audit`, `productive_roughness_audit`).** Each surface's content-duplicate guard now
fingerprints the **whole scored text** the way `voice_distance` does — `sha256` of the exact string the
per-file audit reads, verbatim on both sides — so the fingerprint's equivalence class is the scored
string itself: a guard drops only an EXACT copy and KEEPS any baseline the audit would score differently
(no over-exclusion). The eight guards were not all broken the same way:

- **Five had a genuine over-exclusion bug** (`function_word_grammar`, `discourse`, `stance`, `agency`,
  `phraseological`): they hashed the surface's own **token stream** (a lowercased / case-preserved word
  regex). Codex flagged on `voice_distance` that a token-stream fingerprint folds punctuation/case, so
  it drops a baseline that differs from the target *only* in punctuation/case — yet that baseline is a
  distinct document to the surface's punctuation-/case-SENSITIVE features (sentence-run segmentation,
  per-sentence move classification, `\s+`-joined multi-word markers, case-sensitive proper-noun /
  slot-frame templates). Over-excluding it silently CHANGES the baseline reference corpus rather than
  merely self-excluding the target. Confirmed empirically for each: a punctuation/case variant the
  audit scores differently was being dropped.
- **Three were already whole-text `sha256` over the NFC-normalized text** — NOT token streams
  (`punctuation_cadence`, `paragraph`, `productive_roughness`). They change for two narrower reasons:
  - *Preprocessing alignment* (`punctuation_cadence`, `paragraph`, which run `strip_non_prose`): the
    guard now hashes the **cleaned** text — the actual scored input — instead of the raw text, so the
    fingerprint matches what the audit reads and a copy wrapped in stripped front matter is now caught
    (a raw-text hash missed it).
  - *Dropping an overly broad NFC fold* (all three): NFC normalization could over-collapse a
    Unicode-composition variant that the word tokenizers split differently (so the audit scores it
    differently). Hashing the scored string verbatim keeps such a variant instead of over-excluding it.

Grouped by preprocessing: the six `strip_non_prose` surfaces (`function_word_grammar`, `discourse`,
`stance`, `agency`, `punctuation_cadence`, `paragraph`) hash the **cleaned** text; the two that do not
run `strip_non_prose` (`phraseological`, `productive_roughness`) hash their audit's scored input —
`productive_roughness` the raw text, and `phraseological` the text after its own `keep_quotes` handling
(blockquote lines stripped under the default, kept with `--keep-quotes`) so a quote-wrapped copy the
audit scores identically is still self-excluded on both sides.

`dialogue_voice_audit` is deliberately unchanged: its matcher is narration-agnostic, so its
extracted-turn-sequence fingerprint already is its exact scored input, and a whole-text hash would
*leak* a narration variant into the baseline. Per-surface regression tests updated: the five
token-stream surfaces now assert a punctuation/case variant is KEPT (was asserted excluded); the strip
surfaces assert a front-matter-wrapped copy is excluded; and `punctuation_cadence`, `paragraph`, and
`productive_roughness` assert a Unicode-composition (NFD) variant the audit scores differently is KEPT.
