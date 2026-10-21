# SVP dependency inversion: non-text helpers

**Status:** closed 2026-10-14 after 13 units (last: train PR #788); the remaining exemption rows are out of scope · **Card:** CB-5 (Fleet #708) · **Repo:** setec-voiceprint

## Goal

Move shared non-text helpers out of surface modules into `setec.core`, then delete
the layering exemptions that only those helpers needed. That is one hub per PR, and
each PR is done when `tools/check_layering.py --strict` passes with fewer rows and
the suite is green. Text primitives stay out of scope; they are CAM-12's
(Claude-only).

## First unit: variance_audit threshold metadata

Lines are from main `606583a6`.

1. Create `plugins/setec-voiceprint/scripts/setec/core/compression_thresholds.py` (stdlib only), and move into it:
   - `THRESHOLD_STATUS_VALUES` (line 1927) and `ThresholdSpec` (1937);
   - `resolve_direction_with_slice` and `resolve_direction` (2099, 2157);
   - `COMPRESSION_HEURISTICS` (2177) and `POS_BIGRAM_KL_HEURISTIC` (2539);
   - the six status helpers from line 2427: `signals_by_status`,
     `calibrated_signals`, `literature_anchored_signals`,
     `empirically_oriented_signals`, `heuristic_signals` and `provisional_signals`.
2. `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py` re-exports the same objects, not copies, so
   in-place registry changes still reach classification.
3. Import `COMPRESSION_HEURISTICS` from core in
   `plugins/setec-voiceprint/scripts/calibration/calibrate_thresholds.py`, `plugins/setec-voiceprint/scripts/calibration/calibration_survey.py` and
   `plugins/setec-voiceprint/scripts/setec/surfaces/before_after_restoration.py`.
   - In `calibrate_thresholds`, replace its lazy `__getattr__` alias with a plain
     import. The laziness only kept heavy `variance_audit` out of plain import,
     and core is stdlib.
   - Fix its "edit scripts/variance_audit.py" operator message (line 3097).
4. Delete the three `l2_to_l2` exemption rows from those files to `variance_audit.py`.
5. If `normalize_pos_bigram_counts` and `pos_bigram_kl_contributions` (lines 412 and
   437) are plain dict math, move them too and retarget `manuscript_bigram_diff`.
   That deletes its row. After this unit, `variance_audit`'s non-text work is done.
   What remains there needs text primitives.

No signature, default, registry order, threshold or status changes.

**Tests:**
- add one identity check, that `variance_audit.X is compression_thresholds.X` for
  the moved names;
- keep existing threshold, routing, polarity and contract-fixture tests green.

## Later units

`acquisition_core`, `manifest_validator`, `validation_harness`, then the remaining
hubs, one per PR. Each follows the same pattern and the same done-check.
