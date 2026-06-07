#!/usr/bin/env python3
"""triage_agreement.py — framework-vs-human triage agreement (validation).

Closes the loop the framework keeps open on principle. SETEC's load-bearing claim
is that "source triage is judgment work, not algorithm, and most surface flags
resolve as earned on triage." This audit measures how well the framework's surfaced
candidates agree with an operator's triage judgments on a labeled item set, and
reports it as a number: confusion matrix, percent agreement, Cohen's kappa, the
prevalence-and-bias-adjusted kappa (PABAK), and a seeded bootstrap CI on kappa.

It measures CONCORDANCE, not correctness — it never decides who is right. Cohen's
kappa is prevalence-sensitive (the "kappa paradox": high agreement can yield low
kappa under skew), which is exactly why PABAK is reported alongside.

Input is a JSONL (default) or CSV file, one row per triaged item, each carrying a
framework decision and a human decision under `--framework-key` / `--human-key`.

Usage:

    python3 scripts/triage_agreement.py labels.jsonl
    python3 scripts/triage_agreement.py labels.jsonl --json
    python3 scripts/triage_agreement.py labels.csv --format csv --bootstrap 5000
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "validation"
TOOL_NAME = "triage_agreement"
SCRIPT_VERSION = "1.0"
MIN_ITEMS = 10


def load_pairs(path: Path, *, framework_key: str, human_key: str,
               fmt: str) -> tuple[list[tuple[str, str]], int]:
    """Return (pairs, n_dropped). Rows missing either key are dropped + counted."""
    rows: list[dict[str, Any]] = []
    if fmt == "csv":
        with path.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:  # jsonl
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    pairs: list[tuple[str, str]] = []
    dropped = 0
    for row in rows:
        f = row.get(framework_key)
        h = row.get(human_key)
        if f is None or h is None or f == "" or h == "":
            dropped += 1
            continue
        pairs.append((str(f), str(h)))
    return pairs, dropped


def _confusion(pairs: list[tuple[str, str]],
               categories: list[str]) -> dict[str, dict[str, int]]:
    table = {a: {b: 0 for b in categories} for a in categories}
    for f, h in pairs:
        table[f][h] += 1
    return table


def cohens_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa between the framework and human columns. 0.0 if degenerate."""
    n = len(pairs)
    if n == 0:
        return 0.0
    cats = sorted({c for pair in pairs for c in pair})
    if len(cats) < 2:
        # Only one category observed across both raters: perfect-but-trivial.
        # By convention here, no informative agreement to measure → 0.0.
        return 0.0
    f_counts = Counter(f for f, _ in pairs)
    h_counts = Counter(h for _, h in pairs)
    p_o = sum(1 for f, h in pairs if f == h) / n
    p_e = sum((f_counts.get(c, 0) / n) * (h_counts.get(c, 0) / n) for c in cats)
    if p_e == 1.0:
        return 0.0
    return (p_o - p_e) / (1 - p_e)


def pabak(pairs: list[tuple[str, str]], n_categories: int) -> float:
    """Prevalence-and-bias-adjusted kappa: (p_o - 1/k) / (1 - 1/k)."""
    n = len(pairs)
    if n == 0 or n_categories < 2:
        return 0.0
    p_o = sum(1 for f, h in pairs if f == h) / n
    inv_k = 1 / n_categories
    return (p_o - inv_k) / (1 - inv_k)


def _bootstrap_kappa_ci(pairs: list[tuple[str, str]], *, n_boot: int,
                        seed: int) -> list[float] | None:
    if n_boot <= 0 or len(pairs) < 2:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    kappas: list[float] = []
    for _ in range(n_boot):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        kappas.append(cohens_kappa(sample))
    kappas.sort()

    def _pct(p: float) -> float:
        idx = min(len(kappas) - 1, max(0, int(round(p * (len(kappas) - 1)))))
        return kappas[idx]

    return [round(_pct(0.025), 4), round(_pct(0.975), 4)]


def analyze(pairs: list[tuple[str, str]], *, n_dropped: int, n_boot: int,
            seed: int) -> dict[str, Any]:
    """Compute the descriptive agreement profile. Deterministic given seed."""
    n = len(pairs)
    categories = sorted({c for pair in pairs for c in pair})
    p_o = sum(1 for f, h in pairs if f == h) / n if n else 0.0
    kappa = cohens_kappa(pairs)
    return {
        "n_items": n,
        "n_dropped": n_dropped,
        "categories": categories,
        "confusion": _confusion(pairs, categories),
        "marginals": {
            "framework": dict(Counter(f for f, _ in pairs)),
            "human": dict(Counter(h for _, h in pairs)),
        },
        "percent_agreement": round(p_o, 4),
        "cohens_kappa": round(kappa, 4),
        "pabak": round(pabak(pairs, len(categories)), 4),
        "bootstrap_n": n_boot,
        "kappa_ci95": _bootstrap_kappa_ci(pairs, n_boot=n_boot, seed=seed),
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "the measured concordance between the framework's surfaced candidates "
            "and an operator's triage judgments on this item set — percent "
            "agreement, Cohen's kappa, PABAK, and a bootstrap CI on kappa."
        ),
        does_not_license=(
            "any inference that the framework or the human is correct: kappa "
            "measures agreement, not ground truth. It does not license "
            "generalization beyond this item set, and it is not an AI verdict."
        ),
        comparison_set={"mode": "paired_framework_vs_human_labels"},
        additional_caveats=[
            "Cohen's kappa is prevalence-sensitive (the 'kappa paradox': high "
            "agreement can yield a low kappa under skewed prevalence) — PABAK is "
            "reported for exactly that reason.",
            "The result is only as representative as the item set; a "
            "non-representative sample does not generalize.",
            "Small N produces a wide bootstrap CI; read the interval, not the "
            "point estimate.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/18-triage-agreement.md",
        ],
    )


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  available: bool,
                  warnings: list[str] | None = None) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,  # non-prose input; item count lives in results.
        baseline=None,
        results=results if available else {},
        claim_license=_claim_license() if available else None,
        available=available,
        warnings=warnings,
        target_extra={"n_items": results.get("n_items", 0)} if results else None,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Framework-vs-human triage agreement — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Too few usable items — no agreement statistics produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    ci = r["kappa_ci95"]
    ci_str = f"[{ci[0]}, {ci[1]}]" if ci else "n/a"
    lines += [
        f"**Items:** {r['n_items']} ({r['n_dropped']} dropped)  |  "
        f"**Categories:** {r['categories']}",
        "",
        "## Agreement",
        "",
        f"- **Percent agreement:** {r['percent_agreement']}",
        f"- **Cohen's kappa:** {r['cohens_kappa']}  (95% CI {ci_str}, "
        f"n_boot={r['bootstrap_n']})",
        f"- **PABAK:** {r['pabak']}",
        f"- **Confusion:** {r['confusion']}",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="Path to a JSONL (default) or CSV labels file.")
    p.add_argument("--framework-key", default="framework",
                   help="Row key holding the framework's decision (default: framework).")
    p.add_argument("--human-key", default="human",
                   help="Row key holding the human's triage decision (default: human).")
    p.add_argument("--format", choices=("jsonl", "csv"), default="jsonl",
                   help="Input format (default: jsonl).")
    p.add_argument("--bootstrap", type=int, default=2000,
                   help="Bootstrap resamples for the kappa CI (0 to skip).")
    p.add_argument("--seed", type=int, default=0,
                   help="Bootstrap seed (default: 0) — output is deterministic.")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    pairs, dropped = load_pairs(
        target_path, framework_key=args.framework_key,
        human_key=args.human_key, fmt=args.format,
    )

    if len(pairs) < MIN_ITEMS:
        payload = build_payload(
            {"n_items": len(pairs)}, target_path=target_path, available=False,
            warnings=[
                f"Only {len(pairs)} usable item(s) ({dropped} dropped); below the "
                f"{MIN_ITEMS}-item floor for meaningful agreement statistics."
            ],
        )
    else:
        results = analyze(pairs, n_dropped=dropped, n_boot=args.bootstrap,
                          seed=args.seed)
        payload = build_payload(results, target_path=target_path, available=True)

    text_out = (json.dumps(payload, indent=2, default=str)
                if args.json else render_report(payload))
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
