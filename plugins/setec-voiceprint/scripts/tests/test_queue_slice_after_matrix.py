"""End-to-end smoke tests for the queue-slice-after-matrix shell
driver (``calibration/queue_slice_after_matrix.sh``).

The driver polls a watch dir for ``survey_*.json`` files written by
``bakeoff_matrix.sh`` and, for each new one, runs ``slice_bakeoff_v2.py``
and ``polarity_audit.py`` against the surrounding cache dir. Markers
(``<survey>.sliced`` + ``<survey>.polarity``) make re-runs idempotent.

These tests exercise the script the way operators will: set env vars,
invoke bash with ``--once``, check exit codes, watch for marker files
and stub-binary side effects. The real slice / polarity scripts are
replaced with tiny Python stubs (via ``SETEC_SLICER_BIN`` /
``SETEC_POLARITY_BIN``) so the tests don't need torch / a manifest /
real cache files. The stubs write deterministic outputs the assertions
can read back.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = CALIB_DIR / "queue_slice_after_matrix.sh"


def _bash_available() -> bool:
    return shutil.which("bash") is not None


_skip_no_bash = pytest.mark.skipif(
    not _bash_available(),
    reason="bash not on PATH (slim CI harness without /bin/bash)",
)

# Stub binary template. Receives args via sys.argv, writes a small JSON
# payload to the path passed via STUB_OUTPUT_PATH so each test can
# verify the stub ran and what it received. STUB_EXIT_CODE controls
# the simulated rc so error-path tests can flip behavior without
# editing the stub source.
_STUB_TEMPLATE = """#!{python}
import json
import os
import sys
out = os.environ.get("STUB_OUTPUT_PATH")
rc = int(os.environ.get("STUB_EXIT_CODE", "0"))
extra = os.environ.get("STUB_EXTRA_PATH")
if out:
    # Append-mode so multiple stub invocations (slicer + polarity) in
    # one driver pass don't clobber each other. Each line is one
    # invocation record.
    with open(out, "a") as f:
        f.write(json.dumps({{"name": {name!r}, "argv": sys.argv[1:]}}) + "\\n")
if extra:
    # Simulate the slicer writing slice_analysis.csv into --out-dir so
    # the polarity step can find it. Look for --out-dir in argv.
    if "--out-dir" in sys.argv:
        i = sys.argv.index("--out-dir")
        out_dir = sys.argv[i + 1]
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, extra), "w") as f:
            f.write("corpus,model,signal,slice_key,slice_value,n_pos,n_neg,auc,da_auc,abs_signal\\n")
sys.exit(rc)
"""


def _write_stub(path: Path, name: str) -> Path:
    """Write a stub script that records its argv and exits 0 by default."""
    path.write_text(_STUB_TEMPLATE.format(python=sys.executable, name=name))
    path.chmod(0o755)
    return path


def _run_script(
    env_overrides: dict[str, str] | None = None,
    *,
    args: list[str] | None = None,
    timeout_s: float = 30.0,
) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if env_overrides:
        env.update(env_overrides)
    cmd = ["bash", str(SCRIPT)]
    if args:
        cmd.extend(args)
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _make_survey(dir_: Path, name: str) -> Path:
    """Write a minimally-shaped survey JSON file in ``dir_``."""
    p = dir_ / name
    p.write_text(json.dumps({"meta": {"alias": name}, "rows": []}))
    return p


@pytest.fixture
def stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (slicer_bin, polarity_bin, invocation_log)."""
    slicer = _write_stub(tmp_path / "fake_slicer.py", "slicer")
    polarity = _write_stub(tmp_path / "fake_polarity.py", "polarity")
    invocations = tmp_path / "invocations.jsonl"
    return slicer, polarity, invocations


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    """A minimal manifest file (existence-checked by the script)."""
    m = tmp_path / "manifest.jsonl"
    m.write_text("{}\n")
    return m


def _base_env(
    *,
    watch_dir: Path,
    manifest: Path,
    slicer: Path,
    polarity: Path,
    invocations: Path,
    slice_out_dir: Path,
    corpus: str = "mage",
    polarity_out_json: Path | None = None,
) -> dict[str, str]:
    env = {
        "SETEC_BAKEOFF_DIR": str(watch_dir),
        "SETEC_MANIFEST": str(manifest),
        "SETEC_CORPUS_LABEL": corpus,
        "SETEC_SLICE_OUT_DIR": str(slice_out_dir),
        "SETEC_SLICER_BIN": str(slicer),
        "SETEC_POLARITY_BIN": str(polarity),
        # Stub-internal channels.
        "STUB_OUTPUT_PATH": str(invocations),
        # Slicer stub writes slice_analysis.csv into its --out-dir so the
        # polarity stub's required input is present.
        "STUB_EXTRA_PATH": "slice_analysis.csv",
    }
    if polarity_out_json is not None:
        env["SETEC_POLARITY_OUT_JSON"] = str(polarity_out_json)
    return env


# ----------------------------------------------------------------- Tests


@_skip_no_bash
def test_script_exists_and_is_executable():
    """Sanity: the queue script is in the repo and has the +x bit."""
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), (
        "queue_slice_after_matrix.sh must be executable; chmod +x and re-commit"
    )


@_skip_no_bash
def test_script_parses_cleanly():
    """``bash -n`` catches syntax regressions cheaply."""
    rc = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
    ).returncode
    assert rc == 0


@_skip_no_bash
def test_fails_when_required_env_missing(tmp_path: Path):
    """SETEC_MANIFEST + SETEC_CORPUS_LABEL are required; absent them
    the script must fail fast and name the offending variable."""
    watch = tmp_path / "bake"
    watch.mkdir()
    cp = _run_script({
        "SETEC_BAKEOFF_DIR": str(watch),
    }, args=["--once"])
    assert cp.returncode != 0
    assert "SETEC_MANIFEST" in cp.stderr or "SETEC_CORPUS_LABEL" in cp.stderr


@_skip_no_bash
def test_fails_when_watch_dir_missing(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """A nonexistent watch dir is a configuration error, not a
    transient empty state. The script dies with a clear message."""
    slicer, polarity, invocations = stubs
    env = _base_env(
        watch_dir=tmp_path / "does-not-exist",
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "out",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 2
    assert "watch dir" in cp.stderr


@_skip_no_bash
def test_once_picks_up_new_surveys(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """The core happy path. Three survey JSONs sit in the watch dir;
    --once should run slicer + polarity_audit exactly once each, then
    write a .sliced + .polarity marker beside every survey."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    surveys = [
        _make_survey(watch, "survey_phaseA_mxbai.json"),
        _make_survey(watch, "survey_phaseA_gemma.json"),
        _make_survey(watch, "survey_phaseB_gpt2.json"),
    ]
    slice_out = tmp_path / "slice_out"
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=slice_out,
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0, (
        f"--once failed:\nstderr:\n{cp.stderr}\nstdout:\n{cp.stdout}"
    )
    # Found-N log line surfaces.
    assert "Found 3 new survey" in cp.stdout
    # Slicer + polarity each invoked exactly once for the whole batch.
    lines = [
        json.loads(line) for line in invocations.read_text().splitlines() if line
    ]
    assert sum(1 for r in lines if r["name"] == "slicer") == 1, lines
    assert sum(1 for r in lines if r["name"] == "polarity") == 1, lines
    # Every survey now has both markers.
    for s in surveys:
        assert (s.with_name(s.name + ".sliced")).exists()
        assert (s.with_name(s.name + ".polarity")).exists()
    # Slicer call carried the expected flags.
    slicer_call = next(r for r in lines if r["name"] == "slicer")
    argv = slicer_call["argv"]
    assert "--corpus" in argv and argv[argv.index("--corpus") + 1] == "mage"
    assert "--cache-dir" in argv and argv[argv.index("--cache-dir") + 1] == str(watch)
    assert "--manifest" in argv and argv[argv.index("--manifest") + 1] == str(manifest)
    assert "--out-dir" in argv and argv[argv.index("--out-dir") + 1] == str(slice_out)
    # Default audit mode is 'polarity'.
    assert "--audit" in argv and argv[argv.index("--audit") + 1] == "polarity"
    # MAGE corpus defaults to notes.original_source + comparator_class=mage.
    assert argv[argv.index("--comparator-key") + 1] == "notes.original_source"
    assert argv[argv.index("--comparator-class") + 1] == "mage"


@_skip_no_bash
def test_already_processed_surveys_are_skipped(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Surveys with both .sliced and .polarity markers are not
    re-processed. With ALL surveys pre-marked, the slicer + polarity
    stubs should never run."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    for name in ("survey_phaseA_mxbai.json", "survey_phaseB_gpt2.json"):
        s = _make_survey(watch, name)
        (s.with_name(s.name + ".sliced")).touch()
        (s.with_name(s.name + ".polarity")).touch()
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    # Stub log should be absent or empty -- neither tool fired.
    assert not invocations.exists() or invocations.read_text().strip() == ""
    # Found-N log line should NOT appear because zero surveys were new.
    assert "Found" not in cp.stdout


@_skip_no_bash
def test_mixed_new_and_old_surveys(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """One survey pre-marked, two new. Only the two new ones trigger
    processing, and only they get fresh markers from this run. The
    pre-marked one keeps its existing markers untouched."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    old = _make_survey(watch, "survey_phaseA_minilm.json")
    (old.with_name(old.name + ".sliced")).write_text("from-prior-run\n")
    (old.with_name(old.name + ".polarity")).write_text("from-prior-run\n")
    new_a = _make_survey(watch, "survey_phaseA_mxbai.json")
    new_b = _make_survey(watch, "survey_phaseB_gpt2.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    assert "Found 2 new survey" in cp.stdout
    # New surveys got fresh markers.
    assert (new_a.with_name(new_a.name + ".sliced")).exists()
    assert (new_a.with_name(new_a.name + ".polarity")).exists()
    assert (new_b.with_name(new_b.name + ".sliced")).exists()
    assert (new_b.with_name(new_b.name + ".polarity")).exists()
    # Pre-existing markers are NOT overwritten (operator's marker
    # bookkeeping survives across passes).
    assert (old.with_name(old.name + ".sliced")).read_text() == "from-prior-run\n"
    assert (old.with_name(old.name + ".polarity")).read_text() == "from-prior-run\n"


@_skip_no_bash
def test_no_new_surveys_exits_cleanly(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Empty watch dir under --once: clean rc=0, no stub invocations,
    no marker activity. Pins the no-op pass shape."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    assert not invocations.exists() or invocations.read_text().strip() == ""


@_skip_no_bash
def test_slicer_failure_leaves_markers_absent(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """If the slicer exits non-zero, NO markers should be written and
    the polarity step should not fire. The whole batch retries on the
    next pass. Exit code is still 0 (per-survey errors don't kill the
    loop)."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    surveys = [
        _make_survey(watch, "survey_phaseA_mxbai.json"),
        _make_survey(watch, "survey_phaseB_gpt2.json"),
    ]
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    env["STUB_EXIT_CODE"] = "5"  # slicer (and polarity, but it shouldn't run) both fail
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0, (
        f"per-survey failures should not abort the loop; "
        f"rc={cp.returncode}\nstdout:\n{cp.stdout}\nstderr:\n{cp.stderr}"
    )
    assert "slice_bakeoff_v2 failed" in cp.stdout
    lines = [
        json.loads(line) for line in invocations.read_text().splitlines() if line
    ]
    # Slicer ran once and failed. Polarity NEVER ran (no CSV to feed it).
    assert sum(1 for r in lines if r["name"] == "slicer") == 1
    assert sum(1 for r in lines if r["name"] == "polarity") == 0
    # No markers written -- the surveys will be retried next pass.
    for s in surveys:
        assert not (s.with_name(s.name + ".sliced")).exists()
        assert not (s.with_name(s.name + ".polarity")).exists()


@_skip_no_bash
def test_polarity_failure_keeps_sliced_marker_only(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """If the slicer succeeds but polarity fails, .sliced markers
    are written (the slice pass completed) but .polarity markers are
    NOT (so the audit retries next pass). This is the partial-progress
    contract -- expensive slicer work doesn't get re-done because the
    cheap audit had a transient failure."""
    slicer, polarity, invocations = stubs
    # Replace the polarity stub with one that always exits 1.
    polarity.write_text(
        _STUB_TEMPLATE.format(python=sys.executable, name="polarity").replace(
            'rc = int(os.environ.get("STUB_EXIT_CODE", "0"))',
            "rc = 1",  # polarity always fails
        )
    )
    polarity.chmod(0o755)
    watch = tmp_path / "bake"
    watch.mkdir()
    surveys = [_make_survey(watch, "survey_phaseA_mxbai.json")]
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    assert "polarity_audit failed" in cp.stdout
    for s in surveys:
        assert (s.with_name(s.name + ".sliced")).exists(), (
            ".sliced marker should be present after slicer success"
        )
        assert not (s.with_name(s.name + ".polarity")).exists(), (
            ".polarity marker should be absent after polarity failure"
        )


@_skip_no_bash
def test_once_env_var_equivalent_to_flag(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """SETEC_QUEUE_ONCE=1 is the env-var equivalent of --once. Pins
    that operators who set the env var get the same one-shot behavior
    as the CLI flag (for cron-style invocations where env vars are
    easier to wire than CLI args)."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    _make_survey(watch, "survey_phaseA_mxbai.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    env["SETEC_QUEUE_ONCE"] = "1"
    cp = _run_script(env)  # no --once flag
    assert cp.returncode == 0
    assert "Found 1 new survey" in cp.stdout


@_skip_no_bash
def test_watch_dir_positional_arg_overrides_env(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Positional WATCH_DIR overrides SETEC_BAKEOFF_DIR. Useful when
    an operator runs the queue against a different dir than the
    matrix's normal output (e.g., a recovery dir)."""
    slicer, polarity, invocations = stubs
    env_dir = tmp_path / "env_dir"
    env_dir.mkdir()
    arg_dir = tmp_path / "arg_dir"
    arg_dir.mkdir()
    # Put a survey only in arg_dir so we can tell which dir was watched.
    _make_survey(arg_dir, "survey_phaseA_mxbai.json")
    env = _base_env(
        watch_dir=env_dir,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once", str(arg_dir)])
    assert cp.returncode == 0
    # The arg-dir survey should have been seen + marked.
    assert (arg_dir / "survey_phaseA_mxbai.json.sliced").exists()


@_skip_no_bash
def test_raid_corpus_default_comparator_key(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """RAID corpus defaults the comparator-key to notes.domain and the
    comparator-class to raid (mirrors bakeoff_matrix.sh). Operators
    don't have to pass either explicitly for the two known framework
    corpora."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    _make_survey(watch, "survey_phaseB_gpt2.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
        corpus="raid",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    lines = [
        json.loads(line) for line in invocations.read_text().splitlines() if line
    ]
    slicer_argv = next(r for r in lines if r["name"] == "slicer")["argv"]
    assert slicer_argv[slicer_argv.index("--comparator-key") + 1] == "notes.domain"
    assert slicer_argv[slicer_argv.index("--comparator-class") + 1] == "raid"


@_skip_no_bash
def test_unknown_corpus_omits_comparator_defaults(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """For a corpus label other than mage / raid, both comparator-key
    and comparator-class default to unset. The slicer then runs in
    pre-1.98 mode (every signal uses its SIGNAL_SPECS default).
    Operators with custom taxonomies pass the env vars explicitly to
    opt in."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    _make_survey(watch, "survey_phaseA_mxbai.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
        corpus="custom_corpus",
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    lines = [
        json.loads(line) for line in invocations.read_text().splitlines() if line
    ]
    slicer_argv = next(r for r in lines if r["name"] == "slicer")["argv"]
    assert "--comparator-key" not in slicer_argv
    assert "--comparator-class" not in slicer_argv


@_skip_no_bash
def test_slice_audit_can_be_disabled(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """SETEC_SLICE_AUDIT='' disables the slicer-side polarity audit
    (the slicer is invoked without --audit). Operators who only want
    the standalone polarity verdict (e.g., minimizing slicer runtime
    on a huge cache) can opt out."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    _make_survey(watch, "survey_phaseA_mxbai.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    env["SETEC_SLICE_AUDIT"] = ""
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0
    lines = [
        json.loads(line) for line in invocations.read_text().splitlines() if line
    ]
    slicer_argv = next(r for r in lines if r["name"] == "slicer")["argv"]
    assert "--audit" not in slicer_argv


@_skip_no_bash
def test_unknown_flag_rejected(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Unknown CLI flags fail fast. Pins the small surface (--once /
    --help only) so future additions are an explicit choice rather
    than silently accepted typos."""
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
    )
    cp = _run_script(env, args=["--once", "--nope"])
    assert cp.returncode == 2
    assert "unknown flag" in cp.stderr


@_skip_no_bash
def test_standalone_polarity_audit_forwards_comparator_class(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Standalone polarity audit must receive ``--comparator-class`` so
    its direction registry matches the slicer's integrated ``--audit
    polarity`` output. Without this, the two artifacts can disagree on
    signals routed by comparator (RAID surprisal_sd in particular).

    Pin: ``SETEC_COMPARATOR_CLASS=raid`` should produce ``--comparator-
    class raid`` in the polarity stub's argv.
    """
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    _make_survey(watch, "survey_phaseA_mxbai.json")
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=tmp_path / "slice_out",
        corpus="raid",
    )
    # Slicer stub writes a CSV so polarity has something to read.
    env["STUB_EXTRA_PATH"] = "slice_analysis.csv"
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0, cp.stderr
    invocation_lines = invocations.read_text().splitlines()
    polarity_calls = [
        json.loads(line) for line in invocation_lines
        if json.loads(line)["name"] == "polarity"
    ]
    assert len(polarity_calls) == 1, "polarity should be called once"
    argv = polarity_calls[0]["argv"]
    assert "--comparator-class" in argv
    assert argv[argv.index("--comparator-class") + 1] == "raid", (
        "raid should propagate through; without this the standalone "
        "polarity audit silently falls back to default registry directions"
    )


@_skip_no_bash
def test_sliced_only_survey_skips_slicer_on_retry(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Partial-progress retry: a survey with .sliced present but
    .polarity absent must re-run the polarity audit WITHOUT re-running
    the (expensive) whole-cache slicer pass. The original loop only
    skipped surveys with BOTH markers, so a transient polarity failure
    forced a wasted slicer call on the next pass.
    """
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    survey = _make_survey(watch, "survey_phaseA_mxbai.json")
    # Simulate prior pass: slicer succeeded, polarity failed.
    (watch / (survey.name + ".sliced")).write_text("")
    # Pre-write the CSV so the polarity stub has something to read
    # without the slicer running again this pass.
    slice_out = tmp_path / "slice_out"
    slice_out.mkdir()
    (slice_out / "slice_analysis.csv").write_text(
        "corpus,model,signal,slice_key,slice_value,n_pos,n_neg,auc,da_auc,abs_signal\n"
    )
    env = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,
        invocations=invocations,
        slice_out_dir=slice_out,
    )
    cp = _run_script(env, args=["--once"])
    assert cp.returncode == 0, cp.stderr
    invocation_lines = invocations.read_text().splitlines()
    slicer_calls = [
        line for line in invocation_lines
        if json.loads(line)["name"] == "slicer"
    ]
    polarity_calls = [
        line for line in invocation_lines
        if json.loads(line)["name"] == "polarity"
    ]
    assert len(slicer_calls) == 0, (
        f"slicer must NOT run when every survey already has .sliced; "
        f"got {len(slicer_calls)} invocations"
    )
    assert len(polarity_calls) == 1, "polarity should retry exactly once"
    assert (watch / (survey.name + ".polarity")).exists(), (
        "polarity marker should be written after the retry succeeds"
    )


@_skip_no_bash
def test_two_pass_polarity_failure_then_success_slices_once(
    tmp_path: Path, manifest: Path, stubs: tuple[Path, Path, Path],
):
    """Cumulative regression for the partial-progress contract: across
    two ``--once`` passes where polarity fails on pass 1 and succeeds
    on pass 2, the slicer is called exactly once. The original loop
    would call it twice (once per pass) because the .sliced-only
    survey was re-queued for slicing.
    """
    slicer, polarity, invocations = stubs
    watch = tmp_path / "bake"
    watch.mkdir()
    survey = _make_survey(watch, "survey_phaseA_mxbai.json")
    slice_out = tmp_path / "slice_out"

    # ---- pass 1: polarity fails ----
    failing_polarity = tmp_path / "fake_polarity_fail.py"
    failing_polarity.write_text(
        _STUB_TEMPLATE.format(python=sys.executable, name="polarity").replace(
            'rc = int(os.environ.get("STUB_EXIT_CODE", "0"))',
            "rc = 1",
        )
    )
    failing_polarity.chmod(0o755)
    env1 = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=failing_polarity,
        invocations=invocations,
        slice_out_dir=slice_out,
    )
    env1["STUB_EXTRA_PATH"] = "slice_analysis.csv"
    cp1 = _run_script(env1, args=["--once"])
    assert cp1.returncode == 0
    assert (watch / (survey.name + ".sliced")).exists()
    assert not (watch / (survey.name + ".polarity")).exists()

    # ---- pass 2: polarity succeeds, slicer must NOT be re-called ----
    env2 = _base_env(
        watch_dir=watch,
        manifest=manifest,
        slicer=slicer,
        polarity=polarity,  # the original happy stub (rc=0)
        invocations=invocations,
        slice_out_dir=slice_out,
    )
    cp2 = _run_script(env2, args=["--once"])
    assert cp2.returncode == 0, cp2.stderr
    assert (watch / (survey.name + ".polarity")).exists()

    invocation_lines = invocations.read_text().splitlines()
    slicer_call_count = sum(
        1 for line in invocation_lines
        if json.loads(line)["name"] == "slicer"
    )
    polarity_call_count = sum(
        1 for line in invocation_lines
        if json.loads(line)["name"] == "polarity"
    )
    assert slicer_call_count == 1, (
        f"slicer must be called exactly once across both passes; "
        f"got {slicer_call_count} invocations (expensive whole-cache "
        f"pass duplicated on partial-progress retry)"
    )
    assert polarity_call_count == 2, (
        f"polarity should be called both passes (fail then succeed); "
        f"got {polarity_call_count}"
    )
