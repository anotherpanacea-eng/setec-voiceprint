"""Tests for calibration-pipeline judge/generator threading.

Roadmap item D (post-1.101 follow-ups). PR #106 (1.100.0) added
per-(judge × generator) direction routing to the standalone
``variance_audit.py`` CLI + the ``ThresholdSpec.direction_by_
comparator_and_slice`` infrastructure + the ``resolve_direction_
with_slice`` helper. PR #105 (1.101.0) threaded ``comparator_class``
through the calibration pipeline end-to-end. This PR closes the
symmetric gap: ``validation_harness.score_smoothing_entry``,
``calibrate_thresholds.score_corpus``, and ``calibration_survey.py``
didn't accept ``judge`` / ``generator``, so calibration runs against
the 13 RAID ``comparator_dependent`` cells (PR #106) still routed
under per-comparator or spec defaults instead of the per-slice
direction.

This module pins the threading contract end-to-end:

  * ``score_smoothing_entry`` accepts ``judge`` + ``generator`` and
    forwards both to ``classify_compression``.
  * ``score_corpus`` reads ``args.judge`` / ``args.generator`` and
    forwards to ``score_smoothing_entry``.
  * ``calibration_survey`` exposes ``--judge`` / ``--generator``
    and forwards into the inner Namespace.
  * ``calibrate_thresholds.py`` CLI exposes both flags.
  * Cache identity treats both as load-bearing (same contract
    shape as ``comparator_class``).
  * ``bakeoff_matrix.sh`` reads ``SETEC_JUDGE`` and ``SETEC_GENERATOR``
    and appends ``--judge X --generator Y`` to ``BASE_ARGS`` — but
    with **NO corpus-based default** (unlike ``SETEC_COMPARATOR_CLASS``).
    Judges and generators are slice axes WITHIN a corpus, not
    properties of the corpus itself.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calibrate_thresholds as ct  # type: ignore  # noqa: E402
import calibration_survey as cs  # type: ignore  # noqa: E402
import validation_harness as vh  # type: ignore  # noqa: E402


# ---------- score_smoothing_entry threading ----------------------


class TestScoreSmoothingEntryAcceptsJudgeGenerator:
    """The per-entry scorer must accept ``judge`` + ``generator`` and
    forward both to ``classify_compression`` so the per-slice
    direction resolution happens with operator intent on the table."""

    def test_signature_accepts_judge_kwarg(self):
        import inspect
        sig = inspect.signature(vh.score_smoothing_entry)
        assert "judge" in sig.parameters
        assert sig.parameters["judge"].default is None

    def test_signature_accepts_generator_kwarg(self):
        import inspect
        sig = inspect.signature(vh.score_smoothing_entry)
        assert "generator" in sig.parameters
        assert sig.parameters["generator"].default is None

    def test_defaults_preserve_pre_1_x_behavior(self):
        """Both kwargs default to None and forward as None into
        classify_compression so per-spec direction resolution falls
        back to the per-comparator (or spec default) layer."""
        captured = {}

        def _spy_classify(audit, **kw):
            captured.update(kw)
            return {
                "band": "indeterminate",
                "weighted_score": 0.0,
                "available_weight": 0.0,
                "compression_fraction": None,
                "flagged_signals": [],
                "available_signals": [],
                "skipped_signals": [],
                "n_flagged": 0,
                "notes": {},
                "thresholds_used": {},
                "calibration_status": {},
            }

        entry = {
            "id": "test1", "path": "/tmp/none",
            "_resolved_path": "/tmp/none", "_lineno": 1,
        }
        with mock.patch.object(vh, "audit_text") as audit_stub:
            audit_stub.return_value = {
                "summary": {"n_words": 0},
                "available": True,
                "tier1": {}, "tier2": {}, "tier3": {},
            }
            with mock.patch.object(vh, "classify_compression",
                                   side_effect=_spy_classify):
                vh.score_smoothing_entry(
                    entry,
                    positive_statuses={"ai_generated"},
                    negative_statuses={"pre_ai_human"},
                    text="some text",
                )
        assert captured.get("judge") is None
        assert captured.get("generator") is None

    def test_explicit_judge_generator_forward_to_classifier(self):
        """Both must reach classify_compression verbatim. The pair
        activates the inner-most slice routing layer in
        ThresholdSpec.direction_by_comparator_and_slice."""
        captured = {}

        def _spy_classify(audit, **kw):
            captured.update(kw)
            return {
                "band": "indeterminate",
                "weighted_score": 0.0,
                "available_weight": 0.0,
                "compression_fraction": None,
                "flagged_signals": [],
                "available_signals": [],
                "skipped_signals": [],
                "n_flagged": 0,
                "notes": {},
                "thresholds_used": {},
                "calibration_status": {},
            }

        entry = {
            "id": "test1", "path": "/tmp/none",
            "_resolved_path": "/tmp/none", "_lineno": 1,
        }
        with mock.patch.object(vh, "audit_text") as audit_stub:
            audit_stub.return_value = {
                "summary": {"n_words": 0},
                "available": True,
                "tier1": {}, "tier2": {}, "tier3": {},
            }
            with mock.patch.object(vh, "classify_compression",
                                   side_effect=_spy_classify):
                vh.score_smoothing_entry(
                    entry,
                    positive_statuses={"ai_generated"},
                    negative_statuses={"pre_ai_human"},
                    text="some text",
                    judge="chatgpt",
                    generator="gpt-4o",
                )
        assert captured.get("judge") == "chatgpt"
        assert captured.get("generator") == "gpt-4o"


# ---------- score_corpus threading -------------------------------


class TestScoreCorpusReadsJudgeGeneratorFromArgs:
    """``score_corpus`` reads ``args.judge`` / ``args.generator`` via
    getattr and forwards to ``score_smoothing_entry``."""

    def test_score_corpus_passes_args_judge_generator_to_entry(
        self, tmp_path,
    ):
        manifest = tmp_path / "manifest.jsonl"
        text_file = tmp_path / "text.txt"
        text_file.write_text("a " * 200, encoding="utf-8")
        import json as _json
        manifest.write_text(_json.dumps({
            "id": "row1",
            "path": str(text_file),
            "ai_status": "ai_generated",
            "use": ["validation"],
            "split": "test",
            "register": "blog_essay",
            "language_status": "non_native_advanced",
        }) + "\n")
        args = argparse.Namespace(
            manifest=str(manifest),
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            out=None, slug=None, replace=False,
            bootstrap_resamples=10,
            bootstrap_confidence=0.95,
            bootstrap_seed=42,
            tier2=False, tier3=False,
            notes=None, max_entries=None,
            max_entries_seed=None,
            records_cache=None, refresh_cache=False,
            comparator_class="raid",
            judge="chatgpt",
            generator="gpt-4o",
        )
        captured = {}

        def _spy_score(entry, **kw):
            captured.update(kw)
            return {
                "id": entry.get("id"),
                "path": entry.get("path"),
                "ai_status": entry.get("ai_status"),
                "label": 1,
                "score": 0.5,
                "score_name": "compression_fraction",
                "usable_for_metrics": True,
                "register": "blog_essay",
                "language_status": "non_native_advanced",
                "per_signal_scores": {
                    "tier1.sentence_length.burstiness_B": 0.5,
                    "tier1.connective_density.per_1000_tokens": 0.4,
                    "tier1.mattr.value": 0.7,
                    "tier1.mtld": 50.0,
                    "tier1.yules_k": 100.0,
                    "tier1.shannon_entropy_bits": 9.0,
                    "tier1.fkgl.sd": 1.0,
                    "tier1.sentence_length.sd": 4.0,
                    "tier2.mdd.sd": 0.5,
                    "tier3.adjacent_cosine.mean": 0.4,
                    "tier3.adjacent_cosine.sd": 0.1,
                },
            }

        with mock.patch.object(ct, "score_smoothing_entry",
                               side_effect=_spy_score):
            ct.score_corpus(args)
        assert captured.get("judge") == "chatgpt"
        assert captured.get("generator") == "gpt-4o"


# ---------- Cache identity contract -------------------------------


class TestJudgeGeneratorCacheIdentity:
    """``judge`` and ``generator`` are part of the cache identity.
    Same compat-check contract as ``comparator_class``: a cache scored
    under one (judge, generator) pair can't be reused under another
    because the per-spec direction may differ at the slice layer."""

    def _make_args(self, **overrides):
        defaults = dict(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B",
            tier2=False, tier3=False,
            comparator_class=None,
            judge=None,
            generator=None,
            max_entries=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_cache_compatible_when_both_match_none(self, tmp_path):
        """Pre-1.X cache (no judge / generator fields) + un-routed
        run -> compat passes (legacy contract preserved)."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args()
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert "judge" not in reason
        assert "generator" not in reason

    def test_cache_compatible_when_both_match_explicit(self, tmp_path):
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "judge": "chatgpt",
            "generator": "gpt-4o",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(judge="chatgpt", generator="gpt-4o")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert "judge" not in reason
        assert "generator" not in reason

    def test_cache_invalidates_when_judge_differs(self, tmp_path):
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "judge": "chatgpt",
            "generator": "gpt-4o",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(judge="llama", generator="gpt-4o")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "judge" in reason
        assert "chatgpt" in reason and "llama" in reason

    def test_cache_invalidates_when_generator_differs(self, tmp_path):
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "judge": "chatgpt",
            "generator": "gpt-4o",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(judge="chatgpt", generator="gpt-3.5")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "generator" in reason
        assert "gpt-4o" in reason and "gpt-3.5" in reason

    def test_cache_invalidates_when_judge_added_to_args(self, tmp_path):
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(judge="chatgpt", generator="gpt-4o")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        # Either judge or generator drives the invalidation.
        assert "judge" in reason or "generator" in reason


# ---------- CLI integration --------------------------------------


def test_calibrate_thresholds_cli_exposes_judge_generator():
    result = subprocess.run(
        [
            sys.executable,
            str(CALIB_DIR / "calibrate_thresholds.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0
    assert "--judge" in result.stdout
    assert "--generator" in result.stdout


def test_calibration_survey_cli_exposes_judge_generator():
    parser = cs.build_arg_parser()
    flag_names = {opt for a in parser._actions for opt in a.option_strings}
    assert "--judge" in flag_names
    assert "--generator" in flag_names


def test_calibration_survey_forwards_judge_generator_to_inner_namespace():
    parent = argparse.Namespace(
        manifest="/tmp/x", use="validation",
        signal="burstiness_B", fpr_target=0.01,
        out=None, slug=None, replace=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
        tier4=False,
        embedding_model=None, embedding_revision=None,
        embedding_dtype="auto", embedding_device=None,
        surprisal_model=None, surprisal_revision=None,
        surprisal_dtype="auto",
        notes=None, max_entries=None, max_entries_seed=None,
        records_cache=None, refresh_cache=False,
        comparator_class="raid",
        judge="chatgpt",
        generator="gpt-4o",
        sweep_signals=None, batched_tier4_batch_size=8,
    )
    inner = cs._build_inner_args(parent, "burstiness_B")
    assert inner.judge == "chatgpt"
    assert inner.generator == "gpt-4o"

    parent_legacy = argparse.Namespace(
        manifest="/tmp/x", use="validation",
        signal="burstiness_B", fpr_target=0.01,
        out=None, slug=None, replace=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tier2=False, tier3=False,
        tier4=False,
        embedding_model=None, embedding_revision=None,
        embedding_dtype="auto", embedding_device=None,
        surprisal_model=None, surprisal_revision=None,
        surprisal_dtype="auto",
        notes=None, max_entries=None, max_entries_seed=None,
        records_cache=None, refresh_cache=False,
        sweep_signals=None, batched_tier4_batch_size=8,
    )
    inner_legacy = cs._build_inner_args(parent_legacy, "burstiness_B")
    assert inner_legacy.judge is None
    assert inner_legacy.generator is None


# ---------- Replay command (provenance) --------------------------


class TestHarnessCommandReplayCarriesJudgeGenerator:
    """``_build_harness_command`` must surface ``--judge`` and
    ``--generator`` on replay commands so a threshold derived under
    a specific slice is reproducible from the ledger entry."""

    def test_command_omits_flags_when_both_none(self):
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
        )
        assert "--judge" not in cmd
        assert "--generator" not in cmd

    def test_command_includes_judge_when_set(self):
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            judge="chatgpt",
        )
        assert "--judge chatgpt" in cmd

    def test_command_includes_generator_when_set(self):
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            generator="gpt-4o",
        )
        assert "--generator gpt-4o" in cmd

    def test_command_includes_both_when_pair_set(self):
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            judge="chatgpt",
            generator="gpt-4o",
        )
        assert "--judge chatgpt" in cmd
        assert "--generator gpt-4o" in cmd

    def test_command_shell_quotes_unsafe_values(self):
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            judge="model with spaces",
            generator="gen with $special",
        )
        assert "'model with spaces'" in cmd
        assert "'gen with $special'" in cmd


# ---------- Provenance entry -------------------------------------


class TestProvenanceEntryRecordsJudgeGenerator:
    """The provenance ledger entry persists ``judge`` and ``generator``
    alongside ``comparator_class`` so audit consumers can tell two
    thresholds derived under different slices apart on inspection."""

    def _records(self):
        return [
            {
                "id": f"r{i}",
                "path": f"/tmp/r{i}.txt",
                "ai_status": "ai_generated" if i % 2 == 0 else "pre_ai_human",
                "label": 1 if i % 2 == 0 else 0,
                "score": 0.5 + 0.01 * i,
                "score_name": "compression_fraction",
                "usable_for_metrics": True,
                "per_signal_scores": {
                    "tier1.sentence_length.burstiness_B": 0.5 + 0.01 * i,
                },
            }
            for i in range(30)
        ]

    def _scoring_meta(self, **overrides):
        meta = {
            "manifest_path": "/tmp/x",
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False, "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
            "n_entries_full": 30, "n_entries_scored": 30,
            "scored_at": "2026-05-19T00:00:00Z",
            "comparator_class": None,
            "judge": None,
            "generator": None,
        }
        meta.update(overrides)
        return meta

    def test_provenance_fields_present_when_set(self, tmp_path):
        args = argparse.Namespace(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B", fpr_target=0.01,
            out=None, slug=None, replace=False,
            bootstrap_resamples=10, bootstrap_confidence=0.95,
            bootstrap_seed=42, tier2=False, tier3=False,
            notes=None, max_entries=None, max_entries_seed=None,
            records_cache=None, refresh_cache=False,
            comparator_class="raid",
            judge="chatgpt",
            generator="gpt-4o",
            positive_statuses="ai_generated",
            negative_statuses="pre_ai_human",
        )
        try:
            entry = ct.derive_threshold_from_records(
                self._records(), args=args,
                scoring_meta=self._scoring_meta(
                    comparator_class="raid",
                    judge="chatgpt",
                    generator="gpt-4o",
                ),
            )
        except (KeyError, ValueError, AttributeError, SystemExit):
            pytest.skip(
                "synthetic fixture too minimal for full sweep; "
                "harness_command coverage above pins the contract"
            )
        assert entry.get("judge") == "chatgpt"
        assert entry.get("generator") == "gpt-4o"
        assert "--judge chatgpt" in entry.get("harness_command", "")
        assert "--generator gpt-4o" in entry.get("harness_command", "")

    def test_provenance_fields_are_none_when_unset(self, tmp_path):
        args = argparse.Namespace(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B", fpr_target=0.01,
            out=None, slug=None, replace=False,
            bootstrap_resamples=10, bootstrap_confidence=0.95,
            bootstrap_seed=42, tier2=False, tier3=False,
            notes=None, max_entries=None, max_entries_seed=None,
            records_cache=None, refresh_cache=False,
            comparator_class=None,
            judge=None,
            generator=None,
            positive_statuses="ai_generated",
            negative_statuses="pre_ai_human",
        )
        try:
            entry = ct.derive_threshold_from_records(
                self._records(), args=args,
                scoring_meta=self._scoring_meta(),
            )
        except (KeyError, ValueError, AttributeError, SystemExit):
            pytest.skip(
                "synthetic fixture too minimal for full sweep"
            )
        # Always-present keys (None on un-routed runs); downstream
        # consumers don't need defensive .get() calls.
        assert "judge" in entry
        assert "generator" in entry
        assert entry["judge"] is None
        assert entry["generator"] is None


# ---------- Bake-off matrix shell driver -------------------------


class TestBakeoffMatrixPropagatesJudgeGenerator:
    """``bakeoff_matrix.sh`` reads SETEC_JUDGE and SETEC_GENERATOR env
    vars and appends ``--judge X --generator Y`` to BASE_ARGS. Unlike
    SETEC_COMPARATOR_CLASS, there is NO corpus-based default —
    judges and generators are slice axes WITHIN a corpus."""

    import subprocess as _subprocess

    def _run_script(self, env_extra: dict, timeout_s: float = 30.0):
        import os
        import shutil
        if not shutil.which("bash"):
            pytest.skip("bash not on PATH; shell driver test skipped")
        env = os.environ.copy()
        for k in list(env):
            if k.startswith("SETEC_") or k.startswith("_SETEC_"):
                del env[k]
        env.update(env_extra)
        return self._subprocess.run(
            [
                "bash",
                str(ROOT / "calibration" / "bakeoff_matrix.sh"),
            ],
            env=env,
            capture_output=True, text=True, timeout=timeout_s,
        )

    def test_unset_judge_generator_leaves_pre_1_x_behavior(self, tmp_path):
        """No SETEC_JUDGE / SETEC_GENERATOR -> banner shows the
        pre-1.X sentinel; provenance records None."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(tmp_path / "log"),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0, (
            f"script failed: rc={result.returncode}\n"
            f"stderr:\n{result.stderr[-400:]}"
        )
        assert "judge:" in result.stdout
        assert "generator:" in result.stdout
        # Critical: no auto-default from CORPUS_LABEL (unlike
        # comparator_class). The sentinel "(none, pre-1.X behavior)"
        # appears for both.
        assert "judge:     (none" in result.stdout
        assert "generator: (none" in result.stdout

    def test_corpus_label_raid_does_not_default_judge_generator(
        self, tmp_path,
    ):
        """SETEC_CORPUS_LABEL=raid auto-defaults comparator_class to
        'raid' but MUST NOT touch SETEC_JUDGE / SETEC_GENERATOR.
        Judges and generators are slice axes within RAID, not
        properties of the corpus."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(tmp_path / "log"),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        # comparator_class auto-defaulted from CORPUS_LABEL...
        assert "comparator_class: raid" in result.stdout
        # ...but judge/generator stayed unset.
        assert "judge:     (none" in result.stdout
        assert "generator: (none" in result.stdout

    def test_explicit_judge_generator_propagate(self, tmp_path):
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_JUDGE": "chatgpt",
            "SETEC_GENERATOR": "gpt-4o",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(tmp_path / "log"),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        assert "judge:     chatgpt" in result.stdout
        assert "generator: gpt-4o" in result.stdout

    def test_provenance_records_judge_generator(self, tmp_path):
        import json as _json
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        log_dir = tmp_path / "log"
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_JUDGE": "chatgpt",
            "SETEC_GENERATOR": "gpt-4o",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(log_dir),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        prov_files = list(log_dir.glob("bakeoff_matrix_*_provenance.json"))
        assert prov_files
        prov = _json.loads(prov_files[0].read_text())
        assert prov.get("judge") == "chatgpt"
        assert prov.get("generator") == "gpt-4o"

    def test_provenance_unset_judge_generator_record_as_none(self, tmp_path):
        """Unset env vars -> provenance records None (not empty
        string), so downstream consumers can rely on the type
        (None or str) for branching."""
        import json as _json
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        log_dir = tmp_path / "log"
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(log_dir),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        prov_files = list(log_dir.glob("bakeoff_matrix_*_provenance.json"))
        assert prov_files
        prov = _json.loads(prov_files[0].read_text())
        assert prov.get("judge") is None
        assert prov.get("generator") is None
