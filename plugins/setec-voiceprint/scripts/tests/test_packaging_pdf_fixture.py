"""Historical PDF fixture entry point keeps optionality and adjacent outputs."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def copied_builder(tmp_path):
    plugin = tmp_path / "plugin"
    original = Path(__file__).resolve().parents[2]
    for relative in (
        ".claude-plugin/plugin.json", "scripts/setec/__init__.py",
        "scripts/setec/paths.py", "scripts/setec/test_data/__init__.py",
        "scripts/setec/test_data/pdf_inventory_fixture/__init__.py",
        "scripts/setec/test_data/pdf_inventory_fixture/_make_fixtures.py",
        "scripts/test_data/pdf_inventory_fixture/_make_fixtures.py",
    ):
        target = plugin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / relative, target)
    return tmp_path, plugin


def _run(copied_builder, *args, bare=False):
    outside, _ = copied_builder
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["SOURCE_DATE_EPOCH"] = "1577836800"
    return subprocess.run(
        [sys.executable, "-B", *(["-S"] if bare else []), *map(str, args)],
        cwd=outside, env=env, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("legacy_name", [
    "_make_fixtures", "test_data.pdf_inventory_fixture._make_fixtures",
])
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_builder_hooks(copied_builder, legacy_name, package_first):
    pytest.importorskip("reportlab")
    _, plugin = copied_builder
    result = _run(copied_builder, "-c", """
import importlib, sys
from pathlib import Path
scripts = Path(sys.argv[1])
sys.path[:0] = [str(scripts), str(scripts / 'test_data/pdf_inventory_fixture')]
names = [sys.argv[2], 'setec.test_data.pdf_inventory_fixture._make_fixtures']
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.HERE == scripts / 'test_data/pdf_inventory_fixture'
a.main = lambda: 17
assert b.main() == 17
assert not any(name in sys.modules for name in ('spacy', 'torch', 'transformers'))
""", plugin / "scripts", legacy_name, package_first)
    assert result.returncode == 0, result.stderr


def _invoke(script, runpy):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p];runpy.run_path(p,run_name='__main__')", script]
    return [script]


@pytest.mark.parametrize("runpy", [False, True])
def test_missing_reportlab_refuses_before_outputs(copied_builder, runpy):
    _, plugin = copied_builder
    script = plugin / "scripts/test_data/pdf_inventory_fixture/_make_fixtures.py"
    result = _run(copied_builder, *_invoke(script, runpy), bare=True)
    assert result.returncode == 2 and result.stdout == b""
    assert result.stderr == (
        "reportlab is needed to (re)build fixture PDFs. pip install --user reportlab"
        + os.linesep
    ).encode()
    assert not list(plugin.rglob("*.pdf"))


@pytest.mark.parametrize("runpy", [False, True])
def test_real_generation_preserves_classification_inputs(copied_builder, runpy):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    _, plugin = copied_builder
    historical = plugin / "scripts/test_data/pdf_inventory_fixture"
    result = _run(copied_builder, *_invoke(historical / "_make_fixtures.py", runpy))
    assert result.returncode == 0 and result.stderr == b""
    assert b"Total fixture size:" in result.stdout
    with_meta = pypdf.PdfReader(historical / "text_layer_with_metadata.pdf")
    assert with_meta.metadata.title == "On the Discipline of Attention"
    assert with_meta.metadata.author == "Synthetic Author"
    assert "attention" in "".join(page.extract_text() for page in with_meta.pages)
    without_meta = pypdf.PdfReader(historical / "text_layer_without_metadata.pdf")
    assert not without_meta.metadata.title and not without_meta.metadata.author
    assert any(page.extract_text().strip() for page in without_meta.pages)
    image = pypdf.PdfReader(historical / "image_only.pdf")
    assert len(image.pages) == 1 and not image.pages[0].extract_text().strip()
    with pytest.raises(pypdf.errors.PdfReadError):
        pypdf.PdfReader(historical / "corrupted.pdf")
    assert not list((plugin / "scripts/setec/test_data").rglob("*.pdf"))
