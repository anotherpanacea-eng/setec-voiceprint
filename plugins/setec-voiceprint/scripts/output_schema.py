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

import math
import re
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense, TASK_SURFACE_LABELS  # type: ignore

SCHEMA_VERSION = "1.0"

# R3 — structured error model (spec §4). One envelope shape for success
# AND failure: a failed/blocked run emits the SAME schema_version 1.0
# envelope with ``available: false`` plus two ADDITIVE keys, ``reason``
# (human text) and ``reason_category`` (this enum). The keys are present
# only on error envelopes, so the 12-key success contract (and the R5
# goldens) are untouched; schema_version stays "1.0" (additive only).
REASON_CATEGORIES = frozenset({
    "version_floor",       # surface's min_setec_version > running setec_version
    "missing_dependency",  # a required dependencies.python import is absent
    "bad_input",           # unknown surface / malformed args / usage error
    "text_too_short",      # input below a surface's length floor
    "policy_refused",      # a privacy / policy guard refused the run
    "internal_error",      # unexpected failure, incl. an out-of-bounds value
})

# Canonical task surfaces — derived from
# claim_license.TASK_SURFACE_LABELS (the single source of truth, itself
# assembled from drop-in fragments) so an unknown surface fails loudly and
# a new surface is registered in exactly one place (a fragment file).
VALID_TASK_SURFACES = frozenset(TASK_SURFACE_LABELS)


# R4 — output-validity bounds gate (spec §5). Cheap, surface-agnostic
# plausibility checks applied to the ``results`` payload at the
# build_output() boundary so an out-of-bounds COMPUTED value (the
# DirectML-surprisal class of bug: a confident, out-of-range number)
# becomes an R3 ``internal_error`` instead of shipping in the envelope.
#
# Only UNAMBIGUOUS, surface-agnostic bounds are enforced (per the spec's
# explicit instruction not to invent bounds):
#   * any numeric that is NaN or +/-inf is invalid (a finite-value
#     guarantee no real metric violates);
#   * a value at a key whose name marks it as a cosine SIMILARITY must be
#     in [-1, 1];
#   * a value at a key whose name marks it as a surprisal / perplexity /
#     entropy must be >= 0 (no upper bound is asserted — log2|vocab| is
#     only known when vocab size is actually present, which it is not at
#     this boundary, so no upper bound is invented);
#   * a value at a key whose name marks it as a probability must be in
#     [0, 1].
# Anything whose correct bound is not unambiguous is LEFT UNCHECKED.
class OutputValidityError(ValueError):
    """Raised by ``build_output`` when a computed ``results`` value violates
    an unambiguous, surface-agnostic plausibility bound (R4). The dispatcher
    catches this and emits an ``internal_error`` envelope rather than
    shipping the bad number."""


# Field-name -> bound classifier. Keys are matched case-insensitively as
# whole snake_case tokens (so ``cosine_distance`` is NOT treated as a
# similarity, and ``perplexity_ratio`` is matched on ``perplexity``).
_COSINE_SIM_RE = re.compile(
    r"(?:^|_)(?:cosine_similarity|cos_sim|adjacent_cosine|cosine_sim)(?:$|_)"
)
_SURPRISAL_RE = re.compile(
    r"(?:^|_)(?:surprisal|perplexity|entropy|cross_entropy|nll)(?:$|_)"
)
# Probability bound is restricted to UNAMBIGUOUS names only. A bare ``prob``
# token is intentionally NOT matched: SETEC has many ``*_log_prob*`` /
# ``log_prob_sum`` fields (a log-probability is <= 0, never in [0, 1]), so
# matching ``prob`` would wrongly reject them — the exact over-constraint the
# spec warns against. We match the full word ``probability`` and the
# statistical p-value names, and we explicitly do NOT fire when the value sits
# on a log-/ratio-transformed probability field.
_PROBABILITY_RE = re.compile(
    r"(?:^|_)(?:probability|p_value|pvalue)(?:$|_)"
)
# Guard: a key carrying one of these tokens names a TRANSFORM/DERIVATION of a
# base quantity (a log, ratio, sum, difference, …) whose range is NOT the
# base's range, so we leave it unchecked rather than invent a bound. This is
# deliberately conservative — it lists only unambiguous transform words, NOT
# units (``bits``/``nats`` are units, not transforms: ``surprisal_bits`` is a
# raw surprisal and stays checked) and NOT ``score``/``z`` (too broad).
_TRANSFORM_RE = re.compile(
    r"(?:^|_)(?:log|ln|logit|ratio|sum|delta|diff)(?:$|_)"
)


def _check_numeric_bounds(key: str, value: float) -> None:
    """Raise ``OutputValidityError`` if a numeric ``value`` at ``key``
    violates an unambiguous bound. NaN/inf is always invalid; named
    cosine-similarity / surprisal-family / probability fields get their
    range checked. Unrecognized keys are left unchecked."""
    if math.isnan(value) or math.isinf(value):
        raise OutputValidityError(
            f"results[...][{key!r}] = {value!r} is not finite "
            f"(NaN/inf is never a valid computed metric)"
        )
    lname = key.lower()
    # A derived/transformed metric (a log, ratio, delta, difference, sum,
    # z-score, etc. OF a base quantity) does NOT inherit the base's range, so
    # we leave transformed fields unchecked rather than invent a bound. This
    # is what keeps the gate off ``actual_log_prob_sum_nats`` (a negative log
    # probability) and similar legitimately-out-of-[0,1]/negative fields.
    transformed = _TRANSFORM_RE.search(lname) is not None
    if _COSINE_SIM_RE.search(lname):
        # A cosine similarity is bounded [-1, 1] even under naming variants;
        # a *delta*/*ratio* of cosines is not, so respect the transform guard.
        if not transformed and not (-1.0 <= value <= 1.0):
            raise OutputValidityError(
                f"results[...][{key!r}] = {value!r} is a cosine similarity "
                f"outside [-1, 1]"
            )
    elif _SURPRISAL_RE.search(lname):
        # >= 0 only, and only for the RAW quantity. No upper bound:
        # log2|vocab| is unknown at this boundary, and the spec forbids
        # inventing one. A ratio/delta/z of a surprisal/entropy can be
        # negative, so the transform guard suppresses the check there.
        if not transformed and value < 0.0:
            raise OutputValidityError(
                f"results[...][{key!r}] = {value!r} is a surprisal/entropy "
                f"value below 0"
            )
    elif _PROBABILITY_RE.search(lname):
        if not transformed and not (0.0 <= value <= 1.0):
            raise OutputValidityError(
                f"results[...][{key!r}] = {value!r} is a probability "
                f"outside [0, 1]"
            )


def validate_results_bounds(results: Any, _key: str = "") -> None:
    """Recursively walk a ``results`` payload and assert R4 bounds on every
    numeric leaf. ``bool`` is skipped (a subclass of ``int`` but never a
    metric). Strings/None and any non-numeric leaf are ignored. Mappings
    recurse by key (so the field name reaches ``_check_numeric_bounds``);
    sequences recurse carrying the parent key, so e.g. a list of cosine
    similarities under ``adjacent_cosine`` is still range-checked."""
    if isinstance(results, bool):
        return
    if isinstance(results, (int, float)):
        _check_numeric_bounds(_key, float(results))
        return
    if isinstance(results, dict):
        for k, v in results.items():
            validate_results_bounds(v, str(k))
        return
    if isinstance(results, (list, tuple)):
        for item in results:
            # Carry the parent key so list elements inherit the field's
            # semantic (a list under ``adjacent_cosine`` is similarities).
            validate_results_bounds(item, _key)
        return
    # str / None / other: nothing to bound.


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
    validate_bounds: bool = True,
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
    - ``validate_bounds`` — default ``True``. Runs the R4 output-validity
      gate over ``results`` and raises ``OutputValidityError`` on an
      out-of-bounds computed value (NaN/inf, a cosine similarity outside
      [-1, 1], a negative surprisal/entropy, a probability outside
      [0, 1]). Set ``False`` only to bypass the gate intentionally
      (e.g., a script that has already validated and wants to skip the
      re-walk); the dispatcher leaves it on.

    Raises:

    - ``ValueError`` for the metadata-contract violations above.
    - ``OutputValidityError`` (a ``ValueError`` subclass) when the R4
      gate rejects a ``results`` value.
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

    # R4 output-validity gate: bound-check the computed payload before it
    # can enter the envelope. Only run on available results (an
    # available=False envelope legitimately carries {} or partial data),
    # and only on the script-specific ``results`` (the metadata blocks are
    # builder-controlled). Raises OutputValidityError on violation; the
    # dispatcher turns that into an internal_error envelope.
    if validate_bounds and available:
        validate_results_bounds(results)

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


def build_error_output(
    *,
    task_surface: str | None,
    tool: str,
    version: str,
    reason: str,
    reason_category: str,
    target_path: Path | str | None = None,
    target_words: int = 0,
    warnings: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the R3 structured-error envelope (spec §4).

    One envelope shape for success and failure: this returns the SAME
    ``schema_version: 1.0`` envelope ``build_output`` produces, with
    ``available: false`` and the script-specific blocks emptied
    (``baseline: null``, ``results: {}``, ``claim_license: null``), PLUS
    two ADDITIVE keys the consumer branches on:

    - ``reason`` — human-readable explanation.
    - ``reason_category`` — one of :data:`REASON_CATEGORIES`.

    These keys are present only on error envelopes, so the 12-key success
    contract (and the R5 goldens) is untouched and ``schema_version`` stays
    ``"1.0"`` (additive only).

    ``task_surface`` may be ``None`` (e.g. an unknown surface, where no
    valid surface label exists yet) — unlike the success path, the error
    builder does NOT validate it against ``VALID_TASK_SURFACES``, because a
    ``bad_input`` failure is exactly the case where the surface is unknown.

    For a ``version_floor`` failure the caller puts BOTH the requested
    floor and the observed version into ``reason`` (and may carry the
    machine-readable pair via ``extra``); the builder does not invent
    defaults (the ``_install_instructions`` self-contradiction bug).
    """
    if reason_category not in REASON_CATEGORIES:
        raise ValueError(
            f"Unknown reason_category {reason_category!r}; expected one of "
            f"{sorted(REASON_CATEGORIES)!r}"
        )

    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_surface": task_surface,
        "tool": tool,
        "version": version,
        "available": False,
        "target": {
            "path": str(target_path) if target_path is not None else None,
            "words": int(target_words),
        },
        "baseline": None,
        "results": {},
        "claim_license": None,
        "claim_license_rendered": None,
        "warnings": list(warnings) if warnings else [],
        "ai_status": None,
        # Additive R3 keys (present only on error envelopes).
        "reason": reason,
        "reason_category": reason_category,
    }
    if extra:
        for k, v in extra.items():
            if k in envelope:
                raise ValueError(
                    f"build_error_output: extra key {k!r} collides with a "
                    f"reserved envelope key"
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
    # Stringify via POSIX form so envelope paths are deterministic across
    # platforms: str(Path("/abs/x")) yields backslashes on Windows, but the
    # JSON envelope is consumed cross-platform and pinned by downstream
    # consumers. On POSIX str(Path) is already forward-slash, so this is a
    # no-op there; on Windows it normalizes "\abs\x" -> "/abs/x".
    if files_loaded is not None:
        block["files_loaded"] = [Path(p).as_posix() for p in files_loaded]
    if files_skipped is not None:
        block["files_skipped"] = [Path(p).as_posix() for p in files_skipped]
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
