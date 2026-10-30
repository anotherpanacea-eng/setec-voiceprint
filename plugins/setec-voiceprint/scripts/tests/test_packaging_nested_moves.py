"""Copied-plugin compatibility for the remaining nested entry points."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("nested_plugin")
    plugin = root / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return root, plugin / "scripts"


def _run(copied_plugin, *args, stdlib_only=True):
    root, _ = copied_plugin
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", *(["-S"] if stdlib_only else []), *map(str, args)],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("family,name", [("replication", "train_xgboost"), ("calibration", "calibration_survey")])
@pytest.mark.parametrize("package_first", [False, True])
def test_copied_import_identity(copied_plugin, family, name, package_first):
    _, scripts = copied_plugin
    result = _run(copied_plugin, "-c", """
import importlib, pathlib, sys
scripts, family, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
sys.path.insert(0, str(pathlib.Path(scripts) / family))
names = [name, 'setec.' + family + '.' + name]
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(n) for n in names]
assert a is b
assert a.__file__ == str(pathlib.Path(scripts) / 'setec' / family / (name + '.py'))
a.SCRIPT_VERSION = 'shared sentinel'
assert b.SCRIPT_VERSION == 'shared sentinel'
assert not any(n in sys.modules for n in ('spacy', 'torch', 'xgboost'))
""", scripts, family, name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("family,name", [("replication", "train_xgboost"), ("calibration", "calibration_survey")])
def test_copied_legacy_cli(copied_plugin, family, name):
    _, scripts = copied_plugin
    result = _run(copied_plugin, scripts / family / (name + ".py"), "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_copied_train_optional_dependency_and_empty_manifest(copied_plugin):
    root, scripts = copied_plugin
    result = _run(copied_plugin, "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from setec.replication import train_xgboost as train
import manifest_format
assert train.load_jsonl is manifest_format.load_jsonl
assert 'xgboost' not in sys.modules
try:
    train.fit_xgb([], [], [], [], task='binary', hyperparams={}, n_classes=2)
except RuntimeError as exc:
    assert 'xgboost is required' in str(exc)
else:
    raise AssertionError('missing optional dependency was not reported')
assert train.macro_f1([0, 1], [0, 1], n_classes=2) == 1.0
""", scripts)
    assert result.returncode == 0, result.stderr
    manifest = root / "empty.jsonl"
    manifest.write_text("", encoding="utf-8")
    result = _run(
        copied_plugin, scripts / "replication/train_xgboost.py",
        "--feature-manifest", manifest, "--output-dir", root / "train-output",
    )
    assert result.returncode == 2
    assert "manifest yielded 0 usable rows" in result.stderr


def test_copied_survey_spawn_matches_serial(copied_plugin):
    root, scripts = copied_plugin
    probe = root / "survey_spawn.py"
    probe.write_text('''
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
import multiprocessing
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from setec.calibration import calibration_survey as survey
import calibrate_thresholds
assert survey.ct is calibrate_thresholds

if __name__ == '__main__':
    args = argparse.Namespace(
        manifest=Path('synthetic.jsonl'), use='validation', fpr_target=0.25,
        bootstrap_resamples=8, bootstrap_confidence=0.95, bootstrap_seed=7,
        tier2=False, tier3=False,
    )
    records = [
        {'label': label, 'per_signal_scores':
            {'tier1.sentence_length.burstiness_B': value}}
        for label, values in [(0, [0.2, 0.3, 0.4, 0.5]),
                              (1, [-0.9, -0.8, -0.7, -0.6])]
        for value in values
    ]
    expected = survey.survey_one_signal(
        'burstiness_B', args, tpr_floor=0.05, aggressiveness_tolerance=0.05,
        cached_records=records, cached_scoring_meta={},
    )
    assert expected.error is None, expected.error
    assert expected.n_pos == expected.n_neg == 4
    with ProcessPoolExecutor(
        max_workers=1, mp_context=multiprocessing.get_context('spawn'),
        initializer=survey._pool_init,
        initargs=(records, {}, args, 0.05, 0.05),
    ) as pool:
        actual = pool.submit(survey._survey_one_signal_pooled,
                             'burstiness_B').result(timeout=30)
    assert asdict(actual) == asdict(expected)
''', encoding="utf-8")
    result = _run(copied_plugin, probe, scripts, stdlib_only=False)
    assert result.returncode == 0, result.stderr
