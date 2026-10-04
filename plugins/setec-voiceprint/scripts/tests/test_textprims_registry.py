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


def test_splitter_rows_bind_distinct_final_owners_and_closed_fields():
    rows = textprims.SENTENCE_SPLITTERS
    assert set(rows) == {"split_sentences_punkt", "split_sentences_regex"}
    expected = {
        "id", "family", "implementation_ref", "pattern_sha256", "case_policy",
        "unicode_normalization", "allowed_backends", "behavior_sha256",
    }
    assert len({row["id"] for row in rows.values()}) == len(rows)
    for symbol, row in rows.items():
        assert set(row) == expected
        assert row["implementation_ref"].endswith("setec/core/textprims.py:" + symbol)
        assert row["id"] == "sentence_splitter-" + row["behavior_sha256"][:12] + "-v1"
        assert callable(getattr(textprims, symbol))
