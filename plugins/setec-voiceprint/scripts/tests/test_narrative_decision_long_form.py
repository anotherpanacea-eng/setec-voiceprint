#!/usr/bin/env python3
"""Tests for narrative_decision_long_form.py (spec 79 M1).

Model-free: mock/manifest judges only, no network. Covers the S1
signal-id pins, the operator-table partition (disjoint AND total over the
real CORE_FEATURES), length routing, per-segment manifest keying, the
mock/degenerate-judge refusals, the M1 suppression envelope, the emit
guard (including the mandated pair: an injected float ANYWHERE raises,
and the real mandated envelope PASSES), calibration-only mode, and the
judge-identity-bound resume cache.
"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import narrative_decision_long_form as ndlf  # type: ignore  # noqa: E402
import narrative_feature_schema as nfs  # type: ignore  # noqa: E402
import narrative_longform_segment as nls  # type: ignore  # noqa: E402
from output_schema import REASON_CATEGORIES  # type: ignore  # noqa: E402


# ---------- fixtures ------------------------------------------------

def _make_text(n_paras: int, words_per_para: int = 200) -> str:
    """Synthetic prose: unique-ish tokens, single-blank-line paragraph
    boundaries, no chapter headings / scene breaks / roman-numeral
    lines, so the segmenter lands on the paragraph tier."""
    paras = []
    for p in range(n_paras):
        words = [f"w{p:03d}q{i:03d}" for i in range(words_per_para)]
        paras.append(" ".join(words))
    return "\n\n".join(paras) + "\n"


def _full_values(seed: int) -> dict:
    """A complete judge values dict; distinct seeds < 60 yield distinct
    value vectors (some option list length in {2,..,6} separates them)."""
    values: dict = {}
    for f in nfs.CORE_FEATURES:
        opts = f.response_options
        if f.feature_type == "multi":
            values[f.key] = [opts[seed % len(opts)]]
        else:
            values[f.key] = opts[seed % len(opts)]
    return values


_IDENTITY = {
    "model": "test-judge",
    "model_revision": "r1",
    "prompt_version": "pv1",
}


def _keyed_manifest(
    seg: "nls.Segmentation",
    *,
    identity: dict | None = None,
    same_values: bool = False,
) -> dict:
    ident = dict(identity or _IDENTITY)
    return {
        s.content_sha256: {
            "values": _full_values(0 if same_values else s.index),
            "judge_identity": dict(ident),
        }
        for s in seg.segments
    }


@pytest.fixture(scope="module")
def long_case(tmp_path_factory):
    """A 30,000-word target (over the 25,000 ceiling), its deterministic
    segmentation, and a keyed manifest with distinct per-segment values."""
    tmp = tmp_path_factory.mktemp("ndlf_long")
    text = _make_text(150)
    target = tmp / "long.txt"
    target.write_text(text, encoding="utf-8")
    seg = nls.segment_text(text)
    assert seg.n_segments >= 3  # the tripwire + coverage tests need >= 3
    manifest = tmp / "keyed.json"
    manifest.write_text(json.dumps(_keyed_manifest(seg)), encoding="utf-8")
    return {
        "tmp": tmp, "text": text, "target": target,
        "seg": seg, "manifest": manifest,
    }


@pytest.fixture(scope="module")
def long_envelope(long_case, tmp_path_factory):
    """One real scoring run; most envelope tests read this."""
    out = tmp_path_factory.mktemp("ndlf_env") / "envelope.json"
    rc = ndlf.main([
        str(long_case["target"]),
        "--judge", "manifest",
        "--judge-manifest", str(long_case["manifest"]),
        "--out", str(out),
    ])
    assert rc == 0
    return json.loads(out.read_text(encoding="utf-8"))


def _run_expect_refusal(argv: list[str], out: Path) -> tuple[int, dict]:
    rc = ndlf.main(argv + ["--out", str(out)])
    envelope = json.loads(out.read_text(encoding="utf-8"))
    assert envelope["available"] is False
    assert envelope["reason_category"] in REASON_CATEGORIES
    return rc, envelope


# ---------- S1: signal-id pins ---------------------------------------

def test_signal_ids_33_unique_19_14_split():
    ids = ndlf.all_signal_ids()
    assert len(ids) == 33
    assert len(set(ids)) == 33
    pairs = [(f, s) for f in nfs.CORE_FEATURES for s in f.signals]
    no_suffix = [
        ndlf.signal_id_for(f, s) for f, s in pairs if s.option is None
    ]
    with_suffix = [
        ndlf.signal_id_for(f, s) for f, s in pairs if s.option is not None
    ]
    assert len(no_suffix) == 19
    assert len(with_suffix) == 14
    for f, s in pairs:
        if s.option is None:
            assert ndlf.signal_id_for(f, s) == (
                f"narrative.{s.bundle}.{f.key}"
            )
        else:
            assert ndlf.signal_id_for(f, s) == (
                f"narrative.{s.bundle}.{f.key}.{s.option}"
            )


def test_capability_matches_runtime_register_and_output_suffix():
    manifest = (
        ROOT.parent / "capabilities.d" / "narrative_decision_long_form.yaml"
    ).read_text(encoding="utf-8")
    assert "\n    registers:\n      - long_form_fiction\n" in manifest
    assert "\n      artifacts:\n        - .narrative_long_form.json\n" in manifest


def test_eight_single_leaning_option_bearing_ids_retain_suffix():
    """The v3-defeating pin: these eight single-leaning option-bearing
    signals must keep their option suffix."""
    ids = set(ndlf.all_signal_ids())
    eight = {
        "narrative.thematic_over_determination."
        "narratorial_thematic_commentary.yes",
        "narrative.thematic_over_determination."
        "dialogue_function.philosophical_debate",
        "narrative.sensory_embodied_performativity."
        "dominant_sensory_modalities.olfactory",
        "narrative.structural_streamlining."
        "agency_in_resolution.protagonist_choice",
        "narrative.structural_streamlining."
        "character_introduction.external_description",
        "narrative.structural_streamlining."
        "mode_of_resolution.resolved_internally",
        "narrative.intertextual_richness."
        "intertextual_strategy_types.explicit_named",
        "narrative.narrative_diversity."
        "moral_polarity_toward_protagonist.ambivalent_or_mixed",
    }
    assert eight <= ids


# ---------- operator table -------------------------------------------

_NOT_AGGREGATABLE_PAIRS = {
    ("mode_of_resolution", "resolved_internally"),
    ("agency_in_resolution", "protagonist_choice"),
    ("subplot_integration", "no_subplots"),
    ("subplot_integration", "thematically_parallel"),
    ("anachrony_intensity", None),
    ("degree_of_chronological_discontinuity", None),
    ("nonlinear_framing_for_delayed_disclosure", None),
    ("depth_of_recontextualization_after_surprise", None),
    ("opening_spatial_grounding", None),
    ("character_introduction", "external_description"),
    ("pre_threat_character_investment", None),
    ("location_variety_scope", None),
}


def test_operator_table_disjoint_and_total_over_real_schema():
    real_pairs = {
        (f.key, s.option) for f in nfs.CORE_FEATURES for s in f.signals
    }
    assert set(ndlf.OPERATOR_TABLE.keys()) == real_pairs

    by_class: dict[str, set] = {}
    for pair, op in ndlf.OPERATOR_TABLE.items():
        by_class.setdefault(op, set()).add(pair)
    assert set(by_class) == {
        ndlf.OPERATOR_MEAN,
        ndlf.OPERATOR_PREVALENCE,
        ndlf.OPERATOR_NOT_AGGREGATABLE,
    }
    # 12 + 12 + 9 = 33, disjoint (dict keys) AND total (== real_pairs).
    assert len(by_class[ndlf.OPERATOR_NOT_AGGREGATABLE]) == 12
    assert len(by_class[ndlf.OPERATOR_MEAN]) == 12
    assert len(by_class[ndlf.OPERATOR_PREVALENCE]) == 9
    assert by_class[ndlf.OPERATOR_NOT_AGGREGATABLE] == (
        _NOT_AGGREGATABLE_PAIRS
    )
    # The type rule fills the remainder: every remaining option-bearing
    # signal is prevalence; every remaining option=None signal is mean.
    for (fkey, option), op in ndlf.OPERATOR_TABLE.items():
        if (fkey, option) in _NOT_AGGREGATABLE_PAIRS:
            continue
        expected = (
            ndlf.OPERATOR_PREVALENCE if option is not None
            else ndlf.OPERATOR_MEAN
        )
        assert op == expected, (fkey, option)


# ---------- emit guard (unit) ------------------------------------------

@pytest.mark.parametrize(
    "key", sorted(ndlf.FORBIDDEN_REDUCTION_KEYS)
)
def test_guard_banned_exact_keys(key):
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"nested": {key: None}})


@pytest.mark.parametrize(
    "key", ["my_verdict_band_x", "a_composite_thing", "Verdictish"]
)
def test_guard_banned_substring_keys(key):
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({key: "s"})


def test_guard_floats_forbidden_everywhere():
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"a": 0.5})
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"a": {"b": [1.0]}})
    # even under an n_* key a float is a float
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"n_words": 1.0})


def test_guard_int_allowlist():
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"words": 5})
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction({"support": [3]})
    # allowed: n_* prefix + the exact structural keys
    ndlf.assert_no_work_level_reduction({
        "n_segments": 5,
        "index": 1,
        "segment_target_words": 5000,
        "start": 0,
        "end": 10,
        "coverage": {"n_segments_contributing": 3},
        "flag": True,
        "text": "4",
        "nothing": None,
    })


# ---------- length routing ---------------------------------------------

def test_in_range_target_refuses_bad_input(tmp_path):
    target = tmp_path / "short.txt"
    target.write_text(_make_text(4), encoding="utf-8")  # 800 words
    rc, envelope = _run_expect_refusal(
        [str(target)], tmp_path / "o.json"
    )
    assert rc == 1
    assert envelope["reason_category"] == "bad_input"
    assert "base audit" in envelope["reason"]


# ---------- manifest keying ---------------------------------------------

def test_flat_manifest_on_segmented_run_refused(long_case, tmp_path):
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({
        "values": _full_values(0),
        "judge_identity": dict(_IDENTITY),
    }), encoding="utf-8")
    rc, envelope = _run_expect_refusal(
        [
            str(long_case["target"]),
            "--judge", "manifest",
            "--judge-manifest", str(flat),
        ],
        tmp_path / "o.json",
    )
    assert rc == 1
    assert envelope["reason_category"] == "bad_input"
    assert "flat" in envelope["reason"]


def test_missing_segment_key_refused(long_case, tmp_path):
    keyed = _keyed_manifest(long_case["seg"])
    dropped = long_case["seg"].segments[1].content_sha256
    del keyed[dropped]
    manifest = tmp_path / "missing.json"
    manifest.write_text(json.dumps(keyed), encoding="utf-8")
    rc, envelope = _run_expect_refusal(
        [
            str(long_case["target"]),
            "--judge", "manifest",
            "--judge-manifest", str(manifest),
        ],
        tmp_path / "o.json",
    )
    assert rc == 1
    assert envelope["reason_category"] == "bad_input"
    assert "missing" in envelope["reason"]


def test_manifest_judge_requires_manifest_flag(long_case):
    # Mirrors the base audit: judge-construction failure routes through
    # argparse (usage line, exit 2).
    with pytest.raises(SystemExit) as excinfo:
        ndlf.main([str(long_case["target"])])
    assert excinfo.value.code == 2


# ---------- judge-provenance refusals -----------------------------------

def test_mock_judge_refused_on_scoring_run(long_case, tmp_path):
    rc, envelope = _run_expect_refusal(
        [str(long_case["target"]), "--judge", "mock"],
        tmp_path / "o.json",
    )
    assert rc == 2
    assert envelope["reason_category"] == "policy_refused"


def test_degenerate_judge_tripwire(long_case, tmp_path):
    """One flat value-set reused across >= 3 segments (what a text-blind
    judge produces) refuses the run."""
    manifest = tmp_path / "degenerate.json"
    manifest.write_text(
        json.dumps(_keyed_manifest(long_case["seg"], same_values=True)),
        encoding="utf-8",
    )
    rc, envelope = _run_expect_refusal(
        [
            str(long_case["target"]),
            "--judge", "manifest",
            "--judge-manifest", str(manifest),
        ],
        tmp_path / "o.json",
    )
    assert rc == 2
    assert envelope["reason_category"] == "policy_refused"
    assert "degenerate" in envelope["reason"]


# ---------- the M1 envelope ----------------------------------------------

def test_envelope_top_level(long_envelope, long_case):
    assert long_envelope["available"] is True
    assert long_envelope["task_surface"] == "narrative_decision_long_form"
    assert long_envelope["tool"] == "narrative_decision_long_form"
    assert long_envelope["target"]["words"] > ndlf.CEILING_WORDS
    results = long_envelope["results"]
    assert set(results.keys()) == {
        "segmentation", "per_segment", "per_signal_aggregates",
        "per_bundle", "validation_binding", "judge", "cache",
    }
    assert results["judge"] == {
        "kind": "manifest",
        "model": "test-judge",
        "model_revision": "r1",
        "prompt_version": "pv1",
    }
    seg_block = results["segmentation"]
    assert seg_block["n_segments"] == long_case["seg"].n_segments


def test_per_segment_raw_responses(long_envelope, long_case):
    per_segment = long_envelope["results"]["per_segment"]
    seg = long_case["seg"]
    assert len(per_segment) == seg.n_segments
    for i, block in enumerate(per_segment):
        assert set(block.keys()) == {
            "index", "content_sha256", "signals", "register_warnings",
            "validation_warnings", "reduction_licensed",
        }
        # Spec-mandated, and absent from the first build: the mechanical
        # residue against the reconstruction limit rides on every block.
        assert block["reduction_licensed"] is False
        assert all(
            isinstance(w, str) for w in block["validation_warnings"]
        )
        assert block["index"] == i
        assert block["content_sha256"] == seg.segments[i].content_sha256
        assert set(block["signals"].keys()) == set(ndlf.all_signal_ids())
        for cell in block["signals"].values():
            assert set(cell.keys()) == {"response", "available"}
            response = cell["response"]
            # responses stay STRINGS (or lists of strings for multi
            # features) exactly as the judge returned them — never
            # numerics.
            assert (
                response is None
                or isinstance(response, str)
                or (
                    isinstance(response, list)
                    and all(isinstance(x, str) for x in response)
                )
            )
            assert isinstance(cell["available"], bool)
        assert all(
            isinstance(w, str) for w in block["register_warnings"]
        )


def test_per_signal_aggregates_all_suppressed(long_envelope):
    aggregates = long_envelope["results"]["per_signal_aggregates"]
    assert set(aggregates.keys()) == set(ndlf.all_signal_ids())
    statuses = Counter(a["status"] for a in aggregates.values())
    assert statuses == Counter({
        "provisional_unvalidated": 21,
        "not_aggregatable": 12,
    })
    n_total = long_envelope["results"]["segmentation"]["n_segments"]
    for sid, agg in aggregates.items():
        assert agg["value"] is None, sid  # null, never 0.0, never omitted
        assert agg["operator"] in {
            ndlf.OPERATOR_MEAN,
            ndlf.OPERATOR_PREVALENCE,
            ndlf.OPERATOR_NOT_AGGREGATABLE,
        }
        coverage = agg["coverage"]
        assert coverage["n_segments_total"] == n_total
        # complete manifest values -> every segment contributes
        assert coverage["n_segments_contributing"] == n_total


def test_per_bundle_class_rollups_null(long_envelope):
    per_bundle = long_envelope["results"]["per_bundle"]
    assert set(per_bundle.keys()) == set(nfs.BUNDLE_LABELS)
    for bundle, block in per_bundle.items():
        for cls in ("mean_class", "prevalence_class"):
            assert block[cls]["value"] is None, bundle
            assert block[cls]["dispersion"] is None, bundle
            assert block[cls]["n_validated"] == 0, bundle
        assert block["mean_class"]["units"] == "response_units"
        assert block["prevalence_class"]["units"] == "prevalence"
        assert block["basis"] == "longform_validated_subset"
    # measured composition consequences (from the real schema):
    temporal = per_bundle["temporal_complexity"]
    assert temporal["mean_class"]["n_signals"] == 0
    assert temporal["prevalence_class"]["n_signals"] == 0
    assert len(temporal["excluded_signal_ids"]) == 4
    streamlining = per_bundle["structural_streamlining"]
    assert streamlining["mean_class"]["n_signals"] == 2
    assert streamlining["prevalence_class"]["n_signals"] == 0
    assert len(streamlining["excluded_signal_ids"]) == 6


def test_validation_binding_receipt_absent(long_envelope):
    """Spec 79: every S3 required-match field is present and `absent`.

    The first build emitted `match: {}`, which is indistinguishable from a
    match object that silently lost a field.
    """
    binding = long_envelope["results"]["validation_binding"]
    assert binding["receipt_present"] is False
    assert binding["receipt_path"] is None
    assert binding["receipt_sha256"] is None
    assert binding["licensed"] is False
    assert binding["suppression_reason"] == "provisional_unvalidated"
    assert binding["match"] == {
        "signal_id_set_sha256": "absent",
        "segmenter.version": "absent",
        "segmenter.params_sha256": "absent",
        "segmenter.segment_target_words": "absent",
        "judge.kind": "absent",
        "judge.model": "absent",
        "judge.model_revision": "absent",
        "judge.prompt_version": "absent",
        "validated_segment_count_range": "absent",
        "validated_segment_words": "absent",
    }
    assert set(binding["match"]) == set(ndlf.REQUIRED_MATCH_FIELDS)


def test_claim_license_demotion(long_envelope):
    license_block = long_envelope["claim_license"]
    assert license_block["task_surface"] == "narrative_decision_long_form"
    does_not = license_block["does_not_license"]
    assert "work-level aggregation" in does_not
    assert "provenance" in does_not
    assert "signal_target_value" in does_not


def test_license_describes_the_cleaning_it_actually_performs(long_envelope):
    """Codex P2: the license said "exactly as returned"; the code emits
    `validate_values` output, which DROPS out-of-vocabulary multi-select
    options and NULLS out-of-vocabulary scalars."""
    licenses = long_envelope["claim_license"]["licenses"]
    assert "exactly as returned" not in licenses
    for phrase in ("validate_values", "DROPPED", "null", "validation_warnings"):
        assert phrase in licenses, phrase
    refs = long_envelope["claim_license"]["references"]
    assert any("specs/79-storyscope-long-form-extension.md" in r for r in refs)
    assert not any("77-storyscope" in r for r in refs)
    comparison = long_envelope["claim_license"]["comparison_set"]
    assert "spec 79" in comparison["literature_anchor"]


def test_cleaning_is_visible_in_validation_warnings(long_case, tmp_path):
    """The emission stays truthful because the drop is recorded.

    A multi-select carrying one legal and one bogus option emits only the
    legal one — the exact case Codex named — and the segment's
    `validation_warnings` says so verbatim.
    """
    multi = next(
        f for f in nfs.CORE_FEATURES if f.feature_type == "multi"
    )
    keyed = _keyed_manifest(long_case["seg"])
    first = long_case["seg"].segments[0].content_sha256
    keyed[first]["values"][multi.key] = [
        multi.response_options[0], "bogus_option"
    ]
    manifest = tmp_path / "dirty.json"
    manifest.write_text(json.dumps(keyed), encoding="utf-8")
    out = tmp_path / "o.json"
    assert ndlf.main([
        str(long_case["target"]), "--judge", "manifest",
        "--judge-manifest", str(manifest), "--out", str(out),
    ]) == 0
    block = json.loads(out.read_text(encoding="utf-8"))["results"][
        "per_segment"
    ][0]
    sid = ndlf.signal_id_for(multi, multi.signals[0])
    assert block["signals"][sid]["response"] == [multi.response_options[0]]
    assert any(
        "bogus_option" in w and multi.key in w
        for w in block["validation_warnings"]
    )


# ---------- judge-identity typing (closed refusals) ---------------------

@pytest.mark.parametrize("bad", [[], 7, {"nested": 1}, True, 1.5])
def test_manifest_judge_identity_exact_types_refuse(
    long_case, tmp_path, bad
):
    """Codex P3: `model: []` raised an uncaught TypeError building the
    identity set, and `model: 7` escaped as a WorkLevelReductionError from
    the emit guard. Both are bad input and must refuse as such."""
    keyed = _keyed_manifest(long_case["seg"])
    for entry in keyed.values():
        entry["judge_identity"]["model"] = bad
    manifest = tmp_path / f"badid.json"
    manifest.write_text(json.dumps(keyed), encoding="utf-8")
    rc, envelope = _run_expect_refusal(
        [
            str(long_case["target"]), "--judge", "manifest",
            "--judge-manifest", str(manifest),
        ],
        tmp_path / "o.json",
    )
    assert rc == 1
    assert envelope["reason_category"] == "bad_input"
    assert "judge_identity.model" in envelope["reason"]


def test_manifest_judge_identity_non_object_refuses(long_case, tmp_path):
    keyed = _keyed_manifest(long_case["seg"])
    for entry in keyed.values():
        entry["judge_identity"] = ["not", "an", "object"]
    manifest = tmp_path / "badid2.json"
    manifest.write_text(json.dumps(keyed), encoding="utf-8")
    rc, envelope = _run_expect_refusal(
        [
            str(long_case["target"]), "--judge", "manifest",
            "--judge-manifest", str(manifest),
        ],
        tmp_path / "o.json",
    )
    assert rc == 1
    assert "judge_identity" in envelope["reason"]


def test_no_forbidden_reduction_fields_anywhere(long_envelope):
    """The base audit's reductive vocabulary must not appear as keys in
    the long-form results."""
    def walk_keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield str(k)
                yield from walk_keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from walk_keys(item)

    keys = set(walk_keys(long_envelope["results"]))
    for banned in (
        "score", "aggregate", "verdict_band", "contribution",
        "target_value", "mean_contribution", "per_feature_confidence",
        "values", "raw_response",
    ):
        assert banned not in keys, banned


# ---------- the mandated guard pair ---------------------------------------

def test_guard_passes_the_real_mandated_envelope(long_envelope):
    """The check the spec's drafts kept mandating without running: the
    emit guard PASSES on the real emitted results dict."""
    ndlf.assert_no_work_level_reduction(long_envelope["results"])


@pytest.mark.parametrize("inject", [
    lambda r: r["per_signal_aggregates"].__getitem__(
        sorted(r["per_signal_aggregates"])[0]
    ).__setitem__("value", 0.5),
    lambda r: r["per_segment"][0]["signals"].__getitem__(
        sorted(r["per_segment"][0]["signals"])[0]
    ).__setitem__("response", 3.7),
    lambda r: r["segmentation"]["segments"][0].__setitem__(
        "n_words", 1.0
    ),
    lambda r: r["validation_binding"]["match"].__setitem__(
        "judge.kind", 0.0
    ),
    lambda r: r["per_bundle"]["reader_engagement"]["mean_class"]
    .__setitem__("value", 0.12),
])
def test_guard_rejects_injected_float_anywhere(long_envelope, inject):
    results = copy.deepcopy(long_envelope["results"])
    inject(results)
    with pytest.raises(ndlf.WorkLevelReductionError):
        ndlf.assert_no_work_level_reduction(results)


# ---------- calibration mode ------------------------------------------

def test_calibration_mock_allowed_any_length(tmp_path):
    """--calibration-emit-segments takes an IN-RANGE work and the mock
    judge; output is segmentation + stamped per-segment blocks only."""
    target = tmp_path / "short.txt"
    target.write_text(_make_text(4), encoding="utf-8")  # 800 words
    out = tmp_path / "cal.json"
    rc = ndlf.main([
        str(target), "--judge", "mock",
        "--calibration-emit-segments", "--out", str(out),
    ])
    assert rc == 0
    envelope = json.loads(out.read_text(encoding="utf-8"))
    results = envelope["results"]
    assert results["calibration_only"] is True
    assert results["judge_kind"] == "mock"  # prominent, mandated
    assert results["judge"]["kind"] == "mock"
    assert results["per_signal_aggregates"] == {}
    assert "per_bundle" not in results
    assert "validation_binding" not in results
    assert len(results["per_segment"]) >= 1
    for block in results["per_segment"]:
        assert block["calibration_only"] is True
    does_not = envelope["claim_license"]["does_not_license"]
    assert "evidentiary" in does_not
    # the guard runs on calibration emissions too
    ndlf.assert_no_work_level_reduction(results)


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_output_cannot_alias_the_source_text(tmp_path, alias_kind):
    target = tmp_path / "source.txt"
    original = _make_text(4).encode("utf-8")
    target.write_bytes(original)
    out = target
    if alias_kind == "symlink":
        out = tmp_path / "source-link.txt"
        out.symlink_to(target)
    elif alias_kind == "hardlink":
        out = tmp_path / "source-hardlink.txt"
        out.hardlink_to(target)
    rc = ndlf.main([
        str(target), "--judge", "mock",
        "--calibration-emit-segments", "--out", str(out),
    ])
    assert rc == 1
    assert target.read_bytes() == original


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_output_cannot_alias_the_judge_manifest(
    long_case, tmp_path, alias_kind
):
    manifest = tmp_path / "judge.json"
    original = long_case["manifest"].read_bytes()
    manifest.write_bytes(original)
    out = manifest
    if alias_kind == "symlink":
        out = tmp_path / "judge-link.json"
        out.symlink_to(manifest)
    elif alias_kind == "hardlink":
        out = tmp_path / "judge-hardlink.json"
        out.hardlink_to(manifest)
    rc = ndlf.main([
        str(long_case["target"]), "--judge", "manifest",
        "--judge-manifest", str(manifest), "--out", str(out),
    ])
    assert rc == 1
    assert manifest.read_bytes() == original


def test_default_output_cannot_alias_the_judge_manifest(long_case):
    out = long_case["target"].with_suffix(
        long_case["target"].suffix + ".narrative_long_form.json"
    )
    original = long_case["manifest"].read_bytes()
    out.write_bytes(original)
    rc = ndlf.main([
        str(long_case["target"]), "--judge", "manifest",
        "--judge-manifest", str(out),
    ])
    assert rc == 1
    assert out.read_bytes() == original


def test_calibration_mock_long_text_exempt_from_tripwire(
    long_case, tmp_path
):
    """The deterministic mock judge yields identical vectors on every
    segment; calibration-only runs must still emit (they are
    mechanically ineligible for claims), so the tripwire is scoring-only."""
    out = tmp_path / "cal_long.json"
    rc = ndlf.main([
        str(long_case["target"]), "--judge", "mock",
        "--calibration-emit-segments", "--out", str(out),
    ])
    assert rc == 0
    envelope = json.loads(out.read_text(encoding="utf-8"))
    assert (
        len(envelope["results"]["per_segment"])
        == long_case["seg"].n_segments
    )


# ---------- resume cache ------------------------------------------------

def test_cache_same_judge_hits_changed_prompt_version_misses(
    long_case, tmp_path
):
    cache_dir = tmp_path / "cache"
    n = long_case["seg"].n_segments

    def run(manifest_path: Path, out_name: str) -> dict:
        out = tmp_path / out_name
        rc = ndlf.main([
            str(long_case["target"]),
            "--judge", "manifest",
            "--judge-manifest", str(manifest_path),
            "--cache-dir", str(cache_dir),
            "--out", str(out),
        ])
        assert rc == 0
        return json.loads(out.read_text(encoding="utf-8"))

    # run 1: cold cache
    env1 = run(long_case["manifest"], "o1.json")
    assert env1["results"]["cache"] == {
        "enabled": True, "n_hits": 0, "n_misses": n,
    }
    # run 2: same judge identity -> full hit
    env2 = run(long_case["manifest"], "o2.json")
    assert env2["results"]["cache"] == {
        "enabled": True, "n_hits": n, "n_misses": 0,
    }
    # run 3: changed prompt_version -> content hash alone must NOT hit
    changed_identity = dict(_IDENTITY, prompt_version="pv2")
    changed = tmp_path / "keyed_pv2.json"
    changed.write_text(
        json.dumps(
            _keyed_manifest(long_case["seg"], identity=changed_identity)
        ),
        encoding="utf-8",
    )
    env3 = run(changed, "o3.json")
    assert env3["results"]["cache"] == {
        "enabled": True, "n_hits": 0, "n_misses": n,
    }
    # and the per-segment payloads are identical across hit/miss runs
    assert (
        env1["results"]["per_segment"] == env2["results"]["per_segment"]
    )


# ---------- cache tamper / staleness (Codex P2) -------------------------

def _warm_cache(long_case, tmp_path, manifest_path=None) -> Path:
    cache_dir = tmp_path / "cache"
    rc = ndlf.main([
        str(long_case["target"]), "--judge", "manifest",
        "--judge-manifest", str(manifest_path or long_case["manifest"]),
        "--cache-dir", str(cache_dir), "--out", str(tmp_path / "warm.json"),
    ])
    assert rc == 0
    assert list(cache_dir.glob("*.json"))
    return cache_dir


def _rerun_with_cache(long_case, tmp_path, cache_dir, manifest_path=None):
    return _run_expect_refusal(
        [
            str(long_case["target"]), "--judge", "manifest",
            "--judge-manifest", str(manifest_path or long_case["manifest"]),
            "--cache-dir", str(cache_dir),
        ],
        tmp_path / "o.json",
    )


def test_edited_cache_signals_refuse_rather_than_emit(long_case, tmp_path):
    """Codex P2: `_cache_load` accepted any dict containing `signals` and
    emitted it without re-running judge validation. Forge a response in the
    cache file and the run emitted the forgery."""
    cache_dir = _warm_cache(long_case, tmp_path)
    entry = sorted(cache_dir.glob("*.json"))[0]
    payload = json.loads(entry.read_text(encoding="utf-8"))
    sid = sorted(payload["signals"])[0]
    payload["signals"][sid]["response"] = "not_an_option_in_any_feature"
    entry.write_text(json.dumps(payload), encoding="utf-8")

    rc, envelope = _rerun_with_cache(long_case, tmp_path, cache_dir)
    assert rc == 1
    assert envelope["reason_category"] == "bad_input"
    assert "cache entry" in envelope["reason"]


def test_edited_cache_values_vector_refuses(long_case, tmp_path):
    """The forged vector is what drives the degenerate-judge tripwire, so
    it must be a function of the emitted signals, not a free field."""
    cache_dir = _warm_cache(long_case, tmp_path)
    entry = sorted(cache_dir.glob("*.json"))[0]
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["values_vector"] = '{"forged":"vector"}'
    entry.write_text(json.dumps(payload), encoding="utf-8")

    rc, envelope = _rerun_with_cache(long_case, tmp_path, cache_dir)
    assert rc == 1
    assert "values_vector" in envelope["reason"]


def test_schema_valid_cache_forgery_refuses(long_case, tmp_path):
    """A legal response plus its recomputed vector is still not live judge
    output and must fail the manifest comparison."""
    cache_dir = _warm_cache(long_case, tmp_path)
    entry = sorted(cache_dir.glob("*.json"))[0]
    payload = json.loads(entry.read_text(encoding="utf-8"))
    feature = next(f for f in nfs.CORE_FEATURES if f.feature_type == "scale")
    signal_ids = [
        ndlf.signal_id_for(feature, signal)
        for signal in feature.signals
    ]
    old = payload["signals"][signal_ids[0]]["response"]
    new = next(option for option in feature.response_options if option != old)
    for signal_id in signal_ids:
        payload["signals"][signal_id]["response"] = new
    payload["values_vector"] = ndlf._values_vector(
        ndlf._cleaned_from_signals(payload["signals"])
    )
    entry.write_text(json.dumps(payload), encoding="utf-8")

    rc, envelope = _rerun_with_cache(long_case, tmp_path, cache_dir)
    assert rc == 1
    assert "live manifest" in envelope["reason"]


def test_cache_entry_moved_to_another_key_refuses(long_case, tmp_path):
    """A cache file is not authoritative because of its filename: the
    binding it records must equal the live one."""
    cache_dir = _warm_cache(long_case, tmp_path)
    entries = sorted(cache_dir.glob("*.json"))
    assert len(entries) >= 2
    entries[1].write_bytes(entries[0].read_bytes())  # segment 0 under key 1

    rc, envelope = _rerun_with_cache(long_case, tmp_path, cache_dir)
    assert rc == 1
    assert "binding" in envelope["reason"]


def test_changed_manifest_response_misses_instead_of_serving_stale(
    long_case, tmp_path
):
    """Codex P2: change a manifest response while keeping segment content
    and declared identity, and the old answers were served from cache."""
    cache_dir = _warm_cache(long_case, tmp_path)
    keyed = _keyed_manifest(long_case["seg"])
    first = long_case["seg"].segments[0].content_sha256
    scale = next(
        f for f in nfs.CORE_FEATURES if f.feature_type == "scale"
    )
    old = keyed[first]["values"][scale.key]
    new = next(o for o in scale.response_options if o != old)
    keyed[first]["values"][scale.key] = new
    edited = tmp_path / "edited.json"
    edited.write_text(json.dumps(keyed), encoding="utf-8")

    out = tmp_path / "after.json"
    assert ndlf.main([
        str(long_case["target"]), "--judge", "manifest",
        "--judge-manifest", str(edited), "--cache-dir", str(cache_dir),
        "--out", str(out),
    ]) == 0
    results = json.loads(out.read_text(encoding="utf-8"))["results"]
    # segment 0 recomputed; the untouched segments still hit.
    assert results["cache"]["n_misses"] == 1
    assert results["cache"]["n_hits"] == long_case["seg"].n_segments - 1
    sid = ndlf.signal_id_for(scale, scale.signals[0])
    assert results["per_segment"][0]["signals"][sid]["response"] == new


def test_unreadable_cache_entry_refuses(long_case, tmp_path):
    cache_dir = _warm_cache(long_case, tmp_path)
    sorted(cache_dir.glob("*.json"))[0].write_text("{ not json",
                                                   encoding="utf-8")
    rc, envelope = _rerun_with_cache(long_case, tmp_path, cache_dir)
    assert rc == 1
    assert "invalid JSON" in envelope["reason"]


def test_cache_disabled_reports_disabled(long_envelope):
    assert long_envelope["results"]["cache"] == {
        "enabled": False, "n_hits": 0,
        "n_misses": long_envelope["results"]["segmentation"]["n_segments"],
    }
