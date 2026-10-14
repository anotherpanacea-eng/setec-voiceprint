### Changed

**Zero-install consumer case for `general_imposters` (CB-6).**
The gate writes a tiny synthetic corpus. Two of the baseline files are the
candidate's identity baseline, and the third plus four short texts are five
impostor writers, the harness's minimum. `general_imposters` then runs its
seeded 100-iteration bootstrap on the shared input, and the case pins the
envelope. The ID leaves the pending list.
