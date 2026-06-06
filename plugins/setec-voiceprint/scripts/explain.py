#!/usr/bin/env python3
"""explain.py — plain-language explainer for a SETEC audit envelope.

Takes any SETEC ``build_output`` envelope (a file, or ``-`` for stdin) and prints
a short, jargon-free summary for a non-technical reader: what the audit is, what
it found (or why it couldn't), what you may conclude, what you may NOT conclude,
and a suggested next step.

It is a *renderer*, not an audit: it computes nothing and invents nothing. Every
line traces to a field already in the envelope — chiefly its ``claim_license``
block (the load-bearing epistemic surface) plus ``available`` / ``warnings``. The
suggested next step comes from a small rule table keyed on the task surface.

Usage:

    python3 scripts/explain.py result.variance.json
    python3 scripts/variance_audit.py draft.md --json | python3 scripts/explain.py -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from claim_license import TASK_SURFACE_LABELS  # type: ignore
except Exception:  # pragma: no cover - claim_license is always present in-tree
    TASK_SURFACE_LABELS = {}

TOOL_NAME = "explain"
SCRIPT_VERSION = "1.0"

# Suggested-next-step rules, keyed on task_surface. Falls back to a generic
# evidence-not-verdict line for any surface not listed (incl. surfaces added by
# unmerged PRs). Keys are matched exactly; unknown → default.
NEXT_STEP: dict[str, str] = {
    "smoothing_diagnosis": "Compare against the writer's own register-matched baseline "
    "before drawing conclusions — the band is provisional without one.",
    "voice_coherence": "Interpret only against a baseline of the writer's prior work; "
    "distance is not identity.",
    "voice_coherence_acquisition": "This is corpus-assembly bookkeeping; check the "
    "manifest before using the corpus downstream.",
    "validation": "These are operating-point statistics — read the stated FPR target "
    "before trusting any threshold.",
    "calibration": "Thresholds here are corpus-specific; check the provenance before "
    "reusing them elsewhere.",
    "craft_restoration": "Use as revision guidance, not a score; re-run after revising "
    "to check whether the change helped.",
    "metric_targeted_restoration": "Treat as prompt targets for revision, not metrics "
    "to optimize directly; re-run a post-check after revising.",
    "binoculars_discrimination": "Discrimination evidence — thresholds are operator-side "
    "and uncalibrated by default; treat as evidence, never a verdict.",
    "external_mirror_discrimination": "Discrimination evidence — uncalibrated by default; "
    "treat as evidence, never a verdict.",
    "narrative_decision_audit": "Uncalibrated by default; treat as literature-anchored "
    "evidence, not a verdict.",
    # Non-voice descriptive surfaces (some added by in-flight PRs):
    "document_layout": "A descriptive, non-voice profile — do not read it as a voice, "
    "authorship, AI, or quality signal.",
    "reference_ecology": "A topic-bound, non-voice profile — it shifts with subject "
    "matter, so do not read it as voice drift.",
    "formulaicity": "A phraseological-texture measurement — explicitly NOT an AI signal "
    "and NOT a quality judgment.",
}
DEFAULT_NEXT_STEP = (
    "Read the claim-license above: this output is evidence with a stated scope, "
    "not a verdict. SETEC refuses single AI/human verdicts by design."
)


def is_setec_envelope(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and "schema_version" in obj
        and "task_surface" in obj
        and "tool" in obj
    )


def render_explain(env: dict[str, Any]) -> str:
    """Render a plain-language explanation. Deterministic; invents nothing."""
    tool = env.get("tool", "?")
    surface = env.get("task_surface", "?")
    label = TASK_SURFACE_LABELS.get(surface, surface)
    available = env.get("available", True)
    cl = env.get("claim_license") if isinstance(env.get("claim_license"), dict) else {}

    lines = [
        "## What this is",
        "",
        f"Output from **{tool}** — a {label} (`{surface}`).",
        "",
        "## What it found",
        "",
    ]
    if available:
        results = env.get("results") if isinstance(env.get("results"), dict) else {}
        if results:
            lines.append("The audit ran and produced measurements in these sections "
                         "(see the JSON for the numbers):")
            for k in results:
                lines.append(f"- `{k}`")
        else:
            lines.append("The audit ran and produced an output envelope.")
    else:
        lines.append("The audit did **not** produce a result. Reasons given:")
        for w in env.get("warnings") or ["(no reason recorded)"]:
            lines.append(f"- {w}")
    lines.append("")

    licenses = cl.get("licenses")
    does_not = cl.get("does_not_license")
    lines += [
        "## What you may conclude",
        "",
        (licenses if licenses else
         "_No claim-license was attached to this output._"),
        "",
        "## What you may NOT conclude",
        "",
        (does_not if does_not else
         "_No claim-license was attached; treat the output as evidence only._"),
        "",
        "## Suggested next step",
        "",
        NEXT_STEP.get(surface, DEFAULT_NEXT_STEP),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("envelope", help="Path to a SETEC audit JSON envelope, or '-' for stdin.")
    p.add_argument("--out", help="Write to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        raw = sys.stdin.read() if args.envelope == "-" else \
            Path(args.envelope).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"Could not read input: {exc}\n")
        return 2
    try:
        env = json.loads(raw)
    except json.JSONDecodeError:
        sys.stderr.write("Input is not valid JSON.\n")
        return 2
    if not is_setec_envelope(env):
        sys.stderr.write(
            "Input is not a SETEC audit envelope (missing schema_version / "
            "task_surface / tool).\n")
        return 2

    out_text = render_explain(env)
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        sys.stderr.write(f"Wrote explanation to {args.out}\n")
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
