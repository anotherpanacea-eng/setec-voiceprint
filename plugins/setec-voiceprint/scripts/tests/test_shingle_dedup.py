"""Synthetic black-box contract tests for :mod:`shingle_dedup`.

The fixture vocabulary is deliberately generated control tokens.  No prose or
corpus fixture belongs in this test module or in a generated index/report.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "shingle_dedup.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import shingle_dedup as sd  # noqa: E402


def _tokens(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{number:04d}" for number in range(count))


def _manifest(path: Path, rows: list[dict[str, object]], *, newline: bytes = b"\n") -> None:
    path.write_bytes(newline.join(
        json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        for row in rows
    ) + newline)


def _run(*arguments: str, timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _receipt(result: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.endswith(b"\n") and b"\r" not in result.stdout
    return json.loads(result.stdout)


def _index(tmp_path: Path, rows: list[dict[str, object]], *, name: str = "records") -> tuple[Path, Path, dict[str, object]]:
    manifest = tmp_path / f"{name}.jsonl"
    index = tmp_path / f"{name}.sqlite"
    checkpoint = tmp_path / f"{name}-state"
    _manifest(manifest, rows)
    receipt = _receipt(_run(
        "build-index", "--manifest", str(manifest), "--index-out", str(index),
        "--checkpoint-dir", str(checkpoint),
    ))
    assert index.is_file()
    return manifest, index, receipt


def _pin(receipt: dict[str, object]) -> str:
    for key in ("index_sha256", "raw_index_sha256"):
        value = receipt.get(key)
        if isinstance(value, str):
            return value
    raise AssertionError(f"build receipt omitted exact index pin: {receipt}")


def _base_rows() -> list[dict[str, object]]:
    # 107 tokens means exactly 100 unique 8-grams.  The second document shares
    # 42 leading tokens, hence exactly 35/100 directional containment.
    first = _tokens("ctl", 107)
    second = " ".join(first.split()[:42] + _tokens("alt", 65).split())
    third = _tokens("zzz", 107)
    return [
        {"id": "ref", "draft_id": "draft_a", "stage": "s0", "stage_order": 0, "text": first},
        {"id": "later", "draft_id": "draft_a", "stage": "s1", "stage_order": 1, "text": second},
        {"id": "other", "draft_id": "draft_b", "stage": "s0", "stage_order": 0, "text": third},
        {"id": "short", "draft_id": "draft_c", "stage": "s0", "stage_order": 0, "text": _tokens("tiny", 7)},
    ]


def test_tokenization_eight_gram_floor_repetition_and_exact_metrics() -> None:
    assert sd._tokens("ALPHA, beta! \u03a9mega") == ["alpha", "beta", "\u03c9mega"]
    assert sd._shingle_digests(sd._tokens(_tokens("floor", 7))) == set()
    eight = sd._shingle_digests(sd._tokens(_tokens("floor", 8)))
    assert len(eight) == 1
    repeated = sd._shingle_digests(sd._tokens(" ".join(["repeat"] * 16)))
    assert len(repeated) == 1
    query = ("query", "d", "later", 1, None, 10, 3, "eligible")
    reference = ("reference", "d", "earlier", 0, None, 10, 4, "eligible")
    q = {b"a", b"b", b"c"}; r = {b"a", b"b", b"c", b"d"}
    forward = sd._pair(query, reference, q, r, batch=False)
    reverse = sd._pair(reference, query, r, q, batch=False)
    assert forward["containment"] == 1.0 and forward["reverse_containment"] == 0.75
    assert reverse["containment"] == 0.75 and reverse["jaccard"] == forward["jaccard"] == 0.75
    batch = sd._pair(query, reference, q, r, batch=True)
    assert batch["pair_containment_direction"] == "query_in_reference"
    assert batch["tier_metric_numerator"] == 3 and batch["tier_metric_denominator"] == 3
    equal = sd._pair(query, reference, q, q, batch=True)
    assert equal["pair_containment_direction"] == "equal"


def test_opaque_control_field_exact_128_byte_boundary() -> None:
    assert sd._opaque("x" * 128) == "x" * 128
    with pytest.raises(sd.Refusal):
        sd._opaque("x" * 129)


def test_config_and_logical_canonical_stream_are_literal_goldens() -> None:
    expected_configs = {
        "15.0.0": "b55fbadcfb18f8a53ac6f6df6f20fd2b4a59677f04e16b4a3642ea418ae1880c",
        "15.1.0": "fa978a380a017266526025466d51a364a2d2132a0efc8b2c714b3a73f64e377d",
    }
    assert sd.unicodedata.unidata_version in expected_configs
    assert sd._config_sha256() == expected_configs[sd.unicodedata.unidata_version]

    class FrozenRows:
        def execute(self, sql: str) -> list[tuple[object, ...]]:
            if "FROM documents" in sql:
                return [("doc", "draft", "stage", -1, b"\x11" * 32, 8, 1, "eligible")]
            return [(b"\x22" * 32, "doc")]

    meta = {"schema_version": "x", "logical_sha256": "0" * 64,
            "unicode_version": "fixed"}
    assert sd._logical_seal(FrozenRows(), meta) == (
        "8494b72fd058e1b8c5a3f9fda0d8649a4eb9bfa46ba22e92366ff9c3e9b848be"
    )


def test_missing_final_lf_unicode_separators_signed_orders_and_self_id(tmp_path: Path) -> None:
    text = _tokens("control", 8) + "\u2028inside\u2029data"
    rows = [
        {"id": "low", "draft_id": "d-low", "stage": "s", "stage_order": -(2**63), "text": text},
        {"id": "high", "draft_id": "d-high", "stage": "s", "stage_order": 2**63 - 1, "text": text},
    ]
    manifest = tmp_path / "records.jsonl"
    # No final LF/CR terminator; U+2028/U+2029 remain JSON string data rather
    # than physical JSONL separators.
    manifest.write_bytes(b"\r".join(json.dumps(row, separators=(",", ":")).encode() for row in rows))
    index = tmp_path / "index.sqlite"
    build = _receipt(_run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                          "--checkpoint-dir", str(tmp_path / "state")))
    query = tmp_path / "query.txt"; query.write_bytes(text.encode())
    report = tmp_path / "report.json"
    _receipt(_run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "low", "--report-out", str(report)))
    assert [row["reference_id"] for row in json.loads(report.read_bytes())["pairs"]] == ["high"]


def test_shuffled_manifest_preserves_logical_index_and_recursive_artifact_leak_gate(tmp_path: Path) -> None:
    rows = _base_rows()[:3]
    first_manifest, first_index, first = _index(tmp_path, rows, name="first")
    second_manifest, second_index, second = _index(tmp_path, list(reversed(rows)), name="second")
    with sqlite3.connect(first_index) as connection:
        first_descriptor = dict(connection.execute("SELECT key,value FROM meta"))["canonical_descriptors_sha256"]
    with sqlite3.connect(second_index) as connection:
        second_descriptor = dict(connection.execute("SELECT key,value FROM meta"))["canonical_descriptors_sha256"]
    assert first_descriptor == second_descriptor
    assert first["logical_index_sha256"] != second["logical_index_sha256"]
    assert hashlib.sha256(first_manifest.read_bytes()).hexdigest() != hashlib.sha256(second_manifest.read_bytes()).hexdigest()
    # Generated index/checkpoint artifacts may contain only hashes and opaque
    # ids; the synthetic source token vocabulary never crosses that boundary.
    for artifact in (tmp_path / "first.sqlite", tmp_path / "second.sqlite", *tmp_path.glob("*-state/*.sqlite")):
        assert b"ctl0000" not in artifact.read_bytes()


def test_recursive_artifacts_and_console_records_do_not_leak_synthetic_tokens(tmp_path: Path) -> None:
    rows = [{"id": "doc", "draft_id": "draft", "stage": "s", "stage_order": 0, "text": _tokens("leakseed", 8)}]
    manifest, index, build = _index(tmp_path, rows, name="leak")
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("leakseed", 8).encode())
    report = tmp_path / "report.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report))
    _receipt(result)
    for stream in (result.stdout, result.stderr):
        assert b"leakseed0000" not in stream
        for line in stream.splitlines():
            assert set(json.loads(line)) <= {"schema_version", "tool", "status", "code", "report_sha256", "reported_pairs", "summary", "phase", "processed"}
    for artifact in tmp_path.rglob("*"):
        if artifact.is_file() and artifact not in {manifest, query}:
            assert b"leakseed0000" not in artifact.read_bytes()


def test_build_query_batch_exact_boundary_and_leak_boundary(tmp_path: Path) -> None:
    manifest, index, build = _index(tmp_path, _base_rows())
    pin = _pin(build)
    query = tmp_path / "query.txt"
    query.write_bytes(_tokens("ctl", 107).encode("utf-8"))
    query_report = tmp_path / "query.json"
    query_result = _run(
        "query-doc", "--index", str(index), "--index-sha256", pin,
        "--query-file", str(query), "--query-id", "query", "--report-out", str(query_report),
    )
    query_receipt = _receipt(query_result)
    assert set(query_receipt["summary"]) >= {"potential_pairs", "assessed_pairs", "reported_pairs", "containment_at_least_0_60_pairs"}
    assert query_receipt.get("report_sha256") == hashlib.sha256(query_report.read_bytes()).hexdigest()
    report_bytes = query_report.read_bytes()
    assert report_bytes.endswith(b"\n") and b"\r" not in report_bytes
    report = json.loads(report_bytes)
    assert report["schema_version"] == "setec-shingle-report/1"
    assert report["calibration_status"] == "operational_uncalibrated"
    assert report["method"]["shingle_k"] == 8
    # Same content remains a valid hit under a distinct opaque id.  It ranks
    # ahead of the exact 35/100 boundary row.
    assert [pair["reference_id"] for pair in report["pairs"]] == ["ref", "later"]
    boundary = report["pairs"][1]
    assert boundary["containment_numerator"] == 35
    assert boundary["containment_denominator"] == 100
    assert boundary["overlap_tier"] == "containment_0_35_to_0_60"
    assert boundary["pair_containment_direction"] is None
    assert b"ctl0000" not in report_bytes and b"alt0000" not in report_bytes
    assert b"query" not in query_result.stdout and b"ref" not in query_result.stdout

    batch_report = tmp_path / "batch.json"
    batch_receipt = _receipt(_run(
        "batch-report", "--index", str(index), "--index-sha256", pin,
        "--report-out", str(batch_report), "--checkpoint-dir", str(tmp_path / "batch-state"),
    ))
    assert set(batch_receipt["summary"]) >= {"potential_pairs", "assessed_pairs", "reported_pairs", "containment_at_least_0_60_pairs"}
    assert batch_receipt.get("report_sha256") == hashlib.sha256(batch_report.read_bytes()).hexdigest()
    batch = json.loads(batch_report.read_bytes())
    assert batch["report_kind"] == "draft_stage_pair_candidates"
    assert len(batch["pairs"]) == 1
    pair = batch["pairs"][0]
    assert pair["query_id"] == "later" and pair["reference_id"] == "ref"
    assert pair["pair_kind"] == "draft_stage_pair_candidate"
    assert pair["overlap_tier"] == "containment_0_35_to_0_60"
    # Manifest bytes are bound as a source seal but neither text nor a path is
    # permitted to enter the report payload.
    assert batch["source_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert b"ctl0000" not in batch_report.read_bytes()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_manifest_physical_newline_forms_are_equivalent(tmp_path: Path, newline: bytes) -> None:
    rows = _base_rows()[:2]
    manifest = tmp_path / "records.jsonl"
    index = tmp_path / "records.sqlite"
    _manifest(manifest, rows, newline=newline)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    receipt = _receipt(result)
    assert isinstance(receipt.get("logical_index_sha256"), str)


@pytest.mark.parametrize("payload", [
    b"\n",
    b"not-json\n",
    b"[]\n",
    b'{"id":"a","id":"b","draft_id":"d","stage":"s","stage_order":0,"text":"x"}\n',
    b'{"id":"a","draft_id":"d","stage":"s","stage_order":0,"text":{"nested":1}}\n',
    b'{"id":"a","draft_id":"d","stage":"s","stage_order":true,"text":"x"}\n',
    b'{"id":"a","draft_id":"d","stage":"s","stage_order":1e400,"text":"x"}\n',
    b'{"id":"a","draft_id":"d","stage":"s","stage_order":NaN,"text":"x"}\n',
    b'{"id":"a","draft_id":"d","stage":"s","stage_order":0,"text":"x","extra":1}\n',
    b'\xef\xbb\xbf{"id":"a","draft_id":"d","stage":"s","stage_order":0,"text":"x"}\n',
    b'\xff\n',
])
def test_strict_manifest_refusals_leave_no_final_index(tmp_path: Path, payload: bytes) -> None:
    manifest = tmp_path / "bad.jsonl"
    index = tmp_path / "out.sqlite"
    manifest.write_bytes(payload)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3
    assert b"Traceback" not in result.stderr
    assert not index.exists()


def test_usage_error_is_exit_two_and_does_not_echo_untrusted_argument() -> None:
    result = _run("query-doc", "--index", "private-argument-token")
    assert result.returncode == 2
    assert b"private-argument-token" not in result.stderr
    assert b"Traceback" not in result.stderr


def test_portability_source_avoids_unconditional_posix_permission_calls() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    io_source = (SCRIPTS / "shingle_dedup_io.py").read_text(encoding="utf-8")
    assert "chmod(" not in source and "fchmod(" not in source
    assert "def _optional_flag" in io_source and "getattr(os, name, 0)" in io_source
    assert '_optional_flag("O_BINARY")' in io_source
    assert '_optional_flag("O_NOFOLLOW")' in io_source


def test_windows_source_reader_allows_stable_multiple_links_only_for_read_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shingle_dedup_io as secure_io

    payload = b"stable-multiple-link-source"
    calls: list[tuple[str, bool]] = []
    offsets: dict[int, int] = {}

    class Direct:
        def __init__(self, identity: tuple[object, ...], size: int = 0) -> None:
            self.identity = identity
            self.size = size

    class FakeWindowsIo:
        next_handle = 10

        def pin_directory(self, _path: Path, *, writable_final: bool) -> tuple[int, int, str]:
            assert not writable_final
            self.next_handle += 2
            return self.next_handle - 1, self.next_handle, "parent"

        def open_file(self, _parent: int, _name: str, *, allow_multiple_links: bool = False) -> int:
            calls.append(("open", allow_multiple_links))
            self.next_handle += 1
            offsets[self.next_handle] = 0
            return self.next_handle

        def require_direct(
            self, handle: int, kind: str, *, allow_multiple_links: bool = False,
        ) -> Direct:
            calls.append((kind, allow_multiple_links))
            if kind == "directory":
                return Direct(("directory",))
            return Direct(("file", 2), len(payload))

        def read(self, handle: int, maximum: int) -> bytes:
            start = offsets[handle]
            chunk = payload[start:start + maximum]
            offsets[handle] += len(chunk)
            return chunk

        def list_names(self, _parent: int) -> list[str]:
            return []

        def close(self, _handle: int) -> None:
            return None

    fake = FakeWindowsIo()
    monkeypatch.setattr(secure_io, "_windows_module", lambda: fake)
    assert secure_io._windows_read(tmp_path / "source.bin", len(payload)) == payload
    assert [allowed for kind, allowed in calls if kind in {"open", "file"}] == [True] * 5
    assert all(not allowed for kind, allowed in calls if kind == "directory")


def test_descriptor_symlink_refuses_without_output(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(_tokens("safe", 8).encode())
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable on this runner")
    manifest = tmp_path / "records.jsonl"
    _manifest(manifest, [{"id": "doc", "draft_id": "d", "stage": "s", "stage_order": 0, "path": "linked.txt"}])
    output = tmp_path / "index.sqlite"
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(output),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not output.exists()


@pytest.mark.parametrize("path_value", ["../outside.txt", "/absolute.txt", "bad\x00name"])
def test_descriptor_unsafe_path_refuses(tmp_path: Path, path_value: str) -> None:
    manifest = tmp_path / "records.jsonl"; output = tmp_path / "index.sqlite"
    _manifest(manifest, [{"id": "doc", "draft_id": "d", "stage": "s", "stage_order": 0, "path": path_value}])
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(output),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not output.exists()


def test_descriptor_directory_refuses(tmp_path: Path) -> None:
    (tmp_path / "directory").mkdir()
    manifest = tmp_path / "records.jsonl"; output = tmp_path / "index.sqlite"
    _manifest(manifest, [{"id": "doc", "draft_id": "d", "stage": "s", "stage_order": 0, "path": "directory"}])
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(output),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not output.exists()


@pytest.mark.parametrize("rows", [
    [
        {"id": "same", "draft_id": "d1", "stage": "s", "stage_order": 0, "text": _tokens("a", 8)},
        {"id": "same", "draft_id": "d2", "stage": "s", "stage_order": 0, "text": _tokens("b", 8)},
    ],
    [
        {"id": "one", "draft_id": "d", "stage": "s", "stage_order": 0, "text": _tokens("a", 8)},
        {"id": "two", "draft_id": "d", "stage": "s", "stage_order": 1, "text": _tokens("b", 8)},
    ],
])
def test_duplicate_descriptor_identity_refuses(tmp_path: Path, rows: list[dict[str, object]]) -> None:
    manifest = tmp_path / "records.jsonl"; output = tmp_path / "index.sqlite"
    _manifest(manifest, rows)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(output),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not output.exists()


def test_short_query_and_existing_output_refuse_without_overwrite(tmp_path: Path) -> None:
    _manifest_rows, index, build = _index(tmp_path, _base_rows()[:2])
    query = tmp_path / "short.txt"
    query.write_bytes(_tokens("q", 7).encode())
    report = tmp_path / "report.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()
    report.write_bytes(b"winner")
    query.write_bytes(_tokens("ctl", 107).encode())
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and report.read_bytes() == b"winner"


def test_exact_34_35_60_thresholds_and_index_postings_match_bruteforce(tmp_path: Path) -> None:
    query_text = _tokens("metric", 107)
    query_tokens = query_text.split()

    def document(shared_tokens: int, suffix: str) -> str:
        # A contiguous common prefix of n tokens yields n - 7 shared shingles.
        return " ".join(query_tokens[:shared_tokens] + _tokens(suffix, 107 - shared_tokens).split())

    rows = [
        {"id": "m34", "draft_id": "d34", "stage": "a", "stage_order": 0, "text": document(41, "a")},
        {"id": "m35", "draft_id": "d35", "stage": "a", "stage_order": 0, "text": document(42, "b")},
        {"id": "m60", "draft_id": "d60", "stage": "a", "stage_order": 0, "text": document(67, "c")},
        {"id": "none", "draft_id": "dno", "stage": "a", "stage_order": 0, "text": _tokens("disjoint", 107)},
    ]
    _manifest_rows, index, build = _index(tmp_path, rows, name="thresholds")
    # Every document contributes exactly 100 unique postings.  Some documents
    # intentionally overlap each other through their synthetic common prefix;
    # the inverted table must still contain the exact digest/doc pairs.
    with sqlite3.connect(index) as connection:
        assert connection.execute("SELECT COUNT(*) FROM documents").fetchone() == (4,)
        assert connection.execute("SELECT COUNT(*) FROM postings").fetchone() == (400,)
        persisted = {(bytes(row[0]), row[1]) for row in connection.execute("SELECT shingle_sha256,doc_id FROM postings")}
    expected: set[tuple[bytes, str]] = set()
    for row in rows:
        expected.update((digest, row["id"]) for digest in sd._shingle_digests(sd._tokens(row["text"])))
    assert persisted == expected
    query = tmp_path / "metric.txt"
    query.write_bytes(query_text.encode())
    report_path = tmp_path / "thresholds.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report_path))
    _receipt(result)
    report = json.loads(report_path.read_bytes())
    got = {row["reference_id"]: row for row in report["pairs"]}
    assert set(got) == {"m35", "m60"}
    assert got["m35"]["shared_shingles"] == 35
    assert got["m35"]["overlap_tier"] == "containment_0_35_to_0_60"
    assert got["m60"]["shared_shingles"] == 60
    assert got["m60"]["overlap_tier"] == "containment_at_least_0_60"
    assert report["summary"]["below_0_35_pairs"] == 1
    assert report["summary"]["no_overlap_pairs"] == 1


def test_query_exact_tie_uses_utf8_id_tiebreak_and_reports_tied_best_count(tmp_path: Path) -> None:
    text = _tokens("tie", 8)
    rows = [
        {"id": "b", "draft_id": "d-b", "stage": "s", "stage_order": 0, "text": text},
        {"id": "a", "draft_id": "d-a", "stage": "s", "stage_order": 0, "text": text},
    ]
    _manifest, index, build = _index(tmp_path, rows, name="ties")
    query = tmp_path / "query.txt"; query.write_bytes(text.encode())
    report = tmp_path / "report.json"
    _receipt(_run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report)))
    payload = json.loads(report.read_bytes())
    assert [pair["reference_id"] for pair in payload["pairs"]] == ["a", "b"]
    assert payload["summary"]["tied_best_count"] == 2


def test_batch_max_gate_finds_expansion_and_contraction_but_not_cross_draft(tmp_path: Path) -> None:
    full = _tokens("gate", 107)
    short = " ".join(full.split()[:67])
    rows = [
        {"id": "contract-early", "draft_id": "contract", "stage": "early", "stage_order": 0, "text": full},
        {"id": "contract-late", "draft_id": "contract", "stage": "late", "stage_order": 1, "text": short},
        {"id": "expand-early", "draft_id": "expand", "stage": "early", "stage_order": 0, "text": short},
        {"id": "expand-late", "draft_id": "expand", "stage": "late", "stage_order": 1, "text": full},
        {"id": "cross", "draft_id": "other", "stage": "only", "stage_order": 0, "text": full},
    ]
    _manifest, index, build = _index(tmp_path, rows, name="gate")
    report = tmp_path / "batch.json"
    _receipt(_run("batch-report", "--index", str(index), "--index-sha256", _pin(build),
                  "--report-out", str(report), "--checkpoint-dir", str(tmp_path / "state")))
    pairs = json.loads(report.read_bytes())["pairs"]
    assert {(pair["query_id"], pair["reference_id"], pair["pair_containment_direction"]) for pair in pairs} == {
        ("contract-late", "contract-early", "query_in_reference"),
        ("expand-late", "expand-early", "reference_in_query"),
    }
    assert all(pair["overlap_tier"] == "containment_at_least_0_60" for pair in pairs)


def test_tied_stage_order_refuses_before_index_publication(tmp_path: Path) -> None:
    rows = [
        {"id": "one", "draft_id": "draft", "stage": "one", "stage_order": 0, "text": _tokens("one", 8)},
        {"id": "two", "draft_id": "draft", "stage": "two", "stage_order": 0, "text": _tokens("two", 8)},
    ]
    manifest = tmp_path / "records.jsonl"; output = tmp_path / "index.sqlite"
    _manifest(manifest, rows)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(output),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not output.exists()


def test_report_closed_schema_and_payload_seal(tmp_path: Path) -> None:
    _manifest, index, build = _index(tmp_path, _base_rows()[:2], name="sealed-report")
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("ctl", 107).encode())
    report = tmp_path / "report.json"
    _receipt(_run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report)))
    payload = json.loads(report.read_bytes())
    assert set(payload) == {"schema_version", "tool", "method_version", "report_kind", "calibration_status",
                            "index_sha256", "logical_index_sha256", "source_sha256", "method", "summary", "pairs", "payload_sha256"}
    assert set(payload["method"]) == {"tokenizer_id", "unicode_version", "shingle_k", "minimum_tokens", "tier_metric",
                                       "low_threshold_numerator", "low_threshold_denominator", "high_threshold_numerator", "high_threshold_denominator"}
    assert set(payload["summary"]) == {"potential_pairs", "unassessed_pairs", "assessed_pairs", "no_overlap_pairs",
                                        "below_0_35_pairs", "containment_0_35_to_0_60_pairs", "containment_at_least_0_60_pairs",
                                        "reported_pairs", "indexed_documents", "eligible_documents", "unassessed_documents", "tied_best_count"}
    pair = payload["pairs"][0]
    assert pair["query_stage"] is None and pair["reference_stage"] is None and pair["pair_containment_direction"] is None
    assert isinstance(pair["query_tokens"], int) and isinstance(pair["containment"], float)
    seal = payload.pop("payload_sha256")
    assert seal == hashlib.sha256(sd._canonical(payload)).hexdigest()


def test_same_index_fresh_query_and_batch_reports_are_byte_deterministic(tmp_path: Path) -> None:
    _manifest, index, build = _index(tmp_path, _base_rows(), name="deterministic")
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("ctl", 107).encode())
    q1 = tmp_path / "q1.json"; q2 = tmp_path / "q2.json"
    for output in (q1, q2):
        _receipt(_run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                      "--query-file", str(query), "--query-id", "query", "--report-out", str(output)))
    assert q1.read_bytes() == q2.read_bytes()
    b1 = tmp_path / "b1.json"; b2 = tmp_path / "b2.json"
    for output, state in ((b1, tmp_path / "b1-state"), (b2, tmp_path / "b2-state")):
        _receipt(_run("batch-report", "--index", str(index), "--index-sha256", _pin(build),
                      "--report-out", str(output), "--checkpoint-dir", str(state)))
    assert b1.read_bytes() == b2.read_bytes()


def test_candidate_amplification_refuses_without_partial_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": f"doc{number}", "draft_id": "draft", "stage": f"s{number}", "stage_order": number,
         "text": _tokens("amplify", 8)}
        for number in range(3)
    ]
    manifest = tmp_path / "records.jsonl"; index = tmp_path / "index.sqlite"; state = tmp_path / "build-state"
    _manifest(manifest, rows)
    receipt = sd._build_index(manifest, index, state, resume=False)
    monkeypatch.setattr(sd, "MAX_PAIR_COUNT", 1)
    report = tmp_path / "report.json"
    with pytest.raises(sd.Refusal):
        sd._batch(index, _pin(receipt), report, tmp_path / "batch-state", resume=False)
    assert not report.exists()


def test_posting_fanout_ceiling_refuses_without_partial_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": f"doc{number}", "draft_id": f"draft{number}", "stage": "s", "stage_order": 0,
         "text": _tokens("fanout", 8)}
        for number in range(3)
    ]
    manifest = tmp_path / "records.jsonl"; index = tmp_path / "index.sqlite"
    _manifest(manifest, rows)
    monkeypatch.setattr(sd, "MAX_POSTING_FANOUT", 2)
    with pytest.raises(sd.Refusal):
        sd._build_index(manifest, index, tmp_path / "state", resume=False)
    assert not index.exists()


def test_all_short_build_and_unknown_checkpoint_entry_fail_closed(tmp_path: Path) -> None:
    rows = [{"id": "short", "draft_id": "draft", "stage": "s", "stage_order": 0, "text": _tokens("s", 7)}]
    manifest = tmp_path / "all-short.jsonl"
    index = tmp_path / "all-short.sqlite"
    _manifest(manifest, rows)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not index.exists()


def test_checkpoint_shards_are_sealed_and_resume_ignores_reserved_temp(tmp_path: Path) -> None:
    rows = _base_rows()[:2]
    manifest, index, first = _index(tmp_path, rows, name="resume")
    state = tmp_path / "resume-state"
    shard_names = sorted(item.name for item in state.iterdir())
    assert any(name.startswith("inventory-") and name.endswith(".sqlite") for name in shard_names)
    assert any(name.startswith("build-") and name.endswith(".sqlite") for name in shard_names)
    assert not any(name.endswith(suffix) for name in shard_names for suffix in ("-wal", "-shm", "-journal"))
    connection = sqlite3.connect(index)
    try:
        assert connection.execute("PRAGMA application_id").fetchone() == (1397244977,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    finally:
        connection.close()
    connection = sqlite3.connect(state / "inventory-00000000.sqlite")
    try:
        assert connection.execute("PRAGMA application_id").fetchone() == (1397244721,)
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    finally:
        connection.close()
    # Simulate an interruption residue.  It has an allowed reserved-temp name,
    # but must never be opened or trusted by resume.
    (state / ".tmp-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa").write_bytes(b"unpublished")
    index.unlink()
    resumed = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                   "--checkpoint-dir", str(state), "--resume")
    resumed_receipt = _receipt(resumed)
    # SQLite page bytes are pinned per artifact but deliberately are not a
    # cross-build determinism promise; logical content is the stable identity.
    assert first["logical_index_sha256"] == resumed_receipt["logical_index_sha256"]

    batch_state = tmp_path / "batch-resume-state"
    first_report = tmp_path / "first-batch.json"
    result = _run("batch-report", "--index", str(index), "--index-sha256", _pin(resumed_receipt),
                  "--report-out", str(first_report), "--checkpoint-dir", str(batch_state))
    _receipt(result)
    batch_bytes = first_report.read_bytes()
    assert any(item.name.startswith("batch-") and item.name.endswith(".sqlite") for item in batch_state.iterdir())
    (batch_state / ".tmp-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb").write_bytes(b"unpublished")
    first_report.unlink()
    resumed_batch = _run("batch-report", "--index", str(index), "--index-sha256", _pin(resumed_receipt),
                         "--report-out", str(first_report), "--checkpoint-dir", str(batch_state), "--resume")
    _receipt(resumed_batch)
    assert first_report.read_bytes() == batch_bytes
    state = tmp_path / "bad-state"
    state.mkdir()
    (state / "unknown.bin").write_bytes(b"not a checkpoint")
    previous_index = index.read_bytes()
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(state), "--resume")
    assert result.returncode == 3 and index.read_bytes() == previous_index


def test_resume_rejects_checkpoint_seal_corruption(tmp_path: Path) -> None:
    manifest, index, _receipt_build = _index(tmp_path, _base_rows()[:2], name="sealed")
    state = tmp_path / "sealed-state"
    shard = state / "inventory-00000000.sqlite"
    with sqlite3.connect(shard) as connection:
        connection.execute(
            "UPDATE checkpoint_meta SET value=? WHERE key='checkpoint_sha256'",
            ("f" * 64,),
        )
        connection.commit()
    index.unlink()
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(state), "--resume")
    assert result.returncode == 3 and not index.exists()


def test_resume_rejects_changed_manifest_pin(tmp_path: Path) -> None:
    manifest, index, _receipt_build = _index(tmp_path, _base_rows()[:2], name="changed")
    state = tmp_path / "changed-state"
    index.unlink()
    changed = _base_rows()[:2]
    changed[0]["text"] = _tokens("replacement", 107)
    _manifest(manifest, changed)
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(state), "--resume")
    assert result.returncode == 3 and not index.exists()


def test_build_checkpoint_chunks_over_250_descriptors_and_resume(tmp_path: Path) -> None:
    rows = [
        {"id": f"doc{number:04d}", "draft_id": f"draft{number:04d}", "stage": "s", "stage_order": 0,
         "text": _tokens(f"chunk{number:04d}", 8)}
        for number in range(251)
    ]
    manifest, index, first = _index(tmp_path, rows, name="chunked")
    state = tmp_path / "chunked-state"
    names = {entry.name for entry in state.iterdir()}
    assert {"inventory-00000000.sqlite", "inventory-00000001.sqlite", "build-00000000.sqlite", "build-00000001.sqlite"} <= names
    with sqlite3.connect(state / "inventory-00000000.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM inventory").fetchone() == (250,)
    with sqlite3.connect(state / "inventory-00000001.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM inventory").fetchone() == (1,)
    index.unlink()
    resumed = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                   "--checkpoint-dir", str(state), "--resume", timeout=60)
    receipt = _receipt(resumed)
    assert receipt["logical_index_sha256"] == first["logical_index_sha256"]


def test_query_251_candidates_emits_aggregate_progress_and_receipt_summary(tmp_path: Path) -> None:
    text = _tokens("progress", 8)
    rows = [
        {"id": f"doc{number:04d}", "draft_id": f"draft{number:04d}", "stage": "s", "stage_order": 0, "text": text}
        for number in range(251)
    ]
    _manifest, index, build = _index(tmp_path, rows, name="progress")
    query = tmp_path / "query.txt"; query.write_bytes(text.encode())
    report = tmp_path / "query.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report), timeout=60)
    receipt = _receipt(result)
    progress = [json.loads(line) for line in result.stderr.splitlines() if line]
    assert {"phase": "query", "processed": 250}.items() <= progress[0].items()
    assert receipt["summary"]["potential_pairs"] == 251


def test_batch_checkpoint_splits_over_250_report_pairs(tmp_path: Path) -> None:
    rows = [
        {"id": f"stage{number:02d}", "draft_id": "draft", "stage": f"s{number:02d}", "stage_order": number,
         "text": _tokens("same" if number < 2 else f"different{number:02d}", 8)}
        for number in range(23)
    ]
    _manifest, index, receipt = _index(tmp_path, rows, name="batch-chunk")
    report = tmp_path / "batch.json"
    state = tmp_path / "batch-state"
    result = _run("batch-report", "--index", str(index), "--index-sha256", _pin(receipt),
                  "--report-out", str(report), "--checkpoint-dir", str(state))
    _receipt(result)
    assert {"batch-00000000.sqlite", "batch-00000001.sqlite"} <= {entry.name for entry in state.iterdir()}
    assert len(json.loads(report.read_bytes())["pairs"]) == 1
    declared: list[dict[str, int]] = []
    for name in ("batch-00000000.sqlite", "batch-00000001.sqlite"):
        with sqlite3.connect(state / name) as connection:
            meta = dict(connection.execute("SELECT key,value FROM checkpoint_meta"))
        counters = {key: int(meta[key]) for key in (
            "potential_pairs", "unassessed_pairs", "assessed_pairs", "no_overlap_pairs", "below_0_35_pairs",
            "containment_0_35_to_0_60_pairs", "containment_at_least_0_60_pairs", "reported_pairs",
        )}
        assert counters["potential_pairs"] == counters["unassessed_pairs"] + counters["assessed_pairs"]
        assert counters["assessed_pairs"] == (counters["no_overlap_pairs"] + counters["below_0_35_pairs"] +
                                                counters["containment_0_35_to_0_60_pairs"] + counters["containment_at_least_0_60_pairs"])
        assert counters["reported_pairs"] == counters["containment_0_35_to_0_60_pairs"] + counters["containment_at_least_0_60_pairs"]
        declared.append(counters)
    assert sum(item["potential_pairs"] for item in declared) == 253
    assert sum(item["reported_pairs"] for item in declared) == 1


def test_batch_resume_restores_shards_without_rescoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _manifest, index, receipt = _index(tmp_path, _base_rows()[:2], name="resume-no-score")
    first = tmp_path / "first.json"
    state = tmp_path / "state"
    sd._batch(index, _pin(receipt), first, state, resume=False)
    original = first.read_bytes()
    first.unlink()
    def forbidden(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("resume must consume sealed batch shards, not rescore")
    monkeypatch.setattr(sd, "_pair", forbidden)
    sd._batch(index, _pin(receipt), first, state, resume=True)
    assert first.read_bytes() == original


def test_batch_resume_recomputes_only_when_terminal_shard_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": f"stage{number:02d}", "draft_id": "draft", "stage": f"s{number:02d}", "stage_order": number,
         "text": _tokens("same" if number < 2 else f"different{number:02d}", 8)}
        for number in range(23)
    ]
    manifest = tmp_path / "records.jsonl"; index = tmp_path / "index.sqlite"; state = tmp_path / "state"; report = tmp_path / "report.json"
    _manifest(manifest, rows)
    receipt = sd._build_index(manifest, index, tmp_path / "build-state", resume=False)
    sd._batch(index, _pin(receipt), report, state, resume=False)
    expected = report.read_bytes()
    (state / "batch-00000001.sqlite").unlink(); report.unlink()
    calls = 0; original = sd._pair
    def counted(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(sd, "_pair", counted)
    sd._batch(index, _pin(receipt), report, state, resume=True)
    assert calls == 3
    assert report.read_bytes() == expected


def test_build_resume_reuses_build_shards_without_rescoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _base_rows()[:2]
    manifest = tmp_path / "records.jsonl"
    index = tmp_path / "index.sqlite"
    state = tmp_path / "state"
    _manifest(manifest, rows)
    first = sd._build_index(manifest, index, state, resume=False)
    index.unlink()
    def forbidden(*_args: object, **_kwargs: object) -> set[bytes]:
        raise AssertionError("resume must consume build shard postings, not reshingle")
    monkeypatch.setattr(sd, "_shingle_digests", forbidden)
    monkeypatch.setattr(sd, "_tokens", forbidden)
    resumed = sd._build_index(manifest, index, state, resume=True)
    assert resumed["logical_index_sha256"] == first["logical_index_sha256"]


def test_build_resume_rescores_only_missing_final_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"id": f"doc{number:04d}", "draft_id": f"draft{number:04d}", "stage": "s", "stage_order": 0,
         "text": _tokens(f"resume{number:04d}", 8)}
        for number in range(251)
    ]
    manifest = tmp_path / "records.jsonl"; index = tmp_path / "index.sqlite"; state = tmp_path / "state"
    _manifest(manifest, rows)
    first = sd._build_index(manifest, index, state, resume=False)
    (state / "build-00000001.sqlite").unlink()
    index.unlink()
    calls = 0; original = sd._shingle_digests
    def counted(tokens: list[str]) -> set[bytes]:
        nonlocal calls
        calls += 1
        return original(tokens)
    monkeypatch.setattr(sd, "_shingle_digests", counted)
    resumed = sd._build_index(manifest, index, state, resume=True)
    assert calls == 1
    assert resumed["logical_index_sha256"] == first["logical_index_sha256"]


def test_build_resume_from_inventory_only_scores_all_unbuilt_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _base_rows()[:2]
    manifest = tmp_path / "records.jsonl"; index = tmp_path / "index.sqlite"; state = tmp_path / "state"
    _manifest(manifest, rows)
    sd._build_index(manifest, index, state, resume=False)
    for shard in state.glob("build-*.sqlite"):
        shard.unlink()
    index.unlink()
    calls = 0; original = sd._shingle_digests
    def counted(tokens: list[str]) -> set[bytes]:
        nonlocal calls
        calls += 1
        return original(tokens)
    monkeypatch.setattr(sd, "_shingle_digests", counted)
    sd._build_index(manifest, index, state, resume=True)
    assert calls == len(rows)


def test_inventory_crash_preserves_first_250_and_resume_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"id": f"doc{number:03d}", "draft_id": f"draft{number:03d}", "stage": "s",
         "stage_order": 0, "text": _tokens(f"token{number:03d}", 8)}
        for number in range(251)
    ]
    manifest = tmp_path / "inventory-crash.jsonl"; _manifest(manifest, rows)
    index = tmp_path / "inventory-crash.sqlite"; state = tmp_path / "inventory-crash-state"
    original = sd._materialize_descriptors
    inventory_calls = 0

    def crash_on_second_inventory(descriptors, root, *, compute_shingles):
        nonlocal inventory_calls
        if not compute_shingles:
            inventory_calls += 1
            if inventory_calls == 2:
                raise sd.Refusal()
        return original(descriptors, root, compute_shingles=compute_shingles)

    monkeypatch.setattr(sd, "_materialize_descriptors", crash_on_second_inventory)
    with pytest.raises(sd.Refusal):
        sd._build_index(manifest, index, state, resume=False)
    first = state / "inventory-00000000.sqlite"
    first_bytes = first.read_bytes()
    assert not (state / "inventory-00000001.sqlite").exists()
    assert not index.exists()

    resumed_inventory_sizes: list[int] = []

    def record_resume(descriptors, root, *, compute_shingles):
        items = list(descriptors)
        if not compute_shingles:
            resumed_inventory_sizes.append(len(items))
        return original(items, root, compute_shingles=compute_shingles)

    monkeypatch.setattr(sd, "_materialize_descriptors", record_resume)
    receipt = sd._build_index(manifest, index, state, resume=True)
    assert receipt["indexed_documents"] == 251
    assert resumed_inventory_sizes == [250, 1]
    assert first.read_bytes() == first_bytes
    assert (state / "inventory-00000001.sqlite").exists()


def test_pin_corruption_and_unknown_sqlite_object_refuse_before_report(tmp_path: Path) -> None:
    _manifest_rows, index, build = _index(tmp_path, _base_rows()[:2])
    query = tmp_path / "query.txt"
    query.write_bytes(_tokens("ctl", 107).encode())
    report = tmp_path / "report.json"
    wrong = "0" * 64 if _pin(build) != "0" * 64 else "1" * 64
    result = _run("query-doc", "--index", str(index), "--index-sha256", wrong,
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()
    with sqlite3.connect(index) as connection:
        connection.execute("CREATE TABLE extra_object(value TEXT)")
        connection.commit()
    exact_corrupt_pin = hashlib.sha256(index.read_bytes()).hexdigest()
    result = _run("query-doc", "--index", str(index), "--index-sha256", exact_corrupt_pin,
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


def test_index_sidecar_and_application_version_mutations_refuse(tmp_path: Path) -> None:
    _manifest_rows, index, build = _index(tmp_path, _base_rows()[:2])
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("ctl", 107).encode())
    report = tmp_path / "report.json"
    sidecar = index.with_name(index.name + "-wal"); sidecar.write_bytes(b"sidecar")
    result = _run("query-doc", "--index", str(index), "--index-sha256", _pin(build),
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


@pytest.mark.parametrize("statement", [
    "PRAGMA application_id=1",
    "PRAGMA user_version=2",
    "UPDATE meta SET value='2' WHERE key='method_version'",
    "UPDATE meta SET value='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff' "
    "WHERE key='logical_sha256'",
    "UPDATE meta SET value='0.0' WHERE key='unicode_version'",
    "UPDATE meta SET value='999' WHERE key='document_count'",
])
def test_index_meta_mutations_refuse_even_with_new_raw_pin(tmp_path: Path, statement: str) -> None:
    _manifest_rows, index, _build = _index(tmp_path, _base_rows()[:2])
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("ctl", 107).encode())
    report = tmp_path / "report.json"
    with sqlite3.connect(index) as connection:
        connection.execute(statement); connection.commit()
    result = _run("query-doc", "--index", str(index), "--index-sha256", hashlib.sha256(index.read_bytes()).hexdigest(),
                  "--query-file", str(query), "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


@pytest.mark.parametrize("statement", [
    "UPDATE documents SET content_sha256=x'00' WHERE doc_id=(SELECT doc_id FROM documents LIMIT 1)",
    "UPDATE documents SET content_sha256='not-a-blob' WHERE doc_id=(SELECT doc_id FROM documents LIMIT 1)",
    "UPDATE postings SET shingle_sha256=x'00' WHERE (shingle_sha256,doc_id)="
    "(SELECT shingle_sha256,doc_id FROM postings LIMIT 1)",
])
def test_index_digest_length_and_storage_type_mutations_refuse(
    tmp_path: Path, statement: str,
) -> None:
    _manifest_rows, index, _build = _index(tmp_path, _base_rows()[:2], name="digest-mutation")
    with sqlite3.connect(index) as connection:
        connection.execute(statement); connection.commit()
    query = tmp_path / "digest-query.txt"; query.write_bytes(_tokens("query", 8).encode())
    report = tmp_path / "digest-report.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256",
                  hashlib.sha256(index.read_bytes()).hexdigest(), "--query-file", str(query),
                  "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


def test_foreign_key_corruption_refuses_before_report(tmp_path: Path) -> None:
    _manifest_rows, index, _build = _index(tmp_path, _base_rows()[:2], name="fk-corrupt")
    with sqlite3.connect(index) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("INSERT INTO postings VALUES(?,?)",
                           (hashlib.sha256(b"orphan-only-control").digest(), "missing"))
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        counts = {
            "posting_count": connection.execute("SELECT COUNT(*) FROM postings").fetchone()[0],
            "distinct_shingle_count": connection.execute(
                "SELECT COUNT(DISTINCT shingle_sha256) FROM postings"
            ).fetchone()[0],
            "maximum_posting_fanout": connection.execute(
                "SELECT COALESCE(MAX(n),0) FROM (SELECT COUNT(*) n FROM postings GROUP BY shingle_sha256)"
            ).fetchone()[0],
        }
        for key, value in counts.items():
            meta[key] = str(value)
            connection.execute("UPDATE meta SET value=? WHERE key=?", (str(value), key))
        connection.execute("UPDATE meta SET value=? WHERE key='logical_sha256'",
                           (sd._logical_seal(connection, meta),))
        connection.commit()
    report = tmp_path / "fk-report.json"
    query = tmp_path / "fk-query.txt"; query.write_bytes(_tokens("query", 8).encode())
    result = _run("query-doc", "--index", str(index), "--index-sha256",
                  hashlib.sha256(index.read_bytes()).hexdigest(), "--query-file", str(query),
                  "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


def test_deeply_nested_json_is_controlled_refusal_without_traceback_or_path(tmp_path: Path) -> None:
    manifest = tmp_path / "private-name.jsonl"
    manifest.write_bytes(b"[" * 10_000 + b"0" + b"]" * 10_000 + b"\n")
    index = tmp_path / "deep.sqlite"
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not index.exists()
    assert b"Traceback" not in result.stderr and str(manifest).encode() not in result.stderr


def test_json_recursion_error_is_explicitly_translated_to_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    def recursive(*_args: object, **_kwargs: object) -> None:
        raise RecursionError
    monkeypatch.setattr(sd.json, "loads", recursive)
    with pytest.raises(sd.Refusal):
        sd._strict_json("{}")


@pytest.mark.parametrize("receipt_also_fails", [False, True])
def test_memory_error_is_controlled_refusal(
    monkeypatch: pytest.MonkeyPatch, receipt_also_fails: bool,
) -> None:
    def exhausted(*_args: object, **_kwargs: object) -> None:
        raise MemoryError
    monkeypatch.setattr(sd, "_build_index", exhausted)
    if receipt_also_fails:
        monkeypatch.setattr(sd, "_console", exhausted)
    assert sd.main(["build-index", "--manifest", "m", "--index-out", "i",
                    "--checkpoint-dir", "c"]) == 3


def test_oversized_index_refuses_before_sqlite_open_or_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manifest_rows, index, build = _index(tmp_path, _base_rows()[:2], name="oversized")
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("query", 8).encode())
    report = tmp_path / "report.json"
    monkeypatch.setattr(sd, "MAX_INDEX_BYTES", index.stat().st_size - 1)

    def forbidden_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("oversized index must refuse before SQLite")

    monkeypatch.setattr(sd.sqlite3, "connect", forbidden_connect)
    with pytest.raises(sd.Refusal):
        sd._query(index, _pin(build), query, "q", report)
    assert not report.exists()


@pytest.mark.parametrize("order", [-(2**63) - 1, 2**63])
def test_stage_order_outside_signed_64_bit_refuses_ingestion(
    tmp_path: Path, order: int,
) -> None:
    manifest = tmp_path / "order.jsonl"; index = tmp_path / "order.sqlite"
    _manifest(manifest, [{"id": "doc", "draft_id": "draft", "stage": "stage",
                          "stage_order": order, "text": _tokens("order", 8)}])
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3 and not index.exists()


@pytest.mark.parametrize("stored", [str(-(2**63) - 1), str(2**63)])
def test_stage_order_outside_signed_64_bit_refuses_index_validation(
    tmp_path: Path, stored: str,
) -> None:
    _manifest_rows, index, _build = _index(tmp_path, _base_rows()[:2], name="bad-index-order")
    with sqlite3.connect(index) as connection:
        connection.execute("UPDATE documents SET stage_order=? WHERE doc_id=(SELECT doc_id FROM documents LIMIT 1)",
                           (stored,))
        connection.commit()
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("query", 8).encode())
    report = tmp_path / "report.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256",
                  hashlib.sha256(index.read_bytes()).hexdigest(), "--query-file", str(query),
                  "--query-id", "q", "--report-out", str(report))
    assert result.returncode == 3 and not report.exists()


def test_batch_semantics_are_invariant_to_manifest_order(tmp_path: Path) -> None:
    rows = _base_rows()[:3]
    semantic: list[tuple[dict[str, object], list[dict[str, object]]]] = []
    for name, ordered in (("forward", rows), ("reverse", list(reversed(rows)))):
        _manifest_path, index, build = _index(tmp_path, ordered, name=name)
        report = tmp_path / f"{name}.json"
        _receipt(_run("batch-report", "--index", str(index), "--index-sha256", _pin(build),
                      "--report-out", str(report), "--checkpoint-dir", str(tmp_path / f"{name}-batch")))
        payload = json.loads(report.read_bytes())
        semantic.append((payload["summary"], payload["pairs"]))
    assert semantic[0] == semantic[1]


def test_source_output_aliases_refuse_before_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "alias.jsonl"
    _manifest(manifest, _base_rows()[:1])
    before = manifest.read_bytes()
    with pytest.raises(sd.Refusal):
        sd._build_index(manifest, manifest, tmp_path / "unused-state", resume=False)
    assert manifest.read_bytes() == before and not (tmp_path / "unused-state").exists()

    hardlink = tmp_path / "different-spelling.jsonl"; hardlink.hardlink_to(manifest)
    def forbidden_score(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("alias refusal must precede scoring")
    original = sd._materialize_descriptors
    sd._materialize_descriptors = forbidden_score  # type: ignore[assignment]
    try:
        with pytest.raises(sd.Refusal):
            sd._build_index(manifest, hardlink, tmp_path / "hardlink-state", resume=False)
    finally:
        sd._materialize_descriptors = original
    assert hardlink.read_bytes() == before and not (tmp_path / "hardlink-state").exists()
    source_index = tmp_path / "alias-index.sqlite"
    build = sd._build_index(manifest, source_index, tmp_path / "build-state", resume=False)
    query = tmp_path / "query.txt"; query.write_bytes(_tokens("alias-query", 8).encode())
    query_before = query.read_bytes()
    query_alias = tmp_path / "query-output-hardlink.json"; query_alias.hardlink_to(query)
    with monkeypatch.context() as guarded:
        def forbidden_loader(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("alias refusal must precede index loading")
        def forbidden_tokens(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("alias refusal must precede scoring")
        guarded.setattr(sd, "_load_index", forbidden_loader)
        guarded.setattr(sd, "_tokens", forbidden_tokens)
        with pytest.raises(sd.Refusal):
            sd._query(source_index, _pin(build), query, "q", query_alias)
    assert query.read_bytes() == query_before and query_alias.read_bytes() == query_before


def test_resume_rejects_changed_method_and_changed_batch_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, index, build = _index(tmp_path, _base_rows()[:2], name="method-pin")
    build_state = tmp_path / "method-pin-state"
    index.unlink()
    original_config = sd._config_sha256
    monkeypatch.setattr(sd, "_config_sha256", lambda: "f" * 64)
    with pytest.raises(sd.Refusal):
        sd._build_index(manifest, index, build_state, resume=True)
    assert not index.exists()
    monkeypatch.setattr(sd, "_config_sha256", original_config)

    # Rebuild two distinct pinned indexes and prove a completed checkpoint for
    # the first cannot be resumed against the second.
    first_manifest, first_index, first_build = _index(tmp_path, _base_rows()[:2], name="first-pin")
    del first_manifest
    batch_state = tmp_path / "resume-batch-state"; first_report = tmp_path / "first-report.json"
    sd._batch(first_index, _pin(first_build), first_report, batch_state, resume=False)
    changed_rows = _base_rows()[:2]; changed_rows[1]["text"] = _tokens("changed", 8)
    _second_manifest, second_index, second_build = _index(tmp_path, changed_rows, name="second-pin")
    resumed_report = tmp_path / "resumed-report.json"
    with pytest.raises(sd.Refusal):
        sd._batch(second_index, _pin(second_build), resumed_report, batch_state, resume=True)
    assert not resumed_report.exists()


@pytest.mark.parametrize("ceiling", [
    "MAX_MANIFEST_BYTES", "MAX_LINE_BYTES", "MAX_DESCRIPTORS",
    "MAX_DOCUMENT_BYTES", "MAX_TOKENS", "MAX_SHINGLES_PER_DOCUMENT",
    "MAX_TOTAL_DOCUMENT_BYTES", "MAX_TOTAL_TOKENS", "MAX_POSTINGS",
    "MAX_DISTINCT_SHINGLES", "MAX_POSTING_FANOUT", "MAX_INDEX_BYTES",
    "MAX_QUERY_BYTES", "MAX_QUERY_TOKENS", "MAX_QUERY_SHINGLES",
    "MAX_EMITTED_PAIRS", "MAX_POSTINGS_VISITED", "MAX_CANDIDATE_DOCUMENTS",
    "MAX_PAIR_COUNTER_INCREMENTS", "MAX_PAIR_COUNT", "MAX_REPORT_BYTES",
])
def test_declared_ceiling_accepts_boundary_and_refuses_one_over_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ceiling: str,
) -> None:
    row = {"id": "doc", "draft_id": "draft", "stage": "s0", "stage_order": 0,
           "text": _tokens("bound", 8)}
    manifest = tmp_path / "boundary.jsonl"; _manifest(manifest, [row])
    raw_manifest = manifest.read_bytes(); row_bytes = raw_manifest.rstrip(b"\n")

    if ceiling in {"MAX_MANIFEST_BYTES", "MAX_LINE_BYTES", "MAX_DESCRIPTORS"}:
        boundary = {"MAX_MANIFEST_BYTES": len(raw_manifest), "MAX_LINE_BYTES": len(row_bytes),
                    "MAX_DESCRIPTORS": 1}[ceiling]
        monkeypatch.setattr(sd, ceiling, boundary)
        assert len(sd._parse_manifest_descriptors(manifest)[0]) == 1
        monkeypatch.setattr(sd, ceiling, boundary - 1)
        with pytest.raises(sd.Refusal): sd._parse_manifest_descriptors(manifest)
        return

    descriptors, _pin_unused = sd._parse_manifest_descriptors(manifest)
    if ceiling in {"MAX_DOCUMENT_BYTES", "MAX_TOKENS", "MAX_SHINGLES_PER_DOCUMENT"}:
        boundary = {"MAX_DOCUMENT_BYTES": len(row["text"].encode()), "MAX_TOKENS": 8,
                    "MAX_SHINGLES_PER_DOCUMENT": 1}[ceiling]
        monkeypatch.setattr(sd, ceiling, boundary)
        assert len(sd._materialize_descriptors(descriptors, tmp_path, compute_shingles=True)[0]) == 1
        monkeypatch.setattr(sd, ceiling, boundary - 1)
        with pytest.raises(sd.Refusal):
            sd._materialize_descriptors(descriptors, tmp_path, compute_shingles=True)
        return

    if ceiling in {"MAX_TOTAL_DOCUMENT_BYTES", "MAX_TOTAL_TOKENS", "MAX_POSTINGS",
                   "MAX_DISTINCT_SHINGLES", "MAX_POSTING_FANOUT"}:
        boundary = {"MAX_TOTAL_DOCUMENT_BYTES": len(row["text"].encode()), "MAX_TOTAL_TOKENS": 8,
                    "MAX_POSTINGS": 1, "MAX_DISTINCT_SHINGLES": 1,
                    "MAX_POSTING_FANOUT": 1}[ceiling]
        monkeypatch.setattr(sd, ceiling, boundary)
        sd._build_index(manifest, tmp_path / "at-limit.sqlite", tmp_path / "at-limit-state", resume=False)
        monkeypatch.setattr(sd, ceiling, boundary - 1)
        refused = tmp_path / "over-limit.sqlite"
        with pytest.raises(sd.Refusal):
            sd._build_index(manifest, refused, tmp_path / "over-limit-state", resume=False)
        assert not refused.exists()
        return

    if ceiling == "MAX_INDEX_BYTES":
        _manifest_path, index, receipt = _index(tmp_path, [row], name="index-limit")
        raw = index.read_bytes(); boundary = len(raw)
        monkeypatch.setattr(sd, ceiling, boundary)
        connection, _meta = sd._load_index_bytes(raw, _pin(receipt)); connection.close()
        monkeypatch.setattr(sd, ceiling, boundary - 1)
        with pytest.raises(sd.Refusal): sd._load_index_bytes(raw, _pin(receipt))
        return

    if ceiling == "MAX_REPORT_BYTES":
        payload = {"control": "value"}; boundary = len(sd._canonical(payload))
        monkeypatch.setattr(sd, ceiling, boundary)
        sd._write_report(tmp_path / "at-limit.json", payload)
        monkeypatch.setattr(sd, ceiling, boundary - 1)
        refused = tmp_path / "over-limit.json"
        with pytest.raises(sd.Refusal): sd._write_report(refused, payload)
        assert not refused.exists()
        return

    if ceiling == "MAX_PAIR_COUNT":
        rows = [row, {**row, "id": "doc2", "stage": "s1", "stage_order": 1}]
        _manifest_path, index, receipt = _index(tmp_path, rows, name="pair-limit")
        monkeypatch.setattr(sd, ceiling, 1)
        sd._batch(index, _pin(receipt), tmp_path / "at-limit.json", tmp_path / "batch-ok", resume=False)
        monkeypatch.setattr(sd, ceiling, 0)
        refused = tmp_path / "over-limit.json"
        with pytest.raises(sd.Refusal):
            sd._batch(index, _pin(receipt), refused, tmp_path / "batch-bad", resume=False)
        assert not refused.exists()
        return

    _manifest_path, index, receipt = _index(tmp_path, [row], name=f"{ceiling}-index")
    query = tmp_path / f"{ceiling}.txt"; query.write_bytes(row["text"].encode())
    boundary = {"MAX_QUERY_BYTES": len(query.read_bytes()), "MAX_QUERY_TOKENS": 8,
                "MAX_QUERY_SHINGLES": 1, "MAX_EMITTED_PAIRS": 1,
                "MAX_POSTINGS_VISITED": 1, "MAX_CANDIDATE_DOCUMENTS": 1,
                "MAX_PAIR_COUNTER_INCREMENTS": 1}[ceiling]
    monkeypatch.setattr(sd, ceiling, boundary)
    sd._query(index, _pin(receipt), query, "query", tmp_path / "at-limit.json")
    monkeypatch.setattr(sd, ceiling, boundary - 1)
    refused = tmp_path / "over-limit.json"
    with pytest.raises(sd.Refusal): sd._query(index, _pin(receipt), query, "query", refused)
    assert not refused.exists()
