# 73 - Deterministic, collection-conserving CI test topology

> Replace the single contention-prone Ubuntu pytest process tree with disjoint,
> auditable execution lanes while preserving every test, default, consistency
> gate, and focused native-platform job.

- **Status:** specification review GO; instrumented monolithic baseline built;
  immutable-head CI measurement pending
- **Scope:** CI workflow and test-infrastructure code only
- **Source contract:** owner-authorized post-B2 CI-topology follow-up
- **Production behavior:** unchanged
- **Corpus/model/GPU work:** none

## 1. Problem and measured baseline

At setec-voiceprint `88f8b06`, `.github/workflows/tests.yml` has one Ubuntu
job that installs the complete core dependency set and runs the whole suite
with `pytest ... -n auto`. The same workflow has four focused native-platform
jobs. B2 adds a fifth focused Windows job on its open branch; integration must
preserve that job if B2 lands first.

The immutable B2 head `fd61abf` supplies contextual evidence only:

- full Ubuntu job: 5m43s wall;
- pytest phase: 7,046 passed, 163 skipped, 22 warnings in 289.60s;
- focused platform jobs: 27-47s wall.

It is not the performance baseline because its 7,209 collected nodes and fifth
platform job differ from the implementation base. The current main checkout
collects 7,063 tests from 286 `test_*.py` files in the maintainer's installed
environment. That count is evidence, not a frozen
constant: optional dependencies and concurrent feature PRs may change the
collection. The topology must prove conservation from the collection on the
exact CI head and dependency image.

The failure mode to remove is nested fan-out: xdist workers concurrently start
Python, shell, and multiprocessing children, including tests with fixed join or
subprocess deadlines. Local full-suite runs have shown timeout sensitivity
under contention. The solution is execution isolation and bounded concurrency,
not test deletion, skip inflation, or longer deadlines.

## 2. Non-goals

This item does not:

- delete, deselect, weaken, rewrite, or reclassify production tests by outcome;
- change application code, capability contracts, requirements, or runtime defaults;
- set global pytest parallelism or make bare `pytest` require pytest-xdist;
- replace focused macOS or Windows validation with Linux simulation;
- claim a speedup until repeated immutable-head measurements support it;
- add corpus material, fiction, inference, calibration, model, or GPU work.

## 3. Checked-in plan and planner

Add `tools/ci_test_plan.json`, `tools/ci_test_plan.py`, a minimal checked-in
`tools/ci_pytest_plugin.py`, and focused tests under the existing test root.
The JSON document has schema
`setec-ci-test-plan/1` and exactly these top-level fields:

```json
{
  "schema": "setec-ci-test-plan/1",
  "test_root": "plugins/setec-voiceprint/scripts/tests",
  "unit_shards": 2,
  "serial_subprocess_cli": [],
  "integration_contract": [],
  "unit_shard_overrides": {}
}
```

Paths are repository-relative POSIX spellings of regular, non-symlink
`test_*.py` files below the fixed test root. Lists are sorted and duplicate-free.
The two explicit lanes are disjoint. Overrides may name only files assigned to
the unit complement and may select only shard `0` or `1`. Schema v1 fixes
`unit_shards` to exactly `2`; every other value is invalid. Changing the shard
count requires a later schema/spec revision that atomically changes the
workflow matrix and its binding tests.

The planner discovers test files without following links. Assignment precedence
is closed and disjoint:

1. `serial_subprocess_cli`;
2. `integration_contract`;
3. every other discovered test file is `unit` and maps to one unit shard.

Unit shard assignment is the unsigned integer value of the first eight bytes of
SHA-256 over the UTF-8 repository-relative path, modulo `unit_shards`, unless a
reviewed override exists. A whole file always stays in one shard. Overrides are
permitted only to correct a measured critical-path imbalance and require a
short rationale in the spec or PR; they may not hide failures.

The initial serial list contains every current test file with process-risk AST
evidence, plus reviewed overrides for indirect child-process helpers. The v1
trigger grammar is deliberately conservative and exact:

- any `import` or `from ... import ...` of `subprocess` or `multiprocessing`,
  including local imports and aliases;
- any import or attribute reference resolving to
  `concurrent.futures.ProcessPoolExecutor`;
- any call or attribute reference resolving through direct import or alias to
  `asyncio.create_subprocess_exec` or `asyncio.create_subprocess_shell`;
- any call or attribute reference resolving through direct import or alias to
  `os.system`, `os.popen`, `os.fork`, `os.forkpty`, every `os.spawn*`, every
  `os.posix_spawn*`, or `pty.spawn`.

An import of `subprocess.CompletedProcess` used only for a mock still triggers:
safe whole-file overclassification is intentional. Comments and string literals
alone do not trigger. The verifier fails closed when a triggering file is absent
from the serial lane. An allowlist is not part of v1: move the whole file to the
serial lane. Static analysis cannot see a production helper that launches a
child internally, so those files remain explicit reviewed serial overrides.

The narrow integration list contains direct in-process contract seams such as
capability/drop-in, drift, documentation freshness, readiness, task-surface,
claim-license, and output-schema tests. A subprocess-bearing contract test is
serial by execution topology even when its semantics are integrative. Lane
names describe execution safety, not the only testing style present in a file.

## 4. Planner command contract

The stdlib-only planner has four subcommands:

```text
ci_test_plan.py verify [--collect] [--collection-out PATH]
ci_test_plan.py list --lane {unit,serial_subprocess_cli,integration_contract}
                     [--shard-index N] [--null]
ci_test_plan.py run --lane {unit,serial_subprocess_cli,integration_contract}
                    [--shard-index N] -- [PYTEST_ARGS...]
ci_test_plan.py verify-results --collection-report PATH --result PATH...
                               [--baseline-result PATH]
```

`verify` rejects malformed JSON, unknown fields, absolute/backslash/dot-segment
paths, non-UTF-8 data, links, paths outside the root, missing or non-test files,
unsorted/duplicate/overlapping entries, invalid shard counts or overrides,
empty required lanes/shards, and unassigned process-risk files. It prints one
canonical ASCII JSON object plus LF and exits 0 on success. JSON uses sorted keys,
compact separators, and ASCII escaping. The base object has `schema`, `files`,
`serial_subprocess_cli`, `integration_contract`, `unit_0`, and `unit_1`, with
integer counts. Controlled validation errors print one sanitized ASCII line to
stderr plus LF and exit 2; no traceback or absolute machine path is emitted.

`verify --collect` runs the exact-head suite in collection-only mode after CI
dependencies are installed. It must not parse pytest's human stdout. The
checked-in plugin captures `session.items` at `pytest_collection_finish` before
lane filtering and returns exact node IDs in memory to the planner. The planner
accepts them only when pytest exits successfully; collection/import failure
discards every partial result. It assigns every collected node ID through the
file plan and adds a `collection` object with `canonical`,
`serial_subprocess_cli`, `integration_contract`, `unit_0`, and `unit_1`; each
contains integer `count` and lowercase `sha256`. For each set, digest bytes are
the UTF-8 node IDs sorted by encoded byte sequence, each followed by one NUL,
including a trailing NUL after the last node ID. The empty-set digest, although
required lanes may not be empty, is SHA-256 of empty bytes. The sorted disjoint
lane union must equal the sorted canonical node-ID set exactly: missing zero,
duplicates zero. Collection or import failure is a controlled nonzero result,
never treated as an empty plan.

Parameterized and Unicode node IDs are opaque UTF-8 strings. The planner never
parses or reconstructs their parameter segments; it binds only the already
discovered repository-relative file prefix.

`--collection-out` is valid only with `--collect`. It writes the same canonical
JSON bytes printed on stdout to a create-new binary file and refuses an existing
destination. The baseline monolith and candidate integration job save this file
and upload exactly one artifact named `ci-collection` under `if: always()` with
missing-file behavior set to error. The aggregate downloads that exact artifact
before `verify-results`; zero, duplicate, malformed, or incomplete collection
artifacts fail the gate. Jobs do not rely on a shared filesystem.

`list` emits repository-relative files sorted by UTF-8 bytes. Text mode appends
one LF after every path, including the last; `--null` appends one NUL after every
path, including the last. Unit requires an in-range shard index; the two
unsharded lanes reject one. Invalid combinations exit 2.

`run` verifies the plan and then invokes `sys.executable -m pytest` with the
selected whole-file paths followed by the arguments after `--`. It preserves
the child's stdout, stderr, and exit code. It never uses a shell, rewrites a
timeout, or converts failure into success.

The pytest plugin also accepts `--ci-result-out PATH`. Its canonical result JSON
has exactly `schema`, `complete`, `exitstatus`, `warnings`, `expected_count`, and
`outcomes`. `schema` is `setec-ci-test-result/1`; `complete` is boolean;
`exitstatus`, `warnings`, and `expected_count` are integers; `outcomes` is an
array sorted by UTF-8 node-ID bytes whose objects have exactly `nodeid` and
`outcome`. Serialization uses sorted object keys, compact separators, ASCII
escaping, and one final LF. The plugin records exact node IDs
and closed final outcomes (`passed`, `skipped`, `xfailed`, `xpassed`, `failed`,
or `error`) across setup/call/teardown, plus session warning count, in canonical
JSON schema `setec-ci-test-result/1`. Final outcome reduction is exact:

1. a failed setup or teardown is `error` and overrides every call outcome;
2. a report carrying `wasxfail` is `xfailed` when skipped and `xpassed` when
   passed or failed (therefore strict XPASS remains a failing pytest session);
3. an ordinary failed call is `failed`, skipped setup/call is `skipped`, and a
   passed call with clean setup/teardown is `passed`;
4. a selected node without one of those complete phase shapes makes the artifact
   incomplete and invalid.

Workers never publish. Under xdist, the controller uses xdist's completed-node
collection hook to require identical selected node-ID sets from every worker,
then records forwarded worker reports and writes their final union. A worker
crash, mismatched worker collection, missing outcome, interrupt, collection
error, internal error, or usage error produces no result file. Exit status 0 or
ordinary test-failure status 1 may publish `complete=true` only when every
expected node has exactly one closed final outcome. The destination must not exist.
After the session ends, the controller serializes the complete canonical bytes
in memory, opens the destination create-new in binary mode, writes them once,
flushes and fsyncs where supported, and closes it. An existing destination is a
controlled refusal; no replacement occurs. Truncated/invalid JSON or a report
without `complete=true` is rejected, so a partial write cannot masquerade as
complete. Every Linux lane
uploads its report. The aggregate downloads them and the stdlib planner verifies
that their node-ID sets are disjoint and their union equals the exact canonical
collection. Failures remain failures; this artifact gate detects a collected
node that was neither run nor explicitly skipped.

A real pytest-xdist `-n 2` acceptance test must prove one controller-only report,
identical worker collections, and the exact full selected-node union. Companion
tests inject a worker crash and collection mismatch and prove no valid report is
published.

`verify-results` validates every report schema/completeness marker, rejects
unknown/duplicate outcomes, proves pairwise disjointness and exact collection
union, and prints canonical aggregate counts/digests. The outcome-map digest is
SHA-256 over entries sorted by UTF-8 node-ID bytes, each encoded as node ID UTF-8
plus NUL plus the lowercase ASCII final outcome plus NUL. With
`--baseline-result`, it requires both skipped-node and full outcome-map digests
to match the monolithic baseline. The two measurement heads differ only in
workflow topology, so no test-result delta is permitted in that comparison.

All planner path and output handling follows update-14 portability guidance:
binary-stable UTF-8/LF or NUL output, `pathlib`/stdlib APIs, no chmod/fchmod or
permission policy, no unguarded `O_*` flag, and no POSIX-only process primitive.

## 5. Linux workflow topology

Replace the instrumented monolithic Ubuntu execution with these required jobs,
all on Python 3.12 and the same complete dependency/model image used by the
baseline job:

1. **`pytest-unit`** - matrix shard 0 and 1; each runs the planner's unit shard
   with fixed `-n 2 --dist loadfile -q -rs`. It never uses `-n auto`.
2. **`pytest-subprocess`** - the serial subprocess/CLI lane with no `-n` flag.
3. **`pytest-integration`** - the narrow integration-contract lane with no
   `-n` flag. Before its tests it runs `verify --collect`; after its tests it
   runs the three existing consistency commands under `if: always()`.
4. **`pytest`** - a tiny `if: always()` aggregate job whose dependencies are the
   unit matrix, subprocess lane, integration lane, and every focused macOS and
   Windows job present on the integration base, including the new focused
   Windows planner job below. It succeeds only when every dependency result is
   exactly `success`. Failure, cancellation, or skipping of any required
   dependency makes this job fail.

Each Linux test invocation enables the result plugin and uploads its canonical
report under a lane/shard-specific artifact name even on failure. Before the
aggregate returns success, it downloads the reports and verifies exact result
union conservation against the downloaded `ci-collection` artifact.

The aggregate retains the existing `pytest` required-check name and makes the
native-platform jobs transitively blocking even if branch protection requires
only that historical context. The disjoint Linux lanes together are the full
Linux gate; there is no second duplicate full run. Consistency commands remain
exactly:

```text
python3 tools/check_capabilities_drift.py
python3 tools/check_docs_freshness.py
python3 tools/gen_calibration_readiness.py --check
```

They execute exactly once per workflow and remain blocking even when the
integration test step fails.

`pytest.ini` keeps its current effective behavior: test root only, parallelism
opt-in. Volatile historical test-count/timing comments may be replaced with a
pointer to this spec, but no `addopts` parallel default is added.

## 6. Native-platform preservation and integration rule

The current base's focused jobs and exact test selections remain intact:

- `macos-descriptor-confinement`;
- `windows-descriptor-backend`;
- `windows-owner-corrections`;
- `windows-private-writer-guards`.

Add **`windows-ci-test-plan`**, a focused Python 3.12 Windows job that installs
pytest and runs the planner test module. Its tests invoke the real planner in
temporary repositories and byte-check LF stderr/stdout, text-list trailing LF,
NUL-list trailing NUL, Unicode/space/`#` paths, sanitized path errors, argument
round-trip, and child exit/stdout/stderr propagation. It is additive: no existing
platform job's selection changes.

If an integration merge brings B2's `windows-nonprose-sweep`, preserve it
unchanged as well. Focused platform duplication is intentional and outside the
disjoint Linux conservation calculation. A merge conflict is resolved by
retaining both topologies and every platform job; never drop a job to make YAML
merge cleanly.

## 7. Acceptance tests

The implementation must prove:

1. valid schema parsing and deterministic byte-identical plan output;
2. rejection of unknown schema/fields, malformed types, invalid lane/shard
   combinations, absolute/backslash/dot-segment/escaping paths, symlinks,
   missing/non-test files, duplicates, overlaps, unsorted entries, stale
   overrides, and empty lanes/shards;
3. a newly discovered ordinary test file enters exactly one unit shard without
   a manifest edit;
4. stable whole-file SHA-256 assignment and valid override behavior;
5. every v1 process-risk trigger family requires serial classification, with
   positive direct/local/aliased import and call cases for each family;
6. comments and string literals do not create false process evidence, while a
   type-only `CompletedProcess` import triggers by the conservative import rule;
7. full file union equals discovery exactly and is pairwise disjoint;
8. `verify --collect` reports exact canonical/per-lane counts and digests and
   fails on collection error, a missing node, or a duplicate node, including
   adversarial parameterized and Unicode node IDs without stdout parsing;
9. `run` uses no shell and preserves child arguments, stdout, stderr, and exit
   status;
10. bare pytest behavior is unchanged and every existing test still collects;
11. the workflow matrix launches exactly unit shards `0` and `1` once each, and
    a binding test proves that it matches schema v1's fixed shard set;
12. the workflow aggregate fails for an injected failed, cancelled, or skipped
    Linux or native-platform dependency and passes only for all-success;
13. all platform job IDs and exact selections on the integration base remain;
14. the native-Windows planner lane proves the byte and child-process contract;
15. complete lane-result artifacts are pairwise disjoint, conserve the canonical
    node-ID set exactly, and reject partial/interrupted/unknown outcomes;
16. real xdist `-n 2` produces exactly one controller report with the full worker
    union and no competing writer, while injected worker crash/mismatch produces
    no valid report;
17. result reduction pins ordinary pass/fail, setup skip/error, xfail, non-strict
    and strict xpass, teardown failure override, interrupt, and collection error;
18. exactly one valid collection artifact crosses the job boundary; missing,
    duplicate, malformed, partial, or stale collection artifacts fail closed.

## 8. Measurement and no-flake gates

Record queue-excluded job execution and user-visible created-to-complete time
separately. Critical path is the interval from the earliest required-job
`startedAt` through aggregate-job completion; created-to-complete includes queue
delay. For baseline and candidate, record immutable head/run IDs, each required
job's wall time, test-phase time, exact passed/skipped/warning counts, and summed
runner-seconds.

Local gates:

- canonical collection and plan digest repeat three times identically;
- the serial lane passes five consecutive runs with no retry;
- each integration/unit lane passes three consecutive runs with no retry;
- no timeout increase, worker crash, orphan process, or skip inflation.

For the apples-to-apples experiment, build the branch in two implementation
commits after the reviewed spec:

1. a **baseline head** containing the final planner, plan, tests, result plugin,
   and additive Windows planner job. Rename the old Linux executor to
   `pytest-monolith`; instrument it with `verify --collect`, one complete result
   report, and artifact upload. Add the same tiny `pytest` aggregate used by the
   candidate, depending on the monolith plus every focused platform job and
   verifying the monolithic result against the collection report. Open the one
   draft PR at this head so the existing `pull_request` trigger runs it;
2. a **candidate head** changing only the Linux execution topology, aggregate's
   Linux dependency list, and direct workflow-binding documentation/assertions.

The two heads must produce the same canonical collection digest under the same
dependency image before any topology speed claim is permitted. Their complete
and skipped-node result-set digests are compared. Warning counts are reported
per session because splitting sessions can duplicate session-level warnings;
they are not summed as if they were node outcomes.

GitHub gate:

- capture four executions of the immutable baseline head and four of the
  immutable candidate head. For each head, treat the initial PR-trigger run as
  observational attempt 1 and predeclare three sequential full `gh run rerun`
  attempts as measured attempts 2-4; record the run ID plus GitHub `run_attempt`.
  Every attempt, including a failure, remains in the evidence, so this is a fixed
  protocol rather than rerun-until-green. Confirm cache restoration for attempts
  2-4 from setup/cache logs and report attempt 1 plus actual cache state
  separately. If any measured attempt lacks confirmed cache restoration, make no
  performance claim;
- all eight executions pass with no timeout, cancellation, or worker crash;
- latency and runner-second medians use exactly confirmed-cache attempts 2-4 for
  each head; attempt 1 is never included in those medians;
- candidate median critical path is at least 15% below the matched baseline
  median, and no candidate run exceeds the slowest matched baseline run;
- candidate median summed Linux runner-seconds, including dependency setup,
  tests, consistency checks, and aggregate job but excluding unchanged platform
  jobs, is at most 1.5 times the matched baseline median summed Linux
  runner-seconds;
- the slower unit-shard test phase is at most 125% of the faster unit-shard test
  phase; serial and integration lanes are constrained by the overall critical
  path, not compared to heterogeneous unit durations;
- report platform runner-seconds separately and do not claim any cost improvement
  unless it is measured;
- baseline and candidate canonical collection digests must match; candidate
  complete-result union must equal that collection; skipped-node digest must
  match the monolithic baseline exactly; all eight attempts must share the same
  full node-ID-to-final-outcome digest;
- focused platform jobs remain green and their exact test selections are
  unchanged.

If the latency or balance gate fails, adjust only reviewed shard overrides or
bounded worker count and rerun the same measurements. Schema v1's shard count
stays fixed at two. Do not delete coverage, raise deadlines, add skips, or weaken
the conservation gate.

## 9. Delivery gates

- independent Sol specification review before implementation;
- independent Sol implementation, workflow, and conservation reviews after
  implementation;
- one draft PR; merge commits only;
- exact pass/skip/warning and collection-digest evidence in PR and fleet ledger;
- leak gate before every push;
- merge held for Code-PC Claude.
