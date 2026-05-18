"""Tests for ``calibration/_bakeoff_provenance.py``.

The provenance helper is the testable core of the cloud bake-off
matrix runner (``calibration/bakeoff_matrix.sh``): the shell
driver only does env-var validation and per-cell process
invocations; everything else — the skip-if-done check, the
provenance dict shape, the post-run summary — lives here and gets
unit-tested in isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Make the calibration package importable as a top-level module
# under the test harness. The harness adds plugin scripts/ to the
# path via conftest; calibration is one level deeper.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "calibration"))

import _bakeoff_provenance as bp  # noqa: E402


# ============================================================
# is_survey_complete
# ============================================================


def test_is_survey_complete_returns_true_for_well_formed_survey(tmp_path: Path):
    """A survey JSON with a non-empty ``rows`` list is treated as
    complete. This is the load-bearing skip-if-done check the
    bash driver uses to make restarts cheap."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps({"rows": [{"signal": "x"}], "meta": {}}))
    assert bp.is_survey_complete(p) is True


def test_is_survey_complete_returns_false_for_empty_rows(tmp_path: Path):
    """A survey JSON with an empty ``rows`` list falls through to a
    re-run — partial files shouldn't pin a cell as done."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps({"rows": [], "meta": {}}))
    assert bp.is_survey_complete(p) is False


def test_is_survey_complete_returns_false_for_missing_rows_key(tmp_path: Path):
    """A JSON without a ``rows`` key is treated as not-done. Some
    early-failure write paths produce only the meta dict; those
    must be re-runnable."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps({"meta": {"reason": "crashed before rows"}}))
    assert bp.is_survey_complete(p) is False


def test_is_survey_complete_returns_false_for_nonexistent_file(tmp_path: Path):
    """Never-run cell → file doesn't exist → not done."""
    assert bp.is_survey_complete(tmp_path / "does-not-exist.json") is False


def test_is_survey_complete_returns_false_for_empty_file(tmp_path: Path):
    """Zero-byte file from a crashed write → not done."""
    p = tmp_path / "survey.json"
    p.touch()
    assert bp.is_survey_complete(p) is False


def test_is_survey_complete_returns_false_for_corrupt_json(tmp_path: Path):
    """Truncated / corrupted JSON → not done (don't trip a parser
    failure into a skip; that would silently lose a cell)."""
    p = tmp_path / "survey.json"
    p.write_text("{rows: [...this is not json")
    assert bp.is_survey_complete(p) is False


def test_is_survey_complete_returns_false_for_non_dict_root(tmp_path: Path):
    """A JSON whose root is a list / int / etc. is not a survey
    file — treat as not-done."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps([{"signal": "x"}]))
    assert bp.is_survey_complete(p) is False


# ============================================================
# build_provenance
# ============================================================


def _make_inputs(tmp_path: Path, **overrides) -> bp.ProvenanceInputs:
    defaults = dict(
        session_id="20260518_120000",
        corpus_label="mage",
        manifest_path=tmp_path / "manifest.jsonl",
        phase_a_aliases=["mxbai", "minilm"],
        phase_b_aliases=["tinyllama", "olmo2_1b"],
        phase_a_signals=["adjacent_cosine_mean", "adjacent_cosine_sd"],
        phase_b_signals=[
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ],
        phase_a_paths={
            "mxbai": "mixedbread-ai/mxbai-embed-large-v1",
            "minilm": "sentence-transformers/all-MiniLM-L6-v2",
        },
        phase_b_paths={
            "tinyllama": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "olmo2_1b": "allenai/OLMo-2-0425-1B",
        },
        max_entries=5000,
        bootstrap_engine="torch",
        bootstrap_resamples=2000,
        fpr_target=0.01,
        cooldown_sec=10,
        survey_dir=tmp_path / "survey",
    )
    defaults.update(overrides)
    return bp.ProvenanceInputs(**defaults)


def test_build_provenance_includes_all_input_fields(tmp_path: Path):
    """The dict roundtrips every input field — operators consuming
    provenance.json downstream rely on a stable shape."""
    inputs = _make_inputs(tmp_path)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["session_id"] == "20260518_120000"
    assert prov["corpus_label"] == "mage"
    assert prov["phases"]["A"]["aliases"] == ["mxbai", "minilm"]
    assert prov["phases"]["B"]["aliases"] == ["tinyllama", "olmo2_1b"]
    assert prov["phases"]["A"]["signals"] == [
        "adjacent_cosine_mean", "adjacent_cosine_sd",
    ]
    assert prov["calibration_args"]["max_entries"] == 5000
    assert prov["calibration_args"]["bootstrap_engine"] == "torch"
    assert prov["calibration_args"]["bootstrap_resamples"] == 2000
    assert prov["calibration_args"]["fpr_target"] == 0.01
    assert prov["cooldown_sec"] == 10


def test_build_provenance_records_host_python_version(tmp_path: Path):
    """``host.python`` is the running interpreter's version. Lets
    cloud reruns flag environment skew (e.g., a venv update
    between sessions)."""
    inputs = _make_inputs(tmp_path)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["host"]["python"].count(".") >= 1
    # Sanity: matches the current interpreter exactly.
    assert prov["host"]["python"] == sys.version.split()[0]


def test_build_provenance_manifest_size_handles_missing_file(tmp_path: Path):
    """If the manifest path doesn't exist (e.g., dry-run before
    the operator has staged data), ``manifest.size_bytes`` is
    None rather than blowing up. Provenance writes should never
    block on best-effort fields."""
    inputs = _make_inputs(
        tmp_path, manifest_path=tmp_path / "nope.jsonl",
    )
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["manifest"]["size_bytes"] is None
    assert prov["manifest"]["path"].endswith("nope.jsonl")


def test_build_provenance_manifest_size_present_when_file_exists(tmp_path: Path):
    """When the manifest does exist, the recorded size matches."""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_bytes(b"abc\n")
    inputs = _make_inputs(tmp_path, manifest_path=manifest)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["manifest"]["size_bytes"] == 4


def test_build_provenance_repo_head_sha_optional(tmp_path: Path):
    """``repo_head_sha`` is None when the directory isn't a git
    repo. Provenance must still produce a valid dict; rerunning
    inside the framework's actual repo (with git) populates it."""
    inputs = _make_inputs(tmp_path)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    # tmp_path is not a git repo; expect None.
    assert prov["repo_head_sha"] is None


def test_build_provenance_repo_head_sha_present_inside_a_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``repo_head_sha`` is the short HEAD SHA when ``repo_root``
    is a real git checkout. Mocks the subprocess.run call so the
    test doesn't depend on the test runner's CWD being a git
    checkout (CI bind-mounts may not have a .git)."""
    captured: list[dict] = []

    class _FakeProc:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def fake_run(cmd, **kwargs):
        captured.append({"cmd": cmd, "cwd": kwargs.get("cwd")})
        return _FakeProc("abcdef1\n", 0)

    monkeypatch.setattr(bp.subprocess, "run", fake_run)
    inputs = _make_inputs(tmp_path)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["repo_head_sha"] == "abcdef1"
    assert captured[0]["cmd"] == ["git", "rev-parse", "--short", "HEAD"]


def test_build_provenance_includes_cuda_visible_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``host.cuda_visible_devices`` mirrors the runtime env. For
    multi-GPU cloud hosts running parallel matrix copies pinned to
    different GPUs, the recorded value differentiates the
    session's GPU assignment."""
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    inputs = _make_inputs(tmp_path)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["host"]["cuda_visible_devices"] == "1"


def test_build_provenance_max_entries_passes_none_through(tmp_path: Path):
    """``max_entries=None`` (full-corpus run) is preserved in the
    output, not coerced to 0 or omitted."""
    inputs = _make_inputs(tmp_path, max_entries=None)
    prov = bp.build_provenance(inputs, repo_root=tmp_path)
    assert prov["calibration_args"]["max_entries"] is None


# ============================================================
# write_provenance
# ============================================================


def test_write_provenance_writes_pretty_json_with_trailing_newline(tmp_path: Path):
    """Provenance files are human-readable artifacts an operator
    may diff across sessions. Pretty-printed (indent=2), keys
    sorted for stable diffs, trailing newline."""
    inputs = _make_inputs(tmp_path)
    out = tmp_path / "prov.json"
    bp.write_provenance(inputs, repo_root=tmp_path, out_path=out)
    text = out.read_text()
    assert text.endswith("\n")
    assert "  " in text  # indented
    # Keys at the top level are sorted alphabetically.
    parsed = json.loads(text)
    top_keys = list(parsed.keys())
    assert top_keys == sorted(top_keys)


def test_write_provenance_overwrites_existing(tmp_path: Path):
    """Running the matrix twice in the same log dir should leave
    a clean provenance.json each time (each session writes its
    own session_id; no append)."""
    out = tmp_path / "prov.json"
    out.write_text("old garbage")
    inputs = _make_inputs(tmp_path)
    bp.write_provenance(inputs, repo_root=tmp_path, out_path=out)
    parsed = json.loads(out.read_text())
    assert parsed["session_id"] == "20260518_120000"


# ============================================================
# summarize_matrix
# ============================================================


def _write_survey(
    survey_dir: Path, *, phase: str, alias: str, rows: list[dict],
) -> None:
    """Helper to drop a synthetic survey JSON in the conventional
    layout: ``survey_phase{A,B}_{alias}.json``."""
    survey_dir.mkdir(parents=True, exist_ok=True)
    p = survey_dir / f"survey_phase{phase}_{alias}.json"
    p.write_text(json.dumps({"rows": rows}))


def test_summarize_matrix_renders_phase_a_and_phase_b_tables(tmp_path: Path):
    """The summary contains both Phase A and Phase B tables with
    the expected signal columns. Mirrors the laptop template's
    output shape so existing consumers (manual-review,
    grep-driven scripts) keep working."""
    survey_dir = tmp_path / "surveys"
    _write_survey(
        survey_dir, phase="A", alias="mxbai", rows=[
            {"signal": "adjacent_cosine_mean", "calibration": {"direction_aware_auc": 0.60}},
            {"signal": "adjacent_cosine_sd", "calibration": {"direction_aware_auc": 0.55}},
        ],
    )
    _write_survey(
        survey_dir, phase="B", alias="tinyllama", rows=[
            {"signal": "surprisal_mean", "calibration": {"direction_aware_auc": 0.62}},
            {"signal": "surprisal_sd", "calibration": {"direction_aware_auc": 0.70}},
            {"signal": "surprisal_acf_lag1", "calibration": {"direction_aware_auc": 0.45}},
        ],
    )
    md = bp.summarize_matrix(
        survey_dir,
        phase_a_aliases=["mxbai"],
        phase_b_aliases=["tinyllama"],
        phase_a_signals=["adjacent_cosine_mean", "adjacent_cosine_sd"],
        phase_b_signals=[
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ],
    )
    assert "## Phase A" in md
    assert "## Phase B" in md
    assert "mxbai" in md
    assert "tinyllama" in md
    # Phase A AUCs rendered with their signal strengths.
    assert "0.6000 (0.100)" in md
    # The strongest |signal| in Phase B is sd at 0.70 -> 0.200.
    assert "0.7000 (0.200)" in md


def test_summarize_matrix_picks_winner_with_highest_signal_strength(tmp_path: Path):
    """The 'tentative winners' section names the model with the
    largest |da_AUC - 0.5| in each phase. Operators use this to
    decide which model to advance into the next calibration step."""
    survey_dir = tmp_path / "surveys"
    # Two Phase B models; tinyllama's strongest signal is 0.20,
    # gpt2's is 0.32 -> gpt2 wins.
    _write_survey(survey_dir, phase="B", alias="tinyllama", rows=[
        {"signal": "surprisal_mean", "calibration": {"direction_aware_auc": 0.70}},
        {"signal": "surprisal_sd", "calibration": {"direction_aware_auc": 0.55}},
        {"signal": "surprisal_acf_lag1", "calibration": {"direction_aware_auc": 0.48}},
    ])
    _write_survey(survey_dir, phase="B", alias="gpt2", rows=[
        {"signal": "surprisal_mean", "calibration": {"direction_aware_auc": 0.60}},
        {"signal": "surprisal_sd", "calibration": {"direction_aware_auc": 0.82}},
        {"signal": "surprisal_acf_lag1", "calibration": {"direction_aware_auc": 0.50}},
    ])
    md = bp.summarize_matrix(
        survey_dir,
        phase_a_aliases=[],
        phase_b_aliases=["tinyllama", "gpt2"],
        phase_a_signals=[],
        phase_b_signals=[
            "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
        ],
    )
    assert "Phase B winner: **gpt2**" in md
    assert "0.3200" in md  # |0.82 - 0.5| = 0.32 max for gpt2


def test_summarize_matrix_handles_missing_survey_with_double_dash(tmp_path: Path):
    """A model with no survey JSON renders as ``--`` cells, not as
    a crash. Operators who restart partway through the matrix see
    which cells remain to run."""
    survey_dir = tmp_path / "surveys"
    md = bp.summarize_matrix(
        survey_dir,
        phase_a_aliases=["mxbai"],
        phase_b_aliases=[],
        phase_a_signals=["adjacent_cosine_mean"],
        phase_b_signals=[],
    )
    # The row exists, cell content is "--".
    assert "| mxbai | -- |" in md
    assert "Phase A winner: (no surveys completed)" in md


def test_summarize_matrix_handles_inverted_polarity_error_string(tmp_path: Path):
    """Some calibration_survey rows surface the direction-aware
    AUC in their error string (when the underlying signal was
    direction-inverted vs. the registry — the AUC is computed
    but the calibration step refuses to emit a threshold). The
    extractor must still pick the AUC up from the error string
    so the summary doesn't lose those cells."""
    survey_dir = tmp_path / "surveys"
    _write_survey(survey_dir, phase="B", alias="tinyllama", rows=[
        {
            "signal": "surprisal_mean",
            "error": (
                "polarity inverted: direction_aware_auc = 0.3450 "
                "(< 0.5 under registry direction 'gt')"
            ),
        },
    ])
    md = bp.summarize_matrix(
        survey_dir,
        phase_a_aliases=[],
        phase_b_aliases=["tinyllama"],
        phase_a_signals=[],
        phase_b_signals=["surprisal_mean"],
    )
    # 0.345 should be recovered from the error string.
    assert "0.3450" in md
    # |0.345 - 0.5| = 0.155
    assert "0.155" in md


def test_summarize_matrix_handles_corrupt_survey_with_err_label(tmp_path: Path):
    """A survey file that exists but won't parse renders as ``err``
    (distinguishable from ``--`` for a missing file). Pins the
    operator-debugging value of the summary output."""
    survey_dir = tmp_path / "surveys"
    survey_dir.mkdir()
    (survey_dir / "survey_phaseA_mxbai.json").write_text("{not-json")
    md = bp.summarize_matrix(
        survey_dir,
        phase_a_aliases=["mxbai"],
        phase_b_aliases=[],
        phase_a_signals=["adjacent_cosine_mean"],
        phase_b_signals=[],
    )
    assert "| mxbai | err |" in md


# ============================================================
# CLI subcommands
# ============================================================


def _module_path() -> Path:
    return _HERE.parent / "calibration" / "_bakeoff_provenance.py"


def test_cli_check_done_exits_zero_on_complete_survey(tmp_path: Path):
    """The shell driver calls ``python3 _bakeoff_provenance.py
    check-done <survey.json>``; exit 0 means skip the cell."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps({"rows": [{"signal": "x"}]}))
    rc = subprocess.run(
        [sys.executable, str(_module_path()), "check-done", str(p)],
        capture_output=True,
    ).returncode
    assert rc == 0


def test_cli_check_done_exits_nonzero_on_incomplete_survey(tmp_path: Path):
    """Exit 1 means re-run the cell. The bash driver branches on
    this exact return code."""
    p = tmp_path / "survey.json"
    p.write_text(json.dumps({"rows": []}))
    rc = subprocess.run(
        [sys.executable, str(_module_path()), "check-done", str(p)],
        capture_output=True,
    ).returncode
    assert rc == 1


def test_cli_check_done_exits_two_on_missing_argument():
    """Missing path argument → usage error (rc=2). Bash driver
    should never reach this; pins the helper's own argument
    contract."""
    rc = subprocess.run(
        [sys.executable, str(_module_path()), "check-done"],
        capture_output=True,
    ).returncode
    assert rc == 2


def test_cli_unknown_subcommand_exits_two():
    """Unknown subcommand → usage error so a typo in the bash
    driver surfaces fast rather than producing a silent no-op."""
    rc = subprocess.run(
        [sys.executable, str(_module_path()), "no-such-subcommand"],
        capture_output=True,
    ).returncode
    assert rc == 2


def test_cli_write_provenance_writes_a_valid_json(tmp_path: Path):
    """Subcommand contract: read args.json, write provenance.json.
    Round-trips the args without requiring the bash driver to
    inline any Python."""
    args = {
        "session_id": "TEST",
        "corpus_label": "mage",
        "manifest_path": str(tmp_path / "manifest.jsonl"),
        "phase_a_aliases": ["mxbai"],
        "phase_b_aliases": ["tinyllama"],
        "phase_a_signals": ["adjacent_cosine_mean"],
        "phase_b_signals": ["surprisal_mean"],
        "phase_a_paths": {"mxbai": "mxbai/repo"},
        "phase_b_paths": {"tinyllama": "tinyllama/repo"},
        "max_entries": 5000,
        "bootstrap_engine": "torch",
        "bootstrap_resamples": 2000,
        "fpr_target": 0.01,
        "cooldown_sec": 10,
        "survey_dir": str(tmp_path / "out"),
        "repo_root": str(tmp_path),
    }
    args_file = tmp_path / "args.json"
    args_file.write_text(json.dumps(args))
    out = tmp_path / "prov.json"
    rc = subprocess.run(
        [sys.executable, str(_module_path()),
         "write-provenance", str(args_file), str(out)],
        capture_output=True,
    ).returncode
    assert rc == 0
    parsed = json.loads(out.read_text())
    assert parsed["session_id"] == "TEST"
    assert parsed["corpus_label"] == "mage"


def test_cli_summarize_writes_markdown(tmp_path: Path):
    """The summarize subcommand reads the aliases+signals from
    args.json and writes a markdown summary at the target path.
    Same pattern as write-provenance."""
    survey_dir = tmp_path / "surveys"
    _write_survey(survey_dir, phase="A", alias="mxbai", rows=[
        {"signal": "adjacent_cosine_mean", "calibration": {"direction_aware_auc": 0.6}},
    ])
    args = {
        "phase_a_aliases": ["mxbai"],
        "phase_b_aliases": [],
        "phase_a_signals": ["adjacent_cosine_mean"],
        "phase_b_signals": [],
    }
    args_file = tmp_path / "args.json"
    args_file.write_text(json.dumps(args))
    out = tmp_path / "summary.md"
    rc = subprocess.run(
        [sys.executable, str(_module_path()),
         "summarize", str(survey_dir), str(args_file), str(out)],
        capture_output=True,
    ).returncode
    assert rc == 0
    text = out.read_text()
    assert "## Phase A" in text
    assert "mxbai" in text
