# Spec 77 — iMessage conversational-register isolation

Date: 2026-07-26

## Decision and scope

The owner admits the reconciled iMessage corpus as its own conversational
register. The canonical register value is `message.imessage`. The staged
proposal's provisional `text.personal` label is replaced at registration:
that older value is already used by historical Voicewright training fixtures
and is not a corpus-specific register. `message.imessage` is never an
essay-family register.

Registration is limited to the clean pre-AI, floor-qualified population:

- 9,221 contiguous author turns;
- 363,083 words;
- minimum turn length: 20 words;
- `use: ["voice_profile"]`;
- `privacy: "private"`.

The registered rows are available only for a `message.imessage`-scoped voice
profile. They are not editor training pairs, do not receive `baseline` use,
do not enter a pooled author reference, and are not authorized for CPT.

The reconciled source denominator remains 42,107 pre-AI turns / 606,547 words.
The 32,886 pre-AI turns below the 20-word floor (243,464 words) stay retained
and flagged but are excluded from the computed profile. The 15,056
unknown-status turns (290,932 words) stay retained and are not registered by
this decision.

Activation, broader use, and future CPT inclusion remain false/owner-gated.

## Mechanical guard

`manifest_validator.py` must:

1. recognize `message.imessage` as an owner-approved register;
2. emit an error when a `message.imessage` row contains `baseline` in `use`;
3. allow private `message.imessage` rows whose only use is `voice_profile`.

`stylometry_core.py` must refuse, rather than warn, before feature extraction
when a profile or distance baseline combines at least one `message.imessage`
entry with any entry whose register is not exactly `message.imessage`. A missing,
blank, or non-string register counts as a different register and therefore
fails closed. A baseline composed exclusively of `message.imessage` entries is
allowed. Existing behavior for mixtures that do not contain `message.imessage`
is unchanged.

The guard applies to every Voiceprint path that forms a stylometric author
reference:

- `stylometry_core.build_profile()`;
- `stylometry_core.compare_to_baseline()`;
- `voice_distance.bootstrap_compare()`, including direct callers that bypass
  `compare_to_baseline()`;
- `voice_drift_tracker._load_manifest_entries()` and
  `voice_drift_tracker.build_period_profiles()`.

Each path must call the shared assertion before feature extraction or
period/profile centroid construction.

The register classifier maps `message.imessage` to its existing
`short_social` descriptive family so the manifest vocabulary remains total.
That classifier-family mapping is not a baseline-composition permission: the
exact `message.imessage` guard still requires register-scoped references.

The companion Voicewright consumer must separately refuse `message.imessage`
records at every training/revision materialization seam. That consumer guard
lands in its own repository and PR because the private author-corpus loader's
current record contract permits profile rows to reach training materializers.

## Registration procedure

The private registry append must be atomic and private:

1. select only staged proposal rows with `ai_status: pre_ai_human` and
   `word_count >= 20`;
2. set `register: message.imessage`, `use: ["voice_profile"]`, and preserve
   private provenance and whole-artifact hashes;
3. validate the full candidate manifest with the guarded validator;
4. prove that the existing pooled selector — the conjunction
   `"baseline" in use AND "voice_profile" in use` — selects a byte-identical
   pre-existing row set before and after the append; the new
   `use: ["voice_profile"]` rows cannot satisfy that selector;
5. publish with a no-partial-write private replacement;
6. retain the temporary fresh-A reference and failed private staging tree.

No prose, per-message identifier, contact name or handle, or machine-local
path may appear in a code change, PR, or registration record. The record may
contain only aggregate counts, register/use labels, authorization flags,
dates, and whole-artifact hashes.

## Acceptance tests

- `message.imessage` plus `baseline` use is rejected by the validator.
- Private `message.imessage` plus `voice_profile` only is accepted.
- A `message.imessage`-only profile and distance baseline is accepted.
- A `message.imessage` plus essay-register baseline is rejected before feature
  extraction.
- A `message.imessage` plus missing-register baseline is rejected before feature
  extraction.
- Direct bootstrap comparison and period-profile/drift composition apply the
  same refusal and cannot bypass it.
- Voicewright's ordinary-target, revision-group, and mirror-group
  materializers refuse selectable `message.imessage` rows before yielding prose.
- Existing non-message mixed-register warning behavior remains unchanged.
- The private append is exactly 9,221 rows / 363,083 words.
- The forward denominator is recorded as 42,107 turns / 606,547 words.
- The pooled pre-existing reference remains 1,025 rows / 1,793,410 words with
  an identical selection digest.
- Registration is true; activation, editor-pair use, pooled-reference use,
  essay-register use, and CPT use remain false.

## Out of scope

The staged proposal is not converted into a Voicewright sealed
`records.jsonl` package in this change. It currently lacks the required
producer receipt, and package import or activation is not part of this owner
ruling.
