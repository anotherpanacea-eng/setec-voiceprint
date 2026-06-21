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


def test_min_docs_floor_is_hard_cannot_be_lowered():
    """The MIN_DOCS_PER_FAMILY floor is HARD: a caller may RAISE it but never lower it below 5. A 3-doc
    family with min_docs=2 must STILL abstain (the request is clamped up to 5), so the small-n over-claim
    protection cannot be opted out of. Without the clamp, rank_families(..., min_docs=2) would return
    attribution_available=True for 3-doc families — the posture leak the spec's P2 rework refuses."""
    fams = {"familyA": _family(_A_BASE, n=3), "familyB": _family(_B_BASE, n=3)}
    res = mfa.rank_families(dict(_A_BASE), fams, min_docs=2)
    # clamped up to the hard floor -> both 3-doc families are thin -> abstain
    assert res["min_docs_per_family"] == mfa.MIN_DOCS_PER_FAMILY
    assert res["attribution_available"] is False
    assert f"below the {mfa.MIN_DOCS_PER_FAMILY}-doc floor" in res["reason"]
    # raising the floor IS honored (operator may be stricter)
    fat = {"familyA": _family(_A_BASE, n=6), "familyB": _family(_B_BASE, n=6)}
    res_raise = mfa.rank_families(dict(_A_BASE), fat, min_docs=8)
    assert res_raise["min_docs_per_family"] == 8
    assert res_raise["attribution_available"] is False  # 6 < 8 -> thin


def test_cli_min_docs_clamped_up_to_floor(tmp_path, capsys):
    """The CLI mirrors the hard floor: `--min-docs 2` is clamped up to MIN_DOCS_PER_FAMILY (with a stderr
    notice), so an operator cannot lower the small-n protection from the command line either."""
    root = _good_dir(tmp_path, n=6)
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat and ran fast " * 22)
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(root), "--min-docs", "2"])
    assert env["results"]["min_docs_per_family"] == mfa.MIN_DOCS_PER_FAMILY


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
    """The margin gate must trip on an IN-DISTRIBUTION near-tie, ISOLATED from the relative-OOD gate.

    Two families that overlap heavily on a SINGLE axis (function_word_ratio), with within-scatter (±0.30)
    large relative to the centroid gap (0.10). The target sits at the fwr midpoint, so it is ~equidistant
    from both centroids (tiny top-2 margin) YET well inside each family's own scatter (NOT out-of-
    distribution). attribution_available is therefore False because of the MARGIN gate alone — we assert
    out_of_distribution is False and 'out-of-distribution' is NOT in the reason, so the test cannot pass
    on the OOD gate instead. The earlier version put the target at the midpoint of two WELL-SEPARATED
    families, which sits far from both centroids and trips OOD first; attribution_available=False was then
    satisfied by OOD, and the test could not tell the margin gate from the OOD gate."""
    def _fam_on_fwr(fwr_center, n=8, spread=0.30):
        # n docs spread symmetrically along function_word_ratio only; centroid == fwr_center, with a
        # large within-scatter so an in-between target stays in-distribution.
        docs = []
        for i in range(n):
            eps = (i - (n - 1) / 2) / ((n - 1) / 2)  # symmetric in [-1, 1]
            docs.append(_feat(0.0, 0.70, 60.0, fwr_center + eps * spread))
        return docs

    fams = {"familyA": _fam_on_fwr(0.35), "familyB": _fam_on_fwr(0.45)}  # gap 0.10 << scatter 0.30
    mid = _feat(0.0, 0.70, 60.0, 0.40)  # equidistant on the fwr axis -> tiny margin
    res = mfa.rank_families(mid, fams, margin_threshold=0.05)
    assert res["top_margin"] < 0.05
    assert res["out_of_distribution"] is False, (
        "the near-tie target must stay IN-distribution so the margin gate is isolated from OOD"
    )
    assert res["attribution_available"] is False
    assert "margin" in res["reason"]
    assert "out-of-distribution" not in res["reason"], (
        "the OOD gate must not co-trip — attribution_available=False must be due to MARGIN alone"
    )


def test_human_would_be_top_abstains():
    """A `human` reference may rank like any label but may NEVER be the reported top — if it would,
    the surface abstains."""
    fams = {"human": _family(_A_BASE, n=6), "modelX": _family(_B_BASE, n=6)}
    res = mfa.rank_families(dict(_A_BASE), fams)
    # human is closest, so it would be top -> abstain
    assert res["family_ranking"][0]["family"] == "human"
    assert res["attribution_available"] is False
    assert "human" in res["reason"]


@pytest.mark.parametrize(
    "label", ["human", "Human", "HUMAN", "humans", "human_writers", "human-writers", "people", "organic"]
)
def test_human_gate_not_defeatable_by_relabel(label):
    """The never-an-AI/human-verdict gate must NOT be defeatable by a one-character relabel. A reserved
    human-class label in ANY case or near-synonym form (`Human`, `humans`, `human_writers`, `people`, ...)
    occupying the top slot forces abstention — not only the exact lowercase string `human`. Previously the
    gate was an exact case-sensitive compare, so `Human`/`humans`/`human_writers` returned
    attribution_available=True with that label as the reported TOP slot."""
    fams = {label: _family(_A_BASE, n=6), "modelX": _family(_B_BASE, n=6)}
    res = mfa.rank_families(dict(_A_BASE), fams)
    assert res["family_ranking"][0]["family"] == label  # the human-class label is closest -> would be top
    assert res["attribution_available"] is False, (
        f"a {label!r}-top case must abstain — the human gate must be relabel-proof"
    )
    assert "human-class label" in res["reason"]


def test_non_human_label_does_not_trip_human_gate():
    """The normalized human gate must not over-match: an ordinary family label that merely CONTAINS a
    human-ish substring (e.g. `humane_llm`, `superhuman`) is NOT the reserved human class and may be top."""
    for label in ("humane_llm", "superhuman", "gpt_humanlike"):
        assert mfa._is_human_label(label) is False, f"{label!r} must NOT match the reserved human class"
    fams = {"humane_llm": _family(_A_BASE, n=6), "modelX": _family(_B_BASE, n=6)}
    res = mfa.rank_families(dict(_A_BASE), fams)
    assert res["family_ranking"][0]["family"] == "humane_llm"
    assert res["attribution_available"] is True  # not a human-class label -> gate does not trip


# --- standardization: a large-scale feature does not dominate -----------------

def test_standardization_prevents_mtld_domination(monkeypatch):
    """MTLD must be DISCRIMINATIVE-BUT-MISLEADING so the test fails if standardization is removed.

    The two families now differ on MTLD too (A~50, B~60), not just on a small-scale feature. The target's
    RAW MTLD (58) sits nearer familyB, but its function_word_ratio (0.20) matches familyA. With robust-z
    standardization the small-scale fwr separates the families and the target ranks familyA top (correct);
    WITHOUT standardization the large raw MTLD scale dominates and the target flips to familyB. The earlier
    construction shared an IDENTICAL MTLD distribution across both families, so MTLD cancelled between the
    centroids and the test passed with OR without standardization — a tautology that could not distinguish
    working standardization from absent standardization."""
    a_docs = [_feat(0.0, 0.7, 50.0 + d, 0.20) for d in (-2, -1, 0, 1, 2, 0)]
    b_docs = [_feat(0.0, 0.7, 60.0 + d, 0.60) for d in (-2, -1, 0, 1, 2, 0)]
    fams = {"familyA": a_docs, "familyB": b_docs}
    # target: familyA's fwr (0.20), but a raw MTLD (58) that sits NEARER familyB's docs.
    tgt = _feat(0.0, 0.7, 58.0, 0.20)

    # WITH standardization: the small-scale fwr separates the families -> familyA top (correct).
    res = mfa.rank_families(tgt, fams)
    assert res["family_ranking"][0]["family"] == "familyA", (
        "MTLD's large raw scale dominated the ranking — standardization is not working"
    )

    # WITHOUT standardization (identity scalers): raw MTLD dominates -> ranking flips to familyB. This
    # sub-assertion makes the test FAIL if standardization is removed (the previous version did not).
    monkeypatch.setattr(
        mfa, "_robust_scalers", lambda pooled, feature_set: {name: (0.0, 1.0) for name in feature_set}
    )
    res_raw = mfa.rank_families(tgt, fams)
    assert res_raw["family_ranking"][0]["family"] == "familyB", (
        "without standardization the raw MTLD scale should dominate and flip the ranking to familyB — "
        "if it does not, this test cannot detect a removed standardization step"
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


# --- target length floor ------------------------------------------------------

def test_short_target_forced_to_abstain(tmp_path):
    """The advertised --min-words length floor (length_floor_words: 50) guards the TARGET, not only the
    reference docs. A sub-floor target can NEVER reach attribution_available=True — it is forced to abstain
    with an explicit too-short reason and a warning, and the family_ranking stays as raw evidence.
    Previously the floor was applied only to reference docs, so a 3-word target produced a full ranking and
    could in principle be attributed."""
    root = _good_dir(tmp_path, n=6)
    tgt = tmp_path / "t.txt"
    tgt.write_text("cat sat mat")  # 3 words, well below the 50-word floor
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(root)])
    assert env["available"] is True  # the run completes; it abstains, it does not error
    r = env["results"]
    assert r["target_words"] == 3
    assert r["attribution_available"] is False
    assert r.get("target_below_min_words") is True
    assert "length floor" in r["reason"]
    warns = " ".join(env.get("warnings") or [])
    assert "length floor" in warns


def test_at_floor_target_is_not_forced_to_abstain_by_length(tmp_path):
    """A target AT/above the floor is not abstained on length grounds — the length guard fires only below
    --min-words, so it does not suppress legitimate in-floor targets."""
    root = _good_dir(tmp_path, n=6)
    tgt = tmp_path / "t.txt"
    tgt.write_text("the cat sat on the mat and ran fast " * 22)  # comfortably above 50 words
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(root)])
    r = env["results"]
    assert r["target_words"] >= 50
    assert "target_below_min_words" not in r
    assert "length floor" not in r["reason"]


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


def test_self_exclusion_drops_inline_text_copies_of_target(tmp_path):
    """Self-exclusion must hold for the INLINE-`text` manifest form too, not only path-based refs (#255 P1).

    The smallest input that breaks the path-only check: a manifest that repeats the EXACT target text
    inline 5x under one family (clearing the 5-doc floor) plus a second family. Every inline row carries
    path=None, so the old path-only self-exclusion retained all five — yielding a zero-distance centroid
    that could flip attribution_available=True off a self-copy. With content-based self-exclusion all five
    inline copies are dropped, the family collapses, and attribution is NOT available off the self-copy.

    Against the PRE-FIX code this FAILS: the inline copies are retained, `familyA` survives with a
    zero-distance centroid, `attribution_available` is True, and the self-exclusion warning never fires.
    """
    target_text = "the cat sat on the mat and ran fast and far " * 12
    other_text = "consequently the apparatus necessitates reconsideration throughout the process " * 12
    tgt = tmp_path / "t.txt"
    tgt.write_text(target_text)
    man = tmp_path / "m.jsonl"
    lines = []
    # five inline EXACT copies of the target under one family (each path=None)
    for _ in range(5):
        lines.append(json.dumps({"family": "familyA", "text": target_text}))
    # a distinct second family so n_families >= 2 absent self-exclusion
    for _ in range(5):
        lines.append(json.dumps({"family": "familyB", "text": other_text}))
    man.write_text("\n".join(lines) + "\n")

    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(man)])
    assert env["available"] is True  # the run completes; it abstains, it does not error
    warns = " ".join(env.get("warnings") or [])
    assert "self-exclusion" in warns, "inline-text copies of the target must be self-excluded by content"
    r = env["results"]
    # the zero-distance self-copy must NOT manufacture an attribution
    assert r["attribution_available"] is False, (
        "repeating the exact target inline must NOT yield attribution_available=True off a "
        "zero-distance self-copy"
    )
    # familyA collapsed entirely once its 5 inline self-copies were dropped -> < 2 families remain
    assert r["n_families"] < 2


def test_self_exclusion_drops_content_copy_at_other_path(tmp_path):
    """Path-based self-exclusion must also catch an exact CONTENT copy living at a DIFFERENT path (a
    `text_path` row whose file holds a byte-for-byte copy of the target). The path check alone misses it
    because the resolved path differs; the content key catches it. Mirrors the inline case for the
    `text_path` input form so BOTH manifest forms are covered."""
    target_text = "the cat sat on the mat and ran fast and far " * 12
    other_text = "consequently the apparatus necessitates reconsideration throughout the process " * 12
    tgt = tmp_path / "t.txt"
    tgt.write_text(target_text)
    # a separate file holding an exact copy of the target, referenced via text_path
    copy = tmp_path / "copy.txt"
    copy.write_text(target_text)
    man = tmp_path / "m.jsonl"
    lines = [json.dumps({"family": "familyA", "text_path": "copy.txt"}) for _ in range(5)]
    lines += [json.dumps({"family": "familyB", "text": other_text}) for _ in range(5)]
    man.write_text("\n".join(lines) + "\n")
    rc, env = _envelope(["--target", str(tgt), "--reference-manifest", str(man)])
    assert env["available"] is True
    warns = " ".join(env.get("warnings") or [])
    assert "self-exclusion" in warns
    assert env["results"]["attribution_available"] is False


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
