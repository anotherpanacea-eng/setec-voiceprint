### Changed

**`conformal_gate` — count calibration authors.** Calibration lines may read `score,author`; when several texts share an author, `results.n_calibration_authors` reports the author count and a warning names the author as the exchangeable unit, says the pooled per-text calibration does not carry its guarantee for a new author, and notes when too few authors exist to express the chosen `fpr_bound`. Counting authors warns only; it does not change the calculation. A test pins n = 30, q = 0.05 to the calibration maximum and n below 20 to abstention.
