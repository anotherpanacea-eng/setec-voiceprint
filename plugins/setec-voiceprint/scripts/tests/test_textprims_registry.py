"""The registry names one owner per shared primitive and stays light to import."""
import importlib
from types import MappingProxyType

from setec.core import textprims


def test_every_primitive_resolves_to_its_owners_object():
    assert isinstance(textprims.PRIMITIVES, MappingProxyType)
    for name, owner in textprims.PRIMITIVES.items():
        assert getattr(textprims, name) is getattr(importlib.import_module(owner), name)


def test_importing_the_registry_loads_no_owner_module(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    core = tmp_path / "scripts/setec/core"
    core.mkdir(parents=True)
    shutil.copyfile(Path(textprims.__file__), core / "textprims.py")
    # Only the registry exists here: its own tables must import, and nothing
    # owned elsewhere may load until it is asked for.
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from setec.core.textprims import FUNCTION_WORDS; assert 'and' in FUNCTION_WORDS; "
            "assert sorted(m for m in sys.modules if m.startswith('setec')) == ['setec', 'setec.core', 'setec.core.textprims']")
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(tmp_path / "scripts")],
                            cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
