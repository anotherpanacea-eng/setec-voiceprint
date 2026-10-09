"""The copy lint: registered primitives must not be re-defined elsewhere."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("textprims_lint", ROOT / "tools/gen_textprims_inventory.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

REGISTRY = '''
import re
WORDS = {"a", "and", "the"}
_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words_alpha(text):
    """Count words."""
    return len(_WORD_RE.findall(text))


from types import MappingProxyType as _MappingProxyType
PRIMITIVES = _MappingProxyType({"WORDS": "setec.core.textprims", "count_words_alpha": "setec.core.textprims"})
'''


def _tree(tmp_path, files):
    scripts = tmp_path / "plugins/setec-voiceprint/scripts"
    for name, text in {"setec/core/textprims.py": REGISTRY, **files}.items():
        (scripts / name).parent.mkdir(parents=True, exist_ok=True)
        (scripts / name).write_text(text, encoding="utf-8")
    return lint.check(tmp_path)


def test_repository_has_no_copies():
    assert lint.check(ROOT) == []


def test_renamed_copy_is_reported(tmp_path):
    errors = _tree(tmp_path, {"surface.py": 'import re\n_WORD_RE = re.compile(r"[A-Za-z\']+")\n\ndef n(text):\n    return len(_WORD_RE.findall(text))\n'})
    assert errors == ["copy of registered primitive count_words_alpha at plugins/setec-voiceprint/scripts/surface.py:4; import it from setec.core.textprims"]


def test_same_name_with_different_code_or_globals_is_not_a_copy(tmp_path):
    assert _tree(tmp_path, {
        "a.py": "def count_words_alpha(text):\n    return len(text.split())\n",
        "b.py": 'import re\n_WORD_RE = re.compile(r"[A-Za-z0-9\']+")\n\ndef count_words_alpha(text):\n    return len(_WORD_RE.findall(text))\n',
    }) == []


def test_copied_word_set_is_reported(tmp_path):
    errors = _tree(tmp_path, {"c.py": 'STOP = {"the", "a", "and"}\n'})
    assert errors == ["copy of registered primitive WORDS at plugins/setec-voiceprint/scripts/c.py:1; import it from setec.core.textprims"]


def test_registered_name_missing_from_its_owner_is_reported(tmp_path):
    errors = _tree(tmp_path, {"setec/core/textprims.py": REGISTRY.replace('"WORDS": "setec.core.textprims"', '"WORDS": "setec.core.other"')})
    assert errors == ["registered owner missing: WORDS -> setec.core.other"]
