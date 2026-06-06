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
    "external_mirror_discrimination": "external-mirror discrimination",
    "formulaicity": "formulaicity / phraseological-texture profile (non-voice)",
    "binoculars_discrimination": "Binoculars-style perplexity-ratio discrimination",
    "reference_ecology": "reference-ecology profile (non-voice; topic-bound)",
    "discrimination_curvature": (
        "Fast-DetectGPT conditional-curvature discrimination (Bao et al. 2024)"
    ),
    "narrative_decision_audit": (
        "narrative-decision audit (Russell et al. 2026 / StoryScope "
        "30-core-feature anchor)"
    ),
    "document_layout": "document structure / layout profile (non-voice)",
    "authorship_embedding": (
        "style-embedding voice fingerprint (LUAR / Wegmann learned "
        "style manifold; cosine-similarity distribution, no verdict)"
    ),
    "narratorial_distance": (
        "narratorial-distance / free-indirect-discourse profile "
        "(voice-coherence family; descriptive, non-verdict)"
    ),
    "productive_roughness": (
        "productive-roughness deviation vs the writer's own baseline "
        "(descriptive, strictly baseline-relative, non-verdict)"
    ),
    "intrinsic_dimension": (
        "intrinsic (PHD) dimension of the text's contextual-embedding "
        "cloud under a named model (discrimination evidence, "
        "uncalibrated, non-verdict)"
    ),
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


# --------------- B.3: state-routed caveats (v1.47.0+) -----------
#
# Per SPEC_authorship_states.md §9, audit scripts that emit a
# ClaimLicense should vary their caveats by the ai_status of the
# target and (where applicable) the comparison set. The shared
# templates below are taxonomy-aware: an audit script supplies the
# values, the helper applies the right caveats.
#
# The audit script's per-script `licenses` / `does_not_license` text
# is unchanged — those describe what the audit measures, which is
# orthogonal to the authorship state. The state-routed caveats
# extend `additional_caveats` so the operator reading the block
# learns what authorship-state-specific restrictions apply to the
# inference. This keeps the helper additive: scripts that don't
# call `.with_state_caveats(...)` get the v1.45.0 behavior.


TARGET_STATE_CAVEAT_TEMPLATES: dict[str, str] = {
    "pre_ai_human": (
        "Target is labeled `pre_ai_human` (pre-Nov-2022 human prose, "
        "or attested no-AI authorship). The result applies to the "
        "pre-AI baseline; comparison against post-AI work would "
        "require a different reference set."
    ),
    "ai_generated": (
        "Target is labeled `ai_generated` (LLM output, seed degree "
        "unspecified). This is the catch-all bucket — the result "
        "does not distinguish thin-prompt generation from "
        "outline-based generation, which may have different "
        "stylometric fingerprints."
    ),
    "ai_generated_from_outline": (
        "Target was generated from a substantive human seed "
        "(outline, brief, transcript, point-by-point structure). The "
        "result does NOT license inference about fully-AI-generated "
        "prose; the stylometric fingerprint of outline-seeded "
        "generation may differ from thin-prompt AI generation."
    ),
    "ai_assisted": (
        "Target is human-authored prose with collaborative LLM "
        "assistance (per-suggestion human adjudication). The result "
        "does NOT license inference about fully-AI-generated or "
        "outline-seeded prose."
    ),
    "ai_edited": (
        "Target is human-authored prose passed through an LLM for "
        "low-touch editing (suggestions accepted in bulk). The "
        "result does NOT license inference about fully-AI-generated "
        "or outline-seeded prose, and the human-vs-AI editorial "
        "agency boundary is fuzzy by definition."
    ),
    "mixed": (
        "Target carries multiple authorship states across sections "
        "(see `notes.composite_states`). Inferences route per "
        "sub-state, not the aggregate; readers should walk the "
        "composite_states list before drawing conclusions."
    ),
    "unknown": (
        "Target's authorship state is genuinely unknown. The result "
        "does not license any state-specific inference; treat as a "
        "lower-confidence reading and consider the differential "
        "diagnosis of cause (writer / register / language) before "
        "attributing patterns to AI involvement."
    ),
}


COMPARISON_STATE_CAVEAT_TEMPLATES: dict[frozenset[str], str] = {
    frozenset({"pre_ai_human"}): (
        "Comparison baseline is exclusively `pre_ai_human`. The "
        "result is interpretable as 'this target is more / less "
        "like the writer's pre-AI prose,' which is different from "
        "comparing against post-AI typical work."
    ),
    frozenset({"ai_generated"}): (
        "Comparison baseline is exclusively `ai_generated`. The "
        "result is interpretable as 'this target is more / less "
        "like the LLM baseline,' which licenses self-vs-AI "
        "distinctness inference but not AI-vs-human."
    ),
}


def _comparison_caveat(
    comparison_states: list[str] | set[str] | frozenset[str] | None,
) -> str | None:
    """Pick a comparison-baseline caveat based on the unique set of
    ai_status values in the comparison set.

    Exact matches against ``COMPARISON_STATE_CAVEAT_TEMPLATES`` win;
    otherwise the helper emits a generic "mixed baseline" caveat
    that names the states present. ``None`` returns ``None`` (no
    caveat) so callers can pass through whatever they have.
    """
    if not comparison_states:
        return None
    state_set = frozenset(comparison_states)
    if state_set in COMPARISON_STATE_CAVEAT_TEMPLATES:
        return COMPARISON_STATE_CAVEAT_TEMPLATES[state_set]
    if len(state_set) > 1:
        sorted_states = ", ".join(f"`{s}`" for s in sorted(state_set))
        return (
            f"Comparison baseline mixes authorship states "
            f"({sorted_states}). The result reflects a heterogeneous "
            f"reference; per-state inference requires slicing the "
            f"baseline by `ai_status` and re-running the comparison."
        )
    # Single unrecognized state. Fall back to generic.
    only = next(iter(state_set))
    return (
        f"Comparison baseline is `{only}`. Inferences are "
        f"relative to that state and do not generalize across the "
        f"authorship-state taxonomy without additional baselines."
    )


def state_routed_caveats(
    *,
    target_ai_status: str | None = None,
    comparison_ai_statuses: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    """Build the list of state-specific caveats for a given target
    + comparison context. Pure function — no side effects, easy to
    test in isolation.

    Returns an empty list when no state inputs are supplied; this
    keeps the helper a safe-by-default no-op when callers haven't
    plumbed authorship-state metadata through to the audit yet.
    """
    caveats: list[str] = []
    if target_ai_status:
        tmpl = TARGET_STATE_CAVEAT_TEMPLATES.get(target_ai_status)
        if tmpl is not None:
            caveats.append(tmpl)
    comparison_caveat = _comparison_caveat(comparison_ai_statuses)
    if comparison_caveat is not None:
        caveats.append(comparison_caveat)
    return caveats


def with_state_caveats(
    license_block: ClaimLicense,
    *,
    target_ai_status: str | None = None,
    comparison_ai_statuses: list[str] | set[str] | frozenset[str] | None = None,
) -> ClaimLicense:
    """Return a new ``ClaimLicense`` with state-routed caveats
    appended to ``additional_caveats``.

    The base block's other fields (licenses, does_not_license,
    comparison_set, length_range_words, etc.) pass through
    unchanged. Idempotent: calling with no state inputs returns a
    structurally-equal copy.

    Audit scripts integrate this like::

        lic = ClaimLicense(
            task_surface=TASK_SURFACE,
            licenses="...",
            does_not_license="...",
            additional_caveats=[...script-specific caveats...],
        )
        lic = with_state_caveats(
            lic,
            target_ai_status=audit.get("ai_status"),
        )
        return lic.render_block()

    The script-specific caveats stay in place; the state-routed
    caveats are appended after. Reading order in the rendered
    block: script context first, then state context.
    """
    extras = state_routed_caveats(
        target_ai_status=target_ai_status,
        comparison_ai_statuses=comparison_ai_statuses,
    )
    if not extras:
        return ClaimLicense(
            task_surface=license_block.task_surface,
            licenses=license_block.licenses,
            does_not_license=license_block.does_not_license,
            comparison_set=dict(license_block.comparison_set),
            length_range_words=license_block.length_range_words,
            register_match=list(license_block.register_match),
            language_match=list(license_block.language_match),
            fpr_target=license_block.fpr_target,
            confidence_interval_95=license_block.confidence_interval_95,
            additional_caveats=list(license_block.additional_caveats),
            references=list(license_block.references),
        )
    return ClaimLicense(
        task_surface=license_block.task_surface,
        licenses=license_block.licenses,
        does_not_license=license_block.does_not_license,
        comparison_set=dict(license_block.comparison_set),
        length_range_words=license_block.length_range_words,
        register_match=list(license_block.register_match),
        language_match=list(license_block.language_match),
        fpr_target=license_block.fpr_target,
        confidence_interval_95=license_block.confidence_interval_95,
        additional_caveats=(
            list(license_block.additional_caveats) + extras
        ),
        references=list(license_block.references),
    )
