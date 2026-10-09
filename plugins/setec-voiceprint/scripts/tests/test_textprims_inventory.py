"""The copy lint: registered primitives must not be re-defined elsewhere."""
import importlib.util
from pathlib import Path

import pytest

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


def test_copy_with_renamed_unannotated_parameter_is_reported(tmp_path):
    errors = _tree(tmp_path, {"d.py": 'import re\n_WORD_RE = re.compile(r"[A-Za-z\']+")\n\ndef _count(s):\n    return len(_WORD_RE.findall(s))\n'})
    assert errors == ["copy of registered primitive count_words_alpha at plugins/setec-voiceprint/scripts/d.py:4; import it from setec.core.textprims"]



@pytest.mark.parametrize("kind,primitive", [
    ("renamed-function", "count_words_alpha"),
    ("nested-function", "count_words_alpha"),
    ("same-name-function", "count_words_alpha"),
    ("top-level-set", "WORDS"),
    ("nested-set", "WORDS"),
    ("same-line-set", "WORDS"),
])
def test_same_owner_copies_are_reported(tmp_path, kind, primitive):
    # One canonical definition is allowed, not every matching node in its file.
    additions = {
        "renamed-function": "\n\ndef copied(s):\n    return len(_WORD_RE.findall(s))\n",
        "nested-function": "\n\ndef outer():\n    def copied(s):\n        return len(_WORD_RE.findall(s))\n    return copied\n",
        "same-name-function": "\n\ndef count_words_alpha(text):\n    return len(_WORD_RE.findall(text))\n",
        "top-level-set": "\nCOPY = {\"the\", \"a\", \"and\"}\n",
        "nested-set": "\n\ndef outer():\n    WORDS = {\"the\", \"a\", \"and\"}\n    return WORDS\n",
    }
    if kind == "same-line-set":
        owner = REGISTRY.replace('WORDS = {"a", "and", "the"}',
                                 'WORDS = {"a", "and", "the"}; COPY = {"the", "a", "and"}')
    else:
        owner = REGISTRY + additions[kind]
    errors = _tree(tmp_path, {"setec/core/textprims.py": owner})
    assert len(errors) == 1
    assert f"copy of registered primitive {primitive} at plugins/setec-voiceprint/scripts/setec/core/textprims.py:" in errors[0]


def test_owner_canonical_definitions_and_aliases_are_allowed(tmp_path):
    owner = REGISTRY + "\nword_alias = WORDS\ncount_alias = count_words_alpha\n"
    assert _tree(tmp_path, {"setec/core/textprims.py": owner}) == []


def test_owner_different_body_is_not_a_copy(tmp_path):
    owner = REGISTRY + "\n\ndef other_count(text):\n    return len(text.split())\n"
    assert _tree(tmp_path, {"setec/core/textprims.py": owner}) == []
