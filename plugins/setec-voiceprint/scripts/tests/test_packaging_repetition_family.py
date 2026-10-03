"""Observable compatibility of the whole repetition family, without models."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_zero_install import make_bare_copy

SCRIPTS = Path(__file__).resolve().parents[1]
FAMILY = ("repetition_audit", "manuscript_repetition_audit", "chapter_distinctiveness_audit")


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", "-c", code, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("stem", FAMILY)
@pytest.mark.parametrize("package_first", (False, True))
def test_module_identity_and_shared_monkeypatches(stem, package_first, tmp_path):
    (tmp_path / "reference.txt").write_text("River stone.", encoding="utf-8")
    result = _probe("""
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
stem = sys.argv[2]
names = [stem, 'setec.surfaces.' + stem]
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.SCRIPT_DIR == Path(sys.argv[1])
assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
for writer, reader, word in [(a, b, 'copper'), (b, a, 'silver')]:
    if stem == 'repetition_audit':
        writer.tokenize = lambda text: [word, word]
        assert reader.load_baseline_counts([])[1] == 0
        assert reader.score_against_baseline_counts('x', {}, 1,
            function_words=set(), anchor_words=set(), min_count=1)[0][0]['word'] == word
    elif stem == 'chapter_distinctiveness_audit':
        writer.tokenize = lambda text: [word, word]
        assert reader.precompute_chapter_counts([{'text': 'x'}])[0][0] == {word: 2}
    else:
        writer.load_baseline_counts = lambda paths: ({word: 2}, 2, paths, [])
        writer.score_against_baseline_counts = lambda *args, **kw: ([], 17)
        result = reader.audit_manuscript_repetition([{'label': 'one', 'text': 'x'}],
            sys.argv[4], function_words=set(), anchor_words=set(),
            min_count=1, min_word_len=1, cluster_window=3)
        assert result['total_target_words'] == 17 and result['baseline_words'] == 2
ra = importlib.import_module('repetition_audit')
mra = importlib.import_module('manuscript_repetition_audit')
assert mra.BaselineError is ra.BaselineError
assert not any(name in sys.modules for name in ('spacy', 'torch', 'nltk', 'sentence_transformers'))
""", tmp_path, SCRIPTS, stem, package_first, tmp_path)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def bare_plugin(tmp_path):
    return make_bare_copy(tmp_path)


def test_copied_plugin_identity_and_baseline_self_exclusion(bare_plugin, tmp_path):
    result = _probe("""
import importlib, json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
ra, mra, cda = [importlib.import_module(name) for name in
    ('repetition_audit', 'manuscript_repetition_audit', 'chapter_distinctiveness_audit')]
for module in (ra, mra, cda):
    assert module is importlib.import_module('setec.surfaces.' + module.TOOL_NAME)
    assert module.SCRIPT_DIR == Path(sys.argv[1])
root = Path.cwd()
target = root / 'target.txt'
target.write_text('Copper copper copper private_synthetic_marker.', encoding='utf-8')
reference = root / 'reference.txt'
reference.write_text('Silver silver river.', encoding='utf-8')
chapters = [{'label': 'one', 'text': target.read_text()},
            {'label': 'two', 'text': 'Lantern lantern lantern.'}]
kw = dict(function_words=set(), anchor_words=set(), min_count=1,
          min_word_len=4, cluster_window=3)
result = mra.audit_manuscript_repetition(chapters, str(root),
                                        target_paths={target}, **kw)
assert result['baseline_files_loaded'] == [str(reference)]
assert result['baseline_words'] == 3
payload = mra.build_audit_payload(result, target_path=target)
assert 'private_synthetic_marker' not in json.dumps(payload)
assert payload['available'] is True and payload['baseline']['n_files'] == 1
reference.unlink()
try:
    mra.audit_manuscript_repetition(chapters, str(root), target_paths={target}, **kw)
except ra.BaselineError as exc:
    assert 'No usable baseline files' in str(exc)
else:
    raise AssertionError('self-only baseline accepted')
try:
    ra.find_repetitions('Copper copper.', [], function_words=set(), anchor_words=set())
except ra.BaselineError:
    pass
else:
    raise AssertionError('empty baseline accepted')
try:
    cda.audit_chapter_distinctiveness(chapters[:1], min_ratio=1.5, **kw)
except ValueError as exc:
    assert 'at least two chapters' in str(exc)
else:
    raise AssertionError('one-chapter internal baseline accepted')
""", tmp_path, bare_plugin / "scripts")
    assert result.returncode == 0, result.stderr
    assert "Dropped manuscript files from baseline: target.txt." in result.stderr


@pytest.mark.parametrize("stem", FAMILY)
def test_copied_invalid_input_cli_refusals(bare_plugin, tmp_path, stem):
    script = bare_plugin / "scripts" / (stem + ".py")
    if stem == "chapter_distinctiveness_audit":
        target = tmp_path / "one-chapter.txt"
        target.write_text("Only one chapter here.", encoding="utf-8")
        args = [str(target), "--json"]
        expected = "at least two chapters"
    else:
        target = tmp_path / "target.txt"
        target.write_text("Copper copper copper.", encoding="utf-8")
        args = [str(target), "--baseline-dir", str(tmp_path / "absent"), "--json"]
        expected = "No .txt or .md files" if stem == "repetition_audit" else "No usable baseline files"
    result = _probe("import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')",
                    tmp_path, script, *args)
    assert result.returncode == 1 and not result.stdout, result.stderr
    assert expected in result.stderr and "Traceback" not in result.stderr
