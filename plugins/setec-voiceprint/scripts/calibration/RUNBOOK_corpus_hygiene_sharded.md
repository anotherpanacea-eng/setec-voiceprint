# RUNBOOK: sharded corpus_hygiene at RAID scale (v1.45.0)

**Audience**: a maintainer running `check_corpus`'s preprocessing
audit across a corpus large enough that the single-process script
becomes impractical. Empirically: ~436K files (MAGE) takes ~30 min
single-threaded; ~8.3M files (RAID) projected at ~13 h. Sharding
plus 8 parallel workers should drop the RAID run to ~2 h.

**Scope**: the corpus_hygiene task surface within shard_runner.
Per-host setup (launchd on macOS, systemd/cron on Linux) is in
`launchd/RUNBOOK_macos_nightly.md`. Multi-machine sync is in
`RUNBOOK_multi_machine_sync.md`.

---

## 0. When to use this

Use the sharded path **only** when the single-process
`check_corpus.py` is too slow. The single-process path is simpler
(one command, one report, no state.json) and remains the
operator-facing UX for small corpora. Concrete threshold:

  * Under ~100K files: single-process. Total wall-clock is under
    10 minutes; shard overhead is not justified.
  * 100K–1M files: judgment call. Sharded gives 4–8x speedup but
    requires the runbook below.
  * Over 1M files: sharded. Single-process projection exceeds an
    overnight window.

The sharded artifact is parity-tested against the single-process
artifact (see `test_shard_runner_corpus_hygiene.py
::test_aggregate_parity_with_single_process`) so an operator can
swap paths without re-tuning downstream consumers.

---

## 1. Prerequisites

  * A corpus manifest (JSONL) with one row per file. Each row must
    carry a `path` field, resolved relative to the manifest's
    parent directory.
  * `check_corpus`'s dependencies installed (spaCy is NOT required
    for hygiene; just the stdlib + the framework's preprocessing
    rules).
  * Disk space for the per-shard cache files. Hygiene records are
    smaller than calibration records (~1 KB each); 8.3M files at
    1 KB is ~8 GB.

---

## 2. Shard the source manifest

Split the source manifest into roughly-equal stratified shards.
For 8.3M rows at a 100K shard-size target, that's ~83 shards.

```bash
python3 scripts/calibration/shard_runner.py \
    --base-dir $SETEC_BASELINES_DIR \
    shard \
    --task corpus_hygiene \
    --source-manifest path/to/manifest.jsonl \
    --run-id raid_hygiene_2026-05-15 \
    --shard-size 100000 \
    --stratify register,ai_status \
    --shuffle-seed 42
```

Optional hygiene-specific flags (defaults shown):

  * `--warn-threshold 0.01` — strip-ratio warning threshold.
  * `--fail-threshold 0.05` — strip-ratio failure threshold.
  * `--strip-rules <comma-list>` — explicit preprocessing rule
    names. Default is all conservative rules.
  * `--strip-aggressive` — also enable aggressive URL/image/
    footnote/citation stripping.

After shard, `state.json` records `task=corpus_hygiene` and a
`task_params` blob with the thresholds. Every shard is in state
`pending`.

---

## 3. Work the queue

Run workers in parallel. On the RAID calibration host (8 cores, 16 GB):

```bash
python3 scripts/calibration/shard_runner.py \
    --base-dir $SETEC_BASELINES_DIR \
    work \
    --task corpus_hygiene \
    --run-id raid_hygiene_2026-05-15 \
    --workers 8
```

`--task corpus_hygiene` is required (state.json is authoritative;
the worker refuses to start if the CLI flag disagrees). Each
worker claims one shard at a time, reads its manifest slice, calls
`check_corpus.score_manifest_rows` for that slice, and writes the
result to `shards/<id>/cache.json`.

Workers honor the same SIGTERM / pause-marker / time-window /
multi-machine-sync contracts as the calibration_survey task. See
`launchd/RUNBOOK_macos_nightly.md` for the launchd nightly path
and `RUNBOOK_multi_machine_sync.md` for the multi-machine path.

Inspect progress while it runs:

```bash
python3 scripts/calibration/shard_runner.py status \
    --run-id raid_hygiene_2026-05-15
```

---

## 4. Aggregate

Once every shard is `done`:

```bash
python3 scripts/calibration/shard_runner.py \
    --base-dir $SETEC_BASELINES_DIR \
    aggregate \
    --task corpus_hygiene \
    --run-id raid_hygiene_2026-05-15 \
    --out path/to/raid_hygiene_summary.json \
    --no-derive
```

`--no-derive` is required for corpus_hygiene (the flag only
applies to calibration_survey's per-signal threshold sweep, which
this task surface doesn't do).

The output JSON has the same shape as single-process
`check_corpus.check_corpus_paths`, with these additions:

  * `task: "corpus_hygiene"` — the task surface that produced it.
  * `run_id` — the sharded run.
  * `source_manifest_sha256` — provenance.
  * `n_shards_contributed` — count of `done` shards aggregated.
  * `contributing_shards` — sorted list of shard ids.
  * `aggregated_at` — UTC timestamp.

The `status`, `n_clean`, `n_warning`, `n_fail`, `n_error`,
`dominant_rule`, and per-file `files` keys all match the
single-process schema.

---

## 5. Failure modes and recovery

  * **A worker dies mid-shard.** SIGTERM/SIGKILL paths and
    sweep-stale work the same way they do for calibration_survey
    (see `internal/SPEC_sharded_calibration.md` §2.2/§2.4).
    Resuming the dead shard re-runs the full scoring pass —
    corpus_hygiene scorers don't yet opt into the
    `SigtermInterrupt` mid-shard checkpoint contract. For an 8.3M-
    file run with 100K-row shards, the worst-case re-do is one
    shard's worth of work (~1 hour single-worker), which is
    tolerable.
  * **A shard cache is missing or corrupted.** `verify` catches
    this against the SHA-256 recorded in state.json. `aggregate`
    refuses to produce a summary if any cache fails integrity
    unless `--allow-partial` is passed. Either path requires the
    operator to re-run the affected shard.
  * **`--task` mismatch.** `work` and `aggregate` refuse to run
    if the CLI `--task` disagrees with state.json. State.json is
    authoritative; pass the matching flag.

---

## 6. What's not in v1.45.0

  * `corpus_hygiene` scorers don't yet honor `SigtermInterrupt`
    mid-shard checkpoints. The contract is in place; the opt-in
    lands once a RAID-scale run reveals whether the per-shard
    re-do cost is actually a problem.
  * The aggregator doesn't yet emit a Markdown-rendered report
    (`check_corpus.render_report` parity). The JSON output is the
    canonical artifact; a downstream consumer can render it.
  * The `verify` subcommand only checks cache SHA-256s. A task-
    aware integrity check (e.g., "all rows have a `status`
    field") is in scope for v1.46.0.
