import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "normalize_author_registry.py"
SPEC = importlib.util.spec_from_file_location("normalize_author_registry", SCRIPT)
assert SPEC and SPEC.loader
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)


def _manifest(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _identity_entry(**overrides) -> dict:
    entry = {
        "id": "entry-1", "path": "x.txt", "persona": "joshua",
        "register": "personal", "ai_status": "pre_ai_human",
        "corpus_role": "identity_baseline", "use": ["voice_profile"],
        "split": "baseline", "consent_status": "author_consent",
    }
    entry.update(overrides)
    return entry


def _build_one(tmp_path: Path, entry: dict, *, aliases=None):
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir(parents=True)
    manifest = root / "source.jsonl"
    (root / "x.txt").write_text("message", encoding="utf-8")
    _manifest(manifest, [entry])
    return registry.build_registry(
        sources={"legacy": manifest},
        register_map={("legacy", "personal"): "text.personal"},
        canonical_persona="joshua", source_persona_aliases=aliases,
    )


def test_registry_normalizes_persona_registers_and_ai_eligibility(tmp_path: Path):
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    messages = root / "messages.jsonl"
    email = root / "email.jsonl"
    (root / "a.txt").write_text("message", encoding="utf-8")
    (root / "b.txt").write_text("email", encoding="utf-8")
    _manifest(messages, [_identity_entry(
        id="message-1", path="a.txt", date_written="2020-01-02",
    )])
    _manifest(email, [_identity_entry(
        id="email-1", path="b.txt", persona="anotherpanacea",
        ai_status="unknown", date_written="2025-01-02",
        content_hash="sha256:normalized-source-hash",
    )])
    output = root / "registry"
    summary = registry.run(registry.build_arg_parser().parse_args([
        "--source-manifest", f"imessage_sent={messages}",
        "--source-manifest", f"gmail_sent={email}",
        "--register-map", "imessage_sent:personal=text.personal",
        "--register-map", "gmail_sent:personal=email.personal",
        "--source-persona-alias", "gmail_sent:anotherpanacea=joshua",
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
    _manifest(manifest, [_identity_entry(id="x")])
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


def test_private_path_rejects_symlink_escape(tmp_path: Path):
    private_root = tmp_path / "ai-prose-baselines-private"
    (private_root / "sub").mkdir(parents=True)
    registry._private_path(private_root / "sub")  # genuine private path passes
    with pytest.raises(ValueError):
        registry._private_path(private_root / ".." / "outside")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = private_root / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        registry._private_path(link)


def test_source_text_rejects_intermediate_symlink(tmp_path: Path):
    root = tmp_path / "ai-prose-baselines-private"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("escaped", encoding="utf-8")
    (root / "linkdir").symlink_to(outside, target_is_directory=True)
    manifest = root / "m.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        registry._source_text_path(manifest, "linkdir/x.txt")


def test_registry_refuses_foreign_persona_without_keyed_alias(tmp_path: Path):
    with pytest.raises(ValueError, match="persona is not authorized"):
        _build_one(tmp_path, _identity_entry(persona="anotherpanacea"))


@pytest.mark.parametrize(
    "aliases",
    [
        {("legacy", "anotherpanacea"): "someone-else"},
        {("other-source", "anotherpanacea"): "joshua"},
    ],
)
def test_registry_refuses_unauthorized_alias_policy(tmp_path: Path, aliases):
    with pytest.raises(ValueError, match="source persona alias"):
        _build_one(
            tmp_path, _identity_entry(persona="anotherpanacea"), aliases=aliases,
        )


def test_registry_accepts_source_qualified_persona_alias(tmp_path: Path):
    aliases = registry._persona_aliases(["legacy:anotherpanacea=joshua"])
    rows, summary = _build_one(
        tmp_path, _identity_entry(persona="anotherpanacea"), aliases=aliases,
    )
    assert len(rows) == 1
    assert rows[0]["training_eligibility"] == "eligible_pre_ai"
    assert summary["by_training_eligibility"] == {"eligible_pre_ai": 1}


@pytest.mark.parametrize(
    "conflict",
    [
        {"corpus_role": "impostor"},
        {"use": ["voice_impostor"]},
        {"split": "test"},
        {"consent_status": "revoked"},
        {"impostor_for": "joshua"},
        {"register_match": "exact"},
        {"topic_match": False},
    ],
)
def test_registry_refuses_nonbaseline_and_impostor_posture(
    tmp_path: Path, conflict,
):
    with pytest.raises(ValueError):
        _build_one(tmp_path, _identity_entry(**conflict))
