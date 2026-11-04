"""Synthetic sent-message builders retain namespace, CLI and output paths."""

import mailbox
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


FAMILIES = [
    ("acquisition_gmail_fixture", "sent_fixture.mbox"),
    ("acquisition_imessage_fixture", "chat_fixture.db"),
]


@pytest.fixture
def copied_builders(tmp_path):
    plugin = tmp_path / "plugin"
    original = Path(__file__).resolve().parents[2]
    paths = [
        ".claude-plugin/plugin.json", "scripts/setec/__init__.py",
        "scripts/setec/paths.py", "scripts/setec/test_data/__init__.py",
    ]
    for family, _ in FAMILIES:
        paths.extend([
            f"scripts/test_data/{family}/build_fixture.py",
            f"scripts/setec/test_data/{family}/__init__.py",
            f"scripts/setec/test_data/{family}/build_fixture.py",
        ])
    for relative in paths:
        target = plugin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / relative, target)
    return tmp_path, plugin


def _run(copied_builders, *args):
    outside, _ = copied_builders
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside,
        env=env, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("family,artifact", FAMILIES)
@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_entry_and_builder_hooks(copied_builders, family, artifact, qualified, package_first):
    _, plugin = copied_builders
    result = _run(copied_builders, "-c", """
import importlib, sys
from pathlib import Path
scripts, family, artifact = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path[:0] = [str(scripts), str(scripts / 'test_data' / family)]
legacy = 'test_data.' + family + '.build_fixture' if sys.argv[4] == 'True' else 'build_fixture'
names = [legacy, 'setec.test_data.' + family + '.build_fixture']
if sys.argv[5] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.HERE == scripts / 'test_data' / family
assert b.build_fixture.__defaults__[0] == a.HERE / artifact
a.build_fixture = lambda: Path('shared-hook')
assert b.main() is None
assert not any(name in sys.modules for name in ('spacy', 'torch', 'requests'))
""", plugin / "scripts", family, artifact, qualified, package_first)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == "wrote shared-hook"


def test_qualified_same_basename_builders_coexist(copied_builders):
    _, plugin = copied_builders
    result = _run(copied_builders, "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from test_data.acquisition_gmail_fixture import build_fixture as g
from test_data.acquisition_imessage_fixture import build_fixture as i
from setec.test_data.acquisition_gmail_fixture import build_fixture as cg
from setec.test_data.acquisition_imessage_fixture import build_fixture as ci
assert g is cg and i is ci and g is not i
assert g.MBOX_PATH.name == 'sent_fixture.mbox'
assert i.DB_PATH.name == 'chat_fixture.db'
g.main = lambda: 19
assert cg.main() == 19 and ci.main is not cg.main
""", plugin / "scripts")
    assert result.returncode == 0, result.stderr


def _assert_artifact(path, family):
    if family == "acquisition_gmail_fixture":
        box = mailbox.mbox(path, create=False)
        try:
            assert any(message["Subject"] == "Lunch plans, Alice?" for message in box)
        finally:
            box.close()
    else:
        connection = sqlite3.connect(path)
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute(
                "SELECT text FROM message WHERE text IS NOT NULL AND text != '' LIMIT 1"
            ).fetchone()[0]
            assert connection.execute("SELECT chat_identifier FROM chat").fetchall()
        finally:
            connection.close()


@pytest.mark.parametrize("family,artifact", FAMILIES)
@pytest.mark.parametrize("runpy", [False, True])
def test_real_historical_cli_generates_adjacent_fixture(copied_builders, family, artifact, runpy):
    _, plugin = copied_builders
    historical = plugin / "scripts/test_data" / family
    script = historical / "build_fixture.py"
    args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p];runpy.run_path(p,run_name='__main__')", script] if runpy else [script]
    result = _run(copied_builders, *args)
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.decode().strip() == f"wrote {historical / artifact}"
    _assert_artifact(historical / artifact, family)
    assert not (plugin / "scripts/setec/test_data" / family / artifact).exists()


@pytest.mark.parametrize("family,artifact", FAMILIES)
def test_explicit_destination_still_overrides_captured_default(copied_builders, family, artifact):
    outside, plugin = copied_builders
    target = outside / "explicit" / artifact
    result = _run(copied_builders, "-c", """
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
builder = importlib.import_module('test_data.' + sys.argv[2] + '.build_fixture')
target = Path(sys.argv[3])
assert builder.build_fixture(target) == target
assert not builder.build_fixture.__defaults__[0].exists()
""", plugin / "scripts", family, target)
    assert result.returncode == 0, result.stderr
    _assert_artifact(target, family)
