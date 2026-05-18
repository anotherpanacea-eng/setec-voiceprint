#!/usr/bin/env python3
"""Regression tests for the batched-Tier-4 caller wiring in
``calibrate_thresholds.score_corpus`` (1.90.0+).

Pins three contracts:

  * When ``--tier4`` is on and a SurprisalBackend can be imported,
    the per-entry scoring loop pre-batches texts and calls
    ``backend.score_texts(texts, batch_size=...)`` once per chunk —
    NOT ``backend.score_text(text)`` per entry. This is the
    load-bearing speedup the wiring exists to deliver.
  * When ``--tier4`` is on but the SurprisalBackend module cannot
    be imported (no transformers / torch on the host), the loop
    falls through to the legacy per-entry path bit-exactly. No
    crash, no behavior change for operators on a no-Tier-4 install.
  * The ``--surprisal-batch-size`` CLI flag propagates from the
    operator's invocation through to the ``score_texts`` call.

The tests stub ``audit_text`` to a no-op that records its kwargs,
so the bookkeeping doesn't depend on real Tier 1 / Tier 2 / Tier 3
compute — only the batched-Tier-4 wiring contract is exercised.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import calibrate_thresholds as ct  # type: ignore


# ------------------- Helpers ----------------------------------------


class _RecordingFakeBackend:
    """Stand-in for SurprisalBackend that records every score_text /
    score_texts invocation so the test can pin the batching shape.

    Returns synthetic per-token surprisal series whose length matches
    a heuristic of (len(text) // 5 + 1) tokens, which is enough for
    audit_surprisal's downstream stats to compute on the legacy path
    but doesn't have to match real-LM tokenization (the test stubs
    audit_text to a no-op anyway)."""

    def __init__(self, *args, **kwargs):
        self.score_text_calls: list[tuple[str, int]] = []
        self.score_texts_calls: list[tuple[list[str], int]] = []

    def score_text(self, text, return_top_k=0):
        self.score_text_calls.append((text, return_top_k))
        n = max(1, len(text) // 5)
        series = [1.0] * (n - 1) if n > 1 else []
        if return_top_k > 0:
            return series, []
        return series

    def score_texts(self, texts, *, batch_size=8):
        self.score_texts_calls.append((list(texts), batch_size))
        out = []
        for t in texts:
            n = max(1, len(t) // 5)
            out.append([1.0] * (n - 1) if n > 1 else [])
        return out

    def identifier_block(self):
        return {
            "id": "fake/recording-backend",
            "revision": None,
            "alias": "fake",
            "deterministic_mode": True,
            "method": "transformers-causal-lm",
        }


def _fake_entries_with_files(tmp_path: Path, n_pos: int, n_neg: int):
    """Create fake manifest entries pointing at real text files in
    tmp_path so the per-entry text-read in score_smoothing_entry
    succeeds (the function reads the file even when audit_text is
    stubbed)."""
    entries = []
    for i in range(n_pos):
        p = tmp_path / f"pos_{i}.txt"
        p.write_text(
            f"This is a positive example number {i}. " * 20,
            encoding="utf-8",
        )
        entries.append({
            "id": f"pos_{i}",
            "path": str(p),
            "_resolved_path": str(p),
            "_lineno": i + 1,
            "ai_status": "ai_generated",
            "use": ["validation"],
            "split": "test",
            "language_status": "non_native_advanced",
        })
    for i in range(n_neg):
        p = tmp_path / f"neg_{i}.txt"
        p.write_text(
            f"This is a negative example number {i}. " * 20,
            encoding="utf-8",
        )
        entries.append({
            "id": f"neg_{i}",
            "path": str(p),
            "_resolved_path": str(p),
            "_lineno": n_pos + i + 1,
            "ai_status": "pre_ai_human",
            "use": ["validation"],
            "split": "test",
            "language_status": "non_native_advanced",
        })
    return entries


def _make_args(**overrides) -> argparse.Namespace:
    base = dict(
        manifest="dummy.jsonl",
        use="validation",
        signal="burstiness_B",
        fpr_target=0.01,
        out=None,
        slug=None,
        replace=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tier2=False,
        tier3=False,
        tier4=False,
        notes=None,
        max_entries=None,
        max_entries_seed=None,
        records_cache=None,
        refresh_cache=False,
        embedding_model=None,
        embedding_revision=None,
        surprisal_model=None,
        surprisal_revision=None,
        surprisal_batch_size=8,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _stub_audit_text(text, **kwargs):
    """Stub the entire audit_text function so the test doesn't depend
    on real Tier 1/2/3 compute. Records the tier4_score_fn kwarg so
    the test can pin that batched-mode entries get a precomputed
    scorer injected. Returns the minimum legal audit shape that
    score_smoothing_entry's downstream code accepts."""
    _stub_audit_text.calls.append(kwargs)
    return {
        "preprocessing": {"applied": False},
        "summary": {
            "n_words": 50,
            "n_sentences": 5,
            "reliable": True,
            "preprocessing_applied": False,
        },
        "tier1": {
            "sentence_length": {
                "burstiness_B": 0.0,
                "sd": 0.0,
                "mean": 10.0,
            },
            "mattr": {"window": 50, "value": 0.5},
            "mtld": {"score": 50.0},
            "yules_k": {"score": 100.0},
            "shannon_entropy_bits": {"score": 5.0},
            "fkgl": {"sd": 1.0, "mean": 8.0},
            "connective_density": {"score": 0.05},
            "function_words": {"score": 0.5},
        },
    }


_stub_audit_text.calls = []  # type: ignore[attr-defined]


# ------------------- Tests ------------------------------------------


def test_batched_surprisal_calls_score_texts_once_per_chunk(
    tmp_path: Path,
):
    """With --tier4 on and 5 entries at --surprisal-batch-size 2,
    the backend should see score_texts called for chunks (2, 2, 1)
    = 3 invocations total, NOT score_text called 5 times."""
    entries = _fake_entries_with_files(tmp_path, n_pos=3, n_neg=2)
    args = _make_args(tier4=True, surprisal_batch_size=2)

    fake_backend_instance = _RecordingFakeBackend()
    fake_module = mock.MagicMock()
    fake_module.SurprisalBackend = mock.MagicMock(
        return_value=fake_backend_instance,
    )
    fake_module.resolve_model_arg = lambda x: x or "tinyllama"
    _stub_audit_text.calls.clear()

    with mock.patch.dict(sys.modules, {"surprisal_backend": fake_module}):
        with mock.patch.object(
            ct, "validate_manifest",
            return_value={"n_errors": 0},
        ):
            with mock.patch.object(
                ct, "load_manifest_entries", return_value=entries,
            ):
                with mock.patch.object(
                    ct, "_manifest_content_hash",
                    return_value="sha256:test",
                ):
                    with mock.patch.object(
                        ct, "_corpus_text_fingerprint",
                        return_value="fp:test",
                    ):
                        with mock.patch(
                            "validation_harness.audit_text",
                            side_effect=_stub_audit_text,
                        ):
                            records, meta = ct.score_corpus(args)

    assert len(records) == 5
    # The critical assertion: batched path drove the calls.
    assert len(fake_backend_instance.score_texts_calls) == 3, (
        f"Expected 3 batched calls for 5 entries at batch_size=2; "
        f"got {len(fake_backend_instance.score_texts_calls)}"
    )
    assert len(fake_backend_instance.score_text_calls) == 0, (
        "score_text (single-text path) must NOT be called when the "
        "batched path is active; it was called "
        f"{len(fake_backend_instance.score_text_calls)} times."
    )
    # Each chunk should respect the batch_size argument.
    for chunk_texts, bs in fake_backend_instance.score_texts_calls:
        assert bs == 2
        assert len(chunk_texts) <= 2


def test_batched_surprisal_injects_tier4_score_fn_into_audit_text(
    tmp_path: Path,
):
    """Each batched entry's audit_text call should receive a
    non-None tier4_score_fn kwarg, which is how the precomputed
    surprisal series flows through to audit_surprisal without
    triggering the per-row backend call."""
    entries = _fake_entries_with_files(tmp_path, n_pos=2, n_neg=2)
    args = _make_args(tier4=True, surprisal_batch_size=4)

    fake_backend_instance = _RecordingFakeBackend()
    fake_module = mock.MagicMock()
    fake_module.SurprisalBackend = mock.MagicMock(
        return_value=fake_backend_instance,
    )
    fake_module.resolve_model_arg = lambda x: x or "tinyllama"
    _stub_audit_text.calls.clear()

    with mock.patch.dict(sys.modules, {"surprisal_backend": fake_module}):
        with mock.patch.object(
            ct, "validate_manifest",
            return_value={"n_errors": 0},
        ):
            with mock.patch.object(
                ct, "load_manifest_entries", return_value=entries,
            ):
                with mock.patch.object(
                    ct, "_manifest_content_hash",
                    return_value="sha256:test",
                ):
                    with mock.patch.object(
                        ct, "_corpus_text_fingerprint",
                        return_value="fp:test",
                    ):
                        with mock.patch(
                            "validation_harness.audit_text",
                            side_effect=_stub_audit_text,
                        ):
                            ct.score_corpus(args)

    assert len(_stub_audit_text.calls) == 4
    for call_kwargs in _stub_audit_text.calls:
        assert call_kwargs.get("do_tier4") is True
        assert call_kwargs.get("tier4_score_fn") is not None, (
            "Each batched entry must receive a non-None "
            "tier4_score_fn kwarg so audit_surprisal uses the "
            "precomputed series instead of constructing a per-row "
            "backend."
        )


def test_no_backend_falls_through_to_legacy_per_entry_path(
    tmp_path: Path,
):
    """When the SurprisalBackend module cannot be imported (no
    transformers / torch on the host), the batched scoring is
    skipped and the legacy per-entry path runs unchanged. Pinning
    this guards against introducing a hard dependency on the
    optional Tier-4 stack."""
    entries = _fake_entries_with_files(tmp_path, n_pos=2, n_neg=2)
    args = _make_args(tier4=True, surprisal_batch_size=2)
    _stub_audit_text.calls.clear()

    # Force the ``from surprisal_backend import ...`` inside
    # score_corpus to fail by replacing the module with one that
    # raises on attribute access. The fall-through path leaves
    # batched_surprisal_backend = None and the per-entry loop runs.
    failing_module = mock.MagicMock()
    failing_module.SurprisalBackend = mock.MagicMock(
        side_effect=ImportError("simulated missing transformers"),
    )
    with mock.patch.dict(sys.modules, {"surprisal_backend": failing_module}):
        with mock.patch.object(
            ct, "validate_manifest",
            return_value={"n_errors": 0},
        ):
            with mock.patch.object(
                ct, "load_manifest_entries", return_value=entries,
            ):
                with mock.patch.object(
                    ct, "_manifest_content_hash",
                    return_value="sha256:test",
                ):
                    with mock.patch.object(
                        ct, "_corpus_text_fingerprint",
                        return_value="fp:test",
                    ):
                        with mock.patch(
                            "validation_harness.audit_text",
                            side_effect=_stub_audit_text,
                        ):
                            records, _meta = ct.score_corpus(args)

    assert len(records) == 4
    # Per-entry path: no tier4_score_fn injected; the audit's own
    # Tier-4 block would construct a per-row backend (which would
    # then also fail to import in this test, but that failure is
    # audit_text's to handle — score_corpus's job is only to not
    # crash before getting there).
    for call_kwargs in _stub_audit_text.calls:
        assert call_kwargs.get("tier4_score_fn") is None


def test_tier4_disabled_skips_batched_scoring_entirely(
    tmp_path: Path,
):
    """With --tier4 off the batched scoring is skipped regardless
    of backend availability. The per-entry loop runs the legacy
    path bit-exactly — same as pre-1.90 behavior."""
    entries = _fake_entries_with_files(tmp_path, n_pos=2, n_neg=2)
    args = _make_args(tier4=False, surprisal_batch_size=2)
    _stub_audit_text.calls.clear()

    fake_backend_instance = _RecordingFakeBackend()
    fake_module = mock.MagicMock()
    fake_module.SurprisalBackend = mock.MagicMock(
        return_value=fake_backend_instance,
    )
    fake_module.resolve_model_arg = lambda x: x or "tinyllama"

    with mock.patch.dict(sys.modules, {"surprisal_backend": fake_module}):
        with mock.patch.object(
            ct, "validate_manifest",
            return_value={"n_errors": 0},
        ):
            with mock.patch.object(
                ct, "load_manifest_entries", return_value=entries,
            ):
                with mock.patch.object(
                    ct, "_manifest_content_hash",
                    return_value="sha256:test",
                ):
                    with mock.patch.object(
                        ct, "_corpus_text_fingerprint",
                        return_value="fp:test",
                    ):
                        with mock.patch(
                            "validation_harness.audit_text",
                            side_effect=_stub_audit_text,
                        ):
                            ct.score_corpus(args)

    # Tier 4 off: never call the backend at all.
    assert len(fake_backend_instance.score_text_calls) == 0
    assert len(fake_backend_instance.score_texts_calls) == 0
    for call_kwargs in _stub_audit_text.calls:
        assert call_kwargs.get("tier4_score_fn") is None
        assert call_kwargs.get("do_tier4") is False


def test_surprisal_batch_size_flag_is_exposed_on_calibration_survey_cli():
    """The --surprisal-batch-size flag must be a real option on
    calibration_survey's parser. Direct introspection via
    build_arg_parser() — no help-text capture, no None-guards that
    silently skip the assertion."""
    import calibration_survey as cs  # type: ignore
    parser = cs.build_arg_parser()
    option_strings = {
        s
        for action in parser._actions
        for s in (action.option_strings or [])
    }
    assert "--surprisal-batch-size" in option_strings, (
        "calibration_survey.build_arg_parser() must register "
        "--surprisal-batch-size; flag not found in option_strings: "
        f"{sorted(option_strings)}"
    )


def test_surprisal_batch_size_flag_is_exposed_on_calibrate_thresholds_cli(
    capsys,
):
    """The --surprisal-batch-size flag must also be exposed on
    calibrate_thresholds' CLI. The parser is built inline inside
    main() so we exercise it via ``main(['--help'])`` and capture
    the stdout to assert the flag string appears there.

    main() raises SystemExit(0) on --help; the test catches and
    inspects capsys.readouterr().out."""
    try:
        ct.main(["--help"])
    except SystemExit as exc:
        # --help exits with 0; anything else is a real failure.
        assert exc.code == 0, f"Unexpected exit code: {exc.code}"
    captured = capsys.readouterr()
    assert "--surprisal-batch-size" in captured.out, (
        "calibrate_thresholds --help output must mention "
        "--surprisal-batch-size; not found. Captured help text "
        "(truncated to 500 chars): "
        f"{captured.out[:500]!r}"
    )


def test_batched_surprisal_disables_after_first_chunk_failure(
    tmp_path: Path,
):
    """Reviewer P2 on #90: a single batched ``score_texts`` failure
    used to retry overlapping batches per remaining row, producing
    O(N) failed forward passes and matching log spam. The latch
    flips on the first failure; subsequent rows skip batched mode
    entirely and fall through to the legacy per-entry path.

    Set up: 6 entries at batch_size=2, with a backend whose
    ``score_texts`` raises unconditionally. Without the latch we'd
    see 5 batched calls (one per i in 0..5 since the cache stays
    empty); with the latch, exactly 1."""
    entries = _fake_entries_with_files(tmp_path, n_pos=3, n_neg=3)
    args = _make_args(tier4=True, surprisal_batch_size=2)

    class _FailingBatchedBackend(_RecordingFakeBackend):
        def score_texts(self, texts, *, batch_size=8):
            self.score_texts_calls.append((list(texts), batch_size))
            raise RuntimeError("CUDA out of memory (simulated)")

    fake_backend_instance = _FailingBatchedBackend()
    fake_module = mock.MagicMock()
    fake_module.SurprisalBackend = mock.MagicMock(
        return_value=fake_backend_instance,
    )
    fake_module.resolve_model_arg = lambda x: x or "tinyllama"
    _stub_audit_text.calls.clear()

    with mock.patch.dict(sys.modules, {"surprisal_backend": fake_module}):
        with mock.patch.object(
            ct, "validate_manifest",
            return_value={"n_errors": 0},
        ):
            with mock.patch.object(
                ct, "load_manifest_entries", return_value=entries,
            ):
                with mock.patch.object(
                    ct, "_manifest_content_hash",
                    return_value="sha256:test",
                ):
                    with mock.patch.object(
                        ct, "_corpus_text_fingerprint",
                        return_value="fp:test",
                    ):
                        with mock.patch(
                            "validation_harness.audit_text",
                            side_effect=_stub_audit_text,
                        ):
                            records, _meta = ct.score_corpus(args)

    assert len(records) == 6
    # The critical assertion: exactly one batched call, not one
    # per remaining row.
    assert len(fake_backend_instance.score_texts_calls) == 1, (
        f"Expected 1 batched call after the latch fires; got "
        f"{len(fake_backend_instance.score_texts_calls)}. "
        "The disable-on-first-failure latch is not engaging — "
        "operators on rented GPU hours would see O(N) wasted "
        "forward passes plus matching log spam."
    )
    # After the latch fires, every subsequent row runs the legacy
    # per-entry path (tier4_score_fn=None).
    assert all(
        c.get("tier4_score_fn") is None
        for c in _stub_audit_text.calls
    ), (
        "Once batched mode is disabled, every row should see "
        "tier4_score_fn=None and fall through to the per-entry "
        "Tier-4 path."
    )
