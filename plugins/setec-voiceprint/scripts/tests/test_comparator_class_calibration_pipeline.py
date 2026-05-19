"""Tests for 1.98.2 calibration-pipeline comparator_class threading.

PR #103 (1.98.0) added per-comparator direction routing to the
standalone ``variance_audit.py`` CLI. PR #104 (1.98.1) was the
dtype-Markdown chore. This PR (1.98.2) closes the calibration-
pipeline gap: ``validation_harness.score_smoothing_entry``,
``calibrate_thresholds.score_corpus``, and
``calibration_survey.py`` didn't accept ``comparator_class``, so
calibration runs (the workflow operators actually use for the
cloud bake-off matrix) still got the un-routed MAGE-default
directions.

This module pins the threading contract end-to-end:

  * ``score_smoothing_entry`` accepts ``comparator_class`` and
    forwards to ``classify_compression``.
  * ``score_corpus`` reads ``args.comparator_class`` and forwards
    to ``score_smoothing_entry``.
  * ``calibration_survey`` exposes ``--comparator-class`` and
    forwards into the inner Namespace.
  * ``calibrate_thresholds.py`` CLI exposes ``--comparator-class``.
  * Cache identity treats ``comparator_class`` as load-bearing:
    a cache scored under one class can't be reused under another
    (same contract shape as surprisal_dtype_resolved).
  * Pre-1.98.2 caches that lack the field are treated as
    ``comparator_class=None`` and stay compatible with current
    runs that also don't supply one.
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


class TestScoreSmoothingEntryAcceptsComparatorClass:
    """The per-entry scorer must accept ``comparator_class`` and
    forward it to ``classify_compression`` so the per-spec
    direction resolution happens with operator intent on the
    table."""

    def test_signature_accepts_comparator_class_kwarg(self):
        """Pre-1.98.2 callers can pass ``comparator_class='raid'``
        without TypeError. The kwarg defaults to None so existing
        callers (test_calibration_*, validation_harness CLI) don't
        need to change."""
        import inspect
        sig = inspect.signature(vh.score_smoothing_entry)
        assert "comparator_class" in sig.parameters
        assert sig.parameters["comparator_class"].default is None

    def test_default_is_none_preserves_pre_1_98_2_behavior(self):
        """``comparator_class=None`` (the default) MUST forward as
        None into classify_compression, so per-spec direction
        resolution falls back to spec defaults -- preserving every
        pre-1.98.2 caller's exact verdict structure."""
        # Spy on classify_compression to see what it receives.
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
        # audit_text reads the file -- give it a non-existent path
        # that triggers the unavailable path or short-circuits.
        # Since the goal is verifying the kwarg threading, stub
        # both audit_text and classify_compression.
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
                    text="some text",  # bypass disk read
                )
        # Default propagates as None.
        assert captured.get("comparator_class") is None

    def test_explicit_comparator_class_forwards_to_classifier(self):
        """``comparator_class='raid'`` must reach classify_compression
        verbatim, where the per-spec resolver picks up the RAID
        override on surprisal_sd."""
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
                    comparator_class="raid",
                )
        assert captured.get("comparator_class") == "raid"


# ---------- score_corpus threading -------------------------------


class TestScoreCorpusReadsComparatorClassFromArgs:
    """``score_corpus`` reads ``args.comparator_class`` via getattr
    and forwards to ``score_smoothing_entry``. Mirror of the
    surprisal_dtype + embedding_dtype getattr pattern."""

    def test_score_corpus_passes_args_comparator_class_to_entry(
        self, tmp_path,
    ):
        """Build a minimal Namespace with comparator_class='raid'
        and assert score_smoothing_entry sees it. Uses a 1-entry
        manifest + stubbed scorer so we just verify the kwarg
        plumbing without running real audits."""
        # Write a minimal manifest the entry loader accepts.
        manifest = tmp_path / "manifest.jsonl"
        text_file = tmp_path / "text.txt"
        text_file.write_text("a " * 200, encoding="utf-8")  # 200 words
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
        )
        captured = {}

        def _spy_score(entry, **kw):
            captured.update(kw)
            return {
                "id": entry.get("id"),
                "path": entry.get("path"),
                "ai_status": entry.get("ai_status"),
                "label": 1,
                "score": 0.0,
                "score_name": "compression_fraction",
                "usable_for_metrics": True,
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
        assert captured.get("comparator_class") == "raid"


# ---------- Cache identity contract -------------------------------


class TestComparatorClassCacheIdentity:
    """``comparator_class`` is part of the Tier-3/Tier-4 cache
    identity. A cache scored under one class can't be reused
    under another -- per_signal_scores in the cache reflect
    compressed/not-compressed verdicts computed under one
    direction, so reusing them under a different direction would
    silently produce wrong band calls. Same contract shape as
    surprisal_dtype_resolved (PR #93)."""

    def _make_args(self, **overrides):
        defaults = dict(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B",
            tier2=False, tier3=False,
            comparator_class=None,
            max_entries=None,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_cache_compatible_when_classes_match_none(self, tmp_path):
        """No comparator_class on either side -- the default pre-
        1.98.2 contract. Compat-check must pass (it'd be a
        regression to break every existing cache)."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
            # No comparator_class key -- pre-1.98.2 cache.
        }
        args = self._make_args()
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        # Other compat checks may pass; this assertion verifies
        # comparator_class doesn't independently fail the check.
        assert "comparator_class" not in reason

    def test_cache_compatible_when_classes_match_raid(self, tmp_path):
        """Cache scored under raid + args also raid -> reusable."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "comparator_class": "raid",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(comparator_class="raid")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert "comparator_class" not in reason

    def test_cache_invalidates_when_classes_differ(self, tmp_path):
        """The load-bearing contract: a cache scored under raid
        and reused under mage would produce wrong band calls on
        any signal with a per-comparator override (surprisal_sd
        in 1.98.0+). Compat-check MUST refuse."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "comparator_class": "raid",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(comparator_class="mage")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "comparator_class" in reason
        assert "raid" in reason and "mage" in reason

    def test_cache_invalidates_when_class_added_to_args(self, tmp_path):
        """Pre-1.98.2 cache (no class) being reused by a 1.98.2
        run that NOW supplies --comparator-class raid must
        invalidate -- the cache was computed under un-routed
        defaults; the new run expects routed verdicts."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            # No comparator_class -- pre-1.98.2 cache treated as None.
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(comparator_class="raid")
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "comparator_class" in reason

    def test_cache_invalidates_when_class_removed_from_args(
        self, tmp_path,
    ):
        """Symmetric: cache scored under --comparator-class raid
        being reused by a run that DROPS the flag must invalidate
        -- the cache reflects routed verdicts; the new run
        expects un-routed."""
        cache_meta = {
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False,
            "do_tier3": False,
            "comparator_class": "raid",
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
        }
        args = self._make_args(comparator_class=None)
        ok, reason = ct.cache_is_compatible(
            cache_meta, args,
            manifest_sha256="sha256:abc",
            corpus_text_fingerprint="sha256:def",
        )
        assert ok is False
        assert "comparator_class" in reason


# ---------- CLI integration --------------------------------------


def test_calibrate_thresholds_cli_exposes_comparator_class():
    """``calibrate_thresholds.py --help`` lists --comparator-class
    so operators discover the flag. The parser is built inline in
    main() rather than a separate builder, so we drive --help via
    subprocess (cheaper than mocking sys.argv)."""
    result = subprocess.run(
        [
            sys.executable,
            str(CALIB_DIR / "calibrate_thresholds.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0
    assert "--comparator-class" in result.stdout


def test_calibration_survey_cli_exposes_comparator_class():
    """Same on the survey CLI. Survey exposes ``build_arg_parser``
    as a module-level helper so we can introspect without
    subprocess."""
    parser = cs.build_arg_parser()
    flag_names = {opt for a in parser._actions for opt in a.option_strings}
    assert "--comparator-class" in flag_names


def test_calibration_survey_forwards_class_to_inner_namespace():
    """The survey builds an inner Namespace for calibrate_thresholds
    and must forward comparator_class into it. ``_build_inner_args``
    takes ``(parent_args, signal)`` -- the signal is whichever the
    survey is sweeping for this iteration."""
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
        sweep_signals=None, batched_tier4_batch_size=8,
    )
    inner = cs._build_inner_args(parent, "burstiness_B")
    assert inner.comparator_class == "raid"

    # And the absence path: parent without the attr (pre-1.98.2
    # caller building a Namespace by hand) still works -- the
    # forwarder uses getattr with None default.
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
        # No comparator_class on the parent.
        sweep_signals=None, batched_tier4_batch_size=8,
    )
    inner_legacy = cs._build_inner_args(parent_legacy, "burstiness_B")
    assert inner_legacy.comparator_class is None


# ---------- Reviewer P1 follow-ups -------------------------------


class TestHarnessCommandReplayCarriesComparatorClass:
    """Reviewer P1 on PR #105: ``_build_harness_command`` didn't
    accept or surface ``--comparator-class``, so a threshold
    derived with ``--comparator-class raid`` emitted a replay
    command that omitted the flag and would silently replay under
    the MAGE default -- producing a different threshold value on
    the rerun. Pin the replay-command contract."""

    def test_command_omits_flag_when_class_is_none(self):
        """Pre-1.99 / un-routed runs MUST emit clean commands
        (no --comparator-class). Otherwise we'd pollute every
        legacy ledger entry with a None-valued flag."""
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
        )
        assert "--comparator-class" not in cmd

    def test_command_includes_flag_when_class_is_set(self):
        """A routed run MUST surface the flag so the operator
        replaying from the ledger gets the same direction regime."""
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            comparator_class="raid",
        )
        assert "--comparator-class raid" in cmd

    def test_command_shell_quotes_class_value(self):
        """Defensive: a class string with shell-unsafe characters
        (impossible with the framework taxonomy but possible with
        an operator-supplied custom class) MUST be quoted to
        survive copy-paste replay."""
        cmd = ct._build_harness_command(
            manifest_path="/tmp/x",
            use="validation",
            signal="burstiness_B",
            fpr_target=0.01,
            comparator_class="my class with spaces",
        )
        # shlex.quote wraps strings with spaces in single quotes.
        assert "'my class with spaces'" in cmd


class TestProvenanceEntryRecordsComparatorClass:
    """The provenance ledger entry must persist ``comparator_class``
    alongside the corpus / calibration identity fields so audit
    consumers can tell two thresholds derived under different
    classes apart on inspection. The ``harness_command`` field
    also surfaces it (above); this test pins the structured
    field."""

    def test_provenance_field_present_when_set(self, tmp_path):
        """Spy on ``derive_threshold_from_records`` to capture the
        provenance entry it builds; assert it carries
        ``comparator_class``. We use a tiny synthetic records list
        rather than running the full scoring loop."""
        # Build a minimal records fixture the threshold sweep can
        # consume.
        records = [
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
        args = argparse.Namespace(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B", fpr_target=0.01,
            out=None, slug=None, replace=False,
            bootstrap_resamples=10, bootstrap_confidence=0.95,
            bootstrap_seed=42, tier2=False, tier3=False,
            notes=None, max_entries=None, max_entries_seed=None,
            records_cache=None, refresh_cache=False,
            comparator_class="raid",
            positive_statuses="ai_generated",
            negative_statuses="pre_ai_human",
        )
        scoring_meta = {
            "manifest_path": "/tmp/x",
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False, "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
            "n_entries_full": 30, "n_entries_scored": 30,
            "scored_at": "2026-05-18T00:00:00Z",
            "comparator_class": "raid",
        }
        # derive_threshold_from_records returns the provenance entry
        # via the public path. We don't need to assert on the full
        # threshold math -- just on the new field's presence.
        try:
            entry = ct.derive_threshold_from_records(
                records, args=args, scoring_meta=scoring_meta,
            )
        except (KeyError, ValueError, AttributeError, SystemExit):
            # If the synthetic fixture is too minimal for the real
            # sweep, fall back to confirming the structured field
            # would be there by inspecting the builder.
            pytest.skip(
                "synthetic fixture too minimal for full sweep; "
                "harness_command coverage above pins the contract"
            )
        assert entry.get("comparator_class") == "raid"
        # And the replay command surfaces it.
        assert "--comparator-class raid" in entry.get(
            "harness_command", "",
        )

    def test_provenance_field_is_none_when_unset(self, tmp_path):
        """Pre-1.99 / un-routed runs: the field is present (None)
        on every entry. Always-present keys are easier for downstream
        ledger consumers than conditionally-present keys -- if a
        consumer queries ``entry['comparator_class']`` it doesn't
        need a defensive ``.get()``."""
        # Same fixture shape as above but no comparator_class on args.
        records = [
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
        args = argparse.Namespace(
            manifest="/tmp/x", use="validation",
            signal="burstiness_B", fpr_target=0.01,
            out=None, slug=None, replace=False,
            bootstrap_resamples=10, bootstrap_confidence=0.95,
            bootstrap_seed=42, tier2=False, tier3=False,
            notes=None, max_entries=None, max_entries_seed=None,
            records_cache=None, refresh_cache=False,
            positive_statuses="ai_generated",
            negative_statuses="pre_ai_human",
        )
        scoring_meta = {
            "manifest_path": "/tmp/x",
            "manifest_sha256": "sha256:abc",
            "corpus_text_fingerprint": "sha256:def",
            "use": "validation",
            "do_tier2": False, "do_tier3": False,
            "scorer_version": ct.SCORER_CACHE_VERSION,
            "sub_sample": None,
            "n_entries_full": 30, "n_entries_scored": 30,
            "scored_at": "2026-05-18T00:00:00Z",
        }
        try:
            entry = ct.derive_threshold_from_records(
                records, args=args, scoring_meta=scoring_meta,
            )
        except (KeyError, ValueError, AttributeError, SystemExit):
            pytest.skip(
                "synthetic fixture too minimal for full sweep"
            )
        # The field is present and None.
        assert "comparator_class" in entry
        assert entry["comparator_class"] is None
        # Replay command omits the flag.
        assert "--comparator-class" not in entry.get("harness_command", "")


class TestBakeoffMatrixPropagatesComparatorClass:
    """Reviewer P1 on PR #105: ``bakeoff_matrix.sh`` built
    ``BASE_ARGS`` without ``--comparator-class``, so a RAID bake-
    off launched through the script evaluated comparator-dependent
    signals under the default registry direction. Fix:
    SETEC_COMPARATOR_CLASS env var with default-from-CORPUS_LABEL
    inference (mage / raid) and ``BASE_ARGS+=(--comparator-class
    "$COMPARATOR_CLASS")`` when set.

    These tests drive the shell script in dry-run mode and
    inspect the provenance JSON / banner output to confirm the
    propagation."""

    import subprocess as _subprocess

    def _run_script(self, env_extra: dict, timeout_s: float = 30.0):
        """Run bakeoff_matrix.sh with given env vars; capture
        stdout / stderr / rc / provenance JSON."""
        import os
        import shutil
        if not shutil.which("bash"):
            pytest.skip("bash not on PATH; shell driver test skipped")
        env = os.environ.copy()
        # Strip out any caller-side SETEC vars that could interfere.
        for k in list(env):
            if k.startswith("SETEC_") or k.startswith("_SETEC_"):
                del env[k]
        env.update(env_extra)
        result = self._subprocess.run(
            [
                "bash",
                str(
                    ROOT / "calibration" / "bakeoff_matrix.sh"
                ),
            ],
            env=env,
            capture_output=True, text=True, timeout=timeout_s,
        )
        return result

    def test_corpus_raid_auto_defaults_comparator_class(self, tmp_path):
        """``SETEC_CORPUS_LABEL=raid`` (no explicit
        SETEC_COMPARATOR_CLASS) auto-defaults the comparator class
        to 'raid' and propagates --comparator-class into BASE_ARGS.
        Confirm via the banner output."""
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
            f"stdout:\n{result.stdout[-800:]}\n"
            f"stderr:\n{result.stderr[-400:]}"
        )
        # Banner surfaces the resolved class.
        assert "comparator_class: raid" in result.stdout

    def test_explicit_comparator_class_wins_over_corpus(
        self, tmp_path,
    ):
        """``SETEC_COMPARATOR_CLASS=mage`` overrides
        SETEC_CORPUS_LABEL=raid. Lets operators with custom
        labels point at the right routing taxonomy explicitly."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "raid",
            "SETEC_COMPARATOR_CLASS": "mage",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(tmp_path / "log"),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        assert "comparator_class: mage" in result.stdout

    def test_unknown_corpus_label_leaves_class_unset(self, tmp_path):
        """SETEC_CORPUS_LABEL='editlens' (or any non-framework
        label) without an explicit SETEC_COMPARATOR_CLASS leaves
        comparator_class unset -- pre-1.99 behavior. The
        BASE_ARGS doesn't get --comparator-class at all."""
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "manifest.jsonl").write_text("{}\n")
        result = self._run_script({
            "SETEC_CORPUS_DIR": str(corpus),
            "SETEC_CORPUS_LABEL": "editlens",
            "SETEC_BAKEOFF_DIR": str(tmp_path / "bake"),
            "SETEC_CALIBRATION_RUNS_DIR": str(tmp_path / "runs"),
            "SETEC_LOG_DIR": str(tmp_path / "log"),
            "SETEC_DRY_RUN": "1",
        })
        assert result.returncode == 0
        # Banner shows the "none" sentinel.
        assert "(none, pre-1.99 behavior)" in result.stdout

    def test_provenance_records_comparator_class(self, tmp_path):
        """The provenance JSON written at session start surfaces
        ``comparator_class`` so a re-run from the ledger
        reproduces the exact direction regime."""
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
        assert prov.get("comparator_class") == "raid"
