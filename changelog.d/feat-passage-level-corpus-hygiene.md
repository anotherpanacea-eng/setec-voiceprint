### Added

**`near_dup_dedup` passage/span mode — sub-document repetition hygiene (spec 36 M1).**
Both existing hygiene axes are document-grained: exact SHA-256 dedup
(`acquisition_core.content_hash_already_present`) and `near_dup_dedup`'s document
mode both operate on whole documents, so a passage repeated *inside* or *across*
otherwise-distinct documents is invisible to both. The new `--passages` mode adds
that lens in two stages, because no single passage-unit similarity threshold can
see both classes.

- **Stage A — near-duplicate passage units.** Documents split into raw paragraphs
  (never coalesced, never split); sub-floor paragraphs grouped by exact
  normalized-token equality instead of being fed to MinHash (which closes the
  sub-`k` shingle-fallback false-merge class); LSH candidates confirmed against
  the **true shingle sets** rather than `MinHash.jaccard()`'s estimate, so no
  probabilistic estimate participates in any accept/reject decision.
- **Stage B — exact shared-span scan.** A stdlib inverted 8-shingle index reports
  every contiguous verbatim span repeated at ≥ 2 locations, with an arithmetic
  detection guarantee (`L − k + 1` consecutive shared shingles ⇒ every verbatim
  span of at least `max(k, min_span_words)` tokens is reported), regardless of
  what surrounds it. Word-granularity analogue of the exact-substring dedup pass
  in **Deduplicating Training Data Makes Language Models Better**
  ([arXiv:2107.06499](https://arxiv.org/abs/2107.06499)); repeated spans being
  memorized disproportionately fast is the mechanism in **Quantifying
  Memorization Across Neural Language Models**
  ([arXiv:2202.07646](https://arxiv.org/abs/2202.07646)).

Report-first and no-verdict: spans are reported for consumer-side loss masking or
chunk-stream filtering, never excised, and the report carries a mandatory
`assumptions` params-and-limits block plus a real `ClaimLicense` that refuses any
"memorization-safe" / "clean corpus" determination, any AI/human verdict, any
claim that a reported repetition is illegitimate, and any absolute memorization
rate. Passage mode never rewrites the input manifest. Ships heuristic /
uncalibrated — no bands, no thresholds promoted.

The optional `--out MANIFEST --passage-dir DIR` export writes a
`manifest_validator`-clean passage-unit corpus: one text file per kept passage,
rows carrying a resolvable `path` and **every inheritable source field copied
verbatim** (including `ai_status`, `privacy`, `consent_status`). It **refuses
entirely** — no partial write, no bypass flag — when a source row lacks a field
required on the output row, so the hygiene pass can never launder a
redaction status onto a training artifact.

**New `pool_guard` — duplicate-dependent pools refuse a passage-deduped manifest.**
`corpus_novelty_audit`, `homogeneity_audit`, `distinct_diversity_audit`,
`skeleton_overlap_audit` and `cross_doc_novelty_profile` measure signals that live
*in* retained duplicates (collapse / homogeneity / template reuse / leave-one-out
novelty), so consuming a passage-deduped pool destroys the measured object — the
repo's recurring #306/#307 bug class. Each now scans its manifest path for the
export's `passage_dedup` marker and abstains with `available:false` /
`reason_category: bad_input` / rc 3, naming the invariant and the guard's own
limit (it is a manifest-path check; directory inputs carry no row metadata). The
mechanism is a file-level scan called per surface rather than a shared-loader
kwarg, because the pool-loader class contains clean-room copies a shared signature
cannot reach and every loader discards the row dict the marker lives in.
`originality_audit`, `cross_doc_argument_consistency`, `general_imposters` and
`binoculars_calibrate` are pinned **exempt with rationale** — dedup of a
comparison or calibration pool is legitimate, and firing there would be the exact
inversion the guard forbids. A coverage drift test embeds the complete
nine-module classification map with mandatory rationale strings and three closure
sweeps, so a new axis surface, a new pool loader, or a new clean-room copy fails
until it is classified.
