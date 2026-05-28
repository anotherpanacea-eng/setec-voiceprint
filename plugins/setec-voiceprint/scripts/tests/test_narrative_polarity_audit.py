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
