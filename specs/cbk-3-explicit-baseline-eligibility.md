# CBK-3: explicit baseline eligibility at the producer boundary

> No identity baseline is built from a row that does not explicitly declare `corpus_role: identity_baseline`. Ordinary profiling keeps accepting role-less rows.

- **Status:** Draft build contract. Fleet card CBK-3; it answers part 1 of fleet-coordination#11.
- **Owner ruling (2026-11-05, recorded on the card):**
  - require explicit eligibility whenever a baseline is built, while keeping ordinary profiling;
  - missing or uncertain ownership or roles go back to the existing resolution method, never defaulted or guessed;
  - the boundary must protect programmatic callers, not only CLI selectors.
- **Not authorized:** no private-data run. Row-count reconciliation (part 2 of #11) is a separate task.

## The defect today (`origin/main`)

| Place | What it does |
|---|---|
| `setec/surfaces/general_imposters.py` `_load_manifest` (line 200) | `corpus_role=str(row.get("corpus_role") or "identity_baseline")`: an absent role **becomes** `identity_baseline` |
| `_select_candidate_docs` (line 681) and `_infer_candidate_register` (line 1033) | They select on that defaulted value, so a role-less row in the candidate's persona and register joins the candidate baseline. With no explicit baseline rows, it can **be** the whole pool. |
| `run_gi` (line 501) | It takes `candidate_docs` from any caller and never checks their role. A programmatic caller can pass role-less or defaulted rows. |
| `setec/surfaces/author_corpus_export.py` (line 489) | `entry.get("corpus_role", "identity_baseline")` accepts a role-less document-manifest row as a baseline |
| `setec/core/manifest_core.py` (lines 778, 1091) | Defaults an absent role to `identity_baseline` **for validation warnings and statistics only**. That is profiling-side, not baseline construction. |

The other baseline builders already require the explicit role: `normalize_author_registry` (line 213), `author_corpus_export` records (lines 334, 444), `prepare_author_document_adapter` (line 159) and `author_record` (line 180).

## The contract

1. **Load without defaulting.** `CorpusEntry.corpus_role` becomes `str | None`, and `_load_manifest` stores `None` when the field is absent. Nothing else in loading changes: paths, skip-and-warn, consent and register are untouched.
2. **Select only explicit baselines.** `_select_candidate_docs` and `_infer_candidate_register` keep their predicate `e.corpus_role == "identity_baseline"`, which now matches only an explicitly declared role. `_select_impostor_docs` is unchanged; it already requires `"impostor"`.
3. **Guard the producer boundary in `run_gi`.**
   - Before any feature work, `run_gi` refuses (raises `ValueError`) if any `candidate_docs` entry lacks an explicit `corpus_role == "identity_baseline"`, or any `impostor_docs` entry lacks an explicit `"impostor"`.
   - The message gives counts only: "N candidate rows lack an explicit identity_baseline corpus_role; declare it in the manifest". It never prints a row id, path or text.
   - The CLI's existing `except ValueError` path turns this into its existing refusal. This is the guard a programmatic caller can't bypass.
4. **Make the document-manifest path explicit too.** In `author_corpus_export`, an absent `corpus_role` on a `document_local` manifest row now refuses, like any other non-baseline role. Its error message names the resolution path below.
5. **Leave profiling alone.**
   - `manifest_core` keeps its warning and statistics default, and its docstring says it is profiling-only.
   - `voice_profile`, `voice_distance` and the other profiling surfaces are not touched.
   - The register-tier isolation guard (#369) in `run_gi` is not touched, and its tests pass verbatim.

## Resolving a missing or uncertain role

A refused row is resolved by **declaring its role in the manifest under owner review**, then rerunning. The owner-reviewed correction workflow (`apply_owner_corrections.py`, schema `setec-owner-correction/1` in `references/manifest-schema.md`) is the existing route for reviewed manifest corrections. Its closed schema rewrites **only `register` and `era`** today, so it cannot yet set a `corpus_role`.

- **Today:** the operator edits the manifest by hand after owner review, and `manifest_validator` checks the result.
- **Owner call 1:** whether to extend `setec-owner-correction/1` so a reviewed correction may set `corpus_role`. That is a schema change with its own review. This contract doesn't make it.

Nothing defaults, guesses or infers a role.

## Synthetic regression (names and invariants the build must satisfy)

All tests use synthetic manifests and text. No private data is involved.

| Test | Invariant |
|---|---|
| `test_gi_role_less_rows_cannot_form_baseline` | A manifest whose only rows in the candidate's persona and register omit `corpus_role` produces the existing "no candidate identity-baseline docs" refusal, never a score |
| `test_gi_role_less_rows_excluded_beside_explicit` | Role-less rows beside explicit baseline rows: `candidate_n_docs` counts only the explicit rows |
| `test_gi_explicit_baseline_still_scores` | An explicit `identity_baseline` plus impostors still produces a GI result |
| `test_run_gi_refuses_programmatic_role_less_candidates` | `run_gi` called directly with a `CorpusEntry(corpus_role=None)` candidate raises `ValueError` with counts only, before any feature computation |
| `test_run_gi_refuses_non_impostor_in_impostor_pool` | The same for an impostor-pool entry without an explicit `"impostor"` |
| `test_author_corpus_export_refuses_role_less_document_row` | A `document_local` manifest row with no `corpus_role` refuses |
| `test_manifest_validation_still_defaults_for_profiling` | `manifest_core` statistics and warnings are unchanged for role-less rows |
| existing #369 register-isolation tests | pass verbatim |

**Fixture impact.** The one role-less row in `test_data/impostor_corpus/manifest.jsonl` (`validation_no_era_ok`) has no `persona`, so it never matched a candidate persona. General-imposters outputs on that fixture are unchanged. The build confirms the general-imposters golden capability and the zero-install case are byte-identical.

## Downstream

`general_imposters` and `author_corpus_export` are consumed surfaces. The refusal envelope shape is unchanged: the existing no-candidate refusal is reused, and the new `ValueError` follows the existing CLI refusal path. Voicewright's pinned contract fixtures should not drift. The build runs the surface-addition checklist's drift checks to confirm.

## Owner calls

1. Extend `setec-owner-correction/1` to cover `corpus_role`, or keep hand-editing under review.
2. Whether any existing attested `document_local` manifest omits `corpus_role`. If one does, item 4 makes it refuse until it is corrected. The owner checks this locally, by count only.
