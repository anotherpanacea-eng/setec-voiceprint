"""Tests for ``external_mirror`` Phase B modules: ingest_outputs,
compute_distances, compose_evidence_pack.

Pin the contracts: normalization rules, format detection, distance
matrix shape, evidence pack envelope. No real embedding calls — a
deterministic stub backend ships per-test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_EXTERNAL_MIRROR = _HERE.parent / "external_mirror"
_SCRIPTS = _HERE.parent
sys.path.insert(0, str(_EXTERNAL_MIRROR))
sys.path.insert(0, str(_SCRIPTS))

import ingest_outputs as ingest  # noqa: E402
import compute_distances as dist  # noqa: E402
import compose_evidence_pack as pack  # noqa: E402


# ============================================================
# Stub embedding backend
# ============================================================


class StubBackend:
    """Deterministic embedding stub: each text is mapped to a unit vector
    derived from its hash. Identical texts map to identical embeddings;
    different texts map to nearly-orthogonal vectors. Pure-Python so the
    pytest env doesn't need numpy."""

    def __init__(self, alias: str = "stub"):
        self.alias = alias

    def encode(self, texts, *, normalize=True):
        import hashlib
        import math

        dim = 16
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            vec = [(h[j] / 255.0) - 0.5 for j in range(dim)]
            if normalize:
                norm = math.sqrt(sum(x * x for x in vec))
                if norm > 0:
                    vec = [x / norm for x in vec]
            out.append(vec)
        return out

    def identifier_block(self):
        return {"id": "stub-model", "alias": self.alias, "method": "stub"}


# ============================================================
# Fixtures
# ============================================================


def _make_manifest(tmp_path: Path, windows_count: int = 4, continuation: int = 150) -> Path:
    """Write a minimal Phase A MANIFEST.json that ingest can parse."""
    prompts_dir = tmp_path / "prompts" / "test_run"
    prompts_dir.mkdir(parents=True)
    manifest = {
        "run_id": "test_run",
        "target_path": str(tmp_path / "target.txt"),
        "target_sha256": "0" * 64,
        "target_word_count": 3000,
        "positioning": "equal_skipping_opening",
        "continuation": continuation,
        "context": 500,
        "context_grid": None,
        "windows_count": windows_count,
        "windows": [
            {
                "window_index": i + 1,
                "context_start_word": 100 * i,
                "context_end_word": 100 * i + 500,
                "continuation_start_word": 100 * i + 500,
                "continuation_end_word": 100 * i + 500 + continuation,
                "context_word_count": 500,
                "context_sha256": f"{i:064x}",
            }
            for i in range(windows_count)
        ],
        "genre_descriptor": "test genre",
        "format": "both",
        "tool_path": "/dummy",
        "tool_sha256": "0" * 64,
        "git_head_sha": None,
        "built_at": "2026-05-19T00:00:00+00:00",
        "caveats_recommended": [],
    }
    (prompts_dir / "MANIFEST.json").write_text(json.dumps(manifest))
    return prompts_dir


def _make_outputs(tmp_path: Path, *, families: dict[str, dict]) -> Path:
    """Make an outputs/test_run/ directory. ``families`` is
    ``{family_name: {"format": "t3"|"t4", "windows": {idx: text} | "raw_t4": str}}``."""
    outputs_dir = tmp_path / "outputs" / "test_run"
    outputs_dir.mkdir(parents=True)
    for fam, spec in families.items():
        fam_dir = outputs_dir / fam
        fam_dir.mkdir()
        if spec["format"] == "t3":
            for idx, txt in spec["windows"].items():
                (fam_dir / f"window_{idx}.txt").write_text(txt)
        elif spec["format"] == "t4":
            if "raw_t4" in spec:
                (fam_dir / "windows_batched.json").write_text(spec["raw_t4"])
            else:
                arr = [{"window": idx, "continuation": txt} for idx, txt in spec["windows"].items()]
                (fam_dir / "windows_batched.json").write_text(json.dumps(arr))
    return outputs_dir


# ============================================================
# Normalization
# ============================================================


def test_normalize_strips_preamble():
    text, actions = ingest.normalize_output("Sure! Here's the continuation: The quick brown fox.", expected_words=10)
    assert "stripped_preamble" in actions[0]
    assert "The quick brown fox" in text


def test_normalize_strips_code_fence():
    raw = "```\nThe quick brown fox jumped over the lazy dog.\n```"
    text, actions = ingest.normalize_output(raw, expected_words=10)
    assert "stripped_code_fence" in actions
    assert "The quick brown fox" in text


def test_normalize_strips_code_fence_with_language_tag():
    raw = "```text\nThe continuation here.\n```"
    text, actions = ingest.normalize_output(raw, expected_words=10)
    assert "stripped_code_fence" in actions
    assert "The continuation here" in text


def test_normalize_strips_quotes():
    text, actions = ingest.normalize_output('"The continuation here."', expected_words=10)
    assert "stripped_quotes" in actions
    assert text == "The continuation here."


def test_normalize_strips_trailing_commentary():
    raw = "The continuation here.\n\nLet me know if you'd like me to adjust the voice."
    text, actions = ingest.normalize_output(raw, expected_words=10)
    assert any("stripped_trailing" in a for a in actions)
    assert "Let me know" not in text
    assert "The continuation here" in text


def test_normalize_preserves_clean_input():
    raw = "She walked into the bar and ordered a drink. Outside, the rain fell."
    text, actions = ingest.normalize_output(raw, expected_words=20)
    assert text == raw
    assert actions == []


def test_detect_refusal_catches_common_patterns():
    assert ingest.detect_refusal("I can't help with that request.")
    assert ingest.detect_refusal("As an AI language model, I cannot generate continuations.")
    assert ingest.detect_refusal("I'm sorry, but I cannot continue this text.")
    assert not ingest.detect_refusal("She walked into the bar.")


# ============================================================
# Format detection + parsing
# ============================================================


def test_parse_t4_batched_round_trip(tmp_path):
    arr = [{"window": 1, "continuation": "first"}, {"window": 2, "continuation": "second"}]
    p = tmp_path / "windows_batched.json"
    p.write_text(json.dumps(arr))
    pairs = ingest.parse_t4_batched(p)
    assert pairs == [(1, "first"), (2, "second")]


def test_parse_t4_batched_strips_code_fence(tmp_path):
    arr = [{"window": 1, "continuation": "first"}]
    p = tmp_path / "windows_batched.json"
    p.write_text("```json\n" + json.dumps(arr) + "\n```")
    pairs = ingest.parse_t4_batched(p)
    assert pairs == [(1, "first")]


def test_parse_t4_batched_errors_on_non_array(tmp_path):
    p = tmp_path / "windows_batched.json"
    p.write_text(json.dumps({"window": 1, "continuation": "x"}))
    with pytest.raises(ValueError, match="expected JSON array"):
        ingest.parse_t4_batched(p)


def test_parse_t4_batched_errors_on_missing_keys(tmp_path):
    p = tmp_path / "windows_batched.json"
    p.write_text(json.dumps([{"window": 1}]))
    with pytest.raises(ValueError, match="missing 'window'"):
        ingest.parse_t4_batched(p)


def test_parse_t3_separate_matches_filename_indices(tmp_path):
    fam = tmp_path / "claude"
    fam.mkdir()
    (fam / "window_1.txt").write_text("first")
    (fam / "window_2.md").write_text("second")
    (fam / "window_3.txt").write_text("third")
    (fam / "notes.txt").write_text("ignored")
    pairs = ingest.parse_t3_separate(fam)
    indices = [p[0] for p in pairs]
    assert indices == [1, 2, 3]


# ============================================================
# Ingest full pipeline
# ============================================================


def test_ingest_t3_format_round_trip(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=2)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "first claude window", 2: "second claude window"}},
        "chatgpt": {"format": "t3", "windows": {1: "first chatgpt window", 2: "second chatgpt window"}},
    })
    payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    assert len(payload["families"]) == 2
    fam_names = {f["family"] for f in payload["families"]}
    assert fam_names == {"claude", "chatgpt"}
    for fam in payload["families"]:
        assert len(fam["windows"]) == 2


def test_ingest_t4_format_round_trip(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=2)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t4", "windows": {1: "first", 2: "second"}},
    })
    payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    fam = payload["families"][0]
    assert len(fam["windows"]) == 2
    assert fam["windows"][0]["normalized_text"] == "first"


def test_ingest_strict_mode_errors_on_missing_window(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=4)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "first", 2: "second"}},
    })
    with pytest.raises(ValueError, match="missing windows"):
        ingest.ingest(prompts_dir, outputs_dir, strict=True)


def test_ingest_default_mode_warns_on_missing_window(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=4)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "first", 2: "second"}},
    })
    payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    caveats = payload["families"][0]["caveats"]
    assert any("missing_windows" in c for c in caveats)


def test_ingest_flags_refusals(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=1)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "I can't help with that."}},
    })
    payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    rec = payload["families"][0]["windows"][0]
    assert "refused" in rec["caveats"]


def test_ingest_flags_truncation(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=1, continuation=150)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "tiny output"}},
    })
    payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    rec = payload["families"][0]["windows"][0]
    assert any("truncated" in c for c in rec["caveats"])


def test_ingest_errors_on_missing_manifest(tmp_path):
    prompts_dir = tmp_path / "prompts" / "test_run"
    prompts_dir.mkdir(parents=True)
    outputs_dir = _make_outputs(tmp_path, families={"claude": {"format": "t3", "windows": {1: "x"}}})
    with pytest.raises(FileNotFoundError, match="MANIFEST.json"):
        ingest.ingest(prompts_dir, outputs_dir, strict=False)


def test_ingest_errors_on_empty_outputs_dir(tmp_path):
    prompts_dir = _make_manifest(tmp_path)
    outputs_dir = tmp_path / "outputs" / "test_run"
    outputs_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="no family subdirectories"):
        ingest.ingest(prompts_dir, outputs_dir, strict=False)


# ============================================================
# Distance computation
# ============================================================


def _build_ingested(windows_count: int = 2, families_texts: dict[str, list[str]] | None = None) -> dict:
    """Build a synthetic ingested.json payload."""
    if families_texts is None:
        families_texts = {"claude": ["a" * 50, "b" * 50]}
    families = []
    for fam, texts in families_texts.items():
        windows = []
        for i, t in enumerate(texts):
            windows.append({
                "family": fam,
                "window_index": i + 1,
                "source_file": f"/dummy/{fam}/window_{i+1}.txt",
                "raw_text": t,
                "normalized_text": t,
                "normalized_word_count": len(t.split()),
                "normalization_actions": [],
                "caveats": [],
            })
        families.append({"family": fam, "caveats": [], "windows": windows})
    return {
        "ingested_at": "2026-05-19T00:00:00+00:00",
        "prompts_dir": "/dummy/prompts/test_run",
        "outputs_dir": "/dummy/outputs/test_run",
        "manifest": {
            "run_id": "test_run",
            "target_sha256": "0" * 64,
            "target_path": "/dummy/target.txt",
            "target_word_count": 3000,
            "positioning": "equal_skipping_opening",
            "windows_count": windows_count,
            "continuation": 150,
            "windows": [],
        },
        "families": families,
        "caveats": [],
    }


def test_compute_distances_matrix_shape_with_target():
    ingested = _build_ingested(windows_count=2, families_texts={
        "claude": ["claude w1", "claude w2"],
        "chatgpt": ["chatgpt w1", "chatgpt w2"],
    })
    target_continuations = ["target w1 actual", "target w2 actual"]
    payload = dist.compute(ingested, target_continuations=target_continuations, backend=StubBackend())
    matrices = payload["distance_matrices"]
    labels = payload["labels_per_window"]
    assert len(matrices) == 2
    assert len(labels[0]) == 3  # target + 2 families
    assert all(len(row) == 3 for row in matrices[0])
    assert payload["have_target_continuation"] is True


def test_compute_distances_matrix_shape_without_target():
    ingested = _build_ingested(windows_count=2, families_texts={
        "claude": ["claude w1", "claude w2"],
        "chatgpt": ["chatgpt w1", "chatgpt w2"],
    })
    payload = dist.compute(ingested, target_continuations=None, backend=StubBackend())
    labels = payload["labels_per_window"]
    matrices = payload["distance_matrices"]
    assert payload["have_target_continuation"] is False
    assert "__target__" not in labels[0]
    assert len(labels[0]) == 2
    assert len(matrices[0]) == 2
    assert "target_continuation_unavailable" in payload["global_caveats"]


def test_compute_distances_identical_texts_give_zero_distance():
    ingested = _build_ingested(windows_count=1, families_texts={
        "claude": ["same text"],
        "chatgpt": ["same text"],
    })
    payload = dist.compute(ingested, target_continuations=["same text"], backend=StubBackend())
    matrix = payload["distance_matrices"][0]
    assert all(abs(matrix[i][j]) < 1e-5 for i in range(3) for j in range(3))


def test_compute_distances_excludes_refusals():
    ingested = _build_ingested(windows_count=1, families_texts={"claude": ["x"]})
    ingested["families"][0]["windows"][0]["caveats"] = ["refused"]
    ingested["families"][0]["windows"][0]["normalized_text"] = ""
    payload = dist.compute(ingested, target_continuations=["target"], backend=StubBackend())
    matrix = payload["distance_matrices"][0]
    labels = payload["labels_per_window"][0]
    claude_idx = labels.index("claude")
    target_idx = labels.index("__target__")
    assert matrix[target_idx][claude_idx] is None
    assert "family_claude_refused" in payload["per_window_caveats"][0]


def test_compute_distances_summary_statistics():
    ingested = _build_ingested(windows_count=3, families_texts={
        "claude": ["a", "b", "c"],
        "chatgpt": ["x", "y", "z"],
    })
    targets = ["t1", "t2", "t3"]
    payload = dist.compute(ingested, target_continuations=targets, backend=StubBackend())
    summary = payload["summary"]
    assert "claude" in summary
    assert "chatgpt" in summary
    assert summary["claude"]["n_windows_compared"] == 3
    for key in ("mean_vs_target", "median_vs_target", "min_vs_target", "max_vs_target"):
        assert key in summary["claude"]


def test_compute_distances_records_embedding_block():
    ingested = _build_ingested(windows_count=1, families_texts={"claude": ["x"]})
    payload = dist.compute(ingested, target_continuations=["t"], backend=StubBackend())
    assert payload["embedding_block"]["id"] == "stub-model"


# ============================================================
# Target-continuation loading
# ============================================================


def test_load_target_continuations_json_array(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(json.dumps([{"window": 1, "continuation": "first"}, {"window": 2, "continuation": "second"}]))
    out = dist._load_target_continuations(p, windows_count=2)
    assert out == ["first", "second"]


def test_load_target_continuations_json_object(tmp_path):
    p = tmp_path / "targets.json"
    p.write_text(json.dumps({"1": "first", "2": "second"}))
    out = dist._load_target_continuations(p, windows_count=2)
    assert out == ["first", "second"]


def test_load_target_continuations_plain_text_single_window(tmp_path):
    p = tmp_path / "targets.txt"
    p.write_text("the only continuation")
    out = dist._load_target_continuations(p, windows_count=1)
    assert out == ["the only continuation"]


def test_load_target_continuations_plain_text_multi_window_errors(tmp_path):
    p = tmp_path / "targets.txt"
    p.write_text("plain text")
    with pytest.raises(ValueError, match="must be JSON"):
        dist._load_target_continuations(p, windows_count=3)


# ============================================================
# Evidence pack composition
# ============================================================


def _build_distances_payload(windows_count: int = 2) -> dict:
    """Minimal distances.json payload for evidence-pack tests."""
    return {
        "computed_at": "2026-05-19T00:00:00+00:00",
        "script_version": "0.1.0",
        "embedding_block": {"id": "stub-model", "alias": "stub", "method": "stub"},
        "manifest": {
            "run_id": "test_run",
            "target_path": "/dummy/target.txt",
            "target_sha256": "0" * 64,
            "target_word_count": 3000,
            "positioning": "equal_skipping_opening",
            "windows_count": windows_count,
            "continuation": 150,
            "windows": [],
        },
        "families": ["claude", "chatgpt"],
        "windows_count": windows_count,
        "have_target_continuation": True,
        "labels_per_window": [
            ["__target__", "claude", "chatgpt"] for _ in range(windows_count)
        ],
        "distance_matrices": [
            [[0.0, 0.5, 0.6], [0.5, 0.0, 0.4], [0.6, 0.4, 0.0]]
            for _ in range(windows_count)
        ],
        "per_window_caveats": [[] for _ in range(windows_count)],
        "summary": {
            "claude": {"n_windows_compared": windows_count, "mean_vs_target": 0.5, "median_vs_target": 0.5, "min_vs_target": 0.5, "max_vs_target": 0.5},
            "chatgpt": {"n_windows_compared": windows_count, "mean_vs_target": 0.6, "median_vs_target": 0.6, "min_vs_target": 0.6, "max_vs_target": 0.6},
        },
        "ingested_sha256": "abc123" * 10,
        "global_caveats": [],
    }


def test_compose_envelope_has_required_fields():
    distances = _build_distances_payload()
    envelope, markdown = pack.compose(distances)
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "external_mirror_discrimination"
    assert envelope["tool"] == "compose_evidence_pack"
    assert envelope["available"] is True
    assert envelope["claim_license"]["task_surface"] == "external_mirror_discrimination"


def test_compose_envelope_propagates_caveats():
    distances = _build_distances_payload()
    distances["global_caveats"] = ["target_continuation_unavailable"]
    distances["per_window_caveats"][0] = ["family_claude_refused"]
    envelope, _ = pack.compose(distances)
    caveats = envelope["results"]["caveats"]
    assert "target_continuation_unavailable" in caveats
    assert "family_claude_refused" in caveats


def test_compose_envelope_dedupes_caveats():
    distances = _build_distances_payload()
    distances["per_window_caveats"][0] = ["dup_caveat", "dup_caveat"]
    distances["per_window_caveats"][1] = ["dup_caveat"]
    envelope, _ = pack.compose(distances)
    assert envelope["results"]["caveats"].count("dup_caveat") == 1


def test_compose_envelope_operator_license_override():
    distances = _build_distances_payload()
    envelope, _ = pack.compose(
        distances,
        licenses_text="custom license text",
        does_not_license_text="custom does-not text",
    )
    assert envelope["claim_license"]["licenses"] == "custom license text"
    assert envelope["claim_license"]["does_not_license"] == "custom does-not text"


def test_compose_markdown_has_expected_sections():
    distances = _build_distances_payload()
    _, md = pack.compose(distances)
    assert "# External Mirror Discrimination — Evidence Pack" in md
    assert "## Summary distances" in md
    assert "## Per-window distance matrices" in md
    assert "## Caveats" in md
    assert "## Claim license" in md
    assert "## Provenance" in md


def test_compose_markdown_distance_table_renders():
    distances = _build_distances_payload()
    _, md = pack.compose(distances)
    assert "`claude`" in md
    assert "`chatgpt`" in md
    assert "0.500" in md or "0.600" in md


def test_compose_markdown_handles_none_cells():
    distances = _build_distances_payload(windows_count=1)
    distances["distance_matrices"][0] = [
        [0.0, None, None],
        [None, 0.0, None],
        [None, None, 0.0],
    ]
    _, md = pack.compose(distances)
    assert "—" in md


# ============================================================
# Full pipeline integration
# ============================================================


def test_full_pipeline_t3_round_trip(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=2, continuation=10)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {
            1: "She walked into the bar and ordered a drink, watching the rain through the open door.",
            2: "Outside the rain fell steadily, drumming against the corrugated roof of the rum shop.",
        }},
        "human_control": {"format": "t3", "windows": {
            1: "The bartender nodded as she sat down, already reaching for the bottle behind the counter.",
            2: "A man stumbled in from the dark, shaking water from his hat and laughing at the storm.",
        }},
    })
    ingested = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    target_continuations = ["target w1 continuation goes here", "target w2 continuation goes here"]
    distances = dist.compute(ingested, target_continuations=target_continuations, backend=StubBackend())
    envelope, markdown = pack.compose(distances)
    assert envelope["task_surface"] == "external_mirror_discrimination"
    assert envelope["results"]["windows_count"] == 2
    assert set(envelope["results"]["families"]) == {"claude", "human_control"}
    assert "External Mirror Discrimination" in markdown


def test_full_pipeline_writes_evidence_pack_files(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=1, continuation=10)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "a continuation of suitable length goes here yes yes"}},
    })
    ingested_payload = ingest.ingest(prompts_dir, outputs_dir, strict=False)
    ingested_path = outputs_dir / "ingested.json"
    ingested_path.write_text(json.dumps(ingested_payload))

    distances_payload = dist.compute(ingested_payload, target_continuations=["target w1"], backend=StubBackend())
    distances_path = outputs_dir / "distances.json"
    distances_path.write_text(json.dumps(distances_payload))

    envelope, markdown = pack.compose(distances_payload)
    (outputs_dir / "evidence_pack.json").write_text(json.dumps(envelope, indent=2))
    (outputs_dir / "evidence_pack.md").write_text(markdown)

    assert (outputs_dir / "evidence_pack.json").exists()
    assert (outputs_dir / "evidence_pack.md").exists()
    reloaded = json.loads((outputs_dir / "evidence_pack.json").read_text())
    assert reloaded["task_surface"] == "external_mirror_discrimination"


# ============================================================
# CLI smoke
# ============================================================


def test_ingest_cli_returns_zero_on_success(tmp_path):
    prompts_dir = _make_manifest(tmp_path, windows_count=1)
    outputs_dir = _make_outputs(tmp_path, families={
        "claude": {"format": "t3", "windows": {1: "x " * 100}},
    })
    rc = ingest.main([str(prompts_dir), str(outputs_dir)])
    assert rc == 0
    assert (outputs_dir / "ingested.json").exists()


def test_ingest_cli_returns_nonzero_on_missing_manifest(tmp_path):
    prompts_dir = tmp_path / "prompts" / "broken"
    prompts_dir.mkdir(parents=True)
    outputs_dir = _make_outputs(tmp_path, families={"claude": {"format": "t3", "windows": {1: "x"}}})
    rc = ingest.main([str(prompts_dir), str(outputs_dir)])
    assert rc == 1
