# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/) with [Semantic Versioning](https://semver.org/). The version field in `plugins/setec-voiceprint/.claude-plugin/plugin.json` bumps on every shipped commit: `feat:` → MINOR, `fix:` / `chore:` / `docs:` → PATCH. Major version is reserved for breaking changes to the public CLI / JSON contract.

## Unreleased

_(Empty. Future work lands here, gets versioned on commit.)_

## [1.81.0] - 2026-05-16

**Standalone-CLI exposure of the 1.80.0 Tier 4 + pluggable-embedding flags + the inner-Namespace pipe.** Symmetry follow-up to 1.80.0. `score_corpus` reads `do_tier4`, `embedding_model`, `embedding_revision`, `surprisal_model`, `surprisal_revision` from the args Namespace via `getattr` — the sharded path (`shard_runner shard` → `state.json["task_params"]` → `_default_scorer`) populates them, but the standalone `calibration_survey.py` and `calibrate_thresholds.py` argparsers didn't expose them. AND, in `calibration_survey.py`, even after the parser accepted the flags, `_build_inner_args` constructed a fresh Namespace for the score-once path that dropped the new fields — so parsed values landed on `parent_args` but never reached `score_corpus`.

### Added

- **`calibration_survey.py --tier4 --no-tier4 --surprisal-model --surprisal-revision --embedding-model --embedding-revision`** — same flag shapes and help text as `shard_runner shard`. Default `--tier4` off; default model aliases `None` (preserves pre-1.81 bit-exact behavior).
- **`calibrate_thresholds.py --tier4 --no-tier4 --surprisal-model --surprisal-revision --embedding-model --embedding-revision`** — parity with `calibration_survey.py`. The single-signal threshold-derivation CLI now supports the same model swaps as the full survey CLI.
- **`calibration_survey.run_survey` output dict** now surfaces `tier4`, `embedding_model`, `embedding_revision`, `surprisal_model`, `surprisal_revision` in the provenance block alongside the existing `tier2`/`tier3` fields so downstream consumers (band classifier, bake-off comparator, ledger writer) can tell which embedding / surprisal model a survey was scored under. Without these fields the JSON was ambiguous between a default MiniLM run and a swap to a different embedding model.
- **7 new regression tests** in `test_pipeline_model_wiring.py` under "1.81.0: standalone-CLI surface":
  - `test_calibration_survey_cli_exposes_tier4_and_model_flags` — `--help` output contains all 6 new flags.
  - `test_calibrate_thresholds_cli_exposes_tier4_and_model_flags` — same for the threshold-derivation CLI.
  - `test_calibration_survey_parser_defaults_preserve_back_compat` — defaults match pre-1.81 (tier4=False, model fields None).
  - `test_calibration_survey_parser_accepts_explicit_values` — parser correctly populates the args Namespace when all 6 flags are set.
  - **`test_calibration_survey_build_inner_args_forwards_new_fields`** (codex P2 guard) — `_build_inner_args` carries the 5 new fields from parent_args onto the inner Namespace; verified to fail without the fix.
  - **`test_calibration_survey_build_inner_args_defaults_when_parent_lacks_fields`** — back-compat: a pre-1.81 parent_args without the new flags still produces a working inner Namespace (no AttributeError) with safe defaults.
  - **`test_calibration_survey_inner_args_reach_score_corpus`** (codex P2 guard, end-to-end) — parses CLI args, builds inner args, calls `load_or_score_corpus`, spies on `score_smoothing_entry` to confirm `do_tier4` / `embedding_model` / `surprisal_model` actually flowed all the way through to the leaf scorer call. This is the assertion codex pointed at: parser tests prove the flags PARSE; this test proves the parsed values REACH the score-once path.

### Fixed

- **Codex P2 on PR #78: `calibration_survey._build_inner_args` now forwards the 5 new 1.80.0 fields.** Before the fix, the inner Namespace dropped `tier4` / `embedding_model` / `embedding_revision` / `surprisal_model` / `surprisal_revision` even when the parser had captured them on `parent_args`. `score_corpus`'s `getattr` defaults then silently fell back to off / None, so a standalone `calibration_survey.py --tier4 --embedding-model mxbai` run was indistinguishable from the legacy MiniLM Tier 1+2+3 default. Fix uses the `getattr`-with-default pattern so any pre-1.81 fixture that hand-constructs a parent_args without these flags still works.

### Notes

- **No new behavior; new CLI surface + inner-Namespace plumbing.** The scoring pipeline reads these fields the same way it did in 1.80.0; only the standalone CLIs grew arguments and the inner-Namespace builder grew forwarders. The flags are now symmetric across `shard_runner shard`, `calibration_survey.py`, and `calibrate_thresholds.py`.
- **Unblocks the MAGE Tier 3+4 bake-off** via the standalone `calibration_survey.py` path. The bake-off matrix is 4 embedding aliases × 3 surprisal aliases at 5K stratified subsample; the standalone CLI is the appropriate entry point for that scale (sharded toolchain is overkill for 5K records).
- **Test suite status**: 24 passed in `test_pipeline_model_wiring.py` (17 pre-existing 1.80.0 + 7 new in 1.81.0).

## [1.80.0] - 2026-05-16

**Calibration pipeline: pluggable Tier 3 embedding model + Tier 4 surprisal wiring.** Closes two wiring gaps discovered during MAGE Tier 3+4 bake-off planning:

1. **W1 — Tier 3 embedding model.** `embedding_backend.py` shipped a pluggable wrapper with 4 aliases (`mxbai`, `gemma`, `harrier`, `minilm`) on 2026-05-11 but `variance_audit.adjacent_sentence_cosine` was hardcoded to `all-MiniLM-L6-v2`. `shard_runner shard` accepted `--embedding-model` and recorded it in `state.json` since the same date but no scorer in the calibration pipeline ever read it.
2. **W2 — Tier 4 surprisal in the calibration pipeline.** `variance_audit.py --tier4 --surprisal-model` had been operator-facing since 1.61.0, but the calibration pipeline entry points (`shard_runner shard`, `validation_harness.score_smoothing_entry`, `calibrate_thresholds.score_corpus`) didn't accept any Tier 4 toggles. MAGE / RAID Tier 4 calibration would have required a parallel ad-hoc scoring loop bypassing the new streaming + checkpointed infrastructure (1.75.0–1.79.3).

This PR threads both through the full chain: `shard_runner shard` flags → `state.json["task_params"]` → `task_surfaces._score_shard_calibration_survey` → `_default_scorer` → `load_or_score_corpus` → `score_corpus` → `score_smoothing_entry` → `audit_text` → `adjacent_sentence_cosine` (Tier 3) and `_tier4_surprisal_block` (Tier 4).

### Added

- **`variance_audit.py --embedding-model ALIAS --embedding-revision SHA`** — alias resolves via `embedding_backend.MODEL_ALIASES` (`mxbai`, `gemma`, `harrier`, `minilm`) or accepts a full HuggingFace id. Default is `None` (preserves the legacy `all-MiniLM-L6-v2` hardcode for back-compat with pre-1.80 surveys).
- **`adjacent_sentence_cosine(sentences, *, embedding_model, embedding_revision)`** — new kwargs. When set, routes through `_get_embedding_backend()` which constructs and caches an `EmbeddingBackend` per `(alias, revision)`. The method string in the return dict echoes the resolved model id; new fields `embedding_model`, `embedding_alias`, `embedding_revision` for downstream provenance audits. When unset, falls through to the legacy MiniLM → TF-IDF chain unchanged.
- **`_get_embedding_backend(alias, revision)`** helper in `variance_audit.py` — module-level cache keyed on `(alias, revision)` so a scoring loop pays the backend-construction cost once across thousands of documents.
- **`_get_surprisal_backend(alias, revision)`** helper, same shape — module-level cache for Tier 4 LM backends.
- **`shard_runner shard --tier4 --no-tier4 --surprisal-model --surprisal-revision`** — pipeline equivalents of the standalone `variance_audit.py --tier4` flags. Default `--tier4` is off (back-compat).
- **Threaded params on `score_smoothing_entry`** (validation_harness): `do_tier4`, `embedding_model`, `embedding_revision`, `surprisal_model`, `surprisal_revision`. All default to `None` / `False` so pre-1.80 callers keep their bit-exact behavior.
- **Threaded params on `audit_text`** (variance_audit): `embedding_model`, `embedding_revision`, `surprisal_model`, `surprisal_revision`. The existing `do_tier4`, `tier4_score_fn`, `tier4_backend` params are unchanged; when `surprisal_model` is supplied AND no explicit `tier4_backend` was passed, `_tier4_surprisal_block` constructs one via the cached `_get_surprisal_backend`.
- **`scoring_meta` schema extension** in `score_corpus` (both the completed final write AND the in-progress `flush_every` checkpoint after the codex P2 fix below): emits `do_tier4`, `embedding_model`, `embedding_revision`, `surprisal_model`, `surprisal_revision` so the records cache invalidates correctly when an operator changes any of these between runs.
- **`cache_is_compatible` extension** — refuses the cache on any of the 5 new fields mismatching. Pre-1.80 caches (no `do_tier4` key, no `embedding_model` key) are still treated as compatible when the current args also use the defaults — operators upgrading without changing their CLI invocation keep their cached records.
- **`task_params` schema extension** for `calibration_survey` in `cmd_shard`: stores all 5 new fields under `state.json["task_params"]`. Pre-1.80 partial wiring stored `embedding_model` / `embedding_revision` as top-level state.json fields; the task surface honors both via a two-arg `task_params.get(..., run_context.get(...))` fallback (task_params wins).
- **17 new regression tests** in `scripts/tests/test_pipeline_model_wiring.py`:
  - W1 CLI surface (1): `variance_audit.py --embedding-model` exposed.
  - W1 leaf behavior (2): legacy path produces the pre-1.80 method string; new path delegates to `embedding_backend` and echoes the resolved model id.
  - W1 `audit_text` threading (1): `embedding_model` kwarg reaches `adjacent_sentence_cosine`.
  - W2 `audit_text` threading (1): `surprisal_model` kwarg reaches `_tier4_surprisal_block`.
  - Validation_harness threading (1): `score_smoothing_entry` accepts and forwards all 5 new kwargs.
  - Cache compat (4): invalidates on `embedding_model`, `tier4`, `surprisal_model` changes; preserves back-compat with pre-1.80 caches.
  - `shard_runner shard` parser (2): accepts the new flags; defaults match pre-1.80 behavior.
  - `task_surfaces` threading (3): `_score_shard_calibration_survey` forwards the new kwargs to `DEFAULT_SCORER`; honors the pre-1.80 top-level `state.json` fallback for embedding_model; task_params wins over the legacy fallback when both are set.
  - **Codex P2 partial-cache guards (2, added in the P2 fix commit)**: in-progress flush meta carries the 5 new fields so resume works after a crash; end-to-end resume round-trip with non-default models engages the resume path (only the remaining entries re-scored).

### Fixed

- **Codex P2 on PR #77: `score_corpus` in-progress flush `interim_meta` now mirrors all 5 new fields.** The completed `scoring_meta` write at the end of `score_corpus` emitted `do_tier4` + 4 model fields, but the in-progress `flush_every` checkpoint inside the scoring loop only emitted `tier2`/`tier3`. A long `--tier4` / `--embedding-model` scoring run that crashed mid-loop and was resumed would have read its own partial cache, failed `cache_is_compatible` because the new keys were absent, and silently re-scored from scratch — exactly the failure mode that bites the expensive bake-off paths this PR enables. Fix mirrors the same 5 fields in `interim_meta` via the same `getattr`-with-default pattern. Two new regression tests pin the fix; the primary guard test was verified to fail without the fix.

### Changed

- **Existing DEFAULT_SCORER stubs in tests** (`test_hardened_aggregator.py::_stub_scorer_with_signals`, `test_shard_runner.py::_stub_scorer`, `test_sharded_smoke_pipeline.py::_stub_scorer`, `test_task_surfaces.py::_stub`): each grew `**_extra` to absorb the new 1.80 kwargs the dispatcher now passes. Stubs themselves don't act on the new fields — that behavior is tested against the real scorer in `test_pipeline_model_wiring.py`.

### Notes

- **Default behavior preserved.** Without any new flags, the calibration pipeline produces bit-identical Tier 1+2+3 records to 1.79.3: Tier 3 uses MiniLM, Tier 4 is off, no model aliases land in `scoring_meta`. Operators must explicitly opt in to swap models or enable Tier 4.
- **The default embedding model is NOT being changed.** The `embedding_backend.DEFAULT_MODEL = "mxbai"` constant reflects the framework's intentional choice for new audits, but the calibration scoring path keeps MiniLM as its default until the §6.4 fixture test runs on the operator's register mix. The MAGE Tier 3 bake-off this PR unblocks is part of that fixture-test work.
- **Pre-1.80 top-level state.json fields preserved.** `shard_runner shard` continues to write `embedding_model` / `embedding_revision` as top-level keys in `state.json` (it had been doing this since 2026-05-11). The task surface reads from both `task_params` (new) and the top-level fields (legacy) so state.json files written by pre-1.80 `shard_runner shard` invocations keep their configured embedding model when re-run under 1.80+.
- **Test suite status**: 51 passed across `test_pipeline_model_wiring.py` + `test_incremental_corpus_scoring.py` + `test_calibration_cache.py`. Existing calibration-pipeline tests pass with 9 unrelated pre-existing failures (Windows POSIX `ps -o lstart=` PID-tracking tests).
- **Unblocks**: MAGE Tier 3+4 model-selection bake-off (4 embedding aliases × 3 surprisal aliases on a 5K stratified subsample, then a full MAGE Tier 3+4 re-score with the winning configs).

## [1.79.3] - 2026-05-16

**Fix: `test_pdf_inventory_extract.py` test-helper drift.** The 1.70.0 PR added three new CLI flags to `pdf_inventory.py` (`--no-incremental-cache`, `--flush-every`, `--refresh-partial`) and the production code at line 858-860 reads `args.no_incremental_cache`, `args.flush_every`, and `args.refresh_partial`. The test helper `make_inventory_args()` was never updated to match, so all 9 tests that exercise the inventory CLI path failed with `AttributeError: 'Namespace' object has no attribute 'no_incremental_cache'`.

### Fixed

- **`make_inventory_args()` in `test_pdf_inventory_extract.py`** now sets the three 1.70.0 incremental-cache fields in its default base dict (`no_incremental_cache=False`, `flush_every=25`, `refresh_partial=False`), matching `pdf_inventory.build_arg_parser`'s defaults. Test suite: 9 failing → 27 passing.
- **Helper docstring** updated to flag the parser-sync contract so the next CLI flag addition doesn't re-drift the helper silently.

### Notes

- The bug landed in PR #69 (1.70.0, "partial-cache + resume + crash-recovery") and went unnoticed because the failures looked like a stack-trace from production code rather than a test fixture problem — `pdf_inventory.py:858` is the first line of the trace, not the test helper. The fix is a one-line addition to a dict literal.

## [1.79.2] - 2026-05-16

**Chore: pre-public-flip cleanup of test fixtures.** Audit before flipping the GitHub repo from PRIVATE to PUBLIC surfaced two test_data files that should not ship publicly:

### Changed

- **`scripts/test_data/capybara_article.md` removed.** The 188-line essay named a real person (Santiago Schnell) and made public-facing AI-authorship claims about a published essay. Moved out of the repo into the gitignored `internal/drafts/` tree. Nothing in the test suite read this file; it was load-bearing only for an out-of-tree primer draft.
- **`scripts/test_data/validation_smoke_manifest.jsonl`** swapped the dangling `capybara_article.md` reference for `federalist_oracle/01_hamilton_federalist_01.txt` (Federalist #1, Hamilton, 1787 — Project Gutenberg eBook #18, public domain). The Federalist serves the same role: a pre-AI human positive control with a non-fiction policy-advocacy register. The validation harness still produces the expected `pre_ai_human` → `Lightly smoothed` band on this entry.
- **`scripts/test_data/human_sample.txt`** replaced with a ~500-word excerpt from Willa Cather's *My Ántonia* (1918, US public domain pre-1929). The prior file was literary fiction prose of uncertain provenance; the maintainer could not confirm authorship, so it carried IP risk for a public flip. Cather's prose preserves the terse-literary-fiction register that pairs pedagogically with the AI-smoothed `ai_sample.txt`.
- **`.claude-plugin/marketplace.json` version field synced.** The marketplace.json had been stale at 1.66.0 since the 1.67.0 bump landed; the plugin.json version field tracks releases but the marketplace.json had drifted. Synced to 1.79.2 to match plugin.json. Future bumps should touch both files.

### Notes

- The 9 `test_pdf_inventory_extract.py` failures on the current main (`AttributeError: 'Namespace' object has no attribute 'no_incremental_cache'` at `pdf_inventory.py:858`) are pre-existing and unrelated to this cleanup. They appear when running the test suite against main with the same args.Namespace construction the tests use. Filed for separate triage; not blocking the public flip since the failures are local to a CLI argparse contract, not a security/IP concern.

## [1.79.1] - 2026-05-16

**Fix: `--refresh-cache` now actually refreshes.** Codex P2 on PR #68. The flag bypassed the *complete-cache hit* return path in `load_or_score_corpus` but did not flow through to `score_corpus`, which unconditionally read the partial cache and resumed from it. So `--refresh-cache` against an `in_progress` cache silently kept the prior partial's records and only re-scored the missing tail — the opposite of what the operator asked for.

### Fixed

- **`score_corpus(refresh: bool = False)` parameter added.** When `refresh=True`, the function unlinks the existing partial cache file (if any) before the scoring loop and skips the resume-from-partial read block. Without the unlink, a crash mid-refresh would leave a partial that interleaved the discarded prior run's first N records with the new pass's first M-N — a worse state than the bug it was meant to fix.
- **`load_or_score_corpus` now plumbs `refresh` into `score_corpus`.** Single-line change at the call site; preserves the default-False contract so all pre-existing callers (which never passed `refresh`) keep their behavior.

### Added

- **3 new regression tests** in `test_incremental_corpus_scoring.py` under "REFRESH-CACHE × PARTIAL-RESUME":
  - `test_refresh_cache_discards_partial_resume` — pins the fix: refresh=True against an in_progress partial triggers a full re-score, not a 2-of-5 resume.
  - `test_refresh_cache_unlinks_prior_partial` — pins the unlink semantics: a sentinel record planted in the partial does NOT appear in the post-refresh records.
  - `test_refresh_cache_default_false_preserves_resume` — regression guard that the 1.79.0 resume contract still works when refresh=False (we didn't accidentally rip out the resume path).

## [1.79.0] - 2026-05-16

**Incremental corpus-scoring cache + resume.** Stacked on the 1.78.0 streaming-pair-extraction branch. Closes the same all-or-nothing failure mode in `calibrate_thresholds.score_corpus` that the 1.76.0 PR closed for the aggregator: a long scoring run that crashes mid-loop loses everything because the cache is written only once at the end.

This PR applies the same checkpoint + resume pattern to the scoring path that 1.76.0 applied to the aggregator path. Together with the prior four PRs, the calibration toolchain now has uniform divide / measure / save-progress / conquer affordances across every long-running step.

### Added

- **Atomic incremental cache writes** in `score_corpus`: every `--records-cache-flush-every N` entries (default 100), the in-flight records list is written to the `--records-cache` path with `status: "in_progress"`. Crash mid-scoring loses at most `N` entries of work. Atomic (tmp + rename) so a crash mid-write doesn't corrupt the cache.
- **Resume from partial cache**: when `load_or_score_corpus` finds an `--records-cache` with `status: "in_progress"` and compatible `scoring_meta` (same manifest SHA, same tier flags, same use filter), it loads the prior records, builds the set of already-scored entry IDs, and passes them to `score_corpus` which skips those entries during the loop. Threshold-sweep-only re-runs continue to use the `status: "complete"` cache-hit fast path unchanged.
- **`status` field on the cache** (`"in_progress"` or `"complete"`). Backward compat: pre-1.79.0 caches lack the field and are treated as `"complete"` (the prior behavior), so existing caches keep working as full-cache hits.
- **Rate + ETA in progress log**: per-flush log line now includes entries-per-second and minutes-to-completion. At MAGE / RAID scoring scale (hours), the ETA is the difference between "is this hung?" and "still chewing."
- **`--records-cache-flush-every N` CLI flag** on both `calibration_survey.py` and `calibrate_thresholds.py` standalone CLIs. Default 100. Lower (10-50) for tier3 runs where per-entry scoring is slow and crash exposure is high; higher (500+) for tier1-only runs where per-entry scoring is fast and flush I/O would dominate.
- **10 new regression tests** in `scripts/tests/test_incremental_corpus_scoring.py`: partial-cache written every N entries (1), final cache status=complete (1), atomic-write no-tmp contract (1), resume skips already-scored entries (1), resume preserves carried-forward records unchanged (1), incompatible partial discarded (1), pre-1.79 caches without status treated as complete — back-compat (1), progress log includes rate + ETA (1), CLI flag on both standalone parsers (2). Existing 159 calibration + aggregator tests pass unchanged.

### Notes

- The `entry_id` used for the skip-check is constructed by a new `_entry_id_for_record` helper that mirrors the entry-id construction in `validation_harness.score_smoothing_entry` (~line 169). The two must produce identical IDs from the same entry dict; the helper's docstring flags this contract so future drift is easier to catch.
- The incremental writes go to the SAME path as the final cache — no `.partial` suffix dance. The `status` field is the canonical "is this complete?" signal.
- `score_corpus` accepts `partial_cache_path` + `flush_every` kwargs directly. `load_or_score_corpus` wires them from `args.records_cache_flush_every`. Programmatic callers that build their own args dict and want the new behavior should add `records_cache_flush_every` to the namespace (or accept the 100 default).
- **The five-PR stack as a whole** (1.75.0 + 1.76.0 + 1.77.0 + 1.78.0 + 1.79.0) takes the calibration toolchain from "ship MAGE in 8h26m IF you don't crash, RAID infeasible at any speed" to "MAGE in 6.6 seconds aggregated + minutes scored, RAID scoring crash-recoverable, RAID aggregating feasible on consumer hardware." Each PR is one operational principle applied to one bottleneck:
  - 1.75.0 — Divide (pre-extract pairs in main, dispatch via thread/process/SharedMemory)
  - 1.76.0 — Measure + Save progress (per-signal logging, atomic partial JSON, resume) for the **aggregator path**
  - 1.77.0 — Conquer (O(n log n) sweep_threshold dispatch)
  - 1.78.0 — Stream (corollary of divide when RAM-bound)
  - **1.79.0 — Save progress (atomic incremental cache, resume) for the scoring path** — this PR
- The next-tier candidates (`manuscript_audit.py`, `validation_harness.py`, `pdf_inventory.py`, `editlens_to_manifest.py`, `voice_drift_tracker.py`) already landed as wave 2 (PRs #69–#73 at 1.70.0–1.74.0).
- Originally landed as 1.69.0; renumbered to 1.71.0 (first rebase), then to 1.79.0 (second rebase after wave 2 PRs #69–#73 landed).

## [1.78.1] - 2026-05-16

**Fix: streaming aggregate errors on unreadable shard caches by default.** Codex P2 on PR #66. The 1.78.0 streaming pre-extraction loop caught `Exception` on each shard's `json.load`, wrote a stderr warning, and silently `continue`d. An operator with a corrupted shard cache got calibration thresholds derived from a strict subset of the records they thought they were calibrating against — with no error signal and a misleading `n_records` count in the survey JSON.

### Fixed

- **`task_surfaces._aggregate_calibration_records` streaming branch** now collects unreadable shard paths into a list and, after the loop, raises `SystemExit` if any shard was dropped. The error message names the count, lists up to 5 paths, and points operators at the new `--allow-unreadable-shards` opt-in flag for the cases where partial aggregation is intentional (e.g., one shard is being re-scored asynchronously and the operator wants a preliminary survey from the rest).
- **`shard_runner aggregate --allow-unreadable-shards`** new flag, default `False` (strict). Opts into the old skip-and-warn behavior with one important change: the dropped-shard list lands in `aggregator_perf.pair_extraction_shards_unreadable` (with per-entry `path`, `error_type`, `error_message`) so the survey JSON carries an explicit audit trail. `pair_extraction_shards_streamed` is now the count that *successfully* streamed — excludes the dropped ones — and `pair_extraction_shards_unreadable_count` records the dropped tally.

### Added

- **4 new regression tests** in `test_streaming_pair_extraction.py` under "UNREADABLE SHARD HANDLING":
  - `test_streaming_errors_when_shard_unreadable` — pins the default-strict behavior: SystemExit with both "unreadable" and "--allow-unreadable-shards" in the message.
  - `test_streaming_allow_unreadable_records_audit_trail` — pins the opt-in path: aggregation proceeds AND the dropped shards land in perf metadata with full per-entry detail.
  - `test_streaming_clean_run_records_zero_unreadable` — regression guard that a healthy run records `unreadable_count=0` and does NOT bloat the perf block with an empty list field.
  - `test_allow_unreadable_shards_flag_exists` — pins the CLI surface.

## [1.78.0] - 2026-05-16

**Streaming pre-extraction: unblocks RAID-scale calibration on consumer hardware.** Stacked on the 1.77.0 sweep-threshold-fast branch.

The 1.75.0 hardened-aggregator PR moved pre-extraction into the parent process to avoid pickling the records list to every worker. That trade is correct at MAGE scale (436K records × ~5 KB ≈ 2 GB resident — fine on a 32 GB machine), but breaks at RAID scale (8.3M records ≈ 40 GB resident — OOM on any consumer machine). This PR adds an opt-in streaming mode that reads shard caches one at a time, extracts per-signal pairs incrementally, and discards the records before reading the next shard.

### Added

- **`--stream-pair-extraction` flag** on `shard_runner aggregate`. Default off (preserves the 1.75.0+ in-memory pre-extraction behavior). When on, `cmd_aggregate` collects validated shard cache paths instead of materializing records, and passes them to the surface via a new `shard_cache_paths` kwarg.
- **Streaming pre-extraction in `_aggregate_calibration_records`**: iterates `shard_cache_paths`, reads each cache, extracts per-signal pairs via `validation_harness.collect_signal_records`, accumulates into per-signal pair lists, and drops the shard records before reading the next. Peak parent RSS bounded by `(largest_shard_records_bytes + sum_of_per_signal_pair_arrays)` — at RAID scale ~1-2 GB instead of ~40 GB.
- **`shard_cache_paths` kwarg on the surface contract** (calibration + corpus_hygiene). Calibration uses it for streaming; corpus_hygiene accepts it for surface uniformity but doesn't yet stream (warns + degrades gracefully when --stream-pair-extraction is passed; supports a separate follow-up streaming PR for that task surface).
- **`aggregator_perf.pair_extraction_mode`**: `"streaming"` or `"in_memory"`. Plus `pair_extraction_shards_streamed` count for streaming mode. Auditable from the survey JSON without re-running.
- **Fallback handling in `cmd_aggregate`**: if a surface doesn't accept `shard_cache_paths` (older surface, third-party plugin), `cmd_aggregate` catches the TypeError, logs a clear warning, and retries without the kwarg. Operator's signal to either drop --stream-pair-extraction or upgrade the surface.
- **7 new regression tests** in `scripts/tests/test_streaming_pair_extraction.py`: CLI flag (1), cmd_aggregate streaming dispatch passes cache paths (1), in-memory-vs-streaming per-signal parity on the same input (1), one-shard-at-a-time loading verification via spy on `json.load` (1), streaming perf-metadata in survey JSON (1), in-memory perf-metadata symmetry (1), corpus_hygiene graceful degradation (1).

### Notes

- The legacy records-list dispatch path is **unavailable in streaming mode** — there's no records list to fall back to. Signals whose pair extraction fails return as `no usable pairs` errors. In practice this only affects test fixtures with stub specs; production signals (real `signal_path` strings against real `per_signal_scores` columns) extract cleanly.
- For now, **`--resume` + `--stream-pair-extraction` compose**: the partial JSON's existing `per_signal` entries are carried forward, and only the missing signals' pairs need to be streamed.
- The streaming pass walks all shards once and discards records each time. CPython's reference-counted GC reclaims the discarded records before the next cache loads (verified empirically via process memory inspection during dev). At pathologically large per-shard sizes (e.g., one shard with 10M records out of a 100M-row corpus), the per-shard peak still bounds RSS — re-shard the source manifest if a single shard is too big.
- corpus_hygiene streaming is a separate PR. The hygiene aggregator's per-record summary doesn't fit the per-signal-pairs model cleanly; it would need a different streaming primitive (probably a sum-reducer pattern: read shard, fold into running totals, drop shard).
- **The four-PR stack as a whole** (1.75.0 + 1.76.0 + 1.77.0 + 1.78.0) takes the sharded calibration toolchain from "MAGE in 8h26m, RAID infeasible" to "MAGE in 6.6s, RAID feasible on a 32 GB consumer machine." Each PR is a single principle from the operations playbook applied to the aggregator: divide (1.75.0 layer 1), measure (1.76.0 per-signal logging), save progress (1.76.0 checkpoint + resume), conquer (1.77.0 O(n log n) sweep), and the corollary that follows when divide-and-conquer meets RAM limits (1.78.0 streaming).
- Originally landed as 1.68.0; renumbered to 1.70.0 (first rebase), then to 1.78.0 (second rebase after wave 2 PRs #69–#73 landed).

## [1.77.0] - 2026-05-16

**O(n log n) `sweep_threshold` dispatch: unblocks MAGE / RAID-scale calibration.** Stacked on the 1.76.0 checkpointed-aggregate branch.

The 1.75.0 hardened-aggregator PR closed the wiring gap between PRs #53 / #55 / #60 and `shard_runner aggregate`, and the 1.76.0 checkpointed-aggregator PR made the parallel sweep restartable. The combined stack let the operator KICK OFF a MAGE aggregate cleanly with the new fast bootstrap stack engaged — and then revealed the actual MAGE-scale bottleneck was upstream of everything those PRs sped up: `sweep_threshold` itself runs in O(n × unique_scores) ≈ O(n²) and took >70 minutes per signal at the MAGE Tier 1+2 corpus's 338K positive pairs. The numpy bootstrap speedup never had a chance to matter because the sort-and-scan pre-bootstrap pipeline was already dominating.

This PR makes `sweep_threshold` dispatch to an O(n log n) sort-and-scan implementation for large corpora, while preserving the original O(n × k) loop for small corpora (bit-exact backward compatibility with all pre-1.77.0 test fixtures).

### Added

- **`_sweep_threshold_fast` (O(n log n))** in `scripts/calibration/calibrate_thresholds.py`. Sort pairs by score in the direction-relevant order, walk in sorted order maintaining cumulative TP / FP counters, snapshot the operating point at each unique score. One sort + one walk. At MAGE scale (n=338K) returns in **<0.3s** vs. **>70 min** for the loop path — empirically measured 2026-05-16, **≥16,000× speedup**.
- **`_sweep_threshold_loop`** (renamed): the original O(n × k) implementation, preserved bit-exactly. Still used for `n < _SWEEP_THRESHOLD_FAST_DISPATCH_N` (currently 5000). Continues to emit the verbose `candidates` log on failure — useful for small-corpus debugging; suppressed on the fast path to avoid 100K-8M rows of survey-JSON bloat at MAGE / RAID scale.
- **`sweep_threshold` (public)** now dispatches between the two based on `len(pairs)`. The public API + return shape is unchanged for the happy path; only the failure-case `candidates` log is omitted on the fast path.
- **17 new regression tests** in `scripts/tests/test_sweep_threshold_fast.py`: dispatch constant exists (1), small-n uses loop path (1), bit-exact loop behavior at n=500/1500/4000 across both directions (6), fast-vs-loop operating-point equivalence at n above dispatch threshold (2 directions × 1 = 2), tied-score handling (2), single-class / sub-resolution / unreachable-FPR failure parity (3), wall-clock guarantee (< 1s at n=50K, 2 directions = 2).

### Empirical validation

- MAGE Tier 1+2 (n_records=436,606 across 22 cached shards) aggregate now runs end-to-end via `shard_runner aggregate` in **6.6 seconds** (vs. the prior 8h26m kill on the loop engine + multiple post-1.65.0 attempts that ran for hours without completing a single signal). The full survey JSON ships with `status: "complete"`, all 17 signals reported, and the `aggregator_perf` block recording `bootstrap_engine: numpy`, `pair_extraction_signals_fast_path: 17`, `resumed_from_partial: True`, `resumed_signal_count: 5`, `sweep_s: 6.558`.
- Of the 17 MAGE signals: 4 produce valid thresholds (mattr da_AUC=0.5755, mtld 0.5642, yules_k 0.5604, shannon_entropy 0.5174); 5 polarity-invert at sub-chance da_AUC (mdd_sd 0.4198, burstiness_B 0.4540, fkgl_sd 0.4788, sentence_length_sd 0.4793, connective_density 0.4935); 8 lack the per_signal_scores entries (tier3 + tier4 + AIC-8/9 columns not computed in Tier 1+2 runs). The polarity-inversion gate caught the inversions cleanly; the "Why no verdict" framework posture is doing its job.

### Notes

- The dispatch threshold (`_SWEEP_THRESHOLD_FAST_DISPATCH_N = 5000`) is conservative — the fast path is ~2000× faster even at n=15K, where production was already taking 1-15s per signal. The choice of 5000 prefers preserving bit-exact loop semantics on existing fixtures over chasing the last marginal speedup at small scale. Lower it if the loop path's per-call cost starts mattering on a workload.
- The fast-path operating point matches the loop-path within float epsilon on TPR / FPR / precision. Threshold value may differ in the last decimal at tied-score boundaries (continuous data has no ties; tied-score tests verify both paths land on valid operating points respecting the FPR target).
- The fast path's failure case omits the per-threshold `candidates` log emitted by the loop path. Nothing downstream consumes that log; at MAGE / RAID scale it would be 100K-8M dicts of survey JSON bloat. If a future operator needs per-threshold diagnostics at large scale, the right tool is a dedicated diagnostic script that calls the loop path on a subsample, not bloating every survey.
- A natural follow-up: `_ranking_metrics` (which computes raw AUC for the polarity-inversion gate) has not been profiled at MAGE scale, but appears fast enough (the standalone test on 30K pairs returned in 0.03s). If a future corpus exposes a similar bottleneck there, the same sort-and-scan pattern applies.
- Originally landed as 1.67.0; renumbered to 1.69.0 (first rebase), then to 1.77.0 (second rebase after wave 2 PRs #69–#73 landed).

## [1.76.0] - 2026-05-16

**Checkpointed aggregator: per-signal incremental save + resume.** Stacked on the 1.75.0 hardened-aggregator PR. Closes the asymmetry where `shard_runner work` is sharded + restartable + monitorable, but `shard_runner aggregate` was monolithic + silent + all-or-nothing. The motivation is psychological as much as technical — a 30-min aggregate that crashes on signal 14 of 17 should not lose the first 13 signals' work, and an operator watching the run should see progress as it lands, not silence followed by a single "written" line.

### Added

- **`--out`-driven incremental save**: when the aggregator's `--out` is set (the standard operator flow), the calibration task surface writes a partial JSON to `--out` after every per-signal completion, with `status: "in_progress"`. Atomic write via tmp + rename so a crash mid-write doesn't leave a corrupted file. Operators can `cat` the partial mid-run to see which signals are done.
- **`status` field on the survey JSON**: `"in_progress"` during the sweep, `"complete"` on the final return. Downstream consumers can distinguish a finished run from a checkpoint.
- **`--resume` flag (default ON) + `--no-resume`**: when `--out` exists with parseable per_signal state, the aggregator carries forward the prior entries (success or error) and dispatches only the remaining signals. `--no-resume` forces a fresh sweep regardless of any prior partial — useful when the prior partial is from a stale registry / different task_params and you want to regenerate every signal.
- **Resume metadata in `aggregator_perf`**: `resumed_from_partial: true` and `resumed_signal_count: N` so post-hoc audits can tell which run did what work.
- **Idempotent re-run against `complete` payload**: re-running aggregate against an `--out` whose status is already `"complete"` carries forward every signal and dispatches nothing — a no-op that doesn't waste compute. Pass `--no-resume` to force regeneration.
- **Corrupt-prior-partial tolerance**: an unparseable `--out` (truncated JSON, hand-edited garbage) doesn't break the run; the aggregator logs the parse failure and starts fresh.
- **9 new regression tests** in `scripts/tests/test_checkpointed_aggregator.py`: parser flag surface (1), partial-save trajectory observed via spy on `_save_aggregator_partial` (1), atomic-write contract (1), final-status-complete contract (1), resume-skips-prior-signals (1), `--no-resume`-forces-fresh (1), resume-from-complete-is-idempotent (1), corrupt-partial-tolerance (1), perf-block-records-resume (1). Re-uses the synthetic-signals fixture from `test_hardened_aggregator.py`.

### Notes

- The checkpoint write happens after EVERY signal completion in both serial and parallel paths. At MAGE / RAID scale (kilobyte payload + atomic rename) the per-signal save cost is well under 50 ms, negligible compared to the per-signal bootstrap.
- Resume is signal-name keyed, not content-keyed. If the registry changes between runs (a signal added or its `signal_path` changed), the new signals will be dispatched fresh; the old entries remain. To force a complete regeneration after a registry change, pass `--no-resume` or delete `--out` first.
- A future PR could checkpoint at sub-signal granularity (e.g., per-bootstrap-chunk) to cover the case where a single signal takes longer than the operator's patience window. For now, signal-level granularity matches the natural unit operators reason about.
- Originally landed as 1.66.0 on a branch stacked under the wave-1 numbering cluster; renumbered to 1.68.0 (first rebase), then to 1.76.0 (second rebase after wave 2 PRs #69–#73 landed).

## [1.75.0] - 2026-05-16

**Hardened parallel aggregator: closes the wiring gap PRs #53 / #55 / #60 left open, with four-layer memory + concurrency defenses.**

The new fast bootstrap stack (numpy engine in #53, `--aggregate-workers` parallelism in #55, optional torch+ROCm backend in #60) only flowed through `calibration_survey.py`'s standalone CLI — the `shard_runner aggregate` dispatch path didn't expose the new flags or propagate them into the per-signal Namespace. The MAGE Tier 1+2 re-aggregate against 22 cached shards (436K records) sat single-core on the loop engine for 8h26m before the operator killed it. This release wires the gap and hardens the parallel path against the silent OOM-and-zombie pattern the prior runs hit when 11 process workers each pickled the 436K-record list (~25 GB total on a 31 GB host, no Windows crash event, parent process appeared alive but workers wedged with no progress).

### Added

- **Four new flags on `shard_runner aggregate`** — `--bootstrap-engine {loop,numpy,torch}`, `--bootstrap-chunk-size`, `--bootstrap-device`, `--aggregate-workers N`, `--executor {thread,process}`, `--max-worker-rss-gb F`. The calibration task surface reads them off the args namespace via `getattr` (back-compat preserved for all existing callers).
- **Layer 1 (Belt) — pre-extract per-signal pairs in main.** `task_surfaces._aggregate_calibration_records` now walks the records list once in the parent process to extract `(label, score)` pairs per signal via `validation_harness.collect_signal_records`. Workers receive only the pairs for their signal — at MAGE scale that's ~4 MB per signal vs. ~2 GB for the full records list (>99% memory drop). Workers fall back to the legacy records-list path per-signal when pre-extraction fails (bad `signal_path` shape, missing `per_signal_scores` column, etc.) — so existing test fixtures that use stub specs continue to pass.
- **Layer 2 (Suspenders) — `--executor thread` (default).** ThreadPoolExecutor shares the parent's address space; the pre-extracted pair lists never copy or pickle. NumPy releases the GIL during the bootstrap inner loop, so per-signal CIs overlap on multiple cores in principle. (In practice the GIL-holding sweep / polarity-gate work caps effective parallelism well below the requested worker count — when GIL contention dominates, switch to `--executor process`.)
- **Layer 3 (Buttons) — `--executor process` with `multiprocessing.shared_memory`.** ProcessPoolExecutor + SharedMemory keeps one physical copy of each signal's pair arrays across workers; tasks pickle only the SharedMemory names + a small Namespace dict (< 1 KB regardless of corpus size). New helpers `_allocate_shared_pair_arrays` and `_derive_one_via_shared_memory` handle the int8-labels + float64-scores layout, with `try`/`finally` cleanup that close + unlink every block to prevent /dev/shm leaks (POSIX) or named-object leaks (Windows).
- **Layer 4 (Zip) — adaptive worker cap from `psutil`.** New `_cap_workers(requested, executor_kind, max_rss_gb)` helper inspects `psutil.virtual_memory().available` and floor-divides by per-worker RSS estimates (0.5 GB/thread, 0.6 GB/process) to pick a safe count. Combined with the user-imposed `--max-worker-rss-gb` budget. Without psutil the cap honors the requested count and prints a one-line warning. The cap is what prevents the silent OOM-and-zombie pattern that ate 8h+ of MAGE wall-clock before this PR.
- **`aggregator_perf` block in the survey JSON** — every aggregate run now records what actually executed: `executor`, `bootstrap_engine`, `requested_workers`, `capped_workers`, `worker_cap_reason`, `pair_extraction_s`, `pair_extraction_signals_fast_path`, `pair_extraction_signals_legacy_path`, `sweep_s`, `n_signals_dispatched`. Operators can audit a run after the fact without re-running.
- **`pre_extracted_pairs` kwarg on `derive_threshold_from_records`.** Optional `list[tuple[int, float]]` that bypasses the per-call `collect_signal_records` walk. Existing callers that don't set it see no behavior change. Documented as the parallel-aggregator fast-path entry point.
- **`psutil>=5.9` as a soft requirement** in `requirements-calibration.txt`. Without it the adaptive worker cap is inactive (with a one-line warning); the rest of the aggregator works unchanged.
- **16 new regression tests** in `scripts/tests/test_hardened_aggregator.py`: CLI surface (parser accepts new flags + rejects unknown values, 4 tests); Layer 1 pre-extraction (engine propagation, fast-path dispatch, legacy fallback on extraction failure, 3 tests); Layer 2 thread executor end-to-end (1 test); Layer 3 SharedMemory round-trip + empty-list edge case (2 tests); Layer 4 cap-workers under psutil-says-yes / psutil-says-no / budget-cap / floor-at-1 / psutil-missing (5 tests); aggregator_perf block contract (1 test).

### Notes

- The 9 PID-tracking tests in `test_shard_runner.py` (`TestPidReuseIdentityCheck`, `test_terminate_all_signals_active_pid`, `test_kill_all_uses_sigkill`, `test_sweep_stale_*`, `test_refresh_claim_file_makes_resumed_worker_signalable`) fail on Windows because they depend on POSIX `ps -o lstart= -p PID`. These failures pre-date this PR and are unrelated to the aggregator changes; the fix is a separate cross-platform `process_start_time_epoch` shim.
- The default `--executor thread` was chosen over `process` because (a) thread workers don't pay spawn-time pickle, which is the largest cost on Windows, and (b) the GIL contention that caps effective thread parallelism is acceptable when memory is tight, while the SharedMemory + spawn overhead of process workers becomes the bottleneck only on hosts with abundant RAM. Operators trading CPU for memory should switch to `process`; operators trading memory for CPU should stay on `thread`.
- The `aggregator_perf.executor`, `pair_extraction_signals_fast_path`, and `pair_extraction_signals_legacy_path` fields make it easy to audit whether the new fast path actually engaged for a given run — a regression where the legacy records-list path is silently re-engaged on every signal will show up as `pair_extraction_signals_fast_path: 0` in the survey JSON.
- Originally landed as 1.65.0 on a branch stacked under main's pre-rebase 1.65.x/1.66.0 cluster; renumbered first to 1.67.0 (on rebase to main at 1.66.0), then to 1.75.0 on second rebase after the five wave-2 PRs (#69–#73) merged to main under 1.70.0 through 1.74.0.

## [1.74.0] - 2026-05-16

**voice_drift_tracker: per-doc feature cache + per-period progress log.** Independent PR. Applies MEASURE + SAVE PROGRESS to the script that ran `extract_features(text)` on every doc on every invocation — and silently re-extracted everything when an operator re-ran with a different `--period-granularity` against the same baseline.

### Added

- **`--feature-cache PATH`**: opt-in per-doc feature cache keyed by absolute doc path. Stores the raw `extract_features` output verbatim. Reused across runs: re-running with a different `--period-granularity` / `--period-boundaries` against the same baseline now re-uses every per-doc extraction.
- **`--feature-cache-flush-every N`** (default 25): atomically flushes the cache every N freshly-extracted docs. Crash mid-extraction loses at most N extractions.
- **`--refresh-feature-cache`**: discards any prior cache and re-extracts.
- **Per-period progress log** to stderr (always on, no flag required): `[Xs] period N/M 'label': K doc(s) (J from cache); overall D/T (R/s, ETA Y min)`. Operators running a multi-minute drift report see what period the loop is on.
- **6 new regression tests** in `scripts/tests/test_voice_drift_tracker_cache.py`: cache written during extraction (1), resume skips already-cached docs (1), refresh re-extracts (1), no-cache back-compat (1), per-period progress log to stderr (1), CLI flags exposed (1).

### Notes

- The cache key is the doc's absolute path. If a doc moves between runs (same content, different path), it'll be re-extracted; pass `--refresh-feature-cache` after major reorganizations.
- The cache stores `extract_features`'s raw output (a nested dict of `features` + `summary`), not a digest — re-loading is O(1) per doc.
- 30 pre-existing tests in `test_voice_drift_tracker.py` pass unchanged.
- Originally landed as 1.70.0 on a wave-2 branch; renumbered to 1.74.0 on rebase as the last of the five wave-2 PRs to land (#69-#73 took 1.70.0 through 1.73.0 sequentially).

## [1.73.0] - 2026-05-16

**editlens_to_manifest: append-mode + resume + periodic flush + conversion-settings sidecar.** Independent PR. Applies SAVE PROGRESS + full settings-compat check to the EditLens parquet → JSONL manifest converter.

### Added

- **Resume on by default**: when `--out` exists with parseable entries, reads existing IDs, opens in `'a'`, skips matching rows. **Conversion-settings sidecar** at `<out>.meta.json` records every conversion arg (label_map, text_column, label_column, preset, register, language_status, notes_columns, mixed_composite_states, use_tags, source_label). Resume refuses if any arg differs — mixing rows under different conversion semantics in the same manifest is now impossible.
- **`--no-resume` / `--refresh-output`**: bypass the sidecar check.
- **`--flush-every N`** (default 1000): periodic OS-buffer flush + stderr progress with rate + row index.
- **Corrupted-prior protection**: malformed line in existing `--out` → clean overwrite.
- **11 regression tests** in `scripts/tests/test_editlens_to_manifest_resume.py`: original 6 (resume / no-resume / refresh / corrupted-partial / flush / fresh) + 5 from codex P2 fix (sidecar written / refused on label_map / refused on register / refresh bypasses / accepted when sidecar matches).

### Notes

- The `_stable_id(source_basename, row_index)` function is the resume key. If the source row order changes between runs (re-shuffled CSV), resume will silently skip rows by stale IDs — pass `--refresh-output` after any source change.
- Default behavior is RESUME ON. Operators who want pre-1.70.0 overwrite behavior pass `--no-resume`.
- Originally landed as 1.70.0 on a wave-2 branch; renumbered to 1.73.0 on rebase since #69 / #70 / #71 merged first.

## [1.72.0] - 2026-05-16

**pdf_inventory: partial-cache + resume.** Independent PR touching only `pdf_inventory.py`. Applies SAVE PROGRESS to a script that was already parallel (ThreadPoolExecutor with `as_completed`) but accumulated results in memory and only wrote the JSONL after every worker completed — a crash mid-run lost everything.

### Added

- **Partial-inventory cache** at `<output>.partial.json` (path-keyed dict of `asdict(InventoryEntry)`). Written atomically every `--flush-every` worker completions; deleted after the final JSONL lands. Default ON.
- **`--no-incremental-cache`** to revert to pre-1.70.0 monolithic behavior.
- **`--flush-every N`** (default 25) to tune flush cadence.
- **`--refresh-partial`** to discard any existing partial and re-classify everything.
- **Resume contract**: paths already in the partial cache are skipped during the next run — the expensive `classify_pdf` call doesn't fire for them. Determinism preserved (final JSONL emitted in input-path order regardless of worker completion order).
- **8 new regression tests** in `scripts/tests/test_pdf_inventory_incremental.py`.

### Notes

- Compatibility check on the partial cache: `max_file_bytes` (the one knob that affects which paths are classifiable). Other knobs (workers, verbose) don't affect classification outputs; changing them mid-run is fine.
- The partial cache stores `asdict(entry)` (preserving None fields), not the trimmed `entry.to_dict()` — the round-trip preserves enough to reconstruct InventoryEntry. The final JSONL uses the trimmed `to_dict()`.
- Originally landed as 1.70.0 on a wave-2 branch; renumbered to 1.72.0 on rebase since #69 and #70 merged first under 1.70.0 / 1.71.0.

## [1.71.0] - 2026-05-16

**Manuscript audit: per-chapter cache + resume + progress log.** Independent PR touching only `manuscript_audit.py`. Applies MEASURE + SAVE PROGRESS to the chapter-level audit loop, plus full per-chapter text-hash + preprocessing compat check (post-codex P2 fix).

`audit_manuscript` ran `audit_text` on every chapter sequentially with no per-chapter visibility or checkpoint. On a 50-chapter manuscript with tier3 on, one chapter can take minutes (spaCy + signal computation); a crash on chapter 40 of 50 lost the first 39's audit work.

### Added

- **`--chapter-audit-cache PATH` CLI flag** + **`--refresh-chapter-cache`**.
- **Per-chapter progress log** to stderr.
- **Per-chapter text_hash** stored with each cached audit; resume keys on `(label, text_hash)` so editing a chapter under the same label invalidates the cached audit.
- **Preprocessing-args compat check** (do_tier2, do_tier3, allow_non_prose, strip_rules, strip_aggressive): mismatch refuses the cache with a clear stderr reason.
- **`_chapter_text_hash` + `_chapter_cache_compat_reason` helpers**.
- **11 regression tests** in `scripts/tests/test_manuscript_audit_cache.py`: CLI flags, partial write, resume, refresh, no-cache back-compat, stderr/stdout discipline, **plus 4 from codex P2 fix**: edited chapter invalidates entry, cache refused on allow_non_prose / strip_rules / strip_aggressive mismatch.

### Notes

- The cache stores `extract_features`-shaped per-chapter audits; complete-cache hits re-render dashboards at different `--baseline-dir` values without re-running spaCy.
- Originally landed as 1.70.0 on a wave-2 branch independent of the calibration stack; renumbered to 1.71.0 on rebase since #69 (validation_harness scoring checkpoint) merged to main first under 1.70.0.

## [1.70.0] - 2026-05-16

**Validation harness: progress log + optional scored-records cache with resume.** Independent PR (touches `validation_harness.py` only).

The validation harness scores every manifest entry in a single list-comprehension and only emits output at the end. At MVP-validation scale (~hundreds of entries) that's fine; at corpus-validation scale (8M-row corpora the framework now ships toolchain for) it's the same all-or-nothing failure mode the calibration stack addressed for `calibrate_thresholds.score_corpus`. Applies the same two principles (MEASURE + SAVE PROGRESS) to the validation surface.

### Added

- **`_score_validation_entries_with_progress` helper** in `validation_harness.py`: replaces the in-place list-comp at `run_harness` with a for-loop that (a) logs progress every N entries with rate + ETA, and (b) optionally writes a partial scored-records cache atomically every N entries with `status: "in_progress"`, then flips status to `"complete"` on the final write.
- **`--scored-records-cache PATH` CLI flag**: when set, the helper loads any prior partial cache, derives the set of already-scored entry IDs, and skips those entries during the loop. **Full compatibility check (post-codex P2 fix)**: manifest SHA + corpus text fingerprint + mattr_window + allow_non_prose + strip_rules + strip_aggressive + label maps. Mismatch on any field refuses the cache. Pass `--refresh-scored-records-cache` to override.
- **`--scored-records-flush-every N` CLI flag** (default 100).
- **`--refresh-scored-records-cache` CLI flag** to discard any existing cache and re-score from scratch.
- **All progress + cache logging routes to stderr**, not stdout — keeps `--json` output on stdout parseable for downstream consumers.
- **Local `_vh_manifest_content_hash` + `_vh_corpus_text_fingerprint`** helpers (mirroring `calibrate_thresholds`'s helpers but local to avoid a cross-script dependency).
- **`_scored_records_compat_reason()` helper** that returns `None` when the cache_meta is compatible or a one-line human-readable reason on mismatch.
- **13 new regression tests** in `scripts/tests/test_validation_harness_scored_cache.py` covering CLI surface, save-progress + resume, full compat check (manifest SHA, corpus fingerprint, mattr_window, strip_rules, label_map), back-compat for pre-fix caches, stderr-not-stdout contract.

### Notes

- The helper's `_entry_id_for_validation_record` mirrors the entry-id construction in `validation_harness.score_smoothing_entry` (~line 169) and the corresponding helper in `calibrate_thresholds._entry_id_for_record`. All three must produce identical IDs from the same entry dict or resume silently re-scores. Documented as a contract; consider extracting to a shared helper in a follow-up.
- Default behavior (no cache flag) is unchanged: list-comp → records list, plus the new stderr progress log. Pre-1.70.0 operator scripts keep working.

## [1.66.0] - 2026-05-15

**Calibration-status retier: four-tier taxonomy replaces the binary calibrated/provisional split.** The framework's old `ThresholdSpec.provisional: bool` collapsed corpus-tested, locally-experimented, literature-anchored, and pure-heuristic signals into one bucket. The v1.60.1 glossary's `status: calibrated` label drifted on signals whose anchor text literally said "Provisional." Per `internal/SPEC_calibration_status_retier.md`, this release ships a four-tier status enum (plus structural_only for downstream-feeding signals) with per-tier provenance conventions, coordinated across code and glossary.

### Changed

- **`ThresholdSpec` schema migration** (`scripts/variance_audit.py`):
  - `provisional: bool` replaced by `status: Literal["calibrated", "literature_anchored", "empirically_oriented", "heuristic", "structural_only"]`. Default value: `"heuristic"`.
  - `__post_init__` invariant updated: `calibrated`, `literature_anchored`, `empirically_oriented` all require a non-None `provenance` slug citing the appropriate source (corpus / publication / local source respectively). `heuristic` requires `provenance is None`. The old "calibrated XOR provisional" check is generalized.
  - New module-level constant `THRESHOLD_STATUS_VALUES` enumerates the allowed values.
  - **Backward-compat**: `ThresholdSpec.provisional` is preserved as a derived `@property` returning `True` for any non-calibrated, non-structural status. Existing code reading `spec.provisional` keeps the same operational meaning (calibrated → False, everything else → True). JSON-emit sites now include both `status` (new) and `provisional` (deprecated alias) so downstream consumers can migrate at their own pace.

- **New per-tier helper functions**: `signals_by_status(status)`, `literature_anchored_signals()`, `empirically_oriented_signals()`, `heuristic_signals()`. `calibrated_signals()` and `provisional_signals()` continue to work; their semantics now derive from the new status field.

- **21 COMPRESSION_HEURISTICS entries retiered**:
  - 5 → `literature_anchored`: `mattr` (literary-fiction baseline), `shannon_entropy` (native-fiction literature), `surprisal_mean` / `surprisal_sd` / `surprisal_acf_lag1` (all three Tier 4 signals share the DivEye anchor: Basani & Chen, TMLR 2026).
  - 6 → `empirically_oriented`: `burstiness_B`, `sentence_length_sd`, `adjacent_cosine_sd`, `fkgl_sd`, `mdd_sd`, `connective_density` (all six measured locally in `references/calibration-findings-2026-05-10.md` against EditLens v1; per the Stylometry-to-the-people policy these stay un-promoted to calibrated, but the local da_AUC measurements anchor the bands).
  - 10 → `heuristic`: `mtld`, `yules_k`, `adjacent_cosine_mean`, and the 7 AIC-7/8/9 entries (correctio, triplet, manifesto cadence, professional parallel stack, kicker, image conjunction, prestige metaphor scatter).
  - **Plus `POS_BIGRAM_KL_HEURISTIC` → `literature_anchored`** (distributional-diagnostics anchor).

- **`scripts/calibration/calibrate_thresholds.py` operator hint** — the operator-facing string that shows how to promote a signal to calibrated now writes `status = 'calibrated'` instead of `provisional = False`.

- **`scripts/calibration_drift_monitor.py`** — JSON-emit sites surface the new `status` field alongside the backward-compat `provisional` flag.

- **`scripts/variance_audit.py` JSON-emit sites** (the `thresholds_used` block) — same treatment: both `status` and `provisional` fields emitted.

### Rewritten

- **`references/signals-glossary.md`** — full rewrite from the v1.60.1 1004-line semi-pedagogical form to a 528-line terse-reference form. 56 entries, each with metadata line (signal path · family · polarity · status · provenance note) and a one-paragraph definition. No worked examples, no interpretive prose — long-form pedagogy moves to the framework's external primer (Glass-Box Stylometry Sequence in development). Front-matter legend documents the four-tier status taxonomy. Totals table at the end shows the calibration-status distribution.

### Added

- **`README.md` link to the glossary restored.** PR #49 (1.60.2) had removed the link because the in-repo glossary was being deprecated in favor of the maintainer's private primer iteration. Under the new posture (terse reference in-repo, long-form primer external), the link is appropriate again.
- **README family inventory updated** from 49 signals to 56 — adds the AIC-7 / AIC-8 / AIC-9 family bullets that landed in PRs #57-#62 but weren't in the README's barebones inventory.
- **5 new regression tests** in `test_variance_audit_tier4.py`:
  - `TestAic789Registration::test_v1_66_0_retier_schema_invariants` — pins the per-tier provenance contract.
  - `TestAic789Registration::test_v1_66_0_distribution` — pins the 0 calibrated, N literature, M empirical, K heuristic distribution; surfaces drift.
  - `TestAic789Registration::test_v1_66_0_backward_compat_provisional_property` — `.provisional` keeps working.
  - `TestAic789Registration::test_v1_66_0_invalid_status_raises` — constructor rejects typos.
  - `TestCompressionHeuristicsTier4::test_all_three_are_literature_anchored` (renamed from `test_all_three_are_provisional`) — pins the Tier 4 → DivEye anchor.

### Spec doc

- **`internal/SPEC_calibration_status_retier.md`** — status updated from "Draft for maintainer review" to "SHIPPED in v1.66.0". Implementation-time corrections documented: the schema was bool→string-enum (not an enum-rename); no anchor field added to code; glossary rewrite over per-entry retier; empirical retier for the six EditLens-anchored variance signals; Tier 4 all → literature_anchored per maintainer's DivEye call.

### Migration

- **Code reading `spec.provisional`** continues to work unchanged (backward-compat property).
- **Code reading `spec.status` directly** needs to use the new string values (no migration needed for fresh code).
- **JSON consumers** see both `status` (new) and `provisional` (kept) fields. Migrate at your own pace.
- **Operators calibrating signals** via `scripts/calibration/calibrate_thresholds.py` get an updated operator-facing hint telling them to set `status = 'calibrated'` rather than `provisional = False`.

### Notes

- This is a MINOR bump even though it touches the `ThresholdSpec` shape, because the backward-compat `provisional` property preserves the old API. Operators with code that strictly checks `isinstance(spec.provisional, bool)` need to verify; everyone else is fine.
- Per the Stylometry-to-the-people policy, no signal is in the `calibrated` tier. The §5.4 calibration corpus track is the named promotion path: `heuristic` → `calibrated` (or `empirically_oriented` → `calibrated`) as labeled-corpus data lands.
- The retier is an honesty pass, not new empirical work. No threshold values change. No signal added or removed. Same 56 signals before and after; only the labels are tightened.

## [1.65.1] - 2026-05-15

**Rewrite `register_typical.yaml`'s header to honestly describe the values' epistemic status.** The 1.64.0 header called the bands "illustrative starting points derived from spec defaults plus literature anchors." That's an overstatement — the per-register numerical values aren't grounded in published literature. The maintainer flagged the framing while iterating their out-of-repo primer; this PATCH brings the in-repo file into alignment with the honest framing the primer will adopt.

### Changed

- **`baselines/register_typical.yaml` header comment block** — rewritten to disambiguate what IS research-grounded in the framework (Brysbaert 2014 concreteness norms; spaCy GloVe vectors; WordNet hypernyms; the Schnell case-study anchor for correctio at 15.8/1000 words) from what ISN'T (the per-register bands themselves, which are plausibility-set placeholders combining spec-author intuition with band widths chosen to avoid immediate false positives on normal prose). The relative-ordering claims across registers (academic > literary fiction; technical near zero; blog > essay) are now explicitly framed as intuitive hypotheses that a register-aware calibration corpus would test.

### Notes

- Documentation only. No code changes. No behavior changes. The YAML's actual numerical values are unchanged; only the explanatory comment block changes.
- All entries continue to carry `provisional: true` / `provenance: null` per the Stylometry-to-the-people policy. The §5.4 calibration corpus task in `ROADMAP.md` is the work that would replace the bands with empirically-grounded values.
- The rewrite explicitly cites the four real research / empirical anchors in the framework so the maintainer's primer and any future operator-facing docs can be honest about epistemic status: cite Brysbaert et al. 2014 for concreteness; cite the Schnell anchor for correctio (with the "single-author case study" caveat); don't cite the YAML's per-register bands as published findings.

## [1.65.0] - 2026-05-15

**AIC-7 / AIC-8 / AIC-9 integration into `variance_audit.py` — closes spec Step 10.** The detectors shipped as standalone CLIs in 1.61.0-1.64.0, but `audit_text()` didn't invoke them and `COMPRESSION_HEURISTICS` couldn't carry their signal paths without orphaned-registry entries (Codex P2 finding on PR #59, resolved in 1.64.1 by removing the entries). This release ships the proper wiring: opt-in `--aic7` / `--aic8` / `--aic9` flags, registry entries that resolve cleanly, three new ablation families.

### Added

- **Three new opt-in CLI flags** on `variance_audit.py`:
  - `--aic7`: enables the regex-only Discourse Leak / Assistant-Register Intrusion detector (`aic_pattern_audit.all_patterns`). Cheap; only needs the existing regex stack.
  - `--aic8`: enables the Aesthetic Authority Laundering detectors (image conjunction + prestige metaphor). Requires spaCy with `en_core_web_md` or `_lg` for word vectors + the Brysbaert concreteness norms (ship in-repo). Falls back to `available: False` with install hint when dependencies are missing.
  - `--aic9`: enables the Closure Inflation detector (kicker density). Regex with optional spaCy POS check; cheapest of the three to enable.

- **Three new block helpers in `variance_audit.py`**:
  - `_aic7_named_pattern_block(text)` — runs `aic_pattern_audit.all_patterns()` and reshapes to `patterns.<key>.density_per_1k`.
  - `_aic8_image_prestige_block(text)` — runs `image_conjunction_density` + `prestige_metaphor_density`, surfaces both at `aic_8_9.image_conjunction_density.*` and `aic_8_9.prestige_metaphor_density.*`.
  - `_aic9_kicker_block(text)` — runs `kicker_density.kicker_density()`, surfaces at `aic_8_9.kicker_density.value`. Lazy spaCy load with regex fallback.

- **Three new parameters on `audit_text()`**: `do_aic7`, `do_aic8`, `do_aic9` (all default `False`, opt-in per the `--tier4` precedent). When set, the relevant helper runs and its block lands at the spec-registered path so `classify_compression()` walks it automatically.

- **Seven new `COMPRESSION_HEURISTICS` entries** (all `provisional=True`, `provenance=None`):
  - **AIC-7**: `correctio_density`, `triplet_density`, `manifesto_cadence_density`, `professional_parallel_stack_density` (signal_paths under `patterns.*`). Per the spec's Step 10 part 2 backfill.
  - **AIC-8**: `image_conjunction_density`, `prestige_metaphor_scatter` (signal_paths under `aic_8_9.*`). Re-added after 1.64.1's removal; now wired.
  - **AIC-9**: `kicker_density` (signal_path under `aic_8_9.*`). Re-added after 1.64.1's removal; now wired.

  Registry size: 14 → 21. All 21 signals remain provisional (calibrated-signals invariant preserved).

- **Three new ablation families in `_ABLATION_SIGNAL_FAMILIES`**:
  - `assistant_register_intrusion` — bundles the four AIC-7 signals (combined weight 4.0 when `--aic7` is on).
  - `closure_inflation` — wraps the single AIC-9 signal (weight 1.0). Kept separate from `aesthetic_authority_laundering` so ablation distinguishes "the kicker shape was load-bearing" from "the image conjunctions were."
  - `aesthetic_authority_laundering` — bundles the two AIC-8 signals (combined weight 2.0).

  Total ablation families: 6 → 9.

- **`classify_compression()` walks the new signal paths** under the same length-floor + threshold contract as Tier 1-3 and Tier 4. The check loop names each AIC-7/8/9 signal explicitly (the wiring contract Codex flagged on PR #59).

### Changed

- **`TestAic89RegistryGuard` (negative-assertion guard introduced in 1.64.1) replaced with `TestAic789Registration`** (positive-assertion suite). The class docstring documents the wiring contract that must hold for the entries to remain in the registry: signal_paths resolve via `audit_text()` blocks; ablation families name them; classifier walks them.

### Added (tests)

- **`TestAic789Registration` (11 tests)**: AIC-7/8/9 signals registered with expected signal_paths; all provisional; three new ablation families exist with expected membership; no signal orphaned from ablation (invariant test that catches the 1.64.0 mistake from re-occurring).
- **`TestAic789AuditTextWiring` (3 tests)**: `audit_text(do_aic7/8/9=True)` populates the expected blocks; no-flag default omits them.
- **`TestAic789ClassifierWiring` (5 tests)**: signals enter `available_signals` when their flag is set; kicker fires on high-density fixture; ablation family appears in per-family results; no AIC flag means no AIC signal in available (contract guard).

### Notes

- This release closes spec Step 10 in full. Steps 1-12 + 15 are now complete in this repo. Steps 13 (`signals-glossary.md` three new entries) and 14 (APODICTIC framing) remain out-of-scope per earlier maintainer decisions.
- Per the Stylometry-to-the-people policy, all seven new thresholds ship provisional. The §5.4 calibration corpus (idiom negatives + AI-image-conjunction positives + aphoristic essayist negatives + AI-rewrite positives) is the roadmap follow-on that would replace provisional values with empirically-grounded thresholds. Documented in ROADMAP since 1.61.0.
- Operators using `--aic7` get the Discourse Leak detector at minimal cost (regex-only). `--aic8` adds the spaCy + vectors + Brysbaert + WordNet stack and is the heaviest of the three. `--aic9` is cheap (regex with optional POS check). Each flag is independently opt-in; combining them is supported.
- Empirical caveat (documented since 1.61.0 / 1.63.0): the spec's starting thresholds for AIC-8 (T1 = 2.5 concreteness gap, T2 = 0.4 cosine similarity, T3 = 0.7 scatter entropy) don't crisply separate idioms from AI positives on Brysbaert data. The compound diagnostic with cosine similarity carries the load, but calibration is needed before the bands are operational.
- Test suite: **2202 passed, 16 skipped, 0 failed** (was 2129 on main; +19 new tests across `TestAic789Registration` / `TestAic789AuditTextWiring` / `TestAic789ClassifierWiring`).

## [1.64.1] - 2026-05-15

**Codex P2 review of the AIC-8/9 wave: typed dependency errors + remove orphaned registry entries.** Fixes four P2 findings on PRs #58 and #59. None of the bugs are blockers, but all four would bite operators in predictable ways: the three CLI scripts dumped a Python traceback instead of the clean install hint when no spaCy model was installed, and the three `COMPRESSION_HEURISTICS` entries added in 1.64.0 were registered without classifier or ablation wiring — reproducing the Tier-4 wiring-failure pattern that PR #31 fixed.

### Fixed

- **`scripts/image_conjunction.py::_load_spacy_with_parsing()` now raises `EmbeddingsBackendError`** instead of a generic `RuntimeError`. The CLI's existing `try/except embeddings.EmbeddingsBackendError` block now catches the no-spaCy-model failure mode at the same exit path the no-vectors-model failure mode uses. Codex P2: operator without `en_core_web_sm` installed previously saw a traceback; now sees the typed-error install hint and exits with code 2.
- **`scripts/image_conjunction.py::main()` moves the `_load_spacy_with_parsing()` call inside the `try/except`** block. Both the load and the audit run under the same handler; either failure mode produces the install-hint exit.
- **`scripts/prestige_metaphor.py::main()` same fix.** The prestige-metaphor CLI calls `image_conjunction._load_spacy_with_parsing()` and inherits the typed-error behavior automatically; the loader call is now inside the CLI's `try/except` for symmetry with the image-conjunction CLI.
- **`scripts/aesthetic_authority_audit.py::main()` adds a `try/except` block that was missing entirely.** The compound CLI previously had no exception handler around the spaCy load AND the compound audit run; both now route through the typed-error exit. Adds `import embeddings` (was missing) for the exception type.
- **`scripts/variance_audit.py` `COMPRESSION_HEURISTICS` registry**: removes the three orphaned entries added in 1.64.0 (`kicker_density`, `image_conjunction_density`, `prestige_metaphor_scatter`). Registry size returns to 14 (same as 1.63.0). Codex P2: the three entries were registered with `signal_path` values that walked into `aic_8_9.*` keys, but `audit_text()` never emits those keys (the AIC-8/9 detectors are standalone) AND there was no corresponding ablation family. A future band call that relied on these signals would have reported `is_robust_call=True` because ablation never removed them. The fix matches Codex's recommendation: keep the entries out of the shared heuristic registry until classification is intended. The removal block carries an explanatory comment naming the wiring requirements (audit_text emission + ablation family) that re-registration would need to satisfy.

### Added

- **5 new regression tests**:
  - `test_image_conjunction.py::test_load_spacy_raises_typed_error_when_no_model` — pins the typed-error contract of `_load_spacy_with_parsing()` directly.
  - `test_image_conjunction.py::test_cli_clean_exit_when_no_spacy_model` — subprocess-isolated end-to-end test that the CLI exits with code 2 and no traceback when no spaCy model is installed.
  - `test_prestige_metaphor.py::test_cli_clean_exit_when_no_spacy_model` — parallel for the prestige-metaphor CLI.
  - `test_aesthetic_authority_audit.py::test_cli_clean_exit_when_no_spacy_model` — parallel for the compound CLI.
  - `test_variance_audit_tier4.py::TestAic89RegistryGuard` (4 tests) — guards against re-registering the AIC-8/9 entries in `COMPRESSION_HEURISTICS` without simultaneously wiring `audit_text()` emission + an ablation family. The class docstring documents the wiring requirements and notes that the tests can be deleted and replaced with positive registration tests when integration lands.

### Notes

- Behavior change: operators without `en_core_web_md` installed who run `scripts/image_conjunction.py`, `scripts/prestige_metaphor.py`, or `scripts/aesthetic_authority_audit.py` now see a clean error message and exit code 2 instead of a Python traceback. This matches the framework's behavior pattern for other typed dependency errors (surprisal backend, embedding backend).
- No new functionality. The AIC-8/9 detectors still work the same way when their dependencies are present; only the failure-mode behavior changes.
- The AIC-8/9 integration into `variance_audit.py --aic8` / `--aic9` (which would re-justify the `COMPRESSION_HEURISTICS` entries) remains a future PR per the spec's Step 10 wiring requirements. That work would add: (a) CLI flags + `do_aic8: bool` / `do_aic9: bool` parameters to `audit_text()`; (b) lazy import of the detectors when flags are set; (c) `aic_8_9.*` blocks emitted under the audit dict; (d) re-add `COMPRESSION_HEURISTICS` entries; (e) add `aesthetic_authority_laundering` ablation family; (f) update the `TestAic89RegistryGuard` tests to positive registration tests.

## [1.64.0] - 2026-05-15

**AIC-8 / AIC-9 wave complete: compound audit + register-typical baselines + documentation.** Fourth and final PR implementing `internal/SPEC_aic_8_9_implementation.md`. Closes out the four-PR wave (1.61.0 foundation → 1.62.0 AIC-9 → 1.63.0 AIC-8 → 1.64.0 compound + docs). The framework's craft-restoration surface now has named operational signals for Aesthetic Authority Laundering and Closure Inflation, plus a compound audit that surfaces the joint co-occurrence pattern the spec calls "the canonical AI-prose closing move."

### Added

- **`scripts/aesthetic_authority_audit.py`** — compound audit that runs kicker_density + image_conjunction + prestige_metaphor in a single pass and computes joint co-occurrence metrics: `kicker_with_image_conjunction_rate`, `kicker_with_prestige_metaphor_rate`, `all_three_co_occurrence_rate` (proportion of paragraphs ending with a kicker that ALSO contain an image conjunction classified into a hardcoded prestige domain — the strongest single AIC-8/9 signature). Standalone CLI with `--register`, per-signal explicit baselines, threshold tunables.
- **`baselines/register_typical.yaml`** — register-typical default baselines for the four AIC-8/9 signals (`kicker_density`, `image_conjunction_per_1000_tokens`, `prestige_metaphor_per_1000_tokens`, `domain_scatter_entropy`) across six register categories (`contemporary_essay`, `literary_fiction`, `hard_science_fiction`, `academic_prose`, `blog_post`, `technical_documentation`). All entries ship `provisional: true` / `provenance: null` per the Stylometry-to-the-people policy. Operators wanting load-bearing thresholds run their own calibration locally.
- **`baselines/hard-science-fiction/` and `baselines/technical-documentation/`** — two new register subdirectories (matching the existing 5: academic-philosophy, blog-essay, literary-fiction, personal, testimony-policy) for operator-populated personal baselines in those registers. Each ships an empty README explaining the intended structure.
- **`scripts/register_typical_baselines.py`** — YAML loader for the register-typical baselines. Public API:
  - `available_registers(yaml_path=None)` → sorted list of register names.
  - `get_baseline(register, signal, *, yaml_path)` → full baseline dict (mean / sd / band / provisional / provenance).
  - `get_baseline_mean(register, signal, *, yaml_path)` → mean float, convenience for callers that only need the central tendency.
  - `resolve_baseline(register, signal, *, explicit_value, explicit_source, yaml_path)` → dict with `value` + `source`, applying the precedence rule (explicit > register-typical > None). Used by the compound audit to thread baselines through to the component detectors.
- **`references/laundering-vocabulary.md`** — new consolidated reference doc for the four laundering moves (calibration / procedural / audit / image-aesthetic-authority). Documents the unifying rhetorical mechanism (deploy a surface form historically marking completed work, exploit reader deference to the form) and cross-references the originating posts plus the AIC-8 operationalization. Per spec Step 15.
- **49 new regression tests** across `test_register_typical_baselines.py` (20 tests) and `test_aesthetic_authority_audit.py` (9 tests): YAML loader contract (parse / typed errors / missing keys), `get_baseline` lookups (case-insensitive / unknown registers / unknown signals), `resolve_baseline` precedence (explicit > register > None), shipped-YAML integration (6 registers × 4 signals × all-provisional invariant), compound joint-metric math (rates in [0, 1], all-three ≤ pairwise rates), register-based baseline resolution, explicit-override-wins, no-register-no-baselines, diagnostics-populated, CLI smoke.

### Changed

- **`scripts/variance_audit.py` `COMPRESSION_HEURISTICS` registry** — adds three new entries: `kicker_density` (signal_path `aic_8_9.kicker_density.value`, direction `gt`, threshold 0.25), `image_conjunction_density` (signal_path `aic_8_9.image_conjunction_density.value`, direction `gt`, threshold 15.0 per 1000 tokens), `prestige_metaphor_scatter` (signal_path `aic_8_9.prestige_metaphor_density.domain_scatter_entropy`, direction `gt`, threshold 0.7 normalized entropy). All three ship `provisional=True` with `provenance=None`; the registry's "0 of N signal thresholds carry calibration provenance" invariant is preserved (17 of 17 provisional). The thresholds are the spec's starting values; the §5.4 calibration corpus is roadmap work.
- **`references/aic-flags.md`** — adds three substantial new blocks per spec Step 11:
  - "The Variance-and-Frequency Reframing" note above AIC-1, documenting the policy shift from earned-versus-unearned-as-primary-diagnostic to frequency-elevation-as-primary-diagnostic (with source triage as the secondary contextual check).
  - Full AIC-8 entry (Aesthetic Authority Laundering) after AIC-7, with image-conjunction and prestige-metaphor named subtypes, distinguished-from notes, severity bands.
  - Full AIC-9 entry (Closure Inflation) after AIC-8, with kicker-density subtype, severity bands.
  - Genre Tolerance Quick Reference table extended with AIC-8 + AIC-9 rows and three new footnotes (⁷, ⁸, ⁹).
  - Pattern Synthesis: Flag Compounds extended with three new compound entries (AIC-8 + AIC-9 at paragraph ends; AIC-8 + AIC-7 in interiority; AIC-9 + Layer A "Heavily smoothed").
  - "The seven flag families" header updated to "The nine flag families."
- **`references/source-triage.md`** — adds "Source triage as secondary check (variance-and-frequency reframing, 2026-05-15)" section at the top per spec Step 12. Documents the shift in posture (elevation is the diagnostic; source triage refines per-instance). Adds per-instance source-triage rules for the three new patterns (image conjunctions, prestige metaphors, kicker density). The existing Five Named Patterns (fiction) + Five Patterns Parallel Set (nonfiction) per-pattern rules retain their earned/unearned framing as the secondary refinement layer.
- **`plugins/setec-voiceprint/requirements.txt`** — adds `pyyaml>=6.0` as a hard requirement (for the register-typical baselines loader). Updated commentary block.

### Documented (out of scope, per the spec)

- **APODICTIC framing update** (spec Step 14) lives in a separate project. The framework-side rhetorical-bankruptcy framing for AIC-8 / AIC-9 (replacing the earned-versus-unearned justification prompt) is now in `aic-flags.md`; APODICTIC consumers apply it on their side.
- **`signals-glossary.md` three new entries** (spec Step 13) intentionally not landed in this PR. Per the 1.60.2 split, the in-repo glossary is a stable v1.60.1 snapshot; the maintainer iterates a separate longer-form primer on a private track. The new entries for `kicker_density`, `image_conjunction`, `prestige_metaphor` go into that primer rather than into the orphaned repo snapshot.

### Notes

- This release closes out the four-PR AIC-8/9 wave. Operators can now run `scripts/aesthetic_authority_audit.py --register contemporary_essay path/to/draft.md` to get the full AIC-8 + AIC-9 + joint-metrics report in a single call.
- Signals remain `status: "provisional"` per the Stylometry-to-the-people policy. The §5.4 calibration corpus (idiom negatives + AI-image-conjunction positives + aphoristic essayist negatives + AI-rewrite positives) is the roadmap follow-on that would replace provisional thresholds with empirically-grounded values.
- Test suite: 2121 passed, 4 skipped, 0 failed (was 2092 on main; +29 net new tests across the two new test files).

## [1.63.0] - 2026-05-15

**AIC-8 Aesthetic Authority Laundering: image-conjunction and prestige-metaphor detectors.** Third of four PRs implementing `internal/SPEC_aic_8_9_implementation.md`. Two detectors that work together:

- **Image conjunction** flags abstract-concrete word pairs at elevated density relative to a baseline. The canonical pattern: "the machinery of grief", "the architecture of attention", "lowercase love". Uses a compound filter: concreteness gap (Brysbaert 2014 norms, T1 default 2.5) AND embedding cosine similarity (spaCy GloVe vectors, T2 default 0.4). The compound is critical because concreteness gap alone catches conventional idioms ("heavy burden", "sharp decline"); compound + low similarity isolates the deliberate-juxtaposition pattern.
- **Prestige metaphor** classifies each image conjunction's scaffolding word into a prestige domain (architecture / grammar / cartography / ecology / machinery / weather / ritual / infrastructure / topology / geology / economy / music / theater / mathematics / biology / navigation / geometry / choreography), then computes the normalized Shannon entropy of the domain distribution. High entropy + elevated density = "metaphor confetti" diagnostic (the prestige-metaphor signature).

### Added

- **`plugins/setec-voiceprint/scripts/image_conjunction.py`** — AIC-8 image-conjunction detector. Public API:
  - `extract_candidate_pairs(doc)` → iterator of `(word_a, word_b, relation)` tuples. Walks spaCy dependency parse for five relation patterns: `amod` (ADJ → NOUN), `compound` (NOUN → NOUN), `nsubj_verb` (subject → non-copular verb), `attr` (predicate complement), `prep_of` ("X of Y" genitive).
  - `evaluate_pair(word_a, word_b, relation, *, t1, t2, concreteness_path)` → dict if pair passes both filters, else None.
  - `image_conjunction_density(text, *, nlp, t1, t2, ...)` → JSON-ready dict matching spec §6 schema. Density per 1000 tokens, spacing variance (SD of inter-conjunction paragraph distances), paragraph-final co-occurrence rate (AIC-9 cross-tie), per-conjunction diagnostics, optional baseline comparison.
  - Standalone CLI with `--t1`, `--t2`, `--baseline`, `--baseline-source`, `--out` flags.
- **`plugins/setec-voiceprint/scripts/prestige_metaphor.py`** — AIC-8 prestige-metaphor detector. Composes on `image_conjunction.py`. Public API:
  - `classify_domain(word, *, use_wordnet, extra_domains)` → prestige-domain name or None. Lookup order: operator-supplied `extra_domains` → hardcoded `PRESTIGE_DOMAIN_VOCAB` (~50 entries covering the spec's 18 domains plus derived forms: architectural, grammatical, topological, etc.) → WordNet hypernym chain at level 3-4 from root (catches the long tail).
  - `_normalized_shannon_entropy(counts)` → entropy of distribution in [0, 1]. 1.0 = uniform spread; 0.0 = concentrated.
  - `prestige_metaphor_density(text, *, nlp, t1, t2, t3, use_wordnet, extra_domains, ...)` → JSON-ready dict. Reports `value` (prestige-metaphor density per 1000 tokens), `domain_scatter_entropy`, `domain_distribution`, `flag_fires` (the joint diagnostic: entropy > T3 AND density > baseline if provided), per-conjunction `scaffolding_word` + `target_word` + `domain`.
  - Standalone CLI with `--t1`, `--t2`, `--t3`, `--no-wordnet`, `--baseline`, `--baseline-source`, `--out` flags.
- **Three new synthetic test fixtures** in `scripts/test_data/aic_8_9/`:
  - `idiom_negative.md` — conventional collocations (heavy burden, sharp decline, simple machine).
  - `ai_image_conjunction_positive.md` — canonical AI image conjunctions (machinery of grief, architecture of attention, grammar of desire, topology of memory).
  - `concentrated_metaphor_negative.md` — image conjunctions concentrated around the machinery domain (tests scatter-entropy ordering: should be LOWER than the AI fixture's scatter).
- **41 new regression tests** across `test_image_conjunction.py` (20 tests) and `test_prestige_metaphor.py` (21 tests): pair-evaluation compound filter, threshold tuning, ImageConjunction dataclass (abstract/concrete property resolution, immutability), spacing variance edge cases, end-to-end fixture integration (gated on `en_core_web_md` + `en_core_web_sm`), JSON schema completeness, baseline comparison, classifier hardcoded + WordNet + extra_domains paths, entropy math edge cases (empty / single / uniform / skewed / zero-count categories), scatter-entropy ordering (AI > concentrated), flag-fires joint condition, scaffolding-word-is-higher-concreteness invariant, CLI smoke tests.

### Documented (spec-interpretation choice)

- **`prestige_metaphor.py` classifies the HIGHER-concreteness member of each pair as the "scaffolding word"**, not the lower-concreteness member as the spec's Step 7 parenthetical reads. Rationale documented inline + in module docstring: the spec's running examples ("machinery of grief", "architecture of grief", "grammar of desire", "topology of attention") all have the prestige-domain word at higher Brysbaert concreteness than the emotional/cognitive target (machinery 4.75 vs grief 2.7; grammar 3.19 vs desire 1.7; architecture 3.59 vs grief 2.7). The §AIC-8 enumeration of prestige domains (architecture, grammar, machinery, etc.) describes the higher-concreteness words specifically. The operationally-correct interpretation: classify the higher-concreteness scaffolding word so the "metaphor confetti" scatter-entropy diagnostic actually catches the pattern the spec describes. JSON output records both `scaffolding_word` and `target_word` so consumers see the full pair structure.

### Empirical findings (calibration-pending)

- **Spec's default T1 = 2.5 doesn't crisply separate idioms from AI positives** on the Brysbaert data. The canonical AI example "machinery of grief" has gap 2.05 (below T1=2.5); other examples ("architecture of grief" 0.89, "grammar of desire" 1.49) also fall below. With T1 = 2.0, the detector catches the canonical AI positives but also catches some idiom false positives ("writer adjudicates", "simple machine"). The compound filter (T1 + T2) needs joint calibration against the §5.4 four-corpus fixture (roadmap item documented in 1.61.0 entry). Detector ships with spec defaults; operators tuning for their register can override via CLI.
- **Scatter-entropy ordering is correct on synthetic fixtures**: AI-fixture (4 distinct domains, even spread) registers entropy ≈ 0.96; concentrated-metaphor-fixture (machinery-dominated) registers ≈ 0.77. The ordering matches the diagnostic intent; the absolute threshold T3 = 0.7 is calibration-pending.

### Notes

- Operators using AIC-8 features need to install `en_core_web_md` via `python -m spacy download en_core_web_md` (~50 MB). Documented in `requirements.txt` since 1.61.0. NLTK + WordNet data is optional for the prestige-metaphor WordNet fallback; the detector runs hardcoded-list-only when NLTK is unavailable.
- Per the Stylometry-to-the-people policy, both detectors ship with `status: "provisional"` and no calibrated band. The §5.4 calibration corpus is roadmap work.
- Signals **not yet registered** in `COMPRESSION_HEURISTICS`. Registry plumbing + the full documentation update (aic-flags.md, source-triage.md, signals-glossary.md, new laundering-vocabulary.md) + register_typical.yaml + compound aesthetic_authority_audit all land in PR #4.
- Operators can run `scripts/image_conjunction.py` and `scripts/prestige_metaphor.py` standalone today; integration into `variance_audit.py --aic8` follows in PR #4.

## [1.62.0] - 2026-05-15

**AIC-9 Closure Inflation: kicker-density detector.** Second of four PRs implementing `internal/SPEC_aic_8_9_implementation.md`. Identifies sentences with **kicker shape** — short, declarative, generalizable, sentence-final period — and computes the proportion of paragraphs that end with one. AI-smoothed essayistic prose elevates this rate because the default assistant register has learned that "good paragraphs end with quotable summaries." Human writers ration kickers; aphoristic essayists (Borges, Bacon, La Rochefoucauld) deploy them as genre.

### Added

- **`plugins/setec-voiceprint/scripts/kicker_density.py`** — AIC-9 detector. Public API:
  - `is_kicker_shape(sentence, *, word_limit=15, nlp=None)` → `KickerClassification(is_kicker, confidence, reasons)`. Heuristic classifier checking word count, sentence-final period (not `?`, `!`, `…`, or quote), absence of digits, and absence of proper nouns. Proper-noun check prefers spaCy (`pos_ == "PROPN"` OR NER entity in PERSON/ORG/GPE/LOC/FAC/WORK_OF_ART/EVENT/NORP/PRODUCT), with a regex fallback that flags mid-sentence capitalized tokens not in the `_CAP_ALLOWLIST` (which lets `I`, `I'm`, `I've`, `I'll`, `I'd` through).
  - `kicker_density(text, *, word_limit=15, nlp=None, baseline_value=None, baseline_source=None)` → JSON-ready dict matching spec §5 schema (`signal_path: "aic_8_9.kicker_density"`, `family: "aic-9-closure-inflation"`, `value`, `spacing_variance`, `polarity: "↑"`, `status: "provisional"`, `task_surface: "smoothing_diagnosis"`, `claim_license: "voice_diagnostic"`, plus per-paragraph diagnostics and `baseline_comparison` block when a baseline is provided).
  - `_spacing_variance(positions)` returns the SD of inter-kicker paragraph distances. Diagnostic value: high variance means kickers cluster (a few aphoristic passages punctuating prose that mostly doesn't perform landing); low variance means kickers distribute evenly across the document. Per spec §AIC-9: distributed kickers are more diagnostic than clustered ones.
  - Standalone CLI: `python3 scripts/kicker_density.py path/to/draft.md [--word-limit 15] [--baseline 0.10] [--baseline-source LABEL] [--force-regex] [--out path]`. Emits JSON to stdout or to the `--out` path.
- **`plugins/setec-voiceprint/scripts/test_data/aic_8_9/`** — three synthetic test fixtures plus a README documenting scope:
  - `kicker_aphoristic_positive.md` — 6 paragraphs, 5 ending with kicker-shaped sentences (one closes with an AIC-9 reference that legitimately fails the digit + proper-noun checks). Tests the high-density case.
  - `kicker_normal_negative.md` — 6 paragraphs of long-form prose, none with kicker-shaped endings. Tests the zero-density baseline.
  - `kicker_mixed_clustered.md` — 7 paragraphs where kickers cluster in one passage. Tests the clustering vs. distribution math.
- **35 new regression tests** in `test_kicker_density.py`: per-condition classifier behavior (long sentences fail, questions/exclamations/ellipses fail, digits fail, mid-sentence proper nouns fail, first-person pronouns allowed, word-limit configurable, empty input handled, single-word sentences allowed); spaCy-gated PROPN-detection tests (skipped when `en_core_web_sm` is absent); density math (zero/one/partial ratios, JSON schema completeness, per-paragraph diagnostics); spacing variance (zero for uniform, zero for single kicker, positive for varying distances); baseline-comparison block (emitted when provided, absent when not, zero-baseline edge case); end-to-end fixture integration; CLI smoke tests (runs cleanly, `--baseline` flag, missing-file error, `--help` renders).

### Fixed

- **`scripts/check_corpus.py::score_manifest_rows` propagates `text_id` into error records.** Pre-existing bug discovered while running the full test suite during AIC-9 work. The success path at the bottom of `score_manifest_rows` maps both `text_id` and `id` through to the record dict; the error path for missing-`path` rows only looked up `entry.get("id")`, dropping the identifier when the manifest used `text_id` (the framework's actual convention). The test that exercises this path (`test_score_manifest_rows_emits_error_for_missing_path` in `test_shard_runner_corpus_hygiene.py`) has been failing on main; this one-line fix `entry.get("text_id") or entry.get("id")` resolves it.

### Notes

- This release ships the AIC-9 detector but **does not register the signal** in `COMPRESSION_HEURISTICS` yet. Registry plumbing lands in PR #4 alongside the `aic-flags.md` / `source-triage.md` / `signals-glossary.md` documentation updates. Operators can run `scripts/kicker_density.py` standalone immediately; integration into `variance_audit.py --aic9` follows.
- Per the Stylometry-to-the-people policy, the detector ships with `status: "provisional"` and no calibrated band. The §5.4 calibration corpus (aphoristic-essayist negatives + AI-rewrite positives) is roadmap work (see `ROADMAP.md` "AIC-8 / AIC-9 calibration corpus"). Operators using the detector before calibration should pass `--baseline` explicitly with a register-typical value (the spec's starting point: 0.05-0.10 for contemporary essay, 0.30-0.60 for aphoristic essay).
- The spaCy proper-noun check uses both `pos_ == "PROPN"` and NER. The union is strictly more permissive than either alone — useful because `en_core_web_sm` occasionally mistags rare proper nouns (e.g., "Borges" tags as NOUN, not PROPN). `_md` / `_lg` models are sharper; the detector accepts any model with POS + NER.
- The regex fallback (when spaCy isn't available) has a known false-negative for sentence-initial proper nouns: the first word of every sentence is capitalized, so the heuristic skips position 0 entirely. The aggregate density signal is robust to per-sentence misclassification, but operators wanting per-sentence accuracy should ensure spaCy is installed.

## [1.61.0] - 2026-05-15

**Foundation for AIC-8 (Aesthetic Authority Laundering) and AIC-9 (Closure Inflation).** First of four PRs implementing `internal/SPEC_aic_8_9_implementation.md`. This release ships the foundation infrastructure that the three new detectors will compose: a concreteness loader, a word-embedding helper, and a paragraph-position parser. No detectors yet — those land in PRs #2-4.

### Added

- **`plugins/setec-voiceprint/data/brysbaert_concreteness.csv`** — Brysbaert, Warriner & Kuperman (2014) per-word concreteness norms. 39,954 English words and two-word phrases on a 1-5 scale (5 = most concrete). Shipped in-repo so operators don't need to refetch on install. Schema documented in `plugins/setec-voiceprint/data/README.md` with full citation. Sourced from Springer's open-access supplementary material; the framework redistributes with prominent attribution under the assumption that academic-research supplementary data is intended for downstream research use.
- **`plugins/setec-voiceprint/scripts/concreteness.py`** — Brysbaert loader with O(1) lookups, case-insensitive matching, bigram support, and explicit `None` handling for out-of-vocab words. Functions: `get_concreteness(word)`, `concreteness_gap(word_a, word_b)`, `vocab_size()`, `is_loaded()`. lru_cache on the underlying dict so repeated calls don't re-read the CSV. Foundation for the AIC-8 image-conjunction detector that lands in PR #3.
- **`plugins/setec-voiceprint/scripts/embeddings.py`** — spaCy GloVe-vector wrapper. Functions: `vector(word)`, `cosine_similarity(a, b)`, `has_vector(word)`, `l2_distance(a, b)`, `model_identifier()`. Prefers `en_core_web_md` (~50 MB) over `en_core_web_lg` (~700 MB); raises typed `EmbeddingsBackendError` with install guidance when neither is installed. Returns `None` for out-of-vocab words rather than spaCy's default zero-vector, so callers can distinguish "no vector" from "vector at origin." Foundation for the AIC-8 image-conjunction and prestige-metaphor detectors.
- **`plugins/setec-voiceprint/scripts/paragraph_parser.py`** — paragraph-aware document segmentation. Functions: `split_paragraphs(text)`, `split_sentences(paragraph)`, `parse_document(text)`, `paragraph_final_sentences(text)`, `paragraph_count(text)`, `paragraph_stats(text)`. Returns frozen `SentencePosition` records with `paragraph_index`, `position_in_paragraph`, `paragraph_size`, `is_paragraph_initial`, `is_paragraph_final`. The `is_paragraph_final` flag is the load-bearing signal for AIC-9 kicker-density detection. Caller-supplied per-paragraph sentence lists bypass the regex tokenizer for callers that already have spaCy-parsed sentences.
- **`plugins/setec-voiceprint/scripts/fetch_brysbaert.py`** — re-fetcher for the Brysbaert concreteness norms. Downloads the XLSX from Springer's static-content CDN, converts to the framework's CSV schema, writes to the target path. Three-tier fallback for the network call: `requests` if installed (preferred; handles SSL on macOS Python without certifi), `curl` via subprocess (universal), `urllib` as last resort. Useful when the in-repo CSV is excluded from local redistribution or when refreshing against an upstream update.
- **`plugins/setec-voiceprint/data/README.md`** — schema documentation, Brysbaert citation, license posture statement, regeneration instructions.
- **49 new regression tests** across three test files: `test_concreteness.py` (16 tests; loader contract, lookups, gap math, integration with shipped CSV), `test_embeddings.py` (16 tests; vector lookups via mocked spaCy + 1 integration test gated on `en_core_web_md`), `test_paragraph_parser.py` (17 tests; paragraph + sentence splitting, position annotation, single-sentence-paragraph boundary cases, immutability of position records).

### Changed

- **`plugins/setec-voiceprint/requirements.txt`** — adds `openpyxl>=3.1` as a hard requirement (for the Brysbaert fetcher). Documents in commentary that AIC-8 also requires `en_core_web_md` or `en_core_web_lg` spaCy vectors model (separate install via `python -m spacy download`).
- **`ROADMAP.md` "Calibration corpus track"** — adds two new entries: (a) AIC-8/9 calibration corpus (idiom negatives, AI-image-conjunction positives, aphoristic essayist negatives, AI-rewrite positives) to replace the shipped provisional thresholds with empirically-calibrated values; (b) periodic embedding-model re-evaluation tickler for the AIC-8 stack (spaCy `en_core_web_md` is the 2026-H1 choice; re-evaluate every 6 months against newer embedding stacks). Documents existing empirical finding: spec's T1 = 2.5 starting threshold doesn't clear several of the spec's own positive examples on the Brysbaert data (`machinery/grief` gap 2.05, `architecture/grief` gap 0.89), so the compound diagnostic with T2 cosine-similarity is what carries the load.

### Notes

- This release ships **only foundation infrastructure**. No CLI surface, no detector scripts, no `COMPRESSION_HEURISTICS` entries, no `aic-flags.md` / `source-triage.md` / `signals-glossary.md` updates. Those land in PRs #2 (AIC-9 kicker density), #3 (AIC-8 image conjunction + prestige metaphor), and #4 (compound + baselines + docs).
- **Operators do not need to install anything new for the framework to keep working as-is**. The AIC-8/9 family is opt-in; existing audits (variance_audit, voice_distance, etc.) don't import the new modules. Operators who want to use AIC-8 features when they ship in PR #3 will need to install `en_core_web_md` via `python -m spacy download en_core_web_md` (one-time, ~50 MB).
- **`requirements.txt` adds `openpyxl` as a hard requirement** (not a soft optional) because new installs running the Brysbaert fetcher need it. The in-repo CSV satisfies most operators, but installs that exclude the data file need the fetcher path operational.
- The Brysbaert CSV is licensed per the original Brysbaert et al. 2014 publication; the framework's NOTICE / LICENSE files should be updated alongside it. Citation is documented in `data/README.md` and the `concreteness.py` module docstring.
- The PR-1 foundation is **APODICTIC-independent**. The spec's Step 14 (APODICTIC framing update) lives in a separate project outside this repo. The framework-side rhetorical-bankruptcy framing for the AIC-8/9 patterns ships in PR #4 documentation only; the writer-facing prompt template that the spec proposes is for the maintainer to apply on the APODICTIC side.

## [1.60.2] - 2026-05-15

**Slim the README's stylometric-tests section to a barebones inventory.** The 1.60.1 release shipped a grouped-table layout with per-signal polarity arrows and one-line definitions plus an outbound link to the in-repo signals glossary. The maintainer is iterating the glossary into a longer-form primer on a separate (out-of-repo) track; the in-repo glossary at `plugins/setec-voiceprint/references/signals-glossary.md` is now a stable v1.60.1 snapshot that won't grow alongside the primer. This release reflects the split: the README carries a slim reference inventory only, and stops linking to the glossary so readers don't expect a maintained primer there.

### Changed

- **`README.md` "Stylometric tests" section** — replaced the family-grouped per-signal tables (~110 lines, three columns: signal / polarity / what it measures) with a barebones bulleted inventory (~16 lines, family + count + comma-separated signal names). The outbound link to `signals-glossary.md` is removed; the glossary remains in-repo at its 1.60.1 state but isn't advertised as a maintained reference.

### Notes

- No code changes, no test changes, no behavior changes. The 49 signals listed are the same as in 1.60.1; only the README presentation slims.
- The `plugins/setec-voiceprint/references/signals-glossary.md` file is unchanged in this release and remains at the 1.60.1 state. Operators clone-and-reading the repo can still find it; the README just doesn't tease it as a primary reference.

## [1.60.1] - 2026-05-15

**Ship the stylometric signals glossary.** SETEC computes 49 distinct stylometric measurements across 14 families (Tier 1 variance, Tier 2 syntax, Tier 3 trajectory, Tier 4 surprisal, voice-distance, voice-drift, POV-voice, mimicry, semantic-preservation, phraseology, punctuation, stance-modality, bigram-KL, repetition). Each measurement has a name, a computation, an interpretation, calibration status, and known caveats. Until now those definitions lived dispersed across module docstrings, signal-registry entries in `variance_audit.py`, and per-script README comments. This release consolidates them into a single reader-facing reference.

### Added

- **`plugins/setec-voiceprint/references/signals-glossary.md`** (1004 lines) — comprehensive glossary with one entry per stylometric test. Each entry documents: family, signal path (where registered in `COMPRESSION_HEURISTICS`), polarity (gt/lt/symmetric), calibration status and anchor, what it measures, how it's computed (math in prose), range and units, interpretation guidance (high-vs-low semantics), worked examples (where the codebase / specs document them), and caveats. Entries with sparse interpretation / example sections are explicitly flagged `(NEEDS REFINEMENT)` for the iteration pass; the technical scaffold (definition, computation, range, status, calibration anchor) is filled from primary sources.
- **README.md "Stylometric tests" section** — compact tables grouping all 49 signals by family. Per signal: name, polarity arrow (↓ / ↑ / ↔ / —), one-line definition. Cross-links to the full glossary for definitions, computation details, examples, and caveats.

### Notes

- This is a documentation-only release. No code changes, no test changes, no behavior changes. Existing audits emit the same signals with the same shapes; the glossary documents what those signals mean.
- The shipped signal bands remain PROVISIONAL regardless of calibration anchor per the Stylometry-to-the-people policy (`scripts/calibration/PROVENANCE.md`). The glossary makes this explicit per entry.
- Entries marked `(NEEDS REFINEMENT)` are intentional: they identify where the codebase doesn't yet document typical-value ranges, worked examples, or comparative bands. The framework's maintainer iterates these with non-code Claude / other LLMs against documented before/after restoration pairs and validation-harness output. Subsequent versions of this glossary will fill them in.
- One signal counted in the source-code inventory (raw Type-Token Ratio) is omitted from the glossary because it isn't surfaced as a standalone framework output; it's an internal sub-computation of MATTR. Composite aggregators (`manuscript_audit`, `paragraph_audit`, `sliding_window_heatmap`) pass through the signals listed; they are not additional signals.

## [1.60.0] - 2026-05-15

**Refresh the Tier-4 surprisal candidate set after the 2026-05-15 verification pass.** The original Phase C.1 spec finalized 2026-05-11 listed five candidate causal LMs without a fresh market scan. The 2026-05-15 verification pass against primary sources (HF model cards, arXiv technical reports, license files) dropped one candidate on a constraint violation, added four candidates from the 2024-Q4 through 2026-Q2 release window, and introduced training-cutoff bucketing as a structural input to the §5.4 fixture-test decision rule. This release lands the corresponding code change in `scripts/surprisal_backend.py`: the public `MODEL_ALIASES` table is now the nine-candidate post-2026-05-15 core set, with a typed deprecation gate for the removed `phi3_mini` alias.

### Added

- **Four new core-set aliases in `MODEL_ALIASES`** per `SPEC_surprisal_model_choice.md` §4.1:
  - `llama32_3b` → `meta-llama/Llama-3.2-3B` (3.21B, Llama 3.2 Community License, Dec 2023 cutoff). Within-family parameter scan paired with the existing `llama32_1b`.
  - `olmo2_1b` → `allenai/OLMo-2-0425-1B` (1B, Apache 2.0, openly-published training corpus, Dec 2023 cutoff). The only candidate where PROVENANCE can audit the input corpus directly rather than trust the model creator's documentation; the verification pass flagged it as the best diagnostic fit in the candidate pool.
  - `openelm_1b` → `apple/OpenELM-1_1B` (1.1B, apple-amlr, pre-mid-2024 training corpus fully documented). Apple Sample Code License is permissive but not OSI-certified; flagged in spec §3.1.
  - `qwen3_1_7b` → `Qwen/Qwen3-1.7B-Base` (1.7B, Apache 2.0, 119 languages). Same-family successor to `qwen25_1_5b` with broader multilingual coverage. Training cutoff not documented in arXiv 2505.09388; release-date inference places it post-mid-2024.
  - `smollm2_1_7b` → `HuggingFaceTB/SmolLM2-1.7B` (1.7B, Apache 2.0, English-only). Effective cutoff bounded to April-June 2024 via FineWeb-Edu source snapshot dates available at SmolLM2 training time.
- **`DEPRECATED_ALIASES` dict** — a parallel table the constructor reads to raise typed `SurprisalBackendError` with migration guidance when an operator pins a removed alias. The pattern lets future spec revisions drop aliases cleanly without operators hitting confusing downstream HF-id-not-found failures.
- **Four regression tests in `test_surprisal_backend.py`**:
  - `test_phi3_mini_removed_from_alias_table` (negative-presence assertion)
  - `test_phi3_mini_alias_raises_deprecation_error` (positive-error assertion with substring checks for the alias name, the 2026-05-15 date, and at least one migration path)
  - `test_phi3_mini_full_huggingface_id_still_passes_through` (operators with legacy calibrations can keep using Phi-3 via the full HF id route)
  - `test_deprecated_aliases_table_is_populated` (gate-message rendering sanity check)
- **Module docstring + per-alias commentary** now points at `SPEC_surprisal_model_choice_UPDATE_2026-05-15.md` for the verification log and at the per-candidate training-cutoff bucket tags (`[pre-mid-2024]`, `[boundary]`, `[post-mid-2024]`) that §5.4 reports against.

### Changed

- **`MODEL_ALIASES` table size**: 5 → 9. Existing aliases (`gpt2`, `llama32_1b`, `qwen25_1_5b`, `tinyllama`) unchanged in both key and value.
- **`SurprisalBackend.__post_init__`** now runs the deprecation-alias check before the regular alias-resolution step. Pinning a key in `DEPRECATED_ALIASES` raises `SurprisalBackendError` immediately at construction with the alias's migration-message body, rather than passing through to a downstream load failure.
- **Module docstring** updated to reflect the nine-candidate core set and to reference the spec's §3.7 base-only posture as the reason for the Phi-3 drop.
- **Spec cross-references**: surprisal_backend.py now points at `internal/SPEC_surprisal_model_choice_UPDATE_2026-05-15.md` (the verification pass) in addition to `SPEC_surprisal_model_choice.md` (the spec proper, post-revision).

### Removed

- **`phi3_mini` alias** removed from `MODEL_ALIASES`. Microsoft confirmed in April 2024 on the HF discussion thread that no base variant of any Phi family member would be published; that posture has held through Phi-3.5 Mini (Aug 2024) and Phi-4 Mini (Feb 2025). Instruction-tuning skews per-token distributions in exactly the direction the framework's discrimination test is sensitive to, so the spec's §3.7 base-only posture (added 2026-05-15) excludes the entire Phi family from the alias set.

### Migration

Operators who pinned `--surprisal-model phi3_mini` get a typed error at construction with three documented migration paths:

1. **Full HF id pass-through** (preserves prior behavior for operators with legacy calibrations): `--surprisal-model microsoft/Phi-3-mini-4k-instruct`. The underlying model is still on HuggingFace; only the alias indirection is gone.
2. **Apache-2.0 upper-bound base replacement** (recommended): `--surprisal-model Qwen/Qwen3-4B-Base`. Comparable parameter count, base variant explicit, fully permissive license. Listed as an optional comparator in spec §4.1.
3. **Core-set fallback** to any of the nine new aliases listed above. `tinyllama` remains the conservative default.

### Notes

- This is a public-CLI change (`MODEL_ALIASES` is reachable from `variance_audit.py --surprisal-model`), hence the MINOR bump rather than PATCH.
- No spec changes ship with this release; the spec updates (`internal/SPEC_surprisal_model_choice.md` and the companion `_UPDATE_2026-05-15.md`) are in the `internal/` working tree which is gitignored. This release lands only the code change that makes the spec's §5.5 implementation step 3 operational.
- The `DEFAULT_MODEL` value stays `tinyllama` — unchanged from prior. The verification pass surfaced OLMo 2 1B as "the best diagnostic fit" but the spec is explicit that the default is the conservative pre-mid-2024 footprint pick, not a quality recommendation. Changing the default would be a behavior change without empirical backing from §5.4.
- The §5.4 fixture run remains pending. This release ships the candidate-set plumbing; running the fixture against the new candidates on the AMD calibration host is the next operator step.

## [1.59.4] - 2026-05-14

**Fix `variance_audit.py --help` crash on unescaped `%`.** Running `python3 variance_audit.py --help` (or `--tier4 --help`, or any path that triggers argparse's help formatter) raised:

```
TypeError: %o format: an integer is required, not dict
  File ".../argparse.py", line 602, in _expand_help
    return self._get_help_string(action) % params
```

Root cause: argparse %-substitutes every action's `help=` string against a params dict at format time so `%(prog)s` and `%(default)s` work. The `--window-stride` help text contained the literal "50% overlap"; argparse read `% o` as an octal format spec and crashed. Bug pre-existed v1.59.x (was already in tree when PR #44/#45 landed), surfaced while smoke-testing the v1.59.3 install runbook.

### Fixed

- **`scripts/variance_audit.py` `--window-stride` help text** — `50% overlap` → `50%% overlap`. Argparse renders this back to `50% overlap` on `--help` output (verified). Added an adjacent comment explaining the escape so the next operator who writes a `%` in a help string understands the gotcha.

### Added

- **`scripts/tests/test_variance_audit_cli.py`** — regression tests for argparse hygiene. Two complementary checks:
  - `test_variance_audit_help_runs_cleanly` — subprocess smoke test. Runs `variance_audit.py --help` end-to-end, asserts exit-code 0 and that `usage:` + `--tier4` appear in stdout. Catches any future regression at the level an operator hits.
  - `test_no_unescaped_percent_in_add_argument_help` — static AST scan. Walks every `add_argument(...)` call, extracts the `help=` literal, attempts argparse-equivalent `% defaultdict(...)` substitution. Surfaces the offending line number directly rather than dumping a TypeError from argparse internals. Verified to trip on the pre-fix help text and pass on the post-fix form.

### Notes

- Behavior change: `--help` now renders. No change to any audit output, JSON shape, threshold table, or other CLI behavior.
- The full `scripts/` test suite (1902 passed, 4 skipped) runs cleanly with the fix in place.
- AST scan over all `scripts/**/*.py` found this was the only unescaped-`%` regression in the framework — the rest of the codebase's help strings use `%(default)s`-style substitution, which is the legitimate argparse pattern and isn't affected.

## [1.59.3] - 2026-05-14

**Ship `RUNBOOK_tier4_install.md` — cross-platform Tier-4 install guide.** PR #44 (1.59.2) shipped `requirements-surprisal.txt` with inline wheel-selection commentary covering ROCm / CUDA / MPS / DirectML / CPU-only. Operator feedback from the AMD-workspace shift: the inline commentary is enough for the well-trodden paths (MPS, CUDA) but leaves gaps for ROCm-on-WSL2 (driver requirements, GPU support matrix, gfx-version overrides), torch-directml integration status, and the Python 3.13 wheel gap. This PR adds the full runbook.

### Added

- **`plugins/setec-voiceprint/scripts/calibration/RUNBOOK_tier4_install.md`** (~450 lines). Structure:
  - **§0 Decision table** mapping (host, Python version, GPU) → install path. Five paths total.
  - **§1 Path A: AMD GPU via ROCm 6.x** — both native Linux (§1.1) and WSL2-on-Windows (§1.2). Includes the GPU support matrix (§1.3, RDNA2/RDNA3 work, RDNA1 doesn't) and `HSA_OVERRIDE_GFX_VERSION` workaround for consumer cards that report a slightly-off gfx string.
  - **§2 Path B: NVIDIA CUDA 12.x** — driver version requirements, `cu118` vs `cu121` wheel selection, WSL2 CUDA runtime install.
  - **§3 Path C: Apple Silicon MPS** — the simplest path; `PYTORCH_ENABLE_MPS_FALLBACK=1` for unimplemented ops.
  - **§4 Path D: torch-directml** — Windows cross-vendor fallback. **Flags the framework's current DirectML integration gap**: `surprisal_backend.py` doesn't yet wire DirectML in automatically, so Path D works for ad-hoc smoke tests but doesn't cleanly integrate into `variance_audit.py --tier4`. Recommends Path A (WSL2 + ROCm) for AMD-on-Windows until DirectML support lands.
  - **§5 Path E: CPU-only** — universal fallback with realistic perf expectations (20-100 tokens/sec on modern x86; tractable for sample-size work, impractical for full RAID-scale).
  - **§6 Smoke test** — a copy-pasteable 10-line Python script that loads TinyLlama, scores one sentence, and prints the surprisal series + identifier_block. Documents expected output and the five common failure modes with remediation.
  - **§7 Fallback ladder** — when the preferred path fails, the order to step down (preferred GPU → CPU → smaller model → skip Tier 4).
  - **§8 Performance expectations** — order-of-magnitude tokens/sec table across CUDA / ROCm / MPS / CPU / DirectML for TinyLlama 1.1B.
  - **§9 Common gotchas** — 7 entries: Python 3.13 wheel gap (recommend 3.11/3.12 for accelerator paths), `torch.cuda.is_available()` returning True on ROCm (expected, not a bug), first-load weight download, HuggingFace gated weights (Llama 3.2 1B needs auth), deterministic-mode warnings, torch install size, WSL2 driver requirements.
  - **§10 After install** — pointers to the smoke test, calibration toolchain, and §6.4 fixture suite for picking the operational model.

### Changed

- **`requirements-surprisal.txt`** header — adds a cross-reference to the runbook in the PyTorch-wheel-selection section. The inline commentary remains (fast path); the runbook is the link to follow when the fast path fails.
- **`scripts/surprisal_backend.py` install-hint message** — appends a pointer to `scripts/calibration/RUNBOOK_tier4_install.md` for operators who hit the `transformers is not installed` error. The hint still satisfies the existing test's substring assertions (`"transformers"` and `"pip install"`).
- **`scripts/variance_audit.py` `--tier4` CLI help text** — adds a parallel pointer to the runbook alongside the existing `requirements-surprisal.txt` reference.

### Notes

- Documentation only — no code or behavior changes. The Tier-4 backend has worked end-to-end since v1.45.0 (PR #23 shipped `surprisal_backend.py`) on hosts that already had transformers + torch installed correctly; this runbook makes the "installed correctly" part discoverable across all five reasonable backends.
- The runbook's perf table is order-of-magnitude only — operator-reported figures from the §6.4 fixture-suite work. Don't treat the numbers as a benchmark; treat them as "you should expect this neighborhood, file a bug if you're off by 10x."
- The DirectML integration gap (§4) is now explicitly documented. Roadmap item: wire DirectML's device-move into `surprisal_backend._load` and `score_text` so Path D drops into the framework cleanly. Not in scope for this PATCH.

## [1.59.2] - 2026-05-14

**Ship `requirements-surprisal.txt` — close the Phase C documentation gap.** The Tier-4 surprisal backend (`scripts/surprisal_backend.py`) shipped in 1.45.0 (PR #23) with an install hint that pointed at "the setup skill for tier-by-tier guidance," but the actual pinned dependency layer was scoped-for-when-C.3-ships and then never landed. The framework's calibration toolchain has a sibling `requirements-calibration.txt`; this PR adds the matching Tier-4 file and updates the install hints to point at it.

### Added

- **`plugins/setec-voiceprint/requirements-surprisal.txt`** — pinned dependency layer for the Tier-4 surprisal backend. Includes:
  - `transformers>=4.40,<5` (HuggingFace causal-LM loader + tokenizer).
  - `tokenizers>=0.20,<0.22` (explicit pin so a stale transformers build doesn't pull a tokenizers version that breaks the §6.4 candidate tokenizers).
  - `torch>=2.1,<3` (the inference engine; wheel must be installed first per the platform-specific commentary).
  - Commented-out `accelerate>=0.30,<2` (only needed when the §6.4 fixture-suite picks a model that requires it — Llama 3.2 1B, Qwen 2.5 1.5B, or Phi-3 Mini at fp16).
- **PyTorch wheel-selection commentary** in the file header. Documents the four reasonable paths for picking a wheel: ROCm 6.x (AMD GPU on Linux / WSL2), CUDA 12.x (NVIDIA GPU), MPS (Apple Silicon, default wheel), and CPU-only fallback. Also names `torch-directml` as a cross-vendor fallback for Windows operators when ROCm install collapses; flags that the surprisal backend doesn't yet wire DirectML in automatically (DirectML support is roadmap).
- **Per-accelerator cost commentary**: notes that CPU-only Tier-4 calibration is tractable at sample-size (10K rows ≈ 1-3 hours on TinyLlama 1.1B) but impractical at full RAID-scale (8M rows is days-to-weeks). GPU is mandatory for the latter.

### Changed

- **`scripts/surprisal_backend.py` install-hint message** now points at `requirements-surprisal.txt` instead of "the setup skill." The hint still satisfies the existing test's substring assertions (`"transformers"` and `"pip install"`).
- **`scripts/variance_audit.py` `--tier4` CLI help text** points at `plugins/setec-voiceprint/requirements-surprisal.txt` for the dependency layer + per-accelerator torch-wheel selection guidance.

### Notes

- This is a documentation / packaging artifact only — no code or behavior changes. The Tier-4 backend has always worked when transformers + torch are installed manually; this just makes the install path discoverable and pinned.
- The framework's shipped Tier-4 thresholds in `COMPRESSION_HEURISTICS` remain PROVISIONAL (`provisional=True`, `provenance=None`) regardless of dependency-layer shipping. Phase C.5 (operational model-choice fixture suite) is the remaining gap for load-bearing Tier-4 calibration — but it's operational, not framework-side.
- Existing test (`test_score_text_raises_when_transformers_missing`) still passes — the new hint contains the same substrings the test asserts on.

## [1.59.1] - 2026-05-14

**Operator docs alignment with v1.53.0 – v1.59.0 changes.** A docs-only PATCH bump that brings the three operator-facing reference docs into alignment with the framework features that landed in Wave 4. Pre-AMD-shift readiness pass: an operator following any of these docs end-to-end should land on the actual current behavior rather than the pre-Wave-4 state.

### Changed

- **`PROVENANCE.md`** — adds the v1.59.0 polarity-inversion gate to the docs:
  - Selection-criteria gate 1 now notes the in-code enforcement (`calibrate_thresholds.py` refuses to publish a threshold when `direction_aware_auc` falls below the chance line, raising `PolarityInversionRefusal`) alongside the pre-existing empirical-finding language.
  - New top-level **Polarity-inversion gate (v1.59.0+)** section between selection criteria and available corpora. Documents: how the gate fires, the two new CLI flags (`--allow-polarity-inversion`, `--polarity-inversion-margin`), the override-path provenance shape (`polarity_inversion` block + `POLARITY INVERSION` notes prefix), and the recommended operator workflow (treat inversions as findings, only override to *document* an inversion, never to ship the threshold as load-bearing).
  - Template for new entries gains a required `Polarity gate` bullet (reads "matched" in the common case, carries the full inversion diagnostic for override-path entries) and a notes-prefix annotation guiding override-path entries to lead with `POLARITY INVERSION` so downstream consumers cannot mistake them for calibrated thresholds.
- **`launchd/RUNBOOK_macos_nightly.md`** — aligns the dry-run example output and the install-step output with the v1.53.0 idempotent install flow. The dry-run snippet now shows the three steps (`cp` + `bootout` + `bootstrap`) the operator would run manually, with the bootout marked best-effort. The install output shows the new "Running best-effort bootout..." status line. Troubleshooting bullet for `setup_launchd.py --install` regeneration explicitly notes the install path is idempotent — re-running after a config change is safe.
- **`RUNBOOK_multi_machine_sync.md`** — adds a "Failed-state precedence (v1.54.0+)" paragraph to section 3.3 (real cross-host conflict on state.json). Documents that `merge_state_files` treats `failed` as terminal except against `done`, so a remote `sweep-stale` cannot silently resurrect a failed shard. Operators seeing an unexpected `failed` after `resolve-conflict` should investigate the failure rather than overriding the state.

### Notes

- This is a docs-only PATCH bump. No code, no tests, no behavior changes.
- The three docs were already structurally aligned with the framework (the runbooks shipped with their respective feature PRs); the gaps were specifically the post-PR refinements (idempotent install fix in #26 round 2, failed-state-terminal fix in #27 round 2, the entire polarity-inversion gate in #40). The doc updates close those gaps before the operator workflow shifts to the AMD desktop.

## [1.59.0] - 2026-05-14

**Calibration toolchain — polarity-inversion refusal gate.** Closes the load-bearing methodological gap documented in README "Why no verdict" §cross-corpus polarity volatility. The framework's empirical finding (every Tier 1 signal flipped polarity between EditLens val and MAGE on consecutive days, 2026-05-10 / 2026-05-11) says that per-corpus calibration thresholds do not generalize. Prior to this PR, `calibrate_thresholds.py` would happily publish a threshold from any single corpus regardless of whether the corpus's `direction_aware_auc` agreed with the registry's direction hypothesis — exactly the failure mode the documentation describes. This PR makes the framework's posture operational in code.

### Added

- **`PolarityInversionRefusal` exception class** in `calibrate_thresholds.py`. Subclasses `SystemExit` so the CLI exits non-zero with a diagnostic message; programmatic callers can catch the specific type rather than the generic `SystemExit`. The diagnostic message names the signal, the registry direction, the observed `direction_aware_auc`, the corpus, and the override flag, so an operator hitting the gate sees every input needed to either fix the calibration or document the inversion.
- **`_check_polarity_inversion(...)` helper** — pure function that compares `direction_aware_auc` against the chance line (0.5 minus optional margin) and raises `PolarityInversionRefusal` when the corpus contradicts the registry's hypothesis. Returns `(triggered, chance_line)` so the caller can reuse the validated chance-line value for the provenance block.
- **`_validate_polarity_margin(...)` helper** — Codex review P1 fix. Validates that the margin is in `[0.0, 0.5)` before use and raises `SystemExit` with a clear diagnostic on out-of-range / non-numeric / NaN input. Catches the typo-class failure mode where `--polarity-inversion-margin 5` (intended `0.5`) would shift the chance line to -4.5 and silently disable the gate.
- **`--allow-polarity-inversion` CLI flag** on `calibrate_thresholds.py`. Override the gate when explicitly documenting an inversion in the provenance ledger. The override path is loud: the entry's `notes` field is prefixed with `POLARITY INVERSION` (same convention as the `PIPELINE CHECK` prefix for sub-sampled runs) and a `polarity_inversion` block is added recording the DA-AUC, chance line, and registry direction. Downstream consumers filtering on either prefix can refuse to treat the entry as a calibrated load-bearing threshold.
- **`--polarity-inversion-margin` CLI flag** (default `0.0`, strict). Widens the chance-line cutoff for borderline DA-AUC values near 0.5. Useful for small corpora where the AUC estimate has wide variance and the operator doesn't want the gate firing on noise. A margin of `0.05` shifts the chance line to 0.45 (only DA-AUC < 0.45 trips). Validated to `[0.0, 0.5)` — a typo-class invalid value fails loudly at the earliest possible point.
- **24 new tests in `test_calibration_polarity_inversion.py`** covering: the gate behavior (matched / boundary / inverted / override / margin / back-compat), the `_validate_polarity_margin` helper (zero / small positive / near-upper-bound / negative / at-upper-bound / above-upper-bound / non-numeric / NaN), end-to-end failure modes when the margin is invalid (failing even on matched DA-AUC and missing-DA-AUC paths), and the provenance block recording the validated chance-line value.

### Changed

- `derive_threshold_from_records(records, ..., args, ...)` now calls `_check_polarity_inversion` after `_ranking_metrics`. The gate consults `args.allow_polarity_inversion` and `args.polarity_inversion_margin` via `getattr` with defaults — programmatic callers that build a `Namespace` manually (older tests, scripts) keep working without modification.
- `_check_polarity_inversion(...)` now returns `(triggered, chance_line)` so the caller can reuse the single validated chance-line value for both the gate logic and the provenance block. Pre-fix the provenance block recomputed `0.5 - raw_margin` without validation, allowing a typo-class invalid margin to land in the ledger as semantic garbage (Codex review P1).
- Two existing tests in `test_calibration_cache.py` (`test_derive_threshold_without_cache_flag_still_scores`, `test_derive_threshold_with_missing_records_cache_attr`) had inadvertently polarity-inverted synthetic scoring fixtures (positives high, negatives low for an `lt`-direction signal). Updated to polarity-matched fixtures (positives low, negatives high) — same separation, correct direction. The tests now exercise what real `lt`-signal data looks like; the polarity gate correctly accepts them.

### Notes

- The infrastructure for polarity detection already existed: `_ranking_metrics` computes `direction_aware_auc` (1 − raw AUC for `lt` signals, raw AUC for `gt` signals) such that ≥ 0.5 means the corpus agrees with the registry. This PR adds the **refusal gate** that consumes that signal — small surface area, high methodological leverage.
- The gate is `< chance_line`, not `<= chance_line`. Exactly-at-chance (DA-AUC == 0.5) is treated as the boundary case and passes — that decision is pinned in `test_da_auc_exactly_at_chance_publishes_entry` so a future signed-rounding bug can't silently flip it.
- Override-path entries carry both the `polarity_inversion` provenance block AND the loud notes prefix. The two are redundant on purpose — different downstream consumers filter on different signals (programmatic ledger readers parse the block; operators reading the markdown render see the prefix).
- The margin validator (`_validate_polarity_margin`) runs unconditionally — even on the back-compat DA-AUC-is-None no-op path. An invalid margin fails loudly regardless of whether the gate would ultimately fire, catching the typo at the earliest possible point.
- **Version-bump note**: rebased from declared 1.56.0 → 1.59.0 because PRs #26 (1.53.0), #27 (1.54.0), #31 (1.55.0), #37 (1.56.0), #38 (1.57.0), #39 (1.58.0) merged ahead in Wave 4. MINOR-tier bump preserved since this is a `feat:` change.

## [1.58.0] - 2026-05-14

**Authorship-state taxonomy phase B.3 — wave 4 (final): voice-surface claim-license routing.** Closes out the B.3 rollout by wiring per-state caveats into the two voice-surface audit scripts that emit a `ClaimLicense` block: `general_imposters.py` and `semantic_preservation_check.py`. After this PR, every `claim_license`-using audit script in the framework routes its caveats by authorship state when the operator supplies `--ai-status`.

### Added

- **`general_imposters.py --ai-status` flag.** The General Imposters attribution harness now accepts the manifest's `ai_status` for the target text. Threaded through `GIResult.target_ai_status` to `_structured_claim_license`, which calls `with_state_caveats(...)` after building the base ClaimLicense block. JSON output's `to_dict()` emits the new `ai_status` field only when set, preserving the legacy JSON shape for callers that don't pass the flag.
- **`semantic_preservation_check.py --ai-status` flag.** The before/after preservation guardrail now routes its caveats on the AFTER text's authorship state. `check_preservation(...)` and `_claim_license_dict(...)` both gain a `target_ai_status` kwarg threaded through `main()`.
- **10 new tests** in `test_b3_voice_surfaces.py` covering: pre-B.3 backwards-compat for each script, `ai_generated_from_outline` → seed caveat, `pre_ai_human` → baseline caveat, `mixed` → composite-states mention, JSON output carries `ai_status` when supplied, JSON shape is preserved (no `ai_status` key) when flag is omitted, and the rendered caveat text lives only inside `claim_license.rendered` (not leaked into the structured JSON payload).

### Changed

- `general_imposters.GIResult` gains an optional `target_ai_status: str | None = None` field. `GIResult.to_dict()` emits the new `ai_status` key only when the field is set, so pre-B.3 JSON consumers see the v1.49.0 – v1.57.0 shape unchanged.
- `general_imposters._structured_claim_license(result)` now appends state-routed caveats via `with_state_caveats(lic, target_ai_status=result.target_ai_status)`.
- `general_imposters.run(args)` reads `args.ai_status` via `getattr(args, "ai_status", None)` so programmatic callers that build the Namespace manually (e.g., older tests) keep working — no `AttributeError` on missing field.
- `semantic_preservation_check.check_preservation(...)` and `_claim_license_dict(...)` both gain optional `target_ai_status` kwargs. `main()` threads `args.ai_status` through. Backwards-compat: keyword-less calls continue to work.

### Notes

- **B.3 rollout complete.** Wave 1 (1.49.0, PR #29) shipped the helper + 2 exemplar scripts. Wave 2 (PR #37) covered validation surfaces (3). Wave 3 (PR #38) covered craft surfaces (3). This wave covers voice surfaces (2). All 10 `claim_license`-using audit scripts in the framework now route per-state caveats via the same `with_state_caveats(...)` mechanism.
- The change is rendering-layer (markdown). JSON output's claim-license shape is unchanged for both scripts — downstream consumers that read the JSON keep working. The new `ai_status` field is forward-compat additive only.
- Pre-B.3 callers that don't pass `--ai-status` see the same markdown they got in v1.49.0 – v1.57.0. The helper is no-op without state inputs.
- **Version-bump note**: rebased from declared 1.55.0 → 1.58.0 because PRs #26 (1.53.0), #27 (1.54.0), #31 (1.55.0), #37 (1.56.0), and #38 (1.57.0) merged ahead in Wave 4. MINOR-tier bump preserved since this is a `feat:` change.

## [1.57.0] - 2026-05-14

**Authorship-state taxonomy phase B.3 — wave 3: craft-surface claim-license routing.** Wires per-state caveats into the three craft-surface audit scripts that emit a `ClaimLicense` block: `construction_signature_audit.py`, `punctuation_cadence_audit.py`, and `mimicry_cosplay_audit.py`. Mechanical extension of the B.3 helper shipped in 1.49.0 (PR #29); stacked on wave 2 (PR #37 / 1.56.0). See `internal/SPEC_authorship_states.md` §10 for the rollout plan.

### Added

- **`construction_signature_audit.py --ai-status` flag.** Operator supplies the manifest entry's `ai_status` for the target text. The rendered ClaimLicense block (returned via `_claim_license_dict`) gains the matching state-specific caveat from `claim_license.TARGET_STATE_CAVEAT_TEMPLATES`. Default behavior unchanged when flag is absent.
- **`punctuation_cadence_audit.py --ai-status` flag.** Same wiring (uses the standard `_claim_license_block(audit)` shape with `audit["ai_status"]`).
- **`mimicry_cosplay_audit.py --ai-status` flag.** Same wiring (parallel `_claim_license_dict` shape with new `target_ai_status` kwarg threaded through `audit_cosplay`).
- **13 new tests** in `test_b3_craft_surfaces.py` covering: pre-B.3 backwards-compat for each script, `ai_generated_from_outline` produces the seed caveat, `pre_ai_human` produces the baseline caveat, `mixed` mentions `composite_states`, `ai_edited` matches the low-touch-editing caveat template, the audit dict's `ai_status` field is populated in JSON output (and reports the correct `task_surface`), and the JSON shape doesn't embed the rendered caveats.

### Changed

- `construction_signature_audit.py`'s `build_audit(...)` and `_claim_license_dict(...)` both gain an optional `target_ai_status` kwarg. `main()` threads `args.ai_status` through. Backwards-compat: positional / kwarg-less calls continue to work.
- `mimicry_cosplay_audit.py`'s `audit_cosplay(...)` and `_claim_license_dict(...)` both gain an optional `target_ai_status` kwarg. `main()` threads `args.ai_status` through.
- `punctuation_cadence_audit.py`'s `_claim_license_block(audit)` now consults `audit.get("ai_status")` after building the base block. `main()` populates `audit["ai_status"]` from `args.ai_status`.

### Notes

- B.3 wave 4 (the final wave) covers the voice-surface scripts: `general_imposters`, `semantic_preservation_check`.
- The change is rendering-layer (markdown). JSON output's claim-license shape is unchanged — downstream consumers that read the JSON keep working. The new `ai_status` field is forward-compat additive only.
- Pre-B.3 callers that don't pass `--ai-status` see the same markdown they got in v1.49.0 – v1.56.0. The helper is no-op without state inputs.
- **Version-bump note**: rebased from declared 1.54.0 → 1.57.0 because PRs #26 (1.53.0), #27 (1.54.0), #31 (1.55.0), and #37 (1.56.0) merged ahead in Wave 4. MINOR-tier bump preserved since this is a `feat:` change.

## [1.56.0] - 2026-05-14

**Authorship-state taxonomy phase B.3 — wave 2: validation-surface claim-license routing.** Wires per-state caveats into the three validation-surface audit scripts that emit a `ClaimLicense` block: `confounder_audit.py`, `surface_disagreement_resolver.py`, and `adversarial_robustness_card.py`. Mechanical extension of the B.3 helper shipped in 1.49.0 (PR #29); see `internal/SPEC_authorship_states.md` §10 for the rollout plan.

### Added

- **`confounder_audit.py --ai-status` flag.** Operator supplies the manifest entry's `ai_status` for the target text. The rendered ClaimLicense block gains the matching state-specific caveat from `claim_license.TARGET_STATE_CAVEAT_TEMPLATES`. Default behavior unchanged when flag is absent.
- **`surface_disagreement_resolver.py --ai-status` flag.** Same wiring. The cross-surface meta-layer's claim-license block now routes on state when the operator provides it.
- **`adversarial_robustness_card.py --ai-status` flag.** Same wiring. Per-signal robustness cards route the differential by authorship state when supplied.
- **13 new tests** in `test_b3_validation_surfaces.py` covering: pre-B.3 backwards-compat (no flag → no state caveat), `ai_generated_from_outline` produces the seed caveat, `pre_ai_human` produces the baseline caveat, `mixed` mentions `composite_states`, `ai_edited` produces the edited-source caveat, `unknown` produces the unspecified-state caveat, the audit dict's `ai_status` field is populated in JSON output, and the JSON shape doesn't embed the rendered caveats (separation of concerns).

### Changed

- All three scripts' `_claim_license_block(...)` functions now call `with_state_caveats(lic, target_ai_status=<report|card>.get("ai_status"))` after building the base block. No behavior change when `ai_status` is absent from the input dict.
- Each script's `main()` populates `report["ai_status"]` (or `card["ai_status"]`) from `args.ai_status` so downstream JSON consumers can route on state without re-passing the flag.

### Notes

- B.3 is intentionally a rollout, not a single PR. Wave 1 (1.49.0, PR #29) shipped the helper + two exemplar scripts. This is wave 2; remaining waves cover the craft-surface scripts (`construction_signature_audit`, `punctuation_cadence_audit`, `mimicry_cosplay_audit`) and the voice-surface scripts (`general_imposters`, `semantic_preservation_check`).
- The change is rendering-layer (markdown). JSON output's claim-license shape is unchanged — downstream consumers that read the JSON keep working. The new `ai_status` field is forward-compat additive only.
- Pre-B.3 callers that don't pass `--ai-status` see the same markdown they got in v1.49.0 – v1.55.0. The helper is no-op without state inputs.
- **Version-bump note**: rebased from declared 1.53.0 → 1.56.0 because PRs #26 (launchd idempotency, 1.53.0), #27 (multi-machine + failed-state fix, 1.54.0), and #31 (Tier-4 wiring + ablation family, 1.55.0) merged ahead in Wave 4. MINOR-tier bump preserved since this is a `feat:` change.

## [1.55.0] - 2026-05-14

**variance_audit.py Tier 4 — surprisal integration (phase C.4).** Wires the C.2 surprisal backend + C.3 audit math into `variance_audit.py` as a new Tier 4. Opt-in via `--tier4` (default OFF, mirroring Tier 3's SBERT path). Adds three new `COMPRESSION_HEURISTICS` entries — `surprisal_mean`, `surprisal_sd`, `surprisal_acf_lag1` — all `provisional=True` per SPEC `internal/SPEC_surprisal_signal.md` §3.5. Stacked on C.3 (PR #30); closes the Phase C plan.

The Tier 4 path uses the same `audit_surprisal` math as the C.3 standalone CLI, so the standalone audit and the variance-audit Tier 4 block produce the same numbers. The band classifier picks up the new entries automatically (they're regular `ThresholdSpec`s); operators see the new signals contribute to the compression-fraction band call once `--tier4` is on. The PROVISIONAL marker (`calibration_anchor: user-baseline-required`) propagates from the Tier 4 audit block all the way into the rendered output, so the band call is never read as load-bearing.

### Added

- **`_tier4_surprisal_block(text, *, score_fn=None, backend=None, sliding_window=False, ...)`** — new helper in `variance_audit.py`. Reuses `surprisal_audit.audit_surprisal` for the math; lazy-imports `surprisal_audit` and (optionally) `surprisal_backend` so operators who don't run Tier 4 never pay the import cost. Returns `{"available": False, "reason": ...}` for transformers-missing / empty-text / empty-series cases without crashing.
- **`audit_text(..., do_tier4=False, tier4_score_fn=None, tier4_backend=None)`** — new keyword arguments. Default OFF; when on, builds the `tier4.surprisal` block. The test-friendly `tier4_score_fn` accepts any callable matching `backend.score_text(text, return_top_k=...)` so the test suite injects a stub without loading transformers.
- **`COMPRESSION_HEURISTICS` entries** — three new `ThresholdSpec`s per SPEC §4.3:
  - `surprisal_mean` — `signal_path=tier4.surprisal.mean`, `direction=lt`, `weight=1.5`, `length_floor=300`, `value=3.5` (PROVISIONAL — AI prose tends LOWER as LM samples near its mode).
  - `surprisal_sd` — `signal_path=tier4.surprisal.sd`, `direction=lt`, `weight=2.0`, `length_floor=300`, `value=1.5` (PROVISIONAL — DivEye's load-bearing signal; AI tends LOWER for uniform surprise).
  - `surprisal_acf_lag1` — `signal_path=tier4.surprisal.autocorrelation.lag_1`, `direction=gt`, `weight=1.0`, `length_floor=500`, `value=0.30` (PROVISIONAL — AI tends HIGHER for smooth local predictability).
  All three carry `provisional=True` and `provenance=None` — the `ThresholdSpec.__post_init__` invariant is preserved.
- **`predictability_uniformity` ablation family** — Codex PR #31 review P0 fix. Groups the three Tier 4 signals into a single ablation family so `classify_compression()`'s robustness check can drop ALL of them at once. Pre-fix the family map didn't include the Tier 4 entries; a Tier-4-driven band call would report as robust because no ablation removed the load-bearing surprisal weight. Post-fix, ablating `predictability_uniformity` removes all three signals and the band call demotes appropriately.
- **CLI flags**: `--tier4` (action store_true, default False) and `--surprisal-model` (default None → `tinyllama` via `resolve_model_arg`). The CLI lazily constructs a `SurprisalBackend` when `--tier4` is set, then threads it through to `audit_text`. ImportError from `surprisal_backend` leaves `tier4_backend=None`; the audit_text fallback emits the available=False block with a clear reason.
- **`plugins/setec-voiceprint/scripts/tests/test_variance_audit_tier4.py`** — 17 tests in 6 classes (was 16; +1 ablation regression):
  - `TestTier4DisabledByDefault` (2): default audit_text omits tier4; explicit `do_tier4=False` is the same as omission.
  - `TestTier4WithStub` (4): stub `score_fn` produces fully-populated tier4 block; provisional markers propagate; band call + top-k diagnostic present; COMPRESSION_HEURISTICS signal_paths resolve via `_extract_signal` (the load-bearing band-classifier contract).
  - `TestTier4EdgeCases` (3): empty stub → available=False; empty text → available=False; short text still works in helper.
  - `TestCompressionHeuristicsTier4` (6): three entries registered; all provisional=True with provenance=None; directions match SPEC §4.3 (mean=lt, sd=lt, acf=gt); weights match SPEC §4.3 (1.5, 2.0, 1.0); signal_paths point under `tier4.surprisal.*`; listed in `provisional_signals()`.
  - `TestTier4CliFlags` (1): `--tier4` default False.
  - `TestTier4AblationFamily` (1): regression for the Codex-flagged ablation bug — a Tier-4-only `moderate` band call drops when the `predictability_uniformity` family is ablated, with an additional Tier-1-silent precondition (Codex round 2 P1) to catch threshold drift that would weaken the regression.

### Changed

- `audit_text()`'s signature grows three optional keyword args (`do_tier4`, `tier4_score_fn`, `tier4_backend`) — all default to OFF/None so every existing caller's behavior is preserved bit-for-bit.

### Notes

- Tier 4 is **opt-in**. Operators who don't pass `--tier4` see the v1.46.0 behavior end-to-end. This protects existing test runs, calibration runs, and downstream consumers from the 1-2 orders of magnitude scoring cost overhead that Tier 4 carries.
- Tests inject a stub `score_fn` so no real causal LM is loaded — the test suite remains transformers-free and fast.
- PROVISIONAL bands per SPEC §3.5: the Tier 4 entries' values come from fixture-derived heuristics, NOT a labeled-corpus calibration. Operators wanting load-bearing thresholds run the Phase C.5 fixture suite against their own baseline per `scripts/calibration/PROVENANCE.md`.
- Phase C is now complete: C.1 (this spec), C.2 (backend, PR #23), C.3 (standalone audit, PR #30), C.4 (this PR, Tier 4 integration). C.5 is operational (calibration runs on the AMD desktop), not a framework PR.
- **Round-2 reviewer P2 fix carried**: Tier 4 backend identifier + markdown rendering.
- **Round-2 reviewer P1 carried**: Tier-1-silent precondition pinned in the ablation regression test (Codex review).
- **Version-bump note**: rebased from declared 1.47.0 → 1.55.0 because Waves 1 + 2 + Wave 3 + #26 (1.53.0) + #27 (1.54.0) merged ahead at 1.45.0 – 1.54.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.54.0] - 2026-05-14

**Sharded calibration v1.44.2 — multi-machine git-synced state file.** The final phase of the sharded-calibration toolchain per `internal/SPEC_sharded_calibration.md` §7.3 (originally v1.43.2). When state.json lives inside a git working tree with a configured remote, workers automatically pull-before-read and commit + push after each state transition (claim, done, failed, resume). Multiple hosts share one logical sharded run via git; rare cross-host conflicts are caught at push time and resolved via the new `resolve-conflict` subcommand. Stacked on v1.44.1.C (PR #26).

The framework's three sharded-calibration coordination layers are now complete:

1. **Within a process** (v1.44.1.A): atomic `O_CREAT | O_EXCL` claim files prevent two workers in the same process from claiming the same shard.
2. **Within a host** (v1.44.1.A): `fcntl.flock(LOCK_EX)` state-update lock serializes RMW windows on `state.json`.
3. **Across hosts** (this commit): git pull-rebase + commit + push round-trip on every transition, with a structured 3-way merge for the rare cross-host conflict.

### Added

- **`shard_state.find_git_repo(path)`** — pure-Python walk up from `path` looking for a `.git` entry. Returns the repo root, or `None`. Auto-detects whether state.json is under git without invoking the git CLI for the check.
- **`shard_state.is_git_synced(state_path)`** — convenience predicate (`find_git_repo() is not None`). Wrapping enables the auto-detect-by-default sync semantics: operators don't have to pass a flag — committing the `calibration_runs/` directory to a git repo IS the opt-in.
- **`shard_state.pull_state(state_path, *, enabled=True, remote, branch)`** — runs `git -C <repo_root> pull --rebase --quiet` for the repo containing `state_path`. Returns True if a pull was attempted, False if skipped (not in repo, or `enabled=False`). Discriminates conflict-bearing errors (raises `SyncError` with a `resolve-conflict` hint) from transient errors (raises `SyncError` with a "transient" hint that callers can swallow).
- **`shard_state.push_state(state_path, *, message, enabled=True, remote, branch, retries=3)`** — stages state.json, commits with `message`, and pushes. Returns False on "nothing to commit" (informational, treated as success). On push race (`non-fast-forward`), pulls + retries up to `retries` times before giving up.
- **`shard_state._git(repo_root, args, *, timeout, check)`** — centralized git invocation so tests can monkeypatch one function instead of dozens of `subprocess.run` calls.
- **`shard_state.SyncError`** — exception type raised on unrecoverable sync errors. Transient errors (network blips) raise `SyncError` too; the caller decides whether to swallow based on the message.
- **`shard_state.merge_state_files(base, ours, theirs)`** — structured 3-way merge over two competing state.json revisions. Returns `(merged_state, unresolved_shard_ids)`. Merge policy: trivially-mergeable cases (one side untouched, or both sides made identical changes, or both touched disjoint shards) auto-merge; for same-shard-both-sides-changed, the helper takes the more-advanced state by rank with `failed` treated as terminal unless the other side recorded `done` (the policy fix below); for tied-rank-different-host (the canonical "same-shard concurrent claim" case per spec §4.6), the shard is reported in `unresolved_shard_ids` and the operator must intervene.
- **`shard_runner._synced_state_update(args, state_path, *, message, worker_label)`** — context manager that wraps the state-update lock with `pull_state` before and `push_state` after. Replaces every `with state_update_lock(sp):` call site in the worker and sweep-stale paths. Transient sync errors are logged and swallowed; conflict-bearing errors re-raise so the caller can bail out and point the operator at `resolve-conflict`.
- **`shard_runner._should_sync(args, state_path)`** — central decision helper: returns False if `--no-sync-state` was passed, else `is_git_synced(state_path)`. Tests use tmp_path (not in a git repo) so the sync path is silently skipped without any flag dance.
- **`shard_runner work --no-sync-state`** flag — opt out of git sync even when state.json is in a git repo (debugging, alternative sync mechanisms). Default behavior remains "sync if state.json is in a git repo, else local-only."
- **`shard_runner sweep-stale --no-sync-state`** — same flag on sweep-stale (which also mutates state.json).
- **`shard_runner resolve-conflict --run-id RUN` subcommand** — structured 3-way merge for state.json after a multi-machine sync conflict. Reads the three git index stages via `git show :1:<path>`, `:2:`, `:3:`, runs `merge_state_files`, writes the merged result back to the working tree. Exits rc=7 if any shards remain unresolved (default `--abort-on-unresolved`); rc=0 if the merge resolved everything. `--continue-rebase` automatically runs `git add` + `git rebase --continue` on a clean merge.
- **`plugins/setec-voiceprint/scripts/calibration/RUNBOOK_multi_machine_sync.md`** — operator-facing 6-section runbook: when to use sync, deterministic-split fallback vs. git-synced state tradeoffs, one-time per-host setup, daily progress checks, failure-mode triage (network blip / push race / real conflict / permanently-offline host), teardown + aggregation.

### Changed

- **`_run_single_worker` claim path**: replaces `with state_update_lock(sp):` with `with _synced_state_update(args, sp, message=...):`. Sync errors of the conflict-bearing variety propagate; the worker exits with the new rc=6 sentinel after `release_claim`, pointing the operator at `resolve-conflict`.
- **`_process_shard`** (mark_pending_resume, mark_failed, mark_done paths): same replacement. Commit messages encode the transition (shard id, host, what happened) so the git history is a readable audit trail of who did what when.
- **`cmd_sweep_stale`**: state.json release also goes through `_synced_state_update` so a multi-machine run sees the release on every host within one git round-trip.

### Notes

- The git layer is the cross-host coordination primitive. The atomic-rename `.claim` files from v1.44.1.A only prevent races within one host's filesystem; cross-host races get caught at `git push` time as non-fast-forward errors and trigger the pull-rebase-retry loop.
- Most cross-host runs will never trigger `resolve-conflict`: different hosts naturally pick different shards (claim files in the local filesystem ensure that, with worker-loop logic backing them up), and disjoint shard diffs merge trivially via `git pull --rebase`. The subcommand exists for the genuinely-pathological case (clock skew + race + bad luck).
- Spec §2.7's "deterministic-split fallback mode" is supported and documented in the RUNBOOK as the recommended path for 2-host setups: each host gets a fixed half of the shards, no git sync needed. The runbook calls this out explicitly because it's the simpler path.
- This completes the v1.44.x sharded-calibration toolchain. The pending follow-ups are:
  1. Default scorer's opt-in to `SigtermInterrupt` for mid-shard checkpointing (deferred to its own PR; framework primitive shipped in v1.44.1.B).
  2. Smoke test against MAGE under the sharded pipeline (end-to-end validation; spec §6.3).
  3. Real RAID-scale Tier 1 calibration on the AMD desktop (the load-bearing motivation for all of v1.44).
- **Round-2 reviewer P0 fix carried**: `merge_state_files` now treats `failed` as terminal unless the other side recorded `done`. Pre-fix the state-rank `failed` < `pending` < `claimed` could silently resurrect a failed shard when the other side bumped the shard back to pending/claimed (e.g., a `sweep-stale` on the remote, or a host that never observed the failure attempting to re-claim). Post-fix the merge keeps `failed` in place against any non-`done` competing state; the only state that overrides `failed` is `done` (because `done` means another host genuinely re-ran and succeeded). Tests cover failed-vs-pending, failed-vs-claimed, failed-vs-claimed-pending-resume, and failed-vs-done.
- **Round-2 reviewer P2 carried**: `push_state` retries on transient push-race rc=128; malformed claim files (non-JSON, missing keys) are treated as missing for the read path rather than crashing the worker (Codex review P2).
- **Round-2 reviewer P2 round-2 carried**: `git commit` failure now distinguishes no-op (clean working tree) from a real commit failure (`git diff --cached --quiet` separates the two before `git commit` runs; identity / hook failures still surface as `SyncError`).
- **Cross-stack rebase against #25**: this branch was rebased against the latest v1.44.1.B in main (Codex flagged the pre-fix base). `_synced_state_update` is layered on top of #25's race tolerance + signal-dedupe ordering + resume claim refresh; tests confirm the synced path preserves those invariants.
- **Test counts updated**: 18 new tests in `test_shard_state.py` (was 15: +3 to cover the failed-terminal-vs-done policy), 13 new tests in `test_shard_runner.py`.
- **Version-bump note**: rebased from declared 1.48.0 → 1.54.0 because Waves 1 + 2 + Wave 3 + #26 (1.53.0) merged ahead at 1.45.0 – 1.53.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.53.0] - 2026-05-14

**Sharded calibration v1.44.1.C — launchd nightly setup for macOS.** The third of three v1.44.1 phases per `internal/SPEC_sharded_calibration.md` §7.2 (originally v1.43.1 in the spec). Ships the launchd plist template, the caffeinate wrapper script, the operator-facing `setup_launchd.py` renderer/installer, and a step-by-step RUNBOOK for macOS nightly setup. Stacked on v1.44.1.B (PR #25).

The goal: an operator with a freshly-sharded run can install the nightly agent in ~10 minutes and walk away. The agent fires at the configured start hour, runs under `caffeinate -i` (blocks idle sleep but lets the display dim), invokes `shard_runner work --time-window …`, exits cleanly at sunrise, and is NOT respawned by launchd until the next scheduled fire.

### Added

- **`plugins/setec-voiceprint/scripts/calibration/launchd/com.anotherpanacea.setec-voiceprint.shard-worker.plist.template`** — launchd plist template with `{{LABEL}}`, `{{WRAPPER_PATH}}`, `{{LAUNCHD_LOG_PATH}}`, `{{START_HOUR}}`, `{{START_MINUTE}}` placeholders. Encodes the spec §2.8 contract: `KeepAlive.Crashed=true`, `KeepAlive.SuccessfulExit=false`, `RunAtLoad=false`, `ProcessType=Background`, `ThrottleInterval=60`.
- **`plugins/setec-voiceprint/scripts/calibration/launchd/run_shard_worker.sh.template`** — wrapper shell-script template. Composes a date-stamped log path (launchd's `StandardOutPath` can't do date substitution), then `exec`s `/usr/bin/caffeinate -i python3 shard_runner.py work …`. Closes over the operator's `--time-window`, `--workers`, `--use`, `--run-id`, and `--base-dir`.
- **`plugins/setec-voiceprint/scripts/calibration/launchd/setup_launchd.py`** — Python helper that renders both templates, validates the config (absolute-path checks, label well-formedness, hour/minute bounds), writes them to a staging directory (`~/.setec-voiceprint/launchd/` by default), and on `--install` copies the plist to `~/Library/LaunchAgents/` plus runs `launchctl bootstrap gui/<uid>`. Default is dry-run: prints the commands an operator would run themselves. `--uninstall` runs `launchctl bootout` and removes the plist.
- **`plugins/setec-voiceprint/scripts/calibration/launchd/RUNBOOK_macos_nightly.md`** — 7-step operator runbook: prerequisites, dry-run inspection, install, manual wrapper test, observe first fire, pause/resume mid-run, uninstall, troubleshooting (`plutil -lint`, log paths, common failure modes).
- **`plugins/setec-voiceprint/scripts/tests/test_setup_launchd.py`** — 29 tests covering: RenderConfig validation (relative paths rejected, label well-formedness, hour/minute bounds, zero workers rejected), plist rendering (every placeholder substituted, `plistlib` round-trip, `KeepAlive` semantics, `StartCalendarInterval`, `ThrottleInterval=60`, `RunAtLoad=false`), wrapper rendering (`caffeinate -i` present, `--time-window` passed through, run_id + base_dir interpolated, bash shebang), filesystem writes (plist + wrapper written, wrapper chmod +x), launchctl helpers (dry-run does NOT copy, `bootstrap`/`bootout` command shapes, modern `gui/<uid>` syntax), `_parse_start_time` (HH:MM extraction, whitespace tolerance, malformed input rejected), CLI end-to-end (dry-run produces both files in staging, `--install` + `--uninstall` mutual exclusion, bad time-window rejected with rc=2, relative `--base-dir` rejected via validator).

### Notes

- The CLI's default is `--dry-run` semantics: nothing under `~/Library/LaunchAgents/` is mutated unless the operator passes `--install`. This is the safe-by-default behavior — getting launchd wrong means a daemon misbehaving on a personal laptop, so we make the install step explicit.
- `caffeinate -i` blocks **idle** sleep only. The display can dim and sleep, and disk/USB devices can spin down. The wrapper deliberately does not use `-dimu` (which keeps the display lit) because nightly runs should not light up a screen the user has left dark on purpose.
- `KeepAlive.SuccessfulExit=false` is load-bearing for the time-window semantics: when the worker detects local time has left the window and exits with rc=0, launchd treats it as "job's done for now" and does NOT respawn. The next `StartCalendarInterval` tick is what triggers the following night's run.
- v1.44.2 (next) ships multi-machine git-synced state-file conventions so a Mac + AMD-desktop pair can share a sharded run.
- **Round-2 reviewer P1 carried**: `--install` is now idempotent. The installer runs a best-effort `launchctl bootout` before `bootstrap` so re-running setup after config changes succeeds even when a previous agent is loaded. Tests cover the bootout-then-bootstrap sequence and the no-prior-agent path.
- **Earlier reviewer P2 carried**: XML + shell escaping fixes in `setup_launchd.py` (plistlib for plist generation; `shlex.quote` for shell-arg interpolation in the wrapper template).
- **Version-bump note**: rebased from declared 1.47.0 → 1.53.0 because Waves 1 + 2 + Wave 3 (PRs #21 / #22 / #24 / #32 / #36 / #29 / #33 / #34 / #35 / #25 / #23 / #30) merged ahead at 1.45.0 – 1.52.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.52.0] - 2026-05-14

**Standalone surprisal audit (phase C.3).** Adds `plugins/setec-voiceprint/scripts/surprisal_audit.py`, the standalone CLI that wraps the C.2 surprisal backend and reports the per-token surprisal series statistics pinned in `internal/SPEC_surprisal_signal.md` §2.2 / §2.3. Task surface: `smoothing_diagnosis`. Stacked on C.2 (PR #23). The Tier 4 integration into `variance_audit.py` (C.4) ships separately so each phase reviews independently.

The audit answers: how evenly is the LM's surprise distributed across this draft? Smoothing — the operational fingerprint of LLM editing / generation — manifests as low mean surprisal, low SD, and high small-lag autocorrelation. The audit reports raw numbers and a PROVISIONAL band call; the ClaimLicense block names `calibration_anchor: user-baseline-required` so the band is never read as load-bearing.

### Added

- **`surprisal_audit.py`** — new standalone audit CLI.
  - `audit_surprisal(text, backend|score_fn, ...)` — pure function. Accepts either a `SurprisalBackend` or a callable `score_fn` matching the backend's `score_text(text, return_top_k=...)` signature (the test-friendly path).
  - Distribution summary: mean (bits/token), sample SD + variance, min/max, skew, excess kurtosis, position of max surprisal.
  - Autocorrelation at lags 1, 2, 3, 5, 10 per SPEC §2.2. ACF is `None` for series below `MIN_SERIES_FOR_ACF = 30` tokens (with a `series_too_short_for_acf` flag) and for constant series (zero denominator).
  - Top-k most-surprising tokens (default k=20) — reader-facing diagnostic with decoded token text + position.
  - `--sliding-window` mode per SPEC §2.4: token-indexed (not word-indexed; surprisal's native unit is tokens). Default W=200, S=100. Per-window stats: mean, sd, lag-1 ACF.
  - `_provisional_band()` — 2-of-3 majority-vote classifier over mean / SD / lag-1 ACF using illustrative thresholds. Always emits `provisional=True` and `calibration_anchor: user-baseline-required` so the band call is never read as load-bearing.
  - `render_markdown()` — markdown report with distribution summary, autocorrelation, sliding-window trajectory (when enabled), top-k table, PROVISIONAL band, and the ClaimLicense block.
  - `ClaimLicense` block names `smoothing_diagnosis` task surface and refuses an AI-provenance verdict explicitly; PROVISIONAL banding called out in `additional_caveats`.
  - CLI: `--model`, `--revision`, `--sliding-window`, `--window-size`, `--stride`, `--top-k`, `--json`, `--out`.
- **`plugins/setec-voiceprint/scripts/tests/test_surprisal_audit.py`** — 41 tests in 6 classes covering pure math helpers (14), sliding-window logic (6), provisional banding (4), end-to-end audit_surprisal (10), markdown rendering (3), CLI end-to-end (3). Tests use a stub `score_fn` so no real causal LM is loaded.

### Notes

- No real causal LM is loaded by the test suite — the stub-backend pattern from `test_surprisal_backend.py` is reused. The `score_fn` parameter on `audit_surprisal` is the explicit hook for that.
- Phase C.4 (`variance_audit.py` Tier 4 integration) ships next as a separate PR. It uses the same backend + audit math but inside the existing variance-audit tier structure; the standalone CLI here remains the single-purpose entry point for operators who want a focused surprisal report without the Tier 1-3 overhead.
- Bands are PROVISIONAL: thresholds come from fixture-derived heuristics, not anchored calibration. Operators who want load-bearing thresholds run the Phase C.5 fixture suite per SPEC.
- **P2 fix carried**: clean long-input failure path (Codex review). Wraps backend runtime failures so the CLI exits cleanly when the input exceeds the model's context window.
- **Version-bump note**: rebased from declared 1.46.0 → 1.52.0 because Waves 1 + 2 + PRs #33 / #34 / #35 / #25 / #23 merged ahead at 1.45.0 – 1.51.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.51.0] - 2026-05-14

**Surprisal backend (phase C.2).** Implements the pluggable causal-LM wrapper from `internal/SPEC_surprisal_signal.md` §6 and `internal/SPEC_surprisal_model_choice.md`. Structurally parallel to `embedding_backend.py`: alias table, lazy load, honest failure (no silent fallback), deterministic mode, `identifier_block()` for PROVENANCE. Adds Tier-4 (surprisal) dependency on `transformers` + `torch`; opt-in, not part of core install.

### Added

- **`scripts/surprisal_backend.py`** — pluggable wrapper around `transformers` causal LMs. Alias table covers the five §6.4 candidates per the embedding-spec analog (`gpt2`, `llama32_1b`, `phi3_mini`, `qwen25_1_5b`, `tinyllama`). Implements teacher-forcing surprisal computation: tokenize → single forward pass → log-softmax → per-position surprisal in bits. Returns the surprisal series for any text; optional `return_top_k` produces a reader-facing diagnostic of the k most-surprising tokens with decoded text.
- **`scripts/tests/test_surprisal_backend.py`** — 20 tests covering alias resolution (5 §6.4 candidates plus default), reverse lookup, lazy load (no model download on `--help`), missing-package handling (raises `SurprisalBackendError` with install hint when `transformers` is missing), model-load failure handling, empty/whitespace input bypassing load, top-k diagnostic shape, identifier-block PROVENANCE shape. Three math tests (uniform-logits → expected surprisal, top-k contract, single-token empty-series) gated on `torch` being installed; skipped cleanly without it.

### Notes

- **Default model: `tinyllama`** — chosen on documented-training-cutoff + smallest-footprint grounds, NOT on a claim that tinyllama is best. The spec's no-priority posture stands; the §5.4 fixture test on the user's register mix is the load-bearing decision for what becomes the operational default in any given deployment.
- **No audit-script integration yet.** C.2 ships the backend alone. **C.3** (standalone `surprisal_audit.py`) and **C.4** (`variance_audit.py` Tier-4 integration) follow as separate PRs.
- **Opt-in dependency.** `transformers` and `torch` are not part of the core install. Users who want surprisal install them explicitly (will be documented in `requirements-surprisal.txt` when C.3 ships). The wrapper raises `SurprisalBackendError` with an install hint when the deps are missing — no silent fallback.
- **Stylometry-to-the-people compliance.** Per-document surprisal series stay local — the wrapper does not redistribute. Future surprisal-derived thresholds land in PROVENANCE-as-audit-record, not in `COMPRESSION_HEURISTICS` as load-bearing defaults.
- **Version-bump note**: rebased from declared 1.45.0 → 1.51.0 because Waves 1 + 2 + PRs #33 / #34 / #35 / #25 merged ahead at 1.45.0 – 1.50.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.50.0] - 2026-05-14

**Sharded calibration v1.44.1.B — scheduling + control plane.** The second of three v1.44.1 phases per `internal/SPEC_sharded_calibration.md` §7.2 (the spec originally labelled it v1.43.1; we're shipping under the v1.44.1 banner with the same scope). Adds the cooperative-shutdown and operator-control primitives that make multi-day unattended runs survivable: `--time-window` for nightly schedules, `pause-all` / `terminate-all` / `kill-all` / `sweep-stale` subcommands for operator control, and a `SigtermInterrupt` exception contract that lets opt-in scorers checkpoint mid-shard. The default scorer doesn't yet honor `SigtermInterrupt`; the contract is in place so a follow-up can wire up `load_or_score_corpus` once we have a real RAID-scale run to test against. Stacked on v1.44.1.A (PR #24).

### Added

- **`shard_state.pid_alive(pid)`** — best-effort POSIX liveness check via `os.kill(pid, 0)`. Returns False on `ProcessLookupError`; treats `PermissionError` and unknown `OSError` as alive (conservative: refusing to release a claim we can't conclusively prove is dead). Used by `sweep-stale`, `terminate-all`, and `kill-all`.
- **`shard_runner.SigtermInterrupt`** — exception scorers raise to checkpoint mid-shard. Carries `n_entries_flushed`, `n_entries_total`, and an optional `partial_cache_path`. The orchestrator catches the exception, transitions the shard to `claimed_pending_resume`, leaves the claim file in place (resume eligibility per spec §2.4), and exits the worker loop cleanly with rc=0. The default scorer does NOT yet raise it (that integration is gated on a real RAID-scale run); the contract + worker handling are the framework-level primitive.
- **`shard_runner.pause_marker_path(base, run_id)`**, **`is_paused`**, **`write_pause_marker`**, **`clear_pause_marker`** — atomic-rename write/read for the `.pause` marker. Workers check it between shards and exit cleanly when present; resume by clearing the marker.
- **`shard_runner.parse_time_window(spec)`** and **`is_within_time_window(window, now=None)`** — pure helpers for the `HH:MM-HH:MM` schedule gate. Handles cross-midnight windows (23:00-06:00 includes 03:00). A `None` window always returns "in window" so the default no-flag behavior is "run anytime."
- **`shard_runner work --time-window HH:MM-HH:MM`** — between-shard gate. Outside the window, the worker exits cleanly (launchd will see `SuccessfulExit: false` honored: clean exit means "stop until the next StartCalendarInterval fires"). A shard already in progress finishes (potentially past the deadline) per spec §2.5.
- **`shard_runner pause-all` subcommand** — writes `.pause` so running workers exit cleanly between shards. `--reason "string"` stored alongside the timestamp. `--clear` removes the marker (resume); returns rc=1 when there was nothing to clear (informational, not an error).
- **`shard_runner terminate-all` subcommand** — sends SIGTERM to every distinct local pid recorded in this run's `.claim` files. Skips remote-host claims (we can't signal across hosts from POSIX); skips dead pids (pid reuse is a real failure mode). De-dups pids so a worker holding multiple claims gets exactly one signal.
- **`shard_runner kill-all` subcommand** — same skeleton as `terminate-all` but sends SIGKILL. Last-resort path; partial state is NOT flushed. After kill-all the operator runs `sweep-stale` to release the abandoned claim files.
- **`shard_runner sweep-stale` subcommand** — walks the run's shard directories and releases claim files whose owning pid is dead AND whose `claimed_at` timestamp is older than the threshold (default 6 hours; configurable via `--stale-hours`). The two-condition gate defeats the same-pid race: a worker that crashed and immediately restarted on a different pid would otherwise have its new pid's work silently released. `--include-resume` also releases `claimed_pending_resume` shards (rare; only when the original host is permanently gone). `--dry-run` reports without acting. Cross-host claims are never swept by `sweep-stale` (POSIX can't cross-host).
- **3 new tests in `test_shard_state.py`** covering `pid_alive` (self-alive, sentinel-dead pid, PermissionError + unknown OSError both treated as alive).
- **17 new tests in `test_shard_runner.py`** covering pause marker (write + clear roundtrip, clear-with-no-marker returns rc=1, work exits cleanly under marker), time-window (parser including cross-midnight + invalid input, gate behavior in/out of window via monkeypatched `datetime.now`, malformed flag rejected with rc=2), SigtermInterrupt (mid-shard checkpoint marks claimed_pending_resume + keeps claim file + rc=0, resume path on second invocation completes the shard), sweep-stale (releases dead+old, skips live pid, skips young+dead, dry-run leaves state intact, preserves claimed_pending_resume by default + releases with --include-resume, skips remote-host claims), and terminate-all / kill-all (no-workers returns rc=1, signals active pid with correct signal, skips remote-host claims).

### Changed

- **`_run_single_worker` loop adds two between-shard exit gates**: pause-marker check and time-window check. Both trigger clean rc=0 exits — distinguishable from SIGTERM (which also exits the loop cleanly but signals "operator hit Ctrl+C") and from failures (rc=4).
- **`_process_shard` catches `SigtermInterrupt`**: extracts `n_entries_flushed` / `n_entries_total`, calls `mark_pending_resume` under the state-update lock, returns the new sentinel rc=5. The caller (`_run_single_worker`) translates rc=5 into rc=0 for the worker process exit code while skipping the `release_claim` step (the claim file must survive for resume eligibility).

### Notes

- The spec's v1.43.1 phase included `--workers N` alongside scheduling; we already shipped `--workers N` in v1.44.1.A, so v1.44.1.B is scheduling-only. The phase split keeps each PR reviewable.
- v1.44.1.C (next) ships the launchd plist template + caffeinate runbook so the macOS nightly setup can be operator-launched in one shot.
- v1.44.2 (after that) ships multi-machine git-synced state file conventions.
- The default scorer's opt-in to `SigtermInterrupt` is intentionally deferred. The framework primitive is in place and tested via a stub scorer; the real `load_or_score_corpus` change waits until we have an actual RAID-scale run that benefits from mid-shard checkpointing (and a way to validate the behavior end-to-end).
- **Round-5 reviewer fixes carried**: stale-state races are now soft races in the worker loop, and PID de-dupe happens only after a successful signal (Codex review). Round-4 `process_start_time_epoch` capture and resume-claim file refresh are also included.
- **Version-bump note**: rebased from declared 1.46.0 → 1.50.0 because Waves 1 + 2 + PRs #33 / #34 / #35 merged ahead at 1.45.0 – 1.49.3. MINOR-tier bump preserved since this is a `feat:` change.

## [1.49.3] - 2026-05-14

**Retroactive P2 fix — `evidentiary_conditions_gate` honors `available` flag in usability checks + metadata reads.** Reviewer P2 from the retroactive audit of R6 (commit 7eaa1ef — evidentiary gate + controls audit). `_is_usable_variance` and `_is_usable_voice_distance` checked structural keys (`compression`, `overall`, `families`) but did NOT honor `available: False`. An unavailable payload can still carry these blocks from a stale snapshot or partial run, and the gate counted them as usable surfaces. Reviewer reproduced `research_grade_validation` from a variance payload marked unavailable plus a confounder, because `_read_baseline_size` pulled `n_files` without checking availability AND the variance surface was counted via the structural check alone.

### Fixed

- `_is_usable_variance` and `_is_usable_voice_distance` now refuse payloads with `available: False`, mirroring the existing `_is_usable_paragraph` shape. `_is_usable_confounder` and `_is_usable_gi` get the same fix for parity — all four usability helpers now share consistent semantics.
- `_read_baseline_size` skips payloads with `available: False` for the variance / voice_distance reads AND for the per-surface paragraph / discourse / agency / punctuation / stance / function_grammar reads. Pre-fix, an unavailable payload's stale `baseline.n_files` contributed to the baseline-size indicator.
- `_read_register_match_strength` and `_read_strip_ratio` also honor the available flag — pre-fix they pulled from possibly-stale `register_match` / `preprocessing` blocks on unavailable payloads.

### Tests

- `+18 new` across four classes:
  - `TestUnavailablePayloadsAreRefused` (7): each usability helper refuses available=False; backwards compat for missing `available` key preserved.
  - `TestBaselineSizeIgnoresUnavailable` (6): each baseline-read source (variance, voice_distance, paragraph) skips unavailable payloads; max-of-multiple sees only the available payloads; missing-`available`-key backwards compat preserved.
  - `TestReadHelpersIgnoreUnavailable` (4): register-match-strength + strip-ratio both ignore unavailable payloads.
  - `TestReviewerReproducerScenario` (1): end-to-end reproducer of the reviewer's exact scenario (unavailable variance + confounder) — posture is NOT promoted to `research_grade_validation` post-fix.

### Notes

- **Version-bump note**: rebased from declared 1.44.1 → 1.49.3 because Waves 1 + 2 + PRs #33 / #34 merged ahead at 1.45.0 – 1.49.2. PATCH-tier bump preserved since this is a `fix:` change.

## [1.49.2] - 2026-05-14

**Retroactive P2 fix — `confounder_audit` honors `available` flag + density-key presence.** Reviewer P2 from the retroactive audit of R3 (commit 489e1b7 — confounder_audit + Layer D). `extract_observations()` treated `if audit:` as "input present" and walked into `.get(key, 0.0)` calls for failed audits. A discourse audit with `available: False` silently emitted `discourse_marker_density=low`. An unavailable agency audit silently emitted four "low" observations (nominalization, agentless_passive, generic_institutional, concrete_detail), altering the ranked confounders without any actual evidence.

### Fixed

- New `_audit_is_usable(audit)` helper: returns True iff `audit` is not None AND `audit.get("available")` is not False. Missing `available` key is treated as True for backwards compat with older audit JSONs (R1-R6 era).
- Every audit-input gate in `extract_observations` (variance, voice_distance, paragraph, discourse, aic, agency, idiolect) now uses `_audit_is_usable()` instead of the bare truth check.
- Density-keyed observations in the discourse and agency blocks now require the specific per-1k key to be present AND numeric. Pre-fix, missing keys defaulted to 0.0 via `.get(..., 0.0)` and fired the low-band branch silently. Post-fix, missing keys produce no observation.

### Tests

- `+12 new` in `TestUnavailableAuditsAreIgnored` (7) and `TestDensityKeyPresenceRequired` (5):
  - Each audit input emits no observations when `available: False`
  - Missing `available` key is treated as available (backwards compat)
  - Empty `densities_per_1k` emits nothing
  - Partial densities emit only the present keys
  - Non-numeric density values produce no observation

### Notes

- **Version-bump note**: rebased from declared 1.44.1 → 1.49.2 because Waves 1 + 2 + PR #33 merged ahead at 1.45.0 – 1.49.1. PATCH-tier bump preserved since this is a `fix:` change.

## [1.49.1] - 2026-05-14

**Retroactive P2 fix — `embedding_backend` encode-time runtime-error wrapping.** Reviewer P2 from the retroactive audit of R12 semantic-trajectory (PR #16). `EmbeddingBackend.encode()` wrapped load failures in `EmbeddingBackendError` but NOT runtime failures from `model.encode()`. A bare `RuntimeError` / `IndexError` / `MemoryError` from sentence-transformers (context-window overflow, device OOM, tokenizer surprise) escaped, and `semantic_trajectory_audit.main()` only catches `EmbeddingBackendError` → CLI tracebacked instead of the documented clean-error path. Same shape as the surprisal-audit P2 fix from PR #30.

### Fixed

- `EmbeddingBackend.encode()` now wraps `(MemoryError, RuntimeError, IndexError, ValueError, OSError)` from the underlying `model.encode()` call in `EmbeddingBackendError`, with a diagnostic message naming the typed exception and the common causes (context window, memory, tokenizer shape). Typed `EmbeddingBackendError` exceptions raised from inside the model pass through unchanged so callers that distinguish load-vs-runtime failures see the original message.

### Tests

- `+7 new tests` in `TestEncodeRuntimeErrorWrapping`: every wrapped exception class (RuntimeError, IndexError, MemoryError, ValueError, OSError) gets a focused test; typed `EmbeddingBackendError` pass-through preserved; empty-input short-circuit unaffected. Stubbed model (no sentence-transformers dependency) so the test runs without GPU/transformers installed.

### Notes

- **Version-bump note**: rebased from declared 1.44.1 → 1.49.1 because Waves 1 + 2 (PRs #21 / #22 / #24 / #32 / #36 / #29) merged ahead at 1.45.0 – 1.49.0. PATCH-tier bump preserved since this is a `fix:` change.

## [1.49.0] - 2026-05-14

**Authorship-state taxonomy refinement — phase B.3 (start): claim-license state routing.** Adds the shared `claim_license` helper that audit scripts use to emit state-routed caveats, plus wires the first two exemplar scripts (`stance_modality_audit.py`, `discourse_move_signature.py`) per `internal/SPEC_authorship_states.md` §9. Other audit scripts that import `claim_license` (general_imposters, semantic_preservation_check, etc.) get wired in follow-up PATCH PRs thematically grouped per SPEC §10. Stacked on B.4 (PRs #28 / #36).

The framework now distinguishes inference licensure by authorship state: a ClaimLicense block emitted against a `pre_ai_human` target reads differently from one emitted against `ai_generated_from_outline`. The differential matters because the stylometric fingerprint of outline-seeded LLM generation differs from thin-prompt generation, and a verdict that doesn't acknowledge this distinction is licensure overreach.

### Added

- **`claim_license.TARGET_STATE_CAVEAT_TEMPLATES`** — dict mapping each of the seven canonical `ai_status` values to operator-readable caveat text. Pinned per SPEC §9.2. Covers `pre_ai_human`, `ai_generated`, `ai_generated_from_outline`, `ai_assisted`, `ai_edited`, `mixed`, `unknown`.
- **`claim_license.COMPARISON_STATE_CAVEAT_TEMPLATES`** — dict mapping exact-match comparison-baseline state sets (e.g., `{pre_ai_human}` only, or `{ai_generated}` only) to anchored caveats. The helper falls back to a generic "mixes authorship states" caveat (naming each state) when no exact match.
- **`claim_license.state_routed_caveats(target_ai_status, comparison_ai_statuses)`** — pure function returning the list of state-routed caveats for the given inputs. Returns `[]` when neither input is supplied — keeps the helper a safe-by-default no-op for pre-B.3 callers.
- **`claim_license.with_state_caveats(license_block, *, target_ai_status, comparison_ai_statuses)`** — takes a `ClaimLicense` and returns a new one with state caveats appended to `additional_caveats`. All other fields pass through unchanged. Idempotent when called with no state inputs.
- **`stance_modality_audit.py --ai-status` flag** — operator supplies the manifest entry's `ai_status` for the target; the rendered ClaimLicense block gains the matching caveat.
- **`discourse_move_signature.py --ai-status` flag** — same wiring.
- **21 new tests** across `test_claim_license_migration.py` (+13 covering the helper) and the new `test_b3_state_routing.py` (+8 covering end-to-end CLI behavior for both exemplar scripts).

### Changed

- `stance_modality_audit._claim_license_block` calls `with_state_caveats(lic, target_ai_status=audit.get("ai_status"))` after building the base block. No behavior change when `--ai-status` is absent.
- `discourse_move_signature._claim_license_block` — same wiring.
- Both audit scripts surface `--ai-status` into their JSON output dict as an `ai_status` field, so downstream consumers reading the JSON path can also route on state without having to re-pass the flag.

### Notes

- B.3 is intentionally a rollout, not a single PR. This commit ships the helper + 2 exemplar scripts; the remaining `claim_license`-using audit scripts (general_imposters, semantic_preservation_check, mimicry_cosplay_audit, surface_disagreement_resolver, confounder_audit, construction_signature_audit, punctuation_cadence_audit, adversarial_robustness_card) get wired in subsequent PATCH PRs per the SPEC §10 plan. The helper is the load-bearing piece; per-script wiring is mechanical.
- The change is rendering-layer (markdown). JSON output's claim-license shape is unchanged — downstream consumers that read the JSON keep working.
- Pre-B.3 callers that don't pass `--ai-status` see the same markdown they got in v1.45.0 / v1.46.0 / v1.48.0. The helper is no-op without state inputs.
- **Version-bump note**: rebased from declared 1.47.0 → 1.49.0 because PRs #21 / #22 / #24 / #32 / #36 merged ahead at 1.45.0 / 1.46.0 / 1.47.0 / 1.47.1 / 1.48.0. MINOR-tier bump preserved since this is a `feat:` change.

## [1.48.0] - 2026-05-14

**Authorship-state taxonomy refinement — phase B.4: converter updates.** Wires the B.2 authorship-state vocabulary additions (`ai_generated_from_outline`, `mixed` + `composite_states`) into the EditLens and MAGE converters per `internal/SPEC_authorship_states.md` §7. Stacked on B.2 (PR #22). RAID is unchanged per SPEC §7.3.

### Added

- **`editlens_to_manifest.py`**: Pangram label `-1` (the "edited/mixed" class) now maps to `ai_status: mixed` with `notes.composite_states: ["ai_edited"]` instead of being silently dropped. All three built-in presets (`editlens_nonnative`, `editlens_test`, `editlens_human_detectors`) gain the `-1=mixed` entry in their `label_map`. New `--mixed-composite-states` flag (default `ai_edited`) lets the operator customize the sub-state list; passing `""` omits the field (useful for surfacing the B.2 validator soft warning during manual review). Backwards-compat: operators who prefer the old "drop -1" behavior pass `--label-map "0=pre_ai_human,1=ai_generated"` to keep those rows out of the manifest.
- **`editlens_to_manifest.py` notes-as-dict**: notes are now written as a structured dict instead of a JSON-serialized string. Brings EditLens in line with MAGE's convention and lets the B.2 validator soft check actually walk into `notes.composite_states`. Consumers that previously called `json.loads(entry["notes"])` will now see a `TypeError` (the field is already a dict); a `isinstance(notes, str)` guard makes downstream code work with either form.
- **`mage_to_manifest.py` outline-source routing**: `_ai_status_for_label(label, src, *, outline_sources)` returns `ai_generated_from_outline` when the row's `src` is in the operator-supplied outline-sources set (case-insensitive lookup). New `--outline-sources` CLI flag (comma-separated, default empty) lets the operator opt in. Empty default is honest about the framework's uncertainty about which MAGE subsets used documented outline-based generation — see SPEC §7.2.
- **`mage_to_manifest.py` paraphrase detection**: rows whose `src` contains the tokens `paraphrase` or `dipper` (case-insensitive) are remapped from `ai_generated` to `ai_status: ai_edited` with `notes.attack: "dipper_paraphrase"` — capturing the operational reality that DIPPER paraphrase rewrites are AI-edited source text, not from-scratch generation. Default-on per SPEC §7.2 bullet 4; `--no-paraphrase-detection` opts out.
- **18 new tests** across `test_mage_to_manifest.py` (+10) and the new `test_editlens_to_manifest.py` (+13). Coverage: outline-source routing happy-paths + case-insensitivity + non-outline default; paraphrase detection heuristic + end-to-end remap + opt-out; default outline-sources empty (no surprise behavior); backwards-compat for callers building Namespace without the new args; EditLens preset label-maps include `-1=mixed`; mixed-composite-states override + empty-override; validator round-trip clean (no `composite_states`-missing warning).

### Changed

- `mage_to_manifest.py`'s `_ai_status_for_label` signature gained the optional `src` and `outline_sources` keyword arguments. Backwards-compat: positional `label`-only calls still work; the additional params default to "no outline routing."
- `editlens_to_manifest.py`'s `convert()` reads `args.mixed_composite_states` via the new flag; the flag's `None` default falls back to the preset's `mixed_composite_states` field if set, else the module-level `DEFAULT_MIXED_COMPOSITE_STATES = ("ai_edited",)`.

### Notes

- B.4 ships the converter side of the SPEC §10 phase plan. B.3 (audit-script claim-license routing) is a parallel follow-up that consumes the manifests B.4 produces.
- No converter changes for RAID — RAID has no documented outline-vs-thin-prompt distinction in its metadata (SPEC §7.3).
- Calibration runs against MAGE that want to slice on outline-derived AI prose vs thin-prompt AI prose can now do so by passing `--outline-sources` matched to the operator's MAGE export's `src` strings. PROVENANCE entries should record the `--outline-sources` value for reproducibility.
- **Version-bump note**: rebased from declared 1.46.0 → 1.48.0 because PRs #21 / #22 / #24 / #32 merged ahead at 1.45.0 / 1.46.0 / 1.47.0 / 1.47.1. MINOR-tier bump preserved since this is a `feat:` change.

## [1.47.1] - 2026-05-14

**Sharded calibration: end-to-end smoke fixture.** A test-only PATCH bump that adds `plugins/setec-voiceprint/scripts/tests/test_sharded_smoke_pipeline.py` — the canonical-operator-pipeline integration test that the existing per-subcommand tests in `test_shard_runner.py` don't cover. No production code changes; this is the regression guard that catches "operator pipeline broke even though each subcommand's unit tests pass."

### Added

- **`test_sharded_smoke_pipeline.py`** — 11 tests in 3 classes covering the full operator pipeline (`shard → work → verify → aggregate → status`) end-to-end:
  - **`TestCanonicalPipeline`** (5): `shard` writes state.json + per-shard manifests; `work` marks every shard done with cache_path + cache_sha256; `verify` passes on clean caches; `aggregate` concatenates ALL records (`n_records == source rows`); `status --json` reports 3-of-3 done.
  - **`TestPipelineFailureModes`** (4): post-work cache tampering caught by both `verify` (rc=4) AND `aggregate` (rc=2, integrity refusal); `status` before `work` shows all pending (not garbage); `aggregate` before `work` refuses cleanly; `shard` refuses to overwrite without `--force`.
  - **`TestStateCacheContract`** (2): state.json's per-shard `cache_sha256` matches the on-disk cache's actual SHA-256 (the load-bearing invariant both `verify` and `aggregate`'s integrity check depend on); `aggregate`'s `n_records` equals the sum of per-shard `n_entries` in state.json (catches silent record drops mid-merge).

### Notes

- Uses the same stub-scorer pattern as `test_shard_runner.py` (deterministic synthetic records via `hash(text_id)`). No real corpus or scoring backend involved.
- Catches integration-level regressions the per-subcommand tests miss: refactors that silently break the inter-subcommand contract (state.json fields, cache SHA path matching, aggregate's n_records math).
- This file complements rather than replaces `test_shard_runner.py`. Per-subcommand tests stay where they are; the smoke pipeline pins the cross-subcommand sequence specifically.
- **Version-bump note**: rebased from declared 1.44.1 → 1.47.1 because PRs #21 / #22 / #24 merged ahead at 1.45.0 / 1.46.0 / 1.47.0. PATCH-tier bump preserved since this is a test-only change.

## [1.47.0] - 2026-05-14

**Sharded calibration v1.44.1.A — concurrent workers with atomic claim coordination.** The first of three v1.44.1 phases per `internal/SPEC_sharded_calibration.md` §7.2. Adds `--workers N` to `shard_runner work`, multi-worker coordination via atomic per-shard claim files, and a state-update lock that serializes concurrent state.json read-modify-writes. Multi-worker sessions cut wall-clock for RAID-scale Tier 1 calibration from "single-threaded multi-day" to "N-worker proportional," with cleaner crash recovery than the v1.44.0 single-worker path.

### Added

- **`shard_state.try_claim_shard_atomically(claim_path, host, pid)`** — creates `shards/<id>/.claim` via `O_CREAT | O_EXCL | O_WRONLY`. The kernel guarantees exactly one caller wins when multiple workers race for the same shard. File content is JSON with the winning worker's host, pid, and timestamp so `sweep-stale` (v1.44.1.B) can later identify dead-host claims.
- **`shard_state.release_claim(claim_path)`** — idempotent deletion of a claim file. Workers call this on shard completion; `sweep-stale` calls it on dead-host claims.
- **`shard_state.read_claim_file(claim_path)`** — returns the claim metadata as a dict, or `None` when missing / malformed. Used by `sweep-stale` and `status` for surfacing worker ownership.
- **`shard_state.state_update_lock(state_path)`** — context manager that wraps the state.json read-modify-write window in `fcntl.flock(LOCK_EX)`. Workers serialize on the lock; the actual write still goes through `write_state`'s atomic-rename pattern. POSIX-only (the calibration host is WSL2 Linux); Windows-native is a no-op fallback.
- **`shard_runner.shard_claim_path(base, run_id, shard_id)`** — path helper for the per-shard claim file.
- **`shard_runner` `--workers N` flag on the `work` subcommand**. Default 1 (single-worker, same as v1.44.0). N > 1 spawns N subprocesses (via `multiprocessing` with the `fork` start method on POSIX) that share the run-directory but coordinate atomically.
- **`_select_next_shard(state, base, run_id)` helper** — picks the next shard candidate, preferring resumable shards owned by this host. Filters out pending shards with existing claim files so two workers racing for the same shard cannot land in an infinite retry loop.
- **9 new tests in `test_shard_state.py`** covering atomic claim contract (first wins, after-release succeeds, idempotent release, multiprocess race) plus the state-update lock serialization proof (two workers update state.json concurrently; both claims visible in the final state).
- **4 new tests in `test_shard_runner.py`** covering single-worker uses the atomic-claim path (claim files cleaned up on completion), pre-claimed-shard skip semantics (won't double-claim), two-worker integration (all shards completed cleanly, no leftover claim files, no duplicate scoring), and the workers-default-to-one backwards-compat path.

### Changed

- **`cmd_work` restructured** into a single-worker entry (`_run_single_worker`) and a multi-worker spawner (`_run_multi_worker`). Single-worker mode is unchanged from v1.44.0 in observable behavior; the implementation now goes through the atomic-claim path even with `--workers 1`, so the same coordination guarantees apply if a user manually runs multiple `shard_runner work` invocations.
- **`_process_shard` state.json updates** (mark_failed, mark_done) now go through `state_update_lock`. Single-worker mode is unaffected; multi-worker mode serializes the read-modify-write window cleanly.

### Notes

- **No mid-shard SIGTERM checkpointing yet.** v1.44.0's per-shard SIGTERM granularity carries forward. Mid-shard interrupt (the scorer threads the SIGTERM event into its loop, flushes partial cache, transitions to `claimed_pending_resume`) is scoped to v1.44.1.B per the spec.
- **No `--time-window`, no `pause-all` / `terminate-all` / `kill-all` / `sweep-stale`, no launchd plist.** Those ship in v1.44.1.B and v1.44.1.C respectively.
- **Stale-claim handling**: if a worker crashes between creating a claim file and updating state.json, the affected shard appears pending in state.json but is unclaimable (the .claim file persists). This is the case `sweep-stale` (v1.44.1.B) will handle. For v1.44.1.A, workarounds are manual: delete the offending `.claim` file by hand, or wait for `sweep-stale` to ship.
- **POSIX-only locking.** `state_update_lock` uses `fcntl.flock` on POSIX (which the calibration host runs as WSL2 Linux per `SPEC_embedding_model_choice.md` §6.3). Windows-native is a no-op fallback; the per-shard atomic claim files still coordinate correctly there, just without the state.json read-modify-write serialization. SETEC's supported calibration host is POSIX.
- **Version-bump note**: rebased from declared 1.45.0 → 1.47.0 because PRs #21 (harrier) and #22 (B.2) merged first and took the 1.45.0 / 1.46.0 slots.

## [1.46.0] - 2026-05-14

**Authorship-state taxonomy refinement (phase B.2).** Implements the validator + manifest-schema piece of the `internal/SPEC_authorship_states.md` plan: adds `ai_generated_from_outline` to the `ai_status` vocabulary and a soft consistency check on `ai_status: mixed`. Schema-additive (no existing manifests break); backwards-compat (the bare `ai_generated` value remains the catch-all when seed degree is unknown).

### Added

- **`ai_generated_from_outline`** in `ALLOWED_AI_STATUS`. Opt-in refinement of `ai_generated` for the case where the LLM was given a substantive human seed (outline, brief, transcript, point-by-point structure). The default `ai_generated` remains the backwards-compat catch-all when the seed degree is unknown or unspecified.
- **Soft consistency check** on `ai_status: mixed`. Entries with this value should carry a `notes.composite_states` array listing the authorship states present across sections. Absence produces a warning (not an error), so legacy `mixed` entries from before this check still validate. The new ratchet rule is documented in `references/manifest-schema.md` as rule 16.
- **Operational definitions** for the full `ai_status` vocabulary in `references/manifest-schema.md`. Pins the `ai_assisted` vs `ai_edited` distinction (per-suggestion human adjudication vs bulk-accepted editing), the new `ai_generated_from_outline` use criterion, and the `mixed` + `composite_states` shape.
- **`scripts/tests/test_authorship_states_b2.py`** — 10 tests covering the new vocabulary value (schema-additive, backwards compat), the soft consistency check (proper composite_states clean; absent / empty / non-list warns; non-`mixed` entries unaffected), and the warning-not-error contract.

### Notes

- **No new field.** The roadmap entry (item B) initially scoped this as adding a parallel `authorship_state` field; the spec's revision discovered `ai_status` was already six-way and only one Costa distinction (AI-generated-from-human-inputs vs fully-AI-generated) was missing from the framework's vocabulary. The chosen direction refines the existing taxonomy.
- **Phase B.3** (per-script claim-license routing where audits' evidence licensure distinguishes states) and **phase B.4** (converter updates to map source corpora onto `ai_generated_from_outline` where documented) ship as separate follow-up PRs. This PR is schema-additive only.
- **Stylometry-to-the-people compliance.** No threshold changes. No claim that anchored signals discriminate `ai_generated_from_outline` from `ai_generated`. The refinement is a vocabulary refinement that enables future audit-routing granularity (B.3), not a load-bearing per-state calibration claim.
- **Version-bump note**: rebased from declared 1.45.0 → 1.46.0 because PR #21 (harrier alias) merged first and took the 1.45.0 slot.

## [1.45.0] - 2026-05-13

**Harrier-OSS-v1-270m candidate alias.** Adds `harrier` to `embedding_backend.MODEL_ALIASES` per the embedding-model-choice spec's revision 4 candidate list. Harrier-OSS-v1-270m (Microsoft, MIT, released 2026-03-30) is one of the five §6.4 fixture-test candidates; the alias lets users select it via `--model harrier` without typing the full HuggingFace id.

### Added

- **`embedding_backend.MODEL_ALIASES["harrier"]` → `microsoft/harrier-oss-v1-270m`.** The fourth alias in the table alongside `mxbai`, `gemma`, and `minilm`. The two other §6.4 candidates (`bge-large-en-v1.5` and `Qwen3-Embedding-0.6B`) remain accessible via full HuggingFace identifier; aliases for those wait on whether the §6.4 fixture run keeps them in the load-bearing candidate set.
- **Two new tests** in `test_embedding_backend.py`: forward resolution (alias → full id) and reverse lookup (full id → alias for identifier-block reporting).

### Notes

- **No `--model` default change.** The CLI default stays at `mxbai`. Harrier is opt-in by `--model harrier` until the §6.4 fixture run designates an operational default for the user's register mix (per the no-priority posture in the spec).
- **`embedding_backend.py` comment refresh.** The alias-table comment block was updated from the "co-primary" framing (which described the pre-revision-4 posture) to "candidate aliases with no priority designated."
- **Test count**: 16 in `test_embedding_backend.py` (was 14; +2 harrier-specific). Full suite expected to grow by 2.

## [1.44.0] - 2026-05-12

**Sharded calibration toolchain v1.44.0 — core infrastructure.** Implements the core of the `internal/SPEC_sharded_calibration.md` design: deterministic stratified sharding, single-worker scoring with SIGTERM-safe checkpointing, and per-signal aggregation across shards with cache integrity enforced by default. Single-worker only for v1.44.0; `--workers N` concurrent execution, time-window scheduling, pause-all / terminate-all / kill-all, and multi-machine git-synced state file are scoped to v1.44.1 and v1.44.2 per the spec's phased rollout.

### Added

- **`scripts/calibration/sharding.py`** — pure stratified-split logic. `split_into_shards(rows, n_shards, stratify_by, seed)` produces deterministic, stratification-preserving shards that are invariant to source row order (canonical per-`text_id` sort before per-stratum shuffle). Per-stratum RNG seed derived via SHA-256 over a canonical UTF-8 string for process / machine / interpreter stability (NOT Python's built-in `hash()`, which is randomized under `PYTHONHASHSEED`). `compute_shard_count`, `shard_summary`, `estimate_stratum_balance` helpers for the CLI side.
- **`scripts/calibration/shard_state.py`** — state.json reader/writer with atomic write (temp-file + `os.replace`) and explicit state transitions: `pending` → `claimed` → `done`, with `claimed_pending_resume` and `failed` branches. SHA-256 file hashing helper for cache-integrity tracking. Read/write functions raise typed `ShardStateError` so callers can distinguish state-file failures from generic IO.
- **`scripts/calibration/shard_runner.py`** — the CLI orchestrator. Five subcommands: `shard` (split + write state), `work` (claim + score + flush + done, single worker, SIGTERM-safe), `aggregate` (concat caches + per-signal `derive_threshold_from_records`, with cache-SHA integrity enforced by default), `verify` (cache hash sanity check, standalone), `status` (state file summary, JSON or human-readable). Default scorer wraps `calibrate_thresholds.load_or_score_corpus` with the full Namespace contract (`slug`, `use`, `notes` populated); tests inject a stub via `DEFAULT_SCORER` monkeypatch.
- **`scripts/tests/test_sharding.py`** — 16 tests covering coverage, determinism (including the SHA-256-based stable-stratum-seed regression test), order-invariance, stratification balance, edge cases.
- **`scripts/tests/test_shard_state.py`** — 17 tests covering atomic write, read failure modes, all state transitions (claim, done, failed, pending_resume, reclaim), query helpers, and a simulated `os.replace` failure that proves the original state file is preserved when the rename step fails.
- **`scripts/tests/test_shard_runner.py`** — 22 integration tests covering all five CLI subcommands end-to-end with a synthetic 60-row manifest, stub scorer, and assertions on per-shard manifest writes, claim transitions, cache file integrity, aggregation correctness, verify pass/fail on tampered caches, the full Namespace contract for `derive_threshold_from_records`, refusal on missing done-shard caches, refusal on tampered done-shard caches, and graceful scorer-failure handling.

### Notes

- **Failure modes covered (per spec §4):** §4.1 (atomic write under crash), §4.4 (read_manifest raises on malformed JSON), §4.5 (claim attempts against already-claimed shards fail with typed error), §4.8 (cache hash mismatch caught by `aggregate` AND by `verify`). §4.2 (OOM kill mid-shard) and §4.7 (ROCm crash mid-scoring) are scoped to v1.44.1 (depend on the `--workers N` and pause-marker infrastructure). §4.3 (network failure during git state sync) and §4.6 (state-file merge conflict) are scoped to v1.44.2 (the multi-machine sync phase).
- **Aggregate enforces integrity by default.** Done shards with missing caches OR SHA-mismatched caches fail the `aggregate` command with exit 2 unless `--allow-partial` is passed. With `--allow-partial`, the aggregator warns and continues with the surviving shards. The artifact-producing command does not depend on a separate manual `verify` step; integrity is checked at the point of producing the survey JSON.
- **SIGTERM checkpointing in v1.44.0** is per-shard granularity only — the worker finishes the current shard then exits cleanly. Mid-shard SIGTERM honoring (flushing partial progress and transitioning to `claimed_pending_resume`) requires the worker to thread the SIGTERM event into the scorer call; that integration is scoped to v1.44.1 alongside the `--workers N` work.
- **No real-corpus smoke test ships here.** The spec's §6.3 smoke test against MAGE requires the calibration host (AMD desktop + WSL2 + ROCm) and the full calibration toolchain wired up. Smoke-testing is part of the v1.44.0 rollout, not the v1.44.0 PR. The 55 unit + integration tests exercise the core logic against a synthetic 60-row manifest; behavior against real RAID/MAGE is verified operationally when the user runs the first sharded calibration.
- **Stylometry-to-the-people compliance.** The sharded toolchain produces records caches and per-shard manifests inside `$SETEC_BASELINES_DIR/calibration_runs/<run_id>/`, all gitignored. Aggregation produces a survey JSON that is local-only by convention. Per-signal threshold sweeps via `derive_threshold_from_records` run inside aggregate but their results land in PROVENANCE-as-audit-record, not in `COMPRESSION_HEURISTICS` as load-bearing defaults.
- **1453 tests pass + 1 skipped** (was 1398 + 1 in 1.42.5; +55 from sharded calibration across three review rounds). The full suite runs in ~2.5 minutes.
- **Three review rounds**: first pass surfaced a P1 (sharding determinism under `hash()`) and two P2s (incomplete Namespace, silent-skip on missing caches); second pass surfaced one P2 (aggregate trusted on-disk caches without comparing to recorded SHA); third pass cleared the integrity-by-default fix. Each fix lands as a follow-up commit on this PR, not a separate PR.

## [1.43.0] - 2026-05-11

**R12: Semantic Trajectory Audit (Release 12 of the paired-release schedule).** Measures how the *meaning* of a prose draft moves across its length: paragraph-by-paragraph (or sentence-by-sentence, or fixed-token windows), embed each window with a sentence-transformers model and compute the trajectory of pairwise cosine similarities. The framework's prior cohesion signal (`tier3.adjacent_cosine` in `variance_audit.py`) measured the same shape at sentence-level for smoothing diagnosis; R12 extends the observation to the voice-coherence task surface with paragraph-level windowing as the default, richer trajectory statistics, and an optional baseline-comparison mode.

### Added

- **`scripts/semantic_trajectory_audit.py`** — the R12 main script. Computes adjacent-cosine series, drift (first-to-last cosine + linear-regression slope/R²), autocorrelation at lags 1/2/3/5, and flatness summary (counts above 0.85/0.9/0.95 plus longest consecutive run above 0.9). Three window strategies: `paragraph` (default; coalesces shorts, splits longs), `sentence` (matches the existing tier3 signal), `fixed-token` (uniform N-token windows). Optional `--baseline` mode reads a prior run's JSON and reports descriptive deltas side-by-side. Outputs JSON or markdown; markdown report follows the framework's "fill numerics, leave `{TODO: interpret}` for the LLM/human pass" pattern. Carries an explicit `task_surface=voice_coherence` field and a full `ClaimLicense` block (rendered in both JSON and markdown). Exit codes: 0 = success, 2 = source file not found, 3 = embedding backend error.
- **`scripts/embedding_backend.py`** — pluggable embedding-model wrapper. Resolves three aliases (`mxbai`, `gemma`, `minilm`) per the co-primary decision in the framework's embedding-model-choice spec. Lazy load (no model download on `--help`), honest failure (`EmbeddingBackendError` with install hint when sentence-transformers is missing — no silent TF-IDF fallback), deterministic-mode by default. Provides `identifier_block()` for PROVENANCE output and `resolve_model_arg()` for CLI flag normalisation.
- **`scripts/tests/test_semantic_trajectory_audit.py`** — 28 tests covering windowing strategies, cosine math, drift/autocorrelation/flatness stats, PROVISIONAL banding, baseline comparison, JSON shape, markdown rendering (including the claim-license section on both normal and warning paths), CLI exit codes, and embedding-backend error propagation.
- **`scripts/tests/test_embedding_backend.py`** — 20 tests covering alias resolution, lazy load, missing-package handling, model-load failure handling, kwarg pass-through to sentence-transformers, identifier block shape.

### Notes

- **PROVISIONAL banding only.** R12 ships with illustrative bands (`very_tight` / `tight` / `typical` / `drifting`) derived from author-baseline heuristics, NOT from labeled-corpus calibration. The claim-license block names this explicitly: `calibration_anchor: user-baseline-required`. Per the "Stylometry to the people" policy in `scripts/calibration/PROVENANCE.md`, R12 does not ship anchored thresholds; users wanting load-bearing decision points run the §6.4 fixture suite against their own baseline.
- **Co-primary embedding models.** The `--model` flag defaults to `mxbai` (the spec's CLI default for users who haven't run the §6.4 fixture suite). `mxbai`, `gemma`, and `minilm` resolve via the alias table; full HuggingFace identifiers pass through verbatim. Revision SHA pinning via `--revision` is supported for reproducibility; unpinned runs surface the missing pin in their identifier block.
- **task_surface = `voice_coherence`.** R12 is the eighth voice-coherence surface tool. The audit refuses authorship verdicts and cross-register generalization claims by design.
- **No fallback path.** Unlike `variance_audit.py`'s tier3 signal (which falls back to TF-IDF when sentence-transformers is missing), R12 fails honestly with `EmbeddingBackendError`. Trajectory math against TF-IDF cosines would not produce meaningful semantic-trajectory shape. Callers that want fallback behavior would have to opt in explicitly.
- **Sentence-transformers is optional**, already commented in `requirements.txt`. R12 inherits that opt-in install path.

## [1.42.6] - 2026-05-11

**Policy shift: "Stylometry to the people."** SETEC no longer ships per-signal decision thresholds derived from labeled corpora (EditLens, RAID, MAGE, or any other) as load-bearing defaults. Anchored thresholds derived from one corpus do not generalize to the user's register mix without local recalibration, and shipping them as defaults would constitute the implicit-generalization claim SETEC otherwise refuses to make. The framework ships methods + tooling + PROVENANCE discipline; users wanting corpus-anchored thresholds run `calibrate_thresholds.py` against their own baseline.

### Changed

- **`burstiness_B` in `COMPRESSION_HEURISTICS` reverted to provisional.** The 2026-05-10 EditLens-anchored value (`-0.6227...`, `provisional=False`, `provenance=editlens_val_burstiness_B_fpr0.01_2026-05-10`) is rolled back to the pre-calibration heuristic (`-0.40`, `provisional=True`, `provenance=None`). The original calibration is preserved in `scripts/calibration/thresholds_calibrated.json` and as a `[POLICY: AUDIT-ONLY]`-tagged PROVENANCE entry for reproducibility, but the framework no longer loads it as the runtime threshold.
- **PROVENANCE.md gains a policy banner** at the top explaining the shift, plus a tagged warning on the EditLens burstiness_B entry. The "To populate this ledger" workflow was rewritten to reflect the audit-only posture (PR #15 P2 follow-up): step 5-8 now distinguish "audit-only PR" (the framework path) from "user-local fork" (not a framework commit) rather than instructing maintainers to edit `COMPRESSION_HEURISTICS` directly.
- **PROVENANCE.md "Status" section** reframes "0 of 11 thresholds calibrated" from a transitional state to a load-bearing invariant under the current policy.

### Notes

- The variance-audit footer continues to report "0 of 11 signal thresholds carry calibration provenance" — same wording as before, but now backed by an explicit policy rather than an unstarted toolchain.
- The calibration toolchain (`calibrate_thresholds.py`, `calibration_survey.py`, fetchers, manifest converters) is unaffected. Users running it locally produce their own anchored thresholds, exactly as the new policy intends.
- Inline comment in `variance_audit.py` for `burstiness_B` rewritten to explain the policy and reference the EditLens audit record.
- The threshold-spec contract tests are generic over the registry (don't pin signal names) so the burstiness_B revert needed no test changes.

## [1.42.5] - 2026-05-11

**README: honest costs and resources at the calibration tier.** Adds a new top-level section between Installation and Quick start that registers the real disk / time / memory / GPU footprint for a calibration run. Figures are measured from the 2026-05 RAID + MAGE runs, not theoretical estimates. Smoothing-diagnosis, voice-coherence, and validation tiers are explicitly noted as unaffected.

### Added (docs)

- **Disk table**: RAID corpus ~16 GB, MAGE corpus ~528 MB, RAID manifest ~5.0 GB, MAGE manifest ~187 MB, optional SBERT ~2 GB, optional R12 embeddings ~2.4 GB. Total budget for full setup: 25–28 GB.
- **Time table**: MAGE single-threaded 11–18 hours for all Tier 1 signals (measured), RAID single-threaded 6–13 days (extrapolated, explicitly flagged as not recommended). Per-tier multipliers for Tier 2 and Tier 3.
- **Score-once-survey-many explanation**: clarifies that `calibration_survey.py` scores the corpus once and reuses cached records per signal, so an N-signal sweep does NOT cost N× the listed runtime — record collection is the expensive step, paid once, and the per-signal threshold sweep is seconds per signal.
- **Memory notes**: Tier 1 peaks ~250 MB resident per shard; Tier 2 adds ~1 GB (spaCy); SBERT adds ~1.5 GB. 8-shard concurrent design fits a 16 GB machine.
- **Optional GPU notes**: Tier 1 + Tier 2 CPU-bound; SBERT and R12 embedding work benefit but are not blocked. Mixed-hardware coordination (macOS + Windows + AMD + WSL2 ROCm) described inline.
- **"What this means for the user" footer**: plug in, MAGE first, sharded for RAID, calibration tier is opt-in.

### Changed (reviewer P2 follow-ups)

- **No `internal/` references in user-facing docs.** The new section originally cited `internal/SPEC_embedding_model_choice.md` and `internal/SPEC_sharded_calibration.md`, both of which are gitignored and unreachable from the published README. Replaced with descriptive prose: the mxbai-embed-large-v1 reference now names the model and license inline; the sharded-calibration references describe the design (atomic shard claim, SIGTERM checkpoint, cloud-synced state) in the text rather than pointing at an inaccessible spec path. The pre-existing line-130 reference to `internal/SPEC_calibration_toolchain.md (gitignored)` is unchanged — it was explicit about the spec's status and was not part of this PR's new content.
- **Per-signal cost language corrected.** Original wording said the framework "currently runs the survey one signal at a time" and "multiple signals are surveyed in series, not in parallel," which contradicted the actual code: `derive_thresholds_for_all_signals` scores rows once and iterates signals over the cache for an 11× speedup (per the function's own docstring). Rewrote the paragraph to distinguish record collection (expensive, paid once) from per-signal threshold sweeps (cheap, seconds each).

### Notes

- The user's previous request was to "honestly register the real costs and processes in the Readme." This section discharges that.
- No code changes; no tests added. Purely documentation.

## [1.42.4] - 2026-05-11

**Baselines-folder discovery for fresh SETEC instances.** Observed failure mode: a SETEC instance running inside a git worktree, or after `git clone` into a new directory, doesn't see the user's existing `ai-prose-baselines-private/` folder (which is typically synced via Obsidian / iCloud / Dropbox). Acquisition scripts then fall back to the sibling-of-repo path and silently create a duplicate empty folder, diverging from the user's real corpus. The framework already honored a `SETEC_BASELINES_DIR` env var, but the variable was never surfaced anywhere a user would find it.

### Added

- **`scripts/baseline_discovery.py`** — read-only CLI that searches the common sync roots (repo sibling, `~/Documents/**`, `~/Obsidian*`, `~/Dropbox*`, `~/Google Drive*`, `~/OneDrive*`, macOS iCloud Drive, `~/`) for any folder named `ai-prose-baselines-private`, summarises each (manifest entries, impostor personas, total size, last-modified), ranks them by manifest content, and prints the exact `export SETEC_BASELINES_DIR="..."` line the user should add to their shell rc. Supports `--json` for skill consumption and `--validate PATH` for checking a specific directory. Never creates folders or writes outside its own stdout.
- **`scripts/tests/test_baseline_discovery.py`** — 21 tests covering env-var precedence, env-var validation against the marker-name rule (P2 follow-up), filesystem scan, ranking by manifest entries then impostor count then size, JSON shape, validation rejection of mis-named directories, render-time warning surfacing, and CLI exit codes (0 = found, 1 = nothing found, 2 = validate failed).

### Changed

- **`setup` skill SKILL.md** — adds Step 0 ("Locate the user's existing baselines folder") that runs `baseline_discovery.py` before any tier check and tells the assistant to surface the recommendation (and any duplicate-folder warning) to the user before proceeding.
- **README.md Privacy notice** — documents `SETEC_BASELINES_DIR`, explains why the sibling-of-repo fallback breaks inside worktrees, and points at `baseline_discovery.py`.

### Notes

- **Discovery rule.** When `SETEC_BASELINES_DIR` is set and points to an existing directory **whose final component is named `ai-prose-baselines-private`**, that directory is recommended regardless of which other folders are larger or busier. The env var is an explicit user choice; the script will not override it.
- **Env-var validation (reviewer P2).** If the env var points at a real folder whose final component is NOT `ai-prose-baselines-private`, the discovery script refuses to recommend it (because the acquisition tools' `acquisition_core.is_private_safe_path` rule would refuse to write there) and surfaces a `WARNING:` block at the top of the text report. If a correctly named folder exists elsewhere, recommendation falls through to that one; otherwise no recommendation is emitted. This prevents `setup` from telling the user to persist a path acquisition tools will reject downstream.
- **Recommendation when env var is unset.** Among existing folders, rank by `(manifest_entries, impostor_personas, size_bytes, last_modified_iso)` highest first. The manifest is the canonical signal of "this is the corpus the user actually uses."
- **Bounded walk.** Filesystem scans cap at `--max-depth 4` from each root (default) and `--max-depth-scan 3` per-candidate. A 21 GiB folder will not hang the report.
- **Discovery is non-destructive.** Duplicate folders are listed but never removed; the user decides. The script does not create folders either — if nothing is found, it tells the user what will happen on first acquisition run and recommends setting the env var preemptively.

## [1.42.3] - 2026-05-10

**Schema-conformance fix on the manifest converters.** Running `manifest_validator.py` on the v1.42.2 converter output surfaced five field-value mismatches against the validator's `ALLOWED_*` vocabularies. v1.42.3 maps every field to the validator vocabulary, omits fields where no honest mapping exists, and adds a round-trip integration test.

### Fixed

- **`ai_status`** maps to `pre_ai_human` / `ai_generated` (was `human` / `ai`).
- **`privacy`** maps to `shareable` (was `public`). MIT and Apache-2.0 are permissive-with-attribution, not public-domain.
- **`editing_status`** maps to `raw_draft` (was `unedited`). The validator has no "adversarial" tier; adversarial-transform info lives in `notes.attack` for R7's robustness card.
- **`use`** is list-typed: `["validation"]` (was `"validation"`).
- **`register`** for RAID: domain → validator vocabulary via curated mapping (news→blog_essay, books→literary_fiction, abstracts→academic_philosophy, reddit/reviews/recipes→personal, wikipedia→blog_essay, poetry→literary_fiction). Code/Czech/German return None and the converter OMITS the field rather than asserting a bogus value. Raw domain preserved in `notes.domain`.
- **`register`** for MAGE: OMITTED entirely. MAGE spans 10 source datasets with per-row variation; no single validator-allowed value is honest. Source preserved in `notes.original_source`.

### Added

- **Manifest-validator round-trip test** in both converter test files. Catches any future schema drift at PR time.
- Helpers: `_register_for_row` (RAID), `_attack_token_for_row` (RAID), `_raw_domain_for_row` (RAID).

### Notes

- **1377 tests pass + 1 skipped** (was 1364+1; +13 new tests covering register mapping, attack-token preservation, validator round-trip, and updated mappings in existing tests).
- **Smoke-tested on real fetched corpora**: MAGE 200-row sample → "Manifest is clean"; RAID 300-row sample → "Manifest is clean".
- **The v1.42.0 → v1.42.1 → v1.42.2 → v1.42.3 audit trail** records different reality-checks catching different bugs: HF API (v1.42.1), on-disk shape (v1.42.2), validator round-trip (v1.42.3). The new round-trip test encodes the third check as a gate.
- **In-progress full-conversion runs killed** before this patch because they'd have produced 8M validator-noncompliant entries. Re-running the full conversion is a follow-up operational step after this PR merges.

## [1.42.2] - 2026-05-10

**Make-the-converters-actually-work fixes.** Smoke-testing `raid_to_manifest.py` and `mage_to_manifest.py` against the real fetched corpora (16 GB RAID, 528 MB MAGE) surfaced four bugs the v1.42.0 mocked-pyarrow tests didn't catch. All four ride together because they're the same shape: the converters were written against the HF dataset-card schemas, but the on-disk reality differs.

### Fixed

- **CSV input was not supported.** Pre-1.42.2 both converters did `source_dir.rglob("*.parquet")` and `pyarrow.parquet.ParquetFile`. HuggingFace ships RAID and MAGE as CSV at the repo root (parquet only exists in the HF data viewer as an auto-conversion). Fix: `_read_rows` now dispatches on file extension — `.csv` uses stdlib `csv.DictReader` with `field_size_limit` raised for multi-KB generations; `.parquet` still uses pyarrow. The file-walk picks up both extensions. The renaming `parquet_files` → `source_files` propagates through both converters.
- **MAGE CSVs ship with a UTF-8 BOM.** Pre-1.42.2 the CSV reader opened with `encoding="utf-8"`, so the first column name in `DictReader.fieldnames` came out as `﻿text` instead of `text` — and every `row.get("text")` returned None, dropping all 436,606 MAGE rows as "empty." Fix: open with `encoding="utf-8-sig"` (BOM-tolerant for files with BOM, identical to `utf-8` for files without — RAID's CSVs have no BOM and are unaffected).
- **MAGE's source column is `src`, not `source`.** The HF dataset card calls the column `source`, but the actual CSV header is `src`. Pre-1.42.2 `row.get("source")` returned None on every row. Fix: `row.get("src") or row.get("source")` — accepts either, future-proofs against the HF schema declaration changing back.
- **RAID's Code domain was tagged `language_status: native`.** Code isn't a natural language; SETEC's stylometric tools have no business adjudicating it against an English baseline. Pre-1.42.2 the Code rows fell through to the `native` default. Fix: map `code` to `language_status: unknown`. Users who want only English prose should also pass `--no-nonprose` at conversion time.

### Notes

- **1364 tests pass + 1 skipped** (was 1355+1 in v1.42.1; +9 new tests across `TestConvertEndToEndCSV` (2+2 = end-to-end CSV path + adversarial filter on CSV), `TestSplitForSourceFile` (4 = OOD-slice recognition + backwards-compat alias), and `_language_status_for_row` Code-mapping coverage). The new tests use real on-disk CSVs (no pyarrow mock) so the CSV path is covered by the actual stdlib `csv` module, not a synthetic stand-in. Existing tests that hit the pyarrow path still mock pyarrow with the autouse cleanup fixture from v1.42.0.
- **Smoke-test verification** against real fetched corpora: `raid_to_manifest.py --limit 100` produces 100 valid manifest entries; `mage_to_manifest.py --limit 50` produces 50 valid manifest entries; both round-trip JSON without errors.
- **Backwards-compat alias preserved**: `_split_for_parquet` still imports as an alias for `_split_for_source_file`, so any external caller that grabbed the previous private name continues to work.
- **CHANGELOG / PROVENANCE / docs unchanged for upstream behavior**: the v1.42.0 PROVENANCE.md "Available calibration corpora" section already describes the format-agnostic conversion step. The docstrings in both converters were updated to reflect the actual CSV-on-disk reality of MAGE and RAID.

## [1.42.1] - 2026-05-10

**License-pattern fix on the v1.42.0 fetchers.** Pre-merge of v1.42.0 I read the RAID and MAGE license declarations from the paper / GitHub README rather than from the HF dataset cards. The actual HF cards (verified live against revisions `865cac7...` and `342663f...` on 2026-05-10):

- **RAID** HF card declares `mit`, not `apache-2.0` (the paper / GitHub README cite Apache-2.0).
- **MAGE** HF card declares `apache-2.0`, not `mit` (the paper / GitHub README cite MIT).

Both licenses are permissive and functionally equivalent for the framework's GPL-3-with-attribution posture, but the fetchers' license verification was too narrow and refused the live cards.

### Fixed

- **`fetch_raid.py`** `EXPECTED_LICENSE_PATTERNS` now accepts MIT alongside Apache-2.0. NOTICE.md preamble updated to "Permissive (paper cites Apache-2.0; HF dataset card observed at fetch time: `<observed>`)" so the audit trail records what the framework actually saw rather than asserting a single license.
- **`fetch_mage.py`** symmetric fix: accepts Apache-2.0 alongside MIT. Same NOTICE.md rewording.

### Notes

- **1355 tests pass + 1 skipped** (was 1353+1 in v1.42.0; +2 new regression tests covering the MIT-for-RAID and Apache-for-MAGE paths). Existing NOTICE-text assertions updated to match the new permissive-license wording.
- **No behavior change for users on valid input.** This is a fix to make the fetchers actually work against the live HF cards. Users who would have hit the rejection are now greenlit. Users whose runs would have succeeded see no change.
- **PROVENANCE.md not modified.** The "Available calibration corpora" section already says "RAID Apache-2.0" and "MAGE MIT" per the paper citations. Both are still accurate descriptions of the corpora's intended licensing; the discrepancy is between the paper and the HF card, not between SETEC and reality. The fetcher's NOTICE.md is the authoritative per-fetch record.

## [1.42.0] - 2026-05-10

**Calibration corpus track: RAID + MAGE fetchers + manifest converters.** The framework's calibration toolchain has shipped since 1.10.0 with EditLens as the only labeled corpus. Every threshold in `COMPRESSION_HEURISTICS` still carries `provenance: "provisional"` because EditLens's CC BY-NC-SA 4.0 posture keeps derived thresholds in the local-only quadrant. This release ships fetchers for two permissively-licensed labeled corpora — RAID (Apache-2.0, 8M rows, 16.7 GB) and MAGE (MIT, 437K rows, 554 MB) — and the companion parquet-to-manifest converters. With these, the calibration toolchain can graduate threshold values out of `provisional` against substantially larger labeled corpora than EditLens alone, and RAID's adversarial-transform variants give R7's robustness card real fixtures to evaluate against.

### Added — RAID fetcher (`scripts/calibration/fetch_raid.py`)

- Pulls the full RAID benchmark (Dugan et al., NAACL 2024) from HuggingFace into `ai-prose-baselines-private/raid/`. Mirrors the shape of `fetch_pangram_editlens.py` with the legal posture adjusted for Apache-2.0 (no CC-NC redistribution guard on derived thresholds; corpus stays under private dir by convention; calibrated values can ship in GPL-3 SETEC defaults with NOTICE attribution).
- **CLI:** `--subset {train, test, extra, all}` (default `all` — labeled English + Czech/German/Code), `--no-adversarial` (cuts the fetch from ~17 GB to ~1.4 GB by skipping the 12 attack variants), `--dry-run` (lists the fetch set without committing to a multi-GB pull), `--skip-license-check`, `--refresh`, `--token` (HF token; RAID is public but supports authenticated-proxy edge cases).
- License verification: refuses to proceed unless the HF card declares Apache-2.0. NOTICE.md cites the paper, the HF revision SHA, and the license; `.fetch_record.json` records the revision + fetch parameters for downstream consumers.

### Added — MAGE fetcher (`scripts/calibration/fetch_mage.py`)

- Pulls MAGE (Li et al., ACL 2024) from HuggingFace into `ai-prose-baselines-private/mage/`. Same shape as fetch_raid; legal posture adjusted for MIT.
- **CLI:** `--split {train, validation, test, all}` (default `all`; `validation` matches both `val` and `validation` filename tokens), `--dry-run`, `--skip-license-check`, `--refresh`, `--token`.
- License verification: refuses to proceed unless the HF card declares MIT. NOTICE + `.fetch_record.json` as for RAID.

### Added — Manifest converters

- **`scripts/calibration/raid_to_manifest.py`** — converts the local RAID parquet files into a SETEC manifest slice. Maps RAID's `model` field to `ai_status` (`"human"` for the human-baseline rows, `"ai"` for the 11 LLMs), `attack` to `editing_status` (`unedited` or `adversarial:<attack>`), `domain` to `register`, and Czech/German domains to `language_status: non_native_advanced`. Per-row text is spilled to a 4-level hash-bucketed dir (`text/ab/cd/<id>.txt`) so 8M files don't pile into one directory. CLI flags `--limit`, `--no-adversarial`, `--no-nonprose`, `--allow-public-output`. Default writes refuse to land outside `ai-prose-baselines-private/`; the override flag is documented and tested.
- **`scripts/calibration/mage_to_manifest.py`** — converts MAGE parquet to manifest. Simpler shape: `label` 0 → `human`, 1 → `ai`; `source` field preserved as `source_id`; `register: mixed` (MAGE spans 10 source datasets and per-row register would require a mapping that's not worth the maintenance burden); `editing_status: unedited` (MAGE doesn't expose edit provenance). Same bucketed text-spill + privacy-guard convention as the RAID converter.

### Added — PROVENANCE.md "Available calibration corpora" section

- Documents the three labeled corpora the toolchain can consume: EditLens (CC-NC, local-only, ~14K rows), RAID (Apache-2.0, ~8M rows, 16.7 GB), MAGE (MIT, ~437K rows, 554 MB).
- Names the license posture for each and the calibration workflow that would consume them (RAID first for highest leverage, MAGE as cross-check, EditLens for ESL-specific slices). The PROVENANCE entry for the first cross-corpus calibration run lands in a follow-up.

### Notes

- **1353 tests pass + 1 skipped** (was 1291+1 in 1.41.1; +62 new tests across `test_fetch_raid.py` (15), `test_fetch_mage.py` (9), `test_raid_to_manifest.py` (22), `test_mage_to_manifest.py` (16)). Tests mock `huggingface_hub` and `pyarrow.parquet` via sys.modules injection with autouse cleanup fixtures so the mocks don't leak into downstream tests (sklearn reads `pyarrow.__version__` and would break if a lingering mock lacked the attribute).
- **No CLI breaking changes.** Four new scripts, no existing surface modified.
- **What this release does NOT do:** it does not execute the full RAID+MAGE fetch (~17 GB), and it does not produce a new calibrated threshold provenance entry. Those are operational follow-ups: after this PR merges, the maintainer (or any user with disk space) runs `python3 scripts/calibration/fetch_raid.py` + `fetch_mage.py` + the manifest converters + `calibration_survey.py` against the resulting manifests, and lands the first cross-corpus calibration entry as a follow-up PR with the four-artifact diff documented in PROVENANCE.md ("Calibration commit shape").
- **The split between this release and the calibration runs is deliberate.** Shipping the fetchers as their own release means (a) reviewers see the fetcher infrastructure separately from any threshold value change, (b) the fetchers can be reviewed without a 17 GB download, and (c) the calibration commit gets to focus on the actual `value` + `provenance` edit per threshold, not on the corpus-acquisition mechanics.

## [1.41.1] - 2026-05-10

**Reviewer-flagged P2 fixes across R10 + R9 surfaces.** Four issues spanning three scripts. Reviewer reviewed the v1.40.1 patch range; all four are silent-failure or no-op shapes — the same family as the seven 1.40.1 fixed.

### Fixed

- **`mimicry_cosplay_audit.target_density_per_1k` counted unique phrases, not occurrences.** Pre-1.41.1 `_phrase_hits` returned `len(matched)` (each preservation-list phrase counted at most once), so a signature phrase repeated 20 times in a 2,000-word target reported density `0.5/1k` (not `10/1k`) and the density-anomaly cosplay shape never fired — exactly the over-preservation case the audit was supposed to catch. Reviewer reproduced this end-to-end. Fix: `_phrase_hits` now returns `(n_unique, n_occurrences, matched, missing)`; `compute_idiolect_survival` exposes both `target_density_per_1k` (occurrence-based — read by the density-anomaly verdict) and `unique_phrase_density_per_1k` (coverage-based — kept alongside the existing `survival_rate` for coverage diagnostics). The verdict logic is unchanged; it now reads the correct value.
- **`known_editor_profile --tier2` was a silent no-op.** Pre-1.41.1 the CLI accepted `--tier2` and propagated `do_tier2=True` into `audit_text()`, so tier-2 spaCy signals were COMPUTED, but `_PROFILE_SIGNALS` contained only tier-1 paths so the extraction step never read tier-2 values into the profile. The flag had no observable effect on the learned profile or match report. Fix: split `_PROFILE_SIGNALS` into `_PROFILE_SIGNALS_TIER1` and `_PROFILE_SIGNALS_TIER2` (mdd.mean / mdd.sd / pos_bigrams.entropy_bits — paths variance_audit actually emits); new `_profile_signals(do_tier2=...)` helper assembles the active registry; `_extract_all_signals`, `measure_pair`, and `learn_profile` all take `do_tier2` and use the helper. `_PROFILE_SIGNALS` is preserved as a tier-1-only alias for backwards-compatible imports.
- **`calibration_drift_monitor --include-filenames` collided on duplicate basenames.** Pre-1.41.1 the bench_id was just `path.name` when `--include-filenames` was set, so two benchmarks like `a/same.txt` and `b/same.txt` overwrote each other in the snapshot dict. Reviewer reproduced this with `n_benchmarks: 1` after the second file overwrote the first. Fix: the filename-id branch now uses `path.relative_to(benchmark_dir)` (with `\\` → `/` normalization), and a defensive collision-suffix (`#NNN`) handles the symlink / case-insensitive-FS edge cases. The anonymized branch (default) was already collision-safe via the padded counter. The reviewer's reproduction now produces `a/same.txt` and `b/same.txt` as distinct keys, with `n_benchmarks: 2`.
- **`calibration_drift_monitor.render_report` hid schema-change drift.** Pre-1.41.1 the JSON correctly counted added/removed signals as schema drift (per the 1.40.1 fix), but the Markdown report only surfaced `n_signals_drifted`. A removed signal showed `infrastructure_drift_detected: true` and `n_signals_schema_changed: 1` in the JSON, while the Markdown said `Signals drifted / stable: 0 / 0` and listed no signal name. Reviewer reproduced both cases. Fix: the Markdown summary line now reads `Signals drifted / schema-changed / stable: D / S / T`; the per-benchmark drifted section now lists added signals and removed signals under explicit headers naming each schema-changed signal. The render now matches what the JSON has been reporting since 1.40.1.

### Notes

- **1291 tests pass + 1 skipped** (was 1279+1 in 1.41.0; +12 new regression tests across `TestPhraseDensityAnomalyRegression` (2) + a new `TestPhraseHits.test_repeated_phrase_counts_occurrences_not_unique` in `test_mimicry_cosplay_audit.py`; tier-1-only / tier-2-opt-in / `_profile_signals` helper coverage (3) in `test_known_editor_profile.py`; `TestIncludeFilenamesNoCollision` (2) + `TestRenderSchemaChangeDrift` (3) in `test_calibration_drift_monitor.py`).
- **No CLI breaking changes** for end users on valid input. The internal `_phrase_hits` signature changed from a 3-tuple to a 4-tuple; the three existing tests in `TestPhraseHits` were updated to match. Downstream callers that import `_phrase_hits` directly need to adapt; downstream callers using the public `compute_idiolect_survival` / `audit_cosplay` see additive changes only (a new `n_total_occurrences` and `unique_phrase_density_per_1k` field).
- **The four fixes share the same shape as 1.40.1's seven**: a code path produced clean output on inputs that should have surfaced something. The cosplay density fix gets exactly the failure mode the audit exists to catch (over-preservation by repetition). The known-editor `--tier2` fix removes a feature flag that promised what the code never delivered. The calibration-drift filename-collision fix prevents `--include-filenames` from silently shrinking the benchmark set. The Markdown render fix brings the report into agreement with the JSON it summarizes. Each pre-1.41.1 case looked like a working report but quietly omitted load-bearing information.

## [1.41.0] - 2026-05-10

**Paired-release schedule, Release 11: phrase-level signature mining + version-aware trajectory.** Both pieces extend the framework's voice-coherence story across larger units. The phraseological signature audit reaches above the token level to mine *frames* — the reusable language patterns the writer builds with — pairing naturally with `idiolect_detector`'s token-level keyness work. The draft-history analysis reaches across drafts to trace per-signal trajectories and name where (in the revision arc) signals moved. Both are Tier 3 builds; both compose with surfaces R10 just shipped.

### Added — Phraseological Signature Audit (Surfaces Tier 3)

- **`scripts/phraseological_signature_audit.py`** — phrase-frame mining over the writer's reusable language frames. The framework's `idiolect_detector` answers the keyness question: *which words and phrases are over-represented?* This module asks the complementary phraseology question: *what reusable language frames does this writer build with?* Same surface (phrase-level material), different unit (frames vs. tokens). Two writers can share zero surface phrases and still differ at the frame level; the difference is voice-bearing and the audit makes it visible.
- **Five categories tracked** (v1):
  - **`lexical_bundles`** — recurrent 3- and 4-grams in the baseline (default `min_count=2`). The writer's stable phrase-level building blocks. Survival rate of baseline bundles in the target reports voice preservation at the bundle level.
  - **`slot_frames`** — phrase frames with one or more variable positions. 12 curated structural templates with fixed function-word anchors and variable content slots: `not X but Y`, `the X of the Y`, `as X as Y`, `X if X is Y`, `what X is Y`, `neither X nor Y`, `either X or Y`, `between X and Y`, `from X to Y`, `more X than Y`, `less X than Y`, `the more X the more Y`. Per-frame counts in target and baseline.
  - **`idioms`** — curated 44-entry list of voice-bearing English idioms (`all things considered`, `at the end of the day`, `for what it's worth`, `on the one hand` / `on the other hand`, etc.). The writer's idiomatic register; baseline-only idioms surface what was lost in revision.
  - **`hapax_phrase_survival`** — 3-grams that appear exactly once in the baseline AND at least once in the target. Hapax legomena at the phrase level — contingent by definition; their survival tracks idiosyncratic phrase memory more sharply than high-frequency bundles do.
  - **`stance_intensifier_frames`** — 7 curated stance / hedging / intensifier frames (`really very`, `perhaps it is that`, `I can only X`, `the question is whether`, `it seems to me`, `what is X is Y`, `to X a Y`). Voice-bearing functional reuse beyond word-level keyness.
- **Hardened conventions** (1.34.2 + 1.40.1 onwards): missing user-supplied paths fail loudly with rc=2; `--baseline-dir` filters the target out (self-overlap guard) and rc=2 if the post-filter baseline is empty; argparse `choices=list(CATEGORY_KEYS)` rejects unknown `--category` filter names at parse time; Markdown blockquotes stripped by default with `--keep-quotes` opt-out.
- **Structured ClaimLicense block** explicitly refuses provenance verdicts. Frame reuse is voice-coherence evidence, not authorship certification. The slot-frame patterns are heuristic and curated; the idiom list is non-exhaustive (44 entries in v1); the stance-frame inventory is a small voice-bearing-functional-reuse cross-section. The license documents each.

### Added — Draft-History Analysis (Trustworthiness Tier 3)

- **`scripts/draft_history_analysis.py`** — version-aware stylometric trajectory across N drafts. Single-snapshot audits answer "what does this draft look like?"; `before_after_restoration` answers "what changed in this revision pass?"; draft-history asks the version-aware question: *given a sequence of drafts (v1, v2, … vN), where in the revision arc did the smoothing enter, when did idiolect disappear, was the change gradual or sudden, did later edits restore or further flatten the voice?*
- **Output shape mirrors the canonical use case the ROADMAP names**: "Major distributional compression appears between v3 and v4, concentrated in sections 1, 4, and 6. Later edits restore lexical idiolect but not sentence-architecture variance."
- **Eight tier-1 signals tracked** across versions, aligned with `known_editor_profile._PROFILE_SIGNALS` so the two surfaces compose: a draft history can supply pair-deltas to the editor-profile match step, and vice versa. Per-version values + per-pair deltas + inflection point (the version-pair index where the largest absolute delta occurred for each signal).
- **Per-signal verdict ladder**:
  - `stable_throughout` — every delta within the per-signal noise floor.
  - `gradual_drift` — deltas of consistent direction with cumulative movement above the floor; no single delta dominates (< 2× the mean absolute delta).
  - `sudden_shift` — one delta dominates (≥ 2× the mean absolute delta) and is outside the floor; the trajectory is shaped by a single revision pass.
  - `restored_after_drift` — net cumulative change within the floor BUT individual deltas exceed the floor with sign reversal — the writer drifted forward and then back. Voice-restoration evidence.
  - `unknown` — fewer than 2 usable deltas.
- **Summary aggregates across signals**: per-verdict counts, plus the **dominant inflection pair** (the version-pair that shows up most often as the inflection point — the canonical "between v3 and v4" output).
- **Hardened input handling**: missing `--versions-json` / malformed JSON / non-list shape / entries missing `label`/`path` keys / fewer than 2 entries / missing version files / empty version files all fail loudly with rc=2.
- **Structured ClaimLicense block** explicitly refuses authorship verdicts at any version. The trajectory says WHEN signals moved, not WHAT caused the movement. A `sudden_shift` between v3 and v4 might reflect a global rewrite, an editor pass, an AI-smoothing pass, an authorial pivot, or a structural reorganization — the report names the inflection point and refuses to choose.

### Notes

- **1279 tests pass + 1 skipped** (was 1215+1 in 1.40.1; +64 new tests across `test_phraseological_signature_audit.py` (39) and `test_draft_history_analysis.py` (25)).
- **No breaking changes.** Both pieces are new scripts; no existing surface modified. The phraseological audit composes with `idiolect_detector` (same input shape: target + baseline-dir; complementary output: tokens vs. frames). The draft-history analysis composes with `known_editor_profile` (same signal set).
- **Schedule status: Release 11 shipped.** Per the paired-release schedule, the next release is Release 12 — Semantic Trajectory Audit (Surfaces T3). The framework's first surface that crosses the "measuring meaning" line; needs SBERT or equivalent (gigabytes of weights). Worth a deliberate decision before adopting that dependency posture.
- **The phraseological audit and draft-history analysis are interpretive surfaces in opposite dimensions.** The phraseological audit operates on a single text and reports phrase-level voice patterns — depth across one snapshot. The draft-history analysis operates on N versions and reports per-signal trajectories — depth across time. Together with the surfaces R10 shipped (mimicry-cosplay across one text; known-editor across one before/after pair), the framework now ships interpretation depth in four dimensions: cross-surface (R7), within-surface syntactic (R8), across-revision semantic (R8), and across-version trajectory (R11).
- **R11 closes the framework's "smoothing arc" surface set.** Pre-1.41.0 the framework could detect smoothing in a draft, identify the kind of smoothing (R8 semantic preservation, R10 known-editor profile), and resolve cross-surface disagreement (R7). What was missing was the version-aware piece — a writer with multiple drafts could not ask the framework "when did this enter?" Post-1.41.0 they can. Pair-wise, the draft-history's per-signal trajectory + the phraseological audit's per-frame survival give a writer a complete answer: which frames survived which version, and where in the arc each signal moved.

## [1.40.1] - 2026-05-10

**Reviewer-flagged P2 fixes in the new R8 + R9 surfaces.** Seven issues across four scripts. Reviewer reviewed `v1.37.2..v1.38.0` (R8) and `v1.38.0..v1.39.0` (R9) separately; this patch applies to current `main` (post-R10 at v1.40.0) since none of the R8/R9 surfaces changed during R10. The fixes are all the same shape: silent failures masquerading as clean output (count drift hidden, baseline self-overlap, unknown filter names returning empty success, schema-change drift not counted, empty benchmark snapshots, alternate manifest formats parsed as empty).

### Fixed

- **`semantic_preservation_check.claim_inventory` returned `preserved` on real count drift.** Pre-1.40.1 the category passed empty `items_dropped` and `items_added` lists into `_classify_verdict`, whose large-count branches require non-empty diffs to fire — so a 5 → 10 declarative-count change collapsed to `preserved` at both the category and overall level. Reviewer reproduced the failure end-to-end. Fix: new `_classify_count_only_verdict` helper that uses count-delta logic (with the same small-count floor and ratio thresholds as `_classify_verdict`); `claim_inventory` now uses it. End-to-end regression test reproduces 5 → 10 declaratives correctly returning `shifted_added`.
- **`semantic_preservation_check.check_preservation` silently filtered everything out on a `--category` typo.** Pre-1.40.1 a typo like `--category typo` produced `categories: {}` and `overall_verdict: preserved` via the empty-`all(...)` truthiness path, exit 0 — making a misspelled filter look like a clean audit with no findings. Fix: `check_preservation` now raises `ValueError` listing the unknown name(s) and the valid set; CLI catches the error and returns rc=2. As defense-in-depth, `_overall_verdict({})` now returns `unknown` rather than `preserved`.
- **`construction_signature_audit` could include the target in its own baseline.** Pre-1.40.1 `aggregate_baseline_densities` walked the entire baseline directory without filtering the audited target out, so when the target lived under `--baseline-dir` it counted as one of its own baseline files. Reviewer reproduced a one-file baseline-dir where target density exactly equaled baseline density (delta = 0) and the run exited cleanly. Fix: `aggregate_baseline_densities` accepts a `target_path` keyword and drops baseline entries whose resolved path equals the target's. Empty post-filter baseline → rc=2 with a clear stderr message naming the cause. Same self-overlap-guard convention `paragraph_audit` (1.34.1), `general_imposters` (1.29.1), and `controls_audit` (1.37.1) use.
- **`construction_signature_audit` silently produced empty success on a `--construction` typo.** Pre-1.40.1 a typo in `--construction` filtered every construction out and produced `constructions: {}` with rc=0 — the same shape problem as the category-filter bug. Fix: new public `CONSTRUCTION_KEYS` constant exposing the registry's keys; argparse `choices=list(CONSTRUCTION_KEYS)` rejects unknown construction names at parse time (rc=2). Same convention `fairness_dialect_guardrails` (1.39.0) uses for its `--declare` argument.
- **`calibration_drift_monitor.detect_drift` did not count `added` / `removed` signals as drift.** Pre-1.40.1 `_compare_signals` correctly emitted `added` and `removed` verdicts when a signal appeared or disappeared between snapshot and current, but `detect_drift` only counted `drifted`, so a signal-schema change could result in `infrastructure_drift_detected: false`. Reviewer reproduced both cases with synthetic snapshots. Fix: `detect_drift` now counts `n_signals_added`, `n_signals_removed`, and combines them as `n_signals_schema_changed`. The schema-change count contributes to `bench_drifted`, `infrastructure_drift_detected`, and the recalibration recommendation. Per-benchmark blocks now carry the new counters; the top-level report adds `n_signals_schema_changed`.
- **`calibration_drift_monitor.take_snapshot` produced clean snapshots for empty benchmark directories.** Pre-1.40.1 a benchmark directory containing only empty / whitespace files skipped every file silently and returned a snapshot with `n_benchmarks: 0`; the CLI exited 0 and wrote it. CI drift checks against such a snapshot would silently pass without measuring anything. Fix: `take_snapshot` now raises `FileNotFoundError` when no non-empty benchmark is measured; the CLI returns rc=2 and does NOT write the empty snapshot to disk. Skipped-empty filenames are recorded in the returned snapshot as `skipped_empty` (anonymized count by default, full list with `--include-filenames`).
- **`fairness_dialect_guardrails._read_manifest_language_backgrounds` ignored `.jsonl` and list-valued `use`.** Pre-1.40.1 the reader checked `manifest_path.suffix.lower() == ".json"` and fell through to the TSV parser for `.jsonl`, and used `entry.get("use") != "baseline"` which silently skipped entries whose `use` was a list like `["baseline", "target"]`. Reviewer reproduced both: a `.jsonl` manifest with `non_native_advanced` baseline coverage and a `.json` manifest with list-valued `use` both produced empty `baseline_backgrounds`, leading the guardrail to falsely refuse evaluative use despite legitimate baseline coverage. Fix: new `_entry_uses_baseline` helper accepts scalar or list/set/tuple `use` values; the reader now branches on suffix to handle `.json` (object or list-of-objects), `.jsonl` (line-by-line), and TSV with comma-separated `use` cells.

### Notes

- **1215 tests pass + 1 skipped** (was 1187+1 in 1.40.0; +28 new regression tests across `TestClassifyCountOnlyVerdict` (5), `TestClaimInventoryRegression` (1), `TestUnknownCategoryFilter` (3), CLI `--category` rejection (1) in `test_semantic_preservation_check.py`; baseline self-overlap (2), unknown-construction CLI rejection (1), `CONSTRUCTION_KEYS` export (1) in `test_construction_signature_audit.py`; `TestSchemaChangeDrift` (4), `TestEmptyBenchmarkDirRejected` (3) in `test_calibration_drift_monitor.py`; JSONL/list-use manifest reading (7) in `test_fairness_dialect_guardrails.py`).
- **No CLI breaking changes** for end users on valid input. Three CLIs now reject more cases: `semantic_preservation_check --category` rejects unknown category names (was: silent empty audit); `construction_signature_audit --construction` rejects unknown construction names (was: silent empty audit); `calibration_drift_monitor snapshot` rejects all-empty benchmark dirs (was: silent zero-benchmark snapshot). All three rejections are loudness fixes on cases that were either typos or misuse.
- **The seven fixes are all the same shape**: a code path produced `preserved` / `success` / `no_drift_detected` on inputs that should have raised loud errors or substantive verdicts. The framework's "fail loudly on user-supplied data problems" convention (1.34.2 onwards) was already in place for missing files and malformed JSON; this patch extends it to (a) intentionally-empty diff lists in count-only categories, (b) self-overlapping target/baseline corpora, (c) typo'd filter names, (d) signal-schema changes between snapshots, (e) empty-benchmark snapshots, and (f) alternate manifest formats. After this patch, every CLI surface in the framework either produces meaningful output or fails loudly; none silently lie.
- The schema-change drift fix is the most epistemically load-bearing of the seven: a signal that DISAPPEARED between snapshots is exactly the case the drift monitor exists to catch (a parser update that stopped emitting a signal, or a model version that no longer recognizes the input). Pre-1.40.1 that case returned `infrastructure_drift_detected: false`, which would let CI gates pass on the most consequential drift category. Post-1.40.1 it counts toward both the top-level drift flag and the recalibration recommendation.
- The fairness-guardrail manifest-reading fix preserves the framework's load-bearing fairness guarantee: refuse evaluative use when the validation baseline does NOT include comparable language backgrounds. Pre-1.40.1 a JSONL manifest WITH non-native baseline coverage produced empty counts → the guardrail falsely caps at `revision_only` and refuses evaluative use even though the user did set up the right baselines. The bug was strictly in the wrong direction — the guardrail was over-conservative on real input rather than over-permissive — but it still made the manifest-coverage feature unusable for `.jsonl` and list-use shapes. Both shapes are common in the framework's downstream tooling, and the canonical impostor manifests use JSONL.

## [1.40.0] - 2026-05-10

**Paired-release schedule, Release 10: smoothed-but-by-whom.** Both pieces address the question "smoothed by whom?" from different angles. The mimicry / style-cosplay audit detects over-conspicuous imitation by **cross-checking** lexical-phrase survival against syntactic Delta — exposing the dissociation pattern that aggregate scoring would hide. The known-editor profile **learns** an editorial transformation profile from labeled before/after pairs and matches new pairs against it, distinguishing "this was smoothed" from "this was smoothed in the ordinary way this editor smooths this writer." Both are Tier 3 builds; both pair as natural complements.

### Added — Mimicry / Style-Cosplay Audit (Surfaces Tier 3)

- **`scripts/mimicry_cosplay_audit.py`** — detect lexical mimicry without syntactic conformity. The framework already shipped `before_after_restoration.py` with a metric-gaming heuristic catching the case where a single target signal improved while a related aggregate moved against it. What it does NOT catch is the failure mode where idiolect phrases survive *too conspicuously* while function-word grammar fails to match the lexical mimicry. That's the **style-cosplay** signature — a draft scoring well on per-feature metrics because the writer's signature phrases are present, but reading as imitation because the underlying syntactic profile doesn't match the lexical surface.
- **Two cosplay shapes detected**, NOT aggregated. The audit's value comes from *not* averaging the lexical and syntactic axes — aggregate scores would hide the dissociation that is the cosplay signature.
  - **`lexical_without_syntactic`**: idiolect-phrase survival is HIGH (≥ default 0.6) AND function-word weighted_delta is HIGH (≥ default 1.25, voice_distance_band's "Light drift" cutoff) OR POS-bigram KL is compressed.
  - **`density_anomaly`**: signature-phrase density per 1k words is at or above ~2× a baseline-typical expected density (default baseline 5.0/1k). Catches the over-preservation case where the writer's phrases appear at unnatural density, often a sign of voice-restoration over-targeting.
- **Verdict ladder**: `cosplay_suspected` (both shapes fire) / `mixed` (one shape fires) / `not_cosplay` (signals present but neither shape fires) / `unknown` (insufficient evidence). The framework refuses authorship verdicts at every level; a `cosplay_suspected` reading is evidence the target is NOT a normal authorial sample, not evidence of any specific provenance.
- **Composes with the rest of the framework**: a draft can pass `before_after_restoration` AND fail cosplay; the two audits do not aggregate. The composition with `surface_disagreement_resolver` (cross-surface meta-interpretation) and `semantic_preservation_check` (semantic guardrails) covers four distinct failure modes.
- **Hardened input handling** (1.34.2 conventions); structured ClaimLicense block. POS-bigram KL is read at the correct nesting (`compression.pos_bigram_kl`) per the 1.37.2 fix, with legacy top-level fallback.

### Added — Known-Editor Profile (Trustworthiness Tier 3)

- **`scripts/known_editor_profile.py`** — learn an editorial-transformation profile from labeled before/after pairs and match new pairs against it. The framework's reports could say "this draft has been smoothed" but couldn't say "this draft has been smoothed in the ordinary way THIS editor smooths THIS writer." For literary and institutional writing, that distinction is large — and treating an editor's normal pass as evidence of provenance is exactly the failure mode this module exists to prevent.
- **Two CLI subcommands.** `learn --pairs-json pairs.json --out profile.json` reads a list of `{before, after}` text-path pairs, runs `variance_audit` on each member, and emits a per-signal delta-distribution profile (mean, stdev, min, max). `match --before NEW.txt --after NEW.txt --profile profile.json` reads a saved profile and a new pair, computes per-signal z-scores, and emits a match report.
- **Eight tier-1 signals tracked** (default — `--tier2` opt-in for spaCy POS signals): burstiness_B, sentence_length_sd, mtld, mattr, shannon_entropy, yules_k, fkgl_sd, connective_density. A signal is included in the profile only if at least 2 pairs contributed (otherwise stdev is undefined; single-pair profiles still emit the mean for inspection but cannot reject anything).
- **Match verdict ladder**: `matches_profile` (all signals within ±2 sd of editor's typical delta) / `mismatch` (any signal more than 2 sd outside) / `ambiguous` (profile too narrow to test — single-pair profiles, zero-stdev signals, or no usable signals). Z-threshold is configurable via `--z-threshold`.
- **Privacy-by-default**: pair IDs are anonymized (`pair_001`, `pair_002`) unless `--include-filenames` is passed. Same convention paragraph_audit (1.34.1), controls_audit (1.37.1), construction_signature_audit (1.38.0), calibration_drift_monitor (1.39.0) use.
- **Hardened input handling**; structured ClaimLicense block. The license explicitly refuses to commit beyond the match/mismatch verdict — a `mismatch` does NOT prove the edits were AI-generated, by a different human editor, by the same editor on a different draft, or by no editor (the writer's own self-revision); a `matches_profile` does NOT prove THIS editor made the edits. The framework's "differential diagnosis, not verdict" stance applies in full.

### Notes

- **1187 tests pass + 1 skipped** (was 1134+1 in 1.39.0; +53 new tests across `test_mimicry_cosplay_audit.py` (28) and `test_known_editor_profile.py` (25)).
- **No breaking changes.** Both pieces are new scripts; no existing surface modified.
- **Schedule status: Release 10 shipped.** Per the paired-release schedule, the next release is Release 11 — Phraseological Signature Audit (Surfaces T3) + Draft-history analysis (Trust T3).
- **R10 is the framework's adversarial-completeness pivot.** Pre-1.40.0 the validation harness's adversarial-class track shipped fixture-acquisition + per-class slicing (R7's robustness card was the per-signal output shape over those fixtures); what was missing was a tool that reads cosplay shapes directly from a draft, and a tool that distinguishes a known editor's transformation profile from infrastructure-level smoothing. R10 ships both.
- **The cosplay audit's design refuses aggregation**, on principle. The framework already knows how to compute Delta and idiolect survival as separate audits; what was new in R10 was the recognition that **combining them** dissolves the cosplay signature. Cosplay shows up as a *correlation* between two signals that normally vary independently. Reporting them as separate cells (with a verdict that names the dissociation) preserves the diagnostic information; reporting them as one composite score destroys it. This is the same epistemic-discipline shape the rest of the framework's surfaces share.
- **The known-editor profile is the framework's first profile-learning surface.** Earlier surfaces had thresholds and parameters but did not LEARN anything — they computed signals and applied fixed cutoffs. The known-editor profile learns from labeled data: a small set of (before, after) pairs is enough to characterize an editor's typical delta distribution, and the match step reports per-signal z-scores rather than a global similarity score. The profile shape is suitable for re-use: the eventual transformation-profile-learning surface (per ROADMAP §13+) can use the same shape over any before/after pair set, not just one editor's pairs.

## [1.39.0] - 2026-05-10

**Paired-release schedule, Release 9: validation-infrastructure release.** No paired tool — both pieces are guardrails. The framework now ships enough surface area that infrastructure drift between releases needs explicit monitoring, and the linguistic-background caution surface needs to be visible at report level. R9 ships both: a snapshot/check drift monitor over the framework's per-signal outputs, and an eight-condition fairness/dialect/multilingual caution surface that caps posture when the validation set doesn't include comparable language backgrounds.

### Added — Calibration Drift Monitor (Trustworthiness Tier 2/3)

- **`scripts/calibration_drift_monitor.py`** — infrastructure-drift detector. The framework's threshold values and per-signal computations depend on the dependency stack at run time: spaCy version, the loaded `en_core_web_sm` model version, NLTK data, scipy, and the Python interpreter. Threshold values that were calibrated against one stack can shift materially when any of those move. Pre-1.39.0 the framework had no way to detect this.
- **Two CLI subcommands.** `snapshot --benchmark-dir BENCHMARKS/ --out SNAPSHOT.json` records the framework's outputs on a fixed benchmark set under the current dependency stack. `check --benchmark-dir BENCHMARKS/ --snapshot SNAPSHOT.json` recomputes the same outputs and compares against the recorded snapshot, reporting per-signal drift verdicts.
- **Three drift dimensions tracked.** (a) Per-benchmark per-signal value drift (verdict: `stable` / `drifted` / `added` / `removed`); (b) framework threshold-constant changes (deliberate code changes — reported separately because the cause is different); (c) dependency-stack metadata changes (Python / spaCy / model / NLTK / scipy versions).
- **Per-signal noise floor.** Drift is the maximum of an absolute floor (per-signal, conservative defaults — `burstiness_B`: 0.05, `mtld`: 5.0, `connective_density`: 1.0, etc.) and a relative-change floor (default 10%). A single signal moving below both floors is `stable`; above either is `drifted`.
- **CI-friendly exit codes.** `--exit-nonzero-on-drift` returns rc=3 when drift is detected, suitable for use as a CI gate. Recalibration is recommended when (a) threshold constants changed or (b) the stack changed AND signals drifted — the drift wasn't just deliberate code movement.
- **Privacy-by-default**: benchmark IDs are anonymized (`benchmark_001`, `benchmark_002`) unless `--include-filenames` is passed. Same convention as the rest of the framework's privacy-default surfaces (paragraph_audit 1.34.1, controls_audit 1.37.1, construction_signature_audit 1.38.0).
- **Hardened input handling**; structured ClaimLicense block. The license explicitly clarifies the monitor reports REPRODUCIBILITY across the dependency stack — whether outputs are byte-stable from one run to another. It does NOT validate the underlying calibration. A snapshot with miscalibrated thresholds remains miscalibrated; the monitor only flags when those values move further.

### Added — Fairness / Dialect / Multilingual Guardrails (Trustworthiness Tier 1)

- **`scripts/fairness_dialect_guardrails.py`** — linguistic-background caution surface. The ESL ratchet has existed in `manifest_validator.py` since 1.31.0 (a warning when voice-coherence baselines mix native and non-native English prose). The broader linguistic-background caution layer has not been visible at the report level. This module is that layer.
- **Eight conditions tracked.** Two can be heuristically detected (or declared): `nonnative_english`, `code_switching`. Six are declaration-only — the framework refuses to infer them from prose: `dialect_features`, `translation_influenced`, `speech_to_text`, `neurodivergent_patterns`, `educational_genre`, `institutional_template`. The declaration-only design is load-bearing: dialect identification is contested, neurodivergence diagnosis from prose is harmful, and the framework explicitly refuses both at the report level.
- **Code-switching heuristic detection.** Flags non-ASCII Latin-extended letters and non-Latin scripts (Cyrillic, CJK, etc.) above a small threshold (≥ 5 letters AND ≥ 0.5% of total). Conservative — false positives on a few accented loanwords ("café", "naïve") in otherwise-Standard English are intentionally absent (below threshold). Declared conditions are more reliable than heuristic ones; the design surfaces detection as `source: detected` vs. `source: declared` in the report.
- **Baseline-coverage check.** Reads the framework's manifest schema (`language_status` field: native / non_native_advanced / non_native_intermediate / learner / unknown). For each flagged condition, checks whether the validation baseline includes entries with a comparable language background. The condition→baseline mapping is conservative: `nonnative_english` requires non-native baseline entries; `dialect_features` / `speech_to_text` / `neurodivergent_patterns` / `institutional_template` are always reported as uncovered (the manifest schema doesn't track those backgrounds, so the conservative answer is no).
- **Three-level overall recommendation.** `no_conditions_flagged` (no caution; no posture cap), `conditions_present_baseline_matched` (caution noted; posture not capped at revision_only), `conditions_present_baseline_unmatched` (REFUSES evaluative / disciplinary use; caps posture at `revision_only`). The refusal is explicit, not a soft warning — the load-bearing fairness guarantee.
- **Manifest reading.** Accepts both TSV (the framework's primary manifest format, as used by `manifest_validator.py`) and JSON shapes. Counts only `use: baseline` entries.
- **Hardened input handling**; structured ClaimLicense block. The license explicitly cites the documented motivation: AI-detection tools have produced 61% false-positive rates on TOEFL essays (Liang et al., Patterns 2023); even when this framework is not an AI detector, users may apply it that way. The guardrail's job is to keep that misuse from masquerading as evidence.

### Notes

- **1134 tests pass + 1 skipped** (was 1074+1 in 1.38.0; +60 new tests across `test_calibration_drift_monitor.py` (30) and `test_fairness_dialect_guardrails.py` (30)).
- **No breaking changes.** Both pieces are new scripts; no existing surface modified. The fairness guardrail consumes the manifest schema as-is; the calibration drift monitor consumes the `variance_audit` API as-is.
- **Schedule status: Release 9 shipped.** Per the paired-release schedule, the next release is Release 10 — Mimicry / Style-Cosplay Audit (Surfaces T3) + Known-editor profile (Trust T3).
- **R9 is the framework's epistemic-discipline pivot.** Pre-1.39.0 the framework's surface area had grown to ~30 audit / harness / validation tools across smoothing diagnosis, voice coherence, calibration, craft restoration, and validation. The paired-release schedule's discipline ("ship a guardrail with every tool") is what kept the surface area's claim envelope honest. R9 makes that discipline visible at the infrastructure level: drift monitoring (does the framework reproduce?) and fairness coverage (does the validation set include the writer's background?). Both questions had implicit answers in earlier releases; R9 makes them explicit and CI-checkable.
- **The fairness guardrail's design refuses inference for six of eight conditions.** This is intentional. The framework can detect what it can detect (code-switching has stable Unicode signatures; nonnative-English status is recorded in the manifest schema). Beyond that — dialect, neurodivergence, translation-origin, speech-to-text origin, educational-genre context, institutional-template origin — the framework explicitly declines the inference, and asks the user to declare. This is a load-bearing design choice, not a coverage limitation: detecting dialect from prose is contested in linguistic research; detecting neurodivergence from prose is psychometrically harmful; detecting institutional-template origin from prose blurs the writer's authorship. The framework does not do those things, and the report's CLI accepts only the eight known declarations (argparse `choices`-rejected) so misuse at the CLI level fails loudly.
- **The calibration drift monitor's snapshot is a "release artifact" suggestion.** Future releases can optionally ship a `snapshots/v1.X.Y.json` alongside the release, generated against a small fixed benchmark corpus. CI can then run the `check` subcommand at every commit; any drift triggers the recalibration recommendation. This release ships the tooling, not the snapshot itself — the snapshot's benchmark set is a per-deployer choice.

## [1.38.0] - 2026-05-10

**Paired-release schedule, Release 8: interpretable syntactic evidence + semantic-preservation guardrails.** Both pieces address the same problem from different angles — making structural-level revision answer to meaning. The construction signature audit translates the framework's POS-bigram KL machinery into named syntactic constructions a craft editor can read directly; the semantic preservation check extends `before_after_restoration.py`'s post-check loop with seven semantic-guardrail categories that catch the failure mode where voice restoration moves the stylometric signals but quietly shifts the meaning.

### Added — Construction Signature Audit (Surfaces Tier 3)

- **`scripts/construction_signature_audit.py`** — interpretable syntactic-construction density audit. The right answer to "POS-bigram KL is opaque." Where `variance_audit.py` reports a numerical KL divergence between target and baseline POS-tag-pair distributions, this module names the *constructions* that drive that divergence and reports each one's density per 1,000 words. The signal becomes readable to craft editors who know what a fronted adverbial is but never needed to look at a tag-bigram heatmap.
- **Twelve named constructions detected.** Nine regex-only (no spaCy required): cleft (`It is X that Y`), pseudo-cleft (`What X is Y`), existential there, extraposition (`It is important to V` / `It is clear that Y`), correlative (not only / but also; either / or; neither / nor; both / and; not just / but), concessive opener (Although / Though / While / Even though / Despite / Whereas), participial opener (sentence-initial -ing or -ed phrase + comma), fronted adverbial (sentence-initial PP / adverbial clause), parenthetical insertion (clause-medial comma-bounded). Three spaCy-enhanced (require `en_core_web_sm`): agented passive, agentless passive, stacked prepositional phrases.
- **Cleft / extraposition disambiguation:** the cleft regex requires X to be a foregrounded NP (definite article, demonstrative, proper noun, or pronoun); the extraposition regex matches either `It is X to V` (always extraposition, since cleft cannot take a `to V` continuation) or `It is [predicative-adj] that Y` where the predicate is from a curated list (clear / obvious / important / necessary / etc., 60 entries). Extraposition is detected first and claimed spans are excluded from cleft detection.
- **Output shape mirrors `aic_pattern_audit.py`** — same shape, different unit. Per-construction count + `density_per_1k` + top hits + optional baseline comparison via `--baseline-dir`. Pairs naturally with the AIC density audit at the report level.
- **spaCy-optional graceful degradation:** when spaCy is unavailable, the three spaCy-enhanced constructions report `available: false` rather than producing degraded results. Same convention the rest of the framework uses.
- **Hardened input handling** (1.34.2 conventions): missing target / missing baseline-dir / empty baseline-dir all return rc=2 with clear stderr messages. Privacy-by-default: `--baseline-dir` filenames are anonymized in the JSON output unless `--include-baseline-filenames` is passed.
- **Structured ClaimLicense block** explicitly refuses provenance verdicts: construction density is a voice-coherence layer, not a classifier. AI-shaped prose has characteristic construction preferences (high agentless passive, high extraposition, high stacked PPs), but so do many legitimate registers (institutional / legal / academic prose). The audit pairs naturally with the confounder audit and the evidentiary-conditions gate.

### Added — Semantic Preservation Check (Trustworthiness Tier 3)

- **`scripts/semantic_preservation_check.py`** — semantic-guardrail comparison between an original (pre-restoration) text and a revised (post-restoration) text. Extends `before_after_restoration.py`'s post-check loop. The existing post-check flags metric-gaming (signal moved while a gaming-aggregate moved against it) and signal direction (improved / no_change / degraded / gamed). What it did NOT catch is the failure mode where voice restoration accidentally makes an argument more forceful, less accurate, or less careful — the prose got smoother, but the *meaning* shifted.
- **Seven preservation categories tracked.** Each gets a per-category preservation verdict: `preserved` / `shifted_dropped` / `shifted_added` / `shifted_changed` / `unknown`. The `shifted_added` verdict is load-bearing — it flags new items that appeared in the revision that weren't in the original (potential fabrication / over-confident restoration). Any single `shifted_added` flips the overall verdict to `shifted_added`.
  - **claim_inventory** — declarative-sentence count (proxy for "how many propositions does the prose assert?")
  - **named_entities** — proper-noun + capitalized-multi-word phrases (uses spaCy NER when available; falls back to a capitalized-phrase regex)
  - **citations_and_authorities** — "according to X", "X said / argued / claimed", "research shows", parenthetical (Author, YEAR) citations
  - **stance_markers** — claim verbs (argue / contend / maintain / suggest) + evaluative adverbs (clearly / surprisingly / importantly)
  - **modal_verbs** — must / should / shall / may / might / can / could / will / would / ought
  - **causal_claims** — because / due to / therefore / thus / hence / leads to / results in / causes / enables / as a result of / consequently / so that
  - **hedges** — perhaps / maybe / possibly / arguably / seems / appears / suggests + multi-word hedges (it is possible / it is likely / kind of / sort of / to some extent)
- **Multiset-aware diff:** for each category the report names items dropped (in BEFORE, missing in AFTER), items added (in AFTER, missing in BEFORE), items shared (in both). The dropped/added lists let the writer audit each specific change rather than reading only an aggregate count.
- **Verdict thresholds with small-count floor:** under five items in either side, the verdict demands absolute movement (≥ 2 items added or dropped) rather than ratio movement. This prevents short documents from getting flagged on single-item drift.
- **Hardened input handling**; structured ClaimLicense block. The license explicitly refuses "the revision is better" verdicts: the check reports preservation, not quality. A revision that reduces hedges might be more concise OR less careful — the framework refuses to choose. The author's review remains the load-bearing step.

### Notes

- **1074 tests pass + 1 skipped** (was 982+1 in 1.37.2; +92 new tests across `test_construction_signature_audit.py` (48) and `test_semantic_preservation_check.py` (44)).
- **No breaking changes.** Both pieces are new scripts; no existing surface modified.
- **Schedule status: Release 8 shipped.** Per the paired-release schedule, the next release is Release 9 — calibration drift monitor + fairness / dialect / multilingual guardrails (validation-infrastructure release, no paired tool).
- **The construction audit and semantic preservation check are interpretation tools** in opposite directions. The construction audit operates on a single text and tells the writer which syntactic patterns drive the variance signal — interpretive depth over the prose itself. The semantic preservation check operates on a (before, after) pair and tells the writer which semantic features moved during revision — interpretive depth over the *change*. Together with the surface-disagreement resolver and the robustness card from R7, the framework now ships a complete meta-interpretive layer: cross-surface (R7), within-surface syntactic (R8), and across-revision semantic (R8).
- **The construction audit is the natural pairing for the AIC density audit.** Both report per-pattern density per 1k words; both surface the writer's habitual repertoire vs. the baseline. The difference is the unit — AIC counts named rhetorical figures (correctio, manifesto cadence, false-balance), construction signature counts named syntactic constructions (cleft, extraposition, fronted adverbial). The two audits compose into a complete craft-readable surface over the framework's distributional machinery.
- **The semantic preservation check is the missing piece in the restoration loop.** Pre-1.38.0 the loop went: `restoration_packet.py` (named target signals) → user revises → `before_after_restoration.py` (signal-direction post-check). The user could ship a revision that hit every signal target but quietly added causal claims, removed hedges, or replaced agented passives with agentless ones. The semantic preservation check makes that quiet shift visible. The intended workflow is now: restoration packet → revise → before-after restoration (signal direction) → semantic preservation check (meaning preservation) → ship.

## [1.37.2] - 2026-05-10

**Reviewer-flagged P2 fixes in the new R7 surfaces.** Four issues — three in `surface_disagreement_resolver.py`, one in `adversarial_robustness_card.py`. Reviewer reviewed `v1.36.0..v1.37.0`; the verdict was that R7's architecture is good but the resolver's surface-shape readers and the card's small-base aggregation needed patching to keep the new surfaces from quietly non-firing on production inputs.

### Fixed

- **`_read_voice_drift_level` returned `unknown` on real voice_distance band strings.** Pre-1.37.2 the reader did substring matches on `near` / `close` / `moderate` / `far` / `distant`, but `stylometry_core.voice_distance_band` actually emits `Close to baseline (...)`, `Light drift (...)`, `Strong drift (...)`, `Off-baseline (...)`. Reviewer reproduced all three drift levels collapsing to `unknown`. Fix: primary path matches the actual band strings (case-insensitive, parenthetical note stripped); secondary path falls back to `overall.weighted_delta` thresholds (0.75 / 1.25 / 2.0 — the same cutoffs the band assignment itself uses); tertiary path keeps the legacy substring fallback so older fixtures and any custom band labels still parse. `Strong drift` and `Off-baseline` now correctly map to `high`; `Light drift` to `moderate`; `Close to baseline` to `low`. The `register_shift_or_collaboration`, `self_conscious_imitation`, `gi_inconclusive_despite_drift`, and `agreement_high_compression` patterns now fire on real voice_distance JSONs.
- **`_read_pos_bigram_kl` read at the wrong nesting level.** Pre-1.37.2 the reader looked at `variance.get("pos_bigram_kl")`, but `variance_audit.classify_compression()` adds the block at the top of its own return dict, which the audit assigns to `output["compression"]`. So the actual JSON path is `compression.pos_bigram_kl`. Reviewer reproduced the `syntactic_template_shift` pattern never firing on real variance JSONs even with a clearly-compressed POS-bigram KL block present. Fix: read `variance["compression"]["pos_bigram_kl"]` first; fall back to top-level `variance["pos_bigram_kl"]` for legacy / hand-built fixtures. The `syntactic_template_shift` pattern now fires on real variance audits with baseline-relative POS-bigram KL data.
- **`_read_aic_density` read a path `aic_pattern_audit` never emits.** Pre-1.37.2 the reader looked at `aic.get("pattern_densities")` (a flat dict), but `aic_pattern_audit` actually emits `patterns.<pattern_key>.density_per_1k` per pattern. Reviewer reproduced the `rhetorical_habit_not_smoothing` pattern never firing on real AIC audits even with high-density patterns present. Fix: iterate `aic["patterns"][<key>]["density_per_1k"]` across patterns and take the max; fall back to legacy top-level `pattern_densities` flat-dict shape. The `rhetorical_habit_not_smoothing` pattern now fires on real AIC audits.
- **`adversarial_robustness_card.build_robustness_card` hid large `small_base` movements.** Pre-1.37.2 `_classify_movement` returned `small_base` for any near-zero base regardless of fixture magnitude, and the aggregator excluded `small_base` from `labels_seen`, so a movement like `compression_fraction 0.0 → 0.5` showed `small_base` at the cell level but `overall_robustness=unknown` and `n_fragile_signals=0` — silently hiding exactly the kind of large-absolute-movement-from-near-zero-base reading the user needed to see. Reviewer reproduced this with a one-fixture compression_fraction comparison. Fix: `_classify_movement` now distinguishes the truly-uninterpretable case (base near-zero AND fixture near-zero — label `small_base`, dropped from aggregation) from the notable case (base near-zero but fixture moved by ≥ stability_threshold — new label `unstable_small_base`, aggregated like `fragile`). `build_robustness_card` adds a new `n_unstable_small_base_readings` counter; `render_report` adds a `!` glyph and a header line for the new label. The `unstable_small_base` label is documented in the module docstring, claim-license block, and additional caveats.

### Notes

- **982 tests pass + 1 skipped** (was 940+1 in 1.37.0 → 958+1 in 1.37.1 → 982+1 here; +24 new regression tests across `TestVoiceDriftReader` (+6 real-band-string tests + weighted_delta fallback), `TestPosBigramKlReader` (7 new), `TestAicDensityReader` (6 new), and `TestClassifyMovement` / `TestBuildRobustnessCard` (5 new for `unstable_small_base`)).
- **No CLI breaking changes** for end users. The new `unstable_small_base` cell label and `n_unstable_small_base_readings` aggregate counter are additive; any consumer iterating fixed labels needs to add the new entry to its enum but won't see the label produced by inputs that already worked.
- **The three resolver fixes are all the same shape**: the readers were inferring JSON paths from the meta-layer's design intent rather than from each surface's actual emitted shape. Each fix replaces the wrong path with the correct one and keeps a legacy-shape fallback so older fixtures still parse. The fixes restore exactly the meta-interpretations the resolver was supposed to surface — `syntactic_template_shift`, `rhetorical_habit_not_smoothing`, and the four voice-drift-dependent patterns now fire on real audit JSONs.
- **The robustness-card fix preserves the framework's load-bearing convention**: don't silently hide large readings. `small_base` was conservative-by-design (reject relative-change comparison when base is too small), but it was conservative in *both* directions — uninterpretable cases AND clearly-notable absolute movements. The new `unstable_small_base` label keeps the conservative reading on truly-tiny movement while surfacing the notable case as fragile.

## [1.37.1] - 2026-05-10

**Reviewer-flagged P2 fixes in the new R6 surfaces.** Four issues — two in the evidentiary conditions gate, two in the controls audit. Reviewer reviewed `v1.35.1..v1.36.0`; the fixes apply against the current `main` (post-R7 at v1.37.0) since those surfaces haven't changed in R7. Note: reviewer's suggested patch tag was `v1.36.1`, but R7 already shipped as v1.37.0 between the review and the patch, so this lands as v1.37.1.

### Fixed

- **Failed audit JSONs could promote posture in the evidentiary gate.** Pre-1.37.1 `_count_audit_inputs` counted every non-None dict, including outputs with `available: false` or arbitrary unrelated JSON. Reviewer reproduced `forensic_adjacent_nondispositive` posture using five failed/empty audit payloads plus a confounder stub. Fix: per-surface usability validators (`_is_usable_paragraph`, `_is_usable_variance`, `_is_usable_voice_distance`, `_is_usable_confounder`, `_is_usable_gi`) that check the recognized-shape signature AND the `available: false` flag before counting. New `_count_audit_surfaces` helper takes named arguments (one per surface) and counts only usable inputs. The promotion gates now correctly reject failed payloads — the canonical reviewer reproduction (5 failed payloads + confounder stub + pre_edit + 2500 words) lands at `revision_only` instead of `forensic_adjacent`.
- **Variance + Tier-2 audit baselines were ignored by the gate.** Pre-1.37.1 `_read_baseline_size` only checked `voice_distance.baseline_summary.n_files` and `paragraph.baseline_block.n_files`. A normal variance-only run with `variance_audit --baseline-dir` and 25 baseline files reported `baseline_size: 0` and got capped at `exploratory_comparison`. Same applied to the Tier-2 audits (discourse / agency / punctuation / stance / function_grammar) which all use `baseline_block.n_files`. Fix: `_read_baseline_size` now accepts every audit kwarg, reads from `baseline.n_files` (variance), `baseline_summary.n_files` (voice_distance), and `baseline_block.n_files` (Tier-2 audits), and returns the *maximum* count — best signal of how rich the user's actual baseline is. End-to-end: a variance-only run with a 25-file baseline now correctly reaches `research_grade_validation` posture instead of being capped at exploratory by phantom-zero baseline size.
- **Missing user-supplied control paths in `controls_audit.py` silently downgraded.** Pre-1.37.1 `_read_optional_path` printed an error and returned `None`, so the CLI exited 0 with `negative_control.supplied=false` — making a typo look like deliberately-absent evidence. Reviewer reproduced this with a missing `--negative-control` path. Fix: the helper now returns `(text_or_None, error_occurred)` and the CLI checks the error flag, returning rc=2 on missing user-supplied paths. Same hardened-input convention from `confounder_audit.py` (1.34.2) and `evidentiary_conditions_gate.py` (1.36.0).
- **Empty post-filter baseline in `controls_audit.py` returned rc=0.** Pre-1.37.1, when every baseline entry overlapped the questioned/control paths and got filtered out, the audit returned `available: false` with rc=0 — misreporting a self-overlap-guard failure as a normal output. Reviewer reproduced with `--baseline-dir` containing only the questioned file. Fix: hard-fail with rc=2 + clear stderr message naming the cause. Same convention paragraph_audit (1.34.1) and general_imposters (1.29.1) use.

### Notes

- **958 tests pass + 1 skipped** (was 940+1 in 1.37.0; +18 new regression tests across `TestUsableAuditValidators` (9), `TestVarianceBaselineRead` (4) in `test_evidentiary_conditions_gate.py`, and `TestMissingControlPathsHardFail` (3) + `TestEmptyPostFilterBaseline` (2) in `test_controls_audit.py`). Plus signature change to `_read_baseline_size` (now keyword-only) — two existing tests in `TestIndicatorReadHelpers` updated to use kwargs.
- **No CLI breaking changes** for end users. Internal function signatures changed: `_read_baseline_size` is now keyword-only and accepts every audit kwarg; `_count_audit_inputs` was replaced by `_count_audit_surfaces` with the same shape. Both are private (`_`-prefixed) helpers; downstream callers shouldn't have depended on them.
- The two evidentiary-gate fixes are both **systematic biases against gate accuracy** that 1.36.0 shipped: posture promotion on bad inputs (over-confident) and posture demotion when variance was the only audit (under-confident). Together they made the gate's posture call unreliable in opposite directions on common inputs. Both fixed.
- The two controls_audit fixes bring it into line with the framework's hardened-input + self-overlap-guard conventions — same patterns paragraph_audit, general_imposters, and confounder_audit have used since 1.29.1 / 1.34.1 / 1.34.2 respectively.

## [1.37.0] - 2026-05-10

**Paired-release schedule, Release 7: interpretation meta-layer.** No paired tool. Two cross-surface interpretive guardrails: a resolver that names compatible interpretations from cross-surface disagreement patterns, and a per-signal robustness card output shape that catches transformation-fragility (paraphrase / copyedit / humanizer / backtranslation) across audit fixtures.

### Added — Surface-disagreement resolver (Trustworthiness Tier 1)

- **`scripts/surface_disagreement_resolver.py`** — meta-layer that interprets cross-surface disagreement patterns. The framework runs multiple surfaces over the same draft (variance / voice-distance / GI / paragraph / discourse / agency / AIC / idiolect); cross-surface interpretation has been left to readers to do by hand. This module does that interpretation explicitly. Reads any subset of audit JSONs, extracts directional readings (`high` / `moderate` / `low` / `unknown`) per surface, matches against a curated catalog of disagreement patterns, and surfaces compatible interpretations.
- **Ten curated patterns** in `DISAGREEMENT_PATTERNS`: `edited_authorial_voice` (high smoothing + low voice drift), `register_shift_or_collaboration` (low smoothing + high drift), `self_conscious_imitation` (high drift + high idiolect survival), `syntactic_template_shift` (high POS-bigram KL + normal sentence variance), `rhetorical_habit_not_smoothing` (high AIC + normal Layer A), `gi_inconclusive_despite_drift` (gray zone + high drift), `register_drift_to_institutional` (high agency loss + normal voice distance), `discourse_scaffolding_overload` (high discourse + low smoothing), `paragraph_regularization_only` (paragraph regularized but everything else fine), `agreement_high_compression` (multiple surfaces fire high together).
- **Pattern matcher** supports wildcard (`*`), exact match, and alternation `(low|moderate)`. `unknown` readings only match `*`, so missing-input surfaces don't false-trigger interpretations.
- **Multiple matches are expected.** The framework returns the differential, not a verdict. Consistent with the confounder audit's stance.
- **Hardened JSON input handling** (1.34.2 conventions); structured ClaimLicense block.

### Added — Adversarial robustness-card output shape (Trustworthiness Tier 2)

- **`scripts/adversarial_robustness_card.py`** — per-signal robustness card across fixture transformations. Pre-1.37.0 the validation harness's adversarial-class track was scoped as fixture acquisition + per-class slicing in the existing harness; what was missing was the **per-signal output shape**: "burstiness_B is stable under light copyediting but collapses under paraphrase." This module ships that output shape.
- **Reads variance_audit JSON** for a *base* text plus one or more *fixture variants* (the same text after transformation: paraphrase, light/heavy copyedit, humanizer, backtranslation, voice restoration). For each (signal, fixture) cell, computes the relative change vs. the base and assigns a robustness label.
- **Six robustness labels per cell**: `stable` (|Δ| ≤ 10%), `moderate` (10-30%), `fragile` (> 30%), `inverted_polarity` (sign flip on a clearly non-zero base), `small_base` (|base| too small for stable relative comparison), `unknown` (missing data).
- **Overall per-signal label** aggregates across fixtures: `stable` (all fixtures stable), `fragile` (any fixture fragile or inverted), `moderate` (mixed without fragile), `unknown` (no data).
- **Fixture supplied as `LABEL:PATH`** on the CLI (e.g., `--fixture paraphrase:para.json --fixture copyedit:edited.json`). Multiple fixtures supported via repeated flag.
- **Configurable thresholds** via `--stability-threshold` / `--fragile-threshold` so users can tighten or loosen the robustness call as needed.
- **Infrastructure-only**, not a fixture catalog. Users with their own paraphrased / edited / humanizer-output fixtures can use this immediately. The fixture-generation tooling lives in the validation harness's separate adversarial-class roadmap track.

### Notes

- **940 tests pass + 1 skipped** (was 888+1 in 1.36.0; +52 new tests across `test_surface_disagreement_resolver.py` (24) and `test_adversarial_robustness_card.py` (28)).
- **No breaking changes.** Both pieces are new scripts; no existing surface modified.
- **Schedule status: Release 7 shipped.** Per the paired-release schedule, the next release is Release 8 — Construction Signature Audit (Surfaces T3) + Semantic preservation check (Trust T3).
- The surface-disagreement resolver is the natural meta-layer over the now-richer surface set the framework ships. Pre-1.37.0 a reader had to read multiple audit reports and synthesize across them by hand; the resolver does that synthesis explicitly using a curated pattern catalog. The catalog is heuristic and additive — new patterns can land without changing the resolver's contract.
- The robustness card is **the missing reporting layer for the framework's adversarial track**. Every claim about signal robustness in prior CHANGELOG entries (e.g., "stable under copyediting; fragile under paraphrase") was anecdotal. With this card, robustness becomes a per-signal × per-fixture grid the user can read directly. The first concrete use case: when the validation harness eventually ships DIPPER paraphrase + humanizer-tool fixtures, the harness can emit robustness cards as standard output.
- Both tools are interpretation-only — they consume audit JSONs, they don't run audits themselves. That keeps them composable: any surface that emits JSON can feed either tool.

## [1.36.0] - 2026-05-10

**Paired-release schedule, Release 6: output-discipline release.** No paired tool — both pieces are guardrails. The framework's surfaces already carry per-output `claim_license` blocks naming what the result entitles, and 1.30.x added the differential-diagnosis layer (`confounder_audit.py`) for "compatible-with-what" interpretation. What was still missing — and is the load-bearing addition this release ships — is the *single front-door label* answering **what use is this output entitled for?**, plus the comparison frame that makes voice-distance numbers interpretable to non-technical readers.

### Added — Minimum Evidentiary Conditions Gate (Trustworthiness Tier 1)

- **`scripts/evidentiary_conditions_gate.py`** — front-door evidentiary-posture gate. Reads any combination of audit JSONs and emits an **Evidentiary Posture** label drawn from a fixed five-tier ladder:
  - `revision_only` — input too short, baseline too small, register mismatched, contamination too high. Safe for the writer's own revision; not safe for any other use.
  - `exploratory_comparison` — meaningful inputs but missing context. Useful for triangulating against other evidence; not on its own a claim.
  - `internal_triage` — sufficient evidence to flag work for closer review; not safe to publish or accuse.
  - `research_grade_validation` — labeled corpus, well-matched baseline, multiple corroborating audits, register match. Can support a publication-grade observation.
  - `forensic_adjacent_nondispositive` — strongest available posture; the framework still REFUSES dispositive authorship claims at any level. Forensic-grade use requires human review, due process, and out-of-framework corroborating evidence.
- **The output is not a numerical confidence score.** The framework refuses to compress evidentiary status into a number that could be misused in disciplinary, accusatory, or legal contexts. The label is qualitative; the rationale enumerates which evidence supports the level and which would raise it.
- **Indicator logic:** target length, baseline size, register-match strength, strip ratio (contamination), impostor pool size, audit-surface count, presence of a confounder diagnosis, presence of pre-edit version or known author, declared use case. Each indicator either *caps* the posture at a level or *promotes* it. The final posture is the minimum across all caps and the highest-met promotion gate.
- **Promotion gates encoded explicitly:** research-grade requires either a confounder audit or ≥ 3 corroborating audit surfaces; forensic-adjacent requires (a) pre-edit version OR known author, AND (b) a confounder diagnosis, AND (c) ≥ 5 corroborating surfaces.
- **User-declared use case caps but doesn't promote.** A user declaring `revision_only` won't get a higher label even if evidence supports it; a user declaring `forensic_adjacent` still receives `revision_only` if evidence is weak.
- **Hardened JSON input handling** (1.34.2 conventions): missing user-supplied paths fail loudly, only omitted flags become `None`.
- **Structured ClaimLicense block** explicitly refuses dispositive authorship claims at every posture level.

### Added — Controls Audit (Trustworthiness Tier 2)

- **`scripts/controls_audit.py`** — negative + positive controls for voice-distance comparison. Reports whether the questioned text is closer to a known-authentic control by the same writer (negative pole) or a known-smoothed / AI-edited / heavily-copyedited control (positive pole), against the same baseline. Output: side-by-side function-word L1 distances + classification of which pole the questioned text is closer to + within-band indicator.
- **Five classifications:** `closer_to_negative_control`, `closer_to_positive_control`, `negative_only` (only one pole supplied), `positive_only` (only one pole supplied), `baseline_only` (no controls), `questioned_unavailable` (questioned text empty).
- **Self-overlap guard** (1.29.1 / 1.34.1 convention): drops baseline entries whose resolved path matches the questioned text or either control with a stderr notice.
- **Structured ClaimLicense block** explicitly refuses to verdict on whether the questioned text is "really" authentic or smoothed — the audit measures distance, not provenance. The interpretation depends on the user vouching for the controls' labels.
- **CLI** mirrors `voice_distance.py`'s shape: `--questioned`, `--negative-control` (optional), `--positive-control` (optional), `--baseline-dir` or `--manifest`, full preprocessing flags.
- The metric is **function-word L1 distance, not full Burrows Delta** — robust enough for the comparison frame, doesn't require SpaCy or stylometry_core's full feature-extraction pipeline. Future expansion could add Delta as an opt-in for richer per-family comparison.

### Notes

- **888 tests pass + 1 skipped** (was 842+1 in 1.35.1; +46 new tests across `test_evidentiary_conditions_gate.py` (29) and `test_controls_audit.py` (17)).
- **No breaking changes.** Both pieces are new scripts; no existing surface is modified.
- **Schedule status: Release 6 shipped.** Per the paired-release schedule, the next release is Release 7 — interpretation meta-layer (Surface-disagreement resolver + adversarial robustness-card output shape), no paired tool.
- The Evidentiary Conditions Gate is the framework's **load-bearing epistemic discipline**. Pre-1.36.0 every surface carried its own `claim_license` block with surface-specific guarantees, but the framework had no single answer to "what use is THIS run entitled for?" — readers had to triangulate across surfaces. Now the gate provides a front-door label readers can act on before reading any specific surface's findings.
- The Controls Audit is the simpler interpretive frame writers actually want. The length-matched bootstrap from 1.30.0 gives a percentile against within-baseline scatter, which is statistically correct but reads quietly. The controls comparison reads loudly: "your questioned text is closer to your known-authentic essay than to the known-smoothed example" — exactly the comparison frame a non-technical reader can act on.
- The framework's privacy posture holds: neither tool emits raw text. The evidentiary gate works entirely off audit JSON; the controls audit emits per-text distance numbers but never quotes prose.

## [1.35.1] - 2026-05-10

**Reviewer-flagged P2 fixes in the new R5 surfaces.** Three issues — two in `variance_audit`'s ablation contract, one in the stance audit's evidential category. None broke tests because the existing fixtures masked the failure modes; each one would misstate downstream results in real use.

### Fixed

- **Ablation subtracted weight for unavailable signal families.** Pre-1.35.1 `ablation_band_calls` gated signal inclusion on length-floor only (`if n_words < spec.length_floor: continue`), but `classify_compression` only adds a signal's weight to `available_weight` when its VALUE was retrieved. The two are equivalent only when all tiers are enabled. Reviewer reproduced `--no-tier3` runs with `available_weight=5.5` reporting `over_cohesion weight_excluded=1.5` even though adjacent_cosine signals were never in the call. Fix: `classify_compression` now records an explicit `available_signals: list[str]` in its result dict — the names of signals whose value was retrieved AND cleared the length floor (i.e., contributed to `available_weight`). `ablation_band_calls` reads from `available_signals` directly instead of re-deriving from length floors. Tier-3-disabled runs now correctly report `weight_excluded=0` for tier-3 families. Pre-1.35.1 callers who don't surface `available_signals` (legacy compression dicts) degrade gracefully — every family reports 0 weight excluded rather than crashing.
- **POS-bigram KL was load-bearing but invisible to ablation.** `pos_bigram_kl` participates in the compression call when a baseline is supplied (weight 2.0), but had no ablation family. A band call carried mostly or entirely by KL could report `is_robust_call=true` with no load-bearing families — exactly the wrong robustness reading. Fix: new `baseline_divergence` family in `_ABLATION_SIGNAL_FAMILIES` containing `pos_bigram_kl`. Ablation now also tracks pos_bigram_kl in `available_signals` when it's in-band (line 1539 of `classify_compression`). Future baseline-relative signals (e.g., syntactic-template divergence) belong in this same family. New helper `_signal_weight(signal)` in `ablation_band_calls` looks up weights in both `COMPRESSION_HEURISTICS` and `POS_BIGRAM_KL_HEURISTIC`, transparent to the family taxonomy.
- **Stance-modality evidential category double-counted phrases.** The evidential pattern set has two regexes: a bare-verb pattern (`shows`, `suggests`, ...) and a phrase pattern (`(evidence|research|...)\s+(shows|...)`). A phrase like "evidence shows" matched both, so `n_matches += len(pattern.findall(text))` counted it twice — once as the bare verb, once as the phrase. Reviewer reproduced inflated evidential density and downstream stance entropy / z-score corruption. Fix: collect match `(start, end)` spans across all patterns within a category, then deduplicate by **span containment** — a longer match that covers a shorter one wins; non-overlapping matches all count. The dedup applies uniformly to every category (hedges, boosters, etc.), so any future phrase-pattern additions don't reintroduce double-counting.

### Notes

- **842 tests pass + 1 skipped** (was 833+1 in 1.35.0; +9 new regression tests across `TestAvailableSignalsContract` (2), `TestBaselineDivergenceFamily` (3) in `test_ablation_band_calls.py`, and `TestEvidentialDeduplication` (4) in `test_stance_modality_audit.py`). Existing tests all still pass — the contract changes are additive.
- **No CLI breaking changes.** `classify_compression`'s result dict gains `available_signals`; `--ablation` output unchanged in shape (just more honest). Stance category counts will drop slightly on real prose where evidential phrases were double-counted; the band call thresholds calibrate against this corrected density (no recalibration needed because the heuristic floors were already conservative).
- The ablation contract is now epistemically tight. Pre-1.35.1 a `--ablation` report could lie in two ways: (a) saying a family was load-bearing when those signals were never in the call, or (b) saying the call was robust when KL was the actual carrier. Both are fixed; the report's claim "this family contributes weight X to the call" now matches what `classify_compression` actually did.
- Stance density in 1.35.0 reports against pre-1.35.1 fixtures will differ (mostly downward) on prose containing evidential phrases. Treat this as a corrected baseline; the underlying signal is unchanged, only the count.

## [1.35.0] - 2026-05-10

**Paired-release schedule, Release 5: three Tier-2 promotions + ablation reports.** Largest release in the schedule so far. Promotes three feature families that lived as columns inside `voice_profile.py` to top-level audits with their own band calls, baseline comparisons, structured ClaimLicense blocks, and hardened baseline ingestion. Pairs with the Tier-2 trustworthiness guardrail that tells readers which signal families are *load-bearing* for the existing variance audit's compression call.

### Added — Three Tier-2 surface promotions

- **`scripts/punctuation_cadence_audit.py`** — punctuation rhythm + interruption-grammar audit. Twelve per-mark densities (comma, semicolon, colon, em-dash, en-dash, parenthesis, bracket, ellipsis, double-quote, single-quote, exclamation, question), sentence-final distribution, **interruption grammar** (parenthetical asides, em-dash interruptions, comma appositives), punctuation bigram inventory, comma-period-share metric. Six rhythm signals fire the band call: comma-period dominance, semicolon suppression, em-dash suppression, low interruption grammar, uniform sentence finals, low punctuation-bigram diversity. Catches the pattern AI smoothing and copyediting often produce **before lexical-diversity signals fire** — punctuation regularization is one of the earliest trace marks of edited prose.
- **`scripts/stance_modality_audit.py`** — typed stance / modality / epistemic-posture audit. Seven categories: deontic_modality (`must / shall / should / required`), epistemic_modality (`may / might / could / probably`), hedge (`somewhat / arguably / sort of`), booster (`clearly / obviously / certainly / indeed`), evidential (`seems / suggests / shows / demonstrates`), first_person_stance (`I think / we argue / it seems to me`), refusal (`does not show / cannot conclude / is not enough to establish`). Per-category density + stance entropy + hedge-booster ratio. Six rhythm signals: hedge-booster oscillation (LLM-characteristic), booster dominance, low refusal density, first-person stance collapse, high deontic modality, low stance entropy. Surfaces shifts in epistemic posture that AI editing often makes (booster dominance, refusal absence, first-person collapse) before vocabulary changes are visible.
- **`scripts/function_word_grammar_audit.py`** — function-word **sequence grammar**. Goes beyond `voice_distance.py`'s function-word frequency view (Burrows Delta) into the sequence layer: function-word n-gram inventory + entropy, preposition profile + entropy, demonstrative usage (this/that/these/those rates and dominance), relative-pronoun choice (which/that/who proportions), complementizer choice (that/if/whether), subordinator profile (because/although/while/when/since/...), auxiliary chains (`have been`, `will have been`), pronoun-transition same-share. Six rhythm signals: low function-word bigram entropy, low preposition entropy, single-demonstrative dominance, relative-pronoun monotony, low subordinator density, uniform pronoun transitions.

All three new audits ship with hardened baseline ingestion (1.34.x conventions): validates baseline directory exists, surfaces skipped files with reasons, accepts `target_path` and excludes baseline overlap with stderr notice, anonymizes per-file IDs by default with `--include-baseline-filenames` for opt-in. All three carry `task_surface = "voice_coherence"`.

### Added — Ablation reports (Trustworthiness Tier 2)

- **`variance_audit.ablation_band_calls(compression_result, audit)`** computes leave-one-feature-family-out band calls. The four signal families (matching the heatmap phenomenon classifier from 1.32.0):
  - `syntactic_flattening` — burstiness_B, sentence_length_sd, fkgl_sd, mdd_sd
  - `lexical_compression` — mtld, mattr, shannon_entropy, yules_k
  - `over_cohesion` — adjacent_cosine_mean, adjacent_cosine_sd
  - `connective_overuse` — connective_density
- **Closed-form arithmetic on top of `classify_compression`'s output** — no extra audit run. Known per-signal weights + which signals fired ⇒ subtract family contributions from numerator (fired weights) and denominator (in-scope weights), re-bucket the resulting fraction.
- **Per-family `robustness` label**: `stable` (band unchanged when family removed), `fragile_drop` (band dropped — the family was load-bearing), `fragile_rise` (band rose — rare; family was diluting available_weight without firing).
- **Top-level summary**: `is_robust_call` (boolean) and `load_bearing_families` (list). A robust call holds across every ablation; a fragile call depends on one or two families. Tells readers which families to weight when interpreting the band.
- **`format_ablation_block(ablation)`** renders a markdown table; the variance_audit `--ablation` CLI flag triggers the computation and surfaces it in the report.

### Notes

- **833 tests pass + 1 skipped** (was 784+1 in 1.34.2; +49 new tests across `test_punctuation_cadence_audit.py` (15), `test_stance_modality_audit.py` (12), `test_function_word_grammar_audit.py` (10), `test_ablation_band_calls.py` (12)).
- **No breaking changes.** Three new tools; one new optional CLI flag (`--ablation`) on `variance_audit.py` with no behavior change when omitted.
- **Schedule status: Release 5 shipped.** Per the paired-release schedule, the next release is Release 6 — output-discipline (Minimum Evidentiary Conditions Gate + Negative/Positive Controls), no paired tool.
- The three Tier-2 promotions intentionally don't replace `voice_profile.py`'s feature columns (which feed Burrows Delta and the cluster-mode comparison). They sit alongside as **standalone diagnostic surfaces** with their own band calls — the same way `paragraph_audit.py` sits alongside the variance audit's word-windowed sliding heatmap.
- Punctuation cadence is the most empirically clean of the three (regex over orthography). Stance / modality is moderately clean (English-specific markers). Function-word grammar uses the canonical `FUNCTION_WORDS` set from `stylometry_core.py` and adds sequence-layer features that don't currently feed any other audit. None of the three uses spaCy; all are stdlib + regex-only.
- Ablation reports significantly improve the interpretability of the variance audit's compression call. Pre-1.35.0 a "Heavily smoothed" verdict from variance_audit didn't tell the reader which signal family carried the call; users had to read the per-signal flagged list and infer. Now a single `--ablation` flag surfaces "robust to removing X, fragile if Y removed" — exactly the diagnostic the user can act on.

## [1.34.2] - 2026-05-10

**Reviewer-flagged P2 fixes across the Release-3 + Release-4 surfaces.** Five issues across `confounder_audit.py`, `discourse_move_signature.py`, and `agency_abstraction_audit.py`. None broke tests because the existing test fixtures masked each failure mode. Each one corrupts a downstream claim or repeats a hardening footgun the older tools already fixed.

### Fixed

- **Confounder audit: MDD drift inferred from any rhythm flag.** Pre-1.34.2 `extract_observations` set BOTH `sentence_variance=low` AND `mdd_variance=low` when ANY rhythm flag fired (burstiness_B, sentence_length_sd, fkgl_sd, mdd_sd). The two are separate signals in the confounder matrix; co-triggering them off the same surface flag gave `ai_smoothing` (which expects both) extra evidence it didn't earn. Reviewer reproduced `{'compression': {'flagged_signals': ['burstiness_B']}}` producing an MDD observation. Fix: sentence-rhythm flags (burstiness_B / sentence_length_sd / fkgl_sd) fire `sentence_variance` only; `mdd_sd` fires `mdd_variance` only. Each signal is independent. **4 new tests** in `TestMddDriftInference`.

- **Confounder audit: missing or invalid JSON inputs degraded unsafely.** Pre-1.34.2 `_read_json_or_none` returned `None` on any failure — including a user-supplied path that was missing or contained invalid JSON. Reviewer reproduced a typo'd `--agency-json` exiting 0 with `agency: false`, making the typo look like deliberately absent evidence. Fix: distinguish "user didn't pass this flag" (returns `None`) from "user passed a path that's missing or invalid" (raises `FileNotFoundError` / `ValueError`). The CLI now wraps the input loads in try/except and returns rc=2 with a clear stderr message. **6 new tests** in `TestJsonInputHardening`.

- **Confounder audit: idiolect evidence requested but impossible to supply.** Pre-1.34.2 the missing-evidence list told users to provide `idiolect_detector` output, the matrix relied on `idiolect_survival` to separate AI smoothing from imitation and human editing — but the CLI had no `--idiolect-json` flag and `extract_observations` had no path to read idiolect output. The signal was permanently unobserved, making the matrix's expectations on it dead weight. Fix: new `--idiolect-json` flag reads idiolect_detector JSON output; new `--target-text` flag reads the target text; new `_idiolect_survival_rate(idiolect, target_text)` helper computes the fraction of preservation-list phrases that appear in the target (case-insensitive substring match — same convention `before_after_restoration.py` uses). Survival ≥ 0.6 → `high`; < 0.3 → `low`; the ambiguous 0.3-0.6 range leaves the signal unobserved (consistent with the framework's missing-evidence discipline). The missing-evidence rationale now names the exact CLI flags. **6 new tests** in `TestIdiolectSurvival` covering high-survival, low-survival, no-target-text, no-preservation-list, string-phrase format, and ambiguous-range-unobserved.

- **Discourse + Agency baselines repeated the same baseline-ingestion footguns paragraph_audit fixed in 1.34.1.** Both `audit_baseline_discourse` and `audit_baseline_agency` silently treated nonexistent `--baseline-dir` as empty baselines, silently dropped unreadable files, included the target file when it lived inside `--baseline-dir`, and emitted raw filenames in `per_file_summaries`. Same issues, same fixes: directory existence is now validated (raises `FileNotFoundError`); unreadable / audit-unavailable files surface in `skipped_files`; both functions accept an optional `target_path` parameter and exclude baseline overlap with a stderr notice; per-file summaries use anonymized `baseline_001` IDs by default with `--include-baseline-filenames` for opt-in. **10 new tests** across `TestBaselineHardening` in both files (5 each: nonexistent-raises, target-overlap-excluded, anonymized-by-default, opt-in-preserves-filenames, skipped-files-recorded).

### Notes

- **784 tests pass + 1 skipped** (was 757+1 in 1.34.1; +27 new regression tests across the four fixed scripts).
- **No CLI breaking changes.** Both `audit_baseline_discourse` and `audit_baseline_agency` gain two keyword-only parameters (`target_path`, `include_filenames`) with safe defaults; both CLIs gain `--include-baseline-filenames`. The `confounder_audit` CLI gains `--idiolect-json` and `--target-text`. `_read_json_or_none` now raises on invalid input — but the only callers were `confounder_audit.main()` itself (now wrapped) and tests (now updated).
- The MDD-fix is small but load-bearing. Pre-1.34.2 every variance audit with any rhythm flag firing produced an MDD observation, which the matrix scored as evidence for ai_smoothing. The fix removes that systematic bias; AI-smoothing's compatibility score now depends on whether mdd_sd actually fired, not just whether *any* rhythm signal did.
- The idiolect-input plumbing closes a long-standing gap: the matrix referenced `idiolect_survival` since 1.33.0, but the CLI had no way to populate it. Combining `--idiolect-json` and `--target-text` now produces the observation the matrix needs to distinguish AI smoothing (low survival) from intentional voice imitation (high survival, often "over-preserved") and from human editing (high survival).
- The discourse + agency baseline hardening brings them into line with paragraph_audit (1.34.1), general_imposters (1.29.1), and the rest of the framework's baseline-ingestion conventions: validate the directory, surface what was skipped, exclude target overlap, anonymize filenames by default. Privacy-by-default is the right posture for any tool that might end up in a public report.

## [1.34.1] - 2026-05-10

**Reviewer-flagged P2 fixes across the four most recent surfaces.** The 1.31.0 register-conditioning guard, the 1.32.0 paragraph audit, and the 1.32.0 paragraph baseline path each had subtle correctness or privacy issues that shipped in 1.32.x and survived through 1.34.0. None broke tests because the existing tests used synthetic fixtures that masked each failure mode. This release pins all four with regression tests that target the exact reproductions the reviewer described.

### Fixed

- **Register guard never saw baseline register metadata.** `voice_distance.py`'s 1.31.0 register-match check read `entry.get("register")` directly, but `stylometry_core.load_entries_from_manifest` puts the register under `entry["metadata"]["register"]` (and directory baselines have no top-level register at all). Result: every normal manifest run *and* every `--baseline-dir` run reported a false register mismatch (target classified as e.g. `literary_fiction` was being compared against a baseline whose registers all read as `unknown`). Fix: factored out `_baseline_registers(entries)` that reads both shapes (manifest-loaded under `metadata.register`, top-level fallback for forward-compat) and `_build_register_match(entries, target)` that returns `strength="unavailable"` with an explanatory rationale when no entry exposes a register, instead of calling `register_match` with an all-unknown distribution and producing a false `mismatch`. The CLI now passes the user-supplied `--register` (when set) as a hint to the classifier so the user's declared register nudges borderline classifications. **11 new tests** in `test_voice_distance_register_guard.py` cover the metadata path, top-level fallback, metadata-takes-precedence, directory-baseline returns `None`, empty metadata, and the full set of strength outcomes (unavailable / strong / mismatch / partial coverage / empty-string treated as missing).
- **Paragraph baseline path accepted invalid and self-contaminated baselines.** `paragraph_audit.py`'s 1.32.0 `audit_baseline_paragraphs` silently treated nonexistent `--baseline-dir` paths as empty baselines, silently dropped unreadable files, and had no way to exclude the target file when it lived inside the baseline directory. Reviewer reproduced `target.txt` appearing in `per_file_summaries`, and a nonexistent baseline dir exiting 0 with `baseline empty`. Fix: directory existence is now validated (raises `FileNotFoundError`); unreadable / audit-unavailable files surface in a new `skipped_files` list with their reasons; the function accepts an optional `target_path` parameter and excludes baseline entries whose resolved path matches the target with a stderr notice (same convention as `general_imposters.py`'s `_exclude_target_path` from 1.29.1). The CLI threads the resolved target path through. **3 new tests** cover nonexistent-dir-raises, target-overlap-excluded, unreadable-file-surfaces.
- **Public-safe paragraph JSON leaked baseline filenames.** The 1.32.0 release notes advertised paragraph audits as safe for public reports because the audit emits no raw text — but `--json --baseline-dir` embedded `baseline_block.per_file_summaries` with each baseline's raw filename. Filenames in stylometry workflows often carry manuscript titles, client names, dates, or publication subjects — exactly the metadata the framework's other tools (voice_profile, idiolect_detector, gen_imposters) take care not to leak. Fix: per-file summaries now use anonymized IDs (`baseline_001`, `baseline_002`, …) by default; opt in to raw filenames via `--include-baseline-filenames` when the report stays in private channels. The `include_filenames` choice lands in the JSON output's metadata so downstream consumers can see the privacy posture. **2 new tests** cover default-anonymization and opt-in-preserves-filenames.
- **Long-cluster paragraph signal was effectively dead.** `paragraph_audit.py`'s `dominant_long_paragraph_cluster` flag required a contiguous run covering > 30% of paragraphs, but `long_clusters` only records runs above the document's p75 — at most ~25% of paragraphs by definition. Reviewer reproduced "3/10 long run recorded but not flagged." The combination made the flag mathematically unreachable for any reasonable distribution, so the reported `n_signals = 6` was effectively `n_signals = 5`. Fix: lowered the dominance threshold from `> 0.30` to `>= 0.20` and changed the comparison to inclusive. With the p75 ceiling this still only fires on documents with a clearly clustered run of 3+ long paragraphs covering ≥ 20% of the doc — meaningful, not uniform. **2 new tests** cover the previously-impossible case (3 long in 10 fires the flag) and the negative case (a single long paragraph with no contiguous run does NOT fire).

### Notes

- **757 tests pass + 1 skipped** (was 739+1 in 1.34.0; +18 new regression tests across `test_voice_distance_register_guard.py` and the `1.34.1 reviewer-flagged P2 fixes` block in `test_paragraph_audit.py`).
- **No CLI breaking changes.** `audit_baseline_paragraphs` gains two keyword-only parameters (`target_path`, `include_filenames`), both with safe defaults. The CLI gains `--include-baseline-filenames` flag; the privacy default is anonymization. The `register_match` block in voice_distance result JSON gains an `"unavailable"` strength label as a new option, but existing strength labels (strong / moderate / weak / mismatch) are unchanged.
- **Privacy posture sharpened.** The paragraph audit's default no-raw-filenames behavior brings it into line with the rest of the framework (voice_profile, idiolect_detector, voice_drift_tracker, pov_voice_profile, general_imposters all default to private-output paths or anonymized IDs). The opt-in flag is the explicit "I know this is private" gate.
- The register-guard fix is the highest-impact of the four — every voice_distance run with a manifest baseline was emitting a false register-mismatch warning since 1.31.0, which would have eroded trust in the trustworthiness layer. The new `unavailable` strength label is the honest "I have no signal here" output rather than the spurious "mismatch" output.
- The long-cluster threshold fix corrects a documented signal count: the audit's compression-fraction band call was nominally over 6 sub-signals but actually had only 5 ever-firing signals. The flag now actually fires when the failure mode it's named for (clustered long paragraphs) is present.

## [1.34.0] - 2026-05-10

**Paired-release schedule, Release 4: Agency and Abstraction Audit + Revision-risk model + agency family folded into the confounder matrix.** Per the (1.32.1-narrowed) dependency rule, Agency and Abstraction Audit is the *strengthening complement* to the confounder audit shipped in 1.33.0 — it lands in this release and immediately folds into the confounder matrix to sharpen the differential diagnosis. The revision-risk model extends `restoration_packet.py`'s targetability taxonomy with per-packet risk classification (low / medium / high) along orthogonal axes that the targetability classes don't cover.

### Added — Agency and Abstraction Audit (Surfaces Tier 1)

- **`scripts/agency_abstraction_audit.py`** — agency-loss / abstraction-drift detector. The shipped Layer A suite measures distributional compression at sentence + token layers; the paragraph audit (Release 2) added macro-rhythm; the discourse audit (Release 3) added typed scaffolding. What's structurally missing — and where AI smoothing and institutional editing often do their most legible damage — is **agency loss**: nominalized actions replacing concrete verbs, agentless passives replacing named actors, light-verb constructions ("make a decision," "provide support") replacing direct verbs, generic-institutional vocabulary replacing situated detail.
- **Seven per-signal densities (per 1,000 words)**: nominalization (suffixed derivationals — `-tion / -sion / -ment / -ity / -ance / -ence / -ness / -ization`), agentless passive rate (passives without a named `by X` agent), light-verb constructions (verb + nominalized noun), generic institutional vocabulary (`framework / landscape / dynamic / challenge / opportunity / approach / strategy / actionable / leverage / holistic / robust` etc.), concrete-detail vocabulary (curated heuristic anchor — temporal markers, household objects, kinship terms, sensory nouns), action verbs (curated heuristic anchor — `walked / ran / held / built / cooked / sang` etc.), proper nouns.
- **Entity-to-action ratio**: `(proper nouns + concrete detail) / action verbs` per 1k words. High = situated; low = abstracted. Saturates to entity count when action verbs are zero.
- **Compression-fraction band call** (Lightly / Moderately / Heavily *abstracted*) over six signals. Flags fire on high nominalization, high agentless passive rate, high light-verb density, high generic-institutional density, low concrete-detail density (≥ 500-word doc), low action-verb density (≥ 500-word doc).
- **Optional baseline comparison** computes per-signal z-scores against the baseline aggregate.
- **Structured ClaimLicense block** explicitly refuses to commit to "good vs. bad" abstraction — institutional prose has legitimate reasons to use abstract noun forms.
- **CLI** mirrors the rest of the Surfaces Tier-1 audits: `--baseline-dir`, `--strip-masking`, `--json`, etc.

### Added — Revision-risk model (Trustworthiness Tier 3)

- **`Packet.revision_risk` and `Packet.revision_risk_rationale`** fields on the `restoration_packet.Packet` dataclass. Default `unspecified` for backward compat with packets built before 1.34.0.
- **`classify_revision_risk(targetability, signal, severity)`** maps each targetability + signal combination to `("low" | "medium" | "high", rationale)` along eight risk axes (erase idiolect, create metric gaming, increase generic humanizer artifacts, damage clarity, damage genre expectations, overcorrect into artificial variance, preserve voice but weaken argument, restore quirks intentionally edited out). The risk table covers the canonical signals (sentence-length variance, burstiness, connective density, idiolect, AIC patterns, FKGL, MATTR, POS bigrams, function-word clusters) plus targetability-class fallbacks for `investigate_first` (high — "treating the diagnostic as a target is the canonical metric-gaming failure mode") and `avoid_direct` (high — "structural anti-goal").
- **Severity-driven escalation**: severity `heavy` bumps low → medium and medium → high to reflect the larger-stakes intervention.
- **`apply_revision_risk(packet)`** fills the fields on a packet; called automatically by `build_packets` so every emitted packet carries a risk label.
- **Markdown rendering** in `_render_packet_md` surfaces the risk + rationale; **⚠** marker on medium / high so the reader sees the caveat alongside targetability and severity.

### Changed — Agency family folded into confounder matrix

- **`confounder_audit.CONFOUNDER_MATRIX`** gains four agency-family signals (`nominalization_density`, `agentless_passive_rate`, `generic_institutional_density`, `concrete_detail_density`) across all nine confounders. AI smoothing predicts `high / high / high / low`; legal_or_policy_memo_style predicts `high / high / high` (concrete-detail not specified — distinguishes from AI on this dimension). Translation/ESL cleanup predicts `low / low` (ESL writers tend toward agent-explicit constructions). House-style enforcement predicts high generic-institutional density. Other confounders mark agency-family signals as `any` until evidence sharpens the matrix.
- **`extract_observations(agency=...)`** new keyword reads `agency_abstraction_audit.py` JSON output and emits the four agency observations using thresholds aligned with the matrix expectations (nominalization ≥ 30 / 1k = high; concrete-detail ≥ 3 / 1k = high; etc.).
- **`analyze_confounders(agency=...)`** keyword passes through to `extract_observations`.
- **Missing-evidence list** grows from 9 to 13 signals (the four agency-family signals join the high-leverage list when they're not observed).
- **CLI** gains `--agency-json` flag.

### Notes

- **739 tests pass + 1 skipped** (was 689+1 in 1.33.0; +50 new tests across `test_agency_abstraction_audit.py` (29 — agency signals + band call + baseline + render + CLI), `test_revision_risk.py` (16 — risk classifier + apply_revision_risk + build_packets integration + markdown rendering), and the `TestAgencyFolding` suite added to `test_confounder_audit.py` (8 — extraction + matrix integrity + sharpening contract)).
- **No breaking changes.** `Packet.revision_risk` defaults `unspecified`; `extract_observations` and `analyze_confounders` accept the new `agency` kwarg with default `None`. Pre-1.34.0 callers continue to work; downstream JSON consumers gain new fields they can ignore.
- **Schedule status: Release 4 shipped.** Per the paired-release schedule (ROADMAP `Interleaving` section), the next release is the Tier-2 promotions bundle: Punctuation Cadence + Stance/Modality + Function-Word Grammar paired with Ablation reports.
- The honesty contract from Release 3 holds: agency-family folding doesn't *resolve* the AI-smoothing-vs-legal-memo differential (both predict the same high-agency-loss pattern). Where agency sharpens the differential is in combination — when char_ngram_delta is observed alongside agency-loss, AI smoothing's predicted high-char_ngram_delta + low-idiolect_survival pattern distinguishes from legal memo style's `any` on those signals. The framework continues to refuse a single-cause verdict when the evidence doesn't entitle one.
- The agency-loss vocabulary (`framework / landscape / dynamic / challenge / opportunity` etc.) is curated, not labeled-corpus-validated. Treat it as a texture signal; the audit's primary value is per-signal density relative to baseline rather than absolute counts.

## [1.33.0] - 2026-05-10

**Paired-release schedule, Release 3: Discourse Move Signature + Confounder Audit / Layer D.** The single most leveraged release in the trustworthiness expansion. The confounder audit promotes the framework's "the math doesn't entitle the verdict" stance from claim-license boilerplate into a formal *differential-diagnosis output* — a ranked list of compatible alternative explanations (professional copyediting, register / genre shift, legal / policy memo style, translation or ESL cleanup, dictation cleanup, house-style enforcement, developmental revision, AI smoothing, intentional voice imitation), none presented as the answer. Per the (1.32.1-narrowed) dependency rule, Discourse Move Signature is the *hard prerequisite* — without typed-discourse evidence the confounder matrix can't separate institutional prose from AI-smoothed prose. Both ship in this release.

### Added — Discourse Move Signature (Surfaces Tier 1)

- **`scripts/discourse_move_signature.py`** — typed discourse-marker classifier + move-sequence n-gram audit. Twelve marker categories: contrast, concession, consequence, elaboration, exemplification, sequencing, reframing, epistemic_stance, boosting, hedging, self_correction, metadiscourse. Per-category density per 1,000 words plus the deeper layer the existing `connective_density` ratio doesn't capture: *which kinds of moves the writer falls into and what sequences they use*. The framework's existing connective-density signal answers "how much scaffolding is present"; this audit answers "what kind."
- **Move-sequence bigrams** count consecutive (move_i, move_{i+1}) transitions across sentences, including an `_unmarked` label for sentences without a marker. Surfaces patterns like `concession→reversal→claim` (essayist) vs. `elaboration→exemplification→consequence` (memo) regardless of how dense the markers are.
- **Move-sequence entropy in bits** (full and marked-only). Low marked-only entropy means scripted argumentative cadence — the writer falls into a narrow set of moves repeatedly. High entropy means rhetorical-need-driven cadence.
- **Compression-fraction band call** (Lightly / Moderately / Heavily *scaffolded*) over five rhythm signals: high total marker density, low marked-move entropy, dense concession-contrast-consequence triad, high metadiscourse density, oscillating hedging-and-boosting. Heuristic thresholds documented as calibration-pending.
- **Optional baseline comparison** computes per-category density z-scores against a baseline directory's aggregate.
- **Structured ClaimLicense block** explicitly refuses an AI-provenance verdict, naming the reality that heavy scaffolding is characteristic of legal/policy memos, academic prose, AI-edited drafts, and well-scaffolded essayists alike — the differential diagnosis of cause is the confounder audit's job.
- **CLI**: `python3 scripts/discourse_move_signature.py INPUT.txt [--baseline-dir DIR] [--json]`.

### Added — Confounder Audit / Layer D (Trustworthiness Tier 1)

- **`scripts/confounder_audit.py`** — Layer D differential diagnosis. Reads existing audit JSON outputs (variance audit, voice distance, paragraph audit, discourse move signature, optionally AIC pattern audit) and runs each observed signal pattern against a **confounder signature matrix** that maps each candidate alternative explanation to expected directions across the signal set.
- **Nine confounders** in the initial matrix: professional_copyediting, register_genre_shift, legal_or_policy_memo_style, translation_or_esl_cleanup, dictation_or_transcription_cleanup, house_style_enforcement, developmental_revision, ai_smoothing, intentional_voice_imitation. Each maps a subset of fourteen tracked signals (sentence_variance, mdd_variance, lexical_diversity, pos_bigram_kl, char_ngram_delta, punctuation_regularity, idiolect_survival, connective_density, aic_pattern_density, paragraph_regularity, discourse_marker_density, marked_move_entropy, register_match, length_localization) to expected directions: `high` / `low` / `any` / `absent` / `uniform` / `localized`.
- **Compatibility scoring**: per-confounder score = `(matches + 0.5 * any_signal_matches) / total`, where matches and contradictions are computed against the observed signal pattern. Not a probability — descriptive ("how many observed signals point in the direction this explanation predicts"). Multiple high-scoring candidates is the **expected output** — the framework refuses to commit to a single cause.
- **Distinguishing-evidence detector** finds observations where the top-ranked candidates disagree on expected direction. Gives the reader the per-signal evidence that most rules in or out each top candidate.
- **Missing-evidence list** names high-leverage signals that were *not* observed in the run, with hints for what the user could supply to sharpen the differential (e.g., "no idiolect_detector output provided", "no sliding-window heatmap data supplied").
- **Graceful downgrade**: the audit accepts any subset of input audit JSONs. Fewer inputs means fewer distinguishing observations, but the output still names the underspecification.
- **Structured ClaimLicense block** explicitly refuses to be a classifier: "the framework's load-bearing claim is that the math doesn't entitle the verdict; this audit is the formal expression of that stance — it surfaces the differential, it does not commit to a cause."
- **CLI**: `python3 scripts/confounder_audit.py --variance-json a.json --discourse-json b.json [--paragraph-json c.json] [--voice-distance-json d.json] [--json]`.

### Notes

- **689 tests pass + 1 skipped** (was 638+1 in 1.32.0; +51 new tests across `test_discourse_move_signature.py` and `test_confounder_audit.py`). Honesty contract pinned by `test_canonical_confounder_pair_both_score_high` — when AI smoothing and legal/policy memo style both predict the same surface pattern, both score ≥ 0.6 and the audit refuses to commit.
- **No breaking changes.** Both modules are new; existing surfaces unchanged.
- **Schedule status: Release 3 shipped.** Per the paired-release schedule, the next release pairs the Agency and Abstraction Audit (Surfaces Tier 1) with the Revision-risk model (Trustworthiness Tier 3). At that point the agency family folds into the confounder matrix as a strengthening complement (per the 1.32.1 narrowed dependency rule).
- The confounder audit's signal vocabulary is intentionally bounded. Signals not yet shipped as separate audits — punctuation cadence, idiolect survival, agency, AIC density at calibrated thresholds — appear in the matrix as design hypotheses that the audit notes as `unobserved` until the corresponding surface lands. This is the right shape for a roadmap-aligned confounder audit: it documents where the framework's evidence gaps are and downgrades gracefully when they're not filled.

## [1.32.1] - 2026-05-10

**Reviewer-flagged P2 docs fixes: two sequencing contradictions in the paired-release schedule.** Two contradictions in the 1.30.4 ROADMAP `Interleaving` section (mirrored in the 1.30.4 CHANGELOG summary) that future implementers would have hit if they treated the roadmap as an implementation queue.

### Fixed

- **Confounder-audit dependency rule contradicted the schedule.** The 1.30.4 dependency rule said "Discourse Move Signature *and* Agency and Abstraction Audit ship before the confounder audit gets its first useful version," but the schedule placed the Confounder Audit in Release 3 (paired with Discourse) and Agency in Release 4 — so the confounder audit shipped before its supposed prerequisite. Rule narrowed: **Discourse Move Signature is the hard prerequisite** (the confounder matrix needs typed-discourse evidence to distinguish institutional prose from AI smoothing). **Agency and Abstraction Audit is a strengthening complement** that lands in Release 4, at which point the confounder matrix gains the agency-loss family and sharpens its differential diagnosis. Without agency, the confounder audit's first useful version is still useful — just less sharp on the agency dimension. Fix lands in both ROADMAP.md `Interleaving` section and the 1.30.4 CHANGELOG summary so future readers don't see the contradiction in either place.
- **Adversarial track was both prerequisite and later release item.** The Interleaving section said the paired schedule starts after the adversarial-class track has shipped, but Release 7 still listed an "Adversarial / paraphrase stress harness" as a later guardrail. Distinction now made explicit: pre-schedule work is **fixture acquisition** (DIPPER paraphrases, humanizer-tool outputs) plus the existing `validation_harness.py`'s ROC-AUC / AP slicing across those classes — i.e., the harness can already evaluate per-fixture-class performance using the existing report shape. Release 7's contribution is the **per-signal robustness-card output shape** — the "this signal collapses under paraphrase but survives copyediting" reporting layer over the already-acquired fixtures. Both ROADMAP and the 1.30.4 CHANGELOG summary now name the fixtures-vs-output-shape distinction.

### Notes

- Docs-only release. **638 tests pass + 1 skipped** (no test changes from 1.32.0).
- Both fixes preserve the existing release order. The narrower confounder-audit rule keeps Release 3 as the confounder audit's first useful version (with discourse evidence alone) and Release 4 as the strengthening complement that lands the agency family.
- The adversarial-track distinction explains a fact that was already implicit in the framework: the existing `validation_harness.py` from 1.x is *already* the reporting layer for ROC AUC / AP per slice; what's missing is the per-signal robustness card, which is a different output-shape extension. The pre-schedule fixture-acquisition work and the Release-7 output-shape work are independently shippable.

## [1.32.0] - 2026-05-10

**Paired-release schedule, Release 2: Paragraph Architecture Audit + Source-of-smoothing localization.** Second release implementing the paired-release schedule (1.30.4). Per the schedule, Release 2 pairs a Surfaces Tier-1 tool (Paragraph Architecture Audit) with a Trustworthiness Tier-3 guardrail (Source-of-smoothing localization in the sliding-window heatmap). The pairing is dependency-driven: paragraph-level signals land alongside the extension that makes the heatmap's hot zones interpretable beyond "where does the band fire" into "what kind of smoothing is happening here."

### Added — Paragraph Architecture Audit (Surfaces Tier 1)

- **`scripts/paragraph_audit.py`** — paragraph-level rhythm diagnostic. The shipped Layer A suite measures distributional compression at the *sentence* and *token* layers and the sliding-window heatmap localizes those signals across word positions; what's structurally missing is paragraph-level rhythm. AI editing, professional copyediting, and institutional house style frequently produce **competent rectangle paragraphs** — similar paragraph lengths, similar opening shapes, similar terminal sentences, low macro-rhythm. Sentence-level signals don't see this; word-windowed heatmaps don't see it either.
- **Six paragraph-rhythm signals**:
  - Paragraph length distribution (mean / sd / coefficient-of-variation / p5 / p25 / p50 / p75 / p95).
  - Paragraph length variance (the "regularized rectangles" signal — fires when CV < 0.40).
  - One-sentence paragraph rate (low rate = absence of one-sentence rhetorical breaks).
  - Punchy-ending rate (per-paragraph fraction of paragraphs ending in a short final sentence after a longer body).
  - Median first-to-body sentence length ratio.
  - Long-paragraph clustering (3+ consecutive paragraphs above the document's 75th-percentile length).
- **Opening typology classifier** assigns each paragraph one of seven categories: declarative / question / quoted / fragment / conjunction-led / proper-noun-led / imperative. Closing typology assigns one of six: declarative / question / quoted / fragment / list-or-colon-trailed / aphoristic. **Opening / closing entropy** in bits — low entropy means uniform openings ("competent rectangle" prose); high entropy means rhetorical-need-driven cadence.
- **Compression-fraction band call** (Lightly / Moderately / Heavily smoothed) over six rhythm signals: low-CV, low-one-sentence-rate, low-punchy-rate, low-opening-entropy, low-closing-entropy, dominant-long-cluster. Heuristic thresholds documented as calibration-pending.
- **Baseline comparison**: when `--baseline-dir` is supplied, runs the same signals across baseline files, reports per-signal z-scores and Manhattan-distance between target and baseline opening/closing typology distributions.
- **Structured `claim_license` block** via `claim_license.py` — explicitly refuses an AI-provenance verdict, names the differential-diagnosis problem (the same regularized-paragraph signature can come from professional copyediting, institutional house style, policy-memo templates, translation cleanup, or AI editing).
- **Privacy posture**: paragraph audits emit no raw text, only structural metrics + typology counts. Per-paragraph entries carry only `index`, `n_words`, `n_sentences`, `opening`, `closing`. Safe for public reports.
- **CLI**: `python3 scripts/paragraph_audit.py INPUT.txt [--baseline-dir DIR] [--json] [--strip-masking PROFILE]`. Honors `--strip-masking` from 1.31.0.

### Added — Source-of-smoothing localization (Trustworthiness Tier 3)

- **Hot-zone phenomenon classifier** in `sliding_window_heatmap.py`. Each hot zone in the heatmap now carries a `phenomenon` label classifying which family of signals dominates the firing pattern:
  - `syntactic_flattening` — sentence-rhythm signals (`burstiness_B`, `sentence_length_sd`, `fkgl_sd`, `mdd_sd`).
  - `lexical_compression` — diversity / entropy signals (`mtld`, `mattr`, `shannon_entropy`, `yules_k`).
  - `over_cohesion` — adjacent-cosine signals (`adjacent_cosine_mean`, `adjacent_cosine_sd`).
  - `connective_overuse` — `connective_density`.
  - `mixed_smoothing` — multiple families fire roughly equally (no family ≥ 60% share).
  - `unclassified` — too few or too sparse signals to classify.
- **Dominance threshold**: a single family at ≥ 60% share of the zone's flagged-signal pool wins the phenomenon label; otherwise the zone is `mixed_smoothing` rather than committing to a single cause.
- **`phenomenon_evidence`** field on each `HotZone`: human-readable family-by-family breakdown of which signals contributed (e.g., `"syntactic_flattening: burstiness_B (3/5), sentence_length_sd (2/5)"`).
- **Markdown rendering** in the heatmap report's hot-zones section now annotates each zone with its phenomenon label: "Heavily smoothed at words 1500–2500 (windows 5–6, fraction 0.50–0.55); phenomenon: **syntactic flattening**; dominant signals: ..."
- **JSON output** carries `phenomenon` and `phenomenon_evidence` per hot zone alongside the existing fields. Backward compat: existing JSON consumers reading `band` / `start_word` / `end_word` / `n_windows` / `dominant_signals` continue to work.
- **Polarity-inversion caveat** documented in the classifier's docstring: per the 1.27.0 finding, five lexical-diversity signals invert against ESL student writing — the `lexical_compression` label may be misleading on ESL comparators. The confounder audit (roadmap, paired-release Release 3) is the right surface to disambiguate.

### Notes

- **638 tests pass + 1 skipped** (was 586+1 in 1.31.0; +52 new tests across `test_paragraph_audit.py` and the phenomenon-classification suite in `test_sliding_window_heatmap.py`).
- **No breaking changes.** Paragraph audit is a new tool; the heatmap's existing JSON fields are unchanged (the new `phenomenon` / `phenomenon_evidence` fields are additive).
- **Schedule status: Release 2 shipped.** Per the paired-release schedule (ROADMAP `Interleaving` section), the next release pairs the Discourse Move Signature (Surfaces Tier 1) with the Confounder Audit / Layer D (Trustworthiness Tier 1). Discourse-typed markers are a *prerequisite* for the confounder audit's differential-diagnosis output, not a complement — the confounder audit can't distinguish "legal/policy memo" from "AI smoothing" without typed-discourse evidence.
- The paragraph audit catches the failure mode the rest of the Layer A suite is structurally blind to. AI editing, professional copyediting, and institutional house-style enforcement frequently produce regularized-rectangle paragraphs while leaving the writer's lexical diversity, sentence variance, and POS-bigram footprint inside human bounds. Sentence-level signals miss this; the heatmap's word-windowing misses this. The audit's primary value isn't replacing existing surfaces but adding the macro-rhythm dimension they don't measure.
- The phenomenon classifier's six-label taxonomy is intentionally coarse. A finer taxonomy (e.g. distinguishing "agent suppression" from "lexical generalization" within `lexical_compression`) requires the Surfaces Tier-1 Agency and Abstraction Audit (paired-release Release 4) to provide the input signals. Coarse-now-finer-later is the right interleaving.

## [1.31.0] - 2026-05-10

**Paired-release schedule, Release 1: input-layer infrastructure (stylometric masking profiles + register / genre conditioning).** First release implementing the paired-release schedule announced in 1.30.4. Per the schedule, Release 1 is **precondition work**: it ships without a paired tool because masking and register-conditioning are infrastructure that makes every existing and future call more trustworthy. Two pieces, both opt-in by default to preserve byte-identical pre-1.31.0 behavior.

### Added — masking profiles (Trustworthiness Tier 1)

- **Six new masking rules** in `preprocessing.py` as a third tier alongside `PREPROCESSING_RULES` (corpus-hygiene contamination) and `AGGRESSIVE_RULES` (URL noise / footnotes / citations). Distinct purpose: masking rules remove *prose* that isn't the writer's voice (quoted statutes, block quotations, headings, common LLM wrapper phrases, prompt remnants) rather than non-prose contamination. Opt-in only — these rules are aggressive enough that running them on a normal essay would over-strip.
  - `markdown_heading` — ATX (`#` through `######`) plus setext (`Title\n=====` / `Title\n-----`).
  - `block_quote` — markdown blockquote runs (`> `), conservatively contiguous-only.
  - `long_inline_quotation` — double-quoted passages of 50+ characters between quotes (≈ 8+ words, the empirical floor where short phrase-borrowing ends and quoted speech begins). Conservative on smart quotes; doesn't cross line boundaries.
  - `statutory_citation` — U.S.C. references, Pub. L., Fed. R., section markers (`§`), and case names with `v.` / `vs.`.
  - `llm_wrapper_phrase` — opening / closing apologetic-or-meta boilerplate (`As an AI language model...`, `I cannot provide...`, `I hope this helps`, `Let me know if...`, `Please feel free...`). Pattern requires sentence-or-paragraph boundary so mid-prose mentions of "AI" in discussions ABOUT AI aren't clobbered.
  - `prompt_remnant` — leading-prompt patterns at document start (`Please write...`, `You are a...`, `Act as a...`, `System: ...`).
- **Five masking profile presets** for routing through `--strip-masking`:
  - `none` — no masking applied (default; identical to pre-1.31.0).
  - `prose_body_only` — headings + block quotes + long inline quotes + statutory citations. For policy / legal / testimony / academic prose.
  - `exclude_quotations` — block quotes + long inline quotations.
  - `exclude_headings` — markdown headings only.
  - `prose_strict` — every masking rule. Conservative for analytical comparison; over-strips when used as default.
- **`strip_non_prose(strip_masking=...)` parameter** accepts profile preset name, comma-separated rule list (`"markdown_heading,block_quote"`), or iterable of names. Resolved by new `resolve_masking_rules()`. Metadata records active masking rules and `tokens_masked` count separately from the existing `tokens_stripped_by_rule`.
- **`available_masking_rules()` and `available_masking_profiles()`** public introspection helpers.

### Added — register / genre conditioning (Trustworthiness Tier 1)

- **`scripts/register_classifier.py`** — heuristic register detection over the manifest's canonical taxonomy (`blog_essay`, `personal_essay`, `literary_fiction`, `commercial_fiction`, `literary_horror`, `academic_philosophy`, `academic_general`, `legal_memo`, `policy_memo`, `policy_advocacy`, `testimony_policy`, `journalism`, `marketing`, `newsletter`, `report_prose`, `social_thread`, `email`, `unknown`). Lightweight signal-driven classifier (no machine learning, no labeled corpus dependency) — primary value is honest claim-licensing rather than classification accuracy.
- **Per-register scorers** built from sub-scores over interpretable signals: heading density, first-person density, second-person density, dialogue ratio, question / exclamation density, inline-citation density, statutory citation density, formal-address patterns (`Mr. Chairman`, `the Committee`, `Honorable`), legal voice (`shall`, `pursuant to`, `notwithstanding`), attributed-quote density (`according to X`, `said X`), imperative-open patterns, past-tense narrative verbs, academic voice (`we argue`, `this paper`).
- **`classify_register(text, hint=None, min_words=100)`** returns `{primary, confidence, secondary, scores, evidence, warning}`. Primary is the highest-scoring register; confidence is the score in `[0, 1]`; secondary lists registers within 0.10 of primary; below `min_words` or with confidence under 0.30 the classifier returns `"unknown"` rather than committing to a noisy call. Optional `hint` provides a small score nudge when the user knows the register but wants confirmation.
- **`register_match(target_register, baseline_registers)`** returns `{strength, rationale, target, baseline_distribution}` where strength is one of `strong` (≥80% of baseline matches target), `moderate` (≥50%), `weak` (≥20%), or `mismatch`. Each strength carries a human-readable rationale naming the actual mismatch (`baseline is dominantly legal_memo (3/4); reading any cross-register voice distance as voice drift is unsafe`).
- **`render_register_match_block(match)`** for embedding the result in claim-license blocks.
- **`voice_distance.py` integration**: when comparing target vs. baseline, the result dict gains a `register_match` block with target classification + match strength + rationale. The markdown report surfaces it before the headline Delta band, with a ⚠️ marker on `weak` / `mismatch` strengths so the reader sees the register caveat before the number. JSON output unchanged in shape — the new block is additive.

### Notes

- **586 tests pass + 1 skipped** (was 534+1 in 1.30.4; +52 new tests across `test_masking_profiles.py` and `test_register_classifier.py`).
- **No breaking changes.** `strip_non_prose()` accepts `strip_masking` as a new keyword-only parameter that defaults to `None` (no masking) — every existing call site works byte-identically. `voice_distance.py` adds a `register_match` block to its result and a register-match line to its markdown report; both are additive. The `claim_license` JSON dict shape is unchanged.
- **Schedule status: Release 1 shipped.** Per the paired-release schedule (ROADMAP `Interleaving` section), the next release pairs the Paragraph Architecture Audit (Surfaces Tier 1) with Source-of-smoothing localization in the sliding-window heatmap (Trustworthiness Tier 3). The masking capability shipped here is library-only — surface integrations follow as they naturally pair with new tools.
- The register classifier is intentionally heuristic. It is not a labeled-corpus-validated machine-learning model and it shouldn't become one — the framework's value is in honest claim-licensing, not in pretending to classify register at academic-paper accuracy. Below 100 words the classifier refuses; below 0.30 confidence it returns `unknown`. These floors are deliberate.

## [1.30.4] - 2026-05-10

**Roadmap interleaving: paired-release schedule across the two expansion tracks (new tools + new guardrails).** Releases 1.30.2 and 1.30.3 added two roadmap expansion sections — surface expansion (twenty stylometric surfaces) and trustworthiness expansion (twenty failure-mode-control extensions). Each section had its own internal tier ordering. What both sections left open: the *interleaving* question — should the surface track ship in full before any guardrail work, vice versa, or alternate? Building either track in isolation produces predictable failure modes. This docs-only release adds an `Interleaving: paired-release schedule` section to ROADMAP.md that resolves the question with a concrete cross-track sequence.

### Changed

- **`ROADMAP.md`** gains an `Interleaving` section between the two expansion-track sections and the existing "Open architectural questions" section. Two dependency rules drive the sequence:
  1. **Input-layer guardrails ship before any new tool depends on them.** Stylometric masking profiles + register/genre conditioning are precondition work — they make every existing and future call more reliable, and they ship as their own release without a paired tool.
  2. **Discourse Move Signature is a hard prerequisite for the Tier-1 confounder audit.** The confounder audit's differential diagnosis needs typed-discourse evidence to distinguish institutional prose (legal / policy / testimony) from AI smoothing — without typed markers the matrix can't separate "concession-and-elaboration patterns from policy memo style" from "concession-and-elaboration patterns from AI smoothing." Discourse ships in Release 3 paired with the confounder audit's first useful version. Agency and Abstraction Audit (Release 4) is a *strengthening complement*: it adds the agency-loss family to the confounder matrix and sharpens the differential diagnosis, but is not a hard prerequisite.
- **Proposed paired-release sequence** (twelve releases plus a Tier-4 research bundle, each with one new tool and one new guardrail or precondition guardrail work alone, releasable independently):
  - Release 1: input-layer guardrails (masking + register conditioning); no paired tool — preconditions ship alone.
  - Release 2: Paragraph Architecture Audit + Source-of-smoothing localization (paragraph-level signal feeds the heatmap's "what kind of smoothing" classifier).
  - Release 3: Discourse Move Signature + Confounder audit / Layer D (typed discourse markers feed the confounder matrix; tool is prerequisite for guardrail).
  - Release 4: Agency and Abstraction Audit + Revision-risk model (agency-loss signals pair with per-suggestion risk labels in `restoration_packet.py`).
  - Release 5: Punctuation Cadence + Stance/Modality + Function-Word Grammar (three Tier-2 promotions) + Ablation reports (more feature families need an interpretability mechanism).
  - Release 6: output-discipline release — Minimum evidentiary conditions gate + Negative/positive controls; no paired tool.
  - Release 7: interpretation meta-layer — Surface-disagreement resolver + adversarial **robustness-card output shape** (the per-signal "this signal collapses under paraphrase but survives copyediting" reporting layer over the fixtures, which were acquired in the pre-schedule adversarial-class track); no paired tool.
  - Release 8: Construction Signature Audit + Semantic preservation check (interpretable syntactic evidence pairs with claim/entity/stance preservation guardrails).
  - Release 9: validation infrastructure — Calibration drift monitor + Fairness / dialect / multilingual guardrails; no paired tool.
  - Release 10: Mimicry / Style-Cosplay Audit + Known-editor profile (both address "smoothed-but-by-whom" from different angles).
  - Release 11: Phraseological Signature Audit + Draft-history analysis (phrase-frame mining + version-aware analysis).
  - Release 12: Semantic Trajectory Audit (heaviest dependency footprint — SBERT-class — ships when the framework adopts that posture); from here forward releases get less paired and more research-driven.
  - Release 13+: Tier-4 research items on both tracks — Counterfactual editing sandbox, House-style decomposition, Multi-author segmentation, Transformation-profile learning — each independently shippable, none on a near-term schedule.
- **Anti-pattern check.** The single most-damaging anti-pattern this schedule resists is shipping new tools without their interpretive guardrails, which would systematically grow the framework's surface area for false confidence. Every tool release in the sequence lands with either (a) an existing guardrail it strengthens, (b) a new guardrail that makes it interpretable, or (c) precondition guardrail work having already shipped in an earlier release.

### Notes

- Docs-only release. **534 tests pass + 1 skipped** (no test changes from 1.30.3).
- The schedule deliberately doesn't commit to calendar; the order is the commitment, not the timing. Each release is independently shippable. The framework's release cadence depends on the calibration-breadth track's progress and on user demand for specific surfaces.
- The schedule doesn't replace the per-track tier orderings in the Surfaces and Trustworthiness sections; it sequences *across* the two tracks. If the framework ever needs to deviate (e.g., a specific surface gets pulled forward by user demand), the per-track priority tells you what's safe to skip; the paired-release rationale tells you what dependency is broken if you do.
- The 2.0 refactor target (Compression-of-Choice / Stylistic Choice Entropy) sits beyond this entire schedule. When 2.0 lands, every existing surface gets rewritten as a special case of compression in some choice set, and the trustworthiness layer gets reframed as compression-aware (the confounder audit becomes "differential diagnosis across choice-set perturbations" rather than across signal directions). That's an architectural rewrite, not a release.
- This is the third docs-only release in a row (1.30.2 surfaces, 1.30.3 trustworthiness, 1.30.4 interleaving). Together they triangulate the framework's mid-term direction without committing implementation work — the calibration-breadth track and the existing adversarial-class fixtures remain the immediate roadmap foreground; the paired-release schedule kicks in after.

## [1.30.3] - 2026-05-10

**Roadmap expansion: trustworthiness layer (twenty failure-mode-control extensions tiered with honest priority).** A second reviewer-track contribution surfaced a list of additions that are not new metrics but failure-mode control, interpretability, adversarial realism, and user workflow discipline — the parts that stop a sophisticated stylometric tool from becoming a numerically impressive overclaimer. Where the previous expansion (`Stylometric surface expansion`, 1.30.2) catalogues *new things to measure*, this docs-only release adds a `Trustworthiness expansion` section to ROADMAP.md that catalogues *how to use what is already measured responsibly*. Twenty items tiered into four build groups plus an explicit anti-goals section.

The framework's existing surfaces already encode much of this discipline at the per-surface level — `task_surface` tags, `claim_license` blocks, ESL ratchets in `manifest_validator.py`, gray-zone refusals in the General Imposters harness, the metric-targetability taxonomy in `restoration_packet.py`. The trustworthiness expansion is the *systematization* of that discipline — promoting it from per-surface convention into a shared interpretive layer.

### Changed

- **`ROADMAP.md`** gains a `Trustworthiness expansion` section organizing twenty failure-mode-control additions into four tiers plus explicit anti-goals plus a build order. Architectural framing: the additions form a layered discipline — input layer (masking, register gate, multilingual caution), core layer (existing surfaces unchanged), interpretation layer (confounder audit, surface-disagreement resolver, source-of-smoothing localization, ablation reports), output discipline (evidentiary conditions gate, claim license, controls), validation layer (adversarial harness, calibration drift, fairness guards), author-facing layer (revision risk, semantic preservation, draft history, known editor), research layer (counterfactual sandbox, multi-author segmentation, transformation-profile learning, house-style decomposition).
  - **Tier 1 — Trustworthiness upgrades (5)**: Confounder audit / Layer D (the single most leveraged addition — promotes the framework's "the math doesn't entitle the verdict" stance from claim-license boilerplate into a formal differential-diagnosis output); Register / genre conditioning (operationalizes the "matched register" claim that the claim-license block already makes); Stylometric masking profiles (extends `check_corpus.py` from corpus-hygiene gate into selectable analytical-pass masking modes — quotes, citations, headings, statutory language, prompt remnants); Minimum evidentiary conditions gate (a single front-door evidentiary-posture label — revision-only / exploratory / internal-triage / research-grade / forensic-adjacent — issued before any claim is composed); Surface-disagreement resolver (meta-layer that interprets cross-surface disagreement patterns).
  - **Tier 2 — Validation upgrades (5)**: Adversarial / paraphrase stress harness with **robustness cards** as the deliverable shape (already on the validation harness's adversarial-class roadmap; the contribution here is the per-signal output card); Negative / positive controls in reports; Ablation reports (leave-one-feature-family-out, distinguishes rhythm-driven smoothing from global smoothing); Calibration drift monitor (regression-test suite using fixed benchmark texts; detects threshold movement after spaCy / parser / corpus updates); Fairness / dialect / multilingual guardrails (promotes the manifest's ESL ratchet into a visible report-level caution surface).
  - **Tier 3 — Writer-facing upgrades (5)**: Revision-risk model (extends the metric-targetability taxonomy in `restoration_packet.py` with per-suggestion low/medium/high risk labels for idiolect erasure, metric gaming, humanizer artifacts, etc.); Source-of-smoothing localization (extends `sliding_window_heatmap.py` from "where does the signal fire?" to "what *kind* of smoothing is happening there?"); Semantic preservation check (extends `before_after_restoration.py` with claim inventory, named-entity, citation, stance, modality, hedge preservation); Draft-history analysis (version-aware stylometry — when did the smoothing enter, was it gradual or sudden, which revision introduced the drift); Known-editor profile (learns editorial transformation patterns from before/after pairs; distinguishes "this was smoothed" from "this was smoothed in the ordinary way this editor smooths this writer").
  - **Tier 4 — Advanced research / product layer (5)**: House-style vs. author-voice decomposition (nested baselines for institutional writers); Multi-author / multi-source segmentation (window-level clustering catches rewritten chapters / multi-staff drafting / AI-assisted inserts); **Counterfactual editing sandbox** (the most ambitious addition — generate same-meaning variants under controlled perturbations and use them *diagnostically*, not as final rewrites; tells the user what the tool actually thinks is causing the signal; research-grade work, requires meaning-preserving rewrite component); Transformation-profile learning (general version of known-editor); Interactive report UI (indefinitely deferred unless the framework adopts a UI layer).
  - **Explicit anti-goals**: No single "authenticity score." No "% AI-edited" dosage estimate (consistent with the 2026-05-08 finding that POS-bigram KL doesn't grade dosage within the post-AI cohort). No model-attribution module (fragile under model drift). No metric-optimizing rewrite engine (would create the artifact the framework critiques). No disciplinary report template.
- **Trustworthiness build order** committed at the section's end: 15 items in dependency-aware sequence, with masking-and-register-gate first (input layer), confounder audit and surface-disagreement resolver next (interpretation), then evidentiary gate (output discipline), then validation infrastructure, then writer-facing extensions. Tier 4 ships on a longer horizon.

### Notes

- Docs-only release. **534 tests pass + 1 skipped** (no test changes from 1.30.2).
- The shortest formulation of the difference: the shipped suite measures "what does this text look like stylometrically?" The trustworthiness layer answers "compared to which legitimate alternatives, under what evidentiary conditions, with what confounders, and what revision moves would improve the prose without gaming the instrument?" That's the difference between a detector-shaped tool and a serious writing-forensics / voice-preservation system.
- The two roadmap expansions (1.30.2 surfaces; 1.30.3 trustworthiness) are independent and largely non-overlapping. The surface expansion is about extending what's measured at the analytical layer; the trustworthiness expansion is about extending the interpretive discipline around any measurement. Surface work proceeds layer-by-layer (paragraph, discourse, agency); trustworthiness work proceeds layer-by-layer (masking, register, confounder, evidentiary). Either track can run independently of the other.
- Neither track is on a near-term schedule. The calibration-breadth track (RAID + MAGE corpus fetchers, more calibrated thresholds, polarity-inversion correction against a fluent-native corpus) and the existing adversarial-class track (paraphrase + humanizer fixtures for the validation harness) remain the immediate roadmap foreground. Surface and trustworthiness expansions ship after.

## [1.30.2] - 2026-05-10

**Roadmap expansion: twenty candidate stylometric surfaces tiered with honest priority.** A reviewer-track contribution surfaced an extensive list of additional stylometric surfaces drawn from classical writeprint research (lexical / syntactic / structural / content-specific / idiosyncratic feature families) and recent stylometry surveys (with the persistent challenges around genre, topic leakage, short texts, and forensic reliability). Rather than treat the list as a build queue, this docs-only release adds a `Stylometric surface expansion` section to ROADMAP.md that catalogues each candidate with an honest priority — including the ones I'd indefinitely defer or explicitly *not* ship as voice surfaces.

### Changed

- **`ROADMAP.md`** gains a `Stylometric surface expansion` section organizing twenty candidate surfaces into six tiers:
  - **Tier 1 — Near-term builds** (concrete next picks): Paragraph Architecture Audit, Discourse Move Signature, Agency and Abstraction Audit. Each testable, no new dependencies, lands at a layer the existing suite is structurally blind to.
  - **Tier 2 — Promotions** (already partly there): Punctuation Cadence Audit (currently feature columns in `voice_profile.py`), Stance / Modality / Epistemic Posture Audit (currently in pronoun-modal-negation cluster at frequency level only), Function-Word Grammar Surface (currently used at frequency level via Burrows Delta).
  - **Tier 3 — Substantive new surfaces** (post-Tier-1, design before code): Construction Signature Audit, Mimicry / Style-Cosplay Audit (gated on restoration maturity), Phraseological Signature Audit (extension of `idiolect_detector.py`), Semantic Trajectory Audit (gated on dependency-footprint posture; SBERT-class).
  - **Tier 4 — Specialized / fiction-specific extensions**: Dialogue-Specific Voice Audit, Narratorial Distance / Free Indirect Audit, Productive Roughness Audit (with strict baseline-relative-only constraint frontloaded).
  - **Tier 5 — Adjacent surfaces** (real signals; topic-bound or format-bound enough that calling them voice would muddy claim language): Document Structure / Layout Audit, Reference Ecology Audit, Allusion / Quotation Habit Surface, Stockness / Formulaicity Audit.
  - **Tier 6 — Indefinitely deferred**: Dependency-Tree Shape and Subtree Motifs (modest gain over existing POS-trigram + dependency-n-gram surfaces; same opacity problem as POS-trigram KL); Morphological Texture Audit (heavily correlated with register / education / topic, not voice); Figure-of-Speech Expansion beyond the current AIC set (current AIC inventory already covers AI-prose-relevant figures; better to deepen than broaden).
  - **2.0 refactor target**: Compression-of-Choice / Stylistic Choice Entropy. The deepest theoretical extension on the list. The framework currently measures variance compression in *outputs* (sentence length, lexical diversity, POS-bigram distribution); the more honest object of measurement is variance in the writer's *choice architecture* (which connective among alternatives, which clause-combining strategy, which actor-reference strategy). Built well, this surface would generalize every existing audit — each becomes a special case of "compression in some choice set." Treated as the architectural horizon for 2.0, not a v1 deliverable, since defining defensible choice sets is a curatorial problem (not a coding one) and refactoring existing surfaces on top of the choice-entropy primitive would be a public-API breaking change consistent with a major version bump.
- **Build order section** at the end of the new ROADMAP section pins the concrete near-to-mid-term commitments: Tier 1 (3) → Tier 2 promotions (3) → Tier 3 substantive (4). Tier 4 specialized surfaces ship when their domains pull on them, not on the cathedral schedule. Tier 5 adjacent surfaces ship as separate non-voice surfaces with explicit claim-language guards. Tier 6 items are not commitments.

### Notes

- Docs-only release. **534 tests pass + 1 skipped** (no test changes from 1.30.1).
- The deferral and tier-5-adjacent decisions are deliberate. Reference ecology and allusion habit are real stylometric signals, but they're heavily topic-bound — and the framework's foundational claim is that topic ≠ style. Stockness has both a phrase-list-maintenance commitment the framework doesn't have a model for and a structural risk of becoming a Pangram-style classifier wearing stylometric clothes (the framework's anti-goal). Dependency-tree shape and morphological texture are real signals at modest interpretability gain over what's already shipped. Figure-of-speech expansion would broaden the AIC inventory without changing claim-shape.
- The Tier 1 / Tier 2 commitments add up to roughly six new audits when the toolchain returns to surface expansion. The Tier 3 substantive surfaces add another four with meaningful methodology-pinning work each. None of this is on a near-term schedule; the calibration-breadth track (RAID + MAGE corpus fetchers, more calibrated thresholds, polarity-inversion correction against a fluent-native corpus) and the adversarial-class track (paraphrase + humanizer fixtures for the validation harness) remain the immediate roadmap foreground.

## [1.30.1] - 2026-05-10

**Reviewer-flagged P2 fixes in the new `voice_distance --bootstrap` path.** All three issues land in the bootstrap surface introduced in 1.30.0 — the spine completion ships infrastructure that's right but the wiring had three holes a careful reviewer caught. Each one quietly corrupts a downstream claim, none failed tests because the existing tests used synthetic fixtures rather than real helper output. This release hardens the reads-real-output path.

### Fixed

- **Bootstrap ignores corpus-hygiene preprocessing.** `voice_distance.bootstrap_compare()` fed raw `target_text` and re-read raw baseline files into the function-word vector. The main `compare_to_baseline()` path strips CSS / HTML / footer artifacts; the bootstrap path didn't, so a contamination that the main report cleaned up still contributed to the bootstrap percentile — the headline Burrows Delta and the bootstrap percentile would be measured on different texts. Fix: `bootstrap_compare()` now takes `allow_non_prose`, `strip_rules`, `strip_aggressive` and applies `strip_non_prose` to both target and every baseline text before vectorization. The CLI threads the existing `--allow-non-prose` / `--strip-rules` / `--strip-aggressive` flags through. `config` records the choices so the JSON output documents what was stripped. Two new tests in `TestPreprocessingThreaded`: a CSS-contaminated target measured with stripping is closer to baseline than the same target measured with `--allow-non-prose`; the config block records the user's choice.
- **Bootstrap table reads the wrong distribution shape.** The 1.30.0 markdown formatter read top-level `min`, `p05`, `p25`, etc., but `length_matched_bootstrap()` returns `{n, quantiles: {p5, p25, p50, p75, p95}, mean, sd}` — quantile keys are `p5` not `p05`, nested under `quantiles`, and there's no `min` / `max`. Result: every distribution cell rendered as `n/a` against real helper output. Fix: new `_dist_value(bd, key)` helper reads the nested `quantiles` shape (with a flat-shape fallback for forward-compat); the table header drops the never-populated `min` / `max` columns and uses the actual `p5` / `p25` / `p50` / `p75` / `p95` / `mean` / `sd` / `n` keys. The 1.30.0 fixture-based formatter test that masked the bug now uses the real shape and asserts numeric cells render. Three new tests in `TestNestedDistributionShape`: real-helper-output formatter pass (zero-tolerance for `n/a` flooding), `_dist_value` reads nested quantiles, defensive flat-shape fallback.
- **Degenerate bootstrap CI can exclude the point estimate.** `length_bootstrap.bootstrap_percentile`'s BCa fallback path could return non-finite bounds without raising — SciPy emits a `RuntimeWarning` + a `DegenerateDataWarning` and returns NaN. The downstream clamp `max(0.0, min(1.0, NaN))` turned NaN into `1.0` (or `0.0` depending on argument order), producing nonsense CIs. Reproduced: `percentile=0.5` with `CI=[1.0, 1.0]` on a constant-statistic sample. Fix: a new `_bounds_acceptable(lo, hi)` predicate checks finiteness, ordering, [0, 1] range, and point-containment; bounds that fail are treated like a raised exception, falling through to the percentile method, then to a final degenerate-no-CI marker that returns `[point, point]` with `method="degenerate_no_ci"` rather than risking a misleading CI. Three new tests in `TestDegenerateBootstrapCI`: constant-sample case (no NaN-clamped bounds), target-outside-sample case (`[point, point]` on extreme percentiles), and a randomized invariant test asserting that any reported CI is finite and contains the point estimate across 15 random sample / target combinations.

### Notes

- **534 tests pass + 1 skipped** (was 526+1 in 1.30.0; +8 new regression tests, –0 changed elsewhere). All three reviewer reproductions are now pinned by tests.
- **No CLI breaking changes.** `bootstrap_compare()`'s new preprocessing kwargs default to the same behavior the old code had (the old code didn't strip; the new defaults strip per the conservative ruleset, which is what the rest of voice_distance has always done — bringing the bootstrap into alignment with the headline). The shape of the JSON output's `length_matched_bootstrap.baseline_distribution` block is unchanged (still `{n, quantiles, mean, sd}`); only the markdown formatter learned to read it correctly.
- The `bootstrap_percentile` change tightens an existing helper used by both `voice_distance` and `variance_audit`. The `variance_audit --bootstrap` output also benefits — pre-1.30.1 a degenerate signal could surface a misleading [1.0, 1.0] CI; now it surfaces a `degenerate_no_ci` marker.

## [1.30.0] - 2026-05-10

**Phase 1 spine completed.** The validation spine's six structural steps — manifest validation, surface-tagged outputs, length-matched bootstrap, the validation harness, the structured "what this licenses" block, and POS-bigram KL participation in the band call — were all shipped or partly shipped across 1.0.0 → 1.29.1. Two pieces remained: voice_distance had no length-matched bootstrap (variance_audit had it via `--bootstrap`; voice_distance answered "is this Delta large?" with no calibrated reference), and three older harnesses still rendered freeform `CLAIM_LICENSE` dicts in their markdown reports despite the structured `ClaimLicense` helper being available since 1.29.0. This release closes both, completing the spine end-to-end.

### Added

- **`voice_distance.py --bootstrap` mode (Phase 1 step 3 finisher).** New `bootstrap_compare(target_text, baseline_entries, ...)` runs a length-matched bootstrap on the function-word distance: sample length-N windows from baseline files, compute each window's L1 distance from its function-word relative-frequency vector to the baseline corpus's mean function-word vector, build the empirical distribution at the target's word count, and report the target's percentile in that distribution with a SciPy BCa confidence interval. Replaces the unanchored "is this Delta 1.2 large?" question with a calibrated "your draft sits at the 88th percentile of within-baseline scatter at this length, 95% CI [82%, 92%]." Why function-word L1 rather than full Burrows Delta: the heavier statistic re-extracts every feature family (POS bigrams, char n-grams, dependency n-grams) per window, costing minutes per window with SpaCy on; the function-word vector is the load-bearing piece of the Delta machinery, computes in milliseconds per window, and produces a directly-interpretable percentile. Per-family Burrows Delta bootstrap remains a follow-up that requires a feature-extraction caching path in `stylometry_core`. New CLI flags: `--bootstrap`, `--bootstrap-windows-per-file` (default 10), `--bootstrap-max-windows` (default 200), `--bootstrap-resamples` (default 9999), `--bootstrap-confidence` (default 0.95), `--bootstrap-seed`. Output dict shape mirrors `variance_audit`'s `bootstrap_compare`: target_value, target_n_words, baseline_distribution percentiles, bootstrap percentile + CI, config. Markdown report grows a "Length-matched bootstrap" section with the percentile, CI, and the empirical baseline distribution (min / p05 / p25 / p50 / p75 / p95 / max / mean / sd).
- **15 new regression tests** in `test_voice_distance_bootstrap.py`: function-word vector basics (dict shape, relative-frequency bound, byte stability), baseline-mean computation (empty, single, multi-file averaging), Manhattan distance properties (zero on identical, symmetric, nonneg), `bootstrap_compare` end-to-end (empty target / empty baseline / well-formed output / target-far-from-baseline lands above median on real corpus), markdown formatter (unavailable / available paths).

### Changed (Phase 1 step 5 finisher: ClaimLicense migration)

- **`general_imposters.py`** now renders its claim-license block via `claim_license.ClaimLicense.from_legacy()` + `render_block()`. Carries the legacy `licenses` / `does_not_license` fields plus surface-specific comparison context (candidate persona, candidate / impostor doc counts, persona count, iteration count, feature fraction, top-N feature size), Wilson 95% CI on the proportion as `confidence_interval_95`, and three reference citations (Koppel 2014, Kestemont 2016, R `stylo::imposters()`). Decision regions and impostor floor surface as `additional_caveats`. The legacy dict shape stays in JSON output (`GIResult.to_dict()["claim_license"]`) so downstream consumers reading the dict shape continue to work.
- **`validation_harness.py`** renders the structured block alongside the legacy dict in JSON. Comparison context: manifest path, evaluated surface, n_validation_entries, n_scored_records. The legacy `operating_point` text moves to `additional_caveats`; `fpr_target` from the operating-point dict lands in the structured `fpr_target` field.
- **`voice_validation_harness.py`** likewise renders the structured block. Comparison context: manifest, n_pairs, n_same_author, n_different_author, label_by, bootstrap_method. `fpr_target` from the operating-point dict propagates when available.
- **10 new regression tests** in `test_claim_license_migration.py`: `from_legacy()` basics (carries fields, gray_zone → caveats, missing fields default to empty, render_block works after adapter), GI migration (structured block in render_markdown, refused result still carries license, JSON keeps legacy dict shape), validation_harness migration (legacy dict unchanged + structured block carries surface label and FPR target), voice_validation_harness migration (structured block carries surface label, comparison set, FPR target).

### Notes

- **526 tests pass + 1 skipped** (was 501+1 in 1.29.1; +25 new regression tests, –0 changed elsewhere). Existing harness regression tests all still pass — the migration adds a structured block in the markdown rendering path; it does not change the JSON output shape, the harness math, or the CLI surface.
- **Phase 1 spine status.** Six structural steps:
  - Step 1 (manifest_validator) — shipped in 1.x.
  - Step 2 (task_surface field) — shipped in 1.x.
  - Step 3 (length-matched bootstrap) — variance_audit shipped earlier; voice_distance shipped this release.
  - Step 4 (validation_harness) — MVP shipped for `smoothing_diagnosis` (1.x) + voice-coherence shipped separately as `voice_validation_harness.py`. Adversarial-class expansion (paraphrase + humanizer) is a separate track on the roadmap.
  - Step 5 (claim-license report block) — helper shipped in 1.29.0; all four surfacing scripts now render the structured block as of this release.
  - Step 6 (POS-bigram KL in band call) — shipped in 1.x.
- **What this doesn't close.** Calibration breadth (more signals beyond `burstiness_B`; the five lexical-diversity signals known to be polarity-inverted on ESL still need a fluent-native corpus); RAID + MAGE corpus fetchers; per-window length-matched bootstrap CI ribbons in the sliding-window heatmap; per-family Burrows Delta bootstrap in voice_distance (requires feature-extraction caching). Each is independently shippable follow-up work.
- The voice_distance bootstrap is OFF by default. The per-window cost is light (function-word vector only), so 10 windows × 50 baseline files × 9999 resamples runs in seconds — but the user opts in.
- Three-way version sync at 1.30.0. No new keyword.

## [1.29.1] - 2026-05-10

**Reviewer-flagged P2 fixes across the GI harness, calibration cache, and direction-aware ranking metrics; private-reference template scrub; tag-convention restoration.** The 1.28.0 / 1.29.0 release run produced a clean test suite and shipped both cathedral upgrade #4 and #5 finishers, but a subsequent reviewer pass surfaced four validity issues — the kind that don't fail tests but quietly corrupt downstream claims when the harness is leaned on for real work. This release closes all four plus a private-data leak in a public template.

### Fixed

- **GI: target self-entry into the comparison pool.** `general_imposters.py` reads `--target` separately from the manifest, but if a manifest entry's `path` resolves to the same file as `--target`, the target ended up in the candidate or impostor pool — the same self-normalization failure mode `voice_distance.py` already guards against. The reviewer reproduced a manifest where the target was also in the candidate baseline and the harness reported proportion 1.000. Fix: `CorpusEntry` now carries `resolved_path: Path | None`, populated during `_load_manifest`; a new `_exclude_target_path` filter drops any entry whose resolved path equals `--target` (with a stderr notice listing dropped IDs) before the candidate / impostor selectors run. Symlinks and `./` / `../` paths collapse to canonical form via `Path.resolve()`. Three new tests in `test_general_imposters.py` cover the resolved-path filter, the no-resolved-path passthrough (in-memory entries from caller-built fixtures), and the manifest-loader populating `resolved_path` correctly.
- **GI: `MIN_IMPOSTORS` floor counts personas, not docs.** The General Imposters method's claim language (Koppel et al. 2014; Kestemont et al. 2016) is "M impostor *writers*"; pre-1.29.1 the floor gated on `len(impostor_docs)`, so 5 docs from a single persona — 1 writer — passed the gate and produced a normal attribution report. Fix: gate on `len(impostor_personas)` (distinct persona slugs in the impostor pool); the refusal message names both numbers ("got 1 persona across 7 docs") so the user sees what's missing. Doc count remains in the `GIResult` for adequacy diagnostics. Three new tests in `TestPersonaFloor` cover the single-persona refusal, the five-persona pass, and the refusal-message format.
- **Calibration cache: text-content fingerprint.** The 1.26.0 cache keyed on the manifest JSONL's SHA-256, but if the underlying text files the manifest points at were regenerated (re-OCR, re-extraction, cleanup pass, preprocessing toggle change) without a corresponding manifest edit, `load_or_score_corpus` would return stale scored records. Fix: `_corpus_text_fingerprint(entries)` hashes a canonical `(resolved_path, sha256_of_file_bytes)` listing across every entry the manifest filter selected; the fingerprint lands in `scoring_meta["corpus_text_fingerprint"]` and `cache_is_compatible` invalidates on mismatch. Pre-1.29.1 caches that don't carry the fingerprint are treated as unknown-corpus and force a re-score with a clear reason. Six new tests in `TestCorpusTextFingerprint` cover the fingerprint changing under file-content edits, stability under no change, missing-file handling, end-to-end cache invalidation when a file is rewritten in place, and the legacy-cache fall-through.
- **Direction-aware Average Precision.** `_ranking_metrics` returned AP computed on raw scores regardless of registry direction; for `lt` signals (compressed when value low) this made strong discriminators look weak in the survey table and ledger. Reviewer reproduced a toy `lt` case where raw AP = 0.383 while AP after score-negation = 1.0. Fix: `_ranking_metrics(pairs, direction=...)` now returns four fields — `auc`, `ap` (both raw, polarity-blind), `direction_aware_auc` (`auc` for `gt`, `1 − auc` for `lt`), and `direction_aware_ap` (AP computed with negated scores for `lt`). Both direction-aware values land in `entry["calibration"]`; the survey markdown table grew a `da_AP` column and an explanatory note. The polarity gate (gate 1) was already direction-aware as of 1.27.0; gate 2 (AUC/AP not embarrassing) is maintainer judgment and gains da_AP as the more appropriate input. Four new tests in `TestDirectionAwareAP` cover the lt-negation invariant, gt-direction unchanged, da_AUC consistency with the 1.27.0 formula, and the default `direction="gt"` for back-compat callers.
- **Private-reference leak in `voice_insights_report.template.md`.** The public template named two specific prior subjects ("Critical Animal" + "Joshua / Scu cross-boundary drift comparison") in its provenance line; the names also appeared in `generate_voice_report.py`'s argparse help text and in `scripts/README.md`'s example invocation. The template itself didn't expose voiceprint data, but disclosing that those named voice analyses existed is exactly the metadata the framework otherwise treats as private. Fix: replaced with generic wording ("two internal reference reports produced during framework development"; example author name "Jane Author" / corpus label "Jane Author blog"; example impostor persona "example_impostor_blog").

### Changed

- **Tag convention restored.** The bare `1.29.0` tag created during the 1.29.0 release was retired and replaced with `v1.29.0`, matching every prior release tag (`v1.3.0` through `v1.28.0`). Tooling that filters releases by `v*` glob patterns now sees the full series.

### Notes

- **501 tests pass + 1 skipped** (was 485+1 in 1.29.0; +16 regression tests for the four code-fixes, –0 changed elsewhere). Each P2 has explicit test coverage for both the failure mode the reviewer flagged and the post-fix invariants.
- **No CLI / public-JSON breaking changes.** `_ranking_metrics` gains a `direction` keyword arg defaulted to `gt`; pre-1.29.1 callers continue to work. Cache files written by 1.26.0–1.29.0 invalidate gracefully with a clear reason rather than producing stale results. The GI persona-count gate is stricter than the doc-count gate (any pre-1.29.1 corpus that passed the floor with diverse personas still passes).
- The reviewer pass also flagged a release-hygiene tag-convention issue (bare `1.29.0` tag vs. prior `v1.x` convention); the tag was renamed and re-pushed as part of this release.

## [1.29.0] - 2026-05-10

**Cathedral upgrade #5 finisher (sliding-window heatmap) + Phase-1 step 5 (claim-license helper) + corpus track template (personal pre-AI baseline assembly).** A three-piece release: the sliding-window mode in `variance_audit.py` (band classification per window) had been carrying half the localization story since the original implementation; the heatmap renderer is the missing visualization that turns a per-window band list into a localization map ("the smoothing is concentrated in words 1500–2500"). The claim-license helper factors the divergent freeform "what this licenses / does not license" boilerplate scattered across harness modules into one structured shape every harness can consume. The corpus template documents how a writer assembles their own pre-AI baseline corpus — the irreducible piece of the framework that has to come from the writer, not the internet.

### Added

- **`scripts/sliding_window_heatmap.py`** consumes `variance_audit.py --json` output (or just the `windows` sub-dict) and renders a markdown report with five sections: (1) a unicode-block sparkline of per-window `compression_fraction`, (2) a single-character band tape (`H`/`M`/`L`/`-`), (3) a hot-zones summary identifying contiguous Heavily/Moderately smoothed runs in word coordinates with the dominant flagged signals per run, (4) a per-signal × per-window grid showing which signals fired where, and (5) a claim-license block via `claim_license.py`. Also emits a structured JSON sidecar (`--json-out`) with the same content for downstream tooling. Privacy guard refuses non-private output paths unless `--allow-public-output` is set; pairs with the convention from `voice_profile.py` and `general_imposters.py`. **33 regression tests** in `scripts/tests/test_sliding_window_heatmap.py`: input loading, sparkline scaling, band-tape encoding, hot-zone detection (single window, contiguous merge, disjoint zones, Insufficient breaks runs, dominant-signal aggregation), signal-grid construction, markdown section presence, JSON shape stability, privacy guard, end-to-end CLI.
- **`scripts/claim_license.py`** factors the structured "what this result licenses / does not license" block into one helper. `ClaimLicense` dataclass carries `task_surface`, `licenses`, `does_not_license`, comparison-context (corpus, length range, register match, language match), operating point (FPR target, 95% CI), and surface-specific fields (additional caveats, references). `render_block()` produces a markdown block ready to paste; `to_dict()` / `to_json()` produce JSON. `from_legacy()` adapts pre-1.29.0 dict-shaped `CLAIM_LICENSE` blocks so the existing harnesses can incrementally adopt the structured shape without a hard cutover. The sliding-window heatmap is the first script wired through; the older harnesses (validation_harness, voice_validation, GI) keep their existing freeform blocks but gain a clear migration path. Closes Phase-1 step 5 of the validation spine ("Report template: 'what this result licenses / does not license'").
- **`scripts/calibration/PROVENANCE_TEMPLATE.md`** is the eight-step walkthrough for assembling a personal pre-AI baseline corpus: gather source files (blog exports, manuscripts, journals — using the acquisition tools shipped 1.15.0 / 1.17.0 / 1.18.0 / 1.19.0), write per-file manifest entries with the 1.14.3+ `corpus_role: identity_baseline` and ESL ratchet fields, validate the manifest, run the corpus-hygiene gate, build the voice profile, decide what counts as baseline vs. drift archive, schedule re-surveys, and decide on impostor pools. Also covers the common gotchas: `pre_ai_human` as the writer's judgment (not the framework's audit), heavy-edit drafts that leak post-AI into ostensibly pre-AI files, persona splitting policy ("one persona per voice, not per project"), and the structural privacy posture (voice profiles are voice-cloning input, treat like a password). Sibling to the existing `PROVENANCE.md` (per-signal threshold ledger) at the calibration directory; the template covers identity-baseline corpus assembly while PROVENANCE.md covers per-signal threshold derivation.

### Changed

- **Plugin keyword list** gains `sliding-window-heatmap`. Three-way version sync at 1.29.0.

### Notes

- **485 tests pass + 1 skipped** (was 452+1 in 1.28.0; +33 sliding-window-heatmap tests, –0 changed elsewhere).
- **What this closes:** Cathedral upgrade #5 (sliding-window localization) was scoped as "whole-chapter distance is blunt; the cathedral version says 'the drift is concentrated in paragraphs 12-19, mostly function words and sentence cohesion' with a heatmap." Status moves from "sliding-window mode shipped, heatmap visualization is roadmap" to **shipped end-to-end**. Phase-1 step 5 ("what this result licenses / does not license") moves from "scoped" to **shipped** via the helper; existing harness adoption is incremental work tracked separately.
- **What this doesn't close:** Cathedral upgrade #2 (length-matched bootstrap) is still scoped — the heatmap report's per-window section flags this in the additional-caveats block ("window z-scores at small N are noisy by construction"). When the bootstrap-percentile mode lands, the heatmap can carry per-window CI ribbons rather than scalar fractions.
- The heatmap is document-relative (sparkline is scaled to the per-document max) by design. A tall bar in a low-compression document is not the same as a tall bar in a high-compression one. Future work on the surface: cross-document baseline normalization (compare a heatmap to a writer's pre-AI baseline heatmap) and manuscript-scale stitching (book-level heatmap composed of per-chapter heatmaps).

## [1.28.0] - 2026-05-10

**General Imposters validation harness — Cathedral upgrade #4 finisher.** The impostor corpus shipped across 1.14.3 → 1.19.0 (schema, blog acquisition, Blogger Takeout, online magazines, PDF library) was always built as the *prerequisite* for this harness. This release ships the harness itself: given a target text and a candidate writer's identity baseline, the General Imposters method (Koppel et al. 2014, Kestemont et al. 2016, R `stylo::imposters()` as the canonical reference) turns SETEC's distance machinery into a calibrated attribution claim by asking, under bootstrap resampling: how often does the target fall closer to verified-CANDIDATE than to N plausible-other writers in matched register?

### Added

- **`scripts/general_imposters.py`** with the full GI bootstrap. Per-iteration loop: random sub-sample of the top-N feature vocabulary (default top-200 with 50% per-iteration fraction, matching Koppel 2014's anchor parameters), project target + candidate identity-baseline + impostor docs onto the sub-sampled vocab, compute cosine distance from target to every doc, mark a *win* iff the candidate persona's nearest doc is closer than every impostor persona's nearest doc. Aggregate the win count across iterations into a frequentist proportion. Wilson-score 95% CI on the proportion.
- **Decision regions** matching the literature's gray-zone convention (Kestemont 2016): proportion ≥ 0.80 → `consistent_with_candidate`; ≤ 0.20 → `inconsistent_with_candidate`; in between → `gray_zone_refused` (the framework declines an attribution claim). This is the framework's "the math doesn't entitle the verdict" guard at the harness layer.
- **Hard refusal gates** below the floor: fewer than 5 impostor docs in matched register (`MIN_IMPOSTORS = 5`) → refuse with a clear reason; no candidate identity-baseline docs → refuse; empty feature vocabulary → refuse. The harness emits a markdown report explaining the refusal rather than a misleading proportion.
- **Manifest filtering** that respects the impostor-corpus schema: candidate docs must have `corpus_role: identity_baseline` AND match the candidate persona AND match register; impostor docs must have `corpus_role: impostor` AND name the candidate persona in their `impostor_for` list AND match register. The cross-check from `manifest_validator.py`'s persona-reference ratchet (1.14.3) is the same shape this filter consumes.
- **Claim-license block** in every report (markdown + JSON). Documents what the harness reports ("stylometric consistency between target and candidate's identity baseline, expressed as a frequentist proportion") and what it does NOT report ("authorship attribution in the legal or philosophical sense; AI-edited / paraphrased / humanizer-tool drafts are not adjudicated by this surface"). The gray-zone clause names the [0.20, 0.80] refusal range explicitly.
- **CLI surface** matching the framework's conventions: `--target`, `--manifest`, `--candidate-persona`, `--register` (auto-inferred from the candidate's first observed register if omitted), `--iterations` (default 100), `--feature-fraction` (default 0.5), `--top-n-features` (default 200), `--seed` (default 42 for reproducibility), `--out` for markdown, `--json-out` for JSON, `--allow-public-output` for the marker-based privacy guard.
- **Privacy posture.** GI output is voice-cloning-adjacent — the proportion + impostor-persona list ARE the kind of data a stylometric attacker would want. Default output paths must live under `ai-prose-baselines-private/`. The harness never quotes raw text from the corpus; impostor identities are surfaced as persona slugs (consent-status enforcement happens at the schema layer per `consent_status` field on each impostor entry).
- **23 regression tests** in `scripts/tests/test_general_imposters.py`: refusal gates (too few impostors, no candidate docs), math invariants (target-identical-to-candidate → proportion ≈ 1; target-identical-to-impostor → proportion ≈ 0; bounded to [0,1]; Wilson CI valid + contains proportion), decision regions (consistent / inconsistent / gray-zone), feature-pipeline (vocab sized correctly, vectors sum-bounded, cosine identities), manifest selection (persona + register + impostor_for cross-check), end-to-end CLI against synthetic manifest, privacy guard, JSON shape stability, CLI help, and determinism under seed.

### Notes

- **452 tests pass + 1 skipped** (was 428 + 1 in 1.27.0; +24 GI tests, –0 changed elsewhere). The synthetic test corpus uses 4 candidate docs + 6 impostor docs across 6 personas — enough to exercise both decision regions and the refusal floor.
- **What this closes:** Cathedral upgrade #4 (impostor baselines) was scoped in the original roadmap as "compare the target writer against plausible other writers in matched registers. Without these, the voiceprint over-attributes register and topic to identity." Status moves from "corpus shipped, harness wireup is the missing piece" to **shipped end-to-end**. The framework now answers the GI question on any target + any candidate in the impostor pool.
- **What this doesn't close:** the GI score is *frequentist* — a proportion of bootstrap wins, not a Bayesian posterior. Adversarial paraphrase, humanizer-tool output, and AI-edited drafts remain explicitly out of scope (those are the validation harness's job, run separately). Future work on the GI surface: cross-target ledger of consistency claims, a heldout-split confidence calibration, and integration with the validation harness's licensing block.
- The harness uses lightweight feature extraction (lowercased token frequencies; not the full `stylometry_core` pipeline) by design — the GI method's feature-subset bootstrap step washes out per-token noise, and the proportion-of-wins metric is robust to extraction variants. Future work could swap in `stylometry_core`'s POS-bigram or function-word vectors for richer per-iteration discrimination, but the literature anchors this with simple word frequencies.
- Three-way version sync at 1.28.0. No new keyword (the existing `general-imposters` keyword from earlier releases now points at a real implementation).

## [1.27.0] - 2026-05-10

First calibrated threshold + direction-aware polarity gate. The 1.26.0 maintainer's-first-real-calibration-run surfaced a polarity-gate bug (the gate read raw AUC ≥ 0.5 across all signals, but for `lt`-direction signals the registry's hypothesis is satisfied by raw AUC < 0.5 — high raw AUC on an `lt` signal means inverted polarity, the *opposite* of "discriminates well"). This release fixes the gate, surfaces direction-aware AUC in the survey output, ships the first committed calibrated threshold (`burstiness_B = -0.622724`, derived from EditLens val), and documents the polarity-inversion finding the recalculation revealed.

### Added

- **`burstiness_B` calibrated threshold** at value `-0.622724270454707`. First entry in `scripts/calibration/thresholds_calibrated.json`; first registry threshold to flip `provisional: False` and carry a `provenance` slug. Slug: `editlens_val_burstiness_B_fpr0.01_2026-05-10`. Empirical: AUC 0.317 (raw) / 0.683 (direction-aware), AP 0.388, TPR 7.0% at FPR 0.93%, precision 88% at the operating point, n_pos = n_neg = 753. Bootstrap CI (fixed-threshold paired, 2000 resamples, seed 42): TPR [0.052, 0.089], FPR [0.003, 0.017], precision [0.794, 0.958]. The calibrated threshold is more *conservative* than the prior heuristic (-0.40 → -0.62 means a stricter "below threshold" condition); the framework now catches a smaller, higher-precision slice of AI essays at FPR target 0.01. In-sample only; out-of-corpus performance is the heldout-split roadmap deliverable.
- **Direction-aware polarity gate** in `calibration_survey.evaluate_gates`. Pre-1.27.0 the gate read `raw_auc >= 0.5` regardless of registry direction, which silently passed `lt` signals whose corpus inverted the hypothesis. Now: `gt` signals require raw AUC ≥ 0.5; `lt` signals require raw AUC ≤ 0.5. The maintainer's first calibration run on EditLens val caught this empirically — `mtld` reported raw AUC 0.87 in `lt` direction, suggesting strong discrimination, but threshold sweeps returned TPR ≈ 0 because AI essays are *higher* on mtld than human ESL essays. Real finding, surfaced once the gate read direction-aware.
- **`direction_aware_auc` column** in survey output (markdown table + JSON ledger). For `gt` direction this is the raw AUC; for `lt` it's `1 − raw AUC`. Reads on a consistent "≥ 0.5 = polarity matches" scale across all signals so the maintainer can compare discrimination strength without thinking about direction every time. Survey now sorts by `da_AUC` (not raw AUC), so polarity-matching signals float to the top regardless of registry direction.
- **`references/calibration-findings-2026-05-10.md`** documenting the polarity-inversion finding the run surfaced. Six signals match the registry's hypothesis on this corpus (sentence-rhythm features: `sentence_length_sd`, `burstiness_B`, `adjacent_cosine_sd`, `fkgl_sd`, `mdd_sd`, `connective_density`). Five signals invert (lexical-diversity + entropy + cohesion-mean: `mtld`, `mattr`, `shannon_entropy`, `yules_k`, `adjacent_cosine_mean`). The doc characterizes the inversion as a corpus-mismatch finding — the smoothing-diagnosis hypothesis presupposes a fluent-native human comparator, and against ESL student writing the diversity gap closes or inverts. Concrete implications: which signals to treat as suspect when calibrating against ESL-comparator corpora; why RAID and MAGE (native-fluent samples in the roadmap) are needed to calibrate the inverted signals against the registry's declared polarity.
- **PROVENANCE.md entry** for the calibrated threshold. Per the "Template for new entries" pre-registered shape: slug, signal, derived value, corpus pin (commit SHA), license, calibration method, split role, FPR resolution, full empirical block (AUC, AP, TPR + CI, FPR + CI, precision + CI), CI method, SETEC commit, derivation date, notes carrying the in-sample caveat + the corpus-register caveat + cross-references to the calibration-findings doc.

### Changed

- **`COMPRESSION_HEURISTICS["burstiness_B"]`** in `variance_audit.py`: value `-0.4` → `-0.6227`, `provisional: True` → `False`, `provenance: None` → `editlens_val_burstiness_B_fpr0.01_2026-05-10`. The `ThresholdSpec` dataclass enforces the mutex (a `provenance` slug + `provisional=False` is the calibrated state; `provenance=None` + `provisional=True` is the heuristic state). Variance audits now report `1 of 11 signal thresholds carry calibration provenance` in the calibration-status footer.
- **`test_threshold_spec.test_v1_registry_is_all_provisional`** renamed to `test_calibrated_signals_carry_provenance_slugs` and rewired. Pre-1.27.0 the test asserted every signal was provisional; per its own docstring's instruction ("flip the assertion when the first calibrated threshold lands"), the test now asserts (a) calibrated signals carry non-None provenance slugs + provisional=False, (b) provisional signals carry provenance=None + provisional=True, (c) the two sets together account for every signal. The dataclass mutex catches drift; this test catches misuse of the flags.

### Fixed

- **`relative_to` crash in `calibrate_thresholds.main`** when the user runs the CLI from a worktree. The trailing print statement called `out_path.relative_to(REPO_ROOT)` which raised `ValueError` if the absolute path wasn't a subpath of the repo root. The ledger entry was already written by then; only the success message crashed. Switched to a defensive `is_relative_to` check with a fallback to the absolute path.

### Notes

- **428 tests pass + 1 skipped** (was 429 + 1 in 1.26.0; 2 calibration-survey tests rewired, 1 threshold-spec test renamed/rewired). Calibration-survey tests now exercise direction-aware polarity correctly (the pre-1.27.0 versions used direction-blind AUC values that masked the gate-1 bug).
- The cached scored records at `ai-prose-baselines-private/editlens/_records_cache_val.json` survive across this and future invocations — re-running with relaxed gate floors, different signal subsets, or different FPR targets is cache-read time.
- Three-way version sync at 1.27.0. No new keyword.
- This release closes the *first commit* phase of the calibration arc that started in 1.10.0. Future calibration runs against RAID, MAGE, or native-fluent corpora will likely add more thresholds (and possibly invert some of the polarity findings recorded here for the lexical-diversity signals).

## [1.26.0] - 2026-05-09

Score-once-survey-many architecture + first calibration run on real corpus. The user's question "why would we re-score from scratch unless something changes?" surfaced an architectural inefficiency in the 1.10.0 calibration toolchain: every per-signal `derive_threshold` call independently re-scored the entire corpus, multiplying wall-clock by 11× when running a full survey. This release factors scoring out so the corpus is scored exactly once per survey, the records are persisted to a JSON cache that survives across invocations, and re-runs with different FPR targets / gate floors / signal subsets become threshold-sweep-only (~seconds, not minutes). The cache invalidates correctly when the manifest content, tier toggles, use filter, sub-sample state, or scorer version changes. Includes the maintainer's first actual calibration survey on the EditLens val split (1506 entries, label-balanced 753/753) — a pre-1.26.0 bug in survey row extraction (read from `entry["empirical"]` / `entry["sweep"]` but real entries use `entry["calibration"]` / `entry["derived_value"]`) was caught by running against real data and is fixed in this release.

### Added

- **`score_corpus(args)`** in `calibrate_thresholds.py` — pure scoring, no per-signal logic. Returns `(records, scoring_meta)` where `scoring_meta` carries the cache-validity inputs (manifest path + SHA-256 hash, use filter, tier flags, sub-sample state, scorer version, scoring timestamp). Handles label-stratified sub-sampling internally; the `_stratified_subsample` helper is now a top-level function shared with the cache loader.
- **`derive_threshold_from_records(records, *, args, scoring_meta)`** — pure per-signal threshold sweep + provenance entry composition. No scoring, no I/O. Reads the per-signal score column out of cached records, sweeps direction-aware, builds the bootstrap CI, assembles the entry. Sub-sample provenance flows from `scoring_meta` to the entry so the PIPELINE CHECK notes-prefix propagates correctly through the cache.
- **`load_or_score_corpus(args, *, cache_path, refresh)`** — cache-aware composer. If `cache_path` exists and is compatible with the current args (manifest hash, tier flags, use filter, sub-sample state, scorer version all match), loads the cache and returns `(records, meta, cache_hit=True)`. Otherwise scores fresh, writes the cache, returns `(records, meta, cache_hit=False)`. `refresh=True` forces re-scoring even when the cache is valid.
- **`cache_is_compatible(meta, args, manifest_sha256)`** — explicit cache-invalidation logic returning `(ok, reason)`. The reason string surfaces in stderr when the cache is rejected so the user knows what changed (manifest content / tier toggle / use filter / sub-sample / scorer version).
- **`--records-cache PATH`** and **`--refresh-cache`** flags on both `calibrate_thresholds.py` and `calibration_survey.py`. Single-signal CLI calls and full surveys both benefit from cache reuse — when the same cache path is passed across multiple invocations, the corpus is scored once and reused across all subsequent threshold sweeps.
- **`SCORER_CACHE_VERSION`** constant in `calibrate_thresholds.py`. Read by `cache_is_compatible` so a code change that affects record shape can invalidate all existing caches without users needing to manually delete them. Pre-set to `"1.26.0"`; bump when scoring code changes shape.
- **`derive_threshold(args)` thin shim** for backward compatibility. Pre-1.26.0 callers (the standalone CLI; pre-existing test fixtures) continue to work unchanged. The shim composes `load_or_score_corpus` + `derive_threshold_from_records`, so even single-signal invocations now benefit from cache reuse on re-runs.
- **Score-once-survey-many in `calibration_survey.py`.** `run_survey` now scores the corpus exactly once at the top of the run and iterates all 11 signals over the cached records via `derive_threshold_from_records`. Pre-1.26.0 the wrapper called `derive_threshold` 11 times, each of which re-scored from scratch. Same numerical results; 11× faster. Verified on the real EditLens val split: scoring 1506 entries with Tier 1+2+3 enabled took 2:33 wall-clock; cache-hit re-run for the same survey shape took 16 seconds (the bootstrap CI is the remaining cost — 11 signals × 2000 resamples × 1506 pairs).
- **Real-shape entry extraction** in `survey_one_signal` and `evaluate_gates`. Pre-1.26.0 the survey-row builder read AUC / AP / TPR / threshold from `entry["empirical"]` and `entry["sweep"]`; real provenance entries from `derive_threshold` use `entry["calibration"]` and `entry["derived_value"]`. The mismatch was hidden because tests used synthetic-shape fakes. Fixed: row builder now reads the real shape with fallback to the test shape so existing tests continue to pass. The maintainer's first calibration run against actual data caught the bug in one minute; a real-corpus integration test would have caught it earlier.
- **44 new regression tests** across two new test files:
  - `test_calibration_cache.py` (11 tests): cache JSON shape round-trip, cache hit returns records without re-scoring, cache invalidates on manifest content change / tier toggle / use filter / sub-sample state / scorer version, `--refresh-cache` forces re-scoring, corrupt cache file is treated like a miss, backward compat with Namespaces lacking the new flags.
  - Updated `test_calibration_survey.py` and `test_calibration_subsample.py`: 33 existing tests rewired to mock the new `load_or_score_corpus` + `derive_threshold_from_records` split. Plus a new test `test_survey_runs_corpus_scoring_once_across_signals` that pins the score-once invariant — calls `run_survey` over 3 signals against a 4-entry manifest and asserts `score_smoothing_entry` was called exactly 4 times (not 12).

### Calibration findings (informational; thresholds NOT committed)

The maintainer's first actual calibration run produced this ranking on the EditLens val split (1506 essays, balanced 753 ai_generated / 753 pre_ai_human, FPR target 0.01, Tier 1+2+3 enabled):

| signal | direction | heuristic | AUC | AP | gates passing |
|---|---|---|---|---|---|
| `mtld` | lt | 60.0 | 0.868 | 0.875 | 4 of 5 (gate 4 fails — TPR at FPR≤0.01 too low) |
| `mattr` | lt | 0.65 | 0.842 | 0.846 | 2 of 5 |
| `shannon_entropy` | lt | 7.0 | 0.600 | 0.575 | 3 of 5 |
| `connective_density` | gt | 20.0 | 0.529 | 0.515 | 2 of 5 |
| `burstiness_B` | lt | -0.40 | 0.317 | 0.388 | polarity inverted |
| `mdd_sd` | lt | 0.70 | 0.415 | 0.447 | polarity inverted |
| `fkgl_sd` | lt | 1.5 | 0.365 | 0.402 | polarity inverted |
| `yules_k` | gt | 200.0 | 0.337 | 0.400 | polarity inverted |
| `adjacent_cosine_sd` | lt | 0.12 | 0.319 | 0.394 | polarity inverted |
| `sentence_length_sd` | lt | 5.0 | 0.305 | 0.377 | polarity inverted |
| `adjacent_cosine_mean` | gt | 0.6 | 0.261 | 0.365 | polarity inverted |

**Findings (not commits):**
- **`mtld` and `mattr` are the strongest discriminators** on this corpus (AUC 0.87 / 0.84). Both are lexical-diversity signals; AI-generated essays are systematically lower-MTLD and lower-MATTR than human essays. Both fail gate 4 (TPR < 5% at FPR ≤ 1%) — the discriminative band exists but you can't find a threshold that catches a meaningful fraction of AI essays without producing false positives faster than 1 in 100. That's a real limit, not a calibration mistake.
- **Seven of eleven signals fail polarity** (AUC < 0.5). The registry's declared direction matches the smoothing-diagnosis hypothesis (compression → low variance → low burstiness, low FKGL spread, etc.), but on the EditLens val corpus AI essays score *higher* on those signals than human essays. The registry's polarity hypothesis is empirically falsified for this corpus's mix of generators and registers — important calibration finding worth recording even though no threshold gets committed. Per `PROVENANCE.md` Selection Criterion 1: "AUC < 0.5 in the declared direction is a *finding* about the corpus or about the registry's polarity convention, not a threshold to commit."
- **No signal passes all five gates at FPR target 0.01**, so this release does NOT commit any threshold to `thresholds_calibrated.json`. The cached records and survey JSON are written to `ai-prose-baselines-private/editlens/` for the maintainer's review and re-runs at relaxed FPR targets.
- `mtld` is the strongest commit candidate at a relaxed FPR target. Re-running with `--fpr-target 0.05` reads the cache (no re-scoring) and would show whether the TPR floor passes — that exploration is now a 16-second loop instead of a 2:33 wait.

### Notes

- **429 tests pass + 1 skipped** (was 417 + 2 in 1.25.0; +12 new across the cache surface, –1 collapsed test). Full suite runtime 6:32 (the calibration tests now exercise real `score_smoothing_entry` paths via a mocked-but-realistic synthetic stub; runtime increase is mostly bootstrap CI work, not scoring).
- The cached records file at `ai-prose-baselines-private/editlens/_records_cache_val.json` is gitignored (lives under the private baselines path). Future surveys against the same corpus + tier mix re-use it instantly. Re-runs with different FPR targets, gate floors, or signal subsets are cache-read time only.
- Cache JSON shape is documented in `load_or_score_corpus`'s docstring. Future tooling that wants to read the cache directly (a heatmap visualization, a per-signal density plot) can rely on the stable shape.
- Three-way version sync at 1.26.0. No new keyword.
- Calibration thresholds NOT committed to `thresholds_calibrated.json` from this run — the survey produced no signal passing all five gates, so per the pre-registered selection criteria there is nothing to commit.

## [1.25.0] - 2026-05-09

Calibration sub-sampling for pipeline checks. The full calibration run takes a few hours of Tier 2/3 compute on the ~130-essay ESL slice. That's a real commitment for a maintainer trying to verify the toolchain works end-to-end before spending the time. This release adds `--max-entries N` to both `calibrate_thresholds.py` (inner) and `calibration_survey.py` (wrapper), letting the maintainer run a 10% (or any %) partial first to catch environment / dependency / SSL / spaCy issues, get a wall-clock estimate for the full run, and verify the survey produces the expected output shape. Sub-sampled rows carry visible "PIPELINE CHECK" markers throughout (provenance entry `notes` prefix, `sub_sample` block, survey `is_pipeline_check` flag, markdown banner) so they can never be silently treated as a calibration.

### Added

- **`--max-entries N`** flag on `calibrate_thresholds.py`. Caps the manifest entries fed into the variance audit. Sub-sampling is **label-stratified** — proportional to class size with a floor of 1 per non-empty class — so a small cap can't accidentally collapse one label to zero (which would make the threshold sweep undefined). Sampling is **deterministic** via the bootstrap seed (or `--max-entries-seed` if the maintainer wants to override the sample seed without changing the bootstrap CI seed), so partial runs are reproducible.
- **Provenance tagging.** When sub-sampling is applied, the resulting provenance entry gains:
  - A `sub_sample` block: `{applied: true, n_used: <int>, n_full: <int>, fraction: <float>, seed: <int>}`.
  - A `notes` field that starts with `"PIPELINE CHECK (sub-sampled run, NOT a calibration). N/M entries used. Do not commit this entry to the ledger as a calibrated threshold; small-N gates won't pass meaningfully."` followed by the original notes content.
  Both are belt-and-suspenders against accidental commit of a sub-sampled threshold to `thresholds_calibrated.json`. Future ledger consumers can branch on `sub_sample` to refuse rows where `n_used < n_full`.
- **`--max-entries` and `--max-entries-seed`** plumbed through `calibration_survey.py`. The wrapper forwards the flags to every signal's inner `derive_threshold` call so all 11 signals score the SAME sampled essays (consistency across the survey). The survey JSON gains:
  - `max_entries: N` and `max_entries_seed: S` (echo the flags).
  - `is_pipeline_check: bool` flag for downstream consumers.
- **Pipeline-check banner** at the top of `calibration_survey.py`'s markdown output when `is_pipeline_check: true`. Prominent enough that a maintainer reading the table can't miss that this is not a calibration; small-N gates won't pass meaningfully and the resulting thresholds must not be committed.
- **`PROVENANCE.md` updated** with the partial-run pattern. Documents a 10% pipeline check (`--max-entries 13` against the 130-essay ESL slice) followed by the full run as the recommended first-time-calibration sequence. Also notes that `--no-tier2` and `--no-tier3` make the partial run faster still — useful for the absolute-cheapest first pass.
- **15 new regression tests** in `scripts/tests/test_calibration_subsample.py`: inner CLI accepts `--max-entries` + `--max-entries-seed`; sub-sample caps total entries to the requested N; sub-sample is label-stratified (both classes preserved with floor of 1); sub-sample is deterministic under the same seed; different seeds produce different samples; `--max-entries-seed` correctly overrides `--bootstrap-seed`; sub-sample is a no-op when `max_entries >= full count`; survey wrapper forwards both flags to the inner; survey marks `is_pipeline_check: true` only when `--max-entries` is set; survey markdown shows the `PIPELINE CHECK` banner only on partial runs; CLI surface tests on both scripts.

### Notes

- **417 tests pass + 2 skipped** (was 402 + 2 in 1.24.0; +15 new). Tests use a mocked `score_smoothing_entry` so they don't require the EditLens corpus or Tier 2/3 deps; sub-sampling logic is pure-Python and exercised through `derive_threshold` with stubbed manifest entries.
- The partial-run pattern doesn't make calibration cheaper *per se* — it adds an optional cheap pipeline check before the expensive full run. The trade-off is "a few minutes for a 10% pre-flight" against "a few hours for a full run that fails on the last signal because spaCy isn't installed." Recommended workflow: run `--max-entries 13 --no-tier2 --no-tier3` first to verify deps + SSL + manifest shape, then run the full survey.
- Calibration runs whose results commit to the ledger MUST NOT use `--max-entries`. The provenance ledger's `test_calibration_provenance` regression tests will not specifically refuse rows with a `sub_sample` block (the `notes` prefix and the block itself are the visible warning), but the `PIPELINE CHECK` notes prefix is loud enough that a maintainer copying the entry into the committed ledger will see it before they save the file.
- Three-way version sync at 1.25.0. No new keyword.

## [1.24.0] - 2026-05-09

GitHub-mirror fetcher for the EditLens corpus. Pangram Labs publishes the same EditLens data the calibration toolchain consumes in two places: the license-gated HuggingFace dataset at `pangram/editlens_iclr` (requires `HF_TOKEN` + license-acceptance UI) and the public companion code repo at `pangramlabs/EditLens` (plain `git clone`, no auth). The 1.10.0 fetcher targeted the HF path; this release adds a sibling fetcher for the GitHub path. Both produce identical CSVs; both write the same NOTICE.md license posture; downstream `editlens_to_manifest.py` consumes either output unchanged. The HF path is preserved for users who want the dataset-card revision pin and license-card check; the GitHub path unblocks anyone who can't or doesn't want to do the HF auth dance — including most maintainers' first calibration runs.

### Added

- **`scripts/calibration/fetch_pangram_editlens_github.py`** — stdlib-only fetcher targeting `https://raw.githubusercontent.com/pangramlabs/EditLens/<commit-sha>/data/<filename>.csv`. Seven splits supported (matching the upstream `data/` directory): `nonnative_english` (62 KB, ESL slice — smallest, default), `human_detectors` (2 MB), `val` (9 MB), `test_enron` (15 MB), `raid_10k` (17 MB), `test_llama` (24 MB), `test` (25 MB). `--split all` downloads everything (~92 MB).
- **Reproducibility via commit SHA pin.** `--commit-sha <sha>` pins downloads to a specific upstream commit. Without a flag, the fetcher resolves `main` via the GitHub commits API and prints the resolved SHA so the user can pin it on re-runs ("calibration runs whose results commit to the SETEC ledger should always pass an explicit `--commit-sha`"). The pinned SHA aliases as `revision` in `.fetch_record.json` for HF-fetcher compatibility — `calibrate_thresholds.py`'s `_load_fetch_record` reads either source identically.
- **Per-file SHA-256 hashes** in both `NOTICE.md` and `.fetch_record.json` for tamper detection. A future re-fetch with the same commit SHA must produce identical file content; mismatched hashes between runs are an upstream-content-change signal.
- **SSL context fallback chain.** Python.org's macOS installer ships without running `Install Certificates.command`, so the system Python's default cert store can be empty even though TLS works. The fetcher tries (1) `certifi` package's bundle (almost always present as a transitive dep of `requests` / `pip` / `huggingface_hub`), (2) macOS system bundle at `/etc/ssl/cert.pem`, (3) `ssl.create_default_context()`. SSL errors during SHA verification produce an actionable error message pointing at `Install Certificates.command` or `pip install certifi` rather than a generic "commit not found" footgun.
- **License + provenance NOTICE.md** mirroring the HF fetcher's shape (CC BY-NC-SA 4.0 declaration, DO NOT REDISTRIBUTE block, GPL-3 incompatibility notice, per-file enumeration with hashes). One additional caveat: the GitHub repo doesn't expose a structured dataset card via API, so the GitHub fetcher relies on the upstream `LICENSE` + README declaration of CC BY-NC-SA 4.0 at fetch time. If the upstream repo's licensing posture ever changes, this fetcher will not detect that automatically; the NOTICE.md flags the caveat.
- **`PROVENANCE.md` updated** to document both fetch paths. The doc now recommends the GitHub path for first-time runs (no auth, stdlib-only) and reserves the HF path for runs that want the dataset-card revision pin + license-card check.
- **`dependency_check.py` updated.** `huggingface_hub` and `pyarrow` are now marked `optional_in_tier=True` within the calibration tier — the GitHub-fetcher path is stdlib-only, so users who skip the HF dance never need them. The setup skill no longer reports the calibration tier as "missing required" when only those packages are absent.
- **25 new regression tests** in `scripts/tests/test_fetch_pangram_editlens_github.py`: URL construction (every known split + unknown-split rejection), known-splits coverage (pins all seven so upstream additions trigger a test failure), commit-SHA verification (200 → True, 404 → False, other HTTP errors re-raise, network errors re-raise), SSL context fallback (certifi when present + caching), download driver (writes bytes + hash + size, skip-when-exists, refresh re-downloads), NOTICE.md content (provenance block, license clauses, file enumeration, license-card-check caveat), `.fetch_record.json` shape (HF-compat `revision` alias, files list with hashes), `run()` driver (explicit-SHA + no-verify path, 404 abort, SSL-error message, default-main resolution, `--split all`), CLI surface (every documented flag, default split, unknown-split rejection), and a structural check on the API/raw URL constants.

### Notes

- **402 tests pass + 2 skipped** (was 377 + 2 in 1.23.0; +25 new). Tests use mocked HTTP throughout — CI does not depend on network access. The maintainer verified the live download path against `pangramlabs/EditLens` commit `05a588f15d792330ccaf91be8ee4fdb54ce26835` during development; the 60 KB `nonnative_english.csv` downloads in 2 seconds with hash `sha256:04c7bc646d7d8630377bc336af2b4567a8be56d0e8da1f582c15989712dd51de` and is byte-identical to the HF path.
- The downstream pipeline is unchanged: `editlens_to_manifest.py --inspect` correctly enumerates the 9 columns of the GitHub-source CSV, and `--preset editlens_nonnative` consumes it without modification.
- Three-way version sync at 1.24.0. No new keyword.
- This release closes the friction gap between "the calibration toolchain shipped 13 releases ago" and "the maintainer's first calibration run." The full first-run sequence is now: `fetch_pangram_editlens_github.py --split nonnative_english --commit-sha <sha>` → `editlens_to_manifest.py --source <csv> --preset editlens_nonnative --out <manifest>` → `calibration_survey.py --manifest <manifest> --fpr-target 0.01 --out <survey>` → read the survey table → pick a winner → edit `COMPRESSION_HEURISTICS[<signal>]` → add a section to PROVENANCE.md → commit. Five commands plus one judgment call.

## [1.23.0] - 2026-05-09

Calibration-run readiness. The 1.10.0 calibration toolchain shipped without anyone ever running it; `thresholds_calibrated.json` has been `[]` for 13 releases and variance audits keep reporting "0 of 11 signal thresholds carry calibration provenance." The blockers were not the toolchain (which works) but two friction points the maintainer kept bumping into when sitting down to run the survey: PROVENANCE.md's documented shell loop enumerated only 7 of the 11 signals (silent coverage gap), and the survey-and-pick step required reading 11 separate JSON files to compare AUC / TPR / threshold values across candidates. This release closes both gaps with a survey wrapper. Running the calibration is still maintainer work — license-gated HF access, Tier 2/3 compute, judgment per the five selection criteria — but every step that doesn't require the maintainer's eyes is now one command away.

### Added

- **`scripts/calibration/calibration_survey.py`** — wraps `calibrate_thresholds.derive_threshold` over every key in `COMPRESSION_HEURISTICS` (all 11 signals), aggregates the per-signal AUC / AP / threshold / TPR / FPR / n_neg / fpr_resolution into a single ranked markdown table, and writes a JSON survey ledger for review. Coverage is 11/11 by construction (iterates the registry directly), removing the silent gap in the previous shell-loop incantation. Errors per signal are recorded in a separate table beneath the main one — a single bad signal (registry mismatch, unscored corpus, unreachable FPR target) doesn't abort the survey.
- **Automated gate evaluation** for four of the five PROVENANCE.md selection criteria. Per-row gate booleans:
  1. **Polarity matches** (gate 1) — pass when AUC ≥ 0.5 in the registry's declared direction; an AUC under 0.5 is the corpus inverting the registry's polarity, which is a *finding*, not a threshold to commit.
  3. **Enough negatives** (gate 3) — pass when `fpr_resolution = 1/n_neg ≤ fpr_target`. Already in the toolchain; surfaced visibly in the survey row.
  4. **Interpretable threshold** (gate 4) — pass when `tpr_at_threshold ≥ tpr_floor` (default 0.05; `--tpr-floor` overrides). A threshold that fires on 0.5% of positives is technically valid but operationally meaningless.
  5. **ESL conservative** (gate 5) — pass when the calibrated threshold is within `aggressiveness_tolerance` of the heuristic OR is in the *less-aggressive* direction (for `gt` signals: ≥ heuristic; for `lt` signals: ≤ heuristic). Direction-aware so the same logic catches both signal flavors.
  Gate 2 (AUC/AP not embarrassing) stays explicitly maintainer judgment. The survey surfaces AUC + AP for the maintainer to weigh; it never sets gate 2 to a boolean.
- **Ranked output.** Rows sort by descending pass-glyph count → descending AUC → descending TPR. Signals that pass every evaluable gate float to the top of the table; the maintainer reads from the top and weighs the gate-2 judgment call against the 1/3/4/5 booleans plus AUC + AP.
- **PROVENANCE.md updated.** The doc's step-4 survey instructions now invoke `calibration_survey.py` instead of the partial 7-signal `for sig in ...` loop. The shell loop missed `yules_k`, `shannon_entropy`, `sentence_length_sd`, and `mdd_sd` — a maintainer following the doc would have undercounted by 4 signals. Coverage is now structural: the wrapper iterates the registry, so adding a new signal to `COMPRESSION_HEURISTICS` automatically extends the survey.
- **29 new regression tests** in `scripts/tests/test_calibration_survey.py`: gate evaluation logic (every gate boolean × every direction × within/outside tolerance × pass/fail edge case), survey-runner robustness (records `SystemExit` and arbitrary exception types per-row without aborting), default coverage (surveying without `--signal` hits every key in `COMPRESSION_HEURISTICS`), explicit-signal-list filtering, ranking-by-gates-passed, output rendering (markdown table + error subtable + JSON ledger), CLI surface (every flag in `--help`, invalid `--fpr-target` rejected, unknown signal rejected), and a documentation-drift test that pins the PROVENANCE.md doc no longer carries the partial 7-signal incantation.

### Notes

- **377 tests pass + 2 skipped** (was 348 + 2 in 1.22.0; +29 new). The synthetic gate-evaluation tests use mocked `derive_threshold` returns so they exercise the aggregation/gate/rendering logic without spaCy or SBERT compute. The maintainer's actual calibration run still requires the labeled corpus + Tier 2/3 deps.
- The wrapper's run still requires HF access to Pangram EditLens (CC BY-NC-SA 4.0, license-gated) and a few hours of Tier 2/3 compute on the user's machine. The maintainer's prep is documented in the existing PROVENANCE.md steps 1–3 (install calibration deps → fetch corpus → convert to manifest); the survey wrapper replaces step 4.
- After the maintainer runs the survey, picks the first signal that passes all five criteria (gate 2 is the judgment call), edits `COMPRESSION_HEURISTICS[<signal>]` in `variance_audit.py`, and adds a markdown section to PROVENANCE.md, the existing 9 corpus-independent regression tests in `scripts/tests/test_calibration_provenance.py` will catch any drift across the four artifacts (registry / ledger JSON / PROVENANCE markdown / CHANGELOG-version-bump). The 10th test (corpus-dependent re-derive) will additionally verify the encoded threshold reproduces from the corpus when the maintainer has it available.
- Three-way version sync at 1.23.0. No new keyword.

## [1.22.0] - 2026-05-09

General-purpose corpus acquisition scaffold + LLM-driven adaptation skill. Adds three artifacts that together let a user (with LLM help) adapt the SETEC acquisition pipeline to a source the framework doesn't already cover — a Slack export, an Obsidian vault, an mbox file, a custom CMS, anything. The five existing acquisition scripts (`acquire_blog`, `acquire_blogger_takeout`, `acquire_magazine`, `pdf_inventory`, `pdf_extract`) all share the same six-step pipeline; this release factors that pattern into a reference doc, a starting-point template, and a skill that walks Claude through the adaptation workflow.

### Added

- **`references/acquire-corpus-pattern.md`** — the canonical reference for the acquisition pipeline. Documents the six-step pipeline (discover → extract → preprocess → dedupe → write → emit manifest), enumerates the 15+ shared helpers in `acquisition_core.py` (slugify, content_hash_already_present, html_to_text, AcquiredPiece, RunSummary, the Fetcher abstraction, etc.), pins the standard CLI flag conventions every acquisition script honors, lists the three pure source-specific functions (`discover_items`, `extract_one`, `parse_options`), specifies the testing pattern (fixtures + 5 invariant categories), gives an LLM-consumption workflow (read this doc + template, describe source, fill four TODO markers, dry-run, validate manifest, decide one-off vs. permanent), and lists worked example sources (Slack, Obsidian, Notion, mbox, Discord, custom CMS).
- **`scripts/acquire_corpus_template.py`** — a working scaffold a user copies to `acquire_<source>.py` and adapts. Standard CLI surface wired (every flag the existing five scripts share), `ProcessOptions` and `ItemMeta` dataclasses defined, the shared per-piece pipeline (`process_one_item` + `emit_piece`) implemented and ready to consume. The four `TODO(LLM)` markers — `discover_items`, `extract_one`, `build_arg_parser` source-specific additions, `parse_options` source-specific additions — raise `NotImplementedError` so a forgotten fill-in fails loudly rather than silently producing a no-op.
- **`skills/corpus-acquisition/SKILL.md`** — drives the LLM adaptation workflow. Trigger phrases: "add this to my corpus," "import these files into the impostor pool," "adapt acquire_blog.py for X," "build a corpus from my Slack export," etc. Six-step protocol: survey the source (where it lives, what format, what metadata, what consent posture, will there be enough text) → read the reference + template → adapt by filling the four TODO markers → dry-run with small `--max-items` → real run + validate manifest → decide one-off vs. permanent. Hard safety rules: don't acquire content the user can't articulate consent for, don't auto-push adaptations into the framework as permanent additions, don't silently update existing acquisition scripts when the request was "adapt." Concrete walked example: Slack export adaptation step-by-step.
- **20 new regression tests** in `scripts/tests/test_acquire_corpus_template.py`: template structure tests (file exists, imports cleanly, four TODO markers present, SOURCE_NAME placeholder visible, stubs raise NotImplementedError on call), dataclass shape tests (ItemMeta + ProcessOptions have the documented fields), CLI surface tests (every standard acquisition flag in `--help`, required flags rejection, minimal-args acceptance, default-output-dir resolution, acquired_via tag format), reference-doc tests (file exists, canonical headings present, all 15 acquisition_core helper names enumerated), skill-markdown tests (file exists, references both template and reference doc, six workflow steps in order, all five consent_status options enumerated, at least one concrete-example source mentioned).

### Notes

- **348 tests pass + 2 skipped** (was 328 + 2 in 1.21.0; +20 new). All run on stdlib + the plugin's own modules; the template's stubs raise NotImplementedError on call, so tests don't exercise discovery / extraction (those are filled in per-adaptation).
- Six skills now ship: voice-coherence, smoothing-diagnosis, validation, craft-restoration, metric-targeted-restoration, setup, **corpus-acquisition** (this release). The corpus-acquisition skill is the first SETEC skill that's primarily an LLM workflow — it doesn't run a script directly; it orchestrates a user-LLM-template adaptation cycle.
- The reference doc is structured for LLM consumption: every section the new script needs has its own heading, every helper has a one-line summary, every CLI convention is enumerated in a table. An LLM with the reference + template loaded into context has everything it needs to adapt the pattern to a new source.
- Three-way version sync at 1.22.0. No new keyword (the existing `impostor-corpus` and `general-imposters` keywords cover this skill's surface).

## [1.21.0] - 2026-05-09

First-run dependency surveying. Adds a `setup` skill + supporting `dependency_check.py` script that fixes the long-standing UX gap between fresh install and first successful diagnostic: a fresh SETEC install often hits "module not found" deep inside a pipeline because the user didn't realize the framework runs in four opt-in dependency tiers (core stylometry, acquisition, OCR, calibration). The setup skill catches the gap up front, surfaces what's missing, and asks for permission per tier before installing.

### Added

- **`scripts/dependency_check.py`** — surveys the user's environment across four tiers (core, acquisition, OCR, calibration) plus an optional power-up tier. Reports per-package state (installed vs. missing, version when present), per-spaCy-model state (`en_core_web_sm`), and per-system-binary state (`tesseract`, `gs`, `qpdf`). Exit code 0 = required deps all present; exit code 1 = something required is missing. Three output modes: human-readable (default), `--json` (for skill parsing), `--suggest` (platform-appropriate install commands for what's missing). Detection helpers (`check_python_dep`, `check_spacy_model`, `check_system_dep`) are pure functions with stable return shapes; the per-tier survey aggregates them. Platform detection (`detect_platform`) returns `macos` / `linux` / `windows` and the suggest mode picks Homebrew / apt / chocolatey commands accordingly.
- **`skills/setup/SKILL.md`** — drives the dependency-check workflow. Trigger phrases: "set up SETEC," fresh install, "ModuleNotFoundError" from a SETEC script, "what does this plugin need to run," "is everything installed," "first-time setup." The skill walks Claude through six steps: detect tier from request → run survey → show findings to user → ask for permission per tier → run authorized installs → verify. Hard safety rules: never bundle multiple tiers into one yes/no, never run `pip install` without explicit per-tier confirmation, never run `sudo apt-get` without per-command confirmation, never auto-install heavy deps (`sentence-transformers` ~2 GB) without surfacing the size cost, never modify the user's `requirements.txt`. The user owns the environment; the skill proposes, the user disposes.
- **Platform-specific install hints** for the three OCR system binaries: macOS uses Homebrew (`brew install tesseract ghostscript qpdf`), Linux uses apt or yum (`sudo apt-get install tesseract-ocr ghostscript qpdf`), Windows uses chocolatey (`choco install tesseract ghostscript qpdf`) or documented manual installer URLs (UB-Mannheim tesseract, Ghostscript releases page, qpdf SourceForge).
- **31 new regression tests** in `scripts/tests/test_dependency_check.py`: tier-registry well-formedness (every tier has the keys the renderers expect), per-tier dep-list contents (core has spaCy + en_core_web_sm + 4 packages; acquisition has 6; OCR has 3 system binaries; calibration has huggingface_hub + pyarrow), platform detection, detection helpers (Python module presence/absence, system binary presence/absence via `shutil.which`, spaCy-model load failure handling), survey aggregate stable shape, install-command suggestion correctness across macOS / Linux / Windows, render-human and render-suggest output sanity, CLI help surface, and skill-markdown discoverability tests (the SKILL.md exists, references `dependency_check.py`, mentions `CLAUDE_PLUGIN_ROOT`, and covers all three platforms).

### Notes

- **328 tests pass + 2 skipped** (was 297 + 2 in 1.20.0; +31 new). All run on stdlib + the plugin's own modules; no acquisition deps required.
- The setup skill is **declarative**: Claude reads it on plugin load and invokes `dependency_check.py` when the user's request matches the trigger phrases. It does not run automatically on every conversation start — only when the user signals a need (which is the right posture; the framework should not interrupt unrelated work to nag about deps).
- Three-way version sync at 1.21.0. No new keyword (the existing `validation-harness` and `calibration` keywords cover this skill's surface).
- All five SETEC skills now ship: voice-coherence, smoothing-diagnosis, validation, craft-restoration, metric-targeted-restoration, **setup** (this release).

## [1.20.0] - 2026-05-09

Author-facing voice insights report generation. Closes the v2 deliverable from the 2026-05-08 spec: `generate_voice_report.py` consumes the JSON outputs of `voice_profile.py`, `voice_drift_tracker.py`, and `idiolect_detector.py` and emits a markdown report shaped like the canonical template at `references/templates/voice_insights_report.template.md`. The architectural split the framework considers load-bearing — numerical sections populated programmatically, interpretive sections emitted as `{TODO: interpret}` markers for an LLM/human editorial pass — is enforced by a hard test invariant (`test_no_auto_prose_in_interpretive_sections`).

### Added

- **`scripts/generate_voice_report.py`** with three report shapes auto-selected by which inputs are present:
  - **Profile-only.** `--voice-profile` only. Sections: Header, Durable voiceprint, Idiolectic vocabulary, Three observations, What this cannot say, What's distinctive.
  - **Profile + drift.** Adds an Era / drift section if `--voice-drift` is supplied.
  - **Profile + drift + comparison.** Adds a Comparison-to-control section if `--comparison-drift` is supplied alongside `--voice-drift`.
- **Numerical sections populated automatically** from the corresponding JSON outputs:
  - Header counts (`baseline_summary.n_files`, `total_words`, optional date range from drift periods).
  - Durable voiceprint tables: per-family `most_stable_features` filtered to features whose CV is at or below `--cv-ceiling` (default 0.10) and whose mean is non-zero.
  - Idiolect tables: phrases from `idiolect_detector.py` n=1, n=2, n=3 outputs aggregated and split into topic-domain vs. rhetorical-move buckets via a leading-function-word + stopword-ratio heuristic (the editor can rebalance after reading the tables).
  - Drift cross-period magnitudes: weighted Burrows-Delta + cosine distance per period pair.
  - Drift drifting/stable feature lists: per-family with CV and mean values.
  - Comparison headline magnitudes: subject's vs. control's max-pair Burrows-Delta. The framing reflects the spec's calibration finding that drift magnitude alone is not diagnostic — drift shape is.
- **Interpretive sections emit `{TODO: interpret: <hint>}`** markers carrying enough context (which feature, which direction, which magnitude) for an LLM/human editor to write the prose downstream without re-reading the source JSON. Sections that are entirely manual: durable-voiceprint prose, idiolect interpretation (topic vs. voice), drift cluster paragraphs, comparison diagnostic signatures, three observations, what's distinctive. The `cannot say` section is template boilerplate with substitutions for `--register` and `--ai-disclosure`.
- **`references/templates/voice_insights_report.template.md`** moved from `internal/templates/` so it ships with the plugin install. The script reads the default template path from `${CLAUDE_PLUGIN_ROOT}/references/templates/...`; users can supply a custom template via the existing `--out` redirect pattern (the script writes the populated report; users compare against the template themselves).
- **Privacy guard.** Reports contain voiceprint signatures — voice-cloning input. Default `--out` paths must live under `ai-prose-baselines-private/`; the marker-based check refuses non-private output unless `--allow-public-output` is set. Stdout is allowed without the override flag for interactive use (the user is the audience).
- **Synthetic JSON fixture set** under `plugins/setec-voiceprint/scripts/test_data/voice_report_fixture/`: `voice_profile.json`, `voice_drift.json`, `idiolect_n1.json`, `idiolect_n2.json`, `control_drift.json`. All shaped to match the real script outputs; fixture values are illustrative (not real corpus measurements).
- **38 new regression tests** in `scripts/tests/test_generate_voice_report.py`: helper unit coverage (`todo` marker format, `_format_value` buckets, `_baseline_summary` defensive read, `_stable_features` CV filter and zero-mean drop, idiolect aggregation across n-gram sizes, topic-vs-rhetorical split heuristic, date-range extraction); per-section renderer coverage (header counts + disclosure block visibility, durable voiceprint table + TODO + thin-corpus fallback, idiolect topic + rhetorical tables, drift cross-period table + drifting/stable summaries, comparison headline magnitudes); full-report shape coverage (all section headers present when all inputs supplied, optional sections omitted when inputs absent, no auto-generated prose in interpretive sections, blank-line collapse, trailing newline); end-to-end `run()` coverage (writes to `--out` with privacy guard, stdout fallback, profile-only invocation, missing-input exit code); CLI surface coverage (every flag in `--help`, argparse rejects missing required flags).

### Notes

- **297 tests pass + 2 skipped** (was 259 + 2 in 1.19.0; +38 new). Tests do not require any acquisition deps; only stdlib + the plugin's own modules.
- The framework's deepest principle is encoded as a test invariant: `test_no_auto_prose_in_interpretive_sections` asserts that the Three Observations and What's Distinctive sections each emit exactly 3 `{TODO: interpret}` markers and contain no auto-generated prose paragraphs. Future revisions of the section renderers must preserve this contract.
- The script's TODO hints are designed for LLM consumption: each carries the section's purpose, the feature/phrase names from the data the editor should reference, and (where applicable) the direction or magnitude they should comment on. Users running an LLM pass over the report can paste the report verbatim into their editor and the model will have enough context to fill the TODOs.
- Three-way version sync at 1.20.0 across `plugin.json :: version`, `marketplace.json :: metadata.version`, `marketplace.json :: plugins[0].version`. New keyword `voice-insights-report` added to both files.
- The 2026-05-08 impostor-corpus spec's v1 lanes (live blog, Blogger Takeout, PDF library, online magazine) all shipped 1.15.0 / 1.17.0 / 1.18.0 / 1.19.0. The v2 voice-insights deliverable now ships in 1.20.0. Remaining roadmap items: General Imposters validation harness wireup, additional magazine modules, frequency-table-only acquisition mode, calibration toolchain extensions (RAID + MAGE benchmark fetchers).

## [1.19.0] - 2026-05-09

Online literary-horror magazine acquisition. Closes the second v1 acquisition lane from `internal/2026-05-08-impostor-corpus-spec.md`: site-specific scraper modules for Nightmare and The Dark behind a uniform CLI, producing register-matched literary-horror prose for the General Imposters impostor pool. With this release, **all four v1 acquisition lanes ship** — live blog (1.15.0), offline Blogger Takeout (1.17.0), PDF library (1.18.0), and online magazines (1.19.0).

### Added

- **`scripts/acquire_magazine.py`** — site-specific scraper modules behind a uniform CLI (`--magazine {nightmare,the_dark}`). Each module is a `MagazineConfig` dataclass holding archive URL, issue/story link selectors, story content selector, byline / title / date selectors, and a `strip_after_selector` for post-body cruft (the Nightmare "Author Spotlight" interview block, The Dark's ebook-purchase widget). Same pipeline architecture as `acquire_blog.py`: shared `acquisition_core.Fetcher` abstraction, fixture-driven CI, `preprocessing.py` corpus-hygiene gate, content-hash dedupe, marker-based privacy guard, robots.txt honored by default.
- **Two magazine modules in v1**, both running on WordPress with similar markup: **Nightmare** (`nightmare-magazine.com`) with `.entry-content` body extraction and `#author-spotlight` strip-after; **The Dark** (`thedarkmagazine.com`) with `.entry-content` body extraction and ebook-widget / post-bottom strip-after. Strange Horizons / Apex / Clarkesworld / Lightspeed defer to v2 unless a future maintainer adds the relevant config entry.
- **Per-author persona slugs.** `--persona-from-author` (default) mints one slug per author following the documented `lastname_firstname_personal` rule (`acquisition_core.author_to_persona_slug`). The impostor pool is per-author sliceable downstream — useful for the General Imposters harness when calibrating against named writers (Brian Evenson, Carmen Maria Machado, Kelly Link). `--persona STRING` overrides and lumps every story under one slug; rarely useful for impostor work but supported per spec.
- **Author filter.** `--filter-author` is a case-insensitive substring match against the byline text (after stripping the leading `By ` prefix magazines often add). Multiple author names match any of them. Filter applied at issue-TOC discovery (when bylines are present in the TOC) AND on the story page (the canonical byline source) so a TOC truncation can't slip a filtered-out story through.
- **Author-name cleanup helper.** `_clean_author` strips `By ` / `by ` prefixes that live under `.byline` selectors but not under `.author` anchors, and `_select_text` now iterates a CSS comma-list per-selector in order so the more-specific descendant selector beats the parent (`.author` chosen over `.byline a` in document-tree order). The combined effect: extracted authors match what the user types in `--filter-author`, and `acquisition_core.author_to_persona_slug` produces the documented `lastname_firstname_personal` slug.
- **Magazine-specific URL patterns.** Story href patterns explicitly exclude `/author/<x>/`, `/issues/<x>/`, `/category/`, `/tag/`, `/wp-content/`, `/wp-includes/`, `/feed/`, `/page/` so author-profile and category-archive links inside issue-TOC pages don't cause spurious story-fetch attempts. The Nightmare module restricts to `/fiction/<slug>/`; The Dark uses a broader `/<slug>/` with the negative-prefix list.
- **Fixture corpus** under `plugins/setec-voiceprint/scripts/test_data/acquisition_magazine_fixture/`: Nightmare archive + 2 issue pages + 3 story pages (one with the canonical Author Spotlight strip case, one by a filtered-out author for `--filter-author` exclusion testing); The Dark archive + 1 issue page + 1 story page (with ebook widget and post-bottom strip-after cases). All synthetic prose; no real third-party content.
- **23 new regression tests** in `scripts/tests/test_acquire_magazine.py`: module wiring (both magazines registered with the documented archive URLs); helper unit tests (`_clean_author` strips `By ` prefix, `_select_text` prefers more-specific selectors, author-filter substring match handles `By X` and `X` byline formats); discover/parse helpers (issue-archive self-link filtered out, story metadata correctly extracted from TOC, Author Spotlight strip-after works, date parsed from `<time datetime>` attribute); Nightmare end-to-end (3 stories from 2 issues across 3 author subdirs, all with impostor schema fields); `--filter-author` excludes other writers; substring match matches byline-with-prefix; persona slug determinism; explicit `--persona` lumps; The Dark end-to-end (1 story, ebook widget stripped, post-bottom stripped); The Dark href pattern excludes author-profile URLs; `--since`/`--until` date window; privacy-guard refusal; within-persona dedupe; dry-run no-write; CLI help surface; argparse rejects missing `--impostor-for` and unknown magazine; emitted manifest validates clean against the schema.

### Notes

- **259 tests pass + 2 skipped** (was 236 + 2 in 1.18.0; +23 magazine tests). Tests skip cleanly when `bs4` isn't installed (i.e., users who haven't run `pip install -r requirements-acquisition.txt`).
- All four v1 acquisition lanes from the 2026-05-08 impostor-corpus spec now ship. Beyond v1: `generate_voice_report.py` (consumes the existing `internal/templates/voice_insights_report.template.md` and emits programmatic numeric sections + `{TODO: interpret}` markers for the LLM/human pass), additional magazine modules (Strange Horizons, Apex, Clarkesworld, Lightspeed), and frequency-table-only acquisition mode all remain roadmap-tracked.
- Three-way version sync at 1.19.0 across `plugin.json :: version`, `marketplace.json :: metadata.version`, `marketplace.json :: plugins[0].version`. New keyword `literary-horror` added to both files.

## [1.18.0] - 2026-05-09

PDF library inventory + extraction. Closes the third v1 acquisition lane from `internal/2026-05-08-impostor-corpus-spec.md`: a paired `pdf_inventory.py` / `pdf_extract.py` workflow that turns an existing PDF library — typically academic papers, photocopied chapters, downloaded preprints — into impostor-pool entries. With this release, all three v1 acquisition lanes ship: live blog (1.15.0), offline Blogger Takeout (1.17.0), and PDF library (1.18.0).

### Added

- **`scripts/pdf_inventory.py`** — walks a directory, opens every PDF, samples the first 5 pages, and emits a JSONL row per file with classification (`text_extractable` / `image_only` / `mixed` / `corrupted`), metadata quality (`good` / `partial` / `none`), title / author / creation_date when present, page count, estimated full-document word count (linear extrapolation from the sample), an OCR-layer heuristic (text + images on the same page suggest a prior OCR pass), and the file's SHA-256 for cross-row deduplication. Glob include/exclude patterns, max-files cap, configurable per-file size limit (default 200 MB), and optional thread-pool parallelism. Output is deterministic in input order — re-running against the same library produces a row-identical JSONL so reviewers can diff cleanly.
- **`scripts/pdf_extract.py`** — reads a filtered inventory JSONL and emits cleaned text + draft manifest entries. Text-extractable PDFs go through `pypdf` page-by-page (concatenated with double-newlines so paragraph structure survives); image-only / mixed PDFs go through `ocrmypdf` (force-OCR with deskew/despeckle, configurable language and DPI). Both paths pipe extracted text through `preprocessing.py` for the same corpus-hygiene gate identity baselines and live blog acquisition use. Per-piece output: `<output-dir>/<persona-slug>/<YYYY-MM-DD>_<title-slug>.txt` plus `.meta.json` sidecar; manifest entries carry `acquired_via: pdf_extract_<text_layer|ocrmypdf>_<date>`, `source_file_hash` (the original PDF's SHA-256, for traceability back through the inventory), and the impostor-required field block read from the inventory row.
- **OCR layer is opt-in.** `ocrmypdf` is a soft dependency. When the package or its system binaries (`tesseract`, `gs`, `qpdf`) are missing, `pdf_extract.py` reports the missing component on stderr at the start of the run and skips every image-only / mixed entry — no spurious failures. Pass `--skip-ocr` to silence the notice and acknowledge the skip explicitly. Install on macOS: `pip install ocrmypdf && brew install tesseract ghostscript qpdf`.
- **Inventory ↔ extract contract.** `pdf_inventory.py` is deliberately the **review surface** between an opaque PDF library and the impostor pool — it never writes cleaned text and never emits manifest entries. Between inventory and extract, the user filters rows (drop unwanted topics, image-only entries that aren't worth OCR cost, corrupted files) and adds the impostor metadata fields the manifest validator requires (`persona`, `register`, `register_match`, `topic_match`, `consent_status`, `era`, `impostor_for`). `pdf_extract.py` validates that block per-row and skips any entry where it's incomplete — so the validator's later check at manifest-load time can't fail for impostor-required-fields reasons. README documents a `jq` recipe for bulk-annotating filtered inventories.
- **Within-author deduplication by content hash.** Two PDFs of the same essay (a journal preprint and a republished collection version, for example) hash identically after preprocessing. The first one wins; the second is skipped with a `duplicate hash; skipping` stderr line and recorded in the run summary. Same architecture as `acquire_blog.py` and `acquire_blogger_takeout.py`.
- **Privacy guard on both scripts.** Inventory output and extracted text both go under `ai-prose-baselines-private/` by default. Marker-based check (any path component named `ai-prose-baselines-private` qualifies, repo-internal or sibling). `--allow-public-output` is required to opt out and emits the same refusal message used by the voice-coherence tools.
- **Fixture corpus** under `plugins/setec-voiceprint/scripts/test_data/pdf_inventory_fixture/` (under 8 KB total, well below the spec's 1 MB cap): `text_layer_with_metadata.pdf` (born-digital with title / author / date), `text_layer_without_metadata.pdf` (same content, metadata stripped), `image_only.pdf` (vector-drawn page, zero text operators), `corrupted.pdf` (PDF magic header + deterministic byte pattern that triggers a `pypdf` failure). All synthetic prose; no real third-party content. Includes a `_make_fixtures.py` rebuild script (uses `reportlab`, dev-time only — fixtures are committed prebuilt so CI doesn't need it).
- **27 new regression tests** in `scripts/tests/test_pdf_inventory_extract.py`: classification branch coverage (every threshold + the corrupted-doesn't-raise contract), metadata-quality bucketing, PDF date parsing across format variants, glob filtering / max-files / deterministic order in `discover_pdfs`, end-to-end inventory writer with one row per PDF and the documented schema, privacy-guard refusal, end-to-end extract producing text + manifest, missing-impostor-field skip, corrupted-row skip, dry-run no-write, `--skip-ocr` clean skip, OCR availability check returning the documented tuple, and an integration test that runs the extracted manifest through `validate_manifest` and asserts zero errors when an identity_baseline entry naming the impostor target persona is added.

### Notes

- **236 tests pass + 2 skipped** (was 209 + 2 in 1.17.0; +27 PDF tests). Tests skip cleanly on systems without `pypdf` installed (i.e., users who haven't run `pip install -r requirements-acquisition.txt`).
- **OCR tests pass whether or not OCR deps are installed** — the suite verifies the `--skip-ocr` path and the OCR-availability hook independently of whether `ocrmypdf` is present. A maintainer-side smoke test against a real image-only PDF is documented in the spec but not part of CI.
- Three-way version sync at 1.18.0 across `plugin.json :: version`, `marketplace.json :: metadata.version`, `marketplace.json :: plugins[0].version`. New keyword `pdf-extraction` added to both files.
- All v1 acquisition lanes from the 2026-05-08 impostor-corpus spec are now shipped. The remaining piece is `acquire_magazine.py` (Nightmare + The Dark modules behind a uniform CLI), which is the same shape as `acquire_blog.py` and is independently shippable. Beyond v1, the v2 deliverables (`generate_voice_report.py`, `acquire_magazine.py` follow-on magazines, frequency-table-only acquisition mode) remain roadmap-tracked.

## [1.17.0] - 2026-05-09

Offline Blogger Takeout acquisition for the impostor corpus. This is the local
archive sibling to `acquire_blog.py`: when an author shares a Google Takeout
Blogger export, SETEC can now import the full `Blogger/Blogs/*/feed.atom`
payload without scraping the live site or being capped by Blogger's public feed
limits.

### Added

- **`scripts/acquire_blogger_takeout.py`** — imports a Takeout root, `Blogger/`
  directory, or single `feed.atom` file into the standard impostor-pool artifact
  shape: cleaned `.txt` files, `.meta.json` sidecars, and a draft manifest with
  `corpus_role: "impostor"`, `use: ["voice_impostor"]`, content hashes, consent
  posture, era, `impostor_for`, and `acquired_via`.
- **Comment feeds excluded by default.** The importer reads
  `Blogger/Blogs/*/feed.atom` and ignores `Blogger/Comments/*/feed.atom` unless
  `--include-comments` is passed explicitly. Comment feeds are a different
  register and may contain conversational context or other people's prose.
- **Blogger-specific provenance in sidecars.** Each `.meta.json` records the
  Blogger entry id, stable short id, labels, update timestamp, and source feed
  path. Untitled Blogger entries are retained with stable
  `untitled-<post-id>` filenames so same-day titleless posts do not overwrite
  each other.
- **Fixture coverage.** `scripts/test_data/blogger_takeout_fixture/` adds a
  synthetic Takeout-shaped export with a blog feed, comment feed, titleless
  entry, out-of-window entry, and too-short entry. `test_acquire_blogger_takeout.py`
  covers feed discovery, comment-feed refusal, titleless-entry retention,
  locator-only body skipping, end-to-end manifest emission, and required
  `--impostor-for`.
- **Docs.** `scripts/README.md` now documents the Takeout importer and explains
  why it is preferred over live Blogger feed acquisition when a Takeout archive
  is available.

### Notes

- Manual private smoke run against a shared Blogger Takeout archive produced
  463 acquired posts, 463 sidecars, a validator-clean draft manifest with 0
  errors, and 321,206 cleaned words after `--until 2022-11-01` and
  `--min-words 250`. The only manifest warnings were expected standalone-draft
  `impostor_for` warnings because the target identity baseline is not included
  in the draft manifest.
- 209 tests pass + 2 skipped (was 203 + 2 in 1.16.0; +6 Blogger Takeout tests).

## [1.16.0] - 2026-05-09

Plugin packaging fix: scripts now ship with the plugin install. Pre-1.16.0, `scripts/`, `references/`, and `requirements*.txt` lived at the repo root and the plugin dir at `plugins/setec-voiceprint/` only contained `.claude-plugin/plugin.json` + 5 SKILL.md files. SKILL.md script paths used `${CLAUDE_PLUGIN_ROOT}/../../scripts/`, which assumes the marketplace install ships the whole repo — but in practice it ships only the plugin source dir. Result: a fresh marketplace install of setec-voiceprint had `voice_distance.py`, `acquire_blog.py`, every other script, and every reference doc missing. Users would invoke a skill, follow its example command, and hit `python3: can't open file '.../scripts/voice_distance.py': No such file or directory`.

This is a structural fix to ship scripts inside the plugin where every other plugin in the same marketplace ecosystem keeps them (APODICTIC's `plugins/apodictic/scripts/` and `skills/<name>/scripts/` are the model). The MINOR bump is for the structural change; no behavior changes for existing dev-checkout workflows because top-level paths are preserved via symlinks.

### Changed

- **Scripts moved into the plugin directory.** `scripts/` → `plugins/setec-voiceprint/scripts/`; `references/` → `plugins/setec-voiceprint/references/`; `requirements.txt` / `requirements-acquisition.txt` / `requirements-calibration.txt` → `plugins/setec-voiceprint/`. Files moved with `git mv` so blame history is preserved.
- **Top-level paths preserved as symlinks.** `<repo>/scripts -> plugins/setec-voiceprint/scripts`, same pattern for `references` and the three `requirements*.txt`. Git stores symlinks (mode `120000`); `python3 scripts/foo.py`, `pip install -r requirements.txt`, and every existing dev workflow keeps working from the repo root with no changes.
- **SKILL.md script paths updated.** `${CLAUDE_PLUGIN_ROOT}/../../scripts/foo.py` → `${CLAUDE_PLUGIN_ROOT}/scripts/foo.py` across all five skills (44 total replacements). After this change, the path is correct under both the dev-checkout layout (where `${CLAUDE_PLUGIN_ROOT}` is `<repo>/plugins/setec-voiceprint/`) and the marketplace install (where it's `<install-root>/plugins/setec-voiceprint/`).
- **`parents[N]` indices updated** in scripts that resolved their location relative to repo root: `scripts/acquisition_core.py:resolve_baselines_dir` (was `parents[1]`, now `parents[3]`); `scripts/calibration/calibrate_thresholds.py`, `editlens_to_manifest.py`, `fetch_pangram_editlens.py` (were `parents[2]`, now `parents[4]`). The new indices reflect that scripts now live two levels deeper under `plugins/setec-voiceprint/`.

### Notes

- 203 tests pass + 2 skipped (unchanged from 1.15.3). Verified via both `python3 -m pytest scripts/tests/` (top-level symlink path) and `python3 -m pytest plugins/setec-voiceprint/scripts/tests/` (canonical path).
- Marketplace browsers should now resolve to `1.16.0` and the scripts should be present at `${CLAUDE_PLUGIN_ROOT}/scripts/...` on a fresh install.
- Three version fields stay in sync at 1.16.0: `plugin.json :: version`, `marketplace.json :: metadata.version`, `marketplace.json :: plugins[0].version`.
- Symlink behavior across platforms: macOS and Linux honor symlinks transparently. Windows users would need `core.symlinks=true` (default-on with admin / dev-mode); for users who can't do that, the canonical paths under `plugins/setec-voiceprint/` continue to work without symlinks.

## [1.15.3] - 2026-05-09

Five reviewer-flagged P2 fixes against `acquire_blog.py` + `acquisition_core.py` (1.15.0). The cluster is exactly where impostor-corpus tooling needs to be honest: completeness of Substack archive capture, robots.txt and user-agent honesty, paywall handling on direct-HTML fetches, and manifest validity at exit. None of these change cleaned-text shape or downstream semantics; they all close gaps where the script could silently miss content, misrepresent itself to upstream sites, or emit drafts that immediately fail validation.

### Fixed

- **Substack sitemap-index parsing.** `acquire_blog.parse_sitemap_urls` only inspected `<url>` nodes, but most Substack `/sitemap.xml` responses are `<sitemapindex>` documents whose children are `<sitemap><loc>` daughter pointers. The pre-fix function returned an empty list on indexes; the daughter-fetch fallback in `acquire_substack` checked for "sitemap" in the URL basename of returned pairs and never fired because the pairs list was empty. Result: archive-only posts published before the feed window were silently invisible. Fixed by accepting both `<url>` and `<sitemap>` element kinds — they share the same `<loc>` / `<lastmod>` shape, so the parser handles them uniformly and the caller's existing daughter-detection logic now sees the daughter URLs and recurses into them. Two new regression tests: a unit test on `parse_sitemap_urls` with a synthesized index payload, and an end-to-end Substack acquisition where the top-level sitemap is an index pointing at a daughter sitemap that contains the only copy of a post.
- **Robots.txt user-agent-specific disallow honored.** `acquisition_core.Fetcher._robots_allows` returned `rp.can_fetch(self.user_agent, url) or rp.can_fetch("*", url)`. `urllib.robotparser.can_fetch()` already implements user-agent matching with a `*` fallback when no specific block matches; the explicit `or "*"` check overrode any UA-specific disallow with the open `*` block. A site that wrote `User-agent: setec-voiceprint / Disallow: /` while leaving `User-agent: * / Allow: /` would still be scraped — exactly the user-specific opt-out we shouldn't bypass. Fixed by calling `rp.can_fetch(self.user_agent, url)` once. Two new regression tests: a unit test on `_robots_allows` with a synthesized robots parser, and an end-to-end test where the fixture's robots.txt names our UA explicitly and our run produces zero acquired files even though `*` is allowed.
- **`--user-agent` CLI flag is now actually used.** The CLI exposed `--user-agent`, but `run()` constructed the production fetcher via `make_requests_fetcher(version, rate_limit_seconds)` — no `user_agent` parameter, no threading. Live HTTP requests AND robots.txt checks both used the framework's default UA regardless of what the user passed. Fixed by adding a `user_agent` keyword argument to `make_requests_fetcher` (None defaults to `DEFAULT_USER_AGENT.format(version=version)`) and passing `args.user_agent` from `run()`. Two new regression tests verify the override path and the version-formatted default.
- **Paid Substack posts on the direct-HTML path are skipped.** Feed-entry items already get the `_is_paid_excerpt` check via `FeedItem.is_paid` in pass 1 of `acquire_substack`, but pass 2 (sitemap-only posts fetched as raw HTML) sent the post body straight to `process_one_post`. A paid Substack page served as HTML carries the same paywall markers (`paywall` / `subscriber-only` classes, "Subscribe to read" footer, "This post is for paid subscribers" body) but with the actual essay stripped — the previous flow could write the subscription wrapper as a real impostor entry. Fixed by running `_is_paid_excerpt(post_result.text, {})` on the fetched HTML before processing; matches increment `skipped_paid` and log to the skip ledger. New regression test uses a paid-page HTML fixture (paywall markers + minimal body) reachable only via a sitemap pointing at it; the run must produce zero `.txt` files for that URL.
- **`--impostor-for` is required at argparse time.** The flag previously defaulted to `[]`. Every entry `acquire_blog.py` emits has `corpus_role: "impostor"` hardcoded, and `manifest_validator` errors on impostor entries with empty / missing `impostor_for`. The pre-fix flow let users run a full acquisition (spending the network rate-limit budget on, e.g., 50 fetches), exit with code 0, and only discover the manifest was invalid when they ran the validator afterward. Fixed by switching to `required=True` so argparse rejects the missing flag immediately, before any fetch. Two new regression tests verify both the rejection path and that the normal-use path still works.

### Notes

- 203 tests pass + 2 skipped (was 194 + 2 in 1.15.2; +9 new regression tests across the five fixes).
- No cleaned-text format changes. Pre-1.15.3 acquisition runs are still valid; the fixes affect what gets acquired (more, in the sitemap-index case; less, in the paid-post and UA-disallow cases) and the manifest-validity gate (now caught at argparse time).
- One additional fixture file (`substack_sitemap_index.xml`, `substack_sitemap_daughter.xml`, `substack_post_only_in_daughter.html`, `substack_sitemap_paid_only.xml`, `substack_post_paid_html.html`, `robots_disallow_specific.txt`) covers the new test paths.

## [1.15.2] - 2026-05-09

Marketplace version-field completeness. The Claude Code marketplace UI reads version from `marketplace.json`'s `metadata.version` and `plugins[*].version` — not from the source-pointed `plugin.json` — so without those fields, marketplace browsers show stale or fall-through values regardless of how often `plugin.json` bumps. The APODICTIC plugin's `marketplace.json` carries both fields; setec-voiceprint's didn't. New installs were showing 1.7-era metadata as a result.

### Fixed

- `.claude-plugin/marketplace.json`: added `metadata.version` (top-level) and `plugins[0].version` (per-plugin). Both pinned to `1.15.2` and kept in sync with `plugin.json` going forward.
- Cross-check: `python3 -c "..."` script verifies that `plugin.json`'s `version` equals `marketplace.json`'s `metadata.version` equals `marketplace.json`'s `plugins[0].version`. The release process should keep all three locked together; future drift is the same kind of bug that produced the 1.7 fall-through.

### Notes

- Three version fields total: `plugins/setec-voiceprint/.claude-plugin/plugin.json :: version`, `.claude-plugin/marketplace.json :: metadata.version`, `.claude-plugin/marketplace.json :: plugins[0].version`. All bumped together every release.
- No code changes; metadata only. 194 tests pass + 2 skipped (unchanged from 1.15.1).

## [1.15.1] - 2026-05-09

Marketplace metadata catch-up. The `description` field in `.claude-plugin/marketplace.json` had been drifting since the cathedral upgrades landed (1.10.x onward) — it stopped at "MVP empirical validation against labeled corpora" and never picked up voice drift, per-POV voiceprints, restoration packets, before/after verdicts, calibration toolchain, or impostor-corpus acquisition. Marketplace browsers were seeing a stale feature list. The repo carries no separate version field on marketplace.json, but the plugin description is what users see when they search the marketplace, so this is a real surface to keep current.

### Changed

- `.claude-plugin/marketplace.json`: top-level `metadata.description` and per-plugin `description` rewritten to match the current feature surface (voice-coherence including drift / per-POV / impostor pool, validation including voice-validation harness and calibration toolchain, craft-restoration including before/after verdicts, blog acquisition for the impostor pool).
- Both `plugin.json` and `marketplace.json` now share the same expanded `keywords` list: `voice-drift`, `pov-analysis`, `impostor-corpus`, `general-imposters`, `craft-restoration`, `calibration` added alongside the existing eight. The two files are kept in sync so marketplace search results match the installed plugin's metadata.

### Notes

- No code or test changes; documentation/metadata only. 194 tests pass + 2 skipped (unchanged from 1.15.0).
- Future releases should keep `marketplace.json`'s `description` and `keywords` in sync with `plugin.json` whenever a feature lands. A small lint at release time (or a pre-commit hook) would catch drift earlier; deferred to a maintenance pass.

## [1.15.0] - 2026-05-09

Blog acquisition tooling for the impostor corpus. Commit 2 of three for the impostor-corpus spec (`internal/2026-05-08-impostor-corpus-spec.md`); Commit 1 (1.14.3) shipped the schema, Commit 3 (`acquire_magazine.py` + `pdf_inventory.py` + `pdf_extract.py`) follows. The General Imposters validation harness still has to be wired up separately, but with this release the framework can now build the impostor pool the harness needs from any Substack, WordPress / Ghost blog, or generic-HTML archive.

### Added

- **`scripts/acquire_blog.py`** — single-author blog/Substack archive acquisition with auto-detection across four extraction paths:
  1. **Substack** (`*.substack.com` or Substack-shaped feed at `<url>/feed`) — RSS for recent posts (full text) plus `sitemap.xml` for the full archive. Paid-only posts are detected via class markers / `audience: only_paid` and skipped with a flag; v1 ships no `--include-paid` because authenticated fetch is out of scope.
  2. **WordPress / Ghost** (responds with WP/Ghost-shaped feed at `/feed/` or `/rss/`) — feed parse plus a per-post HTML fetch when feed body looks short.
  3. **Generic HTML archive** (no recognizable feed) — requires `--archive-pattern` pointing at the index page; default link heuristic catches `/YYYY/MM/`-style and `/posts/` URLs.
  4. **Wayback Machine** (`--wayback`) — uses the CDX API to enumerate snapshots within the date window; fetches the most recent snapshot per URL.
- **CLI surface** mirrors the spec: `--persona`, `--impostor-for`, `--register`, `--register-match`, `--topic-match`, `--consent-status`, `--era`, `--since` / `--until`, `--max-posts`, `--rate-limit`, `--user-agent`, `--dry-run`, `--emit-manifest`, `--output-dir`, `--out`, `--allow-non-prose` / `--strip-rules` / `--strip-aggressive` (passed through to `preprocessing.py`), `--allow-public-output` (privacy guard override), and source-type override flags (`--substack` / `--wordpress` / `--html-archive` / `--wayback`). Site-config registry seeded for `marginalrevolution.com`, `slatestarcodex.com`, `overcomingbias.com`, `jehsmith.substack.com`, `thedarkmagazine.com`.
- **Per-piece output convention.** Each acquired post produces `<output>/<YYYY-MM-DD>_<title-slug>.txt` (cleaned text) plus a `.meta.json` sidecar (URL, date, hash, raw byte length, scraper version, full preprocessing metadata block). Default output dir is `<baselines>/impostors/<register>/<author_slug>/`; baselines root resolves through `$SETEC_BASELINES_DIR`, then the documented sibling-of-repo `ai-prose-baselines-private/`, then a fallback under `~/Documents/`. Draft manifest written to `<output>/draft_manifest.jsonl` by default; user merges into `corpus_manifest.jsonl` after review.
- **Impostor manifest emission.** Every emitted entry carries `corpus_role: "impostor"`, `use: ["voice_impostor"]`, `split: "baseline"`, `privacy: "private"`, plus all five impostor-required fields (`impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`) and `acquired_via` keyed by source-type and date (e.g. `acquire_blog_substack_rss_2026-05-09`). `content_hash` (SHA-256 of cleaned text, prefix `sha256:`) populated for dedupe.
- **`scripts/acquisition_core.py`** — shared helpers for the impostor-corpus pipeline. Will be reused by Commit 3's magazine + PDF tools:
  - `slugify` (Unicode-folded ASCII slug with `max_length` and word-boundary trim) and `author_to_persona_slug` (deterministic `lastname_firstname_<suffix>` with collision suffixes).
  - `compute_content_hash` (SHA-256 with `sha256:` prefix, matching the manifest schema).
  - `parse_iso_date` (anchored `YYYY[-MM[-DD]]` + python-dateutil fallback for human formats; returns `None` rather than raising on garbage).
  - `is_private_safe_path` and `check_output_privacy` — marker-based privacy guard mirroring `voice_profile.is_private_output_path` (any path component named `ai-prose-baselines-private` qualifies; repo-internal, sibling, and absolute paths all pass).
  - `Fetcher` abstract base + `FixtureFetcher` (test mock; URL → fixture-file mapping) + `make_requests_fetcher` (production wrapper around `requests` with rate limiting per host, robots.txt enforcement via `urllib.robotparser`, and SETEC user-agent header). The fetcher abstraction is what lets the regression tests run without network access.
  - `html_to_text` (BeautifulSoup with lxml backend, drops `<script>` / `<style>` / `<nav>` / `<aside>` / `<footer>` / `<form>` / `<svg>` globally, plus user-supplied strip selectors, then restricts to a CSS content selector with sensible fallbacks). `html_text_is_clean` is the corresponding test predicate that asserts no HTML tags survived.
  - `AcquiredPiece` dataclass (one acquired text artifact) + `RunSummary` dataclass (acquisition-run aggregate; renders the `Acquired: N files / Skipped (paid-only): N / ...` block on stderr).
  - `write_piece` (atomic `.txt` + `.meta.json` write), `content_hash_already_present` (within-output-dir dedupe scan), `compose_manifest_entry` (impostor-schema-conforming dict), `append_manifest_entry` (append-only JSONL writer with stable key ordering).
  - `preprocess_text` — pipe-through to `scripts/preprocessing.py` so impostor entries are subject to the same content-level guards as identity baselines.
- **`requirements-acquisition.txt`** — opt-in dependency layer matching the existing `requirements-calibration.txt` pattern. Pins `requests`, `feedparser`, `beautifulsoup4`, `lxml`, `python-dateutil`, `pypdf` (for Commit 3); the optional `wayback` and `ocrmypdf` lines are commented with install notes. Ordinary diagnostics, validation, voice distance, and plugin installation do NOT need this layer.
- **Fixture corpus** under `scripts/test_data/acquisition_blog_fixture/`: `substack_feed.xml` (one full-text post + one paid/excerpt-only post + one extra full-text post), `substack_sitemap.xml` (six URLs spanning 2017–2024 for date-window tests), `substack_post_archive.html` (the sitemap-only post fetched via HTML extraction), `wordpress_feed.xml`, `generic_archive.html` (with two post links plus a non-post `/about/` link), `generic_post_quiet_room.html` and `generic_post_attention.html` (with sidebar/script/nav noise that must be stripped), `robots_allow.txt`, `robots_disallow.txt`. All synthetic-prose-only; no real third-party content.
- **`scripts/tests/test_acquire_blog.py`** — 32 regression tests covering the full surface:
  - `acquisition_core` unit tests: slugify (basic + Unicode + max_length), persona-slug rule, content hash determinism, ISO date parser partials, marker-based private-path check, `html_to_text` script/style/nav stripping, `html_text_is_clean` predicate.
  - Substack feed parsing: full-text extraction + paid-marker detection across three flavors + RFC822 date parsing.
  - Sitemap URL filtering by date window.
  - Source-type auto-detection across hostname / feed-probe / generic-fallback.
  - End-to-end Substack acquisition: 3 written posts (paid skipped, sitemap-only one fetched via HTML), manifest entries carry every impostor schema field, cleaned text passes the no-HTML-residue check, no `Subscribe` widget leaks, no trailing comments block, content hashes unique, preprocessing metadata present per sidecar, manifest validates clean.
  - End-to-end WordPress and generic-HTML acquisition with the same invariants.
  - Dedupe-by-content-hash within output dir (a second run against the same dir writes nothing new).
  - Privacy guard refusal path (non-private output → `sys.exit(2)`) + acceptance path (sibling-style private root works).
  - Robots.txt: `Disallow: /` blocks all fetches (zero posts written); `Allow: /` lets fetches through.
  - `--since` / `--until` filters posts by date_written.
  - `--dry-run` writes nothing.
  - `compose_manifest_entry` direct check — every required field present, no None values that would trip validator warnings.
  - End-to-end manifest-validator integration: emitted manifest validates clean (zero errors) when an identity_baseline entry naming the impostor's target persona is added.

### Notes

- This is Commit 2 of three. Commit 3 (`acquire_magazine.py` + `pdf_inventory.py` + `pdf_extract.py`) reuses the `acquisition_core` helpers shipped here. The General Imposters validation harness — the consumer that turns the impostor pool into calibrated attribution claims — is roadmap-tracked separately.
- Privacy posture: acquired text is voice-cloning input from someone else's prose. Default output goes under `ai-prose-baselines-private/impostors/<register>/<author_slug>/`; the privacy guard refuses non-private paths unless `--allow-public-output` is set; impostor entries are never published or distributed; future public-report harnesses must anonymize impostor identities by default and refuse to name `consent_status: undocumented` writers.
- 194 tests pass + 2 skipped (was 163 + 1 in 1.14.3; +32 new acquisition tests, with one previous test reclassified between buckets). One additional `voice_validation_harness.test_manifest_validator_accepts_voice_validation_use` is now part of the count after the impostor schema landed; net change is +31 tests, +1 reclassified.

## [1.14.3] - 2026-05-09

Manifest schema + validator extensions for impostor-corpus support. Commit 1 of three for the impostor-corpus tooling spec (`internal/2026-05-08-impostor-corpus-spec.md`); Commit 2 (`acquire_blog.py`) and Commit 3 (`acquire_magazine.py` / `pdf_inventory.py` / `pdf_extract.py`) follow. The General Imposters validation harness (Koppel et al. 2014, Kestemont et al. 2016) the framework will eventually wire up needs an impostor pool labeled with provenance, consent, register-match strength, era, and corpus-role; this release ships the schema and the validator ratchets that catch impostor-pool misconfiguration at manifest-load time.

### Added

- **Manifest schema additions** (per `references/manifest-schema.md`, the new canonical schema reference). `corpus_role` (default `identity_baseline` for backward compatibility), `impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`, `acquired_via`, `content_hash` are now recognized fields in `KNOWN_FIELDS`. `voice_impostor` added to `ALLOWED_USE`. `literary_horror` added to `ALLOWED_REGISTER` (one of the magazine-acquisition genres in Commit 3). New enum constants: `ALLOWED_CORPUS_ROLE`, `ALLOWED_REGISTER_MATCH`, `ALLOWED_TOPIC_MATCH`, `ALLOWED_CONSENT_STATUS`, `ALLOWED_ERA`.
- **Five new ratchet rules** in `manifest_validator.validate_entry` and `validate_manifest`:
  1. **Impostor required fields** (error). Entries with `corpus_role: impostor` must carry the full impostor metadata block (`impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`, `acquired_via`). Missing any → error.
  2. **Persona-reference + cross-register cross-check** (warning). The validator builds a `persona → set(register)` map from identity-baseline entries during the first pass; impostor entries are then validated in a post-pass against that map. An impostor's `impostor_for` referencing a persona absent from any identity-baseline entry warns. An impostor with `register_match: high` whose own register doesn't appear in the target persona's register set warns.
  3. **Consent-status redistribution ratchet** (warning). `corpus_role: impostor` + `consent_status: undocumented` warns. Future public-report harnesses should escalate this to a refusal unless identities are anonymized and no raw text is emitted.
  4. **Post-AI-era warning** (warning). `corpus_role: impostor` + `era: post_ai_widespread` warns. Post-2024 prose may include AI-collaborated writing that contaminates the human-impostor signal; the user can override intentionally.
  5. **Identity-baseline era recommendation** (warning). Entries with effective `corpus_role: identity_baseline` AND `use` overlapping `{baseline, voice_profile, voice_validation, idiolect, voice_impostor}` AND missing `era` warn. Validation-only entries are exempt — era is for impostor calibration, not for labeled-AI test data.
- **New summary buckets** in the validator's report and JSON output: `by_corpus_role`, `by_era`, `by_consent_status`, `by_register_match`. These appear alongside the existing `by_register` / `by_ai_status` / etc. buckets.
- **`references/manifest-schema.md`**: canonical schema reference. Required fields, common optional fields, impostor fields, allowed enum values, ratchet rules, summary block, and three example entries (identity baseline, impostor, validation). The schema previously lived across `scripts/README.md`, `manifest_validator.py`, and various examples; this consolidation gives downstream contributors and Codex sessions one place to look.
- **`scripts/test_data/impostor_corpus/manifest.jsonl`**: 10-entry synthetic mixed-manifest fixture exercising every ratchet path. Includes a clean impostor, a high-register-match impostor that does pass, an impostor pointing at an unknown persona, an impostor with `register_match: high` but a different register from its target, an undocumented-consent impostor, a post-AI-era impostor, an impostor missing the full required block, an identity-baseline missing `era` with impostor-relevant `use`, and a validation-only entry that's exempt from the era warning. Plus 10 stub `.txt` files so path validation passes.
- **`scripts/tests/test_impostor_manifest_ratchets.py`**: 18 regression tests covering constants surface (the new enum sets, `voice_impostor` in `ALLOWED_USE`, `literary_horror` in `ALLOWED_REGISTER`, new fields in `KNOWN_FIELDS`), all five ratchets with both passing and failing cases, the validation-only-exempt-from-era path, and the new summary-bucket counts (concrete numbers pinned: 7 impostor + 3 identity_baseline, 6 pre_chatgpt + 1 post_ai_widespread, etc.).

### Notes

- This is Commit 1 of three. Commits 2 and 3 (`acquire_blog.py` and the magazine + PDF acquisition tools) require new dependencies (`requests`, `feedparser`, `beautifulsoup4`, `lxml`, `python-dateutil`, `pypdf`, optional `ocrmypdf`) and will land in `requirements-acquisition.txt` per the existing opt-in pattern. They're independently shippable; the schema work shipped here is the prerequisite they all consume.
- Backward compatibility: pre-impostor manifests still validate. `corpus_role` defaults to `identity_baseline` when absent. The era-recommendation ratchet only fires on entries that actually feed impostor calibration, so old `use: validation` manifests don't suddenly generate noise.
- 163 tests pass + 1 skipped (was 145 + 1 in 1.14.2; +18 new ratchet tests).

## [1.14.2] - 2026-05-09

Two further reviewer-flagged P2s on the voice-drift and per-POV trackers (1.13.0 / 1.14.0 / 1.14.1). Both are bugs that surfaced because the tools' behavior was technically working but pedagogically training users into the wrong habits.

### Fixed

- **Privacy-guard allowlist mismatch** in `voice_drift_tracker._check_output_privacy` and `pov_voice_profile._check_output_privacy`. The previous implementation rooted the allowlist at `<repo>/ai-prose-baselines-private/`, but the README and the documented standard layout use a SIBLING `../ai-prose-baselines-private/` directory next to the repo. Users following the documented safe path were hitting the refusal and learning to bypass it with `--allow-public-output` — which trained them to disable the privacy guard. Fixed by switching to the marker-based check `voice_profile.py` already uses (`is_private_output_path`): a path is treated as private if any component in its resolved-absolute form is named `ai-prose-baselines-private`. Repo-internal, sibling, and any other location named that all pass; everywhere else still requires the explicit override. Three new regression tests per tracker cover the sibling-path acceptance, the nested-path acceptance, and the still-refused-without-marker path.
- **Two-POV corpus-mean overclaim** in `pov_voice_profile.pov_vs_corpus_mean_distances`. The previous implementation computed an unweighted midpoint of POV centroids; with K=2, both POVs were equidistant from that midpoint by construction (the existing test asserted this equidistance). The markdown report's framing — "identifies which POV is closest to the writer's neutral default" — was a false claim in the K=2 case. Two-part fix: (a) the function now computes a **word-weighted** corpus-mean centroid (long chapters carry more voice; the mean is biased toward the POV(s) that dominate the manuscript), restoring real signal for K≥2 with unequal word counts and any K≥3; (b) the markdown renderer **suppresses** the corpus-mean section when K=2 with an explicit caveat noting that the diagnostic is structurally weak at two POVs (the word-weighted midpoint just measures which POV got more pages, which is tautological with the input). JSON output retains the raw values either way for callers who want them with the caveat in mind. The existing equidistance test was replaced with a word-weighted-asymmetry test (Madison, with 7848 words on the Federalist fixture, is now closer to the weighted mean than Hamilton with 5888) plus a synthetic test (POV with 10000 words is closer to the weighted mean than POV with 1000) and a markdown-suppression test (the K=2 caveat fires; the per-POV table doesn't render).

### Notes

- Both fixes are reviewer-flagged P2s on top of 1.14.1 — the framework caught them because the tools' technically-correct behavior was training users into the wrong instincts (bypass the privacy guard; trust a structurally-meaningless diagnostic). The CHANGELOG records both because future contributions should know which framings are verified vs. which carry asterisks.
- 145 tests pass + 1 skipped (was 138 + 1 in 1.14.1; +7 new regression tests).

## [1.14.1] - 2026-05-09

Three reviewer-flagged P2 fixes against the voice-drift and per-POV trackers shipped in 1.13.0–1.14.0. The Burrows-Delta one is the substantive one: numeric output for two-period or two-POV reports changes from the (broken) constant `sqrt(2)` to magnitude-sensitive values. Date parser strictness and stdout privacy posture also tightened.

### Fixed

- **Burrows-Delta two-group degeneracy** in `voice_drift_tracker.cross_period_distances` and `pov_voice_profile.cross_pov_distances` + `pov_vs_corpus_mean_distances`. The pre-fix implementation computed z-score column stats over the K period/POV centroids themselves; with K=2 (the natural pre/post or two-character workflow), every informative feature collapsed to symmetric z-scores ±sqrt(2)/2, forcing |z_a − z_b| to a constant `sqrt(2) ≈ 1.4142` regardless of actual drift magnitude. Reproduced by the reviewer with both a tiny shift and a huge shift returning bit-identical Burrows-Delta values. Fixed by computing column stats over the per-DOCUMENT feature vectors across all groups (matches the convention `voice_validation_harness` already uses). Numeric output changes for any two-period or two-POV report; calibrated values from prior runs are not comparable. Cosine-distance values are unchanged (cosine doesn't z-score). Two new regression tests per tracker assert the value is no longer the suspicious sqrt(2) constant on the Federalist fixture, plus synthetic micro-fixtures verify large-drift Burrows-Delta > small-drift Burrows-Delta — the magnitude signal the pre-fix degeneracy threw away.
- **Date parser accepted malformed suffixes and impossible calendar dates** in `voice_drift_tracker._parse_iso_date`. The pre-fix regex was prefix-anchored only, so `"2020-01-foo"` parsed as January 2020. Day-of-month wasn't validated against the month, so `"2020-02-31"` parsed as a real date. Both failure modes silently misclassified documents into wrong periods. Fixed: regex now anchored at both ends and requires fixed-width components (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`); full year-month-day values are validated via `datetime.date` so impossible combinations (Feb 30/31, Apr 31, non-leap-year Feb 29, etc.) are rejected. Year-only and year-month partials still accepted. Three new regression tests cover trailing-garbage rejection, impossible-date rejection (including leap-year edge cases for 2020 vs. 2021), and continued acceptance of valid partials.
- **Stdout privacy bypass** in both `voice_drift_tracker.main` and `pov_voice_profile.main`. The privacy guard checked only `--out` and `--json-out` paths; when both were omitted, the report wrote to stdout without going through the guard. Voice-drift and POV-voiceprint output is voice-cloning input, and stdout writes can leak voiceprint details into terminal logs / CI artifacts / shell history. Fixed: stdout output now also requires `--allow-public-output`; without the override, `main()` exits with code 2 and a stderr message pointing at the file-output flags. Two new regression tests per tracker verify the refusal path and the allow-flag override path.

### Notes

- The Burrows-Delta fix changes numeric output. Anyone who recorded specific values from the 1.13.0 / 1.14.0 trackers should re-run after this update; the old values were degenerate (constant `sqrt(2)` for two-group reports).
- All 138 tests pass + 1 skipped (was 126 + 1 in 1.14.0; +12 new regression tests for these three fixes).

## [1.14.0] - 2026-05-09

Closes cathedral upgrade #6 (voice profile expansion). `pov_voice_profile.py` is the second sub-item — per-POV-character voiceprints for multi-POV fiction, with a heuristic voice-collapse detector flagging pairs of POVs that share too much voice space to be reliably distinguished. Pairs with `voice_distance.py` (writer vs. own baseline) and `voice_drift_tracker.py` (baseline disaggregated by time) to give the framework a complete voice-coherence diagnostic stack: drift across writers, drift across time, drift across characters.

### Added

- `scripts/pov_voice_profile.py` (~600 lines): per-POV voiceprint generator. Reads a manifest with the `pov` field on selected entries (filterable by `--use`, default `voice_profile`). Per-POV centroid in shared feature space; pairwise Burrows-Delta + cosine across POVs; weighted-family aggregate using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP`. Reports POV-vs-corpus-mean distance (which POV is closest to the writer's neutral default — useful for identifying the writer's home register). Reports top distinguishing features per POV (per-POV centroid vs. mean of OTHER POVs, not the corpus mean — that would dilute the comparison by including this POV itself). Voice-collapse verdict flags pairs whose weighted Burrows-Delta falls below the configurable `--collapse-threshold` (heuristic default 0.5; calibration roadmap). Refuses to run when fewer than 2 POVs survive `--min-docs-per-pov` filtering. `task_surface: voice_coherence`. Privacy guard refuses output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed.
- `scripts/test_data/federalist_pov_manifest.jsonl`: synthetic POV-tagged manifest pointing at the existing public-domain Federalist Papers fixture. Maps the 6 documents to two POVs (Hamilton, Madison; 3 docs each) — same trick the drift tracker uses. Cross-POV Burrows-Delta = 1.4142 (different writers in function-word space); collapse verdict correctly does NOT fire at the default 0.5 threshold.
- `scripts/tests/test_pov_voice_profile.py`: 18 regression tests covering manifest loading + POV grouping + min-docs filter, end-to-end run on Federalist (Burrows-Delta > 0.5, distinguishing features surface, POV-vs-mean equidistant for the 2-POV case), no-collapse-flag at default threshold, collapse-flag-fires with aggressive threshold, refusal paths (only one POV after filtering, no POV-tagged entries), privacy guard, JSON / markdown rendering (with collapse section appearing only when flagged), CLI smoke test.

### Changed

- `scripts/README.md` Surface 2 entry extended to mention `pov_voice_profile.py`. Surface tag table updated.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description extended to include "per-POV voiceprints with voice-collapse detection."

### Cathedral status

After 1.14.0, **cathedral upgrade #6 is fully shipped:**

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; the only upgrade still genuinely blocked on a non-code prerequisite
- ✅ #5 Sliding-window localization
- ✅ **#6 Voice profile expansion** — core (1.0.0), idiolect (1.6.0), time drift (1.13.0), per-POV profiles (this release)
- ✅ #7 Before/after restoration loop
- ✅ #8 Privacy / packaging guards

Seven of eight cathedral upgrades are shipped. The framework's voice-coherence stack now answers four distinct questions: "how far is this draft from baseline?" (`voice_distance.py`), "what phrases must survive revision?" (`idiolect_detector.py`), "has the writer's voice changed across time?" (`voice_drift_tracker.py`), and "are this writer's POV characters voice-distinct?" (`pov_voice_profile.py`).

## [1.13.0] - 2026-05-08

Cathedral upgrade #6 — voice profile expansion: time-drift tracking. `voice_drift_tracker.py` disaggregates the writer's baseline by time period, computes cross-period voice distance, and identifies drifting vs. stable features. Pairs with `voice_distance.py` to distinguish "drift between draft and baseline" (recent) from "drift across the writer's own history" (long-term).

### Added

- `scripts/voice_drift_tracker.py` (~600 lines): time-drift surface for cathedral upgrade #6. Reads date-tagged baseline documents from a manifest (with `date_written`) or a directory (with date-prefixed filenames via configurable regex), or accepts an explicit `--periods-json` mapping. Groups documents into periods at the requested granularity (`year` / `quarter` / `month` / `custom` with explicit boundaries). Per-period voiceprint computed via `stylometry_core.extract_features` + `select_feature_names` + per-doc-mean centroid. Cross-period distance: pairwise Burrows-Delta + cosine in a shared feature space (centroids z-scored over the set of period centroids; informative-feature filter same as oracle and voice-validation harness). Weighted-family aggregate using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP`. Per-feature drift scoring: coefficient of variation across period centroids. Reports top drifting + top stable features per family. Refuses to run when fewer than 2 periods survive `--min-docs-per-period` filtering. `task_surface: voice_coherence`. Privacy guard: refuses output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed (voice drift output is voice-cloning input).
- `scripts/test_data/federalist_drift_manifest.jsonl`: synthetic date-tagged manifest pointing at the existing public-domain Federalist Papers fixture. Six entries spanning 1787-10-27 through 1788-01-16. Year granularity yields 2 periods (1787 with 5 docs, 1788 with 1 doc); the cross-period distance reflects authorship change (Hamilton vs. Madison) which is detectable as voice change. Useful for exercising the code paths even though it's not single-writer time drift.
- `scripts/tests/test_voice_drift_tracker.py`: 20 regression tests covering date parsing (partial dates, granularity-specific period keys, custom boundaries), manifest loading + filtering by `use`, period grouping with `min-docs-per-period` filter, end-to-end run on the Federalist fixture (Burrows-Delta > 0.5 between Hamilton and Madison periods, drifting features surface for `function_words`), refusal-when-only-one-period, privacy guard (refuses public output without `--allow-public-output`), JSON / markdown rendering, and CLI smoke tests.

### Changed

- `scripts/README.md` Surface 2 entry extended to mention `voice_drift_tracker.py`. Surface tag table updated. Added explanatory note: drift tracker can tell *which* features are drifting but not *why* — natural stylistic evolution and symptomatic distortion both produce drift; the writer's local read decides.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description extended to include "voice drift tracking across time periods."

### Cathedral status

After 1.13.0, **6 of 8 cathedral upgrades are shipped or partly shipped** with the gap reduced:

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; no code unlock pending
- ✅ #5 Sliding-window localization
- 🟡 #6 Voice profile expansion — time drift shipped (this release); `pov_voice_profile.py` is the remaining sub-item
- ✅ #7 Before/after restoration loop
- ✅ #8 Privacy / packaging guards

Two upgrades remain partly open: #4 (corpus-bound) and #6 sub-item #2 (`pov_voice_profile.py` for multi-POV fiction).

## [1.12.1] - 2026-05-08

Roadmap pass: records the next bounded calibration-corpus follow-ups + flags `voice_drift_tracker.py` as the active next pick under cathedral upgrade #6. No code changes.

### Changed

- `ROADMAP.md`: new "Calibration corpus track" section documenting three bounded follow-ups to the 1.10.0 calibration toolchain — `fetch_raid.py` (RAID benchmark, Apache-2.0, openly redistributable; the most comprehensive openly-licensed AI-detection benchmark), `fetch_mage.py` (MAGE benchmark, MIT; companion to RAID), and `PROVENANCE_TEMPLATE.md` (walkthrough for new users on collecting and labeling personal pre-AI baseline corpora — the irreducible piece of the corpus pool). Each is independently shippable; ordered by leverage. Cathedral upgrade #6 status line updated to flag `voice_drift_tracker.py` as the active next pick (bounded code work on `stylometry_core` primitives, no exotic borrow), with `pov_voice_profile.py` queued behind it.

## [1.12.0] - 2026-05-08

Closes cathedral upgrade #7 v2. The post-check loop that v1 (1.11.0) left as a manual workflow is now automated: `scripts/before_after_restoration.py` reads "before" and "after" diagnostic JSONs (plus the original `restoration_packet.py` output) and reports per-target verdicts with a metric-gaming detector. The framework's metric-gaming resistance now has both a *preventive* surface (the targetability taxonomy in 1.11.0 refuses to issue revision instructions on aggregate divergences) and a *detective* surface (this release's gaming heuristic flags improved targets whose improvement coincides with worsening avoid-direct aggregates).

### Added

- `scripts/before_after_restoration.py` (~600 lines): post-check verdict reporter. Reads any subset of the standard SETEC diagnostic JSONs (variance audit, bigram diff, voice distance, idiolect detector) for both before and after states, plus the original packet output. Two modes:
  - **Packet-driven mode** (`--packet-json` supplied): evaluates each target in the packet against its before/after value with direction-aware improvement logic. For variance signals, looks up the registry's `ThresholdSpec.direction` to know which way is improvement (`lt` signals like `burstiness_B` improve when value rises; `gt` signals like `connective_density` improve when value falls). For bigram packets, improvement = `|kl_contrib|` decreases regardless of sign direction. Per-target verdicts: `improved` / `no_change` / `degraded` / `gamed` / `not_measurable`. Per-signal noise thresholds in `NOISE_THRESHOLDS` constant prevent micro-fluctuations from registering as verdicts.
  - **Diff-only mode** (no packet): raw before/after deltas across every measurable signal. Useful for general "what changed" inspection without committing to a pre-registered set of targets.
- **Metric-gaming detector.** When any actionable (direct or translated) target improves AND a registered avoid-direct aggregate (POS-bigram KL total, voice-distance overall) moves *against* improvement by more than its noise threshold, the verdict flips from `improved` to `gamed`. The note explains why: the revision optimized the local target without addressing the underlying drift — exactly the failure mode that the v1 targetability taxonomy refuses to issue revision instructions on, now caught after the fact when a writer or LLM optimized one anyway.
- **Preservation-list survival check.** When `--original-text` and `--revised-text` are supplied, the report includes a case-insensitive substring search confirming whether each phrase from the idiolect packet's preservation list appears in the revised text. Reports survival rate + list of missing phrases (capped at 30 to keep the output bounded).
- `scripts/test_data/before_after_restoration/`: synthetic fixtures simulating each verdict path (improved, gamed, degraded) plus paired bigram fixtures and two revised-text fixtures (one preserves all phrases, one drops two).
- `scripts/tests/test_before_after_restoration.py`: 19 regression tests covering each verdict path (direction-aware classification for both `lt` and `gt` registry signals; bigram `|kl_contrib|`-reduction logic; degradation), the metric-gaming detector (gaming flag fires when aggregate KL rose; doesn't fire when aggregate fell), avoid-direct packets never claiming improvement, preservation-list survival (full survival + partial survival + skip-when-no-text), diff-only mode (with band-shift detection), JSON / markdown rendering, and CLI smoke tests.

### Changed

- `scripts/README.md` Surface 4 entry extended to mention `before_after_restoration.py` alongside `aic_pattern_audit.py` and `restoration_packet.py`. Surface tag table updated.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description refresh to include "before/after restoration verdicts with metric-gaming detection."

### Cathedral status

After 1.12.0:

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; no code unlock pending
- ✅ #5 Sliding-window localization
- 🚧 #6 Voice profile expansion — `voice_drift_tracker.py` + `pov_voice_profile.py` are bounded code work
- ✅ #7 Before/after restoration loop (v1 packet generator + v2 post-check verdict reporter both shipped)
- ✅ #8 Privacy / packaging guards

Six of eight cathedral upgrades are shipped. Two remain partly open: #4 (corpus-bound) and #6 (bounded code work). The framework now has both the prevention surface (refuse to issue revision instructions on aggregate divergences) and the detection surface (flag improved targets that came at the cost of worse aggregates) for metric-gaming resistance.

## [1.11.0] - 2026-05-08

Metric-targeted restoration: cathedral upgrade #7's first scoped slice. Closes the bridge between SETEC's diagnostic surfaces (Surface 1 smoothing-diagnosis, Surface 2 voice-coherence) and its revision-advisor surface (Surface 4 craft-restoration). The new skill consumes diagnostic JSON and emits bounded prompt packets that classify each signal as direct / translated / investigate-first / avoid-direct, with named guardrails and required post-check commands. The framework's metric-gaming resistance lives in the targetability taxonomy.

### Added

- `references/metric-targeted-restoration.md` (343 lines): the canonical reference. Four-class targetability taxonomy with examples for each class (direct, translated, investigate_first, avoid_direct); POS-bigram and POS-trigram translation tables; dependency-n-gram handling; the restoration-packet JSON schema; prompt-packet field requirements; before/after verification protocol; privacy guard rules. Cross-references the existing Surface 4 reference docs (`aic-flags.md`, `source-triage.md`, `rhetorical-countermoves.md`, `distributional-diagnostics.md`) so the new surface integrates with the existing craft-restoration reference prose.
- `scripts/restoration_packet.py` (~700 lines): the packet generator. Consumes JSON outputs from any subset of `variance_audit.py`, `bigram_diff.py`, `voice_distance.py`, `idiolect_detector.py`, and `aic_pattern_audit.py` (at least one required). Classifies each signal via `DIRECT_TARGETS`, `POS_BIGRAM_TRANSLATIONS`, `POS_TRIGRAM_TRANSLATIONS`, `DEP_NGRAM_TRANSLATIONS`, `INVESTIGATE_FIRST`, and `AVOID_DIRECT` constants. Direction-aware translation (over- vs. under-represented bigrams emit different diagnoses). Severity classification (`light` / `moderate` / `heavy`) from KL contribution or z-score magnitude. `--max-targets` caps actionable (direct + translated) targets per packet at 3 by default, since combining five metric instructions produces incoherent revision pressure. CLI emits both JSON (`--json-out`) and markdown (`--out`); the markdown report is copy/paste-ready as a prompt with the named guardrails attached. Privacy guard refuses output outside `ai-prose-baselines-private/` when private inputs (`--idiolect-json` or `--voice-json`) are supplied unless `--allow-public-output` is passed. `task_surface: craft_restoration`.
- `plugins/setec-voiceprint/skills/metric-targeted-restoration/SKILL.md`: new plugin skill (the framework's fifth public skill, sibling to `craft-restoration` rather than a replacement). Trigger phrases include "reverse this smoothing trend," "make a revision prompt from this diagnostic," "what can an LLM safely target," "metric-targeted restoration," "translate POS bigrams/trigrams," and "post-check this revision." Documents the four-class targetability taxonomy, the workflow (run diagnostics → generate packet → read sections → apply prompt → run post-check), the guardrails, and the privacy posture.
- `scripts/test_data/restoration_packet/`: three synthetic JSON fixtures (`synthetic_bigram_diff.json`, `synthetic_variance.json`, `synthetic_idiolect.json`) crafted to fire specific packet IDs. The bigram fixture's top contributor is `DET-ADJ-NOUN` (a trigram, skipped by bigram translations); the next-ranked `ADJ-NOUN` lands as the first translated packet. `PRON-VERB` has negative `kl_contrib` to test the under-represented direction branch. An unknown bigram (`X-Y`) tests the unknown-bigram skip path.
- `scripts/tests/test_restoration_packet.py`: 20 regression tests covering taxonomy correctness (the load-bearing thing — the framework's metric-gaming resistance lives here), each surface's packet generator, top-level packet assembly + ordering + the actionable cap, render correctness for both JSON and markdown (including the "raw POS labels never appear without a plain-language gloss" check), and a CLI smoke test. The taxonomy tests assert that aggregate divergences (`pos_bigram_kl_total`, `burrows_delta_overall`, `char_ngram_distance`) NEVER appear in the direct/translated/investigate buckets — guards against silent regression of the framework's metric-gaming resistance.

### Changed

- `scripts/README.md`: Surface 4 entry extended to mention `restoration_packet.py` alongside `aic_pattern_audit.py`. Surface tag table updated. Explicit note that `restoration_packet.py` does NOT rewrite prose, claim AI provenance, or optimize metrics directly — the metric-gaming resistance lives in the targetability taxonomy.
- `.claude-plugin/marketplace.json`: plugin description extended to mention "metric-targeted restoration packets that translate diagnostic outputs into bounded revision-safe prompts," plus the calibration toolchain and voice-validation harness that landed in 1.9.0 + 1.10.0.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json`: same description refresh.

### Notes on cathedral status

This commit ships cathedral upgrade #7's v1: metric-targeted restoration packets. The remaining v2 piece is an automated before/after restoration script (`scripts/before_after_restoration.py`) that reruns the diagnostics on the revised text and compares deltas; v1 makes that step manual via the post-check commands embedded in every packet.

After 1.11.0, three of the eight cathedral upgrades remain partly open:

- **#4 Impostor baselines** — still corpus-bound; no code unlock pending.
- **#6 Voice profile expansion** — `voice_drift_tracker.py`, `pov_voice_profile.py` are bounded code work, no exotic borrows.
- **#7 Before/after restoration loop** — v1 (this release) ships the packet generator + post-check workflow; v2 automation is roadmap.

## [1.10.2] - 2026-05-08

Audit `derive_seed` in `validation_harness.py` for the same `hash()` bug pattern the reviewer caught in `voice_validation_harness._stable_seed` during 1.9.0. Finding: NOT buggy. `derive_seed` uses `(i+1)*ord(ch)` accumulation, which is stable across Python processes because Unicode code points don't depend on `PYTHONHASHSEED`. Confirmed empirically (two independent Python invocations produce identical seeds). Adds documentation + a pinned-value regression test so a future "modernizer" can't silently replace the implementation with `hash((parts...))` thinking they're improving it.

### Changed

- `scripts/validation_harness.py` `derive_seed`: docstring expanded to document the cross-process-stable contract and the reasoning behind the `(i+1)*ord(ch)` choice over `hash()`. Behavior unchanged.

### Added

- `scripts/tests/test_validation_harness_seeds.py`: four regression tests pinning the cross-process-stable behavior of `derive_seed`. Pins specific output values (e.g., `derive_seed(42, "per_signal", "burstiness_B") == 29082`) so any algorithm change fails immediately. Also tests that distinct `parts` tuples produce distinct seeds (collision check ensures per-slice bootstrap RNGs stay independent), and that a `None` base seed propagates correctly (preserves the system-entropy fallback when no seed is supplied). Companion to the `voice_validation_harness._stable_seed` regression test from 1.9.0; the two harnesses use different algorithms (validation: `(i+1)*ord(ch)` accumulation, voice: SHA-256 of joined parts) but both satisfy the same cross-process-stable contract.

## [1.10.1] - 2026-05-08

Pre-registers the standards a calibration entry must meet before it lands in `COMPRESSION_HEURISTICS`. No behavior change; documentation only. The calibration toolchain shipped in 1.10.0 now has explicit selection criteria and an "in-sample calibration" epistemic-seatbelt convention recorded *before* any actual calibration run, so the first calibrated threshold (a future commit) is held to standards that pre-date the data rather than being chosen retrospectively.

### Changed

- `scripts/calibration/PROVENANCE.md`: four new sections.
  - **Selection criteria for a calibration entry.** Five gates, all pre-registered: expected polarity matches; AUC/AP not embarrassing; enough negative controls for the requested FPR (with a soft check on TPR-CI width even when `fpr_resolution` is satisfied); interpretable threshold (not "predict almost nothing"); ESL slice behaves conservatively (calibrating against `nonnative_english.csv` should not produce a more aggressive threshold than the heuristic — the ethical commitment is that ESL prose is not the failure mode the band classifier should flag).
  - **In-sample calibration.** Defines the epistemic-seatbelt phrase used in every committed provenance entry: empirical metrics are computed on the same corpus the threshold was derived from; a heldout split is roadmap; the threshold's evidentiary weight is "this value separates the two classes on this fixture under this calibration method," not "this value generalizes." The phrase lives in the JSON ledger entry's `notes`, the Markdown ledger entry's **Notes** bullet, and every calibrated-threshold CHANGELOG entry until a heldout split lands.
  - **Calibration commit shape.** Pre-registers the four-artifact diff a calibration commit produces: one `COMPRESSION_HEURISTICS` registry edit (value + provenance + provisional flipped together; the dataclass mutex enforces it), one new PROVENANCE.md section, one ledger entry appended, CHANGELOG entry + version bump (PATCH or MINOR depending on whether the new value will shift band verdicts on borderline documents).
  - **To populate this ledger** workflow updated: explicit "survey first, pick second" pattern. The previous draft used `burstiness_B` in the example calibrate command; the workflow now lists candidate signals (`burstiness_B`, `connective_density`, `fkgl_sd`, `mattr`, `mtld`, `adjacent_cosine_mean`, `adjacent_cosine_sd`) and explicitly requires the maintainer to survey several before committing the first signal that earns provenance under the criteria above.

## [1.10.0] - 2026-05-08

Per-signal threshold calibration toolchain. Steps 1-8 of `internal/SPEC_calibration_toolchain.md` v2.1, implementing the toolchain on top of the `ThresholdSpec` registry refactor that landed in 1.9.2.

### Added

- `requirements-calibration.txt` at the repo root, pinning `huggingface_hub>=0.23,<1` and `pyarrow>=14`. Calibration-only dependencies; opt-in install via `pip install -r requirements-calibration.txt`. Core `requirements.txt` is untouched. Users who don't run calibration never pay the dependency cost.
- `scripts/calibration/fetch_pangram_editlens.py`: downloads Pangram Labs' EditLens corpus from HuggingFace (`pangram/editlens_iclr`) into `ai-prose-baselines-private/editlens/`. Verifies the dataset card declares CC BY-NC-SA 4.0 (refuses to proceed if the license has drifted). Records HF revision SHA. Auto-writes `NOTICE.md` with attribution + license + redistribution prohibition. Idempotent. Supports `--split` (default `nonnative_english`, the smallest ESL slice) and `--token` (HF access token; required because the dataset is gated). Refuses gracefully when `huggingface_hub` isn't installed, with a clear pointer to `requirements-calibration.txt`.
- `scripts/calibration/editlens_to_manifest.py`: schema-discovery-first conversion of CSV/parquet labeled corpora into SETEC `corpus_manifest.jsonl` slices. `--inspect` mode prints columns + a sample row; explicit `--text-column` / `--label-column` / `--label-map` flags required unless a `--preset` matches (`editlens_nonnative`, `editlens_test`, `editlens_human_detectors` are bundled). Per-row text files spilled to a sibling directory; refuses to write outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed. Reference-detector scores from each row (`fastdetectgpt_score`, `binoculars_score`, EditLens model scores, Pangram v3.2 score) are preserved in the entry's `notes` field for cross-tool comparison. Validates the output manifest via `manifest_validator.validate_manifest` before exit.
- `scripts/calibration/calibrate_thresholds.py`: direction-aware per-signal threshold sweep + provenance writer. Looks up direction (`gt`/`lt`) and dotted signal path from `COMPRESSION_HEURISTICS[signal].direction` and `.signal_path` (the registry is the single source of truth). FPR-resolution check refuses targets below `1/n_neg`. Picks the highest-TPR threshold whose empirical FPR ≤ target. Computes fixed-threshold paired-bootstrap CIs on TPR / FPR / precision at the chosen threshold (selection uncertainty / nested bootstrap is roadmap). Bootstrap seed derivation uses SHA-256 (per the 1.9.0 voice-harness fix) so reproducibility holds across processes. Writes a complete provenance entry to `scripts/calibration/thresholds_calibrated.json` including corpus name + HF revision SHA + license, calibration metrics, CI bounds, SETEC commit, command, derivation date, and a `split_role: calibration_only` tag flagging the in-sample empirical metrics.
- `scripts/calibration/PROVENANCE.md`: human-readable companion to the JSON ledger. v1 ships with no calibrated entries (the toolchain is the deliverable, not the calibrations themselves) but documents the entry format, the calibration workflow, and the legal posture. Entries land via PR as the maintainer's local calibration runs produce them.
- `scripts/calibration/thresholds_calibrated.json`: machine-readable provenance ledger. Initially `[]`; entries appended by `calibrate_thresholds.py`.
- `scripts/tests/test_calibration_provenance.py`: nine regression tests covering ledger integrity (parseability, well-formed slugs, required fields, registry↔ledger referential integrity for slug + signal_path + direction, calibrated value matches ledger derived_value), regardless of whether the private corpus is available. A tenth test re-derives each calibrated threshold via `calibrate_thresholds.derive_threshold` and asserts a match within tolerance — skipped silently when the private corpus is absent (CI-safe), runs in the maintainer's local environment.
- `validation_harness.collect_signal_records(records, signal_path)`: new helper exposing `(label, score)` paired samples for the calibrator. Refactor extracts the per-signal extraction logic that `per_signal_ranking_metrics` previously did inline; both consumers now share the same loop, guaranteeing they operate on identical paired samples (important when the calibrator's derived threshold is later checked against the harness's reported AUC).

### Changed

- `references/implementation-survey.md`: new Implementation Queue item #10 ("Per-signal threshold calibration toolchain — ✅ Shipped (1.10.0)"). Item #12 ("Larger ESL test class") now notes the unblock via `fetch_pangram_editlens.py --split nonnative_english`. Items #13 (band-threshold calibration) and #14 (directional-cluster consistency calibration) added to roadmap as separate methodology passes that build on the v1 toolchain pattern.
- `README.md` Installation section adds a "Calibration toolchain (opt-in)" paragraph noting `requirements-calibration.txt`, the local-only design, and pointers to PROVENANCE.md + the spec.

## [1.9.2] - 2026-05-08

Step 0 of the calibration toolchain (per `internal/SPEC_calibration_toolchain.md` v2.1): replace the tuple-based `COMPRESSION_HEURISTICS` registry with a `ThresholdSpec` dataclass that carries calibration metadata. Unblocks the rest of the calibration toolchain by giving each per-signal threshold a place to record its provenance slug and provisional flag.

### Changed

- `scripts/variance_audit.py`: `COMPRESSION_HEURISTICS` and `POS_BIGRAM_KL_HEURISTIC` are now `ThresholdSpec` dataclass instances instead of `(threshold, direction, weight, length_floor)` tuples. New fields: `signal_path` (the dotted audit-output path the validation harness uses for score extraction), `provenance` (slug into `scripts/calibration/PROVENANCE.md`, `None` for heuristic thresholds), `provisional` (bool; `True` for heuristic, `False` for calibrated). The registry shape is identical in semantics — every existing field is preserved — but consumers now use attribute access (`spec.value`, `spec.weight`, etc.) rather than tuple unpacking.
- Mutual-exclusion contract enforced in `ThresholdSpec.__post_init__`: a threshold cannot be both `provisional=True` and have a non-`None` `provenance` slug, and vice versa. Setting `provenance` to a slug requires clearing `provisional`. Bad direction values (anything other than `"gt"` or `"lt"`) also raise. Catches calibration-vs-heuristic confusion at registry definition time, not at output time.
- Updated all `COMPRESSION_HEURISTICS` consumers to use attribute access: `classify_compression()` (band classifier), `compare_to_baseline()` (length-floor lookup for z-score warnings), POS-bigram KL handling, and `validation_harness.py`'s `_expected_polarity_direction()` polarity check. Behavior is unchanged; the refactor is a code-shape change only.
- `classify_compression()` JSON output gains a new `calibration_status` block: `{n_calibrated, n_provisional, n_total, calibrated_signals, provisional_signals}`. Each entry in `thresholds_used` now also carries `signal_path`, `provenance`, and `provisional` fields. Backward-compatible (only new fields added; existing fields untouched).
- `format_summary()` markdown output gains a "Calibration status" footer line that reports "X of Y signal thresholds carry calibration provenance" and points users at `scripts/calibration/PROVENANCE.md`. v1 release ships with `0 of 11 ... all are heuristic` as expected; the footer flips automatically once calibrated thresholds land.

### Added

- New helpers `provisional_signals(heuristics) -> list[str]` and `calibrated_signals(heuristics) -> list[str]` partition the registry by calibration status. Used by the report renderer; will be used by `scripts/calibration/calibrate_thresholds.py` (Step 4 of the toolchain) to look up which signal paths still need calibration runs.
- `scripts/tests/test_threshold_spec.py`: nine regression tests covering the dataclass contract (default = provisional + no provenance; calibrated must declare provenance; mutex enforcement; direction validation), registry well-formedness (every entry has a non-empty signal_path, valid direction, positive length_floor + weight), `POS_BIGRAM_KL_HEURISTIC` shape, the partition invariant on the `provisional_signals` / `calibrated_signals` helpers, and a "v1 is all provisional" assertion that flips when the first calibrated threshold lands.

## [1.9.1] - 2026-05-08

Roadmap pass on cathedral upgrade #7 (before/after restoration loop): records the metric-targeted restoration packets framing as the next scoped slice.

### Changed

- `ROADMAP.md` cathedral upgrade #7 status line now names the next scoped slice ("metric-targeted restoration packets that translate diagnostic outputs into revision-safe prompt targets, then require a SETEC post-check"). New "Metric-targeted restoration packets" subsection between the adversarial-test-classes and Phase 7+ sections, with the targetability taxonomy (direct targets / translated targets / investigate-first targets / avoid-direct targeting) named so the framework's promised craft-restoration surface has a concrete v1 shape. New cross-layer architectural question added: "which diagnostic signals are safe restoration targets?" — captures that POS bigram/trigram drift is the central test case (diagnostic in raw form, revision-useful only after translation into prose moves).

## [1.9.0] - 2026-05-08

Voice-coherence validation harness. Closes the asymmetry where Surface 1 (smoothing diagnosis) had `validation_harness.py` with ROC AUC + bootstrap CIs + ESL slicing + FPR-target framing, and Surface 2 (voice coherence) had only literature anchoring (Mosteller-Wallace 1964) but no labeled-fixture validation in the repo.

### Added

- `scripts/voice_validation_harness.py`: Surface 2 sibling to `validation_harness.py`. Quantifies how well SETEC's voice-distance feature machinery discriminates same-author document pairs from different-author document pairs on a labeled fixture. Structurally different from the smoothing harness: scores PAIRS (not individual documents), labels by `same_author = (doc_a.author == doc_b.author)`, ranks pairs by per-family Burrows-Delta or cosine distance. Feature-space construction matches production: `select_feature_names` over the entire selected validation slice, `vector_stats` (column mean + SD) over the slice, then per-pair Burrows-Delta as mean absolute z-difference over informative features (sd > 0). Does NOT call `family_distance()` for pairs — that helper is baseline-oriented and a one-document baseline has zero SD on every feature. New CLI: `--manifest`, `--use voice_validation`, `--bootstrap-method {document_cluster,naive_pair}`, `--bootstrap-resamples`, `--bootstrap-confidence`, `--bootstrap-seed`, `--fpr-target`, `--label-by {author,persona}`. Module-level `TASK_SURFACE = "voice_coherence"`; importable as `voice_validation_harness.run_harness(args) -> dict` for downstream gating. Refuses to publish a single aggregate accuracy / TPR / FPR number absent an explicit `--fpr-target` operating point, matching the smoothing harness convention.
- Document-cluster bootstrap CI as the preferred uncertainty estimate: resample documents with replacement within each author stratum, deduplicate, rebuild unordered pairs over the surviving distinct documents, recompute AUC. Skips resamples that lack both same-author and different-author pairs. Treats documents (not pairs) as the unit of evidence, since pair records are dependent — each document appears in multiple pairs. The naive paired-record bootstrap is still available via `--bootstrap-method naive_pair` and is labeled in JSON output with a note that pair dependence makes the interval smoke-test-only.
- Per-family ranking table: AUC + AP + bootstrap CI + n_pairs + polarity check ("OK" if AUC ≥ 0.5 in the expected direction; "INVERTED" if not) for each (family, metric) pair across `function_words`, `char_ngrams_3/4/5`, `pos_trigrams`, `dependency_ngrams`, `punctuation`, `paragraph_dialogue`, `pronoun_modal_negation`. Optional weighted-family aggregate row using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP` matching production `voice_distance.py`'s overall-score shape.
- `scripts/test_data/federalist_voice_validation_manifest.jsonl`: smoke fixture pointing at the existing public-domain Federalist Papers fixture. Six entries (3 Hamilton + 3 Madison), all `public_domain`, all `pre_ai_human`, all `native`, all `register: policy_advocacy`. Hamilton vs. Madison is the canonical Mosteller-Wallace voice-attribution benchmark. Six docs → 15 unordered pairs (6 same-author, 9 different-author). On this tiny fixture the smoke values are: function-word Burrows-Delta AUC ≈ 0.65, function-word cosine AUC ≈ 0.81. These are smoke regression values, not calibration claims; the fixture is too small for a calibration study.
- `scripts/tests/test_voice_validation_harness.py`: six regression tests covering the smoke run, pair-label correctness, function-word AUC tolerance band against the documented smoke values, refusal-of-aggregate-accuracy claim license, operating-point appearance under `--fpr-target`, and `manifest_validator.ALLOWED_USE` round-trip.

### Changed

- `manifest_validator.ALLOWED_USE` extended with `voice_validation`. The new value coexists with `validation` (which routes to the smoothing harness) so a single manifest entry can be tagged `use: ["voice_validation", "validation"]` if it serves both surfaces.
- `references/implementation-survey.md` Implementation Queue item #9 ("Voice-coherence validation harness — Surface 2 sibling to `validation_harness.py`") moves from Roadmap to Shipped.

## [1.8.2] - 2026-05-08

Followup doc fix to 1.8.1: the generated comparison report's Phase A' description still said "same per-doc renormalization within the top-K subset," which is the opposite of what 1.8.1 fixed. The implementation was correct but the report description contradicted it.

### Fixed

- `scripts/oracle/compare.py` Phase A' description text in `render_freq_table_phase_block` now says "full-family relative frequencies preserved (no selected-subset renormalization, matching production `stylometry_core.py`)" and notes that "Row sums are typically < 1.0." The previous phrasing was a leftover from the pre-1.8.1 oracle and contradicted the fix that 1.8.1 actually shipped.
- `scripts/oracle/results/oracle_comparison_report.md` regenerated with the corrected Phase A' description. Numerical content unchanged (Phase A and Phase A' still report Pearson 1.0, mean |Δ| 0.0 across all six feature families).

## [1.8.1] - 2026-05-08

Oracle frequency-table denominator fix: the oracle now exports production-shaped selected-feature vectors instead of selected-subset-renormalized vectors. The Phase A agreement with R `stylo` was previously verifying the math on an altered table whose denominators didn't match production; the fix realigns the oracle with `stylometry_core.py`'s actual feature space.

### Fixed

- `scripts/oracle/setec_to_stylo.py` `char_ngram_table()`, `pos_trigram_table()`, and `dep_ngram_table()` no longer renormalize each document's selected feature vector by the selected-subset total. The exported value for each selected feature is now its full-family relative frequency — the same denominator `stylometry_core.char_ngram_features` / `pos_trigram_features` / `dependency_ngram_features` produces internally before selection. Row sums are typically < 1.0 (the mass not captured by the top-K is the share of features outside the selection); earlier versions divided by the subset total so rows summed to 1.0, which produced an internally-consistent but non-production table. The bug existed in `char_ngram_table()` since it shipped in 1.7.0, and was reproduced in `pos_trigram_table()` / `dep_ngram_table()` when those landed in 1.8.0; this commit fixes all three. Reproduction recorded in `internal/SPEC_oracle_frequency_table_denominator_fix.md`: production `pos:ADP-DET-NOUN` for the first Federalist document was 0.045188, oracle was 0.054225 (8% drift); after the fix, both equal 0.045188.
- `scripts/oracle/run_stylo.R` `build_corpus_table()` no longer divides each row of the selected-feature matrix by the row total. The exported frequencies are preserved exactly from the input full-family-normalized per-document vectors, matching the SETEC-side fix.
- All committed oracle CSVs regenerated with the fixed denominators: `setec_char{3,4,5}_freqs.csv`, `setec_distances_char{3,4,5}.csv`, `setec_pos_trigram_freqs.csv`, `setec_dep_ngram_freqs.csv`, `setec_distances_pos_trigrams.csv`, `setec_distances_dep_ngrams.csv`, `stylo_pos_trigram_freqs.csv`, `stylo_dep_ngram_freqs.csv`, and the corresponding `stylo_distances_phase_a_*` files. Function-word outputs unchanged (the function-word path uses a fixed wordlist with no top-K selection so was unaffected). The comparison report content is unchanged because Phase A and Phase A' agreement remain at perfect (Pearson 1.0, mean |Δ| 0.0) on the production-shaped tables — the fix changes *what is being verified*, not the *answer*.
- Documentation in `references/stylometry-oracle.md` updated to remove "rows sum to 1.0" framing for selected top-K tables and to reframe Phase A and Phase A' as verifying production-shaped selected-feature vectors with full-family denominators preserved.

### Added

- `scripts/tests/test_oracle_frequency_tables.py`: regression tests guarding against the renormalization sneaking back in. Four tests: per-family (char-ngrams, POS-trigrams, dep-n-grams) verify that exported oracle values equal full-family relative frequencies and at least one row sum is < 1.0; a fourth test compares the committed `setec_*_freqs.csv` against `stylo_*_freqs.csv` cell-by-cell to verify the Phase A' acceptance condition without requiring R/stylo at test time.

## [1.8.0] - 2026-05-08

POS-trigram and dependency-n-gram oracle pass against R `stylo`. Closes the last footnote on cross-tool stylometric verification: all six feature families that `voice_distance.py` reports are now oracle-verified at floating-point precision.

### Added

- POS-trigram and dependency-n-gram oracle pass extending `scripts/oracle/`. The function-word oracle and the per-n char-n-gram oracle pass (1.4.0 + 1.7.0) verified SETEC's Burrows-Delta + cosine math on those four feature spaces; this extension does the same for the two spaCy-derived families. Because stylo doesn't natively do POS or dependency parsing, spaCy is the parser of record on both sides: `setec_to_stylo.py` writes per-document parse TSVs to `scripts/oracle/results/parses/<doc_id>.tsv`, and `run_stylo.R` reads them to do its own independent n-gramming. Three checks per family: Phase A (distance correctness on SETEC's frequency table) — both Burrows-Delta and cosine match to floating-point precision (Pearson 1.0, mean |Δ| ≈ 2e-9); Phase A' (frequency-table reconstruction from identical parses) — bit-exact match cell-by-cell (1800 cells, zero setec-only feats, zero stylo-only feats, mean |Δ| = 0.00). The Phase A' result confirms SETEC's `pos_trigram_features` / `dependency_ngram_features` + selection + normalization code paths match a from-scratch reimplementation; the only remaining unverified component is the spaCy parse itself, which is the parser of record on both sides. New SETEC-side helpers `parse_documents`, `write_parse_tsvs`, `pos_trigram_table`, `dep_ngram_table` mirror the existing char-ngram pattern. New R-side helpers `build_pos_trigrams`, `build_dep_ngrams`, `build_corpus_table` reimplement n-gram window construction independently. New `compare.py` helper `render_freq_table_phase_block` compares wide-format frequency tables cell-by-cell. POS / dep pass requires spaCy in the runtime; without spaCy, those exports are skipped with a notice and the rest of the oracle still runs.
- Rolling-window Delta oracle blocker recorded in `references/stylometry-oracle.md`: `stylo::rolling.delta` exposes only four parameters (`gui`, `path`, `primary.corpus.dir`, `secondary.corpus.dir`); window controls (`text.slice.length`, `text.slice.overlap`, `mfw`, `distance.measure`) are baked into the function body as local defaults; `config.txt` override hangs the R process under the conditions tested. Recommended next step if rolling-window verification becomes load-bearing: SETEC-internal pytest contract test rather than cross-tool oracle, since `stylo::rolling.delta`'s API was never going to provide a clean cross-tool reference at this surface.

### Changed

- `references/stylometry-oracle.md` results table extended from four feature spaces to six. Phase A' results table added for POS-trigrams and dep-n-grams. Methodology section reframed from "two complementary phases" to "three complementary phases" (A, A', B) reflecting Phase A''s addition for the spaCy-parsed families.

## [1.7.1] - 2026-05-08

Documentation pass on the Cowork install / update flow with empirical cache findings.

### Changed

- `README.md` Plugin install section for Cowork rewritten. The marketplace path (re-add `anotherpanacea-eng/setec-voiceprint` through Cowork's Plugins UI) is documented as the recommended install and the only path that supports updates. The `--plugin-dir` path is documented as a one-time snapshot: empirical testing on 2026-05-08 found that `git pull` on a `--plugin-dir`-installed local checkout does NOT propagate updates to the running Cowork install even after a version bump and a Cowork restart, with the cache located at `~/Library/Application Support/Claude/local-agent-mode-sessions/<session>/rpm/plugin_<id>/`. This is stronger than the previous catch-up commit's claim (which said only that content changes within an unchanged version field don't invalidate). Working remediation is to remove the `--plugin-dir` install and re-add via the marketplace path. Diagnostic command for users hitting the symptom is included.

## [1.7.0] - 2026-05-08

Char-n-gram correctness pass against R `stylo`, extending the function-word oracle to all three per-n character n-gram families.

### Added

- Char-n-gram oracle pass extending `scripts/oracle/`. The function-word oracle that closed issue #4 verified SETEC's Burrows-Delta + cosine math against stylo on the function-word feature space; this extension does the same for all three of SETEC's per-n character n-gram families (char-3-grams, char-4-grams, char-5-grams) using the top-200 corpus-derived char-ngrams per n. Phase A result on the Federalist fixture: SETEC matches stylo to floating-point precision (Pearson 1.0, Mean |Δ| = 0) for Burrows-Delta and cosine distance on each per-n table independently. Confirms SETEC's per-n separation design (introduced in commit `88c6073` to fix the prior unified-family char-ngram bug) is internally consistent — each per-n family behaves like a standalone Burrows-Delta input the way stylo expects. New helper `setec_to_stylo.char_ngram_table(docs, n)` exports per-n top-K corpus-derived char-ngram frequency tables; `run_stylo.R` reads each and runs `stylo::dist.delta` / `dist.cosine` per n; `compare.py` surfaces the four feature-space results (function words + char-3 + char-4 + char-5) in the comparison report. Char-n-gram Phase B (stylo's own char-ngram tokenization vs. SETEC's) is roadmap. POS-trigram and dependency-n-gram oracle passes are also roadmap (would need a different reference since stylo doesn't natively do POS or dependency parsing).

## [1.6.0] - 2026-05-08

Idiolect detector, content-level corpus-hygiene gate wired into the validation harness, adversarial Unicode-layer fixtures.

### Added

- `scripts/idiolect_detector.py`: keyness/collocation extractor for voice preservation. Scores 1/2/3-grams against a reference corpus, reports idiolectic and anti-idiolectic candidates, emits a quota-balanced "do not normalize" preservation list, shares corpus-hygiene preprocessing with the rest of the pipeline, and applies voice-cloning-grade output privacy guards.
- `scripts/adversarial_fixtures.py` and `scripts/test_data/adversarial/`: deterministic Unicode-layer validation stress fixtures derived from the bundled AI smoke sample (zero-width spaces, Cyrillic homoglyphs, soft hyphens). Manifest entries carry `adversarial_class`, `source_id`, and `transform`; `manifest_validator.py` summarizes adversarial classes and `validation_harness.py` reports a `by_adversarial_class` slice.
- `language_status` field on `corpus_manifest.jsonl` entries (`native | non_native_advanced | non_native_intermediate | learner | unknown`). `manifest_validator.py` warns when entries with non-native language status land in `use: baseline`, `use: voice_profile`, or `use: idiolect`, because ESL prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output (Liang et al., *Patterns* 2023, 61% average FPR on TOEFL essays across seven detectors). Validator summary block now reports `by_language_status` counts.

### Changed

- `validation_harness.py` can now run the content-level corpus hygiene gate with `--check-corpus`. The harness validates the manifest, selects the entries under `--use`, runs `check_corpus_paths()` with the same strip-rule configuration, fails fast if contamination exceeds `--corpus-fail-threshold` (default 0.05), and includes a `corpus_hygiene` block in JSON/Markdown.

## [1.5.2] - 2026-05-08

Documentation pass: license-compatibility section added to the implementation survey, implementation queue refreshed.

### Changed

- `references/implementation-survey.md`: new license-compatibility section enumerating the licenses of each external package SETEC adopts or treats as a reference implementation, with notes on GPL-compatible vs. permissive trade-offs for downstream redistribution. Implementation queue refreshed against current code state.

## [1.5.1] - 2026-05-08

Surface-tag chore fix on `aic_pattern_audit.py`.

### Fixed

- `scripts/aic_pattern_audit.py` surface tag aligned with the rest of the smoothing-diagnosis surface.

## [1.5.0] - 2026-05-08

Layer B/C named-pattern density audit. The framework's first scriptable Layer B/C tool.

### Added

- `scripts/aic_pattern_audit.py`: counts the named rhetorical patterns from `references/aic-flags.md` and `references/source-triage.md` in a target document, reports per-thousand-word density, and (with `--baseline-dir`) compares against a baseline corpus to flag patterns whose density exceeds the writer's voice envelope. Patterns covered: negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus the four regex-tractable nonfiction parallel patterns (false-balance, hedge-and-affirm, recommendation template, authority laundering). Markdown blockquote stripping is on by default (`--keep-quotes` to disable). Layer C earned/unearned verdicts remain the writer's call per instance; the script surfaces candidates and density. Two patterns deferred to v2 because they need NER + abstractness scoring or context analysis: abstraction shielding and indefinite-pronoun gesture. Known v1 limitation: the disguised-correctio detector matches only the explicit `not X, but Y` inline form and the `It is not X. It is Y` frame; multi-sentence correctios are deferred to a sentence-pair scanner using spaCy's dependency parse.

## [1.4.0] - 2026-05-08

Stylometry oracle test harness with R `stylo` and the public-domain Federalist Papers fixture corpus. Closes #4.

### Added

- Stylometry oracle test harness: `scripts/oracle/setec_to_stylo.py` + `scripts/oracle/run_stylo.R` + `scripts/oracle/compare.py`, plus the public-domain Federalist Papers fixture corpus at `scripts/test_data/federalist_oracle/` (six papers from Project Gutenberg eBook #18: 3 Hamilton, 3 Madison, ~13,700 words). Two-phase comparison: Phase A tests distance correctness on identical input (SETEC's Burrows-Delta and cosine distance vs. R `stylo`'s `dist.delta` and `dist.cosine` on the same frequency table); Phase B tests end-to-end agreement on raw text where each side does its own tokenization and feature selection. Phase A: SETEC matches stylo to floating-point precision on both metrics (Pearson 1.0, Mean |Δ| = 0). Phase B: cosine Spearman 0.97 (feature-set choice barely shifts cosine ranking), Burrows-Delta Spearman 0.65 (the fixed-list-vs-corpus-derived-MFW design choice meaningfully shifts the L1-z-score Delta ranking). One bug surfaced and fixed in the oracle harness during the test: the initial draft averaged Burrows-Delta over all features in the fixed wordlist (including constant-zero columns), producing a systematic factor-of-(n_informative / n_total) underestimate vs. stylo's informative-features-only mean; the production `stylometry_core.family_distance` was already correct (only accumulates abs(z) when sd > 0), so the discovery confirmed the production path. The full methodology and divergence catalog lives in `references/stylometry-oracle.md`. R remains optional: the comparison is run-once-when-validating, output CSVs are committed alongside the report, no R install required to read it.

## [1.3.0] - 2026-05-08

Per-signal AUC table for the smoothing-diagnosis validation harness.

### Added

- Per-signal AUC table in `scripts/validation_harness.py`. The harness now reports ROC AUC + average precision + paired bootstrap CIs for each of the 13 Layer A signals independently, in addition to the aggregate `compression_fraction` ranking. Signal scores are extracted at scoring time into `record["per_signal_scores"]`; `per_signal_ranking_metrics()` loops over `_SIGNAL_PATHS`, builds a `(label, signal_value)` paired sample per signal, and runs the existing rank-based metrics + paired bootstrap. Output appears under `slices.overall.per_signal_ranking` in JSON and as a "Per-Signal Discrimination" section in the markdown report. Per-signal CIs are computed only on the overall slice (per-slice per-signal would explode report size and slice samples are typically too small for stable per-signal CIs). Each signal's polarity is checked against `COMPRESSION_HEURISTICS`'s expected direction (`gt` for signals like `yules_k`/`connective_density`/`function_word_ratio` that rise under compression; `lt` for variance signals like `mattr`/`mtld`/`fkgl_sd`/`burstiness_B` that fall); the harness labels each signal as "matches expected direction" or "does NOT match expected direction" so calibration drift or polarity inversion is visible at a glance. Addresses the "which signals are actually carrying the discrimination on this corpus" diagnostic gap that aggregate ranking metrics hide.

## [1.2.0] - 2026-05-08

Per-bigram POS-bigram KL decomposition. Pair of scripts surfacing which specific syntactic templates drive an elevated KL.

### Added

- `scripts/bigram_diff.py` and `scripts/manuscript_bigram_diff.py`: per-bigram POS-bigram KL decomposition. `bigram_diff.py` compares one target document against a cluster of comparator files; `manuscript_bigram_diff.py` compares two corpora at the aggregate level. Both decompose `variance_audit.py`'s aggregate POS-bigram KL into ranked per-bigram contributions, surfacing which specific syntactic templates drive an elevated KL number. Cluster aggregation toggles between pooled counts (long files dominate) and per-file mean (each file weighted equally); default `both` reports side-by-side. Markdown output includes two ranked tables per mode (over-represented and under-represented in target/corpus A) with KL contribution, log₂ ratio, raw probabilities, and example token pairs. JSON output preserves the same fields with `task_surface: smoothing_diagnosis`. Cache machinery in `parse_cluster_files` parses each file once even when running both aggregation modes.
- New helpers in `scripts/variance_audit.py`: `normalize_pos_bigram_counts(counts, keys=None, *, alpha=0.0)` returns Lidstone add-α normalized probabilities; `pos_bigram_kl_contributions(target_probs, baseline_probs, *, target_counts=None, baseline_counts=None, eps=1e-9, min_count=1)` decomposes KL into per-bigram contributions sorted by `abs(kl_contrib)` descending. Both reused by the new bigram-diff scripts.

## [1.1.0] - 2026-05-08

Corpus-hygiene preprocessor wired into Layer A and the validation harness. Catches CSS / HTML / JS / code contamination that previously inflated POS-bigram KL by ~4× against register-matched baselines.

### Added

- `scripts/preprocessing.py`: shared corpus-hygiene preprocessor for `variance_audit.py` and `stylometry_core.py`. Strips suspected non-prose (HTML/CSS/JS scaffolding, Markdown code, loose CSS blocks, conservative HTML tags, JSON-shaped blocks, ASCII tables, YAML front matter) before tokenization and POS-tagging; exposes per-rule token accounting and baseline per-file metadata.
- `scripts/check_corpus.py`: standalone content-level corpus hygiene gate. Runs the shared preprocessing detector over files, directories, or manifest-selected slices; reports stripped-token ratios, dominant stripping rules, and per-file clean/warning/fail status; exits nonzero when contamination exceeds the configured fail threshold or any file cannot be read. Importable as `check_corpus_paths()` for future validation-harness gating.
- `scripts/test_data/preprocessing/css_contaminated_fixture.md`, `scripts/test_data/preprocessing/css_contaminated_fixture_clean.md`, and `scripts/test_data/clean_baseline/`: public synthetic CSS-contamination fixture plus regression coverage for the failure mode where embedded CSS scaffolding inflates POS-bigram KL by ~4× against a register-matched baseline.
- POS-bigram KL band integration: the band classifier in `variance_audit.py` now reads the POS-bigram KL/JSD divergence as a contributing signal alongside the eleven Layer A variance signals, with its own length-floor and weight in `COMPRESSION_HEURISTICS`.

### Changed

- POS-bigram KL and all Layer A text statistics in `variance_audit.py` are computed on preprocessed text by default. Baseline files receive the same preprocessing as the target; `--allow-non-prose` records an explicit opt-out in JSON for users intentionally auditing markup-heavy material. Voice-coherence feature extraction inherits the same preprocessing through `stylometry_core.py`.

## [1.0.0] - 2026-05-07

Initial Cowork plugin release. Packages the SETEC stylometric framework as a Claude Code / Cowork plugin with four task-surface skills. Bundles the development burst that brought the framework from "MVP plus voiceprint" to "validation-spine prerequisites in place."

### Added

- Plugin packaging for Claude Code CLI / Desktop and the Cowork SDK harness. New `.claude-plugin/marketplace.json` declares the marketplace catalog with one plugin entry; new `plugins/setec-voiceprint/.claude-plugin/plugin.json` declares the plugin manifest at version 1.0.0. Four `SKILL.md` files at `plugins/setec-voiceprint/skills/{smoothing-diagnosis,voice-coherence,validation,craft-restoration}/` map one-to-one onto the framework's four task surfaces; each skill's `description` field carries trigger phrases for model-driven invocation, and each script-wrapping skill documents CLI usage with `${CLAUDE_PLUGIN_ROOT}/../../scripts/...` paths so invocations work regardless of where the repo is cloned. README installation section now distinguishes the Claude Code CLI/Desktop install path (`claude plugin marketplace add` + marketplace-driven update flow) from the Cowork harness install path (`--plugin-dir` against a local checkout, `git pull` + new session for updates). The version field lives only in `plugin.json` (not duplicated in the marketplace plugin entry) so resolution priority is unambiguous, and only the canonical `.claude-plugin/marketplace.json` ships (no root-level marketplace.json duplicate) to avoid split-brain when only one of two catalog files gets bumped.
- `scripts/manifest_validator.py`: schema and integrity checks for `corpus_manifest.jsonl`. Per-entry checks (required fields, enum-valued fields, `use` is a list, `word_count` non-negative, unknown field names flagged), cross-entry checks (duplicate `id`, missing-on-disk path, two-ids-one-file, `use: validation` + `split: baseline` contradiction, `use: voice_profile` privacy ratchet, provenance contradictions). JSON output, markdown report, importable `validate_manifest(path) -> dict` for downstream gating.
- `scripts/manuscript_repetition_audit.py`: manuscript-aggregate vocabulary audit. Composes the chapter splitters from `manuscript_audit.py` with the per-document scorer from `repetition_audit.py`. Output: dispersed habit-vocabulary table (words flagged in many chapters at moderate ratio), concentrated repetition table (one or two chapters at high peak ratio), per-chapter top-N. JSON preserves per-chapter and aggregated structures separately.
- `scripts/chapter_distinctiveness_audit.py`: leave-one-out internal-baseline vocabulary audit. For each chapter, baseline is the union of all other chapters; surfaces words distinctive to one chapter rather than habit-vocabulary dispersed across the manuscript. Default `--min-ratio 1.5` because "distinctive" is a stronger claim than "barely over-represented."
- Sliding-window mode in `scripts/variance_audit.py`. New `split_into_windows()`, `audit_windows()`, and `format_windows_dashboard()` plus three CLI flags (`--window-size`, `--window-stride`, `--window-only`). Catches localized compression that whole-document scores would mask: a synthetic document combining clean and AI-flavored prose averages to "Lightly smoothed" at whole scope; the window scan correctly localizes the compression to the AI-flavored sections.
- POS-bigram KL/JSD divergence against baseline aggregate, in `scripts/variance_audit.py`. New `pos_bigram_distance()` helper with Laplace smoothing on the union of bigrams; new `compare_distributions()` entry point keeps the existing `compare_to_baseline()` z-score path unchanged.
- Feature-cluster mode for `scripts/voice_distance.py`. New `FUNCTION_WORD_CLUSTERS` registry (26 predefined syntactic groupings), new `compute_clusters()` aggregator reporting mean signed z, direction consistency, and top contributors per cluster. Catches authorial fingerprints that single-feature top-N misses when a cluster of related features moves together at moderate magnitudes.
- Genre tolerance quick-reference table in `references/aic-flags.md`. 7×6 grid (seven AIC flags by six genres) with three tolerance bands (Low, Med, High) plus N/A and six footnotes for cells where a single band misrepresents the call (AIC-1 in testimony; AIC-2 in testimony; AIC-7 in essay/testimony; AIC-3 in blog; AIC-7 in blog; AIC-3 in testimony).
- `task_surface` field on every script's JSON output and markdown header. Values: `smoothing_diagnosis` (variance/manuscript/repetition audits), `voice_coherence` (voice_distance, voice_profile, idiolect_detector), `validation` (manifest_validator, validation_harness). Each script exports a module-level `TASK_SURFACE` constant for downstream importers.
- Per-n character n-gram families (`char_ngrams_3`, `char_ngrams_4`, `char_ngrams_5`) in `scripts/stylometry_core.py`. Each family normalizes within its own n, has its own selection cap, and contributes its own Burrows-Delta and cosine distance. Replaces the prior unified family that mixed all three n-values in one frequency space.
- `references/implementation-survey.md`: borrow-before-building survey for validation, bootstrap, stylometry-oracle, idiolect, and manifest-schema work. Records which external packages should become dependencies, which should remain reference implementations, and which SETEC-specific responsibilities stay local.
- Final license texts. `LICENSE` carries the canonical GNU GPL v3 text governing code (`GPL-3.0-or-later`); `LICENSE-docs` carries the canonical Creative Commons Attribution-ShareAlike 4.0 International text governing documentation and reference prose (`CC-BY-SA-4.0`); `NOTICE` enumerates which files each license governs and confirms that personal baseline corpora and generated voice profiles fall outside the repository's licensed scope.
- `requirements.txt`: declares `spacy>=3.7,<4`, `scipy>=1.11`, `scikit-learn>=1.3`, `statsmodels>=0.14`, and `nltk>=3.8` as runtime dependencies, with `sentence-transformers` and `textstat` listed as commented optional extras for calibrated cohesion and tightened FKGL. Replaces the scattered `pip install` snippets in the README's Installation section.
- `scripts/length_bootstrap.py` and `--bootstrap` flag in `variance_audit.py`. Phase 1 step 3 of the validation spine. For each Layer A signal, the bootstrap samples random length-matched word-slice windows from each baseline file, pools the per-window statistic values into an empirical distribution at the target's word length, reports the target's mid-rank percentile in that distribution, and uses `scipy.stats.bootstrap` to put a BCa confidence interval on the percentile. Replaces noisy z-scores at small N. Flags: `--bootstrap-windows-per-file` (default 50), `--bootstrap-max-windows` (default 500), `--bootstrap-resamples` (default 9999), `--bootstrap-confidence` (default 0.95), `--bootstrap-seed`. Output appears under `baseline_bootstrap` in JSON and as a "Length-matched bootstrap" section in markdown.
- `scripts/validation_harness.py`: MVP validation harness for the `smoothing_diagnosis` surface. Reads a validated manifest, runs `variance_audit` scoring on entries tagged `use: validation`, reports ROC AUC / average precision with paired bootstrap CIs when both classes are present, and reports thresholded FPR/TPR/FNR/precision only when an explicit `--fpr-target` operating point is supplied. Slices output by register, length bucket, language status, and AI status; includes claim-license language refusing individual-document provenance verdicts and single aggregate accuracy. Defaults leave `mixed` outside the binary label frame unless explicitly mapped.
- `scripts/test_data/validation_smoke_manifest.jsonl`: public smoke fixture for `validation_harness.py`, pointing at the bundled capybara human sample and AI smoke sample.

### Changed

- `references/implementation-survey.md` rewritten against actual code state. spaCy and scikit-learn now appear under Adopt As Dependencies (both already imported by `variance_audit.py` and `stylometry_core.py` as optional, now formally adopted via `requirements.txt`); SciPy moved from "future bootstrap dep" to current runtime requirement; NLTK is now the optional-reference-corpus path for `idiolect_detector.py`'s Brown corpus mode. New sections: ESL handling, adversarial test classes for the validation harness, 0.01% FPR target framing, and a Phase 7+ horizon item for local-LLM cross-perplexity.
- `ROADMAP.md` updated to enumerate ESL handling, adversarial test classes (paraphrase / humanizer / Unicode-layer), the 0.01% FPR target as the recommended deployment threshold for accusation-grade settings, and a Phase 7+ horizon for a local-LLM cross-perplexity sibling tool. The harness step now explicitly slices by `language_status` and refuses to publish a single aggregate accuracy number absent a stated FPR target.
- `README.md` Installation section now points to `requirements.txt` and documents the `python -m spacy download en_core_web_sm` step explicitly, replacing the prior scattered Tier 1 / Tier 2 / Tier 3 pip snippets.
- Band classifier in `classify_compression()` now reports `compression_fraction = weighted_score / available_weight` and thresholds the fraction (< 0.15 / < 0.40 / >= 0.40), not the absolute weighted score. New `Insufficient signal` band for documents below all length floors. Previously such documents falsely classified as "Lightly smoothed."
- Baseline z-score output in `compare_to_baseline()` carries `length_floor`, `length_floor_satisfied`, and a warning string when the target is below the heuristic's floor. Markdown output marks unreliable rows with `[!]`.
- Default `--min-ratio` for `chapter_distinctiveness_audit.py` raised from 1.0 to 1.5. The leave-one-out baseline can drag down ratios for habit-vocabulary that some chapters omit; "distinctive" is a stronger claim than "barely over-represented."
- Default `--char-top` in `voice_distance.py` and `voice_profile.py` lowered from 500 to 200. Semantic also changed: now per-n cap rather than total cap across all three n-values.
- Cluster registry pruned from 27 to 26 families. Dropped `modals_volitional` (singleton "will" never fired under the cluster floor); dropped `more`/`most` from `comparison` (they overlapped `quantifiers` and read as a duplicate lens).
- Cluster `direction` label now derives from majority sign of feature deviations, not from the mean signed z. Prevents the directional flag and direction label from contradicting when one large outlier of opposite sign overwhelms several smaller features pulling the same way.
- Dispersed-habit sort key in `manuscript_repetition_audit.py` changed from `(n_chapters, mean_ratio)` to `(n_chapters, median_ratio)`. Median resists single-spike inflation.

### Fixed

- `voice_distance.py` now drops the target file from baseline entries when the same path appears in `--baseline-dir`. Previously the target self-normalized the score (cosine min collapsing to 0.0).
- `manuscript_repetition_audit.py` and `repetition_audit.py` now refuse zero-token baselines (raises `BaselineError`); surface skipped baseline files with a stderr warning rather than silently dropping them; expose `baseline_files_loaded`, `baseline_files_skipped`, and `baseline_tokens` in JSON output.
- `manifest_validator.py` rejects directory paths after path resolution (uses `is_file()` instead of `exists()`); the voiceprint privacy ratchet now warns on `voice_profile` and `idiolect` entries with missing or non-string `privacy` values, not just non-`'private'` strings.
- `repetition_audit.py` and `manuscript_repetition_audit.py` apply a `min_ratio` floor to candidate scoring (default 1.0). The previous behavior admitted under-represented words (ratio < 1.0) into the candidate list; downstream aggregators treated them as habit-vocabulary candidates.
- `variance_audit.py` POS-bigram metric documentation and computation now match. The reference doc described KL divergence; the script previously computed only entropy of the target. Both are now produced when a baseline is supplied.
- `variance_audit.py` function-word reference doc now points readers to `voice_distance.py` for the actual Burrows-style and Cosine Delta computation. Layer A reports only `function_word_ratio` as advertised.
- README length-floor table now matches `COMPRESSION_HEURISTICS` for all 11 signals (Burstiness B 200, Shannon entropy 2000, Sentence-length SD 5000 corrected from prior stale values).
- Genre tolerance table internal contradictions resolved. Three cells (AIC-3 blog, AIC-7 blog, AIC-3 testimony) now use `Mixed` with footnotes splitting the tolerance by subtype rather than the single-band labels that contradicted the explanatory prose.

[Unreleased]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.3...HEAD
[1.14.3]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.2...v1.14.3
[1.14.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.1...v1.14.2
[1.14.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.0...v1.14.1
[1.14.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.12.1...v1.13.0
[1.12.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.12.0...v1.12.1
[1.12.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.2...v1.10.0
[1.9.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.1...v1.9.2
[1.9.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.0...v1.9.1
[1.9.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.2...v1.9.0
[1.8.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.1...v1.8.2
[1.8.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.7.1...v1.8.0
[1.7.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/releases/tag/v1.0.0
