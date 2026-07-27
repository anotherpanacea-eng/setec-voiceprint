### Added

- Add `narrative_decision_long_form` (spec 77 M1): deterministic segmentation
  of works above the narrative-decision audit's 25,000-word ceiling
  (chapter/scene/blank-line/paragraph tiers, greedy packing, CRLF-tolerant,
  fewest-excluded-words tier selection) plus per-segment scoring through the
  unchanged base audit, with a composite judge-identity cache key and a
  no-reduction emit guard. Every work-level aggregate ships suppressed
  (`provisional_unvalidated`) until a spec 77 M2 validation receipt exists;
  the judge-free agreement/verdict-derivation calibration script
  (`narrative_longform_agreement.py`) that will produce such receipts ships
  alongside, with tamper-refusing receipt verification. Anchored to Russell
  et al. 2026 (StoryScope, arXiv:2604.03136v4).
