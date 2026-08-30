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

Callers divide into two groups:

- Composite profiles such as `argmove_profile` omit only the concreteness-derived field when
  the file is absent. Other stance, agency, abstract-general-domain, and aggregate signals
  continue to run; their scores must not be replaced by zero.
- Concreteness-dependent detector entry points (`image_conjunction`, `prestige_metaphor`, the
  opt-in AIC-8 portion of `variance_audit`, and `aesthetic_authority_audit`) return their normal
  top-level envelope with an explicit `available: false` / `reason: data_not_installed` result,
  rather than a traceback, fabricated zero score, or silent success.

The exact unavailable shape should follow each command's existing JSON/output conventions.
Human-readable CLI output names the missing optional dataset and the explicit fetch command.
No command downloads data automatically.

The authoritative dependency inventory applies to both identical trees, `scripts/` and
`plugins/setec-voiceprint/scripts/`:

| Module/entry point | Exact missing-data contract |
| --- | --- |
| `concreteness.py` | `is_available()` is false; explicit load raises `FileNotFoundError` containing the fetch command |
| `argmove_profile.py` | returned vector omits only `abstraction.mean_concreteness`; CLI still exits 0 |
| `argument_decision_audit.py` (indirect) | `results.reused_signals.available` stays true, B1/B2 remain, and only `results.reused_signals.signals.abstraction.mean_concreteness` is absent |
| `image_conjunction.py` | function/CLI JSON is `{signal_path, available:false, reason:"data_not_installed"}`; CLI exits 0 and emits no `value`/`conjunctions` |
| `prestige_metaphor.py` | envelope `results.available` is false with the same reason; CLI exits 0 and emits no density/domain fields |
| `variance_audit.py --aic8` | `results.aic_8_9.diagnostics.aic8_available=false` and `aic8_unavailable_reason="data_not_installed"`; the two AIC-8 signal blocks are absent and the rest exits 0 |
| `aesthetic_authority_audit.py` | envelope `results.available=false` with the same reason; CLI exits 0 and emits no component/compound blocks |

The two script trees must remain byte-identical for every changed mirrored file. A repository
search for imports of the four foundation modules above is the completion backstop.

## 3. Acquisition and documentation

`plugins/setec-voiceprint/scripts/fetch_brysbaert.py` remains an opt-in convenience. Its default
output is the conventional ignored local path
`plugins/setec-voiceprint/data/brysbaert_concreteness.csv`. It downloads directly from the
publisher and converts locally. Documentation must state that SETEC does not grant permission
to download, use, or redistribute the source data and that the user is responsible for the
source terms.

Add the generated CSV path to `.gitignore` (or the closest scoped ignore file) so a locally
fetched copy cannot be accidentally committed. The fetcher may print the citation/source URL
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
