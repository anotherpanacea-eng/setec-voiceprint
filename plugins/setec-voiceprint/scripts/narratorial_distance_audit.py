#!/usr/bin/env python3
"""narratorial_distance_audit.py — descriptive narratorial-distance / FID profile.

A descriptive craft instrument for literary fiction: it measures *where the
narration sits* relative to a character's consciousness — **close** (free-indirect
discourse: high perception/cognition-verb density, proximal deixis, evaluative
colour) versus **distant** (low) — and reports how that distance moves across the
document (the "distance trajectory").

This is a developmental-editing surface, adjacent to `pov_voice_profile`. It is
deliberately **not** a voice-identity, authorship, AI-provenance, or quality
instrument. Free-indirect-discourse (FID) detection here is a *heuristic signal* —
past-tense narration carrying proximal deixis with no quotation/speech-tag — not a
parse of literary intent. The claim-license refuses authorship / AI / quality
inference and emits **no band and no verdict**, only measurements.

Method (spaCy POS/dep + lemma, per window):

  - **Pronoun anchoring** — ratio of 3rd-person pronouns (he/she/they/...) plus
    proximal demonstratives among all personal/demonstrative pronoun anchors.
    Close narration leans 3rd-person + proximal; distant narration is flatter.
  - **Perception/cognition-verb density** — per-1k-word rate of consciousness
    verbs (saw / felt / knew / wondered / ... — a default lemma set, overridable
    with `--verb-lexicon`). The core FID tell: the narrator inhabits a character's
    sensing/thinking.
  - **Deictic anchoring** — proximal (here/now/this/that/today/...) vs. distal
    (there/then/...) deixis counts + a proximal share. Close narration uses
    proximal deixis as if standing inside the scene.
  - **Evaluative-adjective density** — per-1k-word rate of evaluative adjectives
    (a default lemma set: beautiful / terrible / strange / ...): the character's
    colour leaking into the narration.
  - **FID heuristic score** — a 0..1 blend of (past-tense narration) +
    (proximal-deixis presence) + (perception-verb presence) + (absence of
    quotation / speech-tag). High where narration reads as free-indirect.

Outputs (JSON envelope `results`): per-window distance features, an overall
close/distant **distribution**, and a **distance trajectory** series across
document position. Markdown report by default.

Graceful degradation: if spaCy or `en_core_web_sm` is unavailable, the script
emits a clean envelope with `available=False` and an explanatory warning — never
a traceback.

Usage:

    python3 scripts/narratorial_distance_audit.py MANUSCRIPT.txt
    python3 scripts/narratorial_distance_audit.py MANUSCRIPT.txt --json
    python3 scripts/narratorial_distance_audit.py MANUSCRIPT.txt --window-strategy chapter
    python3 scripts/narratorial_distance_audit.py MANUSCRIPT.txt --verb-lexicon verbs.txt
    python3 scripts/narratorial_distance_audit.py MANUSCRIPT.txt --out report.md
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "narratorial_distance"
TOOL_NAME = "narratorial_distance_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 1500

# Lazy spaCy loader, mirroring variance_audit.py's HAS_SPACY/_NLP idiom.
try:
    import spacy  # type: ignore

    try:
        _NLP = spacy.load("en_core_web_sm")
        HAS_SPACY = True
    except Exception:  # model not installed
        HAS_SPACY = False
        _NLP = None
except ImportError:
    HAS_SPACY = False
    _NLP = None


_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)

# --------------- Lexicons (defaults; --verb-lexicon overrides verbs) ---------

# Perception / cognition verbs (lemmas). The core FID signal: the narration
# inhabits a character's sensing and thinking. Default set; register-tunable.
DEFAULT_PERCEPTION_VERBS: frozenset[str] = frozenset({
    # perception
    "see", "saw", "watch", "hear", "listen", "feel", "smell", "taste", "notice",
    "observe", "glimpse", "sense", "perceive",
    # cognition / affect
    "know", "think", "wonder", "realize", "realise", "remember", "recall",
    "believe", "imagine", "suppose", "understand", "doubt", "decide", "consider",
    "recognize", "recognise", "forget", "hope", "fear", "want", "wish", "expect",
    "guess", "assume", "dread", "long",
})

# Proximal deixis — narration anchored *inside* the scene (close).
PROXIMAL_DEIXIS: frozenset[str] = frozenset({
    "here", "now", "this", "these", "today", "tonight", "currently",
    "presently", "ago", "yesterday", "tomorrow",
})

# Distal deixis — narration anchored at a remove (distant / framed).
DISTAL_DEIXIS: frozenset[str] = frozenset({
    "there", "then", "that", "those", "yonder", "formerly",
})

# Evaluative adjectives (lemmas) — a character's colour leaking into narration.
DEFAULT_EVALUATIVE_ADJECTIVES: frozenset[str] = frozenset({
    "beautiful", "terrible", "wonderful", "awful", "lovely", "horrible",
    "strange", "odd", "ugly", "gorgeous", "hideous", "splendid", "dreadful",
    "marvelous", "marvellous", "magnificent", "wretched", "pathetic",
    "exquisite", "monstrous", "delightful", "ghastly", "glorious", "sublime",
    "absurd", "ridiculous", "pitiful", "charming", "repulsive", "vile",
    "stunning", "appalling", "grotesque", "extraordinary", "remarkable",
    "perfect", "disgusting", "magical", "miserable", "sad", "happy",
})

# 3rd-person personal pronouns (lowercased surface forms).
THIRD_PERSON_PRONOUNS: frozenset[str] = frozenset({
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "they", "them", "their", "theirs", "themselves", "themself",
    "it", "its", "itself",
})

# 1st/2nd-person personal pronouns — the non-3rd anchor pool.
OTHER_PERSON_PRONOUNS: frozenset[str] = frozenset({
    "i", "me", "my", "mine", "myself",
    "we", "us", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
})

# Speech / dialogue tag verbs (lemmas) — their presence argues *against* FID
# (the line is reported/tagged speech, not free-indirect narration).
SPEECH_TAG_VERBS: frozenset[str] = frozenset({
    "say", "ask", "reply", "answer", "tell", "shout", "whisper", "mutter",
    "exclaim", "declare", "respond", "add", "cry", "call", "murmur", "demand",
})

_QUOTE_CHARS = "\"“”‘’'«»"


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_1k(n: int, words: int) -> float:
    return round(n / words * 1000, 2) if words else 0.0


def _round(x: float | None, ndigits: int = 4) -> float | None:
    return round(x, ndigits) if x is not None else None


def load_verb_lexicon(path: str | None) -> tuple[frozenset[str], bool]:
    """Return (verb_lemmas, is_custom). Custom file: one lemma per line;
    blank lines and `#` comments ignored. Lemmas are lowercased."""
    if not path:
        return DEFAULT_PERCEPTION_VERBS, False
    verbs: set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        verbs.add(line.lower())
    if not verbs:
        return DEFAULT_PERCEPTION_VERBS, False
    return frozenset(verbs), True


# --------------- Windowing ---------------------------------------------------

_CHAPTER_RE = re.compile(
    r"^\s*(chapter\b.*|part\b.*|[IVXLCDM]+\.?\s*$|\d+\.?\s*$)",
    re.IGNORECASE,
)


def split_windows(text: str, strategy: str) -> list[str]:
    """Split the text into windows. `paragraph`: blank-line-delimited blocks.
    `chapter`: split on chapter/part headings (falling back to paragraphs when
    no headings are found). Deterministic."""
    if strategy == "chapter":
        return _split_chapters(text)
    return _split_paragraphs(text)


def _split_paragraphs(text: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", text)
    return [b.strip() for b in blocks if b.strip()]


def _split_chapters(text: str) -> list[str]:
    lines = text.splitlines()
    chapters: list[list[str]] = []
    current: list[str] = []
    found_heading = False
    for line in lines:
        if _CHAPTER_RE.match(line) and len(line.strip()) <= 60:
            found_heading = True
            if current:
                chapters.append(current)
            current = []
            continue
        current.append(line)
    if current:
        chapters.append(current)
    if not found_heading:
        # No chapter headings detected — fall back to paragraph windows so the
        # audit still produces a trajectory rather than a single window.
        return _split_paragraphs(text)
    out = ["\n".join(c).strip() for c in chapters]
    return [c for c in out if c.strip()]


# --------------- Per-window feature extraction -------------------------------


def _analyze_window(
    doc: Any,
    raw_text: str,
    *,
    perception_verbs: frozenset[str],
    evaluative_adjectives: frozenset[str],
) -> dict[str, Any]:
    """Compute distance features for a single window's spaCy Doc.

    Pure given the Doc + lexicons; deterministic."""
    tokens = [t for t in doc if not t.is_space]
    word_tokens = [t for t in tokens if not t.is_punct]
    n_words = len(word_tokens)

    third_person = 0
    other_person = 0
    proximal = 0
    distal = 0
    perception_hits = 0
    evaluative_hits = 0
    past_marker_verbs = 0   # VBD (finite past) + VBN (past participle)
    nonpast_finite_verbs = 0  # VBP / VBZ (present finite)
    speech_tag_hits = 0

    for t in tokens:
        low = t.text.lower()
        lemma = t.lemma_.lower()
        pos = t.pos_
        tag = t.tag_

        # Pronoun anchoring.
        if pos == "PRON" or tag in {"PRP", "PRP$"}:
            if low in THIRD_PERSON_PRONOUNS:
                third_person += 1
            elif low in OTHER_PERSON_PRONOUNS:
                other_person += 1

        # Deixis (count by surface form across DET/ADV/PRON anchors).
        if low in PROXIMAL_DEIXIS:
            proximal += 1
        elif low in DISTAL_DEIXIS:
            distal += 1

        # Perception / cognition verbs.
        if pos in {"VERB", "AUX"} or tag.startswith("VB"):
            if lemma in perception_verbs or low in perception_verbs:
                perception_hits += 1
            if lemma in SPEECH_TAG_VERBS:
                speech_tag_hits += 1
            # Tense (disjoint buckets so past_ratio stays in [0, 1]).
            if tag in {"VBD", "VBN"} or "Tense=Past" in str(t.morph):
                past_marker_verbs += 1
            elif tag in {"VBP", "VBZ"}:
                nonpast_finite_verbs += 1

        # Evaluative adjectives.
        if pos == "ADJ" or tag.startswith("JJ"):
            if lemma in evaluative_adjectives or low in evaluative_adjectives:
                evaluative_hits += 1

    pronoun_anchors = third_person + other_person
    pronoun_anchoring_ratio = (
        third_person / pronoun_anchors if pronoun_anchors else 0.0
    )
    deixis_total = proximal + distal
    proximal_share = proximal / deixis_total if deixis_total else 0.0

    perception_density = _per_1k(perception_hits, n_words)
    evaluative_density = _per_1k(evaluative_hits, n_words)

    has_quote = any(ch in raw_text for ch in _QUOTE_CHARS)
    # past markers vs. present finite — disjoint buckets keep the ratio in [0, 1].
    tensed_verbs = past_marker_verbs + nonpast_finite_verbs
    past_ratio = past_marker_verbs / tensed_verbs if tensed_verbs else 0.0

    fid = fid_heuristic_score(
        past_ratio=past_ratio,
        has_proximal_deixis=proximal > 0,
        has_perception_verb=perception_hits > 0,
        has_quote_or_tag=has_quote or speech_tag_hits > 0,
    )

    return {
        "n_words": n_words,
        "pronoun_anchoring": {
            "third_person": third_person,
            "other_person": other_person,
            "ratio_third_person": _round(pronoun_anchoring_ratio),
        },
        "perception_verb_density_per_1k": perception_density,
        "perception_verb_hits": perception_hits,
        "deixis": {
            "proximal": proximal,
            "distal": distal,
            "proximal_share": _round(proximal_share),
        },
        "evaluative_adjective_density_per_1k": evaluative_density,
        "evaluative_adjective_hits": evaluative_hits,
        "fid": {
            "score": _round(fid),
            "past_tense_ratio": _round(past_ratio),
            "has_proximal_deixis": proximal > 0,
            "has_perception_verb": perception_hits > 0,
            "has_quote_or_tag": has_quote or speech_tag_hits > 0,
        },
        "distance": _round(distance_score(
            perception_density=perception_density,
            proximal_share=proximal_share,
            evaluative_density=evaluative_density,
            fid=fid,
        )),
    }


def fid_heuristic_score(
    *,
    past_ratio: float,
    has_proximal_deixis: bool,
    has_perception_verb: bool,
    has_quote_or_tag: bool,
) -> float:
    """Heuristic FID score in [0, 1]. Free-indirect discourse reads as
    past-tense narration that carries proximal deixis + a character's
    perception, *without* quotation marks or a speech tag. This is a signal,
    not a parse of literary intent — equal-weight blend of four components."""
    components = [
        past_ratio,                         # past-tense narration
        1.0 if has_proximal_deixis else 0.0,
        1.0 if has_perception_verb else 0.0,
        0.0 if has_quote_or_tag else 1.0,   # absence of quotation / tag
    ]
    return sum(components) / len(components)


def distance_score(
    *,
    perception_density: float,
    proximal_share: float,
    evaluative_density: float,
    fid: float,
) -> float:
    """A 0..1 narratorial-distance score: higher = CLOSER to the character's
    consciousness. Blends perception-verb density (saturating), proximal-deixis
    share, evaluative-adjective density (saturating), and the FID score. This
    is a descriptive composite, not a calibrated threshold."""
    # Saturating maps so a few dense windows don't dominate the scale.
    perc = min(perception_density / 20.0, 1.0)   # ~20/1k perception verbs ≈ very close
    evalu = min(evaluative_density / 15.0, 1.0)
    return min((perc + proximal_share + evalu + fid) / 4.0, 1.0)


CLOSE_THRESHOLD = 0.5  # descriptive bucketing only, NOT a verdict.


def _classify(distance: float | None) -> str:
    if distance is None:
        return "unknown"
    return "close" if distance >= CLOSE_THRESHOLD else "distant"


# --------------- Aggregation -------------------------------------------------


def audit_narratorial_distance(
    text: str,
    *,
    strategy: str,
    perception_verbs: frozenset[str],
    evaluative_adjectives: frozenset[str],
) -> dict[str, Any]:
    """Compute the full descriptive profile. Pure + deterministic given the
    text and lexicons. Requires spaCy; callers must gate on HAS_SPACY."""
    if not HAS_SPACY or _NLP is None:  # pragma: no cover - gated by caller
        raise RuntimeError("spaCy is required for audit_narratorial_distance")

    raw_windows = split_windows(text, strategy)

    windows: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_windows):
        doc = _NLP(raw)
        feats = _analyze_window(
            doc, raw,
            perception_verbs=perception_verbs,
            evaluative_adjectives=evaluative_adjectives,
        )
        feats["index"] = idx
        feats["classification"] = _classify(feats["distance"])
        windows.append(feats)

    distances = [w["distance"] for w in windows if w["distance"] is not None]
    n_close = sum(1 for w in windows if w["classification"] == "close")
    n_distant = sum(1 for w in windows if w["classification"] == "distant")
    n = len(windows)

    distribution = {
        "n_windows": n,
        "n_close": n_close,
        "n_distant": n_distant,
        "close_fraction": _round(n_close / n) if n else None,
        "distant_fraction": _round(n_distant / n) if n else None,
        "distance_mean": _round(statistics.fmean(distances)) if distances else None,
        "distance_median": _round(statistics.median(distances)) if distances else None,
        "distance_sd": (
            _round(statistics.stdev(distances)) if len(distances) >= 2 else None
        ),
        "distance_min": _round(min(distances)) if distances else None,
        "distance_max": _round(max(distances)) if distances else None,
    }

    # Distance trajectory: one point per window, in document order, with a
    # normalized position (0..1) so consumers can plot close↔distant movement.
    trajectory = [
        {
            "index": w["index"],
            "position": _round(w["index"] / (n - 1)) if n > 1 else 0.0,
            "distance": w["distance"],
            "fid_score": w["fid"]["score"],
            "classification": w["classification"],
        }
        for w in windows
    ]

    return {
        "window_strategy": strategy,
        "windows": windows,
        "distribution": distribution,
        "trajectory": trajectory,
    }


# --------------- Claim license -----------------------------------------------


def _claim_license(*, strategy: str, n_windows: int,
                   custom_verb_lexicon: bool) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "descriptive narratorial-distance / free-indirect-discourse (FID) "
            "features and their trajectory across the text: per-window "
            "pronoun anchoring, perception/cognition-verb density, deictic "
            "anchoring (proximal vs. distal), evaluative-adjective density, a "
            "heuristic FID score, and an overall close/distant distribution."
        ),
        does_not_license=(
            "any authorship-identity, AI-provenance, or writing-quality "
            "inference. Narratorial distance is a craft dimension, not a "
            "voiceprint or an AI signal; close and distant narration are both "
            "valid choices. FID detection is a heuristic (past-tense + "
            "proximal deixis + perception + no quotation/tag), not a parse of "
            "literary intent — high FID scores flag a pattern to read, not a "
            "claim about the author's technique."
        ),
        comparison_set={
            "mode": "single_document_descriptive",
            "window_strategy": strategy,
            "n_windows": n_windows,
            "verb_lexicon": "custom" if custom_verb_lexicon else "default",
        },
        additional_caveats=[
            "FID detection is a heuristic signal, not a parse of literary "
            "intent. It will mis-flag close-third narration that quotes, and "
            "miss FID rendered in unusual tense.",
            "Descriptive only — no band, no verdict, no threshold. The "
            "close/distant split uses a fixed display cutoff for bucketing, "
            "not a calibrated decision boundary.",
            "Verb-class and evaluative-adjective lists are register-tunable "
            "defaults; pass --verb-lexicon to override the perception/cognition "
            "set for a different register.",
        ],
        references=[
            "specs/12-narratorial-distance-audit.md",
        ],
    )


# --------------- Envelope + rendering ----------------------------------------


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  word_count: int, available: bool, strategy: str,
                  custom_verb_lexicon: bool = False,
                  warnings: list[str] | None = None) -> dict[str, Any]:
    n_windows = (
        results.get("distribution", {}).get("n_windows", 0) if available else 0
    )
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=word_count,
        baseline=None,
        results=results if available else {},
        claim_license=(
            _claim_license(
                strategy=strategy, n_windows=n_windows,
                custom_verb_lexicon=custom_verb_lexicon,
            )
            if available else None
        ),
        available=available,
        warnings=warnings,
        target_extra={"spacy_available": HAS_SPACY},
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Narratorial-distance / FID profile — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}  ",
        f"**spaCy available:** {'yes' if payload['target'].get('spacy_available') else 'no'}",
        "",
    ]
    if not payload["available"]:
        lines.append("_No narratorial-distance profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    d = r["distribution"]
    lines += [
        f"**Window strategy:** `{r['window_strategy']}`",
        "",
        "## Close / distant distribution",
        "",
        f"- **Windows:** {d['n_windows']} "
        f"({d['n_close']} close, {d['n_distant']} distant)",
        f"- **Close fraction:** {d['close_fraction']}",
        f"- **Distance:** mean {d['distance_mean']}, median {d['distance_median']}, "
        f"sd {d['distance_sd']} (range {d['distance_min']}–{d['distance_max']})",
        "",
        "## Distance trajectory (document order)",
        "",
        "| # | position | distance | FID | class |",
        "|---:|---:|---:|---:|---|",
    ]
    for pt in r["trajectory"]:
        lines.append(
            f"| {pt['index']} | {pt['position']} | {pt['distance']} | "
            f"{pt['fid_score']} | {pt['classification']} |"
        )
    lines += ["", payload["claim_license_rendered"] or ""]
    return "\n".join(lines) + "\n"


# --------------- CLI ---------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", help="Path to the manuscript (.txt or .md, UTF-8).")
    p.add_argument(
        "--window-strategy", choices=["paragraph", "chapter"],
        default="paragraph",
        help="How to window the text (default: paragraph).",
    )
    p.add_argument(
        "--verb-lexicon",
        help=(
            "Path to a custom perception/cognition verb lexicon (one lemma "
            "per line; '#' comments allowed). Overrides the default set."
        ),
    )
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")
    word_count = count_words(text)

    try:
        perception_verbs, custom = load_verb_lexicon(args.verb_lexicon)
    except OSError as exc:
        sys.stderr.write(f"Could not read --verb-lexicon: {exc}\n")
        return 2

    # Graceful degradation: spaCy / model missing → clean unavailable envelope.
    if not HAS_SPACY or _NLP is None:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count,
            available=False, strategy=args.window_strategy,
            custom_verb_lexicon=custom,
            warnings=[
                "spaCy with the en_core_web_sm model is required for the "
                "narratorial-distance audit (POS/dep + lemma analysis) and is "
                "not available. Install with: pip install spacy && "
                "python -m spacy download en_core_web_sm."
            ],
        )
    elif word_count < LENGTH_FLOOR_WORDS:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count,
            available=False, strategy=args.window_strategy,
            custom_verb_lexicon=custom,
            warnings=[
                f"Target is {word_count} words; below the "
                f"{LENGTH_FLOOR_WORDS}-word floor for a meaningful "
                "narratorial-distance trajectory."
            ],
        )
    else:
        results = audit_narratorial_distance(
            text, strategy=args.window_strategy,
            perception_verbs=perception_verbs,
            evaluative_adjectives=DEFAULT_EVALUATIVE_ADJECTIVES,
        )
        payload = build_payload(
            results, target_path=target_path, word_count=word_count,
            available=True, strategy=args.window_strategy,
            custom_verb_lexicon=custom,
        )

    text_out = (json.dumps(payload, indent=2, default=str)
                if args.json else render_report(payload))
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
