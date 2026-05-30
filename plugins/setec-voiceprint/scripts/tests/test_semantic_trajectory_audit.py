#!/usr/bin/env python3
"""Regression tests for semantic_trajectory_audit.py (R12).

The script computes paragraph-level semantic-trajectory statistics
over an embedding trajectory. Tests pin:

  * Windowing strategies produce expected window counts on
    representative inputs (paragraph splitting + coalescing of
    short paragraphs + splitting of long ones; sentence and
    fixed-token strategies).
  * Cosine + adjacent-cosine series compute correctly on synthetic
    embedding fixtures (we don't load a real model in tests).
  * Drift, autocorrelation, and flatness summaries return the right
    shape on edge cases (constant series, monotone series, empty).
  * PROVISIONAL banding places numbers into the right buckets and
    emits the right alerts.
  * Baseline comparison reports descriptive deltas.
  * Full JSON output assembles the expected shape and carries the
    claim-license block.
  * The CLI honors --window-strategy, --json, --out.
  * Missing-deps path raises ``EmbeddingBackendError`` with a clear
    message.

The tests use a stub embedding backend that returns deterministic
synthetic vectors — we don't pay the cost of loading a real model
in CI, and the stat-computation paths are what the framework's
correctness rests on. Real-model integration is covered by the
§6.4 fixture suite (not run in unit tests).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import semantic_trajectory_audit as sta  # type: ignore
import embedding_backend as eb  # type: ignore


# --------------- Stub backend -----------------------------------


class _StubBackend:
    """Stand-in for ``EmbeddingBackend`` that returns deterministic
    synthetic vectors. Each window's vector is a one-hot in a unique
    dimension scaled by the window's length — so adjacent cosines
    are 0 by construction unless the test wants something else.

    Accepts the same kwargs as the real backend (``model_id``,
    ``revision``, ``deterministic``) plus a test-only ``vectors``
    override, so the CLI path can monkeypatch ``EmbeddingBackend``
    directly without adapter code.
    """

    def __init__(
        self,
        model_id: str = "stub-model",
        revision: str | None = "stub-rev",
        deterministic: bool = True,
        *,
        vectors=None,
        dtype=None,
        device=None,
    ):
        self._vectors = vectors
        self.model_id = model_id
        self.revision = revision
        self.deterministic = deterministic
        # The real EmbeddingBackend grew `dtype` and `device` kwargs;
        # accept and record them so the CLI path (which passes both)
        # constructs the stub without a TypeError.
        self.dtype = dtype
        self.device = device

    def encode(self, texts):
        import numpy as np
        if self._vectors is not None:
            return np.asarray(self._vectors, dtype="float32")
        dim = max(len(texts), 1)
        out = np.zeros((len(texts), dim), dtype="float32")
        for i in range(len(texts)):
            out[i, i] = 1.0
        return out

    def identifier_block(self):
        return {
            "id": self.model_id,
            "revision": self.revision,
            "alias": None,
            "deterministic_mode": True,
            "method": "stub",
        }


# --------------- Windowing --------------------------------------


def test_paragraph_split_basic():
    text = "Para one is here.\n\nPara two is here.\n\nPara three is here."
    # Each para is < MIN_PARA_TOKENS (25), so they'll coalesce.
    out = sta._split_paragraphs(text)
    assert len(out) == 1
    assert "Para one" in out[0]
    assert "Para three" in out[0]


def test_paragraph_split_coalesces_short_into_neighbor():
    # First two paras are short and will coalesce; third is long.
    long_para = " ".join(["word"] * 30)
    text = f"short.\n\n{long_para}\n\nshort end."
    out = sta._split_paragraphs(text)
    # short + long_para coalesce into one window; short end gets
    # appended onto the last window because it's the trailing
    # short paragraph.
    assert len(out) == 1
    assert "short." in out[0]
    assert "short end." in out[0]


def test_paragraph_split_breaks_long_paragraphs():
    # One paragraph that exceeds MAX_PARA_TOKENS (600).
    long_para = ". ".join([" ".join(["w"] * 50) for _ in range(20)]) + "."
    # That's roughly 1000 tokens in one paragraph.
    out = sta._split_paragraphs(long_para)
    assert len(out) > 1
    # Each chunk should be at most MAX_PARA_TOKENS-ish (allowing
    # for the trailing-sentence overshoot).
    for chunk in out:
        assert sta._approx_token_count(chunk) <= sta.MAX_PARA_TOKENS + 60


def test_sentence_split():
    text = "Sentence one. Sentence two? Sentence three!"
    out = sta._split_sentences(text)
    assert len(out) == 3


def test_fixed_token_split():
    text = " ".join(["w"] * 250)
    out = sta._split_fixed_token(text, 100)
    assert len(out) == 3  # 100 + 100 + 50
    assert sta._approx_token_count(out[0]) == 100
    assert sta._approx_token_count(out[-1]) == 50


def test_split_windows_dispatch():
    text = "A. B. C."
    assert len(sta.split_windows(text, "sentence")) == 3
    with pytest.raises(ValueError):
        sta.split_windows(text, "not-a-real-strategy")


# --------------- Cosine + trajectory math -----------------------


def test_cosine_basic():
    import numpy as np
    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0])
    assert sta._cosine(a, b) == pytest.approx(0.0)
    assert sta._cosine(a, a) == pytest.approx(1.0)


def test_cosine_zero_vector_returns_zero():
    import numpy as np
    a = np.zeros(3)
    b = np.array([1.0, 0.0, 0.0])
    assert sta._cosine(a, b) == 0.0


def test_adjacent_cosine_series_on_orthogonal_embeddings():
    """Orthogonal one-hot embeddings → all adjacent cosines == 0."""
    backend = _StubBackend()
    embs = backend.encode(["a", "b", "c", "d"])
    series = sta.adjacent_cosine_series(embs)
    assert len(series) == 3
    for s in series:
        assert s == pytest.approx(0.0)


def test_adjacent_cosine_series_on_identical_embeddings():
    """Identical embeddings → all adjacent cosines == 1."""
    import numpy as np
    vec = np.array([[1.0, 2.0, 3.0]] * 4, dtype="float32")
    series = sta.adjacent_cosine_series(vec)
    assert len(series) == 3
    for s in series:
        assert s == pytest.approx(1.0)


def test_adjacent_cosine_series_empty_on_short_input():
    assert sta.adjacent_cosine_series([]) == []
    import numpy as np
    assert sta.adjacent_cosine_series(np.zeros((1, 3))) == []


def test_linear_regression_slope_constant_series():
    out = sta._linear_regression_slope([0.0, 1.0, 2.0], [0.5, 0.5, 0.5])
    assert out["slope"] == pytest.approx(0.0)


def test_linear_regression_slope_monotone():
    out = sta._linear_regression_slope([0.0, 1.0, 2.0, 3.0], [0.0, 0.1, 0.2, 0.3])
    assert out["slope"] == pytest.approx(0.1, abs=1e-6)


def test_autocorrelation_constant_series_returns_zero():
    assert sta.autocorrelation([0.5, 0.5, 0.5], 1) == 0.0


def test_autocorrelation_basic():
    # Series [1, 0, 1, 0, 1, 0]: lag-1 autocorr should be strongly
    # negative. The function returns sample autocorrelation, so it's
    # bounded in [-1, 1].
    out = sta.autocorrelation([1.0, 0.0, 1.0, 0.0, 1.0, 0.0], 1)
    assert out < 0.0


def test_flatness_summary_counts_and_longest_run():
    # Mostly low cosines, one run of 4 above 0.9.
    series = [0.2, 0.95, 0.94, 0.91, 0.93, 0.3, 0.92, 0.5]
    flat = sta.flatness_summary(series)
    assert flat["counts_above"]["0.90"] == 5
    assert flat["counts_above"]["0.95"] == 1
    assert flat["longest_run_above_0.9"] == 4


def test_flatness_summary_empty():
    flat = sta.flatness_summary([])
    assert flat["longest_run_above_0.9"] == 0


# --------------- Trajectory aggregation -------------------------


def test_compute_trajectory_full_shape():
    import numpy as np
    # 5 windows, mostly similar to each other (positive cosines).
    embs = np.array([
        [1.0, 0.1, 0.0],
        [1.0, 0.0, 0.0],
        [0.95, 0.05, 0.0],
        [0.9, 0.1, 0.0],
        [0.85, 0.15, 0.0],
    ], dtype="float32")
    out = sta.compute_trajectory(embs, [50, 50, 50, 50, 50])
    assert out["n_windows"] == 5
    assert out["adjacent_cosines"]["n_pairs"] == 4
    assert out["adjacent_cosines"]["mean"] > 0.9
    assert out["drift"]["first_to_last_cosine"] > 0.9
    assert "lag_1" in out["autocorrelation"]
    assert "longest_run_above_0.9" in out["flatness"]


def test_compute_trajectory_handles_too_short():
    import numpy as np
    embs = np.array([[1.0, 0.0]], dtype="float32")
    out = sta.compute_trajectory(embs, [50])
    assert out["adjacent_cosines"]["n_pairs"] == 0
    assert out["adjacent_cosines"]["mean"] is None
    assert out["drift"]["first_to_last_cosine"] is None


# --------------- PROVISIONAL banding ----------------------------


def test_banding_typical_range():
    trajectory = {
        "adjacent_cosines": {"mean": 0.87, "n_pairs": 5},
        "drift": {"regression": {"slope": 0.001}},
        "flatness": {"longest_run_above_0.9": 1},
    }
    out = sta.provisional_banding(trajectory)
    assert out["band"] == "typical"
    assert out["provisional"] is True
    assert out["calibration_anchor"] == "user-baseline-required"


def test_banding_very_tight_alerts_on_long_run():
    trajectory = {
        "adjacent_cosines": {"mean": 0.96, "n_pairs": 8},
        "drift": {"regression": {"slope": 0.001}},
        "flatness": {"longest_run_above_0.9": 7},
    }
    out = sta.provisional_banding(trajectory)
    assert out["band"] == "very_tight"
    assert any("longest run" in a for a in out["alerts"])


def test_banding_drift_alert():
    trajectory = {
        "adjacent_cosines": {"mean": 0.83, "n_pairs": 8},
        "drift": {"regression": {"slope": -0.02}},
        "flatness": {"longest_run_above_0.9": 0},
    }
    out = sta.provisional_banding(trajectory)
    assert out["band"] == "drifting"
    assert any("drift slope" in a for a in out["alerts"])


def test_banding_insufficient_data():
    trajectory = {
        "adjacent_cosines": {"mean": None},
        "drift": {"regression": {"slope": 0.0}},
        "flatness": {"longest_run_above_0.9": 0},
    }
    out = sta.provisional_banding(trajectory)
    assert out["band"] == "insufficient_data"


# --------------- Baseline comparison ----------------------------


def test_baseline_comparison_basic(tmp_path: Path):
    baseline = {
        "trajectory": {
            "adjacent_cosines": {"mean": 0.80, "sd": 0.10},
            "drift": {
                "first_to_last_cosine": 0.75,
                "regression": {"slope": 0.001},
            },
            "autocorrelation": {"lag_1": 0.20},
            "flatness": {"longest_run_above_0.9": 1},
        },
        "model": {"id": "stub-baseline"},
        "windowing": {"strategy": "paragraph"},
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    current = {
        "trajectory": {
            "adjacent_cosines": {"mean": 0.90, "sd": 0.05},
            "drift": {
                "first_to_last_cosine": 0.85,
                "regression": {"slope": 0.005},
            },
            "autocorrelation": {"lag_1": 0.35},
            "flatness": {"longest_run_above_0.9": 5},
        },
    }
    out = sta.compare_to_baseline(current, baseline_path)
    assert out["baseline_path"] == str(baseline_path)
    assert out["baseline_model_id"] == "stub-baseline"
    rows = {r["field"]: r for r in out["deltas"]}
    assert rows["mean_adjacent_cosine"]["delta"] == pytest.approx(0.10)
    assert rows["longest_run_above_0.9"]["delta"] == pytest.approx(4.0)


def test_baseline_comparison_handles_missing_file(tmp_path: Path):
    out = sta.compare_to_baseline({}, tmp_path / "nope.json")
    assert "error" in out


# --------------- Full assembly ----------------------------------


def _two_para_text():
    para_a = " ".join(["alpha"] * 40)
    para_b = " ".join(["beta"] * 40)
    return f"{para_a}\n\n{para_b}"


def test_assemble_output_full_shape(tmp_path: Path):
    text = _two_para_text()
    backend = _StubBackend()
    out = sta.assemble_output(
        text,
        backend=backend,
        window_strategy="paragraph",
        window_size=200,
        baseline_path=None,
        source_path=Path("synthetic.txt"),
    )
    assert out["task_surface"] == "voice_coherence"
    assert out["tool"] == "semantic_trajectory_audit"
    assert out["model"]["id"] == "stub-model"
    assert out["windowing"]["strategy"] == "paragraph"
    assert out["trajectory"] is not None
    assert out["provisional_banding"]["provisional"] is True
    assert out["claim_license"]["task_surface"] == "voice_coherence"
    assert (
        "Authorship verdicts"
        in out["claim_license"]["does_not_license"]
    )


def test_assemble_output_too_few_windows_emits_warning():
    text = "tiny."
    backend = _StubBackend()
    out = sta.assemble_output(
        text,
        backend=backend,
        window_strategy="paragraph",
        window_size=200,
        baseline_path=None,
        source_path=None,
    )
    assert "warning" in out
    assert out["trajectory"] is None
    # Claim-license is still emitted so consumers parse uniformly.
    assert out["claim_license"]["task_surface"] == "voice_coherence"


def test_assemble_output_with_baseline(tmp_path: Path):
    baseline = {
        "trajectory": {
            "adjacent_cosines": {"mean": 0.0},
            "drift": {"first_to_last_cosine": 0.0, "regression": {"slope": 0.0}},
            "autocorrelation": {"lag_1": 0.0},
            "flatness": {"longest_run_above_0.9": 0},
        },
        "model": {"id": "stub-baseline"},
        "windowing": {"strategy": "paragraph"},
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    text = _two_para_text()
    backend = _StubBackend()
    out = sta.assemble_output(
        text,
        backend=backend,
        window_strategy="paragraph",
        window_size=200,
        baseline_path=baseline_path,
        source_path=None,
    )
    assert out["baseline_comparison"] is not None
    assert out["baseline_comparison"]["baseline_model_id"] == "stub-baseline"


# --------------- Markdown rendering -----------------------------


def test_render_markdown_basic(tmp_path: Path):
    text = _two_para_text()
    backend = _StubBackend()
    payload = sta.assemble_output(
        text,
        backend=backend,
        window_strategy="paragraph",
        window_size=200,
        baseline_path=None,
        source_path=Path("synthetic.txt"),
    )
    md = sta.render_markdown(payload)
    assert "# Semantic trajectory audit" in md
    assert "task_surface" not in md  # rendered prettily
    assert "voice_coherence" in md
    assert "## Trajectory statistics" in md
    assert "PROVISIONAL banding" in md
    assert "{TODO: interpret}" in md
    # Claim-license block must appear in the markdown (PR #16 P2
    # regression, 2026-05-12). The JSON output carries it; the
    # earlier renderer dropped it. Both surfaces should now carry
    # the licensure section since R12 ships PROVISIONAL under the
    # Stylometry-to-the-people policy and the claim-license is the
    # load-bearing licensure surface.
    assert "## Claim license" in md
    assert "Authorship verdicts" in md  # from the does_not_license text
    assert "user-baseline-required" in md  # from comparison_set


def test_render_markdown_handles_warning_short_text():
    """The warning-short-circuit path still needs to render the
    claim_license block. A reader hitting the warning path
    ("too few windows produced") otherwise has no licensure
    information at all — and that's exactly the moment a
    licensure refusal matters most."""
    payload = {
        "task_surface": "voice_coherence",
        "tool": "semantic_trajectory_audit",
        "tool_version": "1.0",
        "warning": "only 1 window produced",
        "source": "tiny.txt",
        "claim_license": {
            "task_surface": "voice_coherence",
            "licenses": "Nothing — too few windows to compute.",
            "does_not_license": "Authorship verdicts.",
            "comparison_set": {"anchor": "PROVISIONAL"},
            "additional_caveats": ["Test caveat present."],
            "references": [],
        },
    }
    md = sta.render_markdown(payload)
    assert "Warning" in md
    assert "only 1 window" in md
    # Even the warning path must carry the claim-license block.
    assert "## Claim license" in md
    assert "Authorship verdicts" in md
    assert "Test caveat present." in md


# --------------- CLI --------------------------------------------


def test_cli_writes_json_to_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    src = tmp_path / "draft.txt"
    src.write_text(_two_para_text(), encoding="utf-8")
    out_path = tmp_path / "trajectory.json"
    # Patch the EmbeddingBackend constructor to return the stub.
    monkeypatch.setattr(sta, "EmbeddingBackend", _StubBackend)
    rc = sta.main([str(src), "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    # schema_version 1.0 envelope: task_surface stays top-level,
    # windowing rides under envelope.target (target_extra).
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "voice_coherence"
    assert payload["tool"] == "semantic_trajectory_audit"
    assert payload["target"]["windowing"]["strategy"] == "paragraph"


def test_cli_missing_source_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    rc = sta.main([str(tmp_path / "nope.txt")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err


def test_cli_writes_markdown_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    src = tmp_path / "draft.txt"
    src.write_text(_two_para_text(), encoding="utf-8")
    md_path = tmp_path / "trajectory.md"
    monkeypatch.setattr(sta, "EmbeddingBackend", _StubBackend)
    rc = sta.main([
        str(src), "--json",
        "--out", str(tmp_path / "j.json"),
        "--markdown-out", str(md_path),
    ])
    assert rc == 0
    md = md_path.read_text(encoding="utf-8")
    assert "# Semantic trajectory audit" in md


# --------------- Embedding-backend error handling ---------------


def test_main_surfaces_embedding_backend_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """If the embedding backend can't load, ``main()`` should exit 3
    with a clear stderr message — not silently produce empty
    output."""
    src = tmp_path / "draft.txt"
    src.write_text(_two_para_text(), encoding="utf-8")

    class _Broken:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts):
            raise sta.EmbeddingBackendError("test failure mode")

        def identifier_block(self):
            return {"id": "broken", "method": "test"}

    monkeypatch.setattr(sta, "EmbeddingBackend", _Broken)
    rc = sta.main([str(src)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "test failure mode" in err
