#!/usr/bin/env python3
"""train_edit_magnitude.py — clean-room EditLens-style regressor trainer.

Fine-tunes a RoBERTa-family **regressor** with MSE against a similarity-proxy
target (a BERTScore-family score) computed between original/edited text pairs
from an **operator-supplied NON-NC paired corpus**. Implements the calibration
half of `specs/13-editlens-edit-magnitude.md`.

CLEAN-ROOM LICENSE DECISION (load-bearing)
------------------------------------------
EditLens's weights AND dataset are CC BY-NC-SA (non-commercial), which is
incompatible with this repository's GPL-3.0. This trainer therefore vendors
NEITHER. It reimplements the *method* only — which is not copyrightable:

  * a RoBERTa-family sequence regressor (num_labels=1),
  * trained with MSE,
  * against a similarity-proxy target (BERTScore-family) between each
    original/edited pair,
  * on a corpus the operator brings and warrants is NON-NC.

The base model (roberta-large is **MIT**; prefer Apache/MIT) is the
operator's choice, and the operator confirms its license at run time. The
trainer refuses to proceed without an explicit ``--accept-noncommercial-free``
attestation that the supplied corpus is not NC-encumbered.

OPERATOR-RUNTIME GPU STEP — NOT part of the scaffold build/test
---------------------------------------------------------------
A real fine-tune is a GPU job (RoBERTa-large is ~355M params). The trainer
and its model + target-proxy are **injectable** so the SMOKE TEST runs on a
tiny synthetic pair set WITHOUT downloading or fine-tuning a real model: the
test passes a stub trainer + a deterministic stub target-proxy. Nothing in
the build or test path loads transformers or torch.

PROVENANCE
----------
Every calibrated model gets a ``provenance.json`` written beside the
checkpoint, recording the corpus identity, the base model + its license,
the target proxy, the achieved MSE, and the band cut-points — so the
inference audit (`edit_magnitude_audit.py`) can surface the corpus context
and the band is never read out of distribution.

Usage (operator GPU run):

    python3 scripts/calibration/train_edit_magnitude.py \\
        --pairs path/to/nonnc_pairs.jsonl \\
        --base-model roberta-large \\
        --base-model-license MIT \\
        --corpus-name my_editorial_pairs_2026 \\
        --out path/to/edit_magnitude_model \\
        --accept-noncommercial-free

Pairs file (JSONL), one object per line:

    {"original": "...", "edited": "...", "id": "optional"}
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS = SCRIPT_DIR.parent
for _p in (str(SCRIPT_DIR), str(SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCRIPT_VERSION = "0.1.0"
TOOL_NAME = "scripts/calibration/train_edit_magnitude.py"
SCORE_VERSION = "editlens_clean_room_v1"
DEFAULT_BASE_MODEL = "roberta-large"
DEFAULT_BASE_MODEL_LICENSE = "MIT"


class TrainEditMagnitudeError(RuntimeError):
    """Raised on corpus / attestation / training failures."""


# --------------------------------------------------------------------------
# Pair loading
# --------------------------------------------------------------------------


def load_pairs(path: Path) -> list[dict[str, str]]:
    """Load original/edited pairs from a JSONL file.

    Each line must be an object with non-empty ``original`` and ``edited``
    string fields. Blank lines are skipped; malformed lines raise.
    """
    pairs: list[dict[str, str]] = []
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise TrainEditMagnitudeError(
                f"{path}:{lineno}: not valid JSON: {exc}"
            ) from exc
        orig = obj.get("original")
        edited = obj.get("edited")
        if not isinstance(orig, str) or not isinstance(edited, str) or not orig or not edited:
            raise TrainEditMagnitudeError(
                f"{path}:{lineno}: each pair needs non-empty string "
                "'original' and 'edited' fields."
            )
        pairs.append({
            "id": str(obj.get("id", lineno)),
            "original": orig,
            "edited": edited,
        })
    if not pairs:
        raise TrainEditMagnitudeError(f"{path}: no usable pairs found.")
    return pairs


# --------------------------------------------------------------------------
# Target proxy (injectable; BERTScore-family in production, stub in tests)
# --------------------------------------------------------------------------


def default_target_proxy(original: str, edited: str) -> float:
    """Compute the regression TARGET: an edit-magnitude proxy in [0, 1].

    In production this is a BERTScore-family similarity between the
    original and edited text, converted to a magnitude (``1 - similarity``):
    semantically-preserving light edits → low magnitude; heavy rewrites →
    high magnitude. BERTScore needs transformers + torch, so this function
    is the ONLY model-touching path in the trainer and is reached only in a
    real GPU run. The smoke test injects a deterministic stub proxy instead,
    so it loads no model.
    """
    try:
        from bert_score import score as bertscore_score  # type: ignore
    except ImportError as exc:
        raise TrainEditMagnitudeError(
            "bert_score is not installed; cannot compute the BERTScore-"
            "family target proxy. Install it for a real calibration run, "
            "or inject a target_proxy for offline/smoke use."
        ) from exc
    # F1 BERTScore between edited (candidate) and original (reference).
    _p, _r, f1 = bertscore_score([edited], [original], lang="en", verbose=False)
    similarity = float(f1.mean().item())
    # Magnitude = 1 - similarity, clamped to [0, 1].
    return max(0.0, min(1.0, 1.0 - similarity))


# --------------------------------------------------------------------------
# Trainer (injectable; HF Trainer in production, stub in tests)
# --------------------------------------------------------------------------


def default_train_model(
    *,
    base_model: str,
    examples: list[dict[str, Any]],
    out_dir: Path,
    epochs: int,
    seed: int,
) -> dict[str, Any]:
    """Fine-tune a RoBERTa-family regressor with MSE. OPERATOR GPU STEP.

    Production path: loads ``base_model`` (operator's choice; roberta-large
    is MIT), attaches a ``num_labels=1`` regression head, and trains with
    MSE against the per-example ``target``. Reached only in a real GPU run;
    the smoke test injects a stub ``train_model`` so this never executes in
    the suite. Returns a small metrics dict (at minimum ``{"mse": float,
    "n_train": int}``) and writes the checkpoint to ``out_dir``.
    """
    try:
        import numpy as np  # type: ignore  # noqa: F401
        import torch  # type: ignore  # noqa: F401
        from transformers import (  # type: ignore
            AutoModelForSequenceClassification,
            AutoTokenizer,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:
        raise TrainEditMagnitudeError(
            "transformers + torch are required for a real fine-tune. "
            "This is an operator-runtime GPU step. For offline use inject "
            "a train_model callable (the smoke test does exactly this)."
        ) from exc
    # NOTE: the concrete HF training loop is intentionally left as the
    # operator-runtime path; wiring TrainingArguments/Trainer here would
    # require a GPU + a downloaded base model, which is explicitly out of
    # scope for this scaffold. Operators extending this should build a
    # regression dataset from ``examples`` ([{"text", "target"}...]),
    # tokenize with ``AutoTokenizer.from_pretrained(base_model)``, load
    # ``AutoModelForSequenceClassification.from_pretrained(base_model,
    # num_labels=1, problem_type="regression")``, and train with MSE.
    raise TrainEditMagnitudeError(
        "default_train_model is the operator-runtime GPU path and is not "
        "wired for offline execution; supply a calibrated checkpoint or "
        "inject a train_model callable."
    )


# --------------------------------------------------------------------------
# Orchestration (pure; deterministic; injectable boundaries)
# --------------------------------------------------------------------------


def build_examples(
    pairs: list[dict[str, str]],
    *,
    target_proxy: Callable[[str, str], float],
) -> list[dict[str, Any]]:
    """Turn pairs into training examples carrying the MSE target.

    Each example is ``{"id", "text", "target"}`` where ``text`` is the
    edited document (the regressor scores a single document at inference
    time) and ``target`` is the similarity-proxy edit magnitude. Pure
    given a pure ``target_proxy`` — the smoke test's stub proxy makes this
    fully deterministic.
    """
    examples: list[dict[str, Any]] = []
    for pair in pairs:
        target = float(target_proxy(pair["original"], pair["edited"]))
        examples.append({
            "id": pair["id"],
            "text": pair["edited"],
            "target": target,
        })
    return examples


def write_provenance(
    out_dir: Path,
    *,
    corpus_name: str,
    base_model: str,
    base_model_license: str,
    n_pairs: int,
    metrics: dict[str, Any],
    band_cutpoints: dict[str, float | None],
    target_proxy_name: str,
) -> dict[str, Any]:
    """Write ``provenance.json`` beside the checkpoint and return it.

    This is the record the inference audit reads to surface corpus context
    and band cut-points, so the same-corpus band is never read OOD.
    """
    provenance = {
        "tool": TOOL_NAME,
        "tool_version": SCRIPT_VERSION,
        "score_version": SCORE_VERSION,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "method": (
            "clean-room EditLens-style: RoBERTa-family regressor, MSE vs. a "
            "BERTScore-family similarity proxy between original/edited pairs. "
            "No EditLens NC weights or data used."
        ),
        "corpus_name": corpus_name,
        "corpus_noncommercial_free_attested": True,
        "base_model": base_model,
        "base_model_license": base_model_license,
        "target_proxy": target_proxy_name,
        "n_pairs": n_pairs,
        "metrics": dict(metrics),
        "band_cutpoints": dict(band_cutpoints),
        "ood_caveat": (
            "Degree estimation works in-distribution and collapses toward a "
            "binary edited/not-edited signal out-of-distribution; the band is "
            "interpretable only within this corpus."
        ),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2), encoding="utf-8",
    )
    return provenance


def train(
    pairs: list[dict[str, str]],
    *,
    out_dir: Path,
    base_model: str = DEFAULT_BASE_MODEL,
    base_model_license: str = DEFAULT_BASE_MODEL_LICENSE,
    corpus_name: str = "unrecorded",
    accept_noncommercial_free: bool,
    epochs: int = 3,
    seed: int = 0,
    band_cutpoints: dict[str, float | None] | None = None,
    target_proxy: Callable[[str, str], float] | None = None,
    train_model: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the clean-room calibration. Returns the provenance dict.

    Injectable boundaries (mirrors the audit's ``score_fn`` pattern):

    * ``target_proxy`` — ``(original, edited) -> float`` magnitude target.
      ``None`` → ``default_target_proxy`` (BERTScore, GPU/CPU model load).
      The smoke test injects a deterministic stub.
    * ``train_model`` — the fine-tune callable. ``None`` →
      ``default_train_model`` (operator GPU step). The smoke test injects a
      stub that writes a fake checkpoint and returns a metrics dict.

    Refuses to run unless ``accept_noncommercial_free`` is True — the
    operator's attestation that the supplied corpus is NOT NC-encumbered,
    which is the license prerequisite the spec gates the build on.
    """
    if not accept_noncommercial_free:
        raise TrainEditMagnitudeError(
            "refusing to train: pass accept_noncommercial_free=True "
            "(CLI: --accept-noncommercial-free) to attest the supplied "
            "paired corpus is NOT CC-NC / non-commercial-encumbered. "
            "EditLens's own corpus is CC BY-NC-SA and must never be used "
            "here."
        )
    proxy = target_proxy if target_proxy is not None else default_target_proxy
    trainer = train_model if train_model is not None else default_train_model
    proxy_name = getattr(proxy, "__name__", "custom_target_proxy")

    examples = build_examples(pairs, target_proxy=proxy)

    metrics = trainer(
        base_model=base_model,
        examples=examples,
        out_dir=out_dir,
        epochs=epochs,
        seed=seed,
    )
    if "mse" not in metrics:
        raise TrainEditMagnitudeError(
            "train_model must return a metrics dict containing 'mse'."
        )

    provenance = write_provenance(
        out_dir,
        corpus_name=corpus_name,
        base_model=base_model,
        base_model_license=base_model_license,
        n_pairs=len(pairs),
        metrics=metrics,
        band_cutpoints=band_cutpoints or {"low": None, "high": None},
        target_proxy_name=proxy_name,
    )
    return provenance


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pairs", required=True, help="JSONL of {original, edited} pairs (NON-NC corpus).")
    p.add_argument("--out", required=True, help="Output checkpoint directory.")
    p.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help=f"RoBERTa-family base (default {DEFAULT_BASE_MODEL}; MIT).")
    p.add_argument("--base-model-license", default=DEFAULT_BASE_MODEL_LICENSE, help="License of the base model (operator-confirmed).")
    p.add_argument("--corpus-name", default="unrecorded", help="Identifier for the calibration corpus (recorded in provenance).")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--band-low", type=float, default=None, help="Same-corpus band low cut-point (recorded in provenance).")
    p.add_argument("--band-high", type=float, default=None, help="Same-corpus band high cut-point (recorded in provenance).")
    p.add_argument(
        "--accept-noncommercial-free",
        action="store_true",
        help=(
            "Attest the supplied paired corpus is NOT CC-NC / "
            "non-commercial-encumbered. Required; the trainer refuses to "
            "run without it. EditLens's own corpus is CC BY-NC-SA and must "
            "never be used here."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    pairs_path = Path(args.pairs).expanduser()
    if not pairs_path.is_file():
        sys.stderr.write(f"error: pairs file not found at {pairs_path}\n")
        return 2
    try:
        pairs = load_pairs(pairs_path)
        provenance = train(
            pairs,
            out_dir=Path(args.out).expanduser(),
            base_model=args.base_model,
            base_model_license=args.base_model_license,
            corpus_name=args.corpus_name,
            accept_noncommercial_free=args.accept_noncommercial_free,
            epochs=args.epochs,
            seed=args.seed,
            band_cutpoints={"low": args.band_low, "high": args.band_high},
        )
    except TrainEditMagnitudeError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 3
    sys.stdout.write(
        f"Wrote calibrated model + provenance to {args.out}\n"
        f"  corpus={provenance['corpus_name']} "
        f"n_pairs={provenance['n_pairs']} "
        f"mse={provenance['metrics'].get('mse')}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
