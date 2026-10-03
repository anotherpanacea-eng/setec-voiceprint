### Changed

- Relocate the whole `argument_decision_audit` implementation into
  `setec.surfaces`, retaining its permanent schema-1.x launcher, shared module
  identity, normalized JSON delivery, and unchanged scoring/license/refusals.
- Retarget the existing audit-to-argmove_profile layer edge and document the
  visible alias edge and required detached-runpy bootstrap. The measured
  bootstrap ceiling rises from 191 to 192 without exclusions or unrelated removals.
- Add synthetic mock/manifest and copied-plugin compatibility checks. B5 remains
  excluded from aggregation, empty aggregates remain unavailable, and recorded
  judge identity stays intact. This bounded relocation does not qualify full
  P3/P4 acceptance or consumer fixture/lock parity. Release class: PATCH.
