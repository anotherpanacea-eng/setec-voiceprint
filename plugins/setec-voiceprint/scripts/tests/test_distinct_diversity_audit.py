#!/usr/bin/env python3
"""Tests for distinct_diversity_audit.py (NoveltyBench distinct-cluster diversity, M1, arXiv:2504.05228).

M1 is fully CI-testable (stdlib lexical-near-dup lens — word-shingle Jaccard + single-link union-find;
no model, no numpy, deterministic). Covers the spec's findings-folded acceptance set (AC-1..15):
deterministic output, envelope shape, the collapsed / diverse / partial-collapse partition pins, the
threshold-monotone structural property, the jaccard/shingle unit pins, claim-license-present +
refuses-verdict, the recursive no-verdict / no-band / no-single-score field guard, the never-selects
guard, the set-floor + length-floor abstention, graceful degradation, lens-label honesty, and the
P2-folded bounds (distinct_ratio in (0,1] from the surface arithmetic — NOT R4; utility-weighted >= 1).

Anti-Goodhart held-out (AC-15): every fixture used here to PIN a cluster count is a synthetic in-test
string and is HELD OUT of any corpus later used to calibrate the M2 model-dedup lens — no fixture leaks
into a calibration set. M1 ships uncalibrated (status: heuristic), so this is a forward guard for M2.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import distinct_diversity_audit as dd  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# Twelve genuinely distinct ~19-word prose lines (above the 15-word per-text floor, >= the default set
# floor of 10) — a DIVERSE pool. Held out from any M2 calibration corpus (AC-15).
_DIVERSE = [
    "The ocean swallowed the last light of the day while gulls wheeled overhead crying loudly into the salt wind.",
    "Quantum entanglement defies our intuition about locality and separability in modern physics and continues to puzzle researchers everywhere today.",
    "She baked three loaves of sourdough bread before the heavy storm knocked the power out across the whole sleepy village.",
    "Tax policy reform requires carefully balancing equity against efficiency across many competing constituencies and entrenched political interests over time.",
    "The dragon coiled around the jagged mountain peak and exhaled a long plume of green fire into the cold morning.",
    "Investors fled toward safe havens as the bond market loudly signaled a coming recession sometime in the months ahead.",
    "My grandmother always kept her cherished recipes in a small tin box rusted shut by decades of kitchen damp.",
    "The algorithm sorts the enormous array in logarithmic time using a clever recursive divide and conquer approach throughout.",
    "Rain hammered the rattling tin roof all through the night and the swollen river rose far past its muddy banks.",
    "Parliament debated the contentious measure for many hours before adjourning without any clear resolution late that gray evening.",
    "He tuned the battered old guitar very slowly listening closely for the faint buzz of one loose metal string.",
    "The telescope captured the faint ancient light from a distant galaxy billions of years deep in the cosmic past.",
]

# A collapsed pool: twelve copies of one >= 15-word text (all near-dup-identical -> one cluster).
_COLLAPSED_TEXT = (
    "The committee reviewed the proposal carefully and decided to approve the new budget for the "
    "upcoming fiscal year after much deliberation and several long meetings about the matter."
)
_COLLAPSED = [_COLLAPSED_TEXT] * 12

_FORBIDDEN_KEYS = {"is_ai", "is_human", "verdict", "label", "same_author", "score"}


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = dd.main(argv)
    return rc, json.loads(out.getvalue())


def _manifest(tmp_path, texts, name="pool.jsonl"):
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as f:
        for i, t in enumerate(texts):
            f.write(json.dumps({"id": f"x{i}", "text": t}) + "\n")
    return p


def _walk_keys(obj):
    """Yield every dict key anywhere in a nested structure (the recursive no-verdict walk)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


def _pool(texts):
    return [(f"d{i}", t) for i, t in enumerate(texts)]


# --- AC-7: jaccard / shingle unit pins ---------------------------------------

def test_jaccard_self_one_empty_zero_no_nan():
    s = dd.word_shingles(_DIVERSE[0], 5)
    assert dd.jaccard(s, s) == 1.0
    assert dd.jaccard(s, frozenset()) == 0.0
    assert dd.jaccard(frozenset(), frozenset()) == 0.0  # NOT NaN (no 0/0)


def test_word_shingles_count_and_fallback():
    toks = dd.word_shingles  # alias for readability
    # len(tokens) >= k: exactly max(0, len(tokens) - k + 1) shingles.
    text = "alpha beta gamma delta epsilon zeta eta theta"  # 8 tokens
    assert len(toks(text, 5)) == 8 - 5 + 1
    assert len(toks(text, 3)) == 8 - 3 + 1
    # under-k text -> singleton fallback (one shingle), never empty.
    assert len(toks("two words", 5)) == 1
    assert len(toks("", 5)) == 1


# --- AC-1: deterministic output ----------------------------------------------

def test_deterministic_output():
    a = dd.audit_pool(_pool(_DIVERSE))
    b = dd.audit_pool(_pool(_DIVERSE))
    assert a == b


# --- AC-3 / AC-4 / AC-5: partition pins --------------------------------------

def test_collapsed_pool_one_cluster():
    r = dd.audit_pool(_pool(_COLLAPSED))
    n = len(_COLLAPSED)
    assert r["n_clusters"] == 1
    assert r["distinct_ratio"] == pytest.approx(1.0 / n, abs=1e-6)  # results rounded to 6 dp
    assert r["cluster_sizes"] == [n]
    assert len(r["representatives"]) == 1
    assert r["representatives"][0]["size"] == n


def test_diverse_pool_all_singletons():
    r = dd.audit_pool(_pool(_DIVERSE))
    n = len(_DIVERSE)
    assert r["n_clusters"] == n
    assert r["distinct_ratio"] == 1.0
    assert r["cluster_sizes"] == [1] * n
    assert len(r["representatives"]) == n


def test_partial_collapse_pin():
    # 9 distinct singletons + one 3-member near-dup block -> 10 clusters, a size-3 cluster present.
    block = [_COLLAPSED_TEXT] * 3
    pool = _pool(_DIVERSE[:9] + block)
    r = dd.audit_pool(pool, min_set=10)
    assert r["n_clusters"] == 10
    assert 3 in r["cluster_sizes"]
    assert sorted(r["cluster_sizes"], reverse=True) == [3] + [1] * 9


# --- AC-6: threshold-monotone (structural, no magic number) ------------------

def test_threshold_monotone_n_clusters():
    # A pool with a graded-similarity block so intermediate thresholds actually move the partition.
    base = "the quick brown fox jumps over the lazy dog near the old river bank every single morning"
    near = "the quick brown fox jumps over the lazy dog near the old river bank every other morning too"
    pool = _pool(_DIVERSE[:8] + [base, near, base + " indeed", near + " again"])
    strict = dd.audit_pool(pool, near_dup_threshold=0.9, min_set=10)["n_clusters"]
    mid = dd.audit_pool(pool, near_dup_threshold=0.5, min_set=10)["n_clusters"]
    loose = dd.audit_pool(pool, near_dup_threshold=0.1, min_set=10)["n_clusters"]
    # raising the threshold (stricter) never DECREASES n_clusters; lowering never INCREASES it.
    assert strict >= mid >= loose


# --- AC-2: envelope shape ----------------------------------------------------

def test_envelope_shape(tmp_path):
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "set_level_diversity"
    assert env["task_surface"] in VALID_TASK_SURFACES
    assert env["tool"] == "distinct_diversity_audit"
    r = env["results"]
    expected = {"n_texts", "n_clusters", "lens", "cluster_size_distribution", "cluster_sizes",
                "distinct_ratio", "utility_weighted_distinctness", "representatives", "assumptions"}
    assert expected.issubset(set(r))
    assert set(r["cluster_size_distribution"]) == {"n", "mean", "sd", "min", "p10", "p50", "p90"}


def test_dir_mode(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    for i, t in enumerate(_DIVERSE):
        (d / f"p{i}.txt").write_text(t, encoding="utf-8")
    rc, env = _envelope(["--dir", str(d), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["results"]["n_texts"] == 12


# --- P2-folded bounds: distinct_ratio (0,1] from arithmetic, NOT R4 ----------

def test_distinct_ratio_in_open_zero_one():
    for texts in (_DIVERSE, _COLLAPSED, _DIVERSE[:9] + [_COLLAPSED_TEXT] * 3):
        r = dd.audit_pool(_pool(texts), min_set=10)
        assert 0.0 < r["distinct_ratio"] <= 1.0


def test_utility_weighted_distinctness_bounds():
    # discount 0.0 -> pure distinct-count == n_clusters; always >= 1 on a non-empty pool.
    r0 = dd.audit_pool(_pool(_COLLAPSED), utility_discount=0.0)
    assert r0["utility_weighted_distinctness"] == r0["n_clusters"] >= 1
    # discount > 0 credits redundant members: on a collapsed pool it EXCEEDS n_clusters (not in [0,1]).
    rd = dd.audit_pool(_pool(_COLLAPSED), utility_discount=0.5)
    assert rd["utility_weighted_distinctness"] > rd["n_clusters"]


# --- AC-8: claim license present + refuses verdict ---------------------------

def test_claim_license_present_and_refuses_verdict(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    cl = env["claim_license"]
    assert cl is not None
    dnl = json.dumps(cl).lower()
    assert "ai/human" in dnl or "ai / human" in dnl
    assert "not a model defect" in dnl                  # the confound caveat
    assert "representative is positional" in dnl or "representative is\npositional" in dnl \
        or ("representative is" in dnl and "positional" in dnl)  # the positional-not-best caveat
    assert "no verdict" in dnl
    # AC-8: no --verdict flag exists.
    with pytest.raises(SystemExit):
        dd.main(["--verdict"])


# --- AC-9: no-verdict / no-band / no-single-score field guard (recursive) ----

def test_no_verdict_field_guard_recursive(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    keys = set(_walk_keys(env["results"]))
    assert _FORBIDDEN_KEYS.isdisjoint(keys), f"forbidden verdict key in results: {keys & _FORBIDDEN_KEYS}"
    # no band; no single "diversity score" (the explicit anti-pattern).
    assert "band" not in keys
    assert "provisional_band" not in keys
    assert "diversity_score" not in keys
    assert "diversity" not in keys


# --- AC-10: never-selects guard ----------------------------------------------

def test_never_selects_representatives_are_positional():
    r = dd.audit_pool(_pool(_DIVERSE[:9] + [_COLLAPSED_TEXT] * 3), min_set=10)
    for rep in r["representatives"]:
        assert "member_ids" in rep
        # the representative is the EARLIEST member by input order (member_ids ascending -> first).
        assert rep["representative_id"] == rep["member_ids"][0]
        # no winner/rank/score/selected key on a representative.
        for forbidden in ("best", "rank", "score", "selected"):
            assert forbidden not in rep


# --- AC-11 / AC-12 / AC-13: abstention + graceful degradation ----------------

def test_set_floor_abstention(tmp_path):
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE[:4])), "--json"])
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_length_floor_drop_then_set_floor(tmp_path):
    stubs = ["too short here friend"] * 12  # below the 15-word floor
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, stubs)), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_empty_pool_bad_input_no_div_by_zero(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    rc, env = _envelope(["--manifest", str(p), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_malformed_manifest_skips_bad_rows(tmp_path):
    p = tmp_path / "m.jsonl"
    lines = [json.dumps({"id": f"x{i}", "text": t}) for i, t in enumerate(_DIVERSE)]
    lines.insert(0, "not json at all")
    lines.insert(1, "[1, 2, 3]")  # valid JSON, non-object
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc, env = _envelope(["--manifest", str(p), "--json"])
    assert env["available"] is True and env["results"]["n_texts"] == 12


def test_needs_input(tmp_path):
    rc, env = _envelope(["--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


# --- AC-14: lens-label honesty -----------------------------------------------

def test_lens_label_honesty(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    r = env["results"]
    assert r["lens"] == "lexical-near-dup"
    pl = r["assumptions"]["paper_lens_incomparable"].lower()
    assert "2504.05228" in pl
    assert "learned deduper" in pl and "not comparable" in pl
    # no_band assumption surfaced (band is explicitly absent).
    nb = r["assumptions"]["no_band"].lower()
    assert "no" in nb and "band" in nb


def test_model_dedup_lens_fails_loud_missing_dependency(tmp_path):
    # M2 seam: --lens model-dedup lazy-imports; absent the dep -> fail-loud missing_dependency,
    # NEVER a silent fallback to the lexical lens.
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)),
                         "--lens", "model-dedup", "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    assert "silently falling back" in env["reason"].lower()
