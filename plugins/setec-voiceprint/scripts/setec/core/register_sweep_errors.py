"""Register-sweep refusal types and shared string-domain limits.

Moved verbatim from ``setec.surfaces.register_sweep`` (CB-5) so
``manifest_validator`` can share them without importing that surface.
``register_sweep`` re-exports every name here as the same object, so
``except register_sweep.BadInput`` still catches these. Stdlib only.
"""

from __future__ import annotations


MAX_PERSONA_BYTES = 128


MAX_PATH_BYTES = 4_096


MAX_USE_MEMBERS = 32


INT64_MAX = 2**63 - 1


BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮"
    "⁦⁧⁨⁩"
)


class SweepRefusal(Exception):
    """Base controlled refusal.

    Refusals carry no path, digest, commit, filter value, or caught-exception
    text: the CLI maps each subclass to one fixed golden envelope.
    """

    exit_code = 4
    reason_category = "internal_error"
    reason = "register composition sweep unavailable after internal failure"


class BadInput(SweepRefusal):
    """Manifest, document, or CLI input failure."""

    exit_code = 2
    reason_category = "bad_input"
    reason = "register composition sweep refused invalid input"


class InternalError(SweepRefusal):
    """Violation in H2's already-validated in-memory construction."""
