from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "prepare_author_document_adapter.py"
SPEC = importlib.util.spec_from_file_location("prepare_author_document_adapter", SCRIPT)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def test_adapter_builds_document_local_exact_byte_manifest(tmp_path: Path):
    root = tmp_path / "ai-prose-baselines-private"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "piece.txt").write_bytes(
        b"A pre-AI document with\x00 a control and a bidi mark \xe2\x80\xae.\n"
    )
    (source / "draft_manifest.jsonl").write_text(json.dumps({
        "id": "legacy-piece", "path": "piece.txt", "register": "personal",
        "date_written": "2019", "ai_status": "pre_ai_human",
    }) + "\n" + json.dumps({
        "id": "later-piece", "path": "piece.txt", "register": "personal",
        "ai_status": "unknown",
    }) + "\n", encoding="utf-8")
    out = root / "adapter"

    assert adapter.main([
        "--source-manifest", f"legacy={source / 'draft_manifest.jsonl'}",
        "--register-map", "legacy:personal=blog.essay",
        "--persona", "joshua", "--author-identity", "Joshua A. Miller",
        "--legacy-persona-alias", "legacy-joshua", "--output-dir", str(out),
    ]) == 0

    rows = [json.loads(line) for line in (out / "draft_manifest.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()]
    maps = [json.loads(line) for line in (out / "document_map.jsonl").read_text(
        encoding="utf-8",
    ).splitlines()]
    assert len(rows) == len(maps) == 1
    assert rows[0]["register"] == "blog.essay"
    assert rows[0]["date_written"] == "2019-01-01"
    assert maps[0]["unit_kind"] == "document"
    assert "\x00" not in (out / rows[0]["path"]).read_text(encoding="utf-8")
    assert "\u202e" not in (out / rows[0]["path"]).read_text(encoding="utf-8")
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["controls_removed"] == 2
    assert summary["skipped"] == {"unknown": 1}


def test_canonical_date_rejects_invalid_values():
    assert adapter.canonical_date("2020-02-03T12:34:56+00:00") == "2020-02-03"
    assert adapter.canonical_date("pre_2023") is None
    assert adapter.canonical_date("2020-02-31") is None


import pytest  # noqa: E402


def _adapter_run(tmp_path: Path, entry_extra: dict) -> None:
    root = tmp_path / "ai-prose-baselines-private"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "piece.txt").write_bytes(b"A clean pre-AI document.\n")
    entry = {"id": "p1", "path": "piece.txt", "register": "personal",
             "ai_status": "pre_ai_human", **entry_extra}
    (source / "draft_manifest.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    adapter.main([
        "--source-manifest", f"legacy={source / 'draft_manifest.jsonl'}",
        "--register-map", "legacy:personal=blog.essay",
        "--persona", "joshua", "--author-identity", "Joshua A. Miller",
        "--output-dir", str(root / "adapter")])


def test_adapter_refuses_declared_nonbaseline_material(tmp_path: Path):
    # P1a: a source row declaring a non-baseline role/use/split/consent must NOT be
    # silently relabeled as identity_baseline just because it is pre_ai_human.
    for extra in ({"corpus_role": "impostor"}, {"split": "test"},
                  {"use": ["held_out"]}, {"consent_status": "revoked"}):
        with pytest.raises(ValueError):
            _adapter_run(tmp_path / json.dumps(extra, sort_keys=True)[:8], extra)


def test_adapter_refuses_drifted_content_hash(tmp_path: Path):
    # P2a: a declared content hash that does not match the current bytes refuses.
    with pytest.raises(ValueError):
        _adapter_run(tmp_path, {"content_hash": "sha256:" + "0" * 64})


def test_adapter_accepts_matching_declarations(tmp_path: Path):
    # Baseline-consistent declarations + a correct content hash are accepted.
    correct = adapter.sha(b"A clean pre-AI document.\n")
    _adapter_run(tmp_path, {"corpus_role": "identity_baseline", "split": "baseline",
                            "use": ["voice_profile"], "consent_status": "author_consent",
                            "content_hash": correct})


def test_private_rejects_escaping_and_symlink_paths(tmp_path: Path):
    private_root = tmp_path / "ai-prose-baselines-private"
    (private_root / "sub").mkdir(parents=True)
    adapter.private(private_root / "sub")  # a genuine private path passes
    # P1b: a `..` escape that resolves outside the protected directory refuses.
    with pytest.raises(ValueError):
        adapter.private(private_root / ".." / "outside")
    # a symlink whose target escapes the protected directory refuses.
    outside = tmp_path / "outside"
    outside.mkdir()
    link = private_root / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        adapter.private(link)
