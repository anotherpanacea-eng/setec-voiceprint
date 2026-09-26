# Exact pool homogeneity at bounded memory

Status: design review; production blocked on the representative contract below.
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

## Owner decision: what does a representative represent?

The roadmap and this work order require "distribution + representatives";
the existing executable contract has only the distribution and participation ratio.
Preserving representatives is impossible until their meaning is defined. In
particular participation ratio is **not a cluster count**, and supplies neither
cluster membership nor one representative per mode.

Proposed choice A: positional **pair witnesses** for the exact order statistics
used by min/p10/p50/p90. Emit both bracketing pairs for interpolated quantiles,
their values, interpolation weight and original admitted row ordinals; sort ties
by `(cosine, i, j)`. They illustrate the pair distribution, not latent modes or
text quality. They contain no excerpts, paths or user IDs. They remain private
run diagnostics; ordinal linkage is not anonymization. No "best" text is selected.

Alternative B: representatives of clusters or source/register strata. This needs
an additional partition/metadata contract and cannot be inferred from Spec30.
It is a separate scientific/product scope, not an implementation choice here.

Owner must accept A or supply B's contract before production work. Do not silently
omit the required representatives, invent clusters, or turn this into scalar-only
output. This draft recommends A but does not approve it on the owner's behalf.

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
eigenvalues. Zero spread gives 1, retain [1,N] clamp and numpy-absent null/warning
behavior. Mathematically nonzero spread has rank at most min(D,N-1), not N;
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
evidence must show the discrepancy explicitly. A production contract must state
the numerical compatibility domain and degenerate policy before replacing it.

For distribution fidelity, compute each pair with the existing `_cosine` operation
order (including clamping and zero semantics). Write sorted bounded runs of
binary64 values, optionally paired with i/j for approved witness choice A. Use
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
explicitly before allocation; no truncation. Resource exhaustion produces an
unavailable resource result, never a sampled result or partial success envelope.
Select the existing envelope category only after verifying the schema in the
implementation increment; do not add a reason enum opportunistically.

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

Before a production PR: owner representative decision; numerical edge policy;
fresh path claim; independent spec review; small fixtures for ties, duplicates,
zero vectors, orthonormal, rank-deficient and almost-identical rows; full CLI
synthetic equality/refusal checks; numpy-missing behavior; adversarial resume
identity/corruption/interruption tests at each phase; and a process-memory scale
test covering input/vocabulary/vector phases as well as pair reduction.
Require unchanged distribution fields and claim license, no verdict/band/
selection key, explicit duplicate accounting, and paired representative evidence.
Do not promote heuristic status or detector thresholds. Proximity mode and M2
lenses are outside this work. No merge, release or real proposal run is authorized.
