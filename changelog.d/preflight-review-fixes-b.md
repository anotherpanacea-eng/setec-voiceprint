### Fixed

- The span proof command now binds and size-checks every source before it
  validates the policy, so a source that changed, escaped, or exceeds a ceiling
  reports `input_changed`, `path_confinement`, or `size_limit` ahead of
  `policy_contract`, as the shared refusal order requires. The boundary-visit
  ceiling is still counted after the policy check.
- `multiplicity --admission-map ""` now refuses `admission_contract` instead of
  running as if no admission map had been supplied.
- The strict span and multiplicity reloaders now refuse documents whose fields
  contradict each other. The span detail checks that each proof code agrees with
  the row's hashes, size, and offsets, that self-spans prove, that rows on the
  same source bytes agree, and that all dispositions could come from one policy.
  The span receipt checks class-count nesting. The multiplicity receipt bounds
  its violation and cluster counts.
