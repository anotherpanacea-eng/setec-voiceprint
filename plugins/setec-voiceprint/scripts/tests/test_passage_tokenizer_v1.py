"""Focused checks for the frozen UCD-backed Spec-80 tokenizer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import passage_tokenizer_v1 as tokenizer  # noqa: E402


def test_official_table_shape_and_commitment():
    ranges, mappings, data = tokenizer.load_data()
    assert len(ranges) == 749
    assert len(mappings) == 1433
    assert data["data_commitment_sha256"] == (
        "sha256:c5d149e02b103b896d4c928efa0ea7383f1bfbe9cd536ddb71bcbc8c98885002"
    )


def test_non_ascii_letters_and_full_lower_mapping():
    assert tokenizer.tokenize("É") == [
        {"char_start": 0, "char_end": 1, "normalized_token": "é"}
    ]
    assert tokenizer.tokenize("İ") == [
        {"char_start": 0, "char_end": 1, "normalized_token": "i\u0307"}
    ]
    assert tokenizer.tokenize("\U00010400") == [
        {"char_start": 0, "char_end": 1, "normalized_token": "\U00010428"}
    ]


def test_numeric_fields_underscore_offsets_and_no_normalization():
    assert tokenizer.tokenize("A_É²①!") == [
        {"char_start": 0, "char_end": 5, "normalized_token": "a_é²①"}
    ]
    assert tokenizer.tokenize("e\u0301") == [
        {"char_start": 0, "char_end": 1, "normalized_token": "e"}
    ]
    assert tokenizer.tokenize("É") != tokenizer.tokenize("E\u0301")


def test_unassigned_and_combining_only_are_not_words():
    assert tokenizer.tokenize("\u0378") == []
    assert tokenizer.tokenize("\u0301") == []


def test_commitment_mutation_refuses(tmp_path):
    data = json.loads(tokenizer.DATA_FILE.read_text(encoding="utf-8"))
    data["word_ranges"][0]["end"] += 1
    path = tmp_path / "mutated.json"
    path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(tokenizer.TokenizerDataError, match="commitment mismatch"):
        tokenizer.load_data(path)


@pytest.mark.parametrize("variant", ["pretty", "extra_lf", "duplicate_key"])
def test_noncanonical_serializations_refuse(tmp_path, variant):
    raw = tokenizer.DATA_FILE.read_bytes()
    data = json.loads(raw)
    if variant == "pretty":
        mutated = (
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
    elif variant == "extra_lf":
        mutated = raw + b"\n"
    else:
        mutated = raw.replace(
            b"{",
            b'{"schema":"setec-frozen-unicode-word-lower-data/1",',
            1,
        )
    path = tmp_path / f"{variant}.json"
    path.write_bytes(mutated)
    with pytest.raises(
        tokenizer.TokenizerDataError,
        match="strict canonical JSON",
    ):
        tokenizer.load_data(path)
