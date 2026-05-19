"""External Mirror Workflow Harness.

Operator workflow orchestrator for the External Mirror Discrimination
skill. Bridges Phase A (prompt-builder) and Phase B (ingest + distance
+ evidence pack) without depending on either being importable — Phase A
and Phase B scripts are invoked via subprocess.

Implements SPEC_external_mirror_workflow.md v0.1.

Subcommands:
    prepare TARGET.txt   — invoke Phase A, lay out run directory
    status  RUN_DIR      — report which files are present / missing
    score   RUN_DIR      — invoke Phase B chain end-to-end

CLI:
    python3 workflow.py prepare TARGET.txt --families "claude,chatgpt"
    python3 workflow.py status ./runs/mirror_20260519/
    python3 workflow.py score  ./runs/mirror_20260519/
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


SCRIPT_VERSION = "0.1.0"
DEFAULT_FAMILIES = ("claude", "chatgpt", "gemini", "human_control")
_WINDOW_FILE_RE = re.compile(r"^window_(\d+)\.(txt|md)$")


SubprocessRunner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _default_runner(cmd: list[str]) -> "subprocess.CompletedProcess":
    return subprocess.run(cmd, capture_output=True, text=True)


def _here() -> Path:
    return Path(__file__).resolve().parent


# ============================================================
# prepare
# ============================================================


@dataclass
class PrepareResult:
    run_dir: Path
    families: list[str]
    build_prompts_returncode: int
    build_prompts_stdout: str
    build_prompts_stderr: str


def _parse_csv(s: str) -> list[str]:
    return [item.strip() for item in s.split(",") if item.strip()]


def _resolve_run_id(supplied: str | None) -> str:
    if supplied:
        return supplied
    return "mirror_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def prepare(
    *,
    target_path: Path,
    runs_root: Path,
    families: list[str],
    run_id: str | None = None,
    windows: int = 4,
    context: int = 500,
    continuation: int = 150,
    positioning: str = "equal_skipping_opening",
    positions: str | None = None,
    context_grid: str | None = None,
    genre_descriptor: str = "literary prose",
    build_prompts_bin: Path | None = None,
    runner: SubprocessRunner | None = None,
) -> PrepareResult:
    """Lay out a fresh run directory and invoke Phase A. Returns the result."""
    if not target_path.exists():
        raise FileNotFoundError(f"target not found at {target_path}")
    if not families:
        raise ValueError("families list cannot be empty")

    runner = runner or _default_runner
    resolved_run_id = _resolve_run_id(run_id)
    run_dir = runs_root / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    prompts_root = run_dir / "prompts"
    prompts_root.mkdir()

    outputs_root = run_dir / "outputs"
    outputs_root.mkdir()
    for family in families:
        (outputs_root / family).mkdir()

    target_copy = run_dir / "target.txt"
    shutil.copyfile(target_path, target_copy)

    bin_path = build_prompts_bin or (_here() / "build_prompts.py")

    cmd: list[str] = [
        sys.executable,
        str(bin_path),
        str(target_copy),
        "--out", str(prompts_root),
        "--run-id", resolved_run_id,
        "--positioning", positioning,
        "--windows", str(windows),
        "--context", str(context),
        "--continuation", str(continuation),
        "--genre-descriptor", genre_descriptor,
        "--format", "both",
    ]
    if positions:
        cmd.extend(["--positions", positions])
    if context_grid:
        cmd.extend(["--context-grid", context_grid])

    proc = runner(cmd)

    phase_a_run_dir = prompts_root / resolved_run_id
    write_workflow_md(
        run_dir=run_dir,
        resolved_run_id=resolved_run_id,
        families=families,
        windows=windows,
        genre_descriptor=genre_descriptor,
        phase_a_run_dir=phase_a_run_dir,
    )

    return PrepareResult(
        run_dir=run_dir,
        families=list(families),
        build_prompts_returncode=proc.returncode,
        build_prompts_stdout=proc.stdout or "",
        build_prompts_stderr=proc.stderr or "",
    )


def write_workflow_md(
    *,
    run_dir: Path,
    resolved_run_id: str,
    families: list[str],
    windows: int,
    genre_descriptor: str,
    phase_a_run_dir: Path,
) -> None:
    families_csv = ", ".join(f"`{f}`" for f in families)
    family_outputs = "\n".join(f"- `outputs/{f}/`" for f in families)
    lines = [
        "# External Mirror Discrimination — Workflow checklist",
        "",
        f"- **Run ID:** `{resolved_run_id}`",
        f"- **Target:** `target.txt`",
        f"- **Families:** {families_csv}",
        f"- **Windows:** {windows}",
        f"- **Genre descriptor:** {genre_descriptor}",
        "",
        "## Step 1 — Paste prompts to your chatbot(s)",
        "",
        f"Prompt files live in `{phase_a_run_dir.relative_to(run_dir)}/`. For each family below, drop the LLM's outputs in the matching directory.",
        "",
        family_outputs,
        "",
        "### Option A — separate windows (T3)",
        "",
        f"For each family, open {windows} fresh chats. In each, paste one of:",
        ""
    ]
    for i in range(1, windows + 1):
        lines.append(f"- `{phase_a_run_dir.relative_to(run_dir)}/window_{i}.md`")
    lines.extend([
        "",
        "Save each LLM output as `outputs/$family/window_$N.txt` (or `.md`).",
        "",
        "### Option B — batched (T4)",
        "",
        f"For each family, paste `{phase_a_run_dir.relative_to(run_dir)}/windows_batched.md` into a single agent-capable chat. Save the JSON-array output as `outputs/$family/windows_batched.json`.",
        "",
        "## Step 2 — Provide target continuation",
        "",
        "Fill in `target_continuation.json` with the target's actual continuation per window:",
        "",
        "```json",
        "[",
        '  {"window": 1, "continuation": "..."},',
        '  {"window": 2, "continuation": "..."},',
        "  ...",
        "]",
        "```",
        "",
        "(Optional — if omitted, only family-vs-family distances are computed; the discrimination signal is weaker without the target row.)",
        "",
        "## Step 3 — Score",
        "",
        f"```",
        f"python3 workflow.py score {run_dir}",
        "```",
        "",
        "This produces `evidence_pack.json` and `evidence_pack.md`.",
        "",
        "## Step 4 — Inspect",
        "",
        "```",
        "cat evidence_pack.md",
        "```",
        "",
        "The distance matrix + caveats + claim-license block are operator-facing artifacts; the framework does not produce a verdict.",
        "",
    ]
    )
    (run_dir / "WORKFLOW.md").write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# status
# ============================================================


def status(run_dir: Path) -> dict:
    """Walk the run dir and report status. Returns a structured dict."""
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run directory not found: {run_dir}")

    prompts_root = run_dir / "prompts"
    outputs_root = run_dir / "outputs"

    phase_a_run_dirs = list(prompts_root.iterdir()) if prompts_root.exists() else []
    phase_a_dir = next((p for p in phase_a_run_dirs if p.is_dir() and (p / "MANIFEST.json").exists()), None)

    windows_count = None
    target_sha256 = None
    if phase_a_dir is not None:
        manifest = json.loads((phase_a_dir / "MANIFEST.json").read_text())
        windows_count = manifest.get("windows_count")
        target_sha256 = manifest.get("target_sha256")

    families: dict[str, dict] = {}
    if outputs_root.exists():
        for fam_dir in sorted(p for p in outputs_root.iterdir() if p.is_dir()):
            t4 = (fam_dir / "windows_batched.json").exists()
            t3_indices = []
            for p in fam_dir.iterdir():
                if not p.is_file():
                    continue
                m = _WINDOW_FILE_RE.match(p.name)
                if m:
                    t3_indices.append(int(m.group(1)))
            families[fam_dir.name] = {
                "has_t4": t4,
                "t3_window_indices": sorted(t3_indices),
                "expected_windows": windows_count,
                "missing": (
                    sorted(set(range(1, windows_count + 1)) - set(t3_indices))
                    if (windows_count is not None and not t4) else []
                ),
            }

    return {
        "run_dir": str(run_dir.resolve()),
        "phase_a_run_dir": str(phase_a_dir.resolve()) if phase_a_dir else None,
        "windows_count": windows_count,
        "target_sha256": target_sha256,
        "families": families,
        "target_continuation_present": (run_dir / "target_continuation.json").exists(),
        "phase_b_artifacts": {
            "ingested_json": (run_dir / "ingested.json").exists(),
            "distances_json": (run_dir / "distances.json").exists(),
            "evidence_pack_json": (run_dir / "evidence_pack.json").exists(),
            "evidence_pack_md": (run_dir / "evidence_pack.md").exists(),
        },
    }


def render_status(s: dict) -> str:
    lines = [f"# Run status: {s['run_dir']}", ""]
    if s["phase_a_run_dir"]:
        lines.append(f"- **Phase A run:** `{s['phase_a_run_dir']}` ({s['windows_count']} windows)")
    else:
        lines.append("- **Phase A run:** NOT PRESENT (prompts/ is empty or missing MANIFEST.json)")
    lines.append(f"- **Target continuation present:** {s['target_continuation_present']}")
    lines.append("")
    lines.append("## Families")
    if not s["families"]:
        lines.append("(no family directories under outputs/)")
    for fam, info in s["families"].items():
        if info["has_t4"]:
            lines.append(f"- `{fam}`: T4 batched JSON present")
        elif info["t3_window_indices"]:
            missing = info["missing"]
            line = f"- `{fam}`: T3 windows {info['t3_window_indices']}"
            if missing:
                line += f" (missing: {missing})"
            lines.append(line)
        else:
            lines.append(f"- `{fam}`: (no outputs yet)")
    lines.append("")
    lines.append("## Phase B artifacts")
    for k, v in s["phase_b_artifacts"].items():
        lines.append(f"- `{k}`: {'present' if v else 'missing'}")
    return "\n".join(lines)


# ============================================================
# score
# ============================================================


@dataclass
class ScoreResult:
    ingested_path: Path
    distances_path: Path
    evidence_pack_json: Path
    evidence_pack_md: Path
    steps: list[dict]  # [{"step": name, "returncode": int, "stdout": str, "stderr": str}]


def score(
    run_dir: Path,
    *,
    embedding_alias: str = "mxbai",
    ingest_bin: Path | None = None,
    distances_bin: Path | None = None,
    pack_bin: Path | None = None,
    runner: SubprocessRunner | None = None,
) -> ScoreResult:
    """Run the Phase B chain. Raises RuntimeError on any step's nonzero exit."""
    if not run_dir.exists():
        raise FileNotFoundError(f"run directory not found: {run_dir}")

    runner = runner or _default_runner
    here = _here()
    ingest_bin = ingest_bin or (here / "ingest_outputs.py")
    distances_bin = distances_bin or (here / "compute_distances.py")
    pack_bin = pack_bin or (here / "compose_evidence_pack.py")

    prompts_root = run_dir / "prompts"
    phase_a_dirs = [p for p in prompts_root.iterdir() if p.is_dir() and (p / "MANIFEST.json").exists()] if prompts_root.exists() else []
    if not phase_a_dirs:
        raise RuntimeError(f"no Phase A run found under {prompts_root} (expected a $run_id/MANIFEST.json)")
    phase_a_dir = phase_a_dirs[0]

    outputs_root = run_dir / "outputs"
    ingested_path = run_dir / "ingested.json"
    distances_path = run_dir / "distances.json"
    evidence_pack_json = run_dir / "evidence_pack.json"
    evidence_pack_md = run_dir / "evidence_pack.md"
    target_continuation_path = run_dir / "target_continuation.json"

    steps: list[dict] = []

    # Step 1: ingest
    cmd1 = [
        sys.executable, str(ingest_bin),
        str(phase_a_dir), str(outputs_root),
        "--out", str(ingested_path),
    ]
    p1 = runner(cmd1)
    steps.append({"step": "ingest", "returncode": p1.returncode, "stdout": p1.stdout or "", "stderr": p1.stderr or ""})
    if p1.returncode != 0:
        raise RuntimeError(f"step 'ingest' failed (exit {p1.returncode}): {p1.stderr}")

    # Step 2: distances
    cmd2 = [
        sys.executable, str(distances_bin),
        str(ingested_path),
        "--out", str(distances_path),
        "--embedding-alias", embedding_alias,
    ]
    if target_continuation_path.exists():
        cmd2.extend(["--target-continuation", str(target_continuation_path)])
    p2 = runner(cmd2)
    steps.append({"step": "distances", "returncode": p2.returncode, "stdout": p2.stdout or "", "stderr": p2.stderr or ""})
    if p2.returncode != 0:
        raise RuntimeError(f"step 'distances' failed (exit {p2.returncode}): {p2.stderr}")

    # Step 3: evidence pack
    cmd3 = [
        sys.executable, str(pack_bin),
        str(distances_path),
        "--out-json", str(evidence_pack_json),
        "--out-md", str(evidence_pack_md),
    ]
    p3 = runner(cmd3)
    steps.append({"step": "pack", "returncode": p3.returncode, "stdout": p3.stdout or "", "stderr": p3.stderr or ""})
    if p3.returncode != 0:
        raise RuntimeError(f"step 'pack' failed (exit {p3.returncode}): {p3.stderr}")

    return ScoreResult(
        ingested_path=ingested_path,
        distances_path=distances_path,
        evidence_pack_json=evidence_pack_json,
        evidence_pack_md=evidence_pack_md,
        steps=steps,
    )


# ============================================================
# CLI
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="External Mirror Discrimination workflow harness."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prepare = sub.add_parser("prepare", help="Lay out a fresh run directory and invoke Phase A.")
    p_prepare.add_argument("target", help="Path to target text file.")
    p_prepare.add_argument("--runs-root", default="runs", help="Root directory for runs (default ./runs/).")
    p_prepare.add_argument("--run-id", default=None, help="Explicit run id (default mirror_YYYYMMDD_HHMMSS).")
    p_prepare.add_argument("--families", default=",".join(DEFAULT_FAMILIES), help=f"Comma-separated family labels (default '{','.join(DEFAULT_FAMILIES)}').")
    p_prepare.add_argument("--windows", type=int, default=4)
    p_prepare.add_argument("--context", type=int, default=500)
    p_prepare.add_argument("--continuation", type=int, default=150)
    p_prepare.add_argument("--positioning", default="equal_skipping_opening")
    p_prepare.add_argument("--positions", default=None)
    p_prepare.add_argument("--context-grid", default=None)
    p_prepare.add_argument("--genre-descriptor", default="literary prose")
    p_prepare.add_argument("--build-prompts-bin", default=None)

    p_status = sub.add_parser("status", help="Report run directory state.")
    p_status.add_argument("run_dir")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    p_score = sub.add_parser("score", help="Invoke the Phase B chain end-to-end.")
    p_score.add_argument("run_dir")
    p_score.add_argument("--embedding-alias", default="mxbai")
    p_score.add_argument("--ingest-bin", default=None)
    p_score.add_argument("--distances-bin", default=None)
    p_score.add_argument("--pack-bin", default=None)

    args = parser.parse_args(argv)

    if args.cmd == "prepare":
        try:
            result = prepare(
                target_path=Path(args.target),
                runs_root=Path(args.runs_root),
                families=_parse_csv(args.families),
                run_id=args.run_id,
                windows=args.windows,
                context=args.context,
                continuation=args.continuation,
                positioning=args.positioning,
                positions=args.positions,
                context_grid=args.context_grid,
                genre_descriptor=args.genre_descriptor,
                build_prompts_bin=Path(args.build_prompts_bin) if args.build_prompts_bin else None,
            )
        except (FileNotFoundError, ValueError, FileExistsError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if result.build_prompts_returncode != 0:
            print(f"warning: build_prompts.py exited {result.build_prompts_returncode}", file=sys.stderr)
            if result.build_prompts_stderr:
                print(result.build_prompts_stderr, file=sys.stderr)
            return result.build_prompts_returncode
        print(f"Prepared run at {result.run_dir}/")
        print(f"  Families: {', '.join(result.families)}")
        print(f"  Next: read {result.run_dir / 'WORKFLOW.md'}")
        return 0

    if args.cmd == "status":
        try:
            s = status(Path(args.run_dir))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print(render_status(s))
        return 0

    if args.cmd == "score":
        try:
            r = score(
                Path(args.run_dir),
                embedding_alias=args.embedding_alias,
                ingest_bin=Path(args.ingest_bin) if args.ingest_bin else None,
                distances_bin=Path(args.distances_bin) if args.distances_bin else None,
                pack_bin=Path(args.pack_bin) if args.pack_bin else None,
            )
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {r.evidence_pack_json} + {r.evidence_pack_md}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
