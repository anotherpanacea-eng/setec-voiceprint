#!/usr/bin/env python3
"""enthymeme_gapflag.py — model-free structural enthymeme (suppressed-premise) LOCATION flags.

ArgScope M1 (spec ``specs/32-deepa2-enthymeme-gapflag.md``). The stdlib, location-first
sibling of ``warrant_probe`` under the SAME ``argument_pattern_scan`` surface. Where
``warrant_probe`` asks an LLM judge the Toulmin critical questions about a claim's warrant
*coverage* (present/partial/absent), this is a **deterministic, model-free** detector that
points at *where* a warrant is plausibly elided: a conclusion-marked sentence whose
inferential support in the local window is **not bridged** by an explicit warrant/connective
marker — the enthymematic JUMP.

It surfaces; it NEVER authors. M1 emits a candidate suppressed-premise LOCATION (a span
pointer + the structural evidence for the flag), never the reconstructed premise text. The
DeepA2 generative reconstruction that *authors* the missing premise (Betz & Richardson 2021,
"DeepA2: A Modular Framework for Deep Argument Analysis with Pretrained Neural Text2Text
Language Models", arXiv:2110.01509) is the strictly-gated M2 — out of scope here.

POSTURE — load-bearing, non-negotiable
--------------------------------------
This surface flags CANDIDATE locations for a human; it NEVER authors the missing premise and
NEVER rules the argument unsound / incomplete / fallacious.

- No verdict, enforced in the DATA SHAPE: the deliverable is ``enthymeme_gap_flags``, each a
  ``{candidate_type: "suppressed_premise", sentence_index, span_text, jump_evidence}``. No
  ``verdict`` / ``soundness`` / ``unsound`` / ``incomplete`` / ``quality`` / ``score`` / ``*_score``
  key; no aggregate verdict; a recursive walk over ``results`` confirms their absence.
- A flagged jump is frequently legitimate — most arguments leave most warrants implicit because
  they are shared with the reader (the ``warrant_probe`` posture: an absent warrant is a gap to
  examine, not a flaw). A high flag count is NOT a quality signal.
- NEVER auto-fills the missing premise: no ``reconstructed_premise`` / ``suggested_premise`` /
  ``filled_premise`` or any generated-text key anywhere in ``results``.
- Never-selects: flags are emitted in document order; nothing is ranked / argmax'd / scored.
- ``gap_density`` ships as a VALUE + a PROVISIONAL band + ``calibration_status: "uncalibrated"``,
  never a thresholded pass/fail — and a higher rate is explicitly NOT "worse".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "argument_pattern_scan"  # REUSED (sibling of warrant_probe / fallacy_scan)
TOOL_NAME = "enthymeme_gapflag"
SCRIPT_VERSION = "1.0"
METHOD_VERSION = "enthymeme_gapflag_structural_v1"
MARKER_VERSION = "enthymeme_markers_v1"

# Length policy (warrant_probe parity): below HARD_MIN_WORDS -> bad_input; below MIN_WORDS (or
# with no inferential markers at all) -> a SOFT register caveat, never a hard abstain.
HARD_MIN_WORDS = 25
MIN_WORDS = 120

# Ground window: the local lookback over which a conclusion's grounds and the warrant bridge are
# sought. Bounded by the paragraph, capped at GROUND_WINDOW preceding sentences. PROVISIONAL.
GROUND_WINDOW = 3

# Tautology guard: a conclusion that merely restates its grounds (content-token Jaccard at/above
# this ceiling) is an echo, not a suppressed-premise jump — NOT flagged. PROVISIONAL constant.
CONTENT_OVERLAP_CEILING = 0.6

# gap_density band edges (flags per inferential step). PROVISIONAL, operator-side, NOT a gate.
# A higher rate is NOT "worse" — the band is descriptive ("where this rate sits").
BAND_EDGES = {"low": 0.15, "high": 0.65}
BAND_LABELS = ("sparse", "typical", "dense")

_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Sentence segmentation: split on terminal punctuation followed by whitespace. Deterministic and
# stdlib; not a parser. Carries each sentence's paragraph index for human navigation.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# --- Marker lexicons: fixed, SETEC-internal, versioned (MARKER_VERSION). Markers, NOT a learned
# classifier — presence/absence is structural, which keeps M1 deterministic + Goodhart-free. ---

# Condition 1: a conclusion is asserted (the sentence opens with / contains one of these).
_CONCLUSION_MARKERS = (
    "therefore", "thus", "hence", "consequently", "so", "it follows",
    "which shows", "which proves", "clearly", "obviously", "in conclusion",
    "as a result", "accordingly", "ergo",
)

# Condition 2: a warrant / inferential bridge is present (anywhere in the conclusion + ground
# window). Presence => the link is STATED => NOT an enthymeme => not flagged.
#
# These are RELIABLE inferential connectives — subordinating conjunctions and multi-word phrases
# whose presence reliably signals a stated warrant. We deliberately EXCLUDE high-frequency bare
# words that double as non-inferential prepositions/conjunctions ("for", "as", "if") because they
# match non-warrant uses ("toxic chemicals for years", "the best choice for the city") and would
# silently SUPPRESS legitimate enthymeme flags — the dominant false-negative for a marker-only
# detector. The multi-word forms ("as a rule", "as a result"-adjacent, "if ... then" as a paired
# warrant) are matched via their unambiguous heads below; bare "for"/"as"/"if" are not warrant
# evidence on their own.
_WARRANT_MARKERS = (
    "because", "since", "given that", "the reason is", "the reason being",
    "on the grounds that", "it follows from", "follows from the fact",
    "whenever", "in general", "as a rule", "due to", "owing to",
    "in light of", "by virtue of", "inasmuch as", "insofar as", "for the reason",
)

# English stopwords for the content-overlap (tautology) guard. stdlib set, fixed.
_STOPWORDS = frozenset((
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in",
    "on", "at", "by", "for", "with", "as", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those", "we", "you",
    "they", "he", "she", "i", "not", "no", "do", "does", "did", "have", "has",
    "had", "will", "would", "can", "could", "should", "may", "might", "must",
    "from", "into", "than", "such", "which", "who", "what", "there", "their",
    "them", "our", "us", "all", "any", "more", "most", "some", "very", "also",
))


def _norm(text: str) -> str:
    return text.lower()


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_sentences(paragraph: str) -> list[str]:
    """Deterministic stdlib sentence split within a paragraph (not a parser)."""
    raw = _SENT_SPLIT_RE.split(paragraph.strip())
    return [s.strip() for s in raw if s.strip()]


def segment_sentences(text: str) -> list[dict[str, Any]]:
    """Walk paragraphs -> sentences, retaining a global 0-based sentence_index and the
    paragraph_index for human navigation (warrant_probe parity). Deterministic."""
    out: list[dict[str, Any]] = []
    s_idx = 0
    for p_idx, para in enumerate(split_paragraphs(text)):
        for sent in _split_sentences(para):
            out.append({
                "sentence_index": s_idx,
                "paragraph_index": p_idx,
                "text": sent,
            })
            s_idx += 1
    return out


def _content_tokens(text: str) -> set[str]:
    """Stopword-filtered lowercase content tokens — the set the tautology guard compares."""
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _find_conclusion_marker(sentence: str) -> str | None:
    """The first conclusion marker present in the sentence (document-order over the fixed
    lexicon), or None. Word-boundaried so 'sole'/'soak' do not match the marker 'so'."""
    low = _norm(sentence)
    for marker in _CONCLUSION_MARKERS:
        # markers may contain spaces (e.g. "it follows"); a \b-anchored search matches them
        # without false hits inside longer words.
        if re.search(r"(?<![A-Za-z])" + re.escape(marker) + r"(?![A-Za-z])", low):
            return marker
    return None


def _has_warrant_bridge(window_text: str) -> bool:
    """True iff a reliable warrant / inferential-bridge marker is present anywhere in the window.
    Each marker is matched word-boundaried so it cannot fire inside a longer word."""
    low = _norm(window_text)
    for marker in _WARRANT_MARKERS:
        if re.search(r"(?<![A-Za-z])" + re.escape(marker) + r"(?![A-Za-z])", low):
            return True
    return False


def _is_inferential_step(sentence: str, *, is_paragraph_terminal: bool,
                         has_prior_ground: bool) -> tuple[bool, str | None]:
    """Whether this sentence is a conclusion-marked or terminal-assertion inferential step.

    Returns (is_step, conclusion_marker). conclusion_marker is the matched marker text, or the
    literal "terminal-assertion" for the classic implicit-conclusion shape (a paragraph-terminal
    assertion preceded by >= 1 ground sentence). Marker-bearing wins over terminal."""
    marker = _find_conclusion_marker(sentence)
    if marker is not None:
        return True, marker
    if is_paragraph_terminal and has_prior_ground:
        return True, "terminal-assertion"
    return False, None


def detect_enthymemes(text: str, *, ground_window: int = GROUND_WINDOW,
                      content_overlap_ceiling: float = CONTENT_OVERLAP_CEILING,
                      include_terminal: bool = True) -> dict[str, Any]:
    """Walk the sentences and flag candidate suppressed-premise LOCATIONS.

    A flag is raised when ALL hold over the local window (this is the *evidence*, not a ruling):
      1. a conclusion is asserted (a conclusion marker, OR a paragraph-terminal assertion after
         >= 1 ground sentence) -- the matched marker / "terminal-assertion" is recorded;
      2. NO warrant bridge is present in the window spanning the conclusion + its preceding
         ground sentence(s) within the same paragraph;
      3. the jump spans distinct content -- content-token Jaccard between the conclusion and its
         grounds is BELOW the ceiling (a pure restatement is a tautological echo, not a jump).

    Deterministic, stdlib only. Flags are emitted in document (sentence_index) order; nothing is
    ranked or scored. Returns the value-level ``results`` payload."""
    sentences = segment_sentences(text)
    n_sentences = len(sentences)

    # paragraph -> ordered list of its sentence positions (index into `sentences`)
    para_members: dict[int, list[int]] = {}
    for pos, s in enumerate(sentences):
        para_members.setdefault(s["paragraph_index"], []).append(pos)

    flags: list[dict[str, Any]] = []
    n_conclusion_markers = 0
    n_terminal_assertions = 0
    n_warrant_bridges = 0
    n_inferential_steps = 0

    for pos, s in enumerate(sentences):
        members = para_members[s["paragraph_index"]]
        within = members.index(pos)              # position of this sentence within its paragraph
        is_terminal = within == len(members) - 1
        prior_positions = members[:within]       # ground candidates = earlier sentences, same para
        has_prior_ground = len(prior_positions) > 0

        is_step, conclusion_marker = _is_inferential_step(
            s["text"], is_paragraph_terminal=is_terminal, has_prior_ground=has_prior_ground)

        # Honor --no-include-terminal: a terminal-assertion-only step is suppressed entirely.
        if is_step and conclusion_marker == "terminal-assertion" and not include_terminal:
            is_step = False
            conclusion_marker = None

        if not is_step:
            continue

        n_inferential_steps += 1
        if conclusion_marker == "terminal-assertion":
            n_terminal_assertions += 1
        else:
            n_conclusion_markers += 1

        # Ground window: up to `ground_window` preceding sentences, bounded by the paragraph.
        # NB: ground_window == 0 must yield an EMPTY window — `prior_positions[-0:]` is the whole
        # list (Python `-0 == 0`), so guard the zero case explicitly.
        ground_positions = prior_positions[-ground_window:] if ground_window > 0 else []
        ground_indices = [sentences[gp]["sentence_index"] for gp in ground_positions]

        # Condition 2: warrant bridge anywhere in the conclusion + ground window.
        window_texts = [sentences[gp]["text"] for gp in ground_positions] + [s["text"]]
        window_text = " ".join(window_texts)
        warrant_present = _has_warrant_bridge(window_text)
        if warrant_present:
            n_warrant_bridges += 1
            continue  # the link is STATED -> not an enthymeme

        # Condition 3: distinct content (tautology guard). With no grounds, overlap is 0.0.
        ground_tokens: set[str] = set()
        for gp in ground_positions:
            ground_tokens |= _content_tokens(sentences[gp]["text"])
        concl_tokens = _content_tokens(s["text"])
        overlap = _jaccard(concl_tokens, ground_tokens)
        if overlap >= content_overlap_ceiling:
            continue  # restatement, not a suppressed-premise jump

        flags.append({
            "candidate_type": "suppressed_premise",
            "sentence_index": s["sentence_index"],
            "paragraph_index": s["paragraph_index"],
            "span_text": s["text"],
            "jump_evidence": {
                "conclusion_marker": conclusion_marker,
                "warrant_bridge_present": False,
                "ground_window_sentence_indices": ground_indices,
                "content_overlap_jaccard": round(overlap, 6),
            },
            # NOTE: there is intentionally NO reconstructed / suggested / filled premise key.
            # M1 emits WHERE the warrant looks elided, never the authored premise (that is M2).
        })

    n_flags = len(flags)
    gap_value = (n_flags / n_inferential_steps) if n_inferential_steps else 0.0
    band = _gap_density_band(gap_value)

    return {
        "method_version": METHOD_VERSION,
        "marker_version": MARKER_VERSION,
        "enthymeme_gap_flags": flags,
        "gap_density": {
            "value": round(gap_value, 6),
            "n_flags": n_flags,
            "n_inferential_steps": n_inferential_steps,
            # Descriptive band over the rate's OWN axis. PROVISIONAL, operator-side, NOT a gate;
            # a higher rate is NOT "worse". Ships uncalibrated (no shipped operating point).
            "band": band,
            "band_edges": dict(BAND_EDGES),
            "calibration_status": "uncalibrated",
        },
        "marker_tally": {
            "conclusion_markers": n_conclusion_markers,
            "terminal_assertions": n_terminal_assertions,
            "warrant_bridges": n_warrant_bridges,
        },
        "n_flags": n_flags,
        "n_sentences": n_sentences,
        "n_paragraphs": len(para_members),
        "ground_window": ground_window,
        "content_overlap_ceiling": content_overlap_ceiling,
        "include_terminal": include_terminal,
        "assumptions": {
            "method": "marker-based structural enthymeme detection (DeepA2 split, "
                      "arXiv:2110.01509); M1 detects the LOCATION, M2 authors the premise",
            "orientation": "a flag marks WHERE a warrant looks elided — NOT a ruling that a "
                           "premise IS missing, NOT a soundness/completeness verdict; a higher "
                           "gap_density is NOT 'worse' (implicit warrants are normal and often "
                           "legitimate)",
            "marker_prior": "detection is a marker-based structural prior, not a parser: it will "
                            "miss unmarked non-terminal conclusions and over-flag stylistic "
                            "'therefore's. The surface never adjudicates — a human is the "
                            "authority. English-only marker lexicons for v1.",
            "never_authors": "M1 emits the location + the structural evidence, never the "
                             "reconstructed premise text (that is the gated M2's deliverable).",
        },
    }


def _gap_density_band(value: float) -> str:
    """Descriptive band over the gap-density rate's OWN axis. NOT a verdict; PROVISIONAL edges.
    A higher band is NOT 'worse' — it is descriptive ("where this rate sits")."""
    if value < BAND_EDGES["low"]:
        return "sparse"
    if value < BAND_EDGES["high"]:
        return "typical"
    return "dense"


def register_warnings(text: str, n_words: int, results: dict[str, Any]) -> list[str]:
    """Soft caveats (warrant_probe parity), never a hard abstain."""
    out: list[str] = []
    if n_words < MIN_WORDS:
        out.append(
            f"Passage is short ({n_words} words, below ~{MIN_WORDS}); an enthymeme scan over "
            f"so little argument is low-confidence."
        )
    tally = results["marker_tally"]
    if (tally["conclusion_markers"] + tally["terminal_assertions"]
            + tally["warrant_bridges"]) == 0:
        out.append(
            "No inferential markers (conclusion connectives, warrant bridges, or "
            "terminal-assertion shapes) detected — the passage may not be argument-shaped "
            "nonfiction; treat any flags as low-confidence and check the register."
        )
    out.append(
        "Marker-based structural prior, not a parser: misses unmarked non-terminal conclusions, "
        "over-flags stylistic connectives, and is English-only (v1). A flag is a candidate "
        "LOCATION for a human, never a ruling that a premise is missing."
    )
    return out


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The LOCATION of candidate enthymematic JUMPS in an argument-shaped nonfiction "
            "passage: conclusion-marked (or paragraph-terminal-assertion) sentences whose "
            "inferential support in the local window is NOT bridged by an explicit warrant / "
            "connective marker and whose content is not a mere restatement of the grounds. Each "
            "flag is a candidate suppressed-premise LOCATION — a span pointer plus the structural "
            "evidence (the matched conclusion marker, the absence of a warrant bridge, the ground "
            "window, the content-overlap value) — emitted in document order for a human to "
            "examine (DeepA2 split, Betz & Richardson 2021, arXiv:2110.01509)."
        ),
        "does_not_license": (
            "Does NOT author, fill, suggest, or reconstruct the missing/suppressed premise — M1 "
            "emits WHERE a warrant looks elided, never the premise text; the generative DeepA2 "
            "reconstruction is a separate, gated M2. Does NOT license any 'the argument is "
            "incomplete / unsound / weak / fallacious' determination, nor a completeness / "
            "soundness / quality label, score, or aggregate — and emits none. A flagged jump is "
            "frequently legitimate: most arguments leave most warrants implicit because they are "
            "shared with the reader, so a higher gap_density is NOT 'worse'. Absence of flags "
            "does NOT mean every warrant is stated; presence of flags does NOT mean the argument "
            "is bad. Marker-based structural prior, not a parser (English-only, v1); thresholds "
            "are operator-side / PROVISIONAL and the surface ships `uncalibrated` — it emits no "
            "verdict and does not substitute for a human reading the argument in context."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:        # invalid UTF-8 is bad input, not a crash
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), reason=f"cannot read --target: {e}",
            reason_category="bad_input")

    n_words = count_words(text)
    if n_words < HARD_MIN_WORDS:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=n_words,
            reason=f"target too short ({n_words} words, need >= {HARD_MIN_WORDS}) — no "
                   f"argument to scan.", reason_category="bad_input")

    results = detect_enthymemes(
        text, ground_window=args.ground_window,
        content_overlap_ceiling=args.content_overlap_ceiling,
        include_terminal=not args.no_include_terminal)

    warnings = register_warnings(text, n_words, results)
    results["register_warnings"] = warnings
    results["calibration_status"] = "uncalibrated"

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=n_words, baseline=None,
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="Path to the target text (UTF-8).")
    ap.add_argument("--ground-window", type=int, default=GROUND_WINDOW,
                    help=f"Preceding sentences (within the paragraph) treated as the conclusion's "
                         f"grounds (default {GROUND_WINDOW}). PROVISIONAL.")
    ap.add_argument("--content-overlap-ceiling", type=float, default=CONTENT_OVERLAP_CEILING,
                    help=f"Conclusion/grounds content-token Jaccard at/above which a flag is "
                         f"suppressed as a tautological echo (default {CONTENT_OVERLAP_CEILING}). "
                         "PROVISIONAL.")
    ap.add_argument("--no-include-terminal", action="store_true",
                    help="Suppress terminal-assertion (unmarked implicit-conclusion) flags; only "
                         "flag explicit conclusion-marker jumps. Cuts false positives in "
                         "narrative-leaning prose.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.ground_window < 0:
        sys.stderr.write("[enthymeme_gapflag] --ground-window must be >= 0\n")
        return 2
    if not 0.0 <= args.content_overlap_ceiling <= 1.0:
        sys.stderr.write("[enthymeme_gapflag] --content-overlap-ceiling must be in [0.0, 1.0]\n")
        return 2

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
