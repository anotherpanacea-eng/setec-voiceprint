"""Tests for ``external_mirror/build_prompts.py``.

Pin the Phase A prompt-builder contract: positioning strategies,
hygiene step, output format shapes, MANIFEST.json schema, edge-case
errors. No LLM calls — the prompt-builder is pure text processing.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "external_mirror"))

import build_prompts as bp  # noqa: E402


# ============================================================
# Helpers
# ============================================================


def _make_target(tmp_path: Path, n_words: int = 3000, name: str = "target.txt") -> Path:
    """Write a synthetic target with ``n_words`` whitespace-delimited tokens."""
    words = [f"word{i:04d}" for i in range(n_words)]
    text = " ".join(words)
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _build_defaults(target_path: Path, out_root: Path, **overrides):
    """Build with sensible defaults; overrides win."""
    defaults = dict(
        target_path=target_path,
        out_root=out_root,
        windows=4,
        context=500,
        continuation=150,
        positioning="equal_skipping_opening",
        positions=None,
        context_grid=None,
        fmt="both",
        genre_descriptor="literary prose",
        run_id="test_run",
    )
    defaults.update(overrides)
    return bp.build(**defaults)


# ============================================================
# Tokenizer + normalization
# ============================================================


def test_tokenize_preserves_punctuation_and_capitals():
    text = "Hello, world! This is a test."
    tokens = bp.tokenize(text)
    assert len(tokens) == 6
    sliced = bp.slice_words(text, tokens, 0, 6)
    assert sliced == "Hello, world! This is a test."


def test_tokenize_handles_newlines_between_paragraphs():
    text = "First paragraph here.\n\nSecond paragraph here."
    tokens = bp.tokenize(text)
    assert len(tokens) == 6
    sliced = bp.slice_words(text, tokens, 0, 6)
    assert sliced == text


def test_normalize_text_collapses_excessive_newlines_but_preserves_paragraphs():
    text = "A\n\n\n\nB"
    out = bp.normalize_text(text)
    assert out == "A\n\nB"


def test_normalize_text_normalizes_crlf():
    text = "A\r\nB\rC"
    out = bp.normalize_text(text)
    assert out == "A\nB\nC"


def test_normalize_text_strips_trailing_whitespace_per_line():
    text = "hello   \nworld\t\n"
    out = bp.normalize_text(text)
    assert out == "hello\nworld\n"


def test_slice_words_preserves_inline_whitespace():
    text = "alpha  beta   gamma"
    tokens = bp.tokenize(text)
    sliced = bp.slice_words(text, tokens, 0, 3)
    assert sliced == "alpha  beta   gamma"


# ============================================================
# Positioning strategies
# ============================================================


def test_equal_skipping_opening_positions_window_1_has_full_context(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    specs = result.window_specs
    assert len(specs) == 4
    for spec in specs:
        assert spec.context_word_count == 500
        assert spec.context_end_word - spec.context_start_word == 500
        assert spec.continuation_end_word - spec.continuation_start_word == 150
        assert spec.context_start_word >= 0
        assert spec.continuation_end_word <= 3000


def test_three_positioning_strategies_produce_different_position_lists(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    out_c = tmp_path / "c"
    out_a.mkdir()
    out_b.mkdir()
    out_c.mkdir()

    equal = _build_defaults(target, out_a, positioning="equal", run_id="r_equal")
    eso = _build_defaults(target, out_b, positioning="equal_skipping_opening", run_id="r_eso")
    strat = _build_defaults(target, out_c, positioning="stratified", run_id="r_strat")

    p_equal = [s.continuation_start_word for s in equal.window_specs]
    p_eso = [s.continuation_start_word for s in eso.window_specs]
    p_strat = [s.continuation_start_word for s in strat.window_specs]
    assert p_equal != p_eso
    assert p_eso != p_strat
    assert p_equal != p_strat


def test_custom_positioning_round_trips_supplied_positions(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        positioning="custom",
        positions=[800, 1600, 2400],
        windows=3,
    )
    assert [s.continuation_start_word for s in result.window_specs] == [800, 1600, 2400]


def test_custom_positioning_requires_positions(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    with pytest.raises(ValueError, match="custom requires --positions"):
        _build_defaults(target, tmp_path, positioning="custom", positions=None)


def test_custom_positioning_validates_context_underflow(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    with pytest.raises(ValueError, match="insufficient context"):
        _build_defaults(
            target, tmp_path,
            positioning="custom",
            positions=[100],  # < M=500
            windows=1,
        )


def test_custom_positioning_validates_continuation_overflow(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    with pytest.raises(ValueError, match="insufficient continuation room"):
        _build_defaults(
            target, tmp_path,
            positioning="custom",
            positions=[2900],  # 2900 + N=150 > 3000
            windows=1,
        )


# ============================================================
# Expanding-context regime (Design 4)
# ============================================================


def test_expanding_emits_one_window_per_grid_entry(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        positioning="expanding",
        context_grid=[500, 1000, 1500, 2000],
    )
    specs = result.window_specs
    assert len(specs) == 4
    for spec, expected_ctx in zip(specs, [500, 1000, 1500, 2000]):
        assert spec.context_start_word == 0
        assert spec.context_end_word == expected_ctx
        assert spec.context_word_count == expected_ctx
        assert spec.continuation_start_word == expected_ctx
        assert spec.continuation_end_word == expected_ctx + 150


def test_expanding_requires_context_grid(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    with pytest.raises(ValueError, match="expanding requires --context-grid"):
        _build_defaults(target, tmp_path, positioning="expanding", context_grid=None)


def test_expanding_validates_grid_against_text_length(tmp_path):
    target = _make_target(tmp_path, n_words=1000)
    with pytest.raises(ValueError, match="exceeds target word count"):
        _build_defaults(
            target, tmp_path,
            positioning="expanding",
            context_grid=[500, 1500],  # 1500 + 150 > 1000
        )


def test_expanding_manifest_records_grid_and_nulls_context(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        positioning="expanding",
        context_grid=[500, 1000],
    )
    assert result.manifest["context"] is None
    assert result.manifest["context_grid"] == [500, 1000]
    assert result.manifest["positioning"] == "expanding"


# ============================================================
# Hygiene + edge cases
# ============================================================


def test_target_too_short_for_equal_skipping_opening_errors_clearly(tmp_path):
    target = _make_target(tmp_path, n_words=200)
    with pytest.raises(ValueError, match="requires at least"):
        _build_defaults(target, tmp_path)


def test_target_with_only_one_word_errors(tmp_path):
    target = tmp_path / "tiny.txt"
    target.write_text("singleton", encoding="utf-8")
    with pytest.raises(ValueError, match=">= 2 word tokens"):
        _build_defaults(target, tmp_path)


def test_k_eq_1_records_caveat(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        positioning="custom",
        positions=[1500],
        windows=1,
    )
    assert "low_window_count_n_eq_1" in result.manifest["caveats_recommended"]


def test_overlapping_windows_recorded_as_caveat(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    # Custom positions 600 and 800 with M=500 → contexts [100:600] and [300:800] overlap.
    result = _build_defaults(
        target, tmp_path,
        positioning="custom",
        positions=[600, 800],
        windows=2,
    )
    assert "overlapping_windows" in result.manifest["caveats_recommended"]


def test_non_overlapping_windows_no_overlap_caveat(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)  # equal_skipping_opening, K=4, M=500
    assert "overlapping_windows" not in result.manifest["caveats_recommended"]


# ============================================================
# Output format shapes
# ============================================================


def test_separate_format_emits_one_file_per_window(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="separate")
    files = sorted(result.out_dir.glob("window_*.md"))
    assert len(files) == 4
    assert [f.name for f in files] == ["window_1.md", "window_2.md", "window_3.md", "window_4.md"]
    assert not (result.out_dir / "windows_batched.md").exists()


def test_batched_format_emits_single_batched_file(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="batched")
    assert (result.out_dir / "windows_batched.md").exists()
    assert not list(result.out_dir.glob("window_*.md"))


def test_both_format_emits_both(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="both")
    assert (result.out_dir / "windows_batched.md").exists()
    assert len(list(result.out_dir.glob("window_*.md"))) == 4


def test_separate_prompt_has_t3_structure(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="separate")
    body = (result.out_dir / "window_1.md").read_text()
    assert "--- BEGIN CONTEXT ---" in body
    assert "--- END CONTEXT ---" in body
    assert "approximately 150 words" in body
    assert "Continue here:" in body
    assert "Output ONLY the continuation" in body


def test_batched_prompt_has_t4_structure(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="batched")
    body = (result.out_dir / "windows_batched.md").read_text()
    assert "WINDOW 1" in body
    assert "WINDOW 4" in body
    assert "JSON array" in body
    assert "fresh conversation" in body
    assert "approximately 150 words" in body


def test_genre_descriptor_injected_verbatim_in_separate_format(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        fmt="separate",
        genre_descriptor="science journalism (long-form)",
    )
    body = (result.out_dir / "window_1.md").read_text()
    assert "science journalism (long-form)" in body


def test_genre_descriptor_injected_verbatim_in_batched_format(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(
        target, tmp_path,
        fmt="batched",
        genre_descriptor="literary fiction Caribbean Anglo",
    )
    body = (result.out_dir / "windows_batched.md").read_text()
    assert "literary fiction Caribbean Anglo" in body


# ============================================================
# MANIFEST.json schema and round-trip
# ============================================================


REQUIRED_MANIFEST_FIELDS = [
    "run_id", "target_path", "target_sha256", "target_word_count",
    "positioning", "continuation", "context", "context_grid",
    "windows_count", "windows", "genre_descriptor", "format",
    "tool_path", "tool_sha256", "git_head_sha", "built_at",
    "caveats_recommended",
]


def test_manifest_has_all_required_fields(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    for field in REQUIRED_MANIFEST_FIELDS:
        assert field in result.manifest, f"missing manifest field: {field}"


def test_manifest_round_trips_through_disk(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    on_disk = json.loads((result.out_dir / "MANIFEST.json").read_text())
    assert on_disk == result.manifest


def test_manifest_target_sha256_matches_normalized_target(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    raw = target.read_text(encoding="utf-8")
    normalized = bp.normalize_text(raw)
    expected = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert result.manifest["target_sha256"] == expected


def test_manifest_per_window_context_sha256_matches_emitted_prompt(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path, fmt="separate")
    for spec_dict in result.manifest["windows"]:
        idx = spec_dict["window_index"]
        prompt_body = (result.out_dir / f"window_{idx}.md").read_text()
        m = re.search(
            r"--- BEGIN CONTEXT ---\n(.*?)\n--- END CONTEXT ---",
            prompt_body,
            re.DOTALL,
        )
        assert m, f"window_{idx}.md missing context delimiters"
        ctx = m.group(1)
        expected = hashlib.sha256(ctx.encode("utf-8")).hexdigest()
        assert spec_dict["context_sha256"] == expected, (
            f"window {idx}: manifest hash != hash of emitted context"
        )


def test_manifest_tool_sha256_matches_build_prompts_file(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    expected = hashlib.sha256(Path(bp.__file__).read_bytes()).hexdigest()
    assert result.manifest["tool_sha256"] == expected


def test_manifest_windows_count_matches_emitted(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    result = _build_defaults(target, tmp_path)
    assert result.manifest["windows_count"] == len(result.window_specs)
    assert result.manifest["windows_count"] == len(result.manifest["windows"])


# ============================================================
# Determinism
# ============================================================


def test_two_builds_with_same_inputs_produce_same_window_specs(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    r1 = _build_defaults(target, tmp_path / "r1", run_id="run1")
    r2 = _build_defaults(target, tmp_path / "r2", run_id="run2")
    specs_1 = [(s.context_start_word, s.continuation_end_word) for s in r1.window_specs]
    specs_2 = [(s.context_start_word, s.continuation_end_word) for s in r2.window_specs]
    assert specs_1 == specs_2


def test_two_builds_with_same_inputs_produce_same_context_hashes(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    r1 = _build_defaults(target, tmp_path / "r1", run_id="run1")
    r2 = _build_defaults(target, tmp_path / "r2", run_id="run2")
    hashes_1 = [s.context_sha256 for s in r1.window_specs]
    hashes_2 = [s.context_sha256 for s in r2.window_specs]
    assert hashes_1 == hashes_2


# ============================================================
# Output-dir behaviour
# ============================================================


def test_existing_run_id_errors_to_prevent_silent_overwrite(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    _build_defaults(target, tmp_path, run_id="dup")
    with pytest.raises(FileExistsError):
        _build_defaults(target, tmp_path, run_id="dup")


# ============================================================
# CLI smoke test
# ============================================================


def test_cli_smoke_end_to_end(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    out = tmp_path / "cli_out"
    rc = bp.main([
        str(target),
        "--windows", "4",
        "--context", "500",
        "--continuation", "150",
        "--positioning", "equal_skipping_opening",
        "--out", str(out),
        "--format", "both",
        "--genre-descriptor", "test genre",
        "--run-id", "cli_smoke",
    ])
    assert rc == 0
    assert (out / "cli_smoke" / "MANIFEST.json").exists()
    assert (out / "cli_smoke" / "windows_batched.md").exists()
    assert len(list((out / "cli_smoke").glob("window_*.md"))) == 4


def test_cli_smoke_expanding(tmp_path):
    target = _make_target(tmp_path, n_words=3000)
    out = tmp_path / "cli_out"
    rc = bp.main([
        str(target),
        "--continuation", "150",
        "--positioning", "expanding",
        "--context-grid", "500,1000,1500,2000",
        "--out", str(out),
        "--format", "separate",
        "--run-id", "cli_expanding",
    ])
    assert rc == 0
    assert (out / "cli_expanding" / "MANIFEST.json").exists()
    assert len(list((out / "cli_expanding").glob("window_*.md"))) == 4
    manifest = json.loads((out / "cli_expanding" / "MANIFEST.json").read_text())
    assert manifest["context_grid"] == [500, 1000, 1500, 2000]


def test_cli_returns_nonzero_on_invalid_input(tmp_path):
    target = tmp_path / "tiny.txt"
    target.write_text("word", encoding="utf-8")
    rc = bp.main([str(target), "--out", str(tmp_path / "out"), "--run-id", "bad"])
    assert rc == 1
