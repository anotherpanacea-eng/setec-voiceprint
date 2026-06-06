#!/usr/bin/env python3
"""Tests for narratorial_distance_audit.py — descriptive FID / distance profile.

Implements the spec's test contract (specs/12-narratorial-distance-audit.md):
test_surface_registered, test_perception_verb_density, test_deixis_split,
test_fid_heuristic, test_trajectory_shape, test_claim_license_refuses_verdict,
test_envelope_shape, test_deterministic.

Tests that need spaCy are skipped (not failed) when the model is unavailable, so
the suite stays green on stdlib-only boxes. The surface-registration,
claim-license, and FID-heuristic-math tests run without spaCy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import narratorial_distance_audit as nda  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402

_skip_no_spacy = pytest.mark.skipif(
    not nda.HAS_SPACY, reason="spaCy + en_core_web_sm not installed"
)


# A deliberately CLOSE / free-indirect passage: 3rd-person, past tense, proximal
# deixis (here/now/this), dense perception/cognition verbs, evaluative colour,
# no dialogue. Repeated to clear the 1500-word floor while staying close.
_CLOSE = (
    "She saw the light now and knew this was wrong. Here, in this terrible "
    "room, she felt the strange cold and wondered what she had done. She "
    "remembered the awful morning and realized how beautiful the ruin had "
    "become. She watched the shadow and believed it moved. She heard nothing "
    "but felt everything, and now she understood the dreadful truth of this "
    "place. "
)

# A deliberately DISTANT passage: framed, distal deixis (there/then/that),
# few perception verbs, dialogue tags, neutral register.
_DISTANT = (
    "The committee met there in the spring. Then the secretary read the report "
    "aloud. \"We will proceed,\" he said. The members voted on that motion and "
    "the chair recorded the result. Afterward the building closed at the usual "
    "hour. The minutes were filed in that archive and the matter was settled by "
    "procedure. "
)


def _long(text: str, target_words: int = 1700) -> str:
    """Repeat a passage until comfortably over the length floor."""
    out = text
    while nda.count_words(out) < target_words:
        out += "\n\n" + text
    return out


CLOSE_DOC = _long(_CLOSE)
DISTANT_DOC = _long(_DISTANT)


def test_surface_registered():
    assert nda.TASK_SURFACE == "narratorial_distance"
    assert nda.TASK_SURFACE in VALID_TASK_SURFACES
    assert nda.TASK_SURFACE in TASK_SURFACE_LABELS


@_skip_no_spacy
def test_perception_verb_density():
    """A perception-dense close passage should report a clearly higher
    perception-verb density than a neutral, procedural one. Also verifies the
    --verb-lexicon override path changes the measurement."""
    close = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    distant = nda.audit_narratorial_distance(
        DISTANT_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    close_density = close["windows"][0]["perception_verb_density_per_1k"]
    distant_density = distant["windows"][0]["perception_verb_density_per_1k"]
    assert close_density > 0
    assert close_density > distant_density

    # Lexicon override: an empty/irrelevant custom set zeroes the hits.
    custom = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=frozenset({"frobnicate"}),
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    assert custom["windows"][0]["perception_verb_hits"] == 0


@_skip_no_spacy
def test_deixis_split():
    """Proximal vs. distal deixis must be split: the close passage skews
    proximal, the distant passage skews distal."""
    close = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )["windows"][0]["deixis"]
    distant = nda.audit_narratorial_distance(
        DISTANT_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )["windows"][0]["deixis"]
    assert close["proximal"] > 0
    assert distant["distal"] > 0
    assert close["proximal_share"] > distant["proximal_share"]


def test_fid_heuristic():
    """The FID heuristic is past-tense + proximal-deixis + perception, MINUS
    quotation/tag. Pure-math test (no spaCy needed)."""
    high = nda.fid_heuristic_score(
        past_ratio=1.0, has_proximal_deixis=True,
        has_perception_verb=True, has_quote_or_tag=False,
    )
    low = nda.fid_heuristic_score(
        past_ratio=0.0, has_proximal_deixis=False,
        has_perception_verb=False, has_quote_or_tag=True,
    )
    assert high == pytest.approx(1.0)
    assert low == pytest.approx(0.0)
    assert 0.0 <= high <= 1.0 and 0.0 <= low <= 1.0

    # Presence of a quotation/tag must REDUCE the score, all else equal.
    no_tag = nda.fid_heuristic_score(
        past_ratio=1.0, has_proximal_deixis=True,
        has_perception_verb=True, has_quote_or_tag=False,
    )
    with_tag = nda.fid_heuristic_score(
        past_ratio=1.0, has_proximal_deixis=True,
        has_perception_verb=True, has_quote_or_tag=True,
    )
    assert with_tag < no_tag


@_skip_no_spacy
def test_trajectory_shape():
    """The trajectory is one point per window in document order, with a
    normalized 0..1 position and a distance + FID + classification per point."""
    res = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    traj = res["trajectory"]
    assert len(traj) == res["distribution"]["n_windows"]
    assert len(traj) == len(res["windows"])
    # Indices are 0..n-1 in order.
    assert [p["index"] for p in traj] == list(range(len(traj)))
    # Positions are normalized, monotic non-decreasing, ending at 1.0.
    positions = [p["position"] for p in traj]
    assert positions[0] == 0.0
    if len(traj) > 1:
        assert positions[-1] == pytest.approx(1.0)
        assert positions == sorted(positions)
    for p in traj:
        assert 0.0 <= p["distance"] <= 1.0
        assert 0.0 <= p["fid_score"] <= 1.0
        assert p["classification"] in {"close", "distant"}


def test_claim_license_refuses_verdict():
    lic = nda._claim_license(
        strategy="paragraph", n_windows=5, custom_verb_lexicon=False,
    )
    dn = lic.does_not_license.lower()
    # Refuses authorship / AI / quality inference.
    assert "authorship" in dn
    assert "ai" in dn
    assert "quality" in dn
    # Notes FID detection is heuristic, not a parse of literary intent.
    assert "heuristic" in dn
    assert "intent" in dn
    # Licenses the descriptive features + trajectory.
    lc = lic.licenses.lower()
    assert "trajectory" in lc and "fid" in lc
    # Task surface matches; the posture is explicitly non-verdict / no-band.
    assert lic.task_surface == "narratorial_distance"
    rendered = lic.render_block().lower()
    # The descriptive posture is asserted ("no band, no verdict"); the only
    # occurrences of "verdict"/"band" are in negated form.
    assert "no band, no verdict" in rendered
    # No standalone verdict CLAIM (e.g. "verdict: close") leaks in.
    assert "verdict:" not in rendered


@_skip_no_spacy
def test_envelope_shape():
    payload = nda.build_payload(
        nda.audit_narratorial_distance(
            CLOSE_DOC, strategy="paragraph",
            perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
            evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
        ),
        target_path="x.txt", word_count=nda.count_words(CLOSE_DOC),
        available=True, strategy="paragraph",
    )
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "narratorial_distance"
    assert payload["tool"] == "narratorial_distance_audit"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    assert payload["claim_license"]["task_surface"] == "narratorial_distance"
    for key in ("window_strategy", "windows", "distribution", "trajectory"):
        assert key in payload["results"]
    d = payload["results"]["distribution"]
    assert d["n_windows"] == d["n_close"] + d["n_distant"]
    # No verdict / band keys leak into results.
    for forbidden in ("band", "verdict", "smoothed", "compression"):
        assert forbidden not in payload["results"]


def test_envelope_shape_unavailable_no_spacy():
    """When spaCy is missing OR the text is too short, the envelope must be a
    clean available=False block — never a traceback."""
    payload = nda.build_payload(
        {}, target_path="x.txt", word_count=10, available=False,
        strategy="paragraph", warnings=["too short"],
    )
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None
    assert payload["warnings"]


def test_cli_runs_clean_when_too_short(tmp_path):
    """A short input never crashes; rc 0 and a clean unavailable envelope."""
    f = tmp_path / "short.txt"
    f.write_text("She saw the light and knew.\n", encoding="utf-8")
    rc = nda.main([str(f), "--json"])
    assert rc == 0


@_skip_no_spacy
def test_deterministic():
    a = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    b = nda.audit_narratorial_distance(
        CLOSE_DOC, strategy="paragraph",
        perception_verbs=nda.DEFAULT_PERCEPTION_VERBS,
        evaluative_adjectives=nda.DEFAULT_EVALUATIVE_ADJECTIVES,
    )
    assert a == b
