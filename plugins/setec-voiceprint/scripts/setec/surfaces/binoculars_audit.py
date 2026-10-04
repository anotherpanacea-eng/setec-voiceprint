"""Binoculars audit (Design 3-tight) — perplexity-ratio v1.

Computes the perplexity ratio of a target text under a scorer language
model relative to an observer language model. Lower scores indicate
the target is more predictable under the scorer than the observer — a
known signal for AI-generated text in the Hans et al. 2024 framework.

v1 ships the perplexity-ratio (PR) baseline. True Binoculars (eq. 1 in
Hans et al. 2024) uses cross-perplexity between the observer's full
distribution and the scorer's full distribution; computing that requires
extending surprisal_backend to expose per-token distributions, which is
deferred to a v2 follow-up PR. The CLI surface and evidence-pack schema
are API-compatible between v1 and v2.

Implements SPEC_binoculars_audit.md v0.1.

CLI:
    python3 binoculars_audit.py TARGET.txt \\
        [--scorer ALIAS_OR_HF_ID] [--observer ALIAS_OR_HF_ID] \\
        [--scorer-revision SHA] [--observer-revision SHA] \\
        [--surprisal-dtype auto|fp32|fp16|bf16] \\
        [--threshold-low FLOAT] [--threshold-high FLOAT] \\
        [--out PATH] [--out-md PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore
from surprisal_backend import (  # type: ignore
    SurprisalBackend,
    SurprisalBackendError,
)


SCRIPT_VERSION = "0.2.0"
TASK_SURFACE = "binoculars_discrimination"
TOOL_NAME = "binoculars_audit"
SCORE_VERSION_V1 = "perplexity_ratio_v1"
SCORE_VERSION_V2 = "binoculars_cross_perplexity_v2"

DEFAULT_SCORER = "tinyllama"
DEFAULT_OBSERVER = "gpt2"
# Thresholds default to None on purpose: v1 ships no framework-calibrated
# operating point for the perplexity-ratio score against any model pair, so
# the default verdict band is "uncalibrated". Operators who have calibrated
# thresholds for their own model-pair + corpus can supply them explicitly via
# --threshold-low / --threshold-high, and the audit will then surface
# ai_likely / indeterminate / human_likely bands with a caveat noting the
# thresholds are operator-supplied. Hard-coding numeric defaults here would
# violate the framework rule against shipping thresholded claims without
# calibration. v2 may add framework-calibrated defaults once empirical
# threshold data is collected against labelled corpora.
DEFAULT_THRESHOLD_LOW: float | None = None
DEFAULT_THRESHOLD_HIGH: float | None = None
MIN_STABLE_TOKENS = 50


DEFAULT_LICENSES = (
    "Reports the perplexity ratio of the target text under a scorer "
    "language model relative to an observer language model. Lower "
    "scores indicate the target is more predictable under the scorer "
    "than the observer — a known signal for AI-generated text in the "
    "Hans et al. 2024 framework. The score is a numeric measurement "
    "against the chosen model pair; it is not a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license a binary AI/human authorship verdict. The score "
    "is one measurement against one model pair; operator judgment "
    "remains the load-bearing decision step. Ships without "
    "framework-calibrated thresholds by default: the verdict band is "
    "'uncalibrated' and the audit reports the raw ratio only. Operators "
    "who supply --threshold-low / --threshold-high explicitly take "
    "responsibility for those thresholds being appropriate for their "
    "model pair, corpus, and register (and for the score version — "
    "true Binoculars cross-perplexity v2 and the perplexity-ratio v1 "
    "baseline have different numerical scales, so thresholds calibrated "
    "for one don't transfer). Does not control for memorization (if "
    "the target is in the training set of either model, the score will "
    "be biased). Does not generalize across genres without "
    "operator-validated calibration. v2 computes true Hans et al. 2024 "
    "cross-perplexity when scorer and observer share a tokenizer; "
    "falls back to the v1 perplexity-ratio baseline with a caveat "
    "otherwise. Does not substitute for stylometric, embedding-based, "
    "or other framework audits — it complements them."
)


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


def _mean(series: list[float]) -> float:
    if not series:
        return 0.0
    return sum(series) / len(series)


def _band(ratio: float | None, low: float | None, high: float | None) -> str:
    if ratio is None:
        return "unavailable"
    if low is None or high is None:
        return "uncalibrated"
    if ratio < low:
        return "ai_likely"
    if ratio > high:
        return "human_likely"
    return "indeterminate"


def _tokenizers_compatible(
    scorer: SurprisalBackend, observer: SurprisalBackend,
) -> bool:
    """True when scorer + observer share an aligned vocabulary.

    Cross-perplexity requires the two models' vocab indices to mean
    the same thing — the formula sums
    ``exp(P_observer(v|ctx)) * log P_scorer(v|ctx)`` over ALL vocab
    positions v, so if token id N decodes to "foo" in one model and
    "bar" in the other, the sum is computing cross-entropy over
    misaligned distributions and produces a meaningless number.

    Load-bearing check: ``vocab_sha256`` — a deterministic hash of
    each tokenizer's full token→id table (from
    ``tokenizer.get_vocab()``). Matching hashes prove the vocabularies
    align entry-for-entry. The canonical Hans et al. 2024 Binoculars
    pair (Falcon-7b base + Falcon-7b-instruct) shares the same
    underlying tokenizer file and so the same vocab hash, even though
    ``model_name_or_path`` differs — this check accepts that pair.

    ``tokenizer_class`` and ``vocab_size`` are kept as a sanity
    pre-check (two backends that don't match on these can't share a
    vocab hash either; the early exit avoids the false-positive of
    two unrelated tokenizers that happen to collide on hash).

    Defensive: ``vocab_sha256 = None`` (tokenizer without
    ``get_vocab``) is treated as incompatible — the audit falls back
    to v1 PR rather than silently computing cross-perplexity over an
    un-fingerprinted vocab. Stub backends without
    ``tokenizer_identity()`` likewise fall back.
    """
    try:
        a = scorer.tokenizer_identity()
        b = observer.tokenizer_identity()
    except (AttributeError, Exception):  # noqa: BLE001
        return False
    a_vocab = a.get("vocab_sha256")
    b_vocab = b.get("vocab_sha256")
    if a_vocab is None or b_vocab is None:
        return False
    return (
        a.get("tokenizer_class") == b.get("tokenizer_class")
        and a.get("vocab_size") == b.get("vocab_size")
        and a_vocab == b_vocab
    )


def _cross_perplexity_log_nats(
    scorer_log_probs_nats: list[list[float]],
    observer_log_probs_nats: list[list[float]],
) -> float | None:
    """Compute ``log X-PPL(s | scorer, observer)`` per Hans et al. 2024:

        log X-PPL = -(1/L) * sum_l sum_v P_obs(v | s_<l) * log P_scorer(v | s_<l)

    Inputs are per-token log-probability distributions in NATS,
    one per token position (length L). Returns the natural-log
    cross-perplexity; callers divide ``log_PPL_scorer`` (also in
    nats) by this value to produce the Binoculars score B.

    Returns None when either input is empty or the two have
    different length (cross-perplexity is undefined without
    aligned positions).
    """
    import math
    L = len(scorer_log_probs_nats)
    if L == 0 or L != len(observer_log_probs_nats):
        return None
    total = 0.0
    for s_dist, o_dist in zip(scorer_log_probs_nats, observer_log_probs_nats):
        if len(s_dist) != len(o_dist):
            return None
        # P_obs(v) = exp(log P_obs(v)). Cross-entropy term at this
        # position: sum_v exp(o[v]) * s[v]. Negated and summed over
        # positions then divided by L gives log X-PPL.
        position_sum = 0.0
        for s_lp, o_lp in zip(s_dist, o_dist):
            position_sum += math.exp(o_lp) * s_lp
        total += position_sum
    return -total / L


def audit(
    target_text: str,
    *,
    scorer: SurprisalBackend,
    observer: SurprisalBackend,
    threshold_low: float | None = DEFAULT_THRESHOLD_LOW,
    threshold_high: float | None = DEFAULT_THRESHOLD_HIGH,
    score_fn=None,
    use_cross_perplexity: bool | None = None,
) -> dict[str, Any]:
    """Run the audit. Returns the results dict that gets wrapped into
    the build_output() envelope.

    ``score_fn`` is a test injection point. If supplied, it must be a
    callable ``score_fn(backend, text) -> list[float]`` returning the
    per-token surprisal series in bits. Production callers pass
    ``score_fn=None`` and the audit calls ``backend.score_text`` directly.

    ``use_cross_perplexity`` controls v2 behavior:
      - ``None`` (default): auto-detect — use cross-perplexity when
        scorer + observer share a tokenizer identity, fall back to
        the v1 perplexity-ratio baseline otherwise.
      - ``True``: require cross-perplexity. Errors via a caveat if
        tokenizers are incompatible and falls back to PR.
      - ``False``: always use the v1 PR baseline (legacy v1 behavior).

    The v2 path requires ``scorer.score_text_with_distributions``
    and ``observer.score_text_with_distributions`` (added in 1.X+
    via surprisal_backend's distribution-returning extension). Tests
    inject stub backends supplying both methods.
    """
    caveats: list[str] = []

    # Decide which score version to compute.
    tokenizers_match = _tokenizers_compatible(scorer, observer)
    if use_cross_perplexity is None:
        prefer_v2 = tokenizers_match
    elif use_cross_perplexity is True:
        prefer_v2 = tokenizers_match
        if not tokenizers_match:
            caveats.append("v2_requested_but_tokenizers_incompatible_falling_back_to_v1")
    else:
        prefer_v2 = False

    score_version = SCORE_VERSION_V2 if prefer_v2 else SCORE_VERSION_V1
    cross_perplexity_log_nats: float | None = None
    scorer_log_ppl_nats: float | None = None

    # v2 path: use score_text_with_distributions and compute true
    # cross-perplexity Binoculars.
    if prefer_v2 and score_fn is None:
        try:
            scorer_series_bits, scorer_log_probs, scorer_tokens = (
                scorer.score_text_with_distributions(target_text)
            )
            observer_series_bits, observer_log_probs, observer_tokens = (
                observer.score_text_with_distributions(target_text)
            )
        except AttributeError:
            # Backend doesn't expose distributions (older surprisal_backend
            # version or stub without the method) — fall back gracefully.
            caveats.append("backend_lacks_score_text_with_distributions_falling_back_to_v1")
            prefer_v2 = False
            score_version = SCORE_VERSION_V1
            scorer_series = scorer.score_text(target_text)
            observer_series = observer.score_text(target_text)
        else:
            scorer_series = scorer_series_bits
            observer_series = observer_series_bits
            # Strict per-input check: actual token-id sequences must match.
            if scorer_tokens != observer_tokens:
                caveats.append("token_id_sequences_differ_falling_back_to_v1")
                prefer_v2 = False
                score_version = SCORE_VERSION_V1
            else:
                # Compute cross-perplexity. Inputs are in nats; result is
                # log_X_PPL in nats.
                cross_perplexity_log_nats = _cross_perplexity_log_nats(
                    scorer_log_probs, observer_log_probs,
                )
                # log_PPL_scorer in nats: mean of -log_prob_actual_token,
                # which equals scorer surprisal in nats. The surprisal
                # series we got is in bits — convert.
                import math
                ln2 = math.log(2.0)
                scorer_log_ppl_nats = _mean(scorer_series_bits) * ln2
                if cross_perplexity_log_nats is None:
                    caveats.append("cross_perplexity_undefined_falling_back_to_v1")
                    prefer_v2 = False
                    score_version = SCORE_VERSION_V1
    elif prefer_v2 and score_fn is not None:
        # Test injection: score_fn returns surprisal series only, so
        # we honor the v1 path for stubbed tests. Tests targeting v2
        # cross-perplexity should use a stub backend that implements
        # score_text_with_distributions instead.
        prefer_v2 = False
        score_version = SCORE_VERSION_V1
        scorer_series = score_fn(scorer, target_text)
        observer_series = score_fn(observer, target_text)
    else:
        # v1 path (legacy / fallback): per-token surprisal series only.
        if score_fn is None:
            scorer_series = scorer.score_text(target_text)
            observer_series = observer.score_text(target_text)
        else:
            scorer_series = score_fn(scorer, target_text)
            observer_series = score_fn(observer, target_text)

    scorer_id = scorer.model_id
    observer_id = observer.model_id
    if scorer_id == observer_id:
        caveats.append("scorer_equals_observer")

    if len(scorer_series) != len(observer_series):
        caveats.append(f"tokenizer_mismatch:{len(scorer_series)}vs{len(observer_series)}")

    if len(scorer_series) < MIN_STABLE_TOKENS or len(observer_series) < MIN_STABLE_TOKENS:
        caveats.append("target_too_short_for_stable_estimate")

    scorer_mean = _mean(scorer_series)
    observer_mean = _mean(observer_series)

    # Compute the headline score per the chosen score_version.
    ratio: float | None
    if score_version == SCORE_VERSION_V2 and cross_perplexity_log_nats is not None and scorer_log_ppl_nats is not None:
        if abs(cross_perplexity_log_nats) < 1e-9:
            caveats.append("cross_perplexity_near_zero")
            ratio = None
        else:
            ratio = scorer_log_ppl_nats / cross_perplexity_log_nats
    else:
        # v1 PR baseline.
        if observer_mean < 1e-6:
            caveats.append("observer_perplexity_near_zero")
            ratio = None
        else:
            ratio = scorer_mean / observer_mean

    verdict_band = _band(ratio, threshold_low, threshold_high)

    if verdict_band == "uncalibrated":
        caveats.append("no_calibrated_thresholds_supplied")
    elif threshold_low is not None and threshold_high is not None:
        caveats.append("thresholds_operator_supplied_not_framework_calibrated")

    return {
        "scorer": {
            "model_id": scorer.model_id,
            "revision": scorer.revision,
            "identifier_block": scorer.identifier_block(),
        },
        "observer": {
            "model_id": observer.model_id,
            "revision": observer.revision,
            "identifier_block": observer.identifier_block(),
        },
        "scorer_log_perplexity_bits": scorer_mean,
        "observer_log_perplexity_bits": observer_mean,
        "perplexity_ratio": ratio,
        "score_version": score_version,
        # v2: surface the cross-perplexity intermediate when computed
        # so audit consumers can inspect both components of the
        # Binoculars ratio without recomputing.
        "cross_perplexity_log_nats": cross_perplexity_log_nats,
        "scorer_log_perplexity_nats": scorer_log_ppl_nats,
        "tokenizers_compatible": tokenizers_match,
        "thresholds": {"low": threshold_low, "high": threshold_high},
        "verdict_band": verdict_band,
        "scorer_series_length": len(scorer_series),
        "observer_series_length": len(observer_series),
        "caveats": caveats,
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
) -> dict[str, Any]:
    caveats = list(results.get("caveats", []))

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "scorer_model": results["scorer"]["model_id"],
            "observer_model": results["observer"]["model_id"],
            "score_version": results["score_version"],
            "threshold_low": results["thresholds"]["low"],
            "threshold_high": results["thresholds"]["high"],
        },
        additional_caveats=caveats,
        references=[
            "Hans et al. 2024, 'Spotting LLMs With Binoculars: Zero-Shot Detection of Machine-Generated Text'",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    score_version = results.get("score_version", SCORE_VERSION_V1)
    is_v2 = score_version == SCORE_VERSION_V2

    title_suffix = (
        "(Cross-Perplexity v2)" if is_v2 else "(Perplexity Ratio v1)"
    )

    lines: list[str] = []
    lines.append(f"# Binoculars Audit {title_suffix}")
    lines.append("")
    lines.append(f"- **Target:** `{target.get('path')}` ({target.get('words')} words)")
    lines.append(f"- **Scorer:** `{results['scorer']['model_id']}` (rev `{results['scorer']['revision']}`)")
    lines.append(f"- **Observer:** `{results['observer']['model_id']}` (rev `{results['observer']['revision']}`)")
    lines.append(f"- **Score version:** `{score_version}`")
    lines.append(f"- **Tokenizers compatible:** {results.get('tokenizers_compatible')}")
    lines.append("")

    lines.append("## Score")
    lines.append("")
    lines.append("| | Log-perplexity (bits) | Series length |")
    lines.append("|---|---:|---:|")
    lines.append(f"| Scorer | {results['scorer_log_perplexity_bits']:.4f} | {results['scorer_series_length']} |")
    lines.append(f"| Observer | {results['observer_log_perplexity_bits']:.4f} | {results['observer_series_length']} |")
    if is_v2:
        xppl = results.get("cross_perplexity_log_nats")
        scorer_nats = results.get("scorer_log_perplexity_nats")
        if xppl is not None:
            lines.append(f"| log X-PPL (nats) | {xppl:.4f} | — |")
        if scorer_nats is not None:
            lines.append(f"| log PPL scorer (nats) | {scorer_nats:.4f} | — |")
    ratio = results.get("perplexity_ratio")
    ratio_text = f"{ratio:.4f}" if ratio is not None else "(unavailable)"
    lines.append("")
    if is_v2:
        lines.append(f"**Binoculars score B = log_PPL_scorer / log_X-PPL:** {ratio_text}")
    else:
        lines.append(f"**Perplexity ratio (scorer/observer):** {ratio_text}")
    lines.append(f"**Verdict band:** `{results['verdict_band']}` (thresholds: low={results['thresholds']['low']}, high={results['thresholds']['high']})")
    lines.append("")

    caveats = results.get("caveats") or []
    lines.append("## Caveats")
    lines.append("")
    if caveats:
        for c in caveats:
            lines.append(f"- {c}")
    else:
        lines.append("(none surfaced)")
    lines.append("")

    lines.append("## Claim license")
    lines.append("")
    lines.append(envelope["claim_license_rendered"].rstrip())
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- **Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}")
    lines.append(f"- **Scorer identifier_block:** `{json.dumps(results['scorer']['identifier_block'])}`")
    lines.append(f"- **Observer identifier_block:** `{json.dumps(results['observer']['identifier_block'])}`")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Binoculars audit (Design 3-tight) — perplexity-ratio v1."
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument("--scorer", default=DEFAULT_SCORER, help=f"Scorer model alias or HF ID (default {DEFAULT_SCORER}).")
    parser.add_argument("--observer", default=DEFAULT_OBSERVER, help=f"Observer model alias or HF ID (default {DEFAULT_OBSERVER}).")
    parser.add_argument("--scorer-revision", default=None, help="Pin scorer HF commit SHA for reproducibility.")
    parser.add_argument("--observer-revision", default=None, help="Pin observer HF commit SHA for reproducibility.")
    parser.add_argument("--surprisal-dtype", choices=("auto", "fp32", "fp16", "bf16"), default="auto", help="Precision for both model loads (default auto).")
    parser.add_argument("--threshold-low", type=float, default=DEFAULT_THRESHOLD_LOW, help="Below this ratio, verdict is ai_likely. No framework-calibrated default; without this flag the verdict band is 'uncalibrated' and no likely-author label is emitted.")
    parser.add_argument("--threshold-high", type=float, default=DEFAULT_THRESHOLD_HIGH, help="Above this ratio, verdict is human_likely. See --threshold-low note on calibration.")
    parser.add_argument("--out", default=None, help="Evidence pack JSON path (default <target-parent>/<stem>.binoculars.json).")
    parser.add_argument("--out-md", default=None, help="Evidence pack markdown path (default <target-parent>/<stem>.binoculars.md).")
    parser.add_argument("--json", action="store_true", help="Emit the schema_version 1.0 envelope as JSON on stdout (R2 consumer delivery). Default evidence-pack writes are skipped; explicit --out/--out-md are still honored.")
    parser.add_argument("--licenses", default=DEFAULT_LICENSES, help="Override the claim_license.licenses text.")
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE, help="Override the claim_license.does_not_license text.")
    parser.add_argument(
        "--score-version",
        choices=("auto", "v1", "v2"),
        default="auto",
        help=(
            "Which Binoculars score to compute. 'auto' (default) "
            "uses v2 cross-perplexity when scorer + observer share "
            "a tokenizer, falls back to v1 perplexity-ratio with a "
            "caveat otherwise. 'v1' forces the perplexity-ratio "
            "baseline (Hans et al. 2024 prior method). 'v2' "
            "requests true cross-perplexity Binoculars; falls back "
            "to v1 with a caveat if tokenizers are incompatible."
        ),
    )
    args = parser.parse_args(argv)

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"error: target file not found at {target_path}", file=sys.stderr)
        return 1

    try:
        target_text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"error: target not valid UTF-8: {exc}", file=sys.stderr)
        return 1

    target_words = count_words(target_text)

    try:
        scorer = SurprisalBackend(
            model_id=args.scorer,
            revision=args.scorer_revision,
            dtype=args.surprisal_dtype,
        )
    except SurprisalBackendError as exc:
        print(f"error: scorer backend construction failed ({args.scorer}): {exc}", file=sys.stderr)
        return 3

    try:
        observer = SurprisalBackend(
            model_id=args.observer,
            revision=args.observer_revision,
            dtype=args.surprisal_dtype,
        )
    except SurprisalBackendError as exc:
        print(f"error: observer backend construction failed ({args.observer}): {exc}", file=sys.stderr)
        return 3

    # --score-version → audit() use_cross_perplexity arg.
    use_xppl: bool | None
    if args.score_version == "auto":
        use_xppl = None
    elif args.score_version == "v2":
        use_xppl = True
    else:
        use_xppl = False

    try:
        results = audit(
            target_text,
            scorer=scorer,
            observer=observer,
            threshold_low=args.threshold_low,
            threshold_high=args.threshold_high,
            use_cross_perplexity=use_xppl,
        )
    except SurprisalBackendError as exc:
        print(f"error: scoring failed ({args.scorer} / {args.observer}): {exc}", file=sys.stderr)
        return 3

    envelope = compose_envelope(
        target_path=target_path,
        target_words=target_words,
        results=results,
        licenses_text=args.licenses,
        does_not_license_text=args.does_not_license,
    )
    markdown = render_markdown(envelope)

    if args.json:
        # json_delivery: stdout — the envelope is the only stdout output, so
        # dispatcher/consumer calls don't get side files next to the target.
        # Explicit --out/--out-md are still honored (notices go to stderr).
        if args.out:
            out_json = Path(args.out)
            out_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
            print(f"Wrote {out_json}", file=sys.stderr)
        if args.out_md:
            out_md = Path(args.out_md)
            out_md.write_text(markdown, encoding="utf-8")
            print(f"Wrote {out_md}", file=sys.stderr)
        print(json.dumps(envelope, indent=2, default=str))
        return 0

    out_json = Path(args.out) if args.out else target_path.with_suffix(target_path.suffix + ".binoculars.json")
    out_md = Path(args.out_md) if args.out_md else target_path.with_suffix(target_path.suffix + ".binoculars.md")
    out_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    out_md.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_json} + {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
