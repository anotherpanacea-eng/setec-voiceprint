# Spec — stop redistributing the Brysbaert concreteness dataset

**Status:** proposed.
**Date:** 2026-08-30.
**Owner:** `setec-voiceprint`.

## 1. Decision

Remove `plugins/setec-voiceprint/data/brysbaert_concreteness.csv` from the default branch and
all future releases. The source supplementary file has no clear redistribution permission, so
SETEC should not ship a converted copy. Historical tags/source archives are immutable evidence
and will still contain the old file; release notes must identify the first clean version and
supersede those releases for redistribution. Do not rewrite tags. Keep the explicit fetcher so
an individual user may choose to obtain the supplementary file from its publisher for local
use.

This change does not replace the dataset, add a dataset manager, or add a new licensing gate.
Absence is the normal installed state.

## 2. Runtime behavior

The concreteness loader exposes an availability check and raises its existing clear missing
data error when a caller explicitly asks it to load data that is absent. It accepts an
overridden path so tests and users with a locally acquired file can exercise the same loader.

Availability is a statement about the file's CONTENTS, not about whether it opens. A path that
exists but is a 0-byte file, a header-only CSV, a table whose `conc_mean` column is entirely
empty, or a file whose columns are not `word` / `conc_mean` is UNAVAILABLE, because loading it
to an empty table and reporting success made every dependent detector emit a confident `0.0`
that `variance_audit` then banded "within typical range" — a fail-open. The unavailability
reason is named, and the three reasons are distinct because the operator fix differs:

| Reason | Condition | Operator fix |
| --- | --- | --- |
| `data_not_installed` | no file at the path | run the fetcher |
| `data_malformed` | present, but not this dataset (wrong columns, non-UTF-8 bytes, no usable rating rows) | inspect or delete the file, then re-fetch — re-fetching onto it is not the advice |
| `data_unreadable` | present, but cannot be opened (permissions, a directory in its place) | fix the path or its permissions; re-fetching does not help |

Two properties of `data_malformed` are load-bearing:

- **Rating values are validated, not just the table's structure.** Every parsed rating must be
  finite and inside the documented 1-5 scale (`concreteness.CONC_SCALE_MIN` /
  `CONC_SCALE_MAX`). The check fails CLOSED: a single non-finite or out-of-scale value condemns
  the whole file, because such a file is not the published dataset and no "how many fabricated
  ratings are tolerable" threshold is defensible in a forensics tool. A cell that does not parse
  as a number at all (empty, `n/a`) means the rating is ABSENT for that row and is skipped; the
  row-count floor catches it when it is the whole file. The unavailability report names the
  specific finding (which word, which value, which column), not only the class of problem.
- **One content floor, one value oracle, and one counted quantity, shared with the fetcher.**
  `concreteness.MIN_USABLE_ROWS` (10,000 — well below the published table's 39,954) is the floor
  at the conventional install path and `fetch_brysbaert.MIN_CONVERTED_ROWS` IS that constant;
  `concreteness.is_valid_rating` is the single value check and `convert_xlsx_to_csv` calls it on
  every `Conc.M` cell before `os.replace`; and both sides count DISTINCT lowercased words via
  `concreteness.rating_key`, because the loaded table is a dict and duplicated rows collapse in
  it. Sharing only the row floor was not enough, on two axes: an XLSX with one out-of-scale cell
  installed a full-length table the loader then rejected (while the malformed guidance named the
  converter as the remedy — a loop), and a duplicated upstream table of 12,000 rows over 6,000
  distinct words cleared a row floor the loader's distinct-key floor then failed. `--min-rows` below the loader
  floor is refused for the conventional path for the same reason; it stays available for any
  other `--output`, which is the experiment seam and is not expected to load from the
  conventional path. A caller that names an explicit `data_path` gets the
  `MIN_USABLE_ROWS_OVERRIDE` floor instead: naming a path is the documented bring-your-own /
  test seam (§4's tiny test-owned CSV), and only the path nobody chose carries the full floor.

An unreadable path is never reported as malformed. `Path.exists()` propagates `PermissionError`,
so the existence probe lives inside the guarded region; otherwise a permissions problem is
reported with malformed's guidance — "inspect or delete the file" — which would destroy a good
dataset.

The availability check never raises. It is called at module import time by test markers and by
composite audits, so a `KeyError` or `UnicodeDecodeError` escaping it takes down every entry
point (and pytest collection) rather than degrading one optional signal.

Callers divide into two groups:

- Composite profiles such as `argmove_profile` omit only the concreteness-derived field when
  the file is absent. Other stance, agency, abstract-general-domain, and aggregate signals
  continue to run; their scores must not be replaced by zero.
- Concreteness-dependent detector entry points (`image_conjunction`, `prestige_metaphor`, the
  opt-in AIC-8 portion of `variance_audit`, and `aesthetic_authority_audit`) report explicit
  unavailability rather than a traceback, fabricated zero score, or silent success. For the two
  that emit the `schema_version` 1.0 envelope, that means the R3 shape of
  `plugins/setec-voiceprint/references/setec-normalized-entrypoint-spec.md` §4 — ONE envelope shape for success and
  failure — with **top-level** `available: false`, a `warnings` entry that explains it, and
  `reason_category: missing_dependency`, so `setec_run._emit_surface_envelope` can branch on it.
  A top-level `available: true` carrying a nested `results.available: false` is NOT the
  contract: a consumer branching on R3 never reads it.

The exact unavailable shape should follow each command's existing JSON/output conventions.
Human-readable CLI output names the missing optional dataset and the explicit fetch command.
No command downloads data automatically.

The authoritative dependency inventory below is written against
`plugins/setec-voiceprint/scripts/`. The repo-root `scripts` is a **symlink** to that directory,
not a second copy, so there is exactly one tree to change and nothing to keep in sync:

| Module/entry point | Exact missing-data contract |
| --- | --- |
| `concreteness.py` | `is_available()` is false (contents- and value-validating, never raising); explicit load raises `FileNotFoundError` containing the fetch command when the file is absent, and a typed present-but-unusable error carrying `data_malformed` / `data_unreadable` otherwise — including for a permissions failure, which never surfaces as a bare `PermissionError` |
| `argmove_profile.py` | returned vector omits only `abstraction.mean_concreteness`; CLI still exits 0 |
| `argument_decision_audit.py` (indirect) | `results.reused_signals.available` stays true, B1/B2 remain, and only `results.reused_signals.signals.abstraction.mean_concreteness` is absent |
| `image_conjunction.py` | function/CLI JSON is `{signal_path, available:false, reason:"data_not_installed"}`; CLI exits 0 and emits no `value`/`conjunctions` |
| `prestige_metaphor.py` | envelope top-level `available: false` + `reason_category: missing_dependency` + a `warnings` entry naming the reason; `target.words` is the target's real word count; CLI exits 0 and emits no density/domain fields |
| `variance_audit.py --aic8` | `results.aic_8_9.diagnostics.aic8_available=false` and `aic8_unavailable_reason` carrying the named reason (`aic9_available` is symmetric on the AIC-9 branch); the human summary names it for EVERY unavailable reason, not only the missing-file one; the two AIC-8 signal blocks are absent and the rest exits 0 |
| `aesthetic_authority_audit.py` | envelope top-level `available: false` + `reason_category: missing_dependency` + a `warnings` entry naming the reason; CLI exits 0 and emits no component/compound blocks |

There is no second script tree to mirror: the repo-root `scripts` is a symlink to
`plugins/setec-voiceprint/scripts`, so a single edit is visible under both paths. A repository
search for imports of the four foundation modules above is the completion backstop.

## 3. Acquisition and documentation

`plugins/setec-voiceprint/scripts/fetch_brysbaert.py` remains an opt-in convenience. Its default
output is the conventional ignored local path
`plugins/setec-voiceprint/data/brysbaert_concreteness.csv`. It downloads directly from the
publisher and converts locally. The conversion is atomic and validated: rows stream into a temp
file in the output's own directory and are moved onto the output path only after the row count
clears `concreteness.MIN_USABLE_ROWS` counted as the loader counts it (distinct
`concreteness.rating_key` values) and every rating cell passes `concreteness.is_valid_rating`,
so a successful conversion cannot install a file the loader rejects; `--min-rows` overrides the floor if the publisher ever ships a smaller table, but is
refused for the conventional install path, so an interruption, a full disk, or a bad cell cannot leave a header-only or
truncated table at the install path. `--keep-xlsx` keeps the publisher's raw file only when the
conversion SUCCEEDED, and the ignore rule covers the whole `brysbaert_concreteness.*` family so
neither the CSV nor the XLSX can be committed. Documentation must state that SETEC does not grant permission
to download, use, or redistribute the source data and that the user is responsible for the
source terms.

Add the generated local paths to `.gitignore` (or the closest scoped ignore file) — the CSV and
the raw XLSX `--keep-xlsx` parks beside it — so a locally fetched copy cannot be accidentally
committed. The fetcher may print the citation/source URL
and final local path; it needs no manifest, checksum ledger, token, background job, registry,
or receipt.

Implementation order is: remove the tracked CSV, add the exact ignore rule, then assert with
`git check-ignore` that a locally generated file at that path is ignored.

## 4. Compatibility

The public argument-decision contract already treats these reused context signals as optional.
Removing the bundled CSV must not change B1/B2 aggregate decisions, must not make
non-concreteness reused signals unavailable, and must not alter results when a valid local CSV
is supplied. Any checked-in fixtures that contain historical concreteness values may remain as
test evidence, provided they do not embed the source word-rating table.

The compatibility oracle is the existing owned argument-decision fixtures and exact expected
B1/B2 output. With data absent, the only permitted missing reused-context field is
`abstraction.mean_concreteness`; all other existing fields remain exact. A tiny test-owned CSV
uses the documented header schema and invented word/value rows to prove path override and
detector behavior. No upstream rating row is copied into tests.

The APODICTIC desktop payload does not itself ship this dataset. Correcting stale Tauri M0
wording is a separate `apodictic-tauri` documentation follow-up after the first clean SETEC
release; it must retain the other license/SBOM gates and must not claim a payload dependency.

## 5. Tests and release acceptance

- Assert the production CSV is absent from the tree and ignored at its conventional path.
- Assert future source archives derive from a tree without that path. There are no custom
  binary release assets; the tag source archive is the release surface.
- Exercise parsing and scores with a tiny synthetic CSV owned by the test suite.
- Exercise every dependent entry point with the production file absent and assert explicit
  unavailability rather than traceback or false zero.
- Assert `argmove_profile` still emits its unrelated signals and omits only mean concreteness.
- Assert B1/B2 aggregate decisions are unchanged and optional-signal availability is honest.
- Run the repository's focused and normal local test gates.
- Add the required changelog fragment and release note stating that users who want the optional
  detectors must run the explicit fetcher themselves.
- Update current shipped claims in the data README, fetcher/loader docstrings, detector help,
  plugin requirements comment, baseline registry notes, script README, main README/ROADMAP,
  and current reference guidance. Historical changelog entries and evidence fixtures remain
  historical and are not rewritten.

## 6. Non-goals

- Finding or publishing a substitute dataset.
- Mirroring the source, accepting publisher terms for the user, or automatic download.
- Cryptographic provenance records or a generalized dataset installation framework.
- Recalibrating the detectors; unavailable is preferable to an unvalidated substitute.
