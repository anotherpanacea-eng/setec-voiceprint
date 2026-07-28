# StoryScope polarity M1 findings

Schema: `narrative_polarity_extension_receipt/1`

This judge-free M1 increment implements and tests the registration, evaluation,
verification, receipt, and producer-contract machinery for the two StoryScope
polarity arms. It contains no judged corpus and reports no empirical polarity
verdict.

The claim boundary is per signal and class-level only. Arm A concerns direction
over internal per-source-work reductions of segmented responses above the
ordinary audit ceiling. Arm B concerns direction below the ordinary floor only
after the registered full-versus-truncated control clears. A confounded or
inconclusive control suppresses the joint spec-78/spec-79 claim. A spec-79
`not_aggregatable` operator also suppresses that joint claim.

The receipt carries these six limits:

- `custody_residue`: an operator able to rewrite registration, manifests,
  prompts, producer artifacts, and receipt together can fabricate a
  self-consistent run.
- `judge_read_unproven`: artifact coherence does not prove that a model or
  human actually read the text.
- `envelope_path_custody`: M1 reopens every producer envelope and exact target
  bytes, but the operator still selects the local paths.
- `prompt_scan_naming_only`: the lexical blindness scan catches naming, not
  paraphrase, examples, or unregistered conditioning.
- `bridge_read_unverified`: `single_pass_whole_text` is a refusable declaration,
  not proof of the read process.
- `shortness_residue`: Arm B establishes invariance to the registered
  deterministic truncation, not validity for natively short composition.

The receipt licenses no per-work reading, provenance verdict, author-likeness
claim, training selection, or corpus admission decision.
