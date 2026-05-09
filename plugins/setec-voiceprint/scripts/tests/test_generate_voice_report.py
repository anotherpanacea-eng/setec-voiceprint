#!/usr/bin/env python3
"""Regression tests for generate_voice_report.py.

Strategy: a single synthetic JSON-fixture set under
``test_data/voice_report_fixture/`` exercises every section the
template defines. Tests verify that:

  * Numerical sections are populated from the JSON inputs
    (header counts, durable voiceprint table, idiolect tables,
    cross-period distance table).
  * Interpretive sections are emitted as ``{TODO: interpret}``
    markers, NOT as auto-generated prose.
  * Optional sections (drift, comparison) are present when their
    inputs are supplied and absent when they aren't.
  * The privacy guard refuses non-private ``--out`` paths.
  * A profile-only invocation works (no drift, no idiolect).
  * The report's overall shape matches the template's section order.

The "framework's deepest principle" — interpretive readings are the
writer's call — translates into a hard test invariant: the script
MUST emit TODO markers for the interpretive sections rather than
fabricate prose. ``test_no_auto_prose_in_interpretive_sections``
pins that contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import generate_voice_report as gvr  # type: ignore
import acquisition_core as ac  # type: ignore  # noqa: F401

FIXTURE_DIR = ROOT / "test_data" / "voice_report_fixture"
PROFILE = FIXTURE_DIR / "voice_profile.json"
DRIFT = FIXTURE_DIR / "voice_drift.json"
IDIOLECT_N1 = FIXTURE_DIR / "idiolect_n1.json"
IDIOLECT_N2 = FIXTURE_DIR / "idiolect_n2.json"
CONTROL_DRIFT = FIXTURE_DIR / "control_drift.json"


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        voice_profile=str(PROFILE),
        voice_drift=None,
        idiolect_n1=None,
        idiolect_n2=None,
        idiolect_n3=None,
        comparison_drift=None,
        author_name="Synthetic Author",
        corpus_label="Synthetic Author's blog",
        register="blog_essay",
        ai_disclosure=None,
        control_writer_name="the control writer",
        cv_ceiling=gvr.DURABLE_CV_CEILING,
        out=None,
        allow_public_output=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def load_inputs(**kw) -> gvr.ReportInputs:
    """Build a `ReportInputs` from fixture JSONs with optional overrides."""
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    base = dict(
        voice_profile=profile,
        voice_drift=None,
        idiolect_n1=None,
        idiolect_n2=None,
        idiolect_n3=None,
        comparison_drift=None,
        author_name="Synthetic Author",
        corpus_label="Synthetic Author's blog",
        register="blog_essay",
    )
    base.update(kw)
    return gvr.ReportInputs(**base)


# ------------------- Fixture sanity ------------------------------


def test_fixture_files_exist():
    for f in (PROFILE, DRIFT, IDIOLECT_N1, IDIOLECT_N2, CONTROL_DRIFT):
        assert f.is_file(), f"missing fixture: {f}"


def test_template_ships_with_plugin():
    """The default template path must resolve to a real file —
    otherwise the script's documentation / refer pointers break."""
    assert gvr.DEFAULT_TEMPLATE_PATH.is_file(), \
        f"template not at expected path: {gvr.DEFAULT_TEMPLATE_PATH}"


# ------------------- Helper unit tests ---------------------------


def test_todo_marker_format():
    m = gvr.todo("write 3 paragraphs")
    assert m.startswith("{TODO: interpret:")
    assert "write 3 paragraphs" in m
    assert m.endswith("}")


def test_format_value_buckets():
    assert gvr._format_value(0.0042) == "0.0042"
    assert gvr._format_value(15.87) == "15.870"
    assert gvr._format_value(1234.567) == "1,234.57"
    assert gvr._format_value(None) == "None"
    assert gvr._format_value(float("nan")) == "n/a"


def test_baseline_summary_extracts_counts():
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    bs = gvr._baseline_summary(profile)
    assert bs["n_files"] == 47
    assert bs["total_words"] == 142318
    assert bs["mean_words"] == 3028.0


def test_baseline_summary_handles_missing_keys():
    """Defensive read — missing sections fall back to zero, not
    raise."""
    bs = gvr._baseline_summary({})
    assert bs["n_files"] == 0
    assert bs["total_words"] == 0


def test_stable_features_filters_by_cv_ceiling():
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    # CV ceiling 0.10: 'is' (CV 0.126) and 'to' (CV 0.114) and
    # 'question_per_100s' (CV 0.225) should drop out.
    stable = gvr._stable_features(profile, cv_ceiling=0.10)
    fwords = {r["name"] for r in stable.get("function_words", [])}
    assert "the" in fwords  # CV 0.066
    assert "of" in fwords   # CV 0.069
    assert "is" not in fwords  # CV 0.126 > ceiling
    assert "to" not in fwords  # CV 0.114 > ceiling
    punct = {r["name"] for r in stable.get("punctuation", [])}
    assert "punct_total_per_100w" in punct
    assert "question_per_100s" not in punct  # CV 0.225 > ceiling


def test_stable_features_skips_zero_means():
    """A feature whose mean is 0 isn't stable, it's missing.
    The filter must drop it even if the CV happens to be small."""
    profile = {
        "families": {
            "test": {
                "most_stable_features": [
                    {"name": "real", "mean": 1.0, "cv": 0.05},
                    {"name": "missing", "mean": 0.0, "cv": 0.0},
                ]
            }
        }
    }
    stable = gvr._stable_features(profile, cv_ceiling=0.10)
    names = {r["name"] for r in stable.get("test", [])}
    assert "real" in names
    assert "missing" not in names


def test_collect_idiolect_rows_aggregates_across_n():
    n1 = json.loads(IDIOLECT_N1.read_text(encoding="utf-8"))
    n2 = json.loads(IDIOLECT_N2.read_text(encoding="utf-8"))
    rows = gvr._collect_idiolect_rows(n1, n2)
    # 2 rows from n1 + 3 from n2.
    assert len(rows) == 5
    # Sorted by descending score.
    scores = [float(r["score"]) for r in rows]
    assert scores == sorted(scores, reverse=True)
    # Each row carries its n value.
    assert {r["n"] for r in rows} == {1, 2}


def test_split_topic_vs_rhetorical():
    """Single-token rows go to topic; mostly-stopword multi-token
    rows go to rhetorical."""
    rows = [
        {"phrase": "ontology", "n": 1, "score": 100},
        {"phrase": "in other words", "n": 3, "score": 90},
        {"phrase": "embodied agency", "n": 2, "score": 80},
        {"phrase": "I think", "n": 2, "score": 70},
    ]
    topic, rhetorical = gvr._split_topic_vs_rhetorical(rows)
    topic_phrases = {r["phrase"] for r in topic}
    rhet_phrases = {r["phrase"] for r in rhetorical}
    assert "ontology" in topic_phrases
    assert "embodied agency" in topic_phrases  # mostly content words
    assert "in other words" in rhet_phrases  # all stopwords
    assert "I think" in rhet_phrases


def test_date_range_from_drift_summarizes_periods():
    drift = json.loads(DRIFT.read_text(encoding="utf-8"))
    rng = gvr._date_range_from_drift(drift)
    assert rng == "2018 through 2022"


def test_date_range_from_drift_returns_empty_when_absent():
    assert gvr._date_range_from_drift(None) == ""
    assert gvr._date_range_from_drift({}) == ""
    assert gvr._date_range_from_drift({"periods": []}) == ""


# ------------------- Section renderers ---------------------------


def test_render_header_includes_counts_and_corpus_label():
    inputs = load_inputs()
    lines = gvr.render_header(inputs)
    text = "\n".join(lines)
    assert "Synthetic Author: Voice profile insights" in text
    assert "Synthetic Author's blog" in text
    assert "47 files" in text
    assert "142,318 words" in text


def test_render_header_includes_disclosure_when_provided():
    inputs = load_inputs(
        ai_disclosure="no AI use on this blog at any point",
    )
    text = "\n".join(gvr.render_header(inputs))
    assert "no AI use" in text
    assert "ground truth" in text


def test_render_header_omits_disclosure_when_absent():
    inputs = load_inputs(ai_disclosure=None)
    text = "\n".join(gvr.render_header(inputs))
    assert "ground truth" not in text


def test_render_durable_voiceprint_emits_table_and_todo():
    inputs = load_inputs()
    text = "\n".join(gvr.render_durable_voiceprint(inputs))
    # Auto: tables for each family with at least one stable feature.
    assert "## What the profile pins down as durable" in text
    assert "function_words" in text
    assert "punct_total_per_100w" in text
    # CV-stable feature names appear.
    assert "`the`" in text
    # Manual: TODO marker with feature hints.
    assert "{TODO: interpret:" in text
    assert "load-bearing" in text or "identity" in text


def test_render_durable_emits_only_todo_when_no_stable_features():
    """A thin or noisy corpus has no CV<ceiling features. Section
    must still emit cleanly with a TODO explaining what happened."""
    profile = {
        "baseline_summary": {"n_files": 3, "total_words": 1500},
        "families": {
            "function_words": {
                "most_stable_features": [
                    {"name": "the", "mean": 5.0, "cv": 0.50},
                ]
            }
        },
    }
    inputs = gvr.ReportInputs(
        voice_profile=profile,
        author_name="X", corpus_label="X",
    )
    text = "\n".join(gvr.render_durable_voiceprint(inputs))
    assert "{TODO: interpret:" in text
    # No table headers (since no rows).
    assert "| feature | mean | CV |" not in text


def test_render_idiolectic_topic_and_rhetorical_tables():
    inputs = load_inputs(
        idiolect_n1=json.loads(IDIOLECT_N1.read_text(encoding="utf-8")),
        idiolect_n2=json.loads(IDIOLECT_N2.read_text(encoding="utf-8")),
    )
    text = "\n".join(gvr.render_idiolectic_vocabulary(inputs))
    assert "### Topic-domain phrases" in text
    assert "### Rhetorical-move signatures" in text
    assert "ontology" in text
    assert "I think" in text
    assert "{TODO: interpret:" in text


def test_render_idiolectic_emits_todo_when_no_input():
    inputs = load_inputs()
    text = "\n".join(gvr.render_idiolectic_vocabulary(inputs))
    assert "{TODO: interpret:" in text
    assert "no idiolect rows supplied" in text


def test_render_drift_includes_cross_period_table():
    inputs = load_inputs(
        voice_drift=json.loads(DRIFT.read_text(encoding="utf-8")),
    )
    text = "\n".join(gvr.render_drift(inputs))
    assert "## Era / drift" in text
    assert "Burrows-Delta" in text
    assert "0.272" in text or "0.2720" in text
    # Drifting features summary.
    assert "What's drifting" in text
    # Stable through drift summary.
    assert "What's stable through the drift" in text


def test_render_drift_returns_empty_when_no_input():
    inputs = load_inputs()
    assert gvr.render_drift(inputs) == []


def test_render_comparison_includes_subject_and_control_magnitudes():
    inputs = load_inputs(
        voice_drift=json.loads(DRIFT.read_text(encoding="utf-8")),
        comparison_drift=json.loads(CONTROL_DRIFT.read_text(encoding="utf-8")),
        control_writer_name="Control Writer",
    )
    text = "\n".join(gvr.render_comparison(inputs))
    assert "Comparison to Control Writer" in text
    # Both magnitudes appear in the headline.
    assert "0.272" in text or "0.2720" in text
    assert "0.268" in text or "0.2680" in text
    assert "{TODO: interpret:" in text


def test_render_comparison_returns_empty_without_both_inputs():
    inputs = load_inputs(voice_drift=json.loads(DRIFT.read_text(encoding="utf-8")))
    # Drift but no comparison → no comparison section.
    assert gvr.render_comparison(inputs) == []
    inputs2 = load_inputs(
        comparison_drift=json.loads(CONTROL_DRIFT.read_text(encoding="utf-8")),
    )
    # Comparison but no subject drift → no comparison section either.
    assert gvr.render_comparison(inputs2) == []


def test_render_three_observations_is_all_todos():
    inputs = load_inputs()
    text = "\n".join(gvr.render_three_observations(inputs))
    # Three TODOs, no auto-generated prose between them.
    assert text.count("{TODO: interpret:") == 3


def test_render_what_cannot_say_includes_disclosure():
    inputs = load_inputs(ai_disclosure="no AI involvement")
    text = "\n".join(gvr.render_what_cannot_say(inputs))
    assert "What this analysis cannot say" in text
    assert "no AI involvement" in text
    assert "deepest principle" in text


def test_render_whats_distinctive_is_three_todos():
    inputs = load_inputs(register="literary_horror")
    text = "\n".join(gvr.render_whats_distinctive(inputs))
    assert "literary_horror" in text
    assert text.count("{TODO: interpret:") == 3


# ------------------- Full report ---------------------------------


def test_full_report_contains_all_sections_when_all_inputs_supplied():
    inputs = load_inputs(
        voice_drift=json.loads(DRIFT.read_text(encoding="utf-8")),
        idiolect_n1=json.loads(IDIOLECT_N1.read_text(encoding="utf-8")),
        idiolect_n2=json.loads(IDIOLECT_N2.read_text(encoding="utf-8")),
        comparison_drift=json.loads(CONTROL_DRIFT.read_text(encoding="utf-8")),
        control_writer_name="Control Writer",
        ai_disclosure="no AI use on this blog",
    )
    report = gvr.render_report(inputs)
    expected_headers = [
        "# Synthetic Author: Voice profile insights",
        "## What the profile pins down as durable",
        "## Idiolectic vocabulary",
        "## Era / drift",
        "## Comparison to Control Writer",
        "## Three observations to flag",
        "## What this analysis cannot say",
        "## What's distinctive about this corpus",
    ]
    for header in expected_headers:
        assert header in report, f"missing section: {header}"


def test_profile_only_report_omits_optional_sections():
    """Profile-only invocation: no drift, no idiolect, no
    comparison. The optional sections must not appear."""
    inputs = load_inputs()
    report = gvr.render_report(inputs)
    # Drift section absent.
    assert "## Era / drift" not in report
    # Comparison absent.
    assert "Comparison to" not in report
    # Required sections still present.
    assert "## What the profile pins down as durable" in report
    assert "## Idiolectic vocabulary" in report
    assert "## What this analysis cannot say" in report


def test_no_auto_prose_in_interpretive_sections():
    """The framework's deepest principle: interpretive readings are
    the writer's call. The script MUST mark interpretive sections
    as TODO rather than auto-write prose. This pins that contract.
    """
    inputs = load_inputs(
        voice_drift=json.loads(DRIFT.read_text(encoding="utf-8")),
        idiolect_n1=json.loads(IDIOLECT_N1.read_text(encoding="utf-8")),
        idiolect_n2=json.loads(IDIOLECT_N2.read_text(encoding="utf-8")),
    )
    report = gvr.render_report(inputs)
    # Three observations: all 3 must be TODOs (no auto-written
    # observation prose).
    obs_section = report.split("## Three observations to flag")[1]
    obs_section = obs_section.split("## What this analysis cannot say")[0]
    assert obs_section.count("{TODO: interpret:") == 3

    # What's distinctive: 3 TODOs.
    distinct = report.split("## What's distinctive")[1]
    assert distinct.count("{TODO: interpret:") == 3


def test_report_collapses_excess_blank_runs():
    inputs = load_inputs()
    report = gvr.render_report(inputs)
    # No three-blank-line runs (collapse pass).
    assert "\n\n\n" not in report


def test_report_ends_with_newline():
    inputs = load_inputs()
    report = gvr.render_report(inputs)
    assert report.endswith("\n")


# ------------------- run() end-to-end ----------------------------


def test_run_writes_report_to_out(tmp_path):
    out = tmp_path / "ai-prose-baselines-private" / "report.md"
    args = make_args(
        voice_drift=str(DRIFT),
        idiolect_n1=str(IDIOLECT_N1),
        idiolect_n2=str(IDIOLECT_N2),
        out=str(out),
    )
    rc = gvr.run(args)
    assert rc == 0
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "Voice profile insights" in body
    assert "{TODO: interpret:" in body


def test_run_writes_to_stdout_when_no_out(tmp_path, capsys):
    args = make_args()
    rc = gvr.run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Voice profile insights" in out


def test_run_privacy_guard_refuses_non_private(tmp_path):
    public_out = tmp_path / "public_oops" / "report.md"
    args = make_args(
        out=str(public_out),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            gvr.run(args)
        assert exc.value.code == 2
    else:  # pragma: no cover
        try:
            gvr.run(args)
            assert False
        except SystemExit as e:
            assert e.code == 2


def test_run_handles_missing_optional_inputs_cleanly(tmp_path):
    """Profile-only invocation runs without --voice-drift /
    --idiolect-* / --comparison-drift."""
    out = tmp_path / "ai-prose-baselines-private" / "p.md"
    args = make_args(out=str(out))
    rc = gvr.run(args)
    assert rc == 0
    body = out.read_text(encoding="utf-8")
    assert "## Era / drift" not in body
    assert "Comparison to" not in body


def test_run_exits_2_on_missing_voice_profile(tmp_path):
    """Invalid --voice-profile path → exit 2."""
    args = make_args(voice_profile="/nonexistent/profile.json")
    rc = gvr.run(args)
    assert rc == 2


# ------------------- CLI surface ---------------------------------


def test_cli_help_lists_required_flags():
    parser = gvr.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--voice-profile", "--voice-drift", "--idiolect-n1",
        "--idiolect-n2", "--idiolect-n3", "--comparison-drift",
        "--author-name", "--corpus-label", "--register",
        "--ai-disclosure", "--control-writer-name", "--cv-ceiling",
        "--out", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_argparse_rejects_missing_required_flags():
    parser = gvr.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args([])  # missing all required flags
        with pytest.raises(SystemExit):
            parser.parse_args(["--voice-profile", "x.json"])  # missing author/corpus


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
