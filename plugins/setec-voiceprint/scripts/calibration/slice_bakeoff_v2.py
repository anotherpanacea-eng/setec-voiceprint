#!/usr/bin/env python3
"""slice_bakeoff_v2.py — per-stratum AUC analyzer with CIs and polarity audit.

Reads scored-records caches produced by ``calibration_survey`` and
``calibrate_thresholds``, computes Mann-Whitney AUC across user-chosen
slices with Hanley-McNeil approximate confidence intervals, and optionally
emits a polarity-audit verdict per ``(model × signal)`` summarising whether
the framework's registry direction matches the empirical sign of the
discrimination on the comparator at hand.

Successor to ``scripts/slice_bakeoff.py`` v1 (laptop-vintage, hardcoded
paths). v2 is cloud-portable: paths come from CLI, the CSV carries CIs,
multi-key cross-tabs are supported, and the polarity-audit mode produces
a structured JSON report consumable by downstream registry-recommendation
tooling.

Per ``SPEC_slice_bakeoff_v2.md`` and ``SPEC_polarity_audit.md`` in
``internal/`` (gitignored).

Inputs
------

* ``--cache-dir`` — directory containing ``cache_phase{A,B}_<alias>.json``
  files. Each cache is the per-row scored-records list produced by
  ``calibrate_thresholds.score_corpus`` (one record per manifest entry,
  with ``per_signal_scores`` populated per the registry's signal paths).
* ``--manifest`` — the manifest JSONL the cache was scored against.
  Used to join each record's ``id`` with the manifest's ``notes`` block
  for ``notes.<key>`` slicing.

Outputs (under ``--out-dir``)
-----------------------------

* ``slice_analysis.csv`` — one row per (model × signal × slice) cell,
  with raw AUC, direction-aware AUC, |signal|, and Hanley-McNeil 95% CIs
  for all three.
* ``slice_analysis.md`` — human-readable summary: aggregate table,
  per-univariate-slice tables, per-crosstab tables, "real signal" subset
  (cells whose lower CI bound on |sig| clears 0.05).
* ``polarity_audit.json`` — when ``--audit polarity`` is set: per
  ``(model, signal)`` verdict + recommended direction.
* ``provenance.json`` — CLI args, cache-file mtimes, manifest hash,
  slicer version, run timestamp.

The slicer is read-only — it never modifies the registry or the cache.
The polarity audit is *evidence*, not adjudication; the framework owner
decides whether to flip registry directions on the basis of it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

# Shared polarity-audit logic (cell classification, verdict, recommendation).
# Lives in a sibling module so the standalone ``polarity_audit.py`` CLI and
# the slicer's ``--audit polarity`` mode produce identical verdicts; the
# 5K-bundle workflow uses the standalone tool against v1 CSVs, and the
# cloud-bake-off workflow uses the integrated mode against v2 CSVs.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
from polarity_audit import (  # type: ignore  # noqa: E402
    build_audit as build_polarity_audit,
    DEFAULT_REGISTRY_DIRECTIONS,
)

SLICER_VERSION = "v2.0.0"
TOOL_NAME = "slice_bakeoff_v2"

# ----------------------------------------------------------------- Signal registry

# Source of record: ``plugins/setec-voiceprint/scripts/variance_audit.py``
# (the ``COMPRESSION_HEURISTICS`` block). Hardcoded here for self-containment;
# the slicer is intentionally a downstream consumer, not a re-runner of the
# audit. If the registry directions change in variance_audit.py, this table
# moves in lockstep.
SIGNAL_SPECS: dict[str, tuple[str, str]] = {
    "adjacent_cosine_mean": ("tier3.adjacent_cosine.mean", "gt"),
    "adjacent_cosine_sd": ("tier3.adjacent_cosine.sd", "lt"),
    "surprisal_mean": ("tier4.surprisal.mean", "lt"),
    "surprisal_sd": ("tier4.surprisal.sd", "lt"),
    "surprisal_acf_lag1": ("tier4.surprisal.autocorrelation.lag_1", "gt"),
}

PHASE_A_SIGNALS = ("adjacent_cosine_mean", "adjacent_cosine_sd")
PHASE_B_SIGNALS = ("surprisal_mean", "surprisal_sd", "surprisal_acf_lag1")

DEFAULT_MIN_N_PER_CLASS = 30


# ----------------------------------------------------------------- AUC + CIs


def mwu_auc(pos_scores: list[float], neg_scores: list[float]) -> float | None:
    """Mann-Whitney U AUC = P(score(pos) > score(neg)) with proper
    rank-based tie handling (average ranks for tied groups). Returns
    ``None`` when either class has fewer than 2 records.

    The implementation matches v1's exactly — ported here as a backwards-
    compatibility commitment. Spec §"AUC tie handling" pins this as
    rank-sum with average ranks, not naive concordance count.
    """
    n_p = len(pos_scores)
    n_n = len(neg_scores)
    if n_p < 2 or n_n < 2:
        return None
    combined = [(s, 1) for s in pos_scores] + [(s, 0) for s in neg_scores]
    combined.sort(key=lambda x: x[0])
    rank_sum_pos = 0.0
    i = 0
    L = len(combined)
    while i < L:
        j = i
        while j < L and combined[j][0] == combined[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            if combined[k][1] == 1:
                rank_sum_pos += avg_rank
        i = j
    u_pos = rank_sum_pos - n_p * (n_p + 1) / 2.0
    return u_pos / (n_p * n_n)


def hanley_mcneil_se(auc: float, n_pos: int, n_neg: int) -> float:
    """Hanley-McNeil approximate standard error of an AUC.

    SE^2 = (AUC*(1-AUC) + (n_p-1)*(Q1 - AUC^2) + (n_n-1)*(Q2 - AUC^2)) / (n_p*n_n)
    Q1 = AUC / (2 - AUC)
    Q2 = 2*AUC^2 / (1 + AUC)

    Per Hanley & McNeil (1982), "The Meaning and Use of the Area under a
    Receiver Operating Characteristic (ROC) Curve." Used here for normal-
    approximation 95% CIs (auc ± 1.96 * se). For tightly clustered cells
    at small n, the normal approximation is generous — operators reading
    the CSV should treat it as smoke-test rigour, not publication-grade.
    """
    if n_pos <= 0 or n_neg <= 0:
        return float("nan")
    q1 = auc / (2.0 - auc) if auc < 2.0 else 0.0
    q2 = 2.0 * auc * auc / (1.0 + auc) if auc > -1.0 else 0.0
    var = (
        auc * (1.0 - auc)
        + (n_pos - 1) * (q1 - auc * auc)
        + (n_neg - 1) * (q2 - auc * auc)
    ) / (n_pos * n_neg)
    if var < 0.0:
        # Floating-point arithmetic can drive the variance estimate
        # slightly negative for extreme AUC values (≈ 0 or ≈ 1) on
        # small cells. Clamp at 0 rather than emit NaN; the CI then
        # collapses to the point estimate, which is the correct
        # statistical behaviour for degenerate cells.
        var = 0.0
    return math.sqrt(var)


def ci95(auc: float, se: float) -> tuple[float, float]:
    """Normal-approximation 95% CI: auc ± 1.96 * se, clipped to [0, 1]."""
    lo = max(0.0, auc - 1.96 * se)
    hi = min(1.0, auc + 1.96 * se)
    return lo, hi


def abs_signal_ci(da_auc: float, se: float) -> tuple[float, float]:
    """CI on |da_auc - 0.5|. Lower bound clamped at 0 because |.| ≥ 0."""
    point = abs(da_auc - 0.5)
    lo = max(0.0, point - 1.96 * se)
    hi = point + 1.96 * se
    return lo, hi


def da(auc: float | None, direction: str) -> float | None:
    """Direction-aware AUC: registry-`gt` keeps AUC, registry-`lt` flips."""
    if auc is None:
        return None
    return auc if direction == "gt" else 1.0 - auc


# ----------------------------------------------------------------- I/O


def parse_phase_and_model(name: str) -> tuple[str | None, str | None]:
    """Parse ``cache_phase{A,B}_<alias>.json`` → (phase, alias)."""
    m = re.match(r"cache_phase([AB])_(.+)\.json$", name)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def load_manifest_notes(path: Path) -> dict[str, dict[str, Any]]:
    """Load manifest JSONL and return ``{entry_id: notes_block}``.

    Entries without an ``id`` field are skipped; the slicer can't join
    them. The notes block is whatever the manifest carries — schema is
    corpus-specific (MAGE: ``original_source``, ``split``; RAID:
    ``domain``, ``model``, ``attack``).
    """
    out: dict[str, dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_id = e.get("id")
            if not isinstance(entry_id, str):
                continue
            out[entry_id] = e.get("notes") or {}
    return out


def collect_scores(
    records: list[dict[str, Any]],
    signal_path: str,
) -> tuple[list[float], list[float]]:
    """Extract per-class score lists for one signal.

    Does NOT filter on ``usable_for_metrics`` — that flag gates the
    per-doc variance-audit verdict (per-signal length floors); calibration
    AUC uses every row where the signal value is non-null. v1 had a bug
    here that was fixed; v2 preserves the fix.
    """
    pos: list[float] = []
    neg: list[float] = []
    for r in records:
        signal = (r.get("per_signal_scores") or {}).get(signal_path)
        if signal is None:
            continue
        label = r.get("label")
        if label == 1:
            pos.append(float(signal))
        elif label == 0:
            neg.append(float(signal))
    return pos, neg


def slice_records(
    records: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], Any],
) -> dict[Any, list[dict[str, Any]]]:
    """Group records by ``key_fn(record)``."""
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        groups[key_fn(r)].append(r)
    return groups


# ----------------------------------------------------------------- Cells


def emit_cell(
    rows: list[dict[str, Any]],
    corpus: str,
    model: str,
    signal: str,
    direction: str,
    slice_key: str,
    slice_value: str,
    pos: list[float],
    neg: list[float],
    *,
    min_n: int,
) -> None:
    """Compute AUC + CIs for one cell and append a row dict."""
    if len(pos) < min_n or len(neg) < min_n:
        return
    auc = mwu_auc(pos, neg)
    if auc is None:
        return
    se = hanley_mcneil_se(auc, len(pos), len(neg))
    auc_lo, auc_hi = ci95(auc, se)
    da_value = da(auc, direction)
    if da_value is None:
        return
    da_lo = max(0.0, da_value - 1.96 * se) if direction == "gt" else max(0.0, (1.0 - auc) - 1.96 * se)
    da_hi = min(1.0, da_value + 1.96 * se) if direction == "gt" else min(1.0, (1.0 - auc) + 1.96 * se)
    sig_lo, sig_hi = abs_signal_ci(da_value, se)
    rows.append({
        "corpus": corpus,
        "model": model,
        "signal": signal,
        "slice_key": slice_key,
        "slice_value": str(slice_value),
        "n_pos": len(pos),
        "n_neg": len(neg),
        "auc": auc,
        "da_auc": da_value,
        "abs_signal": abs(da_value - 0.5),
        "se": se,
        "auc_lo": auc_lo,
        "auc_hi": auc_hi,
        "da_auc_lo": da_lo,
        "da_auc_hi": da_hi,
        "abs_signal_lo": sig_lo,
        "abs_signal_hi": sig_hi,
    })


def get_crosstab_value(
    record: dict[str, Any], key: str,
) -> Any:
    """Resolve a (possibly dotted) slice key against a record.

    Supports ``length_bucket``, ``register``, ``adversarial_class``
    (top-level) and ``notes.<key>`` for manifest-side fields. Returns
    ``"unknown"`` when the key resolves to None or is absent."""
    if key.startswith("notes."):
        sub = key[len("notes."):]
        return (record.get("_notes") or {}).get(sub) or "unknown"
    return record.get(key) or "unknown"


def emit_crosstab(
    rows: list[dict[str, Any]],
    corpus: str,
    model: str,
    signal: str,
    direction: str,
    signal_path: str,
    records: list[dict[str, Any]],
    keys: list[str],
    *,
    min_n: int,
) -> None:
    """Emit one row per crosstab cell for the (key tuple, value tuple)."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        value_tuple = tuple(get_crosstab_value(r, k) for k in keys)
        groups[value_tuple].append(r)
    slice_key_str = ",".join(keys)
    for value_tuple, sub in groups.items():
        pos, neg = collect_scores(sub, signal_path)
        slice_value_str = ",".join(str(v) for v in value_tuple)
        emit_cell(
            rows, corpus, model, signal, direction,
            slice_key_str, slice_value_str, pos, neg, min_n=min_n,
        )


# ----------------------------------------------------------------- Orchestration


def analyze(
    cache_dir: Path,
    manifest_path: Path,
    out_dir: Path,
    *,
    corpus: str,
    domain_key: str | None,
    split_key: str | None,
    generator_key: str | None,
    crosstabs: list[list[str]],
    min_n: int,
    do_polarity_audit: bool,
    comparator_key: str | None,
) -> int:
    if not cache_dir.exists():
        print(f"[{corpus}] cache_dir missing: {cache_dir}", file=sys.stderr)
        return 2
    if not manifest_path.exists():
        print(f"[{corpus}] manifest missing: {manifest_path}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    notes_by_id = load_manifest_notes(manifest_path)
    cache_files = sorted(cache_dir.glob("cache_phase[AB]_*.json"))
    if not cache_files:
        print(
            f"[{corpus}] no cache_phase[AB]_*.json files in {cache_dir}",
            file=sys.stderr,
        )
        return 2

    rows: list[dict[str, Any]] = []

    for cache_file in cache_files:
        phase, model = parse_phase_and_model(cache_file.name)
        if phase is None or model is None:
            continue
        signals = PHASE_A_SIGNALS if phase == "A" else PHASE_B_SIGNALS
        try:
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(
                f"  SKIP {cache_file.name}: malformed JSON ({exc})",
                file=sys.stderr,
            )
            continue
        records = cache_data.get("records") or []
        for r in records:
            r["_notes"] = notes_by_id.get(r.get("id"), {})

        for signal_name in signals:
            signal_path, direction = SIGNAL_SPECS[signal_name]

            # Aggregate.
            pos, neg = collect_scores(records, signal_path)
            emit_cell(
                rows, corpus, model, signal_name, direction,
                "ALL", "all", pos, neg, min_n=min_n,
            )

            # Univariate slices.
            for bucket, sub in slice_records(
                records, lambda r: r.get("length_bucket") or "unknown",
            ).items():
                p, n = collect_scores(sub, signal_path)
                emit_cell(
                    rows, corpus, model, signal_name, direction,
                    "length_bucket", str(bucket), p, n, min_n=min_n,
                )

            for register_val, sub in slice_records(
                records, lambda r: r.get("register") or "unknown",
            ).items():
                p, n = collect_scores(sub, signal_path)
                emit_cell(
                    rows, corpus, model, signal_name, direction,
                    "register", str(register_val), p, n, min_n=min_n,
                )

            for adv_class, sub in slice_records(
                records, lambda r: r.get("adversarial_class") or "unknown",
            ).items():
                p, n = collect_scores(sub, signal_path)
                emit_cell(
                    rows, corpus, model, signal_name, direction,
                    "adversarial_class", str(adv_class), p, n, min_n=min_n,
                )

            if domain_key:
                k_full = f"notes.{domain_key}"
                for dv, sub in slice_records(
                    records,
                    lambda r: (r.get("_notes") or {}).get(domain_key) or "unknown",
                ).items():
                    p, n = collect_scores(sub, signal_path)
                    emit_cell(
                        rows, corpus, model, signal_name, direction,
                        k_full, str(dv), p, n, min_n=min_n,
                    )

            if split_key:
                k_full = f"notes.{split_key}"
                for sv, sub in slice_records(
                    records,
                    lambda r: (r.get("_notes") or {}).get(split_key) or "unknown",
                ).items():
                    p, n = collect_scores(sub, signal_path)
                    emit_cell(
                        rows, corpus, model, signal_name, direction,
                        k_full, str(sv), p, n, min_n=min_n,
                    )

            if generator_key:
                # Special handling: all humans pooled vs. AI grouped by
                # generator. Lets us reason about which generators are
                # easier vs. harder to detect on the same human baseline.
                human_scores = []
                for r in records:
                    if r.get("label") != 0:
                        continue
                    sval = (r.get("per_signal_scores") or {}).get(signal_path)
                    if sval is not None:
                        human_scores.append(float(sval))
                if len(human_scores) >= min_n:
                    ai_by_gen: dict[Any, list[float]] = defaultdict(list)
                    for r in records:
                        if r.get("label") != 1:
                            continue
                        sval = (r.get("per_signal_scores") or {}).get(signal_path)
                        if sval is None:
                            continue
                        gen = (r.get("_notes") or {}).get(generator_key) or "unknown"
                        ai_by_gen[gen].append(float(sval))
                    for gen, gen_scores in ai_by_gen.items():
                        emit_cell(
                            rows, corpus, model, signal_name, direction,
                            f"notes.{generator_key}_vs_all_humans",
                            str(gen), gen_scores, human_scores, min_n=min_n,
                        )

            # User-specified cross-tabs.
            for crosstab_keys in crosstabs:
                emit_crosstab(
                    rows, corpus, model, signal_name, direction,
                    signal_path, records, crosstab_keys, min_n=min_n,
                )

    # Emit CSV.
    csv_path = out_dir / "slice_analysis.csv"
    write_csv(rows, csv_path)
    print(f"[{corpus}] wrote {csv_path}  ({len(rows)} rows)")

    # Emit markdown.
    md_path = out_dir / "slice_analysis.md"
    write_md(rows, md_path, corpus=corpus, min_n=min_n)
    print(f"[{corpus}] wrote {md_path}")

    # Polarity audit (optional). Single source of truth lives in
    # polarity_audit.py so the standalone CLI and the integrated mode
    # produce byte-identical verdicts. The slicer rows already carry
    # the CI columns the audit needs.
    if do_polarity_audit:
        signal_to_direction = {
            name: direction for name, (_, direction) in SIGNAL_SPECS.items()
        }
        # Override DEFAULT_REGISTRY_DIRECTIONS for any signals not in
        # the standalone module's table (defensive: keeps the slicer
        # working if the registry adds a signal before polarity_audit's
        # default table is updated).
        merged_directions = dict(DEFAULT_REGISTRY_DIRECTIONS)
        merged_directions.update(signal_to_direction)
        audit = build_polarity_audit(
            rows,
            registry_directions=merged_directions,
            comparator_key=comparator_key,
        )
        audit_path = out_dir / "polarity_audit.json"
        audit_path.write_text(
            json.dumps(audit, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"[{corpus}] wrote {audit_path}")

    # Provenance.
    write_provenance(
        out_dir / "provenance.json",
        cache_dir=cache_dir,
        cache_files=cache_files,
        manifest_path=manifest_path,
        corpus=corpus,
        crosstabs=crosstabs,
        min_n=min_n,
        do_polarity_audit=do_polarity_audit,
        comparator_key=comparator_key,
    )
    return 0


# ----------------------------------------------------------------- Output writers


CSV_COLUMNS: tuple[str, ...] = (
    "corpus", "model", "signal", "slice_key", "slice_value",
    "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
    "se", "auc_lo", "auc_hi", "da_auc_lo", "da_auc_hi",
    "abs_signal_lo", "abs_signal_hi",
)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r_out = {k: r.get(k, "") for k in CSV_COLUMNS}
            # Format numerics to 4 decimal places for readability;
            # CIs to 4 dp matches the v1 contract.
            for k in (
                "auc", "da_auc", "abs_signal", "se",
                "auc_lo", "auc_hi", "da_auc_lo", "da_auc_hi",
                "abs_signal_lo", "abs_signal_hi",
            ):
                v = r_out.get(k)
                if isinstance(v, float):
                    r_out[k] = f"{v:.4f}"
            w.writerow(r_out)


def write_md(
    rows: list[dict[str, Any]], path: Path, *, corpus: str, min_n: int,
) -> None:
    aggregate = [r for r in rows if r["slice_key"] == "ALL"]
    sliced = [r for r in rows if r["slice_key"] != "ALL"]
    sliced_sorted = sorted(sliced, key=lambda r: -float(r["abs_signal"]))
    real_signal = [
        r for r in sliced
        if float(r["abs_signal_lo"]) >= 0.05
    ]
    real_signal_sorted = sorted(real_signal, key=lambda r: -float(r["abs_signal"]))

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Per-stratum bake-off slice ({corpus})\n\n")
        f.write(f"Min n per class for a cell to report: {min_n}.\n")
        f.write(
            "|da_AUC - 0.5| is the signal-strength metric "
            "(>= 0.05 = clear discriminator).\n"
        )
        f.write(
            "95% CIs via Hanley-McNeil approximation; treat as "
            "smoke-test rigour, not publication-grade.\n\n"
        )

        f.write("## Aggregate (ALL) per (model × signal) with 95% CI\n\n")
        f.write(
            "| model | signal | n_pos | n_neg | "
            "da_AUC | da_AUC 95% CI | |sig| | |sig| 95% CI |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in aggregate:
            f.write(
                f"| {r['model']} | {r['signal']} | "
                f"{r['n_pos']} | {r['n_neg']} | "
                f"{float(r['da_auc']):.4f} | "
                f"[{float(r['da_auc_lo']):.3f}, {float(r['da_auc_hi']):.3f}] | "
                f"{float(r['abs_signal']):.4f} | "
                f"[{float(r['abs_signal_lo']):.3f}, {float(r['abs_signal_hi']):.3f}] |\n"
            )

        f.write("\n## Top 40 per-cell slices by |da_AUC - 0.5|\n\n")
        f.write(
            "| model | signal | slice_key | slice_value | "
            "n_pos | n_neg | da_AUC | |sig| 95% CI |\n"
        )
        f.write("|---|---|---|---|---|---|---|---|\n")
        for r in sliced_sorted[:40]:
            f.write(
                f"| {r['model']} | {r['signal']} | "
                f"{r['slice_key']} | {r['slice_value']} | "
                f"{r['n_pos']} | {r['n_neg']} | "
                f"{float(r['da_auc']):.4f} | "
                f"[{float(r['abs_signal_lo']):.3f}, "
                f"{float(r['abs_signal_hi']):.3f}] |\n"
            )

        f.write(
            f"\n## Cells with |sig| lower-CI bound >= 0.05 "
            f"('real signal' subset): {len(real_signal_sorted)}\n\n"
        )
        if real_signal_sorted:
            f.write(
                "| model | signal | slice_key | slice_value | "
                "n_pos | n_neg | da_AUC | |sig| 95% CI |\n"
            )
            f.write("|---|---|---|---|---|---|---|---|\n")
            for r in real_signal_sorted:
                f.write(
                    f"| {r['model']} | {r['signal']} | "
                    f"{r['slice_key']} | {r['slice_value']} | "
                    f"{r['n_pos']} | {r['n_neg']} | "
                    f"{float(r['da_auc']):.4f} | "
                    f"[{float(r['abs_signal_lo']):.3f}, "
                    f"{float(r['abs_signal_hi']):.3f}] |\n"
                )


def write_provenance(
    path: Path,
    *,
    cache_dir: Path,
    cache_files: list[Path],
    manifest_path: Path,
    corpus: str,
    crosstabs: list[list[str]],
    min_n: int,
    do_polarity_audit: bool,
    comparator_key: str | None,
) -> None:
    """Per-run provenance: CLI args, cache mtimes, manifest hash."""
    try:
        manifest_hash = hashlib.sha256(
            manifest_path.read_bytes(),
        ).hexdigest()
    except OSError:
        manifest_hash = None
    cache_meta = []
    for cf in cache_files:
        try:
            st = cf.stat()
            cache_meta.append({
                "name": cf.name,
                "size_bytes": st.st_size,
                "mtime_iso": _dt.datetime.fromtimestamp(
                    st.st_mtime, _dt.timezone.utc,
                ).isoformat(),
            })
        except OSError:
            cache_meta.append({"name": cf.name, "size_bytes": None})
    provenance = {
        "tool": TOOL_NAME,
        "tool_version": SLICER_VERSION,
        "run_iso": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "corpus": corpus,
        "cache_dir": str(cache_dir),
        "cache_files": cache_meta,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_hash,
        "crosstabs": crosstabs,
        "min_n_per_class": min_n,
        "polarity_audit_enabled": do_polarity_audit,
        "comparator_key": comparator_key,
    }
    path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ----------------------------------------------------------------- CLI


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slice_bakeoff_v2",
        description=(
            "Per-stratum AUC analyzer for SETEC bake-off caches. Reads "
            "cache_phase{A,B}_*.json files produced by calibration_survey "
            "/ calibrate_thresholds, joins each record with manifest "
            "notes, computes Mann-Whitney AUC across user-chosen slices "
            "with Hanley-McNeil 95% CIs, and (optionally) emits a "
            "polarity-audit verdict per (model × signal)."
        ),
    )
    p.add_argument(
        "--corpus", required=True,
        help="Corpus label (e.g., 'mage', 'raid'). Used in the CSV "
             "'corpus' column and the markdown title; does not affect "
             "slicer behavior.",
    )
    p.add_argument(
        "--cache-dir", required=True, type=Path,
        help="Directory containing cache_phase{A,B}_*.json files.",
    )
    p.add_argument(
        "--manifest", required=True, type=Path,
        help="Manifest JSONL the cache was scored against.",
    )
    p.add_argument(
        "--out-dir", required=True, type=Path,
        help="Output directory; will be created if missing.",
    )
    p.add_argument(
        "--domain-key", default=None,
        help="Manifest notes key to slice by domain (e.g., "
             "'original_source' for MAGE, 'domain' for RAID). Optional.",
    )
    p.add_argument(
        "--split-key", default=None,
        help="Manifest notes key for additional split slicing "
             "(e.g., 'split' for MAGE). Optional.",
    )
    p.add_argument(
        "--generator-key", default=None,
        help="Manifest notes key for AI-generator family. When set, "
             "the slicer emits a notes.<key>_vs_all_humans crosstab "
             "for each value (e.g., 'model' for RAID generates "
             "per-generator-vs-all-humans AUCs).",
    )
    p.add_argument(
        "--crosstab", action="append", default=[],
        help="Comma-separated stratum keys for a cross-tab slice "
             "(e.g., 'length_bucket,notes.original_source'). Can be "
             "passed multiple times for multiple crosstabs.",
    )
    p.add_argument(
        "--min-n", type=int, default=DEFAULT_MIN_N_PER_CLASS,
        help=(
            "Minimum n per class for a cell to be reported "
            f"(default {DEFAULT_MIN_N_PER_CLASS}). At MAGE / RAID "
            "full-corpus scale, 100 is more defensible. Cells "
            "below this bound are silently dropped."
        ),
    )
    p.add_argument(
        "--audit", choices=["polarity"], default=None,
        help="Audit mode. When 'polarity', the slicer also writes "
             "polarity_audit.json per SPEC_polarity_audit.md.",
    )
    p.add_argument(
        "--comparator-key", default=None,
        help="When --audit polarity is set, the slice key whose "
             "values name comparator classes. Used by the verdict's "
             "recommendation block. Typical values: "
             "'notes.original_source' (MAGE), 'notes.domain' (RAID).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    crosstabs: list[list[str]] = []
    for ct in args.crosstab:
        keys = [k.strip() for k in ct.split(",") if k.strip()]
        if len(keys) >= 1:
            crosstabs.append(keys)
    do_polarity = args.audit == "polarity"
    return analyze(
        cache_dir=args.cache_dir,
        manifest_path=args.manifest,
        out_dir=args.out_dir,
        corpus=args.corpus,
        domain_key=args.domain_key,
        split_key=args.split_key,
        generator_key=args.generator_key,
        crosstabs=crosstabs,
        min_n=args.min_n,
        do_polarity_audit=do_polarity,
        comparator_key=args.comparator_key,
    )


if __name__ == "__main__":
    sys.exit(main())
