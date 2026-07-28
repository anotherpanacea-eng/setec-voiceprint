### Fixed

**Private-dyadic register isolation reached four unguarded pooled author
references.** `assert_personal_register_isolated` shipped with its call sites
chosen ad hoc, so surfaces that build a pooled author reference without going
through `build_profile` were never covered. `general_imposters` was the worst
case (P1): it derives ONE shared feature vocabulary from `candidate_docs +
impostor_docs` and had no guard at all, and its impostor selector's register
filter short-circuited on an empty register (`not register or ...`), so an
un-inferable candidate register silently admitted rows of *every* register —
private-dyadic ones included — into that shared space. Register matching in both
selectors is now exact: `""` means the register-less slice, not a wildcard, which
leaves a genuinely register-free manifest behaving as before while failing closed
otherwise. `pov_voice_profile` (per-POV centroids, with `select_feature_names`
run over the union of all POVs), `controls_audit` (pooled function-word baseline
mean) and `lambdag_audit` (pooled reference/background grammar LMs) are guarded
too.

**The guard moved to `register_taxonomy`.** It lived in `stylometry_core`, whose
import pulls spaCy and NLTK — so the pooling surfaces that ship their own
lightweight featurizers precisely to avoid that cost structurally could not reach
it, which is how the gap opened. `register_taxonomy` is stdlib-only and
`stylometry_core` re-exports the guard, so every existing import path is
unchanged. Refusal message, semantics, and the allowance for a same-tier
messaging reference are all unchanged.
