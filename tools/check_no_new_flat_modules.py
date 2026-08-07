#!/usr/bin/env python3
"""check_no_new_flat_modules.py — freeze the flat-tree module set.

Per `specs/svp-packaging-conversion.md` P5: this is what makes the
190-module flat tree at `plugins/setec-voiceprint/scripts/*.py` stop
GROWING regardless of whether the relocation phases (P2-P4) ever land.
It pins the current set of top-level `scripts/*.py` module names as a
frozen `baseline`; a NEW top-level module fails the gate unless it is
added to the committed exemptions file with `owner`, `reason`,
`introduced_sha`, and `removal_phase`.

Scope is deliberately narrow and NON-recursive: only files directly in
`plugins/setec-voiceprint/scripts/` (`scripts/*.py`, no subdirectories)
count as "flat modules" -- `scripts/calibration/*.py`,
`scripts/setec/**/*.py`, `scripts/tests/*.py`, etc. are a different
question (governed by check_layering.py's tier predicates, not this
ratchet) and are not scanned here at all.

The `baseline` list is itself frozen once committed: `--strict`
verifies it is byte-identical to the merge-base's `baseline` (or that
the merge base has none yet -- this PR's own bootstrap case) so nobody
can dodge the exemption requirement by hand-editing a new name straight
into `baseline` instead of adding a reviewed exemption row.

Exit codes:

    0 — every current flat module is in the baseline or a valid exemption
    1 — an unbaselined/unexempted flat module, a malformed exemption row,
        or (--strict) a mutated baseline / ghost exemption row
    2 — internal error (--strict's merge base unavailable)

Usage:

    python3 tools/check_no_new_flat_modules.py
    python3 tools/check_no_new_flat_modules.py --strict
    python3 tools/check_no_new_flat_modules.py --seed-baseline --introduced-sha <sha>
    python3 tools/check_no_new_flat_modules.py --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
EXEMPTIONS_PATH = PLUGIN_ROOT / "flat_module_exemptions.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import enable_utf8_stdio  # noqa: E402

REQUIRED_EXEMPTION_FIELDS = ("module", "reason", "owner", "introduced_sha", "removal_phase")
_LEGAL_REMOVAL_PHASES = {"P2", "P3", "P4", "P5", "not-applicable"}


def find_flat_modules() -> list[str]:
    """Basenames of every `scripts/*.py` file, NON-recursive."""
    return sorted(p.name for p in SCRIPTS_ROOT.glob("*.py"))


def _load_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise ImportError(
            "check_no_new_flat_modules.py requires PyYAML (`pip install pyyaml`)"
        ) from exc


def _load_doc(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = EXEMPTIONS_PATH
    if not path.exists():
        return {"schema_version": 1, "baseline": [], "exemptions": []}
    yaml = _load_yaml()
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a top-level mapping")
    return doc


def load_baseline(path: Path | None = None) -> list[str]:
    doc = _load_doc(path)
    baseline = doc.get("baseline", [])
    if not isinstance(baseline, list):
        raise ValueError(f"{path or EXEMPTIONS_PATH}: `baseline` must be a list")
    return baseline


def load_exemptions(path: Path | None = None) -> list[dict[str, Any]]:
    doc = _load_doc(path)
    rows = doc.get("exemptions", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path or EXEMPTIONS_PATH}: `exemptions` must be a list")
    return rows


def validate_exemption_rows(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        where = f"exemptions[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not a mapping")
            continue
        for f in REQUIRED_EXEMPTION_FIELDS:
            if not row.get(f):
                problems.append(f"{where} ({row.get('module')}): missing/empty `{f}`")
        phase = row.get("removal_phase")
        if phase is not None and phase not in _LEGAL_REMOVAL_PHASES:
            problems.append(
                f"{where} ({row.get('module')}): removal_phase {phase!r} not "
                f"in {sorted(_LEGAL_REMOVAL_PHASES)}"
            )
        mod = row.get("module")
        if mod in seen:
            problems.append(f"{where}: duplicate exemption for module {mod!r}")
        seen.add(mod)
    return problems


# ---------- CLI: seed-baseline ------------------------------------------


def cmd_seed_baseline(args: argparse.Namespace) -> int:
    """Freeze the CURRENT flat-module set as `baseline`. Intended to run
    exactly once, at this ratchet's introduction -- re-running it later
    would silently defeat the ratchet by re-baselining whatever has
    accumulated, so it refuses to overwrite an existing non-empty
    baseline without `--force`."""
    yaml = _load_yaml()
    existing = _load_doc() if EXEMPTIONS_PATH.exists() else {}
    if existing.get("baseline") and not args.force:
        print(
            f"error: {EXEMPTIONS_PATH} already has a non-empty `baseline` "
            "(this ratchet is already seeded). New top-level modules go "
            "through `exemptions`, not a re-seeded baseline. Pass --force "
            "to override (should not be needed in normal operation).",
            file=sys.stderr,
        )
        return 2

    modules = find_flat_modules()
    doc = {
        "schema_version": 1,
        "baseline": modules,
        "baseline_introduced_sha": args.introduced_sha,
        "exemptions": existing.get("exemptions", []),
    }
    EXEMPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXEMPTIONS_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            "# flat_module_exemptions.yaml -- the single exemptions file "
            "for tools/check_no_new_flat_modules.py.\n"
            "#\n"
            "# `baseline` is FROZEN: the exact set of top-level "
            "plugins/setec-voiceprint/scripts/*.py module names at the "
            "P5 ratchet's introduction. It must never be hand-edited to "
            "add a new name -- `--strict` verifies it is byte-identical "
            "to its value at the merge base. A genuinely new top-level "
            "module gets a row in `exemptions` instead, with `module`, "
            "`reason`, `owner`, `introduced_sha`, and `removal_phase`.\n"
            "#\n"
            "# Regenerate baseline (should not be needed again) with: "
            "python3 tools/check_no_new_flat_modules.py --seed-baseline "
            "--introduced-sha <sha> --force\n"
        )
        yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
    print(f"Wrote {len(modules)}-module baseline to {EXEMPTIONS_PATH}")
    return 0


# ---------- CLI: check ---------------------------------------------------


class FlatModuleCheckError(RuntimeError):
    pass


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FlatModuleCheckError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _merge_base(base_ref: str) -> str:
    try:
        return _run_git(["merge-base", "HEAD", base_ref]).strip()
    except FlatModuleCheckError as exc:
        raise FlatModuleCheckError(
            f"could not resolve a merge base against {base_ref!r}: {exc}"
        ) from exc


def _doc_at(sha: str) -> dict[str, Any] | None:
    rel = EXEMPTIONS_PATH.relative_to(REPO_ROOT).as_posix()
    proc = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    yaml = _load_yaml()
    doc = yaml.safe_load(proc.stdout) or {}
    return doc if isinstance(doc, dict) else {}


def check_baseline_frozen(base_sha: str) -> list[str]:
    """`--strict` only: `baseline` must be byte-identical to its value at
    the merge base (or absent there -- this ratchet's own introducing
    commit)."""
    old_doc = _doc_at(base_sha)
    if old_doc is None or "baseline" not in old_doc:
        return []
    old_baseline = old_doc.get("baseline") or []
    new_baseline = load_baseline()
    if sorted(old_baseline) != sorted(new_baseline):
        added = sorted(set(new_baseline) - set(old_baseline))
        removed = sorted(set(old_baseline) - set(new_baseline))
        detail = []
        if added:
            detail.append(f"added: {added}")
        if removed:
            detail.append(f"removed: {removed}")
        return [
            "`baseline` in flat_module_exemptions.yaml was mutated since "
            f"the merge base {base_sha} ({'; '.join(detail)}). baseline is "
            "frozen -- a new top-level module must go through `exemptions`, "
            "never a hand-edited baseline entry."
        ]
    return []


def check_ghost_exemptions(rows: list[dict[str, Any]], current: set[str]) -> list[str]:
    problems = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        mod = row.get("module")
        if mod not in current:
            problems.append(
                f"module {mod!r} has a committed exemption row but no matching "
                "top-level scripts/*.py file exists -- a ghost/expired row. "
                "Delete it (the module was removed) or reconcile."
            )
    return problems


def cmd_check(args: argparse.Namespace) -> int:
    current = find_flat_modules()
    current_set = set(current)

    try:
        baseline = set(load_baseline())
        rows = load_exemptions()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    shape_problems = validate_exemption_rows(rows)
    exempted = {r.get("module") for r in rows if isinstance(r, dict)}
    allowed = baseline | exempted

    new_modules = sorted(current_set - allowed)

    strict_problems: list[str] = []
    if args.strict:
        strict_problems.extend(check_ghost_exemptions(rows, current_set))
        try:
            base_sha = _merge_base(args.base_ref)
        except FlatModuleCheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        strict_problems.extend(check_baseline_frozen(base_sha))

    passed = not shape_problems and not new_modules and not strict_problems

    if args.json:
        print(json.dumps({
            "passed": passed,
            "current_count": len(current),
            "baseline_count": len(baseline),
            "exemption_count": len(rows),
            "new_modules": new_modules,
            "exemption_shape_problems": shape_problems,
            "strict_problems": strict_problems,
        }, indent=2))
        return 0 if passed else 1

    print(
        f"Scanned {len(current)} top-level scripts/*.py module(s); "
        f"baseline={len(baseline)}, exemptions={len(rows)}."
        + (" (--strict)" if args.strict else "")
    )
    if passed:
        print("No new unbaselined/unexempted flat module. ✔")
        return 0

    if new_modules:
        print(f"\n{len(new_modules)} new top-level module(s) not in baseline or exemptions:\n")
        for m in new_modules:
            print(f"  {m}")
        print(
            "\nEither don't add a new flat top-level module, or add a row to "
            f"{EXEMPTIONS_PATH} with module/reason/owner/introduced_sha/removal_phase."
        )
    if shape_problems:
        print(f"\n{len(shape_problems)} exemption-file shape problem(s):\n")
        for p in shape_problems:
            print(f"  {p}")
    if strict_problems:
        print(f"\n{len(strict_problems)} --strict violation(s):\n")
        for p in strict_problems:
            print(f"  {p}")
    return 1


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Freeze the top-level plugins/setec-voiceprint/scripts/*.py "
            "module set; a new one fails unless exempted."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--seed-baseline", action="store_true",
        help="Write the CURRENT flat-module set as `baseline` (one-time; "
             "use --force to override an already-seeded baseline).",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--introduced-sha", default="unknown",
        help="Base SHA recorded on the baseline (--seed-baseline only).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Also reject a ghost exemption row and a mutated baseline.",
    )
    parser.add_argument(
        "--base-ref", default="origin/main",
        help="Ref to compute the merge base against (--strict only).",
    )
    args = parser.parse_args(argv)

    if args.seed_baseline:
        return cmd_seed_baseline(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
