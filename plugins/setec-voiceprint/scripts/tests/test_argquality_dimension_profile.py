"""Tests for argquality_dimension_profile.py (ArgScope M1) + argquality_judge.py.

Torch-free: the LLM judge is exercised ONLY through the deterministic `mock`
backend (and a monkeypatched build_judge for the missing-SDK case). The central
contract is the HARD no-verdict / no-aggregate POSTURE — enforced in the data
shape, not just prose:

  * exactly three independent per-dimension {band, evidence_spans, basis} fields;
  * NO overall / quality / score / aggregate / mean_band / verdict key anywhere
    in results (RECURSIVE key walk — spec-30 P3), and NO numeric leaf under the
    dimensions block (band/spans/basis are strings; leaf-level band-vs-grade);
  * NO module function collapses the three bands to one scalar (structural);
  * null is first-class (the judge declined) and NEVER coerced to `lower`;
  * own-prompt fingerprint, asserted to differ from the sibling judges.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import argument_judge  # type: ignore
import argquality_dimension_profile as aqp  # type: ignore
import argquality_judge  # type: ignore
import fallacy_judge  # type: ignore

# A two-paragraph argument-shaped passage with inferential connectives (so no
# register warning fires on the happy path) and enough words to clear MIN_WORDS.
SAMPLE = (
    "Everyone already knows the policy works, so anyone who doubts it simply "
    "hasn't been paying attention. Because the experts all agree, there is no "
    "real debate to be had here. The data clearly supports the position, and "
    "therefore the position is supported by the data, which is why we proceed.\n\n"
    "We face a stark choice: either we adopt this reform in full, or we accept "
    "the total collapse of the system. Therefore the only responsible vote is "
    "yes. However, some have argued the opposite, and those concerns deserve a "
    "fair hearing before we decide anything final about the matter at hand here."
)

# Substrings that would smuggle a verdict / aggregate / grade into the data
# shape — the operator's hard line. Checked RECURSIVELY over every key, every
# depth (the validate_results_bounds traversal shape; spec-30 P3).
_BANNED_KEY_SUBSTRINGS = (
    "overall", "quality", "score", "aggregate", "verdict", "mean_band",
    "rating", "grade", "is_good", "is_bad",
)


def _run(tmp_path, text=SAMPLE, *args):
    target = tmp_path / "arg.txt"
    target.write_text(text, encoding="utf-8")
    out = tmp_path / "arg.json"
    argv = [str(target), "--out", str(out), "--out-md", str(tmp_path / "arg.md"), *args]
    if "--judge" not in args:                 # --judge is REQUIRED; tests opt into the mock stub
        argv += ["--judge", "mock"]
    rc = aqp.main(argv)
    env = json.loads(out.read_text(encoding="utf-8"))
    return rc, env


def _results(env):
    # success envelope nests results; error envelope is flat-ish — handle both.
    return env.get("results", env)


def _walk_keys(obj, _prefix=""):
    """Yield every (dotted_path, key) at every depth of a nested dict/list."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{_prefix}.{k}" if _prefix else str(k)
            yield path, str(k)
            yield from _walk_keys(v, path)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            yield from _walk_keys(item, f"{_prefix}[{i}]")


def _numeric_leaves(obj):
    """Yield every numeric (non-bool) leaf in a nested structure."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield obj
        return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _numeric_leaves(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _numeric_leaves(item)


# ============ #1 — three-band data shape ============================
def test_three_band_data_shape(tmp_path):
    rc, env = _run(tmp_path)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    dims = r["dimensions"]
    # exactly the three top-tier dimensions
    assert set(dims) == {"logic", "rhetoric", "dialectic"}
    for d, entry in dims.items():
        assert set(entry) == {"band", "evidence_spans", "basis"}
        assert entry["band"] in ("lower", "mid", "higher", None)
        assert isinstance(entry["evidence_spans"], list)
        assert all(isinstance(s, str) for s in entry["evidence_spans"])
        assert isinstance(entry["basis"], str)
    # mock fixture: logic=higher, rhetoric=mid, dialectic=null (declined)
    assert dims["logic"]["band"] == "higher"
    assert dims["rhetoric"]["band"] == "mid"
    assert dims["dialectic"]["band"] is None
    assert r["calibration_status"] == "uncalibrated"
    assert isinstance(r["distribution_reference"], str) and r["distribution_reference"]


# ============ #2 — no-aggregate / no-verdict DATA-SHAPE guard =======
def test_no_aggregate_no_verdict_keys_recursive(tmp_path):
    """RECURSIVE key walk: the banned substrings must be absent from EVERY key
    at EVERY depth of results — so a nested dimensions.logic.score could not pass
    vacuously (spec-30 P3)."""
    _, env = _run(tmp_path)
    r = _results(env)
    for path, key in _walk_keys(r):
        low = key.lower()
        for banned in _BANNED_KEY_SUBSTRINGS:
            assert banned not in low, f"forbidden key substring {banned!r} at {path}"


def test_no_numeric_leaf_under_dimensions(tmp_path):
    """Leaf-level band-vs-grade: there is literally NO numeric leaf anywhere under
    results.dimensions.* (band/spans/basis are all strings) — nothing to
    threshold into a grade (spec-30 P3)."""
    _, env = _run(tmp_path)
    dims = _results(env)["dimensions"]
    leaves = list(_numeric_leaves(dims))
    assert leaves == [], f"unexpected numeric leaf under dimensions: {leaves}"


def test_distribution_reference_is_string_no_numeric(tmp_path):
    _, env = _run(tmp_path)
    r = _results(env)
    assert isinstance(r["distribution_reference"], str)
    assert list(_numeric_leaves(r["distribution_reference"])) == []


# ============ #3 — no cross-dimension roll-up (structural) ==========
def test_no_function_collapses_profile_to_scalar():
    """Structural guard (the analogue of spec-18's no-transform guard): no
    module-level function in argquality_dimension_profile or argquality_judge
    returns a single scalar/aggregate over the three dimension bands. We assert
    no public function NAME suggests a roll-up, and that the surface exposes no
    aggregate accessor."""
    bad_name = re.compile(
        r"(overall|aggregate|mean_band|roll_?up|combine|sum_|_sum|quality_score|"
        r"total_band|score_dims|collapse)",
        re.IGNORECASE,
    )
    for mod in (aqp, argquality_judge):
        for name, obj in vars(mod).items():
            if name.startswith("_"):
                continue
            if inspect.isfunction(obj) and obj.__module__ == mod.__name__:
                assert not bad_name.search(name), (
                    f"{mod.__name__}.{name} looks like a profile-collapsing "
                    f"roll-up — the profile must stay a profile"
                )
    # And there is no module constant exposing an aggregate band list/key.
    assert not hasattr(aqp, "AGGREGATE_KEY")
    assert not hasattr(aqp, "OVERALL_BAND")


# ============ #4 — null-discipline (first-class, never coerced) =====
def test_null_band_is_first_class_not_lower(tmp_path):
    _, env = _run(tmp_path)
    dims = _results(env)["dimensions"]
    # the mock declines dialectic -> band is None, NOT "lower"
    assert dims["dialectic"]["band"] is None
    assert dims["dialectic"]["band"] != "lower"
    # a declined dimension carries no evidence spans (absence != evidence)
    assert dims["dialectic"]["evidence_spans"] == []


def test_normalize_declines_unknown_band_to_null_never_lower():
    paras = ["a paragraph with some real words inside it for the span check"]
    out = argquality_judge.normalize_dimensions(
        {
            "logic": {"band": "higher", "evidence_spans": ["a paragraph with some"],
                      "basis": "ok"},
            "rhetoric": {"band": "NONSENSE", "evidence_spans": [], "basis": ""},  # bad band
            "dialectic": {"band": None, "evidence_spans": ["ignored"], "basis": ""},  # declined
            # 'logic' present, but if a dimension is missing entirely it must still
            # appear as a declined entry:
        },
        paras,
    )
    assert set(out) == {"logic", "rhetoric", "dialectic"}
    assert out["logic"]["band"] == "higher"
    # an unrecognized band becomes null (declined), NEVER coerced to "lower"
    assert out["rhetoric"]["band"] is None
    assert out["rhetoric"]["band"] != "lower"
    # a declined dimension drops its spans
    assert out["dialectic"]["band"] is None
    assert out["dialectic"]["evidence_spans"] == []


def test_missing_dimension_becomes_declined():
    paras = ["some words here for the paragraph"]
    out = argquality_judge.normalize_dimensions({"logic": {"band": "mid"}}, paras)
    assert set(out) == {"logic", "rhetoric", "dialectic"}
    assert out["rhetoric"]["band"] is None and out["dialectic"]["band"] is None


# ============ #5 — span anchoring (verbatim, paragraph-anchored) ====
def test_evidence_spans_are_verbatim_paragraph_substrings(tmp_path):
    _, env = _run(tmp_path)
    dims = _results(env)["dimensions"]
    paras = SAMPLE.split("\n\n")
    norm_paras = [" ".join(p.split()) for p in paras]
    for d, entry in dims.items():
        for span in entry["evidence_spans"]:
            ns = " ".join(span.split())
            assert any(ns in p for p in norm_paras), (
                f"{d} span not a verbatim paragraph substring: {span!r}"
            )


def test_normalize_drops_hallucinated_span():
    paras = ["paragraph zero has some words", "paragraph one contains a real span inside it"]
    out = argquality_judge.normalize_dimensions(
        {
            "logic": {
                "band": "higher",
                "evidence_spans": [
                    "a real span inside it",            # verbatim -> kept
                    "a span the judge never quoted",    # hallucinated -> dropped
                    "  ",                                # empty -> dropped
                ],
                "basis": "x",
            }
        },
        paras,
    )
    assert out["logic"]["evidence_spans"] == ["a real span inside it"]


def test_normalize_tolerates_requoted_whitespace():
    paras = ["the experts\n  all agree, so there is no debate"]
    out = argquality_judge.normalize_dimensions(
        {"logic": {"band": "mid", "evidence_spans": ["the experts all agree"], "basis": ""}},
        paras,
    )
    assert out["logic"]["evidence_spans"] == ["the experts all agree"]


# ============ #6 — claim-license refuses verdict ====================
def test_claim_license_refuses_verdict(tmp_path):
    _, env = _run(tmp_path)
    cl = env["claim_license"]
    dnl = cl["does_not_license"].lower()
    assert "argument quality" in dnl or "quality" in dnl
    assert "overall" in dnl
    for word in ("good", "bad", "strong", "weak"):
        assert word in dnl, f"license should refuse '{word}'"
    # AI-vs-human refusal
    assert "ai" in dnl and ("tell" in dnl or "provenance" in dnl)
    # the "a lower band is frequently appropriate in context" framing
    assert "frequently appropriate in context" in dnl
    # calibration line
    assert "uncalibrated" in dnl
    assert _results(env)["calibration_status"] == "uncalibrated"


# ============ #7 — caveats / abstention =============================
def test_register_warning_is_soft_not_abstain(tmp_path):
    # Above the 25-word hard floor but no inferential connectives -> soft
    # register_warnings, still available (NOT a hard register abstain).
    text = (
        "Cats are nice animals. Dogs are nice animals. Birds are nice animals. "
        "Fish are nice animals. Rabbits are nice animals. Horses are nice animals. "
        "Lizards are nice animals. Turtles are nice animals as well in my view."
    )
    rc, env = _run(tmp_path, text)
    assert rc == 0 and env["available"] is True
    assert _results(env)["register_warnings"], "expected a soft register caveat"


def test_short_input_bad_input(tmp_path):
    rc, env = _run(tmp_path, "too short here")
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_fingerprint_drift_abstains(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", "deadbeef")
    assert env["available"] is False
    assert "drift" in json.dumps(env).lower()


def test_missing_sdk_is_missing_dependency(tmp_path, monkeypatch):
    def _raise(*a, **k):
        raise aqp.JudgeError(
            "anthropic backend requires the `anthropic` SDK; pip install first."
        )
    monkeypatch.setattr(aqp, "build_judge", _raise)
    rc, env = _run(tmp_path, SAMPLE, "--judge", "anthropic", "--judge-model", "x")
    assert env["available"] is False
    assert "missing_dependency" in json.dumps(env)


def test_judge_is_required(tmp_path):
    # no --judge default: a bare run must NOT silently fall back to the fabricating mock.
    target = tmp_path / "arg.txt"
    target.write_text(SAMPLE, encoding="utf-8")
    try:
        aqp.main([str(target), "--out", str(tmp_path / "o.json")])  # no --judge
        raise AssertionError("expected SystemExit (--judge required)")
    except SystemExit as e:
        assert e.code == 2


def test_invalid_utf8_target_is_bad_input(tmp_path):
    target = tmp_path / "bad.txt"
    target.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    out = tmp_path / "o.json"
    rc = aqp.main([str(target), "--judge", "mock", "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False and "bad_input" in json.dumps(env)


# ============ #8 — provenance: OWN fingerprint ======================
def test_own_prompt_fingerprint_differs_from_siblings(tmp_path):
    _, env = _run(tmp_path)
    r = _results(env)
    fp = r["prompt_fingerprint_sha256"]
    assert fp == argquality_judge.fingerprint_prompt()
    # MUST differ from BOTH sibling judges — a shared fingerprint would silently
    # defeat a drift gate keyed to this surface (spec-26 P1, spec-30 #8).
    assert fp != fallacy_judge.fingerprint_prompt()
    assert fp != argument_judge.fingerprint_prompt()
    assert r["judge"]["judge_identity"]["kind"] == "mock"


def test_mock_labelled_as_stub(tmp_path):
    _, env = _run(tmp_path)
    assert any("mock" in w and "stub" in w.lower() for w in env.get("warnings", []))


# ============ judge unit: mock determinism + dispatch ===============
def test_mock_judge_deterministic():
    j = argquality_judge.build_judge("mock")
    a = j(["one two three four five", "six seven eight nine ten"]).values["dimensions"]
    b = j(["one two three four five", "six seven eight nine ten"]).values["dimensions"]
    assert a == b
    assert a["logic"]["band"] == "higher"
    assert a["rhetoric"]["band"] == "mid"
    assert a["dialectic"]["band"] is None


def test_build_judge_dispatch_boundary(monkeypatch):
    """spec-30 P3: build_judge OWNS the mock/manifest dispatch; only the 3 API
    kinds delegate to judge_backends.make_api_judge. mock/manifest must NEVER
    route into make_api_judge."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("make_api_judge must not be called for mock/manifest")

    monkeypatch.setattr(argquality_judge.judge_backends, "make_api_judge", _boom)
    # mock + manifest dispatch locally (no make_api_judge):
    argquality_judge.build_judge("mock")
    assert called["n"] == 0
    with pytest.raises(argquality_judge.JudgeError):
        argquality_judge.build_judge("manifest")  # missing manifest_path -> own error
    assert called["n"] == 0
    # unknown kind is a JudgeError, not a delegation
    with pytest.raises(argquality_judge.JudgeError):
        argquality_judge.build_judge("nonsense")
    assert called["n"] == 0


def test_manifest_judge_reads_dimensions(tmp_path):
    manifest = tmp_path / "bands.json"
    manifest.write_text(json.dumps({
        "values": {"dimensions": {
            "logic": {"band": "lower", "evidence_spans": [], "basis": "m"},
            "rhetoric": {"band": "higher", "evidence_spans": [], "basis": "m"},
            "dialectic": {"band": None, "evidence_spans": [], "basis": "m"},
        }},
        "judge_identity": {"model": "precomputed-x"},
    }), encoding="utf-8")
    j = argquality_judge.build_judge("manifest", manifest_path=manifest)
    out = j(["a paragraph of words"]).values["dimensions"]
    assert out["logic"]["band"] == "lower"
    assert out["rhetoric"]["band"] == "higher"
    assert out["dialectic"]["band"] is None


# ============ #9 — both-goldens registration (drop-in) ==============
def test_surface_registered_in_claim_license_labels():
    import claim_license  # type: ignore
    assert "argquality_dimension_profile" in claim_license.TASK_SURFACE_LABELS
    label = claim_license.TASK_SURFACE_LABELS["argquality_dimension_profile"].lower()
    assert "no aggregate" in label or "no overall" in label


def test_surface_in_valid_task_surfaces():
    import output_schema  # type: ignore
    assert "argquality_dimension_profile" in output_schema.VALID_TASK_SURFACES
