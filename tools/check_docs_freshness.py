#!/usr/bin/env python3
"""check_docs_freshness.py — CI gate against capability-doc staleness.

The companion to tools/check_capabilities_drift.py. Where the drift linter keeps
the capabilities manifest in sync with the *source*, this gate keeps the *docs* in
sync with the manifest, so a shipped capability can't quietly skip its paper trail.

Checks:

  1. **CHANGELOG coverage.** Every curated (status != todo) entry in
     each curated capability (capabilities.d/) must be referenced by `id` in CHANGELOG.md. Shipping or
     curating a capability without a changelog line is drift.

  2. **Generated-doc freshness.** If the calibration-readiness generator and its
     doc are present on this branch, run its `--check`. This sub-check is
     *tolerant*: it is skipped (not failed) when the generator or doc are absent,
     so the gate works on branches where that doc hasn't merged yet. Once present
     on `main`, the sub-check activates automatically.

Exit codes (mirrors check_capabilities_drift.py):

    0 — docs fresh
    1 — staleness detected
    2 — internal error

Usage:

    python3 tools/check_docs_freshness.py
    python3 tools/check_docs_freshness.py --manifest <path>
    python3 tools/check_docs_freshness.py --json

Run in CI alongside check_capabilities_drift.py and the pytest suite.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "plugins" / "setec-voiceprint" / "capabilities.d"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# Use the plugin's canonical manifest loader (dir-aware) rather than reading the
# path directly — repointing DEFAULT_MANIFEST at the capabilities.d/ directory
# would otherwise raise IsADirectoryError on a raw read.
_SCRIPTS_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))
from capabilities import load_manifest  # type: ignore  # noqa: E402


def changelog_coverage(manifest_path: Path, changelog_path: Path) -> list[str]:
    """Return curated capability ids missing from the changelog."""
    manifest = load_manifest(manifest_path)
    changelog = changelog_path.read_text(encoding="utf-8")
    missing = []
    for entry in manifest.get("entries", []):
        if entry.get("status") == "todo":
            continue
        eid = entry.get("id", "")
        if eid and eid not in changelog:
            missing.append(eid)
    return missing


def readiness_freshness() -> tuple[str, str]:
    """('ok'|'stale'|'skipped', detail). Tolerant of a missing generator/doc.

    Compares the generated region directly rather than calling
    ``gen_calibration_readiness.main(["--check"])`` — that helper writes a
    human status line to stdout, which would corrupt this gate's own
    ``--json`` output once the generator is present (P2, PR #145).
    """
    tools_dir = str(REPO_ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import gen_calibration_readiness as gcr  # type: ignore
    except ImportError:
        return ("skipped", "calibration-readiness generator not present on this branch")
    if not gcr.DEFAULT_DOC.exists():
        return ("skipped", "calibration-readiness.md not present on this branch")
    doc_text = gcr.DEFAULT_DOC.read_text(encoding="utf-8")
    rendered = gcr.replace_region(doc_text, gcr.render_block(gcr.load_manifest()))
    if rendered == doc_text:
        return ("ok", "calibration-readiness.md is up to date")
    return ("stale", "calibration-readiness.md is stale — run gen_calibration_readiness.py")


def run(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    missing = changelog_coverage(manifest_path, CHANGELOG)
    readiness_status, readiness_detail = readiness_freshness()
    ok = (not missing) and readiness_status != "stale"
    return {
        "ok": ok,
        "changelog_missing": missing,
        "readiness": {"status": readiness_status, "detail": readiness_detail},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        result = run(args.manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    missing = result["changelog_missing"]
    if missing:
        print("CHANGELOG coverage: STALE — these curated capabilities lack a changelog line:")
        for eid in missing:
            print(f"  - {eid}")
    else:
        print("CHANGELOG coverage: ok (every curated capability is logged).")

    rs = result["readiness"]
    print(f"Readiness matrix: {rs['status']} — {rs['detail']}")

    if result["ok"]:
        print("Docs are fresh. ✔")
        return 0
    print("Docs staleness detected.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
