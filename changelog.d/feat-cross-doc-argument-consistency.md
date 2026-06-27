### Added

- **Cross-document argument consistency (`cross_doc_argument_consistency`)** — a new
  `argument_consistency` surface that maps where an author's argument corpus has its
  **load-bearing commitments in tension across documents**, descriptively. Given a FOCAL
  document vs the rest of a supplied pool, it extracts each document's typed commitments
  (claim / warrant / scope_condition / value_premise / empirical_premise) via a NEW
  `argument_judge`-style LLM-judge pass over free text (`extract_commitments` — **not**
  `argument_spine` parsing), aligns them cross-document by a judge-assigned `topic_ref`,
  classifies each aligned pair's relation (`consistent` / `tension` / `direct_conflict` /
  `incomparable`), and emits a descriptive tension ledger with verbatim loci, a
  legitimate-variation verdict, a descriptive severity ordinal, and a firewall-safe class
  of resolution. The tensions ARE the read — **no top-level consistency score, no author
  score, no "winning document."** The argument-CONTENT sibling of `cross_doc_novelty_profile`
  (the stylometric sibling); the nonfiction-argument analogue of Series Continuity /
  world-bible self-consistency.
  - **The no-verdict firewall is MECHANICAL, not rhetorical** (a clone of
    `within_doc_segmentation`'s `assert_no_authorship`): a `FORBIDDEN_RESULT_KEYS` frozenset
    (`hypocrisy` / `dishonest` / `bad_faith` / `contradicts_self` / `who_is_right` /
    `author_verdict` / `winning_document` / `consistency_score` / `author_score` …) + a
    `FORBIDDEN_SUBSTRINGS` tuple + a recursive `assert_no_verdict()` guard (raises
    `ConsistencyVerdictError`) called immediately before `build_output`, routing to
    `available:false` / `policy_refused`. The guard also whitelist-enforces the `severity`
    ordinal and rejects any non-tension `relation` reaching the ledger.
  - **The legitimate-variation filter is a required mechanical stage:** every surface
    tension runs through five defenses in a fixed precedence — `retraction → time → scope →
    audience → genre` — and the first defense whose REQUIRED textual evidence is present in
    the aligned loci fires (`defended_<that>`); none firing → `genuine`. Defended tensions
    APPEAR in the ledger marked `defended_*`, with the defense named (showing them is more
    honest than hiding them). **Filter-integrity is mechanical:** a `defended_*` (or
    `genuine`) row with an empty `rationale` is a BUILD ERROR (the Python output schema's
    `validate_results` raises), never a silent finding.
  - **M1 = mock-deterministic judge** (CI-safe, marker-driven extraction + a deterministic
    relation rule + the keyword-evidence variation filter — no API, no models);
    **M2 = anthropic** (lazy-import / fail-loud). Ships `calibration_status: heuristic` —
    directional, **no numeric anchor** (no measured discrimination). Spec:
    `specs/cross-doc-argument-consistency.md`.
