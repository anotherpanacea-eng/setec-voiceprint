#!/usr/bin/env python3
"""Contract tests for the locked six-family normalized S5 surface."""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s5_distance_request.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import s5_distance as s5  # type: ignore  # noqa: E402

FORBIDDEN_KEYS = frozenset({
    "threshold", "verdict", "decision", "label", "class", "classification",
    "rank", "selected", "selection", "best", "winner", "score", "is_ai", "is_human",
})


def _request() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_exact_synthetic_fixture_is_six_twos():
    results = s5.compute_s5(_request())
    assert results["family_order"] == list(s5.FAMILY_ORDER)
    assert results["family_burrows_delta"] == {family: 2.0 for family in s5.FAMILY_ORDER}
    assert results["s5_unweighted_mean"] == 2.0
    assert results["selected_feature_counts"] == {family: 1 for family in s5.FAMILY_ORDER}
    assert all(math.isfinite(value) for value in results["family_burrows_delta"].values())
    assert not (set(_walk_keys(results)) & FORBIDDEN_KEYS)


def test_envelope_echoes_all_binding_hashes():
    request = _request()
    envelope = s5._run(str(FIXTURE))
    assert envelope["available"] is True
    results = envelope["results"]
    assert results["target_content_sha256"] == request["target"]["content_sha256"]
    assert results["baseline_manifest_sha256"] == request["baseline"]["manifest_sha256"]
    assert results["baseline_content_inventory_sha256"] == request["baseline"]["content_inventory_sha256"]
    assert results["parser_inventory_sha256"] == request["parser_inventory_sha256"]
    assert results["normalized_feature_inventory_sha256"].startswith("sha256:")
    assert results["request_sha256"].startswith("sha256:")
    assert results["implementation_sha256"].startswith("sha256:")


def test_feature_inventory_digest_binds_the_values_that_determine_s5():
    request = _request()
    original = s5.compute_s5(request)
    mutated = copy.deepcopy(request)
    mutated["target"]["features"]["punctuation"]["comma"] = 10.0
    changed = s5.compute_s5(mutated)
    assert changed["target_content_sha256"] == original["target_content_sha256"]
    assert changed["s5_unweighted_mean"] != original["s5_unweighted_mean"]
    assert (
        changed["normalized_feature_inventory_sha256"]
        != original["normalized_feature_inventory_sha256"]
    )
    assert changed["request_sha256"] != original["request_sha256"]


@pytest.mark.parametrize("mutation", ["missing_family", "extra_key", "unsorted", "overlap", "nan"])
def test_exact_schema_and_disjointness_reject_mutations(mutation):
    request = copy.deepcopy(_request())
    if mutation == "missing_family":
        del request["target"]["features"]["punctuation"]
    elif mutation == "extra_key":
        request["target"]["unexpected"] = True
    elif mutation == "unsorted":
        request["baseline"]["entries"].reverse()
    elif mutation == "overlap":
        request["baseline"]["entries"][0]["content_sha256"] = request["target"]["content_sha256"]
    else:
        request["target"]["features"]["punctuation"]["comma"] = float("nan")
    with pytest.raises(ValueError):
        s5.validate_request(request)


def test_feature_caps_and_ties_are_deterministic():
    request = _request()
    entries = request["baseline"]["entries"]
    for index, item in enumerate(entries):
        item["features"]["char_ngrams_3"] = {
            f"feature-{number:03d}": float(index + 1) for number in range(205)
        }
    names = s5._selected_names(entries, "char_ngrams_3")
    assert len(names) == 200
    assert names == sorted(names)
    assert names[-1] == "feature-199"


def test_cli_import_path_has_no_parser_model_or_network_dependency():
    probe = r'''
import importlib.abc
import json
import socket
import sys

forbidden = {
    "nltk", "numpy", "pandas", "requests", "sklearn", "spacy",
    "torch", "transformers", "urllib3",
}

class BlockForbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in forbidden:
            raise AssertionError(f"forbidden import: {fullname}")
        return None

def no_network(*args, **kwargs):
    raise AssertionError("network access attempted")

sys.meta_path.insert(0, BlockForbidden())
socket.socket = no_network
socket.create_connection = no_network
sys.path.insert(0, sys.argv[1])
import s5_distance

envelope = s5_distance._run(sys.argv[2])
assert envelope["available"] is True
assert envelope["results"]["s5_unweighted_mean"] == 2.0
assert forbidden.isdisjoint(name.partition(".")[0] for name in sys.modules)
'''
    proc = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(SCRIPTS), str(FIXTURE)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr


def test_huge_integer_is_a_normalized_bad_input(tmp_path):
    request = _request()
    request["target"]["features"]["punctuation"]["comma"] = 10**1000
    request_path = tmp_path / "huge-int.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    envelope = s5._run(str(request_path))

    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"


def test_duplicate_json_key_is_a_normalized_bad_input(tmp_path):
    raw = FIXTURE.read_text(encoding="utf-8")
    raw = raw.replace(
        '"parser_inventory_sha256":',
        '"parser_inventory_sha256": "sha256:' + 'e' * 64 + '",\n  '
        '"parser_inventory_sha256":',
        1,
    )
    request_path = tmp_path / "duplicate-key.json"
    request_path.write_text(raw, encoding="utf-8")
    envelope = s5._run(str(request_path))
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"
    assert "duplicate JSON key" in envelope["reason"]
    assert s5.main([str(request_path), "--json"]) == 0


def test_family_mean_overflow_refuses_closed(monkeypatch):
    monkeypatch.setattr(
        s5, "family_distance",
        lambda *_args, **_kwargs: {"burrows_delta": 1e308},
    )
    with pytest.raises(ValueError, match="non-finite S5 unweighted mean"):
        s5.compute_s5(_request())


def test_dispatcher_preserves_structured_bad_input_envelope(tmp_path, capsys):
    import setec_run  # type: ignore

    missing = tmp_path / "missing.json"
    rc = setec_run.dispatch(
        "s5_distance", [str(missing)], observed_version="1.128.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"


def test_stylometry_core_reexports_the_same_primitive(monkeypatch):
    import stylometry_distance as pure  # type: ignore

    # Avoid any real parser/tokenizer dependency while proving the compatibility export.
    class FakeVariance:
        FUNCTION_WORDS = set()
        HAS_SPACY = False
        _NLP = None

        @staticmethod
        def split_sentences(_text):
            return []

        @staticmethod
        def split_words(_text):
            return []

    monkeypatch.setitem(sys.modules, "variance_audit", FakeVariance)
    sys.modules.pop("stylometry_core", None)
    try:
        import stylometry_core as core  # type: ignore

        assert core.family_distance is pure.family_distance
    finally:
        sys.modules.pop("stylometry_core", None)
