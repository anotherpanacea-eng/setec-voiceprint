"""Historical fixture CLI remains usable in a detached, uninstalled plugin."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_fixture_cli(tmp_path_factory):
    outside = tmp_path_factory.mktemp("adversarial")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    source = outside / "invented.txt"
    source.write_bytes(b"The big color in 1999.\n\nAn old honor ends.\xff")
    return outside, plugin, source


def _run(bundle, *args):
    outside, *_ = bundle
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_alias_shared_cli_hooks_and_proxy(copied_fixture_cli, package_first):
    outside, plugin, source = copied_fixture_cli
    result = _run(copied_fixture_cli, "-c", """
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
names = ['adversarial_fixtures', 'setec.surfaces.adversarial_fixtures']
if sys.argv[2] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.TRANSFORMS is b.TRANSFORMS
assert a.RAID_ATTACK_CLASSES is b.RAID_ATTACK_CLASSES
a._ALL_CLI_TRANSFORMS['case_swap'] = lambda text, every=2: text + '|patched|'
assert b.main([sys.argv[3], sys.argv[4], '--transform', 'case_swap', '--every', '3']) == 0
assert Path(sys.argv[4]).read_text(encoding='utf-8').endswith('|patched|')
from setec.calibration import paraphrase_ladder as ladder
value = ladder.build_proxy_ladder('The big color in 1999. An old honor.', passes=2)
assert value['paraphraser'] == 'proxy_stdlib' and len(value['rungs']) == 3
assert value['rungs'][0] != value['rungs'][1]
assert not any(name in sys.modules for name in ('spacy','torch','transformers','sentence_transformers'))
""", plugin / "scripts", package_first, source, outside / "patched.txt")
    assert result.returncode == 0, result.stderr


def _invocation(script, runpy, *args):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,*sys.argv[2:]];runpy.run_path(p,run_name='__main__')", script, *args]
    return [script, *args]


@pytest.mark.parametrize("runpy", [False, True])
def test_actual_transforms_and_raid_manifest(copied_fixture_cli, runpy):
    outside, plugin, source = copied_fixture_cli
    script = plugin / "scripts/adversarial_fixtures.py"
    single = outside / "single.txt"
    result = _run(copied_fixture_cli, *_invocation(script, runpy, source, single,
                  "--transform", "alternative_spelling"))
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert single.read_bytes() == "The big colour in 1999.\n\nAn old honour ends.".replace("\n", os.linesep).encode()
    manifest = outside / "pairs.jsonl"
    result = _run(copied_fixture_cli, *_invocation(script, runpy, source, manifest, "--raid-suite"))
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    assert all(row['clean'] == "The big color in 1999.\n\nAn old honor ends." for row in rows)
    by_class = {row['obfuscation_class']: row for row in rows}
    assert by_class['number_swap']['obfuscated'] == "The big color in 2000.\n\nAn old honor ends."
    assert by_class['paragraph_shuffle']['obfuscated'] == "An old honor ends.\n\nThe big color in 1999."
    assert all(set(row) == {'id','obfuscation_class','clean','obfuscated'} for row in rows)


@pytest.mark.parametrize("runpy", [False, True])
def test_detached_help_and_invalid_transform(copied_fixture_cli, runpy):
    outside, plugin, source = copied_fixture_cli
    script = plugin / "scripts/adversarial_fixtures.py"
    result = _run(copied_fixture_cli, *_invocation(script, runpy, "--help"))
    assert result.returncode == 0 and result.stderr == b""
    assert b"--raid-suite" in result.stdout and b"--every" in result.stdout
    output = outside / "must-not-write.txt"
    result = _run(copied_fixture_cli, *_invocation(script, runpy, source, output,
                  "--transform", "unknown_transform"))
    assert result.returncode == 2 and result.stdout == b""
    assert b"invalid choice" in result.stderr and not output.exists()
