# Exact pool homogeneity at bounded memory

Status: owner choices recorded; precise numerical proposal independently reviewed,
with policy adoption and production authorization still pending.
Tier: COMPLEX. Author: Codex (runtime family GPT-6; no inferred submodel name).
Scope: ROADMAP, "Corpus-scale and registry-contract gaps", homogeneity only.
Base: `780cab5650309b21fe999b5d5ea4d51a80c4f199`.

## Verified starting point

At this base, `plugins/setec-voiceprint/scripts/homogeneity_audit.py`:

- `_load_manifest` and `_load_dir` retain all texts. `build_vocabulary` retains
  all observed n-grams before selecting 200 per family. A blocked pair loop alone
  therefore does **not** make the complete CLI bounded in memory.
- `pairwise_cosines` retains N(N-1)/2 Python floats; `cosine_distribution`
  sorts a second list, uses sample standard deviation and linearly interpolated
  p10/p50/p90. There is no histogram or emitted raw pair list.
- `effective_modes` normalizes rows, centers, constructs an N by N Gram matrix,
  clips its computed eigenvalues at zero and computes their participation ratio.
- `audit_pool` emits n_texts, lens, distribution, mean, effective_modes and
  assumptions. Neither it nor Spec30 defines a representative or a cluster.
- `_run_pool` refuses passage-dedup-marked manifests through `pool_guard`.
  That guard itself reads/splits the whole manifest and retains every marked
  label: it too must change before end-to-end bounded-memory claims are valid.
  Duplicate texts otherwise retain their multiplicity. Too-short rows are filtered
  before min_set; no scale refusal is implemented here. The roadmap's historical
  scale refusal was operational, not a shipped automatic resource guard.

The fleet survey/pipeline and inventory supplement were checked alongside current
producer PRs 485/487/488/489, branch inventory and homogeneity commit history.
No competing scalable homogeneity implementation was found in those surfaces.
This is a bounded search, not proof about every private experiment. The standalone
fleet inventory script was unavailable at the local fleet-root location; direct
current capability/source inventory is the fallback. ROADMAP has a foreign live
claim and is intentionally unchanged.

## Approved owner decisions (2026-09-26)

The owner explicitly approved (1) choice A positional pair witnesses and (2) a
separately versioned, explicitly opt-in scalable arithmetic method while retaining
the legacy method for reproducibility. Approval did not select any numerical
cutoff or license unreliable scalars. The explanation accompanying approval named
the all-pairs memory problem and the substantial rounding-order effect for almost
identical inputs. This is a decision-record update, not production authorization.
See [NUMERICAL-CONTRACT.md](NUMERICAL-CONTRACT.md) for the proposed reliability,
degenerate-input and method-version contract; its unapproved mechanics are explicit.

## What the approved representatives represent

The roadmap and this work order require "distribution + representatives";
the existing executable contract has only the distribution and participation ratio.
The approved additive meaning resolves this gap. In particular participation
ratio is **not a cluster count**, and supplies neither
cluster membership nor one representative per mode.

Approved choice A: positional **pair witnesses** for the exact order statistics
used by min/p10/p50/p90. Emit both bracketing pairs for interpolated quantiles,
their values, interpolation weight and original admitted row ordinals; sort ties
by `(cosine, i, j)`. They illustrate the pair distribution, not latent modes or
text quality. They contain no excerpts, paths or user IDs. They remain private
run diagnostics; ordinal linkage is not anonymization. No "best" text is selected.

Excluded alternative B: representatives of clusters or source/register strata need
an additional partition/metadata contract and cannot be inferred from Spec30.
It is a separate scientific/product scope, not an implementation choice here.

Do not omit the approved witnesses, invent clusters, or turn this into scalar-only
output. Their appearance is restricted to the explicit new method; legacy results
remain unchanged. Private ordinal linkage is not public/anonymized diagnostics.

## Exact arithmetic design

"Exact" means every admitted unordered pair, without sampling, sketches, binning
or a substitute estimator. It does not mean bit-identical cross-platform LAPACK
eigenvalue rounding. Keep the old path as a small-matrix oracle in tests.

Let U contain the unit rows (zero rows stay zero), X = U - mean(U), and G = XX^T.
G is positive semidefinite in real arithmetic. Therefore

`sum(lambda) = tr(G) = sum_i ||X_i||^2`, and
`sum(lambda^2) = tr(G^2) = ||G||_F^2 = ||X^T X||_F^2`.

The exact mathematical participation ratio is thus `tr(X^T X)^2 / ||X^T X||_F^2`.
Compute X in a second pass, after accumulating the mean, and accumulate X^T X
in feature space. Do not use subtraction of raw second moments: cancellation
near identical rows can destroy the spread. Width D is at most the fixed
function-word family plus 600 selected n-grams. Memory O(D^2 + BD); time O(ND^2).
Alternatively compute centered Gram tiles with O(BD+B^2) memory and O(N^2 D)
time; this avoids D^2 storage at the cost of quadratic work. Neither requires
eigenvalues. This algebra motivated the initial floating-point feasibility probe;
it is not the reliability contract for a production implementation. Legacy keeps
its zero-spread 1, [1,N] clamp and numpy-absent null/warning behavior. The proposed
new certified method is defined in NUMERICAL-CONTRACT and can withhold a scalar.
Mathematically nonzero spread has rank at most min(D,N-1), not N;
an orthonormal N-row pool centers to N-1 modes.

Clipping negative *computed* eigenvalues is not a real-arithmetic definition.
The feature-space/blocked formulas will differ from that implementation at
roundoff level and potentially severely when spread is at rounding noise scale.
In particular, changing the order of mean accumulation changes the centered
matrix itself; this can dominate eigenvalue clipping. The probe records a
97-by-11 near-identical positive-row counterexample with a blocked mean, beside
well-conditioned agreement. Its fixed-mean cases test a narrower identity.
No universal relative-error promise is made near zero spread; do not invent a
noise threshold or claim that six-decimal rounding guarantees equality. Synthetic
evidence must show the discrepancy explicitly. The approved new method will not
replace legacy arithmetic. Its numerical target, enclosure rule and proposed
degenerate policy are specified separately for review before implementation.

For distribution fidelity, compute each pair with the existing `_cosine` operation
order (including clamping and zero semantics). Write sorted bounded runs of
binary64 values paired with i/j for approved witness choice A in the new method. Use
fixed-fan-in external merge passes, never one open file per run without a cap.
All pairs remain present; exact rank retrieval reproduces the existing linear
quantile expression. Accumulate exact rational sums of the binary64 observations
and their squares (integer-ratio accumulators), then use the same mean/sample-SD
rounding as the supported Python statistics implementation. Do not replace this
with sum-of-squares subtraction in ordinary float arithmetic.

## End-to-end storage and recovery proposal

Proposed operational bounds (not detector thresholds): 4096 pair records per
sort run, merge fan-in 16, 64 rows per vector block, 8 MiB input record/text cap.
These are design defaults, not owner-selected settings. Refuse oversize inputs
explicitly before allocation; no truncation. Input/pair-storage failures produce
an unavailable result, never a sampled or falsely completed pair distribution.
The new numeric contract has one explicit partial-metric exception: after all
pairs/witnesses complete, declared numeric budget exhaustion withholds modes
with a warning and state, without withholding the complete pair distribution.
The verified existing-category mapping is now specified in NUMERICAL-CONTRACT;
do not add a resource enum or bypass the bounds gate.

1. Stream inputs in the existing order, snapshot admitted text to operator-owned
   local scratch, retaining original ordinals and duplicate occurrences. Apply
   the same decoding, precedence, word floor and passage-dedup refusal. Replace
   the guard's read_text/splitlines/list accumulation with a bounded scan keeping
   total marked count and first five labels, preserving its refusal diagnostics.
   Snapshot and guard must share identical bytes, not reread mutable originals.
   A bounded
   directory-order external sort replaces materializing all path names. Manifest
   line parsing and a single document are bounded by the explicit cap.
2. Preserve current vocabulary semantics, including sums of per-document
   n-gram frequencies in input order, then frequency/lexical tie-breaking.
   Use disk-backed key totals with a bounded page cache; do not normalize counts
   globally or change top-200 pruning. Feature extraction is bounded by document
   cap. Freeze the ordered vocabulary before projecting any vector.
3. Persist vectors as fixed-width binary64 rows; no whole-corpus ndarray/list.
   Iterate canonical i<j pair ranges in bounded units, preserving all duplicates.
   Zero-vector pair handling stays identical to `_cosine`.
4. Checkpoint directory ordering runs/merge groups, input snapshot progress
   (bounded record units), disk-backed
   vocabulary updates transactionally with their last admitted ordinal, vector
   blocks, pair-run publication, each external merge group, each mean/centered
   moment block and final rank scan. Progress goes to stderr/disk; stdout remains
   the final envelope. Resume cannot mean restarting an unbounded sort or scan.
5. Bind resume to canonical length-prefixed identity: schema/algorithm version,
   source snapshot bytes and ordered occurrences (not file timestamps), parser,
   feature-code/version and vocabulary, min_set/length floor, numeric dtype,
   Python/numpy backend identity, resource configuration and witness policy.
   Changing contents, row order, duplicate count, settings or backend refuses reuse.
6. Publish a checkpoint only after flushing payload and verifying length/count/
   checksum; atomically replace a small manifest. On recovery validate version,
   schema, all referenced artifacts, pair-range coverage without holes/overlaps,
   counts and identity before use. Torn/unreferenced staging files are not work
   completed. Fail closed on corruption; never silently mix fresh/stale blocks.
   An exclusive cache writer claim prevents concurrent publishers. Portable
   Windows/POSIX durability and no-clobber behavior need separate acceptance.

Space is necessarily O(ND + N^2) **on disk** for this external-sort design, plus
snapshot text and vocabulary. At N=10,000, P=49,995,000; values alone need
399,960,000 bytes, pair ordinals add storage, and merge input/output coexist.
Preflight scratch headroom for the actual format and all live merge generations;
quota exhaustion remains possible after preflight and must refuse safely.
Runtime remains quadratic for exact all-pairs cosines. Bounded RAM is not a
promise of fast, cheap or unlimited-size execution.

## Acceptance and increment boundary

This draft delivers design plus a synthetic feasibility probe, not the above
production pipeline. Its probe covers exact external quantiles, bounded merge
fan-in, and mathematical modes equivalence against the shipped small oracle.
It must label unimplemented parser/checkpoint/resume/representative behavior.
The scale experiment must process millions of actual synthetic pairs without
holding their values or Gram matrix in RAM, and report peak memory and disk use.
It must not call any embedding model, private corpus or provider.

Before a production PR: adopt the reviewed numerical mechanics (owner choices A
and method separation are already approved); explicit production authorization;
fresh path claim; independent spec review; small fixtures for ties, duplicates,
zero vectors, orthonormal, rank-deficient and almost-identical rows; full CLI
synthetic equality/refusal checks; legacy numpy-missing behavior; adversarial resume
identity/corruption/interruption tests at each phase; and a process-memory scale
test covering input/vocabulary/vector phases as well as pair reduction.
Require unchanged distribution fields and legacy claim license; qualify the new
method's arithmetic target in its claim license while preserving no-verdict
posture. No verdict/band/
selection key, explicit duplicate accounting, and paired representative evidence.
Do not promote heuristic status or detector thresholds. Proximity mode and M2
lenses are outside this work. Claude counter-review is pending and required before
new builds proceed to integration; Codex-only reviews do not satisfy it. No merge,
release or real proposal run is authorized.
