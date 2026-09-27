# CX5 Mirrulations unfinished-path audit — 2026-09-26

Source pin: `780cab5650309b21fe999b5d5ea4d51a80c4f199` (fresh main).
All file:line references below resolve at that commit, not at a moving branch.
`S` means `plugins/setec-voiceprint/scripts/`; `T` means `S/tests/`.
For example, [the audited source](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/780cab5650309b21fe999b5d5ea4d51a80c4f199/plugins/setec-voiceprint/scripts/acquire_mirrulations.py#L151).
The script advertises `SCRAPER_VERSION = "1.2"` at `S/acquire_mirrulations.py:75`.

This is a source audit with synthetic tests, not a bucket survey, corpus-admission
receipt, or implementation. No corpus, network acquisition, model, training,
provider, Open Grants or Zenodo operation was performed. The deferred-spec sweep
is in the companion report. The two reports and a unique docs changelog are the
entire proposed change.

## Finding: the three raises are interface boundaries

The grep census finds exactly three `NotImplementedError` sites, all inside
`ObjectStore`. No unfinished production adapter is established by those raises.
The three-bin vocabulary is **already delivered/superseded**, **intentional
deferral**, and **remaining actionable gap**. An abstract method is classified
with the delivered concrete behavior, not mislabeled a future feature.

| Site | Classification and evidence | Prerequisite for any later change |
|---|---|---|
| `S/acquire_mirrulations.py:157`, `ObjectStore.list_keys` | Already delivered: fixture implementation at :179; S3 paginator at :225. The abstract class documents the adapter seam at :152. | None to deliver listing. A new store would need prefix filtering, pagination and empty-page tests; no new store is requested. |
| `S/acquire_mirrulations.py:160`, `ObjectStore.get_bytes` | Already delivered: fixture lookup at :185; S3 body fetch at :231. The default driver constructs the concrete S3 store at :935. | None to deliver text fetch. Changes to fetch limits/errors need a separately claimed contract and tests. |
| `S/acquire_mirrulations.py:163`, `ObjectStore.get_metadata` | Already delivered: fixture at :189; concrete lazy, bounded metadata fetch at :239; bounded reader at :127. Metadata support is opt-in, not missing. | None for standard metadata mode. Additional source layouts require explicit grammar/join/custody decisions and fail-closed tests. |

Directly constructing `ObjectStore` or injecting an incomplete subclass can reach
the raises. That is a misuse of the abstract seam; `run(..., store=None)` does
not instantiate it. Tests use `FixtureObjectStore`; this audit does not propose
replacing abstract raises with fabricated values or silent empty results.

## Actual follow-ups and historical reconciliation

| Concrete item | Classification | Source evidence and exact prerequisites |
|---|---|---|
| Inline removal of near-duplicate campaign variants | Intentional deferral in this acquirer; standalone capability already delivered | `S/acquire_mirrulations.py:16` and :948 explicitly say exact-hash only. `S/near_dup_dedup.py:15` describes the separate pass; :21 says acquisition scripts do not call it automatically; :23–27 describe LSH candidates with exact confirmation and possible false negatives. Do not build a second deduplicator from this stale follow-up wording. Before integrating, owner must decide inline versus a separately invoked manifest pass, duplicate-unit/threshold policy, preservation/export semantics and admission authority. |
| Per-comment metadata enrichment | Already delivered | `S/acquire_mirrulations.py:32`, :379, :568, :708 and :925 establish opt-in standard mode, source projection, receipt checking, emission and custom-bucket refusal. The merged metadata work is in main history (`c9b7f720`, `aa37aacd`, `ccdd51f1`, merge `d5e7d502`). It is not an unrun CX5 implementation task. |
| Authored-date inference | Intentional exclusion, not a defect | `S/acquire_mirrulations.py:34` refuses the inference. `T/test_acquire_mirrulations.py:454` and :561 exercise source dates independently of authored dates. A later change would need actual authored-date evidence and reviewed semantics; source submission/posting timestamps alone do not supply it. |
| Current live layout/connectivity | Remaining actionable verification gap only when a real pull is authorized | The source's layout verification is dated at `S/acquire_mirrulations.py:23`; operator prefix/dry-run instructions are at :27–31. This audit does not validate today's remote layout. A future authorized acquisition should freeze selected dockets, check prefix hit counts, validate standard-mode joins and reconcile acquired/filtered/duplicate/skipped totals before bulk admission. No bulk run is authorized here. |
| Empty successful acquisition | Already delivered guard | `S/acquire_mirrulations.py:986` onward refuses zero output except allow-empty or dedupe-only reruns. `T/test_acquire_mirrulations.py:435` covers it. A nonempty skip log is not success. Preserve this behavior in later adapter work. |

Historical searches covered fetched target branch history, current PR file scopes,
and available coordination handoffs. They found the prior metadata delivery,
not a completed exact two-report CX5 sweep. This is a bounded search conclusion,
not proof that no unpublished report exists elsewhere. The proposed Grok
follow-up was not dispatched and supplies no audit evidence here.

## Small ordered next queue (proposals, no implementation authority)

1. **Optional documentation reconciliation.** Clarify that the LSH follow-up is
   an operator-run standalone pass today. Acceptance: anchors match the actual
   separate entry point and explicitly preserve exact-only acquisition; no claim
   of automatic campaign removal. Requires a new claim for the existing script
   or reference document; this report claim owns neither.
2. **Only after owner policy: dedup integration contract.** Decide whether there
   is any need for inline integration. If so, synthetic identical/near/independent
   comments must preserve text/provenance, exact-confirm every destructive edge,
   disclose candidate-recall limits, and reconcile all disposition counts.
   A manifest/export or shared acquisition-core change needs separate ownership
   review. Do not silently promote calibration or pool eligibility.
3. **Only after acquisition authorization: current-layout verification.** Small
   bounded dry-run and metadata join verification against owner-chosen dockets;
   no authored-date inference, corpus publication or keyed pulls. This is an
   operational verification unit, not a missing-code diagnosis.

No immediate production fix is justified by the abstract-method census. Current
homogeneity design and register-diagnostics ownership do not overlap these report
paths; they remain excluded, as do shared ROADMAP/board edits. Recheck all live
claims and PR scopes before anyone takes a proposed next unit.

## Validation and limits

At the pinned source, on Python 3.12:

```text
py -3.12 -m pytest plugins/setec-voiceprint/scripts/tests/test_acquire_mirrulations.py -q -k "not test_make_s3_store_requires_boto3" -p no:cacheprovider --basetemp <fresh-local-temp>
95 passed, 1 deselected
```

The excluded test at `T/test_acquire_mirrulations.py:215` constructs a real boto3
client when available; excluding it keeps this audit in the synthetic/in-memory
lane. Fake SDK client tests at :1125, :1143 and :1159 were included. The suite
covers metadata custody, independent timestamps, unsupported-key refusal,
continuation after failures, dry-run, dedupe and empty-output behavior. It does
not establish live S3 health, full-suite clearance or corpus fitness. No baseline
test failure occurred in this focused run. Reports remain draft/unarmed; no
hosted jobs, readiness, merge or release is requested. Any later build retains
its independent correctness/posture reviews and required Claude counter-review.
