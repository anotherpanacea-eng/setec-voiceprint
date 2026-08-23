from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("issue396_controller", ROOT / "controller.py")
assert SPEC and SPEC.loader
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


def test_canonical_json_rejects_nonfinite_and_is_stable():
    assert controller.canonical_json_bytes({"b": 1, "a": "é"}) == (
        '{"a":"é","b":1}\n'.encode("utf-8")
    )
    try:
        controller.canonical_json_bytes({"x": float("nan")})
    except ValueError as exc:
        assert "non-finite" in str(exc)
    else:
        raise AssertionError("NaN was accepted")


def test_accuracy_corpus_and_exact_oracle_are_deterministic():
    first = controller.accuracy_records()
    second = controller.accuracy_records()
    assert first == second
    assert len(first) == 200
    assert len([key for key in first if key.startswith("public-")]) == 6
    truth, scores = controller.exact_pair_oracle(first)
    assert len(scores) == 19_900
    assert len(truth) == 70
    assert ("exact-00-a", "exact-00-b") in truth
    assert ("below-00-a", "below-00-b") not in truth
    assert ("chain-00-a", "chain-00-b") in truth
    assert ("chain-00-b", "chain-00-c") in truth
    assert ("chain-00-a", "chain-00-c") not in truth


def test_exact_oracle_calls_shipped_shingle_semantics():
    left = "Straße one two three four"
    right = "STRASSE one two three four"
    assert controller.shingles(left) == controller.ndd.shingles(left)
    assert controller.shingles(right) == controller.ndd.shingles(right)
    truth, scores = controller.exact_pair_oracle({"left": left, "right": right})
    assert scores[("left", "right")] == 0.0
    assert not truth


def test_scale_corpora_use_exact_production_token_count():
    public_tokens = {
        token
        for text in controller.public_fixture_documents().values()
        for token in controller.tokens(text)
    }
    for total, families in ((1_000, 50), (5_000, 250)):
        records, known = controller.scale_records(total, families)
        assert len(records) == total
        assert len(known) == families
        assert all(len(controller.tokens(text)) == 1_000 for text in records.values())
        assert all(
            any(token in public_tokens for token in controller.tokens(text))
            for text in records.values()
        )


def test_fixture_manifest_hashes_and_licenses():
    manifest = controller.load_fixture_manifest()
    assert len(manifest["files"]) >= 8
    assert all(row["license"] for row in manifest["files"])


def _worker_result(*, dropped: list[str]) -> dict:
    kept = [record_id for record_id in ("a", "b", "z") if record_id not in dropped]
    return {
        "layers": {
            "raw_lsh": [],
            "estimated_pass": [],
            "exact_confirmed": [],
            "co_cluster": [],
        },
        "dedup_result": {
            "kept": kept,
            "dropped": dropped,
            "clusters": {},
        },
        "traced_matches_control": True,
    }


def test_destructive_safety_separates_false_drops_from_candidate_misses():
    duplicate = "one two three four five six"
    records = {"a": duplicate, "b": duplicate, "z": "red blue green gold black white"}

    unsafe = controller.evaluate_worker_result(
        records, _worker_result(dropped=["z"]), threshold=0.85,
    )
    assert unsafe["destructive_safety"] == {
        "false_positive_drops": ["z"],
        "retained_oracle_drops": ["b"],
        "zero_false_positive_drops": False,
    }

    candidate_miss = controller.evaluate_worker_result(
        records, _worker_result(dropped=[]), threshold=0.85,
    )
    assert candidate_miss["destructive_safety"] == {
        "false_positive_drops": [],
        "retained_oracle_drops": ["b"],
        "zero_false_positive_drops": True,
    }
