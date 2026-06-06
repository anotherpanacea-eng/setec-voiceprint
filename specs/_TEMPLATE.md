# <NN>-<capability-slug>

> One-sentence statement of the capability and the axis it measures.

- **Status:** Draft | Ready | In build (`<branch>`) | Shipped (`<script>`) | Calibrated
- **Tier:** near-term | research-grade | defer
- **GPU required:** inference-optional | yes (fine-tune/TDA) | no
- **Upstream / prior art:** <papers, repos, model cards with URLs>
- **License decision:** wrap weights (`<license>`) | clean-room the method | N/A —
  with the specific pre-build verification step if any (see research brief TODO).

## Motivation

Why this belongs in the kit. What gap it fills. **Orthogonality:** what new axis it
measures that SETEC's existing signals / surfaces do not. Cite the brief.

## Method

The math/algorithm, plainly. Inputs and outputs at the value level. Cite the source
method; if clean-room, state exactly what is reimplemented vs. what is reused.

## Contract (the testable interface)

- **task_surface:** `<value>` (existing, or new — register in
  `output_schema.VALID_TASK_SURFACES` and `claim_license.TASK_SURFACE_LABELS`).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/<script>.py <args>` — list flags,
  `--json`, `--out`, defaults.
- **JSON envelope:** built via `output_schema.build_output()`. Enumerate the keys
  under `results`. Carries a `ClaimLicense` block.
- **Claim license:** what the output **licenses** and what it **refuses** (no verdict;
  thresholds operator-side / PROVISIONAL).
- **capabilities.yaml entry:** `id`, `surface`, `status` (start `heuristic` or
  `empirically_oriented`), `handoff`, `compute.tier` + `cost_note` +
  `length_floor_words`, `dependencies.python` / `python_optional`, `use_when`,
  `do_not_use_when`, `inputs`, `references`.
- **Dependencies / footprint:** new packages + a `dependency_check.py` tier if needed;
  disk/VRAM numbers for the readiness matrix + README costs.

## Test contract (names + invariants the build must satisfy)

List concrete test cases (file: `plugins/setec-voiceprint/scripts/tests/test_<x>.py`).
Each is an invariant the implementation must pass — the builder writes code to green
these. Include at minimum: deterministic-output test, envelope-shape test,
claim-license-present test, refuses-verdict test, graceful-degradation/missing-dep
test, and any method-specific numeric pin.

## Calibration posture

What ships PROVISIONAL/uncalibrated. What labeled corpus would calibrate it later, and
what PROVENANCE entry that produces. The default must not be a verdict.

## Out of scope / non-goals

What this capability deliberately does not do.

## Open questions

Decisions the maintainer should make before/while building.
