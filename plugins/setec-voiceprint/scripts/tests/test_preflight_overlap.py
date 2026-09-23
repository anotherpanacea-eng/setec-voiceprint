"""Observable packet overlap and confidentiality regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

import pytest

from setec.preflight.common import Refusal, canonical_json, domain_hash, load_manifest, parse_json, plain_hash
from setec.preflight import overlap as overlap_cli
from setec.preflight import overlap_core
from setec.preflight.overlap import run
from setec.preflight.overlap_core import (
    load_overlap_detail, load_overlap_receipt, preflight_word_ngrams_v1,
)


def _packet(root: Path, texts: list[str], *, groups: list[str] | None = None,
            sources: list[tuple[str, int, int] | None] | None = None) -> Path:
    root.mkdir(parents=True)
    rows = []
    for index, text in enumerate(texts):
        name = f"candidate-{index}.txt"
        data = text.encode("utf-8")
        (root / name).write_bytes(data)
        source = sources[index] if sources else None
        source_path, start, end = source if source else (name, 0, len(data))
        source_bytes = (root / source_path).read_bytes()
        rows.append({
            "id": f"r{index}", "group_id": (groups or [f"g{i}" for i in range(len(texts))])[index],
            "stratum": "synthetic", "path": name,
            "span": {"source_path": source_path,
                     "source_bytes_sha256": plain_hash(source_bytes),
                     "start_byte": start, "end_byte": end},
        })
    path = root / "packet.jsonl"
    path.write_bytes(b"".join(canonical_json(row) for row in rows))
    return path


def _policy(root: Path, *, measure: str = "containment", n: int = 2,
            numerator: int = 4, denominator: int = 5) -> Path:
    path = root / "policy.json"
    path.write_bytes(canonical_json({
        "schema": "setec-preflight-overlap-policy/1",
        "fuzzy": {"ngram": n, "measure": measure,
                  "threshold_numerator": numerator,
                  "threshold_denominator": denominator},
        "coordination_strata": ["synthetic"],
    }))
    return path


def _run(tmp_path: Path, texts: list[str], *, groups: list[str] | None = None,
         assignments: dict[str, str] | None = None, **policy_args):
    root = tmp_path / "inputs"
    manifest = _packet(root, texts, groups=groups)
    policy = _policy(root, **policy_args)
    split_path = None
    if assignments is not None:
        split_path = root / "splits.json"
        split_path.write_bytes(canonical_json({
            "schema": "setec-preflight-splits/1", "assignments": assignments}))
    out = tmp_path / "bundle"
    receipt_bytes, statuses = run(manifest, policy, out, split_path)
    return json.loads((out / "detail.json").read_bytes()), json.loads(receipt_bytes), statuses, out


def test_exact_duplicate_and_cross_split_cluster(tmp_path):
    detail, receipt, statuses, out = _run(
        tmp_path, ["One two three.", "One two three."],
        assignments={"r0": "train", "r1": "test"})
    assert statuses["exact_overlap"] == "failed"
    assert statuses["split_integrity"] == "failed"
    assert receipt["edge_counts"]["exact"] == 1
    assert receipt["reason_counts"]["split_cluster"] == 1
    assert len(detail["clusters"]) == 1
    assert detail["clusters"][0]["member_ids"] == ["r0", "r1"]
    assert receipt["detail_sha256"] == plain_hash((out / "detail.json").read_bytes())
    assert load_overlap_detail(out / "detail.json", receipt["detail_sha256"]) == detail
    assert load_overlap_receipt(out / "receipt.json", plain_hash((out / "receipt.json").read_bytes())) == receipt


def test_containment_finds_short_record_jaccard_misses(tmp_path):
    short = "alpha beta gamma delta"
    long = "prelude " + short + " epilogue outro coda tail"
    _, containment, _, _ = _run(tmp_path / "contain", [short, long])
    _, jaccard, _, _ = _run(tmp_path / "jaccard", [short, long], measure="jaccard")
    assert containment["edge_counts"]["fuzzy"] == 1
    assert jaccard["edge_counts"]["fuzzy"] == 0


def test_group_split_fails_without_similarity_edge(tmp_path):
    _, receipt, statuses, _ = _run(
        tmp_path, ["red apple pear", "blue ocean whale"], groups=["one", "one"],
        assignments={"r0": "train", "r1": "validation"})
    assert statuses["split_integrity"] == "failed"
    assert receipt["reason_counts"]["split_group"] == 1
    assert receipt["reason_counts"]["split_cluster"] == 0


def test_empty_manifest_never_passes(tmp_path):
    root = tmp_path / "inputs"
    root.mkdir()
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"")
    with pytest.raises(Refusal, match="input_contract"):
        load_manifest(manifest)


def test_nonfinite_json_number_uses_contract_refusal():
    with pytest.raises(Refusal, match="input_contract"):
        parse_json(b'{"value":1e999}', "input_contract")


def test_alias_refused_even_with_distinct_names(tmp_path):
    root = tmp_path / "inputs"
    manifest = _packet(root, ["alpha beta"])
    alias = root / "alias.txt"
    try:
        alias.hardlink_to(root / "candidate-0.txt")
    except OSError:
        pytest.skip("hard links unavailable")
    row = json.loads(manifest.read_bytes())
    row["span"]["source_path"] = alias.name
    manifest.write_bytes(canonical_json(row))
    with pytest.raises(Refusal, match="path_alias"):
        load_manifest(manifest)


def test_short_gram_and_ascii_only_case(tmp_path):
    assert preflight_word_ngrams_v1("one", 2) == frozenset()
    assert preflight_word_ngrams_v1("A É a é", 1) == frozenset({("a",), ("É",), ("é",)})
    assert preflight_word_ngrams_v1("can't can\u2019t cant", 1) == frozenset({
        ("can't",), ("can\u2019t",), ("cant",)})


def test_receipt_never_contains_identifiers_or_text(tmp_path):
    root = tmp_path / "inputs"
    manifest = _packet(root, ["secret prose token", "another private line"])
    rows = [json.loads(line) for line in manifest.read_bytes().splitlines()]
    rows[0]["id"] = "secret-id-canary"
    rows[0]["group_id"] = "secret-group-canary"
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    receipt, _ = run(manifest, _policy(root), tmp_path / "bundle")
    for canary in (b"secret-id-canary", b"secret-group-canary", b"secret prose token",
                   b"candidate-0.txt"):
        assert canary not in receipt


def test_strata_withheld_whole_when_small_cell(tmp_path):
    _, receipt, _, _ = _run(tmp_path, [f"unique text {i}" for i in range(4)])
    assert receipt["stratum_counts"] is None


@pytest.mark.parametrize("mutation", [
    lambda detail: detail.update(tool_version=True),
    lambda detail: detail["records"][0].update(start_byte=-1),
    lambda detail: detail["records"][0].update(split="bogus"),
    lambda detail: detail["clusters"][0].update(member_ids=["r1", "r0"]),
])
def test_detail_reloader_refuses_canonical_tampering(tmp_path, mutation):
    detail, _, _, _ = _run(tmp_path, ["alpha beta", "alpha beta"])
    mutation(detail)
    artifact = tmp_path / "tampered.json"
    data = canonical_json(detail)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_overlap_detail(artifact, plain_hash(data))


def test_receipt_reloader_refuses_empty_population(tmp_path):
    _, receipt, _, _ = _run(tmp_path, ["alpha beta"])
    receipt["record_count"] = 0
    artifact = tmp_path / "tampered-receipt.json"
    data = canonical_json(receipt)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_overlap_receipt(artifact, plain_hash(data))


def test_detail_reloader_recomputes_cross_split_integrity(tmp_path):
    detail, _, _, _ = _run(tmp_path, ["shared prose", "shared prose"],
                           groups=["same", "same"],
                           assignments={"r0": "train", "r1": "test"})
    detail["split_integrity"] = {"status": "passed", "cross_split_clusters": [],
                                 "cross_split_groups": []}
    detail["stage_status"]["split_integrity"] = "passed"
    detail["reason_counts"]["split_cluster"] = 0
    detail["reason_counts"]["split_group"] = 0
    artifact = tmp_path / "tampered-integrity.json"
    data = canonical_json(detail)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_overlap_detail(artifact, plain_hash(data))


def test_detail_reloader_binds_record_set_to_record_content(tmp_path):
    detail, _, _, _ = _run(tmp_path, ["one private candidate"])
    record = detail["records"][0]
    record["content_sha256"] = "f" * 64
    replacement_cluster = domain_hash("setec-preflight-cluster-v1",
                                      canonical_json([record["content_sha256"]]))
    record["cluster_sha256"] = replacement_cluster
    detail["clusters"][0]["cluster_sha256"] = replacement_cluster
    artifact = tmp_path / "tampered-content.json"
    data = canonical_json(detail)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_overlap_detail(artifact, plain_hash(data))


def test_detail_reloader_derives_exact_stage_from_edges(tmp_path):
    detail, _, _, _ = _run(tmp_path, ["duplicate prose", "duplicate prose"])
    detail["stage_status"]["exact_overlap"] = "passed"
    detail["reason_counts"]["exact_duplicate"] = 0
    detail["reason_counts"]["ok"] += 1
    artifact = tmp_path / "tampered-stage.json"
    data = canonical_json(detail)
    artifact.write_bytes(data)
    with pytest.raises(Refusal, match="detail_contract"):
        load_overlap_detail(artifact, plain_hash(data))


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL check")
def test_bundle_files_have_explicit_owner_private_dacl(tmp_path):
    import windows_descriptor_io as winio

    _, _, _, out = _run(tmp_path, ["private candidate text"])
    chain = winio.pin_directory_chain(out)
    try:
        winio.require_owner_private(chain[-1], "directory")
        for name in ("detail.json", "receipt.json"):
            handle = winio.open_file(chain[-1], name)
            try:
                winio.require_owner_private(handle, "file")
            finally:
                winio.close(handle)
    finally:
        for handle in reversed(chain):
            winio.close(handle)


# --- CLI boundary: exit codes, streams, precedence (slice 1 §4.6, §4.7, §9) ---

_STAGE_LINE = re.compile(rb"(intake|exact_overlap|fuzzy_overlap|span_overlap|split_integrity) "
                         rb"(passed|failed|needs_human_review|not_run)\Z")


def _main(capsysbinary, manifest: Path, policy: Path, out: Path, *extra: str):
    code = overlap_cli.main(["--manifest", str(manifest), "--policy", str(policy),
                             "--out-bundle", str(out), *extra])
    captured = capsysbinary.readouterr()
    return code, captured.out, captured.err


def _inputs(tmp_path: Path, texts=("alpha beta gamma", "delta epsilon zeta")):
    root = tmp_path / "inputs"
    return _packet(root, list(texts)), _policy(root)


def _splits(root: Path, assignments: object, name: str = "splits.json") -> Path:
    path = root / name
    path.write_bytes(canonical_json({"schema": "setec-preflight-splits/1",
                                     "assignments": assignments}))
    return path


def _break_manifest(manifest: Path) -> None:
    rows = [json.loads(line) for line in manifest.read_bytes().splitlines()]
    rows[-1]["unknown"] = True
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))


def test_main_success_streams_carry_only_receipt_and_stage_lines(tmp_path, capsysbinary):
    root = tmp_path / "inputs"
    root.mkdir()
    rows = []
    for index, text in enumerate([b"secret prose token one", b"secret prose token two"]):
        name = f"secret-path-canary-{index}.txt"
        (root / name).write_bytes(text)
        rows.append({"id": f"secret-id-canary-{index}", "group_id": "secret-group-canary",
                     "stratum": "secret-stratum-canary", "path": name,
                     "span": {"source_path": name, "source_bytes_sha256": plain_hash(text),
                              "start_byte": 0, "end_byte": len(text)}})
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    out = tmp_path / "bundle"
    code, stdout, stderr = _main(capsysbinary, manifest, _policy(root), out)
    assert code == 0
    assert stdout == (out / "receipt.json").read_bytes()
    lines = stderr.splitlines()
    assert len(lines) == 5 and all(_STAGE_LINE.match(line) for line in lines)
    for canary in (b"secret-id-canary", b"secret-group-canary", b"secret-stratum-canary",
                   b"secret-path-canary", b"secret prose", str(tmp_path).encode()):
        assert canary not in stdout and canary not in stderr


@pytest.mark.parametrize("breakage, code, exit_code", [
    ("absolute", "path_confinement", 2),
    ("missing_parent", "output_unavailable", 4),
])
def test_main_refusals_print_only_the_code(tmp_path, capsysbinary, breakage, code, exit_code):
    manifest, policy = _inputs(tmp_path)
    out = tmp_path / "bundle"
    if breakage == "absolute":
        row = json.loads(manifest.read_bytes().splitlines()[0])
        row["path"] = str(tmp_path / "secret-outside.txt")
        manifest.write_bytes(canonical_json(row))
    else:
        out = tmp_path / "no-such-parent" / "bundle"
    result = _main(capsysbinary, manifest, policy, out)
    assert result == (exit_code, b"", code.encode() + b"\n")
    assert not out.exists()


def test_main_reports_injected_exception_as_internal_refusal(tmp_path, capsysbinary, monkeypatch):
    manifest, policy = _inputs(tmp_path)

    def explode(*args, **kwargs):
        raise RuntimeError(f"{tmp_path}/secret r0 traceback text")

    monkeypatch.setattr(overlap_cli, "build_overlap", explode)
    out = tmp_path / "bundle"
    assert _main(capsysbinary, manifest, policy, out) == (2, b"", b"internal_refusal\n")
    assert not out.exists()


def test_stdout_failure_after_publication_is_not_a_refusal(tmp_path, monkeypatch):
    manifest, policy = _inputs(tmp_path)

    class _Broken:
        def write(self, data):
            raise BrokenPipeError()

        def flush(self):
            raise BrokenPipeError()

    class _Stdout:
        buffer = _Broken()

    monkeypatch.setattr("sys.stdout", _Stdout())
    out = tmp_path / "bundle"
    code = overlap_cli.main(["--manifest", str(manifest), "--policy", str(policy),
                             "--out-bundle", str(out)])
    assert code == 0
    assert (out / "receipt.json").is_file()


def test_empty_split_map_argument_is_a_split_contract_refusal(tmp_path, capsysbinary):
    manifest, policy = _inputs(tmp_path)
    out = tmp_path / "bundle"
    result = _main(capsysbinary, manifest, policy, out, "--split-map", "")
    assert result == (2, b"", b"split_contract\n")
    assert not out.exists()


@pytest.mark.parametrize("case, expected", [
    ("manifest_contract+policy_oversize", "size_limit"),
    ("manifest_contract+policy_contract", "input_contract"),
    ("policy_contract+split_contract", "policy_contract"),
    ("manifest_contract+output_inside_manifest_dir", "path_confinement"),
    ("manifest_contract+output_collision", "input_contract"),
    ("split_contract+output_collision", "split_contract"),
])
def test_combined_violations_report_the_earliest_master_order_code(
        tmp_path, capsysbinary, case, expected):
    manifest, policy = _inputs(tmp_path)
    root = manifest.parent
    out = tmp_path / "bundle"
    split_args: tuple[str, ...] = ()
    if "manifest_contract" in case:
        _break_manifest(manifest)
    if "policy_oversize" in case:
        policy.write_bytes(b" " * (64 * 1024 + 1))
    if "policy_contract" in case:
        policy.write_bytes(b"{}")
    if "split_contract" in case:
        split_args = ("--split-map", str(_splits(root, {"r0": "train"})))
    if "output_inside" in case:
        out = root / "bundle"
    if "output_collision" in case:
        out.mkdir()
    code, stdout, stderr = _main(capsysbinary, manifest, policy, out, *split_args)
    assert (code, stdout, stderr) == (2, b"", expected.encode() + b"\n")


def test_collision_leaves_destination_untouched_and_reruns_are_identical(tmp_path, capsysbinary):
    manifest, policy = _inputs(tmp_path)
    first, second, taken = tmp_path / "first", tmp_path / "second", tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_bytes(b"operator data")
    assert _main(capsysbinary, manifest, policy, taken) == (2, b"", b"output_collision\n")
    assert [p.name for p in taken.iterdir()] == ["keep.txt"]
    assert (taken / "keep.txt").read_bytes() == b"operator data"
    assert not any(p.name.startswith(".preflight-") for p in tmp_path.iterdir())
    assert _main(capsysbinary, manifest, policy, first)[0] == 0
    assert _main(capsysbinary, manifest, policy, second)[0] == 0
    for name in ("receipt.json", "detail.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    receipt = json.loads((first / "receipt.json").read_bytes())
    assert receipt["detail_sha256"] == plain_hash((first / "detail.json").read_bytes())


# Two records "a b c" / "a b d" whose declared spans overlap in one source,
# under 2-grams and Jaccard 1/5. Counted by hand from §8's "counted when":
# tokens 3+3; gram memberships 2+2; one posting ("a b") with one pair visit
# and one candidate pair; two lookups over the smaller set; one intersecting
# span pair; two edges (one fuzzy, one span).
_WORK_USED = {
    "tokens": 6, "gram memberships": 4, "posting visits": 1, "candidate pairs": 1,
    "membership lookups": 2, "span pair visits": 1, "edges": 2,
}


def test_work_fixture_counts_cover_every_ceiling():
    assert set(_WORK_USED) == set(overlap_core.CEILINGS)


@pytest.mark.parametrize("counter", sorted(_WORK_USED))
def test_each_work_ceiling_passes_at_limit_and_refuses_one_over(tmp_path, monkeypatch, counter):
    manifest = _packet(tmp_path / "packet", ["a b c", "a b d"])
    packet = manifest.parent
    (packet / "source.txt").write_bytes(b"0123456789")
    rows = [json.loads(line) for line in manifest.read_bytes().splitlines()]
    for row, (start, end) in zip(rows, [(0, 5), (3, 8)]):
        row["span"] = {"source_path": "source.txt",
                       "source_bytes_sha256": plain_hash(b"0123456789"),
                       "start_byte": start, "end_byte": end}
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    policy = _policy(packet, measure="jaccard", n=2, numerator=1, denominator=5)
    monkeypatch.setitem(overlap_core.CEILINGS, counter, _WORK_USED[counter])
    receipt, _ = run(manifest, policy, tmp_path / "at-limit")
    counts = json.loads(receipt)["edge_counts"]
    assert (counts["fuzzy"], counts["span"], counts["exact"]) == (1, 1, 0)
    monkeypatch.setitem(overlap_core.CEILINGS, counter, _WORK_USED[counter] - 1)
    with pytest.raises(Refusal) as caught:
        run(manifest, policy, tmp_path / "one-over")
    assert caught.value.code == "work_limit"
    assert not (tmp_path / "one-over").exists()
