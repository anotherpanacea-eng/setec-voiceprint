#!/usr/bin/env python3
"""Tests for calibration/narrative_longform_agreement.py (spec 79 M1).

Pins:

  * S1 signal-identity: 33 unique ids, 19 option-None / 14
    option-bearing, the eight single-leaning option-bearing ids retain
    their suffix.
  * Frozen operator table: 12 not_aggregatable / 12 mean /
    9 prevalence, pairwise disjoint, total over the schema, with the
    not_aggregatable membership pinned exactly.
  * Statistics: hand-computed Spearman (average-rank ties) and
    Mann-Whitney AUC cases; constant vectors return None (no epsilon).
  * Verdict derivation: floors first (works, per-signal support,
    prevalence class support), degenerate → indeterminate, both mean
    statistics must pass, empirical failure → not_aggregatable,
    not_aggregatable a priori regardless of data.
  * The LICENSED REGIME, and only it: 24 works, per-signal support 18,
    class support 6, at least three segments per work. There is no
    "small test threshold set" here any more — an earlier build carried
    one at 3/3/1 with two segments per work, and minted
    validated_aggregatable receipts under it. A fixture that violates
    the regime is not a convenience; it is a receipt the spec never
    authorised, so the regime IS the fixture.
  * Judge provenance is derived from the manifest and refuses mock,
    heterogeneous, and registration-mismatched identities.
  * Achieved segment lengths are recomputed from the bound segment text;
    asserted integers are cross-checked and refuse.
  * Receipt: exact key set, computed segment bands, framed digests with
    one domain per payload schema.
  * verify_receipt: re-derives verdicts from artifacts, takes the date
    from the caller, and exempts NOTHING; hand-edited verdicts,
    statistics, derivations, dates, paths, and swapped manifests all
    refuse.
  * CLI byte-determinism across two subprocess runs.

All fixtures are synthetic; stdlib + pytest only.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB = ROOT / "calibration"
for p in (str(ROOT), str(CALIB)):
    if p not in sys.path:
        sys.path.insert(0, p)

import narrative_longform_agreement as nla  # type: ignore  # noqa: E402
import narrative_longform_segment as nls  # type: ignore  # noqa: E402
from narrative_feature_schema import (  # type: ignore  # noqa: E402
    CORE_FEATURES,
)

MODULE = CALIB / "narrative_longform_agreement.py"
DATE = "2026-07-27"

# ---------- the licensed thresholds artifact --------------------------
#
# Floors are spec 79's, and `load_thresholds` refuses anything weaker. The
# per-operator values are illustrative — real ones are operator-frozen before
# the M2 study registers — but they are above the discriminating minimums
# (auc_min > 0.5, spearman_min > 0). Direction is explicit in the key names:
# *_min = higher-is-better, *_max = lower-is-better. A mean signal passes only
# if BOTH its statistics pass.
LICENSED_THRESHOLDS = {
    "schema": "narrative-longform-thresholds/1",
    "floors": {
        "min_works": 24,
        "min_signal_support": 18,
        "min_class_support": 6,
    },
    "per_operator": {
        "mean": {"spearman_min": 0.75, "mad_max_response_units": 0.5},
        "prevalence": {"auc_min": 0.8},
    },
}
LICENSED_FLOORS = LICENSED_THRESHOLDS["floors"]

N_WORKS = 24
N_SEGMENTS = 3

SEGMENTER = {
    "version": "narrative_longform_segment/1",
    "params_sha256": "sha256:" + "0" * 64,
    "segment_target_words": 5000,
}
JUDGE = {
    "kind": "manifest",
    "model": "test-judge",
    "model_revision": "rev-1",
    "prompt_version": "p1",
}

SIG_MEAN = "narrative.thematic_over_determination.thematic_unity"
SIG_ORD = "narrative.structural_streamlining.spatial_granularity_level"
SIG_PREV = (
    "narrative.thematic_over_determination."
    "narratorial_thematic_commentary.yes"
)
SIG_PREV_MULTI = (
    "narrative.thematic_over_determination."
    "dialogue_function.philosophical_debate"
)
SIG_NA = "narrative.temporal_complexity.anachrony_intensity"

_ORD_OPTIONS = ("very_low", "low", "medium", "high")

# Per-segment word counts: 2,000 + (k mod 7) x 100 over the 72 segments,
# k = work_index * 3 + segment_index. Sorted, the 72 values are
# 2000 x 11, 2100 x 11, then 2200..2600 x 10 each, so min 2000, max 2600,
# and both middle elements (36th and 37th) fall in the 2300 bucket.
BAND_MIN_WORDS = 2000
BAND_MAX_WORDS = 2600
BAND_MEDIAN_WORDS = 2300.0


def seg_word_count(work_index: int, segment_index: int) -> int:
    return 2000 + ((work_index * N_SEGMENTS + segment_index) % 7) * 100


_CONTENT_CACHE: dict[tuple[int, int], str] = {}


def seg_content(work_index: int, segment_index: int) -> str:
    """Distinct synthetic segment text of an exact word count."""
    key = (work_index, segment_index)
    if key not in _CONTENT_CACHE:
        n = seg_word_count(*key)
        _CONTENT_CACHE[key] = " ".join(
            f"w{work_index}s{segment_index}t{k}" for k in range(n)
        )
    return _CONTENT_CACHE[key]


# ---------- baseline judged values ------------------------------------
#
# Every populated signal validates under LICENSED_THRESHOLDS, and no work has
# three byte-identical segments (which would be a text-blind judge).
#
#   SIG_MEAN  whole cycles 1..5; segment mean = whole + 1/3 (and 5 - 1/3 at
#             the top of the scale) → rho 1.0, MAD exactly 1/3
#   SIG_ORD   whole idx cycles 0..3; segment means 1/3, 4/3, 7/3, 8/3
#             → rho 1.0, MAD exactly 1/3
#   SIG_PREV  12 "yes" works at prevalence 2/3 vs 12 "no" works at 1/3
#             → AUC 1.0
#   SIG_PREV_MULTI same shape → AUC 1.0
#   SIG_NA    populated but a priori not_aggregatable

def _mean_values(i: int) -> tuple[str, list[str]]:
    v = i % 5 + 1
    if v == 5:
        return "5", ["5", "4", "5"]
    return str(v), [str(v), str(v), str(v + 1)]


def _ord_values(i: int) -> tuple[str, list[str]]:
    o = i % 4
    if o == 3:
        return _ORD_OPTIONS[3], [
            _ORD_OPTIONS[3], _ORD_OPTIONS[2], _ORD_OPTIONS[3]
        ]
    return _ORD_OPTIONS[o], [
        _ORD_OPTIONS[o], _ORD_OPTIONS[o], _ORD_OPTIONS[o + 1]
    ]


def _prev_values(i: int) -> tuple[str, list[str]]:
    if i < N_WORKS // 2:
        return "yes", ["yes", "yes", "no"]
    return "no", ["no", "no", "yes"]


def _prev_multi_values(i: int) -> tuple[list[str], list[list[str]]]:
    if i < N_WORKS // 2:
        return (
            ["philosophical_debate", "advance_plot"],
            [
                ["philosophical_debate"],
                ["philosophical_debate", "comic_relief"],
                ["advance_plot"],
            ],
        )
    return (
        ["advance_plot"],
        [["advance_plot"], ["reveal_character"], ["philosophical_debate"]],
    )


def _na_values(i: int) -> tuple[str, list[str]]:
    return "3", ["2", "3", "4"]


BASE_BUILDERS = {
    SIG_MEAN: _mean_values,
    SIG_ORD: _ord_values,
    SIG_PREV: _prev_values,
    SIG_PREV_MULTI: _prev_multi_values,
    SIG_NA: _na_values,
}


def cell(value, available=True):
    return {"value": value, "available": available}


def make_rows(overrides=None, n_works=N_WORKS, judge=None):
    """Build the licensed-regime manifest rows.

    ``overrides`` maps signal_id → callable(i) -> (whole, [seg0, seg1, seg2]);
    entries may be raw responses or preformed cell dicts (to set
    available=False).
    """
    builders = dict(BASE_BUILDERS)
    builders.update(overrides or {})
    identity = dict(judge or JUDGE)
    rows = []
    for i in range(n_works):
        work_id = f"w{i:02d}"
        whole: dict = {}
        seg_maps: list[dict] = [dict() for _ in range(N_SEGMENTS)]
        for sig, builder in builders.items():
            whole_value, seg_values = builder(i)
            whole[sig] = (
                whole_value
                if isinstance(whole_value, dict) and "available" in whole_value
                else cell(whole_value)
            )
            for j in range(N_SEGMENTS):
                sv = seg_values[j]
                seg_maps[j][sig] = (
                    sv if isinstance(sv, dict) and "available" in sv
                    else cell(sv)
                )
        segments = []
        for j in range(N_SEGMENTS):
            content = seg_content(i, j)
            segments.append({
                "segment_id": f"{work_id}-s{j}",
                "content": content,
                "content_sha256": nls.content_digest(content),
                "n_words": nls.count_words(content),
                "judge_identity": dict(identity),
                "signals": seg_maps[j],
            })
        rows.append({
            "work_id": work_id,
            "n_words": 60_000,
            "judge_identity": dict(identity),
            "whole_work": whole,
            "segments": segments,
        })
    return rows


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8",
    )


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def pipeline(tmp_path, rows, thresholds=None, prefix="run", judge=None):
    """register (values-free design) then evaluate; returns
    (receipt, paths dict)."""
    thresholds = thresholds if thresholds is not None else LICENSED_THRESHOLDS
    t = tmp_path / f"{prefix}-thresholds.json"
    m = tmp_path / f"{prefix}-manifest.jsonl"
    d = tmp_path / f"{prefix}-design.jsonl"
    r = tmp_path / f"{prefix}-registration.json"
    write_json(t, thresholds)
    write_jsonl(m, rows)
    write_jsonl(
        d,
        [{"work_id": row["work_id"], "n_words": row["n_words"]}
         for row in rows],
    )
    registration = nla.build_registration(
        date=DATE, thresholds_path=t, manifest_path=d,
        segmenter=dict(SEGMENTER), judge=dict(judge or JUDGE),
    )
    write_json(r, registration)
    receipt = nla.build_receipt(
        date=DATE, thresholds_path=t, registration_path=r,
        manifest_path=m,
    )
    return receipt, {
        "thresholds": t, "manifest": m, "design": d, "registration": r,
    }


def run_cli(args):
    return subprocess.run(
        [sys.executable, str(MODULE), *args],
        capture_output=True, text=True,
    )


def expected_framed(domain: bytes, payload: bytes) -> str:
    """Independent re-implementation of the framing rule."""
    return "sha256:" + hashlib.sha256(
        domain + struct.pack(">Q", len(payload)) + payload
    ).hexdigest()


def canonical(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


# ---------- S1 identity + operator table ------------------------------

def test_signal_id_registry_pin():
    ids = set(nla.SIGNALS)
    assert len(ids) == 33
    assert len(nla.SIGNAL_IDS) == 33
    # option-None ids are narrative.<bundle>.<key> (2 dots);
    # option-bearing add .<option> (3 dots).
    no_option = {i for i in ids if i.count(".") == 2}
    with_option = {i for i in ids if i.count(".") == 3}
    assert len(no_option) == 19
    assert len(with_option) == 14
    # Independent re-derivation straight off the schema.
    expected = set()
    for f in CORE_FEATURES:
        for s in f.signals:
            base = f"narrative.{s.bundle}.{f.key}"
            expected.add(base if s.option is None else f"{base}.{s.option}")
    assert ids == expected
    # The eight single-leaning option-bearing signals retain suffixes.
    for suffixed in (
        "narratorial_thematic_commentary.yes",
        "dialogue_function.philosophical_debate",
        "dominant_sensory_modalities.olfactory",
        "agency_in_resolution.protagonist_choice",
        "character_introduction.external_description",
        "mode_of_resolution.resolved_internally",
        "intertextual_strategy_types.explicit_named",
        "moral_polarity_toward_protagonist.ambivalent_or_mixed",
    ):
        assert any(i.endswith("." + suffixed.split(".", 1)[1])
                   and f".{suffixed.split('.')[0]}." in i
                   for i in with_option), suffixed


def test_operator_table_partition():
    # Frozen sets are pairwise disjoint.
    assert not (nla.NOT_AGGREGATABLE_KEYS & nla.MEAN_KEYS)
    assert not (nla.NOT_AGGREGATABLE_KEYS & nla.PREVALENCE_KEYS)
    assert not (nla.MEAN_KEYS & nla.PREVALENCE_KEYS)
    # Split is exactly 12 / 12 / 9 over the 33 signals (totality is
    # enforced at import; recheck here against the registry).
    by_op = {"not_aggregatable": set(), "mean": set(), "prevalence": set()}
    for spec in nla.SIGNALS.values():
        by_op[spec.operator].add(
            spec.feature_key if spec.option is None
            else f"{spec.feature_key}.{spec.option}"
        )
    assert len(by_op["not_aggregatable"]) == 12
    assert len(by_op["mean"]) == 12
    assert len(by_op["prevalence"]) == 9
    assert by_op["not_aggregatable"] == {
        "mode_of_resolution.resolved_internally",
        "agency_in_resolution.protagonist_choice",
        "subplot_integration.no_subplots",
        "subplot_integration.thematically_parallel",
        "anachrony_intensity",
        "degree_of_chronological_discontinuity",
        "nonlinear_framing_for_delayed_disclosure",
        "depth_of_recontextualization_after_surprise",
        "opening_spatial_grounding",
        "character_introduction.external_description",
        "pre_threat_character_investment",
        "location_variety_scope",
    }
    assert by_op["not_aggregatable"] == set(nla.NOT_AGGREGATABLE_KEYS)
    assert by_op["mean"] == set(nla.MEAN_KEYS)
    assert by_op["prevalence"] == set(nla.PREVALENCE_KEYS)


# ---------- statistics helpers ----------------------------------------

def test_spearman_hand_computed():
    # whole [1,2,2,4] → ranks [1, 2.5, 2.5, 4]; seg [1,3,2,4] → ranks
    # [1,3,2,4]. Pearson over ranks: cov 4.5, var_w 4.5, var_s 5.0 →
    # rho = 4.5 / sqrt(22.5) = 0.9486832980505138.
    rho = nla.spearman_rho([1.0, 2.0, 2.0, 4.0], [1.0, 3.0, 2.0, 4.0])
    assert rho == pytest.approx(0.9486832980505138, abs=1e-12)
    # Perfect monotone agreement.
    assert nla.spearman_rho(
        [1.0, 2.0, 3.0], [10.0, 20.0, 30.0],
    ) == pytest.approx(1.0)
    # Constant vector → None (indeterminate; NO epsilon division).
    assert nla.spearman_rho([3.0, 3.0, 3.0], [1.0, 2.0, 3.0]) is None
    assert nla.spearman_rho([1.0, 2.0, 3.0], [5.0, 5.0, 5.0]) is None


def test_average_ranks_ties():
    assert nla.average_ranks([10.0, 20.0, 20.0, 40.0]) == [
        1.0, 2.5, 2.5, 4.0,
    ]


def test_auc_hand_computed():
    # pos [0.8, 0.6] vs neg [0.2, 0.6]: wins 1 + 1 + 1 + tie 0.5 = 3.5
    # of 4 → 0.875.
    assert nla.auc_mannwhitney(
        [0.8, 0.6], [0.2, 0.6],
    ) == pytest.approx(0.875)
    assert nla.auc_mannwhitney([], [0.1]) is None
    assert nla.auc_mannwhitney([0.9], []) is None


def test_mean_absolute_deviation():
    assert nla.mean_absolute_deviation(
        [4.0, 2.0], [3.5, 2.5],
    ) == pytest.approx(0.5)


# ---------- verdict derivation (the pure function) ---------------------

def test_derive_verdict_rule_order():
    floors = LICENSED_FLOORS
    passing = [
        {"name": "spearman_rho", "value": 0.9, "threshold": 0.75,
         "direction": "min"},
        {"name": "mad_response_units", "value": 0.1, "threshold": 0.5,
         "direction": "max"},
    ]
    # 1. not_aggregatable a priori — beats every other condition,
    #    including a below-floor corpus and passing statistics.
    assert nla.derive_verdict(
        operator="not_aggregatable", corpus_n_works=1, support=0,
        floors=floors, statistics=passing,
    ) == "not_aggregatable"
    # 2. corpus floor beats degenerate (floors first).
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=23, support=24, floors=floors,
        degenerate=True, statistics=None,
    ) == "insufficient_support"
    # 3. per-signal support floor.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=24, support=17, floors=floors,
        statistics=passing,
    ) == "insufficient_support"
    # 4. prevalence class floor (single-valued whole = n == 0 case).
    assert nla.derive_verdict(
        operator="prevalence", corpus_n_works=24, support=24,
        floors=floors, n_pos=24, n_neg=0,
        statistics=[{"name": "auc", "value": 1.0, "threshold": 0.8,
                     "direction": "min"}],
    ) == "insufficient_support"
    assert nla.derive_verdict(
        operator="prevalence", corpus_n_works=24, support=24,
        floors=floors, n_pos=19, n_neg=5,
        statistics=[{"name": "auc", "value": 1.0, "threshold": 0.8,
                     "direction": "min"}],
    ) == "insufficient_support"
    # 5. degenerate → indeterminate once floors pass.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=24, support=24, floors=floors,
        degenerate=True, statistics=None,
    ) == "indeterminate"
    # 6. thresholds: all pass → validated; any failure → empirical
    #    not_aggregatable; boundary equality passes for both
    #    directions.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=24, support=24, floors=floors,
        statistics=passing,
    ) == "validated_aggregatable"
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=24, support=24, floors=floors,
        statistics=[
            {"name": "spearman_rho", "value": 0.75, "threshold": 0.75,
             "direction": "min"},
            {"name": "mad_response_units", "value": 0.5,
             "threshold": 0.5, "direction": "max"},
        ],
    ) == "validated_aggregatable"
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=24, support=24, floors=floors,
        statistics=[
            {"name": "spearman_rho", "value": 0.9, "threshold": 0.75,
             "direction": "min"},
            {"name": "mad_response_units", "value": 1.2,
             "threshold": 0.5, "direction": "max"},
        ],
    ) == "not_aggregatable"


# ---------- the licensed regime is the only regime ----------------------

@pytest.mark.parametrize("floors", [
    {"min_works": 3, "min_signal_support": 3, "min_class_support": 1},
    {"min_works": 23, "min_signal_support": 18, "min_class_support": 6},
    {"min_works": 24, "min_signal_support": 17, "min_class_support": 6},
    {"min_works": 24, "min_signal_support": 18, "min_class_support": 5},
])
def test_thresholds_below_the_licensed_floors_refuse(tmp_path, floors):
    """Codex P1: the committed tests minted validated_aggregatable
    receipts with floors 3/3/1 against four works and two segments each,
    while spec 79 mandates 24/18/6. Weakening a floor is not a smaller
    study; it is a different one, and the artifact must refuse."""
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["floors"] = floors
    t = tmp_path / "t.json"
    write_json(t, bad)
    with pytest.raises(
        nla.CalibrationRefusal, match="below the licensed spec 79 regime",
    ):
        nla.load_thresholds(t)


def test_chance_level_thresholds_refuse(tmp_path):
    """`auc_min = 0.5` was the operator input in Codex's laundering
    construction: tied prevalence everywhere scores exactly 0.5, which then
    "passes"."""
    t = tmp_path / "t.json"
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["per_operator"]["prevalence"]["auc_min"] = 0.5
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="auc_min must be >"):
        nla.load_thresholds(t)

    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["per_operator"]["mean"]["spearman_min"] = 0.0
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="spearman_min must be >"):
        nla.load_thresholds(t)


def test_work_with_fewer_than_three_segments_refuses(tmp_path):
    """Spec 79 fixes validated_segment_count_range.min >= 3; the first
    build imposed no minimum and happily banded a 2-segment study."""
    rows = make_rows()
    rows[7]["segments"] = rows[7]["segments"][:2]
    with pytest.raises(nla.CalibrationRefusal, match="fewer than 3 segments"):
        pipeline(tmp_path, rows)


# ---------- judge provenance is derived, not asserted --------------------

MOCK_JUDGE = {
    "kind": "mock",
    "model": "mock",
    "model_revision": "rev-1",
    "prompt_version": "p1",
}


def _constant_segment_builder(values):
    def build(i):
        whole, _ = values(i)
        constant = values(i)[1][0]
        return whole, [constant] * N_SEGMENTS
    return build


def test_mock_derived_manifest_refuses_even_under_a_concrete_registration(
    tmp_path,
):
    """Codex P1, the laundering construction.

    Manifest segments carried no judge identity at all, and `--evaluate`
    copied the operator-supplied REGISTRATION judge into the receipt
    verbatim. So mock-derived values could be registered under a concrete
    `manifest` identity and emerge inside a licensing receipt. The identity
    now comes from the manifest and is validated there.
    """
    rows = make_rows(judge=MOCK_JUDGE)
    t = tmp_path / "t.json"
    m = tmp_path / "m.jsonl"
    d = tmp_path / "d.jsonl"
    r = tmp_path / "r.json"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(m, rows)
    write_jsonl(d, [{"work_id": row["work_id"]} for row in rows])
    # The registration itself is impeccable: a concrete manifest judge.
    write_json(r, nla.build_registration(
        date=DATE, thresholds_path=t, manifest_path=d,
        segmenter=dict(SEGMENTER), judge=dict(JUDGE),
    ))
    with pytest.raises(nla.CalibrationRefusal, match="mock"):
        nla.build_receipt(
            date=DATE, thresholds_path=t, registration_path=r,
            manifest_path=m,
        )


def test_full_laundering_construction_refuses(tmp_path):
    """The verdict's construction end to end: 24 works, 12/12 whole-work
    prevalence classes, three CONSTANT mock-derived segments per work, and
    `auc_min = 0.5`. Tied prevalence gave AUC 0.5, which passed, and the
    receipt said validated_aggregatable."""
    thresholds = json.loads(json.dumps(LICENSED_THRESHOLDS))
    thresholds["per_operator"]["prevalence"]["auc_min"] = 0.5
    overrides = {
        sig: _constant_segment_builder(builder)
        for sig, builder in BASE_BUILDERS.items()
    }
    rows = make_rows(overrides, judge=MOCK_JUDGE)
    # Three independent gates now stand in its way, and the construction
    # must not survive the removal of any two of them. Outermost first:
    # the chance-level threshold.
    with pytest.raises(nla.CalibrationRefusal, match="auc_min must be >"):
        pipeline(tmp_path, rows, thresholds, judge=MOCK_JUDGE, prefix="a")
    # Legal thresholds, mock identity still refused at load.
    with pytest.raises(nla.CalibrationRefusal, match="mock"):
        pipeline(tmp_path, rows, judge=MOCK_JUDGE, prefix="b")
    # Legal thresholds and a concrete identity: the constant segments are
    # still a text-blind judge.
    with pytest.raises(
        nla.CalibrationRefusal, match="byte-identical signal maps",
    ):
        pipeline(tmp_path, make_rows(overrides), prefix="c")


def test_constant_segments_per_work_refuse(tmp_path):
    """Even with a concrete identity and licensed thresholds, three
    byte-identical segment signal maps inside one work are a text-blind
    judge's signature."""
    overrides = {
        sig: _constant_segment_builder(builder)
        for sig, builder in BASE_BUILDERS.items()
    }
    with pytest.raises(
        nla.CalibrationRefusal, match="byte-identical signal maps",
    ):
        pipeline(tmp_path, make_rows(overrides))


def test_heterogeneous_manifest_identities_refuse(tmp_path):
    rows = make_rows()
    rows[3]["segments"][1]["judge_identity"]["model_revision"] = "rev-2"
    with pytest.raises(
        nla.CalibrationRefusal, match="distinct judge identities",
    ):
        pipeline(tmp_path, rows)


def test_manifest_identity_must_match_the_registration(tmp_path):
    rows = make_rows(judge=dict(JUDGE, model="other-judge"))
    with pytest.raises(nla.CalibrationRefusal, match="pre-declared"):
        pipeline(tmp_path, rows)  # registration still says "test-judge"


def test_receipt_judge_block_is_derived_from_the_manifest(tmp_path):
    other = dict(JUDGE, model="derived-judge")
    receipt, _ = pipeline(tmp_path, make_rows(judge=other), judge=other)
    assert receipt["judge"] == other
    assert nla.derive_manifest_judge(make_rows(judge=other)) == other


def test_segment_missing_judge_identity_refuses(tmp_path):
    rows = make_rows()
    del rows[0]["segments"][0]["judge_identity"]
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="key set mismatch"):
        nla.load_manifest_rows(m, values_free=False)


def test_host_resolved_sentinel_in_manifest_refuses(tmp_path):
    rows = make_rows(judge=dict(JUDGE, model_revision="host-resolved"))
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="host-resolved"):
        nla.load_manifest_rows(m, values_free=False)


# ---------- achieved lengths are recomputed, never asserted --------------

def test_inflated_recorded_n_words_refuses(tmp_path):
    """Codex P1: `_segment_bands` trusted `seg["n_words"]`, so genuine
    5,000-word segments recorded as 20,000 produced a receipt certifying a
    20,000-word band no segment ever exercised."""
    rows = make_rows()
    rows[0]["segments"][0]["n_words"] = 20_000
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(
        nla.CalibrationRefusal, match="words actually present in 'content'",
    ):
        nla.load_manifest_rows(m, values_free=False)


def test_content_hash_mismatch_refuses(tmp_path):
    rows = make_rows()
    rows[2]["segments"][1]["content"] += " smuggled extra words here"
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(
        nla.CalibrationRefusal, match="does not match the framed digest",
    ):
        nla.load_manifest_rows(m, values_free=False)


def test_segment_without_content_refuses(tmp_path):
    rows = make_rows()
    del rows[0]["segments"][0]["content"]
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="key set mismatch"):
        nla.load_manifest_rows(m, values_free=False)


def test_band_tracks_the_bound_content(tmp_path):
    """Lengthen a segment's actual text (fixing its recorded fields) and the
    band moves: the receipt reports what was exercised."""
    receipt, _ = pipeline(tmp_path, make_rows())
    assert receipt["validated_segment_words"]["max"] == BAND_MAX_WORDS

    rows = make_rows()
    seg = rows[0]["segments"][0]
    seg["content"] = seg["content"] + " " + " ".join(
        f"extra{k}" for k in range(4000)
    )
    seg["content_sha256"] = nls.content_digest(seg["content"])
    seg["n_words"] = nls.count_words(seg["content"])
    wider, _ = pipeline(tmp_path, rows, prefix="wide")
    assert wider["validated_segment_words"]["max"] == seg["n_words"]
    assert wider["validated_segment_words"]["max"] > BAND_MAX_WORDS


# ---------- end-to-end statistic classes -------------------------------

def test_mean_and_prevalence_signals_validate(tmp_path):
    receipt, _ = pipeline(tmp_path, make_rows())
    ps = receipt["per_signal"]

    mean_cell = ps[SIG_MEAN]
    assert mean_cell["verdict"] == "validated_aggregatable"
    assert mean_cell["operator"] == "mean"
    assert mean_cell["units"] == "response_units"
    assert mean_cell["support"] == N_WORKS
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] == pytest.approx(1.0)
    assert stats["spearman_rho"]["direction"] == "min"
    # segment mean is exactly whole + 1/3 everywhere (and 5 - 1/3 at the
    # top of the scale), so MAD is exactly 1/3.
    assert stats["mad_response_units"]["value"] == pytest.approx(1 / 3)
    assert stats["mad_response_units"]["direction"] == "max"

    ord_cell = ps[SIG_ORD]
    assert ord_cell["verdict"] == "validated_aggregatable"
    ostats = {s["name"]: s for s in ord_cell["statistics"]}
    assert ostats["spearman_rho"]["value"] == pytest.approx(1.0)
    assert ostats["mad_response_units"]["value"] == pytest.approx(1 / 3)

    for sig in (SIG_PREV, SIG_PREV_MULTI):
        prev_cell = ps[sig]
        assert prev_cell["verdict"] == "validated_aggregatable"
        assert prev_cell["operator"] == "prevalence"
        assert prev_cell["units"] == "prevalence"
        (auc_stat,) = prev_cell["statistics"]
        assert auc_stat["name"] == "auc"
        assert auc_stat["value"] == pytest.approx(1.0)
        assert auc_stat["direction"] == "min"


def test_mean_signal_fails_spearman(tmp_path):
    def inverted(i):
        whole, segs = _mean_values(i)
        flipped = str(6 - int(whole))
        return whole, [flipped, flipped, str(max(1, int(flipped) - 1))]

    receipt, _ = pipeline(tmp_path, make_rows({SIG_MEAN: inverted}))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    # Evaluated and failed → EMPIRICAL not_aggregatable, statistics
    # recorded.
    assert mean_cell["verdict"] == "not_aggregatable"
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] == pytest.approx(-1.0)


def test_mean_signal_fails_mad_only(tmp_path):
    """rho passes but MAD does not: BOTH mean statistics must pass."""
    def spread(i):
        whole, _ = _mean_values(i)
        v = int(whole)
        shifted = str(min(5, v + 2))
        other = str(min(5, v + 1))
        return whole, [shifted, shifted, other]

    receipt, _ = pipeline(tmp_path, make_rows({SIG_MEAN: spread}))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] >= stats["spearman_rho"][
        "threshold"
    ]
    assert stats["mad_response_units"]["value"] > stats[
        "mad_response_units"
    ]["threshold"]
    assert mean_cell["verdict"] == "not_aggregatable"


def test_mean_signal_constant_whole_is_indeterminate(tmp_path):
    def constant_whole(i):
        return "3", ["2", "3", str(1 + i % 5)]

    receipt, _ = pipeline(tmp_path, make_rows({SIG_MEAN: constant_whole}))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    assert mean_cell["verdict"] == "indeterminate"
    assert mean_cell["statistics"] == []


def test_prevalence_signal_fails(tmp_path):
    def inverted(i):
        whole, segs = _prev_values(i)
        return whole, ["no", "no", "yes"] if whole == "yes" else [
            "yes", "yes", "no"
        ]

    receipt, _ = pipeline(tmp_path, make_rows({SIG_PREV: inverted}))
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "not_aggregatable"
    (auc_stat,) = prev_cell["statistics"]
    assert auc_stat["value"] == pytest.approx(0.0)


def test_prevalence_single_valued_whole_is_insufficient(tmp_path):
    def all_yes(i):
        _, segs = _prev_values(i)
        return "yes", segs

    receipt, _ = pipeline(tmp_path, make_rows({SIG_PREV: all_yes}))
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "insufficient_support"
    assert prev_cell["statistics"] == []


def test_prevalence_class_support_floor(tmp_path):
    """5 positive works against the licensed class floor of 6."""
    def thin_positive(i):
        _, segs = _prev_values(i)
        if i < 5:
            return "yes", ["yes", "yes", "no"]
        return "no", ["no", "no", "yes"]

    receipt, _ = pipeline(tmp_path, make_rows({SIG_PREV: thin_positive}))
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "insufficient_support"
    assert prev_cell["statistics"] == []


def test_per_signal_support_floor(tmp_path):
    """17 available whole-work cells against the licensed floor of 18."""
    def thin_support(i):
        whole, segs = _mean_values(i)
        if i >= 17:
            return cell(whole, available=False), segs
        return whole, segs

    receipt, _ = pipeline(tmp_path, make_rows({SIG_MEAN: thin_support}))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    assert mean_cell["support"] == 17
    assert mean_cell["verdict"] == "insufficient_support"
    assert mean_cell["statistics"] == []


def test_corpus_below_the_works_floor(tmp_path):
    """23 works against the licensed floor of 24: every evaluable signal
    is insufficient_support and the 12 a-priori signals keep theirs."""
    receipt, _ = pipeline(tmp_path, make_rows(n_works=23))
    assert receipt["corpus_n_works"] == 23
    for signal_id, cell_out in receipt["per_signal"].items():
        if cell_out["operator"] == "not_aggregatable":
            assert cell_out["verdict"] == "not_aggregatable"
        else:
            assert cell_out["verdict"] == "insufficient_support", signal_id
        assert cell_out["statistics"] == []


def test_not_aggregatable_never_evaluated(tmp_path):
    receipt, _ = pipeline(tmp_path, make_rows())
    na_cell = receipt["per_signal"][SIG_NA]
    assert na_cell["verdict"] == "not_aggregatable"
    assert na_cell["operator"] == "not_aggregatable"
    assert na_cell["units"] == "none"
    assert na_cell["statistics"] == []
    # Availability is still counted (deterministic support), values
    # never converted.
    assert na_cell["support"] == N_WORKS


def test_absent_signal_is_insufficient_support(tmp_path):
    receipt, _ = pipeline(tmp_path, make_rows())
    absent = (
        "narrative.structural_streamlining."
        "continuity_of_main_causal_chain"
    )
    cell_out = receipt["per_signal"][absent]
    assert cell_out["support"] == 0
    assert cell_out["verdict"] == "insufficient_support"


# ---------- receipt shape + hashing convention -------------------------

def test_receipt_shape_and_bands(tmp_path):
    receipt, paths = pipeline(tmp_path, make_rows())
    assert set(receipt) == {
        "schema_version", "date", "arm", "signal_id_set_sha256",
        "thresholds_sha256", "registration_sha256", "derivation_sha256",
        "manifest_sha256", "registration_path", "manifest_path",
        "corpus_n_works", "segmenter", "judge",
        "validated_segment_count_range", "validated_segment_words",
        "per_signal",
    }
    assert receipt["schema_version"] == (
        "narrative_longform_validation_receipt/1"
    )
    assert receipt["arm"] == "stability"
    assert receipt["date"] == DATE
    assert receipt["corpus_n_works"] == N_WORKS
    assert receipt["segmenter"] == SEGMENTER
    assert receipt["judge"] == JUDGE
    # Bands COMPUTED from the segments' bound content.
    assert receipt["validated_segment_count_range"] == {"min": 3, "max": 3}
    assert receipt["validated_segment_words"] == {
        "min": BAND_MIN_WORDS, "max": BAND_MAX_WORDS,
        "median": BAND_MEDIAN_WORDS,
    }
    # All 33 signals present, cells exactly shaped.
    assert set(receipt["per_signal"]) == set(nla.SIGNAL_IDS)
    for cell_out in receipt["per_signal"].values():
        assert set(cell_out) == {
            "verdict", "operator", "units", "support", "statistics",
        }
        for stat in cell_out["statistics"]:
            assert set(stat) == {"name", "value", "threshold", "direction"}
            assert stat["direction"] in ("min", "max")


def test_every_receipt_hash_is_framed_under_its_own_domain(tmp_path):
    """Codex P2: raw `hashlib.sha256` was reused across content, offsets,
    parameters, work ids, derivations, registrations, and manifests, so a
    digest could not say which schema it hashed."""
    receipt, paths = pipeline(tmp_path, make_rows())
    for key, path, domain in (
        ("thresholds_sha256", paths["thresholds"],
         nls.DOMAIN_THRESHOLDS_FILE),
        ("registration_sha256", paths["registration"],
         nls.DOMAIN_REGISTRATION_FILE),
        ("manifest_sha256", paths["manifest"], nls.DOMAIN_MANIFEST_FILE),
    ):
        raw = path.read_bytes()
        assert receipt[key] == expected_framed(domain, raw)
        # ...and NOT the raw digest the three fields used to share a
        # construction with.
        assert receipt[key] != "sha256:" + hashlib.sha256(raw).hexdigest()
    ids_payload = canonical(sorted(nla.SIGNAL_IDS))
    assert receipt["signal_id_set_sha256"] == expected_framed(
        nls.DOMAIN_SIGNAL_ID_SET, ids_payload)
    assert receipt["derivation_sha256"].startswith("sha256:")
    # The three file digests are distinct even though the domain is the
    # only thing distinguishing two identical artifacts would have.
    assert len({
        receipt["thresholds_sha256"], receipt["registration_sha256"],
        receipt["manifest_sha256"],
    }) == 3


def test_identical_bytes_under_two_file_domains_differ(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"identical")
    b.write_bytes(b"identical")
    assert nls.framed_file_digest(nls.DOMAIN_THRESHOLDS_FILE, a) != \
        nls.framed_file_digest(nls.DOMAIN_MANIFEST_FILE, b)
    assert nls.framed_file_digest(nls.DOMAIN_THRESHOLDS_FILE, a) == \
        nls.framed_file_digest(nls.DOMAIN_THRESHOLDS_FILE, b)


def test_registration_record_contents(tmp_path):
    _, paths = pipeline(tmp_path, make_rows())
    registration = json.loads(
        paths["registration"].read_text(encoding="utf-8"),
    )
    assert registration["schema"] == "narrative-longform-registration/1"
    assert registration["segmenter"] == SEGMENTER
    assert registration["judge"] == JUDGE
    # work_ids_sha256 = framed canonical JSON of the sorted work_id list.
    assert registration["work_ids_sha256"] == expected_framed(
        nls.DOMAIN_WORK_IDS,
        canonical([f"w{i:02d}" for i in range(N_WORKS)]),
    )
    assert registration["thresholds_sha256"] == expected_framed(
        nls.DOMAIN_THRESHOLDS_FILE, paths["thresholds"].read_bytes(),
    )


# ---------- registration enforcement -----------------------------------

def test_register_refuses_values_bearing_manifest(tmp_path):
    t = tmp_path / "t.json"
    m = tmp_path / "m.jsonl"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(m, make_rows())  # judged values present
    proc = run_cli([
        "--register", "--manifest", str(m), "--thresholds", str(t),
        "--out", str(tmp_path / "reg.json"), "--date", DATE,
        "--segmenter-version", SEGMENTER["version"],
        "--segmenter-params-sha256", SEGMENTER["params_sha256"],
        "--segment-target-words", str(SEGMENTER["segment_target_words"]),
        "--judge-kind", JUDGE["kind"],
        "--judge-model", JUDGE["model"],
        "--judge-model-revision", JUDGE["model_revision"],
        "--judge-prompt-version", JUDGE["prompt_version"],
    ])
    assert proc.returncode == 2
    assert "values-free" in proc.stderr


def test_register_refuses_segment_values_even_without_whole(tmp_path):
    rows = make_rows()
    for row in rows:
        row["whole_work"] = {}
    with pytest.raises(nla.CalibrationRefusal, match="values-free"):
        m = tmp_path / "m.jsonl"
        write_jsonl(m, rows)
        t = tmp_path / "t.json"
        write_json(t, LICENSED_THRESHOLDS)
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=m,
            segmenter=dict(SEGMENTER), judge=dict(JUDGE),
        )


def test_register_refuses_mock_and_non_concrete_judge(tmp_path):
    t = tmp_path / "t.json"
    d = tmp_path / "d.jsonl"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(d, [{"work_id": "w1"}, {"work_id": "w2"}])
    with pytest.raises(nla.CalibrationRefusal, match="mock"):
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=d,
            segmenter=dict(SEGMENTER), judge=dict(JUDGE, kind="mock"),
        )
    # A `mock` MODEL under any kind is refused too.
    with pytest.raises(nla.CalibrationRefusal, match="mock"):
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=d,
            segmenter=dict(SEGMENTER), judge=dict(JUDGE, model="mock"),
        )
    for field in ("model", "model_revision", "prompt_version"):
        with pytest.raises(nla.CalibrationRefusal):
            nla.build_registration(
                date=DATE, thresholds_path=t, manifest_path=d,
                segmenter=dict(SEGMENTER), judge=dict(JUDGE, **{field: ""}),
            )
    with pytest.raises(nla.CalibrationRefusal, match="host-resolved"):
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=d,
            segmenter=dict(SEGMENTER),
            judge=dict(JUDGE, model_revision="host-resolved"),
        )


def test_register_cli_happy_path(tmp_path):
    t = tmp_path / "t.json"
    d = tmp_path / "d.jsonl"
    out = tmp_path / "reg.json"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(d, [{"work_id": "w1"}, {"work_id": "w2"}])
    proc = run_cli([
        "--register", "--manifest", str(d), "--thresholds", str(t),
        "--out", str(out), "--date", DATE,
        "--segmenter-version", SEGMENTER["version"],
        "--segmenter-params-sha256", SEGMENTER["params_sha256"],
        "--segment-target-words", str(SEGMENTER["segment_target_words"]),
        "--judge-kind", JUDGE["kind"],
        "--judge-model", JUDGE["model"],
        "--judge-model-revision", JUDGE["model_revision"],
        "--judge-prompt-version", JUDGE["prompt_version"],
    ])
    assert proc.returncode == 0, proc.stderr
    registration = json.loads(out.read_text(encoding="utf-8"))
    assert registration["schema"] == "narrative-longform-registration/1"


# ---------- registration matching at evaluate ---------------------------

def test_evaluate_refuses_post_hoc_thresholds(tmp_path):
    _, paths = pipeline(tmp_path, make_rows())
    # Retune thresholds AFTER registration → hash mismatch → refuse.
    tampered = json.loads(json.dumps(LICENSED_THRESHOLDS))
    tampered["per_operator"]["mean"]["spearman_min"] = 0.76
    write_json(paths["thresholds"], tampered)
    with pytest.raises(
        nla.CalibrationRefusal, match="thresholds_sha256",
    ):
        nla.build_receipt(
            date=DATE, thresholds_path=paths["thresholds"],
            registration_path=paths["registration"],
            manifest_path=paths["manifest"],
        )


def test_evaluate_refuses_work_id_drift(tmp_path):
    _, paths = pipeline(tmp_path, make_rows())
    rows = make_rows()
    rows[0]["work_id"] = "w00-substituted"
    for seg in rows[0]["segments"]:
        seg["segment_id"] = "sub-" + seg["segment_id"]
    write_jsonl(paths["manifest"], rows)
    with pytest.raises(nla.CalibrationRefusal, match="work_ids_sha256"):
        nla.build_receipt(
            date=DATE, thresholds_path=paths["thresholds"],
            registration_path=paths["registration"],
            manifest_path=paths["manifest"],
        )


def test_evaluate_cli_requires_registration_and_date(tmp_path):
    t = tmp_path / "t.json"
    m = tmp_path / "m.jsonl"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(m, make_rows())
    proc = run_cli([
        "--evaluate", "--manifest", str(m), "--thresholds", str(t),
        "--out", str(tmp_path / "r.json"), "--date", DATE,
    ])
    assert proc.returncode == 2
    assert "--registration" in proc.stderr
    proc = run_cli([
        "--evaluate", "--manifest", str(m), "--thresholds", str(t),
        "--registration", str(tmp_path / "reg.json"),
        "--out", str(tmp_path / "r.json"),
    ])
    assert proc.returncode == 2
    assert "--date" in proc.stderr


def test_date_must_be_canonical_iso(tmp_path):
    t = tmp_path / "t.json"
    d = tmp_path / "d.jsonl"
    write_json(t, LICENSED_THRESHOLDS)
    write_jsonl(d, [{"work_id": "w1"}])
    with pytest.raises(nla.CalibrationRefusal, match="ISO"):
        nla.build_registration(
            date="07/27/2026", thresholds_path=t, manifest_path=d,
            segmenter=dict(SEGMENTER), judge=dict(JUDGE),
        )


# ---------- thresholds artifact strictness -------------------------------

def test_thresholds_schema_strict(tmp_path):
    t = tmp_path / "t.json"
    # Wrong schema string.
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["schema"] = "narrative-longform-thresholds/2"
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="schema"):
        nla.load_thresholds(t)
    # Extra key refused (hash-frozen artifact; no silent riders).
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["note"] = "post-hoc rider"
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="unexpected"):
        nla.load_thresholds(t)
    # Zero floor refused.
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    bad["floors"]["min_works"] = 0
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="min_works"):
        nla.load_thresholds(t)
    # Missing statistic threshold refused.
    bad = json.loads(json.dumps(LICENSED_THRESHOLDS))
    del bad["per_operator"]["mean"]["mad_max_response_units"]
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="missing"):
        nla.load_thresholds(t)
    # The licensed artifact is itself valid, and stricter floors pass.
    write_json(t, LICENSED_THRESHOLDS)
    assert nla.load_thresholds(t)["floors"]["min_works"] == 24
    stricter = json.loads(json.dumps(LICENSED_THRESHOLDS))
    stricter["floors"] = {
        "min_works": 40, "min_signal_support": 30, "min_class_support": 10,
    }
    write_json(t, stricter)
    assert nla.load_thresholds(t)["floors"]["min_works"] == 40


# ---------- verification / tamper detection ------------------------------

def _write_receipt(path, receipt):
    path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_verify_receipt_roundtrip_and_tamper(tmp_path):
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(receipt_path, receipt)
    # Genuine receipt verifies.
    verified = nla.verify_receipt(
        receipt_path, paths["thresholds"], paths["registration"],
        paths["manifest"], DATE,
    )
    assert verified["per_signal"][SIG_MEAN]["verdict"] == (
        "validated_aggregatable"
    )

    # THE attack: hand-edit a verdict. Make a failing signal claim
    # validation while its Spearman stays visibly terrible.
    def inverted(i):
        whole, _ = _mean_values(i)
        flipped = str(6 - int(whole))
        return whole, [flipped, flipped, str(max(1, int(flipped) - 1))]

    failing_receipt, fpaths = pipeline(
        tmp_path, make_rows({SIG_MEAN: inverted}), prefix="fail",
    )
    assert failing_receipt["per_signal"][SIG_MEAN]["verdict"] == (
        "not_aggregatable"
    )
    tampered = json.loads(json.dumps(failing_receipt))
    tampered["per_signal"][SIG_MEAN]["verdict"] = "validated_aggregatable"
    tampered_path = tmp_path / "tampered.json"
    _write_receipt(tampered_path, tampered)
    with pytest.raises(nla.CalibrationRefusal, match="per_signal"):
        nla.verify_receipt(
            tampered_path, fpaths["thresholds"], fpaths["registration"],
            fpaths["manifest"], DATE,
        )

    # Tampering a statistic value alone is also refused.
    tampered2 = json.loads(json.dumps(receipt))
    tampered2["per_signal"][SIG_MEAN]["statistics"][0]["value"] = 0.02
    tampered2_path = tmp_path / "tampered2.json"
    _write_receipt(tampered2_path, tampered2)
    with pytest.raises(nla.CalibrationRefusal):
        nla.verify_receipt(
            tampered2_path, paths["thresholds"], paths["registration"],
            paths["manifest"], DATE,
        )

    # Tampering the derivation hash is refused.
    tampered3 = json.loads(json.dumps(receipt))
    tampered3["derivation_sha256"] = "sha256:" + "f" * 64
    tampered3_path = tmp_path / "tampered3.json"
    _write_receipt(tampered3_path, tampered3)
    with pytest.raises(
        nla.CalibrationRefusal, match="derivation_sha256",
    ):
        nla.verify_receipt(
            tampered3_path, paths["thresholds"], paths["registration"],
            paths["manifest"], DATE,
        )

    # Swapping the manifest out from under a receipt is refused.
    other_rows = make_rows()
    seg = other_rows[0]["segments"][0]
    seg["content"] = seg["content"] + " tail"
    seg["content_sha256"] = nls.content_digest(seg["content"])
    seg["n_words"] = nls.count_words(seg["content"])
    write_jsonl(paths["manifest"], other_rows)
    with pytest.raises(nla.CalibrationRefusal, match="sha256"):
        nla.verify_receipt(
            receipt_path, paths["thresholds"], paths["registration"],
            paths["manifest"], DATE,
        )


def test_verify_refuses_a_predated_or_postdated_receipt(tmp_path):
    """Codex P2: verification rebuilt the expected receipt from the
    RECEIPT'S OWN date and then exempted `date` from comparison, so a
    re-dated receipt re-derived perfectly against its own lie."""
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"

    for forged_date in ("2025-01-01", "2027-12-31"):
        predated = json.loads(json.dumps(receipt))
        predated["date"] = forged_date
        _write_receipt(receipt_path, predated)
        # Against the real date: the receipt's date is now a claim, checked.
        with pytest.raises(nla.CalibrationRefusal, match="date"):
            nla.verify_receipt(
                receipt_path, paths["thresholds"], paths["registration"],
                paths["manifest"], DATE,
            )
        # And asserting the forged date does not rescue it either: the date
        # is inside the derivation preimage.
        with pytest.raises(
            nla.CalibrationRefusal, match="derivation_sha256",
        ):
            nla.verify_receipt(
                receipt_path, paths["thresholds"], paths["registration"],
                paths["manifest"], forged_date,
            )


def test_verify_refuses_relabelled_artifact_paths(tmp_path):
    """`registration_path` and `manifest_path` were exempt, so a receipt
    could name trusted-looking artifacts while being verified against
    others. Spec 79 S3 binds the receipt "by path and hash"."""
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"
    relabelled = json.loads(json.dumps(receipt))
    relabelled["manifest_path"] = "/audited/corpus/manifest.jsonl"
    _write_receipt(receipt_path, relabelled)
    with pytest.raises(nla.CalibrationRefusal, match="manifest_path"):
        nla.verify_receipt(
            receipt_path, paths["thresholds"], paths["registration"],
            paths["manifest"], DATE,
        )


def test_verify_cli(tmp_path):
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(receipt_path, receipt)
    base = [
        "--verify", "--manifest", str(paths["manifest"]),
        "--thresholds", str(paths["thresholds"]),
        "--registration", str(paths["registration"]),
        "--out", str(receipt_path),
    ]
    proc = run_cli(base + ["--date", DATE])
    assert proc.returncode == 0, proc.stderr
    assert "verified" in proc.stdout
    # --verify without --date refuses rather than trusting the receipt.
    proc = run_cli(base)
    assert proc.returncode == 2
    assert "--date" in proc.stderr
    # Tampered → exit 2.
    obj = json.loads(receipt_path.read_text(encoding="utf-8"))
    obj["per_signal"][SIG_NA]["verdict"] = "validated_aggregatable"
    _write_receipt(receipt_path, obj)
    proc = run_cli(base + ["--date", DATE])
    assert proc.returncode == 2
    assert "refused" in proc.stderr


# ---------- determinism ---------------------------------------------------

def test_cli_byte_determinism(tmp_path):
    _, paths = pipeline(tmp_path, make_rows())
    out_a = tmp_path / "receipt-a.json"
    out_b = tmp_path / "receipt-b.json"
    for out in (out_a, out_b):
        proc = run_cli([
            "--evaluate", "--manifest", str(paths["manifest"]),
            "--thresholds", str(paths["thresholds"]),
            "--registration", str(paths["registration"]),
            "--out", str(out), "--date", DATE,
        ])
        assert proc.returncode == 0, proc.stderr
    assert out_a.read_bytes() == out_b.read_bytes()
    receipt = json.loads(out_a.read_text(encoding="utf-8"))
    assert receipt["per_signal"][SIG_MEAN]["verdict"] == (
        "validated_aggregatable"
    )


# ---------- manifest strictness -------------------------------------------

def test_manifest_unknown_signal_and_illegal_value_refused(tmp_path):
    rows = make_rows()
    rows[0]["whole_work"]["narrative.bogus.signal"] = cell("3")
    m = tmp_path / "m.jsonl"
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="unknown signal_id"):
        nla.load_manifest_rows(m, values_free=False)

    rows = make_rows()
    rows[0]["whole_work"][SIG_MEAN] = cell("6")  # off the Likert scale
    write_jsonl(m, rows)
    loaded = nla.load_manifest_rows(m, values_free=False)
    with pytest.raises(nla.CalibrationRefusal, match="illegal response"):
        nla._evaluate_signal(
            nla.SIGNALS[SIG_MEAN], loaded, LICENSED_THRESHOLDS,
        )

    rows = make_rows()
    rows[0]["work_id"] = rows[1]["work_id"]  # duplicate
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="duplicate work_id"):
        nla.load_manifest_rows(m, values_free=False)
