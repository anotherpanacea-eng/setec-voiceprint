#!/usr/bin/env python3
"""dialogue_voice_audit.py — per-character dialogue-voice profiling +
cross-character divergence (spec 11, Round 2 of pov_voice_profile).

Where ``pov_voice_profile.py`` profiles per-POV *narration*, this audit
profiles per-character *dialogue*. Character-voice collapse — when an
author (or an AI revision) flattens distinct characters into one
register — shows up in spoken lines before it shows up in narration.

Method (spaCy-backed):

  1. Extract quoted spans from the manuscript.
  2. Find dialogue tags ("said X" / "X asked") adjacent to each quote
     and attribute the quote to the tagged speaker. Attribution is a
     **tag heuristic only** — there is no coreference engine here.
     Dialogue with no resolvable tag is bucketed under a dedicated
     ``<unattributed>`` speaker and is NEVER force-attributed.
  3. Per character, compute a dialogue-voice profile: contraction
     rate, mean turn length + variance, dialogue-tag verb diversity,
     vocative rate, interruption / trailing punctuation rate (— / …),
     and top function words + discourse markers.
  4. Build a cross-character pairwise **divergence matrix** over the
     per-character feature vectors, plus a ``converged_pairs`` list of
     the lowest-divergence pairs.

The ``converged_pairs`` list is **descriptive, not a verdict**: low
divergence may be intentional (two characters from the same milieu, a
chorus, a narrator quoting themselves) or symptomatic (voice collapse).
The writer's local read decides. The claim-license refuses author-
identity, AI-provenance, and quality inference.

When spaCy (or the ``en_core_web_sm`` model) is unavailable, the audit
emits ``available: false`` with an actionable warning rather than a
traceback — the same graceful-degradation convention the rest of the
framework uses for spaCy-only signals.

Usage:

    python3 scripts/dialogue_voice_audit.py MANUSCRIPT
    python3 scripts/dialogue_voice_audit.py MANUSCRIPT --json
    python3 scripts/dialogue_voice_audit.py MANUSCRIPT --baseline-dir DIR
    python3 scripts/dialogue_voice_audit.py MANUSCRIPT --out report.md

task_surface: voice_coherence (this is a voice-coherence audit — the
dialogue layer over the per-POV narration layer). Refuses the
classifier reading: convergence is craft signal, not provenance
evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import (  # type: ignore
    build_baseline_metadata,
    build_output,
)

# spaCy is loaded lazily and tolerantly. The model is only needed to
# tag dialogue-tag verbs and tokenize turns for POS-aware counts; the
# quote / tag extraction itself is regex. When spaCy is missing the
# audit reports available=False rather than degrading silently.
try:
    import spacy  # type: ignore

    try:
        _NLP = spacy.load("en_core_web_sm")
        HAS_SPACY = True
    except Exception:
        _NLP = None
        HAS_SPACY = False
except ImportError:
    _NLP = None
    HAS_SPACY = False


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "dialogue_voice_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 2000

# Sentinel speaker for dialogue we could not tag-attribute. Bucketed
# separately and reported on its own; NEVER force-attributed to a
# named character.
UNATTRIBUTED = "<unattributed>"

# Minimum turns a character needs before it gets a profile + enters
# the divergence matrix. Below this the per-character stats are noise.
DEFAULT_MIN_TURNS = 2

# Contractions: common English contracted forms. Used for the
# contraction-rate feature (a strong register marker — formal
# characters under-contract, casual ones over-contract).
_CONTRACTION_RE = re.compile(
    r"\b\w+['’](?:t|s|re|ve|ll|d|m|n)\b"
    r"|\b(?:can|won|don|isn|aren|wasn|weren|haven|hasn|hadn|didn|doesn|"
    r"shouldn|wouldn|couldn|mustn|needn|ain)['’]t\b",
    re.IGNORECASE,
)

# Discourse markers / interjections common at the head of spoken
# turns. Counted as a dialogue-specific texture feature.
DISCOURSE_MARKERS = {
    "well", "oh", "okay", "ok", "yeah", "yes", "no", "look", "listen",
    "now", "so", "anyway", "actually", "honestly", "really", "right",
    "sure", "hey", "please", "alright", "fine", "maybe", "perhaps",
    "indeed", "of course", "you know", "i mean", "i guess", "i think",
}

# Function words for the per-character distribution (Mosteller-Wallace
# core; mirrors variance_audit.FUNCTION_WORDS but kept local so this
# module has no hard dependency on that file's internals).
FUNCTION_WORDS = {
    "a", "about", "after", "again", "all", "am", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "him", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "just", "me", "more", "my", "no", "not", "now",
    "of", "off", "on", "one", "or", "our", "out", "over", "she",
    "should", "so", "some", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "too", "up", "us",
    "very", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "yes", "you", "your",
}

# Vocative cue words that, when followed by a capitalized token or
# direct-address pronoun, mark the line as addressing someone.
_VOCATIVE_NAME_RE = re.compile(
    r",\s*([A-Z][a-z]+)\s*[.!?,]"  # "..., John."
    r"|^\s*([A-Z][a-z]+)\s*,"      # "John, ..."
)

# Interruption / trailing punctuation: em dash and ellipsis (both
# unicode and ASCII triple-dot).
_INTERRUPT_RE = re.compile(r"—|––|--|…|\.\.\.")

_WORD_RE = re.compile(r"[A-Za-z']+")

# Curly + straight double quotes; curly + straight singles handled
# separately so we don't eat apostrophes inside contractions.
_QUOTE_OPEN = "“"
_QUOTE_CLOSE = "”"


# --------------- Quote + tag extraction ---------------------


@dataclass
class DialogueTurn:
    """One quoted span and its (heuristic) attribution."""

    text: str           # the quoted text, quotes stripped
    speaker: str        # attributed speaker, or UNATTRIBUTED
    tag_verb: str | None  # the dialogue-tag verb ("said", "asked"), if any
    attributed: bool    # True iff a speaker tag was resolved


# Dialogue-tag verbs we recognize. The attribution heuristic looks
# for these next to a name in the surrounding context. Kept broad but
# bounded — these are the speech-act verbs that conventionally carry a
# dialogue tag.
_TAG_VERBS = {
    "said", "say", "says", "asked", "ask", "asks", "replied", "reply",
    "answered", "answer", "shouted", "shout", "whispered", "whisper",
    "muttered", "mutter", "cried", "cry", "called", "call", "added",
    "add", "continued", "continue", "began", "begin", "exclaimed",
    "exclaim", "demanded", "demand", "murmured", "murmur", "snapped",
    "snap", "growled", "growl", "laughed", "laugh", "sighed", "sigh",
    "agreed", "agree", "insisted", "insist", "noted", "note", "warned",
    "warn", "offered", "offer", "wondered", "wonder", "groaned",
    "yelled", "yell", "remarked", "remark", "responded", "respond",
    "stated", "state", "told", "tell", "explained", "explain",
    "interrupted", "interrupt", "repeated", "repeat", "breathed",
    "hissed", "hiss", "barked", "bark", "countered", "counter",
}

# A name token: a capitalized word that isn't a sentence-initial
# function word. We treat single capitalized tokens (and simple
# Title-case bigrams) as candidate speaker names.
_NAME_TOKEN = r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"

# Tag patterns surrounding a quote. Each captures (verb, name) or
# (name, verb). Applied to the text immediately AFTER the close-quote
# and immediately BEFORE the open-quote.
_TAG_AFTER_PATTERNS = [
    # `," said John.` / `," John said.`
    re.compile(r"^[\s,]*(?P<verb>[a-z]+)\s+(?P<name>" + _NAME_TOKEN + r")\b"),
    re.compile(r"^[\s,]*(?P<name>" + _NAME_TOKEN + r")\s+(?P<verb>[a-z]+)\b"),
]
_TAG_BEFORE_PATTERNS = [
    # `John said, "` / `said John, "`
    re.compile(r"(?P<name>" + _NAME_TOKEN + r")\s+(?P<verb>[a-z]+)\s*[,:]\s*$"),
    re.compile(r"(?P<verb>[a-z]+)\s+(?P<name>" + _NAME_TOKEN + r")\s*[,:]\s*$"),
]


def _normalize_quotes(text: str) -> str:
    """Fold curly double quotes to straight so one regex handles both."""
    return text.replace(_QUOTE_OPEN, '"').replace(_QUOTE_CLOSE, '"')


def extract_dialogue(text: str) -> list[DialogueTurn]:
    """Extract quoted spans and tag-attribute each to a speaker.

    Deterministic. Walks double-quoted spans in document order. For
    each, inspects a bounded window of context after the close-quote
    (preferred) then before the open-quote for a "said X" / "X said"
    dialogue tag. Unresolvable quotes are bucketed under
    ``UNATTRIBUTED`` — never force-attributed.
    """
    norm = _normalize_quotes(text)
    turns: list[DialogueTurn] = []
    # Match balanced double-quote pairs. Non-greedy; a quote may not
    # span a blank line (paragraph break) — that guards against an
    # unbalanced quote swallowing the rest of the document.
    for m in re.finditer(r'"([^"\n]{1,1000}?)"', norm):
        quoted = m.group(1).strip()
        if not quoted:
            continue
        after = norm[m.end():m.end() + 60]
        before = norm[max(0, m.start() - 60):m.start()]
        speaker, verb = _resolve_tag(before, after)
        turns.append(DialogueTurn(
            text=quoted,
            speaker=speaker or UNATTRIBUTED,
            tag_verb=verb,
            attributed=speaker is not None,
        ))
    return turns


def _resolve_tag(before: str, after: str) -> tuple[str | None, str | None]:
    """Resolve a dialogue tag from the context windows.

    Prefers a tag immediately AFTER the quote (the conventional
    position), then falls back to BEFORE. Returns ``(speaker, verb)``
    or ``(None, None)`` when no tag verb is present. Requires the verb
    to be a known dialogue-tag verb so prose like `," she walked to`
    doesn't mis-attribute.
    """
    for pat in _TAG_AFTER_PATTERNS:
        mm = pat.match(after)
        if mm and mm.group("verb").lower() in _TAG_VERBS:
            return mm.group("name").strip(), mm.group("verb").lower()
    for pat in _TAG_BEFORE_PATTERNS:
        mm = pat.search(before)
        if mm and mm.group("verb").lower() in _TAG_VERBS:
            return mm.group("name").strip(), mm.group("verb").lower()
    return None, None


# --------------- Per-character profile ----------------------


@dataclass
class CharacterProfile:
    speaker: str
    n_turns: int
    n_words: int
    contraction_rate: float        # contractions per word
    mean_turn_length: float        # words per turn
    turn_length_variance: float
    tag_verb_diversity: float      # distinct tag verbs / tagged turns
    vocative_rate: float           # vocative-bearing turns / turns
    interruption_rate: float       # — / … bearing turns / turns
    top_function_words: list[tuple[str, float]]
    top_discourse_markers: list[tuple[str, float]]
    # Feature vector used for the divergence matrix (scalar features +
    # function-word relative frequencies in a shared space).
    feature_vector: dict[str, float] = field(default_factory=dict)


def _count_words(s: str) -> int:
    return len(_WORD_RE.findall(s))


def _has_vocative(turn_text: str) -> bool:
    """Heuristic vocative detection: ", Name." or "Name, ..." forms.

    spaCy NER would over-fire on capitalized non-names; this bounded
    regex keeps the feature deterministic and dependency-light. (The
    spaCy model is still required for the audit overall — for tag-verb
    lemma normalization in the diversity feature — but vocative
    detection is intentionally regex so it doesn't drift with model
    versions.)
    """
    return bool(_VOCATIVE_NAME_RE.search(turn_text))


def build_character_profile(
    speaker: str, turns: list[DialogueTurn], all_function_words: list[str],
) -> CharacterProfile:
    """Build the dialogue-voice profile for one speaker's turns."""
    n_turns = len(turns)
    turn_lengths = [_count_words(t.text) for t in turns]
    n_words = sum(turn_lengths)

    n_contractions = sum(
        len(_CONTRACTION_RE.findall(t.text)) for t in turns
    )
    contraction_rate = n_contractions / n_words if n_words else 0.0

    mean_len = statistics.mean(turn_lengths) if turn_lengths else 0.0
    var_len = (
        statistics.pvariance(turn_lengths) if len(turn_lengths) > 1 else 0.0
    )

    tag_verbs = [t.tag_verb for t in turns if t.tag_verb]
    tag_verb_diversity = (
        len(set(tag_verbs)) / len(tag_verbs) if tag_verbs else 0.0
    )

    n_vocative = sum(1 for t in turns if _has_vocative(t.text))
    vocative_rate = n_vocative / n_turns if n_turns else 0.0

    n_interrupt = sum(1 for t in turns if _INTERRUPT_RE.search(t.text))
    interruption_rate = n_interrupt / n_turns if n_turns else 0.0

    # Word distribution across all this speaker's turns.
    word_counts: Counter[str] = Counter()
    for t in turns:
        for w in _WORD_RE.findall(t.text.lower()):
            word_counts[w] += 1

    fw_freqs: dict[str, float] = {}
    for w in FUNCTION_WORDS:
        fw_freqs[w] = (word_counts.get(w, 0) / n_words) if n_words else 0.0
    top_fw = sorted(
        ((w, fw_freqs[w]) for w in FUNCTION_WORDS if fw_freqs[w] > 0),
        key=lambda kv: (-kv[1], kv[0]),
    )[:10]

    dm_counts: dict[str, float] = {}
    for marker in DISCOURSE_MARKERS:
        # Multi-word markers via substring; single-word via token.
        if " " in marker:
            c = sum(
                t.text.lower().count(marker) for t in turns
            )
        else:
            c = word_counts.get(marker, 0)
        if c:
            dm_counts[marker] = c / n_words if n_words else 0.0
    top_dm = sorted(
        dm_counts.items(), key=lambda kv: (-kv[1], kv[0]),
    )[:10]

    # Feature vector for divergence: scalar features (z-normalized
    # downstream) + function-word relative frequencies over the shared
    # vocabulary so every character lives in the same space.
    fv: dict[str, float] = {
        "contraction_rate": contraction_rate,
        "mean_turn_length": mean_len,
        "vocative_rate": vocative_rate,
        "interruption_rate": interruption_rate,
        "tag_verb_diversity": tag_verb_diversity,
    }
    for w in all_function_words:
        fv[f"fw::{w}"] = fw_freqs.get(w, 0.0)

    return CharacterProfile(
        speaker=speaker,
        n_turns=n_turns,
        n_words=n_words,
        contraction_rate=contraction_rate,
        mean_turn_length=mean_len,
        turn_length_variance=var_len,
        tag_verb_diversity=tag_verb_diversity,
        vocative_rate=vocative_rate,
        interruption_rate=interruption_rate,
        top_function_words=top_fw,
        top_discourse_markers=top_dm,
        feature_vector=fv,
    )


def build_profiles(
    turns: list[DialogueTurn], *, min_turns: int = DEFAULT_MIN_TURNS,
) -> tuple[dict[str, CharacterProfile], CharacterProfile | None, list[str]]:
    """Group turns by speaker and build per-character profiles.

    Returns ``(named_profiles, unattributed_profile, dropped)``. The
    unattributed bucket is profiled SEPARATELY and never enters the
    cross-character divergence matrix. ``dropped`` lists named speakers
    with fewer than ``min_turns`` turns (kept out of the matrix to
    avoid single-turn noise, but still reported in the per-character
    summary count).
    """
    by_speaker: dict[str, list[DialogueTurn]] = {}
    for t in turns:
        by_speaker.setdefault(t.speaker, []).append(t)

    # The shared function-word vocabulary is the full FUNCTION_WORDS
    # set, sorted for determinism, so every character's feature vector
    # has identical keys.
    all_fw = sorted(FUNCTION_WORDS)

    unattributed_profile: CharacterProfile | None = None
    if UNATTRIBUTED in by_speaker:
        unattributed_profile = build_character_profile(
            UNATTRIBUTED, by_speaker[UNATTRIBUTED], all_fw,
        )

    named: dict[str, CharacterProfile] = {}
    dropped: list[str] = []
    for speaker, sp_turns in by_speaker.items():
        if speaker == UNATTRIBUTED:
            continue
        if len(sp_turns) < min_turns:
            dropped.append(speaker)
            continue
        named[speaker] = build_character_profile(speaker, sp_turns, all_fw)
    return named, unattributed_profile, sorted(dropped)


# --------------- Cross-character divergence -----------------


def _vector_stats(
    vectors: list[dict[str, float]], keys: list[str],
) -> dict[str, tuple[float, float]]:
    """Per-key (mean, sd) across the character vectors. sd is the
    population sd; keys with zero variance get sd 0.0 (excluded from
    the z-normalized distance)."""
    stats: dict[str, tuple[float, float]] = {}
    n = len(vectors)
    for k in keys:
        vals = [v.get(k, 0.0) for v in vectors]
        mean = sum(vals) / n if n else 0.0
        if n > 1:
            sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / n)
        else:
            sd = 0.0
        stats[k] = (mean, sd)
    return stats


def _pair_divergence(
    a: dict[str, float], b: dict[str, float],
    keys: list[str], stats: dict[str, tuple[float, float]],
) -> float:
    """Mean absolute z-score difference between two feature vectors
    over the informative (non-zero-variance) keys. A Burrows-Delta-
    style descriptive distance: higher = more voice-distinct.
    Returns 0.0 when no key is informative."""
    informative = [k for k in keys if stats[k][1] > 0.0]
    if not informative:
        return 0.0
    total = 0.0
    for k in informative:
        mean, sd = stats[k]
        za = (a.get(k, 0.0) - mean) / sd
        zb = (b.get(k, 0.0) - mean) / sd
        total += abs(za - zb)
    return total / len(informative)


def divergence_matrix(
    profiles: dict[str, CharacterProfile],
) -> tuple[list[str], list[list[float | None]]]:
    """Symmetric pairwise divergence matrix over named characters.

    Returns ``(speakers, matrix)`` where ``speakers`` is the sorted
    speaker order and ``matrix[i][j]`` is the divergence between
    speakers i and j (``None`` on the diagonal — a character has no
    divergence from itself). Z-normalization is computed across the
    character vectors in the shared feature space.
    """
    speakers = sorted(profiles.keys())
    if not speakers:
        return [], []
    vectors = [profiles[s].feature_vector for s in speakers]
    keys = sorted(vectors[0].keys()) if vectors else []
    stats = _vector_stats(vectors, keys)

    matrix: list[list[float | None]] = []
    for i, si in enumerate(speakers):
        row: list[float | None] = []
        for j, sj in enumerate(speakers):
            if i == j:
                row.append(None)
            else:
                row.append(_pair_divergence(
                    profiles[si].feature_vector,
                    profiles[sj].feature_vector,
                    keys, stats,
                ))
        matrix.append(row)
    return speakers, matrix


def converged_pairs(
    speakers: list[str], matrix: list[list[float | None]], *, top: int = 5,
) -> list[dict[str, Any]]:
    """The lowest-divergence character pairs, ascending.

    DESCRIPTIVE, NOT A VERDICT. Low divergence may be intentional
    (shared milieu, deliberate chorus) or symptomatic (voice
    collapse). No threshold is applied and no flag is raised — the
    list simply surfaces the closest pairs for the writer to inspect.
    """
    pairs: list[dict[str, Any]] = []
    for i, si in enumerate(speakers):
        for j, sj in enumerate(speakers):
            if i >= j:
                continue
            d = matrix[i][j]
            if d is None:
                continue
            pairs.append({
                "speaker_a": si,
                "speaker_b": sj,
                "divergence": d,
            })
    pairs.sort(key=lambda p: (p["divergence"], p["speaker_a"], p["speaker_b"]))
    return pairs[:top]


# --------------- Baseline (optional) ------------------------


def aggregate_baseline(
    baseline_dir: Path, *, min_turns: int, target_path: Path | None,
) -> tuple[dict[str, CharacterProfile], int, list[Path], list[Path]]:
    """Walk a baseline directory and build per-character dialogue
    profiles over the union of its files. Returns ``(profiles,
    total_words, loaded, skipped)``. Self-overlap guard drops the
    target file if it lives under the baseline dir (same convention as
    construction_signature_audit)."""
    if not baseline_dir.exists():
        raise FileNotFoundError(
            f"Baseline directory not found: {baseline_dir}"
        )
    if not baseline_dir.is_dir():
        raise NotADirectoryError(
            f"--baseline-dir is not a directory: {baseline_dir}"
        )
    loaded: list[Path] = []
    skipped: list[Path] = []
    all_turns: list[DialogueTurn] = []
    total_words = 0
    target_resolved = target_path.resolve() if target_path else None

    for path in sorted(baseline_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".md", ".markdown", ".rst"}:
            skipped.append(path)
            continue
        if target_resolved is not None and path.resolve() == target_resolved:
            skipped.append(path)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            skipped.append(path)
            continue
        if not text.strip():
            skipped.append(path)
            continue
        loaded.append(path)
        total_words += _count_words(text)
        all_turns.extend(extract_dialogue(text))

    profiles, _unattr, _dropped = build_profiles(
        all_turns, min_turns=min_turns,
    )
    return profiles, total_words, loaded, skipped


# --------------- Claim license ------------------------------


def _claim_license(
    *,
    n_characters: int,
    n_unattributed_turns: int,
    target_words: int,
    has_baseline: bool,
) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Per-character dialogue-voice profiles (contraction rate, "
            "mean turn length + variance, dialogue-tag verb diversity, "
            "vocative rate, interruption / trailing-punctuation rate, "
            "top function words + discourse markers) and their cross-"
            "character divergence within this manuscript, plus a "
            "descriptive list of the lowest-divergence character pairs."
        ),
        does_not_license=(
            "Author identity, AI provenance, or any writing-quality "
            "judgment. The cross-character divergence and the "
            "converged-pairs list are DESCRIPTIVE, not a verdict: low "
            "divergence between two characters may be intentional "
            "(shared milieu, register, or a deliberate chorus) or "
            "symptomatic (voice collapse) — the writer's local read "
            "decides. No threshold is applied and no flag is raised."
        ),
        comparison_set={
            "mode": (
                "manuscript_internal_with_baseline" if has_baseline
                else "manuscript_internal"
            ),
            "n_characters_profiled": n_characters,
            "n_unattributed_turns": n_unattributed_turns,
            "target_words": target_words,
        },
        additional_caveats=[
            "Speaker attribution is a TAG HEURISTIC only — it matches "
            "\"said X\" / \"X asked\" dialogue tags adjacent to a "
            "quote. There is no coreference engine. Dialogue with no "
            "resolvable tag is bucketed under `<unattributed>` and is "
            "NEVER force-attributed to a named character; the "
            "unattributed bucket is profiled separately and excluded "
            "from the cross-character divergence matrix.",
            "Divergence is an uncalibrated descriptive distance "
            "(mean absolute z-score difference over a shared feature "
            "space). No corpus-wide convergence band has shipped — "
            "interpret pairwise values in relative terms within this "
            "manuscript.",
            "Dialogue-voice signals are most reliable when each "
            "character has many turns; characters below the turn floor "
            "are reported but kept out of the divergence matrix.",
        ],
        references=[
            "specs/11-dialogue-voice-audit.md",
        ],
    )


# --------------- Audit assembly -----------------------------


def _profile_to_dict(p: CharacterProfile) -> dict[str, Any]:
    return {
        "speaker": p.speaker,
        "n_turns": p.n_turns,
        "n_words": p.n_words,
        "contraction_rate": p.contraction_rate,
        "mean_turn_length": p.mean_turn_length,
        "turn_length_variance": p.turn_length_variance,
        "tag_verb_diversity": p.tag_verb_diversity,
        "vocative_rate": p.vocative_rate,
        "interruption_rate": p.interruption_rate,
        "top_function_words": [
            {"word": w, "rel_freq": f} for w, f in p.top_function_words
        ],
        "top_discourse_markers": [
            {"marker": w, "rel_freq": f} for w, f in p.top_discourse_markers
        ],
    }


def build_results(
    *,
    profiles: dict[str, CharacterProfile],
    unattributed: CharacterProfile | None,
    dropped: list[str],
    baseline_profiles: dict[str, CharacterProfile] | None,
) -> dict[str, Any]:
    speakers, matrix = divergence_matrix(profiles)
    pairs = converged_pairs(speakers, matrix)

    results: dict[str, Any] = {
        "n_characters": len(profiles),
        "speakers": speakers,
        "characters": {
            s: _profile_to_dict(profiles[s]) for s in speakers
        },
        "unattributed": (
            _profile_to_dict(unattributed) if unattributed is not None
            else None
        ),
        "dropped_speakers": dropped,
        "divergence_matrix": {
            "speakers": speakers,
            "matrix": matrix,
        },
        "converged_pairs": pairs,
    }
    if baseline_profiles is not None:
        b_speakers, b_matrix = divergence_matrix(baseline_profiles)
        results["baseline_characters"] = {
            s: _profile_to_dict(baseline_profiles[s]) for s in b_speakers
        }
        results["baseline_divergence_matrix"] = {
            "speakers": b_speakers,
            "matrix": b_matrix,
        }
    return results


def run_audit(
    *,
    target_path: Path,
    text: str,
    min_turns: int,
    baseline_dir: Path | None,
) -> dict[str, Any]:
    """Run the full audit and return the schema_version 1.0 envelope.

    Graceful degradation: if spaCy / the model is unavailable, returns
    an ``available=False`` envelope with an actionable warning rather
    than raising.
    """
    target_words = _count_words(text)

    if not HAS_SPACY:
        return build_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            target_path=target_path,
            target_words=target_words,
            baseline=None,
            results={},
            claim_license=None,
            available=False,
            warnings=[
                "spaCy with the en_core_web_sm model is required for "
                "the dialogue-voice audit. Install with: "
                "python -m spacy download en_core_web_sm "
                "(and `pip install spacy`). No result produced.",
            ],
        )

    if target_words < LENGTH_FLOOR_WORDS:
        return build_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            target_path=target_path,
            target_words=target_words,
            baseline=None,
            results={},
            claim_license=None,
            available=False,
            warnings=[
                f"Target is {target_words} words; below the "
                f"{LENGTH_FLOOR_WORDS}-word floor for a meaningful "
                f"dialogue-voice profile.",
            ],
        )

    turns = extract_dialogue(text)
    profiles, unattributed, dropped = build_profiles(
        turns, min_turns=min_turns,
    )

    baseline_profiles: dict[str, CharacterProfile] | None = None
    baseline_meta: dict[str, Any] | None = None
    warnings: list[str] = []
    if baseline_dir is not None:
        baseline_profiles, b_words, b_loaded, b_skipped = aggregate_baseline(
            baseline_dir, min_turns=min_turns, target_path=target_path,
        )
        baseline_meta = build_baseline_metadata(
            n_files=len(b_loaded),
            words=b_words,
            extra={"files_skipped_count": len(b_skipped)},
        )

    n_unattr_turns = (
        unattributed.n_turns if unattributed is not None else 0
    )
    if not profiles:
        warnings.append(
            "No named speakers met the turn floor; the divergence "
            "matrix is empty. Dialogue may be untagged (see the "
            "unattributed bucket) or the manuscript may have too "
            "little tagged dialogue."
        )

    results = build_results(
        profiles=profiles,
        unattributed=unattributed,
        dropped=dropped,
        baseline_profiles=baseline_profiles,
    )

    lic = _claim_license(
        n_characters=len(profiles),
        n_unattributed_turns=n_unattr_turns,
        target_words=target_words,
        has_baseline=baseline_profiles is not None,
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=lic,
        available=True,
        warnings=warnings,
        target_extra={
            "n_turns": len(turns),
            "n_unattributed_turns": n_unattr_turns,
            "spacy_available": HAS_SPACY,
        },
    )


# --------------- Markdown rendering -------------------------


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "∞"
        if abs(v) >= 1:
            return f"{v:.3f}"
        return f"{v:.4f}"
    return str(v)


def render_report(envelope: dict[str, Any]) -> str:
    target = envelope.get("target", {})
    lines: list[str] = [
        f"# Dialogue-voice audit — `{target.get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {target.get('words')}  ",
        f"**Turns:** {target.get('n_turns', 0)} "
        f"({target.get('n_unattributed_turns', 0)} unattributed)",
        "",
    ]
    if not envelope.get("available"):
        lines.append("_No dialogue-voice profile produced._")
        for w in envelope.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = envelope["results"]
    lines.append("## Per-character dialogue-voice profiles")
    lines.append("")
    lines.append(
        "| Character | Turns | Words | Contraction | Mean turn | "
        "Turn var | Tag-verb div | Vocative | Interrupt |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for s in r["speakers"]:
        c = r["characters"][s]
        lines.append(
            f"| `{s}` | {c['n_turns']} | {c['n_words']} | "
            f"{_fmt(c['contraction_rate'])} | "
            f"{_fmt(c['mean_turn_length'])} | "
            f"{_fmt(c['turn_length_variance'])} | "
            f"{_fmt(c['tag_verb_diversity'])} | "
            f"{_fmt(c['vocative_rate'])} | "
            f"{_fmt(c['interruption_rate'])} |"
        )
    lines.append("")

    unattr = r.get("unattributed")
    if unattr:
        lines.append(
            f"_Unattributed dialogue: {unattr['n_turns']} turns, "
            f"{unattr['n_words']} words — profiled separately, not in "
            f"the divergence matrix._"
        )
        lines.append("")
    if r.get("dropped_speakers"):
        lines.append(
            "_Below turn floor (kept out of the matrix): "
            + ", ".join(f"`{s}`" for s in r["dropped_speakers"]) + "._"
        )
        lines.append("")

    speakers = r["divergence_matrix"]["speakers"]
    matrix = r["divergence_matrix"]["matrix"]
    if len(speakers) >= 2:
        lines.append("## Cross-character divergence matrix")
        lines.append("")
        lines.append(
            "Pairwise descriptive distance (mean absolute z-score "
            "difference over the shared feature space). Higher = more "
            "voice-distinct. **Descriptive, not a verdict.**"
        )
        lines.append("")
        lines.append("| | " + " | ".join(f"`{s}`" for s in speakers) + " |")
        lines.append("|---" * (len(speakers) + 1) + "|")
        for i, si in enumerate(speakers):
            cells = [_fmt(matrix[i][j]) for j in range(len(speakers))]
            lines.append(f"| `{si}` | " + " | ".join(cells) + " |")
        lines.append("")

        pairs = r.get("converged_pairs", [])
        if pairs:
            lines.append("## Lowest-divergence pairs (descriptive)")
            lines.append("")
            lines.append(
                "The closest character pairs by divergence. Low "
                "divergence may be intentional or symptomatic — inspect "
                "locally. No flag is raised."
            )
            lines.append("")
            lines.append("| Character A | Character B | Divergence |")
            lines.append("|---|---|---:|")
            for p in pairs:
                lines.append(
                    f"| `{p['speaker_a']}` | `{p['speaker_b']}` | "
                    f"{_fmt(p['divergence'])} |"
                )
            lines.append("")

    rendered = envelope.get("claim_license_rendered")
    if rendered:
        lines.append(rendered)
        lines.append("")
    return "\n".join(lines) + "\n"


# --------------- CLI ----------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dialogue_voice_audit.py",
        description=(
            "Per-character dialogue-voice profiling + cross-character "
            "divergence. Extracts quoted dialogue, tag-attributes it to "
            "speakers (unattributed bucketed separately), profiles each "
            "character's spoken register, and reports a descriptive "
            "cross-character divergence matrix. Voice-coherence surface; "
            "no provenance / quality verdict."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "input", help="Path to the manuscript (.txt / .md / .rst).",
    )
    p.add_argument(
        "--baseline-dir",
        help=(
            "Directory of baseline files (e.g. the manuscript's other "
            "chapters, or a character's prior dialogue) to profile "
            "alongside the target."
        ),
    )
    p.add_argument(
        "--min-turns", type=int, default=DEFAULT_MIN_TURNS,
        help=(
            "Minimum tagged turns a named speaker needs to enter the "
            f"divergence matrix (default {DEFAULT_MIN_TURNS})."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")

    baseline_dir = (
        Path(args.baseline_dir).expanduser() if args.baseline_dir else None
    )
    try:
        envelope = run_audit(
            target_path=target_path,
            text=text,
            min_turns=args.min_turns,
            baseline_dir=baseline_dir,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        sys.stderr.write(f"--baseline-dir: {exc}\n")
        return 2

    out_text = (
        json.dumps(envelope, indent=2, default=str)
        if args.json else render_report(envelope)
    )
    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
