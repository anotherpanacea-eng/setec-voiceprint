"""Acceptance tests for the owner-corrections sidecar applier (spec 70).

All material here is synthetic.  In particular, rejection assertions exercise
only the applier's deliberately generic diagnostic, never a fixture value.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
APPLIER = SCRIPTS / "apply_owner_corrections.py"
REGISTRY = SCRIPTS / "normalize_author_registry.py"
EXPORT = SCRIPTS / "author_corpus_export.py"


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json_line(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _entry(
    root: Path,
    *,
    row_id: str = "row-1",
    path: str = "piece.txt",
    register: str = "personal",
    era: str = "pre_chatgpt",
    text: str = "Synthetic source text with enough ordinary words for a fixture.\n",
    **extra: object,
) -> dict[str, object]:
    text_path = root / path
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_bytes(text.encode("utf-8"))
    row: dict[str, object] = {
        "id": row_id,
        "path": path,
        "source_id": "source-1",
        "author": "Synthetic Author",
        "persona": "joshua",
        "register": register,
        "era": era,
        "date_written": "2019-01-02",
        "ai_status": "pre_ai_human",
        "language_status": "native",
        "word_count": 9,
        "use": ["voice_profile"],
        "split": "baseline",
        "privacy": "private",
        "content_hash": _sha(text.encode("utf-8")),
        "source": "synthetic",
        "corpus_role": "identity_baseline",
        "consent_status": "author_consent",
        "acquired_via": "synthetic-test",
    }
    row.update(extra)
    return row


def _manifest(root: Path, rows: list[dict[str, object]], *, raw: bytes | None = None) -> Path:
    path = root / "source.jsonl"
    path.write_bytes(raw if raw is not None else b"".join(_json_line(row) + b"\n" for row in rows))
    return path


def _correction(
    *,
    row_id: str = "row-1",
    rewrite: dict[str, object] | None = None,
    match: dict[str, object] | None = None,
    expect: dict[str, object] | None = None,
    note: str = "synthetic owner review",
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "setec-owner-correction/1",
        "match": match if match is not None else {"id": row_id},
        "rewrite": rewrite if rewrite is not None else {"register": "blog_essay", "era": "pre_ai_widespread"},
        "note": note,
    }
    if expect is not None:
        value["expect"] = expect
    value.update(extra)
    return value


def _sidecar(
    root: Path, rows: list[object], *, raw: bytes | None = None, name: str = "corrections.jsonl",
) -> Path:
    path = root / name
    path.write_bytes(raw if raw is not None else b"".join(_json_line(row) + b"\n" for row in rows))
    return path


def _run(manifest: Path, corrections: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(APPLIER), str(manifest), str(corrections), *args],
        cwd=str(manifest.parent), capture_output=True, check=False,
    )


def _summary(result: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1
    assert b"\r" not in result.stdout
    return json.loads(result.stdout)


def _assert_refusal(result: subprocess.CompletedProcess[bytes], *secrets: str) -> None:
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == (
        b"apply_owner_corrections: input, policy, or publication validation failed\n"
    )
    for secret in secrets:
        assert secret.encode("utf-8") not in result.stderr


def _canonical_rows(path: Path) -> list[dict[str, object]]:
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and b"\r" not in raw
    return [json.loads(line) for line in raw.splitlines()]


def _applier_module():
    spec = importlib.util.spec_from_file_location("b4_owner_corrections", APPLIER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_rewrite_is_canonical_source_preserving_and_audit_bound(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root, row_id="r-1"), _entry(root, row_id="r-2", path="two.txt")])
    source_before = source.read_bytes()
    corrections = _sidecar(root, [_correction(row_id="r-1", expect={"register": "personal"})])
    out = root / "corrected.jsonl"

    summary = _summary(_run(source, corrections, "--out", str(out)))
    rows = _canonical_rows(out)
    assert [row["id"] for row in rows] == ["r-1", "r-2"]
    assert rows[0]["register"] == "blog_essay"
    assert rows[0]["era"] == "pre_ai_widespread"
    assert rows[0]["path"] == "piece.txt"
    assert rows[1]["register"] == "personal"
    assert source.read_bytes() == source_before
    assert summary == {
        "already_applied": 0,
        "applied": 1,
        "corrections": 1,
        "corrections_sha256": _sha(corrections.read_bytes()),
        "dry_run": False,
        "input_manifest_sha256": _sha(source_before),
        "manifest_rows": 2,
        "output_sha256": _sha(out.read_bytes()),
        "schema": "setec-owner-corrections-result/1",
    }
    assert b"synthetic owner review" not in result_bytes(summary)


def result_bytes(value: dict[str, object]) -> bytes:
    """Encode a parsed aggregate only to make non-disclosure assertions obvious."""
    return json.dumps(value, sort_keys=True, ensure_ascii=True).encode("ascii")


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r", b"\r\n\n\r"])
def test_physical_newline_forms_canonicalize_without_unicode_record_splitting(
    tmp_path: Path, ending: bytes,
) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    row = _entry(root, row_id="id-\u00e9", path="nested/\u03c0.txt", notes="a\u0085b\u2028c\u2029d")
    raw = _json_line(row) + ending
    source = _manifest(root, [row], raw=raw)
    corrections = _sidecar(root, [_correction(row_id="id-\u00e9", note="note \u00e9 \u2028")])
    out = root / "corrected.jsonl"

    _summary(_run(source, corrections, "--out", str(out)))
    output = out.read_bytes()
    assert output.endswith(b"\n") and b"\r" not in output
    assert b"\\u0085" in output and b"\\u2028" in output and b"\\u2029" in output
    assert _canonical_rows(out)[0]["notes"] == "a\u0085b\u2028c\u2029d"


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b"\r", b""])
def test_sidecar_physical_newline_forms_produce_identical_output(
    tmp_path: Path, ending: bytes,
) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root)])
    correction = _correction()
    sidecar = _sidecar(root, [], raw=_json_line(correction) + ending)
    out = root / "corrected.jsonl"
    summary = _summary(_run(source, sidecar, "--out", str(out)))
    assert summary["applied"] == 1
    assert _canonical_rows(out)[0]["register"] == "blog_essay"


def test_missing_final_newline_and_sidecar_order_are_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    rows = [_entry(root, row_id="a"), _entry(root, row_id="b", path="b.txt")]
    source = _manifest(root, rows, raw=_json_line(rows[0]) + b"\r" + _json_line(rows[1]))
    first = _sidecar(root, [_correction(row_id="b"), _correction(row_id="a")], name="first-corrections.jsonl")
    second = _sidecar(root, [_correction(row_id="a"), _correction(row_id="b")], name="second-corrections.jsonl")
    one, two = root / "one.jsonl", root / "two.jsonl"
    first_result = _summary(_run(source, first, "--out", str(one)))
    second_result = _summary(_run(source, second, "--out", str(two)))
    assert one.read_bytes() == two.read_bytes()
    assert first_result["output_sha256"] == second_result["output_sha256"]
    assert first_result["corrections_sha256"] != second_result["corrections_sha256"]


@pytest.mark.parametrize(
    "manifest_raw, correction_raw",
    [
        (b"\xef\xbb\xbf{}\n", _json_line(_correction())),
        (b"\xff\n", _json_line(_correction())),
        (b'{"id":"x","id":"shadow","path":"piece.txt"}', _json_line(_correction())),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","schema":"x","match":{"id":"x"},"rewrite":{"register":"personal"},"note":"n"}'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x","id":"y"},"rewrite":{"register":"personal"},"note":"n"}'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x"},"expect":{"register":"personal","register":"blog_essay"},"rewrite":{"register":"personal"},"note":"n"}'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x"},"rewrite":{"register":"personal","register":"blog_essay"},"note":"n"}'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x"},"rewrite":{"register":"personal"},"note":"n","note":"m"}'),
        (_json_line({"id": "x"}), b'[]'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x"},"rewrite":{"register":NaN},"note":"n"}'),
        (_json_line({"id": "x"}), b'{"schema":"setec-owner-correction/1","match":{"id":"x"},"rewrite":{"register":Infinity},"note":"n"}'),
        (b'{"id":"x","path":"piece.txt","extra":{"nested":[1e400]}}', _json_line(_correction(row_id="x"))),
    ],
)
def test_strict_json_refusals_publish_nothing(
    tmp_path: Path, manifest_raw: bytes, correction_raw: bytes,
) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [], raw=manifest_raw)
    corrections = _sidecar(root, [], raw=correction_raw)
    out = root / "corrected.jsonl"
    _assert_refusal(_run(source, corrections, "--out", str(out)), str(root), "shadow")
    assert not out.exists()


@pytest.mark.parametrize(
    "correction",
    [
        {},
        {"schema": "wrong", "match": {"id": "row-1"}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"path": " "}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"unknown": "x"}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": 1}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "expect": {"register": 1}, "rewrite": {"register": "personal"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {"persona": "other"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {"register": "not-a-register"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {"era": "not-an-era"}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {}, "note": "n"},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {"register": "personal"}, "note": " "},
        {"schema": "setec-owner-correction/1", "match": {"id": "row-1"}, "rewrite": {"register": "personal"}, "note": "n", "unknown": "x"},
    ],
)
def test_closed_schema_and_rewrite_policy_refuse(tmp_path: Path, correction: dict[str, object]) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    export_entry = _entry(root)
    export_entry.pop("source_id")
    export_entry["source"] = "imessage_local"
    source = _manifest(root, [export_entry])
    sidecar = _sidecar(root, [correction])
    _assert_refusal(_run(source, sidecar, "--out", str(root / "out.jsonl")))


@pytest.mark.parametrize(("row_id", "path"), [(1, "piece.txt"), ("row-1", 1), (" ", "piece.txt"), ("row-1", " ")])
def test_malformed_manifest_identity_types_refuse(
    tmp_path: Path, row_id: object, path: object,
) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    row = _entry(root)
    row["id"] = row_id
    row["path"] = path
    source = _manifest(root, [row])
    sidecar = _sidecar(root, [_correction()])
    _assert_refusal(_run(source, sidecar, "--out", str(root / "out.jsonl")))


def test_embedded_nul_path_is_a_controlled_refusal(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    row = _entry(root)
    row["path"] = "bad\x00name"
    source = _manifest(root, [row])
    sidecar = _sidecar(root, [_correction()])
    _assert_refusal(_run(source, sidecar, "--out", str(root / "out.jsonl")))


def test_match_identity_staleness_conflicts_and_idempotence(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root), _entry(root, row_id="row-1", path="two.txt")])
    sidecar = _sidecar(root, [_correction(match={"id": "row-1", "path": "piece.txt"})])
    _assert_refusal(_run(source, sidecar, "--out", str(root / "ambiguous.jsonl")))

    unique = _manifest(root, [_entry(root)])
    for rule in (
        _correction(match={"id": "missing"}),
        _correction(match={"id": "ROW-1"}),
        _correction(expect={"register": "blog_essay"}),
    ):
        _assert_refusal(_run(unique, _sidecar(root, [rule]), "--out", str(root / "bad.jsonl")))
    conflict = _sidecar(root, [_correction(), _correction(note="same target, separate rule")])
    _assert_refusal(_run(unique, conflict, "--out", str(root / "conflict.jsonl")))

    first = root / "first.jsonl"
    _summary(_run(unique, _sidecar(root, [_correction()]), "--out", str(first)))
    second = root / "second.jsonl"
    result = _summary(_run(first, _sidecar(root, [_correction(note="a changed rationale")]), "--out", str(second)))
    assert result["applied"] == 0 and result["already_applied"] == 1
    assert second.read_bytes() == first.read_bytes()
    assert b"a changed rationale" not in result_bytes(result)


def test_validator_errors_refuse_but_warnings_remain_advisory_and_private(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    warning_row = _entry(root, unknown_fixture_extension="advisory-only")
    warning_source = _manifest(root, [warning_row])
    warning_out = root / "warning.jsonl"
    result = _run(warning_source, _sidecar(root, [_correction()]), "--out", str(warning_out))
    summary = _summary(result)
    assert summary["applied"] == 1
    assert b"advisory-only" not in result.stdout

    error_root = tmp_path / "error-root"
    error_root.mkdir()
    error_source = _manifest(error_root, [_entry(error_root, word_count=-1)])
    error_out = error_root / "error.jsonl"
    _assert_refusal(
        _run(error_source, _sidecar(error_root, [_correction()]), "--out", str(error_out)),
        "word_count", str(error_root),
    )
    assert not error_out.exists()


def test_publication_modes_alias_guards_and_dry_run(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root)])
    sidecar = _sidecar(root, [_correction()])
    out = root / "out.jsonl"
    _summary(_run(source, sidecar, "--out", str(out), "--dry-run"))
    assert not out.exists()
    _summary(_run(source, sidecar, "--out", str(out)))
    old = out.read_bytes()
    _assert_refusal(_run(source, sidecar, "--out", str(out)))
    assert out.read_bytes() == old
    _summary(_run(source, sidecar, "--out", str(out), "--replace"))

    foreign = tmp_path / "foreign.jsonl"
    _assert_refusal(_run(source, sidecar, "--out", str(foreign)))
    _assert_refusal(_run(source, sidecar, "--out", str(source), "--replace"))
    _assert_refusal(_run(source, sidecar, "--out", str(sidecar), "--replace"))
    _assert_refusal(_run(source, sidecar, "--in-place", "--replace"))

    before = source.read_bytes()
    result = _summary(_run(source, sidecar, "--in-place"))
    assert result["applied"] == 1
    assert source.read_bytes() != before
    assert _canonical_rows(source)[0]["register"] == "blog_essay"


def test_input_hardlink_alias_is_rejected_before_replace(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root)])
    sidecar = _sidecar(root, [_correction()])
    alias = root / "source-alias.jsonl"
    try:
        os.link(source, alias)
    except OSError as exc:  # pragma: no cover - filesystems without hardlinks
        pytest.skip(f"hardlinks unavailable: {exc}")
    before = source.read_bytes()
    _assert_refusal(_run(source, sidecar, "--out", str(alias), "--replace"))
    assert source.read_bytes() == before and alias.read_bytes() == before


def test_output_cannot_replace_manifest_source_data_or_its_hardlink(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root)])
    sidecar = _sidecar(root, [_correction()])
    data = root / "piece.txt"
    before = data.read_bytes()

    _assert_refusal(_run(source, sidecar, "--out", str(data), "--replace"))
    assert data.read_bytes() == before

    alias = root / "piece-alias.txt"
    try:
        os.link(data, alias)
    except OSError as exc:  # pragma: no cover - filesystems without hardlinks
        pytest.skip(f"hardlinks unavailable: {exc}")
    _assert_refusal(_run(source, sidecar, "--out", str(alias), "--replace"))
    assert data.read_bytes() == before and alias.read_bytes() == before


def test_create_new_race_and_publication_failures_preserve_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the link-create-new and failure cleanup path without prose data."""
    module = _applier_module()
    root = tmp_path / "publish"
    root.mkdir()
    destination = root / "target.jsonl"
    old = b"existing-destination-bytes\n"

    def race_link(_temporary: Path, target: Path) -> None:
        target.write_bytes(old)
        raise FileExistsError("late destination")

    monkeypatch.setattr(module.os, "link", race_link)
    with pytest.raises(module.ControlledFailure):
        module._publish(destination, b"new bytes\n", replace=False)
    assert destination.read_bytes() == old
    assert not list(root.glob(".target.jsonl.*.tmp"))

    monkeypatch.undo()
    destination.write_bytes(old)
    monkeypatch.setattr(module.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync")))
    with pytest.raises(module.ControlledFailure):
        module._publish(destination, b"new bytes\n", replace=True)
    assert destination.read_bytes() == old
    assert not list(root.glob(".target.jsonl.*.tmp"))

    monkeypatch.undo()
    destination.write_bytes(old)
    monkeypatch.setattr(module.os, "replace", lambda _one, _two: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(module.ControlledFailure):
        module._publish(destination, b"new bytes\n", replace=True)
    assert destination.read_bytes() == old
    assert not list(root.glob(".target.jsonl.*.tmp"))


@pytest.mark.parametrize("failure_point", ["write", "flush"])
def test_write_and_flush_failures_preserve_destination_and_remove_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str,
) -> None:
    module = _applier_module()
    root = tmp_path / "publish"
    root.mkdir()
    destination = root / "target.jsonl"
    old = b"existing-destination-bytes\n"
    destination.write_bytes(old)
    real_fdopen = module.os.fdopen

    class FailingHandle:
        def __init__(self, descriptor: int) -> None:
            self.handle = real_fdopen(descriptor, "wb")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.handle.close()

        def write(self, content: bytes):
            if failure_point == "write":
                raise OSError("injected write failure")
            return self.handle.write(content)

        def flush(self) -> None:
            if failure_point == "flush":
                raise OSError("injected flush failure")
            self.handle.flush()

        def fileno(self) -> int:
            return self.handle.fileno()

    monkeypatch.setattr(module.os, "fdopen", lambda descriptor, _mode: FailingHandle(descriptor))
    with pytest.raises(module.ControlledFailure):
        module._publish(destination, b"new bytes\n", replace=True)
    assert destination.read_bytes() == old
    assert not list(root.glob(".target.jsonl.*.tmp"))


def test_registry_and_nondocument_export_consume_corrected_manifest(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    export_entry = _entry(root)
    export_entry.pop("source_id")
    export_entry["source"] = "imessage_local"
    source = _manifest(root, [export_entry])
    # The exporter requires acquisition metadata for its non-document source.
    (root / "piece.meta.json").write_text(json.dumps({
        "content_hash": export_entry["content_hash"],
        "author_corpus_group_locator": "sha256:" + "1" * 64,
        "author_corpus_entry_locator": "sha256:" + "2" * 64,
    }), encoding="utf-8")
    corrected = root / "corrected.jsonl"
    _summary(_run(source, _sidecar(root, [_correction()]), "--out", str(corrected)))

    spec = importlib.util.spec_from_file_location("b4_registry", REGISTRY)
    assert spec and spec.loader
    registry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry)
    records, _ = registry.build_registry(
        sources={"synthetic": corrected},
        register_map={("synthetic", "blog_essay"): "essay.blog"},
        canonical_persona="joshua",
    )
    assert records[0]["source_register"] == "blog_essay"

    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import author_corpus_export as exporter  # type: ignore

    records, _, receipt, _, _ = exporter.build_export(
        sources={"imessage_sent": corrected},
        register_map={"imessage_sent:blog_essay": "essay.blog"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
    )
    assert records[0]["register"] == "essay.blog"
    assert receipt["counts"]["by_era"] == {"pre_ai_widespread": 1}
    assert receipt["source_snapshot_sha256"]
    _, _, source_receipt, _, _ = exporter.build_export(
        sources={"imessage_sent": source},
        register_map={"imessage_sent:personal": "text.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
    )
    assert receipt["source_snapshot_sha256"] != source_receipt["source_snapshot_sha256"]


def test_old_document_attestation_refuses_corrected_manifest(tmp_path: Path) -> None:
    """The applier can write bytes, but must not make an old attestation valid."""
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root, register="literary_horror")])
    document_map = root / "document_map.jsonl"
    document_map.write_text(json.dumps({
        "schema": "setec-author-document-map/1", "source_id": "row-1",
        "private_document_locator": "sha256:" + "a" * 64,
        "private_entry_locator": "sha256:" + "b" * 64,
        "unit_kind": "chapter", "unit_index": 0, "unit_count": 1,
    }) + "\n", encoding="utf-8")
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import author_corpus_export as exporter  # type: ignore
    map_hash = exporter._load_document_map(document_map)[1]
    attestation = root / "document_attestation.json"
    attestation.write_text(json.dumps({
        "schema": "setec-author-document-attestation/1",
        "source_manifest_sha256": _sha(source.read_bytes()), "document_map_hash": map_hash,
        "persona": "joshua", "authorized_by": "joshua", "basis": "self",
        "attested_at": "2026-07-20T00:00:00+00:00", "legacy_persona_aliases": [],
        "author_identities": ["Synthetic Author"], "corpus_role": "identity_baseline",
        "use": ["voice_profile"], "consent_status": "author_consent",
        "allowed_ai_status": ["pre_ai_human"],
    }), encoding="utf-8")
    corrected = root / "corrected.jsonl"
    _summary(_run(source, _sidecar(root, [_correction(rewrite={"register": "literary_fiction"})]), "--out", str(corrected)))
    with pytest.raises(ValueError, match="attestation binding"):
        exporter.build_export(
            sources={"document_local": corrected},
            register_map={"document_local:literary_fiction": "fiction.literary"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
            document_map_path=document_map, document_attestation_path=attestation,
        )


def test_windows_portable_binary_contract_and_no_permission_dependency(tmp_path: Path) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    source = _manifest(root, [_entry(root)])
    sidecar = _sidecar(root, [_correction()])
    out = root / "output.jsonl"
    result = _run(source, sidecar, "--out", str(out))
    _summary(result)
    assert b"\r" not in result.stdout and b"\r" not in out.read_bytes()
    # The real subprocess is the Windows-relevant assertion: it must not need
    # POSIX-only chmod/fchmod/mode APIs merely to publish a canonical output.
    assert result.returncode == 0
