### Fixed

**`within_doc_segmentation` — Codex P1: zero-MAD guard + zero-norm cosine distance (within_doc_segmentation.py).** Two related bugs in the boundary-detection pipeline:

- **Zero-MAD guard** (`analyze_document`): when the within-document MAD of the distance profile is 0 (flat profile — e.g. a uniform document of N identical sentences), boundary detection is now skipped entirely. Previously, MAD==0 collapsed all thresholds to the median (also 0.0), causing every local plateau to satisfy `d_i >= T_moderate` (0.0 >= 0.0 = True) and be classified as `marked_shift`. A uniform document now correctly emits zero boundaries.

- **Zero-norm cosine distance** (`_adjacent_distance_profile` + new `_is_zero_norm` helper): when either feature-vector in an adjacent window pair has zero norm, the derived distance is now 0.0 (treat empty/zero vectors as identical — no shift), not 0.5. Previously, `_cosine_similarity` correctly returned 0.0 on zero-norm input, but the calling formula `(1 - 0.0) / 2 = 0.5` produced a spurious non-zero distance.

Regression test class `TestZeroMadAndZeroNormRegression` (6 tests) in `tests/test_within_doc_segmentation.py`: uniform-document → zero boundaries; no `marked_shift` at `distance==0.0`; zero-norm adjacent pair → distance 0.0 (not 0.5); both-zero-norm pair → distance 0.0.
