#!/usr/bin/env python3
"""lambdag_audit.py — LambdaG grammar likelihood-ratio AV signal (spec 32).

A **white-box, model-free** authorship-verification signal. Build a count-based n-gram language
model over a document's **POS-tag sequences**, then score a query document's grammar
log-likelihood under a **reference-author** POS-LM versus a **background** POS-LM and report the
**log-likelihood-ratio** (λ_G, arXiv:2403.08462) plus a leaning band:

    lambda_g            = logL_ref − logL_bg                # total grammar log-LR (nats)
    lambda_g_per_token  = lambda_g / n_scored_ngrams        # length-normalized (the headline)

`lambda_g > 0` = the query's grammar is more probable under the reference author's model than under
the background's. The signal is the likelihood-ratio sibling of `voice_distance.py`'s Burrows Delta —
same kind of work (compare a target to a reference), same surface (`voice_coherence`), same
no-verdict posture.

Posture (NO VERDICT): the headline is a continuous signed real with both halves of the ratio exposed;
the only categorical is a 3-level PROVISIONAL *leaning* band (background_leaning / indeterminate /
author_leaning); there is no is_ai / is_human / same_author / different_author / verdict / match /
prob_same_author key anywhere, and the surface NEVER ranks authors (verification, one reference author
— not attribution). Thresholds are operator-side / PROVISIONAL. The claim license refuses the
same-author AND the AI/human inferences in words.

Anti-Goodhart: when reference and background are drawn from one manifest the loader asserts the two
document sets are DISJOINT by id (no train-on-the-test-set inflation); self-scoring a corpus against
itself is refused.

M1 (this build) is stdlib: the n-gram LM is pure `collections` + `math` and is tested directly on
fixture POS streams with NO parser. The spaCy POS parse (shared `variance_audit._NLP`) is the ONLY
model-gated step; without it the surface ABSTAINS (`available:false` / `missing_dependency`) — there
is no faithful parse-free POS stream. M2 (deferred) swaps the POS alphabet and/or smoothing behind a
lazy import; the schema, band, license, and wiring are frozen here.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import (  # noqa: E402
    build_baseline_metadata,
    build_error_output,
    build_output,
)
from claim_license import from_legacy  # noqa: E402
from stylometry_core import (  # noqa: E402
    load_entries_from_dir,
    load_entries_from_manifest,
    pos_tag_sentences,
    word_tokens,
)
from variance_audit import HAS_SPACY, _NLP  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "lambdag_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_N = 3
DEFAULT_SMOOTHING_K = 0.5
DEFAULT_TOP_K = 10
LENGTH_FLOOR_WORDS = 150

# Sentence-boundary pad tokens. `<s>` is the start context filler; `</s>` is the
# end-of-sentence terminal that the model must predict so the last real tag is
# scored. These are not UPOS tags so they never collide with the closed UPOS set.
BOS = "<s>"
EOS = "</s>"

# The closed Universal POS inventory (17 tags, universaldependencies.org/u/pos). A POS stream
# draws from this fixed set, so the smoothing support is UPOS_TAGS ∪ {EOS} (∪ any non-standard
# observed tag) — NOT just the tags seen in training. Add-k assigns mass to ANY queryable tag, so
# the denominator V must count every tag the model could be asked about; otherwise a valid UPOS tag
# absent from both corpora gets add-k mass that no V-term covers and the conditional sums above 1
# (Codex P1). With the full support, every P(tag | context) distribution sums to exactly 1.
UPOS_TAGS = frozenset({
    "ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN", "NUM",
    "PART", "PRON", "PROPN", "PUNCT", "SCONJ", "SYM", "VERB", "X",
})

# PROVISIONAL leaning-band thresholds on lambda_g_per_token (nats/n-gram). These
# are placeholder operator-side values; real thresholds await the FPR-targeted
# same-vs-different-author calibration study (spec 32 § Calibration posture).
PROVISIONAL_BAND_THRESHOLDS = {
    "author_leaning_above": 0.05,
    "background_leaning_below": -0.05,
}
CALIBRATION_STATUS = "PROVISIONAL — uncalibrated; thresholds operator-side"
CALIBRATION_ANCHOR = "reference-author + background pair required"


# --------------------------------------------------------------------------
# The grammar n-gram LM — pure stdlib (collections + math). No spaCy here, so
# this whole section is CI-runnable on fixture POS streams with no parser.
# --------------------------------------------------------------------------


class GrammarLM:
    """A count-based, add-k (Lidstone) smoothed n-gram language model over POS-tag
    sequences. Model-free: a `Counter` over `(context, tag)` plus context totals.
    Deterministic and order-stable. The vocabulary is the CLOSED UPOS inventory plus
    the `</s>` terminal (and any non-standard observed tag), NOT merely the tags seen
    in training — so the add-k denominator covers every queryable tag and each
    conditional distribution sums to exactly 1, including for a valid UPOS tag that
    appears in the query but in neither training corpus (Codex P1)."""

    def __init__(self, n: int, k: float):
        if n < 2 or n > 4:
            raise ValueError(f"n must be in 2..4 (got {n})")
        if k <= 0.0:
            raise ValueError(f"smoothing k must be > 0 (got {k})")
        self.n = n
        self.k = k
        self.context_tag_counts: dict[tuple[str, ...], Counter[str]] = {}
        self.context_totals: Counter[tuple[str, ...]] = Counter()
        # Seed the support with the FULL closed UPOS inventory + EOS so the add-k denominator
        # covers every queryable tag (a valid UPOS tag unseen in training is still in the
        # support); add_sentences only ever ADDS non-standard observed tags on top.
        self.vocab: set[str] = set(UPOS_TAGS) | {EOS}
        self.n_sentences = 0
        self.n_pos_tokens = 0

    @staticmethod
    def _padded(tags: list[str], n: int) -> list[str]:
        # (n-1) BOS so the first real tag has a full context; one EOS terminal.
        return [BOS] * (n - 1) + list(tags) + [EOS]

    def add_sentences(self, sentences: Iterable[list[str]]) -> None:
        for tags in sentences:
            self.n_sentences += 1
            self.n_pos_tokens += len(tags)
            self.vocab.update(tags)
            padded = self._padded(tags, self.n)
            # The model predicts every position from index (n-1) onward, i.e. the
            # real tags AND the trailing EOS. BOS is only ever a context filler,
            # never a predicted target, so it is not added to the vocabulary.
            for i in range(self.n - 1, len(padded)):
                context = tuple(padded[i - (self.n - 1):i])
                tag = padded[i]
                self.context_tag_counts.setdefault(context, Counter())[tag] += 1
                self.context_totals[context] += 1
        # `self.vocab` is pre-seeded with UPOS_TAGS ∪ {EOS} in __init__; `update(tags)`
        # above only adds any non-standard observed tag, so the support already covers
        # EOS and every UPOS tag whether or not it was seen.

    @property
    def vocab_size(self) -> int:
        # Add-k spreads k mass over the FULL support of predictable symbols — the closed
        # UPOS inventory + the EOS terminal (+ any non-standard observed tag) — so the
        # denominator covers every queryable tag and the conditional sums to 1. At least 1
        # so a degenerate empty model still yields a finite (uniform) probability.
        return max(len(self.vocab), 1)

    def log_prob(self, context: tuple[str, ...], tag: str) -> float:
        """Natural-log add-k probability of `tag` given `context`. Finite for any
        input: an unseen context has count 0, so the estimate falls back to the
        uniform add-k prior `log(k / (k * V))` = `-log(V)`; an unseen tag in a
        seen context gets `log(k / (total + k*V))`. Never `log(0)`, never `inf`."""
        v = self.vocab_size
        total = self.context_totals.get(context, 0)
        count = 0
        seen = self.context_tag_counts.get(context)
        if seen is not None:
            count = seen.get(tag, 0)
        numerator = count + self.k
        denominator = total + self.k * v
        return math.log(numerator) - math.log(denominator)

    def score_sentence(self, tags: list[str]) -> tuple[float, int]:
        """Return `(sum_log_prob, n_scored_ngrams)` for one sentence under this
        model's n. Each padded position from (n-1) on is one scored n-gram."""
        padded = self._padded(tags, self.n)
        total_lp = 0.0
        n_scored = 0
        for i in range(self.n - 1, len(padded)):
            context = tuple(padded[i - (self.n - 1):i])
            total_lp += self.log_prob(context, padded[i])
            n_scored += 1
        return total_lp, n_scored


def build_lm(sentences: list[list[str]], *, n: int, k: float) -> GrammarLM:
    lm = GrammarLM(n=n, k=k)
    lm.add_sentences(sentences)
    return lm


def share_vocab(*lms: GrammarLM) -> None:
    """Give every LM the SAME vocabulary (the union of their observed tags). The add-k denominator
    is `total + k * vocab_size`; if the reference and background LMs kept their own observed
    vocabularies, a tag seen in one corpus but not the other made the two denominators differ and the
    log-likelihood RATIO biased even for tags both models score identically (Codex P1). A shared
    vocabulary makes the smoothing support identical, so λ_G reflects only grammar differences."""
    shared: set[str] = set()
    for lm in lms:
        shared |= lm.vocab
    for lm in lms:
        lm.vocab = shared


def _ngram_label(context: tuple[str, ...], tag: str) -> str:
    return "-".join(list(context) + [tag])


def score_query(
    query_sentences: list[list[str]],
    ref_lm: GrammarLM,
    bg_lm: GrammarLM,
    *,
    top_k: int,
) -> dict[str, Any]:
    """Score the query's POS streams against the reference and background LMs and
    assemble the λ_G payload. Pure arithmetic — no parser, no model. `ref_lm` and
    `bg_lm` MUST share the same `n` (the caller builds both from the same --n)."""
    if ref_lm.n != bg_lm.n:
        raise ValueError("reference and background LMs must share n")
    # Force a shared add-k support so the likelihood RATIO is unbiased regardless of which corpus
    # happened to observe which tags (Codex P1). Idempotent if the caller already shared.
    share_vocab(ref_lm, bg_lm)
    n = ref_lm.n

    logL_ref = 0.0
    logL_bg = 0.0
    n_scored = 0
    per_sentence: list[dict[str, Any]] = []
    # Per-n-gram log-ratio accumulator (the explanation block): sum the signed
    # ratio over occurrences so a recurrent grammar n-gram dominates by frequency.
    gram_ratio: dict[str, float] = {}

    for i, tags in enumerate(query_sentences):
        padded = GrammarLM._padded(tags, n)
        s_ref = 0.0
        s_bg = 0.0
        s_count = 0
        for j in range(n - 1, len(padded)):
            context = tuple(padded[j - (n - 1):j])
            tag = padded[j]
            lp_ref = ref_lm.log_prob(context, tag)
            lp_bg = bg_lm.log_prob(context, tag)
            s_ref += lp_ref
            s_bg += lp_bg
            s_count += 1
            label = _ngram_label(context, tag)
            gram_ratio[label] = gram_ratio.get(label, 0.0) + (lp_ref - lp_bg)
        logL_ref += s_ref
        logL_bg += s_bg
        n_scored += s_count
        per_sentence.append({
            "i": i,
            "lambda_g": round(s_ref - s_bg, 6),
            "n_ngrams": s_count,
        })

    lambda_g = logL_ref - logL_bg
    lambda_g_per_token = (lambda_g / n_scored) if n_scored else 0.0

    favoring = sorted(gram_ratio.items(), key=lambda kv: kv[1], reverse=True)
    top_author = [
        {"gram": g, "log_ratio": round(r, 6)} for g, r in favoring[:top_k] if r > 0.0
    ]
    top_background = [
        {"gram": g, "log_ratio": round(r, 6)}
        for g, r in sorted(favoring, key=lambda kv: kv[1])[:top_k] if r < 0.0
    ]

    return {
        "lambda_g": round(lambda_g, 6),
        "lambda_g_per_token": round(lambda_g_per_token, 6),
        "logL_ref_nats": round(logL_ref, 6),
        "logL_bg_nats": round(logL_bg, 6),
        "n_scored_ngrams": n_scored,
        "n": n,
        "smoothing": {"method": "lidstone", "k": ref_lm.k},
        "pos_tagset": "spacy_upos",
        "band": _provisional_band(lambda_g_per_token),
        "per_sentence": per_sentence,
        "top_author_favoring_ngrams": top_author,
        "top_background_favoring_ngrams": top_background,
        "reference_summary": {
            "n_docs": None,  # filled by the caller (it knows the doc count)
            "n_sentences": ref_lm.n_sentences,
            "n_pos_tokens": ref_lm.n_pos_tokens,
        },
        "background_summary": {
            "n_docs": None,
            "n_sentences": bg_lm.n_sentences,
            "n_pos_tokens": bg_lm.n_pos_tokens,
        },
        "assumptions": {
            "method": "POS n-gram grammar LM log-likelihood-ratio (LambdaG, arXiv:2403.08462)",
            "tagset": "Universal-Dependencies POS via the shared spaCy parse; "
                      "n-grams do not cross sentences",
            "orientation": "lambda_g > 0 = query grammar more probable under the reference "
                           "author than the background; this is NOT a same-author determination",
            "corpus_dependence": "the LR is relative to THIS reference/background pair; a thin "
                                 "or register-mismatched background inflates |lambda_g|; topic "
                                 "and register confound grammar",
            "smoothing": "add-k (Lidstone) over the closed UPOS vocabulary; "
                         "KN smoothing is an M2 option",
        },
    }


def _provisional_band(lambda_g_per_token: float) -> dict[str, Any]:
    """The PROVISIONAL leaning band over the continuous λ_G/token score. Flat-key
    shape (the surprisal_audit._provisional_band precedent: band / flags /
    provisional / calibration_anchor / thresholds_used) plus an added
    `calibration_status` string. A READING AID, never a same/different-author
    boolean: the band names a *leaning* of the MEASURED grammar log-LR, not the
    inference target."""
    above = PROVISIONAL_BAND_THRESHOLDS["author_leaning_above"]
    below = PROVISIONAL_BAND_THRESHOLDS["background_leaning_below"]
    flags: list[str] = []
    if lambda_g_per_token > above:
        band = "author_leaning"
        flags.append("above_author_threshold")
    elif lambda_g_per_token < below:
        band = "background_leaning"
        flags.append("below_background_threshold")
    else:
        band = "indeterminate"
    return {
        "band": band,
        "flags": flags,
        "provisional": True,
        "calibration_anchor": CALIBRATION_ANCHOR,
        "calibration_status": CALIBRATION_STATUS,
        "thresholds_used": dict(PROVISIONAL_BAND_THRESHOLDS),
    }


# --------------------------------------------------------------------------
# Corpus loading (real loaders only — no invented K=V filter). Reference and
# background each come from a directory OR the shared manifest under one of the
# loader's FIXED keyword filters (persona / split / register), exactly as
# voice_distance.py selects its baseline.
# --------------------------------------------------------------------------


class CorpusError(ValueError):
    """A user-facing corpus-loading failure (empty corpus, overlap, etc.). The
    caller maps it to a bad_input error envelope."""


def _load_corpus(
    *,
    label: str,
    directory: str | None,
    manifest: str | None,
    persona: str | None,
    split: str | None,
    register: str | None,
    use: str | None,
    ai_status: str | None,
) -> list[dict[str, Any]]:
    """Load one corpus (reference or background) via the real stylometry_core
    loaders. A directory wins if given; otherwise the manifest is filtered by the
    fixed loader kwargs. Raises CorpusError when nothing matched.

    `use` / `ai_status` are passed through EXPLICITLY (default `None` from the
    caller) rather than letting `load_entries_from_manifest` fall back to its own
    `use="baseline"` / `ai_status="pre_ai_human"` defaults. For this surface the
    reference is the operator's OWN writing, which is rarely tagged `use:baseline`
    and never `ai_status:pre_ai_human` — silently applying the loader defaults
    would drop the whole reference corpus and raise 'reference corpus is empty'.
    The operator opts into those filters via --reference-use / --reference-ai-status
    (and the background equivalents), mirroring voice_distance.py's --use/--ai-status."""
    if directory:
        entries = load_entries_from_dir(directory)
    elif manifest:
        entries = load_entries_from_manifest(
            manifest,
            persona=persona,
            split=split,
            register=register,
            use=use,
            ai_status=ai_status,
        )
    else:
        raise CorpusError(
            f"no {label} corpus: pass --{label}-dir or --manifest with a "
            f"--{label}-persona/--{label}-split/--{label}-register filter"
        )
    if not entries:
        raise CorpusError(f"{label} corpus is empty (no entries matched the filters)")
    return entries


def assert_disjoint(reference: list[dict[str, Any]], background: list[dict[str, Any]]) -> None:
    """Anti-Goodhart held-out-disjoint guard: the reference and background document
    SETS must not share a SOURCE FILE. A shared doc would train the reference LM on a
    document the background also models (or vice versa), inflating |λ_G| — the
    train-on-the-test-set Goodhart the LR must not do. Self-scoring a corpus
    against itself is the degenerate full-overlap case and is refused here too.

    Overlap is keyed on each entry's `path` (the resolved source file), NOT its
    `id`. The dir loader derives `id` from `path.stem`, so two genuinely distinct
    files in two distinct directories that happen to share a basename (ref/intro.txt
    vs bg/intro.txt) would collide on `id` and be FALSELY refused. Their `path`
    values are always distinct, so path-keying never false-positives on a filename
    coincidence; the manifest path (where a shared id is the SAME doc and resolves
    to the same `path`) is still correctly refused. The operator-facing message
    still names the recognizable `id`(s)."""
    bg_paths = {e["path"] for e in background}
    offenders = sorted(
        {e.get("id", e["path"]) for e in reference if e["path"] in bg_paths}
    )
    if offenders:
        shown = ", ".join(offenders[:10])
        more = "" if len(offenders) <= 10 else f" (+{len(offenders) - 10} more)"
        raise CorpusError(
            f"reference and background document sets overlap on id(s): {shown}{more}. "
            f"They must be held-out disjoint — a doc in both inflates the likelihood "
            f"ratio (train-on-the-test-set). Self-scoring a corpus against itself is refused."
        )


def _entries_to_sentences(entries: list[dict[str, Any]]) -> list[list[str]]:
    """Concatenate the per-sentence POS streams across a corpus's documents.
    Assumes the parser is available (the caller guards HAS_SPACY)."""
    sentences: list[list[str]] = []
    for entry in entries:
        sentences.extend(pos_tag_sentences(entry["text"]))
    return sentences


# --------------------------------------------------------------------------
# Claim license + run + CLI
# --------------------------------------------------------------------------


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "A grammar likelihood-ratio (LambdaG, arXiv:2403.08462): how much more probable the "
            "query document's POS-sequence grammar is under an n-gram language model trained on "
            "the named reference-author corpus than under one trained on the named background "
            "corpus, reported as a signed log-ratio (lambda_g, length-normalized "
            "lambda_g_per_token) plus a per-sentence and per-n-gram decomposition. The point is "
            "voice-comparison: does this document's grammar lean toward the reference author or "
            "the population?"
        ),
        "does_not_license": (
            "A same-author / different-author determination — lambda_g is a relative likelihood "
            "against THIS pair of corpora, not a verdict; the human adjudicates. An AI/human "
            "provenance call (grammar LR is an authorship signal, not an AI-detector). The LR is "
            "corpus-relative: a thin, small, or register-mismatched reference or background "
            "inflates or flips the sign, and topic/register/genre confound grammar (a reference "
            "author writing in a different register can score below background). Thresholds and "
            "bands are operator-side / PROVISIONAL; the surface emits no decision. Calibration "
            "(an FPR-targeted same-vs-different-author study) is the validation harness's job."
        ),
    }


def _err(target: str | None, reason: str, category: str) -> dict[str, Any]:
    return build_error_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=target, reason=reason, reason_category=category,
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target = args.query

    # 9b: no parser → abstain (there is no faithful parse-free POS stream).
    if not HAS_SPACY or _NLP is None:
        return _err(
            target,
            "POS tagging needs spaCy + en_core_web_sm; install it to run this surface",
            "missing_dependency",
        )

    # 9c: query must be readable.
    try:
        query_text = Path(target).read_text(encoding="utf-8")
    except OSError as e:
        return _err(target, f"cannot read query: {e}", "bad_input")

    # Load the two corpora via the real loaders (no invented K=V filter).
    try:
        reference = _load_corpus(
            label="reference", directory=args.reference_dir, manifest=args.manifest,
            persona=args.reference_persona, split=args.reference_split,
            register=args.reference_register, use=args.reference_use,
            ai_status=args.reference_ai_status,
        )
        background = _load_corpus(
            label="background", directory=args.background_dir, manifest=args.manifest,
            persona=args.background_persona, split=args.background_split,
            register=args.background_register, use=args.background_use,
            ai_status=args.background_ai_status,
        )
        # 7: held-out disjoint (and self-scoring refusal).
        assert_disjoint(reference, background)
    except CorpusError as e:
        return _err(target, str(e), "bad_input")

    query_sentences = pos_tag_sentences(query_text)
    if not query_sentences:
        return _err(target, "query produced no parseable sentences", "bad_input")

    ref_lm = build_lm(_entries_to_sentences(reference), n=args.n, k=args.smoothing_k)
    bg_lm = build_lm(_entries_to_sentences(background), n=args.n, k=args.smoothing_k)
    share_vocab(ref_lm, bg_lm)   # identical add-k support so the ratio is unbiased (Codex P1)
    if ref_lm.n_sentences == 0:
        return _err(target, "reference corpus produced no parseable sentences", "bad_input")
    if bg_lm.n_sentences == 0:
        return _err(target, "background corpus produced no parseable sentences", "bad_input")

    results = score_query(query_sentences, ref_lm, bg_lm, top_k=args.top_k)
    results["reference_summary"]["n_docs"] = len(reference)
    results["background_summary"]["n_docs"] = len(background)

    query_words = len(word_tokens(query_text))
    warnings: list[str] | None = None
    if query_words < LENGTH_FLOOR_WORDS:
        warnings = [
            f"query is {query_words} words (< ~{LENGTH_FLOOR_WORDS}); the grammar "
            f"likelihood-ratio is unstable on short text"
        ]

    baseline = build_baseline_metadata(
        n_files=len(background),
        words=bg_lm.n_pos_tokens,
        register=args.background_register,
        split=args.background_split,
    )

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=target, target_words=query_words, baseline=baseline,
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        target_extra={"spacy_available": True}, warnings=warnings,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", help="Path to the query (target) text.")
    ap.add_argument("--reference-dir", help="Directory of reference-author .txt/.md files.")
    ap.add_argument("--background-dir", help="Directory of background .txt/.md files.")
    ap.add_argument("--manifest", help="JSONL corpus manifest (shared source for both corpora).")
    # Reference/background selection uses the REAL fixed loader filters
    # (persona / split / register) — there is no generic K=V filter.
    ap.add_argument("--reference-persona", help="Manifest filter: reference persona value.")
    ap.add_argument("--reference-split", help="Manifest filter: reference split value.")
    ap.add_argument("--reference-register", help="Manifest filter: reference register value.")
    # use / ai_status default to None (no filter) — UNLIKE the loader's own
    # use="baseline"/ai_status="pre_ai_human" defaults — so the documented
    # persona-only manifest example does not silently drop a reference author
    # whose entries aren't tagged baseline/pre_ai_human. Opt in explicitly.
    ap.add_argument("--reference-use", default=None,
                    help="Manifest filter: reference use tag (default: no filter).")
    ap.add_argument("--reference-ai-status", default=None,
                    help="Manifest filter: reference ai_status (default: no filter).")
    ap.add_argument("--background-persona", help="Manifest filter: background persona value.")
    ap.add_argument("--background-split", help="Manifest filter: background split value.")
    ap.add_argument("--background-register", help="Manifest filter: background register value.")
    ap.add_argument("--background-use", default=None,
                    help="Manifest filter: background use tag (default: no filter).")
    ap.add_argument("--background-ai-status", default=None,
                    help="Manifest filter: background ai_status (default: no filter).")
    ap.add_argument("--n", type=int, default=DEFAULT_N,
                    help=f"LM order, 2..4 (default {DEFAULT_N}).")
    ap.add_argument("--smoothing-k", type=float, default=DEFAULT_SMOOTHING_K,
                    help=f"Add-k (Lidstone) smoothing constant (default {DEFAULT_SMOOTHING_K}).")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help=f"Top author-/background-favoring n-grams to report (default {DEFAULT_TOP_K}).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.n < 2 or args.n > 4:
        sys.stderr.write("[lambdag_audit] --n must be in 2..4\n")
        return 2
    if args.smoothing_k <= 0.0:
        sys.stderr.write("[lambdag_audit] --smoothing-k must be > 0\n")
        return 2
    if args.top_k < 0:
        sys.stderr.write("[lambdag_audit] --top-k must be >= 0\n")
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
