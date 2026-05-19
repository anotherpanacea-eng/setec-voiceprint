"""Provenance + summary helpers for the cloud bake-off matrix runner.

Separated from the shell driver (``bakeoff_matrix.sh``) so the
logic that actually computes "is this survey done?", emits the
session provenance.json, and renders the post-run summary can be
unit-tested in isolation. The shell script is thin — env-var
validation and per-cell ``calibration_survey.py`` invocations —
and delegates everything that needs verification to the
functions in this module.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ----------------------------------------------------------------- Skip check


def is_survey_complete(survey_path: Path) -> bool:
    """Return True if ``survey_path`` exists, parses as JSON, and
    has a non-empty ``rows`` list.

    Mirrors the laptop template's inline-python skip check: a
    survey JSON is treated as "done" only when ``calibration_survey``
    has actually produced row data. Empty or partial files fall
    through to a re-run, so a crash mid-cell loses only the
    in-flight cell.
    """
    if not survey_path.is_file() or survey_path.stat().st_size == 0:
        return False
    try:
        with survey_path.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and bool(data.get("rows"))


# ----------------------------------------------------------------- Provenance


@dataclass(frozen=True)
class ProvenanceInputs:
    """Everything ``write_provenance`` needs that isn't probed from
    the running process itself. Pure data so tests can build one
    without a real host."""

    session_id: str
    corpus_label: str  # "mage" / "raid" / something operator-supplied
    manifest_path: Path
    phase_a_aliases: list[str]
    phase_b_aliases: list[str]
    phase_a_signals: list[str]
    phase_b_signals: list[str]
    phase_a_paths: dict[str, str]
    phase_b_paths: dict[str, str]
    max_entries: int | None
    bootstrap_engine: str
    bootstrap_resamples: int
    fpr_target: float
    cooldown_sec: int
    survey_dir: Path
    # 1.99.0+: comparator class for per-signal direction routing.
    # Defaults to None to keep pre-1.99 test fixtures
    # (``ProvenanceInputs(...)`` without this kwarg) working
    # unchanged. When set (operator passed SETEC_COMPARATOR_CLASS
    # or it auto-defaulted from a known corpus label), the shell
    # driver also adds --comparator-class to every
    # calibration_survey call so the routing actually takes
    # effect end-to-end.
    comparator_class: str | None = None
    # 1.X+: per-(judge × generator) slice axes (roadmap item D).
    # No corpus-based default (unlike comparator_class) — judges and
    # generators are slice axes WITHIN a corpus. When set on the
    # shell driver via SETEC_JUDGE / SETEC_GENERATOR, the driver
    # adds --judge X --generator Y to every calibration_survey call.
    judge: str | None = None
    generator: str | None = None


def _git_head_sha(cwd: Path) -> str | None:
    """Return the short SHA of HEAD if ``cwd`` is a git checkout, else None.
    Falls back gracefully if git isn't on PATH or the directory isn't
    a repo — provenance should never block a run."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


def _package_versions() -> dict[str, str | None]:
    """Best-effort package-version probe. None for packages that
    aren't installed in this venv — the cloud host may legitimately
    run with a subset (e.g., torch but not transformers if only the
    embedding path is exercised)."""
    out: dict[str, str | None] = {}
    for pkg in ("torch", "transformers", "sentence_transformers", "numpy"):
        try:
            mod = __import__(pkg)
        except ImportError:
            out[pkg] = None
            continue
        out[pkg] = getattr(mod, "__version__", None)
    return out


def build_provenance(inputs: ProvenanceInputs, *, repo_root: Path) -> dict[str, Any]:
    """Build the provenance dict without writing it. Split out so
    tests can assert the dict shape without touching the
    filesystem."""
    manifest_size: int | None
    try:
        manifest_size = inputs.manifest_path.stat().st_size
    except OSError:
        manifest_size = None
    return {
        "session_id": inputs.session_id,
        "corpus_label": inputs.corpus_label,
        # 1.99.0+: persisted so audit consumers can tell a routed
        # bake-off from an un-routed one, and so a replay from this
        # provenance reproduces the same direction regime.
        "comparator_class": inputs.comparator_class,
        # 1.X+: per-(judge × generator) slice axes recorded so an
        # operator replaying from the ledger reproduces the same
        # slice routing (roadmap item D).
        "judge": inputs.judge,
        "generator": inputs.generator,
        "host": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "cuda_visible_devices": os.environ.get(
                "CUDA_VISIBLE_DEVICES",
            ),
        },
        "packages": _package_versions(),
        "repo_head_sha": _git_head_sha(repo_root),
        "manifest": {
            "path": str(inputs.manifest_path),
            "size_bytes": manifest_size,
        },
        "phases": {
            "A": {
                "aliases": list(inputs.phase_a_aliases),
                "signals": list(inputs.phase_a_signals),
                "paths": dict(inputs.phase_a_paths),
            },
            "B": {
                "aliases": list(inputs.phase_b_aliases),
                "signals": list(inputs.phase_b_signals),
                "paths": dict(inputs.phase_b_paths),
            },
        },
        "calibration_args": {
            "max_entries": inputs.max_entries,
            "bootstrap_engine": inputs.bootstrap_engine,
            "bootstrap_resamples": inputs.bootstrap_resamples,
            "fpr_target": inputs.fpr_target,
        },
        "cooldown_sec": inputs.cooldown_sec,
        "survey_dir": str(inputs.survey_dir),
    }


def write_provenance(
    inputs: ProvenanceInputs, *,
    repo_root: Path, out_path: Path,
) -> dict[str, Any]:
    """Materialize the provenance dict to ``out_path`` (JSON,
    indent=2). Returns the dict for inspection by the caller / tests.

    Idempotent — overwriting a previous provenance.json is fine;
    operators re-run the matrix script across sessions and each
    session writes its own provenance with its own session_id."""
    prov = build_provenance(inputs, repo_root=repo_root)
    out_path.write_text(
        json.dumps(prov, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return prov


# ----------------------------------------------------------------- Summary


def _da_auc_from_row(row: dict[str, Any]) -> float | None:
    """Pull direction-aware AUC out of a survey row, handling both
    the calibration-block case (signal was direction-consistent and
    landed in ``row["calibration"]["direction_aware_auc"]``) and the
    inverted-polarity case (calibration_survey wrote the AUC into
    the error string for visibility). Mirrors the laptop template's
    extractor so the cloud summary stays format-compatible."""
    cal = row.get("calibration") or {}
    da = cal.get("direction_aware_auc")
    if da is not None:
        return float(da)
    err = row.get("error") or ""
    import re
    m = re.search(r"direction_aware_auc = (\d+\.\d+)", err)
    if m:
        return float(m.group(1))
    return None


def summarize_matrix(
    survey_dir: Path, *,
    phase_a_aliases: list[str],
    phase_b_aliases: list[str],
    phase_a_signals: list[str],
    phase_b_signals: list[str],
) -> str:
    """Build the post-run summary table as Markdown. Returns the
    string; caller writes it wherever ($SUMMARY in the shell script,
    or stdout for ad-hoc inspection). Pure function — no side
    effects beyond reading survey files in ``survey_dir``."""

    def cell_for(path: Path, signal: str) -> str:
        if not path.is_file():
            return "--"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "err"
        rows = {r.get("signal"): r for r in data.get("rows", [])}
        r = rows.get(signal)
        if r is None:
            return "--"
        da = _da_auc_from_row(r)
        if da is None:
            return "err"
        strength = abs(da - 0.5)
        return f"{da:.4f} ({strength:.3f})"

    lines: list[str] = []
    lines.append("# Bake-off matrix summary")
    lines.append("")
    lines.append("Signal-strength metric: abs(da_AUC - 0.5).")
    lines.append("  >= 0.05 = clear discriminator (in either direction)")
    lines.append("  < 0.05  = essentially chance")
    lines.append("")

    # Phase A
    lines.append("## Phase A: Tier 3 embedding")
    header_cols = " | ".join(f"{s} (da_AUC, |sig|)" for s in phase_a_signals)
    lines.append(f"| Model | {header_cols} |")
    lines.append("|---|" + "---|" * len(phase_a_signals))
    for alias in phase_a_aliases:
        p = survey_dir / f"survey_phaseA_{alias}.json"
        cells = [cell_for(p, sig) for sig in phase_a_signals]
        lines.append(f"| {alias} | " + " | ".join(cells) + " |")
    lines.append("")

    # Phase B
    lines.append("## Phase B: Tier 4 surprisal")
    header_cols = " | ".join(f"{s} (da_AUC, |sig|)" for s in phase_b_signals)
    lines.append(f"| Model | {header_cols} |")
    lines.append("|---|" + "---|" * len(phase_b_signals))
    for alias in phase_b_aliases:
        p = survey_dir / f"survey_phaseB_{alias}.json"
        cells = [cell_for(p, sig) for sig in phase_b_signals]
        lines.append(f"| {alias} | " + " | ".join(cells) + " |")
    lines.append("")

    # Best-signal pick per phase
    def best(aliases: list[str], signals: list[str], prefix: str) -> tuple[str | None, float]:
        winner: tuple[str | None, float] = (None, -1.0)
        for alias in aliases:
            p = survey_dir / f"{prefix}_{alias}.json"
            if not p.is_file():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows = {r.get("signal"): r for r in data.get("rows", [])}
            best_sig = 0.0
            for sig in signals:
                r = rows.get(sig)
                if r is None:
                    continue
                da = _da_auc_from_row(r)
                if da is None:
                    continue
                best_sig = max(best_sig, abs(da - 0.5))
            if best_sig > winner[1]:
                winner = (alias, best_sig)
        return winner

    wa, wsa = best(phase_a_aliases, phase_a_signals, "survey_phaseA")
    wb, wsb = best(phase_b_aliases, phase_b_signals, "survey_phaseB")
    lines.append("## Tentative winners (highest signal strength)")
    if wa is not None:
        lines.append(f"- Phase A winner: **{wa}** (max |signal|: {wsa:.4f})")
    else:
        lines.append("- Phase A winner: (no surveys completed)")
    if wb is not None:
        lines.append(f"- Phase B winner: **{wb}** (max |signal|: {wsb:.4f})")
    else:
        lines.append("- Phase B winner: (no surveys completed)")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- CLI


def _main(argv: list[str]) -> int:
    """Thin CLI so the shell script can shell out to one of three
    subcommands rather than embedding Python heredocs:

      check-done <survey.json>          -- exit 0 if complete, 1 otherwise
      write-provenance <args.json> <out.json>
      summarize <survey_dir> <args.json> <out.md>

    The ``args.json`` for the latter two is built by the shell script
    (env vars + alias lists serialised). Keeps the bash side
    declarative — it never has to inline Python."""
    if len(argv) < 2:
        sys.stderr.write(
            "usage: _bakeoff_provenance.py "
            "check-done|write-provenance|summarize ...\n"
        )
        return 2
    cmd = argv[1]
    if cmd == "check-done":
        if len(argv) != 3:
            sys.stderr.write("usage: check-done <survey.json>\n")
            return 2
        return 0 if is_survey_complete(Path(argv[2])) else 1
    if cmd == "write-provenance":
        if len(argv) != 4:
            sys.stderr.write(
                "usage: write-provenance <args.json> <out.json>\n"
            )
            return 2
        with open(argv[2], encoding="utf-8") as f:
            raw = json.load(f)
        inputs = ProvenanceInputs(
            session_id=raw["session_id"],
            corpus_label=raw["corpus_label"],
            # 1.99.0+: forgiving on the key's absence so pre-1.99
            # caller fixtures keep working unchanged. None when
            # the shell driver didn't set _SETEC_COMPARATOR_CLASS.
            comparator_class=raw.get("comparator_class"),
            # 1.X+: same forgiving pattern for the new slice axes.
            judge=raw.get("judge"),
            generator=raw.get("generator"),
            manifest_path=Path(raw["manifest_path"]),
            phase_a_aliases=list(raw["phase_a_aliases"]),
            phase_b_aliases=list(raw["phase_b_aliases"]),
            phase_a_signals=list(raw["phase_a_signals"]),
            phase_b_signals=list(raw["phase_b_signals"]),
            phase_a_paths=dict(raw["phase_a_paths"]),
            phase_b_paths=dict(raw["phase_b_paths"]),
            max_entries=raw.get("max_entries"),
            bootstrap_engine=raw["bootstrap_engine"],
            bootstrap_resamples=int(raw["bootstrap_resamples"]),
            fpr_target=float(raw["fpr_target"]),
            cooldown_sec=int(raw["cooldown_sec"]),
            survey_dir=Path(raw["survey_dir"]),
        )
        write_provenance(
            inputs,
            repo_root=Path(raw.get("repo_root", ".")),
            out_path=Path(argv[3]),
        )
        return 0
    if cmd == "summarize":
        if len(argv) != 5:
            sys.stderr.write(
                "usage: summarize <survey_dir> <args.json> <out.md>\n"
            )
            return 2
        with open(argv[3], encoding="utf-8") as f:
            raw = json.load(f)
        text = summarize_matrix(
            Path(argv[2]),
            phase_a_aliases=list(raw["phase_a_aliases"]),
            phase_b_aliases=list(raw["phase_b_aliases"]),
            phase_a_signals=list(raw["phase_a_signals"]),
            phase_b_signals=list(raw["phase_b_signals"]),
        )
        Path(argv[4]).write_text(text, encoding="utf-8")
        return 0
    sys.stderr.write(f"unknown subcommand: {cmd}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
