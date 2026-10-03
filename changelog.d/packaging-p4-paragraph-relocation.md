### Changed

- Relocated the whole `paragraph_audit` implementation into `setec.surfaces`
  behind its permanent schema-1.x legacy launcher. Functions, scoring,
  claim-license text, JSON envelopes and the capability `script_path` remain
  unchanged; ordinary imports share the package module and monkeypatches.
- Bare-copy checks cover paragraph import identity, direct, runpy and module
  JSON execution, plus its existing exit-2/bad_input normalized-dispatch refusal.
  The capability remains TODO and is not promoted to a consumer surface.
- Bootstrap replacement is net zero. The alias uses the existing fully
  documented layering exemption channel; this bounded family does not declare
  full P3/P4 completion or live consumer qualification.

Bump class: PATCH (packaging relocation; no public behavior change).
