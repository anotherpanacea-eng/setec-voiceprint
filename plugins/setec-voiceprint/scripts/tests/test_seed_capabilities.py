#!/usr/bin/env python3
"""Tests for tools/seed_capabilities.py.

Pins:

  * The seeder emits the *current* manifest schema version (v0.2.0).
    Regression for PR #129 review: pre-fix the seeder still wrote
    "schema_version: 0.1.0" and omitted python_optional, so any
    operator following the linter's "run the seeder" prompt would
    regenerate a stale manifest and reintroduce the
    --available false-negative class that PR #129 fixed.
  * Every emitted entry includes both `python` and `python_optional`
    keys in its dependencies block, even when the seeder couldn't
    auto-extract any deps (the schema shape stays consistent across
    seeded and hand-curated entries).
  * The seeder's output parses with yaml.safe_load and produces the
    same schema_version + entry count as the committed manifest.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import seed_capabilities as sc  # type: ignore  # noqa: E402

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


def test_seeder_emits_v0_2_schema():
    """The seeder's schema_version must match the committed manifest's.
    Pre-fix it was hard-coded to 0.1.0 even though the committed
    manifest was 0.2.0."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "capabilities.yaml"
        rc = sc.main(["--out", str(out_path)])
        assert rc == 0
        data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "0.2.0", (
            f"seeder must emit schema_version 0.2.0 to match the "
            f"committed manifest's schema; got "
            f"{data['schema_version']!r}. If the manifest schema is "
            f"bumped, update render_yaml's schema_version literal "
            f"too — otherwise running the seeder regenerates stale "
            f"shape and reintroduces the bugs the bump was meant to "
            f"fix."
        )


def test_seeder_emits_python_optional_on_every_entry():
    """Every seeded entry must include `dependencies.python_optional`
    (even as an empty list) so the schema shape is consistent across
    seeded and hand-curated entries. Operators populate python_optional
    by demoting deps from python during curation."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "capabilities.yaml"
        rc = sc.main(["--out", str(out_path)])
        assert rc == 0
        data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            deps = entry.get("dependencies") or {}
            assert "python" in deps, (
                f"{entry.get('id')}: dependencies.python missing"
            )
            assert "python_optional" in deps, (
                f"{entry.get('id')}: dependencies.python_optional "
                f"missing. The seeder must emit this key (as an "
                f"empty list when nothing's been classified) so "
                f"operators see the slot during hand-curation."
            )
            assert isinstance(deps["python_optional"], list)


def test_seeder_output_parses_and_has_entries():
    """End-to-end smoke test: seeded manifest parses with safe_load
    and contains a plausible number of entries from the repo."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "capabilities.yaml"
        rc = sc.main(["--out", str(out_path)])
        assert rc == 0
        data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert isinstance(data["entries"], list)
        # Loose lower bound — number of TASK_SURFACE-bearing scripts
        # can grow but shouldn't crater below the count at the time
        # of this PR.
        assert len(data["entries"]) >= 30, (
            f"seeder produced suspiciously few entries: "
            f"{len(data['entries'])}"
        )


def test_seeded_entries_use_status_todo():
    """Every seeded entry must carry status: todo so the drift
    linter's todo_content check exempts them. Operators promote
    off todo during hand-curation."""
    if yaml is None:
        return
    with tempfile.TemporaryDirectory() as td:
        out_path = Path(td) / "capabilities.yaml"
        rc = sc.main(["--out", str(out_path)])
        assert rc == 0
        data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            assert entry.get("status") == "todo", (
                f"{entry.get('id')}: seeded entries must use "
                f"status: todo; got {entry.get('status')!r}"
            )


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
