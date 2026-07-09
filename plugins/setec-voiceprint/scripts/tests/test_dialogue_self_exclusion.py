#!/usr/bin/env python3
"""Self-exclusion regression: a content-duplicate of the target's DIALOGUE planted in the baseline dir
under a DIFFERENT filename must be dropped before the baseline character profiles are built. Otherwise
the target pools its own per-character profiles into the baseline and collapses the divergence matrix
toward a false "characters converge" result. The path-only guard misses a copy at a different path;
the content-fingerprint guard closes it.

Sibling of the Codex self-exclusion sweep (idiolect_detector / originality_audit #278 /
rank_turbulence_audit #280). The matcher-aligned unit is the extracted TURN sequence, not the whole
file: profiles are built from ``extract_dialogue`` turns and NARRATION is ignored, so the fingerprint
hashes the ``(speaker, tag_verb, attributed, text)`` turn stream. A copy of the target's dialogue —
even re-wrapped in different narration — is turn-equivalent and self-excluded; a genuinely different
dialogue is KEPT. A text with no turns fingerprints to ``None`` (guard disabled — no over-exclusion).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dialogue_voice_audit as dva  # type: ignore


TARGET = (
    '"I won\'t do it," said Mary. "You have to," John replied. '
    '"Says who?" she asked. "Everyone," he answered. '
    '"Then everyone is wrong," Mary said. "Perhaps," John admitted.\n'
) * 3
OTHER = (
    '"Look at the sky," Anna whispered. "It is going to storm," Ben warned. '
    '"We should go inside," she urged. "Not yet," he said. '
    '"Please," Anna begged. "Fine," Ben agreed.\n'
) * 3


def _names(loaded):
    return {p.name for p in loaded}


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = dva._content_fingerprint(TARGET)
    profiles, words, loaded, skipped = dva.aggregate_baseline(
        bdir, min_turns=1, target_path=None, target_fingerprint=fp,
    )
    names = _names(loaded)
    assert "sneaky_copy.txt" not in names   # the target's own dialogue is dropped
    assert "genuine.txt" in names           # the genuinely-different dialogue is kept
    assert any(p.name == "sneaky_copy.txt" for p in skipped)


def test_same_dialogue_rewrapped_in_narration_is_excluded(tmp_path):
    # Narration is ignored by the turn matcher, so a copy wrapped in extra prose (that touches no
    # dialogue tag) extracts the SAME turns and must be self-excluded (fail-closed vs the matcher).
    rewrapped = "the long grey week wore on and nothing seemed to change at all.\n\n" + TARGET
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "rewrapped.txt").write_text(rewrapped, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    fp = dva._content_fingerprint(TARGET)
    profiles, words, loaded, skipped = dva.aggregate_baseline(
        bdir, min_turns=1, target_path=None, target_fingerprint=fp,
    )
    names = _names(loaded)
    assert "rewrapped.txt" not in names
    assert "genuine.txt" in names


def test_distinct_dialogue_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(
        '"Where did they go?" Clara asked. "North," Dan said. '
        '"Why north?" she pressed. "The maps," he shrugged.\n' * 3,
        encoding="utf-8",
    )
    fp = dva._content_fingerprint(TARGET)
    profiles, words, loaded, skipped = dva.aggregate_baseline(
        bdir, min_turns=1, target_path=None, target_fingerprint=fp,
    )
    assert _names(loaded) == {"a.txt", "b.txt"}


def test_no_dialogue_target_disables_content_guard(tmp_path):
    # A narration-only target has no turns -> fingerprint None -> the guard must NOT mass-exclude
    # every narration-only baseline file.
    assert dva._content_fingerprint("Just plain narration, no quotes anywhere here.") is None
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "plain.txt").write_text("Plain narration with no dialogue at all in it.", encoding="utf-8")
    profiles, words, loaded, skipped = dva.aggregate_baseline(
        bdir, min_turns=1, target_path=None, target_fingerprint=None,
    )
    assert _names(loaded) == {"plain.txt"}
