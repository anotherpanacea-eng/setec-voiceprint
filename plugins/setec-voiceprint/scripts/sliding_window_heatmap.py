#!/usr/bin/env python3
"""sliding_window_heatmap.py — render variance_audit sliding-window output as a heatmap.

Cathedral upgrade #5 finisher. The sliding-window mode in
``variance_audit.py`` (shipped pre-1.10) emits per-window
compression band classifications and per-window flagged-signal
lists. Whole-document distance is blunt; the cathedral version of
the question is "where in the document is the smoothing
concentrated, and which signals fire there?" This script consumes
``variance_audit.py``'s ``--json`` output (or just the ``windows``
block) and renders it as:

  1. A **compression-fraction sparkline** — a one-line ASCII bar
     chart showing the per-window ``compression_fraction`` so the
     spatial shape of the signal is legible at a glance.
  2. A **band tape** — a horizontal strip of single-character band
     codes (``H``/``M``/``L``/``-``) so a reader can see which
     windows fired which band without reading numbers.
  3. A **hot-zones summary** — contiguous runs of Moderately /
     Heavily smoothed windows surfaced in word-position coordinates
     (e.g., "words 1500–2500"), with the band, fraction range, and
     dominant flagged signals for each hot zone.
  4. A **per-signal × per-window heatmap table** — a grid showing
     which signals fired in which windows. Rows are signals, columns
     are windows; each cell is filled when that signal flagged in
     that window. Localizes which signals carry the band call where.
  5. A **claim-license block** (via ``claim_license.py``) explaining
     what the heatmap entitles ("a localization map of where the
     band classification fires") and what it does NOT entitle
     ("a smoothing diagnosis on its own; window z-scores are noisy
     and band calls below 200 words are unreliable").

Usage:

    # From a saved variance_audit.py --json output:
    python3 scripts/sliding_window_heatmap.py \\
        --in path/to/variance_audit_output.json \\
        --out path/to/heatmap_report.md

    # Or pipe directly:
    python3 scripts/variance_audit.py draft.txt --json --window-size 500 \\
        | python3 scripts/sliding_window_heatmap.py --out heatmap.md

Privacy: window-band classifications are *less* voiceprint-shaped
than per-token features, but they still localize compressed regions
of the writer's draft. Default output paths must live under
``ai-prose-baselines-private/``; the marker-based privacy guard
refuses non-private outputs unless ``--allow-public-output`` is
passed (matching the convention from ``voice_profile.py`` and
``general_imposters.py``).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # noqa: E402
from output_schema import build_output  # noqa: E402

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "sliding_window_heatmap"
SCRIPT_VERSION = "1.0"

# Mapping from variance_audit's band labels to single-character codes
# for the compact band tape. The "-" code stays distinct from "L"
# (Lightly smoothed) so a reader can tell low-evidence windows
# (insufficient signal) apart from genuinely-low-compression windows.
BAND_CODE = {
    "Heavily smoothed": "H",
    "Moderately smoothed": "M",
    "Lightly smoothed": "L",
    "Insufficient signal": "-",
}

# Bands considered "hot" — surfaced in the contiguous-run summary.
# Lightly smoothed and Insufficient signal are not hot zones.
HOT_BANDS = {"Heavily smoothed", "Moderately smoothed"}

# Sparkline characters for a one-line ASCII bar chart of the
# compression fraction. Eight levels match the standard unicode
# block characters; the convention pairs with countless prior-art
# sparkline implementations and renders identically across terminals.
SPARK_CHARS = "▁▂▃▄▅▆▇█"

# ---------- Loading the windows block ----------


def load_windows_block(source: Any) -> dict[str, Any]:
    """Accept either a full variance_audit output dict or the
    ``windows`` sub-dict directly. Returns the windows block (with
    ``window_size``, ``stride``, ``n_windows``, ``results``).

    The flexibility matters because the script chains naturally
    after ``variance_audit.py``: a user might save the full report
    to disk and feed the whole thing in, or splice the ``windows``
    block out and feed only that. Either should work.
    """
    if not isinstance(source, dict):
        raise ValueError(
            "input must be a JSON object (got "
            f"{type(source).__name__})"
        )
    if "results" in source and "n_windows" in source:
        return source
    if "windows" in source and isinstance(source["windows"], dict):
        return source["windows"]
    raise ValueError(
        "input does not contain a 'windows' block or 'results' list; "
        "expected variance_audit.py --json output"
    )


def load_input(path: str | None) -> dict[str, Any]:
    """Read either ``--in`` or stdin and return the windows block."""
    if path and path != "-":
        raw = Path(path).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("empty input")
    data = json.loads(raw)
    return load_windows_block(data)


# ---------- Heatmap pieces ----------


def render_sparkline(fractions: list[float | None]) -> str:
    """Sparkline of ``compression_fraction`` per window.

    Maps the [0, max] range across the doc onto the eight-level
    unicode block scale. None / Insufficient-signal windows render
    as a low-tier block (``▁``) rather than blank so the bar count
    matches the window count one-to-one.
    """
    if not fractions:
        return ""
    valid = [f for f in fractions if isinstance(f, (int, float))]
    if not valid:
        return SPARK_CHARS[0] * len(fractions)
    vmax = max(valid)
    if vmax <= 0:
        return SPARK_CHARS[0] * len(fractions)
    out = []
    for f in fractions:
        if not isinstance(f, (int, float)):
            out.append(SPARK_CHARS[0])
            continue
        # Map [0, vmax] onto [0, 7] linearly. Clip at the top in
        # case vmax was somehow miscounted.
        idx = int(round((f / vmax) * (len(SPARK_CHARS) - 1)))
        idx = max(0, min(len(SPARK_CHARS) - 1, idx))
        out.append(SPARK_CHARS[idx])
    return "".join(out)


def render_band_tape(bands: list[str]) -> str:
    """Single-character per-window band codes."""
    return "".join(BAND_CODE.get(b, "?") for b in bands)


@dataclass
class HotZone:
    """Contiguous run of hot windows."""
    start_window: int
    end_window: int
    start_word: int
    end_word: int
    band: str  # the highest band in the run
    bands_in_run: list[str] = field(default_factory=list)
    fractions: list[float] = field(default_factory=list)
    dominant_signals: list[str] = field(default_factory=list)
    raw_signal_counts: dict[str, int] = field(default_factory=dict)
    n_windows: int = 0
    # Source-of-smoothing classification (Release 2). Filled by
    # `_classify_zone_phenomenon`. Possible values:
    #   "syntactic_flattening" — sentence-rhythm signals dominate
    #   "lexical_compression" — diversity / entropy signals dominate
    #   "over_cohesion" — adjacent-cosine signals dominate
    #   "connective_overuse" — connective_density dominates
    #   "mixed_smoothing" — multiple families fire roughly equally
    #   "unclassified" — too few or too sparse signals to classify
    phenomenon: str = "unclassified"
    phenomenon_evidence: list[str] = field(default_factory=list)


def find_hot_zones(windows: list[dict[str, Any]]) -> list[HotZone]:
    """Group consecutive Heavily/Moderately smoothed windows into runs.

    A run breaks on any non-hot band (including Insufficient signal).
    Each run carries the highest band observed in it (Heavy beats
    Moderate), the word-coordinate span, the per-window fractions,
    and the most-frequently-flagged signals across the run.
    """
    if not windows:
        return []
    zones: list[HotZone] = []
    current: HotZone | None = None
    for i, w in enumerate(windows):
        c = w.get("compression") or {}
        band = c.get("band", "unknown")
        is_hot = band in HOT_BANDS
        if is_hot:
            if current is None:
                current = HotZone(
                    start_window=i,
                    end_window=i,
                    start_word=int(w.get("start_word", 0)),
                    end_word=int(w.get("end_word", 0)),
                    band=band,
                    bands_in_run=[band],
                    fractions=[],
                    dominant_signals=[],
                    n_windows=1,
                )
            else:
                current.end_window = i
                current.end_word = int(w.get("end_word", 0))
                current.bands_in_run.append(band)
                current.n_windows += 1
                # Heaviest band wins the run's headline label.
                if (current.band == "Moderately smoothed"
                        and band == "Heavily smoothed"):
                    current.band = "Heavily smoothed"
            frac = c.get("compression_fraction")
            if isinstance(frac, (int, float)):
                current.fractions.append(float(frac))
            for sig in c.get("flagged_signals") or []:
                current.dominant_signals.append(str(sig))
        else:
            if current is not None:
                _finalize_zone(current)
                zones.append(current)
                current = None
    if current is not None:
        _finalize_zone(current)
        zones.append(current)
    return zones


def _finalize_zone(zone: HotZone) -> None:
    """Reduce dominant_signals to the top-3 most-frequent flagged
    signals AND classify the zone's dominant phenomenon (Release 2).

    The phenomenon classifier groups per-window flagged signals into
    families (syntactic-rhythm, lexical-compression, over-cohesion,
    connective-overuse) and labels the zone by which family
    dominates the firing pattern. When two or more families fire
    roughly equally, the zone is labeled `mixed_smoothing` rather
    than committing to a single cause.

    The output is what `source-of-smoothing localization` from the
    trustworthiness expansion calls for: not just "where the band
    fired" (which the heatmap already shows) but "what kind of
    smoothing is happening here." Gives a writer something
    revisable: "hot zone 4 is over-cohesion-driven; rhythm
    rebalancing won't help, restoring local surprise will."
    """
    counts: dict[str, int] = {}
    for s in zone.dominant_signals:
        counts[s] = counts.get(s, 0) + 1
    zone.raw_signal_counts = dict(counts)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    zone.dominant_signals = [
        f"{name} ({count}/{zone.n_windows})"
        for name, count in ranked[:3]
    ]
    phenomenon, evidence = _classify_zone_phenomenon(counts, zone.n_windows)
    zone.phenomenon = phenomenon
    zone.phenomenon_evidence = evidence


# Signal-family taxonomy (Release 2). Maps the signal names from
# `COMPRESSION_HEURISTICS` (in `variance_audit.py`) to the
# phenomenon family they evidence. Signals not in this map are
# folded into an "other" bucket.
#
# Caveat: the taxonomy below assumes the registry's declared
# polarity. Per the 1.27.0 polarity-inversion finding, five
# lexical-diversity signals (mtld, mattr, shannon_entropy, yules_k,
# adjacent_cosine_mean) invert against ESL student writing — when
# the band fires on those signals against an ESL comparator, the
# `lexical_compression` label may be misleading. The confounder
# audit (roadmap, paired-release Release 3) is the right surface
# to disambiguate. For now the classifier reports what the signals
# say at face value and the claim-license block carries the caveat.
_SIGNAL_FAMILIES: dict[str, str] = {
    # Sentence rhythm
    "burstiness_B": "syntactic_flattening",
    "sentence_length_sd": "syntactic_flattening",
    "fkgl_sd": "syntactic_flattening",
    "mdd_sd": "syntactic_flattening",
    # Lexical diversity / entropy
    "mtld": "lexical_compression",
    "mattr": "lexical_compression",
    "shannon_entropy": "lexical_compression",
    "yules_k": "lexical_compression",
    # Cohesion
    "adjacent_cosine_mean": "over_cohesion",
    "adjacent_cosine_sd": "over_cohesion",
    # Connective scaffolding
    "connective_density": "connective_overuse",
}


def _classify_zone_phenomenon(
    counts: dict[str, int], n_windows: int,
) -> tuple[str, list[str]]:
    """Return (phenomenon_label, evidence_list) for a zone.

    `evidence_list` is human-readable strings naming each family
    contribution: e.g. ``["syntactic_flattening: burstiness_B (3/5),
    sentence_length_sd (2/5)", ...]``.
    """
    if not counts:
        return "unclassified", []

    family_signal_counts: dict[str, dict[str, int]] = {}
    other: dict[str, int] = {}
    for sig, c in counts.items():
        family = _SIGNAL_FAMILIES.get(sig)
        if family is None:
            other[sig] = c
            continue
        family_signal_counts.setdefault(family, {})[sig] = c

    family_totals = {
        f: sum(d.values()) for f, d in family_signal_counts.items()
    }
    total = sum(family_totals.values()) + sum(other.values())
    if total == 0 or n_windows == 0:
        return "unclassified", []

    if not family_totals:
        # Only 'other' signals fired — rare; report unclassified
        # rather than mislabel.
        evidence = [
            f"other: {', '.join(f'{s} ({c}/{n_windows})' for s, c in other.items())}"
        ]
        return "unclassified", evidence

    ranked = sorted(family_totals.items(), key=lambda kv: -kv[1])
    leader, leader_count = ranked[0]
    leader_share = leader_count / total

    # Phenomenon label rule:
    #   - Single family dominates (≥ 0.6 share) → that family.
    #   - Otherwise → mixed_smoothing.
    if leader_share >= 0.6:
        phenomenon = leader
    else:
        phenomenon = "mixed_smoothing"

    evidence: list[str] = []
    for family, sigs in sorted(
        family_signal_counts.items(),
        key=lambda kv: -family_totals[kv[0]],
    ):
        sig_strs = ", ".join(
            f"{s} ({c}/{n_windows})"
            for s, c in sorted(sigs.items(), key=lambda kv: -kv[1])
        )
        evidence.append(f"{family}: {sig_strs}")
    if other:
        sig_strs = ", ".join(
            f"{s} ({c}/{n_windows})"
            for s, c in sorted(other.items(), key=lambda kv: -kv[1])
        )
        evidence.append(f"other: {sig_strs}")

    return phenomenon, evidence


def collect_signal_grid(
    windows: list[dict[str, Any]],
) -> tuple[list[str], list[list[bool]]]:
    """Build a per-signal × per-window fired/not-fired matrix.

    Returns ``(signal_names, grid)`` where ``signal_names`` is a
    list of every signal that fired in at least one window (sorted
    by total fire-count descending) and ``grid`` is a list of rows
    parallel to ``signal_names``, each row a list of booleans
    parallel to ``windows``.
    """
    signal_counts: dict[str, int] = {}
    per_window_signals: list[set[str]] = []
    for w in windows:
        c = w.get("compression") or {}
        sigs = set(c.get("flagged_signals") or [])
        per_window_signals.append(sigs)
        for s in sigs:
            signal_counts[s] = signal_counts.get(s, 0) + 1
    signals = sorted(
        signal_counts.keys(),
        key=lambda s: (-signal_counts[s], s),
    )
    grid = [
        [(s in window_sigs) for window_sigs in per_window_signals]
        for s in signals
    ]
    return signals, grid


# ---------- Markdown rendering ----------


def render_signal_grid_table(
    signals: list[str],
    grid: list[list[bool]],
    n_windows: int,
) -> list[str]:
    """A signal-by-window table with `✓` for fired, `·` for not."""
    if not signals:
        return ["_(No signals fired in any window.)_"]
    # Column header: window indices, 1-based.
    header_cells = ["Signal"] + [str(i + 1) for i in range(n_windows)]
    sep_cells = ["---"] + ["---"] * n_windows
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "|" + "|".join(sep_cells) + "|",
    ]
    for row_signals, fire_row in zip(signals, grid):
        cells = [f"`{row_signals}`"] + [
            "✓" if fired else "·" for fired in fire_row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


_PHENOMENON_LABELS = {
    "syntactic_flattening": "syntactic flattening",
    "lexical_compression": "lexical compression",
    "over_cohesion": "over-cohesion",
    "connective_overuse": "connective overuse",
    "mixed_smoothing": "mixed smoothing",
    "unclassified": "unclassified",
}


def render_hot_zones(zones: list[HotZone]) -> list[str]:
    """Bullet list of contiguous hot runs in word coordinates,
    annotated with the dominant phenomenon (Release 2)."""
    if not zones:
        return ["_(No hot zones — no contiguous Moderately or "
                "Heavily smoothed runs.)_"]
    out: list[str] = []
    for z in zones:
        if z.fractions:
            frac_lo = min(z.fractions)
            frac_hi = max(z.fractions)
            frac_str = (
                f"fraction {frac_lo:.2f}–{frac_hi:.2f}"
                if frac_lo != frac_hi else f"fraction {frac_lo:.2f}"
            )
        else:
            frac_str = "fraction n/a"
        sig_str = (
            "; dominant signals: " + ", ".join(z.dominant_signals)
            if z.dominant_signals else ""
        )
        if z.start_window == z.end_window:
            window_span = f"window {z.start_window + 1}"
        else:
            window_span = (
                f"windows {z.start_window + 1}–{z.end_window + 1}"
            )
        phenomenon_label = _PHENOMENON_LABELS.get(
            z.phenomenon, z.phenomenon,
        )
        phenomenon_str = (
            f"; phenomenon: **{phenomenon_label}**"
            if z.phenomenon != "unclassified" else ""
        )
        out.append(
            f"- **{z.band}** at words {z.start_word:,}–{z.end_word:,} "
            f"({window_span}, {z.n_windows} window"
            f"{'s' if z.n_windows != 1 else ''}, {frac_str})"
            f"{phenomenon_str}{sig_str}"
        )
    return out


def render_window_table(windows: list[dict[str, Any]]) -> list[str]:
    """A compact table with one row per window."""
    lines = [
        "| # | start_word | end_word | n_words | band | fraction | n_flagged |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
    for i, w in enumerate(windows):
        c = w.get("compression") or {}
        frac = c.get("compression_fraction")
        frac_str = (
            f"{frac:.3f}" if isinstance(frac, (int, float)) else "n/a"
        )
        lines.append(
            f"| {i + 1} | "
            f"{w.get('start_word', 0):,} | "
            f"{w.get('end_word', 0):,} | "
            f"{w.get('n_words', 0):,} | "
            f"{c.get('band', 'unknown')} | "
            f"{frac_str} | "
            f"{c.get('n_flagged', 0)} |"
        )
    return lines


def render_band_legend() -> list[str]:
    """Explain the H/M/L/- band codes for the compact band tape."""
    return [
        "Band tape legend: "
        + ", ".join(
            f"`{code}` = {name}"
            for name, code in BAND_CODE.items()
        )
        + ".",
    ]


def render_report(
    windows_block: dict[str, Any],
    *,
    source_label: str | None = None,
) -> str:
    """Top-level renderer. Composes the markdown report end-to-end.

    Layout:
        # Sliding-window compression heatmap
        Source: ...
        Window size / stride / count: ...
        ## Compression-fraction sparkline
        ## Band tape
        ## Hot zones
        ## Per-signal × per-window grid
        ## Window detail
        ## Claim license
    """
    windows: list[dict[str, Any]] = list(windows_block.get("results") or [])
    n_windows = int(windows_block.get("n_windows") or len(windows))
    window_size = windows_block.get("window_size")
    stride = windows_block.get("stride")

    bands = [
        (w.get("compression") or {}).get("band", "Insufficient signal")
        for w in windows
    ]
    fractions = [
        (w.get("compression") or {}).get("compression_fraction")
        for w in windows
    ]

    sparkline = render_sparkline(fractions)
    band_tape = render_band_tape(bands)
    hot_zones = find_hot_zones(windows)
    signals, grid = collect_signal_grid(windows)

    # Band-distribution histogram (counts).
    band_dist: dict[str, int] = {}
    for b in bands:
        band_dist[b] = band_dist.get(b, 0) + 1
    band_dist_str = ", ".join(
        f"{name}={count}"
        for name, count in sorted(
            band_dist.items(), key=lambda kv: -kv[1]
        )
    )

    lines: list[str] = []
    lines.append("# Sliding-window compression heatmap")
    lines.append("")
    if source_label:
        lines.append(f"**Source:** `{source_label}`")
    lines.append(
        f"**Windows:** {n_windows} "
        f"(size={window_size}, stride={stride})"
    )
    lines.append(f"**Band distribution:** {band_dist_str or '(empty)'}")
    lines.append("")

    lines.append("## Compression-fraction sparkline")
    lines.append("")
    lines.append("```")
    lines.append(sparkline if sparkline else "(no windows)")
    lines.append("```")
    lines.append(
        "Each block represents one window's `compression_fraction` "
        "(fraction of available signal weight that fired). Taller "
        "blocks = more compression evidence. Bar height is scaled "
        "to the per-document maximum, not an absolute scale."
    )
    lines.append("")

    lines.append("## Band tape")
    lines.append("")
    lines.append("```")
    lines.append(band_tape if band_tape else "(no windows)")
    lines.append("```")
    lines.extend(render_band_legend())
    lines.append("")

    lines.append("## Hot zones")
    lines.append("")
    lines.append(
        "Contiguous runs of Heavily or Moderately smoothed windows, "
        "in word-position coordinates. Use these to localize where "
        "in the document the band call is concentrated."
    )
    lines.append("")
    lines.extend(render_hot_zones(hot_zones))
    lines.append("")

    lines.append("## Per-signal × per-window grid")
    lines.append("")
    lines.append(
        "Rows are individual signals from `COMPRESSION_HEURISTICS`; "
        "columns are window indices. `✓` = the signal fired in that "
        "window; `·` = it did not. Signals not in this table did "
        "not fire in any window."
    )
    lines.append("")
    lines.extend(render_signal_grid_table(signals, grid, n_windows))
    lines.append("")

    lines.append("## Window detail")
    lines.append("")
    lines.extend(render_window_table(windows))
    lines.append("")

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A localization map of where the smoothing-diagnosis "
            "band classification fires across the document, in "
            "word-position coordinates, with per-signal granularity."
        ),
        does_not_license=(
            "A smoothing diagnosis on its own — this is a "
            "visualization of the band call, not a verdict on "
            "AI provenance. Window z-scores at small N are noisy "
            "by construction, and band classifications below 200 "
            "words carry the same length-floor caveat as the "
            "whole-document call. Pair with `variance_audit.py`'s "
            "headline output and a baseline-comparison run for "
            "diagnostic claims."
        ),
        comparison_set={
            "n_windows": n_windows,
            "window_size_words": window_size,
            "stride_words": stride,
            "band_distribution": band_dist_str or "(empty)",
        },
        additional_caveats=[
            "Heatmap colors / sparkline heights are document-relative, "
            "not absolute — a tall bar in a low-compression document "
            "is not the same as a tall bar in a high-compression one.",
            "Hot-zone runs are at window granularity; a single "
            "compressed paragraph that falls between window edges "
            "may smear across two windows. Re-run with smaller "
            "`--window-stride` to localize finer.",
        ],
        references=[
            "ROADMAP.md cathedral upgrade #5 (sliding-window localization)",
            "variance_audit.py classify_compression() — band thresholds",
            "scripts/calibration/PROVENANCE.md — per-signal calibration ledger",
        ],
    ).render_block()
    lines.append(license_block.rstrip())
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------- JSON output ----------


def render_json(
    windows_block: dict[str, Any],
    *,
    target_path: Any = None,
) -> dict[str, Any]:
    """Schema_version 1.0 envelope representation of the heatmap.

    Carries the same content as the markdown report but in machine-
    readable form (under ``results``). The optional ``target_path``
    populates ``envelope.target.path``; callers from the CLI pass
    the original variance_audit input path.
    """
    windows: list[dict[str, Any]] = list(windows_block.get("results") or [])
    bands = [
        (w.get("compression") or {}).get("band", "Insufficient signal")
        for w in windows
    ]
    fractions = [
        (w.get("compression") or {}).get("compression_fraction")
        for w in windows
    ]
    hot_zones = find_hot_zones(windows)
    signals, grid = collect_signal_grid(windows)
    band_dist: dict[str, int] = {}
    for b in bands:
        band_dist[b] = band_dist.get(b, 0) + 1

    results = {
        "n_windows": len(windows),
        "window_size": windows_block.get("window_size"),
        "stride": windows_block.get("stride"),
        "bands": bands,
        "compression_fractions": fractions,
        "band_distribution": band_dist,
        "sparkline": render_sparkline(fractions),
        "band_tape": render_band_tape(bands),
        "hot_zones": [
            {
                "start_window": z.start_window,
                "end_window": z.end_window,
                "start_word": z.start_word,
                "end_word": z.end_word,
                "band": z.band,
                "n_windows": z.n_windows,
                "fraction_min": min(z.fractions) if z.fractions else None,
                "fraction_max": max(z.fractions) if z.fractions else None,
                "dominant_signals": z.dominant_signals,
                "phenomenon": z.phenomenon,
                "phenomenon_evidence": z.phenomenon_evidence,
            }
            for z in hot_zones
        ],
        "signal_grid": {
            "signals": signals,
            "fired": grid,
        },
    }
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results,
        claim_license=ClaimLicense(
            task_surface=TASK_SURFACE,
            licenses=(
                "Sliding-window heatmap representation of "
                "variance_audit output. Reports per-window "
                "compression-band labels, fraction series, hot-zone "
                "boundaries with dominant signals, and a signal-grid "
                "matrix (which signals fired in which windows)."
            ),
            does_not_license=(
                "An authorship verdict. The heatmap reports where "
                "compression signals fire across a document; it does "
                "not license claims about who wrote the document or "
                "whether AI was involved. Hot-zone localization is "
                "evidence for further investigation, not a per-zone "
                "AI-vs-human determination."
            ),
            comparison_set={
                "n_windows": len(windows),
                "window_size": windows_block.get("window_size"),
                "stride": windows_block.get("stride"),
                "n_hot_zones": len(hot_zones),
            },
            additional_caveats=[
                "Heatmap is a visualization of variance_audit "
                "sliding-window output; it inherits variance_audit's "
                "heuristic-tier calibration. Treat band labels as "
                "operator cues, not load-bearing verdicts.",
            ],
        ),
    )


# ---------- Privacy guard ----------


def _is_under_private_root(path: Path) -> bool:
    """Path lives under any directory containing the
    ``ai-prose-baselines-private`` marker.

    Mirrors the convention from ``voice_profile.py`` and
    ``general_imposters.py``: voiceprint-shaped output (and band-
    localization output is voiceprint-adjacent) must live under
    a private root unless the caller explicitly opts out with
    ``--allow-public-output``.
    """
    parts = path.resolve().parts
    return "ai-prose-baselines-private" in parts


# ---------- CLI ----------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sliding_window_heatmap.py",
        description=(
            "Render variance_audit.py sliding-window output as a "
            "markdown heatmap localizing band classifications "
            "across word positions."
        ),
    )
    p.add_argument(
        "--in", dest="input_path", default="-",
        help=(
            "Path to a JSON file (variance_audit.py --json output, "
            "or the windows sub-dict). Use `-` for stdin "
            "(default: stdin)."
        ),
    )
    p.add_argument(
        "--out", dest="output_path", default=None,
        help=(
            "Write the markdown report to this path. If omitted, "
            "prints to stdout."
        ),
    )
    p.add_argument(
        "--json-out", dest="json_output_path", default=None,
        help=(
            "Also emit a JSON sidecar with the structured heatmap "
            "data. Useful for downstream tools."
        ),
    )
    p.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Bypass the private-root requirement on `--out` and "
            "`--json-out`. Use only when the document being audited "
            "is itself public (e.g., a published essay) and the "
            "heatmap will be shared."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        windows_block = load_input(args.input_path)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"  failed to load input: {e}\n")
        return 2

    source_label = (
        args.input_path
        if args.input_path and args.input_path != "-"
        else None
    )
    report = render_report(windows_block, source_label=source_label)

    if args.output_path:
        out_path = Path(args.output_path)
        if not args.allow_public_output and not _is_under_private_root(out_path):
            sys.stderr.write(
                "  refusing to write heatmap to non-private path "
                f"{out_path}; either move under "
                "ai-prose-baselines-private/ or pass "
                "--allow-public-output explicitly\n"
            )
            return 3
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        sys.stderr.write(f"  wrote heatmap report → {out_path}\n")
    else:
        sys.stdout.write(report)

    if args.json_output_path:
        jpath = Path(args.json_output_path)
        if not args.allow_public_output and not _is_under_private_root(jpath):
            sys.stderr.write(
                "  refusing to write heatmap JSON to non-private "
                f"path {jpath}; either move under "
                "ai-prose-baselines-private/ or pass "
                "--allow-public-output explicitly\n"
            )
            return 3
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(
            json.dumps(
                render_json(
                    windows_block,
                    target_path=args.input_path,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        sys.stderr.write(f"  wrote heatmap JSON → {jpath}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
