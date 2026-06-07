#!/usr/bin/env python3
"""assemble_changelog.py — cut a release section from changelog.d/ fragments.

Unreleased changes accumulate as one `<slug>.md` fragment per PR under
`changelog.d/` (instead of every PR editing a shared `## Unreleased` block in
`CHANGELOG.md`). At release, this tool groups the fragments by category and
prepends a `## [X.Y.Z] - DATE` section to `CHANGELOG.md`, then deletes the
consumed fragments.

This is the existing accumulate-then-cut release practice (see `## [1.111.0]`,
a "consolidated MINOR release cutting the accumulated Unreleased wave") with the
shared-file edit removed — so it is **idempotent** (no fragments ⇒ no-op) and
**merge-order-independent** (output depends only on the set of fragments, not the
order they merged).

Fragment format — one `### Added` / `### Changed` / `### Fixed` header on the
first non-blank line, then the body prose (Keep-a-Changelog categories):

    ### Added

    **`my_audit.py` — one-line summary (capability id `my_audit`).** Details…

Usage:

    python3 tools/assemble_changelog.py --version 1.112.0            # writes (date = today)
    python3 tools/assemble_changelog.py --version 1.112.0 --date 2026-06-08
    python3 tools/assemble_changelog.py --stdout                    # preview, no write/delete

Exit codes: 0 ok (incl. nothing-to-assemble) / 2 internal error (bad fragment).
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
FRAGMENT_DIR = REPO_ROOT / "changelog.d"

# Keep-a-Changelog category order. Fragments outside this set are an error.
CATEGORY_ORDER = ["Added", "Changed", "Fixed", "Deprecated", "Removed", "Security"]
_HEADER_RE = re.compile(r"^###\s+(\w+)\s*$", re.M)


class FragmentError(RuntimeError):
    pass


def discover_fragments(frag_dir: Path) -> list[tuple[str, str, str]]:
    """Return [(slug, category, body)] for every fragment, sorted by (category
    order, slug) so output is deterministic and merge-order-independent."""
    out: list[tuple[str, str, str]] = []
    for frag in sorted(frag_dir.glob("*.md")):
        if frag.name == "README.md":
            continue
        text = frag.read_text(encoding="utf-8")
        m = _HEADER_RE.search(text)
        if not m:
            raise FragmentError(
                f"{frag}: missing a `### Added/Changed/Fixed` category header"
            )
        # Enforce one-header-per-fragment, header-first — otherwise extra headers
        # or leading prose are silently swallowed/dropped (body is everything
        # after the first header), a quiet data-loss path.
        if len(_HEADER_RE.findall(text)) != 1:
            raise FragmentError(
                f"{frag}: exactly one category header per fragment "
                f"(found {len(_HEADER_RE.findall(text))}; split into separate files)"
            )
        if text[: m.start()].strip():
            raise FragmentError(
                f"{frag}: the category header must be the first non-blank line "
                f"(prose precedes it and would be dropped)"
            )
        category = m.group(1)
        if category not in CATEGORY_ORDER:
            raise FragmentError(
                f"{frag}: unknown category {category!r}; expected one of {CATEGORY_ORDER}"
            )
        body = text[m.end():].strip("\n")
        if not body.strip():
            raise FragmentError(f"{frag}: fragment has a header but no body")
        out.append((frag.stem, category, body))
    out.sort(key=lambda t: (CATEGORY_ORDER.index(t[1]), t[0]))
    return out


def render_section(version: str, date: str, fragments: list[tuple[str, str, str]]) -> str:
    """Render the `## [version] - date` section. Bodies are concatenated
    verbatim (no reflow) so every capability-id token survives into CHANGELOG.md.
    Sorts internally by (category order, slug) so the output is independent of the
    input list order — i.e. of merge order."""
    fragments = sorted(fragments, key=lambda t: (CATEGORY_ORDER.index(t[1]), t[0]))
    lines = [f"## [{version}] - {date}", ""]
    for category in CATEGORY_ORDER:
        bodies = [b for (_slug, c, b) in fragments if c == category]
        if not bodies:
            continue
        lines.append(f"### {category}")
        lines.append("")
        lines.append("\n\n".join(bodies))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def insert_section(changelog_text: str, section: str) -> str:
    """Insert the section immediately before the first existing `## [` version
    heading (structural anchor — independent of preamble / Unreleased wording)."""
    lines = changelog_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith("## ["):
            return "".join(lines[:i]) + section + "\n" + "".join(lines[i:])
    # No existing version section — append at end.
    return changelog_text.rstrip("\n") + "\n\n" + section


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="release version X.Y.Z (required unless --stdout-only)")
    ap.add_argument("--date", default=None, help="release date YYYY-MM-DD (default: today)")
    ap.add_argument("--stdout", action="store_true", help="print the section; don't write or delete")
    ap.add_argument("--changelog", type=Path, default=CHANGELOG)
    ap.add_argument("--fragment-dir", type=Path, default=FRAGMENT_DIR)
    args = ap.parse_args(argv)

    try:
        fragments = discover_fragments(args.fragment_dir)
    except FragmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not fragments:
        print("nothing to assemble: no fragments in changelog.d/")
        return 0

    version = args.version or "UNRELEASED"
    date = args.date or datetime.date.today().isoformat()
    section = render_section(version, date, fragments)

    if args.stdout:
        sys.stdout.write(section)
        return 0

    if not args.version:
        print("error: --version is required to write a release (use --stdout to preview)",
              file=sys.stderr)
        return 2

    updated = insert_section(args.changelog.read_text(encoding="utf-8"), section)
    args.changelog.write_text(updated, encoding="utf-8")
    consumed = []
    for slug, _c, _b in fragments:
        frag = args.fragment_dir / f"{slug}.md"
        frag.unlink()
        consumed.append(frag.name)
    print(f"assembled {len(consumed)} fragment(s) into {args.changelog.name} "
          f"[{version}] - {date}; removed: {', '.join(consumed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
