### Added

**`argmove_profile.py` — deterministic argument-move population baseline
(ArgScope B3/B4 + AGD).** Aggregates the reused B4 stance + B3 abstraction
signals plus a net-new AGD marker module (assuring/guarding/discounting) over
a corpus into a population profile, and compares two corpora (Cliff's delta
primary) — the judge-free separation test that gates any B1/B2 LLM-judge
spend. New task surface `assertoric`; capability id `argmove_profile`;
`calibration_status: empirically_oriented` (no calibrated thresholds, no
verdict).
