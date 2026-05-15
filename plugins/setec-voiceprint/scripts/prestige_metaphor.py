#!/usr/bin/env python3
"""prestige_metaphor.py — AIC-8 prestige-metaphor detector.

Composes on `image_conjunction.py`. For each detected image
conjunction, classifies the abstract member's semantic domain and
flags documents that scatter conjunctions across many prestige
domains rather than concentrating around a thematic commitment.
Per `internal/SPEC_aic_8_9_implementation.md` Step 7.

The diagnostic question: when a writer deploys image conjunctions,
do they cluster around a single theoretical commitment ("the
machinery of grief" + "the gears of grief" + "the engine of
sorrow" → unified machinery metaphor system) or scatter across
unrelated prestige domains ("the architecture of grief" + "the
topology of attention" + "the grammar of desire" → metaphor
confetti)? The scatter is the AIC-8 prestige-metaphor signature.

**Which word gets classified?** The spec is slightly inconsistent.
Step 7 says "the lower-concreteness member of the pair," but the
spec's running examples ("machinery of grief", "topology of
attention", "grammar of desire") have the prestige-domain word
at *higher* Brysbaert concreteness than the emotional target
(machinery 4.75 vs grief 2.7; grammar 3.19 vs desire 1.7;
architecture 3.59 vs grief 2.7). The §AIC-8 description
identifies the higher-concreteness word as the
"scaffolding/intellectually-serious-domain" element. The
operationally-correct reading: **classify the higher-concreteness
member** as the scaffolding/prestige word. The parenthetical in
Step 7 is the inconsistent piece. This module follows the
operationally-correct interpretation; the conjunctions' classified
JSON records include both `scaffolding_word` (the higher-
concreteness one) and `target_word` (the lower-concreteness one)
so consumers see both members explicitly.

Domain classification has two tiers:

  1. **Hardcoded prestige-domain vocabulary** (primary): a curated
     mapping of ~50 abstract words to their characteristic prestige
     domains (architecture, grammar, cartography, ecology,
     machinery, weather, ritual, infrastructure, topology,
     geology, economy, music, theater, mathematics, biology,
     navigation, geometry, choreography). Matches the spec's
     §AIC-8 enumeration plus derived forms.
  2. **WordNet hypernym chain** (fallback): for abstract words not
     in the hardcoded list, walk WordNet's hypernym chain and
     return the synset name at level 3-4 from the root. Catches
     the long tail (e.g., "metabolism of memory" → "biology"-
     adjacent via WordNet).

The hardcoded-list-first ordering matters: WordNet at level 3-4
abstracts to broad categories (`feeling`, `act`, `content`) that
group prestige and non-prestige words together. The hardcoded
list preserves the spec's intended prestige-domain identification.
WordNet handles the words the hardcoded list doesn't cover.

The detector's flag fires when ``domain_scatter_entropy > T3``
(default 0.7) AND ``image_conjunction_density > register-typical
baseline`` (the latter check requires a baseline; the detector
emits the raw entropy regardless so operators can apply their own
thresholds).

CLI usage::

    python3 scripts/prestige_metaphor.py path/to/draft.md
    python3 scripts/prestige_metaphor.py path/to/draft.md --t3 0.6
    python3 scripts/prestige_metaphor.py path/to/draft.md \\
        --t1 2.0 --t2 0.5 --t3 0.7

Requirements:

  * Everything `image_conjunction.py` requires (spaCy with parsing
    + vectors, Brysbaert CSV).
  * Optional: NLTK + WordNet data for the WordNet fallback. If
    NLTK isn't installed, the detector runs hardcoded-only with a
    diagnostic note in the JSON output.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import embeddings  # type: ignore
import image_conjunction  # type: ignore


# Spec defaults. T3 = 0.7 normalized Shannon entropy.
DEFAULT_T3_DOMAIN_SCATTER_ENTROPY = 0.7


# Hardcoded prestige-domain vocabulary. Spec §AIC-8 enumerates 18
# domains; this map includes the canonical word plus common
# derived forms (adjective + nominal alternates) so the detector
# catches "architectural" alongside "architecture", "topological"
# alongside "topology", etc.
#
# The list is **operator-extensible**: register-specific prestige-
# domain extensions can be added via the `extra_domains` argument
# to `classify_domain` and to `prestige_metaphor_density`.
PRESTIGE_DOMAIN_VOCAB: dict[str, str] = {
    # Architecture / structure
    "architecture": "architecture",
    "architectural": "architecture",
    "architectonic": "architecture",
    # Grammar / language structure
    "grammar": "grammar",
    "grammatical": "grammar",
    "syntax": "grammar",
    "syntactic": "grammar",
    "semantics": "grammar",
    "semantic": "grammar",
    # Cartography / mapping
    "cartography": "cartography",
    "cartographic": "cartography",
    "topography": "cartography",
    # Ecology / biology
    "ecology": "ecology",
    "ecological": "ecology",
    "biology": "biology",
    "biological": "biology",
    "anatomy": "biology",
    "anatomical": "biology",
    "physiology": "biology",
    "physiological": "biology",
    "metabolism": "biology",
    "metabolic": "biology",
    # Machinery / mechanism
    "machinery": "machinery",
    "machine": "machinery",
    "mechanism": "machinery",
    "mechanical": "machinery",
    "mechanics": "machinery",
    "engine": "machinery",
    # Weather / atmosphere
    "weather": "weather",
    "atmosphere": "weather",
    "atmospheric": "weather",
    "climate": "weather",
    # Ritual / ceremony
    "ritual": "ritual",
    "ritualistic": "ritual",
    "ceremony": "ritual",
    "ceremonial": "ritual",
    # Infrastructure
    "infrastructure": "infrastructure",
    "infrastructural": "infrastructure",
    # Topology
    "topology": "topology",
    "topological": "topology",
    # Geology
    "geology": "geology",
    "geological": "geology",
    "geophysics": "geology",
    # Economy
    "economy": "economy",
    "economics": "economy",
    "economic": "economy",
    "marketplace": "economy",
    # Music
    "music": "music",
    "musical": "music",
    "melody": "music",
    "melodic": "music",
    "harmony": "music",
    "harmonic": "music",
    "rhythm": "music",
    "rhythmic": "music",
    # Theater / performance
    "theater": "theater",
    "theatre": "theater",
    "theatrical": "theater",
    "drama": "theater",
    "dramatic": "theater",
    "stagecraft": "theater",
    # Mathematics
    "mathematics": "mathematics",
    "mathematical": "mathematics",
    "calculus": "mathematics",
    "algebra": "mathematics",
    "algebraic": "mathematics",
    # Navigation
    "navigation": "navigation",
    "navigational": "navigation",
    "compass": "navigation",
    # Geometry
    "geometry": "geometry",
    "geometric": "geometry",
    "geometrical": "geometry",
    # Choreography / dance
    "choreography": "choreography",
    "choreographic": "choreography",
    "dance": "choreography",
}


def classify_domain(
    word: str,
    *,
    use_wordnet: bool = True,
    extra_domains: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Return the prestige domain for ``word``, or ``None``.

    Lookup order:

      1. Operator-supplied ``extra_domains`` (if any).
      2. Hardcoded ``PRESTIGE_DOMAIN_VOCAB``.
      3. WordNet hypernym chain at level 3-4 from the root, if
         ``use_wordnet=True`` and NLTK + WordNet data are available.

    Returns ``None`` if no classification fires.
    """
    word_lc = word.lower()
    if extra_domains and word_lc in extra_domains:
        return extra_domains[word_lc]
    if word_lc in PRESTIGE_DOMAIN_VOCAB:
        return PRESTIGE_DOMAIN_VOCAB[word_lc]
    if use_wordnet:
        return _wordnet_domain(word_lc)
    return None


def _wordnet_domain(
    word: str,
    *,
    target_levels: tuple[int, ...] = (4, 3),
) -> Optional[str]:
    """Return the WordNet hypernym at level 3 or 4 from the root.

    Walks the first noun synset's first hypernym path. Tries level
    4 first (more specific) then 3 (more general). Returns
    ``None`` for unknown words, words without noun senses, or when
    NLTK / WordNet data is unavailable.

    Levels 3-4 from root group prestige-adjacent words usefully:
    ``grammar`` and ``topology`` both land near ``content`` /
    ``knowledge_domain``; ``machinery`` and ``architecture`` both
    land near ``artifact``; ``grief`` / ``love`` / ``desire`` all
    land near ``feeling``.
    """
    try:
        from nltk.corpus import wordnet as wn  # type: ignore
    except (ImportError, LookupError):
        return None
    try:
        synsets = wn.synsets(word, pos="n")
    except (LookupError, Exception):  # noqa: BLE001 — WordNet data may not be downloaded
        return None
    if not synsets:
        return None
    primary = synsets[0]
    paths = primary.hypernym_paths()
    if not paths:
        return None
    path = paths[0]
    for level in target_levels:
        if level < len(path):
            return path[level].name().split(".")[0]
    return None


def _normalized_shannon_entropy(counts: dict[str, int]) -> float:
    """Shannon entropy of the ``counts`` distribution, normalized to [0, 1].

    The normalization divides by ``log2(n)`` where n is the
    number of distinct categories — so an even spread across all
    categories gives entropy 1.0 and a concentration on one
    category gives 0.0.

    Empty input returns 0.0 (no scatter to measure).
    Single-category input returns 0.0 (no scatter; concentrated).
    """
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    n_categories = len(counts)
    if n_categories <= 1:
        return 0.0
    raw = -sum(
        (c / total) * math.log2(c / total)
        for c in counts.values() if c > 0
    )
    max_possible = math.log2(n_categories)
    if max_possible <= 0:
        return 0.0
    return raw / max_possible


def prestige_metaphor_density(
    text: str,
    *,
    nlp: Any,
    t1: float = image_conjunction.DEFAULT_T1_CONCRETENESS_GAP,
    t2: float = image_conjunction.DEFAULT_T2_EMBEDDING_SIMILARITY,
    t3: float = DEFAULT_T3_DOMAIN_SCATTER_ENTROPY,
    use_wordnet: bool = True,
    extra_domains: Optional[dict[str, str]] = None,
    concreteness_path: Optional[Path] = None,
    baseline_value: Optional[float] = None,
    baseline_source: Optional[str] = None,
) -> dict[str, Any]:
    """Compute AIC-8 prestige-metaphor density + JSON-ready block.

    Runs the image-conjunction detector first, then classifies each
    conjunction's abstract member into a prestige domain. Reports
    density (per 1000 tokens), domain distribution, normalized
    domain-scatter entropy, and the joint prestige+scatter flag.

    The flag fires when domain_scatter_entropy > t3 AND
    image_conjunction_density > baseline_value (if provided). The
    raw entropy is emitted regardless of whether the flag fires.
    """
    ic_block = image_conjunction.image_conjunction_density(
        text,
        nlp=nlp, t1=t1, t2=t2,
        concreteness_path=concreteness_path,
    )

    # Classify each conjunction's **scaffolding word**.
    #
    # Spec note: `SPEC_aic_8_9_implementation.md` Step 7 says
    # "the lower-concreteness member of the pair". But the spec's
    # running examples ("machinery of grief", "architecture of
    # grief", "topology of attention", "grammar of desire") have
    # the PRESTIGE-DOMAIN word (machinery, architecture, topology,
    # grammar) at *higher* Brysbaert concreteness than the
    # emotional/cognitive target (grief, attention, desire). The
    # spec's §AIC-8 description ("scaffolding word is drawn from
    # intellectually-serious domains: architecture, grammar,
    # cartography, ecology, machinery, weather, ritual,
    # infrastructure, topology, geology, economy, music, theater,
    # mathematics, biology, navigation, geometry, choreography")
    # identifies these higher-concreteness words as the prestige-
    # domain set. The detection has to classify these to make the
    # "metaphor confetti" scatter-entropy diagnostic work.
    #
    # Resolution: classify the HIGHER-concreteness member as the
    # scaffolding/prestige word. This is the operationally
    # correct reading; the parenthetical in Step 7 is the
    # inconsistent piece. Documented here and in module docstring
    # so any future spec edit can resolve the contradiction.
    domain_counts: dict[str, int] = {}
    classified: list[dict[str, Any]] = []
    unclassified_count = 0
    for c in ic_block["conjunctions"]:
        # The scaffolding word is the higher-concreteness member;
        # see comment block above for the spec-interpretation
        # rationale.
        if c["concreteness_a"] >= c["concreteness_b"]:
            scaffolding = c["word_a"]
            target = c["word_b"]
        else:
            scaffolding = c["word_b"]
            target = c["word_a"]
        domain = classify_domain(
            scaffolding,
            use_wordnet=use_wordnet,
            extra_domains=extra_domains,
        )
        rec = dict(c)
        rec["scaffolding_word"] = scaffolding
        rec["target_word"] = target
        rec["domain"] = domain
        classified.append(rec)
        if domain is None:
            unclassified_count += 1
            continue
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

    classified_count = sum(domain_counts.values())
    entropy = _normalized_shannon_entropy(domain_counts)

    # Per-1000-token density of prestige metaphors specifically
    # (only the classified ones, since unclassified abstract words
    # don't count toward "prestige domain scatter").
    total_tokens = ic_block["diagnostics"]["total_tokens"]
    prestige_density_per_1k = (
        (classified_count / total_tokens) * 1000 if total_tokens > 0 else 0.0
    )

    # The joint flag.
    flag_fires = entropy > t3
    if baseline_value is not None:
        flag_fires = flag_fires and (
            prestige_density_per_1k > baseline_value
        )

    block: dict[str, Any] = {
        "signal_path": "aic_8_9.prestige_metaphor_density",
        "family": "aic-8-aesthetic-authority-laundering",
        "value": prestige_density_per_1k,
        "domain_scatter_entropy": entropy,
        "domain_distribution": dict(sorted(
            domain_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )),
        "flag_fires": flag_fires,
        "polarity": "↑",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "conjunctions": classified,
        "diagnostics": {
            "total_tokens": total_tokens,
            "total_paragraphs": ic_block["diagnostics"]["total_paragraphs"],
            "conjunction_count": ic_block["diagnostics"]["conjunction_count"],
            "classified_count": classified_count,
            "unclassified_count": unclassified_count,
            "n_distinct_domains": len(domain_counts),
            "threshold_t1_concreteness_gap": t1,
            "threshold_t2_embedding_similarity": t2,
            "threshold_t3_scatter_entropy": t3,
            "wordnet_used": use_wordnet and _wordnet_available(),
        },
    }

    if baseline_value is not None:
        block["baseline_comparison"] = {
            "baseline_source": baseline_source or "operator-supplied",
            "baseline_value": baseline_value,
            "elevation_factor": (
                prestige_density_per_1k / baseline_value
                if baseline_value > 0 else None
            ),
        }
    return block


def _wordnet_available() -> bool:
    """Return True if NLTK + WordNet data are both installed."""
    try:
        from nltk.corpus import wordnet as wn  # type: ignore
        # Probe with a known synset; raises LookupError if data
        # isn't downloaded.
        wn.synsets("test", pos="n")
        return True
    except (ImportError, LookupError, Exception):  # noqa: BLE001
        return False


# ---------- CLI ------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AIC-8 prestige-metaphor detector. Builds on the image-"
            "conjunction detector by classifying each abstract "
            "word into a prestige domain, then computing the "
            "normalized Shannon entropy of the domain distribution. "
            "High entropy + elevated density = AIC-8 prestige-"
            "metaphor signature ('metaphor confetti')."
        ),
    )
    parser.add_argument(
        "input", type=Path,
        help="Path to a text or Markdown file to audit.",
    )
    parser.add_argument(
        "--t1", type=float,
        default=image_conjunction.DEFAULT_T1_CONCRETENESS_GAP,
        help="Concreteness gap threshold (default: 2.5).",
    )
    parser.add_argument(
        "--t2", type=float,
        default=image_conjunction.DEFAULT_T2_EMBEDDING_SIMILARITY,
        help="Embedding cosine similarity threshold (default: 0.4).",
    )
    parser.add_argument(
        "--t3", type=float,
        default=DEFAULT_T3_DOMAIN_SCATTER_ENTROPY,
        help="Normalized scatter-entropy threshold (default: 0.7).",
    )
    parser.add_argument(
        "--no-wordnet", action="store_true",
        help=(
            "Disable the WordNet fallback. Hardcoded "
            "PRESTIGE_DOMAIN_VOCAB only. Useful when NLTK data is "
            "unavailable or for reproducible test runs."
        ),
    )
    parser.add_argument(
        "--baseline", type=float, default=None, metavar="VALUE",
        help=(
            "Prestige-metaphor density baseline per 1000 tokens "
            "(optional). Spec starting points: 2/1000 contemporary "
            "essay; 1/1000 literary fiction."
        ),
    )
    parser.add_argument(
        "--baseline-source", type=str, default="operator-supplied",
        metavar="LABEL",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Write JSON to this path (default: stdout).",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1

    text = args.input.read_text(encoding="utf-8")
    nlp = image_conjunction._load_spacy_with_parsing()

    try:
        block = prestige_metaphor_density(
            text,
            nlp=nlp,
            t1=args.t1, t2=args.t2, t3=args.t3,
            use_wordnet=not args.no_wordnet,
            baseline_value=args.baseline,
            baseline_source=(
                args.baseline_source if args.baseline else None
            ),
        )
    except embeddings.EmbeddingsBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(block, indent=2)
    if args.out is None:
        print(output)
    else:
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
