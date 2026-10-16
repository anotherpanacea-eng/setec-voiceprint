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


