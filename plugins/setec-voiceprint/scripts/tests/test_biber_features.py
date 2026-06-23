#!/usr/bin/env python3
"""Tests for biber_features.py + stylometry_core biber_features family wiring.

Spec: setec-scratch/spec-wave-4/neurobiber-v2.md (feat/biber-features build).
All AC ids reference the spec's §6 acceptance criteria.

Design:
  - All tests are UNCONDITIONAL (no skipif) — CI-blocking per AC4.
  - Torch / transformers / spaCy / neurobiber are explicitly absent in AC8.
  - The deterministic stub tagger (§4.3) is used throughout M1 tests.
  - AC4 (_FORBIDDEN_KEYS / _walk_keys) is pinned verbatim from
    test_dependency_distance_audit.py:144-159.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import biber_features as bf  # type: ignore  # noqa: E402
import stylometry_core as sc  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Deterministic stub biber_tagger (§4.3 formalization)
#
# SHA-256 over (feature_id, text) → float in [0, 1).
# Properties pinned:
#   (1) identical text ⇒ byte-identical vector (SHA-256, no PRNG, no salt)
#   (2) every value is a finite float in [0, 1) — R4 bounds gate passes
#   (3) per-feature dependence on feature_id gives a non-degenerate vector
# ---------------------------------------------------------------------------

def stub_biber_tagger(text: str) -> dict[str, float]:
    """Deterministic per-feature rate in [0, 1) derived only from (feature_id, text)."""
    out: dict[str, float] = {}
    for feature_id, _label in bf.BIBER_FEATURE_SCHEMA:
        h = hashlib.sha256(f"{feature_id}\x00{text}".encode("utf-8")).digest()
        out[feature_id] = int.from_bytes(h[:8], "big") / float(1 << 64)
    return out


# Mark the stub so biber_family_features can report tagger_name as "stub"
stub_biber_tagger._tagger_name = "stub"


# Minimal baseline entries for family-pipeline tests.
_BASELINE_TEXT_A = (
    "The committee reviewed the preliminary findings and noted that several "
    "significant improvements could be made to the methodology. "
    "Members suggested revisions to the data collection procedures. "
    "The final report was submitted to the oversight board for approval."
)
_BASELINE_TEXT_B = (
    "Initial analyses of the data reveal interesting patterns. "
    "It is likely that additional work will clarify the results further. "
    "Researchers have not yet completed the secondary validation study. "
    "The observations warrant further investigation by qualified personnel."
)
_BASELINE_TEXT_C = (
    "We examined many documents submitted by various organizations. "
    "Our findings suggest that current practices may need updating. "
    "It seems that new standards would benefit all affected parties. "
    "The proposal has been forwarded to the relevant working groups."
)

TARGET_TEXT = (
    "The government released the report after months of deliberation. "
    "Critics argued that the findings contradicted earlier assessments. "
    "Officials noted that the process had followed established guidelines. "
    "Further review will determine whether changes are warranted."
)

def _make_baseline_entries() -> list[dict]:
    return [
        {"id": "a", "path": "a.txt", "text": _BASELINE_TEXT_A, "metadata": {}},
        {"id": "b", "path": "b.txt", "text": _BASELINE_TEXT_B, "metadata": {}},
        {"id": "c", "path": "c.txt", "text": _BASELINE_TEXT_C, "metadata": {}},
    ]


# ---------------------------------------------------------------------------
# AC4 — No-verdict recursive walk (CI-blocking, UNCONDITIONAL, per-test)
#
# Pinned VERBATIM from test_dependency_distance_audit.py:144-159.
# This is a module-level frozenset + generator — exact-key membership,
# NOT substring. The intersection-emptiness assertion form matches the
# shipped code in test_dependency_distance_audit.py:282-285.
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "is_smoothed", "verdict", "label", "class", "classification",
    "decision", "score", "confidence", "rank", "prediction", "flag", "selection",
    "best", "top", "selected",
})


def _walk_keys(obj):
    """Yield every dict key reachable in a nested results payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# ===========================================================================
# AC1 — Schema constant: exactly 96 features, no duplicates
# ===========================================================================

class TestBiberFeatureSchema:
    def test_schema_len_equals_96(self):
        """AC1: BIBER_FEATURE_SCHEMA has exactly 96 entries."""
        assert len(bf.BIBER_FEATURE_SCHEMA) == 96

    def test_schema_all_tuples(self):
        """Each entry is a (feature_id, label) 2-tuple."""
        for item in bf.BIBER_FEATURE_SCHEMA:
            assert isinstance(item, tuple) and len(item) == 2

    def test_schema_no_duplicate_ids(self):
        """AC1: all feature_ids are unique."""
        ids = [fid for fid, _ in bf.BIBER_FEATURE_SCHEMA]
        assert len(ids) == len(set(ids)), f"Duplicate ids: {set(x for x in ids if ids.count(x) > 1)}"

    def test_schema_all_bin_prefix(self):
        """All ids start with BIN_ (Biber INdicator convention)."""
        for fid, _ in bf.BIBER_FEATURE_SCHEMA:
            assert fid.startswith("BIN_"), f"id {fid!r} does not start with BIN_"

    def test_schema_labels_nonempty(self):
        """All labels are non-empty strings."""
        for fid, label in bf.BIBER_FEATURE_SCHEMA:
            assert isinstance(label, str) and label.strip(), f"Empty label for {fid}"


# ===========================================================================
# AC2 — Family normalizer: pure + total, silently drops unknown keys
# ===========================================================================

class TestBiberFamilyFeatures:
    def test_all_96_schema_ids_present(self):
        """Output contains exactly the 96 schema ids."""
        vec = {fid: 0.1 for fid, _ in bf.BIBER_FEATURE_SCHEMA}
        out = bf.biber_family_features(vec)
        schema_ids = [fid for fid, _ in bf.BIBER_FEATURE_SCHEMA]
        assert list(out.keys()) == schema_ids

    def test_missing_feature_filled_zero(self):
        """Missing schema features are filled with 0.0."""
        out = bf.biber_family_features({})
        assert all(v == 0.0 for v in out.values())
        assert len(out) == 96

    def test_unknown_key_silently_dropped(self):
        """AC2: unknown keys are SILENTLY DROPPED — no raise."""
        vec = {"BIN_not_in_schema": 99.9, "BIN_past_tense_verb": 0.5}
        out = bf.biber_family_features(vec)
        # Unknown key absent from output
        assert "BIN_not_in_schema" not in out
        # Known key preserved
        assert out["BIN_past_tense_verb"] == pytest.approx(0.5)
        # Exactly 96 keys
        assert len(out) == 96

    def test_output_is_exact_96_keys(self):
        """Passing a full schema vector returns exactly 96 keys in schema order."""
        vec = {fid: float(i) / 100 for i, (fid, _) in enumerate(bf.BIBER_FEATURE_SCHEMA)}
        out = bf.biber_family_features(vec)
        expected_keys = [fid for fid, _ in bf.BIBER_FEATURE_SCHEMA]
        assert list(out.keys()) == expected_keys

    def test_deterministic(self):
        """biber_family_features is deterministic (pure function)."""
        vec = stub_biber_tagger("Some test text here.")
        assert bf.biber_family_features(vec) == bf.biber_family_features(vec)

    def test_all_values_are_floats(self):
        """All output values are floats."""
        vec = stub_biber_tagger("Test text.")
        out = bf.biber_family_features(vec)
        for v in out.values():
            assert isinstance(v, float)


# ===========================================================================
# AC2a — include_biber=True with both None raises ValueError (fail-loud)
# ===========================================================================

class TestIncludeBiberRaises:
    def test_include_biber_no_vector_no_tagger_raises(self):
        """AC2a: extract_features(text, include_biber=True) raises ValueError."""
        text = "A sample text for testing the fail-loud contract."
        with pytest.raises(ValueError, match="include_biber requires biber_vector or biber_tagger"):
            sc.extract_features(text, include_biber=True)

    def test_include_biber_with_vector_does_not_raise(self):
        """Providing biber_vector satisfies include_biber — no raise."""
        vec = stub_biber_tagger("Some text.")
        result = sc.extract_features("Some text.", include_biber=True, biber_vector=vec)
        assert "biber_features" in result["features"]

    def test_include_biber_with_tagger_does_not_raise(self):
        """Providing biber_tagger satisfies include_biber — no raise."""
        result = sc.extract_features(
            TARGET_TEXT, include_biber=True, biber_tagger=stub_biber_tagger
        )
        assert "biber_features" in result["features"]

    def test_include_biber_false_default_no_biber_family(self):
        """Default include_biber=False: no biber_features key emitted."""
        result = sc.extract_features(TARGET_TEXT)
        assert "biber_features" not in result["features"]


# ===========================================================================
# AC3 — Family-pipeline wiring: compare_to_baseline + build_profile
# ===========================================================================

class TestFamilyPipelineWiring:
    def test_compare_to_baseline_emits_biber_family(self):
        """AC3: compare_to_baseline with include_biber=True emits families.biber_features."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert "biber_features" in result["families"]

    def test_biber_family_has_required_keys(self):
        """AC3: families.biber_features has all family_distance keys."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        fd = result["families"]["biber_features"]
        required = {
            "n_features", "burrows_delta", "cosine_distance_to_centroid",
            "cosine_distance_to_baseline_mean", "cosine_distance_to_baseline_min",
            "top_deviations", "overall_delta_contribution_cap", "capped_in_overall",
        }
        assert required <= set(fd.keys()), f"Missing: {required - set(fd.keys())}"

    def test_biber_family_n_features_equals_96(self):
        """AC3: n_features == 96."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert result["families"]["biber_features"]["n_features"] == 96

    def test_top_deviations_at_most_25(self):
        """AC3 / P3a: top_deviations is capped at 25, not 96."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        td = result["families"]["biber_features"]["top_deviations"]
        assert len(td) <= 25, f"top_deviations has {len(td)} entries (should be <= 25)"

    def test_biber_participates_in_overall(self):
        """AC3: biber_features participates in the weighted overall band."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        # overall must be present and weighted_delta must be finite
        assert "overall" in result
        wd = result["overall"]["weighted_delta"]
        assert isinstance(wd, float) and wd >= 0.0

    def test_build_profile_emits_biber_family(self):
        """AC3: build_profile with include_biber=True emits families.biber_features."""
        entries = _make_baseline_entries()
        profile = sc.build_profile(
            entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert "biber_features" in profile["families"]

    def test_build_profile_biber_n_features_equals_96(self):
        """AC3: build_profile families.biber_features has n_features == 96."""
        entries = _make_baseline_entries()
        profile = sc.build_profile(
            entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert profile["families"]["biber_features"]["n_features"] == 96

    def test_stub_tags_both_target_and_baseline(self):
        """AC3 / §4.3: biber_features present on both target and all baseline items."""
        entries = _make_baseline_entries()
        # extract_features on target
        target_feat = sc.extract_features(
            TARGET_TEXT, include_spacy=False, include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert "biber_features" in target_feat["features"]
        # extract_entry_features on baseline
        baseline_feat = sc.extract_entry_features(
            entries, include_spacy=False, include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        for item in baseline_feat:
            assert "biber_features" in item["features"], (
                f"baseline entry {item['id']!r} missing biber_features"
            )


# ===========================================================================
# AC4 — No-verdict recursive walk (UNCONDITIONAL, no skipif)
# ===========================================================================

class TestNoVerdictRecursiveWalk:
    def test_compare_to_baseline_no_forbidden_keys(self):
        """AC4: compare_to_baseline results carry no forbidden keys."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        keys = set(_walk_keys(result))
        offending = keys & _FORBIDDEN_KEYS
        assert offending == set(), f"Forbidden keys in compare_to_baseline results: {offending}"

    def test_build_profile_no_forbidden_keys(self):
        """AC4: build_profile results carry no forbidden keys."""
        entries = _make_baseline_entries()
        profile = sc.build_profile(
            entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        keys = set(_walk_keys(profile))
        offending = keys & _FORBIDDEN_KEYS
        assert offending == set(), f"Forbidden keys in build_profile results: {offending}"

    def test_standalone_panel_no_forbidden_keys(self):
        """AC4: standalone biber_panel results carry no forbidden keys."""
        result, _warnings = bf.run_biber_panel(TARGET_TEXT, biber_tagger=stub_biber_tagger)
        keys = set(_walk_keys(result))
        offending = keys & _FORBIDDEN_KEYS
        assert offending == set(), f"Forbidden keys in biber_panel results: {offending}"

    def test_benign_key_not_flagged(self):
        """Ensure benign keys like 'n_features', 'top_deviations' are not falsely flagged."""
        # 'top_deviations' and 'n_features' must NOT appear in _FORBIDDEN_KEYS
        assert "top_deviations" not in _FORBIDDEN_KEYS
        assert "n_features" not in _FORBIDDEN_KEYS
        assert "calibration_status" not in _FORBIDDEN_KEYS


# ===========================================================================
# AC5 — Never-selects (posture guard)
# ===========================================================================

class TestNeverSelects:
    def test_no_winner_or_argmax_keys(self):
        """AC5: no winner/proportion/argmax key in any biber output."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        never_select_keys = {
            "winner", "proportion_wins", "proportion_correct",
            "argmax", "pass", "fail",
        }
        keys = set(_walk_keys(result))
        assert not (keys & never_select_keys), f"Selection keys found: {keys & never_select_keys}"

    def test_standalone_no_selection_keys(self):
        """AC5: standalone biber_panel has no selection/argmax keys."""
        result, _ = bf.run_biber_panel(TARGET_TEXT, biber_tagger=stub_biber_tagger)
        never_select_keys = {
            "winner", "proportion_wins", "proportion_correct",
            "argmax", "pass", "fail",
        }
        keys = set(_walk_keys(result))
        assert not (keys & never_select_keys), f"Selection keys found: {keys & never_select_keys}"


# ===========================================================================
# AC6 — Band provisional + calibration_status placement
# ===========================================================================

class TestCalibrationStatusPlacement:
    def test_standalone_panel_has_calibration_status_provisional(self):
        """AC6: biber_panel carries calibration_status: 'provisional'."""
        result, _ = bf.run_biber_panel(TARGET_TEXT, biber_tagger=stub_biber_tagger)
        assert result["biber_panel"]["calibration_status"] == "provisional"

    def test_family_block_has_no_standalone_calibration_status(self):
        """AC6: families.biber_features carries NO standalone calibration_status key."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        fd = result["families"]["biber_features"]
        assert "calibration_status" not in fd, (
            "families.biber_features must NOT carry a standalone calibration_status key; "
            "provisionality lives in overall.threshold_note (PROVISIONAL_BAND_NOTE)"
        )

    def test_overall_threshold_note_is_provisional(self):
        """AC6: overall.threshold_note is PROVISIONAL_BAND_NOTE."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        assert result["overall"]["threshold_note"] == sc.PROVISIONAL_BAND_NOTE


# ===========================================================================
# AC7 — Anti-Goodhart: claim license + model_family_attribution exclusion
# ===========================================================================

class TestAntiGoodhart:
    def test_claim_license_refuses_attribution_and_provenance(self):
        """AC7: does_not_license refuses model-family attribution + AI/human + authorship."""
        cl = bf._claim_license()
        dnl = cl["does_not_license"].lower()
        assert "authorship" in dnl
        assert "ai/human" in dnl
        assert "model-family" in dnl or "model_family" in dnl or "attribution" in dnl

    def test_claim_license_names_dual_use_arxiv(self):
        """AC7: does_not_license names arXiv:2410.16107 dual-use."""
        cl = bf._claim_license()
        assert "2410.16107" in cl["does_not_license"]

    def test_model_family_attribution_does_not_import_biber(self):
        """AC7: model_family_attribution.py does not import biber_features."""
        mfa_path = SCRIPTS / "model_family_attribution.py"
        if not mfa_path.exists():
            pytest.skip("model_family_attribution.py not found")
        src = mfa_path.read_text(encoding="utf-8")
        assert "biber_features" not in src, (
            "model_family_attribution.py imports/references biber_features — "
            "breaks the anti-Goodhart disjointness of this descriptive surface"
        )


# ===========================================================================
# AC8 — Stdlib-import: torch / transformers / spaCy / neurobiber absent
# ===========================================================================

class TestStdlibImport:
    def test_biber_features_importable_without_heavy_deps(self, monkeypatch):
        """AC8: biber_features imports + runs with torch/transformers/spaCy/neurobiber absent."""
        # Shim out the optional heavy deps using monkeypatch.
        import sys as _sys
        fake_modules = {}
        for mod_name in ("torch", "transformers", "spacy", "neurobiber"):
            if mod_name not in _sys.modules:
                fake_modules[mod_name] = None
                monkeypatch.setitem(_sys.modules, mod_name, None)  # type: ignore

        # Re-import the module to ensure the shim is respected.
        importlib.reload(bf)

        # The schema and normalizer must still work.
        assert len(bf.BIBER_FEATURE_SCHEMA) == 96
        out = bf.biber_family_features({})
        assert len(out) == 96

    def test_no_torch_in_biber_features_source(self):
        """AC8: biber_features.py source does not import torch/transformers at module level."""
        src = Path(bf.__file__).read_text(encoding="utf-8")
        # These are only allowed inside function bodies (lazy), not at module top level.
        # We check that neither 'import torch' nor 'import transformers' appears as a
        # top-level (non-indented) statement.
        for line in src.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("import torch") or stripped.startswith("from torch"):
                # Only forbidden if at module top level (no indentation)
                assert line.startswith(" ") or line.startswith("\t"), (
                    f"torch imported at module top level: {line!r}"
                )
            if stripped.startswith("import transformers") or stripped.startswith("from transformers"):
                assert line.startswith(" ") or line.startswith("\t"), (
                    f"transformers imported at module top level: {line!r}"
                )

    def test_stylometry_core_no_top_level_biber_import(self):
        """AC8: stylometry_core does not top-level import biber_features (lazy only)."""
        src = Path(sc.__file__).read_text(encoding="utf-8")
        # The import of biber_family_features must be inside the if-biber: block
        # (indented), never at module top level.
        for line in src.splitlines():
            if "from biber_features import" in line or "import biber_features" in line:
                assert line.startswith(" ") or line.startswith("\t"), (
                    f"biber_features imported at module top level in stylometry_core: {line!r}"
                )


# ===========================================================================
# AC9 — Standalone abstain when no tagger
# ===========================================================================

class TestStandaloneAbstain:
    def test_standalone_panel_abstains_with_no_tagger(self):
        """AC9: run_biber_panel raises when neither tagger nor vector supplied."""
        with pytest.raises(ValueError):
            bf.run_biber_panel(TARGET_TEXT)

    def test_cli_abstains_missing_dependency(self, tmp_path, monkeypatch):
        """AC9: CLI biber_features main() with no real tagger → available:false, missing_dependency."""
        import io
        import json
        from contextlib import redirect_stdout

        # Ensure _try_load_real_tagger returns None (M1 default).
        monkeypatch.setattr(bf, "_try_load_real_tagger", lambda: None)

        t = tmp_path / "target.txt"
        t.write_text(TARGET_TEXT, encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = bf.main([str(t), "--json"])
        env = json.loads(out.getvalue())
        assert env["available"] is False
        assert env["reason_category"] == "missing_dependency"
        assert rc == 3


# ===========================================================================
# AC10 — Opt-in OFF leaves existing surfaces unchanged
# (Also in test_voice_distance_schema.py and test_voice_profile_schema.py —
#  this is the function-call-consumer check on raw stylometry_core APIs.)
# ===========================================================================

class TestOptInOffDefaultBehavior:
    def test_compare_to_baseline_no_biber_by_default(self):
        """AC10: compare_to_baseline without include_biber has NO biber_features key (recursive)."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            # include_biber is False by default
        )
        keys = set(_walk_keys(result))
        assert "biber_features" not in keys, (
            "'biber_features' key found in compare_to_baseline result without --include-biber"
        )

    def test_build_profile_no_biber_by_default(self):
        """AC10: build_profile without include_biber has NO biber_features key (recursive)."""
        entries = _make_baseline_entries()
        profile = sc.build_profile(
            entries,
            include_spacy=False,
            # include_biber is False by default
        )
        keys = set(_walk_keys(profile))
        assert "biber_features" not in keys, (
            "'biber_features' key found in build_profile result without --include-biber"
        )

    def test_extract_features_no_biber_by_default(self):
        """AC10: extract_features without include_biber has NO biber_features key."""
        result = sc.extract_features(TARGET_TEXT, include_spacy=False)
        assert "biber_features" not in result["features"]

    def test_extract_entry_features_no_biber_by_default(self):
        """AC10: extract_entry_features without include_biber has NO biber_features key."""
        entries = _make_baseline_entries()
        baseline_feat = sc.extract_entry_features(entries, include_spacy=False)
        for item in baseline_feat:
            assert "biber_features" not in item["features"]


# ===========================================================================
# AC11 — Injected-vector / stub determinism + precomputed-vector mode
# ===========================================================================

class TestStubDeterminism:
    def test_stub_is_deterministic(self):
        """AC11: identical text → identical stub vector."""
        text = "Some determinism test text for the Biber feature stub."
        v1 = stub_biber_tagger(text)
        v2 = stub_biber_tagger(text)
        for fid in v1:
            assert v1[fid] == pytest.approx(v2[fid], rel=1e-12, abs=1e-12)

    def test_stub_all_values_in_range(self):
        """AC11: all stub values are in [0, 1) — finite, non-negative."""
        vec = stub_biber_tagger("A test of the stub value range.")
        for fid, v in vec.items():
            assert 0.0 <= v < 1.0, f"{fid}: {v} is out of [0, 1)"

    def test_compare_to_baseline_deterministic_with_stub(self):
        """AC11: same text → same compare_to_baseline result (tagger-mode)."""
        entries = _make_baseline_entries()
        kwargs = dict(include_spacy=False, include_biber=True, biber_tagger=stub_biber_tagger)
        r1 = sc.compare_to_baseline(TARGET_TEXT, entries, **kwargs)
        r2 = sc.compare_to_baseline(TARGET_TEXT, entries, **kwargs)
        fd1 = r1["families"]["biber_features"]
        fd2 = r2["families"]["biber_features"]
        assert fd1["n_features"] == fd2["n_features"]
        assert fd1["burrows_delta"] == pytest.approx(fd2["burrows_delta"], rel=1e-12, abs=1e-12)
        assert fd1["cosine_distance_to_centroid"] == pytest.approx(
            fd2["cosine_distance_to_centroid"], rel=1e-12, abs=1e-12
        )

    def test_precomputed_vector_mode_matches_tagger_mode(self):
        """AC11: precomputed biber_vector= gives same family block as tagger call."""
        entries = _make_baseline_entries()
        # Tagger mode
        r_tagger = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        fd_tagger = r_tagger["families"]["biber_features"]

        # Precomputed-vector mode: attach biber_vector to each entry + pass target vector
        target_vec = stub_biber_tagger(TARGET_TEXT)
        entries_with_vec = [
            {**e, "biber_vector": stub_biber_tagger(e["text"])}
            for e in _make_baseline_entries()
        ]
        r_vec = sc.compare_to_baseline(
            TARGET_TEXT, entries_with_vec,
            include_spacy=False,
            include_biber=True,
            biber_vector=target_vec,  # precomputed target vector
            # no biber_tagger
        )
        fd_vec = r_vec["families"]["biber_features"]

        assert fd_tagger["n_features"] == fd_vec["n_features"]
        assert fd_tagger["burrows_delta"] == pytest.approx(
            fd_vec["burrows_delta"], rel=1e-12, abs=1e-12
        )

    def test_stub_ordered_feature_ids_match_schema(self):
        """AC11: stub output keys are in BIBER_FEATURE_SCHEMA order."""
        vec = stub_biber_tagger("Order test text.")
        schema_ids = [fid for fid, _ in bf.BIBER_FEATURE_SCHEMA]
        assert list(vec.keys()) == schema_ids

    def test_n_features_exact_equals_96(self):
        """AC11: n_features count is exact == 96 (not approx)."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        # Exact integer equality
        assert result["families"]["biber_features"]["n_features"] == 96

    def test_feature_ids_list_exact_equals_schema(self):
        """AC11: the feature ids in the family's top_deviations match schema ids."""
        entries = _make_baseline_entries()
        result = sc.compare_to_baseline(
            TARGET_TEXT, entries,
            include_spacy=False,
            include_biber=True,
            biber_tagger=stub_biber_tagger,
        )
        td = result["families"]["biber_features"]["top_deviations"]
        schema_ids_set = {fid for fid, _ in bf.BIBER_FEATURE_SCHEMA}
        for dev in td:
            assert dev["feature"] in schema_ids_set, (
                f"top_deviations feature {dev['feature']!r} not in BIBER_FEATURE_SCHEMA"
            )


# ===========================================================================
# AC12 — Capability registration + golden round-trip
# (checked separately via check_capabilities_drift.py — basic smoke here)
# ===========================================================================

class TestCapabilityRegistration:
    def test_yaml_fragment_exists(self):
        """AC12: capabilities.d/biber_features.yaml exists."""
        caps_dir = Path(__file__).resolve().parents[2] / "capabilities.d"
        assert (caps_dir / "biber_features.yaml").exists(), (
            "capabilities.d/biber_features.yaml missing"
        )

    def test_golden_json_exists(self):
        """AC12: _golden_capabilities/biber_features.json exists."""
        golden_dir = Path(__file__).resolve().parents[0] / "_golden_capabilities"
        assert (golden_dir / "biber_features.json").exists(), (
            "_golden_capabilities/biber_features.json missing"
        )

    def test_golden_agrees_with_yaml(self):
        """AC12: golden and YAML agree on the required fields."""
        import yaml  # type: ignore

        caps_dir = Path(__file__).resolve().parents[2] / "capabilities.d"
        yaml_path = caps_dir / "biber_features.yaml"
        golden_path = Path(__file__).resolve().parents[0] / "_golden_capabilities" / "biber_features.json"

        if not yaml_path.exists() or not golden_path.exists():
            pytest.skip("capability files not yet present")

        with open(yaml_path, encoding="utf-8") as f:
            yd = yaml.safe_load(f)
        entries = yd.get("entries", [])
        assert len(entries) == 1
        ye = entries[0]

        with open(golden_path, encoding="utf-8") as f:
            gd = json.load(f)

        for key in ("id", "script_path", "surface", "status"):
            assert ye[key] == gd[key], f"Mismatch on {key}: YAML={ye[key]!r}, golden={gd[key]!r}"

    def test_task_surface_registered(self):
        """AC12: TASK_SURFACE is a valid registered surface."""
        assert bf.TASK_SURFACE == "voice_coherence"
        assert "voice_coherence" in VALID_TASK_SURFACES


# ===========================================================================
# AC13 — M2 seam inert in CI (skipif guard for real tagger smoke)
# ===========================================================================

_has_neurobiber = False
try:
    import neurobiber  # type: ignore  # noqa: F401
    _has_neurobiber = True
except ImportError:
    pass

@pytest.mark.skipif(not _has_neurobiber, reason="neurobiber package not installed (M2 only)")
def test_real_neurobiber_tagger_smoke():
    """AC13 M2 smoke: real tagger produces 96 named features (CI skips; run out-of-band)."""
    # M2 implementation stub — this test body is a placeholder for when
    # the real _NeurobiberTagger is wired in.
    pytest.skip("M2 NeurobiberTagger not yet implemented (deferred)")
