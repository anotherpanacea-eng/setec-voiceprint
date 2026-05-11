#!/usr/bin/env python3
"""baseline_discovery.py — locate the user's existing baselines folder.

Background. SETEC writes voice profiles, impostor corpora, and
calibration manifests under a directory named
``ai-prose-baselines-private``. Many users sync that directory via
Obsidian, Dropbox, iCloud, or a similar tool so the baseline survives
across machines and worktrees. The framework already understands a
``SETEC_BASELINES_DIR`` environment variable, but the variable was
never surfaced in setup. The observed failure mode: a fresh SETEC
instance, working in a git worktree where no sibling private folder
exists, creates a brand-new empty ``ai-prose-baselines-private/`` and
quietly diverges from the user's real baseline.

This script reads state. It searches a list of common locations,
reports what was found at each (manifest size, impostor-persona
count, last-modified timestamp), recommends the most-populated
candidate, and prints the ``export SETEC_BASELINES_DIR=...`` line the
user should add to their shell rc.

The script does not write anything, does not create folders, and does
not modify the environment. The setup skill calls it before tier
checks and surfaces the recommendation to the user.

Usage::

    # Survey common locations and print findings:
    python3 scripts/baseline_discovery.py

    # JSON output (for the setup skill to parse):
    python3 scripts/baseline_discovery.py --json

    # Validate an explicit path (does it look like a baselines folder?):
    python3 scripts/baseline_discovery.py --validate /path/to/dir

    # Limit search depth (default 4) if home is huge:
    python3 scripts/baseline_discovery.py --max-depth 3

Exit codes::

    0  — at least one candidate found (env var or filesystem)
    1  — no candidate found; user must create one
    2  — explicit ``--validate`` argument is not a usable baselines dir

This script is intentionally non-destructive. The user remains the
sole authority over which folder SETEC writes into.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

TASK_SURFACE = "setup"
TOOL_NAME = "baseline_discovery"
SCRIPT_VERSION = "1.0"

PRIVATE_DIR_NAME = "ai-prose-baselines-private"
ENV_VAR = "SETEC_BASELINES_DIR"

# Manifest filenames recognised. Acquisition writes ``corpus_manifest.jsonl``;
# the calibration corpus writes ``manifest.jsonl``. Either counts as
# evidence the folder is in active use.
MANIFEST_NAMES = ("corpus_manifest.jsonl", "manifest.jsonl")


# --------------- Candidate model ---------------------------------


@dataclass
class Candidate:
    """One discovered baselines-folder candidate.

    Captured per-candidate so the setup skill can render a side-by-side
    comparison: the user may have an old folder in ``~/Documents/`` and
    a newer one synced via Obsidian, and the recommendation should be
    explained rather than asserted.
    """

    path: str
    source: str  # "env_var", "repo_sibling", "documents_scan", etc.
    exists: bool
    manifest_path: str | None
    manifest_entries: int
    impostor_personas: int
    impostor_registers: list[str]
    last_modified_iso: str | None
    size_bytes_total: int
    is_recommended: bool = False
    notes: list[str] = field(default_factory=list)


# --------------- Search strategy ---------------------------------


def _candidate_dirs() -> list[Path]:
    """Common roots to scan for ``ai-prose-baselines-private``.

    The list is intentionally generous: a user might keep baselines
    under iCloud, Dropbox, Obsidian, plain ``~/Documents``, or at the
    bare home root. We cap each scan with ``max_depth`` to keep the
    search bounded on large home directories. The repo-sibling check
    is handled separately by ``_repo_sibling``.
    """
    home = Path.home()
    roots: list[Path] = []
    # Most common containers on macOS / Linux:
    roots.append(home / "Documents")
    roots.append(home)
    # Cloud-sync roots — match anything starting with these prefixes:
    for prefix in ("Obsidian", "Dropbox", "Google Drive", "OneDrive"):
        for child in home.glob(f"{prefix}*"):
            if child.is_dir():
                roots.append(child)
    # macOS iCloud:
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.exists():
        roots.append(icloud)
    return roots


def _repo_sibling(script_path: Path) -> Path | None:
    """The directory that lives next to the SETEC repo, if any.

    After 1.16.0 the script lives at
    ``<repo>/plugins/setec-voiceprint/scripts/baseline_discovery.py``.
    ``parents[3]`` is the repo root in dev and the marketplace root in
    install. The documented standard layout puts the private folder
    next to the repo. Worktrees defeat this convention — that's the
    bug this script exists to mitigate — so we still check it but no
    longer treat it as a strong signal.
    """
    try:
        repo_root = script_path.resolve().parents[3]
    except IndexError:
        return None
    candidate = repo_root.parent / PRIVATE_DIR_NAME
    return candidate if candidate.exists() else None


def _scan_for_marker(
    root: Path, *, max_depth: int,
) -> list[Path]:
    """Walk ``root`` up to ``max_depth`` levels, returning every
    directory named ``ai-prose-baselines-private``.

    We don't use ``Path.glob`` with ``**`` here because the user's
    home directory can be tens of gigabytes deep; a bounded BFS keeps
    runtime predictable. We skip dot-prefixed directories (``.git``,
    ``.Trash``, ``.cache``) and ``node_modules`` outright.
    """
    if not root.exists() or not root.is_dir():
        return []
    matches: list[Path] = []
    # BFS with explicit depth tracking:
    frontier: list[tuple[Path, int]] = [(root, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth > max_depth:
            continue
        try:
            for child in current.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if name.startswith(".") or name == "node_modules":
                    continue
                if name == PRIVATE_DIR_NAME:
                    matches.append(child)
                    # Don't recurse into a found baseline folder.
                    continue
                if depth < max_depth:
                    frontier.append((child, depth + 1))
        except (PermissionError, OSError):
            # Skip directories we can't read rather than abort.
            continue
    return matches


# --------------- Candidate summarisation -------------------------


def _count_manifest_entries(manifest_path: Path) -> int:
    """Count non-empty lines in a JSONL manifest, capped at 1M.

    The cap exists so a malformed binary file accidentally renamed to
    ``manifest.jsonl`` can't hang the survey. Actual SETEC manifests
    top out well below the cap.
    """
    try:
        n = 0
        with manifest_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    n += 1
                    if n >= 1_000_000:
                        break
        return n
    except (OSError, UnicodeDecodeError):
        return 0


def _summarise_directory(path: Path, *, max_depth_scan: int = 3) -> dict[str, Any]:
    """Compute size, mtime, manifest, and impostor stats for one folder.

    Walks at most ``max_depth_scan`` levels deep to avoid pathological
    scans on very large folders. The summary is best-effort: a folder
    we can't fully read is reported with whatever we got plus a note.
    """
    summary: dict[str, Any] = {
        "manifest_path": None,
        "manifest_entries": 0,
        "impostor_personas": 0,
        "impostor_registers": [],
        "last_modified_iso": None,
        "size_bytes_total": 0,
        "notes": [],
    }
    if not path.exists():
        summary["notes"].append("path does not exist")
        return summary
    # Manifest detection — check top-level first, then one level deep.
    for manifest_name in MANIFEST_NAMES:
        cand = path / manifest_name
        if cand.exists():
            summary["manifest_path"] = str(cand)
            summary["manifest_entries"] = _count_manifest_entries(cand)
            break
    # Impostor structure — <root>/impostors/<register>/<persona>/.
    impostors = path / "impostors"
    if impostors.exists() and impostors.is_dir():
        registers = sorted(
            d.name for d in impostors.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        summary["impostor_registers"] = registers
        n_personas = 0
        for reg in registers:
            try:
                n_personas += sum(
                    1 for d in (impostors / reg).iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                )
            except (PermissionError, OSError):
                continue
        summary["impostor_personas"] = n_personas
    # Aggregate mtime + size with bounded walk.
    latest_mtime = 0.0
    total_size = 0
    frontier: list[tuple[Path, int]] = [(path, 0)]
    while frontier:
        current, depth = frontier.pop()
        if depth > max_depth_scan:
            continue
        try:
            for child in current.iterdir():
                if child.is_symlink():
                    continue
                if child.is_dir():
                    if depth < max_depth_scan and not child.name.startswith("."):
                        frontier.append((child, depth + 1))
                    continue
                try:
                    stat = child.stat()
                except OSError:
                    continue
                total_size += stat.st_size
                if stat.st_mtime > latest_mtime:
                    latest_mtime = stat.st_mtime
        except (PermissionError, OSError):
            summary["notes"].append(f"unreadable subtree at {current}")
            continue
    summary["size_bytes_total"] = total_size
    if latest_mtime > 0:
        summary["last_modified_iso"] = _dt.datetime.fromtimestamp(
            latest_mtime, tz=_dt.timezone.utc,
        ).isoformat()
    return summary


def _build_candidate(path: Path, source: str) -> Candidate:
    summary = _summarise_directory(path)
    return Candidate(
        path=str(path),
        source=source,
        exists=path.exists(),
        manifest_path=summary["manifest_path"],
        manifest_entries=summary["manifest_entries"],
        impostor_personas=summary["impostor_personas"],
        impostor_registers=summary["impostor_registers"],
        last_modified_iso=summary["last_modified_iso"],
        size_bytes_total=summary["size_bytes_total"],
        notes=summary["notes"],
    )


# --------------- Recommendation logic ----------------------------


def _score(c: Candidate) -> tuple[int, int, int, str]:
    """Sort key for ranking candidates.

    Tuple (manifest_entries, impostor_personas, size_bytes, mtime_iso),
    highest first. Manifest entries dominate because that's the
    canonical signal of "this is the real corpus." Ties break to the
    folder with more impostor personas, then by raw size, then by
    most-recent activity (lexicographic on ISO timestamp works for
    UTC strings of equal length).
    """
    return (
        c.manifest_entries,
        c.impostor_personas,
        c.size_bytes_total,
        c.last_modified_iso or "",
    )


def discover(
    *,
    script_path: Path | None = None,
    max_depth: int = 4,
    env_value: str | None = None,
) -> list[Candidate]:
    """Run the full search and return all candidates, ranked.

    The function takes ``script_path`` and ``env_value`` as parameters
    so tests can inject deterministic values instead of touching the
    real filesystem and environment. The default values fall back to
    ``__file__`` and the actual env var.
    """
    if script_path is None:
        script_path = Path(__file__)
    if env_value is None:
        env_value = os.environ.get(ENV_VAR)
    candidates: list[Candidate] = []
    seen: set[str] = set()

    def _add(p: Path, source: str) -> None:
        key = str(p.expanduser().resolve()) if p.exists() else str(p)
        if key in seen:
            return
        seen.add(key)
        candidates.append(_build_candidate(p, source))

    # 1. Env var, if set. Always recorded so the user can see whether
    #    it points anywhere real.
    if env_value:
        _add(Path(env_value).expanduser(), "env_var")
    # 2. Repo sibling (the framework's original default).
    sib = _repo_sibling(script_path)
    if sib is not None:
        _add(sib, "repo_sibling")
    # 3. Filesystem scan of common roots.
    for root in _candidate_dirs():
        for match in _scan_for_marker(root, max_depth=max_depth):
            # Best-effort attribution:
            home = Path.home()
            try:
                rel = match.relative_to(home)
                if rel.parts and rel.parts[0] == "Documents":
                    source = "documents_scan"
                elif rel.parts and rel.parts[0].startswith("Obsidian"):
                    source = "obsidian_scan"
                elif rel.parts and rel.parts[0].startswith("Dropbox"):
                    source = "dropbox_scan"
                elif rel.parts and rel.parts[0].startswith("Library"):
                    source = "icloud_scan"
                elif len(rel.parts) == 1:
                    source = "home_root"
                else:
                    source = "home_scan"
            except ValueError:
                source = "home_scan"
            _add(match, source)

    # Recommendation rule:
    #   1. If the env var is set AND points to an existing folder,
    #      recommend that folder. The user has made the choice
    #      explicit; the script must not override it just because
    #      another folder on disk happens to be larger.
    #   2. Otherwise, rank existing folders by
    #      (manifest_entries, personas, size, mtime), highest first.
    existing = [c for c in candidates if c.exists]
    missing = [c for c in candidates if not c.exists]
    existing.sort(key=_score, reverse=True)
    env_existing = next(
        (c for c in existing if c.source == "env_var"), None,
    )
    if env_existing is not None:
        env_existing.is_recommended = True
        # Move the env-var candidate to the head of the existing list
        # so the report and JSON payload order matches the
        # recommendation.
        existing.remove(env_existing)
        existing.insert(0, env_existing)
    elif existing:
        existing[0].is_recommended = True
    # Preserve env-var-missing at the head of the missing group so the
    # report tells the user "your env var points nowhere":
    return existing + missing


# --------------- Rendering ---------------------------------------


def _humanize_bytes(n: int) -> str:
    """Human-readable size for the report.

    We keep the implementation small (KiB / MiB / GiB; binary units
    match what most filesystems report) rather than pull in a
    dependency. Sub-kilobyte sizes get reported as bytes so empty
    folders read as "47 B" rather than "0.0 MB."
    """
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    return f"{n / (1024 * 1024 * 1024):.2f} GiB"


def render_text(candidates: list[Candidate], *, env_value: str | None) -> str:
    """Human-readable report for `setup` skill consumption.

    Sections:
      1. Header with env-var state.
      2. Per-candidate block.
      3. Recommendation footer with the exact ``export`` line.
    """
    lines: list[str] = []
    lines.append("SETEC baseline discovery")
    lines.append("=" * 60)
    lines.append("")
    if env_value:
        lines.append(f"${ENV_VAR} is set to:")
        lines.append(f"  {env_value}")
    else:
        lines.append(f"${ENV_VAR} is NOT set.")
    lines.append("")
    if not candidates:
        lines.append("No baseline folder found in any standard location.")
        lines.append("")
        lines.append("Standard locations searched:")
        lines.append("  - Repo sibling (next to setec-voiceprint/)")
        lines.append("  - ~/Documents/**")
        lines.append("  - ~/Obsidian*/**, ~/Dropbox*/**, ~/Google Drive*/**, ~/OneDrive*/**")
        lines.append("  - macOS iCloud Drive")
        lines.append("  - ~/ (one level)")
        lines.append("")
        lines.append("If you have an existing baselines folder, set:")
        lines.append(f"  export {ENV_VAR}=\"/path/to/your/{PRIVATE_DIR_NAME}\"")
        lines.append("")
        lines.append("If you do not, one will be created on first use under")
        lines.append("  $HOME/Documents/{PRIVATE_DIR_NAME}".format(
            PRIVATE_DIR_NAME=PRIVATE_DIR_NAME))
        lines.append("Setting the env var first is preferred so future SETEC")
        lines.append("sessions (and cloud-synced setups) agree on one location.")
        return "\n".join(lines)
    lines.append(f"Found {len(candidates)} candidate(s):")
    lines.append("")
    for idx, c in enumerate(candidates, 1):
        marker = " ★ RECOMMENDED" if c.is_recommended else ""
        lines.append(f"  {idx}. {c.path}{marker}")
        lines.append(f"     source: {c.source}")
        if not c.exists:
            lines.append("     (path does not exist)")
        else:
            if c.manifest_path:
                lines.append(
                    f"     manifest: {Path(c.manifest_path).name} "
                    f"({c.manifest_entries} entries)"
                )
            else:
                lines.append("     manifest: (none)")
            if c.impostor_personas:
                regs = ", ".join(c.impostor_registers) or "—"
                lines.append(
                    f"     impostor personas: {c.impostor_personas} "
                    f"across {len(c.impostor_registers)} register(s) "
                    f"({regs})"
                )
            else:
                lines.append("     impostor personas: 0")
            lines.append(f"     size: {_humanize_bytes(c.size_bytes_total)}")
            if c.last_modified_iso:
                lines.append(f"     last modified: {c.last_modified_iso}")
        for note in c.notes:
            lines.append(f"     note: {note}")
        lines.append("")
    recommended = next((c for c in candidates if c.is_recommended), None)
    if recommended is not None:
        lines.append("Recommendation:")
        lines.append(f"  export {ENV_VAR}=\"{recommended.path}\"")
        lines.append("")
        lines.append(
            "Add the line above to your shell rc (~/.zshrc or ~/.bashrc) "
            "to persist across sessions. SETEC's acquisition and voice-"
            "profile scripts honor this variable; without it they fall "
            "back to a sibling-of-repo path that breaks inside git "
            "worktrees."
        )
        # Surface duplicates explicitly:
        other_existing = [c for c in candidates if c.exists and not c.is_recommended]
        if other_existing:
            lines.append("")
            lines.append("Other existing folders were found:")
            for c in other_existing:
                lines.append(f"  - {c.path}")
            lines.append(
                "These may be stale or duplicated. SETEC does not need a "
                "per-repo baseline folder; you can delete a duplicate if "
                "you're confident it's empty or superseded."
            )
    else:
        lines.append("No existing folder qualified as recommended.")
        lines.append(
            f"Set ${ENV_VAR} to one of the missing-path candidates above "
            "if you intend to create it there on first use."
        )
    return "\n".join(lines)


def render_json(candidates: list[Candidate], *, env_value: str | None) -> str:
    """Machine-readable report for the setup skill to parse."""
    recommended = next((c for c in candidates if c.is_recommended), None)
    payload: dict[str, Any] = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "tool_version": SCRIPT_VERSION,
        "env_var_name": ENV_VAR,
        "env_var_set": env_value is not None,
        "env_var_value": env_value,
        "candidates": [asdict(c) for c in candidates],
        "recommended_path": recommended.path if recommended else None,
        "export_line": (
            f"export {ENV_VAR}=\"{recommended.path}\"" if recommended else None
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# --------------- Validate subcommand -----------------------------


def validate_path(path: Path) -> tuple[bool, list[str]]:
    """Decide whether ``path`` looks like a usable baselines dir.

    Rules:
      - must exist
      - must be a directory
      - must end in ``ai-prose-baselines-private`` (so the privacy
        marker check downstream doesn't fail)
      - reports manifest / impostor counts as informational
    """
    issues: list[str] = []
    if not path.exists():
        issues.append("path does not exist")
        return False, issues
    if not path.is_dir():
        issues.append("path exists but is not a directory")
        return False, issues
    if path.name != PRIVATE_DIR_NAME:
        issues.append(
            f"directory name is {path.name!r}, not {PRIVATE_DIR_NAME!r}; "
            "the privacy-marker check requires that exact name in the "
            "path, so SETEC's acquisition tools will refuse to write here"
        )
        return False, issues
    return True, issues


# --------------- CLI ---------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baseline_discovery",
        description=(
            "Locate the user's existing ai-prose-baselines-private/ "
            "folder so a fresh SETEC instance doesn't create a duplicate."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable text",
    )
    p.add_argument(
        "--validate",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "check whether PATH is a usable baselines folder and exit; "
            "does not run the search"
        ),
    )
    p.add_argument(
        "--max-depth",
        type=int,
        default=4,
        help="max directory depth to scan under each search root (default 4)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    env_value = os.environ.get(ENV_VAR)
    if args.validate is not None:
        path = Path(args.validate).expanduser()
        ok, issues = validate_path(path)
        if args.json:
            print(json.dumps({
                "task_surface": TASK_SURFACE,
                "tool": TOOL_NAME,
                "tool_version": SCRIPT_VERSION,
                "validated_path": str(path),
                "ok": ok,
                "issues": issues,
            }, indent=2, sort_keys=True))
        else:
            if ok:
                print(f"OK: {path} is a usable SETEC baselines folder.")
            else:
                print(f"NOT OK: {path}")
                for i in issues:
                    print(f"  - {i}")
        return 0 if ok else 2
    candidates = discover(max_depth=args.max_depth, env_value=env_value)
    if args.json:
        print(render_json(candidates, env_value=env_value))
    else:
        print(render_text(candidates, env_value=env_value))
    # Exit 0 if anything found that exists; exit 1 if nothing exists.
    has_existing = any(c.exists for c in candidates)
    return 0 if has_existing else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
