"""Black-box cp1252 regressions for the remaining plugin CLI writers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import test_external_mirror_phase_b as mirror_fixtures
import test_narrative_longform_agreement as longform_fixtures
import test_narrative_polarity_audit as polarity_fixtures
import test_sliding_window_heatmap as heatmap_fixtures
import test_within_doc_segmentation as segmentation_fixtures


SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent


def _run(script: Path, args: list[str], encoding: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "0"
    env["PYTHONIOENCODING"] = encoding
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        cwd=script.parent,
        env=env,
        check=False,
    )


def _heatmap_input(path: Path) -> None:
    windows = [
        heatmap_fixtures._make_window(
            start_word=0, end_word=500, band="Heavily smoothed",
            fraction=0.55, flagged=["burstiness_B"],
        ),
        heatmap_fixtures._make_window(
            start_word=500, end_word=1000, band="Lightly smoothed",
            fraction=0.12,
        ),
    ]
    path.write_text(
        json.dumps({"windows": heatmap_fixtures._make_windows_block(windows)}),
        encoding="utf-8",
    )


def _polarity_input(path: Path) -> None:
    rows = polarity_fixtures._make_rows(n_human=25, n_ai=25)
    path.write_text(
        "".join(json.dumps({
            "text_id": row.text_id,
            "label": row.raw_label,
            "narrative_values": row.values,
        }) + "\n" for row in rows),
        encoding="utf-8",
    )


def _distance_input(path: Path) -> None:
    ingested = mirror_fixtures._build_ingested(
        windows_count=1,
        families_texts={
            "claude": ["A quiet river crossed the valley."],
            "chatgpt": ["The river crossed a quiet valley."],
        },
    )
    path.write_text(json.dumps(ingested), encoding="utf-8")


def test_heatmap_stdout_and_complete_file_run(tmp_path: Path) -> None:
    script = SCRIPTS / "sliding_window_heatmap.py"
    source = tmp_path / "audit.json"
    _heatmap_input(source)
    stdout_args = ["--in", str(source)]
    control = _run(script, stdout_args, "utf-8")
    constrained = _run(script, stdout_args, "cp1252")
    assert control.returncode == 0, control.stderr.decode("utf-8", "replace")
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    assert "×" in constrained.stdout.decode("utf-8")
    assert constrained.stdout == control.stdout

    private = tmp_path / "ai-prose-baselines-private"
    md = private / "heatmap.md"
    js = private / "heatmap.json"
    args = ["--in", str(source), "--out", str(md), "--json-out", str(js)]
    control = _run(script, args, "utf-8")
    assert control.returncode == 0, control.stderr.decode("utf-8", "replace")
    control_md, control_json = md.read_bytes(), js.read_bytes()
    js.unlink()
    constrained = _run(script, args, "cp1252")
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    stderr = constrained.stderr.decode("utf-8")
    assert stderr.count("→") == 2
    assert constrained.stderr == control.stderr
    assert md.read_bytes() == control_md
    assert js.read_bytes() == control_json
    payload = json.loads(js.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["results"]["n_windows"] == 2


def test_segmentation_help_and_success_json(tmp_path: Path) -> None:
    script = SCRIPTS / "within_doc_segmentation.py"
    control = _run(script, ["--help"], "utf-8")
    constrained = _run(script, ["--help"], "cp1252")
    assert control.returncode == 0
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    assert "fewer → bad_input" in constrained.stdout.decode("utf-8")
    assert constrained.stdout == control.stdout

    source = tmp_path / "two-register.txt"
    source.write_text(segmentation_fixtures._TWO_REGISTER_TEXT, encoding="utf-8")
    args = [str(source), "--window-sentences", "3", "--stride-sentences", "1"]
    control = _run(script, args, "utf-8")
    constrained = _run(script, args, "cp1252")
    assert control.returncode == 0, control.stderr.decode("utf-8", "replace")
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    assert constrained.stdout == control.stdout
    payload = json.loads(constrained.stdout.decode("utf-8"))
    assert payload["available"] is True
    assert payload["task_surface"] == "document_segmentation"
    assert isinstance(payload["results"], dict)


def test_longform_register_and_evaluate_summaries(tmp_path: Path) -> None:
    script = SCRIPTS / "calibration" / "narrative_longform_agreement.py"
    rows = longform_fixtures.make_rows()
    thresholds = tmp_path / "thresholds.json"
    design = tmp_path / "design.jsonl"
    manifest = tmp_path / "manifest.jsonl"
    registration = tmp_path / "registration.json"
    receipt = tmp_path / "receipt.json"
    longform_fixtures.write_json(thresholds, longform_fixtures.LICENSED_THRESHOLDS)
    longform_fixtures.write_jsonl(
        design,
        [{"work_id": row["work_id"], "n_words": row["n_words"]} for row in rows],
    )
    longform_fixtures.write_jsonl(manifest, rows)
    segmenter = longform_fixtures.SEGMENTER
    judge = longform_fixtures.JUDGE
    common = ["--thresholds", str(thresholds), "--date", longform_fixtures.DATE]
    register_args = [
        "--register", "--manifest", str(design), "--out", str(registration),
        *common,
        "--segmenter-version", segmenter["version"],
        "--segmenter-params-sha256", segmenter["params_sha256"],
        "--segment-target-words", str(segmenter["segment_target_words"]),
        "--judge-kind", judge["kind"],
        "--judge-model", judge["model"],
        "--judge-model-revision", judge["model_revision"],
        "--judge-prompt-version", judge["prompt_version"],
    ]
    evaluate_args = [
        "--evaluate", "--manifest", str(manifest),
        "--registration", str(registration), "--out", str(receipt), *common,
    ]
    register_control = _run(script, register_args, "utf-8")
    assert register_control.returncode == 0, register_control.stderr.decode("utf-8", "replace")
    registration_bytes = registration.read_bytes()
    evaluate_control = _run(script, evaluate_args, "utf-8")
    assert evaluate_control.returncode == 0, evaluate_control.stderr.decode("utf-8", "replace")
    receipt_bytes = receipt.read_bytes()
    register_constrained = _run(script, register_args, "cp1252")
    evaluate_constrained = _run(script, evaluate_args, "cp1252")
    assert register_constrained.returncode == 0, register_constrained.stderr.decode("utf-8", "replace")
    assert evaluate_constrained.returncode == 0, evaluate_constrained.stderr.decode("utf-8", "replace")
    assert register_constrained.stdout == register_control.stdout
    assert evaluate_constrained.stdout == evaluate_control.stdout
    assert "registration →" in register_constrained.stdout.decode("utf-8")
    assert "receipt →" in evaluate_constrained.stdout.decode("utf-8")
    assert "verdicts:" in evaluate_constrained.stdout.decode("utf-8")
    assert registration.read_bytes() == registration_bytes
    assert receipt.read_bytes() == receipt_bytes
    assert json.loads(registration_bytes)["schema"] == "narrative-longform-registration/1"
    assert json.loads(receipt_bytes)["schema_version"] == "narrative_longform_validation_receipt/1"


def test_polarity_outputs_and_summaries(tmp_path: Path) -> None:
    script = SCRIPTS / "calibration" / "narrative_polarity_audit.py"
    manifest = tmp_path / "balanced.jsonl"
    report = tmp_path / "polarity.json"
    md = tmp_path / "polarity.md"
    _polarity_input(manifest)
    args = [
        "--manifest", str(manifest), "--out-json", str(report),
        "--out-md", str(md), "--corpus-name", "synthetic-balanced",
    ]
    control = _run(script, args, "utf-8")
    assert control.returncode == 0, control.stderr.decode("utf-8", "replace")
    report_bytes, md_bytes = report.read_bytes(), md.read_bytes()
    constrained = _run(script, args, "cp1252")
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    assert constrained.stdout == control.stdout
    assert constrained.stdout.decode("utf-8").count("→") == 2
    assert report.read_bytes() == report_bytes
    assert md.read_bytes() == md_bytes
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["min_class_n"] == 20
    assert payload["n_rows"]["human"] == 25 and payload["n_rows"]["ai"] == 25
    assert "synthetic-balanced" in md.read_text(encoding="utf-8")


def test_word_jaccard_distance_summary_and_artifact(tmp_path: Path) -> None:
    script = SCRIPTS / "external_mirror" / "compute_distances.py"
    ingested = tmp_path / "ingested.json"
    output = tmp_path / "distances.json"
    _distance_input(ingested)
    args = [str(ingested), "--metrics", "word_jaccard", "--out", str(output)]
    control = _run(script, args, "utf-8")
    assert control.returncode == 0, control.stderr.decode("utf-8", "replace")
    reference = json.loads(output.read_text(encoding="utf-8"))
    constrained = _run(script, args, "cp1252")
    assert constrained.returncode == 0, constrained.stderr.decode("utf-8", "replace")
    assert constrained.stdout == control.stdout
    assert "2 families × 1 windows →" in constrained.stdout.decode("utf-8")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["metrics_available"] == ["word_jaccard"]
    assert payload["distance_matrices_by_metric"]["word_jaccard"] is not None
    assert payload["distance_matrices_by_metric"]["sbert"] is None
    for item in (reference, payload):
        stamp = datetime.fromisoformat(item["computed_at"])
        assert stamp.tzinfo is not None
    reference.pop("computed_at")
    payload.pop("computed_at")
    assert payload == reference


def test_copied_plugin_representative_entrypoints(tmp_path: Path) -> None:
    copied = tmp_path / "copied-plugin"
    shutil.copytree(
        PLUGIN, copied,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache", "*.pyc"),
    )
    scripts = copied / "scripts"
    heatmap = tmp_path / "copy-audit.json"
    _heatmap_input(heatmap)
    private = tmp_path / "ai-prose-baselines-private"
    md = private / "copy-heatmap.md"
    js = private / "copy-heatmap.json"
    result = _run(
        scripts / "sliding_window_heatmap.py",
        ["--in", str(heatmap), "--out", str(md), "--json-out", str(js)],
        "cp1252",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stderr.decode("utf-8").count("→") == 2
    assert md.is_file() and js.is_file()

    manifest = tmp_path / "copy-balanced.jsonl"
    _polarity_input(manifest)
    report = tmp_path / "copy-polarity.json"
    findings = tmp_path / "copy-polarity.md"
    result = _run(
        scripts / "calibration" / "narrative_polarity_audit.py",
        ["--manifest", str(manifest), "--out-json", str(report),
         "--out-md", str(findings), "--corpus-name", "synthetic-copy"],
        "cp1252",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert report.is_file() and findings.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["min_class_n"] == 20

    ingested = tmp_path / "copy-ingested.json"
    _distance_input(ingested)
    distances = tmp_path / "copy-distances.json"
    result = _run(
        scripts / "external_mirror" / "compute_distances.py",
        [str(ingested), "--metrics", "word_jaccard", "--out", str(distances)],
        "cp1252",
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(distances.read_text(encoding="utf-8"))["metrics_available"] == ["word_jaccard"]
