#!/usr/bin/env python3
"""Regression tests for `validation_harness.derive_seed`.

The reviewer who caught the same-shape `hash()` bug in
`voice_validation_harness._stable_seed` (1.9.0) flagged that the
smoothing harness's `derive_seed` should be audited for the same
problem. The audit found `derive_seed` does NOT use `hash()` — it
uses `(i+1)*ord(ch)` accumulation, which is stable across processes
because Unicode code points don't depend on `PYTHONHASHSEED`.

These tests pin the cross-process-stable behavior so a future
"modernizer" can't silently replace the implementation with
`hash((parts...))` thinking they're improving it. Pinning specific
derived seed values catches such a regression immediately.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from validation_harness import derive_seed  # type: ignore


def test_derive_seed_returns_none_when_base_is_none() -> None:
    assert derive_seed(None, "per_signal", "burstiness_B") is None
    assert derive_seed(None) is None


def test_derive_seed_pins_known_values() -> None:
    """Cross-process stability: these specific outputs must reproduce
    on every Python invocation regardless of `PYTHONHASHSEED`. If a
    future change to the algorithm switches to `hash(...)`, these
    assertions will fail and bootstrap CIs will silently start
    drifting between runs.

    Spot-checked from a fresh interpreter; values pinned here are
    what the algorithm produces today. A regression in either
    direction (different value or non-deterministic value) means
    `derive_seed` no longer satisfies the cross-process-stable
    contract its docstring promises.
    """
    # Per-signal seeds at base 42.
    assert derive_seed(42, "per_signal", "burstiness_B") == 29082
    assert derive_seed(42, "overall", "roc_auc") == 12778

    # Different base seed shifts the offset by the base delta.
    assert derive_seed(0, "per_signal", "burstiness_B") == 29040
    assert derive_seed(100, "per_signal", "burstiness_B") == 29140

    # Empty parts collapses to the base seed (no offset to add).
    assert derive_seed(42) == 42


def test_derive_seed_distinguishes_distinct_part_combinations() -> None:
    """Different `parts` tuples must produce different seeds. If
    `derive_seed("per_signal", "burstiness_B")` and
    `derive_seed("overall", "roc_auc")` collided, two different
    bootstrap RNGs would synchronize and the harness's per-slice
    independence would be a fiction."""
    seen: set[int] = set()
    cases = [
        ("per_signal", "burstiness_B"),
        ("per_signal", "connective_density"),
        ("per_signal", "mattr"),
        ("overall", "roc_auc"),
        ("overall", "average_precision"),
        ("by_register", "essay"),
        ("by_register", "policy_advocacy"),
        ("by_language_status", "native"),
        ("by_language_status", "non_native_advanced"),
    ]
    for parts in cases:
        s = derive_seed(42, *parts)
        assert s is not None
        assert s not in seen, (
            f"derive_seed collision: parts={parts!r} produced "
            f"already-seen seed {s}"
        )
        seen.add(s)


def test_derive_seed_does_not_use_python_hash() -> None:
    """The cross-process-stable contract requires NOT using Python's
    built-in `hash()`. We can't introspect the implementation
    directly, but we can verify the invariant: calling derive_seed
    with the same inputs in this process produces a value that
    matches the pinned constants — and the pinned constants were
    computed once, then verified to reproduce on a fresh interpreter.
    If a future maintainer switches the implementation to use
    `hash()`, the pinned constants will fail under the next CI run
    that uses a different `PYTHONHASHSEED` (or the next developer's
    fresh interpreter).
    """
    # Sanity: the pinned values are what we claim.
    assert derive_seed(42, "per_signal", "burstiness_B") == 29082
    # The actual cross-process check happens implicitly via CI runs
    # against the test_derive_seed_pins_known_values regression.
