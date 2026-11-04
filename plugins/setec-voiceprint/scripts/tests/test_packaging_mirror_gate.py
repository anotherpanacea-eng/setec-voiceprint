"""Copied internal mirror guard keeps aggregate output and its closed CLI."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_gate(tmp_path_factory):
    outside = tmp_path_factory.mktemp("mirror_gate")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    source, mirror = outside / "source.txt", outside / "mirror.txt"
    source.write_bytes(b"alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon")
    mirror.write_bytes(b"red blue green yellow orange purple black white gray cyan magenta gold silver bronze ivory teal amber coral navy violet")
    return outside, plugin, source, mirror


def _run(copied_gate, *args):
    outside, *_ = copied_gate
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_types_and_patch_reach_canonical_evaluation(copied_gate, package_first):
    _, plugin, *_ = copied_gate
    result = _run(copied_gate, "-c", """
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
names = ['_mirror_gate', 'setec.surfaces._mirror_gate']
if sys.argv[2] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
for name in ('Span', 'Region', 'MirrorGateError'):
    assert pickle.loads(('c_mirror_gate\\n' + name + '\\n.').encode()) is getattr(a, name)
a._similarity = lambda source, mirror: 0.1234
assert b.evaluate(b'invented source', b'other invented words')['similarity'] == 0.1234
assert not any(name in sys.modules for name in ('spacy','torch','transformers','requests'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


def _invocation(script, runpy, *args):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,*sys.argv[2:]];runpy.run_path(p,run_name='__main__')", script, *args]
    return [script, *args]


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_evaluation_is_aggregate_only_binary_lf(copied_gate, runpy):
    outside, plugin, source, mirror = copied_gate
    script = plugin / "scripts/_mirror_gate.py"
    result = _run(copied_gate, *_invocation(script, runpy, "--register", "unknown", source, mirror))
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.endswith(b"\n") and b"\r" not in result.stdout
    value = json.loads(result.stdout)
    assert value["source_words"] == value["mirror_words"] == 20
    assert value["gate_v3"]["schema_version"] == "setec-mirror-gate/3"
    assert b"alpha beta gamma" not in result.stdout and b"red blue green" not in result.stdout
    stale = outside / "stale.json"
    stale.write_text(json.dumps({
        "schema_version": "setec-mirror-quote-regions/1", "source_sha256": "0" * 64,
        "complete": False, "regions": [],
    }), encoding="utf-8")
    result = _run(copied_gate, *_invocation(script, runpy, source, mirror, "--quote-spans", stale))
    assert result.returncode == 3 and result.stdout == b""
    assert result.stderr == b"mirror_gate_error:sidecar_stale\n"


@pytest.mark.parametrize("runpy", [False, True])
def test_help_remains_closed_usage_error(copied_gate, runpy):
    _, plugin, *_ = copied_gate
    result = _run(copied_gate, *_invocation(plugin / "scripts/_mirror_gate.py", runpy, "--help"))
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == b"mirror_gate_error:usage_error\n"
