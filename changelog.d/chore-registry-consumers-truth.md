### Changed

**Registry `consumers:` truth — 49 fragments corrected (roadmap FM-1 + FM-4).**
Forty-eight capability fragments carried `consumers: []` while being actively
imported, invoked, or artifact-bound by other surfaces; the field is not
documentation but the cross-repo discovery filter
(`apodictic/scripts/sync_setec.py` vendors an entry iff `"apodictic"` appears in
its `consumers`), so an empty list makes a live surface structurally invisible.
The corrected values are recorded per fragment, verified against the import /
CLI-reference / artifact-schema graph on `main` rather than transcribed.
Load-bearing examples: `acquisition_core` (22 in-repo importers, including the
contract-pinned `author_corpus_export`), `manifest_validator` (11), and the
eight surfaces imported directly by contract-pinned capabilities
(`biber_features`, `length_bootstrap`, `kicker_density`, `prestige_metaphor`,
`surprisal_audit`, `check_corpus`, `argmove_profile`,
`semantic_trajectory_audit`).

Skill names are deliberately **not** written into `consumers:` — that field
feeds a repo/capability-name filter, and a skill name there would corrupt it.
Rows whose only reader is a skill or a reference doc keep `consumers: []` and
carry a YAML comment naming the surfacing skill. `setec_run_set` keeps
`consumers: []` by design (`handoff: none` is what holds it out of
`setec_run.py --list`), now with a comment saying so rather than reading as
neglect.

**`evidentiary_conditions_gate` records the pairing the shipped fixture already
names.** The released `mimicry_cosplay_audit` claim_license tells the reader to
"pair this audit with the confounder audit and the evidentiary-conditions gate,"
and names `before_after_restoration`, `surface_disagreement_resolver`, and
`semantic_preservation_check` in its caveats — five contract statements vendored
into two consumer repos with nothing pinning them to the registry.
`test_contract_fixtures.py::test_mimicry_fixture_pairings_are_registered` now
asserts both directions: the fixture prose still names each pairing, and each
named capability records `mimicry_cosplay_audit` in its `consumers`. Retiring
one of the five fails here instead of leaving the shipped fixture pointing at a
capability that no longer exists.
