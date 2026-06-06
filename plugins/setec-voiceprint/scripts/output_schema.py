#!/usr/bin/env python3
"""output_schema.py — unified JSON envelope helper for SETEC audits.

Per `internal/SPEC_output_schema_unification.md`. Every audit/diagnostic
CLI script that produces JSON output should call `build_output(...)` to
construct the top-level envelope. Downstream consumers (APODICTIC,
ultrareview tooling, external integrations) pin against
`schema_version` and expect the keys defined here.

This is a rendering-layer module. It doesn't compute anything. It
takes the script's per-call inputs and the script-specific `results`
payload, and packages them into the canonical envelope.

Usage::

    from output_schema import build_output
    from claim_license import ClaimLicense

    lic = ClaimLicense(
        task_surface="craft_restoration",
        licenses="...",
        does_not_license="...",
    )

    envelope = build_output(
        task_surface="craft_restoration",
        tool="aic_pattern_audit",
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_metadata,   # None when no baseline
        results=results_payload,
        claim_license=lic,
        warnings=warnings,
    )
    print(json.dumps(envelope, indent=2, default=str))
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore

SCHEMA_VERSION = "1.0"

# Canonical task surfaces; mirrors claim_license.TASK_SURFACE_LABELS
# so callers that pass an unknown surface fail loudly.
VALID_TASK_SURFACES = frozenset({
    "smoothing_diagnosis",
    "voice_coherence",
    "voice_coherence_acquisition",
    "validation",
    "calibration",
    "craft_restoration",
    "metric_targeted_restoration",
    "external_mirror_discrimination",
    "binoculars_discrimination",
    "narrative_decision_audit",
    "document_layout",
})


def build_output(
    *,
    task_surface: str,
    tool: str,
    version: str,
    target_path: Path | str | None,
    target_words: int,
    baseline: dict[str, Any] | None,
    results: dict[str, Any],
    claim_license: ClaimLicense | None,
    available: bool = True,
    warnings: list[str] | None = None,
    ai_status: str | None = None,
    target_extra: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical schema_version 1.0 envelope.

    Required positional metadata:

    - ``task_surface`` — one of ``VALID_TASK_SURFACES``.
    - ``tool`` — script module name (no .py).
    - ``version`` — the script's SCRIPT_VERSION constant.
    - ``target_path`` — input path; pass ``None`` only for scripts that
      operate on synthesized text. Stringified for JSON.
    - ``target_words`` — word count of the input text.
    - ``baseline`` — dict with ``n_files`` and ``words`` at minimum,
      or ``None`` when no baseline was supplied.
    - ``results`` — script-specific payload. Shape per
      ``internal/SPEC_output_schema_unification.md`` §3.
    - ``claim_license`` — a ``ClaimLicense`` instance. Pass ``None``
      only with ``available=False``.

    Optional:

    - ``available`` — default ``True``. Set ``False`` when the script
      could not produce a result (text too short, dep missing, etc.).
      ``results`` may then be ``{}``; ``warnings`` MUST explain.
    - ``warnings`` — list of strings; defaults to empty.
    - ``ai_status`` — when ``--ai-status`` was passed; per B.3.
    - ``target_extra`` — extra keys to merge into the ``target`` dict.
      Examples: ``{"sentences": 312, "preprocessing": {...}}``.
    - ``extra`` — extra top-level keys for script-specific metadata
      that doesn't belong inside ``results`` (e.g., a top-level
      ``compression`` verdict on variance_audit). Use sparingly.
    """
    if task_surface not in VALID_TASK_SURFACES:
        raise ValueError(
            f"Unknown task_surface {task_surface!r}; expected one of "
            f"{sorted(VALID_TASK_SURFACES)!r}"
        )
    if claim_license is None and available:
        raise ValueError(
            "build_output: claim_license is required when available=True. "
            "Scripts that legitimately produce no result should pass "
            "available=False explicitly."
        )
    if (
        claim_license is not None
        and claim_license.task_surface != task_surface
    ):
        raise ValueError(
            f"claim_license.task_surface={claim_license.task_surface!r} "
            f"does not match envelope task_surface={task_surface!r}"
        )

    target_block: dict[str, Any] = {
        "path": str(target_path) if target_path is not None else None,
        "words": int(target_words),
    }
    if target_extra:
        target_block.update(target_extra)

    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_surface": task_surface,
        "tool": tool,
        "version": version,
        "available": bool(available),
        "target": target_block,
        "baseline": baseline,
        "results": results,
        "claim_license": (
            claim_license.to_dict() if claim_license is not None else None
        ),
        "claim_license_rendered": (
            claim_license.render_block().rstrip()
            if claim_license is not None else None
        ),
        "warnings": list(warnings) if warnings else [],
        "ai_status": ai_status,
    }
    if extra:
        for k, v in extra.items():
            if k in envelope:
                raise ValueError(
                    f"build_output: extra key {k!r} collides with a "
                    f"required envelope key"
                )
            envelope[k] = v
    return envelope


def build_baseline_metadata(
    *,
    n_files: int,
    words: int,
    files_loaded: list[Path] | list[str] | None = None,
    files_skipped: list[Path] | list[str] | None = None,
    register: str | None = None,
    split: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape-checked builder for the ``baseline`` envelope dict.

    Pass the result as ``baseline=`` to ``build_output``. Returns
    ``None`` is the caller's job (no baseline supplied → pass ``None``
    directly).
    """
    block: dict[str, Any] = {
        "n_files": int(n_files),
        "words": int(words),
    }
    if files_loaded is not None:
        block["files_loaded"] = [str(p) for p in files_loaded]
    if files_skipped is not None:
        block["files_skipped"] = [str(p) for p in files_skipped]
    if register is not None:
        block["register"] = register
    if split is not None:
        block["split"] = split
    if extra:
        for k, v in extra.items():
            if k in block:
                raise ValueError(
                    f"build_baseline_metadata: extra key {k!r} collides "
                    f"with a required baseline key"
                )
            block[k] = v
    return block
