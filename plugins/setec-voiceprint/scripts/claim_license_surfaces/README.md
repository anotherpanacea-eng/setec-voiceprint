# Task-surface label fragments

Each `*.txt` file here registers one task surface for
`claim_license.TASK_SURFACE_LABELS`:

- **filename** (without `.txt`) = the surface key (e.g. `smoothing_diagnosis`)
- **contents** = the human-readable label, on a single line

`claim_license.py` assembles `TASK_SURFACE_LABELS` from this directory at
import, and `output_schema.VALID_TASK_SURFACES` is derived from that dict — so
a surface's **label and allow-list membership** come from this one fragment.
(A script's own `TASK_SURFACE = "..."` constant and its `capabilities.yaml`
entry remain separate declarations, cross-checked by
`tools/check_capabilities_drift.py`; this fragment is not a substitute for
either.)

## Adding a surface

Drop in a new file — do **not** edit a shared dict or list:

```
echo "my surface's human-readable label" > my_surface.txt
```

That's it. Both `TASK_SURFACE_LABELS` and `VALID_TASK_SURFACES` pick it up on
next import. This is the whole point: parallel audit PRs each add their own
file instead of editing one shared insertion point, so they never collide
(and can never produce the silent bad-merge that a hand-edited dict can).

## Rules

- One logical line, no trailing newline beyond the single one your editor adds
  (the loader does `rstrip("\n")`). No internal newlines.
- Keep the label terse; richer per-capability metadata lives in
  `../../capabilities.yaml`.
- The filename must match the script's `TASK_SURFACE` constant.
