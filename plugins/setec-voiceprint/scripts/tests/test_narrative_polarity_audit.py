#!/usr/bin/env python3
"""Regression tests for calibration/narrative_polarity_audit.py.

Pins:

  * Per-signal `min_class_n` floor forces verdict=chance on tiny
    samples (PR #128 review P2 — Hanley-McNeil SE collapses to 0
    on perfect separation in tiny manifests, producing spurious
    matches/inverted labels without the floor).
  * Aggregate scorer still computes its AUC; that's allowed since
    the aggregate is over all rows, not per-signal.
  * One-class manifest is rejected at the CLI level (PR #128
    review P2 — a manifest with only human rows used to exit 0).
  * Default min_class_n is 20 and surfaces in the report JSON.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALIB = ROOT / "calibration"
for p in (str(ROOT), str(CALIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import narrative_polarity_audit as npa  # type: ignore  # noqa: E402
from narrative_feature_schema import (  # type: ignore  # noqa: E402
    CORE_FEATURES,
)


def _make_rows(n_human: int, n_ai: int) -> list:
    """Build synthetic rows where AI rows pick AI-elevated options
    and human rows pick human-elevated options, producing perfect
    separation on most signals."""
    human_values = {}
    ai_values = {}
    for f in CORE_FEATURES:
        if f.feature_type == "scale":
            human_values[f.key] = "2"
            ai_values[f.key] = "5"
        elif f.feature_type == "ordinal":
            human_values[f.key] = f.response_options[0]
            ai_values[f.key] = f.response_options[-1]
        elif f.feature_type == "binary":
            sig = f.signals[0]
            human_values[f.key] = "no" if sig.leaning == "ai" else "yes"
            ai_values[f.key] = "yes" if sig.leaning == "ai" else "no"
        elif f.feature_type == "categorical":
            ai_opt = next(
                (s.option for s in f.signals if s.leaning == "ai"), None,
            )
            hu_opt = next(
                (s.option for s in f.signals if s.leaning == "human"), None,
            )
            human_values[f.key] = hu_opt or f.response_options[0]
            ai_values[f.key] = ai_opt or f.response_options[-1]
        else:  # multi
            ai_opt = next(
                (s.option for s in f.signals if s.leaning == "ai"), None,
            )
            hu_opt = next(
                (s.option for s in f.signals if s.leaning == "human"), None,
            )
            human_values[f.key] = [hu_opt] if hu_opt else []
            ai_values[f.key] = [ai_opt] if ai_opt else []
    rows = []
    for i in range(n_human):
        rows.append(npa.Row(
            text_id=f"h{i}", label="human",
            raw_label="pre_ai_human",
            values=dict(human_values),
        ))
    for i in range(n_ai):
        rows.append(npa.Row(
            text_id=f"a{i}", label="ai",
            raw_label="ai_generated",
            values=dict(ai_values),
        ))
    return rows


def test_tiny_samples_forced_to_chance():
    """With 1 row per class, the (degenerate) CI clears 0.5 on every
    perfectly-separated signal; min_class_n forces verdict=chance."""
    rows = _make_rows(n_human=1, n_ai=1)
    cells = npa.per_signal_polarity(rows, min_class_n=20)
    n_matches = sum(1 for c in cells if c.verdict == "matches")
    n_inverted = sum(1 for c in cells if c.verdict == "inverted")
    assert n_matches == 0, (
        f"tiny sample should not emit confident matches; got {n_matches}"
    )
    assert n_inverted == 0, (
        f"tiny sample should not emit confident inverted; got "
        f"{n_inverted}"
    )
    for c in cells:
        if c.verdict == "chance" and c.n_pos > 0 and c.n_neg > 0:
            assert any(
                "below min_class_n" in n for n in c.notes
            ), (
                f"chance verdict from min-n floor must explain itself "
                f"in notes (cell {c.feature_key})"
            )


def test_small_floor_allows_match_verdicts():
    """Lowering the floor to 1 reproduces the pre-fix behavior; this
    pins the flag's actual effect rather than just its default."""
    rows = _make_rows(n_human=1, n_ai=1)
    cells = npa.per_signal_polarity(rows, min_class_n=1)
    n_matches = sum(1 for c in cells if c.verdict == "matches")
    # With min_class_n=1 the spurious matches reappear, confirming
    # the floor is the load-bearing fix.
    assert n_matches > 0, (
        "min_class_n=1 should permit the prior (spurious) matches"
    )


def test_adequate_sample_emits_real_verdicts():
    """At n=20 per class, perfectly-separated signals get matches."""
    rows = _make_rows(n_human=25, n_ai=25)
    cells = npa.per_signal_polarity(rows, min_class_n=20)
    n_matches = sum(1 for c in cells if c.verdict == "matches")
    assert n_matches > 10, (
        f"adequately-sampled separation should yield matches; "
        f"got only {n_matches}"
    )


def test_one_class_manifest_rejected_at_cli():
    """PR #128 review (P2): a manifest with only human rows used to
    write reports with all 33 cells unavailable and exit 0."""
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "human_only.jsonl"
        rows = _make_rows(n_human=5, n_ai=0)
        with manifest.open("w") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "text_id": r.text_id,
                    "label": r.raw_label,
                    "narrative_values": r.values,
                }) + "\n")
        rc = npa.main([
            "--manifest", str(manifest),
            "--out-json", str(Path(td) / "out.json"),
            "--out-md", str(Path(td) / "out.md"),
            "--corpus-name", "human-only",
        ])
        assert rc != 0, (
            "one-class manifest should be rejected, not exit cleanly"
        )


def test_two_class_manifest_runs_to_completion():
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "balanced.jsonl"
        rows = _make_rows(n_human=25, n_ai=25)
        with manifest.open("w") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "text_id": r.text_id,
                    "label": r.raw_label,
                    "narrative_values": r.values,
                }) + "\n")
        out_json = Path(td) / "out.json"
        rc = npa.main([
            "--manifest", str(manifest),
            "--out-json", str(out_json),
            "--out-md", str(Path(td) / "out.md"),
            "--corpus-name", "balanced",
        ])
        assert rc == 0
        report = json.loads(out_json.read_text())
        assert report["min_class_n"] == 20
        assert report["n_rows"]["human"] == 25
        assert report["n_rows"]["ai"] == 25


def test_min_class_n_below_one_rejected():
    """Defensive: --min-class-n 0 (or negative) makes no sense."""
    with tempfile.TemporaryDirectory() as td:
        manifest = Path(td) / "balanced.jsonl"
        rows = _make_rows(n_human=5, n_ai=5)
        with manifest.open("w") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "text_id": r.text_id,
                    "label": r.raw_label,
                    "narrative_values": r.values,
                }) + "\n")
        rc = npa.main([
            "--manifest", str(manifest),
            "--out-json", str(Path(td) / "o.json"),
            "--out-md", str(Path(td) / "o.md"),
            "--corpus-name", "bad-flag",
            "--min-class-n", "0",
        ])
        assert rc != 0


# ---------- perf fix: index pivot replaces inner recompute --------

def test_index_target_values_matches_per_signal_contributions():
    """The pivot helper used by per_signal_polarity must produce the
    same target_value mapping as iterating per_signal_contributions
    directly. This pins behavioral equivalence between the v0.1
    O(S²·N) loop and the v0.2 O(S·N) pivot — the perf fix can't be
    allowed to drift the numbers."""
    from narrative_decision_audit import per_signal_contributions
    rows = _make_rows(n_human=2, n_ai=2)
    for r in rows:
        pivoted = npa._index_target_values(r)
        # Direct path: build the (feature_key, option) -> target_value
        # map from per_signal_contributions and compare.
        direct: dict[tuple, float] = {}
        for c in per_signal_contributions(r.values):
            if c.target_value is None:
                continue
            direct[(c.feature_key, c.option)] = c.target_value
        assert pivoted == direct, (
            f"pivot diverged from direct: extra in pivot="
            f"{set(pivoted) - set(direct)}; missing in pivot="
            f"{set(direct) - set(pivoted)}"
        )


def test_per_signal_polarity_results_stable_under_pivot():
    """End-to-end equivalence check: per_signal_polarity output
    against a fixed manifest should be deterministic and stable
    across re-runs (the pivot doesn't introduce ordering nondeterminism
    or partial coverage drift)."""
    rows = _make_rows(n_human=25, n_ai=25)
    cells_a = npa.per_signal_polarity(rows, min_class_n=20)
    cells_b = npa.per_signal_polarity(rows, min_class_n=20)
    assert len(cells_a) == len(cells_b)
    for a, b in zip(cells_a, cells_b):
        assert a.feature_key == b.feature_key
        assert a.option == b.option
        assert a.n_pos == b.n_pos
        assert a.n_neg == b.n_neg
        assert a.raw_auc == b.raw_auc
        assert a.da_auc == b.da_auc
        assert a.verdict == b.verdict


def test_per_signal_polarity_calls_pivot_once_per_row():
    """Pin the perf fix at the call-count level instead of wall-time
    (which is dominated by the inner Mann-Whitney's O(N²) at small N
    and is unreliable). The fix is real iff per_signal_contributions
    runs exactly N times per call (one per row), not N·S times. We
    monkeypatch the imported reference inside narrative_polarity_audit
    to count calls.
    """
    rows = _make_rows(n_human=20, n_ai=20)
    n_rows = len(rows)
    call_count = {"n": 0}
    real = npa.per_signal_contributions

    def counted(values):
        call_count["n"] += 1
        return real(values)

    npa.per_signal_contributions = counted  # type: ignore
    try:
        npa.per_signal_polarity(rows, min_class_n=20)
    finally:
        npa.per_signal_contributions = real  # type: ignore

    # Pre-fix: 33 signals × 40 rows = 1,320 calls. Post-fix: 40 calls
    # (one per row, via _index_target_values). Allow a small constant
    # overhead for any future bookkeeping calls.
    assert call_count["n"] <= n_rows + 2, (
        f"per_signal_contributions called {call_count['n']} times "
        f"for {n_rows} rows; expected ~{n_rows} (one per row). "
        f"Did the O(S²·N) loop come back?"
    )


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
