#!/usr/bin/env python3
r"""
manuscript_audit.py
Cross-chapter aggregate of Layer A distributional diagnostics.

Runs variance_audit logic on every chapter of a manuscript (or every file
in a chapter directory) and produces a dashboard showing which chapters
deviate from baseline and on which signals. Surfaces manuscript-wide
patterns and outlier chapters that single-chapter audits miss.

Usage:
    python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir BASELINE_DIR
    python3 manuscript_audit.py --chapter-dir CHAPTERS/ --baseline-dir BASELINE_DIR
    python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir BASELINE_DIR --json
    python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir BASELINE_DIR \\
        --chapter-pattern '^#+\s*Chapter\s*(\d+)'
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore


def _chapter_text_hash(text: str) -> str:
    """SHA-256 of the chapter text. Used as part of the resume key
    so editing a chapter under the same label invalidates the
    cached audit (codex P2 on PR #70: label-only key meant edits
    were served from stale cache silently)."""
    return f"sha256:{hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()}"

# Import variance_audit machinery from the same directory.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from variance_audit import (  # type: ignore
    audit_text,
    audit_baseline,
    classify_compression,
)
from preprocessing import aggregate_preprocessing_metadata, available_rule_names, strip_non_prose


# See variance_audit.TASK_SURFACE for the contract. This script runs the
# Layer A diagnostic across every chapter of a manuscript; the output is
# the same prose-quality diagnosis at a different scope.
TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "manuscript_audit"
SCRIPT_VERSION = "1.0"


def split_manuscript(text: str, pattern: str) -> list[dict[str, Any]]:
    """Split a manuscript on a chapter-marker regex. Returns list of dicts
    with keys 'label' (e.g. 'Chapter 4') and 'text'."""
    rx = re.compile(pattern, re.M)
    matches = list(rx.finditer(text))
    if not matches:
        return [{"label": "Whole text", "text": text}]
    chapters = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # Pull the full header line for label
        line_end = text.find("\n", start)
        header = text[start: line_end if line_end != -1 else end].strip().lstrip("#").strip()
        chapters.append({
            "label": header[:80],
            "text": text[start:end],
            "start_byte": start,
            "end_byte": end,
        })
    return chapters


def load_chapter_dir(directory: str) -> list[dict[str, Any]]:
    """Load every .txt or .md file from a directory as a chapter."""
    paths = sorted(Path(directory).glob("*.txt")) + sorted(Path(directory).glob("*.md"))
    chapters = []
    for p in paths:
        chapters.append({
            "label": p.name,
            "text": p.read_text(encoding="utf-8", errors="ignore"),
            "path": str(p),
        })
    return chapters


# Signals to track in the dashboard. Each maps a display name to the
# audit-result path; direction tells whether negative z = compression
# (lower is bad) or positive z = compression (higher is bad).
DASHBOARD_SIGNALS: list[tuple[str, tuple[str, ...], str]] = [
    ("burst_B",       ("tier1", "sentence_length", "burstiness_B"),       "lt"),
    ("sent_sd",       ("tier1", "sentence_length", "sd"),                 "lt"),
    ("MATTR",         ("tier1", "mattr", "value"),                        "lt"),
    ("MTLD",          ("tier1", "mtld",),                                 "lt"),
    ("Yule_K",        ("tier1", "yules_k",),                              "gt"),
    ("entropy",       ("tier1", "shannon_entropy_bits",),                 "lt"),
    ("FKGL_sd",       ("tier1", "fkgl", "sd"),                            "lt"),
    ("conn_dens",     ("tier1", "connective_density", "per_1000_tokens"), "gt"),
    ("fw_ratio",      ("tier1", "function_words", "function_word_ratio"), "gt"),
    ("MDD_sd",        ("tier2", "mdd", "sd"),                             "lt"),
    ("adj_cos_mean",  ("tier3", "adjacent_cosine", "mean"),               "gt"),
    ("adj_cos_sd",    ("tier3", "adjacent_cosine", "sd"),                 "lt"),
]


def get_path(d: Any, path: tuple[str, ...]) -> float | None:
    """Walk dict by tuple path; return None if any link missing."""
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
        if cur is None:
            return None
    if isinstance(cur, (int, float)):
        return float(cur)
    return None


def z_score(value: float, mean: float, sd: float) -> float | None:
    if sd == 0:
        return None
    return (value - mean) / sd


def aggregate_baseline_stats(baseline_audits: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """For each dashboard signal, compute (mean, sd) across baseline files."""
    out: dict[str, dict[str, float]] = {}
    for name, path, _direction in DASHBOARD_SIGNALS:
        vals = []
        for entry in baseline_audits:
            v = get_path(entry.get("audit", {}), path)
            if v is not None:
                vals.append(v)
        if len(vals) >= 2:
            out[name] = {
                "mean": statistics.mean(vals),
                "sd": statistics.stdev(vals),
                "n": len(vals),
            }
        elif len(vals) == 1:
            out[name] = {"mean": vals[0], "sd": 0.0, "n": 1}
    return out


def _save_chapter_audit_cache(
    path: Path,
    chapter_audits: list[dict[str, Any]],
    chapter_preprocessing: dict[str, dict[str, Any]],
    *,
    n_chapters_total: int,
    status: str,
    do_tier2: bool,
    do_tier3: bool,
    allow_non_prose: bool,
    strip_rules: Any,
    strip_aggressive: bool,
) -> None:
    """Atomic write of the per-chapter audit cache. ``status``
    flips from ``"in_progress"`` (per-flush) to ``"complete"`` on
    the final write after every chapter has been audited.

    Compat fields (codex P2 on PR #70): scoring_meta now records
    every preprocessing arg that affects ``audit_text``'s output,
    and each chapter_audit entry carries its own ``text_hash``.
    On resume, the loader matches cached entries by
    ``(label, text_hash)`` AND verifies preprocessing args
    match — silent stale-cache reuse after a chapter edit or a
    strip-rules change is now impossible.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "status": status,
        "tool": "manuscript_audit",
        "tool_version": "1.70.0",
        "scoring_meta": {
            "n_chapters_total": n_chapters_total,
            "n_chapters_audited": len(chapter_audits),
            # Tier flags + preprocessing args (P2 fix: was just
            # tier flags + strip_rules; now allow_non_prose and
            # strip_aggressive are checked too).
            "do_tier2": do_tier2,
            "do_tier3": do_tier3,
            "allow_non_prose": allow_non_prose,
            "strip_rules": strip_rules,
            "strip_aggressive": strip_aggressive,
            "scored_at": _dt.datetime.now(
                _dt.timezone.utc,
            ).isoformat(),
        },
        "chapter_audits": chapter_audits,
        "chapter_preprocessing": chapter_preprocessing,
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)
    tmp.replace(path)


def _chapter_cache_compat_reason(
    cache_meta: dict[str, Any],
    *,
    do_tier2: bool,
    do_tier3: bool,
    allow_non_prose: bool,
    strip_rules: Any,
    strip_aggressive: bool,
) -> str | None:
    """Return ``None`` when scoring_meta is compatible with the
    current run, or a one-line reason string otherwise. Tolerates
    missing fields (back-compat with caches written before this
    fix); refuses on present-but-different."""
    if bool(cache_meta.get("do_tier2")) != do_tier2:
        return "do_tier2 differs"
    if bool(cache_meta.get("do_tier3")) != do_tier3:
        return "do_tier3 differs"
    prior_allow = cache_meta.get("allow_non_prose")
    if prior_allow is not None and bool(prior_allow) != allow_non_prose:
        return "allow_non_prose differs"
    prior_strip = cache_meta.get("strip_rules")
    if prior_strip is not None and prior_strip != strip_rules:
        return f"strip_rules differs (prior={prior_strip!r}, current={strip_rules!r})"
    prior_aggr = cache_meta.get("strip_aggressive")
    if prior_aggr is not None and bool(prior_aggr) != strip_aggressive:
        return "strip_aggressive differs"
    return None


def audit_manuscript(
    chapters: list[dict[str, Any]],
    baseline_dir: str | None,
    *,
    do_tier2: bool = True,
    do_tier3: bool = True,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
    cache_path: Path | None = None,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    """Run audit_text on each chapter; aggregate baseline; compute z-scores.

    MEASURE + SAVE PROGRESS (1.70.0+): logs ``[Xs] chapter N/M
    audited: 'label' (Y words)`` to stderr after every chapter
    completes. When ``cache_path`` is set, also writes a partial
    cache after every chapter with ``status: "in_progress"``, so a
    crash mid-manuscript loses at most one chapter's worth of
    audit work (which on a long-tier3 book can be minutes per
    chapter). On the next run with the same ``cache_path``, any
    chapter whose ``label`` matches a cached audit is loaded from
    cache and skipped. Final write flips status to ``"complete"``.
    """
    chapter_audits: list[dict[str, Any]] = []
    chapter_preprocessing: dict[str, dict[str, Any]] = {}
    # Resume keyed by (label, text_hash) — codex P2 on PR #70:
    # label-only key meant editing a chapter's text under the
    # same label silently reused the stale audit.
    cached_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    cached_preprocessing: dict[str, dict[str, Any]] = {}

    # ----- Resume from prior partial / complete cache (1.70.0+).
    if cache_path is not None and cache_path.exists() and not refresh_cache:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cache_status = cached.get("status", "complete")
            cache_meta = cached.get("scoring_meta") or {}
            incompat_reason = _chapter_cache_compat_reason(
                cache_meta,
                do_tier2=do_tier2, do_tier3=do_tier3,
                allow_non_prose=allow_non_prose,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
            )
            if (
                incompat_reason is None
                and cache_status in ("in_progress", "complete")
            ):
                # Index cached entries by (label, text_hash). If a
                # prior entry was written without a text_hash field
                # (pre-fix cache), accept it under a wildcard hash
                # so back-compat holds — the operator opts into
                # stricter checking by re-running once to regenerate
                # the cache with text hashes embedded.
                for a in (cached.get("chapter_audits") or []):
                    if not isinstance(a.get("label"), str):
                        continue
                    text_hash = a.get("text_hash")
                    key = (
                        a["label"],
                        text_hash if isinstance(text_hash, str)
                        else "__legacy_no_hash__",
                    )
                    cached_by_key[key] = a
                cached_preprocessing = (
                    cached.get("chapter_preprocessing") or {}
                )
                sys.stderr.write(
                    f"Manuscript audit: cache hit at {cache_path} "
                    f"(status={cache_status!r}); "
                    f"{len(cached_by_key)} chapter audit(s) "
                    f"available for reuse (matched by label + "
                    f"text_hash).\n"
                )
            elif incompat_reason is not None:
                sys.stderr.write(
                    f"Manuscript audit: cache at {cache_path} is "
                    f"incompatible ({incompat_reason}); discarding. "
                    f"Pass --refresh-chapter-cache to suppress.\n"
                )
            else:
                sys.stderr.write(
                    f"Manuscript audit: cache at {cache_path} has "
                    f"unknown status ({cache_status!r}); discarding.\n"
                )
        except (json.JSONDecodeError, OSError) as exc:
            sys.stderr.write(
                f"Manuscript audit: cache at {cache_path} is "
                f"unreadable ({exc}); discarding.\n"
            )

    n_total = len(chapters)
    audit_t0 = _dt.datetime.now()
    n_fresh = 0
    for i, ch in enumerate(chapters):
        label = ch.get("label", f"chapter_{i}")
        text_hash = _chapter_text_hash(ch.get("text", ""))
        # First try the strict (label, text_hash) match. Fall back
        # to the legacy-no-hash key only if strict miss AND a
        # legacy entry exists — same compat tolerance as elsewhere.
        cache_key = (label, text_hash)
        legacy_key = (label, "__legacy_no_hash__")
        cached_entry = cached_by_key.get(cache_key)
        if cached_entry is None:
            cached_entry = cached_by_key.get(legacy_key)
        if cached_entry is not None:
            # Resume: re-use the prior audit + preprocessing entries.
            chapter_audits.append(cached_entry)
            if label in cached_preprocessing:
                chapter_preprocessing[label] = cached_preprocessing[label]
            sys.stderr.write(
                f"  chapter {i + 1}/{n_total}: '{label}' loaded "
                f"from cache.\n"
            )
            continue
        a = audit_text(
            ch["text"],
            do_tier2=do_tier2,
            do_tier3=do_tier3,
            allow_non_prose=allow_non_prose,
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
        )
        chapter_preprocessing[ch["label"]] = a.get("preprocessing", {})
        comp = classify_compression(a)
        chapter_audits.append({
            "label": ch["label"],
            "text_hash": text_hash,  # P2 fix: persisted for resume
            "n_words": a.get("summary", {}).get("n_words", 0),
            "audit": a,
            "compression": comp,
        })
        n_fresh += 1
        elapsed = (_dt.datetime.now() - audit_t0).total_seconds()
        n_words = a.get("summary", {}).get("n_words", 0)
        sys.stderr.write(
            f"  [{elapsed:6.1f}s] chapter {i + 1}/{n_total}: "
            f"'{label}' audited ({n_words} words).\n"
        )
        if cache_path is not None:
            try:
                _save_chapter_audit_cache(
                    cache_path, chapter_audits, chapter_preprocessing,
                    n_chapters_total=n_total,
                    status="in_progress",
                    do_tier2=do_tier2, do_tier3=do_tier3,
                    allow_non_prose=allow_non_prose,
                    strip_rules=strip_rules,
                    strip_aggressive=strip_aggressive,
                )
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(
                    f"  WARNING: chapter-cache flush to "
                    f"{cache_path} failed: {type(exc).__name__}: "
                    f"{exc}. Continuing.\n"
                )

    baseline_stats: dict[str, dict[str, float]] = {}
    n_baseline_files = 0
    baseline_preprocessing: dict[str, Any] | None = None
    if baseline_dir:
        baseline_block = audit_baseline(
            baseline_dir,
            do_tier2=do_tier2,
            do_tier3=do_tier3,
            allow_non_prose=allow_non_prose,
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
        )
        n_baseline_files = baseline_block.get("n_files", 0)
        baseline_stats = aggregate_baseline_stats(baseline_block.get("audits", []))
        baseline_preprocessing = baseline_block.get("preprocessing")

    # Per-chapter z-scores
    for ch in chapter_audits:
        z = {}
        for name, path, _direction in DASHBOARD_SIGNALS:
            val = get_path(ch["audit"], path)
            if val is None or name not in baseline_stats:
                continue
            stats = baseline_stats[name]
            if stats["sd"] == 0 or stats["n"] < 2:
                continue
            z[name] = {
                "value": val,
                "z": (val - stats["mean"]) / stats["sd"],
                "baseline_mean": stats["mean"],
                "baseline_sd": stats["sd"],
            }
        ch["z_scores"] = z

    # Final cache write: status=complete + z_scores attached. The
    # cache survives the run as a full artifact future operators
    # can resume from (or re-use directly for re-rendering at
    # different baseline-dirs).
    if cache_path is not None and n_fresh > 0:
        try:
            _save_chapter_audit_cache(
                cache_path, chapter_audits, chapter_preprocessing,
                n_chapters_total=n_total,
                status="complete",
                do_tier2=do_tier2, do_tier3=do_tier3,
                allow_non_prose=allow_non_prose,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
            )
            sys.stderr.write(
                f"Manuscript audit: cache written to {cache_path} "
                f"({len(chapter_audits)} chapter(s), "
                f"status=complete; {n_fresh} freshly audited, "
                f"{len(chapter_audits) - n_fresh} reused).\n"
            )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"WARNING: final chapter-cache write to "
                f"{cache_path} failed ({type(exc).__name__}: "
                f"{exc}).\n"
            )

    return {
        "task_surface": TASK_SURFACE,
        "preprocessing": {
            "chapters": aggregate_preprocessing_metadata(
                chapter_preprocessing,
                rules_active=list(
                    next(iter(chapter_preprocessing.values()), {}).get("rules_active") or []
                ),
                applied=bool(
                    next(iter(chapter_preprocessing.values()), {}).get("applied", True)
                ),
                opt_out=bool(
                    next(iter(chapter_preprocessing.values()), {}).get("opt_out", False)
                ),
            ),
            "baseline": baseline_preprocessing,
        },
        "n_chapters": len(chapter_audits),
        "n_baseline_files": n_baseline_files,
        "chapters": chapter_audits,
        "baseline_stats": baseline_stats,
    }


def manuscript_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Compute manuscript-wide observations: which signals deviate consistently,
    which chapters are outliers."""
    chapters = result["chapters"]
    chapter_count = len(chapters)
    if chapter_count == 0:
        return {"signal_summary": {}, "outliers": []}

    # For each signal: how many chapters had |z| > 1.0 in the compression direction?
    signal_summary: dict[str, dict[str, Any]] = {}
    for name, _path, direction in DASHBOARD_SIGNALS:
        compressed_count = 0
        z_values = []
        for ch in chapters:
            if name not in ch.get("z_scores", {}):
                continue
            z = ch["z_scores"][name]["z"]
            z_values.append(z)
            # Compression-direction check: lt means lower-than-baseline = compressed
            if direction == "lt" and z < -1.0:
                compressed_count += 1
            elif direction == "gt" and z > 1.0:
                compressed_count += 1
        if z_values:
            signal_summary[name] = {
                "compressed_chapters": compressed_count,
                "total_chapters": len(z_values),
                "fraction": compressed_count / len(z_values),
                "mean_z": statistics.mean(z_values),
                "max_abs_z": max(abs(z) for z in z_values),
            }

    # Outlier chapters: count number of |z| > 1.5 signals per chapter
    outliers = []
    for ch in chapters:
        flagged = []
        z_count = 0
        for name, _path, direction in DASHBOARD_SIGNALS:
            if name not in ch.get("z_scores", {}):
                continue
            z = ch["z_scores"][name]["z"]
            z_count += 1
            if direction == "lt" and z < -1.5:
                flagged.append((name, z, "compressed"))
            elif direction == "gt" and z > 1.5:
                flagged.append((name, z, "compressed"))
            elif direction == "lt" and z > 1.5:
                flagged.append((name, z, "elevated"))
            elif direction == "gt" and z < -1.5:
                flagged.append((name, z, "elevated"))
        outliers.append({
            "label": ch["label"],
            "n_words": ch["n_words"],
            "n_signals_evaluated": z_count,
            "flagged": flagged,
            "flag_count": len(flagged),
        })
    outliers.sort(key=lambda x: x["flag_count"], reverse=True)

    return {
        "signal_summary": signal_summary,
        "outliers": outliers,
    }


# ---------- Output formatting ----------

def fmt_z(z: float | None) -> str:
    if z is None:
        return "  --  "
    s = f"{z:+5.2f}"
    return s


def render_dashboard(result: dict[str, Any]) -> str:
    """Markdown dashboard: rows are chapters, columns are signals."""
    chapters = result["chapters"]
    if not chapters:
        return "No chapters found."

    lines = []
    lines.append("# Manuscript Variance Audit")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append(f"**Chapters analyzed:** {len(chapters)}")
    if result["n_baseline_files"]:
        lines.append(f"**Baseline files:** {result['n_baseline_files']}")
    lines.append("")
    lines.append("## Per-chapter signal dashboard")
    lines.append("")
    lines.append("Z-scores against personal baseline. Bold = |z| > 1.0 in the compression "
                 "direction. Negative z on `lt`-direction signals (MATTR, MTLD, FKGL_sd, "
                 "etc.) and positive z on `gt`-direction signals (Yule_K, conn_dens, "
                 "fw_ratio, adj_cos_mean) indicate compression vs. baseline.")
    lines.append("")

    # Header
    cols = [name for name, _, _ in DASHBOARD_SIGNALS]
    header = "| chapter | n_words | " + " | ".join(cols) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(cols) + 2))

    direction_map = {name: direction for name, _, direction in DASHBOARD_SIGNALS}

    for ch in chapters:
        cells = [ch["label"], str(ch["n_words"])]
        for name in cols:
            zinfo = ch.get("z_scores", {}).get(name)
            if zinfo is None:
                cells.append("--")
                continue
            z = zinfo["z"]
            direction = direction_map[name]
            compressed = (direction == "lt" and z < -1.0) or (direction == "gt" and z > 1.0)
            cell = f"{z:+.2f}"
            if compressed:
                cell = f"**{cell}**"
            cells.append(cell)
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    # Manuscript-wide signal summary
    summary = manuscript_summary(result)
    lines.append("## Manuscript-wide patterns")
    lines.append("")
    lines.append("Signals that fire on multiple chapters indicate manuscript-wide "
                 "compression rather than chapter-specific issues.")
    lines.append("")
    lines.append("| signal | compressed_chapters / total | fraction | mean z | max |z| |")
    lines.append("|---|---|---|---|---|")
    sig_items = sorted(summary["signal_summary"].items(),
                       key=lambda x: x[1]["fraction"], reverse=True)
    for name, info in sig_items:
        if info["total_chapters"] == 0:
            continue
        marker = ""
        if info["fraction"] >= 0.5:
            marker = " ⚠"
        lines.append(
            f"| {name} | {info['compressed_chapters']} / {info['total_chapters']} | "
            f"{info['fraction']:.2f}{marker} | {info['mean_z']:+.2f} | "
            f"{info['max_abs_z']:.2f} |"
        )
    lines.append("")
    flagged_signals = [name for name, info in summary["signal_summary"].items()
                       if info["fraction"] >= 0.5]
    if flagged_signals:
        lines.append(
            f"**Manuscript-wide signal:** {', '.join(flagged_signals)} — these signals are "
            f"compressed in at least half of all chapters. Treat as a manuscript-level pattern, "
            f"not chapter-specific."
        )
        lines.append("")

    # Outlier chapters
    lines.append("## Outlier chapters")
    lines.append("")
    lines.append("Chapters with the most |z| > 1.5 signals. These chapters deviate "
                 "most from baseline and are first candidates for revision.")
    lines.append("")
    lines.append("| chapter | n_words | flag_count | top flagged signals |")
    lines.append("|---|---|---|---|")
    for ch in summary["outliers"][:10]:
        if ch["flag_count"] == 0:
            continue
        flagged = sorted(ch["flagged"], key=lambda x: abs(x[1]), reverse=True)
        flagged_str = ", ".join(f"{name} ({z:+.2f})" for name, z, _kind in flagged[:5])
        lines.append(f"| {ch['label']} | {ch['n_words']} | {ch['flag_count']} | {flagged_str} |")
    lines.append("")

    return "\n".join(lines)


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-chapter Layer A variance audit for a manuscript."
    )
    parser.add_argument(
        "manuscript",
        nargs="?",
        help="Path to a single manuscript file with chapter markers (.md, .txt)."
    )
    parser.add_argument(
        "--chapter-dir",
        help="Alternative: directory of chapter files (.txt or .md), one chapter per file."
    )
    parser.add_argument(
        "--baseline-dir",
        help="Optional baseline corpus directory for z-score comparison."
    )
    parser.add_argument(
        "--chapter-pattern",
        default=r"^#+\s*Chapter\s+\d+",
        help="Regex pattern for chapter markers (default: '^#+\\s*Chapter\\s+\\d+')."
    )
    parser.add_argument(
        "--no-tier2", action="store_true",
        help="Skip Tier 2 metrics (POS bigrams, MDD)."
    )
    parser.add_argument(
        "--no-tier3", action="store_true",
        help="Skip Tier 3 metrics (adjacent-sentence cosine)."
    )
    parser.add_argument(
        "--allow-non-prose", action="store_true",
        help="Skip default corpus-hygiene stripping for chapters and baseline files."
    )
    parser.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rules to enable. Default: all "
             "conservative rules. Available: "
             + ", ".join(available_rule_names()) + "."
    )
    parser.add_argument(
        "--strip-aggressive", action="store_true",
        help="Also strip URL-only lines, image URLs, link wrappers, footnotes, "
             "and citations."
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of markdown.")
    parser.add_argument("--out", help="Write output to file instead of stdout.")
    # Chapter-level audit cache + resume (1.70.0). Each call to
    # ``audit_text`` is expensive (spaCy + signal computation); on a
    # 50-chapter manuscript with tier3 on, one chapter can take
    # minutes. Caching per-chapter audits means a crash on chapter
    # 40 of 50 doesn't lose the first 39's work, and a re-run with
    # a different --baseline-dir doesn't re-audit any chapters
    # whose tier flags are unchanged.
    parser.add_argument(
        "--chapter-audit-cache", default=None,
        help=(
            "Path to a JSON cache of per-chapter audits. Optional; "
            "default is no cache (run from scratch each time). When "
            "set, each chapter's audit is written to the cache "
            "atomically after it completes, with status='in_progress'. "
            "The final write flips status to 'complete'. On the next "
            "run with the same path, chapters whose label matches a "
            "cached audit are skipped — useful for long tier3 runs "
            "where a crash on chapter 40 of 50 would otherwise lose "
            "the first 39 chapters' audit work."
        ),
    )
    parser.add_argument(
        "--refresh-chapter-cache", action="store_true",
        help=(
            "Discard any existing --chapter-audit-cache and re-audit "
            "every chapter. Use after a code change that should "
            "invalidate cached audits but won't be caught by the "
            "minimal compatibility check (tier flags)."
        ),
    )

    args = parser.parse_args()

    if not args.manuscript and not args.chapter_dir:
        parser.error("Provide either a manuscript file or --chapter-dir.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.manuscript:
        text = Path(args.manuscript).read_text(encoding="utf-8", errors="ignore")
        chapters = split_manuscript(text, args.chapter_pattern)
    else:
        chapters = load_chapter_dir(args.chapter_dir)

    if not chapters:
        print("No chapters detected.", file=sys.stderr)
        return 1

    result = audit_manuscript(
        chapters,
        args.baseline_dir,
        do_tier2=not args.no_tier2,
        do_tier3=not args.no_tier3,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        cache_path=(
            Path(args.chapter_audit_cache).expanduser()
            if args.chapter_audit_cache
            else None
        ),
        refresh_cache=bool(args.refresh_chapter_cache),
    )

    if args.json:
        # Strip large nested audit fields for cleaner JSON output
        for ch in result["chapters"]:
            ch.pop("audit", None)
        envelope = build_audit_payload(
            result,
            target_path=args.manuscript or args.chapter_dir,
        )
        output = json.dumps(envelope, indent=2, default=str)
    else:
        output = render_dashboard(result)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Cross-chapter manuscript-level Layer A dashboard. Per "
            "chapter, runs variance_audit signals against an optional "
            "external baseline; aggregates into a manuscript-wide "
            "view with per-signal medians, per-chapter band labels, "
            "and outlier flagging."
        ),
        does_not_license=(
            "An authorship verdict. The dashboard surfaces chapter-"
            "level distributional patterns within a manuscript; "
            "explaining the patterns (genre conventions, deliberate "
            "craft choices, register drift, AI involvement) is the "
            "writer's call. Pair with the confounder audit and "
            "Layer C source triage before drawing conclusions."
        ),
        comparison_set={
            "n_chapters": result.get("n_chapters"),
            "n_baseline_files": result.get("n_baseline_files"),
        },
        additional_caveats=[
            "Inherits variance_audit's heuristic-tier calibration. "
            "Band labels are operator cues, not load-bearing "
            "verdicts.",
            "Cross-chapter aggregation uses medians (not means) so "
            "a single outlier chapter does not dominate the "
            "dashboard summary; the chapters list preserves per-"
            "chapter detail for drill-down.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap manuscript_audit in the schema_version 1.0 envelope per
    ``internal/SPEC_output_schema_unification.md``.
    """
    chapters = result.get("chapters", []) or []
    target_words = sum(
        int(ch.get("n_words", 0) or 0) for ch in chapters
    )
    target_extra: dict[str, Any] = {}
    preprocessing = result.get("preprocessing", {}) or {}
    if preprocessing.get("chapters"):
        target_extra["preprocessing"] = preprocessing["chapters"]
    n_chapters = result.get("n_chapters")
    if n_chapters is not None:
        target_extra["n_chapters"] = n_chapters

    baseline_meta: dict[str, Any] | None = None
    if result.get("n_baseline_files"):
        baseline_meta = build_baseline_metadata(
            n_files=int(result.get("n_baseline_files", 0) or 0),
            words=0,
            extra=(
                {"preprocessing": preprocessing["baseline"]}
                if preprocessing.get("baseline") else None
            ),
        )

    results = {
        "chapters": chapters,
        "baseline_stats": result.get("baseline_stats"),
    }

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=_claim_license(result),
        target_extra=target_extra or None,
    )


if __name__ == "__main__":
    sys.exit(main())
