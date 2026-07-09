#!/usr/bin/env python3
"""Regression tests for dialogue_voice_audit.py (spec 11).

The audit is spaCy-gated for the *overall* run (it reports
``available: false`` without spaCy), but the quote / tag extraction,
per-character profiling, and divergence math are pure-Python and
testable without a model. Tests that exercise the full envelope skip
cleanly when spaCy / en_core_web_sm is unavailable.

Fixtures are synthetic two-character dialogue with deliberately
distinct registers (a formal, non-contracting speaker vs. a casual,
heavily-contracting one) plus some unattributed lines, so the
extraction, bucketing, and divergence paths all have signal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore

import dialogue_voice_audit as dva  # type: ignore


# A compact, deterministic two-speaker dialogue with distinct voices
# and one unattributed line. Repeated to clear the length floor when a
# full-envelope run is exercised.
_DIALOGUE_BLOCK = '''
"I cannot abide such conduct, and I shall not pretend otherwise," said Eleanor.
"Aw, c'mon, don't be like that — it's no big deal," Jack replied.
"You will address me properly, Jack," Eleanor said.
"Yeah, yeah, whatever you say," said Jack.
"It is a matter of principle, not preference," Eleanor continued.
"I'm just sayin', you're way too uptight about it," Jack added.
"The silence stretched between them for a long moment."
"Could we not simply discuss this as adults?" Eleanor asked.
"Sure, fine, let's talk, I guess," Jack muttered.
"I would prefer that we resolve it tonight," said Eleanor.
"Can't we just chill out for once?" Jack groaned.
'''


def _long_text() -> str:
    """A target above the 2000-word length floor, full of dialogue."""
    filler = (
        "The room was quiet and the afternoon light fell across the "
        "floorboards in long pale bars while they spoke. "
    )
    return (_DIALOGUE_BLOCK + filler * 40) * 4


# A DISTINCT long baseline text (different speakers + lines), so it is a genuine baseline document
# rather than a content-duplicate of `_long_text()`. Needed since the content-fingerprint
# self-exclusion guard correctly drops a baseline file whose dialogue turns match the target's.
_OTHER_DIALOGUE_BLOCK = '''
"We should set out before the tide turns," Captain Rourke said.
"The men aren't ready, sir," Bell answered.
"Then make them ready," Rourke said.
"Aye, I'll see to it," said Bell.
"And check the forward hold again," Rourke added.
"It was checked at dawn, sir," Bell noted.
"Check it twice," Rourke insisted.
"As you wish, Captain," Bell muttered.
"We cannot lose another cargo to the damp," said Rourke.
"Understood, sir," Bell replied.
'''


def _other_long_text() -> str:
    """A distinct baseline above the 2000-word floor (different dialogue than the target)."""
    filler = (
        "Out on the water the light was hard and flat and the ropes "
        "creaked against the cleats as the swell moved beneath them. "
    )
    return (_OTHER_DIALOGUE_BLOCK + filler * 40) * 4


def _spacy_available() -> bool:
    return dva.HAS_SPACY


# ---- test_surface_registered -------------------------------


def test_surface_registered() -> None:
    """The audit's task_surface must already be a valid surface in
    both output_schema and claim_license — it reuses the EXISTING
    voice_coherence surface and adds none."""
    from output_schema import VALID_TASK_SURFACES  # type: ignore
    from claim_license import TASK_SURFACE_LABELS  # type: ignore

    assert dva.TASK_SURFACE == "voice_coherence"
    assert dva.TASK_SURFACE in VALID_TASK_SURFACES
    assert dva.TASK_SURFACE in TASK_SURFACE_LABELS


# ---- test_dialogue_extraction ------------------------------


def test_dialogue_extraction() -> None:
    """Quotes are extracted and tag-attributed to the right speakers
    via "said X" / "X said" / "X asked" dialogue tags."""
    turns = dva.extract_dialogue(_DIALOGUE_BLOCK)
    # All quoted spans are extracted.
    assert len(turns) == 11
    by_speaker: dict[str, list[dva.DialogueTurn]] = {}
    for t in turns:
        by_speaker.setdefault(t.speaker, []).append(t)
    assert "Eleanor" in by_speaker
    assert "Jack" in by_speaker
    # Eleanor's tagged lines (said/asked/continued) attribute to her.
    eleanor_texts = " ".join(t.text for t in by_speaker["Eleanor"])
    assert "abide" in eleanor_texts
    assert "principle" in eleanor_texts
    # A tag verb was captured for at least some turns.
    assert any(t.tag_verb for t in by_speaker["Eleanor"])
    assert any(t.tag_verb == "asked" for t in turns)


# ---- test_unattributed_bucketed ----------------------------


def test_unattributed_bucketed() -> None:
    """Dialogue with no resolvable tag is bucketed under the
    UNATTRIBUTED sentinel and NEVER force-attributed to a named
    speaker."""
    turns = dva.extract_dialogue(_DIALOGUE_BLOCK)
    unattr = [t for t in turns if t.speaker == dva.UNATTRIBUTED]
    # The "silence stretched" line has no dialogue tag.
    assert len(unattr) >= 1
    assert any("silence stretched" in t.text for t in unattr)
    # The unattributed bucket is profiled SEPARATELY and never enters
    # the named-character set.
    named, unattr_profile, _dropped = dva.build_profiles(turns, min_turns=2)
    assert dva.UNATTRIBUTED not in named
    assert unattr_profile is not None
    assert unattr_profile.speaker == dva.UNATTRIBUTED
    assert unattr_profile.n_turns >= 1
    # Named speakers carry no unattributed turns.
    for prof in named.values():
        assert prof.speaker != dva.UNATTRIBUTED


# ---- test_divergence_matrix_shape --------------------------


def test_divergence_matrix_shape() -> None:
    """The cross-character matrix is square (n_chars x n_chars), with
    None on the diagonal and a symmetric off-diagonal."""
    turns = dva.extract_dialogue(_DIALOGUE_BLOCK)
    named, _unattr, _dropped = dva.build_profiles(turns, min_turns=2)
    speakers, matrix = dva.divergence_matrix(named)
    n = len(speakers)
    assert n == 2  # Eleanor, Jack
    assert len(matrix) == n
    for row in matrix:
        assert len(row) == n
    # Diagonal is None; off-diagonal is a finite, symmetric distance.
    for i in range(n):
        assert matrix[i][i] is None
        for j in range(n):
            if i != j:
                assert matrix[i][j] is not None
                assert matrix[i][j] == matrix[j][i]
                assert matrix[i][j] >= 0.0
    # Two distinct registers should produce a positive divergence.
    assert matrix[0][1] > 0.0
    # converged_pairs is descriptive and bounded by the pair count.
    pairs = dva.converged_pairs(speakers, matrix)
    assert len(pairs) == 1
    assert {pairs[0]["speaker_a"], pairs[0]["speaker_b"]} == {
        "Eleanor", "Jack",
    }
    assert "divergence" in pairs[0]


# ---- test_claim_license_refuses_identity -------------------


def test_claim_license_refuses_identity() -> None:
    """The claim license must license per-character profiles + cross-
    character divergence, and REFUSE author-identity / AI-provenance /
    quality inference, and note the tag-heuristic attribution."""
    lic = dva._claim_license(
        n_characters=2,
        n_unattributed_turns=1,
        target_words=3000,
        has_baseline=False,
    )
    assert lic.task_surface == "voice_coherence"
    licenses = lic.licenses.lower()
    assert "per-character" in licenses
    assert "divergence" in licenses
    refuses = lic.does_not_license.lower()
    assert "identity" in refuses
    assert "provenance" in refuses
    assert "quality" in refuses
    assert "not a verdict" in refuses or "descriptive" in refuses
    # The tag-heuristic caveat is present.
    caveats = " ".join(lic.additional_caveats).lower()
    assert "tag heuristic" in caveats or "tag-heuristic" in caveats
    assert "coreference" in caveats
    assert "force-attributed" in caveats or "never force" in caveats


# ---- test_envelope_shape -----------------------------------


def test_envelope_shape(tmp_path) -> None:
    """The JSON envelope matches schema_version 1.0: required keys,
    correct surface/tool, results carrying the per-character profiles +
    divergence matrix + converged_pairs."""
    if not _spacy_available():
        if pytest is not None:
            pytest.skip("spaCy / en_core_web_sm not available")
        return
    target = tmp_path / "manuscript.md"
    target.write_text(_long_text(), encoding="utf-8")
    envelope = dva.run_audit(
        target_path=target,
        text=_long_text(),
        min_turns=2,
        baseline_dir=None,
    )
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "voice_coherence"
    assert envelope["tool"] == "dialogue_voice_audit"
    assert envelope["available"] is True
    assert envelope["claim_license"] is not None
    r = envelope["results"]
    assert "characters" in r
    assert "unattributed" in r
    assert "divergence_matrix" in r
    assert "converged_pairs" in r
    assert r["divergence_matrix"]["speakers"] == sorted(
        r["divergence_matrix"]["speakers"]
    )
    # The envelope is JSON-serializable.
    json.dumps(envelope, default=str)


def test_envelope_unavailable_without_spacy_is_clean(tmp_path, monkeypatch) -> None:
    """When spaCy is unavailable the audit returns available=False with
    a warning — never a traceback. Simulated by patching HAS_SPACY."""
    monkeypatch.setattr(dva, "HAS_SPACY", False)
    target = tmp_path / "m.md"
    target.write_text(_long_text(), encoding="utf-8")
    envelope = dva.run_audit(
        target_path=target,
        text=_long_text(),
        min_turns=2,
        baseline_dir=None,
    )
    assert envelope["available"] is False
    assert envelope["results"] == {}
    assert envelope["claim_license"] is None
    assert envelope["warnings"]
    assert "spacy" in " ".join(envelope["warnings"]).lower()


# ---- test_deterministic ------------------------------------


def test_deterministic() -> None:
    """Two runs over the same input produce byte-identical output."""
    # Extraction + profiling are deterministic without spaCy.
    turns_a = dva.extract_dialogue(_DIALOGUE_BLOCK)
    turns_b = dva.extract_dialogue(_DIALOGUE_BLOCK)
    assert [
        (t.speaker, t.text, t.tag_verb) for t in turns_a
    ] == [
        (t.speaker, t.text, t.tag_verb) for t in turns_b
    ]
    named_a, _u_a, _d_a = dva.build_profiles(turns_a, min_turns=2)
    named_b, _u_b, _d_b = dva.build_profiles(turns_b, min_turns=2)
    sp_a, mat_a = dva.divergence_matrix(named_a)
    sp_b, mat_b = dva.divergence_matrix(named_b)
    assert sp_a == sp_b
    assert mat_a == mat_b

    # Full envelope determinism (spaCy-gated).
    if not _spacy_available():
        return
    e1 = dva.run_audit(
        target_path=Path("m.md"), text=_long_text(),
        min_turns=2, baseline_dir=None,
    )
    e2 = dva.run_audit(
        target_path=Path("m.md"), text=_long_text(),
        min_turns=2, baseline_dir=None,
    )
    assert json.dumps(e1, default=str) == json.dumps(e2, default=str)


# ---- CLI smoke + baseline ----------------------------------


def test_cli_json_smoke(tmp_path) -> None:
    """End-to-end CLI run with --json produces a valid envelope (or a
    clean available=false envelope when spaCy is missing)."""
    target = tmp_path / "manuscript.md"
    target.write_text(_long_text(), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = dva.main([str(target), "--json", "--out", str(out)])
    assert rc == 0
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["task_surface"] == "voice_coherence"
    assert parsed["tool"] == "dialogue_voice_audit"


def test_baseline_dir(tmp_path) -> None:
    """--baseline-dir builds baseline profiles alongside the target;
    the target file is excluded from its own baseline."""
    if not _spacy_available():
        if pytest is not None:
            pytest.skip("spaCy / en_core_web_sm not available")
        return
    target = tmp_path / "chapter2.md"
    target.write_text(_long_text(), encoding="utf-8")
    base = tmp_path / "baseline"
    base.mkdir()
    # A DISTINCT baseline document (not a content-duplicate of the target): the content-fingerprint
    # self-exclusion guard would otherwise drop a chapter1 that carries the target's own dialogue.
    (base / "chapter1.md").write_text(_other_long_text(), encoding="utf-8")
    envelope = dva.run_audit(
        target_path=target,
        text=_long_text(),
        min_turns=2,
        baseline_dir=base,
    )
    assert envelope["available"] is True
    assert envelope["baseline"] is not None
    assert envelope["baseline"]["n_files"] == 1
    assert "baseline_divergence_matrix" in envelope["results"]
