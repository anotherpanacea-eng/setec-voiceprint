#!/usr/bin/env python3
"""aesthetic_authority_audit.py — compound AIC-8 + AIC-9 audit.

Runs the three AIC-8/9 detectors (kicker density, image conjunction,
prestige metaphor) in parallel and computes joint co-occurrence
metrics that the individual detectors can't see. Per
`internal/SPEC_aic_8_9_implementation.md` Step 8.

The joint signature is the strongest single AIC-8/9 evidence:
paragraphs that simultaneously close with a kicker-shaped sentence
AND contain an image conjunction with prestige-domain
classification are the canonical AI-prose "performing aesthetic
authority" pattern.

Joint metrics:

  * ``kicker_with_image_conjunction``: of paragraphs that close
    with a kicker shape, what proportion contain at least one
    image conjunction anywhere in the paragraph?
  * ``kicker_with_prestige_metaphor``: same, but for paragraphs
    where the image conjunction is classified into a prestige
    domain (filters out the WordNet-fallback "feeling" /
    "act" / similar long-tail classifications).
  * ``all_three_co_occurrence``: proportion of paragraphs that
    end with a kicker AND contain an image conjunction with a
    hardcoded-prestige-domain classification (most stringent).

CLI usage::

    python3 scripts/aesthetic_authority_audit.py path/to/draft.md
    python3 scripts/aesthetic_authority_audit.py path/to/draft.md \\
        --register contemporary_essay
    python3 scripts/aesthetic_authority_audit.py path/to/draft.md \\
        --t1 2.0 --t2 0.5 --t3 0.6

Dependencies: everything the three component detectors require
(spaCy with parsing + vectors, Brysbaert CSV, optional NLTK
WordNet for prestige-metaphor fallback, PyYAML for register-
typical baselines).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import embeddings  # type: ignore
import image_conjunction  # type: ignore
import kicker_density  # type: ignore
import prestige_metaphor  # type: ignore
import register_typical_baselines  # type: ignore


def _paragraphs_with_image_conjunctions(
    ic_block: dict[str, Any],
) -> set[int]:
    """Return the set of paragraph indices that contain at least one
    image conjunction (any classification)."""
    return {
        c["paragraph_index"] for c in ic_block.get("conjunctions", [])
    }


def _paragraphs_with_prestige_classified_conjunctions(
    pm_block: dict[str, Any],
) -> set[int]:
    """Return paragraph indices that contain at least one image
    conjunction whose scaffolding word classified into a prestige
    domain (hardcoded list OR WordNet hypernym)."""
    return {
        c["paragraph_index"]
        for c in pm_block.get("conjunctions", [])
        if c.get("domain") is not None
    }


def _paragraphs_with_hardcoded_prestige_conjunctions(
    pm_block: dict[str, Any],
) -> set[int]:
    """Return paragraph indices that contain at least one image
    conjunction whose scaffolding word classified into the
    hardcoded `PRESTIGE_DOMAIN_VOCAB` (most stringent: rules out
    WordNet fallback)."""
    hardcoded_domains = set(prestige_metaphor.PRESTIGE_DOMAIN_VOCAB.values())
    return {
        c["paragraph_index"]
        for c in pm_block.get("conjunctions", [])
        if c.get("domain") in hardcoded_domains
    }


def aesthetic_authority_audit(
    text: str,
    *,
    nlp: Any,
    t1: float = image_conjunction.DEFAULT_T1_CONCRETENESS_GAP,
    t2: float = image_conjunction.DEFAULT_T2_EMBEDDING_SIMILARITY,
    t3: float = prestige_metaphor.DEFAULT_T3_DOMAIN_SCATTER_ENTROPY,
    word_limit: int = kicker_density.DEFAULT_WORD_LIMIT,
    use_wordnet: bool = True,
    register: Optional[str] = None,
    explicit_baselines: Optional[dict[str, float]] = None,
    yaml_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Run the three AIC-8/9 detectors + compute joint metrics.

    Returns a JSON-ready dict per spec §8 schema. Includes:

      * The three component blocks (kicker_density, image_conjunction,
        prestige_metaphor) at full fidelity.
      * Joint co-occurrence metrics under ``compound``.
      * Resolved baselines (precedence: explicit > register-typical).

    ``register``: optional register name (e.g., ``"contemporary_essay"``,
    ``"literary_fiction"``) used to resolve register-typical
    baselines from ``baselines/register_typical.yaml``. Explicit
    baselines (passed via ``explicit_baselines={"kicker_density":
    0.10, ...}``) take precedence.
    """
    # Resolve per-signal baselines.
    explicit_baselines = explicit_baselines or {}

    def _resolve(signal: str) -> Optional[dict[str, Any]]:
        return register_typical_baselines.resolve_baseline(
            register, signal,
            explicit_value=explicit_baselines.get(signal),
            explicit_source=(
                "operator-supplied"
                if explicit_baselines.get(signal) is not None
                else None
            ),
            yaml_path=yaml_path,
        )

    kicker_baseline = _resolve("kicker_density")
    ic_baseline = _resolve("image_conjunction_per_1000_tokens")
    pm_baseline = _resolve("prestige_metaphor_per_1000_tokens")

    # Run the three detectors.
    kicker_block = kicker_density.kicker_density(
        text, nlp=nlp, word_limit=word_limit,
        baseline_value=(
            kicker_baseline["value"] if kicker_baseline else None
        ),
        baseline_source=(
            kicker_baseline["source"] if kicker_baseline else None
        ),
    )
    pm_block = prestige_metaphor.prestige_metaphor_density(
        text, nlp=nlp, t1=t1, t2=t2, t3=t3, use_wordnet=use_wordnet,
        baseline_value=(
            pm_baseline["value"] if pm_baseline else None
        ),
        baseline_source=(
            pm_baseline["source"] if pm_baseline else None
        ),
    )
    # The prestige_metaphor block embeds the full image-conjunction
    # detection. Extract a separate ic_block view from the same run
    # so downstream consumers see all three blocks; we don't re-run
    # the (relatively expensive) parse + filter pipeline.
    ic_block = {
        "signal_path": "aic_8_9.image_conjunction_density",
        "family": "aic-8-aesthetic-authority-laundering",
        "value": _conjunctions_per_1000(pm_block),
        "spacing_variance": _spacing_variance_from_conjunctions(pm_block),
        "paragraph_final_co_occurrence_rate": _paragraph_final_rate(pm_block),
        "polarity": "↑",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "conjunctions": pm_block.get("conjunctions", []),
        "diagnostics": {
            "total_tokens": pm_block["diagnostics"]["total_tokens"],
            "total_paragraphs": pm_block["diagnostics"]["total_paragraphs"],
            "conjunction_count": pm_block["diagnostics"]["conjunction_count"],
            "threshold_t1_concreteness_gap": t1,
            "threshold_t2_embedding_similarity": t2,
        },
    }
    if ic_baseline is not None:
        ic_block["baseline_comparison"] = {
            "baseline_source": ic_baseline["source"],
            "baseline_value": ic_baseline["value"],
            "elevation_factor": (
                ic_block["value"] / ic_baseline["value"]
                if ic_baseline["value"] > 0 else None
            ),
        }

    # Joint co-occurrence metrics.
    kicker_paragraph_indices = {
        p["paragraph_index"] for p in kicker_block.get("paragraphs", [])
        if p.get("is_kicker")
    }
    ic_paragraph_indices = _paragraphs_with_image_conjunctions(ic_block)
    prestige_classified_paragraphs = (
        _paragraphs_with_prestige_classified_conjunctions(pm_block)
    )
    hardcoded_prestige_paragraphs = (
        _paragraphs_with_hardcoded_prestige_conjunctions(pm_block)
    )

    n_kickers = len(kicker_paragraph_indices)

    kicker_with_ic_count = len(
        kicker_paragraph_indices & ic_paragraph_indices
    )
    kicker_with_prestige_count = len(
        kicker_paragraph_indices & prestige_classified_paragraphs
    )
    all_three_count = len(
        kicker_paragraph_indices & hardcoded_prestige_paragraphs
    )

    compound = {
        "kicker_paragraph_count": n_kickers,
        "kicker_with_image_conjunction_count": kicker_with_ic_count,
        "kicker_with_prestige_metaphor_count": kicker_with_prestige_count,
        "all_three_co_occurrence_count": all_three_count,
        "kicker_with_image_conjunction_rate": (
            kicker_with_ic_count / n_kickers if n_kickers > 0 else 0.0
        ),
        "kicker_with_prestige_metaphor_rate": (
            kicker_with_prestige_count / n_kickers
            if n_kickers > 0 else 0.0
        ),
        "all_three_co_occurrence_rate": (
            all_three_count / n_kickers if n_kickers > 0 else 0.0
        ),
        "signal_path": "aic_8_9.aesthetic_authority_compound",
        "family": "aic-8-9-compound",
        "polarity": "↑",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
    }

    return {
        "signal_path": "aic_8_9.aesthetic_authority_audit",
        "family": "aic-8-9-compound",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "aic_9_kicker_density": kicker_block,
        "aic_8_image_conjunction": ic_block,
        "aic_8_prestige_metaphor": pm_block,
        "compound": compound,
        "diagnostics": {
            "register": register,
            "thresholds": {
                "kicker_word_limit": word_limit,
                "t1_concreteness_gap": t1,
                "t2_embedding_similarity": t2,
                "t3_scatter_entropy": t3,
            },
            "use_wordnet": use_wordnet,
        },
    }


def _conjunctions_per_1000(pm_block: dict[str, Any]) -> float:
    total = pm_block["diagnostics"].get("total_tokens", 0)
    n = pm_block["diagnostics"].get("conjunction_count", 0)
    return (n / total) * 1000 if total > 0 else 0.0


def _spacing_variance_from_conjunctions(pm_block: dict[str, Any]) -> float:
    indices = sorted(
        c["paragraph_index"] for c in pm_block.get("conjunctions", [])
    )
    return image_conjunction._spacing_variance(indices)


def _paragraph_final_rate(pm_block: dict[str, Any]) -> float:
    conjs = pm_block.get("conjunctions", [])
    if not conjs:
        return 0.0
    final = sum(
        1 for c in conjs if c.get("is_paragraph_final_sentence")
    )
    return final / len(conjs)


# ---------- CLI ------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AIC-8 + AIC-9 compound audit: runs kicker density, "
            "image conjunction, and prestige metaphor in parallel "
            "and computes joint co-occurrence metrics. Per "
            "`internal/SPEC_aic_8_9_implementation.md` Step 8."
        ),
    )
    parser.add_argument(
        "input", type=Path,
        help="Path to a text or Markdown file to audit.",
    )
    parser.add_argument(
        "--register", type=str, default=None,
        help=(
            "Register name for register-typical baseline lookup "
            "(e.g., 'contemporary_essay', 'literary_fiction'). See "
            "`baselines/register_typical.yaml` for the shipped "
            "registers. Explicit --kicker-baseline / --ic-baseline / "
            "--pm-baseline values override register lookups."
        ),
    )
    parser.add_argument(
        "--kicker-baseline", type=float, default=None,
        metavar="VALUE",
        help="Explicit kicker_density baseline (proportion).",
    )
    parser.add_argument(
        "--ic-baseline", type=float, default=None,
        metavar="VALUE",
        help="Explicit image_conjunction_per_1000_tokens baseline.",
    )
    parser.add_argument(
        "--pm-baseline", type=float, default=None,
        metavar="VALUE",
        help="Explicit prestige_metaphor_per_1000_tokens baseline.",
    )
    parser.add_argument(
        "--t1", type=float,
        default=image_conjunction.DEFAULT_T1_CONCRETENESS_GAP,
        help="Image conjunction concreteness-gap threshold (default 2.5).",
    )
    parser.add_argument(
        "--t2", type=float,
        default=image_conjunction.DEFAULT_T2_EMBEDDING_SIMILARITY,
        help="Image conjunction cosine-similarity threshold (default 0.4).",
    )
    parser.add_argument(
        "--t3", type=float,
        default=prestige_metaphor.DEFAULT_T3_DOMAIN_SCATTER_ENTROPY,
        help="Prestige scatter-entropy threshold (default 0.7).",
    )
    parser.add_argument(
        "--word-limit", type=int,
        default=kicker_density.DEFAULT_WORD_LIMIT,
        help="Kicker word-count limit (default 15).",
    )
    parser.add_argument(
        "--no-wordnet", action="store_true",
        help="Disable the WordNet fallback for prestige-metaphor.",
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

    explicit = {}
    if args.kicker_baseline is not None:
        explicit["kicker_density"] = args.kicker_baseline
    if args.ic_baseline is not None:
        explicit["image_conjunction_per_1000_tokens"] = args.ic_baseline
    if args.pm_baseline is not None:
        explicit["prestige_metaphor_per_1000_tokens"] = args.pm_baseline

    # Wrap both the model load AND the compound audit in one
    # try/except. Dependency failures (no spaCy model installed,
    # no vectors-bearing model for the embedding similarity check)
    # all raise `EmbeddingsBackendError` and route through the
    # same actionable-message exit. Without this wrapping, the
    # operator saw a traceback instead of the install hint —
    # Codex P2 finding on PR #59.
    try:
        nlp = image_conjunction._load_spacy_with_parsing()
        block = aesthetic_authority_audit(
            text,
            nlp=nlp, t1=args.t1, t2=args.t2, t3=args.t3,
            word_limit=args.word_limit,
            use_wordnet=not args.no_wordnet,
            register=args.register,
            explicit_baselines=explicit,
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
