#!/usr/bin/env python3
"""pan_replay.py — PAN@CLEF obfuscation-replay harness (spec 04).

Replays SETEC's *existing* signals over PAN@CLEF obfuscation fixtures
and emits a per-signal × per-obfuscation-class **robustness card**:
for each (signal, obfuscation class) the relative change between the
clean reading and the obfuscated reading, plus a stable / degraded /
collapsed-style tag. This is **orchestration over existing
components**, not a new detector:

  * The per-(clean, obfuscated) scoring runs ``variance_audit.audit_text``
    + ``classify_compression`` — the same machinery the validation
    harness (``validation_harness.score_smoothing_entry``) uses to
    score a single entry.
  * The per-signal robustness shape is produced by
    ``adversarial_robustness_card.build_robustness_card`` — reused
    verbatim so the output conforms to that card's contract (the spec
    requires ``test_robustness_card_reuse``).
  * The PAN obfuscation classes (unicode / paraphrase / lang_switch /
    short) name the same adversarial territory as
    ``adversarial_fixtures.py``; the unicode class is exactly the
    homoglyph / zero-width / soft-hyphen tokenizer-layer attacks that
    module ships.

The harness lives on the EXISTING ``validation`` task surface. It
emits NO aggregate robustness or accuracy score — the deliverable is
the per-(signal × class) card, and the ClaimLicense block refuses any
detector-accuracy headline (per spec §Contract / Calibration posture).

Fixture provenance
------------------
PAN@CLEF data redistribution is gated. This harness does NOT vendor
real PAN data. To bring the real fixtures, a PAN-account-gated,
local-only fetcher is the follow-up (mirroring
``fetch_pangram_editlens.py``'s posture: download locally, write a
NOTICE.md with attribution + redistribution prohibition, never commit
the corpus). Until then the harness reads whatever (clean, obfuscated)
pairs the operator places in ``--fixtures DIR``; the bundled tiny
synthetic fixture under ``scripts/test_data/pan_replay_fixture/``
exercises the orchestration without standing in for PAN content.

Fixture layout
--------------
``--fixtures DIR`` must contain a ``pairs.jsonl`` manifest. Each line
is one (clean, obfuscated) pair:

    {"id": "doc01", "obfuscation_class": "unicode",
     "clean": "<clean text>", "obfuscated": "<obfuscated text>"}

Text may be supplied inline (``clean`` / ``obfuscated``) or by relative
path (``clean_path`` / ``obfuscated_path``, resolved against DIR). The
``obfuscation_class`` is one of the class names (unicode / paraphrase /
lang_switch / short by default). Lines beginning ``#`` and blank lines
are ignored.

Usage
-----
    python3 plugins/setec-voiceprint/scripts/calibration/pan_replay.py \\
        --fixtures DIR \\
        [--classes unicode,paraphrase,lang_switch,short] \\
        [--signals tier1.mtld,tier1.mattr.value] \\
        [--json] [--out PATH]

``--classes`` restricts which obfuscation classes are replayed (default:
every class present in the manifest, intersected with the known class
vocabulary). ``--signals`` restricts which signals are reported (default:
all signals the robustness card knows). ``--json`` emits the schema_version
1.0 envelope; otherwise a markdown report. ``--out PATH`` writes to a file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Resolve the scripts/ dir for sibling imports. ``parents[1]`` is the
# scripts/ directory (this file lives in scripts/calibration/), matching
# the bootstrap in calibrate_thresholds.py and the other calibration
# scripts. ``parents[4]`` is the repo root in dev / marketplace install.
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
    split_words,
)

TASK_SURFACE = "validation"
TOOL_NAME = "pan_replay"
SCRIPT_VERSION = "1.0"

# The PAN obfuscation-class vocabulary. Aligned with the brief and with
# the adversarial-class territory of ``adversarial_fixtures.py`` (the
# unicode class is exactly that module's homoglyph / zero-width /
# soft-hyphen tokenizer attacks).
DEFAULT_CLASSES = ("unicode", "paraphrase", "lang_switch", "short")

# Robustness tags. The spec's vocabulary is stable / degraded /
# collapsed; the reused robustness card produces stable / moderate /
# fragile / inverted_polarity / small_base / unstable_small_base /
# unknown. We map the card's per-cell label onto the spec's tag so the
# card stays the source of truth and the spec's vocabulary is honored.
_CARD_LABEL_TO_TAG = {
    "stable": "stable",
    "moderate": "degraded",
    "fragile": "collapsed",
    "inverted_polarity": "collapsed",
    "unstable_small_base": "collapsed",
    "small_base": "stable",
    "unknown": "unknown",
}


def _spec_tag(card_label: str) -> str:
    """Map a robustness-card cell label to the spec's stable / degraded
    / collapsed tag vocabulary."""
    return _CARD_LABEL_TO_TAG.get(card_label, "unknown")


# ---------- Fixture loading ----------


class FixtureError(Exception):
    """Raised on a missing or malformed fixtures directory."""


def _read_pair_text(
    entry: dict[str, Any], key_inline: str, key_path: str, *, fixtures_dir: Path,
) -> str:
    """Resolve a pair's clean/obfuscated text from inline value or
    relative path."""
    if key_inline in entry and isinstance(entry[key_inline], str):
        return entry[key_inline]
    if key_path in entry and isinstance(entry[key_path], str):
        rel = entry[key_path]
        base = fixtures_dir.resolve()
        path = (fixtures_dir / rel).resolve()
        # Containment check BEFORE touching the filesystem. ``..`` segments
        # or an absolute path would otherwise let a manifest read files
        # outside the fixture bundle — and these fixtures may eventually
        # come from a gated local download, so a manifest must not be able
        # to exfiltrate arbitrary files. ``.resolve()`` also collapses
        # symlinks, so a symlink escape is caught here too.
        if not path.is_relative_to(base):
            raise FixtureError(
                f"pair {entry.get('id', '?')!r}: {key_path}={rel!r} resolves "
                f"outside the fixtures directory ({base}); refusing to read it"
            )
        if not path.is_file():
            raise FixtureError(
                f"pair {entry.get('id', '?')!r}: {key_path}={rel!r} does not "
                f"resolve to a file under {fixtures_dir}"
            )
        return path.read_text(encoding="utf-8", errors="ignore")
    raise FixtureError(
        f"pair {entry.get('id', '?')!r} is missing both {key_inline!r} "
        f"(inline text) and {key_path!r} (relative path)"
    )


def load_fixture_pairs(fixtures_dir: str | Path) -> list[dict[str, Any]]:
    """Load (clean, obfuscated) pairs from ``DIR/pairs.jsonl``.

    Each returned pair is a dict with keys ``id``, ``obfuscation_class``,
    ``clean`` (text), ``obfuscated`` (text). Raises ``FixtureError`` on a
    missing directory, missing manifest, or a malformed entry.
    """
    if fixtures_dir is None:
        raise FixtureError("no --fixtures DIR supplied")
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.exists():
        raise FixtureError(f"--fixtures directory does not exist: {fixtures_dir}")
    if not fixtures_dir.is_dir():
        raise FixtureError(f"--fixtures is not a directory: {fixtures_dir}")
    manifest = fixtures_dir / "pairs.jsonl"
    if not manifest.is_file():
        raise FixtureError(
            f"--fixtures directory has no pairs.jsonl manifest: {manifest}. "
            f"Each line must be a JSON object with an obfuscation_class and "
            f"clean / obfuscated text (inline or via *_path)."
        )

    pairs: list[dict[str, Any]] = []
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
        obf_class = entry.get("obfuscation_class")
        if not isinstance(obf_class, str) or not obf_class:
            raise FixtureError(
                f"{manifest} line {lineno} is missing a string "
                f"'obfuscation_class'"
            )
        pair_id = entry.get("id") or f"line_{lineno}"
        clean = _read_pair_text(
            entry, "clean", "clean_path", fixtures_dir=fixtures_dir
        )
        obfuscated = _read_pair_text(
            entry, "obfuscated", "obfuscated_path", fixtures_dir=fixtures_dir
        )
        pairs.append({
            "id": pair_id,
            "obfuscation_class": obf_class,
            "clean": clean,
            "obfuscated": obfuscated,
        })
    if not pairs:
        raise FixtureError(
            f"{manifest} contained no usable (clean, obfuscated) pairs"
        )
    return pairs


# ---------- Signal scoring (reuses the validation-harness machinery) ----------


def _score_text(text: str) -> dict[str, Any]:
    """Run the surface-tagged signals on one text, returning the JSON
    shape the robustness card consumes.

    This is the same audit_text + classify_compression path that
    ``validation_harness.score_smoothing_entry`` runs per entry; we wrap
    the result as ``{"audit": <audit>, "compression": <compression>}``
    because ``adversarial_robustness_card._extract_all_signals`` walks
    ``audit.tier1...`` and ``compression.compression_fraction``.

    Tier 4 (surprisal) is left OFF: this is the CPU, no-model contract.
    """
    audit = audit_text(text, do_tier2=True, do_tier3=True, do_tier4=False)
    compression = classify_compression(audit)
    return {"audit": audit, "compression": compression}


# ---------- Replay ----------


def replay(
    pairs: list[dict[str, Any]],
    *,
    classes: list[str] | None = None,
    signals: list[str] | None = None,
    stability_threshold: float = 0.10,
    fragile_threshold: float = 0.30,
) -> dict[str, Any]:
    """Replay the signals over each (clean, obfuscated) pair and build
    the per-(signal × class) robustness card.

    Per-class slicing is strict: each obfuscation class is scored on its
    OWN pairs only (no cross-class mixing). Within a class, every pair's
    obfuscated reading is a fixture against its own clean base reading;
    the per-cell labels are aggregated per (signal, class) into the
    spec's stable / degraded / collapsed tag.
    """
    known_signals = list(_VARIANCE_SIGNALS.keys())
    if signals:
        selected_signals = [s for s in signals if s in _VARIANCE_SIGNALS]
        unknown = [s for s in signals if s not in _VARIANCE_SIGNALS]
    else:
        selected_signals = list(known_signals)
        unknown = []

    # Group pairs by obfuscation class — strict per-class slicing.
    by_class: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        by_class.setdefault(pair["obfuscation_class"], []).append(pair)

    present_classes = sorted(by_class.keys())
    if classes:
        selected_classes = [c for c in classes if c in by_class]
        requested_absent = [c for c in classes if c not in by_class]
    else:
        selected_classes = present_classes
        requested_absent = []

    warnings: list[str] = []
    if unknown:
        warnings.append(
            "Ignored unknown --signals (not in the robustness-card signal "
            f"set): {', '.join(sorted(unknown))}"
        )
    if requested_absent:
        warnings.append(
            "Requested --classes absent from the fixtures: "
            f"{', '.join(sorted(requested_absent))}"
        )

    # Per-class robustness card. Each class is computed in isolation:
    # the card for class C only sees class C's pairs. Each pair within
    # the class contributes one (clean base, obfuscated fixture) cell,
    # labeled by the pair id so the card never mixes pairs.
    per_class_cards: dict[str, dict[str, Any]] = {}
    per_class_n_pairs: dict[str, int] = {}
    for obf_class in selected_classes:
        class_pairs = by_class[obf_class]
        per_class_n_pairs[obf_class] = len(class_pairs)
        # Score each pair. Within a class we build one robustness card
        # per pair (base=clean, fixture=obfuscated under the pair id),
        # then merge the per-signal cells across the class's pairs.
        per_signal_cells: dict[str, dict[str, Any]] = {
            sig: {} for sig in selected_signals
        }
        for pair in class_pairs:
            base_scored = _score_text(pair["clean"])
            obf_scored = _score_text(pair["obfuscated"])
            card = build_robustness_card(
                base=base_scored,
                fixtures=[(pair["id"], obf_scored)],
                stability_threshold=stability_threshold,
                fragile_threshold=fragile_threshold,
            )
            for sig in selected_signals:
                sig_info = card["per_signal"].get(sig, {})
                cell = sig_info.get("per_fixture", {}).get(pair["id"], {})
                per_signal_cells[sig][pair["id"]] = {
                    "base_value": cell.get("base_value"),
                    "obfuscated_value": cell.get("fixture_value"),
                    "relative_change": cell.get("relative_change"),
                    "card_label": cell.get("label", "unknown"),
                    "tag": _spec_tag(cell.get("label", "unknown")),
                }

        # Aggregate per (signal, class): the class-level tag escalates
        # to the worst tag seen across the class's pairs
        # (collapsed > degraded > stable), mirroring the robustness
        # card's "any fragile reading ⇒ fragile overall" rule.
        per_signal_summary: dict[str, Any] = {}
        for sig in selected_signals:
            cells = per_signal_cells[sig]
            tags = [c["tag"] for c in cells.values() if c["tag"] != "unknown"]
            rels = [
                c["relative_change"] for c in cells.values()
                if isinstance(c.get("relative_change"), (int, float))
            ]
            if not tags:
                class_tag = "unknown"
            elif "collapsed" in tags:
                class_tag = "collapsed"
            elif "degraded" in tags:
                class_tag = "degraded"
            else:
                class_tag = "stable"
            # Mean relative change across the class's pairs (descriptive
            # only — not an aggregate robustness SCORE; it's the per-cell
            # delta averaged within one (signal, class) slice).
            mean_delta = (
                round(sum(rels) / len(rels), 4) if rels else None
            )
            per_signal_summary[sig] = {
                "tag": class_tag,
                "mean_relative_change": mean_delta,
                "n_pairs": len(cells),
                "per_pair": cells,
            }

        per_class_cards[obf_class] = {
            "n_pairs": len(class_pairs),
            "per_signal": per_signal_summary,
        }

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "classes": selected_classes,
        "signals": selected_signals,
        "n_pairs_total": sum(per_class_n_pairs.values()),
        "n_pairs_by_class": per_class_n_pairs,
        "stability_threshold": stability_threshold,
        "fragile_threshold": fragile_threshold,
        "per_class": per_class_cards,
        "warnings": warnings,
    }


# ---------- ClaimLicense ----------


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    classes = result.get("classes") or []
    signals = result.get("signals") or []
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "For each SETEC signal S and PAN obfuscation class C, the "
            "relative change Δ in S between the clean reading and the "
            "obfuscated reading on these fixtures, with a per-(signal × "
            "class) robustness tag (stable / degraded / collapsed). The "
            "card licenses the statement 'signal S degrades by Δ under "
            "obfuscation class C on the PAN fixtures' — a robustness "
            "observation about SETEC's own signals under transformation."
        ),
        does_not_license=(
            "Any detector-accuracy headline. This is NOT a detector and "
            "emits NO aggregate robustness or accuracy score: it does not "
            "report AUC, TPR/FPR, or a single 'robustness number,' and it "
            "does not license a provenance verdict for any document. A "
            "signal stable under one fixture's obfuscation may collapse "
            "under a stronger obfuscator; the card is fixture-specific."
        ),
        comparison_set={
            "fixture_provenance": (
                "PAN@CLEF obfuscation fixtures (Generative-AI Authorship "
                "Verification; PAN24/25). PAN data redistribution is "
                "gated — real fixtures are brought via a PAN-account-"
                "gated, local-only fetcher (follow-up, modeled on "
                "fetch_pangram_editlens.py); the bundled fixture is a "
                "tiny synthetic stand-in for the orchestration only and "
                "is NOT PAN content."
            ),
            "obfuscation_classes": ", ".join(classes) or "(none)",
            "n_signals": len(signals),
            "n_pairs_total": result.get("n_pairs_total", 0),
            "stability_threshold": result.get("stability_threshold"),
            "fragile_threshold": result.get("fragile_threshold"),
        },
        additional_caveats=[
            "Per-class slicing is strict: each obfuscation class is "
            "scored on its own (clean, obfuscated) pairs only; no "
            "cross-class mixing.",
            "Stability / collapse thresholds are heuristic (default "
            "± 10% / 30% relative change), inherited from the reused "
            "adversarial_robustness_card. Calibration-pending against a "
            "labeled PAN slice.",
            "Tier 4 surprisal signals are not exercised here: the replay "
            "runs CPU-only Tier 1–3 signals (no model loads).",
        ],
        references=[
            "https://pan.webis.de/clef24/pan24-web/generated-content-analysis.html",
        ],
    )
    return with_state_caveats(lic, target_ai_status=result.get("ai_status"))


def build_audit_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Wrap the replay result in the schema_version 1.0 envelope."""
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


_TAG_GLYPH = {
    "stable": "✓",
    "degraded": "·",
    "collapsed": "✗",
    "unknown": "—",
}


def render_report(result: dict[str, Any]) -> str:
    classes = result.get("classes") or []
    signals = result.get("signals") or []
    per_class = result.get("per_class", {})

    lines: list[str] = [
        "# PAN obfuscation-replay robustness card",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Obfuscation classes:** {', '.join(classes) or '(none)'}",
        f"**Pairs:** {result.get('n_pairs_total', 0)} "
        f"({', '.join(f'{c}={n}' for c, n in (result.get('n_pairs_by_class') or {}).items()) or 'none'})",
        f"**Signals:** {len(signals)}",
        "",
        "No aggregate robustness or accuracy score is emitted; the "
        "deliverable is the per-(signal × class) card below.",
        "",
        "Glyph legend: ✓ stable, · degraded, ✗ collapsed, — unknown.",
        "",
    ]

    if not classes:
        lines.append(
            "_(No obfuscation classes present in the fixtures. Add "
            "(clean, obfuscated) pairs to pairs.jsonl.)_"
        )
        lines.append("")
    else:
        header = ["signal"] + list(classes)
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for sig in signals:
            row = [sig]
            for obf_class in classes:
                cls_block = per_class.get(obf_class, {})
                sig_block = cls_block.get("per_signal", {}).get(sig, {})
                tag = sig_block.get("tag", "unknown")
                rel = sig_block.get("mean_relative_change")
                glyph = _TAG_GLYPH.get(tag, "—")
                if isinstance(rel, (int, float)):
                    row.append(f"{glyph} {tag} ({rel:+.1%})")
                else:
                    row.append(f"{glyph} {tag}")
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
        prog="pan_replay.py",
        description=(
            "Replay SETEC's existing signals over PAN@CLEF obfuscation "
            "fixtures and emit a per-(signal × obfuscation-class) "
            "robustness card (deltas + stable/degraded/collapsed tags). "
            "Not a detector; emits no aggregate score."
        ),
    )
    p.add_argument(
        "--fixtures",
        required=True,
        help=(
            "Directory containing a pairs.jsonl manifest of (clean, "
            "obfuscated) text pairs, each tagged with an obfuscation_class."
        ),
    )
    p.add_argument(
        "--classes",
        default=None,
        help=(
            "Comma-separated obfuscation classes to replay "
            f"(default: all present; vocabulary: {','.join(DEFAULT_CLASSES)})."
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
        help="Relative-change |Δ| below which a signal is `stable` "
             "(default 10%%).",
    )
    p.add_argument(
        "--fragile-threshold", type=float, default=0.30,
        help="Relative-change |Δ| above which a signal is `collapsed` "
             "(default 30%%).",
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

    try:
        pairs = load_fixture_pairs(args.fixtures)
    except FixtureError as exc:
        sys.stderr.write(f"--fixtures: {exc}\n")
        return 2

    result = replay(
        pairs,
        classes=_split_csv(args.classes),
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
