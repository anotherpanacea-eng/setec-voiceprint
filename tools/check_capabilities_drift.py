#!/usr/bin/env python3
"""check_capabilities_drift.py — guard against capability-manifest drift.

The capabilities manifest at `plugins/setec-voiceprint/capabilities.yaml`
is the single source of truth for what every user-facing script in
SETEC does. This linter ensures the manifest stays in sync with the
source by checking three properties:

  1. **Orphan scripts.** Every Python file under
     `plugins/setec-voiceprint/scripts/` that declares a
     `TASK_SURFACE = "..."` module-level constant must have a manifest
     entry whose `script_path` is that file's repo-relative path.

  2. **Orphan manifest entries.** Every manifest entry's
     `script_path` must point at an existing Python file.

  3. **Surface drift.** Every manifest entry's `surface` field must
     equal the `TASK_SURFACE` constant declared in the source file.

The linter is intentionally noisy: it reports every violation
encountered before exiting with a non-zero status. Exit codes:

    0 — no drift
    1 — drift detected
    2 — internal error

Usage:

    python3 tools/check_capabilities_drift.py
    python3 tools/check_capabilities_drift.py --manifest <path>
    python3 tools/check_capabilities_drift.py --json

Exemptions:

    Entries with `status: todo` are allowed to skip *content-quality*
    checks (use_when, do_not_use_when must be filled). The linter
    still requires them to exist and to point at a real script.
    Hand-curated entries (status != todo) must have non-TODO content
    in their use_when / do_not_use_when blocks.

Run in CI:

    Add this to your CI step list. It's stdlib + PyYAML (the latter
    already in requirements.txt) and runs in < 1 second on a clean
    checkout.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

def _load_yaml():
    """Lazy PyYAML import (see capabilities.py for the same pattern)."""
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise ImportError(
            "check_capabilities_drift requires PyYAML to parse the "
            "manifest (`pip install pyyaml`)"
        ) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPO_ROOT
    / "plugins"
    / "setec-voiceprint"
    / "capabilities.yaml"
)
SCRIPTS_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"

SKIP_FILE_PATTERNS = [
    re.compile(r"^test_"),
    re.compile(r"_test\.py$"),
    re.compile(r"^__init__\.py$"),
]


@dataclass
class Violation:
    kind: str  # "orphan_script" | "orphan_entry" | "surface_drift" | "todo_content"
    where: str  # path or entry id
    detail: str

    def render(self) -> str:
        return f"  {self.kind}: {self.where}\n    {self.detail}"


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    scanned_scripts: int = 0
    scanned_entries: int = 0

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "scanned_scripts": self.scanned_scripts,
            "scanned_entries": self.scanned_entries,
            "violations": [
                {"kind": v.kind, "where": v.where, "detail": v.detail}
                for v in self.violations
            ],
        }


# ---------- source scan -------------------------------------------

def find_scripts() -> list[Path]:
    out: list[Path] = []
    for path in SCRIPTS_ROOT.rglob("*.py"):
        name = path.name
        if any(p.search(name) for p in SKIP_FILE_PATTERNS):
            continue
        if "/tests/" in str(path) or "/__pycache__/" in str(path):
            continue
        out.append(path)
    return sorted(out)


def parse_task_surface(path: Path) -> str | None:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id == "TASK_SURFACE"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    return node.value.value
    return None


# ---------- manifest scan -----------------------------------------

def load_manifest(path: Path) -> dict[str, object]:
    yaml = _load_yaml()
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def entries(manifest: dict) -> list[dict]:
    return list(manifest.get("entries") or [])


# ---------- drift checks ------------------------------------------

def check_drift(
    manifest_path: Path = DEFAULT_MANIFEST,
) -> Report:
    report = Report()

    scripts = find_scripts()
    report.scanned_scripts = len(scripts)

    # Build the source-side index: path → TASK_SURFACE
    source_surfaces: dict[Path, str] = {}
    for path in scripts:
        ts = parse_task_surface(path)
        if ts is not None:
            source_surfaces[path] = ts

    # Load manifest
    try:
        manifest = load_manifest(manifest_path)
    except FileNotFoundError:
        report.violations.append(Violation(
            kind="manifest_missing",
            where=str(manifest_path),
            detail=(
                "manifest does not exist. Run "
                "`python3 tools/seed_capabilities.py --out "
                f"{manifest_path}` to bootstrap."
            ),
        ))
        return report

    manifest_entries = entries(manifest)
    report.scanned_entries = len(manifest_entries)

    # Build manifest-side index: script_path → entry. Per PR #129
    # review, two entries claiming the same script_path silently
    # collided here because we just overwrote the dict — we'd then
    # only check the second entry against source state and the first
    # would vanish from every downstream check. The manifest is meant
    # to be one source of truth per script, so a duplicate script_path
    # is itself a drift bug to surface. Detect first, then build the
    # index from a deduplicated view.
    by_script_path: dict[str, dict] = {}
    seen_ids: set[str] = set()
    seen_paths: dict[str, str] = {}  # script_path → first id that claimed it
    for entry in manifest_entries:
        eid = entry.get("id") or "(no id)"
        if eid in seen_ids:
            report.violations.append(Violation(
                kind="duplicate_id",
                where=eid,
                detail="entry id appears more than once in the manifest",
            ))
        seen_ids.add(eid)
        sp = entry.get("script_path")
        if sp:
            if sp in seen_paths:
                report.violations.append(Violation(
                    kind="duplicate_script_path",
                    where=sp,
                    detail=(
                        f"script_path is claimed by multiple entries "
                        f"({seen_paths[sp]!r} and {eid!r}); the "
                        f"manifest is one-source-of-truth per script, "
                        f"so consolidate or remove the duplicate"
                    ),
                ))
                # Don't overwrite the index — the first claim wins
                # for downstream checks. Operators see the conflict
                # in the violation list.
            else:
                seen_paths[sp] = eid
                by_script_path[sp] = entry

    # Check 1: orphan scripts (TASK_SURFACE-bearing source not in manifest)
    for path, surface in source_surfaces.items():
        rel = str(path.relative_to(REPO_ROOT))
        if rel not in by_script_path:
            report.violations.append(Violation(
                kind="orphan_script",
                where=rel,
                detail=(
                    f"script declares TASK_SURFACE={surface!r} but no "
                    f"manifest entry references it. Run "
                    f"`python3 tools/seed_capabilities.py --out "
                    f"{manifest_path}` to add a seed entry."
                ),
            ))

    # Check 2: orphan manifest entries (manifest references missing file)
    for sp, entry in by_script_path.items():
        full = REPO_ROOT / sp
        if not full.exists():
            report.violations.append(Violation(
                kind="orphan_entry",
                where=entry.get("id") or sp,
                detail=(
                    f"manifest references {sp} but the file does not "
                    f"exist. Was the script removed?"
                ),
            ))

    # Check 3: surface drift
    for sp, entry in by_script_path.items():
        full = REPO_ROOT / sp
        if not full.exists():
            continue
        source_surface = parse_task_surface(full)
        if source_surface is None:
            report.violations.append(Violation(
                kind="surface_drift",
                where=entry.get("id") or sp,
                detail=(
                    f"manifest entry exists but the source file "
                    f"declares no TASK_SURFACE constant. Either "
                    f"add TASK_SURFACE to the script or remove the "
                    f"manifest entry."
                ),
            ))
            continue
        manifest_surface = entry.get("surface")
        if manifest_surface != source_surface:
            report.violations.append(Violation(
                kind="surface_drift",
                where=entry.get("id") or sp,
                detail=(
                    f"manifest surface {manifest_surface!r} != source "
                    f"TASK_SURFACE {source_surface!r}"
                ),
            ))

    # Check 4: hand-curated entries (status != todo) must have non-TODO
    # content in their use_when / do_not_use_when / family fields.
    for entry in manifest_entries:
        if entry.get("status") == "todo":
            continue
        eid = entry.get("id") or "(no id)"
        for field_name in ("family",):
            value = entry.get(field_name)
            if value == "TODO" or value is None:
                report.violations.append(Violation(
                    kind="todo_content",
                    where=eid,
                    detail=(
                        f"status is {entry.get('status')!r} but "
                        f"{field_name!r} is still TODO. Promote or "
                        f"set status: todo."
                    ),
                ))
        for list_field in ("use_when", "do_not_use_when"):
            value = entry.get(list_field) or []
            if not value or any(v == "TODO" for v in value):
                report.violations.append(Violation(
                    kind="todo_content",
                    where=eid,
                    detail=(
                        f"status is {entry.get('status')!r} but "
                        f"{list_field!r} still contains TODO."
                    ),
                ))

    # Check 5 (v0.3.0): handoff: stable entries must carry a
    # non-empty `references` list so consumers can find the
    # integration spec the entry's stable-contract promise points
    # at. Without this gate, an entry can claim "pin against me"
    # without any document describing what to pin to.
    for entry in manifest_entries:
        if entry.get("handoff") != "stable":
            continue
        eid = entry.get("id") or "(no id)"
        refs = entry.get("references") or []
        if not refs:
            report.violations.append(Violation(
                kind="stable_without_references",
                where=eid,
                detail=(
                    "handoff is 'stable' but `references` is empty. "
                    "Add at least one path to an integration spec "
                    "or surface doc (typically the audit's own spec "
                    "doc in references/) so downstream consumers "
                    "can find what they're pinning against."
                ),
            ))

    return report


# ---------- CLI ----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lint the SETEC capabilities manifest for drift vs. "
            "source files."
        ),
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST,
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON report instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    report = check_drift(args.manifest)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0 if report.passed else 1

    print(
        f"Scanned {report.scanned_scripts} scripts, "
        f"{report.scanned_entries} manifest entries."
    )
    if report.passed:
        print("Capabilities manifest is consistent with sources. ✔")
        return 0
    print(
        f"\nFound {len(report.violations)} violation"
        f"{'s' if len(report.violations) != 1 else ''}:\n"
    )
    by_kind: dict[str, list[Violation]] = {}
    for v in report.violations:
        by_kind.setdefault(v.kind, []).append(v)
    for kind in sorted(by_kind):
        print(f"[{kind}] ({len(by_kind[kind])})")
        for v in by_kind[kind]:
            print(v.render())
        print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
