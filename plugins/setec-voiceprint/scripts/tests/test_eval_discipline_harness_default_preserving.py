#!/usr/bin/env python3
"""Default-preserving wiring for the eval-discipline harness flags (spec 28).

Acceptance #4: without --topic-split the harness JSON is byte-for-byte identical
to the pre-change harness on the same manifest; with topic labels present but
the flag off, exactly ONE warning is added and no metric value changes.
--simpson-check off by default leaves the report unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validation_harness as vh  # type: ignore  # noqa: E402


def _args(**over):
    ns = argparse.Namespace(
        surface="smoothing_diagnosis",
        use="validation",
        positive_status=list(vh.DEFAULT_POSITIVE_STATUSES),
        negative_status=list(vh.DEFAULT_NEGATIVE_STATUSES),
        fpr_target=None,
        confidence_level=0.95,
        metric_bootstrap_resamples=0,  # deterministic, fast
        ci_method="wilson",
        seed=1,
        mattr_window=50,
        no_tier2=True,
        no_tier3=True,
        allow_non_prose=True,
        strip_rules=None,
        strip_aggressive=False,
        strict_manifest=False,
        check_corpus=False,
        corpus_warn_threshold=0.01,
        corpus_fail_threshold=0.05,
        slice_by=None,
        no_language_status_slice=False,
        no_records_table=False,
        records_limit=100,
        scored_records_cache=None,
        metrics_cache=None,
        topic_split=False,
        simpson_check=None,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _write_manifest(tmp_path, with_topic: bool):
    """A tiny labeled manifest with two AI + two human entries."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    entries = []
    for i in range(4):
        ai = i < 2
        body = tmp_path / f"doc{i}.txt"
        # Enough distinct prose for the compression scorer to produce a score.
        body.write_text(
            "The committee deliberated for hours. " * 20
            + f"Document {i} discusses many varied things in great detail. " * 10,
            encoding="utf-8",
        )
        entry = {
            "id": f"doc{i}",
            "path": f"doc{i}.txt",
            "ai_status": "ai_generated" if ai else "pre_ai_human",
            "use": ["validation"],
            "register": "essay",
        }
        if with_topic:
            entry["topic"] = "policy" if i % 2 == 0 else "sports"
        import json
        entries.append(json.dumps(entry))
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return manifest


def _strip_volatile(result):
    """Drop keys that legitimately vary run-to-run or by flag, so the rest can
    be compared. We compare the metric slices + scored counts."""
    return {
        "n_scored_records": result["n_scored_records"],
        "slices": result["slices"],
        "operating_point": result["operating_point"],
    }


def test_no_topic_labels_no_warning_no_eval_keys(tmp_path):
    manifest = _write_manifest(tmp_path, with_topic=False)
    res = vh.run_harness(_args(manifest=manifest))
    assert "topic_leakage" not in res
    assert "simpson_inversion" not in res
    # No topic-leakage warning when there are no topic labels.
    assert not any("topic leakage" in w.lower() for w in res["warnings"])


def test_topic_labels_present_flag_off_adds_one_warning_no_metric_change(tmp_path):
    manifest = _write_manifest(tmp_path, with_topic=True)
    res_off = vh.run_harness(_args(manifest=manifest, topic_split=False))
    # Exactly the topic-leakage hazard warning is added (count it).
    leakage_warnings = [
        w for w in res_off["warnings"] if "topic leakage" in w.lower()
    ]
    assert len(leakage_warnings) == 1
    # No eval-discipline result keys when the flag is off.
    assert "topic_leakage" not in res_off
    assert "simpson_inversion" not in res_off

    # The metric slices are identical to a run where topic labels are absent
    # (the presence of the label must not change any number).
    res_no_topic = vh.run_harness(
        _args(manifest=_write_manifest(tmp_path / "nt", with_topic=False)))
    assert _strip_volatile(res_off)["slices"] == _strip_volatile(res_no_topic)["slices"]


def test_topic_split_on_adds_keys(tmp_path):
    manifest = _write_manifest(tmp_path, with_topic=True)
    res = vh.run_harness(_args(manifest=manifest, topic_split=True))
    assert "topic_leakage" in res
    # With the flag on, the present-but-off warning is NOT emitted.
    assert not any("pass --topic-split" in w for w in res["warnings"])


def test_simpson_check_on_adds_key(tmp_path):
    manifest = _write_manifest(tmp_path, with_topic=True)
    res = vh.run_harness(_args(manifest=manifest, simpson_check="register"))
    assert "simpson_inversion" in res
    assert res["simpson_inversion"]["strata_field"] == "register"
