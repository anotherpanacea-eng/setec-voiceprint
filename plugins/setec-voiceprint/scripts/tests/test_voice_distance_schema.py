#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on voice_distance.

Wave 4 of the output-schema unification track. voice_distance compares
a target text against a baseline; both target and baseline are
populated. Function-call consumers (callers of compare_to_baseline)
see no change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_distance as vd  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

CLAIM_LICENSE_KEYS = frozenset({
    "task_surface", "licenses", "does_not_license", "comparison_set",
    "length_range_words", "register_match", "language_match",
    "fpr_target", "confidence_interval_95", "additional_caveats",
    "references",
})


def _fake_result() -> dict:
    """Mirror compare_to_baseline's return shape with the minimum
    fields build_audit_payload reads. Avoids the spaCy + stylometric
    feature load needed for a real run.
    """
    return {
        "task_surface": "voice_coherence",
        "target_summary": {
            "n_words": 3200,
            "n_sentences": 180,
        },
        "baseline_summary": {
            "n_files": 8,
            "total_words": 22000,
            "mean_words": 2750,
            "min_words": 1200,
            "max_words": 5400,
        },
        "overall": {
            "weighted_delta": 1.4,
            "band": "Moderate drift",
        },
        "families": {
            "function_words": {
                "delta_normalized": 1.2,
                "top_features": [
                    {"feature": "the", "z": 0.5},
                ],
            },
            "char_ngrams_3": {
                "delta_normalized": 1.5,
                "top_features": [
                    {"feature": "th_", "z": 1.0},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def envelope():
    return vd.build_audit_payload(
        _fake_result(), target_path=Path("draft.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "voice_distance"
        assert envelope["version"] == vd.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_from_target_summary(self, envelope):
        assert envelope["target"]["words"] == 3200

    def test_target_carries_n_sentences(self, envelope):
        assert envelope["target"]["n_sentences"] == 180

    def test_baseline_n_files_and_words(self, envelope):
        assert envelope["baseline"]["n_files"] == 8
        assert envelope["baseline"]["words"] == 22000

    def test_baseline_carries_mean_min_max(self, envelope):
        assert envelope["baseline"]["mean_words"] == 2750
        assert envelope["baseline"]["min_words"] == 1200
        assert envelope["baseline"]["max_words"] == 5400


class TestResultsPayload:
    def test_results_carries_overall_and_families(self, envelope):
        r = envelope["results"]
        assert "overall" in r
        assert "families" in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target_summary", "baseline_summary",
            "overall", "families", "preprocessing",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, envelope):
        assert (
            envelope["claim_license"]["task_surface"]
            == envelope["task_surface"]
        )

    def test_comparison_set_carries_distance_summary(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["band"] == "Moderate drift"
        assert cs["weighted_delta"] == 1.4
        assert cs["n_baseline_files"] == 8

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestOptionalBlocks:
    def test_register_match_under_results(self):
        result = _fake_result()
        result["register_match"] = {
            "target_classification": {
                "primary": "literary_fiction",
                "confidence": 0.8,
            },
            "match": {"verdict": "match"},
        }
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "register_match" in envelope["results"]

    def test_length_matched_bootstrap_under_results(self):
        result = _fake_result()
        result["length_matched_bootstrap"] = {
            "available": True,
            "percentile": 0.65,
        }
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "length_matched_bootstrap" in envelope["results"]


class TestWarningsPropagate:
    def test_warnings_forwarded(self):
        result = _fake_result()
        result["warnings"] = ["Small baseline: <20K words."]
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert envelope["warnings"] == ["Small baseline: <20K words."]


# ---------------------------------------------------------------------------
# AC10 (neurobiber-v2): biber_features opt-in is OFF by default; the existing
# compare_to_baseline consumer path produces no 'biber_features' key anywhere
# in the results envelope when --include-biber is not passed.
# Recursive key walk (NOT substring) — mirrors the _walk_keys / _FORBIDDEN_KEYS
# pattern from test_dependency_distance_audit.py:151-159.
# ---------------------------------------------------------------------------

def _walk_keys_vd(obj):
    """Yield every dict key reachable in a nested payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys_vd(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys_vd(item)


class TestBiberFeaturesAbsentByDefault:
    """AC10: biber_features does not appear in the default voice_distance envelope.

    This protects the downstream drift gate for apodictic and setec-voicewright:
    their pinned contract fixtures are against the default (no --include-biber)
    output, which must be byte-identical before and after this change.
    """

    def test_biber_features_absent_from_default_envelope(self):
        """'biber_features' not in any key of the default build_audit_payload envelope."""
        envelope = vd.build_audit_payload(
            _fake_result(), target_path=Path("draft.md"),
        )
        keys = set(_walk_keys_vd(envelope["results"]))
        assert "biber_features" not in keys, (
            "'biber_features' key found in default voice_distance results envelope "
            "(include_biber defaults to False — this key must NOT appear without --include-biber)"
        )


# ---------------------------------------------------------------------------
# Codex P1 regression: voice_distance --include-biber must emit a clean
# missing_dependency envelope (available:false) rather than crashing with
# ValueError when no real Biber tagger is configured (always the case in
# the M1 build — there is no real tagger yet).
# Ref: Codex P1 finding on voice_distance.py:754
# ---------------------------------------------------------------------------

def _run_vd_main(argv: list[str]) -> int:
    """Invoke vd.main() with a patched sys.argv."""
    import sys as _sys
    orig = _sys.argv
    _sys.argv = argv
    try:
        return vd.main()
    finally:
        _sys.argv = orig


class TestIncludeBiberMissingDependencyCLI:
    """Codex P1: voice_distance --include-biber with no tagger must NOT crash.

    Pre-fix: compare_to_baseline raises ValueError (include_biber requires
    biber_vector or biber_tagger) and the script exits unclean with a traceback.
    Post-fix: the CLI intercepts the missing-tagger condition BEFORE calling
    compare_to_baseline and emits available:false / reason_category=missing_dependency.
    """

    def test_include_biber_no_tagger_emits_missing_dependency(
        self, tmp_path, capsys
    ):
        """--include-biber with no M2 tagger → available:false, missing_dependency."""
        import json as _json

        # Build a minimal two-file baseline so load_entries succeeds.
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        target = tmp_path / "target.md"
        target.write_text(
            "Officials noted that the process followed established guidelines. " * 10,
            encoding="utf-8",
        )

        rc = _run_vd_main([
            "voice_distance.py",
            str(target),
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
        ])

        captured = capsys.readouterr()
        # Must not crash (no uncaught ValueError → non-zero rc from exception)
        # The error envelope exits with EXIT_CONTRACT (3 per setec_run convention)
        # or at minimum does NOT raise an unhandled exception.
        assert rc != 0, (
            "Expected a non-zero exit code (missing_dependency envelope), "
            f"got rc={rc}"
        )
        # The JSON envelope must be on stdout.
        assert captured.out.strip(), (
            "Expected a JSON envelope on stdout; got nothing"
        )
        envelope = _json.loads(captured.out)
        assert envelope["available"] is False, (
            f"Expected available:false, got available={envelope['available']}"
        )
        assert envelope["reason_category"] == "missing_dependency", (
            f"Expected reason_category='missing_dependency', "
            f"got {envelope['reason_category']!r}"
        )
        # Reason must mention the Biber tagger so users understand the gap.
        assert "biber" in envelope["reason"].lower() or "tagger" in envelope["reason"].lower(), (
            f"Expected 'biber' or 'tagger' in reason, got: {envelope['reason']!r}"
        )

    def test_include_biber_abstains_when_neurobiber_importable(
        self, tmp_path, capsys, monkeypatch
    ):
        """Codex round-2 P2: --include-biber with a PRESENT neurobiber still abstains cleanly.

        Pre-fix: _try_load_real_tagger() raised NotImplementedError as soon as
        `neurobiber` was importable, escaping the CLI guard as an uncaught
        traceback. Post-fix: the deferred M2 adapter returns None, so the CLI
        emits available:false / missing_dependency with rc=3.
        """
        import json as _json
        import types as _types

        # Package PRESENT — inject a stub so `import neurobiber` SUCCEEDS.
        monkeypatch.setitem(sys.modules, "neurobiber", _types.ModuleType("neurobiber"))

        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        target = tmp_path / "target.md"
        target.write_text(
            "Officials noted that the process followed established guidelines. " * 10,
            encoding="utf-8",
        )

        # Must NOT raise (pre-fix: NotImplementedError escapes vd.main()).
        rc = _run_vd_main([
            "voice_distance.py",
            str(target),
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
        ])

        captured = capsys.readouterr()
        assert rc == 3, (
            f"Expected rc=3 (missing_dependency envelope), got rc={rc}"
        )
        envelope = _json.loads(captured.out)
        assert envelope["available"] is False
        assert envelope["reason_category"] == "missing_dependency"
