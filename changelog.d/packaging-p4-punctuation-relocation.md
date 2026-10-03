### Changed

- Relocated the whole `punctuation_cadence_audit` surface into `setec.surfaces`
  behind its permanent schema-1.x legacy launcher. Public functions, scoring,
  claim-license text, JSON envelopes and the capability `script_path` are
  unchanged; ordinary legacy imports share the package module and monkeypatches.
- Capability seeding, drift and bare-copy reachability now follow static
  `TASK_SURFACE` imports to implementation metadata, reject broken aliases and
  avoid double-counting relocated surfaces while retaining stay-put package
  surfaces. Bare-copy checks exercise punctuation import identity, direct,
  runpy, module and normalized dispatch routes without installation.
- This is the first bounded P4 family on the frozen integration foundation;
  it does not declare full P3 or P4 completion. Bootstrap replacement is net
  zero, with the existing shrink-only layering boundary retained.

Bump class: PATCH (packaging relocation; no public behavior change).
