"""Detached composer aliases preserve pure burst bytes and native refusals."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_composer(tmp_path_factory):
    outside = tmp_path_factory.mktemp("imessage_composer")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return outside, plugin


def _run(copied_composer, *args):
    outside, _ = copied_composer
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_types_build_exact_bursts_and_share_main_hook(copied_composer, package_first):
    _, plugin = copied_composer
    result = _run(copied_composer, "-c", """
import hashlib, importlib, json, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['compose_imessage_review_bursts', 'setec.surfaces.compose_imessage_review_bursts']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
for name in ('BurstConfig', 'RetainedSourceRow', 'SourceEvent', 'ReviewBurstError'):
    assert pickle.loads(('ccompose_imessage_review_bursts\\n' + name + '\\n.').encode()) is getattr(a, name)
texts = [b'Invented \\tfirst.', b'Second\\r\\ninvented piece.']
rows = [a.RetainedSourceRow(
    source_index=i, source_ordinal='source-' + str(i), entry_locator='hmac-sha256:' + str(i + 1) * 64,
    text_bytes=text, content_sha256='sha256:' + hashlib.sha256(text).hexdigest(),
    word_count=2 + i, unix_nanoseconds=i * 30 * 60 * 1_000_000_000,
    local_date='2020-06-15', group_status=a.atomic.GROUP_STATUS_DIRECT,
    group_locator='hmac-sha256:' + 'a' * 64,
) for i, text in enumerate(texts)]
bursts = b.build_bursts([a.SourceEvent(row, None) for row in rows],
                      a.BurstConfig(gap_minutes=30, target_words=5, min_review_words=20))
assert len(bursts) == 1
expected = b'\\n\\n'.join(texts)
assert bursts[0].text_bytes == expected
assert bursts[0].text_sha256 == 'sha256:' + hashlib.sha256(expected).hexdigest()
assert bursts[0].metadata['separator']['inserted_bytes'] == 2
assert json.loads(bursts[0].metadata_bytes) == bursts[0].metadata
a.compose_review_bursts = lambda *args, **kwargs: {'invented': True}
assert b.main(['--input-run', 'unused', '--output-root', 'unused', '--package-id', 'fixture']) == 0
assert not any(name in sys.modules for name in ('spacy', 'torch', 'transformers', 'requests'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '{"invented":true}'


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_composer, runpy):
    _, plugin = copied_composer
    script = plugin / "scripts/compose_imessage_review_bursts.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_composer, *args)
    assert result.returncode == 0, result.stderr
    assert "--package-id" in result.stdout and "--resume" in result.stdout


@pytest.mark.skipif(sys.platform == "darwin", reason="native private publication has separate Darwin coverage")
def test_native_refusal_precedes_source_or_output_access(copied_composer):
    outside, plugin = copied_composer
    source, output = outside / "must-not-read", outside / "must-not-write"
    result = _run(copied_composer, plugin / "scripts/compose_imessage_review_bursts.py",
                  "--input-run", source, "--output-root", output, "--package-id", "fixture")
    assert result.returncode == 2 and result.stdout == ""
    assert result.stderr == "error: private review-burst production requires macOS\n"
    assert not source.exists() and not output.exists()
