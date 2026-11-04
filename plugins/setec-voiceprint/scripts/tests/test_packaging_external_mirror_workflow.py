"""The nested workflow keeps using its stay-put helpers in a bare plugin copy."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest


@pytest.fixture(scope="module")
def copied_workflow(tmp_path_factory):
    outside = tmp_path_factory.mktemp("mirror_workflow")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    environment = outside / "foreign"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return outside, plugin, python


def _command(copied_workflow, *args):
    outside, _, python = copied_workflow
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [str(python), *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("package_first", [False, True])
def test_copied_import_identity_and_old_pickle_globals(copied_workflow, package_first):
    _, plugin, _ = copied_workflow
    _command(copied_workflow, "-c", """
import importlib, pickle, sys
from pathlib import Path
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
sys.path.insert(0, str(Path(scripts) / 'external_mirror'))
names = ['workflow', 'setec.external_mirror.workflow']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
a.SCRIPT_VERSION = 'synthetic sentinel'
assert b.SCRIPT_VERSION == 'synthetic sentinel'
assert pickle.loads(b'cworkflow\\nPrepareResult\\n.') is a.PrepareResult
assert pickle.loads(b'cworkflow\\nScoreResult\\n.') is a.ScoreResult
""", plugin / "scripts", package_first)


@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_historical_nested_cli_without_install(copied_workflow, mode):
    _, plugin, _ = copied_workflow
    script = plugin / "scripts/external_mirror/workflow.py"
    args = [script, "--help"] if mode == "direct" else [
        "-c", "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
        script, "--help",
    ]
    help_text = _command(copied_workflow, *args)
    for name in ("prepare", "status", "score"):
        assert name in help_text


def test_actual_default_helpers_round_trip_without_models(copied_workflow):
    outside, plugin, _ = copied_workflow
    # The distance helper tries spaCy before reading --metrics. Its absence in
    # this foreign venv is essential; the workflow's children inherit no -S flag.
    _command(copied_workflow, "-c", """
import importlib.util
assert all(importlib.util.find_spec(name) is None for name in
           ('spacy', 'torch', 'sentence_transformers', 'sklearn'))
""")
    target = outside / "target.txt"
    target.write_text(" ".join(f"synthetic{i}" for i in range(400)), encoding="utf-8")
    script = plugin / "scripts/external_mirror/workflow.py"
    _command(
        copied_workflow, script, "prepare", target, "--runs-root", outside / "runs",
        "--run-id", "fixture", "--families", "claude,human_control",
        "--windows", "2", "--context", "10", "--continuation", "5",
    )
    run = outside / "runs/fixture"
    for family in ("claude", "human_control"):
        for index in (1, 2):
            (run / "outputs" / family / f"window_{index}.txt").write_text(
                f"A synthetic {family} continuation for window {index} with ordinary words.",
                encoding="utf-8",
            )
    before = json.loads(_command(copied_workflow, script, "status", run, "--json"))
    assert before["windows_count"] == 2
    assert all(not value["missing"] for value in before["families"].values())
    assert not any(before["phase_b_artifacts"].values())
    _command(copied_workflow, script, "score", run, "--metrics", "word_jaccard")
    after = json.loads(_command(copied_workflow, script, "status", run, "--json"))
    assert all(after["phase_b_artifacts"].values())
    pack = json.loads((run / "evidence_pack.json").read_text(encoding="utf-8"))
    assert pack["task_surface"] == "external_mirror_discrimination"
    assert pack["available"] is True
    assert pack["results"]["metrics_available"] == ["word_jaccard"]
    assert pack["results"]["windows_count"] == 2
    assert pack["results"]["families"] == ["claude", "human_control"]
    assert len(pack["results"]["distance_matrices_by_metric"]["word_jaccard"]) == 2
