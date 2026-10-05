"""Closed registry structure and ownership-only compatibility contracts."""
from types import MappingProxyType

import pytest

from setec.core import textprims


def test_registry_maps_and_nested_rows_are_immutable():
    for name in (
        "TOKENIZERS", "SENTENCE_SPLITTERS", "PARAGRAPH_SPLITTERS",
        "FUNCTION_WORD_SETS", "QUANTILES", "FINGERPRINTS", "PREPROCESSORS",
    ):
        registry = getattr(textprims, name)
        assert isinstance(registry, MappingProxyType)
        with pytest.raises(TypeError):
            registry["unregistered"] = {}
        for row in registry.values():
            with pytest.raises(TypeError):
                row["id"] = "changed"
            assert isinstance(row["allowed_backends"], tuple)


def test_frozen_tokenizer_registry_reexports_the_native_final_owner():
    from setec.core import passage_tokenizer_v1
    assert textprims.tokenize is passage_tokenizer_v1.tokenize
    row = textprims.TOKENIZERS["tokenize"]
    assert row["implementation_ref"].endswith("setec/core/passage_tokenizer_v1.py:tokenize")
    assert row["unicode_normalization"] == "frozen_table"


def test_function_word_import_does_not_load_plugin_dependent_tokenizer(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    scripts = tmp_path / "scripts"
    core = scripts / "setec/core"
    core.mkdir(parents=True)
    shutil.copyfile(Path(textprims.__file__), core / "textprims.py")
    # No marker, tokenizer module or data is present: pure table imports must work.
    code = "import sys; sys.path.insert(0, sys.argv[1]); from setec.core.textprims import FUNCTION_WORDS; assert 'and' in FUNCTION_WORDS; assert 'setec.core.passage_tokenizer_v1' not in sys.modules"
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(scripts)], cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
