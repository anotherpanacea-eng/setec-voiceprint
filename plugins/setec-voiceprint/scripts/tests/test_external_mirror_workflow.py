"""Tests for ``external_mirror/workflow.py``.

Pin the harness contract: prepare lays out the right directory shape
and invokes Phase A with the expected args; status reports the right
counts; score chains the three Phase B scripts in order. No real
Phase A or Phase B execution — subprocess invocations are stubbed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_HERE = Path(__file__).resolve().parent
_EXTERNAL_MIRROR = _HERE.parent / "external_mirror"
sys.path.insert(0, str(_EXTERNAL_MIRROR))

import workflow  # noqa: E402


# ============================================================
# Stub runner
# ============================================================


class RecordingRunner:
    """Captures every subprocess invocation; returns a configurable
    CompletedProcess. Lets tests assert on what the harness invoked."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


# ============================================================
# prepare
# ============================================================


def _write_target(tmp_path: Path, text: str = "lorem ipsum " * 1000) -> Path:
    p = tmp_path / "target.txt"
    p.write_text(text)
    return p


def test_prepare_creates_expected_layout(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    result = workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude", "chatgpt"],
        run_id="t1",
        runner=runner,
    )
    assert result.run_dir == tmp_path / "runs" / "t1"
    assert (result.run_dir / "target.txt").exists()
    assert (result.run_dir / "prompts").is_dir()
    assert (result.run_dir / "outputs").is_dir()
    assert (result.run_dir / "outputs" / "claude").is_dir()
    assert (result.run_dir / "outputs" / "chatgpt").is_dir()
    assert (result.run_dir / "WORKFLOW.md").exists()


def test_prepare_invokes_build_prompts_with_expected_args(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude"],
        run_id="t1",
        windows=4,
        context=500,
        continuation=150,
        positioning="equal_skipping_opening",
        genre_descriptor="literary prose",
        runner=runner,
    )
    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert sys.executable == cmd[0]
    assert "build_prompts.py" in cmd[1]
    assert "--windows" in cmd and cmd[cmd.index("--windows") + 1] == "4"
    assert "--context" in cmd and cmd[cmd.index("--context") + 1] == "500"
    assert "--continuation" in cmd and cmd[cmd.index("--continuation") + 1] == "150"
    assert "--positioning" in cmd and cmd[cmd.index("--positioning") + 1] == "equal_skipping_opening"
    assert "--genre-descriptor" in cmd and cmd[cmd.index("--genre-descriptor") + 1] == "literary prose"
    assert "--run-id" in cmd and cmd[cmd.index("--run-id") + 1] == "t1"
    assert "--format" in cmd and cmd[cmd.index("--format") + 1] == "both"


def test_prepare_forwards_context_grid_for_expanding(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude"],
        run_id="t1",
        positioning="expanding",
        context_grid="500,1000,1500,2000",
        runner=runner,
    )
    cmd = runner.calls[0]
    assert "--context-grid" in cmd
    assert cmd[cmd.index("--context-grid") + 1] == "500,1000,1500,2000"


def test_prepare_forwards_positions_for_custom(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude"],
        run_id="t1",
        positioning="custom",
        positions="500,1500,2500",
        runner=runner,
    )
    cmd = runner.calls[0]
    assert "--positions" in cmd
    assert cmd[cmd.index("--positions") + 1] == "500,1500,2500"


def test_prepare_workflow_md_includes_key_fields(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    result = workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude", "chatgpt", "human_control"],
        run_id="t1",
        windows=4,
        genre_descriptor="literary fiction",
        runner=runner,
    )
    md = (result.run_dir / "WORKFLOW.md").read_text()
    assert "t1" in md
    assert "claude" in md
    assert "chatgpt" in md
    assert "human_control" in md
    assert "literary fiction" in md
    assert "window_1.md" in md
    assert "window_4.md" in md
    assert "windows_batched.md" in md
    assert "target_continuation.json" in md
    assert "workflow.py score" in md


def test_prepare_errors_on_missing_target(tmp_path):
    with pytest.raises(FileNotFoundError):
        workflow.prepare(
            target_path=tmp_path / "nonexistent.txt",
            runs_root=tmp_path / "runs",
            families=["claude"],
            run_id="t1",
            runner=RecordingRunner(),
        )


def test_prepare_errors_on_empty_families(tmp_path):
    target = _write_target(tmp_path)
    with pytest.raises(ValueError, match="families list cannot be empty"):
        workflow.prepare(
            target_path=target,
            runs_root=tmp_path / "runs",
            families=[],
            run_id="t1",
            runner=RecordingRunner(),
        )


def test_prepare_errors_on_existing_run_id(tmp_path):
    target = _write_target(tmp_path)
    runner = RecordingRunner()
    workflow.prepare(
        target_path=target,
        runs_root=tmp_path / "runs",
        families=["claude"],
        run_id="dup",
        runner=runner,
    )
    with pytest.raises(FileExistsError):
        workflow.prepare(
            target_path=target,
            runs_root=tmp_path / "runs",
            families=["claude"],
            run_id="dup",
            runner=runner,
        )


# ============================================================
# status
# ============================================================


def _make_run_dir(tmp_path: Path, *, windows_count: int = 4, families: list[str] = ["claude"]) -> Path:
    run_dir = tmp_path / "runs" / "t1"
    run_dir.mkdir(parents=True)
    phase_a = run_dir / "prompts" / "t1"
    phase_a.mkdir(parents=True)
    manifest = {
        "run_id": "t1",
        "windows_count": windows_count,
        "target_sha256": "abc",
    }
    (phase_a / "MANIFEST.json").write_text(json.dumps(manifest))
    outputs_root = run_dir / "outputs"
    outputs_root.mkdir()
    for fam in families:
        (outputs_root / fam).mkdir()
    return run_dir


def test_status_reports_phase_a_run(tmp_path):
    run_dir = _make_run_dir(tmp_path, windows_count=4)
    s = workflow.status(run_dir)
    assert s["phase_a_run_dir"] is not None
    assert s["windows_count"] == 4
    assert s["target_sha256"] == "abc"


def test_status_reports_empty_families(tmp_path):
    run_dir = _make_run_dir(tmp_path, families=["claude", "chatgpt"])
    s = workflow.status(run_dir)
    assert s["families"]["claude"]["t3_window_indices"] == []
    assert s["families"]["claude"]["missing"] == [1, 2, 3, 4]
    assert s["families"]["chatgpt"]["t3_window_indices"] == []


def test_status_reports_partial_t3_outputs(tmp_path):
    run_dir = _make_run_dir(tmp_path, windows_count=4)
    fam = run_dir / "outputs" / "claude"
    (fam / "window_1.txt").write_text("x")
    (fam / "window_3.md").write_text("y")
    s = workflow.status(run_dir)
    assert s["families"]["claude"]["t3_window_indices"] == [1, 3]
    assert s["families"]["claude"]["missing"] == [2, 4]


def test_status_reports_t4_present(tmp_path):
    run_dir = _make_run_dir(tmp_path, windows_count=4)
    fam = run_dir / "outputs" / "claude"
    (fam / "windows_batched.json").write_text("[]")
    s = workflow.status(run_dir)
    assert s["families"]["claude"]["has_t4"] is True


def test_status_reports_target_continuation_presence(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    s = workflow.status(run_dir)
    assert s["target_continuation_present"] is False
    (run_dir / "target_continuation.json").write_text("[]")
    s2 = workflow.status(run_dir)
    assert s2["target_continuation_present"] is True


def test_status_reports_phase_b_artifacts(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    (run_dir / "ingested.json").write_text("{}")
    (run_dir / "evidence_pack.md").write_text("# pack")
    s = workflow.status(run_dir)
    assert s["phase_b_artifacts"]["ingested_json"] is True
    assert s["phase_b_artifacts"]["distances_json"] is False
    assert s["phase_b_artifacts"]["evidence_pack_md"] is True


def test_status_errors_on_missing_run_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        workflow.status(tmp_path / "nope")


def test_render_status_produces_readable_output(tmp_path):
    run_dir = _make_run_dir(tmp_path, families=["claude"])
    s = workflow.status(run_dir)
    rendered = workflow.render_status(s)
    assert "Run status" in rendered
    assert "claude" in rendered
    assert "Phase B artifacts" in rendered


# ============================================================
# score
# ============================================================


def test_score_invokes_three_phase_b_steps_in_order(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=0)
    r = workflow.score(run_dir, runner=runner)
    assert len(runner.calls) == 3
    assert "ingest_outputs.py" in runner.calls[0][1]
    assert "compute_distances.py" in runner.calls[1][1]
    assert "compose_evidence_pack.py" in runner.calls[2][1]
    assert r.evidence_pack_json == run_dir / "evidence_pack.json"
    assert r.evidence_pack_md == run_dir / "evidence_pack.md"


def test_score_passes_target_continuation_when_present(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    (run_dir / "target_continuation.json").write_text("[]")
    runner = RecordingRunner(returncode=0)
    workflow.score(run_dir, runner=runner)
    cmd2 = runner.calls[1]
    assert "--target-continuation" in cmd2


def test_score_omits_target_continuation_when_absent(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=0)
    workflow.score(run_dir, runner=runner)
    cmd2 = runner.calls[1]
    assert "--target-continuation" not in cmd2


def test_score_forwards_embedding_alias(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=0)
    workflow.score(run_dir, embedding_alias="custom-model", runner=runner)
    cmd2 = runner.calls[1]
    assert cmd2[cmd2.index("--embedding-alias") + 1] == "custom-model"


def test_score_stops_on_ingest_failure(tmp_path):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=1, stderr="ingest broke")
    with pytest.raises(RuntimeError, match="step 'ingest' failed"):
        workflow.score(run_dir, runner=runner)
    assert len(runner.calls) == 1


def test_score_stops_on_distances_failure(tmp_path):
    run_dir = _make_run_dir(tmp_path)

    class TwoStepRunner:
        def __init__(self):
            self.calls = []
            self.count = 0
        def __call__(self, cmd):
            self.calls.append(list(cmd))
            self.count += 1
            return SimpleNamespace(returncode=0 if self.count == 1 else 2, stdout="", stderr="distances broke")

    runner = TwoStepRunner()
    with pytest.raises(RuntimeError, match="step 'distances' failed"):
        workflow.score(run_dir, runner=runner)
    assert len(runner.calls) == 2


def test_score_errors_on_missing_phase_a(tmp_path):
    run_dir = tmp_path / "runs" / "t1"
    run_dir.mkdir(parents=True)
    (run_dir / "prompts").mkdir()
    (run_dir / "outputs").mkdir()
    with pytest.raises(RuntimeError, match="no Phase A run found"):
        workflow.score(run_dir, runner=RecordingRunner())


def test_score_errors_on_missing_run_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        workflow.score(tmp_path / "nope", runner=RecordingRunner())


# ============================================================
# Helpers
# ============================================================


def test_parse_csv():
    assert workflow._parse_csv("a,b,c") == ["a", "b", "c"]
    assert workflow._parse_csv("a, b ,c") == ["a", "b", "c"]
    assert workflow._parse_csv("a,,b") == ["a", "b"]


def test_resolve_run_id_uses_supplied():
    assert workflow._resolve_run_id("custom") == "custom"


def test_resolve_run_id_generates_default():
    rid = workflow._resolve_run_id(None)
    assert rid.startswith("mirror_")


# ============================================================
# CLI
# ============================================================


def test_cli_prepare_returns_zero(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    runner = RecordingRunner(returncode=0)
    monkeypatch.setattr(workflow, "_default_runner", runner)
    rc = workflow.main([
        "prepare", str(target),
        "--runs-root", str(tmp_path / "runs"),
        "--run-id", "cli_t1",
        "--families", "claude,chatgpt",
    ])
    assert rc == 0
    assert (tmp_path / "runs" / "cli_t1" / "WORKFLOW.md").exists()


def test_cli_status_returns_zero(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    rc = workflow.main(["status", str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Run status" in out


def test_cli_status_json(tmp_path, capsys):
    run_dir = _make_run_dir(tmp_path)
    rc = workflow.main(["status", str(run_dir), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "families" in parsed


def test_cli_status_missing_dir(tmp_path):
    rc = workflow.main(["status", str(tmp_path / "nope")])
    assert rc == 1


def test_cli_score_returns_zero(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=0)
    monkeypatch.setattr(workflow, "_default_runner", runner)
    rc = workflow.main(["score", str(run_dir)])
    assert rc == 0


def test_cli_score_failure_returns_one(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    runner = RecordingRunner(returncode=1, stderr="bork")
    monkeypatch.setattr(workflow, "_default_runner", runner)
    rc = workflow.main(["score", str(run_dir)])
    assert rc == 1
