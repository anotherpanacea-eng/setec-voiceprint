import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "normalize_author_registry.py"
SPEC = importlib.util.spec_from_file_location("normalize_author_registry", SCRIPT)
assert SPEC and SPEC.loader
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


def _manifest(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_registry_normalizes_persona_registers_and_ai_eligibility(tmp_path: Path):
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    messages = root / "messages.jsonl"
    email = root / "email.jsonl"
    (root / "a.txt").write_text("message", encoding="utf-8")
    (root / "b.txt").write_text("email", encoding="utf-8")
    _manifest(messages, [{"id": "message-1", "path": "a.txt", "register": "personal", "ai_status": "pre_ai_human", "persona": "joshua", "date_written": "2020-01-02", "split": "baseline"}])
    _manifest(email, [{"id": "email-1", "path": "b.txt", "register": "personal", "ai_status": "unknown", "persona": "anotherpanacea", "date_written": "2025-01-02", "split": "baseline", "content_hash": "sha256:normalized-source-hash"}])
    output = root / "registry"
    summary = registry.run(registry.build_arg_parser().parse_args([
        "--source-manifest", f"imessage_sent={messages}",
        "--source-manifest", f"gmail_sent={email}",
        "--register-map", "imessage_sent:personal=text.personal",
        "--register-map", "gmail_sent:personal=email.personal",
        "--persona", "joshua", "--output-dir", str(output),
    ]))
    rows = [json.loads(line) for line in (output / "author_registry.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary["by_register"] == {"email.personal": 1, "text.personal": 1}
    assert summary["source_declared_hash_mismatches"] == 1
    assert {row["canonical_persona"] for row in rows} == {"joshua"}
    assert {row["training_eligibility"] for row in rows} == {"eligible_pre_ai", "review_or_exclude"}


def test_registry_requires_explicit_mapping(tmp_path: Path):
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    manifest = root / "source.jsonl"
    (root / "x.txt").write_text("message", encoding="utf-8")
    _manifest(manifest, [{"id": "x", "path": "x.txt", "register": "personal", "ai_status": "pre_ai_human"}])
    args = registry.build_arg_parser().parse_args([
        "--source-manifest", f"messages={manifest}",
        "--register-map", "messages:other=text.personal",
        "--persona", "joshua", "--output-dir", str(root / "out"),
    ])
    try:
        registry.run(args)
    except ValueError as exc:
        assert "missing explicit mapping" in str(exc)
    else:
        raise AssertionError("expected explicit mapping refusal")
