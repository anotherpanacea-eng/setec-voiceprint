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
