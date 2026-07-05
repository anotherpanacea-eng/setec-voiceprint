#!/usr/bin/env python3
"""Tests for the AITDNA external-validation benchmark harness.

Covers the adapter (notion-label computation under the FIXED constants,
co-written rule, human-only reference slice, manifest validity, the §3b
field-map, NOTICE/no-vendor), the runner (membership_novelty via
originality_audit, binoculars via injected score_fn, stand-in, orientation,
skip-on-error), the scorer (per-detector metrics + co-written-human FPR),
the anti-Goodhart invariants (no writes, no fitter import, one-way labels,
report block, notion_coverage), and the REQUIRED
``test_notion_parameters_fixed`` guard (peer of the PAN harness's
``test_no_aggregate_score``).

All model-free: binoculars runs via its injected ``score_fn`` hook;
membership_novelty is pure stdlib DJ-Search; nothing here loads a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_CALIB = _SCRIPTS / "calibration"
for _p in (_SCRIPTS, _CALIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pan_metrics as pm  # noqa: E402
import aitdna_to_manifest as adapter  # noqa: E402
import aitdna_benchmark as bench  # noqa: E402
from manifest_validator import validate_manifest  # noqa: E402

FIXTURE = _SCRIPTS / "test_data" / "aitdna_fixture"


# ============================================================
# Helpers
# ============================================================


def _build_manifest(tmp_path: Path, *, aitdna_dir: Path | None = None):
    aitdna_dir = aitdna_dir or FIXTURE
    manifest = tmp_path / "manifest.jsonl"
    reference = tmp_path / "reference.jsonl"
    text_dir = tmp_path / "text"
    rc = adapter.main([
        "--aitdna-dir", str(aitdna_dir),
        "--config", "token",
        "--manifest", str(manifest),
        "--reference-manifest", str(reference),
        "--text-dir", str(text_dir),
    ])
    assert rc == 0
    return manifest, reference


class StubBackend:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.revision = None

    def identifier_block(self):
        return {"id": self.model_id}


def _det_score_fn(backend, text):
    n = max(len(text), 1)
    base = sum(1 for c in text if c.isalpha()) / n
    bump = 0.5 if backend.model_id == "scorer" else 1.0
    return [base + bump] * 120


def _binoculars_kwargs(**over):
    kw = {
        "score_fn": _det_score_fn,
        "scorer_backend": StubBackend("scorer"),
        "observer_backend": StubBackend("observer"),
    }
    kw.update(over)
    return kw


def _run(manifest, reference, detectors, **over):
    args = argparse.Namespace(
        manifest=str(manifest),
        reference_manifest=str(reference) if reference else None,
        detectors=detectors,
        operating_point=over.pop("operating_point", False),
        threshold_low=over.pop("threshold_low", None),
        threshold_high=over.pop("threshold_high", None),
        n_resamples=over.pop("n_resamples", 200),
        confidence_level=0.95,
        seed=7,
        per_instance=over.pop("per_instance", None),
        _binoculars_kwargs=over.pop("_binoculars_kwargs", _binoculars_kwargs()),
        _standin_kwargs=over.pop("_standin_kwargs", {}),
    )
    return bench.run_benchmark(args)


# ============================================================
# Adapter — notion-label computation (the load-bearing hole)
# ============================================================


def test_adapter_manifest_validates_clean(tmp_path):
    manifest, _ = _build_manifest(tmp_path)
    result = validate_manifest(manifest)
    assert result["n_errors"] == 0, result["issues"]


def test_document_level_label_tau(tmp_path):
    manifest, _ = _build_manifest(tmp_path)
    entries = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    by_id = {e["source_id"]: e for e in entries}
    # Fixture: 6 human-only (label 0), 4 all-AI (label 1), 2 co-written
    # AI-majority (label 1), 2 co-written human-majority (label 0).
    n_human = sum(1 for e in entries if e["ai_status"] == "pre_ai_human")
    n_ai = sum(1 for e in entries if e["ai_status"] == "ai_generated")
    assert n_human == 8  # 6 human-only + 2 co-written human-majority
    assert n_ai == 6     # 4 all-AI + 2 co-written AI-majority
    # An all-Bot doc has ai_token_ratio 1.0 -> label 1.
    ai_docs = [e for e in entries if e["notes"]["ai_token_ratio"] == 1.0]
    assert ai_docs and all(e["ai_status"] == "ai_generated" for e in ai_docs)


def test_co_written_rule_and_editing_status(tmp_path):
    manifest, _ = _build_manifest(tmp_path)
    entries = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    co_written = [e for e in entries if e["notes"]["co_written"]]
    # 4 co-written docs (2 AI-majority + 2 human-majority).
    assert len(co_written) == 4
    # co-written -> editing_status coauthored; else raw_draft.
    for e in entries:
        if e["notes"]["co_written"]:
            assert e["editing_status"] == "coauthored"
        else:
            assert e["editing_status"] == "raw_draft"
    # The hard case exists: a co-written doc labeled HUMAN (pre_ai_human).
    hard = [e for e in co_written if e["ai_status"] == "pre_ai_human"]
    assert len(hard) == 2


def test_reference_corpus_is_human_only(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    ref_rows = [json.loads(l) for l in reference.read_text().splitlines() if l.strip()]
    # 6 human-only docs form the reference corpus.
    assert len(ref_rows) == 6
    # Every reference entry maps to a manifest entry with human_only True.
    manifest_by_id = {
        json.loads(l)["id"]: json.loads(l)
        for l in manifest.read_text().splitlines() if l.strip()
    }
    for r in ref_rows:
        assert manifest_by_id[r["id"]]["notes"]["human_only"] is True


def test_reference_provenance_block_written(tmp_path):
    _, reference = _build_manifest(tmp_path)
    prov = reference.with_suffix(".provenance.json")
    assert prov.is_file()
    block = json.loads(prov.read_text())
    assert block["name"] == "AITDNA human-only subset"
    assert block["license"] == "CC-BY-SA-4.0"
    assert block["observed_reference_size"] == 6
    assert "precedence" in block and "before any" in block["precedence"].lower()
    # The fixed constants are recorded, not read from a sweep.
    consts = block["notion_constants"]
    assert consts["doc_tau"] == 0.5
    assert consts["membership_ngram_n"] == 4
    assert consts["membership_percentile_p"] == 5


def test_notice_written_and_no_vendored_aitdna_text(tmp_path):
    manifest, _ = _build_manifest(tmp_path)
    notice = tmp_path / "text" / "NOTICE.md"
    assert notice.is_file()
    body = notice.read_text()
    assert "CC-BY-SA-4.0" in body
    assert "redistribut" in body.lower() or "share-alike" in body.lower()
    # The repo tree contains ONLY the synthetic fixture, never real AITDNA.
    assert (FIXTURE / "README.md").is_file()
    assert "NOT real AITDNA data" in (FIXTURE / "README.md").read_text()


def test_field_map_author_classification():
    # The §3b field-map vocabularies drive H/AI classification.
    assert adapter._classify_author("User") == "H"
    assert adapter._classify_author("human") == "H"
    assert adapter._classify_author("Bot") == "AI"
    assert adapter._classify_author("assistant") == "AI"
    assert adapter._classify_author("qwen2.5") is None  # unrecognized
    assert adapter._classify_author("") is None
    assert adapter._classify_author(None) is None


def test_compute_notion_label_paths():
    # Genesis-ratio path: >τ Bot -> AI (1); <=τ -> human (0).
    ai_row = {"data": [{"text": "a", "author": "Bot"},
                       {"text": "b", "author": "Bot"},
                       {"text": "c", "author": "User"}],
              "metadata": {"human_only": False}}
    r = adapter.compute_notion_label(ai_row)
    assert r["label"] == 1 and r["label_basis"] == "genesis_ratio_tau"
    assert r["co_written"] is True  # has both H and AI

    human_row = {"data": [{"text": "a", "author": "User"},
                          {"text": "b", "author": "User"},
                          {"text": "c", "author": "Bot"}],
                 "metadata": {"human_only": False}}
    r2 = adapter.compute_notion_label(human_row)
    assert r2["label"] == 0  # 1/3 Bot <= 0.5
    assert r2["co_written"] is True

    # Whole-document config with no per-token stream -> human_only fallback.
    doc_row = {"data": [{"text": "long doc text here", "author": "Bot"}],
               "metadata": {"human_only": True}}
    r3 = adapter.compute_notion_label(doc_row)
    assert r3["label"] == 0 and r3["label_basis"] == "metadata_human_only"

    # Empty text -> not scorable.
    r4 = adapter.compute_notion_label({"data": [], "metadata": {}})
    assert r4["label"] is None
    assert r4["label_basis"].startswith("not_scorable")


# ============================================================
# Runner — membership + shared detectors
# ============================================================


def test_membership_novelty_scores_against_reference(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    d = report["detectors"][0]
    assert d["detector"] == "membership_novelty"
    assert d["task_surface"] == "set_level_diversity"
    assert d["score_name"] == "reference_coverage"
    assert d["orientation"] == "lower_is_ai"
    assert d["n_scored"] == 14  # all fixture docs scored


def test_membership_novelty_unavailable_without_reference(tmp_path):
    manifest, _ = _build_manifest(tmp_path)
    # No reference manifest -> membership_novelty skips every doc honestly.
    report = _run(manifest, None, "membership_novelty", _binoculars_kwargs={})
    d = report["detectors"][0]
    assert d["n_scored"] == 0
    assert "empty_reference_corpus" in d["skipped_reasons"]


def test_binoculars_runs_via_injected_score_fn(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "binoculars_audit")
    d = report["detectors"][0]
    assert d["detector"] == "binoculars_audit"
    assert d["score_name"] == "perplexity_ratio"
    assert d["n_scored"] == 14


def test_standin_runs_with_zero_model_loads(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "length_ratio_standin", _binoculars_kwargs={})
    d = report["detectors"][0]
    assert d["detector"] == "length_ratio_standin"
    assert d["n_scored"] == 14


def test_orientation_flips_for_membership(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    ref = bench.load_reference_corpus(reference)
    entries = bench.load_manifest_entries(str(manifest))
    reg = bench.build_detector_registry(["membership_novelty"], reference=ref)
    import pan_voight_kampff_benchmark as pan_bench
    run = pan_bench.run_detector_over_manifest(
        "membership_novelty", reg["membership_novelty"], entries
    )
    assert run["orientation_applied"] == "lower_is_ai"
    for row in run["rows"]:
        assert row["oriented_score"] == -row["raw_score"]


def test_unknown_detector_rejected(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    with pytest.raises(SystemExit):
        _run(manifest, reference, "voice_verifier")  # out-of-M1 (judge dep)


# ============================================================
# Scorer + co-written-human FPR headline cell
# ============================================================


def test_co_written_fpr_null_without_operating_point(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    fpr = report["detectors"][0]["co_written_human_fpr"]
    assert fpr["value"] is None
    assert fpr["reason"] == "no_operating_point_without_fitting_to_aitdna"
    # It still reports HOW MANY co-written-human docs are in the split.
    assert fpr["n_co_written_human"] == 2


def test_co_written_fpr_present_with_operating_point(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    # Give the stand-in a two-threshold band so its bands answer.
    report = _run(
        manifest, reference, "length_ratio_standin",
        operating_point=True,
        _binoculars_kwargs={},
        _standin_kwargs={"threshold_low": 0.60, "threshold_high": 0.62},
    )
    d = report["detectors"][0]
    fpr = d["co_written_human_fpr"]
    assert fpr["n_co_written_human"] == 2
    # With an operating point in force the FPR is a real number in [0, 1].
    assert fpr["value"] is not None
    assert 0.0 <= fpr["value"] <= 1.0
    assert d["operating_point"]["in_force"] is True


def test_thresholded_null_without_operating_point(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    m = report["detectors"][0]["metrics"]
    for k in ("c_at_1", "f1", "f05u"):
        assert m[k]["value"] is None
        assert m[k]["reason"] == "no_operating_point_without_fitting_to_aitdna"
    assert m["roc_auc"]["value"] is not None  # rank metric still reported


def test_aitdna_mean_partial_not_deflated(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    cell = report["detectors"][0]["metrics"]["aitdna_mean"]
    assert cell["value"] is None
    assert cell["partial"] is True
    assert cell["n_metrics_present"] == 2  # roc_auc + brier only
    assert cell["reason"] == "partial_suite_no_operating_point"


# ============================================================
# Anti-Goodhart (load-bearing)
# ============================================================


_FORBIDDEN_WRITE_TARGETS = (
    _CALIB / "thresholds_calibrated.json",
    _SCRIPTS.parent / "capabilities.d",
    _SCRIPTS / "claim_license_surfaces",
)


def _snapshot(paths):
    snap = {}
    for p in paths:
        if p.is_file():
            snap[p] = p.read_bytes()
        elif p.is_dir():
            snap[p] = sorted(q.name for q in p.iterdir())
        else:
            snap[p] = None
    return snap


def test_harness_writes_only_report_and_sidecars(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    before = _snapshot(_FORBIDDEN_WRITE_TARGETS)
    report = _run(manifest, reference, "membership_novelty,length_ratio_standin",
                  per_instance="sink", _binoculars_kwargs={})
    assert report["report_kind"] == "aitdna_benchmark"
    after = _snapshot(_FORBIDDEN_WRITE_TARGETS)
    assert before == after, (
        "harness mutated a threshold/registry/claim-license artifact"
    )


def test_no_fitter_import_in_harness_modules():
    forbidden = ("calibrate_thresholds", "train_edit_magnitude")
    for mod in (
        _CALIB / "aitdna_to_manifest.py",
        _CALIB / "aitdna_benchmark.py",
        _CALIB / "fetch_aitdna.py",
    ):
        src = mod.read_text()
        for sym in forbidden:
            assert f"import {sym}" not in src, f"{mod.name} imports {sym}"
            assert f"from {sym}" not in src, f"{mod.name} imports {sym}"


def test_license_accept_set_admits_only_cc_by_sa():
    # Codex P2 (#296): the fetch gate's accept-set must admit ONLY the CC-BY-SA family.
    # The whole harness (gate error, NOTICE, provenance sidecar, report) asserts
    # CC-BY-SA-4.0, so an unrelated license (creativeml-openrail was a spurious leftover)
    # must NOT pass, else the fetch would proceed on a dataset it can't lawfully treat as SA.
    import fetch_aitdna
    accepts = lambda s: any(p in s for p in fetch_aitdna.EXPECTED_LICENSE_PATTERNS)
    assert accepts("cc-by-sa-4.0")
    assert accepts("cc-by-sa-3.0")             # same share-alike family
    assert not accepts("creativeml-openrail")  # the removed leftover — an unrelated license
    assert not accepts("mit")
    assert not accepts("apache-2.0")
    assert not accepts("cc-by-4.0")            # plain CC-BY (not share-alike) is a mismatch


def test_labels_flow_one_way_report_is_terminal(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    op = report["detectors"][0]["operating_point"]
    assert op["threshold"] is None
    assert op["source"] in ("none", "operator_supplied", "detector_calibrated")
    blob = json.dumps(report)
    for banned in ("best_detector", "fitted_threshold", "selected_operating_point",
                   "optimal_tau", "swept_parameter"):
        assert banned not in blob


def test_report_carries_anti_goodhart_block(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    ag = report["anti_goodhart"]
    assert ag["role"] == "external_held_out_validation"
    assert ag["is_tuning_target"] is False
    assert ag["is_calibration_target"] is False
    assert ag["is_selection_target"] is False
    assert "external validation only" in ag["statement"]


def test_report_carries_notion_coverage_block(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    nc = report["notion_coverage"]["notions"]
    by_notion = {n["notion"]: n for n in nc}
    # All 7 notions present with an honest status.
    assert set(by_notion) == {
        "document_level", "boundary_level", "sentence_level", "intent_based",
        "content_based", "membership_based", "authorship_id_based",
    }
    assert by_notion["document_level"]["status"] == "addressed"
    assert by_notion["membership_based"]["status"] == "addressed"
    assert by_notion["boundary_level"]["status"] == "partial"
    assert by_notion["sentence_level"]["status"] == "partial"
    assert by_notion["authorship_id_based"]["status"] == "partial"
    assert by_notion["intent_based"]["status"] == "not_applicable"
    assert by_notion["content_based"]["status"] == "not_applicable"
    # Every status is one of the three declared values (no fabricated F1).
    for n in nc:
        assert n["status"] in ("addressed", "partial", "not_applicable")


def test_reference_provenance_in_report(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    rp = report["reference_provenance"]
    assert rp["license"] == "CC-BY-SA-4.0"
    assert rp["name"] == "AITDNA human-only subset"
    assert report["n_reference_docs"] == 6
    assert report["dataset"]["license"] == "CC-BY-SA-4.0"
    assert report["dataset"]["arxiv"] == "2606.04906"


# ============================================================
# REQUIRED guard: notion parameters are fixed, never swept
# (peer of the PAN harness's test_no_aggregate_score)
# ============================================================


def test_notion_parameters_fixed(tmp_path):
    """The τ / co-written / n / p constants must be HARD-CODED module
    constants, never read from a sweep, config, or the row — and the
    report must emit no ``swept_parameter`` / ``optimal_*`` field. This is
    the load-bearing anti-Goodhart guard for the notion-label computation.
    """
    # 1. The constants are exactly the declared values.
    assert adapter.DOC_TAU == 0.5
    assert adapter.CO_WRITTEN_MIN == 1
    assert adapter.MEMBERSHIP_NGRAM == 4
    assert adapter.MEMBERSHIP_PERCENTILE == 5

    # 2. The constants are not wired to any CLI argument or read from the
    #    input row: neither the adapter nor the harness argparse surface
    #    exposes a τ / n / p flag, so an operator cannot sweep them. (Static
    #    teeth against a future "just add a --tau flag" regression.)
    for mod_main, sample in (
        (adapter.main, ["--tau", "0.3", "--aitdna-dir", str(tmp_path)]),
        (bench.main, ["--tau", "0.3", "--manifest", "x"]),
    ):
        with pytest.raises(SystemExit):
            # argparse exits (code 2) on an unrecognized --tau: the notion
            # parameters are NOT operator-tunable.
            mod_main(sample)

    # 3. No emitted field (in either module's report/manifest output) is a
    #    sweep/optimal marker. Scan for the *literal string tokens as JSON
    #    keys* rather than any docstring mention.
    banned_emitted_keys = (
        '"swept_parameter"', '"optimal_tau"', '"optimal_threshold"',
        '"best_tau"', '"tuned_tau"',
    )
    for mod in (
        _CALIB / "aitdna_to_manifest.py",
        _CALIB / "aitdna_benchmark.py",
    ):
        src = mod.read_text()
        for tok in banned_emitted_keys:
            assert tok not in src, f"{mod.name} emits key {tok}"

    # 3. The report records the fixed constants under anti_goodhart, and
    #    the values match the module constants (single source of truth).
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    fixed = report["anti_goodhart"]["notion_parameters_fixed"]
    assert fixed["doc_tau"] == adapter.DOC_TAU
    assert fixed["co_written_min_each_side"] == adapter.CO_WRITTEN_MIN
    assert fixed["membership_ngram_n"] == adapter.MEMBERSHIP_NGRAM
    assert fixed["membership_percentile_p"] == adapter.MEMBERSHIP_PERCENTILE

    # 4. The report blob contains no banned tuning key anywhere.
    blob = json.dumps(report)
    for tok in ("swept_parameter", "optimal_tau", "best_tau"):
        assert tok not in blob


# ============================================================
# Report shape + markdown
# ============================================================


def test_report_shape(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    assert report["report_schema_version"] == "1.0"
    assert report["report_kind"] == "aitdna_benchmark"
    assert report["dataset"]["hf_repo_id"] == "UKPLab/AITDNA"
    assert report["dataset"]["n_co_written"] == 4
    assert isinstance(report["detectors"], list) and report["detectors"]
    assert report["harness_version"] == bench.HARNESS_VERSION
    assert "cmd" in report["reproduce"]


def test_markdown_renders(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    report = _run(manifest, reference, "membership_novelty", _binoculars_kwargs={})
    md = bench.render_markdown(report)
    assert "AITDNA Benchmark Report" in md
    assert "co-written FPR" in md
    assert "Notion coverage" in md
    assert "external validation only" in md


def test_full_cli_main_writes_report(tmp_path):
    manifest, reference = _build_manifest(tmp_path)
    out = tmp_path / "report.json"
    md = tmp_path / "report.md"
    rc = bench.main([
        "--manifest", str(manifest),
        "--reference-manifest", str(reference),
        "--detectors", "membership_novelty",
        "--out", str(out),
        "--markdown", str(md),
        "--n-resamples", "0",
    ])
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["report_kind"] == "aitdna_benchmark"
    assert md.read_text().startswith("# AITDNA Benchmark Report")
