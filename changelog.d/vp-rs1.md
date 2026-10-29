### Added

**`period_ruler` — conformal period-register ruler (Chronologic design, VP-RS1).** A new
`period_register` surface that measures how far a set of generated passages, each written for a
target year, sits from real prose of those years. It ports the scoring layer of Chronologic-EN
(arXiv:2609.23178, MIT): every candidate becomes a percentile against real reference passages in
the same log-length bin and within ±10 years of its target year, on a date channel (residual of
the date model's mean year) and an authenticity channel (P(synthetic)). The set is summarised by
the W1 distance of its date percentiles from uniform and by its mean authenticity percentile, each
beside a noise floor of 2,000 leave-one-group-out pseudo-models drawn from the reference, with
0-100 `period_fidelity` / `authenticity_fidelity` scores and their null intervals. The real
instruments are the released `chronologic/chronologic-date-deberta` (T=1.0551) and
`chronologic-authenticity-deberta` checkpoints behind a lazy torch/transformers import; rows that
already carry scores, or a `--score-cache`, run model-free and the cache resumes an interrupted
scoring run. Validator only: not a reward, not a passage-level detector.
