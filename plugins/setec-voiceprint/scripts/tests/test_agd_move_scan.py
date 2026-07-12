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

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

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

# The scan must never emit adjudication vocabulary as data keys.
_FORBIDDEN_KEYS = {"code", "codes", "candidates", "verdict", "score", "quality",
                   "smuggling", "soundness", "severity", "diagnosis"}


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
    assert r["n_observations"] == len(r["observations"]) == 2
    for o in r["observations"]:
        assert set(o) == {"family", "span", "paragraph_index", "cue"}
        assert o["family"] in agd_move_scan_judge.FAMILIES
        assert isinstance(o["paragraph_index"], int)
    # the mock's second observation is the cue-free case — cue null is first-class
    assert r["observations"][1]["cue"] is None
    assert set(r["family_counts"]) == set(agd_move_scan_judge.FAMILIES)


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
