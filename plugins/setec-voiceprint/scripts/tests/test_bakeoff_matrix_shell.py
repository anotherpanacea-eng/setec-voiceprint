"""End-to-end smoke tests for the cloud bake-off matrix shell
driver (``calibration/bakeoff_matrix.sh``).

These exercise the script as operators will: set env vars,
invoke bash, check exit codes and the artifacts the script
emits. The matrix's per-cell calibration invocations are NOT
exercised (those would require torch + model downloads); the
tests stay in dry-run mode where the script writes its
provenance + matrix plan and exits.

The provenance helper's logic is unit-tested separately in
``test_bakeoff_provenance.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
SCRIPT = _HERE.parent / "calibration" / "bakeoff_matrix.sh"


def _bash_available() -> bool:
    return shutil.which("bash") is not None


_skip_no_bash = pytest.mark.skipif(
    not _bash_available(),
    reason="bash not on PATH (slim CI harness without /bin/bash)",
)


def _run_script(
    env_overrides: dict[str, str] | None = None,
    *,
    timeout_s: float = 30.0,
) -> subprocess.CompletedProcess:
    """Invoke the matrix script with a clean env plus the overrides
    a test wants to set. Returns the completed process so callers
    can inspect rc + stderr + stdout."""
    env = {
        # Minimum env for a child bash to find its own utilities.
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


@_skip_no_bash
def test_script_exists_and_is_executable():
    """Sanity: the matrix script is in the repo and has the +x
    bit. Operators ``bash bakeoff_matrix.sh`` directly without
    a chmod step on a fresh checkout."""
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), (
        "bakeoff_matrix.sh must be executable; chmod +x and re-commit"
    )


@_skip_no_bash
def test_script_parses_cleanly():
    """``bash -n`` is the cheapest way to catch syntax regressions
    before any test runs the script. Pins that the heredoc + here
    string + ``set -uo pipefail`` interactions don't break under
    future edits."""
    rc = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
    ).returncode
    assert rc == 0


@_skip_no_bash
def test_script_fails_fast_when_required_env_var_is_missing():
    """The first ``: "${SETEC_CORPUS_DIR:?...}"`` guard fires
    when an operator forgets a required var. Pins the fail-fast
    contract: rc != 0 and the offending var named in stderr."""
    cp = _run_script()
    assert cp.returncode != 0
    # Bash's :? error mentions the offending variable. We don't
    # pin the exact message text (bash version variation) but the
    # variable name must surface in stderr.
    assert "SETEC_CORPUS_DIR" in cp.stderr


@_skip_no_bash
def test_script_fails_when_manifest_does_not_exist(tmp_path: Path):
    """All three required dirs are set, but the corpus dir has no
    ``manifest.jsonl``. The script dies with a clear message
    naming the missing file — operators with a misconfigured
    SETEC_CORPUS_DIR see the cause immediately rather than
    hitting a Python error five seconds in."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_DRY_RUN": "1",
    })
    assert cp.returncode != 0
    # The die() helper writes its message to stderr.
    assert "manifest.jsonl" in cp.stderr


@_skip_no_bash
def test_dry_run_emits_provenance_and_exits_cleanly(tmp_path: Path):
    """The full dry-run path. SETEC_DRY_RUN=1 means the script
    validates env, parses rosters, writes the provenance, prints
    the matrix plan, and exits without invoking
    calibration_survey.py. This is the operator's ``what would
    this do?`` check before launching an overnight run."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    log_dir = tmp_path / "log"
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(log_dir),
        "SETEC_CORPUS_LABEL": "smoke",
        "SETEC_DRY_RUN": "1",
    })
    assert cp.returncode == 0, (
        f"dry run failed:\nstderr:\n{cp.stderr}\nstdout:\n{cp.stdout}"
    )
    # Exactly one provenance.json should land in the log dir.
    prov_files = list(log_dir.glob("bakeoff_matrix_*_provenance.json"))
    assert len(prov_files) == 1, (
        f"expected one provenance.json, got {prov_files}"
    )
    prov = json.loads(prov_files[0].read_text())
    assert prov["corpus_label"] == "smoke"
    assert "phases" in prov
    # Default Phase A roster has the four embedding aliases.
    assert set(prov["phases"]["A"]["aliases"]) >= {
        "mxbai", "minilm", "harrier", "gemma",
    }
    # Default Phase B roster has the seven surprisal aliases.
    assert set(prov["phases"]["B"]["aliases"]) >= {
        "gpt2", "tinyllama", "llama32_1b",
        "olmo2_1b", "qwen25_1_5b", "qwen3_1_7b",
        "smollm2_1_7b",
    }
    # Default calibration args.
    assert prov["calibration_args"]["bootstrap_engine"] == "torch"
    assert prov["calibration_args"]["bootstrap_resamples"] == 2000
    assert prov["calibration_args"]["fpr_target"] == 0.01
    assert prov["calibration_args"]["max_entries"] is None
    assert prov["cooldown_sec"] == 10


@_skip_no_bash
def test_dry_run_honors_roster_overrides(tmp_path: Path):
    """``SETEC_PHASE_{A,B}_PATHS`` override the baked-in rosters.
    Operators with a custom model set (e.g., a 7B reference
    surprisal scorer) supply JSON via env. The override must
    fully replace the default, not merge."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    log_dir = tmp_path / "log"
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(log_dir),
        "SETEC_DRY_RUN": "1",
        "SETEC_PHASE_A_PATHS": json.dumps({"my_embedder": "org/my-embedder"}),
        "SETEC_PHASE_B_PATHS": json.dumps({"my_surprisal": "org/my-surprisal"}),
    })
    assert cp.returncode == 0, (
        f"dry run failed:\nstderr:\n{cp.stderr}\nstdout:\n{cp.stdout}"
    )
    prov = json.loads(
        next(log_dir.glob("bakeoff_matrix_*_provenance.json")).read_text()
    )
    assert prov["phases"]["A"]["aliases"] == ["my_embedder"]
    assert prov["phases"]["B"]["aliases"] == ["my_surprisal"]
    # The original defaults are NOT merged in.
    assert "mxbai" not in prov["phases"]["A"]["aliases"]
    assert "gpt2" not in prov["phases"]["B"]["aliases"]


@_skip_no_bash
def test_dry_run_honors_max_entries_override(tmp_path: Path):
    """``SETEC_MAX_ENTRIES`` is recorded in provenance as an int.
    Empty / unset means full-corpus (max_entries=None). Pins the
    two cases since they branch differently in the bash driver's
    BASE_ARGS assembly."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    log_dir = tmp_path / "log"
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(log_dir),
        "SETEC_DRY_RUN": "1",
        "SETEC_MAX_ENTRIES": "25000",
    })
    assert cp.returncode == 0
    prov = json.loads(
        next(log_dir.glob("bakeoff_matrix_*_provenance.json")).read_text()
    )
    assert prov["calibration_args"]["max_entries"] == 25000


@_skip_no_bash
def test_dry_run_reset_sentinels_removes_pre_existing_surveys(tmp_path: Path):
    """``SETEC_RESET_SENTINELS=1`` clears any pre-existing
    survey JSONs before running. Operators use this to
    re-evaluate models that were skip-sentineled on a prior host
    (the laptop's gpt2/olmo2 sentinels from the 2026-05-18
    crash diagnostic). Without the var, an existing survey JSON
    causes the cell to skip — desired for crash recovery but
    wrong when porting from a different host."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    bake_dir = tmp_path / "bake"
    bake_dir.mkdir()
    stale = bake_dir / "survey_phaseB_gpt2.json"
    stale.write_text(json.dumps({
        "rows": [{"error": "host bounced before scoring; rerun on cloud"}],
        "meta": {"sentinel": True},
    }))
    log_dir = tmp_path / "log"
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(bake_dir),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(log_dir),
        "SETEC_DRY_RUN": "1",
        "SETEC_RESET_SENTINELS": "1",
    })
    assert cp.returncode == 0
    assert not stale.exists(), (
        "SETEC_RESET_SENTINELS=1 should delete pre-existing survey JSONs"
    )


@_skip_no_bash
def test_dry_run_prints_matrix_plan(tmp_path: Path):
    """The dry-run path prints the model x signal plan to stdout so
    operators can sanity-check what would run before launching
    overnight."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_DRY_RUN": "1",
    })
    assert cp.returncode == 0
    # The banner names the corpus + survey dir.
    assert "Cloud bake-off matrix" in cp.stdout
    # The plan section is present.
    assert "Phase A cells:" in cp.stdout
    assert "Phase B cells:" in cp.stdout
    # The signals show up beside each model.
    assert "adjacent_cosine_mean" in cp.stdout
    assert "surprisal_acf_lag1" in cp.stdout


@_skip_no_bash
def test_default_roster_uses_framework_aliases_not_raw_hf_ids(tmp_path: Path):
    """Reviewer P1 on PR #100: the original default Phase B roster
    baked in raw HF ids (TinyLlama-1.1B-Chat-v1.0 instead of the
    canonical intermediate id, Qwen3-1.7B without the ``-Base``
    suffix) that diverged from ``surprisal_backend.MODEL_ALIASES``.
    The fix puts framework alias strings in the default JSON so
    the canonical alias tables stay the single source of truth.
    Pin both rosters against the alias-table values."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    log_dir = tmp_path / "log"
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(log_dir),
        "SETEC_DRY_RUN": "1",
    })
    assert cp.returncode == 0
    prov = json.loads(
        next(log_dir.glob("bakeoff_matrix_*_provenance.json")).read_text()
    )
    # Every Phase B value should be the alias itself (not a raw
    # HF id). The framework's calibration_survey resolves the
    # alias against the canonical table at run time.
    for alias, value in prov["phases"]["B"]["paths"].items():
        assert "/" not in value, (
            f"Phase B alias {alias!r} maps to raw HF id {value!r}; "
            f"should pass through the alias string itself so "
            f"surprisal_backend.MODEL_ALIASES resolves it canonically."
        )
        assert value == alias, (
            f"Phase B alias {alias!r} maps to {value!r}; expected "
            f"the alias string itself"
        )
    # Same contract on Phase A.
    for alias, value in prov["phases"]["A"]["paths"].items():
        assert "/" not in value, (
            f"Phase A alias {alias!r} maps to raw HF id {value!r}"
        )
        assert value == alias


@_skip_no_bash
def test_no_failed_cells_means_exit_zero(tmp_path: Path):
    """Sanity: dry-run completes with no cells run, so
    ``FAILED_CELLS`` stays empty and exit is 0. This is the
    happy-path baseline for the failure-tracking contract."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_DRY_RUN": "1",
    })
    assert cp.returncode == 0
    # The failure-block stderr/stdout messages don't appear in
    # dry-run because nothing ran.
    assert "WARNING:" not in cp.stdout
    assert "failed:" not in cp.stdout


@_skip_no_bash
def test_failed_cells_exit_nonzero_unless_allow_partial(tmp_path: Path):
    """Reviewer P1 on PR #100: failed cells were silently masked
    because per-cell return codes were ignored in the run loops.
    A 5-of-7-failed Phase B run produced rc=0 and printed
    'Matrix run complete', which is a silent false-success path
    for an operator watching only the exit code.

    Inject a Phase A cell pointing at a nonsense alias; the real
    ``calibration_survey.py`` fails at model-load. Phase B is
    empty so the test doesn't depend on heavy deps. The exit code
    must propagate the failure."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text(
        '{"path":"/dev/null","ai_status":0}\n'
    )
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_PHASE_A_PATHS": '{"nonsense_alias_that_wont_load": '
                               '"definitely-not-a-real-model"}',
        "SETEC_PHASE_B_PATHS": '{}',
        "SETEC_COOLDOWN_SEC": "0",
    }, timeout_s=60.0)
    assert cp.returncode != 0, (
        f"expected non-zero exit on cell failure; got rc={cp.returncode}\n"
        f"stdout:\n{cp.stdout[-800:]}"
    )
    assert "failed:" in cp.stdout or "WARNING" in cp.stdout
    assert "nonsense_alias_that_wont_load" in cp.stdout


@_skip_no_bash
def test_allow_partial_lets_failed_cells_succeed_overall(tmp_path: Path):
    """``SETEC_ALLOW_PARTIAL=1`` opts back into exit 0 even with
    failed cells. Use case: known-flaky model in a long overnight
    run where the operator wants the partial data anyway. The
    failure list still surfaces in stdout so the operator can see
    which cells didn't complete."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text(
        '{"path":"/dev/null","ai_status":0}\n'
    )
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_PHASE_A_PATHS": '{"nonsense_alias_that_wont_load": '
                               '"definitely-not-a-real-model"}',
        "SETEC_PHASE_B_PATHS": '{}',
        "SETEC_COOLDOWN_SEC": "0",
        "SETEC_ALLOW_PARTIAL": "1",
    }, timeout_s=60.0)
    assert cp.returncode == 0, (
        f"SETEC_ALLOW_PARTIAL=1 should override failure exit; "
        f"got rc={cp.returncode}\nstdout:\n{cp.stdout[-800:]}"
    )
    assert "nonsense_alias_that_wont_load" in cp.stdout
    assert "treating partial completion as success" in cp.stdout


@_skip_no_bash
def test_empty_phase_a_does_not_trip_nounset(tmp_path: Path):
    """Reviewer P1 follow-up: an empty Phase A roster
    (``SETEC_PHASE_A_PATHS='{}'``) must not abort the script via
    nounset before the failure-summary or Phase B loop runs. Pins
    the symmetric case to the empty-Phase-B regression tests above
    -- one-phase bake-offs (operator wants only embedding OR only
    surprisal cells) shouldn't pay a portability cost."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "manifest.jsonl").write_text("{}\n")
    cp = _run_script({
        "SETEC_CORPUS_DIR": str(corpus),
        "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
        "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
        "SETEC_LOG_DIR": str(tmp_path / "log"),
        "SETEC_PHASE_A_PATHS": '{}',
        "SETEC_PHASE_B_PATHS": '{}',  # both empty
        "SETEC_DRY_RUN": "1",
    })
    # With both rosters empty + dry-run, the script should complete
    # cleanly (banner + plan + provenance write) and exit 0.
    assert cp.returncode == 0, (
        f"empty rosters tripped script; rc={cp.returncode}\n"
        f"stdout:\n{cp.stdout[-800:]}\n\nstderr:\n{cp.stderr[-400:]}"
    )
    # Banner survives with the (none) sentinel for both phases.
    assert "phase_a_aliases: (none)" in cp.stdout
    assert "phase_b_aliases: (none)" in cp.stdout
    # Plan section renders the headers even with zero cells.
    assert "Phase A cells:" in cp.stdout
    assert "Phase B cells:" in cp.stdout
