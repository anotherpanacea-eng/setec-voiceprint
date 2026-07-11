### Added

**`manifest_validator.py` — Ratchet 6: warn on `ai_status: pre_ai_human` with `era: post_ai_widespread`, for any `corpus_role`.**

- A `pre_ai_human` claim on post-2024 material is unverifiable and, if wrong,
  teaches the framework's ground-truth human baseline that AI-assisted prose is
  a writer's own unassisted voice — the exact failure the validator's module
  docstring already warns about. The existing Ratchet 4 covers only
  `corpus_role: impostor` and carries no `ai_status` term, leaving
  `identity_baseline` entries (a writer's own corpus, the ground-truth anchor)
  entirely unguarded against this mismatch.
- Ratchet 6 is a **new, additive** check — Ratchet 4 is untouched. The two
  compose: an `impostor` + `pre_ai_human` + `post_ai_widespread` entry now trips
  both. `warning` severity, consistent with Ratchet 4.
- **Warning surface, intentional and disclosed:** `acquire_manuscript.py`'s
  `--ai-status` defaults to `pre_ai_human`, but its `--era` defaults to `undated`;
  it does not derive era from `date_written`. Manuscript entries therefore newly
  warn only when the operator explicitly supplies `--era post_ai_widespread` (or
  an existing manifest entry already carries that era). This is a warning, not a
  validation failure — nothing breaks mechanically.
- Per `internal/2026-07-09-manifest-validator-ai-status-era-ratchet-spec.md`.
  Five fixtures in `tests/test_ratchet6_ai_status_era.py` cover: Ratchet 6 firing
  on `identity_baseline`; a correctly-tagged `ai_status: unknown` post-AI entry
  validating clean; Ratchet 4 still firing unchanged (no regression); the
  manuscript-default surface; and the impostor co-fire case proving the two
  ratchets compose rather than one masking the other.
