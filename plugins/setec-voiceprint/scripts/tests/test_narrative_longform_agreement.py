#!/usr/bin/env python3
"""Tests for calibration/narrative_longform_agreement.py (spec 77 M1).

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
  * Registration: values-free enforcement, mock-judge refusal,
    thresholds/work-ids matching required at evaluate.
  * Receipt: exact key set, computed segment bands, hashing
    convention (plain file hashes vs canonical-JSON hashes).
  * verify_receipt: re-derives verdicts from artifacts; a hand-edited
    "validated_aggregatable with Spearman 0.02" receipt is refused.
  * CLI byte-determinism across two subprocess runs.

All fixtures are synthetic; stdlib + pytest only.
"""

from __future__ import annotations

import hashlib
import json
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
from narrative_feature_schema import (  # type: ignore  # noqa: E402
    CORE_FEATURES,
)

MODULE = CALIB / "narrative_longform_agreement.py"
DATE = "2026-07-27"

# ---------- documented EXAMPLE thresholds artifact -------------------
# This is the documented example of the thresholds schema
# (narrative-longform-thresholds/1). The floors are the spec-77 study
# floors; the per-operator values are ILLUSTRATIVE ONLY — real values
# are operator-frozen later, before the M2 study registers. Direction
# is explicit in the key names: *_min = higher-is-better,
# *_max = lower-is-better. A mean signal passes only if BOTH its
# statistics pass.
EXAMPLE_THRESHOLDS = {
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

# Small floors so 4-work synthetic fixtures can exercise the
# statistics paths; identical schema.
TEST_THRESHOLDS = {
    "schema": "narrative-longform-thresholds/1",
    "floors": {
        "min_works": 3,
        "min_signal_support": 3,
        "min_class_support": 1,
    },
    "per_operator": {
        "mean": {"spearman_min": 0.7, "mad_max_response_units": 0.75},
        "prevalence": {"auc_min": 0.8},
    },
}

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

# Per-work segment word counts for the 4-work fixture. Sorted word
# list: [4000, 4500, 4800, 5000, 5000, 5000, 5200, 6000] → min 4000,
# max 6000, median (5000 + 5000) / 2 = 5000.0.
WORK_SEG_WORDS = [[4000, 5200], [5000, 5000], [4800, 6000], [5000, 4500]]

# Baseline judged values: every populated signal validates.
#   SIG_MEAN  whole [2,3,4,5], segment means [2,3,4,5] → rho 1.0, MAD 0
#   SIG_ORD   whole idx [0,1,2,3], seg means [0.5,1,2,2.5] → rho 1.0,
#             MAD (0.5+0+0+0.5)/4 = 0.25
#   SIG_PREV  whole [1,1,0,0], prevalence [1.0,0.5,0.0,0.0] → AUC 1.0
#   SIG_PREV_MULTI same shape → AUC 1.0
#   SIG_NA    populated but a priori not_aggregatable
BASE_DATA = {
    SIG_MEAN: (
        ["2", "3", "4", "5"],
        [["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
    ),
    SIG_ORD: (
        ["very_low", "low", "medium", "high"],
        [
            ["very_low", "low"], ["low", "low"],
            ["medium", "medium"], ["high", "medium"],
        ],
    ),
    SIG_PREV: (
        ["yes", "yes", "no", "no"],
        [["yes", "yes"], ["yes", "no"], ["no", "no"], ["no", "no"]],
    ),
    SIG_PREV_MULTI: (
        [
            ["philosophical_debate", "advance_plot"],
            ["advance_plot"],
            ["philosophical_debate"],
            ["reveal_character"],
        ],
        [
            [["philosophical_debate"],
             ["philosophical_debate", "comic_relief"]],
            [["advance_plot"], ["advance_plot"]],
            [["philosophical_debate"], ["advance_plot"]],
            [["worldbuilding"], ["reveal_character"]],
        ],
    ),
    SIG_NA: (
        ["3", "3", "3", "3"],
        [["2", "4"], ["2", "4"], ["2", "4"], ["2", "4"]],
    ),
}


def cell(value, available=True):
    return {"value": value, "available": available}


def make_rows(data=None):
    """Build 4 manifest rows from {signal_id: (whole[4], segs[4][2])}.

    Entries may be raw responses or preformed cell dicts (to set
    available=False).
    """
    data = data if data is not None else BASE_DATA
    rows = []
    for i in range(4):
        work_id = f"w{i + 1}"
        whole = {}
        seg_signal_maps = [dict(), dict()]
        for sig, (whole_vals, seg_vals) in data.items():
            wv = whole_vals[i]
            whole[sig] = wv if isinstance(wv, dict) and "available" in wv \
                else cell(wv)
            for j in range(2):
                sv = seg_vals[i][j]
                seg_signal_maps[j][sig] = (
                    sv if isinstance(sv, dict) and "available" in sv
                    else cell(sv)
                )
        segments = []
        for j in range(2):
            seg_id = f"{work_id}-s{j}"
            segments.append({
                "segment_id": seg_id,
                "content_sha256": "sha256:" + hashlib.sha256(
                    seg_id.encode("utf-8"),
                ).hexdigest(),
                "n_words": WORK_SEG_WORDS[i][j],
                "signals": seg_signal_maps[j],
            })
        rows.append({
            "work_id": work_id,
            "n_words": 20000,
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


def pipeline(tmp_path, rows, thresholds=None, prefix="run"):
    """register (values-free design) then evaluate; returns
    (receipt, paths dict)."""
    thresholds = thresholds if thresholds is not None else TEST_THRESHOLDS
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
        segmenter=dict(SEGMENTER), judge=dict(JUDGE),
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
    floors = TEST_THRESHOLDS["floors"]
    passing = [
        {"name": "spearman_rho", "value": 0.9, "threshold": 0.7,
         "direction": "min"},
        {"name": "mad_response_units", "value": 0.1, "threshold": 0.75,
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
        operator="mean", corpus_n_works=2, support=4, floors=floors,
        degenerate=True, statistics=None,
    ) == "insufficient_support"
    # 3. per-signal support floor.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=4, support=2, floors=floors,
        statistics=passing,
    ) == "insufficient_support"
    # 4. prevalence class floor (single-valued whole = n == 0 case).
    assert nla.derive_verdict(
        operator="prevalence", corpus_n_works=4, support=4,
        floors=floors, n_pos=4, n_neg=0,
        statistics=[{"name": "auc", "value": 1.0, "threshold": 0.8,
                     "direction": "min"}],
    ) == "insufficient_support"
    # 5. degenerate → indeterminate once floors pass.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=4, support=4, floors=floors,
        degenerate=True, statistics=None,
    ) == "indeterminate"
    # 6. thresholds: all pass → validated; any failure → empirical
    #    not_aggregatable; boundary equality passes for both
    #    directions.
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=4, support=4, floors=floors,
        statistics=passing,
    ) == "validated_aggregatable"
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=4, support=4, floors=floors,
        statistics=[
            {"name": "spearman_rho", "value": 0.7, "threshold": 0.7,
             "direction": "min"},
            {"name": "mad_response_units", "value": 0.75,
             "threshold": 0.75, "direction": "max"},
        ],
    ) == "validated_aggregatable"
    assert nla.derive_verdict(
        operator="mean", corpus_n_works=4, support=4, floors=floors,
        statistics=[
            {"name": "spearman_rho", "value": 0.9, "threshold": 0.7,
             "direction": "min"},
            {"name": "mad_response_units", "value": 1.2,
             "threshold": 0.75, "direction": "max"},
        ],
    ) == "not_aggregatable"


# ---------- end-to-end statistic classes -------------------------------

def test_mean_and_prevalence_signals_validate(tmp_path):
    receipt, _ = pipeline(tmp_path, make_rows())
    ps = receipt["per_signal"]

    mean_cell = ps[SIG_MEAN]
    assert mean_cell["verdict"] == "validated_aggregatable"
    assert mean_cell["operator"] == "mean"
    assert mean_cell["units"] == "response_units"
    assert mean_cell["support"] == 4
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] == pytest.approx(1.0)
    assert stats["spearman_rho"]["direction"] == "min"
    assert stats["mad_response_units"]["value"] == pytest.approx(0.0)
    assert stats["mad_response_units"]["direction"] == "max"

    ord_cell = ps[SIG_ORD]
    assert ord_cell["verdict"] == "validated_aggregatable"
    ostats = {s["name"]: s for s in ord_cell["statistics"]}
    # ordinal → 0-based index units; MAD hand-computed 0.25.
    assert ostats["mad_response_units"]["value"] == pytest.approx(0.25)

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
    data = dict(BASE_DATA)
    data[SIG_MEAN] = (
        ["2", "3", "4", "5"],
        [["5", "5"], ["4", "4"], ["3", "3"], ["2", "2"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    # Evaluated and failed → EMPIRICAL not_aggregatable, statistics
    # recorded.
    assert mean_cell["verdict"] == "not_aggregatable"
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] == pytest.approx(-1.0)


def test_mean_signal_fails_mad_only(tmp_path):
    # rho passes (0.9487 >= 0.7) but MAD (2+2+1+0)/4 = 1.25 > 0.75:
    # BOTH statistics must pass, so the signal fails.
    data = dict(BASE_DATA)
    data[SIG_MEAN] = (
        ["1", "2", "4", "5"],
        [["3", "3"], ["4", "4"], ["5", "5"], ["5", "5"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    assert mean_cell["verdict"] == "not_aggregatable"
    stats = {s["name"]: s for s in mean_cell["statistics"]}
    assert stats["spearman_rho"]["value"] == pytest.approx(
        0.9486832980505138,
    )
    assert stats["spearman_rho"]["value"] >= stats["spearman_rho"][
        "threshold"
    ]
    assert stats["mad_response_units"]["value"] == pytest.approx(1.25)


def test_mean_signal_constant_whole_is_indeterminate(tmp_path):
    data = dict(BASE_DATA)
    data[SIG_MEAN] = (
        ["3", "3", "3", "3"],
        [["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    assert mean_cell["verdict"] == "indeterminate"
    assert mean_cell["statistics"] == []


def test_prevalence_signal_fails(tmp_path):
    data = dict(BASE_DATA)
    data[SIG_PREV] = (
        ["yes", "yes", "no", "no"],
        [["no", "no"], ["no", "no"], ["yes", "yes"], ["yes", "no"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "not_aggregatable"
    (auc_stat,) = prev_cell["statistics"]
    assert auc_stat["value"] == pytest.approx(0.0)


def test_prevalence_single_valued_whole_is_insufficient(tmp_path):
    data = dict(BASE_DATA)
    data[SIG_PREV] = (
        ["yes", "yes", "yes", "yes"],
        [["yes", "yes"], ["yes", "no"], ["no", "no"], ["no", "no"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "insufficient_support"
    assert prev_cell["statistics"] == []


def test_prevalence_class_support_floor(tmp_path):
    thresholds = json.loads(json.dumps(TEST_THRESHOLDS))
    thresholds["floors"]["min_class_support"] = 2
    data = dict(BASE_DATA)
    data[SIG_PREV] = (
        ["yes", "no", "no", "no"],  # n_pos = 1 < 2
        [["yes", "yes"], ["yes", "no"], ["no", "no"], ["no", "no"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data), thresholds)
    prev_cell = receipt["per_signal"][SIG_PREV]
    assert prev_cell["verdict"] == "insufficient_support"
    assert prev_cell["statistics"] == []


def test_per_signal_support_floor(tmp_path):
    data = dict(BASE_DATA)
    data[SIG_MEAN] = (
        ["2", "3", cell("4", available=False), cell("5", available=False)],
        [["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
    )
    receipt, _ = pipeline(tmp_path, make_rows(data))
    mean_cell = receipt["per_signal"][SIG_MEAN]
    assert mean_cell["support"] == 2  # < min_signal_support 3
    assert mean_cell["verdict"] == "insufficient_support"
    assert mean_cell["statistics"] == []


def test_works_floor_with_example_thresholds(tmp_path):
    # The documented EXAMPLE floors (24 / 18 / 6) against a 4-work
    # corpus: every evaluable signal is insufficient_support; the 12
    # not_aggregatable signals keep their a-priori verdict.
    receipt, _ = pipeline(tmp_path, make_rows(), EXAMPLE_THRESHOLDS)
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
    assert na_cell["support"] == 4


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
    assert receipt["corpus_n_works"] == 4
    assert receipt["segmenter"] == SEGMENTER
    assert receipt["judge"] == JUDGE
    # Bands COMPUTED from the manifest's segments.
    assert receipt["validated_segment_count_range"] == {"min": 2, "max": 2}
    assert receipt["validated_segment_words"] == {
        "min": 4000, "max": 6000, "median": 5000.0,
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
    # Hashing convention: plain file hashes for the three artifacts...
    for key, path in (
        ("thresholds_sha256", paths["thresholds"]),
        ("registration_sha256", paths["registration"]),
        ("manifest_sha256", paths["manifest"]),
    ):
        assert receipt[key] == "sha256:" + hashlib.sha256(
            path.read_bytes(),
        ).hexdigest()
    # ...canonical JSON for the id set.
    expected_ids = json.dumps(
        sorted(nla.SIGNAL_IDS), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert receipt["signal_id_set_sha256"] == (
        "sha256:" + hashlib.sha256(expected_ids).hexdigest()
    )
    assert receipt["derivation_sha256"].startswith("sha256:")


def test_registration_record_contents(tmp_path):
    _, paths = pipeline(tmp_path, make_rows())
    registration = json.loads(
        paths["registration"].read_text(encoding="utf-8"),
    )
    assert registration["schema"] == "narrative-longform-registration/1"
    assert registration["segmenter"] == SEGMENTER
    assert registration["judge"] == JUDGE
    # work_ids_sha256 = canonical JSON of the sorted work_id list.
    expected = json.dumps(
        ["w1", "w2", "w3", "w4"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    assert registration["work_ids_sha256"] == (
        "sha256:" + hashlib.sha256(expected).hexdigest()
    )
    assert registration["thresholds_sha256"] == "sha256:" + hashlib.sha256(
        paths["thresholds"].read_bytes(),
    ).hexdigest()


# ---------- registration enforcement -----------------------------------

def test_register_refuses_values_bearing_manifest(tmp_path):
    t = tmp_path / "t.json"
    m = tmp_path / "m.jsonl"
    write_json(t, TEST_THRESHOLDS)
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
        write_json(t, TEST_THRESHOLDS)
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=m,
            segmenter=dict(SEGMENTER), judge=dict(JUDGE),
        )


def test_register_refuses_mock_and_non_concrete_judge(tmp_path):
    t = tmp_path / "t.json"
    d = tmp_path / "d.jsonl"
    write_json(t, TEST_THRESHOLDS)
    write_jsonl(d, [{"work_id": "w1"}, {"work_id": "w2"}])
    mock_judge = dict(JUDGE, kind="mock")
    with pytest.raises(nla.CalibrationRefusal, match="mock"):
        nla.build_registration(
            date=DATE, thresholds_path=t, manifest_path=d,
            segmenter=dict(SEGMENTER), judge=mock_judge,
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
    write_json(t, TEST_THRESHOLDS)
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
    tampered = json.loads(json.dumps(TEST_THRESHOLDS))
    tampered["per_operator"]["mean"]["spearman_min"] = 0.1
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
    rows[0]["work_id"] = "w1-substituted"
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
    write_json(t, TEST_THRESHOLDS)
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
    write_json(t, TEST_THRESHOLDS)
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
    bad = json.loads(json.dumps(TEST_THRESHOLDS))
    bad["schema"] = "narrative-longform-thresholds/2"
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="schema"):
        nla.load_thresholds(t)
    # Extra key refused (hash-frozen artifact; no silent riders).
    bad = json.loads(json.dumps(TEST_THRESHOLDS))
    bad["note"] = "post-hoc rider"
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="unexpected"):
        nla.load_thresholds(t)
    # Zero floor refused.
    bad = json.loads(json.dumps(TEST_THRESHOLDS))
    bad["floors"]["min_works"] = 0
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="min_works"):
        nla.load_thresholds(t)
    # Missing statistic threshold refused.
    bad = json.loads(json.dumps(TEST_THRESHOLDS))
    del bad["per_operator"]["mean"]["mad_max_response_units"]
    write_json(t, bad)
    with pytest.raises(nla.CalibrationRefusal, match="missing"):
        nla.load_thresholds(t)
    # The documented example is itself valid.
    write_json(t, EXAMPLE_THRESHOLDS)
    assert nla.load_thresholds(t)["floors"]["min_works"] == 24


# ---------- verification / tamper detection ------------------------------

def test_verify_receipt_roundtrip_and_tamper(tmp_path):
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # Genuine receipt verifies.
    verified = nla.verify_receipt(
        receipt_path, paths["thresholds"], paths["registration"],
        paths["manifest"],
    )
    assert verified["per_signal"][SIG_MEAN]["verdict"] == (
        "validated_aggregatable"
    )

    # THE attack: hand-edit a verdict. Make a failing signal claim
    # validation while its Spearman stays visibly terrible.
    data = dict(BASE_DATA)
    data[SIG_MEAN] = (
        ["2", "3", "4", "5"],
        [["5", "5"], ["4", "4"], ["3", "3"], ["2", "2"]],
    )
    failing_receipt, fpaths = pipeline(
        tmp_path, make_rows(data), prefix="fail",
    )
    assert failing_receipt["per_signal"][SIG_MEAN]["verdict"] == (
        "not_aggregatable"
    )
    tampered = json.loads(json.dumps(failing_receipt))
    tampered["per_signal"][SIG_MEAN]["verdict"] = "validated_aggregatable"
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(nla.CalibrationRefusal, match="per_signal"):
        nla.verify_receipt(
            tampered_path, fpaths["thresholds"], fpaths["registration"],
            fpaths["manifest"],
        )

    # Tampering a statistic value alone is also refused.
    tampered2 = json.loads(json.dumps(receipt))
    tampered2["per_signal"][SIG_MEAN]["statistics"][0]["value"] = 0.02
    tampered2_path = tmp_path / "tampered2.json"
    tampered2_path.write_text(
        json.dumps(tampered2, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(nla.CalibrationRefusal):
        nla.verify_receipt(
            tampered2_path, paths["thresholds"], paths["registration"],
            paths["manifest"],
        )

    # Tampering the derivation hash is refused.
    tampered3 = json.loads(json.dumps(receipt))
    tampered3["derivation_sha256"] = "sha256:" + "f" * 64
    tampered3_path = tmp_path / "tampered3.json"
    tampered3_path.write_text(
        json.dumps(tampered3, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        nla.CalibrationRefusal, match="derivation_sha256",
    ):
        nla.verify_receipt(
            tampered3_path, paths["thresholds"], paths["registration"],
            paths["manifest"],
        )

    # Swapping the manifest out from under a receipt is refused.
    other_rows = make_rows()
    other_rows[0]["segments"][0]["n_words"] = 4001
    write_jsonl(paths["manifest"], other_rows)
    with pytest.raises(nla.CalibrationRefusal, match="sha256"):
        nla.verify_receipt(
            receipt_path, paths["thresholds"], paths["registration"],
            paths["manifest"],
        )


def test_verify_cli(tmp_path):
    receipt, paths = pipeline(tmp_path, make_rows())
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    proc = run_cli([
        "--verify", "--manifest", str(paths["manifest"]),
        "--thresholds", str(paths["thresholds"]),
        "--registration", str(paths["registration"]),
        "--out", str(receipt_path),
    ])
    assert proc.returncode == 0, proc.stderr
    assert "verified" in proc.stdout
    # Tampered → exit 2.
    obj = json.loads(receipt_path.read_text(encoding="utf-8"))
    obj["per_signal"][SIG_NA]["verdict"] = "validated_aggregatable"
    receipt_path.write_text(
        json.dumps(obj, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    proc = run_cli([
        "--verify", "--manifest", str(paths["manifest"]),
        "--thresholds", str(paths["thresholds"]),
        "--registration", str(paths["registration"]),
        "--out", str(receipt_path),
    ])
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
    # And the subprocess receipt matches the in-process one byte-for-
    # byte modulo nothing: same serializer, same content.
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
            nla.SIGNALS[SIG_MEAN], loaded, TEST_THRESHOLDS,
        )

    rows = make_rows()
    rows[0]["work_id"] = rows[1]["work_id"]  # duplicate
    write_jsonl(m, rows)
    with pytest.raises(nla.CalibrationRefusal, match="duplicate work_id"):
        nla.load_manifest_rows(m, values_free=False)
