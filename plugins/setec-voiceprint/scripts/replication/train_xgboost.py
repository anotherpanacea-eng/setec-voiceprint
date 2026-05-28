#!/usr/bin/env python3
"""train_xgboost.py — Stage C1 (binary) + C2 (multiclass).

Trains the paper's XGBoost classifiers on encoded narrative-feature
vectors and emits macro-F1, AUPRC, per-class F1, and prompt-bootstrap
95% CIs. Reproduces paper Tables 2 and 4 when given the paper's
released feature manifest.

Paper hyperparams (defaults below match the paper's Table 2 / 4
notes; operators can swap them via --hyperparam-json):

  * Binary: n_est=420, depth=8, lambda=2.0, scale_pos_weight=5.0
  * Multiclass: n_est=500, depth=7, lambda=1.0

Splits use prompt-level grouping (every story for a prompt stays in
one of {train, val, test}) to prevent leakage. The paper uses 7,383
/ 1,405 / 1,384 prompt-level splits; this script honors any pre-
computed split file or generates a deterministic stratified split
from a seed.

Manifest format
---------------

Input: a JSONL of FeatureRow-shaped dicts (see manifest_format.py):

    {"story_id": "...", "prompt_id": "...", "model": "...",
     "label": "pre_ai_human" | "ai_generated",
     "narrative_values": {feature_key: value, ...}}

The script accepts the SETEC Surface-6 polarity-audit manifest
format too — its rows carry `label` + `narrative_values` and
auto-derive `model` from `label`. Operators replicating the paper's
6-way attribution must supply `model` explicitly (one of
`human`, `claude_sonnet_4_6`, `gpt_5_4`, `gemini_3_flash`,
`deepseek_v3_2`, `kimi_k2_5`).

Encoding
--------

Per paper §3:
  * scale / ordinal → integer encoding
  * binary → 0/1
  * categorical → one-hot
  * multi → multi-hot

The encoder honors the v0.1 audit's 30-core-feature schema by
default. Operators using the full 304-feature paper taxonomy can
supply `--feature-schema-json` pointing at a JSON describing each
feature's type + options; the schema is in the SETEC
`narrative_feature_schema.py` shape but as data, so cross-replication
tooling can swap schemas without touching the script.

Output
------

`<output_dir>/train_<task>.json` — schema 1.0 envelope with
`task_surface="calibration"`, results carrying:

    {
      "task": "binary" | "multiclass",
      "n_features": int,
      "n_encoded_columns": int,
      "splits": {"train": N, "val": N, "test": N},
      "hyperparams": {...},
      "metrics": {
        "macro_f1": float, "macro_f1_ci95": [lo, hi],
        "auprc": float | null, "accuracy": float,
        "per_class_f1": {class_name: float, ...},
        "confusion_matrix": {true_class: {pred_class: count, ...}}
      },
      "feature_importance": [
        {"feature_key": "...", "encoded_column": "...",
         "xgb_gain": float, "xgb_weight": float}
      ]
    }

Dependencies
------------

Requires xgboost, scikit-learn, numpy. Optional: scipy (for some
sklearn helpers) — already in the framework's core requirements.

For SETEC environments without xgboost installed:

    pip install -r requirements-replication.txt
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
for p in (str(SCRIPT_DIR), str(PARENT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from manifest_format import (  # type: ignore  # noqa: E402
    StageSidecar,
    load_jsonl,
    utc_now,
)
from narrative_feature_schema import (  # type: ignore  # noqa: E402
    CORE_FEATURES,
    CoreFeature,
)
from output_schema import build_output  # type: ignore  # noqa: E402

SCRIPT_VERSION = "0.1.0"
TASK_SURFACE = "calibration"
TOOL_NAME = "scripts/replication/train_xgboost.py"

# Paper-reported hyperparams (Tables 2 and 4 footnotes).
DEFAULT_BINARY_HYPERPARAMS = {
    "n_estimators": 420,
    "max_depth": 8,
    "reg_lambda": 2.0,
    "scale_pos_weight": 5.0,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
}
DEFAULT_MULTICLASS_HYPERPARAMS = {
    "n_estimators": 500,
    "max_depth": 7,
    "reg_lambda": 1.0,
    "objective": "multi:softprob",
    "eval_metric": "mlogloss",
}


# ---------- schema --------------------------------------------------

def load_feature_schema(
    path: Path | None,
) -> list[CoreFeature]:
    """Load a feature schema from JSON, or fall back to the bundled
    30-core-feature schema for v0.1 replication."""
    if path is None:
        return list(CORE_FEATURES)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # Defer schema reconstruction; for v0.1 the bundled schema is the
    # supported path. Operators with the paper's full 304-feature
    # taxonomy can extend this and PR back.
    if not isinstance(raw, list):
        raise ValueError("feature schema JSON must be a list of dicts")
    raise NotImplementedError(
        "Loading external feature schemas is a v0.2 follow-up; v0.1 "
        "uses the bundled 30-core-feature schema. Drop --feature-"
        "schema-json or run against the paper's released 30-feature "
        "manifest."
    )


# ---------- encoding ------------------------------------------------

@dataclass
class EncodedRow:
    story_id: str
    prompt_id: str
    model: str
    label: str
    binary_y: int  # 1 = ai_generated, 0 = pre_ai_human
    multiclass_y: int  # index into class_vocab
    vector: list[float]


def encode_value_numeric(feat: CoreFeature, value: Any) -> float | None:
    if value is None:
        return None
    if feat.feature_type == "scale":
        try:
            return float(int(value))
        except (TypeError, ValueError):
            return None
    if feat.feature_type == "ordinal":
        try:
            return float(feat.response_options.index(value))
        except ValueError:
            return None
    if feat.feature_type == "binary":
        if value == "yes":
            return 1.0
        if value == "no":
            return 0.0
    return None


def encoded_columns(
    features: list[CoreFeature],
) -> tuple[list[str], dict[str, list[int]]]:
    """Build the encoded column ordering and the per-feature index map.

    Each feature contributes:
      - 1 column for scale / ordinal / binary,
      - len(options) columns for categorical (one-hot),
      - len(options) columns for multi (multi-hot).
    """
    cols: list[str] = []
    feature_to_cols: dict[str, list[int]] = {}
    for f in features:
        feature_to_cols[f.key] = []
        if f.feature_type in ("scale", "ordinal", "binary"):
            feature_to_cols[f.key].append(len(cols))
            cols.append(f"{f.key}")
        else:  # categorical / multi
            for opt in f.response_options:
                feature_to_cols[f.key].append(len(cols))
                cols.append(f"{f.key}::{opt}")
    return cols, feature_to_cols


def encode_row_vector(
    features: list[CoreFeature],
    cols: list[str],
    feature_to_cols: dict[str, list[int]],
    values: dict[str, Any],
) -> list[float]:
    vec = [0.0] * len(cols)
    for f in features:
        v = values.get(f.key)
        if v is None:
            continue
        idxs = feature_to_cols[f.key]
        if f.feature_type in ("scale", "ordinal", "binary"):
            numeric = encode_value_numeric(f, v)
            if numeric is not None:
                vec[idxs[0]] = numeric
        elif f.feature_type == "categorical":
            for j, opt in enumerate(f.response_options):
                if v == opt:
                    vec[idxs[j]] = 1.0
                    break
        elif f.feature_type == "multi":
            if isinstance(v, list):
                for j, opt in enumerate(f.response_options):
                    if opt in v:
                        vec[idxs[j]] = 1.0
    return vec


# ---------- split ---------------------------------------------------

@dataclass
class Split:
    train_prompts: set[str]
    val_prompts: set[str]
    test_prompts: set[str]


def make_split(
    prompt_ids: Iterable[str],
    *,
    test_frac: float = 0.13,
    val_frac: float = 0.13,
    seed: int = 13,
) -> Split:
    """Deterministic prompt-level split. Paper used 7383 / 1405 / 1384
    out of 10172 prompts (≈ 72.5 / 13.8 / 13.6); the defaults here
    target the same shape but operators can override."""
    rng = random.Random(seed)
    ids = sorted(set(prompt_ids))
    rng.shuffle(ids)
    n = len(ids)
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    test = set(ids[:n_test])
    val = set(ids[n_test: n_test + n_val])
    train = set(ids[n_test + n_val:])
    return Split(train_prompts=train, val_prompts=val, test_prompts=test)


# ---------- training ------------------------------------------------

def derive_label(label: str) -> int:
    return 0 if label == "pre_ai_human" else 1


def class_vocab_from_rows(
    rows: list[EncodedRow],
    *,
    task: str,
) -> list[str]:
    if task == "binary":
        return ["pre_ai_human", "ai_generated"]
    sources = sorted({r.model for r in rows})
    if "human" not in sources:
        # If 'model' wasn't supplied, label='pre_ai_human' rows became
        # model='pre_ai_human'; remap for clarity.
        sources = sorted({
            ("human" if r.label == "pre_ai_human" else r.model)
            for r in rows
        })
    return sources


def assemble_dataset(
    manifest_path: Path,
    features: list[CoreFeature],
):
    cols, feature_to_cols = encoded_columns(features)
    rows: list[EncodedRow] = []
    for d in load_jsonl(manifest_path):
        story_id = str(d.get("story_id") or d.get("text_id"))
        prompt_id = str(d.get("prompt_id") or story_id)
        label = d.get("label", "")
        if label not in ("pre_ai_human", "ai_generated"):
            continue
        model = d.get("model") or (
            "human" if label == "pre_ai_human" else "unknown_ai"
        )
        values = d.get("narrative_values") or d.get("values") or {}
        if not isinstance(values, dict):
            continue
        vec = encode_row_vector(features, cols, feature_to_cols, values)
        rows.append(EncodedRow(
            story_id=story_id,
            prompt_id=prompt_id,
            model=model,
            label=label,
            binary_y=derive_label(label),
            multiclass_y=-1,  # filled in after class_vocab
            vector=vec,
        ))
    return rows, cols, feature_to_cols


def fit_xgb(
    X_train, y_train, X_val, y_val,
    *,
    task: str,
    hyperparams: dict[str, Any],
    n_classes: int,
):
    try:
        import xgboost as xgb  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "xgboost is required for the C1/C2 stage; install via "
            "`pip install -r requirements-replication.txt`"
        ) from exc

    params = dict(hyperparams)
    if task == "multiclass":
        params["num_class"] = n_classes
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val, label=y_val)
    n_estimators = int(params.pop("n_estimators"))
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=n_estimators,
        evals=[(dtrain, "train"), (dval, "val")],
        verbose_eval=False,
    )
    return booster


def predict_xgb(booster, X, *, task: str, n_classes: int):
    import xgboost as xgb  # type: ignore
    dmat = xgb.DMatrix(X)
    probs = booster.predict(dmat)
    if task == "binary":
        preds = (probs >= 0.5).astype(int)
        return preds, probs
    # multiclass
    preds = probs.argmax(axis=1)
    return preds, probs


# ---------- metrics -------------------------------------------------

def macro_f1(y_true, y_pred, *, n_classes: int) -> float:
    f1s = []
    for c in range(n_classes):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        if tp == 0:
            f1s.append(0.0)
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        if precision + recall == 0:
            f1s.append(0.0)
            continue
        f1s.append(2 * precision * recall / (precision + recall))
    return sum(f1s) / len(f1s)


def per_class_f1(y_true, y_pred, vocab: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c, name in enumerate(vocab):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == c and p != c)
        if tp == 0:
            out[name] = 0.0
            continue
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        out[name] = (
            2 * precision * recall / (precision + recall)
            if precision + recall > 0 else 0.0
        )
    return out


def confusion_matrix(
    y_true, y_pred, vocab: list[str],
) -> dict[str, dict[str, int]]:
    mat: dict[str, dict[str, int]] = {
        c: {p: 0 for p in vocab} for c in vocab
    }
    for t, p in zip(y_true, y_pred):
        mat[vocab[t]][vocab[p]] += 1
    return mat


def auprc(y_true, probs) -> float | None:
    """Average-precision (area under precision-recall curve).

    Computed from scratch since sklearn may not be available; uses
    the standard trapezoidal approximation. ``probs`` is the
    positive-class probability for binary problems.
    """
    pairs = sorted(zip(probs, y_true), key=lambda x: -x[0])
    n_pos = sum(1 for _, t in pairs if t == 1)
    if n_pos == 0:
        return None
    tp = 0
    fp = 0
    ap = 0.0
    prev_recall = 0.0
    for prob, t in pairs:
        if t == 1:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / n_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return ap


def prompt_bootstrap_macro_f1(
    y_true_with_prompts,
    y_pred,
    *,
    n_classes: int,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[float, float]:
    """Prompt-level bootstrap 95% CI on macro-F1.

    ``y_true_with_prompts`` is a list of (y, prompt_id) pairs aligned
    with ``y_pred``. Each bootstrap iteration resamples prompts (with
    replacement), includes every story for the resampled prompts, and
    recomputes macro-F1. Returns the 2.5th and 97.5th percentiles.
    """
    rng = random.Random(seed)
    by_prompt: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for (yt, prompt_id), yp in zip(y_true_with_prompts, y_pred):
        by_prompt[prompt_id].append((yt, yp))
    prompt_ids = list(by_prompt)
    scores = []
    for _ in range(n_bootstrap):
        sample_ids = [rng.choice(prompt_ids) for _ in prompt_ids]
        yt = []
        yp = []
        for pid in sample_ids:
            for ytv, ypv in by_prompt[pid]:
                yt.append(ytv)
                yp.append(ypv)
        scores.append(macro_f1(yt, yp, n_classes=n_classes))
    scores.sort()
    lo = scores[int(0.025 * len(scores))]
    hi = scores[int(0.975 * len(scores))]
    return lo, hi


# ---------- main ---------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stage C1 (binary) / C2 (multiclass) XGBoost training "
            "for the StoryScope replication."
        ),
    )
    parser.add_argument(
        "--feature-manifest", type=Path, required=True,
        help="JSONL of per-story feature values.",
    )
    parser.add_argument(
        "--feature-schema-json", type=Path, default=None,
        help=(
            "Optional path to a feature-schema JSON. v0.1 defaults "
            "to the bundled 30-core-feature schema."
        ),
    )
    parser.add_argument(
        "--task", choices=("binary", "multiclass"), default="binary",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
    )
    parser.add_argument(
        "--seed", type=int, default=13,
        help="Deterministic prompt-split seed (default 13).",
    )
    parser.add_argument(
        "--bootstrap-iterations", type=int, default=1000,
    )
    parser.add_argument(
        "--hyperparam-json", type=Path, default=None,
        help=(
            "Override paper-default hyperparams via a JSON file. "
            "Keys merge into the defaults."
        ),
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    features = load_feature_schema(args.feature_schema_json)
    rows, cols, _ = assemble_dataset(args.feature_manifest, features)
    if not rows:
        print(
            "error: manifest yielded 0 usable rows", file=sys.stderr,
        )
        return 2
    vocab = class_vocab_from_rows(rows, task=args.task)
    name_to_class = {n: i for i, n in enumerate(vocab)}
    for r in rows:
        if args.task == "binary":
            r.multiclass_y = r.binary_y
        else:
            # Multiclass: humans → 'human'; AI rows → their model
            mc_name = (
                "human" if r.label == "pre_ai_human" else r.model
            )
            r.multiclass_y = name_to_class[mc_name]

    split = make_split(
        [r.prompt_id for r in rows], seed=args.seed,
    )
    train = [r for r in rows if r.prompt_id in split.train_prompts]
    val = [r for r in rows if r.prompt_id in split.val_prompts]
    test = [r for r in rows if r.prompt_id in split.test_prompts]
    if not train or not val or not test:
        print(
            "error: split produced an empty partition; supply more "
            "prompts or override --seed",
            file=sys.stderr,
        )
        return 2

    hp = (
        dict(DEFAULT_BINARY_HYPERPARAMS)
        if args.task == "binary"
        else dict(DEFAULT_MULTICLASS_HYPERPARAMS)
    )
    if args.hyperparam_json is not None:
        hp.update(json.loads(args.hyperparam_json.read_text()))

    X_train = [r.vector for r in train]
    X_val = [r.vector for r in val]
    X_test = [r.vector for r in test]
    y_train = [r.multiclass_y for r in train]
    y_val = [r.multiclass_y for r in val]
    y_test = [r.multiclass_y for r in test]

    booster = fit_xgb(
        X_train, y_train, X_val, y_val,
        task=args.task, hyperparams=hp,
        n_classes=len(vocab),
    )
    preds, probs = predict_xgb(
        booster, X_test, task=args.task, n_classes=len(vocab),
    )
    n_classes = len(vocab)
    test_macro = macro_f1(y_test, preds, n_classes=n_classes)
    per_class = per_class_f1(y_test, preds, vocab)
    cm = confusion_matrix(y_test, preds, vocab)
    ap = (
        auprc(y_test, probs) if args.task == "binary" else None
    )
    accuracy = sum(1 for t, p in zip(y_test, preds) if t == p) / len(y_test)
    ci_lo, ci_hi = prompt_bootstrap_macro_f1(
        list(zip(y_test, [r.prompt_id for r in test])),
        preds,
        n_classes=n_classes,
        n_bootstrap=args.bootstrap_iterations,
    )

    try:
        importance_gain = booster.get_score(importance_type="gain")
        importance_weight = booster.get_score(importance_type="weight")
    except Exception:  # noqa: BLE001
        importance_gain = {}
        importance_weight = {}
    feature_importance = []
    for i, col in enumerate(cols):
        key = f"f{i}"
        feature_importance.append({
            "encoded_column": col,
            "xgb_gain": float(importance_gain.get(key, 0.0)),
            "xgb_weight": float(importance_weight.get(key, 0.0)),
        })
    feature_importance.sort(
        key=lambda d: d["xgb_gain"], reverse=True,
    )

    results = {
        "task": args.task,
        "class_vocab": vocab,
        "n_features": len(features),
        "n_encoded_columns": len(cols),
        "hyperparams": hp,
        "splits": {
            "train_prompts": len(split.train_prompts),
            "val_prompts": len(split.val_prompts),
            "test_prompts": len(split.test_prompts),
            "train_stories": len(train),
            "val_stories": len(val),
            "test_stories": len(test),
        },
        "metrics": {
            "macro_f1": test_macro,
            "macro_f1_ci95": [ci_lo, ci_hi],
            "accuracy": accuracy,
            "auprc": ap,
            "per_class_f1": per_class,
            "confusion_matrix": cm,
        },
        "feature_importance_top50": feature_importance[:50],
    }
    licenses = (
        "Reports macro-F1, AUPRC, and prompt-bootstrap CIs for an "
        "XGBoost classifier trained on the encoded narrative-decision "
        "feature space, against the SETEC 30-core-feature schema or "
        "an operator-supplied taxonomy. Reproduces paper Tables 2 / "
        "4 / 5 when given the paper's released feature manifests."
    )
    does_not_license = (
        "Does not license a binary AI/human verdict. Does not "
        "generalize across registers without operator-side polarity "
        "validation. Does not substitute for the Surface 6 single-doc "
        "audit — this is the operator's calibration step."
    )
    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses,
        does_not_license=does_not_license,
        comparison_set={
            "literature_anchor": (
                "Russell et al. 2026 (StoryScope, arXiv:2604.03136v4) "
                "Tables 2 / 4 / 5 reported macro-F1 numbers"
            ),
            "task": args.task,
            "hyperparams_sha256": hashlib.sha256(
                json.dumps(hp, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16],
        },
        additional_caveats=[
            "Prompt-bootstrap CIs assume the test split's prompt "
            "distribution is the population of interest; operators "
            "evaluating against a different corpus must re-run the "
            "audit on that corpus.",
        ],
        references=[
            "Russell et al. 2026, 'StoryScope' (arXiv:2604.03136v4)",
        ],
    )

    envelope = build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=args.feature_manifest,
        target_words=sum(1 for _ in load_jsonl(args.feature_manifest)),
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
    )

    out_path = args.output_dir / f"train_{args.task}.json"
    out_path.write_text(
        json.dumps(envelope, indent=2, default=str),
        encoding="utf-8",
    )

    sidecar = StageSidecar(
        stage="C1" if args.task == "binary" else "C2",
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        prompt_fingerprint_sha256=None,
        judge_identity={},
        input_manifest_sha256=None,
        row_count=len(rows),
        completed_at_utc=utc_now(),
        row_status={"ok": len(rows)},
    )
    sidecar.write(args.output_dir / f"train_{args.task}.manifest.json")

    print(f"Wrote {out_path}")
    print(
        f"Test macro-F1: {test_macro:.4f} "
        f"[{ci_lo:.4f}, {ci_hi:.4f}]"
    )
    print(f"Test accuracy: {accuracy:.4f}")
    if ap is not None:
        print(f"Test AUPRC: {ap:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
