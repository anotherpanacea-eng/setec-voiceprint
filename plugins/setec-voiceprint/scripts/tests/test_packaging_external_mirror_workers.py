"""Copied mirror workers preserve prompts, distances and actual workflow paths."""

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest


@pytest.fixture(scope="module")
def copied_workers(tmp_path_factory):
    outside = tmp_path_factory.mktemp("mirror_workers")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__", "brysbaert_concreteness.csv", "brysbaert_concreteness.xlsx"),
    )
    foreign = outside / "foreign-venv"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(foreign)
    interpreter = foreign / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return outside, plugin, interpreter


def _run(copied_workers, *args, foreign=False):
    outside, _, interpreter = copied_workers
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [interpreter, "-B"] if foreign else [sys.executable, "-B", "-S"]
    return subprocess.run(
        [*map(str, command), *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("name", ["build_prompts", "compute_distances"])
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_import_identity_and_legacy_hooks(copied_workers, name, package_first):
    _, plugin, _ = copied_workers
    result = _run(copied_workers, "-c", """
import importlib, pickle, sys
from pathlib import Path
scripts, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
sys.path.insert(0, str(Path(scripts) / 'external_mirror'))
names = ['external_mirror.' + name, 'setec.external_mirror.' + name]
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(value) for value in names]
assert a is b is importlib.import_module(name)
if name == 'build_prompts':
    for cls in ('Token', 'WindowSpec', 'BuildResult'):
        for legacy in ('build_prompts', 'external_mirror.build_prompts'):
            assert pickle.loads(('c' + legacy + '\\n' + cls + '\\n.').encode()) is getattr(a, cls)
    a.sha256_hex = lambda value: 'shared fixture'
    assert b.sha256_hex('invented') == 'shared fixture'
else:
    a._load_embedding_backend = lambda alias: 'shared fixture'
    assert b._load_embedding_backend('unused') == 'shared fixture'
assert not any(name in sys.modules for name in ('spacy', 'sklearn', 'torch', 'sentence_transformers'))
""", plugin / "scripts", name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["build_prompts", "compute_distances"])
@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_workers, name, runpy):
    _, plugin, _ = copied_workers
    script = plugin / "scripts/external_mirror" / (name + ".py")
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_workers, *args)
    assert result.returncode == 0, result.stderr
    assert "Phase" in result.stdout


def test_prompt_bytes_context_and_actual_or_explicit_tool_binding(copied_workers):
    outside, plugin, _ = copied_workers
    result = _run(copied_workers, "-c", """
import hashlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from setec.external_mirror import build_prompts as prompt
outside = Path(sys.argv[2])
words = ['W%03d' % i for i in range(80)]
words[9] += '!'
target = outside / 'invented-target.txt'
target.write_text(' \\t '.join(words), encoding='utf-8')
def build(name, override=None):
    return prompt.build(target, outside / name, windows=2, context=4, continuation=3,
                        positioning='custom', positions=[10,30], context_grid=None,
                        fmt='both', genre_descriptor='invented note', run_id='fixture', tool_path=override)
default = build('default-prompts')
canonical = Path(prompt.__file__).resolve()
assert default.manifest['tool_path'] == str(canonical)
assert default.manifest['tool_sha256'] == hashlib.sha256(canonical.read_bytes()).hexdigest()
alias = Path(sys.argv[1]) / 'external_mirror/build_prompts.py'
assert default.manifest['tool_sha256'] != hashlib.sha256(alias.read_bytes()).hexdigest()
expected = ' \\t '.join(words[6:10])
first = default.window_specs[0]
assert (first.context_start_word, first.context_end_word, first.continuation_start_word, first.continuation_end_word) == (6,10,10,13)
assert first.context_sha256 == hashlib.sha256(expected.encode()).hexdigest()
assert expected in (default.out_dir / 'window_1.md').read_text(encoding='utf-8')
assert default.manifest['caveats_recommended'] == []
override = outside / 'explicit-tool.py'
override.write_bytes(b'# invented explicit tool identity\\n')
explicit = build('explicit-prompts', override)
assert explicit.manifest['tool_path'] == str(override.resolve())
assert explicit.manifest['tool_sha256'] == hashlib.sha256(override.read_bytes()).hexdigest()
for filename in ('window_1.md', 'window_2.md', 'windows_batched.md'):
    assert (default.out_dir / filename).read_bytes() == (explicit.out_dir / filename).read_bytes()
""", plugin / "scripts", outside)
    assert result.returncode == 0, result.stderr


def test_actual_distance_cli_preserves_absence_refusal_and_optional_skips(copied_workers):
    outside, plugin, _ = copied_workers
    ingested = {
        "manifest": {"windows_count": 2, "run_id": "fixture"},
        "families": [
            {"family": "alpha", "windows": [
                {"window_index": 1, "normalized_text": "red blue", "caveats": []},
                {"window_index": 2, "normalized_text": "", "caveats": ["refused"]},
            ]},
            {"family": "beta", "windows": [
                {"window_index": 1, "normalized_text": "blue green", "caveats": []},
                {"window_index": 2, "normalized_text": "green", "caveats": []},
            ]},
            {"family": "absent", "windows": []},
        ],
        "caveats": ["invented_caveat"],
    }
    source, output = outside / "ingested.json", outside / "distances.json"
    source.write_text(json.dumps(ingested), encoding="utf-8")
    result = _run(copied_workers, plugin / "scripts/external_mirror/compute_distances.py",
                  source, "--out", output, "--metrics", "word_jaccard,tfidf,pos_bigram_jaccard")
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    matrices = payload["distance_matrices_by_metric"]["word_jaccard"]
    assert math.isclose(matrices[0][0][1], 2 / 3)
    assert matrices[0][2] == [None, None, None]
    assert matrices[1] == [[None, None, None]] * 3
    assert "family_alpha_refused" in payload["per_window_caveats"][1]
    assert payload["metric_skip_reasons"] == {
        "tfidf": "sklearn unavailable", "pos_bigram_jaccard": "spaCy / en_core_web_sm unavailable",
    }
    assert payload["metrics_available"] == ["word_jaccard"]
    assert "invented_caveat" in payload["global_caveats"]


def test_real_workflow_defaults_use_empty_foreign_parent_and_children(copied_workers):
    outside, plugin, _ = copied_workers
    preflight = _run(copied_workers, "-c", """
from importlib.util import find_spec
assert all(find_spec(name) is None for name in ('spacy','sklearn','torch','sentence_transformers','openpyxl'))
""", foreign=True)
    assert preflight.returncode == 0, preflight.stderr
    target = outside / "workflow-target.txt"
    target.write_text(" ".join(f"invented{i}" for i in range(80)), encoding="utf-8")
    workflow = plugin / "scripts/external_mirror/workflow.py"
    run = outside / "runs/fixture"
    result = _run(copied_workers, workflow, "prepare", target, "--runs-root", outside / "runs",
                  "--run-id", "fixture", "--families", "claude,human_control",
                  "--windows", "2", "--context", "10", "--continuation", "5", foreign=True)
    assert result.returncode == 0, result.stderr
    # These are invented pasted outputs, never provider calls.
    for family, text in (("claude", "red blue"), ("human_control", "blue green")):
        for index in (1, 2):
            (run / "outputs" / family / f"window_{index}.txt").write_text(text, encoding="utf-8")
    result = _run(copied_workers, workflow, "status", run, "--json", foreign=True)
    assert result.returncode == 0, result.stderr
    assert all(value["missing"] == [] for value in json.loads(result.stdout)["families"].values())
    result = _run(copied_workers, workflow, "score", run, "--metrics", "word_jaccard", foreign=True)
    assert result.returncode == 0, result.stderr
    distances = json.loads((run / "distances.json").read_text(encoding="utf-8"))
    assert distances["metrics_available"] == ["word_jaccard"]
    for labels, matrix in zip(distances["labels_per_window"], distances["distance_matrices_by_metric"]["word_jaccard"]):
        assert math.isclose(matrix[labels.index("claude")][labels.index("human_control")], 2 / 3)
    evidence = json.loads((run / "evidence_pack.json").read_text(encoding="utf-8"))
    assert evidence["tool"] == "compose_evidence_pack" and evidence["available"] is True
