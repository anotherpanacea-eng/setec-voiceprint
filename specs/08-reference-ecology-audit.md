# 08-reference-ecology-audit

> A **non-voice** profile of how a document *references the world*: citation style
> and density, quotation/attribution patterns, and external-link/domain breadth.
> Ships on a new `reference_ecology` surface with claim-licensing that refuses
> voice/AI inference **and** explicitly flags topic-leakage — reference ecology
> changes with subject matter, not with authorial voice.

- **Status:** Ready → building (this PR).
- **Tier:** Tier 5 "adjacent surface" (ROADMAP → "Reference Ecology Audit … heavily topic-bound … ship with claim-licensing that explicitly refuses voice attribution. Better as a thematic / register profile than a voice tool.").
- **GPU required:** no — stdlib only (`re`, `statistics`, `collections`).
- **Upstream / prior art:** ROADMAP "Tier 5 — Adjacent surfaces → Reference Ecology Audit" (+ the adjacent Allusion / Quotation Habit note, folded in here as quotation/attribution density).
- **License decision:** N/A — all local code.

## Motivation

Essayists, scholars, and policy writers have identifiable *reference ecologies* —
how densely they cite, in what style (parenthetical vs. attributive), how much they
quote, how broad their external-link domains are. Useful for thematic/register
profiling. But the roadmap is emphatic that this is **not** a voice surface: a
writer changes topic and the reference ecology changes wholesale, so reading it as
voice drift is a category error. This audit honors that — descriptive measurements
only, no band/verdict, and a claim-license that refuses voice/authorship/AI
inference and names the topic-leakage hazard up front.

**Orthogonality:** distinct from `document_layout` (#148, structural formatting) and
from the shipped `phraseological_signature_audit` (idiolectic phrase frames) — this
measures *external reference behavior*, which no existing surface captures.

## Method

Stdlib regex over raw text:

- **Citations** — parenthetical `(Name … 1600–2099)` count + per-1k rate; DOI
  (`10.\d{4,}/…`), arXiv (`arXiv:…`), and `et al.` markers.
- **Footnotes** — Markdown footnote refs `[^id]` + definitions `[^id]:`.
- **Attribution** — "according to X" / "as X" + `X argues/notes/writes/observes/
  contends/suggests/claims` constructions; count + per-1k rate.
- **Quotation** — inline quote-pair count (straight `"` pairs + curly `“…”`) +
  blockquote-line count; quotation density / 1k.
- **Link ecology** — markdown + bare URLs, total + density/1k, **distinct domain
  count** (breadth) + top domains.

All descriptive. No thresholds, no banding.

## Contract (the testable interface)

- **task_surface:** new value `reference_ecology` — added to
  `output_schema.VALID_TASK_SURFACES` and `claim_license.TASK_SURFACE_LABELS`
  (additive; inserted at a distinct anchor from #148 to avoid a merge conflict).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/reference_ecology_audit.py INPUT[.md|.txt] [--json] [--out PATH]` (stdout / `--json` / explicit `--out` only — collision-safe).
- **JSON envelope:** `build_output(task_surface="reference_ecology", …)`. `results`
  keys: `citations`, `footnotes`, `attribution`, `quotation`, `links`. Carries a
  `ClaimLicense`. **No** band/verdict.
- **Claim license:** *licenses* "the document's reference ecology: citation style
  and density, quotation / attribution patterns, and external-link / domain
  breadth"; *refuses* voice / authorship / AI-provenance inference, **and** states
  that reference ecology is heavily topic-bound — it shifts with subject, not voice,
  so it must not be read as voice drift.
- **capabilities.yaml entry:** `id: reference_ecology_audit`, `surface:
  reference_ecology`, `status: heuristic`, `handoff: none`, `compute: {tier: core,
  length_floor_words: 300}`, `dependencies.python: []`.
- **Availability:** under the 300-word floor → `available=False` + warning.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_reference_ecology_audit.py`)

- `test_task_surface_registered` — `reference_ecology` ∈ `VALID_TASK_SURFACES`.
- `test_envelope_shape` — payload validates; correct surface.
- `test_no_verdict_keys` — no `band`/`verdict`/`compression`.
- `test_claim_license_refuses_voice_and_flags_topic` — `does_not_license` names voice/authorship/AI; caveats mention topic.
- `test_parenthetical_citation_count` — `(Smith, 2019)` + `(Doe et al., 2020)` counted; a `(plain note)` not counted.
- `test_doi_arxiv_etal` — DOI / arXiv / et al. tallied.
- `test_footnote_refs_and_defs`.
- `test_attribution_constructions` — "according to Smith" + "Jones argues" counted.
- `test_quotation_pairs_and_blockquote`.
- `test_link_domain_breadth` — distinct-domain count from multiple URLs.
- `test_too_short_unavailable`.
- `test_deterministic`.

## Calibration posture

None to calibrate — descriptive. The claim-license names it a thematic/register
profile, not a signal with an operating point.

## Out of scope / non-goals

- No voice/authorship/AI inference; no quality judgment; no band/verdict.
- Not a citation *validator* (doesn't check that citations resolve).
- No baseline comparison in v1 (single-document descriptive). Baseline delta is a follow-up.
- Proper-noun "reference network" extraction is deferred (would want spaCy NER, which
  this stdlib v1 avoids); v1 stays on regex-detectable citation/quote/link structure.

## Open questions

- Whether to later add a spaCy-backed proper-noun reference network (separate, opt-in).
- Whether `reference_ecology` and `document_layout` should eventually share a parent
  "non-voice descriptive" surface family.
