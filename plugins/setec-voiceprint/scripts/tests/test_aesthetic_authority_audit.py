#!/usr/bin/env python3
"""Regression tests for aesthetic_authority_audit.py.

End-to-end tests: the compound audit composes kicker_density,
image_conjunction, and prestige_metaphor and adds joint co-
occurrence metrics. Tests verify schema correctness, register-
based baseline resolution, joint-metric math, and CLI smoke.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aesthetic_authority_audit as aaa  # type: ignore


_FIXTURE_DIR = ROOT / "test_data" / "aic_8_9"


def _check_spacy_vectors() -> bool:
    try:
        import spacy
        for name in ("en_core_web_md", "en_core_web_lg"):
            try:
                spacy.load(name)
                return True
            except OSError:
                continue
        return False
    except ImportError:
        return False


_HAS_VECTORS = _check_spacy_vectors()
_skip_no_vectors = pytest.mark.skipif(
    not _HAS_VECTORS,
    reason="No spaCy vectors model installed",
)


# ---------- End-to-end fixture integration ----------


@_skip_no_vectors
def test_compound_runs_on_ai_fixture():
    """The AI fixture should fire kicker density, image conjunction
    density, and prestige metaphor scatter all together."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
        register="contemporary_essay",
    )
    # All three component blocks present
    assert "aic_9_kicker_density" in block
    assert "aic_8_image_conjunction" in block
    assert "aic_8_prestige_metaphor" in block
    assert "compound" in block
    # Joint metrics populated
    compound = block["compound"]
    assert compound["kicker_paragraph_count"] >= 1
    assert "kicker_with_image_conjunction_rate" in compound
    assert "all_three_co_occurrence_rate" in compound


@_skip_no_vectors
def test_compound_resolves_register_baselines():
    """Passing a register should resolve baselines for all three signals."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
        register="contemporary_essay",
    )
    # Each component should have its baseline_comparison block
    assert "baseline_comparison" in block["aic_9_kicker_density"]
    assert "baseline_comparison" in block["aic_8_image_conjunction"]
    assert "baseline_comparison" in block["aic_8_prestige_metaphor"]
    # Source labels reflect the register
    src = block["aic_9_kicker_density"]["baseline_comparison"]["baseline_source"]
    assert src == "register_typical_contemporary_essay"


@_skip_no_vectors
def test_explicit_baseline_overrides_register():
    """Explicit baseline takes precedence over register-typical lookup."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
        register="contemporary_essay",
        explicit_baselines={"kicker_density": 0.50},
    )
    bc = block["aic_9_kicker_density"]["baseline_comparison"]
    assert bc["baseline_value"] == 0.50
    assert bc["baseline_source"] == "operator-supplied"


@_skip_no_vectors
def test_compound_no_register_no_baselines():
    """register=None + no explicit baselines → no baseline_comparison."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
        register=None,
    )
    # Component blocks shouldn't have baseline_comparison
    assert "baseline_comparison" not in block["aic_9_kicker_density"]
    assert "baseline_comparison" not in block["aic_8_image_conjunction"]
    assert "baseline_comparison" not in block["aic_8_prestige_metaphor"]


@_skip_no_vectors
def test_compound_diagnostics_populated():
    """The diagnostics block names the register and thresholds."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0, t2=0.4, t3=0.7,
        register="literary_fiction",
    )
    diag = block["diagnostics"]
    assert diag["register"] == "literary_fiction"
    assert diag["thresholds"]["t1_concreteness_gap"] == 2.0
    assert diag["thresholds"]["t2_embedding_similarity"] == 0.4
    assert diag["thresholds"]["t3_scatter_entropy"] == 0.7


# ---------- Joint-metric math ----------


@_skip_no_vectors
def test_joint_rates_are_proportions():
    """All three joint rates are in [0, 1]."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0, register="contemporary_essay",
    )
    compound = block["compound"]
    for key in (
        "kicker_with_image_conjunction_rate",
        "kicker_with_prestige_metaphor_rate",
        "all_three_co_occurrence_rate",
    ):
        rate = compound[key]
        assert 0.0 <= rate <= 1.0, f"{key}={rate} outside [0, 1]"


@_skip_no_vectors
def test_all_three_rate_less_than_or_equal_to_pairwise_rates():
    """Joint rate of all three is bounded by each pairwise rate."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = aaa.aesthetic_authority_audit(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0, register="contemporary_essay",
    )
    compound = block["compound"]
    # all_three is the most stringent; cannot exceed any of the
    # two pairwise rates.
    assert (
        compound["all_three_co_occurrence_rate"]
        <= compound["kicker_with_image_conjunction_rate"]
    )
    assert (
        compound["all_three_co_occurrence_rate"]
        <= compound["kicker_with_prestige_metaphor_rate"]
    )


# ---------- CLI smoke ----------


@_skip_no_vectors
def test_cli_runs_on_fixture():
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "aesthetic_authority_audit.py"),
            str(fixture),
            "--t1", "2.0",
            "--register", "contemporary_essay",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["signal_path"] == "aic_8_9.aesthetic_authority_audit"
    assert data["family"] == "aic-8-9-compound"


def test_cli_help_runs():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "aesthetic_authority_audit.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "compound" in result.stdout.lower() or "AIC-8" in result.stdout
