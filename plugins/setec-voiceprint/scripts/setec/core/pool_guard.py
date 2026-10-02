#!/usr/bin/env python3
"""pool_guard.py — refuse a passage-deduped manifest at duplicate-dependent pools.

`near_dup_dedup.py --passages --out ...` can emit a *passage-unit* corpus whose
Stage-A near-duplicate clusters have already been collapsed to one representative
each. That artifact is fine for a training pipeline and wrong for the
**set-level-diversity** surfaces: collapse / homogeneity / template-reuse /
leave-one-out novelty are signals that live *in the retained duplicates*, so
deduping their pool destroys the very object being measured. This repo has hit
that class before (the 2026-06/07 self-exclusion sweeps, #306/#307 — dedup
applied to pools whose purpose is diversity measurement).

The mechanism is deliberately a **file-level scan called per surface**, not a
kwarg on a shared loader:

  * The pool-loader class contains clean-room copies (`homogeneity_audit`,
    `distinct_diversity_audit` and `cross_doc_novelty_profile` each parse rows
    themselves), so a kwarg on `originality_audit._load_reference_manifest`
    structurally cannot reach them.
  * Every loader in that class returns `(id, text[, path])` tuples and discards
    the row dict where the marker lives, so no post-load check can see the
    marker without changing every loader's return shape.

Which surfaces call it is *not* decided by task-surface tag but by
**duplicate-dependence of the measurement** (the #306/#307 comparison-vs-diversity
purpose rule); the complete classification map, with a rationale on every entry
(firing and exempt alike), is pinned by `tests/test_pool_guard_coverage.py`.

Named limit (also carried in every refusal message): this is a **manifest-path**
check. Directory inputs (`--dir` / `--corpus-dir` / `--reference-dir`) load bare
text files with no row metadata and therefore cannot carry the marker, and an
operator who hand-strips the key has asserted responsibility as with any manifest
edit. It is a default-path guard against the recurring accident, not an
adversarial-proof seal.

Stdlib only, one pass, no dependency on the manifest schema beyond the marker key.
"""

from __future__ import annotations

import json
from pathlib import Path

# The provenance object `near_dup_dedup --passages --out` stamps on every row it
# writes. Not optional and not strippable by flag — it is the artifact's identity.
PASSAGE_DEDUP_MARKER = "passage_dedup"

# The invariant every refusal names, so the operator learns the rule and not just
# that something was rejected.
PASSAGE_DEDUP_INVARIANT = (
    "set-level-diversity pools depend on retained duplicates; this manifest is "
    "passage-deduped (rows carry a `passage_dedup` marker) — feed the pre-dedup "
    "source manifest instead"
)

# How many marked rows a refusal message names before it truncates.
_MAX_NAMED_ROWS = 5


def scan_manifest_for_passage_dedup(path: Path | str) -> list[str]:
    """Return one ``"<id> (line N)"`` label per row carrying the marker.

    Raw one-pass JSONL scan: malformed lines and non-object rows are skipped
    silently (the callers' own loaders already warn about them, and a scanner
    that crashed on a bad line would turn a warning into a hard failure). An
    unreadable path returns ``[]`` — the caller's loader reports the read error
    with its own `bad_input` envelope, and the guard must not pre-empt it.
    """
    p = Path(path)
    try:
        raw_text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    marked: list[str] = []
    for line_no, raw in enumerate(raw_text.splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or PASSAGE_DEDUP_MARKER not in row:
            continue
        # Mirror the pool loaders' id fallback so the label an operator reads
        # here matches the id they'd see in the audit's own output.
        rid = str(
            row.get("id") or row.get("path") or row.get("text_path") or f"line{line_no}"
        )
        marked.append(f"{rid} (line {line_no})")
    return marked


def refusal_reason(path: Path | str, marked: list[str], *, flag: str) -> str:
    """Build the `bad_input` reason string for a marked manifest.

    ``flag`` is the surface's own CLI flag for the manifest (``--manifest`` /
    ``--reference-manifest``) so the message points at the argument the operator
    actually passed.
    """
    shown = ", ".join(marked[:_MAX_NAMED_ROWS])
    if len(marked) > _MAX_NAMED_ROWS:
        shown += f", … (+{len(marked) - _MAX_NAMED_ROWS} more)"
    return (
        f"{flag} {path}: {PASSAGE_DEDUP_INVARIANT}. "
        f"{len(marked)} marked row(s): {shown}. "
        "Note the guard's limit: it is a manifest-path check — a directory input "
        "carries no row metadata and cannot be checked this way."
    )
