#!/usr/bin/env python3
"""spec_anchor_lint.py — catch phantom code-references in a spec or diff before review.

The build retrospective (350 review findings) found **API-anchor drift is ~40% of all
P1s**: a spec/build *asserts* a symbol / ``file:line`` / env-var / sibling-spec / CLI-flag
that does not exist in the real tree. That subclass is mechanically checkable — extract
every code-shaped reference a spec claims and resolve it against the repo. This tool is the
deterministic enforcer of the AGENTS.md "Build pre-flight" mode 1: run it on a spec *before*
building, and on a diff *before* review, so phantom anchors fail fast instead of burning a
Codex window.

SCOPE (honest): it catches **absent** references, NOT *exists-but-mischaracterized* semantic
claims (e.g. "narrative.py is voicewright's own seam" — narrative.py exists, so this won't
flag it; that stays a human/panel check). It catches the dominant "doesn't-exist" subclass,
conservatively, so the gate's false-positive rate is ~0.

Reference types and confidence:

  HIGH (gate — non-zero exit if absent):
    1. file:line        — ``path.py:123`` → file exists under --repo AND has >= 123 lines.
    2. file path        — `` `a/b/c.py` `` (.py/.ya?ml/.json/.txt/.md) → path exists.
    3. sibling-spec     — ``specs/NN-slug.md`` or ``spec NN`` → a specs/NN-*.md exists.
    4. prefixed env-var — VOICEPRINT_* / VOICEWRIGHT_* / SETEC_* → literal present in source.

  MEDIUM (warn — reported, never gates unless --strict):
    5. dotted symbol    — `` `module.func` `` / `` `results.field` `` → last component in source.
    6. call/def symbol  — `` `snake_case` `` (code-shaped) → token present in source.
    7. CLI flag         — ``--flag-name`` → literal present in source.

Pure stdlib (re + pathlib + difflib + argparse + json). Repo-agnostic via --repo; usable
against setec-voicewright too. One tree walk; CI-runnable.

  python3 tools/spec_anchor_lint.py --spec specs/29-foo.md --repo .
  git diff origin/main...HEAD | python3 tools/spec_anchor_lint.py --diff - --repo .
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    from _console import enable_utf8_stdio
except ImportError:  # when imported as tools.spec_anchor_lint
    from tools._console import enable_utf8_stdio  # type: ignore

# Directories never worth indexing.
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache",
              ".venv", "venv", ".tox", "dist", "build", ".eggs"}
# Source we tokenize for symbol / env-var / flag presence. Deliberately EXCLUDES
# .md/.txt: a symbol exists if it's in real source, not if a doc mentions it — and
# this stops the very spec being linted (a .md inside the repo) from "finding" its
# own invented anchors in its own prose.
_SOURCE_EXT = {".py", ".yaml", ".yml", ".cfg", ".toml"}
# File-reference extensions we treat as path claims.
_PATH_EXT = {".py", ".yaml", ".yml", ".json", ".txt", ".md"}
_ENV_PREFIXES = ("VOICEPRINT_", "VOICEWRIGHT_", "SETEC_")

HIGH = "high"
MEDIUM = "medium"


@dataclass
class Reference:
    ref: str
    kind: str           # file_line | file_path | sibling_spec | env_var | dotted_symbol | symbol | cli_flag
    confidence: str     # HIGH | MEDIUM
    line: int | None = None       # for file_line, the cited line number
    status: str = "skipped"       # found | absent | skipped
    suggestion: str | None = None


@dataclass
class RepoIndex:
    root: Path
    rel_paths: set[str] = field(default_factory=set)
    basenames: dict[str, list[str]] = field(default_factory=dict)
    tokens: set[str] = field(default_factory=set)
    spec_numbers: set[int] = field(default_factory=set)
    _blob: str = ""

    def has_token(self, name: str) -> bool:
        return name in self.tokens

    def has_literal(self, literal: str) -> bool:
        return literal in self._blob


def build_repo_index(repo: Path) -> RepoIndex:
    """One walk: collect rel-paths, basenames, a source token set + blob, and spec numbers."""
    idx = RepoIndex(root=repo)
    blob_parts: list[str] = []
    spec_re = re.compile(r"^(\d+)[-_]")
    for path in repo.rglob("*"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        idx.rel_paths.add(rel)
        idx.basenames.setdefault(path.name, []).append(rel)
        if path.suffix in _SOURCE_EXT:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            blob_parts.append(text)
            idx.tokens.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text))
        # specs/NN-slug.md anywhere under the tree
        if path.suffix == ".md" and path.parent.name == "specs":
            m = spec_re.match(path.name)
            if m:
                idx.spec_numbers.add(int(m.group(1)))
    idx._blob = "\n".join(blob_parts)
    return idx


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
_RE_FILE_LINE = re.compile(r"\b([A-Za-z0-9_./-]+\.py):(\d+)\b")
_RE_BACKTICK = re.compile(r"`([^`]+)`")
_RE_SPEC_PATH = re.compile(r"\bspecs/(\d+)[-_][A-Za-z0-9_-]+\.md\b")
_RE_SPEC_WORD = re.compile(r"\bspec\s+(\d+)\b", re.IGNORECASE)
_RE_ENV = re.compile(r"\b((?:VOICEPRINT|VOICEWRIGHT|SETEC)_[A-Z0-9_]+)\b")
_RE_FLAG = re.compile(r"(?<![A-Za-z0-9])(--[a-z][a-z0-9-]+)\b")
_RE_PATHISH = re.compile(r"^[A-Za-z0-9_./-]+\.(py|ya?ml|json|txt|md)$")
_RE_DOTTED = re.compile(r"^[a-z_][A-Za-z0-9_]*(?:\.[a-z_][A-Za-z0-9_]*)+$")
_RE_CODE_SYMBOL = re.compile(r"^[a-z_][a-z0-9_]{2,}$")
# English words that often appear in backticks but are not symbols — never code-flag these.
_PROSE = {"target", "verdict", "results", "true", "false", "none", "null", "spec", "specs",
          "json", "results", "status", "note", "todo", "high", "low", "moderate", "band"}


def extract_references(text: str) -> list[Reference]:
    seen: set[tuple[str, str]] = set()
    refs: list[Reference] = []

    def add(ref: str, kind: str, conf: str, line: int | None = None) -> None:
        key = (kind, ref)
        if key in seen:
            return
        seen.add(key)
        refs.append(Reference(ref=ref, kind=kind, confidence=conf, line=line))

    for m in _RE_FILE_LINE.finditer(text):
        add(f"{m.group(1)}:{m.group(2)}", "file_line", HIGH, line=int(m.group(2)))
    for m in _RE_SPEC_PATH.finditer(text):
        add(m.group(0), "sibling_spec", HIGH, line=int(m.group(1)))
    for m in _RE_SPEC_WORD.finditer(text):
        add(f"spec {m.group(1)}", "sibling_spec", HIGH, line=int(m.group(1)))
    for m in _RE_ENV.finditer(text):
        add(m.group(1), "env_var", HIGH)
    # bare CLI flags in prose (also picked up inside backticks below)
    for m in _RE_FLAG.finditer(text):
        add(m.group(1), "cli_flag", MEDIUM)
    # backticked tokens: classify by shape
    for m in _RE_BACKTICK.finditer(text):
        tok = m.group(1).strip()
        if _RE_PATHISH.match(tok):
            # Only .py paths gate: a phantom .py is almost always a real build error.
            # .md/.json/.yaml/.txt paths are routinely cross-tree (scratch / fleet hub)
            # or historical (retired files a spec mentions in passing), so they advise.
            add(tok, "file_path", HIGH if tok.endswith(".py") else MEDIUM)
        elif tok.startswith("--") and _RE_FLAG.match(tok):
            add(tok, "cli_flag", MEDIUM)
        elif _RE_DOTTED.match(tok):
            add(tok, "dotted_symbol", MEDIUM)
        elif _RE_CODE_SYMBOL.match(tok) and "_" in tok and tok.lower() not in _PROSE:
            # require an underscore so single english words in backticks are skipped
            add(tok, "symbol", MEDIUM)
    return refs


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def _resolve_path(rel: str, idx: RepoIndex) -> str | None:
    if rel in idx.rel_paths:
        return rel
    base = rel.rsplit("/", 1)[-1]
    hits = idx.basenames.get(base, [])
    return hits[0] if len(hits) == 1 else None


def verify(ref: Reference, idx: RepoIndex) -> None:
    if ref.kind == "file_line":
        path = ref.ref.split(":", 1)[0]
        resolved = _resolve_path(path, idx)
        if resolved is None:
            ref.status = "absent"
            ref.suggestion = _suggest(path.rsplit("/", 1)[-1], idx.basenames.keys())
            return
        try:
            n = sum(1 for _ in (idx.root / resolved).open(encoding="utf-8", errors="ignore"))
        except OSError:
            ref.status = "absent"
            return
        ref.status = "found" if (ref.line or 0) <= n else "absent"
        if ref.status == "absent":
            ref.suggestion = f"{resolved} has {n} lines (cited :{ref.line})"
    elif ref.kind == "file_path":
        resolved = _resolve_path(ref.ref, idx)
        ref.status = "found" if resolved else "absent"
        if not resolved:
            ref.suggestion = _suggest(ref.ref.rsplit("/", 1)[-1], idx.basenames.keys())
    elif ref.kind == "sibling_spec":
        ref.status = "found" if ref.line in idx.spec_numbers else "absent"
        if ref.status == "absent":
            ref.suggestion = f"no specs/{ref.line}-*.md (have {sorted(idx.spec_numbers)[-6:]})"
    elif ref.kind == "env_var":
        ref.status = "found" if idx.has_literal(ref.ref) else "absent"
        if ref.status == "absent":
            ref.suggestion = _suggest(ref.ref, [t for t in idx.tokens if t.isupper() and "_" in t])
    elif ref.kind in ("dotted_symbol", "symbol"):
        name = ref.ref.split(".")[-1] if ref.kind == "dotted_symbol" else ref.ref
        ref.status = "found" if idx.has_token(name) else "absent"
        if ref.status == "absent":
            ref.suggestion = _suggest(name, idx.tokens)
    elif ref.kind == "cli_flag":
        ref.status = "found" if idx.has_literal(ref.ref) else "absent"
        if ref.status == "absent":
            flags = set(re.findall(r"--[a-z][a-z0-9-]+", idx._blob))
            ref.suggestion = _suggest(ref.ref, flags)
    else:
        ref.status = "skipped"


def _suggest(name: str, pool) -> str | None:
    m = difflib.get_close_matches(name, list(pool), n=1, cutoff=0.7)
    return f"did you mean {m[0]!r}?" if m else None


# --------------------------------------------------------------------------- #
# lint + report
# --------------------------------------------------------------------------- #
def lint(text: str, idx: RepoIndex, *, strict: bool = False) -> dict:
    refs = extract_references(text)
    for ref in refs:
        verify(ref, idx)
    absent = [r for r in refs if r.status == "absent"]
    high_absent = [r for r in absent if r.confidence == HIGH]
    med_absent = [r for r in absent if r.confidence == MEDIUM]
    gated = bool(high_absent) or (strict and bool(med_absent))
    return {
        "checked": len(refs),
        "found": sum(1 for r in refs if r.status == "found"),
        "absent": len(absent),
        "high_absent": high_absent,
        "medium_absent": med_absent,
        "gated": gated,
        "references": refs,
    }


def _render(report: dict) -> None:
    high, med = report["high_absent"], report["medium_absent"]
    if not high and not med:
        print(f"✔ spec-anchor-lint: {report['found']}/{report['checked']} references resolve; "
              "no phantom anchors.")
        return
    if high:
        print(f"✗ {len(high)} PHANTOM reference(s) — these do not exist in the repo (gating):")
        for r in high:
            tip = f"  ({r.suggestion})" if r.suggestion else ""
            print(f"    [{r.kind}] {r.ref}{tip}")
    if med:
        print(f"⚠ {len(med)} unresolved reference(s) — advisory, verify by hand:")
        for r in med:
            tip = f"  ({r.suggestion})" if r.suggestion else ""
            print(f"    [{r.kind}] {r.ref}{tip}")
    print(f"  ({report['found']}/{report['checked']} resolved)")


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    ap = argparse.ArgumentParser(description="Catch phantom code-references in a spec/diff.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--spec", type=Path, help="spec markdown to lint")
    src.add_argument("--diff", help="unified diff file, or - for stdin (lints added '+' lines)")
    ap.add_argument("--repo", type=Path, default=Path("."), help="repo root to resolve against")
    ap.add_argument("--strict", action="store_true", help="gate on medium-confidence absences too")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    if args.spec:
        text = args.spec.read_text(encoding="utf-8", errors="ignore")
    elif args.diff == "-":
        text = "".join(l[1:] for l in sys.stdin if l.startswith("+") and not l.startswith("+++"))
    else:
        raw = Path(args.diff).read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
        text = "".join(l[1:] for l in raw if l.startswith("+") and not l.startswith("+++"))

    idx = build_repo_index(args.repo.resolve())
    report = lint(text, idx, strict=args.strict)

    if args.json:
        print(json.dumps({
            "checked": report["checked"], "found": report["found"], "absent": report["absent"],
            "gated": report["gated"],
            "references": [{"ref": r.ref, "kind": r.kind, "confidence": r.confidence,
                            "status": r.status, "suggestion": r.suggestion} for r in report["references"]],
        }, indent=2))
    else:
        _render(report)
    return 1 if report["gated"] else 0


if __name__ == "__main__":
    sys.exit(main())
