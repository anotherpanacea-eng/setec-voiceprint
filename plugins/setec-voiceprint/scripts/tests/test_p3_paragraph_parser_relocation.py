"""Observable paragraph-parser relocation and legacy pickle compatibility."""

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]

# Generated with Python 3.12 stdlib pickle.dumps at unchanged source base
# 7537b9f64af8181e1b59250ce66bdfc150d5ac06 (gx-a2-p3-base-20261002).
# Each pair contains SentencePosition itself and the small synthetic instance
# SentencePosition('Synthetic sentence.', 2, 1, 3, False, False), protocols 0-5.
# Keep these external compatibility bytes; do not regenerate from the candidate.
_LEGACY_PICKLES = (
    (
        "Y3BhcmFncmFwaF9wYXJzZXIKU2VudGVuY2VQb3NpdGlvbgpwMAou",
        "Y2NvcHlfcmVnCl9yZWNvbnN0cnVjdG9yCnAwCihjcGFyYWdyYXBoX3BhcnNlcgpT"
        "ZW50ZW5jZVBvc2l0aW9uCnAxCmNfX2J1aWx0aW5fXwpvYmplY3QKcDIKTnRwMwpS"
        "cDQKKGRwNQpWdGV4dApwNgpWU3ludGhldGljIHNlbnRlbmNlLgpwNwpzVnBhcmFn"
        "cmFwaF9pbmRleApwOApJMgpzVnBvc2l0aW9uX2luX3BhcmFncmFwaApwOQpJMQpz"
        "VnBhcmFncmFwaF9zaXplCnAxMApJMwpzVmlzX3BhcmFncmFwaF9pbml0aWFsCnAx"
        "MQpJMDAKc1Zpc19wYXJhZ3JhcGhfZmluYWwKcDEyCkkwMApzYi4=",
    ),
    (
        "Y3BhcmFncmFwaF9wYXJzZXIKU2VudGVuY2VQb3NpdGlvbgpxAC4=",
        "Y2NvcHlfcmVnCl9yZWNvbnN0cnVjdG9yCnEAKGNwYXJhZ3JhcGhfcGFyc2VyClNl"
        "bnRlbmNlUG9zaXRpb24KcQFjX19idWlsdGluX18Kb2JqZWN0CnECTnRxA1JxBH1x"
        "BShYBAAAAHRleHRxBlgTAAAAU3ludGhldGljIHNlbnRlbmNlLnEHWA8AAABwYXJh"
        "Z3JhcGhfaW5kZXhxCEsCWBUAAABwb3NpdGlvbl9pbl9wYXJhZ3JhcGhxCUsBWA4A"
        "AABwYXJhZ3JhcGhfc2l6ZXEKSwNYFAAAAGlzX3BhcmFncmFwaF9pbml0aWFscQtJ"
        "MDAKWBIAAABpc19wYXJhZ3JhcGhfZmluYWxxDEkwMAp1Yi4=",
    ),
    (
        "gAJjcGFyYWdyYXBoX3BhcnNlcgpTZW50ZW5jZVBvc2l0aW9uCnEALg==",
        "gAJjcGFyYWdyYXBoX3BhcnNlcgpTZW50ZW5jZVBvc2l0aW9uCnEAKYFxAX1xAihY"
        "BAAAAHRleHRxA1gTAAAAU3ludGhldGljIHNlbnRlbmNlLnEEWA8AAABwYXJhZ3Jh"
        "cGhfaW5kZXhxBUsCWBUAAABwb3NpdGlvbl9pbl9wYXJhZ3JhcGhxBksBWA4AAABw"
        "YXJhZ3JhcGhfc2l6ZXEHSwNYFAAAAGlzX3BhcmFncmFwaF9pbml0aWFscQiJWBIA"
        "AABpc19wYXJhZ3JhcGhfZmluYWxxCYl1Yi4=",
    ),
    (
        "gANjcGFyYWdyYXBoX3BhcnNlcgpTZW50ZW5jZVBvc2l0aW9uCnEALg==",
        "gANjcGFyYWdyYXBoX3BhcnNlcgpTZW50ZW5jZVBvc2l0aW9uCnEAKYFxAX1xAihY"
        "BAAAAHRleHRxA1gTAAAAU3ludGhldGljIHNlbnRlbmNlLnEEWA8AAABwYXJhZ3Jh"
        "cGhfaW5kZXhxBUsCWBUAAABwb3NpdGlvbl9pbl9wYXJhZ3JhcGhxBksBWA4AAABw"
        "YXJhZ3JhcGhfc2l6ZXEHSwNYFAAAAGlzX3BhcmFncmFwaF9pbml0aWFscQiJWBIA"
        "AABpc19wYXJhZ3JhcGhfZmluYWxxCYl1Yi4=",
    ),
    (
        "gASVKQAAAAAAAACMEHBhcmFncmFwaF9wYXJzZXKUjBBTZW50ZW5jZVBvc2l0aW9ulJOULg==",
        "gASVvQAAAAAAAACMEHBhcmFncmFwaF9wYXJzZXKUjBBTZW50ZW5jZVBvc2l0aW9u"
        "lJOUKYGUfZQojAR0ZXh0lIwTU3ludGhldGljIHNlbnRlbmNlLpSMD3BhcmFncmFw"
        "aF9pbmRleJRLAowVcG9zaXRpb25faW5fcGFyYWdyYXBolEsBjA5wYXJhZ3JhcGhf"
        "c2l6ZZRLA4wUaXNfcGFyYWdyYXBoX2luaXRpYWyUiYwSaXNfcGFyYWdyYXBoX2Zp"
        "bmFslIl1Yi4=",
    ),
    (
        "gAWVKQAAAAAAAACMEHBhcmFncmFwaF9wYXJzZXKUjBBTZW50ZW5jZVBvc2l0aW9ulJOULg==",
        "gAWVvQAAAAAAAACMEHBhcmFncmFwaF9wYXJzZXKUjBBTZW50ZW5jZVBvc2l0aW9u"
        "lJOUKYGUfZQojAR0ZXh0lIwTU3ludGhldGljIHNlbnRlbmNlLpSMD3BhcmFncmFw"
        "aF9pbmRleJRLAowVcG9zaXRpb25faW5fcGFyYWdyYXBolEsBjA5wYXJhZ3JhcGhf"
        "c2l6ZZRLA4wUaXNfcGFyYWdyYXBoX2luaXRpYWyUiYwSaXNfcGFyYWdyYXBoX2Zp"
        "bmFslIl1Yi4=",
    ),
)

_POSITION_CHECKS = """
from dataclasses import FrozenInstanceError, asdict, astuple, is_dataclass, replace

def check_position(cls, value):
    assert type(value) is cls
    assert is_dataclass(cls) and is_dataclass(value)
    expected = cls('Synthetic sentence.', 2, 1, 3, False, False)
    assert value == expected
    assert hash(value) == hash(expected)
    assert {value: 'stored'}[expected] == 'stored'
    assert astuple(value) == ('Synthetic sentence.', 2, 1, 3, False, False)
    assert asdict(value) == {
        'text': 'Synthetic sentence.',
        'paragraph_index': 2,
        'position_in_paragraph': 1,
        'paragraph_size': 3,
        'is_paragraph_initial': False,
        'is_paragraph_final': False,
    }
    changed = replace(value, text='Replacement.')
    assert type(changed) is cls and changed != value
    assert changed.text == 'Replacement.'
    assert value.text == 'Synthetic sentence.'
    for mutate in (lambda: setattr(value, 'text', 'changed'),
                   lambda: delattr(value, 'text')):
        try:
            mutate()
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError('SentencePosition lost frozen behavior')
    match value:
        case cls('Synthetic sentence.', 2, 1, 3, False, False):
            pass
        case _:
            raise AssertionError('SentencePosition positional matching changed')
"""


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    # All subprocesses are read-only (-B), so one copy can serve this module.
    parent = tmp_path_factory.mktemp("paragraph-relocation")
    root = parent / "bare-plugin"
    shutil.copytree(PLUGIN_ROOT, root, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (parent / "foreign-cwd").mkdir()
    return root


def _run(root, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", *map(str, args)],
        cwd=root.parent / "foreign-cwd",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def _imports(root, order):
    names = {
        "legacy-first": ["paragraph_parser", "setec.core.paragraph_parser"],
        "package-first": ["setec.core.paragraph_parser", "paragraph_parser"],
        "package-only": ["setec.core.paragraph_parser"],
    }[order]
    return f"""
import importlib
import sys
sys.path.insert(0, {str(root / 'scripts')!r})
for name in {names!r}:
    importlib.import_module(name)
package = sys.modules['setec.core.paragraph_parser']
"""


@pytest.mark.parametrize("order", ["legacy-first", "package-first"])
def test_imports_share_module_class_and_bidirectional_monkeypatches(bare_plugin, order):
    code = _imports(bare_plugin, order) + _POSITION_CHECKS + """
legacy = sys.modules['paragraph_parser']
assert legacy is package
assert legacy.SentencePosition is package.SentencePosition
assert package.SentencePosition.__module__ == 'paragraph_parser'
check_position(package.SentencePosition,
               legacy.SentencePosition('Synthetic sentence.', 2, 1, 3, False, False))
assert all(type(value) is legacy.SentencePosition
           for value in package.parse_document('First. Second.'))

# Exercise function-global lookups through both public import paths, rather
# than merely observing that an assigned attribute can be read back.
original = legacy.split_sentences
legacy.split_sentences = lambda paragraph: ['First patch.', 'Last patch.']
assert [value.text for value in package.parse_document('Ignored.')] == [
    'First patch.', 'Last patch.',
]
package.split_sentences = lambda paragraph: ['Reverse patch.']
finals = legacy.paragraph_final_sentences('Ignored.')
assert len(finals) == 1 and finals[0].text == 'Reverse patch.'
assert finals[0].is_paragraph_initial and finals[0].is_paragraph_final
package.split_sentences = original
assert legacy.split_sentences is original
assert legacy.split_sentences('First. Second.') == ['First.', 'Second.']
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


@pytest.mark.parametrize("protocol", range(6))
@pytest.mark.parametrize("order", ["legacy-first", "package-first", "package-only"])
def test_baseline_class_and_instance_pickles_load_in_fresh_process(bare_plugin, protocol, order):
    class_pickle, instance_pickle = _LEGACY_PICKLES[protocol]
    code = _imports(bare_plugin, order) + _POSITION_CHECKS + f"""
import base64
import pickle
cls = pickle.loads(base64.b64decode({class_pickle!r}))
value = pickle.loads(base64.b64decode({instance_pickle!r}))
assert cls is package.SentencePosition
assert sys.modules['paragraph_parser'] is package
check_position(cls, value)
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


@pytest.mark.parametrize("protocol", range(6))
def test_new_pickles_keep_legacy_global_after_package_only_import(bare_plugin, protocol):
    code = _imports(bare_plugin, "package-only") + _POSITION_CHECKS + f"""
import io
import pickle

class RecordingUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        references.append((module, name))
        return super().find_class(module, name)

cls = package.SentencePosition
value = cls('Synthetic sentence.', 2, 1, 3, False, False)
for original in (cls, value):
    payload = pickle.dumps(original, protocol={protocol})
    references = []
    restored = RecordingUnpickler(io.BytesIO(payload)).load()
    # find_class handles both GLOBAL and STACK_GLOBAL, including the stdlib
    # reconstructors used by protocols 0/1; no duplicate position class needed.
    assert ('paragraph_parser', 'SentencePosition') in references, references
    assert not any(module.startswith('setec.') for module, name in references), references
    if original is cls:
        assert restored is cls
    else:
        check_position(cls, restored)
assert sys.modules['paragraph_parser'] is package
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_copied_launcher_direct_execution_is_silent(bare_plugin):
    result = _run(bare_plugin, bare_plugin / "scripts" / "paragraph_parser.py")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_detached_runpy_is_silent_and_preserves_main(bare_plugin):
    # No scripts-root insertion: only the copied launcher's own bootstrap can
    # make the package available from the unrelated working directory.
    code = f"""
import runpy
import sys
main = sys.modules['__main__']
runpy.run_path({str(bare_plugin / 'scripts' / 'paragraph_parser.py')!r}, run_name='__main__')
assert sys.modules['__main__'] is main
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_anchor_ratchet_accepts_real_bootstrap_but_refuses_unrelated_additions(monkeypatch):
    checker = runpy.run_path(str(PLUGIN_ROOT.parents[1] / "tools" / "check_packaging_migration.py"))
    path = "plugins/setec-voiceprint/scripts/paragraph_parser.py"
    bootstrap = next(
        row for row in checker["load_exemptions"]()
        if row["path"] == path and row["symbol"] == "_SCRIPT_DIR"
    )
    anchors = checker["find_anchors_in_file"](PLUGIN_ROOT / "scripts" / "paragraph_parser.py")
    assert checker["check_ghost_rows"]([bootstrap], anchors) == []
    assert checker["check_ghost_rows"]([bootstrap], [])

    # Reuse the checker's existing candidate/base inputs. This tests the
    # exact-path exception's behavior, without a second anchor inventory.
    ratchet = checker["check_ratchet"]
    candidate = [bootstrap]
    monkeypatch.setitem(ratchet.__globals__, "_exemptions_file_at", lambda base: [])
    monkeypatch.setitem(ratchet.__globals__, "load_exemptions", lambda: candidate)
    assert ratchet("base") == []

    for new_path, symbol in (
        (path, "UNREVIEWED_ANCHOR"),
        ("plugins/setec-voiceprint/scripts/new_audit.py", "_SCRIPT_DIR"),
        ("plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py", "_SCRIPT_DIR"),
    ):
        candidate.append(dict(bootstrap, path=new_path, symbol=symbol))
        problems = ratchet("base")
        assert any(new_path in problem and symbol in problem for problem in problems), problems
        candidate.pop()
