#!/usr/bin/env python3
"""period_lm_contrast.py — period-LM vs modern-LM likelihood contrast per passage (VP-RS6).

Scores each passage under two causal LMs trained on matched recipes but different
eras of text, and reports the contrast in bits per UTF-8 byte:

    contrast_bpb = bpb(period LM) - bpb(modern twin)

Negative means the period model finds the passage easier than its modern twin does:
the passage reads as more like the period's text. Bits per byte (not per token)
keeps the two models comparable when their tokenizers differ. The set is summarised
by mean and median contrast with a seeded bootstrap 95% interval of the mean; given
``--reference`` real period passages, the same summary is reported for them and the
candidate-minus-reference gap of means gets its own bootstrap interval.

Model pairs (``--pair``):

* ``talkie`` (default): talkie-lm 1930 base vs talkie-web base, 13B twins trained on
  pre-1931 text and on modern web text (both Apache-2.0). Both ship custom model
  code, so loading needs ``--trust-remote-code``; pass ``--period-model`` /
  ``--modern-model`` to point at transformers conversions.
* ``typewriter``: typewriter-ai 1913 7B base. Its repo declares no license; it
  loads for private research use with a warning in the output. It has no modern
  twin: pass ``--modern-model``.

Rows that carry ``period_bpb`` and ``modern_bpb`` run model-free, and
``--score-cache`` appends per-batch scores so an interrupted GPU run resumes.
Descriptive: a likelihood contrast is not a register verdict, and both LMs reward
topic and vocabulary as well as syntax.

TASK_SURFACE: period_register.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.contract.claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "period_register"
TOOL_NAME = "period_lm_contrast"
SCRIPT_VERSION = "1.0"

PAIRS: dict[str, tuple[str, str | None]] = {
    "talkie": ("talkie-lm/talkie-1930-13b-base", "talkie-lm/talkie-web-13b-base"),
    "typewriter": ("typewriter-ai/typewriter-1913-7B-base", None),
}
# Repos with no declared license: loaded for private research use, with a warning.
UNDECLARED_LICENSE_PREFIXES = ("typewriter-ai/",)
DEFAULT_MAX_TOKENS = 1024
DEFAULT_N_BOOT = 2000
DEFAULT_SEED = 1913

BpbScorer = Callable[[Sequence[str]], Sequence[float]]


class ContrastError(ValueError):
    """Bad input reported as a bad_input envelope."""


class PolicyRefusal(RuntimeError):
    """A model repo that ships custom code the operator has not allowed."""


def _bootstrap_mean_ci(values: list[float], n_boot: int, rng: random.Random) -> list[float] | None:
    if len(values) < 2:
        return None
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n_boot))
    return [means[int(0.025 * (n_boot - 1))], means[int(0.975 * (n_boot - 1))]]


def _bootstrap_gap_ci(a: list[float], b: list[float], n_boot: int, rng: random.Random) -> list[float] | None:
    if len(a) < 2 or len(b) < 2:
        return None
    gaps = sorted(statistics.fmean(rng.choices(a, k=len(a))) - statistics.fmean(rng.choices(b, k=len(b)))
                  for _ in range(n_boot))
    return [gaps[int(0.025 * (n_boot - 1))], gaps[int(0.975 * (n_boot - 1))]]


def _summary(contrasts: list[float], n_boot: int, rng: random.Random) -> dict[str, Any]:
    return {
        "n": len(contrasts),
        "mean_contrast_bpb": statistics.fmean(contrasts),
        "median_contrast_bpb": statistics.median(contrasts),
        "mean_contrast_bpb_interval_95": _bootstrap_mean_ci(contrasts, n_boot, rng),
        "share_period_easier": sum(c < 0 for c in contrasts) / len(contrasts),
    }


SCORING_POLICY = "bos-chunked-bpb-v1"


def scoring_context(max_tokens: int) -> str:
    """Identity of the scoring policy a cached bpb depends on (chunk size resets the context)."""
    return f"{SCORING_POLICY}:max_tokens={max_tokens}"


def _sha(model_id: str, context: str, text: str) -> str:
    return hashlib.sha256(f"{model_id}\x00{context}\x00{text}".encode("utf-8")).hexdigest()


def _load_cache(path: Path | None) -> dict[str, float]:
    cache: dict[str, float] = {}
    if path and path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    row = json.loads(line)
                    cache[row["key"]] = row["bpb"]
    return cache


def cache_misses(rows, field, model_id, context, cache) -> int:
    """Rows that still need ``field`` from ``model_id`` after precomputed values and the cache."""
    return sum(1 for r in rows if r.get(field) is None and _sha(model_id, context, r["text"]) not in cache)


def _score_column(rows, field, model_id, context, scorer, cache, cache_path, batch_size, progress):
    todo = [r for r in rows if r.get(field) is None and _sha(model_id, context, r["text"]) not in cache]
    for r in rows:
        if r.get(field) is None and _sha(model_id, context, r["text"]) in cache:
            r[field] = cache[_sha(model_id, context, r["text"])]
    if todo and scorer is None:
        raise LookupError(f"{len(todo)} passage(s) lack {field} and {model_id} could not be loaded")
    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        values = scorer([r["text"] for r in batch])
        lines = []
        for r, v in zip(batch, values):
            r[field] = float(v)
            key = _sha(model_id, context, r["text"])
            cache[key] = r[field]
            lines.append(json.dumps({"key": key, "model": model_id, "context": context, "bpb": r[field]}) + "\n")
        if cache_path:
            with cache_path.open("a", encoding="utf-8") as fh:
                fh.writelines(lines)
        if progress:
            print(f"[{model_id}] {min(start + batch_size, len(todo))}/{len(todo)}", file=sys.stderr)


def run_contrast(
    passages: list[dict[str, Any]],
    reference: list[dict[str, Any]] | None = None,
    *,
    period_model: str,
    modern_model: str,
    period_scorer: BpbScorer | None = None,
    modern_scorer: BpbScorer | None = None,
    cache_path: Path | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    batch_size: int = 4,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    include_per_passage: bool = False,
    progress: bool = False,
) -> dict[str, Any]:
    if not passages:
        raise ContrastError("no passages to score")
    cache = _load_cache(cache_path)
    context = scoring_context(max_tokens)
    sets = [passages] + ([reference] if reference else [])
    for rows in sets:
        for field, model_id, scorer in (("period_bpb", period_model, period_scorer),
                                        ("modern_bpb", modern_model, modern_scorer)):
            _score_column(rows, field, model_id, context, scorer, cache, cache_path, batch_size, progress)
    rng = random.Random(seed)
    cand = [r["period_bpb"] - r["modern_bpb"] for r in passages]
    out: dict[str, Any] = {
        "period_model": period_model, "modern_model": modern_model, "scoring_context": context,
        "candidates": {**_summary(cand, n_boot, rng),
                       "mean_period_bpb": statistics.fmean(r["period_bpb"] for r in passages),
                       "mean_modern_bpb": statistics.fmean(r["modern_bpb"] for r in passages)},
        "calibration_status": "provisional",
        "assumptions": {
            "contrast": "bits per UTF-8 byte under the period LM minus under the modern twin; "
                        "negative = the period LM finds the passage easier",
            "interval": f"seeded bootstrap ({n_boot} resamples) of the mean",
        },
    }
    if reference:
        ref = [r["period_bpb"] - r["modern_bpb"] for r in reference]
        out["reference"] = _summary(ref, n_boot, rng)
        out["candidate_minus_reference_mean_bpb"] = statistics.fmean(cand) - statistics.fmean(ref)
        out["candidate_minus_reference_interval_95"] = _bootstrap_gap_ci(cand, ref, n_boot, rng)
    if include_per_passage:
        out["per_passage"] = [{"id": r["id"], "period_bpb": r["period_bpb"], "modern_bpb": r["modern_bpb"],
                               "contrast_bpb": r["period_bpb"] - r["modern_bpb"]} for r in passages]
    return {"period_lm_contrast": out}


def license_warning(model_id: str) -> str | None:
    if model_id.startswith(UNDECLARED_LICENSE_PREFIXES):
        return (f"{model_id} declares no license; loaded for private research use only, "
                "check its terms before any other use")
    return None


class _CausalLMBpb:
    """Lazy bits-per-byte scorer over a transformers causal LM."""

    def __init__(self, model_id: str, *, device: str | None, max_tokens: int, trust_remote_code: bool):
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

        self._torch = torch
        self.max_tokens = max_tokens
        self.tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=trust_remote_code)
        dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if dev.startswith("cuda") else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            model_id, torch_dtype=dtype, trust_remote_code=trust_remote_code).to(dev).eval()
        self.device = dev

    def _bits(self, text: str) -> float:
        torch = self._torch
        ids = self.tok(text, add_special_tokens=False)["input_ids"]
        bos = self.tok.bos_token_id if self.tok.bos_token_id is not None else self.tok.eos_token_id
        total = 0.0
        step = self.max_tokens - 1
        for start in range(0, len(ids), step):
            chunk = [bos] + ids[start:start + step]
            x = torch.tensor([chunk], device=self.device)
            with torch.no_grad():
                logits = self.model(x).logits.float()
            logp = torch.log_softmax(logits[0, :-1], dim=-1)
            total -= logp.gather(1, x[0, 1:].unsqueeze(1)).sum().item()
        return total / math.log(2)

    def __call__(self, texts):
        return [self._bits(t) / max(1, len(t.encode("utf-8"))) for t in texts]

    def close(self) -> None:
        """Free the weights so the next model loads into the same memory."""
        self.model = None
        if self.device.startswith("cuda"):
            self._torch.cuda.empty_cache()


def _load_scorer(model_id, *, device, max_tokens, trust_remote_code) -> BpbScorer | None:
    if os.environ.get("setec_hermetic_backend") == "stub":
        return None
    try:
        return _CausalLMBpb(model_id, device=device, max_tokens=max_tokens,
                            trust_remote_code=trust_remote_code)
    except ImportError:
        return None
    except ValueError as exc:
        if "trust_remote_code" in str(exc):
            raise PolicyRefusal(f"{model_id} ships custom model code; pass --trust-remote-code "
                                "to run it, or point at a transformers conversion") from exc
        return None
    except (OSError, RuntimeError):
        return None


def _load_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row.get("text"), str) or not row["text"].strip():
                raise ContrastError(f"{label} line {lineno}: missing 'text'")
            row.setdefault("id", str(lineno))
            rows.append(row)
    return rows


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "Per-passage bits per byte under a period-trained LM and its modern-trained twin, "
            "their contrast, and set-level means with bootstrap intervals, as one read of how "
            "much more period-like than modern a set of passages is to these two models."
        ),
        "does_not_license": (
            "This does NOT date a passage or certify a register: both LMs reward topic, names "
            "and vocabulary as well as syntax, and the contrast reflects their training corpora. "
            "Not a training reward. No cut-off is calibrated; calibration is PROVISIONAL."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    def error(reason: str, category: str) -> dict[str, Any]:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=args.passages, reason=reason, reason_category=category)

    default_period, default_modern = PAIRS[args.pair]
    period_model = args.period_model or default_period
    modern_model = args.modern_model or default_modern
    if not modern_model:
        return error(f"pair {args.pair!r} has no modern twin; pass --modern-model", "bad_input")
    try:
        passages = _load_jsonl(Path(args.passages), label="passages")
        reference = _load_jsonl(Path(args.reference), label="reference") if args.reference else None
    except (OSError, json.JSONDecodeError, ContrastError) as exc:
        return error(f"cannot read input: {exc}", "bad_input")
    rows = passages + (reference or [])
    warnings = [w for w in map(license_warning, (period_model, modern_model)) if w]
    try:
        cache_path = Path(args.score_cache) if args.score_cache else None
        cache = _load_cache(cache_path)
        context = scoring_context(args.max_tokens)
        # One model resident at a time: fill a column, free its model, then load the twin.
        for field, model_id in (("period_bpb", period_model), ("modern_bpb", modern_model)):
            if cache_misses(rows, field, model_id, context, cache) == 0:
                continue
            scorer = _load_scorer(model_id, device=args.device, max_tokens=args.max_tokens,
                                  trust_remote_code=args.trust_remote_code)
            try:
                for rs in (passages, reference or []):
                    _score_column(rs, field, model_id, context, scorer, cache, cache_path,
                                  args.batch_size, True)
            finally:
                if hasattr(scorer, "close"):
                    scorer.close()
        results = run_contrast(
            passages, reference, period_model=period_model, modern_model=modern_model,
            cache_path=cache_path, max_tokens=args.max_tokens,
            batch_size=args.batch_size, n_boot=args.n_boot, seed=args.seed,
            include_per_passage=args.per_passage, progress=True)
    except PolicyRefusal as exc:
        return error(str(exc), "policy_refused")
    except LookupError as exc:
        return error(f"{exc}; install torch and transformers (requirements-surprisal.txt) or "
                     "supply precomputed period_bpb/modern_bpb", "missing_dependency")
    except ContrastError as exc:
        return error(str(exc), "bad_input")
    words = lambda rs: sum(len(r["text"].split()) for r in rs)  # noqa: E731
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION, target_path=args.passages,
        target_words=words(passages),
        baseline={"n_files": len(reference or []), "words": words(reference or [])},
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
        validate_bounds=True,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--passages", required=True, help="JSONL {id, text[, period_bpb, modern_bpb]}")
    ap.add_argument("--reference", help="optional JSONL of real period passages, same shape")
    ap.add_argument("--pair", choices=sorted(PAIRS), default="talkie")
    ap.add_argument("--period-model", help="override the period LM (HF id)")
    ap.add_argument("--modern-model", help="override the modern twin (HF id)")
    ap.add_argument("--trust-remote-code", action="store_true",
                    help="allow model repos that ship custom code (the talkie repos do)")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--device")
    ap.add_argument("--score-cache", help="JSONL cache of per-model bpb, appended per batch (resumes)")
    ap.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--per-passage", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
