#!/usr/bin/env python3
"""productive_roughness_audit.py — strictly baseline-relative productive-roughness
deviation profile (spec 10; voice-coherence family).

AI editing and copyediting sand off a writer's *productive* roughness —
fragments for emphasis, sentence-initial "And/But", contractions, repetition for
rhythm, asides, very-short punchy sentences. But "roughness" is in the eye of the
beholder: an absolute roughness score would just encode an editor's preferences
as if they were voice. So this audit is **strictly baseline-relative**. It
measures the writer's own stable roughness pattern (from a writer-specific
baseline corpus) and reports how far a *draft* deviates from it. It NEVER asserts
that roughness is good or bad in the abstract, and it refuses to run at all
without a writer-specific baseline.

Per-feature rates (computed on the draft AND on each baseline document):

  - **fragment_rate** — sentences with no finite/root verb (spaCy dependency
    parse: a sentence is a fragment when its root is not a finite verb/aux and it
    contains no finite verb).
  - **sentence_initial_cc_rate** — sentences that open with a coordinating
    conjunction (And / But / Or / Nor / Yet / So / For).
  - **contraction_rate** — contractions per sentence (n't, 're, 's, 'll, 've,
    'd, 'm, and common reduced forms).
  - **adjacent_word_repetition_rate** — adjacent identical word repetitions per
    sentence ("the the", "really really").
  - **interjection_aside_rate** — interjections / asides per sentence (spaCy INTJ
    tokens plus parenthetical / em-dash-bounded asides).
  - **very_short_sentence_rate** — sentences under 5 words.

Each feature is reported as ``{draft, baseline_mean, baseline_sd, z}`` — the
deviation, never an absolute band. The ``z`` is ``(draft - baseline_mean) /
baseline_sd`` (``null`` when the writer's baseline shows no variation in that
feature). The ``baseline`` envelope block carries the corpus provenance.

The audit degrades gracefully: if spaCy / ``en_core_web_sm`` is unavailable, or
if no readable baseline files remain, it emits ``available=False`` with a clean
warning — never a traceback.

Usage:

    python3 scripts/productive_roughness_audit.py DRAFT --baseline-dir DIR
    python3 scripts/productive_roughness_audit.py DRAFT --baseline-dir DIR --json
    python3 scripts/productive_roughness_audit.py DRAFT --baseline-dir DIR --out report.md

``--baseline-dir`` is REQUIRED (enforced in code): the audit is strictly
baseline-relative and refuses to run on a single document.

task_surface: productive_roughness (voice-coherence family).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore

# spaCy is the primary backend (dependency parse for fragment / finite-verb
# detection). Load once at module level with graceful degradation — mirrors the
# HAS_SPACY / _NLP convention used across the framework (variance_audit,
# construction_signature_audit).
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


TASK_SURFACE = "productive_roughness"
TOOL_NAME = "productive_roughness_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 1000

BASELINE_SUFFIXES = {".txt", ".md", ".markdown", ".rst"}

# Coordinating conjunctions (FANBOYS) for the sentence-initial-CC feature.
COORDINATING_CONJUNCTIONS = {"and", "but", "or", "nor", "yet", "so", "for"}

# Feature keys, in stable report order. Each is a per-sentence rate.
FEATURE_KEYS: tuple[str, ...] = (
    "fragment_rate",
    "sentence_initial_cc_rate",
    "contraction_rate",
    "adjacent_word_repetition_rate",
    "interjection_aside_rate",
    "very_short_sentence_rate",
)

FEATURE_LABELS: dict[str, str] = {
    "fragment_rate": "fragment rate (sentences with no finite/root verb)",
    "sentence_initial_cc_rate": "sentence-initial coordinating-conjunction rate",
    "contraction_rate": "contraction rate (per sentence)",
    "adjacent_word_repetition_rate": "adjacent-word-repetition rate (per sentence)",
    "interjection_aside_rate": "interjection / aside rate (per sentence)",
    "very_short_sentence_rate": "very-short-sentence (<5 words) rate",
}

# Contraction forms: clitic suffixes + a few whole-word reduced forms.
_CONTRACTION_RE = re.compile(
    r"\b\w+['’](?:t|re|s|ll|ve|d|m)\b"
    r"|\b(?:gonna|wanna|gotta|gimme|lemme|ain't|ain’t|y'all|y’all)\b",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")

# Parenthetical / em-dash-bounded asides (regex fallback + augmentation to the
# spaCy INTJ signal). Counts ( ... ) and  — ... —  spans.
_PAREN_ASIDE_RE = re.compile(r"\([^)]{1,200}\)")
_DASH_ASIDE_RE = re.compile(r"[—–]\s.+?\s[—–]")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


# ---------- sentence splitting ----------


def split_sentences(text: str) -> list[str]:
    """Split text into sentences using spaCy when available, else a regex
    fallback. Deterministic either way."""
    text = text.strip()
    if not text:
        return []
    if HAS_SPACY and _NLP is not None:
        try:
            doc = _NLP(text)
            sents = [s.text.strip() for s in doc.sents if s.text.strip()]
            if sents:
                return sents
        except Exception:
            pass
    # Regex fallback: split on sentence-final punctuation followed by space.
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


# ---------- per-sentence feature detectors ----------


def _sentence_is_fragment(sent_doc: Any) -> bool:
    """A sentence is a fragment when it contains no finite verb. spaCy marks
    finite verbs/auxiliaries via the ``Tense``/``VerbForm`` morphology and the
    ROOT dependency; we treat a sentence as non-fragment when its ROOT is a
    finite VERB/AUX, OR any token is a finite verb/aux.

    Conservative: imperatives ("Run.") and copular roots are NOT fragments
    because they carry a finite verb. A bare NP / PP ("The long road home.";
    "And into the dark.") has no finite verb → fragment.
    """
    has_finite_verb = False
    for tok in sent_doc:
        if tok.pos_ in {"VERB", "AUX"}:
            verbform = tok.morph.get("VerbForm")
            tense = tok.morph.get("Tense")
            mood = tok.morph.get("Mood")
            # Finite: explicit Fin VerbForm, OR carries tense/mood (present/past
            # indicative/imperative), OR is the ROOT and not an obvious
            # non-finite form (gerund/participle/infinitive).
            if "Fin" in verbform:
                has_finite_verb = True
                break
            if tense or "Imp" in mood or "Ind" in mood:
                has_finite_verb = True
                break
            if (
                tok.dep_ == "ROOT"
                and "Inf" not in verbform
                and "Part" not in verbform
                and "Ger" not in verbform
            ):
                has_finite_verb = True
                break
    return not has_finite_verb


def _starts_with_cc(sent_text: str) -> bool:
    m = _WORD_TOKEN_RE.search(sent_text)
    if not m:
        return False
    return m.group(0).lower() in COORDINATING_CONJUNCTIONS


def _count_contractions(sent_text: str) -> int:
    return len(_CONTRACTION_RE.findall(sent_text))


def _count_adjacent_repetition(sent_text: str) -> int:
    """Count adjacent identical word repetitions ("the the"). Case-insensitive;
    skips single-character tokens to avoid counting "I I" style artefacts of bad
    OCR? — no, keep them; but skip pure punctuation."""
    tokens = [t.lower() for t in _WORD_TOKEN_RE.findall(sent_text)]
    count = 0
    for a, b in zip(tokens, tokens[1:]):
        if a == b and a.isalpha():
            count += 1
    return count


def _count_asides(sent_doc: Any, sent_text: str) -> int:
    """Interjections (spaCy INTJ) + parenthetical / em-dash-bounded asides."""
    n_intj = 0
    if sent_doc is not None:
        n_intj = sum(1 for tok in sent_doc if tok.pos_ == "INTJ")
    n_paren = len(_PAREN_ASIDE_RE.findall(sent_text))
    n_dash = len(_DASH_ASIDE_RE.findall(sent_text))
    return n_intj + n_paren + n_dash


def _is_very_short(sent_text: str) -> bool:
    return len(_WORD_TOKEN_RE.findall(sent_text)) < 5


# ---------- document-level feature extraction ----------


@dataclass
class DocFeatures:
    n_sentences: int
    n_words: int
    rates: dict[str, float]


def extract_features(text: str) -> DocFeatures:
    """Compute the six per-sentence roughness rates for one document.

    Requires spaCy for the fragment + interjection signals; callers must gate on
    HAS_SPACY before invoking (the CLI does). Deterministic.
    """
    sentences = split_sentences(text)
    n_sentences = len(sentences)
    n_words = count_words(text)

    if n_sentences == 0:
        return DocFeatures(
            n_sentences=0,
            n_words=n_words,
            rates={k: 0.0 for k in FEATURE_KEYS},
        )

    n_fragment = 0
    n_initial_cc = 0
    n_contraction = 0
    n_adjacent_rep = 0
    n_aside = 0
    n_very_short = 0

    # Parse each sentence individually so fragment detection is per-sentence.
    for sent_text in sentences:
        sent_doc = None
        if HAS_SPACY and _NLP is not None:
            try:
                sent_doc = _NLP(sent_text)
            except Exception:
                sent_doc = None

        if sent_doc is not None and _sentence_is_fragment(sent_doc):
            n_fragment += 1
        if _starts_with_cc(sent_text):
            n_initial_cc += 1
        n_contraction += _count_contractions(sent_text)
        n_adjacent_rep += _count_adjacent_repetition(sent_text)
        n_aside += _count_asides(sent_doc, sent_text)
        if _is_very_short(sent_text):
            n_very_short += 1

    denom = float(n_sentences)
    rates = {
        "fragment_rate": n_fragment / denom,
        "sentence_initial_cc_rate": n_initial_cc / denom,
        "contraction_rate": n_contraction / denom,
        "adjacent_word_repetition_rate": n_adjacent_rep / denom,
        "interjection_aside_rate": n_aside / denom,
        "very_short_sentence_rate": n_very_short / denom,
    }
    return DocFeatures(n_sentences=n_sentences, n_words=n_words, rates=rates)


# ---------- baseline aggregation ----------


@dataclass
class BaselineStats:
    n_files: int
    n_words: int
    n_sentences: int
    per_feature: dict[str, dict[str, float]]  # feature -> {mean, sd}
    files_loaded: list[Path]
    files_skipped: list[Path]


def _mean_sd(values: list[float]) -> tuple[float, float]:
    """Population mean and sd (sd over the baseline documents). sd is 0.0 when
    fewer than 2 documents or no variation."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    if n < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


def aggregate_baseline(
    baseline_dir: Path,
    *,
    target_path: Path | None = None,
) -> BaselineStats:
    """Walk the baseline directory, extract per-document rates, and compute the
    per-feature mean/sd ACROSS documents. The audited target is filtered out if
    it lives under the baseline directory (self-overlap guard)."""
    if not baseline_dir.exists():
        raise FileNotFoundError(
            f"Baseline directory not found: {baseline_dir}"
        )
    if not baseline_dir.is_dir():
        raise NotADirectoryError(
            f"--baseline-dir is not a directory: {baseline_dir}"
        )

    target_resolved = target_path.resolve() if target_path else None
    per_feature_values: dict[str, list[float]] = {k: [] for k in FEATURE_KEYS}
    loaded: list[Path] = []
    skipped: list[Path] = []
    total_words = 0
    total_sentences = 0

    for path in sorted(baseline_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in BASELINE_SUFFIXES:
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
        feats = extract_features(text)
        if feats.n_sentences == 0:
            skipped.append(path)
            continue
        loaded.append(path)
        total_words += feats.n_words
        total_sentences += feats.n_sentences
        for k in FEATURE_KEYS:
            per_feature_values[k].append(feats.rates[k])

    per_feature: dict[str, dict[str, float]] = {}
    for k in FEATURE_KEYS:
        mean, sd = _mean_sd(per_feature_values[k])
        per_feature[k] = {"mean": mean, "sd": sd}

    return BaselineStats(
        n_files=len(loaded),
        n_words=total_words,
        n_sentences=total_sentences,
        per_feature=per_feature,
        files_loaded=loaded,
        files_skipped=skipped,
    )


# ---------- audit assembly ----------


def build_results(
    draft_features: DocFeatures,
    baseline: BaselineStats,
) -> dict[str, dict[str, float | None]]:
    """Per-feature {draft, baseline_mean, baseline_sd, z}. STRICTLY relative:
    every feature is reported only as a deviation from the writer's own
    baseline. There is no absolute band or quality score anywhere."""
    results: dict[str, dict[str, float | None]] = {}
    for k in FEATURE_KEYS:
        draft_val = draft_features.rates[k]
        mean = baseline.per_feature[k]["mean"]
        sd = baseline.per_feature[k]["sd"]
        z: float | None
        if sd > 0:
            z = (draft_val - mean) / sd
        else:
            # No variation in the writer's baseline for this feature → z is
            # undefined. We refuse to fabricate a deviation magnitude.
            z = None
        results[k] = {
            "label": FEATURE_LABELS[k],
            "draft": draft_val,
            "baseline_mean": mean,
            "baseline_sd": sd,
            "z": z,
        }
    return results


def _claim_license(
    *,
    baseline: BaselineStats,
    draft_words: int,
) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "How this draft's productive-roughness features (fragment rate, "
            "sentence-initial coordinating-conjunction rate, contraction rate, "
            "adjacent-word-repetition rate, interjection/aside rate, very-short-"
            "sentence rate) deviate from THIS writer's own baseline pattern, "
            "reported strictly as draft-vs-baseline mean/sd and a z-distance."
        ),
        does_not_license=(
            "Any absolute roughness or writing-quality judgment (there is no "
            "'too smooth' / 'too rough' band and no quality score — roughness is "
            "register- and writer-dependent, so an absolute call would just "
            "encode an editor's preferences as voice). Any voice-identity, "
            "authorship, or AI-provenance verdict. Any use without a writer-"
            "specific baseline: the audit refuses to run on a single document."
        ),
        comparison_set={
            "mode": "baseline_relative_only",
            "n_baseline_files": baseline.n_files,
            "baseline_words": baseline.n_words,
            "baseline_sentences": baseline.n_sentences,
            "draft_words": draft_words,
        },
        additional_caveats=[
            "Strictly baseline-relative: every feature is a deviation from the "
            "writer's own stable roughness pattern, never an absolute band. A "
            "large z means 'this draft differs from how this writer usually "
            "writes,' NOT 'this draft is too rough / too smooth.'",
            "A null z means the writer's baseline shows no variation in that "
            "feature (e.g., a single baseline document, or a uniformly-zero "
            "rate); the deviation magnitude is undefined, not zero.",
            "Fragment detection precision varies across registers (dialogue-"
            "heavy fiction vs. essay). Read the per-feature deviations as "
            "diagnostic prompts for the writer, not as findings.",
            "Requires spaCy + en_core_web_sm for the fragment and interjection "
            "signals; without it the audit reports available: false rather than "
            "degrading silently.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/10-productive-roughness-audit.md",
        ],
    )


def build_payload(
    *,
    target_path: Path | str,
    draft_words: int,
    draft_sentences: int,
    results: dict[str, Any],
    baseline: BaselineStats | None,
    available: bool,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    baseline_meta = None
    claim_license = None
    if available and baseline is not None:
        baseline_meta = build_baseline_metadata(
            n_files=baseline.n_files,
            words=baseline.n_words,
            extra={"sentences": baseline.n_sentences},
        )
        claim_license = _claim_license(
            baseline=baseline, draft_words=draft_words,
        )
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=draft_words,
        baseline=baseline_meta,
        results=results if available else {},
        claim_license=claim_license,
        available=available,
        warnings=warnings,
        target_extra={
            "sentences": draft_sentences,
            "spacy_available": HAS_SPACY,
        },
    )


# ---------- markdown rendering ----------


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "∞"
        return f"{v:.4f}"
    return str(v)


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Productive-roughness deviation — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}  ",
        f"**spaCy available:** {'yes' if payload['target'].get('spacy_available') else 'no'}",
        "",
        "_Strictly baseline-relative: deviations from the writer's own pattern, "
        "never an absolute roughness or quality judgment._",
        "",
    ]
    if not payload["available"]:
        lines.append("_No deviation profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    b = payload["baseline"] or {}
    lines.append(
        f"**Baseline:** {b.get('n_files', 0)} files, "
        f"{b.get('words', 0)} words, {b.get('sentences', 0)} sentences"
    )
    lines.append("")
    lines.append("## Per-feature deviation (draft vs. writer's baseline)")
    lines.append("")
    lines.append("| Feature | Draft | Baseline mean | Baseline sd | z |")
    lines.append("|---|---:|---:|---:|---:|")
    for k in FEATURE_KEYS:
        r = payload["results"][k]
        lines.append(
            f"| {r['label']} | {_fmt(r['draft'])} | "
            f"{_fmt(r['baseline_mean'])} | {_fmt(r['baseline_sd'])} | "
            f"{_fmt(r['z'])} |"
        )
    lines.append("")
    rendered = payload.get("claim_license_rendered")
    if rendered:
        lines.append(rendered)
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------- CLI ----------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="productive_roughness_audit.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("draft", help="Path to the draft text file (.txt / .md).")
    p.add_argument(
        "--baseline-dir",
        required=True,
        help=(
            "REQUIRED. Directory of the WRITER'S OWN baseline files. The audit "
            "is strictly baseline-relative and refuses to run on a single "
            "document — there is no absolute roughness judgment."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument(
        "--out", help="Write output to this path instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    target_path = Path(args.draft).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"draft: file not found: {args.draft}\n")
        return 2

    # Hard enforcement of the baseline-relative constraint. argparse already
    # makes --baseline-dir required; this is the defensive second gate so a
    # programmatic caller cannot bypass it.
    if not args.baseline_dir:
        sys.stderr.write(
            "--baseline-dir is required: this audit is strictly baseline-"
            "relative and refuses to run on a single document.\n"
        )
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")
    draft_words = count_words(text)

    # Graceful degradation: spaCy missing → clean unavailable payload, no
    # traceback. The fragment + interjection signals are spaCy-only.
    if not HAS_SPACY:
        payload = build_payload(
            target_path=target_path,
            draft_words=draft_words,
            draft_sentences=0,
            results={},
            baseline=None,
            available=False,
            warnings=[
                "spaCy + en_core_web_sm is not available. The productive-"
                "roughness audit needs the dependency parse for fragment / "
                "finite-verb and interjection detection. Install with "
                "`pip install spacy && python -m spacy download en_core_web_sm`."
            ],
        )
        _emit(payload, args)
        return 0

    baseline_dir = Path(args.baseline_dir).expanduser()
    try:
        baseline = aggregate_baseline(baseline_dir, target_path=target_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        payload = build_payload(
            target_path=target_path,
            draft_words=draft_words,
            draft_sentences=0,
            results={},
            baseline=None,
            available=False,
            warnings=[f"--baseline-dir: {exc}"],
        )
        _emit(payload, args)
        return 0

    if baseline.n_files == 0:
        payload = build_payload(
            target_path=target_path,
            draft_words=draft_words,
            draft_sentences=0,
            results={},
            baseline=None,
            available=False,
            warnings=[
                "--baseline-dir: no readable .txt/.md/.rst baseline files "
                "remained after filtering. The draft cannot be its own "
                "baseline; supply a directory of the writer's OTHER work."
            ],
        )
        _emit(payload, args)
        return 0

    draft_features = extract_features(text)
    results = build_results(draft_features, baseline)

    warnings: list[str] = []
    if draft_words < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"Draft is {draft_words} words; below the {LENGTH_FLOOR_WORDS}-word "
            "guidance floor. Per-sentence rates are noisier on short drafts — "
            "read the deviations as directional, not precise."
        )

    payload = build_payload(
        target_path=target_path,
        draft_words=draft_words,
        draft_sentences=draft_features.n_sentences,
        results=results,
        baseline=baseline,
        available=True,
        warnings=warnings or None,
    )
    _emit(payload, args)
    return 0


def _emit(payload: dict[str, Any], args: argparse.Namespace) -> None:
    text_out = (
        json.dumps(payload, indent=2, default=str)
        if args.json
        else render_report(payload)
    )
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)


if __name__ == "__main__":
    sys.exit(main())
