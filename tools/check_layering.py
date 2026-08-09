#!/usr/bin/env python3
"""check_layering.py — enforce the L0/L1/L2 dependency layering ratchet.

Per `specs/svp-packaging-conversion.md` §4 ("Layering and movement
predicates") and P5 ("Turn on layering ... ratchets in CI"). This
checker derives the LIVE internal import graph by AST across
plugin-runtime source under `plugins/setec-voiceprint/scripts/` and
classifies every module into a tier BY PREDICATE, never by directory —
the whole point is that these properties hold against today's flat
tree (zero modules relocated) and survive unchanged whether or not the
relocation tail (P2-P4) ever lands:

    L0 (contract)    — module basename is one of output_schema,
                        claim_license, capabilities.
    L1 (pure library) — not L0, and has none of: a capabilities.d
                        fragment, a `__main__` CLI entry, an
                        envelope emitted via `build_output(...)`.
    L2 (surface)      — not L0, and has at least one of those three.

Enforced (errors, exit 1 unless exempted):

    * An L0 module may not import an internal module in another layer
      (L0 -> L0 is fine; L0 -> L1 or L0 -> L2 is a violation).
    * An L1 module may import only L0/L1 (L1 -> L2 is a violation).
    * An L2 -> L2 edge is a violation UNLESS it is in the committed,
      SHRINK-ONLY baseline (the count may only decrease; a NEW L2->L2
      edge not in the baseline always fails, `--strict` or not).
      ONE sanctioned exception: edges FROM the R5 contract-fixture
      generator (gen_contract_fixtures.py). Its faithfulness contract
      requires importing each golden surface's real envelope-assembly
      path, so a surface joining the golden regime necessarily adds
      one generator edge; refusing it would close the golden regime
      to new surfaces permanently. Such a row may be ADDED to the
      baseline; every other from_path stays shrink-only.

Reported, never gated: cycles (SCCs of size > 1) in the internal
import graph. Five are known to exist at the P5 baseline; this
checker prints whatever it currently finds every run but never fails
on their account (fixing them is a semantic change outside a
packaging-only PR's scope).

Exemptions live in the SAME file `check_packaging_migration.py` owns
(`plugins/setec-voiceprint/packaging_migration_exemptions.yaml`), per
spec §4 ("Layer exemptions use the same migration-exemptions file"),
under a second top-level key `layer_exemptions:` this tool owns
exclusively — `check_packaging_migration.py --seed` preserves that key
verbatim (round-trip covered by its own tests) and this tool's own
`--seed` preserves the `exemptions:` key verbatim in return. Every row
needs `from_path`, `to_path`, `edge_kind`, `reason`, `owner`,
`introduced_sha`, `removal_phase` — the same six-ish shape as the
anchor exemptions, adapted to an edge instead of an anchor.

Exit codes:

    0 — no unexempted violation
    1 — an unexempted violation, or a malformed/stale exemption row
    2 — internal error (e.g. --strict's merge base unavailable)

Usage:

    python3 tools/check_layering.py                 # gate (CI)
    python3 tools/check_layering.py --strict         # + ghost/ratchet checks
    python3 tools/check_layering.py --seed           # (re)write layer_exemptions
    python3 tools/check_layering.py --json
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"
SCRIPTS_ROOT = PLUGIN_ROOT / "scripts"
CAPABILITIES_D = PLUGIN_ROOT / "capabilities.d"
# Same file check_packaging_migration.py owns the `exemptions:` key of.
EXEMPTIONS_PATH = PLUGIN_ROOT / "packaging_migration_exemptions.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import enable_utf8_stdio  # noqa: E402

_EXCLUDED_DIR_PARTS = {"tests", "__pycache__"}

# L0 predicate: contract modules, by identity (basename), not directory --
# per specs/svp-packaging-conversion.md §4 and this PR's build contract.
_L0_STEMS = {"output_schema", "claim_license", "capabilities"}

# The one from_path allowed to ADD l2_to_l2 baseline rows (see header):
# the R5 golden generator, whose job is importing every golden surface.
_GENERATOR_FROM_PATH = "plugins/setec-voiceprint/scripts/gen_contract_fixtures.py"

EDGE_KINDS = ("l0_outbound", "l1_to_l2", "l2_to_l2")
REQUIRED_LAYER_FIELDS = (
    "from_path", "to_path", "edge_kind", "reason", "owner",
    "introduced_sha", "removal_phase",
)
_LEGAL_REMOVAL_PHASES = {"P2", "P3", "P4", "P5", "not-applicable"}


# ---------- module discovery ---------------------------------------


def find_runtime_scripts() -> list[Path]:
    """Every `.py` file under `scripts/`, excluding tests/__pycache__.

    Deliberately independent of check_packaging_migration.find_runtime_scripts():
    that function ALSO excludes `setec/paths.py` (its own `__file__` anchor
    is intrinsic to the anchor-scan job) -- a narrowing that is specific to
    the anchor ratchet and wrong here. Layering must see every real
    plugin-runtime module, `paths.py` included.
    """
    out = []
    for p in SCRIPTS_ROOT.rglob("*.py"):
        rel = p.relative_to(SCRIPTS_ROOT)
        if any(part in _EXCLUDED_DIR_PARTS for part in rel.parts):
            continue
        out.append(p)
    return sorted(out)


@dataclass
class Module:
    path: Path
    rel: Path = field(init=False)        # relative to SCRIPTS_ROOT
    repo_rel: str = field(init=False)    # repo-relative posix path
    stem: str = field(init=False)
    tree: ast.Module = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.rel = self.path.relative_to(SCRIPTS_ROOT)
        self.repo_rel = self.path.relative_to(REPO_ROOT).as_posix()
        self.stem = self.path.stem
        try:
            source = self.path.read_text(encoding="utf-8")
            self.tree = ast.parse(source, filename=str(self.path))
        except (SyntaxError, UnicodeDecodeError):
            self.tree = ast.parse("")


def _is_package_dir(d: Path) -> bool:
    return (d / "__init__.py").exists()


def _module_package(m: Module) -> tuple[str, ...]:
    """Dotted package tuple `m` lives in, for relative-import resolution.
    Walks from SCRIPTS_ROOT down `m`'s directory chain and stops at the
    first directory that is NOT a real package (no `__init__.py`)."""
    dir_parts = m.rel.parts[:-1]
    for i in range(len(dir_parts)):
        d = SCRIPTS_ROOT.joinpath(*dir_parts[: i + 1])
        if not _is_package_dir(d):
            return dir_parts[:i]
    return dir_parts


def _build_dotted_index(modules: list[Module]) -> dict[str, Module]:
    """dotted module path -> Module, ONLY through directories that are
    real packages (contain `__init__.py`) all the way from SCRIPTS_ROOT.
    `calibration/` and `external_mirror/` have no `__init__.py` today, so
    their modules are reached only via flat stem resolution below --
    matching their actual runtime bootstrap (each per-file launcher adds
    its OWN directory, then `scripts/`, to `sys.path`; nothing imports
    them as `calibration.foo`)."""
    index: dict[str, Module] = {}
    for m in modules:
        dir_parts = m.rel.parts[:-1]
        if not all(
            _is_package_dir(SCRIPTS_ROOT.joinpath(*dir_parts[: i + 1]))
            for i in range(len(dir_parts))
        ):
            continue
        dotted = ".".join(dir_parts) if m.stem == "__init__" else ".".join(dir_parts + (m.stem,))
        if dotted:
            index[dotted] = m
    return index


def _build_stem_index(modules: list[Module]) -> dict[str, list[Module]]:
    idx: dict[str, list[Module]] = {}
    for m in modules:
        if m.stem == "__init__":
            continue
        idx.setdefault(m.stem, []).append(m)
    return idx


def _resolve_bare(
    name: str, importer: Module,
    dotted_index: dict[str, Module], stem_index: dict[str, list[Module]],
) -> Module | None:
    """Resolve an undotted `import name` / `from name import ...` the way
    this codebase's flat sys.path bootstraps actually resolve it at
    runtime: prefer an unambiguous stem match; when more than one file
    shares a stem, prefer one in the IMPORTER's own directory (a launcher
    importing a same-directory sibling), then a flat scripts-root file
    (the common `PARENT_SCRIPTS` bootstrap pattern); otherwise unresolved
    (ambiguous -- not claimed as an edge either way)."""
    if name in dotted_index:
        return dotted_index[name]
    candidates = stem_index.get(name)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    same_dir = [c for c in candidates if c.rel.parent == importer.rel.parent]
    if len(same_dir) == 1:
        return same_dir[0]
    flat = [c for c in candidates if len(c.rel.parts) == 1]
    if len(flat) == 1:
        return flat[0]
    return None


def _resolve_from_import(
    node: ast.ImportFrom, importer: Module,
    dotted_index: dict[str, Module], stem_index: dict[str, list[Module]],
) -> list[Module]:
    if node.level and node.level > 0:
        pkg = _module_package(importer)
        if node.level - 1 > len(pkg):
            return []
        base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
        mod_part = node.module
        dotted = ".".join(base + tuple(mod_part.split("."))) if mod_part else ".".join(base)
        target = dotted_index.get(dotted)
        if target:
            return [target]
        results = []
        for alias in node.names:
            sub = (
                ".".join(base + (mod_part,) + (alias.name,))
                if mod_part else ".".join(base + (alias.name,))
            )
            t2 = dotted_index.get(sub)
            if t2:
                results.append(t2)
        return results

    module_name = node.module or ""

    # Prefer the more specific submodule-of-package resolution first --
    # `from setec import consumer_client` really means "reach
    # setec/consumer_client.py", not just "execute setec/__init__.py".
    submodule_hits = []
    for alias in node.names:
        sub = f"{module_name}.{alias.name}" if module_name else alias.name
        t2 = dotted_index.get(sub)
        if t2:
            submodule_hits.append(t2)
    if submodule_hits:
        return submodule_hits

    if module_name in dotted_index:
        return [dotted_index[module_name]]

    if "." not in module_name:
        r = _resolve_bare(module_name, importer, dotted_index, stem_index)
        if r:
            return [r]
    return []


def build_internal_graph(modules: list[Module]) -> set[tuple[str, str]]:
    """`{(importer_repo_rel, imported_repo_rel), ...}` -- every resolved
    internal-to-internal import edge in the live AST, module-level and
    deferred (function-body) imports alike."""
    dotted_index = _build_dotted_index(modules)
    stem_index = _build_stem_index(modules)
    edges: set[tuple[str, str]] = set()
    for m in modules:
        for node in ast.walk(m.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    target = None
                    if name in dotted_index:
                        target = dotted_index[name]
                    elif "." not in name:
                        target = _resolve_bare(name, m, dotted_index, stem_index)
                    if target is not None and target is not m:
                        edges.add((m.repo_rel, target.repo_rel))
            elif isinstance(node, ast.ImportFrom):
                for target in _resolve_from_import(node, m, dotted_index, stem_index):
                    if target is not m:
                        edges.add((m.repo_rel, target.repo_rel))
    return edges


# ---------- tier classification -------------------------------------


def _has_main_entry(tree: ast.Module) -> bool:
    """Whether the module has an `if __name__ == "__main__":` guard
    anywhere (a real `__main__` CLI entry point)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)):
            continue
        sides = (test.left, test.comparators[0])
        has_name = any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
        has_main = any(
            isinstance(s, ast.Constant) and s.value == "__main__" for s in sides
        )
        if has_name and has_main:
            return True
    return False


def _emits_envelope(tree: ast.Module) -> bool:
    """Whether the module calls `build_output(...)` anywhere -- bare
    (`from output_schema import build_output`) or attribute-qualified."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "build_output":
                return True
            if isinstance(f, ast.Attribute) and f.attr == "build_output":
                return True
    return False


def _load_capability_script_paths() -> set[str]:
    """Every `script_path` registered anywhere in capabilities.d/, via the
    plugin's OWN canonical loader (capabilities.load_manifest) -- the same
    aggregator check_capabilities_drift.py uses, so this tool can never
    disagree with it about what counts as a capability fragment."""
    scripts_root_str = str(SCRIPTS_ROOT)
    added = scripts_root_str not in sys.path
    if added:
        sys.path.insert(0, scripts_root_str)
    try:
        from capabilities import entries, load_manifest  # type: ignore
        manifest = load_manifest()
        return {e["script_path"] for e in entries(manifest) if e.get("script_path")}
    finally:
        if added:
            sys.path.remove(scripts_root_str)


def classify_tiers(
    modules: list[Module], cap_script_paths: set[str],
) -> dict[str, str]:
    tiers: dict[str, str] = {}
    for m in modules:
        if m.stem in _L0_STEMS:
            tiers[m.repo_rel] = "L0"
            continue
        has_cap = m.repo_rel in cap_script_paths
        has_main = _has_main_entry(m.tree)
        has_env = _emits_envelope(m.tree)
        tiers[m.repo_rel] = "L2" if (has_cap or has_main or has_env) else "L1"
    return tiers


# ---------- violations -----------------------------------------------


@dataclass
class Violation:
    from_path: str
    to_path: str
    edge_kind: str  # one of EDGE_KINDS


def find_violations(
    edges: set[tuple[str, str]], tiers: dict[str, str],
) -> list[Violation]:
    out = []
    for a, b in sorted(edges):
        ta, tb = tiers.get(a), tiers.get(b)
        if ta == "L0" and tb != "L0":
            out.append(Violation(a, b, "l0_outbound"))
        elif ta == "L1" and tb == "L2":
            out.append(Violation(a, b, "l1_to_l2"))
        elif ta == "L2" and tb == "L2":
            out.append(Violation(a, b, "l2_to_l2"))
    return out


# ---------- cycles (reported, never gated) ----------------------------


def find_cycles(edges: set[tuple[str, str]]) -> list[list[str]]:
    """Strongly-connected components of size > 1 in the internal import
    graph -- Tarjan's algorithm, iterative to avoid recursion-depth
    issues on a ~250-node graph with deep import chains."""
    graph: dict[str, set[str]] = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set())

    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    sccs: list[list[str]] = []

    for start in sorted(graph):
        if start in index:
            continue
        # iterative DFS: (node, iterator-position, parent) frames
        work: list[tuple[str, list[str], int]] = [(start, sorted(graph[start]), 0)]
        index[start] = index_counter[0]
        lowlink[start] = index_counter[0]
        index_counter[0] += 1
        stack.append(start)
        on_stack[start] = True

        while work:
            v, children, pos = work[-1]
            if pos < len(children):
                w = children[pos]
                work[-1] = (v, children, pos + 1)
                if w not in index:
                    index[w] = index_counter[0]
                    lowlink[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    work.append((w, sorted(graph[w]), 0))
                elif on_stack.get(w):
                    lowlink[v] = min(lowlink[v], index[w])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(comp)

    return sorted((c for c in sccs if len(c) > 1), key=lambda c: sorted(c))


# ---------- exemptions file (layer_exemptions: key) --------------------


def _load_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise ImportError(
            "check_layering.py requires PyYAML (`pip install pyyaml`)"
        ) from exc


def load_layer_exemptions(path: Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        path = EXEMPTIONS_PATH
    if not path.exists():
        return []
    yaml = _load_yaml()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a top-level mapping")
    rows = data.get("layer_exemptions", [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: `layer_exemptions` must be a list")
    return rows


def _exemption_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (row.get("from_path"), row.get("to_path"), row.get("edge_kind"))


def validate_layer_exemption_rows(rows: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for i, row in enumerate(rows):
        where = f"layer_exemptions[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not a mapping")
            continue
        for f in REQUIRED_LAYER_FIELDS:
            if not row.get(f):
                problems.append(f"{where} ({row.get('from_path')}): missing/empty `{f}`")
        kind = row.get("edge_kind")
        if kind is not None and kind not in EDGE_KINDS:
            problems.append(
                f"{where} ({row.get('from_path')}): edge_kind {kind!r} not in {EDGE_KINDS}"
            )
        phase = row.get("removal_phase")
        if phase is not None and phase not in _LEGAL_REMOVAL_PHASES:
            problems.append(
                f"{where} ({row.get('from_path')}): removal_phase {phase!r} not "
                f"in {sorted(_LEGAL_REMOVAL_PHASES)}"
            )
        key = _exemption_key(row)
        if key in seen:
            problems.append(f"{where}: duplicate exemption for {key}")
        seen.add(key)
    return problems


# ---------- CLI: seed --------------------------------------------------


_L0_OUTBOUND_REASONS: dict[tuple[str, str], str] = {
    (
        "plugins/setec-voiceprint/scripts/capabilities.py",
        "plugins/setec-voiceprint/scripts/s5_distance.py",
    ): (
        "capabilities.py's `emit` R1-query-envelope command deliberately "
        "introspects s5_distance.compute_s5(...)['method'] so the "
        "contract block's method field stays live without a hand-edit on "
        "drift (see the docstring above the emit builder: 'a real "
        "computed result keeps this field live without that edit'). This "
        "is intentional runtime coupling for a shipped feature, not "
        "migration debt -- no relocation phase removes it."
    ),
    (
        "plugins/setec-voiceprint/scripts/capabilities.py",
        "plugins/setec-voiceprint/scripts/setec/consumer_client.py",
    ): (
        "capabilities.py's _manifest_schema_meets_contract_floor imports "
        "setec.consumer_client for the shared FLOOR-comparison version "
        "parser (meets_floor) that "
        "fleet-coordination/specs/setec-consumer-client-contract.md "
        "supplies as the one shared client. Intentional cross-tier "
        "coupling to the shared client module, not migration debt."
    ),
}


def _seed_rows(edges: set[tuple[str, str]], tiers: dict[str, str], base_sha: str) -> list[dict[str, str]]:
    violations = find_violations(edges, tiers)
    rows = []
    for v in violations:
        if v.edge_kind == "l0_outbound":
            reason = _L0_OUTBOUND_REASONS.get(
                (v.from_path, v.to_path),
                (
                    f"L0 module {v.from_path} imports internal module "
                    f"{v.to_path} (tier {tiers.get(v.to_path)}). Not yet "
                    "hand-reviewed for a specific fix; frozen into the P5 "
                    "baseline rather than promising an unverified removal."
                ),
            )
            removal_phase = "not-applicable"
        elif v.edge_kind == "l1_to_l2":
            reason = (
                f"Pre-existing L1→L2 dependency frozen into the P5 "
                f"enforcement baseline: {v.from_path} imports L2 module "
                f"{v.to_path}. Not yet analyzed for a specific fix "
                "(possible future direction: invert via a registry so the "
                "L2 module registers itself rather than the L1 module "
                "reaching down); captured as a baseline row rather than "
                "promising an unverified removal phase."
            )
            removal_phase = "not-applicable"
        else:  # l2_to_l2
            reason = (
                f"Pre-existing L2→L2 dependency frozen into the P5 "
                "SHRINK-ONLY baseline (specs/svp-packaging-conversion.md "
                f"§4): {v.from_path} imports {v.to_path} directly. "
                "L2→L2 sharing between capability-bearing surfaces is "
                "real architecture (e.g. acquisition_core/variance_audit "
                "are widely-shared L2 libraries), not migration debt -- no "
                "relocation phase clears this row wholesale. The baseline "
                "may only shrink from here; a NEW L2→L2 edge always "
                "fails, --strict or not."
            )
            removal_phase = "not-applicable"
        rows.append({
            "from_path": v.from_path,
            "to_path": v.to_path,
            "edge_kind": v.edge_kind,
            "reason": reason,
            "owner": "packaging",
            "introduced_sha": base_sha,
            "removal_phase": removal_phase,
        })
    rows.sort(key=lambda r: (r["edge_kind"], r["from_path"], r["to_path"]))
    return rows


def cmd_seed(args: argparse.Namespace) -> int:
    yaml = _load_yaml()
    modules = [Module(p) for p in find_runtime_scripts()]
    cap_paths = _load_capability_script_paths()
    tiers = classify_tiers(modules, cap_paths)
    edges = build_internal_graph(modules)
    rows = _seed_rows(edges, tiers, args.introduced_sha)

    # Preserve every OTHER top-level key already committed in this file --
    # notably `exemptions:`, owned by check_packaging_migration.py --
    # symmetric to the merge-preserving fix that tool's own --seed makes
    # for THIS key (`layer_exemptions:`).
    existing: dict[str, Any] = {}
    if EXEMPTIONS_PATH.exists():
        try:
            existing = yaml.safe_load(EXEMPTIONS_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}

    schema_version = existing.get("schema_version", 1)
    preserved_other = {
        k: v for k, v in existing.items()
        if k not in ("schema_version", "layer_exemptions")
    }

    # Stable, readable key order: schema_version, exemptions (if present),
    # layer_exemptions, then anything else preserved verbatim.
    ordered: dict[str, Any] = {"schema_version": schema_version}
    if "exemptions" in preserved_other:
        ordered["exemptions"] = preserved_other.pop("exemptions")
    ordered["layer_exemptions"] = rows
    ordered.update(preserved_other)

    EXEMPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXEMPTIONS_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            "# packaging_migration_exemptions.yaml -- shared by "
            "tools/check_packaging_migration.py (`exemptions:`) and "
            "tools/check_layering.py (`layer_exemptions:`), per "
            "specs/svp-packaging-conversion.md §4 (\"Layer exemptions "
            "use the same migration-exemptions file\").\n"
            "#\n"
            "# layer_exemptions regenerate with: python3 "
            "tools/check_layering.py --seed --introduced-sha <sha>\n"
            "# Then hand-review the diff -- a `--seed` run freezes every "
            "CURRENT violation as exempt; it does not decide whether a "
            "NEW violation introduced by an unrelated PR should be. "
            "l2_to_l2 rows are a SHRINK-ONLY baseline: a row may be "
            "deleted (the edge was removed) but never added for an edge "
            "that didn't already exist at the last seed.\n"
        )
        yaml.safe_dump(ordered, fh, sort_keys=False, default_flow_style=False)
    print(f"Wrote {len(rows)} layer_exemptions row(s) to {EXEMPTIONS_PATH}")
    return 0


# ---------- CLI: check --------------------------------------------------


class LayeringCheckError(RuntimeError):
    """Internal error (exit 2)."""


def _run_git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise LayeringCheckError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _merge_base(base_ref: str) -> str:
    try:
        return _run_git(["merge-base", "HEAD", base_ref]).strip()
    except LayeringCheckError as exc:
        raise LayeringCheckError(
            f"could not resolve a merge base against {base_ref!r}: {exc}"
        ) from exc


def _layer_exemptions_at(sha: str) -> list[dict[str, Any]] | None:
    """The `layer_exemptions:` rows committed at `sha`, or None if there is
    nothing to ratchet against yet -- either the file didn't exist there
    (mirrors check_packaging_migration.py's own bootstrap case) OR the file
    existed but had no `layer_exemptions:` KEY at all (THIS tool's own
    bootstrap case: the PR that first introduces the key, same as this
    P5 commit). Once the key exists at a merge base -- even as an empty
    list -- an absent key becomes impossible and a present-but-empty list
    is returned as `[]`, which correctly ratchets going forward (any row
    added after all violations were fixed would rightly fail)."""
    rel = EXEMPTIONS_PATH.relative_to(REPO_ROOT).as_posix()
    proc = subprocess.run(
        ["git", "show", f"{sha}:{rel}"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    yaml = _load_yaml()
    doc = yaml.safe_load(proc.stdout) or {}
    if not isinstance(doc, dict) or "layer_exemptions" not in doc:
        return None
    rows = doc.get("layer_exemptions")
    return rows if isinstance(rows, list) else []


def check_ghost_rows(
    rows: list[dict[str, Any]], violations: list[Violation],
) -> list[str]:
    live_keys = {(v.from_path, v.to_path, v.edge_kind) for v in violations}
    problems = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _exemption_key(row)
        if key not in live_keys:
            problems.append(
                f"{row.get('from_path')} -> {row.get('to_path')} "
                f"({row.get('edge_kind')}): committed layer_exemptions row "
                "has no matching current violation -- a ghost/expired row. "
                "Delete it (the edge was removed or the layering fixed) or "
                "rerun --seed to reconcile."
            )
    return problems


def check_ratchet(base_sha: str) -> list[str]:
    """`--strict` only: layer_exemptions rows may only SHRINK once
    committed. A row present in the CANDIDATE file but absent from the
    file as committed at the merge base is a new addition -- legitimate
    only when the file didn't carry `layer_exemptions` at all there yet."""
    old_rows = _layer_exemptions_at(base_sha)
    if old_rows is None:
        return []
    old_keys = {_exemption_key(r) for r in old_rows if isinstance(r, dict)}
    try:
        new_rows = load_layer_exemptions()
    except (ValueError, ImportError):
        return []
    new_keys = {_exemption_key(r) for r in new_rows if isinstance(r, dict)}
    added = sorted(new_keys - old_keys, key=lambda k: (k[2] or "", k[0] or "", k[1] or ""))
    added = [
        (fp, tp, kind) for fp, tp, kind in added
        if not (kind == "l2_to_l2" and fp == _GENERATOR_FROM_PATH)
    ]
    if not added:
        return []
    return [
        f"{fp} -> {tp} ({kind}): NEW layer_exemptions row not present at "
        f"the merge base {base_sha} -- exemptions only shrink once "
        "committed; a genuinely new violation in new code must be fixed, "
        "never added here"
        for fp, tp, kind in added
    ]


def cmd_check(args: argparse.Namespace) -> int:
    modules = [Module(p) for p in find_runtime_scripts()]
    try:
        cap_paths = _load_capability_script_paths()
    except Exception as exc:
        print(f"error: could not load capabilities manifest: {exc}", file=sys.stderr)
        return 2
    tiers = classify_tiers(modules, cap_paths)
    edges = build_internal_graph(modules)
    violations = find_violations(edges, tiers)
    cycles = find_cycles(edges)

    try:
        rows = load_layer_exemptions()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    shape_problems = validate_layer_exemption_rows(rows)
    exempted = {_exemption_key(r) for r in rows if isinstance(r, dict)}
    unexempted = [
        v for v in violations
        if (v.from_path, v.to_path, v.edge_kind) not in exempted
    ]

    strict_problems: list[str] = []
    if args.strict:
        strict_problems.extend(check_ghost_rows(rows, violations))
        try:
            base_sha = _merge_base(args.base_ref)
        except LayeringCheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        strict_problems.extend(check_ratchet(base_sha))

    passed = not shape_problems and not unexempted and not strict_problems

    counts = {
        "modules": len(modules),
        "L0": sum(1 for t in tiers.values() if t == "L0"),
        "L1": sum(1 for t in tiers.values() if t == "L1"),
        "L2": sum(1 for t in tiers.values() if t == "L2"),
        "internal_edges": len(edges),
        "l0_outbound": sum(1 for v in violations if v.edge_kind == "l0_outbound"),
        "l1_to_l2": sum(1 for v in violations if v.edge_kind == "l1_to_l2"),
        "l2_to_l2": sum(1 for v in violations if v.edge_kind == "l2_to_l2"),
        "cycles": len(cycles),
    }

    if args.json:
        print(json.dumps({
            "passed": passed,
            "counts": counts,
            "unexempted": [
                {"from_path": v.from_path, "to_path": v.to_path, "edge_kind": v.edge_kind}
                for v in unexempted
            ],
            "exemption_shape_problems": shape_problems,
            "strict_problems": strict_problems,
            "cycles": cycles,
        }, indent=2))
        return 0 if passed else 1

    print(
        f"Scanned {counts['modules']} module(s): L0={counts['L0']} "
        f"L1={counts['L1']} L2={counts['L2']}; {counts['internal_edges']} "
        "internal edge(s)."
    )
    print(
        f"Violations found: l0_outbound={counts['l0_outbound']} "
        f"l1_to_l2={counts['l1_to_l2']} l2_to_l2={counts['l2_to_l2']}; "
        f"{len(rows)} layer_exemptions row(s) on file."
        + (" (--strict)" if args.strict else "")
    )
    print(f"Cycles (reported, not gated): {len(cycles)}")
    for c in cycles:
        print(f"  {' <-> '.join(sorted(c))}")

    if passed:
        print("Every violation is exempted; no unresolved layering break. ✔")
        return 0

    if unexempted:
        print(f"\n{len(unexempted)} unexempted violation(s):\n")
        for v in unexempted:
            print(f"  [{v.edge_kind}] {v.from_path} -> {v.to_path}")
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
            "Gate the L0/L1/L2 dependency layering ratchet against the "
            "committed layer_exemptions section of "
            "packaging_migration_exemptions.yaml."
        ),
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--seed", action="store_true",
        help="(Re)write the layer_exemptions section from the current tree.",
    )
    parser.add_argument(
        "--introduced-sha", default="unknown",
        help="Base SHA recorded on freshly-seeded rows (--seed only).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help=(
            "Also reject a ghost/expired layer_exemptions row and enforce "
            "the merge-base shrink-only ratchet."
        ),
    )
    parser.add_argument(
        "--base-ref", default="origin/main",
        help="Ref to compute the merge base against (--strict only).",
    )
    args = parser.parse_args(argv)

    if args.seed:
        return cmd_seed(args)
    return cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
