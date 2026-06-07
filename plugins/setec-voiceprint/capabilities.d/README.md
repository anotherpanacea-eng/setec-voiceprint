# Capabilities manifest (drop-in fragments)

The single source of truth for every user-facing script in the framework, as a
**drop-in directory**: one `<id>.yaml` fragment per capability, plus `_meta.yaml`
for top-level keys (`schema_version`). A new capability adds its own fragment
file — never edits a shared manifest — so parallel PRs can't collide here.

Each fragment is a one-entry document:

```yaml
entries:
  - id: my_capability
    script_path: plugins/setec-voiceprint/scripts/my_capability.py
    surface: craft_restoration
    status: heuristic
    ...
```

`capabilities.py:load_manifest()` aggregates `_meta.yaml` + every `<id>.yaml`
into the same `{schema_version, entries}` shape the old single file produced;
the repo tools (`check_capabilities_drift`, `check_docs_freshness`,
`gen_calibration_readiness`) import that canonical loader. Aggregated entry
order is alphabetical by fragment filename (the only collision-free order for a
drop-in directory; all consumers are order-independent or sort).

## Status vocabulary

- `todo` — auto-seeded; hand-curated fields missing
- `heuristic` — shipped, not yet calibrated
- `empirically_oriented` — local experimentation
- `literature_anchored` — peer-reviewed anchor
- `calibrated` — corpus-tested with FPR/TPR metrics
- `structural_only` — feeds downstream signals, not user-facing

Do not let fragments drift from the linter at
`tools/check_capabilities_drift.py` (the `surface` field must match the
script's `TASK_SURFACE` constant; `handoff: stable` requires `references`).
