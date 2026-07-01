#!/usr/bin/env python3
"""Tests for the multi-surface run-set runner (setec_run_set.py).

Pins the spec's test contract (specs 02 §9):

  1.  preset integrity against the LIVE capabilities.d/ manifest;
  2.  exact argv projection per member (incl. the voice_distance
      skip-with-bad_input path — never an argparse crash);
  3.  R3 pass-through: a missing required dep synthesizes a member
      envelope and the run CONTINUES (exit 0);
  4.  attach unwrap + mechanical validation (envelope, legacy raw,
      and the three malformed-attach cases);
  5.  resolver wiring: the envelope["results"] unwrap produces real
      readings (proving the all-unknown failure mode is fixed) + the
      available-but-unknown shape-drift tripwire lands INSIDE the
      combined envelope;
  5a. claim-license state routing (--ai-status populated BEFORE the
      _claim_license(report) call);
  6.  combined envelope shape + tool == "setec_run_set" routing;
  7.  the anti-Goodhart guard (banned-key walk; pass-through exemption;
      tripped guard → internal_error, exit 1, no report.json);
  8.  the no-reduction RUNTIME invariant (float leaf / non-n_* int leaf);
  9.  pass-through identity (results.envelopes JSON-equal to the
      run-folder files; sha256 pinned in run_meta.json);
  10. resume semantics (nothing re-executed; non-empty out-dir refused
      without --resume; error records retried on resume);
  11. exit codes;
  12. --list-sets / --situation execute nothing.

Fixture strategy mirrors test_setec_run.py: an injectable manifest +
fake member scripts that write canned schema-1.0 envelopes (and count
their invocations for the resume tests).
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # scripts/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import capabilities  # type: ignore  # noqa: E402
import setec_run_set  # type: ignore  # noqa: E402

REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


# ---------- fixture helpers ----------------------------------------------

def _member_envelope(tool, task_surface, results, available=True):
    """A hand-built minimal schema-1.0 member envelope (the runner gates
    on schema_version == '1.0', not on the full 12-key contract)."""
    return {
        "schema_version": "1.0",
        "task_surface": task_surface,
        "tool": tool,
        "version": "9.9",
        "available": available,
        "target": {"path": "x.md", "words": 100},
        "baseline": None,
        "results": results,
        "claim_license": None,
        "claim_license_rendered": None,
        "warnings": [],
        "ai_status": None,
    }


def _variance_envelope(band="Heavily smoothed"):
    return _member_envelope(
        "variance_audit", "smoothing_diagnosis",
        {"compression": {"band": band}},
    )


def _voice_envelope(band="Close to baseline (weighted delta 0.5)"):
    return _member_envelope(
        "voice_distance", "voice_coherence",
        {"overall": {"band": band}},
    )


def _fake_script(scripts_dir: Path, sid: str, envelope: dict) -> Path:
    """Write a fake member script that logs its argv to <sid>.calls and
    prints the canned envelope."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    path = scripts_dir / f"{sid}.py"
    payload = json.dumps(json.dumps(envelope, indent=2))
    path.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "here = Path(__file__)\n"
        "with here.with_suffix('.calls').open('a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"print({payload})\n",
        encoding="utf-8",
    )
    return path


def _calls(scripts_dir: Path, sid: str) -> list[list[str]]:
    calls_file = scripts_dir / f"{sid}.calls"
    if not calls_file.exists():
        return []
    return [
        json.loads(line)
        for line in calls_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _entry(sid, script_path, deps=None, **extra):
    e = {
        "id": sid,
        "script_path": str(script_path),
        "surface": "validation",
        "status": "heuristic",
        "purpose": f"{sid} purpose",
        "use_when": [f"use {sid} for smoothing essays"],
        "compute": {"tier": "core"},
        "dependencies": {"python": list(deps or [])},
    }
    e.update(extra)
    return e


def _manifest(*entries):
    return {"schema_version": "0.3.0", "entries": list(entries)}


@pytest.fixture()
def target(tmp_path):
    t = tmp_path / "draft.md"
    t.write_text("Some draft text with a few idiolect phrases.\n" * 5,
                 encoding="utf-8")
    return t


@pytest.fixture()
def baseline_dir(tmp_path):
    d = tmp_path / "baseline"
    d.mkdir()
    (d / "b1.txt").write_text("baseline text\n", encoding="utf-8")
    return d


def _run_main(monkeypatch, manifest, argv):
    """Run main() with an injected manifest, capturing stdout."""
    monkeypatch.setattr(setec_run_set, "_load_live_manifest", lambda: manifest)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = setec_run_set.main(argv)
    return rc, buf.getvalue()


def _run_json(monkeypatch, manifest, argv):
    rc, out = _run_main(monkeypatch, manifest, [*argv, "--json"])
    return rc, json.loads(out)


# ---------- 1. preset integrity (LIVE manifest) ---------------------------

class TestPresetIntegrity:
    @pytest.fixture(scope="class")
    def live_ids(self):
        pytest.importorskip("yaml")
        m = capabilities.load_manifest()
        return {e.get("id") for e in capabilities.entries(m)}

    def test_every_preset_id_resolves_in_live_manifest(self, live_ids):
        for name, members in setec_run_set.RUN_SETS.items():
            for sid in members:
                assert sid in live_ids, (
                    f"preset {name!r} names {sid!r}, which no longer "
                    f"resolves in capabilities.d/"
                )

    def test_attach_only_subset_of_full_picture(self):
        assert setec_run_set.ATTACH_ONLY <= set(
            setec_run_set.RUN_SETS["full_picture"]
        )

    def test_preset_members_are_in_the_closed_universe(self):
        for members in setec_run_set.RUN_SETS.values():
            assert set(members) <= set(setec_run_set.KWARG_MAP)

    def test_attach_required_keys_cover_the_universe(self):
        assert set(setec_run_set.ATTACH_REQUIRED_KEYS) == set(
            setec_run_set.KWARG_MAP
        )


# ---------- 2. argv projection --------------------------------------------

class TestArgvProjection:
    def _capture_cmds(self, monkeypatch, manifest, argv):
        cmds = []

        def fake_run(cmd):
            cmds.append(cmd)
            import subprocess
            sid = Path(cmd[1]).stem
            env = (
                _variance_envelope() if sid == "variance_audit"
                else _voice_envelope() if sid == "voice_distance"
                else _member_envelope(sid, "validation", {"compression": {}})
            )
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(env), stderr="",
            )

        monkeypatch.setattr(setec_run_set, "_run_subprocess", fake_run)
        rc, out = _run_main(monkeypatch, manifest, argv)
        return rc, cmds

    def test_exact_argv_per_member_with_baseline(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        members = [
            "variance_audit", "paragraph_audit", "aic_pattern_audit",
            "discourse_move_signature", "agency_abstraction_audit",
            "voice_distance",
        ]
        entries = [
            _entry(sid, scripts / f"{sid}.py") for sid in members
        ]
        for sid in members:
            (scripts / f"{sid}.py").write_text("", encoding="utf-8")
        rc, cmds = self._capture_cmds(
            monkeypatch, _manifest(*entries),
            ["--surfaces", ",".join(members), "--target", str(target),
             "--baseline-dir", str(baseline_dir),
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        by_script = {Path(c[1]).stem: c for c in cmds}
        for sid in members[:-1]:
            assert by_script[sid][0] == sys.executable
            assert by_script[sid][2:] == [
                str(target), "--json", "--baseline-dir", str(baseline_dir),
            ], sid
        assert by_script["voice_distance"][2:] == [
            str(target), "--baseline-dir", str(baseline_dir), "--json",
        ]

    def test_exact_argv_without_baseline(
        self, monkeypatch, tmp_path, target,
    ):
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "variance_audit.py").write_text("", encoding="utf-8")
        rc, cmds = self._capture_cmds(
            monkeypatch, _manifest(_entry("variance_audit",
                                          scripts / "variance_audit.py")),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        assert cmds[0][2:] == [str(target), "--json"]

    def test_voice_distance_without_baseline_is_bad_input_not_a_crash(
        self, monkeypatch, tmp_path, target,
    ):
        """voice_distance REQUIRES a comparator; without --baseline-dir it
        is skipped with a synthesized bad_input record — never exec'd."""
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        voice = _fake_script(scripts, "voice_distance", _voice_envelope())
        manifest = _manifest(
            _entry("variance_audit", variance),
            _entry("voice_distance", voice),
        )
        rc, env = _run_json(
            monkeypatch, manifest,
            ["--surfaces", "variance_audit,voice_distance",
             "--target", str(target), "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        assert _calls(scripts, "voice_distance") == []  # never executed
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        vd = records["voice_distance"]
        assert vd["available"] is False
        assert vd["reason_category"] == "bad_input"
        assert "--baseline-dir" in vd["reason"]
        unavailable = {
            u["surface_id"]: u
            for u in env["results"]["next_action"]["unavailable_members"]
        }
        assert "--baseline-dir" in unavailable["voice_distance"]["unlock"]


# ---------- 3. R3 pass-through (missing dependency) ------------------------

def test_missing_dep_synthesizes_member_envelope_and_continues(
    monkeypatch, tmp_path, target,
):
    scripts = tmp_path / "scripts"
    variance = _fake_script(scripts, "variance_audit", _variance_envelope())
    aic = _fake_script(scripts, "aic_pattern_audit",
                       _member_envelope("aic_pattern_audit",
                                        "craft_restoration",
                                        {"patterns": {}}))
    manifest = _manifest(
        _entry("variance_audit", variance),
        _entry("aic_pattern_audit", aic,
               deps=["totally_absent_module_zz9"]),
    )
    rc, env = _run_json(
        monkeypatch, manifest,
        ["--surfaces", "variance_audit,aic_pattern_audit",
         "--target", str(target), "--out-dir", str(tmp_path / "run")],
    )
    assert rc == 0  # the run CONTINUES; partial success is normal
    records = {
        r["surface_id"]: r
        for r in env["results"]["run_set"]["member_records"]
    }
    aic_rec = records["aic_pattern_audit"]
    assert aic_rec["available"] is False
    assert aic_rec["reason_category"] == "missing_dependency"
    # The synthesized member envelope is on disk AND in the pass-through.
    member = env["results"]["envelopes"]["aic_pattern_audit"]
    assert member["available"] is False
    assert member["reason_category"] == "missing_dependency"
    assert member["missing_dependency"] == {
        "python": ["totally_absent_module_zz9"],
    }
    # next_action carries the dep-derived unlock hint.
    unavailable = {
        u["surface_id"]: u
        for u in env["results"]["next_action"]["unavailable_members"]
    }
    assert unavailable["aic_pattern_audit"]["unlock"] == (
        "pip install totally_absent_module_zz9"
    )
    # The fake script was never executed (the dep gate fired first).
    assert _calls(scripts, "aic_pattern_audit") == []


# ---------- 4. attach unwrap + mechanical validation -----------------------

class TestAttach:
    def _manifest(self, tmp_path):
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        gi_stub = scripts / "general_imposters.py"
        gi_stub.write_text("", encoding="utf-8")
        return _manifest(
            _entry("variance_audit", variance),
            # Mirrors the LIVE manifest: general_imposters carries
            # json_delivery: file but is attach-only, so the guard's
            # execution scope never fires for it.
            _entry("general_imposters", gi_stub, deps=["scipy"],
                   json_delivery="file"),
        )

    def test_attached_full_envelope_is_unwrapped_for_resolver(
        self, monkeypatch, tmp_path, target,
    ):
        gi_env = _member_envelope(
            "general_imposters", "voice_coherence",
            {"decision": "consistent_with_candidate"},
        )
        attach = tmp_path / "gi.json"
        attach.write_text(json.dumps(gi_env), encoding="utf-8")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        # The unwrap produced a real reading (not all-unknown).
        assert env["results"]["disagreement"]["readings"]["gi_decision"] == (
            "consistent"
        )
        # Verbatim in envelopes/ and results.envelopes.
        assert env["results"]["envelopes"]["general_imposters"] == gi_env
        on_disk = json.loads(
            (tmp_path / "run" / "envelopes" / "general_imposters.json")
            .read_text(encoding="utf-8"),
        )
        assert on_disk == gi_env
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        assert records["general_imposters"]["disposition"] == "attached"

    def test_attached_legacy_raw_report_passes_as_is(
        self, monkeypatch, tmp_path, target,
    ):
        raw = {"decision": "gray_zone_refused", "n_impostors": 8}
        attach = tmp_path / "gi.json"
        attach.write_text(json.dumps(raw), encoding="utf-8")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        assert env["results"]["disagreement"]["readings"]["gi_decision"] == (
            "gray_zone"
        )
        assert env["results"]["envelopes"]["general_imposters"] == raw

    @pytest.mark.parametrize("content, desc", [
        ("this is not json {", "non-JSON file"),
        (json.dumps({"neither": "envelope", "nor": "raw"}),
         "JSON object with neither shape"),
        (json.dumps([1, 2, 3]), "JSON non-object"),
    ])
    def test_malformed_attach_is_bad_input_member_record(
        self, monkeypatch, tmp_path, target, content, desc,
    ):
        attach = tmp_path / "gi.json"
        attach.write_text(content, encoding="utf-8")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        # Other members succeeded, so the run still exits 0.
        assert rc == 0, desc
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        gi = records["general_imposters"]
        assert gi["available"] is False
        assert gi["reason_category"] == "bad_input"
        # Excluded from resolve().
        assert env["results"]["disagreement"]["readings"]["gi_decision"] == (
            "unknown"
        )
        assert env["results"]["disagreement"]["inputs_used"]["gi"] is False
        # Surfaces in next_action with the regeneration command.
        unavailable = {
            u["surface_id"]: u
            for u in env["results"]["next_action"]["unavailable_members"]
        }
        assert "general_imposters" in unavailable
        assert "general_imposters" in unavailable["general_imposters"]["unlock"]

    def test_missing_keys_named_in_reason(
        self, monkeypatch, tmp_path, target,
    ):
        attach = tmp_path / "gi.json"
        attach.write_text(json.dumps({"something": "else"}),
                          encoding="utf-8")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        assert "decision" in records["general_imposters"]["reason"]

    def test_attached_refusal_envelope_is_honored_as_failed_member(
        self, monkeypatch, tmp_path, target,
    ):
        """An attached available:false R3 envelope is excluded from
        resolve() (feeding {} would read all-unknown) with its own
        reason_category carried forward."""
        refusal = _member_envelope(
            "general_imposters", "voice_coherence", {}, available=False,
        )
        refusal["reason"] = "Need at least 5 distinct impostor personas."
        refusal["reason_category"] = "bad_input"
        attach = tmp_path / "gi.json"
        attach.write_text(json.dumps(refusal), encoding="utf-8")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        gi = records["general_imposters"]
        assert gi["available"] is False
        assert gi["reason_category"] == "bad_input"
        assert "impostor" in gi["reason"]
        assert env["results"]["disagreement"]["inputs_used"]["gi"] is False

    def test_attached_refusal_with_unrecognized_category_is_sanitized(
        self, monkeypatch, tmp_path, target,
    ):
        """An attached refusal naming a non-R3 category is sanitized to
        bad_input, so the all-members-failed modal-exit path only ever
        sees the enum (no crash in build_error_output)."""
        refusal = _member_envelope(
            "general_imposters", "voice_coherence", {}, available=False,
        )
        refusal["reason_category"] = "banana"
        attach = tmp_path / "gi.json"
        attach.write_text(json.dumps(refusal), encoding="utf-8")
        scripts = tmp_path / "scripts"
        gi_stub = scripts / "general_imposters.py"
        scripts.mkdir(exist_ok=True)
        gi_stub.write_text("", encoding="utf-8")
        manifest = _manifest(
            _entry("general_imposters", gi_stub, json_delivery="file"),
        )
        # The attached refusal is the ONLY member → all-members-failed
        # path must exit with the sanitized modal category.
        rc, env = _run_json(
            monkeypatch, manifest,
            ["--surfaces", "general_imposters", "--target", str(target),
             "--attach", f"general_imposters={attach}",
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 3
        assert env["reason_category"] == "bad_input"

    def test_attach_only_member_without_attach_prompts(
        self, monkeypatch, tmp_path, target,
    ):
        """A full_picture-style run lacking an attach for an attach-only
        member gets the exact standalone command in next_action."""
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            ["--surfaces", "variance_audit,general_imposters",
             "--target", str(target), "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        gi = records["general_imposters"]
        assert gi["reason_category"] == "bad_input"
        assert "--attach general_imposters=" in gi["reason"]
        unavailable = {
            u["surface_id"]: u
            for u in env["results"]["next_action"]["unavailable_members"]
        }
        assert "setec_run.py general_imposters" in (
            unavailable["general_imposters"]["unlock"]
        )


# ---------- 5 / 5a. resolver wiring + tripwire + state routing -------------

class TestResolverWiring:
    def _manifest(self, tmp_path, variance_env=None):
        scripts = tmp_path / "scripts"
        variance = _fake_script(
            scripts, "variance_audit", variance_env or _variance_envelope(),
        )
        voice = _fake_script(scripts, "voice_distance", _voice_envelope())
        return _manifest(
            _entry("variance_audit", variance),
            _entry("voice_distance", voice),
        )

    def _argv(self, tmp_path, target, baseline_dir):
        return [
            "--surfaces", "variance_audit,voice_distance",
            "--target", str(target), "--baseline-dir", str(baseline_dir),
            "--out-dir", str(tmp_path / "run"),
        ]

    def test_unwrap_produces_real_readings_and_interpretation(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            self._argv(tmp_path, target, baseline_dir),
        )
        assert rc == 0
        readings = env["results"]["disagreement"]["readings"]
        # Proof the envelope→results unwrap fixed the all-unknown mode.
        assert readings["smoothing"] == "high"
        assert readings["voice_drift"] == "low"
        matched = {
            m["name"]
            for m in env["results"]["disagreement"]["matched_interpretations"]
        }
        assert "edited_authorial_voice" in matched

    def test_available_but_unknown_reading_trips_shape_drift_tripwire(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        """An available envelope whose primary reading comes back unknown
        must land in next_action.unavailable_members INSIDE the combined
        envelope (the record of truth), not only on stderr."""
        bogus = _variance_envelope(band="Some unrecognized band")
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path, variance_env=bogus),
            self._argv(tmp_path, target, baseline_dir),
        )
        assert rc == 0
        assert env["results"]["disagreement"]["readings"]["smoothing"] == (
            "unknown"
        )
        drift = [
            u for u in env["results"]["next_action"]["unavailable_members"]
            if u["reason_category"] == "shape_drift"
        ]
        assert [d["surface_id"] for d in drift] == ["variance_audit"]
        assert "smoothing" in drift[0]["reason"]

    def test_ai_status_routes_state_caveats_into_claim_license(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        """5a: report["ai_status"] must be populated BEFORE the
        _claim_license(report) call — the B.3 with_state_caveats routing
        is a no-op otherwise."""
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            [*self._argv(tmp_path, target, baseline_dir),
             "--ai-status", "ai_edited"],
        )
        assert rc == 0
        assert env["ai_status"] == "ai_edited"
        caveats = " ".join(env["claim_license"]["additional_caveats"])
        assert "low-touch editing" in caveats  # the ai_edited template

    def test_without_ai_status_no_state_caveats(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        rc, env = _run_json(
            monkeypatch, self._manifest(tmp_path),
            self._argv(tmp_path, target, baseline_dir),
        )
        assert rc == 0
        assert env["ai_status"] is None
        caveats = " ".join(env["claim_license"]["additional_caveats"])
        assert "low-touch editing" not in caveats


# ---------- 6. combined envelope shape + routing ---------------------------

def test_combined_envelope_shape_and_tool_routing(
    monkeypatch, tmp_path, target,
):
    scripts = tmp_path / "scripts"
    variance = _fake_script(scripts, "variance_audit", _variance_envelope())
    rc, env = _run_json(
        monkeypatch, _manifest(_entry("variance_audit", variance)),
        ["--surfaces", "variance_audit", "--target", str(target),
         "--out-dir", str(tmp_path / "run")],
    )
    assert rc == 0
    assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS
    assert env["schema_version"] == "1.0"
    # Consumers key on `tool`: the shared `validation` task_surface does
    # NOT distinguish the runner from the resolver.
    assert env["tool"] == "setec_run_set"
    assert env["task_surface"] == "validation"
    assert env["results"]["disagreement"]["tool"] == (
        "surface_disagreement_resolver"
    )
    # The combined claim license IS the resolver's refuses-verdict license.
    assert "declines to pick one" in env["claim_license"]["does_not_license"]


# ---------- 7 / 8. the anti-Goodhart guard ---------------------------------

class TestAggregateVerdictGuard:
    def test_banned_key_at_depth_raises(self):
        for bad in ("verdict", "score", "composite_ranking", "is_ai"):
            with pytest.raises(setec_run_set.AggregateVerdictError):
                setec_run_set.assert_no_aggregate_verdict(
                    {"run_set": {"nested": [{"deeper": {bad: "x"}}]}},
                )

    def test_case_folded_key_match(self):
        with pytest.raises(setec_run_set.AggregateVerdictError):
            setec_run_set.assert_no_aggregate_verdict({"VERDICT": "x"})

    def test_float_leaf_trips_no_reduction(self):
        with pytest.raises(
            setec_run_set.AggregateVerdictError, match="no-reduction",
        ):
            setec_run_set.assert_no_aggregate_verdict(
                {"summary": {"coverage": 0.87}},
            )

    def test_non_count_int_leaf_trips(self):
        with pytest.raises(
            setec_run_set.AggregateVerdictError, match="n_\\*",
        ):
            setec_run_set.assert_no_aggregate_verdict({"members": 7})

    def test_n_star_counts_and_bools_and_strings_pass(self):
        setec_run_set.assert_no_aggregate_verdict({
            "n_matches": 3,
            "n_known_readings": 2,
            "available": True,
            "band_like_text": "high",
            "nested": [{"n_items": 1}],
        })

    def test_guard_does_not_walk_pass_through_envelopes(self):
        """A member envelope legitimately carries floats (weighted_delta)
        and its own surface-guarded keys; the emit-time wrapper exempts
        results.envelopes from the walk."""
        results = {
            "run_set": {"name": "x", "member_records": []},
            "envelopes": {
                "voice_distance": {
                    "results": {"overall": {"weighted_delta": 1.7}},
                },
            },
            "disagreement": {"n_matches": 0},
            "next_action": {"rerun": "cmd"},
        }
        setec_run_set._guard_results(results)  # no raise

    def test_happy_path_combined_results_pass(
        self, monkeypatch, tmp_path, target,
    ):
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        rc, env = _run_json(
            monkeypatch, _manifest(_entry("variance_audit", variance)),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 0  # guard ran at emit time and passed

    @pytest.mark.parametrize("payload", [
        {"verdict": "smoothed"},          # banned key
        {"coverage": 0.87},               # float leaf (no-reduction)
        {"members": 7},                   # non-n_* int leaf
    ])
    def test_tripped_guard_is_internal_error_exit_1_no_report(
        self, monkeypatch, tmp_path, target, payload,
    ):
        """RUNTIME enforcement: inject a verdict-shaped / reductive leaf
        into a runner-authored subtree → internal_error envelope, exit 1,
        report.json NOT written. Fail-closed."""
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        real_build = setec_run_set._build_next_action

        def poisoned(**kwargs):
            block = real_build(**kwargs)
            block.update(payload)
            return block

        monkeypatch.setattr(setec_run_set, "_build_next_action", poisoned)
        out_dir = tmp_path / "run"
        rc, env = _run_json(
            monkeypatch, _manifest(_entry("variance_audit", variance)),
            ["--surfaces", "variance_audit", "--target", str(target),
             "--out-dir", str(out_dir)],
        )
        assert rc == 1
        assert env["available"] is False
        assert env["reason_category"] == "internal_error"
        assert not (out_dir / "report.json").exists()
        # The member checkpoints survive (belt) — only the report is
        # withheld.
        assert (out_dir / "envelopes" / "variance_audit.json").exists()


# ---------- 9. pass-through identity ---------------------------------------

def test_pass_through_identity_and_sha256(monkeypatch, tmp_path, target):
    scripts = tmp_path / "scripts"
    variance = _fake_script(scripts, "variance_audit", _variance_envelope())
    out_dir = tmp_path / "run"
    rc, env = _run_json(
        monkeypatch, _manifest(_entry("variance_audit", variance)),
        ["--surfaces", "variance_audit", "--target", str(target),
         "--out-dir", str(out_dir)],
    )
    assert rc == 0
    path = out_dir / "envelopes" / "variance_audit.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert env["results"]["envelopes"]["variance_audit"] == on_disk
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    rec = {
        r["surface_id"]: r for r in meta["member_records"]
    }["variance_audit"]
    assert rec["envelope_sha256"] == hashlib.sha256(
        path.read_bytes(),
    ).hexdigest()
    # The envelope-side record carries the same pin.
    env_rec = {
        r["surface_id"]: r
        for r in env["results"]["run_set"]["member_records"]
    }["variance_audit"]
    assert env_rec["envelope_sha256"] == rec["envelope_sha256"]


# ---------- 10. resume ------------------------------------------------------

class TestResume:
    def _setup(self, tmp_path):
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        voice = _fake_script(scripts, "voice_distance", _voice_envelope())
        manifest = _manifest(
            _entry("variance_audit", variance),
            _entry("voice_distance", voice),
        )
        return scripts, manifest

    def test_resume_re_executes_nothing(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        scripts, manifest = self._setup(tmp_path)
        argv = [
            "--surfaces", "variance_audit,voice_distance",
            "--target", str(target), "--baseline-dir", str(baseline_dir),
            "--out-dir", str(tmp_path / "run"),
        ]
        rc, _ = _run_main(monkeypatch, manifest, argv)
        assert rc == 0
        assert len(_calls(scripts, "variance_audit")) == 1
        rc, env = _run_json(monkeypatch, manifest, [*argv, "--resume"])
        assert rc == 0
        # Fake scripts count invocations: nothing re-executed.
        assert len(_calls(scripts, "variance_audit")) == 1
        assert len(_calls(scripts, "voice_distance")) == 1
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        assert records["variance_audit"]["disposition"] == "skipped"
        assert records["variance_audit"]["available"] is True

    def test_non_empty_out_dir_without_resume_refused(
        self, monkeypatch, tmp_path, target,
    ):
        scripts, manifest = self._setup(tmp_path)
        argv = [
            "--surfaces", "variance_audit", "--target", str(target),
            "--out-dir", str(tmp_path / "run"),
        ]
        rc, _ = _run_main(monkeypatch, manifest, argv)
        assert rc == 0
        rc, env = _run_json(monkeypatch, manifest, argv)
        assert rc == 3
        assert env["reason_category"] == "bad_input"
        assert "--resume" in env["reason"]

    def test_resume_retries_error_records(
        self, monkeypatch, tmp_path, target, baseline_dir,
    ):
        """A member checkpointed as an available:false error record is
        retried on --resume, so a rerun with the previously-missing flag
        actually runs it (the rerun template relies on this)."""
        scripts, manifest = self._setup(tmp_path)
        base_argv = [
            "--surfaces", "variance_audit,voice_distance",
            "--target", str(target), "--out-dir", str(tmp_path / "run"),
        ]
        rc, _ = _run_main(monkeypatch, manifest, base_argv)  # no baseline
        assert rc == 0
        assert _calls(scripts, "voice_distance") == []
        rc, env = _run_json(
            monkeypatch, manifest,
            [*base_argv, "--resume", "--baseline-dir", str(baseline_dir)],
        )
        assert rc == 0
        assert len(_calls(scripts, "voice_distance")) == 1  # now executed
        assert len(_calls(scripts, "variance_audit")) == 1  # still reused
        records = {
            r["surface_id"]: r
            for r in env["results"]["run_set"]["member_records"]
        }
        assert records["voice_distance"]["available"] is True


# ---------- 11. exit codes --------------------------------------------------

class TestExitCodes:
    def test_unknown_set_is_discovery_exit_2(self, monkeypatch, tmp_path):
        rc, out = _run_main(
            monkeypatch, _manifest(), ["--set", "no_such_set",
                                       "--target", "x.md"],
        )
        env = json.loads(out)
        assert rc == 2
        assert env["reason_category"] == "bad_input"
        assert env["available"] is False

    def test_no_target_is_contract_exit_3(self, monkeypatch):
        rc, out = _run_main(
            monkeypatch, _manifest(), ["--set", "smoothing_core"],
        )
        env = json.loads(out)
        assert rc == 3
        assert env["reason_category"] == "bad_input"

    def test_no_set_and_no_surfaces_is_contract_exit_3(self, monkeypatch):
        rc, out = _run_main(monkeypatch, _manifest(), ["--target", "x.md"])
        assert rc == 3

    def test_unknown_surface_in_surfaces_is_discovery_exit_2(
        self, monkeypatch,
    ):
        rc, out = _run_main(
            monkeypatch, _manifest(),
            ["--surfaces", "variance_audit,no_such_surface",
             "--target", "x.md"],
        )
        env = json.loads(out)
        assert rc == 2
        assert "no_such_surface" in env["reason"]

    def test_unknown_attach_id_is_discovery_exit_2(
        self, monkeypatch, tmp_path,
    ):
        f = tmp_path / "x.json"
        f.write_text("{}", encoding="utf-8")
        rc, out = _run_main(
            monkeypatch, _manifest(),
            ["--surfaces", "variance_audit", "--target", "x.md",
             "--attach", f"pov_voice_profile={f}"],
        )
        assert rc == 2

    def test_unreadable_attach_file_is_contract_exit_3(self, monkeypatch):
        rc, out = _run_main(
            monkeypatch, _manifest(),
            ["--surfaces", "variance_audit", "--target", "x.md",
             "--attach", "general_imposters=/no/such/file.json"],
        )
        assert rc == 3

    def test_malformed_attach_spec_is_contract_exit_3(self, monkeypatch):
        rc, out = _run_main(
            monkeypatch, _manifest(),
            ["--surfaces", "variance_audit", "--target", "x.md",
             "--attach", "not-a-pair"],
        )
        assert rc == 3

    def test_all_members_failed_exits_with_modal_category(
        self, monkeypatch, tmp_path, target,
    ):
        scripts = tmp_path / "scripts"
        stub = scripts / "aic_pattern_audit.py"
        scripts.mkdir()
        stub.write_text("", encoding="utf-8")
        manifest = _manifest(
            _entry("aic_pattern_audit", stub,
                   deps=["totally_absent_module_zz9"]),
        )
        rc, env = _run_json(
            monkeypatch, manifest,
            ["--surfaces", "aic_pattern_audit", "--target", str(target),
             "--out-dir", str(tmp_path / "run")],
        )
        assert rc == 3  # missing_dependency's mapped exit
        assert env["reason_category"] == "missing_dependency"
        # The member checkpoint survives for --resume.
        assert (tmp_path / "run" / "envelopes"
                / "aic_pattern_audit.json").exists()

    def test_exec_member_with_file_delivery_is_refused(
        self, monkeypatch, tmp_path, target,
    ):
        """Execution-scoped membership guard: the runner never injects
        --json-out; a member whose entry grew json_delivery: file cannot
        be EXECUTED (attach remains the only path in)."""
        scripts = tmp_path / "scripts"
        stub = scripts / "variance_audit.py"
        scripts.mkdir()
        stub.write_text("", encoding="utf-8")
        manifest = _manifest(
            _entry("variance_audit", stub, json_delivery="file"),
        )
        rc, out = _run_main(
            monkeypatch, manifest,
            ["--surfaces", "variance_audit", "--target", str(target),
             "--out-dir", str(tmp_path / "run")],
        )
        env = json.loads(out)
        assert rc == 3
        assert env["reason_category"] == "bad_input"
        assert "--attach" in env["reason"]


# ---------- 12. --list-sets / --situation -----------------------------------

class TestReportOnlyModes:
    def test_list_sets_enumerates_without_executing(
        self, monkeypatch, tmp_path,
    ):
        scripts = tmp_path / "scripts"
        _fake_script(scripts, "variance_audit", _variance_envelope())
        rc, out = _run_main(monkeypatch, _manifest(), ["--list-sets"])
        assert rc == 0
        for name in setec_run_set.RUN_SETS:
            assert name in out
        assert "attach-only" in out
        assert _calls(scripts, "variance_audit") == []

    def test_situation_prints_recommendation_and_executes_nothing(
        self, monkeypatch, tmp_path,
    ):
        scripts = tmp_path / "scripts"
        variance = _fake_script(scripts, "variance_audit",
                                _variance_envelope())
        manifest = _manifest(_entry("variance_audit", variance))
        rc, out = _run_main(
            monkeypatch, manifest,
            ["--situation", "check smoothing in these essays"],
        )
        assert rc == 0
        assert "Preset coverage" in out
        assert "router of record" in out
        assert _calls(scripts, "variance_audit") == []


# ---------- run-folder layout ------------------------------------------------

def test_run_folder_layout_and_md_report(monkeypatch, tmp_path, target):
    scripts = tmp_path / "scripts"
    variance = _fake_script(scripts, "variance_audit", _variance_envelope())
    out_dir = tmp_path / "run"
    rc, out = _run_main(
        monkeypatch, _manifest(_entry("variance_audit", variance)),
        ["--surfaces", "variance_audit", "--target", str(target),
         "--out-dir", str(out_dir)],
    )
    assert rc == 0
    # Default stdout is the rendered markdown; both artifacts are always
    # written to the run folder.
    assert out.startswith("# SETEC run-set report")
    assert "Multiple matches are expected" in out or "Matched" in out
    for name in ("run_meta.json", "report.json", "report.md"):
        assert (out_dir / name).exists(), name
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert report["tool"] == "setec_run_set"
    md = (out_dir / "report.md").read_text(encoding="utf-8")
    assert md == out
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert meta["setec_version"]
    assert meta["member_records"][0]["argv"] == [str(target), "--json"]
    assert meta["member_records"][0]["exit"] == 0
