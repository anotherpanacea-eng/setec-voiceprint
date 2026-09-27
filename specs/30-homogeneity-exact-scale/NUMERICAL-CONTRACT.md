# Opt-in numerical contract proposal

Status: independently reviewed proposal; policy adoption and production authorization pending.
Decision record: owner approved positional pair witnesses and a separately
versioned, explicitly opt-in scalable arithmetic method on 2026-09-26. The owner
did **not** choose a numerical cutoff, precision budget or degenerate policy.
Those proposed mechanics are distinguished below. Claude counter-review remains
pending; Codex review does not replace that cross-provider gate.

## Method boundary

Proposed selector: `--pool-method legacy | certified-exact-v1`, default `legacy`.
The selector is a design name, not an existing CLI flag. Before allocating a
schema name, the implementation increment must recheck current registrations.
Source search on the current candidate found no existing pool_method,
certified-exact-v1, pair_witnesses or spread_energy_interval implementation.

`legacy` preserves the existing path and its output, optional-numpy behavior and
resource limits. No automatic promotion, fallback or resume reuse between methods.
The new selector is pool-only; use with proximity arguments is a bad-input refusal.
Exact reproducibility of old outputs still requires their original input order,
versions/backend and environment; retaining the method does not make LAPACK
bit-identical across platforms. No new method metadata is inserted into legacy
results merely by adding the opt-in method.

For `certified-exact-v1`, all admitted unordered pairs remain present. The existing
seven distribution fields and headline mean retain `_cosine`'s binary64 operation
order, zero-vector handling and quantile interpolation. No sample/histogram replaces
the distribution. The new modes computation instead targets the exact real
normalization and centering of the **recorded binary64 feature coordinates**.
This distinction is explicit in method metadata and the claim license. It is a
new arithmetic definition, not a claim to reproduce the legacy centered matrix.
Vocabulary/feature generation stays as specified in DESIGN; its rounded stored
coordinates define the input. No confidence claim about unrounded frequencies,
author identity, corpus sampling or latent clusters follows from certification.

The existing probe is **not** an implementation of this certificate. Its blocked
floating moments motivated this proposal; its small and scale receipts cannot
validate the new precision/degenerate rules below.

## Mathematical target and zero spread

Each finite binary64 coordinate is an exact rational input. For row a_i, define
u_i = a_i/sqrt(sum_k a_ik^2), or the zero row when all coordinates are exactly zero.
With mu = mean_i(u_i), C = sum_i (u_i-mu)(u_i-mu)^T, define T=tr(C), S=sum_jk C_jk^2.
When T>0, r=T^2/S lies in [1,min(D,N-1)]. This is a spectral participation ratio,
not a number of discovered modes, clusters or sources. The legacy public key name
`effective_modes` is retained only with this qualification and diagnostic block.
Reject nonfinite coordinates, inconsistent widths and an empty width before
numeric work; never coerce, drop or fill such coordinates. Numeric primitives
need only integer/rational arithmetic; numpy is not required by the new
certificate. This does not waive validation of existing feature-import seams.

Classify exact zero spread **before** approximate interval reduction. All-zero
rows have zero spread. Otherwise every row must be nonzero and an exact positive
multiple of the first row. Test proportionality by cross-products of exact
rational coordinates, using a nonzero pivot and a positive pivot ratio; zero
coordinates must also agree. This is O(ND) checks, bounded-row streaming, no
epsilon. A zero/nonzero mixture is not zero spread. Signed opposite rows are
not equal directions. Duplicate occurrences are never removed.

For exact zero spread, the ratio is 0/0 and is undefined. New-method output:
`effective_modes: null`, `numerics.state: "zero_spread"`, and exact spread energy
interval [0,0]. Do **not** fabricate one cluster or silently copy legacy's 1.0
convention. Legacy still returns its existing value. This null policy is an
author proposal requiring the review/adoption gate below, not an owner decision
already made. N<min_set and invalid inputs still refuse the whole pool first.

## Arithmetic certificate, not a heuristic tolerance

Proposed reference arithmetic uses outward-rounded dyadic intervals with grid
2^-p. Here p counts fractional grid bits (absolute resolution), not significant
bits. Start at p=128; retry unresolved work at 256,512,1024,2048,4096 bits. These
are author-proposed compute budgets, **not** data-dependent detector/noise cutoffs.
Arbitrary-precision integers represent endpoints; no BLAS reduction supplies a
certificate. Faster kernels are admissible only if they enclose this same target
with independently validated rigorous bounds. Resource usage at the largest
precision must be preflighted honestly; smaller allowed resource budgets may
produce an unresolved result, never a weaker certification rule.

This reference algorithm has O(ND^2) interval operations per precision level,
O(D^2+BD) live intervals and integer bit costs that grow with p, log(N) and
binary64 exponent headroom.
These are not ordinary binary64 BLAS timings or byte counts. For roughly 700
features and corpus-scale N, multi-precision covariance can be expensive even
though memory is bounded. No performance feasibility receipt exists yet. A
later synthetic full-width/cap-precision resource study is an implementation
gate; it may require a smaller increment or a reviewed accelerated enclosure
kernel. Do not deploy uncertified fast moments when this reference is too slow.
The 4096-bit cap is not a universal completion guarantee: rows [1,0] and
[1,2^-1074] have positive exact spread but squared covariance energy on the
order of 2^-4298, below that grid. Returning unresolved_precision is correct;
the cap is not permission to round spread down to zero or choose a scalar.

Primitive rules sufficient for a reference implementation:

- Enclose each exact input rational by floor/ceiling multiples of 2^-p.
- Addition/subtraction take endpoint sums/differences and enclose outward.
- Multiplication takes min/max of all four endpoint products and encloses outward.
  Squaring uses zero as its lower endpoint if the input spans zero; otherwise
  min of endpoint squares. This matters for covariance diagonals and S.
- Division is allowed only when the entire denominator interval is positive;
  use endpoint quotient extrema and enclose outward. Otherwise refine or withhold.
- For nonnegative x=A*2^-p, bound sqrt(x) on the same grid using the integer
  square root of A*2^p; upper endpoint is the same only for a perfect square,
  otherwise the next grid point. Apply to each nonnegative endpoint. Clamp a
  square/sum-of-squares lower bound to zero only by its proven nonnegativity.

At each precision, stream row norm/normalization intervals and sum each coordinate
in manifest order; divide by exact N to enclose mu. In a second pass center the
same normalized intervals and accumulate feature-space C. Sum diagonal squares
directly for C_jj; off-diagonal products may have either sign. Form T from diagonal
intervals and S from squared C entries, counting both off-diagonal entries.
The enclosure includes normalization, mean, centering and moment rounding; a
certificate for just the final division is insufficient. Precision-specific
checkpoints store interval endpoint integers and exact precision/version/phase;
never reinterpret a low-precision block as a high-precision one.

Require T_lower>0 and S_lower>0 before forming a ratio. Bound it by
`[T_lower^2 / S_upper, T_upper^2 / S_lower]`, outward rounded, and intersect with
the mathematical [1,min(D,N-1)] bound. An empty intersection or inconsistent
interval is an internal-integrity refusal, not an invitation to clamp a point
estimate. An unresolved positivity check triggers higher precision, not zero spread.

Certify the **six-decimal value only** when exact nearest-even rounding of both
ratio endpoints at six decimal places gives the same integer multiple of 10^-6.
Monotonicity then proves every enclosed value has that same rounding. This is a
display-accuracy rule, not a homogeneity or spread threshold. An interval straddling
a rounding boundary may remain unresolved even when very narrow. Do not weaken
the rule, choose its midpoint or inherit a legacy scalar in that case.

On exhausting precision or an operator's declared numeric resource budget:
`effective_modes: null`, `numerics.state: "unresolved_precision"`, descriptive
warning and last valid enclosures when available. The full distribution plus
witnesses still ships if their computation completed and all input/custody guards
passed. This is an explicitly partial metric result, analogous in shape to the
legacy optional-dependency null, never a completed effective-modes measurement.
Malformed admitted numeric data, corrupted checkpoints or incomplete pairs still refuse the whole
result. Do not reclassify such failures as benign numerical unavailability.
"Last valid" means a **completed full-pool mean/centered-moment pass** at a named
precision, with validated identity and complete row coverage. An interrupted
covariance accumulator never bounds the full pool and is never emitted as a
spread/ratio enclosure. If a 128-bit pass completes but a 256-bit retry stops,
report only the completed 128-bit enclosures, precision_bits=128 and
last_attempted_precision_bits=256. If no full pass completes, both enclosures
and precision_bits are null. Numeric budget exhaustion is recognized at bounded
work boundaries; a crash/corrupt accumulator is not converted into this result.

Use the existing envelope categories verified in `output_schema.REASON_CATEGORIES`:
malformed admitted numeric input/arguments and impossible declared resource configurations are
`bad_input`; corrupt/inconsistent checkpoints, impossible enclosure state and
unexpected storage/computation failures are `internal_error`. No new resource
enum or bounds-gate bypass. Numeric-only declared budget exhaustion is the narrow
partial-null case above; it must have completed pair evidence. Missing required
runtime dependencies remain `missing_dependency`. Passage-dedup and set-floor
refusals retain the existing `bad_input` behavior. Refusal envelopes never carry
a falsely completed scalar.
Preserve existing manifest admission semantics: malformed JSON/non-object rows
and missing referenced files that the current loader skips remain skipped with
its existing diagnostics. The numeric/custody failure rules above do not silently
make those skipped rows fatal. Record input occurrence ordinals before admission
so skips cannot renumber witness identities.

## Proposed additive result contract (new method only)

Keep existing pool keys. Add `pool_method: "certified-exact-v1"` and:

`numerics`: `state` (certified/zero_spread/unresolved_precision),
`target` (exact-real unit-normalized recorded binary64 features), `precision_bits`,
`last_attempted_precision_bits`,
`rounding` (nearest-even, six decimal places), `spread_energy_interval`,
`participation_ratio_interval` (null when not valid), and `limitations` stating
certification covers recorded-coordinate arithmetic only. Encode nonnegative
interval endpoints as exact `{"integer": "...", "exponent2": -p}` objects;
never round an enclosure inward for JSON. A zero endpoint has integer "0".
Enforce bounded integer string lengths from p and the preflight input count/width.
precision_bits always names the completed pass supplying emitted enclosures;
last_attempted_precision_bits names the highest precision actually started, not
merely scheduled. If no full numeric pass completes, spread_energy_interval and
participation_ratio_interval are null. For zero_spread both precision fields are
null (exact proportionality proof), spread_energy_interval is exactly [0,0]
encoded with integer "0" and exponent2=0, and ratio interval is null.
The scalar is non-null only for state certified; it represents
the certified decimal value (ordinary JSON-number encoding is not an exact real).
No unqualified proxy scalar appears on unresolved/zero-spread paths.

`pair_witnesses`: entries for min, p10, p50, p90. Each entry carries lower/upper
zero-based order-statistic ranks, the corresponding admitted-occurrence pair
ordinals and cosine values, plus the same interpolation weight used by the
distribution. For P pairs, h=q(P-1), lower=floor(h), upper=min(lower+1,P-1);
min has ranks 0/0 and weight 0. Preserve both endpoints even when weight is zero.
Ties sort by (cosine,i,j), where i<j are stable original input occurrence ordinals;
filtering may leave gaps, and repeated IDs/texts remain separate occurrences.
For manifests, the ordinal is the zero-based physical line position, including
blank/malformed lines in the numbering but never as admitted observations.
Directory input ordinals count eligible .txt/.md paths in sorted loading order,
before the word-floor filter. Preserve this mapping in the snapshot/resume identity.
The summary's interpolation arithmetic remains the existing binary64 expression.
Witnesses illustrate values in the distribution; they do not stand for inferred
clusters/modes, best prose or publicly anonymous units. No paths, IDs or excerpts
are emitted; private ordinal linkage remains sensitive.

Resume identity includes method, certificate version, precision schedule and active
precision, permitted resource budget, vocabulary and recorded vector bytes, input
occurrence mapping, pair-witness policy, checkpoint phase and backend identity.
Every reuse validates artifact counts/checksums/ranges. Legacy state cannot resume
the new method and vice versa. Precision escalation preserves completed pair
sorting but recomputes or rigorously revalidates every numeric-phase artifact;
changing budget/settings is a fresh run under the initially strict identity rule.

## Acceptance before production integration

| Boundary | Required executable evidence in the later implementation |
|---|---|
| Legacy retention | Default and explicit legacy results match the unchanged oracle in the same pinned environment; no new fields or scale-triggered switch. |
| Target arithmetic | Small rational/orthogonal/positive-proportional/zero/opposite/duplicate fixtures; independent high-precision reference, exact interval containment and correct six-decimal decisions. |
| Near-zero spread | Existing seed-8 counterexample at all recorded perturbations, plus exact proportionality and normalization-underflow inputs; reported interval encloses the new target and non-null scalar is its certified rounding, otherwise explicitly null, never legacy fallback. |
| Certificate primitives | Adversarial signed endpoints, zero-crossing squares, tiny positive denominators, perfect/nonperfect square roots, exact rounding ties; demonstrate no enclosure can exclude its exact rational target where available. |
| No false success | Zero spread, unresolved positivity, rounding-boundary ambiguity, exhausted precision/budget, internal interval inconsistency and malformed/corrupt numeric state each exercise their specified branch. |
| Distribution/witnesses | All seven fields equal the old pair oracle; every rank/value/ordinal endpoint reconciles, including ties, duplicate IDs/texts, filtered rows and multipass merges. |
| Recovery/resources | Crash/restart each preprocessing/sort/numeric/precision-escalation phase; refuse identity/custody changes; process-memory test at full feature width and precision cap; no scalar success from a partial pair set. |
| Posture | Non-null modes only with certified diagnostics and distribution/witnesses; private ordinal warning, existing refusal/claim-license posture; no detector cut, selector or inferred partition. |

No current receipt clears these new gates. Outstanding adoption points are the
proposed zero-spread null convention, the certificate/display rule and precision
budgets. Independent numerical/contract review must assess them; Claude
counter-review remains pending before new builds proceed to integration. The
owner-approved method direction does not itself approve these details. After
adoption, a fresh narrowly scoped implementation claim, current overlap check,
explicit production authorization and ordinary build reviews are still required.
This decision-record update does not create that claim or authorize that build.

## Review receipt (2026-09-26)

Independent Codex numerical and scope/fleet-posture reviewers approved publication
of this proposal after reconciliation. They confirmed the exact zero-spread test,
outward interval arithmetic and endpoint-rounding argument; full-pool-only bounds
and attempted/reported precision metadata; legacy admission/claim-license
preservation; and the narrow numeric-budget null exception. Both read the canonical
spec-authoring and full build-preflight checklists. No new implementation tests
or empirical resource acceptance were claimed. These reviews do not adopt the
proposed numerical policy or replace Claude counter-review.
