### Fixed

- Every ingestion-preflight command now checks its inputs in the series' master
  refusal order. It first confines every input and the output location, then
  checks every size ceiling, then parses contracts. Before this fix, several
  commands checked some of these late. An artifact census or calibrate output
  inside the manifest directory now refuses `path_confinement` even when the
  policy is also malformed. It also ranks first in span, multiplicity, final
  and the P7 report. An oversized policy, labels file, admission map, split map
  or span source now refuses `size_limit` even when the manifest is malformed.
  In the holdout firewall, a manifest row naming a path outside its root
  refuses `path_confinement` ahead of an oversized policy.
- The holdout firewall confines its roots and outputs by file identity, not by
  resolved path strings, so a case-folded or symlinked spelling of a manifest
  root cannot slip past. Two output names that differ only in case now refuse
  `path_confinement`. A symlink loop in an output parent now refuses
  `output_unavailable`, after every input check, instead of `path_confinement`.
- A failing stdout or stderr after a bundle is published no longer turns the
  run into exit 2 `internal_refusal`. Artifacts, span, multiplicity, final, the
  holdout firewall and the P7 report now exit as overlap does. The bundle is
  committed, so the exit code is 0, or 3 for a P7 report with pending stages.
- The holdout detail and receipt reloaders now refuse `detail_contract` and
  `receipt_contract` when two sealed entries share a manifest hash. A genuine
  run refuses such input, so a P7 report can no longer accept one.
