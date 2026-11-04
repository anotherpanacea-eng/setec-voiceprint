"""Detached probe aliases retain provenance and unsupported-host refusal."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_probe(tmp_path_factory):
    outside = tmp_path_factory.mktemp("probe_set")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return outside, plugin


def _run(copied_probe, *args):
    outside, _ = copied_probe
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_types_and_producer_hook(copied_probe, package_first):
    _, plugin = copied_probe
    result = _run(copied_probe, "-c", """
import importlib, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['reconstructibility_probe_set', 'setec.surfaces.reconstructibility_probe_set']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
for name in ('TokenCoordinate', 'ProbeSetError', 'WindowsPrivateNodeProof'):
    assert pickle.loads(('creconstructibility_probe_set\\n' + name + '\\n.').encode()) is getattr(a, name)
identity = ('b' * 40, 'd' * 64)
a._git_identity = lambda: identity
assert b._git_identity() == identity
published = []
b._publish_after_identity_gate(identity, lambda: published.append('invented result'))
assert published == ['invented result']
assert not any(name in sys.modules for name in ('spacy', 'torch', 'transformers', 'requests'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_probe, runpy):
    _, plugin = copied_probe
    script = plugin / "scripts/reconstructibility_probe_set.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_probe, *args)
    assert result.returncode == 0, result.stderr
    assert "model-free" in result.stdout and "--resume" in result.stdout


@pytest.mark.skipif(sys.platform == "darwin", reason="native Darwin publication has its own qualification")
def test_native_cli_refuses_before_private_access(copied_probe):
    outside, plugin = copied_probe
    private = outside / "must-not-open"
    result = _run(copied_probe, plugin / "scripts/reconstructibility_probe_set.py",
                  "--population-manifest", "meta/population.jsonl",
                  "--population-attestation", "meta/attestation.json", "--plan", "meta/plan.json",
                  "--private-root", private, "--checkpoint-dir", "checkpoint", "--output-dir", "output")
    expected = ("windows_publication_unsupported" if os.name == "nt" else
                "linux_acl_backend_unsupported" if sys.platform.startswith("linux") else "platform_unsupported")
    assert result.returncode == 4 and result.stdout == ""
    assert result.stderr == expected + "\n" and not private.exists()


def test_copied_uncommitted_producer_cannot_claim_identity(copied_probe):
    _, plugin = copied_probe
    result = _run(copied_probe, "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from setec.surfaces import reconstructibility_probe_set as probe
try:
    probe._git_identity()
except probe.ProbeSetError as exc:
    assert exc.code == 'producer_identity_refused'
else:
    raise AssertionError('uncommitted copied producer acquired identity')
""", plugin / "scripts")
    assert result.returncode == 0, result.stderr


def test_real_git_identity_binds_committed_implementation_and_refuses_dirt(tmp_path):
    outside = tmp_path.resolve()
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    # Git initialization and staging are confined to this newly copied fixture.
    assert plugin.resolve().parent == outside and plugin != Path.cwd()
    git = shutil.which("git")
    assert git is not None
    hooks = outside / "empty-hooks"
    hooks.mkdir()

    def command(*args):
        return subprocess.run(
            [git, "-C", str(plugin), "-c", "core.autocrlf=false", "-c", f"core.hooksPath={hooks}",
             "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "commit.gpgsign=false", *args],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()

    command("init", "--quiet", "--initial-branch=main")
    (plugin / ".gitignore").write_text("*\n", encoding="utf-8")
    implementation = plugin / "scripts/setec/surfaces/reconstructibility_probe_set.py"
    alias = plugin / "scripts/reconstructibility_probe_set.py"
    command("add", "-f", "--", ".gitignore", str(implementation), str(alias))
    command("commit", "--quiet", "-m", "Synthetic committed producer")
    head = command("rev-parse", "HEAD")
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from setec.surfaces import reconstructibility_probe_set as probe
print(json.dumps(probe._git_identity()))
"""
    result = _run((outside, plugin), "-c", code, plugin / "scripts")
    assert result.returncode == 0, result.stderr
    expected = "sha256:" + hashlib.sha256(implementation.read_bytes()).hexdigest()
    assert json.loads(result.stdout) == [head, expected]
    assert expected != "sha256:" + hashlib.sha256(alias.read_bytes()).hexdigest()
    original = implementation.read_bytes()
    try:
        implementation.write_bytes(original + b"\n")
        dirty = _run((outside, plugin), "-c", code, plugin / "scripts")
        assert dirty.returncode != 0 and "producer_identity_refused" in dirty.stderr
    finally:
        implementation.write_bytes(original)
    restored = _run((outside, plugin), "-c", code, plugin / "scripts")
    assert restored.returncode == 0 and restored.stdout == result.stdout
