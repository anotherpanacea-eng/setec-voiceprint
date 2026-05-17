#!/usr/bin/env python3
"""Regression tests for prestige_metaphor.py.

Three layers:

  * **Domain classifier unit tests** (`classify_domain`): hardcoded
    list lookup, WordNet fallback (gated on NLTK + wordnet
    availability), operator-supplied overrides, case-insensitivity.
  * **Entropy math tests** (`_normalized_shannon_entropy`): edge
    cases (empty, single category, uniform, skewed).
  * **End-to-end fixture tests**: AI-scatter fixture vs concentrated-
    metaphor fixture, scatter-entropy ordering, JSON schema, CLI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import prestige_metaphor as pm  # type: ignore


_FIXTURE_DIR = ROOT / "test_data" / "aic_8_9"


def _check_wordnet_available() -> bool:
    try:
        from nltk.corpus import wordnet as wn  # type: ignore
        wn.synsets("test", pos="n")
        return True
    except (ImportError, LookupError, Exception):
        return False


def _check_spacy_vectors_available() -> bool:
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


_HAS_WORDNET = _check_wordnet_available()
_HAS_VECTORS = _check_spacy_vectors_available()
_skip_no_wordnet = pytest.mark.skipif(
    not _HAS_WORDNET, reason="NLTK WordNet data not installed",
)
_skip_no_vectors = pytest.mark.skipif(
    not _HAS_VECTORS, reason="No spaCy vectors model installed",
)


# ----------------- classify_domain: hardcoded list ----------------


def test_classify_canonical_prestige_domains():
    """Spec's enumerated prestige domains all resolve to themselves
    via the hardcoded list."""
    assert pm.classify_domain("architecture") == "architecture"
    assert pm.classify_domain("grammar") == "grammar"
    assert pm.classify_domain("cartography") == "cartography"
    assert pm.classify_domain("machinery") == "machinery"
    assert pm.classify_domain("topology") == "topology"
    assert pm.classify_domain("geometry") == "geometry"
    assert pm.classify_domain("choreography") == "choreography"


def test_classify_derived_forms_resolve_to_parent_domain():
    """`architectural` → architecture, `grammatical` → grammar, etc."""
    assert pm.classify_domain("architectural") == "architecture"
    assert pm.classify_domain("grammatical") == "grammar"
    assert pm.classify_domain("topological") == "topology"
    assert pm.classify_domain("musical") == "music"


def test_classify_is_case_insensitive():
    assert pm.classify_domain("Architecture") == "architecture"
    assert pm.classify_domain("MACHINERY") == "machinery"


def test_classify_unknown_returns_none_when_wordnet_disabled():
    """Without WordNet, unknown words return None."""
    assert pm.classify_domain(
        "xyzzy_made_up_word", use_wordnet=False,
    ) is None


def test_classify_extra_domains_override():
    """Operator-supplied extra_domains take precedence."""
    extra = {"sportsball": "sports", "architecture": "buildings_my_way"}
    # Override prestige-domain lookup
    assert pm.classify_domain(
        "architecture", extra_domains=extra,
    ) == "buildings_my_way"
    # New domain added by operator
    assert pm.classify_domain(
        "sportsball", extra_domains=extra,
    ) == "sports"


# ----------------- classify_domain: WordNet fallback --------------


@_skip_no_wordnet
def test_wordnet_fallback_resolves_unknown_words():
    """Words not in the hardcoded list resolve via WordNet hypernyms."""
    # `grief` not in hardcoded list; WordNet should classify it
    # (likely to `feeling` or similar at L3-4 from root).
    result = pm.classify_domain("grief", use_wordnet=True)
    assert result is not None


@_skip_no_wordnet
def test_wordnet_fallback_handles_oov():
    """Words WordNet doesn't have return None."""
    result = pm.classify_domain(
        "xyzzy_completely_made_up_nonword",
        use_wordnet=True,
    )
    assert result is None


def test_wordnet_disabled_when_use_wordnet_false():
    """Hardcoded list still works with use_wordnet=False."""
    assert pm.classify_domain("architecture", use_wordnet=False) == "architecture"
    # Unknown words return None instead of WordNet fallback.
    assert pm.classify_domain("xyzzy_unknown", use_wordnet=False) is None


# ----------------- Entropy math -----------------------------------


def test_entropy_empty_returns_zero():
    """Empty distribution: no scatter to measure."""
    assert pm._normalized_shannon_entropy({}) == 0.0


def test_entropy_single_category_returns_zero():
    """Single category: no scatter."""
    assert pm._normalized_shannon_entropy({"machinery": 5}) == 0.0


def test_entropy_uniform_returns_one():
    """Even distribution across N categories: normalized entropy = 1.0."""
    counts = {"a": 3, "b": 3, "c": 3, "d": 3}
    assert pm._normalized_shannon_entropy(counts) == pytest.approx(1.0, abs=1e-6)


def test_entropy_skewed_distribution():
    """Skewed distribution: 0 < entropy < 1."""
    counts = {"machinery": 5, "engine": 1}  # 5/6 + 1/6
    result = pm._normalized_shannon_entropy(counts)
    assert 0.0 < result < 1.0


def test_entropy_handles_zero_count_categories():
    """Categories with zero count are still counted in N, but don't
    contribute to entropy. (Pragmatic: spec doesn't define behavior
    here; we treat them as undefined and skip the log calculation
    for them.)"""
    counts = {"a": 3, "b": 0, "c": 3}
    # n_categories = 3 (so denominator log2(3))
    # numerator: -(3/6 * log(3/6) + 3/6 * log(3/6)) since 0-counts skipped
    # That's -(2 * 0.5 * -1) = 1.0
    # Normalized: 1.0 / log2(3) ≈ 0.631
    result = pm._normalized_shannon_entropy(counts)
    assert 0.5 < result < 0.7


# ----------------- End-to-end fixture integration -----------------


@_skip_no_vectors
def test_density_runs_on_ai_fixture():
    """The AI-image-conjunction fixture should produce some hits
    at T1=2.0 and a non-zero scatter entropy."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = pm.prestige_metaphor_density(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
    )
    assert block["diagnostics"]["conjunction_count"] >= 3
    assert block["domain_scatter_entropy"] > 0.0


@_skip_no_vectors
def test_scatter_entropy_ordering_ai_vs_concentrated():
    """Diagnostic ordering: AI fixture (scattered domains) should
    have HIGHER scatter entropy than concentrated fixture (single
    domain), all else equal. The calibration outcome (whether one
    or both clear T3=0.7) is roadmap work, not a unit-test pin."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    ai_block = pm.prestige_metaphor_density(
        (_FIXTURE_DIR / "ai_image_conjunction_positive.md").read_text(),
        nlp=nlp, t1=2.0,
    )
    conc_block = pm.prestige_metaphor_density(
        (_FIXTURE_DIR / "concentrated_metaphor_negative.md").read_text(),
        nlp=nlp, t1=2.0,
    )
    # Concentrated fixture has 5+ machinery hits dominating its
    # distribution; AI fixture has more uniform spread. Ordering
    # is the diagnostic; absolute values are calibration-pending.
    assert ai_block["domain_scatter_entropy"] > conc_block["domain_scatter_entropy"]


@_skip_no_vectors
def test_json_schema_complete():
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    block = pm.prestige_metaphor_density(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
    )
    assert block["signal_path"] == "aic_8_9.prestige_metaphor_density"
    assert block["family"] == "aic-8-aesthetic-authority-laundering"
    assert block["polarity"] == "↑"
    assert block["status"] == "provisional"
    assert "value" in block
    assert "domain_scatter_entropy" in block
    assert "domain_distribution" in block
    assert "flag_fires" in block
    assert "conjunctions" in block
    # Each classified conjunction has scaffolding_word + target_word
    # + domain
    if block["conjunctions"]:
        c = block["conjunctions"][0]
        assert "scaffolding_word" in c
        assert "target_word" in c
        assert "domain" in c


@_skip_no_vectors
def test_flag_fires_combines_entropy_and_baseline():
    """The joint flag (entropy > t3 AND density > baseline) requires
    both conditions. With no baseline, only entropy matters."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    text = fixture.read_text(encoding="utf-8")
    # Without baseline: entropy alone decides
    block_no_baseline = pm.prestige_metaphor_density(
        text, nlp=nlp, t1=2.0, t3=0.5,
    )
    # With absurdly-high baseline: density check should fail
    block_high_baseline = pm.prestige_metaphor_density(
        text, nlp=nlp, t1=2.0, t3=0.5,
        baseline_value=10000.0,
    )
    # Same entropy + density, but the baseline check disqualifies
    assert block_high_baseline["flag_fires"] is False
    # No-baseline path can still fire based on entropy alone
    if block_no_baseline["domain_scatter_entropy"] > 0.5:
        assert block_no_baseline["flag_fires"] is True


@_skip_no_vectors
def test_scaffolding_word_is_higher_concreteness_member():
    """Per the spec-interpretation rationale in the module
    docstring: the scaffolding_word is the HIGHER-concreteness
    member of each pair."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    fixture = _FIXTURE_DIR / "concentrated_metaphor_negative.md"
    block = pm.prestige_metaphor_density(
        fixture.read_text(encoding="utf-8"),
        nlp=nlp, t1=2.0,
    )
    for c in block["conjunctions"]:
        # Scaffolding word concreteness >= target word concreteness
        if c["concreteness_a"] >= c["concreteness_b"]:
            assert c["scaffolding_word"] == c["word_a"]
            assert c["target_word"] == c["word_b"]
        else:
            assert c["scaffolding_word"] == c["word_b"]
            assert c["target_word"] == c["word_a"]


# ----------------- CLI smoke ---------------------------------------


@_skip_no_vectors
def test_cli_runs_on_fixture():
    fixture = _FIXTURE_DIR / "ai_image_conjunction_positive.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "prestige_metaphor.py"),
            str(fixture),
            "--t1", "2.0",
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    # schema_version 1.0 envelope: signal_path lives in the legacy
    # block under results.
    assert data["schema_version"] == "1.0"
    assert data["tool"] == "prestige_metaphor"
    assert data["results"]["signal_path"] == "aic_8_9.prestige_metaphor_density"


def test_cli_no_wordnet_flag():
    """`--no-wordnet` disables the WordNet fallback. Test exits
    cleanly even when run on a fixture (just smoke; can't check
    real output without spaCy vectors)."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "prestige_metaphor.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "--no-wordnet" in result.stdout


def test_cli_help_runs_cleanly():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "prestige_metaphor.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "prestige" in result.stdout.lower() or "AIC-8" in result.stdout


def test_cli_clean_exit_when_no_spacy_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """End-to-end: when the CLI runs and no spaCy model is
    installed, the user gets a clean exit-2 with an install hint
    on stderr — NOT a Python traceback.

    Codex P2 review of PR #58: ensures the prestige-metaphor CLI's
    typed-error handler covers the spaCy-load path, not just the
    embedding-lookup path.
    """
    fixture = tmp_path / "tiny.md"
    fixture.write_text("A sentence.\n", encoding="utf-8")
    setup = tmp_path / "sitecustomize.py"
    setup.write_text(
        "import spacy\n"
        "def _fail(*a, **kw):\n"
        "    raise OSError('[E050] simulated no model')\n"
        "spacy.load = _fail\n",
        encoding="utf-8",
    )
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": f"{tmp_path}:{ROOT}"}
    import os as _os
    for k in ("HOME", "USER", "LANG", "LC_ALL"):
        v = _os.environ.get(k)
        if v:
            env[k] = v

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "prestige_metaphor.py"),
            str(fixture),
        ],
        capture_output=True, text=True, timeout=30,
        env=env,
    )
    assert result.returncode == 2, (
        f"expected exit 2 (clean typed-error); got "
        f"{result.returncode}\nstderr: {result.stderr}"
    )
    assert "Traceback" not in result.stderr
