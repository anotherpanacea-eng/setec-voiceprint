#!/usr/bin/env python3
"""calibration_drift_monitor.py — infrastructure-drift detector
(paired-release schedule Release 9, Trustworthiness Tier 2/3).

The framework's threshold values and per-signal computations
depend on the dependency stack at run time: spaCy version, the
loaded ``en_core_web_sm`` model version, NLTK data, scipy, and
the Python interpreter. Threshold values that were calibrated
against one stack can shift materially when any of those move.
The score-once cache already carries a ``scorer_version`` field
to invalidate cached scores when the framework deliberately
changes; what's been missing is a check that detects when the
*outputs* shifted even though the framework's own code didn't —
i.e., infrastructure drift.

This module ships that check. It runs the framework's main
audit (`variance_audit.audit_text` + `classify_compression`)
over a fixed set of benchmark texts and records per-signal
values plus the framework's threshold constants. A second
invocation against the same benchmark texts compares the
recomputed values against the snapshot and reports per-signal
drift verdicts: ``stable`` / ``drifted`` / ``unknown``.

Usage:

    # Take a snapshot of current behavior over the benchmark set.
    python3 scripts/calibration_drift_monitor.py snapshot \\
        --benchmark-dir benchmarks/ \\
        --out snapshots/v1.39.0.json

    # Check current behavior against a recorded snapshot.
    python3 scripts/calibration_drift_monitor.py check \\
        --benchmark-dir benchmarks/ \\
        --snapshot snapshots/v1.39.0.json \\
        --json --out drift-report.json

The benchmark directory contains a small fixed set of `.txt` /
`.md` files. The snapshot is byte-stable per (dependency stack,
benchmark set, framework code); any difference is a drift signal
the user should review before publishing claims that depend on
threshold calibration.

task_surface: validation. The check does NOT make authorship
claims; it only reports whether the framework's outputs are
reproducible across the dependency stack at hand.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore

try:
    from variance_audit import (  # type: ignore
        COMPRESSION_HEURISTICS,
        POS_BIGRAM_KL_HEURISTIC,
        HAS_SPACY,
        audit_text,
        classify_compression,
    )
    HAS_VARIANCE_AUDIT = True
except ImportError:
    HAS_VARIANCE_AUDIT = False
    COMPRESSION_HEURISTICS = {}
    POS_BIGRAM_KL_HEURISTIC = None
    HAS_SPACY = False


TASK_SURFACE = "validation"
TOOL_NAME = "calibration_drift_monitor"
SCRIPT_VERSION = "1.0"

# Per-signal noise thresholds for drift detection. A delta within
# the threshold is considered ``stable``; outside is ``drifted``.
# Conservative defaults; intent is to flag moves big enough to
# warrant review, not floating-point noise. Calibration-pending.
_DEFAULT_NOISE_THRESHOLDS: dict[str, float] = {
    # Tier 1 sentence-length signals
    "sentence_length.burstiness_B": 0.05,
    "sentence_length.sd": 0.5,
    "sentence_length.mean": 0.5,
    "mtld": 5.0,
    "mattr.value": 0.02,
    "shannon_entropy_bits": 0.10,
    "yules_k": 10.0,
    "fkgl.sd": 0.20,
    "connective_density.per_1000_tokens": 1.0,
    # Compression
    "compression.compression_fraction": 0.05,
    "compression.weighted_score": 0.20,
    "compression.available_weight": 0.20,
}

_DEFAULT_RELATIVE_THRESHOLD = 0.10  # 10% relative change


# ---------- Benchmark scanning ----------


def _walk_benchmarks(benchmark_dir: Path) -> list[Path]:
    """Return sorted .txt / .md / .rst files under benchmark_dir."""
    if not benchmark_dir.exists():
        raise FileNotFoundError(
            f"Benchmark directory not found: {benchmark_dir}"
        )
    if not benchmark_dir.is_dir():
        raise NotADirectoryError(
            f"--benchmark-dir is not a directory: {benchmark_dir}"
        )
    return sorted(
        p for p in benchmark_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".txt", ".md", ".markdown", ".rst"}
    )


# ---------- Stack metadata ----------


def collect_stack_metadata() -> dict[str, Any]:
    """Capture the dependency-stack metadata that affects
    framework outputs."""
    meta: dict[str, Any] = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "has_spacy": HAS_SPACY,
    }
    try:
        import spacy  # type: ignore
        meta["spacy_version"] = spacy.__version__
        try:
            nlp = spacy.load("en_core_web_sm")
            meta["spacy_model"] = (
                f"en_core_web_sm-{nlp.meta.get('version', 'unknown')}"
            )
        except Exception:
            meta["spacy_model"] = "load_failed"
    except ImportError:
        meta["spacy_version"] = None
        meta["spacy_model"] = None
    try:
        import nltk  # type: ignore
        meta["nltk_version"] = nltk.__version__
    except ImportError:
        meta["nltk_version"] = None
    try:
        import scipy  # type: ignore
        meta["scipy_version"] = scipy.__version__
    except ImportError:
        meta["scipy_version"] = None
    return meta


def collect_framework_constants() -> dict[str, Any]:
    """Capture the framework's threshold constants. These are
    deterministic from the codebase at scan time; they shouldn't
    change without a deliberate code change."""
    out: dict[str, Any] = {
        "compression_heuristics": {},
    }
    if HAS_VARIANCE_AUDIT:
        for name, spec in COMPRESSION_HEURISTICS.items():
            out["compression_heuristics"][name] = {
                "value": spec.value,
                "direction": spec.direction,
                "weight": spec.weight,
                "length_floor": spec.length_floor,
                "signal_path": spec.signal_path,
                "provenance": spec.provenance,
                "provisional": spec.provisional,
            }
        if POS_BIGRAM_KL_HEURISTIC is not None:
            out["pos_bigram_kl_heuristic"] = {
                "value": POS_BIGRAM_KL_HEURISTIC.value,
                "direction": POS_BIGRAM_KL_HEURISTIC.direction,
                "weight": POS_BIGRAM_KL_HEURISTIC.weight,
                "length_floor": POS_BIGRAM_KL_HEURISTIC.length_floor,
                "signal_path": POS_BIGRAM_KL_HEURISTIC.signal_path,
                "provenance": POS_BIGRAM_KL_HEURISTIC.provenance,
                "provisional": POS_BIGRAM_KL_HEURISTIC.provisional,
            }
    return out


# ---------- Per-benchmark signal capture ----------


def _walk_signals(audit: dict[str, Any]) -> dict[str, float]:
    """Extract a flat key→value map of audit signals.

    The keys mirror the ``signal_path`` registry in
    variance_audit (``tier1.sentence_length.burstiness_B`` etc.)
    so a snapshot reads the same paths a calibration step would.
    """
    out: dict[str, float] = {}
    tier1 = audit.get("tier1", {})

    def _walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                _walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            out[prefix] = float(node)

    _walk("", tier1)
    return out


def measure_benchmark(
    text: str, *, do_tier2: bool = True, do_tier3: bool = False,
) -> dict[str, Any]:
    """Run the framework's audit + classify_compression on a
    single benchmark text. Returns a dict of (n_words, signals,
    compression_summary)."""
    if not HAS_VARIANCE_AUDIT:
        raise RuntimeError(
            "variance_audit module unavailable; cannot measure "
            "benchmarks. Install the framework's dependencies."
        )
    audit = audit_text(
        text, do_tier2=do_tier2, do_tier3=do_tier3,
    )
    signals = _walk_signals(audit)
    compression = classify_compression(audit)
    summary = audit.get("summary", {})
    return {
        "n_words": summary.get("n_words"),
        "n_sentences": summary.get("n_sentences"),
        "signals": signals,
        "compression": {
            "band": compression.get("band"),
            "weighted_score": compression.get("weighted_score"),
            "available_weight": compression.get("available_weight"),
            "compression_fraction": (
                compression.get("compression_fraction")
            ),
            "n_flagged": compression.get("n_flagged"),
            "available_signals": list(
                compression.get("available_signals", [])
            ),
        },
    }


# ---------- Snapshot ----------


def take_snapshot(
    benchmark_dir: Path,
    *,
    benchmark_label: str | None = None,
    do_tier2: bool = True,
    do_tier3: bool = False,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Build a snapshot of (stack metadata, framework constants,
    per-benchmark signals)."""
    paths = _walk_benchmarks(benchmark_dir)
    if not paths:
        raise FileNotFoundError(
            f"No benchmark files found in {benchmark_dir}"
        )

    benchmarks: dict[str, Any] = {}
    skipped_empty: list[str] = []
    seen_ids: set[str] = set()
    for idx, path in enumerate(paths):
        # Anonymized: padded benchmark_NNN counter (always unique).
        # With filenames: relative-path-from-benchmark-dir, with
        # path separators normalized to ``/`` so nested benchmarks
        # like ``a/same.txt`` and ``b/same.txt`` produce distinct
        # keys. Pre-1.41.1 used ``path.name`` which collided on
        # duplicate basenames in nested directories and silently
        # shrank the benchmark set. If a relative-path-derived id
        # somehow still collides (symlinks, case-insensitive FS),
        # we fall back to suffixing the anonymized index so no
        # benchmark is dropped.
        if include_filenames:
            try:
                rel = path.relative_to(benchmark_dir)
            except ValueError:
                rel = path
            bench_id = str(rel).replace("\\", "/")
            if bench_id in seen_ids:
                bench_id = f"{bench_id}#{idx + 1:03d}"
        else:
            bench_id = f"benchmark_{idx + 1:03d}"
        seen_ids.add(bench_id)
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            skipped_empty.append(str(path))
            continue
        try:
            measurement = measure_benchmark(
                text, do_tier2=do_tier2, do_tier3=do_tier3,
            )
        except Exception as exc:
            measurement = {"error": str(exc)}
        benchmarks[bench_id] = measurement

    # A snapshot with no measured benchmarks is not a usable
    # CI artifact: a `check` against it would silently pass.
    # Hard-fail rather than write an empty snapshot to disk.
    if not benchmarks:
        raise FileNotFoundError(
            f"No non-empty benchmark files measured in "
            f"{benchmark_dir}. Found {len(paths)} candidate "
            f"file(s); skipped {len(skipped_empty)} as empty / "
            "whitespace-only. A drift snapshot with zero "
            "benchmarks would let CI checks pass without "
            "measuring anything."
        )

    return {
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "snapshot_label": benchmark_label,
        "stack": collect_stack_metadata(),
        "framework_constants": collect_framework_constants(),
        "benchmarks": benchmarks,
        "n_benchmarks": len(benchmarks),
        "skipped_empty": (
            skipped_empty if include_filenames
            else [f"<{len(skipped_empty)} empty/whitespace files>"]
            if skipped_empty else []
        ),
    }


# ---------- Drift detection ----------


def _compare_signals(
    snapshot_signals: dict[str, float],
    current_signals: dict[str, float],
    *,
    relative_threshold: float = _DEFAULT_RELATIVE_THRESHOLD,
    absolute_thresholds: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compare per-signal values; return per-signal drift verdict."""
    absolute_thresholds = absolute_thresholds or _DEFAULT_NOISE_THRESHOLDS
    out: dict[str, dict[str, Any]] = {}
    keys = sorted(set(snapshot_signals) | set(current_signals))
    for key in keys:
        snap = snapshot_signals.get(key)
        curr = current_signals.get(key)
        if snap is None and curr is None:
            continue
        if snap is None:
            out[key] = {
                "snapshot": None, "current": curr,
                "verdict": "added",
            }
            continue
        if curr is None:
            out[key] = {
                "snapshot": snap, "current": None,
                "verdict": "removed",
            }
            continue
        delta = curr - snap
        # Drift threshold: max of the per-signal absolute floor and
        # the relative-change floor.
        rel_floor = abs(snap) * relative_threshold
        abs_floor = absolute_thresholds.get(key, 0.0)
        floor = max(rel_floor, abs_floor)
        verdict = "drifted" if abs(delta) > floor else "stable"
        out[key] = {
            "snapshot": snap, "current": curr,
            "delta": delta,
            "rel_change": (
                delta / snap if abs(snap) > 1e-9 else None
            ),
            "noise_floor": floor,
            "verdict": verdict,
        }
    return out


def _compare_constants(
    snap_constants: dict[str, Any],
    curr_constants: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Compare framework threshold constants. Any change here
    indicates a deliberate code change (or a refactor that moved
    the constants). Reported separately from runtime-signal
    drift because the cause is different."""
    out: dict[str, dict[str, Any]] = {}
    snap_heur = snap_constants.get("compression_heuristics", {})
    curr_heur = curr_constants.get("compression_heuristics", {})
    for name in sorted(set(snap_heur) | set(curr_heur)):
        snap_v = snap_heur.get(name)
        curr_v = curr_heur.get(name)
        if snap_v is None:
            out[name] = {"verdict": "added", "current": curr_v}
            continue
        if curr_v is None:
            out[name] = {"verdict": "removed", "snapshot": snap_v}
            continue
        # Compare each subfield.
        diffs = {}
        for k in ("value", "direction", "weight", "length_floor"):
            if snap_v.get(k) != curr_v.get(k):
                diffs[k] = {
                    "snapshot": snap_v.get(k),
                    "current": curr_v.get(k),
                }
        if diffs:
            out[name] = {"verdict": "changed", "fields": diffs}

    # Compare pos_bigram_kl heuristic separately.
    snap_pb = snap_constants.get("pos_bigram_kl_heuristic")
    curr_pb = curr_constants.get("pos_bigram_kl_heuristic")
    if snap_pb is not None or curr_pb is not None:
        if snap_pb is None:
            out["pos_bigram_kl_heuristic"] = {
                "verdict": "added", "current": curr_pb,
            }
        elif curr_pb is None:
            out["pos_bigram_kl_heuristic"] = {
                "verdict": "removed", "snapshot": snap_pb,
            }
        else:
            diffs = {}
            for k in ("value", "direction", "weight", "length_floor"):
                if snap_pb.get(k) != curr_pb.get(k):
                    diffs[k] = {
                        "snapshot": snap_pb.get(k),
                        "current": curr_pb.get(k),
                    }
            if diffs:
                out["pos_bigram_kl_heuristic"] = {
                    "verdict": "changed", "fields": diffs,
                }
    return out


def _compare_stack(
    snap_stack: dict[str, Any],
    curr_stack: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Compare dependency-stack metadata. Most stack changes
    don't break things, but we report them so a human can decide."""
    out: dict[str, dict[str, Any]] = {}
    keys = (
        "python_version", "spacy_version", "spacy_model",
        "nltk_version", "scipy_version", "has_spacy",
    )
    for k in keys:
        if snap_stack.get(k) != curr_stack.get(k):
            out[k] = {
                "snapshot": snap_stack.get(k),
                "current": curr_stack.get(k),
            }
    return out


def detect_drift(
    *,
    snapshot: dict[str, Any],
    current: dict[str, Any],
    relative_threshold: float = _DEFAULT_RELATIVE_THRESHOLD,
    absolute_thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compare snapshot vs. current. Returns a drift report with
    per-benchmark per-signal verdicts and overall summary."""
    stack_changes = _compare_stack(
        snapshot.get("stack", {}), current.get("stack", {}),
    )
    constant_changes = _compare_constants(
        snapshot.get("framework_constants", {}),
        current.get("framework_constants", {}),
    )

    snap_benches = snapshot.get("benchmarks", {})
    curr_benches = current.get("benchmarks", {})
    per_benchmark: dict[str, Any] = {}
    n_drifted = 0
    n_stable = 0
    drifted_benchmarks: list[str] = []
    for bench_id in sorted(set(snap_benches) | set(curr_benches)):
        snap_b = snap_benches.get(bench_id, {})
        curr_b = curr_benches.get(bench_id, {})
        signal_diffs = _compare_signals(
            snap_b.get("signals", {}),
            curr_b.get("signals", {}),
            relative_threshold=relative_threshold,
            absolute_thresholds=absolute_thresholds,
        )
        # Compression-summary change is reported separately.
        comp_diffs: dict[str, Any] = {}
        snap_comp = snap_b.get("compression", {})
        curr_comp = curr_b.get("compression", {})
        for k in (
            "band", "weighted_score", "available_weight",
            "compression_fraction", "n_flagged",
        ):
            if snap_comp.get(k) != curr_comp.get(k):
                comp_diffs[k] = {
                    "snapshot": snap_comp.get(k),
                    "current": curr_comp.get(k),
                }
        n_signal_drifted = sum(
            1 for d in signal_diffs.values()
            if d.get("verdict") == "drifted"
        )
        n_signal_stable = sum(
            1 for d in signal_diffs.values()
            if d.get("verdict") == "stable"
        )
        # `added` and `removed` are signal-schema changes —
        # signals that appeared or disappeared between snapshot
        # and current. They are infrastructure-drift evidence at
        # least as serious as a value drift, so they count toward
        # bench_drifted and the overall drift verdict.
        n_signal_added = sum(
            1 for d in signal_diffs.values()
            if d.get("verdict") == "added"
        )
        n_signal_removed = sum(
            1 for d in signal_diffs.values()
            if d.get("verdict") == "removed"
        )
        n_signal_schema_changed = n_signal_added + n_signal_removed
        n_drifted += n_signal_drifted
        n_stable += n_signal_stable
        bench_drifted = (
            n_signal_drifted > 0
            or n_signal_schema_changed > 0
            or bool(comp_diffs)
        )
        if bench_drifted:
            drifted_benchmarks.append(bench_id)
        per_benchmark[bench_id] = {
            "n_signals_drifted": n_signal_drifted,
            "n_signals_stable": n_signal_stable,
            "n_signals_added": n_signal_added,
            "n_signals_removed": n_signal_removed,
            "n_signals_schema_changed": n_signal_schema_changed,
            "signal_diffs": signal_diffs,
            "compression_diffs": comp_diffs,
        }

    # Aggregate schema-change counts across benchmarks. A signal
    # appearing or disappearing in a benchmark is a schema change
    # that must count toward overall drift — otherwise a removed
    # signal could let drift detection miss exactly the case it
    # exists to catch.
    n_schema_changed_total = sum(
        b.get("n_signals_schema_changed", 0)
        for b in per_benchmark.values()
    )
    overall_drift = (
        n_drifted > 0
        or n_schema_changed_total > 0
        or bool(constant_changes)
        or bool(comp_diffs_in_aggregate(per_benchmark))
    )
    # The recommendation surface: threshold constants changing,
    # OR the stack changing while *any* drift signal fired
    # (value drift or schema change), recommends recalibration.
    recalibration_recommended = bool(constant_changes) or (
        bool(stack_changes)
        and (n_drifted > 0 or n_schema_changed_total > 0)
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "snapshot_label": snapshot.get("snapshot_label"),
        "stack_changes": stack_changes,
        "constant_changes": constant_changes,
        "per_benchmark": per_benchmark,
        "n_signals_drifted": n_drifted,
        "n_signals_stable": n_stable,
        "n_signals_schema_changed": n_schema_changed_total,
        "n_benchmarks_drifted": len(drifted_benchmarks),
        "drifted_benchmarks": drifted_benchmarks,
        "infrastructure_drift_detected": overall_drift,
        "recalibration_recommended": recalibration_recommended,
        "claim_license": _claim_license_dict(
            stack_changes=stack_changes,
            constant_changes=constant_changes,
            n_signals_drifted=n_drifted,
            n_signals_schema_changed=n_schema_changed_total,
            recalibration=recalibration_recommended,
        ),
    }


def comp_diffs_in_aggregate(
    per_benchmark: dict[str, Any],
) -> dict[str, Any]:
    """Return all non-empty compression_diffs entries."""
    return {
        k: v["compression_diffs"]
        for k, v in per_benchmark.items()
        if v.get("compression_diffs")
    }


def _claim_license_dict(
    *,
    stack_changes: dict[str, Any],
    constant_changes: dict[str, Any],
    n_signals_drifted: int,
    n_signals_schema_changed: int = 0,
    recalibration: bool,
) -> dict[str, Any]:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "An infrastructure-drift report comparing the "
            "framework's outputs on a fixed benchmark set "
            "between a recorded snapshot and the current "
            "dependency stack. For each per-benchmark per-signal "
            "value the report shows the snapshot value, the "
            "current value, the delta, the noise floor used, "
            "and a stable / drifted verdict. Framework threshold "
            "constants and dependency-stack metadata are "
            "compared separately."
        ),
        does_not_license=(
            "A judgment that the framework's results are correct, "
            "calibrated, or fit for any specific use. The drift "
            "monitor reports REPRODUCIBILITY across the "
            "dependency stack — whether the framework's outputs "
            "are byte-stable from one run to another. It does "
            "NOT validate the underlying calibration. A snapshot "
            "with miscalibrated thresholds remains "
            "miscalibrated; the monitor only flags when those "
            "miscalibrated values move further."
        ),
        comparison_set={
            "n_signals_drifted": n_signals_drifted,
            "n_signals_schema_changed": n_signals_schema_changed,
            "stack_changes": list(stack_changes.keys()),
            "constant_changes": list(constant_changes.keys()),
            "recalibration_recommended": recalibration,
        },
        additional_caveats=[
            "The noise-floor thresholds are heuristic. "
            "Per-signal floors are conservative defaults; CI "
            "users should tune them to the noise band they "
            "consider material.",
            "Threshold-constant changes detect deliberate code "
            "changes, not infrastructure drift. They are "
            "reported because both shape framework outputs "
            "from the user's perspective.",
            "The drift monitor's value scales with the size and "
            "diversity of the benchmark set. A single-benchmark "
            "run answers \"does this benchmark reproduce?\"; a "
            "multi-benchmark run answers \"is the framework "
            "reproducible across realistic inputs?\"",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


def render_report(report: dict[str, Any]) -> str:
    drifted = report.get("drifted_benchmarks", [])
    n_drifted = report.get("n_signals_drifted", 0)
    n_stable = report.get("n_signals_stable", 0)
    n_schema = report.get("n_signals_schema_changed", 0)
    overall = report.get("infrastructure_drift_detected", False)
    recalibrate = report.get("recalibration_recommended", False)

    lines: list[str] = [
        "# Calibration drift monitor",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Snapshot label:** "
        f"{report.get('snapshot_label') or '(unlabeled)'}",
        f"**Infrastructure drift detected:** "
        f"{'**yes**' if overall else 'no'}",
        f"**Recalibration recommended:** "
        f"{'**yes**' if recalibrate else 'no'}",
        # Pre-1.41.1 only `drifted` and `stable` were surfaced; a
        # signal that disappeared (`removed`) or newly appeared
        # (`added`) showed up in the JSON's
        # `n_signals_schema_changed` but was invisible in the
        # Markdown header. Fixed by reporting schema_changed
        # alongside drifted/stable in the summary line.
        f"**Signals drifted / schema-changed / stable:** "
        f"{n_drifted} / {n_schema} / {n_stable}",
        f"**Benchmarks drifted:** {len(drifted)}",
        "",
    ]

    stack_changes = report.get("stack_changes", {})
    if stack_changes:
        lines.append("## Stack changes")
        lines.append("")
        for k, v in stack_changes.items():
            lines.append(
                f"- `{k}`: {v.get('snapshot')} → {v.get('current')}"
            )
        lines.append("")

    constant_changes = report.get("constant_changes", {})
    if constant_changes:
        lines.append("## Framework threshold-constant changes")
        lines.append("")
        for name, info in constant_changes.items():
            verdict = info.get("verdict", "changed")
            lines.append(f"- `{name}`: {verdict}")
            for field, vals in (info.get("fields") or {}).items():
                lines.append(
                    f"  - `{field}`: "
                    f"{vals.get('snapshot')} → {vals.get('current')}"
                )
        lines.append("")

    if drifted:
        lines.append("## Drifted benchmarks")
        lines.append("")
        for bench_id in drifted:
            info = report.get("per_benchmark", {}).get(bench_id, {})
            n_drift_b = info.get("n_signals_drifted", 0)
            n_stable_b = info.get("n_signals_stable", 0)
            n_added_b = info.get("n_signals_added", 0)
            n_removed_b = info.get("n_signals_removed", 0)
            schema_summary = ""
            if n_added_b or n_removed_b:
                schema_summary = (
                    f", {n_added_b} added, {n_removed_b} removed"
                )
            lines.append(
                f"### `{bench_id}` "
                f"({n_drift_b} signals drifted, "
                f"{n_stable_b} stable{schema_summary})"
            )
            lines.append("")
            comp_diffs = info.get("compression_diffs", {})
            if comp_diffs:
                lines.append("**Compression band changes:**")
                for k, vals in comp_diffs.items():
                    lines.append(
                        f"- `{k}`: {vals.get('snapshot')} → "
                        f"{vals.get('current')}"
                    )
                lines.append("")
            sig_diffs = info.get("signal_diffs", {})
            drifted_sig = [
                (k, d) for k, d in sig_diffs.items()
                if d.get("verdict") == "drifted"
            ]
            if drifted_sig:
                lines.append("**Drifted signals:**")
                for k, d in drifted_sig:
                    snap_v = d.get("snapshot")
                    curr_v = d.get("current")
                    delta = d.get("delta", 0.0) or 0.0
                    floor = d.get("noise_floor", 0.0) or 0.0
                    lines.append(
                        f"- `{k}`: {snap_v} → {curr_v} "
                        f"(Δ {delta:+.4f}, floor {floor:.4f})"
                    )
                lines.append("")
            # Schema-change signals (added / removed) are
            # drift-bearing per 1.40.1 but were missing from the
            # Markdown render before 1.41.1. Surface them under
            # the same per-benchmark section so the report names
            # the changing signal rather than just an aggregate
            # counter.
            added_sig = [
                (k, d) for k, d in sig_diffs.items()
                if d.get("verdict") == "added"
            ]
            removed_sig = [
                (k, d) for k, d in sig_diffs.items()
                if d.get("verdict") == "removed"
            ]
            if added_sig:
                lines.append("**Added signals (in current, "
                             "not in snapshot):**")
                for k, d in added_sig:
                    lines.append(
                        f"- `{k}`: now `{d.get('current')}` "
                        f"(absent at snapshot time)"
                    )
                lines.append("")
            if removed_sig:
                lines.append("**Removed signals (in snapshot, "
                             "not in current):**")
                for k, d in removed_sig:
                    lines.append(
                        f"- `{k}`: was `{d.get('snapshot')}` "
                        f"(missing in current run)"
                    )
                lines.append("")

    license_block = report.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_snapshot(path_str: str) -> dict[str, Any]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"Snapshot file not found: {path_str}"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Snapshot file is not valid JSON: {exc}"
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibration_drift_monitor.py",
        description=(
            "Infrastructure-drift detector. Records or compares "
            "the framework's outputs on a fixed benchmark set "
            "across the dependency stack."
        ),
    )
    sub = p.add_subparsers(dest="mode", required=True)

    snap = sub.add_parser(
        "snapshot",
        help="Take a snapshot of current behavior.",
    )
    snap.add_argument(
        "--benchmark-dir", required=True,
        help="Directory of fixed benchmark files (.txt / .md / .rst).",
    )
    snap.add_argument(
        "--out", required=True,
        help="Output path for the snapshot JSON.",
    )
    snap.add_argument(
        "--snapshot-label",
        help="Optional label (e.g., framework version or stack tag).",
    )
    snap.add_argument(
        "--no-tier2", action="store_true",
        help="Skip tier-2 (spaCy) signals in the snapshot.",
    )
    snap.add_argument(
        "--include-filenames", action="store_true",
        help="Use filenames as benchmark IDs (default: anonymize).",
    )

    chk = sub.add_parser(
        "check",
        help="Compare current behavior against a recorded snapshot.",
    )
    chk.add_argument(
        "--benchmark-dir", required=True,
        help="Directory of the same benchmark files used at snapshot time.",
    )
    chk.add_argument(
        "--snapshot", required=True,
        help="Path to the snapshot JSON to compare against.",
    )
    chk.add_argument(
        "--out",
        help="Optional output path for the drift report.",
    )
    chk.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of Markdown.",
    )
    chk.add_argument(
        "--no-tier2", action="store_true",
        help="Skip tier-2 (spaCy) signals at check time.",
    )
    chk.add_argument(
        "--relative-threshold", type=float,
        default=_DEFAULT_RELATIVE_THRESHOLD,
        help=(
            "Relative-change threshold for per-signal drift "
            "verdict (default 10%%)."
        ),
    )
    chk.add_argument(
        "--include-filenames", action="store_true",
        help="Use filenames as benchmark IDs (must match snapshot).",
    )
    chk.add_argument(
        "--exit-nonzero-on-drift", action="store_true",
        help=(
            "Exit with code 3 when drift is detected. Useful in "
            "CI to fail the build on drift."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not HAS_VARIANCE_AUDIT:
        sys.stderr.write(
            "variance_audit unavailable; cannot run drift monitor.\n"
        )
        return 2

    if args.mode == "snapshot":
        try:
            snapshot = take_snapshot(
                Path(args.benchmark_dir).expanduser(),
                benchmark_label=args.snapshot_label,
                do_tier2=not args.no_tier2,
                include_filenames=args.include_filenames,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            sys.stderr.write(f"--benchmark-dir: {exc}\n")
            return 2
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(snapshot, indent=2, default=str),
            encoding="utf-8",
        )
        sys.stderr.write(
            f"Wrote snapshot ({snapshot['n_benchmarks']} benchmarks) "
            f"to {args.out}\n"
        )
        return 0

    if args.mode == "check":
        try:
            snapshot = _read_snapshot(args.snapshot)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"--snapshot: {exc}\n")
            return 2
        try:
            current = take_snapshot(
                Path(args.benchmark_dir).expanduser(),
                benchmark_label=snapshot.get("snapshot_label"),
                do_tier2=not args.no_tier2,
                include_filenames=args.include_filenames,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            sys.stderr.write(f"--benchmark-dir: {exc}\n")
            return 2

        report = detect_drift(
            snapshot=snapshot,
            current=current,
            relative_threshold=args.relative_threshold,
        )
        out = (
            json.dumps(report, indent=2, default=str)
            if args.json else render_report(report)
        )
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
            sys.stderr.write(f"Wrote drift report to {args.out}\n")
        else:
            sys.stdout.write(out)

        if args.exit_nonzero_on_drift and report.get(
            "infrastructure_drift_detected"
        ):
            return 3
        return 0

    sys.stderr.write(f"Unknown mode: {args.mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
