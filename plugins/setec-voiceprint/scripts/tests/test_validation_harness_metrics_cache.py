#!/usr/bin/env python3
"""Tests for the metrics/bootstrap checkpoint (issue #132).

The metrics phase used to be single-threaded, silent, and uncheckpointed:
a crash mid-bootstrap lost the entire phase. These tests pin the new
contract — each per-slice / per-signal bootstrap CI is a checkpointed,
resumable unit, and the cache never changes results (the bootstrap is
deterministic given a fixed seed).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from validation_harness import build_slices, _MetricsCheckpoint


def _records(n: int = 40) -> list[dict]:
    rnd = random.Random(0)
    recs: list[dict] = []
    for i in range(n):
        label = i % 2
        score = 0.3 + 0.4 * label + rnd.random() * 0.2
        recs.append({
            "id": f"r{i}",
            "label": label,
            "score": score,
            "usable_for_metrics": True,
            "per_signal_scores": {
                "yules_k": score + rnd.random() * 0.1,
                "mattr": 1.0 - score,
            },
            "ai_status": "ai_generated" if label else "pre_ai_human",
            "register": "blog_essay",
            "length_bucket": "200_499",
            "language_status": "english",
            "adversarial_class": "none",
        })
    return recs


_KW = dict(
    threshold=None,
    confidence_level=0.95,
    ci_method="wilson",
    metric_bootstrap_resamples=200,
    seed=42,
)


def test_metrics_cache_resume_reuses_and_is_identical(tmp_path) -> None:
    recs = _records()
    cache = tmp_path / "metrics_cache.json"
    meta = {"corpus": "synthetic", "resamples": 200, "seed": 42}

    ck1 = _MetricsCheckpoint(cache, meta, flush_every=5, refresh=False)
    out1 = build_slices(recs, ckpt=ck1, **_KW)
    ck1.flush(status="complete")
    s1 = ck1.summary()
    assert s1["computed"] > 0
    assert s1["reused"] == 0
    assert cache.exists()

    # Fresh checkpoint loads the on-disk partial -> every CI is reused,
    # none recomputed, and the assembled output is byte-identical.
    ck2 = _MetricsCheckpoint(cache, meta, flush_every=5, refresh=False)
    out2 = build_slices(recs, ckpt=ck2, **_KW)
    s2 = ck2.summary()
    assert s2["computed"] == 0
    assert s2["reused"] == s1["computed"]
    assert out1 == out2


def test_no_cache_is_a_passthrough(tmp_path) -> None:
    """A cached run must produce the same result as an uncached run."""
    recs = _records()
    out_plain = build_slices(recs, **_KW)  # ckpt defaults to None

    cache = tmp_path / "m.json"
    ck = _MetricsCheckpoint(cache, {"x": 1}, flush_every=2, refresh=False)
    out_cached = build_slices(recs, ckpt=ck, **_KW)
    assert out_plain == out_cached


def test_metrics_cache_meta_mismatch_recomputes(tmp_path) -> None:
    recs = _records()
    cache = tmp_path / "m.json"

    ck1 = _MetricsCheckpoint(cache, {"v": 1}, flush_every=100, refresh=False)
    build_slices(recs, ckpt=ck1, **_KW)
    ck1.flush(status="complete")

    # Incompatible meta -> the on-disk cache is ignored; recompute.
    ck2 = _MetricsCheckpoint(cache, {"v": 2}, flush_every=100, refresh=False)
    build_slices(recs, ckpt=ck2, **_KW)
    assert ck2.summary()["reused"] == 0
    assert ck2.summary()["computed"] > 0


def test_refresh_discards_existing_cache(tmp_path) -> None:
    recs = _records()
    cache = tmp_path / "m.json"

    ck1 = _MetricsCheckpoint(cache, {"v": 1}, flush_every=100, refresh=False)
    build_slices(recs, ckpt=ck1, **_KW)
    ck1.flush(status="complete")

    # refresh=True ignores the matching cache and recomputes everything.
    ck2 = _MetricsCheckpoint(cache, {"v": 1}, flush_every=100, refresh=True)
    build_slices(recs, ckpt=ck2, **_KW)
    assert ck2.summary()["reused"] == 0
    assert ck2.summary()["computed"] > 0


if __name__ == "__main__":  # pragma: no cover
    import pytest as _pt
    raise SystemExit(_pt.main([__file__, "-v"]))
