# 04-pan-obfuscation-replay-harness

> Replay SETEC's existing signals against the public PAN@CLEF obfuscation fixtures
> (Unicode/homoglyph, paraphrase, language-switch, short-text) and report robustness
> per signal — extending the validation surface, not adding a detector.

- **Status:** Ready
- **Tier:** near-term
- **GPU required:** no
- **Upstream / prior art:** PAN@CLEF 2024/2025 Generative-AI Authorship Verification ([PAN24 task](https://pan.webis.de/clef24/pan24-web/generated-content-analysis.html)); complements the existing `adversarial_fixtures.py` + `adversarial_robustness_card.py`.
- **License decision:** N/A for code; **pre-build: confirm PAN fixture redistribution terms** before bundling any PAN-derived text (brief TODO). If terms block redistribution, ship a fetcher (PAN-account-gated, local-only) like `fetch_pangram_editlens.py` rather than vendoring fixtures.

## Motivation

The brief's #3 near-term, license-clean addition. PAN names the open problem as
**OOD generalization, short texts, and obfuscation robustness** — precisely SETEC's
adversarial territory. SETEC already has homoglyph/zero-width/paraphrase fixtures and
a per-signal robustness card; this capability *systematizes* that against the
standard public benchmark so SETEC can state, per signal, "stable under X, collapses
under Y" against the same fixtures the field uses.

## Method

1. Acquire PAN obfuscation variants (fetcher if redistribution is gated; otherwise a
   bundled slice under `scripts/test_data/`).
2. Run the surface-tagged signals over each (clean, obfuscated) pair using the
   existing `validation_harness.py` machinery.
3. Emit the per-signal **robustness card** (reusing `adversarial_robustness_card.py`):
   for each signal × obfuscation class, the delta and a stable/degraded/collapsed tag.
4. Refuse a single aggregate "robustness score."

Mostly orchestration over existing components; new code is the PAN adapter +
obfuscation-class slicing.

## Contract (the testable interface)

- **task_surface:** `validation` (existing).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/calibration/pan_replay.py --fixtures DIR [--classes unicode,paraphrase,lang_switch,short] [--signals ...] [--json] [--out PATH]`
- **JSON envelope:** per-signal × per-class deltas + tags; `ClaimLicense`. No aggregate
  verdict. Reuses the robustness-card shape.
- **Claim license:** licenses "signal S degrades by Δ under obfuscation class C on the
  PAN fixtures"; refuses any "detector accuracy" headline; states fixture provenance.
- **capabilities.yaml entry:** `id: pan_replay`, `surface: validation`,
  `status: empirically_oriented`, `handoff: internal`, `compute: {tier: core}`,
  `dependencies.python: []` (reuses harness optional deps), inputs = fixture dir.
- **Dependencies / footprint:** none new; CPU.

## Test contract (`.../tests/test_pan_replay.py`)

- `test_envelope_and_surface` — validates; `task_surface == "validation"`.
- `test_per_class_slicing` — output has independent per-(signal × class) entries; no
  cross-class mixing (mirrors the existing adversarial-class invariant).
- `test_refuses_aggregate_score` — no single robustness/accuracy number emitted.
- `test_robustness_card_reuse` — output conforms to `adversarial_robustness_card` shape.
- `test_missing_fixtures_clear_error`.
- `test_capabilities_entry_present`.

## Calibration posture

Descriptive/empirical; no thresholds. The deliverable is the robustness card, not a
score. Useful immediately for honesty claims and for prioritizing which signals need
hardening.

## Out of scope / non-goals

- Not a detector; not a leaderboard entry.
- Doesn't redistribute PAN data if terms forbid it — fetcher-gated instead.

## Open questions

- Bundle a small fixture slice vs. fetcher-only (depends on PAN terms — verify first).
- Which signal set to default to (all surface-tagged, or the Tier-1 + Surface-5 core).
