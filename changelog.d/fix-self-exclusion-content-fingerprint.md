### Fixed

**Content-fingerprint self-exclusion for comparative baselines — completes the Codex self-exclusion
sweep (`aic_pattern_audit`, `crosslingual_voice_distance`, `idiolect_detector`, `general_imposters`).**
A comparative audit that self-excludes the target from its own baseline/reference pool by PATH only
(or not at all) lets a content-duplicate of the target at a different path — or an inline-`text`
manifest row with no path — pool the target into its own comparison, deflating the measured
distance/pattern toward a false "on-voice / not-distinctive" result. The 2026-06-23 sweep
(`cross_doc_novelty_profile`, `originality_audit`, `rank_turbulence_audit`, `model_family_attribution`)
fixed the path-collision variant; these four siblings were still open. Each now drops a pool entry on
PATH match OR content-fingerprint match, where the fingerprint hashes the surface's OWN matcher
tokenization (fail-closed: over-collapsing can only drop a copy, never re-admit one):
`aic_pattern_audit` (`craft_restoration`) — `list_baseline_paths`/`baseline_density` gained target
awareness (were previously unguarded); fingerprint over the lowercased `\w+` word stream.
`crosslingual_voice_distance` (`voice_coherence`) — `_load_baseline` gained a target guard; fingerprint
over `_normalize` (the char-n-gram matcher's equivalence). `idiolect_detector` (`voice_coherence`) —
target/reference cross-check added before keyness; fingerprint over the `word_tokens` stream of the
`strip_non_prose`-cleaned text, computed with the same strip options `build_corpus` scores with, so a
reference copy differing from the target only in stripped material (YAML front matter, code fences,
footers) is recognized as a duplicate and dropped rather than kept while the matcher scores it
identically (PR #306 review); empties fail-closed. `general_imposters` — `_exclude_target_path` gained an opt-in content guard (its
docstring already noted a target copy biases the proportion toward 1.0); fingerprint over its `_tokens`
stream. `corpus_novelty_audit` was assessed and deliberately LEFT UNCHANGED: it is a set-level
diversity audit where identical-but-distinct documents are the redundancy signal (content-dedup there
inverts the metric), a call locked by `test_identical_distinct_path_files_are_the_redundancy_signal`
and pinned by the reverted PR #279.
