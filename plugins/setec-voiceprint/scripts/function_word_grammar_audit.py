#!/usr/bin/env python3
"""function_word_grammar_audit.py — function-word sequence grammar.

Surfaces Tier-2 promotion, paired-release schedule Release 5.
The function-word feature family is currently used at frequency
level via Burrows Delta in ``voice_distance.py``. This module
adds the **sequence layer**: function-word n-grams, preposition
profile, demonstrative usage, relative-pronoun choice,
complementizer choice, subordinator profile, auxiliary chains,
and pronoun transition patterns.

Classical authorship attribution leans heavily on function words
because they're relatively content-independent. The frequency
view alone misses that the SAME function-word frequencies can
arrange into different *grammars* — e.g., one writer alternates
``which`` and ``that`` for relative-clause variety while another
uses only ``that``; both have similar ``which`` and ``that``
frequencies if the alternation rate is within the grand mean,
but the alternation pattern itself is voice-distinguishing.

Output:

  - Function-word n-gram inventory (bigrams + trigrams across the
    canonical FUNCTION_WORDS set; counts + top-K).
  - Preposition profile: per-preposition frequency.
  - Demonstrative usage: this / that / these / those rates and
    distinct-vs-same-token-following ratios.
  - Relative pronoun choice: which / that / who / whom proportions.
  - Complementizer choice: that / if / whether proportions.
  - Subordinator profile: because / although / while / when / since
    / before / after / unless / until proportions.
  - Auxiliary chain frequency: 2+ auxiliary verbs in a row
    (`have been`, `will have been`, `could have been`).
  - Pronoun transition: same-pronoun vs. different-pronoun
    transitions across consecutive sentences.

Compression-fraction band over six rhythm signals: function-word
n-gram entropy floor, preposition-profile collapse, single-
demonstrative-dominance, relative-pronoun monotony, subordinator
poverty, pronoun-transition uniformity.

Hardened baseline ingestion (1.34.x conventions).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore
from preprocessing import strip_non_prose  # type: ignore
from stylometry_core import FUNCTION_WORDS  # type: ignore

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "function_word_grammar_audit"
SCRIPT_VERSION = "1.0"


# --- Curated lexicons ------------------------------------------

_PREPOSITIONS = frozenset({
    "of", "in", "to", "for", "with", "on", "at", "by", "from",
    "about", "as", "into", "through", "after", "over", "between",
    "out", "against", "during", "without", "before", "under",
    "around", "among", "above", "across", "behind", "beyond",
    "near", "off", "since", "toward", "towards", "upon", "within",
    "along", "beside", "below",
})

_DEMONSTRATIVES = frozenset({"this", "that", "these", "those"})
_RELATIVE_PRONOUNS = frozenset({"which", "that", "who", "whom", "whose"})
_COMPLEMENTIZERS = frozenset({"that", "if", "whether"})
_SUBORDINATORS = frozenset({
    "because", "although", "though", "while", "when", "since",
    "before", "after", "unless", "until", "whereas", "if",
    "wherever", "whenever",
})
_AUXILIARIES = frozenset({
    "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "having",
    "do", "does", "did",
    "will", "would", "shall", "should", "may", "might",
    "must", "can", "could",
})
_PRONOUNS = frozenset({
    "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their",
})

_WORD_RE = re.compile(r"\b\w+\b")
_SENTENCE_TERMINATORS = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")


def _tokens_lower(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _entropy(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def _per_thousand(count: int, n_words: int) -> float:
    if n_words <= 0:
        return 0.0
    return 1000.0 * count / n_words


def _sentences(text: str) -> list[str]:
    return [
        s.strip()
        for s in _SENTENCE_TERMINATORS.split(text)
        if s.strip()
    ]


def audit_function_word_grammar(text: str) -> dict[str, Any]:
    """Compute the function-word sequence grammar features. Pure
    function; no I/O."""
    n_words = _tokens_lower(text)
    n_total = len(n_words)
    if n_total == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }

    # Function-word n-grams (over FUNCTION_WORDS only; non-function
    # tokens act as boundary breaks for the n-gram extraction).
    function_word_runs: list[list[str]] = []
    cur_run: list[str] = []
    for tok in n_words:
        if tok in FUNCTION_WORDS:
            cur_run.append(tok)
        else:
            if len(cur_run) >= 2:
                function_word_runs.append(cur_run)
            cur_run = []
    if len(cur_run) >= 2:
        function_word_runs.append(cur_run)

    bigram_counts: Counter[tuple[str, str]] = Counter()
    trigram_counts: Counter[tuple[str, str, str]] = Counter()
    for run in function_word_runs:
        for i in range(len(run) - 1):
            bigram_counts[(run[i], run[i + 1])] += 1
        for i in range(len(run) - 2):
            trigram_counts[(run[i], run[i + 1], run[i + 2])] += 1

    n_function_words = sum(1 for t in n_words if t in FUNCTION_WORDS)

    # Preposition profile.
    preposition_counts: Counter[str] = Counter(
        t for t in n_words if t in _PREPOSITIONS
    )
    preposition_entropy = _entropy(dict(preposition_counts))

    # Demonstrative usage.
    demonstrative_counts: Counter[str] = Counter(
        t for t in n_words if t in _DEMONSTRATIVES
    )
    n_demonstratives = sum(demonstrative_counts.values())

    # Relative pronoun choice. To distinguish "the cat that sat"
    # (relative) from complementizer "that" ("She said that..."),
    # we approximate by counting words in the relative-pronoun set
    # that follow a noun (proxy: any word ending in -tion / -ment /
    # plural -s / etc., or simply: this is a heuristic count).
    # Conservative: we count all instances and label them relative-
    # pronoun-set tokens; the full distinction needs spaCy.
    relative_counts: Counter[str] = Counter()
    for w in n_words:
        if w in _RELATIVE_PRONOUNS:
            relative_counts[w] += 1

    # Complementizer choice (raw counts; same ambiguity as above).
    complementizer_counts: Counter[str] = Counter()
    for w in n_words:
        if w in _COMPLEMENTIZERS:
            complementizer_counts[w] += 1

    # Subordinator profile.
    subordinator_counts: Counter[str] = Counter()
    for w in n_words:
        if w in _SUBORDINATORS:
            subordinator_counts[w] += 1
    subordinator_entropy = _entropy(dict(subordinator_counts))

    # Auxiliary chains: 2+ consecutive auxiliaries.
    aux_chains = 0
    cur_aux_run = 0
    for tok in n_words:
        if tok in _AUXILIARIES:
            cur_aux_run += 1
        else:
            if cur_aux_run >= 2:
                aux_chains += 1
            cur_aux_run = 0
    if cur_aux_run >= 2:
        aux_chains += 1

    # Pronoun transition: across consecutive sentences, does the
    # leading pronoun match? Same = stable focus; different =
    # active perspective shifting.
    sentences = _sentences(text)
    sentence_pronouns: list[str | None] = []
    for s in sentences:
        first_tokens = _tokens_lower(s)
        first_pronoun = None
        for tok in first_tokens[:6]:  # check first 6 tokens
            if tok in _PRONOUNS:
                first_pronoun = tok
                break
        sentence_pronouns.append(first_pronoun)
    transitions_same = 0
    transitions_diff = 0
    transitions_total = 0
    for a, b in zip(sentence_pronouns, sentence_pronouns[1:]):
        if a is None or b is None:
            continue
        transitions_total += 1
        if a == b:
            transitions_same += 1
        else:
            transitions_diff += 1
    if transitions_total > 0:
        same_share = transitions_same / transitions_total
    else:
        same_share = 0.5

    flagged_signals: list[str] = []

    # Function-word bigram entropy floor: if the writer reuses the
    # same handful of function-word pairs, their grammar collapsed.
    bigram_entropy = _entropy(
        {f"{a} {b}": c for (a, b), c in bigram_counts.items()}
    )
    if bigram_entropy < 4.0 and sum(bigram_counts.values()) >= 30:
        flagged_signals.append("low_function_bigram_entropy")

    # Preposition profile entropy: if 1-2 prepositions dominate,
    # spatial / relational vocabulary collapsed.
    if (
        preposition_entropy < 2.5
        and sum(preposition_counts.values()) >= 20
    ):
        flagged_signals.append("low_preposition_entropy")

    # Single demonstrative dominance: > 80% of demonstratives are
    # "this" / "that" alone.
    if n_demonstratives >= 5:
        max_dem = max(demonstrative_counts.values())
        if max_dem / n_demonstratives >= 0.80:
            flagged_signals.append("single_demonstrative_dominance")

    # Relative-pronoun monotony: > 90% of relative-pronoun-set
    # tokens are a single word.
    rel_total = sum(relative_counts.values())
    if rel_total >= 5:
        max_rel = max(relative_counts.values())
        if max_rel / rel_total >= 0.90:
            flagged_signals.append("relative_pronoun_monotony")

    # Subordinator poverty: < 1 / 1k for prose ≥ 500 words.
    if (
        _per_thousand(sum(subordinator_counts.values()), n_total) < 1.5
        and n_total >= 500
    ):
        flagged_signals.append("low_subordinator_density")

    # Pronoun transition uniformity: > 90% same-pronoun transitions
    # = single-perspective tunnel; < 10% = no perspective continuity.
    if transitions_total >= 5:
        if same_share >= 0.90 or same_share < 0.10:
            flagged_signals.append("uniform_pronoun_transition")

    n_signals = 6
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly grammar-shifted"
    elif compression_fraction < 0.50:
        band = "Moderately grammar-shifted"
    else:
        band = "Heavily grammar-shifted"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_total,
        "n_function_words": n_function_words,
        "function_word_ratio": (
            n_function_words / n_total if n_total else 0.0
        ),
        "function_bigrams": dict(
            (f"{a} {b}", c) for (a, b), c in bigram_counts.most_common(20)
        ),
        "function_bigram_entropy_bits": round(bigram_entropy, 3),
        "n_function_trigrams": sum(trigram_counts.values()),
        "preposition_counts": dict(preposition_counts.most_common(15)),
        "preposition_entropy_bits": round(preposition_entropy, 3),
        "demonstrative_counts": dict(demonstrative_counts),
        "relative_pronoun_counts": dict(relative_counts),
        "complementizer_counts": dict(complementizer_counts),
        "subordinator_counts": dict(subordinator_counts.most_common(15)),
        "subordinator_entropy_bits": round(subordinator_entropy, 3),
        "auxiliary_chain_count": aux_chains,
        "pronoun_transition": {
            "same": transitions_same,
            "different": transitions_diff,
            "total": transitions_total,
            "same_share": round(same_share, 3),
        },
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline + comparison + render + CLI ----------------------


def audit_baseline_function_grammar(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    paths = [p for p in paths if not p.name.lower().startswith("readme")]

    target_resolved: Path | None = None
    if target_path is not None:
        try:
            target_resolved = Path(target_path).resolve()
        except OSError:
            target_resolved = None

    skipped_files: list[dict[str, str]] = []
    per_file: list[dict[str, Any]] = []
    pooled_bigram_entropy: list[float] = []
    pooled_preposition_entropy: list[float] = []
    pooled_subordinator_entropy: list[float] = []
    pooled_function_ratio: list[float] = []
    pooled_aux_chains: list[float] = []
    pooled_same_share: list[float] = []
    next_anon_id = 1

    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from function-word "
                        "grammar baseline (matches target path)\n"
                    )
                    continue
            except OSError:
                pass
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{len(skipped_files):03d}",
                "reason": f"unreadable: {exc}",
            })
            continue
        cleaned, _ = strip_non_prose(
            raw, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        a = audit_function_word_grammar(cleaned)
        if not a.get("available"):
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{next_anon_id:03d}",
                "reason": f"audit unavailable: {a.get('reason', 'unknown')}",
            })
            next_anon_id += 1
            continue
        per_file.append({
            "file": (
                p.name if include_filenames
                else f"baseline_{next_anon_id:03d}"
            ),
            "function_word_ratio": a["function_word_ratio"],
            "function_bigram_entropy_bits": a["function_bigram_entropy_bits"],
            "preposition_entropy_bits": a["preposition_entropy_bits"],
            "subordinator_entropy_bits": a["subordinator_entropy_bits"],
            "auxiliary_chain_count": a["auxiliary_chain_count"],
            "pronoun_transition_same_share": a["pronoun_transition"]["same_share"],
        })
        next_anon_id += 1
        pooled_function_ratio.append(a["function_word_ratio"])
        pooled_bigram_entropy.append(a["function_bigram_entropy_bits"])
        pooled_preposition_entropy.append(a["preposition_entropy_bits"])
        pooled_subordinator_entropy.append(a["subordinator_entropy_bits"])
        pooled_aux_chains.append(float(a["auxiliary_chain_count"]))
        pooled_same_share.append(a["pronoun_transition"]["same_share"])

    def _mean_sd(vs: list[float]) -> dict[str, float]:
        if not vs:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = sum(vs) / len(vs)
        if len(vs) > 1:
            var = sum((x - m) ** 2 for x in vs) / (len(vs) - 1)
            sd = var ** 0.5
        else:
            sd = 0.0
        return {"mean": m, "sd": sd, "n": len(vs)}

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate": {
            "function_word_ratio": _mean_sd(pooled_function_ratio),
            "function_bigram_entropy_bits": _mean_sd(pooled_bigram_entropy),
            "preposition_entropy_bits": _mean_sd(pooled_preposition_entropy),
            "subordinator_entropy_bits": _mean_sd(pooled_subordinator_entropy),
            "auxiliary_chain_count": _mean_sd(pooled_aux_chains),
            "pronoun_transition_same_share": _mean_sd(pooled_same_share),
        },
        "include_filenames": include_filenames,
    }


def compare_to_baseline(
    target: dict[str, Any],
    baseline_block: dict[str, Any],
) -> dict[str, Any]:
    if not target.get("available"):
        return {"available": False, "reason": "target unavailable"}
    if baseline_block.get("n_files", 0) == 0:
        return {"available": False, "reason": "baseline empty"}
    agg = baseline_block["aggregate"]

    def _z(value: float, bucket: dict[str, float]) -> float | None:
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            return None
        return (value - bucket["mean"]) / sd

    return {
        "available": True,
        "z_function_word_ratio": _z(
            target["function_word_ratio"],
            agg["function_word_ratio"],
        ),
        "z_function_bigram_entropy": _z(
            target["function_bigram_entropy_bits"],
            agg["function_bigram_entropy_bits"],
        ),
        "z_preposition_entropy": _z(
            target["preposition_entropy_bits"],
            agg["preposition_entropy_bits"],
        ),
        "z_subordinator_entropy": _z(
            target["subordinator_entropy_bits"],
            agg["subordinator_entropy_bits"],
        ),
        "z_auxiliary_chain_count": _z(
            float(target["auxiliary_chain_count"]),
            agg["auxiliary_chain_count"],
        ),
        "z_pronoun_transition_same_share": _z(
            target["pronoun_transition"]["same_share"],
            agg["pronoun_transition_same_share"],
        ),
    }


# --- Markdown rendering ----------------------------------------


def _claim_license(audit: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Function-word sequence-grammar profile of the input: "
            "n-gram inventory + entropy, preposition profile, "
            "demonstrative usage, relative-pronoun and "
            "complementizer choice, subordinator profile, "
            "auxiliary chains, pronoun transitions. Surfaces "
            "*how* the writer arranges function words, beyond "
            "the frequency picture Burrows Delta provides."
        ),
        does_not_license=(
            "An AI-provenance verdict. Function-word grammar varies "
            "with register, education, and audience as well as "
            "voice. The differential diagnosis of cause is the "
            "confounder audit's job."
        ),
        comparison_set={
            "n_words": audit.get("n_words"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Relative-pronoun and complementizer counts can't "
            "distinguish (e.g.) `that` as relativizer vs. as "
            "complementizer without spaCy parsing. The audit "
            "reports raw token counts; baseline comparison handles "
            "the genre / writer norm.",
            "Heuristic thresholds (band call) are calibration-"
            "pending; treat the band as a cue, not a verdict.",
            "Function-word grammar is genre-bound: legal prose "
            "has high subordinator density; lyrical prose may "
            "have very low. Read alongside register match.",
        ],
    )


def _claim_license_block(audit: dict[str, Any]) -> str:
    return _claim_license(audit).render_block().rstrip()


_RESULTS_KEYS = (
    "n_function_words", "function_word_ratio",
    "function_bigrams", "function_bigram_entropy_bits",
    "n_function_trigrams",
    "preposition_counts", "preposition_entropy_bits",
    "demonstrative_counts", "relative_pronoun_counts",
    "complementizer_counts",
    "subordinator_counts", "subordinator_entropy_bits",
    "auxiliary_chain_count", "pronoun_transition",
    "compression",
)


def build_audit_payload(
    audit: dict[str, Any],
    *,
    target_path: Path | str,
    baseline_block: dict[str, Any] | None,
    baseline_comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    """Wrap the function-word grammar audit dict in the schema_version
    1.0 envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    available = bool(audit.get("available", True))
    n_words = int(audit.get("n_words", 0) or 0)
    target_extra: dict[str, Any] = {}
    if "preprocessing" in audit:
        target_extra["preprocessing"] = audit["preprocessing"]

    results: dict[str, Any] = {}
    if available:
        for k in _RESULTS_KEYS:
            if k in audit:
                results[k] = audit[k]
        if baseline_comparison is not None:
            results["baseline_comparison"] = baseline_comparison

    baseline_meta: dict[str, Any] | None = None
    if baseline_block is not None:
        baseline_meta = build_baseline_metadata(
            n_files=int(baseline_block.get("n_files", 0) or 0),
            words=int(baseline_block.get("n_words", 0) or 0),
            extra={
                k: v for k, v in baseline_block.items()
                if k not in {"n_files", "n_words"}
            } or None,
        )

    warnings: list[str] = []
    if not available and "reason" in audit:
        warnings.append(audit["reason"])

    lic = _claim_license(audit) if available else None

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=n_words,
        baseline=baseline_meta,
        results=results,
        claim_license=lic,
        available=available,
        warnings=warnings,
        target_extra=target_extra or None,
    )


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            "# Function-word grammar audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    lines: list[str] = [
        "# Function-word grammar audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}  "
        f"**Function-word ratio:** {audit['function_word_ratio']:.3f}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        "## Sequence-grammar metrics",
        "",
        f"- **Function-word bigram entropy:** "
        f"{audit['function_bigram_entropy_bits']:.2f} bits",
        f"- **Preposition entropy:** "
        f"{audit['preposition_entropy_bits']:.2f} bits",
        f"- **Subordinator entropy:** "
        f"{audit['subordinator_entropy_bits']:.2f} bits",
        f"- **Auxiliary chain count:** "
        f"{audit['auxiliary_chain_count']}",
        f"- **Pronoun transition same-share:** "
        f"{audit['pronoun_transition']['same_share']:.2%} "
        f"({audit['pronoun_transition']['same']}/"
        f"{audit['pronoun_transition']['total']})",
        "",
    ]
    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    bigrams = audit.get("function_bigrams", {})
    if bigrams:
        lines.append("## Top function-word bigrams")
        lines.append("")
        lines.append("| bigram | count |")
        lines.append("|---|---:|")
        for bg, cnt in list(bigrams.items())[:10]:
            lines.append(f"| `{bg}` | {cnt} |")
        lines.append("")

    if audit.get("preposition_counts"):
        lines.append("## Preposition profile (top)")
        lines.append("")
        lines.append("| preposition | count |")
        lines.append("|---|---:|")
        for prep, cnt in list(audit["preposition_counts"].items())[:10]:
            lines.append(f"| {prep} | {cnt} |")
        lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| signal | z-score |")
        lines.append("|---|---:|")
        for label, key in (
            ("function_word_ratio", "z_function_word_ratio"),
            ("function_bigram_entropy", "z_function_bigram_entropy"),
            ("preposition_entropy", "z_preposition_entropy"),
            ("subordinator_entropy", "z_subordinator_entropy"),
            ("auxiliary_chain_count", "z_auxiliary_chain_count"),
            ("pronoun_transition_same_share",
             "z_pronoun_transition_same_share"),
        ):
            z = baseline_comparison.get(key)
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {label} | {z_str} |")
        lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="function_word_grammar_audit.py",
        description=(
            "Function-word sequence-grammar audit. Surfaces how the "
            "writer arranges function words (n-grams, preposition "
            "profile, relative-pronoun choice, subordinator profile, "
            "auxiliary chains, pronoun transitions), beyond the "
            "frequency picture Burrows Delta provides."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument("--baseline-dir", help="Optional baseline directory.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--out", help="Write output to this path.")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules", help="Comma-separated strip rules.")
    p.add_argument("--strip-aggressive", action="store_true")
    p.add_argument(
        "--strip-masking",
        help="Optional masking profile (prose_body_only, etc.).",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help=(
            "Include raw baseline filenames in `per_file_summaries` "
            "(privacy default: anonymized as `baseline_001`)."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2
    raw = target_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, prep_meta = strip_non_prose(
        raw, args.strip_rules,
        allow_non_prose=args.allow_non_prose,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
    )
    audit = audit_function_word_grammar(cleaned)
    audit["preprocessing"] = prep_meta

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_function_grammar(
                args.baseline_dir,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
                strip_masking=args.strip_masking,
                target_path=target_path,
                include_filenames=args.include_baseline_filenames,
            )
        except FileNotFoundError as exc:
            sys.stderr.write(f"  baseline error: {exc}\n")
            return 2
        audit["baseline_block"] = block
        if block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files; baseline comparison skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, block)
        audit["baseline_comparison"] = baseline_comparison

    if args.json:
        payload = build_audit_payload(
            audit,
            target_path=target_path,
            baseline_block=audit.get("baseline_block"),
            baseline_comparison=baseline_comparison,
        )
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(audit, baseline_comparison)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
