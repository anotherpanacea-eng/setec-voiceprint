#!/usr/bin/env python3
"""Tests for gmail_locator_map.py — the metadata-only companion locator-map
builder for the shadow reacquisition gate.

Coverage: strict one-to-one join (and vice versa), overwrite refusal,
missing/duplicate locator refusal, atomic-publish crash safety (replace failure
+ read-back verification), never-touch-foreign-files, never-open-.txt, optional
thread/order fields, private-root enforcement, empty-input policy, and prose-free
stdout/stderr. Synthetic inline fixtures only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquisition_core as ac  # type: ignore  # noqa: E402
import gmail_locator_map as G  # type: ignore  # noqa: E402


def _loc(kind: str, seed: str) -> str:
    return "sha256:" + hashlib.sha256(f"{kind}:{seed}".encode()).hexdigest()


def _private_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ai-prose-baselines-private" / "shadow" / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_piece(out: Path, stem: str, *, entry=..., thread=None, order=None,
                 in_manifest=True, sidecar=True, txt=True) -> dict | None:
    """Create a (.txt, .meta.json) pair + optional manifest row. Returns the
    manifest row dict (or None if not in_manifest)."""
    if entry is ...:
        entry = _loc("entry", stem)
    if txt:
        (out / f"{stem}.txt").write_text(f"body for {stem}\n", encoding="utf-8")
    if sidecar:
        meta = {"content_hash": _loc("content", stem)}
        meta["author_corpus_entry_locator"] = entry
        if thread is not None:
            meta["author_corpus_thread_locator"] = thread
        meta["author_corpus_order_timestamp"] = order
        (out / f"{stem}.meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    row = None
    if in_manifest:
        row = {"id": stem, "content_hash": _loc("content", stem)}
        with (out / "draft_manifest.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def _run(out: Path, map_out: Path, extra=None) -> int:
    argv = ["--output-dir", str(out), "--map-out", str(map_out)]
    return G.main(argv + (extra or []))


def test_happy_path_writes_map_with_matching_rows(tmp_path, capsys):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a", entry=_loc("entry", "a"),
                 thread=_loc("thread", "a"), order="2020-01-01T00:00:00+00:00")
    _write_piece(out, "2020-01-02_b", entry=_loc("entry", "b"))
    map_out = out / "shadow_locator_map.jsonl"

    assert _run(out, map_out) == 0
    rows = [json.loads(x) for x in map_out.read_text().splitlines() if x.strip()]
    assert [r["source_id"] for r in rows] == ["2020-01-01_a", "2020-01-02_b"]
    assert rows[0]["private_entry_locator"] == _loc("entry", "a")
    assert rows[0]["private_thread_locator"] == _loc("thread", "a")
    assert rows[0]["private_order_timestamp"] == "2020-01-01T00:00:00+00:00"
    # b had no thread/order → those keys omitted, not null.
    assert "private_thread_locator" not in rows[1]
    assert "private_order_timestamp" not in rows[1]
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["rows_written"] == 2


def test_refuses_to_overwrite_existing_map(tmp_path):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = out / "map.jsonl"
    map_out.write_text("SENTINEL", encoding="utf-8")

    assert _run(out, map_out) != 0
    assert map_out.read_text() == "SENTINEL"
    assert not list(out.glob("*.tmp*"))


def test_missing_locator_refuses_and_writes_nothing(tmp_path, capsys):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    # entry=None sidecar (locator absent).
    _write_piece(out, "2020-01-02_b", entry=None)
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 2
    assert not map_out.exists()
    err = capsys.readouterr().err
    counts = json.loads(err.splitlines()[0])["locator_map_coverage_gap"]
    assert counts["missing_locator"] == 1


def test_orphan_sidecar_without_manifest_row_refuses(tmp_path, capsys):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    _write_piece(out, "2020-01-02_b", in_manifest=False)  # sidecar, no row
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 2
    assert not map_out.exists()
    counts = json.loads(capsys.readouterr().err.splitlines()[0])[
        "locator_map_coverage_gap"]
    assert counts["orphan_sidecars"] == 1


def test_orphan_manifest_id_without_sidecar_refuses(tmp_path, capsys):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    _write_piece(out, "2020-01-02_b", sidecar=False, txt=False)  # row, no sidecar
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 2
    assert not map_out.exists()
    counts = json.loads(capsys.readouterr().err.splitlines()[0])[
        "locator_map_coverage_gap"]
    assert counts["orphan_manifest_ids"] == 1


def test_duplicate_locator_across_two_ids_refuses(tmp_path, capsys):
    out = _private_dir(tmp_path)
    shared = _loc("entry", "shared")
    _write_piece(out, "2020-01-01_a", entry=shared)
    _write_piece(out, "2020-01-02_b", entry=shared)
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 2
    assert not map_out.exists()
    err = capsys.readouterr().err
    counts = json.loads(err.splitlines()[0])["locator_map_coverage_gap"]
    assert counts["duplicate_locators"] == 1
    assert shared in err  # surfaced on stderr for operator review
    # ...but never in any written artifact (none was written).
    assert not map_out.exists()


def test_atomic_publish_link_failure_leaves_no_partial_map(tmp_path, monkeypatch):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = out / "map.jsonl"

    def boom(src, dst):
        raise OSError("simulated link failure")

    monkeypatch.setattr(G.os, "link", boom)
    assert _run(out, map_out) != 0
    assert not map_out.exists()
    assert not list(out.glob("*.tmp*"))  # finally-unlink cleaned the temp


def test_reread_validation_catches_a_corrupted_write(tmp_path, monkeypatch):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = out / "map.jsonl"

    link_calls = []
    monkeypatch.setattr(G, "_reread_text", lambda p: "CORRUPTED CONTENT\n")
    monkeypatch.setattr(
        G.os, "link",
        lambda s, d: link_calls.append((s, d)),
    )
    assert _run(out, map_out) != 0
    assert link_calls == []  # os.link gated on the read-back check
    assert not map_out.exists()
    assert not list(out.glob("*.tmp*"))


def test_publish_preserves_foreign_destination_created_after_check(
    tmp_path, monkeypatch, capsys,
):
    # TOCTOU: a foreign file appears at map_out AFTER run()'s initial
    # existence check but BEFORE the atomic publish. The exclusive os.link claim
    # must refuse and leave the foreign bytes untouched (os.replace would clobber
    # them). Injected by wrapping build_locator_map, which runs between the check
    # and publish_map.
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = out / "map.jsonl"
    foreign = "FOREIGN DESTINATION - MUST NOT BE OVERWRITTEN\n"

    real_build = G.build_locator_map

    def build_then_race(output_dir, manifest_path):
        result = real_build(output_dir, manifest_path)
        map_out.write_text(foreign, encoding="utf-8")  # concurrent foreign writer
        return result

    monkeypatch.setattr(G, "build_locator_map", build_then_race)
    assert _run(out, map_out) != 0                  # refused, not a silent clobber
    assert map_out.read_text(encoding="utf-8") == foreign
    assert not list(out.glob("*.tmp*"))             # temp swept, no residue


def test_duplicate_manifest_id_refuses(tmp_path, capsys):
    # A repeated manifest id must be REJECTED, not silently collapsed through a
    # set (which would hide one of the two colliding rows from the join).
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    with (out / "draft_manifest.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"id": "2020-01-01_a", "content_hash": _loc("content", "twin")},
            sort_keys=True,
        ) + "\n")
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 1
    assert not map_out.exists()
    assert "repeats an id" in capsys.readouterr().err


def test_never_touches_foreign_files(tmp_path):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    _write_piece(out, "2020-01-02_b")
    (out / "recipient_map.json").write_text('{"x": "recipient_01"}', encoding="utf-8")
    map_out = out / "map.jsonl"

    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir() if p.is_file()
    }
    assert _run(out, map_out) == 0
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir() if p.is_file() and p.name != map_out.name
    }
    for name, digest in before.items():
        assert after[name] == digest, f"{name} was modified"


def test_never_opens_txt_files(tmp_path, monkeypatch):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    _write_piece(out, "2020-01-02_b")
    map_out = out / "map.jsonl"

    opened: list[str] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_open = Path.open

    def rec_read_text(self, *a, **k):
        opened.append(str(self))
        return real_read_text(self, *a, **k)

    def rec_read_bytes(self, *a, **k):
        opened.append(str(self))
        return real_read_bytes(self, *a, **k)

    def rec_open(self, *a, **k):
        opened.append(str(self))
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", rec_read_text)
    monkeypatch.setattr(Path, "read_bytes", rec_read_bytes)
    monkeypatch.setattr(Path, "open", rec_open)
    assert _run(out, map_out) == 0
    assert not any(p.endswith(".txt") for p in opened), opened


def test_thread_locator_and_order_timestamp_optional_fields(tmp_path):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a", thread=_loc("thread", "a"),
                 order="2020-01-01T00:00:00+00:00")
    _write_piece(out, "2020-01-02_b")  # neither thread nor order
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 0
    rows = {json.loads(x)["source_id"]: json.loads(x)
            for x in map_out.read_text().splitlines() if x.strip()}
    assert "private_thread_locator" in rows["2020-01-01_a"]
    assert "private_order_timestamp" in rows["2020-01-01_a"]
    assert "private_thread_locator" not in rows["2020-01-02_b"]
    assert "private_order_timestamp" not in rows["2020-01-02_b"]


def test_output_path_outside_private_root_refuses(tmp_path):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = tmp_path / "public_area" / "map.jsonl"  # no private component

    with pytest.raises(SystemExit) as exc:
        _run(out, map_out)
    assert exc.value.code == 2
    assert not map_out.exists()


def test_empty_corpus_refuses_without_allow_empty_and_succeeds_with_it(tmp_path):
    out = _private_dir(tmp_path)  # no sidecars, no manifest
    map_out = out / "map.jsonl"

    assert _run(out, map_out) == 1
    assert not map_out.exists()

    assert _run(out, map_out, ["--allow-empty"]) == 0
    assert map_out.exists()
    assert map_out.read_text() == ""  # 0-row map


def test_cli_argparse_smoke(tmp_path):
    parser = G.build_arg_parser()
    ns = parser.parse_args(
        ["--output-dir", "o", "--map-out", "m", "--allow-empty"]
    )
    assert ns.output_dir == "o" and ns.map_out == "m" and ns.allow_empty is True
    with pytest.raises(SystemExit):
        parser.parse_args(["--output-dir", "o"])  # missing --map-out


def test_stdout_and_stderr_stay_prose_free(tmp_path, capsys):
    out = _private_dir(tmp_path)
    _write_piece(out, "2020-01-01_a")
    map_out = out / "map.jsonl"
    assert _run(out, map_out) == 0
    captured = capsys.readouterr()
    # stdout is exactly one JSON line with count/path/hash keys.
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    summary = json.loads(lines[0])
    assert set(summary) == {"rows_written", "output", "output_sha256"}

    # Failure path: offending detail confined to stderr, never the artifact.
    out2 = tmp_path / "ai-prose-baselines-private" / "shadow" / "two"
    out2.mkdir(parents=True, exist_ok=True)
    _write_piece(out2, "2020-01-01_a", in_manifest=False)
    map_out2 = out2 / "map.jsonl"
    assert _run(out2, map_out2) == 2
    assert not map_out2.exists()
