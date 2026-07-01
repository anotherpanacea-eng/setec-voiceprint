### Added

**`setec_run_set.py` — multi-surface run-set runner (the decision loop).** New
operator-side sibling of `setec_run.py` (deliberately NOT a consumer surface:
no `json_delivery`, no `min_setec_version`, zero drift-gate impact on
apodictic / setec-voicewright). `setec_run_set --set smoothing_core|full_picture
--target draft.md` executes the preset's members directly by manifest
`script_path` with a fixed argv-projection table, checkpoints each schema-1.0
envelope to `envelopes/<surface_id>.json` (`--resume` reuses completed members),
joins operator-supplied envelopes via `--attach <id>=<path>` (general_imposters
and idiolect_detector are attach-only), unwraps `envelope["results"]` and feeds
the collection to the existing `surface_disagreement_resolver` (unchanged), and
emits a combined `tool: setec_run_set` envelope: member records, pass-through
member envelopes (byte-identical, sha256-pinned), the resolver's disagreement
report verbatim, and an all-mechanical `next_action` block (populate-unknown
commands, unavailable-member unlocks, a restoration_packet /
before_after_restoration handoff, and the exact rerun line). **No composite
score, no verdict, ever** — enforced at emit time, every run, by a recursive
banned-key walk plus a numeric-leaf no-reduction check over every
runner-authored subtree (mirroring `within_doc_segmentation`'s runtime
firewall), with a JSON-identity pass-through check covering the exempted member
envelopes.

Also completes the seeded `capabilities.d/` fragment for
`surface_disagreement_resolver` (status `heuristic`, family
`cross-surface-interpretation`) so `capabilities.py recommend` can finally route
to the previously-orphaned cross-surface meta-layer.
