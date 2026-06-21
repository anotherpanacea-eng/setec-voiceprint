#!/usr/bin/env python3
"""paraphrase_robustness.py — detector-level AUC-degradation harness (spec 33, M1).

Measures, for a *labeled corpus*, how much each detector's
``P(machine > human)`` separation **degrades** when the machine windows are
paraphrased. This is the corpus-level, population-ranking counterpart of
``paraphrase_ladder`` (which reports a per-(signal × rung) decay curve on
*single texts*). The two measure at different levels and do not overlap:

  * ``paraphrase_ladder`` — one text, paraphrased again and again, one
    signal's reading moving rung to rung (single-text signal movement).
  * THIS harness — a whole labeled corpus, machine windows paraphrased,
    each *detector's* AUC over the corpus moving rung to rung (population
    ranking shift).

It is **orchestration over INJECTED scores**, not a detector and not a new
signal:

  * STAGE 1 (attack) applies an injectable :class:`Paraphraser` to the
    machine windows ONLY — human windows are the fixed reference class and
    are never paraphrased (the adversary paraphrases its own output).
  * STAGE 2 (re-score) is an injectable :class:`Scorer`. In **M1** the
    scorer returns pre-supplied per-(detector, rung) score lists (no model,
    CI-runnable). In **M2** a GPU operator binds a real scorer over
    ``binoculars_audit`` / ``surprisal_backend`` / ``fast_detect_curvature``
    / ``variance_audit`` — *no model is imported at module load here*.
  * STAGE 3 (report) computes, per (detector, rung): the discriminative
    ``AUC = P(machine > human)`` (WMW-U via the existing
    ``validation_harness.fallback_roc_auc``, **oriented by each detector's
    known machine-vs-human sign** so a "machine lower" signal is not read as
    inverted), ``TPR`` at ``FPR`` budgets {0.05, 0.10}, and the Δ from
    rung 0.

Posture (the ``pan_replay`` / ``paraphrase_ladder`` line, carried forward):

  * It emits **NO aggregate robustness or accuracy scalar** — no
    ``robustness_score`` / ``auc_retained`` / ``area_under_decay`` /
    ``is_robust`` / ``overall_robustness`` / ``n_robust_signals`` /
    ``headline`` anywhere in the payload. The per-(detector × rung)
    ``auc`` / ``tpr_at_fpr05`` / ``delta_auc`` table IS the deliverable;
    those per-cell keys are descriptive, not the banned summary scalar.
  * The **sign/direction is pinned** (``DETECTOR_DIRECTION``) so silent
    inversion — the detection family's shared failure mode — fails a test
    rather than shipping a flipped AUC.
  * It is never a selector, a calibration-threshold input, or a reward.
    Its ``ClaimLicense`` refuses any "robust to paraphrase" claim AND any
    detector-accuracy headline, quoting Sadasivan's separability ceiling.
  * ``calibration_status`` for any downstream surface stays ``heuristic``;
    no threshold is derived from this experiment.

The bundled M1 paraphraser (:class:`StdlibProxyParaphraser`, label
``proxy_stdlib``) is a deterministic, model-free lexical proxy — honestly
weaker than a neural paraphraser. A flat degradation curve under it means
the proxy did not erode separation at THIS strength, **never** that a
detector is paraphrase-robust (Sadasivan et al. 2023, arXiv:2303.11156).
The realistic neural attack (back-translation / DIPPER) is the GPU-gated M2
seam, in a later PR.

Usage (M1, model-free, over an injected-scores JSON)::

    python3 plugins/setec-voiceprint/scripts/calibration/paraphrase_robustness.py \\
        --injected-scores scores.json --json

The injected-scores JSON shape is documented on
:func:`run_from_injected_scores`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# ``parents[1]`` is the scripts/ directory (this file lives in
# scripts/calibration/), matching paraphrase_ladder.py / pan_replay.py.
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from output_schema import build_output  # type: ignore  # noqa: E402
from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from validation_harness import fallback_roc_auc  # type: ignore  # noqa: E402

TASK_SURFACE = "validation"
TOOL_NAME = "paraphrase_robustness"
SCRIPT_VERSION = "1.0"

# Honest label for the M1 stdlib proxy paraphraser. Stamped everywhere so a
# flat proxy curve is never read as a neural-paraphrase (DIPPER) result.
PROXY_LABEL = "proxy_stdlib"

# Operating-point FPR budgets reported before/after attack (the honest-FPR
# posture: AUC alone is not licensed).
FPR_BUDGETS = (0.05, 0.10)

# Sadasivan's separability ceiling, quoted in the ClaimLicense
# (arXiv:2303.11156). Every flat curve is framed against it.
_SADASIVAN_CEILING = (
    "As a paraphraser approaches the human distribution, all detectors "
    "converge toward 0.5-AUROC separability (Sadasivan et al. 2023, "
    "arXiv:2303.11156, AUROC(D) <= 0.5 + TV(M,H) - TV(M,H)^2/2); a flat "
    "degradation curve here means the attack did not erode separation at "
    "THIS paraphrase strength, never that a detector is paraphrase-robust."
)

# ------------------------------------------------------------------ #
# Sign/direction map — the SINGLE source of sign truth (the silent-      #
# inversion guard). Each detector's machine side is fixed and known;     #
# "lower" detectors are NEGATED before the WMW-U AUC so the reported AUC  #
# is the discriminative P(machine > human) separation, not its inverse.  #
#                                                                        #
# Provenance:                                                            #
#   * binoculars_v2: binoculars_audit._band — ``ratio < low -> ai_likely``#
#     => machine has the LOWER cross-perplexity ratio.                   #
#   * surprisal_*: surprisal_backend.SMOOTHED_DIRECTION                  #
#     (mean "lt", sd "lt", acf_lag1 "gt").                               #
#   * variance signals: variance_audit — machine (smoothed) prose runs   #
#     HIGHER Yule's K, LOWER burstiness_B, LOWER MTLD.                   #
#   * fast_detect_curvature: machine HIGHER curvature z.                 #
# "higher" => machine scores higher => raw scores used as-is.            #
# "lower"  => machine scores lower  => scores negated before AUC.        #
# ------------------------------------------------------------------ #
DETECTOR_DIRECTION: dict[str, str] = {
    "binoculars_v2": "lower",
    "fast_detect_curvature": "higher",
    "surprisal_mean": "lower",
    "surprisal_sd": "lower",
    "surprisal_acf_lag1": "higher",
    "yules_k": "higher",
    "burstiness_B": "lower",
    "mtld": "lower",
}

# Banned aggregate-scalar keys (the pan_replay _walk set, scoped to the
# AGGREGATE summary names — NOT the legitimate per-cell auc/tpr/fpr fields,
# which are this harness's whole deliverable).
BANNED_AGGREGATE_KEYS = frozenset({
    "robustness_score",
    "auc_retained",
    "area_under_decay",
    "is_robust",
    "overall_robustness",
    "n_robust_signals",
    "headline",
})


# ============================ Protocols ============================ #


@runtime_checkable
class Paraphraser(Protocol):
    """Injectable attack-set generator. The runner defines its OWN Protocol
    (the ``LadderParaphraser`` seam the roadmap spec referenced does not
    exist on ``feat/raid-dipper-robustness``). ``label`` is stamped into the
    report so a proxy curve is never read as a neural-paraphrase result."""

    label: str

    def paraphrase(self, text: str, *, rung: int) -> str:
        ...


@runtime_checkable
class Scorer(Protocol):
    """Injectable detector. M1 binds a stub returning pre-supplied scores;
    M2 binds a real GPU scorer over the existing detectors. No model is
    imported here."""

    def score(self, detector: str, texts: list[str]) -> list[float]:
        ...


# ====================== Bundled M1 paraphraser ===================== #

# A small CLOSED synonym table (NOT a model). Deterministic and honestly
# weak — a lexical proxy, never a neural paraphraser.
_PROXY_SYNONYMS: dict[str, str] = {
    "very": "extremely",
    "big": "large",
    "small": "tiny",
    "fast": "quick",
    "happy": "glad",
    "sad": "unhappy",
    "good": "fine",
    "bad": "poor",
    "begin": "start",
    "end": "finish",
    "house": "home",
    "child": "kid",
    "buy": "purchase",
    "make": "create",
    "show": "display",
    "use": "employ",
    "help": "assist",
    "near": "close",
    "old": "aged",
    "new": "recent",
}


class StdlibProxyParaphraser:
    """Deterministic, model-free lexical proxy (label ``proxy_stdlib``).

    Applies ``rung`` passes of a closed-table synonym swap plus a whitespace
    jitter. Honestly weaker than a neural paraphraser; used to verify the
    pipeline end-to-end and establish a degradation LOWER bound, never to
    license a robustness claim."""

    label = PROXY_LABEL

    def __init__(self, synonyms: dict[str, str] | None = None) -> None:
        self._synonyms = dict(synonyms) if synonyms is not None else dict(_PROXY_SYNONYMS)

    def _one_pass(self, text: str) -> str:
        out_tokens: list[str] = []
        for tok in text.split(" "):
            lower = tok.lower()
            repl = self._synonyms.get(lower)
            if repl is not None:
                # Preserve a leading capital.
                if tok[:1].isupper():
                    repl = repl[:1].upper() + repl[1:]
                out_tokens.append(repl)
            else:
                out_tokens.append(tok)
        # Whitespace jitter: collapse any runs to single spaces (a benign,
        # detector-visible surface change).
        return " ".join(" ".join(out_tokens).split())

    def paraphrase(self, text: str, *, rung: int) -> str:
        out = text
        for _ in range(max(0, int(rung))):
            out = self._one_pass(out)
        return out


# ===================== AUC / FPR-TPR / Δ math ===================== #


def _orient(detector: str, scores: list[float]) -> list[float]:
    """Negate scores for a 'lower-is-machine' detector so the WMW-U AUC is
    the discriminative ``P(machine > human)`` separation. Unknown detectors
    are left as-is (caller is responsible for registering a direction)."""
    if DETECTOR_DIRECTION.get(detector) == "lower":
        return [-s for s in scores]
    return list(scores)


def oriented_auc(
    detector: str,
    machine_scores: list[float],
    human_scores: list[float],
) -> float | None:
    """``AUC = P(machine > human)`` via the existing WMW-U helper, after
    orienting by the detector's known sign. Returns ``None`` for an empty /
    single-class input (the empty/tie edge case) — never a spurious 0.5."""
    m = _orient(detector, machine_scores)
    h = _orient(detector, human_scores)
    labels = [1] * len(m) + [0] * len(h)
    scores = list(m) + list(h)
    return fallback_roc_auc(labels, scores)


def tpr_at_fpr_budgets(
    detector: str,
    machine_scores: list[float],
    human_scores: list[float],
    budgets: tuple[float, ...] = FPR_BUDGETS,
) -> dict[str, float | None]:
    """For each FPR budget b, the max TPR achievable at FPR <= b, swept over
    candidate thresholds on the ORIENTED scores (machine = label 1, higher =
    more machine after orientation). Returns ``None`` per budget when a side
    is empty. No threshold is fit or shipped — this is a descriptive ROC read
    of the honest-FPR operating points."""
    m = _orient(detector, machine_scores)
    h = _orient(detector, human_scores)
    out: dict[str, float | None] = {}
    if not m or not h:
        for b in budgets:
            out[_fpr_key(b)] = None
        return out
    n_pos = len(m)
    n_neg = len(h)
    # Candidate thresholds: every distinct score; predict-machine when
    # oriented_score >= threshold.
    candidates = sorted(set(m) | set(h), reverse=True)
    for b in budgets:
        best_tpr = 0.0
        for thr in candidates:
            fp = sum(1 for x in h if x >= thr)
            fpr = fp / n_neg
            if fpr <= b:
                tp = sum(1 for x in m if x >= thr)
                tpr = tp / n_pos
                if tpr > best_tpr:
                    best_tpr = tpr
        out[_fpr_key(b)] = best_tpr
    return out


def _fpr_key(budget: float) -> str:
    # 0.05 -> "tpr_at_fpr05", 0.10 -> "tpr_at_fpr10"
    return f"tpr_at_fpr{int(round(budget * 100)):02d}"


# ========================= Orchestration ========================= #


def _score_rung(
    scorer: Scorer,
    detectors: list[str],
    machine_texts: list[str],
    human_texts: list[str],
) -> dict[str, dict[str, list[float]]]:
    """Re-score one rung. Human texts are passed UNMODIFIED (the fixed
    reference class). Returns ``{detector: {"machine": [...], "human": [...]}}``."""
    out: dict[str, dict[str, list[float]]] = {}
    for det in detectors:
        out[det] = {
            "machine": list(scorer.score(det, machine_texts)),
            "human": list(scorer.score(det, human_texts)),
        }
    return out


def _cell(
    detector: str,
    machine_scores: list[float],
    human_scores: list[float],
) -> dict[str, Any]:
    auc = oriented_auc(detector, machine_scores, human_scores)
    cell: dict[str, Any] = {"auc": auc}
    cell.update(tpr_at_fpr_budgets(detector, machine_scores, human_scores))
    return cell


def run_report(
    *,
    paraphraser: Paraphraser,
    scorer: Scorer,
    detectors: list[str],
    machine_texts: list[str],
    human_texts: list[str],
    rungs: int,
    warnings: list[str] | None = None,
    min_length_ratio: float = 0.5,
) -> dict[str, Any]:
    """Full pipeline. Returns the ``results`` payload (no envelope).

    Rung 0 is the unattacked machine corpus. Rungs 1..``rungs`` apply
    ``paraphraser`` to EACH machine window (human windows are never touched).
    A rung's paraphrase of a window is skipped (the window kept unmodified,
    a warning recorded) when it collapses below ``min_length_ratio`` of the
    original length — corrupted text is never scored. Per (detector, rung)
    the cell carries ``auc`` / ``tpr_at_fpr05`` / ``tpr_at_fpr10`` and the Δ
    from rung 0. NO aggregate scalar is computed."""
    warns: list[str] = list(warnings) if warnings else []

    # Rung 0: baseline (unattacked).
    rung0_scores = _score_rung(scorer, detectors, machine_texts, human_texts)
    rung0_cells: dict[str, dict[str, Any]] = {}
    rung0_auc: dict[str, float | None] = {}
    for det in detectors:
        c = _cell(det, rung0_scores[det]["machine"], rung0_scores[det]["human"])
        rung0_cells[det] = c
        rung0_auc[det] = c["auc"]

    per_rung: list[dict[str, Any]] = []
    for rung in range(1, max(1, int(rungs)) + 1):
        attacked: list[str] = []
        for txt in machine_texts:
            para = paraphraser.paraphrase(txt, rung=rung)
            if len(para) < min_length_ratio * max(1, len(txt)):
                warns.append(
                    f"rung {rung}: paraphrase collapsed a machine window "
                    f"(len {len(para)} < {min_length_ratio} * {len(txt)}); "
                    f"kept original, not scored as attacked"
                )
                attacked.append(txt)
            else:
                attacked.append(para)
        rung_scores = _score_rung(scorer, detectors, attacked, human_texts)
        per_detector: dict[str, Any] = {}
        for det in detectors:
            c = _cell(det, rung_scores[det]["machine"], rung_scores[det]["human"])
            base = rung0_auc[det]
            c["delta_auc"] = (
                None if (c["auc"] is None or base is None) else c["auc"] - base
            )
            base05 = rung0_cells[det].get("tpr_at_fpr05")
            cur05 = c.get("tpr_at_fpr05")
            c["delta_tpr_at_fpr05"] = (
                None if (cur05 is None or base05 is None) else cur05 - base05
            )
            per_detector[det] = c
        per_rung.append({
            "rung": rung,
            "paraphraser_label": paraphraser.label,
            "per_detector": per_detector,
        })

    results: dict[str, Any] = {
        "paraphraser_label": paraphraser.label,
        "n_machine_windows": len(machine_texts),
        "n_human_windows": len(human_texts),
        "n_rungs": max(1, int(rungs)),
        "detectors": list(detectors),
        "rung_0": rung0_auc,
        "per_rung": per_rung,
        "_warnings": warns,
    }
    return results


def build_claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A per-(detector x rung) DESCRIPTIVE table of how each detector's "
            "P(machine > human) AUC and its TPR at FPR budgets {0.05, 0.10} "
            "MOVE when the machine windows are paraphrased, plus the per-cell "
            "delta from the unattacked rung 0. Each reading carries the "
            "paraphraser label and the rung depth."
        ),
        does_not_license=(
            "Any claim that a detector is 'robust to paraphrase'; any "
            "aggregate robustness or accuracy scalar; any verdict "
            "(is_ai / is_human); any threshold, selection, or calibration "
            "derived from the attack results. A flat curve under the stdlib "
            "proxy is NOT robustness."
        ),
        additional_caveats=[
            _SADASIVAN_CEILING,
            "The stdlib proxy is honestly weaker than a neural paraphraser; "
            "a flat proxy curve is a LOWER bound on degradation, never proof "
            "of robustness.",
            "Human windows are the fixed reference class and are never "
            "paraphrased; only the adversary's own machine output is attacked.",
            "Attack-corpus texts (paraphrased machine windows) must not be "
            "used as voicewright training data or humanization targets — they "
            "are adversarially constructed, not authentic human prose.",
            "calibration_status: heuristic. No threshold is set or shipped "
            "from this experiment.",
        ],
        references=[
            "specs/33-paraphrase-robustness.md",
            "https://arxiv.org/abs/2303.11156",
            "https://arxiv.org/abs/2303.13408",
            "https://arxiv.org/abs/2401.12070",
        ],
    )


def build_envelope(results: dict[str, Any]) -> dict[str, Any]:
    warns = list(results.get("_warnings", []))
    payload = {k: v for k, v in results.items() if k != "_warnings"}
    # Corpus-level harness — no single target text; the corpus size is the
    # machine-window count carried in the payload.
    target_words = int(results.get("n_machine_windows", 0))
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=None,
        target_words=target_words,
        baseline=None,
        results=payload,
        claim_license=build_claim_license(),
        warnings=warns,
    )


# ===================== Injected-scores entry ===================== #


class _InjectedScorer:
    """M1 stub :class:`Scorer`: returns pre-supplied per-(detector, rung)
    scores keyed by a rung counter. Records nothing about the text — it is a
    pure lookup, so the orchestration/AUC math is exercised with no model."""

    def __init__(self, table: dict[str, dict[str, list[list[float]]]]) -> None:
        # table[detector] = {"machine": [rung0_scores, rung1_scores, ...],
        #                     "human":   [rung0_scores, rung1_scores, ...]}
        self._table = table
        self._rung_counter: dict[str, int] = {}

    def score(self, detector: str, texts: list[str]) -> list[float]:
        # Each (detector) is queried machine-then-human per rung, in order.
        idx = self._rung_counter.get(detector, 0)
        det = self._table[detector]
        # Even idx -> machine of rung idx//2 ; odd -> human of same rung.
        rung = idx // 2
        which = "machine" if idx % 2 == 0 else "human"
        self._rung_counter[detector] = idx + 1
        return list(det[which][rung])


def run_from_injected_scores(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the harness from an injected-scores JSON (M1, no model).

    Shape::

        {
          "paraphraser_label": "proxy_stdlib",
          "detectors": ["binoculars_v2", "surprisal_sd", ...],
          "n_rungs": 3,
          "machine_texts": ["...", ...],
          "human_texts":   ["...", ...],
          "scores": {
            "<detector>": {
              "machine": [[rung0...], [rung1...], ...],   # one list per rung 0..N
              "human":   [[rung0...], [rung1...], ...]
            }, ...
          }
        }

    The injected ``scores`` are the stage-2 detector outputs a GPU operator
    would produce in M2; here they are supplied so the orchestration and
    AUC/Δ math run in CI. The ``paraphraser`` is still applied to produce the
    attacked text (so the human-never-paraphrased / corruption guards run),
    but the injected scorer ignores the text and returns the supplied scores."""
    detectors = list(payload["detectors"])
    n_rungs = int(payload["n_rungs"])
    machine_texts = list(payload["machine_texts"])
    human_texts = list(payload["human_texts"])
    scores = payload["scores"]

    # Validate the injected shape up front so a malformed table fails loudly
    # with a clear message instead of an IndexError deep in scoring. Each
    # detector needs one machine list AND one human list per rung 0..n_rungs
    # (i.e. n_rungs + 1 lists on each side).
    expected_lists = n_rungs + 1
    for det in detectors:
        if det not in scores:
            raise ValueError(f"injected scores missing detector {det!r}")
        for side in ("machine", "human"):
            side_lists = scores[det].get(side)
            if not isinstance(side_lists, list) or len(side_lists) != expected_lists:
                raise ValueError(
                    f"injected scores[{det!r}][{side!r}] must hold "
                    f"{expected_lists} lists (one per rung 0..{n_rungs}); "
                    f"got {len(side_lists) if isinstance(side_lists, list) else type(side_lists).__name__}"
                )

    scorer = _InjectedScorer(scores)
    paraphraser = StdlibProxyParaphraser()
    return run_report(
        paraphraser=paraphraser,
        scorer=scorer,
        detectors=detectors,
        machine_texts=machine_texts,
        human_texts=human_texts,
        rungs=n_rungs,
    )


# ============================== CLI ============================== #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Detector-level AUC-degradation harness (spec 33, M1, model-free "
            "over injected scores). Reports a per-(detector x rung) table of "
            "AUC / TPR@FPR / delta — never an aggregate robustness scalar, "
            "never a verdict."
        )
    )
    parser.add_argument(
        "--injected-scores",
        type=Path,
        required=True,
        help="Path to an injected-scores JSON (see run_from_injected_scores).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON envelope.")
    parser.add_argument("--out", type=Path, default=None, help="Write JSON to PATH.")
    args = parser.parse_args(argv)

    payload = json.loads(args.injected_scores.read_text(encoding="utf-8"))
    results = run_from_injected_scores(payload)
    envelope = build_envelope(results)
    text = json.dumps(envelope, indent=2, default=str)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    if args.json or args.out is None:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
