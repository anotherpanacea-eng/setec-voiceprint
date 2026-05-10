#!/usr/bin/env python3
"""claim_license.py — what-this-result-licenses helper for harness output.

Phase-1 step 5 of the validation spine: every harness output should
carry an explicit licensing block — what the result entitles the
reader to claim, what it does not, what comparison set produced it,
what length / register / language constraints applied, what FPR
target was chosen, and what the confidence interval is. The block
makes the framework's epistemic stance ("the math doesn't entitle
the verdict") legible at the place it has to be read: the output
the user actually sees.

Pre-1.29.0 each task surface had its own freeform "what this
licenses / does not license" boilerplate scattered across script
modules (validation_harness's CLAIM_LICENSE block, voice_drift's,
voice_validation's, GI's). They diverged subtly — the smoothing
surface said one thing about ESL caveats; the voice-coherence
surface said another; the GI harness's gray-zone language wasn't
echoed elsewhere. This module factors the structure into one shape
every harness can consume:

  ClaimLicense(
      task_surface="...",          # smoothing_diagnosis, voice_coherence, etc.
      licenses="...",              # what the output entitles
      does_not_license="...",      # what it does NOT entitle
      comparison_set={"...": ...}, # corpus / impostor / baseline used
      length_range=(min, max),     # word-count band the call is good for
      register_match={...},        # which registers the comparison anchored
      language_match={...},        # native / non-native filter applied
      fpr_target=0.01,             # the chosen FPR ceiling
      confidence_interval=(...)    # the bootstrap CI on the headline metric
  ).render_block()                 # → markdown block ready to paste

Existing harnesses can adopt this incrementally — pass their
existing CLAIM_LICENSE dict through `from_legacy()` to upgrade the
surface without changing semantics.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

TASK_SURFACE_LABELS = {
    "smoothing_diagnosis": "AI-prose smoothing diagnosis",
    "voice_coherence": "voice-coherence comparison",
    "voice_coherence_acquisition": "voice-coherence corpus acquisition",
    "validation": "validation / labeled-corpus harness",
    "calibration": "per-signal threshold calibration",
    "craft_restoration": "craft restoration",
    "metric_targeted_restoration": "metric-targeted restoration packets",
}


@dataclass
class ClaimLicense:
    """Structured licensing block for harness output.

    Every field is optional except ``task_surface``, ``licenses``,
    and ``does_not_license`` — those are the load-bearing claims.
    The remaining fields document the comparison context. A field
    set to None / empty is rendered as "not applicable" so a sparse
    block still produces meaningful output.
    """

    task_surface: str
    licenses: str
    does_not_license: str

    # Comparison context.
    comparison_set: dict[str, Any] = field(default_factory=dict)
    length_range_words: tuple[int, int] | None = None
    register_match: list[str] = field(default_factory=list)
    language_match: list[str] = field(default_factory=list)

    # Operating-point metadata.
    fpr_target: float | None = None
    confidence_interval_95: tuple[float, float] | None = None

    # Bonus / surface-specific.
    additional_caveats: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_surface": self.task_surface,
            "licenses": self.licenses,
            "does_not_license": self.does_not_license,
            "comparison_set": dict(self.comparison_set),
            "length_range_words": list(self.length_range_words)
                                  if self.length_range_words else None,
            "register_match": list(self.register_match),
            "language_match": list(self.language_match),
            "fpr_target": self.fpr_target,
            "confidence_interval_95": (
                list(self.confidence_interval_95)
                if self.confidence_interval_95 else None
            ),
            "additional_caveats": list(self.additional_caveats),
            "references": list(self.references),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def render_block(self) -> str:
        """Markdown block. Pastable into any harness's report.

        Layout:

            ## What this result licenses

            **Task surface:** <label>

            **Reports:** <licenses>

            **Does NOT report:** <does_not_license>

            ### Comparison context
            ...

            ### Operating point
            - **FPR target:** ...
            - **95% CI:** ...

            ### Caveats
            - ...

            ### References
            - ...
        """
        label = TASK_SURFACE_LABELS.get(self.task_surface, self.task_surface)
        lines: list[str] = [
            "## What this result licenses",
            "",
            f"**Task surface:** {label}",
            "",
            f"**Reports:** {self.licenses}",
            "",
            f"**Does NOT report:** {self.does_not_license}",
            "",
        ]

        ctx_lines = self._comparison_context_lines()
        if ctx_lines:
            lines.append("### Comparison context")
            lines.append("")
            lines.extend(ctx_lines)
            lines.append("")

        op_lines = self._operating_point_lines()
        if op_lines:
            lines.append("### Operating point")
            lines.append("")
            lines.extend(op_lines)
            lines.append("")

        if self.additional_caveats:
            lines.append("### Caveats")
            lines.append("")
            for c in self.additional_caveats:
                lines.append(f"- {c}")
            lines.append("")

        if self.references:
            lines.append("### References")
            lines.append("")
            for r in self.references:
                lines.append(f"- {r}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    # --- internal renderers ---------------------------------------

    def _comparison_context_lines(self) -> list[str]:
        out: list[str] = []
        if self.comparison_set:
            for k, v in sorted(self.comparison_set.items()):
                out.append(f"- **{k.replace('_', ' ')}:** {v}")
        if self.length_range_words:
            lo, hi = self.length_range_words
            out.append(
                f"- **Length range (words):** {lo:,}–{hi:,}"
            )
        if self.register_match:
            out.append(
                "- **Register match:** "
                + ", ".join(f"`{r}`" for r in self.register_match)
            )
        if self.language_match:
            out.append(
                "- **Language match:** "
                + ", ".join(f"`{l}`" for l in self.language_match)
            )
        return out

    def _operating_point_lines(self) -> list[str]:
        out: list[str] = []
        if self.fpr_target is not None:
            out.append(f"- **FPR target:** {self.fpr_target}")
        if self.confidence_interval_95 is not None:
            lo, hi = self.confidence_interval_95
            out.append(
                f"- **95% CI on headline metric:** "
                f"[{lo:.4f}, {hi:.4f}]"
            )
        return out


def from_legacy(
    legacy: dict[str, Any],
    *,
    task_surface: str,
) -> ClaimLicense:
    """Adapt the existing ``{"licenses": ..., "does_not_license":
    ...}`` dicts the older harnesses already emit. Other fields
    default to empty; callers can post-fill the comparison context
    from the harness's own state.

    Pre-1.29.0 every harness defined its own CLAIM_LICENSE dict.
    This helper lets a harness keep its existing dict and gain the
    structured rendering at the same time, instead of forcing a
    hard cutover.
    """
    return ClaimLicense(
        task_surface=task_surface,
        licenses=legacy.get("licenses", ""),
        does_not_license=legacy.get("does_not_license", ""),
        additional_caveats=(
            [legacy["gray_zone"]] if "gray_zone" in legacy else []
        ),
    )
