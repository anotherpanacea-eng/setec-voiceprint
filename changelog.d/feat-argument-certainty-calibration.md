### Added

- **Argument certainty calibration (`argument_certainty_calibration`)** — a new
  `argument_calibration` surface that profiles, for ONE argument-shaped document and
  **per load-bearing claim**, whether the claim's expressed **certainty** (hedged ↔
  assertive) matches the evidential **support** it actually carries — flagging
  **overclaim** (asserted hard / thin support) and **underclaim** (tentative / strong
  support). The per-claim `certainty × support → alignment` table IS the read — **no
  top-level overconfidence score, no calibration score, no verdict that the author is
  arrogant / sloppy / dishonest.** The PER-CLAIM complement to `stance_modality_audit`'s
  DOCUMENT-LEVEL hedge/booster distribution (a claim-localized mismatch the
  document-level distribution cannot produce). Spec: `specs/argument-certainty-calibration.md`.
  - **Claim extraction + per-claim support are one NEW `extract_claims` LLM-judge pass**
    (`argument_certainty_judge`) over free text — `argument_judge` labels PARAGRAPHS
    `{role, mode}` and does NOT extract claims, so this is a new seam. Each claim carries a
    verbatim locus (validated `text[start:end] == quote` at the surface — a fabricated
    claim span is dropped) and a judge-derived `support ∈ {none, gestured, substantiated}`.
  - **Expressed certainty is the DETERMINISTIC M1 substrate:** frozen `HEDGE_VOCAB` /
    `BOOSTER_VOCAB` frozensets (multi-word or word-boundary-guarded — **no bare `"may"`**),
    computed over each claim's quote → `{tentative, measured, assertive}` (booster+hedge →
    `measured`; bare assertion → `assertive`). The M1 lexicon is **authoritative** for
    certainty; an M2 judge refinement never silently overrides it.
  - **The legitimate-strong-claim filter ships ONLY the two EVIDENCE-GATED defenses**
    (firewall-critical): `defended_stipulated` (an explicit stipulation marker — `assume` /
    `grant` / `for the sake of argument` / `take as given` — in the claim's quote,
    `str.find`-validated) then `defended_elsewhere` (a REAL in-document supporting locus for
    the claim, validated `text[start:end] == quote`). **A fabricated cross-reference FAILS
    validation → build error** (`CalibrationLocusError`), closing the firewall hole. The
    judgmental defenses (`defended_analytic` / `defended_common_ground`) are **M2-only** and
    NEVER fire in M1 (the schema rejects an M2-only defense on an M1 envelope).
  - **The no-verdict firewall is MECHANICAL, not rhetorical** — a CERTAINTY-SCOPED rename of
    `within_doc_segmentation`'s `assert_no_authorship` (it does NOT reuse the authorship
    keys/substrings): a `FORBIDDEN_RESULT_KEYS` frozenset (`overconfident` / `arrogant` /
    `dunning_kruger` / `dishonest` / `sloppy` / `unsound` / `overconfidence_score` /
    `calibration_score` / `author_verdict`) + a `FORBIDDEN_SUBSTRINGS` tuple + a recursive
    `assert_no_verdict()` guard (raises `CalibrationVerdictError`) called immediately before
    `build_output`, routing to `available:false` / `policy_refused`. **Filter-integrity is
    mechanical:** an `overclaim` (or any `defended_*`) row with an empty `rationale` is a
    BUILD ERROR (the Python output schema's `validate_claim_row` raises), never a silent
    finding.
  - **M1 = mock-deterministic judge** (CI-safe, marker-driven claim extraction; the certainty
    lexicon and the legitimate-strong-claim filter are mechanical Python — no API, no models);
    **M2 = anthropic** (lazy-import / fail-loud). Ships `calibration_status: heuristic` —
    directional, **no numeric anchor**. Single-document scope (no `--reference` / `--compare`
    cross-doc seam).
