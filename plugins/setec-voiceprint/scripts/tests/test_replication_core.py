#!/usr/bin/env python3
"""Tests for the pure-Python parts of the replication pipeline.

Covers:
  * manifest_format JSONL IO + sidecar round-trips,
  * train_xgboost encoding (numeric + one-hot + multi-hot),
  * train_xgboost macro_f1 / per_class_f1 / confusion_matrix / auprc
    math against hand-computed reference values,
  * feature_dedup single-linkage clustering + cluster representative
    selection.

Does NOT exercise the XGBoost training itself (that requires
xgboost + scikit-learn at install time; covered by an operator-side
smoke test against the paper's released manifest).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPL = ROOT / "replication"
for p in (str(ROOT), str(REPL)):
    if p not in sys.path:
        sys.path.insert(0, p)

import feature_dedup as fd  # type: ignore  # noqa: E402
import manifest_format as mf  # type: ignore  # noqa: E402
import train_xgboost as tx  # type: ignore  # noqa: E402
from narrative_feature_schema import CORE_FEATURES  # type: ignore  # noqa: E402


# ---------- manifest format ---------------------------------------

def test_jsonl_round_trip():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.jsonl"
        rows = [
            mf.PromptRow(
                prompt_id="p1", source_story_id="s1",
                prompt_text="hello", target_words=5000,
            ),
            mf.PromptRow(
                prompt_id="p2", source_story_id="s2",
                prompt_text="world", target_words=5000,
            ),
        ]
        n = mf.write_jsonl(path, rows)
        assert n == 2
        loaded = list(mf.load_jsonl(path))
        assert loaded[0]["prompt_id"] == "p1"
        assert loaded[1]["prompt_text"] == "world"


def test_sidecar_round_trip():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sidecar.json"
        s = mf.StageSidecar(
            stage="A1",
            tool="t", version="0.1.0",
            prompt_fingerprint_sha256="deadbeef",
            judge_identity={"kind": "mock"},
            input_manifest_sha256=None,
            row_count=42,
            completed_at_utc=mf.utc_now(),
            row_status={"ok": 42},
        )
        s.write(path)
        loaded = mf.StageSidecar.from_dict(
            json.loads(path.read_text()),
        )
        assert loaded.stage == "A1"
        assert loaded.row_count == 42


def test_sha256_path_stable():
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.jsonl"
        b = Path(td) / "b.jsonl"
        a.write_text("hello\nworld\n")
        b.write_text("hello\nworld\n")
        assert mf.sha256_path(a) == mf.sha256_path(b)
        b.write_text("hello\nWORLD\n")
        assert mf.sha256_path(a) != mf.sha256_path(b)


# ---------- encoding ----------------------------------------------

def test_encoded_columns_count_matches_schema():
    feats = list(CORE_FEATURES)
    cols, fmap = tx.encoded_columns(feats)
    # Scale/ordinal/binary contribute 1 column; categorical/multi
    # contribute len(options). Spot-check the totals match the per-
    # feature contribution sum.
    expected = 0
    for f in feats:
        if f.feature_type in ("scale", "ordinal", "binary"):
            expected += 1
        else:
            expected += len(f.response_options)
    assert len(cols) == expected, (
        f"encoded column count drift: got {len(cols)}, expected "
        f"{expected}"
    )
    # Every feature appears in the map.
    assert set(fmap) == {f.key for f in feats}


def test_encode_row_vector_scale_and_categorical():
    feats = list(CORE_FEATURES)
    cols, fmap = tx.encoded_columns(feats)
    # Build values: scale → "3"; categorical → first option;
    # multi → [first option]; ordinal → 2nd option; binary → "yes".
    values = {}
    for f in feats:
        if f.feature_type == "scale":
            values[f.key] = "3"
        elif f.feature_type == "binary":
            values[f.key] = "yes"
        elif f.feature_type == "ordinal":
            values[f.key] = f.response_options[1]
        elif f.feature_type == "categorical":
            values[f.key] = f.response_options[0]
        elif f.feature_type == "multi":
            values[f.key] = [f.response_options[0]]
    vec = tx.encode_row_vector(feats, cols, fmap, values)
    assert len(vec) == len(cols)
    # Categorical features one-hot at index 0.
    for f in feats:
        if f.feature_type == "categorical":
            assert vec[fmap[f.key][0]] == 1.0
            for j in fmap[f.key][1:]:
                assert vec[j] == 0.0
        if f.feature_type == "scale":
            assert vec[fmap[f.key][0]] == 3.0
        if f.feature_type == "ordinal":
            assert vec[fmap[f.key][0]] == 1.0
        if f.feature_type == "binary":
            assert vec[fmap[f.key][0]] == 1.0


def test_encode_handles_missing_values():
    feats = list(CORE_FEATURES)
    cols, fmap = tx.encoded_columns(feats)
    vec = tx.encode_row_vector(feats, cols, fmap, {})
    assert all(v == 0.0 for v in vec)


# ---------- metrics math -------------------------------------------

def test_macro_f1_perfect_classification():
    y = [0, 1, 0, 1]
    assert tx.macro_f1(y, y, n_classes=2) == 1.0


def test_macro_f1_completely_wrong():
    y_true = [0, 1, 0, 1]
    y_pred = [1, 0, 1, 0]
    assert tx.macro_f1(y_true, y_pred, n_classes=2) == 0.0


def test_per_class_f1_handles_zero_class_predictions():
    """When no predictions land on a class, F1 for that class is 0,
    not NaN."""
    y_true = [0, 1, 1]
    y_pred = [0, 0, 0]
    f1 = tx.per_class_f1(y_true, y_pred, ["a", "b"])
    assert f1["a"] > 0
    assert f1["b"] == 0.0


def test_confusion_matrix_counts():
    y_true = [0, 0, 1, 1]
    y_pred = [0, 1, 1, 1]
    cm = tx.confusion_matrix(y_true, y_pred, ["a", "b"])
    assert cm["a"]["a"] == 1
    assert cm["a"]["b"] == 1
    assert cm["b"]["a"] == 0
    assert cm["b"]["b"] == 2


def test_auprc_perfect():
    """Perfect ranking: AP = 1.0."""
    y_true = [1, 1, 0, 0]
    probs = [0.9, 0.8, 0.2, 0.1]
    assert tx.auprc(y_true, probs) == 1.0


def test_auprc_no_positives_returns_none():
    assert tx.auprc([0, 0, 0], [0.1, 0.2, 0.3]) is None


def test_prompt_bootstrap_zero_variance_perfect_classifier():
    """A perfect classifier should have a tight (1.0, 1.0) CI."""
    y_with_prompts = [(0, "p1"), (1, "p1"), (0, "p2"), (1, "p2")]
    y_pred = [0, 1, 0, 1]
    lo, hi = tx.prompt_bootstrap_macro_f1(
        y_with_prompts, y_pred, n_classes=2, n_bootstrap=200,
    )
    assert lo == 1.0
    assert hi == 1.0


# ---------- splits -------------------------------------------------

def test_make_split_no_overlap_and_deterministic():
    ids = [f"p{i}" for i in range(100)]
    a = tx.make_split(ids, seed=7)
    b = tx.make_split(ids, seed=7)
    assert a.train_prompts == b.train_prompts
    assert a.train_prompts & a.val_prompts == set()
    assert a.train_prompts & a.test_prompts == set()
    assert a.val_prompts & a.test_prompts == set()


# ---------- feature dedup -----------------------------------------

def test_single_linkage_merges_above_threshold():
    sims = {
        (0, 1): 0.9,  # merge
        (0, 2): 0.4,  # below threshold
        (1, 2): 0.3,
    }
    clusters = fd.single_linkage_clusters(sims, n=3, threshold=0.85)
    assert sorted(sorted(c) for c in clusters) == [[0, 1], [2]]


def test_single_linkage_chains_through_high_pairs():
    """Single linkage chains: (0,1)=0.9 and (1,2)=0.9 → {0,1,2}."""
    sims = {
        (0, 1): 0.9,
        (1, 2): 0.9,
        (0, 2): 0.4,
    }
    clusters = fd.single_linkage_clusters(sims, n=3, threshold=0.85)
    assert sorted(sorted(c) for c in clusters) == [[0, 1, 2]]


def test_cluster_representative_picks_closest_to_centroid():
    """Three nearly-aligned vectors; the one closest to the centroid
    wins."""
    emb = [
        fd.normalize([1.0, 0.0]),
        fd.normalize([1.0, 0.01]),
        fd.normalize([1.0, 0.5]),
    ]
    rep = fd.cluster_representative([0, 1, 2], emb)
    # The middle vector (0,1) is closest to the centroid.
    assert rep == 1


def test_cosine_normalize_unit_length():
    v = fd.normalize([3.0, 4.0])
    norm = (v[0] ** 2 + v[1] ** 2) ** 0.5
    assert abs(norm - 1.0) < 1e-9


def test_feature_dedup_no_embed_smoke():
    """End-to-end smoke test of the --no-embed path."""
    with tempfile.TemporaryDirectory() as td:
        cand_path = Path(td) / "cands.jsonl"
        with cand_path.open("w") as fh:
            fh.write(json.dumps({
                "feature_id": "a", "name": "Thematic explicitness",
                "question": "How explicit is the theme",
                "options": ["1", "2", "3"], "dimension": "SIT",
            }) + "\n")
            fh.write(json.dumps({
                "feature_id": "b",
                "name": "Thematic explicitness rating",
                "question": "How explicit is the theme rating",
                "options": ["1", "2", "3"], "dimension": "SIT",
            }) + "\n")
            fh.write(json.dumps({
                "feature_id": "c", "name": "Time jumps",
                "question": "Does time jump",
                "options": ["yes", "no"], "dimension": "TMP",
            }) + "\n")
        out_path = Path(td) / "out.jsonl"
        rc = fd.main([
            "--candidates-jsonl", str(cand_path),
            "--out-jsonl", str(out_path),
            "--no-embed",
            "--threshold", "0.3",
        ])
        assert rc == 0
        rows = list(mf.load_jsonl(out_path))
        # 'a' and 'b' overlap heavily in tokens → cluster together at
        # 0.3; 'c' is a singleton.
        ids = sorted(r["feature_id"] for r in rows)
        assert "c" in ids
        # Either 'a' or 'b' becomes the cluster representative; the
        # other appears as a member.
        clusters = [r for r in rows if len(r["cluster_members"]) > 1]
        assert len(clusters) >= 1
        merged = clusters[0]["cluster_members"]
        assert "a" in merged and "b" in merged


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
