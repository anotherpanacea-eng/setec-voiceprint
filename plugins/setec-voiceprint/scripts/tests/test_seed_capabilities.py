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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import seed_capabilities as sc  # type: ignore  # noqa: E402
import pytest

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None


def test_package_discovery_preserves_launcher_metadata_and_stay_put_surface(tmp_path):
    scripts = tmp_path / "scripts"
    package = scripts / "setec" / "surfaces"
    package.mkdir(parents=True)
    implementation = package / "moved.py"
    implementation.write_text(
        '"""Implementation purpose."""\nimport transformers\n'
        'TASK_SURFACE = "voice_coherence"\nTOOL_NAME = "moved_tool"\n'
        'def main(): pass\nif __name__ == "__main__": main()\n', encoding="utf-8",
    )
    launcher = scripts / "old.py"
    launcher.write_text('from setec.surfaces.moved import TASK_SURFACE\n', encoding="utf-8")
    stay_put = package / "stay_put.py"
    stay_put.write_text('TASK_SURFACE = "setup"\n', encoding="utf-8")
    # Directory-based exclusions would hide this unregistered package surface.
    assert sc.find_scripts(scripts) == sorted([launcher, stay_put])
    seed = sc.parse_module(launcher, scripts, tmp_path)
    assert (seed.id, seed.script_path, seed.surface) == ("moved_tool", "scripts/old.py", "voice_coherence")
    assert seed.purpose == "Implementation purpose."
    assert seed.tier == "surprisal" and "transformers" in seed.deps and seed.has_main


@pytest.mark.parametrize("failure", ["missing", "cyclic", "escape", "ambiguous"])
def test_alias_failures_are_rejected(tmp_path, failure):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    launcher = scripts / "old.py"
    source = {
        "missing": "from absent import TASK_SURFACE\n",
        "cyclic": "from old import TASK_SURFACE\n",
        "escape": "from ..outside import TASK_SURFACE\n",
        "ambiguous": "from a import TASK_SURFACE\nfrom b import TASK_SURFACE\n",
    }[failure]
    launcher.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError):
        sc.find_scripts(scripts)
    with pytest.raises(ValueError):
        sc.parse_module(launcher, scripts, tmp_path)


def test_ordinary_surface_with_static_import_keeps_own_metadata(tmp_path):
    ordinary = tmp_path / "ordinary.py"
    ordinary.write_text('from absent import TASK_SURFACE\nTASK_SURFACE = "setup"\n', encoding="utf-8")
    assert sc.parse_module(ordinary, tmp_path, tmp_path).surface == "setup"


@pytest.mark.parametrize("case,source", [
    ("renamed", "from impl import TASK_SURFACE as OTHER\n"),
    ("deleted", "from impl import TASK_SURFACE\ndel TASK_SURFACE\n"),
    ("rebound", "from impl import TASK_SURFACE\nTASK_SURFACE = None\n"),
    ("annotated", "from impl import TASK_SURFACE\nTASK_SURFACE: str | None = None\n"),
    ("unpacked", "from impl import TASK_SURFACE\nTASK_SURFACE, other = None, 1\n"),
    ("augmented", "from impl import TASK_SURFACE\nTASK_SURFACE += '_wrong'\n"),
    ("conditional", "from impl import TASK_SURFACE\nif True:\n    del TASK_SURFACE\n"),
    ("type_only", "from impl import TASK_SURFACE\nTASK_SURFACE: str | None\n"),
    ("function_local", "from impl import TASK_SURFACE\ndef helper():\n    TASK_SURFACE = None\n    del TASK_SURFACE\n"),
    ("class_local", "from impl import TASK_SURFACE\nclass Helper:\n    TASK_SURFACE = None\n    del TASK_SURFACE\n"),
    ("valid", "from impl import TASK_SURFACE\n"),
])
def test_alias_must_export_surface_for_resolution_and_discovery(tmp_path, case, source):
    implementation = tmp_path / "impl.py"
    implementation.write_text('TASK_SURFACE = "setup"\n', encoding="utf-8")
    launcher = tmp_path / "old.py"
    launcher.write_text(source, encoding="utf-8")
    if case in {"deleted", "rebound", "annotated", "unpacked", "augmented", "conditional"}:
        with pytest.raises(ValueError, match="invalidated TASK_SURFACE alias"):
            sc.resolve_implementation(launcher, tmp_path)
        with pytest.raises(ValueError, match="invalidated TASK_SURFACE alias"):
            sc.find_scripts(tmp_path)
        with pytest.raises(ValueError, match="invalidated TASK_SURFACE alias"):
            sc.parse_module(launcher, tmp_path, tmp_path)
    elif case == "renamed":
        assert sc.resolve_implementation(launcher, tmp_path) == launcher
        assert sc.find_scripts(tmp_path) == sorted([launcher, implementation])
        assert sc.parse_module(launcher, tmp_path, tmp_path) is None
        assert sc.parse_module(implementation, tmp_path, tmp_path).surface == "setup"
    else:
        assert sc.resolve_implementation(launcher, tmp_path) == implementation
        assert sc.find_scripts(tmp_path) == [launcher]
        seed = sc.parse_module(launcher, tmp_path, tmp_path)
        assert seed.surface == "setup" and seed.script_path == "old.py"


@pytest.mark.parametrize("content", [b"def broken(:\n", b"\xff\xfe\n"])
def test_ordinary_bad_source_retains_none_but_followed_alias_refuses(tmp_path, content):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    ordinary = scripts / "ordinary.py"
    ordinary.write_bytes(content)
    assert sc.find_scripts(scripts) == [ordinary]
    assert sc.parse_module(ordinary, scripts, tmp_path) is None
    launcher = scripts / "old.py"
    launcher.write_text("from ordinary import TASK_SURFACE\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read launcher implementation"):
        sc.parse_module(launcher, scripts, tmp_path)
    with pytest.raises(ValueError, match="cannot read launcher implementation"):
        sc.find_scripts(scripts)


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
