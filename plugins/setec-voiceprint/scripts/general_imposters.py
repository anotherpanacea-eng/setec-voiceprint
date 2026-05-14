#!/usr/bin/env python3
"""general_imposters.py — General Imposters attribution harness.

Cathedral upgrade #4 finisher. The impostor corpus shipped across
1.14.3 → 1.19.0 is the prerequisite this script consumes: given a
target text and a candidate writer's identity-baseline corpus, the
General Imposters method (Koppel et al. 2014, Kestemont et al. 2016
as implemented in R `stylo::imposters()`) turns SETEC's distance
machinery into a calibrated attribution claim by asking, under
bootstrap resampling: how often does the target fall closer to
verified-CANDIDATE than to N plausible-other writers in matched
register?

The score the harness emits is not a probability in the strict
Bayesian sense; it's a frequentist proportion of bootstrap iterations
in which the target wins the closeness contest against the impostor
pool. The proportion has the property that 1.0 = "always closer to
candidate than to any impostor" and 0.0 = "always closer to some
impostor than to candidate." Following Kestemont et al. 2016, scores
near 0.0 or 1.0 are trustworthy; the gray zone in the middle
(typically 0.2-0.8) means the evidence is mixed and the harness
explicitly refuses an attribution claim.

What this surface licenses, and what it does not:

  * **Licenses:** "On N bootstrap iterations comparing the target
    to the candidate's identity baseline + M impostor writers in
    matched register, the target was closer to the candidate's
    baseline P proportion of the time."
  * **Does NOT license:** "The target is by the candidate." The
    harness measures consistency with a writer-as-stylometric-
    fingerprint, not authorship in the legal or philosophical sense.
    A score near 1.0 says "stylometrically consistent with"; a score
    near 0.0 says "stylometrically inconsistent with"; the gray zone
    says "the framework refuses to call this."

Privacy: the harness reads voiceprint-shaped data (function-word
distributions, char-n-grams, etc.). Default output goes under
``ai-prose-baselines-private/``; the marker-based privacy guard
refuses non-private outputs unless ``--allow-public-output`` is set.
Public-report harnesses that emit GI scores must anonymize impostor
identities by default and refuse to name ``consent_status:
undocumented`` writers per the manifest schema.

Usage:

    python3 scripts/general_imposters.py \\
        --target path/to/draft.txt \\
        --manifest path/to/corpus_manifest.jsonl \\
        --candidate-persona blog \\
        --register blog_essay \\
        --iterations 100 \\
        --feature-fraction 0.5 \\
        --out path/to/gi_report.md \\
        --json-out path/to/gi_report.json

The harness emits a markdown report with the proportion of wins,
bootstrap CI on that proportion, the impostor count and identities
(by slug; raw text never quoted), and an explicit claim-license
block per the framework's surface conventions.

References:
  - Koppel et al. 2014, "Determining if two documents are written
    by the same author"
  - Kestemont et al. 2016, "Authenticating the Writings of Julius
    Caesar"
  - Stamatatos 2009, "A survey of modern authorship attribution
    methods"
  - R `stylo::imposters()` — the canonical reference implementation
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402
from claim_license import (  # noqa: E402
    ClaimLicense,
    from_legacy,
    with_state_caveats,
)

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "general_imposters"
SCRIPT_VERSION = "1.0"


# ---- Default GI parameters --------------------------------------


# Number of bootstrap iterations. 100 is the spec's anchor; 50 is
# the literature's lower bound for stable proportions; > 1000 hits
# diminishing returns and dominates wall-clock.
DEFAULT_ITERATIONS = 100

# Per-iteration random fraction of the feature vocabulary. Koppel
# et al. 2014 use 0.5; literature range is 0.4-0.6. Lower fractions
# make iterations more independent (better proportions); higher
# fractions are more numerically stable per iteration.
DEFAULT_FEATURE_FRACTION = 0.5

# Top-N most-frequent words to use as the feature vocabulary
# (function-word + content blend; the GI literature uses 100-500).
# 200 is a stable middle ground.
DEFAULT_TOP_N_FEATURES = 200

# Decision regions for the harness's claim language. Below LOW or
# above HIGH is a strong claim ("inconsistent" / "consistent");
# between them is the gray zone and the harness refuses attribution.
GRAY_ZONE_LOW = 0.20
GRAY_ZONE_HIGH = 0.80

# Minimum number of distinct impostor *writers* (personas) the
# harness needs to run. The methodology and claim language describe
# "M impostor writers"; gating on the count of distinct personas
# rather than docs prevents 5 docs from a single persona — which
# would not satisfy the literature's "M impostors" framing — from
# clearing the floor. Document count is reported separately as an
# adequacy diagnostic (the practical target is 10–20 docs across
# 3–5 personas per the corpus-assembly walkthrough).
MIN_IMPOSTORS = 5


# ---- Manifest loading -------------------------------------------


@dataclass
class CorpusEntry:
    """One manifest entry consumed by the harness, post-load.

    ``resolved_path`` is the resolved absolute filesystem location
    of this entry's text content. Stored so the runner can filter
    out an entry whose path collides with ``--target`` — the GI
    proportion is meaningless if the target is also in the
    candidate / impostor pool (same self-normalization failure mode
    voice_distance.py already guards against).
    """
    id: str
    text: str
    persona: str
    register: str
    author: str
    corpus_role: str
    impostor_for: list[str]
    word_count: int
    consent_status: str = "undocumented"
    resolved_path: Path | None = None


def _load_manifest(path: Path) -> list[CorpusEntry]:
    """Read JSONL manifest and resolve text content per entry."""
    entries: list[CorpusEntry] = []
    base = path.parent
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  manifest line {line_no}: {e}; skipping\n")
            continue
        text_path_str = row.get("path") or ""
        text_path = Path(text_path_str)
        if not text_path.is_absolute():
            text_path = (base / text_path).resolve()
        if not text_path.is_file():
            sys.stderr.write(
                f"  manifest line {line_no}: {text_path} not found; skipping\n"
            )
            continue
        try:
            text = text_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            sys.stderr.write(f"  read failed for {text_path}: {e}\n")
            continue
        entries.append(CorpusEntry(
            id=str(row.get("id") or f"line_{line_no}"),
            text=text,
            persona=str(row.get("persona") or ""),
            register=str(row.get("register") or ""),
            author=str(row.get("author") or ""),
            corpus_role=str(row.get("corpus_role") or "identity_baseline"),
            impostor_for=list(row.get("impostor_for") or []),
            word_count=int(row.get("word_count") or 0),
            consent_status=str(row.get("consent_status") or "undocumented"),
            resolved_path=text_path,
        ))
    return entries


def _exclude_target_path(
    entries: list[CorpusEntry], target_path: Path,
) -> list[CorpusEntry]:
    """Drop manifest entries whose resolved path is the same file as
    ``--target``. The target text is supplied separately to the
    harness; if the same file is also in the candidate or impostor
    pool, the GI proportion self-normalizes (the target gets to
    "win" against itself, biasing the proportion toward 1.0).

    Resolution is by ``Path.resolve()`` so symlinks and ``./`` /
    ``../`` paths collapse to the same canonical form. Entries
    without a ``resolved_path`` (e.g., constructed in-memory by a
    caller that bypasses ``_load_manifest``) pass through
    unchanged.
    """
    try:
        target_resolved = target_path.resolve()
    except OSError:
        return entries
    out: list[CorpusEntry] = []
    dropped: list[str] = []
    for e in entries:
        if e.resolved_path is None:
            out.append(e)
            continue
        try:
            if e.resolved_path.resolve() == target_resolved:
                dropped.append(e.id)
                continue
        except OSError:
            pass
        out.append(e)
    if dropped:
        sys.stderr.write(
            "  excluding "
            f"{len(dropped)} manifest entr"
            f"{'ies' if len(dropped) != 1 else 'y'} "
            f"whose path matches --target: {', '.join(dropped)}\n"
        )
    return out


# ---- Feature extraction (lightweight; not stylometry_core) -----


_TOKEN_RE = None


def _tokens(text: str) -> list[str]:
    """Lowercase whitespace-split tokens; cheap stylometric-feature
    feed. The GI method is robust to feature-extraction details
    (the bootstrap-feature-subset step washes out per-token noise);
    using stylometry_core's full pipeline would require spaCy and
    isn't necessary for the proportion-of-wins metric."""
    import re
    global _TOKEN_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = re.compile(r"\w+", re.UNICODE)
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _feature_vocab(
    entries: Sequence[CorpusEntry], top_n: int,
) -> list[str]:
    """Build the most-frequent-tokens vocabulary across all
    entries. The GI method per Koppel et al. uses a single vocab
    across target + candidate + impostors; the bootstrap step
    randomly subsamples it per iteration."""
    from collections import Counter
    counts: Counter[str] = Counter()
    for e in entries:
        counts.update(_tokens(e.text))
    return [tok for tok, _ in counts.most_common(top_n)]


def _feature_vector(
    text: str, vocab: Sequence[str],
) -> list[float]:
    """Per-document relative-frequency vector over `vocab`. Returns
    a list of len(vocab) floats; tokens not in vocab contribute to
    the denominator only (so vectors sum to ≤ 1)."""
    from collections import Counter
    tok_counts: Counter[str] = Counter(_tokens(text))
    total = sum(tok_counts.values()) or 1
    return [tok_counts.get(t, 0) / total for t in vocab]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine distance: 1 − cos(θ). Returns 1.0 on a zero vector
    (no closer than orthogonal)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - (dot / (na * nb))


# ---- The General Imposters loop ---------------------------------


@dataclass
class GIResult:
    """Aggregated GI run."""
    target_id: str
    candidate_persona: str
    candidate_n_docs: int
    n_impostors: int
    impostor_personas: list[str]
    iterations: int
    feature_fraction: float
    top_n_features: int
    wins: int  # iterations where target was closest to candidate
    losses: int  # iterations where target was closest to an impostor
    proportion: float  # wins / iterations
    proportion_ci_95: tuple[float, float] | None
    refused: bool  # True if MIN_IMPOSTORS gate or other hard gate failed
    refusal_reason: str = ""
    decision: str = ""  # "consistent" / "inconsistent" / "gray_zone"
    # B.3 (v1.58.0+): authorship-state value from the operator's
    # manifest entry for the target. Threaded through to the
    # ClaimLicense block by ``_structured_claim_license`` so per-
    # state caveats can be appended. ``None`` means "not supplied"
    # — the helper's no-op path preserves pre-B.3 behavior.
    target_ai_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "target_id": self.target_id,
            "candidate_persona": self.candidate_persona,
            "candidate_n_docs": self.candidate_n_docs,
            "n_impostors": self.n_impostors,
            "impostor_personas": self.impostor_personas,
            "iterations": self.iterations,
            "feature_fraction": self.feature_fraction,
            "top_n_features": self.top_n_features,
            "wins": self.wins,
            "losses": self.losses,
            "proportion": self.proportion,
            "proportion_ci_95": list(self.proportion_ci_95) if self.proportion_ci_95 else None,
            "refused": self.refused,
            "refusal_reason": self.refusal_reason,
            "decision": self.decision,
            "claim_license": _claim_license(),
        }
        # B.3: surface target ai_status in the JSON payload for
        # downstream state-routed consumers. Only emit when set
        # so legacy callers see the same shape.
        if self.target_ai_status is not None:
            out["ai_status"] = self.target_ai_status
        return out


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "Stylometric consistency between the target and the named "
            "candidate writer's identity baseline, expressed as a "
            "frequentist proportion of bootstrap iterations in which the "
            "target fell closer to the candidate baseline than to any "
            "impostor writer in matched register."
        ),
        "does_not_license": (
            "Authorship attribution in the legal or philosophical sense. "
            "Stylometric consistency is not authorship; the harness's "
            "score is consistent with the writer being someone other "
            "than the candidate (e.g., another writer in matched register "
            "whose voice happens to share enough surface features). "
            "Adversarial paraphrase, humanizer-tool output, and AI-edited "
            "drafts are not adjudicated by this surface — those are the "
            "AI-prose-detection harness's job, run separately."
        ),
        "gray_zone": (
            f"Scores in the [{GRAY_ZONE_LOW}, {GRAY_ZONE_HIGH}] range are "
            "the framework's refusal zone — the evidence is mixed and "
            "the harness explicitly declines to emit an attribution "
            "claim. Per Kestemont et al. 2016, scores near the extremes "
            "(<0.05 or >0.95) are trustworthy; the middle is mixed."
        ),
    }


def _decide(proportion: float) -> str:
    if proportion >= GRAY_ZONE_HIGH:
        return "consistent_with_candidate"
    if proportion <= GRAY_ZONE_LOW:
        return "inconsistent_with_candidate"
    return "gray_zone_refused"


def _proportion_ci_wilson(
    wins: int, n: int, confidence: float = 0.95,
) -> tuple[float, float]:
    """Wilson score CI for the win proportion. Better-behaved than
    normal-approximation CI on small N or extreme proportions."""
    if n == 0:
        return (0.0, 1.0)
    from scipy.stats import norm  # type: ignore
    z = float(norm.ppf(0.5 + confidence / 2.0))
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def run_gi(
    target_text: str,
    target_id: str,
    candidate_docs: list[CorpusEntry],
    impostor_docs: list[CorpusEntry],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    feature_fraction: float = DEFAULT_FEATURE_FRACTION,
    top_n_features: int = DEFAULT_TOP_N_FEATURES,
    seed: int | None = 42,
) -> GIResult:
    """Run the General Imposters bootstrap and return a GIResult.

    Per-iteration loop:
      1. Sub-sample ``feature_fraction`` of the top-N feature
         vocabulary at random.
      2. Project target + candidate docs + impostor docs onto the
         sub-sampled vocab.
      3. Compute mean cosine distance from target to candidate's
         per-document centroids, and from target to each impostor's
         per-document centroids.
      4. The candidate "wins" the iteration iff the candidate's
         min distance is smaller than every impostor's min distance.
      5. Record win / loss.

    The proportion of wins across iterations is the GI score. Wilson
    CI on the proportion is reported.
    """
    candidate_persona = (
        candidate_docs[0].persona if candidate_docs else "unknown"
    )
    impostor_personas = sorted({d.persona for d in impostor_docs})

    if len(impostor_personas) < MIN_IMPOSTORS:
        return GIResult(
            target_id=target_id,
            candidate_persona=candidate_persona,
            candidate_n_docs=len(candidate_docs),
            n_impostors=len(impostor_docs),
            impostor_personas=impostor_personas,
            iterations=0,
            feature_fraction=feature_fraction,
            top_n_features=top_n_features,
            wins=0, losses=0,
            proportion=float("nan"),
            proportion_ci_95=None,
            refused=True,
            refusal_reason=(
                f"Need at least {MIN_IMPOSTORS} distinct impostor "
                f"personas (writers) in matched register; got "
                f"{len(impostor_personas)} persona"
                f"{'s' if len(impostor_personas) != 1 else ''} "
                f"across {len(impostor_docs)} doc"
                f"{'s' if len(impostor_docs) != 1 else ''}. "
                "The General Imposters method's claim language is "
                "'M impostor writers'; satisfying the floor with "
                "many docs from one writer doesn't satisfy that."
            ),
            decision="refused",
        )
    if not candidate_docs:
        return GIResult(
            target_id=target_id,
            candidate_persona=candidate_persona,
            candidate_n_docs=0,
            n_impostors=len(impostor_docs),
            impostor_personas=impostor_personas,
            iterations=0,
            feature_fraction=feature_fraction,
            top_n_features=top_n_features,
            wins=0, losses=0,
            proportion=float("nan"),
            proportion_ci_95=None,
            refused=True,
            refusal_reason="No candidate identity-baseline docs supplied.",
            decision="refused",
        )

    rng = random.Random(seed)
    all_entries = candidate_docs + impostor_docs
    vocab = _feature_vocab(all_entries, top_n_features)
    if not vocab:
        return GIResult(
            target_id=target_id,
            candidate_persona=candidate_persona,
            candidate_n_docs=len(candidate_docs),
            n_impostors=len(impostor_docs),
            impostor_personas=impostor_personas,
            iterations=0,
            feature_fraction=feature_fraction,
            top_n_features=top_n_features,
            wins=0, losses=0,
            proportion=float("nan"),
            proportion_ci_95=None,
            refused=True,
            refusal_reason="Empty feature vocabulary.",
            decision="refused",
        )

    # Pre-compute full-vocab vectors for every doc + the target so
    # per-iteration sub-sampling is just a slice.
    target_vec = _feature_vector(target_text, vocab)
    candidate_vecs = [_feature_vector(d.text, vocab) for d in candidate_docs]
    impostor_vecs_by_persona: dict[str, list[list[float]]] = {}
    for d in impostor_docs:
        impostor_vecs_by_persona.setdefault(d.persona, []).append(
            _feature_vector(d.text, vocab)
        )

    sample_size = max(1, int(round(feature_fraction * len(vocab))))
    wins = 0
    losses = 0
    for _ in range(iterations):
        idxs = sorted(rng.sample(range(len(vocab)), sample_size))
        tv = [target_vec[i] for i in idxs]

        def _slice(vec: Sequence[float]) -> list[float]:
            return [vec[i] for i in idxs]

        cand_min = min(_cosine(tv, _slice(v)) for v in candidate_vecs)
        impostor_mins: list[float] = []
        for _persona, vecs in impostor_vecs_by_persona.items():
            impostor_mins.append(
                min(_cosine(tv, _slice(v)) for v in vecs)
            )
        if cand_min < min(impostor_mins):
            wins += 1
        else:
            losses += 1

    proportion = wins / iterations if iterations else float("nan")
    try:
        ci = _proportion_ci_wilson(wins, iterations)
    except Exception:
        ci = None

    return GIResult(
        target_id=target_id,
        candidate_persona=candidate_persona,
        candidate_n_docs=len(candidate_docs),
        n_impostors=len(impostor_docs),
        impostor_personas=impostor_personas,
        iterations=iterations,
        feature_fraction=feature_fraction,
        top_n_features=top_n_features,
        wins=wins, losses=losses,
        proportion=proportion,
        proportion_ci_95=ci,
        refused=False,
        decision=_decide(proportion),
    )


# ---- Manifest filtering -----------------------------------------


def _select_candidate_docs(
    entries: list[CorpusEntry],
    *,
    candidate_persona: str,
    register: str,
) -> list[CorpusEntry]:
    """Identity-baseline docs for the candidate persona in matched register."""
    return [
        e for e in entries
        if e.corpus_role == "identity_baseline"
        and e.persona == candidate_persona
        and (not register or e.register == register)
    ]


def _select_impostor_docs(
    entries: list[CorpusEntry],
    *,
    candidate_persona: str,
    register: str,
) -> list[CorpusEntry]:
    """Impostor-pool docs whose impostor_for names the candidate
    AND whose register matches."""
    return [
        e for e in entries
        if e.corpus_role == "impostor"
        and candidate_persona in (e.impostor_for or [])
        and (not register or e.register == register)
    ]


# ---- Rendering ---------------------------------------------------


def _structured_claim_license(result: GIResult) -> ClaimLicense:
    """Compose the structured ClaimLicense block.

    Carries the legacy dict's licenses / does_not_license text plus
    the harness's empirical context: candidate / impostor counts,
    iteration count, decision regions, the Wilson CI on the
    proportion. Renders to the same paste-into-report markdown the
    sliding-window heatmap uses, via ``ClaimLicense.render_block()``.
    """
    legacy = _claim_license()
    lic = from_legacy(legacy, task_surface=TASK_SURFACE)
    lic.comparison_set = {
        "candidate_persona": result.candidate_persona,
        "candidate_n_docs": result.candidate_n_docs,
        "n_impostors": result.n_impostors,
        "n_impostor_personas": len(result.impostor_personas),
        "iterations": result.iterations,
        "feature_fraction": result.feature_fraction,
        "top_n_features": result.top_n_features,
    }
    if result.proportion_ci_95 is not None:
        lic.confidence_interval_95 = (
            float(result.proportion_ci_95[0]),
            float(result.proportion_ci_95[1]),
        )
    lic.references = [
        "Koppel et al. 2014 — Determining if two documents are written by the same author",
        "Kestemont et al. 2016 — Authenticating the Writings of Julius Caesar",
        "R `stylo::imposters()` — canonical reference implementation",
    ]
    lic.additional_caveats = [
        f"Decision regions: ≤ {GRAY_ZONE_LOW} → inconsistent; "
        f"≥ {GRAY_ZONE_HIGH} → consistent; "
        f"in [{GRAY_ZONE_LOW}, {GRAY_ZONE_HIGH}] → gray-zone refusal.",
        f"Floor: ≥ {MIN_IMPOSTORS} distinct impostor personas in matched register.",
    ]
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status on the run. No-op when target_ai_status is None.
    lic = with_state_caveats(
        lic, target_ai_status=result.target_ai_status,
    )
    return lic


def render_markdown(result: GIResult) -> str:
    lic = _claim_license()
    structured = _structured_claim_license(result)
    lines: list[str] = [
        "# General Imposters attribution report",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Date:** {_dt.date.today().isoformat()}",
        "",
        "## Inputs",
        "",
        f"- **Target id:** `{result.target_id}`",
        f"- **Candidate persona:** `{result.candidate_persona}` "
        f"({result.candidate_n_docs} identity-baseline doc"
        f"{'s' if result.candidate_n_docs != 1 else ''})",
        f"- **Impostors:** {result.n_impostors} doc"
        f"{'s' if result.n_impostors != 1 else ''} across "
        f"{len(result.impostor_personas)} persona"
        f"{'s' if len(result.impostor_personas) != 1 else ''}: "
        f"{', '.join(f'`{p}`' for p in result.impostor_personas) or '(none)'}",
        f"- **Iterations:** {result.iterations}",
        f"- **Feature fraction per iteration:** "
        f"{result.feature_fraction:.2f} of top-{result.top_n_features} "
        "vocabulary",
        "",
    ]
    if result.refused:
        lines.extend([
            "## Refusal",
            "",
            f"The harness refused to emit an attribution claim. Reason:",
            "",
            f"> {result.refusal_reason}",
            "",
            structured.render_block().rstrip(),
            "",
        ])
        return "\n".join(lines) + "\n"

    decision_label = {
        "consistent_with_candidate": "**Stylometrically consistent** "
                                     "with the candidate.",
        "inconsistent_with_candidate": "**Stylometrically inconsistent** "
                                       "with the candidate.",
        "gray_zone_refused": "**Gray zone — the framework refuses to "
                             "call this.**",
    }.get(result.decision, result.decision)

    ci_str = (
        f"({result.proportion_ci_95[0]:.3f}, "
        f"{result.proportion_ci_95[1]:.3f})"
    ) if result.proportion_ci_95 else "(CI unavailable)"

    lines.extend([
        "## Result",
        "",
        f"- **Wins (iterations target closer to candidate than to "
        f"any impostor):** {result.wins} / {result.iterations}",
        f"- **Proportion:** {result.proportion:.3f}",
        f"- **Wilson 95% CI:** {ci_str}",
        "",
        f"**Decision:** {decision_label}",
        "",
        structured.render_block().rstrip(),
        "",
        "## Methodology",
        "",
        "Per-iteration: a random "
        f"{result.feature_fraction:.0%} subset of the top-"
        f"{result.top_n_features} most-frequent token vocabulary is "
        "drawn. Target, candidate identity-baseline, and impostor docs "
        "are projected onto the subset. Cosine distance from target to "
        "every doc is computed; the candidate persona wins the "
        "iteration iff the candidate's nearest doc is closer than every "
        "impostor's nearest doc. The proportion of wins across "
        f"{result.iterations} iterations is the GI score, with Wilson "
        "score 95% CI as the uncertainty band.",
        "",
        "References: Koppel et al. 2014, Kestemont et al. 2016, "
        "R `stylo::imposters()` as the canonical reference implementation.",
        "",
    ])
    return "\n".join(lines) + "\n"


# ---- CLI ---------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "General Imposters attribution harness. Consumes the "
            "impostor corpus + an identity baseline + a target text, "
            "emits a frequentist proportion of bootstrap wins."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--target", required=True,
                   help="Path to the target text.")
    p.add_argument("--target-id", default=None,
                   help="Optional id for the target (defaults to the filename).")
    p.add_argument("--manifest", required=True,
                   help="Path to corpus_manifest.jsonl.")
    p.add_argument("--candidate-persona", required=True,
                   help=(
                       "Persona slug whose identity-baseline docs we "
                       "compare the target against."
                   ))
    p.add_argument("--register",
                   help=(
                       "Optional register filter (e.g. blog_essay). "
                       "Defaults to the candidate's first observed "
                       "register."
                   ))
    p.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                   help=f"Bootstrap iterations (default {DEFAULT_ITERATIONS}).")
    p.add_argument("--feature-fraction", type=float,
                   default=DEFAULT_FEATURE_FRACTION,
                   help=(
                       "Fraction of feature vocab to sub-sample per "
                       f"iteration (default {DEFAULT_FEATURE_FRACTION})."
                   ))
    p.add_argument("--top-n-features", type=int,
                   default=DEFAULT_TOP_N_FEATURES,
                   help=(
                       "Top-N most-frequent words as the feature "
                       f"vocabulary (default {DEFAULT_TOP_N_FEATURES})."
                   ))
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed (default 42).")
    p.add_argument("--out", help="Markdown report path.")
    p.add_argument("--json-out", help="JSON report path.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing outside ai-prose-baselines-private/. "
                       "GI output is voice-cloning-adjacent; only set "
                       "for non-personal corpora."
                   ))
    # B.3 (v1.58.0+): authorship-state routing for the ClaimLicense
    # block. The operator's manifest entry for the target carries
    # an `ai_status` value (pre_ai_human, ai_generated_from_outline,
    # etc.). Surface it to the audit so the rendered license block
    # carries the matching state-specific caveats. Per SPEC §9.2,
    # this is the operational consequence of the B.2 vocabulary —
    # not threshold-shipping, just per-state licensure language.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the target text (e.g., "
            "pre_ai_human, ai_generated, ai_generated_from_outline, "
            "ai_assisted, ai_edited, mixed, unknown). When supplied, "
            "the ClaimLicense block gains state-specific caveats per "
            "SPEC_authorship_states.md §9.2."
        ),
    )
    return p


def run(args: argparse.Namespace) -> int:
    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"--target not found: {target_path}\n")
        return 2

    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.is_file():
        sys.stderr.write(f"--manifest not found: {manifest_path}\n")
        return 2

    target_text = target_path.read_text(encoding="utf-8", errors="ignore")
    target_id = args.target_id or target_path.stem

    entries = _load_manifest(manifest_path)
    entries = _exclude_target_path(entries, target_path)
    register = args.register or _infer_candidate_register(
        entries, args.candidate_persona,
    )
    candidate_docs = _select_candidate_docs(
        entries, candidate_persona=args.candidate_persona, register=register,
    )
    impostor_docs = _select_impostor_docs(
        entries, candidate_persona=args.candidate_persona, register=register,
    )

    sys.stderr.write(
        f"Manifest: {len(entries)} entries; "
        f"candidate `{args.candidate_persona}` "
        f"register `{register}` matched {len(candidate_docs)} docs; "
        f"impostor pool {len(impostor_docs)} docs across "
        f"{len({d.persona for d in impostor_docs})} personas.\n"
    )

    result = run_gi(
        target_text, target_id, candidate_docs, impostor_docs,
        iterations=args.iterations,
        feature_fraction=args.feature_fraction,
        top_n_features=args.top_n_features,
        seed=args.seed,
    )
    # B.3: surface --ai-status into the GIResult so the rendered
    # claim-license block and JSON payload both pick it up. We use
    # getattr() so callers that build the Namespace manually (older
    # tests, programmatic invocations from before B.3) don't have
    # to know about the new flag.
    ai_status = getattr(args, "ai_status", None)
    if ai_status:
        result.target_ai_status = ai_status

    md = render_markdown(result)
    js = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"

    paths_to_check: list[Path] = []
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    if args.json_out:
        paths_to_check.append(Path(args.json_out).expanduser())
    if paths_to_check:
        ac.check_output_privacy(
            paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
        )

    if args.out:
        out_p = Path(args.out).expanduser()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(md, encoding="utf-8")
        sys.stderr.write(f"Wrote markdown report to {out_p}\n")
    else:
        sys.stdout.write(md)
    if args.json_out:
        out_p = Path(args.json_out).expanduser()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(js, encoding="utf-8")
        sys.stderr.write(f"Wrote JSON report to {out_p}\n")
    return 0


def _infer_candidate_register(
    entries: list[CorpusEntry], candidate_persona: str,
) -> str:
    """If the user didn't pass --register, pick the first register
    we see for the candidate persona's identity_baseline entries."""
    for e in entries:
        if (
            e.corpus_role == "identity_baseline"
            and e.persona == candidate_persona
            and e.register
        ):
            return e.register
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
