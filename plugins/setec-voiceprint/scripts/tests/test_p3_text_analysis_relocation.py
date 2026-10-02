"""Compatibility of text-analysis imports and copied-plugin execution."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
MODULES = ("preprocessing", "verbatim_cover", "segmentation_feature_lens")


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("package_first", (False, True))
def test_import_identity_and_shared_patches(name, package_first, tmp_path):
    code = '''
import importlib, sys
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
paths = [name, "setec.core." + name]
if sys.argv[3] == "True":
    paths.reverse()
a, b = [importlib.import_module(p) for p in paths]
assert a is b
if name == "preprocessing":
    symbol = "count_tokens"
    observe = lambda: b.strip_non_prose("Ordinary prose.", allow_non_prose=True)[1]["input_tokens_before"]
elif name == "verbatim_cover":
    symbol = "_match_len"
    observe = lambda: b.audit_originality("alpha beta", [("reference", "alpha beta")], min_ngram=1)["coverage"]
else:
    symbol = "safe_mean"
    observe = lambda: b.window_features("Ordinary prose.")["sent_shape_mean"]
original = getattr(a, symbol)
setattr(a, symbol, lambda *args, **kwargs: 0)
assert observe() == 0
setattr(b, symbol, original)
assert observe() > 0
'''
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code, str(SCRIPTS), name, str(package_first)],
        cwd=tmp_path, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ("direct", "runpy", "package"))
def test_copied_plugin_from_foreign_cwd(mode, tmp_path):
    copied = tmp_path / "plugin" / "scripts"
    shutil.copytree(SCRIPTS, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    for name in MODULES:
        path = copied / (name + ".py")
        if mode == "direct":
            command = [sys.executable, "-I", "-S", "-B", str(path)]
        elif mode == "runpy":
            command = [sys.executable, "-I", "-S", "-B", "-c",
                       "import runpy,sys; original=sys.modules['__main__']; "
                       "runpy.run_path(sys.argv[1],run_name='__main__'); "
                       "assert sys.modules['__main__'] is original", str(path)]
        else:
            command = [sys.executable, "-I", "-S", "-B", "-c",
                       "import importlib,sys; sys.path.insert(0,sys.argv[1]); "
                       "m=importlib.import_module('setec.core.'+sys.argv[2]); "
                       "assert m is importlib.import_module(sys.argv[2])", str(copied), name]
        result = subprocess.run(command, cwd=foreign, text=True, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert result.stderr == ""
