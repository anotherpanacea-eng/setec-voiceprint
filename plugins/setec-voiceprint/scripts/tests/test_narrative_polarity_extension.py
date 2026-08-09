"""Judge/model-free pins for the spec-78 calibration consumer."""
from __future__ import annotations

import sys
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB = ROOT / "calibration"
for path in (str(ROOT), str(CALIB)):
    if path not in sys.path:
        sys.path.insert(0, path)

import narrative_polarity_extension as npe  # type: ignore  # noqa: E402
from storyscope_polarity_contract import FRAME_DOMAINS, framed_file_digest, source_work_sha256  # type: ignore  # noqa: E402


_DATE = "2026-07-27"
_CTX = 40_000
_SEGMENTER = {
    "emitter": "narrative_decision_long_form", "segmenter_version": npe.SEGMENTER_VERSION,
    "params_sha256": "sha256:" + "a" * 64, "segment_target_words": 5000,
}
_FLOORS = {
    "min_source_works": 24, "min_authors": 8, "min_generator_families": 2,
    "min_signal_support": 24, "min_class_n": 20, "class_n_margin": 4,
    "min_availability_rate": 0.9, "min_segment_count_by_work": 3, "min_bridge_works": 8,
    "min_source_work_words": npe.CEILING_WORDS + 1, "max_share_single_work": 0.15,
    "length_overlap_min": 0.8, "length_bins": 4, "fragment_shift_ceiling": 0.5,
    "subfloor_shift_ceiling": 0.5, "effect_threshold_numeric": 0.2, "pre_ai_cutoff_year": 2020,
}
_BANDS = {
    "segment_regime": {"primary": {"min_words": npe.FLOOR_WORDS, "max_words": npe.CEILING_WORDS},
                       "bridge": {"min_words": npe.CEILING_WORDS + 1, "max_words": _CTX}},
    "subfloor": {"primary": {"min_words": 200, "max_words": npe.FLOOR_WORDS - 1},
                 "bridge_full": {"min_words": npe.FLOOR_WORDS, "max_words": npe.CEILING_WORDS},
                 "bridge_truncated": {"min_words": 200, "max_words": npe.FLOOR_WORDS - 1}},
}


def _sha(text):
    import hashlib
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _signal_cells(seed):
    """Legal, non-degenerate responses; every vector distinct across the corpus."""
    out = {}
    for i, sid in enumerate(npe.SIGNAL_IDS):
        spec = npe.SIGNAL_SPECS[sid]
        options = list(spec.response_options)
        value = options[(seed + i) % len(options)]
        out[sid] = {"value": [value] if spec.feature_type == "multi" else value, "available": True}
    return out


def _runtime_receipt(tmp_path, monkeypatch):
    """Emit a receipt the way the CLI does, over a synthetic Arm-A manifest.

    Custody re-opening wants real producer envelopes on disk; it is stubbed
    here because what is under test is the *emitted receipt*, not custody.
    """
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text(json.dumps(
        {"schema": "narrative-polarity-thresholds/1", "floors": _FLOORS, "bands": _BANDS}), encoding="utf-8")

    seeds, rows = iter(range(10_000)), []
    for label in ("pre_ai_human", "ai_generated"):
        for w in range(_FLOORS["min_source_works"]):
            wid = f"{label}-w{w}"
            work_words, work_sha = 30_000, _sha(wid)
            provenance = ({"class": "human", "author_id": f"a{w % 8}", "publication_year": 1990,
                           "source_corpus_id": "c"} if label == "pre_ai_human" else
                          {"class": "ai", "generator_family": f"g{w % 2}", "model": "mm",
                           "model_revision": "rr", "prompt_family": "fam", "generated_date": "2026-01-01"})
            judge = {"kind": "manifest", "model": "m1", "model_revision": "r1", "prompt_version": "p1"}
            for k in range(_FLOORS["min_segment_count_by_work"]):
                rows.append({
                    "text_id": f"{wid}-s{k}", "label": label, "role": "primary", "source_kind": "segment",
                    "source_work_id": wid, "source_work_words": work_words, "source_work_sha256": work_sha,
                    "n_words": 5000 + k * 100, "content_sha256": _sha(f"{wid}-s{k}"),
                    "subfloor_bridge_side": None, "read_mode": None,
                    "segmenter": dict(_SEGMENTER, tier="paragraph", segment_index=k, n_segments_in_work=3),
                    "provenance": dict(provenance, claim_license_amendment="CLA-79-A1", register_extension=None),
                    "judge": dict(judge, source_envelope_sha256=_sha(f"env-{wid}-s{k}"),
                                  source_envelope_path=f"env-{wid}-s{k}.json"),
                    "signals": _signal_cells(next(seeds)),
                })
            if w < _FLOORS["min_bridge_works"]:
                rows.append({
                    "text_id": f"{wid}-bridge", "label": label, "role": "bridge", "source_kind": "whole_work",
                    "source_work_id": wid, "source_work_words": work_words, "source_work_sha256": work_sha,
                    "n_words": work_words, "content_sha256": _sha(f"{wid}-whole"),
                    "subfloor_bridge_side": None, "read_mode": "single_pass_whole_text", "segmenter": None,
                    "provenance": dict(provenance, claim_license_amendment="CLA-79-A2",
                                       register_extension="REG-AUDIT-B1"),
                    "judge": dict(judge, source_envelope_sha256=_sha(f"env-{wid}-b"),
                                  source_envelope_path=f"env-{wid}-b.json"),
                    "signals": _signal_cells(next(seeds)),
                })

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    design = tmp_path / "design.jsonl"
    design.write_text("".join(
        json.dumps({k: r[k] for k in npe.DESIGN_KEYS}) + "\n" for r in rows), encoding="utf-8")
    prompt = tmp_path / "fam.txt"
    prompt.write_text("Write a long story about a lighthouse keeper.\n", encoding="utf-8")

    registration = tmp_path / "registration.json"
    registration.write_bytes(npe.canonical_json(npe.build_registration(
        arm="segment_regime", manifest=design, thresholds=thresholds, date=_DATE, segmenter=_SEGMENTER,
        judge={"kind": "manifest", "model": "m1", "model_revision": "r1", "prompt_version": "p1",
               "context_bound_words": _CTX},
        prompts=[{"prompt_family": "fam", "prompt_text_path": prompt.name,
                  "prompt_sha256": framed_file_digest("setec.voiceprint.spec78.prompt-file.v1", prompt)}],
    )) + b"\n")

    monkeypatch.setattr(npe, "_reopen_envelopes", lambda rows: None)
    monkeypatch.setattr(npe, "_validate_truncations", lambda rows, arm: None)
    return npe.evaluate(arm="segment_regime", manifest=manifest, thresholds=thresholds,
                        registration=registration, date=_DATE)


def test_real_receipt_passes_the_guard(tmp_path, monkeypatch):
    """Spec 78 Rule 3 mandates this gate against the runtime receipt.

    Without it the guard can reject its own well-formed output and the whole
    --evaluate path dies on first real use.
    """
    npe.assert_no_per_text_disclosure(_runtime_receipt(tmp_path, monkeypatch))


def test_real_receipt_is_json_emittable(tmp_path, monkeypatch):
    assert npe.canonical_json(_runtime_receipt(tmp_path, monkeypatch))


@pytest.mark.parametrize("path", [
    ("covered_length_range", "mean_words"),
    ("per_signal", npe.SIGNAL_IDS[0], "bridge", "smuggled"),
    ("per_signal", npe.SIGNAL_IDS[0], "midpoint"),
    ("class_counts", "pre_ai_human.primary", "mean_share"),
])
def test_injected_float_at_unlisted_path_raises(tmp_path, monkeypatch, path):
    receipt = _runtime_receipt(tmp_path, monkeypatch)
    node = receipt
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = 0.5
    with pytest.raises(ValueError):
        npe.assert_no_per_text_disclosure(receipt)


def test_no_per_text_key_survives_the_receipt(tmp_path, monkeypatch):
    receipt = _runtime_receipt(tmp_path, monkeypatch)
    receipt["per_signal"][npe.SIGNAL_IDS[0]]["source_work_id"] = "w0"
    with pytest.raises(ValueError):
        npe.assert_no_per_text_disclosure(receipt)


def test_no_work_level_value_appears_in_receipt(tmp_path, monkeypatch):
    """The internal per-work reduction CLA-79-A1 permits must be computable
    but never claimable: no receipt map may be keyed by a source work id."""
    receipt = _runtime_receipt(tmp_path, monkeypatch)
    work_ids = {f"{label}-w{w}" for label in ("pre_ai_human", "ai_generated") for w in range(24)}

    def walk(node):
        if isinstance(node, dict):
            assert not (set(node) & work_ids), "receipt exposes a per-work subtree"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(receipt)


def test_response_class_disjoint_and_total():
    numeric = {k for k, v in npe.RESPONSE_CLASS_BY_SIGNAL_ID.items() if v == "numeric"}
    indicator = {k for k, v in npe.RESPONSE_CLASS_BY_SIGNAL_ID.items() if v == "indicator"}
    assert numeric.isdisjoint(indicator)
    assert numeric | indicator == set(npe.SIGNAL_IDS)
    assert (len(numeric), len(indicator)) == (19, 14)


def test_framed_hashes_use_the_shared_registry_and_domains():
    payload = b"same bytes"
    domains = sorted(FRAME_DOMAINS)
    assert len(domains) == 12
    assert npe.framed_sha256(domains[0], payload) != npe.framed_sha256(domains[1], payload)


def test_numeric_hedges_zero_pooled_sd_is_indeterminate():
    assert npe.hedges_g([1.0, 1.0], [2.0, 2.0], "ai") is None


def test_verdict_precedence_control_blocks_interval_verdict():
    verdict, step = npe.derive_polarity_verdict(
        arm="segment_regime", availability=(1.0, 1.0), availability_floor=.9,
        support=(24, 24), min_support=24, bridge_support=(8, 8), min_bridge=8,
        response_class="numeric", min_class_n=20,
        bridge=((.01, .30), (.01, .02)), ceiling=.1, degenerate=False,
        interval=(.5, .9), threshold=.2,
    )
    assert (verdict, step) == ("bridge_inconclusive", 3)


def test_crossed_bridge_point_and_ucb_is_inconclusive_not_artifact():
    verdict, step = npe.derive_polarity_verdict(
        arm="segment_regime", availability=(1.0, 1.0), availability_floor=.9,
        support=(24, 24), min_support=24, bridge_support=(8, 8), min_bridge=8,
        response_class="numeric", min_class_n=20,
        bridge=((.09, .11), (.01, .02)), ceiling=.1, degenerate=False,
        interval=(.5, .9), threshold=.2,
    )
    assert (verdict, step) == ("bridge_inconclusive", 3)


def test_quantile_overlap_handles_ties_and_more_bins_than_rows():
    rows = [
        {"label": "pre_ai_human", "n_words": 10},
        {"label": "pre_ai_human", "n_words": 10},
        {"label": "ai_generated", "n_words": 10},
        {"label": "ai_generated", "n_words": 10},
    ]
    assert npe._length_overlap(rows, 10) == 1.0
    rows[-1]["n_words"] = 20
    assert npe._length_overlap(rows, 10) == 0.5


def test_eight_field_design_substitution_changes_digest():
    base = {
        "text_id": "t", "label": "pre_ai_human", "role": "primary",
        "source_kind": "segment", "source_work_id": "w",
        "subfloor_bridge_side": None, "content_sha256": "sha256:" + "0" * 64,
        "source_work_sha256": "sha256:" + "1" * 64,
    }
    changed = dict(base, source_work_id="other")
    digest = lambda rows: npe.framed_sha256(
        "setec.voiceprint.spec78.design-projection-json.v1",
        npe.canonical_json(npe._design(rows)),
    )
    assert digest([base]) != digest([changed])


def test_verify_rejects_tampered_rederived_receipt(tmp_path, monkeypatch):
    expected = {"derivation_sha256": "sha256:good", "verdict": "polarity_matches"}
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(dict(expected, verdict="polarity_inverted")), encoding="utf-8")
    monkeypatch.setattr(npe, "evaluate", lambda **_: expected)
    with pytest.raises(npe.CalibrationRefusal):
        npe.verify_receipt(receipt=receipt, arm="subfloor", manifest=tmp_path / "m", thresholds=tmp_path / "t", registration=tmp_path / "r", date="2026-07-27")


def test_arm_b_unrelated_truncation_refuses(tmp_path):
    full = tmp_path / "full.txt"
    short = tmp_path / "short.txt"
    full.write_text("one two three four", encoding="utf-8")
    short.write_text("one three", encoding="utf-8")
    def envelope(path: Path, name: str) -> Path:
        out = tmp_path / name
        out.write_text(json.dumps({"target": {"path": str(path)}}), encoding="utf-8")
        return out
    full_env, short_env = envelope(full, "full.json"), envelope(short, "short.json")
    row = lambda side, env, n: {"label": "pre_ai_human", "source_work_id": "w", "role": "bridge", "subfloor_bridge_side": side, "judge": {"source_envelope_path": str(env)}, "n_words": n}
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._validate_truncations([row("full", full_env, 4), row("truncated", short_env, 2)], "subfloor")
    assert exc.value.reason == "source_envelope_mismatch"


def test_arm_a_equal_length_source_swap_refuses(tmp_path):
    source_a, source_b = tmp_path / "a.txt", tmp_path / "b.txt"
    source_a.write_text("one two three", encoding="utf-8")
    source_b.write_text("red blue green", encoding="utf-8")
    env = tmp_path / "bridge.json"
    env.write_text(json.dumps({
        "target": {"path": str(source_b)},
        "results": {"judge": {"kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p"}},
    }), encoding="utf-8")
    row = {
        "source_work_words": 3, "source_work_sha256": source_work_sha256(source_a.read_text()),
        "source_kind": "whole_work", "n_words": 3, "content_sha256": "sha256:" + "0" * 64,
        "judge": {"source_envelope_path": str(env), "source_envelope_sha256": framed_file_digest("setec.voiceprint.spec78.source-envelope-file.v1", env), "kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p"},
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._reopen_envelopes([row])
    assert exc.value.reason == "source_envelope_mismatch"


def test_arm_a_false_source_work_word_count_refuses(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("one two three", encoding="utf-8")
    digest = source_work_sha256(source.read_text(encoding="utf-8"))
    env = tmp_path / "bridge.json"
    env.write_text(json.dumps({
        "target": {
            "path": str(source),
            "words": 99_999,
            "source_content_sha256": digest,
        },
        "results": {},
    }), encoding="utf-8")
    row = {
        "source_work_words": 99_999,
        "source_work_sha256": digest,
        "source_kind": "whole_work",
        "n_words": 99_999,
        "content_sha256": "sha256:" + "0" * 64,
        "judge": {
            "source_envelope_path": str(env),
            "source_envelope_sha256": framed_file_digest(
                "setec.voiceprint.spec78.source-envelope-file.v1", env
            ),
            "kind": "manifest",
            "model": "m",
            "model_revision": "r",
            "prompt_version": "p",
        },
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._reopen_envelopes([row])
    assert exc.value.reason == "source_envelope_mismatch"


def test_prompt_blindness_scan_hits_signal_names_but_not_eyes():
    assert npe._prompt_names_signal("Use StoryScope narrative analysis.")
    assert npe._prompt_names_signal("Choose protagonist_choice when appropriate.")
    assert not npe._prompt_names_signal("Her eyes adjusted to the dark yesterday.")


def test_segmenter_registration_requires_exact_four_key_identity(tmp_path):
    # This validates the registration carrier directly; CLI supplies these
    # same three identity flags plus the fixed emitter.
    bad = {"emitter": "narrative_decision_long_form", "segmenter_version": npe.SEGMENTER_VERSION}
    with pytest.raises(npe.CalibrationRefusal):
        npe._validate_registration({
            "schema": "narrative-polarity-registration/1", "date": "2026-07-27", "arm": "segment_regime",
            "thresholds_sha256": "x", "work_ids_sha256": "x", "design_sha256": "x", "signal_id_set_sha256": "x",
            "segmenter": bad,
            "judge": {"kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p", "context_bound_words": 1},
            "generation_prompts": [],
        }, arm="segment_regime", date="2026-07-27")


def test_not_aggregatable_transfer_caveat_policy_is_exact():
    expected = {
        npe.signal_id_for(f, s) for f, _, s in npe.iter_signals()
        if npe.OPERATOR_TABLE[(f.key, s.option)] == "not_aggregatable"
    }
    assert len(expected) == 12
    assert all(npe.OPERATOR_TABLE[(next(f.key for f, _, s in npe.iter_signals() if npe.signal_id_for(f, s) == sid), npe.SIGNAL_SPECS[sid].option)] == "not_aggregatable" for sid in expected)


def test_real_signal_spec_numeric_encoder_and_response_range():
    sid = next(sid for sid, spec in npe.SIGNAL_SPECS.items() if spec.feature_type == "scale")
    spec = npe.SIGNAL_SPECS[sid]
    assert npe.convert_mean_response(spec, spec.response_options[0]) >= 0
    assert npe._response_range(sid) > 0


def test_malformed_signals_and_judge_refuse_not_typeerror():
    row = {
        "text_id":"x", "label":"pre_ai_human", "role":"primary", "source_kind":"whole_work",
        "source_work_id":"w", "source_work_words":None, "source_work_sha256":None, "n_words":1,
        "content_sha256":"sha256:" + "0" * 64, "subfloor_bridge_side":None,
        "provenance":{"class":"human", "author_id":"a", "publication_year":1, "source_corpus_id":"c", "claim_license_amendment":None, "register_extension":None},
        "segmenter":None, "read_mode":None, "judge":[], "signals":[],
    }
    with pytest.raises(npe.CalibrationRefusal):
        npe._validate_row(row, "subfloor", {"kind":"manifest", "model":"m", "model_revision":"r", "prompt_version":"p", "context_bound_words":1})


def test_null_signal_cells_refuse_before_fingerprint_walk():
    row = {
        "text_id":"x", "label":"pre_ai_human", "role":"primary",
        "source_kind":"whole_work", "source_work_id":"w",
        "source_work_words":None, "source_work_sha256":None, "n_words":1,
        "content_sha256":"sha256:" + "0" * 64,
        "subfloor_bridge_side":None,
        "provenance":{
            "class":"human", "author_id":"a", "publication_year":1,
            "source_corpus_id":"c", "claim_license_amendment":None,
            "register_extension":None,
        },
        "segmenter":None, "read_mode":None,
        "judge":{
            "kind":"manifest", "model":"m", "model_revision":"r",
            "prompt_version":"p", "source_envelope_sha256":"sha256:" + "1" * 64,
            "source_envelope_path":"producer.json",
        },
        "signals":{sid:None for sid in npe.SIGNAL_IDS},
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._validate_row(
            row, "subfloor",
            {"kind":"manifest", "model":"m", "model_revision":"r",
             "prompt_version":"p", "context_bound_words":1},
        )
    assert exc.value.reason == "manifest_schema_violation"


def test_corpus_floor_routes_to_step_two_insufficient_support():
    verdict, step = npe.derive_polarity_verdict(
        arm="subfloor", availability=(1, 1), availability_floor=.9, support=(24,24), min_support=24,
        bridge_support=(8,8), min_bridge=8, response_class="numeric", min_class_n=20,
        bridge=((0,0), (0,0)), ceiling=.1, degenerate=False, interval=(.4,.6), threshold=.2,
        corpus_ok=False,
    )
    assert (verdict, step) == ("insufficient_support", 2)


def test_receipt_guard_admits_the_real_receipt_float_leaves():
    """Signal ids and class keys carry dots of their own.

    A guard that joins the path into one string splices a single key into
    several segments, so every allowed pattern misses and the emit path dies
    on its own well-formed receipt.
    """
    sid = npe.SIGNAL_IDS[0]
    assert "." in sid, "signal ids are dotted; this test would not bind otherwise"
    npe.assert_no_per_text_disclosure({
        "per_signal": {sid: {
            "availability_by_class": {"pre_ai_human": 1.0, "ai_generated": 0.95},
            "statistics": [{"value": 0.61, "threshold": 0.2}],
            "ci": {"lo": 0.1, "hi": 0.9, "z": 1.96},
            "bridge": {"value": 0.02, "ci_upper": 0.03, "threshold": 0.1,
                       "by_class": {"pre_ai_human": 0.01, "ai_generated": 0.02},
                       "ci_upper_by_class": {"pre_ai_human": 0.01, "ai_generated": 0.02}},
        }},
        "class_counts": {"pre_ai_human.primary": {
            "max_share_single_work": 0.04,
            "segment_count_stats": {"median": 3.0},
        }},
        "covered_length_range": {"median_words": 5100.0},
        "covered_source_work_range": {"median_words": 30000.0},
        "floors_applied": {"max_share_single_work": 0.15},
    })


def test_receipt_guard_wildcard_spans_exactly_one_segment():
    sid = npe.SIGNAL_IDS[0]
    # An unlisted leaf under a real signal id must still be caught...
    with pytest.raises(ValueError):
        npe.assert_no_per_text_disclosure({"per_signal": {sid: {"smuggled": 1.2}}})
    # ...and "*" must not swallow a run of segments into an allowed pattern.
    with pytest.raises(ValueError):
        npe.assert_no_per_text_disclosure(
            {"per_signal": {sid: {"ci": {"extra": {"lo": 0.5}}}}}
        )


def test_drops_are_attributed_to_the_role_that_lost_the_row():
    """dropped_by_reason is per (label, role); a primary drop must not also
    appear against that label's bridge rows, which lost nothing."""
    def row(role):
        return {
            "label": "pre_ai_human", "role": role, "source_work_id": "w",
            "source_kind": "whole_work" if role == "bridge" else "segment",
            "segmenter": None if role == "bridge" else {"tier": "paragraph"},
            "judge": {"source_envelope_sha256": "sha256:" + "0" * 64},
            "provenance": {"author_id": "a"},
        }
    dropped = Counter({("pre_ai_human", "primary", "duplicate_content_sha256"): 3})
    counts = npe._class_counts([row("primary"), row("bridge")], dropped, "segment_regime")
    assert counts["pre_ai_human.primary"]["dropped_by_reason"]["duplicate_content_sha256"] == 3
    assert counts["pre_ai_human.bridge"]["dropped_by_reason"]["duplicate_content_sha256"] == 0


def test_unformed_bridge_refuses_instead_of_asserting():
    # Exported entry point: an assert would vanish under -O and fall through
    # to a TypeError on the None bridge.
    verdict, step = npe.derive_polarity_verdict(
        arm="segment_regime", availability=(1.0, 1.0), availability_floor=.9,
        support=(24, 24), min_support=24, bridge_support=(8, 8), min_bridge=8,
        response_class="numeric", min_class_n=20, bridge=None, ceiling=.1,
        degenerate=False, interval=(.5, .9), threshold=.2,
    )
    assert (verdict, step) == ("insufficient_support", 2)


def test_receipt_guard_rejects_per_text_key_and_unlisted_float():
    try:
        npe.assert_no_per_text_disclosure({"per_text": {}})
    except ValueError:
        pass
    else:
        raise AssertionError("per_text key escaped receipt guard")
    try:
        npe.assert_no_per_text_disclosure({"unlisted": 1.2})
    except ValueError:
        pass
    else:
        raise AssertionError("unlisted float escaped receipt guard")
