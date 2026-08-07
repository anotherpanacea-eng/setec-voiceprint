"""Tests for agd_move_scan.py + agd_move_scan_judge.py (R3B producer seam).

Torch-free (mock/manifest judges). The central contracts: OBSERVATIONS-ONLY
posture (no code, no adjudication, no aggregate-as-quality), and the
per-paragraph span-integrity discipline (warrant_judge.normalize_claims style —
NOT the document-wide _normalize_spans): wrong-locus or hallucinated spans are
DROPPED with a warning, never relocated or coerced.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import agd_move_scan  # type: ignore
import agd_move_scan_judge  # type: ignore

SAMPLE = (
    "The council may want to reconsider the crossing-guard budget. Studies have "
    "shown that guarded crossings reduce injuries, and some parents report "
    "feeling safer near schools because of them.\n\n"
    "Residents near the depot, several of whom asked about noise at the last "
    "meeting, mostly discussed parking. Therefore the plan should proceed as "
    "drafted, although the fleet schedule is tight."
)

# The scan must never emit adjudication vocabulary as data keys — nor any
# aggregate of the judge inventory (§1a refuses aggregates mechanically;
# family_counts / n_observations were removed on review and must not return).
_FORBIDDEN_KEYS = {"code", "codes", "candidates", "verdict", "score", "quality",
                   "smuggling", "soundness", "severity", "diagnosis",
                   "family_counts", "n_observations"}


def _run(tmp_path, text=SAMPLE, *args):
    target = tmp_path / "arg.txt"
    target.write_text(text, encoding="utf-8")
    out = tmp_path / "arg.json"
    argv = [str(target), "--out", str(out), "--out-md", str(tmp_path / "arg.md"), *args]
    rc = agd_move_scan.main(argv)
    envelope = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return rc, envelope


def test_mock_inventory_shape(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    assert rc == 0 and env["available"] is True
    r = env["results"]
    assert r["method_version"] == "agd_move_scan_v1"
    assert r["calibration_status"] == "heuristic"
    assert len(r["observations"]) == 2
    for o in r["observations"]:
        assert set(o) == {"family", "span", "paragraph_index", "cue"}
        assert o["family"] in agd_move_scan_judge.FAMILIES
        assert isinstance(o["paragraph_index"], int)
    # the mock's second observation is the cue-free case — cue null is first-class
    assert r["observations"][1]["cue"] is None


def test_no_adjudication_data_shape(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    def walk_keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from walk_keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk_keys(v)
    keys = {k.lower() for k in walk_keys(env["results"])}
    assert not (keys & _FORBIDDEN_KEYS)


def test_claim_license_refuses_codes_and_counts(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    dnl = env["claim_license"]["does_not_license"]
    assert "diagnostic code" in dnl
    assert "COUNT is not a quality signal" in dnl
    assert env["claim_license"]["task_surface"] == "agd_move_scan"


def test_span_integrity_drops_are_warned_not_relocated(tmp_path):
    """A manifest observation whose span lives in a DIFFERENT paragraph than its
    declared index is dropped with a warning — never relocated (the wrong-locus
    attach the R3B contract forbids)."""
    paragraphs = agd_move_scan.split_paragraphs(SAMPLE)
    manifest = {
        "values": {"observations": [
            # valid: in-paragraph span
            {"family": "GUARDING", "span": "The council may want",
             "paragraph_index": 0, "cue": "may"},
            # wrong locus: this span is in paragraph 0, declared at 1
            {"family": "ASSURING", "span": "Studies have shown",
             "paragraph_index": 1, "cue": "studies have shown"},
            # out of range
            {"family": "DISCOUNTING", "span": "although the fleet schedule is tight",
             "paragraph_index": 9, "cue": "although"},
            # hallucinated span
            {"family": "GUARDING", "span": "objectively certain beyond dispute",
             "paragraph_index": 0, "cue": None},
            # bad family
            {"family": "HEDGING", "span": "some parents report",
             "paragraph_index": 0, "cue": "some"},
            # bad cue type
            {"family": "GUARDING", "span": "some parents report",
             "paragraph_index": 0, "cue": 7},
        ]},
        "judge_identity": {"model": "test", "prompt_fingerprint_sha256": "abc"},
    }
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest", "--judge-manifest", str(mpath))
    assert rc == 0
    obs = env["results"]["observations"]
    assert len(obs) == 1 and obs[0]["family"] == "GUARDING"
    span_warnings = [w for w in env["warnings"] if w.startswith("Span integrity:")]
    assert len(span_warnings) == 5
    assert any("wrong-locus or hallucinated" in w for w in span_warnings)
    assert any("out of range" in w for w in span_warnings)
    assert any("family 'HEDGING'" in w for w in span_warnings)


def test_manifest_fingerprint_propagates_and_gates(tmp_path):
    manifest = {
        "values": {"observations": []},
        "judge_identity": {"prompt_fingerprint_sha256": "manifest-fp-123"},
    }
    mpath = tmp_path / "m.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest", "--judge-manifest", str(mpath))
    assert env["results"]["prompt_fingerprint_sha256"] == "manifest-fp-123"
    # drift gate: expecting a different fingerprint abstains with bad_input
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest", "--judge-manifest",
                   str(mpath), "--expect-fingerprint", "other-fp")
    assert env["available"] is False
    assert "fingerprint drift" in json.dumps(env)


def test_own_prompt_fingerprint(tmp_path):
    """The fingerprint hashes THIS judge's prompt — distinct from siblings'."""
    import warrant_judge  # type: ignore
    assert (agd_move_scan_judge.fingerprint_prompt()
            != warrant_judge.fingerprint_prompt())
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    assert (env["results"]["prompt_fingerprint_sha256"]
            == agd_move_scan_judge.fingerprint_prompt())


def test_short_input_bad_input(tmp_path):
    rc, env = _run(tmp_path, "Too short.", "--judge", "mock")
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_mock_run_carries_stub_caveat(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    assert any("TEST stub" in w for w in env["warnings"])


def test_observations_only_posture_in_caveats(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    assert any("OBSERVATIONS ONLY" in w for w in env["warnings"])
    assert any("R4A ADR D5" in w for w in env["warnings"])


def test_benchmark_run_manifest_provenance(tmp_path):
    """The committed Phase-1 benchmark artifacts carry provenance at the TOP
    level — the R3B run-manifest schema `{fixture_id, vendor, model_id,
    prompt_fingerprint_sha256, rep, acquired_at, values}`. The manifest judge
    must read it (not just a nested judge_identity) and --expect-fingerprint
    must be able to validate those exact artifacts."""
    manifest = {
        "fixture_id": "cue-free-structural-discounting",
        "vendor": "anthropic",
        "model_id": "claude-fable-5",
        "prompt_fingerprint_sha256": "phase1-fp-abc",
        "rep": 1,
        "acquired_at": "2026-07-12T00:00:00+00:00",
        "values": {"observations": [
            {"family": "GUARDING", "span": "The council may want",
             "paragraph_index": 0, "cue": "may"},
        ]},
    }
    mpath = tmp_path / "fixture--anthropic--rep1.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest",
                   "--judge-manifest", str(mpath),
                   "--expect-fingerprint", "phase1-fp-abc")
    assert rc == 0 and env["available"] is True
    r = env["results"]
    assert r["prompt_fingerprint_sha256"] == "phase1-fp-abc"
    ji = r["judge"]["judge_identity"]
    assert ji["model"] == "claude-fable-5"
    assert ji["vendor"] == "anthropic"
    assert len(r["observations"]) == 1


@pytest.mark.parametrize("bad", [None, {}, "not-a-list", 7])
def test_malformed_observations_collection_fails_closed(tmp_path, bad):
    """`observations: null` (or any non-list collection) is a MALFORMED
    manifest, not an empty inventory — the run must abstain (available: false,
    bad_input), never return a false successful zero-move result."""
    manifest = {"values": {"observations": bad},
                "judge_identity": {"prompt_fingerprint_sha256": "fp"}}
    mpath = tmp_path / "bad.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest",
                   "--judge-manifest", str(mpath))
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_api_result_requires_observations_list():
    """The API boundary fails closed too: a parsed response whose
    `observations` is missing or not a list is a JudgeError."""
    for parsed in ({"observations": None}, {}, {"observations": "x"}):
        with pytest.raises(agd_move_scan_judge.JudgeError):
            agd_move_scan_judge._build_api_result(
                parsed, "raw", {"kind": "test"}, ["one paragraph"])


def test_cue_must_anchor_in_span(tmp_path):
    """A non-null cue is accepted only if it anchors in the observation's span
    (casefolded): an invented cue is a location-integrity failure — dropped
    with a warning, never kept or nulled."""
    manifest = {"values": {"observations": [
        # invented cue: nowhere in the span
        {"family": "GUARDING", "span": "The council may want",
         "paragraph_index": 0, "cue": "arguably"},
        # anchored, case-variant cue — kept verbatim
        {"family": "DISCOUNTING", "span": "although the fleet schedule is tight",
         "paragraph_index": 1, "cue": "Although"},
    ]}}
    mpath = tmp_path / "cues.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    rc, env = _run(tmp_path, SAMPLE, "--judge", "manifest",
                   "--judge-manifest", str(mpath))
    assert rc == 0
    obs = env["results"]["observations"]
    assert len(obs) == 1 and obs[0]["cue"] == "Although"
    assert any("not found in the observation's span" in w
               for w in env["warnings"])


def test_discontinuous_cue_fragments_anchor():
    """Canonical discounting cues are discontinuous ('of course … yet'): each
    ellipsis-separated fragment must anchor in the span IN THE CUE'S ORDER,
    without reusing a span occurrence. An unanchored, reversed, or
    over-multiplied cue drops the observation."""
    para = ("Of course the fleet schedule is tight, yet the crossing-guard "
            "budget deserves its own vote.")
    obs = [{"family": "DISCOUNTING", "span": para, "paragraph_index": 0,
            "cue": "of course … yet"}]
    kept, drops = agd_move_scan_judge.normalize_observations(obs, [para])
    assert len(kept) == 1 and not drops
    for bad_cue in (
        "of course … but",        # unanchored fragment
        "yet … of course",        # reversed — not the surface cue the span reads
        "of course … of course",  # multiplicity — one occurrence claimed twice
    ):
        obs[0]["cue"] = bad_cue
        kept, drops = agd_move_scan_judge.normalize_observations(obs, [para])
        assert not kept and len(drops) == 1, bad_cue
    # a genuinely repeated span occurrence DOES anchor a repeated fragment
    para2 = ("Of course the schedule is tight, and of course the budget "
             "matters, yet the vote should proceed.")
    obs2 = [{"family": "DISCOUNTING", "span": para2, "paragraph_index": 0,
             "cue": "of course … of course … yet"}]
    kept, drops = agd_move_scan_judge.normalize_observations(obs2, [para2])
    assert len(kept) == 1 and not drops


# ----------------- agent_host host-delegated judge (spec 35 follow-on) ----------
import contextlib  # noqa: E402
import os  # noqa: E402


@contextlib.contextmanager
def _host_transport(fn):
    """Inject a STUB host transport (no live host, no key) — the spec-35 seam."""
    jb = agd_move_scan_judge.judge_backends
    prev = jb._HOST_JUDGE_OVERRIDE
    saved = {k: os.environ.pop(k, None) for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD")}
    jb._HOST_JUDGE_OVERRIDE = fn
    try:
        yield
    finally:
        jb._HOST_JUDGE_OVERRIDE = prev
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_agent_host_build_judge_resolves_without_model():
    assert "agent_host" in agd_move_scan_judge.judge_backends.PROVIDERS
    with _host_transport(lambda req: json.dumps({"observations": []})):
        j = agd_move_scan_judge.build_judge("agent_host", model=None)
        assert callable(j)
        res = j(SAMPLE.split("\n\n"))
    ident = res.judge_identity
    assert ident["kind"] == "agent_host"
    assert ident["model"] == "host-resolved"
    assert ident["host"] == "agent_host" and ident["delegated"] is True
    with pytest.raises(agd_move_scan_judge.JudgeError):
        agd_move_scan_judge.build_judge("anthropic", model=None)  # gate unchanged


def test_agent_host_in_audit_choices_default_unchanged():
    parser = agd_move_scan.build_arg_parser()
    judge_action = next(a for a in parser._actions if "--judge" in a.option_strings)
    assert "agent_host" in judge_action.choices
    assert judge_action.required is True and judge_action.default is None


def test_agent_host_end_to_end_via_stub(tmp_path):
    """The disjointness sentence (byte-identical to warrant_probe.py /
    argument_decision_audit.py / argquality_dimension_profile.py /
    fallacy_scan.py's agent_host caveats) must be present here too —
    agd_move_scan.py was the one judge-backed surface missing it."""
    with _host_transport(lambda req: json.dumps({"observations": []})):
        rc, env = _run(tmp_path, SAMPLE, "--judge", "agent_host")
    assert rc == 0 and env["available"] is True
    r = env["results"]
    assert r["judge"]["judge_identity"]["kind"] == "agent_host"
    cl = env["claim_license"]
    caveat = [c for c in cl["additional_caveats"]
              if "`agent_host`" in c and "HOST runtime" in c]
    assert caveat, "expected the agent_host caveat"
    assert "NON-DETERMINISTIC" in caveat[0]
    disjointness_sentence = (
        "The identity is recorded as agent_host:<host>:<model> so a consumer "
        "can assert it is disjoint from any generator it validates (the "
        "consumer's drift gate must enforce judge model != generator model "
        "on holdout/selection surfaces; see specs/35-host-delegated-judge.md)."
    )
    assert disjointness_sentence in caveat[0]
    assert cl["comparison_set"]["judge_host"] == "agent_host"


def test_mock_run_has_no_agent_host_caveat_and_null_host(tmp_path):
    _, env = _run(tmp_path, SAMPLE, "--judge", "mock")
    cl = env["claim_license"]
    assert not [c for c in cl["additional_caveats"] if "HOST runtime" in c]
    assert cl["comparison_set"].get("judge_host") is None
