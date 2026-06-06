# 16-explain-mode

> A plain-language **`explain`** renderer: takes any SETEC `build_output` envelope and
> prints, for a non-technical reader, what the audit measured, what its claim-license
> does and does **not** license, and a suggested next step — no jargon, no new numbers.

- **Status:** Spec (stdlib — buildable in-sandbox; a natural next QoL round).
- **Tier:** QoL (ROADMAP/QoL list → "`--explain` plain-language mode").
- **GPU required:** no — stdlib only.
- **License:** N/A (local).

## Motivation & orthogonality

SETEC's outputs are honest but dense; the claim-license is the load-bearing epistemic
surface, yet a non-technical user (a writer, an editor) may not parse it. `explain`
turns an envelope into a short plain-English paragraph grounded *entirely* in the
envelope's own `claim_license` + `available` + `warnings` — it adds no analysis and
invents no verdict. Complements `evidence_pack` (which bundles) by translating a
single audit. Distinct from a dispatcher (the `setec run` entrypoint is already
specced in #139).

## Method (stdlib)

Read one envelope (or stdin). Render: a one-line "what this is" (tool + task-surface
label from `claim_license.TASK_SURFACE_LABELS`), "what it found" (the `available`
state + a non-technical gloss of the top results, or the warnings if unavailable),
"what you may conclude" (the `licenses` line), "what you may NOT conclude" (the
`does_not_license` line), and "suggested next step" (a small rule table keyed on
task-surface — e.g. smoothing_diagnosis → "compare against the writer's own baseline";
discrimination surfaces → "thresholds are operator-side; treat as evidence"). Every
sentence traces to an envelope field — no fabrication.

## Contract

- **No `task_surface`** — it's a renderer tool, like `evidence_pack` (no manifest entry).
- **CLI:** `python3 scripts/explain.py ENVELOPE.json [--out PATH]` (also reads stdin: `… --json | python3 scripts/explain.py -`).
- **Output:** plain-text/Markdown paragraph(s). Deterministic.
- **Guard:** if the input isn't a SETEC envelope → clear error, exit 2 (mirrors `evidence_pack`).

## Test contract (`tests/test_explain.py`)

- `test_renders_surface_label`; `test_reports_licenses_and_refusals` (both lines present, verbatim from the envelope); `test_unavailable_explained` (uses warnings, no fabricated results); `test_next_step_rule_table` (surface → suggestion mapping); `test_non_envelope_errors`; `test_no_fabricated_verdict` (output contains no claim absent from the envelope); `test_deterministic`.

## Non-goals

- Invents nothing — no new numbers, no verdict, no analysis beyond the envelope.
- Not a dispatcher (`setec run` is #139) and not a bundler (`evidence_pack`).

## Note

Stdlib + self-contained, so this is buildable in the constrained sandbox (unlike the
spaCy/torch items above) — a good candidate for the next in-session build round.
