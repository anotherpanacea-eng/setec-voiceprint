#!/usr/bin/env python3
"""Compute mirror discrimination metrics for the GPT-5 elicitation kit (v0.3, neutral filenames).

Run from the kit's root directory:
    cd gpt5_elicitation_kit
    python3 metrics/compute_metrics.py

Reports both K=4 and K=3 (C1-excluded) aggregates for the Dilexit control,
to handle the case where the C1 prediction is procedurally anomalous.
"""
import re
import json
import statistics
from pathlib import Path

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import spacy
    import numpy as np
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install scikit-learn spacy numpy")
    print("And: python -m spacy download en_core_web_sm")
    raise SystemExit(1)

nlp = spacy.load("en_core_web_sm")

KIT = Path(__file__).resolve().parent.parent
PRED_DIR = KIT / "predictions_paste_here"
TARGET_DIR = KIT / "targets_DO_NOT_READ_until_predictions_saved"

MAG_WINDOWS = ["W1", "W2", "W3", "W4"]
DIL_WINDOWS = ["C1", "C2", "C3", "C4"]


def tokenize(t): return re.findall(r"\b[a-z]+\b", t.lower())


def jaccard(a, b):
    sa, sb = set(tokenize(a)), set(tokenize(b))
    return len(sa & sb) / max(len(sa | sb), 1)


def tfidf_cos(a, b):
    v = TfidfVectorizer().fit([a, b])
    m = v.transform([a, b])
    return float(cosine_similarity(m[0], m[1])[0, 0])


def pos_bigrams(t):
    doc = nlp(t)
    tags = [tok.pos_ for tok in doc if not tok.is_space]
    return [(tags[i], tags[i+1]) for i in range(len(tags) - 1)]


def pos_bigram_cos(a, b):
    ba, bb = pos_bigrams(a), pos_bigrams(b)
    vocab = sorted(set(ba) | set(bb))
    if not vocab:
        return 0.0
    idx = {v: i for i, v in enumerate(vocab)}
    va = np.zeros(len(vocab))
    vb = np.zeros(len(vocab))
    for g in ba: va[idx[g]] += 1
    for g in bb: vb[idx[g]] += 1
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float((va @ vb) / (na * nb))


def run_window_set(label_set):
    results = {}
    for w in label_set:
        pred_path = PRED_DIR / f"{w}_gpt5_prediction.txt"
        tgt_path = TARGET_DIR / f"{w}_target.txt"
        if not pred_path.exists() or not tgt_path.exists():
            print(f"  MISSING: {pred_path.name} or {tgt_path.name}")
            continue
        pred = pred_path.read_text(encoding="utf-8").strip()
        tgt = tgt_path.read_text(encoding="utf-8").strip()
        results[w] = {
            "word_jaccard": round(jaccard(pred, tgt), 4),
            "tfidf_cosine": round(tfidf_cos(pred, tgt), 4),
            "pos_bigram_cosine": round(pos_bigram_cos(pred, tgt), 4),
            "pred_word_count": len(pred.split()),
            "target_word_count": len(tgt.split()),
        }
    return results


def k_agg(results, windows_to_use=None):
    if windows_to_use is None:
        windows_to_use = list(results.keys())
    use = {w: results[w] for w in windows_to_use if w in results}
    if not use:
        return None
    return {
        "n": len(use),
        "word_jaccard": statistics.mean(r["word_jaccard"] for r in use.values()),
        "tfidf_cosine": statistics.mean(r["tfidf_cosine"] for r in use.values()),
        "pos_bigram_cosine": statistics.mean(r["pos_bigram_cosine"] for r in use.values()),
    }


def print_table(label, results):
    print(f"\n=== {label} ===")
    print(f"{'Window':<10} {'Jaccard':>10} {'TFIDF':>10} {'POS-bg':>10} {'Pred wc':>10} {'Tgt wc':>10}")
    print("-" * 70)
    for w, r in results.items():
        print(f"  {w:<8} {r['word_jaccard']:>10.4f} {r['tfidf_cosine']:>10.4f} "
              f"{r['pos_bigram_cosine']:>10.4f} {r['pred_word_count']:>10} {r['target_word_count']:>10}")


def main():
    print(f"Kit root: {KIT}")
    mag = run_window_set(MAG_WINDOWS)
    dil = run_window_set(DIL_WINDOWS)
    print_table("MAGNIFICA HUMANITAS (GPT-5 prediction vs ACTUAL)", mag)
    print_table("DILEXIT NOS (GPT-5 prediction vs ACTUAL)", dil)

    mag_k4 = k_agg(mag)
    dil_k4 = k_agg(dil)
    dil_k3 = k_agg(dil, ["C2", "C3", "C4"])  # exclude C1 if anomalous

    CLAUDE_BLIND_MAG = {"word_jaccard": 0.1421, "tfidf_cosine": 0.5932, "pos_bigram_cosine": 0.8311}
    CLAUDE_BLIND_DIL = {"word_jaccard": 0.1247, "tfidf_cosine": 0.4363, "pos_bigram_cosine": 0.7268}

    print("\n=== K=4 AGGREGATES ===")
    print(f"{'Metric':<20} {'GPT5 Mag K=4':>16} {'GPT5 Dil K=4':>16} {'GPT5 Dil K=3*':>16} {'Claude Mag':>14} {'Claude Dil':>14}")
    print("-" * 100)
    if mag_k4 and dil_k4 and dil_k3:
        for m in ["word_jaccard", "tfidf_cosine", "pos_bigram_cosine"]:
            print(f"{m:<20} {mag_k4[m]:>16.4f} {dil_k4[m]:>16.4f} {dil_k3[m]:>16.4f} "
                  f"{CLAUDE_BLIND_MAG[m]:>14.4f} {CLAUDE_BLIND_DIL[m]:>14.4f}")

    print("\n* K=3 excludes C1 if procedurally anomalous (per FILENAME_KEY notes).")

    if mag_k4 and dil_k4 and dil_k3:
        print("\n=== Discrimination signals ===")
        print(f"GPT-5 within-model Magnifica-vs-Dilexit (K=4 Dilexit): {mag_k4['tfidf_cosine'] - dil_k4['tfidf_cosine']:+.4f}")
        print(f"GPT-5 within-model Magnifica-vs-Dilexit (K=3 Dilexit): {mag_k4['tfidf_cosine'] - dil_k3['tfidf_cosine']:+.4f}")
        print(f"Claude within-model Magnifica-vs-Dilexit:              {CLAUDE_BLIND_MAG['tfidf_cosine'] - CLAUDE_BLIND_DIL['tfidf_cosine']:+.4f}")
        print(f"Cross-family on Magnifica (Claude minus GPT-5):        {CLAUDE_BLIND_MAG['tfidf_cosine'] - mag_k4['tfidf_cosine']:+.4f}")

    out = {
        "magnifica_gpt5_per_window": mag,
        "dilexit_gpt5_per_window": dil,
        "magnifica_gpt5_K4_aggregate": mag_k4,
        "dilexit_gpt5_K4_aggregate": dil_k4,
        "dilexit_gpt5_K3_aggregate_excluding_C1": dil_k3,
        "claude_blind_reference_magnifica": CLAUDE_BLIND_MAG,
        "claude_blind_reference_dilexit": CLAUDE_BLIND_DIL,
    }
    (KIT / "gpt5_results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nResults written to: {KIT / 'gpt5_results.json'}")


if __name__ == "__main__":
    main()
