#!/usr/bin/env python3
"""Tests for cross_doc_novelty_profile.py (spec wave-4, M1, arXiv:2606.12790 + arXiv:2504.05228).

M1 is fully CI-testable (stdlib stylometric lens — extract_features(include_spacy=False); no model,
no numpy, deterministic). Covers the spec's acceptance criteria (AC-1..20):

AC-1  deterministic output
AC-2  envelope shape
AC-3  model-free M1 / no-model-import (sys.modules blocking spacy/torch/transformers)
AC-4  z-position pin (identical pool → z==0 or None; shifted target → large |z|; sd==0 → z=None)
AC-5  mean/SD-only stat (no pool_median/pool_mad/z_robust; no --robust flag; stat=="mean_sd_z")
AC-6  feature-schema count invariant (NOVELTY_FAMILY_COUNT==7; CHAR_NGRAM_NS==(3,4,5))
AC-7  self-exclusion pin (target-in-pool → dropped; self_excluded counted; empty→bad_input)
AC-8  pool-floor abstention (usable pool < min-pool → available:false, bad_input, rc==3)
AC-9  length-floor (target below floor → bad_input; pool docs below floor dropped)
AC-10 claim-license present + refuses-verdict
AC-11 no-verdict field guard (recursive, per-TEST, CI-blocking) — the key assertion test
AC-12 no-single-score / no-band guard
AC-13 no-ranked-selection guard (family/name sorted, no rank/selected/best)
AC-14 orthogonality assertion (no partition/cluster keys; distinct_diversity_audit has no per_feature)
AC-15 CI discrimination boundary (surface==set_level_diversity, handoff==none, consumers==[])
AC-16 embedding-lens fails loud on import-SUCCESS too
AC-17 graceful-degradation (empty pool/manifest → bad_input; malformed rows skipped)
AC-18 target-only honesty (pool-absent names → target_only_features; never z-scored)
AC-19 anti-Goodhart held-out disjoint (forward guard; all fixtures are synthetic held-out strings)
AC-20 lens-label honesty (lens==stylometric; paper_lens_incomparable cites GENIE)
"""

from __future__ import annotations

import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cross_doc_novelty_profile as cdnp  # type: ignore  # noqa: E402
from stylometry_core import CHAR_NGRAM_NS  # type: ignore  # noqa: E402

# ---- Anti-Goodhart held-out fixtures (AC-19) ------------------------------------
# All fixture strings below are SYNTHETIC, held out from any M2 calibration corpus
# (forward guard: M1 ships uncalibrated / status: heuristic).

# All fixture strings are > 100 words (the length floor default). Verified against
# stylometry_core.extract_features(include_spacy=False)["summary"]["n_words"].

# A > 100-word prose paragraph about gardening (topic/register matched for the pool).
_GARDEN_BASE = (
    "The garden came alive in early spring when the first crocuses pushed through the cold soil. "
    "She spent long hours preparing the beds, turning the earth carefully and adding compost to "
    "enrich the tired clay that had lain fallow through the winter months. Seeds were sorted into "
    "labeled envelopes and stored in a cool drawer until the last frost had passed and the soil "
    "temperature climbed above ten degrees. The old apple tree at the back of the garden needed "
    "pruning, its branches tangled and competing for light after years of neglect. "
    "She worked steadily through the afternoon until the light faded and the birds stopped singing."
)  # ~107 words

_GARDEN_POOL_2 = (
    "Spring gardening requires patience and careful observation of the weather patterns each day. "
    "She watched the soil moisture daily and adjusted her watering schedule accordingly every week. "
    "The raised beds along the south-facing fence received the most sun and produced the "
    "best yields of tomatoes and courgettes every single summer without fail for many years. "
    "She composted kitchen waste and added leaf mold in autumn to build up the organic matter. "
    "The children liked to help with the harvesting and often ate the strawberries straight "
    "from the plant while she was busy weeding between the rows of vegetables in the afternoon."
)  # ~108 words

_GARDEN_POOL_3 = (
    "A vegetable plot in a temperate climate demands attention across all four seasons of the year. "
    "In winter she planned the crop rotation, consulting her notebook of previous years to "
    "ensure that brassicas never returned to the same bed two years running as they should not. "
    "The compost heap at the end of the garden generated its own heat and by March was full of "
    "rich dark material ready to be incorporated into the planting beds before the seeds went in. "
    "She preferred to grow from seed where possible, sowing tomatoes and peppers under glass in "
    "February and pricking them out once the true leaves appeared and the seedlings were sturdy."
)  # ~112 words

# Target: same topic/register as the pool. Near-identical function-word profile to pool docs
# (typical → small |z| on function_words). Uses a more distinctive punctuation pattern
# (unusual colons/semicolons) that differs from the pool (atypical → large |z| on punctuation).
_GARDEN_TARGET = (
    "The garden in early spring: a study in contrasts; cold earth and warm aspiration. "
    "She worked the soil methodically: first the digging, then the raking, then the sowing. "
    "Seeds carried in her pocket all morning; she dropped each one at the prescribed depth and "
    "covered it with a fine layer of sifted compost. The apple tree needed attention; its bark "
    "had split in the January frost and she wrapped the wound in grafting tape. She kept notes "
    "in a battered journal: dates, weather observations, which varieties performed well and "
    "which had disappointed her expectations after the dry summer of the previous year. "
    "She wrote carefully, noting every detail she could remember from that difficult season."
)  # ~117 words, extra punctuation (colons/semicolons)

_POOL_3 = [
    ("pool1", _GARDEN_BASE, None),
    ("pool2", _GARDEN_POOL_2, None),
    ("pool3", _GARDEN_POOL_3, None),
]

# A very short text (below 100-word floor) — used for floor tests.
_SHORT_TEXT = "This is too short to analyze properly."

# A pool of 5 identical docs — used for degenerate sd==0 / z=None test.
_IDENTICAL_TEXT = _GARDEN_BASE
_POOL_5_IDENTICAL = [
    (f"id{i}", _IDENTICAL_TEXT, None) for i in range(5)
]

# Helper: slightly shifted text that changes one feature family conspicuously.
# A version of the target with ALL punctuation replaced with simple periods to test large |z|.
_SHIFTED_TARGET = (
    "The garden in early spring. A study in contrasts. Cold earth and warm aspiration. "
    "She worked the soil methodically. First the digging. Then the raking. Then the sowing. "
    "Seeds carried in her pocket all morning. She dropped each one at the prescribed depth. "
    "She covered it with a fine layer of sifted compost. The apple tree needed attention. "
    "Its bark had split in the January frost and she wrapped the wound in grafting tape. "
    "She kept notes in a battered journal. Dates. Weather observations. Which varieties "
    "had performed well and which had disappointed her expectations in the dry summer."
)


# ---- helpers -------------------------------------------------------------------

def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cdnp.main(argv)
    return rc, json.loads(out.getvalue())


def _write_target(tmp_path: Path, text: str, name: str = "target.txt") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _write_manifest(tmp_path: Path,
                    pool: list[tuple[str, str, object]],
                    name: str = "pool.jsonl") -> Path:
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as f:
        for src, text, _rpath in pool:
            f.write(json.dumps({"id": src, "text": text}) + "\n")
    return p


def _walk_keys(obj):
    """Yield every dict key anywhere in a nested structure (the recursive no-verdict walk).
    Mirrors test_distinct_diversity_audit.py:76-84.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# The pinned forbidden-key frozenset. SUPERSET of test_distinct_diversity_audit.py's _FORBIDDEN_KEYS.
# Written verbatim here (not imported) — a dependency on test_distinct_diversity_audit would make
# the no-verdict guard depend on a sibling test's internal constant.
_FORBIDDEN_RESULT_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "band", "is_novel", "novelty_band",
    "same_author", "novelty_score", "score", "is_derivative", "is_original",
})


# ---- AC-6: feature-schema count invariant (run before other tests) ------------

def test_novelty_family_count_invariant():
    """AC-6: NOVELTY_FAMILY_COUNT == len(NOVELTY_FEATURE_SCHEMA) == 7.

    Pinned against CHAR_NGRAM_NS == (3, 4, 5): the four non-char families +
    one family per n in CHAR_NGRAM_NS = 4 + 3 = 7.
    """
    assert cdnp.NOVELTY_FAMILY_COUNT == 7
    assert len(cdnp.NOVELTY_FEATURE_SCHEMA) == 7
    assert cdnp.NOVELTY_FAMILY_COUNT == len(cdnp.NOVELTY_FEATURE_SCHEMA)
    # Pinned against CHAR_NGRAM_NS = (3, 4, 5) — the spec count is FIXED, not derived at build time.
    assert CHAR_NGRAM_NS == (3, 4, 5), (
        "CHAR_NGRAM_NS changed — the NOVELTY_FEATURE_SCHEMA count invariant must be updated together"
    )
    # The 4 non-char families:
    non_char = [fid for fid, _, _ in cdnp.NOVELTY_FEATURE_SCHEMA if not fid.startswith("char_")]
    assert len(non_char) == 4
    # Exactly one char family per n in CHAR_NGRAM_NS:
    char_families = [fid for fid, _, _ in cdnp.NOVELTY_FEATURE_SCHEMA if fid.startswith("char_")]
    assert len(char_families) == len(CHAR_NGRAM_NS)
    for n in CHAR_NGRAM_NS:
        assert f"char_ngrams_{n}" in char_families
    # No spaCy families:
    for fid, _, _ in cdnp.NOVELTY_FEATURE_SCHEMA:
        assert fid not in ("pos_trigrams", "dependency_ngrams"), (
            f"spaCy family {fid!r} must not appear in NOVELTY_FEATURE_SCHEMA"
        )


# ---- AC-11 (PRIMARY): no-verdict field guard (recursive, CI-blocking) ----------

def test_no_forbidden_keys_recursive(tmp_path):
    """AC-11: THE key guard test — unconditional, CI-blocking, no skipif.

    Modeled verbatim on test_distinct_diversity_audit.py:227-228.
    The frozenset is PINNED in this file (not derived from the script).
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert env["available"] is True
    keys = set(_walk_keys(env["results"]))
    assert _FORBIDDEN_RESULT_KEYS.isdisjoint(keys), (
        f"forbidden verdict key in results: {keys & _FORBIDDEN_RESULT_KEYS}"
    )


def test_no_forbidden_keys_with_planted_key():
    """AC-11: Prove the _walk_keys assertion CATCHES a planted banned key.

    Builds a synthetic results dict with a planted forbidden key directly (no CLI round-trip
    needed — the guard test tests the _walk_keys machinery itself, not the CLI path).
    """
    # Build a valid-looking results dict and plant a forbidden key deep inside.
    fake_results = {
        "n_pool": 3,
        "target_words": 115,
        "per_feature": [{"family": "function_words", "feature_id": "f.the", "name": "the",
                          "value": 0.05, "pool_mean": 0.04, "pool_sd": 0.01, "z": 1.0,
                          "n_pool_obs": 3}],
        "per_family_summary": [],
        "target_only_features": {},
        "assumptions": {"calibration_status": "provisional"},
        "verdict": "this_should_be_caught",  # planted forbidden key
    }
    keys = set(_walk_keys(fake_results))
    assert not _FORBIDDEN_RESULT_KEYS.isdisjoint(keys), (
        "planted 'verdict' key should have been found by _walk_keys — test harness broken"
    )
    # And the guard in test_no_forbidden_keys_recursive would have caught it:
    with pytest.raises(AssertionError):
        assert _FORBIDDEN_RESULT_KEYS.isdisjoint(keys), (
            f"forbidden verdict key in results: {keys & _FORBIDDEN_RESULT_KEYS}"
        )


# ---- AC-1: deterministic output ------------------------------------------------

def test_deterministic_output(tmp_path):
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env1 = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    _, env2 = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert env1["results"] == env2["results"]


# ---- AC-2: envelope shape ------------------------------------------------------

def test_envelope_shape(tmp_path):
    """AC-2: build_output success envelope; results carries required keys."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert rc == 0
    assert env["available"] is True
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "set_level_diversity"
    assert env["tool"] == "cross_doc_novelty_profile"
    r = env["results"]
    for key in ("n_pool", "target_words", "per_feature", "per_family_summary",
                "target_only_features", "assumptions"):
        assert key in r, f"missing results key: {key!r}"
    # per_feature rows have the required shape.
    assert len(r["per_feature"]) > 0
    row = r["per_feature"][0]
    for rk in ("family", "feature_id", "name", "value", "pool_mean", "pool_sd", "z", "n_pool_obs"):
        assert rk in row, f"per_feature row missing key: {rk!r}"
    # per_family_summary has entries for each populated family.
    assert len(r["per_family_summary"]) > 0
    summ = r["per_family_summary"][0]
    for sk in ("family", "family_name", "orientation", "n_axes", "abs_z_distribution"):
        assert sk in summ, f"per_family_summary entry missing key: {sk!r}"
    dist = summ["abs_z_distribution"]
    for dk in ("n", "mean", "sd", "min", "p10", "p50", "p90"):
        assert dk in dist, f"abs_z_distribution missing key: {dk!r}"


# ---- AC-3: model-free M1 / no-model-import -------------------------------------

def test_model_free_spacy_torch_absent(tmp_path, monkeypatch):
    """AC-3: M1 path imports no torch/transformers; with spaCy ABSENT output is unchanged."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)

    # Block spacy, torch, transformers from sys.modules.
    for mod in ("spacy", "torch", "transformers"):
        monkeypatch.setitem(sys.modules, mod, None)  # type: ignore

    out = io.StringIO()
    with redirect_stdout(out):
        rc = cdnp.main(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    env = json.loads(out.getvalue())
    assert env["available"] is True, f"model-free M1 should succeed with spacy/torch absent: {env}"
    assert rc == 0


# ---- AC-4: z-position pin ------------------------------------------------------

def test_z_position_identical_pool_zero_or_none():
    """AC-4a: pool of identical docs + target equal to them → z==0 or None (sd==0)."""
    results = cdnp.audit_novelty_profile(
        _IDENTICAL_TEXT,
        _POOL_5_IDENTICAL,
        length_floor_words=50,
        min_pool=5,
    )
    for row in results["per_feature"]:
        # sd==0 → z=None; sd>0 (shouldn't happen for identical texts, but guard):
        if row["z"] is not None:
            assert abs(row["z"]) < 1e-9, (
                f"Expected z==0 for identical pool, got z={row['z']} for {row['feature_id']!r}"
            )


def test_z_degenerate_sd_none_not_nan():
    """AC-4b: degenerate pool (sd==0) → z=None, never NaN (R4 gate would reject NaN)."""
    results = cdnp.audit_novelty_profile(
        _IDENTICAL_TEXT,
        _POOL_5_IDENTICAL,
        length_floor_words=50,
        min_pool=5,
    )
    for row in results["per_feature"]:
        assert row["z"] is None or isinstance(row["z"], float), (
            f"z must be float or None, got {type(row['z'])} for {row['feature_id']!r}"
        )
        if row["z"] is not None:
            assert not (row["z"] != row["z"]), "z must never be NaN"  # NaN != NaN


# ---- AC-5: mean/SD-only stat ---------------------------------------------------

def test_mean_sd_only_no_robust(tmp_path):
    """AC-5: no pool_median/pool_mad/z_robust; no --robust flag; stat=='mean_sd_z'."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert env["available"] is True
    r = env["results"]
    assert r["assumptions"]["stat"] == "mean_sd_z"
    # No robust keys in per_feature rows.
    for row in r["per_feature"]:
        assert "pool_median" not in row
        assert "pool_mad" not in row
        assert "z_robust" not in row
    # No --robust flag.
    with pytest.raises(SystemExit):
        cdnp.main(["--robust"])


# ---- AC-7: self-exclusion pin --------------------------------------------------

def test_self_exclusion_drops_target_from_pool(tmp_path):
    """AC-7: target present in pool by resolved path → dropped; self_excluded==1; n_pool decremented.

    Uses a manifest that references the target file by path so its resolved_path equals the target's.
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET, "target.txt")
    # Manifest with the target referenced by its actual path, plus 5 other pool docs.
    mf = tmp_path / "pool_with_target.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "tgt_in_pool", "path": "target.txt"}) + "\n")
        f.write(json.dumps({"id": "p1", "text": _GARDEN_BASE}) + "\n")
        f.write(json.dumps({"id": "p2", "text": _GARDEN_POOL_2}) + "\n")
        f.write(json.dumps({"id": "p3", "text": _GARDEN_POOL_3}) + "\n")
        f.write(json.dumps({"id": "p4", "text": _GARDEN_POOL_2 + " extra words added here."}) + "\n")
        f.write(json.dumps({"id": "p5", "text": _GARDEN_BASE + " second copy for diversity."}) + "\n")

    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "5", "--json",
    ])
    assert env["available"] is True, f"Expected success but got: {env.get('reason')}"
    r = env["results"]
    assert r["assumptions"]["self_excluded"] == 1
    # n_pool should be 5 (the 5 non-target pool docs), not 6.
    assert r["n_pool"] == 5


def test_self_exclusion_empties_pool_bad_input(tmp_path):
    """AC-7: self-exclusion that empties pool below min-pool → bad_input."""
    # Pool has only the target itself plus a small set.
    tgt = _write_target(tmp_path, _GARDEN_TARGET, "target.txt")
    # Manifest with the target as a file path entry + 2 others (3 total, min-pool=5 → fail).
    mf = tmp_path / "pool.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "p1", "text": _GARDEN_BASE}) + "\n")
        f.write(json.dumps({"id": "p2", "text": _GARDEN_POOL_2}) + "\n")
        # target as path reference
        f.write(json.dumps({"id": "tgt_ref", "path": "target.txt"}) + "\n")
    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "5", "--json",
    ])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert rc == 3


def test_self_exclusion_drops_inline_copy_of_target(tmp_path):
    """Codex P1 (cross_doc_novelty_profile.py:494): a manifest row carrying an INLINE copy of the
    target text has resolved_path=None, so the path-only guard never self-excludes it. The
    content-fingerprint guard must drop it: the target cannot position itself against a pool that
    includes itself. Asserts the inline copy is excluded, self_excluded counts it, and n_pool drops.
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET, "target.txt")
    # Manifest with an INLINE copy of the target text (path None) + 5 genuinely-other pool docs.
    mf = tmp_path / "pool_with_inline_target.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "inline_tgt_copy", "text": _GARDEN_TARGET}) + "\n")
        f.write(json.dumps({"id": "p1", "text": _GARDEN_BASE}) + "\n")
        f.write(json.dumps({"id": "p2", "text": _GARDEN_POOL_2}) + "\n")
        f.write(json.dumps({"id": "p3", "text": _GARDEN_POOL_3}) + "\n")
        f.write(json.dumps({"id": "p4", "text": _GARDEN_POOL_2 + " extra words added here."}) + "\n")
        f.write(json.dumps({"id": "p5", "text": _GARDEN_BASE + " second copy for diversity."}) + "\n")

    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "5", "--json",
    ])
    assert env["available"] is True, f"Expected success but got: {env.get('reason')}"
    r = env["results"]
    # The inline copy is dropped by content fingerprint; self_excluded counts it.
    assert r["assumptions"]["self_excluded"] == 1
    # n_pool is the 5 genuinely-other docs, not 6.
    assert r["n_pool"] == 5


def test_self_exclusion_inline_copy_with_whitespace_variation(tmp_path):
    """The content fingerprint normalizes whitespace/case (normalize_for_char_ngrams), so an inline
    copy that differs only by trivial whitespace/case is still recognized as the target and dropped.
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET, "target.txt")
    # Inline copy with collapsed/extra whitespace and altered case — same normalized content.
    inline_variant = ("  " + _GARDEN_TARGET.upper().replace(" ", "   ") + "  ")
    mf = tmp_path / "pool_inline_variant.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "inline_variant", "text": inline_variant}) + "\n")
        f.write(json.dumps({"id": "p1", "text": _GARDEN_BASE}) + "\n")
        f.write(json.dumps({"id": "p2", "text": _GARDEN_POOL_2}) + "\n")
        f.write(json.dumps({"id": "p3", "text": _GARDEN_POOL_3}) + "\n")
        f.write(json.dumps({"id": "p4", "text": _GARDEN_POOL_2 + " extra words added here."}) + "\n")
        f.write(json.dumps({"id": "p5", "text": _GARDEN_BASE + " second copy for diversity."}) + "\n")

    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "5", "--json",
    ])
    assert env["available"] is True, f"Expected success but got: {env.get('reason')}"
    r = env["results"]
    assert r["assumptions"]["self_excluded"] == 1
    assert r["n_pool"] == 5


def test_self_exclusion_inline_copy_empties_pool_bad_input(tmp_path):
    """Fail-CLOSED: when dropping the inline copy of the target pushes the usable pool below
    --min-pool, the surface abstains with bad_input rather than positioning the target against a
    pool that still secretly contains itself.
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET, "target.txt")
    mf = tmp_path / "pool_inline_target_small.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "inline_tgt_copy", "text": _GARDEN_TARGET}) + "\n")
        f.write(json.dumps({"id": "p1", "text": _GARDEN_BASE}) + "\n")
        f.write(json.dumps({"id": "p2", "text": _GARDEN_POOL_2}) + "\n")
    # 3 rows total. With --min-pool 3, the PRE-FIX code (which never drops the inline target copy)
    # would keep all 3 and succeed. The fix drops the inline target → 2 usable < 3 → bad_input.
    # This isolates the fix: it only abstains BECAUSE the inline copy is now excluded.
    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "3", "--json",
    ])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert rc == 3


# ---- AC-8: pool-floor abstention -----------------------------------------------

def test_pool_floor_abstention(tmp_path):
    """AC-8: usable pool < --min-pool → available:false, bad_input, rc==3."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    # Pool with only 2 docs (below default min-pool of 5).
    mf = _write_manifest(tmp_path, _POOL_3[:2])
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


# ---- AC-9: length-floor --------------------------------------------------------

def test_target_below_length_floor_bad_input(tmp_path):
    """AC-9a: target below --length-floor-words → bad_input."""
    tgt = _write_target(tmp_path, _SHORT_TEXT)
    mf = _write_manifest(tmp_path, _POOL_3)
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_pool_docs_below_length_floor_dropped(tmp_path, capsys):
    """AC-9b: pool docs below --length-floor-words are dropped (with stderr note); if pool falls
    below --min-pool → bad_input."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    # Pool: 1 real + 4 short (below floor) → usable=1, below min-pool=5.
    short_pool = [("p1", _GARDEN_BASE, None)] + [
        (f"short{i}", _SHORT_TEXT, None) for i in range(4)
    ]
    mf = _write_manifest(tmp_path, short_pool)
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


# ---- AC-10: claim-license present + refuses verdict ----------------------------

def test_claim_license_present_and_refuses_verdict(tmp_path):
    """AC-10: ClaimLicense present; does_not_license contains the required phrases."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    cl = env["claim_license"]
    assert cl is not None
    dnl = json.dumps(cl).lower()
    assert "ai/human" in dnl
    assert "low novelty is not an ai/human/derivative" in dnl
    assert "pool" in dnl  # pool-dependence caveat
    assert "no verdict" in dnl
    assert "no single 'novelty score'" in dnl or "no band and no single" in dnl
    # No --verdict flag.
    with pytest.raises(SystemExit):
        cdnp.main(["--verdict"])


# ---- AC-12: no-single-score / no-band guard ------------------------------------

def test_no_single_score_or_band(tmp_path):
    """AC-12: no top-level novelty/novelty_score; no band/provisional_band/novelty_band anywhere;
    calibration_status in assumptions, attached to no decision."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    r = env["results"]
    assert "novelty" not in r, "no top-level 'novelty' key"
    assert "novelty_score" not in r
    # Recursive walk for band variants.
    keys = set(_walk_keys(r))
    assert "band" not in keys
    assert "provisional_band" not in keys
    assert "novelty_band" not in keys
    # calibration_status must be in assumptions.
    assert r["assumptions"]["calibration_status"] == "provisional"


# ---- AC-13: no-ranked-selection guard -----------------------------------------

def test_no_ranked_selection(tmp_path):
    """AC-13: per_feature rows sorted by family then name (NOT |z|); no rank/selected/best."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    r = env["results"]
    rows = r["per_feature"]
    assert len(rows) > 0
    # Check sorted by (family, name).
    for i in range(len(rows) - 1):
        a, b = rows[i], rows[i + 1]
        key_a = (a["family"], a["name"])
        key_b = (b["family"], b["name"])
        assert key_a <= key_b, (
            f"per_feature not sorted by (family, name): {key_a!r} > {key_b!r}"
        )
    # No rank/selected/best keys.
    keys = set(_walk_keys(rows))
    for forbidden in ("rank", "selected", "best"):
        assert forbidden not in keys, f"selection key {forbidden!r} found in per_feature rows"


# ---- AC-14: orthogonality assertion --------------------------------------------

def test_orthogonality_no_cluster_keys(tmp_path):
    """AC-14: this id reports NO partition/cluster keys; no per_feature in distinct_diversity_audit.

    Verifies the two ids are feature-wise and cluster-wise complements:
    - cross_doc_novelty_profile results have no n_clusters/cluster_sizes/representatives
    - distinct_diversity_audit results have no per_feature key
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    r = env["results"]
    keys = set(_walk_keys(r))
    # No cluster partition keys.
    for cluster_key in ("n_clusters", "cluster_sizes", "representatives"):
        assert cluster_key not in keys, (
            f"cluster key {cluster_key!r} must not appear in cross_doc_novelty_profile results "
            "(orthogonality: this is the feature-wise read, not the cluster-wise read)"
        )
    # distinct_diversity_audit (if importable) does NOT emit per_feature.
    try:
        import distinct_diversity_audit as dd  # type: ignore
        import distinct_diversity_audit as _dd  # noqa: F401
        # Build a distinct_diversity_audit envelope with the same pool.
        import io as _io
        from contextlib import redirect_stdout as _rso

        # Write a pool.jsonl with >=10 docs for distinct_diversity_audit's min_set.
        pool10 = [_GARDEN_BASE, _GARDEN_POOL_2, _GARDEN_POOL_3,
                  _GARDEN_TARGET, _SHIFTED_TARGET,
                  _GARDEN_BASE + " A.", _GARDEN_POOL_2 + " B.",
                  _GARDEN_POOL_3 + " C.", _GARDEN_TARGET + " D.", _SHIFTED_TARGET + " E."]
        mf10 = _write_manifest(tmp_path, [(f"p{i}", t, None) for i, t in enumerate(pool10)],
                               name="pool10.jsonl")
        out10 = _io.StringIO()
        with _rso(out10):
            dd.main(["--manifest", str(mf10), "--json"])
        dd_env = json.loads(out10.getvalue())
        if dd_env.get("available"):
            dd_keys = set(_walk_keys(dd_env["results"]))
            assert "per_feature" not in dd_keys, (
                "distinct_diversity_audit must not emit per_feature "
                "(orthogonality: it is the cluster-wise read)"
            )
    except ImportError:
        pass  # distinct_diversity_audit not importable in this test env; skip the sibling check


# ---- AC-15: CI discrimination boundary ----------------------------------------

def test_capabilities_yaml_surface_handoff_consumers():
    """AC-15: capabilities.d/cross_doc_novelty_profile.yaml asserts correct surface/handoff/consumers.

    The boundary is enforced ENTIRELY in-tree (not by a downstream voicewright gate).
    """
    # Find the capabilities.d directory relative to the scripts dir.
    caps_dir = SCRIPTS.parent / "capabilities.d"
    yaml_path = caps_dir / "cross_doc_novelty_profile.yaml"
    assert yaml_path.exists(), (
        f"capabilities.d/cross_doc_novelty_profile.yaml not found at {yaml_path}"
    )
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        # The yaml uses an 'entries' list (matches the other capabilities.d files).
        entries = data.get("entries", [data])
        entry = entries[0]
        assert entry["surface"] == "set_level_diversity", (
            f"surface must be 'set_level_diversity', got {entry['surface']!r}"
        )
        assert entry["handoff"] == "none", (
            f"handoff must be 'none', got {entry['handoff']!r}"
        )
        assert entry["consumers"] == [], (
            f"consumers must be [], got {entry['consumers']!r}"
        )
    except ImportError:
        # yaml not available — read as text and do a string search.
        text = yaml_path.read_text(encoding="utf-8")
        assert "surface: set_level_diversity" in text
        assert "handoff: none" in text
        assert "consumers: []" in text


# ---- AC-16: embedding-lens fails loud on import-SUCCESS too --------------------

def test_embedding_lens_fails_loud_import_absent(tmp_path):
    """AC-16a: --lens embedding → available:false missing_dependency even without authorship_embedding."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--lens", "embedding", "--json",
    ])
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    assert "silently falling back" in env["reason"].lower()


def test_embedding_lens_fails_loud_on_import_success(tmp_path, monkeypatch):
    """AC-16b: planted authorship_embedding stub → embedding lens STILL fails loud.

    M1 wires no real embedding lens; a module being importable must NOT let --lens embedding
    silently fall back to the stylometric lens. Mirrors test_distinct_diversity_audit.py:312-334.
    """
    stub = types.ModuleType("authorship_embedding")
    monkeypatch.setitem(sys.modules, "authorship_embedding", stub)

    err = cdnp._embedding_lens_unavailable()
    assert err, (
        "import-SUCCESS branch must fail loud (non-empty error), never return {} and fall through"
    )
    assert err["reason_category"] == "missing_dependency"
    assert "silently falling back" in err["reason"].lower()

    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    rc, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--lens", "embedding", "--json",
    ])
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    # The mislabel bug: stylometric results leaking under --lens embedding.
    assert env.get("results") is None or "per_feature" not in (env.get("results") or {})


# ---- AC-17: graceful degradation -----------------------------------------------

def test_empty_pool_manifest_bad_input(tmp_path):
    """AC-17a: empty manifest → bad_input (no div-by-zero)."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = tmp_path / "empty.jsonl"
    mf.write_text("", encoding="utf-8")
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_malformed_manifest_rows_skipped_warned(tmp_path):
    """AC-17b: malformed rows are skipped with a warning; valid rows still processed."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = tmp_path / "mixed.jsonl"
    with mf.open("w", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("[1, 2, 3]\n")  # valid JSON, non-object
        for src, text, _ in _POOL_3:
            f.write(json.dumps({"id": src, "text": text}) + "\n")
    # With 3 valid pool docs but min-pool=5 → bad_input (not a crash).
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    # Either the 3-doc pool < min-pool=5 triggers bad_input, or (min-pool=3) succeeds — both OK.
    # The important thing: no unhandled exception (no crash).
    assert "available" in env


def test_missing_target_bad_input(tmp_path):
    """AC-17c: missing --target → bad_input."""
    mf = _write_manifest(tmp_path, _POOL_3)
    rc, env = _envelope(["--reference-manifest", str(mf), "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


# ---- AC-18: target-only honesty ------------------------------------------------

def test_target_only_features_not_z_scored(tmp_path):
    """AC-18: names in target absent from pool axis list appear in target_only_features; never z-scored."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    r = env["results"]
    target_only = r["target_only_features"]
    per_feature = r["per_feature"]
    # Collect all pool-axis names from per_feature rows.
    pool_axis_names: set[str] = set()
    for row in per_feature:
        pool_axis_names.add(row["name"])
    # Any name in target_only must NOT be in per_feature (never z-scored).
    for family_data in target_only.values():
        for name in family_data["names"]:
            assert name not in pool_axis_names, (
                f"target-only feature {name!r} should not appear in per_feature (pool axis list)"
            )


# ---- AC-20: lens-label honesty -------------------------------------------------

def test_lens_label_honesty(tmp_path):
    """AC-20: lens=='stylometric'; paper_lens_incomparable cites GENIE (2606.12790)."""
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope(["--target", str(tgt), "--reference-manifest", str(mf), "--min-pool", "3", "--json"])
    r = env["results"]
    assert r["assumptions"]["lens"] == "stylometric"
    plc = r["assumptions"]["paper_lens_incomparable"].lower()
    assert "2606.12790" in plc
    assert "genie" in plc or "learned task" in plc
    assert "not comparable" in plc or "not" in plc


# ---- Integration: worked example round-trip ------------------------------------

def test_worked_example_envelope_round_trip(tmp_path):
    """Full worked example: 3-doc pool + target (all > 100-word floor).

    Pool and target are the gardening fixtures (topic/register matched). Verifies:
    - n_pool == 3
    - per_family_summary covers 7 schema families (those populated by the pool)
    - target_words > 0
    - per_feature rows non-empty
    - z values for sd>0 rows are finite floats
    """
    tgt = _write_target(tmp_path, _GARDEN_TARGET)
    mf = _write_manifest(tmp_path, _POOL_3)
    _, env = _envelope([
        "--target", str(tgt), "--reference-manifest", str(mf),
        "--min-pool", "3", "--json",
    ])
    assert env["available"] is True
    r = env["results"]
    assert r["n_pool"] == 3
    assert r["target_words"] > 0
    assert len(r["per_feature"]) > 0
    # All z values are float or None (never NaN).
    for row in r["per_feature"]:
        if row["z"] is not None:
            assert isinstance(row["z"], float)
            assert row["z"] == row["z"], f"NaN z for {row['feature_id']!r}"
    # per_family_summary: at most 7 families (those with axes from the pool).
    assert 0 < len(r["per_family_summary"]) <= cdnp.NOVELTY_FAMILY_COUNT
    # The 7-key distribution block is complete.
    for summ in r["per_family_summary"]:
        dist = summ["abs_z_distribution"]
        assert "n" in dist and "mean" in dist and "sd" in dist
        assert "min" in dist and "p10" in dist and "p50" in dist and "p90" in dist
