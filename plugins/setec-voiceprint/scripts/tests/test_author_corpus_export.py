from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import author_corpus_export as E  # type: ignore
import acquire_imessage_sent_atomic as A  # type: ignore
import setec_run  # type: ignore


ATOMIC_EXPORT_SEAM_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "imessage_atomic_export_seam"
)


def _source(root: Path, kind: str, text: str, *, ai_status: str = "pre_ai_human",
            stable: bool = True) -> Path:
    source_value = E.SOURCE_VALUES[kind]
    legacy = "personal"
    src = root / kind
    src.mkdir(parents=True, exist_ok=True)
    text_path = src / "piece.txt"
    text_path.write_text(text, encoding="utf-8")
    content_hash = E._sha(text.encode("utf-8"))
    meta = {"content_hash": content_hash}
    if stable and kind == "imessage_sent":
        meta.update({
            "author_corpus_group_locator": "sha256:" + "1" * 64,
            "author_corpus_entry_locator": "sha256:" + "2" * 64,
        })
    elif stable:
        meta.update({
            "author_corpus_thread_locator": "sha256:" + "3" * 64,
            "author_corpus_entry_locator": "sha256:" + "4" * 64,
            "author_corpus_order_timestamp": "2017-01-02T12:00:00+00:00",
        })
    text_path.with_suffix(".meta.json").write_text(
        json.dumps(meta), encoding="utf-8",
    )
    entry = {
        "id": f"{kind}-1", "path": "piece.txt", "author": "Joshua",
        "persona": "joshua", "register": legacy, "date_written": "2017-01-02",
        "ai_status": ai_status, "language_status": "native", "word_count": 8,
        "use": ["voice_profile"], "split": "baseline", "privacy": "private",
        "content_hash": content_hash, "source": source_value,
        "corpus_role": "identity_baseline", "era": "pre_chatgpt",
        "consent_status": "author_consent", "acquired_via": f"acquire_{kind}_1",
    }
    manifest = src / "draft_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return manifest


def _atomic_imessage_source(root: Path) -> Path:
    """Copy a public, acquirer-shaped atomic source into the private test root."""
    src = root / "imessage_sent_atomic"
    shutil.copytree(ATOMIC_EXPORT_SEAM_FIXTURE, src)
    return src / "draft_manifest.jsonl"


def _write_atomic_adjudication(
    manifest: Path, stems: list[str], *, decision_date: str = "2026-07-19",
) -> None:
    path = manifest.parent / A.ADJUDICATED_IDENTITY_EXCLUSIONS_FILENAME
    path.write_bytes(A._canonical_json_bytes({
        "schema": "setec-imessage-atomic-adjudicated-identity-exclusions/1",
        "rows": [{
            "row_stem": stem,
            "reason": "owner rejected identity-bearing row from corpus ingestion",
            "owner_decision_date": decision_date,
        } for stem in sorted(stems)],
    }))
    os.chmod(path, 0o600)


def _document_source(root: Path, *, count: int = 3):
    src = root / "document_local"
    src.mkdir(parents=True, exist_ok=True)
    group_locator = "sha256:" + "a" * 64
    manifest_rows = []
    map_rows = []
    for index in range(count):
        text = f"Chapter {index + 1}.\n\nThis is a natural multi-paragraph author unit.\n"
        path = src / f"chapter-{index + 1:03d}.txt"
        # Keep the fixture hash tied to the exact on-disk bytes on Windows,
        # where text-mode writes otherwise translate newlines.
        path.write_bytes(text.encode("utf-8"))
        source_id = f"legacy-chapter-{index + 1:03d}"
        manifest_rows.append({
            "id": source_id,
            "path": path.name,
            "author": "private-author-alias",
            "persona": "private-project-label",
            "register": "literary_horror",
            "date_written": "2019-01-01",
            "ai_status": "pre_ai_human",
            "content_hash": E._sha(text.encode("utf-8")),
            "corpus_role": "identity_baseline",
            "use": ["voice_profile"],
            "split": "baseline",
            "source": f"D:\\private\\novel.docx#ch{index + 1:02d}",
        })
        map_rows.append({
            "schema": "setec-author-document-map/1",
            "source_id": source_id,
            "private_document_locator": group_locator,
            "private_entry_locator": "sha256:" + f"{index + 1:064x}",
            "unit_kind": "chapter",
            "unit_index": index,
            "unit_count": count,
        })
    manifest = src / "draft_manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(row) + "\n" for row in manifest_rows),
        encoding="utf-8",
    )
    document_map = src / "document_map.jsonl"
    document_map.write_text(
        "".join(json.dumps(row) + "\n" for row in reversed(map_rows)),
        encoding="utf-8",
    )
    map_hash = E._document_map_hash(sorted(map_rows, key=lambda row: row["source_id"]))
    attestation = src / "document_attestation.json"
    attestation.write_text(json.dumps({
        "schema": "setec-author-document-attestation/1",
        "source_manifest_sha256": E._sha(manifest.read_bytes()),
        "document_map_hash": map_hash,
        "persona": "joshua",
        "authorized_by": "joshua",
        "basis": "self",
        "attested_at": "2026-07-11T00:00:00+00:00",
        "legacy_persona_aliases": ["private-project-label"],
        "author_identities": ["private-author-alias"],
        "corpus_role": "identity_baseline",
        "use": ["voice_profile"],
        "consent_status": "author_consent",
        "allowed_ai_status": ["pre_ai_human"],
    }), encoding="utf-8")
    return manifest, document_map, attestation


@pytest.fixture
def private_root(tmp_path: Path) -> Path:
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir(mode=0o700)
    return root


def _build(private_root: Path, *, gmail_stable: bool = True, key: bytes = b"k" * 32):
    im = _source(private_root, "imessage_sent", "Text message words in my own compact rhythm.")
    gm = _source(
        private_root, "gmail_sent",
        "Email message words in a somewhat more deliberate professional rhythm.",
        stable=gmail_stable,
    )
    return E.build_export(
        sources={"imessage_sent": im, "gmail_sent": gm},
        register_map={
            "gmail_sent:personal": "email.personal",
            "imessage_sent:personal": "text.personal",
        },
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=key,
    )


def _private_key(root: Path, name: str, data: bytes) -> Path:
    """Create a key that exercises the real platform privacy guard."""
    key = root / name
    key.write_bytes(data)
    if os.name == "nt":
        user = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(
            ["icacls", str(key), "/inheritance:r", "/grant:r", f"{user}:(F)"],
            check=True, capture_output=True, text=True,
        )
    else:
        key.chmod(0o600)
    return key


def test_frozen_crypto_preimages_and_hash_vectors():
    key = bytes(range(32))
    raw = "Café\r\nline\n".encode("utf-8")
    content_hash = E._sha(raw)
    normalized_hash = E._sha(E._normalize_text(raw.decode("utf-8")).encode("utf-8"))
    group = E._hmac(key, E.DOMAIN_GROUP, {
        "source_kind": "gmail_sent", "private_group_locator": "sha256:" + "1" * 64,
    }, "grp:hmac-sha256:")
    entry = E._hmac(key, E.DOMAIN_ENTRY, {
        "source_kind": "gmail_sent", "private_entry_locator": "sha256:" + "2" * 64,
        "content_sha256": content_hash,
    }, "src:hmac-sha256:")
    record = {
        "schema": E.RECORD_SCHEMA, "id": "", "persona": "joshua",
        "register": "email.personal", "role": "author",
        "text_path": f"texts/{content_hash[7:9]}/{content_hash[9:11]}/{content_hash[7:]}.txt",
        "source_entry_fingerprint": entry, "source_group": group,
        "conversation_id": None, "unit_kind": "email", "unit_index": 0,
        "unit_count": 1, "date": "2017-01-02",
        "corpus_role": "identity_baseline", "use": ["voice_profile"],
        "consent_status": "author_consent", "ai_status": "pre_ai_human",
        "source_kind": "gmail_sent", "content_sha256": content_hash,
        "normalized_text_sha256": normalized_hash,
    }
    record["id"] = E._record_id(record)
    snapshot = E._source_snapshot_hash([{
        "source_kind": "gmail_sent", "source_manifest_sha256": "sha256:" + "3" * 64,
        "source_id": "gmail-1", "content_sha256": content_hash,
        "private_group_locator": "sha256:" + "1" * 64,
        "private_entry_locator": "sha256:" + "2" * 64,
    }])
    receipt = {
        "schema": E.RECEIPT_SCHEMA, "surface": E.TOOL_NAME,
        "surface_version": E.SURFACE_VERSION, "producer_revision": "0" * 40,
        "source_snapshot_sha256": snapshot, "document_map_hash": None,
        "document_attestation_hash": None,
        "hmac_key_id": E._sha(E.DOMAIN_KEY_ID + key),
        "register_map": {"gmail_sent:personal": "email.personal"},
        "source_persona_aliases": {},
        "allowed_ai_status": ["pre_ai_human"],
        "entries": [{"source_entry_fingerprint": entry, "source_group": group,
                     "record_id": record["id"]}],
        "record_ids": [record["id"]], "package_hash": E._package_hash([record]),
        "counts": {"records": 1, "by_register": {"email.personal": 1},
                   "by_ai_status": {"pre_ai_human": 1},
                   "by_source_kind": {"gmail_sent": 1},
                   "by_era": {"pre_chatgpt": 1}},
        "record_atomic_degraded": False,
    }
    config_hash = E._digest(E.DOMAIN_CONFIG, {
        "producer_revision": receipt["producer_revision"],
        "source_snapshot_sha256": snapshot, "document_map_hash": None,
        "document_attestation_hash": None,
        "hmac_key_id": receipt["hmac_key_id"],
        "register_map": receipt["register_map"],
        "source_persona_aliases": receipt["source_persona_aliases"],
        "allowed_ai_status": receipt["allowed_ai_status"], "persona": "joshua",
    })
    assert receipt["hmac_key_id"] == "sha256:2a0ec87d15516b1aa6e3e3c85f6b2612ac2704b2994d0d2756fae0ab5cb2d0df"
    assert group == "grp:hmac-sha256:a054e85bd96acb55ff84d51cccfe23023545cee16ccf7e8437000ed7c39f62a1"
    assert entry == "src:hmac-sha256:7a5bea655cf090e739da86996be4f626dca8a12bab308044651764c532a07f81"
    assert content_hash == "sha256:1ce843ec991710b45d95e8a9869e3eff33043f872d74661ba5b52b99dacc0c3d"
    assert normalized_hash == "sha256:84b301e724478c998289edd154a620657ae807c178a4360bd4d0dfb9670d9d59"
    assert record["id"] == "sha256:2c36a69563460ee5ed16c146bbb86ae5d4594af9a094b0b3865f33a350982a9a"
    assert snapshot == "sha256:1bf1694953fc09554b0cc8e49830cb440f7e3f91212070739f8d6a85f9544f4e"
    assert receipt["package_hash"] == "sha256:c4507f5df11fc09bd122dba81dc5d060e46db222c23cc8ed78340096b36a3efc"
    assert E._verify_package([record], {content_hash: raw}, receipt) == "sha256:5170cd189a1b8cfd93c9fb93f08ee0cc2937cd78eb94a7f5996f40dd6f028149"
    assert config_hash == "sha256:84dc0de56e0a1b15fb8ed4da8cde9833ea501a1c1031fc8e583610c4d98f35a8"


def test_canonical_order_and_unicode_control_guards():
    assert E._digest(E.DOMAIN_CONFIG, {"b": 2, "a": 1}) == E._digest(
        E.DOMAIN_CONFIG, {"a": 1, "b": 2},
    )
    with pytest.raises(ValueError, match="NFC"):
        E._require_string("persona", "Cafe\u0301")
    with pytest.raises(ValueError, match="control"):
        E._require_string("persona", "safe\u202Eunsafe")


def test_windows_key_acl_requires_private_dacl(private_root: Path, monkeypatch):
    key = private_root / "author-corpus.key"
    key.write_bytes(b"k" * 32)
    monkeypatch.setattr(E.os, "name", "nt")

    class _Result:
        returncode = 0
        stdout = "key NT AUTHORITY\\SYSTEM:(F)\n"

    monkeypatch.setattr(E.subprocess, "run", lambda *args, **kwargs: _Result())
    assert E._read_key(key) == b"k" * 32
    _Result.stdout = "key BUILTIN\\Users:(RX)\n"
    with pytest.raises(PermissionError, match="private Windows ACL"):
        E._read_key(key)


def test_windows_short_key_still_refused(private_root: Path, monkeypatch):
    # The old Windows branch returned before the 32-byte minimum, so a short key passed.
    key = private_root / "short.key"
    key.write_bytes(b"k" * 16)
    monkeypatch.setattr(E.os, "name", "nt")

    class _Result:
        returncode = 0
        stdout = "key NT AUTHORITY\\SYSTEM:(F)\n"

    monkeypatch.setattr(E.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(ValueError, match="at least 32 random bytes"):
        E._read_key(key)


def test_windows_extra_principal_refused(private_root: Path, monkeypatch):
    # A second, unallowed grant beyond the three old denylist names must fail closed.
    key = private_root / "acl.key"
    key.write_bytes(b"k" * 32)
    monkeypatch.setattr(E.os, "name", "nt")

    class _Result:
        returncode = 0
        stdout = ("key NT AUTHORITY\\SYSTEM:(F)\n         CONTOSO\\alice:(RX)\n")

    monkeypatch.setattr(E.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(PermissionError, match="private Windows ACL"):
        E._read_key(key)


@pytest.mark.parametrize("stdout", [
    "",
    "Successfully processed 1 files; Failed processing 0 files\n",
    "1 Dateien erfolgreich verarbeitet; Fehler bei 0 Dateien\n",
    "key VORDEFINIERT\\Administratoren:(F)\n",
])
def test_windows_acl_refuses_zero_unparseable_or_localized_aces(
    private_root: Path, monkeypatch, stdout: str,
):
    key = private_root / "unparseable-acl.key"
    key.write_bytes(b"k" * 32)
    monkeypatch.setattr(E.os, "name", "nt")

    class _Result:
        returncode = 0

    _Result.stdout = stdout
    monkeypatch.setattr(E.subprocess, "run", lambda *a, **k: _Result())
    with pytest.raises(PermissionError, match="private Windows ACL"):
        E._read_key(key)


def test_builds_distinct_registers_and_closed_receipt(private_root: Path):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    assert {r["register"] for r in records} == {"text.personal", "email.personal"}
    assert not receipt["record_atomic_degraded"]
    assert receipt["counts"]["records"] == 2
    assert sorted(e["record_id"] for e in receipt["entries"]) == receipt["record_ids"]
    assert all(e["source_entry_fingerprint"].startswith("src:hmac-sha256:")
               for e in receipt["entries"])
    assert all(e["source_group"].startswith("grp:hmac-sha256:")
               for e in receipt["entries"])
    assert len(texts) == 2 and E.SHA_RE.fullmatch(config_hash)
    assert type(evidence) is E._BuildEvidence
    assert set(receipt) == {
        "schema", "surface", "surface_version", "producer_revision",
        "source_snapshot_sha256", "document_map_hash", "document_attestation_hash",
        "hmac_key_id", "register_map",
        "source_persona_aliases",
        "allowed_ai_status", "entries", "record_ids", "package_hash",
        "counts", "record_atomic_degraded",
    }


def test_explicit_source_persona_alias_is_required_and_hash_bound(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "A deliberately personal email.")
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    entry["persona"] = "anotherpanacea"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="persona does not match"):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )
    _, _, receipt, config_hash, _ = E.build_export(
        sources={"gmail_sent": manifest},
        register_map={"gmail_sent:personal": "email.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        source_persona_aliases={"gmail_sent:anotherpanacea": "joshua"},
    )
    assert receipt["source_persona_aliases"] == {"gmail_sent:anotherpanacea": "joshua"}
    assert E.SHA_RE.fullmatch(config_hash)


def test_document_local_normalizes_legacy_identity_and_order(private_root: Path):
    manifest, document_map, attestation = _document_source(private_root)
    records, texts, receipt, _, _ = E.build_export(
        sources={"document_local": manifest},
        register_map={"document_local:literary_horror": "fiction.literary"},
        allowed_ai_status=["pre_ai_human"],
        persona="joshua",
        hmac_key=b"d" * 32,
        document_map_path=document_map,
        document_attestation_path=attestation,
    )
    assert len(records) == 3 and len(texts) == 3
    ordered = sorted(records, key=lambda row: row["unit_index"])
    assert [row["unit_index"] for row in ordered] == [0, 1, 2]
    assert {row["unit_count"] for row in records} == {3}
    assert {row["unit_kind"] for row in records} == {"chapter"}
    assert len({row["source_group"] for row in records}) == 1
    assert receipt["document_map_hash"] == E._load_document_map(document_map)[1]
    assert E.SHA_RE.fullmatch(receipt["document_attestation_hash"])
    assert receipt["record_atomic_degraded"] is False
    public_metadata = json.dumps({"records": records, "receipt": receipt})
    assert "D:\\private" not in public_metadata
    assert "private-project-label" not in public_metadata
    E._verify_package(records, texts, receipt, hmac_key=b"d" * 32)


def test_document_local_rejects_identity_and_use_conflicts(private_root: Path):
    manifest, document_map, attestation = _document_source(private_root)
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0]["corpus_role"] = "impostor"
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    data = json.loads(attestation.read_text())
    data["source_manifest_sha256"] = E._sha(manifest.read_bytes())
    attestation.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="identity baseline"):
        E.build_export(
            sources={"document_local": manifest},
            register_map={"document_local:literary_horror": "fiction.literary"},
            allowed_ai_status=["pre_ai_human"], persona="joshua",
            hmac_key=b"d" * 32, document_map_path=document_map,
            document_attestation_path=attestation,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("persona", "unmapped-persona", "attested alias"),
        ("author", "unmapped-author", "attested identity"),
        ("use", ["test_set"], "voice_profile"),
        ("consent_status", "fair_use_research", "consent conflicts"),
        ("impostor_for", "joshua", "impostor marker"),
        ("split", "test", "approved baseline"),
        ("split", "benchmark", "approved baseline"),
    ],
)
def test_document_local_rejects_explicit_legacy_override(
    private_root: Path, field, value, message,
):
    manifest, document_map, attestation = _document_source(private_root)
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0][field] = value
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    data = json.loads(attestation.read_text())
    data["source_manifest_sha256"] = E._sha(manifest.read_bytes())
    attestation.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=message):
        E.build_export(
            sources={"document_local": manifest},
            register_map={"document_local:literary_horror": "fiction.literary"},
            allowed_ai_status=["pre_ai_human"], persona="joshua",
            hmac_key=b"d" * 32, document_map_path=document_map,
            document_attestation_path=attestation,
        )


def test_document_map_rejects_duplicate_positions(private_root: Path):
    _, document_map, _ = _document_source(private_root)
    rows = [json.loads(line) for line in document_map.read_text().splitlines()]
    rows[0]["unit_index"] = rows[1]["unit_index"]
    document_map.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="unique and contiguous"):
        E._load_document_map(document_map)


def test_document_map_rejects_atomic_message_unit_kind(private_root: Path):
    _, document_map, _ = _document_source(private_root)
    rows = [json.loads(line) for line in document_map.read_text().splitlines()]
    rows[0]["unit_kind"] = "atomic_message"
    document_map.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError, match="unit_kind is invalid"):
        E._load_document_map(document_map)


def test_document_smoke_is_whole_group_and_byte_bounded(private_root: Path):
    manifest, document_map, attestation = _document_source(private_root, count=21)
    kwargs = {
        "sources": {"document_local": manifest},
        "register_map": {"document_local:literary_horror": "fiction.literary"},
        "allowed_ai_status": ["pre_ai_human"],
        "persona": "joshua",
        "hmac_key": b"d" * 32,
        "document_map_path": document_map,
        "document_attestation_path": attestation,
    }
    with pytest.raises(ValueError, match="complete representative groups"):
        E.build_export(**kwargs, max_records=20, max_text_bytes=1_000_000)
    records, _, _, _, _ = E.build_export(
        **kwargs, max_records=21, max_text_bytes=1_000_000,
    )
    assert len(records) == 21
    with pytest.raises(ValueError, match="max_text_bytes"):
        E.build_export(**kwargs, max_records=21, max_text_bytes=20)


def test_document_cli_dry_run_delivers_no_prose_receipt(private_root: Path):
    manifest, document_map, attestation = _document_source(private_root)
    key = _private_key(private_root, "author-corpus.key", b"d" * 32)
    args = E.build_arg_parser().parse_args([
        "--source-manifest", f"document_local={manifest}",
        "--register-map", "document_local:literary_horror=fiction.literary",
        "--allowed-ai-status", "pre_ai_human",
        "--persona", "joshua",
        "--document-map", str(document_map),
        "--document-attestation", str(attestation),
        "--hmac-key", str(key),
        "--output-dir", str(private_root / "document-dry-run"),
        "--dry-run", "--json",
    ])
    envelope = E.run(args)
    receipt = envelope["results"]["producer_receipt"]
    assert receipt["counts"]["by_source_kind"] == {"document_local": 3}
    assert E.SHA_RE.fullmatch(receipt["document_map_hash"])
    rendered = json.dumps(envelope)
    assert "D:\\private" not in rendered
    assert "Chapter 1" not in rendered


def test_hmac_key_and_group_change_identities(private_root: Path):
    records1, _, receipt1, _, _ = _build(private_root, key=b"a" * 32)
    records2, _, receipt2, _, _ = _build(private_root, key=b"b" * 32)
    assert receipt1["hmac_key_id"] != receipt2["hmac_key_id"]
    assert records1[0]["source_group"] != records2[0]["source_group"]
    assert records1[0]["id"] != records2[0]["id"]


def test_missing_gmail_thread_or_entry_forces_degraded(private_root: Path):
    _, _, receipt, _, _ = _build(private_root, gmail_stable=False)
    assert receipt["record_atomic_degraded"] is True


def test_gmail_units_sort_by_timestamp_then_entry(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "Later email body with enough words.")
    first = json.loads(manifest.read_text())
    first_meta_path = manifest.parent / "piece.meta.json"
    first_meta = json.loads(first_meta_path.read_text())
    first_meta["author_corpus_order_timestamp"] = "2017-01-02T12:00:00+00:00"
    first_meta_path.write_text(json.dumps(first_meta))

    earlier_text = "Earlier email body with enough different words."
    earlier_path = manifest.parent / "earlier.txt"
    earlier_path.write_text(earlier_text)
    earlier = dict(first)
    earlier.update({
        "id": "gmail_sent-2", "path": earlier_path.name,
        "content_hash": E._sha(earlier_text.encode()),
    })
    earlier_path.with_suffix(".meta.json").write_text(json.dumps({
        "content_hash": earlier["content_hash"],
        "author_corpus_thread_locator": first_meta["author_corpus_thread_locator"],
        "author_corpus_entry_locator": "sha256:" + "5" * 64,
        "author_corpus_order_timestamp": "2017-01-02T11:00:00+00:00",
    }))
    manifest.write_text(json.dumps(first) + "\n" + json.dumps(earlier) + "\n")

    records, _, receipt, _, _ = E.build_export(
        sources={"gmail_sent": manifest},
        register_map={"gmail_sent:personal": "email.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
    )
    assert receipt["record_atomic_degraded"] is False
    assert len({row["source_group"] for row in records}) == 1
    by_index = {row["unit_index"]: row for row in records}
    assert by_index[0]["content_sha256"] == earlier["content_hash"]
    assert by_index[1]["content_sha256"] == first["content_hash"]
    assert {row["unit_count"] for row in records} == {2}


def test_gmail_duplicate_private_entry_locator_refuses(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "First distinct email body words.")
    first = json.loads(manifest.read_text())
    first_meta = json.loads((manifest.parent / "piece.meta.json").read_text())
    second_text = "Second distinct email body words with different content."
    second_path = manifest.parent / "second.txt"
    second_path.write_text(second_text)
    second = dict(first)
    second.update({
        "id": "gmail_sent-2", "path": second_path.name,
        "content_hash": E._sha(second_text.encode()),
    })
    second_path.with_suffix(".meta.json").write_text(json.dumps({
        "content_hash": second["content_hash"],
        "author_corpus_thread_locator": first_meta["author_corpus_thread_locator"],
        "author_corpus_entry_locator": first_meta["author_corpus_entry_locator"],
        "author_corpus_order_timestamp": "2017-01-02T13:00:00+00:00",
    }))
    manifest.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    with pytest.raises(ValueError, match="repeats a private entry locator"):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua",
            hmac_key=b"k" * 32,
        )


def test_legacy_imessage_contact_label_group_is_degraded(private_root: Path):
    manifest = _source(
        private_root, "imessage_sent", "Enough words for legacy Messages output.",
        stable=False,
    )
    meta = manifest.parent / "piece.meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["conversation_day_key"] = "contact_01|2020-01-01"
    meta.write_text(json.dumps(data), encoding="utf-8")
    _, _, receipt, _, _ = E.build_export(
        sources={"imessage_sent": manifest},
        register_map={"imessage_sent:personal": "text.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
    )
    assert receipt["record_atomic_degraded"] is True


def test_atomic_imessage_preserves_events_duplicates_and_record_atomic_bounds(
    private_root: Path,
):
    manifest = _atomic_imessage_source(private_root)
    kwargs = {
        "sources": {"imessage_sent_atomic": manifest},
        "register_map": {"imessage_sent_atomic:personal": "text.personal"},
        "allowed_ai_status": ["pre_ai_human"],
        "persona": "fixture_persona",
        "hmac_key": b"k" * 32,
    }
    records, texts, receipt, _, _ = E.build_export(**kwargs)
    assert len(records) == 3
    assert len(texts) == 3
    assert {record["unit_kind"] for record in records} == {"atomic_message"}
    assert {record["unit_index"] for record in records} == {0}
    assert {record["unit_count"] for record in records} == {1}
    assert len({record["source_group"] for record in records}) == 2
    for entry in (
        json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
    ):
        meta = json.loads(
            (manifest.parent / entry["path"]).with_suffix(".meta.json").read_text(
                encoding="utf-8"
            )
        )
        expected_fingerprint = E._hmac(b"k" * 32, E.DOMAIN_ENTRY, {
            "source_kind": "imessage_sent_atomic",
            "private_entry_locator": meta["author_corpus_entry_locator"],
            "content_sha256": entry["content_hash"],
        }, "src:hmac-sha256:")
        expected_group = E._hmac(b"k" * 32, E.DOMAIN_GROUP, {
            "source_kind": "imessage_sent_atomic",
            "private_group_locator": meta["author_corpus_group_locator"],
        }, "grp:hmac-sha256:")
        record = next(item for item in records
                      if item["source_entry_fingerprint"] == expected_fingerprint)
        assert record["source_group"] == expected_group
    repeated_normalized_hash = E._sha(
        b"An atomic note with a normalized ending."
    )
    duplicate_events = [
        record for record in records
        if record["normalized_text_sha256"] == repeated_normalized_hash
    ]
    assert len(duplicate_events) == 2
    assert len({record["content_sha256"] for record in duplicate_events}) == 2
    assert len({record["source_group"] for record in duplicate_events}) == 2
    assert len({record["id"] for record in duplicate_events}) == 2
    assert len({record["source_entry_fingerprint"] for record in duplicate_events}) == 2
    assert receipt["counts"]["by_source_kind"] == {"imessage_sent_atomic": 3}
    assert receipt["record_atomic_degraded"] is False
    assert receipt["package_hash"] == (
        "sha256:8a8228672661f0f7391457f3e741521c8433b155525f492a9d4fabc732322f88"
    )
    assert receipt["source_snapshot_sha256"] == (
        "sha256:28aac74db65eaebd9f83e45c49ce1a06385d54006c930db81f8658d265a45222"
    )
    E._verify_package(records, texts, receipt, hmac_key=b"k" * 32)

    bounded, bounded_texts, bounded_receipt, _, _ = E.build_export(
        **kwargs, max_records=1, max_text_bytes=1_000_000,
    )
    assert len(bounded) == 1
    assert any(
        record["source_group"] == bounded[0]["source_group"]
        and record["id"] != bounded[0]["id"]
        for record in records
    )
    assert bounded[0]["unit_kind"] == "atomic_message"
    assert bounded[0]["unit_index"] == 0 and bounded[0]["unit_count"] == 1
    E._verify_package(
        bounded, bounded_texts, bounded_receipt, hmac_key=b"k" * 32,
    )


def test_atomic_imessage_adjudication_rejects_row_and_binds_snapshot(
    private_root: Path,
):
    manifest = _atomic_imessage_source(private_root)
    manifest_entries = [
        json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()
    ]
    rejected_entry = manifest_entries[0]
    rejected_stem = rejected_entry["path"].split("/")[1]
    rejected_bytes = (manifest.parent / rejected_entry["path"]).read_bytes()
    _write_atomic_adjudication(manifest, [rejected_stem])
    kwargs = {
        "sources": {"imessage_sent_atomic": manifest},
        "register_map": {"imessage_sent_atomic:personal": "text.personal"},
        "allowed_ai_status": ["pre_ai_human"],
        "persona": "fixture_persona",
        "hmac_key": b"k" * 32,
    }

    records, texts, receipt, config_hash, _ = E.build_export(**kwargs)

    assert len(records) == 2
    assert len(texts) == 2
    assert rejected_entry["content_hash"] not in texts
    assert rejected_bytes not in texts.values()
    assert all(
        record["content_sha256"] != rejected_entry["content_hash"]
        for record in records
    )

    _write_atomic_adjudication(
        manifest, [rejected_stem], decision_date="2026-07-20",
    )
    changed_records, changed_texts, changed_receipt, changed_config_hash, _ = (
        E.build_export(**kwargs)
    )
    assert changed_records == records
    assert changed_texts == texts
    assert changed_receipt["package_hash"] == receipt["package_hash"]
    assert changed_receipt["source_snapshot_sha256"] != (
        receipt["source_snapshot_sha256"]
    )
    assert changed_config_hash != config_hash


def test_atomic_imessage_adjudication_unknown_stem_fails_closed(
    private_root: Path,
):
    manifest = _atomic_imessage_source(private_root)
    _write_atomic_adjudication(manifest, ["unknown-row-stem"])
    with pytest.raises(ValueError, match="adjudicated identity exclusions are invalid"):
        E.build_export(
            sources={"imessage_sent_atomic": manifest},
            register_map={"imessage_sent_atomic:personal": "text.personal"},
            allowed_ai_status=["pre_ai_human"],
            persona="fixture_persona",
            hmac_key=b"k" * 32,
        )


def test_atomic_imessage_adjudication_change_during_export_fails_closed(
    private_root: Path, monkeypatch: pytest.MonkeyPatch,
):
    manifest = _atomic_imessage_source(private_root)
    stems = [
        json.loads(line)["path"].split("/")[1]
        for line in manifest.read_text(encoding="utf-8").splitlines()
    ]
    _write_atomic_adjudication(manifest, stems[:1])
    real_binding = E._atomic_adjudication_binding

    def mutate_after_binding(bound_manifest: Path, manifest_bytes: bytes):
        binding = real_binding(bound_manifest, manifest_bytes)
        _write_atomic_adjudication(bound_manifest, stems[:2])
        return binding

    monkeypatch.setattr(E, "_atomic_adjudication_binding", mutate_after_binding)
    with pytest.raises(ValueError, match="changed during export"):
        E.build_export(
            sources={"imessage_sent_atomic": manifest},
            register_map={"imessage_sent_atomic:personal": "text.personal"},
            allowed_ai_status=["pre_ai_human"],
            persona="fixture_persona",
            hmac_key=b"k" * 32,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("author_corpus_group_locator", "sha256:" + "a" * 64, "locator"),
        ("unix_nanoseconds", True, "order timestamp"),
        ("author_corpus_unit_count", 2, "unit semantics"),
    ],
)
def test_atomic_imessage_sidecar_never_degrades(
    private_root: Path, field: str, value, message: str,
):
    manifest = _atomic_imessage_source(private_root)
    first_entry = json.loads(manifest.read_text(encoding="utf-8").splitlines()[0])
    meta_path = (manifest.parent / first_entry["path"]).with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta[field] = value
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        E.build_export(
            sources={"imessage_sent_atomic": manifest},
            register_map={"imessage_sent_atomic:personal": "text.personal"},
            allowed_ai_status=["pre_ai_human"],
            persona="fixture_persona",
            hmac_key=b"k" * 32,
        )


def test_disallowed_ai_status_and_missing_map_refuse(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "Enough words for this email fixture.",
                       ai_status="unknown")
    with pytest.raises(ValueError, match="not explicitly allowed"):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )
    with pytest.raises(ValueError, match="register_map"):
        E.build_export(
            sources={"gmail_sent": manifest}, register_map={},
            allowed_ai_status=["unknown"], persona="joshua", hmac_key=b"k" * 32,
        )


def test_publish_is_atomic_private_and_rejects_overwrite(private_root: Path):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    out = private_root / "package"
    E.publish_package(
        out, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
    )
    assert (out / "records.jsonl").is_file()
    if os.name != "nt":
        assert stat.S_IMODE((out / "records.jsonl").stat().st_mode) == 0o600
        assert stat.S_IMODE((out / "texts").stat().st_mode) == 0o700
    with pytest.raises(ValueError, match="already exists"):
        E.publish_package(
            out, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
        )
    assert not list(private_root.glob(".package.staging-*"))


@pytest.mark.parametrize("mutation", [
    "package_hash", "record_id", "source_group", "entry_mapping",
    "content_bytes", "record_count", "register_count", "era_distribution",
    "degraded_status",
])
def test_publish_reverifies_every_package_binding(private_root: Path, mutation: str):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    records = copy.deepcopy(records)
    texts = dict(texts)
    receipt = copy.deepcopy(receipt)
    if mutation == "package_hash":
        receipt["package_hash"] = "sha256:" + "0" * 64
    elif mutation == "record_id":
        records[0]["id"] = "sha256:" + "0" * 64
    elif mutation == "source_group":
        records[0]["source_group"] = "grp:hmac-sha256:" + "0" * 64
    elif mutation == "entry_mapping":
        receipt["entries"][0]["record_id"] = "sha256:" + "0" * 64
    elif mutation == "content_bytes":
        texts[records[0]["content_sha256"]] += b"tampered"
    elif mutation == "record_count":
        receipt["counts"]["records"] += 1
    elif mutation == "register_count":
        key = next(iter(receipt["counts"]["by_register"]))
        receipt["counts"]["by_register"][key] += 1
    elif mutation == "era_distribution":
        receipt["counts"]["by_era"] = {"pre_ai_widespread": len(records)}
    elif mutation == "degraded_status":
        receipt["record_atomic_degraded"] = not receipt["record_atomic_degraded"]
    out = private_root / f"tampered-{mutation}"
    with pytest.raises(ValueError):
        E.publish_package(
            out, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
        )
    assert not out.exists()


def test_degraded_posture_cannot_be_suppressed_with_a_recomputed_public_digest(
    private_root: Path,
):
    manifest = _source(
        private_root, "imessage_sent", "Enough words for degraded evidence.",
        stable=False,
    )
    records, texts, receipt, _, evidence = E.build_export(
        sources={"imessage_sent": manifest},
        register_map={"imessage_sent:personal": "text.personal"},
        allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
    )
    assert receipt["record_atomic_degraded"] is True
    receipt["record_atomic_degraded"] = False
    recomputed_hash = E._digest(E.DOMAIN_RECEIPT, receipt)
    assert E.SHA_RE.fullmatch(recomputed_hash)
    with pytest.raises(TypeError):
        dataclasses.replace(
            evidence, record_atomic_degraded=False, receipt_hash=recomputed_hash,
        )
    with pytest.raises(ValueError, match="degraded posture"):
        E.publish_package(
            private_root / "forged-degraded", records, texts, receipt,
            hmac_key=b"k" * 32, evidence=evidence,
        )
    with pytest.raises(ValueError, match="immutable evidence"):
        E.publish_package(
            private_root / "forged-evidence", records, texts, receipt,
            hmac_key=b"k" * 32, evidence=E._BuildEvidence(),
        )


def test_source_symlink_and_contact_map_refuse(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "Enough words for source path safety.")
    real = manifest.parent / "piece.txt"
    real.rename(manifest.parent / "real.txt")
    os.symlink("real.txt", real)
    with pytest.raises(ValueError, match="symlink"):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )
    with pytest.raises(ValueError, match="contact/recipient"):
        E.build_export(
            sources={"gmail_sent": private_root / "contact_map.json"},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )


def test_json_dry_run_uses_standard_no_path_envelope(private_root: Path, capsys):
    manifest = _source(private_root, "gmail_sent", "Enough words for envelope fixture.")
    key = _private_key(private_root, "key.bin", b"z" * 32)
    out = private_root / "package"
    rc = E.main([
        "--source-manifest", f"gmail_sent={manifest}",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(out), "--dry-run", "--json",
    ])
    assert rc == 0 and not out.exists()
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "voice_coherence_acquisition"
    assert envelope["target"] == {"path": None, "words": 0}
    assert set(envelope["results"]) == {"producer_receipt"}
    blob = json.dumps(envelope)
    assert str(private_root) not in blob


def test_full_write_requires_matching_smoke(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "Enough words for smoke fixture.")
    key = _private_key(private_root, "key.bin", b"z" * 32)
    args = E.build_arg_parser().parse_args([
        "--source-manifest", f"gmail_sent={manifest}",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(private_root / "full"),
    ])
    with pytest.raises(PermissionError, match="prior bounded live-smoke"):
        E.run(args)


def test_bounded_smoke_then_distinct_full_export_succeeds(
    private_root: Path, monkeypatch: pytest.MonkeyPatch,
):
    im = _source(private_root, "imessage_sent", "Enough text message words for smoke.")
    gm = _source(private_root, "gmail_sent", "Enough email words for smoke coverage.")
    key = _private_key(private_root, "key.bin", b"z" * 32)
    common = [
        "--source-manifest", f"imessage_sent={im}",
        "--source-manifest", f"gmail_sent={gm}",
        "--register-map", "imessage_sent:personal=text.personal",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key),
    ]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    smoke_args = E.build_arg_parser().parse_args(common + [
        "--output-dir", str(private_root / "bounded-smoke"),
        "--max-records", "2", "--max-text-bytes", "1000000",
        "--live-smoke-confirmed",
    ])
    smoke = E.run(smoke_args)
    assert smoke["results"]["producer_receipt"]["counts"]["records"] == 2
    full_args = E.build_arg_parser().parse_args(common + [
        "--output-dir", str(private_root / "full-package"),
    ])
    full = E.run(full_args)
    assert full["results"]["producer_receipt"]["counts"]["records"] == 2
    assert (private_root / "full-package" / "producer_receipt.json").is_file()


def test_bounded_smoke_requires_every_source_register_pair(private_root: Path):
    im = _source(private_root, "imessage_sent", "Enough text words for pair coverage.")
    gm = _source(private_root, "gmail_sent", "Enough email words for pair coverage.")
    with pytest.raises(ValueError, match="complete representative groups"):
        E.build_export(
            sources={"imessage_sent": im, "gmail_sent": gm},
            register_map={
                "imessage_sent:personal": "text.personal",
                "gmail_sent:personal": "email.personal",
            },
            allowed_ai_status=["pre_ai_human"], persona="joshua",
            hmac_key=b"k" * 32, max_records=1,
        )


def test_smoke_receipt_rejects_malformed_stale_and_same_destination(private_root: Path):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    smoke = private_root / "bounded"
    E.publish_package(
        smoke, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
    )
    E._write_smoke_receipt(smoke, config_hash, receipt, records)
    with pytest.raises(PermissionError, match="distinct destination"):
        E._require_smoke_receipt(smoke, config_hash, receipt, records, b"k" * 32)
    path = E._smoke_path(smoke)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["confirmed_at"] = (
        dt.datetime.now(dt.timezone.utc) - E.SMOKE_MAX_AGE - dt.timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PermissionError, match="stale"):
        E._require_smoke_receipt(
            private_root / "full", config_hash, receipt, records, b"k" * 32,
        )
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        E._require_smoke_receipt(
            private_root / "full", config_hash, receipt, records, b"k" * 32,
        )


def test_smoke_receipt_revalidates_the_bounded_artifact(private_root: Path):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    smoke = private_root / "bounded"
    E.publish_package(
        smoke, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
    )
    E._write_smoke_receipt(smoke, config_hash, receipt, records)
    text_path = smoke / records[0]["text_path"]
    text_path.write_bytes(text_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="exact text bytes"):
        E._require_smoke_receipt(
            private_root / "full", config_hash, receipt, records, b"k" * 32,
        )


def test_malformed_smoke_uses_standard_unavailable_envelope(private_root: Path, capsys):
    records, texts, receipt, config_hash, evidence = _build(private_root)
    smoke = private_root / "bounded"
    E.publish_package(
        smoke, records, texts, receipt, hmac_key=b"k" * 32, evidence=evidence,
    )
    E._write_smoke_receipt(smoke, config_hash, receipt, records)
    E._smoke_path(smoke).write_text("[]", encoding="utf-8")
    key = _private_key(private_root, "key.bin", b"k" * 32)
    rc = E.main([
        "--source-manifest", f"imessage_sent={private_root / 'imessage_sent' / 'draft_manifest.jsonl'}",
        "--source-manifest", f"gmail_sent={private_root / 'gmail_sent' / 'draft_manifest.jsonl'}",
        "--register-map", "imessage_sent:personal=text.personal",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(private_root / "full"), "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0 and str(private_root) not in captured.out + captured.err
    envelope = json.loads(captured.out)
    assert envelope["available"] is False and envelope["reason_category"] == "bad_input"


def test_json_refusal_does_not_disclose_private_paths(private_root: Path, capsys):
    key = _private_key(private_root, "key.bin", b"z" * 32)
    missing = private_root / "secret-persona" / "missing-manifest.jsonl"
    rc = E.main([
        "--source-manifest", f"gmail_sent={missing}",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(private_root / "package"),
        "--dry-run", "--json",
    ])
    captured = capsys.readouterr()
    assert rc == 0 and "secret-persona" not in captured.err
    assert str(private_root) not in captured.err
    assert "secret-persona" not in captured.out and str(private_root) not in captured.out
    envelope = json.loads(captured.out)
    assert envelope["available"] is False and envelope["reason_category"] == "bad_input"


def test_dry_run_validates_destination_privacy(private_root: Path, tmp_path: Path):
    manifest = _source(private_root, "gmail_sent", "Enough words for dry-run privacy.")
    key = _private_key(private_root, "key.bin", b"z" * 32)
    args = E.build_arg_parser().parse_args([
        "--source-manifest", f"gmail_sent={manifest}",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(tmp_path / "public-package"),
        "--dry-run", "--json",
    ])
    with pytest.raises(PermissionError, match="private-path policy"):
        E.run(args)


def test_non_json_errors_never_disclose_exception_paths(
    private_root: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    secret = private_root / "private-destination"

    def fail(_args):
        raise OSError(f"could not create {secret}")

    monkeypatch.setattr(E, "run", fail)
    rc = E.main([
        "--source-manifest", "gmail_sent=unused",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", "unused", "--output-dir", str(secret),
    ])
    captured = capsys.readouterr()
    assert rc == 2
    assert str(secret) not in captured.err
    assert captured.err == "author_corpus_export: private input or policy validation failed\n"


@pytest.mark.parametrize("hostile", [[], {}, True, None])
def test_hostile_manifest_field_types_refuse_cleanly(private_root: Path, hostile):
    manifest = _source(private_root, "gmail_sent", "Enough words for hostile JSON.")
    entry = json.loads(manifest.read_text(encoding="utf-8"))
    entry["era"] = hostile
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )


def test_duplicate_json_keys_refuse_in_manifest_and_sidecar(private_root: Path):
    manifest = _source(private_root, "gmail_sent", "Enough words for duplicate keys.")
    original = manifest.read_text(encoding="utf-8").strip()
    manifest.write_text(original[:-1] + ',"era":"pre_chatgpt"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        E.build_export(
            sources={"gmail_sent": manifest},
            register_map={"gmail_sent:personal": "email.personal"},
            allowed_ai_status=["pre_ai_human"], persona="joshua", hmac_key=b"k" * 32,
        )


def test_dispatcher_policy_refusal_projects_no_private_path(tmp_path: Path, capsys):
    public = tmp_path / "public-input"
    manifest = _source(public, "gmail_sent", "Enough words for privacy refusal.")
    key = _private_key(tmp_path, "key.bin", b"q" * 32)
    rc = setec_run.dispatch("author_corpus_export", [
        "--source-manifest", f"gmail_sent={manifest}",
        "--register-map", "gmail_sent:personal=email.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(public / "package"),
        "--dry-run",
    ], observed_version="1.123.0")
    captured = capsys.readouterr()
    assert rc == setec_run.EXIT_CONTRACT
    assert str(tmp_path) not in captured.out and str(tmp_path) not in captured.err
    envelope = json.loads(captured.out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "policy_refused"


def test_normalized_dispatcher_delivers_receipt_in_results(private_root: Path, capsys):
    manifest = _source(private_root, "imessage_sent", "Enough words for dispatcher fixture.")
    key = _private_key(private_root, "key.bin", b"q" * 32)
    rc = setec_run.dispatch("author_corpus_export", [
        "--source-manifest", f"imessage_sent={manifest}",
        "--register-map", "imessage_sent:personal=text.personal",
        "--allowed-ai-status", "pre_ai_human", "--persona", "joshua",
        "--hmac-key", str(key), "--output-dir", str(private_root / "dispatch"),
        "--dry-run",
    ], observed_version="1.123.0")
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["tool"] == "author_corpus_export"
    assert set(envelope["results"]) == {"producer_receipt"}
    assert envelope["target"]["path"] is None
