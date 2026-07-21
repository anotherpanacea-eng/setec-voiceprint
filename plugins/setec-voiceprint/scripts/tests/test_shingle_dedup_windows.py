"""Native-Windows contract tests for the B3 shingle staging utility.

These run only on Windows: POSIX coverage deliberately does not pretend to
exercise reparse-point or binary-console behaviour on another platform.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows only")

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "shingle_dedup.py"


def _words(count: int) -> str:
    return " ".join(f"win{item:04d}" for item in range(count))


def _run(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, timeout=45, check=False)


def test_windows_unicode_space_hash_paths_and_binary_lf_receipts(tmp_path: Path) -> None:
    root = tmp_path / "space # unicode Ω"
    root.mkdir()
    manifest = root / "records # Ω.jsonl"
    index = root / "index # Ω.sqlite"
    state = root / "state # Ω"
    text = _words(16)
    manifest.write_bytes(json.dumps({
        "id": "record", "draft_id": "draft", "stage": "first", "stage_order": 0, "text": text,
    }, separators=(",", ":")).encode("utf-8") + b"\r\n")
    build = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                 "--checkpoint-dir", str(state))
    assert build.returncode == 0, build.stderr.decode("utf-8", "replace")
    assert build.stdout.endswith(b"\n") and b"\r" not in build.stdout
    assert build.stderr.endswith(b"\n") and b"\r" not in build.stderr
    receipt = json.loads(build.stdout)
    query = root / "query # Ω.txt"
    query.write_bytes(text.encode("utf-8"))
    report = root / "report # Ω.json"
    result = _run("query-doc", "--index", str(index), "--index-sha256", receipt["index_sha256"],
                  "--query-file", str(query), "--query-id", "query", "--report-out", str(report))
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.endswith(b"\n") and b"\r" not in result.stdout
    assert report.read_bytes().endswith(b"\n") and b"\r" not in report.read_bytes()


def test_windows_publication_is_create_new(tmp_path: Path) -> None:
    manifest = tmp_path / "records.jsonl"
    index = tmp_path / "index.sqlite"
    manifest.write_bytes(json.dumps({
        "id": "record", "draft_id": "draft", "stage": "first", "stage_order": 0, "text": _words(16),
    }, separators=(",", ":")).encode() + b"\n")
    index.write_bytes(b"winner")
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(tmp_path / "state"))
    assert result.returncode == 3
    assert index.read_bytes() == b"winner"
    assert b"Traceback" not in result.stderr


def test_windows_stable_hardlinked_sources_are_read_only_inputs(tmp_path: Path) -> None:
    manifest = tmp_path / "records.jsonl"
    index = tmp_path / "index.sqlite"
    text = _words(16)
    manifest.write_bytes(json.dumps({
        "id": "record", "draft_id": "draft", "stage": "first", "stage_order": 0,
        "text": text,
    }, separators=(",", ":")).encode() + b"\n")
    (tmp_path / "records-alias.jsonl").hardlink_to(manifest)
    build = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                 "--checkpoint-dir", str(tmp_path / "build-state"))
    assert build.returncode == 0, build.stderr.decode("utf-8", "replace")
    receipt = json.loads(build.stdout)

    (tmp_path / "index-alias.sqlite").hardlink_to(index)
    query = tmp_path / "query.txt"; query.write_bytes(text.encode())
    (tmp_path / "query-alias.txt").hardlink_to(query)
    report = tmp_path / "query-report.json"
    queried = _run("query-doc", "--index", str(index), "--index-sha256", receipt["index_sha256"],
                   "--query-file", str(query), "--query-id", "query", "--report-out", str(report))
    assert queried.returncode == 0, queried.stderr.decode("utf-8", "replace")

    batch_report = tmp_path / "batch-report.json"
    batched = _run("batch-report", "--index", str(index), "--index-sha256", receipt["index_sha256"],
                   "--report-out", str(batch_report),
                   "--checkpoint-dir", str(tmp_path / "batch-state"))
    assert batched.returncode == 0, batched.stderr.decode("utf-8", "replace")


def test_windows_native_directory_junction_is_refused_across_ancestor_chain(tmp_path: Path) -> None:
    target = tmp_path / "real-ancestor"; target.mkdir()
    junction = tmp_path / "junction-ancestor"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, check=False,
    )
    if created.returncode != 0:
        pytest.skip("native junction creation unavailable")
    manifest = tmp_path / "junction-records.jsonl"
    manifest.write_bytes(json.dumps({
        "id": "record", "draft_id": "draft", "stage": "first",
        "stage_order": 0, "text": _words(16),
    }, separators=(",", ":")).encode() + b"\n")
    index = tmp_path / "junction-index.sqlite"
    checkpoint_dir = junction / "child-state"
    result = _run("build-index", "--manifest", str(manifest), "--index-out", str(index),
                  "--checkpoint-dir", str(checkpoint_dir))
    assert result.returncode == 3 and not index.exists()
    assert not (target / "child-state").exists()
    assert b"Traceback" not in result.stderr
