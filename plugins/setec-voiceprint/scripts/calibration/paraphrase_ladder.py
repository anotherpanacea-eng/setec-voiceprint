#!/usr/bin/env python3
"""paraphrase_ladder.py — recursive-paraphrase decay-curve harness (spec 16).

Walks a *graded ladder* of paraphrase passes and reports, per SETEC
signal, the **decay curve** across rungs: how the signal's reading moves
as the same text is paraphrased again and again. This is the recursive-
paraphrase escalation DIPPER and Sadasivan describe — iterating the
paraphrase monotonically erodes stylometric signals toward the human/AI
overlap floor.

This is **orchestration over existing components**, not a new detector
and not a new signal:

  * The per-rung scoring runs ``variance_audit.audit_text`` +
    ``classify_compression`` — the SAME machinery ``pan_replay._score_text``
    uses (reused, not reimplemented).
  * The per-(signal × rung) movement is produced by
    ``adversarial_robustness_card.build_robustness_card`` (rung 0 = base,
    rungs 1..N = fixture columns); ``score_ladder`` extracts only that
    card's per-CELL ``base_value`` / ``fixture_value`` / ``relative_change``
    / ``label`` (the ``pan_replay`` cell-extraction pattern) and NEVER
    embeds the card's top-level aggregate dict.
  * The bundled stdlib *proxy* paraphraser (``build_proxy_ladder``) is
    composed from ``adversarial_fixtures.py`` primitives. It is honestly
    weaker than a neural paraphraser and is labeled ``proxy_stdlib``
    everywhere it appears — a flat proxy curve is NEVER a DIPPER result.

The harness lives on the EXISTING ``validation`` task surface, beside
``pan_replay``. Like it:

  * It emits **NO aggregate robustness or accuracy score** — no
    ``robustness_score`` / ``auc_retained`` / ``area_under_decay`` /
    ``is_robust`` / ``n_robust_signals``. The deliverable is the per-
    (signal × rung) decay curve, a list the operator reads.
  * It is **never** a selector, a calibration-threshold input, or a
    reward. It re-labels no fixture and mutates no manifest.
  * Its ``ClaimLicense`` refuses any detector-accuracy headline AND any
    claim that a signal is "robust to paraphrase", quoting Sadasivan's
    human/AI-overlap separability ceiling directly as a standing caveat.

Fixture provenance
------------------
RAID / DIPPER corpus redistribution is gated; this harness does NOT
vendor them. The bundled tiny fixture exercises the orchestration only.
Real rungs arrive via the stdlib proxy generator, the operator's own
paraphrase passes, or (M2) a GPU-gated DIPPER runner.

Fixture layout
--------------
``--fixtures DIR`` must contain a ``ladder.jsonl`` manifest. Each line is
one ladder:

    {"id": "doc01", "paraphraser": "proxy_stdlib",
     "rungs": ["<text r0=clean>", "<text r1>", "<text r2>", ...]}

Rung 0 is the clean base; each later rung is the previous rung after one
more paraphrase pass. Rungs may be supplied inline (``rungs``) or by
relative path (``rung_paths``, resolved against DIR with the same
containment hardening ``pan_replay`` ships). Lines beginning ``#`` and
blank lines are ignored.

Usage
-----
    python3 plugins/setec-voiceprint/scripts/calibration/paraphrase_ladder.py \\
        --fixtures DIR [--signals tier1.mtld,...] [--json] [--out PATH]

    # Regenerate a stdlib-proxy ladder fixture from one input:
    python3 .../paraphrase_ladder.py --build-proxy IN.txt --passes 3 \\
        --out ladder.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ``parents[1]`` is the scripts/ directory (this file lives in
# scripts/calibration/), matching pan_replay.py's bootstrap.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from output_schema import build_output  # type: ignore
from claim_license import ClaimLicense, with_state_caveats  # type: ignore
from adversarial_robustness_card import (  # type: ignore
    _VARIANCE_SIGNALS,
    build_robustness_card,
)
from variance_audit import (  # type: ignore
    audit_text,
    classify_compression,
)
from adversarial_fixtures import (  # type: ignore
    alternative_spelling,
    synonym_swap,
    whitespace,
)

TASK_SURFACE = "validation"
TOOL_NAME = "paraphrase_ladder"
SCRIPT_VERSION = "1.0"

# Honest label for the M1 stdlib proxy paraphraser. The card stamps this
# everywhere so a flat proxy curve is never read as DIPPER robustness.
PROXY_PARAPHRASER = "proxy_stdlib"

# Sadasivan's separability ceiling, quoted verbatim in the ClaimLicense
# (arXiv:2303.11156). The harness frames every flat curve against it.
_SADASIVAN_CEILING = (
    "As a paraphraser approaches the human distribution, all stylometric "
    "signals converge toward 0.5-AUROC separability (Sadasivan et al. 2023, "
    "arXiv:2303.11156); a flat decay curve here means this attack did not "
    "erode S at THIS paraphrase strength, never that S is paraphrase-robust."
)


# ---------- Fixture loading ----------


class FixtureError(Exception):
    """Raised on a missing or malformed fixtures directory."""


def _read_rung_text(
    rel: str, *, fixtures_dir: Path, ladder_id: str, rung_idx: int,
) -> str:
    """Resolve one rung's text from a relative path, with the same
    path-traversal hardening pan_replay ships (containment check BEFORE
    any read; ``..`` and absolute paths are rejected; the secret file's
    contents never appear in the error)."""
    base = fixtures_dir.resolve()
    path = (fixtures_dir / rel).resolve()
    if not path.is_relative_to(base):
        raise FixtureError(
            f"ladder {ladder_id!r} rung {rung_idx}: rung_path={rel!r} "
            f"resolves outside the fixtures directory ({base}); refusing "
            f"to read it"
        )
    if not path.is_file():
        raise FixtureError(
            f"ladder {ladder_id!r} rung {rung_idx}: rung_path={rel!r} does "
            f"not resolve to a file under {fixtures_dir}"
        )
    return path.read_text(encoding="utf-8", errors="ignore")


def load_ladders(fixtures_dir: str | Path) -> list[dict[str, Any]]:
    """Load ladders from ``DIR/ladder.jsonl``.

    Each returned ladder is a dict with keys ``id``, ``paraphraser``, and
    ``rungs`` (a list of >= 2 texts; rung 0 is the clean base). Raises
    ``FixtureError`` on a missing directory / manifest or a malformed
    entry.
    """
    if fixtures_dir is None:
        raise FixtureError("no --fixtures DIR supplied")
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.exists():
        raise FixtureError(f"--fixtures directory does not exist: {fixtures_dir}")
    if not fixtures_dir.is_dir():
        raise FixtureError(f"--fixtures is not a directory: {fixtures_dir}")
    manifest = fixtures_dir / "ladder.jsonl"
    if not manifest.is_file():
        raise FixtureError(
            f"--fixtures directory has no ladder.jsonl manifest: {manifest}. "
            f"Each line must be a JSON object with a paraphraser label and "
            f"rungs (inline 'rungs' list or 'rung_paths' relative paths); "
            f"rung 0 is the clean base."
        )

    ladders: list[dict[str, Any]] = []
    for lineno, raw in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FixtureError(
                f"{manifest} line {lineno} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(entry, dict):
            raise FixtureError(
                f"{manifest} line {lineno} is not a JSON object"
            )
        ladder_id = entry.get("id") or f"line_{lineno}"
        paraphraser = entry.get("paraphraser")
        if not isinstance(paraphraser, str) or not paraphraser:
            raise FixtureError(
                f"{manifest} line {lineno} is missing a string 'paraphraser' "
                f"label"
            )

        rungs: list[str]
        if "rungs" in entry:
            rungs = entry["rungs"]
            if not isinstance(rungs, list) or not all(
                isinstance(r, str) for r in rungs
            ):
                raise FixtureError(
                    f"{manifest} line {lineno}: 'rungs' must be a list of "
                    f"strings"
                )
        elif "rung_paths" in entry:
            paths = entry["rung_paths"]
            if not isinstance(paths, list) or not all(
                isinstance(p, str) for p in paths
            ):
                raise FixtureError(
                    f"{manifest} line {lineno}: 'rung_paths' must be a list "
                    f"of strings"
                )
            rungs = [
                _read_rung_text(
                    p, fixtures_dir=fixtures_dir,
                    ladder_id=ladder_id, rung_idx=i,
                )
                for i, p in enumerate(paths)
            ]
        else:
            raise FixtureError(
                f"{manifest} line {lineno} is missing both 'rungs' (inline "
                f"texts) and 'rung_paths' (relative paths)"
            )

        if len(rungs) < 2:
            raise FixtureError(
                f"{manifest} line {lineno}: a ladder needs >= 2 rungs (a "
                f"clean base plus at least one paraphrase pass); got "
                f"{len(rungs)}"
            )
        ladders.append({
            "id": ladder_id,
            "paraphraser": paraphraser,
            "rungs": rungs,
        })
    if not ladders:
        raise FixtureError(
            f"{manifest} contained no usable ladders"
        )
    return ladders


# ---------- Stdlib proxy paraphraser ----------


def _proxy_pass(text: str) -> str:
    """One deterministic stdlib paraphrase-proxy pass.

    Composes adversarial_fixtures primitives (synonym swap +
    alternative-spelling + whitespace insertion). Honestly weaker than a
    neural paraphraser — it exercises the LADDER MECHANICS, not realistic
    paraphrase quality."""
    return whitespace(alternative_spelling(synonym_swap(text)))


def build_proxy_ladder(
    text: str, *, passes: int, paraphraser: str = PROXY_PARAPHRASER,
) -> dict[str, Any]:
    """Build a deterministic stdlib-proxy ladder of ``passes + 1`` rungs.

    Rung 0 is the input text; each later rung is the previous rung after
    one more proxy pass. Byte-identical across calls (no RNG, no model).
    Labeled ``proxy_stdlib`` so a flat proxy curve is never mistaken for a
    DIPPER-grade result.
    """
    if passes < 1:
        raise ValueError("passes must be >= 1")
    rungs = [text]
    cur = text
    for _ in range(passes):
        cur = _proxy_pass(cur)
        rungs.append(cur)
    return {
        "id": "proxy",
        "paraphraser": paraphraser,
        "rungs": rungs,
    }


# ---------- Signal scoring (reuses pan_replay's machinery) ----------


def _score_text(text: str) -> dict[str, Any]:
    """Run the surface-tagged signals on one rung, returning the JSON
    shape the robustness card consumes. Identical contract to
    ``pan_replay._score_text``: audit_text + classify_compression,
    Tier 4 OFF (CPU, no-model)."""
    audit = audit_text(text, do_tier2=True, do_tier3=True, do_tier4=False)
    compression = classify_compression(audit)
    return {"audit": audit, "compression": compression}


def _is_monotone(rels: list[float | None]) -> bool:
    """Descriptive: is the sequence of |relative_change| non-decreasing?

    Observed, NEVER enforced. Missing readings (None) break the chain and
    yield ``False`` (the curve is not cleanly monotone if a rung could not
    be scored)."""
    vals = [abs(r) for r in rels if isinstance(r, (int, float))]
    if len(vals) != len(rels) or len(vals) < 2:
        return False
    return all(vals[i] <= vals[i + 1] + 1e-12 for i in range(len(vals) - 1))


# ---------- Score one ladder ----------


def score_ladder(
    ladder: dict[str, Any],
    *,
    signals: list[str] | None = None,
    stability_threshold: float = 0.10,
    fragile_threshold: float = 0.30,
) -> dict[str, Any]:
    """Score one ladder into a per-signal decay curve.

    Re-runs ``_score_text`` on every rung, then builds ONE robustness card
    (base = rung 0, fixtures = rungs 1..N as columns ``rung_1``..``rung_N``)
    and extracts the per-CELL ``base_value`` / ``fixture_value`` /
    ``relative_change`` / ``card_label`` (the pan_replay pattern). The
    card's top-level aggregate dict (``n_robust_signals`` /
    ``overall_robustness`` / ...) is deliberately DISCARDED and never
    embedded in the result.
    """
    rungs = ladder["rungs"]
    n_rungs = len(rungs)

    known_signals = list(_VARIANCE_SIGNALS.keys())
    if signals:
        selected_signals = [s for s in signals if s in _VARIANCE_SIGNALS]
    else:
        selected_signals = list(known_signals)

    base_scored = _score_text(rungs[0])
    fixture_cols: list[tuple[str, dict[str, Any]]] = [
        (f"rung_{i}", _score_text(rungs[i])) for i in range(1, n_rungs)
    ]
    card = build_robustness_card(
        base=base_scored,
        fixtures=fixture_cols,
        stability_threshold=stability_threshold,
        fragile_threshold=fragile_threshold,
    )

    per_signal: dict[str, Any] = {}
    for sig in selected_signals:
        sig_info = card["per_signal"].get(sig, {})
        per_fixture = sig_info.get("per_fixture", {})
        decay: list[dict[str, Any]] = []
        per_rung_label: list[str] = []
        rels: list[float | None] = []
        for i in range(1, n_rungs):
            cell = per_fixture.get(f"rung_{i}", {})
            rel = cell.get("relative_change")
            cell_label = cell.get("label", "unknown")
            # Per-CELL extraction only — never the card's aggregate dict.
            decay.append({
                "rung": i,
                "base_value": cell.get("base_value"),
                "rung_value": cell.get("fixture_value"),
                "relative_change": rel,
                "card_label": cell_label,
            })
            per_rung_label.append(cell_label)
            rels.append(rel)
        per_signal[sig] = {
            "decay": decay,
            "per_rung_label": per_rung_label,
            "monotone": _is_monotone(rels),
        }

    return {
        "id": ladder["id"],
        "paraphraser": ladder["paraphraser"],
        "n_rungs": n_rungs,
        "signals": selected_signals,
        "per_signal": per_signal,
    }


def score_ladders(
    ladders: list[dict[str, Any]],
    *,
    signals: list[str] | None = None,
    stability_threshold: float = 0.10,
    fragile_threshold: float = 0.30,
) -> dict[str, Any]:
    """Score every ladder and assemble the no-aggregate result payload."""
    known_signals = list(_VARIANCE_SIGNALS.keys())
    if signals:
        selected_signals = [s for s in signals if s in _VARIANCE_SIGNALS]
        unknown = [s for s in signals if s not in _VARIANCE_SIGNALS]
    else:
        selected_signals = list(known_signals)
        unknown = []

    warnings: list[str] = []
    if unknown:
        warnings.append(
            "Ignored unknown --signals (not in the robustness-card signal "
            f"set): {', '.join(sorted(unknown))}"
        )

    scored = [
        score_ladder(
            ladder, signals=selected_signals,
            stability_threshold=stability_threshold,
            fragile_threshold=fragile_threshold,
        )
        for ladder in ladders
    ]
    # The reported paraphraser label is the ladders' shared label when
    # they agree; otherwise "mixed" (descriptive, never a score). Each
    # ladder also carries its own label.
    labels = {l["paraphraser"] for l in ladders}
    paraphraser = next(iter(labels)) if len(labels) == 1 else "mixed"
    rung_counts = {l["n_rungs"] for l in scored}

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "paraphraser": paraphraser,
        "signals": selected_signals,
        "n_ladders": len(scored),
        "n_rungs": max(rung_counts) if rung_counts else 0,
        "stability_threshold": stability_threshold,
        "fragile_threshold": fragile_threshold,
        "ladders": scored,
        "warnings": warnings,
    }


# ---------- ClaimLicense ----------


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    signals = result.get("signals") or []
    paraphraser = result.get("paraphraser") or "(unknown)"
    caveats = [
        "Monotonicity is OBSERVED, not enforced: a non-monotone decay "
        "curve (real paraphrase noise) scores normally with `monotone: "
        "False`; the harness never clips a curve to look clean.",
        "No per-rung Δ is a retention threshold. Comparing Δ across "
        "signals or rungs to RANK robustness, or gating on a Δ > k cut, "
        "is NOT licensed — there is deliberately no aggregate field to "
        "threshold.",
        "Stability / collapse thresholds are heuristic (default ± 10% / "
        "30% relative change), inherited from the reused "
        "adversarial_robustness_card.",
        "Tier 4 surprisal signals are not exercised here: the ladder runs "
        "CPU-only Tier 1-3 signals (no model loads).",
        _SADASIVAN_CEILING,
    ]
    if paraphraser == PROXY_PARAPHRASER:
        caveats.append(
            "This ladder's paraphraser is `proxy_stdlib`: a deterministic "
            "lexical/spelling/whitespace proxy, NOT a neural paraphraser. A "
            "flat proxy curve says NOTHING about DIPPER-grade attacks "
            "(arXiv:2303.13408); the realistic recursive paraphrase is the "
            "GPU-gated M2 runner."
        )
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "For each SETEC signal S, the per-rung relative change Δ_i in S "
            "after i paraphrase passes by paraphraser P on these ladder "
            "fixtures — a descriptive per-(signal × rung) decay curve plus a "
            "per-cell stability label (stable / moderate / fragile / ...). "
            "The card licenses 'signal S shows relative change Δ_i at rung i "
            "under paraphraser P on this fixture' — a robustness observation "
            "about SETEC's own signals under recursive transformation."
        ),
        does_not_license=(
            "Any detector-accuracy headline (there is no detector here to "
            "survive an attack), any aggregate robustness or accuracy score "
            "(no AUC / AUROC-retained / area-under-decay / single robustness "
            "number is emitted), and — load-bearing — any claim that a "
            "signal is ROBUST TO PARAPHRASE. A flat decay curve means the "
            "attack did not erode S at THIS paraphrase strength, never that "
            "S is paraphrase-robust: " + _SADASIVAN_CEILING
        ),
        comparison_set={
            "fixture_provenance": (
                "Recursive-paraphrase ladder fixtures. RAID / DIPPER corpus "
                "redistribution is gated — this harness does NOT vendor them; "
                "the bundled fixture is a tiny synthetic stand-in for the "
                "orchestration only. Rungs come from the stdlib proxy "
                "generator, the operator's own paraphrase passes, or the "
                "GPU-gated M2 DIPPER runner."
            ),
            "paraphraser": paraphraser,
            "n_signals": len(signals),
            "n_ladders": result.get("n_ladders", 0),
            "n_rungs": result.get("n_rungs", 0),
            "stability_threshold": result.get("stability_threshold"),
            "fragile_threshold": result.get("fragile_threshold"),
        },
        additional_caveats=caveats,
        references=[
            "https://arxiv.org/abs/2405.07940",  # RAID
            "https://arxiv.org/abs/2303.13408",  # DIPPER
            "https://arxiv.org/abs/2303.11156",  # Sadasivan separability ceiling
        ],
    )
    return with_state_caveats(lic, target_ai_status=result.get("ai_status"))


def build_audit_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Wrap the ladder result in the schema_version 1.0 envelope."""
    metadata_keys = {"task_surface", "tool", "version", "warnings"}
    results_payload = {
        k: v for k, v in result.items() if k not in metadata_keys
    }
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=None,
        target_words=0,
        baseline=None,
        results=results_payload,
        claim_license=_claim_license(result),
        warnings=result.get("warnings") or [],
        ai_status=result.get("ai_status"),
    )


# ---------- Markdown rendering ----------


_LABEL_GLYPH = {
    "stable": "✓",
    "moderate": "·",
    "fragile": "✗",
    "inverted_polarity": "↺",
    "small_base": "?",
    "unstable_small_base": "!",
    "unknown": "—",
}


def render_report(result: dict[str, Any]) -> str:
    signals = result.get("signals") or []
    ladders = result.get("ladders", [])
    paraphraser = result.get("paraphraser", "(unknown)")

    lines: list[str] = [
        "# Recursive-paraphrase decay-curve card",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Paraphraser:** `{paraphraser}`",
        f"**Ladders:** {result.get('n_ladders', 0)}  "
        f"**Max rungs:** {result.get('n_rungs', 0)}  "
        f"**Signals:** {len(signals)}",
        "",
        "No aggregate robustness or accuracy score is emitted; the "
        "deliverable is the per-(signal × rung) decay curve below.",
        "",
        "Glyph legend: ✓ stable, · moderate, ✗ fragile, ↺ inverted "
        "polarity, ? small base, ! unstable small base, — unknown.",
        "",
    ]

    if not ladders:
        lines.append("_(No ladders present in the fixtures.)_")
        lines.append("")
    for ladder in ladders:
        lines.append(f"## Ladder `{ladder['id']}` "
                     f"(paraphraser `{ladder['paraphraser']}`, "
                     f"{ladder['n_rungs']} rungs)")
        lines.append("")
        header = ["signal", "monotone"] + [
            f"rung_{i}" for i in range(1, ladder["n_rungs"])
        ]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for sig in signals:
            block = ladder["per_signal"].get(sig, {})
            row = [sig, str(block.get("monotone", False))]
            for cell in block.get("decay", []):
                glyph = _LABEL_GLYPH.get(cell.get("card_label"), "—")
                rel = cell.get("relative_change")
                if isinstance(rel, (int, float)):
                    row.append(f"{glyph} ({rel:+.1%})")
                else:
                    row.append(glyph)
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append(_claim_license(result).render_block().rstrip())
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paraphrase_ladder.py",
        description=(
            "Walk a recursive-paraphrase ladder and emit a per-(signal × "
            "rung) decay curve for SETEC's existing signals. Not a detector; "
            "emits no aggregate score. The M1 paraphraser is a stdlib proxy "
            "(`proxy_stdlib`); a realistic DIPPER ladder is the gated M2 seam."
        ),
    )
    p.add_argument(
        "--fixtures",
        default=None,
        help=(
            "Directory containing a ladder.jsonl manifest. Each line is one "
            "ladder: {id, paraphraser, rungs:[...] | rung_paths:[...]}; rung "
            "0 is the clean base."
        ),
    )
    p.add_argument(
        "--signals",
        default=None,
        help=(
            "Comma-separated signal names to report (default: all signals "
            "the robustness card knows)."
        ),
    )
    p.add_argument(
        "--stability-threshold", type=float, default=0.10,
        help="Relative-change |Δ| below which a rung is `stable` "
             "(default 10%%).",
    )
    p.add_argument(
        "--fragile-threshold", type=float, default=0.30,
        help="Relative-change |Δ| above which a rung is `fragile` "
             "(default 30%%).",
    )
    p.add_argument(
        "--build-proxy",
        default=None,
        metavar="IN.txt",
        help=(
            "Regenerate a stdlib-proxy ladder fixture from one input text "
            "and write a ladder.jsonl line to --out (or stdout). Honestly "
            "labeled proxy_stdlib."
        ),
    )
    p.add_argument(
        "--passes", type=int, default=3,
        help="Number of proxy paraphrase passes for --build-proxy "
             "(default 3; emits passes+1 rungs).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the fixtures; when supplied, the "
            "ClaimLicense block gains state-specific caveats."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # --build-proxy mode: regenerate a stdlib-proxy ladder fixture.
    if args.build_proxy:
        try:
            text = Path(args.build_proxy).read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError as exc:
            sys.stderr.write(f"--build-proxy: {exc}\n")
            return 2
        try:
            ladder = build_proxy_ladder(text, passes=args.passes)
        except ValueError as exc:
            sys.stderr.write(f"--build-proxy: {exc}\n")
            return 2
        line = json.dumps(ladder, ensure_ascii=False) + "\n"
        if args.out:
            Path(args.out).write_text(line, encoding="utf-8")
            sys.stderr.write(f"Wrote proxy ladder to {args.out}\n")
        else:
            sys.stdout.write(line)
        return 0

    if not args.fixtures:
        sys.stderr.write(
            "--fixtures DIR is required (or use --build-proxy IN.txt)\n"
        )
        return 2

    try:
        ladders = load_ladders(args.fixtures)
    except FixtureError as exc:
        sys.stderr.write(f"--fixtures: {exc}\n")
        return 2

    result = score_ladders(
        ladders,
        signals=_split_csv(args.signals),
        stability_threshold=args.stability_threshold,
        fragile_threshold=args.fragile_threshold,
    )
    if args.ai_status:
        result["ai_status"] = args.ai_status

    if args.json:
        out = json.dumps(build_audit_payload(result), indent=2, default=str)
    else:
        out = render_report(result)

    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
