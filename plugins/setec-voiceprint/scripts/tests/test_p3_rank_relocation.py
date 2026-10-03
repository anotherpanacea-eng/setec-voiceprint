"""Legacy identity and copied-plugin execution for the rank helper relocation."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("package_first", [False, True])
def test_imports_share_identity_and_rank_monkeypatches(package_first):
    imports = (
        "from setec.core import rank_space_signals as package\n"
        "import rank_space_signals as legacy\n"
    )
    if not package_first:
        imports = (
            "import rank_space_signals as legacy\n"
            "from setec.core import rank_space_signals as package\n"
        )
    code = f"import sys\nsys.path.insert(0, {str(SCRIPTS)!r})\n" + imports + """
assert legacy is package
original = legacy._rank_of_token
try:
    legacy._rank_of_token = lambda *_: 0
    assert package.rank_series_from_distributions([[0.0, 0.0]], [0, 1], [1.0])["log_rank_series"] == [0.0]
    package._rank_of_token = lambda *_: 3
    assert legacy.rank_series_from_distributions([[0.0, 0.0]], [0, 1], [1.0])["log_rank_series"] == [package.math.log(4)]
finally:
    package._rank_of_token = original
assert not any(m in sys.modules for m in ("torch", "transformers", "numpy", "scipy"))
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("mode", ["direct", "runpy", "package"])
def test_copied_plugin_from_foreign_cwd(tmp_path, mode):
    # Copy the real runtime, not the repository's symlinked scripts/ view.
    copied = tmp_path / "plugin"
    shutil.copytree(SCRIPTS.parent, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    launcher = copied / "scripts" / "rank_space_signals.py"
    if mode == "direct":
        argv = [sys.executable, "-B", str(launcher)]
    elif mode == "runpy":
        code = f"""
import runpy, sys
before = sys.modules["__main__"]
runpy.run_path({str(launcher)!r}, run_name="__main__")
assert sys.modules["__main__"] is before
"""
        argv = [sys.executable, "-I", "-S", "-B", "-c", code]
    else:
        code = f"""
import sys
sys.path.insert(0, {str(copied / 'scripts')!r})
from setec.core import rank_space_signals as rs
assert rs.aggregate_rank_signals([], [], [])['lrr'] is None
assert not any(m in sys.modules for m in ("torch", "transformers", "numpy", "scipy"))
"""
        argv = [sys.executable, "-I", "-S", "-B", "-c", code]
    result = subprocess.run(argv, cwd=foreign, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
