"""Synthetic confidentiality and complete cross-set matching regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from setec.preflight.common import Refusal, canonical_json, load_manifest, plain_hash
from setec.preflight.holdout_firewall import run
from setec.preflight.holdout_core import (
    CEILINGS, SealedSet, holdout_firewall, load_holdout_conflicts,
    load_holdout_detail, load_holdout_policy, load_holdout_receipt,
)
from setec.preflight.common import WorkBudget


def _manifest(root: Path, texts: list[str], *, prefix: str) -> Path:
    root.mkdir(parents=True)
    rows = []
    for index, text in enumerate(texts):
        name = f"{prefix}-{index}.txt"
        data = text.encode()
        (root / name).write_bytes(data)
        rows.append({"id": f"{prefix}{index}", "group_id": f"g{index}",
                     "stratum": "synthetic", "path": name,
                     "span": {"source_path": name, "source_bytes_sha256": plain_hash(data),
                              "start_byte": 0, "end_byte": len(data)}})
    path = root / "packet.jsonl"
    path.write_bytes(b"".join(canonical_json(row) for row in rows))
    return path


def _span_manifest(root: Path, text: str, source: bytes, start: int, end: int,
                   *, prefix: str) -> Path:
    root.mkdir(parents=True)
    (root / "excerpt.txt").write_bytes(text.encode())
    (root / "source.txt").write_bytes(source)
    row = {"id": prefix, "group_id": "g", "stratum": "synthetic",
           "path": "excerpt.txt",
           "span": {"source_path": "source.txt",
                    "source_bytes_sha256": plain_hash(source),
                    "start_byte": start, "end_byte": end}}
    path = root / "packet.jsonl"
    path.write_bytes(canonical_json(row))
    return path


def _policy(path: Path, *, floor: int = 1, shared_run: bool = True,
            candidate_containment: dict | None = None,
            sealed_containment: dict | None = None, ngram: int = 2) -> Path:
    policy = path / "policy.json"
    policy.write_bytes(canonical_json({
        "schema": "setec-preflight-holdout-policy/1", "ngram": ngram,
        "shared_run": shared_run, "candidate_containment": candidate_containment,
        "sealed_containment": sealed_containment, "min_sealed_distinct": floor,
    }))
    return policy


def _run(tmp_path: Path, candidates: list[str], sealed: list[str],
         *, floor: int = 1, **policy_args):
    candidate_path = _manifest(tmp_path / "candidate", candidates, prefix="c")
    sealed_path = _manifest(tmp_path / "sealed", sealed, prefix="s")
    policy = _policy(tmp_path, floor=floor, **policy_args)
    private = tmp_path / "private"
    conflicts = tmp_path / "conflicts"
    statuses, released = run(candidate_path, [("private-set", sealed_path)], policy,
                             private, conflicts)
    detail = json.loads((private / "detail.json").read_bytes())
    receipt = json.loads((private / "receipt.json").read_bytes())
    generator = json.loads((conflicts / "conflicts.json").read_bytes()) if released else None
    return detail, receipt, generator, statuses, candidate_path, private, conflicts


def test_exact_conflict_and_private_pairwise_detail(tmp_path):
    detail, receipt, generator, statuses, candidate_path, private, conflicts = _run(
        tmp_path, ["shared source words", "different candidate"], ["shared source words"])
    assert statuses["exact"] == "failed"
    assert detail["pairs"][0]["classes"] == ["declared_span", "exact", "shared_run"]
    assert generator["conflicts"] == [{"candidate_id": "c0",
                                      "conflict_class": "holdout_conflict", "severity": "remove"}]
    assert "private-set" not in (conflicts / "conflicts.json").read_text()
    assert receipt["conflicts_sha256"] == plain_hash((conflicts / "conflicts.json").read_bytes())
    assert load_holdout_detail(private / "detail.json", receipt["detail_sha256"]) == detail
    assert load_holdout_receipt(private / "receipt.json",
                                plain_hash((private / "receipt.json").read_bytes())) == receipt
    assert load_holdout_conflicts(conflicts / "conflicts.json",
                                  load_manifest(candidate_path)) == generator


@pytest.mark.parametrize("alias_first", [True, False])
def test_confinement_outranks_alias_across_manifests(tmp_path, alias_first):
    manifests = [_manifest(tmp_path / name, ["alpha beta gamma"], prefix=name)
                 for name in ("candidate", "sealed")]
    alias, missing = manifests if alias_first else manifests[::-1]
    row = json.loads(alias.read_bytes())
    os.link(alias.parent / row["path"], alias.parent / "alias.txt")
    row["span"]["source_path"] = "alias.txt"
    alias.write_bytes(canonical_json(row))
    row = json.loads(missing.read_bytes())
    row["path"] = "missing.txt"
    missing.write_bytes(canonical_json(row))
    with pytest.raises(Refusal) as caught:
        run(manifests[0], [("sealed", manifests[1])], _policy(tmp_path),
            tmp_path / "private", tmp_path / "conflicts")
    assert caught.value.code == "path_confinement"


def test_exact_analysis_normalizes_crlf_and_nfc(tmp_path):
    detail, receipt, _, statuses, *_ = _run(
        tmp_path, ["café\nnext"], ["cafe\u0301\r\nnext"])
    assert "exact" in detail["pairs"][0]["classes"]
    assert statuses["exact"] == "failed"
    assert receipt["reason_counts"]["exact_conflict"] == 1


def test_release_floor_withholds_even_without_conflicts(tmp_path):
    _, receipt, generator, statuses, _, _, conflicts = _run(
        tmp_path, ["candidate prose"], ["unrelated sealed prose"], floor=2)
    assert generator is None
    assert not conflicts.exists()
    assert receipt["release"] == "withheld"
    assert receipt["conflicts_sha256"] is None
    assert statuses["release"] == "needs_human_review"


def test_generator_bytes_depend_only_on_candidate_flag_set(tmp_path):
    first = _run(tmp_path / "a", ["common shared passage", "free candidate"],
                 ["common shared passage"])[2]
    second = _run(tmp_path / "b", ["common shared passage", "free candidate"],
                  ["common shared passage", "very different sealed text"],
                  ngram=3)[2]
    assert canonical_json(first) == canonical_json(second)


def test_containment_direction_and_shared_run_switch(tmp_path):
    detail, receipt, _, statuses, *_ = _run(
        tmp_path, ["alpha beta gamma"], ["prefix alpha beta gamma suffix"],
        shared_run=False,
        candidate_containment={"numerator": 1, "denominator": 1},
        sealed_containment={"numerator": 1, "denominator": 1})
    assert detail["pairs"][0]["classes"] == ["candidate_contained"]
    assert statuses["ngram"] == "failed"
    assert receipt["reason_counts"]["ngram_conflict"] == 1


def test_shared_thirty_token_run_and_twenty_nine_miss(tmp_path):
    common = " ".join(f"word{i}" for i in range(30))
    detail, _, _, _, *_ = _run(tmp_path / "thirty", ["candidate " + common],
                               [common + " sealed"], ngram=30)
    assert "shared_run" in detail["pairs"][0]["classes"]
    short = " ".join(f"word{i}" for i in range(29))
    detail, receipt, _, _, *_ = _run(tmp_path / "twentynine",
                                     ["candidate " + short], [short + " sealed"],
                                     ngram=30)
    assert detail["pairs"] == []
    assert receipt["reason_counts"]["ngram_conflict"] == 0
    detail, _, _, _, *_ = _run(
        tmp_path / "disabled", ["candidate " + common], [common + " sealed"],
        ngram=30, shared_run=False,
        candidate_containment={"numerator": 1, "denominator": 2})
    assert "shared_run" not in detail["pairs"][0]["classes"]


@pytest.mark.parametrize("numerator,denominator,expected", [(1, 2, True),
                                                              (2, 3, True),
                                                              (3, 4, False)])
def test_integer_containment_threshold_boundary(tmp_path, numerator, denominator, expected):
    detail, _, _, _, *_ = _run(
        tmp_path, ["a b c d"], ["a b c z"], shared_run=False,
        candidate_containment={"numerator": numerator, "denominator": denominator})
    assert bool(detail["pairs"]) is expected


def test_reverse_containment_direction(tmp_path):
    detail, _, _, _, *_ = _run(
        tmp_path, ["prefix alpha beta gamma suffix"], ["alpha beta gamma"],
        shared_run=False,
        sealed_containment={"numerator": 1, "denominator": 1})
    assert detail["pairs"][0]["classes"] == ["sealed_contained"]


@pytest.mark.parametrize("sealed_start,sealed_source,expected", [
    (5, b"0123456789", False),
    (4, b"0123456789", True),
    (4, b"different source", False),
])
def test_declared_span_boundary_and_source_identity(tmp_path, sealed_start,
                                                    sealed_source, expected):
    candidate = _span_manifest(tmp_path / "candidate", "red green",
                               b"0123456789", 0, 5, prefix="c")
    sealed = _span_manifest(tmp_path / "sealed", "blue yellow", sealed_source,
                            sealed_start, 10, prefix="s")
    policy = _policy(tmp_path)
    private = tmp_path / "private"
    run(candidate, [("sealed", sealed)], policy, private, tmp_path / "conflicts")
    detail = json.loads((private / "detail.json").read_bytes())
    assert bool(detail["pairs"]) is expected
    if expected:
        assert detail["pairs"][0]["classes"] == ["declared_span"]


@pytest.mark.parametrize("floor,has_conflict,expected_release", [
    (2, True, "released"), (3, True, "withheld"),
    (2, False, "released"), (3, False, "withheld"),
])
def test_sealed_distinct_floor_boundary(tmp_path, floor, has_conflict,
                                        expected_release):
    candidate = ["shared prose"] if has_conflict else ["unrelated candidate"]
    _, receipt, generator, statuses, *_ = _run(
        tmp_path, candidate, ["shared prose", "different sealed"], floor=floor)
    assert receipt["release"] == expected_release
    assert (generator is not None) == (expected_release == "released")
    assert statuses["release"] == ("passed" if expected_release == "released"
                                   else "needs_human_review")


@pytest.mark.parametrize("bad", [
    {"ngram": True}, {"ngram": 65},
    {"shared_run": False, "candidate_containment": None, "sealed_containment": None},
    {"candidate_containment": {"numerator": 2, "denominator": 1}},
])
def test_invalid_policy_refuses_before_publication(tmp_path, bad):
    candidate = _manifest(tmp_path / "candidate", ["alpha beta"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["alpha beta"], prefix="s")
    policy = _policy(tmp_path)
    value = json.loads(policy.read_bytes())
    value.update(bad)
    policy.write_bytes(canonical_json(value))
    with pytest.raises(Refusal, match="policy_contract"):
        run(candidate, [("holdout", sealed)], policy,
            tmp_path / "private", tmp_path / "conflicts")
    assert not (tmp_path / "private").exists()


@pytest.mark.parametrize("edit", ["unknown", "missing"])
def test_policy_key_set_is_exact(tmp_path, edit):
    path = _policy(tmp_path)
    value = json.loads(path.read_bytes())
    if edit == "unknown":
        value["unknown"] = 1
    else:
        del value["ngram"]
    path.write_bytes(canonical_json(value))
    with pytest.raises(Refusal, match="policy_contract"):
        load_holdout_policy(path)


@pytest.mark.parametrize("count,duplicate", [(2, True), (9, False)])
def test_sealed_count_and_label_uniqueness(tmp_path, count, duplicate):
    candidate = _manifest(tmp_path / "candidate", ["candidate words"], prefix="c")
    sealed = [(("same" if duplicate else f"s{i}"),
               _manifest(tmp_path / f"sealed{i}", [f"sealed words {i}"], prefix=f"s{i}"))
              for i in range(count)]
    with pytest.raises(Refusal, match="holdout_contract"):
        run(candidate, sealed, _policy(tmp_path), tmp_path / "private",
            tmp_path / "conflicts")
    assert not (tmp_path / "private").exists()


def test_each_work_counter_limit_and_one_over(tmp_path):
    candidate = load_manifest(_manifest(tmp_path / "candidate", ["shared words"], prefix="c"))
    sealed = SealedSet("sealed", load_manifest(
        _manifest(tmp_path / "sealed", ["shared words"], prefix="s")))
    policy, _ = load_holdout_policy(_policy(tmp_path))
    measured = WorkBudget(CEILINGS)
    holdout_firewall(candidate, (sealed,), policy, measured)
    assert all(value > 0 for value in measured.used.values())
    holdout_firewall(candidate, (sealed,), policy, WorkBudget(measured.used))
    for name, value in measured.used.items():
        ceilings = dict(CEILINGS)
        ceilings[name] = value - 1
        with pytest.raises(Refusal, match="work_limit"):
            holdout_firewall(candidate, (sealed,), policy, WorkBudget(ceilings))


def test_changed_candidate_rejected_by_conflicts_loader(tmp_path):
    _, _, _, _, candidate_path, _, conflicts = _run(
        tmp_path, ["shared candidate"], ["shared candidate"])
    (candidate_path.parent / "c-0.txt").write_bytes(b"revised candidate")
    row = json.loads(candidate_path.read_bytes())
    row["span"]["source_bytes_sha256"] = plain_hash(b"revised candidate")
    row["span"]["end_byte"] = len(b"revised candidate")
    candidate_path.write_bytes(canonical_json(row))
    with pytest.raises(Refusal, match="receipt_contract"):
        load_holdout_conflicts(conflicts / "conflicts.json", load_manifest(candidate_path))


def test_changed_candidate_with_unchanged_manifest_rejected(tmp_path):
    source = b"source bytes used only for declared span"
    candidate = _span_manifest(tmp_path / "candidate", "shared passage", source,
                               0, 5, prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["shared passage"], prefix="s")
    policy = _policy(tmp_path)
    conflicts = tmp_path / "conflicts"
    run(candidate, [("sealed", sealed)], policy, tmp_path / "private", conflicts)
    (candidate.parent / "excerpt.txt").write_text("changed passage")
    with pytest.raises(Refusal, match="receipt_contract"):
        load_holdout_conflicts(conflicts / "conflicts.json", load_manifest(candidate))


def test_sealed_floor_counts_distinct_text_and_union(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["shared prose", "other candidate"], prefix="c")
    first = _manifest(tmp_path / "first", ["shared prose", "shared prose"], prefix="s")
    second = _manifest(tmp_path / "second", ["second sealed", "third sealed"], prefix="t")
    policy = _policy(tmp_path, floor=2)
    private = tmp_path / "private"
    conflicts = tmp_path / "conflicts"
    statuses, released = run(candidate, [("a", first), ("b", second)], policy,
                             private, conflicts)
    assert not released and not conflicts.exists()
    assert statuses["release"] == "needs_human_review"
    receipt = json.loads((private / "receipt.json").read_bytes())
    assert receipt["reason_counts"]["sealed_below_floor"] == 1
    assert receipt["sealed"][0]["distinct_record_count"] == 1


def test_output_collision_leaves_both_untouched(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    conflicts = tmp_path / "conflicts"
    conflicts.mkdir()
    sentinel = conflicts / "do-not-touch"
    sentinel.write_text("untouched")
    with pytest.raises(Refusal, match="output_collision"):
        run(candidate, [("s", sealed)], policy, tmp_path / "private", conflicts)
    assert not (tmp_path / "private").exists()
    assert sentinel.read_text() == "untouched"


def test_private_output_collision_leaves_both_untouched(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    sentinel = private / "do-not-touch"
    sentinel.write_text("untouched")
    with pytest.raises(Refusal, match="output_collision"):
        run(candidate, [("s", sealed)], policy, private, tmp_path / "conflicts")
    assert sentinel.read_text() == "untouched"
    assert not (tmp_path / "conflicts").exists()


def test_fresh_process_bytes_and_sealed_canary_streams(tmp_path):
    canary = "secretraven731"
    candidate = _manifest(tmp_path / "candidate", ["shared prose"], prefix="c")
    sealed_root = tmp_path / canary
    sealed = _manifest(sealed_root, ["shared prose", canary], prefix=canary)
    policy = _policy(tmp_path, floor=1)
    command = [sys.executable, "-m", "setec.preflight.holdout_firewall",
               "--candidate-manifest", str(candidate), "--sealed", canary, str(sealed),
               "--policy", str(policy)]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[1])
    products = []
    for index in range(2):
        private = tmp_path / f"private{index}"
        conflicts = tmp_path / f"conflicts{index}"
        result = subprocess.run(command + ["--private-out", str(private),
                                         "--conflicts-out", str(conflicts)],
                                capture_output=True, env=env, check=False)
        assert result.returncode == 0
        assert result.stdout == b""
        assert result.stderr.splitlines() == [b"intake", b"exact", b"ngram",
                                              b"span", b"release"]
        assert canary.encode() not in result.stderr
        assert canary.encode() not in (conflicts / "conflicts.json").read_bytes()
        products.append(((private / "detail.json").read_bytes(),
                         (private / "receipt.json").read_bytes(),
                         (conflicts / "conflicts.json").read_bytes()))
    assert products[0] == products[1]


def test_manifest_row_permutation_keeps_record_set_identity(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["shared prose", "other prose"],
                          prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["shared prose"], prefix="s")
    policy = _policy(tmp_path)
    outputs = []
    for index in range(2):
        private = tmp_path / f"private{index}"
        conflicts = tmp_path / f"conflicts{index}"
        run(candidate, [("s", sealed)], policy, private, conflicts)
        outputs.append((json.loads((private / "receipt.json").read_bytes()),
                        json.loads((conflicts / "conflicts.json").read_bytes())))
        if index == 0:
            candidate.write_bytes(b"".join(reversed(candidate.read_bytes().splitlines(keepends=True))))
    first_receipt, first_conflicts = outputs[0]
    second_receipt, second_conflicts = outputs[1]
    assert first_receipt["record_set_sha256"] == second_receipt["record_set_sha256"]
    assert first_conflicts["record_set_sha256"] == second_conflicts["record_set_sha256"]
    assert first_receipt["candidate_manifest_sha256"] != second_receipt["candidate_manifest_sha256"]
    assert first_conflicts["candidate_manifest_sha256"] != second_conflicts["candidate_manifest_sha256"]


def test_root_nesting_and_duplicate_sealed_identity_refuse(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    nested = _manifest(candidate.parent / "nested", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    with pytest.raises(Refusal, match="path_confinement"):
        run(candidate, [("s", nested)], policy,
            tmp_path / "private", tmp_path / "conflicts")
    sealed1 = _manifest(tmp_path / "sealed1", ["same source"], prefix="s")
    sealed2 = _manifest(tmp_path / "sealed2", ["same source"], prefix="s")
    with pytest.raises(Refusal, match="holdout_contract"):
        run(candidate, [("one", sealed1), ("two", sealed2)], policy,
            tmp_path / "private", tmp_path / "conflicts")
    assert not (tmp_path / "private").exists()


def test_second_publication_failure_keeps_private_receipt(tmp_path, monkeypatch):
    import importlib

    command = importlib.import_module("setec.preflight.holdout_firewall")
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    actual = command.publish_bundle
    calls = 0

    def fail_second(dest, files):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise Refusal("output_unavailable")
        return actual(dest, files)

    monkeypatch.setattr(command, "publish_bundle", fail_second)
    with pytest.raises(Refusal, match="output_unavailable"):
        run(candidate, [("s", sealed)], policy,
            tmp_path / "private", tmp_path / "conflicts")
    assert (tmp_path / "private" / "receipt.json").is_file()
    assert not (tmp_path / "conflicts").exists()
    receipt = json.loads((tmp_path / "private" / "receipt.json").read_bytes())
    assert receipt["conflicts_sha256"] is not None


def test_seeded_cross_pair_shared_gram_enumeration(tmp_path):
    from setec.preflight.overlap_core import preflight_word_ngrams_v1

    rng = random.Random(7123)
    vocabulary = ("alpha", "beta", "gamma", "delta", "epsilon")
    candidate_texts = [" ".join(rng.choices(vocabulary, k=5)) for _ in range(6)]
    sealed_texts = [" ".join(rng.choices(vocabulary, k=5)) for _ in range(7)]
    detail, _, _, _, *_ = _run(tmp_path, candidate_texts, sealed_texts)
    actual = {(row["candidate_id"], row["sealed_id"]): row["shared_grams"]
              for row in detail["pairs"]}
    expected = {}
    for c_index, candidate in enumerate(candidate_texts):
        c_grams = preflight_word_ngrams_v1(candidate, 2)
        for s_index, sealed in enumerate(sealed_texts):
            shared = len(c_grams & preflight_word_ngrams_v1(sealed, 2))
            if shared:
                expected[(f"c{c_index}", f"s{s_index}")] = shared
    assert actual == expected


@pytest.mark.parametrize("mutation", [
    lambda detail: detail["pairs"][0].update(shared_grams=999),
    lambda detail: detail["pairs"][0].update(shared_grams=0),
    lambda detail: detail["sealed_records"][0].update(analysis_sha256="0" * 64),
    # A run refuses two sealed manifests with equal hashes (spec 04 section 5).
    lambda detail: detail["inputs"]["sealed_manifests"].append(
        {**detail["inputs"]["sealed_manifests"][0], "label": "zz-repeat"}),
])
def test_private_detail_reloader_checks_pair_evidence(tmp_path, mutation):
    detail, _, _, _, _, _, _ = _run(tmp_path, ["same shared words"],
                                    ["same shared words"])
    mutation(detail)
    path = tmp_path / "forged-detail.json"
    data = canonical_json(detail)
    path.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_holdout_detail(path, plain_hash(data))


@pytest.mark.parametrize("artifact", ["detail", "receipt", "conflicts"])
def test_nonfinite_json_number_refuses_with_contract_code(tmp_path, artifact):
    _, receipt, _, _, candidate_path, private, conflicts = _run(
        tmp_path, ["same shared words"], ["same shared words"])
    source = {"detail": private / "detail.json", "receipt": private / "receipt.json",
              "conflicts": conflicts / "conflicts.json"}[artifact]
    raw = source.read_bytes().replace(b'"tool_version":1', b'"tool_version":1e999')
    path = tmp_path / f"nonfinite-{artifact}.json"
    path.write_bytes(raw)
    code = "detail_contract" if artifact == "detail" else "receipt_contract"
    with pytest.raises(Refusal, match=code):
        if artifact == "detail":
            load_holdout_detail(path, plain_hash(raw))
        elif artifact == "receipt":
            load_holdout_receipt(path, plain_hash(raw))
        else:
            load_holdout_conflicts(path, load_manifest(candidate_path))


def test_confinement_outranks_label_and_input_outranks_collision(tmp_path):
    # Slice 1 section 4.6 master order, first match wins: path_confinement before
    # holdout_contract, and every input check before output_collision.
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    nested = _manifest(candidate.parent / "nested", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    with pytest.raises(Refusal, match="path_confinement"):
        run(candidate, [("Bad Label", nested)], policy,
            tmp_path / "private", tmp_path / "conflicts")
    sealed = _manifest(tmp_path / "sealed", ["same source"], prefix="s")
    (tmp_path / "private").mkdir()
    sealed.write_bytes(b"not json\n")
    with pytest.raises(Refusal, match="input_contract"):
        run(candidate, [("s", sealed)], policy, tmp_path / "private", tmp_path / "conflicts")
    bad_policy = tmp_path / "bad-policy"
    bad_policy.mkdir()
    (bad_policy / "policy.json").write_bytes(b"{}")
    good = _manifest(tmp_path / "sealed-ok", ["same source"], prefix="s")
    with pytest.raises(Refusal, match="policy_contract"):
        run(candidate, [("Bad Label", good)], bad_policy / "policy.json",
            tmp_path / "private2", tmp_path / "conflicts2")


def test_missing_output_parent_is_unavailable_after_input_checks(tmp_path):
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["same source"], prefix="s")
    policy = _policy(tmp_path)
    missing = tmp_path / "missing" / "private"
    with pytest.raises(Refusal, match="output_unavailable"):
        run(candidate, [("s", sealed)], policy, missing, tmp_path / "conflicts")
    candidate.write_bytes(b"not json\n")
    with pytest.raises(Refusal, match="input_contract"):
        run(candidate, [("s", sealed)], policy, missing, tmp_path / "conflicts")


def test_largest_valid_conflicts_file_publishes(tmp_path):
    # 5,000 flagged candidates whose ids are 128 astral scalars each: the
    # escaped conflicts file is about 7.7 MiB and must not refuse size_limit.
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "shared.txt").write_bytes(b"shared words")
    source = plain_hash(b"shared words")
    ids = sorted(chr(0x10000 + index) + "\U0010fffd" * 127 for index in range(5000))
    rows = [{"id": record_id, "group_id": "g", "stratum": "synthetic", "path": "shared.txt",
             "span": {"source_path": "shared.txt", "source_bytes_sha256": source,
                      "start_byte": 0, "end_byte": 12}} for record_id in ids]
    candidate = root / "packet.jsonl"
    candidate.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n"
                                   for row in rows))
    sealed = _manifest(tmp_path / "sealed", ["shared words"], prefix="s")
    conflicts = tmp_path / "conflicts"
    _, released = run(candidate, [("s", sealed)], _policy(tmp_path),
                      tmp_path / "private", conflicts)
    assert released
    data = (conflicts / "conflicts.json").read_bytes()
    assert len(data) > 2 * 1024 * 1024
    assert [row["candidate_id"] for row in json.loads(data)["conflicts"]] == ids


def test_gram_membership_ceiling_is_the_stated_limit(tmp_path):
    # Slice 4 section 8: 500,000 gram memberships pass, one more refuses
    # work_limit. The ceiling keeps the n=64 gram sets inside the memory bound.
    half = CEILINGS["gram memberships"] // 2
    assert CEILINGS["gram memberships"] == 500_000
    texts = {"at": [" ".join(f"c{i}" for i in range(half + 1)),
                    " ".join(f"s{i}" for i in range(half + 1))],
             "over": [" ".join(f"c{i}" for i in range(half + 2)),
                      " ".join(f"s{i}" for i in range(half + 1))]}
    policy, _ = load_holdout_policy(_policy(tmp_path, ngram=2))
    for name, (candidate_text, sealed_text) in texts.items():
        candidate = load_manifest(_manifest(tmp_path / name / "c", [candidate_text], prefix="c"))
        sealed = SealedSet("s", load_manifest(
            _manifest(tmp_path / name / "s", [sealed_text], prefix="s")))
        budget = WorkBudget(CEILINGS)
        if name == "at":
            holdout_firewall(candidate, (sealed,), policy, budget)
            assert budget.used["gram memberships"] == CEILINGS["gram memberships"]
        else:
            with pytest.raises(Refusal, match="work_limit"):
                holdout_firewall(candidate, (sealed,), policy, budget)


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


@pytest.mark.parametrize("case, expected", [
    ("sealed_row_outside_root+policy_oversize", "path_confinement"),
    ("output_through_symlink_into_candidate_root", "path_confinement"),
    ("outputs_differ_only_in_case", "path_confinement"),
    ("output_parent_symlink_loop", "output_unavailable"),
    ("output_parent_symlink_loop+candidate_contract", "input_contract"),
])
def test_combined_violations_report_the_earliest_master_order_code(tmp_path, case, expected):
    # Slice 1 section 4.6, first match wins; spec 04 section 5 confinement by
    # file identity; a missing or unusable output parent is output_unavailable.
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["other words"], prefix="s")
    policy = _policy(tmp_path)
    private, conflicts = tmp_path / "private", tmp_path / "conflicts"
    if "sealed_row_outside_root" in case:
        row = json.loads(sealed.read_bytes())
        row["path"] = str(tmp_path / "outside.txt")
        sealed.write_bytes(canonical_json(row))
    if "policy_oversize" in case:
        policy.write_bytes(b" " * (64 * 1024 + 1))
    if "through_symlink" in case:
        _symlink(tmp_path / "alias", candidate.parent)
        private = tmp_path / "alias" / "private"
    if "differ_only_in_case" in case:
        conflicts = tmp_path / "PRIVATE"
    if "symlink_loop" in case:
        _symlink(tmp_path / "loop", tmp_path / "loop")
        private = tmp_path / "loop" / "private"
    if "candidate_contract" in case:
        candidate.write_bytes(b"not json\n")
    with pytest.raises(Refusal, match=expected):
        run(candidate, [("s", sealed)], policy, private, conflicts)
    assert not (tmp_path / "private").exists() and not (tmp_path / "conflicts").exists()


class _BrokenStderr:
    def write(self, data):
        raise BrokenPipeError()

    def flush(self):
        raise BrokenPipeError()


def test_stream_failure_after_publication_is_not_a_refusal(tmp_path, monkeypatch):
    # Slice 1 section 4.7 as spec 04 inherits it: exit 0 means published, and a
    # stream that fails afterwards cannot un-publish the bundles.
    from setec.preflight import holdout_firewall
    candidate = _manifest(tmp_path / "candidate", ["same source"], prefix="c")
    sealed = _manifest(tmp_path / "sealed", ["other words"], prefix="s")
    private = tmp_path / "private"
    monkeypatch.setattr("sys.stderr", _BrokenStderr())
    assert holdout_firewall.main([
        "--candidate-manifest", str(candidate), "--sealed", "s", str(sealed),
        "--policy", str(_policy(tmp_path)), "--private-out", str(private),
        "--conflicts-out", str(tmp_path / "conflicts")]) == 0
    assert (private / "receipt.json").is_file()
