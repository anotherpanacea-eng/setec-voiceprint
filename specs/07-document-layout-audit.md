# 07-document-layout-audit

> A **non-voice** structural/formatting profile of a document: heading cadence,
> section-length variance, list/blockquote/code/table usage, link density. Ships
> as its own small audit under a new `document_layout` task surface, with
> claim-licensing that explicitly refuses any voice/authorship/AI inference.

- **Status:** Ready → building (this PR).
- **Tier:** Tier 5 "adjacent surface" (Stylometric surface expansion → ship as a non-voice surface with explicit claim-language guards).
- **GPU required:** no — stdlib only (`re`, `statistics`).
- **Upstream / prior art:** ROADMAP "Tier 5 — Adjacent surfaces (ship under different framing) → Document Structure / Layout Audit."
- **License decision:** N/A — all local code.

## Motivation

The shipped suite measures voice and distributional compression. It has no
surface for **publishing-format structure** — how a document is laid out
(headings, lists, sections, links). That's genuinely useful for blog / Substack /
policy / memo / newsletter workflows where formatting *is* part of the working
style, but it is **not stylometry**: it's a format fingerprint that changes with
medium and template, not with authorial voice. The roadmap is explicit that it
must ship as a **non-voice** audit with claim-licensing that refuses voice
attribution. This spec honors that: the audit reports measurements only, no band,
no verdict, no voice/AI inference.

**Orthogonality:** distinct from `paragraph_audit` (paragraph *rhythm*) — this
operates at the document-structure layer (headings/sections/lists/links), which no
existing surface measures.

## Method

Operate on the **raw** text (not `strip_non_prose` — formatting is the subject).
Stdlib regex over Markdown/plain structure:

- **Headings** — ATX `#`…`######`: total count, per-1k-word rate, level
  distribution, max depth, distinct levels.
- **Sections** — text spans between headings: count, word-count mean / sd /
  coefficient of variation (the "uniform sections" signal).
- **Lists** — unordered items (`-`/`*`/`+`), ordered items (`1.`/`1)`),
  per-1k-word rate, bullet-marker distribution, list-block count.
- **Blockquotes** — `>` line count + rate.
- **Fenced code blocks** — ```` ``` ```` fence pairs.
- **Links** — Markdown `[...](...)` + bare `http(s)://` URLs; link density / 1k.
- **Thematic breaks** — `---` / `***` horizontal rules.
- **Tables** — pipe-table row count.

All descriptive. No thresholds, no banding.

## Contract (the testable interface)

- **task_surface:** new value `document_layout` — added to
  `output_schema.VALID_TASK_SURFACES` and `claim_license.TASK_SURFACE_LABELS`
  ("document structure / layout profile"). Additive; the surface-parity test
  asserts only a subset, so this is safe.
- **CLI:** `python3 plugins/setec-voiceprint/scripts/document_layout_audit.py INPUT[.md|.txt] [--json] [--out PATH]`. Output path is explicit only (no auto-derived stem → no output-collision risk).
- **JSON envelope:** `output_schema.build_output(task_surface="document_layout", …)`.
  `results` keys: `headings`, `sections`, `lists`, `blockquotes`, `code_blocks`,
  `links`, `thematic_breaks`, `tables`. Carries a `ClaimLicense` block. **No**
  `band`/`verdict`/`compression` key.
- **Claim license:** *licenses* "the document's structural / formatting profile
  (heading cadence, section-length variance, list / link / blockquote / code /
  table usage)"; *refuses* voice, authorship, AI-provenance, and
  quality/"better-written" inferences. Caveat: a publishing-format fingerprint is
  topic- and medium-bound, not stylometry.
- **capabilities.yaml entry:** `id: document_layout_audit`, `surface:
  document_layout`, `status: heuristic` (descriptive; no thresholds to calibrate),
  `handoff: none`, `compute: {tier: core, length_floor_words: 300}`,
  `dependencies.python: []`.
- **Availability:** under the 300-word floor → `available=False` + a warning;
  `results={}`.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_document_layout_audit.py`)

- `test_task_surface_registered` — `document_layout` ∈ `VALID_TASK_SURFACES`.
- `test_envelope_shape` — payload validates; `task_surface == "document_layout"`.
- `test_no_verdict_keys` — `results` has no `band`/`verdict`/`compression`.
- `test_claim_license_refuses_voice` — `does_not_license` names voice/authorship/AI.
- `test_heading_and_level_counts` — fixture with 3 headings at 2 levels.
- `test_section_length_variance` — multi-section fixture yields mean/sd/cv.
- `test_list_and_bullet_detection` — unordered + ordered items counted; bullet markers tallied.
- `test_link_density` — Markdown link + bare URL both counted.
- `test_too_short_unavailable` — sub-floor text → `available=False` + warning.
- `test_deterministic` — same input twice → identical results.

## Calibration posture

None to calibrate — purely descriptive. The claim-license names it a format
profile, not a signal with an operating point.

## Out of scope / non-goals

- No voice/authorship/AI inference; no quality judgment; no band/verdict.
- No baseline comparison in v1 (single-document descriptive profile). Baseline
  delta is a clean follow-up.
- Not a Markdown linter — it measures structure, doesn't validate it.

## Open questions

- Whether to add a baseline-dir comparison (per-signal deltas) in a follow-up.
- Whether `document_layout` should later host sibling non-voice surfaces
  (reference-ecology, stockness) or each gets its own surface.
