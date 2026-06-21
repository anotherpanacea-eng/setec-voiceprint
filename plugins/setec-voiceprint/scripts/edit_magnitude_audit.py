#!/usr/bin/env python3
"""edit_magnitude_audit.py — clean-room EditLens-style edit-magnitude estimate.

A document-level *edit-magnitude* regressor in the EditLens spirit (Thai
et al., ICLR 2026, arXiv:2510.03154): how much a text appears to have been
AI-edited, framed as a **same-corpus calibrated estimate with explicit OOD
caveats** — emphatically **not** an absolute "% AI." Implements
`specs/13-editlens-edit-magnitude.md`.

CLEAN-ROOM LICENSE DECISION (load-bearing — read before extending)
------------------------------------------------------------------
EditLens's published *weights* AND its *dataset* are licensed CC BY-NC-SA,
which is non-commercial and incompatible with this repository's GPL-3.0.
So neither is vendored, imported, downloaded, or wrapped here. What this
module implements is the *method*, which is not copyrightable: a
RoBERTa-family regressor trained with MSE against a BERTScore-style
similarity proxy computed between original/edited text pairs. The
operator supplies (a) a **non-NC** paired pre/post-edit corpus and (b) a
base model whose own license they have confirmed (roberta-large is **MIT**;
prefer Apache/MIT bases). The fine-tune itself is an operator-runtime GPU
step in the sibling trainer `scripts/calibration/train_edit_magnitude.py`
— it is NOT part of this scaffold, and this module loads no model unless
the operator points `--model` at their own calibrated checkpoint.

POSTURE
-------
* **Uncalibrated by default.** With no ``--model``, the audit emits the
  raw magnitude score path only, with **no band** and a clear "no
  calibrated model supplied" caveat. A magnitude score without a
  same-corpus calibration is not interpretable as a band.
* **Same-corpus only.** With ``--model PATH``, the audit emits a band and
  the corpus provenance recorded with that model — the band means "edit
  magnitude relative to THIS corpus," never an absolute fraction of AI
  authorship and never a cross-corpus claim.
* **OOD collapse caveat is load-bearing.** Degree estimation only works
  in-distribution; APT-Eval / Guo et al. show it collapses toward a binary
  edited/not-edited signal out-of-distribution (research brief §C). The
  claim-license states this explicitly.

The scoring/model call is **injectable**: pass ``score_fn`` (a callable
``score_fn(model, text) -> float``) and/or a ``model`` object, exactly as
``binoculars_audit``'s ``score_fn`` test-injection point works. Tests pass
a stub so no real model is ever loaded; production loads the operator's
checkpoint via the injectable ``load_model`` hook.

CLI:

    python3 scripts/edit_magnitude_audit.py TARGET [--model PATH] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "edit_magnitude"
TOOL_NAME = "edit_magnitude_audit"
SCRIPT_VERSION = "0.1.0"
SCORE_VERSION = "editlens_clean_room_v1"

# Below this many words a document-level magnitude regressor has too
# little signal to be meaningful even when calibrated. Advisory only:
# the audit still runs and surfaces the score, but adds a caveat.
LENGTH_FLOOR_WORDS = 100

# roberta-large is the EditLens-family base. Recorded here as a hint /
# default for the operator's training run; NOT downloaded by this module.
DEFAULT_BASE_MODEL = "roberta-large"
DEFAULT_BASE_MODEL_LICENSE = "MIT"

_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


# --------------------------------------------------------------------------
# Model loading (injectable; never downloads in tests)
# --------------------------------------------------------------------------


class EditMagnitudeModelError(RuntimeError):
    """Raised when a calibrated model cannot be loaded or scored.

    Typed so the CLI can report a clean error (and a nonzero exit) when an
    operator points ``--model`` at a missing / malformed checkpoint, rather
    than leaking a transformers stack trace.
    """


def default_load_model(model_path: str) -> Any:
    """Load an operator's calibrated edit-magnitude regressor.

    This is the production loader. It is the ONLY place that touches
    ``transformers`` / ``torch``, and it is reached only when the operator
    supplies ``--model PATH`` pointing at THEIR OWN calibrated checkpoint —
    never a hub download, never EditLens's NC weights. Tests never call
    this path: they inject a stub ``load_model`` / ``score_fn`` instead, so
    the suite loads no model.

    The checkpoint directory is expected to carry a SETEC PROVENANCE record
    (``provenance.json``) written by ``train_edit_magnitude.py`` describing
    the non-NC corpus the regressor was calibrated on; the audit surfaces
    that provenance so the band is never read out of its corpus context.
    """
    path = Path(model_path)
    if not path.exists():
        raise EditMagnitudeModelError(
            f"calibrated model path not found: {model_path}. Supply a "
            "directory produced by scripts/calibration/train_edit_magnitude.py "
            "(operator-runtime GPU fine-tune on a NON-NC paired corpus)."
        )
    try:
        from transformers import (  # type: ignore
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
    except ImportError as exc:
        raise EditMagnitudeModelError(
            "transformers is not installed; cannot load a calibrated "
            "edit-magnitude model. Install the surprisal tier "
            "(pip install -r requirements-surprisal.txt). For the "
            "uncalibrated score path, omit --model entirely."
        ) from exc
    try:
        tokenizer = AutoTokenizer.from_pretrained(str(path))
        # Regression head: num_labels=1, MSE objective at train time.
        model = AutoModelForSequenceClassification.from_pretrained(str(path))
        model.eval()
    except Exception as exc:  # noqa: BLE001
        raise EditMagnitudeModelError(
            f"failed to load calibrated model from {model_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return _LoadedRegressor(tokenizer=tokenizer, model=model, path=str(path))


class _LoadedRegressor:
    """Thin holder so ``default_score_fn`` can pull tokenizer + model.

    Kept module-private; the audit only ever sees it through the injectable
    ``score_fn`` boundary, so tests substitute any object they like.
    """

    def __init__(self, *, tokenizer: Any, model: Any, path: str) -> None:
        self.tokenizer = tokenizer
        self.model = model
        self.path = path


def default_score_fn(model: Any, text: str) -> float:
    """Production scoring: regress an edit-magnitude scalar for ``text``.

    Mirrors ``binoculars_audit``'s ``score_fn`` injection contract —
    ``score_fn(model, text) -> float``. Reached only via ``default_load_model``
    on a real operator checkpoint; tests inject a stub instead, so no real
    model forward pass ever runs in the suite.
    """
    try:
        import torch  # type: ignore
    except ImportError as exc:
        raise EditMagnitudeModelError(
            "torch is not installed; cannot score with a calibrated model."
        ) from exc
    tokenizer = model.tokenizer
    net = model.model
    encoded = tokenizer(
        text, return_tensors="pt", truncation=True, max_length=512,
    )
    with torch.no_grad():
        out = net(**encoded)
    # Regression head emits a single logit; squeeze to a Python float.
    return float(out.logits.squeeze().item())


def load_calibration_provenance(model: Any) -> dict[str, Any] | None:
    """Read the PROVENANCE record beside a calibrated model, if present.

    Returns the parsed ``provenance.json`` dict (corpus identity, base
    model + its license, target proxy, MSE, etc.) or ``None`` when the
    record is absent — in which case the audit still bands but flags the
    missing-provenance caveat. Defensive: any read/parse error returns
    ``None`` rather than crashing the audit.
    """
    path = getattr(model, "path", None)
    if not path:
        return None
    prov = Path(path) / "provenance.json"
    if not prov.exists():
        return None
    try:
        return json.loads(prov.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# Banding (same-corpus only; never when uncalibrated)
# --------------------------------------------------------------------------


def _band(
    score: float | None,
    *,
    calibrated: bool,
    low: float | None,
    high: float | None,
) -> str | None:
    """Map a magnitude score to a same-corpus band, or ``None``.

    Returns ``None`` (i.e. NO band) whenever the audit is uncalibrated — a
    magnitude score without a same-corpus calibration is not interpretable
    as a band, and emitting one would imply an absolute reading the spec
    forbids. When calibrated but the model's provenance carried no band
    cut-points, returns ``"uncalibrated_band"`` so the reader sees the
    score is in-corpus but the cut-points weren't recorded.
    """
    if not calibrated or score is None:
        return None
    if low is None or high is None:
        return "uncalibrated_band"
    if score < low:
        return "low_edit_magnitude"
    if score > high:
        return "high_edit_magnitude"
    return "moderate_edit_magnitude"


def audit(
    target_text: str,
    *,
    model: Any = None,
    score_fn: Callable[[Any, str], float] | None = None,
    calibration_provenance: dict[str, Any] | None = None,
    band_low: float | None = None,
    band_high: float | None = None,
) -> dict[str, Any]:
    """Run the edit-magnitude audit. Returns the ``results`` payload.

    Parameters mirror ``binoculars_audit.audit``'s injection style:

    * ``model`` — an opaque calibrated-model object (or ``None`` for the
      uncalibrated path). Production passes the result of
      ``default_load_model``; tests pass a stub or ``None``.
    * ``score_fn`` — ``score_fn(model, text) -> float``. The single
      model-touching call. Production passes ``None`` → ``default_score_fn``
      is used (only when ``model`` is also supplied). Tests inject a stub
      so no real forward pass runs.
    * ``calibration_provenance`` — the non-NC corpus provenance recorded
      with the model. Surfaced under ``corpus_provenance`` when calibrated.
    * ``band_low`` / ``band_high`` — same-corpus band cut-points (from the
      model's provenance). Only consulted when calibrated.

    Uncalibrated (``model is None``): emits the score path only, ``band``
    is ``None``, and the "no calibrated model supplied" caveat is added.
    Score itself is ``None`` when uncalibrated — there is nothing to score
    against without an operator-calibrated regressor.
    """
    caveats: list[str] = []
    word_count = count_words(target_text)
    calibrated = model is not None

    score: float | None = None
    if calibrated:
        fn = score_fn if score_fn is not None else default_score_fn
        score = float(fn(model, target_text))
    else:
        caveats.append(
            "no_calibrated_model_supplied: emitting the magnitude score "
            "PATH only, with NO band. A calibrated regressor (operator "
            "fine-tune on a non-NC paired corpus) is required to produce "
            "an interpretable, same-corpus band."
        )

    if word_count < LENGTH_FLOOR_WORDS:
        caveats.append(
            f"target_below_length_floor: {word_count} words < "
            f"{LENGTH_FLOOR_WORDS}; document-level magnitude is unstable "
            "on very short inputs."
        )

    band = _band(score, calibrated=calibrated, low=band_low, high=band_high)

    corpus_provenance: dict[str, Any] | None = None
    if calibrated:
        if calibration_provenance is not None:
            corpus_provenance = dict(calibration_provenance)
        else:
            caveats.append(
                "calibrated_model_without_provenance_record: the band is "
                "relative to an unrecorded corpus. Re-run "
                "train_edit_magnitude.py so a provenance.json is written "
                "beside the checkpoint."
            )
        if band == "uncalibrated_band":
            caveats.append(
                "calibrated_model_without_band_cutpoints: score is "
                "in-corpus but no band cut-points were recorded in the "
                "model's provenance, so only the moderate/low/high label "
                "could not be assigned."
            )

    # The OOD-collapse caveat is load-bearing on every run, calibrated or
    # not — it is the central honesty constraint of this surface.
    caveats.append(
        "ood_collapse: degree estimation works in-distribution and "
        "collapses toward a binary edited/not-edited signal "
        "out-of-distribution (APT-Eval / Guo et al.). Read the band only "
        "within the corpus the model was calibrated on."
    )

    return {
        "score": score,
        "score_version": SCORE_VERSION,
        "calibrated": calibrated,
        "band": band,
        "band_cutpoints": {"low": band_low, "high": band_high},
        "corpus_provenance": corpus_provenance,
        "base_model_hint": {
            "model": DEFAULT_BASE_MODEL,
            "license": DEFAULT_BASE_MODEL_LICENSE,
            "note": (
                "Clean-room base-model hint only; not downloaded by this "
                "audit. The operator confirms the base-model license at "
                "calibration time."
            ),
        },
        "target_words": word_count,
        "caveats": caveats,
    }


# --------------------------------------------------------------------------
# Claim license
# --------------------------------------------------------------------------


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    calibrated = results.get("calibrated", False)
    corpus = results.get("corpus_provenance") or {}
    comparison_set: dict[str, Any] = {
        "mode": (
            "same_corpus_calibrated" if calibrated else "uncalibrated_score_only"
        ),
        "score_version": results.get("score_version"),
    }
    if calibrated and corpus:
        comparison_set["calibration_corpus"] = corpus.get(
            "corpus_name", corpus.get("corpus_id", "unrecorded"),
        )

    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "an edit-magnitude estimate relative to the specific corpus the "
            "model was calibrated on. With a calibrated model: a same-corpus "
            "magnitude band (low / moderate / high edit magnitude) plus the "
            "corpus provenance. Without one: the raw magnitude score path "
            "only, with no band."
        ),
        does_not_license=(
            "an absolute \"% AI\" / AI-dosage claim — the score is NOT a "
            "fraction of the text that is AI-authored. Does not license "
            "cross-corpus generalization (a band calibrated on one corpus "
            "does not transfer). Does not license any per-sentence or "
            "per-span localization of edits (this is a document-level "
            "magnitude regressor, not a span detector). OOD-collapse caveat: "
            "degree estimation works in-distribution and collapses toward a "
            "binary edited/not-edited signal out-of-distribution (APT-Eval / "
            "Guo et al.), so the band is only interpretable within the "
            "calibration corpus."
        ),
        comparison_set=comparison_set,
        additional_caveats=list(results.get("caveats", [])),
        references=[
            "EditLens (Thai et al., ICLR 2026, arXiv:2510.03154) — METHOD "
            "ONLY; clean-room reimplementation. EditLens weights + dataset "
            "are CC BY-NC-SA (non-commercial), so neither is vendored, "
            "imported, or downloaded here.",
            "Base model roberta-large is MIT-licensed; the operator confirms "
            "their chosen base-model license at calibration time.",
            "specs/13-editlens-edit-magnitude.md",
        ],
    )


def compose_envelope(
    *,
    target_path: Path | str | None,
    results: dict[str, Any],
    available: bool = True,
) -> dict[str, Any]:
    caveats = list(results.get("caveats", []))
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=int(results.get("target_words", 0)),
        baseline=None,
        results=results,
        claim_license=_claim_license(results),
        available=available,
        warnings=caveats,
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    lines: list[str] = []
    lines.append("# Edit-magnitude audit (clean-room EditLens-style)")
    lines.append("")
    lines.append(f"- **Target:** `{target.get('path')}` ({target.get('words')} words)")
    lines.append(f"- **Score version:** `{results.get('score_version')}`")
    lines.append(f"- **Calibrated:** {results.get('calibrated')}")
    lines.append("")

    lines.append("## Magnitude")
    lines.append("")
    score = results.get("score")
    score_text = f"{score:.4f}" if isinstance(score, (int, float)) else "(uncalibrated — no score)"
    lines.append(f"- **Edit-magnitude score:** {score_text}")
    band = results.get("band")
    lines.append(f"- **Same-corpus band:** {band if band is not None else '(none — uncalibrated)'}")
    if results.get("calibrated"):
        cp = results.get("corpus_provenance") or {}
        lines.append(f"- **Calibration corpus:** {cp.get('corpus_name', cp.get('corpus_id', '(unrecorded)'))}")
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
    lines.append((envelope.get("claim_license_rendered") or "").rstrip())
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", help="Path to target text file (UTF-8).")
    p.add_argument(
        "--model",
        default=None,
        help=(
            "Path to an OPERATOR-CALIBRATED edit-magnitude model directory "
            "(produced by train_edit_magnitude.py on a non-NC paired "
            "corpus). Omit for the uncalibrated score-only path (no band). "
            "Never downloads; never loads EditLens NC weights."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Write output to this path instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"error: target file not found at {target_path}\n")
        return 2
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        sys.stderr.write(f"error: target not valid UTF-8: {exc}\n")
        return 2

    model: Any = None
    calibration_provenance: dict[str, Any] | None = None
    band_low: float | None = None
    band_high: float | None = None
    if args.model:
        try:
            model = default_load_model(args.model)
        except EditMagnitudeModelError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 3
        calibration_provenance = load_calibration_provenance(model)
        if calibration_provenance:
            band = calibration_provenance.get("band_cutpoints") or {}
            band_low = band.get("low")
            band_high = band.get("high")

    try:
        results = audit(
            target_text,
            model=model,
            calibration_provenance=calibration_provenance,
            band_low=band_low,
            band_high=band_high,
        )
    except EditMagnitudeModelError as exc:
        sys.stderr.write(f"error: scoring failed: {exc}\n")
        return 3

    envelope = compose_envelope(target_path=target_path, results=results)

    text_out = (
        json.dumps(envelope, indent=2, default=str)
        if args.json
        else render_markdown(envelope)
    )
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote output to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
