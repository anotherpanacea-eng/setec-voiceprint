#!/usr/bin/env python3
"""End-to-end tests for the corpus_hygiene task surface running
through shard_runner.

Builds a synthetic mini-corpus of .md fixtures, writes a manifest
that references them, runs ``shard_runner shard --task
corpus_hygiene`` to split into shards, runs ``work`` to score, and
finally runs ``aggregate`` to produce a cross-shard hygiene
summary. The synthetic fixtures pull from the same css-contamination
test data the single-process ``test_check_corpus.py`` exercises, so
the sharded artifact can be parity-tested against the single-process
output.

The tests pin:

  * ``shard --task corpus_hygiene`` records the task name + the
    threshold/strip params in state.json.
  * ``work`` (default --task) dispatches to the corpus_hygiene
    scorer surface because state.json is authoritative.
  * Per-shard cache files contain ``records`` and ``meta`` with the
    hygiene-summary shape.
  * ``aggregate`` produces a survey JSON whose ``status``,
    ``n_clean``, ``n_warning``, ``n_fail`` match the single-process
    ``check_corpus_paths`` output on the same input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import shard_runner as sr  # type: ignore
import shard_state as ss  # type: ignore
import check_corpus as cc  # type: ignore


FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"


# --------------- Fixtures ----------------------------------------


def _write_hygiene_manifest(
    path: Path, *, n_clean: int = 4, n_contaminated: int = 2,
) -> None:
    """Build a manifest of repeated CLEAN/CONTAMINATED rows. The
    rows reference the real CSS contamination fixtures so the
    scorer produces real (non-stubbed) hygiene records — this is
    the integration-test path, not a unit test.

    Repeats lets us hit a sharded run with more rows than shards so
    the test exercises actual cross-shard aggregation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_clean):
            fh.write(json.dumps({
                "text_id": f"clean_{i:03d}",
                "path": str(CLEAN),
                "ai_status": "pre_ai_human",
                "use": ["validation"],
                "privacy": "shareable",
                "register": "literary_fiction",
            }) + "\n")
        for i in range(n_contaminated):
            fh.write(json.dumps({
                "text_id": f"contaminated_{i:03d}",
                "path": str(CONTAMINATED),
                "ai_status": "pre_ai_human",
                "use": ["validation"],
                "privacy": "shareable",
                "register": "blog_essay",
            }) + "\n")


@pytest.fixture
def hygiene_run(tmp_path: Path):
    """Set up a sharded run that uses the corpus_hygiene task
    surface. Returns the base directory, run id, and source
    manifest path."""
    base = tmp_path / "baselines"
    base.mkdir()
    src = base / "hygiene_src" / "manifest.jsonl"
    _write_hygiene_manifest(src, n_clean=4, n_contaminated=2)
    run_id = "hygiene_test_run"
    rc = sr.main([
        "--base-dir", str(base),
        "shard",
        "--task", "corpus_hygiene",
        "--source-manifest", str(src),
        "--run-id", run_id,
        "--shard-size", "3",
        "--stratify", "ai_status",
        "--shuffle-seed", "42",
    ])
    assert rc == 0
    return {"base": base, "src": src, "run_id": run_id}


# --------------- shard subcommand --------------------------------


class TestShardCorpusHygiene:
    """Pin that ``shard --task corpus_hygiene`` writes the task
    name + threshold/strip params into state.json so downstream
    workers / aggregator read them back without re-parsing CLI
    flags."""

    def test_state_records_corpus_hygiene_task(self, hygiene_run):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        assert state["task"] == "corpus_hygiene"

    def test_state_task_params_default_thresholds(self, hygiene_run):
        """When the operator passes no hygiene-specific flags, the
        registered task's default_task_params land in state.json."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        params = state["task_params"]
        assert params["warn_threshold"] == 0.01
        assert params["fail_threshold"] == 0.05
        assert params["strip_aggressive"] is False
        assert params["strip_rules"] is None

    def test_explicit_thresholds_land_in_state(self, tmp_path: Path):
        """Operator-supplied --warn-threshold / --fail-threshold
        override the registered defaults."""
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "manifest.jsonl"
        _write_hygiene_manifest(src, n_clean=2, n_contaminated=1)
        rc = sr.main([
            "--base-dir", str(base),
            "shard",
            "--task", "corpus_hygiene",
            "--source-manifest", str(src),
            "--run-id", "explicit_thresholds",
            "--shard-size", "10",
            "--stratify", "ai_status",
            "--warn-threshold", "0.02",
            "--fail-threshold", "0.10",
            "--strip-aggressive",
        ])
        assert rc == 0
        state = ss.read_state(
            sr.state_path(base, "explicit_thresholds")
        )
        assert state["task_params"]["warn_threshold"] == 0.02
        assert state["task_params"]["fail_threshold"] == 0.10
        assert state["task_params"]["strip_aggressive"] is True

    def test_unknown_task_fails_with_helpful_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ):
        """``shard --task does_not_exist`` exits 2 with a clear
        list of registered tasks so the operator can correct the
        typo. The argparse choices path is intentionally not used
        (so tests can register/unregister ad-hoc surfaces), and
        validation lands at runtime instead."""
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "manifest.jsonl"
        _write_hygiene_manifest(src, n_clean=1, n_contaminated=1)
        rc = sr.main([
            "--base-dir", str(base),
            "shard",
            "--task", "does_not_exist",
            "--source-manifest", str(src),
            "--run-id", "doomed",
            "--shard-size", "10",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "does_not_exist" in err
        assert "Registered" in err
        assert "corpus_hygiene" in err


# --------------- work subcommand ---------------------------------


class TestWorkCorpusHygiene:
    """Pin that ``work`` against a corpus_hygiene run actually
    runs check_corpus.score_manifest_rows for each shard and
    produces hygiene records (not calibration records)."""

    def test_work_produces_hygiene_records(self, hygiene_run):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "work",
            "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        assert rc == 0
        state = ss.read_state(sr.state_path(base, run_id))
        # Every shard should be done.
        for sid in state["shards"]:
            assert state["shards"][sid]["state"] == "done"
        # Each cache should have records that look like hygiene
        # records, not calibration records.
        for sid in state["shards"]:
            cp = sr.shard_cache_path(base, run_id, sid)
            assert cp.exists()
            cache = json.loads(cp.read_text())
            assert "records" in cache
            assert "meta" in cache
            for record in cache["records"]:
                # Hygiene records have these fields; calibration
                # records have "scores" / "label". The shape
                # difference is the load-bearing signal that
                # task dispatch worked.
                assert "status" in record
                assert "path" in record
                assert "strip_ratio" in record
                assert record["status"] in (
                    "clean", "warning", "fail", "error",
                )
            # Meta should carry the threshold contract.
            assert cache["meta"]["scorer_version"].startswith("corpus_hygiene")
            assert "warn_threshold" in cache["meta"]

    def test_contaminated_rows_scored_as_fail(self, hygiene_run):
        """The 2 contaminated fixture rows should each score
        status='fail' (their strip_ratio exceeds the default 5%
        fail threshold). The 4 clean rows should score 'clean'."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        state = ss.read_state(sr.state_path(base, run_id))
        n_fail = 0
        n_clean = 0
        for sid in state["shards"]:
            cp = sr.shard_cache_path(base, run_id, sid)
            cache = json.loads(cp.read_text())
            for record in cache["records"]:
                if record["status"] == "fail":
                    n_fail += 1
                elif record["status"] == "clean":
                    n_clean += 1
        assert n_fail == 2
        assert n_clean == 4

    def test_work_omitting_task_dispatches_via_state(self, hygiene_run):
        """Omitting ``--task`` reads the task from state.json
        and dispatches accordingly. The mismatch detection
        kicks in only when the operator passes ``--task``
        *explicitly* AND it disagrees with state.json.

        v1.45.0 originally defaulted ``--task`` to
        ``calibration_survey`` everywhere, which made the
        mismatch error message ("omit ``--task`` to silence
        this error") a lie for ``corpus_hygiene`` runs:
        omitting still tripped the mismatch because the
        default was a positive value. Codex review on PR #50
        caught this; the fix is to default to ``None`` and
        treat ``None`` as "read from state.json". This test
        pins the corrected behavior — a future contributor
        who flips the default back to a positive string will
        break this test.
        """
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
        ])
        # Work succeeds: --task omitted means "use state.json's
        # task", which is corpus_hygiene per the hygiene_run
        # fixture's `shard` invocation.
        assert rc == 0

    def test_work_explicit_task_mismatch_still_errors(
        self, hygiene_run,
    ):
        """Explicit ``--task`` that disagrees with state.json is
        still a fatal operator error. The mismatch detection
        survives the None-default change."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "calibration_survey",
        ])
        assert rc == 2


# --------------- aggregate subcommand ----------------------------


class TestAggregateCorpusHygiene:
    """Pin that ``aggregate --task corpus_hygiene`` produces a
    cross-shard summary whose counts match the single-process
    check_corpus_paths output on the same input. This is the
    parity contract that lets the sharded path replace the
    single-process path on RAID-scale corpora."""

    def test_aggregate_produces_hygiene_summary(
        self, hygiene_run, tmp_path: Path,
    ):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        out_path = tmp_path / "hygiene-aggregate.json"
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--task", "corpus_hygiene",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        assert payload["task"] == "corpus_hygiene"
        assert payload["n_files"] == 6  # 4 clean + 2 contaminated
        assert payload["n_clean"] == 4
        assert payload["n_fail"] == 2
        # An aggregate with any fail rows has status=fail.
        assert payload["status"] == "fail"
        # The dominant rule on the contaminated fixture is
        # css_rule_block per test_check_corpus.py.
        assert payload["dominant_rule"] == "css_rule_block"

    def test_aggregate_parity_with_single_process(
        self, hygiene_run, tmp_path: Path,
    ):
        """Cross-check: aggregate's n_clean / n_fail / dominant_rule
        should match what ``check_corpus.check_corpus_paths``
        produces when called on the same set of input files
        sequentially. This is the load-bearing parity test that
        lets an operator confidently swap the single-process
        path for the sharded path on RAID."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        out_path = tmp_path / "hygiene-aggregate.json"
        sr.main([
            "--base-dir", str(base), "aggregate",
            "--task", "corpus_hygiene",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        payload = json.loads(out_path.read_text())
        # Now compute the single-process equivalent.
        single = cc.check_corpus_paths(
            [CLEAN] * 4 + [CONTAMINATED] * 2,
        )
        assert payload["n_clean"] == single["n_clean"]
        assert payload["n_warning"] == single["n_warning"]
        assert payload["n_fail"] == single["n_fail"]
        assert payload["status"] == single["status"]
        assert payload["dominant_rule"] == single["dominant_rule"]


# --------------- score_manifest_rows --------------------------


class TestScoreManifestRows:
    """Direct tests for the new ``check_corpus.score_manifest_rows``
    helper, the surface the corpus_hygiene scorer adapter calls."""

    def test_score_manifest_rows_resolves_paths(self, tmp_path: Path):
        """Manifest rows with relative paths should resolve against
        the manifest's parent directory (mirroring
        ``paths_from_manifest``'s contract)."""
        # Write fixture copies into tmp so we can use relative paths.
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "c", "path": "clean.md"}) + "\n",
            encoding="utf-8",
        )
        records, summary = cc.score_manifest_rows(manifest)
        assert len(records) == 1
        assert records[0]["status"] == "clean"
        assert summary["n_clean"] == 1

    def test_score_manifest_rows_carries_text_id(self, tmp_path: Path):
        """Records returned by score_manifest_rows must carry
        through the manifest's text_id so the aggregator can join
        records back to source-manifest rows."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({
                "text_id": "carryover_id_42",
                "path": "clean.md",
            }) + "\n",
            encoding="utf-8",
        )
        records, _summary = cc.score_manifest_rows(manifest)
        assert records[0]["text_id"] == "carryover_id_42"

    def test_score_manifest_rows_emits_error_for_missing_path(
        self, tmp_path: Path,
    ):
        """A manifest row without a usable ``path`` field must
        emit an error record (not silently skip). Hygiene gates
        underreport when bad rows vanish; the aggregator's
        ``status: clean`` claim must be auditable against
        ``n_files == source_manifest_row_count``."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "good", "path": "clean.md"}) + "\n"
            + json.dumps({"text_id": "bad_no_path"}) + "\n",
            encoding="utf-8",
        )
        records, _ = cc.score_manifest_rows(manifest)
        # Both rows produce records now — the bad one as an error.
        assert len(records) == 2
        good = next(r for r in records if r.get("text_id") == "good")
        bad = next(r for r in records if r.get("status") == "error")
        assert good["status"] in ("clean", "warning", "fail")
        assert bad["status"] == "error"
        assert bad.get("id") == "bad_no_path"
        assert "missing" in bad["error"].lower() or "path" in bad["error"].lower()
        assert bad["manifest_lineno"] == 2

    def test_score_manifest_rows_emits_error_for_malformed_json(
        self, tmp_path: Path,
    ):
        """Malformed JSON lines must surface as error records,
        not silent skips. Without this, a partially-written
        manifest (e.g., from a crash mid-write) would underreport
        rather than fail loud."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "good", "path": "clean.md"}) + "\n"
            + "{not valid json\n"
            + json.dumps({"text_id": "good2", "path": "clean.md"}) + "\n",
            encoding="utf-8",
        )
        records, _ = cc.score_manifest_rows(manifest)
        # 3 records: good, error, good2 (the error gets emitted,
        # iteration continues on the next line).
        statuses = [r["status"] for r in records]
        assert "error" in statuses
        bad = next(r for r in records if r["status"] == "error")
        assert "malformed" in bad["error"].lower() or "json" in bad["error"].lower()
        assert bad["manifest_lineno"] == 2

    def test_score_manifest_rows_emits_error_for_non_object_row(
        self, tmp_path: Path,
    ):
        """A JSON-valid line that isn't a dict (e.g., a bare list
        or string) must surface as an error record. Same
        rationale: the aggregator must not under-count."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "good", "path": "clean.md"}) + "\n"
            + json.dumps(["not", "a", "dict"]) + "\n",
            encoding="utf-8",
        )
        records, _ = cc.score_manifest_rows(manifest)
        assert len(records) == 2
        bad = next(r for r in records if r["status"] == "error")
        assert "object" in bad["error"].lower() or "dict" in bad["error"].lower()


class TestAbsolutizeManifestPaths:
    """The shard-write step rewrites relative ``path`` fields to
    absolute paths anchored at the source manifest's parent
    directory. Without this, sharded ``corpus_hygiene`` (and any
    other manifest-row consumer that resolves paths relative to
    the manifest file) emits phantom errors for every row whose
    source path is relative — because after sharding the row
    lands in ``<run>/shards/<sid>/manifest.jsonl`` and ``relative
    to that parent`` means a different directory than the
    operator intended.

    Regression for the bug Codex's static review surfaced on
    PR #50, which I had personally hit by hand on the MAGE
    corpus earlier the same day."""

    def test_relative_paths_become_absolute(self, tmp_path: Path):
        # Source-side layout: manifest at <source>/m.jsonl with
        # text files at <source>/texts/<id>.txt — the natural
        # convention for the converters in this repo.
        source = tmp_path / "source"
        source.mkdir()
        (source / "texts").mkdir()
        (source / "texts" / "a.txt").write_text("alpha", encoding="utf-8")
        (source / "texts" / "b.txt").write_text("beta", encoding="utf-8")

        rows = [
            {"id": "a", "path": "texts/a.txt"},
            {"id": "b", "path": "texts/b.txt"},
        ]
        # absolutize against the source manifest's parent (== source/).
        from shard_runner import absolutize_manifest_paths
        rewritten = absolutize_manifest_paths(rows, source)
        # Each path is now absolute.
        for row in rewritten:
            assert Path(row["path"]).is_absolute()
            assert Path(row["path"]).is_file()

    def test_absolute_paths_pass_through_unchanged(
        self, tmp_path: Path,
    ):
        source = tmp_path / "source"
        source.mkdir()
        target = tmp_path / "elsewhere" / "x.txt"
        target.parent.mkdir(parents=True)
        target.write_text("x", encoding="utf-8")
        abs_str = str(target.resolve())

        rows = [{"id": "x", "path": abs_str}]
        from shard_runner import absolutize_manifest_paths
        rewritten = absolutize_manifest_paths(rows, source)
        assert rewritten[0]["path"] == abs_str

    def test_rows_without_path_field_pass_through(
        self, tmp_path: Path,
    ):
        from shard_runner import absolutize_manifest_paths
        rows = [{"id": "no_path"}, {"id": "empty", "path": ""}]
        rewritten = absolutize_manifest_paths(rows, tmp_path)
        # Neither row gets a synthesized path; both come through.
        assert len(rewritten) == 2
        assert "path" not in rewritten[0]
        assert rewritten[1].get("path", "") == ""

    def test_does_not_mutate_input_rows(self, tmp_path: Path):
        source = tmp_path / "source"
        source.mkdir()
        (source / "x.txt").write_text("x", encoding="utf-8")
        original = {"id": "a", "path": "x.txt"}
        rows = [original]
        from shard_runner import absolutize_manifest_paths
        _ = absolutize_manifest_paths(rows, source)
        # Caller's input dict is untouched.
        assert original["path"] == "x.txt"


class TestCmdShardAbsolutizesPathsEndToEnd:
    """The shard subcommand's writes to per-shard manifests must
    contain absolute paths so downstream workers see paths
    relative to the original corpus, not the per-shard directory."""

    def test_shard_manifest_has_absolute_paths(self, tmp_path: Path):
        # Build a source manifest with relative paths to text files
        # in a sibling texts/ directory.
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        texts_dir = source_dir / "texts"
        texts_dir.mkdir()
        # 8 small files so we can shard at small shard-count.
        for i in range(8):
            (texts_dir / f"f{i}.md").write_text(
                CLEAN.read_text(), encoding="utf-8",
            )
        source_manifest = source_dir / "manifest.jsonl"
        with source_manifest.open("w", encoding="utf-8") as fh:
            for i in range(8):
                fh.write(json.dumps({
                    "id": f"row_{i}",
                    "path": f"texts/f{i}.md",
                    "register": "personal",
                    "ai_status": "pre_ai_human",
                }) + "\n")

        # Run cmd_shard via the public CLI entry.
        base_dir = tmp_path / "calibration_runs_base"
        import argparse
        import shard_runner
        args = argparse.Namespace(
            base_dir=str(base_dir),
            source_manifest=str(source_manifest),
            run_id="absolutize_smoke",
            shard_size=4,
            shard_count=None,
            stratify="register,ai_status",
            shuffle_seed=42,
            fpr_target=0.01,
            tier1=True, tier2=False, tier3=False,
            embedding_model=None,
            embedding_revision=None,
            force=False,
            task="corpus_hygiene",
            warn_threshold=0.01,
            fail_threshold=0.05,
            strip_rules=None,
            strip_aggressive=False,
        )
        rc = shard_runner.cmd_shard(args)
        assert rc == 0

        # Each shard manifest must have absolute paths so a
        # consumer that resolves paths against the shard
        # manifest's parent (the documented contract) finds the
        # real files. Prior to the fix, paths would have been
        # 'texts/f0.md' interpreted relative to the shard dir,
        # which doesn't contain a 'texts/' subtree.
        shard_root = base_dir / "calibration_runs" / "absolutize_smoke" / "shards"
        shard_manifests = sorted(shard_root.rglob("manifest.jsonl"))
        assert shard_manifests, "no shard manifests written"
        for sm in shard_manifests:
            for line in sm.read_text(
                encoding="utf-8",
            ).splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                assert Path(row["path"]).is_absolute(), (
                    f"shard {sm.parent.name} has non-absolute "
                    f"path: {row['path']!r}"
                )
                # And the file actually exists.
                assert Path(row["path"]).is_file()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
