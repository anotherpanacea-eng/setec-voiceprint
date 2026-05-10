#!/usr/bin/env python3
"""generate_voice_report.py — populate a voice insights report.

Consumes JSON outputs from the existing voice-coherence scripts
(``voice_profile.py``, ``voice_drift_tracker.py``, ``idiolect_detector.py``)
and emits a markdown report shaped like the canonical template at
``references/templates/voice_insights_report.template.md``.

The template enforces an architectural split that the framework
considers load-bearing:

  * **Numerical sections** are populated programmatically — header
    counts, durable voiceprint tables, idiolectic vocabulary tables,
    cross-period distance matrices.
  * **Interpretive sections** are emitted as ``{TODO: interpret: <hint>}``
    markers. The script does NOT generate prose readings; the
    framework's deepest principle is that the writer's local read
    decides. The TODO hints carry enough detail (which feature, which
    direction, which magnitude) for an editor (human or LLM) to
    write the interpretation in a downstream pass.

Three report shapes are supported, chosen by which inputs are present:

  * **Profile-only.** ``--voice-profile`` only. Sections: Header,
    Durable voiceprint, Idiolectic vocabulary, Three observations,
    What this cannot say.
  * **Profile + drift.** Adds a drift section if ``--voice-drift`` is
    supplied.
  * **Profile + drift + comparison.** Adds a comparison-to-control
    section if ``--comparison-drift`` is supplied alongside the
    drift input.

Privacy: the report contains the writer's voiceprint signatures —
voice-cloning input. Default output goes under
``ai-prose-baselines-private/`` paths; the marker-based privacy guard
refuses non-private targets unless ``--allow-public-output`` is set.

Usage:

    python3 scripts/generate_voice_report.py \\
        --voice-profile path/to/voice_profile.json \\
        --voice-drift path/to/drift.json \\
        --idiolect-n1 path/to/idiolect_n1.json \\
        --idiolect-n2 path/to/idiolect_n2.json \\
        --idiolect-n3 path/to/idiolect_n3.json \\
        --comparison-drift path/to/control_drift.json \\
        --author-name "Author Name" \\
        --corpus-label "Author's blog" \\
        --register blog_essay \\
        --ai-disclosure "no AI use on the blog at any point" \\
        --out path/to/voice_insights.md

See ``references/templates/voice_insights_report.template.md`` for
the canonical template the output follows.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "generate_voice_report"
SCRIPT_VERSION = "1.0"


# Default location for the canonical template that ships with the
# plugin. Users can override with --template if they want to consume
# a customized version.
DEFAULT_TEMPLATE_PATH = (
    SCRIPT_DIR.parent / "references" / "templates"
    / "voice_insights_report.template.md"
)

# CV ceiling for "durable" features: a feature with cross-document
# coefficient-of-variation under this is rare enough to be load-
# bearing identity signal. Spec language: "CV under 0.10 is rare;
# CV under 0.05 is exceptional."
DURABLE_CV_CEILING = 0.10

# Per-section row caps. Reports are author-facing reading material;
# tables longer than ~12 rows lose readability.
MAX_DURABLE_FEATURES_PER_FAMILY = 8
MAX_IDIOLECT_TOPIC_ROWS = 12
MAX_IDIOLECT_RHETORICAL_ROWS = 8
MAX_DRIFT_FEATURES_PER_FAMILY = 6


# --------------- TODO marker helpers -----------------------------


def todo(hint: str) -> str:
    """Render an inline ``{TODO: interpret: <hint>}`` marker.

    Produced wherever the template's automation status table marks a
    section as ``manual`` or ``semi``. The hint string carries
    enough context for a downstream LLM/human pass to write the
    interpretation without re-reading the source JSON.
    """
    return "{TODO: interpret: " + hint + "}"


# --------------- Input dataclasses -------------------------------


@dataclass
class ReportInputs:
    """All structured inputs the renderer needs.

    Constructed in ``run()`` from CLI args; pure data, no I/O. Keeps
    section renderers pure functions for testability.
    """
    voice_profile: dict[str, Any]
    voice_drift: dict[str, Any] | None = None
    idiolect_n1: dict[str, Any] | None = None
    idiolect_n2: dict[str, Any] | None = None
    idiolect_n3: dict[str, Any] | None = None
    comparison_drift: dict[str, Any] | None = None
    author_name: str = "Unknown Author"
    corpus_label: str = "this corpus"
    register: str = "blog_essay"
    ai_disclosure: str | None = None
    control_writer_name: str = "the control writer"
    cv_ceiling: float = DURABLE_CV_CEILING
    today: str = field(default_factory=lambda: _dt.date.today().isoformat())


# --------------- Header ------------------------------------------


def _baseline_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Defensive read of profile['baseline_summary'] with sane defaults."""
    bs = profile.get("baseline_summary") or {}
    return {
        "n_files": int(bs.get("n_files") or 0),
        "total_words": int(bs.get("total_words") or 0),
        "mean_words": float(bs.get("mean_words") or 0.0),
        "min_words": int(bs.get("min_words") or 0),
        "max_words": int(bs.get("max_words") or 0),
    }


def _date_range_from_drift(drift: dict[str, Any] | None) -> str:
    """Extract a ``"YYYY-MM through YYYY-MM"`` summary from drift JSON.

    Drift output exposes the period labels used for grouping (year,
    quarter, month). We pull the first and last labels and format
    them as a human-readable range. Falls back to the raw labels if
    they don't parse as dates.
    """
    if not drift:
        return ""
    periods = drift.get("periods") or []
    if not periods:
        return ""
    labels = sorted(p.get("label", "") for p in periods if p.get("label"))
    if not labels:
        return ""
    return f"{labels[0]} through {labels[-1]}"


def render_header(inputs: ReportInputs) -> list[str]:
    """Produce the report's first-page header.

    Template shape:

        # {Author display name}: Voice profile insights

        A reading of the SETEC voiceprint output for {corpus name}
        ({date range}, {N posts}, {N words} words). Author-facing —
        meant to surface things that might be interesting...

        The framework that produced these numbers measures voice
        as patterns at the level of...

        {AI-disclosure block, optional}
    """
    bs = _baseline_summary(inputs.voice_profile)
    date_range = _date_range_from_drift(inputs.voice_drift)
    range_clause = f", {date_range}" if date_range else ""

    lines: list[str] = [
        f"# {inputs.author_name}: Voice profile insights",
        "",
        (
            f"A reading of the SETEC voiceprint output for {inputs.corpus_label}"
            f" ({bs['n_files']:,} files{range_clause}, "
            f"{bs['total_words']:,} words). Author-facing — meant to surface "
            "things that might be interesting to the writer about their own voice."
        ),
        "",
        (
            "The framework that produced these numbers measures voice as patterns "
            "at the level of function words, character n-grams, punctuation cadence, "
            "paragraph structure, and pronoun/modal/negation profile. None of it "
            "asks whether the prose is good or bad; it asks what's distinctive, "
            "what's stable across time, and what has shifted."
        ),
        "",
    ]
    if inputs.ai_disclosure:
        lines.extend([
            "> The writer has affirmatively disclosed: "
            f"\"{inputs.ai_disclosure}\". "
            "The framework treats this disclosure as ground truth for the "
            "analysis below.",
            "",
        ])
    return lines


# --------------- Durable voiceprint ------------------------------


def _stable_features(profile: dict[str, Any], cv_ceiling: float) -> dict[str, list[dict[str, Any]]]:
    """Return ``{family: [feature dict]}`` filtered to CV ≤ cv_ceiling.

    voice_profile.py emits ``families[<name>].most_stable_features`` as
    a list of {name, mean, sd, cv} dicts already sorted by ascending
    CV. We keep features whose CV is finite and ≤ ceiling; discard
    rows whose mean is zero (a feature that's always zero isn't
    stable, it's missing).
    """
    out: dict[str, list[dict[str, Any]]] = {}
    families = profile.get("families") or {}
    for fname, fdata in families.items():
        rows: list[dict[str, Any]] = []
        for row in (fdata or {}).get("most_stable_features") or []:
            cv = row.get("cv")
            mean = row.get("mean")
            if cv is None or mean is None:
                continue
            try:
                cv_f = float(cv)
                mean_f = float(mean)
            except (TypeError, ValueError):
                continue
            if not (cv_f == cv_f):  # NaN
                continue
            if cv_f > cv_ceiling:
                continue
            if mean_f == 0:
                continue
            rows.append(row)
        if rows:
            out[fname] = rows[:MAX_DURABLE_FEATURES_PER_FAMILY]
    return out


def _format_value(v: Any) -> str:
    """Format a feature value for table display: 4 decimals for
    sub-1 floats, 2 for larger, integer for integers."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f != f:  # NaN
        return "n/a"
    if abs(f) >= 100:
        return f"{f:,.2f}"
    if abs(f) >= 1:
        return f"{f:.3f}"
    return f"{f:.4f}"


def render_durable_voiceprint(inputs: ReportInputs) -> list[str]:
    """The "What the profile pins down as durable" section.

    Auto: per-family table of CV-stable features.
    Manual: prose paragraph identifying the load-bearing identity
            signals. Marked as TODO with hints listing the
            standout features by name.
    """
    stable_by_family = _stable_features(inputs.voice_profile, inputs.cv_ceiling)
    lines: list[str] = [
        "## What the profile pins down as durable",
        "",
        (
            f"Some features are extremely stable across the entire span. These are "
            f"the markers that make any given piece unmistakably the writer's — the "
            f"prose breathing pattern that doesn't shift even as topics, paragraph "
            f"length, and rhetorical mode evolve. Below: features with cross-"
            f"document coefficient of variation at or below {inputs.cv_ceiling:.2f}, "
            f"grouped by feature family."
        ),
        "",
    ]
    if not stable_by_family:
        lines.extend([
            todo(
                "no features met the CV ceiling; check whether the corpus is too "
                "thin (n_files < 10 will fail this gate). Either widen --cv-ceiling "
                "or note explicitly that the corpus does not have a stable surface."
            ),
            "",
        ])
        return lines

    for family, rows in sorted(stable_by_family.items()):
        lines.append(f"### {family}")
        lines.append("")
        lines.append("| feature | mean | CV |")
        lines.append("|---|---:|---:|")
        for row in rows:
            name = row.get("name", "?")
            mean = _format_value(row.get("mean"))
            cv = _format_value(row.get("cv"))
            lines.append(f"| `{name}` | {mean} | {cv} |")
        lines.append("")

    # Manual paragraph: 3-6 prose blocks identifying load-bearing
    # signals. Hint lists the top features so the editor knows what
    # to discuss without re-reading the JSON.
    top_features = []
    for family, rows in sorted(stable_by_family.items()):
        for row in rows[:3]:  # top 3 per family
            name = row.get("name", "?")
            cv = _format_value(row.get("cv"))
            top_features.append(f"{family}.{name} (CV {cv})")
    hint = (
        "write 3-6 paragraphs naming the most load-bearing identity signals "
        "and explaining what level / pattern each corresponds to. "
        "Standout features above: "
        + "; ".join(top_features[:8])
    )
    lines.extend([
        todo(hint),
        "",
    ])
    return lines


# --------------- Idiolectic vocabulary ---------------------------


_FUNCTION_WORD_PREFIXES = {
    "the ", "a ", "an ", "of ", "to ", "in ", "for ", "is ", "on ",
    "that ", "with ", "by ", "and ", "but ", "or ", "as ", "at ",
}


def _is_likely_function_word_phrase(phrase: str) -> bool:
    """Heuristic to drop function-word-only phrases from topic tables.

    The idiolect detector already filters function words by default,
    but an LLM-edited fixture or a user-supplied JSON may contain
    rows where the lead word is a function word. We don't make the
    decision; we surface as a hint.
    """
    if not phrase:
        return True
    phrase_l = phrase.strip().lower()
    return any(phrase_l.startswith(p) for p in _FUNCTION_WORD_PREFIXES)


def _collect_idiolect_rows(
    *jsons: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Aggregate idiolectic rows across n=1, n=2, n=3 inputs.

    Each idiolect_detector.py JSON exposes
    ``rankings[N]['idiolectic']`` as a list of row dicts. We merge,
    keep the row's ``n`` value, and sort by descending score.
    """
    rows: list[dict[str, Any]] = []
    for j in jsons:
        if not j:
            continue
        rankings = j.get("rankings") or {}
        for n_key, sides in rankings.items():
            try:
                n = int(n_key)
            except (TypeError, ValueError):
                continue
            for row in (sides or {}).get("idiolectic") or []:
                merged = dict(row)
                merged["n"] = n
                rows.append(merged)
    rows.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    return rows


def _split_topic_vs_rhetorical(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows into topic-domain and rhetorical-move buckets.

    Heuristic:
      * Bigrams/trigrams of MOSTLY function words (e.g. "I think",
        "in other words") are rhetorical-move signatures.
      * Unigrams and content-word phrases are topic-domain.

    The split is approximate and the report's automation note flags
    interpretation as manual; we provide the heuristic split as a
    starting point so the table layout matches the template.
    """
    topic: list[dict[str, Any]] = []
    rhetorical: list[dict[str, Any]] = []
    for row in rows:
        n = int(row.get("n") or 1)
        phrase = row.get("phrase") or row.get("display") or ""
        # Single content word → topic. Multi-word with function-word
        # leading or all stopwords → rhetorical. Heuristic but visible
        # in the report so editor can rebalance.
        if n == 1:
            topic.append(row)
        else:
            words = phrase.lower().split()
            stopwords = {
                "i", "you", "we", "the", "a", "an", "of", "to", "in",
                "for", "is", "on", "that", "with", "by", "and", "but",
                "or", "as", "at", "have", "had", "has", "be", "been",
                "was", "were", "are", "this", "these", "those", "it",
                "not", "no", "do", "does", "did", "so", "then", "than",
                "if", "when", "while", "out", "up", "down", "from",
                "over", "under", "back", "still", "yet", "even",
                "other", "words", "fact", "any", "case", "say", "think",
                "of", "course", "might", "would", "should", "could",
            }
            stop_count = sum(1 for w in words if w in stopwords)
            stop_ratio = stop_count / max(len(words), 1)
            # Two signals push into rhetorical: high stopword ratio
            # OR a phrase that begins with a function word (paraphrase
            # markers like "in other words", "I think", "the question
            # of" all start with function words even when the
            # informative tail is content). Either signal is enough.
            leads_with_function = bool(words) and words[0] in stopwords
            if stop_ratio >= 0.5 or leads_with_function:
                rhetorical.append(row)
            else:
                topic.append(row)
    return topic, rhetorical


def _idiolect_table(rows: list[dict[str, Any]], top_n: int) -> list[str]:
    """Render an idiolect table with phrase / per-1000 / score."""
    out = [
        "| Phrase | Per 1000 words | Score |",
        "|---|---:|---:|",
    ]
    for row in rows[:top_n]:
        phrase = row.get("display") or row.get("phrase") or "?"
        per_1k = _format_value(row.get("target_per_1000"))
        score = _format_value(row.get("score"))
        out.append(f"| `{phrase}` | {per_1k} | {score} |")
    return out


def render_idiolectic_vocabulary(inputs: ReportInputs) -> list[str]:
    """The idiolectic-vocabulary section.

    Tables: topic-domain phrases + rhetorical-move signatures.
    Prose: TODO marker with hints naming the top phrases.
    """
    rows = _collect_idiolect_rows(
        inputs.idiolect_n1, inputs.idiolect_n2, inputs.idiolect_n3,
    )

    lines: list[str] = [
        "## Idiolectic vocabulary",
        "",
        (
            "Beyond the structural features, the framework can ask which specific "
            "words and phrases the writer uses at unusual densities relative to "
            "general English (NLTK Brown reference corpus). This surfaces three "
            "things at once: topic-domain terminology, technical vocabulary the "
            "field uses, and the specific phrasings that recur often enough to "
            "function as authorial signature."
        ),
        "",
    ]

    if not rows:
        lines.extend([
            todo(
                "no idiolect rows supplied (pass --idiolect-n1, --idiolect-n2, "
                "--idiolect-n3 to populate this section). Or note explicitly that "
                "the corpus did not yield usable idiolect signal."
            ),
            "",
        ])
        return lines

    topic, rhetorical = _split_topic_vs_rhetorical(rows)

    lines.extend([
        "### Topic-domain phrases",
        "",
        (
            "The phrases below appear at unusual densities in the writer's corpus "
            "and almost never appear at comparable rates in the Brown reference. "
            "Some are conceptual frames; some are field-specific terminology; some "
            "are coined or refined enough that they read as authorial signature."
        ),
        "",
    ])
    if topic:
        lines.extend(_idiolect_table(topic, MAX_IDIOLECT_TOPIC_ROWS))
        lines.append("")
        top_phrases = [
            (r.get("display") or r.get("phrase") or "?")
            for r in topic[:6]
        ]
        lines.extend([
            todo(
                "distinguish topic-domain (inherited) vocabulary from "
                "coined/refined phrases. Top phrases: "
                + ", ".join(f"`{p}`" for p in top_phrases)
            ),
            "",
        ])
    else:
        lines.extend([
            todo("no topic-domain phrases met the threshold."),
            "",
        ])

    lines.extend([
        "### Rhetorical-move signatures",
        "",
        (
            "These are bigrams and trigrams that aren't topic vocabulary but "
            "rhetorical moves — the way the writer constructs claims, hedges them, "
            "transitions, paraphrases."
        ),
        "",
    ])
    if rhetorical:
        lines.extend(_idiolect_table(rhetorical, MAX_IDIOLECT_RHETORICAL_ROWS))
        lines.append("")
        top_phrases = [
            (r.get("display") or r.get("phrase") or "?")
            for r in rhetorical[:5]
        ]
        lines.extend([
            todo(
                "which moves stand out, what they signal about "
                "rhetorical habits (hedging, paraphrase markers, framing moves, "
                "quantifier register, blog-format-specific tics). Top phrases: "
                + ", ".join(f"`{p}`" for p in top_phrases)
            ),
            "",
        ])
    else:
        lines.extend([
            todo(
                "no rhetorical-move phrases at threshold. May indicate the corpus "
                "is short, or the rhetorical surface is uniform. Note explicitly."
            ),
            "",
        ])

    lines.extend([
        "### Where idiolect signals topic vs. signals voice",
        "",
        todo(
            "name one topic-domain phrase from above and one voice-marker phrase. "
            "Explain why the first cluster places the writer in their disciplinary "
            "tradition while the second cluster makes any individual piece read as "
            "theirs. Note that the second cluster (voice rather than topic) is "
            "what SETEC calls 'preservation candidates' — phrases the writer's "
            "natural register uses that an editor (human or AI) might smooth out "
            "without realizing they were carrying voice."
        ),
        "",
    ])
    return lines


# --------------- Drift ------------------------------------------


def render_drift(inputs: ReportInputs) -> list[str]:
    """The "Era / drift" section.

    Auto: cross-period distance table (Burrows-Delta + cosine).
    Manual: per-cluster drift paragraphs (TODO with feature lists).
    Auto: stable-through-drift summary (CV<ceiling features).
    """
    drift = inputs.voice_drift
    if not drift:
        return []

    periods = drift.get("periods") or []
    weighted = drift.get("cross_period_distances_weighted") or []
    drift_scores = drift.get("drift_scores") or {}

    period_count = len(periods)

    lines: list[str] = [
        "## Era / drift",
        "",
        (
            f"The drift report disaggregates the corpus into "
            f"{period_count} period(s) at the configured granularity and computes "
            f"voice distance between them."
        ),
        "",
        "### Cross-period magnitudes",
        "",
        "| Comparison | Burrows-Delta | Cosine |",
        "|---|---:|---:|",
    ]
    if weighted:
        for row in weighted:
            pa = row.get("period_a", "?")
            pb = row.get("period_b", "?")
            bd = _format_value(row.get("burrows_delta"))
            cos = _format_value(row.get("cosine_distance"))
            lines.append(f"| {pa} → {pb} | {bd} | {cos} |")
    else:
        lines.append("| _no cross-period pairs_ | — | — |")
    lines.append("")

    # Drifting features per family.
    lines.extend([
        "### What's drifting",
        "",
    ])
    drifting_features: list[str] = []
    if isinstance(drift_scores, dict):
        for family, payload in sorted(drift_scores.items()):
            drifting = (payload or {}).get("drifting") or []
            if not drifting:
                continue
            top = drifting[:MAX_DRIFT_FEATURES_PER_FAMILY]
            family_summary = ", ".join(
                f"{r.get('name', '?')} (CV {_format_value(r.get('cv'))})"
                for r in top
            )
            drifting_features.append(f"{family}: {family_summary}")
            lines.append(f"- **{family}:** {family_summary}")
    if not drifting_features:
        lines.append("_(No features met the drifting threshold.)_")
    lines.append("")
    lines.extend([
        todo(
            "for each cluster of meaningfully drifting features, write one "
            "paragraph naming the direction and magnitude of the shift and what "
            "kind of register / workflow / topic change it is consistent with. "
            "Drifting features: "
            + ("; ".join(drifting_features) if drifting_features else "(none)")
        ),
        "",
    ])

    # Stable through drift.
    lines.extend([
        "### What's stable through the drift",
        "",
    ])
    stable_features: list[str] = []
    if isinstance(drift_scores, dict):
        for family, payload in sorted(drift_scores.items()):
            stable = (payload or {}).get("stable") or []
            top = stable[:MAX_DRIFT_FEATURES_PER_FAMILY]
            for r in top:
                cv = r.get("cv")
                if cv is None:
                    continue
                try:
                    if float(cv) > inputs.cv_ceiling:
                        continue
                except (TypeError, ValueError):
                    continue
                stable_features.append(
                    f"{family}.{r.get('name', '?')} "
                    f"(mean {_format_value(r.get('mean_across_periods'))}, "
                    f"CV {_format_value(cv)})"
                )
    if stable_features:
        for entry in stable_features:
            lines.append(f"- {entry}")
        lines.append("")
        lines.extend([
            todo(
                "brief interpretation: the deep idiolect didn't shift even as "
                "surface texture moved. The features above are the writer's "
                "durable voice carrying through the period boundary."
            ),
            "",
        ])
    else:
        lines.extend([
            todo(
                "no through-drift stable features met the CV ceiling. May "
                "indicate the corpus is genuinely thin, or the drift swept "
                "the writer's surface broadly. Note which interpretation."
            ),
            "",
        ])
    return lines


# --------------- Comparison to control --------------------------


def render_comparison(inputs: ReportInputs) -> list[str]:
    """The "Comparison to {control writer}" section.

    Auto: headline cross-period magnitude vs control magnitude.
    Manual: per-signature interpretation paragraphs (TODOs).

    Spec calibration finding: drift magnitude alone is not
    diagnostic; drift shape is. The headline reflects this.
    """
    drift = inputs.voice_drift
    control = inputs.comparison_drift
    if not drift or not control:
        return []

    def _max_bd(d: dict[str, Any]) -> float | None:
        rows = d.get("cross_period_distances_weighted") or []
        bds = [
            float(r.get("burrows_delta") or 0)
            for r in rows
            if r.get("burrows_delta") is not None
        ]
        return max(bds) if bds else None

    subj_bd = _max_bd(drift)
    ctrl_bd = _max_bd(control)
    subj_str = _format_value(subj_bd) if subj_bd is not None else "n/a"
    ctrl_str = _format_value(ctrl_bd) if ctrl_bd is not None else "n/a"

    lines: list[str] = [
        f"## Comparison to {inputs.control_writer_name}",
        "",
        (
            f"Subject's max-pair drift magnitude (Burrows-Delta {subj_str}) versus "
            f"{inputs.control_writer_name}'s max-pair magnitude (Burrows-Delta "
            f"{ctrl_str}). "
        ),
        "",
        todo(
            "headline finding: state whether the magnitudes are comparable, "
            "smaller, or larger; then explain why magnitude alone is not "
            "diagnostic — it's the drift SHAPE (which features moved which "
            "way) that distinguishes calibration anchor from impostor."
        ),
        "",
        "### Diagnostic signatures",
        "",
        todo(
            "compare 2-3 specific feature signatures between subject and "
            "control. For each, write: (a) what the subject's data shows; "
            "(b) what the control's data shows, ideally moving differently; "
            "(c) one paragraph interpreting whether the divergence does or "
            "doesn't suggest workflow differences. Pull candidate signatures "
            "from the drift_scores top-drifting features in each input."
        ),
        "",
    ]
    return lines


# --------------- Three observations ------------------------------


def render_three_observations(inputs: ReportInputs) -> list[str]:
    """All-manual section. Three concrete observations the editor
    picks from the data."""
    lines: list[str] = [
        "## Three observations to flag",
        "",
        (
            "Three findings worth the writer's attention specifically. "
            "Each observation should be concrete: name the feature, its value, "
            "what it signals."
        ),
        "",
        "The first observation: " + todo(
            "pick one finding from the durable voiceprint or idiolect tables. "
            "Concrete: name the feature/phrase, value, signal."
        ),
        "",
        "The second observation: " + todo(
            "pick a second finding, ideally from a different section "
            "(if first was structural, this can be lexical, or vice versa)."
        ),
        "",
        "The third observation: " + todo(
            "pick a third finding. If a comparison-to-control is in this "
            "report, this slot is a natural fit for a divergence signature."
        ),
        "",
    ]
    return lines


# --------------- What this cannot say ----------------------------


def render_what_cannot_say(inputs: ReportInputs) -> list[str]:
    """Boilerplate section. Use the template's text directly with
    register / disclosure substitutions."""
    register = inputs.register or "this register"
    lines: list[str] = [
        "## What this analysis cannot say",
        "",
        (
            "The voiceprint measures presence and pattern of features. It doesn't "
            "say whether the writing is good. It doesn't say whether drift is "
            "improvement or decline. It doesn't say whether any individual piece "
            "is \"really\" the writer's voice or \"really\" something else. Most "
            "of these features are robust to topic but vulnerable to register; a "
            "deliberate stylistic experiment will show up as drift even if the "
            "writer is fully in command of the experiment."
        ),
        "",
    ]
    cannot_say = (
        "It also can't tell you anything about provenance — whether any piece "
        "is AI-assisted or fully hand-written."
    )
    if inputs.ai_disclosure:
        cannot_say += (
            f" The author's affirmative disclosure is: \"{inputs.ai_disclosure}\". "
            "Any apparent AI signature in the data should be read against that "
            "disclosure rather than as independent evidence."
        )
    lines.extend([cannot_say, ""])
    lines.extend([
        (
            "The framework's deepest principle: the descriptive measurements are "
            "the framework's job; the interpretive reading is the writer's call. "
            "What follows is one informed reading of the numbers, not a verdict "
            "the math entitles."
        ),
        "",
    ])
    return lines


# --------------- What's distinctive ------------------------------


def render_whats_distinctive(inputs: ReportInputs) -> list[str]:
    """All-manual section. Three things distinctive about the corpus
    relative to typical {register} corpora."""
    register = inputs.register or "this register"
    lines: list[str] = [
        "## What's distinctive about this corpus",
        "",
        f"Three things stand out compared to typical {register} corpora:",
        "",
        "The first thing: " + todo(
            "name a distinctive feature with its value and compare to a typical "
            f"{register} reference value. Standard reference points live in "
            "references/distributional-diagnostics.md."
        ),
        "",
        "The second thing: " + todo("name a second distinctive feature."),
        "",
        "The third thing: " + todo("name a third distinctive feature."),
        "",
    ]
    return lines


# --------------- Footer ------------------------------------------


def render_footer(inputs: ReportInputs) -> list[str]:
    return [
        "---",
        "",
        (
            "*Generated by the SETEC stylometric framework "
            "(https://github.com/anotherpanacea-eng/setec-voiceprint). "
            "The numbers are descriptive measurements; the readings are one "
            "person's interpretation. Voice is more than what stylometry can "
            "measure; what it can measure, it measures honestly.*"
        ),
        "",
        f"<!-- generate_voice_report v{SCRIPT_VERSION} ({inputs.today}) -->",
    ]


# --------------- Top-level renderer ------------------------------


def render_report(inputs: ReportInputs) -> str:
    """Compose all sections in template order. Optional sections
    (drift, comparison) are skipped cleanly when their inputs are
    absent."""
    sections: list[list[str]] = [
        render_header(inputs),
        render_durable_voiceprint(inputs),
        render_idiolectic_vocabulary(inputs),
        render_drift(inputs),
        render_comparison(inputs),
        render_three_observations(inputs),
        render_what_cannot_say(inputs),
        render_whats_distinctive(inputs),
        render_footer(inputs),
    ]
    body = "\n".join("\n".join(s) for s in sections if s)
    # Collapse runs of >2 blank lines that section concatenation
    # can introduce when an optional section is absent.
    body = re.sub(r"\n{3,}", "\n\n", body)
    if not body.endswith("\n"):
        body += "\n"
    return body


# --------------- I/O helpers -------------------------------------


def _load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.exists():
        sys.stderr.write(f"  warning: {p} does not exist; skipping\n")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"  warning: {p} is not valid JSON: {exc}\n")
        return None


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Populate a voice insights report from JSON outputs of the "
            "voice-coherence scripts. Numerical sections are filled "
            "programmatically; interpretive sections are emitted as "
            "{TODO: interpret} markers for an LLM/human pass. See "
            "references/templates/voice_insights_report.template.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--voice-profile", required=True,
                   help="Path to voice_profile.py --json output.")
    p.add_argument("--voice-drift",
                   help=(
                       "Path to voice_drift_tracker.py --json-out output. "
                       "Optional; when absent, profile-only report shape."
                   ))
    p.add_argument("--idiolect-n1",
                   help="Path to idiolect_detector.py --json (n=1).")
    p.add_argument("--idiolect-n2",
                   help="Path to idiolect_detector.py --json (n=2).")
    p.add_argument("--idiolect-n3",
                   help="Path to idiolect_detector.py --json (n=3).")
    p.add_argument("--comparison-drift",
                   help=(
                       "Path to a confirmed-human matched-window control's "
                       "voice_drift JSON. When present, adds the "
                       "Comparison-to-control section."
                   ))
    p.add_argument("--author-name", required=True,
                   help="Display name of the author (e.g. \"Jane Author\").")
    p.add_argument("--corpus-label", required=True,
                   help=(
                       "Human-readable corpus label used in the header "
                       "(e.g. \"Jane Author blog\")."
                   ))
    p.add_argument("--register", default="blog_essay",
                   help="Manifest register; used in 'what's distinctive' framing.")
    p.add_argument("--ai-disclosure",
                   help=(
                       "Optional. The author's affirmative AI-status "
                       "disclosure. Surfaces in the AI-status block under "
                       "the header and the 'cannot say' section."
                   ))
    p.add_argument("--control-writer-name", default="the control writer",
                   help=(
                       "Display name of the comparison-control writer. Only "
                       "used when --comparison-drift is supplied."
                   ))
    p.add_argument("--cv-ceiling", type=float, default=DURABLE_CV_CEILING,
                   help=(
                       "CV threshold for 'durable' features (default 0.10). "
                       "Lower = stricter."
                   ))
    p.add_argument("--out",
                   help="Write report here. Default: stdout.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing outside ai-prose-baselines-private/. "
                       "The report contains voiceprint signatures; only set "
                       "when the corpus is intentionally non-personal."
                   ))
    return p


def run(args: argparse.Namespace) -> int:
    """Top-level driver. Returns shell-style exit code."""
    profile = _load_json(args.voice_profile)
    if profile is None:
        sys.stderr.write(
            "--voice-profile is required and must point to a valid JSON file.\n"
        )
        return 2

    inputs = ReportInputs(
        voice_profile=profile,
        voice_drift=_load_json(args.voice_drift),
        idiolect_n1=_load_json(args.idiolect_n1),
        idiolect_n2=_load_json(args.idiolect_n2),
        idiolect_n3=_load_json(args.idiolect_n3),
        comparison_drift=_load_json(args.comparison_drift),
        author_name=args.author_name,
        corpus_label=args.corpus_label,
        register=args.register,
        ai_disclosure=args.ai_disclosure,
        control_writer_name=args.control_writer_name,
        cv_ceiling=args.cv_ceiling,
    )

    report = render_report(inputs)

    if args.out:
        out_path = Path(args.out).expanduser()
        ac.check_output_privacy(
            [out_path], allow_public=args.allow_public_output, tool=TOOL_NAME,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        sys.stderr.write(f"Report written to: {out_path}\n")
    else:
        # Stdout output is allowed without --allow-public-output for
        # interactive use; the user is the audience and can see the
        # implicit privacy posture in the framing of the data.
        sys.stdout.write(report)

    # Count TODOs so the user knows how many editorial calls remain.
    todo_count = report.count("{TODO: interpret:")
    sys.stderr.write(
        f"Report has {todo_count} {{TODO: interpret}} marker(s) for the "
        "LLM/human pass.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
