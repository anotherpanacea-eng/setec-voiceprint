#!/usr/bin/env python3
"""Tests for tocsin_audit.py — the TOCSIN token-cohesiveness surface (spec 31, M1).

Every test runs the default stdlib path or a DETERMINISTIC STUB semantic_diff. No
embedding model is ever loaded or imported: the real embedding_backend path is an
M2 seam exercised only behind main() (not touched here). The numbered tests map to
the spec's AC-1..AC-8.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import tocsin_audit as tc  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    VALID_TASK_SURFACES,
    OutputValidityError,
)
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures / helpers.
# ----------------------------------------------------------------------

def _make_text(n_words: int) -> str:
    # Distinct-enough words so word_tokens yields ~n_words tokens with a real
    # vocabulary (so deletion actually changes the token set).
    return " ".join(f"word{i} content phrase here" for i in range(n_words // 4 + 1))


_ALLOWED_BANDS = {"indeterminate", "low_cohesiveness", "high_cohesiveness"}

# Forbidden *key* substrings (recursive key walk). These name an authorship
# inference / selection target and must never appear as a key at any depth.
_BANNED_KEY_SUBSTRINGS = (
    "is_ai", "is_human", "ai_generated", "human_written", "label",
    "prediction", "classification", "verdict", "decision", "p_ai", "prob_ai",
)
# Forbidden categorical *values* (exact, case-insensitive). A leaf string EQUAL
# to one of these would be a smuggled verdict token. (Substring would false-flag
# the claim-license refusal prose, which legitimately says "not a verdict" etc.;
# the categorical leaves in this envelope are short tokens, so exact-match is the
# right guard.)
_BANNED_VALUE_TOKENS = {
    "is_ai", "is_human", "ai_generated", "human_written", "ai", "human",
    "prediction", "classification", "verdict", "decision",
}


def _walk_keys(obj, _prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{_prefix}.{k}" if _prefix else str(k)
            yield path, str(k)
            yield from _walk_keys(v, path)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            yield from _walk_keys(item, f"{_prefix}[{i}]")


def _walk_string_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_string_values(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_string_values(item)
    elif isinstance(obj, str):
        yield obj


# ----------------------------------------------------------------------
# Surface registration.
# ----------------------------------------------------------------------

def test_surface_registered():
    assert tc.TASK_SURFACE == "token_cohesiveness"
    assert "token_cohesiveness" in VALID_TASK_SURFACES
    assert "token_cohesiveness" in TASK_SURFACE_LABELS


# ----------------------------------------------------------------------
# AC-1 — result shape + determinism.
# ----------------------------------------------------------------------

def test_ac1_result_shape_and_bounds():
    text = _make_text(400)
    r = tc.audit_tocsin(text)
    # exact §3 keys present
    for key in (
        "token_cohesiveness", "cohesiveness_sd", "mean_semantic_diff",
        "n_perturbations", "deletion_fraction", "deletion_unit", "seed",
        "target_tokens", "semantic_diff_backend", "band", "assumptions",
    ):
        assert key in r, f"missing results key {key!r}"
    assert 0.0 <= r["token_cohesiveness"] <= 1.0
    assert r["cohesiveness_sd"] >= 0.0
    assert 0.0 <= r["mean_semantic_diff"] <= 1.0
    assert isinstance(r["n_perturbations"], int)
    assert isinstance(r["target_tokens"], int)
    assert r["deletion_unit"] == "word_token"
    # token_cohesiveness == 1 - mean_semantic_diff by construction
    assert math.isclose(
        r["token_cohesiveness"], 1.0 - r["mean_semantic_diff"], rel_tol=1e-12
    )
    assert r["semantic_diff_backend"]["kind"] == "lexical_overlap_stdlib"
    assert r["semantic_diff_backend"]["metric"] == "1 - jaccard(token_sets)"
    assert r["semantic_diff_backend"]["id"] is None


def test_ac1_deterministic_byte_identical():
    """Same input + seed -> byte-identical results (golden-testable determinism)."""
    text = _make_text(400)
    r1 = tc.audit_tocsin(text, seed=1729)
    r2 = tc.audit_tocsin(text, seed=1729)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)
    # A different seed generally changes the draw (not a hard guarantee, but the
    # engine is genuinely seed-driven): assert the seed is recorded so a run is
    # reproducible.
    assert r1["seed"] == 1729


def test_ac1_injectable_semantic_diff_seam():
    """semantic_diff is injectable (the M1/M2 seam). A stub that returns a fixed
    diff drives token_cohesiveness deterministically."""
    text = _make_text(400)
    calls = {"n": 0}

    def stub(original, perturbed):
        calls["n"] += 1
        assert isinstance(original, list) and isinstance(perturbed, list)
        return 0.25

    r = tc.audit_tocsin(text, semantic_diff=stub, n_perturbations=10)
    assert calls["n"] == 10
    assert math.isclose(r["mean_semantic_diff"], 0.25, rel_tol=1e-12)
    assert math.isclose(r["token_cohesiveness"], 0.75, rel_tol=1e-12)
    assert math.isclose(r["cohesiveness_sd"], 0.0, abs_tol=1e-12)  # constant diff


# ----------------------------------------------------------------------
# AC-2 — CLI happy path + error envelopes + exit codes.
# ----------------------------------------------------------------------

def _run_cli(argv, tmp_path):
    out_path = tmp_path / "env.json"
    rc = tc.main(argv + ["--json", "--out", str(out_path)])
    env = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    return rc, env


def test_ac2_cli_happy_path(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_make_text(400), encoding="utf-8")
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 0
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "token_cohesiveness"
    assert env["tool"] == "tocsin_audit"
    assert env["available"] is True
    assert env["claim_license"] is not None
    assert "token_cohesiveness" in env["results"]


def test_ac2_cli_text_too_short(tmp_path):
    target = tmp_path / "empty.txt"
    target.write_text("   \n  ", encoding="utf-8")
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "text_too_short"


def test_ac2_cli_bad_input_unreadable(tmp_path):
    missing = tmp_path / "nope.txt"
    rc, env = _run_cli(["--target", str(missing)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_ac2_cli_usage_error_deletion_fraction(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_make_text(400), encoding="utf-8")
    rc = tc.main(["--target", str(target), "--deletion-fraction", "1.5", "--json"])
    assert rc == 2  # usage error


def test_ac2_cli_short_text_warns_but_runs(tmp_path):
    target = tmp_path / "short.txt"
    target.write_text(_make_text(40), encoding="utf-8")  # below the 200 floor
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 0
    assert env["available"] is True
    assert any("floor" in w for w in env["warnings"])


# ----------------------------------------------------------------------
# AC-3 — no-verdict recursive walk over the FULL envelope.
# ----------------------------------------------------------------------

def test_ac3_no_verdict_keys_recursive():
    env = tc.compose_envelope(
        target_path="t.txt", target_words=400,
        results=tc.audit_tocsin(_make_text(400)),
    )
    for path, key in _walk_keys(env):
        low = key.lower()
        for banned in _BANNED_KEY_SUBSTRINGS:
            assert banned not in low, f"forbidden key substring {banned!r} at {path}"


def test_ac3_no_verdict_categorical_values_recursive():
    env = tc.compose_envelope(
        target_path="t.txt", target_words=400,
        results=tc.audit_tocsin(_make_text(400)),
    )
    # The ONLY categorical leaf permitted is band.band in the allowed set.
    assert env["results"]["band"]["band"] in _ALLOWED_BANDS
    # No string LEAF anywhere equals a smuggled verdict token (exact match, so
    # the claim-license refusal prose that says "not a verdict" doesn't trip).
    for s in _walk_string_values(env):
        assert s.strip().lower() not in _BANNED_VALUE_TOKENS, (
            f"forbidden categorical value {s!r}"
        )


def test_ac3_band_is_descriptive_over_own_axis():
    """The band names the MEASURED property (cohesiveness), not authorship."""
    # Force each band via a constant-diff stub.
    high = tc.audit_tocsin(_make_text(400), semantic_diff=lambda o, p: 0.05)
    low = tc.audit_tocsin(_make_text(400), semantic_diff=lambda o, p: 0.50)
    mid = tc.audit_tocsin(_make_text(400), semantic_diff=lambda o, p: 0.25)
    assert high["band"]["band"] == "high_cohesiveness"
    assert low["band"]["band"] == "low_cohesiveness"
    assert mid["band"]["band"] == "indeterminate"
    # flags are descriptive, never ai/human.
    for r in (high, low, mid):
        for f in r["band"]["flags"]:
            assert "ai" not in f and "human" not in f


# ----------------------------------------------------------------------
# AC-4 — never-selects (single-target only).
# ----------------------------------------------------------------------

def test_ac4_no_selection_entrypoint():
    """No multi-target / argmax / selection API exists; the CLI takes exactly one
    --target and audit_tocsin scores exactly one text."""
    import inspect

    # audit_tocsin's first positional is a single `text` (not a list/iterable of
    # texts), and there is no public name suggesting selection over texts.
    sig = inspect.signature(tc.audit_tocsin)
    params = list(sig.parameters)
    assert params[0] == "text"
    public = [n for n in dir(tc) if not n.startswith("_")]
    for banned in ("select", "argmax", "rank_texts", "which_is_ai", "classify"):
        assert banned not in public
    # The CLI arg is exactly one --target (required, single value).
    parser = tc.build_arg_parser()
    target_actions = [a for a in parser._actions if "--target" in a.option_strings]
    assert len(target_actions) == 1
    assert target_actions[0].required is True
    assert target_actions[0].nargs is None  # single value, not a list


# ----------------------------------------------------------------------
# AC-5 — anti-Goodhart held-out disjoint / honest tier.
# ----------------------------------------------------------------------

def test_ac5_band_ships_heuristic_user_baseline():
    r = tc.audit_tocsin(_make_text(400))
    assert r["band"]["calibration_status"] == "heuristic"
    assert r["band"]["calibration_anchor"] == "user-baseline-required"


# ----------------------------------------------------------------------
# AC-6 — stdlib-import / model-free.
# ----------------------------------------------------------------------

def test_ac6_no_model_imported_at_load():
    """The module must not pull a model dep at import; the default path runs with
    no transformers/torch/embedding_backend loaded."""
    # tocsin_audit is already imported above; assert no heavy dep is in
    # sys.modules as a *consequence* of importing it (the M2 seam is lazy).
    # We can't prove a clean process here, but we can assert the module does not
    # reference these at module scope: importing it did not add them.
    import importlib

    mod = importlib.reload(tc)
    # The default audit path runs with only stdlib.
    r = mod.audit_tocsin(_make_text(400))
    assert r["semantic_diff_backend"]["kind"] == "lexical_overlap_stdlib"
    # No embedding backend / transformers reference at module level.
    src = Path(mod.__file__).read_text(encoding="utf-8")
    # embedding_backend / transformers / torch must not be imported at top level
    # (only mentioned in docstrings/comments as the M2 seam).
    import ast

    tree = ast.parse(src)
    top_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_imports += [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            top_imports.append(node.module or "")
    for banned in ("transformers", "torch", "embedding_backend", "numpy"):
        assert not any(banned in imp for imp in top_imports), (
            f"{banned!r} imported at module top level"
        )


# ----------------------------------------------------------------------
# AC-7 — R4 bounds gate live on this surface.
# ----------------------------------------------------------------------

def test_ac7_valid_payload_passes_bounds():
    env = tc.compose_envelope(
        target_path="t.txt", target_words=400,
        results=tc.audit_tocsin(_make_text(400)),
    )  # build_output validates bounds by default; no raise == pass
    assert env["available"] is True


def test_ac7_injected_nan_raises_output_validity():
    """A stubbed semantic_diff that returns NaN poisons the payload; build_output's
    recursive R4 walk must reject it with OutputValidityError."""
    bad = tc.audit_tocsin(_make_text(400), semantic_diff=lambda o, p: float("nan"))
    with pytest.raises(OutputValidityError):
        tc.compose_envelope(target_path="t.txt", target_words=400, results=bad)


# ----------------------------------------------------------------------
# AC-8 — calibration honesty (manifest status vs band status).
# ----------------------------------------------------------------------

def test_ac8_manifest_status_vs_band_status_two_objects():
    import capabilities as cap  # type: ignore

    cap_dir = SCRIPTS.parent / "capabilities.d"
    m = cap.load_manifest(cap_dir)
    entry = {e["id"]: e for e in m["entries"]}["tocsin_audit"]
    # manifest status = the signal's literature footing
    assert entry["status"] == "literature_anchored"
    # band status = the threshold's anchoring tier (a DIFFERENT object)
    r = tc.audit_tocsin(_make_text(400))
    assert r["band"]["calibration_status"] == "heuristic"
    # the two must not be conflated
    assert entry["status"] != r["band"]["calibration_status"]


def test_ac8_fragment_tier_and_deps():
    """[P1]/[P2] folded: tier core, dependencies.python [] (M1 stdlib)."""
    import capabilities as cap  # type: ignore

    cap_dir = SCRIPTS.parent / "capabilities.d"
    m = cap.load_manifest(cap_dir)
    entry = {e["id"]: e for e in m["entries"]}["tocsin_audit"]
    assert entry["compute"]["tier"] == "core"
    assert entry["compute"]["length_floor_words"] == 200
    assert entry["dependencies"]["python"] == []
    assert entry["surface"] == "token_cohesiveness"


# ----------------------------------------------------------------------
# Claim license refuses a verdict.
# ----------------------------------------------------------------------

def test_claim_license_refuses_verdict():
    lic = tc._claim_license(tc.audit_tocsin(_make_text(400)))
    assert lic.task_surface == "token_cohesiveness"
    dn = lic.does_not_license.lower()
    assert "verdict" in dn and "ai" in dn and "human" in dn
    assert "is_ai" in dn or "label" in dn
    licenses = lic.licenses.lower()
    assert "cohesiveness" in licenses
    assert "not a verdict" in licenses or "measurement" in licenses


# ----------------------------------------------------------------------
# Math / bounds on saturated / tie / empty input.
# ----------------------------------------------------------------------

def test_jaccard_bounds_on_edge_inputs():
    # identical -> 0 distance
    assert tc.jaccard_distance(["a", "b"], ["a", "b"]) == 0.0
    # disjoint -> 1 distance
    assert tc.jaccard_distance(["a"], ["b"]) == 1.0
    # both empty -> defined as 0 (nothing differs)
    assert tc.jaccard_distance([], []) == 0.0
    # perturbed is a strict subset (deletion) -> in [0,1]
    d = tc.jaccard_distance(["a", "b", "c"], ["a", "b"])
    assert 0.0 <= d <= 1.0
    # bag-of-words: duplicates collapse to a set, so token_cohesiveness is a
    # SET-level measure (documented). identical multiset still 0.
    assert tc.jaccard_distance(["a", "a", "b"], ["a", "b"]) == 0.0


def test_delete_tokens_never_empties_under_fraction_lt_1():
    rng = __import__("random").Random(0)
    toks = [str(i) for i in range(100)]
    out = tc.delete_tokens(toks, 0.10, rng)
    assert len(out) == 90  # floor(0.10 * 100) = 10 deleted
    # order preserved among survivors
    assert out == [t for t in toks if t in set(out)]
    # tiny input: floor(0.10 * 3) = 0 deletions -> unchanged
    assert tc.delete_tokens(["a", "b", "c"], 0.10, rng) == ["a", "b", "c"]


def test_empty_target_raises_input_error():
    with pytest.raises(tc.TocsinInputError):
        tc.audit_tocsin("   ")


def test_deletion_fraction_out_of_range_raises():
    with pytest.raises(tc.TocsinInputError):
        tc.audit_tocsin(_make_text(400), deletion_fraction=0.0)
    with pytest.raises(tc.TocsinInputError):
        tc.audit_tocsin(_make_text(400), deletion_fraction=1.0)


def test_render_markdown_robust():
    env = tc.compose_envelope(
        target_path="t.txt", target_words=400,
        results=tc.audit_tocsin(_make_text(400)),
    )
    md = tc.render_markdown(env)
    assert "Token-Cohesiveness Audit" in md
    assert "token_cohesiveness" in md
    assert "NOT 'is AI'" in md
