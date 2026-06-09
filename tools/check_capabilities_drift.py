#!/usr/bin/env python3
"""check_capabilities_drift.py — guard against capability-manifest drift.

The capabilities manifest at `plugins/setec-voiceprint/capabilities.d/`
is the single source of truth for what every user-facing script in
SETEC does. This linter ensures the manifest stays in sync with the
source. It also gates the R5 contract fixtures (Check 9) so a surface's
golden envelope can never silently drift from `build_output`.

It checks the following properties:

  1. **Orphan scripts.** Every Python file under
     `plugins/setec-voiceprint/scripts/` that declares a
     `TASK_SURFACE = "..."` module-level constant must have a manifest
     entry whose `script_path` is that file's repo-relative path.

  2. **Orphan manifest entries.** Every manifest entry's
     `script_path` must point at an existing Python file.

  3. **Surface drift.** Every manifest entry's `surface` field must
     equal the `TASK_SURFACE` constant declared in the source file.

  9. **Fixture drift (R5).** For every consumer surface that has a
     committed golden envelope under
     `plugins/setec-voiceprint/references/contract_fixtures/`, the
     generator's regenerated envelope (post-normalization) must match the
     golden. This catches a surface whose `build_output(...)` output
     drifts from its pinned `schema_version: 1.0` contract before merge.
     The check delegates to `gen_contract_fixtures.check_all()` so the
     gate and the generator can never disagree about what a golden is.

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

    The one exception: a `handoff: stable` entry may NOT be `status:
    todo` (Check 8). A stable surface is a pinned consumer contract;
    a todo placeholder there is incoherent, so the todo content-check
    exemption does not extend to stable entries.

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

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
DEFAULT_MANIFEST = REPO_ROOT / "plugins" / "setec-voiceprint" / "capabilities.d"

# Aggregation lives in the plugin's manifest API (capabilities.py); this tool
# imports the canonical loader rather than re-implementing dir aggregation.
# Dependency direction: repo tools -> the plugin they tool (the plugin stays
# self-contained). Re-exported as module-level names so callers/tests that use
# `drift.load_manifest` / `drift.entries` keep working.
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
from capabilities import entries, load_manifest  # type: ignore  # noqa: E402

# R5 contract-fixture drift (Check 9). Import the generator so the gate and
# the generator share one definition of "what a golden should be" — they can
# never disagree. gen_contract_fixtures is stdlib-only at import (it imports
# the heavy audit scripts lazily, inside each per-surface builder), so this
# import is cheap and dependency-free.
import gen_contract_fixtures  # type: ignore  # noqa: E402

SKIP_FILE_PATTERNS = [
    re.compile(r"^test_"),
    re.compile(r"_test\.py$"),
    re.compile(r"^__init__\.py$"),
]


@dataclass
class Violation:
    kind: str  # "orphan_script" | "orphan_entry" | "surface_drift" | "todo_content" | "stable_is_todo" | "fixture_drift"
    where: str  # path or entry id
    detail: str

    def render(self) -> str:
        return f"  {self.kind}: {self.where}\n    {self.detail}"


# R1 (normalized-entrypoint) field bundle. The presence of `min_setec_version`
# is the bundle marker: a fragment carrying it is a subprocess consumer surface
# and MUST carry the rest of the bundle in valid form. Fragments WITHOUT
# `min_setec_version` are exempt (reference-tagged / internal entries are left
# untouched).
_R1_BUNDLE_MARKER = "min_setec_version"
_VALID_JSON_DELIVERY = frozenset({"stdout", "file"})
_VALID_INPUT_TYPES = frozenset(
    {"path", "string", "int", "float", "enum", "bool"}
)
# Conservative semver: MAJOR.MINOR.PATCH with optional -prerelease/+build. Good
# enough to catch a fat-fingered floor like "1.86" or "v1.86.0" without pulling
# in a packaging dep.
_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def validate_r1_bundle(entry: dict) -> list[str]:
    """Return a list of human-readable problems with `entry`'s R1 field bundle.

    An entry is subject to the bundle iff it carries `min_setec_version` (the
    marker). When present, the FULL bundle is required and validated:

      * `min_setec_version` — a valid semver string.
      * `json_delivery` — one of {stdout, file}.
      * `inputs` — a non-empty list of mappings, each with `flag`, `type`, and
        `required`; `type` in the legal vocabulary; `values` (a non-empty list)
        present iff `type == "enum"`.

    Entries WITHOUT the marker return `[]` (exempt). This is a pure validator
    (no side effects) so both the drift linter and the seeder can reuse it."""
    if _R1_BUNDLE_MARKER not in entry:
        return []
    problems: list[str] = []

    floor = entry.get("min_setec_version")
    if not isinstance(floor, str) or not _SEMVER_RE.match(floor):
        problems.append(
            f"min_setec_version must be a valid semver string "
            f"(MAJOR.MINOR.PATCH); got {floor!r}"
        )

    delivery = entry.get("json_delivery")
    if delivery not in _VALID_JSON_DELIVERY:
        problems.append(
            f"json_delivery must be one of {sorted(_VALID_JSON_DELIVERY)!r}; "
            f"got {delivery!r}"
        )

    inputs = entry.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        problems.append(
            f"inputs must be a non-empty list of mappings; got "
            f"{type(inputs).__name__ if inputs is not None else None!r}"
        )
    else:
        for i, item in enumerate(inputs):
            if not isinstance(item, dict):
                problems.append(
                    f"inputs[{i}] must be a mapping; got "
                    f"{type(item).__name__}"
                )
                continue
            for key in ("flag", "type", "required"):
                if key not in item:
                    problems.append(
                        f"inputs[{i}] missing required key {key!r}"
                    )
            itype = item.get("type")
            if itype is not None and itype not in _VALID_INPUT_TYPES:
                problems.append(
                    f"inputs[{i}].type {itype!r} not in "
                    f"{sorted(_VALID_INPUT_TYPES)!r}"
                )
            has_values = "values" in item
            if itype == "enum":
                vals = item.get("values")
                if not isinstance(vals, list) or not vals:
                    problems.append(
                        f"inputs[{i}] has type 'enum' but `values` is not a "
                        f"non-empty list; got {vals!r}"
                    )
            elif has_values:
                problems.append(
                    f"inputs[{i}] carries `values` but type is "
                    f"{itype!r} (values is only valid for type 'enum')"
                )
    return problems


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
                f"the capabilities.d/ directory is missing at {manifest_path}. "
                "Restore it: one `<id>.yaml` fragment per capability plus "
                "`_meta.yaml` (schema_version)."
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
        # .as_posix() (not str()) so the comparison key matches the manifest's
        # forward-slash script_path values on Windows too; str(Path) yields
        # backslashes there and would flag every script as an orphan. No-op on
        # POSIX. (Same cross-platform path fix as build_baseline_metadata.)
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel not in by_script_path:
            report.violations.append(Violation(
                kind="orphan_script",
                where=rel,
                detail=(
                    f"script declares TASK_SURFACE={surface!r} but no "
                    f"manifest entry references it. Add a "
                    f"`plugins/setec-voiceprint/capabilities.d/<id>.yaml` "
                    f"fragment for it (one entry; `id` = the filename stem)."
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

    # Check 6 (v0.3.0): handoff/consumers shape validation. Catches
    # typos like `handoff: stabel` that pre-fix passed cleanly
    # because the stable_without_references check only inspects
    # entries whose handoff value is literally "stable" — anything
    # else falls through. The downstream consequence is
    # `capabilities.py list --handoff stable --consumer apodictic`
    # silently dropping a pinned surface.
    valid_handoff = frozenset({"stable", "experimental", "internal", "none"})
    for entry in manifest_entries:
        eid = entry.get("id") or "(no id)"
        handoff = entry.get("handoff")
        if handoff is None:
            # Missing handoff field. Pre-v0.3 manifests don't carry
            # the field; flag as drift so they get seeded.
            report.violations.append(Violation(
                kind="missing_handoff",
                where=eid,
                detail=(
                    "v0.3.0 entries must declare a `handoff` field. "
                    "Default to `handoff: none` if you don't intend "
                    "this entry as a consumer surface, then promote "
                    "during curation — edit the entry's "
                    "`capabilities.d/<id>.yaml` fragment."
                ),
            ))
        elif handoff not in valid_handoff:
            report.violations.append(Violation(
                kind="invalid_handoff",
                where=eid,
                detail=(
                    f"handoff value {handoff!r} is not in the legal "
                    f"vocabulary {sorted(valid_handoff)!r}. Did you "
                    f"mean `stable`? A typo here silently drops the "
                    f"entry from `capabilities.py list --handoff "
                    f"stable` queries."
                ),
            ))
        consumers = entry.get("consumers")
        if consumers is None:
            report.violations.append(Violation(
                kind="missing_consumers",
                where=eid,
                detail=(
                    "v0.3.0 entries must declare a `consumers` field "
                    "(empty list `[]` is fine for entries with no "
                    "named downstream integrations)."
                ),
            ))
        elif not isinstance(consumers, list):
            report.violations.append(Violation(
                kind="invalid_consumers_type",
                where=eid,
                detail=(
                    f"`consumers` must be a list of strings; got "
                    f"{type(consumers).__name__}. A scalar here "
                    f"silently dropped the entry from `--consumer X` "
                    f"filters because the filter does an `in` check."
                ),
            ))
        elif any(not isinstance(c, str) for c in consumers):
            report.violations.append(Violation(
                kind="invalid_consumers_type",
                where=eid,
                detail=(
                    "`consumers` must contain only strings; got "
                    f"mixed types: {[type(c).__name__ for c in consumers]}"
                ),
            ))

    # Check 7 (R1 normalized-entrypoint): any fragment carrying the
    # `min_setec_version` bundle marker must carry the WHOLE bundle in valid
    # form (min_setec_version semver + json_delivery in {stdout,file} +
    # structured inputs[]). Fragments without the marker are exempt — this
    # leaves reference-tagged / internal entries untouched. The check triggers
    # on bundle PRESENCE, not on handoff/consumers, so an entry can be
    # handoff: stable without the bundle and vice versa.
    for entry in manifest_entries:
        eid = entry.get("id") or "(no id)"
        for problem in validate_r1_bundle(entry):
            report.violations.append(Violation(
                kind="invalid_r1_bundle",
                where=eid,
                detail=problem,
            ))

    # Check 8 (R1 build-review follow-up): a `handoff: stable` entry must
    # NOT be `status: todo`. A stable surface is a pinned contract that
    # `emit` advertises to consumers; an `status: todo` entry is an
    # uncurated placeholder that `list --handoff stable` hides by default
    # (it skips todos). The two surfaces then disagree about what is
    # stable. A stable contract made of `family: TODO` / `use_when: [TODO]`
    # placeholders is incoherent, so curate the entry (set a real status +
    # fill the content) or drop it to `handoff: none` until it is ready.
    # The placeholder-content half of the guard catches a stable entry
    # that left a real-but-non-todo status while keeping TODO fields; the
    # Check 4 todo_content guard already covers that for non-stable
    # curated entries, but a stable entry should never carry placeholders
    # regardless of its status.
    for entry in manifest_entries:
        if entry.get("handoff") != "stable":
            continue
        eid = entry.get("id") or "(no id)"
        if entry.get("status") == "todo":
            report.violations.append(Violation(
                kind="stable_is_todo",
                where=eid,
                detail=(
                    "handoff is 'stable' but status is 'todo'. A stable "
                    "surface is a pinned consumer contract `emit` "
                    "advertises, but `list --handoff stable` hides "
                    "todos — the two disagree. Curate the entry (real "
                    "status + non-TODO family/purpose/use_when in its "
                    "`capabilities.d/<id>.yaml` fragment) or drop it to "
                    "`handoff: none` until it is ready."
                ),
            ))
            continue
        # Non-todo stable entry: it must not retain placeholder content.
        family = entry.get("family")
        if family == "TODO" or family is None:
            report.violations.append(Violation(
                kind="stable_is_todo",
                where=eid,
                detail=(
                    f"handoff is 'stable' but family is still TODO "
                    f"(status {entry.get('status')!r}). A stable contract "
                    f"must not be made of placeholders — fill the entry's "
                    f"`capabilities.d/<id>.yaml` fragment."
                ),
            ))
        purpose = entry.get("purpose") or ""
        if "TODO" in str(purpose):
            report.violations.append(Violation(
                kind="stable_is_todo",
                where=eid,
                detail=(
                    f"handoff is 'stable' but purpose still contains TODO "
                    f"(status {entry.get('status')!r}). Curate the entry's "
                    f"`capabilities.d/<id>.yaml` fragment."
                ),
            ))
        for list_field in ("use_when", "do_not_use_when"):
            value = entry.get(list_field) or []
            if not value or any(v == "TODO" for v in value):
                report.violations.append(Violation(
                    kind="stable_is_todo",
                    where=eid,
                    detail=(
                        f"handoff is 'stable' but {list_field!r} is empty "
                        f"or still contains TODO (status "
                        f"{entry.get('status')!r}). A stable contract must "
                        f"not be made of placeholders — fill the entry's "
                        f"`capabilities.d/<id>.yaml` fragment."
                    ),
                ))

    # Check 9 (R5 contract fixtures): fixture_matches_build_output. For each
    # consumer surface that has a committed golden envelope under
    # references/contract_fixtures/, assert the generator's regenerated
    # envelope matches it after normalization. Delegated to the generator's
    # own `check_all()` so the gate and the generator share one source of
    # truth — a fixture can never pass `gen_contract_fixtures.py --check`
    # while failing the gate or vice versa. A surface whose envelope drifts
    # from its pinned schema_version 1.0 golden fails SETEC pre-merge.
    for problem in gen_contract_fixtures.check_all():
        # `problem` is already "<surface>: <detail>"; split the surface off
        # so it lands in `where` (matching the rest of the report's shape).
        surface, _, detail = problem.partition(": ")
        report.violations.append(Violation(
            kind="fixture_drift",
            where=surface or "(contract_fixtures)",
            detail=detail or problem,
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
