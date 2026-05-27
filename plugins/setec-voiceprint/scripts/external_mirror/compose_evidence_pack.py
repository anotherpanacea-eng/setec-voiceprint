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
    "for memorization: per SPEC v0.2, the mirror model's published training "
    "cutoff MUST precede both the target's and the human-control's publication "
    "dates, or the discrimination signal is contaminated (target side: "
    "measures recall not inference; control side: gap compresses, signal is "
    "conservative). High-visibility recent documents may fail this even when "
    "nominally post-cutoff. Does not substitute for stylometric, surprisal, "
    "or other framework audits — it complements them. Operators using agent "
    "orchestration must additionally guarantee orchestration-layer blinding: "
    "subagent context isolation is necessary but not sufficient."
)


def _split_metadata(
    family_metadata: dict,
) -> tuple[list[dict], dict]:
    """Split per-family metadata into mirror_panel and controls blocks.

    Per SPEC v0.2 §JSON schema: a family may declare a mirror_panel block
    (training_cutoff_date / interface / orchestration_layer_blinding /
    nominal_family / reasoning_mode / web_search_enabled), a control block
    (publication_date / cutoff_precedes_publication / visibility_class), or
    both. The evidence pack surfaces them under two separate keys so readers
    can navigate by role.
    """
    mirror_panel: list[dict] = []
    controls_known_human: list[dict] = []
    for family, meta in sorted(family_metadata.items()):
        mp = (meta or {}).get("mirror_panel")
        if mp:
            mirror_panel.append({"family": family, **mp})
        ctrl = (meta or {}).get("control")
        if ctrl:
            controls_known_human.append({"family": family, **ctrl})
    controls_block: dict = {}
    if controls_known_human:
        controls_block["known_human_control"] = (
            controls_known_human[0] if len(controls_known_human) == 1
            else controls_known_human
        )
    return mirror_panel, controls_block


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
    family_metadata = distances.get("family_metadata", {}) or {}
    mirror_panel, controls_block = _split_metadata(family_metadata)

    results = {
        "phase_a_run_id": manifest.get("run_id"),
        "phase_a_target_sha256": manifest.get("target_sha256"),
        "ingested_sha256": distances.get("ingested_sha256"),
        "positioning": manifest.get("positioning"),
        "windows_count": distances.get("windows_count"),
        "families": distances.get("families", []),
        # v0.2 spec: per-family metadata split into mirror_panel (LLM
        # mirror families: cutoff / interface / blinding / reasoning /
        # web-search / nominal vs effective) and controls (human-control
        # publication date, cutoff-precedence, visibility class).
        "mirror_panel": mirror_panel,
        "controls": controls_block,
        "have_target_continuation": distances.get("have_target_continuation"),
        "embedding_block": distances.get("embedding_block"),
        "labels_per_window": distances.get("labels_per_window"),
        "distance_matrices": distances.get("distance_matrices"),
        # v2: per-metric matrices + which metrics actually ran + why
        # the others were skipped (sklearn / spaCy availability).
        "distance_matrices_by_metric": distances.get(
            "distance_matrices_by_metric"
        ),
        "metrics_available": distances.get("metrics_available", []),
        "metric_skip_reasons": distances.get("metric_skip_reasons", {}),
        "summary_distances": summary,
        "summary_by_metric": distances.get("summary_by_metric", {}),
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
            "embedding_alias": (distances.get("embedding_block") or {}).get("alias") if distances.get("embedding_block") else None,
            # v2: surface which metrics' measurements actually landed
            # so the audit consumer knows what they're reading.
            "metrics_available": distances.get("metrics_available", []),
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
    # v2: surface which metrics ran + which were skipped.
    metrics_available = distances.get("metrics_available", [])
    skip_reasons = distances.get("metric_skip_reasons", {})
    if metrics_available:
        lines.append(f"- **Metrics computed:** {', '.join(f'`{m}`' for m in metrics_available)}")
    if skip_reasons:
        skipped = ", ".join(f"`{m}` ({why})" for m, why in skip_reasons.items())
        lines.append(f"- **Metrics skipped:** {skipped}")
    lines.append("")

    # v0.2 spec: mirror_panel + controls tables surface per-family
    # cutoff / interface / blinding / reasoning / web-search and
    # human-control publication metadata. Only render when present;
    # absent metadata means a v0.1-style run.
    results_block = envelope.get("results", {}) or {}
    mirror_panel = results_block.get("mirror_panel") or []
    if mirror_panel:
        lines.append("## Mirror panel (SPEC v0.2)")
        lines.append("")
        lines.append("| Family | Nominal | Training cutoff | Interface | Orchestration | Reasoning | Web search |")
        lines.append("|---|---|---|---|---|---|---|")
        for entry in mirror_panel:
            reasoning = entry.get("reasoning_mode")
            reasoning_str = "—" if reasoning is None else ("yes" if reasoning else "no")
            web_search = entry.get("web_search_enabled")
            web_str = "—" if web_search is None else ("yes" if web_search else "no")
            lines.append(
                f"| `{entry.get('family', '?')}` | "
                f"{entry.get('nominal_family') or '—'} | "
                f"{entry.get('training_cutoff_date') or '—'} | "
                f"{entry.get('interface') or '—'} | "
                f"{entry.get('orchestration_layer_blinding') or '—'} | "
                f"{reasoning_str} | "
                f"{web_str} |"
            )
        lines.append("")

    controls_block_md = results_block.get("controls") or {}
    known_human = controls_block_md.get("known_human_control")
    if known_human:
        rows = known_human if isinstance(known_human, list) else [known_human]
        lines.append("## Controls (SPEC v0.2)")
        lines.append("")
        lines.append("| Family | Publication date | Cutoff precedes publication | Visibility class |")
        lines.append("|---|---|---|---|")
        for entry in rows:
            cutoff_pre = entry.get("cutoff_precedes_publication")
            cutoff_str = "—" if cutoff_pre is None else ("yes" if cutoff_pre else "no")
            lines.append(
                f"| `{entry.get('family', '?')}` | "
                f"{entry.get('publication_date') or '—'} | "
                f"{cutoff_str} | "
                f"{entry.get('visibility_class') or '—'} |"
            )
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

    # v2: per-metric per-window tables. Default to sbert for
    # back-compat with v1 evidence packs; render additional metrics
    # under their own sub-sections when present.
    matrices_by_metric = distances.get("distance_matrices_by_metric") or {}

    def _render_metric_block(metric_name: str, per_window: list) -> None:
        lines.append(f"## Per-window distance matrices — `{metric_name}`")
        lines.append("")
        for w_idx in range(windows_count):
            lines.append(f"### Window {w_idx + 1}")
            lines.append("")
            labels = labels_per_window[w_idx]
            matrix = per_window[w_idx]
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

    rendered_any = False
    for metric_name in metrics_available:
        per_window = matrices_by_metric.get(metric_name)
        if per_window is None:
            continue
        _render_metric_block(metric_name, per_window)
        rendered_any = True

    # Fallback to the v1 layout when matrices_by_metric is absent
    # (e.g., reading a v1-shaped distances.json that pre-dates v2).
    if not rendered_any and matrices:
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
