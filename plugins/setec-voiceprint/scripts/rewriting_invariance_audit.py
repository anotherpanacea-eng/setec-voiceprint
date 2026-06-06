#!/usr/bin/env python3
"""rewriting_invariance_audit.py — Raidar-style rewriting-invariance evidence.

A *discrimination* signal orthogonal to perplexity: rather than asking how
*likely* a text is under a model, ask how much a model *changes* it when told to
"rewrite to improve clarity." The Raidar observation (Mao et al., ICLR 2024) is
that LLMs edit their own (AI-like) prose less than they edit human prose — the
model already regards machine prose as "good," so a rewrite-to-improve prompt
leaves it largely intact, while human prose gets edited more. Low rewrite
distance ⇒ more AI-like; high ⇒ more human-like. This is **descriptive
evidence, not a verdict**.

Clean-room note
---------------
This implements the *published idea* — prompt an LLM to rewrite, then measure
edit distance / token overlap between original and rewrite, averaged over N
trials — from scratch. **No code, prompts, or data from the Raidar reference
repository were consulted or vendored.** The method described in the paper
(rewrite + edit-distance) is simple and published; the prompt text and the
distance metrics here are our own. See the spec
(`specs/15-raidar-rewriting-invariance.md`) and the PR body for the clean-room
decision.

Pluggable rewrite client (mirrors narrative_judge.py)
-----------------------------------------------------
The rewrite call is **injectable**. ``audit_rewriting_invariance`` takes a
``rewrite_fn`` callable ``(text, *, trial) -> str``; the CLI builds one from
``--judge MODEL`` (an operator-supplied API backend, lazy-imported), but tests
pass a deterministic stub rewriter so **no real LLM call is made in the test
suite**. The judge model id and the exact rewrite prompt are recorded in
provenance (model id + prompt text + SHA-256 prompt fingerprint), exactly as
``narrative_judge`` records its judge identity, because the result is
*meaningless without them*: a different model or a reworded prompt yields a
different distance.

Uncalibrated by design
-----------------------
There is **no shipped threshold and no band**. SETEC does not ship an AI/human
verdict for an uncalibrated signal whose scale depends entirely on the judge
model and prompt. The claim-license licenses the mean rewriting distance under
the named model + prompt and refuses any AI/human classification absent
operator-supplied thresholds.

Usage::

    python3 scripts/rewriting_invariance_audit.py TARGET --judge MODEL
    python3 scripts/rewriting_invariance_audit.py TARGET --judge MODEL --n 5 --json
    python3 scripts/rewriting_invariance_audit.py TARGET --judge MODEL --out report.md
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "rewriting_invariance"
TOOL_NAME = "rewriting_invariance_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 50

# The exact rewrite prompt. Pinned and fingerprinted in provenance: the
# distance is only interpretable relative to this prompt. Operators changing
# it MUST expect the prompt fingerprint (and the numbers) to change.
REWRITE_PROMPT = (
    "Rewrite the following text to improve its clarity. Preserve the meaning "
    "and approximate length. Return only the rewritten text, with no preamble, "
    "commentary, or quotation marks.\n\n{text}"
)

# A callable that takes the original text and the trial index and returns the
# rewritten text. Tests inject a deterministic stub; the CLI builds an
# API-backed one from --judge.
RewriteFn = Callable[..., str]

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class RewriteError(RuntimeError):
    """Raised when a rewrite backend cannot produce a result."""


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def prompt_fingerprint(prompt_text: str = REWRITE_PROMPT) -> str:
    """SHA-256 of the rewrite prompt. Two runs sharing this fingerprint
    showed their judges a byte-identical prompt."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()


def render_prompt(text: str, *, prompt_template: str = REWRITE_PROMPT) -> str:
    """Render the rewrite prompt for a given target text."""
    return prompt_template.format(text=text)


# ----------------- distance metrics (clean-room) ------------------

def edit_distance_ratio(original: str, rewrite: str) -> float:
    """Character-level normalized edit distance in [0, 1].

    0.0 == identical; 1.0 == maximally different. We use difflib's similarity
    ratio (stdlib, deterministic) and report 1 - ratio so that *higher means
    more changed*, matching the Raidar intuition (high distance ⇒ more
    human-like)."""
    if not original and not rewrite:
        return 0.0
    sim = difflib.SequenceMatcher(None, original, rewrite).ratio()
    return round(1.0 - sim, 6)


def token_overlap_distance(original: str, rewrite: str) -> float:
    """Token-overlap distance in [0, 1] (1 - Jaccard over token sets).

    Complements the character edit distance with a vocabulary-level view:
    a rewrite that reshuffles wording but keeps the same tokens scores low
    here; one that swaps vocabulary scores high. 0.0 == identical token sets;
    1.0 == disjoint."""
    a = set(_tokenize(original))
    b = set(_tokenize(rewrite))
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    if union == 0:
        return 0.0
    return round(1.0 - inter / union, 6)


def _trial_distance(original: str, rewrite: str) -> dict[str, Any]:
    return {
        "edit_distance": edit_distance_ratio(original, rewrite),
        "token_overlap_distance": token_overlap_distance(original, rewrite),
        "rewrite_words": count_words(rewrite),
    }


# ----------------- core audit (pure, deterministic given rewrite_fn) ----

def audit_rewriting_invariance(
    text: str,
    rewrite_fn: RewriteFn,
    *,
    n: int = 3,
    judge_model: str,
    prompt_template: str = REWRITE_PROMPT,
) -> dict[str, Any]:
    """Run N rewrite trials via ``rewrite_fn`` and aggregate the distances.

    ``rewrite_fn`` is called as ``rewrite_fn(text, trial=i)`` for i in
    range(n). It is the sole point of LLM contact and is injected so tests can
    pass a deterministic stub. The headline metric is the **mean edit
    distance** over trials; per-trial distances and a mean token-overlap
    distance are also reported. Provenance pins the judge model, the exact
    prompt, and its SHA-256 fingerprint."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")

    per_trial: list[dict[str, Any]] = []
    for i in range(n):
        rewrite = rewrite_fn(text, trial=i)
        if not isinstance(rewrite, str):
            raise RewriteError(
                f"rewrite_fn returned {type(rewrite).__name__}, expected str"
            )
        trial = _trial_distance(text, rewrite)
        trial["trial"] = i
        per_trial.append(trial)

    edit_distances = [t["edit_distance"] for t in per_trial]
    overlap_distances = [t["token_overlap_distance"] for t in per_trial]

    mean_edit = round(statistics.fmean(edit_distances), 6)
    mean_overlap = round(statistics.fmean(overlap_distances), 6)
    sd_edit = (
        round(statistics.stdev(edit_distances), 6)
        if len(edit_distances) >= 2 else None
    )

    return {
        "mean_rewrite_distance": mean_edit,
        "mean_token_overlap_distance": mean_overlap,
        "edit_distance_sd": sd_edit,
        "n_trials": n,
        "per_trial_distances": per_trial,
        "provenance": {
            "judge_model": judge_model,
            "rewrite_prompt": prompt_template,
            "prompt_fingerprint_sha256": prompt_fingerprint(prompt_template),
            "distance_metrics": [
                "edit_distance (1 - difflib char-ratio)",
                "token_overlap_distance (1 - Jaccard over token sets)",
            ],
            "computed_at": utc_now(),
        },
    }


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------- rewrite backends (operator-supplied LLM) -------

def _api_rewrite_fn(model: str, *, prompt_template: str = REWRITE_PROMPT,
                    temperature: float = 0.7, max_tokens: int = 4096) -> RewriteFn:
    """Build an Anthropic-backed rewrite function.

    Mirrors narrative_judge's pluggable-client pattern: the SDK is
    lazy-imported on first construction and credentials come from the
    environment. This exists so an operator can spot-check a document; it is
    NEVER exercised in tests (tests inject a stub instead)."""
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RewriteError(
            "the default rewrite backend requires the `anthropic` SDK; "
            "`pip install anthropic` (or inject your own rewrite_fn)."
        ) from exc

    try:
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    except Exception as exc:  # noqa: BLE001
        raise RewriteError(
            f"anthropic client construction failed: {exc}"
        ) from exc

    def _run(text: str, *, trial: int = 0) -> str:
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {
                        "role": "user",
                        "content": render_prompt(
                            text, prompt_template=prompt_template
                        ),
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise RewriteError(
                f"anthropic rewrite call failed (trial {trial}): {exc}"
            ) from exc
        return "".join(
            block.text
            for block in msg.content
            if getattr(block, "type", None) == "text"
        )

    return _run


def build_rewrite_fn(judge_model: str, *,
                     prompt_template: str = REWRITE_PROMPT) -> RewriteFn:
    """Construct the default (API-backed) rewrite function for the CLI.

    Operators who want a different provider should import
    ``audit_rewriting_invariance`` and pass their own ``rewrite_fn`` rather
    than relying on this reference backend."""
    return _api_rewrite_fn(judge_model, prompt_template=prompt_template)


# ----------------- claim license ----------------------------------

def _claim_license(*, judge_model: str,
                   prompt_template: str = REWRITE_PROMPT) -> ClaimLicense:
    fp = prompt_fingerprint(prompt_template)
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            f"the mean rewriting distance of this target under judge model "
            f"`{judge_model}` and the pinned rewrite prompt "
            f"(fingerprint `{fp[:12]}…`) — a Raidar-style discrimination "
            f"signal: lower distance is more AI-like, higher is more "
            f"human-like, as descriptive evidence."
        ),
        does_not_license=(
            "any AI-vs-human verdict. This surface is uncalibrated and ships "
            "no threshold and no band; a classification requires "
            "operator-supplied thresholds calibrated on a labeled corpus "
            "under THIS judge model and THIS prompt."
        ),
        comparison_set={
            "mode": "single_document_rewrite_invariance",
            "judge_model": judge_model,
            "prompt_fingerprint_sha256": fp,
        },
        additional_caveats=[
            "The distance depends STRONGLY on the judge model and the exact "
            "rewrite prompt — both are pinned in provenance. A different "
            "model or a reworded prompt yields a different (incomparable) "
            "number; never compare distances across judge models or prompts.",
            "Each trial is a billed LLM call; cost scales with --n and target "
            "length. Budget accordingly.",
            "Uncalibrated discrimination evidence — no band, no verdict, no "
            "shipped threshold. Stack with other signals; do not read alone.",
            "Clean-room implementation of the published Raidar idea (Mao et "
            "al., ICLR 2024); no Raidar repo code/data was vendored.",
        ],
        references=[
            "specs/15-raidar-rewriting-invariance.md",
            "Mao et al., Raidar: geneRative AI Detection viA Rewriting "
            "(ICLR 2024, arXiv:2401.12970).",
        ],
    )


# ----------------- envelope + report ------------------------------

def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  word_count: int, judge_model: str, available: bool,
                  prompt_template: str = REWRITE_PROMPT,
                  warnings: list[str] | None = None) -> dict[str, Any]:
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
                judge_model=judge_model, prompt_template=prompt_template
            )
            if available else None
        ),
        available=available,
        warnings=warnings,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Rewriting-invariance profile — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_No rewriting-invariance profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    prov = r["provenance"]
    lines += [
        "## Rewrite distance",
        "",
        f"- **Mean rewrite distance (edit):** {r['mean_rewrite_distance']} "
        f"(0 = unchanged → AI-like; 1 = fully rewritten → human-like)",
        f"- **Mean token-overlap distance:** "
        f"{r['mean_token_overlap_distance']}",
        f"- **Edit-distance SD:** {r['edit_distance_sd']}",
        f"- **Trials:** {r['n_trials']}",
        "",
        "### Per-trial distances",
        "",
    ]
    for t in r["per_trial_distances"]:
        lines.append(
            f"- trial {t['trial']}: edit={t['edit_distance']}, "
            f"token_overlap={t['token_overlap_distance']}, "
            f"rewrite_words={t['rewrite_words']}"
        )
    lines += [
        "",
        "### Provenance",
        "",
        f"- **Judge model:** `{prov['judge_model']}`",
        f"- **Prompt fingerprint (SHA-256):** "
        f"`{prov['prompt_fingerprint_sha256']}`",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", help="Path to the target text file (UTF-8).")
    p.add_argument(
        "--judge", required=True, metavar="MODEL",
        help=(
            "Operator-supplied LLM model id used to rewrite the target "
            "(e.g. claude-sonnet-4-6). The reference backend uses the "
            "`anthropic` SDK with ANTHROPIC_API_KEY from the environment."
        ),
    )
    p.add_argument(
        "--n", type=int, default=3, metavar="N",
        help="Number of rewrite trials to average over (default: 3).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None,
         rewrite_fn: RewriteFn | None = None) -> int:
    """CLI entry point.

    ``rewrite_fn`` is exposed so an embedding harness (or a test driving
    main end-to-end) can inject a deterministic rewriter; when None, the CLI
    builds the API-backed reference backend from ``--judge``."""
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2
    if args.n < 1:
        sys.stderr.write(f"--n must be >= 1, got {args.n}\n")
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")
    word_count = count_words(text)

    if word_count < LENGTH_FLOOR_WORDS:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count,
            judge_model=args.judge, available=False,
            warnings=[
                f"Target is {word_count} words; below the "
                f"{LENGTH_FLOOR_WORDS}-word floor for a meaningful "
                "rewriting-invariance reading."
            ],
        )
    else:
        fn = rewrite_fn if rewrite_fn is not None else build_rewrite_fn(
            args.judge
        )
        try:
            results = audit_rewriting_invariance(
                text, fn, n=args.n, judge_model=args.judge,
            )
        except RewriteError as exc:
            sys.stderr.write(f"Rewrite backend error: {exc}\n")
            return 1
        payload = build_payload(
            results, target_path=target_path, word_count=word_count,
            judge_model=args.judge, available=True,
        )

    text_out = (
        json.dumps(payload, indent=2, default=str)
        if args.json else render_report(payload)
    )
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
