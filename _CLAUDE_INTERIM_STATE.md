# _CLAUDE_INTERIM_STATE.md - iMessage atomic live-row handoff (interim)

Session: Claude (Fable 5), 2026-07-18. Token budget wind-down before implementation began.
Authority: D:\Code-PC\CLAUDE-IMESSAGE-LIVE-ROW-HANDOFF-2026-07-18.md
  (sha256 VERIFIED == 33554cfe093fdc77c9cfdf87b7bf643e5314b1a44b3545c6561d69bf31c5732c)
Spec authority: specs/imessage-atomic-message-acquisition.md (in this worktree, untracked).

## STATE SUMMARY

- NO source edits were made. The worktree is byte-identical to Codex's terminal
  state (6 modified + 6 untracked producer files) plus this one interim file.
- Baseline CONFIRMED on this host: 580 passed / 7 skipped
  (test_acquire_imessage_sent_atomic.py + test_acquire_imessage_sent.py +
  test_author_corpus_export.py; PYTHONUTF8=1 py -3.12 -m pytest, fresh basetemp
  D:\Code-PC\_tmp_imsg_atomic_bt\baseline). Matches the handoff's stated
  foundation baseline exactly.
- Environment change made (disclose): `git config --global --add safe.directory
  D:/Code-PC/_wt_voiceprint_imessage_atomic` (worktree .git is owned by the
  CodexSandboxOffline user; git refused all operations without it).

## STATUS vs HANDOFF CONTRACT

| Handoff item | Status |
|---|---|
| Hash-verify + read handoff and spec fully | DONE |
| Survey worktree / preserve 12 producer files | DONE (verified untouched) |
| Baseline test counts | DONE (580/7 confirmed) |
| Live run()/main() implementation | NOT_STARTED (design complete, below) |
| Row transaction / ledger / checkpoint / manifest / receipt | NOT_STARTED (designed) |
| Atomic-run validator | NOT_STARTED (designed) |
| Smoke ladder + TTY receipt | NOT_STARTED (designed) |
| Producer->consumer seam fixture | NOT_STARTED (consumer APIs confirmed, designed) |
| Test evolution (unfreeze 2 frozen tests, adversarial tests) | NOT_STARTED (list below) |
| Capability YAML/golden/changelog/ROADMAP/readiness updates | NOT_STARTED |
| Read-only subagent diff review | NOT_STARTED (no diff yet) |

## RECONNAISSANCE FINDINGS (verified, save re-derivation)

1. `plugins/setec-voiceprint/scripts/acquire_imessage_sent_atomic.py` (~12k lines)
   already contains the COMPLETE bootstrap ladder: snapshot materialization
   (descriptor-pinned, macOS-gated), journal CAS chain reserved ->
   snapshot_in_progress -> snapshot_closed -> universe_closed ->
   options_maps_closed -> owner_closed -> ready_to_promote -> promoted, with
   integrators/resumers/recovery for every state, `build_initialization_closure()`
   (PURE + portable), `discover_candidate_universe()` (portable),
   `process_selected_candidates()` (pure), `processing_receipt_payload()`,
   locator/timestamp/group-status primitives. Entry point:
   `_prepare_or_resume_bootstrap_promoted_locked_at(parent_fd, journal_name,
   expected_reserved, staging_path, source_db, *, lock_fd, lock_name, key_bytes,
   semantic_options, run_controls) -> PreparedPromoted` (evidence carries
   schema_info, universe, InitializationClosure).
2. MISSING (the actual task): row transaction layer, source ledger, checkpoint,
   aggregate draft_manifest.jsonl derivation, final receipt, semantic
   artifact-tree hash, atomic-run validator, live-smoke TTY receipt
   mint/consume, `run()`, real `main()` (currently `parser.error(...)` refuses),
   extended arg parser, seam fixture test.
3. Tests freezing incomplete state (must evolve):
   - `test_foundation_module_does_not_expose_full_acquisition` (asserts not
     hasattr(A, "run"), line ~5083)
   - `test_main_explicitly_refuses_unimplemented_live_work` (line ~5087)
   - Parser tests (~5026-5081) use `_required_cli()` = exactly the 4 required
     options (group policy, --timezone, --apple-date-unit, --hmac-key) and MUST
     KEEP PASSING -> new acquire-mode args must be argparse-OPTIONAL, enforced
     in main() per action.
4. Platform reality on this Windows host: the durable layer is macOS-gated
   (`_require_live_private_tree_ops`, `_write_private_canonical_json_at`,
   `_acquire_bootstrap_lock_at` all refuse off-darwin); the existing 10k-line
   test file exercises it via `_FakeTreeOps` fakes. `load_hmac_key` refuses on
   Windows. `materialize_consistent_snapshot(source_db, staging_dir)`
   (path-only helper) IS portable and Windows-tested; spec itself names the
   path-only snapshot helper "a synthetic compatibility fixture" - precedent
   for a portable synthetic path.
5. Consumer contract (author_corpus_export.py, already modified by Codex,
   supports kind `imessage_sent_atomic` -> source value "imessage_local"):
   - Input = draft_manifest.jsonl (JSONL), entries with keys: id, path
     (relative, traversal-free), author, persona, register, date_written,
     ai_status, language_status, word_count, use=["voice_profile"],
     split="baseline", privacy="private", content_hash (sha256:64hex),
     source="imessage_local", corpus_role="identity_baseline", era,
     consent_status="author_consent", acquired_via (fixed string, no wall
     clock).
   - Per text file `<stem>.txt` a sidecar `<stem>.meta.json` (stem must be
     dot-free) with content_hash, integer unix_nanoseconds,
     author_corpus_group_locator + author_corpus_entry_locator
     (hmac-sha256:64hex, `ATOMIC_PRIVATE_LOCATOR_RE`), author_corpus_unit_kind
     "atomic_message", unit_index 0, unit_count 1. Exporter NEVER degrades
     malformed atomic locators (raises).
   - Call shape: `E.build_export(sources={"imessage_sent_atomic": manifest},
     register_map={"imessage_sent_atomic:personal": "text.personal"},
     allowed_ai_status=[...], persona=..., hmac_key=key_bytes)` ->
     (records, texts, receipt, config_hash, evidence);
     `E.publish_package(dest, records, texts, receipt, hmac_key=..., evidence=...)`.
   - The exporter-test fixture `_atomic_imessage_source()` is the hand-built
     seam the handoff wants replaced by actual acquirer output.
6. voicewright (importable under py -3.12 from D:\Code-PC\setec-voicewright -
   READ-ONLY import, never write there):
   - `voicewright.author_corpus.load_author_corpus_package(package_dir,
     producer_receipt, RegisterAuthorizationScope)`; scope built as
     `RegisterAuthorizationScope(AuthorizationRecord(persona, authorized_by,
     basis, attested_at="...+00:00"), registers=("text.personal",),
     allowed_ai_status=("pre_ai_human",))`. Receipt producer_revision must be
     40-hex (exporter derives from git; works in this worktree).
   - `plan_register_splits(sealed_corpus, scope, ...)` is the duplicate-edge
     authority (unions source_group + content_sha256 + normalized_text_sha256).

## DESIGN DECISIONS (settled; implement as specified)

### Artifact layout in the promoted run dir
```
<run-dir>/
  source-snapshot.db, semantic-options.json, run-controls.json,
  smoke-policy.json, private-contact-map.json,
  private-source-identity-map.json, run-owner.json      # existing 7
  rows/<stem>/<stem>.txt|<stem>.meta.json|<stem>.fragment.json  # per retained row
  source-ledger.json      # canonical retained/excluded ledger, atomic rewrite/row
  checkpoint.json         # derived from closed ledger, rewritten per row
  draft_manifest.jsonl    # derived from sorted closed fragments (exporter input)
  acquisition-receipt.json  # final receipt (excluded from tree hash)
  .row-staging/<stem>/    # transient; absent at completion
```
stem = f"{contact_alias}-{local_date.isoformat()}-{entry_locator_hex[:16]}"
(alias from private-contact-map ordering = sorted group_locator, allocated over
the FULL selected chat universe; dot-free so exporter .with_suffix works;
collision = closed error). Sidecar binds ONLY semantic-options digest (+
snapshot hash, key id, tool, unit 0/1 fields, group_status, local_date,
word_count, content_hash, unix_nanoseconds, preprocessing meta). Fragment =
{schema, entry (exact manifest JSONL object), entry_locator, unix_nanoseconds,
semantic_options_digest, snapshot_file_sha256}. Manifest derivation: sort
fragments by (unix_nanoseconds, entry_locator); bytes = concat of
`_canonical_json_bytes(entry)` per line. Ledger row = {source_ordinal (from
private-source-identity-map), entry_locator, disposition
(retained|9-reason-taxonomy), content_sha256|null, word_count|null,
row_stem|null}; ledger top-level binds snapshot hash + semantic + controls
digests, selected count, counts block, not_considered_after_bound (set at
completion), complete flag. Checkpoint binds ALL THREE option digests +
ledger_sha256 + counts (spec: owner/checkpoint/receipt bind three digests;
sidecar binds semantic only; smoke receipt binds smoke digest only).
Receipt fields: schema, tool{name,version,capability_id}, snapshot metadata
payload, three digests, ai_boundary_version, timezone, hmac_key_id,
contact/source map hashes, allow_empty, max_retained, counts
{candidate,selected,considered,not_considered_after_bound,retained,
excluded_considered_by_final_reason}, full_universe_eligibility_closure,
candidate+selected locator universe hashes, manifest_sha256, ledger_sha256,
semantic_tree_sha256, privacy block. Semantic tree hash: sorted
slash-normalized relpaths + per-file sha256+size over rows/** + ledger +
checkpoint + run-owner.json + draft_manifest.jsonl + semantic-options.json +
run-controls.json + smoke-policy.json; EXCLUDES receipt, snapshot, both
private maps, lock/journal/staging/logs.

### Row transaction (spec order, per retained row)
stage under .row-staging/<stem>/ -> write text, sidecar, fragment (O_EXCL,
fsync) -> read-back verify hashes/bijection -> exclusive-rename dir to
rows/<stem> -> atomic ledger rewrite (row closed; read+compare previous bytes
first = tamper CAS) -> atomic checkpoint rewrite. Excluded row: ledger+checkpoint
only. Resume: ledger is row-journal authority; ledger rows must equal the
deterministically recomputed planned prefix byte-for-byte; at most ONE
committed-unledgered row allowed iff it byte-equals the next planned row (then
close its ledger row); at most ONE staging dir, only for an unclosed stem ->
delete + replay; anything else refuses. Plan rows up front via
`plan_row_artifacts()` (pure, from universe + processing + closure + key) so
interrupted/resumed/rebuilt runs are byte-identical.

### Platform/host strategy (the pivotal decision)
- `run(config, *, key_bytes=None, io=None, bootstrap=None, progress=None)`.
- Default io = LiveDurableRowIo (prepare() calls _require_live_private_tree_ops
  -> refuses off-macOS BEFORE touching the source db). Default bootstrap =
  live journaled chain: _open_private_parent_dirfd(run_dir) ->
  _acquire_bootstrap_lock_at -> bootstrap_journal_payload(state="reserved",...)
  -> _prepare_or_resume_bootstrap_promoted_locked_at -> PreparedPromoted;
  hold flock across row phase; release in finally.
- `_SyntheticFixtureRowIo` + `_synthetic_fixture_bootstrap` (module-level,
  underscore-named, docstring-marked synthetic/fixture-only, NEVER reachable
  from CLI): portable real-FS path used by tests and the seam fixture ONLY.
  Synthetic bootstrap = materialize_consistent_snapshot (already portable) +
  preflight + discover + build_initialization_closure + write the 6 closure
  JSONs byte-exactly + rename staging->run dir; resume = re-derive closure and
  byte-compare all 7 files. This keeps "the fixture consumes actual acquirer
  output" true (same payload/emission code; only durability plumbing differs),
  mirroring the spec's own "path-only snapshot helper = synthetic
  compatibility fixture" precedent. Windows CLI acquire mode therefore
  fail-closed refuses (testable), synthetic tests produce REAL trees the
  exporter+voicewright consume.

### Smoke ladder / TTY receipt
- Rule: runs with max_retained == 1 need no receipt (smoke rung); ANY other
  run (including full, max_retained=None) requires --live-smoke-receipt whose
  smoke_policy_digest equals THIS run's freshly computed smoke-policy digest
  (checked after bootstrap, before any row emission). Stale source db =>
  different snapshot => different smoke digest => refuse (stale-binding gate).
- `mint_live_smoke_receipt(run_receipt_path, output_path)`: requires
  stdin+stdout isatty; loads the smoke run's acquisition receipt; requires
  max_retained==1, retained_rows==1, allow_empty==False; exact typed
  confirmation phrase; writes O_EXCL to a path OUTSIDE any run dir (guard:
  refuse if output parent contains run-owner.json); receipt = {schema,
  smoke_policy_digest, approved_run_receipt_sha256, retained_rows,
  approved_by:"owner-tty", confirmed_at}. Mint reads/writes nothing else
  (approval never acquires/activates/rewrites).
- Receipt payload gets explicit max_retained + allow_empty fields to make
  minting checks possible without inverting digests.

### CLI plan (preserves frozen parser tests)
Keep the 4 existing required options exactly. Add OPTIONAL: --source-db,
--output-root, --run-id, --persona, --author, --register, --since, --until,
--max-messages (default e.g. 250000), --max-retained, --allow-empty,
--checkpoint-interval (default 1), --live-smoke-receipt, and two exclusive
action flags: --mint-live-smoke-receipt (with --smoke-run-receipt,
--receipt-out) and --validate-run DIR. main() dispatches: validate (portable,
read-only) / mint / acquire (requires the acquire args via parser.error;
prints ONLY resolved interpreter + opaque snapshot binding + semantic option
hash + private output dir before prose work, via run()'s progress callback;
aggregate counts after). Zero retained without --allow-empty => nonzero exit.
Preprocessing constants fixed in code: version "legacy-preprocess/1", rules
"imessage-atomic-rules/1" (matches existing test fixtures).

### Validator `validate_atomic_run(run_dir)` (portable, read-only)
Closed inventory of run dir; canonical decode of all 6 init JSONs via existing
validators; owner cross-binding (option digests, map hashes, snapshot rehash);
ledger schema+bindings+counts equations+prefix coverage; rows/ bijection with
ledger retained stems; per-row: exact 3-file inventory, sidecar full schema +
bindings, text-bytes hash == sidecar.content_hash == ledger content_sha256,
word count, locator shapes + uniqueness, stem consistency (alias pattern
contact-\d{6}, date == local_date, suffix == locator prefix); fragment.entry
must equal a strict rebuild from sidecar + semantic options (id/path/era/
ai_status/date/word_count/content_hash); draft_manifest byte-equality with
derivation; checkpoint binds ledger hash + digests; receipt recompute-compare
(counts, universe hashes from source map, manifest/ledger/tree hashes);
unknown schema/state/file => refuse; excluded-from-hash names verified absent
from tree payload. Raises on violation; returns aggregate summary dict.

### AI-status / era
ai_status_for_local_date already exists (pre 2024-07-01 -> pre_ai_human, else
unknown). Add era_for_local_date mirroring exporter boundaries (<2022-11-01
pre_chatgpt, <2024-07-01 pre_ai_widespread, else post_ai_widespread) so
manifest entries carry exporter-consistent era.

### Planned new tests (test_acquire_imessage_sent_atomic.py unless noted)
1. Evolve the 2 frozen tests: module NOW exposes run/validate/mint; main()
   without acquire args exits 2 with missing-argument error; main() with full
   acquire args on Windows refuses fail-closed (macOS-host message) BEFORE
   touching --source-db (non-activation proof).
2. Synthetic end-to-end: 3-outgoing-messages fixture db -> run() (synthetic io
   + bootstrap, key_bytes) -> 3 committed rows, validator passes, counts
   equations hold (acceptance 2).
3. Equal-content duplicates: distinct locators/stems, equal content hashes,
   forward + reverse insertion order, and resumed run -> byte-identical
   ledger/manifest/tree hash (acceptance 3, 11).
4. Crash/resume kill-points: injected failing io after EACH durable step
   (text/sidecar/fragment write, dir commit, ledger, checkpoint, manifest,
   receipt) -> resume completes; final tree byte-identical to uninterrupted
   run; at-most-one-row replay proven (acceptance 10).
5. Torn/tampered state refusals: partial staging replay; committed-unledgered
   adopt-iff-byte-equal; tampered committed text/sidecar/fragment/ledger/
   checkpoint/manifest => refuse; duplicate entry locator forged in ledger =>
   refuse; sidecar snapshot-hash drift (stale source) => refuse; unknown file
   in run dir => refuse.
6. Smoke ladder: --max-retained 1 run needs no receipt; 6-run without receipt
   refuses; mint requires TTY (monkeypatched isatty False => refuse; True +
   StringIO phrase => receipt written O_EXCL, outside run dir); allow_empty or
   retained!=1 cannot mint; digest mismatch on consume refuses (acceptance 14).
7. Bounded equations: max-retained prefix + not_considered_after_bound
   accounting in ledger/receipt; max-messages stays a ceiling (acceptance 12).
8. Zero-output: without --allow-empty run() raises / main nonzero; with
   --allow-empty completes, receipt allow_empty=true, cannot mint.
9. SEAM (the handoff centerpiece): synthetic chat.db -> run() -> validator ->
   E.build_export({"imessage_sent_atomic": <run>/draft_manifest.jsonl}, ...)
   -> E.publish_package -> voicewright.load_author_corpus_package(package,
   receipt, scope) succeeds; one record per retained GUID; duplicates distinct;
   chat grouping preserved. Guard `pytest.importorskip("voicewright")` (module
   resolves on this box from D:\Code-PC\setec-voicewright). Optional stretch:
   assemble + plan_register_splits cross-chat normalized-duplicate component
   fixture (spec sec Required downstream integration, acceptance 16 tail) - if
   API cost is high, note as explicit residual instead.
10. Hostile-sentinel sweep extension: raw GUID/handle sentinels from the
    fixture db appear NOWHERE in rows/ledger/checkpoint/manifest/receipt/
    smoke receipt/stdout/exceptions except the 3 named raw-ID stores
    (acceptance 8).
11. Manifest-consumability micro-check: every emitted entry passes exporter
    `_validate_source_entry` + `_source_locators` without degradation.

### Deliverable file updates still to do
- capability YAML: flip status/do_not_use_when/outputs wording to live
  implemented posture (keep length_floor_words: 0, no count bump).
- Golden `_golden_capabilities/acquire_imessage_sent_atomic.json`: regenerate
  to match YAML (drop-in fragment; check tools/check_capabilities_drift.py).
- changelog.d/imessage-atomic-acquisition.md: rewrite for live row contract.
- ROADMAP.md: reconcile the atomic-acquisition entry.
- references/acquire-corpus-pattern.md: already has an atomic exception
  section from Codex; re-verify wording still true once CLI is live.
- Regenerate calibration-readiness output (tools/gen_calibration_readiness.py);
  run tools/check_docs_freshness.py, tools/check_capabilities_drift.py,
  gen_calibration_readiness.py --check, git diff --check.
- Suggested two-commit split (handoff return contract): (1) spec + acquirer +
  capability/golden + acquisition tests; (2) exporter integration + exporter
  tests + docs/changelog/roadmap. NOTE: exporter-side work was already done by
  Codex in the existing 6 modified files; commit 2 mainly = docs/changelog/
  roadmap + any seam-test additions touching exporter tests.

## VALIDATION COMMANDS (for the resuming session)
```
cd D:\Code-PC\_wt_voiceprint_imessage_atomic
$env:PYTHONUTF8='1'
py -3.12 -m py_compile plugins/setec-voiceprint/scripts/acquire_imessage_sent_atomic.py plugins/setec-voiceprint/scripts/author_corpus_export.py
py -3.12 -m pytest plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent_atomic.py plugins/setec-voiceprint/scripts/tests/test_acquire_imessage_sent.py plugins/setec-voiceprint/scripts/tests/test_author_corpus_export.py -q --basetemp=D:\Code-PC\_tmp_imsg_atomic_bt\runN -p no:cacheprovider
py -3.12 -m pytest -q --basetemp=D:\Code-PC\_tmp_imsg_atomic_bt\fullN -p no:cacheprovider   # full suite
py -3.12 tools/check_capabilities_drift.py ; py -3.12 tools/gen_calibration_readiness.py --check ; py -3.12 tools/check_docs_freshness.py
git diff --check
```
Known env noise: os.fchmod is POSIX-only (skips); never bare `py file.py`
(shebang lands on Store 3.13 CPU python); ASCII-only source (cp1252 console).

## HARD-RULE ATTESTATIONS (this session)
- No real iMessage database touched; no private corpus processed; no
  model/GPU/training/activation; no network acquisition; no commit; no push;
  no stash/reset/checkout-discard; forbidden worktrees untouched.
- Only reads outside this worktree: handoff file, primary-clone
  setec-voicewright sources (import-path check + two function signatures),
  author_corpus_export/acquisition_core in this worktree.

## CODEX PC HAND-BACK UPDATE - WIP, NOT READY (2026-07-18)

The live-row implementation now exists in the producer module, but it is a
work-in-progress transfer snapshot, not a merge-ready or Mac-run-ready build.
The interrupted strict-validator edit was repaired to syntactic validity.
`_prepare_live_bootstrap_for_run()` now owns the parent descriptor and lock as
one unit: acquisition failure closes the parent descriptor, every later
bootstrap failure releases the lock and closes the parent descriptor, success
returns the still-held cleanup tuple to `run()`, and the promoted final
descriptor is closed before transfer.

### Concrete review findings still open on the Mac continuation

1. **Private row publication is not yet equivalent to the bootstrap's private
   filesystem contract.** The WIP writer uses `Path` operations. Replace it with
   descriptor-relative, no-follow publication that checks regular-file/
   directory type, owner, mode, link count, and stable identity; fsync every
   file and containing directory at the specified boundaries. Do not treat the
   current `_write_new_file`, `_atomic_rewrite`, or `os.rename` path as final.
2. **Kill/recovery proof is incomplete.** Fault labels now exist for text,
   sidecar, fragment, row commit, ledger, checkpoint, manifest, and receipt,
   and missing-after-ledger checkpoint repair was started. Add the full kill
   matrix and prove byte-identical completion after every boundary, especially
   ledger-published/checkpoint-missing recovery and at-most-one-row replay.
3. **Adoption and staging cleanup need journal authority.** A committed but
   unledgered directory must be adoptable only as the one journal-bounded next
   row. Preflight the complete rows/staging inventory before mutation; do not
   delete a staging directory merely because its bytes look replayable.
4. **Validator/receipt closure is WIP.** The strict validator now starts to
   close top-level inventory, exact sidecar/fragment keys, source snapshot
   rehash, option/map hashes, receipt schema, and count equations. Re-review it
   as one whole after the interrupted edit. Rebuild authoritative receipt
   fields from the snapshot, options, maps, ledger, manifest, and tree rather
   than accepting receipt values as their own authority.
5. **TTY approval is too permissive.** Before minting, call the completed-run
   validator; require both source and destination to be inside the intended
   private root while the destination remains outside a run directory; compare
   the confirmation phrase exactly (current `.strip()` accepts added
   whitespace); retain exclusive-create behavior. Add stale-source/smoke-digest
   and allowed-empty refusal tests.
6. **The downstream seam is not yet strong enough.** The current synthetic
   test proves producer output is accepted by `author_corpus_export` and
   `voicewright.load_author_corpus_package`. Extend it through the real split
   planner: equal-content events remain distinct, same-chat grouping locks the
   split, and cross-chat normalized duplicates form one duplicate component.
7. **Public posture currently overstates readiness.** Capability/golden,
   changelog, and ROADMAP wording were moved to an implemented/live posture
   before the review findings above closed. Reconcile them to an honest WIP or
   wait to publish them until the Mac implementation and tests are terminal.

No real Messages database has been read by this PC continuation. No commit,
push, activation, model, GPU, provider, or private-corpus action occurred.

### Transfer validation at hand-back

- `py -3.12 -m py_compile` passes for both the producer and exporter modules.
- The narrow off-macOS refusal test and the actual synthetic
  producer-to-exporter-to-Voicewright seam test pass.
- The older direct `publish_planned_rows` unit fixture now fails the stricter
  top-level validator because it never creates `source-snapshot.db`; this is an
  obsolete synthetic fixture shape, not evidence that the new validator is
  closed. Replace it with a real `_synthetic_fixture_bootstrap` tree on Mac/next
  session, then finish the validator review before treating either as authority.
