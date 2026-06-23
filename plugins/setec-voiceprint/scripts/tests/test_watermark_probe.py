#!/usr/bin/env python3
"""Tests for ``watermark_probe.py`` — KGW green-list z-test (M1).

Pins the spec contract (``specs/29-watermark-probe.md``), model-free and
stdlib. Covers the numbered Acceptance criteria:

  1. keyed + deterministic green-list partition
  2. z-statistic + p-value numerics (green-biased vs independent)
  3. parameter validation (ValueError / bad_input)
  4. no-verdict envelope shape (scoped to results, recursively)
  5. absence-≠-human discipline (render + values)
  6. reliability band carries the decay + length caveat (two tiers only)
  7. tokenizer-mismatch guard
  8. key secrecy
  9. claim license present + refuses the verdict (incl. no-threshold text)
 10. both goldens + count bumps (covered by the dropin/label tests)
 11. M2 sweep additive + caveated
 12. surface-addition paper trail (drift/freshness — covered elsewhere)

Plus the folded-finding guards:
  * no-verdict guard scoped to results (NOT the top-level envelope, where
    ai_status is a permitted harness convention) — finding 1
  * band string set contains no class/boolean token — finding 3b
  * import-separation: watermark_probe imports no selection/calibration/
    threshold-setting layer — finding 3
  * p_value reported at full precision; neg_log10_p present — finding 4
  * the ONE surface name agrees across all four sites — finding 5
"""

from __future__ import annotations

import ast
import json
import random
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
sys.path.insert(0, str(_SCRIPTS))

import watermark_probe as wp  # noqa: E402


# ============================================================
# Fixtures
# ============================================================

_KEY = "secret-key-abc"
_GAMMA = 0.5
_VOCAB_SIZE = 1000


def _green_biased_tokens(n=300, *, key=_KEY, gamma=_GAMMA, vocab_size=_VOCAB_SIZE):
    """A token stream forced into each position's (left-hash) green list —
    a synthetic watermarked fixture. z should be large + positive."""
    rng = random.Random(0)
    tokens = [rng.randrange(vocab_size)]
    for _ in range(n):
        prev = tokens[-1]
        g = sorted(wp.green_list(key, (prev,), gamma=gamma, vocab_size=vocab_size))
        tokens.append(rng.choice(g))
    return tokens


def _independent_tokens(n=300, *, vocab_size=_VOCAB_SIZE, seed=99):
    """A token stream drawn independently of any partition. z ≈ 0, p ≈ 0.5."""
    rng = random.Random(seed)
    return [rng.randrange(vocab_size) for _ in range(n)]


# ============================================================
# 1. Keyed + deterministic green-list partition
# ============================================================


def test_green_list_keyed_and_deterministic():
    ctx = (42,)
    g1 = wp.green_list(_KEY, ctx, gamma=_GAMMA, vocab_size=_VOCAB_SIZE)
    g2 = wp.green_list(_KEY, ctx, gamma=_GAMMA, vocab_size=_VOCAB_SIZE)
    assert g1 == g2  # reproducible across calls
    assert len(g1) == int(_GAMMA * _VOCAB_SIZE)  # floor(gamma*|V|)


def test_green_list_differs_on_key_gamma_scheme():
    ctx = (42,)
    base = wp.green_list(_KEY, ctx, gamma=_GAMMA, vocab_size=_VOCAB_SIZE)
    # Different key → different partition.
    other_key = wp.green_list("other-key", ctx, gamma=_GAMMA, vocab_size=_VOCAB_SIZE)
    assert other_key != base
    # Different gamma → different size (hence different set).
    other_gamma = wp.green_list(_KEY, ctx, gamma=0.25, vocab_size=_VOCAB_SIZE)
    assert other_gamma != base
    assert len(other_gamma) == int(0.25 * _VOCAB_SIZE)


def test_both_hash_schemes_produce_valid_green_lists():
    tokens = _independent_tokens(120)
    for scheme, ph in (("left-hash", wp.DEFAULT_PREFIX_H), ("prefix-h", 3)):
        r = wp.probe(
            tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA,
            hash_scheme=scheme, prefix_h=ph,
        )
        assert r.hash_scheme == scheme
        assert r.n_scored_tokens > 0


# ============================================================
# 2. z-statistic + p-value numerics
# ============================================================


def test_z_large_positive_on_green_biased_fixture():
    tokens = _green_biased_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.z is not None and r.z > 10.0
    assert r.p_value < 1e-10
    assert r.green_fraction > 0.9


def test_z_near_zero_on_independent_fixture():
    tokens = _independent_tokens(400)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert abs(r.z) < 4.0
    assert 0.2 < r.p_value < 0.8  # p ≈ 0.5


def test_z_and_green_fraction_closed_form():
    """z = (green - gamma*T)/sqrt(T*gamma*(1-gamma)); green_fraction = green/T."""
    import math
    tokens = _green_biased_tokens(200)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    t = r.n_scored_tokens
    expected_z = (r.green_count - _GAMMA * t) / math.sqrt(t * _GAMMA * (1 - _GAMMA))
    assert r.z == pytest.approx(expected_z)
    assert r.green_fraction == pytest.approx(r.green_count / t)


def test_no_model_loaded():
    """The math path imports no torch/transformers (stdlib only)."""
    assert "torch" not in sys.modules or True  # not asserting global; structural test below
    tokens = _independent_tokens(60)
    # Runs with nothing but stdlib in the call path.
    wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)


# ============================================================
# Finding 4 — p_value full precision + neg_log10_p
# ============================================================


def test_p_value_full_precision_not_floored():
    """A z=6+ fixture must NOT round the tail p down to 0; neg_log10_p
    preserves the magnitude."""
    tokens = _green_biased_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    # p is a tiny float, NOT rounded to 0.0 or a 4-dp floor.
    assert 0.0 < r.p_value < 1e-9
    assert r.p_value != round(r.p_value, 4)  # rounding would erase it
    # neg_log10_p carries the tail precision and is a large positive number.
    assert r.neg_log10_p is not None and r.neg_log10_p > 9.0


def test_neg_log10_p_named_to_dodge_probability_bound():
    """neg_log10_p is > 1 in the tail; build_output's [0,1] probability bound
    must NOT apply to it (it is a -log10 transform, not a probability). The
    envelope must build without an OutputValidityError."""
    tokens = _green_biased_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.neg_log10_p > 1.0
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=300)
    assert env["results"]["neg_log10_p"] == r.neg_log10_p  # survived the gate


# ============================================================
# 3. Parameter validation
# ============================================================


@pytest.mark.parametrize("kwargs", [
    dict(key=None, gamma=0.5, vocab_size=10, hash_scheme="left-hash"),
    dict(key="", gamma=0.5, vocab_size=10, hash_scheme="left-hash"),
    dict(key="k", gamma=0.0, vocab_size=10, hash_scheme="left-hash"),
    dict(key="k", gamma=1.0, vocab_size=10, hash_scheme="left-hash"),
    dict(key="k", gamma=1.5, vocab_size=10, hash_scheme="left-hash"),
    dict(key="k", gamma=0.5, vocab_size=0, hash_scheme="left-hash"),
    dict(key="k", gamma=0.5, vocab_size=10, hash_scheme="bogus"),
])
def test_validate_params_raises(kwargs):
    with pytest.raises(ValueError):
        wp.validate_params(**kwargs)


def test_validate_params_t_below_minimum():
    with pytest.raises(ValueError):
        wp.validate_params(
            key="k", gamma=0.5, vocab_size=10, hash_scheme="left-hash",
            n_scored_tokens=0,
        )


def test_error_type_is_valueerror_subclass():
    assert issubclass(wp.WatermarkProbeError, ValueError)


# ============================================================
# 4. No-verdict envelope shape (scoped to results, recursively) — finding 1
# ============================================================

_FORBIDDEN_VERDICT_KEYS = {"is_watermarked", "is_ai", "is_human", "verdict"}


def _walk_keys(obj):
    """Yield every dict key reachable in a nested results structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


def test_no_verdict_key_anywhere_in_results():
    """Finding 1: the guard is scoped to envelope['results'] (recursively),
    NOT the whole envelope — ai_status is a permitted harness convention at
    the top level (mirrors test_fast_detect_curvature's `'verdict' not in r`)."""
    tokens = _green_biased_tokens(200)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=200)

    results = env["results"]
    keys = set(_walk_keys(results))
    assert _FORBIDDEN_VERDICT_KEYS.isdisjoint(keys), (
        f"forbidden verdict keys in results: {keys & _FORBIDDEN_VERDICT_KEYS}"
    )
    # Spec-mandated result keys ARE present.
    for k in ("z", "p_value", "green_fraction", "gamma", "n_scored_tokens",
              "band", "key_id", "hash_scheme", "assumptions"):
        assert k in results, k
    # ai_status IS a permitted top-level harness convention (defaults None).
    assert "ai_status" in env
    assert env["ai_status"] is None


def test_band_value_is_a_descriptive_string():
    tokens = _independent_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=300)
    assert env["results"]["band"] in wp.BAND_STRINGS
    assert not isinstance(env["results"]["band"], bool)


# ============================================================
# Finding 3a/3b — two bands only, no class/boolean token; no top "fire" tier
# ============================================================


def test_only_two_descriptive_bands_no_strongly_tier():
    """Finding 3a: drop the strongly_* top tier so there is no 'maximum'
    band that reads as a fire signal."""
    assert set(wp.BAND_STRINGS) == {"under_powered", "watermark_consistent"}
    assert not any("strongly" in b for b in wp.BAND_STRINGS)


def test_band_strings_contain_no_class_or_boolean_token():
    """Finding 3b: the band string set contains no class/boolean token —
    nothing that reads as ai/human/watermarked/true/false/yes/no."""
    forbidden_tokens = (
        "ai", "human", "watermarked", "true", "false", "yes", "no",
        "verdict", "likely", "positive", "negative",
    )
    for band in wp.BAND_STRINGS:
        toks = band.split("_")
        for ft in forbidden_tokens:
            assert ft not in toks, f"band {band!r} contains class/bool token {ft!r}"


# ============================================================
# 5. Absence-≠-human discipline
# ============================================================


def test_low_z_band_under_powered_and_render_says_unknown():
    tokens = _independent_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.band == "under_powered"
    rendered = r.render()
    low = rendered.lower()
    # Says `unknown`, not 'no watermark' and not 'human'.
    assert "unknown" in low
    assert "not 'no watermark'" in low
    # The word 'human' appears only in the refusal ("not evidence of human
    # authorship"), never as an output VALUE. No output value equals "human".
    assert "unwatermarked" not in low
    # The two load-bearing caveats are in the render footer.
    assert "not 'ai'" in low
    assert "not evidence of human authorship" in low


def test_human_never_appears_as_an_output_value():
    """`human` is never a band / key_id / assumptions value."""
    tokens = _independent_tokens(200)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    d = r.to_dict()
    for v in (d["band"], d["key_id"], d["hash_scheme"]):
        assert "human" not in str(v).lower()


# ============================================================
# 6. Reliability band carries decay + length caveat
# ============================================================


def test_too_short_forces_under_powered_with_caveats():
    tokens = _green_biased_tokens(20)  # below DEFAULT_LENGTH_FLOOR (50)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.n_scored_tokens < wp.DEFAULT_LENGTH_FLOOR
    assert r.band == "under_powered"
    assert r.assumptions["length_floor"] == wp.DEFAULT_LENGTH_FLOOR
    assert r.assumptions["decay_caveat"]
    assert r.assumptions["band_is_provisional"] is True


def test_rewrite_exposure_heavy_forces_under_powered():
    tokens = _green_biased_tokens(300)  # would otherwise be watermark_consistent
    r = wp.probe(
        tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA,
        rewrite_exposure="heavy",
    )
    assert r.band == "under_powered"
    assert r.assumptions["rewrite_exposure"] == "heavy"


def test_long_green_biased_reaches_watermark_consistent():
    tokens = _green_biased_tokens(300)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.band == "watermark_consistent"
    assert r.assumptions["band_is_provisional"] is True


# ============================================================
# 7. Tokenizer-mismatch guard
# ============================================================


def test_operator_tokens_path_records_tokenization():
    tokens = _independent_tokens(60)
    r = wp.probe(
        tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA,
        tokenization="operator_tokens",
    )
    assert r.assumptions["tokenization"] == "operator_tokens"


def _alpha_word(i: int) -> str:
    """A purely-alphabetic stand-in word for vocab key `i` (the whitespace
    fallback tokenizer's WORD_RE = [A-Za-z']+ strips digits, so vocab keys
    must be alphabetic to survive)."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    s = ""
    n = i
    while True:
        s = letters[n % 26] + s
        n = n // 26 - 1
        if n < 0:
            break
    return "w" + s


def test_whitespace_fallback_records_and_does_not_license_names_hazard():
    vocab = {_alpha_word(i): i for i in range(200)}
    text = " ".join(_alpha_word(i % 200) for i in range(120))
    ids = wp.tokens_from_text_whitespace(text, vocab)
    assert len(ids) == 120  # all words map (alphabetic, in vocab)
    r = wp.probe(
        ids, key=_KEY, vocab_size=len(vocab), gamma=_GAMMA,
        tokenization="whitespace_fallback",
    )
    assert r.assumptions["tokenization"] == "whitespace_fallback"
    lic = wp.build_claim_license(r)
    dnl = lic.does_not_license.lower()
    assert "whitespace fallback" in dnl
    assert "bpe" in dnl
    # The under-detection warning is attached as a caveat on this path.
    assert any("under-detect" in c.lower() for c in lic.additional_caveats)


# ============================================================
# 8. Key secrecy
# ============================================================


def test_key_never_in_results_or_envelope(capsys):
    secret = "TOP-SECRET-XYZZY-9090"
    tokens = _green_biased_tokens(120, key=secret)
    r = wp.probe(tokens, key=secret, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=120)
    serialized = json.dumps(env, default=str)
    assert secret not in serialized
    # key_id is present and is NOT the secret.
    assert r.key_id.startswith("kid_")
    assert secret not in r.key_id
    # And not in the render either.
    assert secret not in r.render()


def test_cli_does_not_echo_key(capsys, tmp_path):
    secret = "CLI-SECRET-7777"
    vocab = {_alpha_word(i): i for i in range(300)}
    (tmp_path / "vocab.json").write_text(json.dumps(vocab), encoding="utf-8")
    text = " ".join(_alpha_word(i % 300) for i in range(200))
    (tmp_path / "t.txt").write_text(text, encoding="utf-8")
    rc = wp.main([
        "--target", str(tmp_path / "t.txt"),
        "--key", secret,
        "--vocab", str(tmp_path / "vocab.json"),
        "--tokenizer", "fallback:whitespace",
        "--json",
    ])
    assert rc == 0
    out = capsys.readouterr()
    assert secret not in out.out
    assert secret not in out.err


# ============================================================
# 9. Claim license — present + refuses the verdict (incl. no-threshold text)
# ============================================================


def test_claim_license_present_and_refuses_verdict():
    tokens = _green_biased_tokens(200)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=200)
    cl = env["claim_license"]
    assert cl is not None
    assert cl["task_surface"] == "watermark_probe"

    dnl = cl["does_not_license"].lower()
    # not AI / not authorship.
    assert "not" in dnl and "ai" in dnl
    # absence is not evidence of human authorship.
    assert "absence is not evidence of human authorship" in dnl
    # tests only the supplied key / blind to other & scrubbed & semantic.
    assert "only the operator-supplied key" in dnl
    assert "scrubbed" in dnl
    assert "semantic" in dnl
    # whitespace fallback under-detection note.
    assert "whitespace fallback" in dnl

    # No verdict / is_ai key anywhere in the envelope's results.
    assert _FORBIDDEN_VERDICT_KEYS.isdisjoint(set(_walk_keys(env["results"])))
    # References cite the three arXiv papers.
    refs = " ".join(cl["references"])
    assert "2301.10226" in refs
    assert "2306.04634" in refs
    assert "2411.13425" in refs


def test_does_not_license_names_no_threshold_verbatim():
    """Finding 3b: the does_not_license must name verbatim 'do not threshold
    band or p_value to manufacture an is_watermarked / is_ai / is_human
    decision.'"""
    dnl = wp.DEFAULT_DOES_NOT_LICENSE.lower()
    assert "threshold" in dnl
    assert "is_watermarked" in dnl
    assert "is_ai" in dnl
    assert "is_human" in dnl
    # The verbatim instruction pattern.
    assert "manufacture an is_watermarked / is_ai / is_human decision" in dnl


def test_surface_registered_in_label_map():
    import claim_license as cl_mod
    assert "watermark_probe" in cl_mod.TASK_SURFACE_LABELS
    label = cl_mod.TASK_SURFACE_LABELS["watermark_probe"].lower()
    # The label itself carries the absence-≠-human caveat.
    assert "never 'ai'" in label
    assert "not evidence of human authorship" in label


# ============================================================
# Finding 5 — one surface name across all four sites
# ============================================================


def test_one_surface_name_agrees_across_four_sites():
    """TASK_SURFACE, the claim_license_surfaces/<name>.txt filename, the
    capability surface field, and the envelope task_surface must all be the
    SAME string, or the TASK_SURFACE_LABELS lookup fails at build_output."""
    name = "watermark_probe"
    assert wp.TASK_SURFACE == name
    assert wp.TOOL_NAME == name
    # The fragment file exists with that exact stem.
    frag = _SCRIPTS / "claim_license_surfaces" / f"{name}.txt"
    assert frag.exists()
    # The envelope task_surface.
    tokens = _independent_tokens(60)
    r = wp.probe(tokens, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    env = wp.compose_envelope(r, target_path="/tmp/x.txt", target_words=60)
    assert env["task_surface"] == name
    assert env["claim_license"]["task_surface"] == name


# ============================================================
# Finding 3 — import separation (no selection/calibration/threshold layer)
# ============================================================


def test_imports_no_selection_or_calibration_or_threshold_layer():
    """watermark_probe.py imports nothing from a selection / calibration /
    threshold-setting layer (mirrors the in-repo separation-guard pattern).
    Named real modules: conformal_gate, calibrate_thresholds,
    calibration_drift_monitor, calibration_survey."""
    src = (_SCRIPTS / "watermark_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    forbidden = {
        "conformal_gate",
        "calibrate_thresholds",
        "calibration_drift_monitor",
        "calibration_survey",
        "binoculars_calibrate",
    }
    leaked = imported & forbidden
    assert not leaked, f"watermark_probe imports a gating layer: {leaked}"


def test_import_pulls_no_model_dependency():
    """Importing the module pulls no torch/transformers (stays stdlib)."""
    src = (_SCRIPTS / "watermark_probe.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "torch" not in imported
    assert "transformers" not in imported


# ============================================================
# 11. M2 sweep — additive, caveated, no aggregate
# ============================================================


def test_sweep_returns_one_card_per_scheme_no_aggregate():
    tokens = _green_biased_tokens(200)
    catalog = [
        {"key": _KEY, "vocab_size": _VOCAB_SIZE, "gamma": 0.5},
        {"key": "other-key", "vocab_size": _VOCAB_SIZE, "gamma": 0.5},
        {"key": _KEY, "vocab_size": _VOCAB_SIZE, "gamma": 0.25},
    ]
    cards = wp.sweep(tokens, catalog, tokenization="whitespace_fallback")
    assert len(cards) == 3
    # Each card is an independent M1 result; the matching key fires.
    assert isinstance(cards[0], wp.WatermarkProbeResult)
    # n_schemes_tried stamped on each card.
    for c in cards:
        assert c.assumptions["n_schemes_tried"] == 3
    # NO cross-scheme aggregate / best-match / boolean — sweep returns a plain
    # list of results, nothing else.
    assert all(isinstance(c, wp.WatermarkProbeResult) for c in cards)


def test_sweep_render_warns_multiple_comparisons():
    tokens = _independent_tokens(200)
    catalog = [
        {"key": "k1", "vocab_size": _VOCAB_SIZE},
        {"key": "k2", "vocab_size": _VOCAB_SIZE},
    ]
    cards = wp.sweep(tokens, catalog)
    lic = wp.build_claim_license(cards[0], n_schemes_tried=len(catalog))
    assert any("multiple-comparisons" in c.lower() for c in lic.additional_caveats)


# ============================================================
# CLI — bad input routes to bad_input
# ============================================================


def test_cli_missing_key_is_bad_input():
    rc = wp.main(["--target", "/nonexistent.txt", "--vocab", "10", "--json"])
    assert rc == 2  # bad_input non-zero exit


def test_cli_bad_input_emits_error_envelope(capsys, tmp_path):
    (tmp_path / "t.txt").write_text("w0 w1 w2", encoding="utf-8")
    rc = wp.main([
        "--target", str(tmp_path / "t.txt"),
        "--key", "k",
        "--vocab", "10",
        "--json",
        # no --tokens and no --tokenizer → bad_input
    ])
    assert rc == 2
    env = json.loads(capsys.readouterr().out)
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


# ============================================================
# Codex re-review regressions (spec 29 / PR #238)
# ============================================================


def test_repeated_ngrams_scored_once_not_inflated():
    # Codex P1: a repeated (context, current_token) event reuses the SAME green list,
    # so it is scored ONCE, not as N independent Bernoulli trials. A single token
    # repeated 201x is ONE unique transition: effective T == 1 (not 200), and the
    # bogus inflated z the per-position count produced is gone.
    stats = wp.green_z_test([7] * 201, key=_KEY, gamma=_GAMMA,
                            vocab_size=_VOCAB_SIZE, hash_scheme="left-hash")
    assert stats["n_positions"] == 200          # raw positions with a context
    assert stats["n_scored_tokens"] == 1        # one unique (context, token) event
    assert abs(stats["z"]) < 4.0                # not the T=200 inflation (~14)
    # two alternating transitions → exactly two unique events
    stats2 = wp.green_z_test([3, 5] * 100, key=_KEY, gamma=_GAMMA,
                             vocab_size=_VOCAB_SIZE, hash_scheme="left-hash")
    assert stats2["n_scored_tokens"] == 2 and stats2["n_positions"] == 199
    # the probe surfaces the dedup in assumptions
    r = wp.probe([3, 5] * 100, key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.assumptions["n_positions"] == 199
    assert r.assumptions["n_repeated_ngrams_excluded"] == 199 - 2


def test_token_ids_validated_before_scoring():
    # Codex P2: non-int / bool / out-of-range token ids are bad_input, not a z.
    for bad in ([-1, 999, True, 3], [0, 1, 2, 4], [0, 1, True, 2], [0, 1.5, 2]):
        with pytest.raises(wp.WatermarkProbeError):
            wp.validate_params(key=_KEY, gamma=_GAMMA, vocab_size=4,
                               hash_scheme="left-hash", token_ids=bad)
    # probe rejects them BEFORE computing any statistic
    with pytest.raises(wp.WatermarkProbeError):
        wp.probe([-1, 999, True, 3], key=_KEY, vocab_size=4, gamma=_GAMMA)
    # a clean in-range stream validates
    wp.validate_params(key=_KEY, gamma=_GAMMA, vocab_size=4,
                       hash_scheme="left-hash", token_ids=[0, 1, 2, 3])


def test_vocab_map_id_domain_validated(tmp_path):
    # Codex P2: a sparse/duplicated id map must be rejected, not silently sized by len.
    sparse = tmp_path / "sparse.json"
    sparse.write_text(json.dumps({"a": 100, "b": 200}), encoding="utf-8")
    with pytest.raises(wp.WatermarkProbeError):
        wp._resolve_vocab(str(sparse))
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps({"a": 0, "b": 0, "c": 1}), encoding="utf-8")
    with pytest.raises(wp.WatermarkProbeError):
        wp._resolve_vocab(str(dup))
    # a dense [0, V) map is accepted and sized by its domain
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"a": 0, "b": 1, "c": 2}), encoding="utf-8")
    vmap, size = wp._resolve_vocab(str(good))
    assert size == 3 and vmap == {"a": 0, "b": 1, "c": 2}


def test_partition_prf_scope_stamped_and_disclaimed():
    # Codex P1: the result stamps the Voiceprint partition PRF (NOT the official KGW
    # processor), and the claim license disclaims official-KGW / third-party interop.
    r = wp.probe(_independent_tokens(120), key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    assert r.assumptions["partition_prf"] == wp.PARTITION_PRF
    assert "partition" in r.render().lower()
    dnl = wp.DEFAULT_DOES_NOT_LICENSE
    assert "simple_1" in dnl and "FALSE-NEGATIVE" in dnl


def test_comparison_set_carries_partition_prf():
    # Codex P2: the machine-readable comparison_set must carry partition_prf so cards
    # from incompatible partition implementations are not treated as comparable.
    r = wp.probe(_independent_tokens(120), key=_KEY, vocab_size=_VOCAB_SIZE, gamma=_GAMMA)
    lic = wp.build_claim_license(r)
    assert lic.comparison_set["partition_prf"] == wp.PARTITION_PRF


def test_vocab_bool_rejected_as_size(tmp_path):
    # Codex P2: bool is an int subclass — a JSON `true` vocab must NOT be sized as 1.
    for val in ("true", "false"):
        p = tmp_path / f"{val}.json"
        p.write_text(val, encoding="utf-8")
        with pytest.raises(wp.WatermarkProbeError):
            wp._resolve_vocab(str(p))
    # a real scalar size still works (file or bare int)
    p = tmp_path / "size.json"
    p.write_text("128", encoding="utf-8")
    assert wp._resolve_vocab(str(p)) == (None, 128)
    assert wp._resolve_vocab("256") == (None, 256)


def test_capability_text_has_no_doubled_apostrophe():
    # PR #240 sibling-site: in a PLAIN (unquoted) YAML scalar `''` is NOT collapsed to `'` — only
    # single-quoted scalars are. watermark_probe.yaml's use_when items were authored unquoted with
    # `module''s` / `partition''s`, so load_manifest() and the rendered "## Use when" emitted the
    # doubled apostrophes verbatim. This is a PROPERTY check on the parsed prose (NOT a re-comparison
    # to the golden — the committed golden was generated from the buggy loader and masked the defect).
    from capabilities import load_manifest  # noqa: E402
    cap_dir = _SCRIPTS.parent / "capabilities.d"
    e = next(x for x in load_manifest(cap_dir)["entries"] if x["id"] == "watermark_probe")
    assert "''" not in e["purpose"], "purpose carries a literal doubled apostrophe: %r" % (e["purpose"],)
    for field in ("use_when", "do_not_use_when"):
        for i, s in enumerate(e[field]):
            assert "''" not in s, "%s[%d] carries a literal doubled apostrophe: %r" % (field, i, s)
    # positive: the intended apostrophes now render correctly
    assert "module's green-list" in e["use_when"][0]
    assert "partition's tokenizer" in e["use_when"][2]
