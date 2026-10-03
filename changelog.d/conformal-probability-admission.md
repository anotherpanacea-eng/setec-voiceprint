### Fixed

**`conformal_gate` — reject invalid probability parameters for direct callers.** Class gates and FPR threshold helpers require finite probabilities strictly between zero and one, including empty-calibration calls. Existing valid-input formulas and CLI behavior are preserved.
