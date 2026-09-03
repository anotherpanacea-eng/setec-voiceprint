### Changed

- Route `conformal_gate` from the `validation` skill as a score-only, exchangeability-bound abstention aid, including one-/two-class prediction sets and the one-tailed reference-class FPR ceiling rooted in Multiscaled Conformal Prediction ([arXiv:2505.05084](https://arxiv.org/abs/2505.05084)); this does not add a detector, verdict, or automatic gate.
- Preserve the existing nonconformity-space `threshold` field while adding an explicit raw-score threshold/comparator pair, so operators can safely apply both `higher_is_nonconforming` and `lower_is_nonconforming` results.
