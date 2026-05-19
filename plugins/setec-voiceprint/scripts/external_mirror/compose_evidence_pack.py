"""Phase B step 3: compose evidence pack.

Takes distances.json (from compute_distances.py) and emits the
schema_version 1.0 envelope JSON + a human-readable markdown rendering.
Attaches task_surface="external_mirror_discrimination" and a structured
claim_license block (operator-overridable).

The evidence pack reports the distance matrix and caveats; no
programmatic verdict. Operator judgment is the load-bearing decision step.

Implements SPEC_external_mirror_phase_b.md v0.1.

CLI:
    python3 compose_evidence_pack.py DISTANCES_JSON \
        [--out-json PATH] [--out-md PATH] \
        [--licenses STR] [--does-not-license STR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from claim_license import ClaimLicense  # noqa: E402
from output_schema import build_output  # noqa: E402


SCRIPT_VERSION = "0.1.0"
TASK_SURFACE = "external_mirror_discrimination"
TOOL_NAME = "compose_evidence_pack"


DEFAULT_LICENSES = (
    "Reports the cosine distance between each LLM family's continuation "
    "of K context windows from the target text and the target's actual "
    "continuation, plus pairwise distances between families. The distance "
    "matrix is a measurement of continuation-space convergence in a "
    "single embedding model's representation of register."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license a binary AI/human authorship verdict. The distance "
    "matrix is one measurement in one embedding model; operator judgment "
    "remains the load-bearing decision step. Does not generalize beyond "
    "the genre descriptor used in Phase A's prompts. Does not control "
    "for memorization (if the target text is in any LLM family's training "
    "set, distances will be artificially low). Does not substitute for "
    "stylometric, surprisal, or other framework audits — it complements them."
)


def compose(
    distances: dict,
    *,
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
) -> tuple[dict, str]:
    """Compose the evidence pack. Returns (envelope_dict, markdown_str)."""
    manifest = distances["manifest"]
    target_path = manifest.get("target_path")
    target_words = int(manifest.get("target_word_count") or 0)

    caveats = list(distances.get("global_caveats", []))
    for w_caveats in distances.get("per_window_caveats", []):
        caveats.extend(w_caveats)
    seen = set()
    deduped_caveats = []
    for c in caveats:
        if c not in seen:
            seen.add(c)
            deduped_caveats.append(c)

    summary = distances.get("summary", {})

    results = {
        "phase_a_run_id": manifest.get("run_id"),
        "phase_a_target_sha256": manifest.get("target_sha256"),
        "ingested_sha256": distances.get("ingested_sha256"),
        "positioning": manifest.get("positioning"),
        "windows_count": distances.get("windows_count"),
        "families": distances.get("families", []),
        "have_target_continuation": distances.get("have_target_continuation"),
        "embedding_block": distances.get("embedding_block"),
        "labels_per_window": distances.get("labels_per_window"),
        "distance_matrices": distances.get("distance_matrices"),
        "summary_distances": summary,
        "caveats": deduped_caveats,
        "phase_b_version": SCRIPT_VERSION,
    }

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "families": distances.get("families", []),
            "windows_count": distances.get("windows_count"),
            "have_target_continuation": distances.get("have_target_continuation"),
            "embedding_alias": (distances.get("embedding_block") or {}).get("alias"),
        },
        additional_caveats=deduped_caveats,
    )

    envelope = build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=deduped_caveats,
    )

    markdown = render_markdown(envelope, distances, license_block)
    return envelope, markdown


def render_markdown(envelope: dict, distances: dict, license_block: Any) -> str:
    manifest = distances["manifest"]
    families = distances["families"]
    windows_count = distances["windows_count"]
    labels_per_window = distances["labels_per_window"]
    matrices = distances["distance_matrices"]
    summary = distances.get("summary", {})

    lines: list[str] = []
    lines.append("# External Mirror Discrimination — Evidence Pack")
    lines.append("")
    lines.append(f"- **Phase A run ID:** `{manifest.get('run_id')}`")
    lines.append(f"- **Target:** `{manifest.get('target_path')}` ({manifest.get('target_word_count')} words)")
    lines.append(f"- **Positioning:** `{manifest.get('positioning')}`")
    lines.append(f"- **Windows:** {windows_count}")
    lines.append(f"- **Families compared:** {', '.join(families) if families else '(none)'}")
    embedding_block = distances.get("embedding_block") or {}
    lines.append(f"- **Embedding model:** `{embedding_block.get('id') or embedding_block.get('alias', 'unknown')}`")
    lines.append(f"- **Target continuation available:** {distances.get('have_target_continuation')}")
    lines.append("")

    if summary:
        lines.append("## Summary distances (family vs target)")
        lines.append("")
        lines.append("| Family | N windows | Mean | Median | Min | Max |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for fam in families:
            s = summary.get(fam, {})
            if s.get("n_windows_compared", 0) == 0:
                lines.append(f"| `{fam}` | 0 | — | — | — | — |")
            else:
                lines.append(
                    f"| `{fam}` | {s['n_windows_compared']} | "
                    f"{s['mean_vs_target']:.3f} | {s['median_vs_target']:.3f} | "
                    f"{s['min_vs_target']:.3f} | {s['max_vs_target']:.3f} |"
                )
        lines.append("")

    lines.append("## Per-window distance matrices")
    lines.append("")
    for w_idx in range(windows_count):
        lines.append(f"### Window {w_idx + 1}")
        lines.append("")
        labels = labels_per_window[w_idx]
        matrix = matrices[w_idx]
        header = "| | " + " | ".join(f"`{l}`" for l in labels) + " |"
        sep = "|---|" + "---|" * len(labels)
        lines.append(header)
        lines.append(sep)
        for i, row_label in enumerate(labels):
            cells = []
            for j in range(len(labels)):
                v = matrix[i][j]
                cells.append(f"{v:.3f}" if isinstance(v, (int, float)) else "—")
            lines.append(f"| `{row_label}` | " + " | ".join(cells) + " |")
        lines.append("")

    caveats = envelope["results"].get("caveats") or []
    lines.append("## Caveats")
    lines.append("")
    if caveats:
        for c in caveats:
            lines.append(f"- {c}")
    else:
        lines.append("(none surfaced)")
    lines.append("")

    lines.append("## Claim license")
    lines.append("")
    lines.append(license_block.render_block().rstrip())
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- **Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}")
    lines.append(f"- **Phase A target_sha256:** `{manifest.get('target_sha256')}`")
    lines.append(f"- **Ingested_sha256:** `{distances.get('ingested_sha256')}`")
    lines.append(f"- **Embedding identity:** `{json.dumps(embedding_block)}`")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase B step 3: compose external-mirror evidence pack."
    )
    parser.add_argument("distances_json", help="distances.json from compute_distances.py")
    parser.add_argument("--out-json", default=None, help="Evidence pack JSON path (default: <distances-parent>/evidence_pack.json)")
    parser.add_argument("--out-md", default=None, help="Evidence pack markdown path (default: <distances-parent>/evidence_pack.md)")
    parser.add_argument("--licenses", default=DEFAULT_LICENSES, help="Override the claim_license.licenses text.")
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE, help="Override the claim_license.does_not_license text.")
    args = parser.parse_args(argv)

    distances_path = Path(args.distances_json)
    if not distances_path.exists():
        print(f"error: distances.json not found at {distances_path}", file=sys.stderr)
        return 1
    distances = json.loads(distances_path.read_text(encoding="utf-8"))

    try:
        envelope, markdown = compose(
            distances,
            licenses_text=args.licenses,
            does_not_license_text=args.does_not_license,
        )
    except (ValueError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_json = Path(args.out_json) if args.out_json else distances_path.parent / "evidence_pack.json"
    out_md = Path(args.out_md) if args.out_md else distances_path.parent / "evidence_pack.md"
    out_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    out_md.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_json} + {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
