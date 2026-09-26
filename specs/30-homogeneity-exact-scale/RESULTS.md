# Synthetic evidence and disposition

2026-09-26; base `780cab5650309b21fe999b5d5ea4d51a80c4f199`.
This is a reviewed design/feasibility delivery, not a shipped scalable audit.

## Outcome

Exact external pair reduction is feasible with small memory. Production work is
blocked on two concrete choices:

1. Accept positional pair witnesses (DESIGN choice A), or define the partition
   and semantics for another kind of representative. Existing Spec30 has none.
2. Define numerical compatibility for `effective_modes` near zero spread. The
   real-arithmetic identity is exact, but a different reduction order changes
   the represented centered matrix enough to change the result dramatically.
   One possible follow-up is a separately versioned opt-in arithmetic contract;
   another is retaining the legacy numerical path and its resource limits.
   Neither is silently selected by this draft. No noise floor is introduced.

No production files changed; no implicit sampling, deduplication, threshold,
verdict, selector, private corpus, model execution, API spend, merge or release.

## Reproduction and measurements

From the repository root with Python 3.12 and numpy installed:

```text
python specs/30-homogeneity-exact-scale/probe.py --scratch <new-local-directory>
python specs/30-homogeneity-exact-scale/probe.py --small-only --scratch <another-new-directory>
```

The probe accepts only synthetic sizes, not a corpus. Scratch must be new; pair
files remain there for inspection. They are experiment artifacts, not resumable
checkpoints. It deletes only intermediate pair files it creates itself.

Receipts: [scale-receipt.json](scale-receipt.json) and
[small-receipt.json](small-receipt.json). Python 3.12.0; numpy 2.4.4; Windows.
The scale invocation started before adding the eighth, multipass small case;
the subsequent `--small-only` invocation tested that final addition. Scale and
allocation code were unchanged between these two invocations.

Eight small cases match all seven distribution fields **exactly** against the
shipped list/sort oracle: varied, multipass, identical, zero, mixed-zero,
orthonormal, rank-one and near-identical rows. Fixed-normalization/mean
feature-moment ratios agree within 1e-10; the largest observed absolute
difference is 7.11e-15. The 370-row multipass fixture has 68,265 pairs, 17 initial
runs and two merge generations. A separate reviewer independently reproduced
its exact seven-field equality.

| Synthetic rows | Actual pairs | Traced reduction peak | Peak live pair-file bytes | Timed reduction |
|---|---:|---:|---:|---:|
| 512 | 130,816 | 1,439,398 B | 2,093,056 B | 3.68 s |
| 2,048 | 2,096,128 | 1,445,478 B | 33,538,048 B | 58.64 s |

These timings include tracemalloc and are not throughput promises. Three-coordinate
synthetic vectors underexercise production feature width and document extraction.
The complete process peak is approximately **734–735 MB**, mostly preexisting
audit imports (~733 MB peak before reduction); this is not a 1.5 MB process.
The allocation gate starts tracing after imports because subtracting earlier
process high-water peaks can conceal later allocations. For this pure-Python/
array reduction, traced memory must remain below 24 MiB. A real dense control
list with 2,096,128 floats used 67,432,968 traced bytes and exceeded that ceiling.
The cap is an experiment resource assertion, never a detector threshold.

The external-sort memory bound is O(F*R), with F=16 input streams, R=4096 doubles
per buffer, plus output buffer and heap heads; no list of all run paths is kept.
Disk remains quadratic. Scale verifies pair count and resource behavior; exact
oracle comparison is confined to the eight small cases. The moment prototype
materializes only small synthetic matrices and does **not** establish scalable
moments/preprocessing/resume behavior.

## Numerical counterexample

Positive 97-by-11 rows: baseline .25, numpy generator seed 8, sequential normal
perturbations. Change full-array mean accumulation to 16-row block accumulation:

| Perturbation | Shipped eigenvalue oracle | Blocked-mean feature moments |
|---|---:|---:|
| 1e-8 | 9.050424495132468 | 9.050424495132457 |
| 1e-15 | 9.04657159577848 | 9.027800784766 |
| 1e-16 | 1.085293597991339 | 9.360408119618224 |

This is an observed limitation, not a passing equivalence test or a new diagnostic
cut. Centering order is consequential at machine precision; exact trace identities
do not ensure compatibility with a previously rounded centered matrix. The
zero-perturbation case also retains the legacy's tiny rounding residual. A universal
six-decimal or relative-tolerance preservation claim would be false.

## Validation and review limits

- Existing `test_homogeneity_audit.py`: **25 passed**, two deprecation warnings.
- Docs freshness and capability drift gates passed; 274 scripts/142 entries.
- Both probe invocations exited zero; the allocation gate passed.
- This Python environment prints an ignored `multiprocess.resource_tracker`
  destructor `AttributeError` on shutdown, also observed by the independent
  numerical reviewer and the existing test run. Do not describe stderr as clean.
- No full repository suite, hosted CI or integration-train clearance is claimed.

Independent spec review approved the design-only disposition after adding a
bounded passage-dedup pre-scan and preprocessing checkpoints. Independent generic
review cleared the probe after adding multipass oracle coverage and the corrected
allocation gate. Fleet-posture review is recorded in the delivery PR/claim.

The production acceptance matrix in DESIGN remains future work: bounded loading,
vocabulary and vector storage, private witness projection, checkpoint identity,
interruption/corruption tests, numpy-absent behavior and full CLI refusal equality
have **not** been implemented or accepted by this experiment.
