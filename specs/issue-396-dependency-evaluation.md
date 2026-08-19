# Voiceprint #396 — retrospective dependency evaluation spec (v4)

## Problem correction and closure boundary

Main already adopted optional Trafilatura extraction in `e910bb5` and optional
datasketch MinHash-LSH document dedup in `dd679f1`. This work evaluates those
shipped seams retrospectively; it does not add or replace a production dependency.
The evaluation PR may close #396 only after it links a separate production
pin/scheme decision for the current unbounded `datasketch>=1.6` behavior-drift
risk. No destructive keep/drop claim may be upgraded by the research packet.

## Reproducible packet and environment isolation

Add `research/dependency-evaluations/issue-396/` containing:

- `README.md`: scope, exact commands, measurements, limitations, package/license/
  Python-support record, and adopt/optional/reject decisions.
- `controller.py`: offline orchestrator and deterministic result assembler.
- `bootstrap.sh`: the only network-permitted phase; downloads the exact locked
  wheels into a gitignored local wheelhouse and verifies every hash.
- `evaluate-offline.sh`: creates workers only from that wheelhouse using
  `--no-index --require-hashes` and runs the controller under a macOS
  `sandbox-exec` profile that denies all network access. A denial probe is an
  acceptance test, not an assumption.
- `research/dependency-evaluations/issue-396/workers/trafilatura_probe.py` and
  `research/dependency-evaluations/issue-396/workers/datasketch_probe.py`: version-local
  workers. They accept one schema-versioned JSON request and return JSON only.
- `locks/`: hash-locked Python 3.13.7 environments for (a) Trafilatura 2.1.0,
  (b) datasketch 1.6.5, and (c) datasketch 2.0.0 + RapidFuzz 3.14.5, including
  exact NumPy/SciPy and transitive versions. Lock files record artifact hashes;
  the controller refuses the wrong Python/package identity.
- `research/dependency-evaluations/issue-396/fixtures/manifest.json` and its
  `fixtures/` directory: frozen HTML, RSS/Atom, and text-pair
  inputs, each hash-bound before use.
- `results.json`: stable semantic results and environment identities. Timing/RSS
  samples live in a separate explicitly non-byte-stable block/file.

Stable JSON/IPC encoding is UTF-8 with LF, sorted keys, separators `(',', ':')`,
finite JSON numbers only, and deterministic array ordering. The stable block contains
no absolute paths, wall-clock timestamps, random temp names, or timing/RSS samples.

After the explicit bootstrap, the offline controller creates three separate
temporary virtual environments from the verified local wheelhouse. Cross-version
IPC is JSON plus raw byte files only. A MinHash signature is
exported as unsigned integer values plus `{version, scheme, num_perm, seed,
shingle_size, normalization}`; no pickle is opened across environments and pickle
byte equality is never treated as semantic compatibility. LSH indexes are rebuilt
unless the exact version/scheme identity is proven.

## Fixture redistribution and scoring contract

Every checked-in source fixture must be public-domain or permissively licensed for
redistribution, with source URL, retrieval date, attribution, license identifier,
license-evidence URL, and SHA-256 in the manifest. Prefer minimal frozen excerpts
that preserve the relevant DOM/feed layout; do not check in a complete copyrighted
page merely because its URL is public.

For each HTML fixture the manifest also records the exact shipped
`content_selector` and `strip_selectors`, required main-text markers, forbidden
boilerplate markers, and expected title. Marker comparison is Unicode NFC after
CRLF normalization and whitespace-run collapse, with case preserved. Per-fixture
recall is required markers found / required markers; leakage is forbidden markers
found / forbidden markers. Report both micro counts and unweighted macro means.
Every score-bearing HTML fixture has nonempty required and forbidden marker lists.
Empty/malformed failure-only fixtures are excluded from recall/leakage denominators
and macro means but retain explicit primary/full-seam fail-soft outcomes.

## Trafilatura arm

1. Use at least six redistributed public fixtures across materially different
   structures (federal-government article, legal/technical document, and
   archive/publication layouts), plus empty and malformed failure cases.
2. Drive the shipped `acquisition_core.extract_main_content` seam twice with each
   fixture's real selectors: `prefer_trafilatura=False` for the current BeautifulSoup
   path and `True` in the locked Trafilatura environment. Also call the shipped
   `_trafilatura_extract` primary directly to attribute primary hit/miss/output;
   full-seam parity produced only by fallback is not Trafilatura evidence.
3. Report recall, leakage, normalized title retention, empty/malformed behavior,
   warm-run median wall time, and output SHA-256 across five reruns.
4. Decision rule for the existing extraction seam:
   - `keep optional` only if every rerun is deterministic, the primary succeeds on
     at least one fixture in every represented structure class, macro recall is no more
     than 0.05 below fallback, macro leakage no more than 0.05 above fallback, no
     fixture loses all required markers when fallback finds them, and median runtime
     is at most 5x fallback; expected titles may not regress where fallback retains
     them, and empty/malformed inputs may not raise or bypass fail-soft behavior;
   - `reject` if any condition fails;
   - `adopt` is reserved for a future mandatory-dependency proposal and is not a
     result this optional-only research PR can enact.
5. Evaluate two frozen feeds (RSS and Atom) separately. Compare Trafilatura's
   offline extracted link list against `acquire_blog.parse_feed` for link set,
   order, and deduplication, then record that the shipped `FeedItem` contract also
   carries title, link, date, body HTML, paid status, and raw byte length. Feed
   replacement is rejected unless every field has parity; link-only parity cannot
   justify replacing `feedparser`.
6. Record Trafilatura 2.1.0's exact artifact/lock identity, Apache-2.0 boundary
   (pre-1.8 releases were GPL), supported Python range, and transitive packages.

## datasketch + RapidFuzz arm — shipped behavior first

Ground truth is exact normalized word-shingle Jaccard plus exact content/verbatim
identity where applicable. The report must not describe current document mode as
exact-confirmed: it currently builds `MinHash(num_perm=...)`, retrieves LSH
candidates, compares estimated MinHash Jaccard, and unions passing candidates.

Produce separate tracks for:

1. unmodified current document mode under datasketch 1.6.5;
2. unmodified current document mode under datasketch 2.0.0 defaults (`affine32`);
3. a clearly labeled research-only 2.0.0 adapter using `scheme="legacy"` with the
   same explicit `seed=1`, `num_perm=128`, five-word shingles, and threshold 0.85;
4. current passage Stage A (complete prefix candidates + exact Jaccard) and Stage B
   (stdlib exact repeated-span scan), including the existing datasketch-presence
   gate and the no-datasketch Stage-B fallback.

The unmodified tracks call the real `near_dup_dedup.dedup_records` seam; do not
substitute an idealized candidate-only implementation. The worker separately emits
signatures to show 1.6.5 vs 2.0 default incompatibility and 2.0 legacy equivalence
or non-equivalence. Persisted-index policy is `rebuild/refuse` across any unproven
version/scheme boundary.

The exhaustive accuracy corpus contains exactly 200 documents (19900 unordered
pairs): exact duplicates, truncations, boilerplate additions, reordered passages,
near-threshold mutations on both sides of 0.85, unrelated controls, and explicit
transitive A-B/B-C/A-not-C components. It uses deterministic synthetic plus
redistributable public text. Truth uses the repository's actual
`near_dup_dedup.shingles()` sets: pair edges are exact Jaccard `>=0.85`; oracle
clusters are connected components of those edges; oracle keep/drop applies the
shipped longest-text then lowest-id representative rule. For each track enumerate
pair false positives and false negatives, component merges/splits, every erroneous
keep/drop, and version-to-version differences.

Pair behavior is reported at three distinct prediction layers: (a) raw pairs returned
by each LSH query, (b) pairs whose estimated MinHash Jaccard passes 0.85, and (c) all
unordered pairs co-clustered by the final transitive union. Each layer gets its own
precision/recall/FP/FN ledger against the exact pair oracle. A transparent worker-local
tracing wrapper returned through `_require_datasketch` delegates to the real MinHash
and MinHashLSH classes while logging query results and `jaccard` pass/fail values. Its
final `DedupResult` must serialize byte-identically to an unwrapped control run in the
same environment before any traced metrics are accepted.

The research-only legacy adapter leaves production files untouched: the worker
temporarily replaces `near_dup_dedup._require_datasketch` with a constructor wrapper
that supplies `MinHash(num_perm=..., seed=1, scheme="legacy")` and the real
`MinHashLSH`, then calls the real `dedup_records` seam.

RapidFuzz is pinned to 3.14.5 and evaluated with `fuzz.ratio`, `processor=None` on
the same normalized strings. Cutoff 85 is the predeclared primary decision; 80 and
90 are exploratory only and cannot license production use. Report precision/recall and
incremental decisions relative to exact Jaccard/verbatim confirmation. Because it
only sees retrieved candidates, it cannot recover an LSH omission; the report must
say whether it filters any false candidates without losing oracle positives and
whether that adds value beyond exact confirmation.

Datasketch recommendation rule: `keep optional` for the current destructive
document seam only if every locked shipped track has zero oracle false-positive
drops, zero false-negative duplicate families, identical deterministic keep/drop
results across five reruns, and the version/scheme policy is explicit. Otherwise
`reject` that destructive seam and separately assess retaining MinHash as
candidate-only infrastructure behind exact confirmation. RapidFuzz merits a
production role only if primary cutoff 85 improves precision over raw LSH by
at least 0.10 with zero oracle-positive loss and beats exact-Jaccard confirmation on
the measured runtime axis; otherwise recommend rejection/no added dependency. Runtime
compares score-only calls over identical already-materialized candidate strings with
exact Jaccard over already-materialized shingle sets; parsing/shingling/setup time is
reported separately and cannot decide the scorer comparison.

## Mac scale and fallback measurements

Performance is separate from exhaustive accuracy. Generate deterministic scale
corpora from the redistributable texts at exactly:

- 1,000 documents × 1,000 normalized tokens; and
- 5,000 documents × 1,000 normalized tokens.

The small rung contains 50 known two-document duplicate families plus 900 unrelated
documents; the large rung contains 250 such families plus 4500 unrelated documents.
Membership is precomputed by exact shingles. Scale reporting checks those known
families plus a fixed-seed sample of 10000 negative pairs; it makes no exhaustive
all-pairs accuracy claim.

Mutations and ordering use a fixed seed. Run each package-version worker as a fresh
process, record worker wall time separately from controller/env startup, and capture
peak resident bytes using macOS `/usr/bin/time -l`. Small-rung timeout is 120 seconds;
large-rung timeout is 600 seconds; memory ceiling is 4 GiB. A timeout/ceiling breach
is a measured failure and must remain in results, not be silently skipped. Repeat
the small rung three times and report median; the large rung runs once.

In an environment without datasketch, demonstrate that base import stays clean,
document mode and passage Stage A fail with the current clear dependency error,
and Stage B remains available. Ownership recommendation: Voiceprint owns approximate
document candidates and exact passage guards; any consumer making a safety decision
must retain/recheck the source-bound exact evidence. State whether RapidFuzz merits
any production role.

## Acceptance gates

- Hash-verified network bootstrap and mechanically network-denied offline evaluation
  are separate; the denial probe, semantic rerun, and fixture-corruption refusal pass.
- No production dependency or runtime change in the research PR.
- Focused packet tests plus existing extraction and near-dup suites pass.
- Report distinguishes unmodified shipped tracks from research adapters and
  measurements from recommendations.
- #396 remains open until either the production datasketch pin/scheme remediation PR
  is merged and tested or the owner explicitly records acceptance of unbounded
  behavior drift; merely linking an unresolved follow-up is insufficient.
- Independent spec review is BUILD-READY before implementation; independent
  implementation review and exact-head Claude clearance are required before merge.
