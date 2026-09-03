# A4 — RAID/MAGE fetcher integrity repair

**Status:** Implementation-ready draft; requires independent spec review before build
**Date:** 2026-09-03
**Repository:** `anotherpanacea-eng/setec-voiceprint`
**Base:** `origin/main` at `d2a5bfc1fbf7ba92ccea7e24b0807dac18fdc081`
**Delivery unit:** Exactly one bounded repair PR

## 1. Goal

Repair the already-shipped RAID and MAGE calibration fetchers so that:

1. the Hugging Face revision written to provenance is the revision used for
   license metadata, file listing, and every file download;
2. user-facing and generated license text reports the exact observed wrapper
   license without asserting that all underlying corpus text inherits that
   license or is publicly redistributable;
3. RAID's `--no-adversarial` option fails safely when the current monolithic
   CSV layout cannot honor file-level adversarial exclusion; and
4. directly implicated roadmap, provenance, help, and changelog text agrees
   with those behaviors.

This is a repair of existing fetchers, not a new acquisition surface and not a
calibration run.

## 2. Verified current anchors

All anchors below were read from the base named above.

| Concern | Existing anchor |
|---|---|
| RAID fetcher | `plugins/setec-voiceprint/scripts/calibration/fetch_raid.py` |
| MAGE fetcher | `plugins/setec-voiceprint/scripts/calibration/fetch_mage.py` |
| RAID converter | `plugins/setec-voiceprint/scripts/calibration/raid_to_manifest.py` |
| MAGE converter | `plugins/setec-voiceprint/scripts/calibration/mage_to_manifest.py` |
| RAID fetcher tests | `plugins/setec-voiceprint/scripts/tests/test_fetch_raid.py` |
| MAGE fetcher tests | `plugins/setec-voiceprint/scripts/tests/test_fetch_mage.py` |
| Corpus/provenance guidance | `plugins/setec-voiceprint/scripts/calibration/PROVENANCE.md` |
| Status contract | `ROADMAP.md` |
| Optional dependencies | `plugins/setec-voiceprint/requirements-calibration.txt` (`huggingface_hub>=0.23,<1`, `pyarrow>=14`) |

The authoritative roadmap reconciliation already says both fetchers shipped
(`ROADMAP.md:203-214`), while the older corpus-track prose still describes them
as pending and carries stale license statements (`ROADMAP.md:415-423`). Line
448 acknowledges that section is stale.

Existing delivery receipts are merged PRs #9 (fetchers/converters), #10
(wrapper-license acceptance), #11 (real CSV layout), and #12 (converter schema
conformance). There is no dedicated tracked RAID/MAGE fetcher spec or issue.
The local `internal/SPEC_calibration_toolchain.md` is gitignored, centers the
original EditLens toolchain, and only supplies the general future-fetcher
pattern. Closed issue #133 deliberately defers resumability work until a real
scale trigger.

### 2.1 Public metadata snapshot used for offline fixtures

Tests may encode only these public metadata facts; they must not include corpus
rows or payload bytes.

| Corpus | Repository | Revision | Wrapper metadata | Current data files |
|---|---|---|---|---|
| RAID | `liamdugan/raid` | `865cac74188466cb0c3b7574a10204007b57a459` | HF card `mit`; upstream GitHub `LICENSE` is MIT | `train.csv`, `test.csv`, `extra.csv` |
| MAGE | `yaful/MAGE` | `342663f0a2b775455c023f5d36a1341ff0ec5402` | HF card and upstream GitHub `LICENSE` say `apache-2.0`; upstream README also displays a conflicting CC BY 4.0 badge | `train.csv`, `valid.csv`, `test.csv`, `test_ood_set_gpt.csv`, `test_ood_set_gpt_para.csv` |

The wrapper declaration is metadata, not proof that every aggregated source
row has the same redistribution terms. Existing repository guidance already
states this explicitly for MAGE and treats MAGE content as local-only
(`references/calibration-findings-2026-05-11-mage.md:38-59` and
`PROVENANCE.md:241-247`). Existing RAID provenance likewise records local-only
use (`PROVENANCE.md:290-297`).

## 3. Defects to repair

### 3.1 Revision is recorded but not enforced

Both scripts currently resolve a SHA, then call `list_repo_files` and
`hf_hub_download` without `revision=...`. A repository update between calls can
therefore produce files from a different snapshot than the receipt names.

Affected symbols:

- RAID: `_verify_license`, `_resolve_revision`, `_list_repo_files`, `_download`,
  `_write_revision_record`, `main`.
- MAGE: the same six symbols.

### 3.2 License and redistribution prose overclaims

The RAID module/help/NOTICE calls RAID Apache-2.0; current authoritative
repository metadata says MIT. The MAGE module/help/NOTICE calls MAGE MIT;
current authoritative repository metadata says Apache-2.0, while MAGE's README
has a separate CC BY 4.0 badge and its corpus aggregates differently licensed
sources. Both generated notices claim that converted per-row text inherits one
blanket license. Those claims are not supported by the metadata the scripts
actually inspect.

### 3.3 RAID `--no-adversarial` cannot honor its promise

`fetch_raid.py` filters adversarial tokens from filenames. Current RAID stores
attack and non-attack rows together in three monolithic CSV files, so
`--no-adversarial` selects the same files and transfer size as the default. The
help still promises a reduction from roughly 17 GB to roughly 1.4 GB, and the
receipt can record `include_adversarial: false` even though the downloaded CSV
contains attack rows.

## 4. Required implementation

### 4.1 One pinned snapshot per invocation

For each fetcher:

1. Resolve the current repository revision exactly once with
   `HfApi.dataset_info(HF_REPO_ID)` and require a non-empty `sha`.
2. Re-read the dataset metadata at that exact SHA with
   `dataset_info(HF_REPO_ID, revision=revision)` for license verification.
3. List files with
   `list_repo_files(HF_REPO_ID, repo_type="dataset", revision=revision)`.
4. Download every selected file with
   `hf_hub_download(..., repo_type="dataset", revision=revision, ...)`.
5. Pass `revision` explicitly through `_verify_license`, `_list_repo_files`, and
   `_download`; no helper may silently fall back to `main` after resolution.
6. Write the receipt only after every selected download succeeds, as today.

The implementation may keep the existing functions or combine initial
revision resolution into a small dataset-snapshot helper. It must not introduce
a shared cross-fetcher module in this PR.

License matching must be exact after lowercase/whitespace normalization. Do
not use substring acceptance. The accepted wrapper identifiers remain the
currently supported permissive set (`mit` and canonical Apache-2.0 aliases),
because the upstream historical declarations conflict. The exact normalized
value observed at the pinned revision must be retained for output and receipt.

### 4.2 Truthful wrapper-license and local-only posture

For both scripts:

- Module documentation, argparse descriptions/help, terminal summaries,
  generated `NOTICE.md`, ROADMAP, and PROVENANCE must call the observed value a
  **repository wrapper-license declaration**.
- Corpus content and converted per-row text must be described as **local-only
  in SETEC's operating posture**.
- Do not say public redistribution is permitted.
- Do not say per-row text "inherits" MIT, Apache-2.0, or another blanket
  wrapper license.
- Do not assert that wrapper licensing alone authorizes shipping calibrated
  defaults. Aggregate calibration results remain governed by the existing
  provenance and policy gates.
- If `--skip-license-check` is used, output and NOTICE must say the check was
  skipped and must not substitute a license conclusion.

Historical release entries in `CHANGELOG.md` are an audit trail and must not be
rewritten. Add a current `changelog.d/fix-raid-mage-fetcher-integrity.md`
fragment with a `### Fixed` heading.

### 4.3 RAID monolithic-CSV `--no-adversarial` contract

The fetcher must distinguish file-level exclusion from row-level exclusion.

When `--no-adversarial` is requested and any selected RAID source is one of the
current monolithic root CSVs (`train.csv`, `test.csv`, `extra.csv`):

1. Do not download any file.
2. Print a concise error explaining that the hosted CSV co-locates attack and
   non-attack rows, so the fetcher cannot reduce the transfer or exclude attack
   rows.
3. Direct the operator to fetch the desired subset without
   `--no-adversarial`, then run `raid_to_manifest.py --no-adversarial` to apply
   row-level exclusion during conversion.
4. Return exit code `4`, the existing "requested selection cannot be
   satisfied" class.

`--dry-run --no-adversarial` must perform the same check and return `4`, while
remaining network-metadata-only and write-free. It must not print a successful
"would fetch" summary for an unfulfillable request.

The existing filename-token behavior remains supported for a future or legacy
layout that truly separates adversarial variants into independently named
files. In that layout, `--no-adversarial` may return `0` and omit those files.

Do not add a streaming/data-server dependency, download-and-rewrite step, or
new override flag. Those would be a separate acquisition design.

### 4.4 Receipt schema

Keep the existing `.fetch_record.json` keys consumed downstream:

- `repo_id`
- `revision`
- `fetch_date`
- RAID: `subset`, `include_adversarial`
- MAGE: `split`

Add these flat fields to both receipts:

```json
{
  "record_schema_version": 2,
  "observed_wrapper_license": "mit",
  "license_check": "verified",
  "content_posture": "local_only"
}
```

Rules:

- `revision` is the exact SHA used for metadata, listing, and downloads.
- `observed_wrapper_license` is the normalized pinned-card value when verified;
  it is `null` when `license_check` is `"skipped"`.
- `license_check` is exactly `"verified"` or `"skipped"`.
- `content_posture` is exactly `"local_only"`.
- RAID `include_adversarial` continues to describe the actual downloaded file
  selection, not operator intent. A failed monolithic-CSV
  `--no-adversarial` invocation writes no receipt. A successful legacy
  file-separated invocation writes `false`; a normal invocation writes `true`.
- MAGE's existing receipt remains backward-compatible by addition only.

No downstream converter or calibrator change is required: current consumers
read `repo_id` and `revision` and ignore additional keys.

### 4.5 Directly implicated documentation

Modify only:

- both fetcher module docstrings, argparse/help text, and NOTICE templates;
- `ROADMAP.md:415-423` so the entries are past-tense shipped anchors with the
  current wrapper metadata and local-only posture; remove the stale "remain"
  wording and the later "mildly stale" qualifier at line 448;
- `PROVENANCE.md:150-184` so its corpus table/license explanation and RAID
  command sequence do not claim the old licenses or a reduced fetch from
  `fetch_raid.py --no-adversarial`;
- the new changelog fragment.

Do not broaden this PR into a general documentation refresh. Existing detailed
MAGE and RAID audit entries that already state local-only posture should remain
unchanged unless a directly contradictory wrapper-license label is on the same
line.

### 4.6 Authorized path set

The PR may change exactly these paths:

- `plugins/setec-voiceprint/scripts/calibration/fetch_raid.py`
- `plugins/setec-voiceprint/scripts/calibration/fetch_mage.py`
- `plugins/setec-voiceprint/scripts/tests/test_fetch_raid.py`
- `plugins/setec-voiceprint/scripts/tests/test_fetch_mage.py`
- `plugins/setec-voiceprint/scripts/calibration/PROVENANCE.md`
- `ROADMAP.md`
- `changelog.d/fix-raid-mage-fetcher-integrity.md`

One additional small fixture beneath
`plugins/setec-voiceprint/scripts/tests/fixtures/` is allowed only if both test
files consume it and inline public metadata would be less clear. No other path
is authorized by this spec.

## 5. Backward compatibility

- Script names, target directories, default subsets/splits, token handling,
  `--refresh`, `--dry-run`, and `--skip-license-check` remain available.
- Receipt keys used by downstream code (`repo_id`, `revision`) remain unchanged.
  New keys are additive; `record_schema_version` makes the stronger contract
  explicit.
- RAID `--no-adversarial` intentionally changes only where the existing command
  falsely claimed file-level exclusion on monolithic CSV. It now fails before
  download rather than silently transferring adversarial rows. This is a
  safety correction, not a compatibility-preserving success path.
- Legacy/file-separated RAID layouts retain the prior successful behavior.
- No converter CLI or manifest schema changes are allowed.

## 6. Exit behavior

The repaired scripts must preserve or explicitly use these codes:

| Code | Meaning |
|---:|---|
| `0` | Successful download/receipt or satisfiable dry-run |
| `1` | Required `huggingface_hub` dependency missing |
| `2` | Pinned wrapper-license declaration absent/unaccepted, or pinned license verification failed; `--skip-license-check` bypasses only this gate |
| `3` | Revision resolution, pinned metadata reload, or pinned file listing failed/incomplete |
| `4` | No matching files, or RAID `--no-adversarial` cannot be satisfied by the monolithic source layout |

Download/filesystem exceptions after selection are not redesigned in this PR;
they continue to terminate non-zero and must not produce a new receipt.

## 7. Tests

Update only `test_fetch_raid.py` and `test_fetch_mage.py` unless a tiny shared
public-metadata fixture file is demonstrably clearer than inline constants.

### 7.1 Required offline cases

Both fetchers:

1. Resolved SHA is passed to pinned `dataset_info`, `list_repo_files`, and every
   `hf_hub_download` call.
2. A fake API whose `main` advances after resolution cannot change the metadata,
   listing, or downloaded revision.
3. Current public wrapper values are accepted (RAID `mit`, MAGE
   `apache-2.0`); unrelated and substring-lookalike values are rejected.
4. Verified receipts contain all version-2 fields and preserve legacy keys.
5. Skipped checks produce `observed_wrapper_license: null`,
   `license_check: "skipped"`, and no affirmative license conclusion in NOTICE.
6. NOTICE says wrapper metadata/local-only and contains no blanket
   inheritance/public-redistribution claim.
7. Dry-run calls no download function and writes neither corpus files, NOTICE,
   nor receipt.
8. Revision/metadata/list failures preserve the exit table above and write no
   receipt.

RAID additionally:

9. The public metadata fixture containing root `train.csv`, `test.csv`, and
   `extra.csv` makes `--no-adversarial` return `4`, perform zero downloads, and
   direct row filtering to `raid_to_manifest.py --no-adversarial`.
10. The same assertion holds in dry-run mode.
11. A synthetic file-separated listing still filters attack-token files,
    succeeds, and records `include_adversarial: false`.

MAGE additionally:

12. The public filename fixture selects `valid.csv` for `validation` and all
    five current CSV data files for `all`.

### 7.2 Public/no-download firewall

- Automated tests must install a fake `huggingface_hub` module or monkeypatch
  every Hub call before invoking either CLI.
- Any unmocked network method must fail the test immediately.
- Tests may contain repository IDs, revisions, license strings, filenames, and
  public file-size metadata only.
- Tests must not contain corpus rows, excerpts, payload hashes derived from
  downloaded corpora, private manifests, or paths under an existing
  `ai-prose-baselines-private/` checkout.
- CI and review commands must not invoke either fetcher against the live Hub,
  even in dry-run mode. Live metadata verification belongs to review notes,
  not the automated acceptance gate.

## 8. Acceptance commands

From repository root, without network access:

```bash
env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q \
  plugins/setec-voiceprint/scripts/tests/test_fetch_raid.py \
  plugins/setec-voiceprint/scripts/tests/test_fetch_mage.py \
  -p no:cacheprovider

python3 tools/check_docs_freshness.py
python3 tools/gen_calibration_readiness.py --check
```

Then run the ordinary full repository test command required by the PR template.
No acceptance command may download RAID, MAGE, or any other dataset.

The PR is acceptable only if:

- all focused and repository-required tests pass;
- a planted moving-`main` test fails against the pre-repair code and passes
  against the candidate;
- a planted monolithic-CSV test fails against the pre-repair RAID code and
  passes against the candidate;
- no automated test opens the network;
- the diff is limited to the authorized path set in Section 4.6; and
- independent implementation review finds no remaining path that records one
  revision while reading another.

## 9. Explicitly out of scope

- Any corpus download, conversion, calibration run, threshold promotion, or
  change to private corpus artifacts.
- Pangram/EditLens or AITDNA fetcher repair, even though their current Hub-call
  pattern merits a separate revision-pinning audit.
- A shared fetcher library or broad deduplication refactor.
- Long-run sharding, explicit resume/checkpoint machinery, default-on caches,
  progress redesign, or reopening issue #133 without a new scale trigger.
- Streaming/filtering RAID rows before transfer, use of the Hugging Face data
  server, or a new dataset dependency.
- Converter behavior, manifest schemas, calibration math, thresholds,
  validation harnesses, or claim-license policy.
- Resolving upstream license ambiguity as a legal conclusion. This repair only
  reports exact observed repository metadata and enforces SETEC's conservative
  local-only posture.
- Rewriting historical `CHANGELOG.md` release entries.
- Push, PR creation, merge, release, deployment, publication, or issue posting
  as part of implementation.

## 10. Delivery sequence

1. Cut one feature branch from the exact current `origin/main` after rechecking
   the base.
2. Add the failing offline revision-pin, license, receipt, and monolithic-CSV
   tests first.
3. Repair RAID and MAGE file-locally; do not create a shared abstraction.
4. Reconcile the directly implicated docs and add the changelog fragment.
5. Run Section 8 without network access.
6. Obtain an independent implementation review on the exact candidate head.
7. Only after review clearance may the normal PR workflow begin; this spec
   grants no authority to push, open, merge, publish, or download data.
