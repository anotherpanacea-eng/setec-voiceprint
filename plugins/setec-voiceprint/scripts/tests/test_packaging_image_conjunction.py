"""Copied-plugin image detector and its live audit consumers need no install."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_detector(tmp_path_factory):
    outside = tmp_path_factory.mktemp("image_detector")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__", "brysbaert_concreteness.csv"),
    )
    return outside, plugin


def _run(copied_detector, *args):
    outside, _ = copied_detector
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_dataclass_and_loader(copied_detector, package_first):
    _, plugin = copied_detector
    result = _run(copied_detector, "-c", """
import importlib, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['image_conjunction', 'setec.surfaces.image_conjunction']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert pickle.loads(b'cimage_conjunction\\nImageConjunction\\n.') is a.ImageConjunction
a._load_spacy_with_parsing = lambda: 'invented loader'
assert b._load_spacy_with_parsing() == 'invented loader'
assert not any(name in sys.modules for name in ('spacy', 'torch', 'sentence_transformers'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_detector, runpy):
    _, plugin = copied_detector
    script = plugin / "scripts/image_conjunction.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys; p=sys.argv[1]; sys.argv=[p,'--help']; runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_detector, *args)
    assert result.returncode == 0, result.stderr
    assert "AIC-8" in result.stdout and "fetch_brysbaert.py" in result.stdout


def test_actual_cli_missing_data_and_input(copied_detector):
    outside, plugin = copied_detector
    script = plugin / "scripts/image_conjunction.py"
    text = outside / "invented.txt"
    text.write_text("Vurnish gralnet.\n", encoding="utf-8")
    expected = {"signal_path": "aic_8_9.image_conjunction_density",
                "available": False, "reason": "data_not_installed"}
    result = _run(copied_detector, script, text)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert "fetch_brysbaert.py" in result.stderr
    output = outside / "unavailable.json"
    result = _run(copied_detector, script, text, "--out", output)
    assert result.returncode == 0 and result.stdout == "", result.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == expected
    result = _run(copied_detector, script, outside / "missing.txt")
    assert result.returncode == 1 and "input file not found" in result.stderr


def test_injected_detection_reaches_canonical_main(copied_detector):
    outside, plugin = copied_detector
    text, output = outside / "pair.txt", outside / "pair.json"
    text.write_text("Vurnish gralnet.\n", encoding="utf-8")
    result = _run(copied_detector, "-c", """
import json, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import image_conjunction as old
from setec.surfaces import image_conjunction as new
old.concreteness.availability_status = lambda *a: (None, None)
old.concreteness.availability_reason = lambda *a: None
old.concreteness.get_concreteness = lambda word, *a: {'vurnish': 1.55, 'gralnet': 4.60}.get(word)
old.concreteness.vocab_size = lambda *a: 2
old.embeddings.cosine_similarity = lambda *a: 0.1
old.embeddings.model_identifier = lambda: {'model': 'invented'}
head = SimpleNamespace(lemma_='gralnet', pos_='NOUN', dep_='ROOT', is_punct=False)
modifier = SimpleNamespace(lemma_='vurnish', pos_='ADJ', dep_='amod', head=head, is_punct=False)
old._load_spacy_with_parsing = lambda: lambda text: [modifier, head]
pair = new.evaluate_pair('vurnish', 'gralnet', 'amod')
assert pair['concreteness_gap'] > 3 and pair['embedding_similarity'] == 0.1
assert new.main([sys.argv[2], '--out', sys.argv[3], '--baseline', '5']) == 0
with open(sys.argv[3], encoding='utf-8') as stream: block = json.load(stream)
assert block['value'] == 500 and block['status'] == 'provisional'
assert block['diagnostics']['conjunction_count'] == 1
assert block['baseline_comparison']['elevation_factor'] == 100
assert block['conjunctions'][0]['is_paragraph_final_sentence'] is True
assert 'spacy' not in sys.modules
""", plugin / "scripts", text, output)
    assert result.returncode == 0, result.stderr


def test_consumers_share_missing_data_and_model_refusal(copied_detector):
    _, plugin = copied_detector
    result = _run(copied_detector, "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from setec.surfaces import image_conjunction as image
from setec.surfaces import prestige_metaphor as prestige, aesthetic_authority_audit as aesthetic, variance_audit as variance
assert prestige.image_conjunction is aesthetic.image_conjunction is image
block = variance._aic8_image_prestige_block('Invented input.')
assert block['available'] is False and block['reason'] == 'data_not_installed'
image.concreteness.availability_status = lambda *a: (None, None)
block = variance._aic8_image_prestige_block('Invented input.')
assert block['available'] is False and 'spaCy is not installed' in block['reason']
assert 'value' not in block and 'image_conjunction_density' not in block
assert 'spacy' not in sys.modules
""", plugin / "scripts")
    assert result.returncode == 0, result.stderr
