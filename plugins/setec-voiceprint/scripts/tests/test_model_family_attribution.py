#!/usr/bin/env python3
"""Tests for model_family_attribution.py (spec 32, M1).

Stdlib, deterministic, torch-free, CI-runnable end to end. Covers the spec-32 test contract:
deterministic raw ranking; the no-posterior / no-verdict guards; REAL abstention (each gate trips with
its reason); standardization (a large-scale feature does not dominate); the fixed subspace (mdd in/out
uniformly); the claim-license refusals; robust input (bad_input); and self-exclusion.

The synthetic per-family corpora are built from controlled stylometric profiles. To avoid coupling to
spaCy availability, the feature-level tests drive `rank_families` directly on hand-built feature dicts;
the CLI tests use text corpora and assert the envelope shape (mdd is whatever the host offers, but the
ranking machinery is identical either way).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import model_family_attribution as mfa  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402


# ---- helpers -----------------------------------------------------------------

def _feat(b, mattr, mtld, fwr, mdd=None):
    """Build a feature dict in the named-feature space (mdd omitted => stdlib-only subspace)."""
    d = {"burstiness_B": b, "mattr": mattr, "mtld": mtld, "function_word_ratio": fwr}
    if mdd is not None:
        d["mean_dependency_distance"] = mdd
    return d


def _family(base, n=6, jitter=0.001):
    """n docs clustered tightly around `base` (a feature dict) so the centroid ~= base and the
    within-scatter is small but nonzero (a non-degenerate family)."""
    docs = []
    for i in range(n):
        eps = (i - n / 2) * jitter
        docs.append({k: v + eps for k, v in base.items()})
    return docs


# Two well-separated, non-degenerate families in the stdlib (mdd-free) subspace.
_A_BASE = _feat(-0.2, 0.70, 60.0, 0.45)
_B_BASE = _feat(0.5, 0.85, 120.0, 0.30)


def _two_families(n=6):
    return {"familyA": _family(_A_BASE, n), "familyB": _family(_B_BASE, n)}


# --- method: deterministic raw ranking ----------------------------------------

def test_deterministic_ranking():
    fams = _two_families()
    tgt = dict(_A_BASE)
    a = mfa.rank_families(tgt, fams)
    b = mfa.rank_families(tgt, fams)
    assert a == b


def test_target_from_family_ranks_that_family_top():
    fams = _two_families()
    res = mfa.rank_families(dict(_A_BASE), fams)
    assert res["family_ranking"][0]["family"] == "familyA"
    assert res["attribution_available"] is True
    # raw similarities are in (0, 1], un-normalized
    for r in res["family_ranking"]:
        assert 0.0 < r["similarity"] <= 1.0


def test_no_key_sums_to_one_over_families():
    """The raw similarities must NOT form a normalized posterior — their sum is not pinned to 1, and
    there is no `advisory_posterior` (or any sum-to-1) key."""
    res = mfa.rank_families(dict(_A_BASE), _two_families())
    sims = [r["similarity"] for r in res["family_ranking"]]
    assert abs(sum(sims) - 1.0) > 1e-6  # not normalized
    assert "advisory_posterior" not in res
    assert "posterior" not in res


# --- no-verdict guard ---------------------------------------------------------

_FORBIDDEN_KEYS = (
    "attributed_family", "verdict", "is_ai", "is_human", "source", "label",
    "advisory_posterior", "selection",
)


def test_no_verdict_keys_in_results():
    res = mfa.rank_families(dict(_A_BASE), _two_families())
    for k in _FORBIDDEN_KEYS:
        assert k not in res, f"forbidden verdict key {k!r} present in results"
    assert res["calibration_status"] == "uncalibrated"


def test_no_verdict_keys_recursive():
    """No forbidden verdict/selection key anywhere in the nested results payload."""
    res = mfa.rank_families(dict(_A_BASE), _two_families())

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in _FORBIDDEN_KEYS, f"forbidden key {k!r} nested in results"
                walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v)

    walk(res)


# --- abstention is REAL -------------------------------------------------------

def test_fewer_than_two_families_abstains():
    res = mfa.rank_families(dict(_A_BASE), {"familyA": _family(_A_BASE)})
    assert res["attribution_available"] is False
    assert "fewer than 2" in res["reason"]
    # the ranking is still emitted as raw evidence
    assert res["family_ranking"]


def test_thin_family_abstains():
    fams = {"familyA": _family(_A_BASE, n=6), "familyB": _family(_B_BASE, n=3)}
    res = mfa.rank_families(dict(_A_BASE), fams, min_docs=5)
    assert res["attribution_available"] is False
    assert "below the 5-doc floor" in res["reason"]


def test_relative_ood_abstains_not_a_fixed_floor():
    """An outlier target (drawn from a 6th, unreferenced profile) trips the RELATIVE within-scatter
    gate. Crucially this is relative: the SAME absolute distance is in-distribution for a family with a
    large within-scatter and OOD for a tight one — proving the gate is not a fixed floor."""
    fams = _two_families()
    # far-out target on every axis -> distance to the nearest centroid >> its within-scatter
    outlier = _feat(0.95, 0.10, 5.0, 0.95)
    res = mfa.rank_families(outlier, fams)
    assert res["out_of_distribution"] is True
    assert res["attribution_available"] is False
    assert "out-of-distribution" in res["reason"]

    # Relativity: a LOOSE family (large within-scatter) tolerates the same target that a TIGHT family
    # rejects. Build one loose and one tight family centered identically; the target sits at a fixed
    # offset. ood must flip with the scatter, not the absolute distance.
    tight = {k: v for k, v in _A_BASE.items()}
    loose_docs = _family(_A_BASE, n=8, jitter=0.5)   # large scatter
    tight_docs = _family(_A_BASE, n=8, jitter=0.001)  # tiny scatter
    offset_target = {k: v + 1.2 for k, v in tight.items()}
    res_loose = mfa.rank_families(offset_target, {"loose": loose_docs, "other": _family(_B_BASE)})
    res_tight = mfa.rank_families(offset_target, {"tight": tight_docs, "other": _family(_B_BASE)})
    # the loose family should be more tolerant than the tight one for the same target
    assert res_tight["out_of_distribution"] is True
    assert res_loose["out_of_distribution"] is False


def test_near_tie_trips_margin():
    """A target sitting (nearly) equidistant between two well-separated family centroids produces a
    top-2 similarity margin below threshold — the two families are too close to separate FOR THIS
    TARGET, so the surface abstains."""
    fams = _two_families()
    # midpoint between the two family bases -> ~equal distance to each -> ~equal similarity -> tiny margin
    mid = {k: (_A_BASE[k] + _B_BASE[k]) / 2.0 for k in _A_BASE}
    res = mfa.rank_families(mid, fams, margin_threshold=0.05)
    assert res["top_margin"] < 0.05
    assert res["attribution_available"] is False
    assert "margin" in res["reason"]


def test_human_would_be_top_abstains():
    """A `human` reference may rank like any label but may NEVER be the reported top — if it would,
    the surface abstains."""
    fams = {"human": _family(_A_BASE, n=6), "modelX": _family(_B_BASE, n=6)}
    res = mfa.rank_families(dict(_A_BASE), fams)
    # human is closest, so it would be top -> abstain
    assert res["family_ranking"][0]["family"] == "human"
    assert res["attribution_available"] is False
    assert "human" in res["reason"]


# --- standardization: a large-scale feature does not dominate -----------------

def test_standardization_prevents_mtld_domination():
    """Two families differing MAINLY on a small-scale feature (function_word_ratio) but with a large,
    SHARED MTLD spread. A raw (un-standardized) mean would let MTLD swamp the discriminative small-scale
    feature; robust-z standardization rescues it. The target shares familyA's function_word_ratio, so it
    must rank familyA top despite MTLD noise."""
    # familyA: low fwr; familyB: high fwr. Both families carry wide MTLD scatter (huge raw scale).
    a_docs = [_feat(0.0, 0.7, 50.0 + d, 0.20) for d in (-40, -20, 0, 20, 40, 10)]
    b_docs = [_feat(0.0, 0.7, 50.0 + d, 0.60) for d in (-40, -20, 0, 20, 40, 10)]
    fams = {"familyA": a_docs, "familyB": b_docs}
    # target: familyA's fwr (0.20), but an MTLD value that happens to sit nearer familyB's docs.
    tgt = _feat(0.0, 0.7, 70.0, 0.20)
    res = mfa.rank_families(tgt, fams)
    assert res["family_ranking"][0]["family"] == "familyA", (
        "MTLD's large raw scale dominated the ranking — standardization is not working"
    )


# --- fixed subspace: mdd uniformly in/out -------------------------------------

def test_feature_set_resolves_once_intersection():
    """The comparison subspace is the intersection across target + every ref doc. If ANY doc lacks mdd
    (spaCy-gated), mdd is dropped for EVERYONE — never per-doc."""
    a_docs = [_feat(-0.2, 0.7, 60.0, 0.45, mdd=3.0) for _ in range(6)]
    # one familyB doc is missing mdd -> mdd must drop out of the whole resolved subspace
    b_docs = [_feat(0.5, 0.85, 120.0, 0.30, mdd=4.0) for _ in range(5)]
    b_docs.append(_feat(0.5, 0.85, 120.0, 0.30))  # no mdd
    tgt = _feat(-0.2, 0.7, 60.0, 0.45, mdd=3.0)
    res = mfa.rank_families(tgt, {"familyA": a_docs, "familyB": b_docs})
    assert "mean_dependency_distance" not in res["feature_set"]
    assert res["feature_set"] == ["burstiness_B", "mattr", "mtld", "function_word_ratio"]


def test_feature_set_includes_mdd_when_present_everywhere():
    a_docs = [_feat(-0.2, 0.7, 60.0, 0.45, mdd=3.0) for _ in range(6)]
    b_docs = [_feat(0.5, 0.85, 120.0, 0.30, mdd=4.0) for _ in range(6)]
    tgt = _feat(-0.2, 0.7, 60.0, 0.45, mdd=3.0)
    res = mfa.rank_families(tgt, {"familyA": a_docs, "familyB": b_docs})
    assert "mean_dependency_distance" in res["feature_set"]


# --- claim license refuses the verdict ----------------------------------------

def test_claim_license_refuses_verdict():
    lic = mfa._claim_license()
    assert lic.task_surface == "model_family_attribution"
    dn = lic.does_not_license.lower()
    assert "produced by" in dn            # refuses attribution verdict
    assert "ai-vs-human" in dn or "human" in dn  # refuses AI/human ruling
    assert "posterior" in dn               # refuses normalized posterior
    assert "absent" in dn or "supplied" in dn    # names only supplied families
    assert "weak" in dn and "low-dimensional" in dn  # the weakness caveat
    # the licenses text is honest about what it IS: a ranking, advisory
    lc = lic.licenses.lower()
    assert "ranking" in lc and "advisory" in lc


# --- robust input -> bad_input ------------------------------------------------

def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = mfa.main(argv)
    return rc, json.loads(out.getvalue())


def _good_dir(tmp_path, n=6):
    root = tmp_path / "refs"
    for fam, base_words in (("familyA", "the cat sat on the mat and ran"),
                            ("familyB", "consequently the apparatus necessitates reconsideration throughout")):
        d = root / fam
        d.mkdir(parents=True)
        for i in range(n):
            # long enough to clear the default --min-words floor
            (d / f"{i}.txt").write_text((base_words + " ") * 20)
    return root


def test_missing_target_is_bad_input(tmp_path):
    root = _good_dir(tmp_path)
    rc, env = _envelope(["--target", str(tmp_path / "nope.txt"), "--reference-dir", str(root)])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert rc == 3


def test_missing_reference_dir_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat " * 20)
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(tmp_path / "nope")])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_non_object_manifest_row_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat " * 20)
    man = tmp_path / "m.jsonl"
    man.write_text('[1, 2, 3]\n')  # valid JSON, not an object
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(man)])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_non_utf8_reference_doc_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat " * 20)
    root = tmp_path / "refs" / "familyA"
    root.mkdir(parents=True)
    (root / "bad.txt").write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(tmp_path / "refs")])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_manifest_row_missing_family_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat " * 20)
    man = tmp_path / "m.jsonl"
    man.write_text(json.dumps({"text": "some text without a family key " * 20}) + "\n")
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(man)])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


# --- self-exclusion -----------------------------------------------------------

def test_self_exclusion_drops_target_from_its_family(tmp_path):
    """A target file that ALSO sits inside its own family dir is dropped from the reference pool, so it
    never matches against itself (the general_imposters pattern). The envelope warns about the drop."""
    root = tmp_path / "refs"
    a = root / "familyA"
    a.mkdir(parents=True)
    b = root / "familyB"
    b.mkdir(parents=True)
    text_a = "the cat sat on the mat and ran fast " * 20
    text_b = "consequently the apparatus necessitates reconsideration " * 20
    for i in range(6):
        (a / f"{i}.txt").write_text(text_a)
        (b / f"{i}.txt").write_text(text_b)
    # the target IS one of familyA's files
    target = a / "0.txt"
    rc, env = _envelope(["--target", str(target), "--reference-dir", str(root)])
    assert env["available"] is True
    warns = " ".join(env.get("warnings") or [])
    assert "self-exclusion" in warns


# --- envelope shape -----------------------------------------------------------

def test_envelope_surface_registered_and_shape(tmp_path):
    assert "model_family_attribution" in VALID_TASK_SURFACES
    root = tmp_path / "refs"
    for fam, words in (("familyA", "the cat sat on the mat and ran fast "),
                       ("familyB", "consequently the apparatus necessitates reconsideration throughout ")):
        d = root / fam
        d.mkdir(parents=True)
        for i in range(6):
            (d / f"{i}.txt").write_text(words * 20)
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat and ran fast " * 22)
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(root)])
    assert env["task_surface"] == "model_family_attribution"
    assert env["schema_version"] == "1.0"
    r = env["results"]
    for key in ("family_ranking", "top_margin", "out_of_distribution",
                "attribution_available", "reason", "n_families", "feature_set",
                "target_words", "calibration_status", "assumptions"):
        assert key in r, f"missing results key {key!r}"
    # claim license present and refuses the verdict
    assert env["claim_license"]["task_surface"] == "model_family_attribution"
    assert "produced by" in env["claim_license"]["does_not_license"].lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
