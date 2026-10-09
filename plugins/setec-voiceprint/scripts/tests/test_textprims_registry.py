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
    # The registry's direct verbatim-cover and paragraph-parser imports are pure stdlib and travel with it.
    for owner in ("verbatim_cover.py", "paragraph_parser.py"):
        shutil.copyfile(Path(textprims.__file__).with_name(owner), core / owner)
    # No marker, tokenizer module or data is present: pure table imports must work.
    code = "import sys; sys.path.insert(0, sys.argv[1]); from setec.core.textprims import FUNCTION_WORDS; assert 'and' in FUNCTION_WORDS; assert 'setec.core.passage_tokenizer_v1' not in sys.modules"
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(scripts)], cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_verbatim_cover_registry_reexports_the_owner_objects():
    import hashlib
    from setec.core import verbatim_cover
    from setec.surfaces import originality_audit
    for symbol, registry in (("_tokens", textprims.TOKENIZERS), ("_content_fingerprint", textprims.FINGERPRINTS)):
        owner = getattr(verbatim_cover, symbol)
        assert getattr(textprims, symbol) is owner
        assert getattr(originality_audit, symbol) is owner
        row = registry[symbol]
        assert row["implementation_ref"] == "plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:" + symbol
        assert (row["case_policy"], row["unicode_normalization"], row["allowed_backends"]) == ("lower", "none", ())
    assert textprims.TOKENIZERS["_tokens"]["pattern_sha256"] == hashlib.sha256(verbatim_cover._TOKEN.pattern.encode("utf-8")).hexdigest()
    assert textprims.FINGERPRINTS["_content_fingerprint"]["pattern_sha256"] is None


def test_paragraph_parser_registry_reexports_the_owner_objects():
    import hashlib
    import paragraph_parser as launcher
    from setec.core import paragraph_parser
    for symbol, registry, pattern in (("split_paragraphs", textprims.PARAGRAPH_SPLITTERS, paragraph_parser._PARAGRAPH_SPLIT), ("split_sentences", textprims.SENTENCE_SPLITTERS, paragraph_parser._SENTENCE_END)):
        owner = getattr(paragraph_parser, symbol)
        assert getattr(textprims, symbol) is owner
        assert getattr(launcher, symbol) is owner
        row = registry[symbol]
        assert row["implementation_ref"] == "plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py:" + symbol
        assert (row["case_policy"], row["unicode_normalization"], row["allowed_backends"]) == ("preserve", "none", ())
        assert row["pattern_sha256"] == hashlib.sha256(pattern.pattern.encode("utf-8")).hexdigest()


def test_preflight_analysis_is_lazy_and_importing_the_registry_does_not_load_preflight():
    import subprocess
    import sys
    from pathlib import Path
    scripts = Path(textprims.__file__).parents[2]
    code = "import sys; sys.path.insert(0, sys.argv[1]); from setec.core import textprims; assert not [m for m in sys.modules if m.startswith('setec.preflight')]; from setec.preflight import common; assert textprims._analysis is common._analysis"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(scripts)], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
