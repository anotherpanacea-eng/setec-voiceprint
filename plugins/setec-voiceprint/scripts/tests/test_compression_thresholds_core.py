"""variance_audit re-exports the moved threshold metadata as the same objects.

A copy instead of a re-export would split the registry: a calibration caller
editing COMPRESSION_HEURISTICS in place would stop reaching classification.
"""

from __future__ import annotations

import variance_audit as va  # type: ignore
from setec.core import compression_thresholds as ct  # type: ignore

MOVED = (
    "COMPRESSION_HEURISTICS", "POS_BIGRAM_KL_HEURISTIC", "THRESHOLD_STATUS_VALUES",
    "ThresholdSpec", "calibrated_signals", "empirically_oriented_signals",
    "heuristic_signals", "literature_anchored_signals", "normalize_pos_bigram_counts",
    "pos_bigram_kl_contributions", "provisional_signals", "resolve_direction",
    "resolve_direction_with_slice", "signals_by_status",
)


def test_variance_audit_reexports_the_core_objects():
    for name in MOVED:
        assert getattr(va, name) is getattr(ct, name), name
