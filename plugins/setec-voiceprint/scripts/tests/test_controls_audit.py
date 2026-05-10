#!/usr/bin/env python3
"""Regression tests for controls_audit.py (Release 6)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import controls_audit as ca  # type: ignore


_BASELINE_TEXTS = [
    "The quiet of the kitchen was its own kind of light. She wrote in "
    "the morning before the day began. The dog stretched by the fire." * 8,
    "You can write a thousand sentences and only three of them will "
    "surprise you. The trick is knowing which three. The rest is "
    "bookkeeping." * 8,
    "My grandmother said: write what frightens you, and write it on "
    "Tuesday. I never asked why Tuesday. I should have." * 8,
]
_NEGATIVE_CONTROL = (
    "The kitchen was quiet. She wrote in the morning. The dog watched. "
    "Three sentences out of a thousand will surprise you."
) * 12
_POSITIVE_CONTROL = (
    "The implementation provides actionable insights through holistic "
    "analysis. Frameworks must address multiple dimensions in a robust "
    "manner. Recommendations are provided."
) * 12


class TestRunControlsAuditBasics:
    def test_empty_baseline_unavailable(self):
        report = ca.run_controls_audit(
            questioned_text="some text",
            baseline_texts=[],
        )
        assert report["available"] is False

    def test_empty_questioned_returns_unavailable_classification(self):
        report = ca.run_controls_audit(
            questioned_text="",
            baseline_texts=_BASELINE_TEXTS,
        )
        # The questioned distance is None; classification should
        # report unavailable.
        assert report["classification"]["interpretation"] == "questioned_unavailable"

    def test_baseline_only_no_controls(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
        )
        assert report["classification"]["interpretation"] == "baseline_only"

    def test_three_distances_recorded(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        for entry in (
            report["questioned"], report["negative_control"],
            report["positive_control"],
        ):
            assert "distance" in entry
            assert entry["distance"] is not None


class TestClassification:
    def test_questioned_identical_to_negative_classifies_as_negative(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        assert report["classification"]["interpretation"] == "closer_to_negative_control"

    def test_questioned_identical_to_positive_classifies_as_positive(self):
        report = ca.run_controls_audit(
            questioned_text=_POSITIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        assert report["classification"]["interpretation"] == "closer_to_positive_control"

    def test_questioned_within_control_band_recorded(self):
        # Synthetic: questioned distance falls between the two
        # control distances → within_band=True.
        # Use mid-shape text — half-and-half.
        questioned = (
            _NEGATIVE_CONTROL[:len(_NEGATIVE_CONTROL) // 2]
            + _POSITIVE_CONTROL[:len(_POSITIVE_CONTROL) // 2]
        )
        report = ca.run_controls_audit(
            questioned_text=questioned,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        # Just verify the field is set; mid-shape text usually
        # falls within the band.
        assert "questioned_within_control_band" in report["classification"]

    def test_negative_only_classification(self):
        report = ca.run_controls_audit(
            questioned_text=_POSITIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
        )
        assert report["classification"]["interpretation"] == "negative_only"
        assert "gap_to_negative" in report["classification"]

    def test_positive_only_classification(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            positive_control_text=_POSITIVE_CONTROL,
        )
        assert report["classification"]["interpretation"] == "positive_only"
        assert "gap_to_positive" in report["classification"]


class TestRender:
    def test_markdown_includes_claim_license(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        md = ca.render_report(report)
        assert "## What this result licenses" in md

    def test_markdown_includes_side_by_side_table(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
            negative_control_text=_NEGATIVE_CONTROL,
            positive_control_text=_POSITIVE_CONTROL,
        )
        md = ca.render_report(report)
        assert "## Side-by-side distances" in md
        assert "questioned" in md.lower()

    def test_markdown_renders_without_controls(self):
        report = ca.run_controls_audit(
            questioned_text=_NEGATIVE_CONTROL,
            baseline_texts=_BASELINE_TEXTS,
        )
        md = ca.render_report(report)
        # Should still render with "(not supplied)" entries.
        assert "(not supplied)" in md


class TestCli:
    def test_cli_requires_baseline(self, tmp_path):
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        rc = ca.main(["--questioned", str(questioned)])
        assert rc == 2

    def test_cli_round_trip(self, tmp_path):
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        neg = tmp_path / "neg.txt"
        neg.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        pos = tmp_path / "pos.txt"
        pos.write_text(_POSITIVE_CONTROL, encoding="utf-8")
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        for i, text in enumerate(_BASELINE_TEXTS):
            (baseline_dir / f"f{i}.txt").write_text(text, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = ca.main([
            "--questioned", str(questioned),
            "--negative-control", str(neg),
            "--positive-control", str(pos),
            "--baseline-dir", str(baseline_dir),
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "voice_coherence"

    def test_cli_handles_missing_questioned(self, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        rc = ca.main([
            "--questioned", str(tmp_path / "missing.txt"),
            "--baseline-dir", str(baseline_dir),
        ])
        assert rc == 2


# ---------- 1.37.1 reviewer-flagged P2 fixes ----------------------


class TestMissingControlPathsHardFail:
    """Pre-1.37.1, missing user-supplied control paths printed an
    error and silently downgraded to baseline-only / single-pole.
    Reviewer reproduced rc=0 with a missing negative-control path.
    Fix: bad paths return rc=2 (matches the hardened-input
    convention from confounder_audit / evidentiary_conditions_gate)."""

    def test_missing_negative_control_returns_2(self, tmp_path):
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        for i, text in enumerate(_BASELINE_TEXTS):
            (baseline_dir / f"f{i}.txt").write_text(text, encoding="utf-8")
        rc = ca.main([
            "--questioned", str(questioned),
            "--negative-control", str(tmp_path / "missing_neg.txt"),
            "--baseline-dir", str(baseline_dir),
        ])
        assert rc == 2

    def test_missing_positive_control_returns_2(self, tmp_path):
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        for i, text in enumerate(_BASELINE_TEXTS):
            (baseline_dir / f"f{i}.txt").write_text(text, encoding="utf-8")
        rc = ca.main([
            "--questioned", str(questioned),
            "--positive-control", str(tmp_path / "missing_pos.txt"),
            "--baseline-dir", str(baseline_dir),
        ])
        assert rc == 2

    def test_supplied_controls_still_work(self, tmp_path):
        """Sanity: when paths are valid the CLI still succeeds."""
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        neg = tmp_path / "neg.txt"
        neg.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        pos = tmp_path / "pos.txt"
        pos.write_text(_POSITIVE_CONTROL, encoding="utf-8")
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        for i, text in enumerate(_BASELINE_TEXTS):
            (baseline_dir / f"f{i}.txt").write_text(text, encoding="utf-8")
        rc = ca.main([
            "--questioned", str(questioned),
            "--negative-control", str(neg),
            "--positive-control", str(pos),
            "--baseline-dir", str(baseline_dir),
            "--json", "--out", str(tmp_path / "out.json"),
        ])
        assert rc == 0


class TestEmptyPostFilterBaseline:
    """Pre-1.37.1, a baseline that contained only the questioned
    file (or only files matching the questioned + control paths)
    would be silently filtered to empty and the audit would exit
    0 with available=false. Fix: hard-fail with rc=2 — same
    convention paragraph_audit + general_imposters use."""

    def test_baseline_only_questioned_returns_2(self, tmp_path, capsys):
        questioned = tmp_path / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        # Baseline directory contains ONLY the questioned file.
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        # Symlink-equivalent: write the same content under
        # baseline-dir/q.txt then point --questioned at it.
        baseline_q = baseline_dir / "q.txt"
        baseline_q.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        rc = ca.main([
            "--questioned", str(baseline_q),
            "--baseline-dir", str(baseline_dir),
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "baseline empty" in captured.err.lower() or "after dropping" in captured.err.lower()

    def test_baseline_with_other_files_still_works(self, tmp_path):
        """Sanity: a baseline with non-overlapping files still
        succeeds even when one entry overlaps the questioned."""
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        # Three baseline files; one is the questioned text.
        questioned = baseline_dir / "q.txt"
        questioned.write_text(_NEGATIVE_CONTROL, encoding="utf-8")
        (baseline_dir / "other1.txt").write_text(
            _BASELINE_TEXTS[0], encoding="utf-8",
        )
        (baseline_dir / "other2.txt").write_text(
            _BASELINE_TEXTS[1], encoding="utf-8",
        )
        rc = ca.main([
            "--questioned", str(questioned),
            "--baseline-dir", str(baseline_dir),
            "--json", "--out", str(tmp_path / "out.json"),
        ])
        # questioned overlapped one baseline entry, but two others
        # remain. Should succeed with rc=0.
        assert rc == 0


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
